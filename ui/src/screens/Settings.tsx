import { useCallback, useEffect, useRef, useState } from "react";
import { Badge, Button, Card, CardHead } from "../components/Primitives";
import { VersionLine } from "../components/VersionLine";
import {
  ACCENTS,
  ACCENT_SWATCH,
  DENSITIES,
  THEMES,
  type Accent,
  type Appearance,
  type Density,
  type ThemeMode,
} from "../appearance";
import { t, type Lang } from "../i18n";
import type { Status } from "../api/client";
import {
  loadSettings,
  saveProjectRoot,
  type Failure,
  type ForgeSettings,
} from "../api/forge";

const THEME_LABEL: Record<Lang, Record<ThemeMode, string>> = {
  ru: { dark: "Тёмная", light: "Светлая", system: "Как в системе" },
  en: { dark: "Dark", light: "Light", system: "System" },
};

const ACCENT_LABEL: Record<Lang, Record<Accent, string>> = {
  ru: { violet: "Фиолетовый", cyan: "Бирюзовый", amber: "Янтарный", emerald: "Изумрудный" },
  en: { violet: "Violet", cyan: "Cyan", amber: "Amber", emerald: "Emerald" },
};

const DENSITY_LABEL: Record<Lang, Record<Density, string>> = {
  ru: { comfortable: "Свободная", compact: "Плотная" },
  en: { comfortable: "Comfortable", compact: "Compact" },
};

/**
 * The one setting the product itself owns: where a delivered project lives.
 *
 * It is *not* `.env` and not deployment configuration: it is a folder the owner picks,
 * it is stored in the application's own settings file, and it decides where the **next**
 * project's files appear. The screen therefore prints the stored value, the documented
 * default, and whether the folder exists right now -- three facts, never one summary.
 *
 * The absolute path is printed here on purpose. Every other payload publishes a
 * relative folder; this screen exists so the owner can see and paste the real one.
 */
const ProjectRootCard = ({ lang }: { lang: Lang }) => {
  const [settings, setSettings] = useState<ForgeSettings | null>(null);
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState("");
  const [notice, setNotice] = useState("");
  const [failure, setFailure] = useState<Failure | null>(null);
  const input = useRef<HTMLInputElement | null>(null);

  const load = useCallback(async () => {
    const outcome = await loadSettings();
    if (!outcome.ok) {
      setFailure(outcome.failure);
      return;
    }
    setFailure(null);
    setSettings(outcome.value);
    setValue(String(outcome.value.project_root ?? ""));
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const save = async (body: { project_root: string } | { use_desktop: true }) => {
    setBusy("save");
    setNotice("");
    setFailure(null);
    const outcome = await saveProjectRoot(body);
    setBusy("");
    if (!outcome.ok) {
      setFailure(outcome.failure);
      return;
    }
    setSettings(outcome.value);
    setValue(String(outcome.value.project_root ?? ""));
    setNotice(
      t(lang, "projects.settings.saved").replace(
        "{path}",
        String(outcome.value.project_root ?? "")
      )
    );
  };

  /**
   * «Сменить папку»: the action a fallback must offer.
   *
   * When the configured folder cannot be written to, the projects really live somewhere can
   * else -- and that is a fact the owner has to be able to act on, not just read. The button
   * puts the cursor in the field and selects the current path, so the next keystroke replaces
   * it; the save button beside it is the existing, validated route.
   */
  const changeFolder = () => {
    const field = input.current;
    if (field) {
      field.focus();
      field.select();
    }
    setNotice(t(lang, "projects.settings.change.hint"));
  };

  const current = String(settings?.project_root ?? "");
  const fallbackRoot = String(settings?.default_project_root ?? "");
  const exists = settings?.project_root_exists === true;
  const effective = String(settings?.project_root_effective ?? "");
  const usedFallback = settings?.project_root_used_fallback === true;
  const rootNotice = String(settings?.project_root_notice ?? "");

  return (
    <Card className="rise d2">
      <CardHead
        title={t(lang, "projects.settings.title")}
        hint={t(lang, "projects.settings.hint")}
        action={<Badge tone={exists ? "ok" : "warn"}>.forge/settings.json</Badge>}
      />
      <div className="stack" style={{ gap: 12 }}>
        <label className="field">
          <span className="label">{t(lang, "projects.settings.root")}</span>
          <input
            className="input"
            type="text"
            value={value}
            ref={input}
            spellCheck={false}
            onChange={(event) => setValue(event.target.value)}
            placeholder={fallbackRoot}
            data-testid="settings-project-root"
          />
        </label>
        <div className="row" style={{ gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          <span data-testid="settings-project-root-save">
            <Button
              size="small"
              disabled={busy === "save" || value.trim() === ""}
              onClick={() => void save({ project_root: value })}
            >
              {busy === "save" ? t(lang, "state.loading") : t(lang, "projects.settings.save")}
            </Button>
          </span>
          <span data-testid="settings-project-root-change">
            <Button size="small" tone="ghost" onClick={changeFolder}>
              {t(lang, "projects.settings.change")}
            </Button>
          </span>
          <span data-testid="settings-project-root-desktop">
            <Button
              size="small"
              tone="ghost"
              disabled={busy === "save"}
              onClick={() => void save({ use_desktop: true })}
            >
              {t(lang, "projects.settings.desktop")}
            </Button>
          </span>
        </div>
        {usedFallback ? (
          <div className="notice warn" data-testid="settings-project-root-fallback">
            <div>
              {t(lang, "projects.settings.fallback")
                .replace("{preferred}", current)
                .replace("{root}", effective)}
            </div>
            {rootNotice !== "" ? (
              <div className="small mono" style={{ marginTop: 6, overflowWrap: "anywhere" }}>
                {rootNotice}
              </div>
            ) : null}
          </div>
        ) : null}
        {effective !== "" ? (
          <div className="small mono" data-testid="settings-project-root-effective" style={{ overflowWrap: "anywhere" }}>
            {t(lang, "projects.settings.effective").replace("{path}", effective)}
          </div>
        ) : null}
        {current !== "" ? (
          <div className="small mono" style={{ overflowWrap: "anywhere" }}>
            {current} {exists ? "" : `· ${t(lang, "projects.save.unknown")}`}
          </div>
        ) : null}
        {fallbackRoot !== "" ? (
          <div className="small muted">
            {t(lang, "projects.settings.default").replace("{path}", fallbackRoot)}
          </div>
        ) : null}
        {notice !== "" ? (
          <div className="notice" data-testid="settings-project-root-notice">
            {notice}
          </div>
        ) : null}
        {failure !== null ? (
          <div className="notice warn" data-testid="settings-project-root-failure">
            <div>{`HTTP ${failure.status}${failure.code ? ` · ${failure.code}` : ""}`}</div>
            {failure.detail ? (
              <div className="small mono" style={{ marginTop: 6, whiteSpace: "pre-wrap" }}>
                {failure.detail}
              </div>
            ) : null}
          </div>
        ) : null}
      </div>
    </Card>
  );
};

export const Settings = ({
  lang,
  appearance,
  onChange,
  onHelp,
  storageWorks,
  status,
}: {
  lang: Lang;
  appearance: Appearance;
  onChange: (next: Appearance) => void;
  onHelp: () => void;
  storageWorks: boolean;
  /** The build facts from /api/status.details, or null when the backend does not answer. */
  status: Status | null;
}) => (
  <div className="content">
    <div className="row between" style={{ marginBottom: 18 }}>
      <div>
        <h1 style={{ margin: 0, fontSize: "var(--t-title)" }}>{t(lang, "nav.settings")}</h1>
        <div className="small muted" style={{ marginTop: 4 }}>
          {lang === "ru"
            ? "Внешний вид, папка проектов и хранение настроек"
            : "Appearance, the projects folder, and where preferences are kept"}
        </div>
        {/* version · short commit: the compact line from Part 1.1 of the brief. */}
        <div style={{ marginTop: 6 }}>
          <VersionLine lang={lang} status={status} />
        </div>
      </div>
      <Button size="small" tone="ghost" onClick={onHelp}>
        {t(lang, "help.how")}
      </Button>
    </div>

    <div className="grid cols-2">
      <Card className="rise">
        <CardHead
          title={lang === "ru" ? "Оформление" : "Appearance"}
          hint={lang === "ru" ? "применяется сразу, без перезагрузки" : "applies instantly, no reload"}
          action={<Badge tone="info">localStorage</Badge>}
        />
        <div className="stack" style={{ gap: 18 }}>
          <div className="field">
            <span className="label">{lang === "ru" ? "Тема" : "Theme"}</span>
            <div className="segment">
              {THEMES.map((mode) => (
                <button
                  key={mode}
                  className={appearance.theme === mode ? "on" : ""}
                  onClick={() => onChange({ ...appearance, theme: mode })}
                >
                  {THEME_LABEL[lang][mode]}
                </button>
              ))}
            </div>
          </div>

          <div className="field">
            <span className="label">{lang === "ru" ? "Акцентный цвет" : "Accent colour"}</span>
            <div className="row" style={{ gap: 10 }}>
              {ACCENTS.map((accent) => (
                <button
                  key={accent}
                  onClick={() => onChange({ ...appearance, accent })}
                  title={ACCENT_LABEL[lang][accent]}
                  aria-label={ACCENT_LABEL[lang][accent]}
                  style={{
                    width: 34,
                    height: 34,
                    borderRadius: 10,
                    background: ACCENT_SWATCH[accent],
                    boxShadow:
                      appearance.accent === accent
                        ? "0 0 0 2px var(--text), 0 0 18px color-mix(in srgb, var(--accent) 50%, transparent)"
                        : "inset 0 0 0 1px var(--border)",
                  }}
                />
              ))}
            </div>
          </div>

          <div className="field">
            <span className="label">{lang === "ru" ? "Плотность" : "Density"}</span>
            <div className="segment">
              {DENSITIES.map((density) => (
                <button
                  key={density}
                  className={appearance.density === density ? "on" : ""}
                  onClick={() => onChange({ ...appearance, density })}
                >
                  {DENSITY_LABEL[lang][density]}
                </button>
              ))}
            </div>
          </div>

          <label className="row" style={{ gap: 10 }}>
            <input
              type="checkbox"
              checked={appearance.reducedMotion}
              onChange={(event) => onChange({ ...appearance, reducedMotion: event.target.checked })}
            />
            <span>
              {lang === "ru" ? "Уменьшить анимацию" : "Reduce animation"}
              <span className="small muted" style={{ display: "block" }}>
                {lang === "ru"
                  ? "Отключает переходы и появление элементов"
                  : "Turns off transitions and element entrances"}
              </span>
            </span>
          </label>

          {!storageWorks ? (
            <div className="notice warn">
              {lang === "ru"
                ? "Браузер запретил localStorage: выбор применён, но не сохранится после перезапуска."
                : "The browser refused localStorage: the choice applies but will not survive a restart."}
            </div>
          ) : null}
        </div>
      </Card>

      <ProjectRootCard lang={lang} />

      <Card className="rise d1">
        <CardHead
          title={lang === "ru" ? "Где что лежит" : "Where things live"}
          hint={lang === "ru" ? "реальные пути этого проекта" : "real paths of this project"}
        />
        <div className="stack small">
          {[
            ["ui/dist", lang === "ru" ? "собранный интерфейс" : "the built interface"],
            [".forge/logs/launcher.log", lang === "ru" ? "журнал запуска" : "launcher journal"],
            [".forge/logs/api.log", lang === "ru" ? "журнал Core API" : "Core API log"],
            [".forge/settings.json", lang === "ru" ? "папка проектов (настройка Forge)" : "the projects folder (a Forge setting)"],
            ["%LOCALAPPDATA%\\ForgeAI\\accounts.json", lang === "ru" ? "учётные записи (код — только хешем)" : "accounts (the code is stored as a hash)"],
            ["FORGE_RUN_STORE_ROOT", lang === "ru" ? "записи запусков" : "run records"],
          ].map(([path, note]) => (
            <div className="row between" key={path}>
              <span className="mono small">{path}</span>
              <span className="muted">{note}</span>
            </div>
          ))}
          <div className="muted" style={{ marginTop: 8 }}>
            {lang === "ru"
              ? "Ключи провайдеров читаются из переменных окружения и в файлах проекта не хранятся."
              : "Provider keys are read from the environment and are not stored in project files."}
          </div>
        </div>
      </Card>
    </div>
  </div>
);
