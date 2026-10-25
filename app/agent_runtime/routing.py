"""Automatic model choice by task complexity — deterministic, no model call.

The owner's question was "where do I choose the model that will work?", and the honest
answer is "you should not have to". This module is the choice itself: a pure function of
the task text, the task category and the deployment's own catalogue. It opens no socket,
reads no secret, imports nothing outside the standard library, and returns the same
answer for the same input every time.

Why not an LLM
--------------
A model asked "is this hard?" is another thing to pay for, another thing that can fail,
and it cannot be unit-tested. So the classification is a short, explicit rule table --
:data:`LONG_TASK_CHARS`, :data:`MANY_CLAUSES`, :data:`MANY_FEATURES`,
:data:`MANY_SPECIFICATION_FIELDS`, :data:`COMPLEXITY_MEDIUM_SCORE`,
:data:`COMPLEXITY_COMPLEX_SCORE`, and the :data:`SCALE_SIGNALS` patterns. Every reason it
publishes is derived from one of those rules, so a reader can always say *why* the tier
was chosen instead of being told a verdict.

The tier policy
---------------
``SIMPLE -> FREE`` (the free/mock rows only), ``MEDIUM -> CHEAP``, ``COMPLEX -> PAID``.
The paid tier is used for :data:`COMPLEX` work **and only when**
``allow_paid_providers`` is true; when it is not, the best cheap-or-free model is chosen
and the reasons say so. A configured ``default_provider`` wins when its tier matches, so a
deployment that deliberately runs everything on one provider keeps doing that. Within a
tier a provider that needs **no account** (the offline engine) is preferred, then the
highest-quality row (published price, then capability count, then context window — a
deterministic order, not a vibe). The keyless preference is deliberate: a free *gateway*
route is still a real network call and a rate limit, while `SIMPLE` work that opens no
socket cannot fail, cannot be billed and cannot change model id under the owner's feet.

An explicit ``override`` (a provider *and* a model named by the caller) always wins. It is
the existing manual path, kept for power users; it is no longer required.

Availability
------------
Only providers the catalogue reports as routable are considered: ``READY`` or
``NOT_CHECKED`` (which means "enabled by configuration and its key is present, never
probed"). ``DISABLED``, ``NO_CREDENTIALS``, ``NETWORK_BLOCKED``, ``NO_CREDITS``, ``ERROR``
and an unprovable ``REGISTERED`` are all skipped, so automatic routing cannot pick a
provider the deployment did not enable or has no credentials for.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence, Tuple

__all__ = [
    "SIMPLE",
    "MEDIUM",
    "COMPLEX",
    "COMPLEXITY_LEVELS",
    "FREE",
    "CHEAP",
    "PAID",
    "TIERS",
    "TIER_FOR_COMPLEXITY",
    "ROUTABLE_PROVIDER_STATES",
    "LONG_TASK_CHARS",
    "MANY_CLAUSES",
    "VERY_MANY_CLAUSES",
    "MANY_FEATURES",
    "MANY_SPECIFICATION_FIELDS",
    "COMPLEXITY_MEDIUM_SCORE",
    "COMPLEXITY_COMPLEX_SCORE",
    "SCALE_SIGNALS",
    "ModelRow",
    "ModelChoice",
    "choose_model",
]

# --------------------------------------------------------------------- vocabulary
SIMPLE = "SIMPLE"
MEDIUM = "MEDIUM"
COMPLEX = "COMPLEX"
COMPLEXITY_LEVELS = (SIMPLE, MEDIUM, COMPLEX)

FREE = "FREE"
CHEAP = "CHEAP"
PAID = "PAID"
TIERS = (FREE, CHEAP, PAID)

#: The one tier policy, published to the interface from here (``GET /api/status``'s
#: ``routing_policy``) so the panel and this function cannot disagree.
TIER_FOR_COMPLEXITY = {SIMPLE: FREE, MEDIUM: CHEAP, COMPLEX: PAID}

# --------------------------------------------------------------------- thresholds
#: A task this long carries more than one request in it. Measured against the owner's own
#: wording: a one-line question is under 100 characters, a real change request is 150-400,
#: and a paragraph that asks for several things at once is above this.
LONG_TASK_CHARS = 240
#: This many clauses (sentences plus "and/затем" joins) is no longer a one-liner.
MANY_CLAUSES = 4
#: This many is a specification in disguise.
VERY_MANY_CLAUSES = 8
#: Strictly more requested features than this makes the work multi-part.
MANY_FEATURES = 3
#: A confirmed discovery specification with this many filled fields is a project, not a task.
MANY_SPECIFICATION_FIELDS = 5
#: Two points is the floor of "not a one-liner": one long text, or one scale word.
COMPLEXITY_MEDIUM_SCORE = 2
#: Four points is project-shaped: two scale words, or a scale word plus write mode.
COMPLEXITY_COMPLEX_SCORE = 4

#: Provider states (``app/agents/providers/provider_state.py``) automatic routing may use.
#: ``READY`` needs no credentials at all (the offline mock provider); ``NOT_CHECKED`` means
#: enabled with its key present and never probed. Anything else is skipped: an unprovable
#: ``REGISTERED`` is not availability.
ROUTABLE_PROVIDER_STATES = frozenset({"READY", "NOT_CHECKED"})

#: Code-generation verbs with a multi-file object, plus the nouns that name real
#: engineering surface. Each pattern contributes its weight; the reason string names the
#: signals that matched, so the tier is never a bare word. The list is the owner's own
#: vocabulary: «создай приложение», «сервер», «база данных», «авторизация»,
#: «интеграция», «тесты», «рефактор», «миграция».
#:
#: The weights are the rule, not a decoration: «создай приложение» is a project on its own
#: (4 points, i.e. COMPLEX by itself), a server / database / auth / integration is half a
#: project (2 points each), and tests, refactoring and a migration are a real but smaller
#: step (1 point each). Any scale word at all floors the task at MEDIUM — a task that names
#: one engineering surface is not a one-liner.
SCALE_SIGNALS: Tuple[Tuple[str, str, int], ...] = (
    (
        "создание приложения",
        r"(?:созда|сдела|напиш|постро|реализу|сгенер|create|build|make|implement|generate|write)"
        r"[а-яёa-z]*\s+[^.!?;\n]{0,40}?"
        r"(?:приложени|application\b|\bapp\b|сервис|service|сервер|server|сайт|website|web\s+site|бот\b|bot\b|инструмент|\btool\b)",
        4,
    ),
    ("сервер", r"\b(?:сервер\w*|server\b|бэкенд\w*|backend\b)", 2),
    (
        "база данных",
        r"(?:баз[аыуе]\s+данных|базой\s+данных|database|\bбд\b|\bdb\b|postgres\w*|sqlite|mongodb?)",
        2,
    ),
    (
        "авторизация",
        r"(?:авторизац\w*|аутентификац\w*|логин\w*|вход\s+по|auth\w*|login\b|sign[\s-]?in\b|oauth\w*|\bjwt\b)",
        2,
    ),
    (
        "интеграция",
        r"(?:интеграц\w*|подключ\w*\s+к\s+|integration\w*|integrate\b|webhook\w*)",
        2,
    ),
    # "Tests" means *writing* tests, not mentioning one: "объясни, почему тест падает" is a
    # diagnosis, while "напиши тесты" / "тесты для модуля" is work. A plural suite word
    # always counts, and a singular one counts when a writing verb precedes it.
    (
        "тесты",
        r"(?:(?:напиш|добав|сдела|покро|созда|прогон|write|add|create|cover)[а-яёa-z]*\s+"
        r"[^.!?;\n]{0,30}?\bтест\w*|\bтесты\b|\bтестов\b|\bтестами\b|\bтестах\b|"
        r"\btests?\b|pytest|unit\s+tests?)",
        1,
    ),
    ("рефакторинг", r"(?:рефактор\w*|refactor\w*)", 1),
    ("миграция", r"(?:миграц\w*|migration\w*|migrate\b)", 1),
)

#: The scale score is capped here, so a wall of keywords cannot outrank the levels.
SCALE_SCORE_CAP = 6

#: The labels ``ProjectSpecification.as_task_text()`` writes. Only these lines count as
#: "the discovery answered it", so the caller's own prose is never counted as a field.
_SPECIFICATION_HEADER = "PROJECT SPECIFICATION"
_SPECIFICATION_LABELS = frozenset(
    {
        "goal",
        "project type",
        "users",
        "core features",
        "platform",
        "integrations",
        "data storage",
        "auth",
        "notifications",
        "constraints",
        "acceptance criteria",
        "priorities",
    }
)

#: Task categories that are also model capabilities: a `code` task is preferred to land on
#: a row that declares `code`. Anything else is not a capability and never filters.
_CAPABILITY_CATEGORIES = frozenset({"code", "analysis", "review", "reasoning"})

_LIST_ITEM = re.compile(r"^\s*(?:[-*•]|\d{1,2}[.)])\s+\S", re.MULTILINE)
_CLAUSE_JOIN = re.compile(r",\s*(?:и|а\s+также|затем|потом|and|also|then)\b", re.IGNORECASE)
_SENTENCE_SPLIT = re.compile(r"[.!?;\n]+")
_FEATURE_MARKERS = ("функци", "features", "требования", "requirements")


# ------------------------------------------------------------------------ catalogue
@dataclass(frozen=True)
class ModelRow:
    """One catalogue row, normalised from whatever shape the caller supplied.

    It accepts the ``GET /api/models`` model row (``provider_id`` / ``model_id`` /
    ``cost_tier`` / ``capabilities`` / ``context_window`` / ``estimated_cost``), a
    ``ProviderModelInfo`` object, or a plain mapping with the same facts. Nothing is
    invented: a fact the row does not carry stays empty or ``None``.

    ``requires_credential`` is read from the provider's ``secret_env_name``: a provider that
    declares no key environment variable needs no account at all (the offline engine is the
    one such provider in this repository). It is ``True`` when the catalogue does not say,
    so an unknown provider is never assumed to be keyless.
    """

    provider: str
    model: str
    tier: str
    capabilities: Tuple[str, ...] = ()
    context_window: int = 0
    input_cost_per_1m: Optional[float] = None
    output_cost_per_1m: Optional[float] = None
    provider_state: str = ""
    requires_credential: bool = True


def _get(source: object, *names: str) -> object:
    """Read the first present name from a mapping or an object, or ``None``."""

    if isinstance(source, Mapping):
        for name in names:
            if name in source and source[name] is not None:
                return source[name]
        return None
    for name in names:
        if hasattr(source, name):
            value = getattr(source, name)
            if value is not None:
                return value
    return None


def _as_text(value: object) -> str:
    return str(value or "").strip()


def _as_float(value: object) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _capability_names(value: object) -> Tuple[str, ...]:
    if isinstance(value, str):
        items: Iterable[object] = value.split(",")
    elif isinstance(value, Iterable):
        items = value
    else:
        return ()
    names = []
    for item in items:
        text = _as_text(getattr(item, "value", item)).lower()
        if text:
            names.append(text)
    return tuple(sorted(set(names)))


def catalogue_rows(catalogue: object) -> Tuple[Tuple[ModelRow, ...], Dict[str, str]]:
    """``(rows, provider_states)`` from the catalogue a caller supplied.

    ``catalogue`` may be the whole ``GET /api/models`` payload (a mapping with ``models``
    and ``providers``), a bare sequence of model rows, or a single mapping of one row. An
    empty catalogue answers an empty tuple rather than an invented registry.
    """

    if catalogue is None:
        return (), {}
    models = _get(catalogue, "models", "model_rows")
    providers = _get(catalogue, "providers")
    if models is None:
        models = catalogue

    states: Dict[str, str] = {}
    keyless: Dict[str, bool] = {}
    if isinstance(providers, Iterable) and not isinstance(providers, (str, bytes, Mapping)):
        for provider in providers:
            provider_id = _as_text(_get(provider, "provider_id", "provider", "id")).lower()
            if not provider_id:
                continue
            states[provider_id] = _as_text(_get(provider, "state", "provider_state")).upper()
            secret_env = _get(provider, "secret_env_name", "default_secret_env")
            if secret_env is not None:
                keyless[provider_id] = _as_text(secret_env) == ""

    rows = []
    if isinstance(models, Mapping):
        models = (models,)
    if isinstance(models, Iterable) and not isinstance(models, (str, bytes)):
        for item in models:
            provider = _as_text(_get(item, "provider_id", "provider")).lower()
            model = _as_text(_get(item, "model_id", "model", "id"))
            if not provider or not model:
                continue
            tier = _as_text(_get(item, "cost_tier", "tier")).upper()
            input_price = _as_float(_get(item, "input_cost_per_1m"))
            if input_price is None:
                # The `/api/models` payload publishes the registry's per-1M input rate as
                # `estimated_cost`; it is a unit price there too.
                input_price = _as_float(_get(item, "estimated_cost"))
            window = _get(item, "context_window")
            rows.append(
                ModelRow(
                    provider=provider,
                    model=model,
                    tier=tier,
                    capabilities=_capability_names(_get(item, "capabilities")),
                    context_window=(
                        int(window)
                        if isinstance(window, (int, float)) and not isinstance(window, bool)
                        else 0
                    ),
                    input_cost_per_1m=input_price,
                    output_cost_per_1m=_as_float(_get(item, "output_cost_per_1m")),
                    provider_state=states.get(provider, ""),
                    requires_credential=not keyless.get(provider, False),
                )
            )

    # Deduplicate by (provider, model) keeping the first occurrence, and sort so the
    # answer cannot depend on the order the registry happened to iterate in.
    seen = set()
    unique = []
    for row in sorted(rows, key=lambda item: (item.provider, item.model)):
        key = (row.provider, row.model)
        if key in seen:
            continue
        seen.add(key)
        unique.append(row)
    return tuple(unique), states


def _is_routable(row: ModelRow, states: Mapping[str, str], routable_providers: Sequence[str]) -> bool:
    """Whether this row may be chosen automatically.

    ``routable_providers`` is the composition's own enabled set when the caller knows it
    (the service passes it); a row outside it is skipped even if the catalogue's state
    says otherwise. An empty value means "the caller supplied no separate set", and the
    catalogue's derived state is the only evidence.
    """

    if not row.tier:
        return False
    if routable_providers and row.provider not in routable_providers:
        return False
    state = (row.provider_state or states.get(row.provider, "")).strip().upper()
    if state and state not in ROUTABLE_PROVIDER_STATES:
        return False
    return True


def _category_rows(rows: Sequence[ModelRow], category: str) -> Sequence[ModelRow]:
    """Rows that declare this task category, when any of them do."""

    clean = _as_text(category).lower()
    if clean not in _CAPABILITY_CATEGORIES:
        return rows
    matching = [row for row in rows if clean in row.capabilities]
    return matching if matching else rows


def _quality_key(row: ModelRow) -> Tuple[int, float, int, int, str, str]:
    """The deterministic "highest quality within a tier" order (lower is better).

    The first component is not quality but certainty: a provider that needs **no account**
    is preferred inside its tier, because it cannot fail on a missing key and its work is
    the cheapest certain work there is (the offline engine). A free *gateway* route is
    still free, still a real network call and still rate-limited; when both are offered for
    `SIMPLE` work, the deterministic one is the honest default — and the preview names it,
    so nothing is hidden. Inside a tier that needs credentials the component is equal and
    the published price leads, which is what makes the strongest paid row win.
    """

    price = (row.input_cost_per_1m or 0.0) + (row.output_cost_per_1m or 0.0)
    return (
        1 if row.requires_credential else 0,
        -price,
        -len(row.capabilities),
        -row.context_window,
        row.provider,
        row.model,
    )


def _best(rows: Sequence[ModelRow]) -> Optional[ModelRow]:
    return sorted(rows, key=_quality_key)[0] if rows else None


# ----------------------------------------------------------------------- complexity
def _clause_count(text: str) -> int:
    sentences = [part for part in _SENTENCE_SPLIT.split(text) if part.strip()]
    return max(len(sentences), 1) + len(_CLAUSE_JOIN.findall(text))


def _scale_signals(lowered: str) -> Tuple[Tuple[str, int], ...]:
    matched = []
    for label, pattern, weight in SCALE_SIGNALS:
        if re.search(pattern, lowered, re.IGNORECASE):
            matched.append((label, weight))
    return tuple(matched)


def _feature_count(text: str) -> int:
    bullets = len(_LIST_ITEM.findall(text))
    if bullets:
        return bullets
    for line in text.splitlines():
        lowered = line.lower()
        if not any(marker in lowered for marker in _FEATURE_MARKERS):
            continue
        _, separator, tail = line.partition(":")
        if not separator:
            _, separator, tail = line.partition("：")
        if not separator:
            continue
        items = [item for item in re.split(r"[;,]", tail) if item.strip()]
        if len(items) > 1:
            return len(items)
    return 0


def _specification_field_count(text: str) -> int:
    if _SPECIFICATION_HEADER not in text:
        return 0
    body = text.split(_SPECIFICATION_HEADER, 1)[1]
    count = 0
    for line in body.splitlines():
        label, separator, value = line.partition(":")
        if not separator or not value.strip():
            continue
        if label.strip().lower() in _SPECIFICATION_LABELS:
            count += 1
    return count


@dataclass(frozen=True)
class ComplexityAssessment:
    """The tier plus the reasons that produced it — never a bare word."""

    level: str
    reasons: Tuple[str, ...]
    score: int


def assess_complexity(
    task_text: str,
    category: str = "code",
    *,
    write_mode: bool = False,
    existing_project: bool = False,
) -> ComplexityAssessment:
    """Classify one task with the explicit rule table above."""

    text = str(task_text or "")
    lowered = text.lower()
    reasons = []
    score = 0

    length = len(text.strip())
    if length >= LONG_TASK_CHARS:
        score += 1
        reasons.append(f"длинная задача: {length} символов")

    clauses = _clause_count(text)
    if clauses >= MANY_CLAUSES:
        score += 1
        if clauses >= VERY_MANY_CLAUSES:
            score += 1
            reasons.append(f"очень много частей: {clauses}")
        else:
            reasons.append(f"несколько частей: {clauses}")

    signals = _scale_signals(lowered)
    if signals:
        score += min(sum(weight for _label, weight in signals), SCALE_SCORE_CAP)
        reasons.append("масштаб: " + ", ".join(label for label, _weight in signals))
        # A task that names one engineering surface is not a one-liner, even when the
        # surface is a small one (tests, a refactor, a migration).
        score = max(score, COMPLEXITY_MEDIUM_SCORE)

    if _as_text(category).lower() == "code" and write_mode:
        score += 2
        reasons.append("код и запись файлов")

    if existing_project:
        score += 1
        reasons.append("изменение существующего проекта")

    features = _feature_count(text)
    if features > MANY_FEATURES:
        score += 1
        reasons.append(f"много функций: {features}")

    specification_fields = _specification_field_count(text)
    if specification_fields >= MANY_SPECIFICATION_FIELDS:
        score += 1
        reasons.append(f"подтверждённая спецификация: {specification_fields} полей")

    if score >= COMPLEXITY_COMPLEX_SCORE:
        level = COMPLEX
    elif score >= COMPLEXITY_MEDIUM_SCORE:
        level = MEDIUM
    else:
        level = SIMPLE
        if not reasons:
            reasons.append("простая задача: признаков сложности нет")

    return ComplexityAssessment(level=level, reasons=tuple(reasons), score=score)


# --------------------------------------------------------------------------- choice
@dataclass(frozen=True)
class ModelChoice:
    """One decision: the pair, its tier, the complexity behind it, and the reasons."""

    provider: str
    model: str
    tier: str
    complexity: str
    reasons: Tuple[str, ...] = ()
    estimated_cost: Optional[float] = None
    automatic: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "tier": self.tier,
            "complexity": self.complexity,
            "reasons": list(self.reasons),
            # ``0.0`` for a free row: nothing can be billed there. ``None`` otherwise,
            # because a run's cost is unknown before it runs -- a unit price is not a
            # forecast, and this module does not invent one.
            "estimated_cost": self.estimated_cost,
            "automatic": bool(self.automatic),
        }


def _setting(settings: object, name: str, default: object) -> object:
    value = _get(settings, name)
    return default if value is None else value


def _allowed_tiers(desired: str) -> Tuple[str, ...]:
    """The tiers to try, best match first. A tier never widens; it only falls back."""

    if desired == FREE:
        return (FREE,)
    if desired == CHEAP:
        return (CHEAP, FREE)
    if desired == PAID:
        return (PAID, CHEAP, FREE)
    return (FREE, CHEAP, PAID)


def choose_model(
    task_text: str,
    category: str = "code",
    *,
    catalogue: object,
    settings: object = None,
    override: Optional[Mapping[str, str]] = None,
    write_mode: bool = False,
    existing_project: bool = False,
) -> ModelChoice:
    """Choose the provider and model for one task, with the reasons for the choice.

    ``catalogue`` is the deployment's own answer (``GET /api/models``'s payload, a
    sequence of rows, or registry objects — see :func:`catalogue_rows`), ``settings`` is
    anything carrying ``default_provider`` / ``default_model`` / ``allow_paid_providers``
    (a mapping or the ``RuntimeSettings`` object itself). ``override`` is the explicit
    ``{"provider": ..., "model": ...}`` a caller named; it always wins.
    """

    assessment = assess_complexity(
        task_text,
        category,
        write_mode=write_mode,
        existing_project=existing_project,
    )
    reasons = list(assessment.reasons)

    default_provider = _as_text(_setting(settings, "default_provider", "")).lower()
    default_model = _as_text(_setting(settings, "default_model", ""))
    allow_paid = bool(_setting(settings, "allow_paid_providers", False))
    enabled = _setting(settings, "enabled_providers", ()) or ()
    routable_providers = tuple(
        _as_text(name).lower() for name in enabled if _as_text(name)
    ) if isinstance(enabled, Iterable) and not isinstance(enabled, (str, bytes)) else ()

    rows, states = catalogue_rows(catalogue)

    override_provider = _as_text((override or {}).get("provider")).lower()
    override_model = _as_text((override or {}).get("model"))
    if override_provider and override_model:
        known = next(
            (
                row
                for row in rows
                if row.provider == override_provider and row.model == override_model
            ),
            None,
        )
        tier = known.tier if known is not None and known.tier in TIERS else ""
        reasons.append(f"ручной выбор: {override_provider} · {override_model}")
        return ModelChoice(
            provider=override_provider,
            model=override_model,
            tier=tier,
            complexity=assessment.level,
            reasons=tuple(reasons),
            estimated_cost=0.0 if tier == FREE else None,
            automatic=False,
        )

    desired = TIER_FOR_COMPLEXITY[assessment.level]
    reasons.append(
        {
            FREE: "простая задача → бесплатная модель",
            CHEAP: "средняя сложность → дешёвая модель",
            PAID: "сложная задача → платная модель",
        }[desired]
    )
    if desired == PAID and not allow_paid:
        desired = CHEAP
        reasons.append(
            "платные модели запрещены настройкой → дешёвая модель, платного вызова нет"
        )

    routable = [
        row for row in rows if _is_routable(row, states, routable_providers)
    ]
    for tier in _allowed_tiers(desired):
        in_tier = _category_rows([row for row in routable if row.tier == tier], category)
        if not in_tier:
            continue
        if tier != desired:
            reasons.append(
                f"в тарифе {desired} нет доступных моделей → выбран тариф {tier}"
            )
        preferred = [row for row in in_tier if row.provider == default_provider]
        if preferred:
            chosen = next(
                (row for row in preferred if row.model == default_model), None
            ) or _best(preferred)
            reasons.append(
                f"совпадает с настроенным по умолчанию провайдером {chosen.provider}"
            )
        else:
            chosen = _best(in_tier)
            if not chosen.requires_credential:
                # The one provider that needs no account: deterministic, no socket, no key.
                reasons.append(
                    "выбран движок без ключа и без сетевого вызова "
                    f"({chosen.provider} · {chosen.model})"
                )
            else:
                reasons.append(
                    f"лучшая доступная модель тарифа {tier}: {chosen.provider} · {chosen.model}"
                )
        return ModelChoice(
            provider=chosen.provider,
            model=chosen.model,
            tier=chosen.tier,
            complexity=assessment.level,
            reasons=tuple(reasons),
            estimated_cost=0.0 if chosen.tier == FREE else None,
            automatic=True,
        )

    # Nothing routable at all: keep the deployment's own default provider in force (the
    # run path then behaves exactly as it did before automatic routing existed) and say
    # so. No model is named, because none could be proved to exist.
    reasons.append(
        "в реестре нет доступной модели: остаётся настроенный по умолчанию провайдер"
    )
    return ModelChoice(
        provider=default_provider,
        model="",
        tier="",
        complexity=assessment.level,
        reasons=tuple(reasons),
        estimated_cost=None,
        automatic=True,
    )
