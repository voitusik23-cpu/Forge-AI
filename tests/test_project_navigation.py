"""«Открыть» must change the screen: the project view has its own address.

The owner's second report of the same dead click: *«Нажимаю на список проектов —
„Почтовый центр“ написано „готово“ — потом кнопку Открыть — нажимаю „Открыть“ ничего
не происходит»*. The previous fix selected the id and scrolled to a card that renders
**below** the list; with the list on screen the click still looked inert. A selection is
not a place.

The fix is an address: the project screen lives at ``#/projects/<id>``, so «Открыть» is a
navigation the browser itself understands -- the hash changes, the address is a history
entry, Back returns to the list, a reload with the same hash lands on the same project,
and an address the app does not know is the list rather than a blank screen.

The rule itself is ``ui/src/project-navigation.js`` -- plain ESM with no React, no DOM
and no network -- so it is **executed** here with Node against a tiny fake of the page's
``location``/``history``/event target, instead of being re-implemented in Python. The
browser half (a real click, a pixel difference and the DOM) is photographed separately
over CDP; see ``.forge/nav2_cdp_proof.py`` and ``.forge/screenshots/nav2-*.png``.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import unittest

REPOSITORY = pathlib.Path(__file__).resolve().parents[1]
MODULE = REPOSITORY / "ui" / "src" / "project-navigation.js"

#: The project the owner reported. Its address is one path segment.
PROJECT_ID = "proj-fce53337245f"

#: A page fake with the four members the production router uses. `location.hash`
#: assignments are history pushes (as in every browser), `history.back()` restores the
#: previous entry, and both dispatch the events the router listens to.
HARNESS = r"""
function fakeWindow(initialHash) {
  const listeners = { hashchange: [], popstate: [] };
  const state = { hash: initialHash || "" };
  const fire = (type) => {
    for (const handler of listeners[type] || []) handler({ type, hash: state.hash });
  };
  const history = {
    entries: [state.hash],
    index: 0,
    back() {
      if (history.index <= 0) return false;
      history.index -= 1;
      state.hash = history.entries[history.index];
      fire("popstate");
      fire("hashchange");
      return true;
    },
    replaceState(_data, _unused, url) {
      const next = String(url == null ? "" : url);
      history.entries[history.index] = next;
      state.hash = next;
      fire("hashchange");
    },
  };
  return {
    location: {
      get hash() {
        return state.hash;
      },
      set hash(value) {
        const next = String(value == null ? "" : value);
        if (next === state.hash) return;
        history.entries = history.entries.slice(0, history.index + 1);
        history.entries.push(next);
        history.index = history.entries.length - 1;
        state.hash = next;
        fire("hashchange");
      },
    },
    history,
    addEventListener(type, handler) {
      (listeners[type] = listeners[type] || []).push(handler);
    },
    removeEventListener(type, handler) {
      listeners[type] = (listeners[type] || []).filter((item) => item !== handler);
    },
  };
}

function follow(window) {
  const seen = [];
  const router = createProjectRouter(window);
  const unsubscribe = router.subscribe((view) => seen.push(view));
  return { window, router, seen, unsubscribe };
}
"""


class ProjectNavigationTests(unittest.TestCase):
    """The address rule, executed as the production module."""

    def _run(self, body: str) -> dict:
        node = shutil.which("node")
        if node is None:
            self.skipTest("node is not installed; the navigation module cannot be executed")
        script = (
            "import { PROJECT_LIST_HASH, PROJECT_HASH_PREFIX, projectHash, projectView, "
            f"createProjectRouter }} from {json.dumps(MODULE.as_uri())};\n"
            + HARNESS
            + "\nconst results = {};\n"
            + body
            + "\nprocess.stdout.write(JSON.stringify(results));\n"
        )
        completed = subprocess.run(
            [node, "--input-type=module", "-e", script],
            cwd=str(REPOSITORY),
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return json.loads(completed.stdout)

    def test_01_a_project_address_is_one_path_segment(self) -> None:
        answer = self._run(
            f"""
const id = {json.dumps(PROJECT_ID)};
results.hash = projectHash(id);
results.prefix = PROJECT_HASH_PREFIX;
results.listHash = PROJECT_LIST_HASH;
results.view = projectView(projectHash(id));
results.roundTrip = projectView(projectHash(id)).projectId === id;
results.emptyId = projectHash("");
results.spaced = projectHash(" id with a space ");
results.spacedView = projectView(projectHash(" id with a space "));
"""
        )
        self.assertEqual(answer["hash"], f"#/projects/{PROJECT_ID}")
        self.assertEqual(answer["prefix"], "#/projects/")
        self.assertEqual(answer["listHash"], "#/projects")
        self.assertEqual(answer["view"], {"view": "detail", "projectId": PROJECT_ID})
        self.assertTrue(answer["roundTrip"])
        self.assertEqual(answer["emptyId"], "#/projects")
        # An id with a space travels percent-encoded and comes back unchanged.
        self.assertEqual(answer["spaced"], "#/projects/id%20with%20a%20space")
        self.assertEqual(
            answer["spacedView"], {"view": "detail", "projectId": "id with a space"}
        )

    def test_02_open_sets_the_hash_and_the_router_resolves_the_detail(self) -> None:
        answer = self._run(
            f"""
const page = fakeWindow("");
const screen = follow(page);
results.before = screen.router.current();
screen.router.open({json.dumps(PROJECT_ID)});
results.hash = page.location.hash;
results.current = screen.router.current();
results.notified = screen.seen;
results.entries = page.history.entries;
results.index = page.history.index;
"""
        )
        self.assertEqual(answer["before"], {"view": "list", "projectId": ""})
        self.assertEqual(answer["hash"], f"#/projects/{PROJECT_ID}")
        # The screen the router says to draw is the project, resolved *from the hash*.
        self.assertEqual(answer["current"], {"view": "detail", "projectId": PROJECT_ID})
        self.assertEqual(
            answer["notified"], [{"view": "detail", "projectId": PROJECT_ID}]
        )
        self.assertEqual(answer["entries"], ["", f"#/projects/{PROJECT_ID}"])
        self.assertEqual(answer["index"], 1)

    def test_03_back_returns_to_the_list(self) -> None:
        answer = self._run(
            f"""
const page = fakeWindow("#/projects");
const screen = follow(page);
screen.router.open({json.dumps(PROJECT_ID)});
results.detail = screen.router.current();
screen.router.back();
results.hashAfterBack = page.location.hash;
results.viewAfterBack = screen.router.current();
results.notified = screen.seen;
"""
        )
        self.assertEqual(answer["detail"], {"view": "detail", "projectId": PROJECT_ID})
        self.assertEqual(answer["hashAfterBack"], "#/projects")
        self.assertEqual(answer["viewAfterBack"], {"view": "list", "projectId": ""})
        self.assertEqual(
            answer["notified"],
            [
                {"view": "detail", "projectId": PROJECT_ID},
                {"view": "list", "projectId": ""},
            ],
        )

    def test_04_an_unknown_address_is_the_list_not_a_blank_screen(self) -> None:
        answer = self._run(
            """
const addresses = [
  "", "#", "#/", "#/projects", "#/projects?x=1", "#/nowhere", "#foo",
  "#/projects/", "#/projects/a/b", "#/projects/%E0%A4%A",
];
results.views = Object.fromEntries(addresses.map((hash) => [hash, projectView(hash)]));
const page = fakeWindow("#/nowhere");
const screen = follow(page);
results.current = screen.router.current();
results.hash = page.location.hash;
"""
        )
        for address, view in answer["views"].items():
            self.assertEqual(
                view,
                {"view": "list", "projectId": ""},
                f"address {address!r} must fall back to the list",
            )
        # Opening the app on an address it does not know shows the list, not an empty page.
        self.assertEqual(answer["current"], {"view": "list", "projectId": ""})
        self.assertEqual(answer["hash"], "#/nowhere")

    def test_05_a_reload_with_the_same_hash_shows_the_same_project(self) -> None:
        answer = self._run(
            f"""
const page = fakeWindow("");
const first = follow(page);
first.router.open({json.dumps(PROJECT_ID)});
// A reload: the old screen unmounts, a new router reads the same address.
first.unsubscribe();
first.router.dispose();
const afterReload = follow(page);
results.hash = page.location.hash;
results.reloaded = afterReload.router.current();
results.reloadedView = projectView(page.location.hash);
"""
        )
        self.assertEqual(answer["hash"], f"#/projects/{PROJECT_ID}")
        self.assertEqual(
            answer["reloaded"], {"view": "detail", "projectId": PROJECT_ID}
        )
        self.assertEqual(
            answer["reloadedView"], {"view": "detail", "projectId": PROJECT_ID}
        )

    def test_06_show_list_navigates_to_the_list_address(self) -> None:
        answer = self._run(
            f"""
const page = fakeWindow("");
const screen = follow(page);
screen.router.open({json.dumps(PROJECT_ID)});
results.backHash = screen.router.showList();
results.hash = page.location.hash;
results.current = screen.router.current();
results.seen = screen.seen.length;
"""
        )
        self.assertEqual(answer["backHash"], {"view": "list", "projectId": ""})
        self.assertEqual(answer["hash"], "#/projects")
        self.assertEqual(answer["current"], {"view": "list", "projectId": ""})
        self.assertEqual(answer["seen"], 2)

    def test_07_an_address_edited_by_the_browser_changes_the_screen(self) -> None:
        # A hand-edited hash, a bookmark or the browser's own Back all change
        # `location.hash`; the router listens and resolves whatever it now names. This is
        # the same path `App.tsx` relies on when it clears a project hash with
        # `history.replaceState` on leaving the section.
        answer = self._run(
            """
const firstId = "proj-aaaaaaaaaaaa";
const page = fakeWindow("");
const screen = follow(page);
screen.router.open(firstId);
results.detail = screen.router.current();
page.location.hash = "#/projects";
results.afterEdit = screen.router.current();
results.notified = screen.seen;
page.location.hash = "#/projects/proj-cccccccccccc";
results.other = screen.router.current();
"""
        )
        self.assertEqual(
            answer["detail"], {"view": "detail", "projectId": "proj-aaaaaaaaaaaa"}
        )
        self.assertEqual(answer["afterEdit"], {"view": "list", "projectId": ""})
        self.assertEqual(
            answer["notified"],
            [
                {"view": "detail", "projectId": "proj-aaaaaaaaaaaa"},
                {"view": "list", "projectId": ""},
                {"view": "detail", "projectId": "proj-cccccccccccc"},
            ],
        )
        self.assertEqual(
            answer["other"], {"view": "detail", "projectId": "proj-cccccccccccc"}
        )

    def test_08_opening_another_project_replaces_the_screen_and_the_address(self) -> None:
        answer = self._run(
            """
const firstId = "proj-aaaaaaaaaaaa";
const secondId = "proj-bbbbbbbbbbbb";
const page = fakeWindow("#/projects");
const screen = follow(page);
screen.router.open(firstId);
screen.router.open(secondId);
results.current = screen.router.current();
results.hash = page.location.hash;
results.entries = page.history.entries;
"""
        )
        self.assertEqual(
            answer["current"], {"view": "detail", "projectId": "proj-bbbbbbbbbbbb"}
        )
        self.assertEqual(answer["hash"], "#/projects/proj-bbbbbbbbbbbb")
        self.assertEqual(
            answer["entries"],
            ["#/projects", "#/projects/proj-aaaaaaaaaaaa", "#/projects/proj-bbbbbbbbbbbb"],
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
