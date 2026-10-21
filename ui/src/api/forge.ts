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
  return {
    kind,
    status: response.status,
    path,
    detail:
      serverMessageOf(body) || detailOf(body, `${response.status} ${response.statusText}`.trim()),
    ...(code === "" ? {} : { code }),
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
  /** Lets the interface cancel its own request; the backend keeps whatever it already did. */
  signal?: AbortSignal;
}): Promise<Result<DispatchResult>> => {
  const path = "/api/tasks/run";
  const body: Record<string, unknown> = {
    description: input.description,
    category: input.category,
  };
  if (input.providerName) body.provider_name = input.providerName;
  const modelName = (input.modelName ?? "").trim();
  if (modelName !== "") body.model_name = modelName;
  if (input.projectId) body.project_id = input.projectId;
  if (input.writeMode === true) body.write_mode = true;
  const discoverySessionId = (input.discoverySessionId ?? "").trim();
  if (discoverySessionId !== "") body.discovery_session_id = discoverySessionId;

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
};

/** The session routes answer `{session, questions}`; `questions` is the next round to ask. */
export type DiscoverySessionPayload = {
  session?: DiscoverySession;
  questions?: DiscoveryQuestion[];
  /** What the user chose so far, field by field. */
  chosen?: DiscoveryValueOrigin[];
  /** What the Core auto-selected so far, field by field. */
  auto_selected?: DiscoveryValueOrigin[];
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
};

/** `Project.summary()`: the one-line projection of the lifecycle list. */export type LifecycleSummary = {
  project_id?: string;
  name?: string;
  status?: string;
  archived?: boolean;
  current_version?: number;
  last_run_at?: string;
  file_count?: number;
  project_path?: string;
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

/** `GET /api/projects/{id}`: the record, every version (newest first) and the current artifacts. */
export type LifecycleDetail = {
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
  change_request?: LifecycleChangeRequest;
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

/** `POST /api/projects/{id}/versions` — stage the next version; the backend composes the task text. */
export const createProjectVersion = (
  projectId: string,
  change: string
): Promise<Result<LifecycleNewVersion>> =>
  postJson<LifecycleNewVersion>(projectPath(projectId, "/versions"), { change });

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
