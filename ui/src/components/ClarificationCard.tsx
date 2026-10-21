// The questions card of Кузница.
//
// Every question is rendered from the **backend's own** `control` and `options`, so the Core
// decides what a question looks like and the client never has to guess from a field name: a
// `radio`/`select`/`yes_no` question offers exactly one answer, a `checkbox` offers several
// («можно выбрать несколько»), and a `text` question is free-form.
//
// Four rules are carried by the rendering, not by a comment:
//
// * a question the backend marked `required: false` says «необязательный», and when every
//   remaining question is optional the card says they do not block planning;
// * «Не знаю — выбери оптимальный вариант» is a real answer: the Core stores the recommended
//   option, and records that it was auto-selected. «Пропустить» is different — it is offered
//   only for an optional question, and a required one has to be answered;
// * «Свой вариант» is always at the bottom of the question, under the options;
// * the card says in one line what happens after the answers are sent, so nobody has to guess
//   whether the click started a run.
//
// At most `QUESTIONS_PER_PAGE` (5) questions are drawn at once; the rest are reached by «Далее».

import { useEffect, useMemo, useState } from "react";
import { Badge } from "./Primitives";
import { t, type Lang } from "../i18n";
import type { DiscoveryOption, DiscoveryQuestion } from "../api/forge";

/** One question's answer as the owner is editing it. */
export type AnswerDraft = {
  /** The option values the user clicked. */
  values: string[];
  /** The «Свой вариант» text, sent beside the clicked values. */
  custom: string;
  /** «Не знаю — выбери оптимальный вариант»: sent as `{unknown: true}`. */
  unknown: boolean;
  /** «Пропустить»: sent as `{skipped: true}`, and only for an optional question. */
  skip: boolean;
};

export const emptyAnswerDraft = (): AnswerDraft => ({
  values: [],
  custom: "",
  unknown: false,
  skip: false,
});

/** Whether a draft carries something worth sending. */
export const draftHasAnswer = (draft: AnswerDraft): boolean =>
  draft.unknown || draft.values.length > 0 || draft.custom.trim() !== "";

/** A few questions per step, never a long interrogation. */
export const QUESTIONS_PER_PAGE = 5;

/** Options used when an older backend sends a question with no `options` field at all. */
const FALLBACK_OPTIONS: Record<string, Array<{ value: string; label: string }>> = {
  project_type: [
    { value: "веб-приложение", label: "веб-приложение" },
    { value: "приложение для Windows", label: "приложение для Windows" },
    { value: "Telegram-бот", label: "Telegram-бот" },
    { value: "мобильное приложение", label: "мобильное приложение" },
    { value: "API-сервис", label: "API-сервис" },
    { value: "CLI", label: "CLI" },
  ],
};

const FALLBACK_CONTROLS: Record<string, string> = {
  project_type: "radio",
  users: "radio",
  platform: "radio",
  data_storage: "select",
  core_features: "checkbox",
  priorities: "radio",
  auth: "yes_no",
  notifications: "yes_no",
  integrations: "yes_no",
};

const controlOf = (question: DiscoveryQuestion): string =>
  String(question.control ?? "") ||
  FALLBACK_CONTROLS[String(question.field ?? "")] ||
  "text";

const optionsOf = (question: DiscoveryQuestion): DiscoveryOption[] => {
  const provided = Array.isArray(question.options) ? question.options : [];
  const usable = provided.filter(
    (option) => String(option.value ?? "").trim() !== "" && String(option.label ?? "").trim() !== ""
  );
  if (usable.length > 0) return usable;
  return (FALLBACK_OPTIONS[String(question.field ?? "")] ?? []).map((option) => ({ ...option }));
};

const optionValue = (option: DiscoveryOption): string => String(option.value ?? "");
const optionLabel = (option: DiscoveryOption): string =>
  String(option.label ?? "") || optionValue(option);

const Chip = ({
  label,
  pressed,
  disabled,
  onClick,
  role,
}: {
  label: string;
  pressed: boolean;
  disabled?: boolean;
  onClick: () => void;
  role?: "radio" | "checkbox";
}) => (
  <button
    type="button"
    className="forge-chip"
    aria-pressed={pressed}
    role={role}
    disabled={disabled}
    onClick={onClick}
  >
    {label}
  </button>
);

/** The «Свой вариант» field, always at the bottom of a question. */
const CustomInput = ({
  lang,
  value,
  onChange,
  disabled,
}: {
  lang: Lang;
  value: string;
  onChange: (value: string) => void;
  disabled: boolean;
}) => (
  <label className="stack" style={{ gap: 4 }}>
    <span className="small muted">{t(lang, "forge.discovery.question.custom")}</span>
    <input
      className="input forge-answer-other"
      type="text"
      value={value}
      disabled={disabled}
      placeholder={t(lang, "forge.discovery.question.other.placeholder")}
      aria-label={t(lang, "forge.discovery.question.custom")}
      onChange={(event) => onChange(event.target.value)}
    />
  </label>
);

/** One question: its text, the typed control, «Свой вариант», and the two explicit non-answers. */
const QuestionRow = ({
  lang,
  question,
  draft,
  onDraft,
}: {
  lang: Lang;
  question: DiscoveryQuestion;
  draft: AnswerDraft;
  onDraft: (draft: AnswerDraft) => void;
}) => {
  const control = controlOf(question);
  const options = optionsOf(question);
  const disabled = draft.unknown || draft.skip;
  const required = question.required !== false && question.optional !== true;
  const allowCustom = question.allow_custom !== false && control !== "yes_no";
  const allowUnknown = question.allow_unknown !== false;
  const multi = control === "checkbox";

  const setValues = (values: string[]) =>
    onDraft({ ...draft, values, custom: draft.custom, unknown: false, skip: false });

  const toggle = (value: string) => {
    if (multi) {
      setValues(
        draft.values.includes(value)
          ? draft.values.filter((item) => item !== value)
          : [...draft.values, value]
      );
      return;
    }
    setValues(draft.values.includes(value) ? [] : [value]);
  };

  const radio = () => (
    <div className="forge-answers" role="radiogroup" aria-label={String(question.text ?? "")}>
      {options.map((option) => (
        <Chip
          key={optionValue(option)}
          label={optionLabel(option)}
          pressed={draft.values.includes(optionValue(option))}
          disabled={disabled}
          role="radio"
          onClick={() => toggle(optionValue(option))}
        />
      ))}
    </div>
  );

  const checkboxes = () => (
    <>
      <div className="small muted">{t(lang, "forge.discovery.question.multi")}</div>
      <div className="forge-answers" role="group" aria-label={String(question.text ?? "")}>
        {options.map((option) => (
          <Chip
            key={optionValue(option)}
            label={optionLabel(option)}
            pressed={draft.values.includes(optionValue(option))}
            disabled={disabled}
            role="checkbox"
            onClick={() => toggle(optionValue(option))}
          />
        ))}
      </div>
    </>
  );

  const select = () => (
    <select
      className="input"
      value={draft.values[0] ?? ""}
      disabled={disabled}
      aria-label={String(question.text ?? "")}
      onChange={(event) => setValues(event.target.value === "" ? [] : [event.target.value])}
    >
      <option value="">—</option>
      {options.map((option) => (
        <option key={optionValue(option)} value={optionValue(option)}>
          {optionLabel(option)}
        </option>
      ))}
    </select>
  );

  // A yes/no question is a two-position switch: the two labels are the whole answer set, so
  // there is nothing to type and no «Свой вариант» to offer.
  const yesNo = () => (
    <div className="forge-answers forge-switch" role="radiogroup" aria-label={String(question.text ?? "")}>
      {options.slice(0, 2).map((option) => (
        <Chip
          key={optionValue(option)}
          label={optionLabel(option)}
          pressed={draft.values.includes(optionValue(option))}
          disabled={disabled}
          role="radio"
          onClick={() => toggle(optionValue(option))}
        />
      ))}
    </div>
  );

  return (
    <li className="forge-question">
      <div className="row between" style={{ gap: 10, flexWrap: "wrap" }}>
        <span style={{ fontWeight: 600 }}>{String(question.text ?? "")}</span>
        <Badge tone={required ? "warn" : "neutral"}>
          {required
            ? t(lang, "forge.discovery.question.required")
            : t(lang, "forge.discovery.question.optional")}
        </Badge>
      </div>
      <div className="small muted">
        {t(lang, "forge.discovery.question.why")} {String(question.why ?? "—")}
      </div>

      <div className={disabled ? "forge-answer-disabled" : ""}>
        {control === "text" ? (
          <input
            className="input"
            type="text"
            value={draft.custom}
            disabled={disabled}
            placeholder={t(lang, "forge.discovery.answer.placeholder")}
            aria-label={String(question.text ?? "")}
            onChange={(event) =>
              onDraft({ ...draft, custom: event.target.value, unknown: false, skip: false })
            }
          />
        ) : control === "checkbox" ? (
          checkboxes()
        ) : control === "select" ? (
          select()
        ) : control === "yes_no" ? (
          yesNo()
        ) : (
          radio()
        )}
        {control !== "text" && allowCustom ? (
          <CustomInput
            lang={lang}
            value={draft.custom}
            disabled={disabled}
            onChange={(value) => onDraft({ ...draft, custom: value, unknown: false, skip: false })}
          />
        ) : null}
      </div>

      <div className="forge-question-actions">
        {allowUnknown ? (
          <Chip
            label={t(lang, "forge.discovery.question.unknown")}
            pressed={draft.unknown}
            onClick={() =>
              onDraft({ values: [], custom: "", unknown: !draft.unknown, skip: false })
            }
          />
        ) : null}
        <Chip
          label={t(lang, "forge.discovery.question.skip")}
          pressed={draft.skip}
          disabled={required}
          onClick={() =>
            onDraft({ values: [], custom: "", unknown: false, skip: !draft.skip })
          }
        />
        {draft.unknown ? (
          <span className="small muted">{t(lang, "forge.discovery.question.unknown.note")}</span>
        ) : null}
        {draft.skip ? (
          <span className="small muted">{t(lang, "forge.discovery.question.skip.note")}</span>
        ) : null}
        {required ? (
          <span className="small muted">{t(lang, "forge.discovery.question.required.note")}</span>
        ) : null}
      </div>
    </li>
  );
};

/**
 * The whole questions card: at most five typed questions, a pager, and the round's buttons.
 *
 * `onSend` receives nothing: the parent builds the answer list from `drafts`, because it is the
 * parent that knows which question ids this round actually asked.
 */
export const ClarificationCard = ({
  lang,
  questions,
  drafts,
  onDraft,
  busy,
  onSend,
  onSkipRound,
  onCancel,
}: {
  lang: Lang;
  questions: DiscoveryQuestion[];
  drafts: Record<string, AnswerDraft>;
  onDraft: (questionId: string, draft: AnswerDraft) => void;
  busy: string;
  onSend: () => void;
  onSkipRound: () => void;
  onCancel: () => void;
}) => {
  const [page, setPage] = useState(0);
  // A new round of questions starts on the first page, never on a page that no longer exists.
  const signature = useMemo(
    () => questions.map((question) => String(question.id ?? "")).join("|"),
    [questions]
  );
  useEffect(() => {
    setPage(0);
  }, [signature]);

  const count = questions.length;
  const pages = Math.max(1, Math.ceil(count / QUESTIONS_PER_PAGE));
  const current = Math.min(Math.max(0, page), pages - 1);
  const from = current * QUESTIONS_PER_PAGE;
  const visible = questions.slice(from, from + QUESTIONS_PER_PAGE);
  const allOptional = count > 0 && questions.every((question) => question.required !== true);

  return (
    <div className="stack" style={{ gap: 12 }}>
      <div className="row between" style={{ gap: 10, flexWrap: "wrap" }}>
        <div className="label">
          {t(lang, "forge.discovery.questions.title").replace("{n}", String(count))}
        </div>
        {count > 0 ? (
          <span className="small muted">
            {t(lang, "forge.discovery.questions.page")
              .replace("{from}", String(from + 1))
              .replace("{to}", String(from + visible.length))
              .replace("{n}", String(count))}
          </span>
        ) : null}
      </div>
      <div className="small muted">{t(lang, "forge.discovery.questions.hint")}</div>
      {allOptional ? (
        <div className="notice">{t(lang, "forge.discovery.questions.optional.free")}</div>
      ) : null}
      {/* One line, once: what a click here actually does, and what it does not do. */}
      <div className="small muted">{t(lang, "forge.discovery.question.after")}</div>

      {count === 0 ? (
        <div className="small muted">{t(lang, "forge.discovery.questions.empty")}</div>
      ) : (
        <ol className="forge-questions">
          {visible.map((question) => {
            const id = String(question.id ?? "");
            return (
              <QuestionRow
                key={id}
                lang={lang}
                question={question}
                draft={drafts[id] ?? emptyAnswerDraft()}
                onDraft={(draft) => onDraft(id, draft)}
              />
            );
          })}
        </ol>
      )}

      {pages > 1 ? (
        <div className="row forge-pager" style={{ gap: 10, flexWrap: "wrap", alignItems: "center" }}>
          <button
            type="button"
            className="btn small"
            disabled={current === 0}
            onClick={() => setPage(current - 1)}
          >
            {t(lang, "forge.discovery.questions.prev")}
          </button>
          <span className="small muted">
            {current + 1} / {pages}
          </span>
          <button
            type="button"
            className="btn small"
            disabled={current >= pages - 1}
            onClick={() => setPage(current + 1)}
          >
            {t(lang, "forge.discovery.questions.next")}
          </button>
        </div>
      ) : null}

      <div className="row" style={{ gap: 12, flexWrap: "wrap", alignItems: "center" }}>
        <button
          type="button"
          className="btn primary"
          disabled={busy === "answers"}
          onClick={onSend}
        >
          {busy === "answers" ? t(lang, "runs.form.running") : t(lang, "forge.discovery.send")}
        </button>
        <button type="button" className="btn" disabled={busy === "skip"} onClick={onSkipRound}>
          {t(lang, "forge.discovery.skip")}
        </button>
        <button
          type="button"
          className="btn ghost"
          disabled={busy === "cancel"}
          onClick={onCancel}
        >
          {t(lang, "forge.discovery.cancel")}
        </button>
      </div>
    </div>
  );
};
