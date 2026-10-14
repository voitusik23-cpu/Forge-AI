import { useCallback, useEffect, useState } from "react";
import { NavRail, TopBar } from "./components/NavRail";
import { Overview } from "./screens/Overview";
import { Agents } from "./screens/Agents";
import { Auth } from "./screens/Auth";
import { loadBackend, type BackendState } from "./api/client";
import { t, type Lang } from "./i18n";

const toneOf = (value: unknown): "ok" | "warn" | "danger" | "idle" => {
  const state = String(value ?? "").toUpperCase();
  if (state === "CONNECTED" || state === "OK") return "ok";
  if (state === "DEGRADED" || state === "PARTIAL") return "warn";
  if (!state || state === "UNKNOWN" || state === "NOT_CHECKED") return "idle";
  return "danger";
};

export const App = () => {
  const params = new URLSearchParams(window.location.search);
  const [lang, setLang] = useState<Lang>(params.get("lang") === "en" ? "en" : "ru");
  const [screen, setScreen] = useState<"app" | "auth">(params.get("screen") === "auth" ? "auth" : "app");
  const [backend, setBackend] = useState<BackendState>({ status: "loading" });
  const [nav, setNav] = useState(params.get("nav") ?? "overview");

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
        onSelect={setNav}
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
        />
        {nav === "overview" ? <Overview lang={lang} backend={backend} /> : null}
        {nav === "agents" ? <Agents lang={lang} backend={backend} onRefresh={() => void reload()} /> : null}
        {nav !== "overview" && nav !== "agents" ? (
          <div className="content">
            <div className="card">
              <div className="card-title">{t(lang, `nav.${nav}`)}</div>
              <p className="muted small" style={{ marginTop: 8 }}>
                {lang === "ru"
                  ? "Этот раздел переносится после проверки разделов Overview и Agents."
                  : "This section is migrated after Overview and Agents are verified."}
              </p>
            </div>
          </div>
        ) : null}
      </div>
    </div>
  );
};
