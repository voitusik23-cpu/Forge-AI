# Forge AI — Stage 12 Agent Harness / Controlled Run Loop v0.1

**Status:** IMPLEMENTED AND VERIFIED

**Scope:** Deterministic Agent Harness coordinating the controlled Run Loop (`AgentHarness`, `AgentHarnessPolicy`, `HarnessPhase`, `HarnessStatus`, `HarnessState`, `StructuredObservation`, `HarnessRequest`, `HarnessResult`). The harness orchestrates existing Forge contracts into a bounded, deterministic cycle:
`OBSERVE` → `ASSEMBLE_CONTEXT` → `DECIDE` → `VALIDATE_DECISION` → `AUTHORIZE` → `ACT` → `OBSERVE_RESULT` → `UPDATE_STATE` → `REPEAT / COMPLETE`.

---

## 1. Executive Summary

Stage 12 introduces the first deterministic Agent Harness and controlled Run Loop in Forge AI. The harness connects existing Forge foundational contracts into a bounded runtime cycle without adding unbounded autonomous behavior, external AI provider dependencies, LLMs, persistence, network, or UI layers.

The harness acts as a coordinator, strictly respecting the pre-existing hierarchy of authorities:
- **Decision Layer** (`DeterministicDecisionProvider`, `DecisionContextAssembler`, `validate_decision`) remains strictly **advisory**.
- **Security Boundaries** (`PermissionPolicy`, `ApprovalPolicy`, `ApprovalResolver`, `ExecutionPolicy`, `ExecutionCoordinator`) remain strictly **authoritative**; decisions can never bypass permission checks or approval requirements.
- **Verification & Acceptance** (`WorkspaceVerifier`, `AcceptanceGate`) remain strictly **authoritative**; decisions can never force a `COMPLETE_RUN` without passing acceptance criteria.
- **Project State** (`ProjectState`, `derive_project_state`) remains strictly **authoritative**; state transitions are derived from verified facts and attempt artifacts, never fabricated by the runtime harness.

A comprehensive test suite of 26 scenarios (unit and adversarial) in `tests/test_agent_harness.py` verifies all lifecycle phases, bounds, policy enforcement, immutability, safe observation filtering, cross-run isolation, and failure modes. All 26 harness tests and all 476 project-wide tests pass cleanly.

---

## 2. Core Architecture & Lifecycle

### 2.1 The Controlled Run Loop

Each iteration of the Agent Harness executes an explicit, ordered sequence of phases:

```
+---------------------------------------------------------------------------------+
|                               AGENT HARNESS LOOP                                |
|                                                                                 |
|  1. OBSERVE           Inspect current iteration and project state              |
|         |                                                                       |
|  2. CONTEXT           Assemble bounded DecisionContextEnvelope (provenance)     |
|         |                                                                       |
|  3. DECIDE            DecisionProvider recommends next typed Decision           |
|         |                                                                       |
|  4. VALIDATE          Validate decision against request and project state       |
|         |                                                                       |
|  5. AUTHORIZE         Evaluate Permission, Approval, and ExecutionPolicy        |
|         |                                                                       |
|  6. ACT               Execute at most one authoritative action                  |
|         |                                                                       |
|  7. OBSERVE_RESULT    Produce bounded, secret-free StructuredObservation        |
|         |                                                                       |
|  8. UPDATE_STATE      Derive new authoritative ProjectState                     |
|         |                                                                       |
|  9. REPEAT / COMPLETE Terminate if terminal condition met, else next iteration  |
+---------------------------------------------------------------------------------+
```

### 2.2 Package Structure

- `app/agent_runtime/__init__.py`: Public exports.
- `app/agent_runtime/models.py`: Immutable models (`HarnessPhase`, `HarnessStatus`, `HarnessState`, `StructuredObservation`, `HarnessRequest`, `HarnessResult`).
- `app/agent_runtime/policy.py`: `AgentHarnessPolicy` defining bounds (`max_iterations`, `max_actions`, `max_execution_attempts`, `max_revision_attempts`, `allow_parallel_actions=False`, `fail_on_unknown_decision=True`).
- `app/agent_runtime/harness.py`: `AgentHarness` implementing the runtime loop and authority boundaries.

### 2.3 Non-Bypassable Invariants

1. **At most one authoritative action per iteration:** The harness dispatches exactly one action (`EXECUTE`, `RUN_VERIFICATION`, `REQUEST_REVISION`, `COMPLETE_RUN`, `FAIL_RUN`, or `WAIT_FOR_APPROVAL`) per cycle.
2. **Authority preservation:** Subprocess execution is dispatched exclusively through `ExecutionCoordinator`. No subprocess is ever spawned directly by the harness.
3. **Approval boundary preservation:** When human approval is required (`ApprovalState.REQUIRED`), execution is suspended and the harness transitions to `HarnessPhase.WAITING` with status `WAITING_FOR_APPROVAL`.
4. **Acceptance boundary preservation:** A decision proposing `COMPLETE_RUN` is authorized only if `acceptance_result` exists and is `AcceptanceStatus.PASS`.
5. **Conservative bounds:** Strict limits (`max_iterations`, `max_actions`, `max_execution_attempts`, `max_revision_attempts`) prevent infinite loops, halting with `HarnessStatus.LIMIT_REACHED`.
6. **Safe observations & events:** `StructuredObservation` and `RunEvent` scrub all raw stdout, stderr, passwords, tokens, and forbidden metadata substrings.
7. **Cross-run isolation:** Context assembly rejects mismatched `run_id` references, transitioning the harness to `FAILED`.

---

## 3. Test Scenarios Summary

The test suite in `tests/test_agent_harness.py` validates 26 distinct scenarios:

| Scenario | Objective | Result |
| :--- | :--- | :--- |
| **A: Single Successful Action** | Authorizes and executes a single action cleanly within bounds | **PASS** |
| **B: Multi-Iteration Successful Loop** | Full cycle: `INITIAL` → `EXECUTE` → `VERIFY` → `COMPLETE` | **PASS** |
| **C: Permission Denial** | Command outside allowed set halts without subprocess invocation | **PASS** |
| **D: Approval Waiting** | Unresolved human approval halts execution and transitions to `WAITING` | **PASS** |
| **E: Approval Rejection** | Explicit rejection halts execution safely with `FAILED` status | **PASS** |
| **F: Execution Failure** | Non-zero exit code produces execution failure and halts progress | **PASS** |
| **G: Verification Failure** | Failed verification condition halts acceptance progress | **PASS** |
| **H: Revision Trigger** | Failed verification triggers revision request and attempt increment | **PASS** |
| **I: Acceptance Success** | Passing verifications lead to acceptance `PASS` and `COMPLETE_RUN` | **PASS** |
| **J: Acceptance Failure** | Missing verification fails acceptance and prevents completion | **PASS** |
| **K: Unknown Decision Rejection** | Malformed or unauthorized decision types trigger safe failure | **PASS** |
| **L: Max Iteration Limit** | Harness strictly halts when `max_iterations` is reached | **PASS** |
| **M: Max Action Limit** | Harness strictly halts when `max_actions` is reached | **PASS** |
| **N: No Infinite Loop Guarantee** | Looping/uncooperative provider terminates safely at configured bounds | **PASS** |
| **O: One Action Per Iteration** | Exactly one authoritative action is executed and observed per iteration | **PASS** |
| **P: Decision Cannot Bypass Permission** | Adversarial decision cannot force unpermitted command execution | **PASS** |
| **Q: Decision Cannot Bypass Approval** | Adversarial decision cannot execute when approval is pending | **PASS** |
| **R: Decision Cannot Bypass ExecutionPolicy** | `ExecutionPolicy` denies forbidden binaries despite decision demand | **PASS** |
| **S: COMPLETE Cannot Bypass Acceptance** | Adversarial `COMPLETE` decision fails validation if acceptance not `PASS` | **PASS** |
| **T: ProjectState Authoritative** | `ProjectState` is derived from artifacts, never fabricated by harness | **PASS** |
| **U: Context Fingerprint Deterministic** | Identical inputs produce bit-identical context fingerprints | **PASS** |
| **V: Harness State Immutability** | `HarnessState` and `StructuredObservation` are frozen/immutable | **PASS** |
| **W: Trace Lifecycle Ordering** | RunTrace sequence numbers are strictly monotonic and sequential | **PASS** |
| **X: Cross-Run Rejection** | Foreign `run_id` in input state halts harness safely | **PASS** |
| **Y: Observation Privacy** | Raw stdout/stderr/secrets/passwords are scrubbed from observations | **PASS** |
| **Z: Purity & Side-Effect Freedom** | Harness control logic performs zero network or socket operations | **PASS** |

---

# Forge AI — Этап 12: Bounded Agent Harness / Controlled Run Loop v0.1 — русская версия

**Статус:** РЕАЛИЗОВАНО И ПРОТЕСТИРОВАНО

**Область действия:** Детерминированный runtime-harness агента, координирующий управляемый цикл выполнения (`AgentHarness`, `AgentHarnessPolicy`, `HarnessPhase`, `HarnessStatus`, `HarnessState`, `StructuredObservation`, `HarnessRequest`, `HarnessResult`). Harness организует существующие контракты Forge в ограниченный детерминированный цикл:
`OBSERVE` → `ASSEMBLE_CONTEXT` → `DECIDE` → `VALIDATE_DECISION` → `AUTHORIZE` → `ACT` → `OBSERVE_RESULT` → `UPDATE_STATE` → `REPEAT / COMPLETE`.

---

## 1. Краткое описание

Этап 12 внедряет первый детерминированный Agent Harness и контролируемый Run Loop в Forge AI. Harness связывает существующие базовые контракты Forge в ограниченный цикл выполнения без добавления неограниченного автономного поведения, внешних AI-провайдеров, LLM, постоянного хранения, сети или UI.

Harness выступает координатором, строго соблюдая установленную иерархию полномочий:
- **Decision Layer** (`DeterministicDecisionProvider`, `DecisionContextAssembler`, `validate_decision`) остаётся строго **рекомендательным**.
- **Границы безопасности** (`PermissionPolicy`, `ApprovalPolicy`, `ApprovalResolver`, `ExecutionPolicy`, `ExecutionCoordinator`) остаются строго **авторитетными**; решения не могут обходить проверку разрешений или требования подтверждения человеком.
- **Верификация и приёмка** (`WorkspaceVerifier`, `AcceptanceGate`) остаются строго **авторитетными**; решения не могут принудительно завершить запуск (`COMPLETE_RUN`) без прохождения критериев приёмки.
- **Состояние проекта** (`ProjectState`, `derive_project_state`) остаётся строго **авторитетным**; переходы состояний выводятся из проверенных фактов и артефактов попыток, а не фабрикуются средой выполнения.

Комплексный набор из 26 тестов (модульных и состязательных) в `tests/test_agent_harness.py` проверяет все фазы жизненного цикла, границы политик, неизменяемость, фильтрацию секретов, межпрогоночную изоляцию и обработку отказов. Все 26 тестов harness и все 476 тестов проекта успешно проходят.

---

## 2. Архитектура и жизненный цикл

### 2.1 Контролируемый цикл Run Loop

Каждая итерация Agent Harness выполняет строгую последовательность фаз:

1. **OBSERVE**: Осмотр текущей итерации и состояния проекта.
2. **CONTEXT**: Сборка ограниченного конверта контекста `DecisionContextEnvelope`.
3. **DECIDE**: Рекомендация типизированного решения от `DecisionProvider`.
4. **VALIDATE**: Валидация решения относительно запроса и состояния проекта.
5. **AUTHORIZE**: Проверка прав доступа через `PermissionPolicy`, `ApprovalPolicy` и `ExecutionPolicy`.
6. **ACT**: Выполнение не более одного авторитетного действия за итерацию.
7. **OBSERVE_RESULT**: Формирование безопасного наблюдения `StructuredObservation` без утечки секретов.
8. **UPDATE_STATE**: Детерминированный вывод обновлённого `ProjectState`.
9. **REPEAT / COMPLETE**: Завершение при достижении терминального состояния или переход к следующей итерации.

### 2.2 Ненарушаемые инварианты

1. **Не более одного действия за итерацию**: за цикл выполняется ровно одно действие.
2. **Невозможность обхода политик**: запуск процессов осуществляется исключительно через `ExecutionCoordinator`.
3. **Остановка при необходимости подтверждения**: при `ApprovalState.REQUIRED` запуск переходит в `HarnessPhase.WAITING`.
4. **Завершение только при прохождении приёмки**: `COMPLETE_RUN` разрешается только при статусе приёмки `PASS`.
5. **Жёсткие ограничения ресурсов**: лимиты итераций и действий исключают бесконечные циклы.
6. **Безопасность наблюдений и событий**: сырые потоки stdout/stderr и секреты исключаются из наблюдений и трассировки.
7. **Изоляция прогонов**: несоответствие `run_id` приводит к безопасному завершению со статусом `FAILED`.

### 2.3 Непреодолимые инварианты

1. **Не более одного авторитетного действия за итерацию:** harness выполняет ровно одно
   действие (`EXECUTE`, `RUN_VERIFICATION`, `REQUEST_REVISION`, `COMPLETE_RUN`,
   `FAIL_RUN` или `WAIT_FOR_APPROVAL`) за цикл.
2. **Сохранение полномочий:** выполнение подпроцессов диспетчеризуется исключительно
   через `ExecutionCoordinator`. Ни один подпроцесс никогда не запускается harness
   напрямую.
3. **Сохранение границы approval:** когда требуется одобрение человека
   (`ApprovalState.REQUIRED`), выполнение приостанавливается, а harness переходит в
   `HarnessPhase.WAITING` со статусом `WAITING_FOR_APPROVAL`.
4. **Сохранение границы acceptance:** решение, предлагающее `COMPLETE_RUN`,
   авторизуется только если `acceptance_result` существует и равен
   `AcceptanceStatus.PASS`.
5. **Консервативные границы:** строгие лимиты (`max_iterations`, `max_actions`,
   `max_execution_attempts`, `max_revision_attempts`) предотвращают бесконечные циклы,
   останавливаясь со статусом `HarnessStatus.LIMIT_REACHED`.
6. **Безопасные наблюдения и события:** `StructuredObservation` и `RunEvent` очищают
   все сырые stdout, stderr, пароли, токены и запрещённые подстроки метаданных.
7. **Изоляция между запусками:** сборка контекста отклоняет ссылки с несовпадающим
   `run_id`, переводя harness в `FAILED`.

---

## 3. Сводка тестовых сценариев

Набор тестов в `tests/test_agent_harness.py` проверяет 26 отдельных сценариев:

| Сценарий | Цель | Результат |
| :--- | :--- | :--- |
| **A: Single Successful Action** | Авторизует и чисто выполняет одно действие в границах | **PASS** |
| **B: Multi-Iteration Successful Loop** | Полный цикл: `INITIAL` -> `EXECUTE` -> `VERIFY` -> `COMPLETE` | **PASS** |
| **C: Permission Denial** | Команда вне разрешённого набора останавливается без запуска подпроцесса | **PASS** |
| **D: Approval Waiting** | Неразрешённое одобрение человека останавливает выполнение и переводит в `WAITING` | **PASS** |
| **E: Approval Rejection** | Явный отказ безопасно останавливает выполнение со статусом `FAILED` | **PASS** |
| **F: Execution Failure** | Ненулевой код возврата даёт отказ выполнения и останавливает продвижение | **PASS** |
| **G: Verification Failure** | Неуспешное условие верификации останавливает продвижение приёмки | **PASS** |
| **H: Revision Trigger** | Неуспешная верификация запускает запрос ревизии и увеличение номера попытки | **PASS** |
| **I: Acceptance Success** | Успешные верификации приводят к приёмке `PASS` и `COMPLETE_RUN` | **PASS** |
| **J: Acceptance Failure** | Отсутствующая верификация проваливает приёмку и предотвращает завершение | **PASS** |
| **K: Unknown Decision Rejection** | Некорректные или неавторизованные типы решений вызывают безопасный отказ | **PASS** |
| **L: Max Iteration Limit** | Harness строго останавливается при достижении `max_iterations` | **PASS** |
| **M: Max Action Limit** | Harness строго останавливается при достижении `max_actions` | **PASS** |
| **N: No Infinite Loop Guarantee** | Зацикленный/некооперативный провайдер безопасно завершается на настроенных границах | **PASS** |
| **O: One Action Per Iteration** | За итерацию выполняется ровно одно авторитетное действие и наблюдается ровно одно | **PASS** |
| **P: Decision Cannot Bypass Permission** | Враждебное решение не может вынудить выполнить неразрешённую команду | **PASS** |
| **Q: Decision Cannot Bypass Approval** | Враждебное решение не может выполниться при ожидающем одобрении | **PASS** |
| **R: Decision Cannot Bypass ExecutionPolicy** | `ExecutionPolicy` отклоняет запрещённые бинарники вопреки требованию решения | **PASS** |
| **S: COMPLETE Cannot Bypass Acceptance** | Враждебное решение `COMPLETE` не проходит валидацию, если приёмка не `PASS` | **PASS** |
| **T: ProjectState Authoritative** | `ProjectState` выводится из артефактов и никогда не фабрикуется harness | **PASS** |
| **U: Context Fingerprint Deterministic** | Идентичные входы дают побитово идентичные fingerprint контекста | **PASS** |
| **V: Harness State Immutability** | `HarnessState` и `StructuredObservation` неизменяемы (frozen) | **PASS** |
| **W: Trace Lifecycle Ordering** | Порядковые номера RunTrace строго монотонны и последовательны | **PASS** |
| **X: Cross-Run Rejection** | Чужой `run_id` во входном состоянии безопасно останавливает harness | **PASS** |
| **Y: Observation Privacy** | Сырые stdout/stderr/секреты/пароли вычищаются из наблюдений | **PASS** |
| **Z: Purity & Side-Effect Freedom** | Логика управления harness не выполняет ни одной сетевой или сокетной операции | **PASS** |
