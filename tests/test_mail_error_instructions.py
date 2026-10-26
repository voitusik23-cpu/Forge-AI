"""The generated «Почта АИ» says *what to do* about a mailbox failure, not only that it failed.

Why this file exists
--------------------
The owner's app was honest and useless at the same time: ``voitkus23@gmail.com — ошибка на
шаге «чтение»``. That sentence tells him nothing he can act on, and the instruction that
would help him (an app password, IMAP switched on, no connection) is exactly what the app
never said.

The mapping lives in the **generated project** -- ``app.py`` under
``.forge/projects-visible/`` -- because that app is what he runs from his desktop shortcut.
``.forge/`` is gitignored, so this test loads the project from disk when it is there and
skips honestly when it is not: it never pretends to have checked a file it did not read.

What is asserted, offline (no IMAP connection, no password, nothing printed):

1. every failure the app can meet maps to the documented Russian instruction, word for
   word -- the same four branches the owner asked for;
2. an unknown failure keeps the raw exception on one line and names the log file;
3. the mapping is a pure function: the same input twice gives the same answer, and the
   answer has no side effects to observe;
4. the route that reports the failure carries the instruction to the page, and the page
   prints it **next to the mailbox** under a one-line «Как исправить».
"""

from __future__ import annotations

import importlib.util
import pathlib
import unittest

REPOSITORY = pathlib.Path(__file__).resolve().parents[1]
VISIBLE_PROJECTS = REPOSITORY / ".forge" / "projects-visible"

#: The wording the owner asked for, kept literally here so a silent reword fails the test.
APP_PASSWORD_TEXT = (
    "Gmail не принял пароль. Обычный пароль не подходит — включите двухфакторную "
    "защиту и создайте **пароль приложения** (Google → Безопасность → Пароли "
    "приложений), затем вставьте его здесь."
)
IMAP_DISABLED_TEXT = (
    "Включите IMAP в настройках Gmail: Настройки → Пересылка и POP/IMAP → "
    "Включить IMAP."
)


def _mail_project() -> pathlib.Path | None:
    """The generated mail app's folder, when this machine has one."""

    if not VISIBLE_PROJECTS.is_dir():
        return None
    for candidate in sorted(VISIBLE_PROJECTS.iterdir()):
        app_py = candidate / "app.py"
        if not app_py.is_file():
            continue
        try:
            source = app_py.read_text(encoding="utf-8")
        except OSError:
            continue
        if "explain_imap_error" in source:
            return candidate
    return None


def _load(path: pathlib.Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise unittest.SkipTest(f"{path} cannot be imported")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PROJECT = _mail_project()
SKIP_REASON = (
    "the generated «Почта АИ» project is not on this machine "
    "(.forge/projects-visible is gitignored); nothing was checked"
)


@unittest.skipIf(PROJECT is None, SKIP_REASON)
class _Mapping(unittest.TestCase):
    """The four documented branches, the fallback, and the shape of every answer."""

    @classmethod
    def setUpClass(cls) -> None:
        assert PROJECT is not None
        cls.app = _load(PROJECT / "app.py", "pochta_app_error_instructions")

    def explain(self, text: str, step: str = "чтение", **kwargs):
        return self.app.explain_imap_error(Exception(text), step, **kwargs)

    # ------------------------------------------------------- the four branches
    def test_the_app_password_branch(self) -> None:
        """Google's four ways of saying «this is not an app password» are one instruction."""

        for text in (
            "b'[AUTHENTICATIONFAILED] Invalid credentials (Failure)'",
            "LOGIN failed",
            "invalid credentials",
            "Application-specific password required: https://support.google.com/mail/?p=WantAuthError",
            # Gmail's own wording when the password login is blocked:
            "b'[ALERT] Please log in via your web browser: https://support.google.com/mail/accounts/answer/78754 (Failure)'",
        ):
            with self.subTest(text=text):
                answer = self.explain(text)
                self.assertEqual(answer["error"], APP_PASSWORD_TEXT)
                # ...and it is the module's own constant, not a copy that can drift.
                self.assertEqual(answer["error"], self.app.IMAP_APP_PASSWORD)
                self.assertIn("пароль приложения", answer["error"])
                self.assertNotEqual(answer["error"], "ошибка на шаге «чтение»")

    def test_the_imap_disabled_branch(self) -> None:
        for text in ("IMAP access is disabled", "[IMAP] IMAP is disabled for this account"):
            with self.subTest(text=text):
                answer = self.explain(text)
                self.assertEqual(answer["error"], IMAP_DISABLED_TEXT)
                self.assertEqual(answer["error"], self.app.IMAP_DISABLED)
                self.assertIn("Включить IMAP", answer["error"])

    def test_the_connection_branch_names_the_server(self) -> None:
        for text in (
            "timed out",
            "socket.timeout: The read operation timed out",
            "socket.gaierror: [Errno 11001] getaddrinfo failed",
        ):
            with self.subTest(text=text):
                answer = self.explain(text, host="imap.gmail.com")
                self.assertEqual(
                    answer["error"], "Нет связи с imap.gmail.com — проверьте интернет/VPN."
                )
                self.assertIn("интернет/VPN", answer["error"])

    def test_an_unknown_failure_keeps_the_raw_text_and_the_log(self) -> None:
        """Anything else keeps the exception itself, on one line, plus where the log is."""

        raw = "b'[IMAP] Something unexpected happened\\nsecond line'"
        answer = self.explain(raw, "чтение")
        error = answer["error"]
        self.assertIn("Something unexpected happened", error)
        self.assertIn("second line", error)
        self.assertNotIn("\n", error)
        self.assertIn(self.app.LAUNCH_LOG, error)
        self.assertIn("Журнал:", error)
        self.assertEqual(answer["how_to_fix"], self.app.IMAP_UNKNOWN_FIX)

    # ------------------------------------------------------------ the shape, purity
    def test_every_answer_is_one_readable_line_and_two_fields(self) -> None:
        for text in (
            "AUTHENTICATIONFAILED",
            "IMAP access is disabled",
            "timed out",
            "something else entirely",
        ):
            with self.subTest(text=text):
                answer = self.explain(text)
                self.assertEqual(sorted(answer), ["error", "how_to_fix"])
                for key in ("error", "how_to_fix"):
                    self.assertTrue(str(answer[key]).strip(), key)
                    self.assertNotIn("\n", str(answer[key]), key)
                # The old useless sentence may survive only as the «anything else» lead-in.
                self.assertFalse(
                    str(answer["error"]).startswith("ошибка на шаге") and "Журнал" not in str(answer["error"]),
                    answer["error"],
                )

    def test_the_mapping_is_pure(self) -> None:
        first = self.explain("AUTHENTICATIONFAILED")
        second = self.explain("AUTHENTICATIONFAILED")
        self.assertEqual(first, second)
        self.assertIsNot(first, second)
        # A dict answer is returned, so a caller cannot mutate the module's constants.
        first["error"] = "изменено"
        self.assertEqual(self.explain("AUTHENTICATIONFAILED")["error"], APP_PASSWORD_TEXT)

    def test_friendly_error_is_the_same_sentence(self) -> None:
        """The list of errors and the per-mailbox block quote one and the same text."""

        self.assertEqual(
            self.app.friendly_error("чтение", Exception("AUTHENTICATIONFAILED")),
            APP_PASSWORD_TEXT,
        )


@unittest.skipIf(PROJECT is None, SKIP_REASON)
class _ThePageSaysItToo(unittest.TestCase):
    """The instruction reaches the page and is printed next to the mailbox.

    This is a source-level assertion on the generated project (it has no test runner of its
    own): the HTTP answer carries ``how_to_fix``, and the page renders it under the error,
    beside the mailbox itself. The live screenshot in ``.forge/screenshots/`` is the other
    half of that evidence.
    """

    @classmethod
    def setUpClass(cls) -> None:
        assert PROJECT is not None
        cls.app_py = (PROJECT / "app.py").read_text(encoding="utf-8")
        cls.app_js = (PROJECT / "app.js").read_text(encoding="utf-8")

    def test_the_check_route_carries_the_instruction(self) -> None:
        self.assertIn('detail = explain_imap_error(e, "чтение", b.get("host"))', self.app_py)
        self.assertIn('"how_to_fix": detail["how_to_fix"]', self.app_py)

    def test_the_page_renders_it_beside_the_mailbox(self) -> None:
        self.assertIn("errorsByMailbox", self.app_js)
        self.assertIn('el("div", "mailbox-problem")', self.app_js)
        self.assertIn("Как исправить:", self.app_js)
        # The field is read from the answer, not invented by the page.
        self.assertIn("how_to_fix", self.app_js)


class _TheProjectMayBeAbsent(unittest.TestCase):
    """A document, in code, of why the two classes above can skip.

    When the project is absent the skip message must say so plainly -- a skipped check is
    not a passed one, and the suite's output has to make that visible.
    """

    def test_absence_is_skipped_with_a_reason(self) -> None:
        if PROJECT is not None:
            self.skipTest("the generated project is present on this machine")
        self.assertIn("nothing was checked", SKIP_REASON)


if __name__ == "__main__":
    unittest.main()
