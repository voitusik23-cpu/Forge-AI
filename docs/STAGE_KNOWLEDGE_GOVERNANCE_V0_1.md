# Forge AI — Knowledge Governance v0.1

## 1. Overview & Core Invariants

Knowledge Governance v0.1 establishes a controlled, auditable bridge between project-specific experience and reusable engineering knowledge in Forge AI.

In autonomous systems, the naive temptation is to immediately generalize whatever happened in a single run or project into universal rules. Forge explicitly rejects this: **Project learning $
eq$ Global learning**.

### Architectural Invariant

$$\text{Recommendation} \neq \text{Authorization} \neq \text{Approval} \neq \text{Execution} \neq \text{Verification} \neq \text{Acceptance}$$

In Forge AI, **Knowledge is Information, NOT Authority**:

- Knowledge items are ingested into decision contexts strictly as informational data under `ContextSourceType.FORGE_KNOWLEDGE`.
- Knowledge items have **zero** capability to grant tool permissions, approve dangerous operations, bypass human approval gates, alter requirements, mutate project state, or override verification/acceptance verdicts.
- The authority boundary is structural, not textual: text within a knowledge statement or rationale asserting permissions (e.g., "permission granted", "bypass approval") has zero operational effect on `PermissionPolicy` or `ApprovalPolicy`.

---

## 2. Boundaries: Memory vs Candidate vs Approved Knowledge

| Boundary | Scope | Visibility & Availability | Authority |
| :--- | :--- | :--- | :--- |
| **Project Memory** | Single `project_id` | Strictly local to that project; never visible across projects | Pure advisory context for that project |
| **Candidate Knowledge** | Proposed scope (`DOMAIN` or `FORGE_GLOBAL`) | Visible only to governance and reviewers; blocked from context assembly | Zero authority; cannot be used across projects |
| **Approved Knowledge** | Governed scope (`DOMAIN` or `FORGE_GLOBAL`) | Eligible for cross-project context assembly **only** when positive applicability matches and exceptions do not match | Pure advisory context; zero operational authority |

---

## 3. Data Model & Contracts

### 3.1 KnowledgeScope

- `DOMAIN`: Knowledge scoped to a specific engineering domain (e.g. `python_cli`, `offline_testing`). Requires `domain` identifier.
- `FORGE_GLOBAL`: Cross-cutting engineering principles verified across multiple independent projects or authoritative specifications.

### 3.2 KnowledgeCategory

- `BEST_PRACTICE`: Proven patterns and recommended approaches.
- `PLATFORM_CONSTRAINT`: Concrete platform limitations and system realities.
- `PITFALL_AVOIDANCE`: Documented failure modes and anti-patterns.
- `DESIGN_PATTERN`: Architectural structural patterns.
- `WORKAROUND`: Temporary solutions for known external defects.

### 3.3 KnowledgeStatus

- `CANDIDATE`: Proposed reusable rule or fact.
- `UNDER_REVIEW`: In formal review by governance or human operators.
- `APPROVED`: Passed evidence thresholds and review; available for matching contexts.
- `REJECTED`: Formally rejected with recorded rationale.
- `DEPRECATED`: Marked obsolete; excluded from default retrieval.
- `SUPERSEDED`: Replaced by a newer version with forward pointer `superseded_by`.

### 3.4 Structured Applicability & Negative Exceptions

Knowledge must describe **WHAT works**, **WHEN it applies**, and **WHEN it does NOT apply**:

- `runtimes`: Target runtimes and versions (e.g. `python>=3.11`).
- `frameworks`: Target frameworks (e.g. `click`, `pytest`).
- `operating_systems`: Target OS environments (`linux`, `windows`).
- `project_types`: Architectural project classifications (`cli`, `service`).
- `execution_modes`: Execution environment modes (`offline`, `isolated_scratch`).
- `tags`: Specific conceptual labels (`concurrency`, `file_io`).

**Matching Rules**:
1. **Positive Applicability**: Every non-empty declared dimension in `applicability` must intersect with the target project's context.
2. **Negative Applicability (Exceptions)**: If any non-empty dimension in `non_applicability` matches the target project's context, the knowledge item is **excluded**.
3. **Mandatory Applicability**: Empty applicability is forbidden for `DOMAIN` and `FORGE_GLOBAL` knowledge; universal "always do X" rules are architecturally blocked.

---

## 4. Evidence Tiers and Promotion Governance

### 4.1 Evidence Tiers

- `SINGLE_OBSERVATION`: Observed in exactly one run or memory of a single project.
- `REPEATED_OBSERVATION`: Observed across multiple runs within the same project.
- `VERIFIED_OBSERVATION`: Backed by passing automated verification/acceptance.
- `CROSS_PROJECT`: Observed independently across $\ge 2$ distinct projects.
- `USER_CONFIRMED`: Explicitly affirmed by human project owner.
- `EXTERNAL_AUTHORITATIVE`: Sourced from official language or standards specifications.

### 4.2 Promotion Rules

1. **No Automatic Promotion**: Candidates cannot transition to `APPROVED` without a valid `KnowledgeReview`. Attempting to approve without review raises `KnowledgeGovernanceError`.
2. **DOMAIN Scope**: Requires at least `VERIFIED_OBSERVATION`, `CROSS_PROJECT`, `USER_CONFIRMED`, or `EXTERNAL_AUTHORITATIVE`. `SINGLE_OBSERVATION` cannot be approved for a domain.
3. **FORGE_GLOBAL Scope**: Requires `CROSS_PROJECT` ($\ge 2$ distinct projects), `USER_CONFIRMED`, or `EXTERNAL_AUTHORITATIVE`. A single-project observation can **never** become global knowledge.

---

## 5. Security & Confidentiality

- **Secret Scanning**: `KnowledgeValidator` scans `statement`, `rationale`, metadata, and applicability dimensions against OpenAI API keys, GitHub tokens, Slack tokens, Bearer tokens, and password assignments.
- **Sensitive Metadata Keys**: Metadata keys matching `_SENSITIVE_KEY_PATTERN` (`token`, `api_key`, `secret`, `password`, `auth`) are rejected.
- **Confidentiality Protection**: Candidate extraction strips host usernames, absolute filesystem paths, and proprietary code. Provenance records source identifiers (`memory_id`, `run_id`), never raw sensitive dumps.

---

## 6. Storage & Deterministic Retrieval

- `InMemoryKnowledgeStore`: Thread-safe (`threading.RLock`) in-memory repository.
- **Default Retrieval**: Returns `APPROVED` items only. Candidates, rejected, superseded, and deprecated items are omitted.
- **Deterministic Sort Order**:
  1. `evidence.tier` rank descending
  2. `version` descending
  3. `created_at` descending
  4. `knowledge_id` ascending
- **Hard Upper Bound**: Truncated strictly to `max_items`.

---

## 7. Context Assembly Integration

- Ingested into `DecisionContextEnvelope` under `ContextSourceType.FORGE_KNOWLEDGE`.
- Bounded text representation with `ContextSensitivity.INTERNAL` and `ContextTrustLevel.CONFIRMED`.
- Context Assembler and Decision Provider preserve zero operational authority: knowledge is purely advisory context.

---

## 8. What Remains Outside v0.1

- No vector databases (Chroma, FAISS, Pinecone).
- No neural embeddings or RAG pipelines.
- No semantic similarity matching.
- No autonomous LLM knowledge crawler or auto-promoter.
- No dynamic permission modification.

---

# Управление знаниями Forge AI v0.1 (Русская версия)

## 1. Обзор и ключевые инварианты

Knowledge Governance v0.1 создаёт контролируемый и аудируемый мост между проектным опытом и переиспользуемыми инженерными знаниями в Forge AI.

В автономных системах типичной ошибкой является автоматическое обобщение того, что произошло в отдельном прогоне или проекте, в универсальные правила. Forge категорически отвергает такой подход: **Опыт одного проекта $
eq$ Глобальное знание**.

### Архитектурный инвариант

$$\text{Recommendation} \neq \text{Authorization} \neq \text{Approval} \neq \text{Execution} \neq \text{Verification} \neq \text{Acceptance}$$

В Forge AI **Знания — это Информация, а НЕ Полномочия**:

- Элементы знаний включаются в контекст принятия решений строго как информационные данные с типом `ContextSourceType.FORGE_KNOWLEDGE`.
- Знания обладают **нулевыми** полномочиями на выдачу прав инструментам, одобрение опасных операций, обход шлюзов согласования, изменение требований, мутацию состояния проекта или переопределение результатов верификации и приёмки.
- Граница полномочий является структурной: текст внутри утверждения или обоснования знания, заявляющий о правах доступа (например, «доступ разрешён», «пропустить согласование»), не оказывает никакого влияния на `PermissionPolicy` или `ApprovalPolicy`.

---

## 2. Границы: Память проекта, Кандидат и Утверждённое знание

| Граница | Область действия | Доступность | Полномочия |
| :--- | :--- | :--- | :--- |
| **Память проекта** | Отдельный `project_id` | Строго локальна для проекта; никогда не видна другим проектам | Рекомендательный контекст для этого проекта |
| **Кандидат в знание** | Предлагаемая область (`DOMAIN` или `FORGE_GLOBAL`) | Виден только ревьюерам и подсистеме управления; исключён из сборки контекста | Нулевые полномочия; недоступен другим проектам |
| **Утверждённое знание** | Утверждённая область (`DOMAIN` или `FORGE_GLOBAL`) | Доступно для сборки контекста в других проектах **только** при совпадении условий применимости и отсутствии исключений | Рекомендательный контекст; нулевые полномочия |

---

## 3. Модель данных и контракты

### 3.1 Области действия (KnowledgeScope)

- `DOMAIN`: Знание в рамках конкретной инженерной предметной области (например, `python_cli`, `offline_testing`). Требует указания идентификатора `domain`.
- `FORGE_GLOBAL`: Общесистемные инженерные принципы, подтверждённые в нескольких независимых проектах или стандартами спецификаций.

### 3.2 Категории (KnowledgeCategory)

- `BEST_PRACTICE`: Проверенные практики и рекомендации.
- `PLATFORM_CONSTRAINT`: Ограничения платформы и системные особенности.
- `PITFALL_AVOIDANCE`: Документированные ошибки и антипаттерны.
- `DESIGN_PATTERN`: Архитектурные шаблоны проектирования.
- `WORKAROUND`: Временные обходные пути для известных внешних дефектов.

### 3.3 Статусы жизненного цикла (KnowledgeStatus)

- `CANDIDATE`: Предложенное правило или факт.
- `UNDER_REVIEW`: Находится на рассмотрении ревьюерами.
- `APPROVED`: Прошло порог доказательности и ревью; доступно подходящим контекстам.
- `REJECTED`: Отклонено с фиксацией обоснования.
- `DEPRECATED`: Устарело; исключено из стандартной выборки.
- `SUPERSEDED`: Заменено более новой версией с указателем `superseded_by`.

### 3.4 Структурированная применимость и исключения

Знание обязано описывать **ЧТО работает**, **КОГДА применяется** и **КОГДА НЕ применяется**:

- `runtimes`: Среды исполнения и версии (`python>=3.11`).
- `frameworks`: Фреймворки (`click`, `pytest`).
- `operating_systems`: Операционные системы (`linux`, `windows`).
- `project_types`: Типы проектов (`cli`, `service`).
- `execution_modes`: Режимы исполнения (`offline`, `isolated_scratch`).
- `tags`: Метки (`concurrency`, `file_io`).

**Правила сопоставления**:
1. **Положительное совпадение**: Каждое непустое измерение в `applicability` должно пересекаться с параметрами целевого проекта.
2. **Отрицательное совпадение (исключения)**: Если любое непустое измерение в `non_applicability` совпадает с контекстом проекта, знание **исключается**.
3. **Обязательность применимости**: Пустая применимость запрещена для `DOMAIN` и `FORGE_GLOBAL`; универсальные правила вида «делай так всегда» заблокированы на уровне архитектуры.

---

## 4. Уровни доказательности и правила продвижения

### 4.1 Уровни доказательности (EvidenceTier)

- `SINGLE_OBSERVATION`: Зафиксировано в 1 прогоне или памяти 1 проекта.
- `REPEATED_OBSERVATION`: Подтверждено в нескольких прогонах одного проекта.
- `VERIFIED_OBSERVATION`: Подтверждено успешным прохождением тестов верификации и приёмки.
- `CROSS_PROJECT`: Зафиксировано независимо в $\ge 2$ разных проектах.
- `USER_CONFIRMED`: Явно подтверждено владельцем проекта (человеком).
- `EXTERNAL_AUTHORITATIVE`: Взято из официальной спецификации или стандарта.

### 4.2 Правила продвижения (Promotion Rules)

1. **Запрет автоматического продвижения**: Кандидат не может стать `APPROVED` без валидного объекта `KnowledgeReview`. Попытка утверждения без ревью вызывает ошибку `KnowledgeGovernanceError`.
2. **Область DOMAIN**: Требует минимум `VERIFIED_OBSERVATION`, `CROSS_PROJECT`, `USER_CONFIRMED` или `EXTERNAL_AUTHORITATIVE`. `SINGLE_OBSERVATION` не может быть утверждён для домена.
3. **Область FORGE_GLOBAL**: Требует `CROSS_PROJECT` ($\ge 2$ независимых проекта), `USER_CONFIRMED` или `EXTERNAL_AUTHORITATIVE`. Опыт одного проекта **никогда** не может стать глобальным знанием Forge.

---

## 5. Безопасность и конфиденциальность

- **Сканирование секретов**: `KnowledgeValidator` проверяет поля утверждения, обоснования, метаданных и параметров применимости на наличие ключей API (OpenAI, GitHub, Slack), токенов Bearer и паролей.
- **Фильтрация ключей метаданных**: Ключи словарей метаданных с именами `token`, `api_key`, `secret`, `password`, `auth` отклоняются.
- **Защита конфиденциальности**: При извлечении кандидата удаляются имена пользователей, абсолютные пути файловой системы и проприетарный код. Происхождение фиксирует идентификаторы (`memory_id`, `run_id`), исключая дампы чувствительных данных.

---

## 6. Хранилище и детерминированная выборка

- `InMemoryKnowledgeStore`: Потокобезопасное хранилище на базе `threading.RLock`.
- **Выборка по умолчанию**: Возвращает исключительно записи со статусом `APPROVED`. Кандидаты, отклонённые, устаревшие и замещённые записи исключаются.
- **Детерминированная сортировка**:
  1. Ранг `evidence.tier` по убыванию
  2. `version` по убыванию
  3. `created_at` по убыванию
  4. `knowledge_id` по возрастанию
- **Ограничение выборки**: Строгое ограничение лимитом `max_items`.

---

## 7. Интеграция со сборкой контекста

- Элементы включаются в `DecisionContextEnvelope` под типом `ContextSourceType.FORGE_KNOWLEDGE`.
- Форматируются как ограниченный по длине текст с чувствительностью `ContextSensitivity.INTERNAL` и уровнем доверия `ContextTrustLevel.CONFIRMED`.
- Сборщик контекста и компонент принятия решений сохраняют нулевую исполнительную власть: знание является исключительно совещательным контекстом.

---

## 8. Что исключено из реализации v0.1

- Векторные базы данных (Chroma, FAISS, Pinecone).
- Нейросетевые эмбеддинги и пайплайны RAG.
- Семантический поиск по сходству.
- Автономный фоновый сборщик и авто-продвижение знаний.
- Динамическое изменение разрешений инструментов.
