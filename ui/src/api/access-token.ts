/**
 * The access token a remote Forge deployment requires.
 *
 * A loopback deployment has no token and this module is inert: every function answers `""`
 * and `withAccessToken` returns the headers it was given, unchanged. A deployment that has
 * a token (`FORGE_ACCESS_TOKEN` on the server, see `deploy/DEPLOY.md`) hands it to the
 * browser **once**, in the address bar (`?token=…`); the page keeps it for the tab in
 * `sessionStorage` -- not a cookie, nothing persistent, gone when the tab closes -- and
 * sends it on every request as `X-Forge-Token`.
 *
 * Three properties are deliberate:
 *
 * * the token is never part of the bundle, a config file or a default, so a built `ui/dist`
 *   carries no credential and one build serves every deployment;
 * * the URL is rewritten after the token is stored, so it does not sit in the address bar,
 *   in a shared link or in the browser's history entry for that tab;
 * * a storage that refuses (private mode, a quota) is not an error here: the token is then
 *   simply not kept, and the server's 401 says so in its own words.
 */

/** The header the Core API and the shell both accept (the other spelling is `Authorization`). */
export const ACCESS_TOKEN_HEADER = "X-Forge-Token";

/** Where the tab keeps the token. One key, one tab, no cookie. */
export const ACCESS_TOKEN_STORAGE_KEY = "forge.access-token";

/** The one-time URL parameter that carries the token from a browser line to the page. */
export const ACCESS_TOKEN_QUERY_PARAM = "token";

/** The part of `sessionStorage` this module uses; injected in a test, read from the tab otherwise. */
export type TokenStorage = {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
};

const browserStorage = (): TokenStorage | null => {
  try {
    if (typeof window === "undefined") return null;
    return window.sessionStorage ?? null;
  } catch {
    // Access to storage can throw when a browser blocks it outright.
    return null;
  }
};

/** The token in one query string (`"?token=abc"` → `"abc"`), or `""`. */
export const tokenFromSearch = (search: string): string => {
  try {
    return (new URLSearchParams(search).get(ACCESS_TOKEN_QUERY_PARAM) ?? "").trim();
  } catch {
    return "";
  }
};

/** Remember a token for this tab; an empty token forgets it. Returns what was stored. */
export const rememberAccessToken = (
  token: string,
  storage: TokenStorage | null = browserStorage()
): string => {
  const value = token.trim();
  if (!storage) return value;
  try {
    if (value) storage.setItem(ACCESS_TOKEN_STORAGE_KEY, value);
    else storage.removeItem(ACCESS_TOKEN_STORAGE_KEY);
  } catch {
    // Nothing to report: the request will be refused and the 401 says why.
  }
  return value;
};

/** The token this tab holds, or `""` when it holds none. */
export const accessToken = (storage: TokenStorage | null = browserStorage()): string => {
  if (!storage) return "";
  try {
    return (storage.getItem(ACCESS_TOKEN_STORAGE_KEY) ?? "").trim();
  } catch {
    return "";
  }
};

/** The same headers, with the token attached when this tab has one. */
export const withAccessToken = (
  headers: Record<string, string>,
  token: string = accessToken()
): Record<string, string> => (token ? { ...headers, [ACCESS_TOKEN_HEADER]: token } : headers);

/**
 * Take a token out of the address bar once, keep it for the tab and remove it from the URL.
 *
 * Called before the interface renders (`src/main.tsx`), so the very first request already
 * carries the token. Returns whether a token was found -- `true` is the normal case for a
 * freshly opened `?token=…` link and `false` for every later page load.
 */
export const captureAccessTokenFromUrl = (): boolean => {
  if (typeof window === "undefined") return false;
  const token = tokenFromSearch(window.location.search);
  if (!token) return false;
  rememberAccessToken(token);
  try {
    const url = new URL(window.location.href);
    url.searchParams.delete(ACCESS_TOKEN_QUERY_PARAM);
    window.history.replaceState({}, "", `${url.pathname}${url.search}${url.hash}`);
  } catch {
    // The token is stored already; a URL that cannot be rewritten is not a failure.
  }
  return true;
};
