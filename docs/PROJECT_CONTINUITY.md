# Forge AI — Project Continuity

**Purpose:** Preserve owner-approved product intent, architecture direction, stage gates, and next actions across chat sessions and agent handoffs.

This is a handoff index, not a replacement for the repository's sources of truth:
- Verified implementation status: [SOURCE_OF_TRUTH.md](SOURCE_OF_TRUTH.md)
- Target architecture: [TECHNICAL_SPECIFICATION.md](TECHNICAL_SPECIFICATION.md)
- Current architecture: [ARCHITECTURE.md](ARCHITECTURE.md)
- Durable decisions: [DECISIONS.md](DECISIONS.md)
- Roadmap: [ROADMAP.md](ROADMAP.md)
- Product vision: [FORGE_VISION.md](FORGE_VISION.md)

Last updated: 2026-10-09.

## 1. Owner's product vision

- **One integrated system:** models, agents, tools, projects, execution, verification, memory, and governance evolve as one coherent product.
- **Sequential development:** build on verified foundations; do not skip stage gates or expand scope before the previous stage is accepted.
- **One working environment:** computer/desktop and web interfaces should share project identity, run state, decisions, evidence, and history.
- **Maximum useful autonomy within policy and budget:** Forge should plan, execute, verify, recover, and iterate within explicit permissions and limits. Autonomy must never grant itself authority.

The long-term product is an AI Engineering Factory that carries work from a goal and discovery through specification, planning, controlled implementation, review, verification, and governed project knowledge. This is a target, not a claim that the whole lifecycle is implemented.

## 2. Non-negotiable architectural invariants

1. **Recommendation != Authorization != Execution.** A model, planner, Fabric, or agent cannot grant execution authority.
2. Provider, model, agent, skill, capability, and tool are distinct concepts.
3. Core remains provider-neutral; provider-specific details stay behind adapters.
4. Trusted server-side policy determines permissions. Client-supplied paths, modes, budgets, or claims cannot expand authority.
5. Workspace boundaries must be enforced in code and, where the threat model requires it, by OS-level isolation—not by prompts alone.
6. Secrets must not enter Git, ordinary model context, prompts, logs, or evidence artifacts.
7. A textual agent summary is not proof of success. Results require inspectable evidence and explicit verification status.
8. A timeout or LOST status is not proof that an OS process has stopped. Process termination, run state, and recovery are separate concerns.
9. Product run records, Core execution runs, physical telemetry, measured usage, calculated cost, and commercial billing/ledger concepts remain distinct.
10. Implementation status cannot be upgraded without code evidence and reproducible tests.
11. Preserve owner changes. Never reset, clean, overwrite, or reformat unrelated working-tree changes.
12. A new stage starts only after its entry criteria and required owner decisions are explicit.

## 3. Target architecture

- **Platform:** users, organizations, projects, access control, policy/budget context, product-facing run records, APIs, UI, and shared history.
- **Core:** execution coordination, lifecycle/state transitions, trusted authorization, workspace enforcement, runner integration, cancellation, verification, and result production.
- **Fabric:** recommends suitable models, tools, and execution routes; it does not authorize or execute them.
- **Model Registry and provider adapters:** describe provider/model capabilities, availability, configuration, and cost metadata.
- **Memory and Knowledge Governance:** separate project-specific history from reviewed reusable knowledge. Observed knowledge is not automatically promoted to global truth.
- **Execution workers:** run work in bounded local or server-side environments. The UI must make execution location and availability clear.

Desktop and web are two clients of shared project/run services, not separate sources of truth. A local worker cannot continue when its host is off; server-side continuity requires an available server worker/service.

## 4. Stage 1 — current gate: NOT ACCEPTED

The 2026-10-09 DeepSeek read-only re-audit reported the local working-tree HEAD as d1c007aab6c5a4f3fe82b2c2c43f24b226604ff0 and made no edits during that audit. It also reported local modifications and untracked files. This does **not** prove those working-tree changes are present on GitHub master. Reconcile the actual machine's Git status/diff with the remote branch before implementation. Do not overwrite owner changes.

The audit confirmed specific scenarios, not universal security:
- selected secret-file names/extensions and directories are excluded from staging;
- environment values are redacted in the checked prompt paths and logs;
- timeout/process-tree behavior was tested for selected Windows scenarios;
- workspace-root mismatch is denied before scratch creation;
- CORS and loopback defaults were checked.

Unresolved blockers from the audit:
1. **Core API has no authentication on the 10 examined routes.** Loopback binding and CORS are not authentication.
2. **Cancellation/process safety:** a PID-reuse risk was reported. Cancellation and actual process death must be handled separately. Windows evidence does not establish POSIX behavior.
3. **Secret staging has known bypasses**, including .git-credentials, .envrc, GCP/Firebase credential filenames, secret YAML/JSON names, Terraform state, and compound/normalized extensions. A deny-list cannot prove arbitrary file contents contain no secrets.
4. **Two tests remain non-deterministic/unexplained** on both the modified tree and baseline: tests/test_api.py::test_07_task_run_via_api and tests/test_desktop.py::test_04_home_view_task_dispatch. The audit observed the mock provider missing from the reported execution path; root cause remains unresolved.
5. Prompt-redaction tests do not cover every provider adapter; a source-introspection test should be replaced with behavioral coverage.
6. Some process-tree depths/platform paths and explicit WorkspaceManager combinations remain untested.

The audit corrected its counts: 8 new files, not 6; 57 test methods in five new test files; 1999 declared methods in the baseline HEAD. These are reported counts, not a substitute for rerunning the current suite.

### Stage 1 acceptance gate

Do not accept Stage 1 until:
- current local Git state and owner changes are reconciled and preserved;
- the owner-approved Core API access model is implemented and tested;
- cancellation and process-tree termination have a safe, platform-scoped design and evidence;
- known secret-staging bypasses are addressed or explicitly risk-accepted, with limitations documented;
- the mock-provider path is understood and deterministic without paid/network calls;
- behavioral redaction coverage includes relevant provider adapters;
- migration/test-environment claims are verified without unsafe changes to the user's database;
- the required regression suite is reproducible and remaining limitations are recorded.

## 5. Stage 2 — target contracts, not approved for implementation yet

### Platform ↔ Core run contract

- RunRecord is the Platform/product record; Core Run is the execution instance, linked by run_record_id and core_run_id.
- POST /v1/runs should create or replay an idempotent request; polling can precede webhook delivery.
- Reuse of an idempotency key with the same normalized request returns the existing run. Reuse with a different fingerprint returns a conflict. Key scope must match the actual organization/project model, and uniqueness must be atomic.
- Core resolves a project to a trusted workspace; it must not trust an arbitrary caller-supplied workspace_root as authority.
- Define one FSM for queued, acquired, preparing, executing, validating, cancelling, terminal, and lost/recovery states. Exact names/transitions must be reconciled with existing code.
- A stale heartbeat signals suspected loss, not confirmed process death. Recovery must avoid two active workers for one run.
- Cancellation response distinguishes “request accepted” from “process tree confirmed stopped”.
- Time, iterations, tokens, cost, and process/resource limits must be enforced during execution, not only schema-validated.
- Evidence includes Git/worktree snapshots, patch or artifact references, verification commands and exit codes, test results when known, timestamps, and explicit completeness/unknown states.
- Telemetry, usage, calculated cost, price/charge, and accounting ledger remain separate.

### Execution evidence contract

A proposed ExecutionArtifact links Platform and Core run IDs and contains terminal state, failure reason, evidence completeness, Git/worktree state, patch digest or artifact reference, verification results, and available telemetry. Unknown metrics must be represented as unknown—not fabricated as zero. Large patches/logs may be stored as controlled artifacts with hashes and access checks.

### PostgreSQL migration proposal

The proposed migration 0016_stage2_execution_contract.sql with run_records, core_runs, execution_evidence, and execution_telemetry is **a proposal, not an approved migration**. Before writing/running it, inspect migration 0015, existing Platform models/repositories, the actual PostgreSQL schema, and current harness/telemetry contracts.

Review points:
- CHECK constraints alone do not enforce all legal status transitions.
- Watchdog logic must handle NULL/stale heartbeats for every relevant state and must not equate a stale heartbeat with a safe retry.
- OS process IDs alone are not durable ownership handles and can be reused.
- Token-total semantics must match provider definitions; cached tokens may already be included in prompt tokens.
- ON DELETE CASCADE may erase audit evidence and needs an explicit retention/deletion policy.
- Evidence may be unavailable after a crash before verification; represent missing/partial evidence explicitly.
- CREATE TABLE IF NOT EXISTS does not validate or reconcile an existing schema.
- Idempotency requires atomic uniqueness plus comparison of the normalized payload fingerprint, not only a hash column.

## 6. Future capabilities — do not pull these into Stage 1

- Human-in-the-loop approvals for dangerous actions, showing exact commands/diffs and durable approval events.
- Inline correction of a single plan step without losing run history.
- Checkpoints and deterministic rollback that preserve pre-existing owner changes.
- Watchdog/heartbeat and recovery with leases/fencing or equivalent single-owner protection.
- OS-level sandboxing based on an explicit threat model.
- Shared desktop/web history and remote worker continuity.
- Multi-agent roles and durable project memory after reliable execution and evidence contracts exist.

## 7. Agreed order of work

1. Finish the Stage 1 read-only remediation plan.
2. Reconcile it with actual local Git diff and remote baseline; preserve owner changes.
3. Get owner decisions on unresolved security/architecture questions before code changes.
4. Implement Stage 1 fixes as small, independently testable packages; review diffs and evidence after each.
5. Accept Stage 1 only against the gate in §4.
6. Audit Stage 2 schemas and proposed DDL against the current implementation in read-only mode.
7. Approve the Platform ↔ Core contract and FSM before implementing migration 0016.
8. Build one vertical slice: idempotent create → queue/claim → execute → verify → persist result/evidence → poll → cancel.
9. Add Fabric/routing, multi-agent orchestration, durable memory, unified clients, and greater autonomy only as subsequent verified capabilities.

## 8. Handoff checklist for every new chat/agent

1. Read this file, then SOURCE_OF_TRUTH.md, TECHNICAL_SPECIFICATION.md, DECISIONS.md, and ROADMAP.md.
2. Inspect actual Git branch, HEAD, status, diff, and untracked files. Never assume local changes from a previous chat were pushed.
3. Separate implemented/verified, partial, proposed, and owner-decision-required.
4. State the current stage and its acceptance gate before proposing implementation.
5. Default to read-only planning when the task is ambiguous. Do not implement Stage 2 before Stage 1 is accepted.
6. Never commit, push, reset, clean, or overwrite owner changes without explicit authorization for that specific action.
7. When decisions change, update durable docs; tie implementation claims to evidence and a commit.

---

# Forge AI — Непрерывность проекта — русская версия

**Назначение:** сохранять утверждённое продуктовое видение, архитектурное направление, контрольные критерии этапов и следующие шаги при переходе между чатами и агентами.

Это документ передачи контекста, а не замена источников истины репозитория:
- Проверенный статус реализации: [SOURCE_OF_TRUTH.md](SOURCE_OF_TRUTH.md)
- Целевая архитектура: [TECHNICAL_SPECIFICATION.md](TECHNICAL_SPECIFICATION.md)
- Текущая архитектура: [ARCHITECTURE.md](ARCHITECTURE.md)
- Устойчивые решения: [DECISIONS.md](DECISIONS.md)
- План развития: [ROADMAP.md](ROADMAP.md)
- Продуктовое видение: [FORGE_VISION.md](FORGE_VISION.md)

Последнее обновление: 2026-10-09.

## 1. Продуктовое видение владельца

- **Всё в единой системе:** модели, агенты, инструменты, проекты, исполнение, проверка, память и управление развиваются как единый продукт.
- **Последовательное развитие:** опираться на проверенный фундамент; не пропускать контрольные этапы и не расширять область работ до приёмки предыдущего этапа.
- **Единая рабочая среда:** компьютерный/desktop-клиент и веб-интерфейс со временем должны использовать общие проекты, состояния запусков, решения, доказательства и историю.
- **Максимальная полезная автономность в пределах политик и бюджета:** Forge должен планировать, выполнять, проверять, восстанавливаться и повторять действия в рамках явных полномочий и лимитов. Автономность не должна сама выдавать себе полномочия.

Долгосрочная цель — AI Engineering Factory, проводящая работу от цели и исследования через спецификацию, планирование, контролируемую реализацию, ревью и проверку к управляемым знаниям проекта. Это целевое направление, а не утверждение, что весь цикл уже реализован.

## 2. Неизменяемые архитектурные инварианты

1. **Recommendation != Authorization != Execution.** Модель, планировщик, Fabric или агент не могут самостоятельно разрешать исполнение.
2. Provider, model, agent, skill, capability и tool — разные понятия.
3. Core остаётся независимым от провайдеров; их особенности находятся за адаптерами.
4. Полномочия определяет доверенная серверная политика. Пути, режимы, бюджеты и заявления клиента не расширяют права.
5. Границы workspace обеспечиваются кодом и, когда этого требует модель угроз, изоляцией ОС, а не только промптами.
6. Секреты не должны попадать в Git, обычный контекст модели, промпты, логи и артефакты доказательств.
7. Текстовое резюме агента не является доказательством успеха. Нужны проверяемые данные и явный статус проверки.
8. Тайм-аут или статус LOST не доказывает, что процесс ОС остановлен. Завершение процесса, состояние запуска и восстановление — разные вопросы.
9. Продуктовые записи, запуски Core, физическая телеметрия, измеренное использование, расчётная стоимость и коммерческий биллинг/ledger остаются разными сущностями.
10. Статус реализации нельзя повышать без доказательств из кода и воспроизводимых тестов.
11. Пользовательские изменения необходимо сохранять. Нельзя сбрасывать, очищать, перезаписывать или форматировать не относящиеся к задаче изменения рабочего дерева.
12. Новый этап начинается только после явного определения входных критериев и решений владельца.

## 3. Целевая архитектура

- **Platform:** пользователи, организации, проекты, доступ, контекст политик/бюджетов, продуктовые записи запусков, API, UI и общая история.
- **Core:** координация исполнения, жизненный цикл и переходы состояний, авторизация, защита workspace, runner, отмена, проверка и формирование результата.
- **Fabric:** рекомендует модели, инструменты и маршруты выполнения, но не авторизует и не исполняет их.
- **Model Registry и адаптеры:** описывают возможности, доступность, конфигурацию и метаданные стоимости.
- **Memory и Knowledge Governance:** отделяют историю проекта от повторно используемых проверенных знаний. Наблюдение агента не становится глобальной истиной автоматически.
- **Исполнители:** выполняют работу в ограниченной локальной или серверной среде. Интерфейс показывает, где выполняется задача и доступен ли исполнитель.

Desktop и web — два клиента общих сервисов проектов и запусков, а не независимые источники истины. Локальный исполнитель не продолжит работу при выключенном компьютере; для этого нужен доступный серверный worker/service.

## 4. Stage 1 — текущий контрольный этап: НЕ ПРИНЯТ

В read-only аудите DeepSeek от 2026-10-09 указан локальный HEAD d1c007aab6c5a4f3fe82b2c2c43f24b226604ff0; во время самого аудита файлы не менялись. Отчёт также описывает локальные изменения и новые файлы. Это **не доказывает, что изменения присутствуют в GitHub master**. Перед реализацией сопоставить фактические локальные git status/diff с удалённой веткой. Не перезаписывать изменения владельца.

Аудит подтвердил отдельные сценарии, но не универсальную безопасность:
- исключение выбранных имён/расширений секретных файлов и каталогов при staging;
- маскирование значений окружения в проверенных путях промптов и логов;
- тайм-аут и завершение отдельных проверенных сценариев дерева процессов на Windows;
- отказ при несовпадении workspace root до создания scratch;
- проверки CORS и loopback по умолчанию.

Незакрытые блокеры:
1. **У Core API нет аутентификации на 10 проверенных маршрутах.** Loopback и CORS не являются аутентификацией.
2. **Отмена и безопасность процессов:** сообщено о риске повторного использования PID. Отмену нужно отделить от подтверждения фактической остановки. Проверки Windows не подтверждают поведение POSIX.
3. **В фильтрации секретов известны обходы**, включая .git-credentials, .envrc, имена GCP/Firebase credentials, имена секретных YAML/JSON, Terraform state и составные/нормализованные расширения. Deny-list не доказывает отсутствие секретов в произвольном содержимом файлов.
4. **Два теста остаются недетерминированными или необъяснёнными** и на изменённом дереве, и на baseline: tests/test_api.py::test_07_task_run_via_api и tests/test_desktop.py::test_04_home_view_task_dispatch. В диагностике не фигурирует mock provider; причина не установлена.
5. Тесты маскирования не покрывают все адаптеры; тест на основе чтения исходника нужно заменить поведенческой проверкой.
6. Некоторые глубины дерева процессов, платформенные пути и комбинации явного WorkspaceManager не проверены.

DeepSeek исправил подсчёты: 8 новых файлов, а не 6; 57 тестовых методов в пяти новых тестовых файлах; 1999 объявленных методов в baseline HEAD. Это числа из аудита, а не замена повторному запуску тестов.

### Критерии приёмки Stage 1

Не принимать Stage 1, пока:
- локальное Git-состояние и изменения владельца не сверены и не сохранены;
- утверждённая владельцем модель доступа Core API не реализована и не проверена;
- безопасная отмена и завершение дерева процессов не имеют доказательств для заявленных платформ;
- известные обходы фильтрации секретов устранены либо явно приняты как риск с описанными ограничениями;
- путь mock-провайдера понятен, тесты детерминированы и не требуют платных/сетевых вызовов;
- поведенческие тесты маскирования покрывают нужные адаптеры;
- миграции и тестовое окружение проверены без небезопасного изменения пользовательской БД;
- регрессия воспроизводима, остаточные ограничения записаны.

## 5. Stage 2 — целевые контракты, пока не утверждённые для реализации

### Контракт запуска Platform ↔ Core

- RunRecord — продуктовая запись Platform; Core Run — экземпляр исполнения, связанный через run_record_id и core_run_id.
- POST /v1/runs создаёт запуск или возвращает существующий при идемпотентном повторе; polling можно реализовать раньше вебхуков.
- Повтор ключа с тем же нормализованным запросом возвращает существующий запуск. Повтор с другим fingerprint возвращает конфликт. Область ключа должна соответствовать модели организаций/проектов, уникальность должна быть атомарной.
- Core сопоставляет проект с доверенным workspace и не доверяет произвольному workspace_root от клиента.
- Нужен единый FSM: queued, acquired, preparing, executing, validating, cancelling, terminal и lost/recovery. Точные имена и переходы сверить с кодом.
- Устаревший heartbeat означает подозрение на потерю связи, а не подтверждённую смерть процесса. Восстановление не должно допускать двух активных исполнителей одного запуска.
- Ответ на отмену различает «запрос принят» и «остановка дерева процессов подтверждена».
- Лимиты времени, итераций, токенов, стоимости и ресурсов обеспечиваются во время исполнения, а не только проверяются в JSON.
- Доказательства включают Git/worktree snapshots, patch или ссылку на артефакт, команды проверки и exit codes, известные результаты тестов, временные метки и явную полноту.
- Телеметрия, использование, расчётная стоимость, цена/списание и бухгалтерский ledger остаются раздельными сущностями.

### Контракт доказательств исполнения

Предлагаемый ExecutionArtifact связывает идентификаторы Platform и Core и содержит терминальный статус, причину ошибки, полноту доказательств, Git/worktree state, patch digest или ссылку на артефакт, результаты проверок и доступную телеметрию. Неизвестные метрики должны быть неизвестными, а не заменяться нулями. Большие diff/логи можно хранить как контролируемые артефакты с хэшами и проверкой доступа.

### Предложение по PostgreSQL

Предложенная миграция 0016_stage2_execution_contract.sql с run_records, core_runs, execution_evidence и execution_telemetry — **предложение, а не утверждённая миграция**. Перед написанием/запуском изучить миграцию 0015, текущие Platform-модели/репозитории, реальную схему PostgreSQL, контракты harness и телеметрии.

Что проверить:
- CHECK сам по себе не обеспечивает все допустимые переходы статусов.
- Watchdog должен учитывать NULL/устаревший heartbeat на релевантных стадиях и не считать его доказательством безопасного повтора.
- PID ОС не является долговечным идентификатором владения и может быть переиспользован.
- Семантика общего числа токенов должна соответствовать провайдеру; cached tokens могут уже входить в prompt tokens.
- ON DELETE CASCADE может удалить аудит и доказательства — нужна явная политика хранения/удаления.
- При падении до проверки доказательства могут отсутствовать; это нужно отражать явно.
- CREATE TABLE IF NOT EXISTS не проверяет совместимость существующей схемы.
- Идемпотентность требует атомарной уникальности и сравнения fingerprint нормализованной нагрузки, а не только колонки с хэшем.

## 6. Будущие возможности — не добавлять в Stage 1

- Human-in-the-loop для опасных действий с точной командой/diff и долговечным событием одобрения.
- Inline-корректировка одного шага плана без потери истории.
- Checkpoints и детерминированный rollback с сохранением исходных пользовательских изменений.
- Watchdog/heartbeat и восстановление с lease/fencing или эквивалентной защитой единственного владельца.
- Изоляция на уровне ОС по явной модели угроз.
- Общая история desktop/web и продолжение через удалённый worker.
- Многоагентные роли и долговременная память проекта — после надёжного исполнения и контракта доказательств.

## 7. Согласованный порядок работ

1. Завершить read-only план исправлений Stage 1.
2. Сверить его с локальным Git diff и удалённым baseline; сохранить изменения владельца.
3. До изменения кода получить решения владельца по нерешённым вопросам безопасности/архитектуры.
4. Исправлять Stage 1 небольшими независимо тестируемыми пакетами; после каждого проверять diff и доказательства.
5. Принимать Stage 1 только по критериям §4.
6. Провести read-only аудит схем Stage 2 и DDL на совместимость с текущей реализацией.
7. Утвердить контракт Platform ↔ Core и FSM до реализации миграции 0016.
8. Реализовать один сквозной сценарий: идемпотентное создание → очередь/claim → исполнение → проверка → сохранение результата/доказательств → polling → отмена.
9. Fabric/маршрутизацию, многоагентность, долговременную память, единые клиенты и рост автономности добавлять только как последующие проверенные возможности.

## 8. Чек-лист для каждого нового чата/агента

1. Прочитать этот файл, затем SOURCE_OF_TRUTH.md, TECHNICAL_SPECIFICATION.md, DECISIONS.md и ROADMAP.md.
2. Проверить фактические Git branch, HEAD, status, diff и untracked files. Не предполагать, что локальные изменения прошлого чата были отправлены в GitHub.
3. Разделять: реализовано/проверено, частично, предложено, требуется решение владельца.
4. До предложения реализации указывать текущий этап и критерии приёмки.
5. При неоднозначности начинать с read-only планирования. Не реализовывать Stage 2 до приёмки Stage 1.
6. Не выполнять commit, push, reset, clean и не перезаписывать пользовательские изменения без явного разрешения на конкретное действие.
7. При изменении решений обновлять долговременную документацию; заявления о реализации связывать с доказательствами и коммитом.
