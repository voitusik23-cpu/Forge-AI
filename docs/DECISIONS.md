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

## Production Planning v0.1

Implemented in the planning block. Verified against the code in
`app/planning/plan.py`, `app/planning/planner.py`, `app/planning/validation.py`,
`app/agent_runtime/harness.py`, `app/execution/declaration.py`,
`app/api/service.py`, `app/runtime/bootstrap.py`, and
`tests/test_planning_production.py`.

### Accepted decisions

- **Planning is a declarative stage inside the existing loop.** It runs in
  `AgentHarness.run` between the observation and the first decision. There is no
  `PlanningServiceLoop`, `PlanCoordinator`, `PlannerAgentLoop`, or second harness:
  `AgentHarness` remains the only production orchestration loop.
- **The existing deterministic `Planner` was extended, not duplicated.**
  `Planner.plan_for_run(goal, run_id, task_id)` reuses `create_plan` and the
  existing templates and returns a run-bound `ExecutionPlan`. No second
  Plan/TaskPlan abstraction was introduced.
- **A plan is an intention, not a capability.** `PlanStepType` is a closed
  taxonomy of `OBSERVE`, `MODIFY`, `VERIFY`, `ACCEPT`. `MODIFY` means the intent to
  change the project; it does not grant a write.
- **No command surface.** `ExecutionPlan` and `PlanStep` carry no argv,
  executable, shell string, environment, working directory, timeout, network flag,
  or tool grant, and forbid authority- or credential-shaped metadata keys at
  construction. A plan cannot become an `AuthorizedExecution`, and nothing in the
  planning modules can reach the coordinator, the adapter, a subprocess, or the
  filesystem.
- **Validation is mandatory and fail-closed.** `validate_plan` /
  `require_valid_plan` check the run and task binding, structural soundness,
  unique step ids, resolvable dependencies, no self-dependency, an acyclic graph
  with a deterministic topological order, only known step types, and authority
  isolation. An invalid plan is rejected outright; there is no silent repair.
- **The harness validates what a planner returns.** A planner's output is
  accepted only if `require_valid_plan` accepts it as this run's plan; otherwise
  the stage is reported as failed and no plan reaches the loop.
- **The goal is operator-supplied.** `ExecutionDeclaration` gained an `intent`
  field - descriptive text naming what the declared execution is meant to achieve.
  The service delivers it as a `TaskSpecification`. With no declared intent there
  is no goal, so planning is skipped rather than given a fabricated one, and the
  task id is never used as a goal because it is an identity, not an intention.
- **One run, one plan, one identity.** The plan is bound to `run_id` and
  `task_id`, and its fingerprint covers that binding, so a plan cannot be replayed
  against another run. Planning runs once, before the first decision.
- **Planning does not touch decision authority.** The plan reaches the decision as
  bounded planning metadata. The `DecisionRequest` still carries no scope,
  workspace, approval, command set, or execution requests, and the plan grants the
  decision nothing it did not already have.
- **Two events, sanitized metadata.** `PLANNING_STARTED` and
  `PLANNING_COMPLETED` record the run id, task id, status, step count, step types,
  dependency count, plan fingerprint, duration, and a safe failure category. No
  command string, environment value, credential, or raw planner output is stored.
- **Deferred on purpose.** Dynamic replanning, plan revision history, retries, and
  parallel execution are not implemented. Revision belongs to a Revision Loop
  block with its own decision.

### Rejected alternatives (with rationale)

- **A new plan model beside `ProjectPlan`.** Rejected: the existing deterministic
  planner and its models already exist; the production need was an immutable,
  run-bound, validated plan, which is an extension rather than a parallel system.
- **Putting argv or a shell string in a plan step.** Rejected: that would make a
  plan a command channel and let planning reach execution authority.
- **Letting the planner read the workspace.** Rejected: observation already
  happens in the discovery stage, and the planner works from the goal plus the
  bounded observation.
- **Letting a planner skip validation.** Rejected: the harness validates every
  returned plan, so a planner cannot inject an unusable plan.
- **Deriving the goal from the task id or the declaration id.** Rejected: both are
  identities, and planning on an identity produces a meaningless plan.
- **Repairing an invalid plan.** Rejected: silently fixing a plan that carries
  authority-shaped data is exactly the failure the validation boundary exists to
  prevent.

### Consequences and open items

1. **No replanning.** A run plans once. Reacting to new information mid-run is a
   Revision Loop concern and stays deferred.
2. **Sequential only.** The graph is a deterministic DAG with a single
   topological order; parallel execution and retries are not implemented.
3. **The templates are the planner's scope.** Planning quality is bounded by the
   existing deterministic templates; richer planning needs its own decision.
4. **GAP-A, GAP-B, GAP-F, directory-depth, snapshot consistency, tool execution
   through the harness, memory, knowledge, reviewer, idempotency, and resume are
   unchanged** by this block.

## Production Planning v0.1 — русская версия

Реализовано в блоке planning. Проверено по коду в `app/planning/plan.py`,
`app/planning/planner.py`, `app/planning/validation.py`,
`app/agent_runtime/harness.py`, `app/execution/declaration.py`,
`app/api/service.py`, `app/runtime/bootstrap.py` и
`tests/test_planning_production.py`.

### Принятые решения

- **Планирование — декларативная стадия внутри существующего loop.** Оно
  выполняется в `AgentHarness.run` между наблюдением и первым решением.
  `PlanningServiceLoop`, `PlanCoordinator`, `PlannerAgentLoop` или второй harness не
  создавались: `AgentHarness` остаётся единственным production-orchestration loop.
- **Существующий детерминированный `Planner` расширен, а не продублирован.**
  `Planner.plan_for_run(goal, run_id, task_id)` переиспользует `create_plan` и
  существующие шаблоны и возвращает привязанный к run `ExecutionPlan`. Вторая
  абстракция Plan/TaskPlan не вводилась.
- **План — намерение, а не возможность.** `PlanStepType` — закрытая таксономия
  `OBSERVE`, `MODIFY`, `VERIFY`, `ACCEPT`. `MODIFY` означает намерение изменить
  проект; права на запись он не даёт.
- **Нет командной поверхности.** `ExecutionPlan` и `PlanStep` не несут ни argv, ни
  executable, ни shell-строки, ни окружения, ни рабочей директории, ни timeout, ни
  сетевого флага, ни выдачи tool, а на композиции запрещают ключи метаданных,
  похожие на authority или credentials. План не может стать
  `AuthorizedExecution`, и ничто в модулях планирования не может достичь
  координатора, adapter'а, subprocess или файловой системы.
- **Валидация обязательна и fail-closed.** `validate_plan` / `require_valid_plan`
  проверяют привязку к run и task, структурную корректность, уникальность step id,
  разрешимость зависимостей, отсутствие self-dependency, ацикличность с
  детерминированным топологическим порядком, только известные типы шагов и
  изоляцию authority. Невалидный план отвергается сразу; молчаливой починки нет.
- **Harness валидирует то, что вернул планировщик.** Результат планировщика
  принимается только если `require_valid_plan` принимает его как план этого run;
  иначе стадия сообщается как неуспешная, и ни один план не достигает loop.
- **Цель задаёт оператор.** `ExecutionDeclaration` получил поле `intent` —
  описательный текст, называющий, что объявленное выполнение должно достичь.
  Сервис доставляет его как `TaskSpecification`. Без объявленного intent цели нет,
  поэтому планирование пропускается, а не получает выдуманную цель, и task id
  никогда не используется как цель, поскольку это идентичность, а не намерение.
- **Один run — один план — одна идентичность.** План привязан к `run_id` и
  `task_id`, и его fingerprint покрывает эту привязку, поэтому план нельзя
  переиграть на другом run. Планирование выполняется один раз, до первого решения.
- **Планирование не затрагивает authority решения.** План достигает решения как
  ограниченные planning-метаданные. `DecisionRequest` по-прежнему не несёт ни
  scope, ни workspace, ни approval, ни набора команд, ни execution requests, и план
  не даёт решению ничего, чего у него не было.
- **Два события, санитизированные метаданные.** `PLANNING_STARTED` и
  `PLANNING_COMPLETED` записывают run id, task id, статус, число шагов, типы шагов,
  число зависимостей, fingerprint плана, длительность и безопасную категорию сбоя.
  Ни командная строка, ни значение окружения, ни credentials, ни сырой вывод
  планировщика не сохраняются.
- **Отложено намеренно.** Динамическое перепланирование, история ревизий плана,
  retries и параллельное исполнение не реализованы. Ревизия относится к блоку
  Revision Loop с собственным решением.

### Отклонённые альтернативы (с обоснованием)

- **Новая модель плана рядом с `ProjectPlan`.** Отклонено: существующий
  детерминированный планировщик и его модели уже есть; production-потребностью был
  неизменяемый, привязанный к run и провалидированный план, а это расширение, а не
  параллельная система.
- **Помещать argv или shell-строку в шаг плана.** Отклонено: это превратило бы
  план в командный канал и позволило бы планированию достичь execution authority.
- **Позволять планировщику читать workspace.** Отклонено: наблюдение уже
  происходит на стадии discovery, а планировщик работает от цели и ограниченного
  наблюдения.
- **Позволять планировщику пропускать валидацию.** Отклонено: harness валидирует
  каждый возвращённый план, поэтому планировщик не может внедрить непригодный план.
- **Выводить цель из task id или declaration id.** Отклонено: и то и другое —
  идентичности, и планирование по идентичности даёт бессмысленный план.
- **Починить невалидный план.** Отклонено: молчаливое исправление плана, несущего
  данные в форме authority, — ровно тот сбой, который обязана предотвращать
  граница валидации.

### Следствия и открытые пункты

1. **Нет перепланирования.** Run планируется один раз. Реакция на новую информацию
   в середине run — забота Revision Loop и остаётся отложенной.
2. **Только последовательно.** Граф — детерминированный DAG с единственным
   топологическим порядком; параллельное исполнение и retries не реализованы.
3. **Область планировщика — это шаблоны.** Качество планирования ограничено
   существующими детерминированными шаблонами; более богатое планирование требует
   своего решения.
4. **GAP-A, GAP-B, GAP-F, directory-depth, snapshot consistency, исполнение tools
   через harness, memory, knowledge, reviewer, idempotency и resume этим блоком не
   изменены.**

## Production Revision Loop v0.1

Implemented in the revision block. Verified against the code in
`app/agent_runtime/revision_decision.py`, `app/agent_runtime/revision_policy.py`,
`app/agent_runtime/harness.py`, `app/runtime/bootstrap.py`, and
`tests/test_revision_loop_production.py`.

### Accepted decisions

- **The revision loop lives inside `AgentHarness`.** There is no
  `RevisionAgentLoop`, `RevisionCoordinator`, `PlannerAgentLoop`, or second
  harness. One `AgentHarness.run` call owns the whole run, including revisions.
- **The trigger is objective.** `evaluate_revision` allows a revision only when a
  real verification outcome exists, the failure is actionable, budget remains,
  and a current plan exists to replace. A planner's or a decision provider's
  wish for another step is never sufficient.
- **The classification is a closed taxonomy and it decides actionability.**
  `ACTIONABLE_CATEGORIES` is `{VERIFICATION_FAILED, ACCEPTANCE_FAILED,
  EXECUTION_FAILED}`; `SECURITY_FAILURE`, `VALIDATION_FAILED`,
  `NON_ACTIONABLE_FAILURE`, and `NO_PROGRESS` are terminal and disjoint from it.
  A security, authority, validation, or no-progress failure ends the run.
- **The budget is server-side.** `RevisionBudget` is immutable, rejects negative
  and non-integer values, and is never read from a task request, a plan, an LLM,
  or a decision. With no explicit budget the harness derives it from its own
  `AgentHarnessPolicy.max_revision_attempts`, so the loop is bounded either way.
- **Revision numbers are semantic and monotonic.** Revision 0 is the initial
  attempt and revision 1 is the first revision. The number is computed
  server-side and travels with `run_id` and `task_id`.
- **Every revision is a new immutable plan.** The old plan is never mutated. The
  same existing `Planner` is asked for a plan against a bounded revision goal, and
  that plan passes the same `require_valid_plan` validation as the initial plan.
- **No blind retry, detected structurally.** `plan_structure_signature` captures a
  plan's ordered intentions. A revision that resolves to the same shape as the
  plan it replaces - or to an already-superseded shape - terminates the run as
  `revision_no_progress`, because a new identity over the same intentions is still
  the same attempt.
- **No self-approval and no shortcut.** A revision re-enters plan validation, the
  decision, the harness's own authorization step, `RunScope`,
  `ExecutionCoordinator`, `AuthorizedExecution`, and approval semantics.
- **One run and one task for the whole loop.** `run_id` and `task_id` never
  change, so the run store, acceptance, observability, and the final outcome stay
  coherent. Cross-run and cross-task plans and decisions are rejected.
- **Acceptance stays terminal and independent.** A passed acceptance ends the run
  and no revision is attempted. A revision cannot add, drop, replace, weaken, or
  self-satisfy a criterion, and it never fabricates an acceptance verdict.
- **A refused revision is a bounded refusal, not a run failure.** When a decision
  asks for a revision that eligibility refuses, the refusal is recorded as a
  denied action and the loop continues until one of its configured bounds ends it.
  Nothing is executed and no plan is produced.
- **Two events with sanitized metadata.** `REVISION_STARTED` and
  `REVISION_COMPLETED` record identities, the revision number, bounded plan
  identity, status, failure category, actionability, remaining budget, and the
  previous and new plan fingerprints. No stdout, stderr, environment, credential,
  or raw model output.

### Rejected alternatives (with rationale)

- **Honouring every `REQUEST_REVISION` decision.** Rejected: it makes the model's
  preference the trigger, which is exactly the subjective basis the boundary must
  exclude.
- **Retrying the same action until it passes.** Rejected: that is a blind retry,
  not a revision, and it converts a failed verification into an unbounded loop.
- **Treating a security or authority denial as retryable.** Rejected: a run must
  never attempt to work around a boundary it was denied.
- **Letting a revision mutate the existing plan.** Rejected: it would let a
  revision reuse a trusted identity for untrusted content.
- **Putting the revision budget in the task request or the plan.** Rejected: it
  makes the bound client-controlled and defeats the purpose of bounding the loop.
- **Letting a revision bypass approval or execute directly.** Rejected: that would
  give the planning layer execution authority through the back door.
- **Widening the harness's existing action/iteration bounds.** Rejected: the
  existing policy bounds already cap the loop, and reusing them keeps one source
  of truth.

### Consequences and open items

1. **The deterministic planner makes no-progress the common outcome.** The
   existing templates cannot invent different intentions, so a revision over the
   same goal currently resolves to the same shape and terminates as
   `revision_no_progress`. Genuinely different revisions need a planner that can
   produce a different intention set; that is deferred.
2. **The `run_agent_loop` path cannot trigger a revision today.** Its verification
   observations are `not_evaluated` because criterion identity on that path is
   GAP-F, so no objective verification outcome exists to react to. An objective
   revision trigger currently requires the operator-declared criteria of the
   accepted-task path.
3. **No revision history.** Only the previous and new plan fingerprints are
   recorded; a full revision history is deferred.
4. **GAP-A, GAP-B, GAP-F, directory-depth, snapshot consistency, parallel and
   distributed execution, richer retry policies, tool execution through the
   harness, memory, knowledge, reviewer, idempotency, and resume are unchanged**
   by this block.

## Production Revision Loop v0.1 — русская версия

Реализовано в блоке revision. Проверено по коду в
`app/agent_runtime/revision_decision.py`, `app/agent_runtime/revision_policy.py`,
`app/agent_runtime/harness.py`, `app/runtime/bootstrap.py` и
`tests/test_revision_loop_production.py`.

### Принятые решения

- **Revision loop живёт внутри `AgentHarness`.** `RevisionAgentLoop`,
  `RevisionCoordinator`, `PlannerAgentLoop` или второй harness не создавались. Один
  вызов `AgentHarness.run` владеет всем run, включая revisions.
- **Триггер объективен.** `evaluate_revision` разрешает revision лишь когда есть
  реальный вердикт verification, сбой actionable, остался бюджет и есть текущий план
  для замены. Желание планировщика или провайдера решений получить ещё один шаг
  никогда не достаточно.
- **Классификация — закрытая таксономия, и она определяет actionable.**
  `ACTIONABLE_CATEGORIES` — это `{VERIFICATION_FAILED, ACCEPTANCE_FAILED,
  EXECUTION_FAILED}`; `SECURITY_FAILURE`, `VALIDATION_FAILED`,
  `NON_ACTIONABLE_FAILURE` и `NO_PROGRESS` терминальны и не пересекаются с ней.
  Сбой security, authority, validation или no-progress завершает run.
- **Бюджет на стороне сервера.** `RevisionBudget` неизменяем, отвергает
  отрицательные и нецелые значения и никогда не читается из task request, плана, LLM
  или решения. Без явного бюджета harness выводит его из собственного
  `AgentHarnessPolicy.max_revision_attempts`, поэтому loop ограничен в любом случае.
- **Номера revision семантичны и монотонны.** Revision 0 — исходная попытка,
  revision 1 — первая revision. Номер вычисляется на сервере и связан с `run_id` и
  `task_id`.
- **Каждая revision — новый неизменяемый план.** Старый план никогда не мутируется.
  Тот же существующий `Planner` получает план под ограниченную revision goal, и этот
  план проходит ту же валидацию `require_valid_plan`, что и исходный.
- **Никакого blind retry, детекция структурная.** `plan_structure_signature`
  фиксирует упорядоченные намерения плана. Revision, сводящаяся к той же форме, что и
  заменяемый план — или к уже заменённой форме — завершает run как
  `revision_no_progress`, поскольку новая идентичность при тех же намерениях всё
  равно остаётся той же попыткой.
- **Никакого self-approval и никаких сокращений.** Revision заново проходит
  валидацию плана, решение, собственный шаг авторизации harness, `RunScope`,
  `ExecutionCoordinator`, `AuthorizedExecution` и approval semantics.
- **Один run и один task на весь loop.** `run_id` и `task_id` не меняются, поэтому
  run store, acceptance, наблюдаемость и итоговый результат остаются согласованными.
  Кросс-run и кросс-task планы и решения отвергаются.
- **Acceptance остаётся терминальным и независимым.** Пройденный acceptance
  завершает run, и revision не выполняется. Revision не может добавить, удалить,
  заменить, ослабить criterion или самостоятельно его удовлетворить, и она никогда не
  фабрикует вердикт acceptance.
- **Отклонённая revision — ограниченный отказ, а не сбой run.** Когда решение просит
  revision, а eligibility отказывает, отказ фиксируется как denied action, и loop
  продолжается до одной из настроенных границ. Ничего не исполняется и никакой план не
  создаётся.
- **Два события с санитизированными метаданными.** `REVISION_STARTED` и
  `REVISION_COMPLETED` записывают идентичности, номер revision, ограниченную
  идентичность плана, статус, категорию сбоя, actionable, остаток бюджета и прежний и
  новый fingerprint плана. Ни stdout, ни stderr, ни окружение, ни credentials, ни
  сырой вывод модели.

### Отклонённые альтернативы (с обоснованием)

- **Исполнять каждое решение `REQUEST_REVISION`.** Отклонено: это делает
  предпочтение модели триггером — ровно то субъективное основание, которое граница
  обязана исключать.
- **Повторять то же действие до успеха.** Отклонено: это blind retry, а не revision,
  и он превращает неуспешную verification в неограниченный цикл.
- **Считать отказ security или authority повторяемым.** Отклонено: run никогда не
  должен пытаться обойти границу, в которой ему отказано.
- **Позволять revision мутировать существующий план.** Отклонено: это позволило бы
  revision переиспользовать доверенную идентичность для недоверенного содержимого.
- **Помещать бюджет revision в task request или в план.** Отклонено: это делает
  границу управляемой клиентом и лишает смысла ограничение loop.
- **Позволять revision обходить approval или исполнять напрямую.** Отклонено: это
  дало бы слою планирования execution authority в обход.
- **Расширять существующие границы действий/итераций harness.** Отклонено:
  существующие границы policy уже ограничивают loop, и их переиспользование сохраняет
  единственный источник истины.

### Следствия и открытые пункты

1. **Детерминированный планировщик делает no-progress обычным исходом.**
   Существующие шаблоны не могут изобрести другие намерения, поэтому revision по той
   же цели сейчас сводится к той же форме и завершается как `revision_no_progress`.
   Действительно иные revisions требуют планировщика, способного дать другой набор
   намерений; это отложено.
2. **Путь `run_agent_loop` сегодня не может вызвать revision.** Его наблюдения
   verification равны `not_evaluated`, поскольку идентичность критериев на этом пути —
   GAP-F, поэтому объективного вердикта verification нет. Объективный триггер revision
   сейчас требует операторских критериев пути accepted-task.
3. **Истории ревизий нет.** Записываются только прежний и новый fingerprint плана;
   полная история ревизий отложена.
4. **GAP-A, GAP-B, GAP-F, directory-depth, snapshot consistency, параллельное и
   распределённое исполнение, более богатые retry-политики, исполнение tools через
   harness, memory, knowledge, reviewer, idempotency и resume этим блоком не
   изменены.**

## Production Tool Execution v0.1

Implemented in the tool execution block. Verified against the code in
`app/agent_runtime/tool_execution.py`, `app/tools/bounded.py`,
`app/agent_runtime/harness.py`, `app/agent_runtime/models.py`,
`app/decision/models.py`, `app/decision/validator.py`,
`app/orchestrator/models.py`, `app/runtime/bootstrap.py`, `app/api/service.py`,
and `tests/test_tool_execution_production.py`.

### Accepted decisions

- **The existing `ToolExecutor` was reused, not duplicated.** There is no second
  registry, permission policy, approval policy, executor, or tool loop. The
  harness composes an invocation and a `ToolExecutionContext`; the executor keeps
  owning the permission check, the approval flow, and the tool call.
- **`ToolIntent` is data.** It carries a tool identity and bounded arguments and
  nothing else. A key that names execution authority (`command`, `argv`,
  `executable`, `shell`, `environment`, `timeout`, `run_scope`,
  `authorized_execution`, `approval*`, `allowed_execution_commands`,
  `allowed_tool_ids`, `capabilities`, `workspace`, `network_access`, credentials)
  is refused at construction rather than ignored.
- **The trusted tool authority is the run's frozen `RunScope`.** The harness reads
  `RunScope.allowed_tool_ids` as the perimeter and refuses a request whose declared
  intents exceed it before anything is planned or decided. `HarnessRequest` gained
  no `allowed_tool_ids` field, so a request can never assert its own tool authority.
- **A decision selects an index, never a tool.** `DecisionAction.INVOKE_TOOL` and
  `DecisionType.INVOKE_TOOL` are distinct from `EXECUTE`, and the validator rejects
  a decision that mixes them. The action is offered only when the run authorizes
  tools, has a declared intent, has a registered executor, and has not already used
  its single tool action.
- **Every invocation is re-authorized and freshly identified.**
  `authorize_tool_intent` mints a new invocation identity per attempt and derives
  the whole context from the run's perimeter, so a previous attempt's identity or
  result can never be reused as authority.
- **Results are bounded before they are durable.** `BoundedToolResult` and
  `bound_tool_result` (`app/tools/bounded.py`) keep a digest, a byte count, a
  capped preview, a truncated error, and allow-listed structural metadata only. Raw
  stdout, stderr, environment, credentials, and arbitrary file content never enter
  the run trail. The ceilings are module constants, not parameters.
- **Denial is terminal.** A permission or approval denial ends the run as `FAILED`;
  it is never turned into a revision, because a revision must not become a way
  around a tool boundary. The bounded tool limit is enforced the same way.
- **One event source per fact.** The executor emits
  `TOOL_INVOCATION_REQUESTED`, `PERMISSION_CHECKED`, `TOOL_INVOCATION_DENIED`,
  `TOOL_EXECUTION_STARTED`, and its terminal tool event. The harness adds exactly
  one new event, `TOOL_RESULT_BOUNDED`, carrying its sanitized projection.
- **Composition stays deny-by-default.** `ForgeApiService` takes an optional
  `tool_intents` parameter that defaults to empty, and its tool perimeter is derived
  exactly as before. With no declared intents no tool action is ever offered, and
  the service path still requires the operator's `allowed_tool_ids` for any tool to
  pass the executor's permission check.

### Rejected alternatives (with rationale)

- **Letting a decision or a model name the tool and its arguments directly.**
  Rejected: that makes the model the source of tool authority, which is exactly the
  bypass this boundary exists to prevent.
- **Adding `allowed_tool_ids` to `HarnessRequest` for convenience.** Rejected: it
  would let a request assert its own tool authority instead of inheriting the frozen
  scope.
- **Building a second tool registry or executor inside the harness.** Rejected: the
  production `ToolExecutor` already owns permission, approval, and the call, and a
  second one would create a parallel security path.
- **Storing raw tool output in the run trail.** Rejected: tool output can contain
  file content or credentials, and the trail is durable.
- **Treating a tool denial as a revision trigger.** Rejected: it would let the
  revision loop retry around a permission or approval decision.
- **Adding a tool action per iteration.** Rejected: one bounded tool action per run
  keeps the loop bounded and prevents a provider from driving repeated invocations.

### Consequences and open items

1. **One tool action per run.** Following an invocation with a second one requires
   lifting the single-action bound, which is deferred.
2. **No sandbox.** A tool runs in-process under the existing trust model, so this
   block does not close GAP-A or GAP-B.
3. **Tool execution is reachable on the declared-verification path** when the
   operator declares both the intent and the allowlist. On `run_agent_loop`
   verification observations remain `not_evaluated` because criterion identity there
   is GAP-F; nothing about that gap was changed or faked.
4. **GAP-A, GAP-B, GAP-F, directory-depth, snapshot consistency, parallel and
   distributed execution, memory, knowledge, reviewer, idempotency, and resume are
   unchanged** by this block.

## Production Tool Execution v0.1 — русская версия

Реализовано в блоке tool execution. Проверено по коду в
`app/agent_runtime/tool_execution.py`, `app/tools/bounded.py`,
`app/agent_runtime/harness.py`, `app/agent_runtime/models.py`,
`app/decision/models.py`, `app/decision/validator.py`,
`app/orchestrator/models.py`, `app/runtime/bootstrap.py`, `app/api/service.py` и
`tests/test_tool_execution_production.py`.

### Принятые решения

- **Существующий `ToolExecutor` переиспользован, а не продублирован.** Второго
  registry, permission policy, approval policy, executor'а или tool-loop нет.
  Harness компонует invocation и `ToolExecutionContext`; executor по-прежнему
  владеет проверкой permission, процессом approval и самим вызовом tool.
- **`ToolIntent` — это данные.** Он несёт идентичность tool и ограниченные
  аргументы, и больше ничего. Ключ, называющий execution authority (`command`,
  `argv`, `executable`, `shell`, `environment`, `timeout`, `run_scope`,
  `authorized_execution`, `approval*`, `allowed_execution_commands`,
  `allowed_tool_ids`, `capabilities`, `workspace`, `network_access`, credentials),
  отвергается при создании, а не игнорируется.
- **Доверенный tool authority — это frozen `RunScope` run.** Harness читает
  `RunScope.allowed_tool_ids` как периметр и отвергает запрос, объявленные intents
  которого выходят за него, ещё до планирования и решения. `HarnessRequest` не
  получил поля `allowed_tool_ids`, поэтому запрос никогда не может сам объявить свой
  tool authority.
- **Решение выбирает индекс, а не tool.** `DecisionAction.INVOKE_TOOL` и
  `DecisionType.INVOKE_TOOL` отличны от `EXECUTE`, и валидатор отвергает решение,
  смешивающее их. Действие предлагается только когда run авторизует tools, объявил
  intent, имеет зарегистрированный executor и ещё не использовал своё единственное
  tool-действие.
- **Каждый invocation авторизуется заново и получает свежую идентичность.**
  `authorize_tool_intent` создаёт новую идентичность invocation на каждую попытку и
  выводит весь контекст из периметра run, поэтому идентичность или результат
  предыдущей попытки никогда не переиспользуется как authority.
- **Результаты ограничиваются до сохранения.** `BoundedToolResult` и
  `bound_tool_result` (`app/tools/bounded.py`) сохраняют только дайджест, размер в
  байтах, ограниченный preview, усечённую ошибку и структурные метаданные из
  allow-list. Сырые stdout, stderr, окружение, credentials и произвольное содержимое
  файлов никогда не попадают в trail run. Границы — это константы модуля, а не
  параметры.
- **Отказ терминален.** Отказ permission или approval завершает run как `FAILED`; он
  никогда не превращается в revision, поскольку revision не должен становиться
  способом обойти границу tool. Ограничение на число tool-действий обеспечивается
  так же.
- **Один источник событий на факт.** Executor испускает
  `TOOL_INVOCATION_REQUESTED`, `PERMISSION_CHECKED`, `TOOL_INVOCATION_DENIED`,
  `TOOL_EXECUTION_STARTED` и своё терминальное событие tool. Harness добавляет ровно
  одно новое событие, `TOOL_RESULT_BOUNDED`, со своей санитизированной проекцией.
- **Композиция остаётся deny-by-default.** `ForgeApiService` принимает
  необязательный параметр `tool_intents` со значением по умолчанию — пустым, и его
  tool-периметр выводится ровно как раньше. Без объявленных intents ни одно
  tool-действие не предлагается, а сервисный путь по-прежнему требует операторского
  `allowed_tool_ids`, чтобы хоть один tool прошёл проверку permission у executor'а.

### Отклонённые альтернативы (с обоснованием)

- **Позволять решению или модели напрямую называть tool и его аргументы.**
  Отклонено: это делает модель источником tool authority — ровно тот обход, который
  обязана предотвращать эта граница.
- **Добавить `allowed_tool_ids` в `HarnessRequest` для удобства.** Отклонено: это
  позволило бы запросу объявлять собственный tool authority вместо наследования
  frozen scope.
- **Построить второй tool registry или executor внутри harness.** Отклонено:
  production `ToolExecutor` уже владеет permission, approval и вызовом, а второй
  создал бы параллельный security path.
- **Хранить сырой вывод tool в trail run.** Отклонено: вывод tool может содержать
  содержимое файлов или credentials, а trail долговечен.
- **Считать отказ tool триггером revision.** Отклонено: это позволило бы revision
  loop повторять попытки в обход решения permission или approval.
- **Добавлять tool-действие на каждую итерацию.** Отклонено: одно ограниченное
  tool-действие на run сохраняет loop ограниченным и не даёт провайдеру инициировать
  повторные вызовы.

### Следствия и открытые пункты

1. **Одно tool-действие на run.** Второй вызов после invocation требует снятия
   ограничения на одно действие; это отложено.
2. **Sandbox отсутствует.** Tool исполняется в процессе под существующей trust
   model, поэтому этот блок не закрывает GAP-A и GAP-B.
3. **Исполнение tools достижимо на пути declared-verification**, когда оператор
   объявляет и intent, и allowlist. На пути `run_agent_loop` наблюдения verification
   остаются `not_evaluated`, поскольку идентичность критериев там — GAP-F; в этом
   блоке ничего в этом разрыве не изменено и не подделано.
4. **GAP-A, GAP-B, GAP-F, directory-depth, snapshot consistency, параллельное и
   распределённое исполнение, memory, knowledge, reviewer, idempotency и resume этим
   блоком не изменены.**

## Production Task / Criterion Identity v0.1 (GAP-F closed for harness entry points)

Implemented in the criterion identity block. Verified against the code in
`app/agent_runtime/criterion_identity.py`, `app/agent_runtime/harness.py`,
`app/agent_runtime/models.py`, `app/api/service.py`,
`app/orchestrator/models.py`, and
`tests/test_criterion_identity_production.py`.

### Accepted decisions

- **Identity is derived, never supplied.** `TaskIdentity` comes from the trusted
  `TaskSpecification`; each `CriterionIdentity` comes from the operator's
  `AcceptanceSpec` criterion plus its `VerificationExpectation`. A task id, a
  criterion id, an acceptance authority, or a verification authority is never
  accepted from an HTTP request, a task context, an LLM, a plan, a decision, or a
  tool result.
- **A criterion cannot exist without its task.** `CriterionIdentity` always carries
  the owning `task_id`, and `bind_run_criteria` refuses to build a binding unless it
  is given a trusted specification. Anonymous global criterion text is therefore not
  representable.
- **One frozen binding per run.** `RunCriterionBinding` ties the task identity and
  every criterion identity to exactly one `run_id`, and the harness refuses a
  second, different binding for the same run. Criteria cannot be swapped after a run
  starts. The existing single `AcceptanceSpec.bind`/`RunAcceptanceCriteria` path is
  unchanged and still serves `run_accepted_task`.
- **Fingerprints are canonical and content-bound.** Both fingerprints are SHA-256
  over a JSON projection with sorted keys. The criterion fingerprint covers the
  criterion *and* its verification expectation, so substituting an expectation is
  detected. Fingerprints contain no command, environment value, credential, or raw
  output.
- **Verification proves identity before evaluating, through one canonical check.**
  `RunCriterionBinding.validate_request` is the only identity path, and it proves
  `bound criteria == request acceptance criteria == verified criteria`. A missing
  criterion, an extra criterion, a criterion reusing a bound id with different
  content, a missing expectation, or an expectation whose path or hash differs is
  refused. The harness keeps no second, weaker inline check. On a mismatch it emits
  `VERIFICATION_IDENTITY_FAILED`, runs neither the verifier nor the acceptance
  gate, and returns `NOT_RUN` results with no acceptance verdict.
- **A binding is registered only after the run's trusted setup succeeds.** The
  binding is validated first, then workspace, command, criteria, and tool checks
  run, then the scope freezes, and only then is the binding recorded, so a run that
  fails its own setup leaves no identity claim behind.
- **The binding registry is per harness instance and bounded.** Each
  `AgentHarness` owns its own map, and entries whose run scope has been released
  are pruned, so claims are bounded by the runs that are still alive. It is not a
  process-global registry.
- **An identity mismatch is terminal, not actionable.** It is a trust failure, so
  the revision loop must not treat it as a reason to try again and cannot use it to
  escape the binding.
- **Absence is honest.** With no binding there is no criterion, so no verification
  results and no acceptance verdict are produced. Missing identity is never
  converted into a pass, and nothing is invented to make acceptance look evaluated.
- **`run_agent_loop` now carries a real criterion identity.** When the operator
  declared an `AcceptanceSpec` for the declaration, the loop binds those criteria to
  the run and uses a policy that admits the verification stage, so its verification
  observations are verdicts rather than `not_evaluated`. Without a declared spec the
  previous single-action behaviour is unchanged.
- **Identity is not authorization.** The identity classes contain no command, argv,
  executable, shell, environment, timeout, scope, approval, capability, tool,
  workspace, network, or credential field, and the module executes nothing.

### Rejected alternatives (with reasoning)

- **Deriving criteria from a task request, a description, or a category.** Rejected:
  a client- or model-supplied criterion would let it choose what counts as done.
  Criteria stay operator-composed.
- **Keying verification results to criteria by position.** Rejected: it silently
  attributes a verdict to the wrong criterion when an order changes. Results are keyed
  by the criterion they declare.
- **Treating a missing binding as a pass.** Rejected: it would fake the very thing
  GAP-F is about.
- **Treating an identity mismatch as an actionable revision failure.** Rejected: it
  would turn a trust violation into another attempt.
- **Adding identity fields to `VerificationResult` or `HarnessRequest`.** Rejected
  for the result: the criterion id plus the run's frozen binding already make a
  verdict attributable, and growing the public result shape is unnecessary. The
  request carries the frozen binding, not a mutable criterion list.

### Consequences and open items

1. **Attribution is durable.** `CRITERION_DEFINED` records the run id, task id, task
   fingerprint, criterion ids, per-criterion fingerprints, the binding fingerprint,
   and the criteria source. No raw criterion text is stored.
2. **GAP-F is closed for the harness entry points only.** `run_agent_loop` with a
   declared `AcceptanceSpec` and `run_accepted_task` carry a real binding;
   `run_agent_loop` without a spec carries no criterion, and the legacy `run_task`,
   HTTP task-run, and desktop dispatch paths carry no binding at all and stay
   `not_evaluated`.
2. **A run still needs its decision provider to complete.** Verification and
   acceptance are real and attributed, but reaching `COMPLETED` requires the
   provider to complete the run after a passing acceptance; otherwise the action
   bound leaves it `LIMIT_REACHED` with the verdict recorded.
3. **Remaining entry points.** `run_agent_loop` and `run_accepted_task` carry the
   binding; routing the other task entry points through it is follow-up work.
4. **GAP-A, GAP-B, directory-depth, snapshot consistency, advanced dynamic
   replanning, parallel and distributed execution, advanced retry policy, memory,
   knowledge, reviewer, idempotency, and resume are unchanged** by this block.

## Production Task / Criterion Identity v0.1 — русская версия (GAP-F закрыт для harness-точек входа)

Реализовано в блоке criterion identity. Проверено по коду в
`app/agent_runtime/criterion_identity.py`, `app/agent_runtime/harness.py`,
`app/agent_runtime/models.py`, `app/api/service.py`,
`app/orchestrator/models.py` и `tests/test_criterion_identity_production.py`.

### Принятые решения

- **Идентичность выводится, а не поставляется.** `TaskIdentity` берётся из
  доверенного `TaskSpecification`; каждый `CriterionIdentity` — из criterion
  операторского `AcceptanceSpec` вместе с его `VerificationExpectation`. Ни task id,
  ни criterion id, ни acceptance authority, ни verification authority никогда не
  принимаются из HTTP-запроса, контекста задачи, LLM, плана, решения или результата
  tool.
- **Criterion не может существовать без своей задачи.** `CriterionIdentity` всегда
  несёт `task_id` владельца, а `bind_run_criteria` отказывается строить привязку без
  доверенной спецификации. Безымянный глобальный текст criterion непредставим.
- **Одна frozen привязка на run.** `RunCriterionBinding` связывает идентичность
  задачи и каждого criterion ровно с одним `run_id`, и harness отвергает вторую,
  отличающуюся привязку для того же run. Criteria нельзя подменить после старта run.
  Существующий путь `AcceptanceSpec.bind`/`RunAcceptanceCriteria` не изменён и
  по-прежнему обслуживает `run_accepted_task`.
- **Fingerprint каноничны и привязаны к содержимому.** Оба fingerprint — SHA-256 по
  JSON-проекции с сортированными ключами. Fingerprint criterion покрывает criterion
  **и** его verification expectation, поэтому подмена expectation обнаруживается. В
  fingerprint нет ни команды, ни значения окружения, ни credentials, ни сырого
  вывода.
- **Verification доказывает идентичность до оценки, через одну каноническую
  проверку.** `RunCriterionBinding.validate_request` — единственный путь
  идентичности, и он доказывает `bound criteria == request acceptance criteria ==
  verified criteria`. Отсутствующий criterion, лишний criterion, criterion с тем же
  id, но другим содержимым, отсутствующий expectation и expectation с другим path или
  hash — всё отвергается. Второй, более слабой инлайн-проверки в harness нет. При
  несовпадении он испускает `VERIFICATION_IDENTITY_FAILED`, не запускает ни verifier,
  ни acceptance gate и возвращает результаты `NOT_RUN` без вердикта acceptance.
- **Привязка регистрируется только после успешного доверенного setup run.** Сначала
  валидируется привязка, затем проходят проверки workspace, команд, criteria и tools,
  затем scope замораживается, и только после этого привязка фиксируется, поэтому run,
  чей setup провалился, не оставляет identity-претензии.
- **Реестр привязок — per harness instance и ограничен.** Каждый `AgentHarness`
  владеет своей картой, и записи, чей run scope освобождён, вычищаются, поэтому
  претензии ограничены живыми runs. Это не process-global реестр.
- **Несовпадение идентичности терминально, а не actionable.** Это trust-сбой,
  поэтому revision loop не должен считать его поводом повторить попытку и не может
  использовать его для выхода из привязки.
- **Отсутствие честно.** Без привязки нет criterion, поэтому нет ни результатов
  verification, ни вердикта acceptance. Отсутствующая идентичность никогда не
  превращается в прохождение, и ничего не выдумывается, чтобы acceptance выглядел
  оценённым.
- **`run_agent_loop` теперь несёт настоящую идентичность criterion.** Когда оператор
  объявил `AcceptanceSpec` для декларации, loop привязывает эти criteria к run и
  использует политику, допускающую стадию verification, поэтому его наблюдения
  verification — вердикты, а не `not_evaluated`. Без объявленного spec прежнее
  одно-действенное поведение не изменено.
- **Идентичность — не authorization.** В классах идентичности нет ни команды, ни
  argv, ни executable, ни shell, ни окружения, ни timeout, ни scope, ни approval, ни
  capability, ни tool, ни workspace, ни сети, ни credentials, а модуль ничего не
  исполняет.

### Отклонённые альтернативы (с обоснованием)

- **Выводить criteria из запроса задачи, описания или категории.** Отклонено:
  критерий, поставляемый клиентом или моделью, позволил бы им выбирать, что считать
  выполненным. Criteria остаются композицией оператора.
- **Связывать результаты verification с criteria по позиции.** Отклонено: при
  изменении порядка это молча приписывает вердикт не тому criterion. Результаты
  связываются по criterion, который они сами объявляют.
- **Считать отсутствие привязки прохождением.** Отклонено: это подделало бы ровно то,
  о чём GAP-F.
- **Считать несовпадение идентичности actionable-сбоем revision.** Отклонено: это
  превратило бы нарушение доверия в ещё одну попытку.
- **Добавлять поля идентичности в `VerificationResult` или `HarnessRequest`.**
  Отклонено для результата: criterion id вместе с frozen привязкой run уже делают
  вердикт атрибутируемым, а расширение публичной формы результата не нужно. Запрос
  несёт frozen привязку, а не изменяемый список criteria.

### Следствия и открытые пункты

1. **Атрибуция долговечна.** `CRITERION_DEFINED` записывает run id, task id,
   fingerprint задачи, идентификаторы criteria, fingerprint каждого criterion,
   fingerprint привязки и источник criteria. Сырой текст criterion не сохраняется.
2. **GAP-F закрыт только для harness-точек входа.** `run_agent_loop` с объявленным
   `AcceptanceSpec` и `run_accepted_task` несут настоящую привязку; `run_agent_loop`
   без spec не несёт criterion, а legacy-пути `run_task`, HTTP task run и desktop
   dispatch вообще не несут привязки и остаются `not_evaluated`.
2. **Run всё ещё нуждается в своём провайдере решений для завершения.** Verification
   и acceptance настоящие и атрибутированные, но достижение `COMPLETED` требует,
   чтобы провайдер завершил run после пройденного acceptance; иначе граница действий
   оставляет run в `LIMIT_REACHED` с записанным вердиктом.
3. **Остальные точки входа.** `run_agent_loop` и `run_accepted_task` несут привязку;
   перевод остальных task-точек входа на неё — последующая работа.
4. **GAP-A, GAP-B, directory-depth, snapshot consistency, продвинутое динамическое
   перепланирование, параллельное и распределённое исполнение, продвинутая
   retry-политика, memory, knowledge, reviewer, idempotency и resume этим блоком не
   изменены.**

## Execution Filesystem Boundary and Network Policy v0.1 (GAP-A / GAP-B)

Implemented in the sandbox and network hardening block. Verified against the code
in `app/execution/sandbox.py`, `app/execution/adapter.py`,
`app/runtime/run_scope.py`, and `tests/test_execution_sandbox_boundary.py`.

### Accepted decisions

- **One canonical filesystem boundary.** `WorkspaceBoundary` is the single answer
  to "is this path inside the run's workspace?". It canonicalizes the root with
  `os.path.realpath` (following junctions and substituted drives, not only
  symlinks), normalizes the requested path so `..` segments and absolute forms are
  refused outright, then canonicalizes the result and proves containment by
  `relative_to` on canonical paths.
- **Enforced twice, at the right places.** The check runs at the authority
  boundary - `RunScope.validate_execution_request` - so an escaping working
  directory or artifact target is refused before any dispatch decision, and again
  inside `LocalExecutionAdapter._run_process` immediately before the child process
  is created. The adapter check re-canonicalizes the working directory from the
  live filesystem, proves it is contained by the trusted execution root carried by
  the `AuthorizedExecution` token, and hands that canonical path to `Popen`. The
  boundary is always built with `WorkspaceBoundary.for_workspace(...)` from a
  trusted root, never from the path being checked: a boundary built from the path
  under test would compare that path with itself and could never refuse. The
  adapter's `_resolve_working_directory` shares the same implementation instead of
  a second lexical check.
- **Entry points are stated explicitly.** The boundary applies to every execution
  passing `ExecutionRequest -> ExecutionCoordinator -> AuthorizedExecution ->
  LocalExecutionAdapter`. The legacy `run_task`, the HTTP task-run compatibility
  path, and desktop dispatch all reach it: they call `ForgeApiService.run_task`,
  which builds a `RunScope` through `_build_run_scope` and delegates to
  `RunExecutor`, which dispatches through `ExecutionCoordinator` with that scope, so
  `RunScope.validate_execution_request` runs on that path too. What differs is
  reachability, not enforcement: `RunExecutor` derives `command_authority` from the
  operator-declared execution commands, and with no server-side execution
  declaration that set is empty, so no process is reachable and the boundary is
  simply not exercised. Legacy behaviour was deliberately left unchanged: these
  paths receive none of the `AgentHarness`-specific behaviour, and this record is
  only about the filesystem/network boundary on the existing execution path.
- **TOCTOU is an architectural limitation, not a defect.** Canonicalizing the
  directory and passing that same canonical path to `Popen` narrows the window
  between the check and the spawn; it cannot eliminate it. Only an OS-level
  mechanism can. The limitation is recorded rather than claimed as closed.
- **The root is authority-derived.** The boundary is built from the frozen
  workspace only. A task, a decision, a plan, a tool intent, a request, or metadata
  can never widen it; metadata is data.
- **Network authority is deny-by-default and server-side.**
  `ProjectExecutionProfile.network_access` defaults to `False`, the scope freezes
  it, and `RunScope.validate_execution_profile` refuses a request profile that
  tries to enable it. `ToolIntent` refuses `network`/`network_access` as
  authority-shaped argument keys.

### Rejected alternatives (with reasoning)

- **Claiming GAP-A and GAP-B are closed.** Rejected: neither is. There is no
  OS-level filesystem sandbox, and the network denial is a policy boundary rather
  than a kernel one. Both are recorded as PARTIAL with explicit residual risk.
- **A lexical path check as the only defense.** Rejected: `sub/../../outside`
  contains no leading `..` and passes a naive prefix test, and a junction whose
  name sits inside the workspace can resolve outside it. Containment is proved on
  canonical paths, not on strings.
- **Relying only on the adapter check.** Rejected: an escape would then be
  discovered at the last moment inside the execution plane rather than refused by
  the authority boundary that is supposed to own the perimeter.
- **Adding `network_access=True` to the default profile** so an ordinary
  development command keeps working. Rejected: it would make the production
  default permissive. The default stays deny; an operator who needs network
  declares it explicitly in the frozen profile.
- **Adding a privileged or root-dependent isolation mechanism** (Windows firewall
  rule, restricted token, AppContainer, Linux namespace) to look complete.
  Rejected: it would not be verifiable on the target runtime and would introduce a
  deployment requirement this repository cannot assume. The honest boundary and
  the recorded risk are the correct outcome for this block.
- **Deleting the proxy variables and calling network disabled.** Rejected: it was
  never a sandbox and the code and docs now say so explicitly.

### Consequences and open items

1. **GAP-A is PARTIAL.** Two residual risks remain, each classified: child-process
   inheritance is an **accepted residual risk** (a spawned process inherits the
   parent's token and may open any path that token may open; `RESIDUAL_RISK`
   records it and a test asserts it by reading an outside file from a child), and
   the check-to-spawn window is an **architectural limitation**. Closing either
   needs a deployment-level mechanism: a container, a restricted token with a
   filesystem ACL on the workspace, or an AppContainer / namespace profile.
2. **GAP-B is PARTIAL.** The proxy-variable denial stops well-behaved HTTP clients
   and does not stop a direct socket; a test asserts that socket creation still
   succeeds. Closing it needs a network namespace, a firewall rule, or a container.
3. **The boundary constrains the execution plane, not the child's syscalls.** This
   is stated in code, in the architecture document, and in the roadmap, and it is
   asserted by tests rather than described only in prose.
4. **Real task acceptance still respects the boundary.** The workspace and every
   artifact target are checked at the authority boundary, so a run cannot be
   accepted on the basis of a path it was never allowed to touch.

## Execution Filesystem Boundary and Network Policy v0.1 — русская версия (GAP-A / GAP-B)

Реализовано в блоке sandbox и network hardening. Проверено по коду в
`app/execution/sandbox.py`, `app/execution/adapter.py`,
`app/runtime/run_scope.py` и `tests/test_execution_sandbox_boundary.py`.

### Принятые решения

- **Одна каноническая файловая граница.** `WorkspaceBoundary` — единственный ответ
  на вопрос «внутри ли этот путь workspace'а run?». Он каноникализирует корень через
  `os.path.realpath` (раскрывая junction'ы и substituted drives, а не только
  symlink'и), нормализует запрошенный путь так, что сегменты `..` и абсолютные формы
  отвергаются сразу, затем каноникализирует результат и доказывает вложенность через
  `relative_to` по каноническим путям.
- **Проверка применяется дважды и в правильных местах.** Она выполняется на границе
  authority — `RunScope.validate_execution_request` — поэтому выходящая за периметр
  рабочая директория или artifact target отвергается до любого решения о dispatch, и
  повторно внутри `LocalExecutionAdapter._run_process` непосредственно перед созданием
  дочернего процесса. Проверка в adapter'е заново каноникализирует рабочую директорию
  из живой файловой системы, доказывает её вложенность в доверенный execution root,
  который несёт токен `AuthorizedExecution`, и передаёт в `Popen` именно этот
  канонический путь. Граница всегда строится через `WorkspaceBoundary.for_workspace(...)`
  от доверенного корня и никогда — от самой проверяемой директории: граница, построенная
  от проверяемого пути, сравнивала бы этот путь с самим собой и не могла бы отказать.
  `_resolve_working_directory` adapter'а использует ту же реализацию вместо второй
  лексической проверки.
- **Точки входа названы явно.** Граница применяется ко всякому исполнению, проходящему
  `ExecutionRequest -> ExecutionCoordinator -> AuthorizedExecution ->
  LocalExecutionAdapter`. Legacy `run_task`, HTTP-путь совместимости task run и
  desktop dispatch её достигают: они вызывают `ForgeApiService.run_task`, который
  строит `RunScope` через `_build_run_scope` и делегирует в `RunExecutor`, а тот
  dispatch'ит через `ExecutionCoordinator` с этим scope, поэтому
  `RunScope.validate_execution_request` выполняется и на этом пути. Отличается
  достижимость, а не enforcement: `RunExecutor` выводит `command_authority` из
  объявленных оператором execution-команд, и без серверного объявления execution это
  множество пусто, поэтому ни один процесс не достижим и граница просто не
  задействована. Legacy-поведение намеренно не менялось: эти пути не получают ничего
  специфичного для `AgentHarness`, и эта запись — только о filesystem/network-границе
  на существующем execution path.
- **TOCTOU — architectural limitation, а не дефект.** Каноникализация директории и
  передача того же канонического пути в `Popen` сужает окно между проверкой и spawn,
  но не может его устранить. Это под силу только OS-level механизму. Ограничение
  зафиксировано, а не объявлено закрытым.
- **Корень выводится из authority.** Граница строится только из frozen workspace.
  Задача, решение, план, tool intent, запрос или metadata никогда не могут её
  расширить; metadata — это данные.
- **Сетевая authority — deny-by-default и server-side.**
  `ProjectExecutionProfile.network_access` по умолчанию `False`, scope замораживает
  этот выбор, а `RunScope.validate_execution_profile` отвергает профиль запроса,
  пытающийся его включить. `ToolIntent` отвергает `network`/`network_access` как
  authority-shaped ключи аргументов.

### Отклонённые альтернативы (с обоснованием)

- **Объявить GAP-A и GAP-B закрытыми.** Отклонено: ни один не закрыт. OS-level
  filesystem sandbox отсутствует, а сетевой запрет — это policy boundary, а не
  kernel-механизм. Оба зафиксированы как PARTIAL с явным остаточным риском.
- **Лексическая проверка пути как единственная защита.** Отклонено:
  `sub/../../outside` не содержит ведущего `..` и проходит наивную проверку
  префикса, а junction, имя которого внутри workspace, может разрешаться наружу.
  Вложенность доказывается по каноническим путям, а не по строкам.
- **Полагаться только на проверку в adapter'е.** Отклонено: тогда выход за периметр
  обнаруживался бы в последний момент внутри execution plane, а не отвергался
  границей authority, которая владеет периметром.
- **Добавить `network_access=True` в профиль по умолчанию**, чтобы обычная
  development-команда продолжала работать. Отклонено: это сделало бы production
  default разрешающим. Default остаётся deny; оператор, которому нужна сеть,
  объявляет её явно в frozen profile.
- **Добавить привилегированный или root-зависимый механизм изоляции** (правило
  Windows firewall, restricted token, AppContainer, Linux namespace) ради
  завершённости вида. Отклонено: это нельзя проверить на целевом runtime, и это
  внесло бы требование к развёртыванию, которое репозиторий не может предполагать.
  Честная граница и зафиксированный риск — правильный итог для этого блока.
- **Удалить proxy-переменные и назвать сеть отключённой.** Отклонено: это никогда
  не было sandbox'ом, и теперь код и документация говорят это явно.

### Следствия и открытые пункты

1. **GAP-A — PARTIAL.** Дочерний процесс наследует security token родителя и может
   открыть любой доступный этому токену путь. `RESIDUAL_RISK` фиксирует это, а тест
   доказывает это, читая файл вне workspace из дочернего процесса. Для закрытия нужен
   механизм уровня развёртывания: контейнер, restricted token с ACL на workspace или
   профиль AppContainer / namespaces.
2. **GAP-B — PARTIAL.** Запрет через proxy-переменные останавливает корректные
   HTTP-клиенты и не останавливает прямой сокет; тест доказывает, что создание сокета
   всё ещё succeeds. Для закрытия нужен network namespace, правило firewall или
   контейнер.
3. **Граница ограничивает execution plane, а не системные вызовы дочернего
   процесса.** Это сказано в коде, в архитектурном документе и в roadmap, и это
   доказывается тестами, а не только описанием.
4. **Реальное принятие задачи по-прежнему уважает границу.** Workspace и каждый
   artifact target проверяются на границе authority, поэтому run не может быть
   принят на основании пути, которого ему никогда не разрешали касаться.

## Run Idempotency and Recoverable Resume v0.1 (GAP-I / GAP-R)

Implemented in the idempotency and resume block. Verified against the code in
`app/agent_runtime/idempotency.py`, `app/agent_runtime/idempotency_integration.py`,
`app/api/service.py`, and `tests/test_idempotency_production.py`.

### Accepted decisions

- **Four identities, never one.** `TaskIdentity` -> `OperationIdentity` ->
  `RunIdentity` -> `AttemptIdentity`, with a separate action identity per side
  effect. Collapsing these into one id is exactly how a duplicate execution
  becomes invisible, so the hierarchy is explicit and each level has its own
  fingerprint.
- **The idempotency key is input, never authority.** It is canonicalized and
  refused when malformed rather than coerced, and it is bound to a fingerprint
  covering the operation class, the key, the task fingerprint, and every
  criterion fingerprint. A key can therefore never be reused for different work.
- **The ledger is separate from `RunStore`.** `RunStore` documents itself as an
  observation sink that is "not a resume engine"; extending it into a decision
  engine would have contradicted the existing architecture. The ledger is a new,
  focused component, and this block does not write run history through it.
- **Claiming a key is atomic through `O_EXCL`.** This is the strongest primitive
  the repository's storage actually offers. Two simultaneous deliveries of one key
  cannot both win; the loser reads the winner's record.
- **No compare-and-set and no lease.** Stealing a stale claim would require
  guessing whether the previous writer is dead, which is unprovable. A `CREATED`
  or `RUNNING` record is therefore refused by plain resume rather than assumed
  interrupted.
- **`STARTED` is written before the effect; `UNKNOWN_AFTER_CRASH` is derived.**
  A completion record is never fabricated, and a caller cannot assert an unknown
  state. Reading reports what is on disk; recovery is a separate explicit step.
  This is what makes the crash window honest instead of guessed.
- **Only workspace writes are replay-safe.** Host processes, provider calls, and
  network calls are not, because repeating them can duplicate an irreversible
  action.
- **An authority failure is terminal - and the ledger enforces it.** Permission,
  policy, approval, identity, sandbox, and network denials classify as
  `SECURITY_FAILURE`, which dominates every other recorded label, and neither
  replay nor resume is available. Crucially this is enforced by
  `validate_lifecycle_transition` inside `IdempotencyLedger.update`, not merely
  documented: a terminal state cannot be rewritten to any other state, a refused
  write leaves the durable record byte-identical, and an unprovable side effect
  cannot be moved into a state a resume would accept. A security review found the
  earlier code accepted `SECURITY_FAILURE -> INTERRUPTED`, after which the run
  resumed; the guard exists so the invariant cannot regress.
- **A refused write is never reported as success.** `RunIdempotencyGuard.finish`
  re-raises instead of swallowing, and `run_agent_loop` answers with an explicit
  unrecorded-outcome refusal. If the durable position cannot be recorded, a later
  delivery would read an earlier state and could be admitted again, so the run is
  not reported as a success the code cannot stand behind.
- **A further attempt needs a new operation identity, not a rewritten record.**
  `FAILED` and `LIMIT_REACHED` are terminal for the same reason as
  `SECURITY_FAILURE`: the honest way to retry is a new idempotency key, which is
  an explicit operator decision, rather than reopening a record.
- **Resume restores position, never permission.** The recorded operation
  fingerprint must be reproduced by the current authority ceiling or the resume is
  refused.
- **The guard is injectable.** `ForgeApiService` accepts an explicit
  `idempotency_guard` instead of reading a process-wide singleton, so a test or an
  embedding composition cannot touch state outside its own ledger.
- **Idempotency sits at the entry point, and the claim precedes every durable
  write.** The three trusted in-process entry points
  (`run_agent_loop`, `run_accepted_task`, `run_declared_verification`) claim their
  operation after the trusted identity exists and before the frozen scope, the
  `RunStore` binding, and the first durable event. A duplicate therefore writes no
  `RUN_STARTED` and freezes no scope. Putting the boundary higher would place it
  where identity is still untrusted; putting it lower would place it after an
  effect has already happened.
- **One operation class per entry point.** Each entry point claims under its own
  `OperationClass`, so one key can never let an agent-loop operation satisfy an
  accepted task or a declared verification. This is identity isolation, not
  authority: the classes decide which record a key addresses, never what may run.
- **`run_task` and its HTTP/desktop callers are deliberately excluded.** Their
  `task_id` is supplied by the caller, so there is no trusted task identity to
  bind a key to. Binding one there would let a caller choose the operation
  identity, and would let two callers collide on one key. They need a
  server-derived operation identity first, which is separate work.
- **`purpose_run_id` stays correlation-only.** It is recorded as sanitized
  metadata and is never part of the operation identity, so presenting a different
  one cannot buy a second verification execution under the same key.

### Rejected alternatives

- **"Exactly once".** Rejected: it cannot be proven here, and claiming it would be
  false. The honest ceiling is at-most-once per idempotency key.
- **Treating an unreadable ledger as "no record".** Rejected: that is precisely
  how a duplicate execution happens. A ledger that cannot be consulted produces a
  refusal, not a silent start.
- **Using `run_id` as the idempotency identity.** Rejected: production generates a
  fresh `run_id` per call (`run-loop-<uuid>`, `run-accept-<uuid>`, and so on), so
  binding idempotency to it would have made every duplicate look new.
- **Letting a task or a request declare that an operation is idempotent.**
  Rejected: that would hand authority-shaped meaning to untrusted input. Only the
  trusted declaration plus the frozen binding compose the identity.
- **Persisting the operation so it can be replayed directly.** Rejected: no
  pickle, no serialized authority, no command, no workspace, no environment. A
  resume rebuilds `AuthorizedExecution` from the current trusted `RunScope`.
- **Extending `RunStore` into a resume engine.** Rejected: it would turn the
  observation sink into a second decision path.

### Consequences and open items

1. **Idempotency covers the three trusted in-process entry points, and only when
   a key is supplied.** Without a key each call keeps its previous semantics
   exactly, which is what preserves legacy behaviour.
2. **`run_accepted_task`, `run_declared_verification`, `run_task`, the HTTP
   task-run path, and desktop dispatch do not yet consult the ledger.** They are
   unaffected rather than protected; routing them through the same identity model
   is follow-up work.
3. **Concurrency is bounded, not complete.** The atomic claim is guaranteed; a
   lease, compare-and-set, or multi-process coordination is not, and is recorded
   as a limitation rather than simulated. Updates use atomic file replacement but
   are not CAS-protected read-modify-write, so concurrent updates for one key can
   lose an update, and Windows can surface `PermissionError` during a concurrent
   replacement. These are liveness and recovery limitations, not authority
   expansions.
4. **The unknown-effect interlock is key-scoped.** Same key plus an unprovable
   effect is refused for both resume and a fresh start; a different key for the
   same task is a new operation identity and is currently admitted. Changing the
   key is an operator-level decision, and the implementation provides no global
   action-level suppression across keys. A new key does not make the previous
   unknown effect safe - it makes it unmanaged. Action-level identity and
   suppression is follow-up work.
5. **The side-effect journal is not wired into production.** Two-phase recording
   and `UNKNOWN_AFTER_CRASH` are implemented, tested, and unreachable from the
   current production run path. Until they are wired, a crash inside a side effect
   is not managed by the ledger; only the run-level claim protects a run.
6. **The subsystem is PARTIAL in every dimension** - run idempotency, resume,
   execution idempotency, tool idempotency, and concurrency - and the journal
   integration is NOT WIRED.
4. **Resume is validated but not yet driven by an entry point.** The contract is
   implemented and tested; wiring an operator-facing resume call is follow-up
   work, deliberately not smuggled into this block.
5. **`RunStore` remains an observation sink and is unchanged**, so run history
   keeps its existing meaning and its existing tests.

## Идемпотентность запусков и восстановимый resume v0.1 — русская версия (GAP-I / GAP-R)

Реализовано в блоке idempotency и resume. Проверено по коду в
`app/agent_runtime/idempotency.py`, `app/agent_runtime/idempotency_integration.py`,
`app/api/service.py` и `tests/test_idempotency_production.py`.

### Принятые решения

- **Четыре идентичности, никогда одна.** `TaskIdentity` -> `OperationIdentity` ->
  `RunIdentity` -> `AttemptIdentity`, плюс отдельная action identity на каждый side
  effect. Схлопывание их в один id — это ровно то, из-за чего дублирующее исполнение
  становится незаметным, поэтому иерархия явная и у каждого уровня свой fingerprint.
- **Ключ идемпотентности — вход, никогда не authority.** Он каноникализируется и при
  неверном формате отвергается, а не приводится, и привязывается к fingerprint,
  покрывающему класс операции, ключ, fingerprint задачи и каждый fingerprint
  criterion. Поэтому ключ никогда нельзя переиспользовать для другой работы.
- **Журнал отделён от `RunStore`.** `RunStore` описывает себя как observation sink,
  который «не является resume-движком»; превращение его в движок решений
  противоречило бы существующей архитектуре. Журнал — новый сфокусированный
  компонент, и этот блок не пишет через него историю run.
- **Захват ключа атомарен через `O_EXCL`.** Это самый сильный примитив, который
  реально предлагает хранилище репозитория. Две одновременные доставки одного ключа
  не могут обе победить; проигравший читает запись победителя.
- **Нет compare-and-set и нет lease.** Отобрать устаревший claim означало бы угадывать,
  мёртв ли предыдущий писатель, а это недоказуемо. Поэтому запись `CREATED` или
  `RUNNING` отклоняется обычным resume, а не считается прерванной.
- **`STARTED` пишется до эффекта; `UNKNOWN_AFTER_CRASH` выводится.** Запись о
  завершении никогда не выдумывается, и вызывающий не может объявить неизвестное
  состояние. Чтение отдаёт то, что на диске; восстановление — отдельный явный шаг.
  Именно это делает окно краха честным, а не угаданным.
- **Replay-safe только записи в workspace.** Host-процессы, вызовы провайдера и
  сетевые вызовы — нет, потому что их повтор может продублировать необратимое
  действие.
- **Отказ authority терминален — и журнал это обеспечивает.** Отказы permission,
  policy, approval, identity, sandbox и network классифицируются как
  `SECURITY_FAILURE`, что доминирует над любой другой записанной меткой, и ни
  replay, ни resume недоступны. Принципиально, что это обеспечивается
  `validate_lifecycle_transition` внутри `IdempotencyLedger.update`, а не только
  документируется: терминальное состояние нельзя перезаписать в любое другое,
  отклонённая запись оставляет durable-запись побайтово неизменной, а недоказуемый
  side effect нельзя перевести в состояние, которое примет resume. Security review
  обнаружил, что прежний код принимал `SECURITY_FAILURE -> INTERRUPTED`, после чего
  run восстанавливался; guard существует, чтобы инвариант не регрессировал.
- **Отклонённая запись никогда не выдаётся за успех.**
  `RunIdempotencyGuard.finish` пробрасывает ошибку вместо проглатывания, а
  `run_agent_loop` отвечает явным отказом о незаписанном исходе. Если durable-позицию
  нельзя записать, последующая доставка прочитает более раннее состояние и может быть
  снова допущена, поэтому run не выдаётся за успех, за который код не может отвечать.
- **Следующая попытка требует новой идентичности операции, а не перезаписи записи.**
  `FAILED` и `LIMIT_REACHED` терминальны по той же причине, что и
  `SECURITY_FAILURE`: честный способ повторить — новый ключ идемпотентности, то есть
  явное решение оператора, а не переоткрытие записи.
- **Resume восстанавливает позицию, никогда разрешение.** Записанный fingerprint
  операции должен быть воспроизведён текущим authority ceiling, иначе resume
  отклоняется.
- **Guard инъектируется.** `ForgeApiService` принимает явный `idempotency_guard`
  вместо чтения process-wide singleton, поэтому тест или встраивающая композиция не
  может затронуть состояние вне своего журнала.
- **Идемпотентность стоит на точке входа, и claim предшествует любой durable-записи.**
  Три доверенные in-process точки входа (`run_agent_loop`, `run_accepted_task`,
  `run_declared_verification`) заявляют операцию после появления доверенной
  идентичности и до frozen scope, привязки `RunStore` и первого durable-события.
  Поэтому повторная доставка не пишет `RUN_STARTED` и не фризит scope. Выше граница
  оказалась бы там, где идентичность ещё недоверенная; ниже — там, где эффект уже
  произошёл.
- **Один класс операции на точку входа.** Каждая точка входа заявляет операцию под
  своим `OperationClass`, поэтому один ключ никогда не позволит операции agent loop
  закрыть accepted task или declared verification. Это изоляция идентичности, а не
  authority: классы решают, к какой записи обращается ключ, и никогда — что можно
  исполнять.
- **`run_task` и его HTTP/desktop-вызывающие намеренно исключены.** Их `task_id`
  поставляет вызывающий, поэтому доверенной идентичности задачи для привязки ключа
  нет. Привязка там позволила бы вызывающему выбирать идентичность операции и
  позволила бы двум вызывающим столкнуться на одном ключе. Сначала им нужна
  server-derived идентичность операции — это отдельная работа.
- **`purpose_run_id` остаётся только correlation.** Он записывается как
  санитизированные метаданные и никогда не входит в идентичность операции, поэтому
  другой `purpose_run_id` не может купить второе исполнение verification под тем же
  ключом.

### Отклонённые альтернативы

- **«Exactly once».** Отклонено: здесь это недоказуемо, и заявлять это было бы ложью.
  Честный потолок — at-most-once на ключ идемпотентности.
- **Считать нечитаемый журнал «записи нет».** Отклонено: это ровно то, из-за чего
  происходит дублирующее исполнение. Журнал, к которому нельзя обратиться, даёт
  отказ, а не молчаливый старт.
- **Использовать `run_id` как идентичность идемпотентности.** Отклонено: production
  генерирует новый `run_id` на каждый вызов (`run-loop-<uuid>`,
  `run-accept-<uuid>` и так далее), поэтому привязка идемпотентности к нему делала бы
  каждый дубликат «новым».
- **Позволить задаче или запросу объявлять операцию идемпотентной.** Отклонено: это
  передало бы authority-образный смысл недоверенному входу. Идентичность составляют
  только доверенная декларация плюс frozen привязка.
- **Сохранять операцию, чтобы переигрывать её напрямую.** Отклонено: никакого pickle,
  сериализованного authority, команды, workspace или окружения. Resume пересобирает
  `AuthorizedExecution` из текущего доверенного `RunScope`.
- **Расширить `RunStore` до resume-движка.** Отклонено: это превратило бы observation
  sink во второй путь принятия решений.

### Следствия и открытые пункты

1. **Идемпотентность покрывает три доверенные in-process точки входа и только при
   переданном ключе.** Без ключа каждый вызов сохраняет прежнюю семантику ровно, и
   именно это сохраняет legacy поведение.
2. **`run_accepted_task`, `run_declared_verification`, `run_task`, HTTP-путь task run
   и desktop dispatch пока не обращаются к журналу.** Они не защищены, а не
   затронуты; перевод их на ту же модель идентичности — последующая работа.
3. **Concurrency ограничена, а не полна.** Атомарный claim гарантирован; lease,
   compare-and-set и межпроцессная координация — нет, и это зафиксировано как
   ограничение, а не сымитировано. Обновления используют атомарную замену файла, но
   это не защищённый CAS read-modify-write, поэтому конкурентные обновления одного
   ключа могут потерять обновление, а Windows может выдать `PermissionError` при
   конкурентной замене. Это ограничения liveness и восстановления, а не расширение
   authority.
4. **Interlock неизвестного эффекта привязан к ключу.** Тот же ключ плюс
   недоказуемый эффект отклоняется и для resume, и для нового старта; другой ключ для
   той же задачи — новая идентичность операции, и сейчас он допускается. Смена ключа —
   решение уровня оператора, и реализация не даёт глобального подавления на уровне
   действия между ключами. Новый ключ не делает предыдущий неизвестный эффект
   безопасным — он делает его неуправляемым. Идентичность уровня действия и подавление
   — последующая работа.
5. **Журнал side effects не подключён к production.** Двухфазная запись и
   `UNKNOWN_AFTER_CRASH` реализованы, протестированы и недостижимы из текущего
   production-пути run. Пока они не подключены, крах внутри side effect не управляется
   журналом; run защищает только run-level claim.
6. **Подсистема PARTIAL по каждому измерению** — run idempotency, resume, execution
   idempotency, tool idempotency и concurrency — а интеграция журнала NOT WIRED.
4. **Resume валидируется, но пока не вызывается точкой входа.** Контракт реализован и
   протестирован; подключение операторского вызова resume — последующая работа,
   намеренно не протащенная в этот блок.
5. **`RunStore` остаётся observation sink и не изменён**, поэтому история run
   сохраняет прежний смысл и прежние тесты.
