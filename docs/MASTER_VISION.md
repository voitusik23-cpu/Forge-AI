# Forge AI — Master Vision and Development Direction

This document records the owner's expanded vision, target architecture, engineering invariants, roadmap, and immediate priorities. It defines direction; it does not claim that every capability described here is implemented. For implementation facts, consult [SOURCE_OF_TRUTH.md](SOURCE_OF_TRUTH.md), code, and reproducible test results.

## 1. Product concept

Forge AI is not a collection of separate AI tools. It is one engineering platform intended to evolve into an autonomous software-development environment.

The foundational goals are:

- **Everything works together:** models, agents, tools, projects, memory, execution, verification, and governance form one coherent system.
- **Sequential development:** establish a reliable foundation first, then a complete execution lifecycle, then expand autonomy.
- **One working environment:** desktop and web clients share projects, runs, decisions, evidence, and history. They are two entry points into one product, not separate systems.
- **Maximum useful autonomy:** Forge plans, selects suitable models and tools, executes, verifies, and corrects work within policy, authorization, workspace, and budget limits.

Forge must deliver **verifiable engineering outcomes**, not merely plausible answers. Autonomy must never mean uncontrolled access to the computer, secrets, repositories, or money.

### Intended end-to-end lifecycle

1. Receive a human goal, existing codebase, website, or dataset.
2. Discover the situation and collect evidence; state unknowns explicitly.
3. Produce a project brief, requirements, and acceptance criteria.
4. Record decisions and required approvals.
5. Design architecture and technical specification.
6. Create a plan and coordinate appropriate agents and tools.
7. Implement changes under explicit authorization.
8. Review, test, verify, and correct failures within bounded limits.
9. Deliver a usable result with evidence and an honest account of limitations.
10. Preserve project memory and promote reusable knowledge only after evaluation and review.

This is the target lifecycle, not a claim that the entire cycle exists today.

## 2. Target architecture and boundaries

These are logical layers; they do not require immediate decomposition into separate services.

| Component | Responsibility | Must not |
|---|---|---|
| **Platform** | Users, organizations, projects, access, policies, budgets, product run records, API, UI, shared history | Bypass Core controls or grant itself execution rights |
| **Core** | Execution lifecycle, authorization enforcement, workspace boundaries, runner coordination, cancellation, verification, results | Trust arbitrary permissions or workspace paths from an untrusted caller |
| **Fabric** | Recommend models, tools, routes, and suitable executors | Authorize or execute its own recommendations |
| **Model Registry and adapters** | Model/provider capabilities, compatibility, availability, configuration, cost metadata | Decide whether a task is authorized |
| **Memory and Knowledge Governance** | Project context, history, decisions, evaluated reusable knowledge | Turn old observations or decisions into current permissions automatically |
| **Runner / Execution Plane** | Perform work inside an authorized environment | Have unrestricted access to the host machine |

### Non-negotiable invariants

1. **Recommendation != Authorization != Execution.** AI may recommend; trusted policy authorizes; Core enforces the permission before execution.
2. **A report is not proof.** Success must be supported by run state, diffs, test results, logs, and independently checkable evidence—not merely an agent's assertion.
3. Provider, model, agent, skill, capability, and tool are distinct concepts.
4. Core remains provider-neutral; provider-specific behavior stays behind adapters.
5. Client-supplied paths or modes never grant authority. Trusted server-side project-to-workspace mapping is required.
6. Secrets must not leak into Git, prompts, ordinary context, logs, or evidence artifacts.
7. Timeout, stale heartbeat, and LOST state do not by themselves prove an OS process has stopped. Stop confirmation and recovery are separate concerns.
8. User changes must be preserved. Rollback must not overwrite unrelated pre-existing work.
9. Project memory and reusable Forge-wide knowledge are separate; shared knowledge requires provenance, evaluation, and review.
10. Implementation status is determined by code and reproducible evidence, not plans or confident reports.

## 3. One shared history; explicit execution location

Project history belongs to the product and execution system, not to a particular chat or client.

- A user creates a task in the web or desktop client.
- Platform persists the task and plan and assigns a stable run-record identifier.
- An authorized local or server worker performs the work in its designated workspace.
- Events, logs, changes, checks, and results are recorded against the same project and run history.
- The user can open the project from another device and inspect its current state.

A shared history does **not** mean a local computer can continue working while powered off. That requires an always-available server worker or another reachable executor. Local and remote execution may share a contract, but the UI must show where work runs and whether the executor is available.

## 4. Run state, evidence, and resource accounting

Keep these concepts separate:

- **RunRecord:** Platform's product-level record of a requested task.
- **Core Run:** the actual execution instance linked to the RunRecord.
- **PhysicalTelemetry:** execution events and machine/process measurements.
- **UsageRecord:** measured model and resource usage.
- **Cost:** a calculated estimate or actual provider cost, with its calculation basis.
- **Price, Charge, Ledger:** commercial pricing, charges, and accounting; these are not execution telemetry.

First establish reliable execution and measurement; then build trustworthy billing. Unknown measurements must remain unknown rather than silently becoming zero.

## 5. Stage 1 — harden the foundation (current gate; not accepted yet)

The immediate priority is not to add more autonomous agents. It is to prove that the foundation is safe and predictable. The read-only audit context identifies unresolved issues; Stage 1 must not be accepted until findings are reconciled with the actual Git state and reproducible checks.

### Required evidence

- **Secrets and redaction:** validate secret-file handling and sensitive-value redaction across prompts, logs, diagnostics, and provider adapters. A filename deny-list cannot prove arbitrary file contents are safe.
- **Authorization and boundaries:** verify Core API authentication, CORS assumptions, fail-closed authorization, workspace mapping, and actual workspace enforcement.
- **Process lifecycle:** verify timeout, cancellation, child-process-tree termination, cleanup, exception handling, and platform-specific behavior. Returning a timeout error is insufficient if a process keeps running.
- **Migrations and reproducibility:** inspect migration 0015 and the current schema, test setup/teardown, CI, and database safety.
- **Git/test consistency:** reconcile branch, HEAD, status, diff, untracked files, test counts, baseline failures, and reported results. Preserve local owner changes; do not assume they exist on the remote branch.

### Stage 1 acceptance gate

Do not close the stage until every claimed fix is tied to concrete files and a reviewable diff; critical security checks pass; tests run reproducibly and exact results are recorded; unexplained discrepancies between reports, Git state, and test output are resolved; and remaining risks are classified as fixed now or explicitly deferred with rationale.

Do not expand Stage 2 implementation while foundational authorization, cancellation, or isolation defects remain unresolved.

## 6. Roadmap after Stage 1

### Stage 1 — foundation hardening
Security, authentication, workspace boundaries, process lifecycle, migrations, and reproducible tests.

**Outcome:** an execution foundation suitable for the next stage.

### Stage 2 — first complete execution lifecycle
Connect Platform and Core through an explicit, testable contract. Create a task, enqueue/claim it safely, execute it, persist state and results, collect telemetry and usage, and correctly handle failure, timeout, cancellation, and recovery boundaries.

**Outcome:** one task travels from a user request to a verified result.

### Stage 3 — Fabric and intelligent executor selection
Select models and tools using task requirements, capabilities, quality evidence, availability, constraints, and cost. Record why a route was chosen and what actually happened.

**Outcome:** a reasoned choice of executor instead of a blind default.

### Stage 4 — multi-agent engineering team
Coordinate roles such as planner, researcher, implementer, tester, reviewer, and security specialist. They use shared contracts and project context but retain controlled permissions.

**Outcome:** specialists work as one governed team, not as disconnected chats.

### Stage 5 — project memory and reusable knowledge
Separate project understanding, run history, and general Forge knowledge. Add relevance retrieval, decision records, provenance, freshness checks, and review before broad reuse.

**Outcome:** work continues from maintained understanding rather than starting from zero each session.

### Stage 6 — unified web and desktop environment
Provide shared projects and run history, plans, event streams, diffs, tests, logs, cancellation, and continuation from another device. Make local versus remote execution visible.

**Outcome:** one working environment with multiple access and execution options.

### Stage 7 — governed autonomy
Forge decomposes goals, executes task chains, runs checks, repairs failures, and retries within explicit limits. Dangerous or irreversible actions require appropriate authorization.

**Outcome:** the user sets goals and boundaries without directing every technical step.

Future components may be designed ahead of time, but implementation should start only after the preceding stage is accepted, except for small, justified dependencies.

## 7. Stage 2 — first vertical slice, not a sprawling platform

Start with one fully working task path. Do not begin by building a large queueing system or many services before inspecting the current code and database.

The first contract must cover:

- Platform-to-Core task creation and execution;
- idempotency, including atomic handling of repeated keys and conflict when a key is reused with a different normalized payload;
- durable run state and explicit allowed state transitions;
- execution in a trusted, bounded workspace;
- cancellation, timeout, errors, and cleanup;
- evidence, telemetry, and usage records;
- end-to-end lifecycle tests.

Decisions requiring explicit design and review:

- **Idempotency:** scope the key correctly and compare a normalized request fingerprint; verify database constraints and transaction behavior.
- **Cancellation:** distinguish “cancellation accepted” from “process tree confirmed stopped.” A cancelled label alone is not proof of termination.
- **Recovery:** a local JSONL telemetry journal does not automatically guarantee database recovery after a crash.
- **Worker model:** an in-process worker may suit a first vertical slice, but restart, failure, and concurrency limitations must be documented. Decide after inspecting the existing implementation.

The first successful end-to-end scenario matters more than a large feature count. Design the contract so that a worker or queue can later be replaced without rewriting the whole Platform.

## 8. Autonomy modes and guardrails

| Mode | Intended behavior |
|---|---|
| **Observe** | Read, analyze, and plan without modifying the project |
| **Assist** | Propose changes and wait for approval for actions requiring it |
| **Execute** | Perform authorized actions within workspace and resource limits |
| **Autonomous** | Decompose goals, execute a bounded sequence, verify, and correct failures within granted policy |

A mode never overrides policy or authorization. Each task should define permitted tools and workspace boundaries; maximum duration, iterations, tokens, and cost/resource limits; stop and escalation conditions; acceptance criteria; and actions requiring human approval.

Increase autonomy by measuring verified outcomes, not by simply granting agents broader access.

## 9. Engineering operating loop and durable records

Use a continuous loop:

**Idea/problem → research and evidence → architecture decision → plan and acceptance criteria → implementation → tests and audit → fixes and re-checks → acceptance → update documentation and memory → next task.**

Maintain four durable records with distinct responsibilities:

- **Master Context:** current architecture, components, contracts, and project state.
- **Ideas & Decisions Archive:** ideas, reasoning, accepted decisions, and rejected alternatives.
- **Implementation Roadmap:** stage order, dependencies, acceptance criteria, and status.
- **Engineering Evidence Log:** test and audit results, commits, defects, and evidence of fixes.

These are not competing sources of truth. Code and reproducible checks establish implementation facts; Master Context explains the current design; the archive records decision history; the roadmap directs future work; the evidence log records verification.

## 10. Immediate next actions

1. Obtain the read-only Stage 1 audit and remediation plan from DeepSeek.
2. Compare it with the actual local Git diff, untracked files, remote baseline, and reproducible test results.
3. Accept findings, return items for correction, or explicitly record residual risks.
4. Only after Stage 1 is accepted, run a read-only compatibility audit of the Stage 2 contract and proposed database migration.
5. Approve the Platform-Core contract and state machine before implementing Stage 2.
6. Update Master Context, Roadmap, and Engineering Evidence Log so new chats and agents use the same verified direction.

**Primary recommendation:** do not disperse effort across UI, multi-agent orchestration, and expansive memory yet. First make Forge reliably take one task from request to verified result. That is the foundation for every later form of autonomy.

---

# Forge AI — Главное видение и направление разработки — русская версия

Документ фиксирует расширенное видение владельца, целевую архитектуру, инженерные инварианты, этапы развития и ближайшие приоритеты. Он задаёт направление, но **не утверждает, что все описанные возможности уже реализованы**. Фактический статус определяется по [SOURCE_OF_TRUTH.md](SOURCE_OF_TRUTH.md), коду и воспроизводимым результатам тестов.

## 1. Продуктовая концепция

Forge AI — не набор отдельных ИИ-инструментов, а единая инженерная платформа, которая постепенно превращается в автономную среду разработки.

- **Всё вместе:** модели, агенты, инструменты, проекты, память, выполнение, проверка и управление объединены в одну систему.
- **Последовательное развитие:** сначала надёжный фундамент, затем полный цикл выполнения, после этого — расширение автономности.
- **Единая рабочая среда:** компьютерный и веб-клиенты используют общие проекты, запуски, решения, доказательства и историю. Это две точки входа в один продукт, а не независимые системы.
- **Максимальная полезная автономность:** Forge планирует, выбирает подходящие модели и инструменты, выполняет работу, проверяет результат и исправляет ошибки в рамках политик, полномочий, рабочего окружения и бюджета.

Forge должен выдавать **проверяемый инженерный результат**, а не только убедительный ответ. Автономность не означает бесконтрольный доступ к компьютеру, секретам, репозиториям или деньгам.

### Целевой полный цикл

1. Получить цель человека, существующую кодовую базу, сайт или набор данных.
2. Исследовать ситуацию и собрать доказательства; явно обозначить неизвестное.
3. Подготовить описание проекта, требования и критерии приёмки.
4. Зафиксировать решения и необходимые одобрения.
5. Спроектировать архитектуру и техническую спецификацию.
6. Составить план и координировать подходящих агентов и инструменты.
7. Реализовать изменения с явной авторизацией.
8. Провести review, тесты и проверки; исправлять ошибки в заданных пределах.
9. Передать пригодный к использованию результат с доказательствами и честным описанием ограничений.
10. Сохранить память проекта, а повторно используемые знания публиковать только после оценки и проверки.

Это целевой цикл, а не утверждение, что он уже полностью работает.

## 2. Целевая архитектура и границы ответственности

Это логические уровни, а не требование немедленно разнести код по отдельным сервисам.

| Компонент | Ответственность | Чего не должен делать |
|---|---|---|
| **Platform** | Пользователи, организации, проекты, доступ, политики, бюджеты, продуктовые записи запусков, API, UI и общая история | Обходить ограничения Core или самостоятельно выдавать права на выполнение |
| **Core** | Жизненный цикл исполнения, проверка полномочий, границы workspace, координация runner, отмена, верификация и результаты | Доверять произвольным полномочиям или путям от недоверенного вызывающего кода |
| **Fabric** | Рекомендации по моделям, инструментам, маршрутам и исполнителям | Самостоятельно авторизовать или исполнять свои рекомендации |
| **Model Registry и адаптеры** | Возможности, совместимость, доступность, конфигурация и метаданные стоимости | Решать, разрешено ли выполнение задачи |
| **Memory и Knowledge Governance** | Контекст проекта, история, решения и оценённые повторно используемые знания | Автоматически превращать старые наблюдения или решения в действующие разрешения |
| **Runner / Execution Plane** | Реальное выполнение в разрешённом окружении | Получать неограниченный доступ к машине |

### Неизменяемые инварианты

1. **Рекомендация != Разрешение != Исполнение.** ИИ может рекомендовать; доверенная политика авторизует; Core обеспечивает соблюдение полномочий перед запуском.
2. **Отчёт не равен доказательству.** Успех подтверждается состоянием запуска, diff, тестами, логами и другими независимо проверяемыми данными, а не только заявлением агента.
3. Provider, model, agent, skill, capability и tool — разные понятия.
4. Core остаётся независимым от провайдеров; их особенности находятся за адаптерами.
5. Пути и режимы, переданные клиентом, не расширяют полномочия. Нужна доверенная серверная привязка проекта к workspace.
6. Секреты не должны попадать в Git, промпты, обычный контекст, логи и артефакты доказательств.
7. Тайм-аут, устаревший heartbeat или статус LOST сами по себе не доказывают остановку процесса ОС. Подтверждение остановки и восстановление — отдельные задачи.
8. Пользовательские изменения нужно сохранять. Откат не должен перезаписывать постороннюю работу, существовавшую до запуска.
9. Память проекта и общие знания Forge разделены; повторное использование общих знаний требует происхождения, оценки и проверки.
10. Статус реализации определяется кодом и воспроизводимыми доказательствами, а не планами или уверенными отчётами.

## 3. Единая история и место исполнения

История принадлежит продукту и системе выполнения, а не конкретному чату или клиенту.

- Пользователь создаёт задачу в веб- или компьютерном клиенте.
- Platform сохраняет задачу и план, присваивая устойчивый идентификатор записи запуска.
- Разрешённый локальный или серверный worker выполняет работу в назначенном окружении.
- События, логи, изменения, проверки и результаты записываются в историю того же проекта и запуска.
- Пользователь открывает проект с другого устройства и видит актуальное состояние.

Общая история **не означает**, что локальный компьютер продолжит работу, когда он выключен. Для этого нужен постоянно доступный серверный worker или другой доступный исполнитель. Локальный и удалённый режимы могут использовать общий контракт, но интерфейс обязан показывать, где выполняется задача и доступен ли исполнитель.

## 4. Состояние запуска, доказательства и учёт ресурсов

Сущности необходимо разделять:

- **RunRecord:** продуктовая запись о задаче на уровне Platform.
- **Core Run:** реальный экземпляр выполнения, связанный с RunRecord.
- **PhysicalTelemetry:** события исполнения и измерения процессов/машины.
- **UsageRecord:** измеренное использование моделей и ресурсов.
- **Cost:** рассчитанная оценка или фактическая стоимость провайдера с указанием основания расчёта.
- **Price, Charge, Ledger:** коммерческие цены, списания и бухгалтерский учёт; это не то же самое, что телеметрия исполнения.

Сначала Forge должен обеспечить надёжное выполнение и измерения, а затем — достоверный биллинг. Неизвестные значения должны оставаться неизвестными, а не незаметно превращаться в ноль.

## 5. Stage 1 — укрепление фундамента (текущий этап; ещё не принят)

Главная задача сейчас — не добавлять автономных агентов, а доказать безопасность и предсказуемость фундамента. Пока выводы read-only аудита не сверены с фактическим состоянием Git и воспроизводимыми проверками, Stage 1 нельзя считать принятым.

### Что необходимо подтвердить

- **Секреты и маскирование:** обработку секретных файлов и маскирование чувствительных значений в промптах, логах, диагностике и адаптерах провайдеров. Один deny-list имён файлов не доказывает безопасность произвольного содержимого.
- **Авторизация и границы:** аутентификацию Core API, предположения о CORS, fail-closed авторизацию, привязку workspace и реальное соблюдение границ.
- **Жизненный цикл процессов:** тайм-ауты, отмену, завершение дерева дочерних процессов, очистку, обработку исключений и поведение на поддерживаемых платформах. Недостаточно вернуть ошибку тайм-аута, если процесс продолжает работать.
- **Миграции и воспроизводимость:** миграцию 0015, текущую схему, настройку и очистку тестов, CI и безопасность работы с БД.
- **Согласованность Git и тестов:** ветку, HEAD, status, diff, untracked files, количество тестов, исходные ошибки и результаты. Локальные изменения владельца необходимо сохранять и не считать отправленными в удалённую ветку без проверки.

### Критерии приёмки Stage 1

Не закрывать этап, пока каждое заявленное исправление не связано с конкретными файлами и проверяемым diff; критические проверки безопасности не проходят успешно; тесты не запускаются воспроизводимо с зафиксированными результатами; расхождения между отчётом, Git-состоянием и тестами не объяснены; оставшиеся риски не классифицированы как исправляемые сейчас или осознанно переносимые с обоснованием.

Не расширять реализацию Stage 2, пока остаются нерешённые дефекты авторизации, отмены или изоляции.

## 6. Дорожная карта после Stage 1

### Stage 1 — укрепление фундамента
Безопасность, аутентификация, границы workspace, жизненный цикл процессов, миграции и воспроизводимые тесты.

**Результат:** исполнительный фундамент, пригодный для следующего этапа.

### Stage 2 — первый полный цикл исполнения
Связать Platform и Core явным проверяемым контрактом. Создавать задачу, безопасно помещать её в очередь и забирать в работу, выполнять, сохранять состояние и результат, собирать телеметрию и использование, корректно обрабатывать сбой, тайм-аут, отмену и восстановление.

**Результат:** задача проходит путь от запроса пользователя до проверенного результата.

### Stage 3 — Fabric и интеллектуальный выбор исполнителя
Выбирать модели и инструменты по требованиям задачи, возможностям, доказательствам качества, доступности, ограничениям и стоимости. Сохранять причину выбора и фактический результат.

**Результат:** обоснованный выбор исполнителя вместо слепого вызова модели по умолчанию.

### Stage 4 — многоагентная инженерная команда
Согласовать роли планировщика, исследователя, разработчика, тестировщика, ревьюера и специалиста по безопасности. Они используют общие контракты и контекст проекта, но сохраняют контролируемые полномочия.

**Результат:** специалисты работают как одна управляемая команда, а не как независимые чаты.

### Stage 5 — память проекта и повторно используемые знания
Разделить понимание проекта, историю запусков и общие знания Forge. Добавить поиск контекста, записи решений, происхождение знаний, проверки актуальности и review перед широким повторным использованием.

**Результат:** работа продолжается на основе поддерживаемого понимания, а не начинается с нуля в каждой сессии.

### Stage 6 — единая веб-среда и компьютерный клиент
Общие проекты и история запусков, планы, поток событий, diff, тесты, логи, отмена и продолжение работы с другого устройства. Явно показывать локальное или удалённое исполнение.

**Результат:** единая рабочая среда с разными способами доступа и исполнения.

### Stage 7 — управляемая автономность
Forge самостоятельно декомпозирует цели, выполняет цепочки задач, запускает проверки, исправляет ошибки и повторяет работу в установленных пределах. Опасные или необратимые действия требуют соответствующего разрешения.

**Результат:** пользователь задаёт цели и ограничения, не управляя каждым техническим шагом.

Будущие компоненты можно проектировать заранее, но реализацию следует начинать после приёмки предыдущего этапа, кроме небольших обоснованных зависимостей.

## 7. Stage 2 — первый вертикальный срез, а не огромная платформа

Сначала нужно реализовать один полностью работающий путь задачи. Не следует начинать с масштабной системы очередей и множества сервисов, пока не изучены текущий код и база данных.

Первый контракт должен включать:

- создание и выполнение задачи через Platform ↔ Core;
- идемпотентность: атомарную обработку повторных ключей и конфликт при повторном использовании ключа с другим нормализованным запросом;
- долговечное состояние запуска и явные допустимые переходы статусов;
- выполнение в доверенном ограниченном workspace;
- отмену, тайм-аут, ошибки и очистку;
- доказательства, телеметрию и записи об использовании;
- сквозные тесты жизненного цикла.

Решения, требующие явного проектирования и проверки:

- **Идемпотентность:** правильно определить область действия ключа и сравнивать fingerprint нормализованного запроса; проверить ограничения БД и транзакционное поведение.
- **Отмена:** различать «запрос на отмену принят» и «остановка дерева процессов подтверждена». Статус cancelled сам по себе не доказывает остановку.
- **Восстановление:** локальный JSONL-журнал телеметрии автоматически не гарантирует восстановление БД после падения.
- **Модель worker:** внутрипроцессный worker может подойти для первого вертикального среза, но ограничения при перезапуске, отказах и параллельном выполнении должны быть описаны. Решение принимать после изучения текущей реализации.

Первый успешный сквозной сценарий важнее количества функций. Контракт нужно спроектировать так, чтобы позднее можно было заменить worker или очередь без переписывания всей Platform.

## 8. Режимы автономности и ограничения

| Режим | Предполагаемое поведение |
|---|---|
| **Observe** | Читает, анализирует и планирует без изменения проекта |
| **Assist** | Предлагает изменения и ждёт одобрения действий, для которых оно требуется |
| **Execute** | Выполняет разрешённые действия в пределах workspace и ресурсных лимитов |
| **Autonomous** | Декомпозирует цели, выполняет ограниченную последовательность, проверяет и исправляет ошибки в рамках выданной политики |

Режим не отменяет политику и авторизацию. Для каждой задачи задаются разрешённые инструменты и границы workspace; максимальное время, число итераций, токенов и лимиты стоимости/ресурсов; условия остановки и эскалации; критерии приёмки; действия, требующие подтверждения человека.

Увеличивать автономность нужно по измеряемым проверенным результатам, а не простым расширением доступа агентов.

## 9. Инженерный цикл и долговременные документы

Постоянный цикл работы:

**Идея/проблема → исследование и доказательства → архитектурное решение → план и критерии приёмки → реализация → тесты и аудит → исправление и повторная проверка → приёмка → обновление документации и памяти → следующая задача.**

Поддерживать четыре долговременные записи:

- **Master Context:** актуальная архитектура, компоненты, контракты и состояние проекта.
- **Ideas & Decisions Archive:** идеи, аргументы, принятые решения и отклонённые альтернативы.
- **Implementation Roadmap:** этапы, зависимости, критерии приёмки и статус.
- **Engineering Evidence Log:** результаты тестов и аудитов, коммиты, дефекты и доказательства исправлений.

Это не конкурирующие источники истины. Код и воспроизводимые проверки устанавливают факты реализации; Master Context объясняет действующий дизайн; архив хранит историю решений; Roadmap направляет будущую работу; Evidence Log фиксирует проверку.

## 10. Что делать прямо сейчас

1. Получить от DeepSeek read-only аудит и план исправлений Stage 1.
2. Сверить его с фактическим локальным Git diff, untracked files, удалённым baseline и воспроизводимыми результатами тестов.
3. Принять выводы, вернуть отдельные пункты на исправление либо явно зафиксировать остаточные риски.
4. Только после приёмки Stage 1 провести read-only аудит совместимости контракта Stage 2 и предлагаемой миграции БД.
5. Утвердить контракт Platform ↔ Core и конечный автомат состояний до реализации Stage 2.
6. Обновить Master Context, Roadmap и Engineering Evidence Log, чтобы новые чаты и агенты работали в одном проверенном направлении.

**Главная рекомендация:** пока не распыляться на UI, многоагентность и масштабную память. Сначала добиться, чтобы Forge надёжно проводил одну задачу от запроса до проверенного результата. Это фундамент всей будущей автономности.
