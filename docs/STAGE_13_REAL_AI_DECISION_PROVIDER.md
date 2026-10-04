# Forge AI — Stage 13 Real AI Decision Provider v0.1

**Status:** IMPLEMENTED AND VERIFIED

**Scope:** Advisory AI Decision Provider integrating real LLM inference with the Decision Layer and Agent Harness (`AIDecisionProvider` in `app/decision/ai_provider.py`). The provider composes with Forge's existing provider layer (`OpenAIProvider` from `app/agents/providers/openai.py`) without granting execution authority to the model.

---

## 1. Executive Summary

Stage 13 introduces the first real AI Decision Provider in Forge AI. The provider connects an LLM into the existing Decision Layer and Agent Harness to recommend control-flow actions based on structured run context.

The implementation strictly maintains the core architectural invariant:
$$\text{AI recommendation} \neq \text{authorization} \neq \text{execution}$$

The AI model functions purely as an **advisory decision engine**. It is given zero execution authority, cannot invoke tools, cannot spawn subprocesses, cannot access the filesystem or network (outside the model inference call itself), and cannot modify ProjectState. All authoritative checks remain with Forge's foundational components:
- `validate_decision` validates decision types, actions, attempts, and acceptance compatibility.
- `PermissionPolicy` enforces command allowlists and sandboxing.
- `ApprovalPolicy` and `ApprovalResolver` gate sensitive operations behind human approval.
- `ExecutionPolicy` and `ExecutionCoordinator` authoritatively execute permitted actions.
- `WorkspaceVerifier` and `AcceptanceGate` verify requirements and determine acceptance independently.
- `derive_project_state` authoritatively tracks project state.
- `AgentHarness` enforces conservative iteration and action bounds.

A test suite of 12 unit and adversarial scenarios in `tests/test_ai_decision_provider.py` verifies all failure modes, security boundaries, determinism, privacy guarantees, and harness interoperability. All 12 Stage 13 tests and all 488 project-wide tests pass cleanly.

---

## 2. Architecture & Design

### 2.1 Provider Composition

`AIDecisionProvider` composes directly with the existing provider abstraction (`app.agents.providers.base.Provider`), defaulting to `OpenAIProvider`. No new API clients or HTTP transport layers were created, avoiding code duplication and leveraging Forge's existing credential management via `SecretStore`.

```
DecisionRequest / DecisionContextEnvelope
                  │
                  ▼
         [AIDecisionProvider]
                  │
       1. build_prompt() (safe, normalized, secret-free)
                  │
                  ▼
         [OpenAIProvider / Provider]
                  │
       2. generate(ProviderRequest)
                  │
                  ▼
       3. Parse strict structured JSON output
                  │
       4. Map to DecisionType & DecisionAction (fail-closed on unknown)
                  │
       5. validate_decision() check
                  │
                  ▼
          Advisory Decision
                  │
                  ▼
           [AgentHarness]
       6. Validate Decision (rejection on invalid/premature)
       7. Authorize (PermissionPolicy, ApprovalPolicy, ExecutionPolicy)
       8. Execute via ExecutionCoordinator (only if authorized)
```

### 2.2 Minimal, Normalized Prompt Construction

The prompt builder (`build_prompt`) produces a bounded, deterministic text representation of the current execution state:
- **Identification:** `run_id`, `attempt_number`, `task_id`.
- **State & Status:** `project_state_status`, `acceptance_status`, `verification_status`.
- **Bounds & Constraints:** Deterministically sorted `blocking_conditions` and `available_actions`.
- **Context Filtering:** Only public/internal context items with safe sensitivity are included. Any item containing forbidden metadata substrings (`secret`, `password`, `token`, `api_key`, `credential`, `stdout`, `stderr`) is strictly excluded.
- **Strict Schema Definition:** Enforces that the model response must be a single JSON object with `decision_type`, `action`, `reason_code`, `rationale`, and `confidence`.

### 2.3 Strict Fail-Closed Semantics

The provider implements fail-closed handling at every step:
- **Malformed JSON:** If the model returns unparseable text or conversational responses, the provider returns a fail-closed `Decision` (`DecisionType.FAIL`, `DecisionAction.FAIL_RUN`, `reason_code="malformed_json"`).
- **Unknown Actions / Types:** Unsupported or fabricated actions (e.g. `NUKE_ENVIRONMENT`) map to a fail-closed `Decision` with `reason_code="unknown_action"`.
- **Provider Outages / Exceptions:** Upstream network errors, timeouts, or rate limits are caught safely, returning `FAIL_RUN` with `reason_code="provider_failure"` without leaking API keys or internal stack traces.
- **Premature Completion:** If the model recommends `COMPLETE_RUN` before acceptance criteria pass, `validate_decision` rejects it with `premature_completion:acceptance_not_passed`, halting the harness safely.
- **Permission & Approval Denials:** If the model recommends `EXECUTE`, the harness authorizer enforces permission allowlists and human approval independently.

---

## 3. Test Scenarios Summary

The test suite in `tests/test_ai_decision_provider.py` verifies 12 comprehensive scenarios:

| Scenario | Objective | Result |
| :--- | :--- | :--- |
| **A: Valid AI Decision** | Parses clean JSON, validates decision, executes via harness | **PASS** |
| **B: Malformed JSON** | Conversational / non-JSON model output triggers fail-closed `FAIL_RUN` | **PASS** |
| **C: Unknown Action** | Non-existent or hostile actions fail closed safely | **PASS** |
| **D: Premature COMPLETE_RUN** | Validator rejects completion when acceptance is not pass | **PASS** |
| **E: EXECUTE with Permission DENY** | PermissionPolicy denies unallowlisted commands recommended by AI | **PASS** |
| **F: Approval Required** | ApprovalPolicy halts execution in `WAITING` despite AI recommendation | **PASS** |
| **G: Provider Failure** | Upstream API exceptions fail closed safely without crash or leak | **PASS** |
| **H: Deterministic Provider Operational** | `DeterministicDecisionProvider` remains completely intact and functional | **PASS** |
| **I: No Secret Leakage** | Passwords, tokens, and keys are scrubbed from prompts and metadata | **PASS** |
| **J: Normalized Prompt Structure** | Identical inputs produce bit-identical, sorted prompt strings | **PASS** |
| **K: No Direct Tool Execution** | AI Decision Provider has no execution methods and zero filesystem side-effects | **PASS** |
| **L: Cross-Run Isolation** | Decisions and validation enforce strict `run_id` binding | **PASS** |

---

# Forge AI — Этап 13: Real AI Decision Provider v0.1 (Русская версия)

**Статус:** РЕАЛИЗОВАНО И ПРОТЕСТИРОВАНО

**Область действия:** Консультативный AI Decision Provider, интегрирующий реальную LLM-генерацию с Decision Layer и Agent Harness (`AIDecisionProvider` в `app/decision/ai_provider.py`). Провайдер компонуется с существующим уровнем провайдеров Forge (`OpenAIProvider` из `app/agents/providers/openai.py`) без передачи модели полномочий на выполнение.

---

## 1. Краткое описание

Этап 13 представляет первый реальный AI Decision Provider в Forge AI. Провайдер подключает LLM к существующим слоям Decision Layer и Agent Harness для рекомендации действий управления потоком на основе структурированного контекста выполнения.

Реализация строго поддерживает ключевой архитектурный инвариант:
$$\text{Рекомендация AI} \neq \text{авторизация} \neq \text{выполнение}$$

AI-модель выступает исключительно как **рекомендательный механизм**. Ей не предоставляются полномочия на выполнение: она не может вызывать инструменты, порождать подпроцессы, обращаться к файловой системе или сети (за пределами вызова API самой модели) и не может изменять `ProjectState`. Все авторитетные проверки остаются за базовыми компонентами Forge:
- `validate_decision` проверяет типы решений, действия, номера попыток и совместимость с приёмкой.
- `PermissionPolicy` обеспечивает соблюдение списков разрешённых команд и изоляцию.
- `ApprovalPolicy` и `ApprovalResolver` блокируют чувствительные операции до подтверждения человеком.
- `ExecutionPolicy` и `ExecutionCoordinator` авторитетно выполняют только разрешённые действия.
- `WorkspaceVerifier` и `AcceptanceGate` независимо проверяют требования и определяют приёмку.
- `derive_project_state` авторитетно отслеживает состояние проекта.
- `AgentHarness` обеспечивает соблюдение консервативных лимитов итераций и действий.

Набор из 12 модульных и состязательных тестов в `tests/test_ai_decision_provider.py` проверяет все режимы отказов, границы безопасности, детерминизм, гарантии конфиденциальности и взаимодействие с harness. Все 12 тестов Этапа 13 и все 488 тестов проекта успешно проходят.

---

## 2. Архитектура и дизайн

### 2.1 Композиция с провайдером

`AIDecisionProvider` напрямую использует существующую абстракцию провайдеров (`app.agents.providers.base.Provider`), по умолчанию выбирая `OpenAIProvider`. Новые HTTP-клиенты не создавались, что исключает дублирование кода и использует существующее управление секретами через `SecretStore`.

### 2.2 Построение нормализованного и безопасного промпта

Построитель промптов (`build_prompt`) формирует ограниченное и детерминированное текстовое представление текущего состояния:
- **Идентификация:** `run_id`, `attempt_number`, `task_id`.
- **Состояние и статус:** `project_state_status`, `acceptance_status`, `verification_status`.
- **Ограничения:** детерминированно отсортированные `blocking_conditions` и `available_actions`.
- **Фильтрация контекста:** включаются только элементы с публичной/внутренней чувствительностью. Элементы, содержащие запрещённые подстроки (`secret`, `password`, `token`, `api_key`, `credential`, `stdout`, `stderr`), полностью исключаются.
- **Строгая схема ответа:** модель обязуется возвращать исключительно одиночный JSON-объект с полями `decision_type`, `action`, `reason_code`, `rationale` и `confidence`.

### 2.3 Принцип безопасного отказа (Fail-Closed)

- **Невалидный JSON:** при получении непарсируемого или разговорного текста провайдер возвращает безопасное решение `FAIL_RUN` (`reason_code="malformed_json"`).
- **Неизвестные действия:** неподдерживаемые или вымышленные действия приводят к безопасному отказу с `reason_code="unknown_action"`.
- **Ошибки провайдера:** сетевые сбои, тайм-ауты или превышения лимитов API перехватываются, возвращая `FAIL_RUN` с `reason_code="provider_failure"` без утечки секретов и стека вызовов.
- **Преждевременное завершение:** попытка модели вернуть `COMPLETE_RUN` до успешной приёмки отклоняется `validate_decision` с кодом ошибки `premature_completion:acceptance_not_passed`.
- **Отказ в разрешениях:** при рекомендации `EXECUTE` авторизационный слой harness независимо применяет `PermissionPolicy` и требует подтверждения человеком при необходимости.
