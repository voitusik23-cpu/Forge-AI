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
 */
export type CatalogueModel = { provider_id?: string; model_id?: string; display_name?: string };

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
  return {
    kind,
    status: response.status,
    path,
    detail: detailOf(body, `${response.status} ${response.statusText}`.trim()),
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
