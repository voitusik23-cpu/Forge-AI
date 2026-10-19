"""The owner-supplied Radox AI model catalogue, as pure data.

Nothing in this file was observed by dialling Radox AI: the whole table was
supplied by the project owner on 2026-10-18. Each context window and price is
therefore a *declaration*, not a probed fact, and the registry rows built from it
carry :data:`CATALOGUE_SOURCE_NOTE` as ``metadata["limits_source"]`` for exactly
that reason.

The table is kept complete as an ARCHIVE (:data:`CATALOGUE`): the owner pruned
the active set, not the data. What the provider advertises and what the registry
registers is the curated subset in :data:`CURATED_MODEL_IDS` -- see the comment
above that tuple for the selection rule. Excluding a model from the active set
must never lose the owner's figures, so every removed model stays in the archive
and is still reachable through :func:`entry_for`.

The module holds data only -- no I/O, no SDK, no credential. Both
``radox.RadoxProvider.MODEL_IDS`` and the registry rows in
``model_registry.create_default_registry`` are derived from
:data:`CURATED_MODEL_IDS` / :data:`CURATED_CATALOGUE`, so the provider's model
list and the registry cannot drift apart.

Context strings were converted to the token count they denote: ``8K`` = 8,192,
``262K`` = 262,144, ``500K`` = 500,000, ``1M`` = 1,000,000, ``1.048576M`` =
1,048,576 and ``1.05M`` = 1,050,000.

``provider_capabilities`` carries the catalogue's own capability tags verbatim.
Most map onto a ``ModelCapability`` member, but ``web`` has none; it is preserved
here rather than invented as a new member of a shared domain enum. The same
applies to the input/output price, which is per *one million* tokens for every
model in this catalogue.
"""

from dataclasses import dataclass

#: Date the owner supplied this catalogue, and the note recorded on every row.
CATALOGUE_DATE = "2026-10-18"
CATALOGUE_SOURCE_NOTE = "owner-supplied Radox catalogue 2026-10-18"


@dataclass(frozen=True)
class RadoxCatalogueEntry:
    """One Radox AI model exactly as the owner listed it."""

    model_id: str
    display_name: str
    context_window: int
    input_cost_per_1m: float
    output_cost_per_1m: float
    provider_capabilities: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.model_id.strip():
            raise ValueError("model_id must not be empty")
        if not self.display_name.strip():
            raise ValueError("display_name must not be empty")
        if self.context_window <= 0:
            raise ValueError("context_window must be a positive token count")
        if self.input_cost_per_1m <= 0 or self.output_cost_per_1m <= 0:
            raise ValueError("catalogue prices are per 1M tokens and above zero")


# Capability tag groups, kept in the catalogue's own vocabulary.
_REASONING_TOOLS = ("reasoning", "tools", "streaming", "json")
_STANDARD = ("reasoning", "vision", "tools", "streaming", "json")
_STANDARD_WEB = _STANDARD + ("web",)
_STREAMING_JSON = ("streaming", "json")


#: The full catalogue, in the order the owner listed it. This is the ARCHIVE:
#: every model the owner supplied, kept even when it is not part of the active
#: set, so pruning the advertised list never destroys a declared fact.
CATALOGUE: tuple[RadoxCatalogueEntry, ...] = (
    RadoxCatalogueEntry("gemma-2-2b", "Gemma 2 2B", 8192, 0.12, 0.12, _STREAMING_JSON),
    RadoxCatalogueEntry(
        "deepseek-v4-flash-0731",
        "DeepSeek-V4-Flash-0731",
        1048576,
        0.12,
        0.12,
        _REASONING_TOOLS,
    ),
    RadoxCatalogueEntry("gpt-5.6-luna", "GPT-5.6 Luna", 1050000, 0.36, 0.36, _STANDARD),
    RadoxCatalogueEntry(
        "deepseek-v4-pro-0813",
        "DeepSeek-V4-Pro-0813",
        1048576,
        0.55,
        0.55,
        _REASONING_TOOLS,
    ),
    RadoxCatalogueEntry("qwen3.8-27b", "Qwen3.8-27B", 262144, 0.57, 0.57, _STANDARD),
    RadoxCatalogueEntry("seed-2.1-turbo", "Seed 2.1 Turbo", 262144, 0.92, 0.92, _STANDARD),
    RadoxCatalogueEntry("seed-2.1-pro", "Seed 2.1 Pro", 262144, 1.15, 1.15, _STANDARD),
    RadoxCatalogueEntry("kimi-k2.6", "Kimi K2.6", 1000000, 1.22, 1.22, _STANDARD_WEB),
    RadoxCatalogueEntry(
        "gemini-3.7-flash", "Gemini 3.7 Flash", 1048576, 1.24, 1.24, _STANDARD_WEB
    ),
    RadoxCatalogueEntry("glm-5.3", "GLM-5.3", 1000000, 1.36, 1.36, _STANDARD_WEB),
    RadoxCatalogueEntry("glm-5.2", "GLM-5.2", 1000000, 1.36, 1.36, _STANDARD_WEB),
    RadoxCatalogueEntry("qwen3.7-max", "Qwen3.7 Max", 1000000, 1.76, 1.76, _STANDARD_WEB),
    RadoxCatalogueEntry("qwen3.8-max", "Qwen3.8 Max", 1000000, 1.76, 1.76, _STANDARD_WEB),
    RadoxCatalogueEntry("muse-spark-1.1", "Muse Spark 1.1", 1000000, 1.82, 1.82, _STANDARD),
    RadoxCatalogueEntry(
        "deepseek-v4-pro-max",
        "DeepSeek-V4-Pro-Max",
        1048576,
        2.05,
        2.05,
        _REASONING_TOOLS,
    ),
    RadoxCatalogueEntry(
        "gemini-3.6-flash", "Gemini 3.6 Flash", 1048576, 2.5, 2.5, _STANDARD_WEB
    ),
    RadoxCatalogueEntry("grok-4.5", "Grok 4.5", 500000, 2.81, 2.81, _STANDARD_WEB),
    RadoxCatalogueEntry("grok-4.6", "Grok 4.6", 500000, 2.81, 2.81, _STANDARD_WEB),
    RadoxCatalogueEntry(
        "claude-sonnet-5", "Claude Sonnet 5", 1000000, 3.32, 3.32, _STANDARD
    ),
    RadoxCatalogueEntry("gpt-5.6-terra", "GPT-5.6 Terra", 1050000, 3.58, 3.58, _STANDARD),
    RadoxCatalogueEntry(
        "gemini-3.1-pro", "Gemini 3.1 Pro", 1048576, 4.47, 4.47, _STANDARD_WEB
    ),
    RadoxCatalogueEntry("kimi-k3", "Kimi K3", 1000000, 4.98, 4.98, _STANDARD_WEB),
    RadoxCatalogueEntry("claude-opus-5.5", "Claude Opus 5.5", 1000000, 8.3, 8.3, _STANDARD),
    RadoxCatalogueEntry("claude-opus-4.7", "Claude Opus 4.7", 1000000, 8.3, 8.3, _STANDARD),
    RadoxCatalogueEntry("claude-opus-4.6", "Claude Opus 4.6", 1000000, 8.3, 8.3, _STANDARD),
    RadoxCatalogueEntry("claude-opus-4.8", "Claude Opus 4.8", 1000000, 8.3, 8.3, _STANDARD),
    RadoxCatalogueEntry(
        "claude-mythos-preview", "Claude Mythos Preview", 1000000, 8.3, 8.3, _STANDARD
    ),
    RadoxCatalogueEntry("claude-opus-5", "Claude Opus 5", 1000000, 8.3, 8.3, _STANDARD),
    RadoxCatalogueEntry("gpt-5.5-pro", "GPT-5.5 Pro", 1050000, 8.95, 8.95, _STANDARD),
    RadoxCatalogueEntry("gpt-5.5", "GPT-5.5", 1050000, 8.95, 8.95, _STANDARD),
    RadoxCatalogueEntry("gpt-5.6-sol", "GPT-5.6 Sol", 1050000, 8.95, 8.95, _STANDARD),
    RadoxCatalogueEntry(
        "claude-fable-5", "Claude Fable 5", 1000000, 16.61, 16.61, _STANDARD
    ),
    RadoxCatalogueEntry(
        "claude-fable-5.1", "Claude Fable 5.1", 1000000, 16.61, 16.61, _STANDARD
    ),
)

#: The complete owner-supplied table under its archive name: nothing below
#: removes a row from this tuple.
ARCHIVE = CATALOGUE

#: The curated active set: what ``RadoxProvider.MODEL_IDS`` advertises and what
#: ``model_registry.create_default_registry`` registers for ``radox``.
#:
#: Selection rule (owner instruction, 2026-10-18): keep a *small* set of
#: productive, token-heavy workhorses -- cheap bulk work, one strong mid tier,
#: and a couple of premium models for the biggest tasks -- and exclude
#: duplicates and superseded generations. Concretely: every kept model has a
#: context window of at least 262,144 tokens and the set spans both ends of the
#: price range (a $0.12/1M bulk model and a $16.61/1M top tier), so the owner
#: can pick by budget without leaving large-context work behind.
#:
#: Exclusion is not deletion: every model removed from this tuple is still in
#: :data:`CATALOGUE`.
CURATED_MODEL_IDS: tuple[str, ...] = (
    "deepseek-v4-flash-0731",  # 1. cheapest bulk workhorse, 1.048576M
    "gpt-5.6-luna",  # 2. cheap general, 1.05M
    "qwen3.8-27b",  # 3. cheap reasoning+tools, 262K
    "gemini-3.7-flash",  # 4. cheap with web, 1.048576M
    "glm-5.3",  # 5. cheap with web+vision, 1M
    "claude-sonnet-5",  # 6. strong mid, 1M
    "kimi-k3",  # 7. strong long-context, 1M
    "claude-opus-5.5",  # 8. premium for large tasks, 1M
    "gpt-5.5-pro",  # 9. premium reasoning, 1.05M
    "claude-fable-5.1",  # 10. top tier for the biggest tasks, 1M
)

_BY_MODEL_ID = {entry.model_id: entry for entry in CATALOGUE}

if len(_BY_MODEL_ID) != len(CATALOGUE):  # pragma: no cover - guards a typo in the table
    raise ValueError("the Radox catalogue contains a duplicate model id")

if len(set(CURATED_MODEL_IDS)) != len(CURATED_MODEL_IDS):  # pragma: no cover
    raise ValueError("the curated Radox model list contains a duplicate model id")

_UNKNOWN_CURATED_IDS = [
    model_id for model_id in CURATED_MODEL_IDS if model_id not in _BY_MODEL_ID
]
if _UNKNOWN_CURATED_IDS:  # pragma: no cover - guards a typo in the curated list
    raise ValueError(
        "the curated Radox model list names ids absent from the archive: "
        + ", ".join(_UNKNOWN_CURATED_IDS)
    )

#: The curated entries, in curated order: what the registry builds its rows from.
CURATED_CATALOGUE: tuple[RadoxCatalogueEntry, ...] = tuple(
    _BY_MODEL_ID[model_id] for model_id in CURATED_MODEL_IDS
)

#: The model IDs the provider advertises: the curated set, not the whole archive.
MODEL_IDS: tuple[str, ...] = CURATED_MODEL_IDS


def entry_for(model_id: str) -> RadoxCatalogueEntry:
    """Return the archived catalogue entry for ``model_id`` or raise ``KeyError``.

    The lookup covers the whole archive, including models that are not part of
    the curated active set.
    """

    return _BY_MODEL_ID[model_id]
