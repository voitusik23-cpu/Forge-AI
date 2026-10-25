// THE ALIVE BAR — «Работаю · 00:37 · всё идёт по плану · этап: пишу файлы».
//
// The owner's complaint was that a run in progress was indistinguishable from a hang: the step
// rail and the change block both said *something*, but nothing on screen moved. This bar is that
// missing signal, and it is deliberately small:
//
//   * the **clock** ticks once per second from the press that started the run and stops the
//     moment the record is terminal — there is no total and no ETA, because the backend
//     publishes neither (see `ui/src/progress.ts`);
//   * the **ribbon** is an indeterminate CSS animation: it is `true` only while the run is really
//     in flight, so a still ribbon always means "finished", never "probably still working";
//   * the **line** is a `aria-live="polite"` region, so the stage is announced when it changes.
//     While running the clock is `aria-hidden` — a screen reader must not read a number every
//     second — and on the terminal line it is part of the sentence;
//   * the **hint** «Можно свернуть окно — работа продолжится» is true: the run is server-side.
//
// Reduced motion is handled in CSS (`prefers-reduced-motion` and the existing
// `[data-motion="reduced"]` switch): the ribbon becomes a still bar and the text stays.

import { useEffect, useRef, useState } from "react";
import { t, type Lang } from "../i18n";
import { aliveState } from "../progress";

/** Split one translated template around its `{clock}` placeholder. */
const splitClock = (template: string): [string, string] => {
  const marker = "{clock}";
  const index = template.indexOf(marker);
  if (index < 0) return [template, ""];
  return [template.slice(0, index), template.slice(index + marker.length)];
};

export const AliveBar = ({
  lang,
  inFlight,
  terminal,
  success,
  observedAt,
  endedAt,
  stage,
  testId = "forge-alive",
}: {
  lang: Lang;
  /** A dispatch is travelling or a run record is not terminal. */
  inFlight: boolean;
  /** The run record is terminal: the clock stops and the ribbon freezes. */
  terminal: boolean;
  /** The terminal record's own success verdict. */
  success: boolean;
  /** Epoch ms of the press that started the run; 0 when this window did not see it. */
  observedAt: number;
  /** Epoch ms from the run record's own `updated_at`; 0 when the record is not there yet. */
  endedAt: number;
  /** The stage word, already translated («пишу файлы…»). */
  stage: string;
  /** Prefix for the DOM test ids, so two bars on one page stay distinguishable. */
  testId?: string;
}) => {
  const running = inFlight === true;
  const [now, setNow] = useState(() => Date.now());

  // One tick per second, and only while something is in flight: a finished run costs no timer.
  useEffect(() => {
    if (!running) return;
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [running]);

  // The local end, frozen on the first terminal render. Without it a run whose record arrives
  // one poll late would keep counting after it finished, which is the one thing a clock about
  // "how long did it take" must never do.
  const frozen = useRef(0);
  if (terminal && frozen.current === 0) frozen.current = Date.now();
  if (!terminal) frozen.current = 0;

  const state = aliveState({
    inFlight,
    terminal,
    success,
    observedAt,
    endedAt,
    now: terminal ? frozen.current : now,
  });
  if (!state.visible) return null;

  const [before, after] = splitClock(
    t(lang, state.phase === "done" ? "forge.alive.done" : "forge.alive.failed")
  );

  return (
    <div
      className={`forge-alive ${state.phase}`}
      data-testid={testId}
      data-phase={state.phase}
      data-ribbon={state.ribbon ? "on" : "off"}
      data-seconds={String(state.seconds)}
    >
      {/* The indeterminate ribbon: no percentage, because there is no measured total. */}
      <div className="forge-alive-track" aria-hidden="true">
        <span className="forge-alive-ribbon" data-testid={`${testId}-ribbon`} />
      </div>

      {state.phase === "running" ? (
        <div className="forge-alive-line">
          <span className="forge-alive-status">{t(lang, "forge.alive.status.running")}</span>
          <span className="forge-alive-sep" aria-hidden="true">
            ·
          </span>
          <span
            className="mono forge-alive-clock"
            data-testid={`${testId}-clock`}
            aria-hidden="true"
          >
            {state.clock}
          </span>
          <span className="forge-alive-sep" aria-hidden="true">
            ·
          </span>
          {/* The live region carries the part that changes, so the clock is never read aloud. */}
          <span
            className="forge-alive-plan"
            role="status"
            aria-live="polite"
            aria-atomic="true"
            data-testid={`${testId}-live`}
          >
            {t(lang, "forge.alive.plan")}
            <span aria-hidden="true"> · </span>
            {t(lang, "forge.alive.stage").replace("{stage}", stage)}
          </span>
        </div>
      ) : (
        <div
          className="forge-alive-line"
          role="status"
          aria-live="polite"
          aria-atomic="true"
          data-testid={`${testId}-live`}
        >
          {before}
          <span className="mono forge-alive-clock" data-testid={`${testId}-clock`}>
            {state.clock}
          </span>
          {after}
        </div>
      )}

      {state.phase === "running" ? (
        <div className="small muted forge-alive-hint">{t(lang, "forge.alive.hint")}</div>
      ) : null}
    </div>
  );
};
