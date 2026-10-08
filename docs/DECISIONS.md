# Architectural decisions

- Project name: Forge AI.
- Forge AI is intended to be a reusable AI development and orchestration
  platform.
- The architecture must be modular.
- AI providers must be isolated behind adapters.
- The orchestrator coordinates agents rather than containing
  provider-specific logic.
- Secrets must never be stored in Git.
- Git is the source of truth for project history.
- Significant changes must be traceable.
- Agents should receive only the context required for their task whenever
  possible.
- The system must actively optimize token and context usage.
- Agents should not repeatedly scan the entire project unnecessarily.
- Autonomous production changes must be controlled.
- New functionality should be developed in small, understandable modules.
- Tests should accompany important functionality.
- "В Orchestrator v0.1 выбор агента выполняется явно через agent_name. Автоматическая маршрутизация между AI-провайдерами откладывается до отдельного этапа."
- Provider-specific code is isolated from the orchestrator.
- Every provider implementation uses the common provider interface.
- OpenAI v0.1 uses the official Python SDK and Responses API; other real provider
  SDK integrations remain deferred.
- Secrets are not stored in code or Git; provider configuration may hold only an environment variable name reference.
- MockProvider is used for deterministic tests without network access.
- Runtime configuration is separate from provider-specific configuration.
- Secrets are never stored in Git.
- Application startup does not make external API calls.
- Runtime components are assembled through an explicit bootstrap function.
- The configured default provider is not an automatic task-routing mechanism.
- Execution flow is separated from provider-specific implementation.
- The Orchestrator does not execute provider-specific code.
- Providers return provider-neutral responses.
- Usage metadata is carried with the task result.
- MockProvider is used for deterministic end-to-end testing in v0.1.
- Automatic agent/provider routing is not implemented in v0.1.
- The OpenAI API key is read only from the local process environment at explicit
  generation time and is never stored in configuration or logged.
- OpenAI uses `client.responses.create`; startup and ordinary offline tests do
  not perform inference.
- Provider credentials are resolved lazily by a shared `SecretStore`, with
  process environment values taking precedence over the ignored repository-root
  `.env` file. Secret values never enter runtime/provider configuration.
- Anthropic v0.1 uses the official Python SDK Messages API; inference happens
  only when a task is explicitly dispatched to its provider.
- DeepSeek and OpenRouter share an OpenAI-compatible Chat Completions adapter
  and the existing OpenAI SDK; their endpoint, key name, and provider name are
  isolated in their small provider modules.
- OpenRouter model IDs are passed through `ProviderConfig`; `openrouter/free`
  is an explicit configurable model choice, not an automatic routing default.
- OpenRouter has a dedicated `FORGE_OPENROUTER_MODEL` runtime setting, defaulting
  to `cohere/north-mini-code:free`, independent of the general default model.
  `python -m app.smoke_openrouter` is an explicit opt-in real-request check;
  automated tests use fake clients and do not contact the provider.

## Dispatcher v0.2

- Task routing uses a small deterministic category policy: coding prefers OpenAI then Anthropic; reasoning prefers Anthropic then OpenAI; large-context selects Google/Gemini; cheap/free selects OpenRouter; fast/cheap selects DeepSeek; other tasks use the configured default.
- Callers may explicitly choose a provider. Dispatcher v0.2 executes only one selected provider and does not add scoring, automatic fallback execution, or multi-agent execution.
- Task parameters are provider-neutral; the optional `model` parameter overrides the configured model for the chosen provider.
- Groq uses the common OpenAI-compatible adapter and existing OpenAI SDK with Groq's official OpenAI-compatible endpoint; its key is resolved by `SecretStore` under `GROQ_API_KEY`.
- Provider account email metadata is handled by `ProviderAccountConfig`, independently of `SecretStore`. It is local-only metadata and is never copied into `ProviderResponse` or `TaskResult` or written to logs.


## Dispatcher v0.2 — русская версия

- Маршрутизация задач использует небольшую детерминированную политику категорий: coding предпочитает OpenAI, затем Anthropic; reasoning — Anthropic, затем OpenAI; large-context выбирает Google/Gemini; cheap/free выбирает OpenRouter; fast/cheap выбирает DeepSeek; остальные задачи используют настроенный по умолчанию провайдер.
- Вызывающие могут явно выбрать провайдера. Dispatcher v0.2 выполняет только одного выбранного провайдера и не добавляет оценивание, автоматическое выполнение fallback или многоагентное выполнение.
- Параметры задач провайдер-нейтральны; необязательный параметр `model` переопределяет настроенную модель для выбранного провайдера.
- Groq использует общий OpenAI-совместимый адаптер и существующий OpenAI SDK с официальным OpenAI-совместимым эндпоинтом Groq; его ключ разрешается `SecretStore` под `GROQ_API_KEY`.
- Метаданные email-адресов аккаунтов провайдеров обрабатываются `ProviderAccountConfig` независимо от `SecretStore`. Это локальные метаданные, и они никогда не копируются в `ProviderResponse` или `TaskResult` и не пишутся в логи.
## Provider Fallback v0.1

- Fallback is an optional ordered finite provider list configured by `FORGE_PROVIDER_FALLBACK_CHAIN`. The dispatcher tries the policy-selected primary first, then each distinct configured provider sequentially after a missing provider or failed execution.
- A successful attempt ends dispatch. Chain exhaustion returns a failed `TaskResult` with per-provider reasons. Explicit provider or agent selection is never silently changed.
- Fallback does not use cost/quality scoring, retries, parallel calls, or multi-agent execution.


## Provider Fallback v0.1 — русская версия

- Fallback — необязательный упорядоченный конечный список провайдеров, настраиваемый через `FORGE_PROVIDER_FALLBACK_CHAIN`. Dispatcher сначала пытается использовать выбранного политикой primary, затем последовательно каждого отдельного настроенного провайдера после отсутствующего провайдера или неуспешного выполнения.
- Успешная попытка завершает диспетчеризацию. Исчерпание цепочки возвращает неуспешный `TaskResult` с причинами по каждому провайдеру. Явный выбор провайдера или агента никогда не меняется молча.
- Fallback не использует оценивание по стоимости/качеству, повторы, параллельные вызовы или многоагентное выполнение.
## Provider Capabilities v0.1

- Provider capabilities are immutable declarative metadata kept outside the provider interface. The registry mirrors `ProviderConfig.enabled` and API-key variable names without resolving secrets.
- Streaming/tool booleans describe general provider API support and may vary by model; they do not imply that Forge AI's current adapter implements streaming or tool execution.
- Cost metadata is represented only by a coarse `free`, `cheap`, or `paid` label; it does not calculate API cost.


## Provider Capabilities v0.1 — русская версия

- Capabilities провайдеров — неизменяемые декларативные метаданные, хранимые вне интерфейса провайдера. Реестр повторяет `ProviderConfig.enabled` и имена переменных для API-ключей без разрешения секретов.
- Булевы значения streaming/инструментов описывают общую поддержку API провайдера и могут зависеть от модели; они не подразумевают, что текущий адаптер Forge AI реализует streaming или выполнение инструментов.
- Метаданные стоимости представлены только грубой меткой `free`, `cheap` или `paid`; они не рассчитывают стоимость API.
## Cost-aware Provider Routing v0.1

- Ordinary tasks use existing `ProviderCapabilitiesRegistry` cost tiers in `free`, `cheap`, `paid` order, limited to providers registered with both provider and agent registries.
- Paid providers are excluded by default and may be permitted with `FORGE_ALLOW_PAID_PROVIDERS=true`. This policy does not calculate token prices or apply cost/quality scoring.
- Explicit `provider_name` selection overrides cost ordering. The existing bounded fallback chain runs after cost-ordered candidates and remains sequential.


## Cost-aware Provider Routing v0.1 — русская версия

- Обычные задачи используют существующие cost-tiers `ProviderCapabilitiesRegistry` в порядке `free`, `cheap`, `paid`, ограничиваясь провайдерами, зарегистрированными и в реестре провайдеров, и в реестре агентов.
- Платные провайдеры исключены по умолчанию и могут быть разрешены через `FORGE_ALLOW_PAID_PROVIDERS=true`. Эта политика не рассчитывает цены токенов и не применяет оценивание по стоимости/качеству.
- Явный выбор `provider_name` переопределяет порядок по стоимости. Существующая ограниченная цепочка fallback выполняется после упорядоченных по стоимости кандидатов и остаётся последовательной.
## Deterministic provider routing

- Automatic routing checks provider registration, the configured enabled set, required API-key presence, task capability requirements, and cost policy before executing a candidate. Candidate preference and fallback order are deterministic and each provider is attempted at most once.
- `FORGE_ENABLED_PROVIDERS` defaults to `mock,openrouter`. An OpenRouter model ending in `:free` (or `openrouter/free`) is declared free for routing; other OpenRouter models remain cheap. Paid providers, including Gemini, require `FORGE_ALLOW_PAID_PROVIDERS=true` for automatic routing.
- Gemini uses Google's official `google-genai` SDK, `GEMINI_API_KEY` through `SecretStore`, and `FORGE_GEMINI_MODEL`. Its default model is `gemini-3.8-flash`. The `python -m app.smoke_gemini` command is opt-in and performs a real request; unit tests use fake clients.


## Deterministic provider routing — русская версия

- Автоматическая маршрутизация проверяет регистрацию провайдера, настроенный набор включённых, наличие требуемого API-ключа, требования capabilities задачи и политику стоимости до выполнения кандидата. Предпочтение кандидатов и порядок fallback детерминированы, и каждый провайдер проверяется не более одного раза.
- `FORGE_ENABLED_PROVIDERS` по умолчанию равно `mock,openrouter`. Модель OpenRouter, оканчивающаяся на `:free` (или `openrouter/free`), объявляется бесплатной для маршрутизации; остальные модели OpenRouter остаются cheap. Платные провайдеры, включая Gemini, требуют `FORGE_ALLOW_PAID_PROVIDERS=true` для автоматической маршрутизации.
- Gemini использует официальный SDK Google `google-genai`, `GEMINI_API_KEY` через `SecretStore` и `FORGE_GEMINI_MODEL`. Его модель по умолчанию — `gemini-3.8-flash`. Команда `python -m app.smoke_gemini` является opt-in и выполняет реальный запрос; юнит-тесты используют fake-клиенты.
## Revision Loop v0.1

- Multi-agent execution permits at most one primary revision after the reviewer returns `CHANGES_REQUESTED`, followed by one final review.
- The revision receives the original task, initial primary response, and review feedback. The task category and any explicit primary provider selection are retained.
- A further `CHANGES_REQUESTED` ends the workflow without another revision. Revision and reviewer failures preserve the available primary result and never imply approval.


## Revision Loop v0.1 — русская версия

- Многоагентное выполнение допускает не более одной ревизии primary после того, как reviewer вернул `CHANGES_REQUESTED`, после чего следует одно финальное ревью.
- Ревизия получает исходную задачу, первоначальный ответ primary и обратную связь ревью. Категория задачи и явный выбор провайдера для primary сохраняются.
- Дальнейший `CHANGES_REQUESTED` завершает workflow без ещё одной ревизии. Сбои ревизии и reviewer сохраняют доступный результат primary и никогда не подразумевают одобрения.
## Planner v0.1

- Project plans are generated locally from deterministic templates; no provider or LLM is called.
- Planner reuses `TaskCategory` and assigns stable task IDs and a deterministic plan ID from the normalized goal.
- Template tasks form an explicit sequential dependency chain. The first task starts `READY`; dependent tasks start `PENDING`. Planner v0.1 creates plans only and does not execute or update task statuses.


## Planner v0.1 — русская версия

- Планы проектов генерируются локально из детерминированных шаблонов; провайдер или LLM не вызываются.
- Planner повторно использует `TaskCategory` и назначает стабильные ID задач и детерминированный ID плана по нормализованной цели.
- Задачи шаблона образуют явную последовательную цепочку зависимостей. Первая задача начинается как `READY`; зависимые задачи начинаются как `PENDING`. Planner v0.1 только создаёт планы и не выполняет и не обновляет статусы задач.
## Execution Authorization Contract v0.2

Recorded at commit `ad6b82b` (`feat: reset execution authorization contract v0.2`). Verified current behavior is described in [`ARCHITECTURE.md`](ARCHITECTURE.md) ("Execution Authorization Contract v0.2") and [`SOURCE_OF_TRUTH.md`](SOURCE_OF_TRUTH.md).

### Accepted decisions

- **An immutable `ExecutionIntent` is the single authorization subject.** Permission, Approval, Policy and the backend all evaluate the same frozen value object instead of each reading a different subset of `ExecutionRequest` and `ProjectExecutionProfile` fields. Reason: the v0.1 defects were disagreements between partial views of one invocation, not independent bugs.
- **The intent fingerprint covers every field of the intent,** computed by one function from the intent projection, with canonical sorted serialization. Reason: a hand-assembled key list silently omitted environment variables, output limits and artifact targets.
- **`IntentBuilder` is the only construction path,** with canonical normalization applied once at build time. Reason: normalization performed at comparison time produced three divergent path checks.
- **Canonical path security has two domains and one implementation** (`app/execution/paths.py`): `canonical_executable` for program identity (bare name or exact absolute path) and `normalize_workspace_relative_path` for workspace-relative paths. Reason: a single rule applied to both domains rejected `/etc` and accepted `C:\Windows` — the exact inverse of what is needed.
- **Approval is bound to the intent fingerprint,** and `InMemoryApprovalResolver` is single-use and fingerprint-bound. Reason: an approval for one intent must never authorize a materially different intent.
- **`AuthorizedExecution` is an internal marker created only by the coordinator,** and `LocalExecutionAdapter` rejects anything else before touching the filesystem. Reason: the backend must not rely on developer discipline to know whether authorization happened.
- **An explicit `workspace_root` is mandatory for local process spawning** and fails closed with `workspace_root_required` / `workspace_root_invalid`. Reason: an implicit `Path(".")` staged the process working directory into the execution workspace.
- **The marker is an integrity check, not a cryptographic capability.** The stated threat model is accidental and refactoring-introduced bypass, not resistance to a same-process adversary.
- **Overall status of this contract: PARTIAL, not complete.** The capability classifier remains a recognition list, the coordinator's fingerprint verification depends on the resolver returning a fingerprint, and the intent environment does not include the profile environment. These are recorded as open items in `SOURCE_OF_TRUTH.md` §4.

### Rejected or deferred alternatives (with rationale)

- **Wrapper blacklists.** Extending `dangerous_interpreter_argv` or adding `env`/`sudo`/`make`/`npx` style name lists was rejected as a security strategy. A list of programs that can execute other programs is not enumerable, and its incompleteness fails open for unlisted names. The current implementation still contains a recognition list, which is recorded as a limitation, not as the intended model.
- **Cryptographic approval tokens / HMAC-minted authorization tokens.** Rejected for this block: Python cannot provide in-process non-forgeability, and a token would create a false impression of resistance to an adversary it cannot stop. A private marker plus a private adapter was chosen instead.
- **Whole-workspace and whole-profile content digests.** Rejected as default binding: they create approval churn without adding authority. Scoped digests for referenced inputs remain a deferred candidate.
- **A declarative field registry with per-field justification strings.** Rejected in favor of the intent body being the projection plus a frozen field-name test, to avoid two sources of truth that can disagree.
- **Storing approval authority in the resolver.** Rejected: the resolver is an untrusted proposer, not the verifier.
- **OS-level sandboxing (namespaces, cgroups, Job Objects), kernel network enforcement, and container/VM backends.** Deferred to Block 2 or a separate project. A sandbox changes the consequences of a grant; it does not decide which invocations are admissible, and it must not be treated as a substitute for authorization.
- **Durable/persistent approval store with expiration.** Deferred as a separate design: a persistent, expiring approval is a standing authorization with different semantics from a single-use approval event, and must not be introduced as an incidental resolver improvement.
- **MCP as the permission model.** Rejected: authorization belongs to Forge's Permission -> Approval -> Policy -> Execution boundaries. MCP may later be a tool transport under that authority. See [`TECHNOLOGY_RADAR.md`](TECHNOLOGY_RADAR.md).
- **Universal retries, rollback, parallelism and swarm execution.** Rejected as defaults: complexity and cost are not justified by default, and more agents do not mean better results.
- **Unifying `app/tools/workspace.py` with `app/execution/paths.py` in this block.** Deferred: the tool boundary keeps its own validator; duplication is recorded as a known item rather than silently refactored during an authorization change.

### Open items for the next authorization block

1. Replace the capability recognition list with a fail-closed declared-invocation model so that unrecognized execution behavior requires an explicit capability.
2. Make the coordinator's fingerprint verification unconditional (an approval without a fingerprint must not authorize execution).
3. Include the resolved profile environment in the intent body, or prove that it cannot affect execution.
4. Decide whether the program identity is a name resolved at execution time or a path pinned at approval time (PATH binding / TOCTOU).
5. Decide whether declared-input content digests are mandatory for invocations that execute workspace content.

## Execution Authorization Contract v0.2 — русская версия

Зафиксировано на коммите `ad6b82b` (`feat: reset execution authorization contract v0.2`). Проверенное текущее поведение описано в [`ARCHITECTURE.md`](ARCHITECTURE.md) (раздел "Execution Authorization Contract v0.2") и в [`SOURCE_OF_TRUTH.md`](SOURCE_OF_TRUTH.md).

### Принятые решения

- **Неизменяемый `ExecutionIntent` — единственный субъект авторизации.** Permission, Approval, Policy и backend оценивают один и тот же замороженный объект-значение вместо чтения разных подмножеств полей `ExecutionRequest` и `ProjectExecutionProfile`. Причина: дефекты v0.1 были расхождениями между частными представлениями одной инвокации, а не независимыми ошибками.
- **Fingerprint интента покрывает все поля интента** и вычисляется одной функцией по проекции интента с канонической сортированной сериализацией. Причина: собранный вручную список ключей молча пропускал переменные окружения, лимиты вывода и цели артефактов.
- **`IntentBuilder` — единственный путь конструирования,** каноническая нормализация выполняется один раз при сборке. Причина: нормализация во время сравнения породила три расходящиеся проверки путей.
- **Каноническая безопасность путей имеет два домена и одну реализацию** (`app/execution/paths.py`): `canonical_executable` для идентичности программы (одиночное имя или точный абсолютный путь) и `normalize_workspace_relative_path` для путей относительно workspace. Причина: одно правило для обоих доменов отклоняло `/etc` и принимало `C:\Windows` — ровно инверсия нужного.
- **Одобрение привязано к fingerprint интента,** а `InMemoryApprovalResolver` одноразовый и привязан к fingerprint. Причина: одобрение одного интента не должно авторизовать материально другой интент.
- **`AuthorizedExecution` — внутренний маркер, создаваемый только координатором,** а `LocalExecutionAdapter` отклоняет всё остальное до обращения к файловой системе. Причина: backend не должен зависеть от дисциплины разработчика в вопросе о том, была ли авторизация.
- **Явный `workspace_root` обязателен для локального запуска процессов** и отклоняется fail-closed с `workspace_root_required` / `workspace_root_invalid`. Причина: неявный `Path(".")` копировал рабочий каталог процесса в среду выполнения.
- **Маркер — проверка целостности, а не криптографическое полномочие.** Заявленная модель угроз: случайный и внесённый рефакторингом обход, а не сопротивление злонамеренному коду внутри процесса.
- **Общий статус контракта: PARTIAL, а не завершено.** Классификатор capabilities остаётся списком распознавания, проверка fingerprint координатором зависит от того, вернул ли resolver fingerprint, а окружение интента не включает окружение профиля. Эти пункты зафиксированы как открытые в `SOURCE_OF_TRUTH.md` §4.

### Отклонённые и отложенные альтернативы (с обоснованием)

- **Чёрные списки wrapper-программ.** Расширение `dangerous_interpreter_argv` или добавление списков вида `env`/`sudo`/`make`/`npx` отклонено как стратегия безопасности. Список программ, способных запускать другие программы, не перечислим, и его неполнота означает fail-open для неучтённых имён. Текущая реализация всё ещё содержит список распознавания — это зафиксировано как ограничение, а не как целевая модель.
- **Криптографические токены одобрения и HMAC-маркеры авторизации.** Отклонено для этого блока: Python не даёт неизменяемости внутри процесса, а токен создал бы ложное впечатление защиты от противника, которого он не останавливает. Выбран приватный маркер и приватный адаптер.
- **Digest всего workspace и всего профиля.** Отклонено как привязка по умолчанию: это создаёт churn одобрений без добавления полномочий. Точечные digest'ы для referenced inputs остаются отложенным кандидатом.
- **Декларативный реестр полей с обоснованием по каждому полю.** Отклонён в пользу того, что тело интента само является проекцией, плюс тест, фиксирующий набор имён полей, — чтобы не было двух источников истины, способных разойтись.
- **Хранение полномочия одобрения в resolver.** Отклонено: resolver — недоверенный источник решения, а не проверяющая сторона.
- **OS-песочница (namespaces, cgroups, Job Objects), сетевое ограничение на уровне ядра, container/VM backend'ы.** Отложено в Block 2 или в отдельный проект. Песочница меняет последствия выданного полномочия, но не решает, какие инвокации допустимы, и не должна считаться заменой авторизации.
- **Долговременное (durable) хранилище одобрений с истечением срока.** Отложено как отдельный дизайн: постоянное одобрение с истечением — это standing-авторизация с иной семантикой, нежели одноразовое событие одобрения, и не должно вводиться как побочное улучшение resolver'а.
- **MCP как модель разрешений.** Отклонено: авторизация принадлежит границам Forge Permission -> Approval -> Policy -> Execution. MCP может позже стать транспортом инструментов под этой авторизацией. См. [`TECHNOLOGY_RADAR.md`](TECHNOLOGY_RADAR.md).
- **Универсальные retries, rollback, параллелизм и swarm-исполнение.** Отклонено как поведение по умолчанию: сложность и стоимость не оправданы по умолчанию, а больше агентов не означает лучший результат.
- **Объединение `app/tools/workspace.py` и `app/execution/paths.py` в этом блоке.** Отложено: tool-граница сохраняет собственный валидатор; дублирование зафиксировано как известный пункт, а не молча отрефакторено внутри изменения авторизации.

### Открытые вопросы для следующего блока авторизации

1. Заменить список распознавания capabilities на fail-closed модель объявленных инвокаций, чтобы нераспознанное поведение требовало явной capability.
2. Сделать проверку fingerprint координатором безусловной (одобрение без fingerprint не должно авторизовать выполнение).
3. Включить разрешённое окружение профиля в тело интента либо доказать, что оно не может влиять на выполнение.
4. Решить, является ли идентичность программы именем, разрешаемым во время выполнения, или путём, закреплённым при одобрении (привязка PATH / TOCTOU).
5. Решить, обязательны ли digest'ы объявленных входов для инвокаций, исполняющих содержимое workspace.

## Production Run Authority and RunScope v0.1

Recorded at commit `451ffd1` (discovery wiring) and implemented in the production
tool execution wiring block. Verified against the code in `app/api/service.py`,
`app/runtime/bootstrap.py`, and `app/runtime/run_scope.py`.

### Accepted decisions

- **Tool authority belongs to the operator at the composition layer.** The
  allowlist is declared once when `ForgeApiService` is constructed
  (`allowed_tool_ids`). It is the only source of tool authority for an API run.
- **The default operator allowlist is empty.** `frozenset()` is the starting
  point, so an undeclared service authorizes no tool at all.
- **Effective tools are exactly `operator_allowlist & registered_tools`.**
  Intersecting in that direction means a registered tool that is not allow-listed
  is never authorized, and an allow-listed id with no registered implementation
  never appears in a scope.
- **Discovery is not authorization.** `CapabilityFabric.list_capabilities()` and
  the discovery endpoints enumerate; they grant nothing and are never consulted
  to compute an allowlist.
- **Registered is not allowed.** `ToolRegistry.list_tools()` is never used as an
  allowlist, and `allowed_tool_ids = registry.list_tools()` is prohibited.
- **Skills are not authority.** `SkillEvaluator` consumes an already-authorized
  tool set and can only narrow it; `SkillManifest.requested_tools` is a request,
  not a grant. The built-in skill demonstrates why: it requests
  `python_test_runner`, a tool that does not exist.
- **CapabilityFabric is not authority.** `resolve()` is not on the production
  request path and issues no permissions.
- **The API client cannot widen authority.** `TaskRunRequest` has no tool,
  workspace, or approval fields, and `request.context` is never read to compute
  an allowlist. An escalation attempt in `context` has no effect.
- **RunScope is frozen before privileged execution.** `run_task` builds the scope
  and calls `freeze()` followed by `require_active_scope` before invoking the
  executor, so a later expansion attempt is rejected.
- **Workspace is the explicit execution root.** The scope binds the service's own
  `Workspace`; `RunScope.validate_workspace` rejects any other root, and existing
  `Workspace` containment checks are unchanged.
- **Approval is not authorization.** Approval runs after permission and cannot
  grant a tool the allowlist withheld.
- **`write_project_file` without a resolver stays `WAITING_FOR_APPROVAL`.** No
  auto-approve resolver is introduced.
- **One registry serves discovery and execution.** The same `ToolRegistry`
  instance backs `CapabilityFabric` and the runtime `ToolExecutor`, so
  enumeration cannot report a tool that execution cannot reach.
- **Host process execution is not part of this patch.** `ExecutionCoordinator`,
  `LocalExecutionAdapter`, `AgentHarness`, `EngineeringRunExecutor`, and
  `execution_requests` remain unwired from the API. The API scope is created with
  `allowed_execution_commands=frozenset()`, so no command is reachable.

### Rejected or deferred alternatives (with rationale)

- **Allow-listing every registered tool.** Rejected: it makes discovery into an
  authorization source and lets a client-influenced task reach
  `write_project_file` in the workspace root.
- **Deriving authority from `TaskRunRequest` or `request.context`.** Rejected:
  authority must never travel in the HTTP contract, and the caller is untrusted.
- **Deriving authority from skills.** Rejected: skill manifests are content, not
  grants, and `requested_tools` may name tools that do not exist.
- **A project configuration layer supplying tool allowlists.** Rejected for this
  block: no such configuration exists, and creating one would introduce a new
  authority layer. Authority stays an explicit operator declaration.
- **Auto-approving write tools to make runs complete.** Rejected: it converts
  approval into authorization.
- **Wiring `AgentHarness` or `EngineeringRunExecutor` as the API entry point.**
  Deferred: `AgentHarness` does not dispatch tools at all, and
  `EngineeringRunExecutor` requires acceptance criteria and verification
  expectations the API cannot supply. Either would be its own architectural
  stage, not a small patch.
- **Host process execution through `ExecutionCoordinator`.** Deferred as the next
  architectural stage. It needs its own decision about which commands an API run
  may spawn and where.

### Open follow-up items

1. Decide whether an API run may ever spawn host processes, and if so which
   commands and under which execution root.
2. Decide whether tool authority should ever become configurable per project,
   and if so where that configuration lives and who may edit it.
3. Revisit whether the `api-default` execution profile should declare a real
   executable set once host process execution is designed.
4. Give the API a way to express a legitimate, narrowing tool request once a
   server-side allowlist exists to narrow against.

## Production Run Authority and RunScope v0.1 — русская версия

Зафиксировано на коммите `451ffd1` (discovery wiring) и реализовано в блоке
production tool execution wiring. Проверено по коду в `app/api/service.py`,
`app/runtime/bootstrap.py` и `app/runtime/run_scope.py`.

### Принятые решения

- **Authority на tools принадлежит оператору на уровне композиции.** Allowlist
  объявляется один раз при создании `ForgeApiService` (`allowed_tool_ids`) и
  является единственным источником tool-authority для API-run.
- **Default-allowlist оператора пуст.** `frozenset()` — отправная точка, поэтому
  сервис без объявления не авторизует ни один tool.
- **Effective tools — ровно `operator_allowlist & registered_tools`.**
  Пересечение именно в эту сторону: зарегистрированный, но не разрешённый tool
  никогда не авторизуется, а разрешённый id без реализации не попадает в scope.
- **Discovery не является authorization.** `CapabilityFabric.list_capabilities()`
  и discovery-endpoint'ы только перечисляют; они ничего не выдают и никогда не
  используются для вычисления allowlist.
- **Registered не равно allowed.** `ToolRegistry.list_tools()` не используется как
  allowlist, а `allowed_tool_ids = registry.list_tools()` запрещено.
- **Skills не являются authority.** `SkillEvaluator` потребляет уже
  авторизованный набор tools и может только сужать его;
  `SkillManifest.requested_tools` — это запрос, а не разрешение. Встроенный скилл
  показывает почему: он запрашивает `python_test_runner`, которого не существует.
- **CapabilityFabric не является authority.** `resolve()` не находится на
  production-пути запроса и не выдаёт разрешений.
- **API-клиент не может расширить authority.** В `TaskRunRequest` нет полей
  tools, workspace или approval, а `request.context` никогда не читается для
  вычисления allowlist. Попытка эскалации через `context` не даёт эффекта.
- **RunScope замораживается до привилегированного выполнения.** `run_task`
  создаёт scope, вызывает `freeze()`, затем `require_active_scope` — и только
  после этого вызывает executor, поэтому последующее расширение отвергается.
- **Workspace — явный execution root.** Scope привязывается к собственному
  `Workspace` сервиса; `RunScope.validate_workspace` отвергает любой другой
  корень, а существующие containment-проверки `Workspace` не изменены.
- **Approval не является authorization.** Approval выполняется после permission и
  не может выдать tool, который не прошёл allowlist.
- **`write_project_file` без resolver остаётся `WAITING_FOR_APPROVAL`.**
  Auto-approve resolver не вводится.
- **Один registry обслуживает discovery и execution.** Один и тот же экземпляр
  `ToolRegistry` используется и в `CapabilityFabric`, и в runtime `ToolExecutor`,
  поэтому перечисление не может показать tool, недостижимый для выполнения.
- **Host process execution не входит в этот патч.** `ExecutionCoordinator`,
  `LocalExecutionAdapter`, `AgentHarness`, `EngineeringRunExecutor` и
  `execution_requests` остаются не подключёнными к API. API-scope создаётся с
  `allowed_execution_commands=frozenset()`, поэтому ни одна команда недостижима.

### Отклонённые или отложенные альтернативы (с обоснованием)

- **Разрешить все зарегистрированные tools.** Отклонено: это превращает discovery
  в источник authorization и позволяет задаче, на которую влияет клиент, дойти до
  `write_project_file` в корне workspace.
- **Выводить authority из `TaskRunRequest` или `request.context`.** Отклонено:
  authority не должна передаваться в HTTP-контракте, а вызывающий недоверенный.
- **Выводить authority из скиллов.** Отклонено: манифесты скиллов — это контент,
  а не разрешения, и `requested_tools` может называть несуществующие tools.
- **Слой проектной конфигурации с allowlist'ами tools.** Отклонено для этого
  блока: такой конфигурации не существует, а её создание добавило бы новый слой
  authority. Authority остаётся явным объявлением оператора.
- **Авто-одобрение write-tools ради завершения запусков.** Отклонено: это
  превращает approval в authorization.
- **Подключить `AgentHarness` или `EngineeringRunExecutor` как API entry point.**
  Отложено: `AgentHarness` вообще не диспетчеризует tools, а
  `EngineeringRunExecutor` требует acceptance criteria и verification
  expectations, которых API предоставить не может. Любой из них — отдельный
  архитектурный этап, а не маленький патч.
- **Host process execution через `ExecutionCoordinator`.** Отложено как следующий
  архитектурный этап. Требует отдельного решения о том, какие команды может
  запускать API-run и где.

### Открытые follow-up пункты

1. Решить, может ли API-run вообще запускать host-процессы, и если да — какие
   команды и под каким execution root.
2. Решить, должна ли tool-authority когда-либо стать настраиваемой по проекту, и
   если да — где живёт эта конфигурация и кто может её редактировать.
3. Пересмотреть, должен ли профиль `api-default` объявлять реальный набор
   исполняемых файлов после проектирования host process execution.
4. Дать API способ выражать легитимный сужающий запрос tools, когда появится
   server-side allowlist, относительно которого можно сужать.

## Production Host Process Execution Authority v0.1

Implemented in the host process execution wiring block. Verified against the code
in `app/orchestrator/run.py`, `app/api/service.py`, `app/execution/`, and
`app/runtime/run_scope.py`.

### Accepted decisions

- **Operator command authority is the only source of authority to run a host
  process.** `ForgeApiService` takes `allowed_execution_commands`, declared once
  at composition time. It is never derived from `TaskRunRequest`, the request
  context, a skill manifest, `SkillEvaluator`, `CapabilityFabric`, the tool
  registry, `registry.list_tools()`, or `execution_profile.allowed_commands`.
- **The default command allowlist is empty**, which means host process execution
  is disabled. `RunScope.allows_command` rejects every command when the set is
  empty, so no process is reachable until an operator declares one.
- **`RunScope` is the frozen perimeter.** Each API run builds a scope with the
  service's own `Workspace`, the service execution profile, the effective tool
  allowlist, and the operator command allowlist, then calls `freeze()` and
  `require_active_scope()` before the executor runs.
- **The execution profile is a ceiling, not a grant.** `allowed_commands`,
  `capabilities`, `network_access`, `environment_variables`, `timeout_seconds`,
  `max_output_bytes`, and `working_directory` bound what a request may ask for.
  A profile never authorizes anything by itself.
- **`ExecutionRequest` is a request, never authority.** It cannot widen the
  scope, the workspace, the profile, the environment, the network, the timeout,
  or the output limit; every field is re-validated against the frozen scope.
- **`ExecutionCoordinator` is the authorization boundary.** It is the only
  component that evaluates permission, approval, and policy, and the only one
  that may mint an execution token.
- **`AuthorizedExecution` is the execution token.** It is created solely by the
  coordinator with the internal sentinel, is never constructed manually, never
  travels over HTTP, and is never written to the durable run store.
- **`LocalExecutionAdapter` is the only component that spawns a process**, and it
  refuses anything that is not a valid `AuthorizedExecution`.
- **`TaskRunRequest` carries no authority fields.** It gained no command,
  workspace, profile, environment, network, timeout, or output parameter.
- **Approval is server-side.** Host execution requires an explicit server-side
  `ApprovalPolicy`; without one the executor refuses rather than falling back to
  the request's own `approval_required` flag, so that flag can never be the only
  approval mechanism. A missing resolver is fail-closed and reported as an
  approval wait.
- **The production entry point is the existing `RunExecutor`**, extended with an
  execution branch beside the tool path. No new executor was created, and
  `AgentHarness` / `EngineeringRunExecutor` were not repurposed as API entry
  points.
- **No automatic resume and no idempotency.** A recorded `EXECUTION_STARTED` is
  never treated as permission to re-run, and a crash between process start and
  result recording produces no retry.
- **Network restriction is currently best-effort.** `network_access=False` sets
  proxy environment variables for the child process. It is not a kernel-level
  block, and nothing in the documentation claims otherwise.

### Rejected or deferred alternatives (with rationale)

- **A new executor for host execution.** Rejected: `RunExecutor` already carries
  the run id, workspace, frozen scope, and run store, so a fourth executor would
  duplicate authority.
- **Using `AgentHarness` as the API entry point.** Rejected: it does not dispatch
  tools at all, so adopting it would mean adding a tool path, not wiring one.
- **Using `EngineeringRunExecutor` as the API entry point.** Deferred: it
  requires acceptance criteria and verification expectations the API cannot
  supply.
- **Turning `TestVerificationAdapter` into a registered Tool.** Rejected for this
  block: it would create an artificial tool/permission surface purely to generate
  a request.
- **Deriving the command allowlist from the profile.** Rejected: the profile is a
  ceiling, and treating it as a grant would make every profile entry executable.
- **Relying on `request.approval_required`.** Rejected: approval must not depend
  on a field a request can set to `False`.
- **Kernel-level network enforcement, namespaces, seccomp, or proxy-bypass
  protection.** Deferred as a separate security block.
- **Idempotent side-effect execution.** Deferred as its own block; it is a
  prerequisite for durable/resumable host execution, not for wiring.

### Consequences and open items

1. **The production `ExecutionRequest` source is not wired and requires a
   separate architectural decision.** The executor accepts a server-side
   request factory, but `ForgeApiService` supplies none, because no existing
   server-side use case can legitimately produce an execution request from the
   API contract. Until that decision is made, host process execution stays
   disabled by default and the operator command allowlist stays empty.
2. The working-directory isolation provided by `EphemeralWorkspaceManager` uses
   COPY semantics: it stages the source workspace into a scratch directory and
   runs the process there, but it is not a filesystem sandbox. A process given an
   absolute path can still write outside the scratch tree. OS-level isolation
   remains deferred.
3. Idempotent side-effect execution must land before host execution is allowed
   into any durable or resumable flow.

## Production Host Process Execution Authority v0.1 — русская версия

Реализовано в блоке host process execution wiring. Проверено по коду в
`app/orchestrator/run.py`, `app/api/service.py`, `app/execution/` и
`app/runtime/run_scope.py`.

### Принятые решения

- **Authority оператора на команды — единственный источник права запустить
  host-процесс.** `ForgeApiService` принимает `allowed_execution_commands`,
  объявляемый один раз на уровне композиции. Он никогда не выводится из
  `TaskRunRequest`, контекста запроса, манифеста скилла, `SkillEvaluator`,
  `CapabilityFabric`, реестра tools, `registry.list_tools()` или
  `execution_profile.allowed_commands`.
- **Default-allowlist команд пуст**, то есть host process execution выключен.
  `RunScope.allows_command` отвергает любую команду при пустом множестве,
  поэтому ни один процесс недостижим, пока оператор не объявит команду.
- **`RunScope` — замороженный периметр.** Каждый API-run создаёт scope с
  собственным `Workspace` сервиса, профилем выполнения, effective tool allowlist
  и allowlist команд оператора, затем вызывает `freeze()` и
  `require_active_scope()` до запуска executor'а.
- **Профиль выполнения — потолок, а не разрешение.** `allowed_commands`,
  `capabilities`, `network_access`, `environment_variables`, `timeout_seconds`,
  `max_output_bytes` и `working_directory` ограничивают то, что может запросить
  request. Сам профиль ничего не авторизует.
- **`ExecutionRequest` — запрос, а не authority.** Он не может расширить scope,
  workspace, профиль, окружение, сеть, timeout или лимит вывода; каждое поле
  повторно проверяется против замороженного scope.
- **`ExecutionCoordinator` — граница авторизации.** Только он оценивает
  permission, approval и policy и только он может выпустить execution-токен.
- **`AuthorizedExecution` — execution-токен.** Создаётся исключительно
  координатором через внутренний sentinel, никогда не конструируется вручную,
  никогда не передаётся по HTTP и никогда не записывается в durable run store.
- **`LocalExecutionAdapter` — единственный, кто запускает процесс**, и он
  отвергает всё, что не является валидным `AuthorizedExecution`.
- **`TaskRunRequest` не несёт authority-полей.** В него не добавлены ни команда,
  ни workspace, ни профиль, ни окружение, ни сеть, ни timeout, ни лимит вывода.
- **Approval — server-side.** Host execution требует явной server-side
  `ApprovalPolicy`; без неё executor отказывается, а не откатывается к флагу
  `approval_required` самого запроса, поэтому этот флаг никогда не может быть
  единственным механизмом approval. Отсутствие resolver'а — fail-closed и
  сообщается как ожидание approval.
- **Production entry point — существующий `RunExecutor`**, расширенный веткой
  execution рядом с tool-путём. Новый executor не создавался, а `AgentHarness` /
  `EngineeringRunExecutor` не превращались в API entry point.
- **Никакого automatic resume и никакой идемпотентности.** Записанный
  `EXECUTION_STARTED` никогда не считается разрешением на повторный запуск, а
  краш между запуском процесса и записью результата не приводит к повтору.
- **Ограничение сети сейчас best-effort.** `network_access=False` выставляет
  прокси-переменные окружения для дочернего процесса. Это не kernel-level
  блокировка, и никакая документация не утверждает обратного.

### Отклонённые или отложенные альтернативы (с обоснованием)

- **Новый executor для host execution.** Отклонено: `RunExecutor` уже несёт run
  id, workspace, замороженный scope и run store, поэтому четвёртый executor
  дублировал бы authority.
- **Использовать `AgentHarness` как API entry point.** Отклонено: он вообще не
  диспетчеризует tools, поэтому его принятие означало бы добавление tool-пути, а
  не подключение существующего.
- **Использовать `EngineeringRunExecutor` как API entry point.** Отложено: он
  требует acceptance criteria и verification expectations, которых API не может
  предоставить.
- **Превратить `TestVerificationAdapter` в зарегистрированный Tool.** Отклонено
  для этого блока: это создало бы искусственную tool/permission-поверхность
  исключительно ради генерации запроса.
- **Выводить allowlist команд из профиля.** Отклонено: профиль — потолок, и
  трактовка его как разрешения сделала бы исполняемой каждую его запись.
- **Полагаться на `request.approval_required`.** Отклонено: approval не должен
  зависеть от поля, которое запрос может выставить в `False`.
- **Kernel-level network enforcement, namespaces, seccomp, защита от обхода
  прокси.** Отложено как отдельный security-блок.
- **Идемпотентное исполнение side effects.** Отложено как отдельный блок; это
  предпосылка для durable/resumable host execution, а не для wiring.

### Следствия и открытые пункты

1. **Production-источник `ExecutionRequest` не подключён и требует отдельного
   архитектурного решения.** Executor принимает server-side фабрику запросов, но
   `ForgeApiService` её не предоставляет, потому что ни один существующий
   server-side сценарий не может легитимно породить execution request из
   API-контракта. Пока это решение не принято, host process execution остаётся
   выключенным по умолчанию, а allowlist команд оператора — пустым.
2. Изоляция рабочей директории, предоставляемая `EphemeralWorkspaceManager`,
   использует COPY-семантику: она разворачивает исходный workspace в scratch-каталог
   и запускает процесс там, но это не файловая песочница. Процесс, получивший
   абсолютный путь, всё ещё может писать за пределами scratch-дерева. OS-level
   изоляция остаётся отложенной.
3. Идемпотентное исполнение side effects должно появиться до того, как host
   execution будет допущен в любой durable- или resumable-flow.

## Server-Side Declared Execution Intent v0.1

Implemented in the declared verification execution block. Verified against the
code in `app/execution/declaration.py`, `app/api/service.py`,
`app/orchestrator/run.py`, and `app/runtime/run_scope.py`.

### Accepted decisions

- **Host command authority belongs to the operator.** It is declared at
  composition time as `ExecutionDeclaration` values on `ForgeApiService`, next to
  the tool allowlist, the execution profile, and the approval policy.
- **A declaration is data, never a gate.** It can only add limits; it cannot
  authorize anything on its own and it introduces no new permission layer.
- **`command` is the only authority-bearing field.** Every other field
  identifies the declaration or narrows it further, and each is re-validated
  against the frozen `RunScope` and the execution profile.
- **Declarations are frozen and fail closed at composition time.** A declaration
  the active profile cannot admit - wrong executable, different working
  directory, unauthorized environment variable, timeout above the ceiling, or a
  mismatched profile id - is rejected when the service is constructed.
- **v0.1 is verification-only.** `purpose` accepts exactly `verification`;
  anything else is rejected, so the type does not imply that wider purposes are
  merely unconfigured.
- **`TaskRunRequest` is unchanged.** It gained no command, declaration id,
  workspace, profile, environment, timeout, or allowlist field.
- **No client input can select or alter a command.** The resolver receives only
  the task and the active profile. `context`, the task description, `category`,
  the skill layer, the fabric, the tool registry, `Decision`, and the execution
  profile are all non-authoritative for command selection.
- **`category` does not select a declaration.** Mapping a client-supplied
  category onto an arbitrary declaration would let a client choose which
  operator-declared command runs, so that channel is rejected.
- **`allowed_execution_commands` is derived, not configured.** It is the
  executable of the declaration the server-side resolver actually selected. An
  unresolved, unknown, or absent selection yields the empty set, which disables
  host execution for the run.
- **The legacy `allowed_execution_commands` constructor parameter grants
  nothing.** Two independent command sources could disagree, and one of them
  would silently widen the perimeter. The parameter is retained for
  compatibility only.
- **`RunScope` remains the gate.** Its semantics were not changed: it receives
  the already-derived command set, then freezes, then the coordinator
  re-validates every request against it.
- **`ExecutionCoordinator` remains the sole creator of `AuthorizedExecution`.**
  No new executor, token, or authority layer was introduced.
- **Approval stays server-side policy.** A declaration has no
  `approval_required` field, so it cannot weaken approval; a missing
  `ApprovalPolicy` refuses, and a missing resolver is reported as an approval
  wait.
- **No automatic resume and no idempotency.** A recorded `EXECUTION_STARTED` is
  never permission to re-run.

### Rejected alternatives (with rationale)

- **Reusing `TestVerificationIntent` as the general authority container.**
  Rejected: it requires `criterion_id` and `verification_id`, so a non-verification
  purpose would have to invent them, and the type would imply a verification
  contract that does not exist.
- **Turning `TestVerificationAdapter` into a Tool.** Rejected: it would create a
  tool and permission surface purely to generate a request.
- **`TaskSpecification` as the declaration carrier.** Rejected: no production
  code constructs a `TaskSpecification` at all, so a field there would either be
  operator-supplied (equivalent to a declaration, but heavier) or a renamed
  `context` channel.
- **`Decision` as the authority reference.** Rejected: `Decision` deliberately
  carries no authority, and `RunExecutor` does not reach the decision machinery.
- **Declaring `approval_required` on a declaration.** Rejected: it would let a
  declaration remove an approval requirement.
- **Deriving authority from `execution_profile.allowed_commands`.** Rejected:
  the profile is a ceiling, and treating it as a grant would make every declared
  executable runnable.
- **A client-supplied `declaration_id` on the HTTP contract.** Rejected for
  v0.1: it would let a client pick which operator-declared command runs.

### Consequences and open items

1. **The verification binding requires a separate architectural decision.** The
   declaration mechanism is complete and tested end-to-end, but the API path has
   no trusted server-side verification identity: no production code constructs a
   `VerificationExpectation` or a `TestVerificationIntent`, and `TaskRunRequest`
   carries no verification field. No server-side resolver can therefore select a
   declaration from production data, and a client-controlled selector is
   rejected by design. Production host process execution consequently remains
   disabled by default, and the roadmap item for the binding stays open.
2. **GAP-A (workspace isolation).** `EphemeralWorkspaceManager` uses COPY
   staging into a scratch directory; it is a working-directory boundary, not a
   filesystem sandbox. A process given an absolute path can still write outside
   the scratch tree.
3. **GAP-B (network).** `network_access=False` sets proxy environment variables.
   It is best-effort and not a kernel-level block.
4. **GAP-C — CLOSED.** `RunScope.validate_execution_profile()` enforces the
   frozen capability ceiling and rejects attempts to widen capabilities, so a
   request profile can no longer declare capabilities the scope never approved.
   This protection already existed in `RunScope` before this block (closed in
   `1220398 security: close execution authority hardening block`); this entry
   corrects an earlier statement that described it as open.
5. **Policy capabilities matter for declarations.** `ExecutionPolicy` requires
   every capability the argv implies to be declared in the profile, so `python -c`
   requires `INTERPRET_TEXT` in the ceiling. The `api-default` profile now
   declares it; this widens what a declaration *could* ask for but grants
   nothing, since only a declaration can produce a request.

## Server-Side Declared Execution Intent v0.1 — русская версия

Реализовано в блоке declared verification execution. Проверено по коду в
`app/execution/declaration.py`, `app/api/service.py`, `app/orchestrator/run.py` и
`app/runtime/run_scope.py`.

### Принятые решения

- **Authority на host-команды принадлежит оператору.** Она объявляется на уровне
  композиции значениями `ExecutionDeclaration` в `ForgeApiService`, рядом с
  allowlist tools, профилем выполнения и approval-политикой.
- **Объявление — это данные, а не гейт.** Оно может только добавлять ограничения;
  само по себе оно ничего не авторизует и не вводит новый permission-слой.
- **`command` — единственное поле, несущее authority.** Остальные поля
  идентифицируют объявление или сужают его, и каждое повторно проверяется против
  замороженного `RunScope` и профиля выполнения.
- **Объявления заморожены и падают fail-closed на композиции.** Объявление,
  которое активный профиль не может admit'нуть — неверный executable, другой
  working directory, неразрешённая переменная окружения, timeout выше потолка или
  несовпадающий profile id — отвергается при создании сервиса.
- **v0.1 — только verification.** `purpose` принимает ровно `verification`; всё
  остальное отвергается, поэтому тип не подразумевает, что более широкие purpose'ы
  просто не сконфигурированы.
- **`TaskRunRequest` не изменён.** В него не добавлены ни команда, ни
  declaration id, ни workspace, ни профиль, ни окружение, ни timeout, ни allowlist.
- **Никакой клиентский ввод не может выбрать или изменить команду.** Resolver
  получает только задачу и активный профиль. `context`, description задачи,
  `category`, слой скиллов, fabric, реестр tools, `Decision` и профиль выполнения
  не являются authority для выбора команды.
- **`category` не выбирает объявление.** Отображение клиентского `category` на
  произвольное объявление позволило бы клиенту выбирать, какая объявленная
  оператором команда выполнится, поэтому этот канал отвергнут.
- **`allowed_execution_commands` выводится, а не конфигурируется.** Это executable
  того объявления, которое фактически выбрал server-side resolver. Неразрешённый,
  неизвестный или отсутствующий выбор даёт пустое множество, что выключает host
  execution для запуска.
- **Legacy-параметр `allowed_execution_commands` ничего не даёт.** Два независимых
  источника команд могли бы расходиться, и один из них молча расширил бы периметр.
  Параметр сохранён только для совместимости.
- **`RunScope` остаётся гейтом.** Его семантика не менялась: он получает уже
  производный набор команд, затем замораживается, затем координатор перепроверяет
  каждый запрос против него.
- **`ExecutionCoordinator` остаётся единственным создателем `AuthorizedExecution`.**
  Новый executor, токен или authority-слой не вводились.
- **Approval остаётся server-side политикой.** У объявления нет поля
  `approval_required`, поэтому оно не может ослабить approval; отсутствие
  `ApprovalPolicy` отказывает, а отсутствие resolver'а сообщается как ожидание
  approval.
- **Никакого automatic resume и идемпотентности.** Записанный `EXECUTION_STARTED`
  никогда не является разрешением на повторный запуск.

### Отклонённые альтернативы (с обоснованием)

- **Переиспользовать `TestVerificationIntent` как общий authority-контейнер.**
  Отклонено: он требует `criterion_id` и `verification_id`, поэтому для
  не-verification purpose их пришлось бы выдумывать, а тип подразумевал бы
  несуществующий verification-контракт.
- **Превратить `TestVerificationAdapter` в Tool.** Отклонено: это создало бы
  tool- и permission-поверхность исключительно ради генерации запроса.
- **`TaskSpecification` как носитель объявления.** Отклонено: ни один production-код
  не создаёт `TaskSpecification` вовсе, поэтому поле там было бы либо
  операторским (эквивалент объявления, но тяжелее), либо переименованным каналом
  `context`.
- **`Decision` как authority-ссылка.** Отклонено: `Decision` намеренно не несёт
  authority, а `RunExecutor` не достигает decision-машинерии.
- **Объявлять `approval_required` в объявлении.** Отклонено: это позволило бы
  объявлению снять требование approval.
- **Выводить authority из `execution_profile.allowed_commands`.** Отклонено:
  профиль — потолок, и трактовка его как разрешения сделала бы исполняемым каждый
  объявленный executable.
- **Клиентский `declaration_id` в HTTP-контракте.** Отклонено для v0.1: это
  позволило бы клиенту выбирать, какая объявленная оператором команда выполнится.

### Следствия и открытые пункты

1. **Привязка verification требует отдельного архитектурного решения.** Механизм
   объявлений полон и протестирован end-to-end, но в API-пути нет доверенной
   server-side verification-идентичности: ни один production-код не создаёт
   `VerificationExpectation` или `TestVerificationIntent`, а `TaskRunRequest` не
   несёт verification-поля. Поэтому ни один server-side resolver не может выбрать
   объявление из production-данных, а управляемый клиентом селектор отвергнут by
   design. Как следствие production host process execution остаётся выключенным по
   умолчанию, и пункт roadmap по привязке остаётся открытым.
2. **GAP-A (изоляция workspace).** `EphemeralWorkspaceManager` использует
   COPY-разворачивание в scratch-каталог; это граница рабочей директории, а не
   файловая песочница. Процесс, получивший абсолютный путь, всё ещё может писать
   за пределами scratch-дерева.
3. **GAP-B (сеть).** `network_access=False` выставляет прокси-переменные
   окружения. Это best-effort и не kernel-level блокировка.
4. **GAP-C — CLOSED.** `RunScope.validate_execution_profile()` обеспечивает
   соблюдение замороженного потолка capabilities и отвергает попытки расширить
   capabilities, поэтому профиль запроса больше не может объявить capabilities,
   которые scope не одобрял. Эта защита уже существовала в `RunScope` до этого
   блока (закрыта в `1220398 security: close execution authority hardening
   block`); данная запись исправляет более раннее утверждение, описывавшее её как
   открытую.
5. **Capabilities политики важны для объявлений.** `ExecutionPolicy` требует, чтобы
   каждая capability, которую подразумевает argv, была объявлена в профиле, поэтому
   `python -c` требует `INTERPRET_TEXT` в потолке. Профиль `api-default` теперь его
   объявляет; это расширяет то, что объявление *могло бы* запросить, но ничего не
   выдаёт, поскольку только объявление может породить запрос.

## Trusted Server-Side Verification Entry Point v0.1

Implemented in the declared verification execution block. Verified against the
code in `app/api/service.py` and `tests/test_api_declared_execution.py`.

### Accepted decisions

- **The entry point is server-side in-process code, not an HTTP route.**
  `ForgeApiService.run_declared_verification(declaration_id, *,
  purpose_run_id=None)` is called by operator-controlled code. No HTTP endpoint
  was added, because the HTTP layer has no caller-trust model at all.
- **The declaration id is the only selection input.** It resolves exclusively
  against the operator-configured registry. This makes the selection a
  composition fact rather than a lookup over production data, which is why no
  verification identity is needed for it.
- **No authority parameter is accepted.** No command, workspace, execution
  profile, environment, timeout, allowlist, or approval flag. A caller can choose
  *which* declared verification runs, never *what* runs.
- **`TaskRunRequest` is unchanged** and the existing HTTP routes are unchanged.
  The desktop contract is unchanged.
- **`run_task` never invokes declared verification.** The ordinary API task path
  stays execution-disabled even when declarations are configured, so the
  client-reachable surface gains nothing.
- **Unknown declaration fails closed** with the existing
  `UnknownExecutionDeclarationError`, before any scope is frozen, any request is
  built, or any process starts.
- **No declarations means disabled.** There is no default command, no implicit
  first declaration, and no category-based selection.
- **The service generates the run id** and builds the `RunScope` from its own
  trusted values: its `Workspace`, its execution profile, the declared
  executable as the command set, and the system acceptance criterion. No second
  scope mechanism was introduced and `RunScope` semantics were not changed.
- **The existing execution chain is reused unchanged:** `RunExecutor` →
  `ExecutionCoordinator` → `AuthorizedExecution` → `LocalExecutionAdapter`. The
  entry point never bypasses the coordinator, never constructs a token, and never
  spawns a process itself.
- **`purpose_run_id` is correlation only.** It is attached to the translated
  request as sanitized metadata and cannot change what executes.
- **Approval remains server-side policy.** A declaration still has no
  `approval_required` field, a missing `ApprovalPolicy` refuses with
  `approval_policy_required`, a missing resolver waits, and an approval whose
  fingerprint does not match the intent is rejected with
  `intent_fingerprint_mismatch`.

### Rejected alternatives (with rationale)

- **A new HTTP endpoint.** Rejected for now: the HTTP layer has no caller
  authentication or trust model, so exposing this would require a separate
  decision about caller trust before it could be safe.
- **A client-supplied `declaration_id` on `TaskRunRequest`.** Rejected: it would
  let a client pick which operator-declared command runs, and it would make the
  ordinary task path an execution path.
- **A `category` or task-based resolver for production.** Rejected: `category`,
  `description`, `context`, and `task_id` are all client-supplied, so any of them
  as a selector gives the client command selection.
- **A placeholder acceptance criterion presented as a real link.** Rejected: the
  system criterion is used only because `RunScope` requires a non-empty
  declaration, and it is documented as carrying no verification meaning.
- **Renaming `ExecutionDeclaration` to `VerificationExecutionDeclaration`.**
  Deferred as cosmetic; `purpose` is already restricted to `verification`, so the
  rename would change readability but not authority or security.

### Consequences and open items

1. **GAP-F: no criterion binding.** The entry point runs a declared verification
   and records a sanitized result, but there is no trusted criterion identity to
   attach that result to. It is deliberately not linked to a client task's
   acceptance criteria, and no placeholder criterion is presented as real. This
   requires its own decision.
2. **Task-driven verification binding still needs a decision.** There is still no
   production `VerificationExpectation` or `TestVerificationIntent`, and
   `TaskRunRequest` carries no verification field, so the resolver-based path in
   `run_task` remains unused and host execution there stays disabled.
3. **GAP-A, GAP-B and idempotency are unchanged** by this block. GAP-C was
   already closed before it, in `RunScope.validate_execution_profile()`.

## Trusted Server-Side Verification Entry Point v0.1 — русская версия

Реализовано в блоке declared verification execution. Проверено по коду в
`app/api/service.py` и `tests/test_api_declared_execution.py`.

### Принятые решения

- **Точка входа — server-side код внутри процесса, а не HTTP-маршрут.**
  `ForgeApiService.run_declared_verification(declaration_id, *,
  purpose_run_id=None)` вызывается операторским кодом. HTTP-endpoint не добавлен,
  поскольку у HTTP-слоя вообще нет модели доверия вызывающего.
- **Declaration id — единственный вход выбора.** Он разрешается исключительно
  через операторский реестр. Это делает выбор фактом композиции, а не поиском по
  production-данным, поэтому verification-идентичность для него не нужна.
- **Ни один authority-параметр не принимается.** Ни команда, ни workspace, ни
  профиль выполнения, ни окружение, ни timeout, ни allowlist, ни approval-флаг.
  Вызывающий может выбрать *какая* объявленная verification выполнится, но
  никогда — *что* выполнится.
- **`TaskRunRequest` не изменён**, существующие HTTP-маршруты не изменены,
  контракт desktop не изменён.
- **`run_task` никогда не вызывает объявленную verification.** Обычный API-путь
  задачи остаётся execution-disabled даже при сконфигурированных объявлениях,
  поэтому клиентски достижимая поверхность ничего не приобретает.
- **Неизвестное объявление падает fail-closed** существующей ошибкой
  `UnknownExecutionDeclarationError` — до заморозки scope, до построения запроса,
  до запуска процесса.
- **Нет объявлений — выключено.** Нет ни default-команды, ни implicit первого
  объявления, ни выбора по category.
- **Run id генерирует сервис**, а `RunScope` строится из его собственных
  доверенных значений: `Workspace`, профиль выполнения, объявленный executable
  как набор команд и системный acceptance-критерий. Второй механизм scope не
  вводился, семантика `RunScope` не менялась.
- **Существующая цепочка исполнения переиспользована без изменений:**
  `RunExecutor` → `ExecutionCoordinator` → `AuthorizedExecution` →
  `LocalExecutionAdapter`. Точка входа не обходит координатор, не конструирует
  токен и не запускает процесс сама.
- **`purpose_run_id` — только correlation.** Он прикрепляется к
  транслированному запросу как санитизированные метаданные и не может изменить то,
  что выполняется.
- **Approval остаётся server-side политикой.** У объявления по-прежнему нет поля
  `approval_required`; отсутствие `ApprovalPolicy` отказывает с
  `approval_policy_required`, отсутствие resolver'а даёт ожидание, а approval с
  несовпадающим fingerprint отвергается с `intent_fingerprint_mismatch`.

### Отклонённые альтернативы (с обоснованием)

- **Новый HTTP-endpoint.** Отклонено сейчас: у HTTP-слоя нет аутентификации
  вызывающего и модели доверия, поэтому его появление потребовало бы отдельного
  решения о доверии до того, как это станет безопасным.
- **Клиентский `declaration_id` в `TaskRunRequest`.** Отклонено: это позволило бы
  клиенту выбирать, какая объявленная оператором команда выполнится, и превратило
  бы обычный путь задачи в путь исполнения.
- **Resolver по `category` или по задаче для production.** Отклонено: `category`,
  `description`, `context` и `task_id` все клиентские, поэтому любой из них как
  селектор даёт клиенту выбор команды.
- **Placeholder-критерий, выдаваемый за реальную связь.** Отклонено: системный
  критерий используется только потому, что `RunScope` требует непустое
  объявление, и документирован как не несущий verification-смысла.
- **Переименование `ExecutionDeclaration` в `VerificationExecutionDeclaration`.**
  Отложено как косметика; `purpose` уже ограничен `verification`, поэтому
  переименование изменило бы читаемость, но не authority и не безопасность.

### Следствия и открытые пункты

1. **GAP-F: привязки к критерию нет.** Точка входа выполняет объявленную
   verification и записывает санитизированный результат, но доверенной
   criterion-идентичности, к которой этот результат можно привязать, нет. Он
   намеренно не связывается с acceptance criteria клиентской задачи, и
   placeholder-критерий не выдаётся за настоящий. Это требует отдельного решения.
2. **Привязка verification к задаче всё ещё требует решения.** Production
   `VerificationExpectation` или `TestVerificationIntent` по-прежнему нет, а
   `TaskRunRequest` не несёт verification-поля, поэтому путь через resolver в
   `run_task` остаётся неиспользуемым, а host execution там — выключенным.
3. **GAP-A, GAP-B и идемпотентность этим блоком не изменены.** GAP-C был закрыт
   до него, в `RunScope.validate_execution_profile()`.

## Production Agent Loop Vertical Slice v0.1

Implemented in the production agent loop block. Verified against the code in
`app/agent_runtime/harness.py`, `app/runtime/bootstrap.py`,
`app/runtime/context.py`, `app/api/service.py`, and
`tests/test_agent_loop_production.py`.

### Accepted decisions

- **`AgentHarness` is the canonical production orchestrator.** The bootstrap
  supplies it in `RuntimeContext.harness`, so the production runtime contains one
  orchestration loop, constructed by one composition factory
  (`create_agent_harness`). No second loop and no orchestration singleton inside
  the API service were created.
- **`RunExecutor` remains the execution primitive.** The production flow is
  `AgentHarness -> ExecutionCoordinator`, not `RunExecutor -> decision`. The
  decision layer was not bolted onto the execution primitive.
- **The first slice is `ForgeApiService.run_agent_loop(declaration_id, *,
  purpose_run_id=None)`**, a trusted in-process entry point, not an HTTP route.
  `TaskRunRequest` and the HTTP routes are unchanged.
- **`run_task` does not route through the loop.** Routing the existing HTTP task
  path through the harness would have changed the response contract, the provider
  routing path, and `test_api`/`test_desktop` behaviour, and would have required a
  `TaskSpecification`-shaped response that the contract cannot express. The loop
  therefore has its own trusted entry point, and the ordinary task path keeps its
  previous semantics. Wiring `POST /api/tasks/run` into the loop is left as a
  separate change with its own compatibility decision.
- **Decision is not authority.** A decision answers *what to do next*; authority
  answers *what this run may do at all*. The decision provider receives a
  `DecisionRequest` that carries no command, workspace, tool grant, environment,
  timeout, approval policy, or run scope.
- **The decision selects among pre-authorized actions, never authors one.** The
  harness selects `execution_requests[i]`, which the server-side composition built
  once from an operator declaration, and re-checks the selected command against
  `allowed_execution_commands`. The `ExecutionCoordinator` then re-validates
  independently and remains the only creator of `AuthorizedExecution`.
- **Tool authority is operator-configured.** `ForgeApiService.allowed_tool_ids`
  is set at composition time, intersected with registered tools, and bound into
  the frozen `RunScope`. `TaskRunRequest` gained no field, and `category`,
  `context`, `description`, `task_id`, `provider_name`, or LLM output cannot grant
  a tool. The empty default stays fail-closed.
- **The slice is bounded to one action.** `max_actions=1` and
  `max_execution_attempts=1` narrow the existing policy; they add no authority.
  `max_revision_attempts` stays positive because `DeterministicDecisionProvider`
  treats a zero revision budget as already exhausted and would fail the run
  before it acts.
- **Acceptance is deferred, not fabricated.** The slice reports execution success,
  not task acceptance. Criterion identity is GAP-F and was not invented, so no
  placeholder criterion is presented as real and no acceptance event is emitted.
- **`RUN_VERIFICATION` reads only server-side expectations.** The harness takes
  verification expectations from the request, which the composition supplies. A
  decision has no channel to select a declaration, and the declared-execution
  entry point is not reachable from the decision. With no expectations configured
  the acceptance gate fails closed with `verification_missing`.
- **`EngineeringRunExecutor`, `RevisionLoopExecutor`, `MultiAgentExecutor`, and
  `ProviderReviewer` are not production machinery.** They stay test/smoke
  machinery until a separate decision.
- **Observability reuses the existing store.** The harness gained an optional
  observer that receives exactly the events it already collects - a second
  consumer, never a second event stream - and `ForgeApiService` persists those
  events and a terminal snapshot through the existing `RunStore` writer. No new
  event store was created and the finite run loop's control flow is unchanged.

### Rejected alternatives (with rationale)

- **Routing `POST /api/tasks/run` through the harness in this block.** Rejected:
  it would break the desktop/API response contract and the ordinary task path, and
  the brief requires both to keep working.
- **Building the decision layer into `RunExecutor`.** Rejected by the accepted
  architecture: `RunExecutor` is the execution primitive.
- **Adding `allowed_tool_ids` to `TaskRunRequest`.** Rejected: it would be
  client-controlled tool authority.
- **Deriving tools from `category`, `context`, or the description.** Rejected:
  all are client-supplied, so any of them would let a client grant itself tools.
- **Mapping `RUN_VERIFICATION` onto a declared execution.** Rejected: the decision
  would then select which operator-declared command runs. That would be
  LLM-controlled command selection.
- **Passing a hand-built `execution_requests` list from the loop entry point.**
  Rejected: it would be a second, caller-supplied authority source. The list is
  built once from the operator declaration.

### Consequences and open items

1. **`run_task` is unchanged and still execution-disabled for host processes**
   beyond what the operator declared; the loop is a separate trusted entry point.
   Unifying the two paths is a future decision with its own compatibility impact.
2. **GAP-F (criterion identity) remains open** and is the next architectural
   block; the loop's acceptance stage is deliberately deferred behind it.
3. **GAP-A, GAP-B, and idempotency are unchanged** by this block.
4. **Pre-existing cosmetic defect noticed, not fixed:** `HarnessRequest` declares
   the `run_scope` field annotation twice (`app/agent_runtime/models.py`). The
   duplicate is inert for `dataclasses` (`dataclasses.fields` reports one field)
   and was left untouched as unrelated to this block.

## Production Agent Loop Vertical Slice v0.1 — русская версия

Реализовано в блоке production agent loop. Проверено по коду в
`app/agent_runtime/harness.py`, `app/runtime/bootstrap.py`,
`app/runtime/context.py`, `app/api/service.py` и
`tests/test_agent_loop_production.py`.

### Принятые решения

- **`AgentHarness` — канонический production-оркестратор.** Bootstrap поставляет
  его в `RuntimeContext.harness`, поэтому production-runtime содержит один
  orchestration loop, построенный одной композиционной фабрикой
  (`create_agent_harness`). Второй loop и orchestration-singleton внутри API-сервиса
  не создавались.
- **`RunExecutor` остаётся execution-примитивом.** Production-поток —
  `AgentHarness -> ExecutionCoordinator`, а не `RunExecutor -> decision`. Слой
  решений не приделывался к execution-примитиву.
- **Первый срез — `ForgeApiService.run_agent_loop(declaration_id, *,
  purpose_run_id=None)`**, доверенный in-process вход, а не HTTP-маршрут.
  `TaskRunRequest` и HTTP-маршруты не изменены.
- **`run_task` не идёт через loop.** Маршрутизация существующего HTTP-пути задачи
  через harness изменила бы контракт ответа, путь провайдерской маршрутизации и
  поведение `test_api`/`test_desktop`, а также потребовала бы ответ формы
  `TaskSpecification`, которую контракт выразить не может. Поэтому у loop есть
  собственный доверенный вход, а обычный путь задачи сохраняет прежнюю семантику.
  Подключение `POST /api/tasks/run` к loop оставлено отдельным изменением с
  собственным решением о совместимости.
- **Decision не является authority.** Decision отвечает на вопрос *что делать
  дальше*; authority — на вопрос *что этому run вообще разрешено*. Decision
  provider получает `DecisionRequest`, который не несёт ни команды, ни workspace,
  ни выдачи tools, ни окружения, ни timeout, ни approval-политики, ни run scope.
- **Decision выбирает среди уже авторизованных действий, но не создаёт их.**
  Harness выбирает `execution_requests[i]`, которые server-side композиция
  построила один раз из объявления оператора, и повторно проверяет выбранную
  команду против `allowed_execution_commands`. Затем `ExecutionCoordinator`
  перепроверяет независимо и остаётся единственным создателем
  `AuthorizedExecution`.
- **Tool authority конфигурируется оператором.** `ForgeApiService.allowed_tool_ids`
  задаётся на композиции, пересекается с зарегистрированными tools и связывается в
  замороженный `RunScope`. `TaskRunRequest` не получил новых полей, а `category`,
  `context`, `description`, `task_id`, `provider_name` или вывод LLM не могут
  выдать tool. Пустой default остаётся fail-closed.
- **Срез ограничен одним действием.** `max_actions=1` и
  `max_execution_attempts=1` сужают существующую политику; они не добавляют
  authority. `max_revision_attempts` остаётся положительным, поскольку
  `DeterministicDecisionProvider` читает нулевой бюджет ревизий как уже
  исчерпанный и завалил бы run до совершения действия.
- **Acceptance отложен, а не подделан.** Срез сообщает execution success, а не task
  acceptance. Criterion identity — это GAP-F, и она не выдумывалась, поэтому
  placeholder-критерий не выдаётся за настоящий и acceptance-событие не эмитится.
- **`RUN_VERIFICATION` читает только server-side expectations.** Harness берёт
  verification expectations из запроса, который поставляет композиция. У decision
  нет канала для выбора объявления, и вход объявленного выполнения из decision
  недостижим. Без сконфигурированных expectations acceptance-гейт падает
  fail-closed с `verification_missing`.
- **`EngineeringRunExecutor`, `RevisionLoopExecutor`, `MultiAgentExecutor` и
  `ProviderReviewer` не являются production-машинерией.** Они остаются
  test/smoke-машинерией до отдельного решения.
- **Наблюдаемость переиспользует существующее хранилище.** Harness получил
  опциональный observer, который получает ровно те события, которые harness и так
  собирает — второй потребитель, а не второй поток событий — а `ForgeApiService`
  записывает эти события и терминальный snapshot через существующий writer
  `RunStore`. Новое event-хранилище не создавалось, управляющий поток loop не
  изменён.

### Отклонённые альтернативы (с обоснованием)

- **Маршрутизировать `POST /api/tasks/run` через harness в этом блоке.**
  Отклонено: это сломало бы контракт ответа desktop/API и обычный путь задачи, а
  задание требует, чтобы оба продолжали работать.
- **Встроить слой решений в `RunExecutor`.** Отклонено принятой архитектурой:
  `RunExecutor` — execution-примитив.
- **Добавить `allowed_tool_ids` в `TaskRunRequest`.** Отклонено: это была бы
  клиентски управляемая tool authority.
- **Выводить tools из `category`, `context` или description.** Отклонено: все они
  клиентские, поэтому любой из них позволил бы клиенту выдать себе tools.
- **Отобразить `RUN_VERIFICATION` на объявленное выполнение.** Отклонено: тогда
  decision выбирал бы, какая объявленная оператором команда выполнится. Это было
  бы LLM-управляемым выбором команды.
- **Передавать вручную собранный список `execution_requests` из входа loop.**
  Отклонено: это был бы второй, задаваемый вызывающим источник authority. Список
  строится один раз из объявления оператора.

### Следствия и открытые пункты

1. **`run_task` не изменён** и по-прежнему execution-disabled для host-процессов
  сверх объявленного оператором; loop — отдельный доверенный вход. Объединение
  двух путей — будущее решение с собственным влиянием на совместимость.
2. **GAP-F (criterion identity) остаётся открытым** и является следующим
  архитектурным блоком; стадия acceptance в loop намеренно отложена за ним.
3. **GAP-A, GAP-B и идемпотентность этим блоком не изменены.**
4. **Замечен существующий косметический дефект, не исправлен:** `HarnessRequest`
   объявляет аннотацию поля `run_scope` дважды
   (`app/agent_runtime/models.py`). Дубль инертен для `dataclasses`
   (`dataclasses.fields` сообщает одно поле) и оставлен нетронутым как не
   относящийся к этому блоку.

## Production Criterion, Verification, and Acceptance v0.1

Implemented in the acceptance slice block. Verified against the code in
`app/agent_runtime/acceptance_spec.py`, `app/agent_runtime/frozen_verifier.py`,
`app/agent_runtime/harness.py`, `app/api/service.py`, `app/runtime/bootstrap.py`,
and `tests/test_acceptance_production.py`.

### Accepted decisions

- **`EXECUTION_SUCCESS` is not task acceptance.** A process that exits zero is
  reported as execution success. Task acceptance exists only when the existing
  `AcceptanceGate` returns PASS over verification results produced from frozen
  server-side expectations.
- **Criterion identity is created server-side, in two steps.** `AcceptanceSpec`
  is the operator's definition, declared next to the execution declaration.
  `AcceptanceSpec.bind(run_id, task_id)` freezes it into `RunAcceptanceCriteria`,
  which asserts it belongs to exactly that run and task. Criteria cannot be bound
  to a run they were not composed for and cannot be swapped after the run starts.
- **Criteria come only from operator composition.** `ForgeApiService` takes an
  `acceptance_specs` mapping at construction time, next to `declarations`. There
  is no HTTP field, no task context, no description, no category, no task id, and
  no decision input that can add, remove, or replace a criterion.
- **Absent criteria mean no acceptance.** A declaration without an
  `AcceptanceSpec` is refused; missing criteria are never a permissive default.
- **Every criterion must be checkable.** A criterion without an expectation, an
  expectation for an unknown criterion, a duplicate criterion id, an empty
  criterion set, and a malformed digest are rejected when the spec is built, so a
  criterion whose acceptance could only ever be `verification_missing` cannot
  exist.
- **Verification is deterministic and non-authoritative.** `FrozenCriteriaVerifier`
  evaluates workspace facts through the existing `WorkspaceVerifier`: a relative
  path must exist or be absent, optionally matching a SHA-256. It has no
  executable, argv, environment, timeout, profile, or scope, so a criterion can
  never become command authority.
- **Errors are never passes.** A verifier that cannot complete its work reports
  `ERROR`; the gate then rejects it as `verification_failed`. An empty expectation
  set yields no results and no acceptance verdict, which is recorded as
  `not_evaluated` rather than PASS.
- **The verdict belongs to the existing gate.** `AcceptanceGate` decides
  PASS/FAIL from criteria and verification results. The entry point consumes
  `final_acceptance` and does not evaluate acceptance itself.
- **One verification implementation.** The harness's verification stage was
  extracted into `AgentHarness.run_verification`, which the loop action and the
  production entry point both call. No second `AcceptanceGate` or second
  `VerificationEvaluator` was created.
- **Verification runs only after an execution result exists.** An execution
  failure produces no verification and no acceptance verdict, so a failure can
  never be masked by a verdict.
- **The slice is one execution plus its verification.** `ACCEPTANCE_LOOP_POLICY`
  keeps `max_execution_attempts=1`; the action budget admits the mandatory
  verification stage.
- **The acceptance slice runs without workspace COPY-staging.** The criterion
  asserts a fact about the workspace the action was told to work in, and a scratch
  copy would make that fact unverifiable. Only the staging mode changes:
  `RunScope`, `ExecutionPolicy`, approval, and the coordinator sentinel are
  untouched.
- **The public `success` field is not redefined.** `success` means the action ran
  and its verification passed; the acceptance verdict is recorded as
  `acceptance_status` in the durable run record. Changing the public contract is a
  separate decision.

### Rejected alternatives (with rationale)

- **Letting a decision select the criterion or the verifier.** Rejected: the
  decision would then control what counts as done, which is acceptance authority
  flowing from a replaceable component.
- **Deriving a criterion from the command.** Rejected: a criterion created from
  what ran can only restate that it ran, which is exactly the confusion this block
  removes.
- **Accepting on `EXECUTION_SUCCESS` when no expectation is configured.**
  Rejected: that is the artificial PASS the brief forbids.
- **Adding `criterion_id` or criteria to `TaskRunRequest`.** Rejected: it would let
  a client choose what counts as done.
- **The file-based `TestVerificationAdapter` path as the production verifier.**
  Kept test-only; production verification already has a safe deterministic
  mechanism in `WorkspaceVerifier`, and the adapter's intent types are not needed
  to express a workspace fact.

### Consequences and open items

1. **GAP-F is only half closed.** Criterion identity now works for
   operator-declared criteria end to end, including a real PASS/FAIL verdict in the
   durable history. Deriving criteria from a `TaskSpecification` or a task request
   still has no trusted server-side source and remains an open architectural
   decision; it is not closed here.
2. **Acceptance is not exposed on the public contract.** Consumers read
   `acceptance_status` from the run record. Redefining `TaskRunRequest` or
   `TaskRunResponse` is a separate decision.
3. **One criterion shape in this slice.** Only `exists` / `sha256` expectations are
   used. Richer criteria (test-suite outcomes, build artifacts) would need their
   own deterministic verifier and their own decision.
4. **GAP-A, GAP-B, and idempotency are unchanged** by this block.

## Production Criterion, Verification, and Acceptance v0.1 — русская версия

Реализовано в блоке acceptance slice. Проверено по коду в
`app/agent_runtime/acceptance_spec.py`, `app/agent_runtime/frozen_verifier.py`,
`app/agent_runtime/harness.py`, `app/api/service.py`, `app/runtime/bootstrap.py`
и `tests/test_acceptance_production.py`.

### Принятые решения

- **`EXECUTION_SUCCESS` — не task acceptance.** Процесс с нулевым кодом
  сообщается как execution success. Task acceptance существует только тогда,
  когда существующий `AcceptanceGate` вернул PASS по результатам verification,
  полученным из замороженных server-side expectations.
- **Criterion identity создаётся server-side, в два шага.** `AcceptanceSpec` —
  определение оператора, объявляемое рядом с execution-объявлением.
  `AcceptanceSpec.bind(run_id, task_id)` замораживает его в
  `RunAcceptanceCriteria`, который проверяет принадлежность ровно этому run и
  task. Критерии нельзя связать с run, для которого они не объявлялись, и нельзя
  подменить после старта run.
- **Критерии приходят только из композиции оператора.** `ForgeApiService`
  принимает mapping `acceptance_specs` на композиции, рядом с `declarations`. Нет
  ни HTTP-поля, ни task context, ни description, ни category, ни task id, ни
  входа decision, которые могли бы добавить, удалить или заменить критерий.
- **Нет критериев — нет acceptance.** Объявление без `AcceptanceSpec`
  отвергается; отсутствие критериев никогда не является разрешающим значением по
  умолчанию.
- **Каждый критерий обязан быть проверяемым.** Критерий без expectation,
  expectation для неизвестного критерия, дубликат criterion id, пустой набор
  критериев и некорректный digest отвергаются при построении spec, поэтому
  критерий, у которого acceptance могло быть только `verification_missing`,
  существовать не может.
- **Verification детерминирован и не несёт authority.**
  `FrozenCriteriaVerifier` проверяет факты workspace через существующий
  `WorkspaceVerifier`: относительный путь должен существовать или отсутствовать,
  опционально совпадая с SHA-256. У него нет executable, argv, окружения,
  timeout, профиля или scope, поэтому критерий никогда не может стать command
  authority.
- **Ошибки никогда не становятся прохождением.** Verifier, который не может
  выполнить работу, сообщает `ERROR`; гейт затем отвергает это как
  `verification_failed`. Пустой набор expectations даёт ноль результатов и
  отсутствие вердикта acceptance, что записывается как `not_evaluated`, а не PASS.
- **Вердикт принадлежит существующему гейту.** PASS/FAIL решает `AcceptanceGate`
  по критериям и результатам verification. Вход потребляет `final_acceptance` и не
  оценивает acceptance сам.
- **Одна реализация verification.** Стадия verification в harness вынесена в
  `AgentHarness.run_verification`, которую вызывают и действие loop, и
  production-вход. Второй `AcceptanceGate` или второй `VerificationEvaluator` не
  создавались.
- **Verification выполняется только при наличии результата исполнения.** Сбой
  исполнения не даёт ни verification, ни вердикта acceptance, поэтому сбой никогда
  не маскируется вердиктом.
- **Срез — одно исполнение плюс его verification.** `ACCEPTANCE_LOOP_POLICY`
  сохраняет `max_execution_attempts=1`; бюджет действий вмещает обязательную
  стадию verification.
- **Срез acceptance работает без COPY-разворачивания workspace.** Критерий
  утверждает факт о workspace, в котором действию было сказано работать, и
  scratch-копия сделала бы этот факт непроверяемым. Меняется только режим
  разворачивания: `RunScope`, `ExecutionPolicy`, approval и sentinel координатора
  не тронуты.
- **Публичное поле `success` не переопределяется.** `success` означает, что
  действие выполнилось и его verification прошёл; вердикт acceptance записывается
  как `acceptance_status` в durable run record. Изменение публичного контракта —
  отдельное решение.

### Отклонённые альтернативы (с обоснованием)

- **Позволить decision выбирать критерий или verifier.** Отклонено: тогда decision
  управлял бы тем, что считается выполненным, то есть acceptance authority
  текла бы из заменяемого компонента.
- **Выводить критерий из команды.** Отклонено: критерий, созданный из того, что
  выполнялось, может лишь повторить, что оно выполнялось, — а это ровно та
  путаница, которую устраняет этот блок.
- **Принимать по `EXECUTION_SUCCESS`, когда expectation не сконфигурирован.**
  Отклонено: это и есть искусственный PASS, запрещённый заданием.
- **Добавить `criterion_id` или критерии в `TaskRunRequest`.** Отклонено: это
  позволило бы клиенту выбирать, что считается выполненным.
- **Путь файлового `TestVerificationAdapter` как production-verifier.** Оставлен
  test-only; у production verification уже есть безопасный детерминированный
  механизм в `WorkspaceVerifier`, а intent-типы адаптера не нужны для выражения
  факта о workspace.

### Следствия и открытые пункты

1. **GAP-F закрыт лишь наполовину.** Criterion identity теперь работает для
   объявленных оператором критериев end-to-end, включая реальный вердикт PASS/FAIL
   в durable history. Вывод критериев из `TaskSpecification` или запроса задачи
   по-прежнему не имеет доверенного server-side источника и остаётся открытым
   архитектурным решением; здесь он не закрывается.
2. **Acceptance не экспортируется в публичный контракт.** Потребители читают
   `acceptance_status` из run record. Переопределение `TaskRunRequest` или
   `TaskRunResponse` — отдельное решение.
3. **Одна форма критерия в этом срезе.** Используются только expectations
   `exists` / `sha256`. Более богатые критерии (результаты тестовых наборов,
   артефакты сборки) потребовали бы собственного детерминированного verifier и
   собственного решения.
4. **GAP-A, GAP-B и идемпотентность этим блоком не изменены.**

## Production Project Discovery v0.1

Implemented in the project discovery block. Verified against the code in
`app/agent_runtime/project_discovery.py`, `app/agent_runtime/harness.py`,
`app/api/service.py`, `app/runtime/bootstrap.py`, `app/orchestrator/models.py`,
and `tests/test_project_discovery_production.py`.

### Accepted decisions

- **Discovery is a server-side observation layer inside the existing loop.** It
  runs as a stage of `AgentHarness.run`, before the first context assembly.
  `AgentHarness` stays the only production orchestration loop; no
  `ProjectAgent`, `DiscoveryAgent`, `ContextAgent`, second coordinator, or second
  loop was created.
- **The existing bounded scanner is the trust boundary.** `BoundedProjectScanner`
  already enforces file count, per-file size, total bytes, ignored directories,
  symlink handling, secret-file exclusion, and binary handling. Those policies are
  reused verbatim; nothing is re-implemented and there is no second discovery
  system.
- **The existing snapshot model is reused.** `UnderstandingSnapshotter` produces
  the frozen `UnderstandingSnapshot` with its deterministic
  `workspace_fingerprint`. No new snapshot type, database, or event store was
  introduced.
- **The root is composition-owned.** `ProjectDiscovery.observe` accepts only a
  `Workspace`, and the service supplies its own through `HarnessRequest`. No task
  request, decision, or LLM can choose a root, choose a scanner, change limits, or
  disable a boundary. `TaskRunRequest` gained no field.
- **Discovery is bounded, not a filesystem dump.** A snapshot carries paths,
  fingerprints, bounded structural facts, manifests, topology, and warnings. The
  context receives only the bounded `summarize_snapshot` counts, not the file
  inventory, so secret paths never reach the decision.
- **Discovery is not authority.** A snapshot cannot widen a `RunScope` or add a
  command, tool, profile, environment value, or timeout; the decision provider
  receives no scope, workspace, approval policy, scanner, or filesystem handle.
  Discovery never executes anything: no subprocess, no shell, no coordinator, no
  adapter.
- **One run, one snapshot.** The snapshot is produced fresh at the start of each
  run, is immutable, and is never reused across runs. A later run observes the
  current workspace; unchanged content yields the same bounded fingerprint.
- **Failure fails closed.** A discovery failure yields no snapshot, no context
  item, and no decision: the run terminates as `FAILED` with reason
  `project_discovery_failed`. A raising discovery implementation is classified as
  a failure rather than aborting the loop with an untyped exception. There is no
  silent fallback to unrestricted filesystem access, and a partial observation is
  never presented as complete.
- **Two purpose-named events, sanitized metadata.** `PROJECT_DISCOVERY_STARTED`
  and `PROJECT_DISCOVERY_COMPLETED` were added. They record run id, task id,
  bounded counts, workspace fingerprint, snapshot id, duration, and a safe failure
  category. `SNAPSHOT_CREATED` was deliberately not reused because it already
  means a project file snapshot for change tracking, which is a different concept.
- **Every event carries one identity.** Harness events take their task id from the
  task specification or from server-side request metadata, so discovery, decision,
  execution, verification, and acceptance can never be attributed to different
  runs or tasks.

### Rejected alternatives (with rationale)

- **Passing a snapshot in from the caller.** Rejected: it would let the caller
  choose what the decision observes, and a stale or forged snapshot would be
  indistinguishable from a fresh one.
- **Letting the decision provider or LLM drive discovery.** Rejected: the decision
  is a replaceable component and must not control the observation boundary.
- **A new discovery scanner or snapshot type.** Rejected: the bounded scanner and
  the frozen snapshot already exist and are tested.
- **Reusing `SNAPSHOT_CREATED`.** Rejected: that event belongs to the project
  file-snapshot mechanism used for change tracking.
- **Continuing the run after a discovery failure with an empty snapshot.**
  Rejected: an unusable observation presented as a complete one is exactly the
  silent downgrade the block forbids.
- **Freezing or locking the filesystem between discovery and execution.**
  Deferred: the snapshot describes the workspace as of discovery, and execution
  and verification keep their existing authority and semantics. Locking is a
  separate concern.

### Consequences and open items

1. **Snapshot consistency is point-in-time only.** The workspace may change after
   discovery. No locking, no distributed cache, and no resume were added; the
   snapshot documents the workspace at the moment it was taken.
2. **`max_depth` is not bounded by the existing scanner.** It bounds file count,
   per-file size, and total bytes, but not directory depth. Adding a depth bound
   would be a change to the scanner's own contract and is left as a separate item.
3. **One observation per run.** Discovery runs once, before the first context
   assembly; there is no re-observation mid-run.
4. **GAP-A, GAP-B, GAP-F (task-driven criteria), planning, revision loop, tool
   execution through the harness, memory, knowledge, reviewer, and
   idempotency/resume are unchanged** by this block.

## Production Project Discovery v0.1 — русская версия

Реализовано в блоке project discovery. Проверено по коду в
`app/agent_runtime/project_discovery.py`, `app/agent_runtime/harness.py`,
`app/api/service.py`, `app/runtime/bootstrap.py`, `app/orchestrator/models.py`
и `tests/test_project_discovery_production.py`.

### Принятые решения

- **Discovery — server-side слой наблюдения внутри существующего loop.** Он
  выполняется как стадия `AgentHarness.run`, до первой сборки контекста.
  `AgentHarness` остаётся единственным production-orchestration loop; ни
  `ProjectAgent`, ни `DiscoveryAgent`, ни `ContextAgent`, ни второй координатор,
  ни второй loop не создавались.
- **Существующий bounded scanner — это trust boundary.** `BoundedProjectScanner`
  уже обеспечивает лимит файлов, размер файла, общий объём байт, игнорируемые
  каталоги, обработку symlink, исключение секретных файлов и обработку бинарных
  файлов. Эти политики переиспользованы как есть; ничего не переписывалось, второй
  системы discovery нет.
- **Существующая модель snapshot переиспользована.** `UnderstandingSnapshotter`
  создаёт замороженный `UnderstandingSnapshot` с детерминированным
  `workspace_fingerprint`. Новый тип snapshot, база данных или event store не
  вводились.
- **Root принадлежит композиции.** `ProjectDiscovery.observe` принимает только
  `Workspace`, а сервис передаёт собственный через `HarnessRequest`. Ни запрос
  задачи, ни decision, ни LLM не могут выбрать root, выбрать scanner, изменить
  лимиты или отключить границу. `TaskRunRequest` не получил новых полей.
- **Discovery ограничен, а не дамп файловой системы.** Snapshot несёт пути,
  отпечатки, ограниченные структурные факты, манифесты, топологию и
  предупреждения. В контекст попадают только ограниченные счётчики
  `summarize_snapshot`, а не инвентарь файлов, поэтому пути секретов не достигают
  decision.
- **Discovery — не authority.** Snapshot не может расширить `RunScope` или
  добавить команду, tool, профиль, значение окружения или timeout; decision
  provider не получает ни scope, ни workspace, ни approval-политику, ни scanner,
  ни filesystem handle. Discovery ничего не исполняет: ни subprocess, ни shell, ни
  координатор, ни adapter.
- **Один run — один snapshot.** Snapshot создаётся заново в начале каждого run,
  неизменяем и никогда не переиспользуется между run. Следующий run наблюдает
  текущий workspace; неизменное содержимое даёт тот же bounded fingerprint.
- **Сбой — fail closed.** Сбой discovery не даёт ни snapshot, ни элемента
  контекста, ни решения: run завершается как `FAILED` с причиной
  `project_discovery_failed`. Реализация discovery, бросающая исключение,
  классифицируется как сбой, а не прерывает loop нетипизированным исключением.
  Молчаливого отката к неограниченному доступу к файловой системе нет, и частичное
  наблюдение никогда не выдаётся за полное.
- **Два события с целевыми именами и санитизированными метаданными.**
  Добавлены `PROJECT_DISCOVERY_STARTED` и `PROJECT_DISCOVERY_COMPLETED`. Они
  записывают run id, task id, ограниченные счётчики, отпечаток workspace, snapshot
  id, длительность и безопасную категорию сбоя. `SNAPSHOT_CREATED` намеренно не
  переиспользован, поскольку он уже означает project file snapshot для
  отслеживания изменений — это другое понятие.
- **Каждое событие несёт одну идентичность.** События harness берут task id из
  task specification или из server-side метаданных запроса, поэтому discovery,
  decision, execution, verification и acceptance не могут быть отнесены к разным
  run или task.

### Отклонённые альтернативы (с обоснованием)

- **Передавать snapshot от вызывающего.** Отклонено: это позволило бы
  вызывающему выбирать, что наблюдает decision, а устаревший или подделанный
  snapshot был бы неотличим от свежего.
- **Позволять decision provider или LLM управлять discovery.** Отклонено:
  decision — заменяемый компонент и не должен управлять границей наблюдения.
- **Новый scanner discovery или новый тип snapshot.** Отклонено: bounded scanner
  и замороженный snapshot уже существуют и покрыты тестами.
- **Переиспользовать `SNAPSHOT_CREATED`.** Отклонено: это событие принадлежит
  механизму project file snapshot для отслеживания изменений.
- **Продолжать run после сбоя discovery с пустым snapshot.** Отклонено:
  непригодное наблюдение, выданное за полное, — ровно тот молчаливый downgrade,
  который блок запрещает.
- **Замораживать или блокировать файловую систему между discovery и
  исполнением.** Отложено: snapshot описывает workspace на момент discovery, а
  исполнение и verification сохраняют существующие authority и семантику.
  Блокировка — отдельная задача.

### Следствия и открытые пункты

1. **Согласованность snapshot — только на момент времени.** Workspace может
   измениться после discovery. Блокировки, распределённый кэш и resume не
   добавлялись; snapshot документирует workspace в момент его снятия.
2. **`max_depth` существующим scanner'ом не ограничен.** Он ограничивает число
   файлов, размер файла и общий объём байт, но не глубину каталогов. Добавление
   ограничения глубины было бы изменением контракта самого scanner'а и оставлено
   отдельным пунктом.
3. **Одно наблюдение на run.** Discovery выполняется один раз, до первой сборки
   контекста; повторного наблюдения в середине run нет.
4. **GAP-A, GAP-B, GAP-F (task-driven критерии), planning, revision loop,
   исполнение tools через harness, memory, knowledge, reviewer и
   idempotency/resume этим блоком не изменены.**
