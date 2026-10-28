/**
 * THE CHANGE BLOCK'S HONEST STATES, as plain functions.
 *
 * Measured defect (the owner's own run): `mock` was selected, he pressed
 * «Внести изменения» and the card answered like a success while **zero files** were
 * written. Pressing again created another version with zero files (v3, v4, v5), and a
 * second project's history shows the same shape (v6, v7). Nothing on screen said the
 * model had returned no files, and nothing said the offline engine could not change
 * anything.
 *
 * This module states four rules in one place, so they can be executed (and tested) with
 * Node without React or the network:
 *
 * 1. **mock is loud.** A change whose effective provider is the offline engine carries a
 *    warning *before* the press, whatever the form holds.
 * 2. **one press, one dispatch -- and then the field is the owner's again.** A second press
 *    while the run is genuinely in flight is swallowed; once *any* answer settles (applied,
 *    already_applied, rolled_back, refused, error) the textarea is live, empty and focused
 *    and the same button starts the next change. The one exception is a request that never
 *    reached the Core (`disconnected`): nothing was sent, so the words stay.
 * 3. **a zero-file change is never a success.** Its strip prints «файлов 0 — код не
 *    менялся», and a rolled-back run prints the rollback sentence; neither is dressed as
 *    a success, and neither freezes the field.
 * 4. **the model's own words are surfaced.** `whatForgeDid` builds the «Что сделал
 *    Forge» block from `model_intent.summary`, the written-file list with sizes, and --
 *    when no file came back -- the first two lines of the answer. Nothing is invented:
 *    every empty input stays empty.
 */

/** The provider the installed runtime uses for its offline, deterministic answer. */
export const MOCK_PROVIDER = "mock";

/**
 * Every name that means "no model answered".
 *
 * `deterministic-offline` is the *model* name the offline path publishes
 * (`app/api/service.py`), and `offline` is how people write it; all of them describe the
 * same engine, so all of them must produce the warning. An empty string is **unknown**,
 * not mock: a client that has not learned the provider yet must not accuse it.
 */
const MOCK_NAMES = ["mock", "deterministic-offline", "offline", "deterministic"];

/** True when this provider name means the offline engine answered. */
export const isMockProvider = (name: unknown): boolean => {
  const value = String(name ?? "").trim().toLowerCase();
  if (value === "") return false;
  return MOCK_NAMES.includes(value);
};

/** The provider a press would really use: the owner's choice, else the backend default. */
export const effectiveProvider = (chosen: unknown, fallback: unknown): string => {
  const picked = String(chosen ?? "").trim();
  if (picked !== "") return picked;
  return String(fallback ?? "").trim();
};

/** What a finished change really was. */
export type ChangeOutcomeKind = "none" | "applied" | "empty";

/**
 * EVERY WAY ONE DISPATCH CAN SETTLE, in the owner's words.
 *
 * The field's own behaviour does not depend on *which* of these arrived: once one did, the
 * run is over and the panel belongs to the owner again. The single exception is
 * `disconnected` -- the request never reached the Core, so the words were never sent and
 * must survive for the next press.
 */
export type ChangeSettledKind =
  | "applied"
  | "already_applied"
  | "rolled_back"
  | "refused"
  | "error"
  | "disconnected";

/** Every settled kind, in one list, so a test can execute the rule for each of them. */
export const SETTLED_CHANGE_KINDS: readonly ChangeSettledKind[] = [
  "applied",
  "already_applied",
  "rolled_back",
  "refused",
  "error",
  "disconnected",
];

/** Narrow an unknown value to a settled kind; "" when it names none. */
const settledKinds = (value: unknown): ChangeSettledKind | "" => {
  const word = String(value ?? "").trim().toLowerCase();
  return (SETTLED_CHANGE_KINDS as readonly string[]).includes(word)
    ? (word as ChangeSettledKind)
    : "";
};

/**
 * WHAT THE FIELD IS ALLOWED TO DO, as one value.
 *
 * Measured defect (the owner's words: «Запись пропала, но новую на её месте в поле ввода
 * внести не могу»): after a change the sentence was cleared -- and the textarea and the
 * button stayed **disabled**, so the next instruction could only be given after leaving
 * the screen. That freeze belonged to a design where the card kept the old words on
 * purpose; once the words clear, a frozen empty field is simply a dead panel.
 *
 * The rule is therefore the obvious one: **only a genuinely running change keeps the panel
 * off**. Every settled answer -- applied, already_applied, rolled_back, refused, error --
 * hands the field back, empty, focused and ready for a new sentence. `disconnected` is the
 * only kind that also keeps the words, because nothing reached the Core.
 */
export type ChangeFieldState = {
  /** The textarea, the send button and the screenshot chips are live. */
  canEdit: boolean;
  /** A dispatch or the run it started is genuinely still running. */
  inFlight: boolean;
  /** What the settle does with the owner's words: clear them, or keep them. */
  draft: "clear" | "keep";
  /** The field waits for the next instruction: it takes the cursor and says so. */
  ready: boolean;
};

/** Decide the field's own state from the two facts that can change it. */
export const changeFieldState = (input: {
  /** The dispatch or the run it started is still in flight. */
  inFlight?: unknown;
  /** How the last dispatch settled, or ""/absent while none has. */
  settled?: unknown;
}): ChangeFieldState => {
  if (input.inFlight === true) {
    // The words stay in the field while the run travels; the settle decides their fate.
    return { canEdit: false, inFlight: true, draft: "keep", ready: false };
  }
  const settled = settledKinds(input.settled);
  return {
    canEdit: true,
    inFlight: false,
    draft: settled === "disconnected" ? "keep" : "clear",
    ready: settled !== "",
  };
};

/** Everything the change block knows when it decides what the controls may do. */
export type ChangeFacts = {
  /** The textarea holds no words. */
  empty: boolean;
  /** The dispatch or the run it started is still in flight. */
  inFlight: boolean;
  /** How the last dispatch settled; "" while none has. */
  settled: ChangeSettledKind | "";
  /** Files the change really wrote; 0 is a fact, not an unknown. */
  filesWritten: number;
  /** The effective provider is the offline engine. */
  mocked: boolean;
};

/** What the block must render. */
export type ChangeControls = {
  /** Which action the primary button performs. */
  primary: "start" | "sending";
  /** Whether the primary button is off. */
  primaryDisabled: boolean;
  /** Whether the textarea and the provider selectors are frozen. */
  formFrozen: boolean;
  /** The textarea and the send button accept a new instruction. */
  canEdit: boolean;
  /** The field is ready again and asks for the next sentence. */
  ready: boolean;
  /** Whether the mock warning is printed above the button. */
  mockWarning: boolean;
  /** What the outcome was, for the strip's tone and words. */
  outcome: ChangeOutcomeKind;
};

/** Decide the block's controls from the facts above and nothing else. */
export const changeControls = (facts: ChangeFacts): ChangeControls => {
  const field = changeFieldState({ inFlight: facts.inFlight, settled: facts.settled });
  const settled = settledKinds(facts.settled);
  const wrote = Number(facts.filesWritten ?? 0) > 0;
  return {
    // ONE PRESS, ONE DISPATCH. While the run is genuinely in flight the button says
    // «Отправляю…» and is off; the moment an answer settles it is the ordinary action
    // again, and off only because the field is empty.
    primary: field.inFlight ? "sending" : "start",
    primaryDisabled: field.inFlight || facts.empty === true,
    formFrozen: field.canEdit === false,
    canEdit: field.canEdit,
    ready: field.ready,
    mockWarning: facts.mocked === true,
    // A change that wrote nothing -- or was rolled back -- is not a success.
    outcome: field.ready
      ? wrote && settled !== "rolled_back"
        ? "applied"
        : "empty"
      : "none",
  };
};

/** One written file, as the «Что сделал Forge» block prints it. */
export type ForgeFile = {
  path: string;
  size: number;
  change: string;
};

/** The «Что сделал Forge» block, built from facts that arrived. */
export type ForgeDid = {
  /** The model's own one-line summary, or "" when it published none. */
  summary: string;
  /** The files this change really wrote, with their sizes. */
  files: ForgeFile[];
  /** True when the run wrote no file at all. */
  noFiles: boolean;
  /** The first two non-empty lines of the model's answer, or [] when there are none. */
  answerLines: string[];
};

/** How many lines of the answer the block quotes. */
export const ANSWER_LINES = 2;

/** Split the answer excerpt into the first `ANSWER_LINES` non-empty lines. */
export const answerLines = (excerpt: unknown, limit: number = ANSWER_LINES): string[] => {
  const text = String(excerpt ?? "");
  const out: string[] = [];
  for (const line of text.split(/\r?\n/)) {
    const collapsed = line.replace(/\s+/g, " ").trim();
    if (collapsed === "") continue;
    out.push(collapsed);
    if (out.length >= Math.max(1, limit)) break;
  }
  return out;
};

/**
 * Build the «Что сделал Forge» block.
 *
 * `summary` is the model's own `model_intent.summary`; `files` is the version manifest
 * that was really written; `answerExcerpt` is the backend's screened first lines. When
 * the run wrote nothing, `noFiles` is true and the block quotes the answer instead of
 * inventing an explanation.
 */
export const whatForgeDid = (input: {
  summary?: unknown;
  files?: ReadonlyArray<{ path?: unknown; size?: unknown; change?: unknown }> | null;
  answerExcerpt?: unknown;
}): ForgeDid => {
  const files: ForgeFile[] = [];
  for (const item of input.files ?? []) {
    const path = String(item?.path ?? "").trim();
    if (path === "") continue;
    const size = Number(item?.size ?? 0);
    files.push({
      path,
      size: Number.isFinite(size) && size >= 0 ? size : 0,
      change: String(item?.change ?? "").trim(),
    });
  }
  return {
    summary: String(input.summary ?? "").trim(),
    files,
    noFiles: files.length === 0,
    answerLines: answerLines(input.answerExcerpt),
  };
};

/**
 * The honest word for a recorded version's files.
 *
 * `empty` covers exactly the measured case: a version slot that exists, holds no file,
 * and therefore proves that the code did not change. It never reads as success.
 */
export const versionFilesWord = (fileCount: unknown): "empty" | "files" => {
  const count = Number(fileCount ?? 0);
  return Number.isFinite(count) && count > 0 ? "files" : "empty";
};

/**
 * Did one dispatch of a change actually reach the Core?
 *
 * The owner's complaint: after sending a change the textarea kept his words, so asking
 * for the next change meant clearing it by hand. The words belong to the change that
 * was sent, so they are cleared once the answer arrives — but **only** then. A request
 * that never reached the Core (`kind: "disconnected"`: the server is down, the socket
 * failed, DNS failed) did not send anything, and throwing the owner's sentence away
 * there would lose work he would have to retype.
 *
 * A failure with no `kind` at all is treated as an answer, because the Core answered
 * something (an HTTP status, a refusal body): only the measured "never arrived" kind
 * keeps the text.
 */
export const dispatchReachedTheCore = (failure: unknown): boolean => {
  if (failure === null || failure === undefined) return true;
  if (typeof failure !== "object") return true;
  const kind = String((failure as { kind?: unknown }).kind ?? "")
    .trim()
    .toLowerCase();
  return kind !== "disconnected";
};

/**
 * WHICH ANSWER THE CARD HOLDS, as one word, from the card's own three facts.
 *
 * The screen keeps exactly three slots for a settled change: the outcome object of the run
 * that answered, the refusal of the staging request, and the transport failure of the
 * dispatch. This function names them, so the field, the strip and the tests cannot
 * disagree about what happened.
 */
export const changeSettledKind = (facts: {
  /** The outcome object the card keeps for the run that answered. */
  outcome?: { alreadyApplied?: unknown; rolledBack?: unknown } | null;
  /** The staging request was refused (an answer, so no change was queued). */
  changeFailure?: unknown;
  /** The dispatch failed; `dispatchReachedTheCore` separates an answer from a lost request. */
  dispatchFailure?: unknown;
}): ChangeSettledKind | "" => {
  // A refusal of the words themselves is never a transport failure.
  if (facts.changeFailure !== null && facts.changeFailure !== undefined) return "refused";
  if (facts.dispatchFailure !== null && facts.dispatchFailure !== undefined) {
    return dispatchReachedTheCore(facts.dispatchFailure) ? "error" : "disconnected";
  }
  const outcome = facts.outcome;
  if (outcome === null || outcome === undefined) return "";
  if (outcome.alreadyApplied === true) return "already_applied";
  if (outcome.rolledBack === true) return "rolled_back";
  return "applied";
};

/** Everything one dispatch attempt can tell us about the field, and nothing else. */
export type ChangeDispatchResult = {
  /** The request reached the Core and was answered (a success **or** a refusal). */
  ok?: unknown;
  /** The transport failure, when the request did not succeed. */
  failure?: unknown;
};

/**
 * What happens to the owner's words after one dispatch: `clear` or `keep`.
 *
 * `clear` for every completed dispatch — a success and a refusal are both "sent, and
 * answered". `keep` only when the request never reached the Core, so nothing was lost.
 */
export const changeDraftAfterDispatch = (
  result: ChangeDispatchResult | null | undefined
): "clear" | "keep" => {
  if (result !== null && result !== undefined && result.ok === true) return "clear";
  return dispatchReachedTheCore(result ? result.failure : null) ? "clear" : "keep";
};
