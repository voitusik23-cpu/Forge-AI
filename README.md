# Forge AI

Forge AI is a planned reusable platform for coordinating AI-assisted software
development, review, testing, and improvement across projects.

## Development stage

This repository contains the initial scaffold, Orchestrator v0.1, and Provider
Layer v0.1. It includes a Python startup health check, provider-neutral task
dispatch and provider interfaces, safe provider configuration placeholders,
an explicit Runtime & Configuration Layer v0.1, and an offline `MockProvider`.
Runtime settings are validated from `FORGE_*` environment variables; API keys
are not loaded. Provider placeholders for OpenAI, Anthropic,
Google, and xAI do not make API calls; real integrations and SDKs are not
configured. Forge AI does not modify projects. The scaffold uses only the
Python standard library. The synchronous Execution Pipeline v0.1 connects Task,
Orchestrator, Agent, and Provider, and can complete an offline task through
`MockProvider`.

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

## Getting started

Run the startup health check from the repository root:

```bash
python -m app
```

Run the basic test with:

```bash
python -m unittest discover -s tests
```

---

# Forge AI — Русская версия

Forge AI — это планируемая к созданию универсальная платформа для координации
разработки программного обеспечения с помощью ИИ, его проверки, тестирования и
улучшения в разных проектах.

## Этап разработки

В этом репозитории находятся начальный каркас, Orchestrator v0.1 и Provider
Layer v0.1. В него входят простая проверка запуска Python-приложения,
провайдер-независимая диспетчеризация задач и интерфейсы провайдеров, явный
Runtime & Configuration Layer v0.1 и автономный `MockProvider`. Настройки
runtime валидируются из переменных окружения `FORGE_*`; API keys не загружаются.
Заглушки для OpenAI,
Anthropic, Google и xAI не выполняют API-запросы; реальные интеграции и SDK не
подключены. Forge AI не изменяет проекты. Каркас использует только стандартную
библиотеку Python. Синхронный Execution Pipeline v0.1 связывает Task,
Orchestrator, Agent и Provider и выполняет автономную тестовую задачу через
`MockProvider`.

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

## Начало работы

Запустите проверку работоспособности из корневой папки репозитория:

```bash
python -m app
```

Запустите базовый тест командой:

```bash
python -m unittest discover -s tests
```
