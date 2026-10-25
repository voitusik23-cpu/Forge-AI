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
  addProviderKey,
  deleteProviderKey,
  loadProviderKeys,
  loadSettings,
  saveProjectRoot,
  type Failure,
  type ForgeSettings,
  type ProviderKeyEntry,
  type ProviderKeySection,
  type ProviderPreset,
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
  /**
   * THE LABEL GRANT, as the backend reported it.
   *
   * The venv interpreters Forge runs on are low integrity, so the owner's medium-integrity
   * Desktop folder refuses writes until its mandatory label is lowered **once**. When that
   * worked, the configured folder is used and the screen says so («Рабочий стол снова
   * принимает запись»). When it could not be done from inside the application, the exact
   * command is printed in a field the owner can copy — an actionable refusal, not a
   * dead end.
   */
  const labelGranted = settings?.project_root_label_granted === true;
  const grantCommand = String(settings?.project_root_label_grant_command ?? "");

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
            {/* THE ONE COMMAND. A fallback caused by the integrity mismatch is fixable
                with one line, so the screen hands it over in a field the owner can select
                and copy, next to the «Сменить папку» button that is already above. */}
            {grantCommand !== "" ? (
              <div style={{ marginTop: 8 }} data-testid="settings-project-root-label-command">
                <input
                  className="input mono"
                  type="text"
                  readOnly
                  value={grantCommand}
                  onFocus={(event) => event.currentTarget.select()}
                  spellCheck={false}
                />
              </div>
            ) : null}
          </div>
        ) : null}
        {labelGranted ? (
          <div className="notice" data-testid="settings-project-root-label">
            {t(lang, "projects.settings.label_granted").replace(
              "{path}",
              effective !== "" ? effective : current
            )}
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

/**
 * «Провайдеры и ключи»: the place the owner could not find.
 *
 * His complaint: providers came from `.env`, which he is not supposed to edit, so there
 * was nowhere in the product to put a key. This card is that place, and it keeps three
 * facts apart on purpose:
 *
 * * **the form** -- one preset, an optional base URL, an optional default model and the
 *   key. The key input is `type="password"`, `autoComplete="off"` and is cleared on a
 *   successful save: it is never put back on the screen, not even from a response,
 *   because no response contains it;
 * * **the list** -- every provider this build knows plus every entry added here, each
 *   with one of four words: «готов» / «нет ключа» / «отключён» / «не поддерживается»,
 *   and the honest sentence that goes with it;
 * * **the storage line** -- where the non-secret half and the key really live, printed
 *   from the backend's own answer rather than promised by the interface.
 */
const STATE_TONE: Record<string, "ok" | "warn" | "neutral" | "danger"> = {
  ready: "ok",
  no_key: "neutral",
  disabled: "warn",
  unsupported: "warn",
};

const stateWord = (lang: Lang, state: string | undefined): string => {
  const key = String(state ?? "");
  const known = ["ready", "no_key", "disabled", "unsupported"];
  return known.includes(key) ? t(lang, `settings.providers.state.${key}`) : t(lang, "projects.save.unknown");
};

const ProviderKeysCard = ({ lang }: { lang: Lang }) => {
  const [section, setSection] = useState<ProviderKeySection | null>(null);
  const [preset, setPreset] = useState("deepseek");
  const [providerId, setProviderId] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [model, setModel] = useState("");
  const [keyValue, setKeyValue] = useState("");
  const [keyEpoch, setKeyEpoch] = useState(0);
  const [busy, setBusy] = useState("");
  const [notice, setNotice] = useState("");
  const [failure, setFailure] = useState<Failure | null>(null);
  const [message, setMessage] = useState("");
  const keyInput = useRef<HTMLInputElement | null>(null);

  const load = useCallback(async () => {
    const outcome = await loadProviderKeys();
    if (!outcome.ok) {
      setFailure(outcome.failure);
      return;
    }
    setFailure(null);
    setSection(outcome.value);
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const presets: ProviderPreset[] = section?.presets ?? [];
  const current = presets.find((item) => String(item.preset ?? "") === preset) ?? null;
  const isCustom = preset === "custom";

  /** Choosing a preset fills the two optional fields with that provider's documented facts. */
  const choosePreset = (value: string) => {
    setPreset(value);
    const chosen = presets.find((item) => String(item.preset ?? "") === value) ?? null;
    setBaseUrl(String(chosen?.base_url ?? ""));
    setModel(String(chosen?.model ?? ""));
    setMessage("");
    setFailure(null);
    setNotice("");
  };

  const add = async () => {
    setBusy("add");
    setMessage("");
    setFailure(null);
    setNotice("");
    const outcome = await addProviderKey({
      preset,
      ...(isCustom ? { provider_id: providerId } : {}),
      base_url: baseUrl,
      model,
      api_key: keyValue,
    });
    setBusy("");
    if (!outcome.ok) {
      setFailure(outcome.failure);
      return;
    }
    // The key was stored. It is not kept in this component's state, not even masked --
    // and the input is REMOUNTED (`keyEpoch`), because clearing a controlled input's
    // value leaves React's own `value` attribute behind: the field would look empty while
    // the serialized DOM still carried the key. Measured over CDP, not theorized.
    setKeyValue("");
    setKeyEpoch((previous) => previous + 1);
    if (outcome.value.section) setSection(outcome.value.section);
    const added = outcome.value.provider ?? null;
    setNotice(
      t(lang, "settings.providers.added")
        .replace("{provider}", String(added?.label ?? added?.provider_id ?? ""))
        .replace("{state}", stateWord(lang, added?.state))
    );
    setMessage(String(outcome.value.warning ?? ""));
  };

  const remove = async (entry: ProviderKeyEntry) => {
    const id = String(entry.provider_id ?? "");
    if (id === "") return;
    setBusy(`delete:${id}`);
    setMessage("");
    setFailure(null);
    setNotice("");
    const outcome = await deleteProviderKey(id);
    setBusy("");
    if (!outcome.ok) {
      setFailure(outcome.failure);
      return;
    }
    if (outcome.value.section) setSection(outcome.value.section);
    const extra = String(outcome.value.notice ?? "");
    setNotice(
      (extra !== ""
        ? t(lang, "settings.providers.removed.env")
            .replace("{provider}", String(entry.label ?? id))
            .replace("{secret}", String(entry.secret_name ?? ""))
        : t(lang, "settings.providers.removed").replace(
            "{provider}",
            String(entry.label ?? id)
          )) + (extra !== "" ? ` ${extra}` : "")
    );
  };

  return (
    <div data-testid="settings-providers">
    <Card className="rise d1">
      <CardHead
        title={t(lang, "settings.providers.title")}
        hint={t(lang, "settings.providers.hint")}
        action={
          <Badge tone="info">{section?.credential_store ?? "Credential Manager"}</Badge>
        }
      />
      <div className="stack" style={{ gap: 12 }}>
        <label className="field">
          <span className="label">{t(lang, "settings.providers.preset")}</span>
          <select
            className="input"
            value={preset}
            onChange={(event) => choosePreset(event.target.value)}
            data-testid="provider-preset"
          >
            {(presets.length > 0
              ? presets
              : [{ preset: "deepseek", label: "DeepSeek" } as ProviderPreset]
            ).map((item) => (
              <option key={String(item.preset ?? "")} value={String(item.preset ?? "")}>
                {String(item.label ?? item.preset ?? "")}
              </option>
            ))}
          </select>
        </label>
        {isCustom ? (
          <label className="field">
            <span className="label">{t(lang, "settings.providers.provider_id")}</span>
            <input
              className="input"
              type="text"
              value={providerId}
              spellCheck={false}
              autoComplete="off"
              onChange={(event) => setProviderId(event.target.value)}
              placeholder="my-gateway"
              data-testid="provider-id"
            />
            <span className="small muted">
              {t(lang, "settings.providers.provider_id.hint")}
            </span>
          </label>
        ) : null}
        <label className="field">
          <span className="label">{t(lang, "settings.providers.base_url")}</span>
          <input
            className="input"
            type="text"
            value={baseUrl}
            spellCheck={false}
            autoComplete="off"
            onChange={(event) => setBaseUrl(event.target.value)}
            placeholder={String(current?.base_url ?? "")}
            data-testid="provider-base-url"
          />
          <span className="small muted">{t(lang, "settings.providers.base_url.hint")}</span>
        </label>
        <label className="field">
          <span className="label">{t(lang, "settings.providers.model")}</span>
          <input
            className="input"
            type="text"
            value={model}
            spellCheck={false}
            autoComplete="off"
            onChange={(event) => setModel(event.target.value)}
            placeholder={String(current?.model ?? "")}
            data-testid="provider-model"
          />
          <span className="small muted">{t(lang, "settings.providers.model.hint")}</span>
        </label>
        <label className="field">
          <span className="label">{t(lang, "settings.providers.key")}</span>
          {/* A password input, with autocomplete off: the key is typed once and never
              rendered back -- not from state, not from a response (none carries it). */}
          <input
            key={keyEpoch}
            className="input"
            type="password"
            value={keyValue}
            ref={keyInput}
            autoComplete="off"
            spellCheck={false}
            onChange={(event) => setKeyValue(event.target.value)}
            placeholder="sk-…"
            data-testid="provider-api-key"
          />
          <span className="small muted">{t(lang, "settings.providers.key.hint")}</span>
        </label>
        <div className="row" style={{ gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          <span data-testid="provider-add">
            <Button
              size="small"
              tone="primary"
              disabled={busy === "add" || keyValue.trim() === ""}
              disabledReason={
                keyValue.trim() === "" ? t(lang, "settings.providers.key.empty") : undefined
              }
              onClick={() => void add()}
            >
              {busy === "add"
                ? t(lang, "settings.providers.adding")
                : t(lang, "settings.providers.add")}
            </Button>
          </span>
          {isCustom ? (
            <span className="small muted">
              {t(lang, "settings.providers.provider_id.hint")}
            </span>
          ) : null}
        </div>
        {notice !== "" ? (
          <div className="notice" data-testid="provider-notice">
            {notice}
          </div>
        ) : null}
        {message !== "" ? (
          <div className="notice warn" data-testid="provider-message">
            {message}
          </div>
        ) : null}
        {failure !== null ? (
          <div className="notice warn" data-testid="provider-failure">
            <div>{`HTTP ${failure.status}${failure.code ? ` · ${failure.code}` : ""}`}</div>
            {failure.detail ? (
              <div className="small mono" style={{ marginTop: 6, whiteSpace: "pre-wrap" }}>
                {failure.detail}
              </div>
            ) : null}
          </div>
        ) : null}

        <div className="stack" style={{ gap: 6 }} data-testid="provider-list">
          <div className="label">{t(lang, "settings.providers.list.title")}</div>
          {(section?.providers ?? []).length === 0 ? (
            <div className="small muted">{t(lang, "settings.providers.empty")}</div>
          ) : (
            (section?.providers ?? []).map((entry) => {
              const id = String(entry.provider_id ?? "");
              const state = String(entry.state ?? "");
              const source =
                entry.has_key === true
                  ? entry.key_source === "vault"
                    ? t(lang, "settings.providers.key.in_vault")
                    : t(lang, "settings.providers.key.in_env")
                  : state === "ready"
                    ? t(lang, "settings.providers.key.unneeded")
                    : t(lang, "settings.providers.key.none");
              return (
                <div
                  className="row between"
                  key={id}
                  style={{ gap: 10, flexWrap: "wrap", alignItems: "center" }}
                  data-testid={`provider-row-${id}`}
                >
                  <span className="row" style={{ gap: 8, flexWrap: "wrap", alignItems: "center" }}>
                    <span data-testid={`provider-state-${id}`}>
                      <Badge tone={STATE_TONE[state] ?? "neutral"}>
                        {stateWord(lang, state)}
                      </Badge>
                    </span>
                    <span className="small">{String(entry.label ?? id)}</span>
                    <span className="small muted mono">{id}</span>
                    <span className="small muted">{source}</span>
                    {String(entry.model ?? "") !== "" ? (
                      <span className="small muted mono">{String(entry.model)}</span>
                    ) : null}
                  </span>
                  <span data-testid={`provider-delete-${id}`}>
                    <Button
                      size="small"
                      tone="ghost"
                      disabled={busy === `delete:${id}` || entry.has_key !== true}
                      disabledReason={
                        entry.has_key === true ? undefined : t(lang, "settings.providers.key.none")
                      }
                      onClick={() => void remove(entry)}
                    >
                      {busy === `delete:${id}`
                        ? t(lang, "settings.providers.deleting")
                        : t(lang, "settings.providers.delete")}
                    </Button>
                  </span>
                  {state === "unsupported" ? (
                    <span className="small muted" style={{ flexBasis: "100%" }}>
                      {t(lang, "settings.providers.unsupported.note")}
                    </span>
                  ) : null}
                  {state === "disabled" ? (
                    <span className="small muted" style={{ flexBasis: "100%" }}>
                      {t(lang, "settings.providers.disabled.note")}
                    </span>
                  ) : null}
                </div>
              );
            })
          )}
          <div className="small muted">
            {t(lang, "settings.providers.storage")
              .replace("{path}", String(section?.settings_path ?? ".forge/settings.json"))
              .replace("{store}", String(section?.credential_store ?? "Credential Manager"))}
          </div>
        </div>
      </div>
    </Card>
    </div>
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
            ? "Внешний вид, папка проектов, провайдеры и ключи"
            : "Appearance, the projects folder, providers and keys"}
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

      {/* «Провайдеры и ключи»: the answer to "where do I add a provider with a key?" */}
      <ProviderKeysCard lang={lang} />

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
              ? "Ключи провайдеров читаются из окружения, .env и хранилища учётных данных Windows; в файлах проекта и в .forge/settings.json ключей нет."
              : "Provider keys are read from the environment, .env and the Windows Credential Manager; no key is stored in project files or in .forge/settings.json."}
          </div>
        </div>
      </Card>
    </div>
  </div>
);
