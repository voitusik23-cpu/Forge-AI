# Forge AI

Forge AI is a planned reusable platform for coordinating AI-assisted software
development, review, testing, and improvement across projects.

## Development stage

This repository contains the initial scaffold, Orchestrator v0.1, Provider
Layer v0.1, Runtime & Configuration Layer v0.1, and Execution Pipeline v0.1.
It includes provider-neutral task dispatch, provider interfaces, and an offline
`MockProvider`. The OpenAI Provider v0.1 uses the official OpenAI Python SDK
and Responses API when an OpenAI task is explicitly dispatched. It reads
`OPENAI_API_KEY` from the local process environment only during generation;
startup and offline tests make no API calls. Anthropic Provider v0.1 uses the
official Anthropic Python SDK Messages API when explicitly dispatched. The
DeepSeek and OpenRouter providers use the existing OpenAI SDK with their
OpenAI-compatible Chat Completions APIs. All provider keys are resolved through
`SecretStore` at generation time. Google/Gemini and xAI remain unconfigured.
Runtime settings are validated from `FORGE_*` environment variables. Forge AI
does not modify projects. The project requires the OpenAI and Anthropic SDKs.

Groq uses the existing OpenAI-compatible client with the official Groq endpoint
and `GROQ_API_KEY`. Optional provider account emails live in a separate local
`ProviderAccountConfig`; they are not API credentials and are not included in
provider results or logs.

## Dispatcher v0.2

Tasks carry a category (`coding`, `reasoning`, `large-context`, `cheap/free`,
`fast/cheap`, or `other`) and optional provider-neutral parameters. The
dispatcher prefers OpenAI then Anthropic for coding, Anthropic then OpenAI for
reasoning, Gemini for large-context, OpenRouter for cheap/free, and DeepSeek
for fast/cheap. Other tasks use `FORGE_DEFAULT_PROVIDER`. A caller can select a
provider explicitly with `provider_name`; the existing `agent_name` interface
also remains available. The dispatcher starts with its first choice. When a
fallback chain is configured, missing or failed providers advance through that
finite sequence; exhaustion returns a structured task failure. Startup and
dispatcher tests make no real API calls.

## Provider Fallback v0.1

An optional ordered `FORGE_PROVIDER_FALLBACK_CHAIN` environment setting lists
providers tried after an unavailable or failed primary, for example
`anthropic,deepseek`. The chain is finite, sequential, and does not score by
cost or quality. An explicit `provider_name` or `agent_name` is never switched
to another provider. When every configured choice fails, dispatch returns a
failed `TaskResult` with the reasons from the attempts.

## Provider Capabilities v0.1

The runtime exposes declarative capabilities for each provider: key variable
name, general streaming/tool support, a coarse cost tier, and the existing
configuration enabled flag. These labels do not calculate cost or affect
actual token billing. Tool support can vary by model, and metadata does not
mean the current Forge AI adapter implements streaming or tool execution.

## Cost-aware Provider Routing v0.1

For ordinary (`other`) tasks, registered providers are considered in `free`,
`cheap`, then `paid` order using their capability metadata. Paid providers are
excluded unless `FORGE_ALLOW_PAID_PROVIDERS=true`. The configured fallback
chain remains in effect after eligible cost-tier candidates. An explicit
`provider_name` takes priority over the cost policy. This policy uses only
coarse tiers; it does not calculate token costs or score quality or latency.

## Long-term purpose and architecture

The planned system will coordinate multiple agents through a central
orchestrator. Provider adapters isolate provider-specific behavior for
OpenAI, Anthropic, Gemini, xAI, DeepSeek, OpenRouter, and Groq. Supporting modules
will handle tasks, project memory, context and token use, reviews, testing, and
controlled changes. See [the architecture plan](docs/ARCHITECTURE.md) and
[planned agent roles](docs/AGENTS.md).

Git will be the source of truth for project history, and significant changes
should be traceable. Secrets must never be committed to Git; keep credentials
in environment variables or an appropriately secured local environment. The
`.env.example` file contains empty placeholders only.
Store local provider keys in the ignored root `.env` file. The shared
`SecretStore` reads them only when a provider requests a key; process
environment variables take precedence. Never commit `.env`.

## Getting started

Run the startup health check from the repository root:

```bash
python -m app
```

Run the basic test with:

```bash
python -m unittest discover -s tests
```

To run a real OpenAI request manually, install `requirements.txt`, set
`OPENAI_API_KEY` and `FORGE_DEFAULT_PROVIDER=openai` in your local environment,
set `FORGE_DEFAULT_MODEL` to an available model, then dispatch a task explicitly
to agent `openai`. Do not put the key in source files or commit it.

---

# Forge AI — Русская версия

Forge AI — это планируемая к созданию универсальная платформа для координации
разработки программного обеспечения с помощью ИИ, его проверки, тестирования и
улучшения в разных проектах.

## Этап разработки

В репозитории находятся начальный каркас, Orchestrator v0.1, Provider Layer
v0.1, Runtime & Configuration Layer v0.1 и Execution Pipeline v0.1. Здесь есть
провайдер-независимая диспетчеризация задач, интерфейсы провайдеров и автономный
`MockProvider`. OpenAI Provider v0.1 использует официальный Python SDK OpenAI и
Responses API, когда задача явно направлена провайдеру OpenAI. Ключ
`OPENAI_API_KEY` читается из локального окружения процесса только во время
генерации; запуск приложения и автономные тесты не выполняют API-запросов.
Anthropic Provider v0.1 использует официальный Python SDK Anthropic и Messages
API при явной диспетчеризации. Провайдеры DeepSeek и OpenRouter используют
имеющийся OpenAI SDK и совместимые с ним Chat Completions API. Все ключи
провайдеров разрешаются через `SecretStore` во время генерации. Google/Gemini и
xAI пока не настроены. Настройки runtime проверяются по переменным `FORGE_*`.
Forge AI не изменяет проекты. Для проекта требуются SDK OpenAI и Anthropic.

## Долгосрочная цель и архитектура

Планируется, что система будет координировать работу нескольких агентов через
центральный оркестратор. Адаптеры провайдеров изолируют особенности работы с
OpenAI, Anthropic, Gemini, xAI, DeepSeek и OpenRouter. Вспомогательные
модули будут отвечать за задачи, память проектов, контекст и расход токенов,
проверки, тестирование и контролируемое внесение изменений. См. [план
архитектуры](docs/ARCHITECTURE.md) и [планируемые роли агентов](docs/AGENTS.md).

Git будет источником достоверной истории проекта, а значимые изменения должны
быть отслеживаемыми. Секреты нельзя добавлять в Git; храните учетные данные в
переменных окружения или в надлежащим образом защищенном локальном окружении.
Файл `.env.example` содержит только пустые поля-заполнители.
Локальные ключи провайдеров храните в игнорируемом корневом файле `.env`.
Общий `SecretStore` читает их только по запросу провайдера; переменные
окружения процесса имеют приоритет. Не коммитьте `.env`.

## Начало работы

Запустите проверку работоспособности из корневой папки репозитория:

```bash
python -m app
```

Запустите базовый тест командой:

```bash
python -m unittest discover -s tests
```

Чтобы вручную выполнить реальный запрос OpenAI, установите зависимости из
`requirements.txt`, задайте `OPENAI_API_KEY` и `FORGE_DEFAULT_PROVIDER=openai` в
локальном окружении, а в `FORGE_DEFAULT_MODEL` укажите доступную модель. Затем
явно направьте задачу агенту `openai`. Не помещайте ключ в исходники и не
коммитьте его.
