# Planned architecture

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
