# Forge AI — Product Vision

Forge AI is not another AI model.
Forge AI is an AI orchestration system that coordinates multiple AI models and specialized agents to execute real software and knowledge-work projects under controlled user authorization.

## The idea behind the name

A forge is a workshop where different skills and tools shape raw material into
something useful. Forge AI applies that idea to work: a user brings an idea,
question, or project task; specialized agents contribute planning, implementation,
analysis, review, and testing; and the system assembles their work into a result
that can be checked and advanced under the user's direction.

The metaphor is collaborative, not autonomous. Forge AI should make complex
work easier to coordinate while keeping the user in control of project scope,
changes, credentials, and release decisions.

## Long-term architecture

```text
                         +----------------------+
                         |         USER         |
                         |  idea / task / goal  |
                         +----------+-----------+
                                    |
                                    v
                         +----------------------+
                         |       FORGE AI       |
                         |    ORCHESTRATOR      |
                         +----------+-----------+
                                    |
                    +---------------+---------------+
                    |               |               |
                    v               v               v
                 Planner        Dispatcher        Memory
                    |               |               |
                    +---------------+---------------+
                                    |
                                    v
                         +----------------------+
                         |    SPECIALIZED       |
                         |       AGENTS         |
                         +----------+-----------+
                                    |
              +---------------------+---------------------+
              |                     |                     |
              v                     v                     v
            Coder                Reviewer               Tester
              |                     |                     |
              +---------------------+---------------------+
                                    v
                              Final Review
                                    |
                                    v
                         +----------------------+
                         |   CONTROLLED CHANGE  |
                         |    Git / Deploy      |
                         +----------------------+
```

This diagram describes the intended direction. It is not a claim that every
component is already implemented. The current system provides task models,
deterministic classification and provider routing, provider adapters, bounded
fallback, and a primary/reviewer workflow with at most one revision. Planning, durable project
memory, direct project editing, and deployment remain future capabilities.

## What Forge AI is for

Forge AI is intended to help individuals and teams carry meaningful work from
an initial request to a reviewed, verifiable outcome. Software development is
the first and clearest use case, while the task and provider abstractions should
also support knowledge work such as research, technical analysis, documentation,
and structured review.

The product should help users:

- Turn a broad request into explicit, manageable tasks.
- Route each task to an agent or model suited to its category and requirements.
- Combine specialized contributions without tying orchestration to one AI
  provider.
- Review and test outputs before treating them as complete.
- Understand which agents and models contributed and what remains uncertain.
- Keep project changes inspectable and under human authorization.

## How the workshop should work

The intended project workflow is:

1. **Understand the request.** Capture the goal, constraints, relevant files,
   and the level of authority the user has granted.
2. **Plan the work.** Break the goal into clear tasks with dependencies and
   acceptance criteria. Planning should be visible and revisable.
3. **Dispatch deliberately.** Classify tasks and select agents from their
   capabilities, configuration, and applicable cost or policy limits.
4. **Execute in focused roles.** Give each agent only the context and tools it
   needs. Keep provider-specific behavior behind adapters.
5. **Verify and review.** Run appropriate checks and have reviewers assess
   correctness, completeness, and risks. A review must not be mistaken for a
   test, and a test must not be mistaken for user approval.
6. **Present the result.** Explain what changed, what was verified, and what
   needs attention. Keep the proposed changes inspectable.
7. **Advance only with the right authority.** Apply changes, commit, publish,
   or deploy only within the authorization given for that task.

Not every request needs every stage. The orchestrator should choose the
smallest workflow that can deliver a reliable result, and it should not invent
approval for actions the user did not authorize.

## Specialized agents

The long-term system may coordinate roles such as:

- **Planner:** decomposes goals and tracks dependencies.
- **Architect:** examines system boundaries and design tradeoffs.
- **Coder:** implements a focused change.
- **Reviewer:** independently checks a proposed result and identifies gaps.
- **Tester:** validates behavior and edge cases.
- **Security reviewer:** looks for credential exposure and unsafe changes.
- **Researcher or analyst:** gathers and compares evidence for knowledge-work
  tasks.
- **Documentation agent:** keeps user and developer documentation aligned with
  actual behavior.

Roles describe responsibilities, not permanent provider assignments. The same
provider may fill different roles, and different providers may handle the
primary and review steps. Routing should use shared interfaces and declared
capabilities rather than embedding provider-specific SDK knowledge in the
orchestrator.

## Product principles

### User authority is explicit

The user remains responsible for the goal and the authority granted to Forge
AI. Agents should not expand a task's scope, access unrelated projects, change
production, or perform destructive operations on their own. When an action
requires approval, Forge AI should show a concrete, reviewable proposal first.

### Results are traceable

Tasks, routing decisions, agent results, reviews, and verification outcomes
should be understandable after execution. Important architecture decisions
belong in project documentation, and Git remains the source of truth for code
history.

### Providers are replaceable

Models and providers will continue to change. Forge AI should isolate those
changes behind provider adapters and keep task execution, routing policy, and
agent roles provider-neutral.

### Routing is explainable

Routing should begin with predictable rules based on task category, configured
availability, declared capabilities, and policy. Cost, latency, quality, and
budget may become additional constraints, but they should not turn routing into
an opaque decision. Users should retain an explicit provider override.

### Context and cost are managed

Agents should receive the smallest useful context. The system should avoid
re-reading unchanged files, repeating work, and invoking extra models without a
clear benefit. Usage should be visible where available; estimates must not be
presented as actual charges.

### Review is a separate responsibility

A review is an assessment of a result, not permission to modify it. Future
workflows may propose corrections after review, but those corrections must be
bounded, visible, and governed by the user's authorization. Agent loops must
always have explicit limits and a clear stop condition.

### Secrets stay outside project history

Credentials belong in local secret storage or approved secret-management
systems. They must not appear in source, task output, logs, review context when
not needed, or Git history. Provider account metadata remains separate from
API credentials.

## Roadmap direction

The roadmap is capability-based rather than date-based. Each step should build
on the existing provider-neutral interfaces and retain offline tests.

### Foundation — implemented

- Provider-neutral tasks, results, usage, and agent interfaces.
- Provider registry, adapters, local secret resolution, and runtime settings.
- Deterministic task classification and capability-aware routing.
- Bounded sequential provider fallback.
- One primary execution, at most one revision, and a final review when changes are requested.

### Reliable project workflows — next direction

- Explicit plans, acceptance criteria, and task dependencies.
- Better evidence links between requested work, changed files, and verification.
- Review policies that can distinguish approval, requested changes, and
  unresolved questions.
- More complete model, capability, budget, and latency metadata.

### Controlled project operations — future direction

- Project-scoped context and tools with narrow permissions.
- Proposed file changes that users can inspect before application.
- Sandboxed execution and repeatable verification where appropriate.
- Approval gates for destructive operations, commits, publication, and
  production deployment.

### Durable collaboration — future direction

- Project memory that is explicit, reviewable, and scoped to the right project.
- Multi-agent workflows with bounded parallel work where it provides value.
- Auditable histories of plans, decisions, results, and user approvals.
- Optional integrations with team tools and development platforms.

These directions are goals, not promises about a release date. Each capability
should be added only when its permissions, failure handling, observability, and
testing can be made clear.

## Current boundary

Forge AI is an orchestration foundation, not yet a self-directed software
engineering organization. It can route tasks through configured providers and
run a bounded primary, review, and one-revision workflow. It does not currently create project plans, retain
durable project memory, autonomously edit a user's project, or deploy software.
Those boundaries should remain explicit as the system grows.

For implemented interfaces and execution details, see
[`ARCHITECTURE.md`](ARCHITECTURE.md). For durable design choices, see
[`DECISIONS.md`](DECISIONS.md).

The long-term system boundaries and target constraints are defined in the
[`Technical Specification`](TECHNICAL_SPECIFICATION.md).

## Forge product identity

Forge AI is its own product and should have a distinct visual identity across
future clients and distribution surfaces. Generated projects are separate
products with their own names, logos, icons, colors, and interface style; they
do not inherit Forge branding automatically. This principle does not select a
logo, palette, UI framework, or implementation technology.

### Идентичность продукта Forge

Forge AI — самостоятельный продукт, которому нужна собственная визуальная
идентичность в будущих клиентах и каналах распространения. Генерируемые проекты
являются отдельными продуктами со своими названиями, логотипами, иконками,
цветами и стилем интерфейса; они не наследуют брендинг Forge автоматически.
Этот принцип не выбирает логотип, палитру, UI framework или технологию
реализации.

---

# Forge AI — Product Vision — русская версия

Forge AI — не ещё одна AI-модель.
Forge AI — это система AI-оркестрации, которая координирует множество AI-моделей и
специализированных агентов для выполнения реальных проектов в разработке ПО и
интеллектуальной работе под контролируемой авторизацией пользователя.

## Идея названия

Кузница (forge) — это мастерская, где разные навыки и инструменты превращают
сырьё во что-то полезное. Forge AI переносит эту идею на работу: пользователь
приносит идею, вопрос или проектную задачу; специализированные агенты вносят
планирование, реализацию, анализ, ревью и тестирование; а система собирает их
работу в результат, который можно проверить и продвинуть под управлением
пользователя.

Метафора — коллаборативная, а не автономная. Forge AI должен делать сложную работу
проще в координации, сохраняя за пользователем контроль над рамками проекта,
изменениями, учётными данными и решениями о релизе.

## Долгосрочная архитектура

```text
                         +----------------------+
                         |         USER         |
                         |  idea / task / goal  |
                         +----------+-----------+
                                    |
                                    v
                         +----------------------+
                         |       FORGE AI       |
                         |    ORCHESTRATOR      |
                         +----------+-----------+
                                    |
                    +---------------+---------------+
                    |               |               |
                    v               v               v
                 Planner        Dispatcher        Memory
                    |               |               |
                    +---------------+---------------+
                                    |
                                    v
                         +----------------------+
                         |    SPECIALIZED       |
                         |       AGENTS         |
                         +----------+-----------+
                                    |
              +---------------------+---------------------+
              |                     |                     |
              v                     v                     v
            Coder                Reviewer               Tester
              |                     |                     |
              +---------------------+---------------------+
                                    v
                              Final Review
                                    |
                                    v
                         +----------------------+
                         |   CONTROLLED CHANGE  |
                         |    Git / Deploy      |
                         +----------------------+
```

Эта схема описывает предполагаемое направление. Она не является утверждением, что
каждый компонент уже реализован. Текущая система предоставляет модели задач,
детерминированную классификацию и маршрутизацию провайдеров, адаптеры
провайдеров, ограниченный fallback и workflow primary/reviewer не более чем с
одной ревизией. Планирование, долговременная память проектов, прямое
редактирование проектов и деплой остаются будущими возможностями.

## Для чего нужен Forge AI

Forge AI призван помогать отдельным людям и командам вести значимую работу от
первоначального запроса до проверенного, верифицируемого результата. Разработка ПО
— первый и наиболее ясный сценарий, при этом абстракции задач и провайдеров должны
также поддерживать интеллектуальную работу: исследования, технический анализ,
документацию и структурное ревью.

Продукт должен помогать пользователям:

- Превращать широкий запрос в явные, управляемые задачи.
- Направлять каждую задачу агенту или модели, подходящим по категории и требованиям.
- Объединять вклады специализированных участников, не привязывая оркестрацию к
  одному AI-провайдеру.
- Проверять и тестировать результаты, прежде чем считать их завершёнными.
- Понимать, какие агенты и модели внесли вклад и что остаётся неопределённым.
- Сохранять изменения проекта инспектируемыми и под человеческой авторизацией.

## Как должна работать мастерская

Предполагаемый workflow проекта:

1. **Понять запрос.** Зафиксировать цель, ограничения, релевантные файлы и уровень
   полномочий, предоставленных пользователем.
2. **Спланировать работу.** Разбить цель на ясные задачи с зависимостями и
   критериями приёмки. План должен быть видимым и пересматриваемым.
3. **Диспетчеризовать осознанно.** Классифицировать задачи и выбирать агентов по их
   возможностям, конфигурации и применимым ограничениям стоимости или политики.
4. **Выполнять в сфокусированных ролях.** Давать каждому агенту только нужный ему
   контекст и инструменты. Держать особенности провайдеров за адаптерами.
5. **Верифицировать и проверять.** Запускать подходящие проверки и поручать
   рецензентам оценку корректности, полноты и рисков. Ревью нельзя путать с тестом,
   а тест — с одобрением пользователя.
6. **Представлять результат.** Объяснять, что изменилось, что проверено и что
   требует внимания. Сохранять предложенные изменения инспектируемыми.
7. **Продвигать только с надлежащими полномочиями.** Применять изменения,
   коммитить, публиковать или деплоить только в пределах авторизации, выданной для
   этой задачи.

Не каждому запросу нужны все стадии. Оркестратор должен выбирать наименьший
workflow, способный дать надёжный результат, и не должен изобретать одобрение для
действий, которые пользователь не авторизовал.

## Специализированные агенты

Долгосрочная система может координировать такие роли:

- **Planner:** декомпозирует цели и отслеживает зависимости.
- **Architect:** исследует границы системы и компромиссы дизайна.
- **Coder:** реализует сфокусированное изменение.
- **Reviewer:** независимо проверяет предложенный результат и выявляет пробелы.
- **Tester:** проверяет поведение и граничные случаи.
- **Security reviewer:** ищет утечки учётных данных и небезопасные изменения.
- **Researcher или analyst:** собирает и сравнивает свидетельства для задач
  интеллектуальной работы.
- **Documentation agent:** поддерживает соответствие пользовательской и
  разработческой документации реальному поведению.

Роли описывают обязанности, а не постоянные назначения провайдеров. Один и тот же
провайдер может выполнять разные роли, а разные провайдеры могут выполнять шаги
primary и review. Маршрутизация должна использовать общие интерфейсы и объявленные
capabilities, а не встраивать знание SDK конкретного провайдера в оркестратор.

## Продуктовые принципы

### Полномочия пользователя явны

Пользователь остаётся ответственным за цель и за полномочия, выданные Forge AI.
Агенты не должны расширять рамки задачи, обращаться к посторонним проектам,
изменять production или выполнять деструктивные операции самостоятельно. Когда
действие требует одобрения, Forge AI должен сначала показать конкретное,
проверяемое предложение.

### Результаты трассируемы

Задачи, решения о маршрутизации, результаты агентов, ревью и результаты
верификации должны быть понятны после выполнения. Важные архитектурные решения
принадлежат документации проекта, а Git остаётся источником истины для истории
кода.

### Провайдеры заменяемы

Модели и провайдеры продолжат меняться. Forge AI должен изолировать эти изменения
за адаптерами провайдеров и держать выполнение задач, политику маршрутизации и роли
агентов провайдер-нейтральными.

### Маршрутизация объяснима

Маршрутизация должна начинаться с предсказуемых правил, основанных на категории
задачи, настроенной доступности, объявленных capabilities и политике. Стоимость,
задержка, качество и бюджет могут стать дополнительными ограничениями, но они не
должны превращать маршрутизацию в непрозрачное решение. У пользователей должно
оставаться явное переопределение провайдера.

### Контекст и стоимость управляются

Агенты должны получать наименьший полезный контекст. Система должна избегать
повторного чтения неизменённых файлов, повторения работы и вызова лишних моделей
без ясной выгоды. Использование должно быть видимым там, где это доступно; оценки
не должны представляться как фактические списания.

### Ревью — отдельная обязанность

Ревью — это оценка результата, а не разрешение его изменять. Будущие workflow могут
предлагать исправления после ревью, но эти исправления должны быть ограниченными,
видимыми и управляться авторизацией пользователя. Циклы агентов всегда должны
иметь явные лимиты и ясное условие остановки.

### Секреты остаются вне истории проекта

Учётные данные принадлежат локальному хранилищу секретов или одобренным системам
управления секретами. Они не должны появляться в исходниках, выводе задач, логах,
контексте ревью, когда не нужны, или в истории Git. Метаданные аккаунтов
провайдеров остаются отдельными от API-credentials.

## Направление roadmap

Roadmap основан на возможностях, а не на датах. Каждый шаг должен строиться на
существующих провайдер-нейтральных интерфейсах и сохранять offline-тесты.

### Foundation — реализовано

- Провайдер-нейтральные интерфейсы задач, результатов, использования и агентов.
- Реестр провайдеров, адаптеры, локальное разрешение секретов и настройки runtime.
- Детерминированная классификация задач и маршрутизация с учётом capabilities.
- Ограниченный последовательный fallback провайдеров.
- Одно выполнение primary, не более одной ревизии и финальное ревью при запросе
  изменений.

### Надёжные проектные workflow — следующее направление

- Явные планы, критерии приёмки и зависимости задач.
- Лучшие связи-свидетельства между запрошенной работой, изменёнными файлами и
  верификацией.
- Политики ревью, различающие одобрение, запрошенные изменения и нерешённые
  вопросы.
- Более полные метаданные модели, capabilities, бюджета и задержки.

### Контролируемые проектные операции — будущее направление

- Контекст и инструменты в рамках проекта с узкими разрешениями.
- Предлагаемые изменения файлов, которые пользователи могут проверить до применения.
- Изолированное выполнение и воспроизводимая верификация там, где это уместно.
- Гейты одобрения для деструктивных операций, коммитов, публикации и деплоя в
  production.

### Долговременная коллаборация — будущее направление

- Память проекта, которая явна, проверяема и ограничена нужным проектом.
- Многоагентные workflow с ограниченной параллельной работой там, где она даёт
  ценность.
- Аудируемые истории планов, решений, результатов и одобрений пользователя.
- Необязательные интеграции с командными инструментами и платформами разработки.

Эти направления — цели, а не обещания о дате релиза. Каждая возможность должна
добавляться только тогда, когда её разрешения, обработка отказов, наблюдаемость и
тестирование могут быть сделаны ясными.

## Текущая граница

Forge AI — фундамент оркестрации, а не самоуправляемая организация по разработке ПО.
Он умеет направлять задачи через настроенных провайдеров и выполнять ограниченный
workflow primary, review и одной ревизии. Сейчас он не создаёт планы проектов, не
хранит долговременную память проектов, не редактирует проект пользователя
автономно и не выполняет деплой ПО. Эти границы должны оставаться явными по мере
роста системы.

О реализованных интерфейсах и деталях выполнения см.
[`ARCHITECTURE.md`](ARCHITECTURE.md). Об устойчивых проектных решениях см.
[`DECISIONS.md`](DECISIONS.md).

Долгосрочные границы системы и целевые ограничения определены в
[`Technical Specification`](TECHNICAL_SPECIFICATION.md).

## Идентичность продукта Forge

Forge AI — самостоятельный продукт и должен иметь собственную визуальную
идентичность в будущих клиентах и каналах распространения. Генерируемые проекты —
отдельные продукты со своими названиями, логотипами, иконками, цветами и стилем
интерфейса; они не наследуют брендинг Forge автоматически. Этот принцип не
выбирает логотип, палитру, UI framework или технологию реализации.
