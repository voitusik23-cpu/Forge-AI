export type RunProjectReason = "" | "no_project" | "unknown_project";

export type RunProjectTarget = {
  /** The exact id an action may open, or "" when no action may open anything. */
  projectId: string;
  /** "" when `projectId` is usable; otherwise why the actions are disabled. */
  reason: RunProjectReason;
};

export function selectRunProject(
  rawId: unknown,
  lifecycleIds: readonly string[]
): RunProjectTarget;
