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
startup and offline tests make no API calls. Anthropic, Google, and xAI remain
Google and xAI remain unconfigured. Anthropic Provider v0.1 uses the official
Anthropic Python SDK Messages API only when explicitly dispatched and reads
`ANTHROPIC_API_KEY` through `SecretStore`. Runtime settings are validated from
`FORGE_*` environment variables. Forge AI does not modify projects. The
project requires the OpenAI and Anthropic SDKs for their provider integrations.

## Long-term purpose and architecture

The planned system will coordinate multiple agents through a central
orchestrator. Provider adapters will isolate provider-specific behavior for
OpenAI/Codex, Anthropic/Claude, Google/Gemini, and xAI/Grok. Supporting modules
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
Google и xAI пока не настроены. Anthropic Provider v0.1 использует официальный
Python SDK Anthropic и Messages API только при явной диспетчеризации задачи;
ключ `ANTHROPIC_API_KEY` он получает через `SecretStore`. Настройки runtime
проверяются по переменным `FORGE_*`. Forge AI не изменяет проекты. Для
провайдеров OpenAI и Anthropic требуются их SDK.

## Долгосрочная цель и архитектура

Планируется, что система будет координировать работу нескольких агентов через
центральный оркестратор. Адаптеры провайдеров будут изолировать особенности
работы с OpenAI/Codex, Anthropic/Claude, Google/Gemini и xAI/Grok. Вспомогательные
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
