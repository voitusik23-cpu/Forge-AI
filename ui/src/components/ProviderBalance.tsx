// Honest provider balance rendering.
//
// GET /api/providers/balance reports available balance, granted credit, topped-up credit and usage
// as four separate fields, and it may report that a provider publishes no balance at all. This
// component keeps those facts apart: it never adds them, never relabels one as another, never turns
// an absent figure into zero, and always names the currency, the source and the time the value was
// obtained.
import { Badge, Button, Card, CardHead } from "./Primitives";
import { IconRefresh } from "./Icons";
import { t, type Lang } from "../i18n";
import type { ProviderBalance, ProviderBalanceObservation } from "../api/client";

/** Amount text: the provider's own `*_display` string, else the integer minor units / 100. */
export const balanceAmount = (display?: string | null, minor?: number | null): string | null => {
  if (typeof display === "string" && display.trim() !== "") return display.trim();
  if (typeof minor === "number" && Number.isFinite(minor)) return (minor / 100).toFixed(2);
  return null;
};

/** A timestamp as reported, or an explicit statement that none was reported. */
export const balanceTime = (lang: Lang, iso?: string): string => {
  const raw = typeof iso === "string" ? iso.trim() : "";
  if (raw === "") return t(lang, "balance.updated.none");
  const stamp = new Date(raw);
  // Unparsable input is shown as it arrived: no time is invented.
  if (Number.isNaN(stamp.getTime())) return raw;
  return stamp.toLocaleString(lang === "ru" ? "ru-RU" : "en-GB", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
};

/** "устарело: 12 мин назад" / "stale: 12 min ago" -- a retained value is always aged out loud. */
export const staleAge = (lang: Lang, seconds?: number | null): string => {
  if (typeof seconds !== "number" || !Number.isFinite(seconds) || seconds < 0) {
    return t(lang, "balance.age.none");
  }
  if (seconds < 60) return `${Math.floor(seconds)} ${t(lang, "balance.unit.sec")}`;
  return `${Math.floor(seconds / 60)} ${t(lang, "balance.unit.min")}`;
};

/**
 * The real link to a provider's balance on the Models screen. This application has no router: the
 * shell reads `nav` and the Models screen reads `provider` from the query string on load, so the
 * link carries the current parameters forward and adds those two.
 */
export const providerBalanceHref = (providerId: string): string => {
  const params = new URLSearchParams(window.location.search);
  params.delete("agent");
  params.set("nav", "models");
  if (providerId) params.set("provider", providerId);
  return `${window.location.pathname}?${params.toString()}`;
};

const statusTone = (status: string, stale: boolean): "ok" | "warn" | "danger" | "neutral" => {
  if (stale) return "warn";
  if (status === "OBSERVED") return "ok";
  if (status === "UNAUTHORIZED" || status === "UNREACHABLE" || status === "ERROR") return "danger";
  return "neutral";
};

/** The provider publishes no balance through its API. */
const unsupported = (report: ProviderBalance): boolean => report.supported === false;

/** No probe was performed: the composition forbids outbound probes, or the provider was not checked. */
const noProbe = (report: ProviderBalance, probesEnabled: boolean | undefined): boolean =>
  probesEnabled === false || String(report.connection_status ?? "") === "NOT_CHECKED";

type AmountCell = { text: string; currency: string; minor: string };

/**
 * One amount. The precedence is deliberate and stated, not accidental:
 *
 * 1. "the provider publishes no balance" wins over everything -- a figure is never shown for it;
 * 2. "no probe was performed in this composition" wins over "stale" -- no figure is shown as if a
 *    balance had been requested;
 * 3. a retained value is shown only after a probe actually ran and failed (`stale`), and then it is
 *    always labelled with its age and the reason;
 * 4. a null amount is reported as unknown, never as 0.
 */
const amountCell = (
  lang: Lang,
  report: ProviderBalance,
  display: string | null | undefined,
  minor: number | null | undefined,
  probesEnabled: boolean | undefined
): AmountCell => {
  const unknown = t(lang, "runs.unknown");
  if (unsupported(report)) {
    return { text: t(lang, "balance.not_available"), currency: "", minor: unknown };
  }
  if (noProbe(report, probesEnabled)) {
    return { text: t(lang, "balance.no_probe.value"), currency: "", minor: unknown };
  }
  const amount = balanceAmount(display, minor);
  if (amount === null) return { text: unknown, currency: "", minor: unknown };
  const currency = String(report.currency ?? "").trim();
  return {
    text: amount,
    currency: currency === "" ? t(lang, "balance.currency.none") : currency,
    minor: typeof minor === "number" && Number.isFinite(minor) ? String(minor) : unknown,
  };
};

const AmountRow = ({
  lang,
  label,
  report,
  display,
  minor,
  probesEnabled,
}: {
  lang: Lang;
  label: string;
  report: ProviderBalance;
  display?: string | null;
  minor?: number | null;
  probesEnabled?: boolean;
}) => {
  const cell = amountCell(lang, report, display, minor, probesEnabled);
  return (
    <div className="row between small">
      <span className="muted">{label}</span>
      <span className="mono">
        {cell.text}
        {cell.currency === "" ? null : <span className="muted"> {cell.currency}</span>}
      </span>
    </div>
  );
};

const FieldRow = ({ label, value }: { label: string; value: string }) => (
  <div className="row between small">
    <span className="muted">{label}</span>
    <span className="mono" style={{ textAlign: "right", wordBreak: "break-all" }}>
      {value}
    </span>
  </div>
);

/** The age of a retained value plus the reason it could not be refreshed. */
const staleNote = (lang: Lang, report: ProviderBalance): string => {
  const age = `${t(lang, "balance.stale")}: ${staleAge(lang, report.age_seconds)}`;
  const reason = String(report.error ?? "").trim();
  const base = `${age} — ${t(lang, "balance.stale.note")}`;
  return reason === "" ? base : `${base} ${t(lang, "balance.field.error")}: ${reason}`;
};

export const ProviderBalanceCard = ({
  lang,
  providerId,
  report,
  probesEnabled,
  refreshing,
  open,
  onRefresh,
  onToggleDetail,
}: {
  lang: Lang;
  providerId: string;
  report: ProviderBalance | null;
  probesEnabled?: boolean;
  refreshing: boolean;
  open: boolean;
  onRefresh: () => void;
  onToggleDetail: () => void;
}) => {
  if (report === null) {
    return (
      <div className="provider" style={{ flexDirection: "column", alignItems: "stretch", gap: 8 }}>
        <div className="row between">
          <span className="row" style={{ gap: 10 }}>
            <span className="glyph">{providerId.slice(0, 2).toUpperCase()}</span>
            <span style={{ fontWeight: 600 }}>{providerId}</span>
          </span>
          <Badge tone="neutral">{t(lang, "balance.value.none")}</Badge>
        </div>
        <div className="small muted">{t(lang, "balance.report.missing")}</div>
      </div>
    );
  }

  const provider = String(report.provider ?? providerId);
  const status = String(report.connection_status ?? "").trim();
  const stale = report.stale === true;
  const reason = String(report.error ?? "").trim();
  const source = String(report.source ?? "").trim();
  const probeMissing = noProbe(report, probesEnabled);
  const sourceUnknown = unsupported(report);

  return (
    <div
      className="provider"
      id={`balance-${provider}`}
      style={{ flexDirection: "column", alignItems: "stretch", gap: 8 }}
    >
      <div className="row between">
        <span className="row" style={{ gap: 10 }}>
          <span className="glyph">{provider.slice(0, 2).toUpperCase()}</span>
          <span>
            <span style={{ display: "block", fontWeight: 600 }}>{provider}</span>
            <span className="small muted">{t(lang, "balance.provider.hint")}</span>
          </span>
        </span>
        <span className="row" style={{ gap: 8 }}>
          <Badge tone={statusTone(status, stale)}>
            {status === "" ? t(lang, "balance.status.unknown") : status}
          </Badge>
          <Button size="small" disabled={refreshing} onClick={onRefresh}>
            <IconRefresh size={14} /> {t(lang, "action.refresh")}
          </Button>
        </span>
      </div>

      {sourceUnknown ? (
        <div className="stack" style={{ gap: 4 }}>
          <div>
            <Badge tone="warn">{t(lang, "balance.not_available")}</Badge>
          </div>
          <div className="small muted">{reason === "" ? t(lang, "balance.value.none") : reason}</div>
        </div>
      ) : probeMissing ? (
        <div className="stack" style={{ gap: 4 }}>
          <div className="notice">{t(lang, "balance.no_probe")}</div>
          {stale ? (
            <div className="small muted">{`${t(lang, "balance.stale")}: ${staleAge(
              lang,
              report.age_seconds
            )}`}</div>
          ) : null}
        </div>
      ) : (
        <>
          <AmountRow
            lang={lang}
            label={t(lang, "balance.available")}
            report={report}
            display={report.available_display}
            minor={report.available_minor}
            probesEnabled={probesEnabled}
          />
          <AmountRow
            lang={lang}
            label={t(lang, "balance.granted")}
            report={report}
            display={report.granted_display}
            minor={report.granted_minor}
            probesEnabled={probesEnabled}
          />
          <AmountRow
            lang={lang}
            label={t(lang, "balance.topped_up")}
            report={report}
            display={report.topped_up_display}
            minor={report.topped_up_minor}
            probesEnabled={probesEnabled}
          />
          <AmountRow
            lang={lang}
            label={t(lang, "balance.usage")}
            report={report}
            display={report.usage_display}
            minor={report.usage_minor}
            probesEnabled={probesEnabled}
          />
          {stale ? (
            <div className="notice warn">{staleNote(lang, report)}</div>
          ) : reason === "" ? null : (
            <div className="notice warn">{`${t(lang, "balance.field.error")}: ${reason}`}</div>
          )}
        </>
      )}

      <div className="row between small">
        <span className="muted">{t(lang, "balance.col.source")}</span>
        <span className="mono" style={{ textAlign: "right", wordBreak: "break-all" }}>
          {source === "" ? t(lang, "balance.source.none") : source}
        </span>
      </div>
      <div className="row between small">
        <span className="muted">{t(lang, "balance.col.updated")}</span>
        <span className="mono">{balanceTime(lang, report.last_updated)}</span>
      </div>
      {String(report.billing_period ?? "").trim() === "" ? null : (
        <div className="row between small">
          <span className="muted">{t(lang, "balance.field.period")}</span>
          <span className="mono">{report.billing_period}</span>
        </div>
      )}

      <div className="row between">
        <Button size="small" tone="ghost" onClick={onToggleDetail}>
          {open ? t(lang, "balance.detail.hide") : t(lang, "balance.detail.show")}
        </Button>
        {probeMissing || sourceUnknown || stale ? (
          <span className="small muted">{t(lang, "balance.not_current")}</span>
        ) : null}
      </div>
    </div>
  );
};

/** History entries carry minor units only, so they are formatted the same way. */
const historyAmount = (lang: Lang, entry: ProviderBalanceObservation, minor?: number | null): string => {
  const amount = balanceAmount(null, minor);
  if (amount === null) return t(lang, "runs.unknown");
  const currency = String(entry.currency ?? "").trim();
  return currency === "" ? amount : `${amount} ${currency}`;
};

export const ProviderBalanceDetail = ({
  lang,
  providerId,
  report,
  history,
  historyScope,
  checkedAt,
  probesEnabled,
  refreshing,
  onRefresh,
  onClose,
}: {
  lang: Lang;
  providerId: string;
  report: ProviderBalance | null;
  history: ProviderBalanceObservation[];
  historyScope?: string;
  checkedAt?: string;
  probesEnabled?: boolean;
  refreshing: boolean;
  onRefresh: () => void;
  onClose: () => void;
}) => {
  // The API appends observations newest last; this list shows the newest first.
  const newestFirst = [...history].reverse();
  const amounts: Array<[string, string | null | undefined, number | null | undefined]> =
    report === null
      ? []
      : [
          [t(lang, "balance.field.available"), report.available_display, report.available_minor],
          [t(lang, "balance.field.granted"), report.granted_display, report.granted_minor],
          [t(lang, "balance.field.topped_up"), report.topped_up_display, report.topped_up_minor],
          [t(lang, "balance.field.usage"), report.usage_display, report.usage_minor],
        ];

  return (
    <Card className="rise">
      <CardHead
        title={`${t(lang, "balance.detail.title")} · ${providerId}`}
        hint={`${t(lang, "balance.checked_at")}: ${balanceTime(lang, checkedAt)}`}
        action={
          <span className="row" style={{ gap: 8 }}>
            <Button size="small" disabled={refreshing} onClick={onRefresh}>
              <IconRefresh size={14} /> {t(lang, "action.refresh")}
            </Button>
            <Button size="small" tone="ghost" onClick={onClose}>
              {t(lang, "balance.detail.close")}
            </Button>
          </span>
        }
      />

      {report === null ? (
        <div className="small muted">{t(lang, "balance.report.missing")}</div>
      ) : (
        <>
          {unsupported(report) ? (
            <div className="notice warn" style={{ marginBottom: 10 }}>
              <div>{t(lang, "balance.not_available")}</div>
              <div className="small">
                {String(report.error ?? "").trim() === ""
                  ? t(lang, "balance.value.none")
                  : String(report.error)}
              </div>
            </div>
          ) : noProbe(report, probesEnabled) ? (
            <div className="notice" style={{ marginBottom: 10 }}>
              {t(lang, "balance.no_probe")}
            </div>
          ) : report.stale === true ? (
            <div className="notice warn" style={{ marginBottom: 10 }}>
              {staleNote(lang, report)}
            </div>
          ) : String(report.error ?? "").trim() === "" ? null : (
            <div className="notice warn" style={{ marginBottom: 10 }}>
              {`${t(lang, "balance.field.error")}: ${String(report.error)}`}
            </div>
          )}

          <table className="table">
            <thead>
              <tr>
                <th>{t(lang, "balance.field.amount")}</th>
                <th>{t(lang, "balance.field.value")}</th>
                <th>{t(lang, "balance.field.currency")}</th>
                <th>{t(lang, "balance.field.minor")}</th>
              </tr>
            </thead>
            <tbody>
              {amounts.map(([label, display, minor]) => {
                const cell = amountCell(lang, report, display, minor, probesEnabled);
                return (
                  <tr key={label}>
                    <td className="small">{label}</td>
                    <td className="mono small">{cell.text}</td>
                    <td className="small muted">
                      {cell.currency === "" ? t(lang, "balance.value.none") : cell.currency}
                    </td>
                    <td className="mono small">{cell.minor}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>

          <div className="stack small" style={{ marginTop: 14 }}>
            <FieldRow
              label={t(lang, "balance.field.provider")}
              value={String(report.provider ?? providerId)}
            />
            <FieldRow
              label={t(lang, "balance.field.supported")}
              value={report.supported === false ? t(lang, "balance.no") : t(lang, "balance.yes")}
            />
            <FieldRow
              label={t(lang, "balance.field.status")}
              value={
                String(report.connection_status ?? "").trim() === ""
                  ? t(lang, "balance.value.none")
                  : String(report.connection_status)
              }
            />
            <FieldRow
              label={t(lang, "balance.field.currency")}
              value={
                String(report.currency ?? "").trim() === ""
                  ? t(lang, "balance.value.none")
                  : String(report.currency)
              }
            />
            <FieldRow
              label={t(lang, "balance.field.exponent")}
              value={
                typeof report.minor_unit_exponent === "number"
                  ? String(report.minor_unit_exponent)
                  : t(lang, "balance.value.none")
              }
            />
            <FieldRow
              label={t(lang, "balance.field.period")}
              value={
                String(report.billing_period ?? "").trim() === ""
                  ? t(lang, "balance.value.none")
                  : String(report.billing_period)
              }
            />
            <FieldRow
              label={t(lang, "balance.field.updated")}
              value={balanceTime(lang, report.last_updated)}
            />
            <FieldRow
              label={t(lang, "balance.field.updated_raw")}
              value={
                String(report.last_updated ?? "").trim() === ""
                  ? t(lang, "balance.updated.none")
                  : String(report.last_updated)
              }
            />
            <FieldRow
              label={t(lang, "balance.field.stale")}
              value={report.stale === true ? t(lang, "balance.yes") : t(lang, "balance.no")}
            />
            <FieldRow
              label={t(lang, "balance.field.age")}
              value={
                typeof report.age_seconds === "number"
                  ? String(report.age_seconds)
                  : t(lang, "balance.age.none")
              }
            />
            <FieldRow
              label={t(lang, "balance.field.source")}
              value={
                String(report.source ?? "").trim() === ""
                  ? t(lang, "balance.source.none")
                  : String(report.source)
              }
            />
            <FieldRow
              label={t(lang, "balance.field.error")}
              value={
                String(report.error ?? "").trim() === ""
                  ? t(lang, "balance.value.none")
                  : String(report.error)
              }
            />
          </div>
        </>
      )}

      <div className="label" style={{ marginTop: 18 }}>
        {t(lang, "balance.history.title")}
      </div>
      <div className="small muted" style={{ marginTop: 4 }}>
        {`${t(lang, "balance.history.scope")}: ${
          String(historyScope ?? "").trim() === "" ? t(lang, "balance.value.none") : String(historyScope)
        }`}
      </div>
      {newestFirst.length === 0 ? (
        <div className="small muted" style={{ marginTop: 8 }}>
          {t(lang, "balance.history.empty")}
        </div>
      ) : (
        <table className="table" style={{ marginTop: 8 }}>
          <thead>
            <tr>
              <th>{t(lang, "balance.history.col.at")}</th>
              <th>{t(lang, "balance.history.col.status")}</th>
              <th>{t(lang, "balance.history.col.available")}</th>
              <th>{t(lang, "balance.history.col.granted")}</th>
              <th>{t(lang, "balance.history.col.topped_up")}</th>
              <th>{t(lang, "balance.history.col.usage")}</th>
              <th>{t(lang, "balance.history.col.stale")}</th>
              <th>{t(lang, "balance.history.col.error")}</th>
            </tr>
          </thead>
          <tbody>
            {newestFirst.map((entry, index) => (
              <tr key={`${String(entry.at ?? "")}-${index}`}>
                <td className="mono small">{balanceTime(lang, entry.at)}</td>
                <td className="small">{String(entry.status ?? t(lang, "balance.value.none"))}</td>
                <td className="mono small">{historyAmount(lang, entry, entry.available_minor)}</td>
                <td className="mono small">{historyAmount(lang, entry, entry.granted_minor)}</td>
                <td className="mono small">{historyAmount(lang, entry, entry.topped_up_minor)}</td>
                <td className="mono small">{historyAmount(lang, entry, entry.usage_minor)}</td>
                <td className="small">
                  {entry.stale === true ? t(lang, "balance.yes") : t(lang, "balance.no")}
                </td>
                <td className="small muted">
                  {String(entry.error ?? "").trim() === ""
                    ? t(lang, "balance.value.none")
                    : String(entry.error)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <div className="small muted" style={{ marginTop: 8 }}>
        {t(lang, "balance.history.note")}
      </div>
    </Card>
  );
};
