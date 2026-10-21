import { Fragment, type ReactElement } from "react";
import { ForgeMark, IconSearch } from "./Icons";
import {
  IconAgents,
  IconAnvil,
  IconHelp,
  IconModels,
  IconOverview,
  IconProjects,
  IconRuns,
  IconSettings,
  IconTools,
  IconUsage,
} from "./Icons";
import { t, type Lang } from "../i18n";
import { StatusDot } from "./Primitives";
import type { ThemeMode } from "../appearance";

// Seven primary sections. Агенты, Модели, Расходы and Инструменты are one area now: their
// screens, routes and endpoints are untouched, they simply live under «Провайдеры» instead of
// taking four top-level slots.
const ITEMS: Array<{ id: string; key: string; Icon: (p: { size?: number }) => ReactElement; badge?: string }> = [
  { id: "forge", key: "nav.forge", Icon: IconAnvil },
  { id: "overview", key: "nav.overview", Icon: IconOverview },
  { id: "projects", key: "nav.projects", Icon: IconProjects, badge: "1" },
  { id: "runs", key: "nav.runs", Icon: IconRuns },
  { id: "providers", key: "nav.providers", Icon: IconModels },
  { id: "settings", key: "nav.settings", Icon: IconSettings },
  { id: "help", key: "nav.help", Icon: IconHelp },
];

/** The four screens the Провайдеры area links to. Their ids are their existing routes. */
const PROVIDER_ITEMS: Array<{ id: string; key: string; Icon: (p: { size?: number }) => ReactElement }> = [
  { id: "agents", key: "nav.agents", Icon: IconAgents },
  { id: "models", key: "nav.models", Icon: IconModels },
  { id: "usage", key: "nav.usage", Icon: IconUsage },
  { id: "tools", key: "nav.tools", Icon: IconTools },
];

const PROVIDER_ITEM_IDS = PROVIDER_ITEMS.map((item) => item.id);

/** Whether one rail entry is the active one, including a child of the Провайдеры area. */
const isActive = (id: string, active: string): boolean => {
  if (id === active) return true;
  return id === "providers" && PROVIDER_ITEM_IDS.indexOf(active) >= 0;
};

export const NavRail = ({
  lang,
  active,
  onSelect,
  coreTone,
  providersReady,
  providersTotal,
}: {
  lang: Lang;
  active: string;
  onSelect: (id: string) => void;
  coreTone: "ok" | "warn" | "danger" | "idle";
  providersReady: number;
  providersTotal: number;
}) => (
  <aside className="rail">
    <div className="brand">
      <ForgeMark />
      <div>
        <div className="name">FORGE AI</div>
        <div className="tag">COMMAND CENTER</div>
      </div>
    </div>

    <nav className="nav">
      {ITEMS.map(({ id, key, Icon, badge }) => (
        <Fragment key={id}>
          <button
            onClick={() => onSelect(id)}
            className={`nav-item${isActive(id, active) ? " active" : ""}`}
          >
            <Icon size={19} />
            <span className="label">{t(lang, key)}</span>
            {badge ? <span className="count">{badge}</span> : null}
          </button>
          {id === "providers" ? (
            <div className="nav-sub">
              {PROVIDER_ITEMS.map((child) => (
                <button
                  key={child.id}
                  onClick={() => onSelect(child.id)}
                  className={`nav-item sub${child.id === active ? " active" : ""}`}
                  title={t(lang, child.key)}
                >
                  <child.Icon size={15} />
                  <span className="label">{t(lang, child.key)}</span>
                </button>
              ))}
            </div>
          ) : null}
        </Fragment>
      ))}
    </nav>

    <div className="rail-foot">
      <div className="rail-card">
        <div className="row between">
          <span className="label">{t(lang, "top.core")}</span>
          <StatusDot tone={coreTone} />
        </div>
        <div className="small muted" style={{ marginTop: 6 }}>
          {providersReady}/{providersTotal} providers ready
        </div>
        <div className="meter" style={{ marginTop: 10 }}>
          <span style={{ width: `${providersTotal ? (providersReady / providersTotal) * 100 : 0}%` }} />
        </div>
      </div>
      <div className="rail-card row" style={{ gap: 10 }}>
        <div className="avatar" style={{ width: 30, height: 30, fontSize: 11 }}>
          DA
        </div>
        <div>
          <div style={{ fontWeight: 600 }}>developer</div>
          <div className="small muted">local session</div>
        </div>
      </div>
    </div>
  </aside>
);

export const TopBar = ({
  lang,
  onLanguage,
  project,
  coreLabel,
  coreTone,
  themeMode,
  onCycleTheme,
  onHelp,
}: {
  lang: Lang;
  onLanguage: (lang: Lang) => void;
  project: string;
  coreLabel: string;
  coreTone: "ok" | "warn" | "danger" | "idle";
  themeMode: ThemeMode;
  onCycleTheme: () => void;
  onHelp: () => void;
}) => (
  <header className="topbar">
    <button className="project-switch" onClick={onHelp} title={t(lang, "help.how")}>
      <span className="dot ok" />
      <span>
        <span className="title">{project}</span>
        <span className="sub" style={{ display: "block" }}>
          workspace · main
        </span>
      </span>
    </button>

    {/* The search field is deliberately OFF: this shell has no search route, and a
        field that accepts typing and does nothing is exactly the kind of dead zone
        the owner stopped tolerating. It says so, instead of pretending. */}
    <div className="search off" title={t(lang, "top.search.off")}>
      <IconSearch size={16} />
      <input
        placeholder={t(lang, "top.search")}
        disabled
        aria-disabled
        title={t(lang, "top.search.off")}
      />
    </div>

    <div className="row" style={{ marginLeft: "auto", gap: 14 }}>
      <span className="row" style={{ gap: 8 }}>
        <StatusDot tone={coreTone} />
        <span className="small">{coreLabel}</span>
      </span>
      <button className="btn small" onClick={onCycleTheme} title={t(lang, "appearance.cycle")}>
        {t(lang, `appearance.theme.${themeMode}`)}
      </button>
      <button className="btn small" onClick={onHelp} title={t(lang, "help.how")}>
        ?
      </button>
      <div className="segment">
        <button className={lang === "ru" ? "on" : ""} onClick={() => onLanguage("ru")}>
          RU
        </button>
        <button className={lang === "en" ? "on" : ""} onClick={() => onLanguage("en")}>
          EN
        </button>
      </div>
      <div className="avatar">DA</div>
    </div>
  </header>
);
