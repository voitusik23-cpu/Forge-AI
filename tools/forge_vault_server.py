"""Local vault of AI-provider accounts, served on loopback only.

The owner keeps emails, passwords and API keys of provider accounts in one place and
checks the balances the Forge Core already knows how to read. This module is the whole
backend: a small stdlib HTTP server in front of ``.forge/vault/vault.json``.

Design notes, each answering a requirement:

* **Plaintext, local, stated.** The vault is a local JSON file under ``.forge/`` (ignored
  by git). Passwords and keys are stored as typed -- no fake encryption -- and the page
  says so in plain words. The file is written with owner-only permissions where the OS
  supports it.
* **The raw key does not travel by default.** ``GET /api/accounts`` returns only
  ``sk-...abcd``; the page cannot read a real secret until the owner asks for it
  (``POST /api/accounts/{id}/reveal``), which is exactly the copy action.
* **Atomic writes.** Every change goes through a temp file plus ``os.replace`` in the same
  directory, so a crash mid-write cannot leave a half-written vault.
* **Loopback only, no ambient authority.** The socket binds ``127.0.0.1``; a request whose
  ``Host`` is not loopback, or whose ``Origin`` is a foreign site, is refused (that is what
  stops a web page in another tab from reading or editing the vault). Only ``/`` and
  ``/index.html`` are served; no request path is ever turned into a filesystem path, so
  there is no directory traversal to exploit.
* **Balances are honest.** ``/api/balances`` proxies the Forge Core on 127.0.0.1:8011 and
  returns the providers it reports; when the Core is down the answer is
  ``{"available": false, "reason": ...}``, never a made-up number.

Usage:
    python tools/forge_vault_server.py [--port 8030]
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import secrets
import socketserver
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler

REPO = pathlib.Path(__file__).resolve().parent.parent
VAULT_DIR = REPO / ".forge" / "vault"
VAULT_FILE = VAULT_DIR / "vault.json"
APP_FILE = REPO / "tools" / "vault_app" / "index.html"
FORGE_BALANCE = "http://127.0.0.1:8011/api/providers/balance"
DEFAULT_PORT = 8030

MAX_BODY = 64 * 1024
ID_RE = re.compile(r"^[a-z0-9]{8,32}$")
PROVIDER_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,31}$")
LOOPBACK_NAMES = {"127.0.0.1", "localhost", "::1"}

#: Field name -> maximum accepted length. Anything longer is a mistake, not data.
FIELD_LIMITS = {
    "provider": 32,
    "label": 120,
    "model": 120,
    "email": 200,
    "password": 400,
    "api_key": 400,
    "color": 16,
    "emoji": 16,
}

#: The vault starts with visible examples so the window is never an empty grid. They are
#: marked ``demo`` in the file, carry placeholder secrets and are removed in one click.
DEMO_ACCOUNTS = [
    {
        "provider": "deepseek",
        "label": "DeepSeek",
        "model": "deepseek-chat",
        "email": "owner@example.com",
        "password": "demo-password",
        "api_key": "sk-demo-deepseek-000000000001",
        "color": "#4a86c8",
        "emoji": "\U0001f433",
    },
    {
        "provider": "openrouter",
        "label": "OpenRouter",
        "model": "openai/gpt-4o-mini",
        "email": "owner@example.com",
        "password": "demo-password",
        "api_key": "sk-demo-openrouter-000000000002",
        "color": "#7b6ad6",
        "emoji": "\U0001f310",
    },
    {
        "provider": "openai",
        "label": "OpenAI",
        "model": "gpt-4o",
        "email": "",
        "password": "",
        "api_key": "sk-demo-openai-000000000003",
        "color": "#2fb37a",
        "emoji": "\u25c6",
    },
    {
        "provider": "gemini",
        "label": "Google Gemini",
        "model": "gemini-2.0-flash",
        "email": "",
        "password": "",
        "api_key": "AIza-demo-gemini-0000000004",
        "color": "#c9a227",
        "emoji": "\u2726",
    },
    {
        "provider": "anthropic",
        "label": "Anthropic",
        "model": "claude-sonnet-4",
        "email": "",
        "password": "",
        "api_key": "sk-ant-demo-000000000005",
        "color": "#c96f45",
        "emoji": "\u2733",
    },
    {
        "provider": "nvidia",
        "label": "NVIDIA NIM",
        "model": "nvidia/llama-3.1-nemotron",
        "email": "",
        "password": "",
        "api_key": "nvapi-demo-000000000006",
        "color": "#76b900",
        "emoji": "\u25a3",
    },
]

_LOCK = threading.Lock()


# --------------------------------------------------------------------------- storage


def ensure_vault() -> None:
    """Create the vault (with examples) the first time, and the directory always."""

    VAULT_DIR.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        if VAULT_FILE.exists():
            return
        accounts = []
        for seed in DEMO_ACCOUNTS:
            account = dict(seed)
            account["id"] = secrets.token_hex(8)
            account["demo"] = True
            accounts.append(account)
        _save_locked({"accounts": accounts})


def _read_vault() -> dict:
    """Return the vault as a dict; a missing or unreadable file reads as empty."""

    if not VAULT_FILE.exists():
        return {"accounts": []}
    try:
        data = json.loads(VAULT_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"accounts": []}
    if not isinstance(data, dict) or not isinstance(data.get("accounts"), list):
        return {"accounts": []}
    accounts = [row for row in data["accounts"] if isinstance(row, dict) and row.get("id")]
    return {"accounts": accounts}


def _save_locked(data: dict) -> None:
    """Write the vault atomically: temp file in the same directory, then os.replace."""

    VAULT_DIR.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    temp = VAULT_FILE.parent / (VAULT_FILE.name + ".tmp")
    try:
        with open(temp, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        try:  # owner-only; Windows may ignore the mode, which is fine
            os.chmod(temp, 0o600)
        except OSError:
            pass
        os.replace(temp, VAULT_FILE)
    finally:
        if temp.exists():
            try:
                temp.unlink()
            except OSError:
                pass


def save_vault(data: dict) -> None:
    with _LOCK:
        _save_locked(data)


def mask_secret(value: str) -> str:
    """``sk-...abcd``: enough to recognise the key, never enough to use it."""

    if not value:
        return ""
    if len(value) < 10:
        return "\u2026" + value[-2:]
    return value[:3] + "\u2026" + value[-4:]


def masked_account(account: dict) -> dict:
    """The only shape of an account the page ever receives."""

    return {
        "id": account.get("id", ""),
        "provider": account.get("provider", ""),
        "label": account.get("label") or account.get("provider", ""),
        "model": account.get("model", ""),
        "email": account.get("email", ""),
        "color": account.get("color", ""),
        "emoji": account.get("emoji", ""),
        "demo": bool(account.get("demo")),
        "api_key_masked": mask_secret(account.get("api_key", "")),
        "has_key": bool(account.get("api_key")),
        "has_password": bool(account.get("password")),
    }


def _clean_fields(payload: dict, target: dict, *, creating: bool) -> list:
    """Copy known fields from the request into ``target``; return human-readable errors."""

    errors = []
    for field, limit in FIELD_LIMITS.items():
        if field not in payload:
            continue
        value = payload[field]
        if value is None:
            value = ""
        if not isinstance(value, str):
            errors.append(f"{field}: ожидалась строка")
            continue
        value = value.strip()
        if len(value) > limit:
            errors.append(f"{field}: длиннее {limit} символов")
            continue
        if field == "provider":
            value = value.lower()
            if not PROVIDER_RE.match(value):
                errors.append("provider: только латиница, цифры, точка, дефис или подчёркивание")
                continue
        # An empty secret in an edit form means "leave the stored one alone"; the page
        # never loads the real value, so a blank box cannot wipe a key by accident.
        if not creating and field in ("api_key", "password") and value == "":
            continue
        target[field] = value
    return errors


def new_account(payload: dict) -> tuple:
    provider = str(payload.get("provider") or "").strip().lower()
    if not PROVIDER_RE.match(provider):
        return None, ["provider: укажите провайдера (например deepseek)"]
    account = {
        "id": secrets.token_hex(8),
        "provider": provider,
        "label": "",
        "model": "",
        "email": "",
        "password": "",
        "api_key": "",
        "color": "",
        "emoji": "",
        "demo": False,
    }
    errors = _clean_fields(payload, account, creating=True)
    if errors:
        return None, errors
    account["label"] = account.get("label") or provider
    return account, []


# --------------------------------------------------------------------------- balances


def fetch_balances() -> dict:
    """Proxy the Forge Core balance report, reduced to what a tile shows."""

    checked = time.strftime("%Y-%m-%dT%H:%M:%S")
    try:
        with urllib.request.urlopen(FORGE_BALANCE, timeout=8) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as error:  # noqa: BLE001 - the point is to report the failure honestly
        return {
            "available": False,
            "reason": (
                "Forge API на 127.0.0.1:8011 не отвечает "
                f"({type(error).__name__}); баланс не получен"
            ),
            "checked_at": checked,
        }
    providers = payload.get("providers") if isinstance(payload, dict) else None
    if not isinstance(providers, dict):
        return {
            "available": False,
            "reason": "Forge API ответил без списка провайдеров; баланс не получен",
            "checked_at": checked,
        }
    slim = {}
    for name, row in providers.items():
        if not isinstance(row, dict):
            continue
        slim[str(name).lower()] = {
            "status": row.get("connection_status") or "",
            "available_display": row.get("available_display"),
            "currency": row.get("currency") or "",
            "source": row.get("source") or "",
            "error": row.get("error") or "",
            "last_updated": row.get("last_updated") or "",
        }
    return {
        "available": True,
        "providers": slim,
        "checked_at": payload.get("checked_at") or checked,
    }


# --------------------------------------------------------------------------- HTTP


def _host_ok(host_header: str) -> bool:
    if not host_header:
        return False
    host = host_header.strip()
    if host.startswith("["):
        host = host[1 : host.find("]")] if "]" in host else host
    elif ":" in host:
        host = host.rsplit(":", 1)[0]
    return host.strip().lower() in LOOPBACK_NAMES


def _origin_ok(origin: str) -> bool:
    if not origin:
        return True  # a local tool (curl) sends none; a web page always does
    parts = urllib.parse.urlsplit(origin)
    return parts.scheme == "http" and (parts.hostname or "").lower() in LOOPBACK_NAMES


class VaultHandler(BaseHTTPRequestHandler):
    server_version = "ForgeVault/1.0"
    protocol_version = "HTTP/1.1"

    # -- plumbing ---------------------------------------------------------

    def log_message(self, fmt: str, *args) -> None:
        # Paths only: ids are opaque, and no request body is ever logged.
        sys.stderr.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), fmt % args))

    def handle_one_request(self) -> None:
        # A browser that closes a keep-alive connection mid-read raises
        # ConnectionResetError inside the base class; it is a normal event, not a fault,
        # and it must not fill the server log with tracebacks.
        try:
            super().handle_one_request()
        except (ConnectionResetError, ConnectionAbortedError, TimeoutError):
            self.close_connection = True

    def _send(self, status: int, content_type: str, body: bytes, extra: dict = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for name, value in (extra or {}).items():
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(status, "application/json; charset=utf-8", body)

    def _fail(self, status: int, message: str) -> None:
        self._json(status, {"ok": False, "error": message})

    def _guard(self) -> bool:
        if not _host_ok(self.headers.get("Host", "")):
            self._fail(403, "запрос не с loopback-хоста")
            return False
        if not _origin_ok(self.headers.get("Origin", "")):
            self._fail(403, "чужой Origin: запрос отклонён")
            return False
        return True

    def _read_json(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise ValueError("некорректный Content-Length")
        if length < 0 or length > MAX_BODY:
            raise ValueError("слишком большое тело запроса")
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise ValueError("тело запроса не JSON")
        if not isinstance(payload, dict):
            raise ValueError("тело запроса должно быть JSON-объектом")
        return payload

    def _path(self) -> str:
        return urllib.parse.urlsplit(self.path).path

    # -- routes -----------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 - the base class names it this way
        if not self._guard():
            return
        path = self._path()
        if path in ("/", "/index.html"):
            self._page()
            return
        if path == "/api/accounts":
            self._list_accounts()
            return
        if path == "/api/balances":
            self._json(200, fetch_balances())
            return
        if path == "/api/ping":
            self._json(200, {"ok": True, "service": "forge-vault"})
            return
        self._fail(404, "не найдено")

    def do_HEAD(self) -> None:  # noqa: N802
        self.do_GET()

    def do_POST(self) -> None:  # noqa: N802
        if not self._guard():
            return
        path = self._path()
        if path == "/api/accounts":
            self._create_account()
            return
        match = re.fullmatch(r"/api/accounts/([a-z0-9]{8,32})/reveal", path)
        if match:
            self._reveal(match.group(1))
            return
        self._fail(404, "не найдено")

    def do_PUT(self) -> None:  # noqa: N802
        if not self._guard():
            return
        match = re.fullmatch(r"/api/accounts/([a-z0-9]{8,32})", self._path())
        if not match:
            self._fail(404, "не найдено")
            return
        self._update_account(match.group(1))

    def do_DELETE(self) -> None:  # noqa: N802
        if not self._guard():
            return
        match = re.fullmatch(r"/api/accounts/([a-z0-9]{8,32})", self._path())
        if not match:
            self._fail(404, "не найдено")
            return
        self._delete_account(match.group(1))

    # -- handlers ---------------------------------------------------------

    def _page(self) -> None:
        try:
            body = APP_FILE.read_bytes()
        except OSError:
            self._fail(500, "страница tools/vault_app/index.html не найдена")
            return
        csp = (
            "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
            "img-src data:; connect-src 'self'; base-uri 'none'; form-action 'none'"
        )
        self._send(
            200,
            "text/html; charset=utf-8",
            body,
            {"Content-Security-Policy": csp, "X-Frame-Options": "DENY"},
        )

    def _list_accounts(self) -> None:
        with _LOCK:
            data = _read_vault()
        accounts = [masked_account(row) for row in data["accounts"]]
        self._json(
            200,
            {
                "ok": True,
                "vault": ".forge/vault/vault.json",
                "accounts": accounts,
                "storage_note": (
                    "Локальный файл .forge/vault/vault.json, вне git. "
                    "Пароли и ключи лежат открытым текстом."
                ),
            },
        )

    def _create_account(self) -> None:
        try:
            payload = self._read_json()
        except ValueError as error:
            self.close_connection = True
            self._fail(400, str(error))
            return
        account, errors = new_account(payload)
        if account is None:
            self._fail(400, "; ".join(errors) or "некорректные данные")
            return
        with _LOCK:
            data = _read_vault()
            data["accounts"].append(account)
            _save_locked(data)
        self._json(201, {"ok": True, "account": masked_account(account)})

    def _update_account(self, account_id: str) -> None:
        if not ID_RE.match(account_id):
            self._fail(404, "не найдено")
            return
        try:
            payload = self._read_json()
        except ValueError as error:
            self.close_connection = True
            self._fail(400, str(error))
            return
        with _LOCK:
            data = _read_vault()
            account = next((row for row in data["accounts"] if row.get("id") == account_id), None)
            if account is None:
                self._fail(404, "аккаунт не найден")
                return
            errors = _clean_fields(payload, account, creating=False)
            if errors:
                self._fail(400, "; ".join(errors))
                return
            if not account.get("provider"):
                self._fail(400, "provider: не может быть пустым")
                return
            account["label"] = account.get("label") or account["provider"]
            # Once the owner edits an example it is their entry, not a demo.
            account["demo"] = False
            _save_locked(data)
        self._json(200, {"ok": True, "account": masked_account(account)})

    def _delete_account(self, account_id: str) -> None:
        with _LOCK:
            data = _read_vault()
            kept = [row for row in data["accounts"] if row.get("id") != account_id]
            if len(kept) == len(data["accounts"]):
                self._fail(404, "аккаунт не найден")
                return
            data["accounts"] = kept
            _save_locked(data)
        self._json(200, {"ok": True, "deleted": account_id})

    def _reveal(self, account_id: str) -> None:
        try:
            payload = self._read_json()
        except ValueError as error:
            self.close_connection = True
            self._fail(400, str(error))
            return
        field = str(payload.get("field") or "api_key")
        if field not in ("api_key", "password"):
            self._fail(400, "field: ожидается api_key или password")
            return
        with _LOCK:
            data = _read_vault()
            account = next((row for row in data["accounts"] if row.get("id") == account_id), None)
        if account is None:
            self._fail(404, "аккаунт не найден")
            return
        value = account.get(field, "")
        if not value:
            self._fail(404, "значение не задано")
            return
        # Only this explicit call returns a raw secret; it never enters a list response.
        self._json(200, {"ok": True, "id": account_id, "field": field, field: value})


class _Server(socketserver.ThreadingTCPServer):
    """One thread per connection; the page and the proxy must not block each other."""

    allow_reuse_address = True
    daemon_threads = True


def main(argv: list = None) -> int:
    parser = argparse.ArgumentParser(description="Локальное хранилище ключей и балансов")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args(argv)
    ensure_vault()
    try:
        with _Server(("127.0.0.1", args.port), VaultHandler) as httpd:
            print(f"AI vault on http://127.0.0.1:{args.port}/", flush=True)
            httpd.serve_forever()
    except OSError as error:
        print(f"cannot listen on 127.0.0.1:{args.port}: {error}", file=sys.stderr, flush=True)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
