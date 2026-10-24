// The address of a project screen: `#/projects/<id>`.
//
// The measured complaint (twice): the owner pressed «Открыть» on the «Почтовый центр»
// row and *nothing happened*. The previous attempt only selected the id and scrolled to
// a card that renders **below** the list, so with the list on screen the click still
// looked inert. A selection is not a place: this module gives the project view its own
// address, so «Открыть» is a navigation the browser itself understands --
//
//   * the hash changes to `#/projects/<id>`;
//   * the address is a history entry, so Back returns to the list;
//   * reloading the page with that hash lands on the same project;
//   * an address the app does not know is the list, never a blank screen.
//
// This module is deliberately plain ESM JavaScript (with `project-navigation.d.ts` for
// the types) so the regression test in `tests/test_project_navigation.py` executes
// *this* code with Node: it has no React and no DOM of its own, and it receives the
// page's `location`/`history`/event target from the caller.

/** The address of the project list: what «← Назад к проектам» navigates to. */
export const PROJECT_LIST_HASH = "#/projects";

/** The prefix of one project's address. A project id is appended, URL-encoded. */
export const PROJECT_HASH_PREFIX = "#/projects/";

/** How a project's id becomes an address. */
export function projectHash(projectId) {
  const id = typeof projectId === "string" ? projectId.trim() : "";
  if (id === "") return PROJECT_LIST_HASH;
  return PROJECT_HASH_PREFIX + encodeURIComponent(id);
}

/**
 * Resolve an address to the screen it names.
 *
 * @param {unknown} hash the page's `location.hash` (with or without the leading `#`)
 * @returns {{ view: "list", projectId: "" } | { view: "detail", projectId: string }}
 */
export function projectView(hash) {
  const raw = typeof hash === "string" ? hash : "";
  const value = raw.startsWith("#") ? raw.slice(1) : raw;
  // A query string may travel after the path (`#/projects?x=1`); it never names a project.
  const path = value.split("?")[0] ?? "";
  if (path === "" || path === "/" || path === "/projects") {
    return { view: "list", projectId: "" };
  }
  if (path.startsWith("/projects/")) {
    const encoded = path.slice("/projects/".length);
    // A project id is one path segment. A raw `/` (or an empty segment) means the
    // address names no project this app has: fall back to the list, not to a blank area.
    if (encoded === "" || encoded.includes("/")) return { view: "list", projectId: "" };
    let id = encoded;
    try {
      id = decodeURIComponent(encoded);
    } catch {
      return { view: "list", projectId: "" };
    }
    id = id.trim();
    if (id === "") return { view: "list", projectId: "" };
    return { view: "detail", projectId: id };
  }
  // `#/something-else`, `#foo`, a legacy address: the list is the honest answer.
  return { view: "list", projectId: "" };
}

/**
 * The one navigation object the «Проекты» screen talks to.
 *
 * @param {{
 *   location: { hash: string },
 *   history: { back: () => void, replaceState?: (data: unknown, unused: string, url?: string) => void },
 *   addEventListener: (type: string, listener: (event: unknown) => void) => void,
 *   removeEventListener: (type: string, listener: (event: unknown) => void) => void,
 * }} target the page's `window` (a fake with the same four members in the test)
 */
export function createProjectRouter(target) {
  const { location, history } = target;
  /** @type {Array<(view: ReturnType<typeof projectView>) => void>} */
  const listeners = [];
  let current = projectView(location.hash);

  const same = (left, right) => left.view === right.view && left.projectId === right.projectId;

  const publish = (next) => {
    if (same(next, current)) return current;
    current = next;
    // A copy per listener: a subscriber cannot mutate the router's own state.
    for (const listener of [...listeners]) listener({ view: current.view, projectId: current.projectId });
    return current;
  };

  const sync = () => publish(projectView(location.hash));
  const onAddressChange = () => sync();

  // Back/Forward and a hand-edited address both arrive this way. `popstate` is what a
  // history traversal fires; `hashchange` is what a plain hash edit fires.
  target.addEventListener("hashchange", onAddressChange);
  target.addEventListener("popstate", onAddressChange);

  const navigate = (hash) => {
    if (location.hash === hash) {
      // The address is already the one asked for: nothing to push, but the view is it.
      return publish(projectView(hash));
    }
    // Assigning `location.hash` is a real navigation: it pushes one history entry and
    // fires `hashchange`, so Back returns to the screen the owner came from.
    location.hash = hash;
    // Resolve once more: some embedders dispatch `hashchange` on a later task, and the
    // click must change the screen within the same frame either way.
    return publish(projectView(location.hash));
  };

  return {
    /** The view the address currently names. */
    current: () => ({ view: current.view, projectId: current.projectId }),

    /** Navigate to one project: the project screen, at its own address. */
    open(projectId) {
      const id = typeof projectId === "string" ? projectId.trim() : "";
      if (id === "") return current;
      return navigate(projectHash(id));
    },

    /** Navigate back to the list (also a real history entry, so Back works from it). */
    showList() {
      return navigate(PROJECT_LIST_HASH);
    },

    /** The browser's own Back: returns to the previous address in the history. */
    back() {
      history.back();
    },

    /** Subscribe to every resolved view change; returns the unsubscribe function. */
    subscribe(listener) {
      listeners.push(listener);
      return () => {
        const index = listeners.indexOf(listener);
        if (index >= 0) listeners.splice(index, 1);
      };
    },

    /** Remove the page listeners (the screen unmounting). */
    dispose() {
      target.removeEventListener("hashchange", onAddressChange);
      target.removeEventListener("popstate", onAddressChange);
      listeners.length = 0;
    },
  };
}
