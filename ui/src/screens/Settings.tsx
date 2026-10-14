import { Badge, Button, Card, CardHead } from "../components/Primitives";
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

export const Settings = ({
  lang,
  appearance,
  onChange,
  onHelp,
  storageWorks,
}: {
  lang: Lang;
  appearance: Appearance;
  onChange: (next: Appearance) => void;
  onHelp: () => void;
  storageWorks: boolean;
}) => (
  <div className="content">
    <div className="row between" style={{ marginBottom: 18 }}>
      <div>
        <h1 style={{ margin: 0, fontSize: "var(--t-title)" }}>{t(lang, "nav.settings")}</h1>
        <div className="small muted" style={{ marginTop: 4 }}>
          {lang === "ru"
            ? "Внешний вид и хранение настроек"
            : "Appearance and where preferences are kept"}
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
                        ? "0 0 0 2px var(--ice), 0 0 18px rgba(124,92,255,.5)"
                        : "inset 0 0 0 1px var(--hairline)",
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
