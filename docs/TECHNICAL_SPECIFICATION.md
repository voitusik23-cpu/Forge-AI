# Forge AI — Technical Specification v1.0

This document defines the long-term target architecture and constraints for
Forge AI. It distinguishes that target from the implementation currently in
this repository. The specification is architectural guidance, not a claim that
all described capabilities exist today.

## 1. Product purpose

Forge AI is an AI orchestration platform designed to let a user describe a
project or task in natural language and have Forge coordinate specialized AI
agents, tools, project state, review, testing, and controlled changes.

Forge itself is **not an AI model**. It is the orchestration layer between the
user, AI models, agents, tools, projects, Git, and external services. Its role
is to coordinate work while keeping project scope, credentials, and sensitive
actions under user control.

## 2. Current implementation and target architecture

### Current implementation

> Verified status, per-area evidence, and known limitations are maintained in
> [`SOURCE_OF_TRUTH.md`](SOURCE_OF_TRUTH.md). That file is the authoritative
> index of what exists today; this section is a summary of it.

- A Python application with provider-neutral `Task`, `TaskResult`, agent, and
  provider interfaces.
- An in-memory runtime assembled by `create_runtime`; startup does not call AI
  providers.
- A deterministic Planner that selects one of four templates and returns a
  static plan with sequential dependencies. It does not execute that plan.
- Deterministic task classification and Dispatcher routing using provider
  registration, configuration, capabilities, key availability, cost tiers,
  explicit selection, and a finite fallback policy.
- Provider integrations for OpenAI, Anthropic, Google Gemini, DeepSeek,
  OpenRouter, and Groq, plus an offline Mock provider. xAI is registered as an
  unconfigured placeholder.
- A primary execution and reviewer flow with at most one revision after
  `CHANGES_REQUESTED`, followed by a final review.
- A local `SecretStore` that resolves credentials on demand, checking process
  environment values before the ignored root `.env` file.
- A Decision layer producing advisory control-flow recommendations with no
  execution authority, a Decision Context Envelope, and a bounded Agent Harness
  implementing the explicit
  `OBSERVE -> CONTEXT -> DECIDE -> VALIDATE -> AUTHORIZE -> ACT -> OBSERVE_RESULT -> UPDATE`
  loop with at most one authoritative action per iteration.
- An Agent Skill System (registry, deterministic evaluator, provenance, trust
  constraints) and a multi-skill agent run.
- A Project Execution Profile and Execution Plane: `ExecutionRequest`,
  `ExecutionResult`, `ProjectExecutionProfile`, `ExecutionPolicy`, an ephemeral
  local workspace manager, and a local process adapter.
- An Execution Authorization Contract v0.2: immutable `ExecutionIntent` with a
  canonical fingerprint, canonical path security for program identity and
  workspace-relative paths, a default-deny Permission boundary, an
  intent-bound single-use Approval boundary, capability-classified invocation
  policy, a mandatory explicit `workspace_root`, and an `AuthorizedExecution`
  marker that the local adapter requires before spawning any process.
- Project Memory (models, store, validator) and Knowledge Governance
  (candidate/review/approval models, store, validator).
- A verification contract with structured evidence, a test verification
  adapter, and an acceptance gate with requirement traceability.
- A project-state contract, a run event/trace contract, change sets, project
  snapshots, and an integration failure matrix.
- Unit tests, real-provider smoke commands, and Git repository history managed
  by developers. The application itself does not currently manage Git changes.

There is no Forge API, Desktop or Web client, setup wizard, task queue, parallel
project execution, general-purpose tool runner, or automated project
Git/deployment integration in the current application. The execution plane has no
production entry point: `create_runtime` does not construct an execution
coordinator, an adapter, or a workspace root. There is no OS-level sandbox, and
none is claimed.

### Target architecture

The target system separates presentation clients, a stable API boundary, and a
provider-neutral Core. The diagram shows intended boundaries; it is not a
description of currently running services.

```text
                         USER
                           |
                           v
                  +------------------+
                  | Forge Desktop/Web|
                  +--------+---------+
                           |
                           v
                  +------------------+
                  |    Forge API     |
                  +--------+---------+
                           |
                           v
                  +------------------+
                  |    Forge Core    |
                  +--+-----+-----+---+
                     |     |     |
                     v     v     v
                 Planner Dispatcher Memory
                     \     |     /
                      \    |    /
                       v   v   v
                    Agent System
                  /      |       \
                 v       v        v
              Coder   Reviewer   Tester
                 \       |       /
                  v      v      v
                    Providers
                       |
                       v
                 Tools / Git
                       |
                       v
                    PROJECT
```

Responsibilities and boundaries:

- **Desktop/Web:** present project state, collect user input, and display
  plans, results, and proposed changes. They are clients and contain no Core
  orchestration or project business rules.
- **Forge API:** provide a stable, authenticated application boundary for
  clients and other authorized integrations. API transport must not become
  coupled to orchestration internals.
- **Forge Core:** own task and project workflow, policy enforcement, execution
  coordination, and the provider-neutral domain interfaces. Core must remain
  usable without either UI client.
- **Planner:** turn a goal into an inspectable plan and dependency structure.
- **Dispatcher:** select an eligible provider-backed agent according to task
  intent, capabilities, configuration, policy, and explicit user choice.
- **Memory:** retrieve project-scoped relevant facts and decisions; it must not
  indiscriminately load the repository into every task.
- **Agent System:** execute role-specific work through shared contracts.
  Agent roles are separate from model/provider implementations.
- **Providers:** adapt provider-specific SDKs and protocols to a common
  request/response contract.
- **Tools/Git:** expose narrowly scoped operations for project inspection,
  verification, and authorized changes. They must not bypass Core policy or
  user approval.

## 3. Mandatory architectural principles

1. **Core independence:** Forge Core must not depend on Desktop/Web UI.
2. **API boundary:** Desktop and Web communicate with Core through a stable
   API boundary.
3. **Provider independence:** Core must not depend on one AI provider.
4. **Agent independence:** agent roles must be separated from provider
   implementations.
5. **UI independence:** UI must not contain business logic belonging to Core.
6. **Secret isolation:** API keys, tokens, and passwords must never be embedded
   in source code.
7. **Controlled changes:** agents must not freely modify production systems.
8. **Human authorization:** destructive, deployment, or production-affecting
   actions require explicit authorization.
9. **Deterministic infrastructure where practical:** planning, routing, and
   validation should be deterministic where that provides value.
10. **Modular architecture:** components must remain replaceable.
11. **Context/token economy:** agents should receive only the context required
    for their task.
12. **Testability:** Core components should be testable without external AI
    providers whenever practical.
13. **No unnecessary coupling:** future UI, providers, and tools must not force
    changes throughout Core.

## 4. Intended user experience (future)

The intended first-run and project workflow is:

1. Install and launch Forge.
2. Complete a setup wizard and configure providers, workspace, and optional
   integrations.
3. Create or select a project and describe a goal in natural language.
4. Start a workflow and observe its plan, task progress, agent activity,
   reviews, and tests.
5. Inspect the proposed changes and authorize or reject controlled actions.
6. Open the resulting project and review its change summary.

For normal operation, a user should not need to understand Python, Git, APIs,
model routing, or agent orchestration. Advanced configuration may expose those
details without making them prerequisites for ordinary use.

## 5. Desktop application (future)

Desktop is a presentation client of Forge API. Its intended capabilities
include a project dashboard, project creation, goal/task input, status and task
queue views, agent activity, review and test results, change review, Git
status, provider settings, appropriate logs, and a setup wizard.

Desktop must not contain Forge orchestration logic or business rules. It must
use the same Core/API contracts as Web.

## 6. Web interface (future)

Web is another client of the same Forge API and Core contracts. Desktop and Web
may have different presentation and interaction patterns, but they must not
implement separate workflow or routing logic. This specification does not
select a frontend framework or design the actual frontend.

## 7. Setup wizard (future)

The first-run wizard may configure AI provider credentials and selection,
GitHub connection, Git identity, workspace/project directory, optional email,
external integrations, and tool availability. Every setting must be validated
and its purpose explained. Optional provider account email metadata remains
separate from API credentials.

Secrets must be stored through the existing `SecretStore` architecture or a
future secure secret backend. The wizard must not put secrets in source,
ordinary runtime settings, logs, plans, or task results. The current local
`.env` approach is a developer workflow, not a claim that a production secret
backend already exists.

## 8. Provider system

The provider layer is provider-neutral and extensible. The current factory
registers OpenAI, Anthropic, Google/Gemini, xAI, DeepSeek, OpenRouter, Groq,
and Mock. OpenAI, Anthropic, Google/Gemini, DeepSeek, OpenRouter, and Groq have
provider integrations; xAI remains an unconfigured placeholder, and Mock is
offline/test support.

Provider selection may consider task category, declared capabilities,
availability, cost tier, configuration, fallback policy, and explicit
user/provider selection. No provider is always best. Explicit user selection
has priority; automatic policy and fallback must be bounded and explainable.
Adding a provider must not require provider-specific branching throughout
Core.

The current cost tiers are coarse metadata, not calculated prices. Future
budget or cost policies must state their data source and uncertainty rather
than imply exact billing knowledge.

## 9. Agent system

Potential role-based agents include Planner, Architect, Coder, Analyst,
Reviewer, Tester, Researcher, Documentation agent, and Git/Release agent.

**Agent role is not an AI provider.** A role describes responsibility and may
use different providers under routing policy. No agent is automatically
authoritative. Review and test results are evidence for the user and workflow;
they do not themselves grant permission to change or release a project.

## 10. Intended project lifecycle

```text
User Goal
   -> Planning
   -> Task Decomposition
   -> Task Queue
   -> Dispatcher
   -> Agent Execution
   -> Review
   -> Controlled Revision
   -> Testing
   -> Final Review
   -> Change Summary
   -> User Authorization
   -> Git Commit
   -> Optional Deployment
```

**Current:** the Planner creates a static deterministic template plan;
Dispatcher classifies/routes individual tasks; the runtime can execute a
primary agent and reviewer, perform at most one revision after requested
changes, and return a final review status. Current tests validate Forge code;
the application does not automatically run a project's planned test task.

**Future:** persistent plans, decomposition and execution of dependent tasks,
task queues, parallel execution, role-specific tool use, project-level test
orchestration, change summaries, and authorized Git/deployment actions. Each
stage must report its status and preserve failures rather than implying
success.

## 11. Project Memory (future)

Project Memory may retain or index project structure, architecture decisions,
important files, previous tasks and reviews, project conventions, known issues,
and important constraints. Retrieval must be scoped to the project and task.
Memory must not mean blindly loading the entire repository for every request.
Stored facts should be inspectable, correctable, and deletable under clear
project-level controls. No persistent project memory is implemented today.

## 12. Git and version control

The intended workflow is:

```text
Agent changes -> Diff -> Tests -> Review -> User authorization where required
             -> Commit -> Optional push -> Optional deployment
```

Today, this repository uses Git for source history, but Forge application code
does not provide project Git operations or GitHub integration. In the target
system, Git actions must go through scoped tools and policy checks. There must
be no automatic force-push, destructive history rewrite, or production deploy.
Push and deployment are separate optional actions with explicit authorization
where required.

## 13. Safety and change control

Mandatory protections for current development and future execution:

- Do not expose secrets in source, logs, task context, or results.
- Do not make uncontrolled production changes.
- Do not run destructive operations without explicit authorization.
- Do not force-push or rewrite Git history without explicit authorization.
- Keep changes visible and reviewable; do not make hidden changes.
- Run relevant tests before committing where appropriate and report their
  actual results.
- Require explicit authorization for sensitive actions, including production
  impact, destructive changes, publishing, or deployment.

Tool permissions must be narrow, project-scoped, and checked at the point of
use. A model response is not authorization.

## 14. Context and cost management

The target strategy is to retrieve only relevant files, maintain a lightweight
project map, give role-specific context, avoid repeated full-repository scans,
choose the least expensive suitable provider, and escalate to stronger models
when justified. Parallel agents should be used only when their independent
work provides value. Repeated reviews without new evidence should be avoided.
Usage and cost estimates should be identified as estimates when they are not
actual provider billing data.

## 15. Extensibility

The target system should support new providers, agent roles, tools, project
types, UI clients, external integrations, and execution environments without
rewriting Core. Extensions should implement stable interfaces and declare
capabilities/configuration. Provider SDK types and UI-specific data must not
leak into shared workflow models.

## 16. Current and future capability matrix

| Area | Current | Future |
| --- | --- | --- |
| Provider Layer | Common interface; OpenAI, Anthropic, Gemini, DeepSeek, OpenRouter, Groq, Mock; xAI placeholder | Broader provider/model metadata and replaceable integrations |
| Dispatcher | Deterministic classification, capability/config checks, cost tiers, explicit selection, finite fallback | Policy growth for budget, quality, and latency with explainable decisions |
| Task Classification | Rule-based CODE, ANALYSIS, REVIEW, OTHER plus legacy routing categories | User-visible classification controls and broader project/task taxonomies |
| Planner | Deterministic templates; static plans with ordered dependencies | Goal decomposition, editable plans, and execution-aware planning |
| Multi-Agent Execution | Primary plus reviewer; at most one correction cycle | Policy-driven bounded roles and workflows |
| Review | Provider-neutral review result and APPROVED/CHANGES_REQUESTED/FAILED | Configurable review policy and richer evidence |
| Revision | One revision after CHANGES_REQUESTED, then final review | Carefully bounded correction policy with user controls |
| Project Memory | Not implemented | Project-scoped, inspectable memory and retrieval |
| Task Queue | Not implemented; plans are not dispatched as a queue | Persistent queue, dependency scheduling, and lifecycle state |
| Parallel Execution | Not implemented | Bounded parallel tasks with conflict management |
| Forge API | Not implemented | Stable client/API boundary with authentication and authorization |
| Desktop | Not implemented | Desktop client of Forge API |
| Web | Not implemented | Web client sharing Core/API contracts |
| Setup Wizard | Not implemented | Validated first-run provider, workspace, Git, and tool setup |
| Git workflow | Git tracks this source repository; no in-app project Git operations | Scoped diff/test/review/commit/push flow with approval gates |
| Deployment | Not implemented by the application | Optional, explicitly authorized deployment workflows |
| Project management | Static plan models only; no persistent project lifecycle | Project state, plans, dependencies, tasks, and outcomes managed end to end |

## 17. Relationship to other documentation

- `TECHNICAL_SPECIFICATION.md` defines target architecture and constraints.
- `ROADMAP.md` defines the staged implementation order.
- `ARCHITECTURE.md` describes current technical architecture and implemented
  behavior.
- `FORGE_VISION.md` describes the product vision and motivation.
- `AGENTS.md` defines development rules for AI agents.

These documents should agree on what exists and what remains future work. When
they differ, implementation and verified behavior determine the current state;
update the relevant documentation rather than presenting a target as shipped.

## 18. Non-goals for the current stage

Forge AI is not currently intended to be:

- An unrestricted autonomous coding agent.
- A replacement for human authorization.
- Permanently tied to one AI provider.
- Dependent on one IDE.
- A single monolithic application that combines UI and orchestration logic.
- An automatic production deployment system.

These boundaries do not prevent future capabilities; they require those
capabilities to be introduced through explicit, testable, and controlled
designs.

## 19. Specification maintenance

Keep this specification concise enough to maintain as architecture evolves.
Keep policy and boundaries here; place detailed implementation behavior in
source code and the current architecture document. Describe future work as
future work, and update this document when a material target constraint or
boundary changes.

## 20. Documentation governance

Forge technical and product documentation intended for both AI agents and the
human project owner should retain its canonical English content and include a
Russian translation after it where appropriate. Translations preserve meaning,
structure, numbering, and technical terminology; code, paths, commands,
identifiers, and other technical literals remain unchanged unless translation
is necessary for understanding.

When documented architecture, product, safety, workflow, or other rules
change, identify the affected documents, update only those documents, update
their Russian translations where present, and check consistency across the
documentation set. Do not mechanically revise every document after every
change. Apply the same governance to new permanent documentation.

The documentation hierarchy is:

- `AGENTS.md` — rules for AI development behavior.
- `FORGE_VISION.md` — product vision and long-term direction.
- `TECHNICAL_SPECIFICATION.md` — target technical architecture and mandatory
  constraints.
- `ARCHITECTURE.md` — current technical implementation architecture.
- `ROADMAP.md` — staged implementation order and project progress.
- `DECISIONS.md` — recorded architectural decisions and their rationale.

Keep current implementation distinct from target/future architecture. When
implementation changes, update current-state documentation and roadmap status
when appropriate. Change the technical specification only when target
architecture or constraints change, the vision only when product direction
changes, and decision records only when an architectural decision is made.
Derived descriptions must remain consistent with the authoritative document,
without unnecessary duplication.

## 21. Discovery intelligence and project context (future)

Discovery must maintain a project-scoped, versioned context rather than rely
on an unstructured conversation transcript. Its logical areas include product
goal, users and roles, workflows, inputs and outputs, external systems,
existing software and infrastructure, data, constraints, security, selected
capabilities, requirements, decisions, questions, evidence, assumptions,
unknowns, risks, acceptance criteria, architecture, plan, and change history.
The set of populated areas depends on the project; this list is not a required
schema for every generated product.

Current context is a derived working view. Material corrections create a new
`ProjectContextVersion`; prior versions remain in history with the actor,
reason, supporting evidence, and affected requirements, decisions, and
approvals. Historical records are not silently overwritten. Readiness and
understanding assessments are derived and must identify the context version
they evaluated.

### Independent understanding, evidence, and readiness

Assess understanding separately for Product, Workflow, Data, Integration,
Security, Runtime, Deployment, Users/Roles, and Acceptance, with additional
areas when a project requires them. A level is scoped to both an area and a
workflow stage: `SUFFICIENT for Brief` does not imply `SUFFICIENT for
Architecture`. Use `UNKNOWN`, `PARTIAL`, `SUFFICIENT`, and `CONFIRMED`; these
levels describe completeness, not evidence type or numeric confidence.

Evidence records keep assertion kind separate from source quality. Assertion
kinds are `FACT`, `SUPPORTED_INFERENCE`, `WEAK_INFERENCE`, `ASSUMPTION`,
`UNKNOWN`, and `CONFLICT`. Relevant metadata includes source/provenance,
author, observation time, source version where available, scope, freshness,
and authority for the assertion type. High confidence does not turn an
inference into a fact.

Readiness is a calculated assessment for one transition, not a permanent
project status. It reports required areas, accepted unknowns, blockers,
unresolved conflicts, stale decisions, and missing approvals. Its outcomes
are `BLOCKED`, `READY_WITH_OPEN_ITEMS`, and `READY`. Project-specific gates may
add requirements. A user confirmation of understanding is distinct from
approval of a decision, plan, or action.

| Transition | Required understanding | Unknowns that may remain |
| --- | --- | --- |
| Discovery -> Brief | Goal, users, and main workflows | Low-risk edge cases |
| Brief -> Requirements | Scope, outcomes, and constraints | Implementation details that do not change requirements |
| Requirements -> Specification | Critical scenarios, integrations, security, and data constraints | Low-risk technical details |
| Specification -> Architecture | Material NFRs, deployment assumptions, and system boundaries | Implementation details |
| Architecture -> Planning | Material architecture decisions and selected capabilities | Details of individual tasks |
| Planning -> Implementation | Acceptance criteria, blockers, and required approvals | Future enhancements |

This matrix is a baseline, not a universal gate configuration.

### Questions, decisions, and impact

A question is a decision-support record, not just prompt text. It identifies
the decision it informs, why the answer matters, known evidence, affected
objects, required stage, impact priority, blocking status, whether Forge may
decide safely, and whether deferral is acceptable. Do not ask a question when
its answer cannot change a requirement, acceptance criterion, security,
deployment, capability choice, or material behavior. Check relevant Project
Memory, evidence, source findings, approved documents, and applicable Forge
Knowledge before asking.

Question lifecycle (`OPEN`, `ANSWERED`, `RESOLVED`, `DEFERRED`, `REJECTED`),
impact priority (`BLOCKING`, `HIGH`, `MEDIUM`, `LOW`, `NON_CRITICAL`), approval,
and readiness are independent properties. Decision status
(`PROPOSED`, `AWAITING_APPROVAL`, `APPROVED`, `REJECTED`, `SUPERSEDED`) is also
independent from decision freshness (`VALID`, `POTENTIALLY_AFFECTED`, `STALE`,
`REQUIRES_REVALIDATION`). An approved decision may become stale after new
evidence.

Substantial new information triggers impact analysis across requirements,
decisions, architecture, capabilities, data, security, deployment, acceptance
criteria, tests, and plan. Decision dependencies form a graph. A changed
upstream decision marks dependent decisions for review; it does not delete or
rewrite them. Cycles are treated as conflicts until a resolution policy is
defined.

### Source authority and conflict resolution

There is no universal source ranking. Authority depends on the assertion:
owner intent is best established by an explicit user decision; existing
behavior by source or runtime evidence; API contracts by official
documentation/schema; and project business rules by an owner-approved
requirement. Freshness is considered separately. A newer source is not
automatically more authoritative.

When sources conflict, preserve both assertions and show their sources,
context, freshness, potential impact, and the decision required. Resolve
automatically only when an approved rule applies and the impact is low-risk;
otherwise request owner resolution. Never silently choose a source solely
because it is newer.

### Confirmation and authorization

Keep these user actions distinct and scoped to the reviewed version:

- **Confirm understanding:** confirms that Forge's summary reflects the
  user's intent; it does not authorize implementation or side effects.
- **Approve decision:** approves a specific decision and its stated scope.
- **Approve plan:** authorizes the reviewed plan, not unlisted work.
- **Approve action:** authorizes a specific external or irreversible action.

There is no implicit global permission from a general affirmative response.
Forge may make a technical decision independently only when it is reversible,
low-risk, preserves product meaning and security boundaries, does not materially
change deployment or cost, and creates no significant external commitment. Such
decisions retain rationale, evidence, alternatives where material, and a
revalidation path.

### Forge Knowledge governance (future)

Project Memory and Forge Knowledge are separate stores and trust domains.
Project Memory may contain project decisions, requirements, evidence,
approvals, project preferences, business logic references, and change history.
Forge Knowledge contains reviewed, abstract engineering patterns,
capabilities, anti-patterns, and verified lessons. Project Memory may retrieve
applicable Forge Knowledge; it does not publish to it automatically.

The knowledge lifecycle is:

```text
Observed Pattern -> Candidate Knowledge -> Evidence -> Review
 -> Approved Knowledge -> Applicability Rules -> Usage / Feedback
 -> Revalidation or Deprecation
```

Knowledge records identify origin, evidence, approval, version, applicable
and non-applicable conditions, exceptions, counterexamples, usage history, and
current status. Frequency alone does not make a capability mandatory. For
example, recurring proxy use may prompt a question for matching automation
projects; it does not require a proxy manager in every project.

Before approval, screen candidates for secrets, credentials, personal data,
project-specific business logic, accidental one-project rules, unsupported
inferences, missing counterexamples, and unclear applicability. Failures may
produce candidate anti-patterns, but a failure becomes global guidance only
after root-cause review and approval. Retrieved knowledge must be relevant,
versioned, and checked against its applicability conditions; do not load the
entire knowledge base into every task.

### Optional capabilities and unresolved governance

Mastery Creator is an optional candidate capability or runtime integration,
not a required layer for browser, UI, or generated projects. Its real
architecture and integration contract require a separate analysis before it
can be selected. Browser, proxy, multi-account, Telegram, IMAP, EVM, challenge
handling, language, and database capabilities are likewise selected from
requirements rather than assumed to be universal.

The following remain `DECISION REQUIRED`: approval gates at each project
transition; authority for approving global Forge Knowledge; default Project
Memory retention and deletion; source authority by assertion type; mandatory
audit records and retention period; handling of knowledge later disproved;
which artifacts receive full version history; and how readiness is presented
without a misleading percentage. No storage technology or local/cloud/hybrid
trust model is selected by this section.

### Stage 1.3 conceptual walkthroughs

These walkthroughs validate the model; they do not claim implemented behavior.

| Scenario | Expected Discovery behavior |
| --- | --- |
| Simple project with adequate requirements | Reuse known evidence, ask only decision-changing questions, and reach readiness with a concise Brief. |
| Site with unknown email verification | Mark verification behavior unknown; request allowed evidence or user clarification; do not invent the flow. |
| Legacy software conflicts with owner description | Preserve source finding and owner statement, show the conflict, and request resolution where it changes behavior. |
| Deployment changes after Architecture approval | Create a new context version, run impact analysis, mark dependent decisions potentially affected, and revalidate before replanning. |
| Common capability does not fit current project | Retrieve the pattern only if applicable; explain it as an option or ask a relevant question; do not add it automatically. |
| One project failure occurs once | Record project evidence and a candidate lesson; do not publish a global anti-pattern without review. |
| Critical external action is proposed | Present a scoped action proposal and require separate action approval; understanding or plan confirmation is insufficient. |

## 22. Project classification and capability selection (future)

Project classification describes the problem space; it is not a technology
choice and is not a closed enum. A project may have multiple relevant classes,
such as CLI/tool, API/backend, web or desktop application, browser automation,
bot, worker, scheduler, data processing, integration service, migration, or a
hybrid system. Classification informs Discovery depth and capability
investigation; it does not automatically select an implementation mechanism.

The proposed capability selection flow is:

```text
Project Understanding -> Requirements -> Project Classification
 -> Candidate Capabilities -> Applicability Check
 -> Constraints / Compatibility Check -> Capability Selection
 -> Architecture / Specification -> Plan
```

The ordering between Architecture and Technical Specification remains open;
both may need to inform each other. This proposed flow does not override the
project lifecycle elsewhere in this specification until the owner resolves
that ordering.

Every recommendation should have concise decision traceability: triggering
requirements and project characteristics, supporting evidence and applicable
Forge Knowledge, assumptions, constraints, alternatives and why they were not
selected, limitations/risks, reversibility, and whether approval is required.
This is an explanation of the decision and its sources; it does not require
disclosure of private model reasoning.

### Knowledge and capability are different

Forge Knowledge describes reviewed patterns, engineering rules, anti-patterns,
lessons, applicability conditions, exceptions, and evidence. The Capability
Registry describes mechanisms Forge can potentially recommend, generate, or
integrate, including purpose, contract, prerequisites, dependencies, supported
environments, limitations, permissions, security implications, external
effects, tests, compatibility, and possible implementation mechanisms.
Knowledge may inform capability discovery; capability availability does not
make it required.

Capability disposition must distinguish `REQUIRED`, `OPTIONAL`, `CONDITIONAL`,
`UNAVAILABLE`, `INCOMPATIBLE`, `DEFERRED`, and `REJECTED`. Absence is not a
problem when a capability is not required. A conditional capability remains
unselected until its applicability condition is resolved. Recommendations
remain proposals until project-specific selection and any required owner
approval.

### Optional integrations and entity boundaries

Mastery Creator is an optional capability/integration candidate, not a
universal Forge component. UI does not imply Mastery Creator; browser use does
not imply Mastery Creator; multi-account does not imply Mastery Creator.
Discovery must first establish whether UI, browser, profile, session, or other
capabilities are needed, then evaluate available mechanisms. Other projects
may use another browser runtime, their own implementation, a conventional UI,
or no UI/browser at all. Mastery Creator remains outside Forge Core unless a
separate approved decision establishes otherwise; its real integration
contract requires its own analysis.

Do not collapse Account, Identity, Credential, Browser Profile, Browser
Instance, Session, Persistent State, Proxy, Wallet, Email, and
project-specific account state into one universal Account object. An Account
may reference some of these entities without owning them. Fingerprint, proxy,
browser, wallet, and email are not default Account fields or universal
requirements; relationships come from the project requirements.

### Learning after project creation

Learning continues through implementation, build/run, tests, real-world
feedback, failures/observations, corrections, and verification. New findings
belong first to Project Memory. Only reviewed, abstracted, applicable lessons
may become Candidate Forge Knowledge and then approved reusable knowledge.
Project-specific business logic, secrets, credentials, cookies, personal data,
license keys, and session data must never be promoted to Forge Knowledge.

Failure records should preserve project context, affected component, observed
behavior, known or suspected cause, established root cause if any,
consequences, correction, verification, project-specific status, candidate
anti-pattern status, applicability, and counterexamples. One failure is not
enough to establish a global rule. Repeated failures with an independently
verified common cause may justify a reviewed Candidate Anti-Pattern; frequency
alone does not prove that a technology or mechanism is generally unsuitable.

### Generated project independence and technology choice

Forge is the factory/orchestrator; a generated project is a separate product.
The default target is an independently runnable and maintainable project that
does not silently depend on the entire Forge runtime. If a project intentionally
uses a Forge SDK, runtime, or service, document that dependency, lifecycle,
versioning, portability, deployment, and security implications as an explicit
architecture decision. Forge updates must not silently update user projects.

Select a project's language, database, UI framework, deployment platform, and
browser runtime from its requirements, existing source when migrating,
deployment environment, available integrations, user/team preferences,
maintenance, security, performance, cost, ecosystem, tooling, and compatibility.
Axis, KonnexMastery, FlashCast, Mastery Creator, and KeyCore-Hub are evidence
sources and examples, not universal templates. Their described details remain
owner-provided until independently inspected.

KeyCore-Hub is an existing project requiring separate analysis. This
specification does not make it the universal Forge runtime, a Forge dependency,
the mandatory execution host, or the universal account manager. Launching or
managing a generated project through KeyCore-Hub is a possible project-specific
deployment integration, not an established architecture decision. KeyCore-Hub
code is outside the scope of this specification update.

### Product identity and future clients

Forge AI is itself a product with a distinct visual identity. Its conceptual
asset set may include a master logo, application and platform icons, SVG/PNG
source assets, favicon, splash/start screen, light/dark themes, and future
installer/shortcut branding. This records categories of design work only; it
does not choose a logo, colors, formats, UI framework, or implementation
technology. A generated project owns its own name, logo, icons, colors, UI
style, and branding; Forge branding is not inherited automatically.

Future Desktop and Web clients should present one coherent Forge product and
use Forge API/Core rather than implement business logic independently. This is
a target boundary, not a current implementation or a decision about Desktop
technology, authentication, API security, or local/hosted/hybrid deployment.

Project classification and risk may influence QUICK, STANDARD, DEEP, or
MIGRATION Discovery depth without forcing a rigid questionnaire.

### Documentation impact and unresolved decisions

| Document | Section | Addition / disposition | Reason | Source and status |
| --- | --- | --- | --- | --- |
| `TECHNICAL_SPECIFICATION.md` | Project classification and capability selection | Classification, explainable selection, optionality, integration/entity boundaries, post-creation learning, project independence, stack choice, KeyCore-Hub boundary | Defines target constraints without claiming implementation | Stage 1.3 follow-up; **PROPOSED** |
| `FORGE_VISION.md` | Forge product identity | Forge identity is distinct from generated-project identity | Records the product-level distinction without choosing visual design | Stage 1.3 follow-up; **PROPOSED** |
| `ARCHITECTURE.md` | No section changed | No update | This document describes current implementation; these concepts are not implemented | Stage 1.3 follow-up; **CURRENT/FUTURE boundary preserved** |
| `ROADMAP.md` | No section changed | No update | Implementation order and owner decisions remain unresolved | Stage 1.3 follow-up; **DECISION REQUIRED** before scheduling |
| `AGENTS.md` | No section changed | No update | No permanent agent rule changes are required | Existing documentation governance applies |
| Discovery-specific document | Not present | No separate file created; target model is recorded in this specification | Avoid duplicating the same target rules before a separate Discovery manual is approved | Stage 1.3 follow-up |

Owner decisions remain open for Architecture/Specification ordering; the
Knowledge Store and governance authority; capability review and lifecycle;
KeyCore-Hub and Mastery Creator integration; Forge API security and
authentication; Local/Hosted/Hybrid deployment; final UI/Desktop technology;
Git integration; branding design; and approval policy. This section does not
select a database, runtime, framework, or deployment model.

### Stage 1.3 additional conceptual walkthroughs

These scenarios validate boundaries and are not implemented behavior.

| Scenario | Expected behavior |
| --- | --- |
| CLI/API project with no UI or browser requirement | Do not recommend Mastery Creator solely because it exists in Forge Knowledge. |
| Desktop/Web UI without browser automation | Treat UI and browser runtime as separate capabilities; do not infer a browser requirement. |
| Browser workflow satisfiable by another runtime | Compare compatible capabilities; do not select Mastery Creator automatically. |
| Repeated mechanism does not fit current requirements | Explain it only as a conditional pattern; do not make it mandatory. |
| Generated project fails in deployment | Record evidence in Project Memory; do not immediately create global knowledge. |
| Same verified root cause recurs independently | Propose a Candidate Anti-Pattern with applicability and counterexamples for review. |
| Python and Node.js both appear viable | Compare requirements and constraints; prior project language is not the deciding rule. |
| Project can run independently or via Forge runtime | Make the dependency and lifecycle implications explicit for approval. |
| Project may launch through KeyCore-Hub | Treat as a project-specific integration pending KeyCore-Hub and requirement analysis. |
| Forge has its own logo and Desktop identity | Do not inherit Forge branding into generated projects. |

## 23. Lifecycle and governance reconciliation (proposed)

This section reconciles target lifecycle concepts without claiming that the proposed order or approval policy has been approved or implemented.

### Established boundaries

- Requirements state what the product must achieve, its constraints, and acceptance criteria.
- A Specification describes sufficiently precise behavior and contracts to build and verify the requirements.
- Architecture describes structure, boundaries, components, interfaces, deployment shape, technology choices, and material non-functional concerns.
- A Plan describes work needed to realize the approved Specification and Architecture.
- Capability selection is derived from requirements and can influence Architecture; Architecture can reveal a missing requirement or an incompatible, unnecessary, or unavailable capability.
- Discovery evidence, owner intent, proposals, approvals, and current implementation are distinct. Current implementation does not prove a target capability exists.

The existing execution flow in Section 10 begins after planning and describes execution, review, verification, and controlled change. It is not the complete product-intake lifecycle.

### Proposed canonical lifecycle

```text
User Goal / Existing Materials
 -> Discovery <-> Evidence
 -> Project Brief / Confirm Understanding
 -> Requirements / Scope Approval when required
 -> Project Classification (derived assessment)
 -> Candidate Capabilities
 -> Applicability and Compatibility Checks
 -> Proposed Capability Selection
 -> Specification <-> Architecture (controlled iteration)
 -> Readiness Gate
 -> Plan / Plan Approval when required
 -> Implementation
 -> Build / Run / Tests / Verification
 -> Ready-to-Run Handoff
 -> Operation / Feedback / Change Management
 -> Project Memory
 -> Candidate Lesson -> Review -> Possible Forge Knowledge
```

This lifecycle is iterative, not a mandatory waterfall. Evidence can revise a requirement; Architecture can expose a constraint requiring requirement revalidation; compatibility analysis can reject a proposed capability. Each material correction creates a Project Context Version and runs Impact Analysis. Specification and Architecture form a controlled design loop and are presented together at the required approval gate. The owner must decide whether one artifact has formal precedence or both receive separate approvals; neither ordering is established here.

### Artifacts, processes, and derived assessments

| Kind | Examples | Meaning |
| --- | --- | --- |
| Artifact | Project Brief, Requirement, Evidence, Decision, Specification, Architecture, Plan, Verification Result, Project Context Version | A versioned or attributable record of project understanding, intent, design, or outcome. |
| Process | Discovery, Impact Analysis, Capability Selection, Approval, Revalidation, Planning, Verification, Knowledge Review | Work that creates or changes artifacts and assessments. |
| Derived assessment | Readiness, Complexity, Risk, Project Classification, Capability Applicability | A result derived from current artifacts and rules; not a permanent source of truth. |

Decisions and approvals retain their provenance and scope. A derived assessment identifies the artifact versions and rules it used.

### Capability selection and technology choice

The proposed capability flow is requirements -> candidate capabilities -> applicability/compatibility -> proposed selection -> architecture impact -> approval when required. It iterates when architecture changes the candidate set. Explain the requirements/evidence, matched project conditions, constraints, alternatives, risks, reversibility, and approval need behind a material recommendation.

Technology selection is a reasoned comparison, not a fixed algorithm or historical preference:

- **Existing project / migration:** preserve compatible technology by default; propose a change when requirements or verified constraints justify it.
- **New project:** compare viable stacks against requirements and constraints.
- **Mixed project:** treat existing components, interfaces, and deployment as compatibility constraints; justify any polyglot boundary.

Inputs may include requirements, existing code, deployment environment, integrations, team/user preferences, ecosystem/tooling, maintenance, security, performance, cost, long-term support, and portability. Prior Forge projects do not determine the next project's language or database.

### Owner decisions and approval scopes

Forge may generally make a technical choice without owner approval only when it is reversible and low-risk and does not materially change product meaning, security boundaries, budget, deployment, external commitments, long-term architecture, or irreversible effects. Record the choice, rationale, evidence, material alternatives, and revalidation path.

Where a decision materially affects those areas, seek owner approval. Approval is tied to the specific decision or action, Project Context Version, scope, consequences, and permitted side effects; it is not blanket or permanent. Keep these types distinct:

- **Confirm understanding** — confirms the project summary, not permission to build or act.
- **Approve requirements** — accepts a stated scope and its requirements.
- **Approve decision** — accepts a specific material decision.
- **Approve specification** — accepts the behavioral/technical contracts for implementation.
- **Approve architecture** — accepts major structure and boundaries.
- **Approve plan** — accepts the described implementation work.
- **Approve action** — authorizes a concrete external or irreversible action.

Not every project necessarily needs every approval. Risk-based approval is a proposal; the final policy remains `DECISION REQUIRED`. Each approval refers to the reviewed artifact/context version and does not cover materially changed scope.

### Readiness gates for the proposed lifecycle

Readiness answers whether Forge is sufficiently informed **and authorized to move from one named stage to another**. It does not assert that the whole project is complete or fully understood. Each assessment reports mandatory knowledge, required approvals, acceptable unknowns, blockers, stale decisions, and unresolved conflicts. Project-specific risk may add gate conditions.

| Transition | Mandatory knowledge / approvals | Acceptable unknowns | Blockers |
| --- | --- | --- | --- |
| Discovery -> Brief | Goal, users, main workflows, available evidence; confirm understanding before treating the Brief as owner-validated | Low-risk edge cases | Unclear goal or material unresolved conflict |
| Brief -> Requirements | Scope, outcomes, constraints, key inputs/outputs | Implementation details | Product ambiguity that changes outcomes |
| Requirements -> Specification/Architecture loop | Critical scenarios, integrations, data/security constraints; requirements approval when required by policy | Low-risk technical details | Critical unknowns, incompatible constraints, required approval absent |
| Specification/Architecture loop -> Planning | Coherent contracts and boundaries; material decisions revalidated; approvals when required | Task-level implementation detail | Stale decision, unresolved architecture-impacting conflict, missing required approval |
| Planning -> Implementation | Acceptance criteria, dependencies, blockers, authorized scope; plan approval when required | Deferred non-blocking improvements | Missing criteria, blocking dependency, absent approval |
| Implementation -> Verification / Ready-to-Run | Required build/run/test evidence, setup instructions, known limitations | Explicitly deferred non-critical checks | Failed mandatory checks, unsafe configuration, missing required handoff material |
| Change request -> Replan / Implementation | Change classification, impact map, affected approvals, updated scope | Unaffected prior unknowns | Unresolved high-impact conflict or required approval absent |

This is a proposed baseline, not a final approval policy. The earlier simpler readiness matrix should be reconciled with this one after owner approval of the lifecycle model.

### Change management after initial generation

Classify a new request as clarification, additive requirement, changed requirement, removed requirement, constraint change, architecture change, deployment change, or security change. Then follow:

```text
New Request -> Change Classification -> Impact Analysis
 -> Affected Requirements / Decisions / Specification / Architecture
 -> Plan / Tests / Approvals Update
 -> Revalidation -> Replan -> Implementation -> Verification
```

Do not treat every change as merely a new task. Preserve superseded records and identify changed scope. A change to security boundaries, deployment, or external effects requires the relevant readiness and approval checks again.

### Generated-project dependencies

The default target remains an independently runnable and maintainable generated project. A deliberate dependency on Forge runtime, SDK, service, KeyCore-Hub, or managed external infrastructure must be explicit and assessed for version compatibility, availability, deployment, security, portability, update lifecycle, failure behavior, and independent operation. This is an evaluation policy, not a selected dependency architecture.

Owner-provided information confirms KeyCore-Hub is an existing project. Its complete verified architecture and exact relationship to Forge remain unknown. Launching/managing a project through it is only a possible integration. Mastery Creator remains an optional candidate mechanism, not a universal runtime.

### Memory and learning through operation

Project Memory continues after generation and distinguishes current known state from historical records. Subject to the unresolved retention/deletion policy, it may retain the original goal, approved requirements, architecture decisions, implementation changes, verification results, deployment observations, failures, corrections, user feedback, and superseded decisions.

```text
Project Memory -> Observation -> Candidate Lesson -> Review
 -> Possible Forge Knowledge
```

Project lessons never become global knowledge automatically. Review considers recurrence, root cause, evidence quality, independent examples, applicability, counterexamples, privacy/security filtering, approval, versioning, and later revalidation. Authority to approve, delegate, dispute, deprecate, or reverse global knowledge remains `DECISION REQUIRED`.

### Walkthrough of the reconciled lifecycle

These are conceptual checks, not implemented behavior.

| Case | Expected outcome |
| --- | --- |
| Simple new project | Discovery asks only material questions; clear requirements pass the relevant gate; a straightforward Specification/Architecture loop yields a plan. |
| Existing software conflicts with owner description | Preserve code/runtime evidence and owner intent separately; resolve the conflict before approving affected requirements. |
| Browser capability is uncertain | Keep it conditional; request evidence or clarification only if it changes workflow or architecture. |
| Browser/UI exists but Mastery Creator may not fit | Compare candidates against requirements; do not select Mastery Creator by default. |
| Architecture exposes a new constraint | Record evidence, revalidate the affected requirement, update Specification/Architecture, then replan. |
| Deployment changes after architecture approval | Version context, mark dependent decisions affected, obtain required reapproval, and revalidate before replanning. |
| Security boundary changes | Run security impact analysis and require scoped approval where policy requires; do not rely on old approval. |
| Post-release failure is corrected | Record failure, correction, and verification in Project Memory; preserve prior state in history. |
| Same verified failure recurs independently | Create a Candidate Anti-Pattern for review; do not publish a global rule automatically. |
| Plan includes irreversible external action | Require separate scoped action approval before execution; earlier understanding/plan approval is insufficient. |

### Documentation impact and owner decisions

| Document | Section | Change / reason | Source and status |
| --- | --- | --- | --- |
| `TECHNICAL_SPECIFICATION.md` | Lifecycle, artifact boundaries, readiness, governance | Reconcile lifecycle as an explicit proposal; preserve unresolved order and approval choices | Stage 1.4; **PROPOSED / DECISION REQUIRED** |
| `FORGE_VISION.md` | No change | Existing high-level workflow is compatible; detailed lifecycle belongs in the technical specification | Stage 1.4 review |
| `ARCHITECTURE.md` | No change | Describes current implementation, not target lifecycle behavior | Stage 1.4 review; current/future boundary |
| `ROADMAP.md` | No change | Do not schedule unresolved governance/lifecycle decisions as implementation commitments | Stage 1.4 review; **DECISION REQUIRED** |
| `AGENTS.md` | No change | No development-rule change is needed | Stage 1.4 review |
| `DECISIONS.md` | No change | The proposed model is not an approved architectural decision | Stage 1.4 review |

Owner decisions remain open for Specification/Architecture precedence and approval; whether every project needs each approval or uses risk-based policy; the final technology-selection policy; permitted generated-project dependencies; KeyCore-Hub relationship; Project Memory retention/deletion; and authority, delegation, dispute, and deprecation rules for Forge Knowledge. This section makes none of these decisions.
---

# Forge AI — Technical Specification v1.0 — русская версия (разделы 1–20)

Этот документ определяет долгосрочную целевую архитектуру и ограничения Forge AI.
Он отличает эту цель от реализации, находящейся сейчас в этом репозитории.
Спецификация — архитектурное руководство, а не утверждение, что все описанные
возможности существуют сегодня.

## 1. Назначение продукта

Forge AI — платформа AI-оркестрации, позволяющая пользователю описать проект или
задачу на естественном языке, после чего Forge координирует специализированных
AI-агентов, инструменты, состояние проекта, ревью, тестирование и контролируемые
изменения.

Сам Forge — **не AI-модель**. Это слой оркестрации между пользователем, AI-моделями,
агентами, инструментами, проектами, Git и внешними сервисами. Его роль —
координировать работу, сохраняя рамки проекта, учётные данные и чувствительные
действия под контролем пользователя.

## 2. Текущая реализация и целевая архитектура

### Текущая реализация

> Проверенный статус, свидетельства по областям и известные ограничения
> поддерживаются в [`SOURCE_OF_TRUTH.md`](SOURCE_OF_TRUTH.md). Этот файл —
> авторитетный индекс того, что существует сегодня; данный раздел — его краткое
> изложение.

- Python-приложение с провайдер-нейтральными интерфейсами `Task`, `TaskResult`,
  агентов и провайдеров.
- Runtime в памяти, собираемый `create_runtime`; запуск не вызывает AI-провайдеров.
- Детерминированный Planner, выбирающий один из четырёх шаблонов и возвращающий
  статический план с последовательными зависимостями. Он не выполняет этот план.
- Детерминированная классификация задач и маршрутизация Dispatcher с
  использованием регистрации провайдеров, конфигурации, capabilities, наличия
  ключей, cost-tiers, явного выбора и конечной политики fallback.
- Интеграции провайдеров для OpenAI, Anthropic, Google Gemini, DeepSeek,
  OpenRouter и Groq, а также offline-провайдер Mock. xAI зарегистрирован как
  ненастроенная заглушка.
- Поток одного выполнения primary и reviewer не более чем с одной ревизией после
  `CHANGES_REQUESTED`, за которым следует финальное ревью.
- Локальный `SecretStore`, разрешающий учётные данные по требованию, проверяя
  значения окружения процесса раньше игнорируемого корневого файла `.env`.
- Слой решений, выдающий рекомендации по управлению потоком без полномочий
  выполнения, конверт контекста решений и ограниченный Agent Harness, реализующий
  явный цикл
  `OBSERVE -> CONTEXT -> DECIDE -> VALIDATE -> AUTHORIZE -> ACT -> OBSERVE_RESULT -> UPDATE`
  не более чем с одним авторитетным действием за итерацию.
- Agent Skill System (реестр, детерминированный evaluator, происхождение,
  ограничения доверия) и запуск агента с несколькими skills.
- Project Execution Profile и Execution Plane: `ExecutionRequest`, `ExecutionResult`,
  `ProjectExecutionProfile`, `ExecutionPolicy`, менеджер временного локального
  workspace и локальный адаптер процессов.
- Execution Authorization Contract v0.2: неизменяемый `ExecutionIntent` с
  каноническим fingerprint, каноническая безопасность путей для идентичности
  программы и путей относительно workspace, граница Permission с default-deny,
  привязанная к интенту одноразовая граница Approval, политика инвокаций с
  классификацией по capabilities, обязательный явный `workspace_root` и маркер
  `AuthorizedExecution`, который локальный адаптер требует до запуска любого
  процесса.
- Project Memory (модели, store, validator) и Knowledge Governance (модели
  candidate/review/approval, store, validator).
- Контракт верификации со структурированными свидетельствами, адаптер верификации
  тестов и acceptance gate с трассируемостью требований.
- Контракт состояния проекта, контракт событий/трассы запуска, change sets,
  снапшоты проектов и матрица отказов интеграции.
- Юнит-тесты, smoke-команды реальных провайдеров и история Git-репозитория,
  которой управляют разработчики. Само приложение сейчас не управляет
  изменениями Git.

В текущем приложении нет Forge API, Desktop- или Web-клиента, setup wizard, очереди
задач, параллельного выполнения проектов, универсального запуска инструментов или
автоматической интеграции с Git/деплоем проекта. У execution plane нет
production-точки входа: `create_runtime` не создаёт координатор выполнения,
адаптер или корень workspace. OS-песочницы нет, и она не заявляется.

### Целевая архитектура

Целевая система разделяет клиентов представления, стабильную границу API и
провайдер-нейтральное ядро. Схема показывает предполагаемые границы; это не
описание сервисов, работающих сейчас.

```text
                         USER
                           |
                           v
                  +------------------+
                  | Forge Desktop/Web|
                  +--------+---------+
                           |
                           v
                  +------------------+
                  |    Forge API     |
                  +--------+---------+
                           |
                           v
                  +------------------+
                  |    Forge Core    |
                  +--+-----+-----+---+
                     |     |     |
                     v     v     v
                 Planner Dispatcher Memory
                     \     |     /
                      \    |    /
                       v   v   v
                    Agent System
                  /      |       \
                 v       v        v
              Coder   Reviewer   Tester
                 \       |       /
                  v      v      v
                    Providers
                       |
                       v
                 Tools / Git
                       |
                       v
                    PROJECT
```

Ответственности и границы:

- **Desktop/Web:** показывают состояние проекта, собирают ввод пользователя и
  отображают планы, результаты и предложенные изменения. Это клиенты, и они не
  содержат оркестрации ядра или бизнес-правил проекта.
- **Forge API:** предоставляет стабильную аутентифицированную границу приложения
  для клиентов и других авторизованных интеграций. Транспорт API не должен
  связываться с внутренностями оркестрации.
- **Forge Core:** владеет workflow задач и проектов, применением политик,
  координацией выполнения и провайдер-нейтральными доменными интерфейсами. Ядро
  должно оставаться работоспособным без любого из UI-клиентов.
- **Planner:** превращает цель в инспектируемый план и структуру зависимостей.
- **Dispatcher:** выбирает подходящего агента на базе провайдера согласно смыслу
  задачи, capabilities, конфигурации, политике и явному выбору пользователя.
- **Memory:** извлекает релевантные факты и решения в рамках проекта; она не должна
  без разбора загружать репозиторий в каждую задачу.
- **Agent System:** выполняет работу по ролям через общие контракты. Роли агентов
  отделены от реализаций моделей/провайдеров.
- **Providers:** адаптируют SDK и протоколы конкретных провайдеров к общему
  контракту запроса/ответа.
- **Tools/Git:** предоставляют узко ограниченные операции для инспекции проекта,
  верификации и авторизованных изменений. Они не должны обходить политику ядра или
  одобрение пользователя.

## 3. Обязательные архитектурные принципы

1. **Независимость ядра:** Forge Core не должен зависеть от Desktop/Web UI.
2. **Граница API:** Desktop и Web взаимодействуют с ядром через стабильную границу
   API.
3. **Независимость от провайдера:** ядро не должно зависеть от одного AI-провайдера.
4. **Независимость агентов:** роли агентов должны быть отделены от реализаций
   провайдеров.
5. **Независимость UI:** UI не должен содержать бизнес-логику, принадлежащую ядру.
6. **Изоляция секретов:** API-ключи, токены и пароли никогда не должны быть
   встроены в исходный код.
7. **Контролируемые изменения:** агенты не должны свободно изменять production-системы.
8. **Человеческая авторизация:** деструктивные действия и действия, влияющие на
   deployment или production, требуют явной авторизации.
9. **Детерминированная инфраструктура там, где это практично:** планирование,
   маршрутизация и валидация должны быть детерминированными там, где это даёт
   ценность.
10. **Модульная архитектура:** компоненты должны оставаться заменяемыми.
11. **Экономия контекста/токенов:** агенты должны получать только необходимый для
    их задачи контекст.
12. **Тестируемость:** компоненты ядра должны быть тестируемыми без внешних
    AI-провайдеров там, где это практично.
13. **Никакой лишней связанности:** будущие UI, провайдеры и инструменты не должны
    вынуждать изменения по всему ядру.

## 4. Предполагаемый пользовательский опыт (будущее)

Предполагаемый первый запуск и workflow проекта:

1. Установить и запустить Forge.
2. Пройти setup wizard и настроить провайдеров, workspace и необязательные
   интеграции.
3. Создать или выбрать проект и описать цель на естественном языке.
4. Запустить workflow и наблюдать его план, прогресс задач, активность агентов,
   ревью и тесты.
5. Проверить предложенные изменения и авторизовать или отклонить контролируемые
   действия.
6. Открыть полученный проект и просмотреть его сводку изменений.

Для обычной работы пользователю не нужно понимать Python, Git, API, маршрутизацию
моделей или оркестрацию агентов. Расширенная конфигурация может раскрывать эти
детали, не делая их обязательным условием обычного использования.

## 5. Desktop-приложение (будущее)

Desktop — клиент представления Forge API. Его предполагаемые возможности включают
панель проекта, создание проекта, ввод цели/задачи, представления статуса и очереди
задач, активность агентов, результаты ревью и тестов, проверку изменений, статус
Git, настройки провайдеров, соответствующие логи и setup wizard.

Desktop не должен содержать логику оркестрации Forge или бизнес-правила. Он должен
использовать те же контракты Core/API, что и Web.

## 6. Web-интерфейс (будущее)

Web — ещё один клиент того же Forge API и тех же контрактов ядра. Desktop и Web
могут иметь разные шаблоны представления и взаимодействия, но не должны
реализовывать отдельную логику workflow или маршрутизации. Эта спецификация не
выбирает frontend-фреймворк и не проектирует сам frontend.

## 7. Setup wizard (будущее)

Мастер первого запуска может настраивать учётные данные и выбор AI-провайдеров,
подключение GitHub, идентичность Git, каталог workspace/проекта, необязательный
email, внешние интеграции и доступность инструментов. Каждая настройка должна
валидироваться, а её назначение — объясняться. Необязательные метаданные
email-адресов аккаунтов провайдеров остаются отдельными от API-credentials.

Секреты должны храниться через существующую архитектуру `SecretStore` или будущий
защищённый backend секретов. Мастер не должен помещать секреты в исходники,
обычные настройки runtime, логи, планы или результаты задач. Текущий локальный
подход через `.env` — это рабочий процесс разработчика, а не утверждение, что
production-backend секретов уже существует.

## 8. Система провайдеров

Слой провайдеров провайдер-нейтрален и расширяем. Текущая фабрика регистрирует
OpenAI, Anthropic, Google/Gemini, xAI, DeepSeek, OpenRouter, Groq и Mock. OpenAI,
Anthropic, Google/Gemini, DeepSeek, OpenRouter и Groq имеют интеграции провайдеров;
xAI остаётся ненастроенной заглушкой, а Mock — offline/test-поддержкой.

Выбор провайдера может учитывать категорию задачи, объявленные capabilities,
доступность, cost-tier, конфигурацию, политику fallback и явный выбор
пользователя/провайдера. Ни один провайдер не является всегда лучшим. Явный выбор
пользователя имеет приоритет; автоматическая политика и fallback должны быть
ограниченными и объяснимыми. Добавление провайдера не должно требовать
специфичных для провайдера ветвлений по всему ядру.

Текущие cost-tiers — грубые метаданные, а не рассчитанные цены. Будущие политики
бюджета или стоимости должны указывать источник данных и неопределённость, а не
подразумевать точное знание биллинга.

## 9. Система агентов

Потенциальные ролевые агенты включают Planner, Architect, Coder, Analyst, Reviewer,
Tester, Researcher, Documentation agent и Git/Release agent.

**Роль агента — не AI-провайдер.** Роль описывает ответственность и может
использовать разных провайдеров в рамках политики маршрутизации. Ни один агент не
является автоматически авторитетным. Результаты ревью и тестов — свидетельства для
пользователя и workflow; они сами по себе не дают разрешения изменять или
выпускать проект.

## 10. Предполагаемый жизненный цикл проекта

```text
User Goal
   -> Planning
   -> Task Decomposition
   -> Task Queue
   -> Dispatcher
   -> Agent Execution
   -> Review
   -> Controlled Revision
   -> Testing
   -> Final Review
   -> Change Summary
   -> User Authorization
   -> Git Commit
   -> Optional Deployment
```

**Сейчас:** Planner создаёт статический детерминированный план по шаблону;
Dispatcher классифицирует и маршрутизирует отдельные задачи; runtime может
выполнить primary-агента и reviewer, выполнить не более одной ревизии после
запрошенных изменений и вернуть финальный статус ревью. Текущие тесты проверяют код
Forge; приложение не запускает автоматически запланированную задачу тестирования
проекта.

**Будущее:** постоянные планы, декомпозиция и выполнение зависимых задач, очереди
задач, параллельное выполнение, использование инструментов по ролям, оркестрация
тестирования на уровне проекта, сводки изменений и авторизованные действия
Git/деплоя. Каждая стадия должна сообщать свой статус и сохранять отказы, а не
подразумевать успех.

## 11. Project Memory (будущее)

Project Memory может хранить или индексировать структуру проекта, архитектурные
решения, важные файлы, предыдущие задачи и ревью, конвенции проекта, известные
проблемы и важные ограничения. Извлечение должно быть ограничено проектом и
задачей. Память не должна означать слепую загрузку всего репозитория на каждый
запрос. Сохранённые факты должны быть инспектируемыми, исправимыми и удаляемыми под
ясными проектными средствами контроля. Постоянная память проектов сегодня не
реализована.

## 12. Git и контроль версий

Предполагаемый workflow:

```text
Agent changes -> Diff -> Tests -> Review -> User authorization where required
             -> Commit -> Optional push -> Optional deployment
```

Сегодня этот репозиторий использует Git для истории исходников, но код приложения
Forge не предоставляет Git-операции проекта или интеграцию с GitHub. В целевой
системе действия Git должны проходить через ограниченные инструменты и проверки
политики. Не должно быть автоматического force-push, деструктивного переписывания
истории или деплоя в production. Push и деплой — отдельные необязательные действия
с явной авторизацией там, где она требуется.

## 13. Безопасность и контроль изменений

Обязательные защиты для текущей разработки и будущего выполнения:

- Не раскрывать секреты в исходниках, логах, контексте задач или результатах.
- Не вносить неконтролируемые изменения в production.
- Не выполнять деструктивные операции без явной авторизации.
- Не выполнять force-push и не переписывать историю Git без явной авторизации.
- Держать изменения видимыми и проверяемыми; не делать скрытых изменений.
- Запускать релевантные тесты перед коммитом там, где это уместно, и сообщать их
  фактические результаты.
- Требовать явную авторизацию для чувствительных действий, включая влияние на
  production, деструктивные изменения, публикацию или деплой.

Разрешения инструментов должны быть узкими, ограниченными проектом и проверяемыми
в точке использования. Ответ модели не является авторизацией.

## 14. Управление контекстом и стоимостью

Целевая стратегия — извлекать только релевантные файлы, поддерживать лёгкую карту
проекта, давать контекст по ролям, избегать повторных полных сканирований
репозитория, выбирать наименее дорогого подходящего провайдера и переходить к более
сильным моделям, когда это оправдано. Параллельные агенты должны использоваться
только когда их независимая работа даёт ценность. Повторных ревью без новых
свидетельств следует избегать. Оценки использования и стоимости должны
обозначаться как оценки, когда они не являются фактическими данными биллинга
провайдера.

## 15. Расширяемость

Целевая система должна поддерживать новых провайдеров, роли агентов, инструменты,
типы проектов, UI-клиенты, внешние интеграции и среды выполнения без переписывания
ядра. Расширения должны реализовывать стабильные интерфейсы и объявлять
capabilities/конфигурацию. Типы SDK провайдеров и данные, специфичные для UI, не
должны протекать в общие модели workflow.

## 16. Матрица текущих и будущих возможностей

| Область | Сейчас | Будущее |
| --- | --- | --- |
| Provider Layer | Общий интерфейс; OpenAI, Anthropic, Gemini, DeepSeek, OpenRouter, Groq, Mock; заглушка xAI | Более широкие метаданные провайдеров/моделей и заменяемые интеграции |
| Dispatcher | Детерминированная классификация, проверки capabilities/конфигурации, cost-tiers, явный выбор, конечный fallback | Рост политики по бюджету, качеству и задержке с объяснимыми решениями |
| Task Classification | Основанные на правилах CODE, ANALYSIS, REVIEW, OTHER плюс legacy-категории маршрутизации | Видимые пользователю средства управления классификацией и более широкие таксономии проектов/задач |
| Planner | Детерминированные шаблоны; статические планы с упорядоченными зависимостями | Декомпозиция целей, редактируемые планы и планирование с учётом выполнения |
| Multi-Agent Execution | Primary плюс reviewer; не более одного цикла коррекции | Ограниченные политикой роли и workflow |
| Review | Провайдер-нейтральный результат ревью и APPROVED/CHANGES_REQUESTED/FAILED | Настраиваемая политика ревью и более богатые свидетельства |
| Revision | Одна ревизия после CHANGES_REQUESTED, затем финальное ревью | Аккуратно ограниченная политика коррекции со средствами управления пользователя |
| Project Memory | Не реализовано | Память и извлечение в рамках проекта, инспектируемые |
| Task Queue | Не реализовано; планы не диспетчеризуются как очередь | Постоянная очередь, планирование зависимостей и состояние жизненного цикла |
| Parallel Execution | Не реализовано | Ограниченные параллельные задачи с управлением конфликтами |
| Forge API | Не реализовано | Стабильная граница клиент/API с аутентификацией и авторизацией |
| Desktop | Не реализовано | Desktop-клиент Forge API |
| Web | Не реализовано | Web-клиент, разделяющий контракты Core/API |
| Setup Wizard | Не реализовано | Валидированная первичная настройка провайдеров, workspace, Git и инструментов |
| Git workflow | Git отслеживает этот исходный репозиторий; внутри приложения нет Git-операций проекта | Ограниченный поток diff/test/review/commit/push с гейтами одобрения |
| Deployment | Не реализовано приложением | Необязательные, явно авторизованные workflow деплоя |
| Project management | Только модели статического плана; нет постоянного жизненного цикла проекта | Состояние проекта, планы, зависимости, задачи и результаты, управляемые от начала до конца |

## 17. Связь с другой документацией

- `TECHNICAL_SPECIFICATION.md` определяет целевую архитектуру и ограничения.
- `ROADMAP.md` определяет поэтапный порядок реализации.
- `ARCHITECTURE.md` описывает текущую техническую архитектуру и реализованное
  поведение.
- `FORGE_VISION.md` описывает продуктовое видение и мотивацию.
- `AGENTS.md` определяет правила разработки для AI-агентов.

Эти документы должны согласованно описывать, что существует, а что остаётся будущей
работой. Когда они расходятся, реализация и проверенное поведение определяют
текущее состояние; обновляйте соответствующий документ, а не представляйте цель как
уже поставленную.

## 18. Не-цели текущей стадии

Forge AI сейчас не предполагается как:

- Неограниченный автономный агент кодинга.
- Замена человеческой авторизации.
- Навсегда привязанный к одному AI-провайдеру.
- Зависимый от одной IDE.
- Единое монолитное приложение, объединяющее UI и логику оркестрации.
- Система автоматического деплоя в production.

Эти границы не препятствуют будущим возможностям; они требуют, чтобы эти
возможности вводились через явный, тестируемый и контролируемый дизайн.

## 19. Поддержание спецификации

Держите эту спецификацию достаточно краткой, чтобы её можно было поддерживать по
мере эволюции архитектуры. Держите политику и границы здесь; детальное поведение
реализации размещайте в исходном коде и в документе текущей архитектуры. Описывайте
будущую работу как будущую работу и обновляйте этот документ, когда меняется
существенное целевое ограничение или граница.

## 20. Управление документацией

Техническая и продуктовая документация Forge, предназначенная как для AI-агентов,
так и для владельца проекта, должна сохранять каноническое английское содержание и,
где уместно, включать после него перевод на русский язык. Переводы сохраняют смысл,
структуру, нумерацию и техническую терминологию; код, пути, команды, идентификаторы
и другие технические literals остаются неизменными, если перевод не необходим для
понимания.

Когда задокументированные архитектурные, продуктовые, безопасностные, рабочие или
иные правила меняются, определите затронутые документы, обновите только их,
обновите их русские переводы там, где они есть, и проверьте согласованность по
всему набору документации. Не пересматривайте механически каждый документ после
каждого изменения. Применяйте то же управление к новой постоянной документации.

Иерархия документации:

- `AGENTS.md` — правила поведения AI-разработки.
- `FORGE_VISION.md` — продуктовое видение и долгосрочное направление.
- `TECHNICAL_SPECIFICATION.md` — целевая техническая архитектура и обязательные
  ограничения.
- `ARCHITECTURE.md` — текущая техническая архитектура реализации.
- `ROADMAP.md` — поэтапный порядок реализации и прогресс проекта.
- `DECISIONS.md` — зафиксированные архитектурные решения и их обоснование.

Держите текущую реализацию отдельно от целевой/будущей архитектуры. Когда
реализация меняется, обновляйте документацию текущего состояния и статус roadmap,
когда это уместно. Меняйте техническую спецификацию только когда меняется целевая
архитектура или ограничения, видение — только когда меняется направление продукта,
а записи решений — только когда принято архитектурное решение. Производные описания
должны оставаться согласованными с авторитетным документом без лишнего дублирования.

---

# Forge AI — русская версия дополнения Stage 1.3

## 21. Интеллект Discovery и контекст проекта (будущее)

Discovery должен поддерживать версионируемый контекст проекта, а не полагаться
на неструктурированную стенограмму диалога. Его логические области включают
цель продукта, пользователей и роли, сценарии, входы и выходы, внешние системы,
существующее ПО и инфраструктуру, данные, ограничения, безопасность, выбранные
capabilities, требования, решения, вопросы, свидетельства, предположения,
неизвестное, риски, критерии приёмки, архитектуру, план и историю изменений.
Набор заполненных областей зависит от проекта; этот список не является
обязательной схемой для каждого генерируемого продукта.

Текущий контекст — это производное рабочее представление. Существенные
исправления создают новую `ProjectContextVersion`; предыдущие версии остаются
в истории вместе с автором, причиной, подтверждающими материалами и затронутыми
требованиями, решениями и подтверждениями. Исторические записи нельзя молча
перезаписывать. Оценки готовности и понимания являются производными и должны
указывать версию контекста, которую они оценивали.

### Независимые понимание, evidence и готовность

Оценивать понимание отдельно для областей Product, Workflow, Data, Integration,
Security, Runtime, Deployment, Users/Roles и Acceptance, добавляя другие области
при необходимости проекта. Уровень привязан одновременно к области и этапу:
`SUFFICIENT for Brief` не означает `SUFFICIENT for Architecture`. Использовать
`UNKNOWN`, `PARTIAL`, `SUFFICIENT` и `CONFIRMED`; эти уровни описывают полноту,
а не тип свидетельства или численную уверенность.

Записи Evidence должны отделять тип утверждения от качества источника. Типы:
`FACT`, `SUPPORTED_INFERENCE`, `WEAK_INFERENCE`, `ASSUMPTION`, `UNKNOWN` и
`CONFLICT`. К метаданным относятся источник/provenance, автор, время наблюдения,
версия источника, если доступна, область действия, свежесть и авторитетность
для данного типа утверждения. Высокая уверенность не превращает inference в
факт.

Readiness — вычисляемая оценка конкретного перехода, а не постоянный статус
проекта. Она указывает обязательные области, допустимые неизвестные, блокеры,
неразрешённые противоречия, устаревшие решения и отсутствующие подтверждения.
Результаты: `BLOCKED`, `READY_WITH_OPEN_ITEMS` и `READY`. Для конкретного
проекта могут добавляться дополнительные условия. Подтверждение понимания
пользователем отличается от утверждения решения, плана или действия.

| Переход | Обязательное понимание | Какие неизвестные могут остаться |
| --- | --- | --- |
| Discovery -> Brief | Цель, пользователи и основные сценарии | Низкорисковые граничные случаи |
| Brief -> Requirements | Область проекта, результаты и ограничения | Детали реализации, не меняющие требования |
| Requirements -> Specification | Критические сценарии, интеграции, ограничения безопасности и данных | Низкорисковые технические детали |
| Specification -> Architecture | Существенные NFR, предположения о deployment и системные границы | Детали реализации |
| Architecture -> Planning | Существенные архитектурные решения и выбранные capabilities | Детали отдельных задач |
| Planning -> Implementation | Критерии приёмки, блокеры и необходимые подтверждения | Будущие улучшения |

Эта таблица — базовая модель, а не универсальная конфигурация gate.

### Вопросы, решения и анализ влияния

Вопрос — это запись для поддержки решения, а не только текст запроса. Она
указывает, какое решение уточняет, почему ответ важен, известные evidence,
затрагиваемые объекты, необходимый этап, приоритет влияния, блокирующий статус,
может ли Forge безопасно решить самостоятельно и допустима ли отсрочка.
Не задавать вопрос, если ответ не может изменить требование, критерий приёмки,
безопасность, deployment, выбор capability или существенное поведение. Перед
вопросом проверить релевантную Project Memory, evidence, выводы из исходников,
утверждённые документы и применимые Forge Knowledge.

Жизненный цикл вопроса (`OPEN`, `ANSWERED`, `RESOLVED`, `DEFERRED`, `REJECTED`),
приоритет влияния (`BLOCKING`, `HIGH`, `MEDIUM`, `LOW`, `NON_CRITICAL`), approval
и readiness — независимые свойства. Статус решения (`PROPOSED`,
`AWAITING_APPROVAL`, `APPROVED`, `REJECTED`, `SUPERSEDED`) также отделён от
свежести решения (`VALID`, `POTENTIALLY_AFFECTED`, `STALE`,
`REQUIRES_REVALIDATION`). Утверждённое решение может устареть после появления
новых evidence.

Существенная новая информация запускает анализ влияния на требования, решения,
архитектуру, capabilities, данные, безопасность, deployment, критерии приёмки,
тесты и план. Зависимости решений образуют граф. Изменение исходного решения
помечает зависимые решения для проверки, но не удаляет и не переписывает их.
Циклы считаются конфликтами, пока не определена политика их разрешения.

### Авторитетность источников и разрешение противоречий

Универсального рейтинга источников нет. Авторитет зависит от утверждения:
намерение владельца лучше всего подтверждается явным решением пользователя;
существующее поведение — исходниками или runtime evidence; API-контракт —
официальной документацией/schema; бизнес-правила проекта — утверждённым
владельцем требованием. Свежесть учитывается отдельно. Более новый источник
не становится автоматически более авторитетным.

При конфликте сохранять оба утверждения и показывать их источники, контекст,
свежесть, возможное влияние и требуемое решение. Автоматически разрешать
конфликт только если применимо заранее утверждённое правило и риск низкий;
иначе запросить решение владельца. Нельзя выбирать источник молча лишь потому,
что он новее.

### Подтверждение и разрешение действий

Не смешивать следующие действия пользователя; каждое относится к проверенной
версии контекста:

- **Confirm understanding:** подтверждает, что резюме Forge отражает намерение
  пользователя; это не разрешение на реализацию или side effects.
- **Approve decision:** утверждает конкретное решение и его заданную область.
- **Approve plan:** разрешает рассмотренный план, но не работу за его рамками.
- **Approve action:** разрешает конкретное внешнее или необратимое действие.

Общий утвердительный ответ не является глобальным разрешением. Forge может
самостоятельно принять техническое решение, только если оно обратимо,
низкорисково, сохраняет смысл продукта и границы безопасности, существенно не
меняет deployment или стоимость и не создаёт значительных внешних обязательств.
Такие решения сохраняют обоснование, evidence, существенные альтернативы и
возможность повторного рассмотрения.

### Управление Forge Knowledge (будущее)

Project Memory и Forge Knowledge — отдельные хранилища и домены доверия. Project
Memory может содержать решения проекта, требования, evidence, approvals,
проектные предпочтения, ссылки на бизнес-логику и историю изменений. Forge
Knowledge содержит проверенные абстрактные инженерные паттерны, capabilities,
anti-patterns и подтверждённые уроки. Project Memory может извлекать подходящее
Forge Knowledge, но не публикуется в нём автоматически.

Жизненный цикл знания:

```text
Observed Pattern -> Candidate Knowledge -> Evidence -> Review
 -> Approved Knowledge -> Applicability Rules -> Usage / Feedback
 -> Revalidation or Deprecation
```

Запись knowledge фиксирует происхождение, evidence, approval, версию, условия
применимости и неприменимости, исключения, контрпримеры, историю использования
и актуальный статус. Частота сама по себе не делает capability обязательной.
Например, частое использование proxy может стать поводом спросить о нём для
подходящих automation-проектов, но не требованием иметь Proxy Manager в каждом
проекте.

Перед утверждением кандидаты проверяются на секреты, credentials, персональные
данные, проектную бизнес-логику, случайные правила из одного проекта,
неподтверждённые inference, отсутствие контрпримеров и неясную применимость.
Сбои могут стать кандидатами в anti-pattern, но глобальным правилом — только
после анализа причины и утверждения. Извлечённое knowledge должно быть
релевантным, версионируемым и проверенным на применимость; нельзя загружать
всю базу знаний в каждый task.

### Необязательные capabilities и нерешённые вопросы управления

Mastery Creator — необязательная потенциальная capability или интеграция
runtime, а не обязательный слой для browser, UI или генерируемых проектов.
Его реальную архитектуру и контракт интеграции нужно отдельно изучить до
выбора. Browser, proxy, multi-account, Telegram, IMAP, EVM, challenge handling,
язык и база данных также выбираются по требованиям, а не предполагаются
универсальными.

Остаются `DECISION REQUIRED`: подтверждения на переходах проекта; полномочия
утверждать глобальное Forge Knowledge; хранение и удаление Project Memory по
умолчанию; авторитет источников для разных типов утверждений; обязательные
audit records и срок их хранения; обработка опровергнутого знания; какие
артефакты получают полную историю версий; представление readiness без
вводящего в заблуждение процента. Этот раздел не выбирает технологию хранения
или trust model local/cloud/hybrid.

### Концептуальные walkthrough Stage 1.3

Walkthrough проверяют модель и не означают, что поведение уже реализовано.

| Сценарий | Ожидаемое поведение Discovery |
| --- | --- |
| Простой проект с достаточными требованиями | Использовать известные evidence, задавать только влияющие на решения вопросы и подготовить краткий Brief. |
| Сайт с неизвестным email verification | Пометить поведение как неизвестное; запросить разрешённые материалы или уточнение; не выдумывать workflow. |
| Legacy-программа расходится с описанием владельца | Сохранить вывод из исходников и слова владельца, показать конфликт и запросить решение, если меняется поведение. |
| Deployment меняется после утверждения Architecture | Создать новую версию контекста, выполнить impact analysis, отметить зависимые решения и перепроверить их до replanning. |
| Частая capability не подходит текущему проекту | Использовать паттерн только при применимости; объяснить его как вариант или задать уместный вопрос; не добавлять автоматически. |
| Одна ошибка произошла в проекте | Сохранить project evidence и кандидатный урок; не публиковать глобальный anti-pattern без review. |
| Предлагается критическое внешнее действие | Показать ограниченное предложение и запросить отдельное разрешение; подтверждения понимания или плана недостаточно. |

## 22. Классификация проекта и выбор capabilities (будущее)

Классификация описывает предметную область проекта, а не выбирает технологию,
и не является закрытым enum. Один проект может относиться к нескольким классам:
CLI/tool, API/backend, web- или desktop-приложение, browser automation, bot,
worker, scheduler, обработка данных, интеграционный сервис, миграция или
гибридная система. Классификация влияет на глубину Discovery и исследование
capabilities, но не выбирает реализационный механизм автоматически.

Предлагаемый поток выбора capabilities:

```text
Project Understanding -> Requirements -> Project Classification
 -> Candidate Capabilities -> Applicability Check
 -> Constraints / Compatibility Check -> Capability Selection
 -> Architecture / Specification -> Plan
```

Порядок Architecture и Technical Specification остаётся открытым; они могут
взаимно влиять друг на друга. Этот предлагаемый поток не заменяет жизненный цикл
в других разделах спецификации, пока владелец не решит вопрос о порядке.

Для каждой рекомендации нужна краткая трассировка решения: требования и
характеристики проекта, которые её вызвали; подтверждающие evidence и
применимое Forge Knowledge; предположения; ограничения; альтернативы и причины
их отклонения; ограничения и риски; обратимость; необходимость approval. Это
объяснение решения и его источников, а не раскрытие скрытого рассуждения модели.

### Knowledge и capability — разные понятия

Forge Knowledge описывает проверенные паттерны, инженерные правила,
anti-patterns, уроки, условия применимости, исключения и evidence. Capability
Registry описывает механизмы, которые Forge потенциально может рекомендовать,
генерировать или интегрировать: назначение, контракт, prerequisites,
зависимости, поддерживаемые среды, ограничения, permissions, последствия для
безопасности, внешние эффекты, тесты, совместимость и возможные способы
реализации. Knowledge может помогать обнаруживать capabilities; наличие
capability не делает её обязательной.

Статус capability должен различать `REQUIRED`, `OPTIONAL`, `CONDITIONAL`,
`UNAVAILABLE`, `INCOMPATIBLE`, `DEFERRED` и `REJECTED`. Отсутствие capability —
не проблема, если она не требуется. Условная capability остаётся невыбранной,
пока не выяснено условие её применимости. Рекомендации остаются предложениями,
пока capability не выбрана для проекта и не получено нужное одобрение владельца.

### Необязательные интеграции и границы сущностей

Mastery Creator — необязательный кандидат на capability/integration, а не
универсальный компонент Forge. UI не означает необходимость Mastery Creator;
использование browser не означает необходимость Mastery Creator; multi-account
не означает необходимость Mastery Creator. Сначала Discovery устанавливает,
нужны ли UI, browser, profile, session или другие capabilities, затем оценивает
доступные механизмы. Другие проекты могут использовать другой browser runtime,
собственную реализацию, обычный UI или вообще не иметь UI/browser. Mastery
Creator остаётся за границами Forge Core, пока отдельное утверждённое решение не
докажет обратное; его реальный контракт интеграции требует самостоятельного
анализа.

Не сводить Account, Identity, Credential, Browser Profile, Browser Instance,
Session, Persistent State, Proxy, Wallet, Email и проектное состояние аккаунта
к одному универсальному объекту Account. Account может ссылаться на некоторые
из них, не владея ими. Fingerprint, proxy, browser, wallet и email не являются
полями Account или универсальными требованиями по умолчанию; связи определяются
требованиями проекта.

### Обучение после создания проекта

Обучение продолжается на этапах реализации, build/run, тестирования,
эксплуатационной обратной связи, ошибок/наблюдений, исправлений и проверки.
Новые находки сначала принадлежат Project Memory. Только проверенные,
абстрагированные и применимые уроки могут стать Candidate Forge Knowledge, а
затем утверждённым повторно используемым знанием. Проектная бизнес-логика,
секреты, credentials, cookies, персональные данные, license keys и session data
не должны переноситься в Forge Knowledge.

Запись о сбое должна сохранять контекст проекта, затронутый компонент,
наблюдаемое поведение, известную или предполагаемую причину, установленную
root cause (если есть), последствия, исправление, проверку, проектную
специфичность, статус кандидатного anti-pattern, применимость и контрпримеры.
Одного сбоя недостаточно для глобального правила. Повторение с независимо
подтверждённой общей причиной может стать основанием для Candidate Anti-Pattern
после review; одна лишь частота не доказывает, что технология или механизм в
общем случае непригодны.

### Независимость генерируемого проекта и выбор технологии

Forge — фабрика/оркестратор; генерируемый проект — отдельный продукт. Целевой
вариант по умолчанию — самостоятельно запускаемый и поддерживаемый проект без
скрытой зависимости от всего runtime Forge. Если проект намеренно использует
Forge SDK, runtime или service, необходимо явно описать эту зависимость,
жизненный цикл, versioning, portability, deployment и последствия для
безопасности как архитектурное решение. Обновления Forge не должны молча
обновлять пользовательские проекты.

Язык, базу данных, UI framework, deployment platform и browser runtime проекта
выбирать исходя из требований, существующего исходного кода при миграции,
deployment-среды, доступных интеграций, предпочтений пользователя/команды,
сопровождения, безопасности, производительности, стоимости, экосистемы,
инструментов и совместимости. Axis, KonnexMastery, FlashCast, Mastery Creator и
KeyCore-Hub — источники сведений и примеры, а не универсальные шаблоны.
Описанные сведения остаются предоставленными владельцем, пока их не проверили
по исходникам.

KeyCore-Hub — существующий проект, требующий отдельного анализа. Эта
спецификация не объявляет его универсальным runtime Forge, зависимостью Forge,
обязательной средой выполнения или универсальным менеджером аккаунтов.
Запуск или управление проектом через KeyCore-Hub — возможная project-specific
интеграция deployment, а не установленное архитектурное решение. Код
KeyCore-Hub не входил в область этого обновления спецификации.

### Идентичность продукта и будущие клиенты

Forge AI является самостоятельным продуктом со своей визуальной идентичностью.
К возможным категориям ресурсов относятся master logo, иконки приложения и
платформ, исходные SVG/PNG, favicon, splash/start screen, светлая/тёмная темы и
брендинг будущего установщика/ярлыков. Здесь фиксируются только категории
дизайна; логотип, цвета, форматы, UI framework и технология реализации не
выбираются. У сгенерированного проекта собственные название, logo, icons,
colors, UI style и branding; брендинг Forge не наследуется автоматически.

Будущие Desktop и Web клиенты должны выглядеть как единый продукт Forge и
использовать Forge API/Core, а не независимо реализовывать бизнес-логику. Это
целевая граница, а не текущая реализация или решение о технологии Desktop,
authentication, API security или local/hosted/hybrid deployment.

Классификация проекта и risk могут влиять на глубину Discovery QUICK, STANDARD,
DEEP или MIGRATION без превращения процесса в жёсткую анкету.

### Влияние на документацию и нерешённые решения

| Документ | Раздел | Дополнение / решение | Причина | Источник и статус |
| --- | --- | --- | --- | --- |
| `TECHNICAL_SPECIFICATION.md` | Классификация проекта и выбор capabilities | Классификация, explainable selection, optionality, границы интеграций/сущностей, обучение после создания, независимость проектов, выбор стека, граница KeyCore-Hub | Фиксирует целевые ограничения, не заявляя о реализации | Дополнение Stage 1.3; **PROPOSED** |
| `FORGE_VISION.md` | Идентичность продукта Forge | Идентичность Forge отделена от идентичности генерируемого проекта | Фиксирует продуктовую границу, не выбирая дизайн | Дополнение Stage 1.3; **PROPOSED** |
| `ARCHITECTURE.md` | Раздел не менялся | Без изменений | Документ описывает текущую реализацию; эти концепции не реализованы | Дополнение Stage 1.3; граница **CURRENT/FUTURE** сохранена |
| `ROADMAP.md` | Раздел не менялся | Без изменений | Порядок реализации и решения владельца ещё не определены | Дополнение Stage 1.3; **DECISION REQUIRED** до планирования |
| `AGENTS.md` | Раздел не менялся | Без изменений | Новых постоянных правил для агентов не требуется | Действуют текущие правила документирования |
| Документ Discovery | Отсутствует | Отдельный файл не создан; целевая модель записана в спецификации | Не дублировать правила до утверждения отдельного руководства Discovery | Дополнение Stage 1.3 |

Решения владельца остаются открытыми для порядка Architecture/Specification;
Knowledge Store и полномочий его утверждения; проверки и lifecycle capabilities;
интеграций KeyCore-Hub и Mastery Creator; API security/authentication Forge;
deployment Local/Hosted/Hybrid; конечной UI/Desktop технологии; Git integration;
дизайна брендинга и нерешённой approval policy. Этот раздел не выбирает БД,
runtime, framework или deployment model.

### Дополнительные концептуальные walkthrough Stage 1.3

Эти сценарии проверяют границы и не являются реализованным поведением.

| Сценарий | Ожидаемое поведение |
| --- | --- |
| CLI/API-проект без требований к UI и browser | Не рекомендовать Mastery Creator только потому, что он есть в Forge Knowledge. |
| Desktop/Web UI без browser automation | Считать UI и browser runtime отдельными capabilities; не выводить необходимость browser. |
| Browser workflow, поддерживаемый другим runtime | Сравнить совместимые capabilities; не выбирать Mastery Creator автоматически. |
| Повторяющийся механизм не подходит требованиям | Объяснить его только как условный паттерн; не делать обязательным. |
| Сгенерированный проект падает при deployment | Записать evidence в Project Memory; не создавать сразу глобальное знание. |
| Одна root cause независимо подтверждена несколько раз | Предложить Candidate Anti-Pattern с применимостью и контрпримерами на review. |
| Python и Node.js подходят одинаково | Сравнить требования и ограничения; язык прошлых проектов не является решающим фактором. |
| Проект запускается самостоятельно или через Forge runtime | Явно показать зависимость и последствия жизненного цикла для approval. |
| Проект может запускаться через KeyCore-Hub | Считать это project-specific интеграцией до анализа KeyCore-Hub и требований. |
| У Forge свой логотип и Desktop-идентичность | Не переносить брендинг Forge в генерируемые проекты автоматически. |

## 23. Согласование жизненного цикла и governance (предложение)

Этот раздел согласует целевые понятия жизненного цикла, но не утверждает, что предлагаемый порядок или политика подтверждений уже приняты или реализованы.

### Установленные границы

- Requirements описывают, чего должен достичь продукт, его ограничения и критерии приёмки.
- Specification описывает достаточно точные поведение и контракты, чтобы построить и проверить требования.
- Architecture описывает структуру, границы, компоненты, интерфейсы, форму deployment, технологические решения и существенные нефункциональные аспекты.
- Plan описывает работу, необходимую для реализации утверждённых Specification и Architecture.
- Выбор capabilities выводится из требований и может влиять на Architecture; Architecture может выявить недостающее требование или несовместимую, ненужную либо недоступную capability.
- Evidence Discovery, намерения владельца, предложения, approvals и текущая реализация — разные сведения. Текущая реализация не доказывает наличие целевой capability.

Существующий поток исполнения в разделе 10 начинается после планирования и описывает исполнение, review, проверку и контролируемые изменения. Это не полный жизненный цикл приёма проекта.

### Предлагаемый канонический жизненный цикл

```text
User Goal / Existing Materials
 -> Discovery <-> Evidence
 -> Project Brief / Confirm Understanding
 -> Requirements / Scope Approval when required
 -> Project Classification (derived assessment)
 -> Candidate Capabilities
 -> Applicability and Compatibility Checks
 -> Proposed Capability Selection
 -> Specification <-> Architecture (controlled iteration)
 -> Readiness Gate
 -> Plan / Plan Approval when required
 -> Implementation
 -> Build / Run / Tests / Verification
 -> Ready-to-Run Handoff
 -> Operation / Feedback / Change Management
 -> Project Memory
 -> Candidate Lesson -> Review -> Possible Forge Knowledge
```

Жизненный цикл итеративный, а не обязательный waterfall. Evidence может изменить требование; Architecture может выявить ограничение, требующее повторной проверки требования; анализ совместимости может отклонить предлагаемую capability. Каждое существенное исправление создаёт Project Context Version и запускает Impact Analysis. Specification и Architecture образуют контролируемый цикл проектирования и рассматриваются совместно перед необходимым approval gate. Владелец должен решить, имеет ли один артефакт формальный приоритет или оба требуют отдельных approvals; здесь ни один порядок не считается установленным.

### Артефакты, процессы и производные оценки

| Тип | Примеры | Значение |
| --- | --- | --- |
| Artifact | Project Brief, Requirement, Evidence, Decision, Specification, Architecture, Plan, Verification Result, Project Context Version | Версионируемая или атрибутируемая запись о понимании проекта, намерении, дизайне или результате. |
| Process | Discovery, Impact Analysis, Capability Selection, Approval, Revalidation, Planning, Verification, Knowledge Review | Работа, создающая или изменяющая артефакты и оценки. |
| Derived assessment | Readiness, Complexity, Risk, Project Classification, Capability Applicability | Результат, производный от текущих артефактов и правил, а не постоянный источник истины. |

Решения и approvals сохраняют provenance и область действия. Производная оценка указывает версии артефактов и правила, которые использовала.

### Выбор capabilities и технологий

Предлагаемый поток: requirements -> candidate capabilities -> проверки применимости/совместимости -> предлагаемая selection -> влияние на архитектуру -> approval при необходимости. Если архитектура меняет список кандидатов, процесс повторяется. Для существенной рекомендации кратко объяснять требования/evidence, совпавшие условия проекта, ограничения, альтернативы, риски, обратимость и необходимость approval.

Выбор технологии — обоснованное сравнение, а не фиксированный алгоритм или историческое предпочтение:

- **Существующий проект / миграция:** по умолчанию сохранять совместимую технологию; предлагать изменение, если его обосновывают требования или проверенные ограничения.
- **Новый проект:** сравнивать подходящие стеки по требованиям и ограничениям.
- **Смешанный проект:** считать существующие компоненты, интерфейсы и deployment ограничениями совместимости; обосновывать границы polyglot.

Факторы выбора могут включать требования, существующий код, deployment-среду, интеграции, предпочтения команды/пользователя, экосистему и инструменты, сопровождение, безопасность, производительность, стоимость, долгосрочную поддержку и portability. Прошлые проекты Forge не определяют язык или базу данных следующего проекта.

### Решения владельца и границы approval

Forge может самостоятельно принять техническое решение только тогда, когда оно обратимо и низкорисково и существенно не меняет смысл продукта, границы безопасности, бюджет, deployment, внешние обязательства, долгосрочную архитектуру или необратимые последствия. Решение должно сохранять обоснование, evidence, существенные альтернативы и путь повторной проверки.

Если решение существенно влияет на эти области, требуется approval владельца. Approval относится к конкретному решению или действию, версии Project Context, области действия, последствиям и разрешённым side effects; он не является общим или постоянным. Различать типы:

- **Confirm understanding** — подтверждает резюме проекта, но не разрешает разработку или действие.
- **Approve requirements** — принимает заданный scope и требования.
- **Approve decision** — принимает конкретное существенное решение.
- **Approve specification** — принимает поведенческие/технические контракты для реализации.
- **Approve architecture** — принимает основные структуру и границы.
- **Approve plan** — принимает описанный план реализации.
- **Approve action** — разрешает конкретное внешнее или необратимое действие.

Не каждому проекту обязательно нужны все виды approval. Risk-based подход — предложение; окончательная политика остаётся `DECISION REQUIRED`. Каждый approval ссылается на проверенную версию артефакта/контекста и не распространяется на существенно изменившийся scope.

### Readiness gates для предлагаемого жизненного цикла

Readiness отвечает, достаточно ли Forge информирован **и уполномочен перейти с одного названного этапа на другой**. Это не означает, что весь проект завершён или полностью понят. Оценка перечисляет обязательные знания, необходимые approvals, допустимые неизвестные, блокеры, устаревшие решения и неразрешённые противоречия. Риск конкретного проекта может добавлять условия gate.

| Переход | Обязательные знания / approvals | Допустимые неизвестные | Блокеры |
| --- | --- | --- | --- |
| Discovery -> Brief | Цель, пользователи, основные сценарии, доступные evidence; подтвердить понимание до того, как Brief считается подтверждённым владельцем | Низкорисковые граничные случаи | Неясная цель или существенное неразрешённое противоречие |
| Brief -> Requirements | Scope, результаты, ограничения, важные входы/выходы | Детали реализации | Неоднозначность продукта, меняющая результаты |
| Requirements -> цикл Specification/Architecture | Критические сценарии, интеграции, ограничения данных/безопасности; approval требований, если того требует политика | Низкорисковые технические детали | Критическое неизвестное, несовместимые ограничения, отсутствие обязательного approval |
| Цикл Specification/Architecture -> Planning | Согласованные контракты и границы; перепроверенные существенные решения; необходимые approvals | Детали задач | Устаревшее решение, неразрешённый архитектурный конфликт, отсутствие обязательного approval |
| Planning -> Implementation | Acceptance criteria, зависимости, блокеры, разрешённый scope; approval плана при необходимости | Отложенные неблокирующие улучшения | Нет критериев, блокирующая зависимость, отсутствие approval |
| Implementation -> Verification / Ready-to-Run | Требуемые build/run/test evidence, инструкции и известные ограничения | Явно отложенные некритичные проверки | Не пройдены обязательные проверки, небезопасная конфигурация, отсутствуют нужные материалы передачи |
| Change request -> Replan / Implementation | Классификация изменения, карта влияния, затронутые approvals, обновлённый scope | Не затронутые прежние неизвестные | Существенный конфликт не разрешён или отсутствует обязательный approval |

Это предлагаемая базовая модель, а не окончательная политика approval. Предыдущую упрощённую readiness matrix следует согласовать с этой таблицей после утверждения владельцем модели жизненного цикла.

### Управление изменениями после генерации

Классифицировать новый запрос как уточнение, добавление требования, изменение требования, удаление требования, изменение ограничения, архитектуры, deployment или безопасности. Далее:

```text
New Request -> Change Classification -> Impact Analysis
 -> Affected Requirements / Decisions / Specification / Architecture
 -> Plan / Tests / Approvals Update
 -> Revalidation -> Replan -> Implementation -> Verification
```

Не считать каждое изменение просто новой задачей. Сохранять заменённые записи и указывать изменившийся scope. Изменение границ безопасности, deployment или внешних эффектов требует повторных проверок readiness и approvals.

### Зависимости генерируемого проекта

Целевым вариантом по умолчанию остаётся самостоятельно запускаемый и поддерживаемый генерируемый проект. Намеренная зависимость от Forge runtime, SDK, service, KeyCore-Hub или управляемой внешней инфраструктуры должна быть явной и оцениваться по совместимости версий, доступности, deployment, безопасности, portability, циклу обновлений, поведению при сбоях и возможности работать независимо. Это политика оценки, а не выбор архитектуры зависимостей.

По информации, предоставленной владельцем, KeyCore-Hub является существующим проектом. Его полностью проверенная архитектура и точная связь с Forge пока неизвестны. Запуск/управление проектом через него — только возможная интеграция. Mastery Creator остаётся необязательным кандидатом на механизм, а не универсальным runtime.

### Память и обучение во время эксплуатации

Project Memory продолжает существовать после генерации и различает текущее известное состояние и исторические записи. С учётом нерешённой политики хранения/удаления она может хранить исходную цель, утверждённые требования, архитектурные решения, изменения реализации, результаты проверки, наблюдения deployment, ошибки, исправления, отзывы пользователя и заменённые решения.

```text
Project Memory -> Observation -> Candidate Lesson -> Review
 -> Possible Forge Knowledge
```

Уроки проекта никогда не становятся глобальным знанием автоматически. Review учитывает повторяемость, root cause, качество evidence, независимые примеры, применимость, контрпримеры, фильтрацию privacy/security, approval, versioning и последующую перепроверку. Полномочия утверждать, делегировать, оспаривать, снимать с публикации или отменять глобальное знание остаются `DECISION REQUIRED`.

### Walkthrough согласованного жизненного цикла

Это концептуальные проверки, а не реализованное поведение.

| Случай | Ожидаемый результат |
| --- | --- |
| Новый простой проект | Discovery задаёт только существенные вопросы; ясные требования проходят соответствующий gate; простой цикл Specification/Architecture приводит к плану. |
| Существующая программа расходится с описанием владельца | Сохранить отдельно evidence из кода/runtime и намерение владельца; разрешить конфликт до утверждения затронутых требований. |
| Неясно, нужна ли browser capability | Оставить её условной; запросить evidence или уточнение, только если это меняет workflow или архитектуру. |
| Browser/UI есть, но Mastery Creator может не подойти | Сравнить кандидатов с требованиями; не выбирать Mastery Creator по умолчанию. |
| Architecture выявляет новое ограничение | Записать evidence, перепроверить требование, обновить Specification/Architecture, затем перепланировать. |
| Deployment меняется после approval архитектуры | Версионировать контекст, пометить зависимые решения, получить необходимые approvals и перепроверить до replanning. |
| Меняется граница безопасности | Запустить security impact analysis и запросить scoped approval, если требует политика; не полагаться на прежнее approval. |
| Исправлена ошибка после релиза | Записать ошибку, исправление и проверку в Project Memory; сохранить прежнее состояние в истории. |
| Одинаковая проверенная ошибка повторилась независимо | Создать Candidate Anti-Pattern на review; не публиковать глобальное правило автоматически. |
| План требует необратимого внешнего действия | Запросить отдельное scoped action approval до исполнения; прежнего подтверждения понимания/плана недостаточно. |

### Влияние на документацию и решения владельца

| Документ | Раздел | Изменение / причина | Источник и статус |
| --- | --- | --- | --- |
| `TECHNICAL_SPECIFICATION.md` | Жизненный цикл, границы артефактов, readiness, governance | Согласовать lifecycle как явное предложение; оставить открытыми порядок и политику approvals | Stage 1.4; **PROPOSED / DECISION REQUIRED** |
| `FORGE_VISION.md` | Без изменений | Существующий общий workflow совместим; детальный lifecycle относится к Technical Specification | Проверено на Stage 1.4 |
| `ARCHITECTURE.md` | Без изменений | Описывает текущую реализацию, а не целевое поведение lifecycle | Проверено на Stage 1.4; граница current/future |
| `ROADMAP.md` | Без изменений | Не планировать нерешённые вопросы governance/lifecycle как обязательства реализации | Проверено на Stage 1.4; **DECISION REQUIRED** |
| `AGENTS.md` | Без изменений | Правила разработки менять не нужно | Проверено на Stage 1.4 |
| `DECISIONS.md` | Без изменений | Предложенная модель ещё не является утверждённым архитектурным решением | Проверено на Stage 1.4 |

Решения владельца остаются открытыми для приоритета и approval Specification/Architecture; обязательности каждого approval для каждого проекта или применения risk-based policy; окончательной политики выбора технологий; допустимых зависимостей генерируемых проектов; связи с KeyCore-Hub; хранения/удаления Project Memory; полномочий, делегирования, оспаривания и deprecation Forge Knowledge. Этот раздел не принимает решения за владельца.