// Which project the result card's three project actions may open.
//
// The run response publishes two different identifiers and they must never be
// confused:
//
//   * `project_id`          -- the workspace/Core project this server was composed
//                              for (`ws-…`); it is not in the lifecycle list.
//   * `lifecycle_project_id` -- the `proj-…` record the run created or was attached
//                              to, or `null` when the run produced none.
//
// Only the second one names a project «Проекты» can open, and only when the
// lifecycle list confirms it still exists. This module is deliberately plain ESM
// JavaScript (with `project-selection.d.ts` for the types) so the regression test in
// `tests/test_run_project_id.py` can execute *this* function with Node instead of
// re-implementing it. It has no React, no DOM and no network.

/**
 * @typedef {"" | "no_project" | "unknown_project"} RunProjectReason
 * @typedef {{ projectId: string, reason: RunProjectReason }} RunProjectTarget
 */

/**
 * Resolve the one project id a result action may open.
 *
 * @param {unknown} rawId the run response's `lifecycle_project_id`
 * @param {readonly string[]} lifecycleIds the ids `GET /api/projects/lifecycle` lists
 * @returns {RunProjectTarget}
 */
export function selectRunProject(rawId, lifecycleIds) {
  const id = typeof rawId === "string" ? rawId.trim() : "";
  if (id === "") return { projectId: "", reason: "no_project" };
  const known = Array.isArray(lifecycleIds) ? lifecycleIds.map(String) : [];
  // Never fall back to another project: an id the list does not name is a disabled
  // button with a reason, not a click that lands somewhere else.
  if (!known.includes(id)) return { projectId: "", reason: "unknown_project" };
  return { projectId: id, reason: "" };
}
