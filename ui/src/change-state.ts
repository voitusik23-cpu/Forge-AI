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
 * 2. **one press, then a different button.** After a change that wrote files the primary
 *    button is off and reads «Изменения внесены (vN)»; the form is frozen until the owner
 *    presses the smaller «Внести ещё изменения».
 * 3. **a zero-file change is never a success.** Its primary button reads «Повторить с
 *    реальной моделью» and switches the provider to the best available non-mock one; the
 *    strip prints «файлов 0 — код не менялся».
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

/**
 * The best available provider that is not the offline engine, or "".
 *
 * "Best" is not a judgement about quality: the deployment's own default is preferred
 * because that is the pair its operator configured, and the first enabled provider is
 * used when the default *is* the mock. Everything else is left alone.
 */
export const bestRealProvider = (options: readonly unknown[], fallback: unknown): string => {
  const real = options
    .map((item) => String(item ?? "").trim())
    .filter((item) => item !== "" && !isMockProvider(item));
  if (real.length === 0) return "";
  const preferred = String(fallback ?? "").trim();
  if (preferred !== "" && real.includes(preferred)) return preferred;
  return real[0];
};

/** What a finished change really was. */
export type ChangeOutcomeKind = "none" | "applied" | "empty";

/** Everything the change block knows when it decides what the button says. */
export type ChangeFacts = {
  /** The textarea holds no words. */
  empty: boolean;
  /** The dispatch or the run it started is still in flight. */
  inFlight: boolean;
  /** A recorded outcome exists for this change (a version or a refusal). */
  finished: boolean;
  /** Files the change really wrote; 0 is a fact, not an unknown. */
  filesWritten: number;
  /** The version the change produced, 0 when it produced none. */
  version: number;
  /** The effective provider is the offline engine. */
  mocked: boolean;
  /** At least one non-mock provider is available to switch to ("" when none). */
  realProvider: string;
  /** The owner pressed «Внести ещё изменения», so the form is live again. */
  armed: boolean;
};

/** What the block must render. */
export type ChangeControls = {
  /** Which action the primary button performs. */
  primary: "start" | "sending" | "applied" | "retry";
  /** Whether the primary button is off. */
  primaryDisabled: boolean;
  /** The version the «Внесены» label names, 0 when it names none. */
  primaryVersion: number;
  /** Whether the smaller «Внести ещё изменения» control is offered. */
  secondaryVisible: boolean;
  /** Whether the textarea and the provider selectors are frozen. */
  formFrozen: boolean;
  /** Whether the mock warning is printed above the button. */
  mockWarning: boolean;
  /** What the outcome was, for the strip's tone and words. */
  outcome: ChangeOutcomeKind;
};

/** Decide the block's controls from the facts above and nothing else. */
export const changeControls = (facts: ChangeFacts): ChangeControls => {
  const mockWarning = facts.mocked === true;
  if (facts.inFlight === true) {
    return {
      primary: "sending",
      primaryDisabled: true,
      primaryVersion: 0,
      secondaryVisible: false,
      formFrozen: true,
      mockWarning,
      outcome: "none",
    };
  }
  if (facts.finished !== true) {
    return {
      primary: "start",
      primaryDisabled: facts.empty === true,
      primaryVersion: 0,
      secondaryVisible: false,
      formFrozen: false,
      mockWarning,
      outcome: "none",
    };
  }
  const wrote = Number(facts.filesWritten ?? 0) > 0;
  const outcome: ChangeOutcomeKind = wrote ? "applied" : "empty";
  if (facts.armed === true) {
    // The owner asked for another change: the form is live and the primary action is
    // the ordinary one again. The previous outcome's strip stays, so the record of what
    // just happened is never wiped by the next attempt.
    return {
      primary: "start",
      primaryDisabled: facts.empty === true,
      primaryVersion: 0,
      secondaryVisible: false,
      formFrozen: false,
      mockWarning,
      outcome,
    };
  }
  if (wrote) {
    return {
      primary: "applied",
      primaryDisabled: true,
      primaryVersion: Number(facts.version ?? 0),
      secondaryVisible: true,
      formFrozen: true,
      mockWarning,
      outcome,
    };
  }
  return {
    // A change that wrote nothing is not a success and must not be labelled like one.
    primary: "retry",
    primaryDisabled: String(facts.realProvider ?? "") === "",
    primaryVersion: 0,
    secondaryVisible: true,
    formFrozen: true,
    mockWarning,
    outcome,
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
