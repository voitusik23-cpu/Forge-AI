// A one-click hard reload of the whole interface.
//
// Why it exists: the application is a Chromium `--app` window, and the launcher used
// to only bring an open window to the front. After `vite build` the served asset file
// names change (Vite emits content hashes), but an already-open document keeps running
// the bundle it loaded, so the owner kept looking at the previous build while the
// server was already serving the new one.
//
// `location.replace` with a unique query loads a **new document**: `index.html` and
// every hashed asset are requested from the server instead of being answered from the
// document cache. That is what makes this a hard reload rather than a repaint.
//
// The function returns the URL it navigated to, so a test can assert the cache-busting
// parameter without depending on the browser's own state.
//
// The launcher enforces the same rule from the other side: when it finds an already
// open window it reloads it (`hard_reload_window` in `tools/forge_launch.py`), so the
// owner is never shown a document older than `ui/dist` -- whichever way the window was
// opened.

export const RELOAD_QUERY_PARAM = "__fresh";

export const hardReload = (
  target: Pick<Location, "href" | "replace"> = window.location,
  now: number = Date.now()
): string => {
  const url = new URL(target.href);
  url.searchParams.set(RELOAD_QUERY_PARAM, String(now));
  const next = url.toString();
  target.replace(next);
  return next;
};
