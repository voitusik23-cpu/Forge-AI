# Forge AI — Stage 1.5 External Architecture Benchmark

**Status:** CONCEPTUAL ANALYSIS — proposed statuses are not approved architectural decisions.

**Scope:** Publicly documented product patterns reviewed on 2026-10-03. This document compares patterns, not private implementation details. It does not authorize implementation or resolve owner decisions.

## Executive summary

Forge already has a useful orchestration foundation: provider-neutral task execution, deterministic classification and routing, explicit provider override, cost-tier metadata, finite provider fallback, multi-agent primary/review execution with one controlled revision, and a deterministic project planner. It does not yet implement the broader product lifecycle, durable runs, tools, sandboxes, project memory, or a control-center UI. The target Technical Specification already describes Discovery, project context, readiness, approvals, provenance, Project Memory and Forge Knowledge as future concepts.

The benchmark points to one structural priority: make the future run durable, inspectable, permission-scoped and resumable before expanding autonomous execution. A durable run should preserve state and artifact references, record actions and decisions without storing chain-of-thought, stop at authorization boundaries, and resume from a verified checkpoint. Agents, skills, tools and providers should remain replaceable capabilities governed by a Forge-owned policy layer.

Nothing in this document approves an implementation, storage technology, UI, approval policy, or autonomy level. The classifications below are recommendations for owner review.

## External findings

| System and documented pattern | Problem and conceptual mechanism | Relevance and Forge gap | Recommendation / layer |
| --- | --- | --- | --- |
| **OpenAI Codex / Agents:** separate orchestration harness from sandbox compute; the harness owns routing, handoffs, approvals, tracing, recovery and run state, while a sandbox owns files and commands. Sandboxes support snapshots and resumable work. | Prevent model-directed execution from owning trusted authentication and recovery controls; preserve a stateful workspace across work and review. | Strong fit for Forge’s future run model. Current provider/runtime execution has no durable project workspace or recovery contract. | **PROPOSED:** adopt the separation as a target boundary. Core run control owns authorization and provenance; workspace execution is an isolated capability/runtime. Do not mandate a vendor sandbox. [OpenAI Agents SDK sandbox guide](https://developers.openai.com/api/docs/guides/agents/sandboxes) |
| **GitHub Copilot agents:** custom agent profiles define purpose, prompt, tools and MCP servers; built-in specialist agents include explore, research, code review and security review. Session status and review/iteration are visible. | Reuse role-specific instructions and access; delegate focused work without flooding the parent context; keep a reviewable branch/PR handoff. | Forge has primary/reviewer roles, but no general agent contract, reusable role catalogue, run UI, or structured handoff. | **PROPOSED:** define role contracts and least-privilege tools in Core; expose specialist roles as capabilities. Keep branch/PR integration external. [Custom agents](https://docs.github.com/en/copilot/concepts/agents/copilot-cli/about-custom-agents), [agent session workflow](https://docs.github.com/en/copilot/how-tos/copilot-on-github/use-copilot-agents) |
| **Cursor:** project rules can be scoped and loaded by relevance; background agents run asynchronously in isolated environments, show status, accept follow-ups and allow user takeover; generated memories require approval before saving. | Reuse local rules without always loading everything; let long work continue while keeping it reviewable; avoid silently treating inferred memory as approved fact. | Forge has AGENTS/spec documents and a context-economy rule. Durable work, selective skill loading and governed project memory are future. | **PROPOSED:** load only applicable rules/skills; show pause/takeover; make candidate memory reviewable. These belong in context, runtime and project-memory layers. [Rules](https://docs.cursor.com/context/rules-for-ai), [background agents](https://docs.cursor.com/background-agent), [memories](https://docs.cursor.com/en/context/memories) |
| **Claude Code:** separates persistent instructions, scoped rules, skills, hooks and subagents, with different load timing and authority. MCP is an integration protocol. | Give recurring knowledge a predictable place and let agents specialize without conflating instructions, executable hooks and delegation. | Forge has repository-level agent rules and conceptual specialist agents, but no Skill/Hook contract or authority model. | **PROPOSED:** distinguish instruction, skill package, agent role, hook, tool and connector. Gate executable hooks as code with explicit permissions and review. [Claude Code customization overview](https://claude.com/blog/steering-claude-code-skills-hooks-rules-subagents-and-more), [MCP overview](https://docs.anthropic.com/en/docs/agents-and-tools/mcp) |
| **OpenHands:** SDK documents pause/resume, persistence, persistent memory, security/action confirmation, skills, plugins and event-driven agent workflows. | Operate a composable agent runtime over multiple sessions while making actions, state and security controls inspectable. | Forge’s revision loop is bounded but not restart-durable; no persisted run/event model or tool security analyzer exists. | **PROPOSED:** study lifecycle/event and confirmation patterns, not framework coupling. Put run state in Core and action-specific enforcement in runtime/tool boundaries. [OpenHands SDK documentation index](https://github.com/OpenHands/docs/blob/main/llms.txt) |
| **SWE-agent:** a configurable Agent-Computer Interface (ACI) shapes what commands and observations the agent receives; configuration controls tools, prompts, demonstrations and I/O. | Raw shell interfaces are hard for models to use reliably; a task-oriented action/observation surface can improve outcomes. | Forge has no software workspace tool layer. | **PROPOSED:** give tools typed contracts and bounded, task-relevant observations; evaluate ACI choices against golden tasks. Runtime/tool capability, not provider code. [Configuration](https://github.com/SWE-agent/SWE-agent/blob/main/docs/config/config.md), [ACI paper](https://arxiv.org/abs/2405.15793) |
| **LangGraph:** checkpointers persist state at workflow steps; thread identity supports resume, human-in-the-loop, fault tolerance and history; stores are separately used for cross-thread memory. | Resume a graph after interruption without treating durable project memory and in-flight execution state as the same thing. | Forge has neither durable checkpointing nor separate implemented Project Memory. | **PROPOSED:** separate run checkpoint state from project memory/knowledge. Persist explicit versions and side-effect status; do not adopt LangGraph as a mandatory dependency. [Persistence](https://langchain-ai.github.io/langgraphjs/how-tos/persistence-postgres/) |
| **Model Context Protocol (MCP):** standardizes tools, resources and prompts. Its security model assigns trust decisions and access control to client/server operators; model-selected tools can be invoked in sequences. | Interoperate with external tools and context sources without vendor-specific connectors everywhere. | Forge has provider adapters but no connector/permission layer. MCP does not supply Forge’s project lifecycle, authorization policy or provenance. | **OPTIONAL integration:** put MCP behind Forge’s own connector/tool contracts and permission checks. Treat each server as a trusted dependency, review its scope, and do not pass credentials through blindly. [MCP overview](https://modelcontextprotocol.io/specification/draft/server/index), [MCP security model](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/SECURITY.md) |

### Cross-system patterns worth retaining

1. **Control plane versus execution plane:** trusted run state, authorization, secrets and audit stay outside model-directed command execution.
2. **Capability-scoped roles:** a specialist role is a purpose plus contract and permitted capability set, not necessarily a permanent model.
3. **Durable run identity:** pause/resume and background work require a stable run/thread ID, versioned checkpoint, and explicit wait reason.
4. **Visible review and takeover:** users need to inspect a concrete diff/result and intervene without losing run state.
5. **Selective context:** scoped rules, on-demand skills, retrieval and summaries reduce irrelevant prompt material; provenance must survive compaction.
6. **Protocol interoperability with local governance:** MCP can transport tool/context interactions, while Forge remains responsible for consent, policy, state, and audit.
7. **Tool interface quality matters:** constrained commands and typed observations can be safer and more reliable than unrestricted shell access.

## Already covered in Forge

- Provider independence and model/provider replacement; deterministic routing by category and cost tier with explicit provider override and finite fallback.
- A primary agent, independent reviewer and one bounded revision cycle; failures do not imply approval.
- A deterministic Planner with four plan templates and explicit task dependencies.
- Product-level safety, secret handling, human authority, provenance and a target project lifecycle are present in documentation.
- Technical Specification sections 21–23 describe discovery/evidence, project context and impact, readiness/approval distinctions, artifact/process/derived-assessment boundaries, and the proposed project lifecycle.
- The Vision describes current versus future functionality and controlled change.

These are documented foundations; they do not mean durable execution, memory, tooling, UI, or production safeguards are implemented.

## Missing or under-specified

- Durable run state, stable run identity, checkpoint format, resumability semantics, and idempotent handling of uncertain side effects.
- Append-only action/event records with redaction, retention, access control and artifact provenance; user-facing trace views.
- Tool/connector registry, role contracts, capability manifests and per-operation permission enforcement.
- Isolated execution/workspaces, network policy, credential injection and cleanup boundaries.
- Untrusted-input handling for prompt injection and tool/connector content.
- Dry-run previews, risk/reversibility classification, recovery evidence and explicit external-action authorization.
- Project-specific verification profiles and a Forge Evaluation Suite with golden scenarios.
- Context retrieval/compaction and structured handoff preserving decisions, open questions, evidence references and artifact versions.
- Operational control-center UX for pending approvals, activity, cost/time, evidence, artifacts and takeover.
- Retention/deletion rules for project memory, run logs, checkpoints and generated artifacts.

## Proposed additions to Forge documentation

**PROPOSED:** add these concepts to the target Technical Specification when Stage 1.5 is accepted by the owner. Keep current implementation claims in `docs/ARCHITECTURE.md`; only schedule accepted work in `docs/ROADMAP.md`; record decisions in `docs/DECISIONS.md` only after they are made.

1. A **Run and Checkpoint** model, distinct from Project Memory, with stable identity, lifecycle state, versioned checkpoint, waiting reason, pending approval, and resumable workspace/artifact references.
2. An **Event / Trace** contract for observable actions and outcomes; explicitly exclude hidden chain-of-thought. Define secret redaction, retention, provenance and access rules before storage design.
3. **Agent, Skill, Tool and Connector contracts** with inputs/outputs, phase applicability, evidence, permissions, failure behavior, version, tests and owner.
4. A **permission and execution boundary** separating trusted orchestration, isolated project workspace, test/build execution and production; least privilege by role, environment and operation.
5. **Untrusted content and connector security**: external content is evidence/data, not higher-authority instructions; validate tool inputs/outputs; review and revoke connector trust.
6. **Impact preview and recovery** for consequential changes: affected artifacts/systems, permission, side effect, risk, reversibility, backup/checkpoint, verification and recovery path.
7. **Project-specific verification and Forge Evals**, beginning with a small golden scenario set; use explicit acceptance evidence, not agent self-report or a single quality score.
8. **Progressive-disclosure control center** with run/activity, decisions, approvals, artifacts/diffs, evidence, plan, verification and a clear user takeover/pause/resume path.

## Benchmark matrix

`Forge Today` describes current documented/implemented scope, not a future promise. All statuses are proposals for owner review.

| Capability / Pattern | Forge Today | External Pattern | Gap | Proposed Status |
| --- | --- | --- | --- | --- |
| Discovery | Target concept in Technical Specification; no dedicated runtime | Scoped context collection, approvals and resumable sessions | No implemented Discovery workflow | **MUST HAVE** for full product; stage and scope TBD |
| Planning | Deterministic template planner with dependencies | Agent systems expose inspectable plans and iterative execution | No editable, execution-aware plan/queue | **SHOULD HAVE** |
| Subagents | Primary + reviewer; bounded revision | Role-specific subagents and delegation | No general role contracts/handoff | **SHOULD HAVE** |
| Skills | Not implemented; repo documentation is static context | On-demand, scoped reusable instructions/assets | No package, applicability, version or evaluation contract | **SHOULD HAVE** |
| Tools | Provider-backed model calls; no general project tools | Typed or task-oriented action interfaces | No governed tool registry | **MUST HAVE** before project execution actions |
| MCP | Not implemented | Standard tools/resources/prompts interoperability | No trust/consent boundary or connector governance | **OPTIONAL** integration |
| Permissions | Human authority documented; no operation-level permission engine | Agent/tool-scoped capability grants | No enforcement by role/project/environment/action | **MUST HAVE** before autonomous side effects |
| Sandbox | Not implemented | Isolated workspace/runtime with scoped network and credentials | No execution isolation | **MUST HAVE** before agent command execution on user projects |
| Durable execution | No pause/resume after process restart | Persistent run/thread state | Missing stable run identity and recovery protocol | **MUST HAVE** for long-running workflows |
| Checkpoints | No durable execution checkpoints | Workflow-state snapshots at meaningful boundaries | Missing checkpoints and stale-state validation | **MUST HAVE** for durable execution |
| Memory | Future Project Memory and separate Forge Knowledge | Thread state versus longer-term store; approved memory examples | No retention, review or access lifecycle | **SHOULD HAVE**; policy is **DECISION REQUIRED** |
| Provenance | Target docs discuss evidence, versions and provenance | Artifact/run/action traceability | No implemented source/version links across runs | **MUST HAVE** for trustworthy project outputs |
| Event log | Not implemented | Ordered activity/event records | No append-only action record or retention policy | **MUST HAVE** for auditable autonomous work |
| Observability | Smoke outputs; no run telemetry UI | Per-run agent/tool/test/time/cost traces | No structured user-facing trace | **SHOULD HAVE** |
| Background agents | Not implemented | Async work, status/follow-up/takeover | No background scheduler or durable run manager | **OPTIONAL** product mode after durable core |
| Human approval | Explicit provider choice; target approval scopes documented | Pause at review/approval, iterate after feedback | No general approval inbox or binding approval receipts | **MUST HAVE** for consequential actions |
| Decision queue | Owner decisions listed in docs only | Queued, prioritized decisions | No inbox, urgency or defer workflow | **SHOULD HAVE** |
| Dry run | Not implemented | Preview changes and side effects | No preview/effect manifest | **MUST HAVE** for high-impact operations |
| Rollback | Bounded revision loop is content correction, not operation rollback | Snapshots, recovery plans, version control | No state-change recovery contract | **MUST HAVE** where action claims reversibility; otherwise disclose limits |
| Testing | Unit tests and live smoke commands | Evidence-based project-specific checks | No generated-project verification profile/acceptance mapping | **MUST HAVE** per project class |
| Evals | No Forge-level eval suite documented as implemented | Golden tasks/regression evaluation | No quality baseline for reasoning workflows | **SHOULD HAVE** before major routing/workflow autonomy growth |
| Security review | Safety principles; provider error handling | Dedicated security agents/gates/scans | No project-risk-driven security gate | **MUST HAVE** for security-sensitive/high-risk project classes |
| Cost controls | Cost tiers, estimated provider usage; no budget enforcement | Budgets, usage attribution and rate limits | No end-to-end run budget gate | **SHOULD HAVE**; policy **DECISION REQUIRED** |
| Model routing | Deterministic categories, capabilities, cost tiers, fallback | Provider/model selection also considers privacy, quality, latency, limits | Some dimensions are not modeled | **SHOULD HAVE**; avoid premature scoring |
| Project templates | Planner templates for four project categories | Reference projects and templates as starting points | No versioned reference architecture/evaluation corpus | **OPTIONAL**; never convert template assumptions into requirements |
| Plugins/extensions | Not implemented | Packages can combine agents, tools, skills and connectors | No trust/version/permission lifecycle | **FUTURE** |
| Visual project map | Target idea only | Navigable graph of project objects | No UI or graph model | **OPTIONAL** UX capability |
| Project health | Readiness concept in target specification | Explainable dimensions and blockers | No health assessment or freshness checks | **SHOULD HAVE**; never collapse into one opaque score |

## MUST / SHOULD / OPTIONAL / FUTURE / REJECT

These are benchmark recommendations, not owner-approved priorities.

### MUST HAVE — before Forge is structurally complete as a project-execution system

- Durable run state and meaningful checkpoints with safe resume, including uncertain side-effect handling.
- Explicit, enforceable permission boundaries and isolated execution before agents can act on user projects.
- Artifact/event provenance and an inspectable record of actions, approvals, failures and verification; no chain-of-thought logging.
- Approval bound to the exact action, scope, project-context version and permitted side effects.
- Dry-run/impact preview and truthful recovery/reversibility claims for consequential operations.
- Project-specific verification and security gates where risk requires them; completion based on evidence.
- Untrusted input handling that prevents external content from acquiring instruction authority.

### SHOULD HAVE

- Clear contracts and structured handoffs for specialist agents, skills and tools.
- Context retrieval, compaction and checkpoint summaries that preserve decisions, unknowns and source links.
- User-facing run observability, Decision Inbox, budget controls, explainable project health and Forge Evals/golden scenarios.
- Reviewed Project Memory and Forge Knowledge lifecycles with provenance and stale-content handling.
- A Ready-to-Run handoff package with setup, configuration references, tests, limitations and verification evidence.

### OPTIONAL

- MCP and other connector protocols; remote/background execution; visual project map; project templates; scheduling and external event triggers; parallel execution where dependencies are explicit.
- Use only where the project class and user value justify added operational/security burden.

### FUTURE

- Extension marketplace, broad plugin ecosystem, Forge self-improvement loop, adaptive cost/quality optimization, and general autonomous work over multiple days. These depend on durable state, evals, security controls and owner policy.

### REJECT / NOT APPLICABLE as defaults

- One global project-health score that conceals blockers or evidence.
- Loading every conversation, repository file, skill and knowledge item into every model call.
- Majority vote as the default resolver for agent disagreement.
- Unrestricted shell/network/credential access as the default tool grant.
- Treating templates or prior project stacks as mandatory architecture.
- Automatic self-modification of Forge from a single project outcome.
- Feature-count maximization or cloning a competitor’s proprietary UI/implementation.

## UI — future Forge Control Center

**PROPOSED** information architecture with progressive disclosure:

- **Project dashboard:** goal, lifecycle stage, readiness blockers, health dimensions, latest activity.
- **Conversation / Discovery:** owner intent, questions, answers, evidence references, unresolved conflicts.
- **Requirements and architecture:** versioned artifacts, decisions, alternatives, dependencies, change impact.
- **Decision Inbox and approvals:** why it matters, urgency, evidence, options, consequences, exact approval scope/version, approve/reject/defer.
- **Plan:** phases, dependency graph, task status, assigned role/provider and blockers.
- **Run / Activity:** durable run state, agents, tool calls, duration/cost, retries, failures, approvals, checkpoint, pause/resume/cancel/takeover.
- **Evidence and memory:** origin, confidence/quality, applicable scope, provenance, retention and supersession.
- **Artifacts:** diffs, generated files, builds, screenshots, tests and handoff package.
- **Security / permissions:** workspace/environment boundary, enabled connectors, secret references (never raw values), network/tool grants and pending risk gates.

Default view should answer “what is happening, what needs me, and what will change?”; technical traces and detailed evidence should be available on demand.

## Security

Required target boundaries before autonomous execution:

1. Treat websites, repositories, issues, email, documents, API responses, generated text and tool metadata as untrusted data unless independently authorized as Forge policy.
2. Keep orchestration/authentication and audit controls outside isolated command execution where practical; use least-privilege project workspaces, network rules and scoped credentials.
3. Enforce permissions in the runtime/tool boundary, not only in prompts. Bind grants to role, project, environment, operation, scope and expiry/revocation.
4. Secrets are references injected by trusted runtime only for authorized operations; never put raw secrets in context, project memory, event payloads or artifacts.
5. Review connectors/MCP servers as software dependencies. Show their tools and requested access; validate arguments/results and require consent before connecting or performing sensitive operations.
6. Before risky actions, show affected files/systems, external effects, risk, cost estimate if available, reversibility, recovery path and exact approval scope.
7. Keep an auditable event record with redaction and retention rules; do not store hidden reasoning. Support incident review and credential revocation.

Security depth must follow project risk. Whether every generated project requires an independent security approval is **DECISION REQUIRED**.

## Agent system

Define separate concepts rather than one all-purpose “agent” record:

- **Role/Agent contract:** purpose, phase, allowed input/output, evidence bar, prohibited actions, tools, model constraints, approval requirement, failure/handoff behavior, owner/version/evals.
- **Skill:** selectively loaded instructions, references, scripts/assets, prerequisites, applicability, permissions, version and evaluation. Knowledge records general patterns; skills package reusable expertise/workflow.
- **Tool:** typed action or read interface with input validation, output limits, idempotency/retry semantics, permission declaration, audit event and error contract.
- **Connector:** authenticated boundary to an external service, with trust owner, scopes, data handling, revocation and version/update process. MCP may implement transport/discovery, not Forge authorization.
- **Runtime:** run identity, checkpoint/recovery, context assembly/compaction, cancellation, concurrency, sandbox binding, approval waits, event capture and artifact staging.
- **Handoff:** concise goal, accepted decisions, evidence/artifact references, unresolved questions, next action/permissions and expected output; receiving agent fetches only relevant source artifacts.

Parallelize only independent tasks with explicit dependency and shared-artifact coordination. For disagreement, compare evidence/source authority, scope and risk; escalate material conflicts rather than taking a vote. Retries must be bounded and account for uncertain external side effects.

## Lifecycle

Stage 1.4’s proposed lifecycle remains coherent. Add the following operational cycle after an approved plan and around every consequential action:

```text
Plan / approved scope
  -> create durable Run + checkpoint
  -> assemble minimal context + applicable skills
  -> verify role/tool permissions and sandbox
  -> preview consequential changes -> approval if required
  -> execute one bounded step -> append action/result event
  -> verify artifact/acceptance evidence -> checkpoint
  -> pause / wait / resume / recover / hand off as needed
  -> Ready-to-Run gate -> owner handoff / authorized release
  -> operation feedback -> candidate lesson review
```

Run state is not Project Memory. Event history is not chain-of-thought. An approval is not portable to a materially changed scope or context version. A completed agent response is not a verification result. Release/deployment remains an explicitly authorized action.

No change to formal Specification-versus-Architecture precedence, approval policy, or lifecycle ownership is implied.

## DECISION REQUIRED

Owner input is required before converting proposals into permanent architecture or roadmap commitments:

1. Is durable execution/checkpointing a prerequisite before any project-file or external-service actions, or only before background/multi-session work?
2. Which run artifacts/events should persist, for how long, and who can inspect/export/delete them?
3. What default autonomy and approval policy applies by risk and environment? Which actions always require approval?
4. Should the first sandbox be local, remote, or an interchangeable interface with one initial backend? What network access is allowed by default?
5. Which tool types are in initial scope (filesystem, terminal, browser, Git, GitHub, database, deployment)?
6. Should the Control Center begin as desktop, web, or a CLI/API plus a later UI? Which surfaces are required for MVP?
7. What are the retention, privacy, user correction and deletion policies for Project Memory and Forge Knowledge? Who can promote lessons?
8. Which project classes require an independent security review or user acceptance gate?
9. What resource budgets must be enforced (cost, time, tokens, concurrency, API limits), and who sets them?
10. What owner-provided facts and access boundaries apply to Axis, KonnexMastery, FlashCast, Mastery Creator and KeyCore-Hub? No integration assumptions are made here.

## Conflicts

- **Current code versus target vision:** provider orchestration, review/revision and template planning exist; durable runs, Discovery, project memory, tools, sandboxes and UI do not. Keep status labels explicit.
- **Planner versus execution lifecycle:** Planner v0.1 creates static plans and does not execute or update task statuses. Do not describe tasks as tracked progress until implemented.
- **Revision loop versus rollback:** the one-cycle content revision is not rollback of filesystem, database or external side effects.
- **Provider fallback versus task retry:** provider fallback is bounded routing after provider failure; it does not make arbitrary tool actions idempotent or safe to repeat.
- **MCP versus Forge governance:** MCP interoperability does not provide Forge’s approval, provenance, sandbox or project lifecycle guarantees.
- **Event trace versus internal reasoning:** record observable inputs/actions/results as appropriate; do not expose or persist hidden chain-of-thought.
- **Owner decisions remain open:** Stage 1.4’s unresolved lifecycle/approval decisions remain unresolved; Stage 1.5 does not silently settle them.

No direct contradiction requires changing current implementation documentation. The main documentation risk is presenting target patterns as shipped features.

## Documentation impact

| Document | Stage 1.5 impact |
| --- | --- |
| `docs/STAGE_1_5_EXTERNAL_BENCHMARK.md` | This conceptual benchmark and proposal register. |
| `docs/TECHNICAL_SPECIFICATION.md` | **Recommended after owner review:** add approved target contracts for durable runs, events, tools/skills, security boundaries, evaluation and UI. Do not copy this whole benchmark into the specification. |
| `docs/FORGE_VISION.md` | No immediate change. Its direction already covers controlled, reviewable multi-agent work; revisit if owner changes product priorities. |
| `docs/ARCHITECTURE.md` | No change: this is current implementation documentation. Update only as features are implemented. |
| `docs/ROADMAP.md` | No change before owner prioritization. Turn accepted proposals into staged roadmap items only after scope/order decisions. |
| `docs/DECISIONS.md` | No change: proposals are not decisions. Record only owner-approved architectural choices and rationale. |

## Next stage

**Recommended next conceptual stage: Stage 1.6 — Durable Run and Permission Boundary Decision Record.** First resolve only the owner questions needed to define the minimum run lifecycle, persisted state, approval binding, action/tool scope, sandbox boundary and event-retention principles. Then update the Technical Specification with accepted contracts and update the Roadmap only for agreed priorities. Do not select databases, cloud vendors or UI framework until requirements justify them.

## Research sources and limits

Sources were public product/developer documentation or a peer-reviewed benchmark paper, checked 2026-10-03. Product details can change; this is a pattern comparison, not a feature or security guarantee. Some systems document behavior for only one product surface or plan tier. The benchmark does not evaluate private internals or claim that Forge should reproduce any vendor feature.

- OpenAI, [Sandbox Agents](https://developers.openai.com/api/docs/guides/agents/sandboxes).
- GitHub, [About custom agents](https://docs.github.com/en/copilot/concepts/agents/copilot-cli/about-custom-agents), [Use Copilot agents](https://docs.github.com/en/copilot/how-tos/copilot-on-github/use-copilot-agents), [Hooks reference](https://docs.github.com/en/copilot/reference/hooks-reference).
- Cursor, [Rules](https://docs.cursor.com/context/rules-for-ai), [Background Agents](https://docs.cursor.com/background-agent), [Memories](https://docs.cursor.com/en/context/memories).
- Anthropic, [Steering Claude Code](https://claude.com/blog/steering-claude-code-skills-hooks-rules-subagents-and-more), [MCP](https://docs.anthropic.com/en/docs/agents-and-tools/mcp).
- OpenHands, [SDK documentation index](https://github.com/OpenHands/docs/blob/main/llms.txt) and linked guides for persistence, pause/resume, skills, secrets and security.
- SWE-agent, [Configuration](https://github.com/SWE-agent/SWE-agent/blob/main/docs/config/config.md), [Agent-Computer Interface paper](https://arxiv.org/abs/2405.15793).
- LangGraph, [Persistence](https://langchain-ai.github.io/langgraphjs/how-tos/persistence-postgres/).
- Model Context Protocol, [server primitives](https://modelcontextprotocol.io/specification/draft/server/index), [security model](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/SECURITY.md).

---

# Forge AI — Stage 1.5: внешний архитектурный benchmark

**Статус:** КОНЦЕПТУАЛЬНЫЙ АНАЛИЗ — предложенные статусы не являются утверждёнными архитектурными решениями.

**Объём:** Публично описанные продуктовые подходы, проверенные 2026-10-03. Сравниваются подходы, а не закрытые детали реализации. Документ не разрешает реализацию и не принимает решения за владельца.

## Краткий вывод

У Forge уже есть полезная основа оркестрации: provider-neutral исполнение задач, детерминированная классификация и маршрутизация, явный выбор провайдера, метаданные уровней стоимости, конечный fallback, выполнение primary/reviewer с одной контролируемой revision и детерминированный планировщик проекта. Более широкий жизненный цикл продукта, durable runs, инструменты, sandbox, Project Memory и UI центра управления пока не реализованы. Целевая Technical Specification уже описывает Discovery, project context, readiness, approvals, provenance, Project Memory и Forge Knowledge как будущие концепции.

Главный структурный приоритет по результатам benchmark — сделать будущие запуски долговечными, проверяемыми, ограниченными разрешениями и возобновляемыми до расширения автономного выполнения. Durable run должен сохранять состояние и ссылки на артефакты, записывать действия и решения без chain-of-thought, останавливаться на границах авторизации и продолжаться с проверенной контрольной точки. Agents, skills, tools и providers должны оставаться заменяемыми capabilities под управлением политики Forge.

Этот документ не утверждает реализацию, технологию хранения, UI, политику approvals или уровень автономии. Приведённые ниже классификации являются рекомендациями для рассмотрения владельцем.

## Внешние выводы

| Система и описанный подход | Решаемая проблема и идея | Актуальность и пробел Forge | Рекомендация / слой |
| --- | --- | --- | --- |
| **OpenAI Codex / Agents:** разделение orchestration harness и sandbox compute; harness отвечает за routing, handoffs, approvals, tracing, recovery и состояние run, а sandbox — за файлы и команды. Поддерживаются snapshots и возобновляемая работа. | Не позволять model-directed выполнению владеть доверенными контролями аутентификации и восстановления; сохранять рабочее пространство на время выполнения и проверки. | Хорошо подходит будущей модели запусков Forge. В текущем runtime нет контракта для долговечного workspace или восстановления. | **ПРЕДЛОЖЕНО:** взять разделение за целевую границу. Core управляет разрешениями и provenance; исполнение в workspace — изолированная capability/runtime. Не привязываться к sandbox конкретного поставщика. [Руководство OpenAI по sandbox](https://developers.openai.com/api/docs/guides/agents/sandboxes) |
| **GitHub Copilot agents:** профили custom agents задают цель, prompt, tools и MCP servers; встроенные специалисты включают explore, research, code review и security review. Видны статусы сессий и цикл проверки/правок. | Повторно использовать инструкции ролей и доступы; делегировать ограниченную работу без переполнения контекста родительского агента; передавать результат на проверку через branch/PR. | У Forge есть роли primary/reviewer, но нет общего контракта агента, каталога ролей, UI запусков или структурированной передачи результата. | **ПРЕДЛОЖЕНО:** определить контракты ролей и least-privilege tools в Core; роли специалистов оформлять как capabilities. Интеграцию branch/PR оставить внешнему слою. [Custom agents](https://docs.github.com/en/copilot/concepts/agents/copilot-cli/about-custom-agents), [workflow агентов](https://docs.github.com/en/copilot/how-tos/copilot-on-github/use-copilot-agents) |
| **Cursor:** project rules можно ограничивать областью и загружать по релевантности; background agents работают асинхронно в изолированной среде, показывают статус, принимают уточнения и позволяют пользователю перехватить управление; для автоматической памяти запрашивается approval. | Повторно использовать правила, не загружая всё постоянно; продолжать долгую работу с возможностью проверки; не считать автоматически выведенную память утверждённым фактом. | У Forge есть AGENTS/spec документы и принцип экономии контекста. Durable work, выборочная загрузка skills и управляемая Project Memory — будущее. | **ПРЕДЛОЖЕНО:** загружать только применимые правила/skills; поддержать pause/takeover; сделать memory-кандидаты проверяемыми. Это относится к слоям context, runtime и project memory. [Rules](https://docs.cursor.com/context/rules-for-ai), [background agents](https://docs.cursor.com/background-agent), [memories](https://docs.cursor.com/en/context/memories) |
| **Claude Code:** разделяет постоянные инструкции, scoped rules, skills, hooks и subagents с разным временем загрузки и уровнем полномочий. MCP — протокол интеграции. | Размещать повторяемые знания предсказуемо и разделять инструкции, исполняемые hooks и делегирование. | У Forge есть правила репозитория и концептуальные роли специалистов, но нет контракта Skill/Hook или модели полномочий. | **ПРЕДЛОЖЕНО:** различать instruction, skill package, agent role, hook, tool и connector. Исполняемые hooks должны быть кодом с явными permissions и проверкой. [Обзор настройки Claude Code](https://claude.com/blog/steering-claude-code-skills-hooks-rules-subagents-and-more), [MCP](https://docs.anthropic.com/en/docs/agents-and-tools/mcp) |
| **OpenHands:** SDK описывает pause/resume, persistence, persistent memory, security/action confirmation, skills, plugins и событийные процессы агента. | Работать через несколько сессий в компонуемой среде агента, делая действия, состояние и безопасность проверяемыми. | Revision loop Forge ограничен, но не переживает перезапуск процесса; постоянной модели run/event и анализатора безопасности инструментов нет. | **ПРЕДЛОЖЕНО:** изучить шаблоны lifecycle/event, не связывая архитектуру с фреймворком. Run state относится к Core, защита действий — к границам runtime/tool. [Индекс документации OpenHands SDK](https://github.com/OpenHands/docs/blob/main/llms.txt) |
| **SWE-agent:** настраиваемый Agent-Computer Interface (ACI) определяет доступные команды и наблюдения агента; конфигурация управляет tools, prompts, demonstrations и I/O. | Сырые shell-интерфейсы неудобны моделям; целевая поверхность действий и наблюдений может повысить надёжность результатов. | У Forge нет инструментария для рабочего пространства проекта. | **ПРЕДЛОЖЕНО:** предоставить tools с типизированными контрактами и ограниченными, релевантными задаче наблюдениями; проверять ACI на golden tasks. Слой runtime/tool, не provider. [Конфигурация](https://github.com/SWE-agent/SWE-agent/blob/main/docs/config/config.md), [статья об ACI](https://arxiv.org/abs/2405.15793) |
| **LangGraph:** checkpointers сохраняют состояние на шагах workflow; идентификатор thread позволяет возобновлять работу, организовать human-in-the-loop, fault tolerance и историю; stores отдельно применяются для межсессионной памяти. | Возобновлять граф после перерыва и не смешивать долговременную память проекта с состоянием незавершённого выполнения. | У Forge нет durable checkpointing и реализованной отдельно от него Project Memory. | **ПРЕДЛОЖЕНО:** разделить checkpoint run и Project Memory/Knowledge. Сохранять явные версии и статус внешнего эффекта; не вводить LangGraph как обязательную зависимость. [Persistence](https://langchain-ai.github.io/langgraphjs/how-tos/persistence-postgres/) |
| **Model Context Protocol (MCP):** стандартизует tools, resources и prompts. Его модель безопасности возлагает решение о доверии и контроле доступа на клиентов/операторов; модель может выбирать несколько инструментов подряд. | Интегрировать внешние инструменты и источники контекста без множества vendor-specific connectors. | У Forge есть provider adapters, но нет слоя connectors/permissions. MCP сам по себе не предоставляет lifecycle Forge, политику авторизации и provenance. | **ОПЦИОНАЛЬНАЯ интеграция:** использовать MCP под собственными контрактами tools/connectors и проверками permission в Forge. Считать каждый server доверенной зависимостью, проверять scope и не передавать credentials вслепую. [Обзор MCP](https://modelcontextprotocol.io/specification/draft/server/index), [модель безопасности MCP](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/SECURITY.md) |

### Подходы, которые полезно сохранить

1. **Control plane и execution plane:** доверенное состояние run, авторизация, секреты и аудит остаются вне model-directed исполнения команд.
2. **Роли как capabilities:** специализация — это цель, контракт и разрешённый набор возможностей, а не обязательно постоянно работающая модель.
3. **Идентичность долговечного run:** pause/resume и background work требуют стабильного run/thread ID, versioned checkpoint и явной причины ожидания.
4. **Видимая проверка и takeover:** пользователь должен иметь возможность посмотреть конкретный diff/результат и вмешаться, не теряя состояние запуска.
5. **Выборочный context:** scoped rules, skills по запросу, retrieval и summaries сокращают нерелевантный контекст; provenance должна сохраняться после compaction.
6. **Интероперабельность при локальном governance:** MCP может передавать tool/context interactions, но consent, policy, state и audit остаются ответственностью Forge.
7. **Качество tool-интерфейса важно:** ограниченные команды и типизированные observations могут быть безопаснее и надёжнее неограниченного shell.

## Уже покрыто в Forge

- Независимость от провайдера и заменяемость модели; детерминированный routing по категориям и cost tier, явный override и конечный fallback.
- Primary agent, независимый reviewer и одна ограниченная revision; сбой не означает approval.
- Детерминированный Planner с четырьмя шаблонами плана и явными зависимостями задач.
- На уровне продукта задокументированы безопасность, полномочия пользователя, provenance и целевой lifecycle проекта.
- Разделы 21–23 Technical Specification описывают discovery/evidence, project context и impact, различия readiness/approval, границы artifact/process/derived assessment и предложенный lifecycle проекта.
- Vision отделяет текущую функциональность от будущей и поддерживает контролируемые изменения.

Это задокументированные основы, а не утверждение, что durable execution, memory, tools, UI или production safeguards уже реализованы.

## Отсутствует или описано недостаточно

- Durable run state, стабильная идентичность run, формат checkpoint, правила восстановления и идемпотентная обработка неопределённых внешних эффектов.
- Append-only записи действий и событий с redaction, retention, контролем доступа и provenance; пользовательские представления trace.
- Реестр tools/connectors, контракты ролей, capability manifests и enforcement permissions для каждой операции.
- Изолированное исполнение/workspaces, network policy, передача credentials и границы очистки.
- Обработка prompt injection и контента инструментов как недоверенных данных.
- Dry-run previews, оценка риска/обратимости, recovery evidence и явная авторизация внешних действий.
- Профили проверки по типам проектов и Forge Evaluation Suite с golden scenarios.
- Retrieval/compaction контекста и структурированные handoffs, сохраняющие решения, открытые вопросы, ссылки на evidence и версии артефактов.
- UX центра управления для approvals, активности, стоимости/времени, evidence, artifacts и takeover.
- Правила хранения/удаления Project Memory, run logs, checkpoints и generated artifacts.

## Предлагаемые дополнения к документации Forge

**ПРЕДЛОЖЕНО:** после рассмотрения владельцем добавить эти концепции в целевую Technical Specification Stage 1.5. Сведения о реализованном состоянии оставить в `docs/ARCHITECTURE.md`; принимать в `docs/ROADMAP.md` только одобренные этапы; записывать в `docs/DECISIONS.md` лишь уже принятые решения.

1. Модель **Run and Checkpoint**, отдельная от Project Memory: стабильный ID, lifecycle state, versioned checkpoint, причина ожидания, pending approval и ссылки на возобновляемый workspace/artifacts.
2. Контракт **Event / Trace** для наблюдаемых действий и результатов; явно исключить hidden chain-of-thought. До выбора хранилища определить redaction, retention, provenance и доступ.
3. Контракты **Agent, Skill, Tool и Connector** с input/output, применимостью к фазам, evidence, permissions, поведением при сбое, версией, тестами и владельцем.
4. **Граница permissions и исполнения**, разделяющая доверенную оркестрацию, изолированный workspace проекта, запуск тестов/сборки и production; least privilege по роли, среде и операции.
5. **Безопасность недоверенного контента и connectors:** внешний контент — evidence/data, а не инструкции более высокого приоритета; проверять tool inputs/outputs; пересматривать и отзывать доверие connectors.
6. **Impact preview и recovery** для существенных изменений: затронутые артефакты/системы, permission, side effect, риск, обратимость, backup/checkpoint, verification и recovery path.
7. **Проверки по классу проекта и Forge Evals**, начиная с небольшого набора golden scenarios; завершение подтверждается acceptance evidence, а не самоотчётом агента или единой оценкой качества.
8. **Control center с постепенным раскрытием деталей:** run/activity, decisions, approvals, artifacts/diffs, evidence, plan, verification и ясный путь takeover/pause/resume.

## Матрица benchmark

`Forge сегодня` описывает текущие задокументированные/реализованные возможности, а не обещания на будущее. Все статусы предлагаются владельцу.

| Capability / Pattern | Forge сегодня | Внешний подход | Пробел | Предлагаемый статус |
| --- | --- | --- | --- | --- |
| Discovery | Целевая концепция Technical Specification; runtime отсутствует | Выборочный сбор контекста, approvals, возобновляемые сессии | Нет исполняемого workflow Discovery | **MUST HAVE** для полного продукта; этап и scope TBD |
| Planning | Детерминированный шаблонный Planner с зависимостями | Проверяемые планы с итеративным выполнением | Нет редактируемого плана, связанного с исполнением, и очереди | **SHOULD HAVE** |
| Subagents | Primary + reviewer; ограниченная revision | Роли и делегирование | Нет общего контракта ролей/handoff | **SHOULD HAVE** |
| Skills | Не реализованы; документация репозитория — статический контекст | Выборочные повторно используемые инструкции/assets | Нет package, applicability, версии, тестирования | **SHOULD HAVE** |
| Tools | Provider-backed вызовы модели; общих project tools нет | Типизированные или задачные интерфейсы действий | Нет управляемого реестра tools | **MUST HAVE** до исполнения действий в проектах |
| MCP | Не реализован | Стандартная интероперабельность tools/resources/prompts | Нет границы trust/consent или governance connectors | **OPTIONAL** интеграция |
| Permissions | Полномочия пользователя описаны; движка разрешений операций нет | Grants для agent/tool capabilities | Нет enforcement по role/project/environment/action | **MUST HAVE** до автономных side effects |
| Sandbox | Не реализован | Изолированное workspace/runtime с ограничением сети и credentials | Нет изоляции исполнения | **MUST HAVE** до запуска команд агентом в пользовательских проектах |
| Durable execution | Нет pause/resume после рестарта процесса | Сохранённое run/thread state | Нет стабильной идентичности и recovery protocol | **MUST HAVE** для долгих workflow |
| Checkpoints | Нет долговечных checkpoints исполнения | Снимки состояния на значимых шагах workflow | Нет checkpoints и проверки устаревшего состояния | **MUST HAVE** для durable execution |
| Memory | В будущем Project Memory и отдельный Forge Knowledge | Thread state отделён от долгосрочного store; approval для memory-примеров | Нет retention/review/access lifecycle | **SHOULD HAVE**; политика — **DECISION REQUIRED** |
| Provenance | Целевые документы описывают evidence, версии и provenance | Связность артефактов, запусков и действий | Нет реализованных ссылок на source/version между runs | **MUST HAVE** для надёжных проектных результатов |
| Event log | Не реализован | Упорядоченные записи активности/событий | Нет append-only журнала действий и retention policy | **MUST HAVE** для проверяемой автономной работы |
| Observability | Smoke output; UI телеметрии run отсутствует | Traces агента/tool/test/time/cost | Нет структурированного user-facing trace | **SHOULD HAVE** |
| Background agents | Не реализованы | Асинхронная работа, status/follow-up/takeover | Нет background scheduler или durable run manager | **OPTIONAL** режим после durable core |
| Human approval | Явный выбор provider; целевые scopes описаны | Pause для review/approval, продолжение после feedback | Нет общего inbox или binding approval receipts | **MUST HAVE** для существенных действий |
| Decision queue | Решения владельца указаны только в документах | Очередь решений с приоритетами | Нет inbox, urgency и defer workflow | **SHOULD HAVE** |
| Dry run | Не реализован | Preview изменений и side effects | Нет effect manifest/preview | **MUST HAVE** для существенных операций |
| Rollback | Revision loop исправляет содержание, это не rollback внешнего состояния | Snapshots, recovery plans, version control | Нет контракта восстановления состояния | **MUST HAVE**, если заявляется обратимость; иначе ограничения должны быть явными |
| Testing | Unit tests и live smoke commands | Проверки конкретного проекта на основе evidence | Нет профилей проверки generated projects и связки acceptance | **MUST HAVE** для каждого класса проекта |
| Evals | Нет документированной Forge-level eval suite | Golden tasks/regression evaluation | Нет baseline качества reasoning workflow | **SHOULD HAVE** до заметного роста автономии routing/workflow |
| Security review | Принципы безопасности; обработка ошибок providers | Специализированные security agents/gates/scans | Нет security gate, зависящего от риска проекта | **MUST HAVE** для чувствительных/high-risk классов проектов |
| Cost controls | Cost tiers, estimated provider usage; budget enforcement нет | Budgets, атрибуция расходов, rate limits | Нет end-to-end budget gate для run | **SHOULD HAVE**; политика — **DECISION REQUIRED** |
| Model routing | Категории, capabilities, cost tiers и fallback | Также учитываются privacy, quality, latency, limits | Часть критериев не моделируется | **SHOULD HAVE**; не вводить scoring преждевременно |
| Project templates | Шаблоны Planner для четырёх классов | Reference projects/templates как стартовая точка | Нет версионируемого набора reference architectures/evals | **OPTIONAL**; шаблон не должен превращаться в требование |
| Plugins/extensions | Не реализованы | Packages объединяют agents, tools, skills и connectors | Нет модели доверия/версий/permissions | **FUTURE** |
| Visual project map | Только целевая идея | Граф объектов проекта с навигацией | Нет UI или graph model | **OPTIONAL** UX capability |
| Project health | В целевой спецификации есть readiness | Объяснимые измерения и blockers | Нет оценки health и проверки свежести | **SHOULD HAVE**; не сводить к непрозрачному score |

## MUST / SHOULD / OPTIONAL / FUTURE / REJECT

Это рекомендации benchmark, а не утверждённые владельцем приоритеты.

### MUST HAVE — до структурной полноты Forge как системы исполнения проектов

- Durable run state и значимые checkpoints с безопасным resume и обработкой неопределённых side effects.
- Явно исполняемые границы permissions и изоляция до того, как agents смогут действовать в проектах пользователя.
- Provenance артефактов/событий и проверяемая запись действий, approvals, сбоев и verification; без chain-of-thought.
- Approval привязан к конкретному действию, scope, версии контекста проекта и допустимым side effects.
- Dry-run/impact preview и честное описание recovery/обратимости для существенных операций.
- Verification и security gates по классу проекта и риску; завершение подтверждается evidence.
- Обработка untrusted input, которая не позволяет внешнему контенту получить authority инструкций.

### SHOULD HAVE

- Чёткие contracts и структурированные handoffs для specialist agents, skills и tools.
- Retrieval, compaction и checkpoint summaries контекста с сохранением решений, неизвестного и ссылок на источники.
- User-facing observability run, Decision Inbox, budget controls, объяснимый project health и Forge Evals/golden scenarios.
- Проверяемые циклы Project Memory и Forge Knowledge с provenance и обработкой устаревших сведений.
- Ready-to-Run handoff package с setup, ссылками на configuration, tests, ограничениями и verification evidence.

### OPTIONAL

- MCP и другие протоколы connectors; remote/background execution; visual project map; project templates; scheduling и внешние триггеры; параллельное выполнение при явных dependencies.
- Применять, когда этого требуют класс проекта и ценность пользователю, а дополнительная нагрузка на эксплуатацию/безопасность оправдана.

### FUTURE

- Marketplace расширений, широкая plugin ecosystem, цикл self-improvement Forge, адаптивная оптимизация стоимости/качества и автономная работа в течение нескольких дней. Это зависит от durable state, evals, security controls и политики владельца.

### REJECT / NOT APPLICABLE как значения по умолчанию

- Единый project-health score, скрывающий блокеры и evidence.
- Загрузка каждой беседы, файла репозитория, skill и knowledge item в каждый вызов модели.
- Majority vote как стандартный способ разрешения разногласий агентов.
- Неограниченный shell/network/credential access как разрешение по умолчанию.
- Шаблоны или прежние технологические стеки как обязательная архитектура.
- Автоматическое изменение Forge на основе результата одного проекта.
- Максимизация числа функций или копирование proprietary UI/реализации конкурента.

## UI — будущий Forge Control Center

**ПРЕДЛОЖЕНО** — информационная архитектура с постепенным раскрытием деталей:

- **Project dashboard:** цель, стадия lifecycle, blockers readiness, измерения health, последняя активность.
- **Conversation / Discovery:** намерения владельца, вопросы, ответы, ссылки evidence, неразрешённые противоречия.
- **Requirements and architecture:** версионируемые артефакты, решения, альтернативы, зависимости, impact изменений.
- **Decision Inbox и approvals:** почему важно, срочность, evidence, варианты, последствия, точный scope/version approval, approve/reject/defer.
- **Plan:** фазы, граф зависимостей, статус задач, назначенная роль/provider и blockers.
- **Run / Activity:** durable run state, agents, tool calls, duration/cost, retries, ошибки, approvals, checkpoint, pause/resume/cancel/takeover.
- **Evidence and memory:** происхождение, confidence/quality, применимый scope, provenance, retention и supersession.
- **Artifacts:** diffs, generated files, builds, screenshots, tests и handoff package.
- **Security / permissions:** граница workspace/environment, включённые connectors, ссылки на secrets (без значений), разрешения сети/tools и pending risk gates.

Начальный экран должен отвечать «что происходит, что нужно от меня и что изменится?»; технические traces и подробное evidence доступны по запросу.

## Security

Целевые границы, нужные до автономного исполнения:

1. Считать websites, repositories, issues, email, documents, API responses, generated text и tool metadata недоверенными данными, если они отдельно не утверждены как политика Forge.
2. По возможности отделить orchestration/authentication и audit controls от изолированного исполнения команд; применять least privilege к проектным workspace, сетевым правилам и scoped credentials.
3. Исполнять permissions в runtime/tool boundary, а не только в prompts. Связывать grants с role, project, environment, operation, scope и сроком/отзывом.
4. Передавать secrets только как ссылки, внедряемые доверенным runtime для разрешённых операций; не помещать реальные secrets в context, Project Memory, event payloads или artifacts.
5. Рассматривать connectors/MCP servers как зависимости ПО. Показывать их tools и запрашиваемый доступ; валидировать arguments/results и требовать consent для подключения или чувствительных действий.
6. Перед рискованным действием показывать затронутые файлы/системы, внешние эффекты, риск, доступную оценку стоимости, обратимость, recovery path и точный scope approval.
7. Вести проверяемую запись событий с redaction и retention rules; не хранить hidden reasoning. Поддерживать расследование инцидентов и отзыв credentials.

Глубина security должна соответствовать риску проекта. Нужен ли independent security approval для каждого generated project — **DECISION REQUIRED**.

## Agent system

Определить несколько понятий вместо одной универсальной записи «agent»:

- **Role/Agent contract:** цель, фаза, допустимые input/output, требования к evidence, запрещённые действия, tools, ограничения модели, требование approval, поведение при сбое/handoff, owner/version/evals.
- **Skill:** выборочно загружаемые инструкции, ссылки, scripts/assets, prerequisites, applicability, permissions, версия и оценка. Knowledge описывает общие patterns; skills упаковывают повторно используемую экспертизу/workflow.
- **Tool:** типизированный интерфейс действий/чтения с input validation, лимитами output, семантикой idempotency/retry, декларацией permission, audit event и контрактом ошибок.
- **Connector:** аутентифицированная граница внешнего сервиса с владельцем доверия, scopes, обработкой данных, отзывом и процессом обновления версии. MCP может реализовать transport/discovery, но не авторизацию Forge.
- **Runtime:** run identity, checkpoint/recovery, сборка/compaction контекста, отмена, concurrency, sandbox binding, ожидание approval, фиксация событий и подготовка artifacts.
- **Handoff:** краткая цель, принятые решения, ссылки evidence/artifacts, нерешённые вопросы, следующее действие/permissions и ожидаемый output; следующий agent сам извлекает нужные исходные artifacts.

Выполнять параллельно только независимые задачи с явными dependencies и координацией совместных artifacts. При разногласии сравнивать evidence/source authority, scope и risk; существенные конфликты передавать на эскалацию вместо голосования. Retries должны быть ограничены и учитывать неопределённые внешние side effects.

## Lifecycle

Предложенный в Stage 1.4 lifecycle остаётся согласованным. Добавить следующий операционный цикл после утверждённого плана и вокруг каждого существенного действия:

```text
Plan / approved scope
  -> create durable Run + checkpoint
  -> assemble minimal context + applicable skills
  -> verify role/tool permissions and sandbox
  -> preview consequential changes -> approval if required
  -> execute one bounded step -> append action/result event
  -> verify artifact/acceptance evidence -> checkpoint
  -> pause / wait / resume / recover / hand off as needed
  -> Ready-to-Run gate -> owner handoff / authorized release
  -> operation feedback -> candidate lesson review
```

Run state — не Project Memory. Event history — не chain-of-thought. Approval не переносится на существенно изменившийся scope или версию контекста. Ответ агента о завершении — не результат verification. Release/deployment остаётся отдельно авторизуемым действием.

Формальный приоритет Specification над Architecture, политика approvals и ответственность за lifecycle здесь не определяются.

## DECISION REQUIRED

Решения владельца нужны до превращения предложений в постоянную архитектуру или обязательства roadmap:

1. Является ли durable execution/checkpointing обязательным до любых действий с файлами проекта или внешними сервисами либо только до background/multi-session работы?
2. Какие run artifacts/events хранить, как долго, и кто может их просматривать/export/delete?
3. Какая политика автономии и approvals действует по уровню риска и среде? Для каких действий approval нужен всегда?
4. Какой первый sandbox нужен: local, remote или заменяемый интерфейс с одной начальной реализацией? Какой network access разрешён по умолчанию?
5. Какие типы tools входят в первый scope (filesystem, terminal, browser, Git, GitHub, database, deployment)?
6. С чего начать Control Center: desktop, web или CLI/API с последующим UI? Какие поверхности обязательны для MVP?
7. Каковы правила retention, privacy, исправления и удаления Project Memory/Forge Knowledge? Кто может продвигать уроки?
8. Каким классам проектов нужен независимый security review или user acceptance gate?
9. Какие бюджеты ресурсов требуется ограничивать (cost, time, tokens, concurrency, API limits) и кто их задаёт?
10. Какие предоставленные владельцем факты и границы доступа относятся к Axis, KonnexMastery, FlashCast, Mastery Creator и KeyCore-Hub? Предположений об интеграциях здесь нет.

## Conflicts

- **Текущий код и целевая Vision:** orchestration провайдеров, review/revision и шаблонный планировщик существуют; durable runs, Discovery, project memory, tools, sandboxes и UI — нет. Явно маркировать статус.
- **Planner и lifecycle исполнения:** Planner v0.1 создаёт статические планы и не исполняет задачи и не обновляет их статусы. Нельзя описывать задачи как отслеживаемый прогресс до реализации.
- **Revision loop и rollback:** однократная правка содержания не является rollback состояния filesystem, database или внешних side effects.
- **Provider fallback и retry задач:** ограниченный fallback маршрутизирует после ошибки provider, но не обеспечивает идемпотентность/безопасность повторения произвольных действий tools.
- **MCP и governance Forge:** interoperability MCP не даёт гарантий approvals, provenance, sandbox или lifecycle Forge.
- **Event trace и внутренние рассуждения:** фиксировать наблюдаемые inputs/actions/results по необходимости; не показывать и не хранить hidden chain-of-thought.
- **Решения владельца остаются открытыми:** нерешённые вопросы lifecycle/approval Stage 1.4 остаются открытыми; Stage 1.5 не решает их за владельца.

Прямого противоречия, требующего менять документацию текущей реализации, не найдено. Основной риск документации — выдавать целевые идеи за готовые функции.

## Documentation impact

| Документ | Влияние Stage 1.5 |
| --- | --- |
| `docs/STAGE_1_5_EXTERNAL_BENCHMARK.md` | Этот концептуальный benchmark и реестр предложений. |
| `docs/TECHNICAL_SPECIFICATION.md` | **Рекомендуется после review владельцем:** добавить утверждённые target contracts для durable runs, events, tools/skills, security boundaries, evaluation и UI. Не копировать весь benchmark в specification. |
| `docs/FORGE_VISION.md` | Немедленных изменений нет. Направление уже охватывает контролируемую multi-agent работу с review; пересмотреть при изменении продуктовых приоритетов владельцем. |
| `docs/ARCHITECTURE.md` | Без изменений: это документация текущей реализации. Обновлять при реализации функций. |
| `docs/ROADMAP.md` | Не менять до приоритизации владельцем. Добавлять принятые предложения только после решения о scope/порядке. |
| `docs/DECISIONS.md` | Без изменений: предложения ещё не решения. Записывать утверждённые владельцем архитектурные решения и основания. |

## Next stage

**Рекомендуемый следующий концептуальный этап: Stage 1.6 — Durable Run and Permission Boundary Decision Record.** Сначала разрешить только те вопросы владельца, которые задают минимальный run lifecycle, сохраняемое состояние, связь approval с действием, scope tools/actions, sandbox boundary и принципы retention событий. Затем внести принятые контракты в Technical Specification и обновлять Roadmap только по согласованным приоритетам. Не выбирать базы данных, облачных поставщиков или UI framework, пока этого не требуют требования.

## Источники и ограничения исследования

Источники — публичная продуктовая/разработческая документация или рецензируемая benchmark-статья, проверенные 2026-10-03. Детали продукта могут меняться; это сравнение подходов, а не гарантия функций или безопасности. Некоторые системы описывают поведение только для одной поверхности или тарифа. Benchmark не исследует закрытые внутренние механизмы и не утверждает, что Forge должен воспроизводить функции поставщиков.

- OpenAI, [Sandbox Agents](https://developers.openai.com/api/docs/guides/agents/sandboxes).
- GitHub, [About custom agents](https://docs.github.com/en/copilot/concepts/agents/copilot-cli/about-custom-agents), [Use Copilot agents](https://docs.github.com/en/copilot/how-tos/copilot-on-github/use-copilot-agents), [Hooks reference](https://docs.github.com/en/copilot/reference/hooks-reference).
- Cursor, [Rules](https://docs.cursor.com/context/rules-for-ai), [Background Agents](https://docs.cursor.com/background-agent), [Memories](https://docs.cursor.com/en/context/memories).
- Anthropic, [Steering Claude Code](https://claude.com/blog/steering-claude-code-skills-hooks-rules-subagents-and-more), [MCP](https://docs.anthropic.com/en/docs/agents-and-tools/mcp).
- OpenHands, [SDK documentation index](https://github.com/OpenHands/docs/blob/main/llms.txt) и связанные руководства по persistence, pause/resume, skills, secrets и security.
- SWE-agent, [Configuration](https://github.com/SWE-agent/SWE-agent/blob/main/docs/config/config.md), [Agent-Computer Interface paper](https://arxiv.org/abs/2405.15793).
- LangGraph, [Persistence](https://langchain-ai.github.io/langgraphjs/how-tos/persistence-postgres/).
- Model Context Protocol, [server primitives](https://modelcontextprotocol.io/specification/draft/server/index), [security model](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/SECURITY.md).
