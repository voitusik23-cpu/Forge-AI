// Проекты — the persistent project lifecycle, read from the routes that already exist:
//
//   GET  /api/projects/lifecycle             -> one summary line per project
//   GET  /api/projects/{id}                  -> record + versions + current artifacts
//   GET  /api/projects/{id}/files?version=   -> one version's artifact manifest
//   GET  /api/projects/{id}/history          -> versions, newest first
//   POST /api/projects/{id}/versions         -> stage the next version + its task text
//   POST /api/projects/{id}/verify           -> re-judge the recorded artifacts
//   POST /api/projects/{id}/archive|restore  -> freeze / unfreeze the record
//   GET  /api/runs/{run_id}                  -> the run's own classification (what failed, why)
//   POST /api/tasks/run                      -> the *existing* run path that executes staged text
//
// Three rules decide what this screen may draw:
//
// * **No absolute path.** The record publishes `project_path` repository-relative, and the
//   «Где находится проект» block repeats it exactly as the backend returned it.
// * **No invented success.** The outcome block is chosen by the project's own status word, so a
//   run that only finished is never shown as completed. `NEEDS_CHANGES` says what the verdicts
//   say and, when the run record carries one, the classification's own reason.
// * **A refusal is shown, not hidden.** An action that the lifecycle would refuse stays
//   clickable and the backend's 409 is printed with its code and message: an archived project
//   must be able to demonstrate that it is frozen.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Badge, Button, Card, CardHead, Field, StatusDot } from "../components/Primitives";
import { IconRefresh } from "../components/Icons";
import { t, type Lang } from "../i18n";
import type { BackendState } from "../api/client";
import {
  OFFLINE_PROVIDER,
  acceptanceLabel,
  archiveProject,
  catalogueModelsFor,
  changeProjectPreview,
  createProjectVersion,
  deleteEmptyProjects,
  deleteProject,
  dispatchTask,
  loadDeletedProjects,
  loadProjectDetail,
  loadProjectFiles,
  loadProjectHistory,
  loadProjectLifecycle,
  loadProjectRun,
  loadRunRecord,
  restartProjectRun,
  restoreDeletedProject,
  restoreProject,
  startProjectRun,
  stopProjectRun,
  verificationLabel,
  verifyProject,
  type DispatchResult,
  type Failure,
  type LifecycleArtifact,
  type LifecycleDeletedProject,
  type LifecycleDetail,
  type LifecycleEntryPoint,
  type LifecycleHistory,
  type LifecycleNewVersion,
  type LifecyclePreview,
  type LifecycleProject,
  type LifecycleRun,
  type LifecycleSummary,
  type LifecycleVersion,
  type ProjectPresentation,
  type RunRecord,
} from "../api/forge";

const text = (value: unknown, fallback = "—"): string =>
  value === undefined || value === null || value === "" ? fallback : String(value);

const bytesLabel = (size: unknown): string => {
  const value = typeof size === "number" && Number.isFinite(size) ? size : null;
  if (value === null) return "—";
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KiB`;
  return `${(value / (1024 * 1024)).toFixed(2)} MiB`;
};

/**
 * The tone a raw status word is drawn with, for the *list*.
 *
 * The list shows the backend's own word next to its owner-facing stage; the card
 * itself draws only the owner's word (the raw one lives in «Технические детали»).
 * The word is always printed next to the colour, so a colour-blind reader loses
 * nothing.
 */
const statusTone = (status: unknown): "ok" | "warn" | "danger" | "info" | "neutral" => {
  const word = String(status ?? "").toUpperCase();
  if (word === "COMPLETED") return "ok";
  if (word === "NEEDS_CHANGES" || word === "FAILED") return "danger";
  if (word === "RUNNING" || word === "REVIEWING") return "info";
  if (word === "ARCHIVED") return "neutral";
  if (word === "WAITING_FOR_CONFIRMATION" || word === "NEEDS_CLARIFICATION") return "warn";
  return "neutral";
};

type Load<T> = { status: "loading" } | { status: "ready"; value: T } | { status: "failed"; failure: Failure };

/**
 * Bring the project card to the top of the shell's scrolling region.
 *
 * The shell has exactly one scrolling element (`.content`, see base.css) and the card
 * renders below the list, so without this a click on «Открыть» changed a row's highlight
 * and nothing else -- the measured "does nothing". The offset is computed explicitly
 * rather than with `scrollIntoView`, whose ancestor resolution inside a grid/flex child of
 * an `overflow: auto` container is exactly the kind of thing that silently scrolls a little
 * and leaves the card off screen.
 *
 * The last attempt is recorded on `window.__forgeScroll` so a CDP check can read what the
 * code actually did instead of inferring it from a picture.
 */
const revealProjectCard = (): void => {
  const card = document.getElementById("project-card");
  if (card === null) return;
  const content = card.closest(".content") as HTMLElement | null;
  if (content === null) return;
  const target =
    card.getBoundingClientRect().top -
    content.getBoundingClientRect().top +
    content.scrollTop -
    8;
  content.scrollTo({ top: Math.max(0, target), behavior: "smooth" });
  (window as unknown as Record<string, unknown>).__forgeScroll = {
    target: Math.round(target),
    scrollTopBefore: Math.round(content.scrollTop),
    cardHeight: Math.round(card.getBoundingClientRect().height),
  };
};


/** The four facts of a refused call, printed as they arrived. A 409 is never softened. */
const Refusal = ({ lang, failure, onRetry }: { lang: Lang; failure: Failure; onRetry?: () => void }) => {
  const signedOut = failure.kind === "unauthorized";
  const offline = failure.kind === "disconnected";
  const code = failure.code ?? "";
  const headline = signedOut
    ? t(lang, "projects.err.forbidden")
    : offline
      ? t(lang, "projects.err.offline")
      : code === "unknown_project"
        ? t(lang, "projects.err.unknown")
        : code === "invalid_change_request"
          ? t(lang, "projects.err.invalid_change")
          : code === "illegal_project_transition"
            ? t(lang, "projects.err.illegal_transition")
            : code === "project_not_completable"
              ? t(lang, "projects.err.not_completable")
              : `${t(lang, "projects.err.generic")}: HTTP ${failure.status}`;
  return (
    <div className={`notice ${offline || signedOut ? "danger" : "warn"}`}>
      <div>{headline}</div>
      <div className="small mono" style={{ marginTop: 6, wordBreak: "break-word" }}>
        HTTP {failure.status}
        {code === "" ? "" : ` · ${code}`} · {failure.path}
      </div>
      {/* The backend's own sentence: for an archived project it names the freeze, which is the
          honest answer to "why did adding a feature do nothing". */}
      {failure.detail ? (
        <div className="small mono" style={{ marginTop: 6, whiteSpace: "pre-wrap" }}>
          {failure.detail}
        </div>
      ) : null}
      {onRetry ? (
        <div className="row" style={{ marginTop: 10 }}>
          <Button size="small" onClick={onRetry}>
            {t(lang, "action.retry")}
          </Button>
        </div>
      ) : null}
    </div>
  );
};

/** The same fact grid the Кузница card uses, so both screens read a record the same way. */
const Facts = ({ items }: { items: Array<[string, string]> }) => (
  <div className="forge-facts">
    {items.map(([label, value]) => (
      <div className="forge-fact" key={label}>
        <div className="label">{label}</div>
        <div className="mono small" style={{ wordBreak: "break-word" }}>
          {value}
        </div>
      </div>
    ))}
  </div>
);

/** One artifact line: where it is, how big, what changed, and how it was verified. */
const ArtifactTable = ({ lang, artifacts }: { lang: Lang; artifacts: LifecycleArtifact[] }) => (
  <table className="table">
    <thead>
      <tr>
        <th>{t(lang, "projects.col.folder")}</th>
        <th>{t(lang, "projects.col.kind")}</th>
        <th>{t(lang, "projects.col.size")}</th>
        <th>{t(lang, "projects.col.change")}</th>
        <th>{t(lang, "projects.col.verify")}</th>
        <th>SHA-256</th>
      </tr>
    </thead>
    <tbody>
      {artifacts.map((artifact) => (
        <tr key={String(artifact.location ?? artifact.path ?? "")}>
          <td className="mono small" style={{ maxWidth: 420, overflowWrap: "anywhere" }}>
            {text(artifact.location ?? artifact.path)}
          </td>
          <td className="small">{text(artifact.kind)}</td>
          <td className="mono small">{bytesLabel(artifact.size)}</td>
          <td>
            <Badge tone={artifact.change === "create" ? "ok" : "info"}>{text(artifact.change)}</Badge>
          </td>
          <td className="small">{text(artifact.verification_relevance)}</td>
          <td className="mono small">{String(artifact.sha256 ?? "").slice(0, 12) || "—"}</td>
        </tr>
      ))}
    </tbody>
  </table>
);

/**
 * The local-process block: what may run, in which folder, on which port, and what
 * the supervisor last said.
 *
 * A launch is never one click away: the button opens an explicit confirmation that
 * prints the **exact command** and its digest, and only the confirmation calls the
 * backend. The digest is what the backend compares, so a command that changed cannot
 * be run under the previous approval.
 */
const RunPanel = ({
  lang,
  run,
  entrypoint,
  preview,
  busy,
  onStart,
  onStop,
  onRestart,
  onVerify,
  onShowFiles,
  canVerify,
}: {
  lang: Lang;
  run: LifecycleRun;
  entrypoint: LifecycleEntryPoint | null;
  preview: LifecyclePreview;
  busy: string;
  onStart: (digest: string) => void;
  onStop: () => void;
  onRestart: (digest: string) => void;
  onVerify: () => void;
  onShowFiles: () => void;
  canVerify: boolean;
}) => {
  const [confirming, setConfirming] = useState<"" | "start" | "restart">("");
  const [showLog, setShowLog] = useState(false);
  const state = String(run.state ?? "NOT_RUN");
  const live = state === "STARTING" || state === "RUNNING" || state === "STOPPING";
  const command = String(run.command ?? entrypoint?.run_command ?? "");
  const digest = String(run.entrypoint_digest ?? entrypoint?.digest ?? "");
  const runnable = entrypoint !== null && entrypoint.runnable === true;
  const blocked = state === "RUN_BLOCKED" || state === "REFUSED";
  const lines = Array.isArray(run.log?.lines) ? run.log?.lines ?? [] : [];
  const logPath = String(run.log?.relative_path ?? run.log?.path ?? "");
  const previewUrl = String(preview.url ?? "");
  const port = typeof run.port === "number" && run.port > 0 ? String(run.port) : "—";
  const exitCode =
    typeof run.exit_code === "number" ? String(run.exit_code) : t(lang, "projects.facts.none");
  const language = String(run.language ?? entrypoint?.language ?? "");
  const entryLabel =
    entrypoint !== null && String(entrypoint.path ?? "") !== ""
      ? `${String(entrypoint.path)} (${String(entrypoint.kind ?? "")})`
      : t(lang, "projects.facts.none");
  const eventLine =
    String(run.last_event ?? "") !== ""
      ? String(run.last_event)
      : String(run.reason ?? "") !== ""
        ? String(run.reason)
        : t(lang, "projects.facts.none");

  const canStart = runnable && digest !== "" && !live && state !== "STARTING";
  const canRestart = digest !== "" && !live && state !== "NOT_RUN";

  return (
    <div>
      <div className="label">{t(lang, "projects.run.title")}</div>
      <div style={{ marginTop: 8 }}>
        <Facts
          items={[
            [t(lang, "projects.run.language"), language !== "" ? language : t(lang, "projects.facts.none")],
            [t(lang, "projects.run.entry"), entryLabel],
            [t(lang, "projects.run.command"), command !== "" ? command : t(lang, "projects.run.nocommand")],
            [t(lang, "projects.run.workspace"), text(run.workspace, t(lang, "projects.facts.none"))],
            [t(lang, "projects.run.port"), port],
            [t(lang, "projects.run.url"), previewUrl !== "" ? previewUrl : t(lang, "projects.facts.none")],
            [t(lang, "projects.run.state"), state],
            [t(lang, "projects.run.event"), eventLine],
            [t(lang, "projects.run.network"), t(lang, "projects.run.network.inherited")],
            [t(lang, "projects.run.pid"), typeof run.pid === "number" ? String(run.pid) : "—"],
            [t(lang, "projects.run.exit"), exitCode],
            [t(lang, "projects.run.started"), text(run.started_at, t(lang, "projects.facts.none"))],
          ]}
        />
      </div>
      <div className="small muted" style={{ marginTop: 6 }}>
        {t(lang, "projects.run.hint")}
      </div>

      {/* A blocked run states the reason and the next step, in the backend's words. */}
      {blocked ? (
        <div className="notice warn" style={{ marginTop: 8 }}>
          <div>
            <span className="label">{t(lang, "projects.run.blocked.reason")}: </span>
            {text(run.reason, t(lang, "projects.run.nocommand"))}
          </div>
          {String(run.next_step ?? "") !== "" ? (
            <div style={{ marginTop: 4 }}>
              <span className="label">{t(lang, "projects.run.blocked.next")}: </span>
              {String(run.next_step)}
            </div>
          ) : null}
          {String(run.blocked_code ?? "") !== "" ? (
            <div className="small mono" style={{ marginTop: 4 }}>
              {String(run.blocked_code)}
            </div>
          ) : null}
        </div>
      ) : null}

      <div className="row" style={{ gap: 8, flexWrap: "wrap", marginTop: 10 }}>
        {canStart ? (
          <Button
            size="small"
            tone="primary"
            disabled={busy !== "" || confirming !== ""}
            onClick={() => setConfirming("start")}
          >
            {busy === "start" ? t(lang, "state.loading") : t(lang, "projects.run.start")}
          </Button>
        ) : null}
        {live ? (
          <Button size="small" tone="ghost" disabled={busy !== ""} onClick={onStop}>
            {busy === "stop" ? t(lang, "state.loading") : t(lang, "projects.run.stop")}
          </Button>
        ) : null}
        {canRestart ? (
          <Button
            size="small"
            tone="ghost"
            disabled={busy !== "" || confirming !== ""}
            onClick={() => setConfirming("restart")}
          >
            {busy === "restart" ? t(lang, "state.loading") : t(lang, "projects.run.restart")}
          </Button>
        ) : null}
        {preview.available === true && String(preview.url ?? "") !== "" ? (
          <Button
            size="small"
            tone="ghost"
            onClick={() => window.open(String(preview.url), "_blank", "noopener,noreferrer")}
          >
            {t(lang, "projects.run.open")}
          </Button>
        ) : null}
        <Button size="small" tone="ghost" onClick={onShowFiles}>
          {t(lang, "projects.run.files")}
        </Button>
        <Button size="small" tone="ghost" onClick={() => setShowLog((value) => !value)}>
          {showLog ? t(lang, "projects.run.hidelog") : t(lang, "projects.run.showlog")}
        </Button>
        {canVerify ? (
          <Button size="small" tone="ghost" onClick={onVerify}>
            {t(lang, "projects.run.verify")}
          </Button>
        ) : null}
      </div>

      {/* The explicit approval: the exact command, printed, and the digest the
          backend will compare. Nothing starts until the second button is pressed. */}
      {confirming !== "" ? (
        <div className="notice warn" style={{ marginTop: 10 }}>
          <div className="label">{t(lang, "projects.run.confirm.title")}</div>
          <div className="small" style={{ marginTop: 6 }}>
            {t(lang, "projects.run.confirm.body")}
          </div>
          <pre className="forge-output mono small" style={{ marginTop: 6 }}>
            {command}
          </pre>
          <div className="small mono" style={{ marginTop: 4, wordBreak: "break-word" }}>
            {t(lang, "projects.run.confirm.digest")}: {digest}
          </div>
          <div className="small muted" style={{ marginTop: 4 }}>
            {t(lang, "projects.run.confirm.note")}
          </div>
          <div className="row" style={{ gap: 8, flexWrap: "wrap", marginTop: 8 }}>
            <Button
              size="small"
              tone="primary"
              disabled={busy !== ""}
              onClick={() => {
                const action = confirming;
                setConfirming("");
                if (action === "restart") onRestart(digest);
                else onStart(digest);
              }}
            >
              {t(lang, "projects.run.confirm.accept")}
            </Button>
            <Button size="small" tone="ghost" onClick={() => setConfirming("")}>
              {t(lang, "projects.run.confirm.cancel")}
            </Button>
          </div>
        </div>
      ) : null}

      {showLog ? (
        <div style={{ marginTop: 10 }}>
          <div className="row between" style={{ gap: 10, alignItems: "center" }}>
            <div className="label">{t(lang, "projects.run.logs")}</div>
            {logPath !== "" ? <span className="small mono muted">{logPath}</span> : null}
          </div>
          {lines.length === 0 ? (
            <div className="small muted" style={{ marginTop: 6 }}>
              {t(lang, "projects.run.log.empty")}
            </div>
          ) : (
            <pre className="forge-output mono small" style={{ marginTop: 6, maxHeight: 260, overflow: "auto" }}>
              {lines.join("\n")}
            </pre>
          )}
          {run.log?.truncated === true ? (
            <div className="small muted" style={{ marginTop: 4 }}>
              {t(lang, "projects.run.log.truncated")}
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
};

const ProjectCard = ({
  lang,
  backend,
  presentation,
  project,
  versions,
  files,
  filesVersion,
  selectedVersion,
  onSelectVersion,
  runRecord,
  runFailure,
  runLoading,
  busy,
  onVerify,
  onArchive,
  onRestore,
  onHistory,
  change,
  onChange,
  changeError,
  onNewVersion,
  staged,
  onDispatch,
  dispatching,
  dispatched,
  dispatchFailure,
  provider,
  onProvider,
  model,
  onModel,
  modelChoices,
  writeMode,
  onWriteMode,
  mockSelected,
  actionFailure,
  actionNotice,
  entrypoint,
  preview,
  stage,
  verificationCommand,
  filesCreated,
  previewBusy,
  onPreviewStart,
  onPreviewStop,
  run,
  runBusy,
  onRunStart,
  onRunStop,
  onRunRestart,
  onDelete,
}: {
  lang: Lang;
  backend: BackendState;
  /** The owner-facing card, derived by the backend from this project's own facts. */
  presentation: ProjectPresentation;
  project: LifecycleProject;
  versions: LifecycleVersion[];
  files: LifecycleArtifact[];
  filesVersion: number;
  selectedVersion: number;
  onSelectVersion: (version: number) => void;
  runRecord: RunRecord | null;
  runFailure: Failure | null;
  runLoading: boolean;
  busy: string;
  onVerify: () => void;
  onArchive: () => void;
  onRestore: () => void;
  onHistory: () => void;
  change: string;
  onChange: (value: string) => void;
  changeError: string;
  onNewVersion: () => void;
  staged: LifecycleNewVersion | null;
  onDispatch: () => void;
  dispatching: boolean;
  dispatched: DispatchResult | null;
  dispatchFailure: Failure | null;
  provider: string;
  onProvider: (value: string) => void;
  model: string;
  onModel: (value: string) => void;
  modelChoices: string[];
  writeMode: boolean;
  onWriteMode: (value: boolean) => void;
  mockSelected: boolean;
  actionFailure: Failure | null;
  actionNotice: string;
  /** The one entry point the backend detected from the manifest, or null. */
  entrypoint: LifecycleEntryPoint | null;
  /** The preview's runtime state, as the backend reports it. */
  preview: LifecyclePreview;
  /** The eight-word stage the backend derived from this project's facts. */
  stage: string;
  verificationCommand: string;
  filesCreated: number;
  previewBusy: boolean;
  onPreviewStart: () => void;
  onPreviewStop: () => void;
  /** The project's local process, from live state only. */
  run: LifecycleRun;
  runBusy: string;
  onRunStart: (digest: string) => void;
  onRunStop: () => void;
  onRunRestart: (digest: string) => void;
  /**
   * Delete this project: the parent asks the backend, which *moves* the folder into
   * `.forge/projects-deleted`. The click is refused until it is confirmed inside the card,
   * so a single accidental press cannot move anything.
   */
  onDelete: () => void;
}) => {
  const archived = project.archived === true;
  const currentVersion = typeof project.current_version === "number" ? project.current_version : 0;
  const specification = project.specification ?? {};
  const specKeys: Array<[string, string]> = [
    ["goal", t(lang, "projects.spec.goal")],
    ["project_type", t(lang, "projects.spec.project_type")],
    ["users", t(lang, "projects.spec.users")],
    ["core_features", t(lang, "projects.spec.core_features")],
    ["platform", t(lang, "projects.spec.platform")],
    ["integrations", t(lang, "projects.spec.integrations")],
    ["data_storage", t(lang, "projects.spec.data_storage")],
    ["auth", t(lang, "projects.spec.auth")],
    ["notifications", t(lang, "projects.spec.notifications")],
    ["constraints", t(lang, "projects.spec.constraints")],
    ["acceptance_criteria", t(lang, "projects.spec.acceptance_criteria")],
    ["priorities", t(lang, "projects.spec.priorities")],
  ];
  const specValue = (value: unknown): string => {
    if (Array.isArray(value)) return value.length === 0 ? t(lang, "projects.spec.empty") : value.map((item) => String(item)).join(", ");
    if (typeof value === "string") return value.trim() === "" ? t(lang, "projects.spec.empty") : value;
    if (value === null || value === undefined) return t(lang, "projects.spec.empty");
    return String(value);
  };

  const current = versions.find((item) => item.version === currentVersion) ?? versions[0] ?? null;
  const outcome = String(current?.verification ?? "");
  const acceptance = String(current?.acceptance ?? "");

  // The run's own classification is the only place a reason exists; the project record
  // deliberately stores verdicts, not prose. It is shown inside «Технические детали»,
  // never above the fold.
  const classification = runRecord?.classification ?? null;

  // ------------------------------------------------------------------ the truth block
  // Every word below comes from `presentation`, which the backend derives from this
  // project's own facts (app/projects/presentation.py). Nothing here re-decides a
  // status, so the card cannot contradict the record it is describing.
  const state = String(presentation.project_state ?? "");
  const stageLabel = String(presentation.stage_label ?? "");
  const stateTone = (value: string): "ok" | "warn" | "danger" | "info" | "neutral" => {
    if (value === "ГОТОВО") return "ok";
    if (value === "НУЖНЫ ДОРАБОТКИ") return "warn";
    if (value === "НЕ СОЗДАН") return "warn";
    if (value === "АРХИВ") return "neutral";
    return "info";
  };
  const saveFacts = presentation.save_facts ?? [];
  const factRows: Array<[string, string]> = (presentation.facts ?? []).map((row) => [
    String(row?.[0] ?? ""),
    String(row?.[1] ?? ""),
  ]);
  // The launch buttons belong to the preview block, which knows whether the backend
  // detected an entry point and whether a safe command exists for it. Offering them in
  // the generic action row as well would promise a launch the card cannot perform.
  const actions = (presentation.actions ?? []).filter(
    (item) => item.visible !== false && item.id !== "launch_local" && item.id !== "open_app"
  );
  const hiddenActions = (presentation.actions ?? []).filter((item) => item.visible === false);
  const technicalFields = presentation.technical?.fields ?? [];
  // Whether a run was ever recorded for the current version. It is a technical fact,
  // so it is read from the technical block rather than re-derived here.
  const runIdValue = String((technicalFields.find((field) => field.key === "run_id") ?? {}).value ?? "");
  const hasRun = runIdValue !== "" && runIdValue !== "—";
  const notices = presentation.notices ?? [];

  // ------------------------------------------------------------ the derived facts
  // `stage` is one of the eight words the backend derives from the facts
  // (`app/projects/stage.py`); it is shown beside the owner's word, never instead of it.
  const stageWord = String(stage ?? "");
  const fileTotal =
    typeof filesCreated === "number" && Number.isFinite(filesCreated)
      ? filesCreated
      : Number(presentation.files_count ?? 0);
  const entryLabel =
    entrypoint !== null && typeof entrypoint.path === "string" && entrypoint.path !== ""
      ? `${entrypoint.path} (${text(entrypoint.kind, "—")})`
      : t(lang, "projects.facts.none");
  const previewAvailable = preview.available === true;
  const previewRunning = preview.running === true;
  const previewUrl = String(preview.url ?? "");
  // The two words the backend can return for "not available", and nothing else: an entry
  // point that needs a process, or no entry point at all.
  const honestLaunchNote =
    fileTotal === 0
      ? t(lang, "projects.empty.title")
      : entrypoint === null
        ? t(lang, "projects.preview.no_entry")
        : previewAvailable
          ? ""
          : t(lang, "projects.preview.not_wired");

  const [launchNote, setLaunchNote] = useState("");
  // Deleting is a two-step decision: the first click only opens the confirmation, so the
  // sentence that says what will happen is on screen before anything moves.
  const [confirmDelete, setConfirmDelete] = useState(false);
  const projectId = String(project.project_id ?? "");
  const canDelete = projectId !== "";
  const changeRef = useRef<HTMLTextAreaElement | null>(null);

  // One handler for the whole action vocabulary: the card offers what the backend
  // allows and nothing else, so an unknown id can only be a client/server mismatch.
  const runAction = (id: string): void => {
    setLaunchNote("");
    if (id === "reverify") {
      onVerify();
      return;
    }
    if (id === "new_version") {
      onNewVersion();
      return;
    }
    if (id === "view_files") {
      document.getElementById("project-artifacts")?.scrollIntoView({ behavior: "smooth", block: "start" });
      return;
    }
    if (id === "continue_creation" || id === "add_feature" || id === "answer_questions") {
      changeRef.current?.focus();
      return;
    }
    if (id === "open_app" || id === "launch_local") {
      // There is no route that serves a generated project, so the card says where the
      // file is instead of pretending to start it.
      const target = String(presentation.launch_path ?? "");
      const folder = String(project.project_path ?? "");
      setLaunchNote(
        `${t(lang, "projects.launch.notice")}: ${folder}/${target} (${t(lang, "projects.launch.manual")})`
      );
    }
  };

  return (
    <Card className="rise d1">
      <CardHead
        title={text(project.name, t(lang, "projects.card.untitled"))}
        hint={`${text(project.project_id)} · ${t(lang, "projects.card.hint")}`}
        action={
          <span className="row" style={{ gap: 6, flexWrap: "wrap" }}>
            {/* The owner's word for the stage, never the backend's raw enum. The raw
                word is in «Технические детали», collapsed. */}
            <Badge tone={stateTone(state)}>{text(stageLabel, t(lang, "runs.unknown"))}</Badge>
            {archived ? <Badge tone="neutral">{t(lang, "projects.badge.archived")}</Badge> : null}
          </span>
        }
      />

      <div className="stack" style={{ gap: 16 }}>
        {actionFailure !== null ? <Refusal lang={lang} failure={actionFailure} /> : null}
        {actionNotice !== "" ? <div className="notice">{actionNotice}</div> : null}

        {/* ------------------------------------------------------------ the truth */}
        {/* The owner's word first, then the stage the backend derived from the facts.
            `files: 0` can never render ГОТОВО: that rule lives in the backend, and this
            block only repeats what it returned. */}
        <div className="project-truth" data-state={state}>
          <div className="stack" style={{ gap: 6 }}>
            <div className="label">{t(lang, "projects.truth.title")}</div>
            <div className="project-truth-state">{text(state)}</div>
            <div className="small muted">
              {t(lang, "projects.truth.stage")}: {stageWord === "" ? text(stageLabel) : text(stageWord)}
            </div>
          </div>
          <div className="stack project-truth-lines" style={{ gap: 4 }}>
            {(
              [
                [t(lang, "projects.truth.title"), stageWord === "" ? text(state) : `${text(state)} · ${stageWord}`],
                [t(lang, "projects.facts.files"), String(fileTotal)],
                [t(lang, "projects.facts.folder"), text(project.project_path, t(lang, "projects.facts.none"))],
                [
                  t(lang, "projects.facts.version"),
                  currentVersion > 0 ? `v${currentVersion}` : t(lang, "projects.facts.none"),
                ],
                [t(lang, "projects.facts.entry"), entryLabel],
                [t(lang, "projects.truth.verification"), text(presentation.verification_line)],
                [t(lang, "projects.facts.next"), text(presentation.next_step, t(lang, "projects.facts.none"))],
              ] as Array<[string, string]>
            )
              .filter(([label]) => label !== t(lang, "projects.truth.title"))
              .map(([label, value]) => (
                <div className="row between" key={label} style={{ gap: 12, alignItems: "flex-start" }}>
                  <span className="small muted" style={{ flex: "0 0 180px" }}>
                    {label}
                  </span>
                  <span className="small" style={{ flex: "1 1 auto", textAlign: "right", overflowWrap: "anywhere" }}>
                    {value}
                  </span>
                </div>
              ))}
          </div>
        </div>

        {notices.map((line) => (
          <div className="notice warn" key={line}>
            {line}
          </div>
        ))}

        {/* --------------------------------------------------- no project yet */}
        {/* The only creation actions that exist when there is nothing to act on. They are
            the same ids the backend offers in `presentation.actions`. */}
        {fileTotal === 0 ? (
          <div className="notice warn">
            <div className="label">{t(lang, "projects.empty.title")}</div>
            <div className="row" style={{ gap: 8, flexWrap: "wrap", marginTop: 8 }}>
              <Button
                size="small"
                tone="primary"
                onClick={() => runAction("continue_creation")}
                disabled={archived}
              >
                {t(lang, "projects.empty.continue")}
              </Button>
              <Button size="small" onClick={() => runAction("answer_questions")} disabled={archived}>
                {t(lang, "projects.empty.answer")}
              </Button>
            </div>
          </div>
        ) : null}

        {/* ---------------------------------------------------------- the preview */}
        {/* Project folder · files created · entry point · verification command · run
            command. The launch buttons appear only when the backend detected an entry
            point AND a safe way to serve it exists; otherwise the honest sentence. */}
        <div>
          <div className="label">{t(lang, "projects.preview.title")}</div>
          <div style={{ marginTop: 8 }}>
            <Facts
              items={[
                [t(lang, "projects.preview.folder"), text(project.project_path, t(lang, "projects.facts.none"))],
                [t(lang, "projects.preview.files"), String(fileTotal)],
                [t(lang, "projects.preview.entry"), entryLabel],
                [
                  t(lang, "projects.preview.verify_command"),
                  verificationCommand !== ""
                    ? verificationCommand
                    : t(lang, "projects.preview.nothing_to_verify"),
                ],
                [
                  t(lang, "projects.preview.run_command"),
                  entrypoint !== null && String(entrypoint.run_command ?? "") !== ""
                    ? String(entrypoint.run_command)
                    : t(lang, "projects.facts.none"),
                ],
              ]}
            />
          </div>
          <div className="row" style={{ gap: 8, flexWrap: "wrap", marginTop: 10 }}>
            <Button
              size="small"
              tone="ghost"
              disabled={fileTotal === 0}
              onClick={() =>
                document.getElementById("project-artifacts")?.scrollIntoView({ behavior: "smooth", block: "start" })
              }
            >
              {t(lang, "projects.preview.view")}
            </Button>
            <Button size="small" tone="ghost" onClick={() => setLaunchNote(t(lang, "projects.preview.folder_note"))}>
              {t(lang, "projects.preview.folder_action")}
            </Button>
            <Button
              size="small"
              tone="primary"
              disabled={!previewAvailable || previewRunning || previewBusy}
              onClick={onPreviewStart}
            >
              {previewBusy ? t(lang, "state.loading") : t(lang, "projects.preview.start")}
            </Button>
            <Button
              size="small"
              disabled={!previewRunning || previewUrl === ""}
              onClick={() => {
                if (previewUrl !== "") window.open(previewUrl, "_blank", "noopener,noreferrer");
              }}
            >
              {t(lang, "projects.preview.open")}
            </Button>
            <Button
              size="small"
              tone="ghost"
              disabled={!previewRunning || previewBusy}
              onClick={onPreviewStop}
            >
              {t(lang, "projects.preview.stop")}
            </Button>
            <Button size="small" tone="ghost" disabled={fileTotal === 0 || !hasRun} onClick={onVerify}>
              {t(lang, "projects.preview.reverify")}
            </Button>
          </div>
          {previewRunning && previewUrl !== "" ? (
            <div className="small row" style={{ gap: 6, flexWrap: "wrap", marginTop: 8 }}>
              <Badge tone="ok">{t(lang, "projects.preview.running")}</Badge>
              <span className="muted">{t(lang, "projects.preview.url")}</span>
              <span className="mono">{previewUrl}</span>
            </div>
          ) : honestLaunchNote !== "" ? (
            <div className="notice" style={{ marginTop: 8 }}>
              {honestLaunchNote}
            </div>
          ) : null}
          {launchNote !== "" ? (
            <div className="notice" style={{ marginTop: 8 }}>
              {launchNote}
            </div>
          ) : null}
        </div>

        {/* ------------------------------------------------------- the local run */}
        {/* The command, the folder, the port, the process state and the logs. Every
            button appears only when the backend's own facts make it applicable; the
            launch itself goes through an explicit confirmation that prints the exact
            command, and the digest it shows is the value the backend compares. */}
        <RunPanel
          lang={lang}
          run={run}
          entrypoint={entrypoint}
          preview={preview}
          busy={runBusy}
          canVerify={fileTotal > 0 && hasRun}
          onStart={onRunStart}
          onStop={onRunStop}
          onRestart={onRunRestart}
          onVerify={onVerify}
          onShowFiles={() =>
            document.getElementById("project-artifacts")?.scrollIntoView({ behavior: "smooth", block: "start" })
          }
        />

        {/* -------------------------------------------------------- the save facts */}
        <div>
          <div className="label">{t(lang, "projects.save.title")}</div>
          <div className="save-facts" style={{ marginTop: 8 }}>
            {saveFacts.map((item) => {
              const known = item.known === true;
              const value = String(item.value ?? t(lang, "projects.save.unknown"));
              const tone = !known ? "neutral" : value === "да" || value === "yes" ? "ok" : value === "неизвестно" ? "neutral" : "neutral";
              return (
                <div className="save-fact" key={String(item.key ?? item.label ?? "")} data-known={known ? "yes" : "no"}>
                  <div className="label">{text(item.label)}</div>
                  <Badge tone={tone}>{value}</Badge>
                </div>
              );
            })}
          </div>
          <div className="small muted" style={{ marginTop: 6 }}>
            {t(lang, "projects.save.hint")}
          </div>
        </div>

        {/* ------------------------------------------------------------- the facts */}
        <div>
          <div className="label">{t(lang, "projects.where.title")}</div>
          <div className="stack" style={{ gap: 4, marginTop: 8 }}>
            {factRows.map(([label, value]) => (
              <div className="row between" key={label} style={{ gap: 12, alignItems: "flex-start" }}>
                <span className="small muted" style={{ flex: "0 0 180px" }}>
                  {label}
                </span>
                <span className="mono small" style={{ flex: "1 1 auto", textAlign: "right", overflowWrap: "anywhere" }}>
                  {value}
                </span>
              </div>
            ))}
          </div>
          <div className="small muted" style={{ marginTop: 6 }}>
            {t(lang, "projects.where.note")}
          </div>
        </div>

        {/* ------------------------------------------------------------ specification */}
        <div>
          <div className="label">{t(lang, "projects.spec.title")}</div>
          <div className="stack" style={{ gap: 6, marginTop: 8 }}>
            {specKeys.map(([key, label]) => (
              <div className="row between" key={key} style={{ gap: 12, alignItems: "flex-start" }}>
                <span className="small muted" style={{ flex: "0 0 200px" }}>
                  {label}
                </span>
                <span className="small" style={{ flex: "1 1 auto", textAlign: "right", overflowWrap: "anywhere" }}>
                  {specValue(specification[key])}
                </span>
              </div>
            ))}
          </div>
          <div className="small muted" style={{ marginTop: 6 }}>
            {t(lang, "projects.spec.hint")}
          </div>
        </div>

        {/* --------------------------------------------------------------- versions */}
        <div>
          <div className="row between" style={{ gap: 10, flexWrap: "wrap" }}>
            <div className="label">{t(lang, "projects.versions.title")}</div>
            <Button size="small" tone="ghost" onClick={onHistory}>
              {t(lang, "projects.action.history")}
            </Button>
          </div>
          {versions.length === 0 ? (
            <div className="small muted" style={{ marginTop: 6 }}>
              {t(lang, "projects.versions.empty")}
            </div>
          ) : (
            <table className="table" style={{ marginTop: 8 }}>
              <thead>
                <tr>
                  <th>{t(lang, "projects.col.version")}</th>
                  <th>{t(lang, "projects.col.created")}</th>
                  <th>{t(lang, "projects.col.run")}</th>
                  <th>{t(lang, "projects.col.verify")}</th>
                  <th>{t(lang, "projects.col.accept")}</th>
                  <th>{t(lang, "projects.col.provider")}</th>
                  <th>{t(lang, "projects.col.files")}</th>
                  <th>{t(lang, "projects.col.change")}</th>
                </tr>
              </thead>
              <tbody>
                {versions.map((version) => {
                  const number = typeof version.version === "number" ? version.version : 0;
                  const mock = String(version.provider ?? "") === OFFLINE_PROVIDER;
                  return (
                    <tr
                      key={number}
                      className={number === selectedVersion ? "selected" : ""}
                      style={{ cursor: "pointer" }}
                      onClick={() => onSelectVersion(number)}
                    >
                      <td className="mono small">v{number}</td>
                      <td className="mono small">{text(version.created_at)}</td>
                      <td className="mono small" style={{ maxWidth: 220, overflowWrap: "anywhere" }}>
                        {version.pending === true ? t(lang, "projects.version.pending") : text(version.run_id)}
                      </td>
                      <td>
                        <Badge tone={version.verification === "passed" ? "ok" : version.verification === "failed" ? "danger" : "neutral"}>
                          {verificationLabel(lang, version.verification)}
                        </Badge>
                      </td>
                      <td>
                        <Badge tone={version.acceptance === "passed" ? "ok" : version.acceptance === "failed" ? "danger" : "neutral"}>
                          {acceptanceLabel(lang, version.acceptance)}
                        </Badge>
                      </td>
                      <td className="small">
                        {text(version.provider)}
                        <span className="muted"> · {text(version.model)}</span>
                        {mock ? (
                          <div className="small muted">{t(lang, "projects.version.mock")}</div>
                        ) : null}
                      </td>
                      <td className="mono small">{typeof version.file_count === "number" ? version.file_count : "—"}</td>
                      <td className="small" style={{ maxWidth: 260, overflowWrap: "anywhere" }}>
                        {version.change_request && typeof version.change_request.text === "string"
                          ? version.change_request.text
                          : t(lang, "projects.version.first")}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
          <div className="small muted" style={{ marginTop: 6 }}>
            {t(lang, "projects.versions.hint")}
          </div>
        </div>

        {/* -------------------------------------------------------------- artifacts */}
        <div id="project-artifacts">
          <div className="row between" style={{ gap: 10, flexWrap: "wrap" }}>
            <div className="label">{t(lang, "projects.artifacts.title")}</div>
            <Badge tone="neutral">{`v${filesVersion}`}</Badge>
          </div>
          {files.length === 0 ? (
            <div className="stack small" style={{ gap: 4, marginTop: 8 }}>
              <div className="muted">{t(lang, "projects.artifacts.empty").replace("{version}", `v${filesVersion}`)}</div>
              {String(versions.find((item) => item.version === filesVersion)?.provider ?? "") === OFFLINE_PROVIDER ? (
                <div className="muted">{t(lang, "projects.artifacts.mock")}</div>
              ) : null}
            </div>
          ) : (
            <div style={{ marginTop: 8 }}>
              <ArtifactTable lang={lang} artifacts={files} />
            </div>
          )}
        </div>

        {/* --------------------------------------------------------------- actions */}
        {/* The offered set is the backend's: an action that is not visible is not
            offered at all, and every disabled one carries its reason below. */}
        <div>
          <div className="label">{t(lang, "projects.actions.title")}</div>
          <div className="row" style={{ gap: 8, flexWrap: "wrap", alignItems: "center", marginTop: 8 }}>
            {actions.map((item) => {
              const id = String(item.id ?? "");
              const enabled = item.enabled !== false;
              return (
                <Button
                  key={id}
                  size="small"
                  tone={item.primary === true ? "primary" : "ghost"}
                  disabled={!enabled || busy === "verify"}
                  onClick={() => runAction(id)}
                >
                  {busy === "verify" && id === "reverify"
                    ? t(lang, "projects.verify.running")
                    : text(item.label, id)}
                </Button>
              );
            })}
          </div>
          {/* A reason is printed for every action that cannot be used, and for a
              hidden one when its absence would otherwise be a mystery. */}
          <div className="stack" style={{ gap: 2, marginTop: 8 }}>
            {actions
              .filter((item) => item.enabled === false && String(item.reason ?? "") !== "")
              .map((item) => (
                <div className="small muted" key={`why-${String(item.id)}`}>
                  {text(item.label)}: {text(item.reason)}
                </div>
              ))}
            {hiddenActions
              .filter((item) => String(item.reason ?? "") !== "")
              .map((item) => (
                <div className="small muted" key={`hidden-${String(item.id)}`}>
                  {text(item.label)} {t(lang, "projects.actions.hidden")}: {text(item.reason)}
                </div>
              ))}
          </div>
          {launchNote !== "" ? (
            <div className="notice" style={{ marginTop: 8 }}>
              {launchNote}
            </div>
          ) : null}
        </div>

        {/* The record operations. They are not part of the creation action set: they
            act on the record itself and stay available whatever it contains. */}
        <div className="row" style={{ gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          <Button size="small" tone="ghost" onClick={onArchive}>
            {busy === "archive" ? t(lang, "projects.archive.running") : t(lang, "projects.action.archive")}
          </Button>
          <Button size="small" tone="ghost" onClick={onRestore}>
            {busy === "restore" ? t(lang, "projects.restore.running") : t(lang, "projects.action.restore")}
          </Button>
          <Button size="small" tone="ghost" onClick={onHistory}>
            {t(lang, "projects.action.history")}
          </Button>
          {/* Deleting is a *move* into `.forge/projects-deleted`, so the confirmation says
              exactly that before the second click. A record without an id gets a visibly
              disabled button and the reason, never a click that does nothing. */}
          <Button
            size="small"
            tone="danger"
            disabled={!canDelete || busy === "delete"}
            disabledReason={canDelete ? undefined : t(lang, "projects.delete.no_id")}
            onClick={() => setConfirmDelete(true)}
          >
            {busy === "delete" ? t(lang, "projects.delete.running") : t(lang, "projects.action.delete")}
          </Button>
          {!canDelete ? <span className="small muted">{t(lang, "projects.delete.no_id")}</span> : null}
        </div>

        {confirmDelete ? (
          <div className="notice danger" data-testid="project-delete-confirm">
            <div className="label">
              {t(lang, "projects.delete.confirm.title").replace("{id}", text(projectId))}
            </div>
            <div className="small" style={{ marginTop: 6 }}>
              {t(lang, "projects.delete.confirm.body")}
            </div>
            <div className="row" style={{ gap: 8, flexWrap: "wrap", marginTop: 10 }}>
              <Button
                size="small"
                tone="danger"
                disabled={busy === "delete"}
                onClick={() => {
                  setConfirmDelete(false);
                  onDelete();
                }}
              >
                {t(lang, "projects.delete.confirm.yes")}
              </Button>
              <Button size="small" tone="ghost" onClick={() => setConfirmDelete(false)}>
                {t(lang, "projects.delete.confirm.cancel")}
              </Button>
            </div>
          </div>
        ) : null}

        {/* ----------------------------------------------------------- new version */}
        <div>
          <div className="label">{t(lang, "projects.newversion.title")}</div>
          <div className="stack" style={{ gap: 10, marginTop: 8 }}>
            <Field
              label={t(lang, "projects.newversion.label")}
              hint={t(lang, "projects.newversion.hint")}
              error={changeError}
            >
              <textarea
                className="input"
                rows={3}
                ref={changeRef}
                value={change}
                onChange={(event) => onChange(event.target.value)}
                placeholder={t(lang, "projects.newversion.placeholder")}
              />
            </Field>
            <div className="row" style={{ gap: 12, flexWrap: "wrap" }}>
              <label className="field" style={{ flex: "0 1 190px" }}>
                <span className="label">{t(lang, "projects.col.provider")}</span>
                <select
                  className="input"
                  value={provider}
                  onChange={(event) => onProvider(event.target.value)}
                  disabled={backend.status !== "live"}
                >
                  <option value="">{t(lang, "projects.newversion.provider.auto")}</option>
                  {providerOptions(backend).map((item) => (
                    <option key={item} value={item}>
                      {item}
                    </option>
                  ))}
                </select>
              </label>
              <label className="field" style={{ flex: "0 1 240px" }}>
                <span className="label">{t(lang, "projects.col.model")}</span>
                {modelChoices.length > 0 ? (
                  <select className="input" value={model} onChange={(event) => onModel(event.target.value)}>
                    {modelChoices.map((item) => (
                      <option key={item} value={item}>
                        {item}
                      </option>
                    ))}
                  </select>
                ) : (
                  <input
                    className="input"
                    type="text"
                    value={model}
                    onChange={(event) => onModel(event.target.value)}
                    placeholder={t(lang, "forge.task.model.placeholder")}
                  />
                )}
              </label>
            </div>
            <div className="row" style={{ gap: 12, flexWrap: "wrap", alignItems: "center" }}>
              <Button size="small" disabled={busy === "version"} onClick={onNewVersion}>
                {busy === "version" ? t(lang, "projects.newversion.running") : t(lang, "projects.newversion.create")}
              </Button>
              <label className={`forge-toggle${mockSelected ? " disabled" : ""}`}>
                <input
                  type="checkbox"
                  checked={writeMode && !mockSelected}
                  disabled={mockSelected}
                  onChange={(event) => onWriteMode(event.target.checked)}
                />
                <span>{t(lang, "projects.newversion.write")}</span>
                {mockSelected ? <span className="small muted">· {t(lang, "forge.discovery.mock.write")}</span> : null}
              </label>
            </div>
            {mockSelected ? <div className="notice">{t(lang, "forge.discovery.mock.note")}</div> : null}
            <div className="small muted">{t(lang, "projects.newversion.session.note")}</div>

            {staged !== null ? (
              <div className="stack" style={{ gap: 8 }}>
                <div className="label">
                  {t(lang, "projects.newversion.staged").replace("{version}", `v${text(staged.version)}`)}
                </div>
                <pre className="forge-output mono small">{text(staged.task_text)}</pre>
                <div className="row" style={{ gap: 10, flexWrap: "wrap", alignItems: "center" }}>
                  <Button size="small" tone="primary" disabled={dispatching} onClick={onDispatch}>
                    {dispatching ? t(lang, "projects.newversion.submitting") : t(lang, "projects.newversion.submit")}
                  </Button>
                  <span className="small muted">{t(lang, "projects.newversion.submit.hint")}</span>
                </div>
              </div>
            ) : null}

            {dispatchFailure !== null ? <Refusal lang={lang} failure={dispatchFailure} /> : null}
            {dispatched !== null ? (
              <Facts
                items={[
                  [t(lang, "projects.col.run"), text(dispatched.response.run_id)],
                  [t(lang, "projects.col.status"), text(dispatched.response.state, t(lang, "runs.unknown"))],
                  [t(lang, "projects.outcome.route"), `${text(dispatched.response.provider_used)} · ${text(dispatched.response.model_used)}`],
                  [
                    t(lang, "projects.outcome.duration"),
                    typeof dispatched.response.duration_seconds === "number"
                      ? `${dispatched.response.duration_seconds.toFixed(3)} s`
                      : t(lang, "runs.unknown"),
                  ],
                  [t(lang, "runs.result.tokens"), dispatched.response.tokens === null || dispatched.response.tokens === undefined ? t(lang, "runs.unknown") : String(dispatched.response.tokens)],
                ]}
              />
            ) : null}
          </div>
        </div>

        {/* ------------------------------------------------------- technical details */}
        {/* Everything a reader needs to trace the card back to the record, and nothing
            that belongs to the owner's reading. It is collapsed until asked for. */}
        <details className="tech-details">
          <summary>{t(lang, "projects.tech.title")}</summary>
          <div className="stack" style={{ gap: 4, marginTop: 8 }}>
            {technicalFields.map((field) => {
              const value = text(field.value);
              // A long list (the route table) stays complete but scrolls, so it cannot
              // push every other technical fact off the block.
              const long = value.length > 240;
              return (
                <div className="row between" key={String(field.key ?? field.label ?? "")} style={{ gap: 12, alignItems: "flex-start" }}>
                  <span className="small muted" style={{ flex: "0 0 200px" }}>
                    {text(field.label)}
                  </span>
                  <span
                    className="mono small"
                    style={{
                      flex: "1 1 auto",
                      textAlign: "right",
                      overflowWrap: "anywhere",
                      ...(long ? { maxHeight: 130, overflowY: "auto", display: "block" } : {}),
                    }}
                  >
                    {value}
                  </span>
                </div>
              );
            })}
            {classification !== null ? (
              <div className="row between" style={{ gap: 12, alignItems: "flex-start" }}>
                <span className="small muted" style={{ flex: "0 0 200px" }}>
                  {t(lang, "projects.tech.evidence")}
                </span>
                <span className="mono small" style={{ flex: "1 1 auto", textAlign: "right", overflowWrap: "anywhere" }}>
                  {(classification.evidence ?? []).join(", ") || "—"}
                </span>
              </div>
            ) : null}
            <div className="row between" style={{ gap: 12, alignItems: "flex-start" }}>
              <span className="small muted" style={{ flex: "0 0 200px" }}>
                {t(lang, "projects.tech.runrecord")}
              </span>
              <span className="mono small" style={{ flex: "1 1 auto", textAlign: "right", overflowWrap: "anywhere" }}>
                {runLoading
                  ? t(lang, "projects.outcome.reason.loading")
                  : runFailure !== null
                    ? `${t(lang, "projects.outcome.reason.unreadable")}: HTTP ${runFailure.status}`
                    : hasRun
                      ? classification === null
                        ? t(lang, "projects.outcome.reason.absent")
                        : t(lang, "projects.tech.readable")
                      : t(lang, "projects.tech.norun")}
              </span>
            </div>
            <div className="row between" style={{ gap: 12, alignItems: "flex-start" }}>
              <span className="small muted" style={{ flex: "0 0 200px" }}>
                {t(lang, "projects.tech.savefacts")}
              </span>
              <span className="mono small" style={{ flex: "1 1 auto", textAlign: "right", overflowWrap: "anywhere" }}>
                {saveFacts.map((item) => `${text(item.label)}=${text(item.value)}`).join(" · ") || "—"}
              </span>
            </div>
            <div className="row between" style={{ gap: 12, alignItems: "flex-start" }}>
              <span className="small muted" style={{ flex: "0 0 200px" }}>
                {t(lang, "projects.tech.verdicts")}
              </span>
              <span className="mono small" style={{ flex: "1 1 auto", textAlign: "right", overflowWrap: "anywhere" }}>
                {currentVersion === 0
                  ? t(lang, "projects.tech.noversion")
                  : `${t(lang, "projects.outcome.verify")}=${verificationLabel(lang, outcome)} · ${t(lang, "projects.outcome.accept")}=${acceptanceLabel(lang, acceptance)}`}
              </span>
            </div>
          </div>
        </details>
      </div>
    </Card>
  );
};

/** The providers the Core itself reported: enabled_providers plus the agents' own providers. */
const providerOptions = (backend: BackendState): string[] => {
  if (backend.status !== "live") return [];
  const details = backend.core.details ?? {};
  const enabled = Array.isArray(details.enabled_providers)
    ? details.enabled_providers.map((item) => String(item ?? "")).filter((item) => item !== "")
    : [];
  const fromAgents = backend.agents.map((agent) => String(agent.provider ?? "")).filter((item) => item !== "");
  const list: string[] = [];
  for (const item of [...enabled, ...fromAgents]) if (!list.includes(item)) list.push(item);
  return list;
};

export const Projects = ({
  lang,
  backend,
  onRefresh,
  onHelp,
  focusProjectId = "",
  focusNonce = 0,
}: {
  lang: Lang;
  backend: BackendState;
  onRefresh: () => void;
  onHelp: () => void;
  /**
   * A project another screen asked to open (the result card's «Открыть файлы»).
   *
   * Without it that click only switched screens: the list appeared with no card selected,
   * which is exactly the "nothing happens" the owner reported. `focusNonce` makes a second
   * click on the same project a fresh request.
   */
  focusProjectId?: string;
  focusNonce?: number;
}) => {
  const live = backend.status === "live";

  // ------------------------------------------------------------------ the list
  const [list, setList] = useState<Load<LifecycleSummary[]>>({ status: "loading" });
  const [filter, setFilter] = useState<"active" | "archive">("active");
  const [selectedId, setSelectedId] = useState("");

  const loadList = useCallback(async () => {
    setList({ status: "loading" });
    const outcome = await loadProjectLifecycle();
    setList(outcome.ok ? { status: "ready", value: outcome.value } : { status: "failed", failure: outcome.failure });
  }, []);

  useEffect(() => {
    void loadList();
  }, [loadList]);

  const summaries = list.status === "ready" ? list.value : [];
  const visible = useMemo(
    () => summaries.filter((project) => (filter === "archive" ? project.archived === true : project.archived !== true)),
    [summaries, filter]
  );

  /**
   * Open one project: select it, then bring its card into view once the card exists.
   *
   * The card renders below the list, so selecting alone looked like nothing happened on a
   * screen with more rows than fit: the measured complaint. The scroll is therefore tied to
   * the card's *content* arriving (`detail`), not to the click: at click time the card is
   * still the «выберите проект» placeholder, and scrolling to that would stop short.
   */
  const scrollToCard = useRef(false);
  const openProject = useCallback((projectId: string) => {
    if (projectId === "") return;
    scrollToCard.current = true;
    setSelectedId(projectId);
  }, []);

  // A click on another screen's «Открыть файлы»/«Запустить локально» arrives here as
  // `focusProjectId`. It selects that project and scrolls to its card once the list is
  // readable; the detail route is fetched by the existing effect on `selectedId`.
  useEffect(() => {
    if (focusProjectId === "") return;
    openProject(focusProjectId);
  }, [focusProjectId, focusNonce, openProject]);

  // An archived project lives under «Архив»: switch the tab so the row the click asked for
  // is actually in the table that is shown.
  useEffect(() => {
    if (focusProjectId === "" || list.status !== "ready") return;
    const found = summaries.find((project) => project.project_id === focusProjectId);
    if (found === undefined) return;
    setFilter(found.archived === true ? "archive" : "active");
  }, [focusProjectId, focusNonce, list, summaries]);

  // Keep the selection valid across a refresh: a project that vanished must not leave a card
  // describing a record nobody can fetch any more.
  useEffect(() => {
    if (selectedId === "") return;
    if (list.status !== "ready") return;
    if (summaries.some((project) => project.project_id === selectedId)) return;
    setSelectedId("");
  }, [list, summaries, selectedId]);

  // ---------------------------------------------------------------- the detail
  const [detail, setDetail] = useState<Load<LifecycleDetail> | null>(null);
  const [busy, setBusy] = useState("");
  const [actionFailure, setActionFailure] = useState<Failure | null>(null);
  const [actionNotice, setActionNotice] = useState("");

  const loadDetail = useCallback(async (projectId: string) => {
    if (projectId === "") {
      setDetail(null);
      return;
    }
    setDetail({ status: "loading" });
    const outcome = await loadProjectDetail(projectId);
    setDetail(outcome.ok ? { status: "ready", value: outcome.value } : { status: "failed", failure: outcome.failure });
    if (!scrollToCard.current) return;
    scrollToCard.current = false;
    // Two frames: one for React to commit the card, one for the browser to lay it out.
    // Smooth scrolling is then a real animation from the list the user was looking at.
    window.requestAnimationFrame(() =>
      window.requestAnimationFrame(() => revealProjectCard())
    );
  }, []);

  const project = detail?.status === "ready" ? detail.value.project ?? null : null;
  const versions: LifecycleVersion[] = detail?.status === "ready" ? detail.value.versions ?? [] : [];
  const currentVersionNumber = typeof project?.current_version === "number" ? project.current_version : 0;

  // ------------------------------------------------------------------ artifacts
  // `null` means "the current version's manifest"; a number means a version the owner clicked.
  const [files, setFiles] = useState<{ version: number; items: LifecycleArtifact[] } | null>(null);
  const [selectedVersion, setSelectedVersion] = useState(0);

  const filesVersion = selectedVersion > 0 ? selectedVersion : currentVersionNumber;

  useEffect(() => {
    if (selectedId === "" || filesVersion <= 0) {
      setFiles(null);
      return;
    }
    let cancelled = false;
    void (async () => {
      const outcome = await loadProjectFiles(selectedId, filesVersion);
      if (cancelled) return;
      // A failed manifest read is shown as "no files recorded" only when the backend said so;
      // otherwise the card keeps the previous answer and the failure is not turned into an
      // empty list that would read as "the run wrote nothing".
      setFiles(outcome.ok ? { version: filesVersion, items: outcome.value.files ?? [] } : { version: filesVersion, items: [] });
    })();
    return () => {
      cancelled = true;
    };
  }, [selectedId, filesVersion, detail]);

  const artifacts = files?.version === filesVersion ? files.items : [];

  // ------------------------------------------------------------------- preview
  // The preview is *runtime* state (`GET /api/projects/{id}/preview`), read from the detail
  // payload and refreshed after every start/stop. A project with no file, no entry point or a
  // python/node entry point keeps `available: false` and the card prints the backend's own
  // reason instead of offering a launch.
  const [previewState, setPreviewState] = useState<LifecyclePreview | null>(null);
  const [previewBusy, setPreviewBusy] = useState(false);

  useEffect(() => {
    const fromDetail = detail?.status === "ready" ? detail.value.preview ?? null : null;
    setPreviewState(fromDetail);
  }, [detail]);

  // The detail payload already carries this; `previewState` is the fresher answer a
  // start/stop returned, so it wins when it exists.
  const preview: LifecyclePreview =
    previewState ?? (detail?.status === "ready" ? detail.value.preview ?? {} : {});
  const entrypoint: LifecycleEntryPoint | null =
    detail?.status === "ready" ? detail.value.entrypoint ?? null : null;
  const stageWord = detail?.status === "ready" ? String(detail.value.stage ?? "") : "";
  const verificationCommand =
    detail?.status === "ready" ? String(detail.value.verification_command ?? "") : "";
  const filesCreated =
    detail?.status === "ready" && typeof detail.value.files_created === "number"
      ? detail.value.files_created
      : typeof preview.files_created === "number"
        ? preview.files_created
        : 0;

  const changePreview = async (action: "start" | "stop") => {
    if (selectedId === "") return;
    setPreviewBusy(true);
    setActionFailure(null);
    setActionNotice("");
    const outcome = await changeProjectPreview(selectedId, { action });
    setPreviewBusy(false);
    if (!outcome.ok) {
      setActionFailure(outcome.failure);
      return;
    }
    setPreviewState(outcome.value);
    await loadDetail(selectedId);
  };

  // ---------------------------------------------------------------- local run
  // The run block is *runtime* state (`GET /api/projects/{id}/run`), read from the
  // detail payload and refreshed while the process is alive. A started process is
  // never rendered as a finished project: the card prints the supervisor's own word
  // and lets the backend decide what the stage is.
  const [run, setRun] = useState<LifecycleRun | null>(null);
  const [runBusy, setRunBusy] = useState("");

  useEffect(() => {
    setRun(detail?.status === "ready" ? detail.value.run ?? null : null);
  }, [detail]);

  const currentRun: LifecycleRun =
    run ?? (detail?.status === "ready" ? detail.value.run ?? {} : {});

  const refreshRun = useCallback(async () => {
    if (selectedId === "") return;
    const outcome = await loadProjectRun(selectedId);
    if (outcome.ok) setRun(outcome.value);
  }, [selectedId]);

  const runStateWord = String(currentRun.state ?? "NOT_RUN");
  useEffect(() => {
    if (selectedId === "") return;
    if (!["STARTING", "RUNNING", "STOPPING"].includes(runStateWord)) return;
    const timer = window.setInterval(() => {
      void refreshRun();
    }, 1500);
    return () => window.clearInterval(timer);
  }, [selectedId, runStateWord, refreshRun]);

  const finishRunAction = async (value: LifecycleRun) => {
    setRun(value);
    await loadDetail(selectedId);
    await loadList();
  };

  const runStart = async (digest: string) => {
    if (selectedId === "") return;
    setRunBusy("start");
    setActionFailure(null);
    setActionNotice("");
    const outcome = await startProjectRun(selectedId, digest);
    setRunBusy("");
    if (!outcome.ok) {
      setActionFailure(outcome.failure);
      return;
    }
    await finishRunAction(outcome.value);
  };

  const runRestart = async (digest: string) => {
    if (selectedId === "") return;
    setRunBusy("restart");
    setActionFailure(null);
    setActionNotice("");
    const outcome = await restartProjectRun(selectedId, digest);
    setRunBusy("");
    if (!outcome.ok) {
      setActionFailure(outcome.failure);
      return;
    }
    await finishRunAction(outcome.value);
  };

  const runStop = async () => {
    if (selectedId === "") return;
    setRunBusy("stop");
    setActionFailure(null);
    setActionNotice("");
    const outcome = await stopProjectRun(selectedId);
    setRunBusy("");
    if (!outcome.ok) {
      setActionFailure(outcome.failure);
      return;
    }
    await finishRunAction(outcome.value);
  };

  // ---------------------------------------------------------------- run record
  const [runRecord, setRunRecord] = useState<RunRecord | null>(null);
  const [runFailure, setRunFailure] = useState<Failure | null>(null);
  const [runLoading, setRunLoading] = useState(false);

  useEffect(() => {
    const current = versions.find((item) => item.version === currentVersionNumber) ?? null;
    const runId = String(current?.run_id ?? "");
    setRunRecord(null);
    setRunFailure(null);
    setRunLoading(runId !== "");
    if (runId === "") return;
    let cancelled = false;
    void (async () => {
      const outcome = await loadRunRecord(runId);
      if (cancelled) return;
      if (outcome.ok) setRunRecord(outcome.value);
      else setRunFailure(outcome.failure);
      setRunLoading(false);
    })();
    return () => {
      cancelled = true;
    };
  }, [versions, currentVersionNumber]);

  // ------------------------------------------------------------------- history
  const [history, setHistory] = useState<Load<LifecycleHistory> | null>(null);
  const openHistory = async () => {
    if (selectedId === "") return;
    setHistory({ status: "loading" });
    const outcome = await loadProjectHistory(selectedId);
    setHistory(outcome.ok ? { status: "ready", value: outcome.value } : { status: "failed", failure: outcome.failure });
  };

  // ------------------------------------------------------------------- deletion
  //
  // Deleting a project moves its folder into `.forge/projects-deleted`; the backend
  // refuses while a process is running and says so with a 409 the Refusal block prints.
  // Two clicks are required, and the first one only reveals the sentence that says what
  // will happen -- nothing moves on an accidental press.
  const deleteSelected = async () => {
    if (selectedId === "") return;
    const identifier = selectedId;
    setBusy("delete");
    setActionFailure(null);
    setActionNotice("");
    setDeleteNotice("");
    const outcome = await deleteProject(identifier);
    setBusy("");
    if (!outcome.ok) {
      setActionFailure(outcome.failure);
      return;
    }
    const moved = String(outcome.value.trash_path ?? outcome.value.trash_name ?? "");
    // The confirmation card is gone once the project is gone, so the result is printed
    // beside the list -- otherwise the click's outcome would vanish with the card.
    setDeleteNotice(
      t(lang, "projects.delete.done")
        .replace("{id}", identifier)
        .replace("{path}", moved)
    );
    setSelectedId("");
    setDetail(null);
    await loadList();
    await loadTrash();
    onRefresh();
  };

  // -------------------------------------------------------------------- the trash
  //
  // «Удалённые проекты» is not a second project list: it is the answer of
  // `GET /api/projects/deleted`, which reads `.forge/projects-deleted` directly. Every
  // row says when the project was deleted, how many files it held and what status it
  // had, because those three facts decide whether restoring it is worth doing. There
  // is no permanent-delete control here or anywhere in this screen: restore is the
  // only action the trash offers.
  const [trash, setTrash] = useState<Load<LifecycleDeletedProject[]>>({ status: "loading" });
  const [trashOpen, setTrashOpen] = useState(false);
  const [trashTarget, setTrashTarget] = useState("");
  const [trashBusy, setTrashBusy] = useState("");
  const [trashNotice, setTrashNotice] = useState("");
  const [trashFailure, setTrashFailure] = useState<Failure | null>(null);

  const loadTrash = useCallback(async () => {
    const outcome = await loadDeletedProjects();
    setTrash(outcome.ok ? { status: "ready", value: outcome.value } : { status: "failed", failure: outcome.failure });
  }, []);

  useEffect(() => {
    void loadTrash();
  }, [loadTrash]);

  const trashItems = trash.status === "ready" ? trash.value : [];
  const trashCount = trashItems.length;

  const restoreFromTrash = async (identifier: string) => {
    setTrashTarget("");
    setTrashBusy(identifier);
    setTrashFailure(null);
    setTrashNotice("");
    const outcome = await restoreDeletedProject(identifier);
    setTrashBusy("");
    if (!outcome.ok) {
      // The backend's own sentence (a name collision, an entry already restored) is
      // printed verbatim by `Refusal`: this screen never rewords a refusal into a
      // success or guesses which one happened.
      setTrashFailure(outcome.failure);
      return;
    }
    setTrashNotice(t(lang, "projects.trash.restored").replace("{id}", identifier));
    await loadList();
    await loadTrash();
    // The card the click restored is selected, so its files and versions are one
    // click away and the row is visibly back in the list.
    setSelectedId(identifier);
    onRefresh();
  };

  // ------------------------------------------------------------------ bulk cleanup
  // The rule the backend applies is stated in the interface too: empty (0 files) and in
  // NEEDS_CHANGES, FAILED or ARCHIVED. The button first lists what would be removed, so
  // the owner sees the ribbon of junk disappear *after* approving it by name.
  const EMPTY_CLEANUP_STATUSES = useMemo(() => ["NEEDS_CHANGES", "FAILED", "ARCHIVED"], []);
  const emptyCandidates = useMemo(
    () =>
      summaries.filter(
        (project) =>
          String(project.project_id ?? "") !== "" &&
          (typeof project.file_count !== "number" || project.file_count === 0) &&
          EMPTY_CLEANUP_STATUSES.includes(String(project.status ?? "").toUpperCase())
      ),
    [summaries, EMPTY_CLEANUP_STATUSES]
  );
  const [bulkStep, setBulkStep] = useState<"idle" | "confirm">("idle");
  const [bulkBusy, setBulkBusy] = useState(false);
  const [bulkNotice, setBulkNotice] = useState("");
  const [bulkFailure, setBulkFailure] = useState<Failure | null>(null);
  // The result of one deletion, printed beside the list: the card it belonged to is gone
  // by then, so the card cannot be the place that says what happened.
  const [deleteNotice, setDeleteNotice] = useState("");

  const runBulkCleanup = async () => {
    setBulkStep("idle");
    setBulkBusy(true);
    setBulkFailure(null);
    setBulkNotice("");
    const outcome = await deleteEmptyProjects();
    setBulkBusy(false);
    if (!outcome.ok) {
      setBulkFailure(outcome.failure);
      return;
    }
    const removed = outcome.value.deleted_count ?? 0;
    const skipped = outcome.value.skipped_count ?? 0;
    const removedIds = (outcome.value.deleted ?? [])
      .map((item) => String(item.project_id ?? ""))
      .filter((item) => item !== "");
    setBulkNotice(
      `${t(lang, "projects.delete.bulk.done").replace("{n}", String(removed))}${
        removedIds.length > 0 ? ` (${removedIds.join(", ")})` : ""
      }`
    );
    if (skipped > 0) {
      setBulkNotice(
        `${t(lang, "projects.delete.bulk.done").replace("{n}", String(removed))} ` +
          t(lang, "projects.delete.bulk.skipped").replace("{n}", String(skipped))
      );
    }
    // A selection that was just removed must not leave a card describing a gone record.
    if (selectedId !== "" && removedIds.includes(selectedId)) {
      setSelectedId("");
      setDetail(null);
    }
    await loadList();
    await loadTrash();
    onRefresh();
  };

  // ------------------------------------------------------------------- actions
  const runAction = async (
    kind: "verify" | "archive" | "restore",
    call: () => Promise<{ ok: true; value: unknown } | { ok: false; failure: Failure }>,
    doneKey: string
  ) => {
    setBusy(kind);
    setActionFailure(null);
    setActionNotice("");
    const outcome = await call();
    setBusy("");
    if (!outcome.ok) {
      setActionFailure(outcome.failure);
      return;
    }
    setActionNotice(t(lang, doneKey));
    await loadDetail(selectedId);
    await loadList();
    onRefresh();
  };

  const [change, setChange] = useState("");
  const [changeError, setChangeError] = useState("");
  const [staged, setStaged] = useState<LifecycleNewVersion | null>(null);
  const [dispatching, setDispatching] = useState(false);
  const [dispatched, setDispatched] = useState<DispatchResult | null>(null);
  const [dispatchFailure, setDispatchFailure] = useState<Failure | null>(null);

  const [provider, setProvider] = useState("");
  const [model, setModel] = useState("");
  const [writeMode, setWriteMode] = useState(false);

  // One selection resets every per-project answer, so a card never mixes two projects: the staged
  // version, the dispatch result, the artifact manifest, the history, the selected version and the
  // run record all belong to the project that was open when they arrived.
  useEffect(() => {
    setActionFailure(null);
    setActionNotice("");
    setStaged(null);
    setDispatched(null);
    setDispatchFailure(null);
    setFiles(null);
    setHistory(null);
    setSelectedVersion(0);
    setRunRecord(null);
    setRunFailure(null);
    void loadDetail(selectedId);
  }, [selectedId, loadDetail]);

  // The providers come from the Core's own answer, so an empty list means "nothing to choose
  // from" and the request leaves the routing to the backend rather than naming a provider.
  const options = useMemo(() => providerOptions(backend), [backend]);
  useEffect(() => {
    if (options.length === 0) return;
    if (provider !== "" && options.includes(provider)) return;
    const fallback = backend.status === "live" ? String(backend.core.details?.default_provider ?? "") : "";
    setProvider(options.includes(fallback) ? fallback : options[0] ?? "");
  }, [options, provider, backend]);

  const modelChoices = useMemo(
    () => (provider === "" ? [] : catalogueModelsFor(backend.status === "live" ? backend.catalogue : null, provider)),
    [backend, provider]
  );
  useEffect(() => {
    if (modelChoices.length === 0) return;
    if (modelChoices.includes(model)) return;
    setModel(modelChoices[0] ?? "");
  }, [modelChoices, model]);

  const mockSelected = provider === OFFLINE_PROVIDER;

  const submitVersion = async () => {
    if (selectedId === "") return;
    const trimmed = change.trim();
    if (trimmed === "") {
      setChangeError(t(lang, "projects.newversion.error"));
      return;
    }
    setChangeError("");
    setBusy("version");
    setActionFailure(null);
    setActionNotice("");
    setDispatched(null);
    setDispatchFailure(null);
    const outcome = await createProjectVersion(selectedId, trimmed);
    setBusy("");
    if (!outcome.ok) {
      setStaged(null);
      setActionFailure(outcome.failure);
      return;
    }
    setStaged(outcome.value);
    await loadDetail(selectedId);
    await loadList();
  };

  const submitTask = async () => {
    if (staged === null || typeof staged.task_text !== "string" || staged.task_text === "") return;
    setDispatching(true);
    setDispatchFailure(null);
    const outcome = await dispatchTask({
      description: staged.task_text,
      category: "code",
      providerName: options.includes(provider) ? provider : undefined,
      modelName: model !== "" ? model : undefined,
      writeMode: writeMode && !mockSelected,
    });
    setDispatching(false);
    if (!outcome.ok) {
      setDispatchFailure(outcome.failure);
      return;
    }
    setDispatched(outcome.value);
    onRefresh();
  };

  return (
    <div className="content">
      <div className="row between" style={{ marginBottom: 18 }}>
        <div>
          <h1 style={{ margin: 0, fontSize: "var(--t-title)" }}>{t(lang, "nav.projects")}</h1>
          <div className="small muted" style={{ marginTop: 4 }}>
            {t(lang, "projects.subtitle")}
          </div>
        </div>
        <div className="row">
          <Button size="small" tone="ghost" onClick={onHelp}>
            {t(lang, "help.how")}
          </Button>
          <Button
            onClick={() => {
              void loadList();
              onRefresh();
              if (selectedId !== "") void loadDetail(selectedId);
            }}
          >
            <IconRefresh size={15} /> {t(lang, "action.refresh")}
          </Button>
        </div>
      </div>

      {!live ? <div className="notice warn" style={{ marginBottom: 16 }}>{t(lang, "state.offline")}</div> : null}

      {/* --------------------------------------------------------------- the list */}
      <Card className="rise">
        <CardHead
          title={t(lang, "projects.list.title")}
          hint={`/api/projects/lifecycle · ${summaries.length}`}
          action={
            <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
              <Button
                size="small"
                tone="ghost"
                disabled={bulkBusy}
                onClick={() => {
                  setBulkFailure(null);
                  setBulkNotice("");
                  setBulkStep(emptyCandidates.length === 0 ? "idle" : "confirm");
                  if (emptyCandidates.length === 0) setBulkNotice(t(lang, "projects.delete.bulk.none"));
                }}
              >
                {bulkBusy ? t(lang, "projects.delete.bulk.running") : t(lang, "projects.delete.bulk.button")}
              </Button>
              <div className="segment">
                <button className={filter === "active" ? "on" : ""} onClick={() => setFilter("active")}>
                  {t(lang, "projects.filter.active")}
                </button>
                <button className={filter === "archive" ? "on" : ""} onClick={() => setFilter("archive")}>
                  {t(lang, "projects.filter.archive")}
                </button>
              </div>
            </div>
          }
        />
        {list.status === "loading" ? (
          <div className="small muted">{t(lang, "state.loading")}</div>
        ) : list.status === "failed" ? (
          <Refusal lang={lang} failure={list.failure} onRetry={() => void loadList()} />
        ) : visible.length === 0 ? (
          <div className="small muted">
            {filter === "archive" ? t(lang, "projects.empty.archive") : t(lang, "projects.empty.active")}
          </div>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>{t(lang, "projects.col.name")}</th>
                <th>{t(lang, "projects.col.status")}</th>
                <th>{t(lang, "projects.col.version")}</th>
                <th>{t(lang, "projects.col.lastrun")}</th>
                <th>{t(lang, "projects.col.files")}</th>
                <th>{t(lang, "projects.col.folder")}</th>
                <th>{t(lang, "projects.col.actions")}</th>
              </tr>
            </thead>
            <tbody>
              {visible.map((project) => {
                const id = String(project.project_id ?? "");
                const archived = project.archived === true;
                return (
                  <tr
                    key={id}
                    className={id === selectedId ? "selected" : ""}
                    style={{ cursor: "pointer" }}
                    onClick={() => openProject(id)}
                  >
                    <td style={{ fontWeight: 600 }}>
                      {text(project.name, t(lang, "projects.card.untitled"))}
                      <div className="mono small muted">{id}</div>
                    </td>
                    <td>
                      <span className="row" style={{ gap: 6, flexWrap: "wrap" }}>
                        <StatusDot tone={statusTone(project.status) === "ok" ? "ok" : statusTone(project.status) === "danger" ? "danger" : "warn"} />
                        <Badge tone={statusTone(project.status)}>{text(project.status, t(lang, "runs.unknown"))}</Badge>
                      </span>
                    </td>
                    <td className="mono small">
                      {typeof project.current_version === "number" && project.current_version > 0
                        ? `v${project.current_version}`
                        : "—"}
                    </td>
                    <td className="mono small">{text(project.last_run_at)}</td>
                    <td className="mono small">{typeof project.file_count === "number" ? project.file_count : "—"}</td>
                    <td className="mono small" style={{ maxWidth: 340, overflowWrap: "anywhere" }}>
                      {text(project.project_path)}
                      {archived ? <div className="small muted">{t(lang, "projects.badge.archived")}</div> : null}
                    </td>
                    <td>
                      <Button size="small" onClick={() => openProject(id)}>
                        {t(lang, "projects.action.open")}
                      </Button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}

        {/* What the bulk cleanup would do, before it does it. The list is printed by id,
            because "the ribbon of junk" is exactly the rows the owner recognises. */}
        {bulkStep === "confirm" ? (
          <div className="notice warn" data-testid="projects-bulk-confirm">
            <div className="label">
              {t(lang, "projects.delete.bulk.found")
                .replace("{n}", String(emptyCandidates.length))
                .replace("{ids}", emptyCandidates.map((item) => String(item.project_id ?? "")).join(", "))}
            </div>
            <div className="small" style={{ marginTop: 6 }}>
              {t(lang, "projects.delete.bulk.hint")}
            </div>
            <div className="row" style={{ gap: 8, flexWrap: "wrap", marginTop: 10 }}>
              <Button size="small" tone="danger" disabled={bulkBusy} onClick={() => void runBulkCleanup()}>
                {t(lang, "projects.delete.bulk.confirm").replace("{n}", String(emptyCandidates.length))}
              </Button>
              <Button size="small" tone="ghost" onClick={() => setBulkStep("idle")}>
                {t(lang, "projects.delete.bulk.cancel")}
              </Button>
            </div>
          </div>
        ) : null}
        {deleteNotice !== "" ? (
          <div className="notice" style={{ marginTop: 8 }} data-testid="project-delete-notice">
            {deleteNotice}
          </div>
        ) : null}
        {bulkNotice !== "" ? (
          <div className="notice" style={{ marginTop: 8 }} data-testid="projects-bulk-notice">
            {bulkNotice}
          </div>
        ) : null}
        {bulkFailure !== null ? (
          <div style={{ marginTop: 8 }}>
            <Refusal lang={lang} failure={bulkFailure} />
          </div>
        ) : null}

        <div className="small muted" style={{ marginTop: 8 }}>
          {t(lang, "projects.list.hint")}
        </div>
      </Card>

      {/* ---------------------------------------------------------- deleted projects */}
      {/* Collapsed by default: the trash is where a mistake can be undone, not a second
          list to read every time. The heading states the real location and the count,
          and the only action is «Восстановить» -- there is no permanent delete. */}
      <div data-testid="projects-trash">
      <Card className="rise d1">
        <CardHead
          title={t(lang, "projects.trash.title")}
          hint={`/api/projects/deleted · ${trashCount}`}
          action={
            <Button
              size="small"
              tone="ghost"
              onClick={() => {
                const next = !trashOpen;
                setTrashOpen(next);
                if (next) void loadTrash();
              }}
            >
              {trashOpen ? t(lang, "projects.trash.hide") : t(lang, "projects.trash.show")}
            </Button>
          }
        />
        {!trashOpen ? (
          <div className="small muted">{t(lang, "projects.trash.closed")}</div>
        ) : trash.status === "loading" ? (
          <div className="small muted">{t(lang, "state.loading")}</div>
        ) : trash.status === "failed" ? (
          <Refusal lang={lang} failure={trash.failure} onRetry={() => void loadTrash()} />
        ) : trashItems.length === 0 ? (
          <div className="small muted">{t(lang, "projects.trash.empty")}</div>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>{t(lang, "projects.col.name")}</th>
                <th>{t(lang, "projects.trash.deleted_at")}</th>
                <th>{t(lang, "projects.col.files")}</th>
                <th>{t(lang, "projects.trash.status")}</th>
                <th>{t(lang, "projects.col.actions")}</th>
              </tr>
            </thead>
            <tbody>
              {trashItems.map((item) => {
                const id = String(item.project_id ?? "");
                const filesHeld =
                  typeof item.file_count === "number" ? String(item.file_count) : "—";
                return (
                  <tr key={String(item.trash_name ?? id)} data-testid="projects-trash-row">
                    <td style={{ fontWeight: 600 }}>
                      {text(item.name, t(lang, "projects.card.untitled"))}
                      <div className="mono small muted">{text(item.project_id)}</div>
                      <div className="mono small muted" style={{ overflowWrap: "anywhere" }}>
                        {text(item.trash_path)}
                      </div>
                    </td>
                    <td className="mono small">{text(item.deleted_at, t(lang, "projects.trash.unknown_date"))}</td>
                    <td className="mono small">{filesHeld}</td>
                    <td>
                      <Badge tone={statusTone(item.status)}>
                        {text(item.status, t(lang, "runs.unknown"))}
                      </Badge>
                    </td>
                    <td>
                      <Button
                        size="small"
                        disabled={id === "" || trashBusy === id}
                        disabledReason={id === "" ? t(lang, "projects.delete.no_id") : undefined}
                        onClick={() => {
                          setTrashFailure(null);
                          setTrashNotice("");
                          setTrashTarget(id);
                        }}
                      >
                        {trashBusy === id ? t(lang, "projects.trash.restoring") : t(lang, "projects.action.restore")}
                      </Button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}

        {/* The confirmation says exactly what the backend will do: move the folder back
            out of `.forge/projects-deleted`, not "delete" or "undo" in the abstract. */}
        {trashTarget !== "" ? (
          <div className="notice warn" data-testid="project-restore-confirm" style={{ marginTop: 10 }}>
            <div className="label">
              {t(lang, "projects.trash.confirm.title").replace("{id}", trashTarget)}
            </div>
            <div className="small" style={{ marginTop: 6 }}>
              {t(lang, "projects.trash.confirm.body")}
            </div>
            <div className="row" style={{ gap: 8, flexWrap: "wrap", marginTop: 10 }}>
              <Button
                size="small"
                tone="primary"
                disabled={trashBusy !== ""}
                onClick={() => void restoreFromTrash(trashTarget)}
              >
                {t(lang, "projects.trash.confirm.yes")}
              </Button>
              <Button size="small" tone="ghost" onClick={() => setTrashTarget("")}>
                {t(lang, "projects.trash.confirm.cancel")}
              </Button>
            </div>
          </div>
        ) : null}
        {trashNotice !== "" ? (
          <div className="notice" style={{ marginTop: 8 }} data-testid="project-restore-notice">
            {trashNotice}
          </div>
        ) : null}
        {trashFailure !== null ? (
          <div style={{ marginTop: 8 }} data-testid="project-restore-failure">
            <Refusal lang={lang} failure={trashFailure} />
          </div>
        ) : null}
        <div className="small muted" style={{ marginTop: 8 }}>
          {t(lang, "projects.trash.note")}
        </div>
      </Card>
      </div>

      {/* ------------------------------------------------------------- the card */}
      <div id="project-card">
      {selectedId === "" ? (
        <Card className="rise d1">
          <CardHead title={t(lang, "projects.card.title")} />
          <div className="small muted">{t(lang, "projects.card.none")}</div>
        </Card>
      ) : detail?.status === "loading" ? (
        <Card className="rise d1">
          <CardHead title={t(lang, "projects.card.title")} />
          <div className="small muted">{t(lang, "state.loading")}</div>
        </Card>
      ) : detail?.status === "failed" ? (
        <Card className="rise d1">
          <CardHead title={t(lang, "projects.card.title")} />
          <Refusal lang={lang} failure={detail.failure} onRetry={() => void loadDetail(selectedId)} />
        </Card>
      ) : project !== null ? (
        <ProjectCard
          lang={lang}
          backend={backend}
          presentation={detail?.value?.presentation ?? {}}
          project={project}
          versions={versions}
          files={artifacts}
          filesVersion={filesVersion}
          selectedVersion={selectedVersion}
          onSelectVersion={setSelectedVersion}
          runRecord={runRecord}
          runFailure={runFailure}
          runLoading={runLoading}
          busy={busy}
          onVerify={() =>
            void runAction("verify", () => verifyProject(selectedId), "projects.verify.done")
          }
          onArchive={() =>
            void runAction("archive", () => archiveProject(selectedId), "projects.archive.done")
          }
          onRestore={() =>
            void runAction("restore", () => restoreProject(selectedId), "projects.restore.done")
          }
          onHistory={() => void openHistory()}
          change={change}
          onChange={setChange}
          changeError={changeError}
          onNewVersion={() => void submitVersion()}
          staged={staged}
          onDispatch={() => void submitTask()}
          dispatching={dispatching}
          dispatched={dispatched}
          dispatchFailure={dispatchFailure}
          provider={provider}
          onProvider={setProvider}
          model={model}
          onModel={setModel}
          modelChoices={modelChoices}
          writeMode={writeMode}
          onWriteMode={setWriteMode}
          mockSelected={mockSelected}
          actionFailure={actionFailure}
          actionNotice={actionNotice}
          entrypoint={entrypoint}
          preview={preview}
          stage={stageWord}
          verificationCommand={verificationCommand}
          filesCreated={filesCreated}
          previewBusy={previewBusy}
          onPreviewStart={() => void changePreview("start")}
          onPreviewStop={() => void changePreview("stop")}
          run={currentRun}
          runBusy={runBusy}
          onRunStart={(digest) => void runStart(digest)}
          onRunStop={() => void runStop()}
          onRunRestart={(digest) => void runRestart(digest)}
          onDelete={() => void deleteSelected()}
        />
      ) : null}
      </div>

      {/* --------------------------------------------------------------- history */}
      {history !== null ? (
        <Card className="rise d2">
          <CardHead
            title={t(lang, "projects.history.title")}
            hint={t(lang, "projects.history.hint")}
            action={
              <Button size="small" tone="ghost" onClick={() => setHistory(null)}>
                {t(lang, "projects.history.close")}
              </Button>
            }
          />
          {history.status === "loading" ? (
            <div className="small muted">{t(lang, "state.loading")}</div>
          ) : history.status === "failed" ? (
            <Refusal lang={lang} failure={history.failure} onRetry={() => void openHistory()} />
          ) : (history.value.versions ?? []).length === 0 ? (
            <div className="small muted">{t(lang, "projects.history.empty")}</div>
          ) : (
            <table className="table">
              <thead>
                <tr>
                  <th>{t(lang, "projects.col.version")}</th>
                  <th>{t(lang, "projects.col.created")}</th>
                  <th>{t(lang, "projects.col.run")}</th>
                  <th>{t(lang, "projects.col.verify")}</th>
                  <th>{t(lang, "projects.col.accept")}</th>
                  <th>{t(lang, "projects.col.provider")}</th>
                  <th>{t(lang, "projects.col.files")}</th>
                  <th>{t(lang, "projects.col.change")}</th>
                </tr>
              </thead>
              <tbody>
                {(history.value.versions ?? []).map((version) => (
                  <tr key={String(version.version ?? "")}>
                    <td className="mono small">v{text(version.version)}</td>
                    <td className="mono small">{text(version.created_at)}</td>
                    <td className="mono small">{version.pending === true ? t(lang, "projects.version.pending") : text(version.run_id)}</td>
                    <td className="small">{verificationLabel(lang, version.verification)}</td>
                    <td className="small">{acceptanceLabel(lang, version.acceptance)}</td>
                    <td className="small">{`${text(version.provider)} · ${text(version.model)}`}</td>
                    <td className="mono small">{text(version.file_count)}</td>
                    <td className="small" style={{ maxWidth: 280, overflowWrap: "anywhere" }}>
                      {version.change_request && typeof version.change_request.text === "string"
                        ? version.change_request.text
                        : t(lang, "projects.version.first")}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
      ) : null}

      {/* Where a project comes from, stated where the owner looks for a "create" button. */}
      {list.status === "ready" && summaries.length === 0 ? (
        <Card className="rise d1" tight>
          <div className="small muted">{t(lang, "projects.create.via_discovery")}</div>
        </Card>
      ) : null}
    </div>
  );
};
