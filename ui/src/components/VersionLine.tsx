// One line that answers "which build is this?".
//
// The values come from `/api/status.details` (fed by the same builder as `GET /api/version`):
// version, current git commit (short), the backend's build time, the backend flag and the UI
// bundle's build time. Nothing here is guessed: a fact the backend could not establish is shown
// as its own literal (`unknown` / `missing`), and an unreachable backend says so instead of
// printing a dash that could be mistaken for a healthy value. No key and no environment value
// is part of this payload, so none can be shown.
import type { Status } from "../api/client";
import { t, type Lang } from "../i18n";

const literal = (value: unknown, fallback: string): string =>
  value === undefined || value === null || value === "" ? fallback : String(value);

export const VersionLine = ({
  lang,
  status,
  detailed = false,
}: {
  lang: Lang;
  status: Status | null;
  detailed?: boolean;
}) => {
  const details = status?.details;
  if (status === null || details === undefined) {
    return <div className="small muted">{t(lang, "build.offline")}</div>;
  }
  const version = literal(details.version, "unknown");
  const short = literal(details.commit_short, "unknown");
  const full = literal(details.commit, short);
  return (
    <div className="small muted mono" title={`${t(lang, "build.commit")} ${full}`}>
      {t(lang, "build.version")} {version} · {t(lang, "build.commit")} {short}
      {detailed ? (
        <>
          {" · "}
          {t(lang, "build.built")} {literal(details.built_at, "unknown")}
          {" · "}
          {t(lang, "build.ui")} {literal(details.ui_build, "missing")}
          {" · "}
          {literal(details.backend, "unknown")}
        </>
      ) : null}
    </div>
  );
};
