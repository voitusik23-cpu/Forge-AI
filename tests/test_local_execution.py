"""Safe local execution and preview for Python and Node projects.

Everything here is offline and deterministic: no provider is called, no credential
is read, and the only socket opened is the loopback one a test project's own server
binds (and closes) -- which is the feature under test.

What is proved, one rule per test group:

* **PART 1 -- what may run.** A project resolves to exactly one kind, from the
  manifest and the files on disk only: ``static_html``, ``python_script``,
  ``python_module``, ``node_script`` or ``node_npm``. A shape that is recognised but
  cannot be launched here (``node`` is not installed) carries a reason instead of a
  command; anything else has no entry point at all.
* **PART 2 -- the runner.** A command runs only with an explicit approval for this
  project and this exact argv (a changed command makes the approval stale), never
  through a shell, with an environment that holds no secret, with bounded and
  redacted output persisted beside the project, with a timeout that kills a hanging
  script, and with a tree kill that leaves neither the child nor its descendants
  alive.
* **PART 3 -- preview.** A process preview exists only while the project's own process
  is up and a localhost port was observed. A process that never bound a port has no
  URL, and the payload says why instead of inventing one.
* **PART 4 -- the stage gate.** A live process is ``RUNNING``; a refused or blocked
  attempt is ``BLOCKED``; a runnable project that was never started is
  ``READY_TO_RUN``; a stopped one is ``STOPPED``. A started process never becomes
  ``COMPLETED``.
* **PART 5 -- the API.** The four routes require the confirmation and the digest,
  report the same facts the runner does, keep absolute paths inside ``diagnostics``,
  and never carry the raw secret the child printed.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import time
import types
import unittest
import urllib.error
import urllib.request
from dataclasses import replace

from app.projects.entrypoint import (
    ENTRY_KIND_NODE_NPM,
    ENTRY_KIND_NODE_SCRIPT,
    ENTRY_KIND_PYTHON_MODULE,
    ENTRY_KIND_PYTHON_SCRIPT,
    ENTRY_KIND_STATIC_HTML,
    detect_entry_point,
    entry_digest,
    resolve_entry,
    node_executable,
    npm_command,
    python_executable,
    REASON_NPM_SCRIPT,
)
from app.projects.models import ArtifactRef, ProjectStatus, ProjectVersion, utc_now
from app.projects.preview import PREVIEW_NEEDS_PROCESS, PreviewRegistry
from app.projects.runner import (
    FAILED,
    MAX_LOG_BYTES,
    NOT_RUN,
    REFUSED,
    RUN_BLOCKED,
    RUNNING,
    STARTING,
    STOPPED,
    SUCCEEDED,
    TERMINAL_STATES,
    TIMED_OUT,
    LocalRunner,
    RunTarget,
    redact_output_text,
)
from app.projects.service import ProjectService
from app.projects.stage import (
    BLOCKED,
    COMPLETED,
    READY_TO_RUN,
    RUNNING as STAGE_RUNNING,
    STOPPED as STAGE_STOPPED,
    VERIFIED as STAGE_VERIFIED,
    project_stage,
)
from app.projects.store import ProjectStore

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRATCH = REPO_ROOT / ".forge" / "test-local-execution"

PROJECT_ID = "proj-1a2b3c4d5e6f"
OTHER_PROJECT_ID = "proj-0f0e0d0c0b0a"

#: The secret the redaction test makes a child print. It is credential-shaped, so the
#: repository's own scanner must recognise it, and it must never be stored or returned.
SECRET = "sk-" + "Q7x" * 18

ONE_SHOT = 'print("one-shot-ok")\n'
HANGING = 'import time\nprint("sleeping", flush=True)\ntime.sleep(120)\n'
HUGE_OUTPUT = "for index in range(60000):\n    print('row-%06d-' % index + 'y' * 40)\n"
PRINTS_SECRET = 'print("api_key ' + SECRET + '")\nraise SystemExit(4)\n'
PRINTS_ENVIRONMENT = (
    "import json, os\n"
    "print(json.dumps(sorted(os.environ)))\n"
)
SPAWNS_CHILD = (
    "import subprocess, sys, time\n"
    "child = subprocess.Popen([sys.executable, 'child.py'])\n"
    "print('child-pid %d' % child.pid, flush=True)\n"
    "time.sleep(120)\n"
)
CHILD = 'import time\nprint("child-up", flush=True)\ntime.sleep(120)\n'
SERVING = """\
import http.server, socketserver

class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"LOCAL-RUN-OK\\n"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        return

with socketserver.TCPServer(("127.0.0.1", 0), Handler) as httpd:
    print("listening %d" % httpd.server_address[1], flush=True)
    httpd.serve_forever()
"""


class _Session:
    """The minimum a discovery session must expose for ``create_from_discovery``."""

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self.initial_goal = "Небольшой локальный проект для проверки запуска."
        self.specification = {"goal": self.initial_goal, "platform": "веб-приложение"}


def _process_alive(pid: object) -> bool:
    """Whether a pid is still alive, without touching anything this test did not start."""

    if not isinstance(pid, int) or pid <= 0:
        return False
    if os.name == "nt":
        completed = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True,
            check=False,
        )
        return str(pid) in (completed.stdout or b"").decode("utf-8", errors="replace")
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


class _LocalExecutionCase(unittest.TestCase):
    """A project workspace, a store, a preview registry and one runner."""

    def setUp(self) -> None:
        self.base = SCRATCH / f"{os.getpid()}-{id(self)}"
        shutil.rmtree(self.base, ignore_errors=True)
        self.base.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(self.base, ignore_errors=True))
        self.store = ProjectStore(root=self.base / "projects")
        self.workspace = self.store.workspace_path(PROJECT_ID, create=True)
        self.previews = PreviewRegistry()
        self.addCleanup(self.previews.stop_all)
        self.runner = self._runner()
        self.addCleanup(self.runner.stop_all)

    def _runner(self, *, timeout: float = 30.0, startup: float = 5.0) -> LocalRunner:
        runner = LocalRunner(
            store=self.store,
            previews=self.previews,
            timeout=timeout,
            startup_timeout=startup,
        )
        self.addCleanup(runner.stop_all)
        return runner

    # ------------------------------------------------------------------ helpers
    def write(self, relative: str, body: str) -> pathlib.Path:
        target = self.workspace.joinpath(*relative.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
        return target

    def target(self, entry: object, project_id: str = PROJECT_ID) -> RunTarget:
        return RunTarget(
            project_id=project_id,
            workspace=self.workspace,
            entry=entry,
            project_path=f".forge/projects/{project_id}/workspace",
        )

    def entry_for(self, manifest: list, workspace: object = None) -> object:
        return detect_entry_point(manifest, workspace=workspace or self.workspace)

    def start_approved(self, target: RunTarget, runner: LocalRunner | None = None) -> dict:
        active = runner or self.runner
        # The digest is the one the runner itself would show: a hand-made entry in a
        # test has no cached digest, and using the runner's own answer is exactly what
        # the API does with the digest it published.
        token = active.confirm(target, confirm=True, digest=active.digest_for(target))
        self.assertIsNotNone(token, "the digest shown must be approved")
        return active.start(target, token)

    def wait(self, target: RunTarget, *, runner: LocalRunner | None = None, limit: float = 30.0) -> dict:
        active = runner or self.runner
        status = active.status(target)
        deadline = time.time() + limit
        while time.time() < deadline and status["state"] not in TERMINAL_STATES:
            time.sleep(0.05)
            status = active.status(target)
        return status

    def wait_for_state(
        self,
        target: RunTarget,
        wanted: str,
        *,
        runner: LocalRunner | None = None,
        limit: float = 30.0,
    ) -> dict:
        active = runner or self.runner
        status = active.status(target)
        deadline = time.time() + limit
        while time.time() < deadline and status["state"] != wanted:
            if status["state"] in TERMINAL_STATES and status["state"] != wanted:
                break
            time.sleep(0.05)
            status = active.status(target)
        return status

    def log_file(self, project_id: str = PROJECT_ID) -> str:
        """The persisted run log, read with tolerance for Windows file sharing.

        The supervisor rewrites the file through a temporary file and ``os.replace``
        while a run is in progress, and Windows can refuse a replace for the moment a
        reader holds the file. This bounded retry is the same tolerance the project
        store already applies; a genuinely missing file is still a failure.
        """

        path = self.base / "projects" / project_id / "run.log"
        for attempt in range(20):
            try:
                return path.read_text(encoding="utf-8") if path.is_file() else ""
            except PermissionError:
                if attempt == 19:
                    raise
                time.sleep(0.05)
        return ""


# --------------------------------------------------------------------------- #
# 1. what may run: the five kinds, from the manifest only
# --------------------------------------------------------------------------- #


class RunnableKindTests(_LocalExecutionCase):
    def test_01_python_script_has_a_command_and_runs_to_success(self) -> None:
        self.write("app.py", ONE_SHOT)
        entry = self.entry_for([ArtifactRef(path="app.py")])
        self.assertIsNotNone(entry)
        self.assertEqual(entry.kind, ENTRY_KIND_PYTHON_SCRIPT)
        self.assertEqual(entry.run_command, "python app.py")
        self.assertTrue(entry.runnable)
        self.assertEqual(entry.command[0], python_executable())
        self.assertEqual(entry.command[1], "app.py")

        status = self.start_approved(self.target(entry))
        status = self.wait(self.target(entry))
        self.assertEqual(status["state"], SUCCEEDED, status)
        self.assertEqual(status["exit_code"], 0)
        self.assertIn("one-shot-ok", "\n".join(status["log"]["lines"]))

    def test_02_python_module_uses_the_package_main(self) -> None:
        self.write("pkg/__main__.py", 'print("module-ok")\n')
        entry = self.entry_for([ArtifactRef(path="pkg/__main__.py")])
        self.assertIsNotNone(entry)
        self.assertEqual(entry.kind, ENTRY_KIND_PYTHON_MODULE)
        self.assertEqual(entry.command[0], python_executable())
        self.assertEqual(entry.command[1:], ("-m", "pkg"))

        self.start_approved(self.target(entry))
        status = self.wait(self.target(entry))
        self.assertEqual(status["state"], SUCCEEDED, status)
        self.assertIn("module-ok", "\n".join(status["log"]["lines"]))

    def test_03_node_script_runs_when_node_exists_else_is_blocked(self) -> None:
        self.write("server.js", "console.log('node-ok');process.exit(0);\n")
        manifest = [ArtifactRef(path="server.js")]
        entry = self.entry_for(manifest)
        self.assertIsNotNone(entry)
        self.assertEqual(entry.kind, ENTRY_KIND_NODE_SCRIPT)

        node = node_executable()
        if node is None:
            # Honest absence: the shape is recognised, and nothing can be run.
            self.assertEqual(entry.command, ())
            self.assertIn("node is not installed", entry.reason)
            status = self.start_approved(self.target(entry))
            self.assertEqual(status["state"], RUN_BLOCKED, status)
            self.assertIn("node is not installed", status["reason"])
            return

        self.assertEqual(entry.command[0], node)
        self.start_approved(self.target(entry))
        status = self.wait(self.target(entry))
        self.assertEqual(status["state"], SUCCEEDED, status)
        self.assertIn("node-ok", "\n".join(status["log"]["lines"]))

    def test_04_an_unrecognised_shape_has_no_entry_point(self) -> None:
        self.write("notes.md", "# nothing to run\n")
        resolution = resolve_entry([ArtifactRef(path="notes.md")])
        self.assertIsNone(resolution.entry)
        self.assertIn("no entry point", resolution.reason)
        self.assertFalse(resolution.blocked)

        status = self.runner.start(self.target(None), None)
        self.assertEqual(status["state"], REFUSED)
        self.assertEqual(status["blocked_code"], "no_entry_point")

    def test_05_node_npm_only_runs_a_declared_script_and_never_installs(self) -> None:
        self.write("package.json", json.dumps({"scripts": {"start": "node index.js"}}))
        entry = self.entry_for([ArtifactRef(path="package.json")])
        self.assertIsNotNone(entry)
        self.assertEqual(entry.kind, ENTRY_KIND_NODE_NPM)
        self.assertEqual(entry.run_command, "npm start")
        command, reason = npm_command("start")
        if command is None:
            self.assertEqual(entry.command, ())
            self.assertTrue(entry.reason)
            return
        self.assertEqual(entry.command, command)
        self.assertNotIn("install", " ".join(entry.command))
        # Only the declared scripts exist; anything else has no command at all.
        self.assertEqual(npm_command("build"), (None, REASON_NPM_SCRIPT))

    def test_06_a_static_page_is_a_preview_not_a_process(self) -> None:
        self.write("index.html", "<!doctype html><html></html>")
        entry = self.entry_for([ArtifactRef(path="index.html")])
        self.assertEqual(entry.kind, ENTRY_KIND_STATIC_HTML)
        self.assertEqual(entry.command, ())
        status = self.start_approved(self.target(entry))
        self.assertEqual(status["state"], REFUSED)
        self.assertEqual(status["blocked_code"], "static_entry_point")

    def test_07_the_digest_covers_the_exact_command(self) -> None:
        first = entry_digest(ENTRY_KIND_PYTHON_SCRIPT, "app.py", ("python", "app.py"))
        second = entry_digest(ENTRY_KIND_PYTHON_SCRIPT, "app.py", ("python", "main.py"))
        self.assertNotEqual(first, second)
        self.assertEqual(
            first, entry_digest(ENTRY_KIND_PYTHON_SCRIPT, "app.py", ("python", "app.py"))
        )


# --------------------------------------------------------------------------- #
# 2. the approval boundary
# --------------------------------------------------------------------------- #


class ApprovalTests(_LocalExecutionCase):
    def test_08_no_approval_means_no_process(self) -> None:
        self.write("app.py", ONE_SHOT)
        entry = self.entry_for([ArtifactRef(path="app.py")])
        target = self.target(entry)

        for token in (None, "", "not-a-token"):
            status = self.runner.start(target, token)
            self.assertEqual(status["state"], REFUSED, token)
            self.assertEqual(status["blocked_code"], "approval_required")
            self.assertIsNone(status["pid"])

        self.assertEqual(self.runner.status(target)["state"], REFUSED)

    def test_09_a_wrong_digest_approves_nothing(self) -> None:
        self.write("app.py", ONE_SHOT)
        entry = self.entry_for([ArtifactRef(path="app.py")])
        target = self.target(entry)
        self.assertIsNone(self.runner.confirm(target, confirm=True, digest="0" * 64))
        self.assertIsNone(self.runner.confirm(target, confirm="true", digest=entry.digest))
        self.assertIsNone(self.runner.confirm(target, confirm=True, digest=entry.digest + "x"))
        status = self.runner.start(target, None)
        self.assertEqual(status["state"], REFUSED)

    def test_10_an_approval_is_bound_to_one_project(self) -> None:
        self.write("app.py", ONE_SHOT)
        entry = self.entry_for([ArtifactRef(path="app.py")])
        token = self.runner.confirm(self.target(entry), confirm=True, digest=entry.digest)
        other_workspace = self.store.workspace_path(OTHER_PROJECT_ID, create=True)
        (other_workspace / "app.py").write_text(ONE_SHOT, encoding="utf-8")
        other = RunTarget(
            project_id=OTHER_PROJECT_ID, workspace=other_workspace, entry=entry
        )
        status = self.runner.start(other, token)
        self.assertEqual(status["state"], REFUSED)
        self.assertEqual(status["blocked_code"], "approval_required")

    def test_11_a_changed_command_makes_the_approval_stale(self) -> None:
        self.write("app.py", ONE_SHOT)
        self.write("main.py", ONE_SHOT)
        entry = self.entry_for([ArtifactRef(path="app.py")])
        target = self.target(entry)
        token = self.runner.confirm(target, confirm=True, digest=entry.digest)
        # A different, equally allow-listed command in the same project: the approval
        # named the argv of app.py, so it cannot authorise main.py.
        changed = types.SimpleNamespace(
            kind=ENTRY_KIND_PYTHON_SCRIPT,
            path="main.py",
            run_command="python main.py",
            command=(python_executable(), "main.py"),
            reason="",
            serve_kind="process",
        )
        status = self.runner.start(self.target(changed), token)
        self.assertEqual(status["state"], REFUSED)
        self.assertEqual(status["blocked_code"], "approval_stale")


# --------------------------------------------------------------------------- #
# 3. the allowlist and the path boundary
# --------------------------------------------------------------------------- #


class AllowlistTests(_LocalExecutionCase):
    def test_12_a_command_outside_the_allowlist_is_refused(self) -> None:
        self.write("app.py", ONE_SHOT)
        for command in (
            ("cmd.exe", "/c", "echo", "pwned"),
            (python_executable(), "-c", "print(1)"),
            (python_executable(), "app.py", "extra"),
            ("sh", "-c", "echo hi"),
        ):
            entry = types.SimpleNamespace(
                kind=ENTRY_KIND_PYTHON_SCRIPT,
                path="app.py",
                run_command="python app.py",
                command=command,
                reason="",
                serve_kind="process",
            )
            target = self.target(entry)
            status = self.start_approved(target)
            self.assertEqual(status["state"], REFUSED, command)
            self.assertEqual(status["blocked_code"], "command_not_allowed", command)
            self.assertIsNone(status["pid"])

    def test_13_a_path_that_escapes_the_workspace_is_refused(self) -> None:
        self.write("app.py", ONE_SHOT)
        outside = self.base / "outside.py"
        outside.write_text('print("outside")\n', encoding="utf-8")
        for path in ("../outside.py", "sub/../../outside.py", "/abs/app.py", "sub/app.py"):
            entry = types.SimpleNamespace(
                kind=ENTRY_KIND_PYTHON_SCRIPT,
                path=path,
                run_command=f"python {path}",
                command=(python_executable(), path),
                reason="",
                serve_kind="process",
            )
            target = self.target(entry)
            status = self.start_approved(target)
            self.assertEqual(status["state"], REFUSED, path)
            self.assertIn(
                status["blocked_code"], ("unsafe_entry_path", "entry_missing", "command_not_allowed")
            )
            self.assertIsNone(status["pid"])

    def test_14_a_missing_entry_file_is_reported_not_invented(self) -> None:
        entry = types.SimpleNamespace(
            kind=ENTRY_KIND_PYTHON_SCRIPT,
            path="missing.py",
            run_command="python missing.py",
            command=(python_executable(), "missing.py"),
            reason="",
            serve_kind="process",
        )
        status = self.start_approved(self.target(entry))
        self.assertEqual(status["state"], REFUSED)
        self.assertEqual(status["blocked_code"], "entry_missing")


# --------------------------------------------------------------------------- #
# 4. the runner's hard rules
# --------------------------------------------------------------------------- #


class RunnerTests(_LocalExecutionCase):
    def test_15_a_hanging_script_times_out_and_the_process_is_gone(self) -> None:
        self.write("app.py", HANGING)
        entry = self.entry_for([ArtifactRef(path="app.py")])
        fast = self._runner(timeout=1.5, startup=0.5)
        target = self.target(entry)
        started = self.start_approved(target, runner=fast)
        pid = started["pid"]
        self.assertTrue(_process_alive(pid), "the child really was started")

        status = self.wait(target, runner=fast, limit=20.0)
        self.assertEqual(status["state"], TIMED_OUT, status)
        self.assertIsNone(status["exit_code"])
        self.assertFalse(_process_alive(pid), "the timed-out process must be gone")

    def test_16_stop_terminates_the_whole_tree(self) -> None:
        self.write("app.py", SPAWNS_CHILD)
        self.write("child.py", CHILD)
        entry = self.entry_for([ArtifactRef(path="app.py"), ArtifactRef(path="child.py")])
        target = self.target(entry)
        started = self.start_approved(target)
        parent = started["pid"]

        status = self.wait_for_state(target, RUNNING, limit=15.0)
        self.assertIn(status["state"], (RUNNING, STARTING), status)
        lines = "\n".join(self.runner.status(target)["log"]["lines"])
        child_pid = None
        for line in lines.splitlines():
            if line.startswith("child-pid "):
                child_pid = int(line.split()[1])
        self.assertIsNotNone(child_pid, lines)
        self.assertTrue(_process_alive(parent))

        stopped = self.runner.stop(PROJECT_ID)
        self.assertEqual(stopped["state"], STOPPED)
        self.assertFalse(_process_alive(parent), "the direct child must be gone")
        self.assertFalse(_process_alive(child_pid), "the descendant must be gone too")

    def test_17_output_is_capped_and_marked(self) -> None:
        self.write("app.py", HUGE_OUTPUT)
        entry = self.entry_for([ArtifactRef(path="app.py")])
        target = self.target(entry)
        self.start_approved(target)
        status = self.wait(target, limit=60.0)
        self.assertEqual(status["state"], SUCCEEDED, status)
        self.assertTrue(status["log"]["truncated"], status["log"]["count"])
        body = self.log_file()
        self.assertTrue(
            status["log"]["path"].endswith(f"/{PROJECT_ID}/run.log"), status["log"]["path"]
        )
        self.assertLessEqual(len(body.encode("utf-8")), 3 * MAX_LOG_BYTES)
        self.assertIn("output truncated", body)

    def test_18_the_child_environment_holds_no_credential(self) -> None:
        self.write("app.py", PRINTS_ENVIRONMENT)
        os.environ["FORGE_TEST_API_KEY"] = "sk-" + "z" * 24
        os.environ["FORGE_TEST_SECRET"] = "not-a-real-secret"
        self.addCleanup(os.environ.pop, "FORGE_TEST_API_KEY", None)
        self.addCleanup(os.environ.pop, "FORGE_TEST_SECRET", None)

        entry = self.entry_for([ArtifactRef(path="app.py")])
        target = self.target(entry)
        self.start_approved(target)
        status = self.wait(target)
        self.assertEqual(status["state"], SUCCEEDED, status)
        keys_line = next(
            (
                line
                for line in status["log"]["lines"]
                if line.strip().startswith("[")
            ),
            "",
        )
        keys = json.loads(keys_line)
        expected = ["PATH", "PYTHONIOENCODING", "PYTHONUNBUFFERED"]
        if os.name == "nt":
            # TEMP/TMP point inside the project folder when the child's token is
            # lowered: the machine's TEMP is a higher-integrity folder, which a
            # contained child cannot write.
            expected += ["SYSTEMROOT", "TEMP", "TMP"]
        expected += ["FORGE_SANDBOX"]
        self.assertEqual(sorted(keys), sorted(expected))
        for key in keys:
            self.assertFalse(key.upper().endswith("_API_KEY"), key)
            self.assertNotIn("SECRET", key.upper())
            # The one ``FORGE_*`` variable a child may see is the containment marker:
            # it says "you are contained", it never carries a credential.
            if key.upper() != "FORGE_SANDBOX":
                self.assertNotIn("FORGE", key.upper())
        self.assertNotIn("OPENAI", " ".join(keys))

    def test_19_a_restart_runs_the_command_again(self) -> None:
        self.write("app.py", 'import os\nprint("pid", os.getpid())\n')
        entry = self.entry_for([ArtifactRef(path="app.py")])
        target = self.target(entry)
        token = self.runner.confirm(target, confirm=True, digest=entry.digest)
        self.runner.start(target, token)
        first = self.wait(target)
        self.assertEqual(first["state"], SUCCEEDED, first)
        first_pid = first["pid"]

        token = self.runner.confirm(target, confirm=True, digest=entry.digest)
        self.runner.restart(target, token)
        second = self.wait(target)
        self.assertEqual(second["state"], SUCCEEDED, second)
        self.assertNotEqual(second["pid"], first_pid)
        self.assertNotEqual(first_pid, None)

        # A restart without a fresh approval is a refusal, not a silent rerun.
        refused = self.runner.restart(target, None)
        self.assertEqual(refused["state"], REFUSED)

    def test_19b_the_offline_marker_is_recorded_and_is_not_an_isolation_claim(self) -> None:
        """``offline`` records the owner's declaration; the network stays inherited."""

        self.write("app.py", ONE_SHOT)
        entry = self.entry_for([ArtifactRef(path="app.py")])
        target = self.target(entry)
        token = self.runner.confirm(target, confirm=True, digest=entry.digest, offline=True)
        self.assertIsNotNone(token)
        self.runner.start(target, token)
        status = self.wait(target)
        self.assertEqual(status["state"], SUCCEEDED, status)
        self.assertTrue(status["offline"], "the declared offline marker is recorded")
        # The marker never becomes a claim about isolation.
        self.assertEqual(status["network_policy"], "inherited")
        # A non-boolean marker is not an approval at all.
        self.assertIsNone(
            self.runner.confirm(target, confirm=True, digest=entry.digest, offline="yes")
        )

    def test_20_a_secret_never_reaches_the_log_the_file_or_a_payload(self) -> None:
        self.write("app.py", PRINTS_SECRET)
        entry = self.entry_for([ArtifactRef(path="app.py")])
        target = self.target(entry)
        self.start_approved(target)
        status = self.wait(target)
        self.assertEqual(status["state"], FAILED)
        self.assertEqual(status["exit_code"], 4)

        payload = json.dumps(status, ensure_ascii=False)
        self.assertNotIn(SECRET, payload)
        self.assertNotIn(SECRET, self.log_file())
        # The line is either rewritten in place ("[REDACTED]") or replaced whole
        # ("[redacted]") when the scanner still recognises secret material in it.
        self.assertIn("[redacted]", payload.lower())

        log = self.runner.log(target)
        self.assertNotIn(SECRET, json.dumps(log, ensure_ascii=False))
        # The redaction helper itself is the one the runner uses for every line.
        self.assertNotIn(SECRET, redact_output_text("api_key " + SECRET))
        self.assertEqual(redact_output_text("plain line"), "plain line")


# --------------------------------------------------------------------------- #
# 5. the preview rule
# --------------------------------------------------------------------------- #


class LocalPreviewTests(_LocalExecutionCase):
    def test_21_a_serving_process_publishes_a_url_that_answers(self) -> None:
        self.write("app.py", SERVING)
        entry = self.entry_for([ArtifactRef(path="app.py")])
        target = self.target(entry)
        self.start_approved(target)
        status = self.wait_for_state(target, RUNNING, limit=20.0)
        self.assertEqual(status["state"], RUNNING, status)
        self.assertTrue(status["serving"])
        self.assertGreater(status["port"], 0)
        url = status["url"]
        self.assertTrue(url.startswith("http://127.0.0.1:"), url)
        self.assertTrue(self.previews.is_running(PROJECT_ID))
        self.assertEqual(self.previews.status(PROJECT_ID)["source"], "process")

        with urllib.request.urlopen(url, timeout=5) as response:
            self.assertEqual(response.status, 200)
            self.assertIn(b"LOCAL-RUN-OK", response.read())

        self.runner.stop(PROJECT_ID)
        self.assertFalse(self.previews.is_running(PROJECT_ID))
        self.assertEqual(self.previews.status(PROJECT_ID)["url"], "")

    def test_22_a_process_that_binds_no_port_has_no_url(self) -> None:
        self.write("app.py", HANGING)
        entry = self.entry_for([ArtifactRef(path="app.py")])
        target = self.target(entry)
        fast = self._runner(timeout=20.0, startup=0.5)
        self.start_approved(target, runner=fast)
        status = self.wait_for_state(target, RUNNING, runner=fast, limit=10.0)
        self.assertEqual(status["state"], RUNNING, status)
        self.assertFalse(status["serving"])
        self.assertEqual(status["url"], "")
        self.assertFalse(self.previews.is_running(PROJECT_ID))
        started = self.previews.start(PROJECT_ID, self.workspace, entry)
        self.assertFalse(started["available"])
        self.assertEqual(started["reason"], PREVIEW_NEEDS_PROCESS)
        self.assertEqual(started["url"], "")
        fast.stop(PROJECT_ID)


# --------------------------------------------------------------------------- #
# 6. the stage gate
# --------------------------------------------------------------------------- #


class LocalStageTests(unittest.TestCase):
    def _stage(self, **overrides: object) -> str:
        facts = {
            "files_count": 2,
            "verification": "passed",
            "acceptance": "passed",
            # A record that is neither complete nor asking for changes: the process
            # words are the only thing that can move the ladder here.
            "lifecycle_status": "PLANNED",
            "archived": False,
            "preview_available": False,
            "preview_not_required": False,
            "run_state": "",
            "runnable": True,
        }
        facts.update(overrides)
        return project_stage(**facts)

    def test_23_a_started_process_is_running_and_never_completed(self) -> None:
        for state in ("STARTING", RUNNING, "STOPPING"):
            self.assertEqual(self._stage(run_state=state), STAGE_RUNNING, state)
        # Even a project that satisfies every completion fact is RUNNING while alive.
        self.assertEqual(
            self._stage(
                run_state=RUNNING,
                lifecycle_status="COMPLETED",
                preview_available=True,
            ),
            STAGE_RUNNING,
        )

    def test_24_refused_and_blocked_attempts_are_blocked(self) -> None:
        for state in (REFUSED, RUN_BLOCKED):
            self.assertEqual(self._stage(run_state=state), BLOCKED, state)

    def test_25_ready_stopped_and_verified_are_distinct(self) -> None:
        self.assertEqual(self._stage(run_state=NOT_RUN), READY_TO_RUN)
        self.assertEqual(self._stage(run_state=STOPPED), STAGE_STOPPED)
        # A project with nothing runnable keeps the word it had before.
        self.assertEqual(self._stage(runnable=False), STAGE_VERIFIED)
        self.assertEqual(
            self._stage(run_state=STOPPED, preview_not_required=True, lifecycle_status="COMPLETED"),
            COMPLETED,
        )

    def test_26_completion_still_needs_every_fact(self) -> None:
        self.assertEqual(
            self._stage(lifecycle_status="COMPLETED", preview_available=True), COMPLETED
        )
        # A completed record with no preview and no explicit flag is not COMPLETED;
        # it keeps the word the completion gate leaves it with.
        self.assertEqual(
            self._stage(lifecycle_status="COMPLETED", preview_available=False),
            STAGE_VERIFIED,
        )
        self.assertEqual(
            self._stage(lifecycle_status="COMPLETED", preview_available=True, verification="failed"),
            "FAILED",
        )
        # ... and a live process never turns a non-completed project into COMPLETED.
        self.assertEqual(
            self._stage(lifecycle_status="NEEDS_CHANGES", run_state=RUNNING),
            STAGE_RUNNING,
        )


# --------------------------------------------------------------------------- #
# 7. the API routes
# --------------------------------------------------------------------------- #


class LocalExecutionApiTests(_LocalExecutionCase):
    def setUp(self) -> None:
        super().setUp()
        self.projects = ProjectService(self.store)
        created = self.projects.create_from_discovery(_Session(f"sess-{os.getpid()}-{id(self)}"))
        project = self.projects.confirm(created.project_id)
        self.project_id = project.project_id
        self.workspace = self.store.workspace_path(self.project_id, create=True)
        self.write("app.py", ONE_SHOT)
        self.project = self._record(project, "app.py")
        self.runner = LocalRunner(store=self.store, previews=self.previews, timeout=30.0)
        self.addCleanup(self.runner.stop_all)

        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from app.api.project_routes import register_project_routes

        app = FastAPI()
        register_project_routes(
            app, self.projects, preview_registry=self.previews, runner=self.runner
        )
        self.client = TestClient(app)

    def _record(self, project: object, path: str) -> object:
        """Record one verified version whose manifest names ``path``."""

        version = ProjectVersion(
            version=1,
            created_at=utc_now(),
            run_id="run-local-1",
            files=(ArtifactRef(path=path, kind="python"),),
            verification="passed",
            acceptance="passed",
            provider="mock",
            model="deterministic-offline",
        )
        recorded = replace(
            project,
            versions=[version],
            current_version=1,
            status=ProjectStatus.NEEDS_CHANGES,
            updated_at=utc_now(),
        )
        self.store.write(recorded)
        return recorded

    def test_27_the_run_routes_require_the_confirmation_and_the_digest(self) -> None:
        project_id = self.project_id
        detail = self.client.get(f"/api/projects/{project_id}").json()
        entry = detail["entrypoint"]
        self.assertEqual(entry["kind"], ENTRY_KIND_PYTHON_SCRIPT)
        self.assertTrue(entry["runnable"])
        digest = entry["digest"]
        self.assertEqual(detail["run"]["state"], NOT_RUN)
        self.assertEqual(detail["run"]["entrypoint_digest"], digest)

        # No confirmation, a wrong digest and a missing digest all approve nothing.
        for body in ({}, {"confirm": False}, {"confirm": True}, {"confirm": True, "entrypoint_digest": "0" * 64}):
            response = self.client.post(f"/api/projects/{project_id}/run", json=body)
            self.assertEqual(response.status_code, 200, body)
            self.assertEqual(response.json()["state"], REFUSED, body)
            self.assertEqual(response.json()["blocked_code"], "approval_required", body)

        started = self.client.post(
            f"/api/projects/{project_id}/run",
            json={"confirm": True, "entrypoint_digest": digest},
        )
        self.assertEqual(started.status_code, 200)
        # A one-shot script can finish before the response is even read, so the answer
        # is "a process was started", never a promise that it is still starting.
        self.assertIn(started.json()["state"], (STARTING, RUNNING, SUCCEEDED))

        deadline = time.time() + 20
        status = started.json()
        while time.time() < deadline and status["state"] not in TERMINAL_STATES:
            time.sleep(0.05)
            status = self.client.get(f"/api/projects/{project_id}/run").json()
        self.assertEqual(status["state"], SUCCEEDED, status)

        log = self.client.get(f"/api/projects/{project_id}/run/log").json()
        self.assertIn("one-shot-ok", "\n".join(log["lines"]))
        # The absolute workspace path is published inside ``diagnostics`` only.
        published = {key: value for key, value in status.items() if key != "diagnostics"}
        self.assertNotIn(str(self.workspace), json.dumps(published, ensure_ascii=False))
        self.assertEqual(status["diagnostics"]["cwd"], str(self.workspace))
        self.assertEqual(status["network_policy"], "inherited")

    def test_28_stop_and_restart_are_reachable_and_honest(self) -> None:
        project_id = self.project_id
        digest = self.client.get(f"/api/projects/{project_id}").json()["entrypoint"]["digest"]
        self.client.post(
            f"/api/projects/{project_id}/run",
            json={"confirm": True, "entrypoint_digest": digest},
        )
        deadline = time.time() + 20
        while time.time() < deadline:
            state = self.client.get(f"/api/projects/{project_id}/run").json()["state"]
            if state in TERMINAL_STATES:
                break
            time.sleep(0.05)

        restarted = self.client.post(
            f"/api/projects/{project_id}/run/restart",
            json={"confirm": True, "entrypoint_digest": digest},
        )
        self.assertEqual(restarted.status_code, 200)
        self.assertIn(restarted.json()["state"], (STARTING, RUNNING, SUCCEEDED))
        deadline = time.time() + 20
        status = restarted.json()
        while time.time() < deadline and status["state"] not in TERMINAL_STATES:
            time.sleep(0.05)
            status = self.client.get(f"/api/projects/{project_id}/run").json()
        self.assertEqual(status["state"], SUCCEEDED, status)

        stopped = self.client.post(f"/api/projects/{project_id}/run/stop")
        self.assertEqual(stopped.status_code, 200)
        self.assertIn(stopped.json()["state"], (STOPPED, SUCCEEDED))

    def test_29_the_api_payload_never_carries_the_raw_secret(self) -> None:
        self.write("app.py", PRINTS_SECRET)
        self.project = self._record(self.project, "app.py")
        digest = self.client.get(f"/api/projects/{self.project_id}").json()["entrypoint"]["digest"]
        self.client.post(
            f"/api/projects/{self.project_id}/run",
            json={"confirm": True, "entrypoint_digest": digest},
        )
        deadline = time.time() + 20
        status: dict = {}
        while time.time() < deadline:
            status = self.client.get(f"/api/projects/{self.project_id}/run").json()
            if status["state"] in TERMINAL_STATES:
                break
            time.sleep(0.05)
        self.assertEqual(status["state"], FAILED, status)
        log = self.client.get(f"/api/projects/{self.project_id}/run/log").json()
        for payload in (status, log):
            self.assertNotIn(SECRET, json.dumps(payload, ensure_ascii=False))
        self.assertIn("[redacted]", json.dumps(status, ensure_ascii=False).lower())


if __name__ == "__main__":  # pragma: no cover - convenience only
    unittest.main()
