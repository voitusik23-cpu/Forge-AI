import { useEffect, useState } from "react";
import { Badge, Button, Card, CardHead, StatusDot } from "../components/Primitives";
import { IconRefresh } from "../components/Icons";
import { ProviderBalanceCard, ProviderBalanceDetail } from "../components/ProviderBalance";
import { t, type Lang } from "../i18n";
import { loadProviderBalances, type BackendState, type ProviderBalances } from "../api/client";

// A fresh payload replaces the providers it carries and leaves the others as they were.
const mergeBalances = (
  previous: ProviderBalances | null,
  fresh: ProviderBalances
): ProviderBalances => ({
  probes_enabled: fresh.probes_enabled ?? previous?.probes_enabled,
  checked_at: fresh.checked_at ?? previous?.checked_at,
  history_scope: fresh.history_scope ?? previous?.history_scope,
  providers: { ...(previous?.providers ?? {}), ...(fresh.providers ?? {}) },
  history: { ...(previous?.history ?? {}), ...(fresh.history ?? {}) },
});

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

  // Balances are read on demand: the endpoint dials a provider for every read, so it is not part of
  // the 15-second BackendState poll. `?provider=<id>` opens the detail section directly, which is
  // how Agents.tsx links an agent to its provider's balance.
  const [balances, setBalances] = useState<ProviderBalances | null>(null);
  const [balanceError, setBalanceError] = useState("");
  const [balanceBusy, setBalanceBusy] = useState<string | null>(null);
  const [balanceDetail, setBalanceDetail] = useState<string | null>(() =>
    new URLSearchParams(window.location.search).get("provider")
  );

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const payload = await loadProviderBalances();
        if (!alive) return;
        setBalances(payload);
        setBalanceError("");
      } catch (error) {
        if (!alive) return;
        setBalanceError(error instanceof Error ? error.message : String(error));
      }
    };
    void load();
    return () => {
      alive = false;
    };
  }, []);

  const readBalances = async (providerId: string | null) => {
    if (balanceBusy !== null) return;
    setBalanceBusy(providerId ?? "all");
    try {
      const fresh = await loadProviderBalances(providerId ?? undefined, true);
      setBalances((previous) => mergeBalances(previous, fresh));
      setBalanceError("");
    } catch (error) {
      setBalanceError(error instanceof Error ? error.message : String(error));
    } finally {
      setBalanceBusy(null);
    }
  };

  const balanceIds = Object.keys(balances?.providers ?? {});
  const detailReport = balanceDetail === null ? null : balances?.providers?.[balanceDetail] ?? null;

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
            <span className="value mono" title={t(lang, "models.kpi.accounts.source")}>
            {accounts.length === 0 ? "0" : `${accountsConfigured}/${accounts.length}`}
          </span>
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

      <div style={{ marginBottom: 16 }}>
        <Card className="rise">
          <CardHead
            title={t(lang, "balance.section.title")}
            hint={`/api/providers/balance · ${t(lang, "balance.section.hint")}`}
            action={
              <span className="row" style={{ gap: 8 }}>
                <Badge tone={balances?.probes_enabled === false ? "warn" : "info"}>
                  {balances?.probes_enabled === false
                    ? t(lang, "balance.probes.off")
                    : t(lang, "balance.probes.on")}
                </Badge>
                <Button size="small" disabled={balanceBusy !== null} onClick={() => void readBalances(null)}>
                  <IconRefresh size={14} /> {t(lang, "balance.refresh_all")}
                </Button>
              </span>
            }
          />
          <div className="notice" style={{ marginBottom: 12 }}>
            {t(lang, "balance.section.note")}
          </div>
          {balanceError === "" ? null : (
            <div className="notice danger" style={{ marginBottom: 12 }}>
              {`${t(lang, "balance.error.read")}: ${balanceError}`}
            </div>
          )}
          {balances === null ? (
            balanceError === "" ? (
              <div className="small muted">{t(lang, "state.loading")}</div>
            ) : null
          ) : balanceIds.length === 0 ? (
            <div className="small muted">{t(lang, "balance.section.empty")}</div>
          ) : (
            <div className="grid cols-2">
              {balanceIds.map((providerId) => (
                <ProviderBalanceCard
                  key={providerId}
                  lang={lang}
                  providerId={providerId}
                  report={balances.providers?.[providerId] ?? null}
                  probesEnabled={balances.probes_enabled}
                  refreshing={balanceBusy !== null}
                  open={balanceDetail === providerId}
                  onRefresh={() => void readBalances(providerId)}
                  onToggleDetail={() =>
                    setBalanceDetail((current) => (current === providerId ? null : providerId))
                  }
                />
              ))}
            </div>
          )}
        </Card>
      </div>

      {balanceDetail === null ? null : (
        <div style={{ marginBottom: 16 }}>
          <ProviderBalanceDetail
            lang={lang}
            providerId={balanceDetail}
            report={detailReport}
            history={balances?.history?.[balanceDetail] ?? []}
            historyScope={balances?.history_scope}
            checkedAt={balances?.checked_at}
            probesEnabled={balances?.probes_enabled}
            refreshing={balanceBusy !== null}
            onRefresh={() => void readBalances(balanceDetail)}
            onClose={() => setBalanceDetail(null)}
          />
        </div>
      )}

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
                      <a
                        href={`#balance-${providerId}`}
                        className="small"
                        style={{ color: "var(--cyan-hi)" }}
                        onClick={() => setBalanceDetail(providerId)}
                      >
                        {t(lang, "balance.detail.show")}
                      </a>
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
