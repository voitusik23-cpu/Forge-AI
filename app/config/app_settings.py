"""The owner's own settings, kept in a file that is **not** ``.env``.

The product needs one setting the application itself owns: *where a delivered
project lives*. It is deliberately not a ``FORGE_*`` environment variable and not a
line in the repository's ``.env``, for two reasons:

* the owner edits it in «Настройки» and expects the change to survive a restart --
  a text field is not a reason to ask him to edit a dotfile by hand;
* ``.env`` holds deployment configuration and credentials. A folder the owner picks
  for his own projects is neither, and mixing the two is how a secret ends up in a
  place it does not belong.

The file is JSON under the repository's ``.forge`` directory
(``.forge/settings.json``), written atomically. It holds an absolute folder path and
nothing else: no key, no token, no provider configuration. ``FORGE_SETTINGS_FILE``
exists only so a test can point the store at a throwaway file.

Two values live in it:

``project_root``
    the folder **new** projects are delivered into. Default
    ``%USERPROFILE%\\Desktop\\Forge Projects``.

``project_roots_known``
    every root this deployment has used, most recent first. The boundary check for a
    project's own folder is proved against these roots, so changing the setting does
    not make the projects already delivered to the previous root unreadable. A
    project folder is therefore only ever accepted under a root the **owner**
    configured -- never under a path a request or a record supplied.

``project_root_fallback``
    the folder projects were actually delivered into because the configured root could
    not be written to (measured: a protected Desktop, `PermissionError`). It is
    remembered so the same fallback is reused instead of being re-chosen on every call,
    and it is added to ``project_roots_known``. It is *not* the owner's choice: his
    ``project_root`` is untouched, so «Сменить папку» (or fixing the permission) brings
    the projects back to the folder he asked for.

``window_downgrade_allowed``
    whether a browser whose own sandbox cannot start may be retried **once** without it
    when Forge opens a project's window (default ``true``: the retry is what turned the
    owner's grey window into his real page). «Настройки» can turn it off, and then the
    launch refuses honestly instead of starting an unsandboxed browser.

``window_plan_memory``
    what this machine has measured about its browsers: a browser that could not start its
    own sandbox is remembered here, so the next launch does not show its blank window even
    for a moment (:mod:`app.projects.window_memory` owns the key). «Забыть» clears it, and
    the switch above being off makes it irrelevant.

``questions_from_model``
    whether the discovery questions may be helped by a model (default ``false``). The
    deterministic domain banks are the questions; the model is an optional extra that
    costs the owner's tokens, so it is **off** unless he turns it on and a ready
    non-mock provider is configured. Turning it off never disables clarification.

``max_cost_tier``
    the ceiling on the **automatic** model choice: ``free``, ``cheap`` or ``paid``. A
    task classified ``COMPLEX`` cannot be auto-routed above it; values above the ceiling
    are never auto-selected, and the router's reasons say when the ceiling changed the
    choice and name the price. Default ``cheap`` -- the safer product default, because the
    owner's balance was burned by a ``COMPLEX`` task routed to a $16.61/1M model. An
    explicit provider+model named by the owner is still honoured (he may choose
    consciously) and «Настройки» warns that it is more expensive than the ceiling.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from app.agent_runtime.routing import (
    DEFAULT_MAX_COST_TIER,
    MAX_COST_TIER_SETTING,
    TIERS,
)

__all__ = [
    "DEFAULT_PROJECT_ROOT_NAME",
    "MAX_COST_TIER_KEY",
    "PROJECT_ROOT_FALLBACK_KEY",
    "PROJECT_ROOT_KEY",
    "PROJECT_ROOTS_KNOWN_KEY",
    "PROVIDERS_KEY",
    "QUESTIONS_FROM_MODEL_KEY",
    "SETTINGS_ENV_VAR",
    "SETTINGS_SCHEMA_VERSION",
    "WINDOW_DOWNGRADE_ALLOWED_KEY",
    "AppSettingsError",
    "AppSettingsStore",
    "default_project_root",
    "default_settings_path",
    "normalise_project_root",
    "read_settings_document",
    "repository_root",
    "write_settings_document",
]

#: The file a test (or a laboratory run) may redirect the settings to.
SETTINGS_ENV_VAR = "FORGE_SETTINGS_FILE"

#: Written into every settings file so a reader can tell what it is reading.
SETTINGS_SCHEMA_VERSION = "forge.settings.v1"

PROJECT_ROOT_KEY = "project_root"
PROJECT_ROOTS_KNOWN_KEY = "project_roots_known"

#: The non-secret half of a provider entry the owner added in «Провайдеры и ключи».
#: The key itself is **never** here: it lives in the operating system's credential vault
#: (:mod:`app.config.windows_creds`). This list holds which provider, its base URL, its
#: default model -- facts that are not secrets.
PROVIDERS_KEY = "providers"

#: The root actually used when the configured one was not writable. Separate from
#: ``project_root`` on purpose: the owner's choice is not overwritten by a fallback.
PROJECT_ROOT_FALLBACK_KEY = "project_root_fallback"

#: Whether a browser that cannot start with its own sandbox may be retried once without
#: it (the documented relaxation of :mod:`app.projects.app_window`). Default ``true``:
#: the owner asked for that retry, and it is what turns a grey window into his page.
#: «Настройки» writes ``false`` to forbid it, and the launch then refuses honestly.
WINDOW_DOWNGRADE_ALLOWED_KEY = "window_downgrade_allowed"

#: Whether the discovery questions may be helped by a model. **Off by default**: the
#: deterministic domain banks are the questions, and the optional path spends the
#: owner's tokens. An absent or non-boolean value therefore means «off», never «on».
QUESTIONS_FROM_MODEL_KEY = "questions_from_model"

#: The ceiling on the automatic model choice, in the same file and under the name the
#: router reads (:data:`app.agent_runtime.routing.MAX_COST_TIER_SETTING`). One spelling,
#: so the store and the decision cannot disagree about which key is the ceiling.
MAX_COST_TIER_KEY = MAX_COST_TIER_SETTING

#: Every key this store owns. Everything else in the file is preserved verbatim, so a
#: setting written by another module (the provider entries above) is not erased the next
#: time the owner changes his projects folder.
_OWNED_KEYS = (
    "schema",
    "updated_at",
    PROJECT_ROOT_KEY,
    PROJECT_ROOTS_KNOWN_KEY,
    PROJECT_ROOT_FALLBACK_KEY,
)

#: The folder created on the owner's desktop by default.
DEFAULT_PROJECT_ROOT_NAME = "Forge Projects"

#: How many previous roots are remembered. Bounded on purpose: the list exists so an
#: already-delivered project stays reachable, not as a history of the owner's choices.
MAX_KNOWN_ROOTS = 20

#: Characters Windows forbids in a file name are not the issue here -- the root is a
#: whole path the owner typed. What is refused is anything that could not name one
#: folder: an empty value, a relative value, a device path, or one that cannot be
#: created. The list below is the set of path *prefixes* with no single folder meaning.
_REFUSED_PREFIXES = ("\\\\?\\", "\\\\.\\")


class AppSettingsError(ValueError):
    """The requested setting is not a usable value. A refusal, never a crash."""

    code = "invalid_app_setting"

    def __init__(self, message: str, *, field: str = "") -> None:
        super().__init__(message)
        self.field = str(field)


def repository_root() -> Path:
    """The repository this module belongs to, from its own location."""

    return Path(__file__).resolve().parents[2]


def default_settings_path() -> Path:
    """``<repository>/.forge/settings.json``, unless ``FORGE_SETTINGS_FILE`` names one."""

    configured = os.environ.get(SETTINGS_ENV_VAR)
    if configured and configured.strip():
        return Path(configured).expanduser()
    return repository_root() / ".forge" / "settings.json"


def read_settings_document(path: object) -> dict:
    """The whole settings document as it is on disk, or ``{}`` when unreadable.

    Deliberately unnormalised: it is what :func:`write_settings_document` writes back, so
    a module that owns one key (the provider entries) can change only that key and leave
    every other value exactly as another writer left it.
    """

    try:
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return dict(data) if isinstance(data, dict) else {}


def write_settings_document(path: object, document: object) -> Path:
    """Persist one settings document atomically, replacing the file in one step.

    A temporary file beside the target plus ``os.replace``: a half-written settings file
    would decide a folder (or a provider list) from a fragment.
    """

    target = Path(path)
    payload = dict(document) if isinstance(document, dict) else {}
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f"{target.name}.{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.flush()
        try:
            os.fsync(handle.fileno())
        except OSError:
            pass
    os.replace(temporary, target)
    return target


def _user_home() -> Path:
    """``%USERPROFILE%`` (or the platform's home), never the repository."""

    profile = os.environ.get("USERPROFILE")
    if profile and profile.strip():
        return Path(profile)
    return Path.home()


def default_project_root() -> Path:
    """The folder a fresh deployment delivers projects into."""

    return _user_home() / "Desktop" / DEFAULT_PROJECT_ROOT_NAME


def normalise_project_root(value: object) -> Path:
    """Validate one owner-supplied root and return it as an absolute path.

    A relative value is refused instead of being resolved against the process's
    current directory: ``Projects`` typed into the field would otherwise mean a
    different folder depending on how the server happened to be started, and the
    owner would have no way to tell which one he got.
    """

    if not isinstance(value, str) or not value.strip():
        raise AppSettingsError(
            "Укажите папку для проектов: пустой путь не сохраняется.", field="project_root"
        )
    text = value.strip().strip('"').strip("'")
    if not text:
        raise AppSettingsError(
            "Укажите папку для проектов: пустой путь не сохраняется.", field="project_root"
        )
    if "\x00" in text:
        raise AppSettingsError(
            "Путь к папке проектов содержит недопустимый символ.", field="project_root"
        )
    for prefix in _REFUSED_PREFIXES:
        if text.startswith(prefix):
            raise AppSettingsError(
                "Путь вида \\\\?\\ или \\\\.\\ не поддерживается: укажите обычную папку.",
                field="project_root",
            )
    expanded = Path(os.path.expandvars(text)).expanduser()
    if ".." in expanded.parts:
        raise AppSettingsError(
            "Путь к папке проектов не может содержать «..»: укажите полный путь.",
            field="project_root",
        )
    if not expanded.is_absolute():
        raise AppSettingsError(
            "Укажите полный путь к папке проектов (например, "
            f"{default_project_root()}).", field="project_root"
        )
    if expanded.drive and expanded == Path(expanded.drive + "\\"):
        raise AppSettingsError(
            "Корень диска нельзя использовать как папку проектов: выберите папку внутри него.",
            field="project_root",
        )
    if expanded.exists() and not expanded.is_dir():
        raise AppSettingsError(
            f"По этому пути лежит файл, а не папка: {expanded}", field="project_root"
        )
    return expanded


def _as_path(value: object) -> Optional[Path]:
    """A stored value as a path, or ``None`` when it is not a usable absolute one."""

    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return normalise_project_root(value)
    except AppSettingsError:
        return None


class AppSettingsStore:
    """Read and write the owner's settings file atomically."""

    def __init__(self, path: Optional[object] = None) -> None:
        self._path = Path(path) if path is not None else default_settings_path()

    # ------------------------------------------------------------------ path
    @property
    def path(self) -> Path:
        return self._path

    # ------------------------------------------------------------------- I/O
    def read(self) -> Dict[str, object]:
        """The stored document, or an empty one when it is missing or unreadable.

        A corrupt file is reported as empty rather than partially trusted: half a
        settings document would decide a folder from a fragment. Entries this store does
        not own (the provider list) are passed through unchanged, so a reader sees the
        file's real content and a writer never drops them.
        """

        data = read_settings_document(self._path)
        if not data:
            return {}
        root = data.get(PROJECT_ROOT_KEY)
        known = data.get(PROJECT_ROOTS_KNOWN_KEY)
        fallback = data.get(PROJECT_ROOT_FALLBACK_KEY)
        providers = data.get(PROVIDERS_KEY)
        return {
            "schema": str(data.get("schema", "") or ""),
            PROJECT_ROOT_KEY: root if isinstance(root, str) else "",
            PROJECT_ROOTS_KNOWN_KEY: [
                str(item) for item in (known or ()) if isinstance(item, str)
            ]
            if isinstance(known, list)
            else [],
            PROJECT_ROOT_FALLBACK_KEY: fallback if isinstance(fallback, str) else "",
            PROVIDERS_KEY: [
                dict(item) for item in (providers or ()) if isinstance(item, dict)
            ]
            if isinstance(providers, list)
            else [],
            "updated_at": str(data.get("updated_at", "") or ""),
        }

    def _write(
        self, root: Path, known: Iterable[object], *, fallback: str = ""
    ) -> Path:
        """Atomically persist ``root``, the fallback root and the known roots.

        Every key this store does not own survives the write. That is not politeness: the
        provider entries live in the same file, and a «Сменить папку» that silently erased
        them would delete the owner's provider configuration.
        """

        entries: List[str] = []
        for item in (str(root), str(fallback or ""), *[str(value) for value in known]):
            if item and item not in entries:
                entries.append(item)
        payload: Dict[str, object] = {
            key: value
            for key, value in read_settings_document(self._path).items()
            if key not in _OWNED_KEYS
        }
        payload.update(
            {
                "schema": SETTINGS_SCHEMA_VERSION,
                PROJECT_ROOT_KEY: str(root),
                PROJECT_ROOTS_KNOWN_KEY: entries[:MAX_KNOWN_ROOTS],
                "updated_at": _now_iso(),
            }
        )
        if str(fallback or ""):
            payload[PROJECT_ROOT_FALLBACK_KEY] = str(fallback)
        return write_settings_document(self._path, payload)

    # ---------------------------------------------------------------- values
    def project_root(self) -> Path:
        """The folder new projects are delivered into.

        Never empty and always absolute: a missing or unusable stored value falls back
        to :func:`default_project_root` and **the fallback is not written back**, so a
        broken file never silently rewrites the owner's choice.
        """

        stored = _as_path(self.read().get(PROJECT_ROOT_KEY))
        return stored if stored is not None else default_project_root()

    def default_root(self) -> Path:
        """What «Использовать рабочий стол» restores."""

        return default_project_root()

    def known_project_roots(self) -> Tuple[Path, ...]:
        """Every root this deployment has used, the current one first.

        The remembered fallback comes second: a project delivered to it must keep
        resolving after the preferred root becomes writable again.
        """

        current = self.project_root()
        found: List[Path] = [current]
        fallback = self.remembered_root()
        if fallback is not None and fallback not in found:
            found.append(fallback)
        for item in self.read().get(PROJECT_ROOTS_KNOWN_KEY) or ():
            path = _as_path(item)
            if path is not None and path not in found:
                found.append(path)
        return tuple(found)

    def remembered_root(self) -> Optional[Path]:
        """The fallback root this deployment used last time, or ``None``."""

        return _as_path(self.read().get(PROJECT_ROOT_FALLBACK_KEY))

    def remember_fallback_root(self, value: object) -> Path:
        """Remember the root projects were delivered into because the other one failed.

        ``project_root`` -- the owner's own choice -- is deliberately **not** replaced:
        a fallback is a fact about this machine, not a new preference, and overwriting
        the setting would silently move his projects for good the first time a
        permission changed.
        """

        root = normalise_project_root(str(value))
        if self.project_root() == root:
            return root
        document = self.read()
        if str(root) == str(document.get(PROJECT_ROOT_FALLBACK_KEY) or ""):
            return root
        known = [item for item in self.known_project_roots() if item != root]
        self._write(self.project_root(), known, fallback=str(root))
        return root

    # ------------------------------------------------------- how windows open
    def window_downgrade_allowed(self) -> bool:
        """Whether a browser may be retried once without its own sandbox.

        Read from the raw document, not from :meth:`read` (which normalises the
        project-root keys): the boolean the owner set in «Настройки» is exactly what this
        answers. A missing, unreadable or non-boolean value means **allowed** -- that is
        the documented default, and the retry is the owner's own request.
        """

        raw = read_settings_document(self._path).get(WINDOW_DOWNGRADE_ALLOWED_KEY)
        return raw if isinstance(raw, bool) else True

    def set_window_downgrade_allowed(self, value: object) -> bool:
        """Persist the owner's «повтор без песочницы» switch and return it.

        Only that one key is written, so the provider entries and the project root in the
        same file survive unchanged.
        """

        if not isinstance(value, bool):
            raise AppSettingsError(
                "Значение «повтор без песочницы» должно быть true или false.",
                field=WINDOW_DOWNGRADE_ALLOWED_KEY,
            )
        document = read_settings_document(self._path)
        document[WINDOW_DOWNGRADE_ALLOWED_KEY] = bool(value)
        document.setdefault("schema", SETTINGS_SCHEMA_VERSION)
        document["updated_at"] = _now_iso()
        write_settings_document(self._path, document)
        return bool(value)

    # ------------------------------------------------------ the window memory
    def window_plan_memory(self) -> List[Dict[str, Any]]:
        """What this machine remembers about its browsers, as «Настройки» shows it.

        Owned by :mod:`app.projects.window_memory`, in the same file and read through this
        store's own path, so the screen and the launch can never read two different files.
        A memory that cannot be read answers an empty list: a settings screen that 500s
        tells the owner nothing.
        """

        from app.projects.window_memory import WindowPlanMemory

        try:
            return WindowPlanMemory(self._path).describe()
        except Exception:  # noqa: BLE001 - an unreadable memory is "nothing is remembered"
            return []

    def forget_window_plan_memory(self) -> bool:
        """«Забыть»: clear every remembered browser, so the next launch measures afresh."""

        from app.projects.window_memory import WindowPlanMemory

        try:
            return WindowPlanMemory(self._path).forget()
        except Exception:  # noqa: BLE001 - a memory that cannot be written is not fatal
            return False

    # ------------------------------------------------- questions from a model
    def questions_from_model(self) -> bool:
        """Whether the optional model-written questions are allowed.

        **Off unless the owner turned it on.** The raw document is read (not the
        normalised one), and anything that is not the boolean ``true`` -- a missing key,
        a corrupt file, a string someone typed -- answers ``false``: the expensive path
        is never entered by accident.
        """

        raw = read_settings_document(self._path).get(QUESTIONS_FROM_MODEL_KEY)
        return raw if isinstance(raw, bool) else False

    def set_questions_from_model(self, value: object) -> bool:
        """Persist the «вопросы по смыслу задачи (через модель)» switch and return it.

        One key of the same file: the provider entries, the window policy and the
        projects folder survive unchanged.
        """

        if not isinstance(value, bool):
            raise AppSettingsError(
                "Значение «вопросы через модель» должно быть true или false.",
                field=QUESTIONS_FROM_MODEL_KEY,
            )
        document = read_settings_document(self._path)
        document[QUESTIONS_FROM_MODEL_KEY] = bool(value)
        document.setdefault("schema", SETTINGS_SCHEMA_VERSION)
        document["updated_at"] = _now_iso()
        write_settings_document(self._path, document)
        return bool(value)

    # ------------------------------------------------------- the cost ceiling
    def max_cost_tier(self) -> str:
        """The ceiling on the automatic model choice, as ``FREE``/``CHEAP``/``PAID``.

        Read from the raw document (not :meth:`read`, which normalises the project-root
        keys). A missing, non-string or unknown value answers the default ``CHEAP``: the
        ceiling is a cost control, so an unrecognised value must not widen it.
        """

        from app.agent_runtime.routing import normalise_cost_tier

        return normalise_cost_tier(read_settings_document(self._path).get(MAX_COST_TIER_KEY))

    def set_max_cost_tier(self, value: object) -> str:
        """Persist the ceiling and return it as the uppercase word the router speaks.

        The stored form is the lowercase word (``"cheap"``) -- it is read by the owner in
        the settings file -- and every other key in the same file survives unchanged.
        A value outside ``free``/``cheap``/``paid`` is refused: an unknown ceiling must
        not silently become the most permissive one.
        """

        text = str(value or "").strip().upper()
        if text not in TIERS:
            raise AppSettingsError(
                "Потолок тарифа должен быть free, cheap или paid.",
                field=MAX_COST_TIER_KEY,
            )
        document = read_settings_document(self._path)
        document[MAX_COST_TIER_KEY] = text.lower()
        document.setdefault("schema", SETTINGS_SCHEMA_VERSION)
        document["updated_at"] = _now_iso()
        write_settings_document(self._path, document)
        return text

    def set_project_root(self, value: object) -> Path:
        """Store ``value`` as the project root and remember the previous ones.

        Choosing a root is a new decision, so a remembered fallback from the old
        configuration is dropped: it was an answer to a question the owner just asked
        differently.
        """

        root = normalise_project_root(value)
        known = [item for item in self.known_project_roots() if item != root]
        self._write(root, known)
        return root

    def use_desktop(self) -> Path:
        """«Использовать рабочий стол»: restore the documented default root."""

        return self.set_project_root(str(default_project_root()))

    def ensure_root(self) -> Path:
        """Create the configured root when it does not exist yet, and return it."""

        root = self.project_root()
        try:
            root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise AppSettingsError(
                f"Папку проектов не удалось создать: {root} ({type(exc).__name__})",
                field="project_root",
            ) from exc
        return root

    def describe(self, *, candidates: Optional[Iterable[object]] = None) -> Dict[str, object]:
        """What «Настройки» shows about the root: the value, the default, and the truth.

        This is the one place an absolute path of the host is published on purpose: the
        owner has to be able to see and paste the folder he chose. It is *his* setting,
        not a project's internal path, and no request value reaches it.

        It also answers the question he actually asks after a refusal -- *where did my
        projects go?* -- by probing the configured root and reporting the folder that
        will really be used: ``project_root_effective``, ``project_root_used_fallback``,
        and a plain-language ``project_root_notice``. A complete refusal is reported as
        fields, not as an exception: a settings screen that 500s tells him nothing.
        """

        root = self.project_root()
        fallback = self.remembered_root()
        effective: Optional[Path] = None
        notice = ""
        used_fallback = False
        attempted: List[Dict[str, object]] = []
        label_grant_attempted = False
        label_granted = False
        label_grant_reason = ""
        label_grant_command = ""
        try:
            decision = self.resolve_project_root(candidates=candidates)
        except Exception as exc:  # noqa: BLE001 - a refusal is a fact to report, not a crash
            attempted = [
                attempt.to_dict()
                for attempt in getattr(exc, "attempts", ()) or ()
            ]
            notice = str(exc)
        else:
            if decision is not None:
                effective = decision.root
                used_fallback = bool(decision.fallback)
                notice = decision.notice
                attempted = [attempt.to_dict() for attempt in decision.attempts]
                # THE ONE-TIME LABEL GRANT, as a reported fact. ``label_granted`` says the
                # desktop folder now accepts a low-integrity write; when it was attempted
                # and did not work, the command that would fix it is published so «Настройки»
                # can show the owner exactly what to run.
                label_grant_attempted = bool(decision.label_grant_attempted)
                label_granted = bool(decision.label_granted)
                label_grant_reason = str(decision.label_grant_reason or "")
                if label_grant_attempted or used_fallback:
                    from app.projects.root_selection import label_grant_command as _command

                    label_grant_command = _command(decision.preferred)
        return {
            "project_root": str(root),
            "default_project_root": str(default_project_root()),
            "project_root_exists": bool(root.is_dir()),
            "project_root_effective": str(effective) if effective is not None else "",
            "project_root_fallback": str(fallback) if fallback is not None else "",
            "project_root_used_fallback": used_fallback,
            "project_root_notice": notice,
            "project_root_attempted": attempted,
            "project_root_label_grant_attempted": label_grant_attempted,
            "project_root_label_granted": label_granted,
            "project_root_label_grant_reason": label_grant_reason,
            "project_root_label_grant_command": label_grant_command,
            "project_roots_known": [str(item) for item in self.known_project_roots()],
            "window_downgrade_allowed": self.window_downgrade_allowed(),
            # What this machine has measured about its browsers: the browsers whose own
            # sandbox cannot start here, and the evidence for it. «Забыть» clears it.
            "window_plan_memory": self.window_plan_memory(),
            # Off by default; the card explains that it uses the owner's tokens.
            "questions_from_model": self.questions_from_model(),
            # The ceiling on the automatic model choice, as the lowercase word the settings
            # file stores, plus its documented default and the words that are accepted.
            # «Настройки» reads these to draw the control without guessing the vocabulary.
            "max_cost_tier": self.max_cost_tier().lower(),
            "max_cost_tier_default": DEFAULT_MAX_COST_TIER.lower(),
            "max_cost_tier_options": [tier.lower() for tier in TIERS],
            "settings_path": str(self._path),
            "schema": SETTINGS_SCHEMA_VERSION,
        }

    def resolve_project_root(
        self, *, candidates: Optional[Iterable[object]] = None
    ) -> Optional[object]:
        """The writable root the next project goes into, and the reason it is that one.

        Returns an :class:`app.projects.root_selection.ProjectRootDecision`, or ``None``
        when no root is configured. The chosen fallback is remembered here, once, so the
        next call reuses it instead of re-deciding where the owner's projects live.
        """

        from app.projects.root_selection import resolve_writable_root

        preferred = self.project_root()
        decision = resolve_writable_root(
            preferred,
            remembered=self.remembered_root(),
            candidates=candidates,
        )
        if decision.fallback:
            self.remember_fallback_root(decision.root)
        return decision


def _now_iso() -> str:
    """The timestamp written into the settings file, in UTC ISO form."""

    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds")
