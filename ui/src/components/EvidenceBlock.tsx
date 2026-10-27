// «Проверено: N · предположения: M · неизвестно: K», one line that opens into the lists.
//
// The problem this block answers: Forge reported «готово» on the strength of its own
// summary, so a wrong claim was indistinguishable from a checked one. The backend now
// separates measured facts (`verified`) from the model's words (`assumed`) and from what it
// could not establish (`unknown`) — `app/reports/evidence.py`, the rules enforced in code.
// This component is the owner's view of that block, and it adds nothing: every line is the
// backend's own claim or its own reason, printed verbatim.
//
// The banner is deliberately above the fold inside its block and *counts the lists it
// opens*: N comes from `evidenceCounts`, the same function that counts the rows rendered
// below, so the sentence can never disagree with what a reader sees after expanding it.

import { t, type Lang } from "../i18n";
import {
  assumedText,
  evidenceCounts,
  evidenceLists,
  hasEvidence,
  unknownText,
  verifiedText,
  type Evidence,
} from "../evidence";

/** One row with an optional second line, used by all three lists. */
const Row = ({
  text,
  detail,
  mono,
}: {
  text: string;
  detail: string;
  mono?: boolean;
}) => (
  <li className="small" style={{ overflowWrap: "anywhere" }}>
    <div>{text}</div>
    {detail !== "" ? (
      <div className={mono ? "small mono muted" : "small muted"}>{detail}</div>
    ) : null}
  </li>
);

/**
 * The evidence block: one line, three lists.
 *
 * `testId` names the block (`change-evidence`, `forge-evidence`, ...), so a live proof can
 * open exactly this element. When the payload has no evidence block (an older backend) the
 * component renders nothing at all — a «0 · 0 · 0» line would read as "everything is fine",
 * which is the one thing a missing block does not say.
 */
export const EvidenceBlock = ({
  lang,
  evidence,
  testId,
}: {
  lang: Lang;
  evidence: Evidence | null | undefined;
  testId: string;
}) => {
  if (!hasEvidence(evidence)) return null;
  const counts = evidenceCounts(evidence);
  const lists = evidenceLists(evidence);
  const banner = t(lang, "evidence.banner")
    .replace("{verified}", String(counts.verified))
    .replace("{assumed}", String(counts.assumed))
    .replace("{unknown}", String(counts.unknown));
  return (
    <details className="tech-details" data-testid={testId} style={{ marginTop: 8 }}>
      <summary data-testid={`${testId}-banner`} style={{ cursor: "pointer" }}>
        {banner}
      </summary>
      <div className="stack" style={{ gap: 10, marginTop: 8 }}>
        <div data-testid={`${testId}-verified`}>
          <div className="label">{t(lang, "evidence.verified.title")}</div>
          {lists.verified.length === 0 ? (
            <div className="small muted">{t(lang, "evidence.verified.none")}</div>
          ) : (
            <ul className="stack" style={{ gap: 4, margin: "4px 0 0", paddingLeft: 18 }}>
              {lists.verified.map((item, index) => (
                <Row
                  key={`verified-${index}-${verifiedText(item)}`}
                  text={verifiedText(item)}
                  detail={String(item.evidence ?? "").trim()}
                  mono
                />
              ))}
            </ul>
          )}
        </div>
        <div data-testid={`${testId}-assumed`}>
          <div className="label">{t(lang, "evidence.assumed.title")}</div>
          {lists.assumed.length === 0 ? (
            <div className="small muted">{t(lang, "evidence.assumed.none")}</div>
          ) : (
            <ul className="stack" style={{ gap: 4, margin: "4px 0 0", paddingLeft: 18 }}>
              {lists.assumed.map((item, index) => (
                <Row
                  key={`assumed-${index}-${assumedText(item)}`}
                  text={assumedText(item)}
                  detail={String(item.basis ?? "").trim()}
                />
              ))}
            </ul>
          )}
        </div>
        <div data-testid={`${testId}-unknown`}>
          <div className="label">{t(lang, "evidence.unknown.title")}</div>
          {lists.unknown.length === 0 ? (
            <div className="small muted">{t(lang, "evidence.unknown.none")}</div>
          ) : (
            <ul className="stack" style={{ gap: 4, margin: "4px 0 0", paddingLeft: 18 }}>
              {lists.unknown.map((item, index) => (
                <Row
                  key={`unknown-${index}-${unknownText(item)}`}
                  text={unknownText(item)}
                  detail={String(item.why ?? "").trim()}
                />
              ))}
            </ul>
          )}
        </div>
      </div>
    </details>
  );
};
