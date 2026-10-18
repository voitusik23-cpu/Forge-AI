// Кузница — the FORGE AI desktop workspace.
//
// One screen, six real answers from the Core, and nothing else:
//
//   projects  <- GET  /api/projects            (loaded here, because loadBackend does not)
//   providers <- GET  /api/status + /api/agents (enabled_providers, default_provider, agents)
//   dispatch  -> POST /api/tasks/run
//   execution <- GET  /api/runs/{run_id}, polled every 1.75 s until the record is terminal
//   agents    <- GET  /api/agents
//   settings  <- GET  /api/status
//
// There is no artifact or approval route in app/api/server.py, so neither a fake artifact list
// nor an accept/reject button is drawn: the card says what is missing and why. Every number on
// this screen is copied from a response; none is computed to look like progress.

import { useCallback, useEffect, useMemo, useState } from "react";
import { Badge, Button, Card, CardHead, Field, StatusDot } from "../components/Primitives";
import { IconAnvil, IconPlay, IconRefresh } from "../components/Icons";
import { t, type Lang } from "../i18n";
import type { BackendState } from "../api/client";
import {
  TASK_CATEGORIES,
  dispatchTask,
  isTerminalRecord,
  loadProjects,
  loadRunRecord,
  type DispatchResult,
  type Failure,
  type ProjectInfo,
  type RunRecord,
  type TaskCategoryValue,
} from "../api/forge";

// Faster than the 15 s backend poll: a run's own state is what the user watches here.
const POLL_INTERVAL_MS = 1750;
const POLL_INTERVAL_LABEL = (POLL_INTERVAL_MS / 1000).toFixed(2).replace(/0$/, "");

const text = (value: unknown, fallback = "—"): string =>
  value === undefined || value === null || value === "" ? fallback : String(value);

const fixed = (value: unknown, digits: number): string =>
  typeof value === "number" && Number.isFinite(value) ? value.toFixed(digits) : "—";

const yesNo = (lang: Lang, value: unknown): string => {
  if (typeof value !== "boolean") return t(lang, "runs.unknown");
  return value ? (lang === "ru" ? "да" : "yes") : (lang === "ru" ? "нет" : "no");
};

const newIdempotencyKey = (): string =>
  typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `forge-${Date.now()}-${Math.random().toString(16).slice(2)}`;

type ProjectsState =
  | { status: "loading" }
  | { status: "ready"; items: ProjectInfo[] }
  | { status: "failed"; failure: Failure };

type DispatchState =
  | { status: "idle" }
  | { status: "pending" }
  | { status: "sent"; result: DispatchResult }
  | { status: "failed"; failure: Failure };

/** One honest message per failure kind: 401/403, no answer at all, or an answered error. */
const FailureNotice = ({
  lang,
  failure,
  onRetry,
}: {
  lang: Lang;
  failure: Failure;
  onRetry?: () => void;
}) => (
  <div className={`notice ${failure.kind === "error" ? "warn" : "danger"}`}>
    <div>
      {failure.kind === "unauthorized"
        ? t(lang, "forge.state.unauthorized")
        : failure.kind === "disconnected"
          ? t(lang, "forge.state.disconnected")
          : `${t(lang, "forge.state.error")}: ${failure.path} → HTTP ${failure.status}`}
    </div>
    {failure.kind === "error" || failure.kind === "disconnected" ? (
      failure.detail ? (
        <div className="small mono" style={{ marginTop: 6, whiteSpace: "pre-wrap" }}>
          {failure.detail}
        </div>
      ) : null
    ) : null}
    {onRetry ? (
      <div className="row" style={{ marginTop: 10 }}>
        <Button size="small" onClick={onRetry}>
          {t(lang, "action.retry")}
        </Button>
      </div>
    ) : null}
  </div>
);

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

export const Forge = ({
  lang,
  backend,
  onRefresh,
  onLanguage,
  onHelp,
}: {
  lang: Lang;
  backend: BackendState;
  onRefresh: () => void;
  onLanguage: (lang: Lang) => void;
  onHelp: () => void;
}) => {
  const live = backend.status === "live";
  const status = live ? backend.core : null;

  // ------------------------------------------------------------------ projects
  const [projects, setProjects] = useState<ProjectsState>({ status: "loading" });
  const [selectedProject, setSelectedProject] = useState("");

  const loadProjectList = useCallback(async () => {
    setProjects({ status: "loading" });
    const outcome = await loadProjects();
    if (outcome.ok) setProjects({ status: "ready", items: outcome.value });
    else setProjects({ status: "failed", failure: outcome.failure });
  }, []);

  useEffect(() => {
    void loadProjectList();
  }, [loadProjectList]);

  const activeProject = String(status?.active_project ?? "");

  useEffect(() => {
    if (projects.status !== "ready") return;
    const ids = projects.items
      .map((project) => String(project.project_id ?? ""))
      .filter((id) => id !== "");
    if (ids.length === 0) return;
    if (selectedProject !== "" && ids.includes(selectedProject)) return;
    setSelectedProject(ids.includes(activeProject) ? activeProject : ids[0]);
  }, [projects, selectedProject, activeProject]);

  // ------------------------------------------------------------------ providers
  const providerChoice = useMemo(() => {
    if (backend.status !== "live") return { list: [] as string[], fallback: "" };
    const details = backend.core.details ?? {};
    const enabled = Array.isArray(details.enabled_providers)
      ? details.enabled_providers.map((item) => String(item ?? "")).filter((item) => item !== "")
      : [];
    const fromAgents = backend.agents
      .map((agent) => String(agent.provider ?? ""))
      .filter((item) => item !== "");
    const list: string[] = [];
    for (const item of [...enabled, ...fromAgents]) {
      if (!list.includes(item)) list.push(item);
    }
    const fallback = typeof details.default_provider === "string" ? details.default_provider : "";
    return { list, fallback };
  }, [backend]);

  const [provider, setProvider] = useState("");

  useEffect(() => {
    // Nothing reported means nothing to choose from: keep whatever the user had instead of
    // silently clearing the selection while a backend poll is in flight.
    if (providerChoice.list.length === 0) return;
    if (provider !== "" && providerChoice.list.includes(provider)) return;
    const next =
      providerChoice.fallback !== "" && providerChoice.list.includes(providerChoice.fallback)
        ? providerChoice.fallback
        : (providerChoice.list[0] ?? "");
    setProvider(next);
  }, [providerChoice, provider]);

  // ------------------------------------------------------------------ task form
  const [description, setDescription] = useState("");
  const [category, setCategory] = useState<TaskCategoryValue>("code");
  const [descriptionError, setDescriptionError] = useState("");
  const [dispatch, setDispatch] = useState<DispatchState>({ status: "idle" });
  const [idempotencyKey, setIdempotencyKey] = useState(newIdempotencyKey);

  // ------------------------------------------------------------------ run record
  const [record, setRecord] = useState<RunRecord | null>(null);
  const [recordFailure, setRecordFailure] = useState<Failure | null>(null);
  const [recordLoading, setRecordLoading] = useState(false);
  const [pollNonce, setPollNonce] = useState(0);

  const dispatched = dispatch.status === "sent" ? dispatch.result : null;
  const runId = String(dispatched?.response.run_id ?? "");
  const terminal = isTerminalRecord(record);

  // Poll GET /api/runs/{run_id} while the record is not terminal. A read failure stops the
  // loop and is shown with a retry: retrying a *read* executes nothing, unlike retrying a task.
  useEffect(() => {
    if (runId === "") return;
    let cancelled = false;
    let timer: number | undefined;
    setRecordLoading(true);
    const step = async () => {
      const outcome = await loadRunRecord(runId);
      if (cancelled) return;
      if (!outcome.ok) {
        setRecordFailure(outcome.failure);
        setRecordLoading(false);
        return;
      }
      setRecord(outcome.value);
      setRecordFailure(null);
      if (isTerminalRecord(outcome.value)) {
        setRecordLoading(false);
        return;
      }
      timer = window.setTimeout(() => void step(), POLL_INTERVAL_MS);
    };
    void step();
    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [runId, pollNonce]);

  const polling = runId !== "" && recordFailure === null && !terminal;

  const submit = async () => {
    if (dispatch.status === "pending" || !live) return;
    const trimmed = description.trim();
    if (trimmed === "") {
      setDescriptionError(t(lang, "runs.error.description"));
      return;
    }
    setDescriptionError("");
    setRecord(null);
    setRecordFailure(null);
    setDispatch({ status: "pending" });
    const outcome = await dispatchTask({
      description: trimmed,
      category,
      // Only a provider the Core itself reported is sent; an empty or stale selection leaves
      // the routing to the backend instead of naming a provider that is not in the list.
      providerName: providerChoice.list.includes(provider) ? provider : undefined,
      projectId: selectedProject === "" ? undefined : selectedProject,
      idempotencyKey,
    });
    if (outcome.ok) {
      setDispatch({ status: "sent", result: outcome.value });
      // A replayed answer must not silently become a new logical operation.
      if (outcome.value.facts.idempotent_replay !== true) setIdempotencyKey(newIdempotencyKey());
    } else {
      setDispatch({ status: "failed", failure: outcome.failure });
    }
    onRefresh();
  };

  // ------------------------------------------------------------------ settings facts
  const catalogue = backend.status === "live" ? backend.catalogue : null;
  const registryCounts =
    catalogue && typeof catalogue.counts === "object" && catalogue.counts !== null
      ? (catalogue.counts as Record<string, unknown>)
      : null;
  const registrySource = catalogue && typeof catalogue.source === "string" ? catalogue.source : "";
  const enabledProviders =
    status && Array.isArray(status.details?.enabled_providers)
      ? status.details.enabled_providers.map((item) => String(item))
      : [];

  // Whether the run was persisted, from the strongest evidence actually available: the
  // X-Forge-History-Recorded header when it reached the interface, otherwise the result of
  // reading the record back with GET /api/runs/{run_id} — a 200 is proof it was stored, a 404
  // is proof it was not, and anything else leaves the answer unknown.
  const historyPersisted: boolean | null =
    dispatched?.facts.history_recorded ??
    (record !== null
      ? true
      : recordFailure !== null && recordFailure.kind === "error" && recordFailure.status === 404
        ? false
        : null);

  // The dispatch basis arrives in the X-Forge-* response headers. When they are absent the
  // interface says so instead of guessing what kind of number it is showing.
  const basisReported =
    dispatched !== null && dispatched.facts.usage_basis !== "" && dispatched.facts.cost_basis !== "";

  return (
    <div className="content forge-workbench">
      <div className="row between" style={{ marginBottom: 18 }}>
        <div>
          <h1 className="row" style={{ margin: 0, fontSize: "var(--t-title)", gap: 10 }}>
            <IconAnvil size={22} className="forge-anvil" />
            {t(lang, "nav.forge")}
          </h1>
          <div className="small muted" style={{ marginTop: 4 }}>
            {t(lang, "forge.subtitle")}
          </div>
        </div>
        <div className="row">
          <Button size="small" tone="ghost" onClick={onHelp}>
            {t(lang, "help.how")}
          </Button>
          <Button
            onClick={() => {
              void loadProjectList();
              onRefresh();
              if (runId !== "") setPollNonce((previous) => previous + 1);
            }}
          >
            <IconRefresh size={15} /> {t(lang, "action.refresh")}
          </Button>
        </div>
      </div>

      {backend.status === "offline" ? (
        <div className="notice danger" style={{ marginBottom: 16 }}>
          <div>{t(lang, "state.offline")}</div>
          <div className="small mono" style={{ marginTop: 6 }}>
            {backend.reason}
          </div>
        </div>
      ) : null}

      <div className="grid overview">
        <div className="stack">
          {/* ---------------------------------------------------------------- projects */}
          <Card className="rise">
            <CardHead
              title={t(lang, "forge.projects.title")}
              hint={t(lang, "forge.projects.hint")}
              action={
                <Badge tone={projects.status === "ready" ? "info" : "neutral"}>
                  {projects.status === "ready" ? projects.items.length : t(lang, "state.loading")}
                </Badge>
              }
            />
            {projects.status === "loading" ? (
              <div className="small muted">{t(lang, "state.loading")}</div>
            ) : projects.status === "failed" ? (
              <FailureNotice lang={lang} failure={projects.failure} onRetry={() => void loadProjectList()} />
            ) : projects.items.length === 0 ? (
              <div className="small muted">{t(lang, "forge.projects.empty")}</div>
            ) : (
              <div className="stack" style={{ gap: 8 }}>
                {projects.items.map((project) => {
                  const id = String(project.project_id ?? "");
                  const isActive = id !== "" && id === activeProject;
                  const isSelected = id === selectedProject;
                  return (
                    <button
                      key={id || `${String(project.name ?? "")}-${String(project.path ?? "")}`}
                      className={`forge-project${isSelected ? " selected" : ""}`}
                      onClick={() => setSelectedProject(id)}
                    >
                      <span className="row" style={{ gap: 8 }}>
                        <StatusDot tone={isActive ? "ok" : "idle"} />
                        <span style={{ fontWeight: 600 }}>{text(project.name)}</span>
                        {isActive ? <Badge tone="ok">{t(lang, "projects.active")}</Badge> : null}
                        {isSelected ? <Badge tone="info">{t(lang, "forge.projects.selected")}</Badge> : null}
                      </span>
                      <span className="mono small muted">{text(project.project_id)}</span>
                      <span className="mono small muted" style={{ wordBreak: "break-all" }}>
                        {text(project.path)}
                      </span>
                    </button>
                  );
                })}
              </div>
            )}
          </Card>

          {/* -------------------------------------------------------------------- task */}
          <Card className="rise d1">
            <CardHead
              title={t(lang, "forge.task.title")}
              hint={t(lang, "forge.task.hint")}
              action={<Badge tone={live ? "info" : "warn"}>{live ? "LIVE" : "OFFLINE"}</Badge>}
            />
            <div className="stack" style={{ gap: 14 }}>
              <Field
                label={t(lang, "forge.task.description")}
                hint={t(lang, "runs.form.description.hint")}
                error={descriptionError}
              >
                <textarea
                  className="input"
                  rows={4}
                  value={description}
                  onChange={(event) => setDescription(event.target.value)}
                  placeholder={t(lang, "runs.form.description.placeholder")}
                />
              </Field>

              <div className="row" style={{ gap: 12, flexWrap: "wrap" }}>
                <label className="field" style={{ flex: "0 1 190px" }}>
                  <span className="label">{t(lang, "forge.task.provider")}</span>
                  <select
                    className="input"
                    value={provider}
                    onChange={(event) => setProvider(event.target.value)}
                    disabled={providerChoice.list.length === 0}
                  >
                    {providerChoice.list.length === 0 ? (
                      <option value="">{t(lang, "forge.settings.none")}</option>
                    ) : (
                      providerChoice.list.map((item) => (
                        <option key={item} value={item}>
                          {item}
                        </option>
                      ))
                    )}
                  </select>
                </label>
                <label className="field" style={{ flex: "0 1 190px" }}>
                  <span className="label">{t(lang, "runs.form.category")}</span>
                  <select
                    className="input"
                    value={category}
                    onChange={(event) => setCategory(event.target.value as TaskCategoryValue)}
                  >
                    {TASK_CATEGORIES.map((item) => (
                      <option key={item} value={item}>
                        {item}
                      </option>
                    ))}
                  </select>
                </label>
              </div>

              <div className="small muted">
                {providerChoice.list.length === 0
                  ? t(lang, "forge.task.provider.empty")
                  : t(lang, "forge.task.provider.note")}
              </div>

              <div className="row" style={{ gap: 12, flexWrap: "wrap" }}>
                <Button
                  tone="primary"
                  disabled={dispatch.status === "pending" || !live}
                  onClick={() => void submit()}
                >
                  <IconPlay size={15} />
                  {dispatch.status === "pending" ? t(lang, "runs.form.running") : t(lang, "runs.form.submit")}
                </Button>
                <span className="small muted">{t(lang, "forge.task.guarded")}</span>
              </div>

              {dispatch.status === "failed" ? <FailureNotice lang={lang} failure={dispatch.failure} /> : null}
            </div>
          </Card>

          {/* --------------------------------------------------------------- execution */}
          <Card className="rise d2">
            <CardHead
              title={t(lang, "forge.execution.title")}
              hint={t(lang, "forge.execution.hint").replace("{sec}", POLL_INTERVAL_LABEL)}
              action={
                <Badge tone={runId === "" ? "neutral" : terminal ? "ok" : polling ? "info" : "warn"}>
                  {runId === ""
                    ? "—"
                    : terminal
                      ? t(lang, "forge.execution.terminal")
                      : polling
                        ? t(lang, "forge.execution.polling")
                        : t(lang, "forge.execution.waiting")}
                </Badge>
              }
            />
            {runId === "" ? (
              <div className="small muted">{t(lang, "forge.execution.empty")}</div>
            ) : recordFailure ? (
              <FailureNotice
                lang={lang}
                failure={recordFailure}
                onRetry={() => setPollNonce((value) => value + 1)}
              />
            ) : record === null ? (
              <div className="small muted">
                {recordLoading ? t(lang, "forge.execution.waiting") : t(lang, "forge.execution.empty")}
              </div>
            ) : (
              <div className="stack" style={{ gap: 14 }}>
                <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
                  <Badge tone={record.classification?.is_success === true ? "ok" : "neutral"}>
                    {text(record.state, t(lang, "runs.unknown"))}
                  </Badge>
                  <span className="mono small muted">{text(record.run_id)}</span>
                </div>
                <Facts
                  items={[
                    [t(lang, "forge.execution.state"), text(record.state)],
                    [t(lang, "forge.execution.status"), text(record.status)],
                    [t(lang, "forge.execution.events"), text(record.event_count)],
                    [t(lang, "forge.execution.sequence"), text(record.last_sequence_number)],
                    [t(lang, "forge.execution.attempt"), text(record.attempt_number)],
                    [t(lang, "forge.execution.interrupted"), yesNo(lang, record.interrupted)],
                    [t(lang, "forge.execution.updated"), text(record.updated_at)],
                    [t(lang, "forge.execution.classification"), text(record.classification?.outcome)],
                    [t(lang, "forge.execution.reason"), text(record.classification?.reason)],
                    [t(lang, "forge.execution.detail"), text(record.classification?.detail)],
                  ]}
                />
                {record.classification?.evidence && record.classification.evidence.length > 0 ? (
                  <div>
                    <div className="label">{t(lang, "forge.execution.evidence")}</div>
                    <div className="row" style={{ gap: 6, flexWrap: "wrap", marginTop: 6 }}>
                      {record.classification.evidence.map((item) => (
                        <Badge key={item} tone="neutral">
                          {item}
                        </Badge>
                      ))}
                    </div>
                  </div>
                ) : null}
              </div>
            )}
          </Card>

          {/* ----------------------------------------------------------------- result */}
          <Card className="rise d3">
            <CardHead
              title={t(lang, "forge.result.title")}
              hint={t(lang, "forge.result.hint")}
              action={
                dispatched ? (
                  <Badge tone={dispatched.response.success === true ? "ok" : "warn"}>
                    {text(dispatched.response.state, t(lang, "runs.unknown"))}
                  </Badge>
                ) : null
              }
            />
            {!dispatched ? (
              <div className="small muted">{t(lang, "forge.result.empty")}</div>
            ) : (
              <div className="stack" style={{ gap: 14 }}>
                <div>
                  <div className="label">{t(lang, "forge.result.output")}</div>
                  <pre className="forge-output mono small">
                    {typeof dispatched.response.output === "string" && dispatched.response.output !== ""
                      ? dispatched.response.output
                      : t(lang, "forge.result.no_output")}
                  </pre>
                </div>

                {dispatched.response.error ? (
                  <div className="notice danger">{String(dispatched.response.error)}</div>
                ) : null}

                <Facts
                  items={[
                    [
                      t(lang, "runs.result.tokens"),
                      dispatched.response.tokens === null || dispatched.response.tokens === undefined
                        ? t(lang, "runs.unknown")
                        : String(dispatched.response.tokens),
                    ],
                    [
                      t(lang, "runs.result.cost"),
                      typeof dispatched.response.cost === "number"
                        ? `$${fixed(dispatched.response.cost, 4)}`
                        : t(lang, "runs.unknown"),
                    ],
                    [t(lang, "runs.result.duration"), fixed(dispatched.response.duration_seconds, 3)],
                    [t(lang, "runs.result.provider"), text(dispatched.response.provider_used)],
                    [t(lang, "runs.result.model"), text(dispatched.response.model_used)],
                    [t(lang, "forge.result.task"), text(dispatched.response.task_id)],
                    [t(lang, "forge.result.project"), text(dispatched.response.project_id)],
                    [t(lang, "forge.result.run"), text(dispatched.response.run_id)],
                  ]}
                />

                <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
                  <Badge tone={dispatched.facts.usage_basis === "ACTUAL" ? "ok" : "warn"}>
                    {t(lang, "runs.basis.usage")}:{" "}
                    {text(dispatched.facts.usage_basis, t(lang, "runs.unknown"))}
                  </Badge>
                  <Badge tone={dispatched.facts.cost_basis === "ACTUAL" ? "ok" : "warn"}>
                    {t(lang, "runs.basis.cost")}:{" "}
                    {text(dispatched.facts.cost_basis, t(lang, "runs.unknown"))}
                  </Badge>
                  {dispatched.facts.idempotent_replay === true ? (
                    <Badge tone="info">{t(lang, "runs.basis.replay")}</Badge>
                  ) : null}
                  <Badge tone={historyPersisted === false ? "warn" : historyPersisted === true ? "ok" : "neutral"}>
                    {t(lang, "forge.result.history")}: {yesNo(lang, historyPersisted)}
                  </Badge>
                </div>

                {!basisReported ? (
                  <div className="small muted">{t(lang, "forge.result.basis.missing")}</div>
                ) : null}

                {historyPersisted === false ? (
                  <div className="notice warn">{t(lang, "runs.history.failed")}</div>
                ) : null}
              </div>
            )}
          </Card>
        </div>

        <div className="stack">
          {/* ----------------------------------------------------------------- agents */}
          <Card className="rise d1">
            <CardHead
              title={t(lang, "forge.agents.title")}
              hint={t(lang, "forge.agents.hint")}
              action={
                <Badge tone={live ? "info" : "neutral"}>
                  {backend.status === "live" ? backend.agents.length : "—"}
                </Badge>
              }
            />
            {backend.status === "loading" ? (
              <div className="small muted">{t(lang, "state.loading")}</div>
            ) : backend.status === "offline" ? (
              <div className="notice danger">{t(lang, "forge.agents.error")}</div>
            ) : backend.agents.length === 0 ? (
              <div className="small muted">{t(lang, "forge.agents.empty")}</div>
            ) : (
              <div className="stack" style={{ gap: 8 }}>
                {backend.agents.map((agent, index) => (
                  <div className="forge-agent" key={`${String(agent.name ?? "agent")}-${index}`}>
                    <div className="row between">
                      <span className="row" style={{ gap: 8 }}>
                        <StatusDot tone={agent.status === "available" ? "ok" : "warn"} />
                        <span style={{ fontWeight: 600 }}>{text(agent.name)}</span>
                      </span>
                      <Badge tone={agent.status === "available" ? "ok" : "warn"}>
                        {text(agent.status, t(lang, "runs.unknown"))}
                      </Badge>
                    </div>
                    <div className="mono small muted">
                      {text(agent.provider)} · {text(agent.model)}
                    </div>
                    {Array.isArray(agent.capabilities) && agent.capabilities.length > 0 ? (
                      <div className="row" style={{ gap: 6, flexWrap: "wrap", marginTop: 6 }}>
                        {agent.capabilities.map((capability) => (
                          <Badge key={String(capability)} tone="neutral">
                            {String(capability)}
                          </Badge>
                        ))}
                      </div>
                    ) : null}
                  </div>
                ))}
              </div>
            )}
          </Card>

          {/* -------------------------------------------------------------- artifacts */}
          <Card className="rise d2">
            <CardHead
              title={t(lang, "forge.artifacts.title")}
              action={<Badge tone="warn">{t(lang, "forge.artifacts.none")}</Badge>}
            />
            <div className="stack small" style={{ gap: 10 }}>
              <div className="muted">{t(lang, "forge.artifacts.body")}</div>
              <div className="muted">{t(lang, "forge.artifacts.review")}</div>
              <div>
                <div className="label">{t(lang, "forge.artifacts.routes")}</div>
                <div className="stack mono small" style={{ gap: 4, marginTop: 6 }}>
                  {[
                    "GET  /api/status",
                    "GET  /api/projects",
                    "GET  /api/agents",
                    "GET  /api/models",
                    "POST /api/tasks/run",
                    "GET  /api/runs/{run_id}",
                  ].map((route) => (
                    <span key={route} className="muted">
                      {route}
                    </span>
                  ))}
                </div>
              </div>
            </div>
          </Card>

          {/* --------------------------------------------------------------- settings */}
          <Card className="rise d3">
            <CardHead
              title={t(lang, "forge.settings.title")}
              hint={t(lang, "forge.settings.hint")}
              action={<Badge tone={live ? "ok" : "warn"}>{live ? "GET" : "—"}</Badge>}
            />
            {backend.status === "loading" ? (
              <div className="small muted">{t(lang, "state.loading")}</div>
            ) : backend.status === "offline" ? (
              <div className="notice danger">{t(lang, "state.offline")}</div>
            ) : (
              <div className="stack" style={{ gap: 12 }}>
                <Facts
                  items={[
                    [t(lang, "forge.settings.core"), text(status?.core)],
                    [t(lang, "forge.settings.api"), text(status?.api)],
                    [t(lang, "forge.settings.provider_status"), text(status?.provider_status)],
                    [
                      t(lang, "forge.settings.providers"),
                      `${text(status?.providers_ready, "0")} / ${text(status?.providers_total, "0")}`,
                    ],
                    [t(lang, "forge.settings.project"), text(status?.active_project)],
                    [t(lang, "forge.settings.timestamp"), text(status?.timestamp)],
                    [
                      t(lang, "forge.settings.enabled"),
                      enabledProviders.length > 0
                        ? enabledProviders.join(", ")
                        : t(lang, "forge.settings.none"),
                    ],
                    [
                      t(lang, "forge.settings.default"),
                      text(status?.details?.default_provider, t(lang, "forge.settings.none")),
                    ],
                    [
                      t(lang, "forge.settings.registry"),
                      registryCounts
                        ? `${text(registryCounts.providers, "0")} / ${text(
                            registryCounts.models,
                            "0"
                          )} / ${text(registryCounts.accounts, "0")}`
                        : t(lang, "forge.settings.none"),
                    ],
                    [
                      t(lang, "forge.settings.registry.source"),
                      text(registrySource, t(lang, "forge.settings.none")),
                    ],
                  ]}
                />
                <div className="small muted">{t(lang, "forge.settings.registry.note")}</div>

                <div className="field">
                  <span className="label">{t(lang, "forge.settings.language")}</span>
                  <div className="segment">
                    <button className={lang === "ru" ? "on" : ""} onClick={() => onLanguage("ru")}>
                      RU
                    </button>
                    <button className={lang === "en" ? "on" : ""} onClick={() => onLanguage("en")}>
                      EN
                    </button>
                  </div>
                  <span className="hint">{t(lang, "forge.settings.language.note")}</span>
                </div>
              </div>
            )}
          </Card>
        </div>
      </div>
    </div>
  );
};
