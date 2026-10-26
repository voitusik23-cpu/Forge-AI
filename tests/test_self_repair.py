"""The repair panel: «Не запустилось: …», «Исправить автоматически» and the one-line chat.

The owner's requirement this file guards, verbatim:

    «Надо, чтобы программы запускались и шильдиком с рабочего стола, и из Forge кнопкой
    "Запустить приложение". Если проблемы — говорит об этом сразу под строкой "Запустить
    приложение" и предлагает решить. И я там мог бы с ним переписываться и писать, что надо
    сделать.»

Two kinds of evidence are produced here, and neither is a re-implementation:

1. the **client's own rules** are executed from their TypeScript source with Node
   (``ui/src/repair.ts``, plus the sentences in ``ui/src/i18n.ts``) -- the cause mapping, the
   composed instruction, the one-dispatch/one-retry loop and its cap of two attempts;
2. the **backend's own refusal** is produced by the real :class:`ProjectLauncher` over a real
   project folder and a fake supervisor: the ``409`` facts carry the redacted log tail, Forge
   appends the reason to ``<project>\\launch.log``, and a file the project's own launcher
   wrote (the desktop-shortcut path) is read back into the same facts.

Nothing here starts a browser, a child process or a provider.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import tempfile
import unittest

from app.projects.entrypoint import resolve_entry
from app.projects.launch_log import (
    LAUNCH_LOG_NAME,
    append_launch_log,
    read_launch_log_tail,
)
from app.projects.launcher import LaunchRefusal, ProjectLauncher
from app.projects.runner import RunTarget

REPOSITORY = pathlib.Path(__file__).resolve().parents[1]
UI_SOURCE = REPOSITORY / "ui" / "src"
REPAIR_TS = UI_SOURCE / "repair.ts"
I18N_TS = UI_SOURCE / "i18n.ts"
PANEL_TSX = UI_SOURCE / "components" / "RepairPanel.tsx"
PROJECTS_TSX = UI_SOURCE / "screens" / "Projects.tsx"

PROJECT_ID = "proj-083ebccfaea8"
WORKSPACE_PROJECT_ID = "self-repair-workspace"


def _node(script: str) -> str:
    """Run one ES module script with Node and return its stdout.

    ``--experimental-strip-types`` loads the real ``.ts`` file, so the rules under test are
    the ones the interface runs -- not a transcription of them.
    """

    node = shutil.which("node")
    if node is None:  # pragma: no cover - node is present on this host
        raise unittest.SkipTest("node is not installed; the panel rules cannot be executed")
    completed = subprocess.run(
        [node, "--experimental-strip-types", "--input-type=module", "-e", script],
        cwd=str(REPOSITORY),
        capture_output=True,
        text=True,
        timeout=120,
    )
    if completed.returncode != 0:
        raise AssertionError(completed.stderr or completed.stdout)
    return completed.stdout


#: Node's stdout is decoded with the host code page here, so every answer is pure ASCII
#: (``\\uXXXX`` escapes are valid JSON) and read back as UTF-8 by ``json.loads``.
_ASCII_JSON = (
    r"const __a=(v)=>JSON.stringify(v).replace(/[\u007f-\uffff]/g,"
    r"(c)=>'\\u'+c.charCodeAt(0).toString(16).padStart(4,'0'));"
)

#: The two modules the panel is made of. Both are plain data/functions: no React, no fetch.
_IMPORTS = (
    f"import * as r from {json.dumps(REPAIR_TS.as_uri())};"
    f"import {{ STRINGS }} from {json.dumps(I18N_TS.as_uri())};"
    "const fill=(s,p)=>String(s).replace(/\\{(\\w+)\\}/g,(w,k)=>Object.prototype.hasOwnProperty.call(p,k)?String(p[k]):w);"
    "const t=(lang,key)=>STRINGS[lang][key] ?? key;"
    "const clauseOf=(m,lang='ru')=>fill(t(lang,m.sentenceKey),m.params);"
    "const headlineOf=(m,lang='ru')=>fill(t(lang,m.headlineKey),{...m.params,cause:clauseOf(m,lang)});"
)


def _ask(expression: str):
    """Evaluate one expression over the real modules and return its JSON answer."""

    script = _IMPORTS + _ASCII_JSON + f"process.stdout.write(__a({expression}));"
    return json.loads(_node(script))


def _panel(facts: dict, attempts: int = 0, lang: str = "ru") -> dict:
    """The panel model for one refusal, with its sentences already resolved."""

    return _ask(
        "(() => {"
        f"const m = r.repairPanelModel({json.dumps(facts)}, {int(attempts)});"
        f"return {{kind: m.cause.kind, moduleName: m.cause.moduleName, port: m.cause.port,"
        f" lineNumber: m.cause.lineNumber, exitCode: m.cause.exitCode,"
        f" line: m.cause.line, hasRunFacts: m.hasRunFacts,"
        f" clause: clauseOf(m, {json.dumps(lang)}), headline: headlineOf(m, {json.dumps(lang)}),"
        f" visibleLines: m.visibleLines, canAutoRepair: m.canAutoRepair,"
        f" attempts: m.attempts, remaining: m.remaining, logPath: m.logPath}};"
        "})()"
    )


# --------------------------------------------------------------------------- #
# the fake supervisor the real launcher is asked for a refusal
# --------------------------------------------------------------------------- #


class _FailingRunner:
    """A supervisor whose only run died without a port; it reports the log Forge keeps."""

    def __init__(self, *, lines: list[str], exit_code: int = 1, waiting: bool = False) -> None:
        self.lines = list(lines)
        self.exit_code = int(exit_code)
        self.waiting = bool(waiting)
        self.started_calls: list[dict] = []

    def status(self, target):
        return {
            "state": "FAILED",
            "url": "",
            "exit_code": self.exit_code,
            "reason": "",
            "last_event": f"failed with exit code {self.exit_code}",
            "log": {"path": ".forge/projects/x/run.log", "lines": list(self.lines)},
        }

    def start_confirmed(self, target, *, confirm, digest, offline=False):
        self.started_calls.append({"confirm": confirm, "digest": digest})
        return self.status(target)


class _NoWindow:
    """The OS opener: a refusal path must never reach it."""

    def open(self, project_id, url):  # pragma: no cover - a refusal opens no window
        raise AssertionError(f"a refused launch must not open a window ({url})")


# --------------------------------------------------------------------------- #
# 1. the cause mapping, executed from ui/src/repair.ts
# --------------------------------------------------------------------------- #

TRACEBACK_TO_MISSING_FLASK = [
    "--- stderr ---",
    "Traceback (most recent call last):",
    '  File "C:\\Users\\User\\Desktop\\Forge Projects\\app.py", line 5, in <module>',
    "    from flask import Flask, render_template, request, jsonify, send_file, Response",
    "ModuleNotFoundError: No module named 'flask'",
]


class CauseMappingTests(unittest.TestCase):
    """Every known failure is one plain sentence, and an unknown one says so."""

    CASES = (
        (
            "missing module",
            {"log_tail": TRACEBACK_TO_MISSING_FLASK, "exit_code": 1, "run_state": "FAILED"},
            "missing_module",
            "не хватает библиотеки flask — её нужно установить",
        ),
        (
            "missing node module",
            {
                "log_tail": [
                    "node:internal/modules/cjs/loader:1143",
                    "Error: Cannot find module 'express'",
                ],
                "exit_code": 1,
            },
            "missing_module",
            "не хватает библиотеки express — её нужно установить",
        ),
        (
            "port busy (WinError 10013)",
            {
                "log_tail": [
                    "Traceback (most recent call last):",
                    "OSError: [WinError 10013] An attempt was made to access a socket in a way "
                    "forbidden by its access permissions",
                ],
                "exit_code": 1,
            },
            "port_busy",
            "порт занят другой программой",
        ),
        (
            "port busy (address already in use)",
            {
                "log_tail": ["OSError: [Errno 10048] Only one usage of each socket address "
                             "(protocol/network address/port) is normally permitted"],
                "exit_code": 1,
            },
            "port_busy",
            "порт занят другой программой",
        ),
        (
            "syntax error",
            {
                "log_tail": [
                    '  File "C:\\app\\app.py", line 12',
                    "    print('x'",
                    "SyntaxError: invalid syntax",
                ],
                "exit_code": 1,
            },
            "syntax_error",
            "ошибка в коде: строка 12",
        ),
        (
            "waiting for input (backend fact)",
            {"log_tail": ["Введите пароль:"], "exit_code": 3, "waiting_for_input": True},
            "waiting_for_input",
            "программа ждёт ввода — так запускать нельзя",
        ),
        (
            "waiting for input (prompt line only)",
            {"log_tail": ["Выбрать действие:"], "exit_code": 3, "waiting_for_input": False},
            "waiting_for_input",
            "программа ждёт ввода — так запускать нельзя",
        ),
        (
            "generic exit 1",
            {"log_tail": ["--- stderr ---"], "exit_code": 1, "run_state": "FAILED"},
            "exit_code",
            "программа завершилась с ошибкой (код 1)",
        ),
    )

    def test_01_every_known_cause_is_one_plain_sentence(self) -> None:
        for name, facts, kind, sentence in self.CASES:
            with self.subTest(case=name):
                panel = _panel(facts)
                self.assertEqual(panel["kind"], kind, name)
                self.assertEqual(panel["clause"], sentence, name)
                self.assertIn(sentence, panel["headline"], name)
                self.assertTrue(panel["headline"].startswith("Не запустилось: "), name)

    def test_02_the_sentence_is_measured_not_invented(self) -> None:
        """The module, the source line and the exit code are the log's own values."""

        module = _panel(self.CASES[0][1])
        self.assertEqual(module["moduleName"], "flask")
        self.assertTrue(module["line"].endswith("ModuleNotFoundError: No module named 'flask'"))

        syntax = _panel(self.CASES[4][1])
        self.assertEqual(syntax["lineNumber"], 12)

        # The exit code is reported where it *is* the cause, and never invented for a cause
        # the log named precisely.
        self.assertIsNone(module["exitCode"])
        generic = _panel(self.CASES[7][1])
        self.assertEqual(generic["exitCode"], 1)

        # An unknown cause invents nothing and is not dressed as one of the five.
        unknown = _panel({})
        self.assertEqual(unknown["kind"], "unknown")
        self.assertFalse(unknown["hasRunFacts"], "a refusal with no run facts shows no panel")

    def test_03_the_launcher_log_of_the_desktop_shortcut_is_read_too(self) -> None:
        """A program started from its shortcut leaves its reason in launch.log, not in Forge."""

        facts = {
            "log_tail": [],
            "launch_log_tail": [
                "[2026-10-25T10:00:00Z] start.bat: запускаю сервер",
                "OSError: [WinError 10048] Only one usage of each socket address",
            ],
            "launch_log_path": LAUNCH_LOG_NAME,
            "exit_code": 1,
        }
        panel = _panel(facts)
        self.assertEqual(panel["kind"], "port_busy")
        self.assertEqual(panel["logPath"], LAUNCH_LOG_NAME)

    def test_04_the_three_visible_lines_are_the_tail_and_the_rest_is_folded(self) -> None:
        lines = [f"line {number}" for number in range(1, 8)]
        panel = _panel({"log_tail": lines, "exit_code": 1})
        self.assertEqual(panel["visibleLines"], ["line 5", "line 6", "line 7"])
        self.assertEqual(panel["canAutoRepair"], True)
        capped = _panel({"log_tail": lines, "exit_code": 1}, attempts=2)
        self.assertFalse(capped["canAutoRepair"])
        self.assertEqual(capped["attempts"], 2)
        self.assertEqual(capped["remaining"], 0, "no automatic attempt may be left after two")

    def test_05_each_cause_has_an_english_sentence_as_well(self) -> None:
        english = _panel(self.CASES[0][1], lang="en")
        self.assertEqual(
            english["clause"], "the library flask is missing — it has to be installed"
        )
        self.assertIn(english["clause"], english["headline"])


# --------------------------------------------------------------------------- #
# 2. the repair loop, executed from ui/src/repair.ts
# --------------------------------------------------------------------------- #

_LOOP_DEPS = """
const deps = (opts) => {
  const calls = {dispatch: [], launch: 0};
  return {
    calls,
    deps: {
      dispatch: async (text) => {
        calls.dispatch.push(text);
        if (opts.dispatchThrows) return {ok: false, filesWritten: 0, version: 0};
        return {ok: true, filesWritten: opts.filesWritten ?? 2, version: opts.version ?? 2};
      },
      launch: async () => {
        calls.launch += 1;
        const failing = opts.launchResults ? opts.launchResults[(calls.launch - 1) % opts.launchResults.length] : false;
        return failing
          ? {ok: false, url: "", facts: opts.failureFacts ?? FAILURE}
          : {ok: true, url: "http://127.0.0.1:5099/", facts: {}};
      },
    },
  };
};
"""

_FAILURE_FACTS = {
    "log_tail": TRACEBACK_TO_MISSING_FLASK,
    "exit_code": 1,
    "run_state": "FAILED",
}


class RepairLoopTests(unittest.TestCase):
    """One press: one change run, at most one launch retry, and never more than two presses."""

    @classmethod
    def _loop_script(cls) -> str:
        return _IMPORTS + _ASCII_JSON + f"const FAILURE={json.dumps(_FAILURE_FACTS)};" + _LOOP_DEPS

    def _run(self, body: str):
        script = self._loop_script() + f"const out = await (async () => {{ {body} }})();process.stdout.write(__a(out));"
        return json.loads(_node(script))

    def test_01_the_instruction_carries_the_error_line(self) -> None:
        answer = _ask(
            "(() => {"
            f"const cause = r.mapLaunchCause({json.dumps(_FAILURE_FACTS)});"
            "const text = r.composeRepairInstruction(cause);"
            "return {text, cause: cause.kind};"
            "})()"
        )
        self.assertEqual(answer["cause"], "missing_module")
        self.assertIn("ModuleNotFoundError: No module named 'flask'", answer["text"])
        self.assertIn("не хватает библиотеки flask", answer["text"])
        self.assertIn("Исправь и добейся, чтобы приложение запускалось.", answer["text"])
        # Deterministic: the same cause composes the same text, so the backend's
        # "the same words are one change" rule applies to a second press.
        again = _ask(
            f"r.composeRepairInstruction(r.mapLaunchCause({json.dumps(_FAILURE_FACTS)}))"
        )
        self.assertEqual(again, answer["text"])

    def test_02_one_press_dispatches_exactly_one_change_and_retries_the_launch_once(self) -> None:
        answer = self._run(
            """
            const built = deps({launchResults: [true, false]});
            const state = await r.driveRepair(
              built.deps,
              r.initialRepairState(0),
              {instruction: r.composeRepairInstruction(r.mapLaunchCause(FAILURE))}
            );
            return {state, calls: built.calls};
            """
        )
        self.assertEqual(len(answer["calls"]["dispatch"]), 1, "one press, one change run")
        self.assertEqual(answer["calls"]["launch"], 1, "one press, at most one launch retry")
        self.assertEqual(answer["state"]["attempts"], 1)
        # The retry still failed here: the panel keeps the *new* cause and its own sentence.
        self.assertEqual(answer["state"]["phase"], "failed")
        self.assertEqual(answer["state"]["messageKey"], "projects.repair.failed_again")
        self.assertEqual(answer["state"]["cause"]["kind"], "missing_module")
        self.assertIsNotNone(answer["state"]["facts"])

    def test_03_a_successful_retry_opens_the_window_and_stops(self) -> None:
        answer = self._run(
            """
            const built = deps({launchResults: [false]});
            const state = await r.driveRepair(
              built.deps, r.initialRepairState(0), {instruction: "исправь"}
            );
            return {state, calls: built.calls};
            """
        )
        self.assertEqual(answer["calls"]["launch"], 1)
        self.assertEqual(answer["state"]["phase"], "opened")
        self.assertEqual(answer["state"]["url"], "http://127.0.0.1:5099/")
        self.assertEqual(answer["state"]["messageKey"], "projects.repair.opened")

    def test_04_the_second_failure_stops_the_loop_at_two_attempts(self) -> None:
        answer = self._run(
            """
            const built = deps({launchResults: [true]});
            const first = await r.driveRepair(
              built.deps, r.initialRepairState(0), {instruction: "исправь"}
            );
            const second = await r.driveRepair(
              built.deps, first, {instruction: "исправь ещё раз"}
            );
            const third = await r.driveRepair(
              built.deps, second, {instruction: "и ещё раз"}
            );
            return {first, second, third, calls: built.calls};
            """
        )
        self.assertEqual(answer["first"]["attempts"], 1)
        self.assertEqual(answer["second"]["attempts"], 2)
        self.assertEqual(answer["second"]["phase"], "capped")
        self.assertEqual(answer["second"]["messageKey"], "projects.repair.capped")
        # The cap is a promise: the third press dispatches nothing and launches nothing.
        self.assertEqual(len(answer["calls"]["dispatch"]), 2)
        self.assertEqual(answer["calls"]["launch"], 2)
        self.assertEqual(answer["third"]["attempts"], 2)

    def test_05_a_run_that_wrote_nothing_is_not_relaunched(self) -> None:
        answer = self._run(
            """
            const built = deps({filesWritten: 0, launchResults: [false]});
            const state = await r.driveRepair(
              built.deps, r.initialRepairState(0), {instruction: "исправь"}
            );
            return {state, calls: built.calls};
            """
        )
        self.assertEqual(answer["state"]["phase"], "nothing_written")
        self.assertEqual(answer["state"]["messageKey"], "projects.repair.nothing_written")
        self.assertEqual(answer["calls"]["launch"], 0, "unchanged code is never relaunched")
        self.assertEqual(len(answer["calls"]["dispatch"]), 1)

    def test_06_the_zero_file_sentence_is_the_owners(self) -> None:
        answer = _ask(
            "fill(t('ru', 'projects.repair.nothing_written'), {})"
        )
        self.assertEqual(answer, "изменения не записаны")

    def test_07_the_typed_sentence_goes_down_the_same_path(self) -> None:
        answer = self._run(
            """
            const built = deps({launchResults: [false]});
            const state = await r.driveRepair(
              built.deps,
              r.initialRepairState(0),
              {instruction: "установи flask и перезапусти", countAttempt: false}
            );
            return {state, calls: built.calls};
            """
        )
        self.assertEqual(
            answer["calls"]["dispatch"], ["установи flask и перезапусти"],
            "the owner's own words are what the change run receives",
        )
        self.assertEqual(answer["calls"]["launch"], 1)
        self.assertEqual(
            answer["state"]["attempts"], 0,
            "a sentence the owner types never spends an automatic attempt",
        )

    def test_08_a_refused_dispatch_reports_itself_and_starts_no_launch(self) -> None:
        answer = self._run(
            """
            const built = deps({dispatchThrows: true});
            const state = await r.driveRepair(
              built.deps, r.initialRepairState(0), {instruction: "исправь"}
            );
            return {state, calls: built.calls};
            """
        )
        self.assertEqual(answer["state"]["messageKey"], "projects.repair.dispatch_failed")
        self.assertEqual(answer["calls"]["launch"], 0)


# --------------------------------------------------------------------------- #
# 3. the backend's own refusal: the run log, launch.log, and the desktop shortcut
# --------------------------------------------------------------------------- #


class LaunchLogFactsTests(unittest.TestCase):
    """``<project>\\launch.log`` is the one channel both ways into a project share."""

    def setUp(self) -> None:
        base = pathlib.Path(
            tempfile.mkdtemp(prefix="self-repair-", dir=str(REPOSITORY / ".forge"))
        )
        self.addCleanup(lambda: shutil.rmtree(base, ignore_errors=True))
        self.workspace = base / "workspace"
        self.workspace.mkdir(parents=True, exist_ok=True)
        (self.workspace / "app.py").write_text("print('x')\n", encoding="utf-8")
        self.entry = resolve_entry([{"path": "app.py"}]).entry

    def _launch(self, runner: _FailingRunner):
        launcher = ProjectLauncher(
            runner=runner, previews=None, windows=_NoWindow(), readiness_timeout=0.1
        )
        target = RunTarget(
            project_id=PROJECT_ID,
            workspace=self.workspace,
            entry=self.entry,
            project_path="projects-visible/x",
        )
        with self.assertRaises(LaunchRefusal) as raised:
            launcher.launch(
                project_id=PROJECT_ID,
                target=target,
                entry=self.entry,
                files=1,
                confirm=True,
                digest="digest-shown-to-the-owner",
            )
        return raised.exception

    def test_01_a_refusal_carries_the_redacted_run_tail(self) -> None:
        refusal = self._launch(
            _FailingRunner(lines=TRACEBACK_TO_MISSING_FLASK, exit_code=1)
        )
        self.assertEqual(refusal.code, "project_launch_refused")
        facts = refusal.facts
        self.assertEqual(facts["exit_code"], 1)
        self.assertEqual(facts["run_state"], "FAILED")
        self.assertFalse(facts["waiting_for_input"])
        self.assertEqual(facts["log_tail"], TRACEBACK_TO_MISSING_FLASK)
        # The route publishes exactly these fields; the panel maps them to one sentence.
        panel = _panel(facts)
        self.assertEqual(panel["kind"], "missing_module")
        self.assertEqual(
            panel["clause"], "не хватает библиотеки flask — её нужно установить"
        )

    def test_02_forge_writes_the_reason_so_the_next_press_can_read_it(self) -> None:
        path = self.workspace / LAUNCH_LOG_NAME
        self.assertFalse(path.exists())
        self._launch(_FailingRunner(lines=TRACEBACK_TO_MISSING_FLASK, exit_code=1))
        self.assertTrue(path.is_file(), "a failed launch leaves its reason in launch.log")
        written = path.read_text(encoding="utf-8")
        self.assertIn("Forge:", written)
        self.assertIn("код 1", written)
        self.assertIn("ModuleNotFoundError: No module named 'flask'", written)

        # The next refusal reads that block back into its facts.
        refusal = self._launch(_FailingRunner(lines=["no output"], exit_code=1))
        own = refusal.facts.get("launch_log_tail")
        self.assertIsInstance(own, list)
        self.assertTrue(own, "the appended block is readable again")
        self.assertIn("ModuleNotFoundError: No module named 'flask'", "\n".join(own))
        self.assertEqual(refusal.facts.get("launch_log_path"), LAUNCH_LOG_NAME)

    def test_03_a_log_written_by_the_desktop_shortcut_reaches_the_panel(self) -> None:
        """The project launched outside Forge: only its own launch.log knows why it failed."""

        (self.workspace / LAUNCH_LOG_NAME).write_text(
            "start.bat: запускаю приложение\n"
            "Traceback (most recent call last):\n"
            "OSError: [WinError 10048] Only one usage of each socket address\n",
            encoding="utf-8",
        )
        refusal = self._launch(_FailingRunner(lines=["--- stderr ---"], exit_code=1))
        own = refusal.facts.get("launch_log_tail") or []
        self.assertTrue(own)
        self.assertIn("WinError 10048", "\n".join(own))
        panel = _panel(refusal.facts)
        self.assertEqual(panel["kind"], "port_busy")
        self.assertEqual(panel["clause"], "порт занят другой программой")
        self.assertEqual(panel["logPath"], LAUNCH_LOG_NAME)

    def test_04_a_secret_in_the_launcher_log_never_reaches_the_panel(self) -> None:
        secret = "sk-live-abcdefghijklmnopqrstuvwxyz0123456789"
        (self.workspace / LAUNCH_LOG_NAME).write_text(
            f"Authorization: Bearer {secret}\nport 5261 refused\n", encoding="utf-8"
        )
        refusal = self._launch(_FailingRunner(lines=["no output"], exit_code=1))
        own = "\n".join(refusal.facts.get("launch_log_tail") or [])
        self.assertIn("port 5261 refused", own)
        # The secret never reaches the panel. The *file* is the project's own and keeps what
        # its launcher wrote; Forge redacts what it publishes, it does not rewrite history.
        self.assertNotIn(secret, own)

    def test_05_the_log_is_bounded_and_appending_never_raises(self) -> None:
        path = self.workspace / LAUNCH_LOG_NAME
        path.write_text("\n".join(f"line {i}" for i in range(50)) + "\n", encoding="utf-8")
        self.assertTrue(append_launch_log(self.workspace, "проверка", ["новое"]))
        tail = read_launch_log_tail(self.workspace, limit=2)
        self.assertEqual(tail[-1], "новое")
        # A folder that is not there answers an empty tail and a False write: a log that
        # cannot be written must never fail a launch.
        missing = self.workspace / "gone"
        self.assertEqual(read_launch_log_tail(missing), [])
        self.assertFalse(append_launch_log(None, "x", ["y"]))


# --------------------------------------------------------------------------- #
# 4. the panel is under the launch button, and it is the change machinery
# --------------------------------------------------------------------------- #


class PanelPlacementTests(unittest.TestCase):
    """One place under the button, and no second "change" code path behind it."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.projects = PROJECTS_TSX.read_text(encoding="utf-8")
        cls.panel = PANEL_TSX.read_text(encoding="utf-8")
        cls.i18n = I18N_TS.read_text(encoding="utf-8")

    def test_01_the_panel_is_rendered_under_the_launch_button(self) -> None:
        button = self.projects.index('data-testid="project-launch-button"')
        panel = self.projects.index("<RepairPanel")
        notice = self.projects.index('data-testid="project-launch-notice"')
        self.assertLess(button, panel, "the panel must come after «Запустить приложение»")
        self.assertLess(panel, notice, "and before the success line")
        # Both are inside the same «Результат проекта» block: nothing else on the page.
        launch = self.projects.index('data-testid="project-launch"')
        self.assertLess(launch, button)
        self.assertLess(panel - launch, 4000, "the panel stays inside the launch block")
        # The card's generic action row is elsewhere: a failure is never reported there.
        self.assertNotIn('data-testid="repair-panel"', self.projects)

    def test_02_the_panel_has_the_two_actions_and_the_folded_log(self) -> None:
        for marker in (
            'data-testid="repair-panel"',
            'data-testid="repair-headline"',
            'data-testid="repair-log"',
            'data-testid="repair-log-last"',
            'data-testid="repair-log-full"',
            'data-testid="repair-auto"',
            'data-testid="repair-chat-input"',
            'data-testid="repair-chat-send"',
            'data-testid="repair-message"',
            'data-testid="repair-capped"',
        ):
            self.assertIn(marker, self.panel, marker)
        # The heading of the folded log is the owner's own phrase.
        self.assertIn("projects.repair.log", self.panel)
        self.assertIn("Показать журнал полностью", self.i18n)
        self.assertIn('"projects.repair.log": "Show the full log"', self.i18n)

    def test_03_both_actions_use_the_same_change_path(self) -> None:
        # One dispatch path: the panel's press reaches `startChange`, the handler
        # «Что поправить?» uses, and this file holds exactly one of each request.
        self.assertEqual(self.projects.count("createProjectVersion("), 1)
        self.assertEqual(self.projects.count("dispatchTask("), 1)
        self.assertIn("const outcome = await startChange(undefined, text);", self.projects)
        self.assertIn("const next = await driveRepair(", self.projects)
        self.assertIn("const answer = await requestLaunch();", self.projects)
        # The panel's strip is the very element the change block renders: one flight. The
        # screen passes that one element to the panel's `children`, so no second strip,
        # second AliveBar or second dispatch can exist.
        self.assertEqual(self.projects.count("{changeFlightBlock}"), 2)
        self.assertIn("const changeFlightBlock = (", self.projects)
        self.assertIn("{children}", self.panel)
        # «Исправить автоматически» composes the deterministic instruction.
        self.assertIn("composeRepairInstruction(model.cause)", self.projects)
        self.assertIn("initialRepairState(repairAttempts)", self.projects)

    def test_04_the_panel_carries_every_word_the_owner_asked_for(self) -> None:
        for phrase in (
            '"projects.repair.headline": "Не запустилось: {cause}"',
            '"projects.repair.auto": "Исправить автоматически"',
            '"projects.repair.chat.label": "Что сделать?"',
            '"projects.repair.chat.send": "Отправить"',
            '"projects.repair.capped": "больше не пробую сам — скажите, что менять"',
            '"projects.repair.cause.port_busy": "порт занят другой программой"',
            '"projects.repair.cause.waiting_for_input": '
            '"программа ждёт ввода — так запускать нельзя"',
        ):
            self.assertIn(phrase, self.i18n, phrase)

    def test_05_the_success_line_replaces_the_panel(self) -> None:
        """A launch that really opened a window says one green line and nothing else."""

        body = self.projects
        opened = body.index('t(lang, "projects.repair.opened")')
        cleared = body.rindex("setRepairFacts(null);", 0, opened)
        self.assertGreater(cleared, body.index("const runRepair = async"))
        self.assertIn('"projects.repair.opened": "Открыто в отдельном окне: {url}"', self.i18n)
        self.assertIn('"projects.launch.success": "Открыто в отдельном окне: {url}"', self.i18n)
        self.assertIn("setLaunchNotice(", body)


# --------------------------------------------------------------------------- #
# 5. the real 409 body shape feeds the real model
# --------------------------------------------------------------------------- #


class RouteShapeTests(unittest.TestCase):
    """The panel consumes the fields the route publishes, not a shape of its own."""

    def test_01_a_real_refusal_becomes_the_panel(self) -> None:
        base = pathlib.Path(
            tempfile.mkdtemp(prefix="self-repair-route-", dir=str(REPOSITORY / ".forge"))
        )
        self.addCleanup(lambda: shutil.rmtree(base, ignore_errors=True))
        workspace = base / "workspace"
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / "app.py").write_text("print('x')\n", encoding="utf-8")
        entry = resolve_entry([{"path": "app.py"}]).entry
        launcher = ProjectLauncher(
            runner=_FailingRunner(lines=TRACEBACK_TO_MISSING_FLASK, exit_code=1),
            previews=None,
            windows=_NoWindow(),
            readiness_timeout=0.1,
        )
        with self.assertRaises(LaunchRefusal) as raised:
            launcher.launch(
                project_id=PROJECT_ID,
                target=RunTarget(
                    project_id=PROJECT_ID,
                    workspace=workspace,
                    entry=entry,
                    project_path="projects-visible/x",
                ),
                entry=entry,
                files=1,
                confirm=True,
                digest="d",
            )
        refusal = raised.exception
        # ``app/api/project_routes.py`` publishes exactly this body for a 409.
        detail = {
            "refused": True,
            "error_code": refusal.code,
            "executed": False,
            "code": refusal.code,
            "error": str(refusal),
            **dict(refusal.facts),
        }
        self.assertEqual(detail["code"], "project_launch_refused")
        panel = _panel(detail)
        self.assertEqual(panel["kind"], "missing_module")
        self.assertEqual(
            panel["headline"],
            "Не запустилось: не хватает библиотеки flask — её нужно установить",
        )
        self.assertTrue(panel["canAutoRepair"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
