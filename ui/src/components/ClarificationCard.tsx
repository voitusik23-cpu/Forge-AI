// The questions card of Кузница.
//
// Every question gets a **typed control** chosen by its `field`, so the owner answers by clicking
// instead of writing a new prompt. The controls only produce the answer text; the shape that leaves
// this component is the one the discovery route already accepts (`{question_id, text}`), with the
// multi-select values joined by `"; "`.
//
// Three rules are carried by the rendering, not by a comment:
//
// * a question the backend marked `required: false` says «Необязательный вопрос», and when every
//   remaining question is optional the card says they do not block planning;
// * «Не знаю» and «Пропустить» are per question and mean different things: «Не знаю» is recorded as
//   an explicit unknown (the backend stores it as a skipped answer and stops asking), while
//   «Пропустить» simply is not sent in this round, so the question stays open;
// * at most `QUESTIONS_PER_PAGE` (5) questions are drawn at once; the rest are reached by «Далее».

import { useEffect, useMemo, useState } from "react";
import { Badge } from "./Primitives";
import { t, type Lang } from "../i18n";
import type { DiscoveryQuestion } from "../api/forge";

/** One question's answer as the owner is editing it. `text` is what will be sent. */
export type AnswerDraft = {
  text: string;
  /** «Не знаю»: sent as `{text: "", skipped: true}`. */
  unknown: boolean;
  /** «Пропустить»: not sent at all in this round. */
  skip: boolean;
};

export const emptyAnswerDraft = (): AnswerDraft => ({ text: "", unknown: false, skip: false });

/** A few questions per step, never a long interrogation. */
export const QUESTIONS_PER_PAGE = 5;

/** The separator the backend itself uses for a list answer (`_split_values`). */
const JOIN = "; ";

const OTHER_VALUE = "__other__";

type Control =
  | { kind: "select"; options: string[]; other?: boolean }
  | { kind: "radio"; options: string[]; other?: boolean }
  | { kind: "checkbox"; options: string[]; other?: boolean }
  | { kind: "chips"; options: string[]; other?: boolean }
  | { kind: "text" };

/** The options an expense tracker actually offers, shared by the two feature questions. */
const FEATURE_OPTIONS = [
  "добавление",
  "редактирование",
  "удаление",
  "месячный отчёт",
  "отчёт по категориям",
  "импорт",
  "экспорт",
];

/**
 * `field -> control`, exactly as the brief defines it.
 *
 * A field outside this table gets the generic chips control with a one-line «Другое» field, so an
 * area added to the backend later still renders as *something* answerable instead of a bare input
 * nobody expects. The option values are the product's own words and are sent verbatim.
 */
const CONTROLS: Record<string, Control> = {
  currency: { kind: "select", options: ["RUB", "USD", "EUR"], other: true },
  categories: { kind: "checkbox", options: FEATURE_OPTIONS },
  core_features: { kind: "checkbox", options: FEATURE_OPTIONS },
  auth: { kind: "radio", options: ["без авторизации", "локальный пароль", "вход по email"] },
  data_storage: { kind: "select", options: ["SQLite", "JSON-файл", "PostgreSQL", "не знаю"] },
  platform: { kind: "radio", options: ["локально", "публикация в интернет"] },
  deployment: { kind: "radio", options: ["локально", "публикация в интернет"] },
  constraints: { kind: "text" },
  project_type: {
    kind: "chips",
    options: ["веб-приложение", "Telegram-бот", "API", "дашборд", "CLI"],
    other: true,
  },
  users: { kind: "chips", options: ["один пользователь", "семья", "команда", "внешние клиенты"], other: true },
  integrations: { kind: "chips", options: ["email", "Telegram", "SMS", "оплата"], other: true },
  notifications: { kind: "chips", options: ["email", "Telegram", "push", "не нужны"], other: true },
  acceptance_criteria: {
    kind: "chips",
    options: ["всё работает локально", "отчёт формируется", "данные сохраняются"],
    other: true,
  },
  priorities: { kind: "chips", options: ["скорость", "функции", "качество"], other: true },
};

const controlFor = (field: string): Control => CONTROLS[field] ?? { kind: "chips", options: [], other: true };

/** The parts of an answer text, split the same way the discovery service splits it. */
const partsOf = (text: string): string[] =>
  text
    .split(JOIN)
    .map((part) => part.trim())
    .filter((part) => part !== "");

const chosenOptions = (text: string, options: string[]): string[] =>
  partsOf(text).filter((part) => options.includes(part));

const customText = (text: string, options: string[]): string =>
  partsOf(text)
    .filter((part) => !options.includes(part))
    .join(JOIN);

const joinAnswer = (chosen: string[], other: string): string => {
  const items = [...chosen];
  const custom = other.trim();
  if (custom !== "") items.push(custom);
  return items.join(JOIN);
};

const Chip = ({
  label,
  pressed,
  onClick,
}: {
  label: string;
  pressed: boolean;
  onClick: () => void;
}) => (
  <button type="button" className="forge-chip" aria-pressed={pressed} onClick={onClick}>
    {label}
  </button>
);

const OtherInput = ({
  lang,
  value,
  onChange,
}: {
  lang: Lang;
  value: string;
  onChange: (value: string) => void;
}) => (
  <input
    className="input forge-answer-other"
    type="text"
    value={value}
    placeholder={t(lang, "forge.discovery.question.other.placeholder")}
    aria-label={t(lang, "forge.discovery.question.other")}
    onChange={(event) => onChange(event.target.value)}
  />
);

/** One question: its text, the typed control, and the two explicit non-answers. */
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
  const field = String(question.field ?? "");
  const control = controlFor(field);
  const disabled = draft.unknown || draft.skip;
  const other = t(lang, "forge.discovery.question.other");

  const setText = (text: string) => onDraft({ text, unknown: false, skip: false });

  const chips = (options: string[], allowOther: boolean) => {
    const chosen = chosenOptions(draft.text, options);
    const rest = customText(draft.text, options);
    return (
      <>
        <div className="forge-answers">
          {options.map((option) => (
            <Chip
              key={option}
              label={option}
              pressed={chosen.includes(option)}
              onClick={() =>
                setText(
                  joinAnswer(
                    chosen.includes(option)
                      ? chosen.filter((item) => item !== option)
                      : [...chosen, option],
                    rest
                  )
                )
              }
            />
          ))}
        </div>
        {allowOther ? (
          <OtherInput
            lang={lang}
            value={rest}
            onChange={(value) => setText(joinAnswer(chosen, value))}
          />
        ) : null}
      </>
    );
  };

  const single = (options: string[], allowOther: boolean) => {
    const value = draft.text.trim();
    const isOther = value !== "" && !options.includes(value);
    return (
      <>
        <select
          className="input"
          value={isOther ? OTHER_VALUE : value}
          aria-label={String(question.text ?? "")}
          onChange={(event) => {
            const next = event.target.value;
            setText(next === OTHER_VALUE ? other : next);
          }}
        >
          <option value="">—</option>
          {options.map((option) => (
            <option key={option} value={option}>
              {option}
            </option>
          ))}
          {allowOther ? <option value={OTHER_VALUE}>{other}</option> : null}
        </select>
        {isOther ? (
          <OtherInput
            lang={lang}
            value={value === other ? "" : value}
            onChange={(next) => setText(next.trim() === "" ? other : next)}
          />
        ) : null}
      </>
    );
  };

  // A radio: exactly one option, so picking another replaces the value and picking
  // the chosen one clears the answer. Rendered as chips because the options are two
  // or three short words, but the behaviour is a single choice, not a set.
  const oneOf = (options: string[]) => {
    const value = draft.text.trim();
    return (
      <div className="forge-answers">
        {options.map((option) => (
          <Chip
            key={option}
            label={option}
            pressed={value === option}
            onClick={() => setText(value === option ? "" : option)}
          />
        ))}
      </div>
    );
  };

  return (
    <li className="forge-question">
      <div className="row between" style={{ gap: 10, flexWrap: "wrap" }}>
        <span style={{ fontWeight: 600 }}>{String(question.text ?? "")}</span>
        <Badge tone={question.required ? "warn" : "neutral"}>
          {question.required
            ? t(lang, "forge.discovery.question.required")
            : t(lang, "forge.discovery.question.optional")}
        </Badge>
      </div>
      <div className="small muted">
        {t(lang, "forge.discovery.question.why")} {String(question.why ?? "—")}
      </div>

      <div className={disabled ? "forge-answer-disabled" : ""}>
        {control.kind === "text" ? (
          <input
            className="input"
            type="text"
            value={draft.text}
            disabled={disabled}
            placeholder={t(lang, "forge.discovery.answer.placeholder")}
            aria-label={String(question.text ?? "")}
            onChange={(event) => setText(event.target.value)}
          />
        ) : control.kind === "select" ? (
          single(control.options, control.other === true)
        ) : control.kind === "radio" ? (
          oneOf(control.options)
        ) : (
          chips(control.options, control.other !== false)
        )}
      </div>

      <div className="forge-question-actions">
        <Chip
          label={t(lang, "forge.discovery.question.unknown")}
          pressed={draft.unknown}
          onClick={() => onDraft({ text: "", unknown: !draft.unknown, skip: false })}
        />
        <Chip
          label={t(lang, "forge.discovery.question.skip")}
          pressed={draft.skip}
          onClick={() => onDraft({ text: "", unknown: false, skip: !draft.skip })}
        />
        {draft.unknown ? (
          <span className="small muted">{t(lang, "forge.discovery.question.unknown.note")}</span>
        ) : null}
        {draft.skip ? (
          <span className="small muted">{t(lang, "forge.discovery.question.skip.note")}</span>
        ) : null}
      </div>
    </li>
  );
};

/**
 * The whole questions card: at most five typed questions, a pager, and the round's buttons.
 *
 * `onSend` receives only the answers worth sending; the parent builds the `{question_id, text}`
 * list from `drafts`, this component only decides what belongs on screen.
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
