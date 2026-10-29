// Кузница — the FORGE AI desktop workspace.
//
// The workspace is **questions first**. One screen, and the order on it is the order of the work:
//
//   0. «Как это работает» — five short lines, collapsed by default, for someone who has never seen
//      the app. It says the one thing the owner had to learn the hard way: the file format is
//      Forge's job, not the user's;
//   1. «Задача» (top of the page) — the idea, then the clarification card that replaces the idea
//      form the moment the first submit returned questions, then the specification, then the launch
//      step (provider · model · cost_tier · estimated_cost, the write switch and the run button);
//   2. «Выполнение» — GET /api/runs/{run_id}, polled every 1.75 s until the record is terminal. The
//      nine chips and the raw state word live here, the technical facts are collapsed;
//   3. «Результат» — ONE plain-language sentence derived from the facts (files written? questions
//      asked? a plan instead of files?), its action, and only then the collapsed «Технические
//      детали»: the raw `state`/`status`/`run_id`/`event_count`/`classification`, the English
//      `code_write.reason`, the diff and the raw output. `COMPLETED` is never the headline, because
//      the word describes the dispatch finishing, not a project existing;
//   4. «Артефакты и изменения» — only once a run exists (there is no artifact route in
//      app/api/server.py, and the card says so instead of drawing an invented list);
//   5. «Проекты» and «Настройки» in the right-hand column. The agent list lives in «Провайдеры»,
//      which is the full diagnostics hub, so the workspace is not half-filled with it.
//
// Every number on this screen is copied from a response; none is computed to look like progress.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Badge, Button, Card, CardHead, Field, StatusDot } from "../components/Primitives";
import {
  ClarificationCard,
  draftHasAnswer,
  emptyAnswerDraft,
  type AnswerDraft,
} from "../components/ClarificationCard";
import { IconAnvil, IconPlay, IconRefresh } from "../components/Icons";
import { AliveBar } from "../components/AliveBar";
import { RunProgress } from "../components/RunProgress";
import { RouteLine, complexityWord, tierWord } from "../components/RouteLine";
import { EvidenceBlock } from "../components/EvidenceBlock";
import { t, type Lang } from "../i18n";
import { selectRunProject } from "../project-selection.js";
import {
  aliveStageKey,
  stepStates,
  stampMs,
  type StepId,
  type StepState,
} from "../progress";
import { deriveRunHeadline, answerAskedQuestions, type ResultActionId, type ResultHeadline } from "../forge-result";
import type { BackendState } from "../api/client";
import {
  TASK_CATEGORIES,
  cancelDiscovery,
  catalogueEntryFor,
  catalogueModelsFor,
  codeWriteOf,
  confirmDiscovery,
  dispatchTask,
  estimateRoute,
  isTerminalRecord,
  loadDiscoverySpecification,
  loadProjectLifecycle,
  loadProjects,
  loadRunRecord,
  loadUiDefaults,
  sendDiscoveryAnswers,
  skipDiscoveryClarification,
  startDiscovery,
  type CodeWriteResult,
  type DiscoverySessionPayload,
  type DiscoverySpecification,
  type DiscoverySpecificationPayload,
  type DispatchResult,
  type Failure,
  type ModelChoice,
  type ProjectInfo,
  type RunRecord,
  type TaskCategoryValue,
} from "../api/forge";

// Faster than the 15 s backend poll: a run's own state is what the user watches here.
const POLL_INTERVAL_MS = 1750;
const POLL_INTERVAL_LABEL = (POLL_INTERVAL_MS / 1000).toFixed(2).replace(/0$/, "");

/**
 * The provider select's own value for "nothing is chosen, Forge decides".
 *
 * It is not `""`: an empty string is what the request omits, and the form needs a value it
 * can display. `__auto__` can never collide with a provider id (ids are `[a-z0-9_-]`).
 */
const AUTO_ROUTE = "__auto__";

//: The preview is debounced so typing is not a request per keystroke. Nothing is
//: dispatched by it: the route answers the classification from the pure routing function.
const ROUTE_PREVIEW_DELAY_MS = 350;

type RoutePreview =
  | { status: "empty" }
  | { status: "pending" }
  | { status: "ready"; choice: ModelChoice }
  | { status: "failed" };

const text = (value: unknown, fallback = "—"): string =>
  value === undefined || value === null || value === "" ? fallback : String(value);

const fixed = (value: unknown, digits: number): string =>
  typeof value === "number" && Number.isFinite(value) ? value.toFixed(digits) : "—";

const yesNo = (lang: Lang, value: unknown): string => {
  if (typeof value !== "boolean") return t(lang, "runs.unknown");
  return value ? (lang === "ru" ? "да" : "yes") : (lang === "ru" ? "нет" : "no");
};

/** The label of each action a headline may offer. Every word lives in `i18n.ts`. */
const RESULT_ACTION_LABELS: Record<ResultActionId, string> = {
  open_files: "forge.result.action.open_files",
  launch_local: "forge.result.action.launch_local",
  verify: "forge.result.action.verify",
  answer: "forge.result.action.answer",
  retry: "forge.result.action.retry",
};

/**
 * One headline sentence, with its `{n}` count and its `{reason}` sentence filled in.
 *
 * The reason is itself an i18n key (`forge.reason.*`), so no raw English writer reason and no
 * backend enum ever reaches this text.
 */
const headlineText = (lang: Lang, headline: ResultHeadline): string => {
  let sentence = t(lang, headline.key);
  if (headline.params.n !== undefined) {
    sentence = sentence.replace("{n}", headline.params.n);
  }
  if (headline.params.reason !== undefined) {
    sentence = sentence.replace("{reason}", t(lang, headline.params.reason));
  }
  return sentence;
};

const newIdempotencyKey = (): string =>
  typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `forge-${Date.now()}-${Math.random().toString(16).slice(2)}`;

/**
 * What a clarification dispatch carries: a run id and the answers.
 *
 * Two runs are involved and they are never the same one. «Ответить» sends the run whose card asked
 * (the backend reads *that* run's context and adds the answers to it); the answers kept for
 * «Повторить» are named by the run that was just dispatched (the one whose card offers the retry),
 * because that is the record the backend can read the answers back from.
 */
type ClarificationDispatch = {
  runId: string;
  answers: Array<{ question: string; answer: string }>;
};

type ProjectsState =
  | { status: "loading" }
  | { status: "ready"; items: ProjectInfo[] }
  | { status: "failed"; failure: Failure };

type DispatchState =
  | { status: "idle" }
  | { status: "pending" }
  | { status: "sent"; result: DispatchResult }
  | { status: "failed"; failure: Failure };

/** The four steps of the Кузница flow. Each one is a real request, not a drawing. */
type DiscoveryPhase = StepId;

const DISCOVERY_STEPS: Array<{ id: DiscoveryPhase; labelKey: string }> = [
  { id: "idea", labelKey: "forge.discovery.step.idea" },
  { id: "clarify", labelKey: "forge.discovery.step.clarify" },
  { id: "spec", labelKey: "forge.discovery.step.spec" },
  { id: "run", labelKey: "forge.discovery.step.run" },
];

/** The specification fields, in the order the brief asks for them. */
type SpecKey = "goal" | "project_type" | "users" | "core_features" | "platform" | "integrations" |
  "data_storage" | "auth" | "notifications" | "constraints" | "acceptance_criteria" | "priorities";

const SPEC_FIELDS: Array<{ key: SpecKey; labelKey: string }> = [
  { key: "goal", labelKey: "forge.discovery.spec.goal" },
  { key: "project_type", labelKey: "forge.discovery.spec.project_type" },
  { key: "users", labelKey: "forge.discovery.spec.users" },
  { key: "core_features", labelKey: "forge.discovery.spec.core_features" },
  { key: "platform", labelKey: "forge.discovery.spec.platform" },
  { key: "integrations", labelKey: "forge.discovery.spec.integrations" },
  { key: "data_storage", labelKey: "forge.discovery.spec.data_storage" },
  { key: "auth", labelKey: "forge.discovery.spec.auth" },
  { key: "notifications", labelKey: "forge.discovery.spec.notifications" },
  { key: "constraints", labelKey: "forge.discovery.spec.constraints" },
  { key: "acceptance_criteria", labelKey: "forge.discovery.spec.acceptance_criteria" },
  { key: "priorities", labelKey: "forge.discovery.spec.priorities" },
];

/** The human name of one specification field, for the chosen/auto-selected lists. */
const specLabel = (lang: Lang, field: string): string => {
  const entry = SPEC_FIELDS.find((candidate) => candidate.key === field);
  return entry ? t(lang, entry.labelKey) : field;
};

/** One `field -> values` record rendered as «поле: значение; значение». */
const OriginList = ({
  lang,
  origins,
  empty,
}: {
  lang: Lang;
  origins: Array<{ field?: string; values?: string[] }>;
  empty: string;
}) => {
  const rows = origins.filter((origin) => (origin.values ?? []).length > 0);
  if (rows.length === 0) return <div className="small muted">{empty}</div>;
  return (
    <ul className="forge-spec">
      {rows.map((origin) => (
        <li className="forge-spec-row" key={String(origin.field ?? "")}>
          <span className="label">{specLabel(lang, String(origin.field ?? ""))}</span>
          <span>{(origin.values ?? []).join("; ")}</span>
        </li>
      ))}
    </ul>
  );
};

/** The one honest sentence per failure, with the status kept visible next to it. */
const failureLabel = (lang: Lang, failure: Failure): string => {
  if (failure.kind === "unauthorized") return t(lang, "forge.state.unauthorized");
  if (failure.kind === "disconnected") return t(lang, "forge.state.disconnected");
  const code = failure.code ?? "";
  // A paid model the caller did not explicitly confirm: the Core's own sentence says what would
  // have been spent, so it is shown verbatim instead of being summarised away.
  if (failure.status === 409 && code === "paid_model_requires_confirmation")
    return failure.detail;
  if (failure.status === 409 && code === "discovery_not_confirmed")
    return t(lang, "forge.discovery.not_confirmed");
  if (failure.status === 409 && code === "discovery_not_ready")
    return t(lang, "forge.discovery.not_ready");
  if (failure.status === 409 && code === "discovery_state_conflict")
    return t(lang, "forge.discovery.state_conflict");
  if (failure.status === 404 && code === "unknown_discovery_session")
    return t(lang, "forge.discovery.unknown_session");
  if (failure.status === 400 && code === "invalid_discovery_answer")
    return t(lang, "forge.discovery.invalid_answer");
  return `${t(lang, "forge.state.error")}: ${failure.path} → HTTP ${failure.status}`;
};

/** What the refusal means for the next click, when the code says something specific. */
const failureHint = (lang: Lang, failure: Failure): string => {
  const code = failure.code ?? "";
  if (failure.status === 409 && code === "paid_model_requires_confirmation")
    return t(lang, "forge.launch.paid.refusal");
  if (failure.status === 409 && code === "discovery_not_confirmed")
    return t(lang, "forge.discovery.not_confirmed.detail");
  if (failure.status === 409 && code === "discovery_not_ready")
    return t(lang, "forge.discovery.not_ready.detail");
  if (failure.status === 409 && code === "discovery_state_conflict")
    return t(lang, "forge.discovery.state_conflict.detail");
  if (failure.status === 404 && code === "unknown_discovery_session")
    return t(lang, "forge.discovery.unknown_session.detail");
  return "";
};

/**
 * What the confirmation produced, in the owner's words.
 *
 * Three facts, never one: the specification is confirmed; the project has a short name and a
 * real folder; and, when the configured desktop folder could not be written to, which folder
 * was used instead and why. The last one is the sentence that turns a silent move into an
 * explained one -- the failure this replaces was a 409 with no way forward.
 */
const confirmationNotice = (lang: Lang, payload: DiscoverySessionPayload): string => {
  const parts: string[] = [t(lang, "forge.discovery.spec.confirmed")];
  const name = String(payload.project?.name ?? "").trim();
  const folder = String(payload.project?.project_path ?? "").trim();
  if (name !== "") {
    parts.push(
      t(lang, "forge.project.created")
        .replace("{name}", name)
        .replace("{folder}", folder !== "" ? folder : "—")
    );
  }
  const root = payload.project_root;
  if (root?.fallback) {
    parts.push(
      t(lang, "forge.project.root.fallback")
        .replace("{preferred}", String(root.preferred ?? "—"))
        .replace("{root}", String(root.root ?? "—"))
    );
  }
  return parts.join(" · ");
};

/** One honest message per failure kind: 401/403, no answer at all, or an answered error. */
const FailureNotice = ({
  lang,
  failure,
  onRetry,
}: {
  lang: Lang;
  failure: Failure;
  onRetry?: () => void;
}) => {
  const hint = failureHint(lang, failure);
  return (
    <div className={`notice ${failure.kind === "error" ? "warn" : "danger"}`}>
      <div>{failureLabel(lang, failure)}</div>
      {failure.kind === "error" ? (
        <div className="small mono" style={{ marginTop: 6, wordBreak: "break-word" }}>
          HTTP {failure.status}
          {failure.code ? ` · ${failure.code}` : ""} · {failure.path}
        </div>
      ) : null}
      {hint !== "" ? (
        <div className="small" style={{ marginTop: 6 }}>
          {hint}
        </div>
      ) : null}
      {failure.kind === "error" || failure.kind === "disconnected" ? (
        // The detail is printed only when it says something the label does not: for a refusal whose
        // message *is* the backend's own sentence (the paid gate), repeating it would read as two
        // different failures.
        failure.detail && failure.detail !== failureLabel(lang, failure) ? (
          <div className="small mono" style={{ marginTop: 6, whiteSpace: "pre-wrap" }}>
            {failure.detail}
          </div>
        ) : null
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
 * The step rail: which step is current, which ones are really complete, which one failed.
 *
 * The four states come from `stepStates` in `ui/src/progress.ts` — the same module the Node test
 * executes — so the rail cannot paint a step green from a guess. Steps 1–3 are `done` only when
 * the Core's session is CONFIRMED (or the owner has already moved past them), step 4 is
 * `current` while a run is in flight, `done` only for a terminal-success record and `error` (red)
 * for a terminal failure. The current step carries the accent colour, a pulsing dot and the
 * moving ribbon that says the application is alive; reduced motion turns the ribbon into a still
 * bar (the CSS handles both `prefers-reduced-motion` and the `[data-motion="reduced"]` switch).
 */
const StepRail = ({ lang, states }: { lang: Lang; states: Record<StepId, StepState> }) => (
  <ol className="forge-steps">
    {DISCOVERY_STEPS.map((step, index) => {
      const state = states[step.id];
      return (
        <li
          className={`forge-step ${state}`}
          key={step.id}
          data-testid={`forge-step-${step.id}`}
          data-state={state}
        >
          <span className="forge-step-index" aria-hidden="true">
            {state === "done" ? "✓" : state === "error" ? "!" : index + 1}
          </span>
          <span>{t(lang, step.labelKey)}</span>
          {state === "current" ? (
            <span className="small muted">· {t(lang, "forge.discovery.step.current")}</span>
          ) : null}
        </li>
      );
    })}
  </ol>
);

/** One specification value as text: a list joins with `;`, an empty field says so. */
const specValue = (specification: DiscoverySpecification | undefined, key: SpecKey): string => {
  if (!specification) return "";
  const value = specification[key];
  if (Array.isArray(value)) {
    return value.map((item) => String(item).trim()).filter((item) => item !== "").join("; ");
  }
  return typeof value === "string" ? value.trim() : "";
};

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

/** The tones the writer's four statuses are drawn with, so a refusal never looks like success. */
const WRITE_TONES: Record<string, "ok" | "warn" | "danger" | "neutral"> = {
  applied: "ok",
  no_changes: "neutral",
  rejected: "danger",
  verification_failed: "warn",
};

/**
 * What the guarded writer actually did, read from `project_state.code_write` of the run.
 *
 * This is the *technical* half and it lives inside the collapsed «Технические детали»: the raw
 * status word, the post-write verification word, the English writer reason, the per-file +/-
 * counts and the unified diff. Every line comes from the record, so a reader who opens it sees
 * exactly what was recorded -- and a reader who does not never meets `WRITER: no_changes`,
 * `diff пуст` or the English reason above the fold.
 */
const CodeWriteDetails = ({ lang, write }: { lang: Lang; write: CodeWriteResult | null }) => {
  const status = write ? String(write.status ?? "") : "";
  const tone = WRITE_TONES[status] ?? "neutral";
  const files: Array<Record<string, unknown>> = Array.isArray(write?.files)
    ? write.files.filter((file): file is Record<string, unknown> => !!file && typeof file === "object")
    : [];
  const diff = typeof write?.diff === "string" ? write.diff : "";
  const reason = text(write?.reason, t(lang, "forge.write.reason.empty"));

  return (
    <div>
      <div className="row between" style={{ gap: 10, flexWrap: "wrap" }}>
        <div className="label">{t(lang, "forge.write.title")}</div>
        <Badge tone={write ? tone : "neutral"}>
          {write ? text(status, t(lang, "runs.unknown")) : t(lang, "forge.write.none")}
        </Badge>
      </div>
      <div className="small muted" style={{ marginTop: 4 }}>
        {t(lang, "forge.write.hint")}
      </div>
      {write === null ? (
        <div className="small muted" style={{ marginTop: 6 }}>
          {t(lang, "forge.write.absent")}
        </div>
      ) : (
        <div className="stack" style={{ gap: 10, marginTop: 6 }}>
          <Facts
            items={[
              [t(lang, "forge.write.status"), text(status, t(lang, "runs.unknown"))],
              [t(lang, "forge.write.verification"), text(write.verification, t(lang, "runs.unknown"))],
              [t(lang, "forge.write.files.count"), String(files.length)],
              [t(lang, "forge.write.reason"), reason],
            ]}
          />

          {files.length === 0 ? (
            <div className="small muted">{t(lang, "forge.write.files.empty")}</div>
          ) : (
            <div>
              <div className="label">{t(lang, "forge.write.files")}</div>
              <div className="stack mono small" style={{ gap: 6, marginTop: 6 }}>
                {files.map((file, index) => (
                  <div
                    className="row between"
                    key={`${String(file.path ?? "")}-${index}`}
                    style={{ gap: 10, flexWrap: "wrap" }}
                  >
                    <span style={{ wordBreak: "break-all" }}>{text(file.path)}</span>
                    <span className="row" style={{ gap: 6, flexWrap: "wrap" }}>
                      <Badge tone={file.action === "create" ? "ok" : "info"}>
                        {text(file.action, t(lang, "runs.unknown"))}
                      </Badge>
                      <Badge tone="ok">+{text(file.added, "0")}</Badge>
                      <Badge tone="danger">-{text(file.removed, "0")}</Badge>
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {diff === "" ? (
            <div className="small muted">{t(lang, "forge.write.diff.none")}</div>
          ) : (
            <details>
              <summary className="small muted" style={{ cursor: "pointer" }}>
                {t(lang, "forge.write.diff")}
              </summary>
              <pre className="forge-output mono small">{diff}</pre>
            </details>
          )}
        </div>
      )}
    </div>
  );
};

export const Forge = ({
  lang,
  backend,
  onRefresh,
  onLanguage,
  onHelp,
  onOpenProviders,
  onOpenProjects,
}: {
  lang: Lang;
  backend: BackendState;
  onRefresh: () => void;
  onLanguage: (lang: Lang) => void;
  onHelp: () => void;
  /** Opens «Провайдеры»: the agent list is the hub's job, not the workspace's. */
  onOpenProviders: () => void;
  /**
   * Opens «Проекты» **on one project**.
   *
   * The id is the only thing that makes the click do its job: switching screens without
   * it left the owner on a list with no card selected, which reads as "nothing happened".
   * An empty argument means "the list, with nothing selected".
   */
  onOpenProjects: (projectId?: string) => void;
}) => {
  const live = backend.status === "live";
  const status = live ? backend.core : null;

  // ------------------------------------------------------------------ projects
  const [projects, setProjects] = useState<ProjectsState>({ status: "loading" });
  const [selectedProject, setSelectedProject] = useState("");

  const loadProjectList = useCallback(async () => {
    setProjects({ status: "loading" });
    const outcome = await loadProjects();
    if (outcome.ok) setProjects({ status: "ready", items: outcome.value });
    else setProjects({ status: "failed", failure: outcome.failure });
  }, []);

  useEffect(() => {
    void loadProjectList();
  }, [loadProjectList]);

  const activeProject = String(status?.active_project ?? "");

  /**
   * The ids `GET /api/projects/lifecycle` lists, so a result action can name a project
   * that really exists in «Проекты».
   *
   * The run response publishes two ids: `project_id` is the *workspace* project the
   * Core was bound to (`ws-…`, not in this list), and `lifecycle_project_id` is the
   * `proj-…` record the run created or was attached to. Only the latter may open
   * anything, and only when this list confirms it -- the check lives in
   * `project-selection.js`, which the regression test executes directly.
   */
  const [lifecycleIds, setLifecycleIds] = useState<string[]>([]);
  const loadLifecycleIds = useCallback(async () => {
    const outcome = await loadProjectLifecycle();
    if (!outcome.ok) {
      setLifecycleIds([]);
      return;
    }
    setLifecycleIds(
      outcome.value
        .map((row) => String(row.project_id ?? ""))
        .filter((id) => id !== "")
    );
  }, []);

  useEffect(() => {
    void loadLifecycleIds();
  }, [loadLifecycleIds]);

  useEffect(() => {
    if (projects.status !== "ready") return;
    const ids = projects.items
      .map((project) => String(project.project_id ?? ""))
      .filter((id) => id !== "");
    if (ids.length === 0) return;
    if (selectedProject !== "" && ids.includes(selectedProject)) return;
    setSelectedProject(ids.includes(activeProject) ? activeProject : ids[0]);
  }, [projects, selectedProject, activeProject]);

  // ------------------------------------------------------------------ providers
  // The model catalogue the form chooses from: the same GET /api/models answer `loadBackend`
  // already reads, so no second request and no second source of truth.
  const catalogue = backend.status === "live" ? backend.catalogue : null;

  const providerChoice = useMemo(() => {
    if (backend.status !== "live") return { list: [] as string[], fallback: "" };
    const details = backend.core.details ?? {};
    const enabled = Array.isArray(details.enabled_providers)
      ? details.enabled_providers.map((item) => String(item ?? "")).filter((item) => item !== "")
      : [];
    const fromAgents = backend.agents
      .map((agent) => String(agent.provider ?? ""))
      .filter((item) => item !== "");
    const list: string[] = [];
    for (const item of [...enabled, ...fromAgents]) {
      if (!list.includes(item)) list.push(item);
    }
    const fallback = typeof details.default_provider === "string" ? details.default_provider : "";
    return { list, fallback };
  }, [backend]);

  // «Авто (по сложности задачи)» is the DEFAULT and the state a fresh form is in: the
  // owner should never have to hunt for a model. Naming a provider is the exception, kept
  // behind «Выбрать вручную», and it always wins over the automatic choice.
  const [provider, setProvider] = useState(AUTO_ROUTE);
  const [manualRoute, setManualRoute] = useState(false);

  useEffect(() => {
    // Nothing reported means nothing to choose from: keep whatever the user had instead of
    // silently clearing the selection while a backend poll is in flight.
    if (providerChoice.list.length === 0) return;
    // The automatic value is always a valid choice and must never be overwritten by the
    // configured default, which is what made the form look like a manual picker.
    if (provider === AUTO_ROUTE) return;
    if (provider !== "" && providerChoice.list.includes(provider)) return;
    const next =
      providerChoice.fallback !== "" && providerChoice.list.includes(providerChoice.fallback)
        ? providerChoice.fallback
        : (providerChoice.list[0] ?? "");
    setProvider(next);
  }, [providerChoice, provider]);

  // ------------------------------------------------------------------ models
  // The models the catalogue lists for the selected provider, sorted by id. An entry is
  // membership, not availability — the note under the selects says so.
  const modelChoice = useMemo(() => catalogueModelsFor(catalogue, provider), [catalogue, provider]);

  const [model, setModel] = useState("");
  // Set as soon as the owner picks a model himself, so a later arriving default never overwrites
  // a deliberate choice.
  const [modelPicked, setModelPicked] = useState(false);
  // The configured `FORGE_DEFAULT_MODEL`, read through the shell server. Empty when the route is
  // absent, which only means the form falls back to the first model of the provider.
  const [configuredModel, setConfiguredModel] = useState("");

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const outcome = await loadUiDefaults();
      if (cancelled || !outcome.ok) return;
      const value = String(outcome.value.default_model ?? "").trim();
      if (value !== "") setConfiguredModel(value);
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  /** What the select actually shows: the state, or the first id while the effect catches up. */
  const selectedModel =
    modelChoice.length === 0 ? model.trim() : modelChoice.includes(model) ? model : (modelChoice[0] ?? "");

  useEffect(() => {
    if (provider === "") return;
    // No catalogue entry for this provider: the field is free text and keeps what was typed.
    if (modelChoice.length === 0) return;
    // The configured default wins while it belongs to this provider; otherwise the first id.
    const preferred = modelChoice.includes(configuredModel) ? configuredModel : (modelChoice[0] ?? "");
    if (modelPicked && modelChoice.includes(model)) return;
    if (model !== preferred) setModel(preferred);
  }, [provider, modelChoice, model, configuredModel, modelPicked]);

  // ------------------------------------------------------------------ task form
  const [description, setDescription] = useState("");
  const [category, setCategory] = useState<TaskCategoryValue>("code");
  // Off by default: a read-only dispatch stays what every earlier click did. Turning it on is
  // the only way this window can ask the Core to write, and the warning under the box says
  // exactly what that means instead of reassuring anyone.
  const [writeMode, setWriteMode] = useState(false);
  // What the *current* run asked for, fixed at submit time: the checkbox may be toggled while
  // the run is still in flight, and the card below must describe the run, not the form.
  const [requestedWriteMode, setRequestedWriteMode] = useState(false);
  const [descriptionError, setDescriptionError] = useState("");
  const [dispatch, setDispatch] = useState<DispatchState>({ status: "idle" });
  const [idempotencyKey, setIdempotencyKey] = useState(newIdempotencyKey);
  // Everything the submit button has to say that is not a task failure: that a second click was
  // refused, that the request was cancelled, or that it is still in flight.
  const [notice, setNotice] = useState("");
  const inFlight = useRef<AbortController | null>(null);

  // ------------------------------------------------------------------ clarification
  // The answers of a run that asked, one draft per question the card shows. `lastClarification`
  // is what «Повторить» re-sends, and `clarificationSentTo` is the id of the run those answers
  // started -- the card uses it to state that a NEW run began instead of leaving the owner to
  // guess. The drafts are dropped whenever the run changes: new run, new questions.
  const [clarificationDrafts, setClarificationDrafts] = useState<Record<string, string>>({});
  const [lastClarification, setLastClarification] = useState<ClarificationDispatch | null>(null);
  const [clarificationSentTo, setClarificationSentTo] = useState("");

  // ------------------------------------------------------------------ discovery
  // The session is the Core's own record: `session.state` decides which step is reachable, and
  // only a CONFIRMED session can be run (the backend enforces it with 409). Nothing below sets a
  // step from a guess; every transition follows an answer that actually arrived.
  const [phase, setPhase] = useState<DiscoveryPhase>("idea");
  const [discoveryBusy, setDiscoveryBusy] = useState("");
  const [discoverySession, setDiscoverySession] = useState<DiscoverySessionPayload | null>(null);
  const [discoverySpec, setDiscoverySpec] = useState<DiscoverySpecificationPayload | null>(null);
  // One draft per question. `text` is exactly what would be sent; the typed control writes it,
  // «Не знаю» and «Пропустить» are explicit flags beside it, never a guessed value.
  const [drafts, setDrafts] = useState<Record<string, AnswerDraft>>({});
  const [discoveryFailure, setDiscoveryFailure] = useState<Failure | null>(null);
  const [discoveryNotice, setDiscoveryNotice] = useState("");

  const session = discoverySession?.session ?? null;
  const sessionId = String(session?.session_id ?? "");
  const sessionState = String(session?.state ?? "");
  const questions = discoverySession?.questions ?? [];
  const confirmed = sessionState === "CONFIRMED";

  /**
   * The idea as the Core stored it, shown read-only above the questions.
   *
   * It is `initial_goal`, never the local textarea: a goal the backend screened away is not
   * repeated here, which is why the empty case says so instead of falling back to user text.
   */
  const storedGoal = String(session?.initial_goal ?? "").trim();
  const ideaText = storedGoal !== "" ? storedGoal : t(lang, "forge.discovery.idea.redacted");

  // The questions card is the first thing on the page, so every step change brings it into view:
  // that is the fix for "the questions appeared at the bottom of a long page".
  const taskCardRef = useRef<HTMLDivElement | null>(null);
  const previousPhase = useRef<DiscoveryPhase | null>(null);
  useEffect(() => {
    const previous = previousPhase.current;
    previousPhase.current = phase;
    if (previous === null || previous === phase) return;
    taskCardRef.current?.scrollIntoView({ block: "start", behavior: "smooth" });
  }, [phase]);

  /**
   * A refused dispatch belongs to the step it was attempted from.
   *
   * Without this, the honest 409 of one step would sit under the next step's form and look like
   * that step had already failed. Clearing it says only "this step has not dispatched yet".
   */
  const dropFailedDispatch = () => {
    if (dispatch.status === "failed") setDispatch({ status: "idle" });
  };

  /** Take a session answer as it came back, so the widgets show what the Core stored. */
  const adoptSession = (payload: DiscoverySessionPayload) => {
    setDiscoverySession(payload);
    const stored = payload.session?.answers ?? [];
    const next: Record<string, AnswerDraft> = {};
    for (const answer of stored) {
      const id = String(answer.question_id ?? "");
      if (id === "") continue;
      const storedText = typeof answer.text === "string" ? answer.text : "";
      const storedValues = Array.isArray(answer.values) ? answer.values.map(String) : [];
      const storedCustom = typeof answer.custom === "string" ? answer.custom : "";
      // An answer the Core stored as skipped with no text was «Пропустить»/«Не знаю» with nothing
      // to record; an answer with `auto_selected` stays visible as Forge's own choice instead of
      // looking like something the user typed.
      next[id] = {
        values: storedValues,
        custom: storedCustom || (storedValues.length === 0 ? storedText : ""),
        unknown: answer.auto_selected === true,
        skip: answer.skipped === true && storedText === "",
      };
    }
    setDrafts(next);
    setDiscoveryFailure(null);
  };

  /** Step 3 always reads the specification from its own route, never from a cached guess. */
  const openSpecification = async (id: string) => {
    if (id === "") {
      setDiscoveryFailure({
        kind: "error",
        status: 0,
        path: "/api/discovery/sessions",
        detail: "the Core returned a session without a session_id",
      });
      return;
    }
    setDiscoveryBusy("spec");
    const outcome = await loadDiscoverySpecification(id);
    setDiscoveryBusy("");
    if (!outcome.ok) {
      setDiscoveryFailure(outcome.failure);
      return;
    }
    setDiscoverySpec(outcome.value);
    setDiscoveryNotice("");
    setPhase("spec");
  };

  /** Step 1 -> step 2 (or straight to step 3 when the Core found nothing essential missing). */
  const continueFromIdea = async () => {
    if (discoveryBusy !== "") {
      setDiscoveryNotice(t(lang, "forge.discovery.busy"));
      return;
    }
    const goal = description.trim();
    if (goal === "") {
      setDescriptionError(t(lang, "runs.error.description"));
      setDiscoveryNotice("");
      return;
    }
    setDescriptionError("");
    dropFailedDispatch();
    setDiscoveryFailure(null);
    setDiscoveryNotice("");
    setDiscoveryBusy("idea");
    const outcome = await startDiscovery(goal);
    setDiscoveryBusy("");
    if (!outcome.ok) {
      setDiscoveryFailure(outcome.failure);
      return;
    }
    adoptSession(outcome.value);
    const id = String(outcome.value.session?.session_id ?? "");
    const state = String(outcome.value.session?.state ?? "");
    if (state === "READY_FOR_PLANNING") {
      setDiscoveryNotice(t(lang, "forge.discovery.ready"));
      await openSpecification(id);
      return;
    }
    setPhase("clarify");
  };

  /**
   * Step 2: the answers the owner actually gave through the widgets.
   *
   * Four shapes leave here, and each one means something different to the Core:
   * `{values, custom}` is a real answer built from the clicked options and the «Свой вариант»
   * field, `{unknown: true}` is «Не знаю — выбери оптимальный вариант» (the Core stores the
   * recommended option and marks it auto-selected), `{skipped: true}` is «Пропустить» and is
   * sent only for an optional question, and an untouched question is not sent at all, so it
   * stays open for the next round.
   */
  const sendAnswers = async () => {
    if (discoveryBusy !== "" || sessionId === "") return;
    const answers: Array<{
      question_id: string;
      values?: string[];
      custom?: string;
      unknown?: boolean;
      skipped?: boolean;
    }> = [];
    const skippedRequired: string[] = [];
    for (const question of questions) {
      const id = String(question.id ?? "");
      if (id === "") continue;
      const draft = drafts[id] ?? emptyAnswerDraft();
      const required = question.required !== false && question.optional !== true;
      if (draft.skip) {
        if (required) {
          // A required decision cannot be silently dropped: the Core refuses it, so it is
          // never sent and the user is told below instead of being handed a 400.
          skippedRequired.push(String(question.text ?? id));
          continue;
        }
        answers.push({ question_id: id, skipped: true });
        continue;
      }
      if (draft.unknown) {
        answers.push({ question_id: id, unknown: true });
        continue;
      }
      if (!draftHasAnswer(draft)) continue;
      answers.push({
        question_id: id,
        values: draft.values,
        custom: draft.custom.trim(),
      });
    }
    if (skippedRequired.length > 0) {
      setDiscoveryNotice(
        `${t(lang, "forge.discovery.questions.skip.required")} ${skippedRequired.join("; ")}`
      );
      return;
    }
    if (answers.length === 0) {
      setDiscoveryNotice(t(lang, "forge.discovery.answers.empty"));
      return;
    }
    setDiscoveryNotice("");
    setDiscoveryFailure(null);
    dropFailedDispatch();
    setDiscoveryBusy("answers");
    const outcome = await sendDiscoveryAnswers(sessionId, answers);
    setDiscoveryBusy("");
    if (!outcome.ok) {
      setDiscoveryFailure(outcome.failure);
      return;
    }
    adoptSession(outcome.value);
    const state = String(outcome.value.session?.state ?? "");
    if (state === "READY_FOR_PLANNING" || state === "CONFIRMED") {
      setDiscoveryNotice(t(lang, "forge.discovery.ready"));
      await openSpecification(sessionId);
      return;
    }
    setDiscoveryNotice(t(lang, "forge.discovery.answers.accepted"));
  };

  /** Step 2 secondary action: end the round without answering everything. */
  const skipClarification = async () => {
    if (discoveryBusy !== "" || sessionId === "") return;
    dropFailedDispatch();
    setDiscoveryBusy("skip");
    setDiscoveryFailure(null);
    setDiscoveryNotice("");
    const outcome = await skipDiscoveryClarification(sessionId);
    setDiscoveryBusy("");
    if (!outcome.ok) {
      setDiscoveryFailure(outcome.failure);
      return;
    }
    adoptSession(outcome.value);
    await openSpecification(sessionId);
  };

  /** «Отменить»: close the session; the idea stays in the field. */
  const cancelSession = async () => {
    if (discoveryBusy !== "" || sessionId === "") return;
    dropFailedDispatch();
    setDiscoveryBusy("cancel");
    setDiscoveryFailure(null);
    setDiscoveryNotice("");
    const outcome = await cancelDiscovery(sessionId);
    setDiscoveryBusy("");
    if (!outcome.ok) {
      setDiscoveryFailure(outcome.failure);
      return;
    }
    adoptSession(outcome.value);
    setDiscoverySpec(null);
    setPhase("idea");
    setDiscoveryNotice(t(lang, "forge.discovery.cancelled"));
  };

  /** Step 3 -> step 4: the freeze that lets a run through. */
  const confirmSpecification = async () => {
    if (discoveryBusy !== "" || sessionId === "") return;
    dropFailedDispatch();
    setDiscoveryBusy("confirm");
    setDiscoveryFailure(null);
    setDiscoveryNotice("");
    const outcome = await confirmDiscovery(sessionId);
    setDiscoveryBusy("");
    if (!outcome.ok) {
      setDiscoveryFailure(outcome.failure);
      return;
    }
    adoptSession(outcome.value);
    setDiscoveryNotice(confirmationNotice(lang, outcome.value));
    setPhase("run");
  };

  /** Back from step 3 to step 2. Only possible before the confirmation, by the Core's own rule. */
  const editAnswers = () => {
    setDiscoveryNotice("");
    setPhase("clarify");
  };

  /**
   * The result's «Ответить» button.
   *
   * A run stopped because its answer asked questions instead of writing files, so the honest next
   * step is a **new run that carries the answers** — not the discovery card.
   *
   * That card was the measured bug: its questions are derived from the goal text
   * (`app/discovery/questions.py`), so the owner answered questions the model never asked; they
   * were stored in a discovery session that was already CONFIRMED (where `answer` refuses with
   * 409 and the UI sidestepped it by opening yet another session from the same idea), and then
   * «приступить» re-dispatched the SAME description. The model asked the same questions again —
   * forever. Now the answers travel with the dispatch (`clarification_answers` plus the id of the
   * run that asked, `clarification_run_id`), the backend appends the canonical block to the task
   * text, and the clarification context belongs to the run, not to a discovery session — so a
   * CONFIRMED, open or absent session makes no difference to whether the answers are used.
   */
  const answerClarification = async () => {
    if (dispatch.status === "pending") return;
    // The exact questions the card shows, in its order, with what the owner typed next to each.
    // An untouched question is simply not sent: the block carries answers, never guesses.
    const answers = responseQuestions
      .map((question) => ({
        question,
        answer: String(clarificationDrafts[question] ?? "").trim(),
      }))
      .filter((item) => item.answer !== "");
    if (answers.length === 0) {
      setNotice(t(lang, "forge.result.clarification.answers.empty"));
      return;
    }
    await submit({ runId, answers });
  };

  // ------------------------------------------------------------------ run record
  const [record, setRecord] = useState<RunRecord | null>(null);
  const [recordFailure, setRecordFailure] = useState<Failure | null>(null);
  const [recordLoading, setRecordLoading] = useState(false);
  const [pollNonce, setPollNonce] = useState(0);
  /**
   * The press that started the current run, in epoch milliseconds.
   *
   * It is a real local fact, not a fake timer: the dispatch is synchronous, so the window holds
   * no run id while the Core works, and `RunRecord` publishes no start of its own (only the
   * final snapshot's `updated_at`). The clock therefore starts at the press and ends at the
   * record's own timestamp — which bounds it even when the record arrives late.
   */
  const [runObservedAt, setRunObservedAt] = useState(0);

  const dispatched = dispatch.status === "sent" ? dispatch.result : null;
  const runId = String(dispatched?.response.run_id ?? "");
  const terminal = isTerminalRecord(record);
  // `project_state.code_write` is written by the Core only for a write-mode code run, so its
  // presence is the record's own proof that the writer ran — never inferred from the checkbox.
  const codeWrite = codeWriteOf(record);

  /**
   * The facts the result card is allowed to read. `files_created` is the response's own count of
   * files the writer applied; the questions are the ones the *final* answer asked (after the
   * single retry, when one happened).
   */
  const filesCreated =
    typeof dispatched?.response.files_created === "number" ? dispatched.response.files_created : 0;
  const responseQuestions = Array.isArray(dispatched?.response.clarification_questions)
    ? dispatched.response.clarification_questions
        .map((item) => String(item ?? ""))
        .filter((item) => item.trim() !== "")
    : [];
  /**
   * `clarification_round` as the backend published it. It is the run's own count (0 = no
   * clarification context, 1 = may still ask, 2 = the forced final round), read from the
   * response and never inferred from the wording of an answer.
   */
  const clarificationRound =
    typeof dispatched?.response.clarification_round === "number"
      ? dispatched.response.clarification_round
      : 0;
  const resultFacts = {
    filesCreated,
    writeStatus: String(codeWrite?.status ?? ""),
    verification: String(codeWrite?.verification ?? ""),
    writeReason: String(codeWrite?.reason ?? ""),
    questions: responseQuestions,
    needsClarification: dispatched?.response.needs_clarification === true,
    clarificationRound,
    error: String(dispatched?.response.error ?? ""),
    writeRequested: requestedWriteMode,
  };

  /**
   * The one plain-language sentence this run's result card says, derived from the facts only.
   *
   * The facts are the response's own `files_created`, the writer record's status/verification/reason
   * and the questions the answer asked. `state` is deliberately not one of them: `COMPLETED` means
   * "the dispatch finished", and the measured failure had it over a project that was never created.
   * The derivation itself lives in `forge-result.ts` so the rule is one testable function, not JSX.
   */
  const headline = deriveRunHeadline(lang, resultFacts);
  /** True only when the answer really asked (a plan ending in "?" is not a question list). */
  const askedQuestions = answerAskedQuestions(resultFacts);

  /**
   * The one project id the result actions may open: the run's own
   * `lifecycle_project_id` **only** when the lifecycle list confirms it.
   *
   * `project_id` is deliberately not read here: it is the workspace/Core project
   * (`ws-…`), which is not a lifecycle record. An absent id means "this run has no
   * project" and an id the list does not name means "project not found"; both are a
   * disabled button with the matching reason, never a click that lands on something
   * else. `selectRunProject` is the whole rule and is executed by the regression test.
   */
  const runProject = selectRunProject(dispatched?.response.lifecycle_project_id, lifecycleIds);
  const resultProjectId = runProject.projectId;
  const resultProjectBlockedReason =
    runProject.reason === "no_project"
      ? t(lang, "forge.result.action.no_project")
      : runProject.reason === "unknown_project"
        ? t(lang, "forge.result.action.unknown_project")
        : "";

  // A run can create a project, so the list is re-read once the run has an id: the id this
  // card could open must be one that exists in «Проекты» *now*, not one that existed before.
  useEffect(() => {
    if (runId === "") return;
    void loadLifecycleIds();
  }, [runId, loadLifecycleIds]);

  // A new run carries its own questions, so the drafts of the previous card are dropped: an
  // answer typed for one question must never be attached to a different one.
  useEffect(() => {
    setClarificationDrafts({});
  }, [runId]);

  /**
   * What each headline action does. Files, the check and the local run live in «Проекты»,
   * so those three hand that screen a **concrete project id**; with no id the button is
   * disabled and the reason is printed instead of pretending to open something.
   */
  const runResultAction = (action: ResultActionId) => {
    if (action === "answer") {
      void answerClarification();
      return;
    }
    if (action === "retry") {
      // A retry of a clarification round re-sends the answers that round carried: the backend
      // keeps them in the run's own context, and dropping them here would ask the model the same
      // question again with LESS information than the attempt it is retrying.
      void submit(clarificationRound > 0 && lastClarification !== null ? lastClarification : undefined);
      return;
    }
    if (resultProjectId === "") return;
    onOpenProjects(resultProjectId);
  };

  /**
   * The provider-neutral outcome the run record itself carries (`project_state.outcome`).
   *
   * The record's own `state` is the run's terminal word (`COMPLETED` as soon as the run finished),
   * which is not the same fact as "the project is complete". Both are shown, side by side, so the
   * execution card can never be read as a completion claim the result card contradicts.
   */
  const recordOutcome = (() => {
    const projectState = record?.project_state;
    if (!projectState || typeof projectState !== "object") return "";
    const value = (projectState as Record<string, unknown>).outcome;
    return typeof value === "string" ? value.trim() : "";
  })();

  /**
   * `project_state.write_retry`: whether Forge asked the model a second time.
   *
   * `null` means the record has not said — the run recorded no retry at all, or the record has
   * not arrived yet. `TaskRunResponse` is byte-pinned, so this fact lives in the run's durable
   * state beside `code_write` instead of in the response body.
   */
  const writeRetryAttempted = (() => {
    const projectState = record?.project_state;
    if (!projectState || typeof projectState !== "object") return null;
    const value = (projectState as Record<string, unknown>).write_retry;
    if (!value || typeof value !== "object" || Array.isArray(value)) return null;
    const attempted = (value as Record<string, unknown>).attempted;
    return typeof attempted === "boolean" ? attempted : null;
  })();

  // Poll GET /api/runs/{run_id} while the record is not terminal. A read failure stops the
  // loop and is shown with a retry: retrying a *read* executes nothing, unlike retrying a task.
  useEffect(() => {
    if (runId === "") return;
    let cancelled = false;
    let timer: number | undefined;
    setRecordLoading(true);
    const step = async () => {
      const outcome = await loadRunRecord(runId);
      if (cancelled) return;
      if (!outcome.ok) {
        setRecordFailure(outcome.failure);
        setRecordLoading(false);
        return;
      }
      setRecord(outcome.value);
      setRecordFailure(null);
      if (isTerminalRecord(outcome.value)) {
        setRecordLoading(false);
        return;
      }
      timer = window.setTimeout(() => void step(), POLL_INTERVAL_MS);
    };
    void step();
    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [runId, pollNonce]);

  const polling = runId !== "" && recordFailure === null && !terminal;

  // ------------------------------------------------------------------ live progress
  // The rail and the alive bar read only facts that exist: the session's own CONFIRMED state,
  // the dispatch in flight and the run record. `POST /api/tasks/run` is synchronous, so while
  // the Core works there is no run id yet — that window is exactly where the owner used to sit
  // for half a minute without seeing anything move, and it is what the bar now covers.
  const runEvents = typeof record?.event_count === "number" ? record.event_count : 0;
  const runInFlight =
    dispatch.status === "pending" || (runId !== "" && recordFailure === null && !terminal);
  const runSucceeded = record?.classification?.is_success === true;
  const steps = stepStates({
    phase,
    confirmed,
    inFlight: runInFlight,
    terminal,
    success: runSucceeded,
  });
  // The stage word for the bar: the Core is executing the task until a record proves a later
  // stage (one per four persisted events, the same rule the change strip uses).
  const aliveStage = t(lang, aliveStageKey(runEvents, record !== null));

  // The write switch is available whenever the run can write at all: a `code`
  // category and a routable provider. `mock` is included on purpose -- the Core's
  // `MOCK_DETERMINISTIC` mode writes the deterministic scaffold when the run belongs
  // to a lifecycle project, and honestly reports `no_changes` when it does not; the
  // old blanket "mock cannot write" disabled the only free path that creates a
  // project at all. A non-code category or no provider still disables it.
  // «Авто» is the default: no provider is named, so the Core chooses by complexity.
  const autoRoute = !manualRoute || provider === AUTO_ROUTE;
  const mockSelected = provider === "mock";
  // The write switch is available whenever a run can write at all: a `code` category and a
  // route. In «Авто» mode the route is the one the Core will choose, so there is nothing to
  // check here; the per-run choice is the checkbox below, not a provider name.
  const writeUnavailable =
    category !== "code"
      ? t(lang, "forge.task.write.noncode")
      : autoRoute
        ? providerChoice.list.length === 0
          ? t(lang, "forge.task.write.noprovider")
          : ""
        : !providerChoice.list.includes(provider)
          ? t(lang, "forge.task.write.noprovider")
          : "";
  const writeSupported = writeUnavailable === "";
  const effectiveWriteMode = writeMode && writeSupported;

  // The launch step's cost facts, straight from the /api/models row of the selected pair. An
  // unlisted pair has no tier and no price: both stay unknown instead of being assumed free.
  const catalogueEntry = useMemo(
    () => catalogueEntryFor(catalogue, provider, selectedModel),
    [catalogue, provider, selectedModel]
  );
  const costTier = String(catalogueEntry?.cost_tier ?? "");
  const paidModel = costTier === "PAID";
  // A paid run needs a deliberate click on this box, per provider/model selection.
  const [paidConfirmed, setPaidConfirmed] = useState(false);
  useEffect(() => {
    setPaidConfirmed(false);
  }, [provider, selectedModel]);
  // THE AUTOMATIC PREVIEW. When no provider is named, what will run is decided by task
  // complexity, and the owner must SEE it before pressing. This is the same pure chooser
  // the run uses (`POST /api/tasks/run` with `estimate: true`), debounced and local: no
  // provider is called, no run is created and nothing can be spent.
  const [routePreview, setRoutePreview] = useState<RoutePreview>({ status: "empty" });
  useEffect(() => {
    if (!autoRoute || backend.status !== "live") {
      setRoutePreview({ status: "empty" });
      return;
    }
    const taskText = description.trim();
    if (taskText === "") {
      setRoutePreview({ status: "empty" });
      return;
    }
    const controller = new AbortController();
    setRoutePreview({ status: "pending" });
    const timer = window.setTimeout(() => {
      void (async () => {
        const outcome = await estimateRoute({
          description: taskText,
          category,
          writeMode: writeMode && category === "code",
          // A confirmed session contributes its specification, exactly as the real run
          // will; an unconfirmed one contributes nothing here.
          discoverySessionId: confirmed ? sessionId : undefined,
          signal: controller.signal,
        });
        if (controller.signal.aborted) return;
        setRoutePreview(
          outcome.ok ? { status: "ready", choice: outcome.value } : { status: "failed" }
        );
      })();
    }, ROUTE_PREVIEW_DELAY_MS);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [
    autoRoute,
    backend.status,
    description,
    category,
    writeMode,
    confirmed,
    sessionId,
  ]);
  const autoChoice = routePreview.status === "ready" ? routePreview.choice : null;
  // The tier the run will really use. In automatic mode it comes from the preview, because
  // the pair is not on screen to look up in the catalogue.
  const effectiveTier = autoRoute ? String(autoChoice?.tier ?? "") : costTier;
  const effectivePaid = effectiveTier === "PAID";
  // «Это дороже вашего потолка». An explicitly named pair is always honoured -- the owner
  // may choose consciously -- but a pair above the ceiling he set in «Настройки» must not
  // be chosen in silence. The ceiling is the router's own value from
  // `/api/status.details.max_cost_tier`, not a second policy computed here.
  const tierRank: Record<string, number> = { FREE: 0, CHEAP: 1, PAID: 2 };
  const ceilingWord = String(status?.details?.max_cost_tier ?? "").toUpperCase();
  const aboveCeiling =
    !autoRoute &&
    typeof tierRank[effectiveTier] === "number" &&
    typeof tierRank[ceilingWord] === "number" &&
    tierRank[effectiveTier] > tierRank[ceilingWord];
  // The automatic path never asks for a second confirmation: the deployment's
  // `allow_paid_providers` is the permission, and routing only reaches PAID because of it.
  // A manual choice keeps the deliberate per-run box it always had.
  const runBlocked = !autoRoute && paidModel && !paidConfirmed;

  const costLine = [
    provider === "" ? t(lang, "forge.task.route.auto") : provider,
    selectedModel === "" ? "—" : selectedModel,
    costTier === "" ? t(lang, "forge.launch.cost.unknown") : costTier,
    typeof catalogueEntry?.estimated_cost === "number"
      ? t(lang, "forge.launch.cost.unit").replace(
          "{price}",
          `$${catalogueEntry.estimated_cost.toFixed(4)}`
        )
      : t(lang, "forge.launch.cost.notlisted"),
  ].join(" · ");

  // Which write mode this request will carry. In «Авто» mode the mock case is the preview's
  // own provider: the offline engine is what writes the deterministic scaffold.
  const effectiveMockProvider = autoRoute
    ? String(autoChoice?.provider ?? "") === "mock"
    : mockSelected;

  const submit = async (clarification?: ClarificationDispatch) => {
    // The button stays live for every honest reason a run cannot start (a disabled button with no
    // explanation is what made "Запустить задачу" look dead). The single exception is the paid
    // confirmation below: there the button is disabled *and* the reason is printed beside it.
    if (dispatch.status === "pending") {
      setNotice(t(lang, "forge.task.busy"));
      return;
    }
    if (runBlocked) {
      setNotice(t(lang, "forge.launch.paid.blocked"));
      return;
    }
    const trimmed = description.trim();
    if (trimmed === "") {
      setDescriptionError(t(lang, "runs.error.description"));
      setNotice("");
      return;
    }
    setDescriptionError("");
    setNotice("");
    setRecord(null);
    setRecordFailure(null);
    setDispatch({ status: "pending" });
    setRequestedWriteMode(effectiveWriteMode);
    // The clock of the alive bar starts here: this is the press whose result the owner waits for.
    setRunObservedAt(Date.now());
    // The same form, the same provider/model/approval rules and the same write decision; only the
    // answers (and the run that asked) are added. A dispatch that carries no answers is a plain
    // run, so the "answers started this run" line is cleared rather than left from an earlier one.
    if (clarification === undefined) setClarificationSentTo("");
    const controller = new AbortController();
    inFlight.current = controller;
    // Only a provider the Core itself reported is sent; an empty or stale selection leaves the
    // routing to the backend instead of naming a provider that is not in the list. In the
    // default «Авто» mode NOTHING is named, so the Core classifies the task and chooses —
    // that is the whole point of the automatic route.
    const providerToSend =
      manualRoute && providerChoice.list.includes(provider) ? provider : undefined;
    // The model rides with the provider it was chosen for: sending an id from another provider
    // would be a route nobody chose, and an empty id leaves the Core's own default in force.
    const modelToSend = providerToSend !== undefined && selectedModel !== "" ? selectedModel : undefined;
    try {
      const outcome = await dispatchTask({
        description: trimmed,
        category,
        providerName: providerToSend,
        modelName: modelToSend,
        projectId: selectedProject === "" ? undefined : selectedProject,
        writeMode: effectiveWriteMode,
        // The session travels with the run so the Core can apply its own confirmation guard: an
        // unconfirmed session is refused with 409 discovery_not_confirmed, and that refusal is
        // shown as it arrived instead of being hidden by the interface.
        //
        // A clarification dispatch is the exception, deliberately: its answers belong to the RUN,
        // not to a discovery session, so an open (unconfirmed) session must not refuse a click
        // that has nothing to do with it. A CONFIRMED session still travels, so the run keeps its
        // specification and its project write target.
        discoverySessionId:
          clarification === undefined ? sessionId : confirmed ? sessionId : undefined,
        clarificationAnswers: clarification?.answers,
        clarificationRunId: clarification?.runId,
        idempotencyKey,
        signal: controller.signal,
      });
      if (controller.signal.aborted) {
        // Aborted by the user: no result to show, and the in-flight state is cleared below.
        setDispatch({ status: "idle" });
        setNotice(t(lang, "forge.task.cancelled"));
      } else if (outcome.ok) {
        setDispatch({ status: "sent", result: outcome.value });
        if (clarification !== undefined) {
          // Keep the answers for «Повторить» and name the NEW run: the owner must see that the
          // click started another run, not the same card again. The saved `runId` is the run that
          // just ran (the one whose card offers «Повторить»), because the backend reads the
          // answers from the named run's own record.
          const newRunId = String(outcome.value.response.run_id ?? "");
          setLastClarification({ runId: newRunId, answers: clarification.answers });
          setClarificationSentTo(newRunId);
        }
        // A replayed answer must not silently become a new logical operation.
        if (outcome.value.facts.idempotent_replay !== true) setIdempotencyKey(newIdempotencyKey());
      } else {
        setDispatch({ status: "failed", failure: outcome.failure });
      }
    } catch (error) {
      // `dispatchTask` reports failures instead of throwing, so this is the unexpected path; it
      // must still clear the in-flight state rather than leave the form permanently busy.
      setDispatch(
        controller.signal.aborted
          ? { status: "idle" }
          : {
              status: "failed",
              failure: {
                kind: "disconnected",
                status: 0,
                path: "/api/tasks/run",
                detail: error instanceof Error ? error.message : String(error),
              },
            }
      );
      if (controller.signal.aborted) setNotice(t(lang, "forge.task.cancelled"));
    } finally {
      if (inFlight.current === controller) inFlight.current = null;
      onRefresh();
    }
  };

  /** Cancel this window's own request and drop the in-flight state. */
  const cancel = () => {
    inFlight.current?.abort();
    inFlight.current = null;
    setDispatch({ status: "idle" });
    setNotice(t(lang, "forge.task.cancelled"));
  };

  // ------------------------------------------------------------------ settings facts
  const registryCounts =
    catalogue && typeof catalogue.counts === "object" && catalogue.counts !== null
      ? (catalogue.counts as Record<string, unknown>)
      : null;
  const registrySource = catalogue && typeof catalogue.source === "string" ? catalogue.source : "";
  const enabledProviders =
    status && Array.isArray(status.details?.enabled_providers)
      ? status.details.enabled_providers.map((item) => String(item))
      : [];

  // The dispatch basis arrives in the X-Forge-* response headers. When they are absent the
  // interface says so instead of guessing what kind of number it is showing.
  const basisReported =
    dispatched !== null && dispatched.facts.usage_basis !== "" && dispatched.facts.cost_basis !== "";

  // The exact provider · model pair the submit button will send, or the honest "the backend
  // chooses" when the Core reported nothing to choose from. It is only a *named* pair in
  // «Выбрать вручную» mode; in «Авто» the route is the preview below.
  const routeLabel =
    [
      manualRoute && providerChoice.list.includes(provider) ? provider : "",
      manualRoute && providerChoice.list.includes(provider) ? selectedModel : "",
    ]
      .filter((part) => part !== "")
      .join(" · ") || t(lang, "forge.task.route.auto");

  // What the automatic route will use, one line plus up to three reasons — the line the
  // owner reads BEFORE pressing, and the same «provider · model · tier · why» the response
  // publishes afterwards.
  const previewPair =
    autoChoice === null
      ? ""
      : [autoChoice.provider ?? "—", autoChoice.model === "" ? "—" : (autoChoice.model ?? "—"), tierWord(lang, autoChoice.tier)].join(
          " · "
        );
  const previewReasons = (autoChoice?.reasons ?? [])
    .map((reason) => String(reason ?? "").trim())
    .filter((reason) => reason !== "")
    .slice(0, 3);

  /**
   * The launch step: the route, the category, the cost facts, the write switch and the
   * submit itself.
   *
   * It is drawn on step 4 only. The questions and the specification come first, so the
   * owner is never asked to pick a model before the Core has said what the project is —
   * and by default he is never asked to pick one at all: «Авто (по сложности задачи)» is
   * the state a fresh form is in, and the decision is previewed as the task is typed.
   */
  const runControls = (
    <div className="stack" style={{ gap: 14 }}>
      <div className="row" style={{ gap: 12, flexWrap: "wrap" }}>
        <label className="field" style={{ flex: "0 1 190px" }}>
          <span className="label">{t(lang, "runs.form.category")}</span>
          <select
            className="input"
            value={category}
            onChange={(event) => setCategory(event.target.value as TaskCategoryValue)}
          >
            {TASK_CATEGORIES.map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </select>
        </label>
        <label className="field" style={{ flex: "0 1 260px" }}>
          <span className="label">{t(lang, "forge.task.route")}</span>
          <label className="forge-toggle" style={{ marginTop: 8 }}>
            <input
              type="checkbox"
              checked={manualRoute}
              onChange={(event) => {
                const next = event.target.checked;
                setManualRoute(next);
                // Turning the manual mode on starts from the configured default, so the
                // first thing the owner sees is a real pair and not an empty select.
                if (next) {
                  setProvider(
                    providerChoice.fallback !== "" &&
                      providerChoice.list.includes(providerChoice.fallback)
                      ? providerChoice.fallback
                      : (providerChoice.list[0] ?? AUTO_ROUTE)
                  );
                }
              }}
            />
            <span>{t(lang, "forge.launch.route.manual")}</span>
          </label>
        </label>
      </div>

      {/* The manual pair, collapsed by default: it exists for whoever wants to name it,
          and the automatic route needs nothing here at all. */}
      {manualRoute ? (
        <div className="row" style={{ gap: 12, flexWrap: "wrap" }}>
          <label className="field" style={{ flex: "0 1 190px" }}>
            <span className="label">{t(lang, "forge.task.provider")}</span>
            <select
              className="input"
              value={provider}
              onChange={(event) => setProvider(event.target.value)}
              disabled={providerChoice.list.length === 0}
            >
              {providerChoice.list.length === 0 ? (
                <option value={AUTO_ROUTE}>{t(lang, "forge.settings.none")}</option>
              ) : (
                providerChoice.list.map((item) => (
                  <option key={item} value={item}>
                    {item}
                  </option>
                ))
              )}
            </select>
          </label>
          <label className="field" style={{ flex: "0 1 240px" }}>
            <span className="label">{t(lang, "forge.task.model")}</span>
            {modelChoice.length > 0 ? (
              <select
                className="input"
                value={selectedModel}
                onChange={(event) => {
                  setModelPicked(true);
                  setModel(event.target.value);
                }}
                disabled={provider === ""}
              >
                {modelChoice.map((item) => (
                  <option key={item} value={item}>
                    {item}
                  </option>
                ))}
              </select>
            ) : (
              // The catalogue lists no model for this provider: free text keeps the field
              // usable instead of offering an empty select nobody can submit.
              <input
                className="input"
                type="text"
                value={model}
                onChange={(event) => {
                  setModelPicked(true);
                  setModel(event.target.value);
                }}
                placeholder={t(lang, "forge.task.model.placeholder")}
                disabled={provider === ""}
              />
            )}
          </label>
        </div>
      ) : null}

      {/* A pair the owner named above his own ceiling: still runnable, never silent. */}
      {aboveCeiling ? (
        <div className="notice warn" data-testid="forge-ceiling-override">
          {t(lang, "settings.routing.ceiling.override")
            .replace("{ceiling}", ceilingWord)
            .replace("{tier}", tierWord(lang, effectiveTier))}
        </div>
      ) : null}

      {/* THE AUTOMATIC DECISION, before pressing: «будет: mock · … · бесплатно — простая
          задача», then up to three reasons. It is the same decision the run makes, not a
          second opinion: the route answers it with the pure chooser and dispatches nothing. */}
      {autoRoute ? (
        <div className="forge-cost" data-testid="route-preview">
          <div className="row between" style={{ gap: 10, flexWrap: "wrap" }}>
            <span className="label">{t(lang, "forge.launch.route.title")}</span>
            <Badge
              tone={
                effectiveTier === "FREE"
                  ? "ok"
                  : effectiveTier === "CHEAP"
                    ? "info"
                    : effectiveTier === "PAID"
                      ? "warn"
                      : "neutral"
              }
            >
              {effectiveTier === ""
                ? t(lang, "forge.launch.cost.unknown")
                : tierWord(lang, effectiveTier)}
            </Badge>
          </div>
          <div className="mono small" style={{ marginTop: 6, wordBreak: "break-word" }}>
            {routePreview.status === "pending"
              ? t(lang, "forge.launch.route.preview.pending")
              : routePreview.status === "empty"
                ? t(lang, "forge.launch.route.preview.empty")
                : routePreview.status === "failed"
                  ? t(lang, "forge.launch.route.preview.failed")
                  : `${t(lang, "forge.launch.route.preview")}: ${previewPair}${
                      autoChoice?.complexity
                        ? ` — ${complexityWord(lang, autoChoice.complexity)}`
                        : ""
                    }`}
          </div>
          {previewReasons.map((reason) => (
            <div className="small muted" data-testid="route-preview-reason" key={reason} style={{ marginTop: 2 }}>
              {reason}
            </div>
          ))}
          <div className="small muted" style={{ marginTop: 6 }}>
            {t(lang, "forge.launch.route.auto.note")}
          </div>
        </div>
      ) : null}

      {autoRoute ? null : (
        <>
          <div className="small muted">
            {providerChoice.list.length === 0
              ? t(lang, "forge.task.provider.empty")
              : t(lang, "forge.task.provider.note")}
          </div>

          {/* The catalogue note is drawn only when the Core actually reported providers: with
              no answer at all the honest statement is the provider note above, not a claim
              that the registry holds no model for this provider. */}
          {providerChoice.list.length > 0 ? (
            <div className="small muted">
              {modelChoice.length === 0
                ? t(lang, "forge.task.model.free")
                : t(lang, "forge.task.model.note")}
            </div>
          ) : null}

          {/* What the next submit will actually name, so the owner sees the model that runs. */}
          <div className="small row" style={{ gap: 6, flexWrap: "wrap" }}>
            <span className="muted">{t(lang, "forge.task.route")}</span>
            <span className="mono">{routeLabel}</span>
          </div>

          {/* provider · model · cost_tier · estimated_cost, all four read from the /api/models row. */}
          <div className="forge-cost">
            <div className="row between" style={{ gap: 10, flexWrap: "wrap" }}>
              <span className="label">{t(lang, "forge.launch.cost")}</span>
              <Badge
                tone={costTier === "FREE" ? "ok" : costTier === "CHEAP" ? "info" : costTier === "PAID" ? "warn" : "neutral"}
              >
                {costTier === "" ? t(lang, "forge.launch.cost.unknown") : costTier}
              </Badge>
            </div>
            <div className="mono small" style={{ marginTop: 6, wordBreak: "break-word" }}>
              {costLine}
            </div>
            <div className="small muted" style={{ marginTop: 6 }}>
              {t(lang, "forge.launch.cost.note")}
            </div>
          </div>

          {/* The offline executor is labelled as what it is: an answer without a file change. */}
          {mockSelected ? <div className="notice">{t(lang, "forge.discovery.mock.note")}</div> : null}
        </>
      )}

      {/* A paid pair runs only after a deliberate confirmation; free and cheap need none. In
          «Авто» mode the confirmation is the deployment's `allow_paid_providers` — routing
          only reaches a paid model because that permission exists — and the preview says so. */}
      {autoRoute ? (
        effectivePaid ? (
          <div className="notice warn">{t(lang, "forge.launch.route.paid.allowed")}</div>
        ) : effectiveTier !== "" ? (
          <div className="small muted">{t(lang, "forge.launch.free.note")}</div>
        ) : null
      ) : paidModel ? (
        <div className="notice warn">
          <label className="forge-toggle">
            <input
              type="checkbox"
              checked={paidConfirmed}
              onChange={(event) => setPaidConfirmed(event.target.checked)}
            />
            <span>{t(lang, "forge.launch.paid.label")}</span>
          </label>
          <div className="small" style={{ marginTop: 6 }}>
            {t(lang, "forge.launch.paid.note")}
          </div>
        </div>
      ) : costTier !== "" ? (
        <div className="small muted">{t(lang, "forge.launch.free.note")}</div>
      ) : null}

      {/* MOCK IS LOUD, AND IT IS SAID IMMEDIATELY ABOVE THE BUTTON.
          The offline engine cannot change a file through a model call, and the measured
          history is a project that "changed" three times with zero files. This line is
          printed for the provider the run will really use -- the owner's choice, or the
          one «Авто» resolves to -- and it is never hidden inside the details. */}
      {effectiveMockProvider ? (
        <div className="notice warn" data-testid="launch-mock-warning">
          {t(lang, "forge.mock.warning")}
        </div>
      ) : null}

      <div className="row" style={{ gap: 12, flexWrap: "wrap", alignItems: "center" }}>
        <Button tone="primary" disabled={runBlocked} onClick={() => void submit()}>
          <IconPlay size={15} />
          {dispatch.status === "pending" ? t(lang, "runs.form.running") : t(lang, "runs.form.submit")}
        </Button>
        {runBlocked ? (
          <span className="small muted">{t(lang, "forge.launch.paid.blocked")}</span>
        ) : null}
        {dispatch.status === "pending" ? (
          <Button size="small" tone="danger" onClick={cancel}>
            {t(lang, "forge.task.cancel")}
          </Button>
        ) : null}
        <label className={`forge-toggle${writeSupported ? "" : " disabled"}`}>
          <input
            type="checkbox"
            checked={effectiveWriteMode}
            disabled={!writeSupported}
            onChange={(event) => setWriteMode(event.target.checked)}
          />
          <span>{t(lang, "forge.task.write.label")}</span>
          {writeSupported ? null : (
            <span className="small muted">
              · {t(lang, "forge.task.write.unavailable")}
              {writeUnavailable === "" ? "" : ` (${writeUnavailable})`}
            </span>
          )}
        </label>
        {writeSupported ? <span className="small muted">{t(lang, "forge.task.guarded")}</span> : null}
      </div>
      <div className="small muted forge-write-warning">{t(lang, "forge.task.write.warning")}</div>

      {/* The write mode this request will carry, stated before the click. There are exactly
          two, and the backend decides from the provider/model/write triple -- this line only
          repeats that decision in the owner's words. */}
      <div className="small row" style={{ gap: 6, flexWrap: "wrap" }}>
        <span className="muted">{t(lang, "forge.launch.write.mode")}</span>
        <span className="mono">
          {effectiveWriteMode
            ? effectiveMockProvider
              ? "MOCK_DETERMINISTIC"
              : "REAL_PROVIDER_EXPLICIT"
            : t(lang, "forge.launch.write.none")}
        </span>
      </div>
      <div className="small muted">
        {effectiveWriteMode
          ? t(lang, "forge.launch.write.hint")
          : t(lang, "forge.launch.write.noselection")}
      </div>

      {dispatch.status === "pending" ? <div className="notice">{t(lang, "forge.task.pending")}</div> : null}
      {notice !== "" ? <div className="notice warn">{notice}</div> : null}
      {dispatch.status === "failed" ? <FailureNotice lang={lang} failure={dispatch.failure} /> : null}
    </div>
  );

  return (
    <div className="content forge-workbench">
      <div className="row between" style={{ marginBottom: 18 }}>
        <div>
          <h1 className="row" style={{ margin: 0, fontSize: "var(--t-title)", gap: 10 }}>
            <IconAnvil size={22} className="forge-anvil" />
            {t(lang, "nav.forge")}
          </h1>
          <div className="small muted" style={{ marginTop: 4 }}>
            {t(lang, "forge.subtitle")}
          </div>
        </div>
        <div className="row">
          <Button
            size="small"
            tone="ghost"
            onClick={() => window.open("/code-stats.html", "_blank", "noopener,noreferrer")}
          >
            {t(lang, "forge.stats.open")}
          </Button>
          <Button size="small" tone="ghost" onClick={onHelp}>
            {t(lang, "help.how")}
          </Button>
          <Button
            onClick={() => {
              void loadProjectList();
              onRefresh();
              if (runId !== "") setPollNonce((previous) => previous + 1);
            }}
          >
            <IconRefresh size={15} /> {t(lang, "action.refresh")}
          </Button>
        </div>
      </div>

      {backend.status === "offline" ? (
        <div className="notice danger" style={{ marginBottom: 16 }}>
          <div>{t(lang, "state.offline")}</div>
          <div className="small mono" style={{ marginTop: 6 }}>
            {backend.reason}
          </div>
        </div>
      ) : null}

      {/* ------------------------------------------------------------ first-run guide
          Five lines for a friend who has never seen Forge, collapsed by default so it never gets
          in the way of the work, and the one line the owner had to learn the hard way: the file
          format is Forge's job, not the user's. The same six lines are a topic in «Справка». */}
      <details className="forge-guide">
        <summary>{t(lang, "forge.guide.title")}</summary>
        <ol className="forge-guide-steps">
          {[
            "forge.guide.step1",
            "forge.guide.step2",
            "forge.guide.step3",
            "forge.guide.step4",
            "forge.guide.step5",
          ].map((key) => (
            <li key={key}>{t(lang, key)}</li>
          ))}
        </ol>
        <div className="small muted">{t(lang, "forge.guide.note")}</div>
      </details>

      <div className="grid overview">
        <div className="stack">
          {/* -------------------------------------------------------------------- task
              First in the workspace, and the reason is the owner's complaint: the questions used
              to appear at the bottom of a long page. The idea form is the only thing here until
              the first submit; from then on the clarification card takes its place at the top. */}
          <div ref={taskCardRef}>
            <Card className="rise">
              <CardHead
                title={t(lang, "forge.task.title")}
                hint={t(lang, "forge.task.hint")}
                action={
                  <Badge tone={live ? "info" : backend.status === "loading" ? "neutral" : "warn"}>
                    {backend.status === "loading" ? "…" : live ? "LIVE" : "OFFLINE"}
                  </Badge>
                }
              />
              <div className="stack" style={{ gap: 14 }}>
                <StepRail lang={lang} states={steps} />
                {/* THE ALIVE BAR, directly under the rail: the clock, the moving ribbon and the
                    stage, so a run that takes half a minute is visibly alive. It stays after the
                    run ends and says «Готово за …» or the honest failure, with the ribbon stopped. */}
                <AliveBar
                  lang={lang}
                  inFlight={runInFlight}
                  terminal={terminal}
                  success={runSucceeded}
                  observedAt={runObservedAt}
                  endedAt={stampMs(record?.updated_at)}
                  stage={aliveStage}
                />

                {/* The idea stays above everything else, read-only, once it has been submitted. */}
                {phase !== "idea" ? (
                  <div className="forge-idea">
                    <div className="row between" style={{ gap: 10, flexWrap: "wrap" }}>
                      <span className="label">{t(lang, "forge.discovery.idea.saved")}</span>
                      {confirmed ? null : (
                        <button
                          type="button"
                          className="btn small ghost"
                          onClick={() => {
                            setDiscoveryNotice("");
                            setPhase("idea");
                          }}
                        >
                          {t(lang, "forge.discovery.idea.edit")}
                        </button>
                      )}
                    </div>
                    <div className="forge-idea-text">{ideaText}</div>
                  </div>
                ) : null}

                {/* Step 1 — the idea. The only field that is the user's own words. */}
                {phase === "idea" ? (
                  <div className="stack" style={{ gap: 12 }}>
                    <Field
                      label={t(lang, "forge.discovery.goal.label")}
                      hint={t(lang, "forge.discovery.goal.hint")}
                      error={descriptionError}
                    >
                      <textarea
                        className="input"
                        rows={4}
                        value={description}
                        onChange={(event) => setDescription(event.target.value)}
                        placeholder={t(lang, "forge.discovery.goal.placeholder")}
                      />
                    </Field>
                    <div className="row" style={{ gap: 12, flexWrap: "wrap", alignItems: "center" }}>
                      <Button
                        tone="primary"
                        disabled={discoveryBusy === "idea"}
                        onClick={() => void continueFromIdea()}
                      >
                        <IconPlay size={15} />
                        {discoveryBusy === "idea"
                          ? t(lang, "runs.form.running")
                          : t(lang, "forge.discovery.continue")}
                      </Button>
                      <span className="small muted">{t(lang, "forge.discovery.continue.note")}</span>
                    </div>
                  </div>
                ) : null}

                {/* The session the Core actually opened, named as it is stored. */}
                {phase !== "idea" ? (
                  <div className="small row" style={{ gap: 10, flexWrap: "wrap", alignItems: "center" }}>
                    <span className="muted">{t(lang, "forge.discovery.session")}</span>
                    <span className="mono">{text(sessionId, "—")}</span>
                    <span className="muted">{t(lang, "forge.discovery.state")}</span>
                    <Badge tone={confirmed ? "ok" : sessionState === "CANCELLED" ? "danger" : "info"}>
                      {text(sessionState, t(lang, "runs.unknown"))}
                    </Badge>
                  </div>
                ) : null}

                {/* Step 2 — the typed questions. They replace the idea form, at the top. */}
                {phase === "clarify" ? (
                  <ClarificationCard
                    lang={lang}
                    questions={questions}
                    domain={{
                      label: session?.domain_label,
                      confidence: session?.domain_confidence,
                    }}
                    drafts={drafts}
                    onDraft={(questionId, draft) =>
                      setDrafts((previous) => ({ ...previous, [questionId]: draft }))
                    }
                    busy={discoveryBusy}
                    onSend={() => void sendAnswers()}
                    onSkipRound={() => void skipClarification()}
                    onCancel={() => void cancelSession()}
                  />
                ) : null}

                {/* Step 3 — the specification, as a definition list from its own route. */}
                {phase === "spec" ? (
                  <div className="stack" style={{ gap: 12 }}>
                    <div>
                      <div className="label">{t(lang, "forge.discovery.spec.understood")}</div>
                      <div className="small muted">{t(lang, "forge.discovery.spec.hint")}</div>
                    </div>
                    {discoverySpec === null ? (
                      <div className="small muted">
                        {discoveryBusy === "spec"
                          ? t(lang, "forge.discovery.spec.loading")
                          : t(lang, "forge.discovery.spec.empty")}
                      </div>
                    ) : (
                      <dl className="forge-spec">
                        {SPEC_FIELDS.map(({ key, labelKey }) => {
                          const value = specValue(discoverySpec.specification, key);
                          return (
                            <div className="forge-spec-row" key={key}>
                              <dt className="label">{t(lang, labelKey)}</dt>
                              <dd className={value === "" ? "muted" : ""}>
                                {value === "" ? t(lang, "forge.discovery.spec.empty") : value}
                              </dd>
                            </div>
                          );
                        })}
                      </dl>
                    )}
                    {/* Two separate records, never merged: a decision the owner made and a value
                        Forge picked through «Не знаю — выбери оптимальный вариант». */}
                    {discoverySpec === null ? null : (
                      <div className="stack" style={{ gap: 8 }}>
                        <div>
                          <div className="label">{t(lang, "forge.discovery.spec.chosen")}</div>
                          <OriginList
                            lang={lang}
                            origins={discoverySpec.chosen ?? []}
                            empty={t(lang, "forge.discovery.spec.chosen.empty")}
                          />
                        </div>
                        <div>
                          <div className="label">{t(lang, "forge.discovery.spec.auto")}</div>
                          <OriginList
                            lang={lang}
                            origins={discoverySpec.auto_selected ?? []}
                            empty={t(lang, "forge.discovery.spec.auto.empty")}
                          />
                        </div>
                      </div>
                    )}
                    <div className="row" style={{ gap: 12, flexWrap: "wrap", alignItems: "center" }}>
                      <Button
                        tone="primary"
                        disabled={discoveryBusy === "confirm"}
                        onClick={() => void confirmSpecification()}
                      >
                        {discoveryBusy === "confirm"
                          ? t(lang, "runs.form.running")
                          : t(lang, "forge.discovery.spec.confirm")}
                      </Button>
                      <Button onClick={editAnswers}>{t(lang, "forge.discovery.spec.edit")}</Button>
                      <Button
                        tone="ghost"
                        disabled={discoveryBusy === "cancel"}
                        onClick={() => void cancelSession()}
                      >
                        {t(lang, "forge.discovery.cancel")}
                      </Button>
                    </div>
                    {typeof discoverySpec?.task_text === "string" && discoverySpec.task_text !== "" ? (
                      <details>
                        <summary className="small muted" style={{ cursor: "pointer" }}>
                          task_text
                        </summary>
                        <pre className="forge-output mono small">{discoverySpec.task_text}</pre>
                      </details>
                    ) : null}
                  </div>
                ) : null}

                {/* Step 4 — the launch. Provider, model, cost and the run button appear here and
                    nowhere earlier: the questions and the specification come first. */}
                {phase === "run" ? (
                  <div className="stack" style={{ gap: 14 }}>
                    <div className="row between" style={{ gap: 10, flexWrap: "wrap" }}>
                      <div className="label">{t(lang, "forge.discovery.run.title")}</div>
                      <Badge tone={confirmed ? "ok" : "warn"}>
                        {text(sessionState, t(lang, "runs.unknown"))}
                      </Badge>
                    </div>
                    <div className={`notice ${confirmed ? "" : "warn"}`}>
                      {confirmed
                        ? t(lang, "forge.discovery.run.ready")
                        : t(lang, "forge.discovery.run.preconfirm")}
                    </div>
                    <Field
                      label={t(lang, "forge.discovery.run.task")}
                      hint={t(lang, "forge.discovery.run.task.hint")}
                      error={descriptionError}
                    >
                      <textarea
                        className="input"
                        rows={3}
                        value={description}
                        onChange={(event) => setDescription(event.target.value)}
                        placeholder={t(lang, "forge.discovery.goal.placeholder")}
                      />
                    </Field>
                    {runControls}
                  </div>
                ) : null}

                {discoveryFailure !== null ? (
                  <FailureNotice lang={lang} failure={discoveryFailure} />
                ) : null}
                {discoveryNotice !== "" ? <div className="notice">{discoveryNotice}</div> : null}
              </div>
            </Card>
          </div>

          {/* --------------------------------------------------------------- execution */}
          <Card className="rise d2">
            <CardHead
              title={t(lang, "forge.execution.title")}
              hint={t(lang, "forge.execution.hint").replace("{sec}", POLL_INTERVAL_LABEL)}
              action={
                <Badge tone={runId === "" ? "neutral" : terminal ? "ok" : polling ? "info" : "warn"}>
                  {runId === ""
                    ? "—"
                    : terminal
                      ? t(lang, "forge.execution.terminal")
                      : polling
                        ? t(lang, "forge.execution.polling")
                        : t(lang, "forge.execution.waiting")}
                </Badge>
              }
            />
            {runId === "" ? (
              <div className="small muted">{t(lang, "forge.execution.empty")}</div>
            ) : recordFailure ? (
              <FailureNotice
                lang={lang}
                failure={recordFailure}
                onRetry={() => setPollNonce((value) => value + 1)}
              />
            ) : record === null ? (
              <div className="small muted">
                {recordLoading ? t(lang, "forge.execution.waiting") : t(lang, "forge.execution.empty")}
              </div>
            ) : (
              <div className="stack" style={{ gap: 14 }}>
                {/* The stages the record itself proves. The last stage is claimed only when files
                    were really written, so nine ✅ can never sit over an empty project; without
                    files it reads «Завершено без файлов». */}
                <RunProgress lang={lang} record={record} filesCreated={filesCreated} />
                {/* Everything technical about the *record* is here, collapsed: `state`, `status`,
                    `run_id`, `event_count`, the sequence number and the read-time classification.
                    None of it is above the fold any more. */}
                <details className="tech-details">
                  <summary>{t(lang, "forge.result.tech")}</summary>
                  <div className="stack" style={{ gap: 10, marginTop: 10 }}>
                    <Facts
                      items={[
                        [t(lang, "forge.execution.state"), text(record.state)],
                        [t(lang, "forge.execution.status"), text(record.status)],
                        [t(lang, "forge.result.outcome"), text(recordOutcome)],
                        [t(lang, "forge.result.run"), text(record.run_id)],
                        [t(lang, "forge.execution.events"), text(record.event_count)],
                        [t(lang, "forge.execution.sequence"), text(record.last_sequence_number)],
                        [t(lang, "forge.execution.attempt"), text(record.attempt_number)],
                        [t(lang, "forge.execution.interrupted"), yesNo(lang, record.interrupted)],
                        [t(lang, "forge.execution.updated"), text(record.updated_at)],
                        [t(lang, "forge.execution.classification"), text(record.classification?.outcome)],
                        [t(lang, "forge.execution.reason"), text(record.classification?.reason)],
                        [t(lang, "forge.execution.detail"), text(record.classification?.detail)],
                      ]}
                    />
                    {record.classification?.evidence && record.classification.evidence.length > 0 ? (
                      <div>
                        <div className="label">{t(lang, "forge.execution.evidence")}</div>
                        <div className="row" style={{ gap: 6, flexWrap: "wrap", marginTop: 6 }}>
                          {record.classification.evidence.map((item) => (
                            <Badge key={item} tone="neutral">
                              {item}
                            </Badge>
                          ))}
                        </div>
                      </div>
                    ) : null}
                  </div>
                </details>
              </div>
            )}
          </Card>

          {/* ----------------------------------------------------------------- result */}
          <Card className="rise d3">
            <CardHead title={t(lang, "forge.result.title")} hint={t(lang, "forge.result.hint")} />
            {!dispatched ? (
              <div className="small muted">{t(lang, "forge.result.empty")}</div>
            ) : (
              <div className="stack" style={{ gap: 14 }}>
                {/* ONE plain-language sentence, chosen from the facts and never from `state`. The
                    first thing a reader meets is whether a project exists -- not COMPLETED. */}
                <div className="project-truth result-headline" data-state={headline.state}>
                  <div className="stack" style={{ gap: 10 }}>
                    <div className="label">{t(lang, "forge.result.headline.label")}</div>
                    <div className="result-headline-text">{headlineText(lang, headline)}</div>
                    {headline.actions.length > 0 ? (
                      <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
                        {headline.actions.map((action) => {
                          // The three project actions need a real lifecycle project id.
                          // Without one the button is visibly off and the sentence below
                          // says whether the run has no project at all or the project it
                          // names is not in the list -- never a click that lands elsewhere.
                          const needsProject =
                            action === "open_files" || action === "launch_local" || action === "verify";
                          const blocked = needsProject && resultProjectId === "";
                          return (
                            <Button
                              key={action}
                              size="small"
                              tone={action === "retry" || action === "answer" ? "primary" : "ghost"}
                              disabled={blocked || (action === "retry" && dispatch.status === "pending")}
                              disabledReason={blocked ? resultProjectBlockedReason : undefined}
                              onClick={() => runResultAction(action)}
                            >
                              {t(lang, RESULT_ACTION_LABELS[action])}
                            </Button>
                          );
                        })}
                      </div>
                    ) : null}
                    {headline.actions.some(
                      (action) =>
                        (action === "open_files" || action === "launch_local" || action === "verify") &&
                        resultProjectId === ""
                    ) ? (
                      <div className="small muted">{resultProjectBlockedReason}</div>
                    ) : null}
                    {headline.kind === "ready" || headline.kind === "unverified" ? (
                      <div className="small muted">{t(lang, "forge.result.action.note")}</div>
                    ) : null}
                    {headline.kind === "plan" ? (
                      <div className="small muted">{t(lang, "forge.result.retry.note")}</div>
                    ) : null}
                  </div>
                </div>

                {/* WHAT WAS CHECKED, WHAT WAS ONLY SAID, WHAT IS UNKNOWN. One line that
                    opens into the backend's three lists (`app/reports/evidence.py`):
                    the numbers are the list lengths, so a run whose work was never
                    verified shows «Проверено: 0» beside the sentences the model wrote,
                    instead of a success the run cannot prove. */}
                <EvidenceBlock
                  lang={lang}
                  evidence={dispatched.response.evidence}
                  testId="forge-evidence"
                />

                {/* «Провайдер · Модель · тариф · почему»: the routing decision this run
                    made, above the fold and always visible — the owner never has to open a
                    dropdown or a technical block to learn what ran and why. */}
                <RouteLine lang={lang} choice={dispatched.response.model_choice} testId="forge-route-line" />

                {/* The new run the answers started. Naming its id is the proof the click moved the
                    flow forward instead of re-opening the same card: the record card below then
                    polls THIS id, so its progress is the new run's progress. */}
                {clarificationSentTo !== "" && clarificationSentTo === runId ? (
                  <div className="notice">
                    {t(lang, "forge.result.clarification.new_run").replace("{run}", runId)}
                  </div>
                ) : null}

                {/* The questions the final answer asked, when it really asked any — listed as
                    they arrived, never re-worded. A plan that merely ends in "?" is not a
                    question list: that case is the «plan instead of files» headline above.
                    On the final round there is nothing to answer into, so the list is shown
                    without inputs: the card above already says the loop is over. */}
                {askedQuestions ? (
                  <div className="notice warn">
                    <div className="label">{t(lang, "forge.result.questions")}</div>
                    <ol className="forge-questions" style={{ marginTop: 8 }}>
                      {responseQuestions.map((question) => (
                        <li className="forge-question" key={question}>
                          {question}
                        </li>
                      ))}
                    </ol>
                    {headline.kind === "questions" ? (
                      // One input per question, in the card's own order. «Ответить» above sends
                      // them as `clarification_answers`, so the next run's task text carries
                      // `- question → answer` for each one the owner filled in.
                      <div className="stack" style={{ gap: 10, marginTop: 10 }}>
                        {responseQuestions.map((question) => (
                          <Field key={question} label={question}>
                            <textarea
                              className="input"
                              rows={2}
                              value={clarificationDrafts[question] ?? ""}
                              placeholder={t(lang, "forge.result.clarification.answer_placeholder")}
                              onChange={(event) =>
                                setClarificationDrafts((previous) => ({
                                  ...previous,
                                  [question]: event.target.value,
                                }))
                              }
                            />
                          </Field>
                        ))}
                      </div>
                    ) : null}
                    <div className="small" style={{ marginTop: 6 }}>
                      {headline.kind === "questions"
                        ? t(lang, "forge.result.clarification.hint")
                        : t(lang, "forge.result.clarification.final_hint")}
                    </div>
                  </div>
                ) : null}

                {/* Everything technical about the *answer* is here, collapsed: the raw output,
                    the response facts, the save facts, the writer's own status and English reason,
                    and the diff. */}
                <details className="tech-details">
                  <summary>{t(lang, "forge.result.tech")}</summary>
                  <div className="stack" style={{ gap: 14, marginTop: 12 }}>
                    <div>
                      <div className="label">{t(lang, "forge.result.output")}</div>
                      <pre className="forge-output mono small">
                        {typeof dispatched.response.output === "string" && dispatched.response.output !== ""
                          ? dispatched.response.output
                          : t(lang, "forge.result.no_output")}
                      </pre>
                    </div>

                    {dispatched.response.error ? (
                      <div className="notice danger">{String(dispatched.response.error)}</div>
                    ) : null}

                    <Facts
                      items={[
                        [
                          t(lang, "runs.result.tokens"),
                          dispatched.response.tokens === null || dispatched.response.tokens === undefined
                            ? t(lang, "runs.unknown")
                            : String(dispatched.response.tokens),
                        ],
                        [
                          t(lang, "runs.result.cost"),
                          typeof dispatched.response.cost === "number"
                            ? `$${fixed(dispatched.response.cost, 4)}`
                            : t(lang, "runs.unknown"),
                        ],
                        [t(lang, "runs.result.duration"), fixed(dispatched.response.duration_seconds, 3)],
                        [t(lang, "runs.result.provider"), text(dispatched.response.provider_used)],
                        [t(lang, "runs.result.model"), text(dispatched.response.model_used)],
                        [t(lang, "forge.result.outcome"), text(dispatched.response.outcome)],
                        [t(lang, "forge.result.state"), text(dispatched.response.state)],
                        [
                          t(lang, "forge.result.files.created"),
                          typeof dispatched.response.files_created === "number"
                            ? String(dispatched.response.files_created)
                            : t(lang, "runs.unknown"),
                        ],
                        [
                          t(lang, "forge.result.write_mode"),
                          text(dispatched.response.write_mode, t(lang, "forge.launch.write.none")),
                        ],
                        // The retry is a write-mode fact from the run's own record; a run that
                        // did not write has nothing to say about it, so the line is omitted.
                        ...(requestedWriteMode
                          ? ([
                              [
                                t(lang, "forge.result.retry"),
                                writeRetryAttempted === null
                                  ? t(lang, "runs.unknown")
                                  : yesNo(lang, writeRetryAttempted),
                              ],
                            ] as Array<[string, string]>)
                          : []),
                        [
                          t(lang, "forge.result.manifest"),
                          yesNo(lang, dispatched.response.artifact_manifest_saved),
                        ],
                        [
                          t(lang, "forge.result.cost.tier"),
                          text(dispatched.response.cost_tier, t(lang, "forge.launch.cost.unknown")),
                        ],
                        [t(lang, "forge.result.task"), text(dispatched.response.task_id)],
                        [t(lang, "forge.result.project"), text(dispatched.response.project_id)],
                        [t(lang, "forge.result.run"), text(dispatched.response.run_id)],
                      ]}
                    />

                    {/* The three independent save facts the response carries. They used to be
                        stated above the fold, where one boolean hid an empty project behind a
                        stored run record; they are facts for an auditor, so they moved here. */}
                    <div>
                      <div className="label">{t(lang, "forge.result.saved.title")}</div>
                      <div style={{ marginTop: 8 }}>
                        <Facts
                          items={[
                            [
                              t(lang, "forge.result.saved.run"),
                              yesNo(lang, dispatched.response.run_record_saved),
                            ],
                            [
                              t(lang, "forge.result.saved.project"),
                              yesNo(lang, dispatched.response.project_created),
                            ],
                            [
                              t(lang, "forge.result.saved.files"),
                              yesNo(lang, dispatched.response.project_files_created),
                            ],
                          ]}
                        />
                      </div>
                    </div>

                    <CodeWriteDetails lang={lang} write={codeWrite} />

                    <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
                      <Badge tone={dispatched.facts.usage_basis === "ACTUAL" ? "ok" : "warn"}>
                        {t(lang, "runs.basis.usage")}:{" "}
                        {text(dispatched.facts.usage_basis, t(lang, "runs.unknown"))}
                      </Badge>
                      <Badge tone={dispatched.facts.cost_basis === "ACTUAL" ? "ok" : "warn"}>
                        {t(lang, "runs.basis.cost")}:{" "}
                        {text(dispatched.facts.cost_basis, t(lang, "runs.unknown"))}
                      </Badge>
                      {dispatched.facts.idempotent_replay === true ? (
                        <Badge tone="info">{t(lang, "runs.basis.replay")}</Badge>
                      ) : null}
                    </div>

                    {!basisReported ? (
                      <div className="small muted">{t(lang, "forge.result.basis.missing")}</div>
                    ) : null}
                  </div>
                </details>
              </div>
            )}
          </Card>

          {/* -------------------------------------------------------------- artifacts
              Only after a run exists: the card states that the route is missing, and before a run
              there is nothing to state. */}
          {dispatched !== null ? (
            <Card className="rise d3">
              <CardHead
                title={t(lang, "forge.artifacts.title")}
                action={<Badge tone="warn">{t(lang, "forge.artifacts.none")}</Badge>}
              />
              <div className="stack small" style={{ gap: 10 }}>
                <div className="muted">{t(lang, "forge.artifacts.body")}</div>
                <div className="muted">{t(lang, "forge.artifacts.review")}</div>
                <div>
                  <div className="label">{t(lang, "forge.artifacts.routes")}</div>
                  <div className="stack mono small" style={{ gap: 4, marginTop: 6 }}>
                    {[
                      "GET  /api/status",
                      "GET  /api/projects",
                      "GET  /api/agents",
                      "GET  /api/models",
                      "POST /api/tasks/run",
                      "GET  /api/runs/{run_id}",
                    ].map((route) => (
                      <span key={route} className="muted">
                        {route}
                      </span>
                    ))}
                  </div>
                </div>
              </div>
            </Card>
          ) : null}
        </div>

        <div className="stack">
          {/* ---------------------------------------------------------------- projects */}
          <Card className="rise d1">
            <CardHead
              title={t(lang, "forge.projects.title")}
              hint={t(lang, "forge.projects.hint")}
              action={
                <Badge tone={projects.status === "ready" ? "info" : "neutral"}>
                  {projects.status === "ready" ? projects.items.length : t(lang, "state.loading")}
                </Badge>
              }
            />
            {projects.status === "loading" ? (
              <div className="small muted">{t(lang, "state.loading")}</div>
            ) : projects.status === "failed" ? (
              <FailureNotice lang={lang} failure={projects.failure} onRetry={() => void loadProjectList()} />
            ) : projects.items.length === 0 ? (
              <div className="small muted">{t(lang, "forge.projects.empty")}</div>
            ) : (
              <div className="stack" style={{ gap: 8 }}>
                {projects.items.map((project) => {
                  const id = String(project.project_id ?? "");
                  const isActive = id !== "" && id === activeProject;
                  const isSelected = id === selectedProject;
                  return (
                    <button
                      key={id || `${String(project.name ?? "")}-${String(project.path ?? "")}`}
                      className={`forge-project${isSelected ? " selected" : ""}`}
                      onClick={() => setSelectedProject(id)}
                    >
                      <span className="row" style={{ gap: 8 }}>
                        <StatusDot tone={isActive ? "ok" : "idle"} />
                        <span style={{ fontWeight: 600 }}>{text(project.name)}</span>
                        {isActive ? <Badge tone="ok">{t(lang, "projects.active")}</Badge> : null}
                        {isSelected ? <Badge tone="info">{t(lang, "forge.projects.selected")}</Badge> : null}
                      </span>
                      <span className="mono small muted">{text(project.project_id)}</span>
                      <span className="mono small muted" style={{ wordBreak: "break-all" }}>
                        {text(project.path)}
                      </span>
                    </button>
                  );
                })}
              </div>
            )}
          </Card>

          {/* ----------------------------------------------------------------- agents
              Collapsed to one line: the agent list, its models and its capabilities live in
              «Провайдеры», which is the full diagnostics hub. */}
          <Card className="rise d2">
            <CardHead
              title={t(lang, "forge.agents.title")}
              hint={t(lang, "forge.agents.hint")}
              action={
                <Badge tone={live ? "info" : "neutral"}>
                  {backend.status === "live" ? backend.agents.length : "—"}
                </Badge>
              }
            />
            <div className="stack small" style={{ gap: 10 }}>
              <div className="muted">
                {backend.status === "live" && backend.agents.length === 0
                  ? t(lang, "forge.agents.empty")
                  : t(lang, "forge.agents.link")}
              </div>
              <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
                <Button size="small" onClick={onOpenProviders}>
                  {t(lang, "forge.agents.open")}
                </Button>
              </div>
            </div>
          </Card>

          {/* --------------------------------------------------------------- settings */}
          <Card className="rise d3">
            <CardHead
              title={t(lang, "forge.settings.title")}
              hint={t(lang, "forge.settings.hint")}
              action={<Badge tone={live ? "ok" : "warn"}>{live ? "GET" : "—"}</Badge>}
            />
            {backend.status === "loading" ? (
              <div className="small muted">{t(lang, "state.loading")}</div>
            ) : backend.status === "offline" ? (
              <div className="notice danger">{t(lang, "state.offline")}</div>
            ) : (
              <div className="stack" style={{ gap: 12 }}>
                <Facts
                  items={[
                    [t(lang, "forge.settings.core"), text(status?.core)],
                    [t(lang, "forge.settings.api"), text(status?.api)],
                    [t(lang, "forge.settings.provider_status"), text(status?.provider_status)],
                    [
                      t(lang, "forge.settings.providers"),
                      `${text(status?.providers_ready, "0")} / ${text(status?.providers_total, "0")}`,
                    ],
                    [t(lang, "forge.settings.project"), text(status?.active_project)],
                    [t(lang, "forge.settings.timestamp"), text(status?.timestamp)],
                    [
                      t(lang, "forge.settings.enabled"),
                      enabledProviders.length > 0
                        ? enabledProviders.join(", ")
                        : t(lang, "forge.settings.none"),
                    ],
                    [
                      t(lang, "forge.settings.default"),
                      text(status?.details?.default_provider, t(lang, "forge.settings.none")),
                    ],
                    [
                      t(lang, "forge.settings.registry"),
                      registryCounts
                        ? `${text(registryCounts.providers, "0")} / ${text(
                            registryCounts.models,
                            "0"
                          )} / ${text(registryCounts.accounts, "0")}`
                        : t(lang, "forge.settings.none"),
                    ],
                    [
                      t(lang, "forge.settings.registry.source"),
                      text(registrySource, t(lang, "forge.settings.none")),
                    ],
                  ]}
                />
                <div className="small muted">{t(lang, "forge.settings.registry.note")}</div>

                <div className="field">
                  <span className="label">{t(lang, "forge.settings.language")}</span>
                  <div className="segment">
                    <button className={lang === "ru" ? "on" : ""} onClick={() => onLanguage("ru")}>
                      RU
                    </button>
                    <button className={lang === "en" ? "on" : ""} onClick={() => onLanguage("en")}>
                      EN
                    </button>
                  </div>
                  <span className="hint">{t(lang, "forge.settings.language.note")}</span>
                </div>
              </div>
            )}
          </Card>
        </div>
      </div>
    </div>
  );
};
