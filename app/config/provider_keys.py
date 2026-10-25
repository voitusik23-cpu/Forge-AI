"""Provider entries the owner adds in «Настройки → Провайдеры и ключи».

The complaint this module answers
---------------------------------
*«Не могу найти, где добавить провайдера с ключами»*. Providers came from ``.env``,
a file the owner is not supposed to edit, so the product offered no place to put a
key. This module is that place, and it splits one provider entry into exactly the
two halves the two storage layers are for:

======================  ============================================  =============
part                    where it lives                               why there
======================  ============================================  =============
provider, base URL,     ``.forge/settings.json`` -> ``providers``      not a secret:
default model                                                         a JSON file may
                                                                       hold it
API key                 the OS credential vault                       a JSON file must
                        (:mod:`app.config.windows_creds`)             never hold it
======================  ============================================  =============

No key value ever passes through this module's return values, its exceptions or its
``repr``: :meth:`ProviderKeyService.describe` reports ``has_key`` (one boolean) and
``key_source`` (``environment`` / ``vault`` / empty), which is all the interface
needs to say «готов».

Runtime visibility, without a restart
-------------------------------------
Nothing here registers a provider. The entry's key is written to the vault under the
*existing* secret variable of that provider (``DEEPSEEK_API_KEY`` and so on), and the
existing provider layer resolves secrets through
:class:`app.config.secrets.SecretStore`, which now reads the vault on every call. So a
provider added here is visible to the model catalogue and to a dispatch on the very
next request -- there is one registry, and this module is not a second one.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence

from app.config.app_settings import (
    PROVIDERS_KEY,
    SETTINGS_SCHEMA_VERSION,
    AppSettingsStore,
    read_settings_document,
    write_settings_document,
)
from app.config.secrets import SecretStore
from app.config.windows_creds import CredentialBackend, default_credential_backend

__all__ = [
    "PRESETS",
    "PROVIDER_ID_PATTERN",
    "ProviderKeyError",
    "ProviderKeyService",
    "ProviderKeyStore",
    "custom_secret_name",
    "preset_by_id",
]

#: A provider id is a short, lower-case identifier: it names a registry entry, a record
#: in the settings file and (indirectly) a vault entry, so it must not be able to look
#: like a path, a variable assignment or a different provider's id.
PROVIDER_ID_PATTERN = re.compile(r"[a-z][a-z0-9_-]{1,39}\Z")

#: The key-length bounds. A key shorter than this is not a key anyone issued -- refusing
#: it is the plain "invalid key" message instead of a silent refusal at the first run.
MIN_KEY_LENGTH = 8
MAX_KEY_LENGTH = 512
MAX_BASE_URL_LENGTH = 300
MAX_MODEL_LENGTH = 120

#: The preset catalogue: the one place where a provider's UI label, its registry id, its
#: secret variable and its documented base URL are stated together. ``provider_id`` is the
#: canonical name the *existing* provider layer knows; a preset may not invent one.
PRESETS: tuple[Dict[str, str], ...] = (
    {
        "preset": "openai_compatible",
        "label": "OpenAI-compatible",
        "provider_id": "openai",
        "secret_name": "OPENAI_API_KEY",
        "base_url": "https://api.openai.com/v1",
        "model": "",
    },
    {
        "preset": "deepseek",
        "label": "DeepSeek",
        "provider_id": "deepseek",
        "secret_name": "DEEPSEEK_API_KEY",
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-chat",
    },
    {
        "preset": "openrouter",
        "label": "OpenRouter",
        "provider_id": "openrouter",
        "secret_name": "OPENROUTER_API_KEY",
        "base_url": "https://openrouter.ai/api/v1",
        "model": "openrouter/auto",
    },
    {
        "preset": "gemini",
        "label": "Gemini",
        "provider_id": "google",
        "secret_name": "GEMINI_API_KEY",
        "base_url": "https://generativelanguage.googleapis.com",
        "model": "",
    },
    {
        "preset": "anthropic",
        "label": "Anthropic",
        "provider_id": "anthropic",
        "secret_name": "ANTHROPIC_API_KEY",
        "base_url": "https://api.anthropic.com",
        "model": "",
    },
    {
        "preset": "groq",
        "label": "Groq",
        "provider_id": "groq",
        "secret_name": "GROQ_API_KEY",
        "base_url": "https://api.groq.com/openai/v1",
        "model": "",
    },
    {
        "preset": "xai",
        "label": "xAI",
        "provider_id": "xai",
        "secret_name": "XAI_API_KEY",
        "base_url": "https://api.x.ai/v1",
        "model": "",
    },
    {
        "preset": "together",
        "label": "Together",
        "provider_id": "together",
        "secret_name": "TOGETHER_API_KEY",
        "base_url": "https://api.together.ai/v1",
        "model": "",
    },
    {
        "preset": "radox",
        "label": "Radox",
        "provider_id": "radox",
        "secret_name": "RADOX_API_KEY",
        "base_url": "https://radoxai.com/v1",
        "model": "",
    },
    {
        # «Свой»: the owner names the provider himself. Its secret variable is derived from
        # the id (:func:`custom_secret_name`), and the entry is reported as unsupported
        # until a build registers that provider -- the key is still stored, honestly.
        "preset": "custom",
        "label": "Свой",
        "provider_id": "",
        "secret_name": "",
        "base_url": "",
        "model": "",
    },
)

#: The preset an entry falls back to when its stored ``preset`` is unknown.
DEFAULT_PRESET = "custom"


class ProviderKeyError(ValueError):
    """A provider entry is not usable. A refusal with a plain sentence, never a crash."""

    code = "invalid_provider_entry"

    def __init__(self, message: str, *, field: str = "", code: str = "") -> None:
        super().__init__(message)
        self.field = str(field)
        if code:
            self.code = str(code)


def preset_by_id(preset: object) -> Dict[str, str]:
    """The preset document for ``preset``, or the «Свой» entry when it is unknown."""

    wanted = str(preset or "").strip().lower()
    for item in PRESETS:
        if item["preset"] == wanted:
            return dict(item)
    for item in PRESETS:
        if item["preset"] == DEFAULT_PRESET:
            return dict(item)
    return {}


def custom_secret_name(provider_id: str) -> str:
    """The secret variable a self-named provider's key is stored under.

    ``my-gateway`` -> ``MY_GATEWAY_API_KEY``. Deterministic, so the provider layer will
    find the key under the same name if a build ever registers that provider.
    """

    cleaned = re.sub(r"[^a-z0-9]+", "_", str(provider_id or "").strip().lower())
    cleaned = cleaned.strip("_").upper()
    if not cleaned:
        raise ProviderKeyError("Укажите идентификатор провайдера.", field="provider_id")
    return f"{cleaned}_API_KEY"


# --------------------------------------------------------------------------- #
# the non-secret half: the application's own settings file
# --------------------------------------------------------------------------- #


class ProviderKeyStore:
    """Read and write the provider entries of ``.forge/settings.json`` (no keys inside).

    The file is shared with the projects-folder setting, so this store never writes the
    document from scratch: it reads it, replaces only ``providers``, and writes it back
    through the same atomic helper the settings store uses. Two writers, one file, no
    lost values.
    """

    def __init__(self, settings: Optional[AppSettingsStore] = None) -> None:
        self._settings = settings if settings is not None else AppSettingsStore()

    @property
    def path(self):
        """The settings file this store reads and writes."""

        return self._settings.path

    def document(self) -> Dict[str, Any]:
        """The whole settings document, unnormalised."""

        return read_settings_document(self.path)

    def list(self) -> List[Dict[str, Any]]:
        """Every stored provider entry, in the order the owner added them.

        Only the known fields are returned: an unknown key in the file (an older or a
        newer build's field) cannot leak into a payload through this read.
        """

        raw = self.document().get(PROVIDERS_KEY)
        if not isinstance(raw, list):
            return []
        entries: List[Dict[str, Any]] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            provider_id = str(item.get("provider_id", "") or "").strip()
            if not provider_id:
                continue
            entries.append(
                {
                    "provider_id": provider_id,
                    "label": str(item.get("label", "") or ""),
                    "preset": str(item.get("preset", "") or ""),
                    "base_url": str(item.get("base_url", "") or ""),
                    "model": str(item.get("model", "") or ""),
                    "secret_name": str(item.get("secret_name", "") or ""),
                    "added_at": str(item.get("added_at", "") or ""),
                }
            )
        return entries

    def upsert(self, entry: Dict[str, Any]) -> Dict[str, Any]:
        """Store one entry, replacing the record of the same provider id.

        Returns the stored record. Nothing secret is accepted here: a caller that passes
        a ``key``/``api_key`` field is refused, because a silent drop would look like a
        successful save to whoever wrote the caller.
        """

        for forbidden in ("key", "api_key", "secret", "token", "value"):
            if forbidden in entry:
                raise ProviderKeyError(
                    "Запись провайдера не может содержать ключ: он хранится только в "
                    "хранилище учётных данных Windows.",
                    field=forbidden,
                    code="key_in_settings",
                )
        provider_id = str(entry.get("provider_id", "") or "").strip()
        if not PROVIDER_ID_PATTERN.fullmatch(provider_id):
            raise ProviderKeyError(
                "Идентификатор провайдера: строчные латинские буквы, цифры, «-» и «_» "
                "(2–40 символов).",
                field="provider_id",
            )
        record = {
            "provider_id": provider_id,
            "label": str(entry.get("label", "") or ""),
            "preset": str(entry.get("preset", "") or ""),
            "base_url": str(entry.get("base_url", "") or ""),
            "model": str(entry.get("model", "") or ""),
            "secret_name": str(entry.get("secret_name", "") or ""),
            "added_at": str(entry.get("added_at", "") or _now_iso()),
        }
        document = self.document()
        existing = [item for item in (document.get(PROVIDERS_KEY) or []) if isinstance(item, dict)]
        kept = [
            item
            for item in existing
            if str(item.get("provider_id", "") or "").strip() != provider_id
        ]
        kept.append(record)
        document[PROVIDERS_KEY] = kept
        document.setdefault("schema", SETTINGS_SCHEMA_VERSION)
        write_settings_document(self.path, document)
        return dict(record)

    def remove(self, provider_id: str) -> bool:
        """Drop one provider record. ``True`` when a record was there."""

        wanted = str(provider_id or "").strip()
        document = self.document()
        raw = document.get(PROVIDERS_KEY)
        if not isinstance(raw, list):
            return False
        kept = [
            item
            for item in raw
            if not (isinstance(item, dict) and str(item.get("provider_id", "") or "").strip() == wanted)
        ]
        if len(kept) == len([item for item in raw if isinstance(item, dict)]):
            return False
        document[PROVIDERS_KEY] = kept
        write_settings_document(self.path, document)
        return True

    def find(self, provider_id: str) -> Optional[Dict[str, Any]]:
        """One stored entry, or ``None``."""

        wanted = str(provider_id or "").strip()
        for item in self.list():
            if item["provider_id"] == wanted:
                return item
        return None


# --------------------------------------------------------------------------- #
# the key half and the derived state
# --------------------------------------------------------------------------- #


class ProviderKeyService:
    """The whole «Провайдеры и ключи» behaviour: list, add, delete.

    Dependencies are injected so the same object serves the HTTP surface and an offline
    test: the settings file, the secret store (which reads env, ``.env`` then the vault),
    the vault itself (for writes and deletes) and two read-only callables that report the
    composition's facts -- the registry's providers and its enabled set.
    """

    def __init__(
        self,
        *,
        store: Optional[ProviderKeyStore] = None,
        secret_store: Optional[SecretStore] = None,
        credential_backend: Optional[CredentialBackend] = None,
        registry_providers: Optional[Callable[[], Sequence[Any]]] = None,
        enabled_providers: Optional[Callable[[], Optional[Iterable[str]]]] = None,
    ) -> None:
        self._store = store if store is not None else ProviderKeyStore()
        self._credential_backend = credential_backend
        self._secret_store = secret_store
        self._registry_providers = registry_providers
        self._enabled_providers = enabled_providers

    # ------------------------------------------------------------------ vault
    def _backend(self) -> CredentialBackend:
        """The vault writes go to. Resolved per call, like every other read."""

        if self._credential_backend is not None:
            return self._credential_backend
        try:
            return default_credential_backend()
        except Exception:  # noqa: BLE001 - an unusable vault is reported, never a crash
            from app.config.windows_creds import UnavailableCredentialBackend

            return UnavailableCredentialBackend()

    def _secret_store_for_reads(self) -> SecretStore:
        """The store used to answer ``has_key`` -- env, ``.env`` and the vault, in order.

        Built per call when this service owns its vault: a cached store would answer from
        a stale backend the moment a test (or the owner) changes a key.
        """

        if self._secret_store is not None:
            return self._secret_store
        return SecretStore(credential_backend=self._backend())

    # ------------------------------------------------------------- registry
    def _registered(self) -> List[Dict[str, str]]:
        """The providers the *composition* knows: id, display name and secret variable."""

        if self._registry_providers is None:
            return []
        try:
            raw = self._registry_providers() or ()
        except Exception:  # noqa: BLE001 - an unreadable registry is an empty one
            return []
        providers: List[Dict[str, str]] = []
        for item in raw:
            provider_id = str(getattr(item, "provider_id", "") or "").strip()
            if not provider_id:
                continue
            providers.append(
                {
                    "provider_id": provider_id,
                    "label": str(getattr(item, "display_name", "") or provider_id),
                    "secret_name": str(getattr(item, "default_secret_env", "") or ""),
                }
            )
        return providers

    def _enabled(self) -> Optional[tuple]:
        if self._enabled_providers is None:
            return None
        try:
            names = self._enabled_providers()
        except Exception:  # noqa: BLE001 - an unreadable enabled set is an unknown one
            return None
        if names is None:
            return None
        return tuple(str(name).strip().lower() for name in names if str(name).strip())

    # -------------------------------------------------------------- entries
    def _entry(self, *, record: Dict[str, Any], registered: Dict[str, Dict[str, str]],
               enabled: Optional[tuple]) -> Dict[str, Any]:
        """One provider entry as the interface sees it: facts, never the key."""

        provider_id = str(record.get("provider_id", "") or "")
        secret_name = str(record.get("secret_name", "") or "")
        secrets = self._secret_store_for_reads()
        has_key = False
        source = ""
        if secret_name:
            try:
                has_key = secrets.has_secret(secret_name)
                source = secrets.secret_source(secret_name) if has_key else ""
            except Exception:  # noqa: BLE001 - an unreadable secret is an absent one
                has_key = False
                source = ""
        known = registered.get(provider_id)
        if known is not None and not secret_name:
            secret_name = str(known.get("secret_name", "") or "")
            if secret_name:
                try:
                    has_key = secrets.has_secret(secret_name)
                    source = secrets.secret_source(secret_name) if has_key else ""
                except Exception:  # noqa: BLE001
                    has_key = False
                    source = ""
        state, reason = _derive_state(
            registered=known is not None,
            needs_key=bool(secret_name),
            has_key=has_key,
            enabled=enabled,
            provider_id=provider_id,
        )
        return {
            "provider_id": provider_id,
            "label": str(record.get("label", "") or (known or {}).get("label", "") or provider_id),
            "preset": str(record.get("preset", "") or ""),
            "base_url": str(record.get("base_url", "") or ""),
            "model": str(record.get("model", "") or ""),
            "secret_name": secret_name,
            "has_key": bool(has_key),
            # A fixed word (``environment`` / ``vault`` / empty) -- never a value.
            "key_source": source,
            "registered": known is not None,
            "state": state,
            "state_reason": reason,
            "added_at": str(record.get("added_at", "") or ""),
        }

    def describe(self) -> Dict[str, Any]:
        """The whole section: configured entries, the presets, and the storage facts.

        The list is the union of two sources, because the owner asked where providers
        come from: every provider the composition knows (with or without a key) and every
        entry he added here. A provider with a key in the environment is therefore listed
        as «готов» beside one whose key he just typed -- both are true, and neither
        payload carries a value.
        """

        registered_list = self._registered()
        registered = {item["provider_id"]: item for item in registered_list}
        enabled = self._enabled()
        records: List[Dict[str, Any]] = list(self._store.list())
        seen = {str(item.get("provider_id", "")) for item in records}
        for item in registered_list:
            if item["provider_id"] in seen:
                continue
            preset = next(
                (
                    dict(entry)
                    for entry in PRESETS
                    if entry["provider_id"] == item["provider_id"]
                ),
                {},
            )
            records.append(
                {
                    "provider_id": item["provider_id"],
                    "label": item["label"],
                    "preset": str(preset.get("preset", "") or ""),
                    "base_url": str(preset.get("base_url", "") or ""),
                    "model": str(preset.get("model", "") or ""),
                    "secret_name": item["secret_name"],
                    "added_at": "",
                }
            )
        entries = [
            self._entry(record=record, registered=registered, enabled=enabled)
            for record in records
        ]
        entries.sort(key=lambda item: (not item["has_key"], item["provider_id"]))
        return {
            "ok": True,
            "providers": entries,
            "presets": [dict(item) for item in PRESETS],
            "settings_path": str(self._store.path),
            "credential_store": str(getattr(self._backend(), "description", "credential store")),
            "counts": {
                "providers": len(entries),
                "ready": len([item for item in entries if item["state"] == "ready"]),
                "with_key": len([item for item in entries if item["has_key"]]),
            },
            "note": (
                "has_key is a boolean: no key value, length or prefix is published here or "
                "stored in the settings file. state is derived from the stored entry, the "
                "secret's presence and the composition's enabled-provider set; it is the "
                "key question, not a probe verdict (GET /api/models publishes probe states)."
            ),
        }

    # ------------------------------------------------------------------ add
    def add(self, body: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """Store one provider: the metadata in the settings file, the key in the vault."""

        payload = dict(body or {})
        preset = preset_by_id(payload.get("preset"))
        provider_id = self._validated_provider_id(payload, preset)
        registered = {item["provider_id"]: item for item in self._registered()}
        is_registered = provider_id in registered
        secret_name = str(preset.get("secret_name", "") or "")
        if not secret_name and is_registered:
            # A preset the owner picked may not name a secret (id and preset can differ):
            # the registry's own declaration wins, so the key is stored under the variable
            # that provider really reads.
            secret_name = str(registered[provider_id].get("secret_name", "") or "")
        if not secret_name:
            record = self._store.find(provider_id)
            secret_name = str((record or {}).get("secret_name", "") or "")
        if not secret_name:
            secret_name = custom_secret_name(provider_id)
        base_url = self._validated_base_url(payload.get("base_url"), preset)
        model = self._validated_model(payload.get("model"))
        key = self._validated_key(payload.get("api_key"))
        if not is_registered:
            # The honest refusal for «Свой»: the key can be stored, but a provider this
            # build does not know cannot serve a run, and saying «готов» would be a lie.
            stored = self._store.upsert(
                {
                    "provider_id": provider_id,
                    "label": str(payload.get("label", "") or preset.get("label", "") or provider_id),
                    "preset": str(preset.get("preset", "") or DEFAULT_PRESET),
                    "base_url": base_url,
                    "model": model,
                    "secret_name": secret_name,
                }
            )
            self._write_key(secret_name, key)
            return {
                "ok": True,
                "saved": True,
                "provider": self._entry(
                    record=stored, registered=registered, enabled=self._enabled()
                ),
                "warning": (
                    "Ключ сохранён, но эта сборка Forge не знает провайдера "
                    f"«{provider_id}»: запуски его не используют."
                ),
                "section": self.describe(),
            }
        stored = self._store.upsert(
            {
                "provider_id": provider_id,
                "label": str(registered[provider_id].get("label", "") or provider_id),
                "preset": str(preset.get("preset", "") or DEFAULT_PRESET),
                "base_url": base_url,
                "model": model,
                "secret_name": secret_name,
            }
        )
        self._write_key(secret_name, key)
        return {
            "ok": True,
            "saved": True,
            "provider": self._entry(
                record=stored, registered=registered, enabled=self._enabled()
            ),
            "section": self.describe(),
        }

    def _write_key(self, secret_name: str, key: str) -> None:
        """Store the key in the vault; a refusal is a plain sentence, never the value."""

        try:
            self._backend().set_secret(secret_name, key)
        except Exception as exc:  # noqa: BLE001 - the vault's message is name-only
            raise ProviderKeyError(
                "Ключ не сохранён: хранилище учётных данных Windows недоступно "
                f"({type(exc).__name__}).",
                field="api_key",
                code="credential_store_unavailable",
            ) from None

    # --------------------------------------------------------------- delete
    def delete(self, provider_id: str) -> Dict[str, Any]:
        """Remove one provider's key from the vault and drop its stored entry."""

        wanted = str(provider_id or "").strip()
        if not PROVIDER_ID_PATTERN.fullmatch(wanted):
            raise ProviderKeyError(
                "Неизвестный провайдер: идентификатор не соответствует формату.",
                field="provider_id",
                code="unknown_provider",
            )
        record = self._store.find(wanted)
        if record is None:
            raise ProviderKeyError(
                f"Провайдер «{wanted}» не добавлен: удалять нечего.",
                field="provider_id",
                code="unknown_provider",
            )
        secret_name = str(record.get("secret_name", "") or "")
        from_env = ""
        removed = False
        if secret_name:
            try:
                source = self._secret_store_for_reads().secret_source(secret_name)
            except Exception:  # noqa: BLE001 - an unreadable secret is not a source
                source = ""
            if source == "environment":
                # The key the run really uses comes from the environment or ``.env``, which
                # this surface must not (and cannot) edit. Say so instead of pretending the
                # provider is gone.
                from_env = (
                    f"Ключ {secret_name} задан в окружении или .env: удаление из "
                    "хранилища Windows его не уберёт."
                )
            try:
                removed = bool(self._backend().delete_secret(secret_name))
            except Exception:  # noqa: BLE001 - a vault refusal is reported as "not removed"
                raise ProviderKeyError(
                    "Ключ не удалён: хранилище учётных данных Windows недоступно.",
                    field="provider_id",
                    code="credential_store_unavailable",
                ) from None
        self._store.remove(wanted)
        return {
            "ok": True,
            "deleted": True,
            "provider_id": wanted,
            "removed_from_vault": removed,
            "notice": from_env,
            "section": self.describe(),
        }

    # ----------------------------------------------------------- validation
    def _validated_provider_id(
        self, payload: Dict[str, Any], preset: Dict[str, str]
    ) -> str:
        preset_id = str(preset.get("provider_id", "") or "")
        if preset_id:
            # A known preset names its own provider: the id is not the caller's to choose,
            # so a request cannot write a key under another provider's variable.
            return preset_id
        provider_id = str(payload.get("provider_id", "") or "").strip().lower()
        if not provider_id:
            raise ProviderKeyError(
                "Укажите идентификатор своего провайдера (например «my-gateway»).",
                field="provider_id",
            )
        if not PROVIDER_ID_PATTERN.fullmatch(provider_id):
            raise ProviderKeyError(
                "Идентификатор провайдера: строчные латинские буквы, цифры, «-» и «_» "
                "(2–40 символов).",
                field="provider_id",
            )
        return provider_id

    @staticmethod
    def _validated_base_url(value: object, preset: Dict[str, str]) -> str:
        text = str(value or "").strip()
        if not text:
            return str(preset.get("base_url", "") or "")
        if len(text) > MAX_BASE_URL_LENGTH:
            raise ProviderKeyError(
                f"Базовый URL слишком длинный (не больше {MAX_BASE_URL_LENGTH} символов).",
                field="base_url",
            )
        if any(character.isspace() for character in text):
            raise ProviderKeyError(
                "Базовый URL не может содержать пробелы.", field="base_url"
            )
        lowered = text.lower()
        if not (lowered.startswith("http://") or lowered.startswith("https://")):
            raise ProviderKeyError(
                "Базовый URL начинается с http:// или https://", field="base_url"
            )
        host = lowered.split("//", 1)[1].split("/", 1)[0]
        if not host or ("." not in host and host not in ("localhost",)):
            raise ProviderKeyError(
                "Базовый URL должен содержать имя хоста, например https://api.example.com/v1.",
                field="base_url",
            )
        return text

    @staticmethod
    def _validated_model(value: object) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        if len(text) > MAX_MODEL_LENGTH:
            raise ProviderKeyError(
                f"Название модели слишком длинное (не больше {MAX_MODEL_LENGTH} символов).",
                field="model",
            )
        if any(character in text for character in ("\r", "\n", "\t", "\x00")):
            raise ProviderKeyError(
                "Название модели не может содержать переводы строк и управляющие символы.",
                field="model",
            )
        return text

    @staticmethod
    def _validated_key(value: object) -> str:
        """The one place a key is inspected -- and it leaves nothing behind.

        The refusals name the rule, never the value: an invalid-key message that echoed
        the value would print the key into the screen, the log and the response body.
        """

        if value is None:
            raise ProviderKeyError(
                "Введите API-ключ: пустой ключ не сохраняется.", field="api_key"
            )
        if not isinstance(value, str):
            raise ProviderKeyError(
                "API-ключ должен быть строкой.", field="api_key"
            )
        key = value.strip()
        if not key:
            raise ProviderKeyError(
                "Введите API-ключ: пустой ключ не сохраняется.", field="api_key"
            )
        if len(key) < MIN_KEY_LENGTH:
            raise ProviderKeyError(
                f"API-ключ слишком короткий (меньше {MIN_KEY_LENGTH} символов): похоже, "
                "это не ключ.",
                field="api_key",
            )
        if len(key) > MAX_KEY_LENGTH:
            raise ProviderKeyError(
                f"API-ключ слишком длинный (больше {MAX_KEY_LENGTH} символов).",
                field="api_key",
            )
        if any(character.isspace() for character in key):
            raise ProviderKeyError(
                "API-ключ не может содержать пробелы и переводы строк.", field="api_key"
            )
        if any(ord(character) < 32 or ord(character) == 127 for character in key):
            raise ProviderKeyError(
                "API-ключ содержит управляющие символы.", field="api_key"
            )
        return key


def _derive_state(
    *,
    registered: bool,
    needs_key: bool,
    has_key: bool,
    enabled: Optional[tuple],
    provider_id: str,
) -> tuple:
    """``(state, reason)`` from four facts, in one place.

    The vocabulary is fixed and the precedence is documented here rather than in the
    interface:

    ``unsupported``  the provider is not in this build's registry, so a key cannot make it
                     usable; the entry is still stored and reported honestly;
    ``disabled``     the provider is known, its key state is satisfied, and the
                     composition's enabled set (when it is readable) does not include it --
                     Forge will not choose it automatically, though an explicit request
                     still names it;
    ``no_key``       the provider needs a key and none resolves from the environment,
                     ``.env`` or the vault;
    ``ready``        the key state is satisfied and nothing above applies. A provider that
                     declares **no** key variable (the offline provider) needs nothing
                     external, so it is never reported as missing a key. This is the *key*
                     question; probe-derived availability lives in ``GET /api/models``.
    """

    if not registered:
        return "unsupported", "provider_not_registered"
    if needs_key and not has_key:
        return "no_key", "key_absent"
    if enabled is not None and provider_id not in enabled:
        return "disabled", "provider_not_enabled"
    return "ready", "key_present"


def _now_iso() -> str:
    """The timestamp a stored entry carries, in UTC ISO form."""

    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds")
