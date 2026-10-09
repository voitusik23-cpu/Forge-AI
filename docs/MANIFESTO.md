# Forge AI Manifesto
## The engineering factory for turning intent into verified results

**Status:** Product manifesto and long-term intent. This document describes what Forge AI is meant to become; it is not a claim that every capability is implemented today.

For the expanded product direction, target architecture, stage-by-stage roadmap, autonomy guardrails, and immediate priorities, see [MASTER_VISION.md](MASTER_VISION.md). This manifesto states the durable principles; the master vision translates them into a development direction.

## 1. Why Forge exists

A powerful model can produce an answer. A useful engineering system must do more: understand the goal, expose uncertainty, make a plan, coordinate the right capabilities, work within an authorized boundary, verify the result, explain what happened, and preserve the knowledge that should survive the task.

**Forge AI exists to turn human intent into dependable, inspectable work.**

Forge is not itself a model. It is the engineering system around models, agents, tools, projects, execution environments, verification, and knowledge.

## 2. The ambition

Our long-term ambition is an **AI Engineering Factory**: a system that can take a goal, an existing codebase, a website, or a dataset and guide the work through a complete, evidence-backed lifecycle:

1. Discover the real problem and inspect available material.
2. Separate evidence, assumptions, unknowns, and risks.
3. Define the project brief, requirements, and acceptance criteria.
4. Make and record architecture and product decisions.
5. Build a plan whose steps and dependencies can be inspected.
6. Coordinate suitable models, agents, skills, and tools.
7. Implement changes in a controlled workspace.
8. Review and verify the result against explicit criteria.
9. Deliver a usable outcome with evidence, limitations, and next steps.
10. Preserve project-specific memory and submit reusable lessons to a governed review process.

This is the destination. Every capability must be marked accurately as implemented, partial, experimental, planned, or deferred. A roadmap is not proof of implementation.

## 3. One product, multiple capabilities

Forge should grow as one coherent system—not as a collection of disconnected demos.

- **Platform** manages users, projects, access, policies, budgets, runs, and the user-facing experience.
- **Core** coordinates authorized execution, lifecycle, boundaries, cancellation, verification, and results.
- **Fabric** helps select suitable models and tools; it does not grant permissions or execute work.
- **Models and providers** are interchangeable capabilities behind explicit adapters, not the architecture's center.
- **Agents and skills** contribute specialized work under shared contracts and bounded responsibilities.
- **Memory and knowledge governance** preserve project context and promote reusable knowledge only through review.
- **Desktop and web** should eventually present one shared project and run history. The execution location and whether a worker is still available must remain visible.

The user should not need to stitch the system together manually or remember which chat contains a critical decision.

## 4. Principles we will not trade away

### Authority is explicit

**Recommendation is not authorization. Authorization is not execution.** An AI-generated plan, model response, tool description, or agent confidence must never grant itself new permissions.

### Evidence beats confident language

Forge must distinguish what it intended, what it attempted, what actually happened, and what was verified. Test output, exit codes, diffs, state transitions, and other inspectable artifacts matter more than an agent's claim of success.

### Safety is an engineering property

Prompts and deny-lists are not security boundaries. Access control, workspace isolation, secret handling, process lifecycle, cancellation, resource limits, and recovery must be enforced and tested at the appropriate system boundary.

### The system must be honest about uncertainty

Unknown telemetry is unknown, not zero. A stale heartbeat is not proof that a process died. A failed verification is not a successful delivery. Missing evidence must be shown as missing.

### Autonomy has boundaries

Forge should become increasingly capable of planning and carrying work forward, but autonomy is always bounded by the user's intent, authorization, project policy, budget, and risk level. Destructive actions, publication, release, and production deployment require explicit policy and approval appropriate to their impact.

### Preserve the owner's work

Forge must not casually discard or overwrite user changes. Checkpoints and rollback must be designed to preserve work that existed before a run, including uncommitted changes.

### Memory must be governed

Project memory belongs to its project and should retain provenance. A lesson observed once does not automatically become global Forge knowledge. Reusable knowledge requires evaluation, evidence, and an approval path.

### Prefer a reliable vertical slice over a broad illusion

Build a small end-to-end path, verify it, document its limits, and then extend it. More agents, more integrations, and more autonomy are not inherently better if the execution foundation is unreliable.

## 5. Autonomy as a progression

Forge may offer different operating modes—such as Observe, Assist, Execute, and Autonomous—but these modes describe the user's chosen level of delegation; they never override permissions, policy, safety controls, or budgets.

Greater autonomy must be earned through evidence: reliable execution, bounded resource use, secure isolation, recoverable state, transparent logs, verified outcomes, and explicit acceptance criteria.

## 6. How we build Forge

We will develop Forge in deliberate stages:

1. **Harden the foundation:** resolve security and execution-lifecycle blockers; make tests deterministic and preserve existing user work.
2. **Complete the execution contract:** connect Platform and Core through durable run identity, idempotency, lifecycle transitions, cancellation, verification, evidence, and telemetry.
3. **Improve routing and Fabric:** choose capabilities based on requirements, availability, quality, and cost without confusing selection with authority.
4. **Coordinate specialized agents:** introduce parallel or multi-agent work only where it improves results and remains bounded and auditable.
5. **Strengthen memory and knowledge governance:** preserve project history and carefully promote reusable, evidenced knowledge.
6. **Unify the experience:** deliver coherent desktop and web workflows over shared project/run state and history.
7. **Expand managed autonomy:** increase delegation only after the lower layers meet explicit acceptance gates.

A stage is not complete because an agent says it is complete. Completion requires reviewed changes, reproducible tests, recorded evidence, and acceptance against its gate.

## 7. The working agreement for humans and agents

Every contributor—human or AI—must:

- read the continuity and source-of-truth documents before continuing work from another session;
- inspect the actual branch, commit, working-tree changes, and untracked files;
- distinguish facts, hypotheses, proposals, owner decisions, and implemented behavior;
- prefer read-only analysis when scope or authority is unclear;
- propose small, reviewable changes with acceptance criteria;
- preserve user-owned changes and never commit, push, reset, clean, or overwrite without the required authorization;
- update durable documentation when an architectural decision or implementation status changes;
- report test failures and limitations plainly instead of hiding them behind a summary.

## 8. What success looks like

A user can bring Forge a meaningful goal and receive more than generated text: a traceable process, controlled changes, independently checkable results, a clear account of what remains uncertain, and a usable deliverable. The user can inspect, correct, pause, cancel, or reject work. Projects retain their own history, and reusable knowledge is promoted responsibly.

**The promise is not that Forge will never fail. The promise is that its authority is bounded, its work is inspectable, its failures are visible, and its capabilities grow through evidence rather than claims.**

---

# Манифест Forge AI
## Инженерная фабрика, превращающая намерение в проверяемый результат

**Статус:** продуктовое видение и долгосрочное намерение. Документ описывает, чем должен стать Forge AI, но не утверждает, что все перечисленные возможности уже реализованы.

Расширенное описание продуктового направления, целевой архитектуры, этапов развития, ограничений автономности и ближайших приоритетов находится в [MASTER_VISION.md](MASTER_VISION.md). Этот манифест закрепляет устойчивые принципы, а master vision переводит их в направление разработки.

## 1. Зачем существует Forge

Мощная модель может выдать ответ. Полезная инженерная система должна сделать больше: понять цель, обозначить неопределённость, составить план, подобрать нужные возможности, работать в разрешённых границах, проверить результат, объяснить произошедшее и сохранить знания, которые должны пережить отдельную задачу.

**Forge AI существует, чтобы превращать человеческое намерение в надёжную и проверяемую работу.**

Forge — не отдельная модель. Это инженерная система вокруг моделей, агентов, инструментов, проектов, сред исполнения, проверки и знаний.

## 2. Наша цель

Долгосрочная цель — **AI Engineering Factory**, которая помогает провести цель, существующий код, сайт или набор данных через полный цикл работы, подкреплённый доказательствами:

1. Исследовать реальную задачу и доступные материалы.
2. Отделить факты, предположения, неизвестное и риски.
3. Сформировать описание проекта, требования и критерии приёмки.
4. Принимать и фиксировать архитектурные и продуктовые решения.
5. Создать проверяемый план с понятными зависимостями.
6. Координировать подходящие модели, агентов, навыки и инструменты.
7. Вносить изменения в контролируемом рабочем пространстве.
8. Проверять результат по явным критериям.
9. Передавать пригодный к использованию результат вместе с доказательствами, ограничениями и следующими шагами.
10. Сохранять память конкретного проекта, а повторно используемые выводы направлять на управляемую проверку.

Это целевое состояние. Каждая возможность должна честно обозначаться как реализованная, частичная, экспериментальная, запланированная или отложенная. Запись в плане развития не доказывает реализацию.

## 3. Один продукт, много возможностей

Forge должен развиваться как единая система, а не как набор несвязанных демонстраций.

- **Platform** управляет пользователями, проектами, доступом, политиками, бюджетами, запусками и пользовательским интерфейсом.
- **Core** координирует разрешённое исполнение, жизненный цикл, границы доступа, отмену, проверку и результаты.
- **Fabric** помогает выбирать подходящие модели и инструменты, но не выдаёт разрешения и не исполняет работу.
- **Модели и провайдеры** — взаимозаменяемые возможности за явными адаптерами, а не центр архитектуры.
- **Агенты и навыки** выполняют специализированную работу в рамках общих контрактов и ограниченных полномочий.
- **Память и управление знаниями** сохраняют контекст проекта и продвигают повторно используемые знания только после проверки.
- **Desktop и web** должны со временем показывать общую историю проектов и запусков. Пользователь всегда должен понимать, где выполняется задача и доступен ли исполнитель.

Пользователь не должен вручную склеивать систему или вспоминать, в каком чате осталось важное решение.

## 4. Принципы, которыми мы не поступимся

### Полномочия должны быть явными

**Рекомендация — не авторизация. Авторизация — не исполнение.** План, ответ модели, описание инструмента или уверенность агента не могут самостоятельно расширять его полномочия.

### Доказательства важнее уверенных слов

Forge должен различать намерение, попытку исполнения, фактически произошедшее действие и подтверждённый результат. Выводы тестов, коды завершения, diff, переходы состояний и другие проверяемые артефакты важнее заявления агента об успехе.

### Безопасность — свойство инженерной системы

Промпты и deny-list не являются границами безопасности. Контроль доступа, изоляция рабочего пространства, защита секретов, жизненный цикл процессов, отмена, лимиты ресурсов и восстановление должны обеспечиваться и проверяться на соответствующем системном уровне.

### Система обязана честно показывать неопределённость

Неизвестная телеметрия — это неизвестное значение, а не ноль. Устаревший heartbeat не доказывает смерть процесса. Неуспешная проверка не является успешной поставкой. Отсутствующие доказательства нужно обозначать как отсутствующие.

### Автономность имеет границы

Forge должен постепенно лучше планировать и доводить работу до результата, но автономность всегда ограничена намерением пользователя, выданными полномочиями, политикой проекта, бюджетом и уровнем риска. Разрушительные действия, публикация, выпуск и развёртывание в production требуют явных правил и одобрения, соответствующих последствиям.

### Работа владельца должна сохраняться

Forge не должен бездумно удалять или перезаписывать пользовательские изменения. Контрольные точки и откат должны сохранять состояние до запуска, включая незакоммиченные изменения.

### Память должна управляться

Память проекта принадлежит этому проекту и должна сохранять происхождение сведений. Вывод, однажды замеченный агентом, не становится глобальным знанием Forge автоматически. Для повторного использования нужны оценка, доказательства и процедура одобрения.

### Надёжный сквозной сценарий лучше широкой иллюзии

Нужно построить небольшой сквозной сценарий, проверить его, задокументировать ограничения и лишь затем расширять. Большее число агентов, интеграций и автономных действий само по себе не улучшает систему, если фундамент исполнения ненадёжен.

## 5. Автономность как последовательное развитие

Forge может предлагать режимы Observe, Assist, Execute и Autonomous. Они описывают выбранный пользователем уровень делегирования, но никогда не отменяют разрешения, политики, ограничения безопасности или бюджеты.

Более высокая автономность должна быть заслужена доказательствами: надёжным исполнением, ограниченным расходом ресурсов, безопасной изоляцией, восстанавливаемым состоянием, прозрачными логами, проверяемыми результатами и явными критериями приёмки.

## 6. Как мы строим Forge

Развитие идёт последовательными этапами:

1. **Укрепить фундамент:** устранить блокеры безопасности и жизненного цикла исполнения, сделать тесты детерминированными и сохранить существующую работу владельца.
2. **Завершить контракт исполнения:** связать Platform и Core через устойчивую идентичность запусков, идемпотентность, переходы состояний, отмену, проверку, доказательства и телеметрию.
3. **Улучшить маршрутизацию и Fabric:** выбирать возможности по требованиям, доступности, качеству и стоимости, не смешивая выбор с полномочиями.
4. **Координировать специализированных агентов:** вводить параллельную и многоагентную работу только там, где она улучшает результат и остаётся ограниченной и проверяемой.
5. **Укрепить память и управление знаниями:** сохранять историю проекта и ответственно продвигать повторно используемые знания, подкреплённые доказательствами.
6. **Объединить пользовательский опыт:** создать согласованные desktop- и web-сценарии поверх общей истории и состояния проектов/запусков.
7. **Расширять управляемую автономность:** увеличивать делегирование только после выполнения нижележащих критериев приёмки.

Этап не считается завершённым потому, что агент объявил о завершении. Нужны проверенные изменения, воспроизводимые тесты, зафиксированные доказательства и приёмка по установленным критериям.

## 7. Рабочее соглашение людей и агентов

Каждый участник — человек или ИИ — обязан:

- перед продолжением работы из другого чата читать документы непрерывности и источника истины;
- проверять реальную ветку, коммит, изменения рабочего дерева и неотслеживаемые файлы;
- различать факты, гипотезы, предложения, решения владельца и реализованное поведение;
- предпочитать read-only анализ, если объём работ или полномочия неясны;
- предлагать небольшие проверяемые изменения с критериями приёмки;
- сохранять работу владельца и не выполнять commit, push, reset, clean или перезапись без необходимого разрешения;
- обновлять долговременную документацию при изменении архитектурных решений или статуса реализации;
- прямо сообщать о падениях тестов и ограничениях, а не скрывать их за общим резюме.

## 8. Как выглядит успех

Пользователь приносит Forge значимую цель и получает больше, чем сгенерированный текст: прослеживаемый процесс, контролируемые изменения, независимо проверяемые результаты, ясное описание неизвестного и пригодный к использованию результат. Пользователь может проверить, скорректировать, приостановить, отменить или отклонить работу. У каждого проекта остаётся собственная история, а повторно используемые знания продвигаются ответственно.

**Обещание не в том, что Forge никогда не ошибётся. Обещание в том, что его полномочия ограничены, работа проверяема, ошибки видимы, а возможности растут благодаря доказательствам, а не заявлениям.**
