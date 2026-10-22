// The one place the `POST /api/tasks/run` request body is composed.
//
// Why this is a module of its own: the body is the *contract* between a screen and
// the run route, and three screens submit through it (Кузница, Проекты and the
// clarification retry). It used to be assembled inline inside `dispatchTask`, so the
// only way to check what a screen really sends was to read the screen and hope. Here
// the payload is a pure function of the caller's choices -- no fetch, no React, no
// imports -- which lets a test execute the exact body a screen would send and post it
// to the real route (the repository already does that for
// `ui/src/project-selection.js` and `ui/src/forge-result.ts`).
//
// It deliberately contains no rule of its own: a field is sent only when the caller
// supplied a real value, so an untouched form leaves the Core's configured default in
// force instead of sending `""`/`false`, which would look like a choice nobody made.

/** What a screen knows when it submits a task. Every field is optional except the text. */
export type TaskRunBodyInput = {
  description: string;
  category: string;
  providerName?: string;
  modelName?: string;
  /**
   * `TaskRunRequest.project_id`: the workspace/Core project this server was composed
   * for. It is **not** a lifecycle (`proj-…`) id and must not be used as one.
   */
  projectId?: string;
  writeMode?: boolean;
  /** The confirmed discovery session whose specification this run executes. */
  discoverySessionId?: string;
  /**
   * The lifecycle project this run belongs to (`proj-…`), read at the HTTP boundary by
   * `POST /api/tasks/run` and deliberately not a field of `TaskRunRequest`. It is the
   * association that makes a change request fill the version it staged.
   */
  lifecycleProjectId?: string;
  clarificationAnswers?: Array<{ question: string; answer: string }>;
  clarificationRunId?: string;
};

/**
 * Build the JSON body for `POST /api/tasks/run`.
 *
 * The key names are the route's own: `provider_name`, `model_name`, `write_mode`,
 * `discovery_session_id`, `lifecycle_project_id`, `clarification_answers`,
 * `clarification_run_id`. A caller that names no provider/model gets the Core's
 * configured default rather than an empty string.
 */
export const taskRunBody = (input: TaskRunBodyInput): Record<string, unknown> => {
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
  const lifecycleProjectId = (input.lifecycleProjectId ?? "").trim();
  if (lifecycleProjectId !== "") body.lifecycle_project_id = lifecycleProjectId;
  const clarificationRunId = (input.clarificationRunId ?? "").trim();
  if (clarificationRunId !== "") body.clarification_run_id = clarificationRunId;
  const clarificationAnswers = (input.clarificationAnswers ?? [])
    .map((item) => ({
      question: String(item?.question ?? "").trim(),
      answer: String(item?.answer ?? "").trim(),
    }))
    .filter((item) => item.question !== "" && item.answer !== "");
  if (clarificationAnswers.length > 0) {
    body.clarification_answers = clarificationAnswers;
  }
  return body;
};
