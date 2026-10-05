# Forge AI — Source of Truth: Implementation Status

> **Purpose.** This file is the single place where a future AI agent can learn
> what is actually implemented in this repository, what is only planned, what is
> experimental, and what has been rejected or deferred — without access to any
> prior chat history.
>
> **Authority.** For **current state**, the source code and the passing test
> suite are authoritative, not this document. This file is a verified index into
> the code; if it disagrees with the code, the code wins and this file is wrong.
> For **target** architecture, [`TECHNICAL_SPECIFICATION.md`](TECHNICAL_SPECIFICATION.md)
> is authoritative. For **current** architecture in detail, see
> [`ARCHITECTURE.md`](ARCHITECTURE.md).
>
> **Verified baseline.** Commit `33d3af5` (`feat: add Together AI provider`),
> working tree at the time of writing. Verification command:
> `python -m unittest discover tests` → **755 tests, OK (skipped=2)**.

## Status legend

| Status | Meaning |
| --- | --- |
| **CURRENT / VERIFIED** | Implemented in `app/` and covered by passing tests. Behaviour described below is observable today. |
| **PARTIAL** | Implemented but incomplete relative to its own principle, or implemented with a known limitation that is documented here. |
| **PLANNED / FUTURE** | Designed or specified, not implemented. Must never be described in the present tense. |
| **EXPERIMENTAL / CANDIDATE** | A proposal awaiting an owner decision; see [`TECHNOLOGY_RADAR.md`](TECHNOLOGY_RADAR.md). |
| **REJECTED / DEFERRED** | Considered and not adopted, or postponed. See the rejected-alternatives register in [`DECISIONS.md`](DECISIONS.md). |

Rule: **architecture status cannot be upgraded without evidence.** A roadmap
entry, a stage document, or an external review is not evidence of implementation.

## 1. Product identity (permanent)

Forge AI is an **AI Engineering Factory**, not a coding assistant, not a
multi-model chat, not a proxy, and not an IDE plugin. Its long-term lifecycle is
the one preserved in [`FORGE_VISION.md`](FORGE_VISION.md) and
[`TECHNICAL_SPECIFICATION.md`](TECHNICAL_SPECIFICATION.md):

```text
Human Goal / Existing Software / Website / Data
        -> Discovery
        -> Evidence + Unknowns
        -> Project Brief
        -> Requirements + Acceptance Criteria
        -> Decisions / Approvals
        -> Architecture
        -> Technical Specification
        -> Plan
        -> Implementation
        -> Review
        -> Verification
        -> Ready-to-run Project
        -> Project Memory
        -> Candidate Forge Knowledge
        -> Evaluation
        -> Approved Forge Knowledge
```

**Status: PLANNED / FUTURE as an end-to-end lifecycle.** Currently implemented
are the orchestration, execution, decision, verification and memory sub-systems
listed in §3. Discovery, Project Brief, Architecture/Specification generation,
Ready-to-run Project generation, and the Candidate -> Approved knowledge
promotion workflow do **not** exist as runnable Forge behaviour.

## 2. Non-negotiable architectural principles

These are permanent and are not implementation claims. Violating one of them is
an architecture defect, not a feature choice.

- `Provider != Model != Agent != Skill != Capability != Tool`.
- `Recommendation != Authorization != Execution`.
- AI/LLM decisions never grant execution authority.
- Forge Core remains provider-neutral.
- Generated projects remain independent from Forge Core unless an explicit
  dependency is approved.
- Secrets never belong in source code, Git, ordinary context, logs, or model
  prompts.
- External skills, tools, and catalogs are untrusted until evaluated.
- MCP is not the permission model.
- More agents does not mean better results.
- Retries, rollback, parallelism and swarm execution are not universalized.
- Architecture status cannot be upgraded without evidence.

## 3. Verified implementation matrix

Evidence column: the module that holds the behaviour, and the test file that
covers it. Test file names are the verification; they run in the default
`unittest discover tests` suite.

### 3.1 Orchestration, providers, routing

| Area | Status | Evidence |
| --- | --- | --- |
| Orchestrator / agent registry / provider-neutral task dispatch | **CURRENT / VERIFIED** | `app/orchestrator/orchestrator.py`, `app/agents/` — `tests/test_orchestrator.py`, `tests/test_routing_smoke.py` |
| Provider layer (OpenAI, Anthropic, Gemini, DeepSeek, OpenRouter, Groq, Together, Mock, xAI placeholder) | **CURRENT / VERIFIED** (xAI unconfigured placeholder) | `app/agents/providers/` — `tests/test_providers.py`, `tests/test_openai_provider.py`, `tests/test_anthropic_provider.py`, `tests/test_gemini_provider.py`, `tests/test_openai_compatible_providers.py`, `tests/test_together_provider.py` |
| Provider capabilities metadata | **CURRENT / VERIFIED** | `app/agents/providers/capabilities.py` — `tests/test_provider_capabilities.py` |
| Dispatcher, deterministic classification, cost-aware routing | **CURRENT / VERIFIED** | `app/orchestrator/dispatcher.py`, `app/orchestrator/classification.py` — `tests/test_dispatcher.py`, `tests/test_task_classification.py` |
| Provider fallback (finite, sequential) | **CURRENT / VERIFIED** | `app/orchestrator/` — `tests/test_dispatcher.py` |
| SecretStore (lazy resolution, env before `.env`) | **CURRENT / VERIFIED** | `app/config/secrets.py` — `tests/test_secrets.py`, `tests/test_secret_redaction.py` |

### 3.2 Multi-agent execution, planning, tasks

| Area | Status | Evidence |
| --- | --- | --- |
| Multi-agent primary + independent reviewer | **CURRENT / VERIFIED** | `app/agents/reviewer.py`, `app/orchestrator/multi_agent.py` — `tests/test_multi_agent.py` |
| Bounded revision loop (at most one revision) | **CURRENT / VERIFIED** | `app/orchestrator/revision.py` — `tests/test_revision.py` |
| Deterministic Project Planner v0.1 (static plan, no execution) | **CURRENT / VERIFIED** | `app/planning/planner.py` — `tests/test_planner.py` |
| TaskSpecification input boundary | **CURRENT / VERIFIED** | `app/tasks/specification.py` — `tests/test_engineering_execution_profile.py` |
| Requirement ↔ Acceptance traceability | **CURRENT / VERIFIED** | `app/tools/acceptance.py`, `app/tasks/specification.py` — `tests/test_requirement_acceptance_traceability.py` |
| Project State contract | **CURRENT / VERIFIED** | `app/projects/state.py` — `tests/test_project_state.py` |
| Engineering Run executor | **CURRENT / VERIFIED** | `app/orchestrator/engineering.py` — `tests/test_engineering.py`, `tests/test_engineering_execution_integration.py` |
| Run trace / event contract | **CURRENT / VERIFIED** | `app/orchestrator/trace.py` — `tests/test_run_trace.py` |
| Integration failure matrix | **CURRENT / VERIFIED** | `tests/test_integration_failure_matrix.py` |

### 3.3 Decision layer

| Area | Status | Evidence |
| --- | --- | --- |
| Decision layer / run-control recommendations (no execution authority) | **CURRENT / VERIFIED** | `app/decision/` — `tests/test_decision.py` |
| Decision context envelope | **CURRENT / VERIFIED** | `app/context/` — `tests/test_decision_context.py` |
| Real AI decision provider (advisory only; fails closed) | **CURRENT / VERIFIED** | `app/decision/ai_provider.py` — `tests/test_ai_decision_provider.py` |
| Bounded Agent Harness (explicit phase loop, one action per iteration) | **CURRENT / VERIFIED** | `app/agent_runtime/harness.py`, `app/agent_runtime/models.py` — `tests/test_agent_harness.py` |
| Real-AI + harness integration | **CURRENT / VERIFIED** | `tests/test_agent_harness_ai_integration.py` |
| Agent Skill System (registry, evaluator, builtin skills) | **CURRENT / VERIFIED** | `app/skills/` — `tests/test_skill_system.py` |
| Multi-skill agent run | **CURRENT / VERIFIED** | `tests/test_multi_skill_harness.py` |

Note: `app/reviews/` contains only an empty package marker. There is **no**
review-engine module; the reviewer exists as an agent inside
`app/agents/reviewer.py`.

### 3.4 Execution plane and authorization (v0.2)

| Area | Status | Evidence |
| --- | --- | --- |
| Execution plane contracts (`ProjectExecutionProfile`, `ExecutionRequest`, `ExecutionResult`) | **CURRENT / VERIFIED** | `app/execution/profile.py`, `app/execution/request.py` — `tests/test_execution_profile.py`, `tests/test_execution_request_result.py` |
| Permission boundary | **CURRENT / VERIFIED** | `app/execution/authorizer.py` (`allowed_commands` default-deny) — `tests/test_execution_authorization_hardening.py`, `tests/test_execution_authorization_hardening_v02.py` |
| Approval boundary (intent-bound, single-use) | **CURRENT / VERIFIED** | `app/tools/approval.py`, `app/execution/authorizer.py` — `tests/test_approval_boundary.py`, `tests/test_execution_authorization_hardening_v02.py` |
| Workspace boundary for tools (`Workspace`) | **CURRENT / VERIFIED** | `app/tools/workspace.py` — `tests/test_workspace_boundary.py` |
| Immutable `RunScope` trust boundary (external content is data, never authority) | **CURRENT / VERIFIED** | `app/runtime/run_scope.py`, `app/agent_runtime/harness.py` — `tests/test_run_scope_trust_boundary.py` |
| Canonical path security (`ProgramIdentity`, `WorkspaceRelativePath`) | **CURRENT / VERIFIED** | `app/execution/paths.py` — `tests/test_execution_authorization_hardening_v02.py` |
| Immutable `ExecutionIntent` + canonical fingerprint | **CURRENT / VERIFIED** | `app/execution/intent.py` (`IntentBuilder`, `ExecutionIntent.fingerprint`) — `tests/test_execution_authorization_hardening_v02.py` |
| Capability-based invocation classification | **PARTIAL** — see §4.2 | `app/execution/capabilities.py` |
| `AuthorizedExecution` marker bound to the coordinator | **CURRENT / VERIFIED** | `app/execution/intent.py`, `app/execution/adapter.py` — `tests/test_execution_authorization_hardening_v02.py` |
| Mandatory explicit `workspace_root` (fail-closed) | **CURRENT / VERIFIED** | `app/execution/authorizer.py` — `tests/test_execution_authorization_hardening_v02.py`, `tests/test_execution_plane.py` |
| Execution policy over the intent | **CURRENT / VERIFIED** | `app/execution/policy.py` — `tests/test_execution_policy.py` |
| Local execution adapter (ephemeral workspace, scoped env, `shell=False`) | **CURRENT / VERIFIED** | `app/execution/adapter.py` — `tests/test_local_execution_adapter.py`, `tests/test_execution_plane.py` |
| Verification boundary and acceptance gate | **CURRENT / VERIFIED** | `app/tools/verification.py`, `app/tools/acceptance.py` — `tests/test_verification.py`, `tests/test_verification_contract.py`, `tests/test_acceptance.py` |
| Test verification adapter | **CURRENT / VERIFIED** | `app/tools/test_verification.py` — `tests/test_test_verification_adapter.py` |
| Change sets and project snapshots | **CURRENT / VERIFIED** | `app/tools/changesets.py`, `app/snapshots.py` — `tests/test_changesets.py`, `tests/test_project_snapshots.py` |

### 3.5 Memory and knowledge

| Area | Status | Evidence |
| --- | --- | --- |
| Project Memory (models, store, validator) | **CURRENT / VERIFIED** | `app/memory/` — `tests/test_project_memory.py`; see [`STAGE_PROJECT_MEMORY_V0_1.md`](STAGE_PROJECT_MEMORY_V0_1.md) |
| Knowledge Governance (candidate/review/approval models, store, validator) | **CURRENT / VERIFIED** | `app/knowledge/` — `tests/test_knowledge_governance.py`; see [`STAGE_KNOWLEDGE_GOVERNANCE_V0_1.md`](STAGE_KNOWLEDGE_GOVERNANCE_V0_1.md) |

Do not confuse these with the **lifecycle** stages. The distinguishing rules are:

- **Project Memory** = project-specific durable truth and history. It is scoped
  to a project/run and is not global.
- **Forge Knowledge** = reviewed, reusable knowledge and patterns with
  provenance, applicability, evidence, and an approval decision.
- **Domain Knowledge** = domain-scoped information that remains domain-scoped.

Knowledge does **not** become global because an agent observed it once. Promotion
requires the governance workflow (candidate -> evaluation -> approved).

## 4. Verified limitations

These are current, observed properties of the implementation. They are recorded
so that no future agent re-discovers them, and so that no document claims
otherwise.

### 4.1 Approval fingerprint verification depends on resolver cooperation

`ExecutionCoordinator` builds the `ExecutionIntent`, then asks the resolver with
`ApprovalRequest(intent_fingerprint=intent.fingerprint)`. It verifies the returned
fingerprint **only when the resolver supplies a non-empty one**:

```python
res_fp = getattr(resolution, "approved_fingerprint", "")
...
if res_fp and res_fp != intent.fingerprint:   # empty res_fp short-circuits
```

`InMemoryApprovalResolver` always supplies `approved_fingerprint`, so the shipped
path is safe. A resolver that returns a bare `ApprovalState.APPROVED` (no
fingerprint) authorizes the current intent without any independent binding check.
Observed by probe: an always-`APPROVED`, no-echo resolver produced
`EXECUTION_SUCCESS` for a command that the intent-binding rule is meant to gate.

**Status: PARTIAL (hardening candidate).** See `DECISIONS.md`
("Execution Authorization Contract v0.2" -> unresolved items).

### 4.2 Capability classification is a blacklist, not a capability model

`ExecutionCapability` currently has four members: `EXEC_CHILD`,
`INTERPRET_TEXT`, `INTERPRET_MODULE`, `NETWORK`. `classify_invocation()` derives
them from hard-coded name lists and flag regexes:

- `_WRAPPER_EXECUTABLES` — 15 names (`env`, `xargs`, `sudo`, `doas`, `chroot`,
  `nohup`, `strace`, `ltrace`, `time`, `nice`, `ionice`, `taskset`, `timeout`,
  `watch`, `runas`) plus `find -exec`.
- `_NETWORK_EXECUTABLES` — 16 names plus git network subcommands.
- Interpreter flag regexes for python/node/ruby/perl/php/bash/sh/zsh/dash/ksh,
  powershell/pwsh, cmd.

Anything outside those lists gets `capabilities == frozenset()`, and the policy
then admits a bare-string `allowed_commands` entry with **free-form argv**. This
contradicts the standing principle that security must not depend on an
ever-growing blacklist of executables (`AGENTS.md` §10 and the execution
authorization invariants), and it is the known residual of the F4 class: a
wrapper that nobody has listed yet is not gated.

**Status: PARTIAL.** The invariant enforced today is "a recognised wrapper or
interpreter eval/module flag requires a declared capability", not "unknown
execution behaviour fails closed". The direction recorded for correction is the
declared-invocation / fail-closed-`OPAQUE` model in `DECISIONS.md`.

### 4.3 Intent environment does not include profile environment

`IntentBuilder.from_request()` sets the intent's `environment_variables` from the
**request** only. The adapter later overlays the **profile** environment at
process start (`_build_scoped_environment`). Consequence: a change to
`ProjectExecutionProfile.environment_variables` does not change the intent
fingerprint. Observed by probe: intent env = `{'REQUEST_MARKER': ...}` with the
profile marker absent.

**Status: PARTIAL (hardening candidate).**

### 4.4 There is no OS-level sandbox, and none is claimed

`LocalExecutionAdapter` runs local processes with `shell=False`, an ephemeral
copy of the workspace, a scoped environment whitelist, timeouts with process-tree
termination, output caps, and secret redaction. It does **not** provide
namespaces, cgroups, Job Objects, filesystem confinement enforced by the kernel,
or network enforcement. `network_access=False` is enforced only by setting proxy
environment variables — it is best-effort, not a control.

**Block 2 (sandbox / isolation / workspace-input staging) is NOT implemented.**

### 4.5 Staging is mirror-based, not declared-inputs

`EphemeralWorkspaceManager` copies the supplied workspace root (skipping `.git`
and `__pycache__`) into a temporary scratch directory. There is no declared-input
allow-list, no workspace-identity binding, and no content digest for referenced
inputs. Whatever the caller passes as `workspace_root` is what gets staged.

**Status: PARTIAL** (workspace identity, declared inputs, deny-by-default
staging are Block 2 / deferred).

### 4.6 The execution plane has no production entry point

`app/runtime/bootstrap.py::create_runtime` assembles settings, providers,
registries, the Orchestrator and `RunExecutor`. It does **not** construct an
`ExecutionCoordinator`, an adapter, or a workspace root. The coordinator is
constructed by callers: `app/agent_runtime/harness.py` and
`app/orchestrator/engineering.py`, both from caller-supplied request fields
(`HarnessRequest.workspace`, `EngineeringRunRequest.workspace`). When the
workspace is absent, a local spawn is denied with
`workspace_root_required`.

Consequence: several execution-plane concerns are **latent** rather than live —
they become reachable when a production run loop is wired to the execution plane.

## 5. Planning, deferred and rejected

| Item | Status | Where recorded |
| --- | --- | --- |
| Discovery intelligence, evidence, readiness gates | **PLANNED / FUTURE** | `TECHNICAL_SPECIFICATION.md` §21 |
| Project classification, capability selection | **PLANNED / FUTURE** | `TECHNICAL_SPECIFICATION.md` §22 |
| Durable runs, resumable execution | **PLANNED / FUTURE** | [`STAGE_1_5_EXTERNAL_BENCHMARK.md`](STAGE_1_5_EXTERNAL_BENCHMARK.md) |
| Agent/Skill/Tool/Capability governance beyond the current skill system | **PLANNED / FUTURE** | [`STAGE_1_6_AGENT_SKILL_SYSTEM.md`](STAGE_1_6_AGENT_SKILL_SYSTEM.md) |
| Domain patterns and integrations | **CANDIDATE** | [`STAGE_1_7_EXTERNAL_SOFTWARE_DOMAIN_HARVEST.md`](STAGE_1_7_EXTERNAL_SOFTWARE_DOMAIN_HARVEST.md) |
| Declared-invocation / fail-closed capability model | **PLANNED (candidate for Block 1.1)** | `DECISIONS.md` Execution Authorization Contract v0.2 |
| Block 2 sandbox / isolation / declared workspace inputs | **DEFERRED** | `DECISIONS.md`, `ROADMAP.md` |
| Durable/persistent approval store with expiration | **DEFERRED (separate design)** | `DECISIONS.md` rejected-alternatives register |
| MCP as permission model | **REJECTED** | `DECISIONS.md` |
| Universal retries / rollback / parallelism / swarm execution | **REJECTED as defaults** | `AGENTS.md`, `TECHNICAL_SPECIFICATION.md` §18 |
| Whole-workspace content digests, cryptographic approval tokens, field registries | **REJECTED for the current block** | `DECISIONS.md` rejected-alternatives register |
| Technology candidates (NaraRouter, NVIDIA, QwenCloud, OmniRoute, Headroom, Task Observer, Claude-Mem, Everything Claude Code, Matt Pocock Skills, Ghidra, DBeaver, MarkItDown, MCP, Runnable, Kimi Work, Canva connector) | **CANDIDATE / WATCH** | [`TECHNOLOGY_RADAR.md`](TECHNOLOGY_RADAR.md) |

## 6. How to keep this file true

1. Change code, then change this file — never the reverse.
2. Never move an item from PLANNED to CURRENT without a passing test that
   exercises it and a commit that contains it.
3. When a new execution-affecting field, boundary, or subsystem appears, add a
   row here and record the decision in `DECISIONS.md`.
4. When a limitation in §4 is fixed, delete the limitation and note the fix in
   `DECISIONS.md`; do not leave stale "known limitation" text behind.

---

# Forge AI — Source of Truth: статус реализации — русская версия

> **Назначение.** Этот файл — единственное место, где будущий AI-агент может
> узнать, что реально реализовано в этом репозитории, что только запланировано,
> что экспериментально и что отклонено или отложено, — без доступа к предыдущей
> истории чатов.
>
> **Авторитетность.** Для **текущего состояния** авторитетны исходный код и
> проходящий набор тестов, а не этот документ. Этот файл — проверенный индекс по
> коду; если он расходится с кодом, побеждает код, а этот файл неверен. Для
> **целевой** архитектуры авторитетен
> [`TECHNICAL_SPECIFICATION.md`](TECHNICAL_SPECIFICATION.md). Подробное описание
> **текущей** архитектуры — в [`ARCHITECTURE.md`](ARCHITECTURE.md).
>
> **Проверенная база.** Коммит `33d3af5` (`feat: add Together AI provider`),
> рабочее дерево на момент написания. Команда проверки:
> `python -m unittest discover tests` → **755 tests, OK (skipped=2)**.

## Легенда статусов

| Статус | Значение |
| --- | --- |
| **CURRENT / VERIFIED** | Реализовано в `app/` и покрыто проходящими тестами. Описанное ниже поведение наблюдаемо сегодня. |
| **PARTIAL** | Реализовано, но неполно относительно собственного принципа, либо реализовано с известным ограничением, задокументированным здесь. |
| **PLANNED / FUTURE** | Спроектировано или специфицировано, но не реализовано. Никогда не должно описываться в настоящем времени. |
| **EXPERIMENTAL / CANDIDATE** | Предложение, ожидающее решения владельца; см. [`TECHNOLOGY_RADAR.md`](TECHNOLOGY_RADAR.md). |
| **REJECTED / DEFERRED** | Рассмотрено и не принято либо отложено. См. реестр отклонённых альтернатив в [`DECISIONS.md`](DECISIONS.md). |

Правило: **статус архитектуры нельзя повысить без доказательства.** Пункт
roadmap, stage-документ или внешнее ревью не являются доказательством реализации.

## 1. Идентичность продукта (постоянно)

Forge AI — это **AI Engineering Factory**, а не ассистент для кодинга, не
мультимодельный чат, не прокси и не плагин IDE. Его долгосрочный жизненный цикл —
тот, что зафиксирован в [`FORGE_VISION.md`](FORGE_VISION.md) и
[`TECHNICAL_SPECIFICATION.md`](TECHNICAL_SPECIFICATION.md):

```text
Human Goal / Existing Software / Website / Data
        -> Discovery
        -> Evidence + Unknowns
        -> Project Brief
        -> Requirements + Acceptance Criteria
        -> Decisions / Approvals
        -> Architecture
        -> Technical Specification
        -> Plan
        -> Implementation
        -> Review
        -> Verification
        -> Ready-to-run Project
        -> Project Memory
        -> Candidate Forge Knowledge
        -> Evaluation
        -> Approved Forge Knowledge
```

**Статус: PLANNED / FUTURE как сквозной жизненный цикл.** Сейчас реализованы
подсистемы оркестрации, выполнения, решений, верификации и памяти, перечисленные
в §3. Discovery, Project Brief, генерация Architecture/Specification, генерация
Ready-to-run Project и workflow продвижения знаний Candidate -> Approved **не
существуют** как исполняемое поведение Forge.

## 2. Непреложные архитектурные принципы

Они постоянны и не являются утверждениями о реализации. Нарушение любого из них —
дефект архитектуры, а не выбор функциональности.

- `Provider != Model != Agent != Skill != Capability != Tool`.
- `Recommendation != Authorization != Execution`.
- Решения AI/LLM никогда не выдают полномочия на выполнение.
- Ядро Forge остаётся провайдер-нейтральным.
- Генерируемые проекты остаются независимыми от Forge Core, если явно не одобрена
  зависимость.
- Секреты никогда не должны находиться в исходном коде, Git, обычном контексте,
  логах или промптах моделей.
- Внешние skills, tools и каталоги недоверенны до оценки.
- MCP не является моделью разрешений.
- Больше агентов не означает лучший результат.
- Retries, rollback, параллелизм и swarm-исполнение не универсализируются.
- Статус архитектуры нельзя повысить без доказательства.

## 3. Проверенная матрица реализации

Колонка Evidence: модуль, в котором находится поведение, и тестовый файл, который
его покрывает. Имена тестовых файлов и есть проверка; они запускаются в наборе по
умолчанию `unittest discover tests`.

### 3.1 Оркестрация, провайдеры, маршрутизация

| Область | Статус | Evidence |
| --- | --- | --- |
| Orchestrator / реестр агентов / провайдер-нейтральная диспетчеризация задач | **CURRENT / VERIFIED** | `app/orchestrator/orchestrator.py`, `app/agents/` — `tests/test_orchestrator.py`, `tests/test_routing_smoke.py` |
| Слой провайдеров (OpenAI, Anthropic, Gemini, DeepSeek, OpenRouter, Groq, Together, Mock, заглушка xAI) | **CURRENT / VERIFIED** (xAI — ненастроенная заглушка) | `app/agents/providers/` — `tests/test_providers.py`, `tests/test_openai_provider.py`, `tests/test_anthropic_provider.py`, `tests/test_gemini_provider.py`, `tests/test_openai_compatible_providers.py`, `tests/test_together_provider.py` |
| Метаданные capabilities провайдеров | **CURRENT / VERIFIED** | `app/agents/providers/capabilities.py` — `tests/test_provider_capabilities.py` |
| Dispatcher, детерминированная классификация, cost-aware маршрутизация | **CURRENT / VERIFIED** | `app/orchestrator/dispatcher.py`, `app/orchestrator/classification.py` — `tests/test_dispatcher.py`, `tests/test_task_classification.py` |
| Fallback провайдеров (конечный, последовательный) | **CURRENT / VERIFIED** | `app/orchestrator/` — `tests/test_dispatcher.py` |
| SecretStore (ленивое разрешение, окружение процесса раньше `.env`) | **CURRENT / VERIFIED** | `app/config/secrets.py` — `tests/test_secrets.py`, `tests/test_secret_redaction.py` |

### 3.2 Многоагентное выполнение, планирование, задачи

| Область | Статус | Evidence |
| --- | --- | --- |
| Многоагентный primary + независимый reviewer | **CURRENT / VERIFIED** | `app/agents/reviewer.py`, `app/orchestrator/multi_agent.py` — `tests/test_multi_agent.py` |
| Ограниченный цикл ревизии (не более одной ревизии) | **CURRENT / VERIFIED** | `app/orchestrator/revision.py` — `tests/test_revision.py` |
| Детерминированный Project Planner v0.1 (статический план, без выполнения) | **CURRENT / VERIFIED** | `app/planning/planner.py` — `tests/test_planner.py` |
| Входная граница TaskSpecification | **CURRENT / VERIFIED** | `app/tasks/specification.py` — `tests/test_engineering_execution_profile.py` |
| Трассируемость Requirement ↔ Acceptance | **CURRENT / VERIFIED** | `app/tools/acceptance.py`, `app/tasks/specification.py` — `tests/test_requirement_acceptance_traceability.py` |
| Контракт Project State | **CURRENT / VERIFIED** | `app/projects/state.py` — `tests/test_project_state.py` |
| Исполнитель Engineering Run | **CURRENT / VERIFIED** | `app/orchestrator/engineering.py` — `tests/test_engineering.py`, `tests/test_engineering_execution_integration.py` |
| Контракт трассы/событий запуска | **CURRENT / VERIFIED** | `app/orchestrator/trace.py` — `tests/test_run_trace.py` |
| Матрица отказов интеграции | **CURRENT / VERIFIED** | `tests/test_integration_failure_matrix.py` |

### 3.3 Слой решений

| Область | Статус | Evidence |
| --- | --- | --- |
| Слой решений / рекомендации управления запуском (без полномочий выполнения) | **CURRENT / VERIFIED** | `app/decision/` — `tests/test_decision.py` |
| Конверт контекста решений | **CURRENT / VERIFIED** | `app/context/` — `tests/test_decision_context.py` |
| Реальный AI decision provider (только рекомендации; fail-closed) | **CURRENT / VERIFIED** | `app/decision/ai_provider.py` — `tests/test_ai_decision_provider.py` |
| Ограниченный Agent Harness (явный цикл фаз, одно действие за итерацию) | **CURRENT / VERIFIED** | `app/agent_runtime/harness.py`, `app/agent_runtime/models.py` — `tests/test_agent_harness.py` |
| Интеграция реального AI с harness | **CURRENT / VERIFIED** | `tests/test_agent_harness_ai_integration.py` |
| Agent Skill System (реестр, evaluator, встроенные skills) | **CURRENT / VERIFIED** | `app/skills/` — `tests/test_skill_system.py` |
| Запуск агента с несколькими skills | **CURRENT / VERIFIED** | `tests/test_multi_skill_harness.py` |

Примечание: `app/reviews/` содержит только пустой маркер пакета. Модуля
review-engine **нет**; reviewer существует как агент внутри
`app/agents/reviewer.py`.

### 3.4 Execution plane и авторизация (v0.2)

| Область | Статус | Evidence |
| --- | --- | --- |
| Контракты execution plane (`ProjectExecutionProfile`, `ExecutionRequest`, `ExecutionResult`) | **CURRENT / VERIFIED** | `app/execution/profile.py`, `app/execution/request.py` — `tests/test_execution_profile.py`, `tests/test_execution_request_result.py` |
| Граница Permission | **CURRENT / VERIFIED** | `app/execution/authorizer.py` (`allowed_commands` default-deny) — `tests/test_execution_authorization_hardening.py`, `tests/test_execution_authorization_hardening_v02.py` |
| Граница Approval (привязана к интенту, одноразовая) | **CURRENT / VERIFIED** | `app/tools/approval.py`, `app/execution/authorizer.py` — `tests/test_approval_boundary.py`, `tests/test_execution_authorization_hardening_v02.py` |
| Граница Workspace для инструментов (`Workspace`) | **CURRENT / VERIFIED** | `app/tools/workspace.py` — `tests/test_workspace_boundary.py` |
| Неизменяемая граница доверия `RunScope` (внешнее содержимое — данные, а не полномочия) | **CURRENT / VERIFIED** | `app/runtime/run_scope.py`, `app/agent_runtime/harness.py` — `tests/test_run_scope_trust_boundary.py` |
| Каноническая безопасность путей (`ProgramIdentity`, `WorkspaceRelativePath`) | **CURRENT / VERIFIED** | `app/execution/paths.py` — `tests/test_execution_authorization_hardening_v02.py` |
| Неизменяемый `ExecutionIntent` + канонический fingerprint | **CURRENT / VERIFIED** | `app/execution/intent.py` (`IntentBuilder`, `ExecutionIntent.fingerprint`) — `tests/test_execution_authorization_hardening_v02.py` |
| Классификация инвокаций на основе capabilities | **PARTIAL** — см. §4.2 | `app/execution/capabilities.py` |
| Маркер `AuthorizedExecution`, привязанный к координатору | **CURRENT / VERIFIED** | `app/execution/intent.py`, `app/execution/adapter.py` — `tests/test_execution_authorization_hardening_v02.py` |
| Обязательный явный `workspace_root` (fail-closed) | **CURRENT / VERIFIED** | `app/execution/authorizer.py` — `tests/test_execution_authorization_hardening_v02.py`, `tests/test_execution_plane.py` |
| Execution policy по интенту | **CURRENT / VERIFIED** | `app/execution/policy.py` — `tests/test_execution_policy.py` |
| Локальный адаптер выполнения (временный workspace, ограниченное окружение, `shell=False`) | **CURRENT / VERIFIED** | `app/execution/adapter.py` — `tests/test_local_execution_adapter.py`, `tests/test_execution_plane.py` |
| Граница верификации и acceptance gate | **CURRENT / VERIFIED** | `app/tools/verification.py`, `app/tools/acceptance.py` — `tests/test_verification.py`, `tests/test_verification_contract.py`, `tests/test_acceptance.py` |
| Адаптер верификации тестов | **CURRENT / VERIFIED** | `app/tools/test_verification.py` — `tests/test_test_verification_adapter.py` |
| Change sets и снапшоты проектов | **CURRENT / VERIFIED** | `app/tools/changesets.py`, `app/snapshots.py` — `tests/test_changesets.py`, `tests/test_project_snapshots.py` |

### 3.5 Память и знания

| Область | Статус | Evidence |
| --- | --- | --- |
| Project Memory (модели, store, validator) | **CURRENT / VERIFIED** | `app/memory/` — `tests/test_project_memory.py`; см. [`STAGE_PROJECT_MEMORY_V0_1.md`](STAGE_PROJECT_MEMORY_V0_1.md) |
| Knowledge Governance (модели candidate/review/approval, store, validator) | **CURRENT / VERIFIED** | `app/knowledge/` — `tests/test_knowledge_governance.py`; см. [`STAGE_KNOWLEDGE_GOVERNANCE_V0_1.md`](STAGE_KNOWLEDGE_GOVERNANCE_V0_1.md) |

Не путайте их со стадиями **жизненного цикла**. Различающие правила таковы:

- **Project Memory** — проектно-специфичная долговременная истина и история. Она
  ограничена проектом/запуском и не является глобальной.
- **Forge Knowledge** — проверенные переиспользуемые знания и паттерны с
  происхождением, применимостью, свидетельствами и решением об одобрении.
- **Domain Knowledge** — информация, ограниченная предметной областью и остающаяся
  в её пределах.

Знание **не** становится глобальным только потому, что агент однажды его
наблюдал. Продвижение требует workflow governance (candidate -> evaluation ->
approved).

## 4. Проверенные ограничения

Это текущие наблюдаемые свойства реализации. Они зафиксированы, чтобы будущий
агент не открывал их заново и чтобы ни один документ не утверждал иного.

### 4.1 Проверка fingerprint одобрения зависит от содействия resolver'а

`ExecutionCoordinator` строит `ExecutionIntent`, затем обращается к resolver'у с
`ApprovalRequest(intent_fingerprint=intent.fingerprint)`. Он проверяет возвращённый
fingerprint **только когда resolver передал непустой**:

```python
res_fp = getattr(resolution, "approved_fingerprint", "")
...
if res_fp and res_fp != intent.fingerprint:   # empty res_fp short-circuits
```

`InMemoryApprovalResolver` всегда передаёт `approved_fingerprint`, поэтому
поставляемый путь безопасен. Resolver, возвращающий голый
`ApprovalState.APPROVED` (без fingerprint), авторизует текущий интент без
независимой проверки привязки. Наблюдено пробой: resolver, всегда возвращающий
`APPROVED` без эха fingerprint, дал `EXECUTION_SUCCESS` для команды, которую
правило привязки к интенту должно было ограничить.

**Статус: PARTIAL (кандидат на усиление).** См. `DECISIONS.md`
("Execution Authorization Contract v0.2" -> нерешённые пункты).

### 4.2 Классификация capabilities — это чёрный список, а не модель capabilities

`ExecutionCapability` сейчас содержит четыре члена: `EXEC_CHILD`,
`INTERPRET_TEXT`, `INTERPRET_MODULE`, `NETWORK`. `classify_invocation()` выводит их
из жёстко заданных списков имён и регулярных выражений флагов:

- `_WRAPPER_EXECUTABLES` — 15 имён (`env`, `xargs`, `sudo`, `doas`, `chroot`,
  `nohup`, `strace`, `ltrace`, `time`, `nice`, `ionice`, `taskset`, `timeout`,
  `watch`, `runas`) плюс `find -exec`.
- `_NETWORK_EXECUTABLES` — 16 имён плюс сетевые подкоманды git.
- Регулярные выражения флагов интерпретаторов для python/node/ruby/perl/php/bash/sh/zsh/dash/ksh,
  powershell/pwsh, cmd.

Всё, что вне этих списков, получает `capabilities == frozenset()`, и тогда политика
допускает строковую запись `allowed_commands` с **произвольным argv**. Это
противоречит действующему принципу, что безопасность не должна зависеть от
бесконечно растущего чёрного списка исполняемых файлов (`AGENTS.md` §10 и
инварианты execution authorization), и это известный остаток класса F4: wrapper,
которого ещё никто не внёс в список, не ограничен.

**Статус: PARTIAL.** Инвариант, действующий сегодня, — «распознанный wrapper или
флаг eval/module интерпретатора требует объявленной capability», а не «неизвестное
поведение выполнения отклоняется fail-closed». Зафиксированное направление
исправления — модель declared-invocation / fail-closed-`OPAQUE` в `DECISIONS.md`.

### 4.3 Окружение интента не включает окружение профиля

`IntentBuilder.from_request()` задаёт `environment_variables` интента только из
**запроса**. Адаптер позже накладывает окружение **профиля** при старте процесса
(`_build_scoped_environment`). Следствие: изменение
`ProjectExecutionProfile.environment_variables` не меняет fingerprint интента.
Наблюдено пробой: окружение интента = `{'REQUEST_MARKER': ...}`, маркер профиля
отсутствует.

**Статус: PARTIAL (кандидат на усиление).**

### 4.4 OS-песочницы нет, и она не заявляется

`LocalExecutionAdapter` запускает локальные процессы с `shell=False`, временной
копией workspace, ограниченным белым списком окружения, таймаутами с завершением
дерева процессов, лимитами вывода и маскированием секретов. Он **не**
предоставляет namespaces, cgroups, Job Objects, файловое ограничение на уровне
ядра или сетевое ограничение. `network_access=False` обеспечивается только
установкой переменных окружения прокси — это best-effort, а не контроль.

**Block 2 (песочница / изоляция / подготовка входов workspace) НЕ реализован.**

### 4.5 Подготовка основана на зеркалировании, а не на объявленных входах

`EphemeralWorkspaceManager` копирует переданный корень workspace (пропуская `.git`
и `__pycache__`) во временный scratch-каталог. Нет белого списка объявленных
входов, нет привязки идентичности workspace и нет digest содержимого для
referenced inputs. Что вызывающий передал как `workspace_root`, то и копируется.

**Статус: PARTIAL** (идентичность workspace, объявленные входы, deny-by-default
подготовка относятся к Block 2 / отложены).

### 4.6 У execution plane нет production-точки входа

`app/runtime/bootstrap.py::create_runtime` собирает настройки, провайдеров,
реестры, Orchestrator и `RunExecutor`. Он **не** создаёт `ExecutionCoordinator`,
адаптер или корень workspace. Координатор создаётся вызывающими:
`app/agent_runtime/harness.py` и `app/orchestrator/engineering.py`, оба — из полей
запроса, переданных вызывающим (`HarnessRequest.workspace`,
`EngineeringRunRequest.workspace`). Когда workspace отсутствует, локальный запуск
отклоняется с `workspace_root_required`.

Следствие: ряд аспектов execution plane **латентны**, а не активны — они становятся
достижимыми, когда production-цикл запуска будет подключён к execution plane.

## 5. Планируемое, отложенное и отклонённое

| Пункт | Статус | Где зафиксировано |
| --- | --- | --- |
| Discovery intelligence, свидетельства, readiness gates | **PLANNED / FUTURE** | `TECHNICAL_SPECIFICATION.md` §21 |
| Классификация проектов, выбор capabilities | **PLANNED / FUTURE** | `TECHNICAL_SPECIFICATION.md` §22 |
| Долговременные запуски, возобновляемое выполнение | **PLANNED / FUTURE** | [`STAGE_1_5_EXTERNAL_BENCHMARK.md`](STAGE_1_5_EXTERNAL_BENCHMARK.md) |
| Governance Agent/Skill/Tool/Capability сверх текущей skill-системы | **PLANNED / FUTURE** | [`STAGE_1_6_AGENT_SKILL_SYSTEM.md`](STAGE_1_6_AGENT_SKILL_SYSTEM.md) |
| Доменные паттерны и интеграции | **CANDIDATE** | [`STAGE_1_7_EXTERNAL_SOFTWARE_DOMAIN_HARVEST.md`](STAGE_1_7_EXTERNAL_SOFTWARE_DOMAIN_HARVEST.md) |
| Модель declared-invocation / fail-closed capability | **PLANNED (кандидат для Block 1.1)** | `DECISIONS.md`, Execution Authorization Contract v0.2 |
| Песочница / изоляция / объявленные входы workspace (Block 2) | **DEFERRED** | `DECISIONS.md`, `ROADMAP.md` |
| Долговременное (durable) хранилище одобрений с истечением срока | **DEFERRED (отдельный дизайн)** | реестр отклонённых альтернатив в `DECISIONS.md` |
| MCP как модель разрешений | **REJECTED** | `DECISIONS.md` |
| Универсальные retries / rollback / параллелизм / swarm-исполнение | **REJECTED as defaults** | `AGENTS.md`, `TECHNICAL_SPECIFICATION.md` §18 |
| Digest всего содержимого workspace, криптографические токены одобрения, реестры полей | **REJECTED for the current block** | реестр отклонённых альтернатив в `DECISIONS.md` |
| Технологические кандидаты (NaraRouter, NVIDIA, QwenCloud, OmniRoute, Headroom, Task Observer, Claude-Mem, Everything Claude Code, Matt Pocock Skills, Ghidra, DBeaver, MarkItDown, MCP, Runnable, Kimi Work, Canva connector) | **CANDIDATE / WATCH** | [`TECHNOLOGY_RADAR.md`](TECHNOLOGY_RADAR.md) |

## 6. Как поддерживать актуальность этого файла

1. Меняйте код, затем меняйте этот файл — никогда наоборот.
2. Никогда не переносите пункт из PLANNED в CURRENT без проходящего теста, который
   его проверяет, и без коммита, который его содержит.
3. Когда появляется новое поле, граница или подсистема, влияющая на выполнение,
   добавьте строку здесь и зафиксируйте решение в `DECISIONS.md`.
4. Когда ограничение из §4 исправлено, удалите это ограничение и отметьте
   исправление в `DECISIONS.md`; не оставляйте устаревший текст о «известном
   ограничении».
