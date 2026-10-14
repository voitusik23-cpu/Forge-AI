"""Accounts for the Forge desktop program: email confirmation, and developer access.

Deliberate choices, because this is an access gate and not a form:

* **The code is never stored in clear.** Only a SHA-256 hash reaches the file, so a copy
  of the account file does not hand somebody a working confirmation code.
* **A code expires** (ten minutes) and **stops working after five wrong attempts**, so a
  six-digit code cannot be brute-forced by a script at human speed.
* **Mail delivery is pluggable and honest.** No SMTP server is configured by default, so
  the default delivery is ``local``: the code is shown in the window and goes nowhere.
  That is labelled as such in the interface, and ``SmtpCodeDelivery`` is what makes it a
  real email once the operator sets the SMTP environment variables. Nothing pretends a
  message was sent when it was not.
* **Developer access is an explicit bypass**, created by pressing a labelled button, and
  the reason is stated in the interface rather than hidden.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

CODE_DIGITS = 6
CODE_TTL_SECONDS = 600
MAX_CODE_ATTEMPTS = 5
DEVELOPER_EMAIL = "developer@forge.local"

_EMAIL_MIN = 6
_EMAIL_MAX = 254


def normalise_email(value: object) -> str:
    return str(value or "").strip().lower()


def is_valid_email(value: object) -> bool:
    """A conservative check: one ``@``, a dot in the domain, no spaces.

    Deliberately not a full RFC 5322 parser -- the point is to catch typos, not to be the
    authority on what an address may look like. Anything accepted here still has to be
    confirmed by possession of the code.
    """

    text = normalise_email(value)
    if not (_EMAIL_MIN <= len(text) <= _EMAIL_MAX):
        return False
    if any(character.isspace() for character in text):
        return False
    if text.count("@") != 1:
        return False
    local, _, domain = text.partition("@")
    if not local or not domain or "." not in domain:
        return False
    if domain.startswith(".") or domain.endswith(".") or ".." in domain:
        return False
    return True


def _hash_code(email: str, code: str) -> str:
    """Hash the code together with the address, so one code cannot confirm another."""

    payload = f"{email}:{code}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _default_store_path() -> Path:
    """A per-user path when the platform allows one, otherwise the repository.

    The desktop program must start even where the platform refuses to create a directory
    outside the workspace -- measured on this host, ``%LOCALAPPDATA%`` and ``%TEMP%`` are
    both refused with ``WinError 5`` inside the sandbox -- so the fallback is a real
    fallback rather than an error path.
    """

    candidates = []
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        candidates.append(Path(local_app_data) / "ForgeAI" / "accounts.json")
    candidates.append(Path.home() / ".forge" / "accounts.json")
    candidates.append(Path(__file__).resolve().parents[2] / ".forge" / "accounts.json")
    for candidate in candidates:
        try:
            candidate.parent.mkdir(parents=True, exist_ok=True)
            probe = candidate.parent / ".write-probe"
            probe.write_text("ok", encoding="ascii")
            probe.unlink()
            return candidate
        except OSError:
            continue
    return candidates[-1]


class CodeDelivery:
    """How a confirmation code reaches the person who asked for it."""

    name = "unspecified"

    def deliver(self, email: str, code: str) -> Tuple[str, str]:
        """Return ``(description, code_to_show_or_empty)``."""

        raise NotImplementedError


class LocalCodeDelivery(CodeDelivery):
    """No mail server: the code is shown in the window and goes nowhere else."""

    name = "local (no mail server configured)"

    def deliver(self, email: str, code: str) -> Tuple[str, str]:
        return self.name, code


class SmtpCodeDelivery(CodeDelivery):
    """Real email through SMTP, configured entirely by environment variables."""

    name = "smtp"

    def __init__(self, host: str, port: int, user: str, password: str, sender: str) -> None:
        self._host = host
        self._port = port
        self._user = user
        self._password = password
        self._sender = sender

    @classmethod
    def from_environment(cls, environ: Optional[Dict[str, str]] = None) -> "SmtpCodeDelivery":
        source = os.environ if environ is None else environ
        host = (source.get("FORGE_SMTP_HOST") or "").strip()
        if not host:
            raise ValueError("FORGE_SMTP_HOST is not set")
        return cls(
            host=host,
            port=int(source.get("FORGE_SMTP_PORT") or 587),
            user=(source.get("FORGE_SMTP_USER") or "").strip(),
            password=source.get("FORGE_SMTP_PASSWORD") or "",
            sender=(source.get("FORGE_SMTP_FROM") or source.get("FORGE_SMTP_USER") or "").strip(),
        )

    def deliver(self, email: str, code: str) -> Tuple[str, str]:
        import smtplib
        from email.message import EmailMessage

        message = EmailMessage()
        message["From"] = self._sender
        message["To"] = email
        message["Subject"] = "Forge confirmation code"
        message.set_content(
            f"Your Forge confirmation code is {code}.\n"
            f"It expires in {CODE_TTL_SECONDS // 60} minutes.\n"
        )
        with smtplib.SMTP(self._host, self._port, timeout=20) as server:
            server.starttls()
            if self._user:
                server.login(self._user, self._password)
            server.send_message(message)
        return f"smtp ({self._host})", ""


@dataclass
class Account:
    email: str
    confirmed: bool = False
    is_developer: bool = False
    created_at: float = 0.0
    confirmed_at: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "email": self.email,
            "confirmed": bool(self.confirmed),
            "is_developer": bool(self.is_developer),
            "created_at": float(self.created_at),
            "confirmed_at": float(self.confirmed_at),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Account":
        return cls(
            email=normalise_email(data.get("email")),
            confirmed=bool(data.get("confirmed")),
            is_developer=bool(data.get("is_developer")),
            created_at=float(data.get("created_at") or 0.0),
            confirmed_at=float(data.get("confirmed_at") or 0.0),
        )


@dataclass
class PendingCode:
    email: str
    code_hash: str
    created_at: float
    attempts: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "email": self.email,
            "code_hash": self.code_hash,
            "created_at": float(self.created_at),
            "attempts": int(self.attempts),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PendingCode":
        return cls(
            email=normalise_email(data.get("email")),
            code_hash=str(data.get("code_hash") or ""),
            created_at=float(data.get("created_at") or 0.0),
            attempts=int(data.get("attempts") or 0),
        )


@dataclass
class AccountStore:
    """Accounts and pending confirmations, persisted as JSON."""

    path: Optional[Path] = None
    delivery: CodeDelivery = field(default_factory=LocalCodeDelivery)
    clock: Callable[[], float] = time.time
    code_ttl_seconds: int = CODE_TTL_SECONDS
    max_attempts: int = MAX_CODE_ATTEMPTS
    accounts: Dict[str, Account] = field(default_factory=dict)
    pending: Dict[str, PendingCode] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.path is None:
            self.path = _default_store_path()
        self.path = Path(self.path)
        self.load()

    # ------------------------------------------------------------------ persistence
    def load(self) -> None:
        try:
            raw = self.path.read_text(encoding="utf-8")  # type: ignore[union-attr]
        except (OSError, AttributeError):
            return
        try:
            data = json.loads(raw)
        except ValueError:
            # A corrupt account file must not stop the program from starting; it is
            # left on disk untouched so it can be inspected.
            return
        if not isinstance(data, dict):
            return
        for entry in data.get("accounts") or ():
            if isinstance(entry, dict):
                account = Account.from_dict(entry)
                if account.email:
                    self.accounts[account.email] = account
        for entry in data.get("pending") or ():
            if isinstance(entry, dict):
                pending = PendingCode.from_dict(entry)
                if pending.email:
                    self.pending[pending.email] = pending

    def save(self) -> bool:
        payload = {
            "version": 1,
            "accounts": [account.to_dict() for account in self.accounts.values()],
            "pending": [pending.to_dict() for pending in self.pending.values()],
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)  # type: ignore[union-attr]
            temporary = str(self.path) + ".tmp"  # type: ignore[union-attr]
            Path(temporary).write_text(
                json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            os.replace(temporary, str(self.path))
            return True
        except OSError:
            return False

    # --------------------------------------------------------------------- accounts
    def account_for(self, email: object) -> Optional[Account]:
        return self.accounts.get(normalise_email(email))

    def is_confirmed(self, email: object) -> bool:
        account = self.account_for(email)
        return bool(account and (account.confirmed or account.is_developer))

    def register(self, email: object) -> Tuple[bool, str, str]:
        """Create a pending confirmation.

        Returns ``(ok, message_key, code_to_show)``: the message is a translation key so
        the window decides the wording, and the code is non-empty only for a delivery
        that shows it on screen.
        """

        address = normalise_email(email)
        if not address:
            return False, "register.error.email", ""
        if not is_valid_email(address):
            return False, "register.error.email_invalid", ""

        account = self.accounts.get(address)
        if account is not None and (account.confirmed or account.is_developer):
            # Already able to enter: no new code is generated, so pressing the button
            # twice cannot produce two live codes.
            return True, "register.welcome", ""

        code = "".join(secrets.choice("0123456789") for _ in range(CODE_DIGITS))
        self.pending[address] = PendingCode(
            email=address, code_hash=_hash_code(address, code), created_at=self.clock()
        )
        self.accounts.setdefault(address, Account(email=address, created_at=self.clock()))
        self.save()

        try:
            description, shown = self.delivery.deliver(address, code)
        except Exception as exc:  # noqa: BLE001 - report, never claim delivery
            self.pending.pop(address, None)
            self.save()
            return False, f"delivery_failed:{type(exc).__name__}", ""
        self.save()
        if shown:
            return True, f"register.code.sent|{description}", shown
        return True, f"register.code.emailed|{description}", ""

    def confirm(self, email: object, code: object) -> Tuple[bool, str]:
        address = normalise_email(email)
        typed = str(code or "").strip()
        if not typed:
            return False, "register.error.code"

        pending = self.pending.get(address)
        if pending is None:
            return False, "register.error.code_wrong"

        if self.clock() - pending.created_at > self.code_ttl_seconds:
            self.pending.pop(address, None)
            self.save()
            return False, "register.error.code_expired"

        if pending.attempts >= self.max_attempts:
            self.pending.pop(address, None)
            self.save()
            return False, "register.error.code_expired"

        if not secrets.compare_digest(pending.code_hash, _hash_code(address, typed)):
            pending.attempts += 1
            self.save()
            return False, "register.error.code_wrong"

        account = self.accounts.get(address) or Account(email=address, created_at=self.clock())
        account.confirmed = True
        account.confirmed_at = self.clock()
        self.accounts[address] = account
        self.pending.pop(address, None)
        self.save()
        return True, "register.welcome"

    def developer_access(self, email: str = DEVELOPER_EMAIL) -> Account:
        """Enter without registration. Explicit, labelled, and recorded as such."""

        address = normalise_email(email) or DEVELOPER_EMAIL
        account = self.accounts.get(address) or Account(email=address, created_at=self.clock())
        account.is_developer = True
        account.confirmed = True
        account.confirmed_at = self.clock()
        self.accounts[address] = account
        self.pending.pop(address, None)
        self.save()
        return account

    def forget(self, email: object) -> bool:
        address = normalise_email(email)
        removed = self.accounts.pop(address, None) is not None
        self.pending.pop(address, None)
        if removed:
            self.save()
        return removed
