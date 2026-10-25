"""The port a project really serves on: its own output first, then its sockets.

The owner's sentence this module answers:

    «Приложение не стало сервером: порт не открылся.»
    (Журнал: «Мейл АИ запущен / Адрес: http://127.0.0.1:8790»)

Forge said that **after** the application had printed its address on 8790 and was
answering there. The launch path had only one way to learn a port -- scan the child's
sockets -- and the child in that case never bound one: it found another instance
already serving, printed that instance's address, and exited 0. Reality was a running
server; the manifest was not the truth; the socket scan saw nothing.

So a port is no longer *assumed* from any declaration. It is **discovered**, in this
order, and every candidate is proven with a real HTTP answer before it is called
ready:

1. :func:`parse_banner_ports` reads the child's **own output** -- the address it chose,
   in the order it printed it. The accepted shapes are the ones real tools print:
   ``http://127.0.0.1:<port>``, ``Address: ...:<port>`` / ``Адрес: ...:<port>``,
   ``listening on ...:<port>``, ``Local: http://localhost:<port>``,
   ``Serving on port <port>``, ``Uvicorn running on http://...:<port>``, and a bare
   ``:<port>`` in a startup banner. A port outside the unprivileged range is ignored,
   and so is anything printed inside a Python traceback (a crash names ports too).
2. :func:`first_answering` checks each candidate with a real ``GET /`` on loopback.
   A bound socket is not yet a server: a process that binds and then dies before it
   accepts a request must never be declared ready. Any HTTP answer -- 200, 302, 401,
   404, 500 -- is proof that *a server answers*, which is the only thing this module
   claims.

The manifest keeps its place: it is the project's **claim** about itself
(:func:`manifest_port_claim`). When the claim and the observation disagree, reality
wins and the difference is said out loud (the launcher's notice, «Порт из манифеста
8765, фактический 8790»). Nothing here ever turns a claim into a URL.

Nothing in this module opens a socket into anything but loopback, starts a process,
reads a file, or knows about a project. It is observation only.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Iterable, Optional, Tuple
import urllib.error
import urllib.request

__all__ = [
    "PORT_MAX",
    "PORT_MIN",
    "PROBE_TIMEOUT_SECONDS",
    "first_answering",
    "is_usable_port",
    "loopback_url",
    "manifest_port_claim",
    "parse_banner_ports",
    "port_from_url",
    "probe_http",
]

#: The unprivileged port range. A banner that names 80 or 22 is not this project's
#: server: those are system services, and opening them would be a lie at best.
PORT_MIN = 1024
PORT_MAX = 65535

#: How long one loopback ``GET /`` may take before that candidate is "no answer".
#: One second, not one tick: a real application can take a moment to answer its very
#: first request (a cold runtime, a template read), and declaring it "not a server"
#: for that would be exactly the mistake this module exists to stop. A port nobody
#: answers refuses the connection immediately, so the cost is paid only by a server
#: that is genuinely slow to speak.
PROBE_TIMEOUT_SECONDS = 1.0

#: The loopback names a project address can carry. ``0.0.0.0`` is included because a
#: banner that says "listening on 0.0.0.0:8790" names a port this machine answers on.
_LOOPBACK_HOSTS = r"(?:127\.0\.0\.1|localhost|0\.0\.0\.0|\[::1\])"

#: ``http://127.0.0.1:8790`` -- the most explicit form, and the one Uvicorn, Flask and
#: a plain ``http.server`` print.
_URL = re.compile(r"https?://" + _LOOPBACK_HOSTS + r":(\d{2,5})", re.IGNORECASE)

#: ``Адрес: http://127.0.0.1:8790`` / ``Address: 127.0.0.1:8790``. The owner's own
#: application prints exactly this line.
_ADDRESS = re.compile(
    r"(?:адрес\w*|address)\s*:\s*[^\s]*?:(\d{2,5})", re.IGNORECASE
)

#: ``127.0.0.1:8790`` without a scheme -- "listening on 127.0.0.1:9001", "at
#: localhost:5173".
_HOST_PORT = re.compile(_LOOPBACK_HOSTS + r":(\d{2,5})", re.IGNORECASE)

#: ``listening on 127.0.0.1:8790``, ``listening 8790``, ``Listening at port 8790``.
_LISTENING = re.compile(
    r"(?:listening|listens)\b[^\d\n]{0,24}?(?:port\s*)?(\d{2,5})", re.IGNORECASE
)

#: ``Serving on port 8080``, ``Serving HTTP on 127.0.0.1 port 8000``.
_SERVING = re.compile(r"serv(?:ing|er)\b[^\d\n]{0,24}?(?:port\s*)?(\d{2,5})", re.IGNORECASE)

#: ``Running on http://127.0.0.1:8000``, ``Uvicorn running on ...``.
_RUNNING = re.compile(
    r"running\s+on\s+(?:https?://)?[^\s]*?:(\d{2,5})", re.IGNORECASE
)

#: ``port 8790``, ``порт 8790``, ``порту 8790``, ``port=8790``, ``port: 8790``.
_PORT_WORD = re.compile(r"(?:порт\w*|port)\s*[:=]?\s*(\d{2,5})", re.IGNORECASE)

#: The last resort the specification names: a bare ``:<port>`` in a startup banner.
#: It is consulted only on a line that also reads like a banner, so a random ratio or
#: a timestamp cannot turn into a port.
_BANNER_PORT = re.compile(r":(\d{2,5})\b")

_BANNER_WORDS = (
    "http",
    "listen",
    "serv",
    "running",
    "local",
    "server",
    "banner",
    "адрес",
    "порт",
    "запущ",
)

#: Every shape above, in the order they are tried inside one line.
_LINE_PATTERNS = (
    _URL,
    _ADDRESS,
    _HOST_PORT,
    _LISTENING,
    _SERVING,
    _RUNNING,
    _PORT_WORD,
)

#: Forge's own log header and stream markers are not the child's output.
_FORGE_MARKERS = ("--- stdout ---", "--- stderr ---", "--- truncated ---")

_TRACEBACK_HEADS = (
    "traceback (most recent call last)",
    "during handling of the above exception",
    "the above exception was the direct cause",
)


def is_usable_port(value: object) -> bool:
    """True for an integer port in the unprivileged range this module accepts."""

    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False
    return PORT_MIN <= number <= PORT_MAX


def _child_lines(lines: Iterable[Any]) -> Iterable[str]:
    """The child's own output lines, with tracebacks removed.

    A Python traceback names ports too (``OSError: 127.0.0.1:8765 busy``,
    ``serve("http://127.0.0.1:9999")``). Reading those as "the server is on this port"
    would refuse a launch with a crash still unexplained or, worse, open a URL from an
    exception message. The block is skipped from its header through its final
    exception line.
    """

    in_traceback = False
    for raw in lines or ():
        line = str(raw or "").rstrip("\r\n")
        stripped = line.strip()
        lowered = stripped.casefold()
        if lowered.startswith(_TRACEBACK_HEADS):
            in_traceback = True
            continue
        if in_traceback:
            if stripped == "":
                # A blank line ends the block (the usual "Traceback ... / ... " shape).
                in_traceback = False
                continue
            if line[:1] in (" ", "\t") or stripped.startswith(('File "', "File '")):
                # A frame (and its source line) belongs to the traceback.
                continue
            # The final exception line: part of the traceback, then the block ends.
            in_traceback = False
            continue
        if stripped == "" or stripped.startswith(_FORGE_MARKERS):
            continue
        if line.startswith("# "):
            # Forge's own persisted header (`# command: ...`), not the child's words.
            continue
        yield line


def _ports_in_line(line: str) -> Tuple[int, ...]:
    """The ports one line names, in the order they appear in it."""

    matches: list[Tuple[int, int]] = []
    for pattern in _LINE_PATTERNS:
        for match in pattern.finditer(line):
            matches.append((match.start(), int(match.group(1))))
    lowered = line.casefold()
    if any(word in lowered for word in _BANNER_WORDS):
        # The bare ``:<port>`` of a startup banner, tried alongside the explicit
        # shapes: a line may name more than one number ("listening on 127.0.0.1:9001"
        # matches both the host form and the colon form), and the range filter drops
        # the ones that cannot be a port of this project.
        for match in _BANNER_PORT.finditer(line):
            matches.append((match.start(), int(match.group(1))))
    matches.sort(key=lambda item: item[0])
    return tuple(port for _start, port in matches)


def parse_banner_ports(lines: Iterable[Any]) -> Tuple[int, ...]:
    """The ports a child's own output names, first match first, duplicates collapsed.

    The order is the order the child printed them, because the **first** address a
    program announces is the one it chose. Ports outside :data:`PORT_MIN`-:data:`PORT_MAX`
    are dropped and a traceback's ports are ignored (see :func:`_child_lines`).
    """

    found: list[int] = []
    for line in _child_lines(lines):
        for port in _ports_in_line(line):
            if is_usable_port(port) and port not in found:
                found.append(port)
    return tuple(found)


def port_from_url(url: object) -> int:
    """The port of a loopback URL, or ``0`` when it carries none this module accepts."""

    text = str(url or "").strip()
    match = re.match(
        r"^https?://" + _LOOPBACK_HOSTS + r":(\d{1,5})(?:[/?#]|$)", text, re.IGNORECASE
    )
    if match is None:
        return 0
    port = int(match.group(1))
    return port if is_usable_port(port) else 0


def loopback_url(port: object) -> str:
    """``http://127.0.0.1:<port>/`` for a usable port, else an empty string.

    A URL without a port is never built here: this function has exactly one shape.
    """

    return f"http://127.0.0.1:{int(port)}/" if is_usable_port(port) else ""


def probe_http(port: object, *, timeout: float = PROBE_TIMEOUT_SECONDS) -> int:
    """The HTTP status one loopback ``GET /`` produced, or ``0`` when nothing answers.

    Any status is an answer: a 200 page, a 302 redirect, a 401 login wall and even a
    500 all prove that *a server is listening and speaking HTTP* there. What this
    function refuses to do is read a bound socket as an answer -- a connection that
    does not reply within ``timeout`` is ``0``.

    Proxies are bypassed explicitly (``ProxyHandler({})``): a machine-wide HTTP proxy
    would otherwise answer for a loopback port and make Forge open an address that
    belongs to nobody.
    """

    if not is_usable_port(port):
        return 0
    url = loopback_url(port)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    request = urllib.request.Request(
        url,
        method="GET",
        headers={"User-Agent": "forge-port-probe/1", "Accept": "*/*"},
    )
    try:
        with opener.open(request, timeout=float(timeout)) as response:
            return int(getattr(response, "status", 0) or 0)
    except urllib.error.HTTPError as exc:
        # The server answered, with a status urllib treats as an error. Still an answer.
        return int(getattr(exc, "code", 0) or 0)
    except Exception:  # noqa: BLE001 - refused, timed out, not HTTP: no answer
        return 0


def first_answering(
    candidates: Iterable[object],
    *,
    probe: Optional[Callable[[int], int]] = None,
) -> Tuple[int, int]:
    """``(port, status)`` of the first candidate a real ``GET /`` answers on.

    ``(0, 0)`` when no candidate answers -- which is the *only* fact that justifies
    "the application did not become a server". The check is injectable so a test can
    describe a machine without opening a socket; the default is the real probe.
    """

    check = probe if probe is not None else probe_http
    seen: set[int] = set()
    for value in candidates or ():
        if not is_usable_port(value):
            continue
        port = int(value)  # type: ignore[arg-type]
        if port in seen:
            continue
        seen.add(port)
        status = 0
        try:
            status = int(check(port) or 0)
        except Exception:  # noqa: BLE001 - a probe that raises is "no answer"
            status = 0
        if status > 0:
            return port, status
    return 0, 0


def manifest_port_claim(text: object) -> int:
    """The port a project's manifest **claims**, or ``0`` when it claims none.

    This is a claim, never a fact: the manifest is what the project was asked for and
    what it says about itself. A full loopback URL is the strongest claim and wins
    over a bare "порт 993" (which is usually a mail server, not this application's
    own port). The caller compares this with the observed port and says both out
    loud; nothing opens on it.
    """

    body = str(text or "")
    for match in _URL.finditer(body):
        port = int(match.group(1))
        if is_usable_port(port):
            return port
    for match in _PORT_WORD.finditer(body):
        port = int(match.group(1))
        if is_usable_port(port):
            return port
    return 0
