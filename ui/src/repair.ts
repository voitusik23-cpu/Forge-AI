/**
 * THE REPAIR PANEL'S HONEST RULES, as plain functions.
 *
 * The owner's own sentence, twice: «если падает программа, Forge сам её и чинит, показывает
 * ошибку, предлагает пути решения» and «я там мог бы с ним переписываться и писать, что надо
 * сделать». The launch button already answered with a typed ``409 project_launch_refused``
 * carrying the run's state, its exit code and the redacted tail of its log
 * (``ui/src/api/forge.ts``, ``app/projects/launcher.py``). What was missing is the one thing
 * this module states: **what that log means, in one plain sentence**, and how many times
 * Forge may try to repair it by itself.
 *
 * Everything here is a pure function of the facts the backend published -- no React, no
 * ``fetch``, no clocks -- so `tests/test_self_repair.py` executes *this* code with Node and
 * checks the words the owner will read, instead of re-implementing them in Python.
 *
 * The rules, in one place:
 *
 * 1. **the cause is read from the log, never guessed.** `mapLaunchCause` recognises exactly
 *    five shapes (a missing library, a busy port, a syntax error, a program waiting for
 *    console input, a process that exited) and says «причина в журнале не видна» when
 *    nothing matches. It never invents a module, a port or a line number.
 * 2. **the panel model is the whole panel's state**, derived from the refusal's own fields
 *    plus the number of auto-repairs already spent: the sentence, the three visible log
 *    lines, the full (bounded) tail, the fold label, and whether the button is still on.
 * 3. **an automatic repair is one change run, then one launch retry -- and at most two of
 *    those.** `driveRepair` performs exactly one dispatch and at most one launch per press;
 *    after `REPAIR_ATTEMPT_LIMIT` attempts it stops and says «больше не пробую сам —
 *    скажите, что менять». A run that wrote no file never reaches the launch: the panel says
 *    «изменения не записаны», because relaunching unchanged code would only fail again.
 * 4. **the instruction is deterministic.** The same cause always composes the same change
 *    text, so the backend's own idempotency (the same words are one change) applies and a
 *    second press cannot breed another empty version.
 *
 * The *displayed* sentences live in `i18n.ts` in both languages, and the model returns their
 * keys plus parameters (the same shape `ui/src/forge-result.ts` uses). The one sentence this
 * module writes itself is the instruction sent to the Core: it is the owner's own Russian,
 * because the run answers in that language, and it must stay byte-identical between the two
 * callers (the panel and its test).
 */

/** The launch refusal's structured fields, as the surface publishes them. */
export type LaunchFacts = {
  /** The redacted tail of the run's own log (already screened by the runner). */
  log_tail?: unknown;
  /**
   * The redacted tail of `<project>\launch.log`: what the project's *own* launcher wrote
   * when it was started from its desktop shortcut, outside Forge. Forge reads it so both
   * ways of launching end in the same panel.
   */
  launch_log_tail?: unknown;
  /** Where that file is, relative to the project folder (a display string). */
  launch_log_path?: unknown;
  /** Where the run's own log is (a display string; the backend never publishes an absolute path). */
  log_path?: unknown;
  exit_code?: unknown;
  waiting_for_input?: unknown;
  run_state?: unknown;
  /** The backend's own sentence for the refusal. */
  error?: unknown;
};

/** The five recognised causes, plus the honest "nothing in the log names it". */
export type RepairCauseKind =
  | "missing_module"
  | "port_busy"
  | "syntax_error"
  | "waiting_for_input"
  | "exit_code"
  | "unknown";

/** One cause, exactly as the log names it. Empty fields stay empty -- nothing is invented. */
export type RepairCause = {
  kind: RepairCauseKind;
  /** The one log line the cause was read from; "" when the log named nothing. */
  line: string;
  /** The library for `missing_module`; "" otherwise. */
  moduleName: string;
  /** The port for `port_busy`; 0 when the log did not name one. */
  port: number;
  /** The 1-based source line for `syntax_error`; 0 when unknown. */
  lineNumber: number;
  /** The process exit code for `exit_code`; null when unknown. */
  exitCode: number | null;
};

/** How many automatic repair attempts Forge makes per project in one session. */
export const REPAIR_ATTEMPT_LIMIT = 2;

/** How many log lines stay visible before the rest is folded away. */
export const REPAIR_VISIBLE_LOG_LINES = 3;

/** The most log lines the panel keeps at all: a tail, never the whole file. */
export const REPAIR_LOG_TAIL_LIMIT = 20;

const toStringList = (value: unknown): string[] => {
  if (!Array.isArray(value)) return [];
  const lines: string[] = [];
  for (const item of value) {
    const line = String(item ?? "").replace(/[\r\n]+$/, "");
    if (line.trim() !== "") lines.push(line);
  }
  return lines;
};

/**
 * The log lines the cause is read from: the run's own log first, then the project launcher's.
 *
 * The order matters. When Forge started the process, the run log is the child's own output
 * and the launcher log is history; when the owner started it from the desktop shortcut, only
 * the launcher log exists. Reading them in that order means the newest, most relevant line
 * wins without ever throwing the other one away.
 */
export const logLinesOf = (facts: LaunchFacts): string[] => {
  const run = toStringList(facts?.log_tail);
  const own = toStringList(facts?.launch_log_tail);
  const all = [...run, ...own];
  return all.length > REPAIR_LOG_TAIL_LIMIT ? all.slice(-REPAIR_LOG_TAIL_LIMIT) : all;
};

/** Structural traceback lines are a crash, not a prompt -- the same rule the runner applies. */
const TRACEBACK_PREFIXES = [
  "traceback (most recent call last)",
  "during handling of the above exception",
  "the above exception was the direct cause",
];

const PROMPT_WORDS = ["выбрать", "введите", "enter", "input"];

/** Whether one line looks like an application waiting for console input. */
const looksLikePrompt = (line: string): boolean => {
  const trimmed = line.trim();
  if (trimmed === "") return false;
  if (trimmed.startsWith("--- ") || trimmed.startsWith("# ")) return false;
  const lowered = trimmed.toLowerCase();
  if (TRACEBACK_PREFIXES.some((prefix) => lowered.startsWith(prefix))) return false;
  if (trimmed.startsWith('File "') || trimmed.startsWith("File '")) return false;
  // An indented line is a source frame or a continuation, never a prompt.
  if (/^[ \t]/.test(line)) return false;
  if (trimmed.endsWith(":") || trimmed.endsWith("：")) return true;
  return PROMPT_WORDS.some((word) => lowered.includes(word));
};

/** The library a missing-module line names, or "" when the shape is not one of the known ones. */
const moduleOf = (line: string): string => {
  const patterns = [
    /No module named ['"]?([A-Za-z0-9_][A-Za-z0-9_.\-]*)['"]?/i,
    /Cannot find module ['"]([^'"]+)['"]/i,
    /Cannot find package ['"]([^'"]+)['"]/i,
    /ModuleNotFoundError:\s*([A-Za-z0-9_][A-Za-z0-9_.\-]*)/i,
  ];
  for (const pattern of patterns) {
    const match = pattern.exec(line);
    if (match && match[1] && match[1].trim() !== "") return match[1].trim();
  }
  return "";
};

const PORT_BUSY_MARKERS = [
  "winerror 10013",
  "winerror 10048",
  "wsaeacces",
  "eaddrinuse",
  "address already in use",
  "only one usage of each socket address",
  "errno 10048",
  "errno 98",
];

/** The port a busy-port line names, or 0 when it names none. */
const portOf = (line: string): number => {
  const match = /(?:127\.0\.0\.1['"]?\s*,\s*|:)(\d{2,5})\b/.exec(line);
  if (!match) return 0;
  const port = Number(match[1]);
  return Number.isFinite(port) && port > 0 && port <= 65535 ? port : 0;
};

const SYNTAX_MARKERS = ["syntaxerror", "indentationerror", "taberror", "unexpected token"];

/** The 1-based source line a syntax error names, or 0 when the log does not say. */
const lineNumberOf = (lines: string[]): number => {
  // Python: `  File "...\app.py", line 12, in <module>`; Node: `at file.js:12:5`.
  for (const line of lines) {
    const python = /,\s*line\s+(\d+)/i.exec(line);
    if (python) return Number(python[1]);
    const node = /[A-Za-z0-9_.\-]+\.(?:js|mjs|cjs|ts|tsx|py):(\d+)(?::\d+)?/.exec(line);
    if (node) return Number(node[1]);
  }
  return 0;
};

const cause = (partial: Partial<RepairCause> & { kind: RepairCauseKind }): RepairCause => ({
  kind: partial.kind,
  line: partial.line ?? "",
  moduleName: partial.moduleName ?? "",
  port: partial.port ?? 0,
  lineNumber: partial.lineNumber ?? 0,
  exitCode: partial.exitCode ?? null,
});

/**
 * What the log says happened, in the order the facts become trustworthy.
 *
 * A concrete error in the log wins over the generic exit code: «не хватает библиотеки flask»
 * is what the owner can act on, «код 1» is only the last resort the requirement names. The
 * `waiting_for_input` fact the backend publishes is used when nothing in the log is concrete
 * -- and prompt-shaped log lines are recognised here too, because a launcher log written
 * outside Forge carries no such fact.
 */
export const mapLaunchCause = (facts: LaunchFacts): RepairCause => {
  const lines = logLinesOf(facts);
  const exitCode =
    typeof facts?.exit_code === "number" && Number.isFinite(facts.exit_code)
      ? Math.trunc(facts.exit_code)
      : null;

  for (const line of lines) {
    const moduleName = moduleOf(line);
    if (moduleName !== "") return cause({ kind: "missing_module", line, moduleName });
  }

  for (const line of lines) {
    const lowered = line.toLowerCase();
    if (PORT_BUSY_MARKERS.some((marker) => lowered.includes(marker))) {
      return cause({ kind: "port_busy", line, port: portOf(line) });
    }
  }

  for (const line of lines) {
    const lowered = line.toLowerCase();
    if (SYNTAX_MARKERS.some((marker) => lowered.includes(marker))) {
      return cause({ kind: "syntax_error", line, lineNumber: lineNumberOf(lines) });
    }
  }

  if (facts?.waiting_for_input === true || lines.some((line) => looksLikePrompt(line))) {
    const line = lines.find((item) => looksLikePrompt(item)) ?? "";
    return cause({ kind: "waiting_for_input", line });
  }

  if (exitCode !== null) {
    const line = lines.length > 0 ? lines[lines.length - 1] : "";
    return cause({ kind: "exit_code", line, exitCode });
  }

  return cause({ kind: "unknown", line: lines.length > 0 ? lines[lines.length - 1] : "" });
};

/** The i18n key of the plain clause, per cause. Both languages live in `i18n.ts`. */
export const causeSentenceKey = (kind: RepairCauseKind): string => {
  const keys: Record<RepairCauseKind, string> = {
    missing_module: "projects.repair.cause.missing_module",
    port_busy: "projects.repair.cause.port_busy",
    syntax_error: "projects.repair.cause.syntax_error",
    waiting_for_input: "projects.repair.cause.waiting_for_input",
    exit_code: "projects.repair.cause.exit_code",
    unknown: "projects.repair.cause.unknown",
  };
  return keys[kind] ?? keys.unknown;
};

/** The parameters a clause (or the headline) is filled with. Only measured values appear. */
export const causeParams = (value: RepairCause): Record<string, string> => {
  const params: Record<string, string> = {};
  if (value.moduleName !== "") params.module = value.moduleName;
  if (value.lineNumber > 0) params.line = String(value.lineNumber);
  if (value.port > 0) params.port = String(value.port);
  if (value.exitCode !== null) params.code = String(value.exitCode);
  return params;
};

/**
 * The deterministic change text an automatic repair sends.
 *
 * It must carry the log line itself: the run that has to fix the project reads *this* text,
 * and «почини» without the error is a guess. The closing order («исправь и добейся, чтобы
 * приложение запускалось») is the owner's requirement verbatim. `clause` is the sentence the
 * panel shows, so the instruction and the screen carry the same words; when it is omitted the
 * canonical Russian clause of the module is used, which is what the test asserts.
 */
export const composeRepairInstruction = (value: RepairCause, clause = ""): string => {
  const canonical: Record<RepairCauseKind, string> = {
    missing_module:
      value.moduleName === ""
        ? "не хватает библиотеки — её нужно установить"
        : `не хватает библиотеки ${value.moduleName} — её нужно установить`,
    port_busy: "порт занят другой программой",
    syntax_error:
      value.lineNumber > 0
        ? `ошибка в коде: строка ${value.lineNumber}`
        : "ошибка в коде",
    waiting_for_input: "программа ждёт ввода — так запускать нельзя",
    exit_code:
      value.exitCode === null
        ? "программа завершилась с ошибкой"
        : `программа завершилась с ошибкой (код ${value.exitCode})`,
    unknown: "причина не видна в журнале",
  };
  const reason = clause.trim() !== "" ? clause.trim() : canonical[value.kind];
  const where =
    value.line.trim() !== ""
      ? `В журнале: ${value.line.trim()}`
      : "Журнал не назвал ни одной строки с причиной.";
  return (
    `Приложение не запускается: ${reason}. ${where} ` +
    "Исправь и добейся, чтобы приложение запускалось."
  );
};

/** One panel, derived from the refusal's fields and the attempts already spent. */
export type RepairPanelModel = {
  cause: RepairCause;
  /** True when the refusal really carries run facts: without them no panel is shown. */
  hasRunFacts: boolean;
  /** i18n key of the clause: «не хватает библиотеки flask — её нужно установить». */
  sentenceKey: string;
  /** i18n key of the whole line: «Не запустилось: {cause}». */
  headlineKey: string;
  params: Record<string, string>;
  /** The lines that stay visible. */
  visibleLines: string[];
  /** The whole bounded tail, folded under «Показать журнал полностью». */
  logLines: string[];
  logPath: string;
  attempts: number;
  attemptsLimit: number;
  /** How many automatic repairs are still allowed. */
  remaining: number;
  /** Whether «Исправить автоматически» may be pressed at all. */
  canAutoRepair: boolean;
};

export const repairPanelModel = (facts: LaunchFacts, attempts = 0): RepairPanelModel => {
  const value = mapLaunchCause(facts ?? {});
  const logLines = logLinesOf(facts ?? {});
  const exitCode = typeof facts?.exit_code === "number" && Number.isFinite(facts.exit_code);
  const hasRunFacts =
    logLines.length > 0 ||
    exitCode ||
    facts?.waiting_for_input === true ||
    value.kind === "waiting_for_input" ||
    value.kind === "exit_code" ||
    value.kind === "missing_module" ||
    value.kind === "port_busy" ||
    value.kind === "syntax_error";
  const spent = Number.isFinite(attempts) && attempts > 0 ? Math.trunc(attempts) : 0;
  const logPath = String(
    (facts?.launch_log_path ?? "") !== "" ? facts?.launch_log_path : facts?.log_path ?? ""
  );
  return {
    cause: value,
    hasRunFacts,
    sentenceKey: causeSentenceKey(value.kind),
    headlineKey: "projects.repair.headline",
    params: causeParams(value),
    visibleLines: logLines.slice(-REPAIR_VISIBLE_LOG_LINES),
    logLines,
    logPath,
    attempts: spent,
    attemptsLimit: REPAIR_ATTEMPT_LIMIT,
    remaining: Math.max(0, REPAIR_ATTEMPT_LIMIT - spent),
    canAutoRepair: spent < REPAIR_ATTEMPT_LIMIT,
  };
};

/** Where one repair stands after a press: the loop's whole state, as data. */
export type RepairPhase = "idle" | "working" | "opened" | "nothing_written" | "failed" | "capped";

export type RepairState = {
  /** Automatic repair attempts spent (manual chat presses do not count). */
  attempts: number;
  phase: RepairPhase;
  /** The cause of the *latest* failure, when a retry failed again. */
  cause: RepairCause | null;
  /** The URL a successful retry opened. */
  url: string;
  /** i18n key of the outcome sentence plus its parameters. */
  messageKey: string;
  messageParams: Record<string, string>;
  /** The newest refusal facts, so the panel can re-derive its cause and log. */
  facts: LaunchFacts | null;
};

export const initialRepairState = (attempts = 0): RepairState => ({
  attempts: Number.isFinite(attempts) && attempts > 0 ? Math.trunc(attempts) : 0,
  phase: "idle",
  cause: null,
  url: "",
  messageKey: "",
  messageParams: {},
  facts: null,
});

/** What one dispatched change run really did. */
export type RepairRunFacts = {
  ok: boolean;
  /** Files the run wrote; 0 means the code did not change. */
  filesWritten: number;
  version: number;
};

/** What one launch retry really did. */
export type RepairLaunchFacts = {
  ok: boolean;
  url: string;
  facts: LaunchFacts;
};

/**
 * The one place a repair is executed. The caller supplies the two real requests, so the panel
 * and the test drive the *same* steps; nothing here knows about React or `fetch`.
 */
export type RepairDeps = {
  dispatch: (instruction: string) => Promise<RepairRunFacts>;
  launch: () => Promise<RepairLaunchFacts>;
};

/** What one press does: exactly one change run, then at most one launch retry. */
export type RepairOptions = {
  instruction: string;
  /** Automatic presses spend an attempt and are capped; a typed message does not. */
  countAttempt?: boolean;
};

export const driveRepair = async (
  deps: RepairDeps,
  state: RepairState,
  options: RepairOptions
): Promise<RepairState> => {
  const count = options.countAttempt !== false;
  const spent = state.attempts;
  if (count && spent >= REPAIR_ATTEMPT_LIMIT) {
    // The cap is a promise, not a warning: nothing is dispatched, nothing is launched.
    return {
      ...state,
      phase: "capped",
      messageKey: "projects.repair.capped",
      messageParams: {},
    };
  }
  const attempts = count ? spent + 1 : spent;
  const instruction = String(options.instruction ?? "").trim();
  if (instruction === "") {
    return {
      ...state,
      attempts,
      phase: "failed",
      messageKey: "projects.repair.dispatch_failed",
      messageParams: {},
    };
  }

  const run = await deps.dispatch(instruction);
  if (!run.ok) {
    return {
      ...state,
      attempts,
      phase: "failed",
      messageKey: "projects.repair.dispatch_failed",
      messageParams: {},
    };
  }
  if (run.filesWritten <= 0) {
    // A run that wrote nothing cannot have repaired anything: relaunching unchanged code
    // would only produce the same failure, so the launch is not retried at all.
    return {
      ...state,
      attempts,
      phase: "nothing_written",
      messageKey: "projects.repair.nothing_written",
      messageParams: { version: String(run.version ?? 0) },
    };
  }

  const launched = await deps.launch();
  if (launched.ok) {
    return {
      attempts,
      phase: "opened",
      cause: state.cause,
      url: String(launched.url ?? ""),
      messageKey: "projects.repair.opened",
      messageParams: { url: String(launched.url ?? "") },
      facts: null,
    };
  }
  const cause = mapLaunchCause(launched.facts ?? {});
  const capped = count && attempts >= REPAIR_ATTEMPT_LIMIT;
  return {
    attempts,
    phase: capped ? "capped" : "failed",
    cause,
    url: "",
    messageKey: capped ? "projects.repair.capped" : "projects.repair.failed_again",
    messageParams: causeParams(cause),
    facts: launched.facts ?? {},
  };
};
