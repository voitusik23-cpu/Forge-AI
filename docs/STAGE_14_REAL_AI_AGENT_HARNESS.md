# Forge AI — Stage 14 Real AI Agent Harness Integration v0.1

**Status:** IMPLEMENTED AND VERIFIED

**Scope:** Integration of `AIDecisionProvider` with the deterministic `AgentHarness` to prove a bounded, multi-turn AI-advised engineering run loop. Demonstrates the complete flow:
$$\text{Task} \to \text{AgentHarness} \to \text{Context} \to \text{AIDecisionProvider} \to \text{Decision Validator} \to \text{Authorization} \to \text{Execution} \to \text{Observation} \to \text{Verification} \to \text{ProjectState} \to \text{Next Decision} \to \text{Terminal Result}$$

---

## 1. Executive Summary

Stage 14 establishes Forge AI's first complete, bounded autonomous runtime loop powered by real AI recommendations while strictly preserving authoritative boundaries.

The core architectural invariant remains inviolable:
$$\text{AI recommendation} \neq \text{authorization} \neq \text{execution}$$

Under this model:
- The AI Decision Provider operates strictly as an **advisory control-flow consultant**.
- The AI never executes tools directly, never accesses the filesystem, and never modifies project state.
- All authorizations (`PermissionPolicy`, `ApprovalPolicy`, `ExecutionPolicy`), verifications (`WorkspaceVerifier`), acceptance gates (`AcceptanceGate`), and state updates (`derive_project_state`) remain deterministic, authoritative Forge components.
- The runtime loop is strictly bounded by `AgentHarnessPolicy` limits (`max_iterations`, `max_actions`, `max_execution_attempts`, `max_revision_attempts`).
- Any malformed AI response, unauthorized action, upstream provider failure, permission denial, or ungranted approval fails closed deterministically.

A dedicated integration test suite of 16 scenarios in `tests/test_agent_harness_ai_integration.py` validates the entire lifecycle, multi-turn state progression, security enforcement, failure modes, secret safety, and cross-run isolation. All 15 automated integration tests pass (1 optional live network smoke test skipped when API keys are absent), bringing total repository tests to 504 (502 passed, 2 skipped, 0 failed).

---

## 2. Architecture & Flow

### 2.1 The Controlled Engineering Lifecycle

The harness coordinates an explicit 9-phase lifecycle per iteration:

```
                  ┌──────────────────────────────────────────────┐
                  │                Harness Request               │
                  └──────────────────────┬───────────────────────┘
                                         │
    ┌────────────────────────────────────▼───────────────────────────────────┐
    │                                                                        │
    │  PHASE 1: OBSERVE                                                      │
    │  Read Workspace snapshot, prior execution results & verifications      │
    │                                                                        │
    │  PHASE 2: ASSEMBLE_CONTEXT                                             │
    │  Construct DecisionContextEnvelope (filtered, secret-free, sanitized)  │
    │                                                                        │
    │  PHASE 3: DECIDE (Advisory AI Provider)                                │
    │  AIDecisionProvider builds prompt, queries LLM, parses strict JSON     │
    │                                                                        │
    │  PHASE 4: VALIDATE_DECISION (Authoritative)                            │
    │  validate_decision checks action legality & premature completion       │
    │                                                                        │
    │  PHASE 5: AUTHORIZE (Authoritative)                                    │
    │  PermissionPolicy (allowlist) & ApprovalPolicy (human gate)            │
    │                                                                        │
    │  PHASE 6: ACT (Authoritative Coordinator)                              │
    │  ExecutionCoordinator runs tool/command, or Verifier/Acceptance runs   │
    │                                                                        │
    │  PHASE 7: OBSERVE_RESULT                                               │
    │  Capture execution/verification output into StructuredObservation      │
    │                                                                        │
    │  PHASE 8: UPDATE_STATE                                                 │
    │  derive_project_state derives authoritative ProjectState status        │
    │                                                                        │
    │  PHASE 9: REPEAT OR COMPLETE                                           │
    │  Check bounds, terminal conditions (COMPLETE, WAITING, FAILED)         │
    │                                                                        │
    └────────────────────────────────────┬───────────────────────────────────┘
                                         │
                  ┌──────────────────────▼───────────────────────┐
                  │                 HarnessResult                │
                  │   (final_state, observations, trace events)  │
                  └──────────────────────────────────────────────┘
```

### 2.2 Authority Boundaries

| Subsystem | Authority Level | Role & Invariants |
| :--- | :--- | :--- |
| **AIDecisionProvider** | Advisory only | Proposes next action (`EXECUTE`, `RUN_VERIFICATION`, `REQUEST_REVISION`, `WAIT_FOR_APPROVAL`, `COMPLETE_RUN`, `FAIL_RUN`). Has zero tool access or filesystem permissions. |
| **Decision Validator** | Authoritative | Rejects unsupported actions, invalid decision types, or premature completion before `acceptance_status == "pass"`. |
| **PermissionPolicy** | Authoritative | Blocks any execution command not explicitly allowlisted in `allowed_execution_commands`. |
| **ApprovalPolicy** | Authoritative | Halts execution into `WAITING_FOR_APPROVAL` if human authorization is required and ungranted. |
| **ExecutionCoordinator** | Authoritative | Executes permitted commands in isolated subprocesses; captures bounded results. |
| **WorkspaceVerifier** | Authoritative | Verifies physical workspace artifacts and conditions against specification expectations. |
| **AcceptanceGate** | Authoritative | Evaluates whether criteria pass; sets authoritative `acceptance_status`. |
| **ProjectState** | Authoritative | Derives project health (`INITIAL`, `IN_PROGRESS`, `CHANGED`, `VERIFIED`, `ACCEPTED`, `FAILED`) purely from verified facts. |
| **AgentHarness** | Authoritative | Strictly enforces limits on iterations, actions, executions, and revisions; halts runaway loops. |

---

## 3. What Was Proven vs. What Remains Unproven

### 3.1 Proven Invariants

1. **Multi-Turn Context Awareness:** Proved in Scenario G that the AI model observes evolving project state across turns:
   - Turn 1: Observes `INITIAL` $\to$ recommends `EXECUTE`.
   - Turn 2: Observes `CHANGED` $\to$ recommends `RUN_VERIFICATION`.
   - Turn 3: Observes `ACCEPTED` (with passing acceptance status) $\to$ recommends `COMPLETE_RUN`.
2. **Authoritative Gate Enforcement:**
   - Proved in Scenario C that AI recommending `EXECUTE` with an unauthorized command is blocked by `PermissionPolicy` and recorded as `permission_denied`.
   - Proved in Scenario D that AI recommending `EXECUTE` when approval is required halts in `WAITING_FOR_APPROVAL`.
   - Proved in Scenario H that AI recommending `COMPLETE_RUN` before acceptance passes is rejected by `validate_decision` and transitions harness to `FAILED`.
3. **Fail-Closed Resilience:**
   - Proved in Scenario I that malformed or conversational model output safely fails closed into `FAILED`.
   - Proved in Scenario J that upstream model exceptions (timeouts, rate limits, network outages) fail closed without leaking credentials or crashing.
4. **Runaway Loop Protection:** Proved in Scenario K that an uncooperative or looping model is bounded strictly by `max_iterations`, terminating cleanly with `LIMIT_REACHED`.
5. **Secret Safety:** Proved in Scenario M that sensitive metadata (`api_key`, `token`, `secret`, `password`) is filtered from AI prompts, observations, and run traces.
6. **Cross-Run Isolation:** Proved in Scenario L that cross-run context mismatches fail safely.
7. **Zero Tool Execution Capability:** Proved in Scenario N via reflection that `AIDecisionProvider` exposes zero tool or execution methods.
8. **Backward Compatibility:** Proved in Scenario O that `DeterministicDecisionProvider` functions identically and without regression in the harness.

### 3.2 What Remains Unproven (Out of Scope for Stage 14)

- **Unbounded Multi-Goal Tasks:** Complex planning requiring long-horizon multi-step decomposition.
- **Model Context Protocol (MCP):** Dynamic tool servers and external tool discovery.
- **Persistent State Storage:** Database persistence of run state across process restarts.
- **Version Control System Integration:** Automatic Git commit, branch creation, or GitHub pull requests.
- **User Interface:** Web or CLI interactive shells for monitoring live harness execution.
- **Parallel Subagent Orchestration:** Concurrent multi-agent execution threads.

---

## 4. Test Results

All 16 test cases in `tests/test_agent_harness_ai_integration.py`:

| Test Scenario | Verification Objective | Result |
| :--- | :--- | :--- |
| **Scenario A** | Real AI decision enters the harness loop and records trace events | **PASS** |
| **Scenario B** | Valid AI `EXECUTE` recommendation triggers authorization and execution | **PASS** |
| **Scenario C** | AI recommendation cannot bypass `PermissionPolicy` allowlist | **PASS** |
| **Scenario D** | AI recommendation cannot bypass human approval gate (`ApprovalPolicy`) | **PASS** |
| **Scenario E** | Execution result is transformed into a secret-safe `StructuredObservation` | **PASS** |
| **Scenario F** | Verification result produces a bounded `StructuredObservation` | **PASS** |
| **Scenario G** | Multi-turn controlled loop (`INITIAL` $\to$ `CHANGED` $\to$ `ACCEPTED` $\to$ complete) | **PASS** |
| **Scenario H** | AI proposing premature `COMPLETE_RUN` is rejected by validator | **PASS** |
| **Scenario I** | Conversational or corrupt AI responses fail closed in `FAILED` | **PASS** |
| **Scenario J** | Upstream model provider exceptions halt harness safely without crash | **PASS** |
| **Scenario K** | Harness strictly caps looping AI decisions at configured bounds (`LIMIT_REACHED`) | **PASS** |
| **Scenario L** | Foreign context with mismatched `run_id` causes a safe failure | **PASS** |
| **Scenario M** | Secrets present in metadata are never leaked into prompt, observations, or trace | **PASS** |
| **Scenario N** | `AIDecisionProvider` contains zero tool execution methods | **PASS** |
| **Scenario O** | Existing `DeterministicDecisionProvider` path works without regression | **PASS** |
| **Optional Smoke** | Live OpenAI API connectivity test (runs only when API key is present) | **SKIPPED** |

**Full Test Suite Summary:**
- Stage 14 Integration: 15 passed, 1 skipped.
- Total Repository: 504 tests, 502 passed, 2 skipped, 0 failed.
- Compilation: `python -m compileall app tests` completed with code 0.

---

## 5. Known Limitations

1. **Single Action per Iteration:** The harness executes exactly one authoritative action per iteration, preserving maximum control at the expense of throughput.
2. **Synchronous In-Memory Execution:** The harness runs synchronously within a single process. Run state is maintained in-memory.
3. **Structured Single-Action Schema:** The model is constrained to recommend a single discrete control action from the canonical `DecisionAction` enum.

---

# Forge AI — Интеграция реального AI-провайдера решений с Agent Harness v0.1 (Русская версия)

**Статус:** РЕАЛИЗОВАНО И ПРОВЕРЕНО

**Область действия:** Интеграция `AIDecisionProvider` с детерминированным циклом `AgentHarness` для демонстрации ограниченного многошагового цикла инженерного выполнения с рекомендациями AI. Демонстрирует полный поток:
$$\text{Task} \to \text{AgentHarness} \to \text{Context} \to \text{AIDecisionProvider} \to \text{Валидатор решений} \to \text{Авторизация} \to \text{Выполнение} \to \text{Наблюдение} \to \text{Верификация} \to \text{ProjectState} \to \text{Следующее решение} \to \text{Итоговый результат}$$

---

## 1. Краткое резюме

Этап 14 формирует первый полноценный, ограниченный контур автономного выполнения Forge AI, управляемый рекомендациями реального AI-провайдера при строгом сохранении всех авторитетных границ.

Ключевой архитектурный инвариант остаётся нерушимым:
$$\text{Рекомендация AI} \neq \text{авторизация} \neq \text{выполнение}$$

В рамках этой модели:
- AI-провайдер решений действует исключительно как **совещательный консультант по потоку управления**.
- Модель AI не выполняет инструменты напрямую, не имеет доступа к файловой системе и не может изменять состояние проекта.
- Все проверки авторизации (`PermissionPolicy`, `ApprovalPolicy`, `ExecutionPolicy`), верификации (`WorkspaceVerifier`), приёмка (`AcceptanceGate`) и обновление состояния (`derive_project_state`) остаются детерминированными и авторитетными компонентами Forge.
- Цикл выполнения строго ограничен политикой `AgentHarnessPolicy` (`max_iterations`, `max_actions`, `max_execution_attempts`, `max_revision_attempts`).
- Любой некорректный ответ AI, несанкционированное действие, сбой внешнего провайдера, отказ в разрешениях или отсутствие согласования приводят к детерминированному безопасному завершению (fail-closed).

Специализированный набор интеграционных тестов из 16 сценариев в `tests/test_agent_harness_ai_integration.py` подтверждает весь жизненный цикл, многошаговое развитие состояния, соблюдение безопасности, обработку сбоев, защиту секретов и изоляцию между запусками. Все 15 автоматических интеграционных тестов успешно пройдены (1 опциональный тест с реальной сетью пропущен из-за отсутствия ключей API), общее число тестов репозитория достигло 504 (502 пройдены, 2 пропущены, 0 ошибок).

---

## 2. Архитектура и поток управления

### 2.1 Контролируемый инженерный жизненный цикл

Контур `AgentHarness` координирует 9 явных фаз на каждой итерации:

```
                  ┌──────────────────────────────────────────────┐
                  │                 Запрос к Harness             │
                  └──────────────────────┬───────────────────────┘
                                         │
    ┌────────────────────────────────────▼───────────────────────────────────┐
    │                                                                        │
    │  ФАЗА 1: OBSERVE                                                       │
    │  Чтение снимка рабочего пространства, результатов выполнения и проверок│
    │                                                                        │
    │  ФАЗА 2: ASSEMBLE_CONTEXT                                              │
    │  Формирование DecisionContextEnvelope (очищено от секретов и потоков)  │
    │                                                                        │
    │  ФАЗА 3: DECIDE (Совещательный AI-провайдер)                           │
    │  AIDecisionProvider строит промпт, запрашивает LLM, парсит строгий JSON│
    │                                                                        │
    │  ФАЗА 4: VALIDATE_DECISION (Авторитетная проверка)                     │
    │  validate_decision проверяет допустимость действия и раннее завершение │
    │                                                                        │
    │  ФАЗА 5: AUTHORIZE (Авторитетная проверка)                             │
    │  PermissionPolicy (список разрешений) и ApprovalPolicy (согласование)  │
    │                                                                        │
    │  ФАЗА 6: ACT (Авторитетный координатор)                                │
    │  ExecutionCoordinator запускает команду либо запускается верификация   │
    │                                                                        │
    │  ФАЗА 7: OBSERVE_RESULT                                               │
    │  Фиксация результата выполнения/проверки в StructuredObservation       │
    │                                                                        │
    │  ФАЗА 8: UPDATE_STATE                                                 │
    │  derive_project_state авторитетно вычисляет статус ProjectState        │
    │                                                                        │
    │  ФАЗА 9: REPEAT OR COMPLETE                                           │
    │  Проверка лимитов и терминальных состояний (COMPLETE, WAITING, FAILED) │
    │                                                                        │
    └────────────────────────────────────┬───────────────────────────────────┘
                                         │
                  ┌──────────────────────▼───────────────────────┐
                  │                 HarnessResult                │
                  │   (final_state, observations, trace events)  │
                  └──────────────────────────────────────────────┘
```

### 2.2 Границы полномочий

| Подсистема | Уровень полномочий | Роль и инварианты |
| :--- | :--- | :--- |
| **AIDecisionProvider** | Исключительно совещательный | Предлагает следующее действие (`EXECUTE`, `RUN_VERIFICATION`, `REQUEST_REVISION`, `WAIT_FOR_APPROVAL`, `COMPLETE_RUN`, `FAIL_RUN`). Не имеет доступа к инструментам и файловой системе. |
| **Валидатор решений** | Авторитетный | Отклоняет неподдерживаемые действия, некорректные типы решений или преждевременное завершение до `acceptance_status == "pass"`. |
| **PermissionPolicy** | Авторитетный | Блокирует любую команду, не входящую в явный белый список `allowed_execution_commands`. |
| **ApprovalPolicy** | Авторитетный | Приостанавливает выполнение в статус `WAITING_FOR_APPROVAL`, если требуется и не получено подтверждение оператора. |
| **ExecutionCoordinator** | Авторитетный | Выполняет разрешённые команды в изолированных подпроцессах; сохраняет ограниченный результат. |
| **WorkspaceVerifier** | Авторитетный | Проверяет физические артефакты и условия рабочей области в соответствии со спецификацией задачи. |
| **AcceptanceGate** | Авторитетный | Оценивает выполнение критериев приёмки; устанавливает авторитетный `acceptance_status`. |
| **ProjectState** | Авторитетный | Вычисляет состояние проекта (`INITIAL`, `IN_PROGRESS`, `CHANGED`, `VERIFIED`, `ACCEPTED`, `FAILED`) исключительно на основе фактов. |
| **AgentHarness** | Авторитетный | Строго соблюдает лимиты итераций, действий, попыток выполнения и ревизий; предотвращает бесконечные циклы. |

---

## 3. Что доказано и что остаётся недоказанным

### 3.1 Доказанные инварианты

1. **Многошаговое восприятие контекста:** В сценарии G доказано, что модель видит эволюцию состояния проекта между шагами:
   - Шаг 1: Видит `INITIAL` $\to$ рекомендует `EXECUTE`.
   - Шаг 2: Видит `CHANGED` $\to$ рекомендует `RUN_VERIFICATION`.
   - Шаг 3: Видит `ACCEPTED` (со статусом приёмки `pass`) $\to$ рекомендует `COMPLETE_RUN`.
2. **Соблюдение авторитетных ограничений:**
   - В сценарии C доказано, что рекомендация AI запустить неразрешённую команду блокируется `PermissionPolicy` с фиксацией `permission_denied`.
   - В сценарии D доказано, что рекомендация AI выполнить действие, требующее согласования, переводит контур в `WAITING_FOR_APPROVAL`.
   - В сценарии H доказано, что попытка AI преждевременно завершить запуск отклоняется `validate_decision`, переводя контур в `FAILED`.
3. **Безопасное завершение при сбоях (Fail-Closed):**
   - В сценарии I доказано, что некорректный или разговорный ответ модели безопасно приводит к `FAILED`.
   - В сценарии J доказано, что ошибки внешнего провайдера (таймауты, лимиты, сбои сети) безопасно останавливают запуск без утечки учётных данных и без аварийных падений.
4. **Защита от зацикливания:** В сценарии K доказано, что зацикленная или некооперативная модель строго ограничивается параметром `max_iterations`, завершая работу со статусом `LIMIT_REACHED`.
5. **Защита конфиденциальных данных:** В сценарии M доказано, что конфиденциальные метаданные (`api_key`, `token`, `secret`, `password`) фильтруются и не попадают в промпты, наблюдения или трассировку.
6. **Изоляция между запусками:** В сценарии L доказано, что несоответствие контекста по `run_id` приводит к безопасной остановке.
7. **Отсутствие прямого доступа к инструментам:** В сценарии N через рефлексию подтверждено, что `AIDecisionProvider` не имеет методов выполнения инструментов.
8. **Обратная совместимость:** В сценарии O подтверждено, что детерминированный провайдер решений (`DeterministicDecisionProvider`) работает в контуре без регрессий.

### 3.2 Что остаётся недоказанным (вне рамок Этапа 14)

- **Многоцелевые задачи без ограничений:** Сложное долгосрочное планирование с декомпозицией на множество шагов.
- **Model Context Protocol (MCP):** Динамические серверы инструментов и обнаружение сторонних API.
- **Персистентное хранилище состояния:** Сохранение состояния запусков в базе данных между перезапусками процесса.
- **Интеграция с системами контроля версий:** Автоматические коммиты в Git, создание веток или GitHub PR.
- **Пользовательский интерфейс:** Веб-интерфейс или интерактивный терминал для живого мониторинга.
- **Параллельная оркестрация подагентов:** Одновременное выполнение нескольких независимых агентов в параллельных потоках.

---

## 4. Результаты тестирования

Все 16 тестовых сценариев в `tests/test_agent_harness_ai_integration.py`:

| Сценарий | Цель проверки | Результат |
| :--- | :--- | :--- |
| **Сценарий A** | Реальное AI-решение поступает в контур harness и фиксируется в событиях трассы | **УСПЕХ** |
| **Сценарий B** | Корректное AI-решение `EXECUTE` проходит авторизацию и выполняется | **УСПЕХ** |
| **Сценарий C** | Рекомендация AI не может обойти белый список `PermissionPolicy` | **УСПЕХ** |
| **Сценарий D** | Рекомендация AI не может обойти ворота согласования человеком (`ApprovalPolicy`) | **УСПЕХ** |
| **Сценарий E** | Результат выполнения преобразуется в безопасное наблюдение `StructuredObservation` | **УСПЕХ** |
| **Сценарий F** | Результат верификации формирует ограниченное наблюдение `StructuredObservation` | **УСПЕХ** |
| **Сценарий G** | Многошаговый контролируемый цикл (`INITIAL` $\to$ `CHANGED` $\to$ `ACCEPTED` $\to$ complete) | **УСПЕХ** |
| **Сценарий H** | Попытка AI завершить запуск до прохождения приёмки отклоняется валидатором | **УСПЕХ** |
| **Сценарий I** | Разговорные или повреждённые ответы AI приводят к завершению в `FAILED` | **УСПЕХ** |
| **Сценарий J** | Исключения внешнего провайдера безопасно останавливают контур без сбоев | **УСПЕХ** |
| **Сценарий K** | Контур строго ограничивает зацикленные решения AI лимитом (`LIMIT_REACHED`) | **УСПЕХ** |
| **Сценарий L** | Чужой контекст с несовпадающим `run_id` вызывает безопасную остановку | **УСПЕХ** |
| **Сценарий M** | Секреты в метаданных не попадают в промпт, наблюдения и события трассы | **УСПЕХ** |
| **Сценарий N** | `AIDecisionProvider` не содержит методов прямого вызова инструментов | **УСПЕХ** |
| **Сценарий O** | Путь работы с `DeterministicDecisionProvider` функционирует без регрессий | **УСПЕХ** |
| **Опциональный Smoke** | Проверка подключения к живому OpenAI API (запускается при наличии ключа) | **ПРОПУЩЕН** |

**Сводка по полному набору тестов:**
- Интеграционные тесты Этапа 14: 15 пройдено, 1 пропущен.
- Полный набор репозитория: 504 теста, 502 пройдено, 2 пропущено, 0 ошибок.
- Компиляция: `python -m compileall app tests` завершена с кодом 0.

---

## 5. Известные ограничения

1. **Одно действие на итерацию:** Контур выполняет ровно одно авторитетное действие за шаг, сохраняя максимальный контроль за счёт снижения параллельной пропускной способности.
2. **Синхронное выполнение в памяти:** Контур выполняется синхронно в рамках одного процесса; состояние хранится в оперативной памяти.
3. **Строгая схема одного действия:** Модель ограничена рекомендацией одного дискретного действия из канонического перечисления `DecisionAction`.
