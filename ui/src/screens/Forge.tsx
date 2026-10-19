// Кузница — the FORGE AI desktop workspace.
//
// One screen, six real answers from the Core, and nothing else:
//
//   projects  <- GET  /api/projects            (loaded here, because loadBackend does not)
//   providers <- GET  /api/status + /api/agents (enabled_providers, default_provider, agents)
//   dispatch  -> POST /api/tasks/run
//   execution <- GET  /api/runs/{run_id}, polled every 1.75 s until the record is terminal
//   agents    <- GET  /api/agents
//   settings  <- GET  /api/status
//
// There is no artifact or approval route in app/api/server.py, so neither a fake artifact list
// nor an accept/reject button is drawn: the card says what is missing and why. Every number on
// this screen is copied from a response; none is computed to look like progress.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Badge, Button, Card, CardHead, Field, StatusDot } from "../components/Primitives";
import { IconAnvil, IconPlay, IconRefresh } from "../components/Icons";
import { t, type Lang } from "../i18n";
import type { BackendState } from "../api/client";
import {
  TASK_CATEGORIES,
  cancelDiscovery,
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

/** The one honest sentence per failure, with the status kept visible next to it. */
const failureLabel = (lang: Lang, failure: Failure): string => {
  if (failure.kind === "unauthorized") return t(lang, "forge.state.unauthorized");
  if (failure.kind === "disconnected") return t(lang, "forge.state.disconnected");
  const code = failure.code ?? "";
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
        failure.detail ? (
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
}: {
  lang: Lang;
  backend: BackendState;
  onRefresh: () => void;
  onLanguage: (lang: Lang) => void;
  onHelp: () => void;
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
  const [answerDraft, setAnswerDraft] = useState<Record<string, string>>({});
  const [discoveryFailure, setDiscoveryFailure] = useState<Failure | null>(null);
  const [discoveryNotice, setDiscoveryNotice] = useState("");

  const session = discoverySession?.session ?? null;
  const sessionId = String(session?.session_id ?? "");
  const sessionState = String(session?.state ?? "");
  const questions = discoverySession?.questions ?? [];
  const confirmed = sessionState === "CONFIRMED";

  /**
   * A refused dispatch belongs to the step it was attempted from.
   *
   * Without this, the honest 409 of one step would sit under the next step's form and look like
   * that step had already failed. Clearing it says only "this step has not dispatched yet".
   */
  const dropFailedDispatch = () => {
    if (dispatch.status === "failed") setDispatch({ status: "idle" });
  };

  /** Take a session answer as it came back, keeping what the user already typed. */
  const adoptSession = (payload: DiscoverySessionPayload) => {
    setDiscoverySession(payload);
    const stored = payload.session?.answers ?? [];
    const draft: Record<string, string> = {};
    for (const answer of stored) {
      const id = String(answer.question_id ?? "");
      if (id !== "" && typeof answer.text === "string" && answer.text !== "") draft[id] = answer.text;
    }
    setAnswerDraft(draft);
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

  /** Step 2: the answers the user actually wrote. An empty field is not sent. */
  const sendAnswers = async () => {
    if (discoveryBusy !== "" || sessionId === "") return;
    const answers = questions
      .map((question) => {
        const id = String(question.id ?? "");
        return { question_id: id, text: (answerDraft[id] ?? "").trim() };
      })
      .filter((answer) => answer.question_id !== "" && answer.text !== "");
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

  // The mock provider is the offline executor: it answers without touching a file. The write
  // switch is therefore disabled for it and the run is labelled honestly. For every real
  // provider the switch stays live, because the guarded writer works there.
  const mockSelected = provider === "mock";
  const effectiveWriteMode = writeMode && !mockSelected;

  const submit = async () => {
    // The button is deliberately never disabled. A disabled button with no reason is what made
    // "Запустить задачу" look dead; every click now either dispatches or says why it did not.
    if (dispatch.status === "pending") {
      setNotice(t(lang, "forge.task.busy"));
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

  // Whether the run was persisted, from the strongest evidence actually available: the
  // X-Forge-History-Recorded header when it reached the interface, otherwise the result of
  // reading the record back with GET /api/runs/{run_id} — a 200 is proof it was stored, a 404
  // is proof it was not, and anything else leaves the answer unknown.
  const historyPersisted: boolean | null =
    dispatched?.facts.history_recorded ??
    (record !== null
      ? true
      : recordFailure !== null && recordFailure.kind === "error" && recordFailure.status === 404
        ? false
        : null);

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
   * The run block: provider, model, category, write switch and the submit itself.
   *
   * Drawn on step 4 and, deliberately, one step earlier — the same button that would run a
   * confirmed session is the one that shows the Core's 409 refusal while it is not confirmed.
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

      {/* The offline executor is labelled as what it is: an answer without a file change. */}
      {mockSelected ? <div className="notice">{t(lang, "forge.discovery.mock.note")}</div> : null}

      <div className="row" style={{ gap: 12, flexWrap: "wrap", alignItems: "center" }}>
        <Button tone="primary" onClick={() => void submit()}>
          <IconPlay size={15} />
          {dispatch.status === "pending" ? t(lang, "runs.form.running") : t(lang, "runs.form.submit")}
        </Button>
        {dispatch.status === "pending" ? (
          <Button size="small" tone="danger" onClick={cancel}>
            {t(lang, "forge.task.cancel")}
          </Button>
        ) : null}
        <label className={`forge-toggle${mockSelected ? " disabled" : ""}`}>
          <input
            type="checkbox"
            checked={effectiveWriteMode}
            disabled={mockSelected}
            onChange={(event) => setWriteMode(event.target.checked)}
          />
          <span>{t(lang, "forge.task.write.label")}</span>
          {mockSelected ? (
            <span className="small muted">· {t(lang, "forge.discovery.mock.write")}</span>
          ) : null}
        </label>
        {mockSelected ? null : <span className="small muted">{t(lang, "forge.task.guarded")}</span>}
      </div>
      <div className="small muted forge-write-warning">{t(lang, "forge.task.write.warning")}</div>

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
          {/* ---------------------------------------------------------------- projects */}
          <Card className="rise">
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

          {/* -------------------------------------------------------------------- task */}
          <Card className="rise d1">
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

              {/* Step 2 — the questions the Core returned, as a numbered list. */}
              {phase === "clarify" ? (
                <div className="stack" style={{ gap: 12 }}>
                  <div className="row between" style={{ gap: 10, flexWrap: "wrap" }}>
                    <div className="label">
                      {t(lang, "forge.discovery.questions.title").replace("{n}", String(questions.length))}
                    </div>
                    <Badge tone={sessionState === "READY_FOR_PLANNING" ? "info" : "neutral"}>
                      {text(sessionState, t(lang, "runs.unknown"))}
                    </Badge>
                  </div>
                  {questions.length === 0 ? (
                    <div className="small muted">{t(lang, "forge.discovery.questions.empty")}</div>
                  ) : (
                    <ol className="forge-questions">
                      {questions.map((question) => {
                        const id = String(question.id ?? "");
                        return (
                          <li className="forge-question" key={id}>
                            <div className="row between" style={{ gap: 10, flexWrap: "wrap" }}>
                              <span style={{ fontWeight: 600 }}>{text(question.text)}</span>
                              <Badge tone={question.required ? "warn" : "neutral"}>
                                {question.required
                                  ? t(lang, "forge.discovery.question.required")
                                  : t(lang, "forge.discovery.question.optional")}
                              </Badge>
                            </div>
                            <div className="small muted">
                              {t(lang, "forge.discovery.question.why")} {text(question.why, "—")}
                            </div>
                            <input
                              className="input"
                              type="text"
                              value={answerDraft[id] ?? ""}
                              onChange={(event) =>
                                setAnswerDraft((previous) => ({ ...previous, [id]: event.target.value }))
                              }
                              placeholder={t(lang, "forge.discovery.answer.placeholder")}
                              aria-label={text(question.text)}
                            />
                          </li>
                        );
                      })}
                    </ol>
                  )}
                  <div className="row" style={{ gap: 12, flexWrap: "wrap", alignItems: "center" }}>
                    <Button
                      tone="primary"
                      disabled={discoveryBusy === "answers"}
                      onClick={() => void sendAnswers()}
                    >
                      {discoveryBusy === "answers"
                        ? t(lang, "runs.form.running")
                        : t(lang, "forge.discovery.send")}
                    </Button>
                    <Button
                      disabled={discoveryBusy === "skip"}
                      onClick={() => void skipClarification()}
                    >
                      {t(lang, "forge.discovery.skip")}
                    </Button>
                    <Button
                      tone="ghost"
                      disabled={discoveryBusy === "cancel"}
                      onClick={() => void cancelSession()}
                    >
                      {t(lang, "forge.discovery.cancel")}
                    </Button>
                  </div>
                </div>
              ) : null}

              {/* Step 3 — the specification, as a definition list from its own route. */}
              {phase === "spec" ? (
                <div className="stack" style={{ gap: 12 }}>
                  <div>
                    <div className="label">{t(lang, "forge.discovery.spec.title")}</div>
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

              {/* Step 4 — the run. The same controls are reachable one step earlier on purpose:
                  pressing "Запустить задачу" before confirming is exactly how the Core's 409
                  guard becomes visible instead of hidden behind a disabled button. */}
              {phase === "run" ? (
                <div className="stack" style={{ gap: 14 }}>
                  <div className={`notice ${confirmed ? "" : "warn"}`}>
                    {confirmed ? t(lang, "forge.discovery.run.ready") : t(lang, "forge.discovery.run.preconfirm")}
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

              {phase === "clarify" || phase === "spec" ? (
                <div className="stack" style={{ gap: 12 }}>
                  <div className="row between" style={{ gap: 10, flexWrap: "wrap" }}>
                    <div className="label">{t(lang, "forge.discovery.run.title")}</div>
                    <Badge tone="warn">HTTP 409</Badge>
                  </div>
                  <div className="notice warn">{t(lang, "forge.discovery.run.preconfirm")}</div>
                  {runControls}
                </div>
              ) : null}

              {discoveryFailure !== null ? (
                <FailureNotice lang={lang} failure={discoveryFailure} />
              ) : null}
              {discoveryNotice !== "" ? <div className="notice">{discoveryNotice}</div> : null}
            </div>
          </Card>

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
                  <span className="mono small muted">{text(record.run_id)}</span>
                </div>
                <Facts
                  items={[
                    [t(lang, "forge.execution.state"), text(record.state)],
                    [t(lang, "forge.execution.status"), text(record.status)],
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
                  <Badge tone={dispatched.response.success === true ? "ok" : "warn"}>
                    {text(dispatched.response.state, t(lang, "runs.unknown"))}
                  </Badge>
                ) : null
              }
            />
            {!dispatched ? (
              <div className="small muted">{t(lang, "forge.result.empty")}</div>
            ) : (
              <div className="stack" style={{ gap: 14 }}>
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
                  <Badge tone={historyPersisted === false ? "warn" : historyPersisted === true ? "ok" : "neutral"}>
                    {t(lang, "forge.result.history")}: {yesNo(lang, historyPersisted)}
                  </Badge>
                </div>

                {!basisReported ? (
                  <div className="small muted">{t(lang, "forge.result.basis.missing")}</div>
                ) : null}

                {historyPersisted === false ? (
                  <div className="notice warn">{t(lang, "runs.history.failed")}</div>
                ) : null}
              </div>
            )}
          </Card>
        </div>

        <div className="stack">
          {/* ----------------------------------------------------------------- agents */}
          <Card className="rise d1">
            <CardHead
              title={t(lang, "forge.agents.title")}
              hint={t(lang, "forge.agents.hint")}
              action={
                <Badge tone={live ? "info" : "neutral"}>
                  {backend.status === "live" ? backend.agents.length : "—"}
                </Badge>
              }
            />
            {backend.status === "loading" ? (
              <div className="small muted">{t(lang, "state.loading")}</div>
            ) : backend.status === "offline" ? (
              <div className="notice danger">{t(lang, "forge.agents.error")}</div>
            ) : backend.agents.length === 0 ? (
              <div className="small muted">{t(lang, "forge.agents.empty")}</div>
            ) : (
              <div className="stack" style={{ gap: 8 }}>
                {backend.agents.map((agent, index) => (
                  <div className="forge-agent" key={`${String(agent.name ?? "agent")}-${index}`}>
                    <div className="row between">
                      <span className="row" style={{ gap: 8 }}>
                        <StatusDot tone={agent.status === "available" ? "ok" : "warn"} />
                        <span style={{ fontWeight: 600 }}>{text(agent.name)}</span>
                      </span>
                      <Badge tone={agent.status === "available" ? "ok" : "warn"}>
                        {text(agent.status, t(lang, "runs.unknown"))}
                      </Badge>
                    </div>
                    <div className="mono small muted">
                      {text(agent.provider)} · {text(agent.model)}
                    </div>
                    {Array.isArray(agent.capabilities) && agent.capabilities.length > 0 ? (
                      <div className="row" style={{ gap: 6, flexWrap: "wrap", marginTop: 6 }}>
                        {agent.capabilities.map((capability) => (
                          <Badge key={String(capability)} tone="neutral">
                            {String(capability)}
                          </Badge>
                        ))}
                      </div>
                    ) : null}
                  </div>
                ))}
              </div>
            )}
          </Card>

          {/* -------------------------------------------------------------- artifacts */}
          <Card className="rise d2">
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
