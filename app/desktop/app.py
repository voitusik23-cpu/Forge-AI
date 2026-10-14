"""The Forge desktop program: language, introduction, access, and the working shell.

Order of events when the program opens:

1. the animated introduction (:mod:`app.desktop.intro`) -- skippable, 23 seconds;
2. the access window (:class:`RegistrationGate`) -- email plus a confirmation code, or
   the labelled developer entry that skips confirmation;
3. the shell -- navigation, a language switch that re-labels every control, and the
   in-program manual.

Two deliberate details:

* the window title in Russian is exactly ``FORGE — «Кузница»``, the string the existing
  shell test asserts;
* ``CONNECTED`` / ``DISCONNECTED`` stay untranslated. They are status tokens, like
  ``PASSED`` and ``UNKNOWN`` elsewhere in Forge, and the interface translates the word
  around them ("Ядро: CONNECTED") rather than the token itself.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Any, Callable, Dict, List, Optional, Tuple, Type

from app.api.client import ForgeApiClient
from app.api.service import ForgeApiService
from app.desktop.accounts import DEVELOPER_EMAIL, AccountStore, LocalCodeDelivery
from app.desktop.i18n import Translator, available_languages
from app.desktop.intro import ForgeIntro
from app.desktop.views import (
    AgentsView,
    BaseView,
    CostsView,
    HomeView,
    ProjectsView,
    RunsView,
    SettingsView,
)

try:  # pragma: no cover - styling only
    import customtkinter as ctk

    _USE_CTK = True
except ImportError:  # pragma: no cover
    _USE_CTK = False


MANUAL_SECTIONS: Tuple[str, ...] = (
    "manual.section.start",
    "manual.section.register",
    "manual.section.keys",
    "manual.section.run",
    "manual.section.status",
    "manual.section.files",
)

MANUAL_BODY: Dict[str, Dict[str, str]] = {
    "manual.section.start": {
        "en": (
            "Double-click the Forge shortcut, or run\n"
            "    python -m app.desktop\n"
            "from the repository root. The introduction plays once; a click or Esc skips "
            "it. The web console starts separately with\n"
            "    scripts\\run_dev_console.cmd 8011\n"
            "and serves http://127.0.0.1:8011/ on loopback only."
        ),
        "ru": (
            "Дважды щёлкните ярлык Forge или выполните\n"
            "    python -m app.desktop\n"
            "из корня репозитория. Заставка проигрывается один раз; щелчок или Esc её "
            "пропускают. Веб-консоль запускается отдельно:\n"
            "    scripts\\run_dev_console.cmd 8011\n"
            "и отдаёт http://127.0.0.1:8011/ только на loopback."
        ),
    },
    "manual.section.register": {
        "en": (
            "Enter an email and press \"Send confirmation code\". No mail server is "
            "configured by default, so the code appears in the window (local mode) and "
            "never leaves this machine; set FORGE_SMTP_HOST, FORGE_SMTP_PORT, "
            "FORGE_SMTP_USER, FORGE_SMTP_PASSWORD and FORGE_SMTP_FROM to send real "
            "email. The code is stored only as a hash, expires after ten minutes and "
            "stops working after five wrong attempts. \"I am the developer\" enters "
            "without confirmation."
        ),
        "ru": (
            "Укажите почту и нажмите «Отправить код подтверждения». Почтовый сервер по "
            "умолчанию не настроен, поэтому код показывается в окне (локальный режим) и "
            "никуда не уходит; чтобы отправлять настоящие письма, задайте FORGE_SMTP_HOST, "
            "FORGE_SMTP_PORT, FORGE_SMTP_USER, FORGE_SMTP_PASSWORD и FORGE_SMTP_FROM. "
            "Код хранится только как хеш, действует десять минут и перестаёт работать "
            "после пяти неверных попыток. Кнопка «Я разработчик» входит без подтверждения."
        ),
    },
    "manual.section.keys": {
        "en": (
            "Provider keys are read from the environment, never from a committed file. "
            ".env is ignored by git and .env.example lists the names. The Settings view "
            "shows which providers are configured; a provider without a key is reported "
            "as unavailable instead of being quietly skipped."
        ),
        "ru": (
            "Ключи провайдеров читаются из переменных окружения и никогда — из файла, "
            "который попадает в git. .env в игнорировании, а .env.example перечисляет "
            "имена. В разделе «Настройки» видно, какие провайдеры настроены; провайдер "
            "без ключа сообщается как недоступный, а не пропускается молча."
        ),
    },
    "manual.section.run": {
        "en": (
            "Open Forge, type the task into \"Task Prompt / Instruction\", press \"Run "
            "task in core\". Nothing executes without an explicit authorization: a "
            "recommendation is not a permission and a permission is not an execution. "
            "Every run is written to the run store, and a step reports what it actually "
            "did, including UNKNOWN when the evidence is missing."
        ),
        "ru": (
            "Откройте «Кузницу», введите задачу в поле «Task Prompt / Instruction» и "
            "нажмите «Запустить задачу в ядре». Ничто не выполняется без явной "
            "авторизации: рекомендация — не разрешение, а разрешение — не исполнение. "
            "Каждый запуск пишется в хранилище, а шаг сообщает то, что реально сделал, "
            "включая UNKNOWN, когда доказательств нет."
        ),
    },
    "manual.section.status": {
        "en": (
            "PASSED means a declared check was evaluated and held. FAILED means it was "
            "evaluated and did not hold. ERROR and UNKNOWN mean it could not be "
            "evaluated, and INDETERMINATE means the attempt cannot be summarised as "
            "success. A report is never treated as evidence."
        ),
        "ru": (
            "PASSED — объявленная проверка выполнена и подтвердилась. FAILED — выполнена "
            "и не подтвердилась. ERROR и UNKNOWN — проверить не удалось, INDETERMINATE — "
            "попытку нельзя свести к успеху. Отчёт никогда не считается доказательством."
        ),
    },
    "manual.section.files": {
        "en": (
            "Project files are in the repository. Run records and the graph journal live "
            "in the run store (FORGE_RUN_STORE_ROOT). Accounts are kept in "
            "%LOCALAPPDATA%\\ForgeAI\\accounts.json, or in .forge\\accounts.json when the "
            "platform refuses that directory; the confirmation code itself is never "
            "written there."
        ),
        "ru": (
            "Файлы проекта лежат в репозитории. Записи запусков и журнал графа — в "
            "хранилище запусков (FORGE_RUN_STORE_ROOT). Учётные записи хранятся в "
            "%LOCALAPPDATA%\\ForgeAI\\accounts.json, а если платформа запрещает этот "
            "каталог — в .forge\\accounts.json; сам код подтверждения туда не пишется."
        ),
    },
}


class RegistrationGate:
    """The access window: email, confirmation code, and the developer entry."""

    def __init__(
        self,
        parent: tk.Misc,
        translator: Translator,
        accounts: AccountStore,
        on_enter: Callable[[str], None],
    ) -> None:
        self._t = translator
        self._accounts = accounts
        self._on_enter = on_enter
        self._pending_email = ""

        self.window = tk.Toplevel(parent)
        self.window.geometry("580x400")
        self.window.resizable(False, False)
        self.window.protocol("WM_DELETE_WINDOW", lambda: self._on_enter(""))
        self.window.configure(bg="#12141c")

        self._frame = ttk.Frame(self.window, padding=22)
        self._frame.pack(fill="both", expand=True)

        self.title_label = ttk.Label(self._frame, font=("Segoe UI", 15, "bold"))
        self.title_label.pack(anchor="w")
        self.subtitle_label = ttk.Label(self._frame, font=("Segoe UI", 10))
        self.subtitle_label.pack(anchor="w", pady=(0, 14))

        self.email_label = ttk.Label(self._frame, font=("Segoe UI", 10))
        self.email_label.pack(anchor="w")
        self.email_entry = ttk.Entry(self._frame, width=46)
        self.email_entry.pack(anchor="w", pady=(2, 8))
        self.email_button = ttk.Button(self._frame, command=self._send_code)
        self.email_button.pack(anchor="w")

        self.code_label = ttk.Label(self._frame, font=("Segoe UI", 10))
        self.code_label.pack(anchor="w", pady=(14, 0))
        self.code_entry = ttk.Entry(self._frame, width=16)
        self.code_entry.pack(anchor="w", pady=(2, 8))
        self.confirm_button = ttk.Button(self._frame, command=self._confirm)
        self.confirm_button.pack(anchor="w")

        self.developer_button = ttk.Button(self._frame, command=self._developer)
        self.developer_button.pack(anchor="w", pady=(16, 0))
        self.developer_hint = ttk.Label(self._frame, font=("Segoe UI", 9))
        self.developer_hint.pack(anchor="w")

        self.message = tk.StringVar(value="")
        self.message_label = ttk.Label(
            self._frame, textvariable=self.message, wraplength=500, justify="left"
        )
        self.message_label.pack(anchor="w", pady=(12, 0))

        self.retranslate()

    def retranslate(self) -> None:
        """Apply the current language to every control in this window."""

        self.window.title(self._t.t("register.title"))
        self.title_label.configure(text=self._t.t("register.title"))
        self.subtitle_label.configure(text=self._t.t("register.subtitle"))
        self.email_label.configure(text=self._t.t("register.email"))
        self.email_button.configure(text=self._t.t("register.send_code"))
        self.code_label.configure(text=self._t.t("register.code"))
        self.confirm_button.configure(text=self._t.t("register.confirm"))
        self.developer_button.configure(text=self._t.t("register.dev"))
        self.developer_hint.configure(text=self._t.t("register.dev.hint"))

    # ------------------------------------------------------------------------- actions
    def send_code(self) -> str:
        """Ask for a code; returns the message that is now on screen."""

        email = self.email_entry.get()
        ok, message_key, shown = self._accounts.register(email)
        if not ok:
            self.message.set(self._t.t(message_key))
            return self.message.get()
        self._pending_email = email
        if "|" in message_key:
            template, delivery = message_key.split("|", 1)
            text = self._t.t(template, delivery=delivery, email=email)
        else:
            text = self._t.t(message_key, email=email)
        if shown:
            text = text + "\n" + self._t.t("register.code.local_notice") + f"\n\n{shown}"
        self.message.set(text)
        self.code_entry.focus_set()
        return text

    def confirm(self) -> bool:
        """Check the typed code and let the person in when it is right."""

        address = self._pending_email or self.email_entry.get()
        ok, message_key = self._accounts.confirm(address, self.code_entry.get())
        if not ok:
            self.message.set(self._t.t(message_key))
            return False
        self.message.set(self._t.t("register.welcome", email=address))
        self._on_enter(address)
        return True

    def developer(self) -> str:
        """Enter without registration, and say so."""

        account = self._accounts.developer_access()
        self.message.set(self._t.t("register.welcome", email=account.email))
        self._on_enter(account.email)
        return account.email

    # The buttons call these; the names above are what tests drive.
    _send_code = send_code
    _confirm = confirm
    _developer = developer

    def destroy(self) -> None:
        try:
            self.window.destroy()
        except tk.TclError:
            pass


class ForgeDesktopApp:
    """The shell: navigation, language, manual, and the opening sequence."""

    def __init__(
        self,
        client: Optional[ForgeApiClient] = None,
        api_service: Optional[ForgeApiService] = None,
        headless: bool = False,
        translator: Optional[Translator] = None,
        accounts: Optional[AccountStore] = None,
        show_intro: bool = True,
        intro_duration: Optional[float] = None,
    ) -> None:
        self._headless = headless
        self._client = client or ForgeApiClient(api_service=api_service or ForgeApiService())
        self._t = translator or Translator("ru")
        self._accounts = accounts or AccountStore()
        # The introduction is an interactive opening; it never runs in headless mode.
        self._show_intro = bool(show_intro) and not headless
        self._intro_duration = intro_duration

        self._root: Optional[Any] = None
        self._current_view_name: str = ""
        self._views: Dict[str, Any] = {}
        self._nav_buttons: Dict[str, Any] = {}
        self._language_buttons: Dict[str, Any] = {}
        self._is_running: bool = False
        self._core_connected: bool = False
        self._signed_in_as: str = ""
        self._gate: Optional[RegistrationGate] = None
        self._intro: Optional[ForgeIntro] = None

    # ------------------------------------------------------------------------ accessors
    @property
    def client(self) -> ForgeApiClient:
        return self._client

    @property
    def root(self) -> Optional[Any]:
        return self._root

    @property
    def is_running(self) -> bool:
        return self._is_running

    @property
    def current_view_name(self) -> str:
        return self._current_view_name

    @property
    def translator(self) -> Translator:
        return self._t

    @property
    def language(self) -> str:
        return self._t.language

    @property
    def signed_in_as(self) -> str:
        return self._signed_in_as

    @property
    def accounts(self) -> AccountStore:
        return self._accounts

    @staticmethod
    def _nav_keys() -> Tuple[str, ...]:
        return (
            "nav.forge",
            "nav.projects",
            "nav.agents",
            "nav.runs",
            "nav.costs",
            "nav.settings",
            "nav.manual",
        )

    def nav_labels(self) -> List[str]:
        """Current labels of the navigation buttons, in order."""

        return [self._t.t(key) for key in self._nav_keys()]

    def manual_text(self, key: str) -> str:
        """The manual section in the current language."""

        return MANUAL_BODY[key][self._t.language]

    # ------------------------------------------------------------------------- language
    def set_language(self, language: str) -> str:
        """Switch language and re-label every control that is on screen."""

        self._t.set_language(language)
        if self._root is not None:
            self._retranslate()
        return self._t.language

    def toggle_language(self) -> str:
        return self.set_language("en" if self._t.language == "ru" else "ru")

    # ---------------------------------------------------------------------- lifecycle
    def initialize(self) -> None:
        """Create the window and the widget hierarchy."""

        if _USE_CTK and not self._headless:
            ctk.set_appearance_mode("Dark")
            ctk.set_default_color_theme("blue")
            self._root = ctk.CTk()
        else:
            self._root = tk.Tk()

        self._root.geometry("1080x700")
        self._root.minsize(860, 520)
        self._setup_styles()
        self._build_sidebar()
        self._build_content_area()
        self._retranslate()
        self._is_running = True

    def _setup_styles(self) -> None:
        style = ttk.Style(self._root)
        for theme in ("clam", "vista", "default"):
            try:
                style.theme_use(theme)
                break
            except Exception:  # noqa: BLE001 - any available theme beats crashing
                continue

    def _build_sidebar(self) -> None:
        self.sidebar = ttk.Frame(self._root, width=215, padding=10)
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)

        self.brand_label = ttk.Label(self.sidebar, font=("Segoe UI", 15, "bold"))
        self.brand_label.pack(anchor="w", padx=10, pady=(8, 0))
        self.subtitle_label = ttk.Label(self.sidebar, font=("Segoe UI", 9, "italic"))
        self.subtitle_label.pack(anchor="w", padx=10, pady=(0, 12))

        # The language switch: one button per shipped language, so the current one is
        # always visible rather than hidden inside a menu.
        self.language_frame = ttk.LabelFrame(self.sidebar, padding=6)
        self.language_frame.pack(fill="x", padx=6, pady=(0, 10))
        self.language_label = ttk.Label(self.language_frame, font=("Segoe UI", 9))
        self.language_label.pack(anchor="w")
        for code, native in available_languages():
            button = ttk.Button(
                self.language_frame,
                text=native,
                width=9,
                command=lambda chosen=code: self.set_language(chosen),
            )
            button.pack(side="left", padx=2, pady=2)
            self._language_buttons[code] = button

        for key in self._nav_keys():
            button = ttk.Button(
                self.sidebar,
                command=lambda view=key.split(".", 1)[1]: self.show_view(view),
            )
            button.pack(fill="x", padx=5, pady=3)
            self._nav_buttons[key] = button

        self.lbl_sidebar_status = ttk.Label(self.sidebar, font=("Segoe UI", 9))
        self.lbl_sidebar_status.pack(side="bottom", anchor="w", padx=10, pady=10)

    def _build_content_area(self) -> None:
        self.content_container = ttk.Frame(self._root)
        self.content_container.pack(side="right", fill="both", expand=True)

        view_classes: Dict[str, Type[BaseView]] = {
            "forge": HomeView,
            "projects": ProjectsView,
            "agents": AgentsView,
            "runs": RunsView,
            "costs": CostsView,
            "settings": SettingsView,
        }
        for name, view_class in view_classes.items():
            self._views[name] = view_class(self.content_container, self._client)

        self._build_manual()

    def _build_manual(self) -> None:
        self.manual_frame = ttk.Frame(self.content_container, padding=18)
        self.manual_title = ttk.Label(self.manual_frame, font=("Segoe UI", 15, "bold"))
        self.manual_title.pack(anchor="w")
        self.manual_intro = ttk.Label(
            self.manual_frame, wraplength=760, justify="left", font=("Segoe UI", 10)
        )
        self.manual_intro.pack(anchor="w", pady=(0, 12))
        self.manual_sections: Dict[str, Tuple[Any, Any]] = {}
        for key in MANUAL_SECTIONS:
            heading = ttk.Label(self.manual_frame, font=("Segoe UI", 11, "bold"))
            heading.pack(anchor="w", pady=(8, 0))
            body = ttk.Label(
                self.manual_frame, wraplength=760, justify="left", font=("Segoe UI", 10)
            )
            body.pack(anchor="w")
            self.manual_sections[key] = (heading, body)
        self._views["manual"] = self.manual_frame

    def _retranslate(self) -> None:
        """Push the current language into every control the shell owns."""

        if self._root is None:
            return
        self._root.title(self._t.t("app.title"))
        self.brand_label.configure(text=self._t.t("app.title"))
        self.subtitle_label.configure(text=self._t.t("app.subtitle"))
        self.language_label.configure(text=self._t.t("app.language"))
        for key, button in self._nav_buttons.items():
            button.configure(text=self._t.t(key))
        self._refresh_status_label()
        self.manual_title.configure(text=self._t.t("manual.title"))
        self.manual_intro.configure(text=self._t.t("manual.intro"))
        for key, (heading, body) in self.manual_sections.items():
            heading.configure(text=self._t.t(key))
            body.configure(text=MANUAL_BODY[key][self._t.language])
        if self._gate is not None:
            self._gate.retranslate()

    # ------------------------------------------------------------------------- status
    def connect_api(self) -> bool:
        """Check the core and update the status line. The token stays untranslated."""

        health = self._client.check_health()
        self._core_connected = health.core_status == "connected"
        self._refresh_status_label()
        return self._core_connected

    def _refresh_status_label(self) -> None:
        label = getattr(self, "lbl_sidebar_status", None)
        if label is None:
            return
        state = (
            self._t.t("app.status.connected")
            if self._core_connected
            else self._t.t("app.status.disconnected")
        )
        label.configure(text=f"{self._t.t('app.status.core')}: {state}")

    def show_view(self, view_name: str) -> None:
        if view_name not in self._views:
            return
        for name, view in self._views.items():
            if name == view_name:
                view.pack(fill="both", expand=True)
                refresh = getattr(view, "refresh", None)
                if callable(refresh):
                    refresh()
            else:
                view.pack_forget()
        self._current_view_name = view_name

    # -------------------------------------------------------------------------- flow
    def start(self) -> None:
        """Open the program: introduction, then access, then the shell."""

        self.initialize()
        self.connect_api()

        if self._headless:
            self.show_view("forge")
            return

        if self._show_intro:
            self._intro = ForgeIntro(
                self._root, self._t, self._after_intro, duration=self._intro_duration
            )
            self._intro.start()
            self._pump()
            return

        self._after_intro()
        self._pump()

    def _after_intro(self) -> None:
        self._gate = RegistrationGate(self._root, self._t, self._accounts, self._enter)
        self.show_view("forge")

    def _pump(self) -> None:
        """Run the window until it is closed, tolerating a closed root."""

        if self._root is None:
            return
        try:
            self._root.mainloop()
        except tk.TclError:
            pass

    def _enter(self, email: str) -> None:
        """Called by the access window once somebody is allowed in."""

        self._signed_in_as = email
        if self._gate is not None:
            self._gate.destroy()
            self._gate = None

    def shutdown(self) -> None:
        self._is_running = False
        if self._gate is not None:
            self._gate.destroy()
            self._gate = None
        if self._root is not None:
            try:
                self._root.destroy()
            except Exception:  # noqa: BLE001 - shutdown must not raise
                pass
            self._root = None


__all__ = [
    "DEVELOPER_EMAIL",
    "ForgeDesktopApp",
    "LocalCodeDelivery",
    "MANUAL_BODY",
    "MANUAL_SECTIONS",
    "RegistrationGate",
]
