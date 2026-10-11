# Forge AI — Component Registry

> **Status: current-state index.** This file answers one question: for each top-level
> package and each notable mechanism, is it **actually used**, partially wired, unused,
> planned, or not reliably determined? It is an index into the code, not a specification.
>
> **Authority.** For behaviour the code and the passing test suite are authoritative; for
> target architecture, `TECHNICAL_SPECIFICATION.md`; for the verified feature matrix,
> `SOURCE_OF_TRUTH.md`. If this file disagrees with the code, the code wins and this file
> is wrong.
>
> **Method.** Reachability was determined by import analysis from the real entry points
> (`app/main.py`, `app/api/main.py`, `app/api/server.py`, `app/runtime/bootstrap.py`) plus
> targeted reading. A package is **not** marked UNUSED merely because no module imports it
> by name: dynamic registration, composition-time injection and entry-point discovery are
> all counted. Where a package is reached only from tests, that is stated as its own
> status rather than being reported as used.

## Status vocabulary

| Status | Meaning | What it obligates |
| --- | --- | --- |
| **ACTIVE** | Reached from a real entry point and exercised in the running system. | Nothing; changes must keep it working. |
| **PARTIAL** | Reached and working in some paths, absent or inert in others. | The gap is named in the row. |
| **UNUSED** | No working use path was found. | A decision: wire it, or stop paying for it. **Nothing is deleted in this pass.** |
| **PLANNED** | Designed or specified, not implemented. | Must never be described in the present tense. |
| **NEEDS_REVIEW** | Purpose or state could not be established with confidence. | A bounded investigation. |

## 1. Top-level packages

| Package | Status | Reached from an entry point | Evidence / gap |
| --- | --- | --- | --- |
| `app/orchestrator/` | **PARTIAL** | Yes | Dispatcher, classification, `run.py`, `trace.py`, `executor.py` are live. `engineering.py` (a second ~760-line run loop), `revision.py`, `multi_agent.py`, `review.py` are imported by tests only. |
| `app/agent_runtime/` | **ACTIVE** | Yes | `harness.py` is the canonical production loop; `idempotency*.py` wired into three trusted entry points; `physical_telemetry.py` writes durable measurements. `frozen_verifier.py` has no construction site in `app/`. |
| `app/execution/` | **ACTIVE** | Yes | Authorization chain, intent, approval, policy, workspace boundary and adapter, all live. New in this pass: `secret_files.py`. |
| `app/runtime/` | **ACTIVE** | Yes | `bootstrap.py` composes the runtime; `run_scope.py` is the frozen perimeter. |
| `app/tools/` | **ACTIVE** | Yes | One `ToolRegistry` serves execution and discovery; `approval.py` is shared by `ToolExecutor` and `ExecutionCoordinator`. `project_snapshots.py` and `test_verification.py` are test-reachable only. |
| `app/context/` | **PARTIAL** | Yes | **Two live assemblers**: the legacy `ContextAssembler` (used by `RunExecutor`) and `DecisionContextAssembler` (used by `AgentHarness`). `ContextItem` still accepts two naming schemes. |
| `app/understanding/` | **ACTIVE** | Yes | `BoundedProjectScanner` and the snapshotter run per-run through `project_discovery.py`. |
| `app/agents/` | **PARTIAL** | Yes | Providers and capabilities live. `reviewer.py` and `mock_agent.py` are not reached from a production entry point. |
| `app/decision/` | **PARTIAL** | Yes | The deterministic rule engine is composed at bootstrap; `ai_provider.py` is constructed in tests only. |
| `app/planning/` | **ACTIVE** | Yes | Deterministic templates produce an immutable, run-bound plan. |
| `app/tasks/`, `app/projects/` | **ACTIVE** | Yes | Specification boundary and derived project state are used by the production paths. |
| `app/memory/` | **UNUSED** | No | Real store, models and validator, and 23 passing tests, but the only injection point is `harness.py` and no production composition supplies it. The single test tying it to the loop asserts only `assertIsNotNone`. |
| `app/knowledge/` | **UNUSED** | No | ~1 108 lines with **zero importers** outside the package; `extract_candidate_from_memory` has no caller in `app/` or in tests. The candidate → approved path does not exist. |
| `app/fabric/` | **PARTIAL** | Yes, discovery **and** run accounting | `list_capabilities()` serves `GET /api/capabilities`, and `record_usage()` **does** fire: `ForgeApiService` creates the fabric first and `bind_fabric_to_runtime` attaches that exact object to the dispatcher, so a dispatched run records its attempts and `get_all_run_accounting()` returns them (measured: 1 record, 4 attempts for one `code` task, and `GET /api/dashboard` sees the run). `resolve()` (routing) still needs the fabric to be consulted during candidate selection, which the dispatcher does only when it is given one — which it now is. |
| `app/skills/` | **UNUSED** | No | `SkillRegistry` is never constructed in `app/`; `builtin.py` is called only by tests; the evaluator is built at `harness.py` but nothing populates `available_skills`. |
| `app/dashboard/` | **PARTIAL** | Yes | Real aggregation behind `GET /api/dashboard`, over real records. `today_totals` is still the same object as `all_time_totals`: the service does not distinguish a time window. |
| `app/api/` | **ACTIVE** | Yes | Service layer drives the real executor and harness. New in this pass: a launcher (`main.py`) and an explicit CORS/bind policy. |
| `app/desktop/` | **UNUSED** | No | Real tkinter shell, but no `__main__` and no launcher; referenced only by `desktop/__init__.py` and `tests/test_desktop.py`. Contains a setup-wizard button labelled "(Placeholder)" that does nothing. |
| `app/platform/` | **UNUSED** | No | Stage 1 domain, schema, persistence and the AS-1/AS-2 application slice are implemented and tested against live PostgreSQL, but **no entry point reaches the package** and there is no Platform → Core transport. |
| `app/reviews/`, `app/utils/` | **UNUSED** | No | Package markers only; `app/reviews/__init__.py` is a single docstring line. |
| `app/config/` | **ACTIVE** | Yes | `SecretStore`, settings and provider-account configuration. |

## 2. Notable mechanisms and their single owner

Recorded because a second implementation of any of these is an architecture defect, not a
style choice.

| Mechanism | Single owner today | Duplicate or competing implementation |
| --- | --- | --- |
| Execution authority | `app/execution/authorizer.py` (one production `AuthorizedExecution.create` call site) | None |
| Approval (intent-bound, single-use) | `app/tools/approval.py`, shared by the tool and host paths | None |
| Capability catalogues | `app/agents/providers/capabilities.py` + `app/tools/registry.py` | `app/fabric/`, `app/skills/`, `ModelRegistry` — `dispatcher.decide` consults four mechanisms, and which one wins depends on what the composition populated; `app/skills/` is still unpopulated |
| Path containment | `app/execution/sandbox.py` (`WorkspaceBoundary`) | Also implemented in `app/tools/workspace.py` and `app/execution/workspace_manager.py`. A shared secret-file recognizer was introduced in this pass; the path resolvers remain separate. |
| Context assembly | `app/context/assembler.py` | Two live classes in one module |
| Run loop | `app/agent_runtime/harness.py` | `app/orchestrator/engineering.py` (test-only) |
| Durable run history | `app/runtime/run_store.py` | Idempotency ledger and telemetry are separate stores by design; fabric accounting is a fourth store, now populated by dispatch (see §1) |
| Workspace snapshots | `app/understanding/snapshotter.py` | `app/tools/project_snapshots.py` (reachable only via `revision.py`) |
| Secret-file recognition | `app/execution/secret_files.py` (introduced in this pass) | None — the scanner delegates to it, so staging and indexing cannot disagree |

## 3. Cleanup candidates

Listed for a later decision. **No component is deleted in this pass.**

1. `app/knowledge/` — zero consumers; freeze until Project Memory is wired.
2. `app/orchestrator/engineering.py`, `revision.py`, `multi_agent.py`, `review.py` — reached only from tests.
3. `app/context/` — retire the legacy assembler and the dual `ContextItem` naming scheme.
4. `app/fabric/resolve()` and `ModelRegistry` — decide which catalogue is authoritative before extending any of them.
5. `app/memory/` — wire it into the harness or remove it; then decide about `app/knowledge/`.
6. `app/skills/` — connect it to the harness or mark it PLANNED.
7. The two duplicate path resolvers (`app/tools/workspace.py`, `app/execution/workspace_manager.py`).
8. `dashboard` `today_totals` — it is the same object as `all_time_totals` and reports a value that never narrows to a window; either implement the window or remove the field. (Fabric run accounting is no longer a candidate: it is populated.)
9. `app/desktop/` — add a launcher and finish or remove the placeholder wizard.
10. `_allowed_execution_commands` in the API service — a retained parameter that no longer grants anything, by the comment's own admission.

## 4. What this registry does not claim

- It does not claim a package is dead because a static import is absent. Composition-time
  injection is how several of these are meant to be reached, and that is why `PARTIAL`
  (reached in some paths) is used rather than `UNUSED` wherever tests demonstrate a working
  path.
- It does not rank importance. `app/platform/` being UNUSED says nothing about its value;
  it says no entry point reaches it yet, which is a wiring gap rather than a design defect.
- It does not merge Project Understanding, Project Memory and Forge Knowledge. The three
  remain separate concepts, and the distinction is asserted in `SOURCE_OF_TRUTH.md` §3.5.

---

# Forge AI — Реестр компонентов — русская версия

> **Статус: индекс текущего состояния.** Файл отвечает на один вопрос: для каждого
> пакета верхнего уровня — он **реально используется**, подключён частично, не
> используется, запланирован или достоверно не установлен? Это индекс по коду, а не
> спецификация.
>
> **Источник истины.** За поведение отвечают код и проходящий набор тестов; за целевую
> архитектуру — `TECHNICAL_SPECIFICATION.md`; за проверенную матрицу возможностей —
> `SOURCE_OF_TRUTH.md`. При расхождении побеждает код, а этот файл неверен.
>
> **Метод.** Достижимость определена анализом импортов от реальных точек входа
> (`app/main.py`, `app/api/main.py`, `app/api/server.py`, `app/runtime/bootstrap.py`)
> вместе с целевым чтением кода. Пакет **не** помечается UNUSED только из-за отсутствия
> прямого импорта: динамическая регистрация, инъекция на этапе композиции и обнаружение
> через entry points учитываются. Если пакет достижим только из тестов, это указывается
> отдельно, а не выдаётся за использование.

## Словарь статусов

| Статус | Значение | Что обязывает |
| --- | --- | --- |
| **ACTIVE** | Достижим из реальной точки входа и работает в системе. | Ничего; изменения не должны ломать его. |
| **PARTIAL** | Достижим и работает на части путей, на других отсутствует или инертен. | Пробел назван в строке. |
| **UNUSED** | Рабочего пути использования не найдено. | Решение: подключить или перестать платить за него. **В этом проходе ничего не удаляется.** |
| **PLANNED** | Спроектирован или описан, не реализован. | Никогда не описывать в настоящем времени. |
| **NEEDS_REVIEW** | Назначение или состояние не удалось установить достоверно. | Ограниченное исследование. |

## 1. Пакеты верхнего уровня

| Пакет | Статус | Достижим из точки входа | Доказательство / пробел |
| --- | --- | --- | --- |
| `app/orchestrator/` | **PARTIAL** | Да | Dispatcher, классификация, `run.py`, `trace.py`, `executor.py` работают. `engineering.py` (второй цикл исполнения, ~760 строк), `revision.py`, `multi_agent.py`, `review.py` импортируются только тестами. |
| `app/agent_runtime/` | **ACTIVE** | Да | `harness.py` — канонический продуктивный цикл; `idempotency*.py` подключены к трём доверенным точкам входа; `physical_telemetry.py` пишет durable-измерения. У `frozen_verifier.py` нет точки конструирования в `app/`. |
| `app/execution/` | **ACTIVE** | Да | Цепочка авторизации, intent, approval, policy, граница workspace и адаптер — всё работает. Новое в этом проходе: `secret_files.py`. |
| `app/runtime/` | **ACTIVE** | Да | `bootstrap.py` собирает runtime; `run_scope.py` — замороженный периметр. |
| `app/tools/` | **ACTIVE** | Да | Один `ToolRegistry` обслуживает исполнение и discovery; `approval.py` общий для `ToolExecutor` и `ExecutionCoordinator`. `project_snapshots.py` и `test_verification.py` достижимы только из тестов. |
| `app/context/` | **PARTIAL** | Да | **Два живых сборщика**: legacy `ContextAssembler` (использует `RunExecutor`) и `DecisionContextAssembler` (использует `AgentHarness`). `ContextItem` по-прежнему принимает две схемы именования. |
| `app/understanding/` | **ACTIVE** | Да | `BoundedProjectScanner` и snapshotter работают на каждый запуск через `project_discovery.py`. |
| `app/agents/` | **PARTIAL** | Да | Провайдеры и capabilities работают. `reviewer.py` и `mock_agent.py` не достижимы из продуктивной точки входа. |
| `app/decision/` | **PARTIAL** | Да | Детерминированный rule engine собран в bootstrap; `ai_provider.py` конструируется только в тестах. |
| `app/planning/` | **ACTIVE** | Да | Детерминированные шаблоны дают неизменяемый план, привязанный к запуску. |
| `app/tasks/`, `app/projects/` | **ACTIVE** | Да | Граница спецификации и производное состояние проекта используются продуктивными путями. |
| `app/memory/` | **UNUSED** | Нет | Реальный store, модели и validator, 23 проходящих теста, но единственная точка инъекции — `harness.py`, и ни одна продуктивная композиция её не заполняет. Единственный тест, связывающий память с циклом, проверяет лишь `assertIsNotNone`. |
| `app/knowledge/` | **UNUSED** | Нет | ~1 108 строк с **нулём импортёров** вне пакета; у `extract_candidate_from_memory` нет вызывающих ни в `app/`, ни в тестах. Путь candidate → approved не существует. |
| `app/fabric/` | **PARTIAL** | Да, discovery **и** run accounting | `list_capabilities()` обслуживает `GET /api/capabilities`, и `record_usage()` **действительно срабатывает**: `ForgeApiService` создаёт fabric первым, а `bind_fabric_to_runtime` привязывает именно этот объект к dispatcher'у, поэтому выполненный запуск записывает свои попытки, а `get_all_run_accounting()` их возвращает (измерено: 1 запись и 4 попытки для одной задачи `code`, и `GET /api/dashboard` видит запуск). `resolve()` (маршрутизация) по-прежнему требует, чтобы dispatcher обращался к fabric при выборе кандидатов — теперь он его получает. |
| `app/skills/` | **UNUSED** | Нет | `SkillRegistry` не конструируется в `app/`; `builtin.py` вызывается только тестами; evaluator создаётся в `harness.py`, но `available_skills` ничем не заполняется. |
| `app/dashboard/` | **PARTIAL** | Да | Реальная агрегация за `GET /api/dashboard`, по реальным записям. `today_totals` — по-прежнему тот же объект, что `all_time_totals`: сервис не различает временное окно. |
| `app/api/` | **ACTIVE** | Да | Слой сервиса управляет реальным executor и harness. Новое в этом проходе: launcher (`main.py`) и явная политика CORS/привязки. |
| `app/desktop/` | **UNUSED** | Нет | Реальный tkinter-шелл, но без `__main__` и без launcher'а; на него ссылаются только `desktop/__init__.py` и `tests/test_desktop.py`. Содержит кнопку мастер-настройки с меткой «(Placeholder)», которая ничего не делает. |
| `app/platform/` | **UNUSED** | Нет | Домен Stage 1, схема, персистентность и срез AS-1/AS-2 реализованы и протестированы на живой PostgreSQL, но **ни одна точка входа не достигает пакета**, и транспорта Platform → Core нет. |
| `app/reviews/`, `app/utils/` | **UNUSED** | Нет | Только маркеры пакетов; `app/reviews/__init__.py` — одна строка docstring. |
| `app/config/` | **ACTIVE** | Да | `SecretStore`, настройки и конфигурация провайдерских аккаунтов. |

## 2. Механизмы и их единственный владелец

Записано потому, что вторая реализация любого из них — архитектурный дефект, а не
стилистический выбор.

| Механизм | Единственный владелец сегодня | Дублирующая или конкурирующая реализация |
| --- | --- | --- |
| Полномочия исполнения | `app/execution/authorizer.py` (один продуктивный вызов `AuthorizedExecution.create`) | Нет |
| Approval (привязан к intent, одноразовый) | `app/tools/approval.py`, общий для tools и host-пути | Нет |
| Каталоги capabilities | `app/agents/providers/capabilities.py` + `app/tools/registry.py` | `app/fabric/`, `app/skills/`, `ModelRegistry` — `dispatcher.decide` обращается к четырём механизмам, и победитель зависит от того, что заполнила композиция; `app/skills/` по-прежнему не заполнен |
| Containment путей | `app/execution/sandbox.py` (`WorkspaceBoundary`) | Дополнительно реализован в `app/tools/workspace.py` и `app/execution/workspace_manager.py`. В этом проходе введён общий распознаватель секретных файлов; резолверы путей остаются раздельными. |
| Сборка контекста | `app/context/assembler.py` | Два живых класса в одном модуле |
| Цикл исполнения | `app/agent_runtime/harness.py` | `app/orchestrator/engineering.py` (только тесты) |
| Durable-история запусков | `app/runtime/run_store.py` | Idempotency ledger и telemetry — отдельные хранилища по замыслу; fabric accounting — четвёртое хранилище, теперь заполняемое при диспетчеризации (см. §1) |
| Снимки workspace | `app/understanding/snapshotter.py` | `app/tools/project_snapshots.py` (достижим только через `revision.py`) |
| Распознавание секретных файлов | `app/execution/secret_files.py` (введён в этом проходе) | Нет — сканер делегирует ему, поэтому staging и индексация не могут разойтись |

## 3. Кандидаты на очистку

Перечислены для будущего решения. **В этом проходе ни один компонент не удаляется.**

1. `app/knowledge/` — ноль потребителей; заморозить до подключения Project Memory.
2. `app/orchestrator/engineering.py`, `revision.py`, `multi_agent.py`, `review.py` — достижимы только из тестов.
3. `app/context/` — вывести legacy-сборщик и двойную схему именования `ContextItem`.
4. `app/fabric/resolve()` и `ModelRegistry` — решить, какой каталог главный, прежде чем расширять любой из них.
5. `app/memory/` — подключить к harness или удалить; затем решать про `app/knowledge/`.
6. `app/skills/` — подключить к harness или пометить PLANNED.
7. Два дублирующих резолвера путей (`app/tools/workspace.py`, `app/execution/workspace_manager.py`).
8. `today_totals` в dashboard — это тот же объект, что `all_time_totals`, и он никогда не сужается до окна; либо реализовать окно, либо удалить поле. (Fabric run accounting больше не кандидат: он заполняется.)
9. `app/desktop/` — добавить launcher и доделать или удалить placeholder-мастер.
10. `_allowed_execution_commands` в API-сервисе — сохранённый параметр, который, по признанию самого комментария, больше ничего не даёт.

## 4. Чего этот реестр не утверждает

- Он не утверждает, что пакет мёртв из-за отсутствия статического импорта. Инъекция на
  этапе композиции — это способ, которым часть из них и должна достигаться; поэтому
  используется `PARTIAL` (достижим на части путей), а не `UNUSED`, там где тесты
  демонстрируют рабочий путь.
- Он не расставляет приоритеты. Статус UNUSED у `app/platform/` ничего не говорит о его
  ценности: он говорит, что ни одна точка входа его пока не достигает, а это пробел в
  подключении, а не дефект дизайна.
- Он не объединяет Project Understanding, Project Memory и Forge Knowledge. Эти три
  остаются разными понятиями, и различие зафиксировано в `SOURCE_OF_TRUTH.md` §3.5.
