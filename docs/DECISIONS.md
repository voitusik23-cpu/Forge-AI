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
- **Overall status of this contract: PARTIAL, with fingerprint and profile-environment items closed.** The coordinator requires a non-empty approval fingerprint matching the intent, and the resolved profile environment is included in the intent fingerprint while remaining an authorization declaration rather than child-process payload. The remaining open item is the capability recognition-list residual; it is recorded in `SOURCE_OF_TRUTH.md` §4.

### Rejected or deferred alternatives (with rationale)

- **Wrapper blacklists.** Extending `dangerous_interpreter_argv` or adding `env`/`sudo`/`make`/`npx` style name lists was rejected as a security strategy. A list of programs that can execute other programs is not enumerable, and its incompleteness fails open for unlisted names. The current implementation still contains a recognition list, which is recorded as a limitation, not as the intended model.
- **Cryptographic approval tokens / HMAC-minted authorization tokens.** Rejected for this block: Python cannot provide in-process non-forgeability, and a token would create a false impression of resistance to an adversary it cannot stop. A private marker plus a private adapter was chosen instead.
- **Whole-workspace and whole-profile content digests.** Rejected as default binding: they create approval churn without adding authority. Scoped digests for referenced inputs remain a deferred candidate.
- **A declarative field registry with per-field justification strings.** Rejected in favor of the intent body being the projection plus a frozen field-name test, to avoid two sources of truth that can disagree.
- **Storing approval authority in the resolver.** Rejected: the resolver is an untrusted proposer, not the verifier.
- **OS-level sandboxing (namespaces, cgroups, Job Objects как границы изоляции used as an isolation boundary), kernel network enforcement, and container/VM backends.** Deferred to Block 2 or a separate project. A sandbox changes the consequences of a grant; it does not decide which invocations are admissible, and it must not be treated as a substitute for authorization.
- **Durable/persistent approval store with expiration.** Deferred as a separate design: a persistent, expiring approval is a standing authorization with different semantics from a single-use approval event, and must not be introduced as an incidental resolver improvement.
- **MCP as the permission model.** Rejected: authorization belongs to Forge's Permission -> Approval -> Policy -> Execution boundaries. MCP may later be a tool transport under that authority. See [`TECHNOLOGY_RADAR.md`](TECHNOLOGY_RADAR.md).
- **Universal retries, rollback, parallelism and swarm execution.** Rejected as defaults: complexity and cost are not justified by default, and more agents do not mean better results.
- **Unifying `app/tools/workspace.py` with `app/execution/paths.py` in this block.** Completed as a focused follow-up: `Workspace` delegates normalization to the shared canonical validator while retaining its tool-boundary checks.

### Open items for the next authorization block

1. Replace the capability recognition list with a fail-closed declared-invocation model so that unrecognized execution behavior requires an explicit capability.
2. Decide whether declared-input content digests are mandatory for invocations that execute workspace content.

### Program identity binding

The logical executable remains the profile and permission identity, while
`IntentBuilder` resolves a bare executable to a concrete path and includes that
`resolved_executable` in the immutable intent fingerprint. The adapter launches
the pinned path. This closes PATH changes after approval; the small filesystem
replacement window between resolution and spawn remains an OS-level residual.

**Пиннинг — это рубеж, а не описание.** Bare-имя, которое не удалось разрешить в абсолютный путь, остаётся с пустым `resolved_executable`, и `LocalExecutionAdapter` отказывается запускать незапинованную bare-команду до того, как процесс появится. Тотальность `IntentBuilder` намеренна: решение политики о такой команде должно по-прежнему уметь ответить «эта команда не разрешена», а не падать при её описании. `PATH`, объявленный явно пустым, соблюдается как «не искать нигде», а не подменяется `PATH` процесса.

**Pinning is a gate, not a description.** A bare executable that cannot be resolved to an absolute path is left with an empty `resolved_executable`, and `LocalExecutionAdapter` refuses to launch an unpinned bare command before a process exists. Keeping `IntentBuilder` total is deliberate: a policy decision about such a command must still be able to answer "that command is not allowed" instead of failing while describing it. A `PATH` declared as explicitly empty is honoured as "search nowhere" rather than replaced by the process `PATH`.

### Привязка идентичности программы

Логическое имя executable остаётся идентичностью профиля и permission, а
`IntentBuilder` разрешает bare executable в конкретный путь и включает
`resolved_executable` в fingerprint неизменяемого intent. Adapter запускает
закреплённый путь. Это закрывает изменение PATH после approval; небольшое окно
замены файла между разрешением и запуском остаётся OS-level residual.

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
- **Общий статус контракта: PARTIAL; пункты о fingerprint и окружении профиля закрыты.** Координатор требует непустой fingerprint одобрения, совпадающий с интентом, а разрешённое окружение профиля входит в fingerprint интента и остаётся декларацией авторизации, а не payload дочернего процесса. Открытым остаётся остаточный риск списка распознавания capabilities; он зафиксирован в `SOURCE_OF_TRUTH.md` §4.

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
- **Объединение `app/tools/workspace.py` и `app/execution/paths.py` в этом блоке.** Выполнено отдельным сфокусированным изменением: `Workspace` делегирует нормализацию общему canonical validator, сохраняя собственную проверку tool boundary.

### Открытые вопросы для следующего блока авторизации

1. Заменить список распознавания capabilities на fail-closed модель объявленных инвокаций, чтобы нераспознанное поведение требовало явной capability.
2. Решить, обязательны ли digest'ы объявленных входов для инвокаций, исполняющих содержимое workspace.

## Workspace identity binding v0.1

- **A frozen `RunScope` binds the workspace by filesystem identity as well as canonical path.** `WorkspaceIdentity` captures the canonical root, `st_dev`, and `st_ino`; `validate_workspace_root` rejects a directory replacement at the same pathname. This closes path-only replacement without introducing a second authorization system. The execution intent remains the authority subject for the command itself.
- **Замороженный `RunScope` связывает workspace не только canonical path, но и идентичностью файловой системы.** `WorkspaceIdentity` сохраняет canonical root, `st_dev` и `st_ino`; `validate_workspace_root` отклоняет замену каталога по тому же пути. Это закрывает замену только по pathname без введения второй системы авторизации. Execution intent остаётся субъектом авторизации самой команды.

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
7. **Resume валидируется, но пока не вызывается точкой входа.** Контракт реализован и
   протестирован; подключение операторского вызова resume — последующая работа,
   намеренно не протащенная в этот блок.
8. **`RunStore` остаётся observation sink и не изменён**, поэтому история run
   сохраняет прежний смысл и прежние тесты.


## Forge Platform Stage 0 — Architecture Decisions (D-PLATFORM-01..12)

Frozen before any Platform implementation begins. Baseline `0161d24`. These
decisions are **architectural direction**, not implementation claims: nothing in
them is built yet, and the existing status of any component is unchanged by this
record. Each decision states what exists today so the direction cannot be misread
as delivered behaviour.

### Accepted decisions

- **D-PLATFORM-01 — Hybrid persistence.** Platform uses PostgreSQL for durable
  platform state: users, organizations, memberships, projects, API keys, provider
  account metadata, `RunRecord`, `UsageRecord`, and future billing entities. Core
  keeps its autonomy and keeps using its local workspace, snapshots, `RunStore`,
  the local execution event log, and the local execution/idempotency
  infrastructure. **Core must not import PostgreSQL, SQLAlchemy, psycopg, Alembic,
  Stripe, or any Platform module.** Verified today: `sqlite3`, `sqlalchemy`,
  `psycopg`, and `alembic` have **zero** imports in `app/` and `tests/`.
- **D-PLATFORM-02 — `Run` vs `RunRecord`.** `Run`
  (`app/orchestrator/models.py`) stays the mutable in-memory runtime object of
  Core. `RunRecord` is the durable Platform record of the outer lifecycle. They
  share exactly one stable correlation identity: the Core `run_id`. Platform never
  mutates a Core `Run`; Core never reads a Platform `RunRecord` to make an
  execution decision.
- **D-PLATFORM-03 — Execution state ownership.** Core owns the internal execution
  flow and the `AgentHarness` phases. Platform owns the outer lifecycle
  (`QUEUED`/`RUNNING`/terminal). **No second source of truth for the same state.**
  `RunStore` remains Core execution observation and audit infrastructure; it does
  not become a Platform database and does not become an authorization engine. This
  preserves the invariant `RunStore` already documents about itself: it is
  deliberately not an execution authority and not a resume engine.
- **D-PLATFORM-04 — Organization-first.** A `User` never directly owns a
  `Project`. Registration atomically creates a `User`, a **Personal
  Organization**, and an `OWNER` `Membership`. `Project` and `Wallet` belong to the
  `Organization`. Rationale: `project_id` is already threaded through more than ten
  call sites as a free-form string, so choosing `Project -> User` now would force a
  rewrite of every one of them at the first B2B requirement.
- **D-PLATFORM-05 — Tenant authority separation.** A client-supplied `project_id`
  or `organization_id` is **never** proof of ownership and **never** execution
  authority. The only permitted derivation is:

  ```
  authenticated identity -> Membership -> Organization -> Project
      -> server-derived Workspace -> RunScope -> execution
  ```

  `request.project_id -> Workspace` is forbidden: it would become cross-tenant file
  access at the first transition to a per-project workspace. `AI decision !=
  authorization`, and `authorization != execution authority`. Note the current
  reality this guards: `TaskRunRequest.project_id` is supplied by the caller, and
  today it is harmless **only** because `RunScope` has no `project_id` field and
  `ForgeApiService` holds one constant workspace. That accident must not be relied
  on.
- **D-PLATFORM-06 — Physical usage vs financial data.** Core is responsible for
  physical telemetry only. The minimum `Usage` contract is: `run_id`,
  `attempt_number`, `provider_name`, `model_name`, `input_tokens`,
  `output_tokens`, `cached_tokens`, `duration`, `request_count`, plus
  success/error/fallback metadata where needed. **Core must not compute retail
  price, charge, or wallet balance.** All of those fields already exist today
  except `tool_call_count`, and they live inside `AttemptUsageRecord` /
  `RunAccountingRecord`.
- **D-PLATFORM-07 — Usage / Cost / Price / Charge / Ledger separation.** `Usage` is
  physical measurement. `Cost` is Forge COGS. `Price` is the retail pricing rule.
  `Charge` is the customer financial obligation. `Ledger` is the immutable
  accounting record. These five concepts must never be merged. Today
  `AttemptUsageRecord` carries both physical fields and `estimated_cost` /
  `provider_reported_cost`, so this separation is a **split of an existing object**,
  not an addition on top of it.
- **D-PLATFORM-08 — Money representation.** Financial fields **must not** use
  `float`. The internal representation is fixed as **integer minor units /
  micro-credits**. The external JSON/API representation is left as a separate
  implementation decision and is not needed yet. This is a hard constraint because
  the existing cost fields are `Optional[float]` and are `round(..., 6)`-ed today.
- **D-PLATFORM-09 — `ProviderAccount`.** Do not create a duplicate
  `ProviderCredential` entity. The existing
  `app/agents/providers/models.py::ProviderAccount` remains the canonical provider
  account model. `secret_ref` stays a **reference value**, and its future contract
  must support an abstract secret-reference scheme (for example `env:` / `vault:` /
  `kms:`), extending the current environment-variable-name validation rather than
  replacing the field. **Open provider secrets are never part of durable Platform
  records.** Today `secret_ref` is validated as an uppercase environment variable
  name, and that is already a reference model, not a value store.
- **D-PLATFORM-10 — Core independence.** Core must remain runnable without
  Platform, without PostgreSQL, without Stripe, without mandatory network, and
  without authentication. Platform may **refuse** a run; Platform may **not**
  widen Core execution authority. This preserves `AuthorizedExecution` as the only
  execution authority.
- **D-PLATFORM-11 — Future billing boundary.** Billing is **not** implemented now.
  The architectural direction is fixed as:

  ```
  Usage -> CostRecord -> Pricing -> Charge -> immutable Ledger
  ```

  `Wallet`, compare-and-swap, pre-authorization, and payment remain future stages.
- **D-PLATFORM-12 — Deferred scope.** Not implemented at this stage: Stripe;
  subscriptions; invoices; partner payouts; reseller; distributor; MLM;
  KeyCore-Hub dependency; and migration of Core artifacts/snapshots into
  PostgreSQL. Note that `partner`, `reseller`, `affiliate`, `referral`, and
  `commission` have **zero** mentions in `app/` today, so declining them removes
  nothing that exists.

### Rejected alternatives

- **Platform-owned execution authority.** Rejected: it would create a second
  authority beside `AuthorizedExecution` and break `identity != authorization !=
  execution authority`.
- **`Project` owned by `User`.** Rejected: forces a full schema, billing, and
  authorization rewrite at the first team/B2B requirement.
- **Storing retail or charged amounts in Core.** Rejected: Core cannot know a
  tariff, a margin, or a wallet, and a price change would retroactively alter
  immutable telemetry.
- **A second `ProviderCredential` entity.** Rejected: duplicates the existing
  `ProviderAccount` and splits credential ownership in two.
- **PostgreSQL for Core state.** Rejected: Core must stay runnable as a library
  with no database. The existing file-based `RunStore` and `IdempotencyLedger`
  already write outside the source checkout and keep Core autonomous.
- **Migrating Core artifacts and snapshots into PostgreSQL now.** Rejected: it
  gives Platform ownership of Core observability before the boundary is proven.
- **Floating-point money.** Rejected: the existing cost fields are already floats;
  adding a ledger on top of them would make rounding divergence permanent and
  unauditable.

### Stage 0 implementation gate

Stage 0.1 must not begin until all of the following are documented and agreed:

1. Architecture boundary documented (Core / Platform / Billing / API).
2. Persistence model documented, including the hybrid split and its rationale.
3. `Run` vs `RunRecord` documented, including the single correlation identity.
4. Authority boundary documented, including the tenant derivation chain.
5. `Usage` / `Cost` / `Price` / `Charge` / `Ledger` boundary documented.
6. Money representation documented (integer minor units / micro-credits, no float).
7. `ProviderAccount` / `secret_ref` migration direction documented.
8. Core independence invariant documented.

Status of this gate at the time of writing: **all eight items are documented by
this record.** The gate is a documentation precondition only; it does not assert
that any Platform component exists.

### Consequences and open items

1. **Nothing in D-PLATFORM-01..12 is implemented.** The Platform layer, PostgreSQL
   persistence, tenant identity, `RunRecord`, `UsageRecord`, and billing do not
   exist in this repository. This record fixes direction, not delivery.
2. **Three existing Core constructs carry Platform or financial semantics and will
   need a split before billing is possible.** `AttemptUsageRecord` mixes physical
   telemetry with `estimated_cost` / `provider_reported_cost`;
   `RunAccountingRecord` carries `project_id`, a Platform ownership concept; and
   `app/dashboard/` plus `app/dashboard/provider_health.py` are customer-facing
   analytics and provider-balance operations rather than Core.
3. **Usage currently survives only in memory.** `CapabilityFabric._run_usage` is a
   `defaultdict(list)`. `RunStateSnapshot` has an `accounting` field that no
   production path populates, so the record is discarded with the process. Durable
   physical telemetry is therefore the first prerequisite for anything financial.
4. **`provider_reported_cost` is currently a misleading name.**
   `app/orchestrator/dispatcher.py` assigns it from the model's own
   `estimated_cost`. It is not provider-confirmed cost and must not be used as the
   basis of a charge.
5. **`tool_call_count` is required by D-PLATFORM-06 and does not exist yet**
   (zero occurrences in `app/`). It is a Core-side measurement addition.
6. **`allow_paid_providers` is a process-global flag** in `RuntimeSettings` and
   `FabricRequest`. It has no owner, so it cannot express a per-tenant policy and
   will have to move or be superseded.
7. **These decisions do not change any current behaviour.** No code, no test, and
   no runtime path is modified by this record.


## Forge Platform Stage 0 — архитектурные решения (D-PLATFORM-01..12) — русская версия

Зафиксировано до начала любой реализации Platform. Базовая точка — `0161d24`. Эти
решения задают **архитектурное направление**, а не заявляют реализацию: ничего из
перечисленного ещё не построено, и текущий статус любого компонента этим документом
не меняется. Каждое решение отдельно указывает, что существует сегодня, чтобы
направление нельзя было прочитать как уже поставленное поведение.

### Принятые решения

- **D-PLATFORM-01 — гибридная персистентность.** Platform использует PostgreSQL для
  durable platform state: users, organizations, memberships, projects, API keys,
  метаданные провайдерских аккаунтов, `RunRecord`, `UsageRecord` и будущие
  billing-сущности. Core сохраняет автономность и продолжает использовать локальный
  workspace, snapshots, `RunStore`, локальный журнал событий исполнения и локальную
  инфраструктуру execution/idempotency. **Core не должен импортировать PostgreSQL,
  SQLAlchemy, psycopg, Alembic, Stripe или любой модуль Platform.** Проверено
  сегодня: `sqlite3`, `sqlalchemy`, `psycopg` и `alembic` имеют **ноль** импортов в
  `app/` и `tests/`.
- **D-PLATFORM-02 — `Run` против `RunRecord`.** `Run`
  (`app/orchestrator/models.py`) остаётся mutable in-memory runtime-объектом Core.
  `RunRecord` — durable-запись Platform о внешнем жизненном цикле запуска. У них
  ровно одна стабильная корреляционная идентичность: Core `run_id`. Platform никогда
  не мутирует Core `Run`; Core никогда не читает Platform `RunRecord` для принятия
  execution-решений.
- **D-PLATFORM-03 — владение состоянием исполнения.** Core владеет внутренним потоком
  исполнения и фазами `AgentHarness`. Platform владеет внешним жизненным циклом
  (`QUEUED`/`RUNNING`/terminal). **Не создавать второй источник истины для одного и
  того же состояния.** `RunStore` остаётся инфраструктурой наблюдения и аудита
  исполнения Core; он не становится базой данных Platform и не становится движком
  авторизации. Это сохраняет инвариант, который `RunStore` уже заявляет о себе: он
  намеренно не является execution authority и не является resume-движком.
- **D-PLATFORM-04 — Organization-first.** `User` никогда напрямую не владеет
  `Project`. Регистрация атомарно создаёт `User`, **Personal Organization** и
  `OWNER` `Membership`. `Project` и `Wallet` принадлежат `Organization`. Обоснование:
  `project_id` уже протянут более чем через десять мест как свободная строка, и выбор
  `Project -> User` сейчас потребовал бы переписать каждое из них при первом же
  B2B-требовании.
- **D-PLATFORM-05 — разделение tenant authority.** Клиентский `project_id` или
  `organization_id` **никогда** не является доказательством владения и **никогда** не
  является execution authority. Единственно допустимый вывод:

  ```
  аутентифицированная идентичность -> Membership -> Organization -> Project
      -> server-derived Workspace -> RunScope -> исполнение
  ```

  `request.project_id -> Workspace` запрещено: при первом же переходе к
  per-project workspace это стало бы межарендаторным доступом к файлам.
  `AI decision != authorization`, а `authorization != execution authority`. Отметим
  сегодняшнюю реальность, которую это защищает: `TaskRunRequest.project_id`
  поставляет вызывающий, и сегодня это безвредно **только** потому, что у `RunScope`
  нет поля `project_id`, а `ForgeApiService` держит один постоянный workspace. На эту
  случайность нельзя опираться.
- **D-PLATFORM-06 — физическое потребление против финансовых данных.** Core отвечает
  только за physical telemetry. Минимальный контракт `Usage`: `run_id`,
  `attempt_number`, `provider_name`, `model_name`, `input_tokens`, `output_tokens`,
  `cached_tokens`, `duration`, `request_count`, плюс метаданные success/error/fallback
  при необходимости. **Core не должен считать retail price, charge или wallet
  balance.** Все эти поля, кроме `tool_call_count`, существуют сегодня и живут внутри
  `AttemptUsageRecord` / `RunAccountingRecord`.
- **D-PLATFORM-07 — разделение Usage / Cost / Price / Charge / Ledger.** `Usage` —
  физическое измерение. `Cost` — себестоимость Forge (COGS). `Price` — правило
  розничного ценообразования. `Charge` — финансовое обязательство клиента.
  `Ledger` — неизменяемая бухгалтерская запись. Эти пять понятий нельзя смешивать
  никогда. Сегодня `AttemptUsageRecord` несёт одновременно физические поля и
  `estimated_cost` / `provider_reported_cost`, поэтому это разделение — **разрезание
  существующего объекта**, а не добавление поверх него.
- **D-PLATFORM-08 — представление денег.** Финансовые поля **не должны** использовать
  `float`. Внутреннее представление фиксируется как **целые минорные единицы /
  micro-credits**. Внешнее представление JSON/API остаётся отдельным
  implementation-решением и пока не требуется. Это жёсткое ограничение, потому что
  существующие cost-поля — `Optional[float]` и сегодня округляются через
  `round(..., 6)`.
- **D-PLATFORM-09 — `ProviderAccount`.** Не создавать сущность-дубль
  `ProviderCredential`. Существующий
  `app/agents/providers/models.py::ProviderAccount` остаётся канонической моделью
  провайдерского аккаунта. `secret_ref` остаётся **ссылочным значением**, и его
  будущий контракт должен поддерживать абстрактную схему secret reference (например
  `env:` / `vault:` / `kms:`), расширяя текущую валидацию имени переменной окружения,
  а не заменяя поле. **Открытые провайдерские секреты никогда не являются частью
  durable-записей Platform.** Сегодня `secret_ref` валидируется как имя переменной
  окружения в верхнем регистре, и это уже ссылочная модель, а не хранилище значений.
- **D-PLATFORM-10 — независимость Core.** Core должен оставаться запускаемым без
  Platform, без PostgreSQL, без Stripe, без обязательной сети и без аутентификации.
  Platform может **отказать** в запуске; Platform **не может** расширить execution
  authority Core. Это сохраняет `AuthorizedExecution` единственной execution
  authority.
- **D-PLATFORM-11 — будущая граница биллинга.** Billing **сейчас не реализуется**.
  Архитектурное направление фиксируется так:

  ```
  Usage -> CostRecord -> Pricing -> Charge -> immutable Ledger
  ```

  `Wallet`, compare-and-swap, pre-authorization и платежи остаются будущими этапами.
- **D-PLATFORM-12 — отложенная область.** На текущем этапе не реализуется: Stripe;
  подписки; инвойсы; выплаты партнёрам; reseller; distributor; MLM; зависимость от
  KeyCore-Hub; перенос Core artifacts/snapshots в PostgreSQL. Отметим, что `partner`,
  `reseller`, `affiliate`, `referral` и `commission` сегодня имеют **ноль** упоминаний
  в `app/`, поэтому отказ от них не удаляет ничего существующего.

### Отклонённые альтернативы

- **Execution authority, принадлежащая Platform.** Отклонено: это создало бы вторую
  authority рядом с `AuthorizedExecution` и сломало бы
  `identity != authorization != execution authority`.
- **`Project`, принадлежащий `User`.** Отклонено: вынуждает полную переписку схемы,
  биллинга и авторизации при первом же требовании командной работы/B2B.
- **Хранение retail- или списанных сумм в Core.** Отклонено: Core не может знать
  тариф, маржу или кошелёк, а изменение цены задним числом изменило бы неизменяемую
  телеметрию.
- **Вторая сущность `ProviderCredential`.** Отклонено: дублирует существующий
  `ProviderAccount` и расщепляет владение кредами на две части.
- **PostgreSQL для состояния Core.** Отклонено: Core должен оставаться запускаемым как
  библиотека без базы данных. Существующие файловые `RunStore` и `IdempotencyLedger`
  уже пишут вне исходного checkout и сохраняют автономность Core.
- **Перенос Core artifacts и snapshots в PostgreSQL сейчас.** Отклонено: это отдаёт
  Platform владение наблюдаемостью Core до того, как граница доказана.
- **Деньги с плавающей точкой.** Отклонено: существующие cost-поля уже float;
  добавление ledger поверх них сделало бы расхождения округления постоянными и
  неаудируемыми.

### Stage 0 implementation gate

Stage 0.1 не должен начинаться, пока всё перечисленное не задокументировано и
согласовано:

1. Граница архитектуры задокументирована (Core / Platform / Billing / API).
2. Модель персистентности задокументирована, включая гибридное разделение и его
   обоснование.
3. `Run` против `RunRecord` задокументировано, включая единую корреляционную
   идентичность.
4. Граница authority задокументирована, включая цепочку вывода tenant'а.
5. Граница `Usage` / `Cost` / `Price` / `Charge` / `Ledger` задокументирована.
6. Представление денег задокументировано (целые минорные единицы / micro-credits, без
   float).
7. Направление миграции `ProviderAccount` / `secret_ref` задокументировано.
8. Инвариант независимости Core задокументирован.

Состояние этого gate на момент написания: **все восемь пунктов задокументированы этой
записью.** Gate — это только документационное предусловие; он не утверждает, что
какой-либо компонент Platform существует.

### Следствия и открытые пункты

1. **Ничто из D-PLATFORM-01..12 не реализовано.** Слоя Platform, персистентности
   PostgreSQL, tenant-идентичности, `RunRecord`, `UsageRecord` и биллинга в этом
   репозитории не существует. Эта запись фиксирует направление, а не поставку.
2. **Три существующих конструкции Core несут платформенную или финансовую семантику,
   и до биллинга их потребуется разрезать.** `AttemptUsageRecord` смешивает
   физическую телеметрию с `estimated_cost` / `provider_reported_cost`;
   `RunAccountingRecord` несёт `project_id` — платформенную концепцию владения; а
   `app/dashboard/` и `app/dashboard/provider_health.py` — это customer-facing
   аналитика и операции с балансом провайдера, а не Core.
3. **Сегодня Usage существует только в памяти.** `CapabilityFabric._run_usage` — это
   `defaultdict(list)`. У `RunStateSnapshot` есть поле `accounting`, которое не
   заполняет ни один production-путь, поэтому запись исчезает вместе с процессом.
   Следовательно, durable physical telemetry — первое предусловие для всего
   финансового.
4. **`provider_reported_cost` сегодня — вводящее в заблуждение имя.**
   `app/orchestrator/dispatcher.py` присваивает ему собственную `estimated_cost`
   модели. Это не подтверждённая провайдером себестоимость, и это нельзя использовать
   как основу списания.
5. **`tool_call_count` требуется D-PLATFORM-06 и пока не существует** (ноль
   вхождений в `app/`). Это добавление измерения на стороне Core.
6. **`allow_paid_providers` — процесс-глобальный флаг** в `RuntimeSettings` и
   `FabricRequest`. У него нет владельца, поэтому он не может выражать политику
   отдельного арендатора и должен быть перемещён или заменён.
7. **Эти решения не меняют текущее поведение.** Ни код, ни тесты, ни один
   runtime-путь этой записью не изменяются.


## Physical Execution Telemetry v0.1 (D-PLATFORM-13)

Implements the first half of the Stage 0.1 boundary frozen in `D-PLATFORM-06` and
`D-PLATFORM-07`, at baseline `0161d24`.

### Accepted decisions

- **The physical contract is separate from the accounting record.**
  `app/agent_runtime/physical_telemetry.py::PhysicalTelemetry` is a frozen value
  carrying identity and measurement only: `run_id`, `attempt_number`,
  `provider_name`, `model_name`, `input_tokens`, `output_tokens`, `cached_tokens`,
  `duration_seconds`, `success`, `fallback`, `tool_call_count`, `completed_at`,
  `error_type`, and a derived `attempt_id`. It has no `to_dict` passthrough, so a
  caller cannot smuggle an extra field into the durable record. The existing
  `AttemptUsageRecord` / `RunAccountingRecord` are **not** deleted: they keep
  serving runtime reporting and the dashboard. The new durable path simply does
  not depend on their financial fields.
- **There is one attempt identity, and it is the existing one.**
  `PhysicalTelemetry.identity` returns `<run_id>#attempt-<n>`, which is exactly
  `AttemptIdentity.attempt_id`. No second identity format was introduced.
- **The sink is a port with a transitional local adapter.**
  `PhysicalTelemetrySink` is a one-method protocol (`record` -> `bool`, raising on
  failure). `FileTelemetrySink` writes one JSON line per terminal attempt to
  `<root>/<run_id>.jsonl`, append-only, UTF-8, `sort_keys=True`, explicit bounded
  serialization, no unsafe object serialization, no dynamic evaluation. The root
  follows the existing `RunStore` convention and its own
  `FORGE_TELEMETRY_ROOT`, and never lands inside the source checkout or the user's
  workspace. This file is explicitly transitional infrastructure until a Platform
  persistence layer exists.
- **Terminal attempts only.** A measurement is written when an attempt reaches a
  genuinely finished outcome: `COMPLETED`, `FAILED`, or `LIMIT_REACHED`.
  `WAITING_FOR_APPROVAL` is terminal for the run loop but the attempt is not
  finished, and a run that raises is not recorded at all. Writing either would
  assert an outcome that did not happen.
- **Idempotent per attempt, including across a restart.** The sink refuses a second
  record for an identity it has already written, and it seeds that knowledge from
  the file, so re-persisting after a process restart is still a no-op. There is no
  distributed compare-and-set, no lease, and no database uniqueness: local file
  idempotency follows the existing durability primitives of this project.
- **A missing measurement is never invented.** Tokens are accumulated only from a
  `Usage` the decision provider actually reports. A provider that reports nothing
  contributes nothing and is recorded as measured zero, not as an estimate.
  `tool_call_count` is the measured number of tool invocations the attempt
  recorded a bounded result for; a measured zero means no tool ran.
- **Every number is finite and bounded.** `duration_seconds` must be a finite,
  non-negative number: `NaN`, `+inf`, and `-inf` are refused, because `NaN`
  compares false against a plain non-negativity check and `json.dumps` would then
  write a bare `NaN` token that is not valid JSON and that a strict reader in
  another language rejects. The writer additionally uses `allow_nan=False`, so the
  durable log cannot contain `NaN` or `Infinity` even if a value bypassed
  construction. Counters are bounded by three documented canonical constants, all
  enforced on every construction path including deserialization:
  `MAX_TOKEN_COUNT = 10**12` for `input_tokens`, `output_tokens`, and
  `cached_tokens`; `MAX_TOOL_CALL_COUNT = 10**6` for `tool_call_count`, which is
  bounded separately and much lower because tool invocations are bounded by the run
  loop and a token budget says nothing about how many tools may run;
  `MAX_ATTEMPT_NUMBER = 10**6` for `attempt_number`, which advances only on resume.
  The token ceiling keeps five to six orders of magnitude of headroom over any real
  workload, stays exactly representable, and fits well inside a 64-bit signed
  integer, so it cannot overflow a downstream counter. These are measurement
  bounds: they grant, deny, and budget nothing, and they never reach the execution
  authority chain.
- **A write failure is reported, and it does not abort the run.** The port's
  failure contract is explicit and enforced by the caller, not merely by
  convention: a sink *should* raise `TelemetryWriteError`, and any other exception
  it raises is normalized into `TelemetryWriteError` with the original kept as
  `__cause__`. The normalization wraps **only the sink call**, so an
  execution-stage failure elsewhere in the run keeps propagating as itself and can
  never be relabelled as a telemetry failure. The harness then reports a
  `physical_telemetry_write_failed` event, carrying the original error's type as
  `cause_category`, and keeps the run's own outcome. A dropped record is never
  silent, and telemetry can never destroy an already-determined terminal outcome.
- **The measurement carries no authority.** The `AgentHarness` builds it from
  counts and durations it already holds. Nothing in `decision -> authorization ->
  execution` changed: `AuthorizedExecution` stays the only execution authority,
  the `RunScope` is untouched, the sandbox and network policy are untouched, and
  approval semantics are untouched. Supplying a sink cannot widen what a run may
  do.
- **The sink is a composition dependency, like the run store and the idempotency
  guard.** `create_agent_harness` supplies a default `FileTelemetrySink`, and
  `ForgeApiService` accepts an explicit `telemetry_sink` and passes it to the
  harness factory it uses for both the agent-loop and acceptance slices. An
  embedding composition therefore records into its own storage rather than the
  process default.
- **Where the measurement comes from.** `AIDecisionProvider` is the only decision
  provider in this repository that performs a real provider call, so it now retains
  the response's physical measurement (`last_usage`, `last_provider_name`,
  `last_model_name`) instead of discarding it. The harness accumulates it per
  attempt and measures the attempt once, at the terminal boundary. Nothing else in
  that class changed: it remains advisory and holds no authority.

### Rejected alternatives

- **Writing telemetry at decision time.** Rejected: the decision is not the
  terminal outcome of the attempt, so a record written there would claim a result
  that had not happened.
- **Reusing `RunAccountingRecord` as the durable record.** Rejected: it carries
  `estimated_cost`, `provider_reported_cost`, `effective_cost`, and `project_id`,
  so persisting it would put money and ownership into the physical path. That is a
  direct `D-PLATFORM-06` / `D-PLATFORM-07` violation.
- **Deleting the financial fields from `AttemptUsageRecord` now.** Rejected for
  this block: the dashboard and runtime reporting still consume them, so removing
  them would be a refactor with its own compatibility risk. The split was achieved
  by giving the durable path its own contract instead.
- **A second telemetry stream beside the harness's events.** Rejected: the
  measurement is emitted through the harness's existing collector, so there is one
  event stream and one measurement, not two competing records of the same attempt.
- **Recording a run that raised.** Rejected: an exception is not a terminal
  outcome, and a fabricated failure record would be worse than no record.
- **Defaulting missing tokens to an estimate.** Rejected: a guessed token count is
  a false physical measurement, and the whole point of this boundary is that the
  physical record is trustworthy.

### Consequences and open items

1. **Stage 0.2 is not done.** The trusted execution *port* is only half present:
   `PhysicalTelemetry` is the output half, and there is no
   `TrustedExecutionRequest` input half.
2. **The legacy accounting path is still live and still in memory.**
   `CapabilityFabric._run_usage` remains a `defaultdict(list)`, and the `accounting`
   field of `RunStateSnapshot` is still never populated. This record adds a
   durable physical path; it does not migrate the legacy one.
3. **`provider_reported_cost` is still misleading.**
   `app/orchestrator/dispatcher.py` continues to assign it from the model's own
   `estimated_cost`. That is unchanged by this block and remains a `D-PLATFORM-07`
   open item.
4. **Token measurements are only as good as the decision provider.** The default
   deterministic provider makes no provider call, so a production run using it
   records an honest measured zero. A real AI decision provider records real
   tokens, which
   `tests/test_physical_telemetry.py::ProductionEntryPointTelemetryTests` proves
   end to end through `ForgeApiService.run_agent_loop`.
5. **This is transitional file persistence.** `FileTelemetrySink` is a local
   append-only JSONL adapter behind a port. A Platform persistence layer replaces
   the adapter without touching Core.
6. **No financial semantics were added and none were moved.** Cost, price, charge,
   wallet, credits, billing account, project ownership, organization ownership, and
   user identity are absent from the contract and asserted absent by tests.
7. **The sink failure contract has a known escalation point.** On this stage a
   lost measurement never blocks or reclassifies a run, which is correct while no
   money moves. Before a `Charge` can exist, a missing durable `PhysicalTelemetry`
   for an attempt must make the run financially incomplete: either the terminal
   state becomes fail-closed (`physical_telemetry_unavailable`) or the ledger must
   carry a compensating marker that the billing pipeline rejects. Until that
   invariant exists, `PhysicalTelemetry -> UsageRecord -> CostRecord -> Charge`
   would admit a successful execution that is never billed.
8. **Deferred from the Stage 0.1 review, still open:** concurrent writers can
   still produce a duplicate record (no compare-and-set); the `run_id` sanitizer is
   lossy, and `list_run_ids` returns the sanitized stem rather than the original
   id; the caller-supplied `attempt_id` can override the derived identity; string
   fields have no length bound and `error_type` is not canonicalized;
   `completed_at` is not validated; and there is no file rotation or `fsync`.


## Physical Execution Telemetry v0.1 (D-PLATFORM-13) — русская версия

Реализует первую половину границы Stage 0.1, замороженной в `D-PLATFORM-06` и
`D-PLATFORM-07`, на базовой точке `0161d24`.

### Принятые решения

- **Физический контракт отделён от бухгалтерской записи.**
  `app/agent_runtime/physical_telemetry.py::PhysicalTelemetry` — неизменяемое
  значение, несущее только идентичность и измерение: `run_id`, `attempt_number`,
  `provider_name`, `model_name`, `input_tokens`, `output_tokens`, `cached_tokens`,
  `duration_seconds`, `success`, `fallback`, `tool_call_count`, `completed_at`,
  `error_type` и производный `attempt_id`. У него нет passthrough в `to_dict`,
  поэтому вызывающий не может протащить лишнее поле в durable-запись. Существующие
  `AttemptUsageRecord` / `RunAccountingRecord` **не удаляются**: они продолжают
  обслуживать runtime-отчётность и dashboard. Новый durable-путь просто не зависит
  от их финансовых полей.
- **Идентичность попытки одна, и она существующая.**
  `PhysicalTelemetry.identity` возвращает `<run_id>#attempt-<n>`, что в точности
  равно `AttemptIdentity.attempt_id`. Второй формат идентичности не вводился.
- **Sink — это порт с переходным локальным адаптером.** `PhysicalTelemetrySink` —
  протокол из одного метода (`record` -> `bool`, с исключением при сбое).
  `FileTelemetrySink` пишет одну JSON-строку на терминальную попытку в
  `<root>/<run_id>.jsonl`: append-only, UTF-8, `sort_keys=True`, явная
  ограниченная сериализация, без небезопасной объектной сериализации, без
  динамического вычисления. Корень следует существующей конвенции `RunStore` и
  собственной переменной `FORGE_TELEMETRY_ROOT` и никогда не попадает внутрь
  исходного checkout или в пользовательский workspace. Этот файл — явно переходная
  инфраструктура до появления слоя персистентности Platform.
- **Только терминальные попытки.** Измерение записывается, когда попытка достигает
  действительно завершённого исхода: `COMPLETED`, `FAILED` или `LIMIT_REACHED`.
  `WAITING_FOR_APPROVAL` терминально для цикла run, но попытка не завершена, а run,
  завершившийся исключением, не записывается вовсе. Запись любого из них
  утверждала бы исход, которого не было.
- **Идемпотентность по попытке, включая перезапуск.** Sink отказывается писать
  вторую запись для уже записанной идентичности и берёт это знание из файла,
  поэтому повторная запись после перезапуска процесса по-прежнему no-op.
  Распределённого compare-and-set, lease и уникальности в базе данных нет: локальная
  файловая идемпотентность следует существующим примитивам durability этого проекта.
- **Отсутствующее измерение никогда не выдумывается.** Токены накапливаются только
  из `Usage`, которое decision provider действительно сообщает. Провайдер, который
  ничего не сообщает, не даёт ничего и записывается как измеренный ноль, а не как
  оценка. `tool_call_count` — измеренное число вызовов инструментов, для которых
  попытка записала bounded-результат; измеренный ноль означает, что ни один
  инструмент не выполнялся.
- **Каждое число конечно и ограничено.** `duration_seconds` должно быть конечным
  неотрицательным числом: `NaN`, `+inf` и `-inf` отвергаются, потому что `NaN`
  даёт ложь при простой проверке неотрицательности, и тогда `json.dumps` записал бы
  голый токен `NaN`, который не является валидным JSON и который строгий читатель на
  другом языке отвергает. Писатель дополнительно использует `allow_nan=False`,
  поэтому durable-журнал не может содержать `NaN` или `Infinity`, даже если значение
  как-то обошло конструирование. Счётчики ограничены тремя
  документированными каноническими константами, причём на всех путях
  конструирования, включая десериализацию: `MAX_TOKEN_COUNT = 10**12` для
  `input_tokens`, `output_tokens` и `cached_tokens`; `MAX_TOOL_CALL_COUNT = 10**6` для
  `tool_call_count`, который ограничен отдельно и намного ниже, потому что вызовы
  инструментов ограничены циклом run, а бюджет токенов ничего не говорит о том,
  сколько инструментов может выполниться; `MAX_ATTEMPT_NUMBER = 10**6` для
  `attempt_number`, который увеличивается только при resume. Потолок токенов оставляет пять-
  шесть порядков запаса относительно любой реальной нагрузки, точно
  представим и умещается в 64-битное целое со знаком, поэтому не может
  переполнить нижний счётчик. Это границы измерения: они ничего не
  разрешают, не запрещают и не бюджетируют, и никогда не достигают цепочки
  execution authority.
- **Сбой записи сообщается и не прерывает run.** Контракт отказа порта
  явный и обеспечивается вызывающим, а не только договорённостью: sink
  **должен** поднимать `TelemetryWriteError`, а любое другое его исключение
  нормализуется в `TelemetryWriteError` с сохранением исходного как `__cause__`.
  Нормализация оборачивает **только вызов sink**, поэтому ошибка стадии
  исполнения в другом месте run продолжает распространяться как она есть и никогда не
  переклассифицируется как ошибка телеметрии. Затем harness сообщает событие
  `physical_telemetry_write_failed`, неся тип исходной ошибки как `cause_category`, и сохраняет
  собственный исход run. Потерянная запись никогда не бывает беззвучной, а
  телеметрия никогда не может уничтожить уже определённый терминальный
  исход.
- **Измерение не несёт authority.** `AgentHarness` строит его из счётчиков и
  длительностей, которые у него уже есть. В цепочке `decision -> authorization ->
  execution` ничего не изменилось: `AuthorizedExecution` остаётся единственной
  execution authority, `RunScope` не тронут, политика sandbox и сети не тронута,
  семантика одобрения не тронута. Передача sink не может расширить то, что run
  может делать.
- **Sink — это зависимость композиции, как run store и idempotency guard.**
  `create_agent_harness` поставляет `FileTelemetrySink` по умолчанию, а
  `ForgeApiService` принимает явный `telemetry_sink` и передаёт его в фабрику
  harness, которую использует и для agent-loop, и для acceptance slice. Поэтому
  встраивающая композиция пишет в собственное хранилище, а не в процессный дефолт.
- **Откуда берётся измерение.** `AIDecisionProvider` — единственный decision
  provider в этом репозитории, выполняющий реальный вызов провайдера, поэтому теперь
  он сохраняет физическое измерение ответа (`last_usage`, `last_provider_name`,
  `last_model_name`) вместо того, чтобы его отбрасывать. Harness накапливает его по
  попытке и измеряет попытку один раз, на терминальной границе. Больше в этом классе
  ничего не изменилось: он остаётся рекомендательным и не несёт authority.

### Отклонённые альтернативы

- **Запись телеметрии в момент решения.** Отклонено: решение не является
  терминальным исходом попытки, поэтому запись там утверждала бы результат, которого
  не было.
- **Использование `RunAccountingRecord` как durable-записи.** Отклонено: он несёт
  `estimated_cost`, `provider_reported_cost`, `effective_cost` и `project_id`,
  поэтому его сохранение поместило бы деньги и владение в физический путь. Это прямое
  нарушение `D-PLATFORM-06` / `D-PLATFORM-07`.
- **Удаление финансовых полей из `AttemptUsageRecord` сейчас.** Отклонено для этого
  блока: dashboard и runtime-отчётность всё ещё их потребляют, поэтому удаление было
  бы рефактором с собственным риском совместимости. Разделение достигнуто тем, что
  durable-путь получил собственный контракт.
- **Второй поток телеметрии рядом с событиями harness.** Отклонено: измерение
  испускается через существующий коллектор harness, поэтому есть один поток событий и
  одно измерение, а не две конкурирующие записи об одной попытке.
- **Запись run, завершившегося исключением.** Отклонено: исключение не является
  терминальным исходом, и сфабрикованная запись о сбое была бы хуже отсутствия
  записи.
- **Подстановка оценки вместо отсутствующих токенов.** Отклонено: угаданное число
  токенов — ложное физическое измерение, а весь смысл этой границы в том, что
  физическая запись достоверна.

### Следствия и открытые пункты

1. **Stage 0.2 не выполнен.** Trusted execution *port* присутствует лишь наполовину:
   `PhysicalTelemetry` — выходная половина, а входной половины
   `TrustedExecutionRequest` нет.
2. **Наследный accounting-путь всё ещё живой и всё ещё в памяти.**
   `CapabilityFabric._run_usage` остаётся `defaultdict(list)`, а поле `accounting` у
   `RunStateSnapshot` по-прежнему не заполняется. Эта запись добавляет durable
   физический путь; она не мигрирует наследный.
3. **`provider_reported_cost` по-прежнему вводит в заблуждение.**
   `app/orchestrator/dispatcher.py` продолжает присваивать ему собственную
   `estimated_cost` модели. Это не изменено этим блоком и остаётся открытым пунктом
   `D-PLATFORM-07`.
4. **Измерения токенов настолько хороши, насколько хорош decision provider.**
   Дефолтный детерминированный провайдер не делает вызовов провайдера, поэтому
   production-run с ним записывает честный измеренный ноль. Реальный AI decision
   provider записывает реальные токены, что доказывает end-to-end
   `tests/test_physical_telemetry.py::ProductionEntryPointTelemetryTests` через
   `ForgeApiService.run_agent_loop`.
5. **Это переходная файловая персистентность.** `FileTelemetrySink` — локальный
   append-only JSONL-адаптер за портом. Слой персистентности Platform заменяет
   адаптер, не трогая Core.
6. **Никакой финансовой семантики не добавлено и не перенесено.** Cost, price,
   charge, wallet, credits, billing account, project ownership, organization
   ownership и user identity отсутствуют в контракте, и тесты утверждают их
   отсутствие.
7. **У контракта отказа sink есть известная точка эскалации.** На этом этапе
   потерянное измерение никогда не блокирует и не переклассифицирует run, и это
   правильно, пока не начинаются деньги. До появления `Charge` отсутствие
   durable `PhysicalTelemetry` для попытки обязано делать run финансово незавершённым: либо
   терминальное состояние становится fail-closed
   (`physical_telemetry_unavailable`), либо в ledger должна появиться компенсирующая
   отметка, которую billing-конвейер отвергает. Пока этого инварианта нет,
   `PhysicalTelemetry -> UsageRecord -> CostRecord -> Charge` допускал бы успешное исполнение, за
   которое никогда не выставляется счёт.
8. **Отложено из review Stage 0.1 и остаётся открытым:** конкурентные
   писатели всё ещё могут создать дубликат записи (нет compare-and-set); санитайзер
   `run_id` lossy, а `list_run_ids` возвращает санитизированный stem, а не исходный id;
   передаваемый вызывающим `attempt_id` может переопределить производную
   идентичность; строковые поля не имеют ограничения длины, а `error_type` не
   канонизируется; `completed_at` не валидируется; ротации файлов и `fsync`
   нет.


## Stage 1 Architecture Contract (D-PLATFORM-14)

The normative contract for Stage 1 lives in
[`STAGE-1-ARCHITECTURE-CONTRACT.md`](STAGE-1-ARCHITECTURE-CONTRACT.md). It binds the
Platform work that follows and restates the frozen Stage 0 decisions
(`D-PLATFORM-01..12`) and the Stage 0.1 telemetry decision (`D-PLATFORM-13`) in one
implementable form.

What this decision adds, beyond restating frozen material:

- **The Platform -> Core port is normative, not optional.** Stage 1 must introduce a
  single narrow typed contract (`TrustedExecutionRequest` or an equivalent internal
  port) carrying only server-derived trusted execution context. A port that accepts
  plaintext provider secret material as an ordinary DTO field is **rejected**; a
  contract shaped like `provider_secret_environ: Mapping[str, str]` is explicitly
  named as forbidden. Secrets are addressed by an **opaque secret reference**,
  resolved only at a controlled boundary, and never written to a persistent
  `RunRecord`, to `PhysicalTelemetry`, or to an ordinary API response.
- **No secret backend is frozen.** Neither a KMS/vault vendor nor a reference
  grammar such as `kms:v1:<base64_ciphertext>` is mandated here. The existing
  `ProviderAccount.secret_ref` reference model is extended, not replaced.
- **The workspace contract is platform-neutral.** It is expressed as an abstract
  server-derived resource root. A Linux-only layout must not be baked into the
  architecture, and Core stays cross-platform. `Workspace`, `WorkspaceBoundary`,
  and `RunScope` remain boundaries, never authorization.
- **Nine entities are fixed for Stage 1**, and no further entity may be added
  without its own decision record: `User`, `Organization`, `Membership`, `Project`,
  `ProviderAccount`, `APIKey`, `RunRecord`, `UsageRecord`, plus the `Membership`-based
  role model (no separate role entity). Billing entities — `Wallet`,
  `CreditTransaction`, `CostRecord`, `PricingPlan`, `PriceRule`, `Payment` — must not
  appear in Stage 1 without a separate gate.
- **Thirteen security invariants are permanent**, including "client IDs are not
  authority", "workspace is not permission", "`RunRecord` ownership is not execution
  authority", and "Platform may deny a launch but cannot expand Core authority".
- **Thirteen open decisions are recorded as open**, with the implementation step each
  one gates. They are deliberately not invented in the contract.

**Status: NOT STARTED.** This decision freezes direction and sequencing. It creates
no code, no runtime component, no dependency, and no Platform entity.

### Consequences and open items

1. **Nothing from Stage 1 exists.** No `User`, `Organization`, `Membership`,
   `Project`, `APIKey`, `RunRecord`, `UsageRecord`, PostgreSQL, authentication, or
   Platform -> Core port is implemented. The contract is a plan, and must not be
   described in the present tense.
2. **Stage 0.1 remains frozen and unchanged.** `PhysicalTelemetry` stays an
   exclusively physical measurement; the contract adds no field to it and changes no
   Core behaviour.
3. **Two deferred review findings are carried forward.** F-46-03 (concurrent writers
   can duplicate a telemetry record) and F-46-04 (lossy `run_id` sanitizer;
   `list_run_ids` returns the sanitized stem) remain open until a persistent storage
   layer exists.
4. **The fail-closed telemetry invariant is restated, not implemented.** Billing is
   not built, so a lost measurement still does not block a run on Stage 0.1.
   Before a `Charge` can exist, missing durable `PhysicalTelemetry` must make the
   execution financially incomplete.
5. **The open decisions gate implementation steps, not the contract.** O-1..O-13 must
   be decided before their dependent step, and each decision must be recorded here
   rather than assumed in code.


## Архитектурный контракт Stage 1 (D-PLATFORM-14) — русская версия

Нормативный контракт Stage 1 находится в
[`STAGE-1-ARCHITECTURE-CONTRACT.md`](STAGE-1-ARCHITECTURE-CONTRACT.md). Он обязателен
для последующей работы над Platform и переформулирует замороженные решения Stage 0
(`D-PLATFORM-01..12`) и решение о телеметрии Stage 0.1 (`D-PLATFORM-13`) в одной
реализуемой форме.

Что это решение добавляет сверх повторения замороженного материала:

- **Порт Platform -> Core нормативен, а не опционален.** Stage 1 должен ввести единый
  узкий типизированный контракт (`TrustedExecutionRequest` или эквивалентный
  внутренний порт), передающий только server-derived доверенный контекст исполнения.
  Порт, принимающий plaintext-секреты провайдера как обычное поле DTO,
  **отклонён**; контракт вида `provider_secret_environ: Mapping[str, str]` явно
  назван запрещённым. Секреты адресуются **непрозрачной ссылкой на секрет**,
  разрешаются только на контролируемой границе и никогда не пишутся в persistent
  `RunRecord`, в `PhysicalTelemetry` или в обычный ответ API.
- **Бэкенд секретов не фиксируется.** Ни вендор KMS/vault, ни грамматика ссылки вида
  `kms:v1:<base64_ciphertext>` здесь не предписываются. Существующая ссылочная модель
  `ProviderAccount.secret_ref` расширяется, а не заменяется.
- **Контракт workspace платформенно-нейтрален.** Он выражается как абстрактный
  server-derived корень ресурса. Linux-only раскладка не должна зашиваться в
  архитектуру, и Core остаётся кросс-платформенным. `Workspace`, `WorkspaceBoundary`
  и `RunScope` остаются границами и никогда — авторизацией.
- **Девять сущностей фиксируются для Stage 1**, и ни одна дополнительная сущность не
  вводится без собственной записи решения: `User`, `Organization`, `Membership`,
  `Project`, `ProviderAccount`, `APIKey`, `RunRecord`, `UsageRecord` плюс модель ролей
  на основе `Membership` (без отдельной сущности роли). Сущности биллинга — `Wallet`,
  `CreditTransaction`, `CostRecord`, `PricingPlan`, `PriceRule`, `Payment` — не должны
  появиться в Stage 1 без отдельного gate.
- **Тринадцать инвариантов безопасности постоянны**, включая «клиентские ID — не
  authority», «workspace — не разрешение», «владение `RunRecord` — не execution
  authority» и «Platform может отказать в запуске, но не может расширить authority
  Core».
- **Тринадцать открытых решений зафиксированы как открытые**, с указанием шага
  реализации, который каждое из них гейтит. Они намеренно не выдумываются в
  контракте.

**Статус: НЕ НАЧАТА.** Это решение замораживает направление и порядок. Оно не
создаёт ни кода, ни runtime-компонента, ни зависимости, ни сущности Platform.

### Следствия и открытые пункты

1. **Ничего из Stage 1 не существует.** Ни `User`, ни `Organization`, ни
   `Membership`, ни `Project`, ни `APIKey`, ни `RunRecord`, ни `UsageRecord`, ни
   PostgreSQL, ни аутентификация, ни порт Platform -> Core не реализованы. Контракт —
   это план, и его нельзя описывать в настоящем времени.
2. **Stage 0.1 остаётся замороженным и неизменным.** `PhysicalTelemetry` остаётся
   исключительно физическим измерением; контракт не добавляет ей полей и не меняет
   поведение Core.
3. **Два отложенных finding'а переносятся дальше.** F-46-03 (конкурентные писатели
   могут дублировать запись телеметрии) и F-46-04 (lossy-санитайзер `run_id`;
   `list_run_ids` возвращает санитизированный stem) остаются открытыми до появления
   слоя постоянного хранилища.
4. **Fail-closed инвариант телеметрии повторяется, а не реализуется.** Billing не
   построен, поэтому на Stage 0.1 потерянное измерение по-прежнему не блокирует
   запуск. До появления `Charge` отсутствие durable `PhysicalTelemetry` обязано
   делать исполнение финансово незавершённым.
5. **Открытые решения гейтят шаги реализации, а не контракт.** O-1..O-13 должны быть
   решены до зависимого шага, и каждое решение должно быть записано здесь, а не
   подразумеваться в коде.


## Stage 1 Contract Refinements (D-PLATFORM-15)

Refines [`STAGE-1-ARCHITECTURE-CONTRACT.md`](STAGE-1-ARCHITECTURE-CONTRACT.md) after
an independent architecture review of `D-PLATFORM-14`. The review is an **input**,
not a source of truth; the binding text is the contract itself, section 21. Nothing
here creates code, a dependency, or a runtime component, and `D-PLATFORM-01..14` are
unchanged.

### Accepted decisions

- **R-1 — Platform run identity and Core run identity are distinct.**
  `RunRecord.id` is the Platform identity; `RunRecord.core_run_id` is the immutable
  Core correlation identity carried in `PhysicalTelemetry.run_id`. `UsageRecord`
  must carry `run_record_id`, `core_run_id`, and `attempt_number` under explicitly
  distinct names, and must not use an ambiguous single field named `run_id`. The
  bridge is one-way: `PhysicalTelemetry.run_id -> core_run_id -> resolves the
  Platform RunRecord -> creates the usage record`.
- **R-1 — `UNIQUE(run_record_id, attempt_number)` resolves O-8 at contract level.**
  This is a uniqueness statement about one physical attempt of one Platform run. It
  is **not** financial truth, does not authorize anything, and does not replace
  Core's local idempotency. If implementation later needs additional dimensions,
  that is a separate decision record and never a silent change.
- **R-2 — Credential resolution boundary.** `TrustedExecutionRequest` never contains
  plaintext provider secrets; it carries only an opaque credential or secret
  reference. Core depends only on an abstract port, named
  `EphemeralSecretResolver`, which imports no Platform module, no KMS or vault SDK,
  no database driver, and no payment library, and which is injected by the Platform
  or application composition layer. A plaintext secret never enters a serialized
  trusted request, a `RunRecord`, a `UsageRecord`, `PhysicalTelemetry`, an ordinary
  API response, or a log. This is a boundary statement: it does not assert that
  Platform hands plaintext to Core, does not say Core knows about KMS or Vault, and
  does not freeze a resolver implementation.
- **R-3 — Tenant containment is in force before DTO implementation.** Every
  tenant-owned Platform entity carries an explicit `organization_id` as an
  application-level containment invariant, in force before step 1 (DTO / domain
  contracts) begins, and it does not replace future PostgreSQL RLS. The three
  layers are defense-in-depth: application authorization plus explicit
  `organization_id` containment plus RLS. O-9 remains open only for the exact RLS
  implementation.
- **R-4 — Project workspace roots are mutually isolated.** The workspace root of
  each `Project` must be isolated from the workspace roots of all other projects,
  including two projects inside the same `Organization`. A run in one project must
  not traverse into, read, write, or execute against another project's workspace.
  No filesystem topology, Linux path, or OS mechanism is fixed.
- **R-5 — API key authority semantics and revocation.** The `APIKey` authority model
  is open, and exactly one of two models must be chosen before step 8: an
  organization-scoped service principal, or a creator-membership-derived
  credential. Whichever is chosen, revocation behavior must be unambiguous, and the
  state where a creator loses membership while a key they created silently retains
  authority is explicitly forbidden. No token format, JWT, hash algorithm, or prefix
  is fixed; O-1 stays open.
- **R-6 — RunRecord failure and orphan semantics.** The final status enum stays open
  (O-7), but the lifecycle must have explicit semantics for normal success, normal
  failure, cancellation, timeout, and execution interruption, crash, or orphaned
  run, and there must be a mechanism that prevents a `RunRecord` from remaining
  indefinitely running or claimed after its execution worker is lost. Whether that
  is a lease, a heartbeat, a timeout, or a reconciliation sweep is left open.
- **R-7 — Explicitly NOT frozen.** A concrete token format such as
  `forge_live_<base62>`, JWT, stateful sessions, a concrete envelope-encryption
  scheme, a concrete reference grammar such as `env:<VAR_NAME>` or
  `vault:<secret_id>`, a specific KMS or vault vendor, a specific workspace
  topology, Linux-specific paths, and a specific payment provider are recorded as
  **review recommendations, not contract**. Each needs its own decision record.
- **R-8 — Eight security invariants added**, bringing the permanent list to 21:
  distinct Platform and Core run identities; `UsageRecord` preserves both;
  tenant-owned entities carry `organization_id`; project workspace roots are
  isolated; credential references are non-secret handles; plaintext credentials
  never cross a serialized Platform -> Core boundary; `APIKey` semantics have
  explicit revocation; `RunRecord` never remains indefinitely running after worker
  loss.
- **O-6 stays open.** The Platform -> Core port contract is a mandatory
  architectural boundary, but the exact transport — in-process, subprocess, local
  IPC, RPC, or another safe transport — is deliberately not chosen here.

### Rejected alternatives

- **A single `UsageRecord.run_id` field.** Rejected: it cannot say whether it names
  the Platform record or the Core correlation identity, and the two must stay
  distinguishable.
- **Passing plaintext provider secrets as ordinary DTO fields.** Rejected: a
  serialized boundary must carry a reference, never the secret.
- **Treating tenant containment as an RLS-only concern.** Rejected: containment is
  an application-level invariant that must hold before any database exists, with RLS
  as an additional layer rather than the only one.
- **Fixing the Platform -> Core transport now.** Rejected: it would freeze a
  deployment decision before the boundary has an implementation.
- **Promoting review recommendations into the contract.** Rejected: a token format,
  a crypto scheme, a reference grammar, or a vendor are implementation decisions
  with their own tradeoffs, and recording them as architecture would remove the
  choice without a decision.

### Consequences and open items

1. **Nothing is implemented.** Stage 1 remains **NOT STARTED**; the refinements are
   normative text only.
2. **O-8 is resolved at contract level only.** `UNIQUE(run_record_id, attempt_number)`
   binds the contract; an implementation review that needs more dimensions must
   raise a new decision rather than widen the uniqueness silently.
3. **O-7 and O-9 remain open** for the exact status enum and the exact RLS
   implementation, while their required semantics are now fixed by R-6 and R-3.
4. **O-1, O-2, O-3, O-4, O-5, O-6 remain open**, and O-10..O-13 remain deferred to
   later stages.
5. **Billing is still not implemented.** The fail-closed telemetry invariant from
   `D-PLATFORM-13` is restated in the contract and remains a future requirement.


## Уточнения контракта Stage 1 (D-PLATFORM-15) — русская версия

Уточняет [`STAGE-1-ARCHITECTURE-CONTRACT.md`](STAGE-1-ARCHITECTURE-CONTRACT.md) после
независимого архитектурного ревью `D-PLATFORM-14`. Ревью — это **входные данные**, а
не источник истины; обязывает сам контракт, раздел 21. Ничто здесь не создаёт код,
зависимость или runtime-компонент, а `D-PLATFORM-01..14` не изменяются.

### Принятые решения

- **R-1 — Идентичность запуска Platform и идентичность запуска Core различны.**
  `RunRecord.id` — идентичность Platform; `RunRecord.core_run_id` — неизменяемая
  корреляционная идентичность Core, переносимая в `PhysicalTelemetry.run_id`.
  `UsageRecord` должен нести `run_record_id`, `core_run_id` и `attempt_number` под
  явно различными именами и не должен использовать неоднозначное единое поле с
  именем `run_id`. Мост односторонний: `PhysicalTelemetry.run_id -> core_run_id ->
  разрешает Platform RunRecord -> создаёт запись потребления`.
- **R-1 — `UNIQUE(run_record_id, attempt_number)` разрешает O-8 на уровне
  контракта.** Это утверждение об уникальности одной физической попытки одного
  запуска Platform. Это **не** финансовая истина, оно ничего не авторизует и не
  заменяет локальную идемпотентность Core. Если реализации позже потребуются
  дополнительные измерения, это отдельная запись решения, и никогда — молчаливое
  изменение.
- **R-2 — Граница разрешения кредилов.** `TrustedExecutionRequest` никогда не
  содержит plaintext-секретов провайдера; он несёт только непрозрачную ссылку на
  кредил или секрет. Core зависит только от абстрактного порта, названного
  `EphemeralSecretResolver`, который не импортирует ни модуль Platform, ни SDK KMS
  или vault, ни драйвер базы данных, ни платёжную библиотеку, и который внедряется
  слоем композиции Platform или приложения. Plaintext-секрет никогда не входит в
  сериализованный trusted request, в `RunRecord`, в `UsageRecord`, в
  `PhysicalTelemetry`, в обычный ответ API или в лог. Это утверждение о **границе**:
  оно не утверждает, что Platform передаёт plaintext в Core, не говорит, что Core
  знает о KMS или Vault, и не замораживает реализацию resolver'а.
- **R-3 — Containment арендаторов действует до реализации DTO.** Каждая
  tenant-owned сущность Platform несёт явный `organization_id` как
  application-level инвариант containment, действующий до начала шага 1 (DTO /
  доменные контракты), и он не заменяет будущий PostgreSQL RLS. Три слоя образуют
  defense-in-depth: авторизация приложения плюс явный containment `organization_id`
  плюс RLS. O-9 остаётся открытым только для точной реализации RLS.
- **R-4 — Workspace-корни проектов взаимно изолированы.** Workspace-корень каждого
  `Project` должен быть изолирован от workspace-корней всех остальных проектов,
  включая два проекта внутри одной `Organization`. Запуск в одном проекте не должен
  проходить внутрь, читать, писать или исполнять в отношении workspace другого
  проекта. Ни топология файловой системы, ни Linux-путь, ни механизм ОС не
  фиксируются.
- **R-5 — Authority и отзыв API key.** Модель authority `APIKey` открыта, и до шага
  8 должна быть выбрана ровно одна из двух моделей: сервисный принципал уровня
  Organization либо кредил, выведенный из membership создателя. Какую бы модель ни
  выбрали, поведение отзыва должно быть однозначным, а состояние, когда создатель
  теряет membership, а созданный им ключ молча сохраняет authority, явно запрещено.
  Формат токена, JWT, алгоритм хэша и префикс не фиксируются; O-1 остаётся открытым.
- **R-6 — Семантика отказа и orphan для RunRecord.** Итоговый enum статусов остаётся
  открытым (O-7), но жизненный цикл должен иметь явную семантику для нормального
  успеха, нормального отказа, отмены, таймаута и прерывания исполнения, краха или
  orphaned run, и должен существовать механизм, предотвращающий бесконечное
  зависание `RunRecord` в running или claimed после потери execution worker'а.
  Является ли это lease, heartbeat, таймаутом или reconciliation-обходом —
  оставлено открытым.
- **R-7 — Явно НЕ заморожено.** Конкретный формат токена вида
  `forge_live_<base62>`, JWT, stateful-сессии, конкретная схема
  envelope-шифрования, конкретная грамматика ссылки вида `env:<VAR_NAME>` или
  `vault:<secret_id>`, конкретный вендор KMS или vault, конкретная топология
  workspace, Linux-специфичные пути и конкретный платёжный провайдер записаны как
  **рекомендации ревью, а не контракт**. Каждому нужна собственная запись решения.
- **R-8 — Добавлены восемь инвариантов безопасности**, доводя постоянный список до
  21: различные идентичности запуска Platform и Core; `UsageRecord` сохраняет обе;
  tenant-owned сущности несут `organization_id`; workspace-корни проектов
  изолированы; ссылки на кредилы — non-secret хэндлы; plaintext-кредилы никогда не
  пересекают сериализованную границу Platform -> Core; семантика `APIKey` имеет
  явный отзыв; `RunRecord` никогда не остаётся бесконечно в running после потери
  worker'а.
- **O-6 остаётся открытым.** Контракт порта Platform -> Core — обязательная
  архитектурная граница, но точный транспорт — in-process, subprocess, локальный
  IPC, RPC или другой безопасный транспорт — здесь намеренно не выбирается.

### Отклонённые альтернативы

- **Единое поле `UsageRecord.run_id`.** Отклонено: оно не может сказать, называет
  ли оно запись Platform или корреляционную идентичность Core, а они должны
  оставаться различимыми.
- **Передача plaintext-секретов провайдера как обычных полей DTO.** Отклонено:
  сериализованная граница должна нести ссылку, а не секрет.
- **Рассмотрение containment арендаторов как заботы только RLS.** Отклонено:
  containment — application-level инвариант, который должен действовать до
  появления любой базы данных, а RLS — дополнительный слой, а не единственный.
- **Фиксация транспорта Platform -> Core сейчас.** Отклонено: это заморозило бы
  решение о развёртывании до появления реализации границы.
- **Превращение рекомендаций ревью в контракт.** Отклонено: формат токена, схема
  шифрования, грамматика ссылки или вендор — это implementation-решения с
  собственными компромиссами, и запись их как архитектуры убрала бы выбор без
  решения.

### Следствия и открытые пункты

1. **Ничего не реализовано.** Stage 1 остаётся **НЕ НАЧАТЫМ**; уточнения — это
   только нормативный текст.
2. **O-8 разрешён только на уровне контракта.** `UNIQUE(run_record_id,
   attempt_number)` обязывает контракт; implementation review, которому потребуются
   дополнительные измерения, должен поднять новое решение, а не расширять
   уникальность молча.
3. **O-7 и O-9 остаются открытыми** для точного enum статусов и точной реализации
   RLS, тогда как их требуемая семантика теперь зафиксирована R-6 и R-3.
4. **O-1, O-2, O-3, O-4, O-5, O-6 остаются открытыми**, а O-10..O-13 остаются
   отложенными на более поздние этапы.
5. **Billing по-прежнему не реализован.** Fail-closed инвариант телеметрии из
   `D-PLATFORM-13` повторён в контракте и остаётся будущим требованием.


## ProviderAccount ownership: tenant-owned or system-owned (D-PLATFORM-16)

Formalizes the one gap the Stage 1 domain-contract security gate found: the
`ProviderAccount` domain record permits a null `organization_id`, which the Stage 1
Architecture Contract already allowed in its entity table but never described as an
explicit, permitted ownership mode. Version 1 is adopted: the system-owned record is
a legitimate domain case, not an exception to be removed.

### Accepted decisions

- **`ProviderAccount` has exactly two ownership modes, and only two.**
  **tenant-owned** — belongs to one `Organization`, `organization_id` required and
  non-null (the BYOK case). **system-owned / Forge-managed** — belongs to no
  `Organization`, `organization_id` null.
- **Ownership mode is a server-side domain fact.** It is never a client-supplied
  claim, and a client cannot manufacture system ownership by sending a null or
  empty organization reference.
- **A null `organization_id` is never a wildcard.** It never means "all tenants", it
  never widens a tenant scope, and a tenant-scoped query must treat `NULL` as an
  absence of ownership rather than as a match.
- **A tenant-scoped read selects only records explicitly belonging to that
  organization.** A system-owned record must not appear merely because
  `organization_id IS NULL`.
- **System-owned records are outside tenant ownership.** They are not a tenant
  resource and are not exposed through an ordinary tenant-scoped client read.
- **System-owned records are reachable only through an explicitly
  server-authorized path**, if such a path is ever defined.
- **System ownership is an ownership boundary and nothing more.** It introduces no
  new entity, no new authority mechanism, and no permission object.
  `ProviderAccount` of either mode remains not-Core-authority, and system ownership
  by itself grants no execution authority.

### Rejected alternatives

- **`NULL` organization_id as "any tenant".** Rejected: it is the classic place a
  tenant filter silently stops filtering, and it would let a single null column
  become a cross-tenant read.
- **Treating the system-owned case as an implicit exception.** Rejected: an
  undocumented null in a tenant column is indistinguishable from a bug, and Step 2
  would have had to guess whether the schema may be `NOT NULL`.
- **Removing the system-owned case and requiring `organization_id` on every
  `ProviderAccount`.** Rejected: the architecture already needs a Forge-managed
  provider credential, and inventing a second entity for it would add a concept the
  contract does not need.
- **Adding pricing, wholesale cost, or billing semantics to the system-owned
  case.** Rejected: system ownership is an ownership boundary, and those remain
  later-stage decisions.

### Consequences

1. **The Step 2 schema must permit a nullable `organization_id` on
   `ProviderAccount`.** A blanket `NOT NULL` is not available for this table.
2. **Application and domain containment must distinguish tenant-owned from
   system-owned.** The distinction must be explicit rather than inferred from a
   null at read time.
3. **RLS and ordinary queries must never interpret `NULL` as a wildcard.** O-9
   remains open for the exact RLS implementation, but this rule binds whatever is
   implemented.
4. **No client API may let a caller choose a provider account's ownership mode.**
5. **System ownership grants no execution authority by itself.**
6. **Nothing is implemented.** No code, schema, migration, or credential
   resolution is created by this decision; the domain record already matched it.

### Open decisions unchanged

O-1, O-2, O-3, O-4, O-5, O-6, O-7 and O-9 remain **open**; O-8 remains resolved at
contract level; O-10..O-13 remain deferred. No RLS implementation is chosen.


## Владение ProviderAccount: tenant-owned или system-owned (D-PLATFORM-16) — русская версия

Формализует единственный пробел, найденный security-гейтом доменных контрактов Stage 1:
доменная запись `ProviderAccount` допускает null `organization_id`, что контракт Stage 1 уже
разрешал в таблице сущностей, но никогда не описывал как явный, разрешённый
режим владения. Принят вариант 1: system-owned запись — легитимный доменный кейс,
а не исключение, которое нужно убрать.

### Принятые решения

- **У `ProviderAccount` ровно два режима владения, и только два.** **tenant-owned** —
  принадлежит одной `Organization`, `organization_id` обязателен и non-null (кейс BYOK).
  **system-owned / Forge-managed** — не принадлежит никакой `Organization`,
  `organization_id` null.
- **Режим владения — server-side доменный факт.** Он никогда не является
  заявлением клиента, и клиент не может создать системное владение,
  отправив пустую или null-ссылку на организацию.
- **Нулевой `organization_id` никогда не является подстановочным символом.** Он
  никогда не означает «все арендаторы», никогда не расширяет область
  арендатора, и tenant-scoped запрос должен трактовать `NULL` как отсутствие
  владения, а не как совпадение.
- **Tenant-scoped чтение выбирает только записи, явно принадлежащие этой
  организации.** System-owned запись не должна появляться только потому,
  что `organization_id IS NULL`.
- **System-owned записи находятся вне владения арендатора.** Они не являются
  ресурсом арендатора и не показываются через обычное tenant-scoped клиентское
  чтение.
- **System-owned записи доступны только через явно авторизованный на сервере
  путь**, если такой путь вообще будет определён.
- **Системное владение — это граница владения и больше ничего.** Оно не
  вводит ни новой сущности, ни нового механизма authority, ни permission-объекта.
  `ProviderAccount` любого режима остаётся не-authority для Core, и системное владение само
  по себе не даёт execution authority.

### Отклонённые альтернативы

- **`NULL` в organization_id как «любой арендатор».** Отклонено: это
  классическое место, где фильтр по арендатору молча перестаёт фильтровать, и одна
  null-колонка становится межарендаторным чтением.
- **Трактовка system-owned кейса как неявного исключения.** Отклонено:
  недокументированный null в колонке арендатора неотличим от ошибки, и Step 2
  должен был бы угадывать, может ли схема быть `NOT NULL`.
- **Удаление system-owned кейса и требование `organization_id` для каждого
  `ProviderAccount`.** Отклонено: архитектуре уже нужен Forge-managed провайдерский кредил, и
  изобретение для него второй сущности добавило бы концепцию, которая контракту не
  нужна.
- **Добавление ценообразования, оптовой себестоимости или биллинговой
  семантики в system-owned кейс.** Отклонено: системное владение — это граница
  владения, а эти вещи остаются решениями более поздних этапов.

### Следствия

1. **Схема Step 2 должна допускать nullable `organization_id` у `ProviderAccount`.**
   Сплошной `NOT NULL` для этой таблицы недоступен.
2. **Application- и доменный containment должны различать tenant-owned
   и system-owned.** Различие должно быть явным, а не выводиться из null во время чтения.
3. **RLS и обычные запросы никогда не должны трактовать `NULL` как
   подстановочный символ.** O-9 остаётся открытым для точной реализации RLS, но
   это правило обязывает любую её реализацию.
4. **Ни один клиентский API не должен позволять вызывающему выбрать режим
   владения провайдерского аккаунта.**
5. **Системное владение само по себе не даёт execution authority.**
6. **Ничего не реализовано.** Этим решением не создаётся ни код, ни схема,
   ни миграция, ни разрешение кредилов; доменная запись уже ему
   соответствовала.

### Открытые решения без изменений

O-1, O-2, O-3, O-4, O-5, O-6, O-7 и O-9 остаются **открытыми**; O-8 остаётся
решённым на уровне контракта; O-10..O-13 остаются отложенными. Никакая
реализация RLS не выбрана.


## Stage 1 PostgreSQL schema design and proposed RLS design (D-PLATFORM-17)

Records the schema-design pass for Stage 1 / Step 2. The design itself lives in
[`STAGE-1-STEP-2-SCHEMA-DESIGN.md`](STAGE-1-STEP-2-SCHEMA-DESIGN.md); this decision
records what it fixes, what it proposes, and what it deliberately leaves open.

### Accepted decisions

- **The persistence boundary is fixed.** PostgreSQL holds Platform state only:
  `users`, `organizations`, `memberships`, `projects`, `provider_accounts`,
  `api_keys`, `run_records`, `usage_records`. Core keeps its local file-based
  execution persistence and gains no database dependency. The two stores are joined
  by exactly one value, the Core correlation id.
- **No Billing table and no financial column is created.** `wallets`,
  `credit_transactions`, `cost_records`, `pricing_plans`, `price_rules`, and
  `payments` are out of scope, and no column in the eight tables can hold money.
- **Tenant containment is a schema invariant.** `organization_id` is `NOT NULL` on
  every tenant-owned table. `organizations` carries no `organization_id` because it
  *is* the tenant boundary; `users` carries none because a user is a global
  identity. These two absences are deliberate, not omissions.
- **D-PLATFORM-16 is persisted exactly.** `provider_accounts.organization_id` is the
  only nullable tenant column: non-null means tenant-owned, `NULL` means
  system-owned / Forge-managed. A `NULL` is never a wildcard, a tenant-scoped query
  excludes such rows structurally, a client cannot manufacture system ownership by
  sending a null, and system ownership grants no execution authority.
- **`UsageRecord` keeps three identities and no fourth.** `run_record_id`,
  `core_run_id`, and `attempt_number` are separate columns; there is no `run_id`
  column. `UNIQUE (run_record_id, attempt_number)` is the only contract-level
  uniqueness, and any further dimension requires a separate decision.
- **`RunRecord` keeps two identities.** `id` is the Platform identity and
  `core_run_id` is the Core correlation identity, typed `text` because Core mints
  non-UUID correlation values. The lifecycle checks the schema enforces are exactly
  the ones the domain contract already fixes; anything that depends on O-7 is left
  unconstrained so that deciding O-7 needs no data migration.
- **`APIKey` stores neither a token nor a hash.** The safe-representation algorithm
  is O-1, so creating a field for it now would close O-1 silently.
- **`secret_ref` and `workspace_ref` stay opaque.** No column encodes a reference
  grammar, a backend, a filesystem path, or a topology.
- **Enumerations are `text` plus `CHECK ... IN (...)`**, not PostgreSQL `enum`
  types, so that a member change is an ordinary migration while O-7 is open.
- **Identifiers are `uuid`** and `core_run_id` is `text`, matching what each layer
  actually produces.

### PROPOSED — not yet accepted

Recorded in the design as K-1 … K-10 and requiring an explicit decision before
implementation: the schema's identifier representation; membership pair
uniqueness; slug uniqueness scope; `provider_accounts` partial unique indexes;
API-key prefix uniqueness; delete semantics; the composite-key mechanism that makes
cross-tenant references structurally impossible; `core_run_id` uniqueness;
`scopes` representation; and the mutability contract for `updated_at`.

### Proposed RLS design — O-9 remains OPEN

The design in section I of the schema document is a **PROPOSED** design, not an
accepted one:

- tenant context is conveyed out of band through a transaction-scoped session
  setting that the server populates from the authenticated subject's membership,
  never from request data;
- every tenant-owned table carries a policy applying the containment predicate to
  `USING` for reads and deletes and to `WITH CHECK` for inserts and updates, so a
  row cannot be written into or moved between tenants;
- a missing tenant context denies, stated explicitly rather than relying on
  three-valued logic;
- `provider_accounts` tenant policies **exclude** system-owned rows rather than
  including them, and system-owned rows are reachable only through a separate
  server-only path if one is ever defined;
- patterns that treat `NULL` as "all tenants" are named as forbidden.

**O-9 is not closed by this record.** Adopting this design is a separate decision
requiring approval, and the design states plainly that RLS does not replace
application authorization: `identity != authorization != execution authority`
remains intact, and a visible row is not a permission.

### Rejected alternatives

- **Freezing a token or hash column now.** Rejected: it closes O-1 without a
  decision.
- **A `NOT NULL` `organization_id` on `provider_accounts`.** Rejected: it would
  contradict D-PLATFORM-16 and remove the Forge-managed case.
- **Relying on foreign keys alone for tenant containment.** Rejected: a foreign key
  proves a parent exists, not that two parents agree on the tenant. The composite-key
  mechanism is proposed precisely because of that gap.
- **Treating `NULL` as a tenant wildcard.** Rejected: it is the classic way a tenant
  filter stops filtering.
- **PostgreSQL `enum` types for status.** Rejected while O-7 is open, because
  changing an enum type on a live table is a heavier migration than changing a
  `CHECK` constraint.
- **Deciding Billing tables now.** Rejected: Billing needs its own gate.

### Consequences

1. **Nothing is implemented.** No migration, ORM model, repository, connector, or
   database policy was created, and no database dependency entered Core.
2. **Step 2 has a design contract to implement against**, including an explicit
   checklist and the constraints and indexes it must add.
3. **K-1 … K-10 must be accepted or amended before implementation**, so no proposal
   becomes a silent decision.
4. **O-9 remains open**, with a proposed design that must be approved rather than
   assumed.
5. **O-1 … O-7 remain open**, and O-10 … O-13 remain deferred. O-8 stays resolved at
   contract level.
6. **D-PLATFORM-16 is not weakened**; the nullable tenant column is carried into the
   schema with its exclusion rule stated on the read path and in the RLS design.


## Дизайн схемы PostgreSQL и предложенный дизайн RLS Stage 1 (D-PLATFORM-17) — русская версия

Фиксирует schema-design проход для Stage 1 / Шага 2. Сам дизайн находится в
[`STAGE-1-STEP-2-SCHEMA-DESIGN.md`](STAGE-1-STEP-2-SCHEMA-DESIGN.md); это решение фиксирует, что он
закрепляет, что предлагает и что намеренно оставляет открытым.

### Принятые решения

- **Граница персистентности зафиксирована.** PostgreSQL хранит только
  состояние Platform: `users`, `organizations`, `memberships`, `projects`,
  `provider_accounts`, `api_keys`, `run_records`, `usage_records`. Core сохраняет свою
  локальную файловую персистентность исполнения и не получает
  зависимости от базы данных. Два хранилища соединены ровно одним
  значением — корреляционной идентичностью Core.
- **Ни одна Billing-таблица и ни одна финансовая колонка не
  создаются.** `wallets`, `credit_transactions`, `cost_records`, `pricing_plans`,
  `price_rules` и `payments` вне области, и ни одна колонка в восьми таблицах
  не может хранить деньги.
- **Containment арендаторов — инвариант схемы.** `organization_id` — `NOT NULL` во
  всех tenant-owned таблицах. `organizations` не несёт `organization_id`, потому что она
  *и есть* граница арендатора; `users` не несёт, потому что пользователь —
  глобальная идентичность. Эти два отсутствия намеренны, а не являются
  пропущенными местами.
- **D-PLATFORM-16 персистится точно.** `provider_accounts.organization_id` —
  единственная nullable tenant-колонка: non-null означает tenant-owned, `NULL`
  означает system-owned / Forge-managed. `NULL` никогда не является подстановочным
  символом, tenant-scoped запрос структурно исключает такие строки, клиент не
  может создать системное владение, отправив null, и системное владение не
  даёт execution authority.
- **`UsageRecord` сохраняет три идентичности и ни одной четвёртой.**
  `run_record_id`, `core_run_id` и `attempt_number` — отдельные колонки;
  колонки `run_id` нет. `UNIQUE (run_record_id, attempt_number)` — единственная
  уникальность уровня контракта, и любое дополнительное измерение требует
  отдельного решения.
- **`RunRecord` сохраняет две идентичности.** `id` — идентичность
  Platform, `core_run_id` — корреляционная идентичность Core, типа `text`, потому что Core
  порождает не-UUID значения. Проверки жизненного цикла, которые обеспечивает
  схема, — ровно те, что уже фиксирует доменный контракт; всё, что зависит от
  O-7, оставлено неограниченным, чтобы решение O-7 не требовало миграции данных.
- **`APIKey` не хранит ни токен, ни хэш.** Алгоритм
  безопасного представления — это O-1, поэтому создание для него поля сейчас молча
  закрыло бы O-1.
- **`secret_ref` и `workspace_ref` остаются непрозрачными.** Ни одна
  колонка не кодирует грамматику ссылки, бэкенд, путь файловой системы или
  топологию.
- **Перечисления — это `text` плюс `CHECK ... IN (...)`,** а не типы PostgreSQL
  `enum`, чтобы изменение члена было обычной миграцией, пока O-7 открыт.
- **Идентификаторы — `uuid`,** а `core_run_id` — `text`, что соответствует тому,
  что фактически порождает каждый слой.

### PROPOSED — ещё не принято

Записаны в дизайне как K-1 … K-10 и требуют явного решения до реализации:
представление идентификаторов в схеме; уникальность пары membership; область
уникальности slug; частичные уникальные индексы `provider_accounts`;
уникальность префикса API-ключа; семантика удаления; механизм
составных ключей, делающий кросс-арендаторные ссылки структурно
невозможными; уникальность `core_run_id`; представление `scopes`; и контракт
изменяемости для `updated_at`.

### Предложенный дизайн RLS — O-9 остаётся OPEN

Дизайн в разделе I документа схемы — это **PROPOSED** дизайн, а не
принятое решение:

- tenant-контекст передаётся внеполосно через ограниченную транзакцией
  настройку сессии, которую сервер заполняет из membership
  аутентифицированного субъекта, никогда из данных запроса;
- каждая tenant-owned таблица несёт политику, применяющую предикат containment
  к `USING` для чтений и удалений и к `WITH CHECK` для вставок и обновлений, чтобы строку
  нельзя было записать в другого арендатора или переместить между ними;
- отсутствующий tenant-контекст отказывает, и это сформулировано явно, а не
  через расчёт на трёхзначную логику;
- tenant-политики `provider_accounts` **исключают** system-owned строки, а не
  включают их, и system-owned строки доступны только через отдельный
  server-only путь, если он когда-\u043bибо будет определён;
- шаблоны, трактующие `NULL` как «все арендаторы», названы запрещёнными.

**O-9 не закрывается этой записью.** Принятие этого дизайна — отдельное
решение, требующее утверждения, и дизайн прямо говорит, что RLS не
заменяет авторизацию приложения: `identity != authorization !=
execution authority` остаётся неизменным, и видимая строка не является
разрешением.

### Отклонённые альтернативы

- **Заморозить колонку токена или хэша сейчас.** Отклонено: это
  закрывает O-1 без решения.
- **`NOT NULL` `organization_id` на `provider_accounts`.** Отклонено: это
  противоречило бы D-PLATFORM-16 и убрало бы кейс Forge-managed.
- **Опора только на внешние ключи для containment арендаторов.**
  Отклонено: внешний ключ доказывает существование родителя, а не
  согласие двух родителей по арендатору. Механизм составных ключей
  предложен именно из-за этого пробела.
- **Трактовка `NULL` как подстановочного символа арендатора.**
  Отклонено: это классический способ, которым фильтр по арендатору
  перестаёт фильтровать.
- **Типы PostgreSQL `enum` для статусов.** Отклонено, пока O-7
  открыт, потому что изменение типа enum на живой таблице — более
  тяжёлая миграция, чем изменение ограничения `CHECK`.
- **Решение о Billing-таблицах сейчас.** Отклонено: Billing требует
  собственного gate.

### Следствия

1. **Ничего не реализовано.** Не создано ни миграции, ни ORM-модели,
   ни репозитория, ни коннектора, ни политики базы данных, и в Core не
   попала зависимость от базы данных.
2. **У Шага 2 есть дизайн-контракт для реализации**, включая явный
   чек-лист и ограничения и индексы, которые она обязана добавить.
3. **K-1 … K-10 должны быть приняты или изменены до реализации**,
   чтобы ни одно предложение не стало молчаливым решением.
4. **O-9 остаётся открытым**, с предложенным дизайном, который
   должен быть утверждён, а не предположен.
5. **O-1 … O-7 остаются открытыми**, а O-10 … O-13 остаются отложенными. O-8
   остаётся решённым на уровне контракта.
6. **D-PLATFORM-16 не ослаблен**; nullable tenant-колонка перенесена в схему
   вместе с правилом исключения на пути чтения и в дизайне RLS.


## Schema design decisions and the RLS design (D-PLATFORM-18)

Closes the decision gate that `D-PLATFORM-17` left open: the ten schema proposals
K-1 … K-10 and the RLS design. The operative summary lives in
[`STAGE-1-STEP-2-SCHEMA-DESIGN.md`](STAGE-1-STEP-2-SCHEMA-DESIGN.md); this record
states the decisions and their reasons.

Priorities applied, in order: **A** security and tenant containment, **B**
consistency with the already frozen domain contract, **C** no premature product
lock-in, **D** simple implementation.

### O-9 — RESOLVED as a design decision

The proposed RLS design is accepted. It is technically sound and requires no new
product decision:

- **Tenant context is transaction-local.** The server sets
  `SET LOCAL forge.organization_id`, inside an explicit transaction, from the
  resolved `authenticated subject -> membership -> organization`. The client never
  supplies it, and the database policy never sees a client-supplied value.
- **Fail-closed is stated explicitly.** A missing custom setting returns an empty
  string rather than `NULL`, so the predicate is written as
  `nullif(current_setting('forge.organization_id', true), '') IS NOT NULL AND
  organization_id = nullif(...)::uuid`. Absent or empty context yields no tenant
  rows and no tenant writes.
- **All eight tables carry RLS.** The six tenant-owned tables use the containment
  predicate; `users` and `organizations` get their own rules because they are not
  tenant-owned.
- **`USING` for reads and deletes, `WITH CHECK` for inserts and updates.** A
  `USING`-only policy would filter reads while still allowing a write into another
  tenant.
- **`provider_accounts` requires `organization_id IS NOT NULL AND organization_id =
  current tenant`** for tenant access, so system-owned rows are excluded
  structurally rather than incidentally.
- **A system-owned row is reached only through a separate server-only policy on a
  role the tenant path never uses**, gated by its own explicit session flag. That is
  a distinct authorization boundary, not an RLS bypass.
- **RLS is not the only defence against a cross-tenant reference** (K-7 below), and
  **RLS does not replace application authorization**: `identity != authorization !=
  execution authority` is unchanged.
- **Transaction discipline is a hard requirement.** `SET LOCAL` has no scope
  outside an explicit transaction, and session-level state must never be used
  because a pooled connection would carry the previous caller's tenant.

**Two verifications are required of the implementing task**, because PostgreSQL is
not installed in the development environment and these semantics were therefore
reasoned about rather than executed:

- **V-1** — confirm that `current_setting('forge.organization_id', true)` returns an
  empty string when unset, and that the predicate denies in that case.
- **V-2** — confirm that a tenant-scoped read outside an explicit transaction
  returns no rows rather than unfiltered rows.

O-9 is resolved as a design decision. Implementation and V-1/V-2 remain
outstanding, and no policy exists in any database.

### K-1 … K-10

| # | Decision | Verdict | Reason |
| --- | --- | --- | --- |
| K-1 | Identifiers persisted as `uuid`, application-generated | **ACCEPT** | A storage representation, not a new public API format; the domain's opaque identity semantics are unchanged. Precondition: every persisted identifier must be a UUID string, enforced by the persistence adapter and tested |
| K-2 | `UNIQUE (organization_id, user_id)` on `memberships` | **ACCEPT** | One user has at most one membership per organization, which is the model's linking intent. Compatible with K-6 because a rejoin reactivates the existing row rather than inserting a second |
| K-3 | Slug uniqueness scope | **PARTIAL** — per-organization **ACCEPT**, global for organizations **REJECT** | The contract fixes no global slug namespace, and a global unique constraint would leak whether a slug exists in another tenant through an ordinary constraint error. Tenant-owned entities get tenant-scoped uniqueness; organizations keep a non-unique lookup index |
| K-4 | `provider_accounts` partial unique indexes | **ACCEPT** | Exactly what `D-PLATFORM-16` fixes: one tenant-owned account per provider per organization, at most one system-owned account per provider. The modes are distinguished by `organization_id` nullability, so no additional ownership column is introduced |
| K-5 | `UNIQUE (key_prefix) WHERE key_prefix IS NOT NULL` | **DEFER** | Prefix uniqueness depends on the key format and on whether authentication looks keys up by prefix, both part of O-1. The column exists unconstrained |
| K-6 | Delete semantics | **ACCEPT, with every `CASCADE` removed** | A cascade can delete run or usage truth as a side effect. `RESTRICT` wherever the reference is required by the domain; `SET NULL` only for `run_records.initiated_by_user_id`, which is already optional. Retirement is a `status` change; hard delete is an administrative operation |
| K-7 | Composite foreign keys for tenant containment | **ACCEPT** | The security decision. `UNIQUE (id, organization_id)` on `projects` and `run_records`, with children referencing the pair, makes a cross-tenant reference structurally impossible in the schema. RLS cannot see a cross-table reference, so RLS does not replace this |
| K-8 | `UNIQUE (core_run_id) WHERE core_run_id IS NOT NULL` | **DEFER** | Uniqueness would freeze retry and resume semantics that are neither implemented nor decided. A **non-unique** lookup index covers the real need; uniqueness can arrive later as a follow-up constraint that fails loudly on existing data |
| K-9 | `scopes` as `text[]` | **ACCEPT** the representation, **DEFER** the GIN index | Storing a tuple of strings as a text array is a persistence detail, not authority semantics; scope meaning stays open. The index is added only when scope membership actually needs to be queried |
| K-10 | `updated_at` maintained by the application | **ACCEPT** | No trigger machinery. `created_at` is immutable and set at insert; `updated_at` is set by the server on write. `usage_records` stays immutable and carries no `updated_at`, matching the domain |

### Rejected alternatives

- **A global unique slug on `organizations`.** Rejected: it is not required by the
  contract and it turns an ordinary constraint error into a cross-tenant existence
  oracle.
- **Any `ON DELETE CASCADE`.** Rejected: deleting a parent must not silently destroy
  execution or usage history.
- **Making `api_keys.created_by_user_id` nullable to allow `SET NULL`.** Rejected:
  the domain contract requires the field, and nulling it would weaken key
  provenance to save a delete rule.
- **Relying on RLS alone for cross-tenant integrity.** Rejected: a row can satisfy a
  policy while referencing a parent in another organization, because the policy sees
  one table.
- **Freezing `core_run_id` uniqueness now.** Rejected: it would silently decide
  retry and resume semantics.
- **A new ownership column on `provider_accounts`.** Rejected: `D-PLATFORM-16`
  already fixes the two modes, and the nullability of `organization_id` is the
  discriminator.
- **PostgreSQL trigger machinery for `updated_at`.** Rejected as unnecessary
  complexity for a field the application already controls.

### Consequences

1. **Nothing is implemented.** No migration, SQL file, ORM model, repository,
   connector, or database policy was created, and no database dependency entered
   Core.
2. **Step 2 now has no open design choice.** The implementation task can be
   mechanical, and its checklist is in section M of the schema document.
3. **Five O-decisions remain open:** O-1 … O-7 minus O-8, which stays resolved at
   contract level. O-9 is resolved as a design decision; O-10 … O-13 stay deferred.
4. **Three items are deferred rather than decided:** K-5, K-8, and the K-9 index.
   Each is blocked on a decision that is genuinely open, not on unfinished analysis.
5. **`D-PLATFORM-16` is not weakened.** The nullable tenant column survives into the
   schema with its exclusion rule stated in the policy text itself, in the read-path
   rule, and in the partial unique indexes of K-4.
6. **`D-PLATFORM-17` is not superseded.** It recorded the design pass and left these
   items proposed; this record decides them.


## Решения по схеме и дизайн RLS (D-PLATFORM-18) — русская версия

Закрывает decision gate, который `D-PLATFORM-17` оставил открытым: десять
предложений схемы K-1 … K-10 и дизайн RLS. Оперативная сводка находится в
[`STAGE-1-STEP-2-SCHEMA-DESIGN.md`](STAGE-1-STEP-2-SCHEMA-DESIGN.md); эта запись фиксирует решения и их
причины.

Применённые приоритеты в порядке: **A** безопасность и containment арендаторов, **B**
соответствие уже замороженному доменному контракту, **C** отсутствие
преждевременного product lock-in, **D** простая реализация.

### O-9 — RESOLVED как дизайн-решение

Предложенный дизайн RLS принят. Он технически корректен и не требует нового
продуктового решения:

- **Tenant-контекст локален транзакции.** Сервер устанавливает
  `SET LOCAL forge.organization_id` внутри явной транзакции из разрешённой цепочки
  `аутентифицированный субъект -> membership -> organization`. Клиент никогда его не
  поставляет, и политика базы никогда не видит значение от клиента.
- **Fail-closed сформулирован явно.** Отсутствующая пользовательская
  настройка возвращает пустую строку, а не `NULL`, поэтому предикат написан как
  `nullif(current_setting('forge.organization_id', true), '') IS NOT NULL AND
  organization_id = nullif(...)::uuid`. Отсутствующий или пустой
  контекст даёт ноль строк и ноль записей в арендаторе.
- **Все восемь таблиц несут RLS.** Шесть tenant-owned таблиц используют предикат
  containment; `users` и `organizations` получают собственные правила, потому что они не
  tenant-owned.
- **`USING` для чтений и удалений, `WITH CHECK` для вставок и обновлений.** Политика
  только с `USING` фильтровала бы чтения, но всё ещё позволяла бы запись в
  другого арендатора.
- **`provider_accounts` требует `organization_id IS NOT NULL AND organization_id =
  current tenant`** для tenant-доступа, поэтому system-owned строки
  исключаются структурно, а не случайно.
- **System-owned строка достигается только через отдельную server-only
  политику** на роли, которую tenant-путь никогда не использует, ограниченную
  собственным явным session-флагом. Это другая граница авторизации, а не
  RLS bypass.
- **RLS — не единственная защита от кросс-арендаторной ссылки** (K-7 ниже), и
  **RLS не заменяет авторизацию приложения**: `identity != authorization !=
  execution authority` не изменено.
- **Транзакционная дисциплина — жёсткое требование.** `SET LOCAL` не
  имеет скоупа вне явной транзакции, а session-level состояние нельзя использовать
  никогда, потому что соединение из пула перенесло бы арендатора
  предыдущего вызова.

**От реализующей задачи требуются две проверки**, потому что PostgreSQL не
установлен в среде разработки и эти семантики были обоснованы, а не выполнены:

- **V-1** — подтвердить, что `current_setting('forge.organization_id', true)`
  возвращает пустую строку, когда не установлена, и что предикат в таком
  случае отказывает.
- **V-2** — подтвердить, что tenant-scoped чтение вне явной транзакции
  возвращает ноль строк, а не неотфильтрованные строки.

O-9 решён как дизайн-решение. Реализация и V-1/V-2 остаются
невыполненными, и ни одной политики нет ни в какой базе данных.

### K-1 … K-10

| # | Решение | Вердикт | Причина |
| --- | --- | --- | --- |
| K-1 | Идентификаторы персистятся как `uuid`, порождаемые приложением | **ACCEPT** | Представление в хранилище, а не новый публичный формат API; семантика непрозрачной идентичности домена не изменена. Предусловие: каждый персистимый идентификатор обязан быть UUID-строкой, что обеспечивает адаптер персистентности и покрывает тест |
| K-2 | `UNIQUE (organization_id, user_id)` на `memberships` | **ACCEPT** | У пользователя не более одного membership на организацию — это намерение связывания в модели. Совместимо с K-6, потому что возвращение реактивирует существующую строку, а не вставляет вторую |
| K-3 | Область уникальности slug | **PARTIAL** — на организацию **ACCEPT**, глобальная для organizations **REJECT** | Контракт не фиксирует глобального пространства имён, а глобальное уникальное ограничение утекало бы сведения о существовании slug в другом арендаторе через обычную ошибку ограничения. Tenant-owned сущности получают уникальность внутри арендатора; organizations сохраняют неуникальный индекс поиска |
| K-4 | Частичные уникальные индексы `provider_accounts` | **ACCEPT** | Ровно то, что фиксирует `D-PLATFORM-16`: один tenant-owned аккаунт на провайдера на организацию, не более одного system-owned на провайдера. Режимы различаются nullability `organization_id`, поэтому дополнительная колонка владения не вводится |
| K-5 | `UNIQUE (key_prefix) WHERE key_prefix IS NOT NULL` | **DEFER** | Уникальность префикса зависит от формата ключа и от того, ищет ли аутентификация ключи по префиксу, а и то и другое — часть O-1. Колонка существует без ограничения |
| K-6 | Семантика удаления | **ACCEPT, но все `CASCADE` убраны** | Cascade может удалить истину о запусках или потреблении как побочный эффект. `RESTRICT` там, где ссылка обязательна в домене; `SET NULL` только для `run_records.initiated_by_user_id`, которое уже опционально. Вывод из эксплуатации — смена `status`; жёсткое удаление — административная операция |
| K-7 | Составные внешние ключи для containment | **ACCEPT** | Security-решение. `UNIQUE (id, organization_id)` на `projects` и `run_records` вместе со ссылками детей на пару делает кросс-арендаторную ссылку структурно невозможной. RLS не видит межтабличную ссылку, поэтому RLS это не заменяет |
| K-8 | `UNIQUE (core_run_id) WHERE core_run_id IS NOT NULL` | **DEFER** | Уникальность заморозила бы семантику retry и resume, которые не реализованы и не решены. **Неуникальный** индекс поиска покрывает реальную потребность; уникальность может прийти позже отдельным ограничением, которое громко упадёт на существующих данных |
| K-9 | `scopes` как `text[]` | **ACCEPT** представление, **DEFER** GIN-индекс | Хранение кортежа строк как text-массива — это деталь персистентности, а не семантика authority; смысл scope остаётся открытым. Индекс добавляется, только когда членство в scope действительно нужно запрашивать |
| K-10 | `updated_at` поддерживает приложение | **ACCEPT** | Без trigger-механики. `created_at` неизменяем и ставится при вставке; `updated_at` ставит сервер при записи. `usage_records` остаётся неизменяемым и не несёт `updated_at`, что соответствует домену |

### Отклонённые альтернативы

- **Глобальный уникальный slug на `organizations`.** Отклонено: контрактом не
  требуется, и оно превращает обычную ошибку ограничения в оракул
  существования между арендаторами.
- **Любой `ON DELETE CASCADE`.** Отклонено: удаление родителя не должно
  молча уничтожать историю исполнения или потребления.
- **Сделать `api_keys.created_by_user_id` nullable ради `SET NULL`.** Отклонено:
  доменный контракт требует это поле, и его обнуление ослабило бы
  происхождение ключа ради экономии правила удаления.
- **Опора только на RLS для кросс-арендаторной целостности.** Отклонено:
  строка может удовлетворять политике, ссылаясь на родителя в другой
  организации, потому что политика видит одну таблицу.
- **Заморозить уникальность `core_run_id` сейчас.** Отклонено: это молча
  решило бы семантику retry и resume.
- **Новая колонка владения на `provider_accounts`.** Отклонено: `D-PLATFORM-16` уже
  фиксирует два режима, и дискриминатором является nullability
  `organization_id`.
- **PostgreSQL trigger-механика для `updated_at`.** Отклонено как ненужная
  сложность для поля, которое приложение уже контролирует.

### Следствия

1. **Ничего не реализовано.** Не создано ни миграции, ни SQL-файла,
   ни ORM-модели, ни репозитория, ни коннектора, ни политики базы данных,
   и в Core не попала зависимость от базы данных.
2. **У Шага 2 больше нет открытого дизайн-выбора.** Задача реализации
   может быть механической, а её чек-лист находится в разделе M
   документа схемы.
3. **Остаются открытыми пять O-решений:** O-1 … O-7 без O-8, который
   остаётся решённым на уровне контракта. O-9 решён как дизайн-решение;
   O-10 … O-13 остаются отложенными.
4. **Три пункта отложены, а не решены:** K-5, K-8 и индекс K-9.
   Каждый заблокирован действительно открытым решением, а не
   незавершённым анализом.
5. **`D-PLATFORM-16` не ослаблен.** Nullable tenant-колонка сохраняется
   в схеме вместе с правилом исключения, сформулированным в самом тексте
   политики, в правиле пути чтения и в частичных уникальных
   индексах K-4.
6. **`D-PLATFORM-17` не отменяется.** Он записал дизайн-проход и оставил
   эти пункты предложенными; эта запись их решает.


### Implementation status (recorded after the migration)

The migration, the schema, and the row-level security policies are implemented.
Twelve numbered SQL migrations under
`app/platform/persistence/postgres/migrations/`, a consolidated
`schema.sql` generated from them, and a committed `schema.snapshot.sql` that
records the schema as PostgreSQL itself stores it.

**Verifications V-1 and V-2 are complete, measured on PostgreSQL 17.11:**

- **V-1.** With no tenant context, all eight tables return zero rows to the tenant
  role, and insert, update and delete are rejected. The measurement produced a
  result that refines this record: there are **two** no-context states, not one.
  A setting that was *never assigned* returns `NULL`; a setting that was *assigned
  and then reverted* returns an **empty string**. A predicate written only as
  `IS NOT NULL` is therefore true in the second state, which is exactly the window
  in which a pooled connection could serve the next request. The adopted
  `nullif(..., '')` predicate denies in both, and the contract tests assert both —
  including that the unsound predicate is **true** on an empty setting.
- **V-2.** `SET LOCAL` lives only inside its transaction. After commit, the setting
  is empty, the helper returns NULL, and the tenant role sees zero rows.

**Two additions the implementation needed, neither of which changes a decision:**

- **Row-level security does not apply to a table's owner**, so all eight tables
  carry both `ENABLE` and `FORCE ROW LEVEL SECURITY`, and the two platform roles
  are kept separate from the migration role. Both roles are `NOLOGIN`,
  `NOSUPERUSER` and `NOBYPASSRLS`: they are authorization scopes reached with
  `SET ROLE`, not login accounts, so no password exists in the schema.
- **The tenant must be bound with `set_config(name, value, true)`, not with a
  literal `SET LOCAL`.** `SET` accepts no placeholder, so `SET LOCAL
  forge.organization_id = $1` is a parse error, and building the statement by
  interpolation would splice a client-supplied value into SQL.

**One correction.** PostgreSQL has no `isfinite(double precision)` — the function
exists only for date and time types. The `duration_seconds` guard is written as a
comparison against the two infinity values. NaN needs no separate clause and must
not be handled by a self-equality test, because PostgreSQL considers NaN equal to
itself and greater than every other float; such a clause would have **accepted**
NaN rather than rejecting it.

**Deliberate absences, asserted by the tests:** no unique constraint on
`core_run_id` (K-8) or on `key_prefix` (K-5); no `scopes` GIN index (K-9); and a
terminal `RunRecord` status without `finished_at` is accepted, because that
question belongs to O-7.

**Open decisions are unchanged.** O-1 … O-7 remain **open**; O-8 remains resolved
at contract level; O-9 is resolved as a design decision and now implemented;
O-10 … O-13 remain deferred. No billing table, money column, repository, service,
endpoint, authentication mechanism, credential resolver, or Platform -> Core
transport was created, and Core gained no database dependency.


### Статус реализации (записано после миграции)

Миграция, схема и политики row-level security реализованы. Двенадцать
нумерованных SQL-миграций в `app/platform/persistence/postgres/migrations/`, консолидированный `schema.sql`,
сгенерированный из них, и закоммиченный `schema.snapshot.sql`, фиксирующий схему
так, как её хранит сам PostgreSQL.

**Проверки V-1 и V-2 выполнены, измерены на PostgreSQL 17.11:**

- **V-1.** Без tenant-контекста все восемь таблиц возвращают ноль строк
  tenant-роли, а вставка, обновление и удаление отклоняются. Измерение дало
  уточнение к этой записи: состояний «нет контекста» **два**, а не одно.
  Никогда не заданная настройка возвращает `NULL`; настройка, которая была задана
  и затем откачена, возвращает **пустую строку**. Предикат, написанный только как
  `IS NOT NULL`, поэтому истинен во втором состоянии — именно в том окне, в котором
  соединение из пула могло бы обслужить следующий запрос. Принятый
  предикат `nullif(..., '')` отказывает в обоих, и контрактные тесты базы
  данных проверяют оба — включая то, что ненадёжный предикат **истинен** на
  пустой настройке.
- **V-2.** `SET LOCAL` живёт только внутри своей транзакции. После commit
  настройка пуста, хэлпер возвращает NULL, а tenant-роль видит ноль строк.

**Два добавления, которые потребовала реализация, и ни одно из них
не изменяет решение:**

- **Row-level security не применяется к владельцу таблицы**, поэтому все
  восемь таблиц несут и `ENABLE`, и `FORCE ROW LEVEL SECURITY`, а две роли Platform отделены от
  роли миграций. Обе роли — `NOLOGIN`, `NOSUPERUSER` и `NOBYPASSRLS`: это скоупы
  авторизации, достигаемые через `SET ROLE`, а не login-аккаунты, поэтому пароля в
  схеме нет.
- **Арендатора нужно связывать через `set_config(name, value, true)`, а не через
  литеральный `SET LOCAL`.** `SET` не принимает плейсхолдер, поэтому
  `SET LOCAL forge.organization_id = $1` — ошибка разбора, а сборка запроса
  интерполяцией вставила бы клиентское значение в SQL.

**Одна коррекция.** В PostgreSQL нет `isfinite(double precision)` — функция
существует только для типов даты и времени. Защита `duration_seconds` написана как
сравнение с двумя значениями бесконечности. NaN не требует отдельного условия
и не должен обрабатываться проверкой самого себя на равенство, потому что
PostgreSQL считает NaN равным самому себе и большим любого другого float; такое
условие **приняло** бы NaN, а не отвергло бы его.

**Намеренные отсутствия, проверяемые тестами:** нет уникального
ограничения на `core_run_id` (K-8) и на `key_prefix` (K-5); нет GIN-индекса `scopes` (K-9);
терминальный статус `RunRecord` без `finished_at` принимается, потому что этот
вопрос относится к O-7.

**Открытые решения не изменены.** O-1 … O-7 остаются **открытыми**; O-8
остаётся решённым на уровне контракта; O-9 решён как дизайн-решение и теперь
реализован; O-10 … O-13 остаются отложенными. Не создано ни одной
Billing-таблицы, ни денежной колонки, ни репозитория, ни сервиса, ни endpoint'а, ни
механизма аутентификации, ни разрешения кредилов, ни транспорта
Platform -> Core, и Core не получил зависимости от базы данных.


## Platform security boundaries hardened after adversarial review (D-PLATFORM-19)

An independent adversarial review of the PostgreSQL schema found **no real tenant
escape** in the row-level security implementation. It raised four boundaries that
were implicit rather than enforced. This record closes them.

### The four boundaries

**1. The two platform scopes are separate roles, and a GUC is not authorization.**
`forge_platform_app` is the tenant path; `forge_platform_system` is the server-only
scope. Both are `NOLOGIN`, `NOSUPERUSER`, `NOBYPASSRLS` and `NOINHERIT`, neither is
ever a member of the other, the migrations revoke that membership on every run, and
`platform.platform_scopes_are_separate()` lets a deployment assert it. `NOINHERIT`
matters: a session holding both memberships would still not pick up the system
policies merely by connecting.

`forge.system_scope` is **not** an authorization mechanism. Any session can set a
custom GUC, so what gates the system scope is the policy's `TO` clause plus the role
membership the deployment controls. The flag only narrows the scope further.

**Responsibility split, stated rather than masked.** The database owns the role
attributes, the mutual non-membership, the policies, and the grants. The deployment
owns which login role connects: tenant traffic and system traffic must be served by
different login roles, so that a connection serving a tenant request cannot also
`SET ROLE` into the system scope. **The application owns** setting the tenant
context from the resolved membership, and never from client input. Neither the
migration nor this decision can decide a deployment's login roles, and neither
pretends to.

**2. The server scope is discovery and bootstrap, not administration.** It may read
`users`, `memberships` and `organizations`; it may insert the bootstrap rows (a user
account, an organization, its first `OWNER` membership) and update, reactivate or
delete a membership; and it manages system-owned provider credentials. That is the
whole list. It has **no privilege and no policy** on `projects`, `api_keys`,
`run_records` or `usage_records`, so it cannot read or write another tenant's work
at all. The former `FOR ALL ... USING (system_scope_is_declared())` policies are
gone: `FOR ALL` now survives only on `provider_accounts`, where every operation is
bound to the system-owned row shape.

The pre-tenant discovery sequence is therefore fixed: resolve the subject, read its
memberships, read the organizations those memberships name, then enter a tenant
context and act through the ordinary tenant path.

**3. Usage is append-only and run history is not deletable.** `usage_records` may
be inserted and read, never updated or deleted. `run_records` may transition state
but never be deleted by the tenant path. Both rules are enforced in the policy layer
**and** the privilege layer, so a later policy edit cannot re-open a privilege that
was never granted. System-owned provider credentials remain the only rows the
server scope may delete.

**4. Provenance is tenant-safe.** `api_keys.created_by_user_id` and
`run_records.initiated_by_user_id` referenced `users(id)` alone, which proved the
user existed but not that they belonged to the row's organization; a tenant that
knew another tenant's user id could record it as its own key creator or run
initiator. Both now also carry a composite foreign key on
`(organization_id, <user column>)` referencing
`memberships (organization_id, user_id)`, which is already unique. The creator or
initiator must be a member of the row's own organization, enforced by PostgreSQL.
The single-column keys remain, so a queued run's null initiator is still permitted.
Both provenance keys are `ON DELETE RESTRICT`, consistent with the rule that no
foreign key cascades.

### Rejected alternatives

- **Treating `forge.system_scope = 'on'` as the system boundary.** Rejected: a GUC
  is settable by any session, so it can never grant access.
- **Leaving `FOR ALL` system policies in place on the operational tables and relying
  on the `TO` clause.** Rejected: it makes the server scope a universal cross-tenant
  reader and writer the moment one policy is edited, and it gives that scope a
  capability no accepted contract requires.
- **Removing UPDATE on `memberships` from the server scope.** Rejected, but the
  question was examined: K-2 allows one membership row per organization and user
  pair, so a returning member must be a status change on the existing row, which
  needs UPDATE.
- **Making identity updates available to the server scope.** Rejected: nothing in
  the accepted contract needs them, and `NO UPDATE` is a stronger statement than a
  policy that happens to match nothing.
- **Enforcing cross-tenant provenance in application code.** Rejected by the task
  and by the design: a database check cannot be bypassed by a missed code path.
- **`ON DELETE SET NULL` on the provenance keys.** Rejected: `created_by_user_id` is
  `NOT NULL`, so it is not available there, and where it would be available it would
  silently erase provenance instead of making deletion deliberate.
- **`FORCE ROW LEVEL SECURITY` alone, without separate roles.** Rejected: the owner
  exempts itself, so a test or deployment running as the owner would observe every
  row while appearing to pass.

### Consequences

1. **No tenant escape was found and none is claimed.** The review's verdict was GO
   WITH FIXES, and the fixes close implicit boundaries rather than a live breach.
2. **The security tests run from a real non-superuser login role.** A superuser and
   a table owner both bypass row-level security, so proving "the tenant path cannot
   reach system rows" from such a session would prove nothing.
3. **The server scope lost capabilities it did not need.** `UPDATE` on `users` and
   on `organizations` is gone, along with every policy and privilege on the four
   operational tables.
4. **Deleting a membership or a user is now stricter**, because provenance keys
   RESTRICT. Memberships are retired by `status`, which K-2 already required.
5. **Open decisions are unchanged.** O-1 … O-7 remain **open**; O-8 stays resolved
   at contract level; O-9 stays resolved and implemented; O-10 … O-13 stay deferred.
   O-7 in particular is untouched: no status gained or lost a terminal-state rule.
6. **No billing table, money column, repository, service, endpoint, authentication
   mechanism, credential resolver, or Platform -> Core transport was added**, and
   Core gained no database dependency.


## Границы безопасности Platform усилены после adversarial review (D-PLATFORM-19) — русская версия

Независимый adversarial review схемы PostgreSQL не нашёл **реального tenant
escape** в реализации row-level security. Он выявил четыре границы, которые были
неявными, а не принудительными. Эта запись их закрывает.

### Четыре границы

**1. Два скоупа Platform — разные роли, и GUC — не авторизация.**
`forge_platform_app` — путь арендатора; `forge_platform_system` — server-only скоуп. Обе —
`NOLOGIN`, `NOSUPERUSER`, `NOBYPASSRLS` и `NOINHERIT`, ни одна никогда не является членом
другой, миграции отозывают это членство при каждом запуске, а
`platform.platform_scopes_are_separate()` позволяет деплою это проверить. `NOINHERIT`
важен: сессия, владеющая обоими членствами, всё равно не подхватила бы
system-политики просто при подключении.

`forge.system_scope` **не** является механизмом авторизации. Любая
сессия может установить пользовательскую GUC, поэтому доступ даёт клауза `TO` в
политике вместе с членством в роли, которым управляет деплой. Флаг лишь
дополнительно сужает скоуп.

**Разделение ответственности сформулировано, а не замаскировано.**
База данных владеет атрибутами ролей, взаимным нечленством, политиками
и привилегиями. Деплой владеет тем, какая login-роль подключается: трафик
арендатора и трафик system-скоупа должны обслуживаться разными login-ролями,
чтобы соединение, обслуживающее запрос арендатора, не могло также
выполнить `SET ROLE` в system-скоуп. **Приложение владеет** установкой
tenant-контекста из разрешённого membership и никогда из клиентского ввода. Ни
миграция, ни это решение не могут определить login-роли деплоя, и ни одно из
них не делает вид, что может.

**2. Серверный скоуп — discovery и bootstrap, а не администрирование.** Он
может читать `users`, `memberships` и `organizations`; может вставлять bootstrap-строки
(аккаунт, организацию, её первый `OWNER` membership) и обновлять, реактивировать
или удалять membership; и управляет system-owned провайдерскими кредилами. Это весь
список. У него **нет ни привилегий, ни политик** на `projects`, `api_keys`,
`run_records` или `usage_records`, поэтому он вообще не может читать или писать работу
другого арендатора. Бывшие политики
`FOR ALL ... USING (system_scope_is_declared())` удалены: `FOR ALL` теперь есть только на
`provider_accounts`, где каждая операция привязана к форме system-owned строки.

Последовательность pre-tenant discovery тем самым зафиксирована:
разрешить субъект, прочитать его memberships, прочитать организации, на
которые они указывают, а затем войти в тенантный контекст и действовать
через обычный путь арендатора.

**3. Потребление — append-only, история запусков не удаляется.**
`usage_records` можно вставлять и читать, но никогда не обновлять или
удалять. `run_records` может менять статус, но никогда не удаляется путём
арендатора. Оба правила обеспечены в слое политик **и** в слое
привилегий. System-owned провайдерские кредилы остаются
единственными строками, которые серверный скоуп может удалить.

**4. Provenance безопасен для арендатора.**
`api_keys.created_by_user_id` и `run_records.initiated_by_user_id` ссылались только на
`users(id)`, что доказывало существование пользователя, но не его
принадлежность организации строки; арендатор, знающий id пользователя
другого арендатора, мог записать его создателем своего ключа или
инициатором своего запуска. Теперь оба несут дополнительный составной
внешний ключ на `(organization_id, <колонка user>)`, ссылающийся на
`memberships (organization_id, user_id)`, который уже уникален. Создатель или инициатор
обязан быть участником собственной организации строки, и это
обеспечивает PostgreSQL. Одноколоночные ключи сохранены, поэтому null
инициатор queued-запуска по-прежнему допустим. Оба ключа provenance —
`ON DELETE RESTRICT`, что согласуется с правилом об отсутствии каскадов.

### Отклонённые альтернативы

- **Считать `forge.system_scope = 'on'` границей system-доступа.** Отклонено: GUC
  устанавливается любой сессией, поэтому он никогда не может давать доступ.
- **Оставить `FOR ALL` system-политики на операционных таблицах,
  полагаясь на клаузу `TO`.** Отклонено: это делает серверный скоуп
  универсальным кросс-арендаторным читателем и писателем при первой же
  правке политики.
- **Убрать UPDATE на `memberships` у серверного скоупа.** Отклонено, но
  вопрос рассмотрен: K-2 допускает одну строку membership на пару
  организация-пользователь, поэтому возвращающийся участник — это
  смена статуса существующей строки.
- **Дать серверному скоупу изменение идентичности.** Отклонено: ни один
  принятый контракт этого не требует, а `NO UPDATE` — более сильное утверждение,
  чем политика, которая просто ничего не сопоставляет.
- **Проверять кросс-арендаторный provenance в коде приложения.** Отклонено
  задачей и дизайном: проверку базы нельзя обойти забытым путём в коде.
- **`ON DELETE SET NULL` на ключах provenance.** Отклонено:
  `created_by_user_id` — `NOT NULL`, там это недоступно, а там, где доступно, это
  молча стирало бы provenance вместо того, чтобы сделать удаление
  осознанным.
- **Только `FORCE ROW LEVEL SECURITY`, без разделения ролей.** Отклонено:
  владелец освобождает себя от неё, поэтому тест или деплой, работающий
  как владелец, видел бы все строки, выглядя при этом успешным.

### Следствия

1. **Tenant escape не найден, и ничего такого не заявляется.** Вердикт
   ревью был GO WITH FIXES, и исправления закрывают неявные границы, а не
   живущую брешь.
2. **Безопасные тесты выполняются от реальной non-superuser login-роли.**
   И superuser, и владелец таблицы обходят row-level security, поэтому доказательство
   «путь арендатора не достаёт system-строк» от такой сессии не доказывало бы
   ничего.
3. **Серверный скоуп потерял возможности, которые ему не нужны.** `UPDATE` на
   `users` и `organizations` убран, вместе со всеми политиками и привилегиями на
   четырёх операционных таблицах.
4. **Удаление membership или пользователя теперь строже**, потому что
   ключи provenance — RESTRICT. Membership выводится из эксплуатации через `status`, чего K-2
   уже требовал.
5. **Открытые решения не изменены.** O-1 … O-7 остаются **открытыми**; O-8 —
   решён на уровне контракта; O-9 — решён и реализован; O-10 … O-13 — отложены. O-7
   в частности не затронут: ни один статус не получил и не потерял правило
   терминального состояния.
6. **Не добавлено ни Billing-таблицы, ни денежной колонки, ни
   репозитория, ни сервиса, ни endpoint'а, ни механизма аутентификации, ни
   разрешения кредилов, ни транспорта Platform -> Core, и Core не получил
   зависимости от базы данных.


## Platform persistence, repositories, and Unit of Work (D-PLATFORM-20)

Implements the Platform persistence layer over the frozen Step 2 schema, and records
a documentation gap found at the start of the step.

### The documentation gap, recorded rather than papered over

The task named `docs/STAGE-1-STEP-3-PERSISTENCE-DESIGN.md` as its architectural
anchor. **That file did not exist in the repository**: `git log --all` has no history
for it, no other document carried its content, and no Step 3 persistence design had
ever been written. Rather than invent a design silently or treat the missing document
as licence to improvise, the implementation was derived from three sources that do
exist and are normative: the Step 3 brief itself, Stage 1 Architecture Contract
sections 17 and 18 (implementation order and required test invariants), and the Step 2
schema contract. The resulting design is now written down as
`docs/STAGE-1-STEP-3-PERSISTENCE-DESIGN.md`, marked as written after the fact. **No
frozen contract was changed** to accommodate the implementation.

### Accepted decisions

- **Layering is one-way and the domain stays stdlib-only.** Application services ->
  protocols -> Unit of Work -> repositories -> session -> pool -> PostgreSQL. The
  eight domain records stay frozen dataclasses; `mapper.py` is the only module that
  knows both a domain record and a physical column; `asyncpg` is imported only inside
  `database.py`, lazily. Nothing outside `app/platform/` imports the persistence
  package.
- **Two database logins, not one.** A deployment must serve tenant traffic and system
  traffic through different logins, because one login in both roles would let a tenant
  connection step into the system scope. The pool **refuses to build** when the
  effective role is a superuser, has `BYPASSRLS`, or owns the Platform tables: all
  three exempt a session from the policies it must obey.
- **Three named scopes.** `tenant(organization_id, user_id=...)`,
  `pre_tenant(user_id=...)`, and `server(role=..., system_scope=...)`. Each opens its
  own transaction, sets its own role with `SET LOCAL ROLE`, and sets its own context.
- **`forge.system_scope` is not an authorization mechanism.** Any session can set a
  custom GUC; what gates the system scope is the policy's `TO` clause plus the role.
  The flag only narrows the scope further.
- **Repositories never manage transactions.** The words `begin`, `commit`, and
  `rollback` do not appear in `repositories.py`. A session refuses statements outside a
  transaction, so a repository used outside a Unit of Work raises rather than
  auto-committing.
- **No tenant-scoped repository takes an organization identifier.** The tenant comes
  from the Unit of Work, so there is no parameter to pass a foreign tenant and none to
  forget to validate.
- **No generic `update(**fields)` anywhere.** Each mutable transition is a named
  method, so an immutable field has nowhere to be written from.
- **`UsageRecordRepository` has no update and no delete method**, and the protocol has
  none either. Append-only is expressed by absence, backed by withheld table
  privileges and the absence of any policy granting them.
- **Provider account ownership is two repositories**, not one with a nullable filter.
  `provider_accounts` is bounded by the session tenant and never uses
  `organization_id IS NULL` as a lookup; `system_provider_accounts` is bounded to
  `organization_id IS NULL` and is reachable only from the server scope with the
  system role. There is no `get_by_organization(None)`.
- **Identifiers are validated at the persistence boundary.** The domain accepts any
  opaque non-empty string and storage stores `uuid`; `mapper.as_uuid` converts or
  raises `InvalidIdentifierError` naming the field, before any SQL is built.
  `core_run_id` and `task_id` are correlation/text values, not identifiers of this
  kind, and are not validated as UUIDs.
- **Uniqueness is the database's, never a pre-check.** Memberships, project slugs,
  provider accounts, and usage attempts all rely on their constraints; the run claim
  uses a conditional `UPDATE` whose `status = 'queued'` predicate is part of the
  statement, so a race cannot be won twice.
- **An authorization failure is never reported as absence.** A policy denial is a
  `PermissionDeniedError`; a missing record is an `EntityNotFound`. The SQLSTATE and
  violated constraint name are preserved so a caller can react to a specific lost race
  and an operator can read the original message.

### Two Step 2 policy gaps closed

Both are the same shape: **a privilege granted with no policy to match**, which is a
latent inconsistency rather than a safeguard. Neither was a live tenant escape.

1. The system role held `INSERT` on `users` with no INSERT policy, so the bootstrap
   path -- creating an organization and its first `OWNER` membership -- could not
   actually run. Added `users_system_insert`.
2. The tenant role held `UPDATE` on `users` with no UPDATE policy, so an update matched
   zero rows and the repository reported a missing record. That is the worst of the
   three possible outcomes, because it is a silent wrong answer. Added
   `users_tenant_update`, bounded to the subject's own row
   (`id = platform.current_user_id()`), so one subject cannot rewrite another's record.

### Rejected alternatives

- **One login holding both platform roles.** Rejected: it collapses the boundary the
  security work established, and a test fixture using it would prove less than it
  appears to.
- **Switching to the system role on the tenant connection at runtime.** Rejected: the
  role would then be reachable from tenant traffic, and the separation would exist only
  in application code.
- **One `ProviderAccountRepository` with a nullable organization filter.** Rejected:
  `organization_id IS NULL` is the system-owned marker, and a filter that accepts it
  from a tenant caller is exactly the wildcard D-PLATFORM-16 forbids.
- **A generic `update(**fields)` per repository.** Rejected: it gives an immutable field
  a way to be written.
- **Pre-checking uniqueness before inserting.** Rejected: a check-then-insert still
  loses a race; the constraint is the authority.
- **Returning `None` for a policy denial.** Rejected: it makes an authorization failure
  indistinguishable from absence.
- **Making `RunRecord.core_run_id` unique while implementing the lookup.** Rejected:
  K-8 defers retry and resume semantics, and a unique index would freeze them.
- **Constraining terminal `RunRecord` statuses in the repository.** Rejected: which
  statuses are terminal is O-7, which remains open.
- **Importing the driver at module scope in the repositories.** Rejected: it would put a
  driver in the Platform import graph and make the package unusable without one.

### Consequences

1. **Persistence is implemented and proven against PostgreSQL 17.11**: 88 persistence
   tests, plus the 65 schema and 35 security tests from Step 2 all still passing, and
   the 58 domain tests from Step 1.
2. **The two policy gaps are closed**, so the bootstrap path runs and an identity update
   either succeeds or fails loudly.
3. **Application services can be written against `protocols.py`** without knowing that
   PostgreSQL exists. That is the next step, and it is not part of this one.
4. **No endpoint, authentication mechanism, authorization engine, Billing record,
   credential resolver, or Platform -> Core transport was added**, no ORM, SQLAlchemy,
   Alembic, or migration framework was introduced, `requirements.txt` is unchanged, and
   Core gained no database dependency.
5. **Open decisions are unchanged.** O-1 … O-7 remain **open**; O-8 stays resolved at
   contract level; O-9 stays resolved and implemented; O-10 … O-13 stay deferred.


## Персистентность Platform, репозитории и Unit of Work (D-PLATFORM-20) — русская версия

Реализует слой персистентности Platform поверх замороженной схемы Шага 2 и
фиксирует пробел в документации, обнаруженный в начале шага.

### Пробел в документации — зафиксирован, а не замаскирован

В задании как архитектурный ориентир назван
`docs/STAGE-1-STEP-3-PERSISTENCE-DESIGN.md`. **Этого файла в репозитории не было**: в
`git log --all` нет его истории, ни один другой документ не нёс его содержания, и
дизайн персистентности Шага 3 никогда не был написан. Вместо того, чтобы молча
выдумать дизайн или счесть отсутствующий документ правом на импровизацию,
реализация была выведена из трёх существующих нормативных источников: самого
задания Шага 3, разделов 17 и 18 Architecture Contract Stage 1 (порядок реализации и обязательные
тестовые инварианты) и контракта схемы Шага 2. Получившийся дизайн теперь
записан как `docs/STAGE-1-STEP-3-PERSISTENCE-DESIGN.md` с пометкой, что он написан после факта.
**Ни один замороженный контракт не был изменён** ради реализации.

### Принятые решения

- **Слоистость односторонняя, домен остаётся stdlib-only.** Приложение ->
  протоколы -> Unit of Work -> репозитории -> сессия -> пул -> PostgreSQL. Восемь доменных
  записей остаются замороженными dataclass'ами; `mapper.py` — единственный модуль,
  знающий и доменную запись, и физическую колонку; `asyncpg` импортируется
  только внутри `database.py` и лениво.
- **Два логина базы данных, а не один.** Деплой обязан обслуживать трафик
  арендатора и system-трафик разными логинами. Пул **отказывается создаваться**,
  если эффективная роль — суперпользователь, имеет `BYPASSRLS` или владеет таблицами.
- **Три именованных скоупа:** `tenant(...)`, `pre_tenant(...)` и `server(role=..., system_scope=...)`.
- **`forge.system_scope` не является механизмом авторизации.**
- **Репозитории никогда не управляют транзакциями**: слов `begin`, `commit`,
  `rollback` нет в `repositories.py`.
- **Ни один tenant-scoped репозиторий не принимает идентификатор организации.**
- **Нет generic `update(**fields)`.**
- **У `UsageRecordRepository` нет методов update и delete** — append-only выражен
  отсутствием операции.
- **Владение провайдерскими аккаунтами — два репозитория**, а не один с
  nullable-фильтром.
- **Идентификаторы валидируются на границе персистентности** через
  `mapper.as_uuid`.
- **Уникальность принадлежит базе, никогда предпроверке.**
- **Отказ авторизации никогда не выдаётся за отсутствие.**

### Два пробела в политиках Шага 2, закрытые здесь

Оба одной формы: **привилегия выдана, а политики к ней нет**. Ни одно
не было живым tenant escape.

1. System-роль имела `INSERT` на `users` без INSERT-политики, поэтому bootstrap-путь
   фактически не мог выполниться. Добавлена `users_system_insert`.
2. Tenant-роль имела `UPDATE` на `users` без UPDATE-политики, поэтому обновление
   совпадало с нулём строк, и репозиторий сообщал об отсутствующей записи —
   тихий неверный ответ. Добавлена `users_tenant_update`, ограниченная собственной
   строкой субъекта (`id = platform.current_user_id()`).

### Отклонённые альтернативы

- **Один логин с обеими ролями Platform.** Отклонено: это сворачивает
  границу, которую установила работа по безопасности.
- **Переключение в system-роль на тенантном соединении во время работы.**
  Отклонено: роль стала бы достижимой из тенантного трафика.
- **Один `ProviderAccountRepository` с nullable-фильтром организации.** Отклонено:
  `organization_id IS NULL` — маркер system-owned, и фильтр, принимающий его от тенантного
  вызывающего, — это и есть подстановочный символ, запрещённый
  D-PLATFORM-16.
- **Generic `update(**fields)`.** Отклонено: даёт неизменяемому полю способ быть
  записанным.
- **Предпроверка уникальности перед вставкой.** Отклонено: проверка-
  затем-вставка всё равно проигрывает гонку.
- **Возврат `None` при отказе политики.** Отклонено: делает отказ
  авторизации неотличимым от отсутствия.
- **Сделать `core_run_id` уникальным при реализации поиска.** Отклонено: K-8
  откладывает семантику retry/resume.
- **Ограничить терминальные статусы `RunRecord` в репозитории.** Отклонено:
  это O-7, который остаётся открытым.
- **Импорт драйвера на уровне модуля в репозиториях.** Отклонено: поместило
  бы драйвер в граф импортов Platform.

### Следствия

1. **Персистентность реализована и доказана на PostgreSQL 17.11**: 88 тестов
   персистентности, плюс 65 тестов схемы и 35 тестов безопасности из Шага 2, плюс 58
   доменных тестов из Шага 1.
2. **Два пробела в политиках закрыты**, поэтому bootstrap-путь работает, а обновление
   идентичности либо успешно, либо громко падает.
3. **Прикладные сервисы можно писать против `protocols.py`**, не зная о
   существовании PostgreSQL. Это следующий шаг, и он не входит в этот.
4. **Не добавлено ни эндпоинта, ни механизма аутентификации,
   ни движка авторизации, ни Billing-записи, ни разрешения кредилов, ни
   транспорта Platform -> Core**; ни ORM, ни SQLAlchemy, ни Alembic, ни фреймворк
   миграций; `requirements.txt` не изменён, и Core не получил зависимости от
   базы данных.
5. **Открытые решения не изменены.** O-1 … O-7 остаются **открытыми**; O-8 —
   решён на уровне контракта; O-9 — решён и реализован; O-10 … O-13 — отложены.

### Adversarial review of the persistence layer, and what it changed

An independent adversarial review of this step reported findings that the
implementation's own tests had missed. Each was reproduced before being accepted, and
each fix carries a regression test that fails against the previous behaviour. The
findings are recorded because two of them were ways to **defeat the role guard**, and a
guard that can be defeated is worse than no guard, since it is trusted.

**F1 — the role guard accepted a superuser login (fixed).** The guard asked
``pg_has_role(current_user, application_role, 'MEMBER')`` and only then read
``rolsuper``/``rolbypassrls`` — from the *substituted* role. Two independent holes
followed. First, ``pg_has_role`` returns true for a superuser for **every** role
without any grant, so the membership gate was vacuous and ``SET ROLE`` succeeded;
verified directly: a superuser has zero rows in ``pg_auth_members`` for
``forge_platform_app`` and ``pg_has_role`` still reports true. Second, the login's own
attributes were never examined when ``application_role`` was set, so a superuser login
was accepted — exactly the case an inline comment claimed was refused.

*Fix:* the login's attributes are checked before any switch, using both ``rolsuper`` /
``rolbypassrls`` and ``pg_has_role(role, 'postgres', 'USAGE')``, which also catches
membership in a privileged role; the substituted role's attributes are checked after
the switch; membership is proven before ``SET ROLE`` is attempted so a genuine
non-member gets this layer's ``ConnectionError`` rather than a driver error.

**F1b — one login could hold both scopes (fixed).** After
``GRANT forge_platform_system TO <tenant login>``, the tenant pool could step into the
system scope and read another tenant's memberships and organizations.

*Fix:* when the application role is the tenant role, the pool refuses to build if the
login is a member of the system role. The separation is now enforced by the layer, not
left to a deployment convention.

**F2 — the ownership check was pinned to schema ``public`` (fixed).** The migrations
create the tables unqualified, so they live wherever ``search_path`` resolves, and the
repositories use unqualified names too. A deployment whose search path put them
elsewhere was accepted, and the owning role could then run
``ALTER TABLE ... NO FORCE ROW LEVEL SECURITY`` and read every tenant.

*Fix:* the check now looks for a role owning any Platform table in **any** non-system
schema, reports the schema and table, and also covers partitioned and foreign tables.

**F3 — two organization methods could never succeed (fixed by removal).** No scope can
update ``organizations``: the system role holds ``SELECT, INSERT`` only and no UPDATE
policy exists for either role. Under the tenant role the statement was filtered to zero
rows, so the repository reported a *permission refusal as a missing tenant* — the exact
inversion the error hierarchy forbids. The capability was removed from the repository
and the protocol rather than left advertised and unrunnable; a policy-filtered UPDATE
is indistinguishable from absence, and the honest fix is to stop offering the method.

**F4 — ``memberships.create`` took the tenant from the record (fixed).** It violated the
layer's own rule that a tenant-scoped repository must never take the tenant from data
the caller assembled. Containment rested on the policy alone.

*Fix:* the tenant-scoped repository now takes **no** organization parameter and reads the
tenant from the session, so a caller cannot even express a cross-tenant write. Creating
the first membership of a brand-new tenant genuinely cannot be tenant-scoped, so it is
served by a separate `system_memberships` repository on the server-only scope — the
capability and its scope now match.

**F5 — raw ``TypeError``/``ValueError`` escaped the repository boundary (fixed).**
``json.dumps`` on unsupported provider metadata raised ``TypeError``, and on a circular
mapping ``ValueError``; a caller catching ``PersistenceError`` would have missed both.

*Fix:* a single ``metadata_json`` helper converts both into ``PersistenceError``.

**F6 — a bare string became a list of characters (fixed).** ``"keys:read"`` satisfies
``Sequence[str]``, so ``scopes_to_array`` stored nine single-character scopes — a silent
corruption of the field that says what an API key may do, with no error anywhere.

*Fix:* ``str`` and ``bytes`` are refused with ``InvalidIdentifierError``.

**F7 — ``find_by_slug`` disagreed with the writer (fixed).** A slug-less project maps to
``''`` in the domain and ``NULL`` in storage, but the lookup compared ``slug = ''``,
which cannot match ``NULL``, so an existence check reported a project missing that
``get`` returned.

*Fix:* ``IS NOT DISTINCT FROM`` against the normalized value. The first attempt at this
fix used plain equality on the normalized value and was **wrong** — ``slug = NULL`` is
never true in SQL — which the regression test caught; the episode is recorded because it
is the reason the test exists.

**F8 — a failed ``COMMIT`` looked successful, and a failed close was silent (fixed).**
``_in_transaction`` was cleared in a ``finally`` block, so a failed ``COMMIT`` left
``close()`` believing there was nothing to undo; and ``_exit_error`` was assigned but
never read, so a rollback failure vanished.

*Fix:* the flag is cleared only on success, the failure path routes through
``rollback()``, and ``Session.close_error`` exposes a close failure instead of
suppressing it. No pooled-connection tenant leak was demonstrated — ``asyncpg``
resets a connection on release and the pool re-runs setup on every acquire — but
``Session.close()``'s guarantee should not belong to the driver.

**Verified correct by the same review, unchanged:** every tenant-scoped statement is
bounded by the session tenant; the system-owned provider account repository is
unreachable from a tenant session in every operation; no SQL injection (only hardcoded
column constants and validated role names are interpolated; all values are bound);
transaction and scope handling does not leak a tenant across requests; ``normalize_error``
is idempotent and maps the documented SQLSTATEs; the mapping layer matches every domain
record field for field; nothing outside ``app/platform`` imports the persistence package
or the driver; and no money column exists anywhere.

**One concurrency note, from the same review.** Applying migrations concurrently against
one cluster raised ``tuple concurrently updated`` (SQLSTATE XX000), a transient conflict
on the shared role catalogue. The migration runner now retries that specific error a
bounded number of times, and the test fixture does the same for its role provisioning.
Any other error still surfaces immediately.


### Адверсариальное ревью слоя персистентности и что оно изменило — русская версия

Независимое адверсариальное ревью этого шага сообщило о находках, которые
собственные тесты реализации пропустили. Каждая была воспроизведена до того, как
быть принятой, и каждое исправление сопровождается регрессионным тестом, который
падает на предыдущем поведении. Находки зафиксированы, потому что две из них
были способами **обойти защиту роли**, а защита, которую можно обойти, хуже отсутствия
защиты, поскольку ей доверяют.

**F1 — защита роли принимала логин-суперпользователя (исправлено).** Защита
спрашивала ``pg_has_role(current_user, application_role, 'MEMBER')`` и только затем читала
``rolsuper``/``rolbypassrls`` — у *подставленной* роли. Отсюда две независимые дыры.
Во-первых, ``pg_has_role`` возвращает истину для суперпользователя для **любой** роли
без всякого гранта, поэтому проверка членства была пустой, а ``SET ROLE`` удавался;
проверено напрямую: у суперпользователя ноль строк в ``pg_auth_members`` для
``forge_platform_app``, а ``pg_has_role`` всё равно сообщает истину. Во-вторых, собственные
атрибуты логина никогда не проверялись, когда ``application_role`` была задана.

*Исправление:* атрибуты логина проверяются до любого переключения через
``rolsuper``/``rolbypassrls`` и ``pg_has_role(role, 'postgres', 'USAGE')``; атрибуты подставленной роли — после
переключения; членство доказывается до попытки ``SET ROLE``.

**F1b — один логин мог держать оба скоупа (исправлено).** После
``GRANT forge_platform_system TO <tenant login>`` тенантный пул мог войти в system-скоуп и читать чужие
memberships и organizations. *Исправление:* когда запрошена tenant-роль, пул отказывается
строиться, если логин является членом system-роли.

**F2 — проверка владения была привязана к схеме ``public`` (исправлено).** Миграции
создают таблицы без квалификации, поэтому они лежат там, куда указывает ``search_path``.
Развёртывание, у которого таблицы оказывались в другой схеме, принималось, и
владеющая роль могла выполнить ``ALTER TABLE ... NO FORCE ROW LEVEL SECURITY`` и читать всех
арендаторов. *Исправление:* проверка теперь ищет владение в **любой** несистемной
схеме и сообщает схему и таблицу.

**F3 — два метода организаций не могли успеть никогда (исправлено удалением).**
Ни один скоуп не может обновлять ``organizations``. Под tenant-ролью оператор фильтровался до
нуля строк, поэтому репозиторий сообщал о *отказе в правах как об отсутствующем арендаторе**
— точно инверсия, которую запрещает иерархия ошибок. Возможность удалена из
репозитория и протокола.

**F4 — ``memberships.create`` брал арендатора из записи (исправлено).** Это нарушало
собственное правило слоя. *Исправление:* tenant-scoped репозиторий теперь **не
принимает** параметра организации; создание первого membership нового арендатора обслуживает
отдельный репозиторий `system_memberships` на server-скоупе.

**F5–F8 — граничные дефекты (исправлены).** Несериализуемые metadata
больше не выпускают сырой ``TypeError``/``ValueError``; строка больше не разбивается на символы в
``scopes``; ``find_by_slug`` согласован с пишущим кодом; неудавшийся ``COMMIT`` больше не выглядит
успешным, и неудавшееся закрытие видно через ``Session.close_error``.

## Stage 1 persistence hardening after adversarial review (D-PLATFORM-21)

A second adversarial review of the Step 3 persistence layer reported six findings:
one HIGH and five MEDIUM. All six are fixed, each with a regression test that fails
against the previous behaviour. Two of them changed a design constraint rather than
only a line of code, and those changes are recorded here because they narrow what the
schema claims.

### H-1 — a COMMIT on an aborted transaction reported success

**Measured, not assumed.** PostgreSQL accepts ``COMMIT`` on a transaction that an
earlier failed statement aborted, performs a **rollback**, and returns the status
string ``ROLLBACK`` without raising. `asyncpg` passes that string through. So a
service that caught a repository error and continued would exit its Unit of Work
cleanly while every write in it was discarded — the worst possible outcome, because
nothing reports it.

Two independent signals are now checked, because neither covers the other:

* the session records that a statement failed inside the transaction, so a failure it
  saw is never forgotten even after the caller catches the exception;
* the ``COMMIT`` status string is inspected, so a transaction that is already aborted
  for a reason this session did not record is still caught.

On either signal the transaction is rolled back and a `TransactionError` names the
original cause. `ROLLBACK TO SAVEPOINT` is the one statement exempt from the aborted
guard, because it is the statement that repairs the transaction; the server accepts
nothing else in that state, so the exemption cannot be used to smuggle work through.
A failed statement now also makes further statements on that session fail with a named
error instead of a driver error the caller has to decode.

### M-1 — the role/scope boundary is proven from the catalogue

`application_role=None` is no longer a valid state: a pool must step down, and the
constructor refuses the alternative. `pg_has_role` is **not** used to decide
membership, because it is vacuously true for a superuser for every role and also
follows inherited membership, which `NOINHERIT` deliberately does not exercise.
Membership is read from `pg_auth_members`, the catalogue a `GRANT` writes to, and:

* the login must be an explicit member of the role it steps down to;
* the login must **not** be an explicit member of the other Platform role, so one
  login cannot serve both scopes;
* the login's own attributes are read before the switch, and the effective role's
  after it, so neither a privileged login nor a privileged substitute is accepted;
* no superuser name is named anywhere — privilege is read as `rolsuper` /
  `rolbypassrls`, so the check holds on a cluster whose superuser is called something
  else.

`SET LOCAL ROLE` remains the mechanism for the tenant and system scopes.

### M-2 — discovery is bounded by a declared subject

The system scope could read every identity, membership, and tenant, gated only by the
scope flag. Discovery is now bound to **one subject per transaction**, and the binding
is enforced where it can be expressed without breaking bootstrap.

The first attempt put the subject in the policy
(`USING (... AND id = platform.current_user_id())`). **That does not work**, and the
reason is worth keeping: `INSERT ... RETURNING` also consults the SELECT policies for
the returned columns, so bootstrap — which by definition creates the subject and
cannot yet declare one — was refused its own new row. The subject-bound read and the
bootstrap write cannot both be expressed by one predicate on the same table.

So `Session` owns the binding: `set_subject` records the declared subject,
`require_subject` **refuses** a discovery read that names a different one, and every
discovery read passes its argument through it. The policy keeps the half a policy can
decide: the system scope must be declared, or the scope reads nothing. `users.list_all`
is **removed** — a directory read cannot survive this rule, and a method named
`list_all` that can only ever return one row is a trap. `find_by_email` and the
membership and organization discovery reads are bounded to the subject.

`forge.user_id` is **not authentication**, and this is stated in the migration as well
as here: any session can set a custom GUC, so a session that can already use this role
could name any subject. What the binding buys is that a discovery read is expressed as
"the subject this transaction was bound to" rather than "everyone", so a caller mistake
yields one subject's rows instead of the whole directory. Deciding who the subject
really is belongs to the authentication path above this layer.

### M-3 — the tenant UPDATE on `users` is column-limited

The tenant role held `UPDATE` on the whole row, so a subject could change its own
`status` — a suspended account could un-suspend itself — and its own `email`. The
privilege is now `UPDATE (display_name, updated_at)` with the policy unchanged, and the
two Platform scopes hold **disjoint write capabilities** on this table:

| scope | may write |
| --- | --- |
| tenant | `display_name` (the subject's own row) |
| system | `status`, `email` (any row, with a policy to match) |

`updated_at` is granted to both because every writer in this layer stamps it and a
column-level grant does not cover a column it does not name; the database supplies the
value, so it widens nothing a caller can decide. `set_status` and `set_email` were
**removed** from the tenant repository and now live on a server-side repository, and
the tenant scope has no method that could name them at all.

### M-4 — lifecycle transitions are compare-and-set

`finish` and `set_status` carried no state guard, so two workers could both act on one
run and a finished run could be overwritten. Both now apply the guard **inside the
UPDATE** — a read-then-update would let both writers pass the read — and raise a new
`ConcurrentModificationError` when no row matches, which is distinct from
`EntityNotFound` on purpose: the row usually exists, and reporting "not found" for a
lost race sends a caller looking for the wrong problem.

The conditions are `status <> 'queued'` (a queued run has no Core identity or
initiator) and `status NOT IN (<terminal>)`, so a finished run cannot be finished
again or revived. An optional `expected_status` narrows the guard when the caller knows
the state it observed. **O-7 is not closed by this**: which statuses are terminal is
still open, and the terminal set here is deliberately the conservative one, chosen so
that no ordinary transition can reopen a run that has finished.

Reading a run's state for that guard needs a system-scope read, so this scope gained
`SELECT` on `run_records` — with the grant and the policy added **together**, because
this repository has already been bitten twice by a privilege with no policy and the
reverse is the same inconsistency from the other side. It has no `INSERT`, no `UPDATE`,
and no `DELETE`: it can observe a run, not create, modify, or erase one.

### M-5 — a mismatching `organization_id` is refused, never overridden

A tenant-scoped write took the tenant from the session and silently ignored a record
naming another tenant. Writing it into the session's tenant is confused-deputy
behaviour: the caller believes it addressed one tenant and another was written.
`usage.append` now requires the record's `organization_id` to equal the session's
tenant and refuses a disagreement with both identifiers in the error; a record with no
`organization_id` is refused as well, because inferring one is the same implicit
redirect. The membership insert never had the parameter at all — the tenant comes from
the session — and the system repositories keep their own explicit and strict handling.

### Rejected alternatives

- **Trusting the `COMMIT` status string alone.** Rejected: a failure the session
  recorded is more precise about the cause, and the string alone would make the error
  message useless.
- **Trusting the session flag alone.** Rejected: it only knows about failures this
  session saw.
- **A savepoint around every repository call.** Rejected: it would change the
  transaction semantics of every write to make one diagnostic easier, and a savepoint
  per call would silently change what a partial failure means.
- **`pg_has_role` for membership.** Rejected: vacuous for a superuser, and it follows
  inherited membership.
- **Naming `postgres` as the superuser to reject.** Rejected: not deployment-neutral.
- **The subject predicate in the policy.** Rejected **after measuring it**: it makes
  bootstrap's `INSERT ... RETURNING` unsatisfiable.
- **Keeping `users.list_all` and bounding it by the subject.** Rejected: it would
  return one row under a name that claims a directory.
- **A `FOR ALL` system policy on `run_records`.** Rejected: it would grant write
  capabilities the contract does not ask for. `SELECT` is granted individually.

### Consequences

1. **A caught statement failure can no longer be committed silently.** The transaction
   is rolled back and the failure is named.
2. **One login cannot serve both scopes**, and the refusal is derived from the
   catalogue rather than from a configuration convention.
3. **Discovery is one subject per transaction**, and bootstrap still works because the
   binding lives in the layer rather than in a predicate that would block it.
4. **Neither Platform scope can do the other's job on `users`**, enforced by
   column-level privileges with a policy attached to each.
5. **A lifecycle race is a named error**, and a finished run cannot be reopened by an
   ordinary transition.
6. **No tenant-scoped write can be redirected to another tenant by the data it is
   handed.**
7. **O-7 … O-13 remain open or deferred**, unchanged. No new decision was invented to
   make a fix convenient, and no frozen contract was altered.


## Усиление персистентности Step 1 после адверсариального ревью (D-PLATFORM-21) — русская версия

Второе адверсариальное ревью слоя персистентности Шага 3 сообщило о шести
находках: одна HIGH и пять MEDIUM. Все шесть исправлены, каждая — с регрессионным
тестом, падающим на предыдущем поведении. Две из них изменили дизайн-ограничение,
а не только строку кода, и эти изменения зафиксированы здесь, потому что они
сужают то, что заявляет схема.

### H-1 — COMMIT на aborted-транзакции сообщал об успехе

**Измерено, а не предположено.** PostgreSQL принимает ``COMMIT`` на
транзакции, которую предыдущий неудавшийся оператор перевёл в aborted-состояние, выполняет
**откат** и возвращает строку статуса ``ROLLBACK``, не поднимая ошибку. Поэтому сервис,
который перехватил ошибку репозитория и продолжил, вышел бы из Unit of Work чисто, а все
записи в нём были бы отброшены.

Теперь проверяются два независимых сигнала: сессия помнит, что оператор внутри
транзакции не удался, а строка статуса ``COMMIT`` проверяется отдельно. При любом из
сигналов транзакция откатывается, а `TransactionError` называет исходную причину.
`ROLLBACK TO SAVEPOINT` — единственный оператор, исключённый из aborted-защиты, потому что он
восстанавливает транзакцию.

### M-1 — граница ролей доказывается по каталогу

`application_role=None` больше не допустимое состояние. `pg_has_role` **не**
используется для решения о членстве: она ложно истинна для суперпользователя для
любой роли. Членство читается из `pg_auth_members`. Логин обязан быть явным членом своей
роли и **не** быть членом другой роли Platform. Имя суперпользователя нигде не
называется.

### M-2 — discovery ограничен объявленным субъектом

Первая попытка поместить субъект в политику **не работает**:
`INSERT ... RETURNING` также консультирует SELECT-политики, поэтому bootstrap лишался собственной
новой строки. Привязка перенесена в `Session`: `require_subject` **отказывает** discovery-чтению,
называющему другого субъекта. Политика оставляет себе то, что может решить:
скоуп должен быть объявлен. `users.list_all` **удалён**.

`forge.user_id` — **не аутентификация**: любая сессия может установить
пользовательскую GUC. Привязка даёт то, что discovery выражается как «субъект, к
которому привязана транзакция», а не «все».

### M-3 — tenant UPDATE на `users` ограничен колонками

Привилегия теперь `UPDATE (display_name, updated_at)`, и два скоупа Platform имеют
**непересекающиеся права записи**: tenant — `display_name`, system — `status`, `email`. `set_status` и `set_email`
**удалены** из tenant-репозитория.

### M-4 — переходы жизненного цикла — compare-and-set

Гард встроен **в сам UPDATE**; новый `ConcurrentModificationError` отличается от
`EntityNotFound`. Условия: `status <> 'queued'` и `status NOT IN (<terminal>)`. **O-7 не закрытто**: набор
терминальных статусов здесь консервативный. System-скоуп получил `SELECT` на `run_records`
вместе с привилегией и политикой; `INSERT`, `UPDATE` и `DELETE` не выданы.

### M-5 — несовпадающий `organization_id` отклоняется, а не переопределяется

`usage.append` требует совпадения `organization_id` записи с арендатором сессии и
отказывает запись без `organization_id` — выводить его самому было бы тем же неявным
перенаправлением.

### Отклонённые альтернативы

- **Доверять только строке статуса `COMMIT`.** Отклонено: флаг точнее называет
  причину.
- **Доверять только флагу сессии.** Отклонено: он знает только о тех сбоях,
  которые видела сама.
- **Savepoint вокруг каждого вызова репозитория.** Отклонено: изменило бы
  семантику каждой записи.
- **`pg_has_role` для членства.** Отклонено: ложно истинна для суперпользователя.
- **Имя `postgres` как запретное.** Отклонено: не deployment-neutral.
- **Предикат субъекта в политике.** Отклонено **после измерения**:
  он делает `INSERT ... RETURNING` невыполнимым для bootstrap.
- **Оставить `users.list_all`.** Отклонено: вернул бы одну строку под
  именем, обещающим каталог.
- **`FOR ALL` для system-скоупа на `run_records`.** Отклонено: выдало бы права записи,
  которых контракт не требует.

### Следствия

1. **Перехваченная ошибка оператора больше не может быть закоммичена молча.**
2. **Один логин не может обслуживать оба скоупа.**
3. **Discovery — один субъект на транзакцию**, и bootstrap работает.
4. **Ни один скоуп не может сделать работу другого на `users`.**
5. **Гонка жизненного цикла — именованная ошибка**, и завершённый запуск
   нельзя переоткрыть.
6. **Ни одна tenant-scoped запись не может быть перенаправлена в другого
   арендатора данными, которые ей передали.**
7. **O-7 … O-13 остаются открытыми/отложенными** без изменений.

## Stage 1 Step 4: authentication boundary and Application Services contracts (D-PLATFORM-22)

Design step. Records the trust boundary between a verified identity, an authorized
operation, and the persistence and execution layers, and records exactly one executable
contract. Contract document:
`docs/STAGE-1-STEP-4-AUTH-APPLICATION-SERVICES-DESIGN.md`.

### What is accepted

- **A verified identity exists only as `AuthenticatedPrincipal`.** Four fields:
  `subject_id`, `subject_kind`, `method`, `verified_at`. Deliberately absent:
  `organization_id`, `project_id`, role, scopes, email, display name, workspace, and any
  credential material. A principal answers **who**; every **what** is resolved per
  operation. A principal that carried authority would make "verified identity" and
  "authorized operation" the same object, and the authorization step could be skipped by
  constructing one.
- **Only a verifier may produce one.** `AuthenticatedPrincipal.create(...)` requires a
  module-private sentinel, mirroring the `AuthorizedExecution` mechanism Core already
  uses. This is an **accident guard, not a boundary**: Python cannot stop hostile
  in-process code, and the same caveat is already documented for `AuthorizedExecution`.
  What it guarantees is that no ordinary code path can mint an identity, so "the client
  sent us a `user_id`" has no route to becoming a principal.
- **The authentication mechanism is not designed here.** O-2 stays open. The port is
  defined; no implementation ships, and nothing in `app/platform/persistence/` may ever
  verify a password, a JWT, or an external token — a repository that could verify a
  credential would become an authentication authority by accident.
- **Authorization is decided in the application layer, never in persistence.** An ordered,
  fail-closed algorithm resolves principal -> ACTIVE membership -> organization ->
  project -> required role, and the tenant-scoped Unit of Work is opened **only after**
  every check passes, so a denied operation cannot leave a partial write.
  `PermissionDeniedError` from row-level security reaching the application layer is
  treated as a **bug signal** — the layer should have denied first — and is surfaced as a
  denial, never softened into "not found".
- **A client claim is never repaired.** A wrong `organization_id` is refused; the server
  does not substitute the right one. Substitution teaches the client that a wrong
  identifier works and hides the defect from the operator.
- **Platform may deny a launch but never expand Core's authority.** Platform resolves and
  may only **narrow** the profile, the allowed-command set, the allowed tools, and network
  access; `ExecutionCoordinator` re-derives all of it, `RunScope` validates the workspace
  root and the full command identity, and only the coordinator can mint
  `AuthorizedExecution`. An `AuthorizedExecution` is not client-creatable, and
  `dataclasses.replace` on a principal is refused because the generated `__init__` is not
  in use.
- **`forge.user_id` is not authentication.** The GUC decides which subject a transaction
  is *willing to talk about*; it never decides who the caller *is*. Step 3 enforces "one
  subject, and always the same one"; Step 4 owns "only a verifier may name the subject".
- **M-2's guarantee is a composition, not a single layer.** The RLS policy decides only
  that the system scope is declared; the session decides *one subject per transaction*;
  the verifier decides *this subject is who the caller is*. Step 4 carries the subject to
  `pre_tenant` from the principal and from nowhere else, and no use case accepts a
  `user_id` parameter.
- **Two error vocabularies, kept apart.** Persistence errors stay in persistence; the
  application layer translates them into a closed refusal code list. An API layer that
  had to catch `UniqueViolationError` would be coupled to the schema.
- **Cross-tenant attempts are answered `not_found` rather than `not_permitted`**
  (recorded as Q-6). `not_permitted` would disclose that an identifier exists, which
  allows enumeration. Within a caller's own tenant a genuine shortfall is
  `not_permitted`. The usability cost is named, and the owner may reverse it.
- **Roles are not turned into a permission matrix.** `MembershipRole` already exists as a
  vocabulary, but no accepted contract states what each role may do, and section 17 of the
  architecture contract orders "Membership / RBAC" as step 5. Step 4 fixes only the
  mechanism: an operation declares `any_active_member` or `named_roles`, the role is
  resolved from the membership and never from a claim, and `OWNER` cannot be
  manufactured. `BILLING` has no Step 4 operation at all. A proposed default matrix is
  offered as a proposal, not a decision (Q-2).
- **`TrustedExecutionRequest` is a contract here, not an implementation.** The name
  appears nowhere in `app/`; step 10 owns it. The draft shape separates trusted
  server-derived fields, correlation-only fields, and fields that must never appear — in
  particular any plaintext provider secret in any spelling, which the contract explicitly
  rejects.
- **`EphemeralSecretResolver` remains a named boundary with no implementation.** R-2
  defines the port on Core's side; step 7 owns it. The request carries an opaque handle;
  `ProviderAccount.secret_ref` is extended, not replaced.
- **The Platform id and the Core run id stay distinct** (R-1). The request carries
  `platform_run_id`; Core produces `core_run_id` and returns it in
  `PhysicalTelemetry.run_id`; `UsageRecord` carries both under distinct names.
- **The ordering problem between the durable row and the Core launch is named, not
  assumed away.** All three orderings leave a failure mode; the recommendation is to
  commit the `RunRecord` first and make the orphan **visible** rather than impossible,
  with a reconciliation sweep as the R-6 mechanism. R-6 leaves the mechanism open, so this
  is a proposal (Q-3), not a decision.

### Rejected alternatives

- **A principal carrying tenancy or role.** Rejected: it collapses verification and
  authorization into one object, and the authorization step becomes skippable.
- **Creating a principal with the dataclass constructor.** Rejected: an unverified
  identity would be one keyword away.
- **Putting the credential check in the persistence layer.** Rejected: a repository that
  can verify a credential is an authentication authority by accident.
- **Treating `forge.user_id` as proof.** Rejected: any session can set a custom setting.
- **Substituting the correct organization for a wrong client claim.** Rejected:
  confused-deputy behaviour, and it hides the defect.
- **One error vocabulary across both layers.** Rejected: it couples the API to the schema.
- **Answering a cross-tenant attempt with `not_permitted`.** Rejected: it discloses
  existence and permits enumeration.
- **Freezing a complete RBAC matrix.** Rejected: step 5 owns it and the owner has not
  decided it.
- **Implementing a fake authentication service so the suite can pass.** Rejected: it would
  make the tests green while proving nothing.

### Consequences

1. **The trust boundary is executable, and small.** One module,
   `app/platform/principal.py`, plus `tests/test_platform_auth_contracts.py` (26 tests, no
   database needed).
2. **The application layer has a contract to be written against.** Nine use cases with
   inputs, authorization requirements, transaction boundaries, error behaviour and
   idempotency requirements, and a named ordering problem for the launch path.
3. **Nothing is implemented beyond the principal contract.** No service, no endpoint, no
   authorization engine, no transport, no schema change.
4. **Open decisions are unchanged and several are newly raised.** O-1..O-7 stay open;
   Q-2 (first role matrix), Q-3 (R-6 mechanism), Q-4 (where Platform idempotency is
   recorded), and Q-5 (how the result intake is authenticated) need an owner decision
   before the steps that depend on them. Q-4 and Q-3 must be answered before AS-3 and AS-6
   can be implemented, so the recommended first implementation task deliberately avoids
   both.


## Stage 1 Шаг 4: граница аутентификации и контракты Application Services (D-PLATFORM-22) — русская версия

Шаг проектирования. Фиксирует границу доверия между проверенной идентичностью,
разрешённой операцией и слоями персистентности и исполнения, и записывает ровно один
исполняемый контракт. Документ контракта:
`docs/STAGE-1-STEP-4-AUTH-APPLICATION-SERVICES-DESIGN.md`.

### Что принято

- **Проверенная идентичность существует только как `AuthenticatedPrincipal`.** Четыре поля:
  `subject_id`, `subject_kind`, `method`, `verified_at`. Намеренно отсутствуют:
  `organization_id`, `project_id`, роль, scopes, email, display name, workspace и любой
  материал креденла. Principal отвечает на вопрос **кто**; каждое **что**
  разрешается для каждой операции. Principal, несущий authority, сделал бы
  «проверенную идентичность» и «авторизованную операцию» одним объектом, и шаг
  авторизации можно было бы пропустить, просто создав его.
- **Создать его может только верификатор.** `create(...)` требует
  модульно-приватный sentinel. Это **защита от случайности, а не граница**: Python
  не остановит враждебный внутрипроцессный код, и та же оговорка уже задокументирована
  для `AuthorizedExecution`.
- **Механизм аутентификации здесь не проектируется.** O-2 остаётся открытым.
  Порт определён; никакая реализация не поставляется, и ничто в
  `app/platform/persistence/` никогда не должно проверять пароль, JWT или внешний токен.
- **Авторизация решается в application-слое, никогда в персистентности.**
  Упорядоченный fail-closed алгоритм разрешает principal -> ACTIVE membership ->
  организация -> проект -> требуемая роль, и tenant-scoped Unit of Work открывается **только
  после** всех проверок, поэтому отклонённая операция не может оставить частичную
  запись. `PermissionDeniedError` от RLS, дошедший до application-слоя, считается **сигналом
  бага** и показывается как отказ, никогда не смягчается в «не найдено».
- **Утверждение клиента никогда не «ремонтируется».** Неверный
  `organization_id` отклоняется; сервер не подставляет правильный.
- **Platform может отказать в запуске, но никогда не расширяет authority Core.**
  Platform может только **сужать** профиль, набор разрешённых команд, инструментов и
  сетевой доступ; `ExecutionCoordinator` перевыводит всё заново, `RunScope` проверяет
  workspace-корень и полную идентичность команды, и только координатор может создать
  `AuthorizedExecution`. `AuthorizedExecution` нельзя создать из клиента, а `dataclasses.replace` для principal
  отвергается, потому что сгенерированный `__init__` не используется.
- **`forge.user_id` не является аутентификацией.** GUC решает, о каком субъекте
  транзакция *готова говорить*; он никогда не решает, *кем является* вызывающий.
- **Гарантия M-2 — это композиция, а не один слой.** RLS-политика
  решает только, что system-скоуп объявлен; сессия — *один субъект на транзакцию*;
  верификатор — *этот субъект есть тот, кем является вызывающий*.
- **Два словаря ошибок разделены.** Ошибки персистентности остаются в
  персистентности; application-слой транслирует их в закрытый список кодов отказа.
- **Кросс-арендаторные попытки отвечаются `not_found`, а не `not_permitted`** (Q-6).
- **Роли не превращаются в матрицу разрешений.** Механизм фиксируется
  (`any_active_member` или `named_roles`), а матрица предлагается как предложение (Q-2).
- **`TrustedExecutionRequest` — здесь контракт, а не реализация.** Имя не
  встречается в `app/`; им владеет шаг 10.
- **`EphemeralSecretResolver` остаётся названной границей без реализации.**
- **Идентификатор Platform и идентификатор запуска Core остаются различными** (R-1).
- **Проблема порядка между устойчивой строкой и запуском Core названа, а не
  предположена замолчанной.** Рекомендация — сначала коммит `RunRecord`, orphan
  сделать **видимым**, а не невозможным, со сверкой-развёрткой как механизмом R-6 (Q-3).

### Отклонённые альтернативы

- **Principal, несущий аренду или роль.** Отклонено: сворачивает
  проверку и авторизацию в один объект.
- **Создание principal через конструктор dataclass.** Отклонено: непроверенная
  идентичность была бы на расстоянии одного ключевого слова.
- **Проверка креденла в слое персистентности.** Отклонено: репозиторий,
  способный проверить креденл, становится authority аутентификации по случайности.
- **Трактовка `forge.user_id` как доказательства.** Отклонено: любая сессия
  может установить пользовательскую настройку.
- **Подстановка правильной организации вместо неверного утверждения.** Отклонено:
  confused-deputy поведение, и оно скрывает дефект.
- **Один словарь ошибок на оба слоя.** Отклонено: связывает API со схемой.
- **Ответ `not_permitted` на кросс-арендаторную попытку.** Отклонено: раскрывает
  существование и позволяет перечисление.
- **Замораживание полной RBAC-матрицы.** Отклонено: им владеет шаг 5,
  и владелец этого не решил.
- **Реализация fake-сервиса аутентификации, чтобы набор прошёл.** Отклонено: это
  сделало бы тесты зелёными, ничего не доказывая.

### Следствия

1. **Граница доверия исполняема и мала.** Один модуль,
   `app/platform/principal.py`, плюс `tests/test_platform_auth_contracts.py` (26 тестов, БД не нужна).
2. **У application-слоя есть контракт, под который писать.** Девять use cases
   с входами, требованиями авторизации, границами транзакций, поведением при
   ошибках и требованиями идемпотентности, и названная проблема порядка для
   пути запуска.
3. **За пределами контракта principal ничего не реализовано.** Ни сервиса, ни
   эндпоинта, ни движка авторизации, ни транспорта, ни изменения схемы.
4. **Открытые решения не изменены, и несколько подняты заново.** O-1..O-7
   остаются открытыми; Q-2 (первая матрица ролей), Q-3 (механизм R-6), Q-4 (где
   записывается идемпотентность Platform) и Q-5 (как аутентифицируется приём результата)
   требуют решения владельца.

## Offline Task Execution Path v0.1

Status: **IMPLEMENTED** (2026-10-18). Verified by
`tests/test_offline_task_execution.py` plus the existing task, idempotency,
history, dashboard, desktop and launcher suites.

### Context

`POST /api/tasks/run` with the offline provider (`mock`, the only provider this
composition may use without credentials) answered with one canned sentence from
`MockProvider.generate` -- `MockProvider response: <prompt>` -- and recorded a
fixed five events. The owner asked for real work on that path instead of a stub,
in an offline/deterministic/zero-cost form ("хочу реальной работы, хотя бы в
тестовом режиме").

### Accepted decisions

- **The offline selection runs the canonical harness loop.** When the resolved
  provider for a task is the offline provider, `ForgeApiService.run_task_detailed`
  delegates to `ForgeApiService._run_offline_task`, which executes the task with
  `AgentHarness.run` -- the same loop, `Planner`, `ProjectDiscovery`,
  `AcceptanceGate` and durable `RunStore` writer the production entry points use.
  No second loop was written.
- **The work is derived from the task text, deterministically.**
  `app/agent_runtime/offline_task.py` turns the description into at most six
  read-only checks (list a directory, read the first N lines of a file, prove a
  path exists, count entries, summarise the workspace). Keyword and path parsing
  only: no model, no network, no randomness, no clock. One
  `AcceptanceCriterion` is derived per check, so the run is judged by checks it
  actually performed.
- **The observation is real and read-only.** `OfflineWorkspaceVerifier`
  implements the harness's existing verifier port and reads the service's own
  `Workspace` (bounded listing, bounded walk, bounded excerpt). It never writes,
  starts a process, or opens a socket, and it refuses credential-shaped paths
  through the canonical `app/execution/secret_files.py` recognizer.
- **The offline decision provider can only verify and conclude.**
  `OfflineTaskDecisionProvider` proposes `RUN_VERIFICATION`, then `COMPLETE_RUN`
  on a passing acceptance or `FAIL_RUN` otherwise. It never proposes `EXECUTE`,
  `INVOKE_TOOL`, `REQUEST_REVISION`, or an approval wait, so no command, tool, or
  unbounded retry is reachable on this path.
- **No authority was widened.** The offline run builds its own frozen `RunScope`
  from the service's own workspace and execution profile with
  `allowed_tool_ids=frozenset()` and `allowed_execution_commands=frozenset()`, and
  carries no `execution_requests` and no tool intents. The fail-closed
  "no authorized project" guard, the idempotency claim, `RunScope`, the project
  binding, and workspace isolation are unchanged.
- **Named providers are unchanged.** The branch is taken only when the resolved
  provider is the offline provider; any other provider still dispatches through
  `RunExecutor` -> `Orchestrator` -> provider chain exactly as before.
- **Telemetry stays honest.** The path makes no provider call, so it reports
  `tokens=0`, `cost=0.0` with the existing `MOCK` basis (a measured zero), a
  measured wall-clock duration, `provider_used="mock"` and
  `model_used="deterministic-offline"` (no model produced anything).
- **The response model is unchanged.** `TaskRunResponse.to_dict()` keeps its
  byte-pinned key set; the task-derived result travels in the existing `output`
  text, and the per-check steps are in the durable event trail that
  `GET /api/runs/{run_id}` already exposes. No UI shape change was needed.
- **The observation bound is narrower, not wider.** The offline loop supplies a
  `ProjectDiscovery` with reduced `ScanLimits` (300 files, 4 MB, depth 6) because
  one interactive task must finish inside a request budget; a narrower bound can
  only observe less.

### Rejected alternatives

- **Letting the mock provider "answer" with the task text.** That is what the
  canned string already did: text in, text out, no observed work.
- **Deriving a host command from the description.** Rejected: it would let client
  text select a command, which the declared-execution decision explicitly
  refuses.
- **Granting a read tool to the run scope.** Rejected: the tool perimeter is
  operator composition, and a client-reachable path must not widen it. Read-only
  observation is performed inside the verifier port, which holds no execution
  authority.
- **Fabricating a passing acceptance for tasks with nothing to check.** A check
  that cannot be performed is `FAILED`/`DENIED`, and the run reports it; an empty
  or unusable description still gets one honest workspace summary.

### Consequences and open items

1. **Read-only by design.** The offline path inspects the workspace; it does not
   change it. Producing code offline still requires a real provider and the
   operator-declared execution/permission chain.
2. **No provider-attempt accounting for offline runs.** Nothing was consumed, so
   no attempt is recorded; the dashboard's provider tables legitimately show no
   row for such a run. Task history and acceptance history are recorded.
3. **The task text selects what is observed.** The boundary is
   `Workspace.normalize_relative_path` plus the secret recognizer; a path outside
   the workspace is refused with `workspace_or_path_rejected` and fails the run.
4. **`MockProvider` itself is unchanged.** Its canned sentence is still what a
   direct `Orchestrator.dispatch` through the mock provider returns; only the API
   task path was redirected.

## Offline Task Execution Path v0.1 -- русская версия

Статус: **РЕАЛИЗОВАНО** (2026-10-18). Проверено
`tests/test_offline_task_execution.py` и существующими наборами тестов задачи,
идемпотентности, истории запусков, дашборда, desktop и launcher.

### Контекст

`POST /api/tasks/run` с offline-провайдером (`mock` -- единственный провайдер,
доступный без credentials) отвечал одной заготовленной фразой из
`MockProvider.generate` (`MockProvider response: <prompt>`) и записывал
фиксированные пять событий. Владелец попросил реальную работу на этом пути вместо
заглушки, в offline/детерминированном/нулевом по стоимости виде.

### Принятые решения

- **Offline-выбор запускает канонический цикл harness.** Если выбранный
  провайдер задачи -- offline, `ForgeApiService.run_task_detailed` передаёт
  выполнение в `ForgeApiService._run_offline_task`, который исполняет задачу
  через `AgentHarness.run` -- тот же цикл, `Planner`, `ProjectDiscovery`,
  `AcceptanceGate` и тот же durable-писатель `RunStore`, что и production-точки
  входа. Второй цикл не написан.
- **Работа выводится из текста задачи детерминированно.**
  `app/agent_runtime/offline_task.py` превращает описание не более чем в шесть
  read-only проверок (перечислить каталог, прочитать первые N строк файла,
  подтвердить существование пути, посчитать записи, дать сводку рабочей области).
  Только разбор ключевых слов и путей: без модели, сети, случайности и часов.
  На каждую проверку выводится `AcceptanceCriterion`, поэтому запуск судится по
  фактически выполненным проверкам.
- **Наблюдение реальное и read-only.** `OfflineWorkspaceVerifier` реализует
  существующий порт верификатора harness и читает собственную `Workspace`
  сервиса (ограниченный листинг, ограниченный обход, ограниченная выдержка).
  Он ничего не пишет, не запускает процессы и не открывает сокеты, а
  пути, похожие на credentials, отклоняет через канонический распознаватель
  `app/execution/secret_files.py`.
- **Offline decision provider умеет только проверять и завершать.**
  `OfflineTaskDecisionProvider` предлагает `RUN_VERIFICATION`, затем
  `COMPLETE_RUN` при пройденной приёмке либо `FAIL_RUN`. Он никогда не
  предлагает `EXECUTE`, `INVOKE_TOOL`, `REQUEST_REVISION` или ожидание
  approval, поэтому ни команда, ни инструмент, ни неограниченный повтор на этом
  пути недостижимы.
- **Ни одна authority не расширена.** Offline-запуск строит собственный
  замороженный `RunScope` из собственной workspace и execution profile сервиса с
  `allowed_tool_ids=frozenset()` и `allowed_execution_commands=frozenset()`, без
  `execution_requests` и tool intents. Fail-closed проверка "нет
  авторизованного проекта", заявка идемпотентности, `RunScope`, привязка
  проекта и изоляция workspace не изменены.
- **Именованные провайдеры не изменены.** Ветка выбирается только когда
  выбранный провайдер -- offline; любой другой по-прежнему идёт через
  `RunExecutor` -> `Orchestrator` -> цепочку провайдеров.
- **Телеметрия остаётся честной.** Путь не вызывает провайдера, поэтому
  сообщает `tokens=0`, `cost=0.0` с существующим basis `MOCK` (измеренный ноль),
  измеренную длительность, `provider_used="mock"` и
  `model_used="deterministic-offline"` (ни одна модель ничего не произвела).
- **Модель ответа не изменена.** Набор ключей `TaskRunResponse.to_dict()`
  остаётся прежним; результат задачи передаётся в существующем тексте `output`, а
  пошаговые проверки -- в durable-журнале событий, который уже отдаёт
  `GET /api/runs/{run_id}`. Изменение формы UI не потребовалось.
- **Граница наблюдения сужена, а не расширена.** Offline-цикл передаёт
  `ProjectDiscovery` с уменьшенными `ScanLimits` (300 файлов, 4 МБ, глубина 6),
  потому что одна интерактивная задача должна укладываться в бюджет запроса;
  более узкая граница может только наблюдать меньше.

### Отклонённые альтернативы

- **Позволить mock-провайдеру "отвечать" текстом задачи.** Это и делала
  заготовленная строка: текст на входе, текст на выходе, никакой наблюдённой
  работы.
- **Выводить host-команду из описания.** Отклонено: это дало бы клиентскому
  тексту выбор команды, что решение о declared execution прямо запрещает.
- **Выдавать read-инструмент в run scope.** Отклонено: периметр инструментов --
  это композиция оператора, и клиентский путь не должен его расширять.
  Read-only наблюдение выполняется внутри порта верификатора, у которого нет
  authority на исполнение.
- **Фабриковать пройденную приёмку для задач, где нечего проверять.** Проверка,
  которую невозможно выполнить, даёт `FAILED`/`DENIED`, и запуск сообщает об
  этом; пустое или непригодное описание всё равно получает одну честную сводку
  рабочей области.

### Следствия и открытые пункты

1. **Только чтение по замыслу.** Offline-путь исследует рабочую область, но не
   изменяет её. Генерация кода offline по-прежнему требует реального провайдера и
   объявленной оператором цепочки исполнения/разрешений.
2. **Нет учёта попыток провайдера для offline-запусков.** Ничего не потрачено,
   поэтому попытка не записывается; таблицы провайдеров в дашборде законно не
   показывают строку для такого запуска. История задач и приёмки записывается.
3. **Текст задачи выбирает, что наблюдать.** Граница --
   `Workspace.normalize_relative_path` плюс распознаватель секретов; путь вне
   рабочей области отклоняется с `workspace_or_path_rejected` и проваливает
   запуск.
4. **Сам `MockProvider` не изменён.** Его заготовленная фраза по-прежнему
   возвращается при прямом `Orchestrator.dispatch` через mock-провайдера;
   перенаправлен только API-путь задачи.

## Guarded Code-Writing Path v0.1

### Context

The repository could plan, verify, report and (with an operator declaration)
execute a command, but **no path turned a model answer into a change on disk**.
The offline harness is read-only by construction
(`app/agent_runtime/offline_task.py`), and no executor applied model output to
files. Every "write code" request therefore had to leave the product: the owner
had to copy the answer into the tree by hand.

The capability is the product's point, so the missing piece had to exist. What
could not be relaxed while adding it: the frozen `RunScope`, the workspace
containment boundary, the deny rules around credentials/`.git`/migrations/
`authorizer.py`, the fail-closed defaults, idempotency, and the rule that a run
reports what really happened.

### Accepted decisions

- **One isolated, standard-library module.** `app/agent_runtime/code_writer.py`
  is the whole capability: deterministic, no network, no model call, no shell,
  no subprocess. It receives the model answer and the caller's own workspace and
  returns a structured result. It is not a second execution plane.
- **Two answer shapes only, and no guessing.** A fenced block carrying a path
  header (`` ```python path=tools/foo.py `` or a preceding `**File: ...**` line)
  and a unified diff (`--- a/path` / `+++ b/path`). An answer with neither is
  `no_changes`; a fenced block with no path is prose, not a change.
- **Path validation reuses the canonical boundary.** Every path is normalized by
  `normalize_workspace_relative_path` (which already refuses `..`, absolute,
  drive-qualified, UNC, ADS and reserved names) and then resolved through
  `WorkspaceBoundary.resolve_relative` - the same containment check
  `app/execution/sandbox.py` runs at the authority boundary and before a child
  process is spawned. A canonical target that differs from its lexical form
  (a link on the way) is refused.
- **A hard deny list, not a silent skip.** `.env`/`.env.*`, `.git`, `.venv`,
  `node_modules`, `dist`, `authorizer.py`, the PostgreSQL migrations, names
  carrying `secret`/`credential`/`token`, and anything the project's own secret
  recognizer (`app/execution/secret_files.py`) marks as credential material are
  refused. Refusal rejects the **whole answer**; no file is written.
- **Caps.** At most 20 files and 200 KB written per answer, enforced before the
  first byte is written.
- **Atomic per file, with backups.** Each file is written through
  `<path>.forge-new` and `os.replace`. A modified file's bytes are first copied
  to `.forge/backups/<run_id>/<relative path>`. Nothing is deleted by a change;
  a diff that would delete a file is refused.
- **Fail-closed verification.** Every touched `.py` is compiled with the standard
  library's `py_compile` (in-process - the same check as `python -m py_compile`,
  with no process and no artifact left in the workspace). If one fails, **every**
  file of that run is rolled back from its backup (created files are removed), and
  the result is `verification_failed`.
- **A real diff.** The reported diff is computed with `difflib` from the before
  and after bytes, per file, with `--- /dev/null` for a creation.
- **Opt-in, code-only request field.** `TaskRunRequest.write_mode: bool = False`,
  read from the `write_mode` payload key of `POST /api/tasks/run`. Write mode is
  active only when the flag is true **and** the category is `code`; every existing
  caller keeps the read-only behaviour, and the pinned response contract is
  unchanged (the report travels in `output`). A write-mode dispatch is a different
  logical operation, so the idempotency fingerprint appends a marker **only when
  the flag is set**, leaving every existing fingerprint byte-identical.
- **The live provider branch is reused, not reimplemented.** With a real provider
  selected, the model answer is the provider's own output from the existing
  `RunExecutor` dispatch (the write happens after it, inside the same frozen
  scope). With the offline provider, the "answer" is the deterministic plan text,
  so the pipeline (parse -> validate -> apply -> verify -> persist) runs end to
  end and honestly reports `no_changes`.
- **The model gets names, not contents.** In write mode the task text is sent with
  a bounded, deterministic listing of the workspace's file names
  (`build_workspace_context`), skipping secret-shaped paths. This keeps the prompt
  inside the request budget and keeps the prompt from becoming a read primitive.
- **The run record carries the outcome.** The result (status, files with
  added/removed counts, the real diff, the verification status, the reason) is
  merged into the run's existing `RunStateSnapshot.project_state` under
  `code_write`, and a bounded `PROJECT_STATE_UPDATED` event marks the same fact.
  `success` survives only when the status is `applied` and verification passed;
  `no_changes`, `rejected` and `verification_failed` are all reported as failures.
- **The durable lifecycle follows the final verdict.** The idempotency lifecycle
  write moved after write mode on the provider path, so a keyed write-mode run
  cannot be recorded `COMPLETED` while nothing was written.

### Rejected alternatives

- **Let the model name a file without a header and infer the body.** That is a
  guess about which file to overwrite; refused.
- **Fuzzy-apply a diff whose context does not match.** It would silently write a
  file the model never saw. Refused; the change is rejected instead.
- **Give the writer its own workspace or its own authority.** The workspace comes
  from the frozen scope the run already holds, so writing cannot widen a run.
- **Implement the write as a tool invocation.** The tool plane has its own
  approval and permission policy, and routing a file write through it would make
  a model answer an indirect tool request. The write is a separate, explicit,
  post-dispatch step controlled by the request flag.
- **Add fields to `TaskRunResponse`.** The response is byte-pinned by existing
  guard tests; the report rides in `output` and in the run record instead.
- **Report `applied` for a run whose verification failed.** Verification failure
  rolls back and reports `verification_failed`; success is never claimed.

### Consequences and open items

1. **A live provider is still required for real project code.** The provider
   branch is exercised with local fakes only; no live provider is enabled in this
   environment, so end-to-end writing of real code needs one enabled (and the
   model must answer with a supported shape).
2. **An answer cannot patch a file it never saw.** Only names are sent, so an
   update diff is applied strictly against the file on disk; a mismatch is
   refused. Sending small file contents is a separate, deliberate change.
3. **No deletions, ever.** A diff that deletes a file is refused.
4. **Write mode has no separate approval gate.** It is opt-in per request and
   bounded by the rails above; whether an interactive approval must precede a
   write remains an owner decision, and the approval machinery of the tool plane
   is deliberately not reused.

## Guarded Code-Writing Path v0.1 -- русская версия

### Контекст

Репозиторий умел планировать, проверять, отчитываться и (по объявлению оператора)
запускать команду, но **ни один путь не превращал ответ модели в изменение на
диске**. Offline-цикл по построению только читает (`app/agent_runtime/offline_task.py`),
и ни один исполнитель не применял вывод модели к файлам. Любой запрос «напиши код»
выводил за пределы продукта: владельцу приходилось вручную переносить ответ в
рабочее дерево.

Именно эта возможность и есть смысл продукта, поэтому недостающий шаг должен был
появиться. Что нельзя было ослабить при его добавлении: замороженный `RunScope`,
границу рабочей области, запреты вокруг секретов/`.git`/миграций/`authorizer.py`,
fail-closed значения по умолчанию, идемпотентность и правило, что запуск сообщает
только то, что произошло на самом деле.

### Принятые решения

- **Один изолированный модуль на стандартной библиотеке.**
  `app/agent_runtime/code_writer.py` — это вся возможность: детерминированно, без
  сети, без вызова модели, без shell и без subprocess. Он получает ответ модели и
  рабочую область вызывающего и возвращает структурированный результат. Это не
  вторая плоскость исполнения.
- **Только две формы ответа и никаких догадок.** Фенсед-блок с заголовком пути
  (`` ```python path=tools/foo.py `` или строка `**File: ...**` перед блоком) и
  unified diff (`--- a/path` / `+++ b/path`). Ответ без них — `no_changes`;
  фенсед-блок без пути — это текст, а не изменение.
- **Проверка путей переиспользует каноническую границу.** Каждый путь
  нормализуется `normalize_workspace_relative_path` (уже отклоняет `..`,
  абсолютные, с буквой диска, UNC, ADS и зарезервированные имена), а затем
  разрешается через `WorkspaceBoundary.resolve_relative` — ту же проверку
  вложенности, которую `app/execution/sandbox.py` выполняет на границе authority и
  перед запуском дочернего процесса. Каноническая цель, отличающаяся от лексической
  формы (ссылка по пути), отклоняется.
- **Жёсткий deny-list, а не молчаливый пропуск.** `.env`/`.env.*`, `.git`,
  `.venv`, `node_modules`, `dist`, `authorizer.py`, миграции PostgreSQL, имена с
  `secret`/`credential`/`token` и всё, что распознаватель секретов проекта
  (`app/execution/secret_files.py`) считает credential-материалом, отклоняется.
  Отказ отклоняет **весь ответ**; ни один файл не пишется.
- **Лимиты.** Не более 20 файлов и 200 КБ на ответ, проверяется до записи первого
  байта.
- **Атомарность на файл, с резервными копиями.** Каждый файл пишется через
  `<path>.forge-new` и `os.replace`. Байты изменяемого файла сначала копируются в
  `.forge/backups/<run_id>/<relative path>`. Изменение ничего не удаляет; diff,
  который удалил бы файл, отклоняется.
- **Fail-closed проверка.** Каждый затронутый `.py` компилируется стандартным
  `py_compile` (в процессе — та же проверка, что `python -m py_compile`, без
  процесса и без артефактов в рабочей области). При ошибке **все** файлы запуска
  откатываются из резервных копий (созданные удаляются), результат —
  `verification_failed`.
- **Настоящий diff.** Отчётный diff вычисляется `difflib` по байтам до и после, по
  каждому файлу, с `--- /dev/null` для создания.
- **Опциональное поле запроса, только для кода.**
  `TaskRunRequest.write_mode: bool = False`, читается из ключа `write_mode` тела
  `POST /api/tasks/run`. Write mode активен только когда флаг истинен **и**
  категория `code`; все существующие вызовы сохраняют read-only поведение, а
  закреплённый контракт ответа не меняется (отчёт идёт в `output`). Диспетчеризация
  write mode — другая логическая операция, поэтому fingerprint идемпотентности
  добавляет маркер **только при установленном флаге**, оставляя все существующие
  fingerprint байт-в-байт прежними.
- **Ветка живого провайдера переиспользуется, а не переписывается.** При выбранном
  реальном провайдере ответ модели — это его собственный вывод из существующей
  диспетчеризации `RunExecutor` (запись происходит после неё, внутри того же
  замороженного scope). С offline-провайдером «ответ» — это детерминированный текст
  плана, поэтому конвейер (parse -> validate -> apply -> verify -> persist)
  проходит целиком и честно сообщает `no_changes`.
- **Модель получает имена, а не содержимое.** В write mode текст задачи
  отправляется с ограниченным детерминированным списком имён файлов рабочей
  области (`build_workspace_context`), пропускающим похожие на секреты пути. Это
  удерживает промпт в бюджете запроса и не превращает его в примитив чтения.
- **Запись запуска содержит результат.** Результат (статус, файлы с числом
  добавленных/удалённых строк, настоящий diff, статус проверки, причина)
  добавляется в существующий `RunStateSnapshot.project_state` запуска под ключом
  `code_write`, и ограниченное событие `PROJECT_STATE_UPDATED` фиксирует тот же
  факт. `success` сохраняется только при статусе `applied` и пройденной проверке;
  `no_changes`, `rejected` и `verification_failed` сообщаются как неудачи.
- **Durable-жизненный цикл следует финальному вердикту.** Запись lifecycle
  идемпотентности перенесена после write mode на пути провайдера, поэтому
  ключевой запуск write mode не может быть записан как `COMPLETED` при отсутствии
  записи.

### Отклонённые альтернативы

- **Позволить модели назвать файл без заголовка и вывести тело.** Это догадка о
  том, какой файл перезаписывать; отклонено.
- **Применять diff с несовпадающим контекстом «примерно».** Это молча записало бы
  файл, которого модель не видела. Отклонено; изменение отклоняется.
- **Дать писателю собственную рабочую область или собственную authority.** Рабочая
  область берётся из замороженного scope запуска, поэтому запись не может его
  расширить.
- **Реализовать запись как вызов инструмента.** У плоскости инструментов своя
  политика разрешений и одобрений, и маршрутизация записи через неё сделала бы
  ответ модели косвенным запросом инструмента. Запись — отдельный явный шаг после
  диспетчеризации, управляемый флагом запроса.
- **Добавить поля в `TaskRunResponse`.** Ответ закреплён существующими guard-тестами;
  отчёт идёт в `output` и в запись запуска.
- **Сообщать `applied` для запуска с провалившейся проверкой.** Провал проверки
  откатывает изменения и сообщает `verification_failed`; успех не заявляется.

### Следствия и открытые пункты

1. **Для реального кода проекта всё ещё нужен живой провайдер.** Ветка провайдера
   проверена только локальными фейками; в этой среде ни один живой провайдер не
   включён, поэтому сквозная запись реального кода требует его включения (и модель
   должна ответить поддерживаемой формой).
2. **Ответ не может патчить файл, которого не видел.** Передаются только имена,
   поэтому diff-обновление применяется строго к файлу на диске; несовпадение
   отклоняется. Передача содержимого небольших файлов — отдельное осознанное
   изменение.
3. **Никаких удалений.** Diff, удаляющий файл, отклоняется.
4. **У write mode нет отдельного гейта одобрения.** Он опционален для запроса и
   ограничен перечисленными рамками; нужен ли интерактивный approval перед записью —
   решение владельца, и механизм одобрений плоскости инструментов сознательно не
   переиспользуется.

## Provider failover and context guards

- **A hard account failure disables the account, not just rotates past it.** Radox
  402 (insufficient funds/quota) and 401/403 (rejected credential) set
  `disabled_until` (one hour) in addition to advancing the rotation cursor, exactly
  like a 429. Before this, the ledger recorded `last_error_code: 402` with no
  cooldown, so the same dead account was reserved again on the next request. A
  disabled position stays a last resort: when every account is cooling down the pool
  is still attempted, and the failure message says so.
- **The fallback chain advances on the provider's problem, not on the request's.**
  `ProviderError.failover_eligible` (a class attribute, never message text) marks
  credential/quota/rate-limit/transport failures as eligible for the next provider;
  a request error (400/404/422) ends the chain, because every provider would reject
  the same request. `NOT_CONFIGURED` remains eligible.
- **A resolved configured default is a primary, not a pin.** The API passes the
  deployment's configured default with `allow_fallback=True`; a provider named by the
  caller stays pinned. Passed as an explicit name, the default used to pin the run,
  so a 402 on it ended the run with the configured chain never consulted.
- **The operator's own task text is budgeted separately.** `TASK_SPECIFICATION_BUDGET_TOKENS`
  (128k) bounds Tier 1 text independently of the model window, and the assembled
  context's character bound is derived from it. A narrow window sized for retrieved
  evidence must not reject the instruction itself; an absurd description is still
  refused with an explicit limit message.
- **The security filter refuses secret material, not the word "token".** A value
  naming `tokens_in`/`tokens_out`/`max_tokens` is engineering text; a `Bearer`
  credential, JWT, `sk-`/`ghp_` key, assigned credential, or credential-shaped
  random value is still refused with the existing reasons. A lowercase hex digest
  is an identifier (workspace fingerprints, snapshot ids) and is not treated as
  secret material.

### Provider failover и защиты контекста — русская версия

- **Жёсткий отказ аккаунта выключает аккаунт, а не только пропускает его.** Radox
  402 (нет средств/квоты) и 401/403 (отклонённые credentials) выставляют
  `disabled_until` (один час) вместе с продвижением курсора ротации, как и 429. До
  этого ledger записывал `last_error_code: 402` без cooldown, и тот же мёртвый
  аккаунт выбирался снова на следующем запросе. Выключенная позиция остаётся
  последним шансом: когда остывают все аккаунты, пул всё равно пробуется, и
  сообщение об ошибке это сообщает.
- **Цепочка fallback продвигается из-за проблемы провайдера, а не запроса.**
  `ProviderError.failover_eligible` (атрибут класса, не текст сообщения) помечает
  отказы credentials/квоты/rate-limit/транспорта как допустимые для следующего
  провайдера; ошибка запроса (400/404/422) цепочку завершает, потому что тот же
  запрос отвергнет любой провайдер. `NOT_CONFIGURED` остаётся допустимым.
- **Разрешённый настроенный default — это primary, а не pin.** API передаёт
  настроенный default с `allow_fallback=True`; провайдер, названный вызывающим,
  остаётся закреплённым. Переданный как явное имя, default закреплял запуск, поэтому
  402 на нём завершал запуск без обращения к настроенной цепочке.
- **Текст задачи оператора бюджетируется отдельно.** `TASK_SPECIFICATION_BUDGET_TOKENS`
  (128k) ограничивает Tier 1 текст независимо от окна модели, и граница символов
  собранного контекста выводится из неё. Узкое окно, рассчитанное на извлечённые
  доказательства, не должно отвергать саму инструкцию; абсурдное описание
  по-прежнему отклоняется с явным сообщением о лимите.
- **Фильтр безопасности отклоняет секретный материал, а не слово «token».** Значение,
  называющее `tokens_in`/`tokens_out`/`max_tokens`, — это инженерный текст;
  `Bearer`-credential, JWT, ключ `sk-`/`ghp_`, присвоенный credential или похожее на
  credential случайное значение по-прежнему отклоняются с существующими причинами.
  Hex-дайджест в нижнем регистре — это идентификатор (fingerprint рабочего
  пространства, id снимка), а не секретный материал.

## Discovery / Clarification v0.1

- **Clarification sits in front of the pipeline, and owns no execution.** The layer
  (`app/discovery`) decides whether the idea says enough to plan, asks at most five
  short questions (three for a short idea), derives a `ProjectSpecification`, and
  refuses planning/execution until the user confirms. After confirmation the existing
  Planner / Dispatcher / Run path runs unchanged; there is no second executor and no
  second run system.
- **The obvious fields are derived by rules, not by a model.** Project type,
  platform, users, core features, integrations, storage, auth and notifications are
  matched with fixed keyword rules (`app/discovery/analyzer.py`), so the same goal
  always yields the same derivation, tests need no network, and a run is never
  blocked on an LLM being available. An optional LLM phrasing hook is deliberately
  not implemented.
- **Unstated means unknown, never guessed.** The area set is fixed (twelve areas);
  an area neither the goal nor an answer covers stays empty and is asked about.
- **The question list is capped and ordered.** At most five questions (`MAX_QUESTIONS`)
  and at most three for a short idea (`SHORT_IDEA_MAX_QUESTIONS`), essential areas
  first, so the cap can never drop a required question; an answered or explicitly
  skipped area is never asked again. A short idea is a sentence or two (at most 240
  characters, four lines), and it must not turn into a questionnaire.
- **Every question is clickable, and knows why it is asked.** `ClarificationQuestion`
  carries `control` (`radio|checkbox|select|yes_no|text`), labelled `options` with one
  `recommended` default, `allow_custom` («Свой вариант») and `allow_unknown` («Не знаю —
  выбери оптимальный вариант»). «Не знаю» records the recommended option with
  `auto_selected=True`; «Пропустить» is allowed only for an `optional` question and a
  required one is refused with `RequiredQuestionSkipped`, so a required decision cannot
  vanish silently. The specification keeps `chosen_values` and
  `auto_selected_values` apart, and both are published as two lists.
- **Confirmation is the only way in.** `require_confirmed()` raises
  `DiscoveryNotConfirmed` unless the state is `CONFIRMED`. `skip()` moves the
  session to `READY_FOR_PLANNING`, not to `CONFIRMED`, so a skip is never a bypass:
  the user still confirms explicitly before anything plans or runs.
- **Sessions live in their own small store.** One JSON file per session under
  `.forge/discovery` (`FORGE_DISCOVERY_ROOT`), atomically replaced exactly like the
  Run Store's snapshots, but separate from it: a session precedes a run and is not
  execution evidence.
- **A secret-shaped answer is never stored.** Answers are screened with the existing
  `find_forbidden_value_content`; a flagged answer is replaced by a
  `[REDACTED:<reason>]` marker with `redacted=True`, so the value cannot reach the
  specification, the file, the API, or a run. Answers are user text only.
- **The hand-off is one task description.** `POST /api/tasks/run` reads
  `discovery_session_id` at the HTTP boundary (not on `TaskRunRequest`, whose field
  set is pinned), refuses 404/409 before dispatch unless the session is confirmed,
  and otherwise prepends `as_task_text()` to the caller's description. Explicit
  provider/model selection, automatic routing, classification, the fallback chain,
  cost-aware routing, the frozen `RunScope` and every security boundary are
  untouched.

### Discovery / Clarification v0.1 — русская версия

- **Уточнение стоит перед конвейером и не владеет выполнением.** Слой
  (`app/discovery`) решает, достаточно ли идея говорит для планирования, задаёт не
  более пяти коротких вопросов (три для короткой идеи), выводит
  `ProjectSpecification` и не позволяет планировать/выполнять до подтверждения. После
  подтверждения существующий путь Planner / Dispatcher / Run работает без изменений;
  второго исполнителя и второй системы запусков нет.
- **Очевидные поля выводятся правилами, а не моделью.** Тип проекта, платформа,
  пользователи, ключевые функции, интеграции, хранение, авторизация и уведомления
  сопоставляются фиксированными ключевыми правилами (`app/discovery/analyzer.py`),
  поэтому одна цель всегда даёт один и тот же вывод, тесты не требуют сети, а запуск
  никогда не блокируется доступностью LLM. Необязательный LLM-хук формулировок
  намеренно не реализован.
- **Не сказано — значит неизвестно, а не угадано.** Набор областей фиксирован
  (двенадцать); область, не покрытая ни целью, ни ответом, остаётся пустой и о ней
  спрашивают.
- **Список вопросов ограничен и упорядочен.** Не более пяти вопросов (`MAX_QUESTIONS`)
  и не более трёх для короткой идеи (`SHORT_IDEA_MAX_QUESTIONS`), существенные области
  идут первыми, поэтому ограничение не может отбросить обязательный вопрос; область с
  ответом или с явным пропуском больше не спрашивается. Короткая идея — это одно-два
  предложения (до 240 символов, до четырёх строк), и она не должна превращаться в
  анкету.
- **Каждый вопрос нажимаемый и знает, зачем он задан.** `ClarificationQuestion` несёт
  `control` (`radio|checkbox|select|yes_no|text`), подписанные `options` с одним
  `recommended` по умолчанию, `allow_custom` («Свой вариант») и `allow_unknown»
  («Не знаю — выбери оптимальный вариант»). «Не знаю» записывает рекомендованный
  вариант с `auto_selected=True`; «Пропустить» разрешён только для необязательного
  вопроса, а на обязательном возникает `RequiredQuestionSkipped`, поэтому обязательное
  решение не может исчезнуть молча. Спецификация хранит `chosen_values` и
  `auto_selected_values` раздельно, и оба списка публикуются.
- **Единственный вход — подтверждение.** `require_confirmed()` выбрасывает
  `DiscoveryNotConfirmed`, если состояние не `CONFIRMED`. `skip()` переводит сессию в
  `READY_FOR_PLANNING`, а не в `CONFIRMED`, поэтому пропуск не является обходом:
  пользователь всё равно подтверждает явно, прежде чем что-то планируется или
  выполняется.
- **Сессии живут в собственном небольшом хранилище.** Один JSON-файл на сессию под
  `.forge/discovery` (`FORGE_DISCOVERY_ROOT`), атомарно заменяемый так же, как снимки
  Run Store, но отдельно от него: сессия предшествует запуску и не является
  доказательством выполнения.
- **Похожий на секрет ответ не сохраняется.** Ответы проверяются существующим
  `find_forbidden_value_content`; помеченный ответ заменяется маркером
  `[REDACTED:<reason>]` с `redacted=True`, поэтому значение не может попасть ни в
  спецификацию, ни в файл, ни в API, ни в запуск. Ответы — только текст пользователя.
- **Передача — одно описание задачи.** `POST /api/tasks/run` читает
  `discovery_session_id` на границе HTTP (не в `TaskRunRequest`, чей набор полей
  закреплён), отклоняет 404/409 до диспетчеризации, если сессия не подтверждена, и
  иначе добавляет `as_task_text()` перед описанием вызывающего. Явный выбор
  провайдера/модели, автоматическая маршрутизация, классификация, цепочка fallback,
  cost-aware маршрутизация, замороженный `RunScope` и все границы безопасности не
  изменяются.

## Project lifecycle v0.1

### Context

The discovery layer freezes a specification, and the run path produces evidence about one execution.
Nothing kept the thing in between: after a confirmation there was no durable record with a status, no
place where a later run's files and verdicts belonged, and no way to ask "is this project finished?"
without reading a run. The owner needed one answer per project, and needed it to be auditable.

### Accepted decisions

- **A project is a record, not a run.** `app/projects` stores one JSON per project plus
  `index.json` under `.forge/projects`; the run store stays the execution evidence and the project
  store references it by `run_id`. The two are never merged: a project precedes a run and outlives
  it.
- **One JSON per project, and the session link lives in the index.** The record keeps exactly the
  documented field set; the link from a confirmed discovery session to its project is kept in
  `index.json` (`sessions`), because it is a lookup, not a property of the project. Losing the index
  costs the link, never the project: the directory is the truth.
- **Statuses are a table, not branches.** `ALLOWED_TRANSITIONS` in `app/projects/lifecycle.py` is
  data; an unlisted move raises `IllegalTransition` and is never coerced to a "nearest legal"
  state, so every status is the result of a named event from a named status.
- **`previous_status` makes restore honest.** Archiving records the status the project had, and
  `restore` returns exactly that value; when it is missing the restore is refused rather than
  invented. While archived, every other event is refused.
- **The completion gate: a write-less run can never be `COMPLETED`.** Completion requires a
  terminal-success run, `verification == passed`, `acceptance == passed` and — when the run wrote
  files — at least one recorded artifact. "Finished" is never treated as "successful", and there is
  no optimistic completion path anywhere.
- **Re-verification re-checks bytes, not prose.** `verify_again` confirms every recorded artifact at
  its recorded size and SHA-256 and compiles every recorded Python artifact; the acceptance verdict
  is the recorded one. The re-judgement event therefore skips only the run-state row of the gate, and
  verification, acceptance and the artifact rule still apply.
- **Artifacts travel as descriptions, never as content.** A recorded file is a path, a kind, a size,
  a SHA-256, a `create`/`update` change and a verification relevance. The bytes stay in the project
  workspace, so a record can be listed, compared and archived without carrying code or a credential
  that ended up in it.
- **No absolute path reaches a payload.** `project_path` is repository-relative
  (`.forge/projects/<id>/workspace`) and an artifact publishes a repository-relative `location`
  composed by the backend, so a client never joins two fields into a host path.
- **Additive wiring only.** The existing `GET /api/projects` (workspace list) is untouched; the
  lifecycle list has its own path and is registered before `/{project_id}`. Confirmation gains an
  additive `project` field, and `POST /api/tasks/run` attaches its run through the existing
  confirmed-session guard, reporting the outcome in `X-Forge-Project-Attach` without ever changing
  the run's own response.
- **The UI reports, it does not judge.** The screen selects the outcome block by the project's own
  status, prints verdicts as the backend words them, keeps a refused action clickable so the real
  409 is visible, and labels a `mock` run as "an answer without file changes" with the write switch
  disabled. Live progress is derived only from the run record, with no percentage anywhere.

### Rejected alternatives

- **Store the project inside the run record.** A project outlives many runs and is not evidence of
  one execution; merging them would make the run store's schema carry planning state.
- **Keep a status field on the run and derive the project from it.** That loses the transition
  history and makes "who moved this project" unanswerable; the explicit table is auditable instead.
- **Let a finished run imply `COMPLETED`.** A run that wrote nothing, or whose verification did not
  pass, would be reported as a finished project. The gate exists precisely to refuse that.
- **Publish the discovery session id on the project record.** It was rejected to keep the record's
  field set exactly as documented; the cost is that a version submitted from the project screen
  carries no `discovery_session_id`, which the UI states rather than hides. (Superseded in part by
  the decision below: the screen carries the project's own id instead, so no record field is added.)
- **Show a percentage or an ETA while a run is in flight.** The backend publishes neither a per-step
  document nor a rate, so any percentage would be invented.

### The change-run association (2026-10)

A change requested from «Проекты» ran, wrote its files, and the card promised a version that never
appeared: the screen had no association to send, so `attach_run` was never called. The smallest
deterministic addition is a second **run-route** field, `lifecycle_project_id`, read at the HTTP
boundary exactly like `discovery_session_id` and likewise not a field of `TaskRunRequest`.

- It names the project by its own id, so nothing has to be published on the record and no reverse
  lookup of the session index is needed.
- The composition validates it *before* dispatch with `require_active` (404 unknown, 409 deleted,
  409 archived) and refuses a session/project pair that disagrees (409 `project_mismatch`).
- After the run, the existing `attach_run` path records the version **that run id** produced and
  advances only on its own gate; a change run that wrote no file leaves the previous version current.
- The response's `files_created`/verdicts now come from that attempt, not from whatever version
  happens to be current, so a zero-file change cannot borrow the previous version's evidence.

### Consequences and open items

The session link is still the creation path, and the project id is the change path; both end in the
same `attach_run`. `docs/USER_MANUAL.md` describes the flow for the owner; `docs/ARCHITECTURE.md`
describes the implementation.

## Project lifecycle v0.1 — русская версия

### Контекст

Слой уточнения фиксирует спецификацию, а путь запуска даёт доказательства об одном исполнении.
Ничего не хранило то, что между ними: после подтверждения не было долговечной записи со статусом, не
было места, куда попадут файлы и вердикты более позднего запуска, и нельзя было спросить «этот проект
завершён?», не читая запуск. Владельцу нужен один ответ на проект, и он должен быть проверяемым.

### Принятые решения

- **Проект — это запись, а не запуск.** `app/projects` хранит один JSON на проект плюс
  `index.json` под `.forge/projects`; хранилище запусков остаётся доказательством исполнения, а
  хранилище проектов ссылается на него через `run_id`. Они никогда не сливаются: проект
  предшествует запуску и переживает его.
- **Один JSON на проект, а связь сессии живёт в индексе.** Запись сохраняет ровно
  задокументированный набор полей; связь подтверждённой сессии уточнения с её проектом хранится в
  `index.json` (`sessions`), потому что это поиск, а не свойство проекта. Потеря индекса стоит связи,
  но не проекта: каталог — это истина.
- **Статусы — таблица, а не ветки.** `ALLOWED_TRANSITIONS` в `app/projects/lifecycle.py` — это
  данные; переход, которого нет в таблице, выбрасывает `IllegalTransition` и никогда не приводится к
  «ближайшему законному» состоянию, поэтому каждый статус — результат именованного события из
  именованного статуса.
- **`previous_status` делает восстановление честным.** Архивация записывает статус, который был, а
  `restore` возвращает ровно его; если значение отсутствует, восстановление отклоняется, а не
  придумывается. Пока проект в архиве, любое другое событие отклоняется.
- **Гейт завершения: запуск без записи файлов никогда не станет `COMPLETED`.** Завершение требует
  терминально успешного запуска, `verification == passed`, `acceptance == passed` и — если запуск
  писал файлы — хотя бы одного записанного артефакта. «Завершился» никогда не считается «успешен», и
  оптимистического пути к завершению нет нигде.
- **Повторная проверка сверяет байты, а не прозу.** `verify_again` подтверждает каждый записанный
  артефакт по записанным размеру и SHA-256 и компилирует каждый записанный Python-артефакт; вердикт
  приёмки берётся записанный. Поэтому событие повторного суждения пропускает только строку состояния
  запуска в гейте, а проверка, приёмка и правило артефактов продолжают действовать.
- **Артефакты передаются описаниями, никогда содержимым.** Записанный файл — это путь, вид, размер,
  SHA-256, изменение `create`/`update` и значимость для проверки. Байты остаются в рабочей области
  проекта, поэтому запись можно перечислять, сравнивать и архивировать, не перенося код или секрет,
  оказавшийся в нём.
- **Абсолютный путь не попадает в ответ.** `project_path` относителен репозиторию
  (`.forge/projects/<id>/workspace`), а артефакт публикует относительный к репозиторию `location`,
  составленный backend'ом, поэтому клиент никогда не склеивает два поля в путь хоста.
- **Только аддитивная проводка.** Существующий `GET /api/projects` (список рабочих пространств) не
  изменён; у списка жизненного цикла свой путь, и он регистрируется до `/{project_id}`.
  Подтверждение получает аддитивное поле `project`, а `POST /api/tasks/run` привязывает свой запуск
  через существующую защиту подтверждённой сессии, сообщая итог в `X-Forge-Project-Attach` и никогда
  не меняя собственный ответ запуска.
- **Интерфейс сообщает, а не судит.** Экран выбирает блок итога по статусу самого проекта, печатает
  вердикты словами backend, оставляет отклонённое действие кликабельным, чтобы настоящий 409 был
  виден, и помечает запуск `mock` как «ответ без изменения файлов», блокируя переключатель записи.
  Ход выполнения выводится только из записи запуска, без процентов.

### Отклонённые альтернативы

- **Хранить проект внутри записи запуска.** Проект переживает много запусков и не является
  доказательством одного исполнения; слияние заставило бы схему Run Store нести состояние
  планирования.
- **Держать поле статуса на запуске и выводить проект из него.** Это теряет историю переходов и
  делает вопрос «кто сдвинул проект» безответным; явная таблица вместо этого проверяема.
- **Считать завершённый запуск признаком `COMPLETED`.** Запуск, который ничего не записал или не
  прошёл проверку, был бы показан как завершённый проект. Гейт существует именно чтобы это отклонить.
- **Публиковать идентификатор сессии уточнения в записи проекта.** Отклонено, чтобы набор полей
  записи оставался ровно задокументированным; цена — версия, отправленная с экрана проекта, не несёт
  `discovery_session_id`, и интерфейс это сообщает, а не скрывает. (Частично снято решением ниже:
  экран несёт собственный id проекта, поэтому поле записи не добавляется.)
- **Показывать процент или оценку времени, пока запуск идёт.** Backend не публикует ни пошаговый
  документ, ни темп, поэтому любой процент был бы выдумкой.

### Привязка запуска изменения (2026-10)

Изменение, запрошенное из «Проектов», выполнялось, записывало файлы, а карточка обещала версию,
которая не появлялась: экрану нечего было отправить для привязки, поэтому `attach_run` не
вызывался. Минимальное детерминированное добавление — второе поле **маршрута запуска**,
`lifecycle_project_id`, читаемое на границе HTTP так же, как `discovery_session_id`, и так же не
являющееся полем `TaskRunRequest`.

- Оно называет проект его собственным id, поэтому в записи ничего публиковать не нужно и обратный
  поиск по индексу сессий не требуется.
- Композиция проверяет его **до** диспетчеризации через `require_active` (404 неизвестен, 409
  удалён, 409 архив) и отклоняет пару «сессия/проект», которая не совпадает (409 `project_mismatch`).
- После запуска существующий путь `attach_run` записывает версию, которую произвёл **этот run id**,
  и двигает жизненный цикл только по своему условию; запуск изменения, не записавший файлов,
  оставляет текущей предыдущую версию.
- `files_created`/вердикты ответа теперь относятся к этой попытке, а не к той версии, что случайно
  оказалась текущей, поэтому изменение без файлов не может «одолжить» доказательства предыдущей
  версии.

### Следствия и открытые пункты

Связь сессии остаётся путём создания, а id проекта — путём изменения; оба заканчиваются одним и тем
же `attach_run`. `docs/USER_MANUAL.md` описывает поток для владельца; `docs/ARCHITECTURE.md`
описывает реализацию.

## Run Outcome Gate and Paid-Model Policy v0.1

### Context

Two defects were measured together on the owner's machine, and both reported
success that had not happened:

1. **False completion.** The owner typed a short idea ("a small local web app for
   household expenses"). The model answered with clarifying questions. The UI
   showed `COMPLETED` / «Запись сохранена: да» -- although no project existed, no
   file was written, the specification was never confirmed and the questions were
   never answered. `TaskResult.success` (the provider returned text) was being
   reported as a finished project.
2. **An unbilled-but-billed run.** A run went to
   `openrouter / anthropic/claude-haiku-4.5` without the caller naming that
   provider or model. The row was registered `CHEAP` even though its published rate
   is 1.00 / 5.00 USD per 1M tokens, so "cheap" was read as "free to run" and the
   automatic route selected it while `allow_paid_providers` was false.

### Decision

1. **One provider-neutral outcome, in `app/agent_runtime/outcome.py`.** A pure,
   deterministic `RunOutcome` (`CLARIFICATION_REQUIRED`, `SPECIFICATION_READY`,
   `PLAN_READY`, `EXECUTION_RUNNING`, `VERIFICATION_REQUIRED`, `PROJECT_COMPLETED`,
   `NEEDS_CHANGES`, `FAILED`) is derived from facts, in a fixed precedence:
   error/settled-failure -> clarifying questions (a list of question lines, or a
   discovery session still `NEEDS_CLARIFICATION`) -> no project record or an
   unconfirmed specification (`PLAN_READY` when a plan waits for confirmation,
   otherwise `SPECIFICATION_READY`) -> in flight (`EXECUTION_RUNNING`) ->
   verification not run -> verification or acceptance failed -> only then, and only
   with every fact (project, workspace, artifacts or an explicit no-file result,
   `verification == passed`, `acceptance == passed`, non-empty manifest), is the
   result `PROJECT_COMPLETED`.
2. **The response follows the outcome.** `TaskRunResponse.outcome` carries it and
   the user-facing `state` is derived from it. The gate may only *downgrade* a
   success claim: `COMPLETED` is reachable from `PROJECT_COMPLETED` alone, so a
   text-only answer can never surface as a completed project. The outcome and the
   questions are also written into the run record's `project_state`, and
   `GET /api/runs/{run_id}` republishes them read-only.
3. **One completion rule, not two.** `app.projects.lifecycle.completion_blockers`
   no longer restates the rule: it translates its event into the classifier's facts
   and names the missing ones, so the lifecycle gate and the response gate cannot
   disagree. Its public blocker vocabulary (`run_not_terminal_success`,
   `verification_*`, `acceptance_*`, `artifacts_missing`) is preserved.
4. **Independent save indicators.** `run_record_saved`, `project_created`,
   `project_files_created` and `artifact_manifest_saved` are four separate
   booleans, plus `files_created`. A single "saved" indicator is what hid an empty
   project behind a stored run record.
5. **A priced model is `PAID`.** `openrouter/anthropic/claude-haiku-4.5` is
   registered `PAID`, so the automatic route (which asks for
   `max_cost_tier=CHEAP` while `allow_paid_providers` is false) cannot select it.
   Even with paid allowed, the free/cheap rows sort first; a paid row is reached
   only by naming it.
6. **A paid pair requires an explicit choice.** Before any dispatch, the service
   resolves the pair the run would actually use and its `cost_tier`. A `PAID` pair
   runs only when the caller named **both** the provider and the model *and* the
   operator enabled paid providers. Otherwise the run is refused with
   `paid_model_requires_confirmation` (409 over HTTP) and no provider is called.
   The refusal names the provider and the model -- never a key, a variable name, a
   prefix or a length.
7. **`cost_tier` and `estimated_cost` are published** on the run response and on
   every models-catalogue row, so a UI can show what a run costs. `cost_tier` is
   exactly `FREE`/`CHEAP`/`PAID`; `estimated_cost` is nullable and is never
   invented from a zero -- on the catalogue row it is the model's own per-1M-input
   unit price, on a run it is the run's own estimated cost.

### Rejected alternatives

- **Add a boolean `project_completed` next to `success`.** It would preserve the
  ambiguous `state` word and let two consumers disagree; the outcome is a value,
  not a flag, and the state is derived from it.
- **Derive `PROJECT_COMPLETED` from `success` plus `files_created > 0`.** A file
  can exist in a run workspace that never became a project, and a project can be
  completed by a run that wrote nothing (a read-only task). Both facts are
  necessary and neither is sufficient.
- **Keep the lifecycle's rule and re-implement it in the API.** That is exactly how
  the two layers drifted; the brief required one source of truth.
- **Keep `anthropic/claude-haiku-4.5` as `CHEAP` and refuse only at dispatch.** The
  tier is what automatic routing reads, so leaving it `CHEAP` would keep the
  mis-selection reachable by every other caller of the registry.
- **Allow an explicitly named paid model while `allow_paid_providers` is false.**
  The flag is the operator's own bound; letting a caller override it with a name
  would make the deployment setting advisory.

### Consequences

`tests/test_false_completion_guard.py` pins the classifier precedence, the
questions answer, the empty workspace, the text answer and the full positive path
(a real `code_writer` write, verified and accepted). `tests/test_paid_model_policy.py`
pins the refusal, the 409 code, the automatic preference for free rows, the
explicit paid path and the payloads. Three existing assertions that required
`state == "COMPLETED"` for text-only runs were corrected, because they encoded the
defect. The remaining work is the interface: questions-first layout, structured
question widgets, the provider panel on the launch step, and hiding the agents
panel.

## Гейт результата запуска и политика платных моделей v0.1 — русская версия

### Контекст

На машине владельца были измерены два дефекта, и оба сообщали о успехе, которого не
было:

1. **Ложное завершение.** Владелец ввёл короткую идею («небольшое локальное
   веб-приложение для учёта домашних расходов»). Модель ответила уточняющими
   вопросами. Интерфейс показал `COMPLETED` / «Запись сохранена: да» — хотя проекта
   не было, ни один файл не записан, спецификация не подтверждена, а вопросы не
   отвечены. `TaskResult.success` (провайдер вернул текст) выдавался за завершённый
   проект.
2. **Запуск, который стоил денег без выбора.** Запуск ушёл в
   `openrouter / anthropic/claude-haiku-4.5` без того, чтобы вызывающий назвал этого
   провайдера или модель. Строка была зарегистрирована как `CHEAP`, хотя её
   опубликованная цена — 1.00 / 5.00 USD за 1M токенов, поэтому «дешёвая» читалась
   как «бесплатная для запуска», и автоматическая маршрутизация выбрала её при
   `allow_paid_providers` = false.

### Решение

1. **Один провайдеро-нейтральный результат в `app/agent_runtime/outcome.py`.**
   Чистый детерминированный `RunOutcome` (`CLARIFICATION_REQUIRED`,
   `SPECIFICATION_READY`, `PLAN_READY`, `EXECUTION_RUNNING`,
   `VERIFICATION_REQUIRED`, `PROJECT_COMPLETED`, `NEEDS_CHANGES`, `FAILED`)
   выводится из фактов в фиксированном порядке: ошибка/завершённая неудача →
   уточняющие вопросы (список строк-вопросов или сессия уточнения всё ещё
   `NEEDS_CLARIFICATION`) → нет записи проекта или спецификация не подтверждена
   (`PLAN_READY`, если план ждёт подтверждения, иначе `SPECIFICATION_READY`) → запуск
   идёт (`EXECUTION_RUNNING`) → проверка не выполнялась → проверка или приёмка не
   прошли → и только затем, и только при всех фактах (проект, рабочая область,
   артефакты или явный результат без файлов, `verification == passed`,
   `acceptance == passed`, непустой манифест) результат — `PROJECT_COMPLETED`.
2. **Ответ следует за результатом.** `TaskRunResponse.outcome` несёт его, а
   пользовательский `state` выводится из него. Гейт может только *понижать* заявку
   на успех: `COMPLETED` достижим только из `PROJECT_COMPLETED`, поэтому текстовый
   ответ никогда не покажется завершённым проектом. Результат и вопросы также
   записываются в `project_state` записи запуска, а `GET /api/runs/{run_id}`
   переопубликовывает их только для чтения.
3. **Одно правило завершения, а не два.** `app.projects.lifecycle.completion_blockers`
   больше не пересказывает правило: он переводит своё событие в факты
   классификатора и называет недостающие, поэтому гейт жизненного цикла и гейт ответа
   не могут расходиться. Публичный словарь блокеров (`run_not_terminal_success`,
   `verification_*`, `acceptance_*`, `artifacts_missing`) сохранён.
4. **Независимые индикаторы сохранения.** `run_record_saved`, `project_created`,
   `project_files_created` и `artifact_manifest_saved` — четыре отдельных булевых
   значения, плюс `files_created`. Именно единый индикатор «сохранено» скрывал
   пустой проект за сохранённой записью запуска.
5. **Модель с ценой — это `PAID`.** `openrouter/anthropic/claude-haiku-4.5`
   зарегистрирована как `PAID`, поэтому автоматический маршрут (он запрашивает
   `max_cost_tier=CHEAP`, пока `allow_paid_providers` = false) не может её выбрать.
   Даже при разрешённых платных строках бесплатные и дешёвые сортируются первыми;
   платная строка достигается только именованием.
6. **Платная пара требует явного выбора.** До любого запуска сервис определяет пару,
   которая реально будет использована, и её `cost_tier`. Платная пара выполняется
   только если вызывающий назвал **и** провайдера, **и** модель, **и** оператор
   включил платных провайдеров. Иначе запуск отклоняется с
   `paid_model_requires_confirmation` (409 по HTTP), и ни один провайдер не
   вызывается. Отказ называет провайдера и модель — никогда ключ, имя переменной,
   префикс или длину.
7. **`cost_tier` и `estimated_cost` публикуются** в ответе запуска и в каждой строке
   каталога моделей, чтобы интерфейс мог показать стоимость запуска. `cost_tier` —
   ровно `FREE`/`CHEAP`/`PAID`; `estimated_cost` допускает `null` и никогда не
   выдумывается из нуля — в строке каталога это собственная цена модели за 1M
   входных токенов, в запуске — собственная оценка запуска.

### Отклонённые альтернативы

- **Добавить булев `project_completed` рядом с `success`.** Это сохранило бы
  двусмысленное слово `state` и позволило бы двум потребителям расходиться;
  результат — это значение, а не флаг, и состояние выводится из него.
- **Выводить `PROJECT_COMPLETED` из `success` плюс `files_created > 0`.** Файл может
  существовать в рабочей области запуска, который так и не стал проектом, а проект
  может быть завершён запуском, который ничего не записал (задача только на чтение).
  Оба факта необходимы, и ни один не достаточен.
- **Оставить правило жизненного цикла и повторить его в API.** Именно так два слоя и
  разошлись; задача требовала единственного источника истины.
- **Оставить `anthropic/claude-haiku-4.5` как `CHEAP` и отклонять только при
  запуске.** Уровень — это то, что читает автоматическая маршрутизация, поэтому
  `CHEAP` сохранил бы ошибочный выбор достижимым для любого другого потребителя
  реестра.
- **Разрешать явно названную платную модель при `allow_paid_providers` = false.**
  Флаг — это собственное ограничение оператора; позволить вызывающему обойти его
  именем значило бы сделать настройку развёртывания совещательной.

### Следствия

`tests/test_false_completion_guard.py` фиксирует порядок правил классификатора, ответ
с вопросами, пустую рабочую область, текстовый ответ и полный положительный путь
(реальная запись `code_writer`, проверенная и принятая).
`tests/test_paid_model_policy.py` фиксирует отказ, код 409, автоматическое
предпочтение бесплатных строк, явный платный путь и полезные нагрузки. Три
существующих утверждения, требовавших `state == "COMPLETED"` для текстовых
запусков, были исправлены, потому что они кодировали дефект. Оставшаяся работа —
интерфейс: схема «сначала вопросы», структурированные виджеты вопросов, панель
провайдера на шаге запуска и скрытие панели агентов.

## Project Truth Presentation v0.1

**Context.** A project card could show `Files: 0`, `Verification: NOT RUN` and
`Status: NEEDS_CHANGES` while the run that filled it said `COMPLETED`, and the
reader took it for a finished project. Every single fact was true; there was no
one place that put them in the owner's reading order, and no rule that could not
say "done" about an empty project.

**Decision.**

1. **One presentation function, in Python.**
   `app/projects/presentation.py::build_project_presentation` is the only place
   where a backend status plus facts become the owner's words. It is pure: no
   clock, no I/O, no store. `GET /api/projects/{id}` publishes its result under
   `presentation`; the interface renders that and decides nothing itself. The
   backend enum is not replaced -- `project.status`, `versions[].verification` and
   `files` keep their own words in the same payload, and the raw words are repeated
   inside `presentation.technical`, which the card renders collapsed.
2. **The state word is derived from the facts, in this order.**
   `АРХИВ` when archived, `ГОТОВО` only when the record is `COMPLETED` **and**
   `files > 0` **and** verification passed **and** acceptance passed **and** the
   workspace exists, `НЕ СОЗДАН` when nothing was written, otherwise
   `НУЖНЫ ДОРАБОТКИ`. `Files: 0` can therefore never render `ГОТОВО`; it is a
   structural consequence, not a check that a caller must remember.
3. **Six save facts, answered separately.** `Запуск сохранён`,
   `Спецификация сохранена`, `План сохранён`, `Файлы проекта сохранены`,
   `Проверка выполнена`, `Проект готов`. One combined "saved" line is what hid an
   empty project behind a stored run record. An unreadable fact is `неизвестно`,
   never `да` by default.
4. **Actions are gated by the files rule.** With no file the only creation action is
   `Продолжить создание проекта` (plus `Ответить на вопросы` while a session is
   open). With files, `Запустить локально` / `Открыть приложение` are offered only
   when the manifest holds a launchable artifact (an HTML page, or an entry file);
   otherwise they are hidden with a one-line reason. Every disabled action states
   why, and where the backend has its own refusal sentence it is printed verbatim
   beside the owner's reason.
5. **A change run that writes nothing does not advance the version.** If
   `files_created == 0`, the previous version stays current, the attempt is still
   recorded, and the card says `Изменения не записаны: новой версии нет`. The status
   word for that run is still `NEEDS_CHANGES`; nothing is invented to make the
   version look successful.
6. **The run response and the project card agree.** `_attach_project` now hands the
   durable record's own `state` to `record_project_attach`, not the response's
   already-downgraded word, so a project that really completed reports
   `outcome=PROJECT_COMPLETED, state=COMPLETED` instead of
   `outcome=PROJECT_COMPLETED, state=SPECIFICATION_READY`.

**Rejected alternatives.**

- **Map the statuses in the React client.** The rule would exist twice (once per
  language), the Python suite could not assert it, and the two copies would drift
  exactly the way the lifecycle gate and the API response once drifted.
- **Add a `presentation_status` field to the project record.** That is a second
  status vocabulary on the wire and in the store, and the stored value can be older
  than the facts it summarises; the card must be a function of the facts read now.
- **Hide the raw status and the technical facts entirely.** The defect was a
  perception defect, and hiding evidence would have replaced a misleading card with
  an unfalsifiable one. The technical block is collapsed, not removed.
- **Treat acceptance alone as completion.** Acceptance is the run's own verdict and
  says nothing about a file existing; the owner's card read exactly that way.

**Consequences.** `tests/test_project_truth_presentation.py` (32 tests) fixes the
mapping, the layout, the six save facts, the action gating and the version rule on
pure fake data, plus a real-lifecycle check that a second version preserves the
first. `app/smoke_project_truth.py` prints the two outcomes end to end, offline.
`tests/test_project_lifecycle.py`, `tests/test_false_completion_guard.py`,
`tests/test_api.py` and `tests/test_discovery.py` are unchanged and pass.

## Презентация правды о проекте v0.1 — русская версия

**Контекст.** Карточка проекта могла показывать `Files: 0`, `Verification: NOT RUN`
и `Status: NEEDS_CHANGES`, тогда как заполнивший её запуск сообщал `COMPLETED`, и
владелец читал это как готовый проект. Каждый факт по отдельности был верен; не
было одного места, которое выстраивает их в порядок чтения владельца, и не было
правила, которое не может сказать «готово» о пустом проекте.

**Решение.**

1. **Одна функция презентации, на Python.**
   `app/projects/presentation.py::build_project_presentation` — единственное место,
   где статус бэкенда и факты превращаются в слова владельца. Функция чистая: без
   часов, ввода-вывода и хранилища. `GET /api/projects/{id}` публикует её результат
   в поле `presentation`; интерфейс рисует его и сам ничего не решает. Перечисление
   бэкенда не заменяется — `project.status`, `versions[].verification` и `files`
   остаются в том же ответе со своими словами, а исходные слова повторяются в
   `presentation.technical`, который карточка показывает свёрнутым.
2. **Слово состояния выводится из фактов, в таком порядке.** `АРХИВ` для архива;
   `ГОТОВО` только когда запись `COMPLETED` **и** `files > 0` **и** проверка
   пройдена **и** приёмка пройдена **и** рабочая область существует; `НЕ СОЗДАН`,
   когда ничего не записано; иначе `НУЖНЫ ДОРАБОТКИ`. Поэтому `Files: 0` не может
   отрисовать `ГОТОВО` — это следствие структуры, а не проверка, о которой должен
   помнить вызывающий.
3. **Шесть фактов сохранения, каждый отдельно.** `Запуск сохранён`,
   `Спецификация сохранена`, `План сохранён`, `Файлы проекта сохранены`,
   `Проверка выполнена`, `Проект готов`. Именно одна общая строка «сохранено»
   скрывала пустой проект за записанным журналом запуска. Нечитаемый факт —
   `неизвестно`, а не `да` по умолчанию.
4. **Действия ограничены правилом файлов.** Без файла единственное действие
   создания — `Продолжить создание проекта` (плюс `Ответить на вопросы`, пока
   открыта сессия). С файлами `Запустить локально` / `Открыть приложение`
   предлагаются только когда в манифесте есть запускаемый артефакт (HTML-страница
   или файл-точка входа); иначе они скрыты с однострочной причиной. Каждое
   недоступное действие объясняет почему, а собственная фраза отказа бэкенда
   печатается дословно рядом с причиной владельца.
5. **Запуск доработки, ничего не записавший, не двигает версию.** Если
   `files_created == 0`, предыдущая версия остаётся текущей, попытка всё равно
   записывается, а карточка говорит `Изменения не записаны: новой версии нет`.
   Статус такого запуска остаётся `NEEDS_CHANGES`; ничего не выдумывается, чтобы
   версия выглядела успешной.
6. **Ответ запуска и карточка проекта согласованы.** `_attach_project` теперь
   передаёт в `record_project_attach` собственное `state` из журнала, а не уже
   понижённое слово ответа, поэтому действительно завершённый проект сообщает
   `outcome=PROJECT_COMPLETED, state=COMPLETED`, а не
   `outcome=PROJECT_COMPLETED, state=SPECIFICATION_READY`.

**Отклонённые альтернативы.**

- **Сопоставлять статусы в React-клиенте.** Правило существовало бы дважды (по
  одному на язык), набор тестов Python не мог бы его проверить, и копии разошлись
  бы так же, как когда-то разошлись гейт жизненного цикла и ответ API.
- **Добавить в запись проекта поле `presentation_status`.** Это второй словарь
  статусов и в хранилище, и в ответе, а хранимое значение может быть старее
  фактов, которые оно обобщает; карточка должна быть функцией фактов, прочитанных
  сейчас.
- **Скрыть исходный статус и технические факты совсем.** Дефект был дефектом
  восприятия, и скрытие доказательств заменило бы вводящую в заблуждение карточку
  на неопровержимую. Технический блок свёрнут, а не удалён.
- **Считать приёмку достаточной для завершения.** Приёмка — это вердикт самого
  запуска, и он ничего не говорит о существовании файла; карточка владельца
  читалась ровно так.

**Следствия.** `tests/test_project_truth_presentation.py` (32 теста) фиксирует
сопоставление, порядок карточки, шесть фактов сохранения, ограничения действий и
правило версий на чистых поддельных данных, плюс проверку на реальном жизненном
цикле, что вторая версия сохраняет первую. `app/smoke_project_truth.py` печатает
оба исхода от начала до конца, офлайн. `tests/test_project_lifecycle.py`,
`tests/test_false_completion_guard.py`, `tests/test_api.py` и
`tests/test_discovery.py` не менялись и проходят.

## Project Materialisation and Local Preview v0.1

**Decision.** A write-mode run that belongs to a persistent project writes into that
project's own controlled folder (`.forge/projects/<id>/workspace`), never into the
repository tree. The write itself is still the existing guarded writer
(`app.agent_runtime.code_writer.apply_changes`): `app/projects/materialize.py` only
points it at the project workspace and then describes the bytes that are really on
disk (path, kind, size, SHA-256, create/update, verification relevance). No file
content enters the manifest, an event or a payload, and no second writer, store or
boundary is introduced. The materialisation returns one typed result:
`PROJECT_NOT_CREATED` (unavailable, refused, or zero files), `FILES_CREATED` (files,
nothing compiled), `VERIFIED` (files, every Python file compiled) or `FAILED`
(verification failed, or an artifact could not be read back).

**Two write modes, and nothing else.** `MOCK_DETERMINISTIC` is the offline `mock`
provider's mode: the answer is a deterministic scaffold Forge itself produces
(`app.projects.mock_answer`), so the whole writer/manifest/preview path runs with no
model, no socket and no cost. The scaffold fills a project and never replaces one:
`scaffold_conflicts_with` refuses it when the project already holds `index.html` or
`app.py`, because on a delivered project those two names are the owner's code, and a
"change" that replaced them with Forge's template would be reported as a change while
destroying the work. Such a run writes nothing, fails, and says so.
`REAL_PROVIDER_EXPLICIT` applies only when the caller
sets `write_mode` **and** names both the provider and the model. Any other combination
resolves to the empty mode, writes nothing, and reports the empty mode in the
response, so "files appeared" is always traceable to one of two facts.

**The same words are one change.** `new_version` is idempotent by (project, change
text) after normalising whitespace and letter case. The last version recorded for the
same words is either `already_applied` (it holds files and is current: nothing is
staged, `task_text` is empty, no run is dispatched) or `retargeted` (it holds no files:
the retry writes into the same version number). This exists because the owner pressed
one button on one sentence three times and the record grew v3, v4 and v5, each with
zero files: a failed run fills its slot, so every later press appended another one.

**Entry point and preview.** The entry point is detected from the artifact manifest
only, with a fixed priority (`index.html`; `app.py`/`main.py`/`server.py` at the root;
`package.json` with a `start`/`dev` script, of which only the script *name* is read;
any other root `*.html`). The preview serves a `static_html` project read-only on
`127.0.0.1` with an ephemeral port: one directory, GET/HEAD only, strict path decoding,
containment re-proved per request, no listing, no symlinks, no execution. A `python`
or `node` entry point is refused with "local run is not wired for this entry kind"
instead of running the project's own script. The derived stage is one of eight words,
and `COMPLETED` additionally requires a running preview **or** an explicit
`preview_not_required` recorded on the version -- the existing gate still requires a
`passed` verification, so `not_applicable` never completes a project.

**Rejected alternatives.** A second writer or a second project store (two rail sets
would drift); launching a project by parsing model prose (a sentence would become an
execution decision); starting a project's `python`/`node` entry point as a "preview"
(that is arbitrary code execution, not a preview); treating the offline scaffold as a
model result (it is labelled as a mock in the output and in both written files);
inferring a write mode from the file count (the mode is decided before the run).

**Consequences.** `tests/test_materialization.py` (37 tests, offline) covers the
controlled folder, the boundary and deny-list refusals, the manifest, the entry-point
rules, the preview and its traversal refusals, the stage gate, both write modes, version
preservation, archive/restore and the absence of secrets or bodies from events and
payloads. `app/smoke_materialization.py` prints the whole path offline. `GET
/api/projects/{id}` gains additive `stage`, `files_created`, `entrypoint`, `preview`
and `verification_command` fields, and `POST /api/tasks/run` reports `write_mode`.

## Материализация проекта и локальный предпросмотр v0.1 — русская версия

**Решение.** Write-mode запуск, принадлежащий постоянному проекту, пишет в его
собственную контролируемую папку (`.forge/projects/<id>/workspace`), а не в дерево
репозитория. Запись по-прежнему выполняет существующий защищённый писатель
(`app.agent_runtime.code_writer.apply_changes`): `app/projects/materialize.py` лишь
указывает ему рабочую область проекта и затем описывает реально лежащие на диске
байты (путь, вид, размер, SHA-256, создание/обновление, отношение к проверке).
Содержимое файлов не попадает ни в манифест, ни в событие, ни в ответ, и второй
писатель, второе хранилище или вторая граница не появляются. Результат типизирован:
`PROJECT_NOT_CREATED` (недоступно, отказ или ноль файлов), `FILES_CREATED` (файлы
есть, ничего не компилировалось), `VERIFIED` (файлы есть, каждый `.py` скомпилирован)
или `FAILED` (проверка не пройдена или артефакт не читается обратно).

**Ровно два режима записи.** `MOCK_DETERMINISTIC` — режим офлайн-провайдера `mock`:
ответом служит детерминированный каркас, который производит сам Forge
(`app.projects.mock_answer`), поэтому весь путь писателя, манифеста и предпросмотра
проходит без модели, сокета и затрат. Каркас заполняет проект и никогда его не
заменяет: `scaffold_conflicts_with` отказывает, если в проекте уже есть `index.html`
или `app.py`, — на готовом проекте эти два имени и есть код владельца, и «изменение»,
заменившее их шаблоном Forge, было бы отчитано как изменение, разрушая работу. Такой
запуск ничего не пишет, завершается ошибкой и говорит об этом. `REAL_PROVIDER_EXPLICIT`
действует только когда вызывающий выставил `write_mode` **и** назвал и провайдера, и
модель. Любая другая комбинация даёт пустой режим, ничего не пишет и сообщает пустой
режим в ответе, поэтому «файлы появились» всегда объясняется одним из двух фактов.

**Одни и те же слова — одно изменение.** `new_version` идемпотентен по паре (проект,
текст изменения) после нормализации пробелов и регистра. Последняя версия, записанная
для тех же слов, — это либо `already_applied` (в ней есть файлы, и она текущая: ничего
не ставится в очередь, `task_text` пуст, запуск не отправляется), либо `retargeted`
(файлов нет: повтор пишет в тот же номер версии). Это существует потому, что владелец
нажал одну кнопку на одном предложении три раза, и в записи появились v3, v4 и v5,
каждая с нулём файлов: неудачный запуск заполняет свой слот, поэтому каждое следующее
нажатие добавляло ещё один.

**Точка входа и предпросмотр.** Точка входа определяется только по манифесту
артефактов, с фиксированным приоритетом (`index.html`; `app.py`/`main.py`/`server.py`
в корне; `package.json` со скриптом `start`/`dev`, из которого читается только *имя*
скрипта; любой другой корневой `*.html`). Предпросмотр отдаёт проект `static_html`
только для чтения на `127.0.0.1` со случайным портом: одна папка, только GET/HEAD,
строгое декодирование пути, containment перепроверяется на каждом запросе, нет
листинга, нет символических ссылок, нет выполнения. Для точки входа `python` или
`node` возвращается «local run is not wired for this entry kind» вместо запуска
собственного скрипта проекта. Производный этап — одно из восьми слов, и `COMPLETED`
дополнительно требует работающий предпросмотр **или** явный `preview_not_required`,
записанный на версии; существующий гейт по-прежнему требует `passed` проверки,
поэтому `not_applicable` никогда не завершает проект.

**Отклонённые альтернативы.** Второй писатель или второе хранилище проектов (два
набора ограничений разошлись бы); запуск проекта по прозе модели (предложение стало бы
решением об исполнении); запуск `python`/`node` точки входа как «предпросмотра» (это
произвольное исполнение кода, а не предпросмотр); выдача офлайн-каркаса за результат
модели (он помечен как mock в выводе и в обоих записанных файлах); вывод режима записи
из количества файлов (режим решается до запуска).

**Следствия.** `tests/test_materialization.py` (37 тестов, офлайн) покрывает
контролируемую папку, отказы границы и deny-list, манифест, правила точки входа,
предпросмотр и отказы обхода путей, гейт этапа, оба режима записи, сохранность версий,
архив/восстановление и отсутствие секретов и тел файлов в событиях и ответах.
`app/smoke_materialization.py` печатает весь путь офлайн. `GET /api/projects/{id}`
получает аддитивные поля `stage`, `files_created`, `entrypoint`, `preview` и
`verification_command`, а `POST /api/tasks/run` сообщает `write_mode`.

## Local run and preview for projects v0.2

**Decision.** A project's own entry point may now be **run locally**, inside its own
workspace, and a server it starts may be previewed — and it is run by the existing
execution plane, not beside it. This entry supersedes the sentence in *"Project
materialization and local preview v0.1"* that said a `python`/`node` entry point is
reported but not started: the reason it was not started was that no supervisor
existed, and the honest answer at the time was "not wired". Two facts changed that.
First, `ExecutionCoordinator` gained `authorize()` — the *same* chain
(`RunScope` → permission → intent → approval → policy → `workspace_root`) returning
the `AuthorizedExecution` token **without dispatching**, because a server must stay up
after the call that started it and `LocalExecutionAdapter.execute` is synchronous by
design. Second, the adapter's tree kill was lifted to a module-level
`terminate_process_tree`, so the supervisor terminates a process tree with exactly the
code the adapter already uses instead of a second implementation.

**The kinds, all from the manifest.** `app/projects/entrypoint.py` resolves exactly one
of `static_html`, `python_script` (root `app.py`/`main.py`/`server.py`),
`python_module` (a root package with `__main__.py`), `node_script` (root
`server.js`/`app.js`) or `node_npm` (`package.json` with a declared `start`/`dev`
script). Every command is built server-side: the interpreter is the repository's own
virtual environment (or this process's interpreter with `pythonw.exe` mapped to
`python.exe`), `node` is resolved once, and `npm` is started as
`node <npm-cli.js> run <script>` because CreateProcess cannot execute the `.cmd`
wrapper without a shell — and no shell is used anywhere. `npm install` is never part
of a command. A recognised shape whose runtime is missing carries a `reason` and an
**empty** command, which the runner reports as `RUN_BLOCKED`; it never invents one.

**The runner.** `app/projects/runner.py` supervises the child. `start(project,
approval_token)` refuses unless an explicit approval was recorded for *this* project
and *this* exact argv: the API must be given `{"confirm": true}` plus the
`entrypoint_digest` that was shown, the approval is single-use, it expires, and a
command that changed (even to another allow-listed script) makes it stale. The child
is started with `subprocess.Popen(shell=False)`, `cwd` = the authorized workspace
root, and an environment of exactly `PATH`, `SYSTEMROOT`, `TEMP` and
`PYTHONIOENCODING=utf-8` — a Forge credential, a provider key or a `.env` value is
*absent*, not blanked. Output is captured into two 64 KiB ring buffers, every line is
scanned by `DefaultSecretRedactor` **and** `find_forbidden_value_content`, and the
redacted result is persisted to `.forge/projects/<id>/run.log`. The states are
`NOT_RUN`, `STARTING`, `RUNNING`, `STOPPING`, `STOPPED`, `SUCCEEDED`, `FAILED`,
`TIMED_OUT`, `REFUSED` and `RUN_BLOCKED`. Timeout semantics are split on purpose: a
script that is meant to finish is killed at the timeout (`TIMED_OUT`); a process that
bound a localhost port is a server, so the timeout only answers "did it start" and
never kills it. `stop` terminates the **tree** through the job object / Toolhelp
snapshot / `taskkill /T` path on the verified pid — never by image name.

**The preview.** `app/projects/preview.py` keeps its static server unchanged and gains
a *registration* for a process preview: the runner publishes the URL only after it
observed the child listening on a localhost port (netstat on Windows, `/proc` on
Linux). A process that never bound a port has no URL, and the payload says
"the project process is not running, so there is no preview URL" instead of inventing
one. `POST /preview {"action": "stop"}` refuses to close a process preview: that would
leave a dead URL while the process kept running.

**The gate, the API and the card.** `app/projects/stage.py` gains `READY_TO_RUN`,
`RUNNING`, `STOPPED` and `BLOCKED`. A live process is `RUNNING` and outranks a stored
verdict; a refused or blocked attempt is `BLOCKED` only while the work is not
otherwise complete; `COMPLETED` still requires files, `passed` verification, `passed`
acceptance, the lifecycle record and a preview **or** `preview_not_required` — a
started process never supplies those facts and is never read as completion. The API
adds `GET/POST /api/projects/{id}/run`, `POST /api/projects/{id}/run/stop`,
`POST /api/projects/{id}/run/restart` and `GET /api/projects/{id}/run/log`, plus an
additive `run` block on the detail payload; absolute paths (the interpreter, the
`cwd`) appear only inside `diagnostics`. The card prints the language, entry point,
command, workspace, port, preview URL, process state, last event and logs, and offers
each button only when its own facts allow it; the launch is an explicit confirm step
that prints the exact command and the digest the backend compares.

**Rejected alternatives.** A second executor or a generic shell (two security surfaces
would drift); starting the process from `LocalExecutionAdapter.execute` with a
background wait (the adapter's own contract is "run and report", and a server has no
report); inferring the port from a project file or from prose (only an observed
listening socket is evidence); installing dependencies automatically (a run that
mutates its own environment is not a preview); claiming network isolation (the child
inherits the machine's network, and the payload says `inherited`).

**Consequences.** `tests/test_local_execution.py` (29 tests, offline) covers the five
kinds, the allowlist and path refusals, the approval boundary (missing, wrong,
cross-project, stale), success, module and node execution, the timeout kill, the
process-tree stop, the 64 KiB cap, the environment, restart, redaction, the process
preview and its absence, the four new stage words and the HTTP contract.
`app/smoke_python_preview.py` and `app/smoke_node_preview.py` walk
materialize → entrypoint → approval → start → preview `GET` 200 → verification → stop
(and the node one prints `SKIPPED: node is not installed` and exits 0 when there is no
node).

## Локальный запуск и предпросмотр проектов v0.2 — русская версия

**Решение.** Точку входа проекта теперь можно **запустить локально** в его собственной
рабочей области, а поднятый ею сервер — показать в предпросмотре, и запускает её
существующая плоскость исполнения, а не что-то рядом с ней. Эта запись отменяет фразу
из «Материализация проекта и локальный предпросмотр v0.1» о том, что точка входа
`python`/`node` только показывается, но не запускается: причиной было отсутствие
супервизора, и честным ответом тогда было «не подключено». Изменили это два факта.
Во-первых, у `ExecutionCoordinator` появился `authorize()` — **та же** цепочка
(`RunScope` → разрешение → намерение → одобрение → политика → `workspace_root`),
возвращающая токен `AuthorizedExecution` **без диспетчеризации**: сервер должен
оставаться живым после вызова, который его запустил, а `LocalExecutionAdapter.execute`
синхронен по своему контракту. Во-вторых, уничтожение дерева процессов из адаптера
вынесено в модульную `terminate_process_tree`, поэтому супервизор убивает дерево ровно
тем кодом, которым уже пользуется адаптер, а не второй реализацией.

**Виды, и только из манифеста.** `app/projects/entrypoint.py` разрешает ровно один из
`static_html`, `python_script` (корневые `app.py`/`main.py`/`server.py`),
`python_module` (корневой пакет с `__main__.py`), `node_script` (корневые
`server.js`/`app.js`) или `node_npm` (`package.json` с объявленным скриптом
`start`/`dev`). Каждая команда собирается на сервере: интерпретатор — собственное
виртуальное окружение репозитория (или интерпретатор этого процесса с заменой
`pythonw.exe` на `python.exe`), `node` разрешается один раз, а `npm` запускается как
`node <npm-cli.js> run <script>`, потому что CreateProcess не может выполнить
`.cmd`-обёртку без командного интерпретатора — а shell не используется нигде.
`npm install` никогда не входит в команду. Распознанная форма, у которой нет рантайма,
несёт `reason` и **пустую** команду, и раннер сообщает это как `RUN_BLOCKED`, а не
придумывает команду.

**Раннер.** `app/projects/runner.py` наблюдает за дочерним процессом. `start(project,
approval_token)` отказывает, пока не записано явное одобрение для **этого** проекта и
**этого** точного argv: API обязан получить `{"confirm": true}` и
`entrypoint_digest`, который был показан; одобрение одноразовое, истекает, а
изменившаяся команда (даже на другой разрешённый скрипт) делает его устаревшим.
Дочерний процесс стартует через `subprocess.Popen(shell=False)`, `cwd` = авторизованный
корень рабочей области, а окружение — ровно `PATH`, `SYSTEMROOT`, `TEMP` и
`PYTHONIOENCODING=utf-8`: учётные данные Forge, ключ провайдера или значение `.env`
**отсутствуют**, а не обнуляются. Вывод захватывается в два кольцевых буфера по 64 КиБ,
каждая строка проверяется `DefaultSecretRedactor` **и** `find_forbidden_value_content`,
а очищенный результат сохраняется в `.forge/projects/<id>/run.log`. Состояния:
`NOT_RUN`, `STARTING`, `RUNNING`, `STOPPING`, `STOPPED`, `SUCCEEDED`, `FAILED`,
`TIMED_OUT`, `REFUSED`, `RUN_BLOCKED`. Семантика таймаута разделена намеренно: скрипт,
который должен завершиться, убивается по таймауту (`TIMED_OUT`); процесс, занявший
локальный порт, — это сервер, поэтому таймаут отвечает только «успел ли он стартовать»
и никогда его не убивает. `stop` уничтожает **дерево** через job object / снимок
Toolhelp / `taskkill /T` по проверенному pid — никогда по имени образа.

**Предпросмотр.** `app/projects/preview.py` сохраняет статический сервер без изменений
и получает *регистрацию* процессного предпросмотра: раннер публикует URL только после
того, как увидел дочерний процесс слушающим локальный порт (netstat в Windows,
`/proc` в Linux). У процесса, который не занял порт, URL нет, и ответ говорит
«процесс проекта не запущен, поэтому URL предпросмотра нет» вместо выдуманного адреса.
`POST /preview {"action": "stop"}` отказывается закрывать процессный предпросмотр: это
оставило бы мёртвый URL при живом процессе.

**Гейт, API и карточка.** `app/projects/stage.py` получает `READY_TO_RUN`, `RUNNING`,
`STOPPED` и `BLOCKED`. Живой процесс — `RUNNING` и важнее записанного вердикта;
отказанная или заблокированная попытка — `BLOCKED`, только пока работа не завершена
иначе; `COMPLETED` по-прежнему требует файлов, `passed` проверки, `passed` приёмки,
записи жизненного цикла и предпросмотра **или** `preview_not_required` — запущенный
процесс этих фактов не даёт и никогда не читается как завершение. API добавляет
`GET/POST /api/projects/{id}/run`, `POST /api/projects/{id}/run/stop`,
`POST /api/projects/{id}/run/restart` и `GET /api/projects/{id}/run/log`, а также
аддитивный блок `run` в ответе детали; абсолютные пути (интерпретатор, `cwd`) видны
только внутри `diagnostics`. Карточка печатает язык, точку входа, команду, рабочую
область, порт, URL предпросмотра, состояние процесса, последнее событие и логи, и
показывает каждую кнопку только когда это позволяют её собственные факты; запуск —
явный шаг подтверждения, печатающий точную команду и отпечаток, который сверяет backend.

**Отклонённые альтернативы.** Второй исполнитель или generic shell (две поверхности
безопасности разошлись бы); запуск процесса из `LocalExecutionAdapter.execute` с
фоновым ожиданием (контракт адаптера — «запустить и сообщить», а у сервера нет
отчёта); вывод порта из файла проекта или из прозы (доказательство — только
наблюдаемый слушающий сокет); автоматическая установка зависимостей (запуск, который
меняет собственное окружение, — не предпросмотр); заявление о сетевой изоляции
(дочерний процесс наследует сеть машины, и ответ говорит `inherited`).

**Следствия.** `tests/test_local_execution.py` (29 тестов, офлайн) покрывает пять
видов, отказы allowlist и путей, границу одобрения (нет, неверный, другой проект,
устаревший), успешный запуск, модуль и node, убийство по таймауту, остановку дерева,
ограничение 64 КиБ, окружение, перезапуск, редактирование секретов, процессный
предпросмотр и его отсутствие, четыре новых слова этапа и HTTP-контракт.
`app/smoke_python_preview.py` и `app/smoke_node_preview.py` проходят путь
материализация → точка входа → одобрение → запуск → `GET` предпросмотра 200 →
проверка → остановка (а node-версия печатает `SKIPPED: node is not installed` и
завершается с кодом 0, когда node нет).

## Decision: provider-neutral structured intent (v0.1)

**Status:** accepted and implemented.

**Problem.** The run outcome was already decided by facts on disk, and correctly so. The
missing half was the *declaration*: an answer that asks three questions, proposes a plan,
offers files or delivers a review was indistinguishable from a finished answer, because
the only signal read was a narrow question-line heuristic. A structured protocol between
the model and Forge would make the declaration explicit — but a model-controlled status
would be a new way to fake completion, which is exactly the defect the outcome gate
exists to prevent.

**Decision.** Add a provider-neutral intent *parser*, not a protocol the model controls:

1. `app/agent_runtime/intent.py` is pure, stdlib-only and non-raising. It accepts three
   envelope shapes and validates the fields each type requires. Malformed JSON, an
   unknown type and a missing field produce a reported `FINAL_ANSWER` (or the recognised
   type with `valid=False` and named errors); the run continues.
2. Without an envelope, a deterministic heuristic classifies the answer: two or more
   question lines, a fenced file declaration, a numbered plan with no code fence, else
   plain text.
3. The parsed type reaches the **existing** classifier as one more optional fact
   (`derive_outcome(intent_type=...)`). It can only lower the outcome, and
   `intent_allows_completion` admits only `GENERATE_PROJECT` (or no declared intent), so
   `FINAL_ANSWER`, `CLARIFICATION_REQUIRED`, `PLAN_READY` and `REVIEW` can never yield a
   completed project.
4. The response publishes the row (`model_intent`: `intent_type`, `summary`, `valid`,
   `validation_errors`, `next_action`, `raw_text_preserved`) and does not echo the raw
   answer.

**Rejected alternatives.**

* A new completion path decided by the intent: two places would decide "done", and the
  model would gain control of the status.
* Raising the outcome when the model declares `GENERATE_PROJECT`: a declaration is not a
  write; only the write path plus verification and acceptance can complete a project.
* A heuristic-only solution (no envelope): the question and plan guesses cannot express
  a generation, and the owner asked for a structured option that providers can adopt
  later.
* Replacing the existing question extraction with the parser's: the classifier's narrow
  rule is already tested and used elsewhere, so `intent.py` imports it instead of
  restating it.
* Echoing the raw answer in the response row: the run response is a client-facing
  document, and the answer can contain prompt material or a credential-shaped string.

**Consequences.** The explicit provider/model override, the fallback chain, the approval
boundary and the paid-model policy are untouched: the intent is published, never obeyed.
`tests/test_structured_intent.py` (31 tests) and `tests/test_beta_path.py` (10 tests) are
offline; `app/smoke_structured_generation.py` demonstrates the four branches and exits 0
without opening a socket. Entry points that parse no model answer (the deterministic
offline path, a replay, a project attach) publish an empty mapping — an honest "no intent
declared" rather than an invented one.

## Решение: провайдеро-нейтральное структурированное намерение (v0.1)

**Статус:** принято и реализовано.

**Проблема.** Исход запуска уже решался фактами на диске, и это правильно. Не хватало
второй половины — *заявления*: ответ, который задаёт три вопроса, предлагает план,
предлагает файлы или даёт ревью, был неотличим от законченного ответа, потому что
единственным сигналом была узкая эвристика строк с вопросами. Структурированный
протокол между моделью и Forge сделал бы заявление явным, — но статус, управляемый
моделью, стал бы новым способом подделать завершение, а именно этот дефект гейт исхода
и предотвращает.

**Решение.** Добавить провайдеро-нейтральный *парсер* намерения, а не протокол, которым
управляет модель:

1. `app/agent_runtime/intent.py` чистый, только на stdlib и не бросающий исключений. Он
   принимает три формы конверта и проверяет поля, обязательные для каждого типа.
   Сломанный JSON, неизвестный тип и отсутствующее поле дают сообщённый `FINAL_ANSWER`
   (или распознанный тип с `valid=False` и названными ошибками); запуск продолжается.
2. Без конверта детерминированная эвристика классифицирует ответ: две или более строки
   вопросов, объявление файла в фенсе, нумерованный план без фенса кода, иначе плоский
   текст.
3. Разобранный тип попадает в **существующий** классификатор как один дополнительный
   необязательный факт (`derive_outcome(intent_type=...)`). Он может только понизить
   исход, а `intent_allows_completion` допускает только `GENERATE_PROJECT` (или
   отсутствие заявленного намерения), поэтому `FINAL_ANSWER`, `CLARIFICATION_REQUIRED`,
   `PLAN_READY` и `REVIEW` никогда не дают завершённого проекта.
4. Ответ публикует строку (`model_intent`: `intent_type`, `summary`, `valid`,
   `validation_errors`, `next_action`, `raw_text_preserved`) и не возвращает исходный
   ответ.

**Отклонённые альтернативы.**

* Новый путь завершения, решаемый намерением: тогда «готово» решалось бы в двух местах,
  и модель получила бы контроль над статусом.
* Повышение исхода, когда модель заявляет `GENERATE_PROJECT`: заявление — это не запись;
  завершить проект могут только путь записи плюс проверка и приёмка.
* Решение только на эвристике (без конверта): догадки о вопросах и плане не выражают
  генерацию, а владелец просил структурированный вариант, который провайдеры смогут
  принять позже.
* Замена существующего извлечения вопросов парсером: узкое правило классификатора уже
  протестировано и используется в другом месте, поэтому `intent.py` импортирует его, а не
  переписывает.
* Возврат исходного ответа в строке ответа: ответ запуска — клиентский документ, а в
  тексте могут быть материал промпта или строка, похожая на credential.

**Следствия.** Явный override провайдера/модели, цепочка fallback, граница подтверждения
и политика платных моделей не тронуты: намерение публикуется, но ему не подчиняются.
`tests/test_structured_intent.py` (31 тест) и `tests/test_beta_path.py` (10 тестов)
офлайн; `app/smoke_structured_generation.py` демонстрирует четыре ветки и завершается
кодом 0, не открывая сокет. Точки входа, которые не парсят ответ модели
(детерминированный офлайн-путь, replay, attach проекта), публикуют пустое отображение —
честное «намерение не заявлено» вместо выдуманного.

## Decision: a credential word is not a credential value (v0.1)

**Measured defect.** The owner's own manual run stopped after a single event with
`Critical Tier 1 context item 'task:…' cannot fit … Reason:
security_forbidden_content:secret`; the provider was never called and every later
stage stayed «ожидается». The refused item was the task description itself, and the
rule that fired was not a credential check: `find_forbidden_value_content` matched the
bare substring `secret` inside ordinary prose about not adding secrets to a project.
The same rule refused `password`, `credentials` and `API_KEY=` with an empty value, so
a normal idea that mentioned credential *hygiene* could not run at all. The
clarification layer was hit by the same rule: a secret-shaped *goal* is deliberately
not stored, so that session opened with an empty idea, derived nothing, and asked a
generic questionnaire.

**Decision.** Classify by *value*, never by *word*, and keep the boundary where it
matters:

1. The credential nouns (`secret`, `password`, `api_key`, `credential`) no longer match
   on their own in a context value. They still block a metadata *key*, where they name
   the thing being carried.
2. A secret-shaped VALUE is still refused, with the reason word it always had: a
   `Bearer` credential of 40+ characters, a JWT, a provider key prefix
   (`sk-`, `ghp_…`, `xoxb-…`, `AKIA…`), an assigned key, a password or credentials
   assignment carrying a real value, and a 40+ character high-entropy run.
3. An assignment pattern reads only the value on its **same line** and only when that
   value is machine-shaped, so an `.env.example` with empty values and
   `API_KEY=your-key-here` are documentation, not material.
4. The structural markers (`stdout`, `stderr`, `prompt`, `chain_of_thought`,
   `source_code`, `file_content`) and a bare `token` word stay refused: this decision
   narrows the credential nouns only.
5. The refusal publishes `security_forbidden_content:<reason>` unchanged, plus a
   content-free diagnostic -- `rule=<id>`, `length_bucket=<range>`,
   `location=<task_description|task_context|context_item>` -- and never the value and
   never a prefix of it.

**Rejected alternatives.** Matching the word and only relaxing the capitalisation: the
measured text is prose, not a key. Dropping the scanner from the task context: the task
description is user input, and a real token pasted into it must still be refused.
Raising the assigned-value threshold to 20 characters alone: a shorter but clearly
machine-made value would have slipped through, so the shape check stays beside it.

**Consequences.** `app/context/validation.py` owns `scan_value_content` (a finding with
`reason`, `rule`, `length_bucket`), `find_forbidden_value_content` keeps its signature
and its vocabulary, and `ContextSelector` raises with the safe sentence.
`tests/test_secret_filter_classification.py` (19 tests) pins the admissible prose, the
still-refused material, the no-echo rule and the fact that a safe task reaches the
provider; `tests/test_discovery.py`, `tests/test_context_secret_scanning.py`,
`tests/test_task_context_budget.py` and `tests/test_context_selector.py` stay green.
The security boundary is unchanged where it matters: every secret-shaped value is still
refused, and none of the new messages carries it.

## Решение: слово-креденшл — не значение-секрет (v0.1)

**Измеренный дефект.** Ручной запуск владельца остановился после одного события с
`Critical Tier 1 context item 'task:…' cannot fit … Reason:
security_forbidden_content:secret`; провайдер не вызывался, а все следующие стадии
остались «ожидается». Отказанным элементом было само описание задачи, а сработало не
проверка секрета: `find_forbidden_value_content` находил подстроку `secret` в обычной
прозе о том, что секреты нельзя добавлять в проект. То же правило отказывало
`password`, `credentials` и `API_KEY=` с пустым значением, поэтому нормальная идея,
упоминающая *гигиену* креденшлов, не могла запуститься вообще. Слой уточнения страдал
от того же правила: похожая на секрет **цель** намеренно не сохраняется, поэтому такая
сессия открывалась с пустой идеей, ничего не выводила и задавала общую анкету.

**Решение.** Классифицировать по *значению*, а не по *слову*, и сохранить границу там,
где она важна:

1. Слова-креденшлы (`secret`, `password`, `api_key`, `credential`) больше не совпадают
   сами по себе в значении контекста. Как *ключ* метаданных они по-прежнему запрещены:
   там они называют то, что несут.
2. Похожее на секрет ЗНАЧЕНИЕ по-прежнему отказывается, с прежним словом причины:
   `Bearer`-креденшл из 40+ символов, JWT, префикс ключа провайдера (`sk-`, `ghp_…`,
   `xoxb-…`, `AKIA…`), присвоенный ключ, присвоение пароля или креденшлов с реальным
   значением и случайный набор из 40+ символов с высокой энтропией.
3. Шаблон присвоения читает только значение на **той же строке** и только если оно
   машинной формы, поэтому `.env.example` с пустыми значениями и
   `API_KEY=your-key-here` — документация, а не материал.
4. Структурные маркеры (`stdout`, `stderr`, `prompt`, `chain_of_thought`,
   `source_code`, `file_content`) и отдельное слово `token` остаются запрещёнными:
   решение сужает только слова-креденшлы.
5. Отказ публикует прежний `security_forbidden_content:<reason>` и безопасную
   диагностику — `rule=<id>`, `length_bucket=<диапазон>`,
   `location=<task_description|task_context|context_item>`, — и никогда само значение
   и никогда его префикс.

**Отклонённые альтернативы.** Совпадение по слову с ослаблением регистра: измеренный
текст — проза, а не ключ. Убрать сканер из контекста задачи: описание задачи — ввод
пользователя, и настоящий токен, вставленный в него, должен по-прежнему отказываться.
Поднять порог присвоенного значения только до 20 символов: более короткое, но явно
машинное значение проскочило бы, поэтому проверка формы остаётся рядом.

**Следствия.** `app/context/validation.py` владеет `scan_value_content` (находка с
`reason`, `rule`, `length_bucket`), `find_forbidden_value_content` сохраняет сигнатуру и
словарь причин, а `ContextSelector` выбрасывает исключение с безопасной фразой.
`tests/test_secret_filter_classification.py` (19 тестов) фиксирует допустимую прозу,
по-прежнему отказываемый материал, правило «без эха» и тот факт, что безопасная задача
доходит до провайдера; `tests/test_discovery.py`,
`tests/test_context_secret_scanning.py`, `tests/test_task_context_budget.py` и
`tests/test_context_selector.py` остаются зелёными. Граница безопасности не ослаблена
там, где это важно: любое значение-секрет по-прежнему отказывается, и ни одно новое
сообщение его не содержит.

## Decision: the app asks the model for files, and asks exactly once more (v0.2)

**Context.** The measured product failure was not a model failure. A `code` task in write mode
(«создать безопасный экран проверки настроенных API-провайдеров», `deepseek/deepseek-flash`)
came back as a plan plus the question "which option, A/B/C?", the writer honestly recorded
`code_write.status = no_changes`, and the run was still presented as finished: `state=COMPLETED`,
nine ✅ chips and a «Запись сохранена: да» badge over a project that did not exist. Nothing in
the app had ever told the model what shape of answer the writer can apply, so getting a file
required the owner to know the ```` ```html path=… ```` convention himself.

**Decision.** The write-mode prompt is assembled by the code path, never by the owner, and it
carries a deterministic output contract (`app/agent_runtime/output_contract.py`): one fenced block
per file with a relative `path=`, the allowed write root, the forbidden `.env`/secret/`.git`
paths, "only what the plan needs", and the statement that a `code` run in write mode owes files.
The contract is appended after the task text and the bounded workspace listing, because the
instruction a model reads last is the one it follows; it is idempotent and adds nothing to a
read-only prompt. If the answer still carries no file block at all, the service asks **once**
more with a shorter, harder instruction, by re-entering the existing `RunExecutor` at its
revision seam (`_run`, `_attempt_number=1`) — the same frozen scope, provider pin and store, a
new task text. `MAX_WRITE_RETRIES = 1`; there is no loop. The retry is recorded in
`project_state["write_retry"]` and published as `write_retry_attempted`, so "asked twice" is a
durable fact rather than an inference from a call count. An answer that *does* carry file blocks
is never retried, and an empty answer is not retried either (there is nothing to ask again).

**Rejected alternatives.** Putting the contract in the owner's own task text: that is exactly the
prompt engineering the change exists to remove. A loop or an escalating retry budget: it spends
tokens on a model that has already ignored the instruction, and it hides a bad answer instead of
reporting it. Retrying on `no_changes` from any reason: "no change survived validation" is a
refusal, not a missing file block. Using the revision loop for this: acceptance-driven revision is
about failed *criteria*, and a run that wrote nothing has none.

**Consequences.** A model that answers with prose is asked once more and then reported honestly:
the result card now says «Проект не создан: модель вернула план вместо файлов» with a «Повторить»
button, and `COMPLETED` appears only inside the collapsed «Технические детали». The card's one
headline sentence is derived from the facts in `ui/src/forge-result.ts` (files, writer status,
verification, questions, error), never from the run's `state`; the raw `state`, `run_id`,
`event_count`, `classification.outcome`, the English `code_write.reason` and the diff live in that
collapsed block only, and the ninth chip reads «Завершено без файлов» when no file was written.

## Решение: приложение само просит у модели файлы и просит ровно один раз (v0.2)

**Контекст.** Измеренный продуктовый провал не был провалом модели. Задача `code` в режиме
записи («создать безопасный экран проверки настроенных API-провайдеров»,
`deepseek/deepseek-flash`) вернулась планом и вопросом «какой вариант выбираем A/B/C?», писатель
честно записал `code_write.status = no_changes`, а запуск всё равно был показан завершённым:
`state=COMPLETED`, девять ✅ и бейдж «Запись сохранена: да» над несуществующим проектом. Нигде в
приложении модель не получала форму ответа, которую писатель умеет применять, поэтому для
получения файла владельцу нужно было самому знать соглашение ```` ```html path=… ````.

**Решение.** Prompt режима записи собирает код, а не владелец, и он несёт детерминированный
контракт (`app/agent_runtime/output_contract.py`): по одному фенсед-блоку на файл с относительным
`path=`, разрешённый корень записи, запрет `.env`/секретов/`.git`, «только то, что нужно плану» и
утверждение, что запуск `code` в режиме записи обязан вернуть файлы. Контракт добавляется после
текста задачи и ограниченного списка файлов рабочей папки — инструкция, которую модель читает
последней, и выполняется последней; он идемпотентен и ничего не добавляет в read-only prompt.
Если в ответе всё равно нет ни одного файлового блока, сервис спрашивает **один** раз более
короткой и жёсткой инструкцией, входя в существующий `RunExecutor` через его шов ревизий
(`_run`, `_attempt_number=1`): та же замороженная область, тот же провайдер и store, новый текст
задачи. `MAX_WRITE_RETRIES = 1`; цикла нет. Повтор пишется в `project_state["write_retry"]` и
публикуется как `write_retry_attempted`, поэтому «спрашивали дважды» — долговечный факт, а не
вывод из числа вызовов. Ответ, в котором файловые блоки есть, не повторяется, и пустой ответ тоже
(спрашивать не о чем).

**Отклонённые альтернативы.** Контракт в тексте владельца: это ровно тот prompt engineering,
который изменение и убирает. Цикл или растущий бюджет повторов: он тратит токены на модель,
которая уже проигнорировала инструкцию, и скрывает плохой ответ вместо того, чтобы сообщить о нём.
Повтор при любом `no_changes`: «no change survived validation» — это отказ, а не отсутствие
файловых блоков. Использовать цикл ревизий: ревизия по acceptance — про проваленные *критерии*, а
у запуска, который ничего не записал, их нет.

**Следствия.** Модель, ответившая прозой, получает один дополнительный вопрос, после чего о
результате сообщается честно: карточка результата говорит «Проект не создан: модель вернула план
вместо файлов» с кнопкой «Повторить», а `COMPLETED` появляется только внутри свёрнутых
«Технических деталей». Единственная фраза карточки выводится из фактов в `ui/src/forge-result.ts`
(файлы, статус писателя, проверка, вопросы, ошибка) и никогда из `state` запуска; сырые `state`,
`run_id`, `event_count`, `classification.outcome`, английский `code_write.reason` и diff живут
только в этом свёрнутом блоке, а девятый шаг читается как «Завершено без файлов», когда ни один
файл не записан.

## Deleted projects are tombstoned by the trash entry v0.1

### Context

MEASURED: `POST /api/projects/{id}/verify` was already in flight when
`DELETE /api/projects/{id}` completed, and the project **came back**. The exact write path
was `verify_again` -> `ProjectStore.read` (before the move) -> `ProjectStore.write` (after
it): the record was re-created in the active store while its workspace sat in
`.forge/projects-deleted/<id>-<ts>/`, so `GET /api/projects/lifecycle` listed it again and
`POST /{id}/restore-deleted` answered `409 project_already_exists`. A real project on this
machine (`proj-2e2fe4a64973`) still has that shape. Deletion was a move with no durable
"this id is deleted" fact for a late writer to consult.

### Accepted decisions

- **The trash entry is the deletion marker.** `ProjectStore.delete` creates
  `projects-deleted/<id>-<ts>/` before it moves anything, and only a successful
  `restore_deleted` clears it. `trash_entries(id)` / `newest_trash_entry(id)` /
  `is_deleted(id)` read that fact from disk on every call, so it is durable, needs no
  in-memory state, and survives a restart. No second index file exists to drift from the
  trash.
- **The guard is on the write itself.** `ProjectStore.write` re-checks `is_deleted` and
  raises `ProjectDeleted` (`409 project_deleted`) before writing, so the measured path
  (read, delete, write) is refused at the exact point where the record would be
  re-created, even when the writer is another process.
- **One lock per project, held across read -> check -> write.** `ProjectService` keeps a
  `threading.Lock` per (store root, project id); every mutating operation (`confirm`,
  `attach_run`, `new_version`, `archive`, `restore`, `set_preview_not_required`,
  `verify_again`, `delete`, `restore_deleted`) holds it and calls `require_active` inside
  it. Chosen ordering: **DELETE waits for an in-flight verification to finish and then
  moves the project**; a verification that finds the marker instead answers
  `409 project_deleted`. Both outcomes are allowed by the product; a resurrected record is
  not.
- **`require_active` checks the marker before the record.** A record that a late writer
  already put back is still a deleted project. An unknown id stays `404 unknown_project`;
  a deleted id is `409 project_deleted` from every mutating route.
- **Restore moves the folder and the record back and clears the marker in one critical
  section.** If the trashed entry cannot be cleared, the move is rolled back: a restored
  project that still answers "deleted" would be worse than a retryable refusal. `409
  project_already_exists` is answered only when a genuinely active record holds the id.
- **A failed delete rolls its trash entry back.** A delete that could not move the project
  removes the empty entry it created, so a still-live project is never tombstoned.

### Rejected alternatives

- **An in-memory "deleted ids" set.** It dies with the process, so the first restart
  resurrects exactly the projects this fix exists to keep deleted.
- **A second marker file (`.deleted-index.json`) maintained by delete/restore.** It can
  drift from the trash after a crash between the two writes, and the trash already records
  the same fact; one source of truth is cheaper to keep correct.
- **Relying only on the write-time check, without the lock.** The record would still be
  re-created in the window between the check and `os.replace`, and the two halves of
  read-then-write would interleave. The lock makes the in-process interleaving
  deterministic; the marker is the cross-process backstop.
- **Hard-deleting the record so a late write cannot find it.** That destroys the
  reversible deletion the product promises (byte-identical restore).

### Consequences and open items

Within one process the interleaving is fully serialised; across processes the marker is
re-read at the write, which narrows the window to the check-to-rename gap of a single
write. A hard kill between creating the trash entry and moving the folder leaves a
tombstoned but unmoved project (refused, not resurrected); a hard kill during the move
leaves a partial trash entry that `restore_deleted` still refuses to guess about. Neither
is repaired automatically, deliberately: guessing here would either resurrect a project or
erase one. `tests/test_deleted_project_race.py` pins the fixed behaviour, including the
deterministic read -> delete -> write interleaving.

## Удалённые проекты помечаются записью в корзине v0.1

### Контекст

ИЗМЕРЕНО: `POST /api/projects/{id}/verify` уже выполнялся, когда
`DELETE /api/projects/{id}` завершился, и проект **вернулся**. Точный путь записи:
`verify_again` -> `ProjectStore.read` (до переноса) -> `ProjectStore.write` (после него):
запись создавалась заново в активном хранилище, пока её рабочая папка лежала в
`.forge/projects-deleted/<id>-<ts>/`, поэтому `GET /api/projects/lifecycle` снова показывал
её, а `POST /{id}/restore-deleted` отвечал `409 project_already_exists`. Реальный проект на
этой машине (`proj-2e2fe4a64973`) до сих пор имеет такую форму. Удаление было переносом без
долговечного факта «этот id удалён», который мог бы прочитать поздний писатель.

### Принятые решения

- **Маркер удаления — запись в корзине.** `ProjectStore.delete` создаёт
  `projects-deleted/<id>-<ts>/` до переноса чего-либо, и только успешный
  `restore_deleted` её убирает. `trash_entries(id)` / `newest_trash_entry(id)` /
  `is_deleted(id)` читают этот факт с диска при каждом вызове, поэтому он долговечен, не
  требует состояния в памяти и переживает перезапуск. Второго индексного файла нет,
  поэтому ему нечем разойтись с корзиной.
- **Проверка стоит на самой записи.** `ProjectStore.write` повторно проверяет
  `is_deleted` и до записи поднимает `ProjectDeleted` (`409 project_deleted`), поэтому
  измеренный путь (чтение, удаление, запись) отклоняется ровно в точке, где запись была бы
  создана заново, — даже если писатель находится в другом процессе.
- **Один замок на проект, удерживаемый на всём пути чтение -> проверка -> запись.**
  `ProjectService` держит `threading.Lock` на пару (корень хранилища, id проекта); каждая
  изменяющая операция (`confirm`, `attach_run`, `new_version`, `archive`, `restore`,
  `set_preview_not_required`, `verify_again`, `delete`, `restore_deleted`) удерживает его и
  вызывает `require_active` внутри. Выбранный порядок: **DELETE ждёт завершения
  выполняющейся проверки и затем переносит проект**; проверка, увидевшая маркер, отвечает
  `409 project_deleted`. Оба исхода допустимы для продукта; воскрешённая запись — нет.
- **`require_active` проверяет маркер раньше записи.** Запись, которую поздний писатель уже
  вернул, всё равно относится к удалённому проекту. Неизвестный id остаётся
  `404 unknown_project`; удалённый id даёт `409 project_deleted` на каждом изменяющем
  маршруте.
- **Восстановление возвращает папку и запись и очищает маркер в одной критической
  секции.** Если запись корзины не удаётся очистить, перенос откатывается: восстановленный
  проект, который всё ещё отвечает «удалён», был бы хуже, чем повторяемый отказ. `409
  project_already_exists` отвечается только когда id действительно занят активной записью.
- **Неудавшееся удаление убирает свою запись корзины.** Удаление, которое не смогло
  перенести проект, удаляет созданную пустую запись, поэтому живой проект никогда не
  помечается удалённым.

### Отклонённые альтернативы

- **Множество «удалённых id» в памяти.** Оно умирает вместе с процессом, и первый же
  перезапуск воскрешает ровно те проекты, которые это исправление обязано удерживать
  удалёнными.
- **Второй файл-маркер (`.deleted-index.json`), который ведут delete/restore.** Он может
  разойтись с корзиной после падения между двумя записями, а корзина уже хранит тот же
  факт; один источник истины дешевле держать корректным.
- **Полагаться только на проверку при записи, без замка.** Запись всё равно создавалась бы
  заново в окне между проверкой и `os.replace`, а половины «чтение-запись» чередовались бы.
  Замок делает чередование внутри процесса детерминированным; маркер — межпроцессная
  подстраховка.
- **Жёстко удалять запись, чтобы поздняя запись ничего не нашла.** Это уничтожает обратимое
  удаление, которое обещает продукт (побайтовое восстановление).

### Следствия и открытые вопросы

Внутри одного процесса чередование полностью сериализовано; между процессами маркер
перечитывается при записи, что сужает окно до промежутка между проверкой и переименованием
одной записи. Жёсткое завершение между созданием записи корзины и переносом папки оставляет
помеченный, но неперенесённый проект (отказ, не воскрешение); жёсткое завершение во время
переноса оставляет частичную запись корзины, о которой `restore_deleted` отказывается
догадываться. Ни то, ни другое не чинится автоматически — намеренно: догадка здесь либо
воскресила бы проект, либо стёрла его. `tests/test_deleted_project_race.py` закрепляет
исправленное поведение, включая детерминированное чередование чтение -> удаление -> запись.

## Decision: the clarification answers reach the next run, and the loop stops (v0.1)

### Context

MEASURED, in the owner's own loop. A run answered with questions; the result card said «Forge задал
вопросы — ответьте, и он продолжит» (`forge.headline.questions`); he pressed «Ответить», answered in
different words, pressed «приступить», and **the same card with the same questions appeared again**.
The screenshots show `state=COMPLETED` with no files, five recorded events, only step one confirmed,
the model's two questions quoted, and the «Ответить» button.

The path, exactly. `answerClarification` (`ui/src/screens/Forge.tsx`) opened the *discovery* card:
its questions come from `app/discovery/questions.py`, derived from the goal text, so the owner
answered questions the model never asked. Those answers went to
`POST /api/discovery/sessions/{id}/answers`, and a session that is already `CONFIRMED` refuses them
(`DiscoveryService.answer` -> `_require_active` -> `DiscoveryStateConflict`, 409) — the UI avoided the
refusal by opening a brand-new session from the same unchanged text. `submit()` then dispatched
`POST /api/tasks/run` with `description: trimmed` — the textarea — and the route had **no**
clarification input at all (`app/api/server.py`), while `ForgeApiService._write_task_description`
built the prompt from that description alone. The model was never told anything the owner had said.
Nothing counted the rounds, so `CLARIFICATION_REQUIRED` (or, in write mode, the write-mode failure
whose answer still carried the question list) re-derived the very same card forever.

### Decisions

- **The answers are part of the task text, not a second channel.** One deterministic block
  (`app/agent_runtime/clarification.py`): `Уточнения от пользователя:`, `- <question> → <answer>`
  lines, then `Продолжай и СЕЙЧАС создай файлы. Больше вопросов не задавай.` It is appended after
  the description and before the output contract, because the text a model reads last decides the
  answer's shape. The description is never rewritten.
- **Two rounds exist, and the second is forced.** `clarification_round` is 1 for a run that may ask
  and 2 for a run carrying answers; round 2 appends `intent=GENERATE_PROJECT` and «Do not ask
  further questions — write the files with the information you have», and
  `MAX_CLARIFICATION_ROUNDS = 2` clamps any retry of the final round back to 2. A third round is
  impossible by construction, not by a guard in the UI.
- **The answers are stored in the run's own durable state.** `project_state.clarification` and
  `project_state.clarification_round` are written beside `code_write` and `write_retry`; the request
  names `clarification_run_id` and the service reads that record back, so a retry keeps the answers
  without the client re-sending them. No second store, no session, nothing new to keep consistent.
- **The answers are read at the HTTP boundary and are not request-model fields.**
  `clarification_answers` and `clarification_run_id` join `discovery_session_id` in that rule: they
  are task text and durable context, never execution authority, so `TaskRunRequest` — whose field set
  several contract tests pin — is unchanged.
- **The clarification context belongs to the run, not to a discovery session.** A `CONFIRMED`, open
  or absent session makes no difference to whether an answer is used; an unknown `clarification_run_id`
  starts a fresh context instead of silently dropping the answers.
- **A repeated question is dropped, and nothing new means the end.** Questions are compared
  normalised (case, punctuation, list markers, «Уточните, пожалуйста:», ё/е); an answered question is
  not surfaced again, and an empty remainder escalates to the final round rather than offering the
  same list.
- **The card reads the backend's own round.** `TaskRunResponse.clarification_round` is additive and
  last (like `lifecycle_project_id`); round 2 has its own headline, «Больше Forge вопросов не задаст:
  модель не смогла создать файлы», with «Повторить» and no «Ответить». «Ответить» dispatches exactly
  one new run and the card names its id.

### Rejected alternatives

- **Keep using the discovery card for a run's questions.** Its questions are derived from the goal,
  not from the model's answer; using it is what made the answers irrelevant to the next prompt.
- **Merge the answers into the discovery session's specification only.** That text is already
  prepended to the run, and the model still asked the same two questions: the specification says what
  the project is, not which of *its* questions has been answered.
- **A global "answered questions" registry.** It has no owner and no lifetime, so one project's
  answers would silence another's questions; the run record already has both.
- **Let the client decide the round.** A client that has lost its state (a refresh) could then reopen
  round 3, which is exactly the loop. The server counts, the client reads.
- **Add the new fields to `TaskRunRequest`.** The model's field set is a pinned contract and these
  values are context, not a task-request field; the boundary passes them like the discovery id.

### Consequences and open questions

A retry of the final round re-sends the same answers and stays round 2, so «Повторить» is honest
(it gives the model another attempt with the same information) and can never reopen a clarification
round. `tests/test_clarification_loop.py` (18 tests, offline) pins the text, the order, the two-round
limit, the filter, the durable answers, the unchanged provider/model/approval facts, the still-refused
paid pair, one dispatch per «Ответить» through the real route, and the terminal card executed from the
shipped TypeScript. What is NOT claimed: the model itself may still answer with a plan after round 2 —
that is reported by the existing plan/retry card, not as completion, and no new automatic attempt is
made.

## Решение: ответы на уточнения доходят до следующего запуска, и цикл останавливается (v0.1)

### Контекст

ИЗМЕРЕНО, в цикле самого владельца. Запуск ответил вопросами; карточка результата сказала «Forge
задал вопросы — ответьте, и он продолжит» (`forge.headline.questions`); он нажал «Ответить», ответил
другими словами, нажал «приступить», и **та же карточка с теми же вопросами появилась снова**.
Скриншоты показывают `state=COMPLETED` без файлов, пять записанных событий, подтверждённый только
первый шаг, процитированные два вопроса модели и кнопку «Ответить».

Путь, точно. `answerClarification` (`ui/src/screens/Forge.tsx`) открывала карточку *уточнения*: её
вопросы берутся из `app/discovery/questions.py` и выводятся из текста цели, поэтому владелец отвечал
на вопросы, которых модель не задавала. Эти ответы уходили в
`POST /api/discovery/sessions/{id}/answers`, а сессия, которая уже `CONFIRMED`, их не принимает
(`DiscoveryService.answer` -> `_require_active` -> `DiscoveryStateConflict`, 409); интерфейс обходил
отказ, открывая новую сессию из того же неизменного текста. Затем `submit()` отправлял
`POST /api/tasks/run` с `description: trimmed` — текстом из поля, — а маршрут **вообще не имел**
входа для уточнений (`app/api/server.py`), и `ForgeApiService._write_task_description` строил промпт
только из этого описания. Модель никогда не узнавала ничего из сказанного владельцем. Раунды никто не
считал, поэтому `CLARIFICATION_REQUIRED` (а в режиме записи — отказ записи, чей ответ всё равно нёс
список вопросов) снова и снова порождал ту же карточку.

### Принятые решения

- **Ответы — часть текста задачи, а не второй канал.** Один детерминированный блок
  (`app/agent_runtime/clarification.py`): `Уточнения от пользователя:`, строки
  `- <вопрос> → <ответ>`, затем `Продолжай и СЕЙЧАС создай файлы. Больше вопросов не задавай.` Он
  добавляется после описания и до выходного контракта, потому что текст, который модель читает
  последним, определяет форму ответа. Описание никогда не переписывается.
- **Раундов ровно два, и второй — форсированный.** `clarification_round` равен 1 для запуска,
  который может спрашивать, и 2 для запуска с ответами; раунд 2 добавляет `intent=GENERATE_PROJECT`
  и «Do not ask further questions — write the files with the information you have», а
  `MAX_CLARIFICATION_ROUNDS = 2` возвращает любой повтор последнего раунда обратно в 2. Третий раунд
  невозможен по построению, а не по проверке в интерфейсе.
- **Ответы хранятся в долговечном состоянии самого запуска.** `project_state.clarification` и
  `project_state.clarification_round` пишутся рядом с `code_write` и `write_retry`; запрос называет
  `clarification_run_id`, и сервис читает эту запись обратно, поэтому повтор сохраняет ответы без
  повторной отправки клиентом. Второго хранилища, сессии и новой согласованности не появляется.
- **Ответы читаются на границе HTTP и не являются полями модели запроса.** `clarification_answers` и
  `clarification_run_id` следуют тому же правилу, что `discovery_session_id`: это текст задачи и
  долговечный контекст, а не полномочия на выполнение, поэтому `TaskRunRequest` — чей набор полей
  закреплён контрактными тестами — не изменён.
- **Контекст уточнения принадлежит запуску, а не сессии уточнения.** Состояние сессии (`CONFIRMED`,
  открыта или её нет) не влияет на то, будет ли ответ использован; неизвестный `clarification_run_id`
  начинает свежий контекст, а не молча теряет ответы.
- **Повторённый вопрос выбрасывается, а «нового нет» означает конец.** Вопросы сравниваются
  нормализованно (регистр, пунктуация, маркеры списка, «Уточните, пожалуйста:», ё/е); отвеченный
  вопрос больше не показывается, а пустой остаток эскалируется в последний раунд вместо повторной
  выдачи того же списка.
- **Карточка читает собственный раунд бэкенда.** `TaskRunResponse.clarification_round` аддитивно и
  последним (как `lifecycle_project_id`); у раунда 2 свой заголовок «Больше Forge вопросов не
  задаст: модель не смогла создать файлы» с «Повторить» и без «Ответить». «Ответить» запускает ровно
  один новый запуск, и карточка называет его id.

### Отклонённые альтернативы

- **Продолжать использовать карточку уточнения для вопросов запуска.** Её вопросы выводятся из цели,
  а не из ответа модели; именно поэтому ответы не влияли на следующий промпт.
- **Добавлять ответы только в спецификацию сессии уточнения.** Эта спецификация уже добавляется к
  запуску, и модель всё равно задавала те же два вопроса: спецификация говорит, что за проект, а не
  на какой из *его* вопросов уже ответили.
- **Глобальный реестр «отвеченных вопросов».** У него нет владельца и времени жизни, поэтому ответы
  одного проекта глушили бы вопросы другого; в записи запуска уже есть и то, и другое.
- **Позволить клиенту решать номер раунда.** Клиент, потерявший состояние (перезагрузка), открыл бы
  раунд 3 — ровно тот цикл. Считает сервер, читает клиент.
- **Добавить новые поля в `TaskRunRequest`.** Набор полей этой модели — закреплённый контракт, а эти
  значения являются контекстом, а не полем запроса задачи; граница передаёт их так же, как id сессии
  уточнения.

### Следствия и открытые вопросы

Повтор последнего раунда переотправляет те же ответы и остаётся раундом 2, поэтому «Повторить»
честен (даёт модели ещё одну попытку с той же информацией) и никогда не открывает раунд уточнений
заново. `tests/test_clarification_loop.py` (18 тестов, офлайн) закрепляет текст, порядок, предел в
два раунда, фильтр, долговечность ответов, неизменность фактов провайдера/модели/подтверждений,
по-прежнему отказанную платную пару, один диспатч на «Ответить» через настоящий маршрут и
терминальную карточку, исполняемую из поставляемого TypeScript. Чего НЕ утверждается: сама модель
может и после раунда 2 ответить планом — это показывает существующая карточка плана/повтора, а не
завершение, и новой автоматической попытки не делается.

## Decision: a delivered project is a visible folder, and the card has a real entrance into it

### Context

MEASURED, in the owner's own words: *«он должен работать как швейцарские часы: я вставляю промт и в
конце получаю на рабочий стол готовый проект. Из Forge должен быть вход в проект — кнопка перехода»*.
Three facts, each verified in the code before this change:

- a lifecycle project's files lived in `.forge/projects/<id>/workspace` — a hidden subdirectory of the
  repository's own store. The card published a repository-relative path and the preview block printed
  a sentence saying a browser cannot open folders, so there was no folder the owner could open in
  Explorer or in VS Code;
- no route started anything on the machine: the only "open" affordances scrolled the page or printed
  the path that was already on screen, which is indistinguishable from a dead click;
- the correction loop existed (`POST /api/projects/{id}/versions` plus the run that fills the version)
  but the block that drives it was titled «Добавить функцию / новая версия», sat below the artifact
  table, and its result never said *what* had changed.

### Decisions

- **Two roots, and they are different things.** The *record* (JSON, versions, artifact manifest, run
  links) stays in `.forge/projects/<id>/` — it is evidence and it is never moved. The *files* are
  delivered to a **visible project root**: `%USERPROFILE%\Desktop\Forge Projects` by default, in a
  normal folder named `<project name> (proj-<id>)`.
- **The root is the application's own setting, not `.env`.** `app/config/app_settings.py` stores
  `project_root` (and the roots previously used) in `.forge/settings.json`, written atomically, holding
  no key and no provider configuration. `GET/POST /api/settings` are the only writers; «Настройки» has
  a text field and «Использовать рабочий стол».
- **The record, not the setting, says where an existing project lives.** `LifecycleProject.workspace_dir`
  holds the absolute folder (written to the record file and deliberately excluded from `to_dict`, so it
  can never be published); `project_path` holds the publishable form — root-relative for a visible
  project, repository-relative for a legacy one. A project created before this change keeps working, and
  changing the root does not move anything: it only decides the next project's folder.
- **«Переместить в видимую папку» moves files, never history.** `ProjectService.move_to_visible_folder`
  copies each file, compares SHA-256 before and after, refuses a destination that already holds
  different bytes, and removes the source only after every file matched. The record keeps its versions;
  only `workspace_dir`/`project_path` are rewritten.
- **One trash root, the existing one.** A delete moves the folder — visible or legacy — into
  `.forge/projects-deleted/<id>-<ts>`, where the listing and the restore already work. Restore puts the
  folder back where the **trashed record** says it belongs, proved against the configured roots; a
  recorded folder that no longer proves falls back to the legacy workspace and the record is rewritten.
- **The boundary is re-proved for the new root.** `app/projects/folders.py` refuses a relative path, a
  `..` segment, a device path, a path whose real (link-resolved) location leaves every configured root,
  and a path inside `.git`; a junction planted inside the root is therefore refused as well. Workspace
  *files* additionally refuse `.git` segments and anything the repository's existing secret recognizer
  knows (`.env`, a private key), so Forge never writes a secret into a folder the owner can commit.
- **Three OS integrations, allow-listed, with no shell.** `app/projects/opener.py` performs exactly
  `explorer.exe <folder>`, `code <folder>` and `wt -d <folder>` (falling back to
  `powershell -NoExit -Command Set-Location -LiteralPath '<folder>'`). The path is resolved server-side
  from the record and never accepted from the request; every process is started with `shell=False` and
  an argv; a target that cannot be performed answers `409 project_open_refused` with a plain sentence
  and starts nothing. The card disables the button with that same sentence.
- **The correction loop is one obvious block.** `POST /api/projects/{id}/versions` remains the only
  staging path, and the card renders «Что поправить?» (textarea + «Исправить проект») above the
  preview, the local-run panel and the artifacts, then reports the new version, the run id, and the
  files the version's own manifest says changed (create/update + size). A run that wrote nothing says
  «Изменения не записаны: новой версии нет» and shows no invented file list.

### Rejected alternatives

- **Move the record too.** It would put the proof of a project's history inside a folder the owner can
  rename, move to the cloud or delete — and it would break the durable deletion marker and the trash.
- **Store the root in `.env`.** `.env` holds deployment configuration and credentials; a folder the
  owner picks is neither, and «Настройки» is where he expects to change it.
- **Publish the absolute path everywhere.** The card keeps printing the relative folder above the fold;
  the absolute path is a field of the collapsed diagnostics block, where it can be pasted.
- **`shell=True` with a composed command string.** A folder name is user text; as a string it becomes
  command syntax. The one command that must carry it quotes it with `-LiteralPath` and a doubled quote,
  and is still started as an argv.
- **Create the visible folder on first write instead of at project creation.** A record pointing at a
  folder it does not have would be a lie the API publishes; the folder is created (and proved) before
  the record that names it is written.
- **A second trash root under the project root.** It would double the restore path and split the
  durable "this id is deleted" marker across two directories.

### Consequences and open questions

`tests/test_project_visible_root.py`, `tests/test_project_open_actions.py` and
`tests/test_owner_acceptance.py` (plus the extended `tests/test_flow_invariants.py`) are the evidence;
the acceptance one walks the owner's sentence end to end with an in-process scripted provider and a
recording opener, and never opens a real window. The launcher (`app/api/main.py`) composes the visible
root from the settings file; a caller that builds its own `ProjectService` (every test, every laboratory
script) keeps the legacy hidden workspace, which is why this change adds no Desktop traffic to the suite.
Remaining limits: a folder the owner deletes by hand is reported (the open targets refuse with
"папка проекта не найдена") but not recreated; `.forge/backups/run-<id>/` inside a project folder is the
existing writer backup and is not a project file; there is no OS sandbox, so a program the owner starts
from Forge still runs with his own token.

## Решение: готовый проект — это видимая папка, а в карточке есть настоящий вход в него

### Контекст

ИЗМЕРЕНО, словами владельца: *«он должен работать как швейцарские часы: я вставляю промт и в конце
получаю на рабочий стол готовый проект. Из Forge должен быть вход в проект — кнопка перехода»*. Три
факта, каждый проверен в коде до этого изменения:

- файлы проекта жизненного цикла лежали в `.forge/projects/<id>/workspace` — скрытом подкаталоге
  хранилища самого репозитория. Карточка показывала путь относительно репозитория, а блок предпросмотра
  печатал фразу о том, что браузер не открывает папки, поэтому не было папки, которую владелец мог бы
  открыть в Проводнике или VS Code;
- ни один маршрут ничего не запускал на машине: единственные «открывающие» элементы прокручивали
  страницу или печатали уже показанный путь, что неотличимо от мёртвого клика;
- цикл правок существовал (`POST /api/projects/{id}/versions` плюс запуск, заполняющий версию), но
  блок назывался «Добавить функцию / новая версия», находился под таблицей артефактов, и его результат
  никогда не говорил, *что именно* изменилось.

### Принятые решения

- **Два корня, и это разные вещи.** *Запись* (JSON, версии, манифест артефактов, связи с запусками)
  остаётся в `.forge/projects/<id>/` — это доказательство, и оно не переносится. *Файлы* попадают в
  **видимый корень проектов**: по умолчанию `%USERPROFILE%\Desktop\Forge Projects`, в обычную папку с
  именем `<имя проекта> (proj-<id>)`.
- **Корень — собственная настройка приложения, а не `.env`.** `app/config/app_settings.py` хранит
  `project_root` (и ранее использованные корни) в `.forge/settings.json`, записываемом атомарно, без
  ключей и конфигурации провайдеров. `GET/POST /api/settings` — единственные писатели; в «Настройках»
  есть текстовое поле и кнопка «Использовать рабочий стол».
- **Где живёт существующий проект, говорит запись, а не настройка.** `LifecycleProject.workspace_dir`
  хранит абсолютную папку (пишется в файл записи и намеренно исключена из `to_dict`, поэтому не может
  быть опубликована); `project_path` хранит публикуемую форму — относительную к корню для видимого
  проекта и относительную к репозиторию для старого. Проект, созданный до этого изменения, продолжает
  работать, а смена корня ничего не переносит: она определяет папку только следующего проекта.
- **«Переместить в видимую папку» переносит файлы, а не историю.**
  `ProjectService.move_to_visible_folder` копирует каждый файл, сравнивает SHA-256 до и после,
  отказывает, если в приёмнике уже лежат другие байты, и удаляет источник только после совпадения всех
  файлов. Запись сохраняет версии; переписываются только `workspace_dir`/`project_path`.
- **Один корень корзины — существующий.** Удаление переносит папку (видимую или старую) в
  `.forge/projects-deleted/<id>-<ts>`, где уже работают список и восстановление. Восстановление
  возвращает папку туда, куда указывает **запись из корзины**, проверяя путь против настроенных корней;
  если записанная папка больше не проходит проверку, проект возвращается в старое скрытое место, и
  запись переписывается.
- **Граница заново доказывается для нового корня.** `app/projects/folders.py` отказывает
  относительному пути, сегменту `..`, пути устройства, пути, реальное (с разрешёнными ссылками)
  положение которого выходит за все настроенные корни, и пути внутри `.git`; поэтому junction,
  посаженный внутри корня, тоже отклоняется. *Файлы* рабочей области дополнительно отклоняют сегменты
  `.git` и всё, что знает существующий распознаватель секретов репозитория (`.env`, приватный ключ),
  чтобы Forge никогда не записал секрет в папку, которую владелец может закоммитить.
- **Три интеграции ОС, по белому списку и без оболочки.** `app/projects/opener.py` выполняет ровно
  `explorer.exe <папка>`, `code <папка>` и `wt -d <папка>` (с запасным
  `powershell -NoExit -Command Set-Location -LiteralPath '<папка>'`). Путь разрешается на сервере из
  записи и никогда не принимается из запроса; каждый процесс запускается с `shell=False` и вектором
  аргументов; недоступное действие отвечает `409 project_open_refused` обычной фразой и ничего не
  запускает. Кнопка в карточке отключается с той же фразой.
- **Цикл правок — один очевидный блок.** `POST /api/projects/{id}/versions` остаётся единственным путём
  подготовки версии, а карточка показывает «Что поправить?» (текстовое поле + «Исправить проект») выше
  предпросмотра, панели локального запуска и артефактов, затем сообщает новую версию, id запуска и
  файлы, которые изменила именно эта версия (создан/обновлён + размер). Запуск, ничего не записавший,
  говорит «Изменения не записаны: новой версии нет» и не показывает выдуманный список файлов.

### Отклонённые альтернативы

- **Переносить и запись.** Тогда доказательство истории проекта лежало бы в папке, которую владелец
  может переименовать, перенести в облако или удалить, — и это сломало бы долговечный маркер удаления и
  корзину.
- **Хранить корень в `.env`.** В `.env` лежат конфигурация развёртывания и секреты; выбранная папка —
  ни то, ни другое, и менять её владелец ожидает в «Настройках».
- **Публиковать абсолютный путь везде.** Карточка печатает относительную папку на виду; абсолютный путь
  — поле свёрнутого блока технических деталей, откуда его можно скопировать.
- **`shell=True` со склеенной строкой команды.** Имя папки — пользовательский текст; в строке оно
  становится синтаксисом команды. Единственная команда, которой нужен путь текстом, заключает его в
  `-LiteralPath` с удвоенной кавычкой и всё равно запускается вектором аргументов.
- **Создавать видимую папку при первой записи, а не при создании проекта.** Запись, указывающая на
  папку, которой нет, была бы ложью, которую API публикует; папка создаётся (и проверяется) до записи,
  которая её называет.
- **Вторая корзина под корнем проектов.** Она удвоила бы путь восстановления и раздвоила бы долговечный
  маркер «этот id удалён» между двумя каталогами.

### Следствия и открытые вопросы

Доказательство — `tests/test_project_visible_root.py`, `tests/test_project_open_actions.py` и
`tests/test_owner_acceptance.py` (плюс расширенный `tests/test_flow_invariants.py`); приёмочный тест
проходит фразу владельца целиком с внутрипроцессным сценарным провайдером и записывающим opener-ом и
никогда не открывает настоящее окно. Лаунчер (`app/api/main.py`) собирает видимый корень из файла
настроек; вызывающий, который создаёт свой `ProjectService` (каждый тест, каждый лабораторный скрипт),
сохраняет старое скрытое расположение — поэтому это изменение не добавляет набору тестов обращений к
рабочему столу. Оставшиеся ограничения: удалённую вручную папку система сообщает (действия открытия
отказывают фразой «папка проекта не найдена»), но не пересоздаёт; `.forge/backups/run-<id>/` внутри
папки проекта — существующая резервная копия писателя, а не файл проекта; содержание запущенного
процесса проекта появилось позже и описано в решении «Содержание запущенного процесса проекта»
ниже — оно ограничивает дерево процессов и целостность токена, но не сеть и не файлы на хосте, где
сам Forge работает при той же низкой целостности.

---

## Decision: a launched project runs inside a job object with a low-integrity token (v0.1)

### Context

MEASURED before this change: the process a project's own entry point ran as was created by
`subprocess.Popen` with the owner's own token, merely assigned to a job object that carried no limits.
Four consequences, each observed rather than assumed:

* stop killed the tree, but nothing bounded it while it ran -- a runaway allocation or a process bomb
  could take the machine down;
* the child held the owner's full token: it could write anything the owner could write, including
  Forge's own records in `.forge/projects/<id>/*.json`, the run store and the repository's `.env`;
* the payload said `network_policy: "inherited"` and nothing about files or tokens, so the card could
  not tell the owner what was and was not contained;
* the `--offline` flag was already only a declaration, and there was no honest place to say so per run.

The owner's last agreed item was an **honest** containment layer: implement what needs no administrator
rights, and say precisely what it does and does not stop.

### Decisions

* **Two mechanisms, both stdlib `ctypes`, no new dependency.** `CreateJobObjectW` +
  `SetInformationJobObject(JobObjectExtendedLimitInformation)` with
  `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`, `JOB_OBJECT_LIMIT_PROCESS_MEMORY` and
  `JOB_OBJECT_LIMIT_ACTIVE_PROCESS`; and, for the token, `CREATE_SUSPENDED` + `OpenProcessToken` +
  `SetTokenInformation(TokenIntegrityLevel, S-1-16-4096)` + `GetTokenInformation` + a documented
  thread resume. `TerminateJobObject` is the stop path.
* **Read the limits back.** The reported limits come from `QueryInformationJobObject`, never from the
  values the code asked for: a limit the kernel did not accept is published as `0`.
* **The token is only lowered when the project folder is writable at that level.** The folder is
  labelled low integrity (`AddMandatoryAce` + `SetNamedSecurityInfoW`) first; if that is impossible the
  token is left unchanged and the report says `integrity: "unchanged"` rather than breaking the run.
  `TEMP`/`TMP` of a lowered child point inside its own folder.
* **The write boundary is measured, not claimed.** A probe process under the *same* containment tries
  to create a file in the project folder, create a file in the project store root and overwrite a record
  Forge put there. `writes_outside` is `blocked` / `allowed` / `unknown` -- and `allowed` is rendered as
  a warning, never as success. On a host where Forge itself runs at low integrity (measured here: the
  repository, its store and even its `.venv` interpreters are low-labelled), that honest answer is
  `allowed`, and the card says «программа работает с вашими правами».
* **No admin-only layer is claimed.** Default-deny network needs Windows Firewall/WFP; a restricted
  token from `SaferComputeTokenFromLevel` needs `CreateProcessAsUser` and
  `SeAssignPrimaryTokenPrivilege`; AppContainer needs capability ACLs on every DLL of the runtime.
  `network` is therefore always `"inherited (not isolated)"` and `--offline` stays a declaration.
* **A refused, never-run or disabled sandbox cannot look contained.** The booleans default to false and
  `writes_outside` to `unknown`; the only state that may be shown as contained is `job_object` **and**
  `restricted_token` **and** `writes_outside == "blocked"`.

### Rejected alternatives

* **Claiming network isolation via proxy environment variables.** It is bypassed by any client that
  opens a socket itself, and it would be a lie in the payload.
* **`SaferComputeTokenFromLevel` + `CreateProcessAsUser`.** It fails without
  `SeAssignPrimaryTokenPrivilege` on a normal user; pretending a restricted token exists would be the
  exact dishonesty this change removes.
* **An AppContainer.** It would be a real kernel boundary, but it needs ACLs on the interpreter's
  installation outside the repository (and would break the run when they are missing).
* **Lowering the token without labelling the project folder.** The child would be unable to write its
  own output; a containment that breaks the feature is not acceptable.
* **Guessing `writes_outside` from the token level.** The probe measures the process's actual ability;
  this host proves why the guess would be wrong.

### Consequences and open questions

Evidence: `app/execution/containment.py`, the extended `app/execution/adapter.py`
(`set_process_tree_job_limits`, `process_tree_job_limits`), `app/projects/runner.py`, the `sandbox`
block and the «Песочница» panel in `ui/src/screens/Projects.tsx`, and `tests/test_run_sandbox.py`
(13 offline tests: tree kill by pid, memory and process caps read back from the OS, the measured write
boundary, the environment, the payload shape, the refused/never-run/disabled states, the configured
server lifetime). Costs: one short-lived probe process per start (bounded at 8 s) and in-memory
containment state per run. Open: on a host where Forge itself is low-integrity the token layer does not
separate the child from Forge's data -- measured as `allowed` and shown as a warning; a genuine boundary
there needs the administrator-only mechanisms listed above.

---

## Решение: запущенный процесс проекта работает внутри job object с токеном низкой целостности (v0.1)

### Контекст

ИЗМЕРЕНО до этого изменения: процесс, в котором работала собственная точка входа проекта, создавался
через `subprocess.Popen` с токеном самого владельца и лишь приписывался к job object без лимитов.
Четыре следствия, каждое наблюдённое, а не предположенное:

* остановка убивала дерево, но ничто не ограничивало его во время работы — runaway-аллокация или
  «бомба процессов» могли уронить машину;
* дочерний процесс держал полный токен владельца: он мог писать всё, что мог писать владелец, включая
  собственные записи Forge в `.forge/projects/<id>/*.json`, хранилище запусков и `.env` репозитория;
* payload говорил `network_policy: "inherited"` и ничего — о файлах и токене, поэтому карточка не
  могла сказать владельцу, что именно содержится, а что нет;
* флаг `--offline` уже был только объявлением, и не было честного места сказать это на каждый запуск.

Последний согласованный пункт владельца — **честный** слой содержания: реализовать то, что не требует
прав администратора, и точно сказать, что он останавливает, а что нет.

### Принятые решения

* **Два механизма, оба на `ctypes` из стандартной библиотеки, без новой зависимости.**
  `CreateJobObjectW` + `SetInformationJobObject(JobObjectExtendedLimitInformation)` с
  `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`, `JOB_OBJECT_LIMIT_PROCESS_MEMORY` и
  `JOB_OBJECT_LIMIT_ACTIVE_PROCESS`; и для токена — `CREATE_SUSPENDED` + `OpenProcessToken` +
  `SetTokenInformation(TokenIntegrityLevel, S-1-16-4096)` + `GetTokenInformation` + документированное
  возобновление потока. Остановка идёт через `TerminateJobObject`.
* **Лимиты читаются обратно.** Сообщаемые лимиты берутся из `QueryInformationJobObject`, а не из того,
  что просил код: лимит, который ядро не приняло, публикуется как `0`.
* **Токен понижается только когда папка проекта доступна на запись при этом уровне.** Сначала папка
  помечается низкой целостностью (`AddMandatoryAce` + `SetNamedSecurityInfoW`); если это невозможно,
  токен остаётся без изменений и отчёт говорит `integrity: "unchanged"`, вместо того чтобы ломать
  запуск. `TEMP`/`TMP` понижённого процесса указывают внутрь его собственной папки.
* **Граница записи измеряется, а не заявляется.** Пробный процесс под **тем же** содержанием пытается
  создать файл в папке проекта, создать файл в корне хранилища проектов и перезаписать запись, которую
  положил Forge. `writes_outside` — это `blocked` / `allowed` / `unknown`, и `allowed` показывается
  предупреждением, а не успехом. На хосте, где сам Forge работает при низкой целостности (здесь
  измерено: репозиторий, его хранилище и даже интерпретаторы `.venv` помечены низко), честный ответ —
  `allowed`, и карточка говорит «программа работает с вашими правами».
* **Ни один слой, требующий администратора, не заявляется.** Default-deny сети требует Windows
  Firewall/WFP; restricted token из `SaferComputeTokenFromLevel` требует `CreateProcessAsUser` и
  `SeAssignPrimaryTokenPrivilege`; AppContainer требует ACL на каждой DLL рантайма. Поэтому `network`
  всегда `"inherited (not isolated)"`, а `--offline` остаётся объявлением.
* **Отказанное, не запускавшееся или выключенное содержание не может выглядеть содержащимся.** Булевы
  поля по умолчанию false, `writes_outside` — `unknown`; единственное состояние, которое можно показать
  как «содержится», — это `job_object` **и** `restricted_token` **и** `writes_outside == "blocked"`.

### Отклонённые альтернативы

* **Заявлять сетевую изоляцию через переменные окружения с прокси.** Это обходится любым клиентом,
  который открывает сокет сам, и было бы ложью в payload.
* **`SaferComputeTokenFromLevel` + `CreateProcessAsUser`.** Не работает без
  `SeAssignPrimaryTokenPrivilege` у обычного пользователя; выдавать restricted token за существующий —
  ровно та нечестность, которую это изменение убирает.
* **AppContainer.** Это была бы настоящая граница ядра, но ей нужны ACL на установку интерпретатора вне
  репозитория (и запуск ломался бы без них).
* **Понижать токен без пометки папки проекта.** Дочерний процесс не смог бы записать свой собственный
  вывод; содержание, ломающее функцию, недопустимо.
* **Угадывать `writes_outside` по уровню токена.** Проба измеряет фактическую способность процесса;
  этот хост доказывает, почему догадка была бы неверной.

### Следствия и открытые вопросы

Доказательство: `app/execution/containment.py`, расширенный `app/execution/adapter.py`
(`set_process_tree_job_limits`, `process_tree_job_limits`), `app/projects/runner.py`, блок `sandbox` и
панель «Песочница» в `ui/src/screens/Projects.tsx`, а также `tests/test_run_sandbox.py` (13 офлайн
тестов: убийство дерева по pid, лимиты памяти и процессов, прочитанные из ОС, измеренная граница
записи, окружение, форма payload, состояния «отказано / не запускался / выключено», настроенное время
жизни сервера). Цена: один короткоживущий пробный процесс на запуск (ограничен 8 с) и состояние
содержания в памяти на каждый запуск. Открыто: на хосте, где сам Forge работает при низкой
целостности, слой токена не отделяет дочерний процесс от данных Forge — это измеряется как `allowed`
и показывается предупреждением; настоящая граница там требует перечисленных выше механизмов, доступных
только администратору.

## A derived project name and a writable project root v0.1

**Context.** The owner could not create a project at all. `POST /api/discovery/sessions/{id}/confirm`
answered `409 unsafe_project_folder` with *«папку проекта не удалось создать:
…\Desktop\Forge Projects\Создай локальное приложение «Почтовый центр» — только Python (ст
(proj-…) (PermissionError)»*: the folder was named from the first ~60 characters of his description, and
the preferred root — his Desktop — was not writable by the process.

**Decisions.**

* **The folder name is derived from the idea by a documented rule, not copied from it.** A quoted phrase
  wins (the owner naming his own product), then the first 3–5 meaningful words, then `Проект <N>`; the
  result is one Windows-safe component capped at 40 characters at a word boundary, and a collision is
  numbered (`(2)`, `(3)`, …) rather than shared or refused. The full idea stays in `specification`.
  *Why:* a name is a label, and the first 60 characters of a paragraph are not one. *Rejected:* keeping
  the long name and only shortening the path — the card would still show a sentence where a name belongs.
* **The project root is a preference, resolved by probing, with a fixed fallback order.** Configured root
  → `%USERPROFILE%\Forge Projects` → `%USERPROFILE%\Documents\Forge Projects` →
  `%LOCALAPPDATA%\Forge Projects` → `.forge\projects-visible` inside the repository. *Why:* a project
  creation must never die because one folder is protected; the last candidate is the folder the product
  always owns. *Rejected:* refusing with a 409 the owner cannot act on (the measured failure), and
  silently using the legacy hidden workspace (that is the original complaint about invisible projects).
* **The chosen fallback is remembered separately from the preference.** `project_root_fallback` in
  `.forge/settings.json` plus an entry in `project_roots_known`; `project_root` is untouched. *Why:* the
  fallback is a fact about this machine, not a new choice; overwriting the setting would move his projects
  for good the first time a permission changed, and dropping the root from the known list would make
  already-delivered projects unopenable.
* **Only a total failure is a refusal, and it is typed.** `ProjectRootUnavailable`
  (`code="project_root_unavailable"`) carries every attempted path and the OS reason; the route answers one
  409, never a stack trace.
* **The probe uses one `mkdir`, never `tempfile.mkdtemp`.** On Windows `mkdtemp` retries `PermissionError`
  10 000 times because it trusts `os.access(dir, W_OK)` (the read-only attribute, not the ACL). Measured
  here: a protected folder made `mkdtemp` fail to return in 20 s while a single `os.mkdir` refused in the
  same millisecond. A writability probe that can hang is worse than the refusal it was meant to prevent.

**Evidence.** `app/projects/root_selection.py`, `app/projects/folders.py` (`derive_project_name`,
`visible_folder`), `app/config/app_settings.py` (`project_root_fallback`, `describe`),
`app/api/discovery_routes.py` (the additive `project_root` block and the typed 409),
`tests/test_project_visible_root.py` (44 tests), `tests/test_project_open_actions.py` (27 tests),
`tests/test_owner_acceptance.py` (the owner's own idea, with and without an unwritable root).

**Consequences and open limits.** Every `workspace_path` resolution now probes the root (four file
operations); that is the price of an honest answer on a machine whose permissions change. If a candidate's
writability itself hangs inside the OS, no timeout is applied — the probe is a direct syscall, and the
measured hang came from the standard library, not from the OS. The fallback does not retry a project
already delivered to another root: the record's folder is re-proved against all known roots instead.

## Производное имя проекта и корень, в который можно писать v0.1

**Контекст.** Владелец не мог создать проект вообще. `POST /api/discovery/sessions/{id}/confirm` отвечал
`409 unsafe_project_folder` с текстом «папку проекта не удалось создать:
…\Desktop\Forge Projects\Создай локальное приложение «Почтовый центр» — только Python (ст (proj-…)
(PermissionError)»: папка называлась первыми ~60 символами описания, а предпочтительный корень — его
Рабочий стол — был недоступен процессу для записи.

**Решения.**

* **Имя папки выводится из идеи по задокументированному правилу, а не копируется из неё.** Побеждает
  фраза в кавычках (владелец сам называет продукт), затем первые 3–5 значимых слов, затем `Проект <N>`;
  результат — один безопасный для Windows компонент пути длиной до 40 символов по границе слова, а
  совпадение нумеруется (`(2)`, `(3)`, …), а не делится и не приводит к отказу. Полная идея остаётся в
  `specification`. *Почему:* имя — это ярлык, а первые 60 символов абзаца им не являются. *Отклонено:*
  оставить длинное имя и сокращать только путь — карточка всё равно показывала бы предложение там, где
  должно быть имя.
* **Корень проектов — предпочтение, разрешаемое пробой, с фиксированным порядком запасных вариантов.**
  Настроенный корень → `%USERPROFILE%\Forge Projects` → `%USERPROFILE%\Documents\Forge Projects` →
  `%LOCALAPPDATA%\Forge Projects` → `.forge\projects-visible` внутри репозитория. *Почему:* создание
  проекта не должно умирать из-за одной защищённой папки; последний кандидат — папка, которая всегда
  принадлежит продукту. *Отклонено:* отказ 409, с которым владелец ничего не может сделать (измеренный
  отказ), и молчаливое использование старой скрытой рабочей области (это и есть исходная жалоба на
  невидимые проекты).
* **Выбранный запасной корень запоминается отдельно от предпочтения.** `project_root_fallback` в
  `.forge/settings.json` плюс запись в `project_roots_known`; `project_root` не трогается. *Почему:*
  запасной вариант — факт об этой машине, а не новый выбор; перезапись настройки увела бы его проекты
  навсегда при первом изменении прав, а удаление корня из списка известных сделало бы уже выданные
  проекты неоткрываемыми.
* **Отказом является только полный отказ, и он типизирован.** `ProjectRootUnavailable`
  (`code="project_root_unavailable"`) несёт все испробованные пути и причину от ОС; маршрут отвечает
  одним 409, никогда трассировкой.
* **Проба делает один `mkdir`, а не `tempfile.mkdtemp`.** На Windows `mkdtemp` повторяет
  `PermissionError` 10 000 раз, потому что доверяет `os.access(dir, W_OK)` (атрибут «только чтение», а не
  ACL). Измерено здесь: на защищённой папке `mkdtemp` не вернулся за 20 с, тогда как одиночный
  `os.mkdir` отказал за ту же миллисекунду. Проба доступности, которая может зависнуть, хуже отказа,
  который она должна была предотвратить.

**Доказательство.** `app/projects/root_selection.py`, `app/projects/folders.py` (`derive_project_name`,
`visible_folder`), `app/config/app_settings.py` (`project_root_fallback`, `describe`),
`app/api/discovery_routes.py` (аддитивный блок `project_root` и типизированный 409),
`tests/test_project_visible_root.py` (44 теста), `tests/test_project_open_actions.py` (27 тестов),
`tests/test_owner_acceptance.py` (собственная идея владельца — с недоступным корнем и без него).

**Следствия и открытые ограничения.** Каждое разрешение `workspace_path` теперь пробует корень (четыре
файловые операции); это цена честного ответа на машине, где права меняются. Если доступность кандидата
зависает внутри ОС, таймаут не применяется — проба это прямой системный вызов, а измеренное зависание
шло из стандартной библиотеки, а не из ОС. Запасной вариант не переносит проект, уже выданный в другой
корень: папка из записи заново доказывается против всех известных корней.

## Write-mode durability, no scaffold substitution, and launchable static pages v0.3

**Decision.** Three measured defects in the write-mode/project path are closed by one
rule each, and nothing else changes.

1. **The effective write mode is part of the durable record.**
   `project_state["write_mode"]` is written by both task paths with the same string the
   response reports (`MOCK_DETERMINISTIC`, `REAL_PROVIDER_EXPLICIT`, or `""` for a run
   that did not write). A run that had the checkbox on and reported
   `REAL_PROVIDER_EXPLICIT` used to leave the key absent, so a write-mode run that
   silently degraded to "not allowed to write" was indistinguishable from one that
   wrote. The record and the response now cannot disagree.
2. **The deterministic scaffold belongs to the offline `mock` provider and to nothing
   else.** `app.projects.mock_answer.deterministic_answer_for_provider(provider_name,
   task_text)` returns `""` for every other provider, and the offline path asks it with
   the *effective* provider name — not with the write-mode string alone. On the provider
   path an answer that carries **no** fenced block with a `path=` header (after the one
   existing retry) applies nothing: the writer is not called, no version advances, and
   the run ends as a failure with `Проект не создан: модель не вернула файлы` and
   `files_created = 0`. A real provider's silence is never answered with Forge's own
   template.
3. **A static page is runnable.** `static_html` reports `runnable: true`,
   `serve_kind: "static"` and the fixed display sentence `открыть в браузере (preview)`;
   its launch is the existing read-only preview route. The card offers «Запустить
   локально» and «Открыть приложение» for it, and the run panel's launch button calls
   that preview route instead of asking the runner for a process it cannot start. The
   honest refusals stay: a process kind whose argv was not resolved is still not
   runnable, and the runner still refuses a `static_html` entry with
   `static_entry_point` (the preview route, not the runner, is a page's launch).

**Why.** The owner asked for a mail-centre application and received a five-file static
scaffold inside a project that reported nothing about how it had been written; and the
same project's card said its page could not be run while the preview served it with
HTTP 200. Each of the three facts above was the missing half of an honesty rule the
product already claimed.

**Rejected alternatives.** Recording the mode only in the response (the record is what
an operator reads after a restart); inferring the mode from `code_write` (a write that
was refused has no mode in it either); treating "no file block" as success (that is the
substitution this decision forbids); making `static_html` a process entry so the
existing run button applies (arbitrary execution instead of a preview); keeping
`runnable: false` and only relabelling the button (the button would call a route that
refuses).

**Evidence.** `tests/test_write_output_contract.py` (write mode recorded for a real
provider and for a read-only run; the guard refuses every provider but `mock`; the
writer is never called for an answer with no file; a real answer with files is written
from its own text), `tests/test_projects_change_run.py` (a zero-file change keeps the
previous version current, records the mode and names the sentence),
`tests/test_materialization.py` (the static entry is runnable with the preview sentence;
the API publishes both launch actions), `tests/test_project_truth_presentation.py`.
`python -m compileall -q app tests` and `git diff --check` are clean, and the full
suite runs.

**Consequences and open limits.** The single retry still carries only the hard
instruction and the workspace listing, not the task text (pinned by
`tests/test_write_output_contract.py::test_09`): a real provider re-asked without its
task can answer an unrelated project, and that answer is applied because it *is* the
model's answer. Widening the retry prompt is a product decision, not part of this fix.
A generated project that hard-codes a busy port still cannot start: the app reports
`FAILED` with the child's own exit code, and the local run stays a real launch attempt.

## Сохранность режима записи, отсутствие подстановки каркаса и запускаемые статические страницы v0.3

**Решение.** Три измеренных дефекта на пути режима записи и проекта закрываются по
одному правилу каждый, и больше ничего не меняется.

1. **Эффективный режим записи — часть durable-записи.**
   `project_state["write_mode"]` пишется обоими путями задачи той же строкой, что
   сообщает ответ (`MOCK_DETERMINISTIC`, `REAL_PROVIDER_EXPLICIT` или `""` для запуска
   без записи). Раньше запуск с включённым флажком, сообщивший
   `REAL_PROVIDER_EXPLICIT`, оставлял ключ отсутствующим, поэтому тихая деградация до
   «писать нельзя» была неотличима от запуска, который писал. Теперь запись и ответ не
   могут расходиться.
2. **Детерминированный каркас принадлежит офлайн-провайдеру `mock` и никому больше.**
   `app.projects.mock_answer.deterministic_answer_for_provider(provider_name,
   task_text)` возвращает `""` для любого другого провайдера, и офлайн-путь
   спрашивает её с *эффективным* именем провайдера, а не только со строкой режима
   записи. На пути провайдера ответ, не содержащий **ни одного** блока с заголовком
   `path=` (после единственного существующего повтора), не применяется: писатель не
   вызывается, версия не продвигается, и запуск завершается неудачей с
   `Проект не создан: модель не вернула файлы` и `files_created = 0`. Молчание
   реального провайдера никогда не заменяется собственным шаблоном Forge.
3. **Статическая страница запускаема.** `static_html` сообщает `runnable: true`,
   `serve_kind: "static"` и фиксированную строку показа
   `открыть в браузере (preview)`; её запуск — существующий маршрут предпросмотра
   только для чтения. Карточка предлагает для неё «Запустить локально» и «Открыть
   приложение», а кнопка запуска в панели запуска вызывает этот маршрут предпросмотра,
   а не просит раннер о процессе, который тот не может запустить. Честные отказы
   остаются: вид-процесс без разрешённого argv по-прежнему не запускаем, и раннер
   по-прежнему отказывает точке входа `static_html` с `static_entry_point` (запуск
   страницы — маршрут предпросмотра, а не раннер).

**Почему.** Владелец просил приложение «Почтовый центр», а получил статический каркас
из пяти файлов внутри проекта, который ничего не сообщал о том, как он был записан; и
карточка того же проекта говорила, что страницу нельзя запустить, тогда как
предпросмотр отдавал её с HTTP 200. Каждый из трёх фактов выше — недостающая половина
правила честности, которое продукт уже обещал.

**Отклонённые альтернативы.** Записывать режим только в ответ (запись читает оператор
после перезапуска); выводить режим из `code_write` (у отказанной записи режима в ней
тоже нет); считать «нет файлового блока» успехом (это и есть запрещённая подстановка);
сделать `static_html` видом-процессом, чтобы работала существующая кнопка запуска
(произвольное исполнение вместо предпросмотра); оставить `runnable: false` и лишь
переименовать кнопку (кнопка вызывала бы маршрут, который отказывает).

**Доказательство.** `tests/test_write_output_contract.py` (режим записан для реального
провайдера и для запуска только на чтение; гард отказывает всем провайдерам кроме
`mock`; для ответа без файлов писатель не вызывается; ответ с файлами записывается из
своего текста), `tests/test_projects_change_run.py` (нулевая доработка сохраняет
текущую версию, записывает режим и называет фразу),
`tests/test_materialization.py` (статическая точка входа запускаема со строкой
предпросмотра; API публикует оба действия запуска),
`tests/test_project_truth_presentation.py`. `python -m compileall -q app tests` и
`git diff --check` чисты, полный набор тестов прогоняется.

**Следствия и открытые ограничения.** Единственный повтор по-прежнему несёт только
жёсткую инструкцию и листинг рабочей области, без текста задачи (закреплено
`tests/test_write_output_contract.py::test_09`): реальный провайдер, переспрошенный без
своей задачи, может ответить посторонним проектом, и такой ответ применяется, потому
что это *ответ модели*. Расширение промпта повтора — продуктовое решение, а не часть
этого исправления. Сгенерированный проект с жёстко заданным занятым портом
по-прежнему не запустится: приложение сообщает `FAILED` с собственным кодом выхода
дочернего процесса, а локальный запуск остаётся настоящей попыткой запуска.

## Decision: the list is the owner's, and a name reads like a product v0.4

**Decision.** Two product defects are closed by two rules, and the store's data is tidied
through the routes that already exist.

1. **The list draws the owner's projects.** `GET /api/projects/lifecycle` publishes one
   additive fact per row, `in_visible_root` (`ProjectStore.in_visible_root`): the
   record's own `workspace_dir`, proved to sit inside a **visible** delivery root. «Проекты»
   draws the `true` rows by default and offers «Показать всё ({n} скрыто)» for the rest.
   The *answer* is never filtered — the whole listing stays whole — so the screen can
   count what it hides, and a deployment with no visible root shows everything rather
   than hiding everything. The table is bounded to one page (`ui/src/project-list.js`,
   `PROJECT_LIST_PAGE_SIZE` = 25) with «Показать ещё», the same shape the trash uses.
2. **A derived name is a noun phrase, not a task sentence.** `derive_project_name` cuts
   the idea at a comma, a relative clause and a phrase-length colon head; drops leading
   "create/make/I want/simple" words *repeatedly* and trailing courtesies; keeps five
   words and 40 characters at a word boundary; rewrites the part before the first
   preposition from the accusative into the nominative; hyphenates compound prefixes; and
   capitalises the first letter. A quoted phrase still wins outright.

**Why.** The owner opened «Проекты» and saw 42 rows of task sentences — 41 of them
records the test suite and the demo scripts had left in the hidden workspace — with his
only real project («Почтовый центр») lost in the middle of them. A name derived from an
idea also read like the request («Хочу простую локальную страницу-визитку…») instead of
like a product.

**Rejected alternatives.** Filtering the API answer (a client could no longer say how much
it hid); deleting the records outright (the trash already exists and a mistake must be one
click from undone, so the tidy went through `DELETE /api/projects/{id}` one by one);
guessing from the name alone (evidence is the folder, the name and the file count);
lower-casing the whole derived name (it would destroy proper nouns such as `FastAPI`);
normalising every word of the idea (after a preposition the owner's grammar is already
right: «Лендинг для новогоднего корпоратива»).

**Evidence.** `tests/test_project_list_filter.py` (7 tests: the visible-root fact, the
default view, the toggle, a project that is always visible, the page and «Показать ещё»,
the archive tab; the list rule itself is executed with Node from
`ui/src/project-list.js`), `tests/test_project_visible_root.py` (the owner's four ideas,
repeated leading words, the colon and relative-clause cuts, the English equivalents, the
cap), `tests/test_project_lifecycle.py` (the additive field), plus the existing
`tests/test_project_delete.py`, `tests/test_api.py`, `tests/test_owner_acceptance.py` and
`tests/test_user_journey.py`.

**Consequences and open limits.** `demo-projects` is in `project_roots_known` on this
host, so a record delivered there counted as visible until the tidy moved it; the rule
itself is "inside a root this deployment has delivered into", not "inside the root of the
current setting". The nominative rewrite is suffix-only (`-ую/-юю/-у/-ю`) with a documented
list of indeclinables, so a first-person verb that survives the leading fillers could be
rewritten; such verbs are in the filler list for that reason. `in_visible_root` is derived
per request, so a record whose folder is gone reports `false` and stays hidden behind the
toggle instead of disappearing.

## Решение: список принадлежит владельцу, а имя читается как продукт v0.4

**Решение.** Два продуктовых дефекта закрываются двумя правилами, а данные хранилища
приводятся в порядок существующими маршрутами.

1. **Список рисует проекты владельца.** `GET /api/projects/lifecycle` публикует по каждой
   строке один аддитивный факт — `in_visible_root` (`ProjectStore.in_visible_root`):
   собственный `workspace_dir` записи, доказанно лежащий внутри **видимого** корня
   доставки. «Проекты» по умолчанию рисует строки `true`, а для остальных предлагает
   «Показать всё ({n} скрыто)». Сам *ответ* никогда не фильтруется — полный список
   остаётся полным, — поэтому экран может посчитать скрытое, а развёртывание без
   видимого корня показывает всё, а не скрывает всё. Таблица ограничена одной страницей
   (`ui/src/project-list.js`, `PROJECT_LIST_PAGE_SIZE` = 25) с «Показать ещё» — тем же
   приёмом, что применяет корзина.
2. **Выведенное имя — именная группа, а не предложение задачи.** `derive_project_name`
   обрезает идею по запятой, придаточному и двоеточию с головой в 2–5 слов; **повторно**
   отбрасывает ведущие «создай/сделай/хочу/простой» и хвостовые любезности; оставляет
   пять слов и 40 символов по границе слова; переписывает часть до первого предлога из
   винительного падежа в именительный; соединяет составные приставки дефисом; делает
   первую букву заглавной. Фраза в кавычках по-прежнему побеждает полностью.

**Почему.** Владелец открыл «Проекты» и увидел 42 строки предложений-задач — 41 из них
оставили набор тестов и демонстрационные скрипты в скрытом рабочем каталоге, — а его
единственный настоящий проект («Почтовый центр») терялся посередине. Имя, выведенное из
идеи, тоже читалось как запрос («Хочу простую локальную страницу-визитку…»), а не как
продукт.

**Отклонённые альтернативы.** Фильтровать сам ответ API (клиент больше не смог бы
сказать, сколько он скрыл); стереть записи (корзина уже есть, и ошибка должна
отменяться одним нажатием, поэтому уборка шла через `DELETE /api/projects/{id}` по
одной); угадывать по одному имени (доказательство — папка, имя и число файлов);
приводить всё выведенное имя к нижнему регистру (это уничтожило бы имена вроде
`FastAPI`); нормализовать каждое слово идеи (после предлога грамматика владельца уже
верна: «Лендинг для новогоднего корпоратива»).

**Доказательство.** `tests/test_project_list_filter.py` (7 тестов: факт видимого корня,
вид по умолчанию, переключатель, всегда видимый проект, страница и «Показать ещё»,
вкладка архива; само правило списка исполняется через Node из `ui/src/project-list.js`),
`tests/test_project_visible_root.py` (четыре идеи владельца, повтор ведущих слов, обрезка
по двоеточию и придаточному, английские эквиваленты, предел), `tests/test_project_lifecycle.py`
(аддитивное поле), а также существующие `tests/test_project_delete.py`,
`tests/test_api.py`, `tests/test_owner_acceptance.py` и `tests/test_user_journey.py`.

**Следствия и открытые ограничения.** `demo-projects` на этом хосте входит в
`project_roots_known`, поэтому запись, доставленная туда, считалась видимой, пока уборка
её не перенесла; само правило — «внутри корня, в который это развёртывание доставляло
проекты», а не «внутри корня текущей настройки». Переписывание в именительный работает
только по окончаниям (`-ую/-юю/-у/-ю`) и документированному списку несклоняемых, поэтому
глагол в первом лице, переживший ведущие филлеры, мог бы быть переписан; такие глаголы
для того и внесены в список. `in_visible_root` вычисляется на каждый запрос, поэтому
запись, чья папка исчезла, сообщает `false` и остаётся за переключателем, а не пропадает.

## 2026-10-25 — One change action, a status strip, and provider keys the owner can add

**Decision.** The «Проекты» card offers exactly **one** primary action for a change
request — «Внести изменения в проект» — directly under the textarea. It performs both
requests that used to be two buttons (`POST /api/projects/{id}/versions`, then
`POST /api/tasks/run` with `lifecycle_project_id` and `write_mode: true`), and it prints a
status strip immediately above itself: `Запущено: run-… · <stage>` while the run is
followed through the existing `GET /api/runs/{run_id}`, ending in «Готово: vN записана»
only when the project's own `current_version` moved, otherwise in the backend's sentence
and error text, verbatim. Everything the card used to show below that block — launch,
preview, local run, save facts, facts, specification, artifact table, backend action
vocabulary, technical details — stays available inside one collapsed
«Показать подробности». The visible card is: status block, the change block with its
strip, the project's folder, one compact row of secondary actions (open folder, launch,
verify, archive, restore, delete), and the versions.

Providers and their API keys are added in «Настройки → Провайдеры и ключи». The non-secret
half of an entry (provider id, base URL, default model, secret variable name) is stored in
`.forge/settings.json` under `providers`; the key is written to **Windows Credential
Manager** under `ForgeAI/provider/<SECRET_ENV_NAME>` and never to a file, a log, an event,
a response or the interface. `app/config/secrets.py::SecretStore` reads that vault as its
third source (after the process environment and `.env`), uncached, so a provider added this
way is visible to the *existing* provider layer on the next request — no restart, no second
registry. The routes are `GET/POST /api/settings/providers` and
`DELETE /api/settings/providers/{id}`, loopback-only, and they publish `has_key` (a
boolean) and `key_source` (`environment` / `vault` / empty) and nothing that could
reconstruct a key.

**Why.** Three measured complaints on one card. Two buttons for one action meant the owner
pressed one, then the other, and could not tell whether anything had started; the card was
so tall he had to read technical blocks to use it; and there was no place to add a provider
with a key, because providers came from `.env`, which he is not supposed to edit.

**Rejected alternatives.** A progress bar or a percentage (the record publishes an event
count, not a plan); polling a not-yet-written run (the dispatch route is synchronous, so
the run id exists only when it answers — the strip therefore appears on the click with
«Запускаю», and names the run as soon as the dispatch answers); a second provider registry
(the vault is reached through the one `SecretStore` every provider already uses); storing
the key in `.forge/settings.json` (a JSON file is not a credential store); reporting
«готов» from a probe (that is `GET /api/models`' question — this section answers the key
question, and says «отключён» when the composition does not enable the provider).

**Evidence.** `tests/test_provider_keys.py` (18 tests, in-memory vault: add → ready and the
existing secret path sees the key, delete → not ready, an empty/invalid key refused without
echoing it, the key absent from the settings file and from every payload and log record,
the two writers of the settings file not erasing each other, a non-loopback peer refused),
`tests/test_flow_invariants.py` (one button, the strip, the collapsed details, the single
secondary row), `tests/test_projects_change_run.py`, `tests/test_beta_ui_actions.py`,
`tests/test_secrets.py`, `tests/test_project_delete.py` (the provider DELETE route is the
second DELETE, and it names no project), `tests/test_together_provider.py` (the preset
catalogue is not a routing integration point), plus the CDP evidence in
`.forge/screenshots/one-*.png` against the running application with the offline provider.

**Consequences and open limits.** A run's stage word can only be printed once the run record
exists; the dispatch route is synchronous, so for a long real run the strip shows
«выполняю запуск…» until the answer arrives, and the recorded stage afterwards. A provider
the composition does not enable is reported «отключён» with the honest sentence «выберите
его явно в задаче», not as ready. The base URL and default model of an entry are stored and
shown, but the transport of each provider still owns its endpoint constant; wiring an
arbitrary base URL into a provider's transport is separate work.

## 2026-10-25 — Одно действие изменения, полоса состояния и ключи провайдеров, которые владелец добавляет сам

**Решение.** Карточка «Проекты» предлагает ровно **одно** основное действие для просьбы об
изменении — «Внести изменения в проект» — прямо под полем ввода. Оно выполняет оба запроса,
которые раньше были двумя кнопками (`POST /api/projects/{id}/versions`, затем
`POST /api/tasks/run` с `lifecycle_project_id` и `write_mode: true`), и сразу над собой
печатает полосу состояния: `Запущено: run-… · <этап>` пока запуск отслеживается через
существующий `GET /api/runs/{run_id}`, заканчиваясь «Готово: vN записана» только если
`current_version` проекта действительно вырос, иначе — предложением бэкенда и его текстом
ошибки дословно. Всё, что раньше было ниже этого блока — запуск, предпросмотр, локальный
запуск, факты сохранения, факты, спецификация, таблица файлов, словарь действий бэкенда,
технические детали — остаётся доступным внутри одного свёрнутого «Показать подробности».
Видимая карточка: блок статуса, блок изменения с полосой, папка проекта, одна компактная
строка второстепенных действий (открыть папку, запустить, проверить, архивировать,
восстановить, удалить) и версии.

Провайдеры и их API-ключи добавляются в «Настройки → Провайдеры и ключи». Несекретная
половина записи (идентификатор провайдера, базовый URL, модель по умолчанию, имя переменной
секрета) хранится в `.forge/settings.json` в разделе `providers`; ключ пишется в
**хранилище учётных данных Windows** под именем `ForgeAI/provider/<ИМЯ_ПЕРЕМЕННОЙ_СЕКРЕТА>`
и никогда — в файл, журнал, событие, ответ или интерфейс.
`app/config/secrets.py::SecretStore` читает это хранилище третьим источником (после
окружения процесса и `.env`), без кеша, поэтому добавленный так провайдер виден
*существующему* слою провайдеров на следующем запросе — без перезапуска и без второго
реестра. Маршруты: `GET/POST /api/settings/providers` и
`DELETE /api/settings/providers/{id}`, только с этого компьютера; они публикуют `has_key`
(булево) и `key_source` (`environment` / `vault` / пусто) и ничего, по чему ключ можно
восстановить.

**Почему.** Три измеренные жалобы на одну карточку. Две кнопки для одного действия: владелец
нажимал одну, потом другую и не мог понять, началось ли что-нибудь; карточка была такой
длинной, что для работы её приходилось читать как технический документ; и не было места,
где добавить провайдера с ключом, потому что провайдеры брались из `.env`, который ему
редактировать не следует.

**Отклонённые альтернативы.** Индикатор прогресса или проценты (запись публикует число
событий, а не план); опрос ещё не записанного запуска (маршрут отправки синхронный, поэтому
идентификатор запуска существует только когда он ответил — полоса появляется по нажатию с
«Запускаю» и называет запуск сразу, как только отправка ответила); второй реестр
провайдеров (хранилище достигается через единственный `SecretStore`, которым уже
пользуется каждый провайдер); хранение ключа в `.forge/settings.json` (JSON-файл не является
хранилищем секретов); вывод «готов» из проверки провайдера (это вопрос `GET /api/models`;
этот раздел отвечает на вопрос о ключе и говорит «отключён», когда состав Forge провайдера
не включает).

**Доказательство.** `tests/test_provider_keys.py` (18 тестов, хранилище в памяти: добавили →
«готов», и существующий путь секрета видит ключ; удалили → не готов; пустой или негодный
ключ отклонён без эха значения; ключа нет ни в файле настроек, ни в одном ответе, ни в
записи журнала; два писателя одного файла настроек не стирают друг друга; запрос не с этого
компьютера отклонён), `tests/test_flow_invariants.py` (одна кнопка, полоса, свёрнутые
подробности, одна строка второстепенных действий), `tests/test_projects_change_run.py`,
`tests/test_beta_ui_actions.py`, `tests/test_secrets.py`, `tests/test_project_delete.py`
(DELETE провайдера — второй DELETE на поверхности, и он не называет проект),
`tests/test_together_provider.py` (каталог пресетов не является точкой интеграции
маршрутизации), плюс CDP-доказательство в `.forge/screenshots/one-*.png` на работающем
приложении с офлайн-провайдером.

**Следствия и открытые ограничения.** Слово этапа запуска можно напечатать только когда
запись запуска существует; маршрут отправки синхронный, поэтому для долгого настоящего
запуска полоса показывает «выполняю запуск…» до ответа, а записанный этап — после. Провайдер,
которого состав Forge не включает, показывается как «отключён» с честным предложением
«выберите его явно в задаче», а не как готовый. Базовый URL и модель по умолчанию
записываются и показываются, но транспортом каждого провайдера по-прежнему владеет его
собственная константа адреса; подключение произвольного базового URL к транспорту — отдельная
работа.

## 2026-10-25 — The project port is discovered from its own output, not taken from the manifest

**Decision.** The launch and the run read the port a project serves on from **reality**, in
`app/projects/ports.py`: the address the child's own output announced (the first match wins;
`http://127.0.0.1:<port>`, `Адрес: ...:<port>`, `listening on ...:<port>`,
`Local: http://localhost:<port>`, `Serving on port <port>`, `Uvicorn running on
http://...:<port>`, a bare `:<port>` in a startup banner; ports outside 1024-65535 ignored and
ports named inside a traceback not read at all), then the listening TCP ports owned by the
child pid and its descendants, and finally a real loopback `GET /` on **every** candidate --
any HTTP answer (200, 302, 401, 404, 500) proves a server answers, and a bound socket that
never answers is not ready. The runner publishes `observed_port`, and the preview's process
session carries it too. A child that exits 0 while an address it printed already answers is
an **already-running** instance: the run records `reused: true` and the launch answers
`reused: true` and opens that address, instead of refusing. The manifest keeps its place as a
*claim*: the record's specification is scanned for a declared loopback port, and when it
differs from the observed one the launch `notice` names both («Порт из манифеста 8765,
фактический 8790»).

**Why.** The owner clicked «Запустить приложение» and Forge answered
`409 project_launch_refused` -- «Приложение не стало сервером: порт не открылся. Процесс
завершился с кодом 0» -- while the application was running and answering: its own free-port
logic had found another instance already serving on 8790, printed that address and exited 0
without binding anything itself. The launch path knew only one source of ports (the child's
sockets), so a real, answering server was invisible and the wrong sentence was said. The
manifest's claimed port is what the project *says*; the socket and an HTTP answer are what is
*true*.

**Rejected alternatives.** Trusting the manifest's port (it named 8765, the application chose
8790 -- opening a claim is how a browser window ends up on nothing); trusting a printed address
without an HTTP answer (a crash and a busy-port error both print ports); keeping the socket
scan as the only source (it cannot see a server the child did not bind); treating a printed
address as proof of readiness (a process that prints and dies before accepting a request must
never be called a server); calling exit 0 a failure (the server the owner asked for was up).

**Evidence.** `tests/test_project_launch.py`: `PortDiscoveryTests` (every banner form, the
owner's own two lines, first-match order, out-of-range and traceback ports ignored, the
manifest claim, the real probe against a real loopback server, the pid-socket path with a
fake socket table, a bound socket that never answers, a descendant's socket, the announced
port preferred and still verified, the already-running check reading the child's output),
`test_12_an_already_running_app_is_a_success_with_the_reused_port` (a real child, a real
second server, `200`, `reused: true`, the observed port, the URL answering `200`, and the
notice naming 8765 and the actual port) and
`test_13_an_announced_address_nobody_answers_is_still_a_refusal` (`409` with exit code 0 and
the log tail, no window). The live proof on `proj-fce53337245f` is in
`.forge/screenshots/port-*.png`.

**Consequences and open limits.** One bounded probe (`PROBE_TIMEOUT_SECONDS = 1.0`) runs per
candidate while the supervisor waits, so a server that is slow to answer its very first
request is not mistaken for a dead one; a candidate that never answers costs that second per
probe tick. A printed address that answers but is not the project's (another service that
happens to be named in the output) would be opened, because an HTTP answer there is
indistinguishable from the project's own response -- the notice names the port, and the run
block says `reused: true`, so the payload is honest about it. A reused server is not
registered as a preview session (nothing would ever unregister it), so the card's preview
block says "no preview" while the launch notice and the run block carry the address.

## 2026-10-25 — Порт проекта обнаруживается по его собственному выводу, а не берётся из манифеста

**Решение.** Запуск и прогон читают порт, на котором служит проект, из **реальности** — в
`app/projects/ports.py`: адрес, объявленный самим процессом в его выводе (побеждает первое
совпадение; `http://127.0.0.1:<порт>`, `Адрес: …:<порт>`, `listening on …:<порт>`,
`Local: http://localhost:<порт>`, `Serving on port <порт>`, `Uvicorn running on
http://…:<порт>`, голый `:<порт>` в стартовом баннере; порты вне 1024–65535 игнорируются, а
порты внутри traceback не читаются вовсе), затем TCP-порты, которые слушает pid ребёнка и
его потомки, и наконец настоящий loopback-`GET /` по **каждому** кандидату — любой
HTTP-ответ (200, 302, 401, 404, 500) доказывает, что сервер отвечает, а занятый сокет без
ответа готовым не считается. Раннер публикует `observed_port`, и процессная сессия
предпросмотра несёт его же. Ребёнок, завершившийся с кодом 0, при том что объявленный им
адрес уже отвечает, — **уже запущенный** экземпляр: прогон записывает `reused: true`, запуск
отвечает `reused: true` и открывает этот адрес вместо отказа. Манифест остаётся *заявкой*:
спецификация записи просматривается на объявленный loopback-порт, и при расхождении `notice`
запуска называет оба числа («Порт из манифеста 8765, фактический 8790»).

**Почему.** Владелец нажал «Запустить приложение», а Forge ответил
`409 project_launch_refused` — «Приложение не стало сервером: порт не открылся. Процесс
завершился с кодом 0» — хотя приложение работало и отвечало: его собственная логика
свободного порта нашла уже служащий экземпляр на 8790, напечатала этот адрес и вышла с кодом
0, ничего не заняв. Путь запуска знал только один источник портов (сокеты ребёнка), поэтому
настоящий отвечающий сервер был невидим и была сказана неверная фраза. Порт манифеста — это
то, что проект *говорит*; сокет и HTTP-ответ — то, что *есть*.

**Отклонённые альтернативы.** Доверять порту манифеста (он называл 8765, приложение выбрало
8790 — открытие заявки это и есть способ получить окно браузера в пустоту); доверять
напечатанному адресу без HTTP-ответа (и падение, и ошибка занятого порта печатают порты);
оставить сканирование сокетов единственным источником (оно не видит сервер, который ребёнок
не занимал); считать напечатанный адрес доказательством готовности (процесс, который
печатает и умирает до приёма запроса, нельзя называть сервером); считать код 0 неудачей
(сервер, который просил владелец, работал).

**Доказательство.** `tests/test_project_launch.py`: `PortDiscoveryTests` (каждая форма
баннера, две строки самого владельца, порядок «первое совпадение», игнор портов вне
диапазона и внутри traceback, заявка манифеста, настоящий probe против настоящего
loopback-сервера, путь pid-сокетов на поддельной таблице сокетов, занятый сокет без ответа,
сокет потомка, объявленный порт предпочитается и всё равно проверяется, проверка
«уже запущено» по выводу ребёнка),
`test_12_an_already_running_app_is_a_success_with_the_reused_port` (настоящий ребёнок,
настоящий второй сервер, `200`, `reused: true`, наблюдаемый порт, URL отвечает `200`,
`notice` называет 8765 и фактический порт) и
`test_13_an_announced_address_nobody_answers_is_still_a_refusal` (`409` с кодом выхода 0 и
хвостом журнала, окно не открыто). Живое доказательство на `proj-fce53337245f` —
в `.forge/screenshots/port-*.png`.

**Следствия и открытые ограничения.** Пока супервизор ждёт, на каждого кандидата выполняется
один ограниченный probe (`PROBE_TIMEOUT_SECONDS = 1.0`), поэтому сервер, медленно отвечающий
на самый первый запрос, не принимается за мёртвый; кандидат, который не отвечает никогда,
стоит этой секунды на каждый такт проверки. Напечатанный адрес, который отвечает, но
принадлежит не проекту (другая служба, случайно названная в выводе), будет открыт: HTTP-ответ
там неотличим от ответа самого проекта — `notice` называет порт, а блок прогона говорит
`reused: true`, поэтому payload честен. Переиспользованный сервер не регистрируется как
сессия предпросмотра (снять её было бы некому), поэтому блок предпросмотра в карточке говорит
«предпросмотра нет», а адрес несут `notice` запуска и блок прогона.

## 2026-10-25 — Prose is not a secret, and the desktop folder gets one label grant

**Decision.** (1) The `high_entropy` rule of `app/context/validation.py` applies to **token-shaped values
only**: a candidate is one whitespace-free run of at least 32 characters whose every character is from
`A-Za-z0-9+/=_-`, and it must be machine-made (mixed case, four digits). A sentence can never produce one,
so prose is never a finding — while a real token is still refused, alone or as its own word inside a
sentence, and every prefixed shape keeps its rule. (2) When the probe of the owner's **preferred** project
folder fails with *access denied*, `resolve_writable_root` runs one `icacls "<folder>"
/setintegritylevel (OI)(CI)L` (`shell=False`, no console window, at most once per resolve, never for a
remembered fallback), re-probes, and uses the folder when it now works (`label_granted=True`). A failed
grant keeps the honest fallback and produces an actionable notice: what is wrong, whether the label moved,
the one command the owner can run, and «Сменить папку».

**Why.** Two measured defects from the owner's screenshots. His normal change request («я хочу добавить в
проект еще пароль от почты …») was refused as `security_forbidden_content:secret` with
`rule=high_entropy`: a heuristic meant for generated keys read a Russian sentence, no run reached a
provider, and two versions stayed at zero files. And his projects could not be delivered to
`Desktop\Forge Projects` at all, because the interpreters Forge runs on carry a low mandatory label
(`S-1-16-4096`, no-write-up): a low-integrity process may not create or write in a medium folder, so every
project landed in `.forge/projects-visible`. A fallback is a fact, not a fix, and the owner asked for his
projects on his desktop.

**Rejected alternatives.** Keeping the entropy rule value-wide and only excluding Cyrillic (a Latin sentence
would still be refused, and the rule would still be guessing); refusing only when the value *is* one token
(a real key inside a sentence would pass unwatched); treating every `PermissionError` as a label problem (a
path whose parent is a file has a different reason and keeps the generic sentence); granting the label
silently for a remembered fallback (that is not the owner's choice); granting before probing (a writable
folder must never be relabelled); running `icacls` through a shell (quoting, and a console window on the
owner's screen).

**Evidence.** `tests/test_secret_filter_classification.py` — `OwnersProseIsNotASecretTests` (his verbatim
sentence passes the scanner and never reports `secret`/`high_entropy`; the same sentence plus a fake `sk-`
token is still refused; a real 40-character token is refused alone and inside prose; a path, a long Russian
word and a URL are not tokens) and `ASafeTaskReachesTheProviderTests.test_04` (his sentence reaches the
provider). `tests/test_project_visible_root.py` — `LabelGrantTests` (grant attempted once then the folder is
used with `label_granted=True`; a failed grant keeps the fallback and the actionable notice names the exact
command and «Сменить папку»; a writable root never runs the grant; a non-permission failure is not a label
problem; the settings report publishes the grant).

**Limits.** The grant needs the right to rewrite that folder's label. A Forge that itself runs at low
integrity cannot relabel a medium folder (measured on this host: `icacls` → `Access is denied`), so the
honest outcome there is the fallback plus the command in the notice — the folder becomes usable the moment
that one line runs from a normal (medium-integrity) shell.

## 2026-10-25 — Обычный текст — не секрет, а папка Рабочего стола получает одно понижение метки

**Решение.** (1) Правило `high_entropy` в `app/context/validation.py` применяется **только к токенам**:
кандидат — это один непрерывный (без пробелов) отрезок не короче 32 символов, каждый символ которого из
`A-Za-z0-9+/=_-`, и значение должно быть машинным (смешанный регистр, четыре цифры). Предложение такого
отрезка дать не может, поэтому обычный текст никогда не становится находкой — при этом настоящий токен
по-прежнему отклоняется, и отдельно, и как отдельное слово внутри предложения, а все формы с префиксом
сохраняют своё правило. (2) Когда проба **предпочтительной** папки владельца завершается отказом доступа,
`resolve_writable_root` выполняет одну команду `icacls "<папка>" /setintegritylevel (OI)(CI)L`
(`shell=False`, без окна консоли, не более одного раза за разрешение корня и никогда для запомненного
запасного варианта), пробует заново и использует папку, если запись теперь проходит (`label_granted=True`).
Неудачное понижение оставляет честный запасной вариант и даёт действующее уведомление: что именно не так,
понижена ли метка, одна команда, которую владелец может выполнить, и «Сменить папку».

**Почему.** Два измеренных дефекта из скриншотов владельца. Его обычный запрос на изменение («я хочу
добавить в проект еще пароль от почты …») был отклонён как `security_forbidden_content:secret` с
`rule=high_entropy`: эвристика для сгенерированных ключей прочитала русское предложение, ни один запуск не
дошёл до провайдера, и две версии остались с нулём файлов. А проекты вообще не попадали в
`Desktop\Forge Projects`, потому что интерпретаторы, на которых работает Forge, несут низкую обязательную
метку (`S-1-16-4096`, без права записи вверх): процесс с пониженной целостностью не может ни создать, ни
записать в папку среднего уровня, поэтому каждый проект оказывался в `.forge/projects-visible`. Запасной
вариант — это факт, а не исправление, и владелец просил проекты на Рабочем столе.

**Отклонённые альтернативы.** Оставить правило по всему значению и исключить только кириллицу (латинское
предложение всё равно отклонялось бы, а правило продолжало бы угадывать); отклонять только когда значение
*целиком* один токен (настоящий ключ внутри предложения прошёл бы незамеченным); считать любую
`PermissionError` проблемой метки (у пути, чей родитель — файл, другая причина, и он сохраняет общую
фразу); понижать метку молча для запомненного запасного варианта (это не выбор владельца); понижать метку
до пробы (папку, доступную для записи, нельзя перемаркировать); запускать `icacls` через оболочку
(кавычки и окно консоли на экране владельца).

**Доказательство.** `tests/test_secret_filter_classification.py` — `OwnersProseIsNotASecretTests` (его
дословное предложение проходит сканер и никогда не сообщает `secret`/`high_entropy`; то же предложение с
поддельным токеном `sk-` по-прежнему отклоняется; настоящий токен из 40 символов отклоняется и отдельно, и
внутри текста; путь, длинное русское слово и URL токенами не являются) и
`ASafeTaskReachesTheProviderTests.test_04` (его предложение доходит до провайдера).
`tests/test_project_visible_root.py` — `LabelGrantTests` (понижение выполняется один раз, затем папка
используется с `label_granted=True`; неудачное понижение оставляет запасной вариант, а действующее
уведомление называет точную команду и «Сменить папку»; доступная для записи папка не перемаркируется;
неразрешение-по-правам не считается проблемой метки; отчёт настроек публикует результат понижения).

**Ограничения.** Понижение требует права переписать метку этой папки. Forge, который сам работает с
пониженной целостностью, не может перемаркировать папку среднего уровня (измерено на этом хосте:
`icacls` → `Access is denied`), поэтому честный результат там — запасной вариант плюс команда в
уведомлении: папка становится доступной в тот момент, когда эта одна строка выполняется из обычной
оболочки (средней целостности).

## 2026-10-27 — The model is chosen by task complexity, by default and visibly

**Decision.** A request that names no provider/model is routed by a new pure module,
`app/agent_runtime/routing.py`: `choose_model(task_text, category, *, catalogue, settings,
override=None, write_mode, existing_project)`. It classifies the task into `SIMPLE`,
`MEDIUM` or `COMPLEX` from explicit, testable signals — length (`LONG_TASK_CHARS = 240`),
clause count (`MANY_CLAUSES = 4`, `VERY_MANY_CLAUSES = 8`), the weighted `SCALE_SIGNALS`
table («создай приложение» 4, сервер/база данных/авторизация/интеграция 2 each,
тесты/рефактор/миграция 1 each, capped at `SCALE_SCORE_CAP = 6`, any signal flooring the
task at `MEDIUM`), `code` + write mode (2), an existing project (1), more than
`MANY_FEATURES = 3` requested features (1) and a confirmed specification with
`MANY_SPECIFICATION_FIELDS = 5` filled fields (1) — and maps the level to a tier
(`TIER_FOR_COMPLEXITY`: `SIMPLE → FREE`, `MEDIUM → CHEAP`, `COMPLEX → PAID`). The paid tier
is reachable only for `COMPLEX` work **and** only when `allow_paid_providers` is true;
otherwise the best cheap/free row is chosen and a reason says so. Within a tier the
configured `default_provider` wins when its tier matches, otherwise a provider that needs
**no account** (the offline, deterministic engine) comes first and only then the
highest-quality row (published price, then capability count, then context window): a free
*gateway* route is still a real network call and a rate limit, while `SIMPLE` work that
opens no socket cannot fail, cannot be billed and cannot change model id underneath the
owner. Only providers the catalogue
reports `READY` or `NOT_CHECKED` are routable, so automatic routing cannot pick a disabled
or key-less provider. The decision is applied to a derived `TaskRunRequest`, so provider
selection, write mode and the paid gate all judge the pair that will really run — an
automatic route names its pair before dispatch exactly like a caller's own choice, which
keeps the owner's own "write the files" checkbox working in the default mode instead of
silently disabling it — and the
same decision is published on every response as `model_choice` (provider · model · tier ·
complexity · reasons · estimated_cost). `POST /api/tasks/run` accepts `estimate: true`,
which answers that decision and dispatches nothing — no provider call, no run, no
idempotency claim — and the Кузница launch step defaults to «Авто (по сложности задачи)»
and previews the same decision, debounced, as the task is typed.

**Why.** The owner asked «где мне выбрать модель которая будет работать? … Там у меня даже
нет возможности что-то выбирать.» His own rule was already automatic — free models for
simple work, paid ones for complex work — but nothing implemented or showed it. A hidden
default is not a policy: he could not see what would run, and the form preselected a
provider, which read as "the choice is yours and it is unavailable". Classifying with
explicit rules rather than an LLM keeps the decision free, deterministic, unit-testable, and
explainable in the reasons the response carries.

**Rejected alternatives.** Asking a model to judge complexity (a second call to pay for, and
a decision that cannot be asserted in a test); a per-point scoring model with no floor (a
single «тесты» would have stayed `SIMPLE`); two scale signals meaning `COMPLEX` (a refactor
with tests was classified as a paid project); keeping the manual picker as the default and
merely adding the classifier behind it (that leaves the owner hunting for a dropdown);
routing by provider cost tier alone without a catalogue state filter (it picked providers
with no credentials); letting the preview compute its own answer in TypeScript (a second
source of truth that could disagree with the run).

**Evidence.** `tests/test_model_routing.py` — classification of ten realistic tasks
including the owner's mail-centre idea (`COMPLEX`, reasons naming «создание приложения,
база данных, авторизация, интеграция»), the tier mapping, the paid refusal when not
permitted, the override winning, provider-state filtering, determinism, and the run path
(the decision reaches the provider call site; `estimate: true` calls nothing). The response
key set is pinned deliberately in `tests/test_run_project_id.py`,
`tests/test_product_integration.py` and `tests/test_project_workflow_production.py` with the
new `model_choice` key.

**Limits.** The classifier is keyword- and shape-based, so a task written in unusual words
can be mis-tiered; the reasons name the signals so a wrong call is visible rather than
silent. The paid tier is only reachable when the deployment already permits paid providers —
there is no per-run paid confirmation for an automatic choice (that permission is the
confirmation), while a manual pair keeps its own checkbox. The reason lines are Russian, the
owner's language, and are shown verbatim in both interface languages.

## 2026-10-27 — Модель выбирается по сложности задачи, по умолчанию и на виду

**Решение.** Запрос без указанных провайдера и модели направляется новым чистым модулем
`app/agent_runtime/routing.py`: `choose_model(task_text, category, *, catalogue, settings,
override=None, write_mode, existing_project)`. Он относит задачу к `SIMPLE`, `MEDIUM` или
`COMPLEX` по явным, проверяемым признакам — длина (`LONG_TASK_CHARS = 240`), число частей
(`MANY_CLAUSES = 4`, `VERY_MANY_CLAUSES = 8`), взвешенная таблица `SCALE_SIGNALS` («создай
приложение» 4, сервер/база данных/авторизация/интеграция по 2, тесты/рефактор/миграция по
1, с потолком `SCALE_SCORE_CAP = 6` и полом `MEDIUM` при любом признаке), `code` + запись
файлов (2), существующий проект (1), больше `MANY_FEATURES = 3` запрошенных функций (1) и
подтверждённая спецификация с `MANY_SPECIFICATION_FIELDS = 5` заполненными полями (1) — и
переводит уровень в тариф (`TIER_FOR_COMPLEXITY`: `SIMPLE → FREE`, `MEDIUM → CHEAP`,
`COMPLEX → PAID`). Платный тариф достижим только для `COMPLEX` и только при
`allow_paid_providers = true`; иначе выбирается лучшая дешёвая или бесплатная модель, и
причина это называет. Внутри тарифа настроенный `default_provider` побеждает, если его
тариф совпадает, иначе первым идёт провайдер, которому **не нужен аккаунт** (офлайн-движок,
детерминированный), и только затем лучшая по качеству строка (опубликованная цена, затем
число возможностей, затем окно контекста): бесплатный **шлюзовой** маршрут — это всё ещё
настоящий сетевой вызов и лимит частоты, а `SIMPLE`-работа, не открывающая сокет, не может
ни упасть, ни списаться, ни сменить идентификатор модели под владельцем. Маршрутизируются
только провайдеры в состоянии
`READY` или `NOT_CHECKED`, поэтому автоматический выбор не может взять выключенного
провайдера или провайдера без ключа. Решение применяется к производному
`TaskRunRequest`, поэтому выбор провайдера, режим записи и платный шлюз судят ту пару,
которая действительно выполнится — автоматический маршрут называет пару до отправки так
же, как собственный выбор вызывающего, и это сохраняет его галочку «записать файлы» в
режиме по умолчанию вместо молчаливого отключения, — а само решение публикуется в каждом
ответе как `model_choice` (провайдер · модель · тариф · сложность · причины ·
estimated_cost).
`POST /api/tasks/run` принимает `estimate: true`, который отвечает этим решением и ничего не
запускает — ни вызова провайдера, ни запуска, ни заявки идемпотентности — а шаг запуска
Кузницы по умолчанию стоит на «Авто (по сложности задачи)» и показывает то же решение с
задержкой ввода, пока задача печатается.

**Почему.** Владелец спросил: «где мне выбрать модель которая будет работать? … Там у меня
даже нет возможности что-то выбирать.» Его собственное правило уже было автоматическим —
бесплатные модели для простого, платные для сложного — но ничего его не реализовывало и не
показывало. Скрытое умолчание — не политика: он не видел, что выполнится, а форма
предвыбирала провайдера, что читалось как «выбор за вами, и он недоступен». Классификация
по явным правилам, а не моделью, делает решение бесплатным, детерминированным, проверяемым
тестами и объяснимым — в причинах, которые несёт ответ.

**Отклонённые альтернативы.** Спрашивать модель о сложности (второй платный вызов и
решение, которое нельзя утверждать в тесте); балльная модель без пола (одни «тесты»
остались бы `SIMPLE`); два признака масштаба = `COMPLEX` (рефакторинг с тестами
классифицировался как платный проект); оставить ручной выбор по умолчанию и лишь добавить
классификатор за ним (владелец так и искал бы список); маршрутизация только по тарифу
провайдера без фильтра состояния каталога (выбирались провайдеры без ключей); считать
предпросмотр в TypeScript (второй источник истины, который мог бы расходиться с запуском).

**Доказательство.** `tests/test_model_routing.py` — классификация десяти реалистичных
задач, включая идею центра почты владельца (`COMPLEX`, причины называют «создание
приложения, база данных, авторизация, интеграция»), отображение тарифов, отказ от платного
без разрешения, победа ручного выбора, фильтр состояния провайдеров, детерминизм и путь
запуска (решение доходит до места вызова провайдера; `estimate: true` ничего не вызывает).
Набор ключей ответа намеренно закреплён в `tests/test_run_project_id.py`,
`tests/test_product_integration.py` и `tests/test_project_workflow_production.py` с новым
ключом `model_choice`.

**Ограничения.** Классификатор основан на ключевых словах и форме текста, поэтому необычно
сформулированная задача может попасть в другой уровень; причины называют признаки, поэтому
ошибка видна, а не молчит. Платный тариф достижим только когда развёртывание уже разрешает
платные модели — отдельного подтверждения на каждый автоматический платный запуск нет (это
разрешение и есть подтверждение), а ручная пара сохраняет свою галочку. Строки причин —
русские, на языке владельца, и показываются дословно в обоих языках интерфейса.

## 2026-10-25 — A project is launched as its program, not as its page

**Decision.** `app/projects/entrypoint.py` ranks a runnable entry **above** any static page. The
fixed order is: an explicitly declared entry (`forge.json` at the project root with
`"entry": "<path>"`, or the project specification's `entry`/`entrypoint`, honoured only when it names
a file that exists), then `python_script` (root `app.py` → `main.py` → `server.py`), `python_module`
(a root package with `__main__.py`), `node_script` (root `server.js`/`app.js`), `node_npm` (root
`package.json` with a declared `start`/`dev` script), and only then `static_html` (`index.html`, then
any other root `*.html`). Static is the fallback used when no runnable entry exists, and it now says
so in the card's run-command fact: «статическая страница, сервер не требуется». When the chosen entry
is a process, the launch starts it through the existing runner and opens the URL its own port
discovery observed; the card prints the command (`python app.py`, `npm start`, ...). The entry point
is **derived on every read** from the current version's manifest **plus the file names really at the
project root** (one bounded, symlink-checked listing), so a run that wrote only `index.html` cannot
relabel a project that still has `app.py`. Forge's own deterministic `MOCK_DETERMINISTIC` scaffold
module is recognised by its banner (bounded read of a root `*.py` file) and is deliberately not
treated as a server, so every offline scaffold keeps its working page.

**Why.** This was the single most damaging defect left: the owner's «Запустить приложение» produced
an empty grey window at least four times. `index.html` ranked first, so a project that really had a
server and a leftover/stub page was launched as a static page; Forge served the HTML with no backend
and the owner concluded nothing worked. Changing the priority fixes the class of defect instead of
one project. Re-deriving the entry from the manifest alone was the second half of the same bug: a
version's manifest records what *that run* wrote, so a change run writing one page made the server
invisible; reading the root's real names keeps the program visible while nothing is guessed. The
scaffold carve-out exists because `MOCK_DETERMINISTIC` writes a page *and* a side-effect-free module:
treating that module as a server would start a process that exits at once and take away the page that
does work.

**Rejected alternatives.** Keeping `index.html` first and adding a warning (the owner would still get
the empty window); inferring "is this a server?" from source contents by keyword (a project that
builds its server in a helper module, or uses an unusual framework, would be misclassified -- the
same class of guess that already failed four times); reading `requirements.txt`/`package.json` and
assuming that implies a server (dependencies are not an entry point); storing the entry point on the
version (a stored value goes stale and is exactly what the record-recompute half of this decision
avoids); launching the static page automatically when the process dies (already rejected: the page is
offered as an explicit second action, never opened behind the owner's back); rewriting the mock
scaffold's module name instead of recognising its banner (could not fix projects created before the
fix, which already have the old `app.py`).

**Evidence.** `tests/test_entry_priority.py` (26 tests, offline): every combination
(`app.py`+`index.html`, `main.py`+`index.html`, `server.py` alone, `package.json`+`index.html`,
`index.html` alone, nothing) resolves to the expected kind; the declared override is honoured from
`forge.json` and from the specification and ignored when it names nothing real or escapes the
workspace; a page-only materialisation over a project with a server keeps `python_script`; the card
payload and the presentation's `launch_role`/`launch_path` show the recomputed `app.py`; and the
launch path is proved to call the runner, open the process port, and never call the preview registry.
`tests/test_materialization.py` (entry tests updated to the new ranking), `tests/test_local_execution.py`,
`tests/test_project_launch.py` and `tests/test_project_truth_presentation.py` were kept green. The
live proof launched a real stdlib `http.server` project with a decoy `index.html` through Forge's
`POST /api/projects/{id}/launch`: the URL was the process port, `GET` returned the server marker, the
page's decoy marker never appeared, and the preview registry was not used; a pure static project
launched through the read-only preview. Screenshots and evidence JSON are in
`.forge/screenshots/entry-*.png` and `.forge/entry-live-evidence.json`.

**Consequences and open limits.** The priority is by **name**, so a project whose server is only a
non-standard filename (e.g. `serve.py`) stays a static page unless it declares its entry; a project
named `app.py` that is not a server will now be started and honestly refused when it exits without a
port, with the static page offered as the explicit fallback. The declared entry is a declaration, not
a preference: a value naming no existing file is ignored, and a value that escapes the workspace is
refused. The root listing is capped at 512 entries and the scaffold read at 4 KiB, so an enormous
project root is inspected only partially (never truncated silently into a wrong entry: the manifest
still supplies every recorded path). The root scan reads names only, never bodies, recurses nowhere
and follows no symlink, so a nested page cannot become an entry.

## 2026-10-25 — Проект запускается как программа, а не как страница

**Решение.** `app/projects/entrypoint.py` ставит запускаемую точку входа **выше** любой статической
страницы. Фиксированный порядок: явно объявленная точка входа (`forge.json` в корне проекта с
`"entry": "<путь>"` или `entry`/`entrypoint` из спецификации проекта — учитывается, только если
называет существующий файл), затем `python_script` (корневые `app.py` → `main.py` → `server.py`),
`python_module` (корневой пакет с `__main__.py`), `node_script` (корневые `server.js`/`app.js`),
`node_npm` (корневой `package.json` с объявленным скриптом `start`/`dev`) и только затем
`static_html` (`index.html`, затем любой другой корневой `*.html`). Статика — запасной вариант,
когда запускаемой точки входа нет, и теперь она прямо говорит об этом в факте «команда запуска»:
«статическая страница, сервер не требуется». Когда выбранная точка входа — процесс, запуск
стартует его через существующий раннер и открывает адрес, порт которого тот обнаружил; карточка
печатает команду (`python app.py`, `npm start`, ...). Точка входа **пересчитывается при каждом
чтении** из манифеста текущей версии **плюс имён файлов, реально лежащих в корне проекта** (одно
ограниченное чтение каталога с проверкой симлинков), поэтому запуск, записавший только
`index.html`, не может переименовать проект, в котором всё ещё есть `app.py`. Собственный
детерминированный каркас `MOCK_DETERMINISTIC` распознаётся по баннеру (ограниченное чтение
корневого `*.py`) и намеренно не считается сервером, чтобы каждый офлайн-каркас сохранял рабочую
страницу.

**Почему.** Это был самый разрушительный из оставшихся дефектов: «Запустить приложение» давало
пустое серое окно как минимум четыре раза. `index.html` был первым, поэтому проект, у которого
действительно был сервер и оставшаяся или заглушечная страница, запускался как статическая
страница; Forge отдавал HTML без бэкенда, и владелец делал вывод, что ничего не работает. Смена
приоритета лечит класс дефекта, а не один проект. Повторное определение точки входа только по
манифесту было второй половиной той же ошибки: манифест версии фиксирует то, что записал *этот*
запуск, поэтому запуск изменения, записавший одну страницу, делал сервер невидимым; чтение реальных
имён в корне сохраняет программу видимой и ничего не выдумывает. Исключение для каркаса нужно
потому, что `MOCK_DETERMINISTIC` пишет страницу *и* модуль без побочных эффектов: если считать этот
модуль сервером, запустится процесс, который сразу завершится, и рабочая страница пропадёт.

**Отклонённые альтернативы.** Оставить `index.html` первым и добавить предупреждение (владелец
всё равно получил бы пустое окно); определять «сервер ли это?» по содержимому исходников по
ключевым словам (проект, собирающий сервер в вспомогательном модуле или на необычном фреймворке,
был бы классифицирован неверно — тот же класс догадки, который уже подвёл четыре раза); читать
`requirements.txt`/`package.json` и предполагать, что это подразумевает сервер (зависимости — не
точка входа); хранить точку входа в версии (сохранённое значение устаревает, и именно этого
избегает вторая половина решения — пересчёт записи); автоматически открывать статическую страницу,
когда процесс умер (уже отклонено: страница предлагается как явное второе действие и никогда не
открывается за спиной владельца); переименовать модуль mock-каркаса вместо распознавания баннера
(не исправило бы проекты, созданные до исправления, — в них уже есть старый `app.py`).

**Доказательство.** `tests/test_entry_priority.py` (26 тестов, офлайн): каждая комбинация
(`app.py`+`index.html`, `main.py`+`index.html`, один `server.py`, `package.json`+`index.html`, один
`index.html`, ничего) даёт ожидаемый вид; объявленное переопределение учитывается из `forge.json` и
из спецификации и игнорируется, если называет несуществующий файл или выходит за пределы рабочей
области; материализация только страницы поверх проекта с сервером сохраняет `python_script`;
полезная нагрузка карточки и `launch_role`/`launch_path` презентации показывают пересчитанный
`app.py`; путь запуска доказанно вызывает раннер, открывает порт процесса и ни разу не обращается к
реестру предпросмотра. `tests/test_materialization.py` (тесты точки входа обновлены под новый
порядок), `tests/test_local_execution.py`, `tests/test_project_launch.py` и
`tests/test_project_truth_presentation.py` остались зелёными. Живое доказательство запустило через
`POST /api/projects/{id}/launch` настоящий проект со stdlib `http.server` и обманным `index.html`:
адресом стал порт процесса, `GET` вернул маркер сервера, маркер страницы-обманки не появился, а
реестр предпросмотра не использовался; чистый статический проект открылся через предпросмотр только
для чтения. Скриншоты и JSON доказательств — в `.forge/screenshots/entry-*.png` и
`.forge/entry-live-evidence.json`.

**Ограничения.** Приоритет определяется **именем**, поэтому проект, у которого сервер называется
нестандартно (например, `serve.py`), остаётся статической страницей, пока не объявит точку входа;
проект с именем `app.py`, который не является сервером, теперь будет запущен и честно отклонён,
когда завершится без порта, а статическая страница будет предложена как явный запасной вариант.
Объявленная точка входа — это объявление, а не предпочтение: значение, не называющее существующий
файл, игнорируется, а выходящее за пределы рабочей области отклоняется. Чтение корня ограничено 512
записями, чтение каркаса — 4 КиБ, поэтому очень большой корень проекта просматривается неполно
(никогда не усекаясь молча в неверную точку входа: манифест всё равно даёт все записанные пути).
Сканирование корня читает только имена, никогда тела, не рекурсирует и не следует симлинкам,
поэтому вложенная страница не может стать точкой входа.


## 2026-10-26 — A failed launch is explained and repaired under its own button

**Decision.** A refused launch is explained by one panel
(`ui/src/components/RepairPanel.tsx`) rendered immediately under «Запустить приложение», and
the cause it prints is derived only from the redacted log lines the `409
project_launch_refused` already carries (`ui/src/repair.ts`). Its two correction actions
(«Исправить автоматически» and the one-line chat) go through the *existing* change path
(`POST /api/projects/{id}/versions` + the dispatch), sharing the change block's strip and
AliveBar element, with at most two automatic attempts per project in one session. The
project's own `<project>\launch.log` is read into the same facts and appended on failure, so
a program started from its desktop shortcut fails into the same panel.

**Why.** The owner asked for one place instead of two "change" flows, for the error to be
said in plain words immediately under the button, and for Forge to fix a failing program
itself without him running to the agent each time. Keeping the cause mapping and the attempt
cap in a pure, Node-executable module means the words he reads are the words the test
executes.

**Rejected alternatives.** A second change flow inside the panel (a copy of the dispatch
logic) — it would drift from «Что поправить?»; guessing the port or the module name when the
log does not name one; relaunching after a zero-file run (unchanged code cannot start); an
unbounded number of automatic attempts (a loop that would keep spending provider calls on a
project that needs a decision from the owner).


## 2026-10-26 — Неудачный запуск объясняется и чинится под своей кнопкой

**Решение.** Отказ запуска объясняет одна панель (`ui/src/components/RepairPanel.tsx`),
отрисованная сразу под «Запустить приложение», а причина в ней выводится только из строк
журнала без секретов, которые уже несёт `409 project_launch_refused`
(`ui/src/repair.ts`). Оба её действия починки («Исправить автоматически» и однострочный
чат) идут по **существующему** пути изменения (`POST /api/projects/{id}/versions` + запуск),
разделяя элемент полосы состояния и AliveBar блока изменения, с не более чем двумя
автоматическими попытками на проект за сессию. Собственный `<project>\launch.log` проекта
читается в те же факты и дописывается при ошибке, поэтому программа, запущенная ярлыком с
рабочего стола, падает в ту же панель.

**Почему.** Владелец просил одно место вместо двух «потоков изменения», чтобы ошибка
говорилась простыми словами сразу под кнопкой, а Forge сам чинил падающую программу, без
беготни к агенту каждый раз. Держа отображение причин и предел попыток в чистом модуле,
исполняемом Node, мы гарантируем: слова, которые он читает, — это слова, которые исполняет
тест.

**Отклонённые варианты.** Второй поток изменения внутри панели (копия логики запуска) — он
разошёлся бы с «Что поправить?»; угадывание порта или имени модуля, если журнал их не
называет; перезапуск после запуска с нулём файлов (неизменённый код не запустится);
неограниченное число автоматических попыток (цикл, тратящий вызовы провайдера на проект,
которому нужно решение владельца).


## 2026-10-26 — Один двойной щелчок показывает приложение: свежий профиль окна и проверка, что окно отрисовалось

**Решение.** Лаунчер ярлыка и запуск Forge больше не считают открытие окна успехом по факту
запуска процесса; окно сначала нужно подтвердить.

* `app/projects/app_window.py`: каждый запуск получает **свой** профиль браузера
  (`<repo>/.forge/app-profile/<project-id>/launch-<время>-<pid>-<случайное>`, хранятся последние
  `PROFILE_RETENTION = 2`). Браузер, вышедший раньше `WINDOW_GRACE_SECONDS = 2.0` — с любым кодом,
  включая `0` — окном не считается: тот же браузер повторяется **один раз** с
  `--no-sandbox --test-type` (`NO_SANDBOX_FLAGS`), и только после этого пробуется следующий. Результат
  несёт `sandboxed`/`downgraded`, погибшие планы — в `WindowResult.failures`, а `title`/`pid` называют
  окно, которое реально видел ОС.
* `app/projects/launcher.py` дописывает отказы и точную фразу «песочница недоступна (0x80000003) —
  повтор без песочницы» в собственный `<project>\launch.log` («окно открыто через edge_app после
  отказа: …»): именно этот файл читает следующее нажатие и ярлык проекта. Когда окна не дал ни один
  план, ответ — типизированный `409 project_launch_refused` со списком отказов и хвостом журнала, а не
  заявленное окно.
* `POST/GET /api/settings` и `.forge/settings.json` несут `window_downgrade_allowed` (по умолчанию
  `true`, читается при каждом запуске): «Настройки → Как открываются окна» могут запретить повтор, и
  тогда браузер без песочницы не запускается вовсе.
* `tools/open_mail_ai.py` — один лаунчер для ярлыка: читает `port` из `settings.json`, поднимает
  `pythonw.exe app.py` с `CREATE_NO_WINDOW`, если `/api/health` не отвечает, ждёт health до 25 с,
  проверяет `GET /`, открывает `--app=<url>` в Chrome, затем в Edge и **подтверждает окно** (видимое
  окно этого pid с заголовком не «голый хост»). Не подтвердилось — профиль удаляется и попытка
  повторяется без песочницы браузера (`--no-sandbox --test-type`), и это записывается в журнал;
  последняя попытка — `os.startfile` **и** окно сообщения с адресом. Каждый шаг, каждая командная
  строка и каждая причина отказа пишутся в `<project>\launch.log`.

**Почему.** Владелец двое суток видел серое пустое окно с заголовком `127.0.0.1 /` при отчёте
«Открыто в отдельном окне». Замерено на этой машине: Chromium, запущенный процессом Python, не
инициализирует песочницу рендерера — `chrome.exe` падает с `0x80000003` через ~0.3 с, `msedge.exe`
выживает, но показывает пустую страницу, чей заголовок падает до голого хоста; с `--no-sandbox` тот же
аргументный вектор рисует страницу. Поэтому песочница остаётся включённой на всех первых попытках и
снимается только как последнее средство — и это сказано в журнале.

**Отклонённые альтернативы.** `CREATE_BREAKAWAY_FROM_JOB` и `ShellExecuteExW` из Python — замерено,
окна не дают; `os.startfile` как основной путь — тот самый молчаливый отказ, ради которого всё и
делается; общий профиль на проект «ради запоминания места окна» — дороже надёжности; `--no-sandbox`
по умолчанию для всех запусков — песочницу не сдают без измерения.

**Решено владельцем (2026-10-26).** Разрешение на повтор без песочницы в самом продукте дано, с
оговоркой: первая попытка всегда остаётся с песочницей, повтор — один, и он записан в журнал.
«Настройки → Как открываются окна» оставляют за владельцем право запретить повтор
(`window_downgrade_allowed`); тогда запуск, которому не удалось открыть окно, честно отказывает,
вместо того чтобы запускать браузер без песочницы.