// Запуски — the read-only run history.
//
// There is exactly ONE place in the application that dispatches a task: the task card on
// «Кузница» (POST /api/tasks/run). This screen used to carry a second copy of that form, which
// meant two submit buttons for one operation and two independent in-flight guards. It is now the
// read side only, and it reads the two routes that exist:
//
//   GET /api/product/runs   -> the recorded runs of the active project (the list below)
//   GET /api/runs/{run_id}  -> one persisted run record plus its classification (the details)
//
// Nothing here dispatches, retries, or resumes a run: opening a row is a read.

import { useState } from "react";
import { Badge, Button, Card, CardHead, StatusDot } from "../components/Primitives";
import { IconPlay, IconRefresh } from "../components/Icons";
import { t, type Lang } from "../i18n";
import { loadRunRecord, type Failure, type RunRecord } from "../api/forge";
import type { BackendState, RunRow } from "../api/client";

const text = (value: unknown, fallback = "—"): string =>
  value === undefined || value === null || value === "" ? fallback : String(value);

const yesNo = (lang: Lang, value: unknown): string => {
  if (typeof value !== "boolean") return t(lang, "runs.unknown");
  return value ? (lang === "ru" ? "да" : "yes") : (lang === "ru" ? "нет" : "no");
};

/** One run record: what the backend answered for GET /api/runs/{run_id}, or why it did not. */
type Detail =
  | { status: "idle" }
  | { status: "loading"; runId: string }
  | { status: "ready"; runId: string; record: RunRecord }
  | { status: "failed"; runId: string; failure: Failure };

const Facts = ({ items }: { items: Array<[string, string]> }) => (
  <div className="forge-facts">
    {items.map(([label, value]) => (
      <div className="forge-fact" key={label}>
        <div className="label">{label}</div>
        <div className="mono small" style={{ wordBreak: "break-word" }}>
          {value}
        </div>
      </div>
    ))}
  </div>
);

export const Runs = ({
  lang,
  backend,
  onRefresh,
  onHelp,
  onOpenForge,
}: {
  lang: Lang;
  backend: BackendState;
  onRefresh: () => void;
  onHelp: () => void;
  onOpenForge: () => void;
}) => {
  const live = backend.status === "live";
  const history: RunRow[] = live && Array.isArray(backend.runs.runs) ? backend.runs.runs : [];
  const recorded = live && typeof backend.runs.total_recorded === "number" ? backend.runs.total_recorded : history.length;
  const truncated = live && backend.runs.truncated === true;
  const [detail, setDetail] = useState<Detail>({ status: "idle" });

  const open = async (runId: string) => {
    setDetail({ status: "loading", runId });
    const outcome = await loadRunRecord(runId);
    if (outcome.ok) setDetail({ status: "ready", runId, record: outcome.value });
    else setDetail({ status: "failed", runId, failure: outcome.failure });
  };

  const selected = detail.status === "ready" ? detail.record : null;
  const selectedId = detail.status === "idle" ? "" : detail.runId;
  const classification = selected?.classification ?? null;

  return (
    <div className="content">
      <div className="row between" style={{ marginBottom: 18 }}>
        <div>
          <h1 style={{ margin: 0, fontSize: "var(--t-title)" }}>{t(lang, "nav.runs")}</h1>
          <div className="small muted" style={{ marginTop: 4 }}>
            {t(lang, "runs.subtitle")}
          </div>
        </div>
        <div className="row">
          <Button size="small" tone="ghost" onClick={onHelp}>
            {t(lang, "help.how")}
          </Button>
          <Button onClick={onRefresh}>
            <IconRefresh size={15} /> {t(lang, "action.refresh")}
          </Button>
        </div>
      </div>

      {!live ? (
        <div className="notice warn" style={{ marginBottom: 16 }}>
          {t(lang, "state.offline")}
        </div>
      ) : null}

      {/* The single dispatch point is named, linked, and never duplicated here. */}
      <div className="notice" style={{ marginBottom: 16 }}>
        <div className="row between" style={{ gap: 12, flexWrap: "wrap" }}>
          <span>{t(lang, "runs.readonly.note")}</span>
          <Button size="small" onClick={onOpenForge}>
            <IconPlay size={14} /> {t(lang, "runs.open.forge")}
          </Button>
        </div>
      </div>

      <div className="grid overview">
        <div className="stack">
          <Card className="rise">
            <CardHead
              title={t(lang, "runs.history.title")}
              hint="/api/product/runs"
              action={
                <Badge tone={live ? "info" : "neutral"}>
                  {live ? `${history.length} / ${recorded}` : "OFFLINE"}
                </Badge>
              }
            />
            <div className="small muted" style={{ marginBottom: 10 }}>
              {t(lang, "runs.history.note")}
            </div>
            {history.length === 0 ? (
              <div className="small muted">{t(lang, "runs.history.empty")}</div>
            ) : (
              <table className="table">
                <thead>
                  <tr>
                    <th>{t(lang, "runs.col.id")}</th>
                    <th>{t(lang, "runs.col.state")}</th>
                    <th>{t(lang, "runs.col.updated")}</th>
                    <th>{t(lang, "runs.col.events")}</th>
                  </tr>
                </thead>
                <tbody>
                  {history.map((run, index) => {
                    const runId = String(run.run_id ?? run.id ?? "");
                    const success = run.classification?.is_success === true;
                    return (
                      <tr
                        key={`${runId}-${index}`}
                        className={runId !== "" && runId === selectedId ? "selected" : ""}
                        onClick={() => (runId === "" ? undefined : void open(runId))}
                        style={{ cursor: runId === "" ? "default" : "pointer" }}
                      >
                        <td className="mono small">{text(runId)}</td>
                        <td>
                          <span className="row" style={{ gap: 8 }}>
                            <StatusDot tone={success ? "ok" : run.state === "FAILED" ? "danger" : "idle"} />
                            <span className="small">{text(run.state ?? run.status)}</span>
                            {run.classification?.outcome ? (
                              <span className="small muted">{String(run.classification.outcome)}</span>
                            ) : null}
                          </span>
                        </td>
                        <td className="mono small">{text(run.updated_at ?? run.created_at)}</td>
                        <td className="mono small">{text(run.event_count)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )}
            {truncated ? (
              <div className="small muted" style={{ marginTop: 10 }}>
                {t(lang, "runs.history.truncated")}
              </div>
            ) : null}
            <div className="small muted" style={{ marginTop: 10 }}>
              {t(lang, "runs.detail.hint")}
            </div>
          </Card>

          <Card className="rise d1">
            <CardHead
              title={t(lang, "runs.detail.title")}
              hint={selectedId === "" ? "/api/runs/{run_id}" : `/api/runs/${selectedId}`}
              action={
                selected ? (
                  <Badge tone={classification?.is_success === true ? "ok" : "warn"}>
                    {text(selected.state, t(lang, "runs.unknown"))}
                  </Badge>
                ) : null
              }
            />
            {detail.status === "idle" ? (
              <div className="small muted">{t(lang, "runs.selected.none")}</div>
            ) : detail.status === "loading" ? (
              <div className="small muted">{t(lang, "forge.execution.waiting")}</div>
            ) : detail.status === "failed" ? (
              <div className="notice danger">
                <div>
                  {detail.failure.kind === "unauthorized"
                    ? t(lang, "forge.state.unauthorized")
                    : detail.failure.kind === "disconnected"
                      ? t(lang, "forge.state.disconnected")
                      : `${t(lang, "forge.state.error")}: ${detail.failure.path} → HTTP ${detail.failure.status}`}
                </div>
                {detail.failure.detail ? (
                  <div className="small mono" style={{ marginTop: 6, whiteSpace: "pre-wrap" }}>
                    {detail.failure.detail}
                  </div>
                ) : null}
              </div>
            ) : (
              <Facts
                items={[
                  [t(lang, "forge.execution.state"), text(selected?.state)],
                  [t(lang, "forge.execution.status"), text(selected?.status)],
                  [t(lang, "forge.execution.events"), text(selected?.event_count)],
                  [t(lang, "forge.execution.sequence"), text(selected?.last_sequence_number)],
                  [t(lang, "forge.execution.attempt"), text(selected?.attempt_number)],
                  [t(lang, "forge.execution.interrupted"), yesNo(lang, selected?.interrupted)],
                  [t(lang, "forge.execution.updated"), text(selected?.updated_at)],
                  [t(lang, "forge.execution.classification"), text(classification?.outcome)],
                  [t(lang, "forge.execution.reason"), text(classification?.reason)],
                  [t(lang, "forge.execution.detail"), text(classification?.detail)],
                ]}
              />
            )}
          </Card>
        </div>

        <div className="stack">
          <Card className="rise d1">
            <CardHead
              title={t(lang, "runs.result.title")}
              hint="/api/runs/{run_id} · classification"
              action={
                classification ? (
                  <Badge tone={classification.is_success === true ? "ok" : "warn"}>
                    {text(classification.outcome, t(lang, "runs.unknown"))}
                  </Badge>
                ) : null
              }
            />
            {classification === null ? (
              <div className="small muted">{t(lang, "runs.selected.none")}</div>
            ) : (
              <div className="stack" style={{ gap: 12 }}>
                <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
                  <Badge tone={classification.is_terminal === true ? "ok" : "neutral"}>
                    {t(lang, "runs.result.terminal")}: {yesNo(lang, classification.is_terminal)}
                  </Badge>
                  <Badge tone={classification.is_success === true ? "ok" : "warn"}>
                    {t(lang, "runs.result.success")}: {yesNo(lang, classification.is_success)}
                  </Badge>
                </div>
                <div>
                  <div className="label">{t(lang, "runs.result.output")}</div>
                  <pre className="forge-output mono small">{text(classification.detail, "")}</pre>
                </div>
                {classification.reason ? (
                  <div className="small muted">
                    {t(lang, "forge.execution.reason")}: {String(classification.reason)}
                  </div>
                ) : null}
                {classification.evidence && classification.evidence.length > 0 ? (
                  <div>
                    <div className="label">{t(lang, "forge.execution.evidence")}</div>
                    <div className="row" style={{ gap: 6, flexWrap: "wrap", marginTop: 6 }}>
                      {classification.evidence.map((item) => (
                        <Badge key={item} tone="neutral">
                          {item}
                        </Badge>
                      ))}
                    </div>
                  </div>
                ) : null}
                <div className="small muted">{t(lang, "runs.result.source")}</div>
              </div>
            )}
          </Card>

          <Card className="rise d2" tight>
            <CardHead title={t(lang, "runs.routes.title")} hint="/api/product/runs · /api/runs/{run_id}" />
            <div className="stack small" style={{ gap: 6 }}>
              <div className="row between">
                <span className="muted mono">POST /api/tasks/run</span>
                <span className="small muted">{t(lang, "runs.routes.dispatch")}</span>
              </div>
              <div className="row between">
                <span className="muted mono">GET /api/product/runs</span>
                <span className="small muted">{t(lang, "runs.routes.history")}</span>
              </div>
              <div className="row between">
                <span className="muted mono">GET /api/runs/{"{run_id}"}</span>
                <span className="small muted">{t(lang, "runs.routes.detail")}</span>
              </div>
              <div className="muted" style={{ marginTop: 6 }}>
                {t(lang, "runs.record.gap")}
              </div>
            </div>
          </Card>
        </div>
      </div>
    </div>
  );
};
