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

/** `RunRecordClassification.to_dict()` in `app/runtime/run_store.py`. */
export type RunClassification = {
  outcome?: string;
  reason?: string;
  detail?: string;
  evidence?: string[];
  is_terminal?: boolean;
  is_success?: boolean;
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
  projectId?: string;
  /** Sent as the `Idempotency-Key` header, which `run_task` reads (`Header(alias=...)`). */
  idempotencyKey?: string;
}): Promise<Result<DispatchResult>> => {
  const path = "/api/tasks/run";
  const body: Record<string, unknown> = {
    description: input.description,
    category: input.category,
  };
  if (input.providerName) body.provider_name = input.providerName;
  if (input.projectId) body.project_id = input.projectId;

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
