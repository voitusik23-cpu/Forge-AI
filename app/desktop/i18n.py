"""Interface languages for the Forge desktop program.

Two languages are shipped, English and Russian, and every visible string is looked up
here rather than written into a widget. ``Translator.t`` returns the key itself when a
translation is missing, so a gap shows up as ``register.title`` on screen instead of
silently falling back to another language: a visibly missing label is a bug report, a
silently English label in a Russian interface is a lie about being translated.

``missing_keys`` exists so the test suite can assert that neither language lags behind.
"""

from __future__ import annotations

from typing import Dict, Iterable, Mapping, Sequence, Tuple

FALLBACK_LANGUAGE = "en"

LANGUAGE_NAMES: Mapping[str, str] = {
    "en": "English",
    "ru": "Русский",
}

TRANSLATIONS: Mapping[str, Mapping[str, str]] = {
    "en": {
        # application shell
        "app.title": "FORGE — the Forge",
        "app.subtitle": "AI-native engineering environment",
        "app.language": "Language",
        "app.lang.switch": "Switch language",
        "app.status.core": "Core",
        # Status tokens are NOT translated, on purpose: they are the same kind of literal
        # as PASSED / FAILED / UNKNOWN elsewhere in Forge. The interface translates the
        # word around them ("Ядро: CONNECTED"), never the token itself.
        "app.status.connected": "CONNECTED",
        "app.status.disconnected": "DISCONNECTED",
        # navigation
        "nav.forge": "Forge",
        "nav.projects": "Projects",
        "nav.agents": "Agents",
        "nav.runs": "Runs",
        "nav.costs": "Costs",
        "nav.settings": "Settings",
        "nav.manual": "Manual",
        # widget context menu
        "widget.refresh": "Refresh",
        "widget.open": "Open Forge",
        "widget.close": "Close widget",
        # generic actions
        "action.refresh": "Refresh",
        "action.run_task": "Run task in core",
        "action.close": "Close",
        "action.back": "Back",
        "action.continue": "Continue",
        "action.skip": "Skip intro",
        # introduction animation
        "intro.skipping": "Click anywhere or press Esc to skip",
        "intro.caption.stars": "Deep space, far from the forge",
        "intro.caption.ship": "A courier ship is inbound",
        "intro.caption.inside": "Inside: the forge never sleeps",
        "intro.caption.forge": "Two smiths, one anvil, a blade taking shape",
        # registration
        "register.title": "Create your account",
        "register.subtitle": "The forge needs to know who is at the bellows.",
        "register.email": "Email",
        "register.email.hint": "you@example.com",
        "register.send_code": "Send confirmation code",
        "register.code": "Confirmation code",
        "register.code.hint": "6 digits",
        "register.confirm": "Confirm and enter",
        "register.dev": "I am the developer — enter without registering",
        "register.dev.hint": "Developer access skips confirmation entirely.",
        "register.code.sent": "Code generated. Delivery: {delivery}",
        "register.code.local_notice": (
            "Local mode: no mail server is configured, so the code is shown here and "
            "never leaves this machine. Configure SMTP in Settings to send real email."
        ),
        "register.code.emailed": "A confirmation code was sent to {email}.",
        "register.error.email": "Enter an email address first.",
        "register.error.email_invalid": "That does not look like an email address.",
        "register.error.code": "Enter the 6-digit code you received.",
        "register.error.code_wrong": "That code is not correct.",
        "register.error.code_expired": "That code has expired. Request a new one.",
        "register.welcome": "Welcome, {email}.",
        # manual
        "manual.title": "How Forge works",
        "manual.intro": (
            "Forge turns a goal into specified, executed and verified work. Nothing runs "
            "without an explicit authorization, and a report is never treated as proof."
        ),
        "manual.section.start": "Starting the program",
        "manual.section.register": "Registration and access",
        "manual.section.keys": "Keys and providers",
        "manual.section.run": "Running a task",
        "manual.section.status": "Reading the statuses",
        "manual.section.files": "Where the files live",
        # settings / keys
        "settings.title": "Settings",
        "settings.providers": "Providers and keys",
        "settings.keys.hint": (
            "Keys are read from the environment. Never paste a key into a file that is "
            "committed: .env is ignored by git, .env.example documents the names."
        ),
    },
    "ru": {
        "app.title": "FORGE — «Кузница»",
        "app.subtitle": "Инженерная среда, работающая с ИИ",
        "app.language": "Язык",
        "app.lang.switch": "Переключить язык",
        "app.status.core": "Ядро",
        "app.status.connected": "CONNECTED",
        "app.status.disconnected": "DISCONNECTED",
        "nav.forge": "Кузница",
        "nav.projects": "Проекты",
        "nav.agents": "Агенты",
        "nav.runs": "Запуски",
        "nav.costs": "Затраты",
        "nav.settings": "Настройки",
        "nav.manual": "Руководство",
        # widget context menu
        "widget.refresh": "Обновить",
        "widget.open": "Открыть Forge",
        "widget.close": "Закрыть виджет",
        "action.refresh": "Обновить",
        "action.run_task": "Запустить задачу в ядре",
        "action.close": "Закрыть",
        "action.back": "Назад",
        "action.continue": "Продолжить",
        "action.skip": "Пропустить заставку",
        "intro.skipping": "Щёлкните в любом месте или нажмите Esc, чтобы пропустить",
        "intro.caption.stars": "Глубокий космос, далеко от кузницы",
        "intro.caption.ship": "Курьерский корабль на подходе",
        "intro.caption.inside": "Внутри: кузница не спит",
        "intro.caption.forge": "Двое кузнецов, одна наковальня, клинок обретает форму",
        "register.title": "Создайте учётную запись",
        "register.subtitle": "Кузнице нужно знать, кто стоит у мехов.",
        "register.email": "Почта",
        "register.email.hint": "you@example.com",
        "register.send_code": "Отправить код подтверждения",
        "register.code": "Код подтверждения",
        "register.code.hint": "6 цифр",
        "register.confirm": "Подтвердить и войти",
        "register.dev": "Я разработчик — войти без регистрации",
        "register.dev.hint": "Доступ разработчика полностью пропускает подтверждение.",
        "register.code.sent": "Код создан. Доставка: {delivery}",
        "register.code.local_notice": (
            "Локальный режим: почтовый сервер не настроен, поэтому код показан здесь и "
            "никуда не уходит. Чтобы отправлять настоящие письма, укажите SMTP в настройках."
        ),
        "register.code.emailed": "Код подтверждения отправлен на {email}.",
        "register.error.email": "Сначала укажите адрес почты.",
        "register.error.email_invalid": "Это не похоже на адрес почты.",
        "register.error.code": "Введите 6-значный код, который вы получили.",
        "register.error.code_wrong": "Код неверный.",
        "register.error.code_expired": "Срок действия кода истёк. Запросите новый.",
        "register.welcome": "Добро пожаловать, {email}.",
        "manual.title": "Как работает Forge",
        "manual.intro": (
            "Forge превращает цель в описанную, выполненную и проверенную работу. Ничто "
            "не запускается без явной авторизации, а отчёт никогда не считается доказательством."
        ),
        "manual.section.start": "Запуск программы",
        "manual.section.register": "Регистрация и доступ",
        "manual.section.keys": "Ключи и провайдеры",
        "manual.section.run": "Как запустить задачу",
        "manual.section.status": "Как читать статусы",
        "manual.section.files": "Где лежат файлы",
        "settings.title": "Настройки",
        "settings.providers": "Провайдеры и ключи",
        "settings.keys.hint": (
            "Ключи читаются из переменных окружения. Никогда не вставляйте ключ в файл, "
            "который попадает в git: .env в игнорировании, а .env.example описывает имена."
        ),
    },
}


def available_languages() -> Sequence[Tuple[str, str]]:
    """``(code, native name)`` for every shipped language, in a stable order."""

    return tuple((code, LANGUAGE_NAMES.get(code, code)) for code in ("ru", "en"))


def missing_keys(language: str) -> Tuple[str, ...]:
    """Keys present in the reference language but absent from ``language``."""

    reference = set(TRANSLATIONS[FALLBACK_LANGUAGE])
    return tuple(sorted(reference - set(TRANSLATIONS.get(language, {}))))


def extra_keys(language: str) -> Tuple[str, ...]:
    """Keys present in ``language`` but not in the reference language."""

    reference = set(TRANSLATIONS[FALLBACK_LANGUAGE])
    return tuple(sorted(set(TRANSLATIONS.get(language, {})) - reference))


class Translator:
    """Holds the current language and looks strings up in it."""

    def __init__(self, language: str = "ru") -> None:
        self._language = FALLBACK_LANGUAGE
        self.set_language(language)

    @property
    def language(self) -> str:
        return self._language

    @property
    def language_name(self) -> str:
        return LANGUAGE_NAMES.get(self._language, self._language)

    def set_language(self, language: str) -> None:
        """Switch language. An unknown code falls back instead of raising."""

        code = str(language or "").strip().lower()
        if code in TRANSLATIONS:
            self._language = code
        elif code:
            self._language = FALLBACK_LANGUAGE

    def toggle(self) -> str:
        """Switch to the other shipped language and return the new code."""

        self.set_language("en" if self._language == "ru" else "ru")
        return self._language

    def t(self, key: str, **values: object) -> str:
        """Translate ``key``; a missing key is returned verbatim, never guessed."""

        table = TRANSLATIONS.get(self._language, {})
        text = table.get(key)
        if text is None:
            text = TRANSLATIONS[FALLBACK_LANGUAGE].get(key)
        if text is None:
            return key
        if values:
            try:
                return text.format(**values)
            except (KeyError, IndexError):
                # A malformed translation must not crash a window; show the raw text.
                return text
        return text

    def items(self, prefix: str) -> Dict[str, str]:
        """Every string whose key starts with ``prefix``, for building a menu or a page."""

        table = TRANSLATIONS.get(self._language, TRANSLATIONS[FALLBACK_LANGUAGE])
        return {key: value for key, value in table.items() if key.startswith(prefix)}


def all_keys() -> Iterable[str]:
    return tuple(sorted(TRANSLATIONS[FALLBACK_LANGUAGE]))
