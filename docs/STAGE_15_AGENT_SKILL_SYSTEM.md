# Forge AI — Stage 15 Agent Skill System v0.1

**Status:** IMPLEMENTED AND VERIFIED

**Scope:** Implementation of the deterministic, advisory Agent Skill System (`app/skills/`) and integration into the context assembly and agent harness lifecycle. Demonstrates the authoritative capability pipeline:
$$\text{Agent} \to \text{Skill (Advisory)} \to \text{Capability (Declarative)} \to \text{Tool ID} \to \text{Permission} \to \text{Approval} \to \text{Execution} \to \text{Verification}$$

---

## 1. Executive Summary

Stage 15 establishes Forge AI's Agent Skill System v0.1. A Skill in Forge AI is structured domain guidance and procedural context that helps the advisory AI decision provider make more informed action recommendations.

Crucially, Stage 15 strictly upholds the foundational architectural invariant:
$$\text{Skill recommendation} \neq \text{Authorization} \neq \text{Permission} \neq \text{Execution}$$

Under this architecture:
- **Skills are purely advisory context:** A skill provides procedural guidance and references required capabilities and requested tool IDs. It possesses **zero** execution authority.
- **Capabilities are declarative strings:** Capabilities are namespaced strings (e.g. `process:execute`, `workspace:read`) representing environmental prerequisites. There is **no runtime `CapabilityRegistry`** that confers execution authority.
- **Tools are abstract Tool IDs:** `requested_tools` strictly accepts abstract alphanumeric identifiers (e.g. `python_test_runner`). Raw executable shell commands, command-line arguments, shell pipes, or redirection tokens are strictly rejected at definition time.
- **Deterministic boolean evaluation:** `SkillEvaluator` is 100% deterministic, offline, and side-effect free. It runs without LLMs, network requests, subprocess spawning, filesystem mutations, or project state alterations. It contains no ranking engine, no token similarity scoring, and no confidence score calculations. It evaluates required capabilities, requested tool availability, explicit task preconditions, and configured trust constraints to produce a boolean applicability outcome with deterministic matched and missing capabilities.
- **Separation of Applicability and Permission:** `SkillEvaluator` decides whether a skill is applicable context for the advisory AI. `PermissionPolicy` authoritatively decides whether an action or tool is permitted. A skill being applicable does not imply or grant permission.
- **Strict, uncompromised validation:** Skill manifests enforce fail-closed validation. Sensitive tokens (passwords, secrets, tokens, api keys, credentials) in metadata are explicitly rejected.
- **Authoritative gates remain unbroken:** Even if a skill advises a specific action, tool, or command, that command must independently pass `validate_decision`, `PermissionPolicy`, `ApprovalPolicy`, `ExecutionCoordinator`, `WorkspaceVerifier`, and `AcceptanceGate`.

A comprehensive test suite of 18 test scenarios in `tests/test_skill_system.py` validates the entire skill system lifecycle, immutability, safety constraints, authority preservation, and cross-run isolation. Full repository test suite results: 522 tests: 520 passed, 2 skipped, 0 failed.

---

## 2. Architecture & Authority Boundaries

### 2.1 The Capability & Execution Pipeline

```
┌────────────────────────────────────────────────────────┐
│                     HarnessRequest                     │
│  (task, available_capabilities, available_skills)     │
└───────────────────────────┬────────────────────────────┘
                            │
┌───────────────────────────▼────────────────────────────┐
│                    SkillEvaluator                      │
│  - Boolean matching of required capabilities           │
│  - Verification of requested tool IDs availability     │
│  - Task precondition matching (e.g. target_task_types) │
│  - Trust constraint evaluation (allowed_trust_levels)  │
│  - Returns boolean applicability + matched/missing caps│
└───────────────────────────┬────────────────────────────┘
                            │ Applicable Skills (Advisory)
┌───────────────────────────▼────────────────────────────┐
│              DecisionContextAssembler                  │
│  - Creates bounded (<= 1000 chars) ContextItem         │
│  - source_type = ContextSourceType.SKILL               │
│  - Formats name, skill_id, and procedural guidance     │
└───────────────────────────┬────────────────────────────┘
                            │ Bounded Context Envelope
┌───────────────────────────▼────────────────────────────┐
│                  AIDecisionProvider                    │
│  - Receives skill guidance as advisory background      │
│  - Proposes next Action (EXECUTE, VERIFY, etc.)        │
└───────────────────────────┬────────────────────────────┘
                            │ Proposed Decision
┌───────────────────────────▼────────────────────────────┐
│              AUTHORITATIVE BOUNDARIES                  │
│                                                        │
│  1. DecisionValidator: checks schema & action legality │
│  2. PermissionPolicy: checks command allowlist         │
│  3. ApprovalPolicy: checks human approval gates        │
│  4. ExecutionCoordinator: sandboxed subprocess run     │
│  5. WorkspaceVerifier: inspects actual filesystem      │
│  6. AcceptanceGate: validates verified criteria        │
│  7. ProjectState: derives authoritative health         │
└────────────────────────────────────────────────────────┘
```

### 2.2 Authority Matrix

| Subsystem | Authority Level | Role & Invariants |
| :--- | :--- | :--- |
| **SkillDefinition** | Declarative Data | Immutable advisory structure containing procedural guidance, required capabilities, and requested tool IDs. Cannot execute code or grant rights. |
| **Capability** | Declarative Namespace | Pure string label (e.g. `process:execute`). Does not grant execution authority. |
| **SkillEvaluator** | Deterministic Matcher | Evaluates whether environment meets skill prerequisites. Pure logic, zero side-effects. Does not authorize or permit tool execution. |
| **AIDecisionProvider** | Advisory | Recommends next step based on context envelope (including skill items). Has no tool execution authority. |
| **Decision Validator** | Authoritative Gate | Verifies decision structure and prevents premature completion before passing acceptance. |
| **PermissionPolicy** | Authoritative Gate | Authoritatively blocks any command not in the explicit allowlist. Skills cannot alter this allowlist. |
| **ApprovalPolicy** | Authoritative Gate | Halts execution if human approval is required. Skills cannot grant or bypass approval. |
| **AcceptanceGate** | Authoritative Gate | Evaluates authoritative requirement criteria. Skills cannot force pass acceptance. |
| **AgentHarness** | Authoritative Runtime | Coordinates the multi-turn loop within strict budget limits (`max_iterations`, `max_actions`). |

---

## 3. What Was Proven vs. What Remains Unproven

### 3.1 Proven Invariants

1. **Advisory Non-Authority & Permission Separation:** Proved that a skill recommending an action cannot execute tools or bypass policies:
   - Proved in **Scenario E** that when an AI decision follows a skill's guidance to run an unauthorized command, `PermissionPolicy` authoritatively blocks execution with `permission_denied`.
   - Proved in **Scenario F** that when an action requires human approval, an AI decision guided by a skill is halted into `WAITING_FOR_APPROVAL` by `ApprovalPolicy`.
   - Proved in **Scenario I** that even when an `UNTRUSTED` skill is evaluated as applicable, `PermissionPolicy` authoritatively blocks unallowlisted execution commands. Applicability does not imply permission.
   - Proved in **Scenario K** that an AI decision attempting `COMPLETE_RUN` under skill guidance is rejected by `AcceptanceGate` when acceptance criteria are not met.
2. **Strict Manifest & Tool Validation:**
   - Proved in **test_raw_shell_command_rejected_in_requested_tools** that manifests reject raw shell commands, flags, arguments, and shell metacharacters (`python -m unittest`, `-rf`, `cat|grep`). Only abstract tool identifiers (`^[a-zA-Z0-9_-]+$`) are allowed.
   - Proved in **test_malformed_skill_metadata_rejection** that manifests reject sensitive substrings in metadata (`password`, `secret`, `token`, `api_key`, `credential`, `stdout`, `stderr`).
3. **Deterministic Boolean Evaluation:**
   - Proved in **Scenario B & C** that matching capabilities produces boolean applicability (`is_applicable=True`), while missing required capabilities deterministically marks the skill as inapplicable (`is_applicable=False`) with precise missing capability tracking.
   - Proved in **Scenario I** that `SkillEvaluator` cleanly supports trust level constraints (`allowed_trust_levels`) without acting as a second permission policy.
4. **Context Window & Secret Safety:**
   - Proved in **Scenario G & H** that `DecisionContextAssembler` converts applicable skills into bounded `ContextItem` objects (<= 1000 chars), positions skill name and ID at the top to ensure visibility after prompt truncation, and strips sensitive keywords.
5. **Multi-Turn Harness Integration:**
   - Proved in **Scenario K** that skills passed to `HarnessRequest` are evaluated during Phase 2 `CONTEXT`, integrated into context, presented to `AIDecisionProvider`, and enable successful multi-turn execution and acceptance.
6. **Immutability & Cross-Run Isolation:**
   - Proved in **Scenario L & test_immutable_skill_objects** that modifying request objects or executing consecutive runs does not mutate skill definitions, registry contents, or cross-pollinate runtime state.

### 3.2 What Remains Unproven (Out of Scope for v0.1)

- Dynamic skill installation from remote network sources or git repositories.
- Skill version resolution and semantic version dependency graphs.
- Multi-skill composition with complex priority conflict resolution.
- Dynamic capability negotiation during runtime.
- Model-guided dynamic skill selection via agent tool calling.

---

## 4. Implementation Details

### 4.1 Data Models (`app/skills/models.py`)

- `SkillTrustLevel`: Enum with values `BUILTIN`, `LOCAL`, `UNTRUSTED`.
- `SkillProvenance`: Frozen dataclass capturing origin, author, version, commit_hash, and sha256 content_hash.
- `SkillManifest`: Frozen dataclass with strict field validation in `__post_init__`:
  - `skill_id`: Must match `^[a-z0-9_.-]+$`.
  - `required_capabilities` / `optional_capabilities`: Must be namespaced strings matching `^[a-z0-9_-]+:[a-z0-9_-]+$`.
  - `requested_tools`: Must be abstract identifiers matching `^[a-zA-Z0-9_-]+$`.
  - `parameters` / `metadata`: Converted to immutable mappings; metadata audited against sensitive patterns.
- `SkillDefinition`: Frozen dataclass pairing `SkillManifest` with bounded procedural `instructions` (<= 10000 chars).
- `SkillApplicability`: Deterministic boolean result containing `skill_id`, `is_applicable`, `reason`, `matched_capabilities`, and `missing_capabilities`.

### 4.2 In-Memory Registry (`app/skills/registry.py`)

- `SkillRegistry`: In-memory registry supporting `register()`, `get()`, `list_skills()`, and `contains()`. Rejects duplicate registrations explicitly to prevent silent overrides.

### 4.3 Deterministic Evaluator (`app/skills/evaluator.py`)

- `SkillEvaluator`: Evaluates skills against available capabilities, tools, and constraints without side effects:
  - Verifies trust constraints if `allowed_trust_levels` is specified.
  - Verifies all required capabilities are present in `available_capabilities`.
  - Verifies requested tools are present in `available_tool_ids`.
  - Checks explicit task preconditions (`target_task_types`).
  - Returns `SkillApplicability` with boolean `is_applicable`, deterministic `reason`, `matched_capabilities`, and `missing_capabilities`.
  - Orders applicable skills deterministically by `skill_id`.

### 4.4 Builtin Skill (`app/skills/builtin.py`)

- `forge.builtin.python_test_runner`: Builtin reference skill providing procedural guidance on running python unit tests, inspecting failure outputs, and diagnosing test errors. Declares required capabilities `("process:execute",)` and requested tool `("python_test_runner",)`.

### 4.5 Context Assembler Integration (`app/context/assembler.py`)

- Added `ContextSourceType.SKILL = "SKILL"`.
- `DecisionContextAssembler.assemble()` accepts typed `skills: Iterable[SkillDefinition] = ()`.
- Formats each skill as a structured JSON payload bounded to 1000 characters, placing `name` and `skill_id` first to prevent prompt truncation from hiding skill identity.

### 4.6 Harness Integration (`app/agent_runtime/harness.py` & `models.py`)

- `HarnessRequest` typed with `available_skills: tuple[SkillDefinition, ...] = ()` and `available_capabilities: tuple[str, ...] = ()`.
- In Phase 2 `CONTEXT`, `AgentHarness` invokes `SkillEvaluator` on provided skills and capabilities.
- Applicable skills are passed to `DecisionContextAssembler.assemble()`, making advisory guidance visible to the decision provider.

---

## 5. Verification Matrix

The test suite in `tests/test_skill_system.py` executes 18 automated tests:

| Test ID | Method Name | Description / Invariant Verified | Result |
| :--- | :--- | :--- | :--- |
| **A** | `test_scenario_a_registration_and_retrieval` | Register and retrieve builtin `python_test_runner` skill from `SkillRegistry`. | **PASS** |
| **B** | `test_scenario_b_deterministic_applicability` | Evaluator marks skill applicable when all capabilities and tools align. | **PASS** |
| **C** | `test_scenario_c_missing_capability_rejection` | Evaluator marks skill inapplicable when required capabilities are missing. | **PASS** |
| **D** | `test_scenario_d_no_execution_authority` | Proves via reflection that Skill classes expose zero execution methods or hooks. | **PASS** |
| **E** | `test_scenario_e_permission_cannot_be_bypassed_by_skill` | PermissionPolicy strictly blocks unauthorized command advised by skill. | **PASS** |
| **F** | `test_scenario_f_approval_cannot_be_bypassed_by_skill` | ApprovalPolicy halts execution into `WAITING_FOR_APPROVAL` even when advised by skill. | **PASS** |
| **G** | `test_scenario_g_context_assembly_includes_skill` | Context assembler generates bounded `ContextItem` with source `SKILL`. | **PASS** |
| **H** | `test_scenario_h_secret_safe_skill_context` | Proves skills and context items do not leak secret keys. | **PASS** |
| **I** | `test_scenario_i_untrusted_skill_handling` | Evaluator checks trust constraints; proves skill applicability does not imply permission. | **PASS** |
| **J** | `test_scenario_j_multiple_applicable_skills_structurally_supported` | Evaluator and Assembler accept and deterministically order multiple skills. | **PASS** |
| **K** | `test_scenario_k_real_bounded_run_using_python_test_runner` | Multi-turn run guided by skill context to successful acceptance pass. | **PASS** |
| **L** | `test_scenario_l_deterministic_provider_regression` | Deterministic provider runs without regression with skills present. | **PASS** |
| **Extra 1** | `test_duplicate_skill_id_rejection` | Registry raises `DuplicateSkillError` on duplicate registration. | **PASS** |
| **Extra 2** | `test_immutable_skill_objects` | Manifest, Definition, and Provenance are strictly frozen dataclasses. | **PASS** |
| **Extra 3** | `test_malformed_skill_metadata_rejection` | Checks rejection of sensitive patterns in manifest metadata. | **PASS** |
| **Extra 4** | `test_raw_shell_command_rejected_in_requested_tools` | Rejects raw shell commands, flags, and spaces in `requested_tools`. | **PASS** |
| **Extra 5** | `test_provenance_hash_determinism` | Verifies sha256 content hash determinism for skill definitions. | **PASS** |
| **Extra 6** | `test_cross_run_skill_and_context_isolation` | Proves skill context items do not introduce cross-run pollution. | **PASS** |

---

# Система навыков агента Forge AI — Этап 15 (Русская версия)

**Статус:** РЕАЛИЗОВАНО И ПРОВЕРЕНО

**Область действия:** Реализация детерминированной, консультативной системы навыков агента (`app/skills/`) и её интеграция в сборку контекста и жизненный цикл `AgentHarness`. Демонстрирует авторитетный пайплайн возможностей:
$$\text{Agent} \to \text{Skill (Консультативный)} \to \text{Capability (Декларативный)} \to \text{Tool ID} \to \text{Permission} \to \text{Approval} \to \text{Execution} \to \text{Verification}$$

---

## 1. Краткое резюме

Этап 15 закладывает основу системы навыков агента (Agent Skill System v0.1) в Forge AI. Навык (Skill) в Forge AI представляет собой структурированные предметные рекомендации и процедурный контекст, помогающие консультативному AI-провайдеру принимать более точные решения.

При этом неизменно соблюдается базовый архитектурный инвариант:
$$\text{Рекомендация навыка} \neq \text{Авторизация} \neq \text{Разрешение (Permission)} \neq \text{Выполнение (Execution)}$$

Ключевые принципы архитектуры:
- **Навыки — это исключительно консультативный контекст:** Навык предоставляет процедурные инструкции, список требуемых capabilities и идентификаторы запрашиваемых инструментов. Навык обладает **нулевыми** полномочиями на выполнение.
- **Capabilities — это декларативные строки:** Возможности представляют собой строки с пространством имён (например, `process:execute`, `workspace:read`), описывающие требования к среде. В системе **нет рантайм-реестра `CapabilityRegistry`**, дающего право на выполнение.
- **Инструменты — это абстрактные идентификаторы (Tool IDs):** В `requested_tools` разрешены только буквенно-цифровые идентификаторы (например, `python_test_runner`). Необработанные исполняемые команды оболочки, флаги, аргументы, пайпы или токены перенаправления строго отклоняются на этапе валидации манифеста.
- **Детерминированная булева оценка:** `SkillEvaluator` на 100% детерминирован, работает в оффлайн-режиме и не имеет побочных эффектов. Он функционирует без вызовов LLM, сетевых запросов, запуска процессов, изменения файловой системы или состояния проекта. В нём отсутствуют движки ранжирования, оценка сходства токенов и расчёт уровней уверенности (confidence scores). Оцениваются только требуемые capabilities, наличие инструментов, явные предусловия задачи и ограничения доверия, возвращая булев результат применимости с детерминированными списками совпавших и недостающих возможностей.
- **Разделение применимости (Applicability) и разрешения (Permission):** `SkillEvaluator` определяет, применим ли навык как контекст для AI-консультанта. `PermissionPolicy` авторитетно решает, разрешено ли действие или инструмент. Применимость навыка не подразумевает и не даёт разрешения на выполнение.
- **Строгая валидация без компромиссов:** Манифесты навыков следуют принципу запрета по умолчанию (fail-closed). Любые конфиденциальные токены (пароли, ключи API, токены, учётные данные) в метаданных вызывают ошибку валидации.
- **Авторитетные границы неприкосновенны:** Даже если навык рекомендует выполнить конкретное действие или команду, эта команда должна независимо пройти проверки `validate_decision`, `PermissionPolicy`, `ApprovalPolicy`, `ExecutionCoordinator`, `WorkspaceVerifier` и `AcceptanceGate`.

Полный набор из 18 тестов в `tests/test_skill_system.py` подтверждает корректность жизненного цикла, неизменяемость, сохранение границ авторитета и изоляцию между прогонами. Результаты полного набора тестов репозитория: 522 tests: 520 passed, 2 skipped, 0 failed.

---

## 2. Архитектура и границы полномочий

### 2.1 Пайплайн возможностей и выполнения

Пайплайн строго разделяет консультативную фазу сопоставления навыков от авторитетной фазы исполнения:
1. `HarnessRequest` передаёт задачу, доступные возможности (`available_capabilities`) и набор навыков (`available_skills: tuple[SkillDefinition, ...]`).
2. `SkillEvaluator` детерминированно проверяет наличие необходимых capabilities и инструментов, сверяет ограничения доверия и предусловия задачи, возвращая булев результат применимости.
3. `DecisionContextAssembler` формирует ограниченный по размеру (<= 1000 символов) элемент контекста с типом `ContextSourceType.SKILL`, размещая идентификаторы в начале и удаляя потенциальные секреты.
4. `AIDecisionProvider` получает рекомендации навыка в составе контекста и предлагает следующее действие.
5. Авторитетные компоненты (`DecisionValidator`, `PermissionPolicy`, `ApprovalPolicy`, `ExecutionCoordinator`, `WorkspaceVerifier`, `AcceptanceGate`) независимо проверяют и санкционируют предложенное действие.

### 2.2 Матрица полномочий

| Подсистема | Уровень авторитета | Роль и инварианты |
| :--- | :--- | :--- |
| **SkillDefinition** | Декларативные данные | Неизменяемая структура с процедурным руководством, требуемыми возможностями и ID инструментов. Не может исполнять код или выдавать права. |
| **Capability** | Декларативное пространство имён | Простая строка (например, `process:execute`). Не даёт прав на выполнение. |
| **SkillEvaluator** | Детерминированный матчер | Проверяет соответствие среды требованиям навыка. Чистая логика без побочных эффектов. Не даёт разрешений на выполнение. |
| **AIDecisionProvider** | Консультативный | Рекомендует следующий шаг на основе контекста (включая навыки). Не имеет прямого доступа к инструментам. |
| **Decision Validator** | Авторитетный шлюз | Проверяет структуру решения и блокирует преждевременное завершение до прохождения приёмки. |
| **PermissionPolicy** | Авторитетный шлюз | Блокирует любые команды, не входящие в белый список. Навыки не могут изменить этот список. |
| **ApprovalPolicy** | Авторитетный шлюз | Приостанавливает выполнение при отсутствии обязательного подтверждения человеком. |
| **AcceptanceGate** | Авторитетный шлюз | Оценивает соответствие критериям приёмки. Навыки не могут принудительно утвердить приёмку. |
| **AgentHarness** | Авторитетный рантайм | Управляет многошаговым циклом в пределах заданных лимитов (`max_iterations`, `max_actions`). |

---

## 3. Что доказано, а что остаётся недоказанным

### 3.1 Доказанные инварианты

1. **Консультативный статус и разделение полномочий:**
   - В **Сценарии E** доказано, что если AI-решение по совету навыка пытается выполнить неразрешённую команду, `PermissionPolicy` блокирует её со статусом `permission_denied`.
   - В **Сценарии F** доказано, что если действие требует подтверждения человеком, рекомендация навыка переводит ран в статус `WAITING_FOR_APPROVAL` политикой `ApprovalPolicy`.
   - В **Сценарии I** доказано, что даже если `UNTRUSTED` навык признан применимым, `PermissionPolicy` блокирует выполнение неразрешённых команд. Применимость не означает разрешения.
   - В **Сценарии K** доказано, что попытка `COMPLETE_RUN` под влиянием навыка блокируется `AcceptanceGate`, если критерии приёмки не выполнены.
2. **Строгая валидация манифестов и инструментов:**
   - В тесте **test_raw_shell_command_rejected_in_requested_tools** доказано, что манифесты отклоняют сырые shell-команды, аргументы и спецсимволы оболочки (`python -m unittest`, `-rf`, `cat|grep`). Разрешены только абстрактные ID (`^[a-zA-Z0-9_-]+$`).
   - В тесте **test_malformed_skill_metadata_rejection** доказано отклонение метаданных с чувствительными паттернами (`password`, `secret`, `token`, `api_key`, `credential`, `stdout`, `stderr`).
3. **Детерминированная булева оценка применимости:**
   - В **Сценариях B и C** доказано детерминированное сопоставление возможностей: при наличии всех требований навык признаётся применимым, при отсутствии хотя бы одного — неприменимым.
   - В **Сценарии I** доказана поддержка ограничений доверия (`allowed_trust_levels`) без узурпации функций политик разрешений.
4. **Безопасность контекста и секретов:**
   - В **Сценариях G и H** доказано, что `DecisionContextAssembler` создаёт ограниченные элементы контекста (<= 1000 символов), размещает имя и ID навыка в начале строки для защиты от усечения и исключает конфиденциальные слова.
5. **Интеграция в многошаговый цикл Harness:**
   - В **Сценарии K** доказано, что навыки, переданные в `HarnessRequest`, оцениваются во второй фазе `CONTEXT`, передаются в AI-провайдер и обеспечивают успешное выполнение цикла до завершения приёмки.
6. **Неизменяемость и изоляция между прогонами:**
   - В **Сценарии L и test_immutable_skill_objects** доказано, что передача структур данных и выполнение последовательных прогонов не изменяют определения навыков, реестр или состояние других задач.

### 3.2 Что остаётся недоказанным (вне рамок v0.1)

- Динамическая загрузка навыков из удалённых сетевых источников или git-репозиториев.
- Разрешение версий навыков и граф семантических зависимостей.
- Композиция множества навыков со сложным разрешением конфликтов приоритетов.
- Динамическое согласование capabilities во время выполнения.
- Вызов навыков агентом через механизм function calling / tool calling модели.
