# Forge AI вЂ” Roadmap

> This roadmap describes the evolution of Forge AI from the current orchestration foundation into the full multi-agent project execution system described in [FORGE_VISION.md](FORGE_VISION.md).

The target architecture and constraints for roadmap work are defined in
[`TECHNICAL_SPECIFICATION.md`](TECHNICAL_SPECIFICATION.md).

## Status legend

- **DONE** вЂ” implemented in the repository and verified by tests.
- **IN PROGRESS** вЂ” partially implemented; the known gap is stated explicitly.
- **NEXT** вЂ” the immediate next work item, not yet started.
- **FUTURE** вЂ” planned; not yet implemented.
- **DEFERRED** вЂ” deliberately postponed, with the reason recorded.

The roadmap is capability-based and has no promised release dates. The
**CURRENT/FUTURE** markers used by the phase sections below are historical
labels; where a phase section uses them, `CURRENT` means **DONE** and `FUTURE`
means **FUTURE**. Verified per-area status lives in
[`SOURCE_OF_TRUTH.md`](SOURCE_OF_TRUTH.md).

## Phase 0 вЂ” Foundation

**CURRENT**

- [x] Project scaffold
- [x] Architecture rules
- [x] Runtime and configuration
- [x] Test infrastructure
- [x] Git safety rules

## Phase 1 вЂ” Provider Layer

**CURRENT**

- [x] OpenAI provider
- [x] Anthropic provider
- [x] Gemini provider
- [x] DeepSeek provider
- [x] OpenRouter provider
- [x] Groq provider
- [x] Provider capabilities metadata
- [x] Provider fallback
- [x] SecretStore

Provider adapters share the provider-neutral interface. Credentials are
resolved locally through SecretStore and are not part of provider metadata or
Git history.

## Phase 2 вЂ” Routing

**CURRENT**

- [x] Dispatcher
- [x] Automatic provider routing
- [x] Cost-aware routing
- [x] Deterministic task classification
- [x] Specialized routing by task category

Current task categories:

- `CODE`
- `ANALYSIS`
- `REVIEW`
- `OTHER`

Classification is deterministic and rule-based. A caller-supplied category or
explicit provider selection retains priority. Automatic routing uses declared
capabilities, configuration, and the existing cost-tier policy; it does not
use an LLM classifier or opaque scoring.

## Phase 3 вЂ” Multi-Agent Execution

**CURRENT**

- [x] Primary agent execution
- [x] Independent reviewer agent
- [x] Combined primary + review result
- [x] Safe handling of primary failures
- [x] Safe handling of reviewer failures
- [x] One bounded revision loop (Revision Loop v0.1)
- [x] Limited automatic correction cycle

The reviewer performs one assessment pass and does not modify files. The
reviewer role is independent from the primary role; current configuration may
route both through the same provider and model.

**FUTURE**

- [ ] Review policy engine

The current cycle runs at most once after `CHANGES_REQUESTED`; a second review
that still requests changes ends with `changes_requested`. Review or revision
failures preserve the available primary result without implying approval.

## Phase 4 вЂ” Planning

**CURRENT**

- [x] Deterministic Project Planner v0.1 templates
- [x] Planned tasks with explicit dependencies and initial readiness status

Planner v0.1 supports Telegram bot, web application, data/analysis, and generic
software goals. It creates a static plan only; tasks are not queued, executed,
or automatically updated as work completes.

**FUTURE**

- [ ] Large-task decomposition
- [ ] Task dependency graph
- [ ] Task queue
- [ ] Parallel task execution

### Intended end-to-end flow

```text
USER GOAL
   в†“
PROJECT PLAN
   в†“
TASKS
   в†“
DEPENDENCIES
   в†“
AGENT ASSIGNMENT
   в†“
EXECUTION
   в†“
REVIEW & VERIFICATION
   в†“
USER APPROVAL
   в†“
CONTROLLED CHANGE (GIT / DEPLOY)
```

The complete flow is a future direction. Today, Forge AI can create a static
deterministic plan, or accept an individual task, classify and route it,
execute one primary agent, and run a bounded review/revision workflow. Plan
execution, runtime dependency management, parallel execution, applying project
changes, and deployment are not implemented.
Changes to Git or production remain subject to explicit authorization.

## Phase 5 вЂ” Execution Plane and Authorization

### DONE

- [x] Execution plane contracts (`ProjectExecutionProfile`, `ExecutionRequest`, `ExecutionResult`)
- [x] Execution policy boundary over the resolved intent
- [x] Local execution adapter (ephemeral workspace, scoped environment, `shell=False`, timeouts, output caps, redaction)
- [x] Permission boundary (default-deny, exact canonical executable identity)
- [x] Approval boundary (single-use, bound to the execution intent)
- [x] Canonical path security (`ProgramIdentity` and `WorkspaceRelativePath`)
- [x] Immutable `ExecutionIntent` with a canonical fingerprint and a single construction path
- [x] `AuthorizedExecution` marker required by the local adapter before spawning
- [x] Mandatory explicit `workspace_root` with fail-closed validation
- [x] Capability classification of invocations (recognized wrappers, interpreter eval/module flags, network commands)
- [x] Engineering-run execution integration and the offline integration failure matrix

### IN PROGRESS вЂ” Execution Authorization Contract v0.2

- [ ] Replace the capability recognition list with a fail-closed declared-invocation model, so unrecognized execution behavior requires an explicit capability instead of relying on a list of known wrapper names.
- [ ] Make the coordinator's intent-fingerprint verification unconditional: an approval that carries no fingerprint must not authorize execution.
- [ ] Include the resolved profile environment in the intent body, or demonstrate that it cannot affect execution.
- [ ] Decide program-identity binding: a name resolved at execution time versus a path pinned at approval time (PATH / TOCTOU).
- [ ] Decide whether declared-input content digests are mandatory for invocations that execute workspace content.

Known limits are recorded in [`SOURCE_OF_TRUTH.md`](SOURCE_OF_TRUTH.md) В§4 and in
[`DECISIONS.md`](DECISIONS.md) ("Execution Authorization Contract v0.2").

### NEXT

- [ ] Unify the two path validators (`app/execution/paths.py` and `app/tools/workspace.py`) behind one canonical implementation without weakening the tool boundary.
- [x] Wire production **tool** execution into the API entry point with an explicitly scoped execution root (`RunScope` frozen per API run, operator-declared tool allowlist, service `Workspace` as the explicit root).
- [x] Wire production **host process** execution into the API entry point through `ExecutionCoordinator` / `LocalExecutionAdapter` (operator-declared command allowlist, frozen `RunScope` perimeter, coordinator-minted `AuthorizedExecution`). The production request factory is not yet wired, so host execution stays disabled by default.
- [x] Server-side `ExecutionDeclaration` mechanism: operator-declared, frozen verification executions with a pure `to_execution_request` translator, implemented and tested end-to-end (declaration → `ExecutionRequest` → `RunScope` → `ExecutionCoordinator` → `AuthorizedExecution` → `LocalExecutionAdapter`). Inert in production because no resolver is configured.
- [x] Trusted server-side verification entry point: `ForgeApiService.run_declared_verification(declaration_id, *, purpose_run_id=None)` runs one operator-declared verification execution. Selection input is the declaration id only, resolved against the operator registry; no HTTP endpoint, no `TaskRunRequest` change, and `run_task` stays execution-disabled. `purpose_run_id` is correlation metadata only.
- [x] Production agent loop vertical slice: `AgentHarness` is the canonical production orchestrator (supplied in `RuntimeContext.harness`), `RunExecutor` stays the execution primitive, and `ForgeApiService.run_agent_loop(declaration_id)` runs one context assembly → one decision → one operator-authorized action → one execution result → durable history. Decision is a recommendation, never authority; tool authority is operator-configured; the slice is bounded to one action; acceptance is deferred.
- [ ] Route `POST /api/tasks/run` through the agent loop: **requires a separate decision**, because it changes the API/desktop response contract and the ordinary task path. The loop currently has its own trusted entry point and `run_task` keeps its previous semantics.
- [ ] Multi-step loops, revision, and multi-agent execution in production: `EngineeringRunExecutor`, `RevisionLoopExecutor`, `MultiAgentExecutor`, and `ProviderReviewer` remain test/smoke machinery until a separate decision.
- [x] Production criterion, verification, and acceptance slice: `AcceptanceSpec` declares operator-owned criteria next to the execution declaration, `bind()` freezes them per run into `RunAcceptanceCriteria`, `FrozenCriteriaVerifier` checks workspace facts deterministically, and the existing `AcceptanceGate` decides PASS/FAIL. `ForgeApiService.run_accepted_task(declaration_id)` records the criterion, verification, and acceptance lifecycle in the durable history. `EXECUTION_SUCCESS` is not task acceptance: a successful process with a failing criterion is rejected.
- [ ] GAP-F remaining half — **task-driven** criterion identity: criteria come from operator composition only. Deriving criteria from a `TaskSpecification` or a task request still has no trusted server-side source and needs a separate decision, because a client-supplied criterion would let the client choose what counts as done.
- [ ] Acceptance reporting through the public API contract: `TaskRunResponse.success` still means "the action ran and its verification passed", while the acceptance verdict is recorded as `acceptance_status` in the durable run record. Redefining the public `success` field (or exposing acceptance directly) is a contract decision.
- [ ] `TaskSpecification`/task-driven verification binding: the API path still has no trusted server-side verification identity (no production `VerificationExpectation`, no production `TestVerificationIntent`, and `TaskRunRequest` carries no verification field). A client-controlled selector (`context`, description, `category`, or `task_id`) is explicitly rejected, so this must not be closed by inventing one. `run_task` therefore stays execution-disabled by default.
- [ ] Idempotent side-effect execution boundary for host processes (no automatic retry after a crash between process start and result recording).

### DEFERRED вЂ” Block 2 (workspace and isolation)

- [ ] `WorkspaceIdentity` and workspace identity binding in the intent
- [ ] Declared-input allow-lists and deny-by-default staging
- [ ] Secret, `.env` and VCS exclusion policy for staged workspaces
- [ ] Scoped content digests for referenced inputs
- [ ] Durable or persistent approval store (separate design: standing authorization semantics)
- [ ] OS-level isolation and kernel network enforcement (namespaces, cgroups, Job Objects) вЂ” a separate project, not part of the authorization redesign

## Phase 6 вЂ” Lifecycle, product surface and knowledge

### FUTURE

- [ ] Discovery intelligence, evidence and unknowns ([`TECHNICAL_SPECIFICATION.md`](TECHNICAL_SPECIFICATION.md) В§21)
- [x] Wire production **project discovery** into the agent loop: `ProjectDiscovery` observes the workspace through the existing `BoundedProjectScanner`/`UnderstandingSnapshotter` before the first context assembly, and the immutable snapshot reaches the decision as `PROJECT_UNDERSTANDING` context via the existing `DecisionContextAssembler`. Bounded, per-run, non-authoritative, fail-closed.
- [ ] Directory-depth bound for the project scanner: the existing bounds cover file count, per-file size, and total bytes, but not depth. Adding one changes the scanner's own contract and needs its own decision.
- [ ] Re-observation mid-run and snapshot-consistency mechanism: discovery currently runs once per run and the workspace may change afterwards; locking or re-observation is deferred.
- [x] Wire production **planning** into the agent loop: `Planner.plan_for_run` reuses the existing deterministic templates to produce an immutable, run-bound `ExecutionPlan`, validated server-side before use. A plan is a declarative intention with no command surface - no argv, executable, environment, timeout, or tool grant - and it cannot become an `AuthorizedExecution`. The goal is the operator's `ExecutionDeclaration.intent`; no intent means no plan.
- [ ] Dynamic replanning and plan revision history: planning runs once per run and a plan is immutable. Replanning on new information belongs to a Revision Loop and needs its own decision.
- [ ] Parallel execution and retries of planned steps: the first slice is a deterministic sequential DAG.
- [ ] Project classification and capability selection (В§22)
- [ ] Project Brief, Architecture and Technical Specification generation for target projects
- [ ] Ready-to-run project generation
- [ ] Candidate Forge Knowledge -> evaluation -> Approved Forge Knowledge promotion workflow
- [ ] Forge API, desktop and web clients, setup wizard
- [x] Durable run history / checkpoint persistence (append-only event log + state snapshot, `GET /api/runs/{run_id}`)
- [x] Capability / tool discovery (`GET /api/capabilities`, `GET /api/tools`; one shared `ToolRegistry` for execution and discovery)
- [ ] Host environment discovery (executables, runtimes, git, Docker, shell, filesystem capabilities) — separate architectural stage with its own trust boundary
- [ ] Automatic resume of interrupted runs (deferred pending side-effect idempotency design)
- [ ] Durable, resumable task queues

Technology candidates under evaluation are tracked in
[`TECHNOLOGY_RADAR.md`](TECHNOLOGY_RADAR.md). Nothing there is approved.

---

# Forge AI — Roadmap — русская версия

> Этот roadmap описывает эволюцию Forge AI от текущего фундамента оркестрации к
> полной многоагентной системе выполнения проектов, описанной в
> [FORGE_VISION.md](FORGE_VISION.md).

Целевая архитектура и ограничения для работ по roadmap определены в
[`TECHNICAL_SPECIFICATION.md`](TECHNICAL_SPECIFICATION.md).

## Легенда статусов

- **DONE** — реализовано в репозитории и проверено тестами.
- **IN PROGRESS** — реализовано частично; известный пробел указан явно.
- **NEXT** — ближайший следующий пункт работ, ещё не начатый.
- **FUTURE** — запланировано; не реализовано.
- **DEFERRED** — сознательно отложено с зафиксированной причиной.

Roadmap основан на возможностях и не содержит обещанных дат релизов. Маркеры
**CURRENT/FUTURE**, используемые в разделах фаз ниже, — исторические метки; там,
где раздел фазы использует их, `CURRENT` означает **DONE**, а `FUTURE` означает
**FUTURE**. Проверенный статус по областям находится в
[`SOURCE_OF_TRUTH.md`](SOURCE_OF_TRUTH.md).

## Фаза 0 — Foundation

**CURRENT**

- [x] Каркас проекта
- [x] Правила архитектуры
- [x] Runtime и конфигурация
- [x] Инфраструктура тестов
- [x] Правила безопасности Git

## Фаза 1 — Provider Layer

**CURRENT**

- [x] Провайдер OpenAI
- [x] Провайдер Anthropic
- [x] Провайдер Gemini
- [x] Провайдер DeepSeek
- [x] Провайдер OpenRouter
- [x] Провайдер Groq
- [x] Метаданные capabilities провайдеров
- [x] Fallback провайдеров
- [x] SecretStore

Адаптеры провайдеров используют общий провайдер-нейтральный интерфейс. Учётные
данные разрешаются локально через SecretStore и не входят в метаданные провайдеров
или историю Git.

## Фаза 2 — Routing

**CURRENT**

- [x] Dispatcher
- [x] Автоматическая маршрутизация провайдеров
- [x] Cost-aware маршрутизация
- [x] Детерминированная классификация задач
- [x] Специализированная маршрутизация по категории задачи

Текущие категории задач:

- `CODE`
- `ANALYSIS`
- `REVIEW`
- `OTHER`

Классификация детерминированная и основана на правилах. Категория, заданная
вызывающим, или явный выбор провайдера сохраняют приоритет. Автоматическая
маршрутизация использует объявленные capabilities, конфигурацию и существующую
политику cost-tier; она не использует LLM-классификатор или непрозрачное
оценивание.

## Фаза 3 — Multi-Agent Execution

**CURRENT**

- [x] Выполнение primary-агента
- [x] Независимый reviewer-агент
- [x] Объединённый результат primary + review
- [x] Безопасная обработка сбоев primary
- [x] Безопасная обработка сбоев reviewer
- [x] Один ограниченный цикл ревизии (Revision Loop v0.1)
- [x] Ограниченный цикл автоматической коррекции

Reviewer выполняет один проход оценки и не изменяет файлы. Роль reviewer
независима от роли primary; текущая конфигурация может направлять обе роли через
один и тот же провайдер и модель.

**FUTURE**

- [ ] Движок политики review

Текущий цикл выполняется не более одного раза после `CHANGES_REQUESTED`; второе
review, снова запрашивающее изменения, завершается со статусом
`changes_requested`. Сбои review или ревизии сохраняют доступный результат primary,
не подразумевая одобрения.

## Фаза 4 — Planning

**CURRENT**

- [x] Детерминированные шаблоны Project Planner v0.1
- [x] Запланированные задачи с явными зависимостями и начальным статусом готовности

Planner v0.1 поддерживает Telegram-бота, веб-приложение, задачи данных/анализа и
общие программные цели. Он создаёт только статический план; задачи не ставятся в
очередь, не выполняются и не обновляются автоматически по мере выполнения работы.

**FUTURE**

- [ ] Декомпозиция крупных задач
- [ ] Граф зависимостей задач
- [ ] Очередь задач
- [ ] Параллельное выполнение задач

### Предполагаемый сквозной поток

```text
USER GOAL
   ↓
PROJECT PLAN
   ↓
TASKS
   ↓
DEPENDENCIES
   ↓
AGENT ASSIGNMENT
   ↓
EXECUTION
   ↓
REVIEW & VERIFICATION
   ↓
USER APPROVAL
   ↓
CONTROLLED CHANGE (GIT / DEPLOY)
```

Полный поток — это будущее направление. Сегодня Forge AI может создать
статический детерминированный план либо принять отдельную задачу, классифицировать
и направить её, выполнить одного primary-агента и запустить ограниченный
workflow review/ревизии. Выполнение плана, управление зависимостями во время
выполнения, параллельное выполнение, применение изменений в проекте и деплой не
реализованы. Изменения в Git или production остаются предметом явной авторизации.

## Фаза 5 — Execution Plane и авторизация

### DONE

- [x] Контракты execution plane (`ProjectExecutionProfile`, `ExecutionRequest`, `ExecutionResult`)
- [x] Граница execution policy по разрешённому интенту
- [x] Локальный адаптер выполнения (временный workspace, ограниченное окружение, `shell=False`, таймауты, лимиты вывода, маскирование)
- [x] Граница Permission (default-deny, точный канонический идентификатор исполняемого файла)
- [x] Граница Approval (одноразовая, привязана к интенту выполнения)
- [x] Каноническая безопасность путей (`ProgramIdentity` и `WorkspaceRelativePath`)
- [x] Неизменяемый `ExecutionIntent` с каноническим fingerprint и единственным путём конструирования
- [x] Маркер `AuthorizedExecution`, требуемый локальным адаптером перед запуском процесса
- [x] Обязательный явный `workspace_root` с fail-closed проверкой
- [x] Классификация инвокаций по capabilities (распознанные wrappers, флаги eval/module интерпретаторов, сетевые команды)
- [x] Интеграция выполнения в engineering run и offline-матрица отказов интеграции

### IN PROGRESS — Execution Authorization Contract v0.2

- [ ] Заменить список распознавания capabilities на fail-closed модель объявленных инвокаций, чтобы нераспознанное поведение выполнения требовало явной capability вместо опоры на список известных имён wrapper-программ.
- [ ] Сделать проверку fingerprint интента координатором безусловной: одобрение без fingerprint не должно авторизовать выполнение.
- [ ] Включить разрешённое окружение профиля в тело интента либо доказать, что оно не может влиять на выполнение.
- [ ] Решить вопрос привязки идентичности программы: имя, разрешаемое во время выполнения, или путь, закреплённый при одобрении (PATH / TOCTOU).
- [ ] Решить, обязательны ли digest'ы объявленных входов для инвокаций, исполняющих содержимое workspace.

Известные ограничения зафиксированы в [`SOURCE_OF_TRUTH.md`](SOURCE_OF_TRUTH.md) §4
и в [`DECISIONS.md`](DECISIONS.md) ("Execution Authorization Contract v0.2").

### NEXT

- [ ] Объединить два валидатора путей (`app/execution/paths.py` и `app/tools/workspace.py`) за одной канонической реализацией без ослабления tool-границы.
- [x] Подключить production **tool** execution к API-точке входа с явно ограниченным корнем выполнения (`RunScope`, замораживаемый на каждый API-run, объявленный оператором allowlist tools, `Workspace` сервиса как явный корень).
- [x] Подключить production **host process** execution к API-точке входа через `ExecutionCoordinator` / `LocalExecutionAdapter` (объявленный оператором allowlist команд, замороженный периметр `RunScope`, `AuthorizedExecution` выпускается только координатором). Production-фабрика запросов пока не подключена, поэтому host execution по умолчанию выключен.
- [x] Механизм server-side `ExecutionDeclaration`: объявленные оператором замороженные verification-выполнения с чистым транслятором `to_execution_request`, реализован и протестирован end-to-end (declaration → `ExecutionRequest` → `RunScope` → `ExecutionCoordinator` → `AuthorizedExecution` → `LocalExecutionAdapter`). В production бездействует, поскольку resolver не сконфигурирован.
- [x] Доверенный server-side вход для verification: `ForgeApiService.run_declared_verification(declaration_id, *, purpose_run_id=None)` выполняет одно объявленное оператором verification-выполнение. Единственный вход выбора — declaration id, разрешаемый через операторский реестр; HTTP-endpoint не добавлен, `TaskRunRequest` не изменён, а `run_task` остаётся execution-disabled. `purpose_run_id` — только correlation-метаданные.
- [x] Первый вертикальный срез production agent loop: `AgentHarness` — канонический production-оркестратор (поставляется в `RuntimeContext.harness`), `RunExecutor` остаётся execution-примитивом, а `ForgeApiService.run_agent_loop(declaration_id)` выполняет одну сборку контекста → одно решение → одно авторизованное оператором действие → один результат исполнения → durable history. Decision — только рекомендация, не authority; tool authority задаётся оператором; срез ограничен одним действием; acceptance отложен.
- [ ] Направить `POST /api/tasks/run` через agent loop: **требует отдельного решения**, поскольку это меняет контракт ответа API/desktop и обычный путь задачи. Сейчас у loop собственный доверенный вход, а `run_task` сохраняет прежнюю семантику.
- [ ] Многошаговые loop, ревизии и multi-agent выполнение в production: `EngineeringRunExecutor`, `RevisionLoopExecutor`, `MultiAgentExecutor` и `ProviderReviewer` остаются test/smoke-машинерией до отдельного решения.
- [x] Первый production-срез criterion, verification и acceptance: `AcceptanceSpec` объявляет критерии оператора рядом с execution-объявлением, `bind()` замораживает их per-run в `RunAcceptanceCriteria`, `FrozenCriteriaVerifier` детерминированно проверяет факты workspace, а решение PASS/FAIL выдаёт существующий `AcceptanceGate`. `ForgeApiService.run_accepted_task(declaration_id)` записывает жизненный цикл criterion, verification и acceptance в durable history. `EXECUTION_SUCCESS` — не task acceptance: успешный процесс с непройденным критерием отвергается.
- [ ] Оставшаяся половина GAP-F — **task-driven** criterion identity: критерии приходят только из композиции оператора. Вывод критериев из `TaskSpecification` или запроса задачи по-прежнему не имеет доверенного server-side источника и требует отдельного решения, поскольку клиентский критерий позволил бы клиенту выбирать, что считать выполненным.
- [ ] Отчётность acceptance через публичный контракт API: `TaskRunResponse.success` по-прежнему означает «действие выполнилось, и его verification прошёл», а вердикт acceptance записывается как `acceptance_status` в durable run record. Переопределение публичного поля `success` (или прямой экспорт acceptance) — решение о контракте.
- [ ] Привязка verification к `TaskSpecification`/задаче: в API-пути по-прежнему нет доверенной server-side verification-идентичности (нет production `VerificationExpectation`, нет production `TestVerificationIntent`, а `TaskRunRequest` не несёт verification-поля). Управляемый клиентом селектор (`context`, description, `category` или `task_id`) явно отвергнут, поэтому закрывать пункт выдумыванием такого селектора нельзя. `run_task` поэтому остаётся execution-disabled по умолчанию.
- [ ] Идемпотентная граница side effects для host-процессов (никакого автоматического повтора после краша между запуском процесса и записью результата).

### DEFERRED — Block 2 (workspace и изоляция)

- [ ] `WorkspaceIdentity` и привязка идентичности workspace в интенте
- [ ] Белые списки объявленных входов и deny-by-default подготовка
- [ ] Политика исключения секретов, `.env` и VCS при подготовке workspace
- [ ] Точечные digest'ы содержимого для referenced inputs
- [ ] Долговременное (durable) или постоянное хранилище одобрений (отдельный дизайн: семантика standing-авторизации)
- [ ] Изоляция на уровне ОС и сетевое ограничение на уровне ядра (namespaces, cgroups, Job Objects) — отдельный проект, не часть редизайна авторизации

## Фаза 6 — Жизненный цикл, продуктовые поверхности и знания

### FUTURE

- [ ] Discovery intelligence, свидетельства и неизвестные ([`TECHNICAL_SPECIFICATION.md`](TECHNICAL_SPECIFICATION.md) §21)
- [x] Подключить production **project discovery** к agent loop: `ProjectDiscovery` наблюдает workspace через существующие `BoundedProjectScanner`/`UnderstandingSnapshotter` до первой сборки контекста, и неизменяемый snapshot достигает decision как контекст `PROJECT_UNDERSTANDING` через существующий `DecisionContextAssembler`. Bounded, per-run, без authority, fail-closed.
- [ ] Ограничение глубины каталогов для сканера проекта: существующие границы покрывают число файлов, размер файла и общий объём байт, но не глубину. Его добавление меняет контракт самого сканера и требует отдельного решения.
- [ ] Повторное наблюдение в середине run и механизм согласованности snapshot: сейчас discovery выполняется один раз на run, и workspace может измениться после него; блокировка или повторное наблюдение отложены.
- [x] Подключить production **planning** к agent loop: `Planner.plan_for_run` переиспользует существующие детерминированные шаблоны и создаёт неизменяемый, привязанный к run `ExecutionPlan`, валидируемый server-side до использования. План — декларативное намерение без командной поверхности (ни argv, ни executable, ни окружения, ни timeout, ни выдачи tool), и он не может стать `AuthorizedExecution`. Цель — операторский `ExecutionDeclaration.intent`; нет intent — нет плана.
- [ ] Динамическое перепланирование и история ревизий плана: планирование выполняется один раз на run, и план неизменяем. Перепланирование по новой информации относится к Revision Loop и требует своего решения.
- [ ] Параллельное исполнение и retries шагов плана: первый срез — детерминированный последовательный DAG.
- [ ] Классификация проектов и выбор capabilities (§22)
- [ ] Генерация Project Brief, Architecture и Technical Specification для целевых проектов
- [ ] Генерация готового к запуску проекта
- [ ] Workflow продвижения Candidate Forge Knowledge -> evaluation -> Approved Forge Knowledge
- [ ] Forge API, desktop- и web-клиенты, setup wizard
- [x] Durable run history / checkpoint persistence (append-only event log + state snapshot, `GET /api/runs/{run_id}`)
- [x] Capability / tool discovery (`GET /api/capabilities`, `GET /api/tools`; единый `ToolRegistry` для выполнения и discovery)
- [ ] Host environment discovery (исполняемые файлы, runtime'ы, git, Docker, shell, возможности файловой системы) — отдельный архитектурный этап со своей границей доверия
- [ ] Автоматическое возобновление прерванных запусков (отложено до проектирования идемпотентности side effects)
- [ ] Долговременные возобновляемые очереди задач

Технологические кандидаты на рассмотрении отслеживаются в
[`TECHNOLOGY_RADAR.md`](TECHNOLOGY_RADAR.md). Ничто там не одобрено.
