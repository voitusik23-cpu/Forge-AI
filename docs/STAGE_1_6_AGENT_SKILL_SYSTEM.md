# Forge AI — Stage 1.6 Agent and Skill System

**Status:** CONCEPTUAL ARCHITECTURE. Existing concepts are marked **CONFIRMED**; proposed design boundaries remain **PROPOSED** until accepted by the owner. This document does not describe implemented functionality.

## CONCEPT MODEL

The central boundary is:

> **Agent ≠ Skill ≠ Capability ≠ Knowledge ≠ Tool ≠ Project Memory.**

| Concept | Definition | Answers | Example |
| --- | --- | --- | --- |
| **Agent** | A role-bound executor instantiated for a bounded task/run. It applies judgment and returns a result under a contract. | Who performs the work? | Architecture Agent |
| **Skill** | A versioned, reusable procedure for a class of work. It guides execution but has no independent authority to expand scope or permissions. | How is this work performed? | Legacy Analysis |
| **Capability** | A technical ability available in a runtime, subject to prerequisites and authorization. | What can the system technically do? | Browser access |
| **Tool** | A concrete executable or read interface that uses one or more capabilities. | What operation can be invoked? | Open a page; run a scoped command |
| **Knowledge** | A reviewed, provenance-bearing fact, pattern, constraint, or lesson with applicability conditions. | What has Forge learned or established? | A verified migration compatibility pattern |
| **Project Memory** | Project-specific, versioned understanding and history: requirements, decisions, evidence, approvals, conventions and outcomes. | What is true or has happened in this project? | Approved architecture decision |

**CONFIRMED:** The Technical Specification already distinguishes Project Memory from Forge Knowledge and states that Project Memory does not publish into Forge Knowledge automatically. It also separates provider from agent role, and says a model result is not authorization. Stage 1.6 extends these boundaries; it does not replace them.

**PROPOSED:** Forge selects a compatible composition for each bounded task:

```text
Task + approved project context
  -> Agent role
  -> candidate Skills -> applicability / conflict check -> selected Skills
  -> required Capabilities -> permission check -> permitted Tools
  -> minimal context assembly -> bounded execution
  -> result + artifacts + evidence + provenance + status
```

No candidate combination may create permissions. Each layer can narrow the work; none may silently expand the approved objective, scope, or authorization.

## AGENT MODEL

### Identity and lifecycle

**PROPOSED:** Separate an Agent definition (versioned role contract) from an Agent instance (one assignment during a Run). Roles can be reusable and long-lived as definitions; instances are created for bounded tasks and then completed, paused, failed, or cancelled. A role does not require its own model or provider. Dispatcher/provider policy chooses a compatible provider for the task; the Agent remains provider-neutral.

Candidate roles include Discovery Agent, Research Agent, Requirements Analyst, Architecture Agent, Capability Analyst, Planner, Implementer, Code Reviewer, Security Reviewer, Test Engineer, Documentation Agent, Migration Analyst, and Release/Deployment Reviewer. These are a role catalogue to consider, not a requirement to create a separate agent for every label. Several roles may initially share an implementation or model.

### Agent contract

Every Agent definition should declare:

- purpose, supported project phases/task classes, owner/source and version;
- accepted task/objective, scope, constraints, acceptance criteria and required context;
- expected output schema and evidence bar;
- Skills it can use and required/optional Capabilities;
- allowed and forbidden Tools/actions and the permissions it may request (never grant);
- model/provider constraints, if any, as preferences/eligibility constraints rather than provider SDK logic;
- timeout, cancellation, checkpoint and failure behavior;
- delegation policy: whether it may request a handoff; only the Orchestrator authorizes and schedules it;
- validation/evaluation cases and status (candidate, reviewed, enabled, deprecated, blocked).

An Agent receives only task-relevant requirements, evidence, decisions, approved context, Skills, and Tools authorized for this assignment. It may return a result, artifact references, evidence, proposals, verification observations, status, structured errors, provenance, and follow-up needs. It must not silently alter approved Requirements, Specification, Architecture, Plan, permissions, or scope. Proposed changes enter the established change/approval process.

**PROPOSED status contract:** `PENDING`, `READY`, `RUNNING`, `WAITING_FOR_USER`, `WAITING_FOR_EXTERNAL_EVENT`, `BLOCKED`, `RECOVERABLE`, `FAILED`, `COMPLETED`, `CANCELLED`. These describe run/assignment state; they do not mean approval or quality. Retry/recovery is bounded and recorded. Failure of one assignment is isolated and preserves valid partial artifacts without marking the overall task successful.

### Orchestration and delegation

**PROPOSED:** The Orchestrator owns assignment, eligibility, scheduling, checkpoints, authorization gates, retries, cancellation, handoffs and result aggregation. Agents can recommend another role or report a missing Skill/Capability but cannot spawn arbitrary agents, change their own permissions, or authorize another Agent. The Orchestrator validates each new assignment against the same scope and permission boundary.

Sequential specialist handoff is the default. Example: Architecture Agent uses Legacy Analysis and Architecture Decision Skills, then hands off accepted constraints, evidence references and open questions to Planner. The next Agent receives a structured handoff, not the entire prior conversation.

## SKILL MODEL

A **Skill** is a reusable, bounded procedure—not an Agent, tool, permission, project requirement, or authoritative source of facts. It can tell an authorized Agent how to do a task, but cannot independently run, approve, expand scope, grant access, or change project decisions.

### Skill manifest and progressive disclosure

**PROPOSED:** A Skill manifest contains only enough metadata for discovery and eligibility:

- stable ID, name, summary, version, source/owner, provenance and lifecycle status;
- purpose, task/project-phase applicability and explicit non-applicability;
- prerequisites, dependencies, conflicts and compatible project types/runtimes;
- required Capabilities, requested Tools and declared permission needs;
- input/output expectations, risk class and side-effect declarations;
- validation/evaluation status, compatible versions, supersedes/replaced-by and usage history.

Load content progressively:

1. **Manifest:** discover and filter candidates without placing full instructions in context.
2. **Detailed instructions:** load only after a Skill passes applicability, trust, compatibility and permission checks.
3. **Resources:** retrieve only referenced templates, examples, standards and supporting documents needed by this task.
4. **Scripts/tools:** treat executable content as code/dependencies; inspect, validate, permission-check and sandbox it separately. A Skill cannot smuggle executable authority through prose.

### Sources and statuses

**PROPOSED source classes:** Forge built-in; project-local; owner-approved external; experimental. An external source is not trusted by default. Project-local Skills apply only in their project unless separately generalized and approved.

**PROPOSED lifecycle:**

```text
OBSERVED PATTERN
  -> CANDIDATE SKILL
  -> GENERALIZATION + PROVENANCE
  -> TEST CASES + EVALUATION
  -> SECURITY / SCOPE REVIEW
  -> OWNER OR DELEGATED GOVERNANCE APPROVAL
  -> VERSIONED, ENABLED SKILL
  -> MONITORING / REVALIDATION
  -> REVISED, DEPRECATED, BLOCKED, OR RETIRED
```

Suggested status vocabulary: `UNKNOWN`, `EXPERIMENTAL`, `CANDIDATE`, `REVIEWED`, `VERIFIED`, `TRUSTED`, `DEPRECATED`, `BLOCKED`. Status describes evidence/trust state, not permission. An owner may set stricter promotion rules. **DECISION REQUIRED:** who can approve Forge-wide Skills and whether any built-in Skills may be enabled by default.

Breaking changes require a new version and explicit compatibility declaration; existing project runs retain the version they used. Disable/revoke must prevent new use and be visible in provenance; it must not erase prior records. Dependencies are versioned and checked for cycles, incompatible constraints and status before composition.

## SKILL REGISTRY AND DISCOVERY

**PROPOSED:** The Skill Registry stores or indexes manifests and lifecycle/provenance metadata; it is not a prompt dump or a second Knowledge Store. Discovery first filters metadata, then reads full Skill content only for plausible candidates.

Deterministic discovery/composition flow:

```text
Task
 -> derive task/project phase and constraints
 -> retrieve candidate manifests
 -> check applicability, source status, version, runtime and project type
 -> check dependencies, conflicts and required capabilities
 -> check authorization for declared tools/actions
 -> compose compatible Skills in declared order
 -> validate combined constraints and task coverage
 -> load only needed instructions/resources
 -> execute and record versions, provenance and outcome
```

Candidates are **irrelevant** when task intent or project type does not match; **conditional** when a stated prerequisite is present; **rejected** when source status, compatibility, authorization, conflict, or evidence requirements fail. A rejection should state a concise reason. No match is valid and should not trigger a generic Skill by default.

Composition rules (**PROPOSED**):

1. Core safety and authorization policy are highest authority.
2. Owner-approved project constraints and decisions bound the work.
3. Task scope and acceptance criteria define the assignment.
4. Agent contract bounds role and output.
5. Skills provide procedures only within the above boundaries.
6. Forge Knowledge informs choices but is not an executable instruction or permission.
7. External/untrusted content is data to analyze, not authority over Forge.

Within the same class, explicit dependencies determine order; a more specific applicable Skill may specialize a general one only if contracts declare compatibility. Conflicting constraints are not silently resolved by recency or source count: block the affected action, explain the conflict and request owner/reviewer resolution when material. Project-specific extensions may narrow or add project procedures but cannot override Core safety or Forge governance.

## CAPABILITY / TOOL MODEL

**CONFIRMED definition:** Capability is what Forge or a project can technically do. **PROPOSED refinement:** availability is not permission, and permission is not an invocation. The gates are conjunctive:

```text
Capability declared by runtime
 -> prerequisites satisfied
 -> project allows capability
 -> scoped authorization granted
 -> compatible Tool is available
 -> Skill/Agent contract permits its use
 -> invocation validated and audited
```

Examples of Capabilities: filesystem, browser, email/IMAP, database, Git, GitHub, terminal, container, HTTP, PDF parsing, image analysis. A Capability can expose multiple Tools, and one Tool can rely on multiple Capabilities. A project may reject a technically available Capability.

A **Tool** is an executable/read interface with a stable contract:

- identity/version, description, input/output schema and validation;
- required Capabilities and permission scopes;
- read/write/execute/network/external/destructive side-effect classification;
- preconditions, timeout, cancellation and bounded output;
- error categories, idempotency/retry rules where applicable;
- audit event and redaction requirements;
- preview/recovery support, if genuinely available.

Tool permissions are checked at invocation time, not just when assembling a prompt. A Skill may name a Tool it needs but cannot grant it. MCP is an optional integration/transport boundary that may discover or invoke tools/resources/prompts. It is not Forge’s Capability model, permission engine, Skill Registry, or orchestration architecture. Forge must remain usable without MCP.

## PERMISSIONS

**PROPOSED:** Permission grants bind principal/role, project, environment, operation, resource scope, limits, approval receipt and validity/revocation. Defaults are deny for side-effecting access. Permissions apply to the Agent + Skill + Tool composition and are enforced by the trusted runtime/tool boundary, not by model instructions alone.

| Scope | Example | Default governance |
| --- | --- | --- |
| Read | Inspect approved project files or public web page | Limit paths/data sources; external/untrusted content remains data |
| Write | Change files in project workspace | Specific workspace/branch; show diff; verify output |
| Execute | Run build/test or command | Isolated environment and allowlisted purpose; timeout/resource limits |
| Network | Reach domains/services | Deny or restrict by environment and purpose; never infer broad access from HTTP capability |
| Secrets | Use named secret reference | No raw value in context/logs; scoped injection only for authorized operation |
| External system | GitHub, email, database, cloud API | Named connector/account/resource and limited operations |
| Forge internals | Change Core, policies, registries or global Skills | Separate high-impact authorization; project grant is insufficient |
| Destructive / production | Delete, migrate, publish, deploy, alter production | Preview, recovery statement, explicit scoped approval; some effects may be irreversible |

Approval is required for high-impact, irreversible, credential-bearing, externally visible, production, destructive or materially scope-changing actions. Exact thresholds and which low-risk actions can be autonomous remain **DECISION REQUIRED**. Approval is bound to the exact reviewed action, scope, project-context/artifact version and side effects; material change invalidates it. Revocation takes effect before a subsequent tool call. No affirmative response grants global permission.

## CONTEXT

**PROPOSED context assembly:**

```text
Task
 -> relevant Requirements
 -> relevant Evidence
 -> applicable Decisions
 -> applicable Knowledge
 -> selected Skill manifests/instructions/resources
 -> Agent contract
 -> permitted Tool/Capability descriptions
 -> required project artifacts
 -> minimal sufficient context
```

Do not load all Project Memory, Forge Knowledge, Skills, Tools, or repositories by default. Retrieve by task, project phase, paths/artifact IDs, applicability and permission; report material unknowns rather than filling gaps with invented facts.

Each retrieved item carries provenance: source/creator, project or global scope, version/time, evidence/decision links, applicability, trust/status and supersession. Context summaries and handoffs keep references to authoritative artifacts; a summary is not allowed to silently replace an approved decision. Secrets are references resolved only at authorized execution time and are excluded from normal context, logs and memory.

## MULTI-AGENT

**PROPOSED:** Prefer one capable Agent for one bounded task; add roles only when they contribute independent expertise, assurance or necessary separation of duties.

- **Sequential:** default for dependency chains, e.g. Research → Requirements → Architecture → Plan → Implement → Review.
- **Parallel:** only for independent read/analysis tasks or isolated changes with explicit dependencies, resource limits and merge/reconciliation steps. Do not parallelize tasks where one changes assumptions another is using.
- **Review loop:** preserve the existing bounded revision behavior; it is a workflow policy, not a Skill, and cannot authorize extra changes.
- **Independent analysis/debate:** keep claims, sources, assumptions and proposed actions separate; an orchestrator/reviewer compares evidence and material alternatives.
- **Hierarchical delegation:** Orchestrator controls child assignment, scope, permission, budget and stop conditions. Agents may request delegation; they do not self-authorize it.

Aggregation records each contribution, missing evidence, overlap, disagreement and unresolved issue. Do not select a majority automatically. Compare source authority, reproducibility, requirement coverage, skill/role fit, risk and confidence basis. Ask for review/owner decision when disagreement affects scope, safety, architecture or external consequences. Partial outputs remain labeled partial.

Retries are finite and reason-coded. Retry only when the failure is plausibly transient and the action is safe/idempotent; uncertain external side effects require state inspection or human takeover before retry. Provider fallback does not imply that a task/tool action can safely be repeated. Revoked permission stops new invocations and moves the run to a blocked/waiting state; cancellation and recovery preserve the audit trail.

## EVALUATION

Agent/Skill readiness should be measured with versioned cases before promotion and after material change. Evaluation dimensions may include correctness, requirement coverage, acceptance evidence, reliability, security, cost, latency, reproducibility and regression behavior. Results are scoped to tested tasks/models/runtimes; they are not universal guarantees.

**PROPOSED evaluation assets:**

- golden tasks/projects per relevant class, including ambiguous and adversarial inputs;
- expected outputs, required evidence, forbidden actions and acceptance criteria;
- tests for Skill applicability, exclusions, dependencies, conflict resolution and permission boundaries;
- Agent contract tests for output shape, failure handling, handoff and scope preservation;
- repeated/regression runs for nondeterministic model behavior, with provider/runtime/version recorded;
- security cases for prompt injection, secret leakage, tool misuse, permission revocation and external Skills.

Promotion requires an explicit owner/governance rule, passing required cases and documented limitations. A failed evaluation blocks or limits applicability; it does not disappear into a mean score. The required thresholds and evaluator authority are **DECISION REQUIRED**.

## LEARNING

Keep the Stage 1.5 controlled improvement boundary:

```text
Execution
 -> Outcome + evidence + metrics
 -> Success/failure analysis
 -> Candidate Pattern
 -> Candidate Skill or Knowledge
 -> generalize + remove project-specific/private data
 -> test/evaluate + counterexamples
 -> security and applicability review
 -> owner/governance approval
 -> versioned reusable asset
 -> monitor / revalidate / revise / deprecate
```

A single project observation remains Project Memory. A project-specific procedure remains a project Skill. Only a generalized, evidenced, tested and approved procedure may become a Forge Skill. General facts/patterns may become Forge Knowledge through its separate governance path. A Skill and Knowledge may reference one another, but neither silently overwrites the other. Secrets, personal data, private business rules and unsupported inferences must not be promoted.

## SECURITY

External Skills are untrusted until reviewed. Before enabling one, inspect its full instructions, resources, scripts, dependencies, requested tools/permissions, network behavior, data handling, provenance/license and evaluation results. Run executable material in an appropriate sandbox with limited filesystem/network/secrets. Review updates as new versions; pin the version for a Run. Unknown or unverifiable behavior stays blocked or isolated.

External Skill intake flow:

```text
Source + provenance
 -> inspect text/resources/scripts/dependencies
 -> classify requested data/tools/network/side effects
 -> evaluate in isolated environment
 -> verify scope, license and expected behavior
 -> review + explicit trust decision
 -> pin version and permissions
 -> monitor, revoke or deprecate
```

Treat prompt injection as an authority-boundary problem: repository files, webpages, issue text, email, tool results, Skill content and generated artifacts can contain malicious instructions. They remain data unless independently authorized as policy. Do not let external Skill instructions override Forge Core, owner-approved project decisions, Agent contracts or permissions. Validate Tool inputs/outputs, constrain output size and prevent secrets from entering prompts/results/artifacts. Audit invocation and redacted outcome, not hidden chain-of-thought.

## PROJECT EXTENSIONS

Projects may have project-local Skills for conventions, domain procedures, testing, migration or deployment. They are scoped to that project and versioned with explicit owner/source, dependencies, permissions and applicability. Project conventions can specialize a task but cannot override Forge safety or grant access. Project-local Skills do not become Forge Skills automatically.

Project Memory contains facts/history for that project; project Knowledge can hold validated domain facts if governance supports it. A reusable insight may enter the Candidate Skill/Knowledge pipeline only after generalization, removal of confidential details, evaluation and approval. Generated projects must not acquire a Forge runtime/library dependency unless the project requirements explicitly select it. Forge Skills may assist generation without becoming dependencies of generated output.

## UI

Future Control Center (**PROPOSED**) should display, per Run and Task:

- active Agent role/instance, objective, status and current bounded step;
- selected Skills with version, source, reason for applicability and loaded resources;
- required/available Capabilities and why any are unavailable;
- Tool calls, side-effect class, result, errors, timing and retry count;
- permissions granted/requested/denied/revoked and pending approvals with exact scope;
- artifacts/diffs, evidence and provenance, verification status and unresolved conflicts;
- checkpoint, wait/block reason, pause/resume/cancel/takeover actions;
- partial/final outcome and follow-up requirements.

Use progressive disclosure: summary and actions needed from the owner first, evidence and technical traces on demand. Never expose internal chain-of-thought, raw credentials, or unreviewed external Skill instructions as trusted Forge guidance.

## MUST / SHOULD / OPTIONAL / FUTURE / REJECT

These are recommendations for owner review, not approved commitments.

### MUST HAVE before implementing autonomous project actions

- Distinct contracts and identities for Agent, Skill, Capability, Tool, Knowledge and Project Memory.
- Runtime-enforced least-privilege authorization for each Agent + Skill + Tool invocation; permission and approval cannot be inferred from a prompt.
- Trust review, version pinning and sandbox policy for executable/external Skills and Tools.
- Provenance and auditable status/results for assignments, context, Skills, Tools, approvals and artifacts; no chain-of-thought capture.
- Bounded retries/delegation and safe interruption/revocation behavior, with durable checkpoints before long-running execution.
- Validation against the approved task scope and acceptance evidence before reporting completion.

### SHOULD HAVE

- Metadata-first Skill Registry with progressive disclosure and deterministic applicability/composition checks.
- Reusable Agent contracts, structured handoffs, project-specific Skill support and a small evaluation/golden-task suite.
- Control Center views for run state, selected assets, permissions, approvals, provenance and recovery.
- Separate candidate-to-approved governance cycles for Forge Skills and Forge Knowledge.

### OPTIONAL

- External Skill repositories/marketplaces, MCP connectors, parallel specialist review, dynamic role instantiation, skill recommendations and project health views. Enable only with specific value, permissions and evaluation.

### FUTURE

- Broad plugin ecosystem, autonomous Skill generation/promotion suggestions, cross-project learning and adaptive orchestration. These depend on durable runtime, trust, evaluation, and owner-approved governance.

### REJECT / NOT APPLICABLE

- One universal Agent or Skill for all work; maximizing agent count.
- Loading every Skill, Tool, repository and Knowledge item for every task.
- Skills that hide requirements, modify scope or grant permissions.
- Every Capability enabled by default or silent permission escalation.
- Automatic promotion of one-off observations or project logic into Forge-wide assets.
- Unlimited spawning/retries, majority vote without evidence, or invisible external Skills.
- Project-specific behavior embedded into Forge Core; agents silently changing requirements.
- A mandatory single language/runtime or mandatory Forge dependency in generated projects.

## DECISION REQUIRED

Owner/governance decisions still open:

1. Who owns, reviews and approves built-in, external and Forge-wide Skills? May approval be delegated, and to whom?
2. Which Skills (if any) are enabled by default, and what minimum verification means `VERIFIED` or `TRUSTED`?
3. Can Agents request broader access mid-run, or must they pause for a newly scoped approval? Which permission types always require an owner?
4. Which actions/tools are disallowed, always approval-gated, or permitted under a bounded policy? Define risk tiers and environment differences.
5. What is the first trust boundary for Skills with scripts/network access: local sandbox, remote sandbox, or no executable Skills initially?
6. What versioning/compatibility policy applies to Skills used by active or resumed Runs? How quickly may a compromised Skill be revoked?
7. Who resolves conflicts between owner project instructions, approved requirements, Skills and external evidence, and which sources are authoritative for each claim type?
8. What evaluation evidence and thresholds are required to promote Agents/Skills? Who can override a failed evaluation?
9. What Run, tool, artifact and Skill usage history is retained, visible, exportable or deletable?
10. May one Agent invoke another directly through an Orchestrator request? What fan-out, concurrency, cost and depth limits apply?
11. What changes to Forge Core or governance require separate owner approval and release procedures?

This document does not answer these on the owner’s behalf.

## CONFLICTS

- Existing `TECHNICAL_SPECIFICATION.md` §9 lists potential role-based agents and clarifies that role is not a provider; Stage 1.6 makes the role contract explicit without making those roles implemented.
- Existing Planner creates static plans and does not execute or update statuses. Agent contracts and run statuses described here are target concepts, not current Planner behavior.
- Existing multi-agent execution is primary + reviewer with one revision. The proposed general delegation system does not expand that implemented limit.
- Technical Specification separates Forge Knowledge from Project Memory. This proposal preserves that separation and adds Skill as a third, procedural asset rather than merging them.
- Tool/Capability examples are target concepts. Current provider calls do not imply that filesystem, terminal, browser or external-service tools exist in Forge.
- Stage 1.5 proposes durable runs, sandbox, scoped permissions, event provenance and external Skill review. Stage 1.6 depends on those boundaries; it does not define a selected database, sandbox vendor, UI, MCP dependency or final permission policy.
- Stage 1.4 approval ordering and lifecycle questions remain open. No conflict resolution or owner decision is inferred here.

## DOCUMENTATION IMPACT

| Document | Impact |
| --- | --- |
| `docs/STAGE_1_6_AGENT_SKILL_SYSTEM.md` | This conceptual model and governance proposal, in English and Russian. |
| `docs/TECHNICAL_SPECIFICATION.md` | **Recommended after owner review:** fold accepted stable boundaries into §§9, 13–16 and future capability matrix. Keep detailed lifecycle/status, registry and governance policy here or in a linked specification only if accepted. |
| `docs/STAGE_1_5_EXTERNAL_BENCHMARK.md` | No change; Stage 1.6 refines the benchmark’s agent/skill/tool proposals. |
| `docs/ARCHITECTURE.md` | No change: describes current implementation; no Agent/Skill runtime was implemented. |
| `docs/ROADMAP.md` | No change before owner prioritization and sequencing. |
| `docs/DECISIONS.md` | No change: Stage 1.6 contains proposals and unresolved questions, not approved decisions. |
| `docs/FORGE_VISION.md` | No change; the existing product direction is compatible. |

## NEXT STAGE

**Recommended:** Stage 1.7 — Agent/Skill Governance and Permission Policy Decisions. Resolve approval authority, trust/status criteria, permission escalation, external/executable Skill boundaries, evaluation thresholds and active-run version pinning. Then update the canonical Technical Specification with only the accepted stable contracts. Defer implementation and technology selection until these boundaries and the durable Run model from Stage 1.5 are accepted.

## IMPORTANT FINAL QUESTION — FOUNDATION BEFORE IMPLEMENTATION?

**Recommendation: YES, the conceptual Agent + Skill boundaries should be foundational before implementing a general Agent/Skill runtime.** They prevent role/procedure/tool/knowledge/permission concerns from collapsing into an ungoverned prompt bundle. This is a recommendation, not an owner-approved implementation mandate.

Freeze now, at the conceptual contract level:

1. Agent is a bounded role/executor; Skill is a reusable procedure; Capability is technical ability; Tool is an invocation; Knowledge is a reviewed fact/pattern; Project Memory is project-scoped history.
2. Neither Agent nor Skill grants permission; authorization is enforced at tool invocation and bound to scope.
3. Skills cannot change requirements, architecture, approved scope or governance; external content has no instruction authority by default.
4. Forge Knowledge, Project Memory, project-local Skills and Forge-wide Skills have separate trust and promotion lifecycles with provenance.
5. Assignment, output, failure, handoff and provenance are explicit; delegation/retries are bounded; verification is evidence-based.
6. Context is assembled selectively; secrets are runtime references, not ordinary context.

Keep flexible until evidence and owner decisions justify freezing:

- number/names of roles; whether one model serves multiple roles; routing and dynamic instantiation;
- Skill file/package format, registry/storage technology, retrieval/ranking and external distribution;
- status names and evaluation thresholds; exact review/approval authority;
- sandbox vendor, local/remote topology, MCP adoption and specific Tool APIs;
- UI layout, parallelism limits, budgets and risk tiers;
- Knowledge/Memory persistence, retention and deletion implementation.

Do not freeze database schemas, APIs or implementation architecture in this conceptual stage.

---

# Forge AI — Stage 1.6: система Agents и Skills

**Статус:** КОНЦЕПТУАЛЬНАЯ АРХИТЕКТУРА. Существующие положения помечены **CONFIRMED**; предлагаемые границы остаются **PROPOSED** до утверждения владельцем. Документ не описывает уже реализованную функциональность.

## CONCEPT MODEL

Ключевое разграничение:

> **Agent ≠ Skill ≠ Capability ≠ Knowledge ≠ Tool ≠ Project Memory.**

| Понятие | Определение | На какой вопрос отвечает | Пример |
| --- | --- | --- | --- |
| **Agent** | Исполнитель с ограниченной ролью, создаваемый для конкретной задачи/run. Применяет суждение и возвращает результат в рамках контракта. | Кто выполняет работу? | Architecture Agent |
| **Skill** | Версионируемая повторно используемая процедура для класса работ. Направляет выполнение, но не может самостоятельно расширять scope или permissions. | Как выполнять этот класс работ? | Legacy Analysis |
| **Capability** | Техническая возможность runtime при выполнении prerequisites и авторизации. | Что система технически может делать? | Browser access |
| **Tool** | Конкретный исполняемый или читающий интерфейс, использующий одну или несколько capabilities. | Какую операцию можно вызвать? | Открыть страницу; запустить ограниченную команду |
| **Knowledge** | Проверенный факт, pattern, ограничение или урок с provenance и условиями применимости. | Что Forge узнал или установил? | Подтверждённый pattern совместимости при миграции |
| **Project Memory** | Проектные, версионируемые сведения и история: requirements, decisions, evidence, approvals, conventions и outcomes. | Что известно или происходило в этом проекте? | Утверждённое архитектурное решение |

**CONFIRMED:** Technical Specification уже отделяет Project Memory от Forge Knowledge и указывает, что Project Memory не публикует сведения в Forge Knowledge автоматически. Также отделены роль агента и provider; результат модели не является авторизацией. Stage 1.6 расширяет эти границы, а не заменяет их.

**PROPOSED:** для каждой ограниченной задачи Forge выбирает совместимую композицию:

```text
Task + approved project context
  -> Agent role
  -> candidate Skills -> applicability / conflict check -> selected Skills
  -> required Capabilities -> permission check -> permitted Tools
  -> minimal context assembly -> bounded execution
  -> result + artifacts + evidence + provenance + status
```

Ни один кандидатный компонент не может создавать разрешения. Каждый слой вправе сузить работу; ни один не должен незаметно расширять утверждённую цель, scope или авторизацию.

## AGENT MODEL

### Идентичность и жизненный цикл

**PROPOSED:** отделить определение Agent (версионируемый контракт роли) от экземпляра Agent (одно назначение в рамках Run). Определения ролей могут быть повторно используемыми и постоянными; экземпляры создаются для ограниченных задач, затем завершаются, приостанавливаются, завершаются с ошибкой или отменяются. Роли не требуется иметь собственную модель или provider. Политика Dispatcher/provider выбирает совместимый provider для задачи; Agent остаётся provider-neutral.

Кандидатные роли: Discovery Agent, Research Agent, Requirements Analyst, Architecture Agent, Capability Analyst, Planner, Implementer, Code Reviewer, Security Reviewer, Test Engineer, Documentation Agent, Migration Analyst и Release/Deployment Reviewer. Это каталог возможных ролей, а не требование создавать отдельного агента для каждой метки. Несколько ролей могут сначала использовать одну реализацию или модель.

### Контракт Agent

Каждое определение Agent должно объявлять:

- цель, допустимые фазы проекта/классы задач, владельца/источник и версию;
- принимаемые задачу/цель, scope, ограничения, acceptance criteria и требуемый контекст;
- ожидаемую структуру результата и требования к evidence;
- доступные Skills и обязательные/необязательные Capabilities;
- разрешённые и запрещённые Tools/actions и permissions, которые он может запросить, но не выдавать;
- ограничения модели/provider, если они есть, как предпочтения или условия совместимости, а не логику SDK конкретного provider;
- поведение при timeout, отмене, checkpoint и ошибке;
- политику делегирования: может ли запросить handoff; только Orchestrator разрешает и планирует его;
- проверочные/evaluation cases и статус (candidate, reviewed, enabled, deprecated, blocked).

Agent получает только относящиеся к задаче requirements, evidence, decisions, утверждённый context, Skills и Tools, разрешённые для этого назначения. Он возвращает результат, ссылки на artifacts, evidence, proposals, наблюдения verification, status, структурированные ошибки, provenance и необходимые follow-up. Он не должен незаметно менять утверждённые Requirements, Specification, Architecture, Plan, permissions или scope. Предложения об изменениях проходят установленный процесс изменений/approval.

**PROPOSED status contract:** `PENDING`, `READY`, `RUNNING`, `WAITING_FOR_USER`, `WAITING_FOR_EXTERNAL_EVENT`, `BLOCKED`, `RECOVERABLE`, `FAILED`, `COMPLETED`, `CANCELLED`. Они описывают состояние run/назначения, а не approval или качество. Retry/recovery ограничены и фиксируются. Сбой одного назначения изолируется; корректные частичные artifacts сохраняются без отметки об успешном завершении всей задачи.

### Оркестрация и делегирование

**PROPOSED:** Orchestrator отвечает за назначения, совместимость, расписание, checkpoints, authorization gates, retries, отмену, handoffs и агрегацию результатов. Agent может предложить другую роль или сообщить об отсутствующем Skill/Capability, но не может запускать произвольных агентов, менять свои permissions или авторизовать другого Agent. Orchestrator проверяет каждое новое назначение по тем же ограничениям scope и permissions.

По умолчанию специалисты передают работу последовательно. Например: Architecture Agent использует Skills Legacy Analysis и Architecture Decision, затем передаёт Planner утверждённые ограничения, ссылки на evidence и открытые вопросы. Следующий Agent получает структурированный handoff, а не весь предыдущий разговор.

## SKILL MODEL

**Skill** — повторно используемая ограниченная процедура, а не Agent, tool, permission, требование проекта или авторитетный источник фактов. Skill объясняет уполномоченному Agent, как выполнить работу, но сам не выполняется, не утверждает действия, не расширяет scope, не выдаёт доступ и не меняет решения проекта.

### Manifest Skill и постепенное раскрытие

**PROPOSED:** manifest Skill содержит только метаданные, нужные для поиска и проверки допустимости:

- стабильный ID, имя, описание, версия, источник/владелец, provenance и статус жизненного цикла;
- цель, применимость к задачам/фазам проекта и явные условия неприменимости;
- prerequisites, dependencies, conflicts и совместимые типы проектов/runtimes;
- необходимые Capabilities, запрашиваемые Tools и объявленные permissions;
- требования к input/output, класс риска и side effects;
- состояние validation/evaluation, совместимые версии, supersedes/replaced-by и история использования.

Загружать содержимое постепенно:

1. **Manifest:** искать и фильтровать кандидатов, не добавляя полные инструкции в context.
2. **Подробные инструкции:** загружать только после проверок применимости, доверия, совместимости и permissions.
3. **Ресурсы:** извлекать только нужные текущей задаче templates, примеры, стандарты и supporting documents.
4. **Scripts/tools:** считать исполняемое содержимое кодом/зависимостями; отдельно проверять, валидировать, разрешать и запускать в sandbox. Skill не может передавать исполняемые полномочия через текст.

### Источники и статусы

**PROPOSED классы источников:** встроенные Forge; локальные для проекта; внешние, утверждённые владельцем; экспериментальные. Внешний источник не является доверенным по умолчанию. Project-local Skills действуют только в проекте, если их отдельно не обобщили и не утвердили.

**PROPOSED lifecycle:**

```text
OBSERVED PATTERN
  -> CANDIDATE SKILL
  -> GENERALIZATION + PROVENANCE
  -> TEST CASES + EVALUATION
  -> SECURITY / SCOPE REVIEW
  -> OWNER OR DELEGATED GOVERNANCE APPROVAL
  -> VERSIONED, ENABLED SKILL
  -> MONITORING / REVALIDATION
  -> REVISED, DEPRECATED, BLOCKED, OR RETIRED
```

Предлагаемые статусы: `UNKNOWN`, `EXPERIMENTAL`, `CANDIDATE`, `REVIEWED`, `VERIFIED`, `TRUSTED`, `DEPRECATED`, `BLOCKED`. Статус описывает evidence/уровень доверия, но не permissions. Владелец может установить более строгие правила продвижения. **DECISION REQUIRED:** кто может утверждать Skills уровня Forge и могут ли какие-либо встроенные Skills включаться по умолчанию.

Breaking changes требуют новой версии и явной совместимости; активные Runs сохраняют использованную версию. Disable/revoke запрещает новое использование и отражается в provenance, но не удаляет прежние записи. Dependencies версионируются, проверяются на циклы, несовместимые ограничения и статус до композиции.

## SKILL REGISTRY И DISCOVERY

**PROPOSED:** Skill Registry хранит или индексирует manifests, lifecycle metadata и provenance; это не дамп prompts и не вторая Knowledge Store. Сначала поиск фильтрует метаданные, затем полное содержимое загружается только у правдоподобных кандидатов.

Детерминированный flow поиска/композиции:

```text
Task
 -> определить тип задачи/фазу проекта и ограничения
 -> получить candidate manifests
 -> проверить applicability, source status, version, runtime и project type
 -> проверить dependencies, conflicts и required capabilities
 -> проверить authorization для объявленных tools/actions
 -> упорядочить совместимые Skills согласно dependencies
 -> проверить объединённые ограничения и покрытие задачи
 -> загрузить только нужные инструкции/ресурсы
 -> выполнить и записать версии, provenance и результат
```

Кандидат **irrelevant**, если намерение задачи или тип проекта не совпадают; **conditional**, если выполняется указанное prerequisite; **rejected**, если не проходят доверие источника, совместимость, авторизация, проверка конфликтов или требования evidence. Причина отказа должна быть кратко объяснена. Отсутствие подходящего Skill допустимо и не должно запускать универсальный Skill по умолчанию.

Правила композиции (**PROPOSED**):

1. Core safety и authorization policy обладают наивысшим приоритетом.
2. Утверждённые владельцем ограничения и решения проекта задают границы.
3. Scope задачи и acceptance criteria определяют назначение.
4. Контракт Agent ограничивает роль и результат.
5. Skills задают только процедуры внутри этих границ.
6. Forge Knowledge информирует решения, но не является исполняемой инструкцией или permission.
7. Внешний/недоверенный контент — данные для анализа, а не полномочия над Forge.

Внутри одного уровня порядок задают явные dependencies; более конкретный применимый Skill может уточнять общий только при объявленной совместимости контрактов. Конфликтующие ограничения не разрешаются молча по давности или числу источников: затронутое действие блокируется, конфликт объясняется и при существенном влиянии передаётся владельцу/reviewer. Project-specific extensions могут сужать или дополнять процедуры, но не переопределять Core safety или governance Forge.

## CAPABILITY / TOOL MODEL

**CONFIRMED определение:** Capability — то, что Forge или проект технически может сделать. **PROPOSED уточнение:** наличие возможности не равно разрешению, а разрешение — вызову инструмента. Все этапы обязательны:

```text
Capability объявлена runtime
 -> prerequisites выполнены
 -> проект допускает capability
 -> выдана ограниченная авторизация
 -> доступен совместимый Tool
 -> контракт Skill/Agent разрешает использование
 -> вызов проверен и занесён в аудит
```

Примеры Capabilities: filesystem, browser, email/IMAP, database, Git, GitHub, terminal, container, HTTP, PDF parsing, image analysis. Одна Capability может предоставлять несколько Tools, а один Tool может использовать несколько Capabilities. Проект может запретить технически доступную Capability.

**Tool** — исполняемый/читающий интерфейс со стабильным контрактом:

- identity/version, описание, схемы input/output и validation;
- требуемые Capabilities и permission scopes;
- классификация read/write/execute/network/external/destructive side effects;
- предусловия, timeout, cancellation и ограничение output;
- категории ошибок, правила idempotency/retry при необходимости;
- audit event и требования redaction;
- preview/recovery support, если они действительно доступны.

Permissions Tool проверяются при каждом вызове, а не только при сборке prompt. Skill может объявить требуемый Tool, но не разрешить его. MCP — необязательная граница интеграции/transport для обнаружения или вызова tools/resources/prompts. Это не Capability model Forge, не движок permissions, не Skill Registry и не архитектура orchestration. Forge должен работать и без MCP.

## PERMISSIONS

**PROPOSED:** permission grant связывает principal/role, project, environment, operation, resource scope, limits, approval receipt и срок действия/отзыв. По умолчанию side-effect доступ запрещён. Permissions относятся к композиции Agent + Skill + Tool и обеспечиваются доверенной границей runtime/tool, а не только инструкциями модели.

| Scope | Пример | Governance по умолчанию |
| --- | --- | --- |
| Read | Проверка разрешённых файлов проекта или публичной страницы | Ограничить paths/data sources; внешний контент остаётся данными |
| Write | Изменение файлов в workspace проекта | Конкретный workspace/branch; показать diff; проверить результат |
| Execute | Запуск build/test/команды | Изолированная среда и разрешённая цель; timeout/resource limits |
| Network | Доступ к доменам/сервисам | Запрет или ограничение по среде/цели; HTTP capability не означает широкий доступ |
| Secrets | Использование именованной secret reference | Без значения в context/logs; ограниченная передача только для разрешённой операции |
| External system | GitHub, email, database, cloud API | Именованный connector/account/resource и ограниченные операции |
| Forge internals | Изменение Core, policies, registries или глобальных Skills | Отдельная high-impact authorization; разрешения проекта недостаточно |
| Destructive / production | Удаление, миграция, публикация, deployment, изменение production | Preview, описание восстановления, точный approval; эффект может быть необратимым |

Approval требуется для high-impact, irreversible, credential-bearing, externally visible, production, destructive или существенно меняющих scope операций. Точные пороги и перечень действий, которые можно выполнять автономно с ограничениями, остаются **DECISION REQUIRED**. Approval привязан к проверенному действию, scope, версии project context/artifact и side effects; существенное изменение его аннулирует. Отзыв вступает в силу до следующего вызова tool. Положительный ответ не создаёт глобального разрешения.

## CONTEXT

**PROPOSED сборка context:**

```text
Task
 -> релевантные Requirements
 -> релевантные Evidence
 -> применимые Decisions
 -> применимые Knowledge
 -> выбранные Skill manifests/instructions/resources
 -> контракт Agent
 -> описания разрешённых Tools/Capabilities
 -> необходимые project artifacts
 -> минимально достаточный context
```

Не загружать по умолчанию всю Project Memory, Forge Knowledge, Skills, Tools или репозитории. Выбирать по задаче, фазе проекта, paths/artifact IDs, применимости и permissions; сообщать существенные неизвестные вместо выдумывания фактов.

У каждого полученного элемента есть provenance: source/creator, scope проекта или глобальный, версия/время, evidence/decision links, применимость, trust/status и supersession. Context summaries и handoffs сохраняют ссылки на авторитетные artifacts; summary не может незаметно подменять утверждённое решение. Secrets представлены ссылками и разрешаются только при авторизованном исполнении; они исключены из обычного context, logs и memory.

## MULTI-AGENT

**PROPOSED:** предпочитать одного подходящего Agent для одной ограниченной задачи; добавлять роли, только если они дают независимую экспертизу, assurance или необходимое разделение обязанностей.

- **Последовательно:** по умолчанию при зависимостях, например Research → Requirements → Architecture → Plan → Implement → Review.
- **Параллельно:** только для независимых read/analysis задач или изолированных изменений с явными dependencies, лимитами ресурсов и шагом объединения/согласования. Не запускать параллельные задачи, если одна меняет допущения, нужные другой.
- **Review loop:** сохранить существующее ограниченное поведение revision; это политика workflow, а не Skill, и она не может разрешать дополнительные изменения.
- **Независимый анализ/debate:** отдельно хранить утверждения, источники, допущения и предложенные действия; Orchestrator/reviewer сравнивает evidence и существенные альтернативы.
- **Иерархическое делегирование:** Orchestrator управляет дочерними назначениями, scope, permissions, budget и stop conditions. Agent может запросить делегирование, но не разрешать его самостоятельно.

Агрегация сохраняет вклад каждого участника, отсутствующие evidence, пересечения, разногласия и нерешённые вопросы. Не выбирать решение автоматически большинством. Сравнивать authority источников, воспроизводимость, покрытие требований, соответствие роли/skill, риск и основания confidence. Существенные разногласия о scope, safety, architecture или внешних последствиях передавать на review/владельцу. Частичные результаты остаются помеченными как частичные.

Retries ограничены и имеют код причины. Повторять только когда сбой похож на временный и операция безопасна/idempotent; при неопределённом внешнем side effect сначала проверить состояние или передать управление человеку. Provider fallback не означает, что задачу/tool action безопасно повторять. Отозванное permission запрещает новые вызовы и переводит run в blocked/waiting; отмена и recovery сохраняют audit trail.

## EVALUATION

Готовность Agent/Skill проверяется версионируемыми cases до продвижения и после существенных изменений. Возможные dimensions: correctness, requirement coverage, acceptance evidence, reliability, security, cost, latency, reproducibility и regression behavior. Результаты применимы к проверенным задачам/models/runtimes и не являются универсальной гарантией.

**PROPOSED evaluation assets:**

- golden tasks/projects подходящих классов, включая неоднозначные и adversarial inputs;
- ожидаемые результаты, обязательные evidence, запрещённые действия и acceptance criteria;
- тесты applicability, исключений, dependencies, разрешения конфликтов и permission boundaries Skill;
- contract tests Agent для формы результата, failure handling, handoff и сохранения scope;
- повторные/regression runs для недетерминированного поведения модели с фиксацией provider/runtime/version;
- security cases для prompt injection, утечки secrets, неправильного использования tools, отзыва permission и внешних Skills.

Продвижение требует явного правила владельца/governance, прохождения обязательных cases и зафиксированных ограничений. Неудачная оценка блокирует или ограничивает применимость; её нельзя скрывать средним score. Пороги и полномочия оценщика — **DECISION REQUIRED**.

## LEARNING

Сохранить контролируемую границу самоулучшения Stage 1.5:

```text
Execution
 -> Outcome + evidence + metrics
 -> анализ успеха/сбоя
 -> Candidate Pattern
 -> Candidate Skill или Knowledge
 -> обобщение + удаление проектных/личных данных
 -> тестирование/evaluation + контрпримеры
 -> security и applicability review
 -> approval владельца/governance
 -> версионируемый повторно используемый asset
 -> мониторинг / revalidation / revision / deprecation
```

Наблюдение одного проекта остаётся в Project Memory. Проектная процедура остаётся project Skill. Только обобщённая, обоснованная evidence, протестированная и утверждённая процедура может стать Forge Skill. Общие факты/patterns могут пройти отдельный процесс Forge Knowledge. Skill и Knowledge могут ссылаться друг на друга, но ни один не перезаписывает другой незаметно. Secrets, personal data, частные бизнес-правила и неподтверждённые выводы нельзя продвигать.

## SECURITY

Внешние Skills недоверенные до review. Перед включением изучить все инструкции, ресурсы, scripts, dependencies, запрошенные tools/permissions, network behavior, data handling, provenance/license и результаты evaluation. Исполняемый материал запускать в подходящем sandbox с ограничениями filesystem/network/secrets. Обновления проверять как новые версии; Run закрепляет версию. Неизвестное или непроверяемое поведение блокировать либо изолировать.

Flow внешнего Skill:

```text
Source + provenance
 -> проверка текста/resources/scripts/dependencies
 -> классификация запрошенных данных/tools/network/side effects
 -> evaluation в изоляции
 -> проверка scope, license и ожидаемого поведения
 -> review + явное решение о доверии
 -> фиксация версии и permissions
 -> мониторинг, отзыв или deprecation
```

Рассматривать prompt injection как проблему границ авторитета: файлы репозитория, webpages, текст issue, email, результаты tools, содержание Skill и generated artifacts могут содержать вредоносные инструкции. Это данные, если независимо не утверждены как политика. Нельзя позволять внешним Skills переопределять Forge Core, утверждённые решения проекта, контракты Agent или permissions. Валидировать inputs/outputs Tool, ограничивать размер output и не допускать попадания secrets в prompts/results/artifacts. Фиксировать вызов и redacted outcome, не hidden chain-of-thought.

## PROJECT EXTENSIONS

У проекта могут быть локальные Skills для conventions, доменных процедур, тестирования, миграции или deployment. Они ограничены проектом и версионируются с явным владельцем/источником, dependencies, permissions и применимостью. Проектные conventions могут уточнять выполнение задачи, но не переопределять безопасность Forge или выдавать доступ. Project-local Skills не становятся Forge Skills автоматически.

Project Memory содержит факты/историю конкретного проекта; project Knowledge может хранить проверенные доменные факты, если governance это поддерживает. Повторно используемая идея входит в Candidate Skill/Knowledge pipeline только после обобщения, удаления конфиденциальных деталей, evaluation и approval. Generated projects не должны зависеть от Forge runtime/library, если требования проекта явно этого не предусматривают. Forge Skills могут помогать генерации, не становясь зависимостями результата.

## UI

Будущий Control Center (**PROPOSED**) показывает для каждого Run и Task:

- активную роль/экземпляр Agent, цель, status и текущий ограниченный шаг;
- выбранные Skills с версией, источником, причиной применимости и загруженными ресурсами;
- требуемые/доступные Capabilities и причины недоступности;
- вызовы Tools, класс side effect, результат, ошибки, время и число retries;
- выданные/запрошенные/отклонённые/отозванные permissions и pending approvals с точным scope;
- artifacts/diffs, evidence и provenance, статус verification и нерешённые конфликты;
- checkpoint, причину ожидания/блокировки, pause/resume/cancel/takeover;
- частичный/итоговый результат и последующие действия.

Использовать постепенное раскрытие: сначала краткая сводка и действия для владельца, затем evidence и технические traces по запросу. Не показывать chain-of-thought, реальные credentials или непроверенные инструкции внешнего Skill как доверенные правила Forge.

## MUST / SHOULD / OPTIONAL / FUTURE / REJECT

Это рекомендации для владельца, а не утверждённые обязательства.

### MUST HAVE до реализации автономных действий в проектах

- Раздельные контракты и идентичность Agent, Skill, Capability, Tool, Knowledge и Project Memory.
- Least-privilege authorization, исполняемая runtime для каждого вызова Agent + Skill + Tool; permissions и approval не выводятся из prompt.
- Проверка доверия, закрепление версии и sandbox policy для исполняемых/внешних Skills и Tools.
- Provenance и проверяемые статусы/результаты назначений, context, Skills, Tools, approvals и artifacts; без записи chain-of-thought.
- Ограниченные retries/delegation и безопасное поведение при паузе/отзыве; durable checkpoints до долгого исполнения.
- Проверка соответствия утверждённому scope и acceptance evidence до объявления результата завершённым.

### SHOULD HAVE

- Skill Registry на основе метаданных с progressive disclosure и детерминированными проверками applicability/composition.
- Повторно используемые Agent contracts, структурированные handoffs, project-specific Skills и небольшой evaluation/golden-task набор.
- Представления Control Center для run state, выбранных assets, permissions, approvals, provenance и recovery.
- Отдельные governance циклы candidate-to-approved для Forge Skills и Forge Knowledge.

### OPTIONAL

- Внешние Skill repositories/marketplaces, MCP connectors, параллельный specialist review, динамическое создание ролей, рекомендации Skills и project health views. Включать при наличии конкретной пользы, permissions и evaluation.

### FUTURE

- Широкая plugin ecosystem, автономные предложения по генерации/продвижению Skills, межпроектное обучение и адаптивная оркестрация. Это зависит от durable runtime, trust, evaluation и утверждённого governance владельца.

### REJECT / NOT APPLICABLE

- Один универсальный Agent или Skill на все задачи; максимизация количества агентов.
- Загрузка каждого Skill, Tool, репозитория и Knowledge item для каждой задачи.
- Skills, скрывающие requirements, меняющие scope или выдающие permissions.
- Включение каждой Capability по умолчанию или незаметное повышение permissions.
- Автоматическое продвижение разовых наблюдений или проектной логики в глобальные assets Forge.
- Неограниченное создание агентов/retries, majority vote без evidence или невидимые внешние Skills.
- Проектная логика в Forge Core; незаметное изменение requirements агентом.
- Обязательный один язык/runtime или обязательная зависимость generated projects от Forge.

## DECISION REQUIRED

Открытые решения владельца/governance:

1. Кто владеет, проверяет и утверждает встроенные, внешние и глобальные Skills Forge? Можно ли делегировать approval и кому?
2. Какие Skills (если такие есть) включаются по умолчанию, и какие минимальные проверки означают `VERIFIED` или `TRUSTED`?
3. Могут ли Agents запрашивать расширение доступа во время Run или должны ждать нового ограниченного approval? Какие permissions всегда требуют владельца?
4. Какие actions/tools запрещены, всегда требуют approval либо разрешены по ограниченной политике? Определить уровни риска и различия сред.
5. Какова первая trust boundary для Skills с scripts/network: local sandbox, remote sandbox или пока без исполняемых Skills?
6. Какая политика versioning/compatibility применяется к Skills активных или возобновлённых Runs? Как быстро отзывается скомпрометированный Skill?
7. Кто разрешает конфликты owner project instructions, approved requirements, Skills и внешних evidence; какие источники авторитетны для разных типов утверждений?
8. Какое evaluation evidence и пороги нужны для promotion Agents/Skills? Кто вправе разрешить продолжение после неуспешной оценки?
9. Какие Run/tool/artifact/Skill usage history хранить, показывать, экспортировать или удалять?
10. Может ли один Agent напрямую вызывать другого через Orchestrator request? Какие пределы fan-out, concurrency, cost и глубины?
11. Какие изменения Forge Core или governance требуют отдельного owner approval и процедур выпуска?

Документ не отвечает на эти вопросы за владельца.

## CONFLICTS

- Существующий §9 `TECHNICAL_SPECIFICATION.md` перечисляет потенциальные role-based agents и уточняет, что role не provider; Stage 1.6 формализует контракт, не объявляя роли реализованными.
- Существующий Planner создаёт статические планы и не исполняет задачи, не обновляет статусы. Agent contracts и run statuses здесь — целевая модель, не текущее поведение Planner.
- Существующее multi-agent исполнение — primary + reviewer с одной revision. Предлагаемое общее делегирование не увеличивает этот реализованный лимит.
- Technical Specification разделяет Forge Knowledge и Project Memory. Предложение сохраняет разделение и добавляет Skill как процедурный asset, а не объединяет их.
- Примеры Tool/Capability — целевые концепции. Текущие вызовы provider не означают, что в Forge существуют filesystem, terminal, browser или внешние сервисные tools.
- Stage 1.5 предлагает durable runs, sandbox, scoped permissions, event provenance и review внешних Skills. Stage 1.6 зависит от этих границ, но не выбирает database, sandbox vendor, UI, зависимость от MCP или окончательную политику permissions.
- Открытые вопросы approvals/lifecycle Stage 1.4 остаются открытыми. Их решение здесь не предполагается.

## DOCUMENTATION IMPACT

| Документ | Влияние |
| --- | --- |
| `docs/STAGE_1_6_AGENT_SKILL_SYSTEM.md` | Эта концептуальная модель и governance предложение на английском и русском. |
| `docs/TECHNICAL_SPECIFICATION.md` | **Рекомендуется после review владельцем:** включить принятые стабильные границы в §§9, 13–16 и будущую capability matrix. Подробные lifecycle/status, registry и governance policy оставить здесь или в связанной спецификации только при утверждении. |
| `docs/STAGE_1_5_EXTERNAL_BENCHMARK.md` | Без изменений; Stage 1.6 уточняет benchmark предложения об agent/skill/tool. |
| `docs/ARCHITECTURE.md` | Без изменений: описывает текущую реализацию; Agent/Skill runtime не реализован. |
| `docs/ROADMAP.md` | Без изменений до приоритизации и определения порядка владельцем. |
| `docs/DECISIONS.md` | Без изменений: Stage 1.6 содержит предложения и открытые вопросы, а не принятые решения. |
| `docs/FORGE_VISION.md` | Без изменений; существующее направление продукта совместимо. |

## NEXT STAGE

**Рекомендуется:** Stage 1.7 — Agent/Skill Governance and Permission Policy Decisions. Утвердить полномочия approval, критерии trust/status, расширение permissions, границы внешних/исполняемых Skills, пороги evaluation и закрепление версии для активных Runs. Затем внести в canonical Technical Specification только принятые стабильные контракты. Отложить реализацию и выбор технологий, пока эти границы и модель durable Run из Stage 1.5 не утверждены.

## ВАЖНЫЙ ИТОГОВЫЙ ВОПРОС — ОСНОВА ДО РЕАЛИЗАЦИИ?

**Рекомендация: ДА, концептуальные границы Agent + Skill должны стать основой до реализации общего Agent/Skill runtime.** Они не дают смешать роли, процедуры, инструменты, знания и permissions в неуправляемый prompt bundle. Это рекомендация, а не утверждённый владельцем план реализации.

Зафиксировать сейчас на уровне концептуальных контрактов:

1. Agent — ограниченная роль/исполнитель; Skill — повторно используемая процедура; Capability — техническая возможность; Tool — вызов; Knowledge — проверенный факт/pattern; Project Memory — проектная история.
2. Ни Agent, ни Skill не выдают permissions; authorization проверяется при вызове Tool и привязана к scope.
3. Skills не меняют requirements, architecture, утверждённый scope или governance; внешний контент по умолчанию не обладает authority инструкций.
4. Forge Knowledge, Project Memory, project-local Skills и Forge-wide Skills имеют отдельные trust/promotion lifecycles с provenance.
5. Assignment, output, failure, handoff и provenance явны; delegation/retries ограничены; verification основана на evidence.
6. Context собирается избирательно; secrets — runtime references, а не обычный context.

Оставить гибкими до появления evidence и решения владельца:

- количество/названия ролей; использует ли одна модель несколько ролей; routing и dynamic instantiation;
- формат файлов/packages Skills, технология Registry/storage, retrieval/ranking и внешнее распространение;
- названия статусов и пороги evaluation; полномочия review/approval;
- sandbox vendor, local/remote топология, применение MCP и конкретные Tool APIs;
- UI layout, limits параллельности, budgets и risk tiers;
- реализация хранения, retention и удаления Knowledge/Memory.

Не фиксировать схемы баз данных, APIs и реализационную архитектуру на этом концептуальном этапе.
