# Forge AI

Forge AI is a planned **AI Engineering Factory**: a platform that turns a human
goal, an existing codebase, a website, or a dataset into a specified,
implemented, verified, ready-to-run project — and then preserves what it learned
as reviewed, reusable knowledge.

It is deliberately **not** an AI coding assistant, not a multi-model chat, not a
proxy, and not an IDE plugin. Forge is the engineering layer around models: it
discovers, specifies, plans, executes under explicit authorization, verifies
against acceptance criteria, and promotes knowledge only after review.

Long-term lifecycle (see [`FORGE_VISION.md`](docs/FORGE_VISION.md)):

```text
Human Goal / Existing Software / Website / Data
        -> Discovery -> Evidence + Unknowns -> Project Brief
        -> Requirements + Acceptance Criteria -> Decisions / Approvals
        -> Architecture -> Technical Specification -> Plan
        -> Implementation -> Review -> Verification
        -> Ready-to-run Project -> Project Memory
        -> Candidate Forge Knowledge -> Evaluation -> Approved Forge Knowledge
```

## Current state

Forge AI is at an **orchestration, execution and governance foundation** stage.
Implemented and verified today:

- provider-neutral task dispatch with seven real provider integrations plus an
  offline Mock provider, deterministic classification and cost-aware routing,
  and a finite fallback chain;
- primary + independent reviewer execution with one bounded revision;
- a deterministic template Planner (static plans only, no execution);
- a Decision layer that produces advisory run-control recommendations with **no
  execution authority**, a Decision Context Envelope, and a bounded Agent Harness;
- an Agent Skill System;
- an Execution Plane with an **Execution Authorization Contract v0.2**:
  immutable `ExecutionIntent` with a canonical fingerprint, canonical path
  security, default-deny Permission, intent-bound single-use Approval,
  capability-classified invocation policy, mandatory explicit workspace root, and
  an `AuthorizedExecution` marker the local adapter requires before spawning;
- verification contract with structured evidence, acceptance gate, requirement
  traceability, change sets, project snapshots and an integration failure matrix;
- Project Memory and Knowledge Governance.

Not implemented: the end-to-end lifecycle above, Discovery, the Forge API,
desktop/web clients, a setup wizard, durable/resumable runs, task queues,
parallel project execution, and any OS-level sandbox. The execution plane has no
production entry point yet.

**Authoritative status:** [`docs/SOURCE_OF_TRUTH.md`](docs/SOURCE_OF_TRUTH.md) is
the verified, per-area index of what exists, what is partial, and what is
planned. Read it before trusting any other description of "current state".

## High-level architecture

```text
User / Goal
   -> Dispatcher -> Provider adapters (OpenAI, Anthropic, Gemini, DeepSeek,
                    OpenRouter, Groq, Together, Mock) -- provider-neutral Core
   -> Orchestrator / Agents / Skills
   -> Decision layer (advisory only) -> Agent Harness (bounded run loop)
   -> Execution Plane: Permission -> ExecutionIntent -> Approval -> Policy
                       -> AuthorizedExecution -> local backend (shell=False)
   -> Verification -> Acceptance Gate -> Project State / Trace
   -> Project Memory / Knowledge Governance
```

Core invariants: `Provider != Model != Agent != Skill != Capability != Tool`;
`Recommendation != Authorization != Execution`; AI output never grants execution
authority; Forge Core stays provider-neutral; generated projects stay independent
of Forge Core; secrets never enter source, Git, context, logs, or prompts;
external skills/tools are untrusted until evaluated; MCP is not the permission
model; more agents does not mean better results; retries, rollback, parallelism
and swarm execution are not universalized.

## Getting started

Requires Python 3 and the provider SDKs in `requirements.txt`.

```bash
python -m app                                  # startup health check, no API calls
python -m unittest discover tests              # full test suite (offline)
```

Real-provider checks are explicit opt-ins and make real requests:
`python -m app.smoke_openrouter`, `python -m app.smoke_gemini`. Keys are read from
the ignored root `.env` or the process environment through `SecretStore`; never
commit `.env` or any credential.

## Documentation / Документация

All durable Forge documentation is bilingual. English is the canonical technical
text; the Russian counterpart lives in the **same file**, after the English,
introduced by a heading that ends with `— русская версия` (`Forge AI — Русская
версия` for this README). There are no separate `_RU.md` files by design: keeping
both languages in one file prevents the two versions from drifting apart.

Вся постоянная документация Forge двуязычна. Канонический технический текст —
английский; русская версия находится в **том же файле**, после английской, и
начинается с заголовка, оканчивающегося на `— русская версия` (`Forge AI —
Русская версия` для этого README). Отдельных файлов `_RU.md` намеренно нет: два
языка в одном файле не дают версиям разойтись.

| Document / Документ | English | Русская версия |
| --- | --- | --- |
| Repository rules / правила репозитория | `AGENTS.md` | `AGENTS.md` → section «Правила разработки Forge AI — русская версия» |
| Verified current state / проверенное текущее состояние | [`docs/SOURCE_OF_TRUTH.md`](docs/SOURCE_OF_TRUTH.md) | same file → «Source of Truth: статус реализации — русская версия» |
| Current architecture / текущая архитектура | [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | same file → per-section `— русская версия` sections |
| Durable decisions / устойчивые решения | [`docs/DECISIONS.md`](docs/DECISIONS.md) | same file → «Execution Authorization Contract v0.2 — русская версия» |
| Implementation order / порядок реализации | [`docs/ROADMAP.md`](docs/ROADMAP.md) | same file → «Forge AI — Roadmap — русская версия» |
| Technology candidates / технологические кандидаты | [`docs/TECHNOLOGY_RADAR.md`](docs/TECHNOLOGY_RADAR.md) | same file → «Technology Radar — русская версия» |
| Target architecture / целевая архитектура | [`docs/TECHNICAL_SPECIFICATION.md`](docs/TECHNICAL_SPECIFICATION.md) | same file → «Technical Specification v1.0 — русская версия (разделы 1–20)» + «русская версия дополнения Stage 1.3» |
| Product vision / продуктовое видение | [`docs/FORGE_VISION.md`](docs/FORGE_VISION.md) | same file → «Product Vision — русская версия» |
| Project continuity / передача контекста | [`docs/PROJECT_CONTINUITY.md`](docs/PROJECT_CONTINUITY.md) | same file → «Непрерывность проекта — русская версия» |

## Documentation map

| Document | Purpose |
| --- | --- |
| [`docs/SOURCE_OF_TRUTH.md`](docs/SOURCE_OF_TRUTH.md) | Verified current state, per-area evidence, known limitations, planned/rejected items. |
| [`docs/FORGE_VISION.md`](docs/FORGE_VISION.md) | Product direction and long-term lifecycle. |
| [`docs/TECHNICAL_SPECIFICATION.md`](docs/TECHNICAL_SPECIFICATION.md) | Target architecture and constraints (future design). |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Current architecture and implemented behavior. |
| [`docs/ROADMAP.md`](docs/ROADMAP.md) | Implementation order and progress. |
| [`docs/DECISIONS.md`](docs/DECISIONS.md) | Durable decisions and rejected alternatives. |
| [`docs/PROJECT_CONTINUITY.md`](docs/PROJECT_CONTINUITY.md) | Owner's product intent, stage gates, open blockers, and handoff checklist across chats/agents. |
| [`docs/TECHNOLOGY_RADAR.md`](docs/TECHNOLOGY_RADAR.md) | External technology candidates and their rings. |
| [`AGENTS.md`](AGENTS.md) | Operating contract for AI agents working in this repository. |
| [`docs/reviews/`](docs/reviews/README.md), [`docs/benchmarks/`](docs/benchmarks/README.md) | External reviews and benchmark reports (working artifacts; see the directory READMEs for their language policy). |
| [`docs/STAGE_17_FORGE_EXECUTION_PLANE.md`](docs/STAGE_17_FORGE_EXECUTION_PLANE.md) and the other `STAGE_*` files | Per-stage technical records (bilingual). |

## What is being built next

The next authorization block corrects the known limits of the v0.2 contract:
replace the capability recognition list with a fail-closed declared-invocation
model, make the coordinator's intent-fingerprint verification unconditional,
include the resolved profile environment in the intent, and decide
program-identity binding. Block 2 (workspace identity, declared inputs,
deny-by-default staging, secret exclusion, and any sandbox) remains deferred. See
[`docs/ROADMAP.md`](docs/ROADMAP.md) and `docs/DECISIONS.md` ("Execution
Authorization Contract v0.2" -> open items).

## Historical record — provider layer

This section records completed stages. It is history, not future work.

### Development stage

This repository contains the scaffold, Orchestrator v0.1, Provider
Layer v0.1, Runtime & Configuration Layer v0.1, and Execution Pipeline v0.1.
It includes provider-neutral task dispatch, provider interfaces, and an offline
`MockProvider`. The OpenAI Provider v0.1 uses the official OpenAI Python SDK
and Responses API when an OpenAI task is explicitly dispatched. It reads
`OPENAI_API_KEY` from the local process environment only during generation;
startup and offline tests make no API calls. Anthropic Provider v0.1 uses the
official Anthropic Python SDK Messages API when explicitly dispatched. The
DeepSeek and OpenRouter providers use the existing OpenAI SDK with their
OpenAI-compatible Chat Completions APIs. All provider keys are resolved through
`SecretStore` at generation time. Gemini uses Google's official `google-genai`
SDK; xAI remains unconfigured. Runtime settings are validated from `FORGE_*`
environment variables. The project requires the OpenAI, Anthropic, and Google
Gen AI SDKs.

Groq uses the existing OpenAI-compatible client with the official Groq endpoint
and `GROQ_API_KEY`. Optional provider account emails live in a separate local
`ProviderAccountConfig`; they are not API credentials and are not included in
provider results or logs.

### Dispatcher v0.2

Tasks carry a category (`coding`, `reasoning`, `large-context`, `cheap/free`,
`fast/cheap`, or `other`) and optional provider-neutral parameters. The
dispatcher prefers OpenAI then Anthropic for coding, Anthropic then OpenAI for
reasoning, Gemini for large-context, OpenRouter for cheap/free, and DeepSeek
for fast/cheap. Other tasks use `FORGE_DEFAULT_PROVIDER`. A caller can select a
provider explicitly with `provider_name`; the existing `agent_name` interface
also remains available. Automatic routing skips providers disabled by
`FORGE_ENABLED_PROVIDERS`, providers without their configured API key, providers
that lack task-required capabilities, and paid providers unless
`FORGE_ALLOW_PAID_PROVIDERS=true`. The default enabled set is `mock,openrouter`;
OpenRouter's configured free model is treated as free for routing. Set
`FORGE_ENABLED_PROVIDERS=mock,openrouter,google` to enable Gemini routing too,
and set `FORGE_GEMINI_MODEL` to choose its model. Large-context tasks use Gemini
only when enabled, configured with `GEMINI_API_KEY`, and permitted by cost
policy. Provider preferences and the bounded fallback chain are deterministic;
startup and unit tests make no real API calls.

### Provider Fallback v0.1

An optional ordered `FORGE_PROVIDER_FALLBACK_CHAIN` environment setting lists
providers tried after an unavailable or failed primary, for example
`anthropic,deepseek`. The chain is finite, sequential, and does not score by
cost or quality. An explicit `provider_name` or `agent_name` is never switched
to another provider. When every configured choice fails, dispatch returns a
failed `TaskResult` with the reasons from the attempts.

### Provider Capabilities v0.1

The runtime exposes declarative capabilities for each provider: key variable
name, general streaming/tool support, a coarse cost tier, and the existing
configuration enabled flag. These labels do not calculate cost or affect
actual token billing. Tool support can vary by model, and metadata does not
mean the current Forge AI adapter implements streaming or tool execution.

### Cost-aware Provider Routing v0.1

For ordinary (`other`) tasks, registered providers are considered in `free`,
`cheap`, then `paid` order using their capability metadata. Paid providers are
excluded unless `FORGE_ALLOW_PAID_PROVIDERS=true`. The configured fallback
chain remains in effect after eligible cost-tier candidates. An explicit
`provider_name` takes priority over the cost policy. This policy uses only
coarse tiers; it does not calculate token costs or score quality or latency.

### Long-term purpose and architecture

The planned system coordinates multiple agents through a central
orchestrator. Provider adapters isolate provider-specific behavior for
OpenAI, Anthropic, Gemini, xAI, DeepSeek, OpenRouter, and Groq. Supporting
modules handle tasks, project memory, context and token use, reviews, testing,
and controlled changes. See [the architecture](docs/ARCHITECTURE.md) and
[planned agent roles](docs/AGENTS.md).

Git is the source of truth for project history, and significant changes
are traceable. Secrets must never be committed to Git; keep credentials
in environment variables or an appropriately secured local environment. The
`.env.example` file contains empty placeholders only. Store local provider keys
in the ignored root `.env` file. The shared `SecretStore` reads them only when a
provider requests a key; process environment variables take precedence. Never
commit `.env`.

### Provider smoke commands

To run a real OpenAI request manually, install `requirements.txt`, set
`OPENAI_API_KEY` and `FORGE_DEFAULT_PROVIDER=openai` in your local environment,
set `FORGE_DEFAULT_MODEL` to an available model, then dispatch a task explicitly
to agent `openai`. Do not put the key in source files or commit it.

To check the OpenRouter integration with one real request through Forge AI, set
`OPENROUTER_API_KEY` in the ignored local `.env` or process environment, then
run:

```bash
python -m app.smoke_openrouter
```

The command explicitly dispatches to OpenRouter and uses
`FORGE_OPENROUTER_MODEL`, which defaults to `cohere/north-mini-code:free`.
Unlike unit tests and normal startup, this smoke command sends a real API
request. It prints only a static pass/fail message, not the response or secret.

To send one real Gemini request through Forge AI, enable Google in
`FORGE_ENABLED_PROVIDERS`, set `GEMINI_API_KEY` through the ignored local `.env`
or process environment, and run:

```bash
python -m app.smoke_gemini
```

The model comes from `FORGE_GEMINI_MODEL` (default `gemini-3.8-flash`). The
smoke command prints only a static pass/fail message.

---

# Forge AI — Русская версия

Forge AI — планируемая **AI Engineering Factory**: платформа, превращающая цель
человека, существующий код, сайт или данные в специфицированный, реализованный,
проверенный и готовый к запуску проект, а затем сохраняющая полученные знания как
проверенные и переиспользуемые.

Forge сознательно **не** является AI-ассистентом для кодинга, не мультимодельным
чатом, не прокси и не плагином IDE. Forge — инженерный слой вокруг моделей:
discovery, спецификация, планирование, выполнение под явной авторизацией,
верификация по критериям приёмки и продвижение знаний только после review.

Долгосрочный жизненный цикл (см. [`FORGE_VISION.md`](docs/FORGE_VISION.md)):

```text
Цель человека / существующее ПО / сайт / данные
        -> Discovery -> Evidence + неизвестные -> Project Brief
        -> Требования + критерии приёмки -> Решения / одобрения
        -> Архитектура -> Техническая спецификация -> План
        -> Реализация -> Review -> Верификация
        -> Готовый к запуску проект -> Project Memory
        -> Кандидаты в Forge Knowledge -> Оценка -> Утверждённые Forge Knowledge
```

## Текущее состояние

Сейчас Forge AI находится на стадии **фундамента оркестрации, выполнения и
управления знаниями**. Реализовано и проверено:

- провайдер-нейтральная диспетчеризация с шестью реальными провайдерами и offline
  Mock-провайдером, детерминированная классификация и cost-aware маршрутизация,
  конечная цепочка fallback;
- primary + независимый reviewer с одной ограниченной ревизией;
- детерминированный шаблонный Planner (только статические планы);
- Decision-слой, выдающий только рекомендации и **не имеющий полномочий
  выполнения**, конверт контекста решений и ограниченный Agent Harness;
- Agent Skill System;
- Execution Plane с **Execution Authorization Contract v0.2**: неизменяемый
  `ExecutionIntent` с каноническим fingerprint, каноническая безопасность путей,
  default-deny Permission, привязанное к интенту одноразовое Approval,
  capability-классификация инвокаций, обязательный явный workspace root и маркер
  `AuthorizedExecution`, требуемый локальным адаптером перед запуском процесса;
- контракт верификации со структурированными свидетельствами, acceptance gate,
  трассируемость требований, changesets, снапшоты и матрица отказов интеграции;
- Project Memory и Knowledge Governance.

Не реализовано: сквозной жизненный цикл выше, Discovery, Forge API, desktop/web
клиенты, setup wizard, долговременные/возобновляемые запуски, очереди задач,
параллельное выполнение проектов и любая OS-песочница. У execution plane пока нет
production-точки входа.

**Достоверный статус:** [`docs/SOURCE_OF_TRUTH.md`](docs/SOURCE_OF_TRUTH.md) —
проверенный индекс по областям: что есть, что частично, что запланировано. Читайте
его до того, как доверять любому другому описанию «текущего состояния».

## Архитектура (верхний уровень)

```text
Пользователь / Цель
   -> Dispatcher -> адаптеры провайдеров (OpenAI, Anthropic, Gemini, DeepSeek,
                    OpenRouter, Groq, Together, Mock) -- провайдер-нейтральное ядро
   -> Orchestrator / агенты / Skills
   -> Decision-слой (только рекомендации) -> Agent Harness (ограниченный цикл)
   -> Execution Plane: Permission -> ExecutionIntent -> Approval -> Policy
                       -> AuthorizedExecution -> локальный backend (shell=False)
   -> Верификация -> Acceptance Gate -> Project State / Trace
   -> Project Memory / Knowledge Governance
```

Ключевые инварианты: `Provider != Model != Agent != Skill != Capability != Tool`;
`Recommendation != Authorization != Execution`; вывод ИИ никогда не выдаёт
полномочия на выполнение; ядро Forge остаётся провайдер-нейтральным; генерируемые
проекты остаются независимыми от Forge Core; секреты не попадают в исходники, Git,
контекст, логи и промпты; внешние skills/tools недоверенны до оценки; MCP не
является моделью разрешений; больше агентов не означает лучший результат;
retries, rollback, параллелизм и swarm-исполнение не универсализируются.

## Начало работы

Требуется Python 3 и SDK провайдеров из `requirements.txt`.

```bash
python -m app                                  # проверка запуска, без API-вызовов
python -m unittest discover tests              # полный набор тестов (offline)
```

Проверки с реальными провайдерами — явный opt-in и выполняют реальные запросы:
`python -m app.smoke_openrouter`, `python -m app.smoke_gemini`. Ключи читаются из
игнорируемого корневого `.env` или окружения процесса через `SecretStore`; никогда
не коммитьте `.env` и любые учётные данные.

## Документация / Documentation

Вся постоянная документация Forge двуязычна. Канонический технический текст —
английский; русская версия находится в **том же файле**, после английской, и
начинается с заголовка, оканчивающегося на `— русская версия` (`Forge AI —
Русская версия` для этого README). Отдельных файлов `_RU.md` намеренно нет: два
языка в одном файле не дают версиям разойтись.

All durable Forge documentation is bilingual. English is the canonical technical
text; the Russian counterpart lives in the same file, after the English,
introduced by a heading ending with `— русская версия`.

| Документ / Document | English | Русская версия |
| --- | --- | --- |
| Правила репозитория | `AGENTS.md` | `AGENTS.md` → раздел «Правила разработки Forge AI — русская версия» |
| Проверенное текущее состояние | [`docs/SOURCE_OF_TRUTH.md`](docs/SOURCE_OF_TRUTH.md) | тот же файл → «Source of Truth: статус реализации — русская версия» |
| Текущая архитектура | [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | тот же файл → разделы `— русская версия` |
| Устойчивые решения | [`docs/DECISIONS.md`](docs/DECISIONS.md) | тот же файл → «Execution Authorization Contract v0.2 — русская версия» |
| Порядок реализации | [`docs/ROADMAP.md`](docs/ROADMAP.md) | тот же файл → «Forge AI — Roadmap — русская версия» |
| Технологические кандидаты | [`docs/TECHNOLOGY_RADAR.md`](docs/TECHNOLOGY_RADAR.md) | тот же файл → «Technology Radar — русская версия» |
| Целевая архитектура | [`docs/TECHNICAL_SPECIFICATION.md`](docs/TECHNICAL_SPECIFICATION.md) | тот же файл → «Technical Specification v1.0 — русская версия (разделы 1–20)» + «русская версия дополнения Stage 1.3» |
| Продуктовое видение | [`docs/FORGE_VISION.md`](docs/FORGE_VISION.md) | тот же файл → «Product Vision — русская версия» |

## Карта документации

| Документ | Назначение |
| --- | --- |
| [`docs/SOURCE_OF_TRUTH.md`](docs/SOURCE_OF_TRUTH.md) | Проверенное текущее состояние, свидетельства по областям, известные ограничения, планы и отклонённые решения. |
| [`docs/FORGE_VISION.md`](docs/FORGE_VISION.md) | Направление продукта и долгосрочный жизненный цикл. |
| [`docs/TECHNICAL_SPECIFICATION.md`](docs/TECHNICAL_SPECIFICATION.md) | Целевая архитектура и ограничения (будущий дизайн). |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Текущая архитектура и реализованное поведение. |
| [`docs/ROADMAP.md`](docs/ROADMAP.md) | Порядок реализации и прогресс. |
| [`docs/DECISIONS.md`](docs/DECISIONS.md) | Устойчивые решения и отклонённые альтернативы. |
| [`docs/TECHNOLOGY_RADAR.md`](docs/TECHNOLOGY_RADAR.md) | Внешние технологические кандидаты и их категории. |
| [`AGENTS.md`](AGENTS.md) | Рабочий контракт для AI-агентов в этом репозитории. |
| [`docs/reviews/`](docs/reviews/README.md), [`docs/benchmarks/`](docs/benchmarks/README.md) | Внешние ревью и benchmark-отчёты (рабочие артефакты; политика языка описана в README соответствующих каталогов). |
| [`docs/STAGE_17_FORGE_EXECUTION_PLANE.md`](docs/STAGE_17_FORGE_EXECUTION_PLANE.md) и остальные файлы `STAGE_*` | Технические записи по стадиям (двуязычные). |

## Что делается далее

Следующий блок авторизации исправляет известные ограничения контракта v0.2:
замена списка распознавания capabilities на fail-closed модель объявленных
инвокаций, безусловная проверка fingerprint интента координатором, включение
разрешённого окружения профиля в интент и решение о привязке идентичности
программы. Block 2 (идентичность workspace, объявленные входы, deny-by-default
подготовка, исключение секретов и любая песочница) остаётся отложенным. См.
[`docs/ROADMAP.md`](docs/ROADMAP.md) и `docs/DECISIONS.md` (раздел
"Execution Authorization Contract v0.2" -> открытые вопросы).

## Историческая запись — слой провайдеров

Этот раздел фиксирует пройденные этапы. Это история, а не будущая работа.

### Этап разработки

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
провайдеров разрешаются через `SecretStore` во время генерации. Gemini использует
официальный SDK Google `google-genai`; xAI остаётся ненастроенным. Настройки
runtime проверяются по переменным `FORGE_*`.

Groq использует существующий OpenAI-совместимый клиент с официальным эндпоинтом
Groq и `GROQ_API_KEY`. Необязательные email-адреса провайдерских аккаунтов
хранятся отдельно в локальном `ProviderAccountConfig`; это не API-credentials, и
они не попадают в результаты провайдеров и в логи.

### Долгосрочная цель и архитектура

Планируется, что система будет координировать работу нескольких агентов через
центральный оркестратор. Адаптеры провайдеров изолируют особенности работы с
OpenAI, Anthropic, Gemini, xAI, DeepSeek, OpenRouter и Groq. Вспомогательные
модули будут отвечать за задачи, память проектов, контекст и расход токенов,
проверки, тестирование и контролируемое внесение изменений. См. [архитектуру](docs/ARCHITECTURE.md)
и [планируемые роли агентов](docs/AGENTS.md).

Git является источником достоверной истории проекта, а значимые изменения должны
быть отслеживаемыми. Секреты нельзя добавлять в Git; храните учётные данные в
переменных окружения или в надлежащим образом защищённом локальном окружении.
Файл `.env.example` содержит только пустые поля-заполнители. Локальные ключи
провайдеров храните в игнорируемом корневом файле `.env`. Общий `SecretStore`
читает их только по запросу провайдера; переменные окружения процесса имеют
приоритет. Не коммитьте `.env`.

### Smoke-команды провайдеров

Чтобы вручную выполнить реальный запрос OpenAI, установите зависимости из
`requirements.txt`, задайте `OPENAI_API_KEY` и `FORGE_DEFAULT_PROVIDER=openai` в
локальном окружении, а в `FORGE_DEFAULT_MODEL` укажите доступную модель. Затем
явно направьте задачу агенту `openai`. Не помещайте ключ в исходники и не
коммитьте его.

Чтобы проверить интеграцию с OpenRouter одним реальным запросом через Forge AI,
задайте `OPENROUTER_API_KEY` в игнорируемом локальном `.env` или в окружении
процесса и выполните `python -m app.smoke_openrouter`. Модель берётся из
`FORGE_OPENROUTER_MODEL` (по умолчанию `cohere/north-mini-code:free`). Команда
печатает только статическое сообщение об успехе или ошибке, без ответа и секрета.

Чтобы отправить один реальный запрос Gemini, включите Google в
`FORGE_ENABLED_PROVIDERS`, задайте `GEMINI_API_KEY` через игнорируемый `.env` или
окружение процесса и выполните `python -m app.smoke_gemini`. Модель берётся из
`FORGE_GEMINI_MODEL` (по умолчанию `gemini-3.8-flash`).
