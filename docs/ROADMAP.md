# Forge AI — Roadmap

> This roadmap describes the evolution of Forge AI from the current orchestration foundation into the full multi-agent project execution system described in [FORGE_VISION.md](FORGE_VISION.md).

## Status legend

- **CURRENT** — already implemented in the repository.
- **FUTURE** — planned; not yet implemented.

The roadmap is capability-based and has no promised release dates. Checked
items describe the present implementation; unchecked items are future work.

## Phase 0 — Foundation

**CURRENT**

- [x] Project scaffold
- [x] Architecture rules
- [x] Runtime and configuration
- [x] Test infrastructure
- [x] Git safety rules

## Phase 1 — Provider Layer

**CURRENT**

- [x] OpenAI provider
- [x] Anthropic provider
- [x] Gemini provider
- [x] DeepSeek provider
- [x] OpenRouter provider
- [x] Groq provider
- [x] Provider capabilities metadata
- [x] Provider fallback
- [x] SecretStore

Provider adapters share the provider-neutral interface. Credentials are
resolved locally through SecretStore and are not part of provider metadata or
Git history.

## Phase 2 — Routing

**CURRENT**

- [x] Dispatcher
- [x] Automatic provider routing
- [x] Cost-aware routing
- [x] Deterministic task classification
- [x] Specialized routing by task category

Current task categories:

- `CODE`
- `ANALYSIS`
- `REVIEW`
- `OTHER`

Classification is deterministic and rule-based. A caller-supplied category or
explicit provider selection retains priority. Automatic routing uses declared
capabilities, configuration, and the existing cost-tier policy; it does not
use an LLM classifier or opaque scoring.

## Phase 3 — Multi-Agent Execution

**CURRENT**

- [x] Primary agent execution
- [x] Independent reviewer agent
- [x] Combined primary + review result
- [x] Safe handling of primary failures
- [x] Safe handling of reviewer failures
- [x] One bounded revision loop (Revision Loop v0.1)
- [x] Limited automatic correction cycle

The reviewer performs one assessment pass and does not modify files. The
reviewer role is independent from the primary role; current configuration may
route both through the same provider and model.

**FUTURE**

- [ ] Review policy engine

The current cycle runs at most once after `CHANGES_REQUESTED`; a second review
that still requests changes ends with `changes_requested`. Review or revision
failures preserve the available primary result without implying approval.

## Phase 4 — Planning

**CURRENT**

- [x] Deterministic Project Planner v0.1 templates
- [x] Planned tasks with explicit dependencies and initial readiness status

Planner v0.1 supports Telegram bot, web application, data/analysis, and generic
software goals. It creates a static plan only; tasks are not queued, executed,
or automatically updated as work completes.

**FUTURE**

- [ ] Large-task decomposition
- [ ] Task dependency graph
- [ ] Task queue
- [ ] Parallel task execution

### Intended end-to-end flow

```text
USER GOAL
   ↓
PROJECT PLAN
   ↓
TASKS
   ↓
DEPENDENCIES
   ↓
AGENT ASSIGNMENT
   ↓
EXECUTION
   ↓
REVIEW & VERIFICATION
   ↓
USER APPROVAL
   ↓
CONTROLLED CHANGE (GIT / DEPLOY)
```

The complete flow is a future direction. Today, Forge AI can create a static
deterministic plan, or accept an individual task, classify and route it,
execute one primary agent, and run a bounded review/revision workflow. Plan
execution, runtime dependency management, parallel execution, applying project
changes, and deployment are not implemented.
Changes to Git or production remain subject to explicit authorization.
