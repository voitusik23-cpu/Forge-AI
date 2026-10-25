// «Провайдер · Модель · тариф · почему», one line plus at most three short reasons.
//
// The owner's complaint was «где мне выбрать модель которая будет работать? ... Там у меня
// даже нет возможности что-то выбирать.» The answer is not another dropdown: it is this
// line, drawn under every run and on the project card, reading the backend's own
// `model_choice` (app/agent_runtime/routing.py). Nothing here decides anything — the pair,
// the tier and the reasons are the decision's, and this module only words them.
//
// The tier and complexity words live in i18n in both languages; the reason lines are the
// backend's own short Russian sentences and are shown verbatim, because re-wording a
// reason would make the sentence no longer the reason the run actually made.

import { t, type Lang } from "../i18n";
import type { ModelChoice } from "../api/forge";

/** `FREE`/`CHEAP`/`PAID` as the owner reads them; an unknown tier stays unknown. */
export const tierWord = (lang: Lang, tier: string | undefined): string => {
  const clean = String(tier ?? "").trim().toUpperCase();
  if (clean === "FREE") return t(lang, "route.tier.free");
  if (clean === "CHEAP") return t(lang, "route.tier.cheap");
  if (clean === "PAID") return t(lang, "route.tier.paid");
  return t(lang, "route.tier.unknown");
};

/** `SIMPLE`/`MEDIUM`/`COMPLEX` as the owner reads them. */
export const complexityWord = (lang: Lang, complexity: string | undefined): string => {
  const clean = String(complexity ?? "").trim().toUpperCase();
  if (clean === "SIMPLE") return t(lang, "route.complexity.simple");
  if (clean === "MEDIUM") return t(lang, "route.complexity.medium");
  if (clean === "COMPLEX") return t(lang, "route.complexity.complex");
  return t(lang, "route.complexity.unknown");
};

/** The one line: `provider · model · tier`, or `—` when the decision names no model. */
export const routePairLine = (lang: Lang, choice: ModelChoice | null | undefined): string => {
  if (!choice) return "";
  const provider = String(choice.provider ?? "").trim();
  if (provider === "") return "";
  const model = String(choice.model ?? "").trim();
  return [provider, model === "" ? "—" : model, tierWord(lang, choice.tier)].join(" · ");
};

/** At most three reasons: the owner reads three lines, not a paragraph. */
export const routeReasons = (choice: ModelChoice | null | undefined): string[] =>
  (choice?.reasons ?? [])
    .map((reason) => String(reason ?? "").trim())
    .filter((reason) => reason !== "")
    .slice(0, 3);

/**
 * The card line. `null`/an empty provider draws nothing at all — a run that made no
 * decision must not show an invented route.
 */
export const RouteLine = ({
  lang,
  choice,
  testId = "route-line",
}: {
  lang: Lang;
  choice?: ModelChoice | null;
  testId?: string;
}) => {
  const pair = routePairLine(lang, choice);
  if (pair === "") return null;
  const reasons = routeReasons(choice);
  return (
    <div
      data-testid={testId}
      style={{ display: "grid", gap: 2, marginTop: 4 }}
      title={[pair, ...(choice?.reasons ?? [])].join("\n")}
    >
      <div className="row" style={{ gap: 6, flexWrap: "wrap", alignItems: "baseline" }}>
        <span className="label">{t(lang, "route.title")}</span>
        <span className="mono small" data-testid="route-pair">
          {pair}
        </span>
        <span className="small muted">
          — {complexityWord(lang, choice?.complexity)}
          {choice?.automatic === false ? ` · ${t(lang, "forge.launch.route.manual")}` : ""}
        </span>
      </div>
      {reasons.map((reason) => (
        <div className="small muted" data-testid="route-reason" key={reason}>
          {reason}
        </div>
      ))}
    </div>
  );
};
