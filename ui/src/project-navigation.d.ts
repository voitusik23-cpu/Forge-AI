/**
 * The address a project screen has: `#/projects/<id>`.
 *
 * `projectView` is the single place that decides which screen an address names, so the
 * screen cannot disagree with what the test asserts about it.
 */
export const PROJECT_LIST_HASH: string;

export const PROJECT_HASH_PREFIX: string;

export type ProjectView =
  | { view: "list"; projectId: "" }
  | { view: "detail"; projectId: string };

/** Turn a project id into its address (`#/projects/<encoded id>`). */
export function projectHash(projectId: unknown): string;

/**
 * Resolve an address to a screen. An address this app does not know resolves to the
 * list, never to a blank screen.
 */
export function projectView(hash: unknown): ProjectView;

/** The slice of `window` the router uses: a fake with these members drives the test. */
export type ProjectRouterTarget = {
  location: { hash: string };
  history: {
    back: () => void;
    replaceState?: (data: unknown, unused: string, url?: string) => void;
  };
  addEventListener: (type: string, listener: (event: unknown) => void) => void;
  removeEventListener: (type: string, listener: (event: unknown) => void) => void;
};

export type ProjectRouter = {
  /** The view the address currently names. */
  current: () => ProjectView;
  /** Navigate to `#/projects/<id>`: one history entry, Back returns to the list. */
  open: (projectId: string) => ProjectView;
  /** Navigate to the list (`#/projects`). */
  showList: () => ProjectView;
  /** The browser's own Back. */
  back: () => void;
  /** Observe every resolved view change; the returned function unsubscribes. */
  subscribe: (listener: (view: ProjectView) => void) => () => void;
  /** Remove the page listeners. */
  dispose: () => void;
};

export function createProjectRouter(target: ProjectRouterTarget): ProjectRouter;
