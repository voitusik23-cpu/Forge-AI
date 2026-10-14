import { useEffect, useState } from "react";
import { Badge, Button, Card, CardHead, Field, StatusDot } from "../components/Primitives";
import { IconPlay, IconRefresh } from "../components/Icons";
import { t, type Lang } from "../i18n";
import type { BackendState, Project, RunRow } from "../api/client";

const CATEGORIES: string[] = ["code", "analysis", "review", "coding", "reasoning", "large-context", "cheap/free", "fast/cheap", "other"];

const text = (value: unknown, fallback = "—"): string =>
  value === undefined || value === null || value === "" ? fallback : String(value);

const fixed = (value: unknown, digits: number): string =>
  typeof value === "number" && Number.isFinite(value) ? value.toFixed(digits) : "—";

type RunResult = Record<string, unknown>;

export const Runs = ({
  lang,
  backend,
  onRefresh,
  onHelp,
}: {
  lang: Lang;
  backend: BackendState;
  onRefresh: () => void;
  onHelp: () => void;
}) => {
  const live = backend.status === "live";
  const projects: Project[] = live && Array.isArray(backend.onboarding.projects) ? backend.onboarding.projects : [];
  const agents = live ? backend.agents : [];
  const productHistory: RunRow[] = live && Array.isArray(backend.runs.runs) ? backend.runs.runs : [];

  const [projectId, setProjectId] = useState("");
  const [category, setCategory] = useState(CATEGORIES[0] ?? "code");
  const [provider, setProvider] = useState("");
  const [description, setDescription] = useState("");
  const [inFlight, setInFlight] = useState(false);
  const [result, setResult] = useState<RunResult | null>(null);
  const [error, setError] = useState("");
  const [dispatched, setDispatched] = useState<RunResult[]>([]);
  const [record, setRecord] = useState<{ id: string; body: string } | null>(null);

  useEffect(() => {
    if (!projectId && projects.length > 0) setProjectId(String(projects[0].project_id ?? ""));
  }, [projects, projectId]);

  useEffect(() => {
    if (!provider && agents.length > 0) setProvider(String(agents[0].provider ?? ""));
  }, [agents, provider]);

  // POST /api/tasks/run has no idempotency key, so a second click must be stopped here:
  // the handler refuses re-entry and the button is disabled for the whole request.
  const submit = async () => {
    if (inFlight) return;
    if (!description.trim()) {
      setError(t(lang, "runs.error.description"));
      return;
    }
    setInFlight(true);
    setError("");
    setResult(null);
    const submitted = description.trim();
    try {
      const response = await fetch("/api/tasks/run", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          description: submitted,
          category,
          project_id: projectId || undefined,
          provider_name: provider || undefined,
        }),
      });
      const payload = (await response.json()) as RunResult;
      if (!response.ok) {
        setError(`${response.status}: ${text(payload.detail ?? payload.error, "")}`);
      } else {
        setResult(payload);
        setDispatched((previous) => [payload, ...previous].slice(0, 20));
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setInFlight(false);
      onRefresh();
    }
  };

  // The durable record: shown exactly as the backend answers, including a refusal.
  const loadRecord = async (runId: string) => {
    setRecord(null);
    try {
      const response = await fetch(`/api/runs/${encodeURIComponent(runId)}`);
      const body = await response.text();
      setRecord({
        id: runId,
        body:
          response.ok
            ? body
            : `${response.status} ${response.statusText}\n${body || t(lang, "runs.record.missing")}`,
      });
    } catch (caught) {
      setRecord({ id: runId, body: caught instanceof Error ? caught.message : String(caught) });
    }
  };

  return (
    <div className="content">
      <div className="row between" style={{ marginBottom: 18 }}>
        <div>
          <h1 style={{ margin: 0, fontSize: "var(--t-title)" }}>{t(lang, "nav.runs")}</h1>
          <div className="small muted" style={{ marginTop: 4 }}>{t(lang, "runs.subtitle")}</div>
        </div>
        <div className="row">
          <Button size="small" tone="ghost" onClick={onHelp}>{t(lang, "help.how")}</Button>
          <Button onClick={onRefresh}><IconRefresh size={15} /> {t(lang, "action.refresh")}</Button>
        </div>
      </div>

      <div className="grid overview">
        <div className="stack">
          <Card className="rise">
            <CardHead
              title={t(lang, "runs.form.title")}
              hint="POST /api/tasks/run"
              action={<Badge tone={live ? "info" : "warn"}>{live ? "LIVE" : "OFFLINE"}</Badge>}
            />
            <div className="stack" style={{ gap: 14 }}>
              <div className="row" style={{ gap: 12, flexWrap: "wrap" }}>
                <label className="field" style={{ flex: "1 1 200px" }}>
                  <span className="label">{t(lang, "runs.form.project")}</span>
                  <select className="input" value={projectId} onChange={(event) => setProjectId(event.target.value)}>
                    {projects.map((project) => (
                      <option key={String(project.project_id)} value={String(project.project_id)}>
                        {String(project.name ?? project.project_id)}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="field" style={{ flex: "0 1 160px" }}>
                  <span className="label">{t(lang, "runs.form.category")}</span>
                  <select className="input" value={category} onChange={(event) => setCategory(event.target.value)}>
                    {CATEGORIES.map((item) => (
                      <option key={item} value={item}>{item}</option>
                    ))}
                  </select>
                </label>
                <label className="field" style={{ flex: "0 1 190px" }}>
                  <span className="label">{t(lang, "runs.form.agent")}</span>
                  <select className="input" value={provider} onChange={(event) => setProvider(event.target.value)}>
                    {agents.map((agent) => (
                      <option key={String(agent.provider)} value={String(agent.provider)}>
                        {String(agent.name)} ({String(agent.provider)})
                      </option>
                    ))}
                  </select>
                </label>
              </div>

              <Field label={t(lang, "runs.form.description")} hint={t(lang, "runs.form.description.hint")}>
                <textarea
                  className="input"
                  rows={3}
                  value={description}
                  onChange={(event) => setDescription(event.target.value)}
                  placeholder={t(lang, "runs.form.description.placeholder")}
                />
              </Field>

              <div className="row" style={{ gap: 10 }}>
                <Button tone="primary" disabled={inFlight || !live} onClick={() => void submit()}>
                  <IconPlay size={15} /> {inFlight ? t(lang, "runs.form.running") : t(lang, "runs.form.submit")}
                </Button>
                <span className="small muted">{t(lang, "runs.form.idempotency")}</span>
              </div>

              {error ? <div className="notice danger">{error}</div> : null}
            </div>
          </Card>

          {result ? (
            <Card className="rise d1">
              <CardHead
                title={t(lang, "runs.result.title")}
                hint={String(result.run_id ?? "")}
                action={
                  <Badge tone={result.success === true ? "ok" : "warn"}>
                    {String(result.state ?? "UNKNOWN").toUpperCase()}
                  </Badge>
                }
              />
              <div className="grid cols-4" style={{ marginBottom: 12 }}>
                {[
                  [t(lang, "runs.result.tokens"), String(result.tokens ?? "—")],
                  [t(lang, "runs.result.cost"), fixed(result.cost ?? 0, 4)],
                  [t(lang, "runs.result.duration"), fixed(result.duration_seconds ?? 0, 2)],
                  [t(lang, "runs.result.provider"), text(result.provider_used)],
                ].map(([label, value]) => (
                  <div key={String(label)}>
                    <div className="label">{String(label)}</div>
                    <div className="mono">{String(value)}</div>
                  </div>
                ))}
              </div>
              <pre className="mono small" style={{ whiteSpace: "pre-wrap", margin: 0 }}>
                {text(result.output, "")}
              </pre>
              {result.error ? (
                <div className="notice danger" style={{ marginTop: 10 }}>{String(result.error)}</div>
              ) : null}
              <div className="small muted" style={{ marginTop: 8 }}>
                {t(lang, "runs.result.model")}: {text(result.model_used)} · task {text(result.task_id)} · project{" "}
                {text(result.project_id)}
              </div>
            </Card>
          ) : null}
        </div>

        <div className="stack">
          <Card className="rise d1">
            <CardHead
              title={t(lang, "runs.session.title")}
              hint={t(lang, "runs.session.hint")}
              action={<Badge tone="ok">{dispatched.length}</Badge>}
            />
            {dispatched.length === 0 ? (
              <div className="small muted">{t(lang, "runs.session.empty")}</div>
            ) : (
              <div className="stack small" style={{ gap: 6 }}>
                {dispatched.map((item, index) => (
                  <button
                    key={`${String(item.run_id)}-${index}`}
                    className="nav-item"
                    onClick={() => void loadRecord(String(item.run_id ?? ""))}
                  >
                    <StatusDot tone={item.success === true ? "ok" : "warn"} />
                    <span className="label mono">{String(item.run_id ?? "")}</span>
                    <span className="count">
                      {fixed(item.duration_seconds ?? 0, 2)}s · ${fixed(item.cost ?? 0, 4)}
                    </span>
                  </button>
                ))}
              </div>
            )}
          </Card>

          <Card className="rise d2">
            <CardHead
              title={t(lang, "runs.history.title")}
              hint="/api/product/runs"
              action={<Badge tone="neutral">PIPELINE</Badge>}
            />
            <div className="small muted" style={{ marginBottom: 8 }}>{t(lang, "runs.history.note")}</div>
            {productHistory.length === 0 ? (
              <div className="small muted">{t(lang, "runs.history.empty")}</div>
            ) : (
              <div className="stack small" style={{ gap: 6 }}>
                {productHistory.slice(0, 10).map((run, index) => (
                  <div className="row between" key={String(run.run_id ?? index)}>
                    <span className="mono small">{String(run.run_id ?? "—")}</span>
                    <span className="small muted">{String(run.status ?? "—")}</span>
                  </div>
                ))}
              </div>
            )}
          </Card>

          {record ? (
            <Card className="rise d3">
              <CardHead title={t(lang, "runs.detail.title")} hint={record.id} />
              <pre className="mono small" style={{ whiteSpace: "pre-wrap", margin: 0, maxHeight: 240, overflow: "auto" }}>
                {record.body}
              </pre>
            </Card>
          ) : (
            <Card className="rise d3" tight>
              <div className="small muted">{t(lang, "runs.record.gap")}</div>
            </Card>
          )}
        </div>
      </div>
    </div>
  );
};
