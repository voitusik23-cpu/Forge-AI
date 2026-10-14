import { useCallback, useEffect, useState } from "react";
import { NavRail, TopBar } from "./components/NavRail";
import { Overview } from "./screens/Overview";
import { Agents } from "./screens/Agents";
import { Settings } from "./screens/Settings";
import { Help } from "./screens/Help";
import { Auth } from "./screens/Auth";
import { loadBackend, type BackendState } from "./api/client";
import { CONTEXT_TOPIC } from "./help-content";
import {
  applyAppearance,
  loadAppearance,
  loadAppearanceWithUrl,
  saveAppearance,
  watchSystemTheme,
  type Appearance,
  type ThemeMode,
} from "./appearance";
import { t, type Lang } from "./i18n";

const THEME_ORDER: ThemeMode[] = ["dark", "light", "system"];

const toneOf = (value: unknown): "ok" | "warn" | "danger" | "idle" => {
  const state = String(value ?? "").toUpperCase();
  if (state === "CONNECTED" || state === "OK") return "ok";
  if (state === "DEGRADED" || state === "PARTIAL") return "warn";
  if (!state || state === "UNKNOWN" || state === "NOT_CHECKED") return "idle";
  return "danger";
};

export const App = () => {
  const params = new URLSearchParams(window.location.search);
  const startSection = params.get("nav") ?? "overview";
  const [lang, setLang] = useState<Lang>(params.get("lang") === "en" ? "en" : "ru");
  const [screen, setScreen] = useState<"app" | "auth">(params.get("screen") === "auth" ? "auth" : "app");
  const [backend, setBackend] = useState<BackendState>({ status: "loading" });
  const [nav, setNav] = useState(startSection);
  const [helpTopic, setHelpTopic] = useState(CONTEXT_TOPIC[startSection] ?? "project");
  const [appearance, setAppearance] = useState<Appearance>(() => loadAppearanceWithUrl());
  const [storageWorks, setStorageWorks] = useState(true);

  const updateAppearance = useCallback((next: Appearance) => {
    setAppearance(next);
    applyAppearance(next);
    setStorageWorks(saveAppearance(next));
  }, []);

  useEffect(() => {
    applyAppearance(appearance);
  }, [appearance]);

  useEffect(() => {
    // In System mode the interface must follow Windows when the user changes it.
    return watchSystemTheme(() => applyAppearance(loadAppearance()));
  }, []);

  const reload = useCallback(async () => {
    setBackend({ status: "loading" });
    setBackend(await loadBackend());
  }, []);

  useEffect(() => {
    let alive = true;
    const load = async () => {
      const state = await loadBackend();
      if (alive) setBackend(state);
    };
    load();
    const timer = window.setInterval(load, 15000);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, []);

  const openHelp = useCallback(
    (topic?: string) => {
      setHelpTopic(topic ?? CONTEXT_TOPIC[nav] ?? "project");
      setNav("help");
    },
    [nav]
  );

  const selectSection = useCallback((section: string) => {
    setNav(section);
    if (section === "help") setHelpTopic(CONTEXT_TOPIC.help);
  }, []);

  if (screen === "auth") {
    return <Auth lang={lang} onLanguage={setLang} onEnter={() => setScreen("app")} />;
  }

  const live = backend.status === "live";
  const coreTone: "ok" | "warn" | "danger" | "idle" =
    backend.status === "loading" ? "idle" : live ? toneOf(backend.core.core) : "danger";
  const providersReady = live && typeof backend.core.providers_ready === "number" ? backend.core.providers_ready : 0;
  const providersTotal = live && typeof backend.core.providers_total === "number" ? backend.core.providers_total : 0;

  return (
    <div className="app">
      <NavRail
        lang={lang}
        active={nav}
        onSelect={selectSection}
        coreTone={coreTone}
        providersReady={providersReady}
        providersTotal={providersTotal}
      />
      <div className="main">
        <TopBar
          lang={lang}
          onLanguage={setLang}
          project={live ? String(backend.core.active_project ?? "default") : "forge-ai"}
          coreLabel={live ? `core ${String(backend.core.core ?? "")}` : backend.status === "loading" ? "checking..." : "offline"}
          coreTone={coreTone}
          themeMode={appearance.theme}
          onCycleTheme={() =>
            updateAppearance({
              ...appearance,
              theme: THEME_ORDER[(THEME_ORDER.indexOf(appearance.theme) + 1) % THEME_ORDER.length],
            })
          }
          onHelp={() => openHelp()}
        />
        {nav === "overview" ? <Overview lang={lang} backend={backend} /> : null}
        {nav === "agents" ? <Agents lang={lang} backend={backend} onRefresh={() => void reload()} /> : null}
        {nav === "settings" ? (
          <Settings
            lang={lang}
            appearance={appearance}
            onChange={updateAppearance}
            onHelp={() => openHelp(CONTEXT_TOPIC.settings)}
            storageWorks={storageWorks}
          />
        ) : null}
        {nav === "help" ? <Help lang={lang} topic={helpTopic} /> : null}
        {["overview", "agents", "settings", "help"].indexOf(nav) < 0 ? (
          <div className="content">
            <div className="row between" style={{ marginBottom: 12 }}>
              <div className="card-title">{t(lang, `nav.${nav}`)}</div>
              <button className="btn small ghost" onClick={() => openHelp(CONTEXT_TOPIC[nav])}>
                {t(lang, "help.how")}
              </button>
            </div>
            <div className="card">
              <p className="muted small" style={{ margin: 0 }}>
                {lang === "ru"
                  ? "Раздел ещё не перенесён: сначала проверяются Overview, Agents, руководство и оформление. Доступные данные и следующий шаг для этого раздела описаны в руководстве."
                  : "This section is not migrated yet: Overview, Agents, the guide and appearance are verified first. The available data and the next step for this section are described in the guide."}
              </p>
              <div className="row" style={{ marginTop: 12 }}>
                <button className="btn small" onClick={() => openHelp(CONTEXT_TOPIC[nav])}>
                  {t(lang, "help.how")}
                </button>
              </div>
            </div>
          </div>
        ) : null}
      </div>
    </div>
  );
};
