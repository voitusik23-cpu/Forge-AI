"""Automatic model routing: an honest, deterministic complexity classifier.

The owner's complaint was «где мне выбрать модель которая будет работать? ... Там у меня
даже нет возможности что-то выбирать.» The answer this suite pins is not a dropdown: a
request that names no provider/model is classified by explicit, testable signals and the
chosen pair is published with the reasons for it.

What is asserted here, and why each assertion is worth having:

* the classification of ~10 realistic tasks, including the owner's mail-centre idea, with
  the *reasons* — never a bare tier;
* the tier mapping (SIMPLE→FREE, MEDIUM→CHEAP, COMPLEX→PAID) and the paid refusal when the
  deployment has not enabled paid providers;
* that an explicit provider+model always wins (the existing manual path);
* determinism: the same input produces the same decision, byte for byte;
* the run path really uses the decision (an unnamed request dispatches the chosen pair)
  and publishes it, and ``estimate: true`` dispatches nothing.

No provider is called anywhere in this file: the classifier opens no socket, and the one
dispatch test uses a fake provider registered under the real provider's name.
"""

from __future__ import annotations

import pathlib
import tempfile
import unittest

from app.agent_runtime.routing import (
    CHEAP,
    COMPLEX,
    COMPLEXITY_LEVELS,
    DEFAULT_MAX_COST_TIER,
    FREE,
    MEDIUM,
    PAID,
    SIMPLE,
    TIER_FOR_COMPLEXITY,
    TIERS,
    assess_complexity,
    catalogue_rows,
    choose_model,
    normalise_cost_tier,
)
from app.config.app_settings import AppSettingsStore


# --------------------------------------------------------------------------- catalogue
def model_row(
    provider: str,
    model: str,
    tier: str,
    *,
    capabilities=("code", "analysis", "review"),
    context_window: int = 128000,
    input_cost: float | None = None,
    output_cost: float | None = None,
) -> dict:
    """One ``GET /api/models`` row, in the payload's own key names."""

    return {
        "provider_id": provider,
        "model_id": model,
        "cost_tier": tier,
        "capabilities": list(capabilities),
        "context_window": context_window,
        "estimated_cost": input_cost,
        "output_cost_per_1m": output_cost,
    }


def catalogue(*rows, states=None, keyless=("mock",)) -> dict:
    """A catalogue payload with provider states, as the service builds it.

    ``keyless`` names the providers whose ``secret_env_name`` is empty — the one kind of
    provider that needs no account (the offline engine). The live payload reports exactly
    that field, and the chooser reads it to prefer a deterministic route inside a tier.
    """

    providers = {str(row["provider_id"]) for row in rows}
    if states is None:
        states = {provider: "READY" for provider in providers}
    return {
        "models": list(rows),
        "providers": [
            {
                "provider_id": provider,
                "state": state,
                "secret_env_name": "" if provider in keyless else f"{provider.upper()}_KEY",
            }
            for provider, state in sorted(states.items())
        ],
    }


#: A small, realistic deployment: the offline mock, one free gateway row, one cheap row
#: and one paid row. ``default_provider`` is the offline provider, like the repository's
#: own defaults.
FREE_ONLY = catalogue(
    model_row("mock", "mock-v1", FREE),
    model_row("openrouter", "cohere/north-mini-code:free", FREE),
    model_row("deepseek", "deepseek-chat", CHEAP, input_cost=0.14, output_cost=0.28),
    model_row("anthropic", "claude-3-7-sonnet-20250219", PAID, input_cost=3.0, output_cost=15.0),
)

MIXED = catalogue(
    model_row("mock", "mock-v1", FREE),
    model_row("openrouter", "cohere/north-mini-code:free", FREE),
    model_row("deepseek", "deepseek-reasoner", CHEAP, input_cost=0.55, output_cost=2.19),
    model_row("deepseek", "deepseek-chat", CHEAP, input_cost=0.14, output_cost=0.28),
    model_row("anthropic", "claude-3-7-sonnet-20250219", PAID, input_cost=3.0, output_cost=15.0),
    model_row("openrouter", "anthropic/claude-sonnet-5.5", PAID, input_cost=3.0, output_cost=15.0),
)

OFFLINE_SETTINGS = {
    "default_provider": "mock",
    "default_model": "mock",
    "allow_paid_providers": False,
    "enabled_providers": ("mock", "openrouter", "deepseek", "anthropic"),
}

# ------------------------------------------------------------------------- the tasks
#: One-line questions: nothing to plan, nothing to build.
SIMPLE_READ = "прочитай README и скажи в одном предложении, что это за проект"
SIMPLE_COUNT = "посчитай, сколько строк в этом файле"
SIMPLE_DIAGNOSIS = "объясни, почему тест падает с ошибкой KeyError"
#: Real but bounded engineering: a test suite, a refactor, a migration.
TESTS_ONLY = "напиши тесты для модуля импорта"
REFACTOR_WITH_TESTS = (
    "сделай рефакторинг: вынеси разбор аргументов в отдельный модуль и добавь тесты"
)
MIXED_CHANGE = (
    "добавь в проект экспорт отчёта в CSV, миграцию для таблицы в базе данных и тесты"
)
#: A project: a new application with its own storage.
NEW_APP = (
    "создай приложение для заметок: экран списка, экран заметки, поиск, "
    "сохранение в базу данных"
)
#: THE OWNER'S MAIL-CENTRE IDEA: several engineering surfaces and a project, in one
#: sentence. It must land on COMPLEX and the reasons must name the surfaces.
MAIL_CENTRE = (
    "Хочу центр почты: создай приложение, база данных для писем, авторизация, "
    "интеграция с Exchange, тесты и миграция существующих писем"
)
#: A long single paragraph that asks for several things at once and names no scale word.
LONG_REVIEW = (
    "Проверь модуль оплаты и опиши словами, что в нём происходит, "
    "какие ветки кода недостижимы и какие ошибки не обрабатываются никогда. "
    "Ничего не меняй, только отчёт с номерами строк и короткими цитатами. "
    "Отдельно перечисли места, где исключение проглатывается молча, "
    "и укажи, к чему это приводит на практике у пользователя. "
    "В конце дай список из трёх самых важных находок по убыванию риска."
)

#: The ten realistic tasks. The third item is the signal that must appear in the reasons;
#: `None` means "no scale word, the level comes from length and clauses alone".
TASKS = (
    (SIMPLE_READ, SIMPLE, None),
    (SIMPLE_COUNT, SIMPLE, None),
    (SIMPLE_DIAGNOSIS, SIMPLE, None),
    (TESTS_ONLY, MEDIUM, "масштаб"),
    (REFACTOR_WITH_TESTS, MEDIUM, "масштаб"),
    (MIXED_CHANGE, COMPLEX, "масштаб"),
    (NEW_APP, COMPLEX, "создание приложения"),
    (MAIL_CENTRE, COMPLEX, "база данных"),
    (LONG_REVIEW, MEDIUM, "длинная задача"),
)

#: A task with more than three requested features, written as a list.
FEATURE_LIST_TASK = (
    "Добавь в форму регистрации:\n"
    "- проверку длины пароля\n"
    "- подтверждение пароля\n"
    "- показ сложности пароля\n"
    "- блокировку кнопки, пока поля не заполнены\n"
    "- сообщение об ошибке под каждым полем\n"
)


class ComplexityTests(unittest.TestCase):
    """The level and, above all, the reasons."""

    def test_01_the_realistic_tasks_land_on_the_expected_level(self) -> None:
        for text, expected, signal in TASKS:
            with self.subTest(task=text[:40]):
                assessment = assess_complexity(text, "analysis")
                self.assertEqual(
                    assessment.level,
                    expected,
                    (text, assessment.level, assessment.reasons),
                )
                self.assertIn(assessment.level, COMPLEXITY_LEVELS)
                # A level is never published as a bare word.
                self.assertTrue(assessment.reasons, text)
                if signal is not None:
                    self.assertTrue(
                        any(signal in reason for reason in assessment.reasons),
                        f"{signal!r} is missing from {assessment.reasons}",
                    )

    def test_02_the_mail_centre_idea_is_project_shaped(self) -> None:
        assessment = assess_complexity(
            MAIL_CENTRE, "code", write_mode=True
        )
        self.assertEqual(assessment.level, COMPLEX, assessment.reasons)
        joined = " | ".join(assessment.reasons)
        for surface in ("создание приложения", "база данных", "авторизация", "интеграция"):
            self.assertIn(surface, joined)
        # Write mode on a code task is its own, separately named signal.
        self.assertIn("код и запись файлов", joined)

    def test_03_a_simple_task_names_the_absence_of_signals(self) -> None:
        assessment = assess_complexity("скажи в одном слове: работает?", "analysis")
        self.assertEqual(assessment.level, SIMPLE)
        self.assertEqual(
            assessment.reasons, ("простая задача: признаков сложности нет",)
        )

    def test_04_write_mode_only_counts_for_code(self) -> None:
        text = "добавь строку в файл"
        self.assertEqual(
            assess_complexity(text, "code", write_mode=True).level, MEDIUM
        )
        # The same flag on an analysis task is not a scale signal at all.
        self.assertEqual(
            assess_complexity(text, "analysis", write_mode=True).level, SIMPLE
        )

    def test_05_an_existing_project_is_its_own_signal(self) -> None:
        text = "поменяй заголовок на странице"
        self.assertEqual(assess_complexity(text, "code").level, SIMPLE)
        changed = assess_complexity(text, "code", existing_project=True)
        self.assertEqual(changed.level, SIMPLE)  # one point is still a one-liner
        self.assertIn("изменение существующего проекта", changed.reasons)
        # ... and together with write mode it is a change to a codebase.
        combined = assess_complexity(
            text, "code", write_mode=True, existing_project=True
        )
        self.assertEqual(combined.level, MEDIUM, combined.reasons)

    def test_06_more_than_three_requested_features_is_medium(self) -> None:
        assessment = assess_complexity(FEATURE_LIST_TASK, "code", write_mode=True)
        self.assertTrue(
            any("много функций" in reason for reason in assessment.reasons),
            assessment.reasons,
        )
        self.assertIn(assessment.level, (MEDIUM, COMPLEX))

    def test_07_a_confirmed_specification_counts_its_fields(self) -> None:
        specification = (
            "PROJECT SPECIFICATION\n"
            "Goal: заметки\n"
            "Project type: web\n"
            "Users: один владелец\n"
            "Core features: список; поиск; теги\n"
            "Platform: браузер\n"
            "Data storage: sqlite\n"
        )
        assessment = assess_complexity(specification + "\nсделай это", "code")
        self.assertTrue(
            any("подтверждённая спецификация" in reason for reason in assessment.reasons),
            assessment.reasons,
        )

    def test_08_classification_is_deterministic(self) -> None:
        for text, _expected, _signal in TASKS:
            first = assess_complexity(text, "code", write_mode=True)
            second = assess_complexity(text, "code", write_mode=True)
            self.assertEqual(first, second)


class TierPolicyTests(unittest.TestCase):
    """SIMPLE→FREE, MEDIUM→CHEAP, COMPLEX→PAID, and paid only when permitted."""

    def test_01_the_mapping_is_the_published_one(self) -> None:
        self.assertEqual(TIER_FOR_COMPLEXITY, {SIMPLE: FREE, MEDIUM: CHEAP, COMPLEX: PAID})
        self.assertEqual(TIERS, (FREE, CHEAP, PAID))

    def test_02_a_simple_task_lands_on_a_free_model(self) -> None:
        choice = choose_model(
            "скажи в одном слове: работает?",
            "analysis",
            catalogue=MIXED,
            settings=OFFLINE_SETTINGS,
        )
        self.assertEqual(choice.complexity, SIMPLE)
        self.assertEqual(choice.tier, FREE)
        # The configured default provider matches the tier, so it is preferred.
        self.assertEqual(choice.provider, "mock")
        self.assertEqual(choice.estimated_cost, 0.0)
        self.assertTrue(choice.automatic)
        self.assertIn("совпадает с настроенным по умолчанию провайдером mock", choice.reasons)

    def test_03_a_medium_task_lands_on_a_cheap_model(self) -> None:
        choice = choose_model(
            "сделай рефакторинг модуля импорта и добавь тесты",
            "code",
            catalogue=MIXED,
            settings=OFFLINE_SETTINGS,
        )
        self.assertEqual(choice.complexity, MEDIUM)
        self.assertEqual(choice.tier, CHEAP)
        # Within the tier the strongest published row wins (price, then capabilities).
        self.assertEqual(choice.provider, "deepseek")
        self.assertEqual(choice.model, "deepseek-reasoner")
        # The decision carries the chosen row's published rate per 1M input tokens, so a
        # capped choice can name what it will cost instead of saying "unknown".
        self.assertEqual(choice.estimated_cost, 0.55)

    def test_04_paid_is_used_only_for_complex_work_and_only_when_allowed(self) -> None:
        text = MAIL_CENTRE  # the mail-centre idea
        refused = choose_model(
            text, "code", catalogue=MIXED, settings=OFFLINE_SETTINGS
        )
        self.assertEqual(refused.complexity, COMPLEX)
        self.assertNotEqual(refused.tier, PAID)
        self.assertEqual(refused.tier, CHEAP)
        self.assertTrue(
            any("платные модели запрещены" in reason for reason in refused.reasons),
            refused.reasons,
        )

        # With the ceiling explicitly unlimited, paid permission is the only remaining cap.
        allowed = choose_model(
            text,
            "code",
            catalogue=MIXED,
            settings={
                **OFFLINE_SETTINGS,
                "allow_paid_providers": True,
                "max_cost_tier": PAID,
            },
        )
        self.assertEqual(allowed.tier, PAID)
        self.assertIn(allowed.provider, {"anthropic", "openrouter"})
        self.assertEqual(allowed.estimated_cost, 3.0)
        self.assertFalse(allowed.ceiling_limited)

    def test_05_cheap_work_never_reaches_paid_even_when_allowed(self) -> None:
        allowed = choose_model(
            "добавь в README одну строку",
            "code",
            catalogue=MIXED,
            settings={**OFFLINE_SETTINGS, "allow_paid_providers": True},
        )
        self.assertEqual(allowed.tier, FREE)
        self.assertEqual(allowed.provider, "mock")

    def test_06_an_unavailable_provider_is_never_chosen(self) -> None:
        # The cheap row's provider lost its credentials: the chooser must not pick it.
        catalogue_without_cheap = catalogue(
            model_row("mock", "mock-v1", FREE),
            model_row("deepseek", "deepseek-chat", CHEAP),
            states={"mock": "READY", "deepseek": "NO_CREDENTIALS"},
        )
        choice = choose_model(
            "сделай рефакторинг модуля импорта и добавь тесты",
            "code",
            catalogue=catalogue_without_cheap,
            settings=OFFLINE_SETTINGS,
        )
        self.assertEqual(choice.tier, FREE)
        self.assertEqual(choice.provider, "mock")
        self.assertTrue(
            any("в тарифе CHEAP нет доступных моделей" in reason for reason in choice.reasons),
            choice.reasons,
        )

    def test_07_no_routable_model_keeps_the_configured_default(self) -> None:
        empty = catalogue(
            model_row("anthropic", "claude-3-7-sonnet-20250219", PAID),
            states={"anthropic": "DISABLED"},
        )
        choice = choose_model(
            "простая задача", "analysis", catalogue=empty, settings=OFFLINE_SETTINGS
        )
        self.assertEqual(choice.provider, "mock")
        self.assertEqual(choice.model, "")
        self.assertEqual(choice.tier, "")
        self.assertTrue(
            any("нет доступной модели" in reason for reason in choice.reasons),
            choice.reasons,
        )

    def test_08_the_override_always_wins(self) -> None:
        choice = choose_model(
            MAIL_CENTRE,
            "code",
            catalogue=MIXED,
            settings={**OFFLINE_SETTINGS, "allow_paid_providers": True},
            override={"provider": "anthropic", "model": "claude-3-7-sonnet-20250219"},
        )
        self.assertFalse(choice.automatic)
        self.assertEqual(choice.provider, "anthropic")
        self.assertEqual(choice.model, "claude-3-7-sonnet-20250219")
        self.assertEqual(choice.tier, PAID)
        self.assertEqual(choice.complexity, COMPLEX)
        self.assertIn("ручной выбор: anthropic · claude-3-7-sonnet-20250219", choice.reasons)

    def test_09_an_unlisted_override_is_still_honoured_and_has_no_tier(self) -> None:
        choice = choose_model(
            "простая задача",
            "analysis",
            catalogue=MIXED,
            settings=OFFLINE_SETTINGS,
            override={"provider": "custom", "model": "my-model"},
        )
        self.assertEqual((choice.provider, choice.model), ("custom", "my-model"))
        self.assertEqual(choice.tier, "")

    def test_10_a_free_gateway_route_never_outranks_the_keyless_engine(self) -> None:
        """The live deployment's shape: a real default provider, a free gateway row, a
        keyless engine. `SIMPLE` work must land on the engine that opens no socket, and the
        reason must say so — a free gateway route is still a real network call."""

        live_like = catalogue(
            model_row("mock", "mock-v1", FREE),
            model_row(
                "openrouter",
                "cohere/north-mini-code:free",
                FREE,
                capabilities=("code", "analysis", "review", "fast", "streaming"),
                context_window=131072,
            ),
            model_row("deepseek", "deepseek-chat", CHEAP, input_cost=0.14, output_cost=0.28),
            states={"mock": "READY", "openrouter": "READY", "deepseek": "READY"},
        )
        choice = choose_model(
            "скажи в одном слове: работает?",
            "analysis",
            catalogue=live_like,
            settings={
                "default_provider": "deepseek",
                "default_model": "deepseek-chat",
                "allow_paid_providers": True,
                "enabled_providers": ("mock", "openrouter", "deepseek"),
            },
        )
        self.assertEqual(choice.complexity, SIMPLE)
        self.assertEqual((choice.provider, choice.model), ("mock", "mock-v1"))
        self.assertEqual(choice.tier, FREE)
        self.assertTrue(
            any("без ключа и без сетевого вызова" in reason for reason in choice.reasons),
            choice.reasons,
        )

    def test_11_a_credentialed_free_row_is_used_when_no_keyless_engine_is_enabled(self) -> None:
        without_mock = catalogue(
            model_row(
                "openrouter",
                "cohere/north-mini-code:free",
                FREE,
                capabilities=("code", "analysis", "review", "fast", "streaming"),
            ),
            model_row("deepseek", "deepseek-chat", CHEAP, input_cost=0.14, output_cost=0.28),
            states={"openrouter": "READY", "deepseek": "READY"},
        )
        choice = choose_model(
            "скажи в одном слове: работает?",
            "analysis",
            catalogue=without_mock,
            settings={
                "default_provider": "deepseek",
                "allow_paid_providers": True,
                "enabled_providers": ("openrouter", "deepseek"),
            },
        )
        self.assertEqual(choice.tier, FREE)
        self.assertEqual(choice.provider, "openrouter")
        self.assertEqual(choice.model, "cohere/north-mini-code:free")


class CostCeilingTests(unittest.TestCase):
    """The owner's ceiling: COMPLEX work cannot auto-escalate above it, and never silently.

    The owner's balance was burned by a `COMPLEX` task routed to a paid model at $16.61 per
    1M. `max_cost_tier` is the ceiling he asked for: automatic routing never goes above it,
    the reason names the ceiling and the chosen model's price, an explicit pair is still
    honoured (flagged), and an absent or unknown value is the documented default, never the
    most permissive reading.
    """

    #: The priciest CHEAP row in :data:`MIXED`; the capped decision must not exceed it.
    MIXED_CHEAP_PRICE = 0.55

    def _complex(self, *, ceiling, allow_paid: bool = True):
        settings = {**OFFLINE_SETTINGS, "allow_paid_providers": allow_paid}
        if ceiling is not None:
            settings["max_cost_tier"] = ceiling
        return choose_model(MAIL_CENTRE, "code", catalogue=MIXED, settings=settings)

    def test_01_a_cheap_ceiling_caps_a_complex_task_and_says_so_with_the_price(self) -> None:
        choice = self._complex(ceiling=CHEAP)
        self.assertEqual(choice.complexity, COMPLEX)
        self.assertEqual(choice.tier, CHEAP)
        self.assertEqual(choice.max_cost_tier, CHEAP)
        self.assertTrue(choice.ceiling_limited)
        sentence = "сложная задача, но тариф ограничен CHEAP → выбрана дешёвая модель"
        self.assertTrue(
            any(sentence in reason for reason in choice.reasons), choice.reasons
        )
        # The price is in the reason, and the decision's own estimate is no higher than the
        # priciest cheap row: the ceiling is a number, not an abstract word.
        self.assertTrue(
            any("$0.55" in reason for reason in choice.reasons), choice.reasons
        )
        self.assertIsNotNone(choice.estimated_cost)
        self.assertLessEqual(choice.estimated_cost, self.MIXED_CHEAP_PRICE)
        self.assertGreater(choice.estimated_cost, 0.0)
        # The operative sentence is one of the three lines the card shows.
        self.assertIn(sentence, choice.reasons[0])

    def test_02_a_free_ceiling_caps_a_complex_task_at_zero(self) -> None:
        choice = self._complex(ceiling=FREE)
        self.assertEqual(choice.complexity, COMPLEX)
        self.assertEqual(choice.tier, FREE)
        self.assertTrue(choice.ceiling_limited)
        self.assertEqual(choice.estimated_cost, 0.0)
        self.assertTrue(
            any(
                "тариф ограничен FREE" in reason and "бесплатная модель" in reason
                for reason in choice.reasons
            ),
            choice.reasons,
        )
        self.assertTrue(
            any("$0 за 1M" in reason for reason in choice.reasons), choice.reasons
        )

    def test_03_a_paid_ceiling_leaves_the_existing_choice_unchanged(self) -> None:
        choice = self._complex(ceiling=PAID)
        self.assertEqual(choice.tier, PAID)
        self.assertFalse(choice.ceiling_limited)
        self.assertEqual(choice.max_cost_tier, PAID)
        self.assertFalse(
            any("тариф ограничен" in reason for reason in choice.reasons), choice.reasons
        )

    def test_04_an_override_above_the_ceiling_is_honoured_and_flagged(self) -> None:
        choice = choose_model(
            MAIL_CENTRE,
            "code",
            catalogue=MIXED,
            settings={
                **OFFLINE_SETTINGS,
                "allow_paid_providers": True,
                "max_cost_tier": CHEAP,
            },
            override={
                "provider": "anthropic",
                "model": "claude-3-7-sonnet-20250219",
            },
        )
        # Honoured: he may choose consciously.
        self.assertFalse(choice.automatic)
        self.assertEqual(
            (choice.provider, choice.model),
            ("anthropic", "claude-3-7-sonnet-20250219"),
        )
        self.assertEqual(choice.tier, PAID)
        # Flagged, first, with its own price -- never a silent overspend.
        self.assertIn("это дороже вашего потолка", choice.reasons[0])
        self.assertIn("CHEAP", choice.reasons[0])
        self.assertEqual(choice.estimated_cost, 3.0)
        self.assertFalse(choice.ceiling_limited)

    def test_05_an_absent_ceiling_uses_the_documented_default(self) -> None:
        self.assertEqual(DEFAULT_MAX_COST_TIER, CHEAP)
        choice = choose_model(
            MAIL_CENTRE,
            "code",
            catalogue=MIXED,
            settings={**OFFLINE_SETTINGS, "allow_paid_providers": True},
        )
        self.assertEqual(choice.max_cost_tier, DEFAULT_MAX_COST_TIER)
        self.assertEqual(choice.tier, CHEAP)
        self.assertTrue(choice.ceiling_limited)

    def test_06_an_unknown_ceiling_is_the_default_not_the_permissive_reading(self) -> None:
        for value in (None, "", "PLATINUM", 42, {"tier": CHEAP}):
            self.assertEqual(normalise_cost_tier(value), DEFAULT_MAX_COST_TIER, value)
        choice = choose_model(
            MAIL_CENTRE,
            "code",
            catalogue=MIXED,
            settings={
                **OFFLINE_SETTINGS,
                "allow_paid_providers": True,
                "max_cost_tier": "platinum",
            },
        )
        self.assertEqual(choice.max_cost_tier, DEFAULT_MAX_COST_TIER)
        self.assertEqual(choice.tier, CHEAP)
        self.assertTrue(choice.ceiling_limited)

    def test_07_the_ceiling_never_narrows_a_choice_below_it(self) -> None:
        # A ceiling is a cap, not a promotion: SIMPLE stays FREE, MEDIUM stays CHEAP, and
        # neither is reported as capped.
        simple = choose_model(
            "скажи в одном слове: работает?",
            "analysis",
            catalogue=MIXED,
            settings={**OFFLINE_SETTINGS, "max_cost_tier": PAID},
        )
        self.assertEqual(simple.tier, FREE)
        self.assertFalse(simple.ceiling_limited)
        medium = choose_model(
            "сделай рефакторинг модуля импорта и добавь тесты",
            "code",
            catalogue=MIXED,
            settings={**OFFLINE_SETTINGS, "max_cost_tier": CHEAP},
        )
        self.assertEqual(medium.tier, CHEAP)
        self.assertFalse(medium.ceiling_limited)
        self.assertTrue(
            any("средняя сложность → дешёвая модель" in reason for reason in medium.reasons),
            medium.reasons,
        )

    def test_08_an_override_at_or_below_the_ceiling_is_not_flagged(self) -> None:
        choice = choose_model(
            "простая задача",
            "analysis",
            catalogue=MIXED,
            settings={**OFFLINE_SETTINGS, "max_cost_tier": CHEAP},
            override={"provider": "deepseek", "model": "deepseek-chat"},
        )
        self.assertEqual(choice.tier, CHEAP)
        self.assertFalse(
            any("дороже вашего потолка" in reason for reason in choice.reasons),
            choice.reasons,
        )
        self.assertEqual(choice.estimated_cost, 0.14)


class CostCeilingSettingTests(unittest.TestCase):
    """The setting itself: the app's own file, default CHEAP, preserved keys, the route."""

    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.path = pathlib.Path(self._temporary.name) / "settings.json"

    def test_01_a_fresh_store_answers_the_default(self) -> None:
        self.assertFalse(self.path.exists())
        store = AppSettingsStore(self.path)
        self.assertEqual(store.max_cost_tier(), DEFAULT_MAX_COST_TIER)
        # Reading the default must not write the file: a fresh install is not silently
        # given a value the owner never chose.
        self.assertFalse(self.path.exists())

    def test_02_the_choice_round_trips_and_survives_another_writer(self) -> None:
        store = AppSettingsStore(self.path)
        self.assertEqual(store.set_max_cost_tier("cheap"), CHEAP)
        self.assertEqual(AppSettingsStore(self.path).max_cost_tier(), CHEAP)
        # The stored word is the lowercase one the owner reads in the file.
        self.assertIn('"max_cost_tier": "cheap"', self.path.read_text(encoding="utf-8"))
        # A different writer of the same file (the projects folder) must not drop it.
        root = pathlib.Path(self._temporary.name) / "projects"
        store.set_project_root(str(root))
        self.assertEqual(AppSettingsStore(self.path).max_cost_tier(), CHEAP)
        # Mixed case is accepted; the answer is always the router's uppercase word.
        self.assertEqual(store.set_max_cost_tier("Paid"), PAID)
        self.assertEqual(AppSettingsStore(self.path).max_cost_tier(), PAID)
        self.assertEqual(store.set_max_cost_tier("FREE"), FREE)

    def test_03_an_unknown_value_is_refused_and_the_stored_one_survives(self) -> None:
        from app.config.app_settings import AppSettingsError

        store = AppSettingsStore(self.path)
        store.set_max_cost_tier("cheap")
        with self.assertRaises(AppSettingsError):
            store.set_max_cost_tier("platinum")
        self.assertEqual(AppSettingsStore(self.path).max_cost_tier(), CHEAP)

    def test_04_a_corrupt_stored_value_is_the_default(self) -> None:
        self.path.write_text('{"max_cost_tier": "PLATINUM"}', encoding="utf-8")
        self.assertEqual(
            AppSettingsStore(self.path).max_cost_tier(), DEFAULT_MAX_COST_TIER
        )
        self.path.write_text("not json at all", encoding="utf-8")
        self.assertEqual(
            AppSettingsStore(self.path).max_cost_tier(), DEFAULT_MAX_COST_TIER
        )

    def test_05_the_route_publishes_and_accepts_the_ceiling(self) -> None:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from app.api.settings_routes import register_settings_routes
        from app.config.app_settings import AppSettingsError

        class _Store:
            """The surface the route uses, without probing a real projects folder."""

            def __init__(self) -> None:
                self.tier = DEFAULT_MAX_COST_TIER.lower()

            def describe(self):
                return {
                    "max_cost_tier": self.tier,
                    "max_cost_tier_default": DEFAULT_MAX_COST_TIER.lower(),
                    "max_cost_tier_options": [tier.lower() for tier in TIERS],
                }

            def set_max_cost_tier(self, value):
                text = str(value).strip().lower()
                if text not in {tier.lower() for tier in TIERS}:
                    raise AppSettingsError("bad tier", field="max_cost_tier")
                self.tier = text
                return text.upper()

        app = FastAPI()
        register_settings_routes(app, _Store())
        client = TestClient(app)

        first = client.get("/api/settings").json()
        self.assertEqual(first["max_cost_tier"], DEFAULT_MAX_COST_TIER.lower())
        written = client.post("/api/settings", json={"max_cost_tier": "free"})
        self.assertEqual(written.status_code, 200, written.text)
        self.assertEqual(written.json()["max_cost_tier"], "free")
        self.assertEqual(client.get("/api/settings").json()["max_cost_tier"], "free")
        self.assertEqual(
            client.post("/api/settings", json={"max_cost_tier": "platinum"}).status_code,
            400,
        )
        self.assertEqual(
            client.post(
                "/api/settings",
                json={"max_cost_tier": "paid", "project_root": str(self.path)},
            ).status_code,
            400,
        )
        self.assertEqual(client.get("/api/settings").json()["max_cost_tier"], "free")


class CatalogueShapeTests(unittest.TestCase):
    """The catalogue is read, never invented."""

    def test_01_the_api_payload_is_understood(self) -> None:
        rows, states = catalogue_rows(MIXED)
        self.assertEqual(states["mock"], "READY")
        self.assertIn(("deepseek", "deepseek-chat"), {(r.provider, r.model) for r in rows})

    def test_02_the_per_1m_rate_is_read_from_estimated_cost(self) -> None:
        rows, _ = catalogue_rows(FREE_ONLY)
        cheap = next(row for row in rows if row.model == "deepseek-chat")
        self.assertEqual(cheap.input_cost_per_1m, 0.14)
        self.assertEqual(cheap.output_cost_per_1m, 0.28)

    def test_03_an_empty_catalogue_answers_no_choice(self) -> None:
        self.assertEqual(catalogue_rows(None), ((), {}))
        choice = choose_model("x", "analysis", catalogue=None, settings=OFFLINE_SETTINGS)
        self.assertEqual(choice.provider, "mock")
        self.assertEqual(choice.model, "")

    def test_04_determinism_of_the_whole_decision(self) -> None:
        first = choose_model(NEW_APP, "code", catalogue=MIXED, settings=OFFLINE_SETTINGS)
        second = choose_model(NEW_APP, "code", catalogue=MIXED, settings=OFFLINE_SETTINGS)
        self.assertEqual(first.to_dict(), second.to_dict())


class PublishedShapeTests(unittest.TestCase):
    """What the response and the card read."""

    def test_the_payload_carries_the_six_required_facts(self) -> None:
        choice = choose_model(
            "скажи в одном слове: работает?",
            "analysis",
            catalogue=MIXED,
            settings=OFFLINE_SETTINGS,
        )
        payload = choice.to_dict()
        for key in (
            "provider",
            "model",
            "tier",
            "complexity",
            "reasons",
            "estimated_cost",
        ):
            self.assertIn(key, payload)
        self.assertIsInstance(payload["reasons"], list)
        self.assertTrue(payload["reasons"])


class RunPathTests(unittest.TestCase):
    """The dispatch path really uses the choice, and the dry run dispatches nothing."""

    def _service(self, *, allow_paid: bool = False):
        from app.agents.provider_agent import ProviderAgent
        from app.agents.providers.base import (
            Provider,
            ProviderRequest,
            ProviderResponse,
        )
        from app.agents.providers.config import ProviderConfig
        from app.api.service import ForgeApiService
        from app.config.settings import RuntimeSettings
        from app.runtime.bootstrap import create_runtime

        class _Recording(Provider):
            def __init__(self) -> None:
                super().__init__(
                    ProviderConfig(provider_name="deepseek", model_name="deepseek-chat")
                )
                self.requests: list[ProviderRequest] = []

            def generate(self, request: ProviderRequest) -> ProviderResponse:
                self.requests.append(request)
                return ProviderResponse(
                    provider_name=self.provider_name,
                    model_name=request.model_name or self.model_name,
                    output="ok",
                )

        settings = RuntimeSettings(
            default_provider="mock",
            enabled_providers=("mock", "deepseek"),
            provider_fallback_chain=(),
            allow_paid_providers=allow_paid,
        )
        runtime = create_runtime(settings=settings)
        provider = _Recording()
        runtime.agent_registry._agents["deepseek"] = ProviderAgent(provider)
        service = ForgeApiService(runtime=runtime, authorized_project_id="routing-test")
        return service, provider

    def test_01_an_unnamed_request_publishes_the_automatic_decision(self) -> None:
        from app.api.models import TaskRunRequest

        service, _provider = self._service()
        outcome = service.run_task_detailed(
            TaskRunRequest(description="скажи в одно слово: работает?", category="analysis")
        )
        published = outcome.response.model_choice
        self.assertTrue(published, "the run published no routing decision")
        self.assertEqual(published["complexity"], SIMPLE)
        self.assertEqual(published["tier"], FREE)
        self.assertEqual(published["provider"], "mock")
        self.assertTrue(published["reasons"])
        # The response still names what really ran.
        self.assertEqual(outcome.response.provider_used, "mock")

    def test_02_a_medium_task_is_routed_to_the_cheap_model_it_names(self) -> None:
        from app.api.models import TaskRunRequest

        service, provider = self._service()
        outcome = service.run_task_detailed(
            TaskRunRequest(description=REFACTOR_WITH_TESTS, category="code")
        )
        published = outcome.response.model_choice
        self.assertEqual(published["complexity"], MEDIUM)
        self.assertEqual(published["tier"], CHEAP)
        self.assertEqual(published["provider"], "deepseek")
        # The decision reached the provider call site: this is the model it ran. The
        # strongest cheap row is chosen (the registry's own published rate decides).
        self.assertEqual(len(provider.requests), 1, outcome.response.error)
        self.assertEqual(provider.requests[0].model_name, "deepseek-reasoner")
        self.assertEqual(outcome.response.model_used, "deepseek-reasoner")

    def test_03_the_dry_run_dispatches_nothing(self) -> None:
        service, provider = self._service()
        decision = service.estimate_route(
            description="сделай рефакторинг модуля импорта и добавь тесты",
            category="code",
        )
        self.assertTrue(decision["estimate"])
        self.assertEqual(decision["model_choice"]["tier"], CHEAP)
        self.assertEqual(provider.requests, [], "the dry run called the provider")

    def test_04_a_complex_auto_choice_never_spends_without_permission(self) -> None:
        service, provider = self._service(allow_paid=False)
        decision = service.estimate_route(
            description=MAIL_CENTRE,
            category="code",
        )
        self.assertEqual(decision["model_choice"]["tier"], CHEAP)
        self.assertTrue(
            any(
                "платные модели запрещены" in reason
                for reason in decision["model_choice"]["reasons"]
            ),
            decision,
        )
        self.assertEqual(provider.requests, [])

    def test_05_the_explicit_pair_still_wins_over_the_classifier(self) -> None:
        from app.api.models import TaskRunRequest

        service, provider = self._service()
        outcome = service.run_task_detailed(
            TaskRunRequest(
                description="скажи в одно слово: работает?",
                category="analysis",
                provider_name="deepseek",
                model_name="deepseek-chat",
            )
        )
        published = outcome.response.model_choice
        self.assertFalse(published["automatic"])
        self.assertEqual((published["provider"], published["model"]), ("deepseek", "deepseek-chat"))
        self.assertEqual(provider.requests[0].model_name, "deepseek-chat")


if __name__ == "__main__":
    unittest.main()
