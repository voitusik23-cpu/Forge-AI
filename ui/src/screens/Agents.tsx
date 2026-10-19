import { useMemo, useState } from "react";
import { Badge, Button, Card, CardHead, StatusDot } from "../components/Primitives";
import { IconRefresh, IconSearch } from "../components/Icons";
import { providerBalanceHref } from "../components/ProviderBalance";
import { t, type Lang } from "../i18n";
import type { Agent, AgentUsage, BackendState, Capability, Provider } from "../api/client";

const money = (value: unknown): string => {
  if (typeof value !== "number" || !Number.isFinite(value)) return "—";
  return `$${value.toFixed(4)}`;
};

const integer = (value: unknown): string =>
  typeof value === "number" && Number.isFinite(value) ? value.toLocaleString("ru-RU") : "—";

type Row = {
  agent: Agent;
  provider?: Provider;
};

export const Agents = ({
  lang,
  backend,
  onRefresh,
}: {
  lang: Lang;
  backend: BackendState;
  onRefresh: () => void;
}) => {
  const live = backend.status === "live";
  const agents = live ? backend.agents : [];
  const providers = live && Array.isArray(backend.dashboard.providers) ? backend.dashboard.providers : [];
  const skillCapabilities: Capability[] = live
    ? backend.capabilities.filter((item) => String(item.capability_id ?? "").startsWith("cap:skill:"))
    : [];

  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("all");
  const [provider, setProvider] = useState("all");
  const [model, setModel] = useState("all");
  const [sort, setSort] = useState<"name" | "provider" | "status">("name");
  // Review affordance: ?agent=<name> opens the card directly. The value still comes
  // from /api/agents; an unknown name simply selects nothing.
  const [selected, setSelected] = useState<string | null>(
    new URLSearchParams(window.location.search).get("agent")
  );
  const [copied, setCopied] = useState(false);

  const providerOf = (name: unknown): Provider | undefined =>
    providers.find((item) => String(item.provider ?? "") === String(name ?? ""));

  // An agent's own confirmed figures come from /api/dashboard agents[]. When the dashboard carries
  // no row for the agent, the figure is unknown -- never rendered as 0.
  const dashboardAgents: AgentUsage[] = live && Array.isArray(backend.dashboard.agents)
    ? backend.dashboard.agents
    : [];
  const usageOf = (name: unknown): AgentUsage | undefined =>
    dashboardAgents.find((item) => String(item.agent ?? "") === String(name ?? ""));

  const rows: Row[] = useMemo(() => {
    const filtered = agents.filter((agent) => {
      const haystack = [
        agent.name,
        agent.provider,
        agent.model,
        ...(agent.capabilities ?? []),
      ]
        .join(" ")
        .toLowerCase();
      if (query && !haystack.includes(query.toLowerCase())) return false;
      if (status !== "all" && String(agent.status ?? "") !== status) return false;
      if (provider !== "all" && String(agent.provider ?? "") !== provider) return false;
      if (model !== "all" && String(agent.model ?? "") !== model) return false;
      return true;
    });
    const compare = (left: Row, right: Row): number => {
      if (sort === "provider") {
        return String(left.agent.provider ?? "").localeCompare(String(right.agent.provider ?? ""));
      }
      if (sort === "status") {
        return String(left.agent.status ?? "").localeCompare(String(right.agent.status ?? ""));
      }
      return String(left.agent.name ?? "").localeCompare(String(right.agent.name ?? ""));
    };
    return filtered
      .map((agent) => ({ agent, provider: providerOf(agent.provider) }))
      .sort(compare);
  }, [agents, query, status, provider, model, sort, providers]);

  const selectedRow = rows.find((row) => String(row.agent.name ?? "") === selected) ?? null;
  const selectedUsage = selectedRow === null ? undefined : usageOf(selectedRow.agent.name);
  const selectedProvider = String(selectedRow?.agent.provider ?? "");
  const available = agents.filter((agent) => String(agent.status ?? "") === "available").length;
  const failing = agents.filter((agent) => {
    const state = String(agent.status ?? "").toLowerCase();
    return state !== "" && state !== "available" && state !== "active";
  }).length;
  const totalSpent = live ? backend.dashboard.all_time_totals?.total_spent : undefined;

  const statuses = Array.from(new Set(agents.map((agent) => String(agent.status ?? "")))).filter(Boolean);
  const providerNames = Array.from(new Set(agents.map((agent) => String(agent.provider ?? "")))).filter(Boolean);
  const modelNames = Array.from(new Set(agents.map((agent) => String(agent.model ?? "")))).filter(Boolean);

  if (!live) {
    return (
      <div className="content">
        <h1 style={{ margin: 0, fontSize: "var(--t-title)" }}>{t(lang, "agents.title")}</h1>
        <div className="notice warn" style={{ marginTop: 16 }}>
          {t(lang, "state.offline")}
          {backend.status === "offline" ? ` — ${backend.reason}` : ""}
        </div>
        <div style={{ marginTop: 12 }}>
          <Button onClick={onRefresh}>
            <IconRefresh size={15} /> {t(lang, "action.retry")}
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div className="content">
      <div className="row between" style={{ marginBottom: 18 }}>
        <div>
          <h1 style={{ margin: 0, fontSize: "var(--t-title)", letterSpacing: "-0.01em" }}>
            {t(lang, "agents.title")}
          </h1>
          <div className="small muted" style={{ marginTop: 4 }}>
            {t(lang, "agents.subtitle")}
          </div>
        </div>
        <Button onClick={onRefresh}>
          <IconRefresh size={15} /> {t(lang, "action.refresh")}
        </Button>
      </div>

      <div className="grid cols-4" style={{ marginBottom: 18 }}>
        <Card tight>
          <div className="kpi">
            <span className="label">{t(lang, "agents.kpi.total")}</span>
            <span className="value mono">{integer(agents.length)}</span>
            <span className="small muted mono">/api/agents</span>
          </div>
        </Card>
        <Card tight>
          <div className="kpi">
            <span className="label">{t(lang, "agents.kpi.available")}</span>
            <span className="value mono">{integer(available)}</span>
            <span className="small muted">status = available</span>
          </div>
        </Card>
        <Card tight>
          <div className="kpi">
            <span className="label">{t(lang, "agents.kpi.unavailable")}</span>
            <span className="value mono">{integer(failing)}</span>
            <span className="small muted">{failing === 0 ? "—" : "status ≠ available"}</span>
          </div>
        </Card>
        <Card tight>
          <div className="kpi">
            <span className="label">{t(lang, "agents.kpi.spend")}</span>
            <span className="value mono">{money(totalSpent)}</span>
            <span className="small muted">all_time_totals.total_spent</span>
          </div>
        </Card>
      </div>

      <Card className="rise" tight>
        <div className="row" style={{ gap: 12, flexWrap: "wrap" }}>
          <div className="search" style={{ flex: "1 1 260px", maxWidth: 420 }}>
            <IconSearch size={16} />
            <input
              placeholder={t(lang, "agents.search")}
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
          </div>
          <label className="row small" style={{ gap: 6 }}>
            <span className="muted">{t(lang, "agents.filter.status")}</span>
            <select className="input" value={status} onChange={(event) => setStatus(event.target.value)}>
              <option value="all">{t(lang, "agents.filter.all")}</option>
              {statuses.map((item) => (
                <option key={item} value={item}>
                  {item}
                </option>
              ))}
            </select>
          </label>
          <label className="row small" style={{ gap: 6 }}>
            <span className="muted">{t(lang, "agents.filter.provider")}</span>
            <select className="input" value={provider} onChange={(event) => setProvider(event.target.value)}>
              <option value="all">{t(lang, "agents.filter.all")}</option>
              {providerNames.map((item) => (
                <option key={item} value={item}>
                  {item}
                </option>
              ))}
            </select>
          </label>
          <label className="row small" style={{ gap: 6 }}>
            <span className="muted">{t(lang, "agents.filter.model")}</span>
            <select className="input" value={model} onChange={(event) => setModel(event.target.value)}>
              <option value="all">{t(lang, "agents.filter.all")}</option>
              {modelNames.map((item) => (
                <option key={item} value={item}>
                  {item}
                </option>
              ))}
            </select>
          </label>
          <label className="row small" style={{ gap: 6 }}>
            <span className="muted">{t(lang, "agents.sort")}</span>
            <select
              className="input"
              value={sort}
              onChange={(event) => setSort(event.target.value as "name" | "provider" | "status")}
            >
              <option value="name">{t(lang, "agents.sort.name")}</option>
              <option value="provider">{t(lang, "agents.sort.provider")}</option>
              <option value="status">{t(lang, "agents.sort.status")}</option>
            </select>
          </label>
        </div>
      </Card>

      <div className="grid overview" style={{ marginTop: 16 }}>
        <div className="stack">
          <Card className="rise d1">
          <CardHead
            title={t(lang, "agents.title")}
            hint={`/api/agents · ${rows.length} / ${agents.length}`}
            action={<Badge tone="info">LIVE</Badge>}
          />
          {rows.length === 0 ? (
            <div className="stack" style={{ padding: "10px 4px" }}>
              <div style={{ fontWeight: 600 }}>
                {agents.length === 0 ? t(lang, "agents.empty.title") : t(lang, "agents.empty.filtered")}
              </div>
              <div className="small muted">
                {agents.length === 0 ? t(lang, "agents.empty.body") : "—"}
              </div>
            </div>
          ) : (
            <table className="table">
              <thead>
                <tr>
                  <th>{t(lang, "agents.col.name")}</th>
                  <th>{t(lang, "agents.col.provider")}</th>
                  <th>{t(lang, "agents.col.model")}</th>
                  <th>{t(lang, "agents.col.status")}</th>
                  <th>{t(lang, "agents.col.capabilities")}</th>
                  <th>{t(lang, "agents.col.spend")}</th>
                  <th>{t(lang, "agents.col.tokens")}</th>
                  <th>{t(lang, "agents.col.balance")}</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => {
                  const name = String(row.agent.name ?? "");
                  const usage = usageOf(name);
                  const providerId = String(row.agent.provider ?? "");
                  return (
                    <tr
                      key={name}
                      onClick={() => setSelected(name)}
                      style={{ cursor: "pointer" }}
                      className={selected === name ? "selected" : ""}
                    >
                      <td style={{ fontWeight: 600 }}>{name}</td>
                      <td className="mono small">{String(row.agent.provider ?? "—")}</td>
                      <td className="mono small">{String(row.agent.model ?? "—")}</td>
                      <td>
                        <Badge tone={String(row.agent.status) === "available" ? "ok" : "warn"}>
                          {String(row.agent.status ?? "UNKNOWN").toUpperCase()}
                        </Badge>
                      </td>
                      <td className="small muted">{(row.agent.capabilities ?? []).join(", ") || "—"}</td>
                      <td className="mono small">
                        {usage === undefined ? t(lang, "runs.unknown") : money(usage.effective_cost)}
                      </td>
                      <td className="mono small">
                        {usage === undefined ? t(lang, "runs.unknown") : integer(usage.total_tokens)}
                      </td>
                      <td className="small">
                        <a
                          href={providerBalanceHref(providerId)}
                          style={{ color: "var(--info-text)" }}
                          onClick={(event) => event.stopPropagation()}
                        >
                          {t(lang, "agents.balance.link")}
                        </a>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
          </Card>

          <Card className="rise d2">
            <CardHead
              title={t(lang, "agents.providers.title")}
              hint={t(lang, "agents.providers.hint")}
              action={<Badge tone="neutral">/api/dashboard</Badge>}
            />
            {providers.length === 0 ? (
              <div className="small muted">—</div>
            ) : (
              <table className="table">
                <thead>
                  <tr>
                    <th>{t(lang, "agents.col.provider")}</th>
                    <th>{t(lang, "agents.detail.finance.forge_spent")}</th>
                    <th>{t(lang, "agents.col.runs")}</th>
                    <th>{t(lang, "agents.detail.finance.estimate")}</th>
                    <th>tokens</th>
                    <th>ok / fail</th>
                  </tr>
                </thead>
                <tbody>
                  {providers.map((item) => (
                    <tr key={String(item.provider)}>
                      <td className="mono small">{String(item.provider ?? "—")}</td>
                      <td className="mono small">{money(item.forge_spent)}</td>
                      <td className="mono small">{integer(item.total_runs)}</td>
                      <td className="mono small">{money(item.estimated_cost)}</td>
                      <td className="mono small">{integer(item.total_tokens)}</td>
                      <td className="mono small">
                        {integer(item.success_count)} / {integer(item.failure_count)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            <div className="small muted" style={{ marginTop: 10 }}>
              {t(lang, "agents.providers.scope")}
            </div>
          </Card>
        </div>

        <Card className="rise d2">
          {selectedRow === null ? (
            <>
              <CardHead title={t(lang, "agents.detail.title")} />
              <div className="small muted">{t(lang, "agents.detail.select")}</div>
              <div className="stack small" style={{ marginTop: 16 }}>
                {(live ? backend.sources : []).map((source) => (
                  <div className="row between" key={source.path}>
                    <span className="row" style={{ gap: 8 }}>
                      <StatusDot tone={source.ok ? "ok" : "warn"} />
                      <span className="mono small">{source.path}</span>
                    </span>
                    <span className="small muted">{source.detail}</span>
                  </div>
                ))}
              </div>
            </>
          ) : (
            <>
              <CardHead
                title={String(selectedRow.agent.name ?? "")}
                hint={t(lang, "agents.detail.identity")}
                action={
                  <Badge tone={String(selectedRow.agent.status) === "available" ? "ok" : "warn"}>
                    {String(selectedRow.agent.status ?? "UNKNOWN").toUpperCase()}
                  </Badge>
                }
              />

              <div className="stack small">
                <div className="row between">
                  <span className="muted">{t(lang, "agents.col.provider")}</span>
                  <span className="mono">{String(selectedRow.agent.provider ?? "—")}</span>
                </div>
                <div className="row between">
                  <span className="muted">{t(lang, "agents.col.model")}</span>
                  <span className="mono">{String(selectedRow.agent.model ?? "—")}</span>
                </div>
                <div className="row between">
                  <span className="muted">{t(lang, "agents.col.capabilities")}</span>
                  <span className="small">{(selectedRow.agent.capabilities ?? []).join(", ") || "—"}</span>
                </div>
                <div className="row between">
                  <span className="muted">{t(lang, "agents.col.runs")}</span>
                  <span className="mono small">
                    {integer(live ? backend.runs.total_recorded : undefined)}
                  </span>
                </div>
              </div>

              <div className="row" style={{ gap: 8, marginTop: 14 }}>
                <Button
                  size="small"
                  onClick={() => {
                    void navigator.clipboard?.writeText(String(selectedRow.agent.name ?? ""));
                    setCopied(true);
                    window.setTimeout(() => setCopied(false), 1600);
                  }}
                >
                  {copied ? t(lang, "agents.copied") : t(lang, "agents.copy")}
                </Button>
              </div>

              <div className="label" style={{ marginTop: 18 }}>
                {t(lang, "agents.detail.behaviour")}
              </div>
              <div className="notice" style={{ marginTop: 8 }}>
                {t(lang, "agents.detail.instruction.gap")}
              </div>
              <div className="small muted" style={{ marginTop: 8 }}>
                {t(lang, "agents.detail.skills")}:{" "}
                {skillCapabilities.length === 0
                  ? t(lang, "agents.detail.unavailable")
                  : skillCapabilities.map((item) => String(item.name ?? item.capability_id)).join(", ")}
              </div>

              <div className="label" style={{ marginTop: 18 }}>
                {t(lang, "agents.detail.tools")}
              </div>
              <div className="stack small" style={{ marginTop: 8 }}>
                {backend.tools.map((tool) => (
                  <div key={String(tool.id)}>
                    <span className="mono small">{String(tool.id)}</span>
                    <div className="muted">{String(tool.description ?? "")}</div>
                  </div>
                ))}
                <div className="small muted">{t(lang, "agents.detail.tools.scope")}</div>
              </div>

              <div className="label" style={{ marginTop: 18 }}>
                {t(lang, "agents.detail.permissions")}
              </div>
              <div className="small muted" style={{ marginTop: 8 }}>
                {t(lang, "agents.detail.permissions.body")}
              </div>

              <div className="label" style={{ marginTop: 18 }}>
                {t(lang, "agents.detail.finance")}
              </div>
              <div className="stack small" style={{ marginTop: 8 }}>
                <div className="row between">
                  <span className="muted">{t(lang, "agents.detail.finance.agent_spend")}</span>
                  <span className="mono small">
                    {selectedUsage === undefined ? t(lang, "runs.unknown") : money(selectedUsage.effective_cost)}
                  </span>
                </div>
                <div className="row between">
                  <span className="muted">{t(lang, "agents.detail.finance.agent_tokens")}</span>
                  <span className="mono small">
                    {selectedUsage === undefined ? t(lang, "runs.unknown") : integer(selectedUsage.total_tokens)}
                  </span>
                </div>
                <div className="row between">
                  <span className="muted">{t(lang, "agents.detail.finance.balance")}</span>
                  <span className="small">
                    <a href={providerBalanceHref(selectedProvider)} style={{ color: "var(--info-text)" }}>
                      {t(lang, "agents.balance.link")}
                    </a>
                  </span>
                </div>
                <div className="small muted">{t(lang, "agents.balance.note")}</div>
                <div className="row between">
                  <span className="muted">{t(lang, "agents.detail.finance.forge_spent")}</span>
                  <span className="mono small">{money(selectedRow.provider?.forge_spent)}</span>
                </div>
                <div className="row between">
                  <span className="muted">{t(lang, "agents.detail.finance.estimate")}</span>
                  <span className="mono small">{money(selectedRow.provider?.estimated_cost)}</span>
                </div>
                <div className="row between">
                  <span className="muted">{t(lang, "agents.detail.finance.reported")}</span>
                  <span className="mono small">{money(selectedRow.provider?.provider_reported_cost)}</span>
                </div>
                <div className="row between">
                  <span className="muted">{t(lang, "agents.detail.finance.accounted")}</span>
                  <span className="mono small">{money(selectedRow.provider?.effective_cost)}</span>
                </div>
                <div className="small muted">{t(lang, "agents.detail.finance.provider_scope")}</div>
                <div className="row between">
                  <span className="muted">{t(lang, "agents.detail.finance.total")}</span>
                  <span className="mono small">{money(totalSpent)}</span>
                </div>
                <div className="notice warn" style={{ marginTop: 6 }}>
                  {t(lang, "agents.detail.finance.no_agent_balance")}
                </div>
              </div>

              <div className="label" style={{ marginTop: 18 }}>
                {t(lang, "agents.detail.history")}
              </div>
              <div className="stack small" style={{ marginTop: 8 }}>
                <div className="muted">{t(lang, "agents.detail.history.none")}</div>
                <div className="muted">{t(lang, "agents.detail.history.gap")}</div>
              </div>

              <div className="label" style={{ marginTop: 18 }}>
                {t(lang, "agents.detail.config")}
              </div>
              <div className="small muted" style={{ marginTop: 8 }}>
                {t(lang, "agents.detail.config.gap")}
              </div>
            </>
          )}
        </Card>
      </div>
    </div>
  );
};
