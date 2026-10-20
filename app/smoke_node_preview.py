"""End-to-end smoke of the local Node run and its preview, offline and repeatable.

Run it with::

    .\\\\.venv\\\\Scripts\\\\python.exe -m app.smoke_node_preview

Exit code 0 means either that every step below really ran, or that this machine has
no ``node`` -- in which case the smoke prints ``SKIPPED: node is not installed`` and
exits 0. A skip is stated, never disguised as a pass.

The sequence is the same as :mod:`app.smoke_python_preview`, with a tiny
``node:http`` server materialised through the real writer:

1. **materialize** -- a fenced ``server.js`` is applied inside a fresh project
   workspace (the writer's own boundary, caps and verification apply);
2. **entrypoint** -- the manifest resolves to ``node_script`` with the absolute
   ``node`` this machine has;
3. **approval** -- an explicit approval bound to the digest the owner would be shown;
4. **start** -- the process starts, and the supervisor watches it;
5. **preview** -- the process binds a loopback port, the URL is published, and a real
   ``GET`` returns 200 with the project's own body;
6. **stop** -- the process tree is terminated and the preview is unregistered.

Everything is written under ``.forge/smoke-node-preview`` (wiped at start).
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import List, Optional, Sequence

from app.projects.entrypoint import (
    ENTRY_KIND_NODE_SCRIPT,
    resolve_entry,
    node_executable,
    reset_cached_interpreters,
)
from app.projects.materialize import materialize_into
from app.projects.preview import PreviewRegistry
from app.projects.runner import TERMINAL_STATES, LocalRunner, RunTarget
from app.projects.store import ProjectStore, new_project_id, repository_root

__all__ = ["main"]


SMOKE_ROOT = repository_root() / ".forge" / "smoke-node-preview"

ANSWER = """A tiny local server.

```javascript path=server.js
const http = require('node:http');

const server = http.createServer((request, response) => {
  const body = 'FORGE NODE PREVIEW OK\\n';
  response.writeHead(200, {
    'Content-Type': 'text/plain; charset=utf-8',
    'Content-Length': Buffer.byteLength(body),
  });
  response.end(body);
});

server.listen(0, '127.0.0.1', () => {
  console.log('listening ' + server.address().port);
});
```
"""


def _line(label: str, value: object) -> None:
    print(f"  {label}: {value}")


def _relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(repository_root()).as_posix()
    except ValueError:
        return path.as_posix()


def _get(url: str, timeout: float = 10.0) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return int(response.status), response.read()
    except urllib.error.HTTPError as error:
        return int(error.code), error.read()
    except urllib.error.URLError:
        return 0, b""


def _wait(runner: LocalRunner, target: RunTarget, limit: float = 30.0) -> dict:
    status = runner.status(target)
    deadline = time.time() + limit
    while time.time() < deadline:
        if status["state"] == "RUNNING" and status.get("port"):
            return status
        if status["state"] in TERMINAL_STATES:
            return status
        time.sleep(0.1)
        status = runner.status(target)
    return status


def main(argv: Optional[Sequence[str]] = None) -> int:
    reset_cached_interpreters()
    node = node_executable()
    if node is None:
        print("SKIPPED: node is not installed")
        return 0

    root = SMOKE_ROOT
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True, exist_ok=True)
    store = ProjectStore(root=root / "projects")
    project_id = new_project_id()
    workspace = store.workspace_path(project_id, create=True)

    print("FORGE NODE LOCAL RUN SMOKE")
    print(f"  scratch root: {_relative(root)}")
    print(f"  project_id: {project_id}")
    print(f"  node: {node}")
    failures: List[str] = []

    # ------------------------------------------------------------ 1. materialize
    result = materialize_into(
        workspace,
        ANSWER,
        f"run-smoke-{project_id}",
        project_id=project_id,
        project_path=store.project_path_relative(project_id),
    )
    print("\n[1] MATERIALIZE")
    _line("status", result.status)
    _line("verification", result.verification)
    _line("files", [item.path for item in result.files])
    if not any(item.path == "server.js" for item in result.files):
        failures.append("the materialiser did not write server.js")

    # ------------------------------------------------------------- 2. entrypoint
    resolution = resolve_entry(result.files, workspace=workspace)
    entry = resolution.entry
    print("\n[2] ENTRYPOINT (from the manifest only)")
    _line("reason", resolution.reason)
    if entry is None:
        print("\nRESULT: FAILED (no entry point)")
        return 1
    _line("kind", entry.kind)
    _line("path", entry.path)
    _line("language", entry.language)
    _line("run command", entry.run_command)
    _line("digest", entry.digest)
    if entry.kind != ENTRY_KIND_NODE_SCRIPT:
        failures.append(f"expected node_script, got {entry.kind}")
    if not entry.command or entry.command[0] != node:
        failures.append("the entry point does not name this machine's node")

    previews = PreviewRegistry()
    runner = LocalRunner(store=store, previews=previews, timeout=60.0, startup_timeout=15.0)
    target = RunTarget(
        project_id=project_id,
        workspace=workspace,
        entry=entry,
        project_path=store.project_path_relative(project_id),
    )
    try:
        print("\n[3] APPROVAL (explicit, bound to the digest)")
        token = runner.confirm(target, confirm=True, digest=entry.digest)
        _line("approval issued", bool(token))
        if not token:
            failures.append("the approval was not issued")

        print("\n[4] START")
        started = runner.start(target, token)
        _line("state", started["state"])
        _line("command", started["command"])
        _line("pid", started["pid"])
        _line("network policy", started["network_policy"])
        if started["state"] in TERMINAL_STATES:
            failures.append(f"the run did not start: {started['state']} {started['reason']}")

        status = _wait(runner, target)
        _line("state after wait", status["state"])
        _line("serving", status["serving"])
        _line("port", status["port"])
        _line("url", status["url"])
        _line("log tail", status["log"]["lines"][-6:])
        if status["state"] != "RUNNING" or not status["port"]:
            failures.append("the process did not bind a local port")

        print("\n[5] PREVIEW")
        url = status["url"]
        if url:
            code, body = _get(url)
            _line("GET", f"{url} -> {code}")
            _line("body", body)
            if code != 200:
                failures.append(f"the preview answered {code}")
            if b"FORGE NODE PREVIEW OK" not in body:
                failures.append("the preview body is not the project's own")
        else:
            failures.append("no preview URL was published")
        if not previews.is_running(project_id):
            failures.append("the preview registry does not report the process preview")

        print("\n[6] STOP")
        stopped = runner.stop(project_id)
        _line("state", stopped["state"])
        _line("preview registered after stop", previews.is_running(project_id))
        if stopped["state"] != "STOPPED":
            failures.append(f"the process did not stop: {stopped['state']}")
        if previews.is_running(project_id):
            failures.append("the preview was not unregistered after stop")
        pid = int(status.get("pid") or 0)
        if pid:
            completed = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                capture_output=True,
                check=False,
            )
            gone = str(pid) not in (completed.stdout or b"").decode("utf-8", errors="replace")
            _line("process gone", gone)
            if not gone:
                failures.append("the process is still alive after stop")

        print("\nSUMMARY")
        _line("project_id", project_id)
        _line("workspace", store.project_path_relative(project_id))
        _line("entry kind", entry.kind)
        _line("run command", entry.run_command)
        _line("preview url (while running)", url)
        _line("final run state", stopped["state"])
    finally:
        runner.stop_all()
        previews.stop_all()

    if failures:
        print("\nFAILED:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("\nRESULT: OK (exit 0)")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised by running the module
    raise SystemExit(main(sys.argv[1:]))
