// THE EVIDENCE BLOCK: what Forge checked, what it assumed, what it does not know.
//
// The rule this module exists for (taken from the reverse-engineering prompt library the
// owner approved): **every claim carries its evidence, and facts, inferences and unknowns
// are separated**. Forge used to report «готово» on the strength of its own summary. The
// backend now builds the block (`app/reports/evidence.py`) and refuses to put anything but
// a measured fact into `verified`; this file is the client's half: types that mirror the
// payload, and one function that counts the three lists.
//
// `evidenceCounts` is the only place the banner's numbers come from. The component renders
// `t(lang, "evidence.banner")` with these counts substituted, so the sentence can never
// disagree with the lists below it: N is the length of the list a reader can open.
//
// Nothing here is React, so a test can execute it with Node without a DOM.

import type { Lang } from "./i18n";

/** One fact the system measured. `evidence` names the path, command or URL+status. */
export type EvidenceVerified = {
  claim?: string;
  evidence?: string;
  /** `file` | `verification` | `acceptance` | `version` | `server` */
  kind?: string;
};

/** One sentence taken from the model's text, explicitly marked as unchecked. */
export type EvidenceAssumed = {
  claim?: string;
  basis?: string;
};

/** One gap the run could not close. */
export type EvidenceUnknown = {
  item?: string;
  why?: string;
};

/** The raw material the three lists were built from. */
export type EvidenceProof = {
  files?: Array<Record<string, unknown>>;
  verification?: { status?: string; command?: string };
  acceptance?: { status?: string; code?: string };
  run_id?: string;
  commands?: string[];
};

/** `evidence` on a run response and on a project detail, built by the backend. */
export type Evidence = {
  verified?: EvidenceVerified[];
  assumed?: EvidenceAssumed[];
  unknown?: EvidenceUnknown[];
  proof?: EvidenceProof;
};

/** The three numbers the banner prints. */
export type EvidenceCounts = {
  verified: number;
  assumed: number;
  unknown: number;
};

const listOf = (value: unknown): unknown[] => (Array.isArray(value) ? value : []);

/**
 * The length of each list.
 *
 * A missing or malformed block counts as three zeros, never as one; a caller that has no
 * evidence renders no banner (`hasEvidence` is false), so the zeros are not shown as "all
 * clear".
 */
export const evidenceCounts = (evidence: unknown): EvidenceCounts => {
  if (!evidence || typeof evidence !== "object" || Array.isArray(evidence)) {
    return { verified: 0, assumed: 0, unknown: 0 };
  }
  const record = evidence as Record<string, unknown>;
  return {
    verified: listOf(record.verified).length,
    assumed: listOf(record.assumed).length,
    unknown: listOf(record.unknown).length,
  };
};

/**
 * Whether the payload carries an evidence block at all.
 *
 * An older backend publishes no `evidence` key; the screen then shows nothing rather than a
 * «Проверено: 0 · предположения: 0 · неизвестно: 0» line that would read as a clean result.
 */
export const hasEvidence = (evidence: unknown): boolean => {
  if (!evidence || typeof evidence !== "object" || Array.isArray(evidence)) return false;
  const record = evidence as Record<string, unknown>;
  return (
    Array.isArray(record.verified) ||
    Array.isArray(record.assumed) ||
    Array.isArray(record.unknown)
  );
};

/** One verified row's readable text: the claim, with its proof on the line below. */
export const verifiedText = (item: EvidenceVerified | null | undefined): string =>
  String(item?.claim ?? "").trim();

/** One assumption's readable text. */
export const assumedText = (item: EvidenceAssumed | null | undefined): string =>
  String(item?.claim ?? "").trim();

/** One unknown's readable text. */
export const unknownText = (item: EvidenceUnknown | null | undefined): string =>
  String(item?.item ?? "").trim();

/** The three lists, as the block renders them, with malformed entries dropped. */
export const evidenceLists = (
  evidence: unknown
): { verified: EvidenceVerified[]; assumed: EvidenceAssumed[]; unknown: EvidenceUnknown[] } => {
  if (!evidence || typeof evidence !== "object" || Array.isArray(evidence)) {
    return { verified: [], assumed: [], unknown: [] };
  }
  const record = evidence as Record<string, unknown>;
  return {
    verified: listOf(record.verified).filter(
      (item): item is EvidenceVerified => !!item && typeof item === "object"
    ),
    assumed: listOf(record.assumed).filter(
      (item): item is EvidenceAssumed => !!item && typeof item === "object"
    ),
    unknown: listOf(record.unknown).filter(
      (item): item is EvidenceUnknown => !!item && typeof item === "object"
    ),
  };
};

/** The language a banner would be printed in; kept for the component's signature. */
export type EvidenceLang = Lang;
