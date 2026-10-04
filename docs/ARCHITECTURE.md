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

## Planner v0.1

`Planner` maps a plain-text goal to a provider-neutral `ProjectPlan` using
deterministic templates for Telegram bots, web applications, data/analysis,
and generic software. Plans contain ordered `PlannedTask` items with stable
IDs, existing `TaskCategory` values, explicit dependencies, and `READY` or
`PENDING` initial status. The first task is ready; later tasks depend on the
previous task in the selected template. Plan IDs are stable for the same
normalized goal. This component only creates plans: it does not call a model,
dispatch tasks, track execution, or manage dependencies at runtime.

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

## Local secrets

`SecretStore` lazily resolves a provider's environment-variable reference from
the process environment first, then the repository-root `.env` file. It does
not load secrets into `RuntimeSettings`, `ProviderConfig`, or process-wide
environment state. Providers receive the shared store through `ProviderFactory`
and request their own key only when generating. `.env` is ignored by Git;
`.env.example` contains blank key fields and remains tracked as documentation.
The same lookup flow supports OpenAI, Anthropic, Gemini, xAI, DeepSeek, and
OpenRouter adapters.

## Anthropic Provider v0.1

`AnthropicProvider` uses the official Python SDK Messages API when a task is
explicitly dispatched to it. It resolves `ANTHROPIC_API_KEY` through the shared
`SecretStore` at generation time, sends the prompt and serialized context as a
user message, and maps text blocks and token usage into `ProviderResponse`.
Startup and offline tests do not make requests.

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

## Gemini provider

`GoogleProvider` implements Gemini through Google's official `google-genai`
SDK. It resolves `GEMINI_API_KEY` lazily through `SecretStore`, uses the model
from `FORGE_GEMINI_MODEL` (default `gemini-3.8-flash`), and maps text and usage
to the shared `ProviderResponse`. Startup and automated tests do not make API
requests. The explicit `python -m app.smoke_gemini` command performs one real
request.

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

## Граница подтверждения v0.1

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

## Tool Contract v0.1 — только чтение

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
or credentials. Runs and events currently exist only in process memory. There
is no durable event store, checkpoint/resume, distributed tracing, or telemetry
service.

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

## Граница Workspace v0.1 — контролируемая запись

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

## Граница ChangeSet / Artifact v0.1

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

## Граница Project Snapshot v0.1

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
- `SecretRedactor` / `DefaultSecretRedactor`: ensures execution metadata, command arguments, environment variables, and output streams are safely redacted before storage or event emission. Known secret tokens, high-entropy API key patterns (`sk-...`, `ghp_...`, Bearer tokens), and sensitive key patterns (`token`, `secret`, `password`, `key`, `auth`, `credential`) are replaced with `[REDACTED]`.
- Integration: `TaskSpecification` and `EngineeringRunRequest` support attaching a `ProjectExecutionProfile`. The profile is validated, serialized into task context, exposed on `EngineeringRunResult`, and emitted in safe run start event metadata without leaking secret or environment payloads.

## Execution Plane Contracts and Project Execution Profile v0.1 — русская версия

Контракт Execution Plane вводит типизированные доменные модели, определяющие где, что и как должно выполняться, без запуска реальных процессов shell, контейнеров или сетевых вызовов:

- `ProjectExecutionProfile`: декларирует целевое окружение выполнения (`ExecutionEnvironmentType`: `HOST`, `VIRTUALENV`, `CONTAINER`, `CUSTOM`), имя и версию рантайма, целевую ОС (`TargetOS`), относительную рабочую директорию, таймаут, лимит вывода в байтах, флаг доступа к сети и опциональный список разрешённых команд `allowed_commands`. Детерминированная валидация проверяет безопасность путей, положительные лимиты и корректность имён переменных окружения.
- `ExecutionRequest`: специфицирует конкретное действие выполнения через типизированный кортеж аргументов команды (`command` argv), рабочую директорию, переменные окружения уровня запроса и ограничения профиля. При проверке относительно профиля запросы с неразрешёнными командами отклоняются до запуска.
- `ExecutionResult`: фиксирует результат выполнения, включая типизированный `ExecutionStatus` (`SUCCESS`, `FAILURE`, `TIMEOUT`, `DENIED`, `ERROR`), целочисленный код возврата, стандартный вывод и поток ошибок, длительность и признак усечения вывода.
- `SecretRedactor` / `DefaultSecretRedactor`: обеспечивает безопасную маскировку секретов в метаданных выполнения, аргументах команд, переменных окружения и потоках вывода перед сохранением или отправкой событий. Зарегистрированные секреты, шаблоны API-ключей (`sk-...`, `ghp_...`, Bearer) и чувствительные имена ключей (`token`, `secret`, `password`, `key`, `auth`, `credential`) заменяются на `[REDACTED]`.
- Интеграция: `TaskSpecification` и `EngineeringRunRequest` поддерживают привязку `ProjectExecutionProfile`. Профиль валидируется, сериализуется в контекст задачи, предоставляется в `EngineeringRunResult` и передаётся в безопасных метаданных события запуска без утечки секретов и переменных окружения.

## Execution Boundary and Local Execution Adapter v0.1

The Execution Boundary defines the deterministic enforcement layer that bridges authorized `ExecutionRequest`s and local process execution:

- `ExecutionPolicy`: evaluates an `ExecutionRequest` against its associated `ProjectExecutionProfile` under a strict default-deny model. It ensures the request exists, the command is non-empty, the profile is valid, the command is explicitly listed in `profile.allowed_commands` (supporting path basenames and case-insensitivity), the working directory is safe and relative, timeout does not exceed the profile's `timeout_seconds`, and environment variable names are valid. If policy evaluation fails, `ExecutionStatus.DENIED` is returned and no subprocess is created.
- `LocalExecutionAdapter`: executes policy-authorized requests as local operating system processes strictly without shell (`shell=False`). It resolves working directories within workspace boundaries, overlays profile and request environment variables, enforces timeouts (`ExecutionStatus.TIMEOUT`), captures standard output and error streams, maps exit codes (0 to `SUCCESS`, non-zero to `FAILURE`, unhandled exceptions to `ERROR`), bounds output byte sizes using `profile.max_output_bytes` (setting `truncated=True`), and redacts secrets and sensitive metadata via `SecretRedactor`.

## Execution Boundary and Local Execution Adapter v0.1 — русская версия

Граница выполнения (Execution Boundary) задаёт детерминированный уровень контроля, связывающий авторизованные запросы `ExecutionRequest` с локальным запуском процессов:

- `ExecutionPolicy`: оценивает `ExecutionRequest` относительно связанного профиля `ProjectExecutionProfile` по строгой модели default-deny. Политика проверяет наличие запроса, непустую команду, валидность профиля, явное присутствие команды в `profile.allowed_commands` (с поддержкой совпадения по basename и регистру), безопасность и относительность рабочей директории, непревышение таймаута профиля `timeout_seconds` и корректность имён переменных окружения. При непрохождении проверки возвращается статус `ExecutionStatus.DENIED` и процесс не запускается.
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
  2. Permission boundary: checks executable against `allowed_execution_commands` (default-deny, case-insensitive, basename-aware). If not allowed, returns `ExecutionStatus.DENIED` with `outcome_status=ExecutionOutcomeStatus.PERMISSION_DENIED` and emits `execution_denied`. No process is launched.
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
  2. Граница Permission: проверяет исполняемый файл по списку `allowed_execution_commands` (default-deny, с учётом регистра и имени файла). При отсутствии разрешения возвращает `ExecutionStatus.DENIED` с `outcome_status=ExecutionOutcomeStatus.PERMISSION_DENIED` и отправляет событие `execution_denied`. Процесс не запускается.
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
  - `EngineeringRunResult.context_envelopes` и `.final_context_envelope` открывают доступ к собранным контекстным конвертам.
- **Чем Контекстный конверт решений НЕ является:**
  - НЕ является сборщиком промптов для LLM или памятью генеративного агента.
  - НЕ является векторной базой данных, хранилищем эмбеддингов или поисковой системой.
  - НЕ является шиной событий, брокером сообщений или распределённым хранилищем.
  - НЕ является органом авторизации или обходом политик безопасности.
