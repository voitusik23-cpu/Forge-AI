# Forge AI — Stage 16B Multi-Skill Agent Run v0.1

**Status:** IMPLEMENTED AND VERIFIED

**Scope:** Verification of multi-skill execution within a bounded Agent Harness run loop, demonstrating that multiple applicable skills can advise the AI Decision Provider across turns while strictly preserving all existing authority boundaries.

$$\text{Task} \to \text{SkillEvaluator} \to \text{ALL Applicable Skills} \to \text{Bounded Context} \to \text{AI Decision} \to \text{Validation} \to \text{Permission} \to \text{Approval} \to \text{Execution} \to \text{Verification} \to \text{Update}$$

---

## 1. Goal

The goal of Stage 16B is to prove that Forge AI can coordinate bounded engineering run loops using **MULTIPLE** applicable skills simultaneously without:
- Creating a skill workflow engine or skill planner
- Introducing artificial skill truncation (`max_skills`), routing, or priority engines
- Using confidence scores, semantic similarity, or LLM-based skill selection
- Permitting skills to chain, dispatch, or execute code
- Weakening any existing permission, approval, execution, verification, or acceptance authority

Skills remain strictly advisory context.

---

## 2. Existing Stage 15 Foundation

Stage 15 (`ae96c8a`) provided the complete structural pipeline for skills:
- **`SkillDefinition` & `SkillManifest` (`app/skills/models.py`):** Immutable, frozen data containers with validated alphanumeric Tool IDs (`^[a-zA-Z0-9_\-]+$`), declarative namespaced capabilities (`process:execute`, `workspace:read`), and sensitive metadata audits.
- **`SkillEvaluator` (`app/skills/evaluator.py`):** Deterministic prerequisite evaluator matching capabilities, tools, task preconditions, and trust constraints. Returns pure boolean `is_applicable: bool` plus matched and missing capabilities.
- **`DecisionContextAssembler` (`app/context/assembler.py`):** Encapsulates skills into discrete `ContextItem` entries with `source_type=ContextSourceType.SKILL`, bounding procedural guidance to $\le 1000$ characters and placing `name` and `skill_id` first.
- **`AgentHarness` (`app/agent_runtime/harness.py`):** Evaluates available skills during Phase 2 `CONTEXT` and passes applicable skills to context assembly.

Because Stage 15 was designed with multi-skill structural support from the beginning, **zero production code changes in `app/` were required** for Stage 16B.

---

## 3. Stage 16A Approved Architecture

Under the approved Stage 16A architecture:
1. **No Artificial Truncation (`max_skills`):** An artificial cap such as "first 3 skills by `skill_id`" would create an implicit, arbitrary priority policy favoring skills named alphabetically earlier. Stage 16 reuses the approved rule: **SkillEvaluator returns ALL applicable skills.**
2. **Context Bounding Over Selection Routing:** Safety and budget guardrails are enforced at the context assembly level through existing envelope bounds (`MAX_CONTEXT_ITEMS = 64`) and per-skill instruction bounds ($\le 1000$ characters).
3. **Deterministic Ordering:** Applicable skills are ordered deterministically by `manifest.skill_id` ascending to ensure bit-identical context across runs.
4. **Advisory Decision Model Unchanged:** `Decision` does not track or reference `skill_id`. Provenance is fully preserved in `DecisionContextEnvelope` items and `RunTrace` records.

---

## 4. Multi-Skill Data Flow

```
                          ┌───────────────────────────┐
                          │       HarnessRequest      │
                          │ - task_specification      │
                          │ - available_skills (N)    │
                          │ - available_capabilities  │
                          │ - allowed_execution_cmds  │
                          └─────────────┬─────────────┘
                                        │
                                        ▼
═══════════════════════════════ PHASE 1: OBSERVE ═══════════════════════════════
  Read workspace snapshot, past execution results, observations.
  Skills have ZERO presence or influence here.
═════════════════════════════════════════════════════════════════════════════════
                                        │
                                        ▼
═══════════════════════════════ PHASE 2: CONTEXT ═══════════════════════════════
  1. SkillEvaluator.find_applicable_skills(
         available_skills,
         task=task_spec,
         available_capabilities=caps,
         available_tool_ids=tools
     )
     -> Evaluates boolean eligibility for each skill.
     -> Returns ALL matching skills, deterministically sorted by skill_id.
  2. DecisionContextAssembler.assemble(..., skills=applicable_skills)
     -> Formats each skill as ContextItem(source_type=SKILL, max_len=1000).
     -> Injects items into DecisionContextEnvelope.
═════════════════════════════════════════════════════════════════════════════════
                                        │
                                        ▼
═══════════════════════════════ PHASE 3: DECIDE ════════════════════════════════
  AIDecisionProvider receives DecisionContextEnvelope.
  Builds prompt containing:
    - Task specification
    - Project state
    - ALL applicable skills advisory guidance (Skill A, Skill B, ...)
    - Prior observations
  Queries configured LLM -> Parses strict structured Decision JSON.
  Result: Advisory Decision (e.g. EXECUTE "python_test_runner").
═════════════════════════════════════════════════════════════════════════════════
                                        │
     ┌──────────────────────────────────┴──────────────────────────────────┐
     ▼                                                                     ▼
═════════════════════════════════════════════════════════════════════════════════
                 AUTHORITATIVE CONTROL & EXECUTION GATES
  *** ALL SKILL INFLUENCE TERMINATES AT THIS BOUNDARY. SKILLS CANNOT ACT. ***
═════════════════════════════════════════════════════════════════════════════════
  PHASE 4: VALIDATE  -> DecisionValidator checks schema & action legality.
  PHASE 5: AUTHORIZE -> PermissionPolicy enforces command allowlist.
                     -> ApprovalPolicy enforces human authorization gate.
  PHASE 6: ACT       -> ExecutionCoordinator runs command in subprocess sandbox.
  PHASE 7: VERIFY    -> WorkspaceVerifier evaluates filesystem expectations.
  PHASE 8: UPDATE    -> AcceptanceGate evaluates criteria; derive_project_state.
  PHASE 9: TERMINATE -> Check iteration/action bounds -> Complete or repeat.
```

---

## 5. Applicability Semantics

- A skill is evaluated solely against environmental prerequisites:
  1. `required_capabilities`: All must be present in `available_capabilities`.
  2. `requested_tools`: If specified and `available_tool_ids` is non-empty, at least one requested tool must be available.
  3. `target_task_types`: If specified in metadata, `task.task_type` must match.
  4. `allowed_trust_levels`: If evaluator specifies trust constraints, `skill.manifest.trust_level` must be included.
- All skills that satisfy these boolean checks are returned.
- No skills are dropped due to arbitrary count limits or similarity thresholds.

---

## 6. Context Semantics

- Each applicable skill produces exactly one `ContextItem`:
  - `source_type = ContextSourceType.SKILL`
  - `item_type = "skill"`
  - `item_id = "skill:<skill_id>"`
  - `trust_level`: Mapped from `SkillTrustLevel` (`BUILTIN` $\to$ `VERIFIED`, `LOCAL` $\to$ `CONFIRMED`, `UNTRUSTED` $\to$ `UNVERIFIED`).
  - `value`: JSON string `{"name": str, "skill_id": str, "guidance": str}`.
- Procedural instructions are strictly bounded to $\le 1000$ characters.
- Metadata and guidance are audited against sensitive substrings (`secret`, `password`, `token`, `api_key`, `credential`).

---

## 7. Conflict Semantics

When multiple skills present in context recommend different tools or mutually incompatible actions:
- **No Automatic Conflict Engine:** Forge AI does not include a `SkillConflictResolver`, priority matrix, or voting system.
- **Advisory Synthesis:** The advisory AI Decision Provider evaluates the full context envelope (task requirements, project state, all skills) and synthesizes a single proposed action.
- **Authoritative Gating:** The proposed action is independently validated by `validate_decision` and checked by `PermissionPolicy` and `ApprovalPolicy`. If unallowlisted, it is denied regardless of how many skills recommended it.
- **No Deadlock:** The harness proceeds or fails deterministically without hanging or deadlock.

---

## 8. Permission / Approval / Execution Boundaries

$$\text{Skill recommendation} \neq \text{Authorization} \neq \text{Permission} \neq \text{Execution}$$

1. **Permission Boundary:** `PermissionPolicy` checks execution commands against the explicit allowlist. Even if 5 applicable skills recommend running an unallowlisted tool, execution is authoritatively blocked with `permission_denied`.
2. **Approval Boundary:** `ApprovalPolicy` inspects required tools. Even if multiple skills advise proceeding immediately, execution halts into `WAITING_FOR_APPROVAL` if human approval is ungranted.
3. **Execution Boundary:** Skills have no execution methods, shell hooks, or process spawning. Execution occurs exclusively through `ExecutionCoordinator`.

---

## 9. Skill Chaining Rule

$$\boxed{\text{Skills cannot activate, execute, authorize, or chain to other Skills.}}$$

- If Skill A's guidance text contains: *"After this step, invoke forge.builtin.other_skill"*, this text is purely informational text for human/LLM reading.
- No dynamic skill resolution, recursion, or harness dispatch occurs.
- The skill set is evaluated strictly once per turn during Phase 2 `CONTEXT` from `HarnessRequest.available_skills`.

---

## 10. Skill vs. Plan Boundary

$$\begin{aligned}
\text{Skill} &= \text{Advisory procedural domain knowledge (\"how to do a class of work\")} \\
\text{Plan} &= \text{Explicit execution intent and step dependency graph (\"what concrete steps to do\")} \\
\text{Harness} &= \text{Controlled runtime execution loop and phase bounds} \\
\text{Policy} &= \text{Authoritative permission, approval, and acceptance gate}
\end{aligned}$$

Skills must never become hidden execution plans. Workflow sequencing belongs exclusively to the Planning layer (`app/planning/`) and the Agent Harness control loop.

---

## 11. Test Matrix MS-1 through MS-8

The dedicated test suite in `tests/test_multi_skill_harness.py` validates all multi-skill invariants:

| Test ID | Test Method | Description & Invariant Verified | Result |
| :--- | :--- | :--- | :---: |
| **MS-1** | `test_all_applicable_skills_included_without_truncation` | 5 eligible skills are all returned without truncation; sorted deterministically by `skill_id`; all reach context. | **PASS** |
| **MS-2** | `test_multi_skill_context_assembly_and_bounds` | Multiple skills format into discrete bounded `ContextItem` entries ($\le 1000$ chars) with verified trust mapping and secret safety. | **PASS** |
| **MS-3** | `test_complementary_multi_skill_end_to_end_run` | Multi-turn run with 2 complementary skills: Turn 1 executes tests per Skill A; Turn 2 verifies per Skill B; Turn 3 completes upon acceptance pass. | **PASS** |
| **MS-4** | `test_conflicting_multi_skill_advisory_resolution` | 2 conflicting skills in context: AI selects one; authoritative policies gate it; executes cleanly with no conflict engine or deadlock. | **PASS** |
| **MS-5** | `test_multi_skill_cannot_bypass_permission_policy` | Multiple skills advising unallowlisted command are authoritatively blocked with `permission_denied` by `PermissionPolicy`. | **PASS** |
| **MS-6** | `test_multi_skill_cannot_bypass_approval_policy` | Multiple skills advising unapproved action are authoritatively halted into `WAITING_FOR_APPROVAL` by `ApprovalPolicy`. | **PASS** |
| **MS-7** | `test_skill_text_chaining_is_inert` | Text in Skill A mentioning Skill B triggers zero dynamic activation, injection, or recursion. | **PASS** |
| **MS-8** | `test_multi_skill_deterministic_cross_run_isolation` | Consecutive multi-skill runs produce bit-identical context items and zero cross-run state pollution. | **PASS** |

---

## 12. Test Results

- **Stage 16B Test Suite (`tests/test_multi_skill_harness.py`):**
  `Ran 8 tests in 0.148s — OK`
- **Full Repository Test Suite (`tests/test_*.py`):**
  `Ran 530 tests in 3.999s — OK (skipped=2)`
  **Exact breakdown: 530 tests: 528 passed, 2 skipped, 0 failed.**
- **Bytecode Compilation (`compileall`):**
  `python -m compileall app tests` — Clean (0 errors, 0 warnings).
- **Git Diff Check (`git diff --check`):**
  Passed with 0 errors.

---

## 13. Non-Goals

Stage 16 explicitly does **NOT** implement:
- Dynamic skill marketplace or network downloading.
- Skill priority, weighting, or ranking engines.
- Semantic similarity, embeddings, or vector search for skills.
- LLM-based skill routing or selection.
- Skill dependency graphs or prerequisites between skills.
- Skill chaining, state machines, or workflow execution.
- Skill-based permission escalation or dynamic allowlists.
- Modifications to the `Decision` schema.

---

## 14. Known Limitations

- All skills must be declared in `HarnessRequest.available_skills` at run invocation time.
- Context items are bounded by `MAX_CONTEXT_ITEMS = 64`. If a task provides dozens of skills, total context item limits will prevent unbounded growth.
- Skills do not communicate with each other. Any procedural coordination across turns is synthesized purely by the advisory AI Decision Provider.

---

# Многонавыковый запуск агента Forge AI — Этап 16B — русская версия

**Статус:** РЕАЛИЗОВАНО И ПРОВЕРЕНО

**Область действия:** Проверка выполнения нескольких навыков в рамках ограниченного цикла `AgentHarness`. Демонстрирует, что несколько применимых навыков могут одновременно консультировать AI-провайдера решений на разных шагах цикла при строгом сохранении всех существующих границ полномочий.

$$\text{Task} \to \text{SkillEvaluator} \to \text{ВСЕ применимые навыки} \to \text{Ограниченный контекст} \to \text{AI-решение} \to \text{Валидация} \to \text{Разрешение} \to \text{Одобрение} \to \text{Выполнение} \to \text{Верификация} \to \text{Обновление}$$

---

## 1. Цель

Цель этапа 16B — доказать, что Forge AI может координировать ограниченный инженерный цикл с использованием **НЕСКОЛЬКИХ** применимых навыков одновременно без:
- Создания движка рабочих процессов (workflow engine) или планировщика навыков.
- Введения искусственных ограничений количества навыков (`max_skills`), маршрутизации или приоритетов.
- Использования оценок уверенности (confidence scores), семантического сходства или выбора навыков через LLM.
- Разрешения навыкам вызывать другие навыки или исполнять код.
- Ослабления полномочий существующих механизмов `PermissionPolicy`, `ApprovalPolicy`, `ExecutionCoordinator`, `WorkspaceVerifier` и `AcceptanceGate`.

Навыки остаются исключительно консультативным контекстом.

---

## 2. Существующая база Этапа 15

Этап 15 (`ae96c8a`) заложил полную структуру для работы с навыками:
- **`SkillDefinition` и `SkillManifest` (`app/skills/models.py`):** Неизменяемые структуры данных с валидированными буквенно-цифровыми Tool ID (`^[a-zA-Z0-9_\-]+$`), декларативными пространствами имён capabilities (`process:execute`, `workspace:read`) и строгой проверкой метаданных на секреты.
- **`SkillEvaluator` (`app/skills/evaluator.py`):** Детерминированная проверка соответствия средe, возвращающая булев результат `is_applicable: bool`.
- **`DecisionContextAssembler` (`app/context/assembler.py`):** Упаковка навыков в элементы `ContextItem` с типом `ContextSourceType.SKILL` и ограничением инструкций до $\le 1000$ символов.
- **`AgentHarness` (`app/agent_runtime/harness.py`):** Оценка навыков во второй фазе `CONTEXT` и передача применимых навыков в контекст.

Поскольку архитектура Этапа 15 изначально была рассчитана на работу со списком навыков, **для Этапа 16B не потребовалось вносить изменений в рабочий код пакета `app/`**.

---

## 3. Утверждённая архитектура Этапа 16A

1. **Без искусственного усечения (`max_skills`):** Искусственное ограничение вида «первые 3 навыка по `skill_id`» создало бы скрытую политику приоритетов. В Этапе 16 действует правило: **SkillEvaluator возвращает ВСЕ применимые навыки.**
2. **Ограничение контекста вместо маршрутизации:** Безопасность контекстного окна обеспечивается на уровне сборки контекста (`MAX_CONTEXT_ITEMS = 64` и $\le 1000$ символов на инструкции каждого навыка).
3. **Детерминированный порядок:** Применимые навыки сортируются по `manifest.skill_id` (по возрастанию) для обеспечения побитовой воспроизводимости.
4. **Неизменность модели решений:** Модель `Decision` не содержит ссылок на `skill_id`. Аудит полностью обеспечивается через элементы `DecisionContextEnvelope` и записи `RunTrace`.

---

## 4. Поток данных нескольких навыков

1. **Фаза 1 (OBSERVE):** Чтение состояния рабочей области. Навыки здесь не участвуют.
2. **Фаза 2 (CONTEXT):** `SkillEvaluator.find_applicable_skills` проверяет требования каждого навыка и возвращает все подходящие навыки. `DecisionContextAssembler` преобразует их в ограниченные элементы `ContextItem`.
3. **Фаза 3 (DECIDE):** `AIDecisionProvider` строит промпт со всеми применимыми навыками и запрашивает advisory-решение у LLM.
4. **Фазы 4–8 (Авторитетные шлюзы):** Любое влияние навыков заканчивается на входе в фазу `VALIDATE`. `DecisionValidator`, `PermissionPolicy`, `ApprovalPolicy`, `ExecutionCoordinator`, `WorkspaceVerifier` и `AcceptanceGate` независимо санкционируют и проверяют выполнение.

---

## 5. Семантика конфликтов

Если в контексте присутствуют навыки с противоположными рекомендациями:
- В Forge AI **нет автоматического движка разрешения конфликтов** (`SkillConflictResolver`).
- AI-провайдер синтезирует контекст и предлагает одно действие.
- Предложенное действие независимо проверяется авторитетными политиками (`PermissionPolicy`, `ApprovalPolicy`). Если команда не входит в белый список, она блокируется со статусом `permission_denied`.
- Никаких взаимных блокировок (deadlock) не возникает.

---

## 6. Правило цепочек навыков

$$\boxed{\text{Навыки не могут активировать, исполнять, санкционировать или вызывать другие навыки.}}$$

- Любой текст в инструкциях навыка с упоминанием других навыков является исключительно справочным текстом.
- Никакой динамической загрузки, рекурсии или порождения новых шагов цикла не происходит.

---

## 7. Граница между навыком (Skill) и планом (Plan)

$$\begin{aligned}
\text{Навык (Skill)} &= \text{Консультативное процедурное знание (\"как делать данный класс задач\")} \\
\text{План (Plan)} &= \text{Явный граф выполнения шагов (\"какие конкретные шаги делать в проекте\")} \\
\text{Harness} &= \text{Ограниченный рантайм-цикл и фазы исполнения} \\
\text{Политика (Policy)} &= \text{Авторитетные шлюзы разрешений, одобрений и приёмки}
\end{aligned}$$

Навыки никогда не должны превращаться в скрытые планы выполнения.

---

## 8. Матрица тестов MS-1 — MS-8

| ID теста | Название метода | Проверяемый инвариант | Результат |
| :--- | :--- | :--- | :---: |
| **MS-1** | `test_all_applicable_skills_included_without_truncation` | Все 5 подходящих навыков возвращаются без усечения; отсортированы по `skill_id`. | **PASS** |
| **MS-2** | `test_multi_skill_context_assembly_and_bounds` | Навыки преобразуются в отдельные элементы `ContextItem` ($\le 1000$ символов) с правильными уровнями доверия. | **PASS** |
| **MS-3** | `test_complementary_multi_skill_end_to_end_run` | Многошаговый ран с 2 дополняющими навыками: Шаг 1 — тесты (навык A); Шаг 2 — верификация (навык B); Шаг 3 — завершение при успешной приёмке. | **PASS** |
| **MS-4** | `test_conflicting_multi_skill_advisory_resolution` | 2 конфликтующих навыка: AI выбирает один путь; авторитетные политики проверяют его; ран завершается без дедлоков. | **PASS** |
| **MS-5** | `test_multi_skill_cannot_bypass_permission_policy` | Навыки не могут обойти белый список `PermissionPolicy`: отказ со статусом `permission_denied`. | **PASS** |
| **MS-6** | `test_multi_skill_cannot_bypass_approval_policy` | Навыки не могут обойти подтверждение человеком: переход в статус `WAITING_FOR_APPROVAL` политикой `ApprovalPolicy`. | **PASS** |
| **MS-7** | `test_skill_text_chaining_is_inert` | Текстовое упоминание другого навыка не вызывает динамической активации или рекурсии. | **PASS** |
| **MS-8** | `test_multi_skill_deterministic_cross_run_isolation` | Последовательные прогоны дают идентичные элементы контекста без перекрёстного загрязнения. | **PASS** |

---

## 9. Результаты тестирования

- **Набор тестов Этапа 16B (`tests/test_multi_skill_harness.py`):**
  `Ran 8 tests in 0.148s — OK`
- **Полный набор тестов репозитория (`tests/test_*.py`):**
  `Ran 530 tests in 3.999s — OK (skipped=2)`
  **Точные итоги: 530 tests: 528 passed, 2 skipped, 0 failed.**
- **Байт-компиляция (`compileall`):**
  `python -m compileall app tests` — Чисто (0 ошибок, 0 предупреждений).
- **Проверка `git diff --check`:**
  Успешно (0 ошибок).

---

## 10. Граница Skill и Plan

$$\begin{aligned}
\text{Skill} &= \text{Advisory procedural domain knowledge (\"how to do a class of work\")} \\
\text{Plan} &= \text{Explicit execution intent and step dependency graph (\"what concrete steps to do\")} \\
\text{Harness} &= \text{Controlled runtime execution loop and phase bounds} \\
\text{Policy} &= \text{Authoritative permission, approval, and acceptance gate}
\end{aligned}$$

Skills никогда не должны становиться скрытыми планами выполнения. Упорядочивание
workflow принадлежит исключительно слою Planning (`app/planning/`) и циклу
управления Agent Harness.

---

## 11. Тестовая матрица MS-1 ... MS-8

Выделенный набор тестов в `tests/test_multi_skill_harness.py` проверяет все
инварианты множественных skills:

| Test ID | Метод теста | Описание и проверяемый инвариант | Результат |
| :--- | :--- | :--- | :---: |
| **MS-1** | `test_all_applicable_skills_included_without_truncation` | Все 5 подходящих skills возвращаются без усечения; детерминированно отсортированы по `skill_id`; все доходят до контекста. | **PASS** |
| **MS-2** | `test_multi_skill_context_assembly_and_bounds` | Несколько skills форматируются в отдельные ограниченные элементы `ContextItem` ($\le 1000$ символов) с проверенным отображением trust и безопасностью секретов. | **PASS** |
| **MS-3** | `test_complementary_multi_skill_end_to_end_run` | Многоходовый запуск с 2 дополняющими skills: ход 1 выполняет тесты по Skill A; ход 2 верифицирует по Skill B; ход 3 завершается при прохождении приёмки. | **PASS** |
| **MS-4** | `test_conflicting_multi_skill_advisory_resolution` | 2 конфликтующих skills в контексте: ИИ выбирает один; авторитетные политики ограничивают его; выполнение проходит чисто, без движка конфликтов и без дедлока. | **PASS** |
| **MS-5** | `test_multi_skill_cannot_bypass_permission_policy` | Несколько skills, советующих неразрешённую команду, авторитетно блокируются с `permission_denied` со стороны `PermissionPolicy`. | **PASS** |
| **MS-6** | `test_multi_skill_cannot_bypass_approval_policy` | Несколько skills, советующих неодобренное действие, авторитетно останавливаются в `WAITING_FOR_APPROVAL` со стороны `ApprovalPolicy`. | **PASS** |
| **MS-7** | `test_skill_text_chaining_is_inert` | Текст в Skill A, упоминающий Skill B, не запускает никакой динамической активации, инъекции или рекурсии. | **PASS** |
| **MS-8** | `test_multi_skill_deterministic_cross_run_isolation` | Последовательные запуски с несколькими skills дают побитово идентичные элементы контекста и нулевое загрязнение состояния между запусками. | **PASS** |

---

## 12. Результаты тестов

- **Набор тестов Stage 16B (`tests/test_multi_skill_harness.py`):**
  `Ran 8 tests in 0.148s — OK`
- **Полный набор тестов репозитория (`tests/test_*.py`):**
  `Ran 530 tests in 3.999s — OK (skipped=2)`
  **Точная разбивка: 530 тестов: 528 прошли, 2 пропущены, 0 неуспешных.**
- **Компиляция байткода (`compileall`):**
  `python -m compileall app tests` — чисто (0 ошибок, 0 предупреждений).
- **Проверка git diff (`git diff --check`):**
  Пройдена с 0 ошибок.

---

## 13. Не-цели

Stage 16 явно **НЕ** реализует:
- Динамический маркетплейс skills или их скачивание из сети.
- Движки приоритета, весов или ранжирования skills.
- Семантическое сходство, эмбеддинги или векторный поиск для skills.
- LLM-маршрутизацию или выбор skills.
- Графы зависимостей skills или предварительные условия между skills.
- Сцепление skills, конечные автоматы или выполнение workflow.
- Повышение разрешений на основе skills или динамические белые списки.
- Изменения схемы `Decision`.

---

## 14. Известные ограничения

- Все skills должны быть объявлены в `HarnessRequest.available_skills` в момент вызова запуска.
- Элементы контекста ограничены `MAX_CONTEXT_ITEMS = 64`. Если задача предоставляет десятки skills, лимиты общего числа элементов контекста предотвратят неограниченный рост.
- Skills не взаимодействуют друг с другом. Любая процедурная координация между ходами синтезируется исключительно рекомендательным AI Decision Provider.
