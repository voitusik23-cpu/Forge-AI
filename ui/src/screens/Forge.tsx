// Кузница — the FORGE AI desktop workspace.
//
// The workspace is **questions first**. One screen, and the order on it is the order of the work:
//
//   1. «Задача» (top of the page) — the idea, then the clarification card that replaces the idea
//      form the moment the first submit returned questions, then the specification, then the launch
//      step (provider · model · cost_tier · estimated_cost, the write switch and the run button);
//   2. «Выполнение» — GET /api/runs/{run_id}, polled every 1.75 s until the record is terminal;
//   3. «Запись в проект» — only when the run recorded a code_write;
//   4. «Результат» — the POST /api/tasks/run answer, its outcome and the three independent save
//      facts (run record / project / project files) the response carries;
//   5. «Артефакты и изменения» — only once a run exists (there is no artifact route in
//      app/api/server.py, and the card says so instead of drawing an invented list);
//   6. «Проекты» and «Настройки» in the right-hand column. The agent list lives in «Провайдеры»,
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
import { RunProgress } from "../components/RunProgress";
import { t, type Lang } from "../i18n";
import type { BackendState } from "../api/client";
import {
  TASK_CATEGORIES,
  cancelDiscovery,
  catalogueEntryFor,
  catalogueModelsFor,
  codeWriteOf,
  confirmDiscovery,
  dispatchTask,
  isTerminalRecord,
  loadDiscoverySpecification,
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
  type ProjectInfo,
  type RunRecord,
  type TaskCategoryValue,
} from "../api/forge";

// Faster than the 15 s backend poll: a run's own state is what the user watches here.
const POLL_INTERVAL_MS = 1750;
const POLL_INTERVAL_LABEL = (POLL_INTERVAL_MS / 1000).toFixed(2).replace(/0$/, "");

const text = (value: unknown, fallback = "—"): string =>
  value === undefined || value === null || value === "" ? fallback : String(value);

const fixed = (value: unknown, digits: number): string =>
  typeof value === "number" && Number.isFinite(value) ? value.toFixed(digits) : "—";

const yesNo = (lang: Lang, value: unknown): string => {
  if (typeof value !== "boolean") return t(lang, "runs.unknown");
  return value ? (lang === "ru" ? "да" : "yes") : (lang === "ru" ? "нет" : "no");
};

const newIdempotencyKey = (): string =>
  typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `forge-${Date.now()}-${Math.random().toString(16).slice(2)}`;

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
type DiscoveryPhase = "idea" | "clarify" | "spec" | "run";

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

/** The step rail: which step is current, which ones were actually completed. */
const StepRail = ({ lang, phase }: { lang: Lang; phase: DiscoveryPhase }) => {
  const current = DISCOVERY_STEPS.findIndex((step) => step.id === phase);
  return (
    <ol className="forge-steps">
      {DISCOVERY_STEPS.map((step, index) => {
        const state = index < current ? "done" : index === current ? "current" : "next";
        return (
          <li className={`forge-step ${state}`} key={step.id}>
            <span className="forge-step-index">{index + 1}</span>
            <span>{t(lang, step.labelKey)}</span>
            {index === current ? (
              <span className="small muted">· {t(lang, "forge.discovery.step.current")}</span>
            ) : null}
          </li>
        );
      })}
    </ol>
  );
};

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
 * Every line here comes from the record: the status, the post-write verification, the exact
 * per-file +/- counts and the unified diff. When the record carries no `code_write` at all the
 * card says so instead of drawing an empty list that would read as "nothing to report".
 */
const CodeWriteCard = ({ lang, write }: { lang: Lang; write: CodeWriteResult | null }) => {
  const status = write ? String(write.status ?? "") : "";
  const tone = WRITE_TONES[status] ?? "neutral";
  const files: Array<Record<string, unknown>> = Array.isArray(write?.files)
    ? write.files.filter((file): file is Record<string, unknown> => !!file && typeof file === "object")
    : [];
  const diff = typeof write?.diff === "string" ? write.diff : "";
  const reason = text(write?.reason, t(lang, "forge.write.reason.empty"));

  return (
    <Card className="rise d2">
      <CardHead
        title={t(lang, "forge.write.title")}
        hint={t(lang, "forge.write.hint")}
        action={
          <Badge tone={write ? tone : "neutral"}>
            {write ? text(status, t(lang, "runs.unknown")) : t(lang, "forge.write.none")}
          </Badge>
        }
      />
      {write === null ? (
        <div className="notice warn">{t(lang, "forge.write.absent")}</div>
      ) : (
        <div className="stack" style={{ gap: 14 }}>
          <Facts
            items={[
              [t(lang, "forge.write.status"), text(status, t(lang, "runs.unknown"))],
              [t(lang, "forge.write.verification"), text(write.verification, t(lang, "runs.unknown"))],
              [t(lang, "forge.write.files.count"), String(files.length)],
            ]}
          />

          {status !== "applied" ? (
            <div className={`notice ${status === "rejected" ? "danger" : "warn"}`}>
              <div className="label">{t(lang, "forge.write.reason")}</div>
              <div className="mono small" style={{ marginTop: 6, whiteSpace: "pre-wrap" }}>
                {reason}
              </div>
            </div>
          ) : null}

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
    </Card>
  );
};

export const Forge = ({
  lang,
  backend,
  onRefresh,
  onLanguage,
  onHelp,
  onOpenProviders,
}: {
  lang: Lang;
  backend: BackendState;
  onRefresh: () => void;
  onLanguage: (lang: Lang) => void;
  onHelp: () => void;
  /** Opens «Провайдеры»: the agent list is the hub's job, not the workspace's. */
  onOpenProviders: () => void;
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

  const [provider, setProvider] = useState("");

  useEffect(() => {
    // Nothing reported means nothing to choose from: keep whatever the user had instead of
    // silently clearing the selection while a backend poll is in flight.
    if (providerChoice.list.length === 0) return;
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
    setDiscoveryNotice(t(lang, "forge.discovery.spec.confirmed"));
    setPhase("run");
  };

  /** Back from step 3 to step 2. Only possible before the confirmation, by the Core's own rule. */
  const editAnswers = () => {
    setDiscoveryNotice("");
    setPhase("clarify");
  };

  /**
   * The result's «Ответить на вопросы» button.
   *
   * A run whose outcome is `CLARIFICATION_REQUIRED` stopped because its answer asked questions, so
   * the honest next step is the clarification card, not a second run. An open session is reopened
   * as it is; a confirmed or cancelled one cannot accept answers any more (the Core refuses with
   * 409 `discovery_state_conflict`), so a fresh session is opened from the same idea.
   */
  const answerClarification = async () => {
    if (sessionId !== "" && !confirmed && sessionState !== "CANCELLED") {
      setDiscoveryNotice("");
      setPhase("clarify");
      return;
    }
    await continueFromIdea();
  };

  // ------------------------------------------------------------------ run record
  const [record, setRecord] = useState<RunRecord | null>(null);
  const [recordFailure, setRecordFailure] = useState<Failure | null>(null);
  const [recordLoading, setRecordLoading] = useState(false);
  const [pollNonce, setPollNonce] = useState(0);

  const dispatched = dispatch.status === "sent" ? dispatch.result : null;
  const runId = String(dispatched?.response.run_id ?? "");
  const terminal = isTerminalRecord(record);
  // `project_state.code_write` is written by the Core only for a write-mode code run, so its
  // presence is the record's own proof that the writer ran — never inferred from the checkbox.
  const codeWrite = codeWriteOf(record);

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

  // The offline executor is the one provider the Core's own composition names as "no files":
  // it answers with a deterministic report and writes nothing. The write switch is therefore
  // disabled there, and equally disabled when the run *cannot* write at all — a category other than
  // `code` (`_write_mode_enabled` ignores write_mode for it) or no routable provider.
  const mockSelected = provider === "mock";
  const writeUnavailable = mockSelected
    ? t(lang, "forge.task.write.offline")
    : category !== "code"
      ? t(lang, "forge.task.write.noncode")
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
  const runBlocked = paidModel && !paidConfirmed;

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

  const submit = async () => {
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
    const controller = new AbortController();
    inFlight.current = controller;
    // Only a provider the Core itself reported is sent; an empty or stale selection leaves the
    // routing to the backend instead of naming a provider that is not in the list.
    const providerToSend = providerChoice.list.includes(provider) ? provider : undefined;
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
        discoverySessionId: sessionId,
        idempotencyKey,
        signal: controller.signal,
      });
      if (controller.signal.aborted) {
        // Aborted by the user: no result to show, and the in-flight state is cleared below.
        setDispatch({ status: "idle" });
        setNotice(t(lang, "forge.task.cancelled"));
      } else if (outcome.ok) {
        setDispatch({ status: "sent", result: outcome.value });
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
  // chooses" when the Core reported nothing to choose from.
  const routeLabel =
    [
      providerChoice.list.includes(provider) ? provider : "",
      providerChoice.list.includes(provider) ? selectedModel : "",
    ]
      .filter((part) => part !== "")
      .join(" · ") || t(lang, "forge.task.route.auto");

  /**
   * The launch step: provider, model, category, the cost facts of that pair, the write switch and
   * the submit itself.
   *
   * It is drawn on step 4 only. The questions and the specification come first, so the owner is
   * never asked to pick a model before the Core has said what the project is.
   */
  const runControls = (
    <div className="stack" style={{ gap: 14 }}>
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
              <option value="">{t(lang, "forge.settings.none")}</option>
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
      </div>

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

      {/* A paid pair runs only after a deliberate confirmation; free and cheap need none. */}
      {paidModel ? (
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
            ? mockSelected
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
                <StepRail lang={lang} phase={phase} />

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
                <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
                  <Badge tone={record.classification?.is_success === true ? "ok" : "neutral"}>
                    {text(record.state, t(lang, "runs.unknown"))}
                  </Badge>
                  {recordOutcome !== "" ? (
                    <Badge
                      tone={
                        recordOutcome === "PROJECT_COMPLETED"
                          ? "ok"
                          : recordOutcome === "FAILED"
                            ? "danger"
                            : "warn"
                      }
                    >
                      {recordOutcome}
                    </Badge>
                  ) : null}
                  <span className="mono small muted">{text(record.run_id)}</span>
                </div>
                {/* The stages the record itself proves. A terminal record stops the pulse and
                    prints its own state; no percentage is drawn anywhere. */}
                <RunProgress lang={lang} record={record} />
                <Facts
                  items={[
                    [t(lang, "forge.execution.state"), text(record.state)],
                    [t(lang, "forge.execution.status"), text(record.status)],
                    [t(lang, "forge.result.outcome"), text(recordOutcome)],
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
            )}
          </Card>

          {/* --------------------------------------------------------------- code write */}
          {codeWrite !== null || (requestedWriteMode && terminal) ? (
            <CodeWriteCard lang={lang} write={codeWrite} />
          ) : null}

          {/* ----------------------------------------------------------------- result */}
          <Card className="rise d3">
            <CardHead
              title={t(lang, "forge.result.title")}
              hint={t(lang, "forge.result.hint")}
              action={
                dispatched ? (
                  <span className="row" style={{ gap: 6, flexWrap: "wrap" }}>
                    <Badge
                      tone={
                        dispatched.response.outcome === "PROJECT_COMPLETED"
                          ? "ok"
                          : dispatched.response.outcome === "FAILED"
                            ? "danger"
                            : "neutral"
                      }
                    >
                      {text(dispatched.response.outcome, t(lang, "runs.unknown"))}
                    </Badge>
                    <Badge tone={dispatched.response.success === true ? "ok" : "warn"}>
                      {text(dispatched.response.state, t(lang, "runs.unknown"))}
                    </Badge>
                  </span>
                ) : null
              }
            />
            {!dispatched ? (
              <div className="small muted">{t(lang, "forge.result.empty")}</div>
            ) : (
              <div className="stack" style={{ gap: 14 }}>
                {/* The three independent save facts, exactly as the response reports them. The old
                    single «Запись сохранена: да» badge is gone: one boolean hid an empty project
                    behind a stored run record. */}
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

                {/* A run that stopped to ask questions lists them and offers the discovery
                    session, instead of looking like a finished project. */}
                {dispatched.response.outcome === "CLARIFICATION_REQUIRED" ? (
                  <div className="notice warn">
                    <div className="label">{t(lang, "forge.result.clarification.title")}</div>
                    {Array.isArray(dispatched.response.clarification_questions) &&
                    dispatched.response.clarification_questions.length > 0 ? (
                      <ol className="forge-questions" style={{ marginTop: 8 }}>
                        {dispatched.response.clarification_questions.map((question) => (
                          <li className="forge-question" key={question}>
                            {question}
                          </li>
                        ))}
                      </ol>
                    ) : (
                      <div className="small" style={{ marginTop: 6 }}>
                        {t(lang, "forge.result.clarification.empty")}
                      </div>
                    )}
                    <div className="row" style={{ marginTop: 10 }}>
                      <Button size="small" tone="primary" onClick={() => void answerClarification()}>
                        {t(lang, "forge.result.clarification.answer")}
                      </Button>
                    </div>
                    <div className="small" style={{ marginTop: 6 }}>
                      {t(lang, "forge.result.clarification.hint")}
                    </div>
                  </div>
                ) : null}

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

                {/* Files were really written: the entry point and the local preview are on the
                    project card, so this line points there instead of promising a launch here. */}
                {typeof dispatched.response.files_created === "number" &&
                dispatched.response.files_created > 0 ? (
                  <div className="notice">{t(lang, "forge.result.launch_note")}</div>
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
