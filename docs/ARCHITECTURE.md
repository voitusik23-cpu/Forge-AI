# Planned architecture

The long-term boundaries and principles are specified in
[`TECHNICAL_SPECIFICATION.md`](TECHNICAL_SPECIFICATION.md); this document
describes the current architecture and implemented behavior.

```text
User
  ↓
Forge AI Orchestrator
  ↓
Agent adapters
  ↓
OpenAI / Anthropic / Gemini / xAI / DeepSeek / OpenRouter
  ↓
Review / Testing
  ↓
Controlled changes
  ↓
Git
```

The orchestrator will coordinate tasks, manage the context provided to agents,
and route work. Provider-specific behavior, request formats, and response
handling must remain isolated behind agent/provider adapters. Adding or changing
a provider should not require rewriting orchestration logic.

Proposed supporting modules cover tasks, project memory, project-specific
knowledge, reviews, tools, and shared utilities. Changes should be reviewed and
validated before controlled application to a project. Git will keep the
authoritative project history and make significant changes traceable.

## Orchestrator v0.1

The initial orchestration core uses provider-neutral task and result models, an
agent interface, an in-memory registry, and a local mock agent. Dispatch is
explicit: the caller supplies `agent_name`; the orchestrator does not choose an
agent or automatically route between providers.

```text
Task
  -> Orchestrator
  -> Registry
  -> Agent
  -> TaskResult
```

Automatic routing is introduced separately in Dispatcher v0.2. Real
integrations for providers other than OpenAI and Anthropic remain future work.


## Orchestrator v0.1 — русская версия

`Orchestrator` — ядро начальной оркестрации, использующее провайдер-нейтральные модели
задач и результатов, интерфейс агента, реестр в памяти и локального mock-агента.
Диспетчеризация явная: вызывающий передаёт `agent_name`; оркестратор не выбирает
агента и не маршрутизирует автоматически между провайдерами.

```text
Task
  -> Orchestrator
  -> Registry
  -> Agent
  -> TaskResult
```

Автоматическая маршрутизация вводится отдельно в Dispatcher v0.2. Реальные
интеграции для провайдеров, отличных от OpenAI и Anthropic, остаются будущей
работой.
## Dispatcher v0.2

`Task` carries a coarse `TaskCategory` and a provider-neutral `parameters`
mapping (including an optional model override and required capabilities).
`Dispatcher` applies a deterministic preference order, checks candidate
availability/configuration/capabilities, and tries each eligible candidate at
most once through the existing `AgentRegistry` and `Provider` adapter:

```text
Task(category, parameters)
  -> Orchestrator
  -> Dispatcher / DispatchPolicy
  -> AgentRegistry
  -> ProviderAgent
  -> Provider interface
  -> TaskResult
```

Coding prefers OpenAI then Anthropic; reasoning prefers Anthropic then OpenAI;
large-context uses Google/Gemini; cheap/free uses OpenRouter; fast/cheap uses
DeepSeek; other categories use `FORGE_DEFAULT_PROVIDER` and cost tiers. A
candidate must be enabled in `FORGE_ENABLED_PROVIDERS`, registered, have its
required API key, and support the requested task capabilities. OpenRouter's
model ID determines whether its capability tier is free (`:free` or
`openrouter/free`) or cheap. Paid candidates require
`FORGE_ALLOW_PAID_PROVIDERS=true`. After category candidates fail or are
unavailable, the finite `FORGE_PROVIDER_FALLBACK_CHAIN` is tried sequentially.
Explicit `provider_name` bypasses automatic choice and fallback; the caller's
selection is never replaced. Dispatcher uses only common registries, metadata,
and SecretStore presence checks, not provider SDKs.


## Dispatcher v0.2 — русская версия

`Task` несёт грубую `TaskCategory` и провайдер-нейтральное отображение `parameters`
(включая необязательное переопределение модели и требуемые capabilities).
`Dispatcher` применяет детерминированный порядок предпочтений, проверяет
доступность/конфигурацию/capabilities кандидата и пытается использовать каждого
подходящего кандидата не более одного раза через существующие `AgentRegistry` и
адаптер `Provider`:

```text
Task(category, parameters)
  -> Orchestrator
  -> Dispatcher / DispatchPolicy
  -> AgentRegistry
  -> ProviderAgent
  -> Provider interface
  -> TaskResult
```

Категория coding предпочитает OpenAI, затем Anthropic; reasoning — Anthropic, затем
OpenAI; large-context использует Google/Gemini; cheap/free — OpenRouter; fast/cheap —
DeepSeek; остальные категории используют `FORGE_DEFAULT_PROVIDER` и cost-tiers.
Кандидат должен быть включён в `FORGE_ENABLED_PROVIDERS`, зарегистрирован, иметь
требуемый API-ключ и поддерживать запрошенные capabilities задачи. Идентификатор
модели OpenRouter определяет, является ли её capability-tier бесплатным (`:free` или
`openrouter/free`) или cheap. Платные кандидаты требуют
`FORGE_ALLOW_PAID_PROVIDERS=true`. После отказа или недоступности кандидатов
категории последовательно перебирается конечная
`FORGE_PROVIDER_FALLBACK_CHAIN`. Явный `provider_name` обходит автоматический выбор
и fallback; выбор вызывающего никогда не подменяется. Dispatcher использует только
общие реестры, метаданные и проверку наличия через SecretStore, но не SDK
провайдеров.
## Task Classification + Specialized Routing v0.1

`TaskCategory` is shared by task models and routing. A caller-supplied category
is honored unchanged. When omitted, a small deterministic classifier examines
the task description, context, and parameters and assigns `code`, `analysis`,
`review`, or `other`. Review patterns take precedence over code patterns, then
analysis; unmatched tasks remain `other`. No model or external service is used
for classification.

For `code`, `analysis`, and `review`, the dispatcher filters providers by the
declarative `task_categories` capability metadata and then orders eligible
providers by the existing free, cheap, and permitted paid tiers. Other tasks
retain the existing cost-aware route, and legacy `TaskCategory` values remain
supported. The same configuration yields the same classification and provider
order. Explicit `provider_name` continues to override automatic routing, and
the configured finite fallback chain remains in effect after automatic
candidates. The `python -m app.smoke_classified_routing` command exercises one
real request for each new category.


## Task Classification + Specialized Routing v0.1 — русская версия

`TaskCategory` используется совместно моделями задач и маршрутизацией. Категория,
заданная вызывающим, соблюдается без изменений. Когда она не указана, небольшой
детерминированный классификатор исследует описание задачи, контекст и параметры и
назначает `code`, `analysis`, `review` или `other`. Паттерны review имеют приоритет
над паттернами code, затем над analysis; задачи без совпадений остаются `other`.
Ни модель, ни внешний сервис для классификации не используются.

Для `code`, `analysis` и `review` dispatcher фильтрует провайдеров по декларативным
метаданным capability `task_categories`, а затем упорядочивает подходящих
провайдеров по существующим уровням free, cheap и разрешённым paid. Остальные задачи
сохраняют существующий cost-aware маршрут, а legacy-значения `TaskCategory`
продолжают поддерживаться. Одна и та же конфигурация даёт одну и ту же
классификацию и один и тот же порядок провайдеров. Явный `provider_name` продолжает
переопределять автоматическую маршрутизацию, а настроенная конечная цепочка fallback
остаётся в силе после автоматических кандидатов. Команда
`python -m app.smoke_classified_routing` выполняет по одному реальному запросу для
каждой новой категории.
## Multi-Agent Execution v0.1

`MultiAgentExecutor` sends the task through the existing Orchestrator/Dispatcher
for one primary execution. On success, a provider-neutral `ProviderReviewer`
submits a `review` category task through the same routing path, with the
original task details and primary response in structured context. If the review
requests changes, the primary is invoked once more with the previous response
and review feedback, followed by one final review. The workflow never revises
more than once. The reviewer only evaluates text and cannot modify files.
Primary failure skips review; revision failure preserves the initial primary
result; reviewer failure preserves the available primary/revision results.
Statuses distinguish `approved`, `changes_requested`, `review_failed`, and
`revision_failed`. An explicit primary `provider_name` is preserved for the
revision, while each review is routed independently. No correction loop runs
after the final review.


## Multi-Agent Execution v0.1 — русская версия

`MultiAgentExecutor` отправляет задачу через существующие Orchestrator/Dispatcher для
одного выполнения primary. При успехе провайдер-нейтральный `ProviderReviewer`
подаёт задачу категории `review` через тот же путь маршрутизации, с исходными
деталями задачи и ответом primary в структурированном контексте. Если review
запрашивает изменения, primary вызывается ещё один раз с предыдущим ответом и
обратной связью ревью, после чего следует одно финальное ревью. Workflow никогда не
выполняет более одной ревизии. Reviewer только оценивает текст и не может изменять
файлы. Сбой primary пропускает ревью; сбой ревизии сохраняет исходный результат
primary; сбой reviewer сохраняет доступные результаты primary/ревизии. Статусы
различают `approved`, `changes_requested`, `review_failed` и `revision_failed`.
Явный `provider_name` primary сохраняется для ревизии, тогда как каждое ревью
маршрутизируется независимо. После финального ревью цикл коррекции не запускается.
## Planner v0.1

`Planner` maps a plain-text goal to a provider-neutral `ProjectPlan` using
deterministic templates for Telegram bots, web applications, data/analysis,
and generic software. Plans contain ordered `PlannedTask` items with stable
IDs, existing `TaskCategory` values, explicit dependencies, and `READY` or
`PENDING` initial status. The first task is ready; later tasks depend on the
previous task in the selected template. Plan IDs are stable for the same
normalized goal. This component only creates plans: it does not call a model,
dispatch tasks, track execution, or manage dependencies at runtime.


## Planner v0.1 — русская версия

`Planner` отображает цель в виде обычного текста в провайдер-нейтральный
`ProjectPlan`, используя детерминированные шаблоны для Telegram-ботов,
веб-приложений, данных/анализа и общего ПО. Планы содержат упорядоченные элементы
`PlannedTask` со стабильными ID, существующими значениями `TaskCategory`, явными
зависимостями и начальным статусом `READY` или `PENDING`. Первая задача готова;
последующие задачи зависят от предыдущей задачи в выбранном шаблоне. ID плана
стабильны для одной и той же нормализованной цели. Этот компонент только создаёт
планы: он не вызывает модель, не диспетчеризует задачи, не отслеживает выполнение и
не управляет зависимостями во время выполнения.
## Provider Layer v0.1

The provider layer sits behind the existing Agent interface. The orchestrator
continues to dispatch to an explicitly selected agent; a generic `ProviderAgent`
adapts a task into a provider-neutral request and adapts the response back into
a `TaskResult`.

```text
User
  -> Orchestrator
  -> Agent
  -> Provider
  -> AI API (future integration only)
```

- **Orchestrator** dispatches a task to the caller-selected agent and does not
  contain provider-specific behavior.
- **Agent** receives the task and owns the generic task-to-result contract.
- **Provider** accepts a provider-neutral request and returns a provider-neutral
  response; provider-specific protocol and SDK details stay behind its
  implementation.
- **AI API** integrations remain isolated inside provider implementations.

`ProviderFactory` creates a provider only when its name is explicitly supplied.
`ProviderRegistry` manages provider instances separately from `AgentRegistry`.
Provider settings store only an environment variable name as a credential
reference; they never read or store the referenced secret. `MockProvider` is
the deterministic, offline implementation for tests.


## Provider Layer v0.1 — русская версия

Слой провайдеров находится за существующим интерфейсом Agent. Оркестратор продолжает
диспетчеризацию явно выбранному агенту; обобщённый `ProviderAgent` адаптирует задачу
в провайдер-нейтральный запрос и адаптирует ответ обратно в `TaskResult`.

```text
User
  -> Orchestrator
  -> Agent
  -> Provider
  -> AI API (future integration only)
```

- **Orchestrator** диспетчеризует задачу выбранному вызывающим агенту и не содержит
  поведения, специфичного для провайдера.
- **Agent** принимает задачу и владеет обобщённым контрактом «задача -> результат».
- **Provider** принимает провайдер-нейтральный запрос и возвращает провайдер-нейтральный
  ответ; детали протокола и SDK конкретного провайдера остаются за его реализацией.
- **AI API** — интеграции остаются изолированными внутри реализаций провайдеров.

`ProviderFactory` создаёт провайдера только когда его имя передано явно.
`ProviderRegistry` управляет экземплярами провайдеров отдельно от `AgentRegistry`.
Настройки провайдера хранят только имя переменной окружения как ссылку на учётные
данные; они никогда не читают и не хранят сам секрет. `MockProvider` —
детерминированная offline-реализация для тестов.
## Runtime & Configuration Layer v0.1

The runtime is assembled explicitly and has no global singleton:

```text
Runtime
  -> Settings
  -> Registries
  -> Orchestrator
  -> Agent
  -> Provider
```

`RuntimeSettings` contains application-wide values such as environment, debug
mode, default provider/model, enabled providers, model overrides, timeout,
retry count, and log level. It is loaded
from the `FORGE_*` environment variables and validated before runtime assembly.
The configured default provider is used for tasks in the `other` category;
the v0.2 dispatcher applies the category policy for other task categories.

`ProviderConfig` remains separate and contains provider-specific metadata,
including only the name of an environment variable that may hold a credential.
Runtime settings do not load API keys, and provider configuration does not
resolve the referenced variable. Secrets are not logged or stored in Git.
Startup performs local dependency assembly only and makes no external API calls.

`ProviderAccountConfig` separately holds optional local account email metadata
for providers. It reads only `PROVIDER_*_EMAIL` values and does not put account
emails in provider responses, task results, or logs. API keys continue to be
resolved only through `SecretStore`.


## Runtime & Configuration Layer v0.1 — русская версия

Runtime собирается явно и не имеет глобального singleton:

```text
Runtime
  -> Settings
  -> Registries
  -> Orchestrator
  -> Agent
  -> Provider
```

`RuntimeSettings` содержит значения уровня приложения: окружение, режим отладки,
провайдера/модель по умолчанию, включённых провайдеров, переопределения моделей,
таймаут, число повторов и уровень логирования. Он загружается из переменных
окружения `FORGE_*` и валидируется до сборки runtime. Настроенный провайдер по
умолчанию используется для задач категории `other`; dispatcher v0.2 применяет
политику категорий для остальных категорий задач.

`ProviderConfig` остаётся отдельным и содержит метаданные, специфичные для
провайдера, включая только имя переменной окружения, которая может хранить учётные
данные. Настройки runtime не загружают API-ключи, а конфигурация провайдера не
разрешает указанную переменную. Секреты не логируются и не хранятся в Git. Запуск
выполняет только локальную сборку зависимостей и не делает внешних API-вызовов.

`ProviderAccountConfig` отдельно хранит необязательные локальные метаданные
email-адресов аккаунтов провайдеров. Он читает только значения `PROVIDER_*_EMAIL` и
не помещает email-адреса аккаунтов в ответы провайдеров, результаты задач или логи.
API-ключи по-прежнему разрешаются только через `SecretStore`.
## Execution Pipeline v0.1

```text
User
  -> Task
  -> Orchestrator
  -> ExecutionService (TaskExecutor)
  -> Agent
  -> Provider
  -> ProviderResponse
  -> TaskResult
  -> User
```

The user supplies a `Task` and explicitly names `agent_name`. The Orchestrator
delegates to `TaskExecutor`, which validates the task, looks up that one agent,
and normalizes expected domain/provider failures and empty results. It does not
select or route agents automatically. `ProviderAgent` adapts the task to a
provider-neutral request; the provider returns a `ProviderResponse` with output
and `Usage`. The agent carries provider, agent, and usage metadata into the
`TaskResult` returned to the caller. Unknown agents and invalid tasks raise
clear domain exceptions; unexpected exceptions are not swallowed.

`MockProvider` runs synchronously and offline, returning deterministic output
and zero token/cost usage. Provider-specific implementations remain behind the
Provider interface and outside the execution service.


## Execution Pipeline v0.1 — русская версия

```text
User
  -> Task
  -> Orchestrator
  -> ExecutionService (TaskExecutor)
  -> Agent
  -> Provider
  -> ProviderResponse
  -> TaskResult
  -> User
```

Пользователь передаёт `Task` и явно указывает `agent_name`. Оркестратор делегирует
`TaskExecutor`, который валидирует задачу, находит этого одного агента и
нормализует ожидаемые доменные/провайдерские отказы и пустые результаты. Он не
выбирает и не маршрутизирует агентов автоматически. `ProviderAgent` адаптирует
задачу в провайдер-нейтральный запрос; провайдер возвращает `ProviderResponse` с
выводом и `Usage`. Агент переносит метаданные провайдера, агента и использования в
`TaskResult`, возвращаемый вызывающему. Неизвестные агенты и невалидные задачи
вызывают понятные доменные исключения; неожиданные исключения не подавляются.

`MockProvider` работает синхронно и offline, возвращая детерминированный вывод и
нулевое использование токенов/стоимости. Реализации, специфичные для провайдеров,
остаются за интерфейсом Provider и вне сервиса выполнения.
## Provider Fallback v0.1

`RuntimeSettings.provider_fallback_chain`, loaded from the optional
`FORGE_PROVIDER_FALLBACK_CHAIN` comma-separated setting, supplies an ordered,
finite list tried after the category policy's primary provider. Dispatcher
checks each candidate through `ProviderRegistry` and its existing capability
metadata, then invokes its adapter through the registered agent. Dispatcher
attempts the primary once, then each distinct configured fallback at most once,
sequentially. Missing providers/agents and normalized provider execution
failures move to the next entry; success stops the chain. Exhaustion returns a
failed `TaskResult` containing the reasons. Explicit `provider_name` and legacy
`agent_name` dispatch bypass fallback. No scoring, retry loop, parallel calls,
or fallback based on cost/quality is involved.


## Provider Fallback v0.1 — русская версия

`RuntimeSettings.provider_fallback_chain`, загружаемый из необязательной настройки
`FORGE_PROVIDER_FALLBACK_CHAIN` (список через запятую), задаёт упорядоченный
конечный список, перебираемый после основного провайдера политики категории.
Dispatcher проверяет каждого кандидата через `ProviderRegistry` и его существующие
метаданные capabilities, затем вызывает его адаптер через зарегистрированного
агента. Dispatcher пытается использовать primary один раз, затем каждый отдельный
настроенный fallback не более одного раза, последовательно. Отсутствующие
провайдеры/агенты и нормализованные отказы выполнения провайдера переводят к
следующей записи; успех останавливает цепочку. Исчерпание возвращает неуспешный
`TaskResult`, содержащий причины. Явный `provider_name` и legacy-диспетчеризация по
`agent_name` обходят fallback. Никакого оценивания, цикла повторов, параллельных
вызовов или fallback по стоимости/качеству здесь нет.
## OpenAI Provider v0.1

`OpenAIProvider` uses the official OpenAI Python SDK and its Responses API. It
is called only when an explicitly dispatched task reaches the provider; runtime
startup, normal health checks, and offline tests make no API requests. The
provider reads the API key from `OPENAI_API_KEY` at generation time and never
copies it into `ProviderConfig`. The SDK key and request data are not logged.
`output_text` becomes the provider-neutral output, Responses usage supplies
input/output token counts, and estimated cost remains unset. Authentication,
rate-limit, timeout, connection/API, and malformed-response failures become
safe provider errors; unexpected exceptions continue to propagate. Google and
xAI provider adapters remain unconfigured, and task routing stays explicit.


## OpenAI Provider v0.1 — русская версия

`OpenAIProvider` использует официальный Python SDK OpenAI и его Responses API. Он
вызывается только когда явно диспетчеризованная задача доходит до провайдера; запуск
runtime, обычные проверки работоспособности и offline-тесты не делают API-запросов.
Провайдер читает API-ключ из `OPENAI_API_KEY` во время генерации и никогда не
копирует его в `ProviderConfig`. Ключ SDK и данные запроса не логируются.
`output_text` становится провайдер-нейтральным выводом, использование Responses даёт
количество входных/выходных токенов, а оценка стоимости остаётся неустановленной.
Отказы аутентификации, лимитов, таймаута, соединения/API и некорректного ответа
превращаются в безопасные ошибки провайдера; неожиданные исключения продолжают
распространяться. Адаптеры провайдеров Google и xAI остаются ненастроенными, а
маршрутизация задач остаётся явной.
## Local secrets

`SecretStore` lazily resolves a provider's environment-variable reference from
the process environment first, then the repository-root `.env` file. It does
not load secrets into `RuntimeSettings`, `ProviderConfig`, or process-wide
environment state. Providers receive the shared store through `ProviderFactory`
and request their own key only when generating. `.env` is ignored by Git;
`.env.example` contains blank key fields and remains tracked as documentation.
The same lookup flow supports OpenAI, Anthropic, Gemini, xAI, DeepSeek, and
OpenRouter adapters.


## Local secrets — русская версия

`SecretStore` лениво разрешает ссылку на переменную окружения провайдера: сначала из
окружения процесса, затем из корневого файла `.env` репозитория. Он не загружает
секреты в `RuntimeSettings`, `ProviderConfig` или состояние окружения процесса.
Провайдеры получают общий store через `ProviderFactory` и запрашивают собственный
ключ только при генерации. `.env` игнорируется Git; `.env.example` содержит пустые
поля ключей и остаётся отслеживаемым как документация. Тот же порядок поиска
поддерживает адаптеры OpenAI, Anthropic, Gemini, xAI, DeepSeek и OpenRouter.
## Anthropic Provider v0.1

`AnthropicProvider` uses the official Python SDK Messages API when a task is
explicitly dispatched to it. It resolves `ANTHROPIC_API_KEY` through the shared
`SecretStore` at generation time, sends the prompt and serialized context as a
user message, and maps text blocks and token usage into `ProviderResponse`.
Startup and offline tests do not make requests.


## Anthropic Provider v0.1 — русская версия

`AnthropicProvider` использует официальный Python SDK и Messages API, когда задача
явно направлена ему. Он разрешает `ANTHROPIC_API_KEY` через общий `SecretStore` во
время генерации, отправляет промпт и сериализованный контекст как сообщение
пользователя и отображает текстовые блоки и использование токенов в
`ProviderResponse`. Запуск и offline-тесты запросов не делают.
## OpenAI-Compatible Providers v0.1

`DeepSeekProvider`, `OpenRouterProvider`, and `GroqProvider` share the local
`OpenAICompatibleProvider` adapter and the existing OpenAI Python SDK, using
the Chat Completions interface with each service's configured base URL and
SecretStore key reference. Groq uses `GROQ_API_KEY` and
`https://api.groq.com/openai/v1`. `ProviderConfig.model_name` accepts provider
model IDs directly; OpenRouter's `openrouter/free` route is available as
`OpenRouterProvider.FREE_MODEL_ID` and can be selected as the configured model.
The runtime separately loads `FORGE_OPENROUTER_MODEL`, defaulting to
`cohere/north-mini-code:free`, without changing other providers' model settings.
The explicit `python -m app.smoke_openrouter` command sends one real request
through the runtime, dispatcher, `ProviderAgent`, and OpenRouter adapter. Tests
use fake clients; ordinary startup makes no request.
These adapters are invoked only by task dispatch, and tests use fake clients
only.


## OpenAI-Compatible Providers v0.1 — русская версия

`DeepSeekProvider`, `OpenRouterProvider` и `GroqProvider` используют общий локальный
адаптер `OpenAICompatibleProvider` и существующий Python SDK OpenAI, применяя
интерфейс Chat Completions с настроенным базовым URL каждого сервиса и ссылкой на
ключ в SecretStore. Groq использует `GROQ_API_KEY` и
`https://api.groq.com/openai/v1`. `ProviderConfig.model_name` принимает
идентификаторы моделей провайдера напрямую; маршрут `openrouter/free` доступен как
`OpenRouterProvider.FREE_MODEL_ID` и может быть выбран как настроенная модель.
Runtime отдельно загружает `FORGE_OPENROUTER_MODEL`, по умолчанию
`cohere/north-mini-code:free`, не меняя настройки моделей других провайдеров. Явная
команда `python -m app.smoke_openrouter` отправляет один реальный запрос через
runtime, dispatcher, `ProviderAgent` и адаптер OpenRouter. Тесты используют
fake-клиенты; обычный запуск запросов не делает.
Эти адаптеры вызываются только при диспетчеризации задач, а тесты используют только
fake-клиенты.
## Gemini provider

`GoogleProvider` implements Gemini through Google's official `google-genai`
SDK. It resolves `GEMINI_API_KEY` lazily through `SecretStore`, uses the model
from `FORGE_GEMINI_MODEL` (default `gemini-3.8-flash`), and maps text and usage
to the shared `ProviderResponse`. Startup and automated tests do not make API
requests. The explicit `python -m app.smoke_gemini` command performs one real
request.


## Gemini provider — русская версия

`GoogleProvider` реализует Gemini через официальный SDK Google `google-genai`. Он
разрешает `GEMINI_API_KEY` лениво через `SecretStore`, использует модель из
`FORGE_GEMINI_MODEL` (по умолчанию `gemini-3.8-flash`) и отображает текст и
использование в общий `ProviderResponse`. Запуск и автоматические тесты не делают
API-запросов. Явная команда `python -m app.smoke_gemini` выполняет один реальный
запрос.
## Provider Capabilities v0.1

`ProviderCapabilitiesRegistry` exposes immutable metadata for each built-in
provider: canonical name, API-key environment-variable name, streaming and tool
support, large-context support, coarse `free`/`cheap`/`paid` cost tier, and
`enabled_by_config`. The
key field contains only a variable name, never the key. `enabled_by_config`
mirrors the existing `ProviderConfig.enabled` flag. Capability metadata is
separate from provider request/response types. Dispatcher uses cost tiers for
the ordinary-task policy described below, while streaming/tool declarations
describe provider API capability in general. Tool support can depend on model,
and these declarations do not claim those features are implemented by Forge
AI's current text-only adapter. Cost tiers are labels, not price data or
calculated estimates.


## Provider Capabilities v0.1 — русская версия

`ProviderCapabilitiesRegistry` предоставляет неизменяемые метаданные для каждого
встроенного провайдера: каноническое имя, имя переменной окружения для API-ключа,
поддержку streaming и инструментов, поддержку large-context, грубый cost-tier
`free`/`cheap`/`paid` и `enabled_by_config`. Поле ключа содержит только имя
переменной, никогда сам ключ. `enabled_by_config` повторяет существующий флаг
`ProviderConfig.enabled`. Метаданные capabilities отделены от типов
запроса/ответа провайдера. Dispatcher использует cost-tiers для политики обычных
задач, описанной ниже, тогда как объявления streaming/инструментов описывают
возможности API провайдера в целом. Поддержка инструментов может зависеть от
модели, и эти объявления не утверждают, что данные возможности реализованы текущим
текстовым адаптером Forge AI. Cost-tiers — это метки, а не данные о ценах или
рассчитанные оценки.
## Cost-aware Provider Routing v0.1

For ordinary `TaskCategory.OTHER` tasks, `DispatchPolicy` orders registered
providers by the existing `ProviderCapabilitiesRegistry`: `free`, then
`cheap`, then `paid`. Candidates must be present in provider and agent
registries, enabled by configuration, have their required key, and satisfy
task capability requirements. Paid candidates are omitted unless the
`RuntimeSettings.allow_paid_providers` policy is enabled through
`FORGE_ALLOW_PAID_PROVIDERS=true`; its default is false. Each candidate is
tried through the existing bounded execution/fallback flow, so a missing
key/model moves to the next permitted candidate. A configured fallback chain
is retained after cost-ordered candidates. Explicit `provider_name` bypasses
cost selection and retains absolute priority. No actual costs are calculated,
and Dispatcher does not score by quality, latency, or budget.


## Cost-aware Provider Routing v0.1 — русская версия

Для обычных задач `TaskCategory.OTHER` `DispatchPolicy` упорядочивает
зарегистрированных провайдеров по существующему `ProviderCapabilitiesRegistry`:
`free`, затем `cheap`, затем `paid`. Кандидаты должны присутствовать в реестрах
провайдеров и агентов, быть включёнными конфигурацией, иметь требуемый ключ и
удовлетворять требованиям capabilities задачи. Платные кандидаты исключаются, если
политика `RuntimeSettings.allow_paid_providers` не включена через
`FORGE_ALLOW_PAID_PROVIDERS=true`; её значение по умолчанию — false. Каждый
кандидат проверяется через существующий ограниченный поток выполнения/fallback,
поэтому отсутствующий ключ/модель переводит к следующему разрешённому кандидату.
Настроенная цепочка fallback сохраняется после упорядоченных по стоимости
кандидатов. Явный `provider_name` обходит выбор по стоимости и сохраняет абсолютный
приоритет. Фактические стоимости не рассчитываются, и Dispatcher не оценивает по
качеству, задержке или бюджету.
## Context Assembly v0.1

`ContextAssembler` builds an `ExecutionContext` from the user task, its existing
`Task.context`, and optional caller-supplied text or `ContextItem` values. Each
item has an ID, kind, content, source, trust, and freshness. Task data is marked
`USER_TASK`; caller-supplied materials are marked `EXPLICIT_INPUT`. Plain text
inputs default to `UNTRUSTED` and `UNKNOWN`; trust/freshness supplied with a
`ContextItem` are preserved, while its source is normalized to
`EXPLICIT_INPUT`.

The assembler performs no filesystem, network, provider, agent, or memory
access. It applies configurable item and character limits and fails the Run
before agent dispatch instead of truncating content. The defaults are 16 items
and 20,000 content characters; callers can configure them by injecting
`ContextAssembler(max_items=..., max_characters=...)` into `RunExecutor`. The
assembled value is passed in the dispatched task under `forge_execution_context`;
the original
`Run.task` remains unchanged. The `context_assembled` event contains only item
count and kind/source/trust/freshness summaries, never item content. This is a
small explicit-input boundary, not RAG, project memory, repository discovery,
or a secret scanner. Context metadata does not grant tool permissions.

## Capability and Tool Discovery v0.1

Capability and tool discovery is wired into the production API path. The
composing service builds one `ToolRegistry` through
`app/tools/registry.py::build_default_tool_registry`, which registers Forge's
built-in tools explicitly and by name - `write_project_file`, and
`read_project_file` when a project root is known. Nothing is discovered by
scanning Python classes, so a tool becomes visible only when it is registered on
purpose.

That single registry instance backs both execution configuration and discovery,
so enumeration cannot report a tool that execution cannot reach.
`CapabilityFabric.list_capabilities()` and
`CapabilityFabric.list_tool_capabilities()` are passive, read-only projections
over the existing `ToolRegistryAdapter`, `ModelRegistryAdapter`, and
`WorkspaceResourceAdapter`. They perform no I/O, start no scan, probe no host,
and cache nothing; enumeration returns the current state of the registries, and
registries that were not supplied contribute nothing rather than a guess.
`CapabilityDescriptor.to_dict()` serialises a descriptor without exposing
credentials, approval state, or a frozen execution scope.

Two read-only endpoints expose this: `GET /api/capabilities` lists the
capability descriptors (domains: tool, model, task), and `GET /api/tools` lists
the registered tools. Both are deterministic and side-effect free.

**Host environment discovery is not implemented.** No endpoint here inspects
executables, runtimes, `git`, Docker, the shell, or filesystem capabilities;
that remains a separate, deliberately deferred architectural stage with its own
trust boundary. `CapabilityFabric.resolve()` is still not invoked on the
production request path, and the legacy `RunExecutor`-based API task endpoint
does not yet dispatch tools; wiring execution onto the shared registry is
tracked separately.

## Production Tool Execution and RunScope v0.1

The API task endpoint now dispatches tools through a frozen `RunScope`.

`ForgeApiService` takes the operator's tool authority as a constructor argument,
`allowed_tool_ids`, which defaults to `frozenset()`. That declaration is the only
source of tool authority for an API run: it is never read from the HTTP request,
`request.context`, a skill manifest, or the fabric. Effective tools are computed
as `operator_allowlist & registered_tools`, so a registered tool that is not
allow-listed is never authorized, and an allow-listed id with no registered
implementation never reaches a scope.

Each call to `run_task` builds a scope with `RunScope(run_id, workspace,
execution_profile, allowed_tool_ids, allowed_execution_commands=frozenset())` and
then calls `freeze()` followed by `require_active_scope` before invoking the
executor. The scope binds the service's own `Workspace` as the explicit execution
root, so `RunScope.validate_workspace` rejects any other root and tool input
cannot move it. The execution profile is `api-default`: `network_access=False`
and `allowed_commands=("python",)`, which `RunScope` requires in order to reject a
silently empty profile. `allowed_execution_commands` is the empty set, so no
command is reachable.

The same `ToolRegistry` instance backs both `CapabilityFabric` (discovery) and the
runtime `ToolExecutor` (execution), so enumeration cannot report a tool that
execution cannot reach. `create_runtime` accepts an optional `tool_registry`;
omitting it preserves the historical empty registry.

Approval is unchanged and is not authorization: `read_project_file` runs without
approval when allowed, `write_project_file` still requires it, and with no
resolver the result is `WAITING_FOR_APPROVAL`. Unknown tools remain
`UNKNOWN_TOOL` / DENY, and workspace escapes remain denied.

**Host process execution is wired but disabled by default.** `ExecutionCoordinator`
and `LocalExecutionAdapter` are reachable from the API path through the
`RunExecutor` execution branch (see the section below), but the operator command
allowlist defaults to the empty set and no production `ExecutionRequest` source
is wired, so no process runs through an API task today. `AgentHarness` and
`EngineeringRunExecutor` remain outside the API path.

## Production Tool Execution and RunScope v0.1 — русская версия

API-endpoint выполнения задач теперь диспетчеризует tools через замороженный
`RunScope`.

`ForgeApiService` принимает authority оператора на tools аргументом конструктора
`allowed_tool_ids` со значением по умолчанию `frozenset()`. Это объявление —
единственный источник tool-authority для API-run: оно никогда не читается из
HTTP-запроса, `request.context`, манифеста скилла или fabric. Effective tools
вычисляются как `operator_allowlist & registered_tools`, поэтому
зарегистрированный, но не разрешённый tool никогда не авторизуется, а разрешённый
id без зарегистрированной реализации не попадает в scope.

Каждый вызов `run_task` создаёт scope через `RunScope(run_id, workspace,
execution_profile, allowed_tool_ids, allowed_execution_commands=frozenset())`,
затем вызывает `freeze()` и `require_active_scope` — и только после этого
обращается к executor'у. Scope привязывает собственный `Workspace` сервиса как
явный execution root, поэтому `RunScope.validate_workspace` отвергает любой
другой корень, и tool input не может его изменить. Профиль выполнения —
`api-default`: `network_access=False` и `allowed_commands=("python",)`, что
требует `RunScope`, чтобы отвергать молча пустой профиль.
`allowed_execution_commands` — пустое множество, поэтому ни одна команда
недостижима.

Один и тот же экземпляр `ToolRegistry` используется и в `CapabilityFabric`
(discovery), и в runtime `ToolExecutor` (execution), поэтому перечисление не
может показать tool, недостижимый для выполнения. `create_runtime` принимает
необязательный `tool_registry`; если его не передать, сохраняется исторический
пустой реестр.

Approval не изменён и не является authorization: `read_project_file` выполняется
без approval, когда разрешён, `write_project_file` по-прежнему его требует, а без
resolver результат — `WAITING_FOR_APPROVAL`. Неизвестные tools остаются
`UNKNOWN_TOOL` / DENY, выход за пределы workspace остаётся запрещённым.

**Host process execution подключён, но выключен по умолчанию.**
`ExecutionCoordinator` и `LocalExecutionAdapter` достижимы из API-пути через
ветку execution в `RunExecutor` (см. раздел ниже), однако allowlist команд
оператора по умолчанию пуст, а production-источник `ExecutionRequest` не
подключён, поэтому сегодня ни один процесс не выполняется через API-задачу.
`AgentHarness` и `EngineeringRunExecutor` остаются вне API-пути.

## Production Host Process Execution v0.1

Host process execution runs through a separate branch beside the tool path in
`RunExecutor`. It is reachable from the API path, and it is disabled by default.

`ForgeApiService` takes `allowed_execution_commands`, declared at composition
time. That declaration is the only source of authority to run a host process: it
is never derived from the request, its context, a skill, the fabric, the tool
registry, or `execution_profile.allowed_commands`. The default is
`frozenset()`, which means no process is reachable. The value is written into the
run's frozen `RunScope` as `allowed_execution_commands`.

The execution branch runs only when all of the following hold, and each missing
piece is a fail-closed refusal rather than a silent skip:

1. the operator command allowlist is non-empty;
2. the run has a frozen `RunScope` (the coordinator re-validates every request
   against it and denies an unscoped dispatch with `run_scope_required`);
3. a server-side `ApprovalPolicy` was supplied, so approval never depends on the
   request's own `approval_required` flag alone;
4. the run has a `Workspace`, which is the explicit execution root.

A server-side request factory may then produce `ExecutionRequest` values. The
factory is a server-side injection point: it can only *ask* for a command, never
authorize one. `RunExecutor` calls `ExecutionCoordinator.execute` with the
request, the scoped workspace root, the run id, the operator allowlist, the
approval policy and resolver, and the frozen scope. The coordinator performs the
permission check, builds the `ExecutionIntent`, evaluates the approval and the
`ExecutionPolicy`, validates the workspace root, and only then mints an
`AuthorizedExecution`. `LocalExecutionAdapter` refuses anything that is not a
valid token and spawns the process inside an ephemeral scratch workspace.

Denied, waiting, and failed outcomes are recorded as sanitized events carrying
only a request id, status, reason, and exit code. Stdout, stderr, raw environment
values, and the execution token are never written, so a durable record can never
act as execution authority later. Recorded host execution results are available
as `Run.execution_results` for reading only, and a pending approval is reported
as `WAITING_FOR_APPROVAL` rather than as a failure.

**Declared executions.** Command authority comes from operator-owned
`ExecutionDeclaration` values (`app/execution/declaration.py`), declared at
composition time on `ForgeApiService` as a `declarations` mapping plus a
server-side `verification_resolver`. A declaration is frozen, may carry only the
`verification` purpose in v0.1, and is rejected at composition time if the active
execution profile cannot admit it (executable, working directory, environment
values, timeout, profile id). `to_execution_request` is a pure translator: it
reads no request, no task description, and no context, and it takes the profile
from the caller so a request always carries the scope's profile.

The command set a run may use is derived from the declaration the resolver
selects, never from an independently configured allowlist. With no declarations,
no resolver, or an unresolvable selection, the derived set is empty, which is what
the executor's command-authority gate consumes, so host execution stays disabled.

**Trusted server-side verification entry point.**
`ForgeApiService.run_declared_verification(declaration_id, *, purpose_run_id=None)`
runs one operator-declared verification execution. It is server-side code called
in-process, never an HTTP route, because the HTTP layer has no caller-trust
model.

Its only selection input is the declaration id, resolved exclusively against the
operator-configured registry. It accepts no command, workspace, profile,
environment, timeout, allowlist, or approval flag, so a caller can choose *which*
declared verification runs, never *what* runs. `purpose_run_id` is correlation
metadata attached to the request, never authority.

The service generates its own run id, builds a frozen `RunScope` from its own
trusted values (its `Workspace`, its execution profile, the declared executable
as the command set, and the system acceptance criterion), and then reuses the
existing chain unchanged: `RunExecutor` → `ExecutionCoordinator` →
`AuthorizedExecution` → `LocalExecutionAdapter`. It never bypasses the
coordinator, never constructs a token, and never spawns a process itself.
Approval remains the sole decision of the server-side `ApprovalPolicy`.

Fail-closed: no declarations configured means declared verification execution is
disabled with no default or implicit command; an unknown declaration id raises
`UnknownExecutionDeclarationError` before any scope is frozen, any request is
built, or any process starts. `run_task` never invokes declared verification, so
the ordinary API task path stays execution-disabled.

**Canonical production orchestration loop.** `AgentHarness`
(`app/agent_runtime/harness.py`) is the canonical production orchestrator and the
bootstrap supplies it in `RuntimeContext.harness`. `RunExecutor` remains the
execution primitive; the loop is not built on top of it.

`ForgeApiService.run_agent_loop(declaration_id, *, purpose_run_id=None)` is the
trusted server-side entry point for the first vertical slice:

```text
trusted caller
  -> AgentHarness
  -> DecisionContextAssembler
  -> DecisionProvider            (recommendation only)
  -> harness AUTHORIZE           (frozen RunScope)
  -> ExecutionCoordinator        (mandatory, authoritative)
  -> AuthorizedExecution
  -> LocalExecutionAdapter
  -> RunStore
```

The slice is deliberately narrow: one context assembly, one decision, one
operator-authorized action, one execution result, recorded in the durable run
history. The bounds are `max_actions=1` and `max_execution_attempts=1`; these
narrow an existing policy and add no authority. `max_revision_attempts` stays
positive because the decision provider reads a zero revision budget as an
already-exhausted budget.

**Decision is not authority.** A decision answers *what to do next*; authority
answers *what this run may do at all*. The harness selects among
`execution_requests` that the server-side composition already built from an
operator declaration, and it re-checks the selected command against
`allowed_execution_commands` before dispatch. The `ExecutionCoordinator` then
re-validates independently. A decision provider cannot add a tool, add a command,
change the workspace, profile, environment, timeout, or approval, and cannot
construct an `AuthorizedExecution`.

**Tools are operator-authorized.** Tool authority comes from the operator-set
`allowed_tool_ids` on `ForgeApiService`, intersected with the registered tools and
bound into the frozen `RunScope`. `TaskRunRequest` carries no authority field, so
a request, its context, its description, its category, or a task id cannot grant a
tool. The empty default remains fail-closed.

**Project discovery: bounded observation before the decision.**
`ProjectDiscovery` (`app/agent_runtime/project_discovery.py`) is the observation
layer of the loop. It runs inside `AgentHarness.run` before the first context
assembly and produces one immutable `UnderstandingSnapshot` for the run:

```text
trusted task
  -> ProjectDiscovery.observe(workspace)     (server-side composition only)
  -> BoundedProjectScanner                   (existing bounded trust boundary)
  -> UnderstandingSnapshotter                (existing immutable snapshot model)
  -> UnderstandingSnapshot                   (immutable, per run)
  -> DecisionContextAssembler                (existing canonical assembler)
  -> ContextItem source_type=PROJECT_UNDERSTANDING
  -> DecisionProvider
  -> authorized execution / verification / acceptance
```

* **The root is composition-owned.** `ProjectDiscovery.observe` accepts only a
  `Workspace` and rejects anything else, so no task request, decision, or LLM can
  choose the root, choose a scanner, change scan limits, or disable a boundary.
  The service passes its own `Workspace` through `HarnessRequest`.
* **Bounds and redaction are the existing scanner's.** File count, per-file size,
  total bytes, ignored directories, symlink handling, secret-file exclusion, and
  binary handling all come from `BoundedProjectScanner`. Nothing is re-implemented
  in the discovery layer, and there is no second discovery system.
* **Bounded inventory, not a filesystem dump.** A snapshot carries paths and
  fingerprints plus bounded structural facts, manifests, topology, and warnings.
  It never carries file contents, and the assembler passes only the bounded
  `summarize_snapshot` counts into context - not the file inventory.
* **Discovery is not authority.** A snapshot cannot widen a `RunScope` or add a
  command, tool, profile, environment value, or timeout, and the decision provider
  receives context only - never the scope, the workspace, approval policy, or a
  filesystem handle. Discovery never executes anything: no subprocess, no shell,
  no coordinator, no adapter.
* **One run, one snapshot.** The snapshot is created fresh at the start of each
  run and never reused, so a later run observes the current workspace. Its
  `workspace_fingerprint` is deterministic for unchanged content.
* **Failure fails closed.** A discovery failure produces no snapshot, no context
  item, and no decision: the run terminates as `FAILED` with reason
  `project_discovery_failed`. There is no silent fallback to unrestricted
  filesystem access, and a partial observation is never presented as a complete
  one.
* **Observability.** `PROJECT_DISCOVERY_STARTED` and
  `PROJECT_DISCOVERY_COMPLETED` are recorded with sanitized metadata: run id, task
  id, bounded counts, the workspace fingerprint, snapshot id, duration, and a safe
  failure category. No file contents, secret paths, or raw environment are stored.

`AgentHarness` remains the only production orchestration loop; discovery adds a
stage to it and no new coordinator, agent, or loop.

**Acceptance slice: criterion, verification, and verdict.**
`ForgeApiService.run_accepted_task(declaration_id)` is the production acceptance
entry point. It is trusted in-process code whose only input is a declaration id.

```text
operator composition
  -> ExecutionDeclaration          (what may run)
  -> AcceptanceSpec                (criterion + deterministic expectation)
  -> run_id / task_id              (server-generated identity)
  -> RunAcceptanceCriteria         (frozen per-run criteria)
  -> AgentHarness
  -> one authorized action
  -> WorkspaceVerifier             (file existence / SHA-256)
  -> AcceptanceGate                (PASS / FAIL)
  -> RunStore                      (criterion, verification, acceptance events)
```

* **Criterion identity** is created server-side. `AcceptanceSpec`
  (`app/agent_runtime/acceptance_spec.py`) is the operator's definition, declared
  next to the execution declaration; `AcceptanceSpec.bind(run_id, task_id)`
  freezes it into `RunAcceptanceCriteria`, which asserts it belongs to exactly
  that run and task. Nothing can bind a spec to a run it was not composed for, and
  criteria cannot be swapped after a run starts.
* **Every criterion must be checkable.** A criterion without an expectation, an
  expectation for an unknown criterion, a duplicate criterion id, an empty
  criterion set, and a malformed digest are all rejected at construction, so a
  criterion can never exist whose acceptance could only ever be
  `verification_missing`.
* **Verification is deterministic and non-authoritative.**
  `FrozenCriteriaVerifier` (`app/agent_runtime/frozen_verifier.py`) evaluates
  workspace facts through the existing `WorkspaceVerifier`. Its input is a plain
  mapping of expectations, so it also serves the harness's own verification stage,
  and there is still one verification implementation. It has no executable, argv,
  environment, timeout, profile, or scope, and it cannot choose a command. A
  verifier that cannot complete its work reports `ERROR`, never `PASS`.
* **The verdict is the existing gate's.** `AcceptanceGate` decides PASS/FAIL; the
  entry point only consumes the result. A missing verdict is recorded as
  `not_evaluated` and is never treated as a pass.
* **The slice is one execution plus its verification.** `ACCEPTANCE_LOOP_POLICY`
  allows `max_execution_attempts=1`, and the action budget admits the verification
  stage that must follow it.

**`EXECUTION_SUCCESS` is not task acceptance.** The response's `success` means the
action ran successfully *and* its verification passed; the acceptance verdict
itself is recorded as `acceptance_status` in the durable run record. A successful
process whose criterion fails is rejected with `required_criteria_failed`.
Redefining the public `success` field is a separate contract decision.

**`RUN_VERIFICATION` in the loop.** The action reads verification expectations
from the server-side request and has no channel to select a declaration, so a
decision cannot choose an arbitrary declared execution. With no expectations
configured the acceptance gate fails closed with `verification_missing` rather
than reporting a pass.

`EngineeringRunExecutor`, `RevisionLoopExecutor`, `MultiAgentExecutor`, and
`ProviderReviewer` are **not** production machinery; they remain test/smoke
machinery until a separate decision.

**What is still missing.** The entry point runs and records a declared
verification, and the loop runs one authorized action, but there is no trusted
criterion identity to attach a result to, so neither is linked to a client task's
acceptance criteria and no placeholder criterion is presented as a real one
(GAP-F). Task-driven verification binding likewise still needs its own decision:
no production `VerificationExpectation` or `TestVerificationIntent` exists, and
`TaskRunRequest` carries no verification field. Network restriction is
best-effort only (`network_access=False` sets proxy environment variables; it is
not a kernel-level block), and the ephemeral workspace boundary is a
working-directory and COPY-staging boundary, not a filesystem sandbox. Idempotent
side-effect execution and automatic resume remain out of scope.

## Production Host Process Execution v0.1 — русская версия

Host process execution выполняется через отдельную ветку рядом с tool-путём в
`RunExecutor`. Он достижим из API-пути и выключен по умолчанию.

`ForgeApiService` принимает `allowed_execution_commands`, объявляемый на уровне
композиции. Это объявление — единственный источник права запустить host-процесс:
оно никогда не выводится из запроса, его контекста, скилла, fabric, реестра tools
или `execution_profile.allowed_commands`. Значение по умолчанию — `frozenset()`,
то есть ни один процесс недостижим. Это значение записывается в замороженный
`RunScope` запуска как `allowed_execution_commands`.

Ветка execution выполняется только при выполнении всех условий, и каждый
недостающий элемент — это fail-closed отказ, а не молчаливый пропуск:

1. allowlist команд оператора непуст;
2. у запуска есть замороженный `RunScope` (координатор перепроверяет каждый
   запрос против него и отвергает dispatch без scope с `run_scope_required`);
3. передана server-side `ApprovalPolicy`, поэтому approval никогда не зависит
   только от флага `approval_required` самого запроса;
4. у запуска есть `Workspace` — явный execution root.

После этого server-side фабрика запросов может породить значения
`ExecutionRequest`. Фабрика — это server-side точка инъекции: она может только
*запросить* команду, но не авторизовать её. `RunExecutor` вызывает
`ExecutionCoordinator.execute` с запросом, корнем workspace из scope, run id,
allowlist оператора, approval policy и resolver'ом, а также замороженным scope.
Координатор выполняет permission-проверку, строит `ExecutionIntent`, оценивает
approval и `ExecutionPolicy`, валидирует корень workspace и только затем выпускает
`AuthorizedExecution`. `LocalExecutionAdapter` отвергает всё, что не является
валидным токеном, и запускает процесс внутри эфемерного scratch-workspace.

Отказы, ожидание и ошибки записываются как санитизированные события, несущие
только request id, статус, причину и exit code. Stdout, stderr, сырые значения
окружения и execution-токен никогда не записываются, поэтому durable-запись
никогда не сможет стать execution-authority. Записанные результаты host execution
доступны как `Run.execution_results` только для чтения, а ожидание approval
сообщается как `WAITING_FOR_APPROVAL`, а не как ошибка.

**Объявленные выполнения.** Command-authority приходит от принадлежащих
оператору значений `ExecutionDeclaration` (`app/execution/declaration.py`),
объявляемых на уровне композиции в `ForgeApiService` как mapping `declarations`
плюс server-side `verification_resolver`. Объявление заморожено, в v0.1 может
нести только purpose `verification` и отвергается на этапе композиции, если
активный профиль выполнения не может его admit'нуть (executable, working
directory, значения environment, timeout, profile id). `to_execution_request` —
чистый транслятор: он не читает ни запрос, ни description задачи, ни context, а
профиль берёт у вызывающего, поэтому запрос всегда несёт профиль scope.

Набор команд, доступных запуску, выводится из объявления, выбранного resolver'ом,
а не из независимо сконфигурированного allowlist. Без объявлений, без resolver'а
или при неразрешимом выборе производный набор пуст — именно его потребляет гейт
command-authority в executor'е, поэтому host execution остаётся выключенным.

**Доверенный server-side вход для verification.**
`ForgeApiService.run_declared_verification(declaration_id, *, purpose_run_id=None)`
выполняет одно объявленное оператором verification-выполнение. Это server-side
код, вызываемый внутри процесса, а не HTTP-маршрут, поскольку у HTTP-слоя нет
модели доверия вызывающего.

Единственный вход выбора — declaration id, разрешаемый исключительно через
операторский реестр. Метод не принимает ни команду, ни workspace, ни профиль, ни
окружение, ни timeout, ни allowlist, ни approval-флаг, поэтому вызывающий может
выбрать *какая* объявленная verification выполнится, но никогда — *что*
выполнится. `purpose_run_id` — correlation-метаданные, прикрепляемые к запросу, а
не authority.

Сервис сам генерирует run id, строит замороженный `RunScope` из собственных
доверенных значений (`Workspace`, профиль выполнения, объявленный executable как
набор команд и системный acceptance-критерий) и затем переиспользует существующую
цепочку без изменений: `RunExecutor` → `ExecutionCoordinator` →
`AuthorizedExecution` → `LocalExecutionAdapter`. Он никогда не обходит
координатор, не конструирует токен и не запускает процесс сам. Approval остаётся
исключительным решением server-side `ApprovalPolicy`.

Fail-closed: если объявления не сконфигурированы, объявленное verification-
выполнение выключено, без default- или implicit-команды; неизвестный declaration
id поднимает `UnknownExecutionDeclarationError` до того, как будет заморожен
scope, построен запрос или запущен процесс. `run_task` никогда не вызывает
объявленную verification, поэтому обычный API-путь задачи остаётся
execution-disabled.

**Канонический production orchestration loop.** `AgentHarness`
(`app/agent_runtime/harness.py`) — канонический production-оркестратор, и
bootstrap поставляет его в `RuntimeContext.harness`. `RunExecutor` остаётся
execution-примитивом; loop не строится поверх него.

`ForgeApiService.run_agent_loop(declaration_id, *, purpose_run_id=None)` — доверенный
server-side вход для первого вертикального среза:

```text
доверенный вызывающий
  -> AgentHarness
  -> DecisionContextAssembler
  -> DecisionProvider            (только рекомендация)
  -> AUTHORIZE в harness         (замороженный RunScope)
  -> ExecutionCoordinator        (обязателен, авторитетен)
  -> AuthorizedExecution
  -> LocalExecutionAdapter
  -> RunStore
```

Срез намеренно узкий: одна сборка контекста, одно решение, одно авторизованное
оператором действие, один результат исполнения, записанный в durable history.
Границы — `max_actions=1` и `max_execution_attempts=1`; они сужают существующую
политику и не добавляют authority. `max_revision_attempts` остаётся положительным,
поскольку decision provider читает нулевой бюджет ревизий как уже исчерпанный.

**Decision не является authority.** Decision отвечает на вопрос *что делать
дальше*; authority — на вопрос *что этому run вообще разрешено*. Harness выбирает
среди `execution_requests`, которые server-side композиция уже построила из
объявления оператора, и повторно проверяет выбранную команду против
`allowed_execution_commands` перед dispatch. Затем `ExecutionCoordinator`
перепроверяет независимо. Decision provider не может добавить tool, добавить
команду, изменить workspace, профиль, окружение, timeout или approval, а также
создать `AuthorizedExecution`.

**Tools авторизуются оператором.** Tool authority приходит из заданного оператором
`allowed_tool_ids` в `ForgeApiService`, пересекается с зарегистрированными tools и
связывается в замороженный `RunScope`. `TaskRunRequest` не несёт authority-полей,
поэтому запрос, его context, description, category или task id не могут выдать
tool. Пустой default остаётся fail-closed.

**Обнаружение проекта: bounded-наблюдение перед решением.**
`ProjectDiscovery` (`app/agent_runtime/project_discovery.py`) — слой наблюдения в
loop. Он выполняется внутри `AgentHarness.run` до первой сборки контекста и
создаёт один неизменяемый `UnderstandingSnapshot` для run:

```text
доверенная задача
  -> ProjectDiscovery.observe(workspace)     (только server-side композиция)
  -> BoundedProjectScanner                   (существующая bounded trust boundary)
  -> UnderstandingSnapshotter                (существующая immutable-модель)
  -> UnderstandingSnapshot                   (immutable, per run)
  -> DecisionContextAssembler                (существующий канонический assembler)
  -> ContextItem source_type=PROJECT_UNDERSTANDING
  -> DecisionProvider
  -> авторизованное исполнение / verification / acceptance
```

* **Root принадлежит композиции.** `ProjectDiscovery.observe` принимает только
  `Workspace` и отвергает всё остальное, поэтому ни запрос задачи, ни decision, ни
  LLM не могут выбрать root, выбрать scanner, изменить лимиты сканирования или
  отключить границу. Сервис передаёт собственный `Workspace` через
  `HarnessRequest`.
* **Bounds и redaction — существующего scanner'а.** Лимит файлов, размер файла,
  общий объём байт, игнорируемые каталоги, обработка symlink, исключение секретных
  файлов и обработка бинарных файлов берутся из `BoundedProjectScanner`. В слое
  discovery ничего не переписывается, второй системы discovery нет.
* **Bounded inventory, а не дамп файловой системы.** Snapshot несёт пути и
  отпечатки плюс ограниченные структурные факты, манифесты, топологию и
  предупреждения. Содержимое файлов в него не попадает, а assembler передаёт в
  контекст только ограниченные счётчики `summarize_snapshot`, но не инвентарь
  файлов.
* **Discovery — не authority.** Snapshot не может расширить `RunScope` или
  добавить команду, tool, профиль, значение окружения или timeout, а decision
  provider получает только контекст — никогда scope, workspace, approval-политику
  или filesystem handle. Discovery ничего не исполняет: ни subprocess, ни shell,
  ни координатор, ни adapter.
* **Один run — один snapshot.** Snapshot создаётся заново в начале каждого run и
  никогда не переиспользуется, поэтому следующий run наблюдает текущий workspace.
  Его `workspace_fingerprint` детерминирован для неизменного содержимого.
* **Сбой — fail closed.** Сбой discovery не даёт ни snapshot, ни элемента
  контекста, ни решения: run завершается как `FAILED` с причиной
  `project_discovery_failed`. Молчаливого отката к неограниченному доступу к
  файловой системе нет, и частичное наблюдение никогда не выдаётся за полное.
* **Наблюдаемость.** `PROJECT_DISCOVERY_STARTED` и `PROJECT_DISCOVERY_COMPLETED`
  записываются с санитизированными метаданными: run id, task id, ограниченные
  счётчики, отпечаток workspace, snapshot id, длительность и безопасная категория
  сбоя. Содержимое файлов, пути секретов и сырое окружение не сохраняются.

`AgentHarness` остаётся единственным production-orchestration loop; discovery
добавляет стадию в него, а не новый координатор, агент или loop.

**Срез acceptance: criterion, verification и вердикт.**
`ForgeApiService.run_accepted_task(declaration_id)` — production-вход acceptance.
Это доверенный in-process код, единственный вход которого — declaration id.

* **Criterion identity создаётся server-side.** `AcceptanceSpec`
  (`app/agent_runtime/acceptance_spec.py`) — определение оператора, объявляемое
  рядом с execution-объявлением; `AcceptanceSpec.bind(run_id, task_id)`
  замораживает его в `RunAcceptanceCriteria`, который проверяет принадлежность
  ровно этому run и task. Никто не может связать spec с run, для которого он не
  объявлялся, а критерии нельзя подменить после старта run.
* **Каждый критерий обязан быть проверяемым.** Критерий без expectation,
  expectation для неизвестного критерия, дубликат criterion id, пустой набор
  критериев и некорректный digest отвергаются при создании.
* **Verification детерминирован и не несёт authority.**
  `FrozenCriteriaVerifier` (`app/agent_runtime/frozen_verifier.py`) проверяет
  факты workspace через существующий `WorkspaceVerifier`. У него нет executable,
  argv, окружения, timeout, профиля или scope, и он не может выбрать команду.
  Verifier, который не может выполнить работу, сообщает `ERROR`, но никогда
  `PASS`.
* **Вердикт выдаёт существующий гейт.** PASS/FAIL решает `AcceptanceGate`; вход
  лишь потребляет результат. Отсутствующий вердикт записывается как
  `not_evaluated` и никогда не трактуется как прохождение.
* **Срез — одно исполнение плюс его verification.** `ACCEPTANCE_LOOP_POLICY`
  допускает `max_execution_attempts=1`, а бюджет действий вмещает обязательную
  следующую за ним стадию verification.

**`EXECUTION_SUCCESS` — не task acceptance.** `success` в ответе означает, что
действие выполнилось успешно *и* его verification прошёл; сам вердикт acceptance
записывается как `acceptance_status` в durable run record. Успешный процесс, чей
критерий не проходит, отвергается с `required_criteria_failed`. Переопределение
публичного поля `success` — отдельное решение о контракте.

**`RUN_VERIFICATION` в срезе.** Действие читает verification expectations из
server-side запроса и не имеет канала для выбора объявления, поэтому decision не
может выбрать произвольное объявленное выполнение. Без сконфигурированных
expectations acceptance-гейт падает fail-closed с `verification_missing`, а не
сообщает о прохождении.

`EngineeringRunExecutor`, `RevisionLoopExecutor`, `MultiAgentExecutor` и
`ProviderReviewer` **не** являются production-машинерией; они остаются
test/smoke-машинерией до отдельного решения.

**Чего ещё нет.** Вход выполняет и записывает объявленную verification, а loop
выполняет одно авторизованное действие, но доверенной criterion-идентичности, к
которой можно привязать результат, нет, поэтому ни то, ни другое не связывается с
acceptance criteria клиентской задачи, а placeholder-критерий не выдаётся за
настоящий (GAP-F). Привязка verification к задаче также всё ещё требует отдельного
решения: production `VerificationExpectation` или `TestVerificationIntent` не
существует, а `TaskRunRequest` не несёт verification-поля. Ограничение сети —
только best-effort (`network_access=False` выставляет прокси-переменные
окружения; это не kernel-level блокировка), а граница эфемерного workspace — это
граница рабочей директории и COPY-разворачивания, а не файловая песочница.
Идемпотентное исполнение side effects и automatic resume остаются вне области
работ.

## Capability and Tool Discovery v0.1 — русская версия

Capability- и tool-discovery подключены к production-пути API. Композирующий
сервис создаёт один `ToolRegistry` через
`app/tools/registry.py::build_default_tool_registry`, который регистрирует
встроенные инструменты Forge явно и по имени - `write_project_file` и
`read_project_file`, когда известен корень проекта. Ничего не обнаруживается
сканированием Python-классов, поэтому инструмент становится видимым только при
осознанной регистрации.

Этот единственный экземпляр реестра обслуживает и конфигурацию выполнения, и
discovery, поэтому перечисление не может показать инструмент, недостижимый для
выполнения. `CapabilityFabric.list_capabilities()` и
`CapabilityFabric.list_tool_capabilities()` - пассивные read-only проекции поверх
существующих `ToolRegistryAdapter`, `ModelRegistryAdapter` и
`WorkspaceResourceAdapter`. Они не выполняют I/O, не запускают сканирование, не
опрашивают хост и ничего не кэшируют; перечисление возвращает текущее состояние
реестров, а непереданные реестры не дают ничего вместо догадки.
`CapabilityDescriptor.to_dict()` сериализует descriptor, не раскрывая
credentials, состояние approval или замороженный scope выполнения.

Два read-only endpoint'а предоставляют это: `GET /api/capabilities` перечисляет
capability descriptors (домены: tool, model, task), а `GET /api/tools`
перечисляет зарегистрированные инструменты. Оба детерминированы и не имеют
побочных эффектов.

**Host environment discovery не реализован.** Ни один endpoint здесь не
исследует исполняемые файлы, runtime'ы, `git`, Docker, shell или возможности
файловой системы; это отдельный, намеренно отложенный архитектурный этап со своей
границей доверия. `CapabilityFabric.resolve()` по-прежнему не вызывается на
production-пути запроса, а legacy API-endpoint выполнения задач на базе
`RunExecutor` пока не диспетчеризует инструменты; подключение выполнения к общему
реестру отслеживается отдельно.


## Context Assembly v0.1 — русская версия

`ContextAssembler` строит `ExecutionContext` из пользовательской задачи, её
существующего `Task.context` и необязательных переданных вызывающим текстовых
значений или значений `ContextItem`. Каждый элемент имеет ID, вид, содержимое,
источник, доверие и свежесть. Данные задачи помечаются `USER_TASK`; переданные
вызывающим материалы помечаются `EXPLICIT_INPUT`. Простые текстовые входы по
умолчанию получают `UNTRUSTED` и `UNKNOWN`; trust/freshness, переданные вместе с
`ContextItem`, сохраняются, тогда как его source нормализуется в
`EXPLICIT_INPUT`.

Сборщик не выполняет доступа к файловой системе, сети, провайдерам, агентам или
памяти. Он применяет настраиваемые лимиты по количеству элементов и символам и
завершает Run с ошибкой до диспетчеризации агенту, а не усекает содержимое.
Значения по умолчанию — 16 элементов и 20 000 символов содержимого; вызывающие
могут настроить их, внедрив `ContextAssembler(max_items=..., max_characters=...)` в
`RunExecutor`. Собранное значение передаётся в диспетчеризованной задаче под ключом
`forge_execution_context`; исходный `Run.task` остаётся неизменным. Событие
`context_assembled` содержит только количество элементов и сводки
kind/source/trust/freshness, никогда содержимое элементов. Это небольшая граница
явного ввода, а не RAG, память проектов, discovery репозитория или сканер секретов.
Метаданные контекста не выдают разрешения на инструменты.
## Tool Contract v0.1 — Read-only execution

Agents can return structured `ToolInvocation` proposals in the existing
`TaskResult`. `RunExecutor` sends each proposal to the injected `ToolExecutor`;
it does not create another orchestrator or bypass the existing dispatch path.
`ToolRegistry` answers whether a tool is registered. The separate
`PermissionPolicy` evaluates each invocation against the current Run context
and its explicit `allowed_tool_ids` (default-deny); invocation input cannot
grant permission. `ToolExecutor` always obtains this policy decision itself,
so a direct call without Run context is denied. Only an `ALLOW` decision may
start tool execution.
`ReadProjectFile` reads only exact relative paths supplied in its explicit
allow-list and rejects traversal and resolved paths outside the configured
project root. It has no secret-store access and cannot write files.

After the one bounded tool round, `ToolResult` values are supplied to the agent
through the existing dispatch path and attached to the final run `TaskResult`.
Further tool proposals receive `TOOL_LOOP_LIMIT` denial. A `permission_checked`
event records run/invocation/tool IDs, `ALLOW` or `DENY`, and a structured reason.
A denied request also records `tool_invocation_denied` and produces no tool
execution events. Other Run events record
only IDs, status, and an output fingerprint on success; they never copy tool
output or file contents. The deterministic `MockAgent` exercises proposals
offline. This is a narrow permission boundary, not a general permission
system or sandbox. Human approval is a separate boundary described below.


## Tool Contract v0.1 — Read-only execution — русская версия

Агенты могут возвращать структурированные предложения `ToolInvocation` в
существующем `TaskResult`. `RunExecutor` отправляет каждое предложение внедрённому
`ToolExecutor`; он не создаёт другой оркестратор и не обходит существующий путь
диспетчеризации. `ToolRegistry` отвечает, зарегистрирован ли инструмент. Отдельная
`PermissionPolicy` оценивает каждую инвокацию относительно текущего контекста Run и
его явного `allowed_tool_ids` (default-deny); входные данные инвокации не могут
выдать разрешение. `ToolExecutor` всегда получает это решение политики сам, поэтому
прямой вызов без контекста Run отклоняется. Только решение `ALLOW` может начать
выполнение инструмента.
`ReadProjectFile` читает только точные относительные пути, переданные в его явном
белом списке, и отклоняет обход и разрешённые пути вне настроенного корня проекта. У
него нет доступа к secret-store, и он не может записывать файлы.

После одного ограниченного раунда инструментов значения `ToolResult` передаются
агенту через существующий путь диспетчеризации и прикрепляются к итоговому
`TaskResult` запуска. Дальнейшие предложения инструментов получают отказ
`TOOL_LOOP_LIMIT`. Событие `permission_checked` фиксирует ID запуска/инвокации/
инструмента, `ALLOW` или `DENY` и структурированную причину. Отклонённый запрос
также фиксирует `tool_invocation_denied` и не порождает событий выполнения
инструмента. Остальные события Run фиксируют только ID, статус и fingerprint вывода
при успехе; они никогда не копируют вывод инструмента или содержимое файлов.
Детерминированный `MockAgent` проверяет предложения offline. Это узкая граница
разрешений, а не общая система разрешений или песочница. Одобрение человеком —
отдельная граница, описанная ниже.
## Approval Boundary v0.1

Tool permission and human approval are separate checks. `ToolExecutor` first
asks `PermissionPolicy`; a `DENY` ends the path without requesting approval.
Only after `ALLOW`, `ApprovalPolicy` checks its explicit
`approval_required_tools` allow-list. Tools outside the list proceed without
approval. A required approval creates an `ApprovalRequest` containing only the
Run, invocation, and tool IDs plus a fixed reason. The request does not contain
tool input or file contents.

An independent `ApprovalResolver` may return `APPROVED` or `REJECTED`. Its
in-memory implementation matches the exact `run_id` and `invocation_id` and
consumes each decision once. With no resolution, the Run enters
`WAITING_FOR_APPROVAL`, records `approval_requested`, and does not execute the
tool or continue agent dispatch. An approval rejection returns a denied
`ToolResult`. Only permission `ALLOW` together with approval
`NOT_REQUIRED` or `APPROVED` can reach tool execution. Agent input cannot set
approval state or supply a decision.

`approval_requested` and `approval_resolved` events contain safe IDs, state or
resolution, and a fixed reason; they do not contain invocation input, secrets,
credentials, or file contents. This v0.1 boundary is in-process and does not
provide a user interface, durable decisions, or checkpoint/resume after a Run
waits for approval. It is not a full human approval workflow.

## Граница подтверждения v0.1 — русская версия

Разрешение инструмента и подтверждение человеком — отдельные проверки.
`ToolExecutor` сначала обращается к `PermissionPolicy`; при `DENY` выполнение
завершается без запроса подтверждения. Только после `ALLOW` политика
`ApprovalPolicy` проверяет явный список `approval_required_tools`. Инструменты,
не включённые в список, выполняются без подтверждения. Обязательное
подтверждение создаёт `ApprovalRequest`, содержащий только ID Run, вызова и
инструмента, а также фиксированную причину. В запрос не входят входные данные
инструмента или содержимое файлов.

Независимый `ApprovalResolver` может вернуть `APPROVED` или `REJECTED`. Его
внутрипроцессная реализация сопоставляет точные `run_id` и `invocation_id` и
использует каждое решение только один раз. Если решения нет, Run переходит в
`WAITING_FOR_APPROVAL`, записывает событие `approval_requested` и не запускает
инструмент или последующую отправку агенту. При отказе возвращается запрещённый
`ToolResult`. Запустить инструмент можно только при сочетании разрешения
`ALLOW` с подтверждением `NOT_REQUIRED` или `APPROVED`. Входные данные агента не
могут устанавливать состояние подтверждения или передавать решение.

События `approval_requested` и `approval_resolved` содержат безопасные ID,
состояние или решение и фиксированную причину; входные данные вызова, секреты,
учётные данные и содержимое файлов в них не записываются. Граница v0.1 работает
в памяти процесса и не предоставляет пользовательский интерфейс, постоянное
хранение решений или checkpoint/resume после ожидания подтверждения. Это не
полноценный процесс подтверждения человеком.

## Tool Contract v0.1 — только чтение — русская версия

Агент может вернуть структурированные предложения `ToolInvocation` в
существующем `TaskResult`. `RunExecutor` передаёт каждое предложение внедрённому
`ToolExecutor`; отдельный оркестратор не создаётся и существующий путь dispatch
не обходится. `ToolRegistry` сообщает, зарегистрирован ли инструмент.
Отдельная `PermissionPolicy` проверяет invocation по контексту текущего Run и
его явному `allowed_tool_ids` (по умолчанию отказ); input invocation не может
выдать разрешение. `ToolExecutor` всегда сам получает решение policy, поэтому
прямой вызов без контекста Run отклоняется. Запустить инструмент можно только
после решения `ALLOW`.
`ReadProjectFile` читает только точные относительные пути из явного allow-list,
отклоняя traversal и разрешённые пути за пределами настроенного корня проекта.
У инструмента нет доступа к хранилищу секретов и возможности записи файлов.

После одного ограниченного tool round значения `ToolResult` передаются агенту
через существующий dispatch path и добавляются в итоговый `TaskResult` Run.
Последующие предложения инструментов получают отказ `TOOL_LOOP_LIMIT`.
Событие `permission_checked` содержит ID run/invocation/tool, `ALLOW` или
`DENY` и структурированную причину. Для отклонённых invocation дополнительно
создаётся `tool_invocation_denied`, но события выполнения инструмента не
создаются. Остальные события Run содержат только ID,
статус и fingerprint результата при успехе; они не копируют вывод инструмента
или содержимое файлов. Детерминированный `MockAgent` проверяет предложения
offline. Это узкая граница, а не полноценная система разрешений, sandbox или
процесс подтверждения. Permission не означает подтверждение человеком.

For traceability, `context_assembled` also records a deterministic SHA-256
fingerprint of the canonical assembled items. The fingerprint can be correlated
with the event's `run_id` without copying context contents into the event
journal. Item IDs and metadata are part of the fingerprint; the per-Run ID is
excluded so identical assembled items have the same fingerprint.

## Context Assembly v0.1 — русская версия

`ContextAssembler` формирует `ExecutionContext` из пользовательской задачи,
существующего `Task.context` и необязательных строк или объектов `ContextItem`,
переданных вызывающей стороной. У каждого элемента есть ID, тип, содержимое,
источник, уровень доверия и актуальность. Данные задачи получают источник
`USER_TASK`, а переданные материалы — `EXPLICIT_INPUT`. Для строк по умолчанию
устанавливаются `UNTRUSTED` и `UNKNOWN`; значения trust/freshness из
`ContextItem` сохраняются, а его source нормализуется в `EXPLICIT_INPUT`.

Assembler не обращается к файловой системе, сети, провайдерам, агентам или
памяти. Он проверяет настраиваемые лимиты количества элементов и символов и
при превышении завершает Run ошибкой до вызова агента, не обрезая содержимое.
Значения по умолчанию — 16 элементов и 20 000 символов содержимого; вызывающая
сторона может изменить их, передав в `RunExecutor` объект
`ContextAssembler(max_items=..., max_characters=...)`.
Собранный объект передаётся в копии задачи под ключом
`forge_execution_context`; исходный `Run.task` не меняется. Событие
`context_assembled` содержит только количество элементов и сводки по
kind/source/trust/freshness, но не само содержимое. Это минимальная граница для
явных входных данных, а не RAG, память проекта, поиск по репозиторию или
сканер секретов. Метаданные контекста не выдают разрешений на инструменты.

## Run + Events v0.1

`RunExecutor` wraps the existing `Orchestrator.dispatch()` call. It creates an
in-memory `Run` with a unique ID, the original task, a `CREATED` state, an event
list, and slots for the existing `TaskResult` and structured error. Execution
transitions through `RUNNING` to `COMPLETED` or `FAILED`. Routing, candidate
selection, and fallback remain owned by the existing Dispatcher.

The Dispatcher can notify the Run observer about `run_started`,
`context_assembled`, `provider_selected`, `provider_attempt`, `provider_result`,
`fallback`,
`run_completed`, and `run_failed` events. Events record only available
execution metadata such as provider/model, attempt number, duration, usage, and
failure reason; they do not record task prompts, model output, chain-of-thought,
or credentials. Runs and events are held in process memory for the duration of
the Run and are additionally persisted to a local Run store
(`app/runtime/run_store.py`) as an append-only sanitized event log
(`events.jsonl`) plus a state snapshot (`state.json`), keyed by `run_id`. The
persisted record is readable after the originating process has exited, through
`GET /api/runs/{run_id}`. There is still no distributed tracing or telemetry
service. Durable run history/checkpoint persistence is implemented; automatic
execution resume remains intentionally deferred pending side-effect idempotency
design, because replaying an interrupted tool or process would duplicate an
irreversible action. The store is a sink only: it grants no authority and never
resumes, retries, or re-executes anything.

Persistence is configured by two environment variables:
`FORGE_RUN_STORE_ENABLED` (default `false`; ordinary library use and test
execution therefore never write runtime state as a side effect) and
`FORGE_RUN_STORE_ROOT` (overrides the per-user storage location). The API
service injects a store explicitly and does not depend on the enable flag.

## Durable Run history v0.1 — русская версия

Run и события по-прежнему живут в памяти процесса на время выполнения Run, но
дополнительно сохраняются в локальное Run-хранилище (`app/runtime/run_store.py`)
в виде append-only журнала событий (`events.jsonl`) и снимка состояния
(`state.json`), с ключом `run_id`. Сохранённая история доступна после завершения
исходного процесса через `GET /api/runs/{run_id}`. Распределённого трейсинга и
телеметрийного сервиса по-прежнему нет. Durable run history/checkpoint
persistence реализован; automatic execution resume намеренно отложен до
проектирования идемпотентности side effects, поскольку повторное выполнение
прерванного инструмента или процесса продублировало бы необратимое действие.
Хранилище — только приёмник: оно не выдаёт полномочий и никогда не возобновляет,
не повторяет и не перезапускает ничего.

## Run + Events v0.1 — русская версия

`RunExecutor` оборачивает существующий вызов `Orchestrator.dispatch()`. Он
создаёт хранящийся в памяти `Run` с уникальным ID, исходной задачей, состоянием
`CREATED`, списком событий и полями для существующего `TaskResult` и
структурированной ошибки. Выполнение проходит состояния `RUNNING`, а затем
`COMPLETED` или `FAILED`. Маршрутизация, выбор кандидатов и fallback остаются
ответственностью существующего Dispatcher.

Dispatcher может передавать наблюдателю Run события `run_started`,
`context_assembled`, `provider_selected`, `provider_attempt`, `provider_result`,
`fallback`,
`run_completed` и `run_failed`. События содержат только доступные метаданные
выполнения, например provider/model, номер попытки, длительность, usage и
причину сбоя. Они не сохраняют текст задачи, ответ модели, chain-of-thought или
учётные данные. Run и события существуют только в памяти процесса. Постоянного
хранилища событий, контрольных точек и возобновления, распределённой трассировки
или сервиса телеметрии пока нет.

## Workspace Boundary v0.1 — controlled writes

Workspace is an existing absolute directory explicitly supplied to
RunExecutor.execute(..., workspace=...). The root is carried in the
Run-scoped ToolExecutionContext; it is never inferred from the process
working directory, the user's home directory, or agent input. Without that
context, WriteProjectFile fails closed.

WriteProjectFile accepts only relative_path, UTF-8 content, and optional
boolean overwrite. ApprovalPolicy always requires approval for this mutation
tool, even if the caller's additional required-tool list omits it. The
execution order remains Permission → Approval → Workspace validation →
write. PermissionPolicy must return ALLOW, and only an independent APPROVED
decision can reach the filesystem operation. An unresolved invocation leaves
the Run waiting; a rejected or denied invocation does not write.

The workspace boundary normalizes both slash styles, rejects absolute paths,
drive/stream paths, and parent traversal, resolves targets semantically under
the canonical root, and rejects existing symlink, junction, or reparse-point
components. Missing parent directories are created one level at a time inside
the workspace. Writes use a temporary sibling file followed by an atomic
filesystem operation. By default, an existing file is denied; explicit
overwrite=true atomically replaces it. This is a path boundary, not an OS
sandbox, and it does not protect against a hostile process racing filesystem
changes between checks and the final operation.

Tool results and completion events expose only a normalized relative path,
byte count, and SHA-256 fingerprint. They never copy the written content into
the event journal. The offline integration tests use isolated temporary
workspaces and make no network/API calls.

## Граница Workspace v0.1 — контролируемая запись — русская версия

Workspace — существующий абсолютный каталог, явно переданный в
RunExecutor.execute(..., workspace=...). Корень передаётся в Run-scoped
ToolExecutionContext; он не определяется по текущему каталогу процесса,
домашнему каталогу пользователя или входным данным агента. Без такого
контекста WriteProjectFile завершает операцию без записи.

WriteProjectFile принимает только relative_path, текст content в UTF-8
и необязательный логический параметр overwrite. ApprovalPolicy всегда
требует подтверждения для этого инструмента изменения файлов, даже если
дополнительный список обязательного подтверждения его не содержит. Порядок
остаётся таким: Permission → Approval → проверка Workspace → запись.
PermissionPolicy должен вернуть ALLOW, и до файловой операции допускает
только независимое решение APPROVED. Если решение ожидается, Run остаётся в
состоянии ожидания; при отказе или запрете запись не выполняется.

Граница Workspace нормализует оба вида разделителей, отклоняет абсолютные
пути, пути с диском или потоком данных и обход через .., семантически
проверяет расположение цели относительно канонического корня и отклоняет
существующие символические ссылки, junction и reparse points в пути. Новые
родительские каталоги создаются по одному уровню только внутри Workspace.
Запись сначала выполняется во временный файл рядом с целью, затем
производится атомарная операция файловой системы. По умолчанию запись поверх
существующего файла запрещена; явный overwrite=true атомарно заменяет его.
Это ограничение пути, а не изоляция на уровне ОС; оно не защищает от
враждебного процесса, который меняет файловую систему между проверкой пути
и финальной операцией.

Результаты инструмента и событие завершения содержат только нормализованный
относительный путь, число байтов и SHA-256 fingerprint. Полный записанный
текст в журнал событий не копируется. Offline integration tests используют
изолированные временные Workspace и не выполняют сетевых/API-запросов.

## Verification Boundary v0.1

`WorkspaceVerifier` checks one caller-supplied `VerificationExpectation` against an explicitly
provided `Workspace`. It reuses the Workspace normalizer, containment checks, and link/reparse
point rejection. Expectations cover file existence or absence and, optionally, a content SHA-256.
It reads only a regular file inside the workspace when content verification is requested; it
never creates directories, changes files, invokes a write tool, or repairs a failed result.

Mutation != Verification. `WriteProjectFile` performs an approved mutation; the verifier
independently observes the resulting workspace state. The intended sequence is **Write → Observe
→ Verify**. A safe `verification_completed` event records the run and verification IDs, normalized
target when valid, outcome code, and content fingerprint when computed. It does not record file
contents. This boundary is read-only and is not an OS sandbox; a hostile concurrent filesystem
change between checks and open remains a limitation of the existing Workspace boundary.


## Verification Boundary v0.1 — русская версия

`WorkspaceVerifier` проверяет одно переданное вызывающим `VerificationExpectation`
относительно явно предоставленного `Workspace`. Он повторно использует нормализатор
Workspace, проверки нахождения в границах и отклонение ссылок/reparse points.
Ожидания охватывают наличие или отсутствие файла и, необязательно, SHA-256
содержимого. Он читает только обычный файл внутри workspace, когда запрошена
проверка содержимого; он никогда не создаёт каталоги, не изменяет файлы, не
вызывает инструмент записи и не исправляет неуспешный результат.

Mutation != Verification. `WriteProjectFile` выполняет одобренное изменение;
verifier независимо наблюдает получившееся состояние workspace. Предполагаемая
последовательность — **Write -> Observe -> Verify**. Безопасное событие
`verification_completed` фиксирует ID запуска и верификации, нормализованную цель
(когда она валидна), код результата и fingerprint содержимого (когда он вычислен).
Оно не фиксирует содержимое файлов. Эта граница только для чтения и не является
OS-песочницей; враждебное параллельное изменение файловой системы между проверкой и
открытием остаётся ограничением существующей границы Workspace.
## Execution Authorization Hardening v0.1

Block 1 makes execution authorization fail closed. `app.execution.identity` is the single
authoritative command identity mechanism used by request validation, Agent Harness,
`ExecutionCoordinator`, and `ExecutionPolicy`. Identity preserves the canonical executable
identity and the complete argv; basename/stem substitution is not accepted. Exact full
`CommandIdentity` entries can authorize one exact argv sequence. Interpreter evaluation flags
such as `python -c` require that exact argv authorization.

The execution order remains:

```text
Command Identity → Permission → Approval → Execution Policy → Execution
```

Missing `allowed_commands` is denied before execution. If a request explicitly requires approval,
missing `ApprovalPolicy`, a missing resolver, or a non-approved decision is denied or held. When
approval is required, the `ApprovalRequest` carries a deterministic intent fingerprint derived
from executable identity, argv, workspace-relative directory, profile identity, network policy,
and timeout. Secret environment values are excluded. The direct EngineeringRun path uses this
same coordinator and identity mechanism.

This block does not provide an OS sandbox, network isolation, environment hardening, dotfile
filtering, host filesystem isolation, hardlink protection, or durable approval persistence.

## Усиление авторизации выполнения v0.1 — русская версия

Block 1 переводит авторизацию выполнения в fail-closed режим. `app.execution.identity` является
единым authoritative-механизмом идентичности команды для валидации запроса, Agent Harness,
`ExecutionCoordinator` и `ExecutionPolicy`. Идентичность сохраняет каноническое имя executable
и полный argv; подмена через basename/stem не принимается. Точная запись `CommandIdentity`
может разрешить только одну точную последовательность argv. Флаги вычисления интерпретатора,
например `python -c`, требуют такой точной авторизации argv.

Порядок выполнения сохраняется:

```text
Command Identity → Permission → Approval → Execution Policy → Execution
```

Отсутствующий `allowed_commands` приводит к отказу до запуска. Если запрос явно требует approval,
отсутствующие `ApprovalPolicy` или resolver, а также неутверждённое решение приводят к отказу
или ожиданию. При необходимости approval объект `ApprovalRequest` получает детерминированный
fingerprint намерения, рассчитанный по executable, argv, относительной рабочей директории,
идентичности профиля, сетевой политике и таймауту. Секретные значения окружения не включаются.
Прямой путь EngineeringRun использует тот же coordinator и механизм идентичности.

Этот блок не реализует OS sandbox, изоляцию сети, усиление окружения, фильтрацию dotfiles,
изоляцию файловой системы хоста, защиту от hardlink или долговременное хранение approval.

## Acceptance Gate v0.1

`AcceptanceGate` makes a deterministic aggregate decision from provider-neutral
`AcceptanceCriterion` values and existing `VerificationResult` observations. Each
criterion ID maps to one verification result. PASS requires at least one criterion
and PASS for every required criterion. A missing or non-PASS required observation,
an empty criteria set, invalid criteria, or duplicate IDs produces FAIL. Optional
criterion failures remain visible in per-criterion results but do not fail the
aggregate. `acceptance_completed` records only the run ID, status, required,
passed and failed counts, and a safe code. The gate does not mutate requirements,
retry tools, invoke agents, or repair files.

The boundary sequence is **Mutation → Verification → VerificationResult →
AcceptanceGate → AcceptanceResult**. Verification observes a concrete workspace
expectation; acceptance decides whether the task's required criteria passed.
Offline integration coverage uses an approved `WriteProjectFile`, a temporary
workspace, `WorkspaceVerifier`, and the gate. It makes no network/API calls.

## Acceptance Gate v0.1 — русская версия

`AcceptanceGate` детерминированно принимает агрегированное решение на основе
provider-neutral объектов `AcceptanceCriterion` и существующих наблюдений
`VerificationResult`. ID каждого критерия сопоставляется одному результату
проверки. PASS требует хотя бы одного критерия и PASS по каждому обязательному
критерию. Отсутствующее или неуспешное обязательное наблюдение, пустой набор,
некорректные критерии или повторяющиеся ID дают FAIL. Неуспех необязательного
критерия виден в его результате, но не меняет итог. `acceptance_completed`
содержит только ID запуска, статус, число обязательных, успешных и неуспешных
критериев и безопасный код. Gate не меняет требования, не повторяет вызовы
инструментов, не запускает агентов и не исправляет файлы.

Последовательность границ: **Mutation → Verification → VerificationResult →
AcceptanceGate → AcceptanceResult**. Verification наблюдает конкретное ожидание
для workspace; acceptance решает, выполнены ли обязательные критерии задачи.
Offline-интеграционный тест использует одобренный `WriteProjectFile`, временный
workspace, `WorkspaceVerifier` и gate. Сетевые/API-вызовы не выполняются.

## Revision Loop v0.1

`RevisionLoopExecutor` performs one initial Run and evaluates the supplied
verification expectations and acceptance criteria. Only an observed Acceptance
FAIL can start a revision. Revisions are bounded by the configurable
`max_revision_attempts` (default 1); zero disables revisions. Each revision
re-enters `RunExecutor` with the failed criterion IDs/codes, reason code, and
monotonic attempt number in assembled task context. Its proposed tool calls pass
through the same PermissionPolicy, ApprovalPolicy, and Workspace checks, followed
by Verification and Acceptance. Pending/rejected approvals, denied tool calls,
workspace security failures, and unavailable acceptance stop without revision.
`revision_started` and `revision_completed` contain safe identifiers, status, and
reason codes only. Reaching the bound returns `LIMIT_REACHED`; no third attempt
is started beyond the configured limit.

Acceptance FAIL → bounded Revision → Verification → Acceptance is an in-memory
controlled cycle. Revision cannot approve its own mutation or declare acceptance.
The offline integration tests use deterministic fixtures and temporary workspaces.

## Revision Loop v0.1 — русская версия

`RevisionLoopExecutor` выполняет первоначальный Run и оценивает переданные
ожидания Verification и критерии Acceptance. Revision запускается только после
полученного Acceptance FAIL. Число повторов ограничено параметром
`max_revision_attempts` (по умолчанию 1); значение 0 отключает повторы. Каждый
повтор снова проходит через `RunExecutor`; в собранный контекст задачи передаются
ID и коды проваленных критериев, код причины и монотонный номер попытки.
Предложенные инструментом изменения проходят те же PermissionPolicy,
ApprovalPolicy и проверки Workspace, затем Verification и Acceptance.
Ожидающее/отклонённое подтверждение, запрещённый вызов инструмента, ошибка
безопасности workspace или отсутствие результата Acceptance останавливают цикл.
`revision_started` и `revision_completed` содержат только безопасные ID, статусы
и коды причин. При достижении лимита возвращается `LIMIT_REACHED`; сверх лимита
новые попытки не запускаются.

Acceptance FAIL → ограниченная Revision → Verification → Acceptance — это
управляемый цикл в памяти процесса. Revision не может сама одобрить изменение
или объявить Acceptance. Offline-интеграционные тесты используют детерминированные
фикстуры и временные workspace.

## ChangeSet / Artifact Boundary v0.1

`ChangeSetCollector` observes only explicitly targeted mutating tool calls after
Permission and Approval pass. It fingerprints the target before and after the
tool operation through the provided Workspace boundary and groups net file
changes by `run_id` and attempt number. Created files have no before fingerprint;
modified files record both SHA-256 fingerprints; unchanged targets are omitted.
Paths outside Workspace and files whose safe state cannot be read are not added.
No file contents or workspace-wide scan are used. Delete remains a reserved
change type because no delete tool exists.

Each non-empty ChangeSet has a linked in-memory `Artifact` of type `CHANGESET`.
Revision attempts retain separate ChangeSets; the ChangeSet can also carry that
attempt's Verification and Acceptance status. The safe `changeset_created` event
records the Run, attempt, change count, artifact ID, and per-file relative path,
type, and fingerprints. ChangeSet describes the actual mutation; Artifact points
to that result; Verification checks the result; Acceptance decides whether the
required task criteria are met. This is in-memory traceability, not Git or an
artifact storage service.

## Граница ChangeSet / Artifact v0.1 — русская версия

`ChangeSetCollector` наблюдает только явно указанные вызовы инструмента
изменения файлов после прохождения Permission и Approval. Он вычисляет
fingerprint цели до и после операции через переданную границу Workspace и
группирует итоговые изменения по `run_id` и номеру попытки. Для созданного
файла fingerprint до записи отсутствует; для изменённого сохраняются оба
SHA-256 fingerprint; неизменённые цели исключаются. Пути за пределами Workspace
и файлы, безопасное состояние которых нельзя прочитать, не добавляются.
Содержимое файлов и полный обход workspace не используются. Тип удаления
зарезервирован, поскольку инструмента удаления пока нет.

Для каждого непустого ChangeSet создаётся связанный in-memory `Artifact` типа
`CHANGESET`. Для каждой revision сохраняется отдельный ChangeSet; в нём также
могут быть статусы Verification и Acceptance этой попытки. Безопасное событие
`changeset_created` содержит Run, номер попытки, число изменений, ID Artifact
и по каждому файлу относительный путь, тип и fingerprints. ChangeSet описывает
фактическое изменение; Artifact ссылается на этот результат; Verification
проверяет результат; Acceptance решает, выполнены ли обязательные критерии
задачи. Это трассируемость в памяти процесса, а не Git или сервис хранения
артефактов.

## Project Snapshot Boundary v0.1

`ProjectSnapshotter` observes only the explicit relative paths passed by the
caller and reuses `Workspace.resolve_target` / `verify_target` for containment
and link rejection. It records existence and SHA-256 fingerprints without
storing file contents or scanning directories. Missing requested files are
recorded with `exists=false` and no fingerprint; unsafe paths and unreadable
targets fail the snapshot. Each non-empty snapshot is linked to an in-memory
`PROJECT_SNAPSHOT` Artifact and a safe `snapshot_created` event. Workspace has
no separate stable ID, so the snapshot carries `run_id` and `attempt_number`.

When `RevisionLoopExecutor.execute(..., snapshot_paths=...)` is used, the
explicit file set is observed before and after each attempt. Snapshots for
initial execution use attempt 0; each revision uses its own attempt number.
The event order records the initial state, mutation and ChangeSet, then the
after-state before Verification and Acceptance. A snapshot describes observed
state; a ChangeSet describes the mutation; Verification checks expectations;
Acceptance decides whether required task criteria passed. Snapshots are
read-only in-memory artifacts, not backups or version history.

## Граница Project Snapshot v0.1 — русская версия

`ProjectSnapshotter` наблюдает только явно переданные относительные пути и
использует `Workspace.resolve_target` / `verify_target` для проверки границы
и отклонения ссылок. Он записывает наличие файла и SHA-256 fingerprints, не
сохраняя содержимое и не сканируя каталоги. Отсутствующий запрошенный файл
записывается с `exists=false` и без fingerprint; небезопасные пути и цели,
которые нельзя безопасно прочитать, завершают Snapshot ошибкой. Каждый
непустой Snapshot связан с in-memory Artifact типа `PROJECT_SNAPSHOT` и
безопасным событием `snapshot_created`. У Workspace нет отдельного стабильного
ID, поэтому Snapshot содержит `run_id` и `attempt_number`.

При вызове `RevisionLoopExecutor.execute(..., snapshot_paths=...)` указанный
список файлов наблюдается до и после каждой попытки. Для первоначального
выполнения используется attempt 0; каждая revision получает собственный номер
попытки. Порядок событий фиксирует исходное состояние, изменение и ChangeSet,
затем состояние после изменения до Verification и Acceptance. Snapshot
описывает наблюдаемое состояние; ChangeSet — изменение; Verification проверяет
ожидания; Acceptance решает, выполнены ли обязательные критерии задачи.
Snapshot — read-only артефакт в памяти, а не резервная копия или история версий.

## Engineering Run v0.1

`EngineeringRunExecutor` is a provider-neutral facade over the existing
`RevisionLoopExecutor`. Its request combines a task, Workspace, explicit
snapshot paths, verification expectations, acceptance criteria, routing and
permission inputs, and a bounded revision limit. It does not create a second
execution or revision engine: every attempt still passes through the existing
`RunExecutor`, tool permission and approval checks, Workspace boundary,
ChangeSet collector, verifier, and acceptance gate.

The result retains the existing `Run` plus an attempt-indexed record of
snapshots, ChangeSets, verification results, and acceptance results. It also
provides flattened collections for callers that need a run-wide view. The
terminal status is `SUCCESS`, `FAILED`, `WAITING_FOR_APPROVAL`, or
`LIMIT_REACHED`. Safe `engineering_run_started` and
`engineering_run_completed` events bracket the lifecycle; the completion event
contains only the status, counts, and a stable reason code. All records remain
in memory. Persistence, rollback, Git operations, provider selection policy,
and autonomous deployment are outside this boundary.

## Engineering Run v0.1 — русская версия

`EngineeringRunExecutor` — provider-neutral фасад поверх существующего
`RevisionLoopExecutor`. Запрос объединяет задачу, Workspace, явно перечисленные
пути для Snapshot, ожидания Verification, критерии Acceptance, параметры
маршрутизации и разрешений, а также ограничение числа revision. Он не создаёт
второй механизм выполнения или revision: каждая попытка по-прежнему проходит
через существующие `RunExecutor`, проверки разрешений и подтверждения
инструментов, границу Workspace, сборщик ChangeSet, verifier и Acceptance gate.

Результат сохраняет существующий `Run` и запись по каждой попытке с Snapshot,
ChangeSet, результатами Verification и Acceptance. Также доступны объединённые
коллекции для просмотра всего Run. Итоговый статус: `SUCCESS`, `FAILED`,
`WAITING_FOR_APPROVAL` или `LIMIT_REACHED`. Безопасные события
`engineering_run_started` и `engineering_run_completed` ограничивают жизненный
цикл; событие завершения содержит только статус, количества и стабильный код
причины. Все записи остаются в памяти. Хранение, откат, операции Git, политика
выбора провайдера и автономный деплой не входят в эту границу.

## Task Specification / Acceptance Input Boundary v0.1

`TaskSpecification` is the provider-neutral input contract for what the user
wants completed: `task_id`, title, description, `Requirement` values, and the
existing `AcceptanceCriterion` values. A Requirement states what must be done;
Verification observes whether an expected result exists; AcceptanceCriterion
states how completion is judged. Requirements do not become Verification
results. `AcceptanceGate` remains the only component that aggregates the
criteria into PASS or FAIL.

`EngineeringRunRequest` accepts either the legacy `Task` plus explicit
acceptance criteria or a `TaskSpecification`. The specification path adapts to
the existing `Task` and `ContextAssembler`; its data is marked as untrusted task
context and is reused for revisions. It cannot set tool permissions, approval,
Workspace boundaries, or the revision limit. A valid specification supplies
the criteria passed to the existing `AcceptanceGate`; the request rejects a
second independent criteria field. Invalid or structurally incomplete
specifications fail validation before a Run starts. `EngineeringRunResult` and
the safe `engineering_run_started` event carry `task_id` without copying the
full specification into events. The contract is structural and deterministic;
it does not infer requirements or perform semantic or LLM validation.

## Task Specification / Acceptance Input Boundary v0.1 — русская версия

`TaskSpecification` — provider-neutral входной контракт того, что нужно
выполнить: `task_id`, заголовок, описание, значения `Requirement` и существующие
значения `AcceptanceCriterion`. Requirement описывает, что необходимо сделать;
Verification наблюдает, существует ли ожидаемый результат; AcceptanceCriterion
задаёт способ оценки выполнения. Requirements не превращаются в результаты
Verification. `AcceptanceGate` остаётся единственным компонентом, который
агрегирует критерии в PASS или FAIL.

`EngineeringRunRequest` принимает либо прежнюю пару `Task` с явно заданными
критериями Acceptance, либо `TaskSpecification`. Ветка со спецификацией
адаптирует её к существующему `Task` и `ContextAssembler`; данные спецификации
помечаются как недоверенный task context и повторно используются в revision.
Спецификация не задаёт разрешения инструментов, approval, границы Workspace или
лимит revision. Валидная спецификация предоставляет критерии для существующего
`AcceptanceGate`; второй независимый набор критериев в запросе запрещён.
Невалидная или структурно неполная спецификация отклоняется до запуска Run.
`EngineeringRunResult` и безопасное событие `engineering_run_started` содержат
`task_id`, не копируя спецификацию целиком в события. Контракт выполняет только
структурную детерминированную проверку: он не выводит требования и не выполняет
семантическую или LLM-валидацию.

## Requirement Acceptance Traceability v0.1

`AcceptanceCriterion` explicitly references its owning `Requirement` via `requirement_id`.
`TaskSpecification.validate()` deterministically enforces that every criterion references a valid
requirement in the specification, disallows orphan criteria, requires non-empty identifiers, and
guarantees that every required `Requirement` has at least one associated `AcceptanceCriterion`.
Optional requirements are permitted without criteria and are recorded as `SKIPPED`.

`AcceptanceGate` evaluates verification outcomes and aggregates them into a structured, typed
`DetailedAcceptanceReport` alongside the aggregate `AcceptanceResult`. A required `Requirement`
fails if any of its required criteria fail, causing overall acceptance to fail. Failures of optional
requirements are visible in `DetailedAcceptanceReport` but do not fail overall acceptance. When
acceptance fails, `RevisionLoopExecutor` extracts structured requirement-level failure data
(`FailedRequirement` and `FailedCriterion` with `requirement_id` and verification outcome codes)
and attaches it to the revision task context, ensuring deterministic defect localization without
relying on unparsed LLM text.

## Requirement Acceptance Traceability v0.1 — русская версия

`AcceptanceCriterion` явно ссылается на породившее его требование `Requirement` через поле `requirement_id`.
Валидатор `TaskSpecification.validate()` детерминированно гарантирует, что каждый критерий ссылается на
валидное требование спецификации, запрещает критерии-сироты (orphan criteria), требует непустые
идентификаторы и обеспечивает наличие хотя бы одного `AcceptanceCriterion` для каждого обязательного
`Requirement`. Необязательные требования допускаются без критериев и отмечаются статусом `SKIPPED`.

`AcceptanceGate` оценивает результаты верификации и формирует структурированный типизированный отчёт
`DetailedAcceptanceReport` вместе с итоговым вердиктом `AcceptanceResult`. Обязательное требование
проваливается, если хотя бы один из его обязательных критериев не пройден, что приводит к итоговому
FAIL приёмки. Провалы необязательных требований фиксируются в `DetailedAcceptanceReport`, но не
приводят к общему провалу приёмки. При отказе приёмки `RevisionLoopExecutor` извлекает структурированные
данные о сбое уровня требований (`FailedRequirement` и `FailedCriterion` с указанием `requirement_id` и
кодов верификации) и передаёт их в контекст задачи ревизии, гарантируя детерминированную локализацию
дефектов без использования неструктурированного текста LLM.

## Execution Plane Contracts and Project Execution Profile v0.1

The Execution Plane contract introduces typed domain models defining where, what, and how tasks execute without executing actual shell processes, containers, or network operations:

- `ProjectExecutionProfile`: declares the target execution environment (`ExecutionEnvironmentType`: `HOST`, `VIRTUALENV`, `CONTAINER`, `CUSTOM`), runtime name and optional version, target operating system (`TargetOS`), workspace-relative working directory, timeout, output byte limits, network access flag, and an optional whitelist of `allowed_commands`. Deterministic validation enforces safe relative paths, positive limits, and valid environment variable identifiers.
- `ExecutionRequest`: specifies a declared execution action via a typed command tuple (`command` argv sequence), working directory, request-level environment variables, and optional profile constraints. When evaluated against a profile, requests for unallowed executables are rejected before execution.
- `ExecutionResult`: captures the outcome of an execution request, including typed `ExecutionStatus` (`SUCCESS`, `FAILURE`, `TIMEOUT`, `DENIED`, `ERROR`), integer exit code, captured standard output and error streams, duration, and output truncation flag.
- `SecretRedactor` / `DefaultSecretRedactor`: ensures execution metadata, command arguments, environment variables, and output streams are safely redacted before storage or event emission. Known secret tokens, high-entropy API key patterns (`sk-...`, `ghp_...`, `ghs_...`, `github_pat_...`, Bearer tokens), and sensitive key patterns (`token`, `secret`, `password`, `key`, `auth`, `credential`) are replaced with `[REDACTED]`. GitHub App installation tokens are ~520 characters since 2026-10-02 and are matched by prefix without any length assumption. A credential value is also recognised by its own shape through `looks_like_credential`, so a secret is redacted even when the caller carries it under an innocuous key name.
- Integration: `TaskSpecification` and `EngineeringRunRequest` support attaching a `ProjectExecutionProfile`. The profile is validated, serialized into task context, exposed on `EngineeringRunResult`, and emitted in safe run start event metadata without leaking secret or environment payloads.

## Execution Plane Contracts and Project Execution Profile v0.1 — русская версия

Контракт Execution Plane вводит типизированные доменные модели, определяющие где, что и как должно выполняться, без запуска реальных процессов shell, контейнеров или сетевых вызовов:

- `ProjectExecutionProfile`: декларирует целевое окружение выполнения (`ExecutionEnvironmentType`: `HOST`, `VIRTUALENV`, `CONTAINER`, `CUSTOM`), имя и версию рантайма, целевую ОС (`TargetOS`), относительную рабочую директорию, таймаут, лимит вывода в байтах, флаг доступа к сети и опциональный список разрешённых команд `allowed_commands`. Детерминированная валидация проверяет безопасность путей, положительные лимиты и корректность имён переменных окружения.
- `ExecutionRequest`: специфицирует конкретное действие выполнения через типизированный кортеж аргументов команды (`command` argv), рабочую директорию, переменные окружения уровня запроса и ограничения профиля. При проверке относительно профиля запросы с неразрешёнными командами отклоняются до запуска.
- `ExecutionResult`: фиксирует результат выполнения, включая типизированный `ExecutionStatus` (`SUCCESS`, `FAILURE`, `TIMEOUT`, `DENIED`, `ERROR`), целочисленный код возврата, стандартный вывод и поток ошибок, длительность и признак усечения вывода.
- `SecretRedactor` / `DefaultSecretRedactor`: обеспечивает безопасную маскировку секретов в метаданных выполнения, аргументах команд, переменных окружения и потоках вывода перед сохранением или отправкой событий. Зарегистрированные секреты, шаблоны API-ключей (`sk-...`, `ghp_...`, `ghs_...`, `github_pat_...`, Bearer) и чувствительные имена ключей (`token`, `secret`, `password`, `key`, `auth`, `credential`) заменяются на `[REDACTED]`. Токены установки GitHub App с 2026-10-02 имеют длину около 520 символов и определяются по префиксу без каких-либо предположений о длине. Значение-credential распознаётся также по собственной форме через `looks_like_credential`, поэтому секрет маскируется даже тогда, когда вызывающий передаёт его под безобидным именем ключа.
- Интеграция: `TaskSpecification` и `EngineeringRunRequest` поддерживают привязку `ProjectExecutionProfile`. Профиль валидируется, сериализуется в контекст задачи, предоставляется в `EngineeringRunResult` и передаётся в безопасных метаданных события запуска без утечки секретов и переменных окружения.

## Execution Boundary and Local Execution Adapter v0.1

> **Historical section.** The behavior below describes the v0.1 execution
> boundary as first introduced. Matching was never basename- or
> case-insensitive in the shipped code, and the current (v0.2) enforcement model
> is defined in "Execution Authorization Contract v0.2" below and in
> [`SOURCE_OF_TRUTH.md`](SOURCE_OF_TRUTH.md).

The Execution Boundary defines the deterministic enforcement layer that bridges authorized `ExecutionRequest`s and local process execution:

- `ExecutionPolicy`: evaluates an execution request against its associated `ProjectExecutionProfile` under a strict default-deny model. It ensures the request exists, the command is non-empty, the profile is valid, the command matches `profile.allowed_commands` by **exact canonical executable identity** (and by exact argv when the entry is a `CommandIdentity`), the working directory is a safe canonical workspace-relative path, timeout does not exceed the profile's `timeout_seconds`, and environment variable names are valid. If policy evaluation fails, `ExecutionStatus.DENIED` is returned and no subprocess is created.
- `LocalExecutionAdapter`: executes policy-authorized requests as local operating system processes strictly without shell (`shell=False`). It resolves working directories within workspace boundaries, overlays profile and request environment variables, enforces timeouts (`ExecutionStatus.TIMEOUT`), captures standard output and error streams, maps exit codes (0 to `SUCCESS`, non-zero to `FAILURE`, unhandled exceptions to `ERROR`), bounds output byte sizes using `profile.max_output_bytes` (setting `truncated=True`), and redacts secrets and sensitive metadata via `SecretRedactor`.

## Execution Boundary and Local Execution Adapter v0.1 — русская версия

Граница выполнения (Execution Boundary) задаёт детерминированный уровень контроля, связывающий авторизованные запросы `ExecutionRequest` с локальным запуском процессов:

- `ExecutionPolicy`: оценивает запрос на выполнение относительно связанного профиля `ProjectExecutionProfile` по строгой модели default-deny. Политика проверяет наличие запроса, непустую команду, валидность профиля, совпадение команды с `profile.allowed_commands` по **точному каноническому идентификатору исполняемого файла** (и по точному argv, если запись — `CommandIdentity`), безопасность канонического относительного пути рабочей директории, непревышение таймаута профиля `timeout_seconds` и корректность имён переменных окружения. При непрохождении проверки возвращается статус `ExecutionStatus.DENIED` и процесс не запускается.
- `LocalExecutionAdapter`: выполняет авторизованные политикой запросы как локальные процессы операционной системы строго без использования shell (`shell=False`). Адаптер контролирует нахождение рабочей директории в границах workspace, формирует окружение на основе профиля и запроса, обеспечивает таймаут (`ExecutionStatus.TIMEOUT`), перехватывает потоки вывода и ошибок, преобразует коды возврата (0 в `SUCCESS`, ненулевой в `FAILURE`, исключения в `ERROR`), ограничивает размер вывода согласно `profile.max_output_bytes` (с установкой флага `truncated=True`) и маскирует секреты и чувствительные метаданные через `SecretRedactor`.

## Engineering Run Execution and Verification Integration v0.1

The Engineering Run execution integration orchestrates the safe, deterministic transition from proposed execution requests to verified acceptance criteria without bypassing security boundaries:

```text
Engineering Run
    ↓
ExecutionRequest
    ↓
Permission / Approval boundary (ExecutionCoordinator)
    ↓
ExecutionPolicy
    ↓
LocalExecutionAdapter
    ↓
ExecutionResult
    ↓
VerificationResult
    ↓
AcceptanceGate
```

- `ExecutionCoordinator`: acts as the single authorization and coordination point enforcing a strict, ordered evaluation sequence:
  1. `ExecutionRequest` reception: records the request and emits the `execution_requested` event.
  2. Permission boundary: checks the requested command against `allowed_execution_commands` (default-deny, **exact canonical executable identity**; no basename or partial matching). If not allowed, returns `ExecutionStatus.DENIED` with `outcome_status=ExecutionOutcomeStatus.PERMISSION_DENIED` and emits `execution_denied`. No process is launched.
  3. Approval boundary: checks whether `ApprovalPolicy` requires human approval for the executable. If required and unresolved/waiting, yields `outcome_status=ExecutionOutcomeStatus.APPROVAL_WAITING` and moves the run to `RunState.WAITING_FOR_APPROVAL`. If rejected, returns `outcome_status=ExecutionOutcomeStatus.APPROVAL_REJECTED` and emits `execution_denied`. No process is launched.
  4. ExecutionPolicy boundary: evaluates the request against `ExecutionPolicy` and profile constraints. Emits `execution_policy_checked`. If denied, returns `outcome_status=ExecutionOutcomeStatus.POLICY_DENIED` and emits `execution_denied`. No process is launched.
  5. LocalExecutionAdapter execution: invoked only after permission, approval, and policy checks all succeed. Emits `execution_started`, executes subprocess strictly without shell (`shell=False`), bounds output bytes, redacts secrets, and emits `execution_completed`.
- Outcome status vocabulary: exact, fine-grained differentiation via `ExecutionOutcomeStatus`: `PERMISSION_DENIED`, `APPROVAL_WAITING`, `APPROVAL_REJECTED`, `POLICY_DENIED`, `EXECUTION_ERROR`, `EXECUTION_TIMEOUT`, `EXECUTION_FAILURE`, and `EXECUTION_SUCCESS`.
- Audit and lifecycle events: safe events (`execution_requested`, `execution_policy_checked`, `execution_started`, `execution_completed`, `execution_denied`) carry only identifiers, statuses, exit codes, and durations. Standard output, standard error, environment variables, and raw secrets are never leaked into event metadata.
- Verification and Acceptance linkage: `VerificationResult` provides an optional `execution_result_id`, and `create_execution_verification_evidence` generates structured criterion evidence containing execution status and exit codes. Execution failure does not automatically fail acceptance unless an acceptance criterion explicitly maps to the execution outcome.
- Backward compatibility: legacy Engineering Runs without execution requests continue to run with 100% functional compatibility.

## Engineering Run Execution and Verification Integration v0.1 — русская версия

Интеграция выполнения в Engineering Run обеспечивает безопасный, детерминированный переход от предложенных запросов выполнения к проверяемым критериям приёмки без обхода границ безопасности:

```text
Engineering Run
    ↓
ExecutionRequest
    ↓
Граница Permission / Approval (ExecutionCoordinator)
    ↓
ExecutionPolicy
    ↓
LocalExecutionAdapter
    ↓
ExecutionResult
    ↓
VerificationResult
    ↓
AcceptanceGate
```

- `ExecutionCoordinator`: выступает единой точкой авторизации и координации, обеспечивающей строгую последовательность проверок:
  1. Получение `ExecutionRequest`: фиксирует запрос и отправляет событие `execution_requested`.
  2. Граница Permission: проверяет запрошенную команду по списку `allowed_execution_commands` (default-deny, **точный канонический идентификатор исполняемого файла**; совпадение по basename или частичное совпадение не поддерживается). При отсутствии разрешения возвращает `ExecutionStatus.DENIED` с `outcome_status=ExecutionOutcomeStatus.PERMISSION_DENIED` и отправляет событие `execution_denied`. Процесс не запускается.
  3. Граница Approval: проверяет, требует ли `ApprovalPolicy` одобрения человека. Если одобрение требуется и находится в ожидании, возвращает `outcome_status=ExecutionOutcomeStatus.APPROVAL_WAITING`, а ран переходит в состояние `RunState.WAITING_FOR_APPROVAL`. При отказе возвращает `outcome_status=ExecutionOutcomeStatus.APPROVAL_REJECTED` и отправляет `execution_denied`. Процесс не запускается.
  4. Граница ExecutionPolicy: проверяет запрос через `ExecutionPolicy` и ограничения профиля. Отправляет событие `execution_policy_checked`. При отклонении возвращает `outcome_status=ExecutionOutcomeStatus.POLICY_DENIED` и отправляет `execution_denied`. Процесс не запускается.
  5. Запуск через LocalExecutionAdapter: вызывается только после успешного прохождения всех трёх предварительных проверок (Permission, Approval, Policy). Отправляет событие `execution_started`, запускает процесс строго без shell (`shell=False`), ограничивает размер вывода, маскирует секреты и отправляет событие `execution_completed`.
- Спектр статусов исхода: точная дифференциация через `ExecutionOutcomeStatus`: `PERMISSION_DENIED`, `APPROVAL_WAITING`, `APPROVAL_REJECTED`, `POLICY_DENIED`, `EXECUTION_ERROR`, `EXECUTION_TIMEOUT`, `EXECUTION_FAILURE` и `EXECUTION_SUCCESS`.
- События жизненного цикла и аудита: безопасные события (`execution_requested`, `execution_policy_checked`, `execution_started`, `execution_completed`, `execution_denied`) содержат только идентификаторы, статусы, коды возврата и длительность. Потоки stdout/stderr, переменные окружения и сырые секреты не попадают в метаданные событий.
- Связывание с верификацией и приёмкой: `VerificationResult` содержит опциональное поле `execution_result_id`, а функция `create_execution_verification_evidence` формирует структурированное свидетельство с данными о выполнении и коде возврата. Сбой выполнения не приводит к автоматическому провалу приёмки, если критерии приёмки не завязаны на этот результат выполнения.
- Обратная совместимость: классические Engineering Run без запросов выполнения продолжают работать со 100% функциональной совместимостью.

## Verification Contract and Objective Evidence v0.2

Verification Contract v0.2 establishes objective evidence boundaries separating process execution from criterion verification:

```text
Requirement
    ↓
AcceptanceCriterion
    ↓
VerificationRequest
    ↓
ExecutionRequest
    ↓
ExecutionResult
    ↓
VerificationEvidence
    ↓
VerificationResult
    ↓
AcceptanceGate
```

- **Fundamental Principle (`ExecutionResult != VerificationResult`):** A zero exit code (`exit_code == 0`) or successful subprocess execution does not by itself prove requirement satisfaction. Verification requires an explicit verification intent, objective comparison against declared expectations, and structured evidence.
- **`VerificationRequest`:** Minimal typed contract capturing verification intent: `verification_id`, `criterion_id`, `verification_type` (`PROCESS_EXIT`, `COMMAND_EXECUTION`, `WORKSPACE_FILE`, `CUSTOM`), `execution_request_id`, `expected_exit_code`, `expected_check`, and `metadata`. Validates non-empty identifiers and integer codes.
- **`VerificationEvidence`:** Typed, structured, audit-grade evidence artifact: `evidence_id`, `verification_id`, `source`, `execution_result_id`, `outcome`, and sanitized `metadata`. LLM opinions or unparsed conversational claims are strictly disallowed as evidence. Lifecycle events and evidence never store raw stdout/stderr, environment variables, or secrets.
- **`VerificationEvaluator`:** Deterministic evaluator converting `VerificationRequest` and `ExecutionResult` into a `VerificationResult` without executing subprocesses or calling LLMs:
  - Exit code matches `expected_exit_code` and status `SUCCESS` → `VerificationStatus.PASS` (`code="execution_matched"`).
  - Exit code mismatch or status `FAILURE` → `VerificationStatus.FAIL` (`code="exit_code_mismatch"` or `"execution_failure"`).
  - Subprocess timeout → `VerificationStatus.ERROR` (`code="execution_timeout"`).
  - Subprocess execution error → `VerificationStatus.ERROR` (`code="execution_error"`).
  - Subprocess authorization denial → `VerificationStatus.DENIED`.
  - Missing execution result → `VerificationStatus.NOT_RUN` (`code="execution_missing"`).
  - Emits safe `verification_requested` and `verification_completed` events carrying metadata only.
- **Deterministic Traceability (`validate_verification_traceability`):** Validates the complete chain `requirement_id → criterion_id → verification_id → execution_result_id → evidence` against:
  - `unknown_requirement`: criteria referencing non-existent requirements.
  - `unknown_criterion`: verifications referencing non-existent criteria.
  - `duplicate_verification_id`: collisions in verification identifiers.
  - `missing_evidence`: verifications without structured evidence.
  - `orphan_verification`: unmapped or orphaned verifications.
  - `verification_referencing_wrong_criterion`: conflicting criterion references.
- **Acceptance Gate Integration:** `AcceptanceGate` remains the sole authority for evaluating task and requirement acceptance from `VerificationResult` instances.

## Verification Contract and Objective Evidence v0.2 — русская версия

Контракт верификации v0.2 задаёт границы объективных доказательств, строго отделяя выполнение процесса от верификации критериев:

```text
Requirement
    ↓
AcceptanceCriterion
    ↓
VerificationRequest
    ↓
ExecutionRequest
    ↓
ExecutionResult
    ↓
VerificationEvidence
    ↓
VerificationResult
    ↓
AcceptanceGate
```

- **Базовый принцип (`ExecutionResult != VerificationResult`):** Успешное выполнение подпроцесса или нулевой код возврата (`exit_code == 0`) сами по себе не доказывают выполнение требования. Верификация требует явного намерения проверки, объективного сравнения с заявленными ожиданиями и структурированного свидетельства (evidence).
- **`VerificationRequest`:** Минимальный типизированный контракт намерения верификации: `verification_id`, `criterion_id`, `verification_type` (`PROCESS_EXIT`, `COMMAND_EXECUTION`, `WORKSPACE_FILE`, `CUSTOM`), `execution_request_id`, `expected_exit_code`, `expected_check` и `metadata`. Валидирует непустые идентификаторы и целочисленные коды возврата.
- **`VerificationEvidence`:** Типизированное структурированное доказательство пригодное для аудита: `evidence_id`, `verification_id`, `source`, `execution_result_id`, `outcome` и очищенные `metadata`. Мнение LLM или текстовые заявления модели категорически запрещены в качестве evidence. События жизненного цикла и evidence никогда не содержат сырые stdout/stderr, переменные окружения или секреты.
- **`VerificationEvaluator`:** Детерминированный оценщик, преобразующий `VerificationRequest` и `ExecutionResult` в `VerificationResult` без запуска процессов и без обращения к LLM:
  - Код возврата совпадает с `expected_exit_code` и статус `SUCCESS` → `VerificationStatus.PASS` (`code="execution_matched"`).
  - Несовпадение кода возврата или статус `FAILURE` → `VerificationStatus.FAIL` (`code="exit_code_mismatch"` или `"execution_failure"`).
  - Таймаут подпроцесса → `VerificationStatus.ERROR` (`code="execution_timeout"`).
  - Ошибка запуска/исполнения подпроцесса → `VerificationStatus.ERROR` (`code="execution_error"`).
  - Отказ авторизации выполнения → `VerificationStatus.DENIED`.
  - Отсутствие результата выполнения → `VerificationStatus.NOT_RUN` (`code="execution_missing"`).
  - Отправляет безопасные события `verification_requested` и `verification_completed`, содержащие только метаданные.
- **Детерминированная трассируемость (`validate_verification_traceability`):** Проверяет полную цепочку `requirement_id → criterion_id → verification_id → execution_result_id → evidence` на:
  - `unknown_requirement`: критерии, ссылающиеся на несуществующие требования.
  - `unknown_criterion`: верификации, ссылающиеся на несуществующие критерии.
  - `duplicate_verification_id`: коллизии идентификаторов верификаций.
  - `missing_evidence`: верификации без прикреплённого доказательства.
  - `orphan_verification`: потерянные или непривязанные верификации.
  - `verification_referencing_wrong_criterion`: противоречивые ссылки на критерии.
- **Интеграция с Acceptance Gate:** `AcceptanceGate` остаётся единственной инстанцией, принимающей решение по приёмке задачи и требований на основе экземпляров `VerificationResult`.

## Test Verification Adapter v0.1

The Test Verification Adapter introduces a framework-neutral adapter that transforms declarative test verification intent into execution and verification requests without launching subprocesses:

```text
AcceptanceCriterion
        ↓
VerificationRequest
        ↓
TestVerificationAdapter
        ↓
ExecutionRequest
        ↓
ExecutionCoordinator
        ↓
Permission / Approval / ExecutionPolicy
        ↓
LocalExecutionAdapter
        ↓
ExecutionResult
        ↓
VerificationEvaluator
        ↓
VerificationResult
        ↓
AcceptanceGate
```

- **Declarative Test Intent (`TestVerificationIntent`):** Specifies test execution intent strictly through an argument sequence tuple (`command: tuple[str, ...]`), target criterion (`criterion_id`), framework category (`TestFramework`: `GENERIC`, `PYTEST`, `UNITTEST`, `CUSTOM`), expected exit code (`expected_exit_code`), safe relative working directory, timeout, and profile constraints. Unparsed shell command strings are strictly forbidden.
- **Execution Delegation:** `TestVerificationAdapter` does not execute subprocesses and does not make authorization decisions. It builds an `ExecutionRequest` which must be dispatched through `ExecutionCoordinator`. All security boundaries (`Permission`, `Approval`, `ExecutionPolicy`) remain mandatory and cannot be bypassed.
- **Verification Evaluation:** Rather than duplicating verification evaluation logic, the adapter delegates execution evaluation to the existing `VerificationEvaluator`, maintaining uniform objective semantics:
  - Exit code matches expected code → `VerificationStatus.PASS`.
  - Exit code mismatch or failure status → `VerificationStatus.FAIL`.
  - Process timeout → `VerificationStatus.ERROR`.
  - Execution error → `VerificationStatus.ERROR`.
  - Authorization denial → `VerificationStatus.DENIED`.
- **Traceability Preservation:** Maintains full traceability from `requirement_id` to `criterion_id`, `verification_id`, `execution_request_id`, `execution_result_id`, `evidence`, and final `AcceptanceGate` assessment.

## Test Verification Adapter v0.1 — русская версия

Адаптер верификации тестов (Test Verification Adapter) вводит нейтральный к тестовым фреймворкам уровень, преобразующий декларативное намерение тестирования в запросы выполнения и верификации без самостоятельного запуска подпроцессов:

```text
AcceptanceCriterion
        ↓
VerificationRequest
        ↓
TestVerificationAdapter
        ↓
ExecutionRequest
        ↓
ExecutionCoordinator
        ↓
Permission / Approval / ExecutionPolicy
        ↓
LocalExecutionAdapter
        ↓
ExecutionResult
        ↓
VerificationEvaluator
        ↓
VerificationResult
        ↓
AcceptanceGate
```

- **Декларативное намерение тестирования (`TestVerificationIntent`):** Специфицирует намерение запуска тестов исключительно через кортеж аргументов (`command: tuple[str, ...]`), целевой критерий (`criterion_id`), категорию фреймворка (`TestFramework`: `GENERIC`, `PYTEST`, `UNITTEST`, `CUSTOM`), ожидаемый код возврата (`expected_exit_code`), безопасную относительную рабочую директорию, таймаут и профиль выполнения. Передача сырых shell-строк категорически запрещена.
- **Делегирование выполнения:** `TestVerificationAdapter` не запускает подпроцессы и не принимает решений об авторизации. Он формирует `ExecutionRequest`, который обязан пройти через существующий `ExecutionCoordinator`. Все границы безопасности (`Permission`, `Approval`, `ExecutionPolicy`) обязательны и не могут быть обойдены.
- **Оценка результатов верификации:** Адаптер не дублирует логику оценки, а делегирует её существующему `VerificationEvaluator`, обеспечивая единую объективную семантику:
  - Код возврата совпадает с ожидаемым → `VerificationStatus.PASS`.
  - Код возврата не совпадает или статус `FAILURE` → `VerificationStatus.FAIL`.
  - Таймаут подпроцесса → `VerificationStatus.ERROR`.
  - Ошибка запуска процесса → `VerificationStatus.ERROR`.
  - Отказ авторизации выполнения → `VerificationStatus.DENIED`.
- **Сохранение трассируемости:** Обеспечивает сквозную трассируемость от `requirement_id` к `criterion_id`, `verification_id`, `execution_request_id`, `execution_result_id`, `evidence` и итоговому вердикту `AcceptanceGate`.

## Project State Contract v0.1

Project State Contract v0.1 introduces a deterministic, provider-neutral model (`ProjectState`) that represents the state of a project across an Engineering Run and its revision attempts:

```text
Workspace + ProjectSnapshot (Observed State)
        ↓
ChangeSet (Mutations)
        ↓
ExecutionResult (Process Evidence)
        ↓
VerificationResult (Objective Verification)
        ↓
AcceptanceResult (Acceptance Evaluation)
        ↓
ProjectState (State & Traceability Record)
```

- **ProjectState Model:** An immutable, structured dataclass capturing the lifecycle state of a run attempt:
  - Identity: `run_id`, `attempt_number`, `project_id`, `task_id`.
  - Artifact references: `snapshot_ids`, `changeset_ids`, `execution_result_ids`, `verification_result_ids`.
  - Outcomes: `acceptance_status`, `status` (`ProjectStateStatus`).
  - Convenient accessors: `snapshot_id`, `changeset_id`.
  - Audit serialization: `to_dict()`.
- **State Semantics (`ProjectStateStatus`):** Minimal deterministic lifecycle enum:
  - `INITIAL`: Run initialized, no changesets, execution results, or verifications.
  - `IN_PROGRESS`: Execution or attempt is actively underway without finished artifacts.
  - `CHANGED`: Changesets or execution results are present, but verification has not run.
  - `VERIFIED`: Verification checks executed and all passed, prior to acceptance evaluation.
  - `ACCEPTED`: Acceptance gate evaluated criteria and marked them as passed.
  - `FAILED`: Any verification failed or acceptance gate rejected criteria.
- **Deterministic Derivation (`derive_project_state`):** Pure function mapping attempt artifacts to `ProjectState`. Purges `stdout`, `stderr`, and `raw_output` from `metadata` to prevent secret leaks and stream bloat.
- **Engineering Run Integration:** `EngineeringRunResult.project_states` maintains the chronological sequence of attempt states (e.g. Attempt 1: `FAILED` -> Attempt 2: `ACCEPTED`), with `EngineeringRunResult.final_project_state` pointing to the state of the final attempt.
- **Boundaries & Exclusions:**
  - `ProjectState` is a state and traceability contract, NOT an execution mechanism.
  - It does NOT replace `Permission`, `Approval`, or `ExecutionPolicy` boundaries.
  - It does NOT make acceptance decisions (which remain the sole responsibility of `AcceptanceGate`).
  - It does NOT perform Git operations, rollback/restore, filesystem changes, or database persistence.

## Project State Contract v0.1 — русская версия

Контракт состояния проекта v0.1 (Project State Contract v0.1) определяет детерминированную, нейтральную к провайдерам модель (`ProjectState`), фиксирующую состояние проекта на протяжении инженерного запуска (Engineering Run) и его попыток доработки:

```text
Workspace + ProjectSnapshot (Наблюдаемое состояние)
        ↓
ChangeSet (Изменения)
        ↓
ExecutionResult (Свидетельства выполнения)
        ↓
VerificationResult (Объективная верификация)
        ↓
AcceptanceResult (Оценка приёмки)
        ↓
ProjectState (Запись состояния и трассируемости)
```

- **Модель `ProjectState`:** Неизменяемый структурированный dataclass, фиксирующий состояние попытки запуска:
  - Идентификация: `run_id`, `attempt_number`, `project_id`, `task_id`.
  - Ссылки на артефакты: `snapshot_ids`, `changeset_ids`, `execution_result_ids`, `verification_result_ids`.
  - Итоги: `acceptance_status`, `status` (`ProjectStateStatus`).
  - Удобные свойства: `snapshot_id`, `changeset_id`.
  - Сериализация для аудита: `to_dict()`.
- **Семантика состояний (`ProjectStateStatus`):** Минимальный детерминированный enum жизненного цикла:
  - `INITIAL`: Запуск инициализирован, артефактов изменений, выполнения или верификации ещё нет.
  - `IN_PROGRESS`: Запуск или попытка выполняется в текущий момент.
  - `CHANGED`: Сформированы изменения (ChangeSet) или результаты выполнения, но они ещё не верифицированы.
  - `VERIFIED`: Проверки верификации завершены и все прошли успешно (до оценки приёмки).
  - `ACCEPTED`: Критерии проверены `AcceptanceGate` и успешно приняты.
  - `FAILED`: Ошибка верификации или отклонение критериев приёмкой.
- **Детерминированный вывод (`derive_project_state`):** Чистая функция, формирующая `ProjectState` на основе артефактов попытки. Очищает `metadata` от `stdout`, `stderr` и `raw_output` для предотвращения утечки секретов и засорения контекста.
- **Интеграция с Engineering Run:** `EngineeringRunResult.project_states` сохраняет хронологическую последовательность состояний попыток (например, Attempt 1: `FAILED` -> Attempt 2: `ACCEPTED`), а свойство `EngineeringRunResult.final_project_state` возвращает состояние последней попытки.
- **Границы и исключения:**
  - `ProjectState` является контрактом состояния и трассируемости, а НЕ механизмом выполнения.
  - Он НЕ заменяет границы `Permission`, `Approval` или `ExecutionPolicy`.
  - Он НЕ принимает решений о приёмке (это исключительная ответственность `AcceptanceGate`).
  - Он НЕ выполняет операций Git, отката/восстановления файлов или сохранения в базу данных.

## Run Trace / Event Contract v0.1

Run Trace / Event Contract v0.1 introduces a deterministic, provider-neutral in-memory event and trace model (`RunEvent`, `RunTrace`, `RunEventCollector`) providing chronological observability across an Engineering Run:

```text
Run Lifecycle Facts
        ↓
RunEvent (Ordered, Immutable Observation)
        ↓
RunEventCollector / RunTrace (Monotonic Sequence & Run Binding)
        ↓
EngineeringRunResult.trace / EngineeringRunResult.events
```

- **Purpose:** Provide a safe, audit-grade chronological trace of execution milestones connecting Forge artifacts without introducing distributed tracing, event sourcing, databases, or streaming infrastructure.
- **RunEvent Model:** An immutable, frozen dataclass capturing safe lifecycle facts:
  - Identity & Ordering: `run_id`, `sequence_number` (monotonic integer starting at 0), `event_type` (`RunEventType`), `event_id`, `timestamp`.
  - Context & Revision: `attempt_number`, `task_id`.
  - Artifact references: `requirement_id`, `criterion_id`, `execution_request_id`, `execution_result_id`, `verification_id`, `changeset_id`, `snapshot_id`, `project_state_status`, `acceptance_status`.
  - Sanitized metadata: `metadata: Mapping[str, object]`.
- **Event Semantics (`RunEventType`):** Minimal canonical lifecycle enum including `RUN_STARTED`, `CONTEXT_ASSEMBLED`, `EXECUTION_REQUESTED`, `EXECUTION_POLICY_CHECKED`, `EXECUTION_STARTED`, `EXECUTION_COMPLETED`, `EXECUTION_DENIED`, `APPROVAL_REQUESTED`, `APPROVAL_RESOLVED`, `VERIFICATION_COMPLETED`, `ACCEPTANCE_COMPLETED`, `REVISION_STARTED`, `REVISION_COMPLETED`, `PROJECT_STATE_UPDATED`, `RUN_COMPLETED`.
- **Deterministic Sequence Ordering:** Authoritative ordering within a Run is established strictly by `run_id + sequence_number`. Wall-clock timestamps provide informational context but sequence monotonicity (0, 1, 2, ...) governs trace validity. Out-of-order, duplicate, or negative sequence numbers are strictly rejected.
- **Metadata Sanitization & Security:** Automatic deterministic filtering purges `stdout`, `stderr`, `raw_output`, `prompt`, `chain_of_thought`, `secret`, `token`, `password`, `api_key`, `credential`, `source_code`, and `file_content` from event metadata. Events store references only, never raw streams or source code.
- **Trace Collection (`RunTrace`, `RunEventCollector`):** In-memory append-only collector binding events to `run_id`, enforcing sequential continuity, and exposing an immutable tuple of events (`trace.events`). Cross-run events and sequence violations raise `ValueError`.
- **Relationship to EngineeringRun & ProjectState:** `EngineeringRunResult.trace` aggregates the full run trace. When each attempt's `ProjectState` is derived, `PROJECT_STATE_UPDATED` events record state transitions referencing `snapshot_id`, `changeset_id`, and `acceptance_status`. The final event in the trace is `RUN_COMPLETED`.
- **What RunTrace is NOT:**
  - NOT a persistence layer or database event store.
  - NOT an event sourcing, message queue, Kafka, or Redis system.
  - NOT an execution mechanism, permission boundary, or approval authority.
  - NOT an acceptance authority (`AcceptanceGate` remains the sole decider).

## Run Trace / Event Contract v0.1 — русская версия

Контракт трассировки и событий запуска v0.1 (Run Trace / Event Contract v0.1) определяет детерминированную, нейтральную к провайдерам in-memory модель событий и трассировки (`RunEvent`, `RunTrace`, `RunEventCollector`), обеспечивающую хронологическую наблюдаемость инженерного запуска (Engineering Run):

```text
Факты жизненного цикла запуска
        ↓
RunEvent (Упорядоченное неизменяемое наблюдение)
        ↓
RunEventCollector / RunTrace (Монотонная последовательность и привязка к run_id)
        ↓
EngineeringRunResult.trace / EngineeringRunResult.events
```

- **Назначение:** Обеспечить безопасную хронологическую трассировку контрольных точек выполнения для аудита, связывающую существующие артефакты Forge без внедрения распределённой трассировки, event sourcing, баз данных или брокеров сообщений.
- **Модель `RunEvent`:** Неизменяемый (frozen) dataclass, фиксирующий факты жизненного цикла:
  - Идентификация и порядок: `run_id`, `sequence_number` (монотонное целое число, начиная с 0), `event_type` (`RunEventType`), `event_id`, `timestamp`.
  - Контекст и попытки: `attempt_number`, `task_id`.
  - Ссылки на артефакты: `requirement_id`, `criterion_id`, `execution_request_id`, `execution_result_id`, `verification_id`, `changeset_id`, `snapshot_id`, `project_state_status`, `acceptance_status`.
  - Очищенные метаданные: `metadata: Mapping[str, object]`.
- **Семантика событий (`RunEventType`):** Минимальный канонический enum жизненного цикла, включающий `RUN_STARTED`, `CONTEXT_ASSEMBLED`, `EXECUTION_REQUESTED`, `EXECUTION_POLICY_CHECKED`, `EXECUTION_STARTED`, `EXECUTION_COMPLETED`, `EXECUTION_DENIED`, `APPROVAL_REQUESTED`, `APPROVAL_RESOLVED`, `VERIFICATION_COMPLETED`, `ACCEPTANCE_COMPLETED`, `REVISION_STARTED`, `REVISION_COMPLETED`, `PROJECT_STATE_UPDATED`, `RUN_COMPLETED`.
- **Детерминированный порядок последовательности:** Авторитетный порядок внутри запуска определяется строго парой `run_id + sequence_number`. Метки времени носят информационный характер, тогда как монотонность последовательности (0, 1, 2, ...) определяет валидность трассы. Непоследовательные, дублирующиеся или отрицательные номера последовательности отклоняются с ошибкой `ValueError`.
- **Санитизация метаданных и безопасность:** Автоматическая детерминированная фильтрация удаляет `stdout`, `stderr`, `raw_output`, `prompt`, `chain_of_thought`, `secret`, `token`, `password`, `api_key`, `credential`, `source_code` и `file_content` из метаданных событий. События содержат только идентификаторы ссылок, но никогда сырые потоки или исходный код.
- **Сбор трассы (`RunTrace`, `RunEventCollector`):** In-memory коллектор, выполняющий только добавление событий с проверкой привязки к `run_id`, соблюдения строгой последовательности и предоставляющий неизменяемый кортеж событий (`trace.events`). События с чужим `run_id` или нарушением порядка отклоняются.
- **Связь с EngineeringRun и ProjectState:** `EngineeringRunResult.trace` содержит полную трассу запуска. При формировании `ProjectState` каждой попытки события `PROJECT_STATE_UPDATED` фиксируют переходы состояний со ссылками на `snapshot_id`, `changeset_id` и `acceptance_status`. Финальным событием трассы является `RUN_COMPLETED`.
- **Чем RunTrace НЕ является:**
  - НЕ является слоем персистентности или хранилищем событий в базе данных.
  - НЕ является event sourcing, очередью сообщений, Kafka или Redis.
  - НЕ является механизмом выполнения, границей прав или инстанцией согласования.
  - НЕ является инстанцией приёмки (`AcceptanceGate` остаётся единственным органом приёмки).

## Decision Layer / Run Control v0.1

Decision Layer / Run Control v0.1 introduces a deterministic, provider-neutral decision contract (`Decision`, `DecisionRequest`, `DecisionType`, `DecisionAction`, `DecisionProvider`, `DeterministicDecisionProvider`, `validate_decision`) that governs WHAT SHOULD HAPPEN NEXT in an Engineering Run without executing actions directly:

```text
ProjectState / Run Context / Blocking Conditions
                    ↓
DecisionRequest (Safe Input Context)
                    ↓
DecisionProvider (Deterministic Precedence Engine)
                    ↓
Decision (Immutable Recommendation)
                    ↓
validate_decision (Run Binding, Compatibility, Capabilities)
                    ↓
EngineeringRun Executor / Event Trace (Observability & Control)
                    ↓
[Existing Authoritative Boundaries: Permission, Approval, ExecutionPolicy, AcceptanceGate]
```

- **Purpose & Core Invariant:** The Decision Layer produces typed recommendations/decisions on run control flow. It does NOT execute actions, make LLM calls, or bypass existing boundaries (`Permission`, `Approval`, `ExecutionPolicy`, `AcceptanceGate`). A decision is NEVER an authorization.
- **Contract Models (`app/decision/models.py`):**
  - `DecisionType`: Coarse decision intent (`CONTINUE`, `REVISE`, `VERIFY`, `REQUEST_APPROVAL`, `WAIT`, `FAIL`, `COMPLETE`).
  - `DecisionAction`: Concrete actionable operation (`EXECUTE`, `RUN_VERIFICATION`, `REQUEST_REVISION`, `REQUEST_USER_APPROVAL`, `WAIT_FOR_APPROVAL`, `COMPLETE_RUN`, `FAIL_RUN`).
  - `DecisionRequest`: Structured input context presenting `run_id`, optional `attempt_number`, `current_project_state`, `acceptance_status`, `verification_status_summary`, `available_actions`, `blocking_conditions`, and sanitized `metadata`.
  - `Decision`: Immutable recommendation containing `decision_id`, `run_id`, `decision_type`, `action`, `reason_code`/`rationale`, optional `confidence`, `attempt_number`, `references`, and sanitized `metadata`.
  - `sanitize_decision_metadata`: Filters out sensitive and raw keys (`stdout`, `stderr`, `raw_output`, `prompt`, `chain_of_thought`, `secret`, `token`, `password`, `api_key`, `credential`, `source_code`, `file_content`) and limits value length to 4096 characters.
- **Decision Validation (`validate_decision`, `DecisionValidationReport`):**
  - Validates `decision.run_id == request.run_id`.
  - Validates `decision_type` and `action` compatibility against `DECISION_TO_ACTION_COMPATIBILITY`.
  - Enforces that `action` is present in `request.available_actions` when capabilities are constrained.
  - Verifies non-negative attempt numbers and attempt consistency.
  - Rejects cross-run references (`references["run_id"] != request.run_id`).
  - Decisions with validation errors are strictly rejected by the run controller.
- **Deterministic Decision Engine (`DeterministicDecisionProvider`):** Implements a strict, deterministic rule precedence order based solely on structured state and blocking conditions:
  - **Rule A (Security/Policy Denial):** Unrecoverable permission, security, or policy failure in `blocking_conditions` (`"security_denied"`, `"permission_denied"`, `"policy_denied"`) -> `FAIL` / `FAIL_RUN`.
  - **Rule B (Revision Limit):** Revision limit reached (`"revision_limit_reached"`) -> `FAIL` / `FAIL_RUN`.
  - **Rule C (Approval Pending):** Human approval required or pending (`"approval_required"`, `"approval_pending"`) -> `REQUEST_APPROVAL` / `REQUEST_USER_APPROVAL` or `WAIT_FOR_APPROVAL`.
  - **Rule D (Acceptance Passed):** Acceptance status is `PASS` or state is `ACCEPTED` -> `COMPLETE` / `COMPLETE_RUN`.
  - **Rule E (Verification Failed):** Verification failed or state is `FAILED` -> `REVISE` / `REQUEST_REVISION`.
  - **Rule F (Verification Required):** State is `CHANGED` or verification required -> `VERIFY` / `RUN_VERIFICATION`.
  - **Rule G (Execution Required):** State is `INITIAL` or execution required -> `CONTINUE` / `EXECUTE`.
  - **Rule H (Default / Idle):** Otherwise -> `WAIT` / `WAIT_FOR_APPROVAL`.
- **Engineering Run & Trace Integration:**
  - `EngineeringRunExecutor` accepts an optional `DecisionProvider` (defaulting to `DeterministicDecisionProvider`).
  - At each project state transition, a `DecisionRequest` is formed and evaluated.
  - Safe trace events are emitted: `DECISION_REQUESTED` and `DECISION_MADE` (or `DECISION_REJECTED` if invalid), populated with `decision_id` in `RunTrace`.
  - `EngineeringRunResult.decisions` exposes all valid accepted decisions, and `EngineeringRunResult.final_decision` provides access to the final decision.
- **What Decision Layer is NOT:**
  - NOT an autonomous agent reasoning system or LLM prompt loop.
  - NOT a provider-specific routing mechanism.
  - NOT a substitute for Permission, Approval, or Execution boundaries.
  - NOT an executor or state mutator.

## Decision Layer / Run Control v0.1 — русская версия

Слой принятия решений и управления запуском v0.1 (Decision Layer / Run Control v0.1) вводит детерминированный, нейтральный к провайдерам контракт решений (`Decision`, `DecisionRequest`, `DecisionType`, `DecisionAction`, `DecisionProvider`, `DeterministicDecisionProvider`, `validate_decision`), определяющий, ЧТО ДОЛЖНО ПРОИЗОЙТИ ДАЛЬШЕ в инженерном запуске (Engineering Run), без непосредственного выполнения действий:

```text
ProjectState / Контекст запуска / Блокирующие условия
                    ↓
DecisionRequest (Безопасный входной контекст)
                    ↓
DecisionProvider (Детерминированный движок приоритетов)
                    ↓
Decision (Неизменяемая рекомендация)
                    ↓
validate_decision (Привязка к запуску, совместимость, возможности)
                    ↓
EngineeringRun Executor / Event Trace (Наблюдаемость и контроль)
                    ↓
[Существующие авторитетные границы: Permission, Approval, ExecutionPolicy, AcceptanceGate]
```

- **Назначение и ключевой инвариант:** Слой решений формирует типизированные рекомендации и решения по управлению потоком запуска. Он НЕ выполняет действия, НЕ обращается к LLM и НЕ обходит существующие границы (`Permission`, `Approval`, `ExecutionPolicy`, `AcceptanceGate`). Решение НИКОГДА не является авторизацией или разрешением.
- **Модели контракта (`app/decision/models.py`):**
  - `DecisionType`: Верхнеуровневый тип намерения (`CONTINUE`, `REVISE`, `VERIFY`, `REQUEST_APPROVAL`, `WAIT`, `FAIL`, `COMPLETE`).
  - `DecisionAction`: Конкретная типизированная операция (`EXECUTE`, `RUN_VERIFICATION`, `REQUEST_REVISION`, `REQUEST_USER_APPROVAL`, `WAIT_FOR_APPROVAL`, `COMPLETE_RUN`, `FAIL_RUN`).
  - `DecisionRequest`: Структурированный входной контекст с полями `run_id`, опциональным `attempt_number`, `current_project_state`, `acceptance_status`, `verification_status_summary`, `available_actions`, `blocking_conditions` и очищенным `metadata`.
  - `Decision`: Неизменяемая рекомендация, содержащая `decision_id`, `run_id`, `decision_type`, `action`, `reason_code`/`rationale`, опциональный `confidence`, `attempt_number`, `references` и очищенный `metadata`.
  - `sanitize_decision_metadata`: Исключает конфиденциальные и сырые ключи (`stdout`, `stderr`, `raw_output`, `prompt`, `chain_of_thought`, `secret`, `token`, `password`, `api_key`, `credential`, `source_code`, `file_content`) и ограничивает размер строковых значений до 4096 символов.
- **Валидация решений (`validate_decision`, `DecisionValidationReport`):**
  - Проверяет равенство `decision.run_id == request.run_id`.
  - Проверяет совместимость `decision_type` и `action` по таблице `DECISION_TO_ACTION_COMPATIBILITY`.
  - Проверяет наличие `action` в `request.available_actions`, если список допустимых действий ограничен.
  - Проверяет неотрицательность номера попытки и соответствие попытке запроса.
  - Отклоняет ссылки на чужие запуски (`references["run_id"] != request.run_id`).
  - Решения с ошибками валидации категорически отклоняются контроллером запуска.
- **Детерминированный движок решений (`DeterministicDecisionProvider`):** Реализует строгую детерминированную иерархию правил на основе структурированного состояния и условий:
  - **Правило A (Отказ безопасности/политики):** Неисправимая ошибка прав, безопасности или политики в `blocking_conditions` (`"security_denied"`, `"permission_denied"`, `"policy_denied"`) -> `FAIL` / `FAIL_RUN`.
  - **Правило B (Превышение лимита):** Достигнут лимит попыток (`"revision_limit_reached"`) -> `FAIL` / `FAIL_RUN`.
  - **Правило C (Ожидание подтверждения):** Требуется или ожидает подтверждение пользователя (`"approval_required"`, `"approval_pending"`) -> `REQUEST_APPROVAL` / `REQUEST_USER_APPROVAL` или `WAIT_FOR_APPROVAL`.
  - **Правило D (Приёмка пройдена):** Статус приёмки `PASS` или состояние `ACCEPTED` -> `COMPLETE` / `COMPLETE_RUN`.
  - **Правило E (Ошибка верификации):** Верификация не пройдена или состояние `FAILED` -> `REVISE` / `REQUEST_REVISION`.
  - **Правило F (Требуется верификация):** Состояние `CHANGED` или выставлен флаг верификации -> `VERIFY` / `RUN_VERIFICATION`.
  - **Правило G (Требуется выполнение):** Состояние `INITIAL` или требуется выполнение -> `CONTINUE` / `EXECUTE`.
  - **Правило H (Ожидание по умолчанию):** Во всех остальных случаях -> `WAIT` / `WAIT_FOR_APPROVAL`.
- **Интеграция с Engineering Run и трассировкой:**
  - `EngineeringRunExecutor` принимает опциональный `DecisionProvider` (по умолчанию `DeterministicDecisionProvider`).
  - При каждом переходе состояния проекта формируется и оценивается `DecisionRequest`.
  - В трассу записываются безопасные события: `DECISION_REQUESTED` и `DECISION_MADE` (или `DECISION_REJECTED` при ошибке валидации), обогащённые `decision_id` в `RunTrace`.
  - `EngineeringRunResult.decisions` предоставляет все принятые валидные решения, а `EngineeringRunResult.final_decision` возвращает финальное решение.
- **Чем Слой решений НЕ является:**
  - НЕ является системой рассуждений автономных агентов или LLM-циклом.
  - НЕ является провайдер-специфичным маршрутизатором.
  - НЕ является заменой границ прав доступа (Permission), подтверждений (Approval) или политик выполнения.
  - НЕ является механизмом выполнения команд или изменения файлов.

## Decision Context Envelope v0.1

Decision Context Envelope v0.1 introduces a bounded, immutable, provenance-aware context container (`DecisionContextEnvelope`, `ContextItem`, `TraceSummary`, `validate_decision_context`, `DecisionContextAssembler`) that assembles the minimal structured context required for the Decision Layer to decide the next action in an Engineering Run:

```text
Structured Run State (ProjectState, TaskSpecification, AcceptanceResult, VerificationResult, RevisionResult, TraceSummary, Blocking Conditions)
                                              ↓
                                   DecisionContextAssembler
                                              ↓
                                   DecisionContextEnvelope
                                              ↓
                                  validate_decision_context
                                              ↓
                                  CONTEXT_DECISION_READY (Event)
                                              ↓
                                   DecisionRequest (Context References)
                                              ↓
                                   DeterministicDecisionProvider
```

- **Purpose and Key Invariant:** Context assembly is strictly an aggregation and bounding mechanism. It does NOT authorize actions, does NOT execute processes, does NOT access external networks, does NOT read the filesystem, and does NOT bypass existing permission, approval, or execution boundaries.
- **Contract Models (`app/context/models.py`):**
  - `ContextSourceType`: Categorizes item provenance (`USER_TASK`, `EXPLICIT_INPUT`, `TASK_CONTEXT`, `PROJECT_STATE`, `REQUIREMENT`, `ACCEPTANCE`, `VERIFICATION`, `REVISION`, `RUN_TRACE`, `SYSTEM_POLICY`).
  - `ContextTrustLevel`: Classifies evidence trustworthiness (`UNTRUSTED`, `CONFIRMED`, `VERIFIED`, `TRUSTED`).
  - `ContextFreshness`: Tracks temporal relevance (`CURRENT`, `HISTORICAL`, `STALE`, `UNKNOWN`).
  - `ContextSensitivity`: Categorizes data sensitivity (`PUBLIC`, `INTERNAL`, `CONFIDENTIAL`, `RESTRICTED`).
  - `ContextItem`: Immutable, provenance-aware context unit with `item_id`, `item_type`, `source_type`, `value`, `trust_level`, `freshness`, `sensitivity`, `source_id`, and `metadata`. Provides backwards-compatible property aliases (`id`, `kind`, `content`, `source`, `trust`) for legacy callers.
  - `TraceSummary`: Safe, bounded summary of `RunTrace` events (event count, latest event types, latest sequence number, latest decision/verification/acceptance references) without duplicating raw event payloads.
  - `DecisionContextEnvelope`: Bounded, immutable envelope comprising `context_id`, `run_id`, `attempt_number`, `task_id`, `project_state_status`, structured summaries of requirements, acceptance, verification, and revision, sorted `blocking_conditions`, `available_actions`, `trace_summary`, `context_items`, and sanitized `metadata`. Computes a deterministic SHA-256 `context_fingerprint` over structured fields.
- **Budget and Validation (`app/context/validation.py`):**
  - Enforces strict upper bounds: `MAX_CONTEXT_ITEMS = 64`, `MAX_METADATA_ITEMS = 32`, `MAX_STRING_LENGTH = 4096`.
  - Validates run binding, rejects negative attempt numbers, empty `run_id`, duplicate item IDs, oversized strings, and cross-run references.
  - Strictly rejects or sanitizes forbidden sensitive keys (`stdout`, `stderr`, `raw_output`, `prompt`, `chain_of_thought`, `secret`, `token`, `password`, `api_key`, `credential`, `source_code`, `file_content`).
- **Deterministic Assembly (`app/context/assembler.py`):**
  - `DecisionContextAssembler`: Pure in-memory assembler that projects structured run artifacts into bounded context items and summaries without any network or filesystem I/O.
  - Enforces budgets and runs validation before returning the envelope; raises `ContextBudgetExceededError` or `ContextAssemblyError` on boundary violations.
- **Engineering Run & Trace Integration:**
  - `EngineeringRunExecutor` coordinates `DecisionContextAssembler` at each `ProjectState` transition.
  - Emits the safe `CONTEXT_DECISION_READY` trace event containing `context_id`, `context_fingerprint`, `run_id`, `attempt_number`, and `item_count`.
  - Populates `context_id`, `context_fingerprint`, and `context_envelope` in `DecisionRequest`.
  - `DeterministicDecisionProvider` propagates `context_id` and `context_fingerprint` into `Decision.references`.
  - `EngineeringRunResult.context_envelopes` and `.final_context_envelope` expose assembled envelopes.
- **What Decision Context Envelope is NOT:**
  - NOT an LLM prompt builder or generative agent memory.
  - NOT a vector database, embedding store, or retrieval engine.
  - NOT an event bus, message broker, or distributed storage system.
  - NOT an execution authority or policy bypass.

## Decision Context Envelope v0.1 — русская версия

Контекстный конверт решений v0.1 (Decision Context Envelope v0.1) вводит ограниченный, неизменяемый и учитывающий происхождение данных контейнер контекста (`DecisionContextEnvelope`, `ContextItem`, `TraceSummary`, `validate_decision_context`, `DecisionContextAssembler`), собирающий минимальный структурированный контекст, необходимый Слою принятия решений (Decision Layer) для определения следующего действия в инженерном запуске (Engineering Run):

```text
Структурированное состояние запуска (ProjectState, TaskSpecification, AcceptanceResult, VerificationResult, RevisionResult, TraceSummary, Блокирующие условия)
                                              ↓
                                   DecisionContextAssembler
                                              ↓
                                   DecisionContextEnvelope
                                              ↓
                                  validate_decision_context
                                              ↓
                                  CONTEXT_DECISION_READY (Событие трассы)
                                              ↓
                                   DecisionRequest (Ссылки на контекст)
                                              ↓
                                   DeterministicDecisionProvider
```

- **Назначение и ключевой инвариант:** Сборка контекста — это строго механизм агрегации и ограничения данных. Она НЕ авторизует действия, НЕ запускает процессы, НЕ обращается к внешним сетям, НЕ читает файловую систему и НЕ обходит существующие границы разрешений (Permission), подтверждений (Approval) или выполнения (Execution).
- **Модели контракта (`app/context/models.py`):**
  - `ContextSourceType`: Источник происхождения элемента (`USER_TASK`, `EXPLICIT_INPUT`, `TASK_CONTEXT`, `PROJECT_STATE`, `REQUIREMENT`, `ACCEPTANCE`, `VERIFICATION`, `REVISION`, `RUN_TRACE`, `SYSTEM_POLICY`).
  - `ContextTrustLevel`: Уровень доверия к источнику (`UNTRUSTED`, `CONFIRMED`, `VERIFIED`, `TRUSTED`).
  - `ContextFreshness`: Актуальность данных во времени (`CURRENT`, `HISTORICAL`, `STALE`, `UNKNOWN`).
  - `ContextSensitivity`: Категория конфиденциальности (`PUBLIC`, `INTERNAL`, `CONFIDENTIAL`, `RESTRICTED`).
  - `ContextItem`: Неизменяемая единица контекста с отслеживаемым происхождением, включающая `item_id`, `item_type`, `source_type`, `value`, `trust_level`, `freshness`, `sensitivity`, `source_id` и `metadata`. Предоставляет свойства обратной совместимости (`id`, `kind`, `content`, `source`, `trust`) для прежних потребителей.
  - `TraceSummary`: Безопасная, ограниченная сводка событий `RunTrace` (количество событий, типы последних событий, последний порядковый номер, последние ссылки на решения, верификацию и приёмку) без копирования сырых данных событий.
  - `DecisionContextEnvelope`: Ограниченный неизменяемый конверт, содержащий `context_id`, `run_id`, `attempt_number`, `task_id`, `project_state_status`, структурированные сводки требований, приёмки, верификации и ревизий, отсортированные `blocking_conditions`, `available_actions`, `trace_summary`, `context_items` и очищенный `metadata`. Вычисляет детерминированный SHA-256 хеш `context_fingerprint` по структурированным полям.
- **Бюджеты и валидация (`app/context/validation.py`):**
  - Задаёт строгие лимиты объема: `MAX_CONTEXT_ITEMS = 64`, `MAX_METADATA_ITEMS = 32`, `MAX_STRING_LENGTH = 4096`.
  - Проверяет привязку к запуску, отклоняет отрицательные номера попыток, пустой `run_id`, дублирующиеся ID элементов, превышение длины строк и перекрёстные ссылки между запусками.
  - Категорически отклоняет или очищает запрещённые конфиденциальные ключи (`stdout`, `stderr`, `raw_output`, `prompt`, `chain_of_thought`, `secret`, `token`, `password`, `api_key`, `credential`, `source_code`, `file_content`).
- **Детерминированная сборка (`app/context/assembler.py`):**
  - `DecisionContextAssembler`: Чистый сборщик в памяти, проецирующий структурированные артефакты запуска в ограниченные элементы контекста и сводки без сетевого ввода-вывода или обращений к файловой системе.
  - Контролирует бюджеты и проводит валидацию перед возвратом конверта; при нарушении границ генерирует `ContextBudgetExceededError` или `ContextAssemblyError`.
- **Интеграция с Engineering Run и трассировкой:**
  - `EngineeringRunExecutor` вызывает `DecisionContextAssembler` при каждом переходе `ProjectState`.
  - Генерирует безопасное событие трассы `CONTEXT_DECISION_READY`, содержащее `context_id`, `context_fingerprint`, `run_id`, `attempt_number` и `item_count`.
  - Передаёт `context_id`, `context_fingerprint` и `context_envelope` в `DecisionRequest`.
  - `DeterministicDecisionProvider` включает `context_id` и `context_fingerprint` в `Decision.references`.
- **Чем Контекстный конверт решений НЕ является:**
  - НЕ является сборщиком промптов для LLM или памятью генеративного агента.
  - НЕ является векторной базой данных, хранилищем эмбеддингов или поисковой системой.
  - НЕ является шиной событий, брокером сообщений или распределённым хранилищем.
  - НЕ является органом авторизации или обходом политик безопасности.

## Execution Authorization Contract v0.2

> **Status: CURRENT / VERIFIED** at commit `ad6b82b` (`feat: reset execution
> authorization contract v0.2`). Implemented in `app/execution/intent.py`,
> `app/execution/capabilities.py`, `app/execution/paths.py`,
> `app/execution/authorizer.py`, `app/execution/policy.py`,
> `app/execution/adapter.py`, `app/tools/approval.py`. Covered by
> `tests/test_execution_authorization_hardening_v02.py`,
> `tests/test_execution_authorization_hardening.py`,
> `tests/test_execution_plane.py`, `tests/test_approval_boundary.py`.
> Verified limitations are recorded in [`SOURCE_OF_TRUTH.md`](SOURCE_OF_TRUTH.md) §4.

### Pipeline

```text
ExecutionRequest (control-plane proposal: command, cwd, env, timeout, artifacts, profile)
        |
        v
1. Permission boundary          allowed_commands, default-deny, exact canonical identity
        |
        v
2. ExecutionIntent construction IntentBuilder.from_request(request, profile).build()
        |                       canonical normalization, immutable value object
        v
3. Approval boundary            ApprovalPolicy -> ApprovalResolver (untrusted proposer)
        |                       coordinator re-checks the returned intent fingerprint
        v
4. ExecutionPolicy              pure evaluation of the intent against the profile
        |
        v
5. workspace_root validation    mandatory, absolute, existing directory, fail-closed
        |
        v
6. AuthorizedExecution          internal authority marker, created only by the coordinator
        |
        v
7. LocalExecutionAdapter        validates the marker, then subprocess.Popen(shell=False)
```

Every denial path returns `ExecutionStatus.DENIED` with a typed
`ExecutionOutcomeStatus` and emits `execution_denied`. No process is created on
any denial path.

### ExecutionIntent (the authorization subject)

`ExecutionIntent` is a frozen value object and is the subject that Permission,
Approval, Policy and the adapter all evaluate. `IntentBody` is an alias of it.

Fields: `executable`, `argv`, `working_directory`, `environment_variables`
(canonical sorted tuple of pairs), `timeout_seconds`, `max_output_bytes`,
`artifact_targets` (canonical sorted tuple), `profile_id`, `network_access`,
`capabilities` (`frozenset[ExecutionCapability]`).

- `IntentBuilder` is the only constructor path. `with_command` canonicalizes the
  executable through `canonical_executable`; `with_working_directory` and
  `with_artifact_targets` normalize through `normalize_workspace_relative_path`;
  `build()` sorts and freezes the collections and classifies capabilities.
- `ExecutionIntent.fingerprint` is a deterministic SHA-256 over a canonical JSON
  projection of **all** listed fields, serialized with sorted keys and stable
  separators. The fingerprint is the authorization identity used by Approval.
- There is no deserialization path from untrusted input into `ExecutionIntent`,
  and no `dataclasses.replace` is applied on the authorize -> execute path.

### Canonical path security

`app/execution/paths.py` provides two domains:

- `canonical_executable` (program identity): bare name preserved
  (case-normalized), absolute path preserved in normalized form, and any
  relative or dot-prefixed form rejected with `PathSecurityError`.
- `normalize_workspace_relative_path` (workspace-relative path): rejects NUL,
  trailing separators, UNC and Windows device paths (`\\?\`, `\\.\`), Windows
  drive-absolute and drive-relative forms (`C:\...`, `C:/...`, `C:foo`), POSIX
  absolute paths, rooted Windows paths, traversal components (`..`), ADS/colon
  syntax, trailing dot/space components, and reserved device names; returns a
  canonical forward-slash relative path.

This module is the single canonical implementation for the execution plane.
`ExecutionPolicy`, `ExecutionRequest.validate`, `ProjectExecutionProfile.validate`
and `IntentBuilder` all delegate to it. `app/tools/workspace.py` keeps its own
independent validator for the **tool** boundary; the two implementations are not
yet unified, and the physical containment check
(`Path.relative_to` after `resolve()`) remains a second, non-redundant layer in
the adapter and in `EphemeralWorkspaceManager`.

### Permission, approval and policy boundaries

- **Permission**: `allowed_commands is None` or an empty command denies with
  `PERMISSION_DENIED` / `permission_missing`. Otherwise the command must match the
  allowed set by exact canonical executable identity.
- **Approval**: required when `ExecutionRequest.approval_required` is true, or when
  `ApprovalPolicy` requires it for `"execute"`, for the `CommandIdentity`, or for
  the canonical executable. The coordinator builds
  `ApprovalRequest(intent_fingerprint=intent.fingerprint)`, calls the resolver, and
  accepts `ApprovalState` or `ApprovalResolution`. `None` or any non-approved
  decision yields `APPROVAL_WAITING`; `REJECTED` yields `APPROVAL_REJECTED`. If the
  resolver returns a non-empty `approved_fingerprint` that differs from the current
  intent fingerprint, execution is denied with `intent_fingerprint_mismatch`.
  `InMemoryApprovalResolver` is single-use and fingerprint-bound; the coordinator's
  verification only runs when the resolver supplies a fingerprint (see limitations).
- **Policy**: `ExecutionPolicy.evaluate(intent, profile)` first matches
  `CommandIdentity` entries (exact executable + exact argv, no capability check),
  then bare-string entries (exact executable; every capability required by the
  intent must be declared in `profile.capabilities`, otherwise
  `argv_not_authorized`). It then re-validates paths defensively, rejects the
  forbidden git flags `--git-dir` / `--work-tree`, and bounds timeout, output size
  and environment variable syntax against the profile.

### Capability model (current implementation)

`ExecutionCapability` has four members: `EXEC_CHILD`, `INTERPRET_TEXT`,
`INTERPRET_MODULE`, `NETWORK`. `classify_invocation(executable, argv)` derives the
required set from hard-coded wrapper/network name lists, a `find -exec` rule, git
network subcommands, and interpreter flag regexes for the python, shell,
PowerShell, cmd, node, ruby, perl and php families.

**This is a recognition list, not a closed capability model.** Programs outside
the lists classify to an empty capability set, and a bare-string `allowed_commands`
entry then admits free-form argv. The intended direction (declared invocations
with a fail-closed default for unrecognized behavior) is recorded as a candidate
in [`SOURCE_OF_TRUTH.md`](SOURCE_OF_TRUTH.md) §4.2 and in
[`DECISIONS.md`](DECISIONS.md).

### Backend authority boundary

`AuthorizedExecution` is the internal authority marker. It carries the intent, the
resolved `workspace_root`, `run_id`, metadata, a private coordinator sentinel, and
the intent fingerprint captured at creation time. `is_valid()` verifies the
sentinel, the types, the absolute workspace root, and that the stored fingerprint
still equals the current intent fingerprint — so swapping the intent after creation
is detected.

`LocalExecutionAdapter.execute()` requires an `AuthorizedExecution` and raises
`ExecutionAuthorizationError` for anything else, before touching the filesystem or
spawning a process. The adapter no longer evaluates policy itself; policy is the
coordinator's boundary. The marker type is a fail-closed integrity check, not a
cryptographic capability: any same-process code can construct data structures, so
the design target is prevention of accidental and refactoring-introduced bypass,
not resistance to an in-process adversary.

### Workspace boundary

`ExecutionCoordinator` requires an explicit `workspace_root` for local process
spawning: a missing root is denied with `workspace_root_required`, and a relative,
non-existent or non-directory root is denied with `workspace_root_invalid`. If an
adapter instance was constructed with its own `_workspace_root`, that value is
used when the caller passes none; a non-local backend without an explicit root
falls back to the system temporary directory.

`LocalExecutionAdapter` stages a COPY of the workspace root into an ephemeral
scratch directory per execution (`EphemeralWorkspaceManager`), rejects symlinks as
staging mechanisms, skips `.git` and `__pycache__`, resolves the working directory
and re-checks containment with `relative_to`, harvests declared artifacts before
cleanup, and deletes the scratch directory afterwards.

**Not implemented (Block 2):** declared-input allow-lists, deny-by-default
staging, workspace identity/content binding, secret-file exclusion policy, and any
OS-level sandbox (namespaces, cgroups, Job Objects, kernel filesystem or network
confinement). `network_access=False` is enforced only by proxy environment
variables and is best-effort. Forge is **not** an OS-level sandbox.

### Events

`execution_requested`, `execution_policy_checked`, `execution_started`,
`execution_completed` and `execution_denied` carry only identifiers, statuses,
outcome status, denial reason, exit code, duration, truncation flag and artifact
count. Standard output, standard error, environment values and secrets are never
placed in event metadata.

## Execution Authorization Contract v0.2 — русская версия

> **Статус: ТЕКУЩАЯ РЕАЛИЗАЦИЯ / ПРОВЕРЕНО** на коммите `ad6b82b`
> (`feat: reset execution authorization contract v0.2`).

### Конвейер авторизации

```text
ExecutionRequest (предложение control plane: команда, cwd, env, timeout, артефакты, профиль)
        |
        v
1. Граница Permission           allowed_commands, default-deny, точный канонический идентификатор
        |
        v
2. Построение ExecutionIntent   IntentBuilder.from_request(request, profile).build()
        |                       каноническая нормализация, неизменяемый объект-значение
        v
3. Граница Approval             ApprovalPolicy -> ApprovalResolver (недоверенный источник решения)
        |                       координатор повторно сверяет fingerprint интента
        v
4. ExecutionPolicy              чистая проверка интента относительно профиля
        |
        v
5. Проверка workspace_root      обязательный, абсолютный, существующий каталог, fail-closed
        |
        v
6. AuthorizedExecution          внутренний маркер полномочия, создаётся только координатором
        |
        v
7. LocalExecutionAdapter        проверяет маркер, затем subprocess.Popen(shell=False)
```

Любой отказ возвращает `ExecutionStatus.DENIED` с типизированным
`ExecutionOutcomeStatus` и событием `execution_denied`. Ни на одном пути отказа
процесс не создаётся.

### ExecutionIntent — субъект авторизации

`ExecutionIntent` — неизменяемый объект-значение и единственный субъект, который
оценивают Permission, Approval, Policy и адаптер. `IntentBody` — псевдоним этого
типа.

Поля: `executable`, `argv`, `working_directory`, `environment_variables`
(канонический отсортированный кортеж пар), `timeout_seconds`,
`max_output_bytes`, `artifact_targets` (канонический отсортированный кортеж),
`profile_id`, `network_access`, `capabilities`
(`frozenset[ExecutionCapability]`).

- `IntentBuilder` — единственный путь конструирования. `with_command`
  канонизирует исполняемый файл через `canonical_executable`;
  `with_working_directory` и `with_artifact_targets` нормализуют через
  `normalize_workspace_relative_path`; `build()` сортирует и замораживает
  коллекции и классифицирует capabilities.
- `ExecutionIntent.fingerprint` — детерминированный SHA-256 по канонической
  JSON-проекции **всех** перечисленных полей, с сортировкой ключей и стабильными
  разделителями. Fingerprint является идентификатором авторизации для Approval.
- Пути десериализации недоверенного ввода в `ExecutionIntent` не существует, и
  `dataclasses.replace` на пути authorize -> execute не применяется.

### Каноническая безопасность путей

`app/execution/paths.py` реализует два домена:

- `canonical_executable` (идентичность программы): одиночное имя сохраняется
  (с нормализацией регистра), абсолютный путь сохраняется в нормализованном виде,
  относительные и начинающиеся с точки формы отклоняются с `PathSecurityError`.
- `normalize_workspace_relative_path` (путь относительно workspace): отклоняет
  NUL, завершающие разделители, UNC и device-пути Windows (`\\?\`, `\\.\`),
  Windows drive-absolute и drive-relative формы (`C:\...`, `C:/...`, `C:foo`),
  POSIX-абсолютные пути, rooted-пути Windows, компоненты обхода (`..`), синтаксис
  ADS/двоеточия, компоненты с точкой или пробелом в конце и зарезервированные
  имена устройств; возвращает канонический относительный путь с прямыми слэшами.

Этот модуль — единственная каноническая реализация для execution plane.
`ExecutionPolicy`, `ExecutionRequest.validate`, `ProjectExecutionProfile.validate`
и `IntentBuilder` делегируют в него. `app/tools/workspace.py` сохраняет
собственный независимый валидатор для **tool**-границы; две реализации пока не
объединены, а проверка физического нахождения в границах (`Path.relative_to`
после `resolve()`) остаётся вторым, не избыточным слоем в адаптере и в
`EphemeralWorkspaceManager`.

### Границы Permission, Approval и Policy

- **Permission**: `allowed_commands is None` или пустая команда отклоняются с
  `PERMISSION_DENIED` / `permission_missing`. Иначе команда должна совпасть с
  разрешённым набором по точному каноническому идентификатору исполняемого файла.
- **Approval**: требуется, если `ExecutionRequest.approval_required` истинно либо
  `ApprovalPolicy` требует одобрения для `"execute"`, для `CommandIdentity` или для
  канонического исполняемого файла. Координатор формирует
  `ApprovalRequest(intent_fingerprint=intent.fingerprint)`, вызывает resolver и
  принимает `ApprovalState` либо `ApprovalResolution`. `None` или любое
  неодобренное решение даёт `APPROVAL_WAITING`; `REJECTED` даёт
  `APPROVAL_REJECTED`. Если resolver вернул непустой `approved_fingerprint`,
  отличный от текущего fingerprint интента, выполнение отклоняется с
  `intent_fingerprint_mismatch`. `InMemoryApprovalResolver` одноразовый и привязан
  к fingerprint; проверка координатора выполняется только тогда, когда resolver
  передал fingerprint (см. ограничения).
- **Policy**: `ExecutionPolicy.evaluate(intent, profile)` сначала сопоставляет
  записи `CommandIdentity` (точный исполняемый файл + точный argv, без проверки
  capabilities), затем строковые записи (точный исполняемый файл; каждая
  требуемая интентом capability должна быть объявлена в `profile.capabilities`,
  иначе `argv_not_authorized`). Далее политика защитно перепроверяет пути,
  отклоняет запрещённые флаги git `--git-dir` / `--work-tree` и ограничивает
  таймаут, размер вывода и синтаксис имён переменных окружения по профилю.

### Модель capabilities (текущая реализация)

`ExecutionCapability` содержит четыре члена: `EXEC_CHILD`, `INTERPRET_TEXT`,
`INTERPRET_MODULE`, `NETWORK`. `classify_invocation(executable, argv)` выводит
требуемый набор из жёстко заданных списков имён wrapper/network-программ, правила
`find -exec`, сетевых подкоманд git и регулярных выражений флагов интерпретаторов
для семейств python, shell, PowerShell, cmd, node, ruby, perl и php.

**Это список распознавания, а не замкнутая модель capability.** Программы вне
списков классифицируются в пустой набор capabilities, и строковая запись
`allowed_commands` после этого допускает произвольный argv. Намеренное
направление (объявленные инвокации с fail-closed поведением по умолчанию для
нераспознанного) зафиксировано как кандидат в
[`SOURCE_OF_TRUTH.md`](SOURCE_OF_TRUTH.md) §4.2 и в [`DECISIONS.md`](DECISIONS.md).

### Граница полномочий backend

`AuthorizedExecution` — внутренний маркер полномочия. Он несёт интент,
разрешённый `workspace_root`, `run_id`, метаданные, приватный sentinel
координатора и fingerprint интента, зафиксированный при создании. `is_valid()`
проверяет sentinel, типы, абсолютность workspace root и то, что сохранённый
fingerprint всё ещё равен текущему fingerprint интента, — поэтому подмена интента
после создания обнаруживается.

`LocalExecutionAdapter.execute()` требует `AuthorizedExecution` и выбрасывает
`ExecutionAuthorizationError` для всего остального, до обращения к файловой
системе и до запуска процесса. Адаптер больше не оценивает политику сам; политика
— граница координатора. Маркер является fail-closed проверкой целостности, а не
криптографическим полномочием: любой код в том же процессе может создавать
структуры данных, поэтому цель дизайна — предотвращение случайного и внесённого
рефакторингом обхода, а не сопротивление злонамеренному коду внутри процесса.

### Граница workspace

`ExecutionCoordinator` требует явный `workspace_root` для локального запуска
процессов: отсутствующий root отклоняется с `workspace_root_required`, а
относительный, несуществующий или не являющийся каталогом — с
`workspace_root_invalid`. Если экземпляр адаптера создан со своим
`_workspace_root`, это значение используется, когда вызывающий не передал ничего;
нелокальный backend без явного root использует системный временный каталог.

`LocalExecutionAdapter` копирует workspace root в временный scratch-каталог на
каждое выполнение (`EphemeralWorkspaceManager`), отклоняет симлинки как механизм
подготовки, пропускает `.git` и `__pycache__`, разрешает рабочую директорию и
повторно проверяет нахождение в границах через `relative_to`, собирает
объявленные артефакты до очистки и удаляет scratch-каталог после.

**Не реализовано (Block 2):** списки объявленных входов, deny-by-default
подготовка, привязка идентичности/содержимого workspace, политика исключения
файлов с секретами и любая OS-песочница (namespaces, cgroups, Job Objects,
файловое или сетевое ограничение на уровне ядра). `network_access=False`
обеспечивается только переменными окружения прокси и является best-effort.
Forge **не** является OS-песочницей.

### События

`execution_requested`, `execution_policy_checked`, `execution_started`,
`execution_completed` и `execution_denied` несут только идентификаторы, статусы,
outcome status, причину отказа, код возврата, длительность, признак усечения и
количество артефактов. Стандартный вывод, поток ошибок, значения переменных
окружения и секреты никогда не попадают в метаданные событий.
