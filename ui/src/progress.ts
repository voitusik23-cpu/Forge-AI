/**
 * THE LIVE PROGRESS MODEL — the four Кузница steps, one clock, one ribbon.
 *
 * The owner's measured complaint was not «I need a percentage»: it was «I cannot tell whether
 * it works or hangs». So this module answers exactly three questions, from facts that exist:
 *
 * 1. **Which step is which?** `stepStates` marks the four steps `done`, `current`, `pending` or
 *    `error`. Steps 1–3 are `done` only when the discovery session itself is `CONFIRMED` (the
 *    Core's own record) or when the owner has already moved past them; step 4 is `current`
 *    while a run is in flight and `done`/`error` only when the run record is terminal. Nothing
 *    here is a timer or a guess: an unconfirmed step is never painted green.
 * 2. **How long has it been going?** `aliveState` turns the press that started the run and the
 *    run record's own timestamp into a clock. There is **no** total and **no** ETA, because the
 *    backend publishes neither: the display is the elapsed value and nothing else.
 * 3. **Is anything moving?** The ribbon is `true` only while the run is really in flight, and it
 *    stops the moment the record is terminal — so a still ribbon always means "finished".
 *
 * `RunRecord` (GET /api/runs/{run_id}) publishes no start timestamp: its only timestamp,
 * `updated_at`, is written by the final snapshot. The start is therefore the press that created
 * the run — a real local fact, not a fabricated one — and the record's own timestamp bounds the
 * end, so a run observed from a fresh page still reports the record's own end.
 *
 * The functions are pure and carry no React, so `tests/test_progress_state.py` executes this
 * exact file with Node (type stripping) instead of re-implementing the rules in Python.
 */

/** The four steps of the Кузница flow, in order. */
export const STEP_IDS = ["idea", "clarify", "spec", "run"] as const;

/** One step id. The strings are the same ones `Forge.tsx` uses for its `phase` state. */
export type StepId = (typeof STEP_IDS)[number];

/** What one step may be. `error` exists so a failed run is red, never a green tick. */
export type StepState = "done" | "current" | "pending" | "error";

/** Everything the rail is allowed to read. */
export type StepFacts = {
  /** The step the owner is on right now. */
  phase: StepId;
  /** The discovery session says `CONFIRMED`. Steps 1–3 are complete only from this fact. */
  confirmed: boolean;
  /** A run is really in flight: the dispatch has not returned, or the record is not terminal. */
  inFlight: boolean;
  /** The run record is terminal. */
  terminal: boolean;
  /** The terminal record's own `classification.is_success`. */
  success: boolean;
};

/**
 * Mark every step from the facts above.
 *
 * The run step is decided *after* steps 1–3, because it can go backwards: a retry dispatched
 * from a finished run resets the record, and the step must read `current` again instead of
 * keeping the old verdict.
 */
export const stepStates = (facts: StepFacts): Record<StepId, StepState> => {
  const currentIndex = STEP_IDS.indexOf(facts.phase);
  const states: Record<StepId, StepState> = {
    idea: "pending",
    clarify: "pending",
    spec: "pending",
    run: "pending",
  };
  for (let index = 0; index < STEP_IDS.length; index += 1) {
    const id = STEP_IDS[index];
    if (id === "run") {
      if (facts.terminal) states.run = facts.success ? "done" : "error";
      else if (facts.inFlight || currentIndex === STEP_IDS.length - 1) states.run = "current";
      else states.run = "pending";
      continue;
    }
    // A step before the current one was passed; steps 1–3 are also complete when the Core's
    // session is confirmed, which is the only fact that proves the specification exists.
    if (facts.confirmed || index < currentIndex) states[id] = "done";
    else if (index === currentIndex) states[id] = "current";
    else states[id] = "pending";
  }
  return states;
};

/** Mask one value into a finite, non-negative number of milliseconds, or 0 when unknown. */
const moment = (value: unknown): number => {
  const number = Number(value);
  return Number.isFinite(number) && number > 0 ? number : 0;
};

/**
 * A record timestamp (`updated_at`, an ISO-8601 string) as epoch milliseconds, or 0.
 *
 * An unparseable or absent stamp is **unknown**, never "now": a clock must not be started from
 * a value the backend did not publish.
 */
export const stampMs = (value: unknown): number => {
  if (typeof value !== "string" || value.trim() === "") return 0;
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : 0;
};

/**
 * `MM:SS`, or `H:MM:SS` past an hour. Never negative, never rounded up.
 *
 * A fraction of a second is floor-ed away, so the clock only reaches `00:01` after a real
 * second has passed; an unknown or negative duration prints `00:00`, not an invented value.
 */
export const formatElapsed = (seconds: number): string => {
  const total = Number.isFinite(seconds) && seconds > 0 ? Math.floor(seconds) : 0;
  const pad = (value: number): string => String(value).padStart(2, "0");
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const rest = total % 60;
  return hours > 0 ? `${hours}:${pad(minutes)}:${pad(rest)}` : `${pad(minutes)}:${pad(rest)}`;
};

/** Everything the alive bar may read. */
export type AliveFacts = {
  /** A run (or its dispatch) is really in flight. */
  inFlight: boolean;
  /** The run record is terminal. */
  terminal: boolean;
  /** The terminal record's own success verdict. */
  success: boolean;
  /** Epoch ms when this window pressed the button that started the run; 0 when unknown. */
  observedAt: number;
  /** Epoch ms from the run record's own timestamp (`updated_at`); 0 when unknown. */
  endedAt: number;
  /** Epoch ms "now". The caller freezes it once the run is terminal. */
  now: number;
};

/** What the alive bar renders. */
export type AliveState = {
  /** Nothing in flight and nothing finished: no bar at all. */
  visible: boolean;
  /** The moving ribbon: true only while a run is in flight, false on every terminal. */
  ribbon: boolean;
  /** `running` while in flight, then the record's own verdict. */
  phase: "running" | "done" | "failed";
  /** Whole seconds elapsed; 0 when no real start is known. */
  seconds: number;
  /** `formatElapsed(seconds)`. */
  clock: string;
};

/**
 * The bar's state from the facts above.
 *
 * `end` prefers the record's own timestamp and falls back to the caller's frozen "now", so a
 * terminal run never keeps ticking. `start` is the press; when the window did not see the press
 * there is no start and the clock honestly reads `00:00` instead of borrowing another timestamp.
 */
export const aliveState = (facts: AliveFacts): AliveState => {
  const visible = facts.inFlight === true || facts.terminal === true;
  const phase: AliveState["phase"] =
    facts.terminal !== true ? "running" : facts.success === true ? "done" : "failed";
  const start = moment(facts.observedAt);
  const frozenEnd = moment(facts.now);
  const recordEnd = moment(facts.endedAt);
  const end = facts.terminal === true ? (recordEnd > 0 ? recordEnd : frozenEnd) : frozenEnd;
  const seconds =
    visible && start > 0 && end >= start ? Math.floor((end - start) / 1000) : 0;
  return {
    visible,
    ribbon: visible && facts.inFlight === true,
    phase,
    seconds,
    clock: formatElapsed(seconds),
  };
};

/**
 * The stage words the bar prints while a run is in flight.
 *
 * During the synchronous dispatch the UI holds no run id yet, so the honest word is that the
 * Core is executing the task; once a record with events exists the same rule the change strip
 * uses applies (one stage per four persisted events). These are i18n keys, never raw English.
 */
export const aliveStageKey = (
  events: number,
  hasRecord: boolean
): "forge.alive.stage.dispatching" | "forge.alive.stage.starting" | "forge.alive.stage.writing" | "forge.alive.stage.checking" => {
  if (hasRecord !== true) return "forge.alive.stage.dispatching";
  const count = Number.isFinite(events) && events > 0 ? Math.floor(events) : 0;
  if (count === 0) return "forge.alive.stage.starting";
  if (count < 4) return "forge.alive.stage.writing";
  return "forge.alive.stage.checking";
};
