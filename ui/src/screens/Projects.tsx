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

import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Badge, Button, Card, CardHead, Field, StatusDot } from "../components/Primitives";
import { AliveBar } from "../components/AliveBar";
import { IconRefresh } from "../components/Icons";
import { RouteLine } from "../components/RouteLine";
import { t, type Lang } from "../i18n";
import { stampMs } from "../progress";
import { PROJECT_LIST_PAGE_SIZE, projectListRows } from "../project-list.js";
import {
  createProjectRouter,
  projectHash,
  projectView,
  type ProjectRouter,
  type ProjectView,
} from "../project-navigation.js";
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
  launchProject,
  loadRunRecord,
  isTerminalRecord,
  moveProjectToVisibleFolder,
  openProject as openProjectTarget,
  restartProjectRun,
  restoreDeletedProject,
  restoreProject,
  startProjectRun,
  stopProjectRun,
  verificationLabel,
  verifyProject,
  uploadProjectAttachment,
  type DispatchResult,
  type Failure,
  type LifecycleArtifact,
  type LifecycleDeletedProject,
  type LifecycleDetail,
  type LifecycleEntryPoint,
  type LifecycleFolder,
  type LifecycleHistory,
  type LifecycleLaunch,
  type LifecycleOpen,
  type LifecyclePreview,
  type LifecycleProject,
  type LifecycleRun,
  type LifecycleSummary,
  type LifecycleVersion,
  type ProjectPresentation,
  type RunRecord,
} from "../api/forge";
// THE ONE ACTION'S RULES live in their own module: the in-flight predicate, the
// synchronous double-click guard and the plain-language refusal classification. They
// import nothing, so `tests/test_flow_invariants.py` executes them directly from Node.
import {
  changeInFlight,
  classifyChangeRefusal,
  createChangeGuard,
  refusalDetail,
} from "../change-flight";
// THE CHANGE BLOCK'S HONEST STATES: the mock warning, the one-press-then-a-different-
// button rule, the zero-file retry and the «Что сделал Forge» block. They import
// nothing, so `tests/test_flow_invariants.py` executes them directly from Node.
import {
  bestRealProvider,
  changeControls,
  effectiveProvider,
  isMockProvider,
  versionFilesWord,
  whatForgeDid,
  type ChangeControls,
  type ForgeDid,
} from "../change-state";
// THE SCREENSHOT THE OWNER ATTACHES TO A CHANGE. What counts as an image on the client,
// how large, how many, and how the bytes reach the API. The image is an artefact for the
// owner: the model is told in words that it cannot see it (see the panel's own hint).
import {
  IMAGE_ACCEPT,
  MAX_ATTACHMENTS_PER_CHANGE,
  MAX_ATTACHMENT_BYTES,
  dragHasFiles,
  imageFilesFrom,
  isImageFile,
  readFileAsBase64,
  type ChangeAttachment,
} from "../change-attachments";
// THE REPAIR PANEL: what a failed launch means in one plain sentence, the redacted log tail,
// and the two ways to fix it («Исправить автоматически» and the one-line chat). The rules
// import nothing, so `tests/test_self_repair.py` executes them directly from Node.
import {
  composeRepairInstruction,
  driveRepair,
  initialRepairState,
  repairPanelModel,
  type LaunchFacts,
  type RepairState,
} from "../repair";
import { RepairPanel } from "../components/RepairPanel";

const text = (value: unknown, fallback = "—"): string =>
  value === undefined || value === null || value === "" ? fallback : String(value);

/**
 * How many trash rows the «Удалённые проекты» section draws at a time.
 *
 * A real trash held 42 entries and the section rendered all of them in one table. The
 * bound is a rendering decision only: the count in the heading stays the real count,
 * «Показать ещё» reveals the next page, and the backend answer is still the full
 * listing (the endpoint also accepts additive `limit`/`offset` for a caller that wants
 * to page server-side).
 */
const TRASH_PAGE_SIZE = 20;

const bytesLabel = (size: unknown): string => {
  const value = typeof size === "number" && Number.isFinite(size) ? size : null;
  if (value === null) return "—";
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KiB`;
  return `${(value / (1024 * 1024)).toFixed(2)} MiB`;
};

/**
 * The sentence a successful launch prints: the URL that is now open in its own window,
 * plus the backend's own note when it had to fall back to the project's static page.
 *
 * The note is never dropped: a fallback means the process failed, and the owner has to
 * learn that from the same block that says a window opened.
 */
const launchSentence = (lang: Lang, value: LifecycleLaunch): string => {
  const url = String(value.url ?? "");
  const extra = String(value.notice ?? "");
  const base = t(lang, "projects.launch.success").replace("{url}", url);
  // A fallback is a second fact ("the process died, here is the page instead"), so it is
  // printed on its own line and never glued onto the URL.
  return extra === "" ? base : `${base}\n${extra}`;
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
 * What one change run did to the project, read from the record *after* the run.
 *
 * The honest rule the card applies: a version exists only when `toVersion` is
 * greater than `fromVersion`. `filesCreated` explains why it is not (a run that
 * wrote no file does not advance the project), and `attached` says whether the Core
 * recorded the run against this project at all.
 */
type ChangeOutcome = {
  runId: string;
  filesCreated: number;
  attached: boolean;
  fromVersion: number;
  toVersion: number;
  /**
   * The version the backend **staged** for this change (the number the press created or
   * re-targeted). It is not `current_version`: a change that wrote no file leaves the
   * current version alone, and the strip must still name the record it made (measured:
   * «файлов 0 — код не менялся (v1 …)» for a change recorded as v2).
   */
  stagedVersion: number;
  /**
   * What the new version really changed, read from its own manifest after the run:
   * a path, whether it was created or updated, and its size. Empty when the run wrote
   * nothing, which is exactly the case the card must not dress up as a new version.
   */
  files: LifecycleArtifact[];
  /**
   * True when the backend refused to record a second version for the same words:
   * the change is already in the project, so no run was dispatched and nothing was
   * created. It is an answer, not a failure, and the card says exactly that.
   */
  alreadyApplied?: boolean;
};

/**
 * THE STATUS STRIP: "did the run start?", answered in one glance.
 *
 * Measured complaint: the owner pressed one button, then the other, and could not tell
 * whether anything had begun. The strip is built from two facts the backend already
 * publishes -- the run id the dispatch returned and the run record polled while it is not
 * terminal -- and it never invents a stage:
 *
 * * `title` names the run (`Запущено: run-…`) and the stage word the record proves;
 * * `stage` is that word alone, so the live line can pulse on its own;
 * * `final` is the honest end: «Готово: v3 записана» only when the project's own
 *   `current_version` really moved, otherwise the backend's sentence;
 * * `refusal` is the **plain-language** reason, and `refusalRule` the safe rule id the
 *   context check published; `refusalDetail` keeps the backend's own sentence verbatim
 *   under «Технические детали». A refusal the owner cannot read is the same silent no-op
 *   as a button that does nothing.
 */
type ChangeStrip = {
  title: string;
  stage: string;
  tone: "info" | "ok" | "warn";
  final: string;
  refusal: string;
  refusalRule: string;
  refusalDetail: string;
  files: LifecycleArtifact[];
};

/**
 * How a project file is named in «Что изменилось»: the backend's own `create`/`update`
 * word, translated, so the card never invents a third vocabulary for the same fact.
 */
const changeWord = (lang: Lang, change: unknown): string => {
  const word = String(change ?? "");
  if (word === "create") return lang === "ru" ? "создан" : "created";
  if (word === "update") return lang === "ru" ? "обновлён" : "updated";
  return text(word);
};

// «Открыть» is a **navigation**, not a selection.
//
// The measured complaint (twice): the owner pressed «Открыть» on the «Почтовый центр»
// row and nothing happened. The card used to render *below* the list and the click only
// selected an id and scrolled towards it, which on a list that fills the window is
// indistinguishable from an inert button. The project screen therefore has its own
// address -- `#/projects/<id>`, resolved by `ui/src/project-navigation.js` -- and it
// **replaces** the list: the browser's Back returns to the list, a reload with the same
// hash lands on the same project, and the screen changes within the same frame as the
// click. Nothing below scrolls the owner anywhere: the detail view starts at the top.


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
              : code === "project_launch_refused"
                ? t(lang, "projects.err.launch")
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

/**
 * The launch refusal's own facts: process state, exit code, the redacted log tail, and
 * the explicit offer of the project's saved page.
 *
 * A portless launch is never dressed as a success, and the saved page is a separate
 * click — the card must not silently replace the run the owner asked for.
 */
const LaunchFacts = ({
  lang,
  failure,
  onFallback,
  busy,
}: {
  lang: Lang;
  failure: Failure;
  onFallback: () => void;
  busy: boolean;
}) => {
  const fields = failure.fields ?? {};
  const runState = text(fields.run_state, "");
  const exitCode = fields.exit_code;
  const logTail = Array.isArray(fields.log_tail)
    ? fields.log_tail.map((line) => String(line)).filter((line) => line.trim() !== "")
    : [];
  const fallbackAvailable =
    fields.fallback_available === true &&
    typeof fields.fallback_url === "string" &&
    fields.fallback_url !== "";
  const waiting = fields.waiting_for_input === true;
  if (runState === "" && (exitCode === null || exitCode === undefined) && logTail.length === 0 && !fallbackAvailable) {
    return null;
  }
  return (
    <div style={{ marginTop: 8 }} data-testid="project-launch-facts">
      <div className="row" style={{ gap: 14, flexWrap: "wrap" }}>
        <span className="small muted">
          {t(lang, "projects.launch.facts.state")}:{" "}
          <span className="mono">{text(runState)}</span>
        </span>
        <span className="small muted">
          {t(lang, "projects.launch.facts.exit")}:{" "}
          <span className="mono">
            {exitCode === null || exitCode === undefined ? "—" : String(exitCode)}
          </span>
        </span>
      </div>
      {waiting ? (
        <div className="small" style={{ marginTop: 6 }}>
          {t(lang, "projects.launch.facts.input")}
        </div>
      ) : null}
      {logTail.length > 0 ? (
        <div style={{ marginTop: 6 }}>
          <div className="label">{t(lang, "projects.launch.facts.log")}</div>
          <pre
            className="small mono"
            style={{ whiteSpace: "pre-wrap", wordBreak: "break-word", margin: 0 }}
          >
            {logTail.join("\n")}
          </pre>
        </div>
      ) : null}
      {fallbackAvailable ? (
        <div
          className="row"
          style={{ gap: 8, flexWrap: "wrap", alignItems: "center", marginTop: 8 }}
        >
          <span data-testid="project-launch-fallback">
            <Button size="small" disabled={busy} onClick={onFallback}>
              {t(lang, "projects.launch.fallback.action")}
            </Button>
          </span>
          <span className="small muted">{t(lang, "projects.launch.fallback.hint")}</span>
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
 *
 * A **static page** is launched by the existing read-only preview route: it has no
 * argv and no digest to approve, so its confirmation prints the fixed sentence and
 * the accepted button calls `onPreviewStart` — the same route the preview block
 * uses. Refusing the page here ("no run command: nothing to start") is the measured
 * defect this wiring removes; the runner's own refusal stays for API callers that
 * insist on a process.
 */
const RunPanel = ({
  lang,
  run,
  entrypoint,
  preview,
  busy,
  onStart,
  onPreviewStart,
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
  onPreviewStart: () => void;
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
  // A static page is launched by the preview route: it has no argv and no digest.
  const staticEntry =
    entrypoint !== null && String(entrypoint.serve_kind ?? "") === "static";
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

  // A static entry needs no digest: the preview route serves the page and answers a
  // URL. It is also already launched while its preview runs, so the button stays off
  // then (the preview block offers «Открыть приложение» instead). A process entry
  // still requires the digest the backend will compare.
  const canStart =
    runnable &&
    !live &&
    state !== "STARTING" &&
    (staticEntry
      ? preview.available === true && preview.running !== true
      : digest !== "");
  const canRestart = !staticEntry && digest !== "" && !live && state !== "NOT_RUN";

  // The containment facts. They are read from the backend and never re-derived here:
  // `job_object`/`restricted_token` are what the process really has, `writes_outside`
  // is a measured probe result, and `network` is always inherited.
  const sandbox = run.sandbox ?? {};
  const sandboxLimits = sandbox.limits ?? {};
  const sandboxDetail = (sandbox.detail ?? {}) as Record<string, unknown>;
  const sandboxJob = sandbox.job_object === true;
  const sandboxToken = sandbox.restricted_token === true;
  const sandboxWrites = String(sandbox.writes_outside ?? "unknown");
  const sandboxMemory =
    typeof sandboxLimits.memory_mb === "number" ? sandboxLimits.memory_mb : 0;
  const sandboxProcesses =
    typeof sandboxLimits.active_process_limit === "number"
      ? sandboxLimits.active_process_limit
      : 0;
  const sandboxLifetime =
    typeof sandboxDetail.server_lifetime_seconds === "number"
      ? sandboxDetail.server_lifetime_seconds
      : 0;
  // The only state that may be shown as contained: every layer active and the write
  // boundary measured as blocked. Anything less is reported as it is.
  const sandboxContained = sandboxJob && sandboxToken && sandboxWrites === "blocked";

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

      {/* «Песочница»: exactly what the layer does, in measured facts. A layer that is
          not active is never shown as a success, and a write boundary that is not
          blocked is a warning -- the program runs with the owner's rights. */}
      <div style={{ marginTop: 12 }}>
        <div className="label">{t(lang, "projects.run.sandbox.title")}</div>
        <div style={{ marginTop: 6 }}>
          <Facts
            items={[
              [
                t(lang, "projects.run.sandbox.job"),
                sandboxJob
                  ? t(lang, "projects.run.sandbox.job.on")
                  : t(lang, "projects.run.sandbox.job.off"),
              ],
              [
                t(lang, "projects.run.sandbox.token"),
                sandboxToken
                  ? t(lang, "projects.run.sandbox.token.low")
                  : t(lang, "projects.run.sandbox.token.none"),
              ],
              [
                t(lang, "projects.run.sandbox.writes"),
                sandboxWrites === "blocked"
                  ? t(lang, "projects.run.sandbox.writes.blocked")
                  : sandboxWrites === "allowed"
                    ? t(lang, "projects.run.sandbox.writes.allowed")
                    : t(lang, "projects.run.sandbox.writes.unknown"),
              ],
              [t(lang, "projects.run.network"), t(lang, "projects.run.network.inherited")],
              [
                t(lang, "projects.run.sandbox.memory"),
                sandboxMemory > 0
                  ? `${sandboxMemory} ${t(lang, "projects.run.sandbox.mb")}`
                  : t(lang, "projects.run.sandbox.not.set"),
              ],
              [
                t(lang, "projects.run.sandbox.processes"),
                sandboxProcesses > 0
                  ? String(sandboxProcesses)
                  : t(lang, "projects.run.sandbox.not.set"),
              ],
              [
                t(lang, "projects.run.sandbox.lifetime"),
                sandboxLifetime > 0
                  ? `${sandboxLifetime} ${t(lang, "projects.run.sandbox.seconds")}`
                  : t(lang, "projects.run.sandbox.lifetime.none"),
              ],
            ]}
          />
        </div>
        {sandboxContained ? (
          <div style={{ marginTop: 6 }}>
            <Badge tone="ok">{t(lang, "projects.run.sandbox.contained")}</Badge>
          </div>
        ) : null}
        {sandboxWrites !== "blocked" ? (
          <div className="notice warn" style={{ marginTop: 8 }}>
            <div className="label">{t(lang, "projects.run.sandbox.warning.title")}</div>
            <div className="small" style={{ marginTop: 4 }}>
              {t(lang, "projects.run.sandbox.warning.body")}
            </div>
          </div>
        ) : null}
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
          {/* A static page has no argv to approve, so there is no digest to show:
              the preview route is what the accepted button calls. */}
          {staticEntry ? null : (
            <div className="small mono" style={{ marginTop: 4, wordBreak: "break-word" }}>
              {t(lang, "projects.run.confirm.digest")}: {digest}
            </div>
          )}
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
                else if (staticEntry) onPreviewStart();
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
  attachments,
  attachmentBusy,
  attachmentError,
  onPickAttachments,
  onRemoveAttachment,
  onStartChange,
  changeFailure,
  changeLocked,
  changeStrip,
  changeStartedAt,
  changeEndedAt,
  dispatched,
  dispatchFailure,
  provider,
  onProvider,
  model,
  onModel,
  modelChoices,
  mockSelected,
  controls,
  forgeDid,
  onRearmChange,
  onRetryWithRealModel,
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
  onLaunch,
  onLaunchFallback,
  launchBusy,
  launchNotice,
  launchFailure,
  repairFacts,
  repairAttempts,
  repairState,
  repairChat,
  onRepairChat,
  onRepairSend,
  onRepairAuto,
  repairBusy,
  onDelete,
  open,
  folder,
  onOpenTarget,
  openingTarget,
  diskNotice,
  diskFailure,
  onMoveToVisible,
  movingToVisible,
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
  /**
   * THE SCREENSHOTS OF THIS CHANGE, already kept with the project.
   *
   * The image is an artefact the owner and Forge keep; it is **not** model input. The
   * panel therefore asks for the error text in one line and the task text says, in words,
   * that the model cannot see the picture.
   */
  attachments: ChangeAttachment[];
  /** True while one screenshot is travelling to the backend. */
  attachmentBusy: boolean;
  /** Why the last screenshot was not attached (the backend's own sentence). */
  attachmentError: string;
  /** Files from the picker, a paste or a drop; each one is uploaded immediately. */
  onPickAttachments: (files: File[]) => void;
  /** «×» on one chip: the file stays on disk, it just stops being part of this change. */
  onRemoveAttachment: (path: string) => void;
  /** THE one action of this screen: stage the change and run it. */
  onStartChange: () => void;
  /**
   * The backend's refusal of this change request, printed beside the button that made it.
   *
   * It is its own slot (not the card's shared `actionFailure`) because the refusal belongs
   * to the control the owner pressed: «Запрос отклонён» at the top of a long card was the
   * same silent no-op the button itself used to be.
   */
  changeFailure: Failure | null;
  /**
   * True while a change is **in flight**: from the click until the run reaches a terminal
   * state or a refusal comes back. It keeps the one action off, so a second press cannot
   * create a second version (measured: two versions at zero files from one double press).
   */
  changeLocked: boolean;
  /**
   * THE STATUS STRIP: what the click started, in the owner's words, printed directly above
   * the button. `null` before the first click -- a strip that says "nothing happened yet"
   * would be another block to read.
   */
  changeStrip: ChangeStrip | null;
  /**
   * The press that started this change, in epoch milliseconds, and the run record's own end.
   *
   * They feed the same alive bar the Кузница rail uses: the clock starts at the press (the
   * synchronous dispatch holds no run id while the Core works) and is bounded by the record's
   * `updated_at`, so a change that used to look frozen for thirty seconds now visibly ticks.
   */
  changeStartedAt: number;
  changeEndedAt: number;
  dispatched: DispatchResult | null;
  dispatchFailure: Failure | null;
  provider: string;
  onProvider: (value: string) => void;
  model: string;
  onModel: (value: string) => void;
  modelChoices: string[];
  mockSelected: boolean;
  /**
   * THE BLOCK'S CONTROLS, decided by `ui/src/change-state.ts` and nothing else: which
   * action the primary button performs, whether the form is frozen, whether the mock
   * warning is printed and whether «Внести ещё изменения» is offered.
   */
  controls: ChangeControls;
  /** «Что сделал Forge», from the model's own answer and the files it really wrote. */
  forgeDid: ForgeDid;
  /** «Внести ещё изменения»: the only way the form comes back after a change. */
  onRearmChange: () => void;
  /** «Повторить с реальной моделью»: switch to the best non-mock provider and re-run. */
  onRetryWithRealModel: () => void;
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
   * «Запустить приложение»: the one obvious button.
   *
   * It runs the project and opens **the result** in its own window; the second click
   * reuses the running server and only opens the window again. It is not the same
   * meaning as the old «Открыть проект», which only showed this card: that action is
   * now called «Подробнее».
   */
  onLaunch: () => void;
  /**
   * The explicit second action a refused launch offers: show the project's saved static
   * page because the process never became a server. It is never automatic.
   */
  onLaunchFallback: () => void;
  launchBusy: boolean;
  /** The sentence a successful launch printed: the URL that is now open. */
  launchNotice: string;
  /** The backend's refusal verbatim, when the launch could not show anything. */
  launchFailure: Failure | null;
  /**
   * THE REPAIR PANEL's facts: the refusal's structured fields (its redacted log tail, the
   * exit code, whether the program waited for input) and the project-launcher log the
   * backend also read. `null` means "no panel": either nothing failed yet, or the refusal
   * carried no run facts to explain (an approval refusal keeps its plain sentence).
   */
  repairFacts: LaunchFacts | null;
  /** Automatic repair attempts already spent on this project in this session. */
  repairAttempts: number;
  /** The repair loop's last answer, from `ui/src/repair.ts`. */
  repairState: RepairState | null;
  repairChat: string;
  onRepairChat: (value: string) => void;
  onRepairSend: () => void;
  onRepairAuto: () => void;
  repairBusy: boolean;
  /**
   * Delete this project: the parent asks the backend, which *moves* the folder into
   * `.forge/projects-deleted`. The click is refused until it is confirmed inside the card,
   * so a single accidental press cannot move anything.
   */
  onDelete: () => void;
  /** What can really be opened, per target, and why not when it cannot. */
  open: LifecycleOpen;
  /** The project's folder as the backend publishes it, plus whether it is there now. */
  folder: LifecycleFolder;
  /** Really open the project: folder, VS Code, or a terminal at it. */
  onOpenTarget: (target: "folder" | "editor" | "terminal") => void;
  openingTarget: string;
  diskNotice: string;
  diskFailure: Failure | null;
  /** Deliver an existing project's files into the configured visible folder. */
  onMoveToVisible: () => void;
  movingToVisible: boolean;
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

  // ------------------------------------------------------------- the entrance
  // Every word here is the backend's: which target is available, and the sentence that
  // says why it is not. The interface computes nothing about the machine -- a folder
  // that is gone or a project with no files is refused by the backend, and the buttons
  // here only reflect that answer.
  const openTargets = open.targets ?? {};
  const folderPath = text(folder.path ?? project.project_path, t(lang, "projects.facts.none"));
  const diskFiles = typeof folder.files === "number" ? folder.files : Number(open.files ?? 0);

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
  // The command the entry point really runs, on the card's own first screen: a program
  // is shown as the command that starts it, and a static page says in words that no
  // server is required. It is the backend's `run_command`, never composed here.
  const entryRunCommand =
    entrypoint !== null && String(entrypoint.run_command ?? "") !== ""
      ? String(entrypoint.run_command)
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
  /* THE SCREENSHOT INPUT: one hidden <input type="file"> opened by the visible button, and
     a flag that lights the field up while a file is dragged over it. */
  const attachmentInputRef = useRef<HTMLInputElement | null>(null);
  const [attachmentDrag, setAttachmentDrag] = useState(false);
  // The primary action of this screen is gated by the text it acts on, and the reason it
  // is off is printed as a sentence: the owner's words were «где кнопка, которая
  // подтверждает ввод?» -- a control that looks like a caption cannot answer that, and a
  // disabled control without a visible reason only moves the question one step further.
  const changeEmpty = change.trim() === "";
  // THE ONE ACTION IS OFF FOR THE WHOLE RUN, not only while the request travels: `busy`
  // covers the dispatch, `changeLocked` covers the run it started (see change-flight.ts).
  const changeBusy = busy === "version" || changeLocked;
  // «Проверить» is offered in the compact row only when the backend's own facts make it
  // applicable, and the reason it is off is a sentence, not a hover-only tooltip.
  const verifyBlocked = busy === "verify" || archived || fileTotal === 0 || !hasRun;
  const verifyBlockedReason = archived
    ? t(lang, "projects.badge.archived")
    : fileTotal === 0
      ? t(lang, "projects.empty.title")
      : !hasRun
        ? t(lang, "projects.verify.hint")
        : undefined;

  // One handler for the whole action vocabulary: the card offers what the backend
  // allows and nothing else, so an unknown id can only be a client/server mismatch.
  const runAction = (id: string): void => {
    setLaunchNote("");
    if (id === "reverify") {
      onVerify();
      return;
    }
    if (id === "new_version") {
      // The one change action lives in the change block above; this vocabulary entry
      // moves the cursor there instead of opening a second way to start the same thing.
      changeRef.current?.focus();
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

  /* THE CORRECTION LOOP, as a value.
   *
   * It is defined here and rendered near the top of the card (right under the
   * folder and its open buttons), because «Что поправить?» is the default way to
   * edit a project: one textarea, one button, no terminal. Keeping the JSX in one
   * place while moving where it appears is what lets the same handlers serve it.
   */
  /* THE CHANGE FLIGHT, AS ONE VALUE.
   *
   * The status strip, the outcome and the live bar are the *one* report of a change run
   * in this card. They are built here, from the facts above, and rendered twice: inside
   * «Что поправить?» and inside the repair panel under the launch button
   * (`ui/src/components/RepairPanel.tsx`), which receives this very element as its
   * `children`. That is what makes a change dispatched from the panel indistinguishable
   * from one dispatched from the block: same strip, same words, same run state -- one
   * flow, not two. */
  const changeFlightBlock = (
    <>
            {changeStrip !== null ? (
              <div
                className="notice"
                data-testid="change-status"
                aria-live="polite"
                style={
                  changeStrip.tone === "warn"
                    ? { borderColor: "var(--warn-text)" }
                    : undefined
                }
              >
                <div
                  className="mono small"
                  data-testid="change-status-line"
                  style={{ overflowWrap: "anywhere" }}
                >
                  {changeStrip.title}
                </div>
                <div className="small muted" style={{ marginTop: 4 }} data-testid="change-stage-line">
                  {changeStrip.stage}
                </div>
                {changeStrip.final !== "" ? (
                  <div data-testid="change-outcome" style={{ marginTop: 8 }}>
                    <Badge tone={changeStrip.tone === "ok" ? "ok" : "warn"}>
                      {t(lang, "projects.newversion.outcome.title")}
                    </Badge>
                    <div style={{ marginTop: 6 }}>{changeStrip.final}</div>
                    {/* THE REFUSAL, in words the owner can act on. The context check's own
                        sentence («Critical Tier 1 context item … Reason:
                        security_forbidden_content:…») is a diagnostic, not an answer, so
                        the strip prints the plain reason plus the rule id and keeps the
                        raw line folded under «Технические детали». */}
                    {changeStrip.refusal !== "" ? (
                      <div style={{ marginTop: 6 }} data-testid="change-refusal">
                        <div className="small" style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>
                          {changeStrip.refusal}
                          {changeStrip.refusalRule !== "" ? (
                            <span className="mono"> · rule={changeStrip.refusalRule}</span>
                          ) : null}
                        </div>
                        {changeStrip.refusalDetail !== "" ? (
                          <details className="tech-details" style={{ marginTop: 6 }}>
                            <summary>{t(lang, "projects.change.technical")}</summary>
                            <div
                              className="small mono"
                              data-testid="change-refusal-detail"
                              style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}
                            >
                              {changeStrip.refusalDetail}
                            </div>
                          </details>
                        ) : null}
                      </div>
                    ) : null}
                    {/* WHAT CHANGED, from the new version's own manifest. A run that
                        wrote nothing has no entry here and the strip says exactly that,
                        instead of showing an empty list that reads as "nothing to report". */}
                    <div className="small" style={{ marginTop: 8 }} data-testid="change-files">
                      <div className="label">{t(lang, "projects.newversion.changed")}</div>
                      {changeStrip.files.length === 0 ? (
                        <div className="muted" style={{ marginTop: 4 }}>
                          {t(lang, "projects.newversion.changed.none")}
                        </div>
                      ) : (
                        <div className="stack" style={{ gap: 2, marginTop: 4 }}>
                          {changeStrip.files.map((item) => (
                            <div
                              className="mono small"
                              key={String(item.path ?? "")}
                              style={{ overflowWrap: "anywhere" }}
                            >
                              {t(lang, "projects.newversion.changed.line")
                                .replace("{path}", text(item.path))
                                .replace("{change}", changeWord(lang, item.change))
                                .replace("{size}", bytesLabel(item.size))}
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                    {/* «ЧТО СДЕЛАЛ FORGE», from the model's own answer. The summary is
                        the model's `model_intent.summary`; the files are the ones it
                        really wrote, with sizes; when it returned no file the block says
                        so and quotes the first two lines of its answer -- screened by the
                        backend. Nothing here is advice, and nothing is composed. */}
                    <div className="small" style={{ marginTop: 10 }} data-testid="change-what-forge-did">
                      <div className="label">{t(lang, "projects.change.did.title")}</div>
                      {forgeDid.summary !== "" ? (
                        <div style={{ marginTop: 4 }} data-testid="change-did-summary">
                          {forgeDid.summary}
                        </div>
                      ) : null}
                      {forgeDid.noFiles ? (
                        <div className="muted" style={{ marginTop: 4 }} data-testid="change-did-no-files">
                          {t(lang, "projects.change.did.no_files")}
                        </div>
                      ) : (
                        <div className="stack" style={{ gap: 2, marginTop: 4 }}>
                          {forgeDid.files.map((item) => (
                            <div
                              className="mono small"
                              key={`did-${item.path}`}
                              style={{ overflowWrap: "anywhere" }}
                            >
                              {t(lang, "projects.newversion.changed.line")
                                .replace("{path}", item.path)
                                .replace("{change}", changeWord(lang, item.change))
                                .replace("{size}", bytesLabel(item.size))}
                            </div>
                          ))}
                        </div>
                      )}
                      {forgeDid.noFiles && forgeDid.answerLines.length > 0 ? (
                        <div
                          className="small"
                          style={{ marginTop: 6, whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}
                          data-testid="change-did-answer"
                        >
                          <span className="muted">
                            {t(lang, "projects.change.did.answer")}{": "}
                          </span>
                          {forgeDid.answerLines.join("\n")}
                        </div>
                      ) : null}
                    </div>
                  </div>
                ) : null}
              </div>
            ) : null}

            {/* THE SAME ALIVE BAR THE КУЗНИЦА RAIL USES, directly under the strip.
                The measured complaint was a change that sat for thirty seconds with nothing
                moving: the strip already named the run, but a still line reads as a hang. The
                bar ticks from the press, names the stage and keeps a ribbon moving while the
                dispatch is really in flight; once the change is recorded it stops and prints
                «Готово за …» (or the honest failure). It only appears when this press really
                dispatched a run or is still waiting for one -- an `already_applied` answer is
                not a run and must not be dressed as one. */}
            {changeBusy || dispatched !== null ? (
              <AliveBar
                lang={lang}
                inFlight={changeBusy}
                terminal={!changeBusy && changeStrip !== null && changeStrip.final !== ""}
                success={changeStrip?.tone === "ok"}
                observedAt={changeStartedAt}
                endedAt={changeEndedAt}
                stage={
                  changeBusy
                    ? t(lang, "projects.change.working")
                    : (changeStrip?.stage ?? t(lang, "projects.change.working"))
                }
                testId="change-alive"
              />
            ) : null}
    </>
  );

  const changeBlock = (
    <>
        {/* ----------------------------------------------------------- new version */}
        <div data-testid="project-change">
          <div className="label">{t(lang, "projects.newversion.title")}</div>
          <div className="stack" style={{ gap: 10, marginTop: 8 }}>
            {/* THE FIELD IS ALSO THE DROP ZONE, and a paste into it is a screenshot when
                the clipboard holds one. Both are the same action as the button: the file
                travels to `POST /api/projects/{id}/attachments` and comes back as a chip
                naming the project-relative path the change text will mention. */}
            <div
              className={`change-attach-zone${attachmentDrag ? " change-attach-zone--over" : ""}`}
              data-testid="change-drop"
              onDragOver={(event) => {
                if (!dragHasFiles(event.dataTransfer)) return;
                event.preventDefault();
                if (!controls.formFrozen) setAttachmentDrag(true);
              }}
              onDragLeave={() => setAttachmentDrag(false)}
              onDrop={(event) => {
                setAttachmentDrag(false);
                const files = imageFilesFrom(event.dataTransfer);
                if (files.length === 0) return;
                event.preventDefault();
                if (!controls.formFrozen) onPickAttachments(files);
              }}
            >
              <Field
                label={t(lang, "projects.newversion.field")}
                hint={t(lang, "projects.newversion.hint")}
                error={changeError}
              >
                <textarea
                  className="input"
                  rows={3}
                  ref={changeRef}
                  value={change}
                  onChange={(event) => onChange(event.target.value)}
                  /* A PASTED SCREENSHOT BECOMES AN ATTACHMENT. A pasted sentence stays a
                     sentence: only a clipboard that really holds an image is intercepted. */
                  onPaste={(event) => {
                    const files = imageFilesFrom(event.clipboardData);
                    if (files.length === 0) return;
                    event.preventDefault();
                    if (!controls.formFrozen) onPickAttachments(files);
                  }}
                  placeholder={t(lang, "projects.newversion.placeholder")}
                  data-testid="change-textarea"
                  /* ONE PRESS, THEN A DIFFERENT BUTTON: after a change the words it was
                     made of are frozen on screen until the owner asks for another change,
                     so the same sentence cannot be sent twice by a second press. */
                  readOnly={controls.formFrozen}
                  disabled={controls.formFrozen}
                />
              </Field>
              <div className="row between change-attach-row" data-testid="change-attach">
                <span>
                  <Button
                    size="small"
                    tone="ghost"
                    disabled={controls.formFrozen || attachmentBusy}
                    disabledReason={
                      controls.formFrozen
                        ? t(lang, "projects.change.again")
                        : attachmentBusy
                          ? t(lang, "projects.attach.uploading")
                          : undefined
                    }
                    onClick={() => attachmentInputRef.current?.click()}
                  >
                    {attachmentBusy
                      ? t(lang, "projects.attach.uploading")
                      : t(lang, "projects.attach.button")}
                  </Button>
                </span>
                <span className="small muted">{t(lang, "projects.attach.drop")}</span>
              </div>
              <input
                ref={attachmentInputRef}
                type="file"
                accept={IMAGE_ACCEPT}
                multiple
                hidden
                data-testid="attachment-input"
                onChange={(event) => {
                  const chosen = Array.from(event.target.files ?? []).filter(isImageFile);
                  /* The same input may offer the same file twice; clearing it makes the
                     next pick of that file an event again. */
                  event.target.value = "";
                  if (chosen.length > 0) onPickAttachments(chosen);
                }}
              />
              {attachments.length > 0 ? (
                <>
                  <div className="attachment-list" data-testid="change-attachments">
                    {attachments.map((item) => (
                      <div className="attachment-chip" data-testid="attachment-chip" key={item.path}>
                        <img className="attachment-thumb" src={item.previewUrl} alt="" />
                        <span className="attachment-name" title={item.path}>
                          {item.name}
                        </span>
                        <button
                          type="button"
                          className="attachment-remove"
                          title={t(lang, "projects.attach.remove")}
                          aria-label={t(lang, "projects.attach.remove")}
                          data-testid="attachment-remove"
                          onClick={() => onRemoveAttachment(item.path)}
                        >
                          ×
                        </button>
                      </div>
                    ))}
                  </div>
                  {/* THE MODEL CANNOT SEE PIXELS, so the panel asks for the text in one
                      line. The image is kept as an artefact for the owner and for Forge. */}
                  <div className="small muted attachment-hint" data-testid="attachment-hint">
                    {t(lang, "projects.attach.hint")}
                  </div>
                </>
              ) : null}
              {attachmentError !== "" ? (
                <div className="notice warn" style={{ marginTop: 8 }} data-testid="attachment-error">
                  {attachmentError}
                </div>
              ) : null}
            </div>
            {/* THE STATUS STRIP, immediately above the one action.
                One glance answers «оно вообще началось?»: the run id the dispatch named,
                the stage word the run record proves, and -- once it ends -- either
                «Готово: vN записана» or the backend's own refusal, verbatim. It appears on
                the click, not after the first refresh. */}
            {changeFlightBlock}

            {/* THE ONE ACTION OF THIS SCREEN, directly under the text it acts on and under
                the strip that reports on it. There used to be TWO primary buttons for one
                change -- «Начать изменения» and «Исправить проект» -- and the owner
                pressed both, unsure whether either had started anything. There is now one
                button, one promise, one state.

                ONE PRESS, THEN A DIFFERENT BUTTON. After a change that wrote files the
                button is off and reads «Изменения внесены (vN)»; the smaller
                «Внести ещё изменения» is the only thing that re-arms the form. After a
                change that wrote NOTHING the button reads «Повторить с реальной моделью»
                and switches the provider in one click -- a zero-file run never looks like
                a success. */}
            <div className="stack" style={{ gap: 8 }} data-testid="change-actions">
              {/* MOCK IS LOUD: printed above the button, before the press, because the
                  offline engine cannot change a single file. */}
              {controls.mockWarning ? (
                <div className="notice warn" data-testid="change-mock-warning">
                  {t(lang, "forge.mock.warning")}
                </div>
              ) : null}
              <span data-testid="change-start">
                <Button
                  tone="primary"
                  wide
                  disabled={controls.primaryDisabled}
                  disabledReason={
                    controls.primary === "start" && changeEmpty
                      ? t(lang, "projects.newversion.start.reason")
                      : controls.primary === "sending"
                        ? t(lang, "projects.newversion.start.busy")
                        : controls.primary === "retry"
                          ? t(lang, "projects.change.retry.none")
                          : undefined
                  }
                  onClick={
                    controls.primary === "retry" ? onRetryWithRealModel : onStartChange
                  }
                >
                  {controls.primary === "sending"
                    ? t(lang, "projects.newversion.sending")
                    : controls.primary === "applied"
                      ? t(lang, "projects.change.done.button").replace(
                          "{version}",
                          `v${controls.primaryVersion}`
                        )
                      : controls.primary === "retry"
                        ? t(lang, "projects.change.retry")
                        : t(lang, "projects.newversion.start")}
                </Button>
              </span>
              {/* THE SMALLER SECOND ACTION: the form comes back only when the owner says
                  so, so a second press cannot repeat the sentence just sent. */}
              {controls.secondaryVisible ? (
                <span data-testid="change-again">
                  <Button size="small" tone="ghost" onClick={onRearmChange}>
                    {t(lang, "projects.change.again")}
                  </Button>
                </span>
              ) : null}
              {/* WHILE THE RUN IS IN FLIGHT the button stays off and the reason is printed
                  beside it, so a second press cannot create a second version. */}
              {changeBusy ? (
                <div className="small muted" data-testid="change-start-busy">
                  {t(lang, "projects.newversion.start.busy")}
                </div>
              ) : null}
              {controls.primary === "start" && changeEmpty && !changeBusy ? (
                <div className="small muted" data-testid="change-start-reason">
                  {t(lang, "projects.newversion.start.reason")}
                </div>
              ) : null}
              {/* The backend's refusal, verbatim, next to the control that produced it. */}
              {changeFailure !== null ? <Refusal lang={lang} failure={changeFailure} /> : null}
              {dispatchFailure !== null ? <Refusal lang={lang} failure={dispatchFailure} /> : null}
            </div>
          </div>
        </div>

    </>
  );

  /* The provider and the model a change runs on. They are real controls, so they must stay
   * reachable -- but they are not what the owner reads to press one button, so they live in
   * the collapsed «Показать подробности» block instead of above the action. */
  const changeOptions = (
    <div className="stack" style={{ gap: 10 }} data-testid="change-options">
      <div className="row" style={{ gap: 12, flexWrap: "wrap" }}>
        <label className="field" style={{ flex: "0 1 190px" }}>
          <span className="label">{t(lang, "projects.col.provider")}</span>
          <select
            className="input"
            value={provider}
            onChange={(event) => onProvider(event.target.value)}
            disabled={backend.status !== "live" || controls.formFrozen}
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
            <select
              className="input"
              value={model}
              onChange={(event) => onModel(event.target.value)}
              disabled={controls.formFrozen}
            >
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
              disabled={controls.formFrozen}
            />
          )}
        </label>
      </div>
      {mockSelected ? <div className="notice">{t(lang, "forge.discovery.mock.note")}</div> : null}
      {dispatched !== null ? (
        <div className="stack" style={{ gap: 6 }}>
          {/* The project card publishes the same «Провайдер · Модель · тариф · почему» the run
              response carries: what really ran, and the reasons the route was chosen. */}
          <RouteLine lang={lang} choice={dispatched.response.model_choice} testId="project-route-line" />
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
              [
                t(lang, "runs.result.tokens"),
                dispatched.response.tokens === null || dispatched.response.tokens === undefined
                  ? t(lang, "runs.unknown")
                  : String(dispatched.response.tokens),
              ],
            ]}
          />
        </div>
      ) : null}
    </div>
  );

  /* THE DEFAULT READING, and nothing else: the change block above, the project's folder,
   * one row of secondary actions, and the versions. Everything a reader only sometimes
   * needs (launch, preview, the local run, the save facts, the facts, the specification,
   * the artifact table, the backend's action set and the technical details) sits inside
   * «Показать подробности» below -- reachable, never in the way. Measured complaint: the
   * card was so tall that the owner could not tell which of its buttons was the one that
   * changes the project. */
  const diskVisible = (
    <div data-testid="project-disk">
      <div className="label">{t(lang, "projects.disk.title")}</div>
      <div className="row between" style={{ gap: 10, flexWrap: "wrap", marginTop: 4 }}>
        <span className="small mono" style={{ overflowWrap: "anywhere" }} data-testid="project-folder-path">
          {folderPath}
        </span>
        <span className="small muted">
          {t(lang, "projects.disk.files")}: {String(diskFiles)}
        </span>
      </div>
      {diskNotice !== "" ? (
        <div className="notice" style={{ marginTop: 8 }} data-testid="project-disk-notice">
          {diskNotice}
        </div>
      ) : null}
      {diskFailure !== null ? <Refusal lang={lang} failure={diskFailure} /> : null}
    </div>
  );

  /* The three open buttons and the move: real actions, kept reachable in the details. */
  const diskButtons = (
    <div className="stack" style={{ gap: 6 }} data-testid="project-open-buttons">
      <div className="label">{t(lang, "projects.disk.open_folder")}</div>
      <div className="row" style={{ gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        {(["folder", "editor", "terminal"] as const).map((target) => {
          const line = openTargets[target] ?? {};
          const available = line.available === true;
          const label =
            target === "folder"
              ? t(lang, "projects.disk.open_folder")
              : target === "editor"
                ? t(lang, "projects.disk.open_editor")
                : t(lang, "projects.disk.open_terminal");
          return (
            <span key={target} data-testid={`project-open-${target}`}>
              <Button
                size="small"
                tone={target === "folder" ? "primary" : "ghost"}
                disabled={!available || openingTarget !== ""}
                disabledReason={available ? undefined : text(line.reason, t(lang, "projects.facts.none"))}
                onClick={() => onOpenTarget(target)}
              >
                {openingTarget === target ? t(lang, "state.loading") : label}
              </Button>
            </span>
          );
        })}
        <span data-testid="project-move-visible">
          <Button
            size="small"
            tone="ghost"
            disabled={movingToVisible || archived}
            onClick={onMoveToVisible}
          >
            {movingToVisible ? t(lang, "projects.disk.moving") : t(lang, "projects.disk.move")}
          </Button>
        </span>
      </div>
      <div className="stack" style={{ gap: 2, marginTop: 8 }}>
        {(["folder", "editor", "terminal"] as const)
          .filter((target) => (openTargets[target] ?? {}).available !== true)
          .map((target) => (
            <div className="small muted" key={`why-open-${target}`} data-testid={`project-open-why-${target}`}>
              {text((openTargets[target] ?? {}).label, target)}: {text((openTargets[target] ?? {}).reason)}
            </div>
          ))}
      </div>
      {/* The absolute path is deliberately NOT printed here: this block shows the
          root-relative folder beside the buttons, and the host path lives in the
          collapsed «Технические детали» block (presentation.technical.folder_absolute). */}
      <div className="small muted" style={{ marginTop: 6 }}>
        {t(lang, "projects.disk.hint")}
      </div>
    </div>
  );

  /* EVERY EXISTING ACTION, in one compact row of secondary buttons. None of them is the
   * action of the screen any more -- the change button above is -- but none of them was
   * removed either: run, open the folder, verify, archive, restore and delete all stay one
   * click away, in the same place, in the same order. */
  const secondaryRow = (
    <div className="row" style={{ gap: 8, flexWrap: "wrap", alignItems: "center" }} data-testid="project-secondary">
      <span data-testid="project-secondary-open">
        <Button
          size="small"
          tone="ghost"
          disabled={(openTargets.folder ?? {}).available !== true || openingTarget !== ""}
          disabledReason={
            (openTargets.folder ?? {}).available === true
              ? undefined
              : text((openTargets.folder ?? {}).reason, t(lang, "projects.facts.none"))
          }
          onClick={() => onOpenTarget("folder")}
        >
          {t(lang, "projects.disk.open_folder")}
        </Button>
      </span>
      <span data-testid="project-secondary-launch">
        <Button
          size="small"
          tone="ghost"
          disabled={launchBusy || archived || fileTotal === 0}
          disabledReason={
            archived
              ? t(lang, "projects.badge.archived")
              : fileTotal === 0
                ? t(lang, "projects.empty.title")
                : undefined
          }
          onClick={onLaunch}
        >
          {launchBusy ? t(lang, "projects.launch.running") : t(lang, "projects.action.launch")}
        </Button>
      </span>
      <span data-testid="project-secondary-verify">
        <Button
          size="small"
          tone="ghost"
          disabled={busy === "verify" || verifyBlocked}
          disabledReason={verifyBlockedReason}
          onClick={onVerify}
        >
          {busy === "verify" ? t(lang, "projects.verify.running") : t(lang, "projects.preview.reverify")}
        </Button>
      </span>
      <span data-testid="project-secondary-archive">
        <Button
          size="small"
          tone="ghost"
          disabled={busy === "archive"}
          onClick={onArchive}
        >
          {busy === "archive" ? t(lang, "projects.archive.running") : t(lang, "projects.action.archive")}
        </Button>
      </span>
      <span data-testid="project-secondary-restore">
        <Button
          size="small"
          tone="ghost"
          disabled={busy === "restore"}
          onClick={onRestore}
        >
          {busy === "restore" ? t(lang, "projects.restore.running") : t(lang, "projects.action.restore")}
        </Button>
      </span>
      <span data-testid="project-secondary-delete">
        <Button
          size="small"
          tone="danger"
          disabled={!canDelete || busy === "delete"}
          disabledReason={canDelete ? undefined : t(lang, "projects.delete.no_id")}
          onClick={() => setConfirmDelete(true)}
        >
          {busy === "delete" ? t(lang, "projects.delete.running") : t(lang, "projects.action.delete")}
        </Button>
      </span>
      <span className="small muted">{t(lang, "projects.card.secondary")}</span>
    </div>
  );

  /* THE VERSIONS, kept on the card: they are the project's own history and the one place
   * «v2 записана» becomes something the owner can see for himself. */
  const versionsBlock = (
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
                  <td className="mono small">
                    {typeof version.file_count === "number" ? version.file_count : "—"}
                    {/* AN EMPTY VERSION SAYS SO. Measured: v3, v4 and v5 were recorded
                        with zero files and the table only showed "0" — which reads like a
                        count, not like "the code did not change". */}
                    {versionFilesWord(version.file_count) === "empty" ? (
                      <div className="small muted" data-testid="version-empty">
                        {t(lang, "projects.version.empty")}
                      </div>
                    ) : null}
                  </td>
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
  );

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
                [t(lang, "projects.facts.run_command"), entryRunCommand],
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

        {/* THE RECORD'S OWN NOTICES. While a change is in flight they describe the *pending*
            version («Изменения не записаны: новой версии нет») and would contradict the strip
            that says the run has just started; the run's own outcome replaces them the moment
            it finishes, and a notice that is still true then is still printed. */}
        {(changeStrip !== null && changeStrip.final === "" ? [] : notices).map((line) => (
          <div className="notice warn" key={line}>
            {line}
          </div>
        ))}

        {/* ================================================== what the owner reads */}
        {/* Four things and nothing else: the change block (its strip is the answer to
            «оно началось?»), the folder, one row of secondary actions, the versions.
            Everything below this block is collapsed on purpose -- it is still there. */}
        {changeBlock}
        {diskVisible}
        {secondaryRow}
        {versionsBlock}
        {fileTotal === 0 ? (
          /* The only creation actions that exist when there is nothing to act on. They are
             the same ids the backend offers in `presentation.actions`. */
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

        {/* ============================================ Показать подробности */}
        {/* The launch, the preview, the local run, the save facts, the facts, the
            specification, the artifact table, the backend's action vocabulary and the
            technical details. Nothing was deleted: every block that used to fill the card
            is one click away, under a summary that says so. */}
        <details className="tech-details" data-testid="project-details">
          <summary>{t(lang, "projects.card.details")}</summary>
          <div className="small muted" style={{ marginTop: 4 }}>
            {t(lang, "projects.card.details.hint")}
          </div>
          <div className="stack" style={{ gap: 16, marginTop: 10 }}>
        {changeOptions}
        {diskButtons}
        {/* ------------------------------------------------------- the launch */}
        {/* THE ONE OBVIOUS BUTTON. The owner's words: «Если написано открыть проект, я
            так понял, что я открою работающий проект и он у меня развернётся на рабочем
            столе. Я хочу видеть результат, а не вечное меню Forge.» One click resolves
            the entry point, starts the project if it is not already running, and opens
            the result in its own OS window. The backend owns every word below: the URL
            is the one the process bound or the preview serves, and a refusal is printed
            verbatim instead of being softened into a success. */}
        <div data-testid="project-launch">
          <div className="label">{t(lang, "projects.launch.title")}</div>
          <div className="row" style={{ gap: 8, flexWrap: "wrap", alignItems: "center", marginTop: 8 }}>
            <span data-testid="project-launch-button">
              <Button
                size="small"
                tone="primary"
                disabled={launchBusy || archived || fileTotal === 0}
                disabledReason={
                  archived
                    ? t(lang, "projects.badge.archived")
                    : fileTotal === 0
                      ? t(lang, "projects.empty.title")
                      : undefined
                }
                onClick={onLaunch}
              >
                {launchBusy ? t(lang, "projects.launch.running") : t(lang, "projects.action.launch")}
              </Button>
            </span>
            {fileTotal === 0 ? (
              <span className="small muted">{t(lang, "projects.empty.title")}</span>
            ) : null}
          </div>
          {/* ================================================ the repair panel */}
          {/* THE PANEL, IMMEDIATELY UNDER THE BUTTON, and only it explains a failure. The
              owner's requirement: «если проблемы — говорит об этом сразу под строкой
              "Запустить приложение" и предлагает решить… и я там мог бы с ним
              переписываться». One plain sentence read from the log, the redacted tail, the
              folded full log, «Исправить автоматически» and the one-line chat. Its strip is
              `changeFlightBlock` -- the very element «Что поправить?» renders -- so a change
              started here and a change started there are one flow with one set of words. */}
          {repairFacts !== null ? (
            <div style={{ marginTop: 8 }} data-testid="project-launch-failure">
              <RepairPanel
                lang={lang}
                facts={repairFacts}
                attempts={repairAttempts}
                state={repairState}
                detail={launchFailure?.detail ?? ""}
                busy={repairBusy || launchBusy}
                chat={repairChat}
                mockWarning={controls.mockWarning}
                fallbackAvailable={
                  launchFailure?.fields?.fallback_available === true &&
                  typeof launchFailure?.fields?.fallback_url === "string"
                }
                onChat={onRepairChat}
                onSend={onRepairSend}
                onAutoRepair={onRepairAuto}
                onFallback={onLaunchFallback}
              >
                {changeFlightBlock}
              </RepairPanel>
            </div>
          ) : launchFailure !== null ? (
            <div style={{ marginTop: 8 }} data-testid="project-launch-failure">
              <Refusal lang={lang} failure={launchFailure} />
              <LaunchFacts
                lang={lang}
                failure={launchFailure}
                onFallback={onLaunchFallback}
                busy={launchBusy}
              />
            </div>
          ) : null}
          {launchNotice !== "" ? (
            <div className="notice" style={{ marginTop: 8 }} data-testid="project-launch-notice">
              <div style={{ whiteSpace: "pre-wrap" }}>{launchNotice}</div>
              {/* «Открыть ещё раз» performs the same request: a running server is
                  reused, and the project's own app profile restores the same window. */}
              <div className="row" style={{ gap: 8, marginTop: 6 }}>
                <span data-testid="project-launch-again">
                  <Button size="small" tone="ghost" disabled={launchBusy} onClick={onLaunch}>
                    {t(lang, "projects.action.launch_again")}
                  </Button>
                </span>
              </div>
            </div>
          ) : null}
          <div className="small muted" style={{ marginTop: 6 }}>
            {t(lang, "projects.launch.hint")}
          </div>
        </div>

        {/* ----------------------------------------------------- the project on disk */}
        {/* THE ENTRANCE INTO THE PROJECT, in two halves.
            The folder itself -- its relative path and how many files it holds -- stays on
            the card: it is the one "where is my project" fact the owner reads without
            opening anything. The buttons that open it, and «Переместить в видимую папку»,
            are actions, so they render from `diskButtons` below, inside the collapsed
            «Показать подробности» block. Each button keeps the backend's own sentence as
            its disabled reason, so nothing promises a click that would do nothing. */}


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
            command, and the digest it shows is the value the backend compares. A
            static page has no command: «Запустить локально» calls the same preview
            route the block above uses. */}
        <RunPanel
          lang={lang}
          run={run}
          entrypoint={entrypoint}
          preview={preview}
          busy={runBusy}
          canVerify={fileTotal > 0 && hasRun}
          onStart={onRunStart}
          onPreviewStart={onPreviewStart}
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

        {/* The record operations are in the compact row above the details block: archive,
            restore and delete appear exactly once on the card, so no two controls can be
            mistaken for the same action. */}
        </div>
        </details>

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

  // ------------------------------------------------------------------ the address
  // The screen the owner sees is the address the page carries. One router per mounted
  // screen; it reads `location.hash`, pushes a history entry per navigation and tells
  // this component what the address now names. `selectedId` below is derived from it,
  // so a project card can never be shown at an address that names the list (or the
  // other way round).
  const routerRef = useRef<ProjectRouter | null>(null);
  const [view, setView] = useState<ProjectView>(() => projectView(window.location.hash));

  useEffect(() => {
    const router = createProjectRouter(window);
    routerRef.current = router;
    const unsubscribe = router.subscribe((next) => setView(next));
    // The address may have changed between the first render and this effect.
    setView(router.current());
    return () => {
      unsubscribe();
      router.dispose();
      if (routerRef.current === router) routerRef.current = null;
    };
  }, []);

  // ------------------------------------------------------------------ the list
  const [list, setList] = useState<Load<LifecycleSummary[]>>({ status: "loading" });
  const [filter, setFilter] = useState<"active" | "archive">("active");
  // The project the screen draws is exactly the project its address names. «Назад к
  // проектам», the browser's Back and a reload all change the address, and with it this
  // one value: leaving the project screen cannot leave a stale card behind.
  const selectedId = view.view === "detail" ? view.projectId : "";

  const loadList = useCallback(async () => {
    setList({ status: "loading" });
    const outcome = await loadProjectLifecycle();
    setList(outcome.ok ? { status: "ready", value: outcome.value } : { status: "failed", failure: outcome.failure });
  }, []);

  useEffect(() => {
    void loadList();
  }, [loadList]);

  const summaries = list.status === "ready" ? list.value : [];
  // Which rows this screen draws: the owner's visible-root projects by default, the
  // whole listing behind «Показать всё», one page at a time. The rule itself lives in
  // `ui/src/project-list.js`, which the regression test executes directly, so the
  // screen cannot drift from what is asserted about it.
  const [showAllProjects, setShowAllProjects] = useState(false);
  const [listShown, setListShown] = useState(0);
  const slice = useMemo(
    () =>
      projectListRows({
        summaries,
        archived: filter === "archive",
        showAll: showAllProjects,
        shown: listShown,
      }),
    [summaries, filter, showAllProjects, listShown]
  );
  const visible = slice.rows;

  /**
   * Open one project: navigate to its own screen, `#/projects/<id>`.
   *
   * This is the fix for the dead click, and it is deliberately not a "select and
   * scroll": the address changes, the list is replaced by the project screen, and Back
   * returns to the list because the navigation pushed a history entry.
   */
  const openProject = useCallback((projectId: string) => {
    if (projectId === "") return;
    routerRef.current?.open(projectId);
  }, []);

  // A click on another screen's «Открыть файлы»/«Запустить локально» arrives here as
  // `focusProjectId`. It navigates to that project's screen; the detail route is then
  // fetched by the existing effect on `selectedId`.
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

  // Keep the address honest across a refresh: a project that vanished must not leave a
  // screen describing a record nobody can fetch any more. The list is the answer.
  useEffect(() => {
    if (selectedId === "") return;
    if (list.status !== "ready") return;
    if (summaries.some((project) => project.project_id === selectedId)) return;
    routerRef.current?.showList();
  }, [list, summaries, selectedId]);

  // ---------------------------------------------------------------- the detail
  const [detail, setDetail] = useState<Load<LifecycleDetail> | null>(null);
  const [busy, setBusy] = useState("");
  const [actionFailure, setActionFailure] = useState<Failure | null>(null);
  const [actionNotice, setActionNotice] = useState("");
  // The entrance into the project: which target is being opened right now, the answer
  // of the last «Переместить в видимую папку», and the backend's refusal when a target
  // could not be performed. None of the three is guessed: each is a response.
  const [openingTarget, setOpeningTarget] = useState("");
  const [diskNotice, setDiskNotice] = useState("");
  const [diskFailure, setDiskFailure] = useState<Failure | null>(null);
  const [movingToVisible, setMovingToVisible] = useState(false);
  // The one-click launch. Its answer is a *window*, so the card keeps three facts: the
  // request is in flight, the URL that is now open in its own window, and the backend's
  // refusal verbatim when nothing could be shown.
  const [launchBusy, setLaunchBusy] = useState(false);
  const [launchNotice, setLaunchNotice] = useState("");
  const [launchFailure, setLaunchFailure] = useState<Failure | null>(null);
  // THE REPAIR PANEL (under the launch button). `repairFacts` holds the refusal's own
  // structured fields -- the redacted log tail, the exit code, `waiting_for_input`, and the
  // project launcher's own log -- and the panel exists only while they do. `repairAttempts`
  // counts *automatic* repairs (a sentence the owner types never spends one), and
  // `repairState` is the loop's last answer; both rules live in `ui/src/repair.ts`.
  const [repairFacts, setRepairFacts] = useState<LaunchFacts | null>(null);
  const [repairAttempts, setRepairAttempts] = useState(0);
  const [repairState, setRepairState] = useState<RepairState | null>(null);
  const [repairChat, setRepairChat] = useState("");
  const [repairBusy, setRepairBusy] = useState(false);
  // A synchronous guard beside the visible flag, so a double click on «Исправить
  // автоматически» cannot start two change runs in one tick (the same defect the change
  // button's guard exists for; measured there as two versions at zero files).
  const repairBusyRef = useRef(false);
  // THE CAP IS PER PROJECT FOR THE WHOLE SESSION: switching to another project and back
  // must not hand the same failing program two fresh automatic attempts.
  const repairAttemptsByProject = useRef<Record<string, number>>({});
  // The row's own launch: same route, same answer, printed under exactly the row that
  // was clicked. The list summary carries no entry point, so the row reads the project's
  // detail first and names the digest the backend itself published -- the approval keeps
  // naming a value the owner was shown, even from the list.
  const [rowLaunching, setRowLaunching] = useState("");
  const [rowLaunchNotice, setRowLaunchNotice] = useState<{ projectId: string; text: string } | null>(null);
  const [rowLaunchFailure, setRowLaunchFailure] = useState<{ projectId: string; failure: Failure } | null>(null);
  // The row's own entrance. «Открыть папку» on a row acts without leaving the list, so
  // its answer is printed under exactly the row that was clicked: the program and its
  // pid when Explorer started, the backend's refusal sentence when it did not.
  const [rowOpening, setRowOpening] = useState("");
  const [rowNotice, setRowNotice] = useState<{ projectId: string; text: string } | null>(null);
  const [rowFailure, setRowFailure] = useState<{ projectId: string; failure: Failure } | null>(null);

  const openRowFolder = async (projectId: string) => {
    if (projectId === "") return;
    setRowOpening(projectId);
    setRowNotice(null);
    setRowFailure(null);
    const outcome = await openProjectTarget(projectId, "folder");
    setRowOpening("");
    if (!outcome.ok) {
      setRowFailure({ projectId, failure: outcome.failure });
      return;
    }
    const value = outcome.value;
    const pid = typeof value.pid === "number" ? value.pid : 0;
    setRowNotice({
      projectId,
      text: t(lang, "projects.row.open_notice")
        .replace("{program}", text(value.program))
        .replace("{pid}", String(pid))
        .replace("{folder}", text(value.folder)),
    });
  };

  /**
   * «Запустить приложение» on a row: run the project and show it in its own window.
   *
   * The list carries no entry point, so this reads the project's own detail route -- the
   * route that already publishes the digest the backend compares -- and then launches
   * with exactly that digest. A refusal is printed under the row, never swallowed.
   */
  const launchRow = async (projectId: string) => {
    if (projectId === "") return;
    setRowLaunching(projectId);
    setRowLaunchNotice(null);
    setRowLaunchFailure(null);
    setRowNotice(null);
    setRowFailure(null);
    const detailOutcome = await loadProjectDetail(projectId);
    if (!detailOutcome.ok) {
      setRowLaunching("");
      setRowLaunchFailure({ projectId, failure: detailOutcome.failure });
      return;
    }
    const digest = String(
      detailOutcome.value.entrypoint?.digest ?? detailOutcome.value.run?.entrypoint_digest ?? ""
    );
    const outcome = await launchProject(projectId, digest === "" ? undefined : digest);
    setRowLaunching("");
    if (!outcome.ok) {
      setRowLaunchFailure({ projectId, failure: outcome.failure });
      return;
    }
    setRowLaunchNotice({ projectId, text: launchSentence(lang, outcome.value) });
    await loadList();
  };

  const loadDetail = useCallback(async (projectId: string, options?: { quiet?: boolean }): Promise<LifecycleDetail | null> => {
    if (projectId === "") {
      setDetail(null);
      return null;
    }
    // A **quiet** refresh keeps the rendered card on screen while the same record is
    // fetched again. The launch needs it: the notice that says where the project
    // opened lives inside the card, and replacing the card with «Загрузка…» would hide
    // the very answer the click produced.
    if (options?.quiet !== true) setDetail({ status: "loading" });
    const outcome = await loadProjectDetail(projectId);
    setDetail(outcome.ok ? { status: "ready", value: outcome.value } : { status: "failed", failure: outcome.failure });
    // The loaded record is returned: a caller that has just changed the project (a
    // dispatched change run) reads the new `current_version` from it instead of guessing
    // it from the run's own response.
    return outcome.ok ? outcome.value : null;
  }, []);

  const project = detail?.status === "ready" ? detail.value.project ?? null : null;
  const versions: LifecycleVersion[] = detail?.status === "ready" ? detail.value.versions ?? [] : [];
  const currentVersionNumber = typeof project?.current_version === "number" ? project.current_version : 0;
  // The entrance block, read from the detail payload exactly as the backend derived it.
  const openBlock: LifecycleOpen = detail?.status === "ready" ? detail.value.open ?? {} : {};
  const folderBlock: LifecycleFolder = detail?.status === "ready" ? detail.value.folder ?? {} : {};

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

  // ------------------------------------------------------------- the entrance
  // One click, one route. The backend resolves the folder from the project record, so
  // the request carries no path; when it refuses (no files, folder gone, VS Code absent)
  // its own sentence is printed and nothing was started.
  const openTarget = async (target: "folder" | "editor" | "terminal") => {
    if (selectedId === "") return;
    setOpeningTarget(target);
    setDiskNotice("");
    setDiskFailure(null);
    const outcome = await openProjectTarget(selectedId, target);
    setOpeningTarget("");
    if (!outcome.ok) {
      setDiskFailure(outcome.failure);
      return;
    }
    const program = String(outcome.value.program ?? "");
    const pid = typeof outcome.value.pid === "number" ? outcome.value.pid : 0;
    const folder = String(outcome.value.diagnostics?.folder ?? outcome.value.folder ?? "");
    const label = text(
      (openBlock.targets ?? {})[target]?.label ?? target
    );
    // The backend's own answer, printed as it arrived: which program started, with which
    // pid, at which folder. A pid of 0 means the OS did not report one, and is not dressed up.
    setDiskNotice(`${label}: ${program}${pid > 0 ? ` (PID ${pid})` : ""} — ${folder}`);
  };

  const moveToVisible = async () => {
    if (selectedId === "") return;
    setMovingToVisible(true);
    setDiskNotice("");
    setDiskFailure(null);
    const outcome = await moveProjectToVisibleFolder(selectedId);
    setMovingToVisible(false);
    if (!outcome.ok) {
      setDiskFailure(outcome.failure);
      return;
    }
    const already = outcome.value.already_visible === true;
    const folder = String(outcome.value.folder ?? "");
    const count = Number(outcome.value.moved_count ?? 0);
    setDiskNotice(
      already
        ? t(lang, "projects.disk.already")
        : t(lang, "projects.disk.moved").replace("{n}", String(count)).replace("{folder}", folder)
    );
    await loadDetail(selectedId);
    await loadList();
  };

  // ------------------------------------------------------------ the launch
  //
  // One click, one request: the backend resolves the entry point, starts (or reuses) the
  // project and opens the result in its own window. The digest is the one the card was
  // already shown for this entry point, so the runner's approval keeps naming the exact
  // command; a static page needs none. The answer is printed as it arrived: the URL that
  // is open in its own window, or the backend's refusal verbatim.
  /**
   * ONE LAUNCH REQUEST, used by the button and by the repair loop's retry.
   *
   * Both callers must make the *same* request (same digest, same approval, no silent
   * fallback), or the retry would not be a retry of the thing the owner pressed. The
   * returned `facts` are the refusal's structured fields, unread and unmodified: the panel
   * derives its cause from them.
   */
  const requestLaunch = async (
    staticFallback = false
  ): Promise<{
    ok: boolean;
    value: LifecycleLaunch | null;
    failure: Failure | null;
    facts: LaunchFacts;
  }> => {
    if (selectedId === "") return { ok: false, value: null, failure: null, facts: {} };
    const digest = String(entrypoint?.digest ?? "");
    const outcome = await launchProject(
      selectedId,
      digest === "" ? undefined : digest,
      staticFallback
    );
    if (!outcome.ok) {
      return {
        ok: false,
        value: null,
        failure: outcome.failure,
        facts: (outcome.failure.fields ?? {}) as LaunchFacts,
      };
    }
    return { ok: true, value: outcome.value, failure: null, facts: {} };
  };

  /**
   * Show a refused launch: the plain sentence, or the repair panel when the log explains it.
   *
   * The panel is offered only when the refusal carries run facts (`log_tail`, an exit code,
   * `waiting_for_input`, the launcher's own log). An approval or archived refusal has no
   * cause to read, and a panel that invented one would be worse than the honest sentence.
   */
  const showLaunchRefusal = (failure: Failure) => {
    setLaunchFailure(failure);
    setLaunchNotice("");
    const facts = (failure.fields ?? {}) as LaunchFacts;
    setRepairFacts(repairPanelModel(facts, repairAttempts).hasRunFacts ? facts : null);
    setRepairState(null);
  };

  const launchSelected = async (staticFallback = false) => {
    if (selectedId === "") return;
    setLaunchBusy(true);
    setActionFailure(null);
    setActionNotice("");
    setLaunchFailure(null);
    setLaunchNotice("");
    setRepairState(null);
    const outcome = await requestLaunch(staticFallback);
    setLaunchBusy(false);
    if (!outcome.ok) {
      if (outcome.failure !== null) showLaunchRefusal(outcome.failure);
      return;
    }
    // A launch that really opened a window is a single green line and nothing else.
    setRepairFacts(null);
    setRepairState(null);
    setRepairChat("");
    setLaunchNotice(launchSentence(lang, outcome.value ?? {}));
    // The backend may have started the project's process or its preview: refresh the card
    // so its runtime blocks agree with the window that just opened. The refresh is quiet,
    // so the notice stays on screen while the record is read again.
    await loadDetail(selectedId, { quiet: true });
    await loadList();
  };

  /**
   * ONE PRESS OF THE PANEL: one change run, then at most one launch retry.
   *
   * The steps are `driveRepair`'s (`ui/src/repair.ts`), not this function's: the panel and
   * its test execute the same loop. What this handler adds is only the wiring -- a change is
   * dispatched through `startChange`, the very handler «Что поправить?» uses, so the strip,
   * the live bar and the outcome are the same objects; the retry is `requestLaunch`, the
   * very request the button makes. A run that wrote no file never reaches the launch.
   */
  const runRepair = async (instruction: string, auto: boolean) => {
    if (selectedId === "" || repairBusyRef.current) return;
    if (instruction.trim() === "") return;
    repairBusyRef.current = true;
    setRepairBusy(true);
    try {
      const next = await driveRepair(
        {
          dispatch: async (text) => {
            const outcome = await startChange(undefined, text);
            if (outcome === null) return { ok: false, filesWritten: 0, version: 0 };
            return {
              ok: true,
              filesWritten: Math.max(
                Number(outcome.filesCreated ?? 0),
                outcome.files.length
              ),
              version: outcome.toVersion,
            };
          },
          launch: async () => {
            const answer = await requestLaunch();
            return {
              ok: answer.ok,
              url: String(answer.value?.url ?? ""),
              facts: answer.facts,
            };
          },
        },
        repairState ?? initialRepairState(repairAttempts),
        { instruction, countAttempt: auto }
      );
      setRepairState(next);
      setRepairAttempts(next.attempts);
      if (selectedId !== "") repairAttemptsByProject.current[selectedId] = next.attempts;
      if (next.phase === "opened") {
        // The honest success: a real window on a URL a probe answered. The panel goes away
        // and the green line takes its place, exactly as for a launch that worked first time.
        setLaunchFailure(null);
        setRepairFacts(null);
        setRepairChat("");
        setLaunchNotice(t(lang, "projects.repair.opened").replace("{url}", next.url));
        await loadDetail(selectedId, { quiet: true });
        await loadList();
      } else if (next.facts !== null) {
        // Still failing: the panel keeps the *new* reason and the new log tail.
        setRepairFacts(next.facts);
      }
    } finally {
      setRepairBusy(false);
      repairBusyRef.current = false;
    }
  };

  /** «Исправить автоматически»: the deterministic instruction, one press, one attempt. */
  const repairAuto = () => {
    if (repairFacts === null) return;
    const model = repairPanelModel(repairFacts, repairAttempts);
    void runRepair(composeRepairInstruction(model.cause), true);
  };

  /** The one-line chat: the owner's own words go down the same change path. */
  const repairSend = () => {
    const text = repairChat.trim();
    if (text === "" || repairFacts === null) return;
    void runRepair(text, false);
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
    // The record is gone, so its address is gone too: the list is where the owner ends up,
    // and the result above the trash says what happened to the project he was looking at.
    routerRef.current?.showList();
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
  // A real trash on this machine held 42 entries, and the screen used to render every
  // one of them: a bounded view keeps the section usable without hiding anything --
  // the count is always the real count, and «Показать ещё» reveals the next page. This
  // is a *rendering* bound only: the answer of `GET /api/projects/deleted` is still the
  // whole listing, and the endpoint's meaning is unchanged.
  const [trashShown, setTrashShown] = useState(TRASH_PAGE_SIZE);

  const loadTrash = useCallback(async () => {
    const outcome = await loadDeletedProjects();
    setTrash(outcome.ok ? { status: "ready", value: outcome.value } : { status: "failed", failure: outcome.failure });
  }, []);

  useEffect(() => {
    void loadTrash();
  }, [loadTrash]);

  const trashItems = trash.status === "ready" ? trash.value : [];
  const trashCount = trashItems.length;
  const trashRows = trashItems.slice(0, Math.max(TRASH_PAGE_SIZE, trashShown));

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
    // The restored record is opened on its own screen: its files, versions and actions
    // are one frame away, and the address says which project is on screen.
    openProject(identifier);
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
    // A project that was just removed must not leave a screen describing a gone record.
    if (selectedId !== "" && removedIds.includes(selectedId)) {
      routerRef.current?.showList();
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
  /**
   * THE SCREENSHOTS ATTACHED TO THIS CHANGE, kept with the project.
   *
   * The owner's complaint was that an honest error gave him nothing to send to Forge. A
   * screenshot is what he has, so «Что сделать?» takes one -- but the model cannot read
   * pixels, so the panel asks for the error text in one line and the backend writes the
   * notice `Приложен скриншот: <path>. Модель не видит изображения — ...` into the task
   * text. The image stays an artefact for the owner and for Forge; nothing here pretends
   * the model looked at it.
   */
  const [attachments, setAttachments] = useState<ChangeAttachment[]>([]);
  const [attachmentBusy, setAttachmentBusy] = useState(false);
  const [attachmentError, setAttachmentError] = useState("");

  /**
   * Upload one paste/drop/pick, one file at a time, and keep what the backend stored.
   *
   * Every refusal is the backend's own sentence (not an image / over 5 MB / outside the
   * project), printed under the field: a control that silently does nothing is the defect
   * this panel was built to remove. The chip appears only after the API answered with a
   * real path, so a chip always means «этот файл уже в проекте».
   */
  const addAttachments = async (files: File[]): Promise<void> => {
    if (selectedId === "" || files.length === 0) return;
    setAttachmentError("");
    setAttachmentBusy(true);
    try {
      let kept = attachments.length;
      for (const file of files) {
        if (kept >= MAX_ATTACHMENTS_PER_CHANGE) {
          setAttachmentError(t(lang, "projects.attach.error.count"));
          break;
        }
        if (!isImageFile(file)) {
          setAttachmentError(t(lang, "projects.attach.error.image"));
          continue;
        }
        if (file.size > MAX_ATTACHMENT_BYTES) {
          setAttachmentError(t(lang, "projects.attach.error.size"));
          continue;
        }
        let data = "";
        try {
          data = await readFileAsBase64(file);
        } catch {
          setAttachmentError(
            t(lang, "projects.attach.error.failed").replace(
              "{reason}",
              t(lang, "projects.attach.error.image")
            )
          );
          continue;
        }
        const outcome = await uploadProjectAttachment(selectedId, file.name, data);
        if (!outcome.ok) {
          const reason =
            String(outcome.failure.detail ?? "").trim() || t(lang, "projects.err.generic");
          setAttachmentError(
            t(lang, "projects.attach.error.failed").replace("{reason}", reason)
          );
          continue;
        }
        const path = String(outcome.value.path ?? "").trim();
        if (path === "") {
          setAttachmentError(
            t(lang, "projects.attach.error.failed").replace(
              "{reason}",
              t(lang, "projects.err.generic")
            )
          );
          continue;
        }
        const item: ChangeAttachment = {
          path,
          name: String(outcome.value.name ?? "") || file.name || "screenshot",
          kind: String(outcome.value.kind ?? ""),
          bytes: Number(outcome.value.bytes ?? file.size ?? 0),
          previewUrl: URL.createObjectURL(file),
        };
        kept += 1;
        setAttachments((current) =>
          current.some((entry) => entry.path === item.path) ? current : [...current, item]
        );
      }
    } finally {
      setAttachmentBusy(false);
    }
  };

  /** «×» on a chip: the change stops naming the file. It stays on disk in the project. */
  const removeAttachment = (path: string): void => {
    setAttachments((current) => {
      const gone = current.find((item) => item.path === path);
      if (gone) URL.revokeObjectURL(gone.previewUrl);
      return current.filter((item) => item.path !== path);
    });
  };

  /** Drop every chip, releasing its local thumbnail URL. */
  const clearAttachments = (): void => {
    setAttachments((current) => {
      current.forEach((item) => URL.revokeObjectURL(item.previewUrl));
      return [];
    });
    setAttachmentError("");
  };
  const [changeFailure, setChangeFailure] = useState<Failure | null>(null);
  const [dispatched, setDispatched] = useState<DispatchResult | null>(null);
  const [changeOutcome, setChangeOutcome] = useState<ChangeOutcome | null>(null);
  const [dispatchFailure, setDispatchFailure] = useState<Failure | null>(null);
  /**
   * THE ONE ACTION'S OWN STATE.
   *
   * `starting` is true from the click until the dispatch answers -- the button reads
   * «Отправляю…» and the strip is already on screen, so the owner never has to guess
   * whether the click was registered. `runId` is the run the dispatch named; it stays set
   * after the run ends, because "which run was that?" is a question the strip answers
   * forever, not only while the request is in flight.
   */
  const [changeStarting, setChangeStarting] = useState(false);
  const [changeRunId, setChangeRunId] = useState("");
  /**
   * The press that started this change, in epoch milliseconds.
   *
   * `POST /api/tasks/run` is synchronous, so the run id — and therefore any record — arrives
   * only after the Core has finished; the clock of the alive bar starts here, at the click,
   * and is bounded by the record's own `updated_at` when it arrives.
   */
  const [changeObservedAt, setChangeObservedAt] = useState(0);
  /**
   * THE SYNCHRONOUS GUARD behind the one button.
   *
   * A double click delivers two `onClick`s before React re-renders, so a `useState` flag
   * cannot stop the second one: by the time it is read the state is unchanged. The guard
   * is acquired before the first `await` and released when the dispatch answers, so the
   * second press is dropped instead of starting a second version.
   */
  const changeGuardRef = useRef(createChangeGuard());
  /**
   * The run record, polled through the *existing* `GET /api/runs/{run_id}` while the run
   * is not terminal. Its `event_count` and `state` are what the stage word is derived
   * from: nothing here invents a stage the record does not prove.
   */
  const [changeRecord, setChangeRecord] = useState<RunRecord | null>(null);

  const [provider, setProvider] = useState("");
  const [model, setModel] = useState("");

  // One selection resets every per-project answer, so a card never mixes two projects: the staged
  // version, the dispatch result, the artifact manifest, the history, the selected version and the
  // run record all belong to the project that was open when they arrived.
  useEffect(() => {
    setActionFailure(null);
    setActionNotice("");
    setDiskNotice("");
    setDiskFailure(null);
    setLaunchBusy(false);
    setLaunchNotice("");
    setLaunchFailure(null);
    // The repair panel belongs to the project that was on screen: its cause, its log and its
    // chat text are reset with everything else. The *spent attempts* come back from the
    // session's own record, so returning to a project cannot re-arm two more automatic tries.
    setRepairFacts(null);
    setRepairAttempts(repairAttemptsByProject.current[selectedId] ?? 0);
    setRepairState(null);
    setRepairChat("");
    setRepairBusy(false);
    repairBusyRef.current = false;
    setDispatched(null);
    setChangeOutcome(null);
    setDispatchFailure(null);
    setChangeFailure(null);
    setChangeStarting(false);
    setChangeRunId("");
    setChangeRecord(null);
    setChangeArmed(false);
    // The screenshots belong to the change of the project that was open: another card
    // must not inherit them (their paths name that project's files).
    clearAttachments();
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

  /**
   * THE PROVIDER A PRESS WOULD REALLY USE.
   *
   * «Авто» (`provider === ""`) is not "no provider": the Core's own configured default
   * runs. The change block therefore names it, so the mock warning is printed for the
   * engine that will really answer rather than only for a name the owner typed.
   */
  const defaultProvider =
    backend.status === "live" ? String(backend.core.details?.default_provider ?? "") : "";
  const effectiveProviderName = effectiveProvider(provider, defaultProvider);
  /**
   * THE LAST PROVIDER LIST THE CORE REALLY PUBLISHED.
   *
   * The shell re-probes the Core every few seconds, and one failed probe publishes an
   * empty provider list for a moment. Deriving the retry from the *current* list alone
   * meant a blip disabled «Повторить с реальной моделью» on a screen where the Core had
   * just listed five providers (measured over CDP right after a change run). The last
   * non-empty list is kept, so a momentary gap cannot take an action away.
   */
  const [knownProviders, setKnownProviders] = useState<string[]>([]);
  useEffect(() => {
    if (options.length > 0) setKnownProviders(options);
  }, [options]);

  /**
   * THE BEST NON-MOCK PROVIDER, or "" when the deployment has none.
   *
   * The zero-file button switches to exactly this one, in one click, and is off with a
   * printed reason when no real provider is configured at all -- never a button that
   * silently keeps the offline engine.
   */
  const realProvider = useMemo(
    () =>
      bestRealProvider(
        options.length > 0 ? options : knownProviders,
        defaultProvider
      ),
    [options, knownProviders, defaultProvider]
  );
  /** The effective provider is the offline engine: the press cannot change any file. */
  const effectiveMock = isMockProvider(effectiveProviderName);
  /**
   * «Внести ещё изменения» was pressed: the form is live again *deliberately*.
   *
   * One press, then a different button. After a change the form stays frozen (and the
   * primary button says what was recorded) until the owner asks for another change, so
   * a second press on the same words cannot produce a second version.
   */
  const [changeArmed, setChangeArmed] = useState(false);

  /**
   * THE ONE ACTION: stage the change and run it, in one click.
   *
   * It used to take two buttons («Начать изменения» then «Исправить проект»), and the
   * owner pressed both without being able to tell whether anything had started. The two
   * requests are still two requests -- `POST /api/projects/{id}/versions` stages the
   * change, `POST /api/tasks/run` executes it -- but they are one action now, in one
   * order, with one state on screen:
   *
   * * `changeStarting` is true from the click, so the button says «Отправляю…» and the
   *   strip is already visible;
   * * a refusal from either step is printed beside the button and clears the strip: no
   *   run started, and the card must not suggest one did;
   * * a run sets `changeRunId`, which the strip prints and the poller follows.
   */
  const startChange = async (
    overrideProvider?: string,
    textOverride?: string
  ): Promise<ChangeOutcome | null> => {
    if (selectedId === "") return null;
    // THE TEXT COMES FROM ONE OF TWO PLACES and is otherwise the same action: the
    // «Что поправить?» textarea, or a line typed into the repair panel's chat. Both go
    // through this one handler, so a change started from the panel is indistinguishable
    // from one started from the block -- same guard, same strip, same outcome object.
    const trimmed = (textOverride !== undefined ? textOverride : change).trim();
    if (trimmed === "") {
      setChangeError(t(lang, "projects.newversion.error"));
      return null;
    }
    // ONE PRESS, ONE DISPATCH. The guard is taken synchronously: a second click of a
    // double click finds it held and does nothing, so no second version is created.
    if (!changeGuardRef.current.tryAcquire()) return null;
    const chosen = overrideProvider !== undefined ? overrideProvider : provider;
    try {
      setChangeError("");
      setChangeFailure(null);
      setDispatchFailure(null);
      setDispatched(null);
      setChangeOutcome(null);
      setChangeRecord(null);
      setChangeRunId("");
      setActionFailure(null);
      setActionNotice("");
      setChangeStarting(true);
      setChangeObservedAt(Date.now());
      setBusy("version");
      setChangeArmed(false);
      const fromVersion = currentVersionNumber;
      // THE SCREENSHOTS TRAVEL WITH THE CHANGE: the paths the upload route published.
      // The backend re-derives each one before it reaches the model's task text.
      const attachmentPaths = attachments.map((item) => item.path);
      const stagedOutcome = await createProjectVersion(selectedId, trimmed, attachmentPaths);
      if (!stagedOutcome.ok) {
        setBusy("");
        setChangeStarting(false);
        setChangeFailure(stagedOutcome.failure);
        return null;
      }
      // THE SAME WORDS ARE ONE CHANGE. The backend refuses to record a second version
      // for a sentence it already holds: `already_applied` means the change is in the
      // project, so dispatching a run would be a second attempt at nothing. The card
      // says what is true instead of pressing again.
      if (stagedOutcome.value.already_applied === true) {
        setBusy("");
        setChangeStarting(false);
        setChangeArmed(false);
        const already: ChangeOutcome = {
          runId: String(stagedOutcome.value.previous_run_id ?? ""),
          filesCreated: 0,
          attached: true,
          fromVersion,
          toVersion: Number(stagedOutcome.value.version ?? fromVersion),
          stagedVersion: Number(stagedOutcome.value.version ?? fromVersion),
          files: [],
          alreadyApplied: true,
        };
        setChangeOutcome(already);
        await loadDetail(selectedId, { quiet: true });
        return already;
      }
      const taskText = String(stagedOutcome.value.task_text ?? "");
      if (taskText === "") {
        setBusy("");
        setChangeStarting(false);
        setChangeFailure({
          kind: "error",
          status: 200,
          path: `/api/projects/${selectedId}/versions`,
          detail: t(lang, "projects.newversion.error"),
        });
        return null;
      }
      // The staged version is a real fact (the record now carries a pending version), so the
      // card refreshes it before the run starts -- QUIETLY. A non-quiet refresh replaces the
      // card with «Загрузка…», which would hide the status strip at the exact moment it is the
      // answer to «оно началось?» (measured over CDP: a blank card under the strip).
      await loadDetail(selectedId, { quiet: true });
      const outcome = await dispatchTask({
        description: taskText,
        category: "code",
        // THE PROVIDER THIS PRESS REALLY USES: the owner's choice, or the one the retry
        // button just switched to. Naming no provider leaves the Core's default in force,
        // which is exactly what «Авто» means until the owner picks something else.
        providerName:
          chosen !== "" && options.includes(chosen) ? chosen : undefined,
        modelName: model !== "" ? model : undefined,
        // THE LABEL PROMISES WRITING, so the run writes. The old toggle made "write" a
        // separate decision and the primary button could start a run that changed nothing.
        writeMode: true,
        // THE ASSOCIATION. The run that executes this staged change text belongs to the
        // project the card is showing, so the project's own `proj-…` id travels with it.
        // The Core then (a) targets the project's controlled workspace with a write-mode
        // answer and (b) records the run as the project's next version through the
        // existing `attach_run` path. Without it the run finished into a workspace no
        // record named and the card promised a version that never appeared.
        lifecycleProjectId: selectedId,
      });
      setBusy("");
      setChangeStarting(false);
      if (!outcome.ok) {
        setDispatchFailure(outcome.failure);
        return null;
      }
      setDispatched(outcome.value);
      setChangeRunId(String(outcome.value.response.run_id ?? ""));
      // Read the record *after* the run: a new version exists only if `current_version`
      // really moved. The response alone cannot answer that question. Quietly again, so the
      // strip that reports the run is never taken off the screen by its own refresh.
      const refreshed = await loadDetail(selectedId, { quiet: true });
      const toVersion =
        refreshed !== null && typeof refreshed.project?.current_version === "number"
          ? refreshed.project.current_version
          : fromVersion;
      // WHAT CHANGED, from the new version's own manifest: the paths, whether each was
      // created or updated, and its size. A version that did not advance reports no files,
      // which is exactly what the card prints («Изменения не записаны»).
      let changed: LifecycleArtifact[] = [];
      if (toVersion > fromVersion) {
        const manifest = await loadProjectFiles(selectedId, toVersion);
        if (manifest.ok) changed = manifest.value.files ?? [];
      }
      const finished: ChangeOutcome = {
        runId: String(outcome.value.response.run_id ?? ""),
        filesCreated: Number(outcome.value.response.files_created ?? 0),
        attached: String(outcome.value.response.lifecycle_project_id ?? "") === selectedId,
        fromVersion,
        toVersion,
        stagedVersion: Number(stagedOutcome.value.version ?? toVersion),
        files: changed,
      };
      setChangeOutcome(finished);
      await loadList();
      onRefresh();
      // The outcome is also *returned*: the repair panel decides from these very numbers
      // whether a launch retry is honest at all (a run that wrote no file has repaired
      // nothing), so it must read the same object the strip renders.
      return finished;
    } finally {
      // The request travel is over; the button stays off through `changeLocked` until the
      // run reaches a terminal state or a refusal is on screen.
      changeGuardRef.current.release();
    }
  };

  /**
   * Follow the change run through the existing run record while it is not terminal.
   *
   * The route is the one the card already uses (`GET /api/runs/{run_id}`); the interval is
   * the project-run poller's own (1.5 s). It stops on a terminal record or on unmount, so a
   * finished run costs no further requests, and a stage word is only ever derived from the
   * record's own `event_count`/`state`.
   */
  useEffect(() => {
    if (changeRunId === "") {
      setChangeRecord(null);
      return;
    }
    let cancelled = false;
    let timer = 0;
    const poll = async () => {
      const outcome = await loadRunRecord(changeRunId);
      if (cancelled) return;
      if (outcome.ok) {
        setChangeRecord(outcome.value);
        if (isTerminalRecord(outcome.value)) return;
      }
      timer = window.setTimeout(() => void poll(), 1500);
    };
    timer = window.setTimeout(() => void poll(), 300);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [changeRunId]);

  /**
   * The strip the card renders, derived from the facts above and nothing else.
   *
   * The words, in order: the click puts «Запускаю · выполняю запуск…» on screen at once; a
   * run id replaces it with «Запущено: run-… · <stage>»; the record's own `event_count`
   * moves the stage word («пишу файлы…» before the fourth recorded event, «проверяю…»
   * after it, the same rule `RunProgress` uses); a terminal record or a finished outcome
   * ends the line and the final sentence states what really happened.
   */
  const changeStrip = useMemo<ChangeStrip | null>(() => {
    const finished = changeOutcome !== null;
    if (!changeStarting && changeRunId === "" && !finished) return null;
    const record = changeRecord;
    const events =
      record !== null && typeof record.event_count === "number" && Number.isFinite(record.event_count)
        ? record.event_count
        : 0;
    const terminal = record !== null && isTerminalRecord(record);
    let stage = t(lang, "projects.change.working");
    if (finished || terminal) stage = t(lang, "projects.change.finished");
    else if (changeRunId !== "" && events >= 4) stage = t(lang, "projects.change.checking");
    else if (changeRunId !== "" && events > 0) stage = t(lang, "projects.change.writing");
    else if (changeStarting) stage = t(lang, "projects.change.starting");
    const title =
      changeRunId !== ""
        ? t(lang, "projects.change.launched")
            .replace("{run}", changeRunId)
            .replace("{stage}", stage)
        : t(lang, "projects.change.starting.line").replace("{stage}", stage);
    let final = "";
    let refusal = "";
    let refusalRule = "";
    let refusalDetailText = "";
    let tone: ChangeStrip["tone"] = "info";
    if (changeOutcome !== null) {
      const advanced = changeOutcome.toVersion > changeOutcome.fromVersion;
      // A CHANGE THAT WROTE NO FILE IS NOT A SUCCESS, and the strip says it in the
      // owner's own measure: «файлов 0 — код не менялся». The old wording («изменения не
      // записаны») read like a hiccup instead of the one fact that matters.
      const wroteFiles =
        Number(changeOutcome.filesCreated ?? 0) > 0 || changeOutcome.files.length > 0;
      final = changeOutcome.alreadyApplied === true
        ? t(lang, "projects.change.already_applied").replace(
            "{version}",
            `v${changeOutcome.stagedVersion || changeOutcome.toVersion}`
          )
        : !changeOutcome.attached
          ? t(lang, "projects.newversion.outcome.unattached")
          : advanced
            ? t(lang, "projects.change.done").replace("{version}", `v${changeOutcome.toVersion}`)
            : t(lang, "projects.change.zero_files").replace(
                "{version}",
                `v${changeOutcome.stagedVersion || changeOutcome.toVersion}`
              );
      if (changeOutcome.alreadyApplied === true) {
        tone = "info";
      } else if (!advanced && !wroteFiles) {
        // THE PLAIN LANGUAGE FIRST. The backend's refusal is a diagnostic sentence
        // («Critical Tier 1 context item … security_forbidden_content:secret …»); the owner
        // needs to know that the *text of his change* was refused by the context check and
        // which rule fired, so that is what the strip prints. The raw line is kept verbatim
        // under «Технические детали».
        const raw = refusalDetail(
          dispatched?.response.error ??
            changeRecord?.classification?.detail ??
            changeRecord?.classification?.reason ??
            ""
        );
        const classified = classifyChangeRefusal(raw);
        refusal =
          classified.kind === "context_refused"
            ? t(lang, "projects.change.refused.context")
            : t(lang, "projects.change.refused.unknown");
        refusalRule = classified.rule;
        refusalDetailText = raw;
        tone = "warn";
      } else if (advanced && wroteFiles) {
        tone = "ok";
      } else {
        tone = "warn";
      }
    }
    return {
      title,
      stage,
      tone,
      final,
      refusal,
      refusalRule,
      refusalDetail: refusalDetailText,
      files: changeOutcome?.files ?? [],
    };
  }, [
    changeStarting,
    changeRunId,
    changeRecord,
    changeOutcome,
    dispatched,
    lang,
  ]);

  /**
   * IS THE ONE ACTION STILL RUNNING?
   *
   * `finished` is the terminal fact: the outcome block exists, a refusal came back from
   * either request, or the polled run record says it is terminal. Until then the button
   * stays off (`changeLocked`), which is what makes a double click produce exactly ONE
   * dispatch instead of two versions.
   */
  const changeFinished =
    changeOutcome !== null ||
    dispatchFailure !== null ||
    changeFailure !== null ||
    (changeRecord !== null && isTerminalRecord(changeRecord));
  const changeLocked = changeInFlight({
    starting: changeStarting,
    runId: changeRunId,
    finished: changeFinished,
  });

  /** Files this change really wrote; 0 stays 0 instead of becoming "unknown". */
  const changeFilesWritten = changeOutcome === null
    ? 0
    : Math.max(Number(changeOutcome.filesCreated ?? 0), changeOutcome.files.length);
  /**
   * WHAT THE BLOCK RENDERS: one press, then a different button; a zero-file change is
   * never a success; mock is loud. The rules live in `ui/src/change-state.ts` and are
   * executed from Node by `tests/test_flow_invariants.py`.
   */
  const controls = changeControls({
    empty: change.trim() === "",
    inFlight: changeStarting || changeLocked,
    finished: changeFinished,
    filesWritten: changeFilesWritten,
    version: changeOutcome?.stagedVersion ?? 0,
    mocked: effectiveMock,
    realProvider,
    armed: changeArmed,
  });
  /**
   * «Что сделал Forge», from the model's own answer and the files it really wrote.
   * The summary is `model_intent.summary`; the files are the version manifest; the two
   * answer lines come from the backend's screened excerpt. Nothing is invented here.
   */
  const forgeDid = useMemo(
    () =>
      whatForgeDid({
        summary:
          dispatched !== null
            ? (dispatched.response.model_intent as Record<string, unknown> | undefined)?.summary
            : undefined,
        files: changeStrip?.files ?? [],
        answerExcerpt: dispatched?.response.answer_excerpt,
      }),
    [dispatched, changeStrip]
  );
  /** «Внести ещё изменения»: the only way the form comes back after a change. */
  const rearmChange = () => {
    setChangeArmed(true);
    setChangeError("");
    setChangeFailure(null);
    setDispatchFailure(null);
    // A new change starts with its own screenshot, not with the previous one's: the chip
    // would otherwise silently re-attach a picture of a problem that is already fixed.
    clearAttachments();
  };
  /**
   * «Повторить с реальной моделью»: switch to the best available non-mock provider and
   * dispatch the same words again, in one press. The backend keeps the same version
   * number (idempotency by change text), so a retry never breeds another version.
   */
  const retryWithRealModel = () => {
    if (realProvider === "") return;
    setProvider(realProvider);
    setChangeArmed(true);
    setChangeError("");
    setChangeFailure(null);
    setDispatchFailure(null);
    setDispatched(null);
    setChangeOutcome(null);
    setChangeRunId("");
    setChangeRecord(null);
    void startChange(realProvider);
  };

  return (
    <div className="content">
      {/* The project screen is its own screen: it replaces the list and starts at the
          top, so the card (title, status word, files, folder, actions) is fully visible
          without scrolling. «← Назад к проектам» and the browser's Back both return. */}
      {view.view === "detail" ? (
        <div className="row between" style={{ marginBottom: 18, alignItems: "flex-start" }} data-testid="project-detail-head">
          <div style={{ minWidth: 0 }}>
            <span data-testid="project-detail-back">
              <Button size="small" tone="ghost" onClick={() => routerRef.current?.showList()}>
                {t(lang, "projects.detail.back")}
              </Button>
            </span>
            <h1 style={{ margin: "10px 0 0", fontSize: "var(--t-title)" }} data-testid="project-detail-title">
              {text(project?.name, t(lang, "projects.card.title"))}
            </h1>
            <div className="small muted" style={{ marginTop: 4, wordBreak: "break-all" }}>
              {t(lang, "projects.detail.hint").replace("{hash}", projectHash(selectedId))}
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
      ) : (
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
      )}

      {!live ? <div className="notice warn" style={{ marginBottom: 16 }}>{t(lang, "state.offline")}</div> : null}

      {/* --------------------------------------------------------------- the list */}
      {view.view === "list" ? (
      <>
      <div data-testid="projects-list">
      <Card className="rise">
        <CardHead
          title={t(lang, "projects.list.title")}
          hint={`/api/projects/lifecycle · ${slice.drawn} / ${summaries.length}`}
          action={
            <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
              {/* Transparency, not a hidden cut: the button names how many rows the
                  default view keeps out of sight, and brings them back unchanged. */}
              {slice.hidden > 0 ? (
                <span data-testid="projects-show-all">
                  <Button
                    size="small"
                    tone={showAllProjects ? "ghost" : "default"}
                    onClick={() => {
                      setShowAllProjects((value) => !value);
                      setListShown(0);
                    }}
                  >
                    {showAllProjects
                      ? t(lang, "projects.list.only_visible")
                      : t(lang, "projects.list.show_all").replace("{n}", String(slice.hidden))}
                  </Button>
                </span>
              ) : null}
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
                // Whether «Открыть папку» can act is decided from the record's own facts:
                // a project that holds no files (or has no id at all) is refused by the
                // backend, so the button is off here with the same sentence, never a
                // click that would do nothing.
                const canOpenFolder =
                  id !== "" && typeof project.file_count === "number" && project.file_count > 0;
                const folderReason =
                  id === "" ? t(lang, "projects.delete.no_id") : t(lang, "projects.row.no_files");
                // A launch needs one file on disk and a record that is not archived. The
                // list cannot know the entry point, so a click on a launchable row may
                // still be refused *by the backend* -- and that sentence is printed under
                // the row. What the row can know, it refuses here.
                const canLaunch = canOpenFolder && !archived;
                const launchReason = archived
                  ? t(lang, "projects.badge.archived")
                  : folderReason;
                return (
                  <Fragment key={id}>
                  <tr
                    className={id === selectedId ? "selected" : ""}
                    style={{ cursor: "pointer" }}
                    onClick={() => openProject(id)}
                    data-testid="project-row"
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
                      {/* The three entrances, all real. «Запустить приложение» is the
                          primary one: it runs the project and opens the result in its own
                          window. «Подробнее» is the old «Открыть проект» and now says what
                          it does -- it shows this card. «Открыть папку» really opens the
                          folder through POST /api/projects/{id}/open and prints the
                          backend's answer ("explorer.EXE · PID …") right under this row.
                          The wrapping span stops these clicks from also firing the row's
                          navigation. */}
                      <span
                        className="row"
                        style={{ gap: 6, flexWrap: "wrap" }}
                        onClick={(event) => event.stopPropagation()}
                      >
                        <span data-testid="project-row-launch">
                          <Button
                            size="small"
                            tone="primary"
                            disabled={!canLaunch || rowLaunching === id}
                            disabledReason={canLaunch ? undefined : launchReason}
                            onClick={() => void launchRow(id)}
                          >
                            {rowLaunching === id
                              ? t(lang, "state.loading")
                              : t(lang, "projects.action.launch")}
                          </Button>
                        </span>
                        <span data-testid="project-row-open">
                          <Button size="small" tone="ghost" onClick={() => openProject(id)}>
                            {t(lang, "projects.action.open")}
                          </Button>
                        </span>
                        <span data-testid="project-row-open-folder">
                          <Button
                            size="small"
                            tone="ghost"
                            disabled={!canOpenFolder || rowOpening === id}
                            disabledReason={canOpenFolder ? undefined : folderReason}
                            onClick={() => void openRowFolder(id)}
                          >
                            {rowOpening === id
                              ? t(lang, "state.loading")
                              : t(lang, "projects.action.open_folder")}
                          </Button>
                        </span>
                      </span>
                      {/* The visible half of a disabled control's reason: the tooltip
                          carries the same sentence, but a screenshot has to show why the
                          button is off, exactly as the card's disk block does. */}
                      {!canLaunch ? (
                        <div className="small muted" data-testid="project-row-launch-why">
                          {t(lang, "projects.action.launch")}: {launchReason}
                        </div>
                      ) : null}
                      {!canOpenFolder ? (
                        <div className="small muted" data-testid="project-row-open-folder-why">
                          {t(lang, "projects.action.open_folder")}: {folderReason}
                        </div>
                      ) : null}
                    </td>
                  </tr>
                  {rowNotice !== null && rowNotice.projectId === id ? (
                    <tr data-testid="project-row-open-notice">
                      <td colSpan={7}>
                        <div className="notice">{rowNotice.text}</div>
                      </td>
                    </tr>
                  ) : null}
                  {rowFailure !== null && rowFailure.projectId === id ? (
                    <tr data-testid="project-row-open-failure">
                      <td colSpan={7}>
                        <Refusal lang={lang} failure={rowFailure.failure} />
                      </td>
                    </tr>
                  ) : null}
                  {rowLaunchNotice !== null && rowLaunchNotice.projectId === id ? (
                    <tr data-testid="project-row-launch-notice">
                      <td colSpan={7}>
                        <div className="notice">
                          <div style={{ whiteSpace: "pre-wrap" }}>{rowLaunchNotice.text}</div>
                          <div className="row" style={{ gap: 8, marginTop: 6 }}>
                            <span data-testid="project-row-launch-again">
                              <Button
                                size="small"
                                tone="ghost"
                                disabled={rowLaunching === id}
                                onClick={() => void launchRow(id)}
                              >
                                {t(lang, "projects.action.launch_again")}
                              </Button>
                            </span>
                          </div>
                        </div>
                      </td>
                    </tr>
                  ) : null}
                  {rowLaunchFailure !== null && rowLaunchFailure.projectId === id ? (
                    <tr data-testid="project-row-launch-failure">
                      <td colSpan={7}>
                        <Refusal lang={lang} failure={rowLaunchFailure.failure} />
                      </td>
                    </tr>
                  ) : null}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        )}

        {/* A bounded view, not a hidden one: the heading keeps the real count, this line
            states how much of it is drawn, and «Показать ещё» reveals the next page --
            exactly what the trash already does. The whole listing is still what
            `GET /api/projects/lifecycle` answered. */}
        {slice.hasMore ? (
          <div
            className="row"
            data-testid="projects-list-more"
            style={{ gap: 8, flexWrap: "wrap", marginTop: 8, alignItems: "center" }}
          >
            <Button
              size="small"
              tone="ghost"
              onClick={() => setListShown(slice.drawn + PROJECT_LIST_PAGE_SIZE)}
            >
              {t(lang, "projects.trash.more")
                .replace("{shown}", String(slice.drawn))
                .replace("{total}", String(slice.tabTotal))}
            </Button>
            <span className="small muted" data-testid="projects-list-bounded">
              {t(lang, "projects.list.bounded")
                .replace("{shown}", String(slice.drawn))
                .replace("{total}", String(slice.tabTotal))}
            </span>
          </div>
        ) : null}

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
          {slice.scope === "visible-root" && slice.hidden > 0 ? (
            <div data-testid="projects-list-scope">
              {t(lang, "projects.list.scope").replace("{n}", String(slice.hidden))}
            </div>
          ) : null}
          {t(lang, "projects.list.hint")}
        </div>
      </Card>
      </div>

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
          <>
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
              {trashRows.map((item) => {
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
          {/* A bounded view, not a hidden one: the heading keeps the real count, this
              line states how much of it is drawn, and «Показать ещё» reveals the next
              page. The whole listing is still what the backend answered. */}
          {trashCount > trashRows.length ? (
            <div
              className="row"
              data-testid="projects-trash-more"
              style={{ gap: 8, flexWrap: "wrap", marginTop: 8, alignItems: "center" }}
            >
              <Button
                size="small"
                tone="ghost"
                onClick={() => setTrashShown(trashRows.length + TRASH_PAGE_SIZE)}
              >
                {t(lang, "projects.trash.more")
                  .replace("{shown}", String(trashRows.length))
                  .replace("{total}", String(trashCount))}
              </Button>
              <span className="small muted" data-testid="projects-trash-bounded">
                {t(lang, "projects.trash.bounded")
                  .replace("{shown}", String(trashRows.length))
                  .replace("{total}", String(trashCount))}
              </span>
            </div>
          ) : null}
          </>
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
      </>
      ) : null}

      {/* ------------------------------------------------------------- the card */}
      {view.view === "detail" ? (
      <div data-testid="project-detail">
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
          attachments={attachments}
          attachmentBusy={attachmentBusy}
          attachmentError={attachmentError}
          onPickAttachments={(files) => void addAttachments(files)}
          onRemoveAttachment={removeAttachment}
          onStartChange={() => void startChange()}
          changeFailure={changeFailure}
          changeLocked={changeLocked}
          changeStrip={changeStrip}
          changeStartedAt={changeObservedAt}
          changeEndedAt={stampMs(changeRecord?.updated_at)}
          dispatched={dispatched}
          dispatchFailure={dispatchFailure}
          provider={provider}
          onProvider={setProvider}
          model={model}
          onModel={setModel}
          modelChoices={modelChoices}
          mockSelected={mockSelected}
          controls={controls}
          forgeDid={forgeDid}
          onRearmChange={rearmChange}
          onRetryWithRealModel={retryWithRealModel}
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
          onLaunch={() => void launchSelected()}
          onLaunchFallback={() => void launchSelected(true)}
          launchBusy={launchBusy}
          launchNotice={launchNotice}
          launchFailure={launchFailure}
          repairFacts={repairFacts}
          repairAttempts={repairAttempts}
          repairState={repairState}
          repairChat={repairChat}
          onRepairChat={setRepairChat}
          onRepairSend={repairSend}
          onRepairAuto={repairAuto}
          repairBusy={repairBusy}
          onDelete={() => void deleteSelected()}
          open={openBlock}
          folder={folderBlock}
          onOpenTarget={(target) => void openTarget(target)}
          openingTarget={openingTarget}
          diskNotice={diskNotice}
          diskFailure={diskFailure}
          onMoveToVisible={() => void moveToVisible()}
          movingToVisible={movingToVisible}
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
      </div>
      ) : null}

      {/* Where a project comes from, stated where the owner looks for a "create" button. */}
      {view.view === "list" && list.status === "ready" && summaries.length === 0 ? (
        <Card className="rise d1" tight>
          <div className="small muted">{t(lang, "projects.create.via_discovery")}</div>
        </Card>
      ) : null}
    </div>
  );
};
