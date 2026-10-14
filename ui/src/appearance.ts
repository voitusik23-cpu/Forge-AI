// Appearance state: the single place that decides theme, accent, density and motion.
//
// The switches only set data-* attributes on <html>; every colour, radius and spacing comes
// from the token sheet. The same file is read by the inline bootstrap in index.html, so the
// stored preference is applied before the first paint and there is no white flash.

export type ThemeMode = "dark" | "light" | "system";
export type Accent = "violet" | "cyan" | "amber" | "emerald";
export type Density = "comfortable" | "compact";

export type Appearance = {
  theme: ThemeMode;
  accent: Accent;
  density: Density;
  reducedMotion: boolean;
};

export const DEFAULT_APPEARANCE: Appearance = {
  theme: "dark",
  accent: "violet",
  density: "comfortable",
  reducedMotion: false,
};

export const STORAGE_KEY = "forge.appearance";

export const THEMES: ThemeMode[] = ["dark", "light", "system"];
export const ACCENTS: Accent[] = ["violet", "cyan", "amber", "emerald"];
export const DENSITIES: Density[] = ["comfortable", "compact"];

export const ACCENT_SWATCH: Record<Accent, string> = {
  violet: "#7c5cff",
  cyan: "#22d3ee",
  amber: "#f0a63c",
  emerald: "#2fbf8f",
};

const isTheme = (value: unknown): value is ThemeMode => THEMES.includes(value as ThemeMode);
const isAccent = (value: unknown): value is Accent => ACCENTS.includes(value as Accent);
const isDensity = (value: unknown): value is Density => DENSITIES.includes(value as Density);

const fromUrl = (): Partial<Appearance> => {
  // ?theme=light&accent=cyan&density=compact&motion=reduced -- applied and saved like a click.
  try {
    const params = new URLSearchParams(window.location.search);
    const result: Partial<Appearance> = {};
    const theme = params.get("theme");
    if (isTheme(theme)) result.theme = theme;
    const accent = params.get("accent");
    if (isAccent(accent)) result.accent = accent;
    const density = params.get("density");
    if (isDensity(density)) result.density = density;
    const motion = params.get("motion");
    if (motion === "reduced") result.reducedMotion = true;
    if (motion === "full") result.reducedMotion = false;
    return result;
  } catch {
    return {};
  }
};

export const loadAppearance = (): Appearance => {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return { ...DEFAULT_APPEARANCE };
    const parsed = JSON.parse(raw) as Partial<Appearance>;
    return {
      theme: isTheme(parsed.theme) ? parsed.theme : DEFAULT_APPEARANCE.theme,
      accent: isAccent(parsed.accent) ? parsed.accent : DEFAULT_APPEARANCE.accent,
      density: isDensity(parsed.density) ? parsed.density : DEFAULT_APPEARANCE.density,
      reducedMotion: Boolean(parsed.reducedMotion),
    };
  } catch {
    return { ...DEFAULT_APPEARANCE };
  }
};

/** Stored preference with any URL override on top, and the override is then persisted. */
export const loadAppearanceWithUrl = (): Appearance => {
  const stored = loadAppearance();
  const override = fromUrl();
  const merged = { ...stored, ...override };
  if (Object.keys(override).length > 0) saveAppearance(merged);
  return merged;
};

export const saveAppearance = (appearance: Appearance): boolean => {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(appearance));
    return true;
  } catch {
    // A blocked storage must not break the interface; the choice still applies for the session.
    return false;
  }
};

export const systemPrefersDark = (): boolean => {
  try {
    return window.matchMedia("(prefers-color-scheme: dark)").matches;
  } catch {
    return true;
  }
};

export const resolvesTo = (mode: ThemeMode): "dark" | "light" =>
  mode === "system" ? (systemPrefersDark() ? "dark" : "light") : mode;

export const applyAppearance = (appearance: Appearance): void => {
  const root = document.documentElement;
  root.dataset.theme = resolvesTo(appearance.theme);
  root.dataset.accent = appearance.accent;
  root.dataset.density = appearance.density;
  root.dataset.motion = appearance.reducedMotion ? "reduced" : "full";
};

export const watchSystemTheme = (onChange: () => void): (() => void) => {
  try {
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    media.addEventListener("change", onChange);
    return () => media.removeEventListener("change", onChange);
  } catch {
    return () => undefined;
  }
};
