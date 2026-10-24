"""Containment for a launched project process: what is real, and what is measured.

Everything here is offline and deterministic. No provider is called, no credential is
read, and every process this file starts is its own child in a scratch folder under
``.forge/``.

The rules this file pins down, one test group each:

* **The job object is real.** A child that spawns a grandchild is stopped, and *both*
  pids are gone afterwards -- asserted by pid, never by image name.
* **The limits are the kernel's, not a comment.** The memory cap and the
  active-process cap reported in the payload are read back from the OS
  (``QueryInformationJobObject``); a child that allocates past the memory cap cannot
  finish.
* **The write boundary is measured, not assumed.** A child tries to write into the
  project store root -- Forge's own data -- and the payload's ``writes_outside`` is
  compared with what the child itself reported. The test derives its expectation from
  that measurement, so it states the truth on a host where the boundary is blocked and
  on one where it is not (for example when Forge itself already runs at low integrity
  and its data is low-labelled).
* **The environment is minimal.** ``FORGE_SANDBOX=1`` is present, no ``*_API_KEY``
  is, and a credential-shaped name planted in the parent's environment never appears.
* **The payload never upgrades a fact.** ``sandbox`` carries exactly the words the OS
  and the probe produced; ``network`` is always "inherited (not isolated)".
* **A refused, never-run or disabled sandbox never reports a contained state.** The
  green combination (job object *and* restricted token *and* blocked writes) can only
  be true when all three were really measured.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import time
import unittest

from unittest import mock

from app.execution import containment as containment_module
from app.execution.containment import (
    NETWORK_INHERITED,
    SandboxLimits,
    directory_integrity_level,
    probe_write_boundary,
    process_integrity_level,
)
from app.projects.entrypoint import detect_entry_point
from app.projects.models import ArtifactRef
from app.projects.runner import (
    FAILED,
    NOT_RUN,
    REFUSED,
    RUNNING,
    STOPPED,
    SUCCEEDED,
    TERMINAL_STATES,
    TIMED_OUT,
    LocalRunner,
    RunTarget,
)
from app.projects.store import ProjectStore

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRATCH = REPO_ROOT / ".forge" / "test-run-sandbox"

PROJECT_ID = "proj-5a4d3b0c0001"

#: A credential-shaped variable name. It ends with ``_API_KEY``, so the runner's own
#: rule must keep it out of the child, and the test must not find its value anywhere.
PLANTED_KEY = "FORGE_SANDBOX_TEST_API_KEY"
PLANTED_VALUE = "sk-" + "Q9z" * 20

SPAWNS_GRANDCHILD = """\
import os
import subprocess
import sys
import time

grandchild = subprocess.Popen([sys.executable, "grandchild.py"])
print("grandchild-pid %d" % grandchild.pid, flush=True)
print("parent-pid %d" % os.getpid(), flush=True)
time.sleep(120)
"""

GRANDCHILD = """\
import time

print("grandchild-up", flush=True)
time.sleep(120)
"""

SLEEPS = """\
import time

print("sleeping", flush=True)
time.sleep(120)
"""

ALLOCATES_PAST_THE_CAP = """\
print("allocating", flush=True)
blob = bytearray(300 * 1024 * 1024)
for offset in range(0, len(blob), 1024 * 1024):
    blob[offset] = 1
print("allocated", flush=True)
import time
time.sleep(5)
"""

WRITES_INTO_FORGE_DATA = """\
import os

print("hello", flush=True)
with open("own-file.txt", "w", encoding="utf-8") as handle:
    handle.write("inside")
print("own-write ok", flush=True)
target = {target!r}
if target:
    try:
        with open(target, "w", encoding="utf-8") as handle:
            handle.write("outside")
        print("CHILD-WRITE-OK", flush=True)
        try:
            os.unlink(target)
        except OSError:
            pass
    except OSError as exc:
        print("CHILD-WRITE-DENIED %s" % type(exc).__name__, flush=True)
print("done", flush=True)
"""


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


class _SandboxCase(unittest.TestCase):
    """A scratch store, a workspace and one runner per test."""

    def setUp(self) -> None:
        self.base = SCRATCH / f"{os.getpid()}-{id(self)}"
        shutil.rmtree(self.base, ignore_errors=True)
        self.base.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(self.base, ignore_errors=True))
        self.store = ProjectStore(root=self.base / "projects")
        self.workspace = self.store.workspace_path(PROJECT_ID, create=True)
        self.runner = self._runner()
        self.addCleanup(self.runner.stop_all)

    def _runner(self, **kwargs: object) -> LocalRunner:
        options = dict(timeout=30.0, startup_timeout=1.0)
        options.update(kwargs)
        runner = LocalRunner(store=self.store, previews=None, **options)
        self.addCleanup(runner.stop_all)
        return runner

    # ------------------------------------------------------------------ helpers
    def write(self, relative: str, body: str) -> pathlib.Path:
        target = self.workspace.joinpath(*relative.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
        return target

    def target(self, entry: object) -> RunTarget:
        return RunTarget(
            project_id=PROJECT_ID,
            workspace=self.workspace,
            entry=entry,
            project_path=f".forge/projects/{PROJECT_ID}/workspace",
        )

    def entry_for(self, manifest: list) -> object:
        return detect_entry_point(manifest, workspace=self.workspace)

    def start_approved(
        self, target: RunTarget, runner: LocalRunner | None = None
    ) -> dict:
        active = runner or self.runner
        token = active.confirm(target, confirm=True, digest=active.digest_for(target))
        self.assertIsNotNone(token, "the digest shown must be approved")
        return active.start(target, token)

    def wait(
        self,
        target: RunTarget,
        *,
        runner: LocalRunner | None = None,
        limit: float = 30.0,
    ) -> dict:
        active = runner or self.runner
        status = active.status(target)
        deadline = time.time() + limit
        while time.time() < deadline and status["state"] not in TERMINAL_STATES:
            time.sleep(0.05)
            status = active.status(target)
        return status

    def wait_for(
        self,
        target: RunTarget,
        predicate: object,
        *,
        limit: float = 20.0,
        runner: LocalRunner | None = None,
    ) -> dict:
        active = runner or self.runner
        status = active.status(target)
        deadline = time.time() + limit
        while time.time() < deadline and not predicate(status):
            time.sleep(0.05)
            status = active.status(target)
        return status

    def log_text(self, status: dict) -> str:
        return "\n".join(status.get("log", {}).get("lines", ()))

    def store_root(self) -> pathlib.Path:
        return pathlib.Path(self.store.root)


class JobObjectTests(_SandboxCase):
    def test_01_stop_kills_the_child_and_its_grandchild_by_pid(self) -> None:
        self.write("app.py", SPAWNS_GRANDCHILD)
        self.write("grandchild.py", GRANDCHILD)
        entry = self.entry_for(
            [ArtifactRef(path="app.py"), ArtifactRef(path="grandchild.py")]
        )
        self.assertIsNotNone(entry)
        target = self.target(entry)
        status = self.start_approved(target)
        self.assertEqual(status["sandbox"]["network"], NETWORK_INHERITED)

        status = self.wait_for(target, lambda value: "grandchild-pid" in self.log_text(value))
        grandchild_pid = None
        for line in status["log"]["lines"]:
            if line.startswith("grandchild-pid "):
                grandchild_pid = int(line.split(" ")[1])
        self.assertIsNotNone(grandchild_pid, status["log"]["lines"])
        parent_pid = status["pid"]
        self.assertIsInstance(parent_pid, int)
        self.assertTrue(_process_alive(parent_pid), "the child is running before stop")
        self.assertTrue(_process_alive(grandchild_pid), "the grandchild is running before stop")

        stopped = self.runner.stop(PROJECT_ID)
        self.assertEqual(stopped["state"], STOPPED)
        self.assertEqual(stopped["sandbox"]["network"], NETWORK_INHERITED)

        deadline = time.time() + 10.0
        while time.time() < deadline and (
            _process_alive(parent_pid) or _process_alive(grandchild_pid)
        ):
            time.sleep(0.1)
        self.assertFalse(_process_alive(parent_pid), "the child is gone by pid")
        self.assertFalse(_process_alive(grandchild_pid), "the grandchild is gone by pid")

    def test_02_the_memory_cap_is_reported_and_a_child_cannot_exceed_it(self) -> None:
        runner = self._runner(memory_mb=256, max_processes=4)
        self.write("app.py", ALLOCATES_PAST_THE_CAP)
        entry = self.entry_for([ArtifactRef(path="app.py")])
        target = self.target(entry)
        self.start_approved(target, runner)
        status = self.wait(target, runner=runner, limit=60.0)

        limits = status["sandbox"]["limits"]
        self.assertEqual(limits["memory_mb"], 256, status["sandbox"])
        self.assertEqual(limits["active_process_limit"], 4, status["sandbox"])
        self.assertTrue(status["sandbox"]["detail"]["kill_on_close"], status["sandbox"])
        # Either the kernel refused the allocation or it killed the process; what is
        # not allowed is the child finishing a 300 MiB allocation under a 256 MiB cap.
        lines = self.log_text(status)
        self.assertIn("allocating", lines, lines)
        self.assertNotIn("allocated", lines, lines)
        self.assertIn(status["state"], (FAILED, TIMED_OUT), status["state"])

    def test_03_the_process_cap_and_memory_cap_are_read_back_from_the_os(self) -> None:
        self.write("app.py", 'print("ok")\n')
        entry = self.entry_for([ArtifactRef(path="app.py")])
        target = self.target(entry)
        self.start_approved(target)
        status = self.wait(target)
        self.assertEqual(status["state"], SUCCEEDED, status)
        sandbox = status["sandbox"]
        if os.name != "nt":
            self.assertFalse(sandbox["job_object"])
            return
        self.assertTrue(sandbox["job_object"], sandbox)
        self.assertEqual(sandbox["limits"]["memory_mb"], 1024, sandbox)
        self.assertEqual(sandbox["limits"]["active_process_limit"], 16, sandbox)
        self.assertEqual(sandbox["limits"]["max_processes"], 16, sandbox)
        self.assertTrue(sandbox["detail"]["kill_on_close"], sandbox)


class WriteBoundaryTests(_SandboxCase):
    def test_04_a_write_into_forge_data_is_recorded_as_measured(self) -> None:
        """The child's own attempt decides the expectation, not the test's belief."""

        target_path = self.store_root() / f".child-write-probe-{os.getpid()}.txt"
        self.addCleanup(lambda: target_path.unlink(missing_ok=True))
        self.write("app.py", WRITES_INTO_FORGE_DATA.format(target=str(target_path)))
        entry = self.entry_for([ArtifactRef(path="app.py")])
        target = self.target(entry)
        self.start_approved(target)
        status = self.wait(target)
        self.assertEqual(status["state"], SUCCEEDED, status)
        lines = self.log_text(status)
        sandbox = status["sandbox"]
        self.assertIn(sandbox["writes_outside"], ("blocked", "allowed", "unknown"), sandbox)
        if "CHILD-WRITE-OK" in lines:
            self.assertEqual(sandbox["writes_outside"], "allowed", sandbox)
            self.assertFalse(target_path.exists(), "the child cleaned up its own probe")
        elif "CHILD-WRITE-DENIED" in lines:
            self.assertEqual(sandbox["writes_outside"], "blocked", sandbox)
            self.assertFalse(target_path.exists(), "a blocked write left no file")
        else:  # pragma: no cover - the child always reports one of the two
            self.assertEqual(sandbox["writes_outside"], "unknown", sandbox)
        # The probe leaves nothing behind in Forge's own data directory.
        leftovers = [
            item.name
            for item in self.store_root().iterdir()
            if item.name.startswith(".forge-sandbox-probe-")
        ]
        self.assertEqual(leftovers, [], leftovers)

    def test_05_the_child_can_write_its_own_project_folder(self) -> None:
        self.write("app.py", WRITES_INTO_FORGE_DATA.format(target=""))
        entry = self.entry_for([ArtifactRef(path="app.py")])
        target = self.target(entry)
        self.start_approved(target)
        status = self.wait(target)
        self.assertEqual(status["state"], SUCCEEDED, status)
        self.assertIn("own-write ok", self.log_text(status))
        self.assertTrue((self.workspace / "own-file.txt").is_file())


class EnvironmentTests(_SandboxCase):
    def test_06_the_child_gets_forge_sandbox_and_no_api_key(self) -> None:
        os.environ[PLANTED_KEY] = PLANTED_VALUE
        self.addCleanup(os.environ.pop, PLANTED_KEY, None)
        self.write(
            "app.py",
            "import json, os\n"
            "print(json.dumps({key: os.environ[key] for key in sorted(os.environ)}))\n",
        )
        entry = self.entry_for([ArtifactRef(path="app.py")])
        target = self.target(entry)
        self.start_approved(target)
        status = self.wait(target)
        self.assertEqual(status["state"], SUCCEEDED, status)
        line = next(
            (item for item in status["log"]["lines"] if item.strip().startswith("{")),
            "",
        )
        environment = json.loads(line)
        self.assertEqual(environment.get("FORGE_SANDBOX"), "1", environment)
        self.assertEqual(
            [key for key in environment if key.upper().endswith("_API_KEY")], []
        )
        self.assertNotIn(PLANTED_VALUE, line)
        self.assertNotIn(PLANTED_VALUE, json.dumps(status, ensure_ascii=False))
        self.assertNotIn(PLANTED_KEY, environment)


class PayloadTests(_SandboxCase):
    def test_07_the_payload_shape_is_exact_and_truthful(self) -> None:
        self.write("app.py", SLEEPS)
        entry = self.entry_for([ArtifactRef(path="app.py")])
        target = self.target(entry)
        self.start_approved(target)
        status = self.wait_for(target, lambda value: value["state"] == RUNNING)
        self.assertEqual(status["state"], RUNNING, status)

        sandbox = status["sandbox"]
        for key in ("job_object", "restricted_token"):
            self.assertIsInstance(sandbox[key], bool, sandbox)
        self.assertIn(sandbox["integrity"], ("restricted_token", "unchanged"), sandbox)
        self.assertEqual(
            sandbox["restricted_token"], sandbox["integrity"] == "restricted_token"
        )
        self.assertIn(
            sandbox["writes_outside"], ("blocked", "allowed", "unknown"), sandbox
        )
        self.assertEqual(sandbox["network"], NETWORK_INHERITED)
        limits = sandbox["limits"]
        for key in ("memory_mb", "max_processes", "active_process_limit"):
            self.assertIsInstance(limits[key], int, limits)
        if sandbox["job_object"]:
            self.assertGreater(limits["memory_mb"], 0, sandbox)
            self.assertGreater(limits["active_process_limit"], 0, sandbox)
        else:
            self.assertEqual(limits["memory_mb"], 0, sandbox)
            self.assertEqual(limits["active_process_limit"], 0, sandbox)
        if os.name == "nt":
            # The child really is below the owner's normal level while it is alive, and
            # the level is what the OS reports for its pid.
            self.assertEqual(process_integrity_level(status["pid"]), "low", sandbox)
            self.assertTrue(sandbox["restricted_token"], sandbox)
        self.runner.stop(PROJECT_ID)

    def test_08_a_never_run_project_reports_no_containment(self) -> None:
        self.write("app.py", 'print("ok")\n')
        entry = self.entry_for([ArtifactRef(path="app.py")])
        status = self.runner.status(self.target(entry))
        self.assertEqual(status["state"], NOT_RUN)
        sandbox = status["sandbox"]
        self.assertFalse(sandbox["job_object"])
        self.assertFalse(sandbox["restricted_token"])
        self.assertEqual(sandbox["integrity"], "unchanged")
        self.assertEqual(sandbox["writes_outside"], "unknown")
        self.assertEqual(sandbox["network"], NETWORK_INHERITED)
        self.assertEqual(sandbox["limits"]["memory_mb"], 0)

    def test_09_a_refused_run_reports_no_containment(self) -> None:
        self.write("app.py", 'print("ok")\n')
        entry = self.entry_for([ArtifactRef(path="app.py")])
        target = self.target(entry)
        refused = self.runner.start(target, None)
        self.assertEqual(refused["state"], REFUSED)
        sandbox = refused["sandbox"]
        self.assertFalse(sandbox["job_object"])
        self.assertFalse(sandbox["restricted_token"])
        self.assertEqual(sandbox["writes_outside"], "unknown")
        self.assertEqual(sandbox["network"], NETWORK_INHERITED)
        self.assertFalse(self._green(sandbox))

    def test_10_a_disabled_sandbox_reports_no_containment(self) -> None:
        runner = self._runner(sandbox_enabled=False)
        self.write("app.py", 'print("still-runs")\n')
        entry = self.entry_for([ArtifactRef(path="app.py")])
        target = self.target(entry)
        self.start_approved(target, runner)
        status = self.wait(target, runner=runner)
        self.assertEqual(status["state"], SUCCEEDED, status)
        self.assertIn("still-runs", self.log_text(status))
        sandbox = status["sandbox"]
        self.assertFalse(sandbox["job_object"], sandbox)
        self.assertEqual(sandbox["writes_outside"], "unknown", sandbox)
        self.assertTrue(sandbox["detail"]["disabled"], sandbox)
        self.assertFalse(self._green(sandbox))

    @staticmethod
    def _green(sandbox: dict) -> bool:
        return bool(
            sandbox["job_object"]
            and sandbox["restricted_token"]
            and sandbox["writes_outside"] == "blocked"
        )


class ServerLifetimeTests(_SandboxCase):
    SERVING = """\
import http.server, socketserver

class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"LIFETIME-OK\\n"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        return

with socketserver.TCPServer(("127.0.0.1", 0), Handler) as httpd:
    print("listening %d" % httpd.server_address[1], flush=True)
    httpd.serve_forever()
"""

    def test_11_a_configured_server_lifetime_terminates_the_job(self) -> None:
        runner = self._runner(server_lifetime=2.0)
        self.write("app.py", self.SERVING)
        entry = self.entry_for([ArtifactRef(path="app.py")])
        target = self.target(entry)
        self.start_approved(target, runner)
        status = self.wait_for(target, lambda value: value["state"] == RUNNING, runner=runner)
        self.assertEqual(status["state"], RUNNING, status)
        pid = status["pid"]
        status = self.wait_for(
            target,
            lambda value: value["state"] in TERMINAL_STATES,
            runner=runner,
            limit=30.0,
        )
        self.assertEqual(status["state"], TIMED_OUT, status)
        self.assertIn("lifetime", status["reason"])
        deadline = time.time() + 10.0
        while time.time() < deadline and _process_alive(pid):
            time.sleep(0.1)
        self.assertFalse(_process_alive(pid))


class PrimitiveTests(unittest.TestCase):
    def test_12_the_limit_value_objects_refuse_negative_numbers(self) -> None:
        limits = SandboxLimits(memory_mb=-1, max_processes=-5).sanitized()
        self.assertEqual(limits.memory_mb, 0)
        self.assertEqual(limits.max_processes, 0)
        self.assertEqual(SandboxLimits().memory_mb, 1024)

    def test_13_the_project_folder_label_is_a_measured_fact(self) -> None:
        label = directory_integrity_level(REPO_ROOT)
        self.assertIn(label, ("low", "medium", "high", "unknown"))

    def test_14_a_probe_that_times_out_is_unknown_and_not_an_exception(self) -> None:
        """A hung probe must be reported, never crash the start it measured.

        Measured in the full suite: a probe that timed out left ``stdout`` unbound,
        ``probe_write_boundary`` raised ``UnboundLocalError``, and the launch reported
        a failed *start* instead of the honest ``probe_error: probe_timeout``.
        """

        if os.name != "nt":  # pragma: no cover - the probe is Windows-only
            self.skipTest("the write-boundary probe is Windows-only")
        base = SCRATCH / f"probe-timeout-{os.getpid()}-{id(self)}"
        shutil.rmtree(base, ignore_errors=True)
        (base / "outside").mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(base, ignore_errors=True))

        class _HungProbe:
            pid = 424242

            def poll(self):
                return 0

            def communicate(self, timeout=None):
                raise subprocess.TimeoutExpired("probe", timeout or 0)

        with mock.patch.object(
            containment_module.subprocess, "Popen", return_value=_HungProbe()
        ), mock.patch.object(
            containment_module, "create_process_tree_job", return_value=None
        ):
            reply = probe_write_boundary(
                interpreter="python",
                cwd=base,
                environment={},
                workspace=base,
                outside_directory=base / "outside",
                limits=SandboxLimits(memory_mb=0, max_processes=0),
                drop_integrity=False,
            )
        self.assertEqual(reply["probe_error"], "probe_timeout")
        self.assertEqual(reply["writes_inside"], "unknown")
        self.assertEqual(reply["writes_outside"], "unknown")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
