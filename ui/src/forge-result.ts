// What a run's result card says, derived from the facts and never from `state`.
//
// The measured failure this module exists for: a run that wrote no file was reported as
// `state=COMPLETED`, `status=COMPLETED`, `classification.outcome=TERMINAL_SUCCESS` while the
// model had answered with a plan and the question "which option, A/B/C?". The word COMPLETED
// is the *dispatch* finishing, not a project existing, so it is never the headline here.
//
// Every branch below reads a fact the backend actually reported:
//
//   * `files_created` — how many files the guarded writer really applied;
//   * `project_state.code_write.status` / `.verification` / `.reason` — what the writer did;
//   * `clarification_questions` — what the model asked instead of writing;
//   * `error` — the dispatch's own failure, when it had one;
//   * `writeRequested` — whether files were even asked for (a read-only run owes none).
//
// The module returns i18n keys and `{n}`-style parameters, never prose: every sentence lives
// in `i18n.ts` in both languages, and the technical words (`COMPLETED`, the raw English
// `code_write.reason`, the diff) never reach this file's output.

import type { Lang } from "./i18n";

/** The one action a headline offers. Each id maps to a button in the result card. */
export type ResultActionId = "open_files" | "launch_local" | "verify" | "answer" | "retry";

/** The shape of the headline, which also decides its colour and the last stage chip. */
export type ResultKind =
  /** Files were applied and the writer's own post-write check passed (or did not apply). */
  | "ready"
  /** Files were applied, but the run's verification has no passed result yet. */
  | "unverified"
  /** Nothing was written and the model asked questions instead. */
  | "questions"
  /** Nothing was written and the model answered with a plan or a report. */
  | "plan"
  /** Nothing was written and nothing was asked for: a plain read-only answer. */
  | "not_written"
  /** The writer refused the answer (a forbidden path, a broken block, a failed check). */
  | "refused"
  /** The dispatch itself failed: no answer, a provider error, a timeout. */
  | "error";

export type ResultHeadline = {
  kind: ResultKind;
  /** The i18n key of the one headline sentence. */
  key: string;
  /** `{n}` and `{reason}` substitutions; a reason is itself an i18n key lookup. */
  params: Record<string, string>;
  /** The `.project-truth` state word, which is also the tone the block is drawn with. */
  state: string;
  actions: ResultActionId[];
};

/** Everything the headline is allowed to read. Nothing here is inferred from `state`. */
export type RunResultFacts = {
  filesCreated: number;
  /** `project_state.code_write.status`, or "" when the record carries no write at all. */
  writeStatus: string;
  /** `project_state.code_write.verification`, or "". */
  verification: string;
  /** `project_state.code_write.reason`: the raw English writer reason. */
  writeReason: string;
  /** The questions the final answer asked, as the backend extracted them. */
  questions: string[];
  /** `needs_clarification`: the backend's own verdict that the work stopped to ask. */
  needsClarification: boolean;
  /** The dispatch's own error text, when it reported one. */
  error: string;
  /** Whether the run asked for files (`write_mode`), captured at submit time. */
  writeRequested: boolean;
};

/**
 * The fixed mapping from a writer/`code_write` reason to a plain sentence key.
 *
 * The reasons are the writer's own constants (`app/agent_runtime/code_writer.py`), so the
 * mapping is closed: a prefix decides the sentence, and anything unrecognised becomes "файлы
 * не записаны" with the technical text kept for the details block only. No English reason is
 * ever shown above the fold.
 */
export const reasonKeyOf = (reason: string): string => {
  const text = String(reason ?? "").trim();
  if (text === "") return "forge.reason.unknown";
  if (text.startsWith("denied_environment_file")) return "forge.reason.denied_env";
  if (text.startsWith("denied_directory")) return "forge.reason.denied_directory";
  if (
    text.startsWith("denied_secret_file") ||
    text.startsWith("denied_secret_directory") ||
    text.startsWith("denied_credential_like_name") ||
    text.startsWith("denied_migration_history") ||
    text.startsWith("denied_file")
  ) {
    return "forge.reason.denied_path";
  }
  if (text.startsWith("model_answer_not_parseable")) return "forge.reason.parse_error";
  if (text.startsWith("planning_failed")) return "forge.reason.planning_failed";
  if (text.startsWith("write_failed")) return "forge.reason.write_failed";
  if (
    text.startsWith("workspace_not_usable") ||
    text.startsWith("workspace_unavailable")
  ) {
    return "forge.reason.workspace";
  }
  if (text.startsWith("materialization_")) return "forge.reason.materialization";
  if (text.startsWith("run_id_is_not_usable")) return "forge.reason.bad_run_id";
  if (text === "the model answer is empty") return "forge.reason.empty_answer";
  if (
    text ===
    "the model answer carries no fenced block with a path header and no unified diff"
  ) {
    return "forge.reason.files_missing";
  }
  if (text === "no change survived validation") return "forge.reason.no_change_valid";
  if (text.startsWith("verification")) return "forge.reason.verification_failed";
  return "forge.reason.unknown";
};

/**
 * The reason a failed *dispatch* gives, in the same closed vocabulary.
 *
 * A provider error arrives as English prose from the provider layer, so it is classified by
 * shape only: the timeouts, the provider refusals, and the write-mode family (whose real
 * reason is in `code_write.reason`, mapped above) keep their own sentences. Everything else
 * is the honest default, and the raw text stays in the details block.
 */
export const dispatchReasonKeyOf = (error: string): string => {
  const text = String(error ?? "").trim();
  if (text === "") return "forge.reason.unknown";
  const lowered = text.toLowerCase();
  if (lowered.includes("timeout") || lowered.includes("timed out")) {
    return "forge.reason.timeout";
  }
  if (lowered.includes("write mode")) return "forge.reason.write_mode";
  if (lowered.includes("provider")) return "forge.reason.provider_failed";
  return "forge.reason.unknown";
};

/** Russian needs three forms for a count; English needs two. */
export const pluralForm = (lang: Lang, count: number): "one" | "few" | "many" => {
  if (lang === "en") return count === 1 ? "one" : "many";
  const mod100 = Math.abs(count) % 100;
  const mod10 = Math.abs(count) % 10;
  if (mod100 >= 11 && mod100 <= 14) return "many";
  if (mod10 === 1) return "one";
  if (mod10 >= 2 && mod10 <= 4) return "few";
  return "many";
};

/** The form a count takes in the current language: only `ready` needs it. */
const filesWord = (count: number): string => (count === 1 ? "1" : String(count));

/**
 * Whether an answer really *asked* rather than answered with prose.
 *
 * The clarification extractor returns the trailing sentence of any answer that ends in a
 * question mark, so the owner's measured answer -- a plan and the question "which option,
 * A/B/C?" -- comes back as one "question" that is in fact the whole plan. Treating that as
 * a question list would hide the real finding (a plan instead of files) behind a polite
 * "Forge asked questions". Two facts settle it: the backend's own `needs_clarification`,
 * or a list of at least two questions.
 */
export const answerAskedQuestions = (facts: RunResultFacts): boolean =>
  facts.questions.length > 0 && (facts.needsClarification || facts.questions.length >= 2);

/** The headline for one run, from its facts only. */
export const deriveRunHeadline = (lang: Lang, facts: RunResultFacts): ResultHeadline => {
  const files = Number.isFinite(facts.filesCreated) ? Math.max(0, Math.trunc(facts.filesCreated)) : 0;
  const verification = String(facts.verification ?? "").trim();
  const writeStatus = String(facts.writeStatus ?? "").trim();

  if (files > 0) {
    if (verification === "failed") {
      return {
        kind: "refused",
        key: "forge.headline.refused",
        params: { reason: "forge.reason.verification_failed" },
        state: "ОШИБКА",
        actions: ["retry"],
      };
    }
    if (verification === "passed" || verification === "not_applicable") {
      return {
        kind: "ready",
        key: `forge.headline.ready.${pluralForm(lang, files)}`,
        params: { n: filesWord(files) },
        state: "ГОТОВО",
        actions: ["open_files", "launch_local"],
      };
    }
    // Files exist but the run's verification has not reported a pass yet: say so instead of
    // claiming the check passed.
    return {
      kind: "unverified",
      key: "forge.headline.unverified",
      params: { n: filesWord(files) },
      state: "ФАЙЛЫ СОЗДАНЫ",
      actions: ["open_files", "verify"],
    };
  }

  // Nothing on disk, so the question is *why*, and the answer is never "COMPLETED".
  if (answerAskedQuestions(facts)) {
    return {
      kind: "questions",
      key: "forge.headline.questions",
      params: {},
      state: "НЕ СОЗДАН",
      actions: ["answer"],
    };
  }
  if (writeStatus === "no_changes") {
    // The measured case: a plan, a report, or a question instead of files.
    return {
      kind: "plan",
      key: "forge.headline.plan",
      params: {},
      state: "НЕ СОЗДАН",
      actions: ["retry"],
    };
  }
  if (writeStatus === "rejected" || writeStatus === "verification_failed") {
    return {
      kind: "refused",
      key: "forge.headline.refused",
      params: { reason: reasonKeyOf(facts.writeReason) },
      state: "ОШИБКА",
      actions: ["retry"],
    };
  }
  if (String(facts.error ?? "").trim() !== "") {
    return {
      kind: "error",
      key: "forge.headline.error",
      params: { reason: dispatchReasonKeyOf(facts.error) },
      state: "ОШИБКА",
      actions: ["retry"],
    };
  }
  return {
    kind: "not_written",
    key: "forge.headline.not_written",
    params: {},
    state: "НЕ СОЗДАН",
    actions: [],
  };
};
