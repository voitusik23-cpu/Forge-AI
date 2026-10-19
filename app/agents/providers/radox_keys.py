"""Key pool, rotation cursor and usage ledger for the Radox AI provider.

Security boundary
-----------------
A Radox key never leaves this module inside a value. Keys are read from the process
environment or the repository ``.env`` (through :class:`app.config.secrets.SecretStore`),
handed straight to one SDK client, and dropped. The durable ledger is addressed by
**position only**: it stores no key, no hash of a key, no prefix, no length and no
tail, so ``.forge/radox-keys.json`` can sit beside the run history without becoming a
second copy of a credential. Keys are re-read on every request, which is why adding a
key needs no ledger migration.

Why a position and not a name
-----------------------------
The owner's ordering is the contract: ``RADOX_API_KEY`` first, then ``RADOX_API_KEY_1``,
``RADOX_API_KEY_2`` and so on in ascending numeric order, with gaps allowed. Adding the
next numbered key appends a position at the end, so every existing position keeps its
meaning. Removing a key from the middle renumbers the accounts after it -- the ledger
still describes positions, and the report says so by returning ``index`` rather than a
key identity.

Rotation
--------
``next_index`` in the ledger is the position that will serve the next request. It is
advanced (modulo the current pool size) under one process-wide lock and written
atomically, so successive requests -- and successive runs -- advance instead of always
starting at the first account. A locked read-modify-write is what "concurrency-safe
enough for one process" means here: two threads cannot be handed the same position
because they cannot read the cursor at the same time.
"""

from __future__ import annotations

import datetime
import json
import os
import threading
import time
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.config.secrets import SecretStore

#: The variable the owner already used for a single account.
RADOX_KEY_ENV_VAR = "RADOX_API_KEY"

#: The numbered form of the same variable: ``<base>_<n>``, n >= 1.
NUMBERED_KEY_SEPARATOR = "_"

#: An explicit ledger location, for a test or a second composition. Absent means
#: ``<repository>/.forge/radox-keys.json``, the same durable area the runs use.
STATE_PATH_ENV_VAR = "FORGE_RADOX_KEYS_PATH"

#: Bumped only when the ledger's stored shape changes incompatibly.
STATE_VERSION = 1

#: How many accounts one request may try. The contract is ``min(len(keys), 3)``.
MAX_FAILOVER_ATTEMPTS = 3

#: How long a 429 disables an account when the provider sends no ``Retry-After``.
DEFAULT_RATE_LIMIT_BACKOFF_SECONDS = 30.0

#: Status codes that are a *hard* account failure, not a temporary rate limit: the
#: account itself cannot serve the request -- 402 means its balance/quota is spent,
#: 401/403 mean its credential is rejected. Rotating to the next account is right, but
#: the failed one must also leave the rotation for a while. Without this the ledger
#: recorded ``last_error_code: 402`` and no cooldown, so the same dead account was
#: chosen again on the very next reservation (measured: ``idx 0..4 err 402`` forever).
HARD_ACCOUNT_FAILURE_STATUS_CODES = frozenset({401, 402, 403})

#: How long a hard account failure keeps the account out of the rotation. One hour is
#: long enough that a run does not burn its attempts on dead accounts again, and short
#: enough that a topped-up balance or a fixed key returns without a manual reset.
HARD_ACCOUNT_FAILURE_BACKOFF_SECONDS = 3600.0

#: Upper bound on a provider-supplied ``Retry-After``: a malicious or broken header
#: must not remove an account from the pool for days.
MAX_RATE_LIMIT_BACKOFF_SECONDS = 3600.0

#: A store that cannot enumerate names is probed up to this many numbered positions.
MAX_NUMBERED_KEY_PROBE = 100

_REPOSITORY_ROOT = Path(__file__).resolve().parents[3]

#: One lock for the whole process, held across every read-modify-write of the ledger.
_STATE_LOCK = threading.RLock()


def default_state_path() -> Path:
    """The ledger path: the environment override, else ``<repo>/.forge/radox-keys.json``."""

    override = os.environ.get(STATE_PATH_ENV_VAR)
    if isinstance(override, str) and override.strip():
        return Path(override.strip())
    return _REPOSITORY_ROOT / ".forge" / "radox-keys.json"


def _utc_now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _iso(moment: datetime.datetime) -> str:
    return moment.isoformat()


def _timestamp(value: Any) -> Optional[float]:
    """An ISO timestamp as epoch seconds, or ``None`` when it is absent/unreadable."""

    if not isinstance(value, str) or not value.strip():
        return None
    try:
        moment = datetime.datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=datetime.timezone.utc)
    try:
        return moment.timestamp()
    except (OverflowError, OSError, ValueError):
        return None


def _non_negative_int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        try:
            value = int(value)
        except (TypeError, ValueError):
            return 0
    return value if value > 0 else 0


def _optional_int(value: Any) -> Optional[int]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_text(value: Any) -> Optional[str]:
    return value if isinstance(value, str) and value.strip() else None


def _normalized_entry(entry: Any) -> Dict[str, Any]:
    """The six ledger fields, coerced, with no seventh field ever carried through."""

    source = entry if isinstance(entry, dict) else {}
    return {
        "requests": _non_negative_int(source.get("requests")),
        "tokens_in": _non_negative_int(source.get("tokens_in")),
        "tokens_out": _non_negative_int(source.get("tokens_out")),
        "last_error_code": _optional_int(source.get("last_error_code")),
        "last_used_at": _optional_text(source.get("last_used_at")),
        "disabled_until": _optional_text(source.get("disabled_until")),
    }


def _entry_disabled(entry: Dict[str, Any], now: Optional[float] = None) -> bool:
    until = _timestamp(entry.get("disabled_until"))
    if until is None:
        return False
    return until > (time.time() if now is None else now)


def numbered_key_indices(
    secret_store: SecretStore, base_var: str = RADOX_KEY_ENV_VAR
) -> List[int]:
    """The present numbered positions of ``base_var``, ascending, gaps included.

    A gap is not an error: ``_1`` and ``_3`` without ``_2`` is a valid pool. The
    expensive, exact answer comes from :meth:`SecretStore.secret_names`; a store
    that cannot enumerate its names is probed over a bounded range instead, so an
    alternative secret backend still works.
    """

    prefix = f"{base_var}{NUMBERED_KEY_SEPARATOR}"
    lister = getattr(secret_store, "secret_names", None)
    names = None
    if callable(lister):
        try:
            names = list(lister())
        except Exception:  # noqa: BLE001 - an unenumerable store is probed instead
            names = None

    if names is not None:
        indices = set()
        for name in names:
            if not isinstance(name, str) or not name.startswith(prefix):
                continue
            suffix = name[len(prefix):]
            if suffix.isdigit():
                indices.add(int(suffix))
        return sorted(index for index in indices if index >= 0)

    probed = set()
    for index in range(1, MAX_NUMBERED_KEY_PROBE + 1):
        if secret_store.get_secret(f"{prefix}{index}"):
            probed.add(index)
    return sorted(probed)


def collect_keys(
    secret_store: SecretStore, base_var: str = RADOX_KEY_ENV_VAR
) -> List[str]:
    """All present key values in the owner's stable order, deduplicated.

    Order: ``RADOX_API_KEY``, then ``RADOX_API_KEY_1`` .. ``RADOX_API_KEY_N`` ascending.
    Identical values appear once, at their first position, because a duplicated key
    is one account and must not consume two rotation slots. Blank values are not keys.
    """

    if not isinstance(base_var, str) or not base_var.strip():
        base_var = RADOX_KEY_ENV_VAR

    keys: List[str] = []
    seen = set()

    def add(value: Optional[str]) -> None:
        if isinstance(value, str) and value.strip() and value not in seen:
            seen.add(value)
            keys.append(value)

    add(secret_store.get_secret(base_var))
    for index in numbered_key_indices(secret_store, base_var):
        add(secret_store.get_secret(f"{base_var}{NUMBERED_KEY_SEPARATOR}{index}"))
    return keys


def retry_after_seconds(value: Any) -> Optional[float]:
    """``Retry-After`` as seconds: a delta, an HTTP date, or ``None`` when unreadable."""

    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        seconds = float(text)
    except (TypeError, ValueError):
        try:
            moment = parsedate_to_datetime(text)
        except (TypeError, ValueError):
            return None
        if moment is None:
            return None
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=datetime.timezone.utc)
        seconds = (moment - _utc_now()).total_seconds()
    if seconds != seconds:  # NaN
        return None
    return max(0.0, min(float(seconds), MAX_RATE_LIMIT_BACKOFF_SECONDS))


class RadoxKeyLedger:
    """The durable per-position counters, written atomically beside the run store.

    Every read-modify-write runs under one lock and replaces the file through a
    temporary sibling, so an interrupted write cannot leave a half document behind
    and two threads cannot award the same rotation slot. An unreadable or corrupt
    file is treated as an empty ledger: rotation is a convenience, and a broken
    counter must never keep the provider from answering.
    """

    def __init__(self, state_path: Optional[os.PathLike[str] | str] = None) -> None:
        self._state_path = (
            Path(state_path) if state_path is not None else default_state_path()
        )

    @property
    def path(self) -> Path:
        return self._state_path

    # ------------------------------------------------------------------ storage
    @staticmethod
    def _empty_state() -> Dict[str, Any]:
        return {"version": STATE_VERSION, "next_index": 0, "keys": {}}

    def _read(self) -> Dict[str, Any]:
        try:
            raw = self._state_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return self._empty_state()
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            return self._empty_state()
        if not isinstance(data, dict):
            return self._empty_state()

        state = self._empty_state()
        stored_keys = data.get("keys")
        if isinstance(stored_keys, dict):
            for index, entry in stored_keys.items():
                text = str(index)
                if text.isdigit():
                    state["keys"][text] = _normalized_entry(entry)
        state["next_index"] = _non_negative_int(data.get("next_index", 0))
        return state

    def _write(self, state: Dict[str, Any]) -> bool:
        payload = {
            "version": STATE_VERSION,
            "updated_at": _iso(_utc_now()),
            "next_index": _non_negative_int(state.get("next_index", 0)),
            "keys": {
                text: _normalized_entry(entry)
                for text, entry in (state.get("keys") or {}).items()
                if str(text).isdigit()
            },
        }
        temporary = self._state_path.with_name(
            f"{self._state_path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
        )
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            with temporary.open("w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.flush()
                try:
                    os.fsync(handle.fileno())
                except OSError:
                    pass
            os.replace(temporary, self._state_path)
            return True
        except OSError:
            try:
                temporary.unlink()
            except OSError:
                pass
            return False

    @staticmethod
    def _entry(state: Dict[str, Any], index: int) -> Dict[str, Any]:
        entry = _normalized_entry(state["keys"].get(str(int(index))))
        state["keys"][str(int(index))] = entry
        return entry

    # ----------------------------------------------------------------- rotation
    def reserve(self, key_count: int) -> int:
        """Claim the next rotation position and persist the advance.

        The returned index is the position that should serve this request. The
        cursor moves by exactly one, so the next caller gets the next position even
        when this request ends in failover and is ultimately served by another one.
        """

        count = _non_negative_int(key_count)
        if count <= 0:
            return 0
        with _STATE_LOCK:
            state = self._read()
            start = int(state["next_index"]) % count
            state["next_index"] = (start + 1) % count
            self._write(state)
            return start

    def candidates(self, start: int, key_count: int) -> List[int]:
        """Every position in failover order from ``start``: usable accounts first.

        A position whose ``disabled_until`` is still in the future goes to the back
        of the queue rather than being dropped. It is therefore skipped while any
        live account can serve the request, yet it remains a last resort instead of
        the provider refusing to work because every account is in a short cooldown.
        """

        count = _non_negative_int(key_count)
        if count <= 0:
            return []
        order = [(_non_negative_int(start) + step) % count for step in range(count)]
        with _STATE_LOCK:
            state = self._read()
            now = time.time()
            live = [
                index
                for index in order
                if not _entry_disabled(_normalized_entry(state["keys"].get(str(index))), now)
            ]
        cooling = [index for index in order if index not in live]
        return live + cooling

    # -------------------------------------------------------------- accounting
    def record_success(
        self, index: int, tokens_in: Any = 0, tokens_out: Any = 0
    ) -> None:
        """Count one served request and the tokens it reported."""

        with _STATE_LOCK:
            state = self._read()
            entry = self._entry(state, index)
            entry["requests"] += 1
            entry["tokens_in"] += _non_negative_int(tokens_in)
            entry["tokens_out"] += _non_negative_int(tokens_out)
            entry["last_error_code"] = None
            entry["last_used_at"] = _iso(_utc_now())
            self._write(state)

    def record_failure(
        self,
        index: int,
        error_code: Optional[int] = None,
        retry_after: Optional[float] = None,
    ) -> None:
        """Count one failed attempt; a 429 or a hard account failure starts a cooldown.

        A 429 keeps the provider's own ``Retry-After`` semantics. A 402/401/403 is a
        hard *account* failure -- insufficient funds/quota, or a rejected credential --
        so it disables the position for :data:`HARD_ACCOUNT_FAILURE_BACKOFF_SECONDS`.
        The position is not removed from the pool: :meth:`candidates` keeps a disabled
        position as a last resort, so a pool where every account is cooling down still
        attempts them rather than refusing to work.
        """

        with _STATE_LOCK:
            state = self._read()
            entry = self._entry(state, index)
            entry["requests"] += 1
            entry["last_error_code"] = _optional_int(error_code)
            entry["last_used_at"] = _iso(_utc_now())
            if entry["last_error_code"] == 429:
                seconds = (
                    float(retry_after)
                    if isinstance(retry_after, (int, float))
                    and not isinstance(retry_after, bool)
                    and retry_after == retry_after
                    and retry_after >= 0
                    else DEFAULT_RATE_LIMIT_BACKOFF_SECONDS
                )
                seconds = min(seconds, MAX_RATE_LIMIT_BACKOFF_SECONDS)
                entry["disabled_until"] = _iso(
                    _utc_now() + datetime.timedelta(seconds=seconds)
                )
            elif entry["last_error_code"] in HARD_ACCOUNT_FAILURE_STATUS_CODES:
                entry["disabled_until"] = _iso(
                    _utc_now()
                    + datetime.timedelta(seconds=HARD_ACCOUNT_FAILURE_BACKOFF_SECONDS)
                )
            self._write(state)

    def every_key_cooling_down(self, key_count: int) -> bool:
        """Whether *every* position is currently disabled, i.e. this was a last resort.

        The rotation still uses a disabled account when nothing else is left (see
        :meth:`candidates`); this answers the question the failure message has to state
        honestly: was the whole pool cooling down, or just the account that failed?
        """

        count = _non_negative_int(key_count)
        if count <= 0:
            return False
        keys = self.report(count)["keys"]
        return bool(keys) and all(entry["disabled"] for entry in keys)

    # ------------------------------------------------------------------- report
    def report(self, key_count: int) -> Dict[str, Any]:
        """The read-only view of the pool: counts and per-position counters only.

        No key, no hash, no prefix, no length: the caller learns how many accounts
        exist, which position is next, and how each position has behaved.
        """

        count = _non_negative_int(key_count)
        with _STATE_LOCK:
            state = self._read()
        now = time.time()
        keys: List[Dict[str, Any]] = []
        for index in range(count):
            entry = _normalized_entry(state["keys"].get(str(index)))
            keys.append(
                {
                    "index": index,
                    "requests": entry["requests"],
                    "tokens_in": entry["tokens_in"],
                    "tokens_out": entry["tokens_out"],
                    "last_error_code": entry["last_error_code"],
                    "disabled": _entry_disabled(entry, now),
                }
            )
        return {
            "count": count,
            "active_index": (int(state["next_index"]) % count) if count else 0,
            "keys": keys,
        }


def key_pool_payload(
    secret_store: SecretStore,
    *,
    base_var: str = RADOX_KEY_ENV_VAR,
    state_path: Optional[os.PathLike[str] | str] = None,
) -> Dict[str, Any]:
    """The ``GET /api/providers/radox/keys`` payload for one secret store."""

    keys = collect_keys(secret_store, base_var)
    return RadoxKeyLedger(state_path=state_path).report(len(keys))
