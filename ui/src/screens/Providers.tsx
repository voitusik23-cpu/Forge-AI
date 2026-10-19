// Провайдеры — the hub that gathers the four screens about model supply.
//
// The navigation used to carry Агенты, Модели, Расходы and Инструменты as four top-level
// sections. They are now one area, and this screen is its front door: the four screens, their
// routes and their data are untouched, and every one of them is still reachable from here and
// from the rail. Nothing on this page is computed: the counters and the states are read from the
// same `GET /api/models` answer the rest of the shell already loads.
//
// The provider state is a *derived* value (`providers[].state`), not an availability claim: the
// note under the list says what it is derived from, and that this read starts no probe.

import { Badge, Button, Card, CardHead, StatusDot } from "../components/Primitives";
import { IconAgents, IconModels, IconTools, IconUsage } from "../components/Icons";
import { t, type Lang } from "../i18n";
import type { BackendState } from "../api/client";

type Tone = "ok" | "warn" | "danger" | "info" | "neutral";

/** The state vocabulary `app/agents/providers/provider_state.py` publishes, with its tone. */
const STATE_TONES: Record<string, Tone> = {
  READY: "ok",
  REGISTERED: "info",
  NOT_CHECKED: "warn",
  NO_CREDENTIALS: "warn",
  NO_CREDITS: "danger",
  NETWORK_BLOCKED: "danger",
  ERROR: "danger",
  DISABLED: "neutral",
};

const text = (value: unknown, fallback = "—"): string =>
  value === undefined || value === null || value === "" ? fallback : String(value);

const asRecord = (value: unknown): Record<string, unknown> | null =>
  value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;

const AREAS: Array<{ id: string; titleKey: string; bodyKey: string; route: string; icon: "agents" | "models" | "usage" | "tools" }> = [
  { id: "agents", titleKey: "nav.agents", bodyKey: "providers.area.agents", route: "/api/agents", icon: "agents" },
  { id: "models", titleKey: "nav.models", bodyKey: "providers.area.models", route: "/api/models", icon: "models" },
  { id: "usage", titleKey: "nav.usage", bodyKey: "providers.area.usage", route: "/api/dashboard · /api/provider-balances", icon: "usage" },
  { id: "tools", titleKey: "nav.tools", bodyKey: "providers.area.tools", route: "/api/tools · /api/capabilities", icon: "tools" },
];

const AREA_ICONS = {
  agents: IconAgents,
  models: IconModels,
  usage: IconUsage,
  tools: IconTools,
};

export const Providers = ({
  lang,
  backend,
  onOpen,
  onHelp,
}: {
  lang: Lang;
  backend: BackendState;
  onOpen: (section: string) => void;
  onHelp: () => void;
}) => {
  const live = backend.status === "live";
  const catalogue = live ? backend.catalogue : null;
  const states = asRecord(catalogue?.provider_states);
  const rows = Array.isArray(catalogue?.providers)
    ? catalogue.providers.filter((row): row is Record<string, unknown> => asRecord(row) !== null)
    : [];
  const ready = states && typeof states.ready_total === "number" ? states.ready_total : null;
  const total = states && typeof states.registered_total === "number" ? states.registered_total : null;

  return (
    <div className="content">
      <div className="row between" style={{ marginBottom: 18 }}>
        <div>
          <h1 className="row" style={{ margin: 0, fontSize: "var(--t-title)", gap: 10 }}>
            <IconModels size={22} />
            {t(lang, "nav.providers")}
          </h1>
          <div className="small muted" style={{ marginTop: 4 }}>
            {t(lang, "providers.subtitle")}
          </div>
        </div>
        <div className="row">
          <Button size="small" tone="ghost" onClick={onHelp}>
            {t(lang, "help.how")}
          </Button>
          <Button size="small" tone="ghost" onClick={() => onOpen("overview")}>
            {t(lang, "nav.overview")}
          </Button>
        </div>
      </div>

      <div className="grid overview">
        <div className="stack">
          <Card className="rise">
            <CardHead
              title={t(lang, "providers.areas.title")}
              hint={t(lang, "providers.areas.hint")}
              action={<Badge tone="info">4</Badge>}
            />
            <div className="forge-hub">
              {AREAS.map((area) => {
                const Icon = AREA_ICONS[area.icon];
                return (
                  <div className="forge-hub-card" key={area.id}>
                    <div className="row" style={{ gap: 8 }}>
                      <Icon size={18} />
                      <span style={{ fontWeight: 600 }}>{t(lang, area.titleKey)}</span>
                    </div>
                    <div className="small muted" style={{ flex: 1 }}>
                      {t(lang, area.bodyKey)}
                    </div>
                    <div className="mono small muted" style={{ wordBreak: "break-word" }}>
                      {area.route}
                    </div>
                    <div className="row">
                      <Button size="small" onClick={() => onOpen(area.id)}>
                        {t(lang, "providers.open")}
                      </Button>
                    </div>
                  </div>
                );
              })}
            </div>
          </Card>

          <Card className="rise d1">
            <CardHead
              title={t(lang, "providers.states.title")}
              hint={t(lang, "providers.states.hint")}
              action={
                <Badge tone={ready !== null && ready > 0 ? "ok" : "neutral"}>
                  {ready !== null && total !== null
                    ? t(lang, "providers.readiness")
                        .replace("{ready}", String(ready))
                        .replace("{total}", String(total))
                    : t(lang, "providers.states.empty")}
                </Badge>
              }
            />
            {!live ? (
              <div className="notice danger">{t(lang, "state.offline")}</div>
            ) : rows.length === 0 ? (
              <div className="small muted">{t(lang, "providers.states.empty")}</div>
            ) : (
              <div className="stack" style={{ gap: 8 }}>
                {rows.map((row, index) => {
                  const state = text(row.state, t(lang, "runs.unknown"));
                  const tone = STATE_TONES[state] ?? "neutral";
                  return (
                    <div className="forge-agent" key={`${String(row.provider_id ?? "provider")}-${index}`}>
                      <div className="row between" style={{ gap: 10, flexWrap: "wrap" }}>
                        <span className="row" style={{ gap: 8 }}>
                          <StatusDot tone={tone === "neutral" ? "idle" : tone === "info" ? "idle" : tone} />
                          <span style={{ fontWeight: 600 }}>{text(row.display_name ?? row.provider_id)}</span>
                          <span className="mono small muted">{text(row.provider_id)}</span>
                        </span>
                        <Badge tone={tone}>{state}</Badge>
                      </div>
                      <div className="small muted">
                        {t(lang, "providers.reason")}: {text(row.reason)} ·{" "}
                        {t(lang, "providers.models.count").replace("{n}", text(row.model_count, "0"))}
                      </div>
                    </div>
                  );
                })}
                <div className="small muted">{t(lang, "providers.states.note")}</div>
              </div>
            )}
          </Card>
        </div>

        <div className="stack">
          <Card className="rise d2">
            <CardHead title={t(lang, "providers.nav.title")} hint={t(lang, "providers.nav.hint")} />
            <div className="stack" style={{ gap: 8 }}>
              {AREAS.map((area) => (
                <button
                  key={area.id}
                  className="forge-project"
                  onClick={() => onOpen(area.id)}
                  style={{ flexDirection: "row", justifyContent: "space-between", alignItems: "center" }}
                >
                  <span style={{ fontWeight: 600 }}>{t(lang, area.titleKey)}</span>
                  <span className="mono small muted">{t(lang, "providers.open")} →</span>
                </button>
              ))}
              <div className="small muted">{t(lang, "providers.nav.note")}</div>
            </div>
          </Card>
        </div>
      </div>
    </div>
  );
};
