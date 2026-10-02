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
