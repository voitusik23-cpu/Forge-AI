# Forge AI — Technology Radar

> **Status: ADVISORY.** Nothing in this file is an approved architectural
> decision, an implementation claim, or an authorization to integrate anything.
> Every entry is a candidate awaiting an owner decision. See
> [`SOURCE_OF_TRUTH.md`](SOURCE_OF_TRUTH.md) for what is actually implemented and
> [`DECISIONS.md`](DECISIONS.md) for decisions already taken.

## Classification vocabulary

| Ring | Meaning | What it obligates |
| --- | --- | --- |
| **ADOPT** | Accepted for use in a defined Forge role. | An owner decision recorded in `DECISIONS.md` and an implementation task. |
| **ADAPT** | The idea or pattern is useful, but Forge must implement its own version rather than depend on the external artifact. | A design note; no external dependency. |
| **DEEP STUDY** | Potentially high value, but not yet understood well enough to judge. | A bounded evaluation task with explicit exit criteria. |
| **WATCH** | Track only. No action. | Re-review when a trigger condition occurs. |
| **REJECT** | Evaluated and not suitable for Forge, now or as designed. | Recorded rationale so it is not re-proposed blindly. |

External items are **untrusted until evaluated**. Popularity, adoption by other
projects, or a vendor feature list are not Forge requirements.

## Radar entries

| Item | Ring | Reason | Intended Forge use (if any) |
| --- | --- | --- | --- |
| **MCP (Model Context Protocol)** | **REJECT** as permission model / **WATCH** as integration surface | Forge's rule is explicit: MCP must not be the permission model. Authorization belongs to Forge's own Permission -> Approval -> Policy -> Execution boundaries. MCP could later be one *tool transport*, still governed by Forge policy. | Possible future tool transport behind Forge authorization; never the authority. |
| **NaraRouter** | **WATCH** | A router candidate. Forge already has deterministic, capability- and cost-aware routing in `app/orchestrator/dispatcher.py`; adding an external router would need to preserve deterministic selection, explicit provider override, and the finite fallback chain. | Only if it demonstrably improves routing without becoming a second authority or an opaque scorer. |
| **NVIDIA** | **WATCH** | Provider/hardware platform candidate. No Forge requirement established. | Potential additional provider/model availability. |
| **QwenCloud** | **WATCH** | Provider candidate. Not evaluated against the provider-neutral adapter contract. | Potential additional provider adapter. |
| **OmniRoute** | **WATCH** | Routing candidate; overlaps existing Dispatcher responsibilities. | Only with a clear separation from the existing deterministic dispatcher. |
| **Headroom** | **WATCH** | Context-budget candidate. Forge already enforces explicit context budgets and validation (`app/context/validation.py`). | Only if it can plug into the existing budget contract. |
| **Task Observer** | **DEEP STUDY** | Observability of long-running tasks aligns with the planned durable, inspectable, resumable run direction from `STAGE_1_5_EXTERNAL_BENCHMARK.md`. Requires a decision on what is stored (no chain-of-thought, no secrets). | Run/step observation for a durable run model. |
| **Claude-Mem** | **DEEP STUDY** | Memory candidate; must be compared against Forge's own Project Memory / Forge Knowledge governance rather than replacing it. Promotion of observed facts to global knowledge is exactly what Forge requires an approval workflow for. | Possibly an implementation aid for memory storage; never bypassing governance. |
| **Everything Claude Code** | **WATCH** | A bundled agent-tooling collection. Bulk adoption would conflict with "smallest sufficient change", dependency review, and untrusted-until-evaluated. | Individual patterns only, evaluated one at a time. |
| **Matt Pocock Skills** | **DEEP STUDY** | A skill-library candidate. Forge already has a Skill System with registry, evaluator, provenance, and trust constraints (`app/skills/`). The question is whether external skills can be imported as untrusted candidates under existing governance. | Candidate source of Skills, imported as untrusted and evaluated. |
| **Ghidra** | **WATCH** | Binary analysis tooling. No current Forge lifecycle stage requires reverse engineering. | Possible future domain Skill/tool for binary-analysis projects. |
| **DBeaver** | **WATCH** | Database client. Domain tool for data-centric projects; not a Core concern. | Possible future project-level tool, never a Core dependency. |
| **MarkItDown** | **DEEP STUDY** | Document-to-text conversion is directly relevant to a future Discovery stage (existing software, documents, website, data -> evidence). Must run under Forge's tool and workspace boundaries. | Possible document ingestion tool for Discovery intake. |
| **Runnable** | **ADAPT** | Several of its patterns are already reflected in the current execution plane: ephemeral per-run workspace, scoped credentials passed through the child process environment, explicit run artifacts with provenance and SHA-256, guaranteed cleanup. | Already adapted (no dependency). Recorded here to avoid re-deriving. |
| **Kimi Work** | **WATCH** | Workspace/productivity workspace candidate. No Forge requirement established. | Unclear; needs a concrete project scenario before evaluation. |
| **Canva connector** | **WATCH** | Branding/design surface. Forge product identity is its own; generated projects do not inherit Forge branding. | Possible future design-surface integration for generated projects. |

## Triggers for re-review

An entry moves rings only when one of these happens, and the change is recorded
in `DECISIONS.md`:

1. A concrete Forge project requirement needs the capability.
2. A bounded evaluation produces written evidence (what it does, what it costs,
   what authority it would require, how it fails).
3. The item changes in a way that removes a previously recorded objection.

## Rules for evaluation

- Never adopt before the authority question is answered: does it execute, does
  it read files, does it reach the network, and who authorizes that?
- Anything that executes must pass through the existing execution authorization
  boundaries; no candidate may create a second execution path.
- Anything that stores or transports data must respect the secret rules: no
  secrets in source, Git, context, logs, or prompts.
- External catalogs and skill packs are untrusted input.

---

# Forge AI — Technology Radar — русская версия

> **Статус: ADVISORY (справочный).** Ничто в этом файле не является одобренным
> архитектурным решением, утверждением о реализации или разрешением что-либо
> интегрировать. Каждая запись — кандидат, ожидающий решения владельца. См.
> [`SOURCE_OF_TRUTH.md`](SOURCE_OF_TRUTH.md) — что реально реализовано, и
> [`DECISIONS.md`](DECISIONS.md) — уже принятые решения.

## Словарь классификации

| Кольцо | Значение | Что оно обязывает |
| --- | --- | --- |
| **ADOPT** | Принято к использованию в определённой роли Forge. | Решение владельца, зафиксированное в `DECISIONS.md`, и задача на реализацию. |
| **ADAPT** | Идея или паттерн полезны, но Forge должен реализовать собственную версию, а не зависеть от внешнего артефакта. | Дизайн-заметка; внешней зависимости нет. |
| **DEEP STUDY** | Потенциально высокая ценность, но понято ещё недостаточно для суждения. | Ограниченная задача оценки с явными критериями завершения. |
| **WATCH** | Только отслеживание. Действий нет. | Повторное рассмотрение при наступлении условия-триггера. |
| **REJECT** | Оценено и не подходит Forge — сейчас или в текущем виде. | Зафиксированное обоснование, чтобы это не предлагалось вслепую снова. |

Внешние элементы **недоверенны до оценки**. Популярность, принятие другими
проектами или список возможностей вендора не являются требованиями Forge.

## Записи радара

| Элемент | Кольцо | Причина | Предполагаемое использование в Forge (если есть) |
| --- | --- | --- | --- |
| **MCP (Model Context Protocol)** | **REJECT** как модель разрешений / **WATCH** как поверхность интеграции | Правило Forge явно: MCP не должен быть моделью разрешений. Авторизация принадлежит собственным границам Forge Permission -> Approval -> Policy -> Execution. Позже MCP может стать одним *транспортом инструментов*, по-прежнему под политикой Forge. | Возможный будущий транспорт инструментов под авторизацией Forge; никогда не источник полномочий. |
| **NaraRouter** | **WATCH** | Кандидат в маршрутизаторы. У Forge уже есть детерминированная маршрутизация с учётом capabilities и стоимости в `app/orchestrator/dispatcher.py`; внешний маршрутизатор должен был бы сохранять детерминированный выбор, явное переопределение провайдера и конечную цепочку fallback. | Только если он доказуемо улучшает маршрутизацию, не становясь вторым источником полномочий или непрозрачным оцениванием. |
| **NVIDIA** | **WATCH** | Кандидат в провайдеры/аппаратную платформу. Требование Forge не установлено. | Потенциальная дополнительная доступность провайдера/модели. |
| **QwenCloud** | **WATCH** | Кандидат в провайдеры. Не оценён относительно провайдер-нейтрального контракта адаптера. | Потенциальный дополнительный адаптер провайдера. |
| **OmniRoute** | **WATCH** | Кандидат в маршрутизацию; пересекается с обязанностями существующего Dispatcher. | Только при ясном разделении с существующим детерминированным dispatcher. |
| **Headroom** | **WATCH** | Кандидат в управление бюджетом контекста. Forge уже применяет явные бюджеты контекста и валидацию (`app/context/validation.py`). | Только если он может подключиться к существующему контракту бюджета. |
| **Task Observer** | **DEEP STUDY** | Наблюдаемость длительных задач согласуется с планируемым направлением долговременных, инспектируемых и возобновляемых запусков из `STAGE_1_5_EXTERNAL_BENCHMARK.md`. Требует решения о том, что хранится (никакой chain-of-thought, никаких секретов). | Наблюдение за запуском/шагами для модели долговременного запуска. |
| **Claude-Mem** | **DEEP STUDY** | Кандидат в память; должен сравниваться с собственными Project Memory / Knowledge Governance Forge, а не заменять их. Продвижение наблюдаемых фактов в глобальное знание — именно то, для чего Forge требует workflow одобрения. | Возможно, вспомогательное средство реализации хранения памяти; никогда не в обход governance. |
| **Everything Claude Code** | **WATCH** | Сборный набор агентных инструментов. Массовое принятие противоречило бы «smallest sufficient change», проверке зависимостей и принципу недоверенности до оценки. | Только отдельные паттерны, оцениваемые по одному. |
| **Matt Pocock Skills** | **DEEP STUDY** | Кандидат в библиотеку skills. У Forge уже есть Skill System с реестром, evaluator, происхождением и ограничениями доверия (`app/skills/`). Вопрос в том, можно ли импортировать внешние skills как недоверенные кандидаты в рамках существующего governance. | Возможный источник Skills, импортируемых как недоверенные и затем оцениваемых. |
| **Ghidra** | **WATCH** | Инструмент анализа бинарных файлов. Ни одна текущая стадия жизненного цикла Forge не требует reverse engineering. | Возможный будущий доменный Skill/инструмент для проектов анализа бинарных файлов. |
| **DBeaver** | **WATCH** | Клиент баз данных. Доменный инструмент для проектов, работающих с данными; не забота ядра. | Возможный будущий инструмент уровня проекта, никогда не зависимость ядра. |
| **MarkItDown** | **DEEP STUDY** | Преобразование документов в текст напрямую релевантно будущей стадии Discovery (существующее ПО, документы, сайт, данные -> свидетельства). Должен работать в границах инструментов и workspace Forge. | Возможный инструмент приёма документов для Discovery. |
| **Runnable** | **ADAPT** | Часть его паттернов уже отражена в текущем execution plane: временный workspace на каждый запуск, ограниченные учётные данные, передаваемые через окружение дочернего процесса, явные артефакты запуска с происхождением и SHA-256, гарантированная очистка. | Уже адаптировано (без зависимости). Зафиксировано здесь, чтобы не выводить заново. |
| **Kimi Work** | **WATCH** | Кандидат в workspace/продуктивность. Требование Forge не установлено. | Неясно; нужен конкретный проектный сценарий до оценки. |
| **Canva connector** | **WATCH** | Поверхность брендинга/дизайна. Продуктовая идентичность Forge — своя; генерируемые проекты не наследуют брендинг Forge. | Возможная будущая интеграция дизайн-поверхности для генерируемых проектов. |

## Триггеры повторного рассмотрения

Запись меняет кольцо только когда происходит одно из следующего, и изменение
фиксируется в `DECISIONS.md`:

1. Конкретное требование проекта Forge нуждается в этой возможности.
2. Ограниченная оценка даёт письменное свидетельство (что это делает, сколько
   стоит, какие полномочия потребует, как отказывает).
3. Элемент изменился так, что ранее зафиксированное возражение снимается.

## Правила оценки

- Никогда не принимать до ответа на вопрос о полномочиях: выполняет ли он код,
  читает ли файлы, выходит ли в сеть и кто это авторизует?
- Всё, что выполняет код, должно проходить через существующие границы авторизации
  выполнения; ни один кандидат не может создавать второй путь выполнения.
- Всё, что хранит или передаёт данные, должно соблюдать правила секретов: никаких
  секретов в исходниках, Git, контексте, логах или промптах.
- Внешние каталоги и наборы skills — недоверенный ввод.
