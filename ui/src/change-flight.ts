/**
 * THE ONE CHANGE ACTION: one press, one dispatch, one honest end.
 *
 * Measured defect (the owner's screenshot): he typed a change request, pressed «Внести
 * изменения в проект», and — with nothing on screen saying anything had started — pressed
 * it again. TWO versions (v4 and v5) were created, both with zero files, and the second
 * press was a duplicate dispatch of the first, not a second change.
 *
 * The screen already showed a strip and a «Отправляю…» label, but the *button* was
 * re-enabled the moment the dispatch answered, i.e. while the run was still in flight. The
 * rule this module states, in one place and as plain functions, is:
 *
 * * a press **acquires** the action synchronously, so a second press in the same tick (a
 *   double click delivers two `onClick`s before React re-renders) is dropped, not queued;
 * * the action stays **in flight** from the click until the run reaches a terminal state
 *   or a refusal comes back — a run id alone is not an end;
 * * a refusal is reported in plain language with the *safe* rule id, and the raw backend
 *   sentence is kept for «Технические детали».
 *
 * Nothing here imports React or the API layer, so the rules can be executed (and tested)
 * on their own from Node, which is what `tests/test_flow_invariants.py` does.
 */

/** What the button knows about the current change: enough to keep it off. */
export type ChangeFlightFacts = {
  /** True from the click until the dispatch answers (or refuses). */
  starting: boolean;
  /** The run the dispatch named; empty until the dispatch answers. */
  runId: string;
  /** True once the run is terminal, or a refusal came back, or the outcome is known. */
  finished: boolean;
};

/**
 * Whether the change action is still in flight.
 *
 * `finished` wins: once the run really ended (or a refusal came back) the button must come
 * back to life, or the owner has to reload the screen to change anything again.
 */
export const changeInFlight = (facts: ChangeFlightFacts): boolean => {
  if (facts.finished) return false;
  return facts.starting === true || String(facts.runId ?? "") !== "";
};

/**
 * The synchronous guard behind the button.
 *
 * `tryAcquire` answers `false` while a dispatch is already running, so the second
 * `onClick` of a double click does nothing at all: no second `POST /api/projects/{id}/versions`,
 * therefore no second version. `release` is called when the dispatch answers; the button
 * stays disabled afterwards through {@link changeInFlight}, which follows the run itself.
 */
export type ChangeGuard = {
  tryAcquire: () => boolean;
  release: () => void;
  held: () => boolean;
};

export const createChangeGuard = (): ChangeGuard => {
  let held = false;
  return {
    tryAcquire: () => {
      if (held) return false;
      held = true;
      return true;
    },
    release: () => {
      held = false;
    },
    held: () => held,
  };
};

/** Which refusal the backend's own sentence describes, and its safe rule id. */
export type ChangeRefusal = {
  /** `context_refused` when the context check refused the text; `unknown` otherwise. */
  kind: "context_refused" | "unknown";
  /** The rule id the backend published (`high_entropy`, `provider_key_prefix`, …) or "". */
  rule: string;
};

/**
 * The rule id inside a refusal sentence, restricted to the ids the scanner publishes.
 *
 * Only `[a-z_]` of a bounded length is read, so a rule id can never become a channel for
 * the refused text itself: the diagnostic beside it is content-free by design.
 */
const SAFE_RULE = /rule=([a-z_]{1,40})/;

/**
 * Classify a refusal so the strip can print words the owner understands.
 *
 * The backend's sentence («Critical Tier 1 context item … Reason:
 * security_forbidden_content:secret. Текст содержит значение, похожее на секрет
 * (rule=high_entropy, …)») is correct but unreadable. The strip prints
 * «Текст изменения отклонён проверкой контекста» plus the rule id, and keeps the raw line
 * folded under «Технические детали».
 */
export const classifyChangeRefusal = (error: unknown): ChangeRefusal => {
  const text = String(error ?? "");
  const match = SAFE_RULE.exec(text);
  const rule = match ? match[1] : "";
  if (text.includes("security_forbidden_content") || text.includes("Critical Tier 1")) {
    return { kind: "context_refused", rule };
  }
  return { kind: "unknown", rule };
};

/** The raw sentence, safe to show, or "" when the run ended without one. */
export const refusalDetail = (error: unknown): string => String(error ?? "").trim();
