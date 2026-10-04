# Forge AI — Project Memory v0.1

## 1. Overview and Core Invariants

Project Memory v0.1 introduces structured, project-scoped engineering knowledge persistence across Engineering Runs in Forge AI. In contrast to ephemeral session state or full-history replay, Project Memory stores durable, validated facts, conventions, constraints, assumptions, and decision records tied directly to a specific `project_id`.

### Core Architectural Invariant

$$\text{Recommendation} \neq \text{Authorization} \neq \text{Approval} \neq \text{Execution} \neq \text{Verification}$$

In Forge AI, **Memory is Information, NOT Authority**.

- Memory items are advisory context ingested through `ContextSourceType.PROJECT_MEMORY`.
- Memory NEVER possesses authority to grant tool permissions, bypass approval gates, modify task requirements, mutate project state directly, or alter acceptance decisions.
- Forge enforces this authority boundary **structurally**, not via heuristic or NLP-based parsing. The decision loop strictly requires all actions to flow through `PermissionPolicy` and `ApprovalPolicy`. Natural language text inside memory (such as "permission granted" or "approved by owner") is treated as plain text data and has zero capability to alter security checks.

---

## 2. Data Models and Contracts

### 2.1 MemoryCategory

Project memories are classified into explicit, non-overlapping categories:

- `FACT`: Concrete technical or environment facts (e.g. database versions, local port assignments).
- `CONSTRAINT`: Explicit architectural or platform limitations.
- `ASSUMPTION`: Documented working assumptions pending verification.
- `DECISION_RECORD`: Architectural decisions agreed upon in prior runs.
- `OUTCOME`: Historical results of past attempts and benchmarks.
- `LESSON`: Known failure patterns or operational post-mortem learnings.

### 2.2 Lifecycle Status and Revisioning

- `ACTIVE`: The current, authoritative version of the memory.
- `SUPERSEDED`: Replaced by a newer revision. Points to `superseded_by`.
- `DEPRECATED`: Marked obsolete without a direct replacement.

Memory items follow an append-oriented revision model. When an active memory is updated via `supersede(...)`, the old item transitions to `SUPERSEDED`, and a new item is created with an incremented revision number and back-link.

### 2.3 MemoryProvenance

Every memory item requires full provenance:

- `source_type`: Originating context source (`ContextSourceType`).
- `source_id`: Specific identifier of the source (e.g. decision ID, task ID).
- `run_id`: Run ID where the memory originated.
- `actor`: Agent or human actor who authored or verified the item.
- `created_at`: ISO 8601 UTC timestamp.

### 2.4 Bounded Item Schema

- `title`: String, maximum 120 characters, non-empty.
- `content`: String, maximum 2000 characters, non-empty.
- `tags`: Sequence of strings, maximum 10 tags, each tag maximum 32 characters.
- `metadata`: Mapping of string keys to values, maximum 20 items, maximum value length 1000 characters.

---

## 3. Security and Secret Avoidance

`MemoryValidator` deterministically validates all incoming memory records before storage:

1. **Secret Pattern Scanning**: Rejects memory content containing API keys (`sk-...`, `ghp_...`, `xoxb-...`), Bearer tokens, or raw password definitions.
2. **Metadata Key Whitelisting**: Rejects metadata containing sensitive keys matching `_SENSITIVE_KEY_PATTERN` (e.g. `api_key`, `token`, `secret`, `password`, `auth`).
3. **Identifier Sanitization**: Enforces strict character sets (`[A-Za-z0-9_.:-]+`) for `project_id` and `memory_id` to prevent path traversal or injection.

---

## 4. In-Memory Store and Deterministic Querying

`InMemoryMemoryStore` implements thread-safe (`threading.RLock`), strictly partitioned storage:

- **Strict Project Partitioning**: Storage is indexed by `project_id`. Queries for `project_A` cannot view, search, or mutate `project_B` records under any circumstances.
- **Deterministic Filtering**: Supports exact filtering on `categories`, `status`, `tags` (conjunction match), and `min_trust_level`.
- **Deterministic Ordering**: Sorts candidates deterministically:
  1. `revision` descending
  2. `provenance.created_at` descending
  3. `memory_id` ascending
- **Strict Bounds**: Enforces `max_items` limit on retrieval to preserve context budget economy.
- **Explicit Trace Events**: Emits `EventType.MEMORY_RECORDED` on `put` / `record` and `EventType.MEMORY_REVISED` on `supersede`. As mandated by architecture, `MEMORY_RETRIEVED` is omitted in v0.1.

---

## 5. Context Assembly and Agent Harness Integration

Project Memory seamlessly integrates into the Agent Harness Run Loop:

1. **Phase 2 (CONTEXT)**: During context assembly, `AgentHarness` queries active memories for the run's project using `InMemoryMemoryStore.get_active_memory(project_id)`.
2. **Context Packaging**: `DecisionContextAssembler` formats each active memory item into a `ContextItem` with:
   - `source_type`: `ContextSourceType.PROJECT_MEMORY`
   - `sensitivity`: `ContextSensitivity.INTERNAL`
   - `trust_level`: Inherited from item (default `CONFIRMED`)
   - Bounded character representation
3. **Decoupled Execution**: The decision provider consumes the assembled context. However, the Harness strictly evaluates resulting decisions via `validate_decision`, `PermissionPolicy`, and `ApprovalPolicy`. Memory content has zero bypass capability.

---

## 6. What Was Excluded in v0.1 (Non-Goals)

- Vector databases and embedding models.
- Semantic similarity search or AI-based retrieval ranking.
- Natural-language authority detection or dynamic permission escalation.
- External database dependencies (remains pure in-memory with thread synchronization).

---

## 7. Verification Matrix

The test suite in `tests/test_project_memory.py` provides complete test coverage across 23 targeted test cases (A through V plus Harness integration):

- **A**: Valid memory creation across all 6 categories.
- **B**: Rejection of empty fields and bounds violations.
- **C**: Complete provenance preservation and round-trip verification.
- **D**: Strict project isolation (`project_A` cannot access `project_B`).
- **E**: Cross-run continuity for the same project.
- **F**: Cross-project isolation under identical categories and tags.
- **G**: Deterministic retrieval order across multiple invocations.
- **H**: Bounded retrieval (`max_items`).
- **I**: Multi-category and single-category filtering.
- **J**: Lifecycle status filtering (`ACTIVE`, `SUPERSEDED`, `DEPRECATED`).
- **K**: Tag conjunction filtering.
- **L**: Supersede and revision increment behavior, pointer updates, and `MEMORY_REVISED` event emission.
- **M**: Secret material scanning and rejection.
- **N**: Sensitive metadata key rejection.
- **O**: DecisionContextAssembler integration with `ContextSourceType.PROJECT_MEMORY`.
- **P**: Structural proof that memory cannot grant permissions.
- **Q**: Structural proof that memory cannot bypass human approval gates.
- **R**: Structural proof that memory cannot trigger unauthorized tool execution.
- **S**: Invariance of TaskSpecification requirements against memory content.
- **T**: Invariance of ProjectState derivation against memory content.
- **U**: Invariance of AcceptanceGate evaluations against memory claims.
- **V**: Deterministic output repeatability across multiple query calls.
- **Harness Integration**: End-to-end integration test of `AgentHarness` injecting project memory during Phase 2 (CONTEXT).

---

# Forge AI — Проектная память v0.1 (Русская версия)

## 1. Обзор и ключевые инварианты

Project Memory v0.1 реализует структурированное долговременное хранение инженерных знаний проекта между запусками (Engineering Runs) в Forge AI. В отличие от эфемерного состояния сессии или воспроизведения всей истории, проектная память сохраняет проверенные факты, соглашения, ограничения, допущения и записи решений, привязанные строго к конкретному `project_id`.

### Ключевой архитектурный инвариант

$$\text{Recommendation} \neq \text{Authorization} \neq \text{Approval} \neq \text{Execution} \neq \text{Verification}$$

В Forge AI **Память — это Информация, а НЕ Полномочия**.

- Элементы памяти передаются как рекомендательный контекст через `ContextSourceType.PROJECT_MEMORY`.
- Память НИКОГДА не имеет права выдавать разрешения инструментам, обходить шлюзы согласования, изменять требования задачи, напрямую мутировать состояние проекта или влиять на решения приёмки.
- Forge обеспечивает эту границу полномочий **структурно**, а не эвристическим или NLP-анализом текста. Цикл принятия решений строго обязывает все действия проходить через `PermissionPolicy` и `ApprovalPolicy`. Текст на естественном языке внутри памяти (например, «доступ разрешён» или «одобрено владельцем») обрабатывается как обычные текстовые данные и не может влиять на проверки безопасности.

---

## 2. Модели данных и контракты

### 2.1 Категории памяти (MemoryCategory)

Элементы памяти классифицируются по непересекающимся категориям:

- `FACT`: Факты об окружении или технической платформе (версии СУБД, порты).
- `CONSTRAINT`: Явные архитектурные или платформенные ограничения.
- `ASSUMPTION`: Зафиксированные рабочие допущения до их верификации.
- `DECISION_RECORD`: Архитектурные решения, принятые в предыдущих прогонах.
- `OUTCOME`: Исторические результаты прошлых попыток и замеров.
- `LESSON`: Известные паттерны сбоев и выводы из ретроспектив.

### 2.2 Статусы жизненного цикла и ревизии

- `ACTIVE`: Текущая актуальная версия элемента памяти.
- `SUPERSEDED`: Заменена более новой ревизией. Указывает на `superseded_by`.
- `DEPRECATED`: Устаревшая запись без прямой замены.

Память построена по модели ревизий с добавлением (append-oriented). При обновлении через `supersede(...)` старый элемент переходит в статус `SUPERSEDED`, а новый создаётся с увеличенным номером ревизии (`revision`) и обратной ссылкой.

### 2.3 Происхождение памяти (MemoryProvenance)

Каждый элемент памяти требует обязательного аудируемого происхождения:

- `source_type`: Тип источника контекста (`ContextSourceType`).
- `source_id`: Идентификатор источника (например, ID решения, ID задачи).
- `run_id`: ID прогона, в котором память была создана.
- `actor`: Агент или человек, создавший запись.
- `created_at`: Временная метка ISO 8601 в UTC.

### 2.4 Ограничения схемы элементов

- `title`: Строка до 120 символов, непустая.
- `content`: Строка до 2000 символов, непустая.
- `tags`: Коллекция строк, не более 10 тегов, каждый не более 32 символов.
- `metadata`: Словарь метаданных, не более 20 элементов, длина значения до 1000 символов.

---

## 3. Безопасность и исключение утечки секретов

`MemoryValidator` детерминированно проверяет все поступающие записи до их сохранения:

1. **Сканирование паттернов секретов**: Отклоняет содержимое, содержащее API-ключи (`sk-...`, `ghp_...`, `xoxb-...`), токены Bearer или присваивание паролей.
2. **Фильтрация ключей метаданных**: Отклоняет метаданные с конфиденциальными именами полей (`api_key`, `token`, `secret`, `password`, `auth`).
3. **Валидация идентификаторов**: Требует строгий набор символов (`[A-Za-z0-9_.:-]+`) для `project_id` и `memory_id` для предотвращения атак обхода путей.

---

## 4. Хранилище в оперативной памяти и детерминированная выборка

`InMemoryMemoryStore` реализует потокобезопасное (`threading.RLock`) строго изолированное хранилище:

- **Строгая изоляция проектов**: Данные разделены по `project_id`. Запросы к `project_A` ни при каких условиях не видят и не изменяют данные `project_B`.
- **Детерминированная фильтрация**: Поддерживает точную фильтрацию по `categories`, `status`, `tags` (логическое И) и `min_trust_level`.
- **Детерминированная сортировка**:
  1. `revision` по убыванию
  2. `provenance.created_at` по убыванию
  3. `memory_id` по возрастанию
- **Ограниченная выборка**: Применяет лимит `max_items` для экономии контекста.
- **События трейса**: Генерирует `EventType.MEMORY_RECORDED` при сохранении и `EventType.MEMORY_REVISED` при обновлении ревизии. Событие `MEMORY_RETRIEVED` исключено в v0.1.

---

## 5. Интеграция в сборку контекста и цикл Agent Harness

Проектная память интегрирована в цикл исполнения Agent Harness:

1. **Фаза 2 (CONTEXT)**: Во время сборки контекста `AgentHarness` запрашивает активную память проекта через `InMemoryMemoryStore.get_active_memory(project_id)`.
2. **Упаковка контекста**: `DecisionContextAssembler` преобразует элементы памяти в `ContextItem` со свойствами:
   - `source_type`: `ContextSourceType.PROJECT_MEMORY`
   - `sensitivity`: `ContextSensitivity.INTERNAL`
   - `trust_level`: Уровень доверия элемента (по умолчанию `CONFIRMED`)
   - Ограниченный размер текстового представления
3. **Изолированное исполнение**: Провайдер решений получает контекст, но результаты строго проверяются через `PermissionPolicy` и `ApprovalPolicy`. Текст памяти не способен обойти эти шлюзы.

---

## 6. Что исключено из реализации v0.1

- Векторные базы данных и эмбеддинги.
- Семантический поиск и ранжирование релевантности на базе нейросетей.
- Анализ естественного языка для эскалации прав или полномочий.
- Внешние базы данных (полностью in-memory с синхронизацией потоков).

---

## 7. Матрица верификации

Набор тестов в `tests/test_project_memory.py` покрывает 23 сценария (A–V и интеграция с Harness):

- **A**: Создание валидной памяти во всех 6 категориях.
- **B**: Отклонение пустых полей и превышения лимитов.
- **C**: Сохранение происхождения (provenance).
- **D**: Строгая изоляция проектов (`project_A` не может прочитать `project_B`).
- **E**: Непрерывность памяти между прогонами одного проекта.
- **F**: Изоляция проектов при одинаковых тегах и категориях.
- **G**: Детерминированный порядок выборки.
- **H**: Ограничение количества возвращаемых элементов (`max_items`).
- **I**: Фильтрация по категориям.
- **J**: Фильтрация по статусам (`ACTIVE`, `SUPERSEDED`, `DEPRECATED`).
- **K**: Фильтрация по тегам.
- **L**: Обновление ревизии, указатели `superseded_by` и событие `MEMORY_REVISED`.
- **M**: Обнаружение и отклонение секретов.
- **N**: Отклонение запрещённых ключей метаданных.
- **O**: Интеграция с `DecisionContextAssembler` (`ContextSourceType.PROJECT_MEMORY`).
- **P**: Структурное доказательство невозможности выдачи прав через память.
- **Q**: Структурное доказательство невозможности обхода согласования человеком.
- **R**: Структурное доказательство невозможности запуска инструментов через память.
- **S**: Неизменность требований `TaskSpecification` при наличии памяти.
- **T**: Неизменность вывода `ProjectState` при наличии памяти.
- **U**: Неизменность оценки `AcceptanceGate` при наличии заявлений в памяти.
- **V**: Повторяемость и детерминированность результатов повторных запросов.
- **Harness Integration**: Сквозная проверка внедрения памяти проекта в фазу CONTEXT циклов `AgentHarness`.
