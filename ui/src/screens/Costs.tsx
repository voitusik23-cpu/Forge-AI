// Costs & usage: the financial screen that distinguishes what is confirmed from what is estimated
// and from what is simply unknown.
//
// Sources, and nothing else:
//   * /api/dashboard -> providers[].provider_reported_cost (reported by the provider API),
//     providers[].estimated_cost (computed by Forge from its price table),
//     providers[].effective_cost (the figure Forge treats as the cost of a run),
//     providers[].total_tokens / total_runs / total_requests, today_totals, all_time_totals,
//     generated_at (when the dashboard produced the numbers).
//   * provider ACCOUNT BALANCES are deliberately NOT read here. A balance is not a cost, it is in
//     the provider's own currency and it is obtained at a different moment; it is linked, never
//     summed. That is why this screen contains no balance figure at all.
//
// Honesty rules encoded below:
//   1. a numeric zero in provider_reported_cost cannot be distinguished from "the provider reported
//      nothing" (the dashboard field is a plain sum), so with at least one run it is shown as
//      not-reported rather than as a confirmed zero;
//   2. no run means nothing was spent, which is a confirmed zero and is shown as such;
//   3. everything unknown renders as an explicit unknown marker, never as 0;
//   4. the currency of Forge's own accounting is stated once, and no cross-currency arithmetic is
//      performed anywhere;
//   5. internal budgets and limits are reported as absent, because the system has none.
//
// Layout uses the existing component system (Card, CardHead, Badge, Button and the established
// grid/kpi classes) so the screen belongs to the product instead of introducing a second style.

import { useEffect, useMemo, useState } from "react";
import { Badge, Button, Card, CardHead } from "../components/Primitives";
import { IconRefresh } from "../components/Icons";
import { providerBalanceHref } from "../components/ProviderBalance";
import { t, type Lang } from "../i18n";
import { loadCostStates, type CostStateEntry, type CostStates } from "../api/client";
import type { BackendState, Provider } from "../api/client";

/** Forge's own accounting figures are USD; a figure is never printed without its currency. */
const amount = (value: unknown): string | null =>
  typeof value === "number" && Number.isFinite(value) ? `$${value.toFixed(4)}` : null;

const integer = (value: unknown): string | null =>
  typeof value === "number" && Number.isFinite(value) ? value.toLocaleString("ru-RU") : null;

type CostsProps = {
  lang: Lang;
  backend: BackendState;
  onRefresh: () => void;
};

/** The same KPI shape the Overview screen uses. */
const Kpi = ({
  label,
  value,
  unit,
  note,
  source,
}: {
  label: string;
  value: string;
  unit?: string;
  note?: string;
  source?: string;
}) => (
  <Card tight className="rise">
    <div className="kpi">
      <span className="label">{label}</span>
      <span className="row" style={{ gap: 6, alignItems: "baseline" }}>
        <span className="value mono">{value}</span>
        {unit ? <span className="unit">{unit}</span> : null}
      </span>
      {note ? <span className="delta">{note}</span> : null}
      <span className="small muted mono">{source || "-"}</span>
    </div>
  </Card>
);

export const Costs = ({ lang, backend, onRefresh }: CostsProps) => {
  const live = backend.status === "live";
  const providers: Provider[] =
    live && Array.isArray(backend.dashboard.providers) ? backend.dashboard.providers : [];
  const totals = (live && backend.dashboard.all_time_totals) || {};
  const today = (live && backend.dashboard.today_totals) || {};
  const generatedAt =
    live && typeof backend.dashboard.generated_at === "string" ? backend.dashboard.generated_at : "";
  const [showLegend, setShowLegend] = useState(false);
  // Reads /api/dashboard through the server: this call dials no provider, so unlike the
  // balances it is safe to run on mount.
  const [states, setStates] = useState<CostStates | null>(null);
  const [statesError, setStatesError] = useState("");
  useEffect(() => {
    let cancelled = false;
    loadCostStates()
      .then((payload) => {
        if (!cancelled) setStates(payload);
      })
      .catch((error: unknown) => {
        if (!cancelled) setStatesError(error instanceof Error ? error.message : String(error));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // The three provider-level figures measure the same work differently, so they are shown side by
  // side and never merged into one number.
  const sums = useMemo(() => {
    const reported = providers.reduce(
      (acc, item) =>
        acc + (typeof item.provider_reported_cost === "number" ? item.provider_reported_cost : 0),
      0
    );
    const estimated = providers.reduce(
      (acc, item) => acc + (typeof item.estimated_cost === "number" ? item.estimated_cost : 0),
      0
    );
    const effective = providers.reduce(
      (acc, item) => acc + (typeof item.effective_cost === "number" ? item.effective_cost : 0),
      0
    );
    const runs = providers.reduce(
      (acc, item) => acc + (typeof item.total_runs === "number" ? item.total_runs : 0),
      0
    );
    return { reported, estimated, effective, runs };
  }, [providers]);

  const unknown = t(lang, "runs.unknown");
  const stateTone = (state?: string): "ok" | "warn" | "danger" | "info" | "neutral" => {
    switch (state) {
      case "KNOWN_NONZERO":
        return "info";
      case "KNOWN_ZERO":
        return "ok";
      case "UNKNOWN":
        return "warn";
      case "ERROR":
      case "MALFORMED":
        return "danger";
      default:
        return "neutral";
    }
  };
  const cell = (text: string | null) =>
    text === null ? <span className="muted">{unknown}</span> : <>{text}</>;

  return (
    <div className="content">
      <div className="row between" style={{ marginBottom: 18 }}>
        <div>
          <h1>{t(lang, "costs.title")}</h1>
          <div className="small muted" style={{ marginTop: 4 }}>
            {t(lang, "costs.subtitle")}
          </div>
        </div>
        <Button onClick={onRefresh} tone="default">
          <IconRefresh size={15} /> {t(lang, "action.refresh")}
        </Button>
      </div>

      {backend.status === "offline" ? (
        <div className="notice warn" style={{ marginBottom: 16 }}>
          {t(lang, "costs.offline")} {backend.reason}
        </div>
      ) : null}

      <div className="grid cols-3" style={{ marginBottom: 16 }}>
        <Kpi
          label={t(lang, "costs.kpi.effective")}
          value={amount(sums.effective) ?? unknown}
          unit="USD"
          source={t(lang, "costs.kpi.effective.source")}
        />
        <Kpi
          label={t(lang, "costs.kpi.reported")}
          value={
            sums.runs === 0
              ? (amount(0) ?? unknown)
              : sums.reported > 0
                ? (amount(sums.reported) ?? unknown)
                : unknown
          }
          note={
            sums.runs > 0 && sums.reported === 0 ? t(lang, "costs.kpi.reported.silent") : undefined
          }
          unit={sums.runs === 0 || sums.reported > 0 ? "USD" : undefined}
          source={t(lang, "costs.kpi.reported.source")}
        />
        <Kpi
          label={t(lang, "costs.kpi.estimated")}
          value={amount(sums.estimated) ?? unknown}
          unit="USD"
          source={t(lang, "costs.kpi.estimated.source")}
        />
        <Kpi
          label={t(lang, "costs.kpi.tokens")}
          value={integer(totals.total_tokens) ?? unknown}
          source={t(lang, "costs.kpi.tokens.source")}
        />
        <Kpi
          label={t(lang, "costs.kpi.runs")}
          value={integer(totals.total_runs) ?? unknown}
          note={`${t(lang, "costs.kpi.requests")}: ${integer(totals.total_requests) ?? unknown}`}
          source="all_time_totals"
        />
        <Kpi
          label={t(lang, "costs.kpi.today")}
          value={amount(today.total_spent) ?? unknown}
          unit="USD"
          source={t(lang, "costs.kpi.today.source")}
        />
      </div>

      <div className="notice" style={{ marginBottom: 16 }}>
        {t(lang, "costs.currency")} · {t(lang, "costs.asof")}:{" "}
        <span className="mono">{generatedAt === "" ? unknown : generatedAt}</span>
      </div>

      <div className="notice" style={{ marginBottom: 16 }}>
        {t(lang, "costs.state.explain")}
        {statesError ? ` · ${t(lang, "costs.state.error")}: ${statesError}` : ""}
      </div>

      <Card>
        <CardHead
          title={t(lang, "costs.table.title")}
          hint={`/api/dashboard · ${t(lang, "costs.table.hint")}`}
        />
        <table className="table">
          <thead>
            <tr>
              <th>{t(lang, "costs.col.provider")}</th>
              <th>{t(lang, "costs.col.reported")}</th>
              <th>{t(lang, "costs.col.state")}</th>
              <th>{t(lang, "costs.col.estimated")}</th>
              <th>{t(lang, "costs.col.effective")}</th>
              <th>{t(lang, "costs.col.tokens")}</th>
              <th>{t(lang, "costs.col.runs")}</th>
              <th>{t(lang, "costs.col.balance")}</th>
            </tr>
          </thead>
          <tbody>
            {providers.map((item) => {
              const name = String(item.provider ?? "");
              const entry: CostStateEntry | undefined = states?.providers?.[name];
              const runs = typeof item.total_runs === "number" ? item.total_runs : null;
              // Rules 1 and 2: silence from the provider is unknown; only "no runs" is a confirmed
              // zero.
              const reportedCell =
                runs === 0
                  ? amount(0)
                  : typeof item.provider_reported_cost === "number" &&
                      item.provider_reported_cost > 0
                    ? amount(item.provider_reported_cost)
                    : null;
              const estimatedCell = amount(item.estimated_cost);
              const effectiveCell = runs === 0 ? amount(0) : amount(item.effective_cost);
              return (
                <tr key={name}>
                  <td>
                    <Badge tone="neutral">{name === "" ? unknown : name}</Badge>
                  </td>
                  <td className="mono">{cell(reportedCell)}</td>
                  <td>
                    <Badge tone={stateTone(entry?.reported_cost_state)}>
                      {entry?.reported_cost_state ?? t(lang, "costs.state.absent")}
                    </Badge>
                  </td>
                  <td className="mono">{cell(estimatedCell)}</td>
                  <td className="mono">{cell(effectiveCell)}</td>
                  <td className="mono">{cell(integer(item.total_tokens))}</td>
                  <td className="mono">{cell(integer(runs))}</td>
                  <td>
                    <a href={providerBalanceHref(name)}>{t(lang, "costs.col.balance.open")}</a>
                  </td>
                </tr>
              );
            })}
            {providers.length === 0 ? (
              <tr>
                <td colSpan={7} className="muted">
                  {t(lang, "costs.table.empty")}
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
        <div className="row" style={{ marginTop: 8 }}>
          <Button onClick={() => setShowLegend((previous) => !previous)} tone="ghost" size="small">
            {showLegend ? t(lang, "costs.legend.hide") : t(lang, "costs.legend.show")}
          </Button>
        </div>
        {showLegend ? (
          <ul className="small muted" style={{ marginTop: 8 }}>
            <li>{t(lang, "costs.legend.reported")}</li>
            <li>{t(lang, "costs.legend.estimated")}</li>
            <li>{t(lang, "costs.legend.effective")}</li>
            <li>{t(lang, "costs.legend.zero")}</li>
          </ul>
        ) : null}
      </Card>

      <div className="grid cols-2" style={{ marginTop: 16 }}>
        <Card>
          <CardHead title={t(lang, "costs.balance.title")} hint={t(lang, "costs.balance.hint")} />
          <p className="small muted">{t(lang, "costs.balance.body")}</p>
          <p className="small muted">{t(lang, "costs.balance.currency")}</p>
          <a href={providerBalanceHref("")}>{t(lang, "balance.section.title")}</a>
        </Card>
        <Card>
          <CardHead title={t(lang, "costs.budget.title")} hint="/api/agents · /api/dashboard" />
          <p className="small muted">{t(lang, "costs.budget.body")}</p>
        </Card>
      </div>
    </div>
  );
};
