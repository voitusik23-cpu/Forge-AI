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
  type WindowPlanMemoryEntry,
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
 * `2026-10-29T00:43:40+00:00` → `29.10`: the day this machine measured the browser.
 *
 * The memory stores an ISO timestamp because a person reading `.forge/settings.json` needs
 * one; the card shows the day and month, which is what the owner asked to see.
 */
const memoryDate = (value?: string) => {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(value ?? ""));
  return match ? `${match[3]}.${match[2]}` : "";
};

/** The entries «Настройки» prints: only the browsers marked as unable to start a sandbox. */
const rememberedBrowsers = (
  entries?: WindowPlanMemoryEntry[]
): WindowPlanMemoryEntry[] =>
  (entries ?? []).filter((entry) => entry?.no_sandbox_required === true);

/**
 * «Как открываются окна»: the policy of a project's window, and the one switch that can
 * forbid its fallback.
 *
 * The line is the policy the backend really applies (`app/projects/app_window.py`): the
 * first attempt at a browser always keeps its sandbox; only a browser that produced no
 * window is retried **once** without it, and that retry is written into the project's
 * `launch.log`. The switch is the owner's veto: off means the launch refuses honestly
 * instead of starting an unsandboxed browser. It is read at every launch, so the next
 * press obeys a change made here without a restart.
 *
 * Below the switch is what this machine has **measured** about its browsers
 * (`window_plan_memory`): a browser whose own sandbox cannot start here is remembered, and
 * then the next launch goes straight to the no-sandbox plan — no blank white window of a
 * doomed attempt. «Забыть» clears that memory, so the next launch measures afresh; the
 * memory is never shown as a promise the owner cannot undo.
 */
const WindowPolicyCard = ({ lang }: { lang: Lang }) => {
  const [allowed, setAllowed] = useState<boolean | null>(null);
  const [memory, setMemory] = useState<WindowPlanMemoryEntry[]>([]);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [failure, setFailure] = useState<Failure | null>(null);

  const load = useCallback(async () => {
    const outcome = await loadSettings();
    if (!outcome.ok) {
      setFailure(outcome.failure);
      return;
    }
    setFailure(null);
    setAllowed(outcome.value.window_downgrade_allowed !== false);
    setMemory(rememberedBrowsers(outcome.value.window_plan_memory));
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const save = async (value: boolean) => {
    setBusy(true);
    setNotice("");
    setFailure(null);
    const outcome = await saveProjectRoot({ window_downgrade_allowed: value });
    setBusy(false);
    if (!outcome.ok) {
      setFailure(outcome.failure);
      return;
    }
    const next = outcome.value.window_downgrade_allowed !== false;
    setAllowed(next);
    setMemory(rememberedBrowsers(outcome.value.window_plan_memory));
    setNotice(
      t(lang, "settings.windows.saved").replace(
        "{state}",
        t(lang, next ? "settings.windows.allowed" : "settings.windows.forbidden")
      )
    );
  };

  /**
   * «Забыть»: clear every remembered browser. The next launch tries the sandbox again and
   * measures afresh — which is the only honest way to undo a fact this machine measured.
   */
  const forget = async () => {
    setBusy(true);
    setNotice("");
    setFailure(null);
    const outcome = await saveProjectRoot({ forget_window_plan_memory: true });
    setBusy(false);
    if (!outcome.ok) {
      setFailure(outcome.failure);
      return;
    }
    setMemory(rememberedBrowsers(outcome.value.window_plan_memory));
    setNotice(t(lang, "settings.windows.forgotten"));
  };

  const enabled = allowed !== false;

  return (
    <Card className="rise d1">
      <CardHead
        title={t(lang, "settings.windows.title")}
        hint={t(lang, "settings.windows.hint")}
        action={
          <Badge tone={enabled ? "ok" : "warn"}>
            {t(lang, enabled ? "settings.windows.allowed" : "settings.windows.forbidden")}
          </Badge>
        }
      />
      <div className="stack" style={{ gap: 12 }} data-testid="settings-window-policy">
        <div className="small" data-testid="settings-window-policy-line">
          {t(lang, "settings.windows.policy")}
        </div>
        <label className="row" style={{ gap: 10 }}>
          <input
            type="checkbox"
            checked={enabled}
            disabled={busy || allowed === null}
            onChange={(event) => void save(event.target.checked)}
            data-testid="settings-window-downgrade"
          />
          <span>
            {t(lang, "settings.windows.allow")}
            <span className="small muted" style={{ display: "block" }}>
              {t(lang, "settings.windows.allow.hint")}
            </span>
          </span>
        </label>
        {/* What this machine remembers, in the owner's own words: which browser, how many
            failures in a row, and the day it was measured. */}
        <div className="stack" style={{ gap: 6 }} data-testid="settings-window-memory">
          <div className="small muted">{t(lang, "settings.windows.memory.title")}</div>
          {memory.length === 0 ? (
            <div className="small" data-testid="settings-window-memory-empty">
              {t(lang, "settings.windows.memory.empty")}
            </div>
          ) : (
            memory.map((entry) => {
              const program = String(entry.program ?? "");
              const failures = Number(entry.sandbox_failures ?? 0);
              return (
                <div
                  className="small"
                  key={program}
                  data-testid="settings-window-memory-line"
                >
                  {t(
                    lang,
                    failures === 1
                      ? "settings.windows.memory.line.one"
                      : "settings.windows.memory.line"
                  )
                    .replace("{program}", program)
                    .replace("{count}", String(failures))
                    .replace("{date}", memoryDate(entry.failed_at))}
                  {entry.reason ? (
                    <span className="muted">
                      {" · "}
                      {t(lang, "settings.windows.memory.reason").replace(
                        "{reason}",
                        String(entry.reason)
                      )}
                    </span>
                  ) : null}
                </div>
              );
            })
          )}
          <div className="small muted">{t(lang, "settings.windows.memory.hint")}</div>
          <div
            className="row"
            style={{ gap: 8, flexWrap: "wrap", alignItems: "center" }}
          >
            <span data-testid="settings-window-forget">
              <Button
                size="small"
                tone="ghost"
                disabled={busy || memory.length === 0}
                onClick={() => void forget()}
              >
                {t(lang, "settings.windows.forget")}
              </Button>
            </span>
            <span className="small muted">{t(lang, "settings.windows.forget.hint")}</span>
          </div>
        </div>
        {notice !== "" ? (
          <div className="notice" data-testid="settings-window-notice">
            {notice}
          </div>
        ) : null}
        {failure !== null ? (
          <div className="notice warn" data-testid="settings-window-failure">
            <div>{`HTTP ${failure.status}${failure.code ? ` · ${failure.code}` : ""}`}</div>
            {failure.detail ? (
              <div className="small mono" style={{ marginTop: 6, whiteSpace: "pre-wrap" }}>
                {failure.detail}
              </div>
            ) : null}
          </div>
        ) : null}
        <div className="small muted">{t(lang, "settings.windows.storage")}</div>
      </div>
    </Card>
  );
};

/**
 * «Вопросы по смыслу задачи (через модель)»: the optional model path of the
 * clarification card, **off by default**.
 *
 * The questions themselves are written by the deterministic domain banks
 * (`app.discovery.banks`) and cost nothing. This switch only allows a ready provider to
 * propose up to three extra questions about the same task: they are validated against
 * the bank (`is_relevant`) and shown first. The switch is read on every clarification
 * round, so turning it off takes effect on the next round without a restart — and while
 * it is off, no provider is called at all.
 *
 * The cost line is not a decoration: the owner earlier forbade model-written questions,
 * and this is the one place he can allow them, knowingly.
 */
const ModelQuestionsCard = ({ lang }: { lang: Lang }) => {
  const [allowed, setAllowed] = useState<boolean | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [failure, setFailure] = useState<Failure | null>(null);

  const load = useCallback(async () => {
    const outcome = await loadSettings();
    if (!outcome.ok) {
      setFailure(outcome.failure);
      return;
    }
    setFailure(null);
    setAllowed(outcome.value.questions_from_model === true);
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const save = async (value: boolean) => {
    setBusy(true);
    setNotice("");
    setFailure(null);
    const outcome = await saveProjectRoot({ questions_from_model: value });
    setBusy(false);
    if (!outcome.ok) {
      setFailure(outcome.failure);
      return;
    }
    const next = outcome.value.questions_from_model === true;
    setAllowed(next);
    setNotice(
      t(lang, "settings.questions_model.saved").replace(
        "{state}",
        t(lang, next ? "settings.questions_model.on" : "settings.questions_model.off")
      )
    );
  };

  const enabled = allowed === true;

  return (
    <Card className="rise d1">
      <CardHead
        title={t(lang, "settings.questions_model.title")}
        hint={t(lang, "settings.questions_model.hint")}
        action={
          <Badge tone={enabled ? "warn" : "neutral"}>
            {t(lang, enabled ? "settings.questions_model.on" : "settings.questions_model.off")}
          </Badge>
        }
      />
      <div className="stack" style={{ gap: 12 }} data-testid="settings-question-model">
        <label className="row" style={{ gap: 10 }}>
          <input
            type="checkbox"
            checked={enabled}
            disabled={busy || allowed === null}
            onChange={(event) => void save(event.target.checked)}
            data-testid="settings-question-model-toggle"
          />
          <span>
            {t(lang, "settings.questions_model.toggle")}
            <span className="small muted" style={{ display: "block" }}>
              {t(lang, "settings.questions_model.toggle.hint")}
            </span>
          </span>
        </label>
        <div className="small" data-testid="settings-question-model-cost">
          {t(lang, "settings.questions_model.cost")}
        </div>
        {notice !== "" ? (
          <div className="notice" data-testid="settings-question-model-notice">
            {notice}
          </div>
        ) : null}
        {failure !== null ? (
          <div className="notice warn" data-testid="settings-question-model-failure">
            <div>{`HTTP ${failure.status}${failure.code ? ` · ${failure.code}` : ""}`}</div>
            {failure.detail ? (
              <div className="small mono" style={{ marginTop: 6, whiteSpace: "pre-wrap" }}>
                {failure.detail}
              </div>
            ) : null}
          </div>
        ) : null}
        <div className="small muted">{t(lang, "settings.questions_model.storage")}</div>
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

/**
 * «Как выбирается модель» — the automatic routing rule, the setting in force now, and
 * the owner's cost ceiling.
 *
 * The three policy lines are read from the Core's own `status.details.routing_policy`
 * rather than repeated here, so the panel cannot describe a rule the code does not use.
 * The «сейчас действует» block names the configured default provider/model and whether
 * paid models are permitted at all.
 *
 * «Потолок тарифа» is the owner's ceiling on that automatic choice (`.forge/settings.json`
 * → `max_cost_tier`, never `.env`): `FREE` / `CHEAP` / `PAID`. It is drawn from
 * `GET /api/settings` together with one **live** sample decision — the model the router
 * would pick right now for a complex task and that model's published price per 1M input
 * tokens — because a ceiling is only tangible next to a number. Changing it takes effect
 * on the next task without a restart; an explicitly named model is still honoured and the
 * launch step warns that it costs more than the ceiling.
 *
 * When the Core answered no policy (an old build, or a disconnected backend) the panel says
 * so instead of inventing the three rows.
 */
const CEILINGS = ["free", "cheap", "paid"] as const;
type Ceiling = (typeof CEILINGS)[number];

/** The price as the owner reads it: `$0`, `$0.14`, `$16.61`; empty when unpublished. */
const usd = (value: number | null | undefined): string => {
  if (typeof value !== "number" || !Number.isFinite(value)) return "";
  if (value <= 0) return "$0";
  return `$${value}`;
};

const RoutingPolicyCard = ({ lang, status }: { lang: Lang; status: Status | null }) => {
  const details = (status?.details ?? {}) as Record<string, unknown>;
  const policy =
    details.routing_policy && typeof details.routing_policy === "object"
      ? (details.routing_policy as Record<string, unknown>)
      : null;
  const defaultProvider = String(details.default_provider ?? "").trim();
  const defaultModel = String(details.default_model ?? "").trim();
  const paid = details.allow_paid_providers;

  const [ceiling, setCeiling] = useState<string>("");
  const [preview, setPreview] = useState<NonNullable<ForgeSettings["routing_preview"]> | null>(
    null
  );
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [failure, setFailure] = useState<Failure | null>(null);

  const load = useCallback(async () => {
    const outcome = await loadSettings();
    if (!outcome.ok) {
      setFailure(outcome.failure);
      return;
    }
    setFailure(null);
    setCeiling(String(outcome.value.max_cost_tier ?? "").toLowerCase());
    setPreview(outcome.value.routing_preview ?? null);
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const save = async (value: Ceiling) => {
    setBusy(true);
    setNotice("");
    setFailure(null);
    const outcome = await saveProjectRoot({ max_cost_tier: value });
    setBusy(false);
    if (!outcome.ok) {
      setFailure(outcome.failure);
      return;
    }
    // The response carries the value the store really holds and the decision it now
    // produces, so the panel never renders a choice the backend refused.
    setCeiling(String(outcome.value.max_cost_tier ?? "").toLowerCase());
    setPreview(outcome.value.routing_preview ?? null);
    setNotice(
      t(lang, "settings.routing.ceiling.saved").replace(
        "{tier}",
        String(outcome.value.max_cost_tier ?? "").toUpperCase()
      )
    );
  };

  const rows: Array<[string, string, string]> = [
    ["SIMPLE", t(lang, "settings.routing.simple"), "route.tier.free"],
    ["MEDIUM", t(lang, "settings.routing.medium"), "route.tier.cheap"],
    ["COMPLEX", t(lang, "settings.routing.complex"), "route.tier.paid"],
  ];

  const current = ceiling === "" ? "" : ceiling.toUpperCase();
  const price = usd(preview?.unit_price_per_1m);
  const pair = preview
    ? [preview.provider ?? "", preview.model === "" ? "—" : (preview.model ?? "—")]
        .filter((part) => part !== "")
        .join(" · ")
    : "";
  const effectKey = `settings.routing.ceiling.effect.${ceiling || "cheap"}${
    price === "" && ceiling !== "free" ? ".noprice" : ""
  }`;
  const effect = t(lang, effectKey).replace("{price}", price);
  const previewLine = preview
    ? t(
        lang,
        preview.tier === "FREE"
          ? "settings.routing.ceiling.preview.free"
          : price === ""
            ? "settings.routing.ceiling.preview.unknown"
            : "settings.routing.ceiling.preview"
      )
        .replace("{pair}", pair === "" ? "—" : pair)
        .replace("{price}", price)
    : "";

  return (
    <Card className="rise d1">
      <CardHead
        title={t(lang, "settings.routing.title")}
        hint={t(lang, "settings.routing.hint")}
        action={
          <Badge
            tone={current === "PAID" ? "warn" : current === "" ? "neutral" : "ok"}
          >
            {current === "" ? "allow_paid_providers" : `≤ ${current}`}
          </Badge>
        }
      />
      <div className="stack" style={{ gap: 10 }}>
        {policy === null ? (
          <div className="small muted">{t(lang, "settings.routing.missing")}</div>
        ) : (
          rows.map(([level, sentence, tierKey]) => (
            <div className="row between" key={level} style={{ gap: 10, flexWrap: "wrap" }}>
              <span className="small">{sentence}</span>
              <span className="mono small muted">
                {String(policy[level] ?? "")}
                {` · ${t(lang, tierKey)}`}
              </span>
            </div>
          ))
        )}

        <div style={{ marginTop: 4 }}>
          <div className="label">{t(lang, "settings.routing.current")}</div>
          <div className="row" style={{ gap: 8, flexWrap: "wrap", marginTop: 6 }}>
            <Badge tone="neutral">
              {`${t(lang, "settings.routing.default_provider")}: ${defaultProvider === "" ? t(lang, "runs.unknown") : defaultProvider}`}
            </Badge>
            <Badge tone="neutral">
              {`${t(lang, "settings.routing.default_model")}: ${defaultModel === "" ? t(lang, "runs.unknown") : defaultModel}`}
            </Badge>
            <Badge tone={paid === true ? "warn" : paid === false ? "ok" : "neutral"}>
              {paid === true
                ? t(lang, "settings.routing.paid.allowed")
                : paid === false
                  ? t(lang, "settings.routing.paid.blocked")
                  : t(lang, "runs.unknown")}
            </Badge>
          </div>
        </div>

        {/* «Потолок тарифа»: the one control of this card. Its three words are the owner's,
            the numbers under it are the router's own live decision. */}
        <div className="field" style={{ marginTop: 4 }}>
          <span className="label">
            {`${t(lang, "settings.routing.ceiling.title")}: ${t(lang, "settings.routing.ceiling.free")} / ${t(lang, "settings.routing.ceiling.cheap")} / ${t(lang, "settings.routing.ceiling.paid")}`}
          </span>
          <div className="segment" data-testid="settings-cost-ceiling">
            {CEILINGS.map((value) => (
              <button
                key={value}
                type="button"
                className={ceiling === value ? "on" : ""}
                disabled={busy || ceiling === ""}
                onClick={() => void save(value)}
                data-testid={`settings-cost-ceiling-${value}`}
              >
                {t(lang, `settings.routing.ceiling.${value}`)}
              </button>
            ))}
          </div>
          <div className="small" data-testid="settings-cost-ceiling-current">
            {ceiling === "paid"
              ? lang === "ru"
                ? "Сейчас: без ограничений"
                : "Now: no limit"
              : t(lang, "settings.routing.ceiling.current").replace(
                  "{tier}",
                  current === "" ? t(lang, "runs.unknown") : current
                )}
          </div>
          <div className="small muted" data-testid="settings-cost-ceiling-effect">
            {effect}
          </div>
        </div>

        {preview ? (
          <div data-testid="settings-cost-ceiling-preview">
            <div className="small">{previewLine}</div>
            <div className="small muted" style={{ marginTop: 2 }}>
              {t(lang, "settings.routing.ceiling.sample").replace(
                "{task}",
                String(preview.sample_task ?? "")
              )}
            </div>
          </div>
        ) : null}

        {notice !== "" ? (
          <div className="notice" data-testid="settings-cost-ceiling-notice">
            {notice}
          </div>
        ) : null}
        {failure !== null ? (
          <div className="notice warn" data-testid="settings-cost-ceiling-failure">
            <div>{`HTTP ${failure.status}${failure.code ? ` · ${failure.code}` : ""}`}</div>
            {failure.detail ? (
              <div className="small mono" style={{ marginTop: 6, whiteSpace: "pre-wrap" }}>
                {failure.detail}
              </div>
            ) : null}
          </div>
        ) : null}

        <div className="small muted">{t(lang, "settings.routing.ceiling.note")}</div>
        <div className="small muted">
          {lang === "ru"
            ? "Простая задача уходит на бесплатную модель, средняя — на дешёвую, сложная — на платную и только при разрешённых платных моделях и потолке без ограничений. Выбирать модель вручную не нужно; это остаётся возможным в шаге запуска."
            : "A simple task goes to a free model, a medium one to a cheap model, and a complex one to a paid model — only when paid models are permitted and the ceiling is unlimited. There is nothing to pick by hand; naming a pair remains possible in the launch step."}
        </div>
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

      {/* «Как открываются окна»: the window policy and the veto over the unsandboxed
          retry. The sandbox always stays on the first attempt; this only forbids the
          documented fallback. */}
      <WindowPolicyCard lang={lang} />

      {/* «Вопросы по смыслу задачи (через модель)»: the optional model path of the
          questions card, off by default. It uses the owner's tokens, and the card says so
          on its own line. */}
      <ModelQuestionsCard lang={lang} />

      {/* «Как выбирается модель»: the rule automatic routing applies, with the setting that
          is in force right now. A reader who wonders «где выбрать модель?» finds the answer
          here in four lines instead of hunting for a dropdown. */}
      <RoutingPolicyCard lang={lang} status={status} />

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
