import { Badge, Button, Card, CardHead, StatusDot } from "../components/Primitives";
import { IconRefresh } from "../components/Icons";
import { t, type Lang } from "../i18n";
import type { BackendState } from "../api/client";

const text = (value: unknown, fallback = "—"): string =>
  value === undefined || value === null || value === "" ? fallback : String(value);

const money = (value: unknown): string =>
  typeof value === "number" && Number.isFinite(value) ? `$${value.toFixed(4)}` : "—";

type Catalogue = {
  providers?: Array<Record<string, unknown>>;
  models?: Array<Record<string, unknown>>;
  accounts?: Array<Record<string, unknown>>;
  counts?: Record<string, number>;
  source?: string;
  availability_note?: string;
};

const shortCapability = (value: unknown): string => String(value).replace("ModelCapability.", "");
const shortTier = (value: unknown): string => String(value).replace("CostTier.", "");
const shortProtocol = (value: unknown): string => String(value).replace("ProviderProtocol.", "");

export const Models = ({
  lang,
  backend,
  catalogue,
  onRefresh,
  onHelp,
}: {
  lang: Lang;
  backend: BackendState;
  catalogue: Catalogue | null;
  onRefresh: () => void;
  onHelp: () => void;
}) => {
  const live = backend.status === "live";
  const providers = live && Array.isArray(backend.dashboard.providers) ? backend.dashboard.providers : [];
  const models = catalogue?.models ?? [];
  const registryProviders = catalogue?.providers ?? [];
  const accounts = catalogue?.accounts ?? [];
  const accountsConfigured = accounts.filter((item) => item.secret_configured === true).length;
  const ready = live && typeof backend.core.providers_ready === "number" ? backend.core.providers_ready : 0;
  const total = live && typeof backend.core.providers_total === "number" ? backend.core.providers_total : 0;

  const probeFor = (providerId: string): Record<string, unknown> | undefined =>
    providers.find((item) => String(item.provider ?? "") === providerId);

  return (
    <div className="content">
      <div className="row between" style={{ marginBottom: 18 }}>
        <div>
          <h1 style={{ margin: 0, fontSize: "var(--t-title)" }}>{t(lang, "nav.models")}</h1>
          <div className="small muted" style={{ marginTop: 4 }}>{t(lang, "models.subtitle")}</div>
        </div>
        <div className="row">
          <Button size="small" tone="ghost" onClick={onHelp}>{t(lang, "help.how")}</Button>
          <Button onClick={onRefresh}><IconRefresh size={15} /> {t(lang, "action.refresh")}</Button>
        </div>
      </div>

      <div className="grid cols-4" style={{ marginBottom: 18 }}>
        <Card tight>
          <div className="kpi">
            <span className="label">{t(lang, "models.kpi.providers")}</span>
            <span className="value mono">{String(catalogue?.counts?.providers ?? "—")}</span>
            <span className="small muted mono">/api/models</span>
          </div>
        </Card>
        <Card tight>
          <div className="kpi">
            <span className="label">{t(lang, "models.kpi.models")}</span>
            <span className="value mono">{String(catalogue?.counts?.models ?? "—")}</span>
            <span className="small muted mono">/api/models</span>
          </div>
        </Card>
        <Card tight>
          <div className="kpi">
            <span className="label">{t(lang, "models.kpi.accounts")}</span>
            <span className="value mono">{accounts.length === 0 ? "0" : `${accountsConfigured}/${accounts.length}`}</span>
            <span className={`delta ${accountsConfigured > 0 ? "up" : "down"}`}>
              {accountsConfigured > 0 ? t(lang, "models.accounts.ready") : t(lang, "models.accounts.none")}
            </span>
          </div>
        </Card>
        <Card tight>
          <div className="kpi">
            <span className="label">{t(lang, "models.kpi.ready")}</span>
            <span className="value mono">{`${ready}/${total}`}</span>
            <span className="small muted">/api/status · probe</span>
          </div>
        </Card>
      </div>

      <div className="notice warn" style={{ marginBottom: 16 }}>
        {t(lang, "models.registry.note")}
      </div>

      <div className="grid overview">
        <div className="stack">
          <Card className="rise">
            <CardHead
              title={t(lang, "models.table.title")}
              hint={`/api/models · ${models.length}`}
              action={<Badge tone={catalogue ? "info" : "warn"}>{text(catalogue?.source, "offline")}</Badge>}
            />
            {models.length === 0 ? (
              <div className="small muted">{t(lang, "models.table.empty")}</div>
            ) : (
              <table className="table">
                <thead>
                  <tr>
                    <th>{t(lang, "models.col.provider")}</th>
                    <th>{t(lang, "models.col.model")}</th>
                    <th>{t(lang, "models.col.context")}</th>
                    <th>{t(lang, "models.col.capabilities")}</th>
                    <th>{t(lang, "models.col.tier")}</th>
                    <th>{t(lang, "models.col.availability")}</th>
                  </tr>
                </thead>
                <tbody>
                  {models.slice(0, 40).map((model, index) => {
                    const providerId = String(model.provider_id ?? "");
                    const probe = probeFor(providerId);
                    const state = String(probe?.account_status ? (probe.account_status as Record<string, unknown>).status ?? "NOT_CHECKED" : "NO_PROBE");
                    const tone = state === "OK" ? "ok" : state === "NOT_CHECKED" || state === "NO_PROBE" ? "idle" : "warn";
                    return (
                      <tr key={`${providerId}-${String(model.model_id)}-${index}`}>
                        <td className="mono small">{providerId}</td>
                        <td style={{ fontWeight: 600 }}>{text(model.display_name ?? model.model_id)}</td>
                        <td className="mono small">
                          {typeof model.context_window === "number"
                            ? `${Math.round(Number(model.context_window) / 1000)}k`
                            : "—"}
                        </td>
                        <td className="small muted">
                          {(Array.isArray(model.capabilities) ? model.capabilities : [])
                            .map(shortCapability)
                            .slice(0, 4)
                            .join(", ")}
                        </td>
                        <td className="small">{shortTier(model.cost_tier)}</td>
                        <td>
                          <span className="row" style={{ gap: 8 }}>
                            <StatusDot tone={tone} />
                            <span className="small">{state}</span>
                          </span>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )}
          </Card>
        </div>

        <div className="stack">
          <Card className="rise d1">
            <CardHead
              title={t(lang, "models.providers.title")}
              hint={`${registryProviders.length}`}
              action={<Badge tone="neutral">REGISTRY</Badge>}
            />
            <div className="stack" style={{ gap: 10 }}>
              {registryProviders.slice(0, 9).map((provider) => {
                const providerId = String(provider.provider_id ?? "");
                const probe = probeFor(providerId);
                const account = probe?.account_status as Record<string, unknown> | undefined;
                return (
                  <div className="provider" key={providerId} style={{ flexDirection: "column", alignItems: "stretch", gap: 8 }}>
                    <div className="row between">
                      <span className="row" style={{ gap: 10 }}>
                        <span className="glyph">{providerId.slice(0, 2).toUpperCase()}</span>
                        <span>
                          <span style={{ display: "block", fontWeight: 600 }}>{text(provider.display_name)}</span>
                          <span className="small muted">
                            {shortProtocol(provider.protocol)} · {String(provider.model_count ?? 0)}{" "}
                            {t(lang, "models.models.short")}
                          </span>
                        </span>
                      </span>
                      <Badge tone={account?.status === "OK" ? "ok" : "neutral"}>
                        {text(account?.status, "NOT_CHECKED")}
                      </Badge>
                    </div>
                    <div className="row between small">
                      <span className="muted">{t(lang, "models.secret.env")}</span>
                      <span className="mono">{text(provider.secret_env_name)}</span>
                    </div>
                    <div className="small muted">{t(lang, "models.secret.note")}</div>
                    <div className="row between small">
                      <span className="muted">{t(lang, "models.finance.forge_spent")}</span>
                      <span className="mono">{money(probe?.forge_spent)}</span>
                    </div>
                    <div className="row between small">
                      <span className="muted">{t(lang, "models.finance.estimate")}</span>
                      <span className="mono">{money(probe?.estimated_cost)}</span>
                    </div>
                    <div className="row between small">
                      <span className="muted">{t(lang, "models.finance.reported")}</span>
                      <span className="mono">{money(probe?.provider_reported_cost)}</span>
                    </div>
                    <div className="row between small">
                      <span className="muted">{t(lang, "models.finance.balance")}</span>
                      <span className="mono">{text(account?.balance_display)}</span>
                    </div>
                  </div>
                );
              })}
            </div>
          </Card>

          <Card className="rise d2" tight>
            <div className="stack small">
              <div className="row between">
                <span className="muted">{t(lang, "models.next.label")}</span>
                <Badge tone="warn">TEST API</Badge>
              </div>
              <div className="muted">{t(lang, "models.next.body")}</div>
            </div>
          </Card>
        </div>
      </div>
    </div>
  );
};
