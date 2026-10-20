// Live progress of a run, derived only from what the backend actually persisted.
//
// The run record (`GET /api/runs/{run_id}`) carries `event_count`, `state`, `status`,
// `attempt_number` and the read-time `classification`; it publishes no per-step document and there
// is no events route on the surface. The nine stages below are therefore marked from those facts
// and nothing else:
//
//   * a run with events proves the pipeline moved past acceptance;
//   * a terminal-success record proves every stage was passed (the run reached COMPLETED);
//   * a terminal record that is not a success never claims the last stage: the outcome exists, but
//     it is a failure, and «Готовим итог» is the successful stage this run did not reach;
//   * while the run is not terminal, one stage per four recorded events is marked, capped at
//     eight, so the final stage is never claimed before the record itself is terminal.
//
// No percentage is computed and none is shown: `event_count` is printed as the raw number it is,
// and a stage that is not proven is simply not marked. When the run is terminal the pulse stops
// and the terminal state is printed; with reduced motion the whole thing is a plain text list.

import { useEffect, useState } from "react";
import { Badge } from "./Primitives";
import { t, type Lang } from "../i18n";
import { isTerminalRecord, type RunRecord } from "../api/forge";

/** The nine stages, in pipeline order. Each label is a key in `i18n.ts`. */
export const RUN_STAGES = [
  "forge.progress.step1",
  "forge.progress.step2",
  "forge.progress.step3",
  "forge.progress.step4",
  "forge.progress.step5",
  "forge.progress.step6",
  "forge.progress.step7",
  "forge.progress.step8",
  "forge.progress.step9",
] as const;

export type RunProgressFacts = {
  /** How many stages the record proves. */
  reached: number;
  /** True when the record is terminal: the pulse stops and the terminal state is printed. */
  terminal: boolean;
  success: boolean;
  /** The raw number of events the record persisted. */
  events: number;
  state: string;
  outcome: string;
};

/** One stage per four persisted events while running; the last stage only on a terminal success. */
const EVENTS_PER_STAGE = 4;
const RUNNING_CAP = RUN_STAGES.length - 1;

export const runProgressOf = (record: RunRecord | null): RunProgressFacts => {
  if (!record) {
    return { reached: 0, terminal: false, success: false, events: 0, state: "", outcome: "" };
  }
  const events = typeof record.event_count === "number" && Number.isFinite(record.event_count) ? record.event_count : 0;
  const terminal = isTerminalRecord(record);
  const success = record.classification?.is_success === true;
  const proven = Math.min(RUNNING_CAP, Math.max(0, Math.floor(events / EVENTS_PER_STAGE)));
  const reached = terminal ? (success ? RUN_STAGES.length : proven) : proven;
  return {
    reached,
    terminal,
    success,
    events,
    state: String(record.state ?? record.status ?? ""),
    outcome: String(record.classification?.outcome ?? ""),
  };
};

/** `prefers-reduced-motion`, read once and kept in step with the OS setting. */
const useReducedMotion = (): boolean => {
  const [reduced, setReduced] = useState(false);
  useEffect(() => {
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") return;
    const query = window.matchMedia("(prefers-reduced-motion: reduce)");
    setReduced(query.matches);
    const listener = (event: MediaQueryListEvent) => setReduced(event.matches);
    query.addEventListener("change", listener);
    return () => query.removeEventListener("change", listener);
  }, []);
  return reduced;
};

export const RunProgress = ({ lang, record }: { lang: Lang; record: RunRecord | null }) => {
  const reduced = useReducedMotion();
  const facts = runProgressOf(record);
  const tone = facts.terminal ? (facts.success ? "ok" : "warn") : "info";

  return (
    <div className="run-progress">
      <div className="row between" style={{ gap: 10, flexWrap: "wrap" }}>
        <div className="label">{t(lang, "forge.progress.title")}</div>
        <span className="row" style={{ gap: 6, flexWrap: "wrap" }}>
          <Badge tone="neutral">
            {t(lang, "forge.progress.events")}: {facts.events}
          </Badge>
          <Badge tone={tone}>
            {facts.terminal
              ? `${t(lang, "forge.progress.terminal")}: ${facts.state || facts.outcome || t(lang, "runs.unknown")}`
              : t(lang, "forge.progress.running")}
          </Badge>
        </span>
      </div>

      <ol className={`run-stages${reduced ? " plain" : ""}`}>
        {RUN_STAGES.map((key, index) => {
          const done = index < facts.reached;
          const current = !facts.terminal && index === facts.reached;
          const state = done ? "done" : current ? "current" : "pending";
          return (
            <li className={`run-stage ${state}`} key={key}>
              <span className="run-stage-mark" aria-hidden="true">
                {done ? "✓" : current ? "•" : "·"}
              </span>
              <span>{t(lang, key)}</span>
              <span className="small muted">
                {done
                  ? `· ${t(lang, "forge.progress.done")}`
                  : current
                    ? `· ${t(lang, "forge.progress.current")}`
                    : `· ${t(lang, "forge.progress.pending")}`}
              </span>
            </li>
          );
        })}
      </ol>

      <div className="small muted">{t(lang, "forge.progress.note")}</div>
      {facts.terminal && !facts.success ? (
        <div className="small" style={{ color: "var(--warn-text)" }}>
          {t(lang, "forge.progress.failed")}
        </div>
      ) : null}
    </div>
  );
};
