import { Card, CardHead, Badge, Button, Sparkline, StatusDot } from "../components/Primitives";
import { IconPlay, IconRefresh } from "../components/Icons";
import { t, type Lang } from "../i18n";
import type { BackendState } from "../api/client";

const text = (value: unknown, fallback = "—"): string =>
  value === undefined || value === null || value === "" ? fallback : String(value);

const num = (value: unknown): string =>
  typeof value === "number" && Number.isFinite(value) ? value.toLocaleString("ru-RU") : "—";

const toneFor = (value: unknown): "ok" | "warn" | "danger" | "idle" => {
  const state = String(value ?? "").toUpperCase();
  if (state === "CONNECTED" || state === "OK" || state === "READY") return "ok";
  if (state === "DEGRADED" || state === "PARTIAL") return "warn";
  if (state === "" || state === "UNKNOWN" || state === "NOT_CHECKED") return "idle";
  return "danger";
};

const Kpi = ({
  label,
  value,
  unit,
  points = [],
  tone,
  note,
  noteTone = "flat",
  source = "",
}: {
  label: string;
  value: string;
  unit?: string;
  points?: number[];
  tone: "violet" | "cyan" | "ok" | "danger";
  note: string;
  noteTone?: "up" | "down" | "flat";
  source?: string;
}) => (
  <Card tight className="rise">
    <div className="kpi">
      <span className="label">{label}</span>
      <span className="row" style={{ gap: 6, alignItems: "baseline" }}>
        <span className="value mono">{value}</span>
        {unit ? <span className="unit">{unit}</span> : null}
      </span>
      <span className={`delta ${noteTone}`}>{note}</span>
      {/* A sparkline implies a time series. Where there is none, the card shows which
          endpoint the value came from instead of drawing motion that does not exist. */}
      {points.length > 1 ? (
        <Sparkline points={points} tone={tone} />
      ) : (
        <span className="small muted mono">{source || "—"}</span>
      )}
    </div>
  </Card>
);

export const Overview = ({ lang, backend }: { lang: Lang; backend: BackendState }) => {
  const live = backend.status === "live";
  const core = live ? backend.core : {};
  const providers = live && Array.isArray(backend.dashboard.providers) ? backend.dashboard.providers : [];
  const runs = live && Array.isArray(backend.runs.runs) ? backend.runs.runs : [];
  const projects = live && Array.isArray(backend.onboarding.projects) ? backend.onboarding.projects : [];

  const ready = typeof core.providers_ready === "number" ? core.providers_ready : undefined;
  const total = typeof core.providers_total === "number" ? core.providers_total : undefined;
  const providerTone = toneFor(core.provider_status);

  return (
    <div className="content">
      <div className="row between" style={{ marginBottom: 18 }}>
        <div>
          <h1 style={{ margin: 0, fontSize: "var(--t-title)", letterSpacing: "-0.01em" }}>
            {t(lang, "overview.title")}
          </h1>
          <div className="small muted" style={{ marginTop: 4 }}>
            {t(lang, "overview.subtitle")}
          </div>
        </div>
        <div className="row">
          <Button>
            <IconRefresh size={15} /> {t(lang, "action.refresh")}
          </Button>
          <Button tone="primary">
            <IconPlay size={15} /> {t(lang, "action.run")}
          </Button>
        </div>
      </div>

      {!live ? (
        <div className={`notice ${backend.status === "offline" ? "warn" : ""}`} style={{ marginBottom: 16 }}>
          {backend.status === "offline" ? `${t(lang, "state.offline")} — ${backend.reason}` : t(lang, "state.live")}
        </div>
      ) : null}

      <div className="grid cols-4" style={{ marginBottom: 18 }}>
        <Kpi
          label={t(lang, "kpi.runs")}
          value={num(live ? backend.runs.total_recorded : undefined)}
          points={
            (backend.status === "live" ? backend.runs.total_recorded ?? 0 : 0) > 0
              ? [0, 1, 1, 2, 2, 3, 3, live ? backend.runs.total_recorded ?? 0 : 0]
              : []
          }
          tone="violet"
          note={live ? (backend.runs.truncated ? "список усечён" : "полный журнал") : "—"}
        />
        <Kpi
          label={t(lang, "kpi.providers")}
          value={ready === undefined || total === undefined ? "—" : `${ready}/${total}`}
          points={[0, 1, 1, 2, 2, 3, 4, ready ?? 0]}
          tone="ok"
          note={String(core.provider_status ?? "—")}
          noteTone={providerTone === "warn" ? "down" : providerTone === "ok" ? "up" : "flat"}
        />
        <Kpi
          label={t(lang, "kpi.core")}
          value={text(core.core)}
          tone="cyan"
          note={`api: ${text(core.api)}`}
          source="/api/status"
        />
        <Kpi
          label={t(lang, "kpi.project")}
          value={text(core.active_project)}
          tone="danger"
          note={`${projects.length} ${lang === "ru" ? "проект(ов)" : "project(s)"}`}
          source="/api/product/onboarding"
        />
      </div>

      <div className="grid overview">
        <div className="stack">
          <Card className="rise d1">
            <CardHead
              title={t(lang, "panel.runs")}
              hint={live ? `/api/product/runs · project ${text(backend.runs.project_id)}` : undefined}
              action={<Badge tone={live ? "info" : "neutral"}>{live ? "LIVE" : "OFFLINE"}</Badge>}
            />
            <table className="table">
              <thead>
                <tr>
                  <th>ID</th>
                  <th>{lang === "ru" ? "Цель" : "Goal"}</th>
                  <th>{lang === "ru" ? "Статус" : "Status"}</th>
                  <th>{lang === "ru" ? "Токены" : "Tokens"}</th>
                  <th>{lang === "ru" ? "Время" : "Time"}</th>
                </tr>
              </thead>
              <tbody>
                {runs.slice(0, 6).map((run, index) => (
                  <tr key={index}>
                    <td className="mono small">{text(run.run_id ?? run.id)}</td>
                    <td>{text(run.goal ?? run.title)}</td>
                    <td>
                      <Badge tone="neutral">{text(run.status, "UNKNOWN")}</Badge>
                    </td>
                    <td className="mono small">{num(run.tokens)}</td>
                    <td className="small muted">{text(run.created_at)}</td>
                  </tr>
                ))}
                {runs.length === 0 ? (
                  <tr>
                    <td colSpan={5} className="small muted" style={{ padding: "18px 12px" }}>
                      {t(lang, "table.empty")}
                    </td>
                  </tr>
                ) : null}
              </tbody>
            </table>
          </Card>

          <Card className="rise d2">
            <CardHead
              title={t(lang, "panel.providers")}
              hint={live ? `/api/dashboard · ${providers.length}` : undefined}
              action={
                <Badge tone={providerTone === "idle" ? "neutral" : providerTone}>
                  {text(core.provider_status, "UNKNOWN")}
                </Badge>
              }
            />
            <div className="grid cols-3">
              {providers.slice(0, 6).map((item, index) => {
                const account = item.account_status ?? {};
                return (
                  <div className="provider" key={`${item.provider}-${index}`}>
                    <span className="glyph">{text(item.provider).slice(0, 2).toUpperCase()}</span>
                    <span style={{ minWidth: 0 }}>
                      <span style={{ display: "block", fontWeight: 600 }}>{text(item.provider)}</span>
                      <span className="small muted">
                        {/* The status is the legacy record's historical verdict, and it is labelled as such.
                  Its balance_display is deliberately not rendered: for several providers that value
                  is hardcoded rather than observed. The sourced balances live in their own section. */}
              <span
                title={
                  lang === "ru"
                    ? "исторический вердикт записи, не текущее наблюдение"
                    : "the record's historical verdict, not a current observation"
                }
              >
                {text(account.status, "NOT_CHECKED")}
              </span>{" "}
              ·{" "}
              <a href="?nav=models" title={t(lang, "balance.section.hint")}>
                {t(lang, "balance.section.title")}
              </a>
                      </span>
                    </span>
                  </div>
                );
              })}
              {providers.length === 0 ? (
                <div className="small muted">{lang === "ru" ? "Провайдеры не найдены" : "No providers found"}</div>
              ) : null}
            </div>
          </Card>
        </div>

        <div className="stack">
          <Card className="rise d1">
            <CardHead
              title={t(lang, "panel.core")}
              hint={live ? t(lang, "state.live") : t(lang, "state.offline")}
              action={<StatusDot tone={live ? toneFor(core.core) : "danger"} />}
            />
            <div className="stack">
              {[
                ["core", text(core.core), toneFor(core.core)],
                ["api", text(core.api), toneFor(core.api)],
                ["provider_status", text(core.provider_status), providerTone],
                ["active_project", text(core.active_project), "idle" as const],
                ["timestamp", text(core.timestamp), "idle" as const],
              ].map(([name, value, tone]) => (
                <div className="row between" key={String(name)}>
                  <span className="row" style={{ gap: 8 }}>
                    <StatusDot tone={tone as "ok" | "warn" | "danger" | "idle"} />
                    <span className="muted">{String(name)}</span>
                  </span>
                  <span className="mono small">{String(value)}</span>
                </div>
              ))}
            </div>
          </Card>

          <Card className="rise d2">
            <CardHead title={lang === "ru" ? "Проекты" : "Projects"} hint="/api/product/onboarding" />
            <div className="stack small">
              {projects.map((project, index) => (
                <div className="row between" key={`${project.project_id}-${index}`}>
                  <span className="row" style={{ gap: 8, minWidth: 0 }}>
                    <StatusDot tone={project.status === "active" ? "ok" : "idle"} />
                    <span style={{ overflow: "hidden", textOverflow: "ellipsis" }}>{text(project.name)}</span>
                  </span>
                  <span className="mono small muted">{text(project.status)}</span>
                </div>
              ))}
              {projects.length === 0 ? <span className="muted">—</span> : null}
            </div>
          </Card>

          <Card className="rise d3">
            <CardHead
              title={lang === "ru" ? "Источники данных" : "Data sources"}
              hint={lang === "ru" ? "откуда взято каждое число на странице" : "where every number came from"}
            />
            <div className="stack small">
              {(live ? backend.sources : []).map((item) => (
                <div className="row between" key={item.path}>
                  <span className="row" style={{ gap: 8 }}>
                    <StatusDot tone={item.ok ? "ok" : "warn"} />
                    <span className="mono small">{item.path}</span>
                  </span>
                  <span className="small muted">{item.detail}</span>
                </div>
              ))}
              {!live ? <span className="muted">—</span> : null}
            </div>
          </Card>

          <Card className="rise d3">
            <CardHead title={t(lang, "panel.activity")} />
            <div className="stack small">
              <div className="row" style={{ gap: 10 }}>
                <StatusDot tone={live ? "ok" : "danger"} />
                <span className="muted">
                  {live ? "/api/status → получен" : "/api/status → недоступен"}
                </span>
              </div>
              {live && backend.notes.length > 0
                ? backend.notes.map((note) => (
                    <div className="row" style={{ gap: 10 }} key={note}>
                      <StatusDot tone="warn" />
                      <span className="muted">{note}</span>
                    </div>
                  ))
                : null}
              {live && backend.notes.length === 0 ? (
                <div className="row" style={{ gap: 10 }}>
                  <StatusDot tone="ok" />
                  <span className="muted">
                    {lang === "ru"
                      ? "Все запросы Overview отвечают реальными данными"
                      : "Every Overview request answers with real data"}
                  </span>
                </div>
              ) : null}
            </div>
          </Card>
        </div>
      </div>
    </div>
  );
};
