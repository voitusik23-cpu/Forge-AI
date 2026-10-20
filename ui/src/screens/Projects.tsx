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

import { useCallback, useEffect, useMemo, useState } from "react";
import { Badge, Button, Card, CardHead, Field, StatusDot } from "../components/Primitives";
import { IconRefresh } from "../components/Icons";
import { t, type Lang } from "../i18n";
import type { BackendState } from "../api/client";
import {
  OFFLINE_PROVIDER,
  acceptanceLabel,
  archiveProject,
  catalogueModelsFor,
  createProjectVersion,
  dispatchTask,
  loadProjectDetail,
  loadProjectFiles,
  loadProjectHistory,
  loadProjectLifecycle,
  loadRunRecord,
  restoreProject,
  verificationLabel,
  verifyProject,
  type DispatchResult,
  type Failure,
  type LifecycleArtifact,
  type LifecycleDetail,
  type LifecycleHistory,
  type LifecycleNewVersion,
  type LifecycleProject,
  type LifecycleSummary,
  type LifecycleVersion,
  type RunRecord,
} from "../api/forge";

const text = (value: unknown, fallback = "—"): string =>
  value === undefined || value === null || value === "" ? fallback : String(value);

const yes = (lang: Lang): string => (lang === "ru" ? "да" : "yes");
const no = (lang: Lang): string => (lang === "ru" ? "нет" : "no");

const bytesLabel = (size: unknown): string => {
  const value = typeof size === "number" && Number.isFinite(size) ? size : null;
  if (value === null) return "—";
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KiB`;
  return `${(value / (1024 * 1024)).toFixed(2)} MiB`;
};

/** The status vocabulary the backend publishes, in the order the product uses it. */
const STATUS_ORDER = [
  "DISCOVERY",
  "NEEDS_CLARIFICATION",
  "READY_FOR_PLANNING",
  "PLANNED",
  "WAITING_FOR_CONFIRMATION",
  "RUNNING",
  "REVIEWING",
  "NEEDS_CHANGES",
  "COMPLETED",
  "FAILED",
  "ARCHIVED",
] as const;

/**
 * The tone a status word is drawn with.
 *
 * The word is always printed next to the colour, so a colour-blind reader loses nothing; the
 * tone only adds emphasis. An unknown word keeps the neutral tone and is printed verbatim.
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

const statusHint = (lang: Lang, status: unknown): string => {
  const word = String(status ?? "").toUpperCase();
  const known = (STATUS_ORDER as readonly string[]).includes(word);
  if (!known) return lang === "ru" ? "неизвестный статус" : "unknown status";
  return t(lang, `projects.status.${word}`);
};

type Load<T> = { status: "loading" } | { status: "ready"; value: T } | { status: "failed"; failure: Failure };

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

const ProjectCard = ({
  lang,
  backend,
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
}: {
  lang: Lang;
  backend: BackendState;
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
}) => {
  const status = String(project.status ?? "");
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
  const canVerify = status === "COMPLETED" || status === "NEEDS_CHANGES";
  const verifyReason = canVerify
    ? t(lang, "projects.verify.hint")
    : t(lang, "projects.verify.disabled").replace("{status}", text(status));
  const outcome = String(current?.verification ?? "");
  const acceptance = String(current?.acceptance ?? "");

  // The run's own classification is the only place a reason exists; the project record
  // deliberately stores verdicts, not prose. When the record cannot be read the block says so.
  const classification = runRecord?.classification ?? null;

  return (
    <Card className="rise d1">
      <CardHead
        title={text(project.name, t(lang, "projects.card.untitled"))}
        hint={`${text(project.project_id)} · ${t(lang, "projects.card.hint")}`}
        action={
          <span className="row" style={{ gap: 6, flexWrap: "wrap" }}>
            <Badge tone={statusTone(status)}>{text(status, t(lang, "runs.unknown"))}</Badge>
            {archived ? <Badge tone="neutral">{t(lang, "projects.badge.archived")}</Badge> : null}
          </span>
        }
      />

      <div className="stack" style={{ gap: 16 }}>
        <div className="small muted">{statusHint(lang, status)}</div>

        {actionFailure !== null ? <Refusal lang={lang} failure={actionFailure} /> : null}
        {actionNotice !== "" ? <div className="notice">{actionNotice}</div> : null}

        {/* ---------------------------------------------------------------- where */}
        <div>
          <div className="label">{t(lang, "projects.where.title")}</div>
          <div className="stack mono small" style={{ gap: 4, marginTop: 8 }}>
            <span>Workspace: {text(project.workspace_id ?? project.project_id)}</span>
            <span>Project folder: {text(project.project_path)}</span>
            <span>Files: {typeof current?.file_count === "number" ? current.file_count : files.length}</span>
            <span>Last version: {currentVersion === 0 ? "—" : `v${currentVersion}`}</span>
          </div>
          <div className="small muted" style={{ marginTop: 6 }}>
            {t(lang, "projects.where.note")}
          </div>
        </div>

        {/* ----------------------------------------------------------------- record */}
        <div>
          <div className="label">{t(lang, "projects.record.title")}</div>
          <Facts
            items={[
              [t(lang, "projects.col.id"), text(project.project_id)],
              [t(lang, "projects.col.status"), text(status)],
              [t(lang, "projects.col.archived"), archived ? yes(lang) : no(lang)],
              [t(lang, "projects.col.prevstatus"), text(project.previous_status)],
              [t(lang, "projects.col.created"), text(project.created_at)],
              [t(lang, "projects.col.updated"), text(project.updated_at)],
              [t(lang, "projects.col.lastrun"), text(project.last_run_at)],
              [t(lang, "projects.col.version"), currentVersion === 0 ? "—" : `v${currentVersion}`],
              [t(lang, "projects.col.plan"), text(project.plan_ref)],
              [t(lang, "projects.col.workspace"), text(project.workspace_id)],
            ]}
          />
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
        <div>
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

        {/* ---------------------------------------------------------------- outcome */}
        <div>
          <div className="label">{t(lang, "projects.outcome.title")}</div>
          {status === "COMPLETED" ? (
            <div className="stack" style={{ gap: 8, marginTop: 8 }}>
              <div className="notice ok">{t(lang, "projects.outcome.completed")}</div>
              <Facts
                items={[
                  [t(lang, "projects.outcome.files"), `${files.length} · ${text(project.project_path)}`],
                  [t(lang, "projects.outcome.verify"), verificationLabel(lang, outcome)],
                  [t(lang, "projects.outcome.accept"), acceptanceLabel(lang, acceptance)],
                  [t(lang, "projects.outcome.route"), `${text(current?.provider)} · ${text(current?.model)}`],
                  [
                    t(lang, "projects.outcome.duration"),
                    dispatched && typeof dispatched.response.duration_seconds === "number"
                      ? `${dispatched.response.duration_seconds.toFixed(3)} s`
                      : t(lang, "projects.outcome.duration.unpublished"),
                  ],
                ]}
              />
              <div className="small muted">{t(lang, "projects.outcome.next")}</div>
            </div>
          ) : status === "NEEDS_CHANGES" ? (
            <div className="stack" style={{ gap: 8, marginTop: 8 }}>
              <div className="notice warn">{t(lang, "projects.outcome.needs_changes")}</div>
              <Facts
                items={[
                  [t(lang, "projects.outcome.verify"), verificationLabel(lang, outcome)],
                  [t(lang, "projects.outcome.accept"), acceptanceLabel(lang, acceptance)],
                  [t(lang, "projects.outcome.route"), `${text(current?.provider)} · ${text(current?.model)}`],
                  [t(lang, "projects.outcome.files"), String(files.length)],
                ]}
              />
              <div>
                <div className="label">{t(lang, "projects.outcome.why")}</div>
                <div className="small" style={{ marginTop: 6, overflowWrap: "anywhere" }}>
                  {classification
                    ? `${text(classification.outcome)} · ${text(classification.reason)} · ${text(classification.detail)}`
                    : runLoading
                      ? t(lang, "projects.outcome.reason.loading")
                      : runFailure !== null
                        ? `${t(lang, "projects.outcome.reason.unreadable")}: HTTP ${runFailure.status}`
                        : t(lang, "projects.outcome.reason.absent")}
                </div>
                {classification?.evidence && classification.evidence.length > 0 ? (
                  <div className="row" style={{ gap: 6, flexWrap: "wrap", marginTop: 6 }}>
                    {classification.evidence.map((item) => (
                      <Badge key={item} tone="neutral">
                        {item}
                      </Badge>
                    ))}
                  </div>
                ) : null}
              </div>
              <div className="small muted">{t(lang, "projects.outcome.needs_changes.next")}</div>
            </div>
          ) : status === "FAILED" ? (
            <div className="stack" style={{ gap: 8, marginTop: 8 }}>
              <div className="notice danger">{t(lang, "projects.outcome.failed")}</div>
              <div className="small" style={{ overflowWrap: "anywhere" }}>
                {classification
                  ? `${text(classification.outcome)} · ${text(classification.reason)} · ${text(classification.detail)}`
                  : runLoading
                    ? t(lang, "projects.outcome.reason.loading")
                    : t(lang, "projects.outcome.failed.safe")}
              </div>
              <div className="small muted">{t(lang, "projects.outcome.failed.next")}</div>
            </div>
          ) : (
            <div className="small muted" style={{ marginTop: 8 }}>
              {t(lang, "projects.outcome.pending").replace("{status}", text(status))}
            </div>
          )}
        </div>

        {/* --------------------------------------------------------------- actions */}
        <div className="row" style={{ gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          <Button size="small" disabled={busy === "verify"} onClick={onVerify}>
            {busy === "verify" ? t(lang, "projects.verify.running") : t(lang, "projects.action.verify")}
          </Button>
          <Button size="small" tone="ghost" onClick={onArchive}>
            {busy === "archive" ? t(lang, "projects.archive.running") : t(lang, "projects.action.archive")}
          </Button>
          <Button size="small" tone="ghost" onClick={onRestore}>
            {busy === "restore" ? t(lang, "projects.restore.running") : t(lang, "projects.action.restore")}
          </Button>
          <Button size="small" tone="ghost" onClick={onHistory}>
            {t(lang, "projects.action.history")}
          </Button>
        </div>
        {/* Every limitation is stated where the action is, not in a manual nobody opens. */}
        <div className="small muted">{verifyReason}</div>

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
}: {
  lang: Lang;
  backend: BackendState;
  onRefresh: () => void;
  onHelp: () => void;
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
            <div className="segment">
              <button className={filter === "active" ? "on" : ""} onClick={() => setFilter("active")}>
                {t(lang, "projects.filter.active")}
              </button>
              <button className={filter === "archive" ? "on" : ""} onClick={() => setFilter("archive")}>
                {t(lang, "projects.filter.archive")}
              </button>
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
                    onClick={() => setSelectedId(id)}
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
                      <Button size="small" onClick={() => setSelectedId(id)}>
                        {t(lang, "projects.action.open")}
                      </Button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
        <div className="small muted" style={{ marginTop: 8 }}>
          {t(lang, "projects.list.hint")}
        </div>
      </Card>

      {/* ------------------------------------------------------------- the card */}
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
        />
      ) : null}

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
