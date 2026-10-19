"""Offline tests for ``tools/forge_telegram_digest.py``.

Every fixture here is synthetic: no real chat export, no credential, no socket.
The tool is loaded by path because ``tools/`` is not an importable package, the
same way the launcher tests load ``tools/forge_launch.py``.

The provider tests never dial anything.  One simulates "no provider enabled" by
patching the tool's settings loader with an ordinary ``RuntimeSettings`` object
(``.env`` is never opened), another simulates a missing key through the tool's
presence-check seam, and one injects a fake provider object to prove the render
path without a network call.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from app.config.settings import RuntimeSettings

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "forge_telegram_digest", ROOT / "tools" / "forge_telegram_digest.py"
)
digest = importlib.util.module_from_spec(SPEC)
sys.modules["forge_telegram_digest"] = digest
SPEC.loader.exec_module(digest)

PROVIDER_KEY_VAR = "OPENROUTER_API_KEY"


def _messages() -> list[dict]:
    """One message of each interesting kind: real text, service, sticker, GIF."""

    return [
        {
            "id": 1,
            "type": "message",
            "date": "2026-10-01T10:00:00",
            "from": "Анна",
            "from_id": "user1",
            "text": "Привет, начинаем обсуждение релиза?",
            "media_type": "",
        },
        {
            "id": 2,
            "type": "message",
            "date": "2026-10-01T10:05:00",
            "from": "Борис",
            "from_id": "user2",
            "text": "Да, решили выпускать релиз в пятницу.",
            "media_type": "",
        },
        {
            "id": 3,
            "type": "message",
            "date": "2026-10-01T10:10:00",
            "from": "Анна",
            "from_id": "user1",
            "text": "Тогда подготовлю план тестирования.",
            "media_type": "",
        },
        {
            "id": 4,
            "type": "service",
            "date": "2026-10-01T10:11:00",
            "actor": "Анна",
            "actor_id": "user1",
            "action": "add_members",
            "text": "Анна добавила Бориса",
        },
        {
            "id": 5,
            "type": "message",
            "date": "2026-10-01T10:12:00",
            "from": "Борис",
            "from_id": "user2",
            "text": "",
            "media_type": "sticker",
            "sticker_emoji": "😀",
        },
        {
            "id": 6,
            "type": "message",
            "date": "2026-10-01T10:13:00",
            "from": "Борис",
            "from_id": "user2",
            "text": "",
            "media_type": "animation",
        },
        {
            "id": 7,
            "type": "message",
            "date": "2026-10-02T12:00:00",
            "from": "Виктор",
            "from_id": "user3",
            "text": "А бюджет согласован?",
            "media_type": "",
        },
        {
            "id": 8,
            "type": "message",
            "date": "2026-10-02T12:05:00",
            "from": "Анна",
            "from_id": "user1",
            "text": "Ссылка на документ: https://example.com/plan",
            "media_type": "",
        },
    ]


def _run(*arguments: str) -> tuple[int, str, str]:
    """Call the CLI in-process and capture its three observable results."""

    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        code = digest.main(list(arguments))
    return code, stdout.getvalue(), stderr.getvalue()


def _section(document: str, title: str) -> str:
    marker = f"## {title}"
    start = document.index(marker)
    rest = document[start + len(marker) :]
    end = rest.find("\n## ")
    return rest if end == -1 else rest[:end]


class TelegramDigestParsingTests(unittest.TestCase):
    """JSON export parsing, noise filtering and section contents."""

    def setUp(self) -> None:
        self._scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self._scratch.cleanup)
        self.root = Path(self._scratch.name)
        self.export = self.root / "result.json"
        self.export.write_text(
            json.dumps(
                {"name": "Тестовая группа", "type": "private_group", "messages": _messages()},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def test_01_json_export_filters_noise_and_keeps_real_messages(self) -> None:
        code, out, err = _run("--export", str(self.export), "--engine", "local")

        self.assertEqual(code, 0, err)
        self.assertIn("обсуждение релиза", out)
        self.assertIn("решили выпускать", out)
        self.assertIn("бюджет согласован", out)
        # Noise must not reach the digest.
        self.assertNotIn("Анна добавила", out)
        self.assertNotIn("😀", out)
        self.assertNotIn("sticker", out.lower())
        self.assertNotIn("animation", out.lower())
        # Only the five substantive messages are counted.
        self.assertIn("Сообщений: 5", out)
        self.assertIn("Пропущено (служебные и шум): 3", out)
        for heading in ("Сводка", "Решения", "Кто что говорил", "Открытые вопросы", "Статистика"):
            self.assertIn(f"## {heading}", out)
        self.assertIn("локальная выжимка (без модели)", out.lower())

    def test_02_decision_message_is_listed_under_decisions(self) -> None:
        code, out, _ = _run("--export", str(self.export), "--engine", "local")

        self.assertEqual(code, 0)
        decisions = _section(out, "Решения")
        self.assertIn("решили выпускать релиз", decisions)
        self.assertIn("Борис", decisions)

    def test_03_question_messages_are_listed_under_open_questions(self) -> None:
        code, out, _ = _run("--export", str(self.export), "--engine", "local")

        self.assertEqual(code, 0)
        questions = _section(out, "Открытые вопросы")
        self.assertIn("бюджет согласован?", questions)
        self.assertIn("обсуждение релиза?", questions)
        self.assertEqual(questions.count("«"), 2, questions)

    def test_04_participant_ranking_is_correct(self) -> None:
        code, out, _ = _run("--export", str(self.export), "--engine", "local")

        self.assertEqual(code, 0)
        speakers = _section(out, "Кто что говорил")
        lines = [line for line in speakers.splitlines() if line.strip().startswith("-")]
        self.assertEqual(len(lines), 3)
        self.assertIn("**Анна**", lines[0])
        self.assertIn("3 сообщения", lines[0])
        self.assertIn("**Борис**", lines[1])
        self.assertIn("**Виктор**", lines[2])

    def test_05_directory_with_result_json_is_accepted(self) -> None:
        code, out, err = _run("--export", str(self.root), "--engine", "local")

        self.assertEqual(code, 0, err)
        self.assertIn("обсуждение релиза", out)
        self.assertIn("тестовая группа", out.lower())

    def test_06_unknown_json_shape_exits_2(self) -> None:
        self.export.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
        code, _, err = _run("--export", str(self.export), "--engine", "local")

        self.assertEqual(code, 2)
        self.assertIn("формат", err.lower())


class TelegramDigestFilterTests(unittest.TestCase):
    """``--since``, ``--limit`` and malformed input."""

    def setUp(self) -> None:
        self._scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self._scratch.cleanup)
        self.root = Path(self._scratch.name)

    def _write_json(self, messages: list[dict]) -> Path:
        path = self.root / "result.json"
        path.write_text(
            json.dumps({"name": "Фильтры", "messages": messages}, ensure_ascii=False),
            encoding="utf-8",
        )
        return path

    def test_07_since_filters_by_date(self) -> None:
        export = self._write_json(_messages())
        code, out, err = _run(
            "--export", str(export), "--since", "2026-10-02", "--engine", "local"
        )

        self.assertEqual(code, 0, err)
        self.assertIn("бюджет согласован", out)
        self.assertNotIn("решили выпускать", out)
        self.assertNotIn("обсуждение релиза", out)
        self.assertIn("Сообщений: 2", out)
        self.assertIn("Период: 02.10.2026", out)

    def test_08_limit_keeps_only_the_last_n_messages(self) -> None:
        export = self._write_json(_messages())
        code, out, err = _run(
            "--export", str(export), "--limit", "2", "--engine", "local"
        )

        self.assertEqual(code, 0, err)
        self.assertIn("Сообщений: 2", out)
        self.assertIn("бюджет согласован", out)
        self.assertNotIn("решили выпускать", out)

    def test_09_missing_export_exits_2_with_a_message(self) -> None:
        code, out, err = _run(
            "--export", str(self.root / "no-such-result.json"), "--engine", "local"
        )

        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("не найден", err)

    def test_10_malformed_json_exits_2_with_a_message(self) -> None:
        broken = self.root / "result.json"
        broken.write_text("{ this is not json", encoding="utf-8")
        code, _, err = _run("--export", str(broken), "--engine", "local")

        self.assertEqual(code, 2)
        self.assertIn("JSON", err)

    def test_11_local_engine_is_deterministic_across_runs(self) -> None:
        export = self._write_json(_messages())
        first_code, first, _ = _run("--export", str(export), "--engine", "local")
        second_code, second, _ = _run("--export", str(export), "--engine", "local")

        self.assertEqual((first_code, second_code), (0, 0))
        self.assertEqual(first, second)


class TelegramDigestTextAndHtmlTests(unittest.TestCase):
    """Plain-text and HTML fallbacks work without Telegram JSON."""

    def setUp(self) -> None:
        self._scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self._scratch.cleanup)
        self.root = Path(self._scratch.name)

    def test_12_plain_text_transcript_is_supported(self) -> None:
        transcript = self.root / "chat.txt"
        transcript.write_text(
            "[01.10.2026 10:00] Анна: Привет, обсудим релиз?\n"
            "[01.10.2026 10:05] Борис: Решили выпустить в пятницу.\n"
            "Продолжение мысли без заголовка.\n",
            encoding="utf-8",
        )
        code, out, err = _run("--export", str(transcript), "--engine", "local")

        self.assertEqual(code, 0, err)
        self.assertIn("обсудим релиз", out)
        self.assertIn("решили выпустить", _section(out, "Решения").lower())
        self.assertIn("продолжение мысли", out.lower())

    def test_13_html_export_is_supported(self) -> None:
        html_path = self.root / "messages.html"
        html_path.write_text(
            """
            <html><body>
            <div class="message default clearfix" id="message1">
              <div class="pull_right date details" title="01.10.2026 10:00:00 UTC+03:00">10:00</div>
              <div class="from_name">Анна</div>
              <div class="text">Привет, обсудим релиз?</div>
            </div>
            <div class="message service" id="message2">
              <div class="body details">Анна добавила Бориса</div>
            </div>
            <div class="message default clearfix" id="message3">
              <div class="pull_right date details" title="01.10.2026 10:05:00 UTC+03:00">10:05</div>
              <div class="from_name">Борис</div>
              <div class="text">Решили выпустить в пятницу.</div>
            </div>
            </body></html>
            """,
            encoding="utf-8",
        )
        code, out, err = _run("--export", str(html_path), "--engine", "local")

        self.assertEqual(code, 0, err)
        self.assertIn("обсудим релиз", out)
        self.assertIn("решили выпустить", _section(out, "Решения").lower())
        self.assertNotIn("Анна добавила", out)


class TelegramDigestProviderTests(unittest.TestCase):
    """Provider selection and the provider render path, exercised offline."""

    def setUp(self) -> None:
        self._scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self._scratch.cleanup)
        self.root = Path(self._scratch.name)
        self.export = self.root / "result.json"
        self.export.write_text(
            json.dumps({"name": "Провайдер", "messages": _messages()}, ensure_ascii=False),
            encoding="utf-8",
        )

    def test_14_provider_requested_without_enabled_provider_exits_3(self) -> None:
        settings = RuntimeSettings(
            enabled_providers=("mock",), allow_paid_providers=False
        )
        with mock.patch.object(digest, "_load_runtime_settings", return_value=settings):
            code, out, err = _run(
                "--export", str(self.export), "--engine", "provider"
            )

        self.assertEqual(code, 3)
        self.assertEqual(out, "")
        self.assertIn("провайдер", err.lower())
        self.assertIn("--engine local", err)

    def test_15_enabled_provider_without_key_exits_3(self) -> None:
        settings = RuntimeSettings(
            default_provider="openrouter",
            enabled_providers=("openrouter",),
            allow_paid_providers=False,
        )
        with mock.patch.object(
            digest, "_load_runtime_settings", return_value=settings
        ), mock.patch.object(digest, "_key_configured", return_value=False):
            code, _, err = _run("--export", str(self.export), "--engine", "provider")

        self.assertEqual(code, 3)
        self.assertIn(PROVIDER_KEY_VAR, err)

    def test_16_auto_engine_falls_back_to_local_when_no_provider(self) -> None:
        settings = RuntimeSettings(enabled_providers=("mock",))
        with mock.patch.object(digest, "_load_runtime_settings", return_value=settings):
            code, out, err = _run("--export", str(self.export), "--engine", "auto")

        self.assertEqual(code, 0, err)
        self.assertIn("локальная выжимка (без модели)", out.lower())
        self.assertIn("провайдер не использован", out.lower())

    def test_17_provider_output_is_rendered_with_provenance(self) -> None:
        captured: dict = {}

        class FakeProvider:
            provider_name = "fake"
            model_name = "fake-1"

            def generate(self, request):
                captured["prompt"] = request.prompt
                return SimpleNamespace(
                    output="## Сводка\n- Модель увидела релиз и бюджет.",
                    provider_name="fake",
                    model_name="fake-1",
                )

        choice = digest.ProviderChoice(
            provider=FakeProvider(), provider_name="fake", model_name="fake-1"
        )
        settings = RuntimeSettings(enabled_providers=("openrouter",))
        with mock.patch.object(
            digest, "_load_runtime_settings", return_value=settings
        ), mock.patch.object(digest, "_choose_provider", return_value=(choice, [])):
            code, out, err = _run(
                "--export", str(self.export), "--engine", "provider"
            )

        self.assertEqual(code, 0, err)
        self.assertIn("Модель увидела релиз и бюджет", out)
        self.assertIn("Выжимка модели: fake / fake-1", out)
        self.assertIn("решили выпускать релиз", captured["prompt"])
        self.assertIn("Борис", captured["prompt"])


if __name__ == "__main__":
    unittest.main()
