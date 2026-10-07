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
