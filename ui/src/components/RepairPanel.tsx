// THE REPAIR PANEL — what did not start, why, and the two ways to fix it, in one place.
//
// The owner's requirement: «если проблемы — говорит об этом сразу под строкой "Запустить
// приложение" и предлагает решить… и я там мог бы с ним переписываться». So this component is
// rendered **immediately under the launch button** and it is the *only* place a failed launch
// is explained: one plain sentence read from the log, the last redacted lines, the folded full
// log, «Исправить автоматически», and a one-line chat that dispatches a change.
//
// It owns no logic of its own. The cause, the sentence, the visible lines and the attempt cap
// come from `ui/src/repair.ts` (executed by `tests/test_self_repair.py`); the two actions are
// callbacks the project screen supplies, and the correction flight itself — the status strip
// and the live bar — is passed in as `children`, because it is the *same* element the
// «Что поправить?» block renders. One flow, one set of words, two places to reach it.

import type { ReactNode } from "react";
import { t, type Lang } from "../i18n";
import { Button } from "./Primitives";
import {
  REPAIR_ATTEMPT_LIMIT,
  repairPanelModel,
  type LaunchFacts,
  type RepairState,
} from "../repair";

/** Fill `{name}` placeholders. A missing parameter stays visible instead of vanishing. */
const fill = (template: string, params: Record<string, string>): string =>
  template.replace(/\{(\w+)\}/g, (whole, name: string) =>
    name in params ? String(params[name]) : whole
  );

export const RepairPanel = ({
  lang,
  facts,
  attempts,
  state,
  detail,
  busy,
  chat,
  mockWarning,
  fallbackAvailable,
  onChat,
  onSend,
  onAutoRepair,
  onFallback,
  children,
}: {
  lang: Lang;
  /** The refusal's structured fields, as the backend published them. */
  facts: LaunchFacts;
  /** Auto-repair attempts already spent on this project in this session. */
  attempts: number;
  /** The outcome of the last repair press, or null before the first one. */
  state: RepairState | null;
  /** The backend's own sentence for the refusal, kept verbatim inside the fold. */
  detail: string;
  /** A change run is in flight (or the launch request is): both actions stay off. */
  busy: boolean;
  /** The one-line chat input. */
  chat: string;
  /** The effective provider is the offline engine: it cannot change a single file. */
  mockWarning: boolean;
  /** The saved static page is offered as a separate action, exactly as before. */
  fallbackAvailable: boolean;
  onChat: (value: string) => void;
  onSend: () => void;
  onAutoRepair: () => void;
  onFallback: () => void;
  /** The shared change strip + alive bar (the same element the change block renders). */
  children?: ReactNode;
}) => {
  const model = repairPanelModel(facts, attempts);
  const clause = fill(t(lang, model.sentenceKey), model.params);
  const headline = fill(t(lang, model.headlineKey), { ...model.params, cause: clause });
  const capped = attempts >= REPAIR_ATTEMPT_LIMIT || state?.phase === "capped";
  const message =
    state !== null && state.messageKey !== "" && state.phase !== "opened"
      ? fill(t(lang, state.messageKey), { ...model.params, ...state.messageParams, cause: clause })
      : "";
  const empty = chat.trim() === "";
  return (
    <div className="notice warn" data-testid="repair-panel" data-attempts={String(attempts)}>
      {/* ONE PLAIN SENTENCE, read from the log. Never a raw traceback, never «ошибка». */}
      <div data-testid="repair-headline" style={{ whiteSpace: "pre-wrap" }}>
        {headline}
      </div>
      {/* The last redacted lines stay visible: the owner should not have to open anything to
          see the machine's own words. The whole tail is one click away. */}
      {model.visibleLines.length > 0 ? (
        <div style={{ marginTop: 8 }}>
          <div className="label">{t(lang, "projects.repair.log.last")}</div>
          <pre
            className="small mono"
            data-testid="repair-log-last"
            style={{ whiteSpace: "pre-wrap", wordBreak: "break-word", margin: "4px 0 0" }}
          >
            {model.visibleLines.join("\n")}
          </pre>
        </div>
      ) : null}
      <details className="tech-details" data-testid="repair-log">
        <summary>{t(lang, "projects.repair.log")}</summary>
        <pre
          className="small mono"
          data-testid="repair-log-full"
          style={{ whiteSpace: "pre-wrap", wordBreak: "break-word", margin: "6px 0 0" }}
        >
          {model.logLines.join("\n")}
        </pre>
        {model.logPath !== "" ? (
          <div className="small mono muted" data-testid="repair-log-path" style={{ marginTop: 6 }}>
            {model.logPath}
          </div>
        ) : null}
        {detail !== "" ? (
          <div
            className="small mono"
            data-testid="repair-log-detail"
            style={{ marginTop: 6, whiteSpace: "pre-wrap", wordBreak: "break-word" }}
          >
            {detail}
          </div>
        ) : null}
      </details>
      {mockWarning ? (
        <div className="small" data-testid="repair-mock-warning" style={{ marginTop: 8 }}>
          {t(lang, "forge.mock.warning")}
        </div>
      ) : null}
      {/* THE TWO WAYS TO FIX IT: one deterministic repair, and the owner's own sentence. */}
      <div className="row" style={{ gap: 8, flexWrap: "wrap", marginTop: 10 }}>
        <span data-testid="repair-auto">
          <Button
            size="small"
            tone="primary"
            disabled={busy || !model.canAutoRepair}
            disabledReason={
              !model.canAutoRepair ? t(lang, "projects.repair.capped") : undefined
            }
            onClick={onAutoRepair}
          >
            {busy ? t(lang, "projects.repair.working") : t(lang, "projects.repair.auto")}
          </Button>
        </span>
        <input
          className="input"
          type="text"
          data-testid="repair-chat-input"
          style={{ flex: "1 1 240px" }}
          value={chat}
          aria-label={t(lang, "projects.repair.chat.label")}
          placeholder={t(lang, "projects.repair.chat.placeholder")}
          disabled={busy}
          onChange={(event) => onChat(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !busy && !empty) onSend();
          }}
        />
        <span data-testid="repair-chat-send">
          <Button
            size="small"
            disabled={busy || empty}
            disabledReason={empty ? t(lang, "projects.repair.chat.empty") : undefined}
            onClick={onSend}
          >
            {t(lang, "projects.repair.chat.send")}
          </Button>
        </span>
      </div>
      <div className="small muted" style={{ marginTop: 6 }} data-testid="repair-hint">
        {t(lang, "projects.repair.hint")}
      </div>
      {attempts > 0 ? (
        <div className="small muted" data-testid="repair-attempts" style={{ marginTop: 4 }}>
          {fill(t(lang, "projects.repair.attempts"), {
            n: String(attempts),
            limit: String(REPAIR_ATTEMPT_LIMIT),
          })}
        </div>
      ) : null}
      {message !== "" ? (
        <div
          className="small"
          data-testid="repair-message"
          role="status"
          aria-live="polite"
          style={{ marginTop: 8, whiteSpace: "pre-wrap" }}
        >
          {message}
        </div>
      ) : null}
      {capped && state?.phase !== "capped" ? (
        // The loop's own answer already says this after the press that spent the last
        // attempt; it is printed here for the case where the cap is known from the spent
        // attempts alone -- e.g. the last run wrote no file, so there is no loop message.
        <div className="small" data-testid="repair-capped" style={{ marginTop: 6 }}>
          {t(lang, "projects.repair.capped")}
        </div>
      ) : null}
      {fallbackAvailable ? (
        <div className="row" style={{ gap: 8, flexWrap: "wrap", marginTop: 10 }}>
          <span data-testid="project-launch-fallback">
            <Button size="small" disabled={busy} onClick={onFallback}>
              {t(lang, "projects.launch.fallback.action")}
            </Button>
          </span>
          <span className="small muted">{t(lang, "projects.launch.fallback.hint")}</span>
        </div>
      ) : null}
      {/* THE SAME FLIGHT THE CHANGE BLOCK SHOWS: strip, outcome, live bar. */}
      {children}
    </div>
  );
};
