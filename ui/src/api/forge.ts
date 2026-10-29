// Transport for the "Кузница" workspace.
//
// Only routes that exist in `app/api/server.py` today are called from here:
//
//   GET  /api/projects       -> list[ProjectInfo]              (server.py, list_projects)
//   POST /api/tasks/run      -> TaskRunResponse + X-Forge-*     (server.py, run_task)
//   GET  /api/runs/{run_id}  -> RunStore summary + classification (server.py, get_run)
//
// `/api/status`, `/api/agents` and `/api/models` are already loaded by `loadBackend()` in
// `client.ts`, so the workspace reads them from the same `BackendState` instead of asking the
// Core a second time. Nothing here invents a field: every shape below mirrors the dataclasses
// in `app/api/models.py` and `app/runtime/run_store.py`.
//
// A failed request is returned, never thrown, and it says *which* failure it was, because
// "no answer at all", "401/403" and "HTTP 500 with a detail" are three different things to a
// user and the interface must not merge them into one message.

import { taskRunBody } from "../task-run-body";
import type { Evidence } from "../evidence";

export type ProjectInfo = {
  project_id?: string;
  name?: string;
  path?: string;
  status?: string;
};

/**
 * The category vocabulary the backend actually accepts.
 *
 * Mirrors `app/orchestrator/models.py::TaskCategory`. `ApiService.run_task_detailed` maps any
 * other value to `CODE` (`TaskCategory(req.category.lower())` inside a `try/except ValueError`),
 * so this list is the routing the Core can recognise, not a suggestion.
 */
export const TASK_CATEGORIES = [
  "code",
  "analysis",
  "review",
  "coding",
  "reasoning",
  "large-context",
  "cheap/free",
  "fast/cheap",
  "other",
] as const;

export type TaskCategoryValue = (typeof TASK_CATEGORIES)[number];

/** Mirrors `app/api/models.py::TaskRunResponse.to_dict()`. */
export type TaskRunResponse = {
  run_id?: string;
  task_id?: string;
  project_id?: string;
  state?: string;
  success?: boolean;
  output?: string;
  error?: string | null;
  tokens?: number | null;
  cost?: number | null;
  duration_seconds?: number;
  provider_used?: string;
  model_used?: string;
  /**
   * `app.agent_runtime.outcome.RunOutcome`: what the run's *work* truthfully produced.
   * `state` is the user-facing word derived from it, so `COMPLETED` appears only for
   * `PROJECT_COMPLETED`. An empty string means this entry point produces no project
   * outcome — an unknown outcome, never a completed one.
   */
  outcome?: string;
  /** The questions the answer asked, when `outcome` is `CLARIFICATION_REQUIRED`. */
  clarification_questions?: string[];
  /** True exactly when the work stopped because the model asked for answers. */
  needs_clarification?: boolean;
  /**
   * Four independent save facts. They are separate booleans on purpose: one combined
   * "saved" indicator hid an empty project behind a stored run record.
   */
  run_record_saved?: boolean;
  project_created?: boolean;
  project_files_created?: boolean;
  artifact_manifest_saved?: boolean;
  /** How many files this run actually wrote (0 for a text-only answer). */
  files_created?: number;
  /**
   * The write mode the run used, stated by the backend: "" when it did not write,
   * otherwise `MOCK_DETERMINISTIC` or `REAL_PROVIDER_EXPLICIT`. A mode is never
   * inferred here from the file count.
   */
  write_mode?: string;
  /** `FREE` | `CHEAP` | `PAID`, or null when the registries do not know the pair. */
  cost_tier?: string | null;
  /** The registry's own rate/estimate, or null when no estimate exists. */
  estimated_cost?: number | null;
  /**
   * The **lifecycle** project (`proj-…`) this run created or was attached to, or
   * `null` when the run produced none. It is a different id from `project_id`
   * above, which is the workspace/Core project this server was composed for
   * (`ws-…`) and is not in the lifecycle list. Only this field may be handed to
   * «Проекты», and only after the lifecycle list confirms it exists.
   */
  lifecycle_project_id?: string | null;
  /**
   * `clarification_round`: the backend's own count for this run's clarification context.
   * 0 = no context at all, 1 = the run may still ask, 2 = the forced final round on which
   * no further «Ответить» is possible. The card reads this number instead of inferring a
   * round from the wording of an answer.
   */
  clarification_round?: number;
  /**
   * `model_choice`: the automatic routing decision for this run —
   * `app/agent_runtime/routing.py`'s `ModelChoice.to_dict()`. `provider`/`model` are the
   * pair that really served the run (a fallback or the offline engine is named as such),
   * `tier` is `FREE`/`CHEAP`/`PAID`, `complexity` is `SIMPLE`/`MEDIUM`/`COMPLEX`, and
   * `reasons` are the short lines that explain the choice. An empty object means the
   * entry point made no routing decision — never that the run was free.
   */
  model_choice?: ModelChoice;
  /**
   * The intent the answer declared, parsed by `parse_model_intent`
   * (`app/agent_runtime/intent.py`): `intent_type`, `summary`, `valid`,
   * `validation_errors`, `next_action`, `raw_text_preserved`. Empty when the entry point
   * parsed no answer. The **summary is the model's own first line**, which is what
   * «Что сделал Forge» quotes — never a sentence composed by the interface.
   */
  model_intent?: Record<string, unknown>;
  /**
   * The first two lines of the model's own answer, collapsed, bounded and screened by
   * the backend (`app/projects/what_forge_did.py`). Empty means there is nothing to
   * quote — the interface then says so instead of inventing a reason.
   */
  answer_excerpt?: string;
  /**
   * What was checked, what was assumed, what remained unknown
   * (`app/reports/evidence.py`). `verified` holds only facts the system measured (file
   * path + size + SHA-256, the verification command, the acceptance verdict, a
   * version number, a URL + HTTP status); `assumed` holds the model's own words,
   * each marked as unchecked; `unknown` names every gap. Absent when the backend
   * published no block — the screen then shows nothing rather than a false "0 · 0 · 0".
   */
  evidence?: Evidence;
};

/**
 * One automatic routing decision (`app/agent_runtime/routing.py::ModelChoice`).
 *
 * The six facts are the ones the backend guarantees; `automatic` says whether the router
 * chose the pair or the caller named it. `estimated_cost` is `0` for a free row and
 * `null` otherwise, because a run's cost is unknown before it runs — a unit price is not a
 * forecast and this type never invents one.
 */
export type ModelChoice = {
  provider?: string;
  model?: string;
  tier?: string;
  complexity?: string;
  reasons?: string[];
  estimated_cost?: number | null;
  automatic?: boolean;
};


/**
 * The dispatch facts the Core reports as response headers, not in the body.
 *
 * `X-Forge-Usage-Basis` and `X-Forge-Cost-Basis` say what kind of number `tokens`/`cost` is:
 * a mock answer, an estimate, or a measured value. Reading them is the difference between
 * showing a figure and claiming what it proves.
 */
export type DispatchFacts = {
  usage_basis: string;
  cost_basis: string;
  history_recorded: boolean | null;
  idempotent_replay: boolean | null;
};

export type DispatchResult = {
  response: TaskRunResponse;
  facts: DispatchFacts;
};

/**
 * One row of `GET /api/models` `models`, with the catalogue's own key names.
 *
 * The registry answers `provider_id` / `model_id` per row (verified against the live payload), so
 * the model list is filtered by the provider the form actually sends, not by a display name.
 * `cost_tier` and `estimated_cost` are the backend's own words/numbers for that row: the tier is
 * `FREE`/`CHEAP`/`PAID` and the cost is the registry's price per 1M input tokens, `null` when it
 * carries no rate. Neither is ever derived here.
 */
export type CatalogueModel = {
  provider_id?: string;
  model_id?: string;
  display_name?: string;
  cost_tier?: string | null;
  estimated_cost?: number | null;
};

/** One catalogue row, as the launch step needs it: the id, its tier and its unit price. */
export type CatalogueEntry = {
  model_id: string;
  /** `FREE` | `CHEAP` | `PAID`, or "" when the row carries no tier. */
  cost_tier: string;
  /** Price per 1M input tokens in USD, or null when the registry lists none. */
  estimated_cost: number | null;
};

/**
 * The catalogue row for one provider/model pair, or `null` when the pair is not listed.
 *
 * `null` is the honest answer "the registry does not describe this pair", which the launch step
 * shows as an unknown tier instead of assuming the model is free.
 */
export const catalogueEntryFor = (
  catalogue: Record<string, unknown> | null,
  provider: string,
  model: string
): CatalogueEntry | null => {
  if (!catalogue || provider === "" || model === "") return null;
  const rows = catalogue.models;
  if (!Array.isArray(rows)) return null;
  for (const row of rows) {
    if (!row || typeof row !== "object") continue;
    const record = row as CatalogueModel;
    if (String(record.provider_id ?? "") !== provider) continue;
    if (String(record.model_id ?? "") !== model) continue;
    const rawTier = String(record.cost_tier ?? "").trim().toUpperCase();
    const rawCost = record.estimated_cost;
    return {
      model_id: model,
      cost_tier: rawTier,
      estimated_cost:
        typeof rawCost === "number" && Number.isFinite(rawCost) ? rawCost : null,
    };
  }
  return null;
};

/**
 * The model ids `/api/models` lists for one provider, sorted by id.
 *
 * A registry row is membership, not availability (`availability_note` says so), which is exactly
 * what a chooser needs: these are the ids the Core knows how to name. Sorting keeps the list from
 * shuffling between two 15 s backend polls, and duplicates are dropped so one id appears once.
 */
export const catalogueModelsFor = (
  catalogue: Record<string, unknown> | null,
  provider: string
): string[] => {
  if (!catalogue || provider === "") return [];
  const rows = catalogue.models;
  if (!Array.isArray(rows)) return [];
  const ids: string[] = [];
  for (const row of rows) {
    if (!row || typeof row !== "object") continue;
    const record = row as CatalogueModel;
    const providerId = String(record.provider_id ?? "");
    const modelId = String(record.model_id ?? "");
    if (providerId !== provider || modelId === "" || ids.includes(modelId)) continue;
    ids.push(modelId);
  }
  return ids.sort((left, right) => (left < right ? -1 : left > right ? 1 : 0));
};

/**
 * The provider/model the installed configuration defaults to.
 *
 * `FORGE_DEFAULT_PROVIDER`/`FORGE_DEFAULT_MODEL` are resolved by `app/config/settings.py` inside
 * the Core, and the Core publishes no route that carries the model (`/api/status.details` has
 * `default_provider` only). The shell server answers `/ui-defaults.json` from that same loader, so
 * the form preselects what the Core would use. The read is best-effort: when the route is absent
 * the screen falls back to the first model of the provider instead of inventing a default.
 */
export type UiDefaults = { default_provider?: string; default_model?: string };

export const loadUiDefaults = async (): Promise<Result<UiDefaults>> => {
  const outcome = await readJson<UiDefaults>("/ui-defaults.json");
  if (!outcome.ok) return outcome;
  if (!outcome.value || typeof outcome.value !== "object") {
    return {
      ok: false,
      failure: { kind: "error", status: 200, path: "/ui-defaults.json", detail: "the response is not an object" },
    };
  }
  return outcome;
};

/** `RunRecordClassification.to_dict()` in `app/runtime/run_store.py`. */
export type RunClassification = {
  outcome?: string;
  reason?: string;
  detail?: string;
  evidence?: string[];
  is_terminal?: boolean;
  is_success?: boolean;
};

/**
 * One file the guarded writer touched.
 *
 * Mirrors one entry of `CodeWriteResult.files` in `app/agent_runtime/code_writer.py`:
 * `path`, `action` (`create` / `update`), and the exact numbers of added and removed lines.
 */
export type CodeWriteFile = {
  path?: string;
  action?: string;
  added?: number;
  removed?: number;
};

/**
 * The write record `app/api/service.py` persists as `project_state.code_write`.
 *
 * It is written only when a dispatch asked for `write_mode` and the task was code work,
 * so its presence is what proves the run went through the guarded writer. `status` is one
 * of `applied`, `no_changes`, `rejected`, `verification_failed`.
 */
export type CodeWriteResult = {
  status?: string;
  verification?: string;
  reason?: string;
  diff?: string;
  files?: CodeWriteFile[];
};

/** `RunRecord.summary()` plus the read-time `classification` key from `get_run_record`. */
export type RunRecord = {
  run_id?: string;
  state?: string;
  status?: string;
  task_id?: string;
  attempt_number?: number;
  completed_attempt_indexes?: number[];
  project_state?: unknown;
  accounting?: unknown;
  event_count?: number;
  last_sequence_number?: number | null;
  updated_at?: string;
  interrupted?: boolean;
  classification?: RunClassification;
};

/**
 * The `project_state.code_write` record of a run, or `null` when the run carried none.
 *
 * A missing key is not an empty write: it is the honest answer "this run recorded no
 * write", and the screen must say that instead of drawing an empty file list.
 */
export const codeWriteOf = (record: RunRecord | null): CodeWriteResult | null => {
  const projectState = record?.project_state;
  if (!projectState || typeof projectState !== "object") return null;
  const value = (projectState as Record<string, unknown>).code_write;
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  return value as CodeWriteResult;
};

export type FailureKind =
  /** The Core answered 401/403: this session has no access to that resource. */
  | "unauthorized"
  /** The request never reached the Core (server down, proxy gone, DNS/socket failure). */
  | "disconnected"
  /** The Core answered, but not with success, and said why. */
  | "error";

export type Failure = {
  kind: FailureKind;
  /** The HTTP status, or 0 when no response arrived at all. */
  status: number;
  /** The path that failed, so the message can name it. */
  path: string;
  /** The backend's own explanation when it gave one. */
  detail: string;
  /**
   * The machine code the surface published (`error_code`/`code`), when it published one.
   *
   * A refusal is not one thing: `discovery_not_confirmed` (409) and a broken route (500) both
   * arrive as a failed request, and the screen must tell the user which one happened instead of
   * merging them into a generic error.
   */
  code?: string;
  /**
   * The refusal's structured fields, when the surface published an object as its
   * `detail`. A launch refusal carries the run's state, its exit code, the redacted log
   * tail and — when the folder holds a static page — `fallback_available`/
   * `fallback_url`. The screen prints them as facts instead of flattening the refusal
   * into one sentence.
   */
  fields?: Record<string, unknown>;
};

export type Result<T> = { ok: true; value: T } | { ok: false; failure: Failure };

const detailOf = (body: unknown, fallback: string): string => {
  if (body && typeof body === "object") {
    const record = body as Record<string, unknown>;
    const detail = record.detail ?? record.error ?? record.message;
    if (typeof detail === "string" && detail.trim() !== "") return detail;
    if (detail !== undefined && detail !== null) return JSON.stringify(detail);
  }
  if (typeof body === "string" && body.trim() !== "") return body.trim();
  return fallback;
};

/**
 * The machine code of a refused request, read from the surface's own error contract.
 *
 * The discovery routes answer `{"detail": {"code": ..., "error_code": ..., "error": ...}}`, and
 * older routes answer a plain string. Both shapes are searched, and an absent code stays empty so
 * the screen falls back to the honest "HTTP <status>" instead of inventing a word.
 */
const machineCodeOf = (body: unknown): string => {
  if (!body || typeof body !== "object" || Array.isArray(body)) return "";
  const record = body as Record<string, unknown>;
  const sources: Array<Record<string, unknown>> = [record];
  if (record.detail && typeof record.detail === "object" && !Array.isArray(record.detail)) {
    sources.unshift(record.detail as Record<string, unknown>);
  }
  for (const source of sources) {
    for (const key of ["code", "error_code"]) {
      const value = source[key];
      if (typeof value === "string" && value.trim() !== "") return value.trim();
    }
  }
  return "";
};

/** The server's own sentence about a refusal, when the body carries a readable one. */
const serverMessageOf = (body: unknown): string => {
  if (!body || typeof body !== "object" || Array.isArray(body)) return "";
  const record = body as Record<string, unknown>;
  const detail = record.detail;
  const sources: Array<Record<string, unknown>> = [record];
  if (detail && typeof detail === "object" && !Array.isArray(detail)) {
    sources.unshift(detail as Record<string, unknown>);
  }
  for (const source of sources) {
    for (const key of ["error", "message"]) {
      const value = source[key];
      if (typeof value === "string" && value.trim() !== "") return value.trim();
    }
  }
  return "";
};

const failureOf = async (path: string, response: Response): Promise<Failure> => {
  let body: unknown = null;
  let raw = "";
  try {
    raw = await response.text();
    body = raw ? JSON.parse(raw) : null;
  } catch {
    body = raw;
  }
  const kind: FailureKind =
    response.status === 401 || response.status === 403 ? "unauthorized" : "error";
  const code = machineCodeOf(body);
  const detail =
    body && typeof body === "object" && !Array.isArray(body)
      ? (body as Record<string, unknown>).detail
      : undefined;
  const fields =
    detail && typeof detail === "object" && !Array.isArray(detail)
      ? (detail as Record<string, unknown>)
      : undefined;
  return {
    kind,
    status: response.status,
    path,
    detail:
      serverMessageOf(body) || detailOf(body, `${response.status} ${response.statusText}`.trim()),
    ...(code === "" ? {} : { code }),
    ...(fields === undefined ? {} : { fields }),
  };
};

const disconnected = (path: string, error: unknown): Failure => ({
  kind: "disconnected",
  status: 0,
  path,
  detail: error instanceof Error ? error.message : String(error),
});

const readJson = async <T>(path: string): Promise<Result<T>> => {
  let response: Response;
  try {
    response = await fetch(path, { headers: { accept: "application/json" } });
  } catch (error) {
    return { ok: false, failure: disconnected(path, error) };
  }
  if (!response.ok) return { ok: false, failure: await failureOf(path, response) };
  try {
    return { ok: true, value: (await response.json()) as T };
  } catch (error) {
    return {
      ok: false,
      failure: { kind: "error", status: response.status, path, detail: detailOf(null, String(error)) },
    };
  }
};

const truthOf = (header: string | null): boolean | null => {
  if (header === "true") return true;
  if (header === "false") return false;
  return null;
};

export const loadProjects = async (): Promise<Result<ProjectInfo[]>> => {
  const outcome = await readJson<ProjectInfo[]>("/api/projects");
  if (!outcome.ok) return outcome;
  if (!Array.isArray(outcome.value)) {
    return {
      ok: false,
      failure: {
        kind: "error",
        status: 200,
        path: "/api/projects",
        detail: "the response is not a list of projects",
      },
    };
  }
  return outcome;
};

/**
 * Dispatch one task through the Core.
 *
 * `provider_name` and `project_id` are sent only when the caller actually has a real value, so
 * the Core keeps its own default rather than receiving an empty string that would look like a
 * choice the user never made.
 */
export const dispatchTask = async (input: {
  description: string;
  category: TaskCategoryValue;
  providerName?: string;
  /**
   * `TaskRunRequest.model_name`: the model the owner picked. Sent only when a non-empty id was
   * chosen, so an untouched field leaves the Core's configured default in force instead of
   * sending `""`, which would look like a choice nobody made.
   */
  modelName?: string;
  projectId?: string;
  /** Sent as the `Idempotency-Key` header, which `run_task` reads (`Header(alias=...)`). */
  idempotencyKey?: string;
  /**
   * `TaskRunRequest.write_mode`: the guarded writer (`app/agent_runtime/code_writer.py`)
   * applies the answer inside the run's workspace. Sent only when it was actually asked
   * for, and the Core only honours it for the `code` category, so its default stays a
   * read-only dispatch byte-for-byte.
   */
  writeMode?: boolean;
  /**
   * The confirmed discovery session this run executes.
   *
   * `POST /api/tasks/run` reads the id at the HTTP boundary and refuses with 409
   * `discovery_not_confirmed` unless that session is CONFIRMED; when it is, the Core prepends
   * the specification to the description itself. This field carries no execution authority: no
   * command, workspace, profile, environment or timeout can be named through it.
   */
  discoverySessionId?: string;
  /**
   * The lifecycle project (`proj-…`) this run belongs to.
   *
   * Sent as `lifecycle_project_id`, which `POST /api/tasks/run` reads at the HTTP
   * boundary (it is deliberately not a field of `TaskRunRequest`) and uses for two
   * things: the tree a write-mode answer may touch is that project's own workspace,
   * and after the run finishes the run is attached to that project as its next
   * version through the existing `attach_run` path. An unknown, archived or deleted
   * id is refused with 404/409 *before* anything is dispatched, so a change request
   * can never write files that no record will name. It grants no execution
   * authority: no command, workspace, profile, environment or timeout travels here.
   */
  lifecycleProjectId?: string;
  /**
   * The owner's answers to the questions the previous run asked.
   *
   * `POST /api/tasks/run` reads them at the HTTP boundary (they are not fields of the
   * request model) and appends the canonical `Уточнения от пользователя:` block to the
   * task text, so the next attempt really is different. They carry no execution
   * authority: no command, workspace, profile, environment or timeout can be named
   * through them.
   */
  clarificationAnswers?: Array<{ question: string; answer: string }>;
  /**
   * The run whose questions were answered. The Core reads that run's own durable
   * clarification context, so a retry keeps the answers already given instead of losing
   * them; an unknown id simply starts a fresh context.
   */
  clarificationRunId?: string;
  /** Lets the interface cancel its own request; the backend keeps whatever it already did. */
  signal?: AbortSignal;
}): Promise<Result<DispatchResult>> => {
  const path = "/api/tasks/run";
  // The body is composed by `ui/src/task-run-body.ts`, the module a test executes to
  // post the same payload a screen posts. Nothing is added to it here.
  const body = taskRunBody({
    description: input.description,
    category: input.category,
    providerName: input.providerName,
    modelName: input.modelName,
    projectId: input.projectId,
    writeMode: input.writeMode,
    discoverySessionId: input.discoverySessionId,
    lifecycleProjectId: input.lifecycleProjectId,
    clarificationAnswers: input.clarificationAnswers,
    clarificationRunId: input.clarificationRunId,
  });

  const headers: Record<string, string> = {
    "content-type": "application/json",
    accept: "application/json",
  };
  if (input.idempotencyKey) headers["Idempotency-Key"] = input.idempotencyKey;

  let response: Response;
  try {
    response = await fetch(path, {
      method: "POST",
      headers,
      body: JSON.stringify(body),
      signal: input.signal,
    });
  } catch (error) {
    return { ok: false, failure: disconnected(path, error) };
  }
  if (!response.ok) return { ok: false, failure: await failureOf(path, response) };

  let payload: unknown;
  try {
    payload = await response.json();
  } catch (error) {
    return {
      ok: false,
      failure: { kind: "error", status: response.status, path, detail: detailOf(null, String(error)) },
    };
  }
  if (!payload || typeof payload !== "object") {
    return {
      ok: false,
      failure: {
        kind: "error",
        status: response.status,
        path,
        detail: "the response is not a task result object",
      },
    };
  }
  return {
    ok: true,
    value: {
      response: payload as TaskRunResponse,
      facts: {
        usage_basis: response.headers.get("X-Forge-Usage-Basis") ?? "",
        cost_basis: response.headers.get("X-Forge-Cost-Basis") ?? "",
        history_recorded: truthOf(response.headers.get("X-Forge-History-Recorded")),
        idempotent_replay: truthOf(response.headers.get("X-Forge-Idempotent-Replay")),
      },
    },
  };
};

/**
 * Ask the Core which provider/model a task *would* run on, without running it.
 *
 * It posts the same body the launch step sends with `estimate: true`, which the route
 * answers from the pure routing function and nothing else: no provider is called, no run
 * is created and no idempotency key is claimed. That is what makes the launch step's live
 * preview honest — it is the decision the real press would make, not a second opinion.
 *
 * The preview is debounced by the caller; this function performs one request and never
 * retries, so typing cannot turn into a request storm.
 */
export const estimateRoute = async (input: {
  description: string;
  category: TaskCategoryValue;
  writeMode?: boolean;
  discoverySessionId?: string;
  lifecycleProjectId?: string;
  signal?: AbortSignal;
}): Promise<Result<ModelChoice>> => {
  const path = "/api/tasks/run";
  const body = taskRunBody({
    description: input.description,
    category: input.category,
    writeMode: input.writeMode,
    discoverySessionId: input.discoverySessionId,
    lifecycleProjectId: input.lifecycleProjectId,
    estimate: true,
  });
  let response: Response;
  try {
    response = await fetch(path, {
      method: "POST",
      headers: { "content-type": "application/json", accept: "application/json" },
      body: JSON.stringify(body),
      signal: input.signal,
    });
  } catch (error) {
    return { ok: false, failure: disconnected(path, error) };
  }
  if (!response.ok) return { ok: false, failure: await failureOf(path, response) };
  let payload: unknown;
  try {
    payload = await response.json();
  } catch (error) {
    return {
      ok: false,
      failure: { kind: "error", status: response.status, path, detail: detailOf(null, String(error)) },
    };
  }
  const choice =
    payload && typeof payload === "object" ? (payload as { model_choice?: unknown }).model_choice : null;
  if (!choice || typeof choice !== "object") {
    return {
      ok: false,
      failure: { kind: "error", status: response.status, path, detail: "the estimate carries no decision" },
    };
  }
  return { ok: true, value: choice as ModelChoice };
};

/** Read one persisted run record, exactly as `GET /api/runs/{run_id}` answers it. */
export const loadRunRecord = async (runId: string): Promise<Result<RunRecord>> => {
  const path = `/api/runs/${encodeURIComponent(runId)}`;
  const outcome = await readJson<RunRecord>(path);
  if (!outcome.ok) return outcome;
  if (!outcome.value || typeof outcome.value !== "object") {
    return {
      ok: false,
      failure: { kind: "error", status: 200, path, detail: "the response is not a run record" },
    };
  }
  return outcome;
};

/**
 * Whether a record proves the run finished.
 *
 * `classification.is_terminal` is the backend's own verdict and is authoritative. The state and
 * status fallback only applies to records that carry no classification at all, and it never
 * treats a missing field as a terminal one — an unreadable record keeps the poll running.
 */
const TERMINAL_LABELS = new Set([
  "COMPLETED",
  "FAILED",
  "CANCELLED",
  "CANCELED",
  "TIMEOUT",
  "REFUSED",
  "BLOCKED",
  "SKIPPED",
]);

export const isTerminalRecord = (record: RunRecord | null): boolean => {
  if (!record) return false;
  const classified = record.classification;
  if (classified && typeof classified.is_terminal === "boolean") return classified.is_terminal;
  const labels = [String(record.state ?? ""), String(record.status ?? "")].map((item) =>
    item.trim().toUpperCase()
  );
  return labels.some((label) => label !== "" && TERMINAL_LABELS.has(label));
};

/* ---------------------------------------------------------------------------------------------
 * Discovery / clarification
 *
 * Mirrors `app/api/discovery_routes.py` and `app/discovery/models.py`. The session is the user's
 * own idea and the questions derived from it; the specification is what the Core understood. The
 * four state names travel unchanged, so this file never translates them.
 *
 * Nothing here decides anything: a run still needs `state === "CONFIRMED"`, and that verdict
 * belongs to the backend (`POST /api/tasks/run` refuses with 409 `discovery_not_confirmed`). The
 * screen shows the answer it received; it never assumes the guard passed.
 * ------------------------------------------------------------------------------------------- */

/** One clickable answer: `value` is what the Core stores, `label` is what the user reads. */
export type DiscoveryOption = {
  value?: string;
  label?: string;
  /** The option «Не знаю — выбери оптимальный вариант» picks. Exactly one per question. */
  recommended?: boolean;
};

export type DiscoveryQuestion = {
  id?: string;
  text?: string;
  field?: string;
  why?: string;
  required?: boolean;
  /** The readable side of `required`: a client needs no negation to show the badge. */
  optional?: boolean;
  /** `radio` | `checkbox` | `select` | `yes_no` | `text`, decided by the Core. */
  control?: string;
  options?: DiscoveryOption[];
  /** «Свой вариант»: a click is the fast path, not a cage. */
  allow_custom?: boolean;
  /** «Не знаю — выбери оптимальный вариант» is offered only when the Core can honour it. */
  allow_unknown?: boolean;
  /** Why this decision matters for *this kind of task* («Почему этот вопрос»). */
  relevance?: string;
};

export type DiscoveryAnswer = {
  question_id?: string;
  text?: string;
  skipped?: boolean;
  redacted?: boolean;
  /** The option values the user clicked. */
  values?: string[];
  /** The «Свой вариант» text, stored beside the clicked values. */
  custom?: string;
  /** True when the Core picked the recommended option, not the user. */
  auto_selected?: boolean;
  /** An explicit "no" to a yes/no question: answered, but the field stays unknown. */
  declined?: boolean;
};

/** One `field -> values` record of what was chosen, or of what was auto-selected. */
export type DiscoveryValueOrigin = {
  field?: string;
  values?: string[];
};

/** `ProjectSpecification.to_dict()`: an empty field means "still unknown", never a guess. */
export type DiscoverySpecification = {
  goal?: string;
  project_type?: string;
  users?: string;
  core_features?: string[];
  platform?: string;
  integrations?: string[];
  data_storage?: string;
  auth?: string;
  notifications?: string;
  constraints?: string[];
  acceptance_criteria?: string[];
  priorities?: string[];
  source_answers?: Record<string, string>;
  /** What the user picked, per field. Never merged with the auto-selected values. */
  chosen_values?: Record<string, string[]>;
  /** What «Не знаю — выбери оптимальный вариант» picked for the user, per field. */
  auto_selected_values?: Record<string, string[]>;
};

export type DiscoverySession = {
  session_id?: string;
  initial_goal?: string;
  /** `NEEDS_CLARIFICATION` | `READY_FOR_PLANNING` | `CONFIRMED` | `CANCELLED`. */
  state?: string;
  questions?: DiscoveryQuestion[];
  answers?: DiscoveryAnswer[];
  specification?: DiscoverySpecification | null;
  created_at?: string;
  updated_at?: string;
  skipped?: boolean;
  skip_reason?: string;
  /**
   * What the Core understood the idea to be (`mail`, `booking`, `shop`, …), the words
   * it shows for it, how sure the rule system is, and the words that made it sure.
   * The questions card prints «Задача понята как: почтовый клиент — 79 %».
   */
  domain?: string;
  domain_label?: string;
  domain_confidence?: number;
  domain_evidence?: string[];
};

/** The session routes answer `{session, questions}`; `questions` is the next round to ask. */
export type DiscoverySessionPayload = {
  session?: DiscoverySession;
  questions?: DiscoveryQuestion[];
  /** What the user chose so far, field by field. */
  chosen?: DiscoveryValueOrigin[];
  /** What the Core auto-selected so far, field by field. */
  auto_selected?: DiscoveryValueOrigin[];
  /** The project the confirmation produced: its short name and its folder. */
  project?: LifecycleSummary;
  /**
   * Present only when the configured project folder could not be used and another one
   * was. It names the folder that really holds the project, the configured one, the
   * plain-language reason, and every path that was probed.
   */
  project_root?: ProjectRootChoice;
};

/** One probed candidate folder: whether it can hold a project, and the OS reason. */
export type ProjectRootAttempt = {
  path?: string;
  writable?: boolean;
  reason?: string;
  /**
   * True when the OS refused for lack of permission. That is the *low-integrity* case:
   * a folder the label grant can address, as opposed to a path whose parent is a file.
   */
  denied?: boolean;
};

/** Where the project's files really went, and why it is not the configured folder. */
export type ProjectRootChoice = {
  root?: string;
  preferred?: string;
  fallback?: boolean;
  notice?: string;
  /** The one-time mandatory-label grant: tried once, on the owner's own folder. */
  label_grant_attempted?: boolean;
  /** True when the grant worked and the configured folder is used after all. */
  label_granted?: boolean;
  /** The OS's own refusal when the grant did not work. */
  label_grant_reason?: string;
  /** The exact command a person can run to fix that folder, when it was not fixed. */
  label_grant_command?: string;
  attempted?: ProjectRootAttempt[];
};

/** `GET .../specification` answers the derived spec plus the text a task would carry. */
export type DiscoverySpecificationPayload = {
  specification?: DiscoverySpecification;
  task_text?: string;
  chosen?: DiscoveryValueOrigin[];
  auto_selected?: DiscoveryValueOrigin[];
};

/**
 * POST JSON and read JSON back, with the same failure vocabulary as the rest of this file.
 *
 * `body` is omitted entirely when `undefined`, so the routes that take no body are called without
 * one instead of receiving an empty object they never declared.
 */
const postJson = async <T>(path: string, body?: unknown): Promise<Result<T>> =>
  sendJson<T>("POST", path, body);

/**
 * Send one JSON request and read JSON back, with the same failure vocabulary as the rest of
 * this file. `DELETE` exists here because deleting a project is a real route: the folder and the
 * record are moved into `.forge/projects-deleted`, never unlinked.
 */
const sendJson = async <T>(
  method: "POST" | "DELETE",
  path: string,
  body?: unknown
): Promise<Result<T>> => {
  const headers: Record<string, string> = { accept: "application/json" };
  const init: RequestInit = { method, headers };
  if (body !== undefined) {
    headers["content-type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  let response: Response;
  try {
    response = await fetch(path, init);
  } catch (error) {
    return { ok: false, failure: disconnected(path, error) };
  }
  if (!response.ok) return { ok: false, failure: await failureOf(path, response) };
  try {
    return { ok: true, value: (await response.json()) as T };
  } catch (error) {
    return {
      ok: false,
      failure: { kind: "error", status: response.status, path, detail: detailOf(null, String(error)) },
    };
  }
};

const discoveryPath = (sessionId: string, suffix: string): string =>
  `/api/discovery/sessions/${encodeURIComponent(sessionId)}${suffix}`;

/** `POST /api/discovery/sessions` — the idea becomes questions (or is ready immediately). */
export const startDiscovery = (goal: string): Promise<Result<DiscoverySessionPayload>> =>
  postJson<DiscoverySessionPayload>("/api/discovery/sessions", { goal });

/**
 * `POST .../answers` — record what the user chose, then re-derive the spec and the next round.
 *
 * Five shapes travel here, and the Core means something different by each:
 *
 * - `{values}` — the options the user clicked;
 * - `{values, custom}` — clicked options plus the «Свой вариант» text;
 * - `{unknown: true}` — «Не знаю — выбери оптимальный вариант»: the Core stores the
 *   recommended option and marks it `auto_selected`, so the review step can say so;
 * - `{skipped: true}` — «Пропустить», allowed only for an `optional` question; the Core
 *   refuses a required one with `required_question_skipped`;
 * - `{text}` — the user's own words, unchanged from before the widgets existed.
 */
export const sendDiscoveryAnswers = (
  sessionId: string,
  answers: Array<{
    question_id: string;
    text?: string;
    values?: string[];
    custom?: string;
    unknown?: boolean;
    skipped?: boolean;
  }>
): Promise<Result<DiscoverySessionPayload>> =>
  postJson<DiscoverySessionPayload>(discoveryPath(sessionId, "/answers"), { answers });

/** `POST .../skip` — end the clarification round; the session becomes READY_FOR_PLANNING. */
export const skipDiscoveryClarification = (
  sessionId: string,
  reason = ""
): Promise<Result<DiscoverySessionPayload>> =>
  postJson<DiscoverySessionPayload>(discoveryPath(sessionId, "/skip"), { reason });

/** `GET .../specification` — readable before confirmation, by design. */
export const loadDiscoverySpecification = (
  sessionId: string
): Promise<Result<DiscoverySpecificationPayload>> =>
  readJson<DiscoverySpecificationPayload>(discoveryPath(sessionId, "/specification"));

/** `POST .../confirm` — freeze the specification for execution. */
export const confirmDiscovery = (sessionId: string): Promise<Result<DiscoverySessionPayload>> =>
  postJson<DiscoverySessionPayload>(discoveryPath(sessionId, "/confirm"));

/** `POST .../cancel` — close the session without planning anything. */
export const cancelDiscovery = (sessionId: string): Promise<Result<DiscoverySessionPayload>> =>
  postJson<DiscoverySessionPayload>(discoveryPath(sessionId, "/cancel"));

/* ---------------------------------------------------------------------------------------------
 * Persistent project lifecycle
 *
 * Mirrors `app/api/project_routes.py` and `app/projects/models.py`. A project is one
 * specification, one workspace and an append-only list of versions; a version is one recorded
 * attempt with its run, its artifacts and two verdicts (`verification`, `acceptance`).
 *
 * Three rules are carried by the shapes below, not by this comment:
 *
 * * `project_path` is **repository-relative** (`.forge/projects/<id>/workspace`). No absolute
 *   path is ever published, so nothing here can print one.
 * * an artifact carries a path, a size, a SHA-256 and a `create`/`update` change -- never the
 *   bytes, so a list can be shown without carrying code or a credential that ended up in it;
 * * a refusal keeps the surface's own code (`409 illegal_project_transition`, `404
 *   unknown_project`, `400 invalid_change_request`), which `failureOf` already preserves.
 *
 * These routes are additive: `GET /api/projects` is still the workspace list, and the lifecycle
 * list lives on its own path.
 * ------------------------------------------------------------------------------------------- */

/** One file a run wrote, as `ArtifactRef.to_dict()` plus the joined repository-relative location. */
export type LifecycleArtifact = {
  path?: string;
  kind?: string;
  size?: number;
  sha256?: string;
  /** `create` | `update` */
  change?: string;
  /** `compiled` | `not_compiled` — stated, never implied. */
  verification_relevance?: string;
  /** `project_path/path`, composed by the backend so a client never joins two fields itself. */
  location?: string;
};

export type LifecycleChangeRequest = {
  text?: string;
  version?: number;
  created_at?: string;
};

/** `ProjectVersion`: one recorded attempt. A pending slot has no `run_id`. */
export type LifecycleVersion = {
  version?: number;
  created_at?: string;
  run_id?: string;
  /** `passed` | `failed` | `not_applicable` | `not_run` | `pending` */
  verification?: string;
  /** `passed` | `failed` | `not_evaluated` | `pending` */
  acceptance?: string;
  provider?: string;
  model?: string;
  file_count?: number;
  pending?: boolean;
  /** The owner's explicit "no local preview required" for this version. */
  preview_not_required?: boolean;
  change_request?: LifecycleChangeRequest | null;
  files?: LifecycleArtifact[];
  /** The change-regression verdict of this attempt (`app/projects/regression.py`). */
  rollback?: ChangeRollback | null;
  /** True only for a restore that completed and was proved by digest. */
  rolled_back?: boolean;
};

/** `Project.summary()`: the one-line projection of the lifecycle list. */
export type LifecycleSummary = {
  project_id?: string;
  name?: string;
  status?: string;
  archived?: boolean;
  current_version?: number;
  last_run_at?: string;
  file_count?: number;
  project_path?: string;
  /**
   * Whether this project's files really live under a configured **visible** project
   * root (a folder the owner can open). The list draws the `true` rows by default and
   * keeps the rest one click away, so a project the test suite left in the hidden
   * workspace cannot become the ribbon the owner complained about.
   */
  in_visible_root?: boolean;
};

/**
 * `Project.to_dict()`: the full record.
 *
 * `specification` is user text that the backend screened before storage, so an empty field
 * means "still unknown" and is never filled with a guess here either.
 */
export type LifecycleProject = {
  project_id?: string;
  name?: string;
  status?: string;
  workspace_id?: string;
  project_path?: string;
  created_at?: string;
  updated_at?: string;
  last_run_at?: string;
  current_version?: number;
  versions?: LifecycleVersion[];
  archived?: boolean;
  specification?: Record<string, unknown>;
  plan_ref?: string;
  previous_status?: string | null;
};

/** `GET /api/projects/{id}`: the record, every version (newest first) and the current artifacts. */export type LifecycleDetail = {
  project?: LifecycleProject;
  versions?: LifecycleVersion[];
  files?: LifecycleArtifact[];
  presentation?: ProjectPresentation;
  /** One of the eight stage words this module documents on the backend. */
  stage?: string;
  files_created?: number;
  entrypoint?: LifecycleEntryPoint | null;
  entrypoint_reason?: string;
  preview?: LifecyclePreview;
  /** The local process, derived at request time from live state only. */
  run?: LifecycleRun;
  /** The manual equivalent of the writer's own `py_compile`, empty when nothing to compile. */
  verification_command?: string;
  /** The project's folder: the record's own relative path and whether it is there now. */
  folder?: LifecycleFolder;
  /** What can really be opened, per target, and why not when it cannot. */
  open?: LifecycleOpen;
  /** The absolute paths of this surface, confined to the collapsed diagnostics block. */
  diagnostics?: { folder_absolute?: string; project_root?: string };
  /**
   * The evidence block for the current version (`app/reports/evidence.py`): its files
   * with their sizes and SHA-256, the verification command and the acceptance verdict
   * as `verified`; the model's words as `assumed` (a version stores none, so this is
   * usually empty — an honest absence); every gap as `unknown`.
   */
  evidence?: Evidence;
  /**
   * THE LAST CHANGE'S OWN VERDICT (`app/projects/regression.py`).
   *
   * `rolled_back` is true only when a change was applied, the project then failed to
   * start, and every written file was restored from the change's own snapshot and
   * proved by digest. Such a change is **not** in the project, so the card can never
   * present it as done — `rollback` carries the owner's sentence, the snapshot name and
   * the digest of every restored file.
   */
  rolled_back?: boolean;
  rollback?: ChangeRollback | null;
};

/** The rollback proof of a change: what came back, from which snapshot, and why. */
export type ChangeRollback = {
  outcome?: string;
  status?: string;
  rolled_back?: boolean;
  checked?: boolean;
  launchable_before?: boolean;
  launchable_after?: boolean;
  http_status?: number;
  /** The run's own reason, e.g. «процесс завершился с кодом 1». */
  reason?: string;
  /** The owner's sentence plus that reason and the log tail. */
  message?: string;
  /** `.forge/backups/<run_id>` the files were restored from. */
  snapshot?: string;
  restored?: Array<{ path?: string; sha256?: string; size?: number }>;
  removed?: string[];
  failed?: string[];
  log_tail?: string[];
  /**
   * What the page lost, in the owner's words («пропали стили: styles.css больше не
   * подключён», «не отдаётся app.js»). Empty for a page that passed; a rolled-back
   * page change always carries at least one reason here.
   */
  degradation?: string[];
  /**
   * The page facts the quality gate compared: size, title, `id`/`<link>`/`<script>`
   * counts and the status every referenced asset answered. `checked: false` means the
   * page was never read, so nothing about its quality was measured.
   */
  page?: {
    checked?: boolean;
    before?: PageFacts;
    after?: PageFacts;
    assets?: Array<{ path?: string; status?: number }>;
  } | null;
};

/** What one page was, read from its own bytes -- never from a browser. */
export type PageFacts = {
  size?: number;
  title?: string;
  ids?: number;
  stylesheets?: number;
  scripts?: number;
  assets?: string[];
};

/** One openable target (`folder`, `editor`, `terminal`) and whether it is available. */
export type LifecycleOpenTarget = {
  target?: string;
  label?: string;
  available?: boolean;
  /** The backend's own sentence; shown verbatim beside the disabled button. */
  reason?: string;
};

/**
 * The entrance into the project (`POST /api/projects/{id}/open`).
 *
 * `available` is computed from the folder **on disk**: a project with no files, or whose
 * folder is gone, has nothing to open and every target says so. The interface renders
 * that sentence instead of offering a click that would do nothing.
 */
export type LifecycleOpen = {
  targets?: Record<string, LifecycleOpenTarget>;
  folder?: string;
  folder_exists?: boolean;
  files?: number;
};

/** Where the project's files live, as the detail payload publishes it. */
export type LifecycleFolder = {
  path?: string;
  exists?: boolean;
  files?: number;
};

/** The answer of one open action: what was started, and the folder it was pointed at. */
export type LifecycleOpenResult = {
  project_id?: string;
  folder?: string;
  target?: string;
  opened?: boolean;
  program?: string;
  pid?: number;
  diagnostics?: { folder?: string; arguments?: string[] };
};

/**
 * The answer of `POST /api/projects/{id}/launch`: the window that now shows the project.
 *
 * `window` is the one that really opened (`chrome_app`, `edge_app` or
 * `default_browser`), `pid` is its process id (0 for the default browser, which reports
 * none), and `reused` says whether the running server was kept instead of restarted.
 *
 * `observed_port` is the port a real HTTP answer proved -- read from the application's
 * own startup output and its sockets, never assumed from the manifest. `notice` appears
 * only when there is something the URL alone cannot tell: a dead process fell back to
 * the project's static page, a server was already running, or the manifest's claimed
 * port is not the port that answered («Порт из манифеста 8765, фактический 8790»). Each
 * sentence is printed beside the URL, so nothing is a silent success.
 */
export type LifecycleLaunch = {
  launched?: boolean;
  url?: string;
  /** `chrome_app` | `edge_app` | `default_browser` */
  window?: string;
  pid?: number;
  reused?: boolean;
  project_id?: string;
  entry_kind?: string;
  notice?: string;
  fallback?: boolean;
  stage?: string;
  /** The port that really answered (verified with a GET), never a claim. */
  observed_port?: number;
  /**
   * Which attempt really produced the window. `sandboxed` names the browser's first,
   * sandboxed attempt; `downgraded` is `true` only when that browser was retried once
   * without its sandbox (the documented relaxation, written into the project's journal).
   */
  sandboxed?: boolean;
  downgraded?: boolean;
};

/**
 * The one entry point detected from the manifest (`app/projects/entrypoint.py`).
 *
 * `path` and `location` are workspace- and repository-relative: an absolute path is never
 * published. `run_command` is a display string this repository never executes;
 * `serve_kind === "static"` is the only kind the local preview can serve.
 */
export type LifecycleEntryPoint = {
  kind?: string;
  path?: string;
  run_command?: string;
  serve_kind?: string;
  location?: string;
  /** `Python`, `Node.js`, `HTML` -- what the project is written in. */
  language?: string;
  /** True when the entry point has a command this deployment may start. */
  runnable?: boolean;
  /** SHA-256 over kind, path and argv: the value an approval must name. */
  digest?: string;
  /** Why a recognised entry point cannot be launched (a missing interpreter). */
  reason?: string;
};

/**
 * The preview block (`GET/POST /api/projects/{id}/preview`).
 *
 * For a static page `available` is true because this process serves it. For a runnable
 * project it is true only while the project's **own process** is up and bound a local
 * port; otherwise `reason` says exactly why there is no URL, and no URL is invented.
 * The absolute workspace root, when something is running, is inside `diagnostics` only.
 */
export type LifecyclePreview = {
  available?: boolean;
  running?: boolean;
  /** `static` for the read-only file server, `process` for the project's own server. */
  source?: string;
  reason?: string;
  url?: string;
  port?: number;
  entry_path?: string;
  entry_kind?: string;
  serve_kind?: string;
  run_command?: string;
  /** The value an approval must name: a changed command invalidates it. */
  entrypoint_digest?: string;
  runnable?: boolean;
  project_folder?: string;
  files_created?: number;
  diagnostics?: Record<string, unknown>;
};

/**
 * The containment actually applied to the project's process, as measured.
 *
 * `job_object` is the Windows job object (kill-on-close, memory ceiling, process
 * ceiling). `restricted_token` says the child's integrity level is below the owner's
 * normal level; `integrity` is `restricted_token` or `unchanged` and is never set from
 * an intention. `writes_outside` is the result of a write probe run under the same
 * containment -- `blocked`, `allowed` or `unknown` -- where `allowed` means the child
 * could write Forge's own project data. `network` is always inherited: there is no
 * network isolation here. `limits` reports what the kernel accepted, with `0` meaning
 * "not in effect".
 */
export type LifecycleSandbox = {
  job_object?: boolean;
  restricted_token?: boolean;
  integrity?: string;
  writes_outside?: string;
  network?: string;
  limits?: {
    memory_mb?: number;
    max_processes?: number;
    active_process_limit?: number;
  };
  detail?: Record<string, unknown>;
};

/**
 * The local process (`GET/POST /api/projects/{id}/run`).
 *
 * `state` is the supervisor's own word -- one of `NOT_RUN`, `STARTING`, `RUNNING`,
 * `STOPPING`, `STOPPED`, `SUCCEEDED`, `FAILED`, `TIMED_OUT`, `REFUSED`, `RUN_BLOCKED`.
 * The command is a display string; the absolute interpreter path stays in `diagnostics`.
 * `network_policy` is `inherited`: this child is not network-isolated, and the payload
 * never pretends it is.
 */
export type LifecycleRun = {
  project_id?: string;
  state?: string;
  running?: boolean;
  serving?: boolean;
  reason?: string;
  next_step?: string;
  blocked_code?: string;
  last_event?: string;
  entry_kind?: string;
  entry_path?: string;
  /** `Python`, `Node.js`, `HTML`, or `""` when nothing was detected. */
  language?: string;
  command?: string;
  executable?: string;
  arguments?: string[];
  entrypoint_digest?: string;
  workspace?: string;
  pid?: number | null;
  exit_code?: number | null;
  port?: number;
  url?: string;
  started_at?: string;
  finished_at?: string;
  updated_at?: string;
  network_policy?: string;
  offline?: boolean;
  approval_required?: boolean;
  sandbox?: LifecycleSandbox;
  log?: {
    path?: string;
    relative_path?: string;
    readable?: boolean;
    truncated?: boolean;
    count?: number;
    lines?: string[];
    max_bytes?: number;
  };
  diagnostics?: Record<string, unknown>;
};

/**
 * The owner-facing card, derived by the backend in `app/projects/presentation.py`.
 *
 * It is a *presentation* of facts, never a replacement for them: `project.status`,
 * `versions[].verification` and `files` keep their own words in this payload, and
 * the raw words are repeated inside `technical`, which the card renders collapsed.
 * Rendering this instead of re-deriving the words in the client is what makes one
 * screening rule exist instead of two.
 *
 * `project_state` is one of `НЕ СОЗДАН`, `НУЖНЫ ДОРАБОТКИ`, `ГОТОВО` (or `АРХИВ`),
 * and it is derived from the facts -- so a project with no file can never say
 * `ГОТОВО`. `stage_label` is the finer word for the current stage (`ЧЕРНОВИК`,
 * `ОЖИДАЮТ УТОЧНЕНИЯ`, `ПЛАН ГОТОВ`, ...).
 */
export type ProjectPresentation = {
  project_state?: string;
  stage_label?: string;
  backend_status?: string;
  files_count?: number;
  last_action?: string;
  next_step?: string;
  verification_line?: string;
  verification_not_run?: boolean;
  notices?: string[];
  notice?: string;
  /** `[label, value]` rows: the four lines the owner reads first, in order. */
  headline?: string[][];
  /** `[label, value]` rows: workspace, repository-relative folder, files, versions, run. */
  facts?: string[][];
  save_facts?: PresentationSaveFact[];
  actions?: PresentationAction[];
  technical?: { collapsed?: boolean; fields?: PresentationField[] };
  change_not_written?: boolean;
  /**
   * True when the last change was applied and then **rolled back** because the project
   * stopped working: it stopped launching, or its page degraded. It can never sit
   * beside `ГОТОВО`: the change is not in the project, and the previous version is
   * current again.
   */
  rolled_back?: boolean;
  /**
   * The record's own sentence for that rollback, e.g. «Изменения откатаны: страница
   * приложения сломалась (пропали стили: styles.css больше не подключён)». Printed as
   * the block's headline so the card cannot say "it stopped launching" about a page
   * that still answered.
   */
  rollback_message?: string;
  /** `[label, value]` rows: the snapshot the change was restored from, the restored
   * files with their digests, what was removed, and what broke. */
  rollback?: string[][];
  /** `open` for an HTML page, `run` for an entry file, `""` when nothing is launchable. */
  launch_role?: string;
  /** The artifact's workspace-relative path (joined to `project_path` for display). */
  launch_path?: string;
};

/** One of the six save facts: `да` / `нет` / `неизвестно`, each answered separately. */
export type PresentationSaveFact = {
  key?: string;
  label?: string;
  value?: string;
  known?: boolean;
};

/** One action the card may offer. `visible: false` means "not offered at all". */
export type PresentationAction = {
  id?: string;
  label?: string;
  enabled?: boolean;
  visible?: boolean;
  primary?: boolean;
  /** Why the action is not offered; empty when it is. */
  reason?: string;
};

export type PresentationField = { key?: string; label?: string; value?: string };


/** `POST /api/projects/{id}/versions`: the staged version and the task text the run path executes. */
export type LifecycleNewVersion = {
  project?: LifecycleProject;
  version?: number;
  task_text?: string;
  /**
   * The screenshots this change names, project-relative. Empty when it names none, so the
   * field is additive and a caller that never attaches anything sees the same answer.
   */
  attachments?: string[];
  change_request?: LifecycleChangeRequest;
  /**
   * The same words are one change: true when the last version recorded for this exact
   * text is current and holds files, so `task_text` is empty and **no run may be
   * dispatched**. It is an answer («это уже внесено»), not a failure.
   */
  already_applied?: boolean;
  /**
   * True when the retry writes into the version number already recorded for these same
   * words instead of appending another empty one. `task_text` is a real task.
   */
  retargeted?: boolean;
  /** The run the same words produced last time, or "" — quoted so the card can name it. */
  previous_run_id?: string;
};

/** `GET /api/projects/{id}/history`: every version, newest first. */
export type LifecycleHistory = {
  project_id?: string;
  count?: number;
  versions?: LifecycleVersion[];
};

/** `POST /api/projects/{id}/verify`: the re-judged record, with its verdicts kept verbatim. */
export type LifecycleVerification = {
  project?: LifecycleProject;
  verified?: boolean;
  verification?: string;
  acceptance?: string;
  status?: string;
};

const projectPath = (projectId: string, suffix: string): string =>
  `/api/projects/${encodeURIComponent(projectId)}${suffix}`;

/**
 * `GET /api/projects/lifecycle` — every project, newest change first.
 *
 * The archived filter is applied here rather than by the query parameter: one request answers
 * both tabs, so switching tabs cannot show a list that disagrees with itself.
 */
export const loadProjectLifecycle = async (): Promise<Result<LifecycleSummary[]>> => {
  const outcome = await readJson<LifecycleSummary[]>("/api/projects/lifecycle");
  if (!outcome.ok) return outcome;
  if (!Array.isArray(outcome.value)) {
    return {
      ok: false,
      failure: {
        kind: "error",
        status: 200,
        path: "/api/projects/lifecycle",
        detail: "the response is not a list of projects",
      },
    };
  }
  return outcome;
};

/** `GET /api/projects/{id}` — the record, its versions and the current version's artifacts. */
export const loadProjectDetail = (projectId: string): Promise<Result<LifecycleDetail>> =>
  readJson<LifecycleDetail>(projectPath(projectId, ""));

/** `GET /api/projects/{id}/files?version=` — one version's artifact manifest. */
export const loadProjectFiles = (
  projectId: string,
  version?: number
): Promise<Result<{ project_id?: string; version?: number; count?: number; files?: LifecycleArtifact[] }>> =>
  readJson(
    projectPath(
      projectId,
      typeof version === "number" ? `/files?version=${encodeURIComponent(String(version))}` : "/files"
    )
  );

/**
 * `POST /api/projects/{id}/versions` — stage the next version; the backend composes the task text.
 *
 * `attachmentPaths` are the project-relative paths `uploadProjectAttachment` answered with.
 * The backend re-derives each one (it must still be this project's own file) and writes the
 * honest notice into the text the model reads; the staged change keeps the owner's words.
 */
export const createProjectVersion = (
  projectId: string,
  change: string,
  attachmentPaths: string[] = []
): Promise<Result<LifecycleNewVersion>> =>
  postJson<LifecycleNewVersion>(projectPath(projectId, "/versions"), {
    change,
    // THE SCREENSHOTS THIS CHANGE NAMES, as the project-relative paths the backend
    // published. It re-derives each one before it is written into the task text, so this
    // field can only ever name a file of this project. Omitted entirely when there is
    // none, so a change without a screenshot sends the body it always sent.
    ...(attachmentPaths.length > 0 ? { attachments: [...attachmentPaths] } : {}),
  });

/**
 * One screenshot kept with the project (`POST /api/projects/{id}/attachments`).
 *
 * `path` is the project-relative location (`.forge/projects/.../.forge/attachments/...`);
 * `kind` is what the **bytes** are (`png`, `jpeg`, `gif`, `webp`), decided by magic bytes
 * on the server, never by the file's name. The image itself is not model input.
 */
export type ProjectAttachment = {
  path?: string;
  bytes?: number;
  kind?: string;
  name?: string;
};

/**
 * `POST /api/projects/{id}/attachments` — keep one screenshot with the project.
 *
 * Base64 JSON rather than multipart: this surface has one body contract and no multipart
 * dependency, and a screenshot is a single small blob. Refusals are the backend's own
 * sentences (not an image / over 5 MB / outside the project), carried in the usual
 * `Failure`, so the card can print exactly why nothing was attached.
 */
export const uploadProjectAttachment = (
  projectId: string,
  filename: string,
  dataBase64: string
): Promise<Result<ProjectAttachment>> =>
  postJson<ProjectAttachment>(projectPath(projectId, "/attachments"), {
    filename,
    data: dataBase64,
  });

/** `POST /api/projects/{id}/archive` — freeze the record without deleting anything. */
export const archiveProject = (projectId: string): Promise<Result<{ project?: LifecycleProject }>> =>
  postJson<{ project?: LifecycleProject }>(projectPath(projectId, "/archive"));

/** `POST /api/projects/{id}/restore` — return it to the status it had before archiving. */
export const restoreProject = (projectId: string): Promise<Result<{ project?: LifecycleProject }>> =>
  postJson<{ project?: LifecycleProject }>(projectPath(projectId, "/restore"));

/**
 * One deletion, as the backend answers it: what moved, and into which trash folder.
 *
 * `moved` lists the folder and the record separately, because they are two moves: the
 * project directory `projects/<id>/` and the record file `projects/<id>.json`. `trash_path`
 * is repository-relative (`.forge/projects-deleted/<id>-<timestamp>`); no absolute path of
 * the host appears here.
 */
export type LifecycleDeletion = {
  project_id?: string;
  deleted?: boolean;
  trash_name?: string;
  trash_path?: string;
  moved?: Array<{ from?: string; to?: string }>;
  timestamp?: string;
  project?: LifecycleProject;
};

/** `DELETE /api/projects/{id}` — move one project into `projects-deleted` (never an erasure). */
export const deleteProject = (projectId: string): Promise<Result<LifecycleDeletion>> =>
  sendJson<LifecycleDeletion>("DELETE", projectPath(projectId, ""));

/** One trash entry, as `GET /api/projects/deleted` reports it. */
export type LifecycleDeletedProject = {
  trash_name?: string;
  project_id?: string;
  trash_path?: string;
  record_present?: boolean;
  folder_present?: boolean;
  /** When the deletion happened (ISO-like, no timezone claimed), or "" when unknown. */
  deleted_at?: string;
  /** The record's own project name, or "" when the record could not be read. */
  name?: string;
  /** The status the project had when it was deleted, or "" when unreadable. */
  status?: string;
  /** The current version's file count at deletion, or null when unreadable. */
  file_count?: number | null;
};

/** `GET /api/projects/deleted` — what the trash holds right now. */
export const loadDeletedProjects = (): Promise<Result<LifecycleDeletedProject[]>> =>
  readJson<LifecycleDeletedProject[]>("/api/projects/deleted");

/**
 * `POST /api/projects/delete-empty` — the bulk cleanup.
 *
 * The rule is the backend's own: the current version holds no file and the status is
 * `NEEDS_CHANGES`, `FAILED` or `ARCHIVED`. `skipped` names every project the rule excluded
 * (with its reason) so the interface can print what was *not* removed.
 */
export const deleteEmptyProjects = (): Promise<
  Result<{
    deleted?: LifecycleDeletion[];
    deleted_count?: number;
    skipped?: Array<{ project_id?: string; status?: string; reason?: string }>;
    skipped_count?: number;
    rule?: string;
  }>
> => postJson("/api/projects/delete-empty");

/** `POST /api/projects/{id}/restore-deleted` — move the newest trashed copy back. */
export const restoreDeletedProject = (
  projectId: string
): Promise<Result<{ project_id?: string; restored?: boolean; trash_name?: string; project?: LifecycleProject }>> =>
  postJson(projectPath(projectId, "/restore-deleted"));

/** `GET /api/projects/{id}/history` — every version, newest first. */
export const loadProjectHistory = (projectId: string): Promise<Result<LifecycleHistory>> =>
  readJson<LifecycleHistory>(projectPath(projectId, "/history"));

/** `POST /api/projects/{id}/verify` — re-check the recorded artifacts; no provider is called. */
export const verifyProject = (projectId: string): Promise<Result<LifecycleVerification>> =>
  postJson<LifecycleVerification>(projectPath(projectId, "/verify"));

/**
 * `POST /api/projects/{id}/open` — really open the project on this machine.
 *
 * `target` is one of `folder`, `editor`, `terminal`. The folder is resolved by the
 * backend from the project record; the request never names a path. A target that cannot
 * be performed answers `409` with a plain sentence (`project_open_refused`), which the
 * card prints verbatim.
 */
export const openProject = (
  projectId: string,
  target: "folder" | "editor" | "terminal"
): Promise<Result<LifecycleOpenResult>> =>
  postJson<LifecycleOpenResult>(projectPath(projectId, "/open"), { target });

/**
 * `POST /api/projects/{id}/launch` — run the project and show it in its own window.
 *
 * One request, one result: the backend resolves the entry point from the manifest,
 * starts the project (or reuses the server that is already up) and opens the URL in a
 * standalone window with no address bar. `confirm` plus `entrypoint_digest` is the same
 * approval `POST /run` needs; a static page needs neither, so the digest is omitted when
 * the card has none. A refusal is a `409` with one plain sentence
 * (`project_launch_refused`), which the card prints verbatim. `staticFallback` is the
 * owner's explicit choice of the project's saved page, offered by that refusal as
 * `fallback_available`; without it a process that never became a server opens nothing.
 */
export const launchProject = (
  projectId: string,
  digest?: string,
  staticFallback = false
): Promise<Result<LifecycleLaunch>> =>
  postJson<LifecycleLaunch>(projectPath(projectId, "/launch"), {
    confirm: true,
    ...(digest ? { entrypoint_digest: digest } : {}),
    ...(staticFallback ? { fallback: true } : {}),
  });

/**
 * `POST /api/projects/{id}/move-to-visible-folder` — deliver an existing project.
 *
 * The record keeps its versions and run links; only the files move into the configured
 * project root. `moved` lists every file with the SHA-256 that proves the copy.
 */
export const moveProjectToVisibleFolder = (
  projectId: string
): Promise<
  Result<{
    project_id?: string;
    already_visible?: boolean;
    folder?: string;
    moved?: Array<{ path?: string; size?: number; sha256?: string }>;
    moved_count?: number;
    project?: LifecycleProject;
    diagnostics?: { folder_absolute?: string; project_root?: string };
  }>
> => postJson(projectPath(projectId, "/move-to-visible-folder"));

/** `GET /api/settings` — the owner's project root, its default, and whether it exists. */
export type ForgeSettings = {
  ok?: boolean;
  project_root?: string;
  default_project_root?: string;
  project_root_exists?: boolean;
  /** The folder projects are *really* delivered into (the fallback when it had to be). */
  project_root_effective?: string;
  /** The remembered fallback, empty when the configured root works. */
  project_root_fallback?: string;
  project_root_used_fallback?: boolean;
  /** A plain-language sentence: which folder is used, and why not the configured one. */
  project_root_notice?: string;
  /**
   * THE ONE-TIME LABEL GRANT. The interpreters Forge runs on are low integrity, so a
   * medium-integrity folder (the owner's Desktop) refuses writes until its mandatory label
   * is lowered once. `project_root_label_granted` says that happened and the configured
   * folder is used; `project_root_label_grant_command` is the exact command to run when it
   * could not be done from inside the application.
   */
  project_root_label_grant_attempted?: boolean;
  project_root_label_granted?: boolean;
  project_root_label_grant_reason?: string;
  project_root_label_grant_command?: string;
  project_root_attempted?: ProjectRootAttempt[];
  project_roots_known?: string[];
  /**
   * «Как открываются окна»: whether a browser whose own sandbox cannot start may be
   * retried **once** without it. Default `true` — the retry is what turns a grey window
   * into the owner's page — and every downgrade is written into the project's launch log.
   */
  window_downgrade_allowed?: boolean;
  /**
   * What this machine has measured about its browsers: a browser whose own sandbox cannot
   * start here is remembered in `.forge/settings.json` (`window_plan_memory`), and the next
   * launch goes straight to its no-sandbox plan — the blank white window of the doomed
   * sandboxed attempt is not shown at all. «Забыть» clears the whole list.
   */
  window_plan_memory?: WindowPlanMemoryEntry[];
  /**
   * «Вопросы по смыслу задачи (через модель)»: whether the optional model path may
   * write up to three clarification questions. **Off by default** — it uses the owner's
   * tokens, and the deterministic domain questions are asked either way.
   */
  questions_from_model?: boolean;
  settings_path?: string;
  schema?: string;
  created?: boolean;
};

/** One browser this machine remembers as unable to start its own sandbox. */
export type WindowPlanMemoryEntry = {
  /** The browser's file name, e.g. `chrome.exe`. */
  program?: string;
  /** `true` — the launch starts it without the sandbox, with no blank attempt first. */
  no_sandbox_required?: boolean;
  /** How many sandboxed failures in a row were measured (`3` is the counter's threshold). */
  sandbox_failures?: number;
  /** When it was measured: ISO-8601 local time with its own offset (`2026-10-29T01:05:20+02:00`). */
  failed_at?: string;
  /** The exit code that proved it: `0x80000003` is the documented abort. */
  reason?: string;
};

export const loadSettings = (): Promise<Result<ForgeSettings>> =>
  readJson<ForgeSettings>("/api/settings");

/**
 * `POST /api/settings` — set the project root, restore the desktop default, decide
 * whether a project window may be retried without the browser's sandbox, or forget what
 * this machine remembers about its browsers.
 *
 * The value is stored in the application's own settings file (`.forge/settings.json`),
 * not in `.env`, and the folder is created here so a typo fails where it was typed.
 */
export const saveProjectRoot = (
  body:
    | { project_root: string }
    | { use_desktop: true }
    | { window_downgrade_allowed: boolean }
    | { forget_window_plan_memory: true }
    | { questions_from_model: boolean }
): Promise<Result<ForgeSettings>> => postJson<ForgeSettings>("/api/settings", body);

// --------------------------------------------------------------- providers and keys
//
// The section the owner could not find: where a provider with an API key is added.
// The key travels **into** these calls and never comes back: every payload below
// carries `has_key` (a boolean) and `key_source` (a fixed word), and the backend stores
// the value in the Windows Credential Manager -- not in `.env`, not in the settings file.

/** One preset from the backend's own catalogue (label, registry id, default URL/model). */
export type ProviderPreset = {
  preset?: string;
  label?: string;
  provider_id?: string;
  secret_name?: string;
  base_url?: string;
  model?: string;
};

/** One configured provider, as the interface may see it: facts, never the key. */
export type ProviderKeyEntry = {
  provider_id?: string;
  label?: string;
  preset?: string;
  base_url?: string;
  model?: string;
  /** The *name* of the secret variable (`DEEPSEEK_API_KEY`), never its value. */
  secret_name?: string;
  has_key?: boolean;
  /** `environment` (process env or `.env`) or `vault` (the credential vault) or empty. */
  key_source?: string;
  /** Whether this build's provider registry knows the id at all. */
  registered?: boolean;
  state?: "ready" | "no_key" | "disabled" | "unsupported";
  state_reason?: string;
  added_at?: string;
};

export type ProviderKeySection = {
  ok?: boolean;
  providers?: ProviderKeyEntry[];
  presets?: ProviderPreset[];
  settings_path?: string;
  credential_store?: string;
  counts?: { providers?: number; ready?: number; with_key?: number };
  note?: string;
};

export type ProviderKeyResult = {
  ok?: boolean;
  saved?: boolean;
  provider?: ProviderKeyEntry;
  warning?: string;
  section?: ProviderKeySection;
};

export type ProviderKeyDeletion = {
  ok?: boolean;
  deleted?: boolean;
  provider_id?: string;
  removed_from_vault?: boolean;
  /** A plain sentence when the key also lives in the environment or `.env`. */
  notice?: string;
  section?: ProviderKeySection;
};

/** `GET /api/settings/providers` — the configured providers, the presets, the storage facts. */
export const loadProviderKeys = (): Promise<Result<ProviderKeySection>> =>
  readJson<ProviderKeySection>("/api/settings/providers");

/**
 * `POST /api/settings/providers` — add one provider.
 *
 * `api_key` is the only secret in this file, and it only ever travels *out*: the body is
 * sent once, the response describes the provider by `has_key`. A refusal is a `400` with
 * a plain sentence (an empty key, a bad URL, an unknown provider) that never echoes the
 * value that was refused.
 */
export const addProviderKey = (body: {
  preset: string;
  provider_id?: string;
  base_url?: string;
  model?: string;
  api_key: string;
}): Promise<Result<ProviderKeyResult>> =>
  postJson<ProviderKeyResult>("/api/settings/providers", body);

/**
 * `DELETE /api/settings/providers/{id}` — remove that provider's key and its entry.
 *
 * The key is removed from the Windows Credential Manager; the stored non-secret entry is
 * dropped too, so nothing on the screen claims a key that is not there. When the same
 * secret is also set in the environment or `.env`, the answer says so (`notice`) instead
 * of pretending the provider is gone.
 */
export const deleteProviderKey = (providerId: string): Promise<Result<ProviderKeyDeletion>> =>
  sendJson<ProviderKeyDeletion>(
    "DELETE",
    `/api/settings/providers/${encodeURIComponent(providerId)}`
  );

/** `GET /api/projects/{id}/entrypoint` — the one entry point the manifest supports. */
export const loadProjectEntryPoint = (
  projectId: string
): Promise<Result<{ project_id?: string; entrypoint?: LifecycleEntryPoint | null; reason?: string }>> =>
  readJson(projectPath(projectId, "/entrypoint"));

/** `GET /api/projects/{id}/preview` — the preview's runtime status. */
export const loadProjectPreview = (
  projectId: string
): Promise<Result<LifecyclePreview & { project_id?: string }>> =>
  readJson(projectPath(projectId, "/preview"));

/**
 * `POST /api/projects/{id}/preview` — start or stop the read-only static preview.
 *
 * `preview_not_required` records the owner's explicit statement on the current version;
 * it is not a substitute for a preview the project could have served, and it starts
 * nothing.
 */
export const changeProjectPreview = (
  projectId: string,
  body: { action: "start" | "stop" | "status"; preview_not_required?: boolean }
): Promise<Result<LifecyclePreview & { project_id?: string; action?: string; stage?: string; stopped?: boolean }>> =>
  postJson(projectPath(projectId, "/preview"), body);

/** `GET /api/projects/{id}/run` — the project's local process, from live state only. */
export const loadProjectRun = (
  projectId: string
): Promise<Result<LifecycleRun & { project_id?: string; stage?: string }>> =>
  readJson(projectPath(projectId, "/run"));

/**
 * `POST /api/projects/{id}/run` — start the declared command.
 *
 * The body can only say *yes, start that exact command*: `confirm` must be true and
 * `entrypointDigest` must be the digest that was shown. The command itself is never sent.
 */
export const startProjectRun = (
  projectId: string,
  entrypointDigest: string
): Promise<Result<LifecycleRun & { project_id?: string; stage?: string }>> =>
  postJson(projectPath(projectId, "/run"), { confirm: true, entrypoint_digest: entrypointDigest });

/** `POST /api/projects/{id}/run/stop` — terminate the process tree. */
export const stopProjectRun = (
  projectId: string
): Promise<Result<LifecycleRun & { project_id?: string; stage?: string }>> =>
  postJson(projectPath(projectId, "/run/stop"), {});

/** `POST /api/projects/{id}/run/restart` — stop, then start again under a fresh approval. */
export const restartProjectRun = (
  projectId: string,
  entrypointDigest: string
): Promise<Result<LifecycleRun & { project_id?: string; stage?: string }>> =>
  postJson(projectPath(projectId, "/run/restart"), {
    confirm: true,
    entrypoint_digest: entrypointDigest,
  });

/** `GET /api/projects/{id}/run/log` — the redacted, bounded log. */
export const loadProjectRunLog = (
  projectId: string
): Promise<Result<{ project_id?: string; state?: string; path?: string; lines?: string[]; truncated?: boolean; count?: number }>> =>
  readJson(projectPath(projectId, "/run/log"));

/**
 * How one verdict word reads, without inventing a meaning the backend did not publish.
 *
 * A value outside the five the backend documents is shown verbatim, because an unknown word is
 * evidence that the client and the surface disagree -- and hiding it would hide that.
 */
export const verificationLabel = (lang: "ru" | "en", value: unknown): string => {
  const word = String(value ?? "");
  const ru: Record<string, string> = {
    passed: "пройдена",
    failed: "не пройдена",
    not_applicable: "нечего проверять (нет .py)",
    not_run: "не выполнялась",
    pending: "ожидает запуска",
  };
  const en: Record<string, string> = {
    passed: "passed",
    failed: "failed",
    not_applicable: "nothing to check (no .py)",
    not_run: "not run",
    pending: "waiting for a run",
  };
  const table = lang === "ru" ? ru : en;
  return table[word] ?? (word === "" ? (lang === "ru" ? "неизвестно" : "unknown") : word);
};

/** The acceptance verdict, in the same conservative style. */
export const acceptanceLabel = (lang: "ru" | "en", value: unknown): string => {
  const word = String(value ?? "");
  const ru: Record<string, string> = {
    passed: "принят",
    failed: "не принят",
    not_evaluated: "не оценивался",
    pending: "ожидает запуска",
  };
  const en: Record<string, string> = {
    passed: "accepted",
    failed: "rejected",
    not_evaluated: "not evaluated",
    pending: "waiting for a run",
  };
  const table = lang === "ru" ? ru : en;
  return table[word] ?? (word === "" ? (lang === "ru" ? "неизвестно" : "unknown") : word);
};

/**
 * The provider the installed runtime uses for its offline, deterministic answer.
 *
 * It comes from `app/config/settings.py::OFFLINE_PROVIDER_NAME`. A run on it never calls
 * a provider, and it writes files only in `MOCK_DETERMINISTIC` mode -- the deterministic
 * scaffold Forge itself produces, used by tests and the smoke -- not model output.
 */
export const OFFLINE_PROVIDER = "mock";
