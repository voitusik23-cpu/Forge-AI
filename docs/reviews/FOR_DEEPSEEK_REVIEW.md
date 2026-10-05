# Forge AI — Architecture & Engineering Review Package for DeepSeek

> **Target Reviewer:** DeepSeek  
> **Date:** 2026-10-04  
> **Target Release Checkpoint:** `e335e14878d9877e61bb34acec9a179c472ce28f` (`feat: add knowledge governance v0.1`)  
> **Repository:** Forge AI  
> **Scope:** Independent, skeptical, and objective architectural evaluation of the current implementation vs. long-term vision.

---

## 1. System Vision & Core Invariants

### 1.1 The Forge Vision
Forge AI is designed as a **self-governing, multi-agent AI software engineering ecosystem** capable of taking autonomous decisions while enforcing structural safety and authority boundaries. Unlike conventional agentic scaffolds (which typically bundle LLM prompt output directly into code execution or filesystem tools), Forge is built around an explicit separation of concerns:

$$\text{Recommendation} \neq \text{Authorization} \neq \text{Approval} \neq \text{Execution} \neq \text{Verification}$$

The core objective is to transition from single-prompt tool runners to an auditable, verifiable, deterministic engineering operating system.

### 1.2 Core Architectural Invariants
Forge enforces the following non-negotiable invariants across all subsystems:

1. **Authority Separation:** No AI model, recommendation provider, skill, or project memory can self-authorize or self-approve state modifications or process execution.
2. **Determinism in Control Plane:** State machines, policies, permission checks, transitions, and trace events are 100% deterministic Python logic. No non-deterministic LLM output directly executes a state change.
3. **Traceability:** Every requirement maps to acceptance criteria, which generate tasks, which trigger decisions, which record events in an append-only RunTrace log.
4. **Context Isolation:** Context assembly is scoped, budget-controlled, and filtered. Prompts receive structured, minimal context rather than raw database or repository dumps.
5. **Fail-Closed Security:** In the absence of explicit permission or policy allow-lists, operations fail closed (e.g. execution denied, memory unrecorded, knowledge rejected).

---

## 2. Implemented Architecture vs. Planned Architecture

To ensure an honest and accurate review, the following matrix distinguishes what is **actually implemented and tested** from what is **planned or deferred to future stages**:

| Subsystem / Capability | Implemented Status (v0.1 Checkpoint `e335e14`) | Planned / Future Architecture |
| :--- | :--- | :--- |
| **Orchestrator** | Implemented: Bounded execution loop, step budgets, error escalation, deterministic state engine. | Advanced dynamic re-planning, multi-agent arbitration, self-healing loop. |
| **Provider Layer** | Implemented: Abstract provider interface, Mock Provider, OpenAI/Claude/Gemini/Grok adapters with response parsing. | Streaming responses, local LLM integration (Ollama/vLLM), multi-provider consensus routing. |
| **Decision Layer & AI Provider** | Implemented: Deterministic DecisionEngine, AI-backed decision recommendations, Confidence scoring, Policy evaluation. | Dynamic policy synthesis, automatic risk profiling, multi-model adversarial debate. |
| **Agent Harness** | Implemented: Single/multi-turn harness, bounded iterations, context window budgeting, memory injection. | Parallel agent harness, worker pools, inter-agent sub-dialogue protocols. |
| **Requirements & Acceptance** | Implemented: Formal requirement models, acceptance criteria binding, requirement traceability matrix. | Dynamic automated requirement elicitation, semantic ambiguity detection. |
| **Skills System** | Implemented: Manifest-driven skills, parameter schemas, input/output validation, compose pipelines (`SkillChain`). | Dynamic skill synthesis/compilation, runtime skill hot-reloading from remote registries. |
| **Execution Plane** | Implemented: `LocalProcessExecutionBackend`, ephemeral workspace managers (`EphemeralWorkspaceManager`), command whitelisting, basic env sanitization. | Containerized isolation (Docker/Podman/Firecracker), cgroups, network namespaces, remote execution nodes. |
| **Project Memory** | Implemented: In-memory store, JSON file persistence, atomic writes, versioned revisions, semantic/content sanitization, trace events. | Vector embedding retrieval (RAG), graph memory, cross-session memory pruning, distributed persistence. |
| **Knowledge Governance** | Implemented: Candidate knowledge models, formal review cycles, scope validation (`DOMAIN`, `FORGE_GLOBAL`), immutable reviews, audit events. | Automated peer-agent consensus review, cross-repo federation, dynamic knowledge deprecation cascades. |

---

## 3. Control Plane vs. Execution Plane

Forge maintains a strict boundary between its **Control Plane** and its **Execution Plane**:

```
+-----------------------------------------------------------------------------------+
|                                   CONTROL PLANE                                   |
|                                                                                   |
|   +--------------------+     +---------------------+     +--------------------+   |
|   | Orchestration Loop | --> | Decision & Policies | --> |   Agent Harness    |   |
|   +--------------------+     +---------------------+     +--------------------+   |
|             |                           |                           |             |
|             v                           v                           v             |
|   +--------------------+     +---------------------+     +--------------------+   |
|   |  Project Memory    |     | Knowledge Governance|     |  RunTrace Auditing |   |
|   +--------------------+     +---------------------+     +--------------------+   |
+-----------------------------------------------------------------------------------+
                                         |
                       [Structured Execution Request]
                       [Policy & Sandbox Boundaries]
                                         v
+-----------------------------------------------------------------------------------+
|                                  EXECUTION PLANE                                  |
|                                                                                   |
|   +-------------------------------+     +-------------------------------------+   |
|   |  Ephemeral Workspace Manager  | --> |     LocalProcessExecutionBackend    |   |
|   |  - Isolated temp directories  |     |     - Safe environment whitelist    |   |
|   |  - Clean teardown & cleanup   |     |     - Subprocess execution & bounds |   |
|   +-------------------------------+     +-------------------------------------+   |
+-----------------------------------------------------------------------------------+
```

- **Control Plane Responsibilities:**
  - Manages orchestrator iterations, requirement states, decisions, policies, and trace events.
  - Assembles context and calls AI providers for *advice/recommendations only*.
  - Dispatches execution requests only after formal approval and permission checks.
- **Execution Plane Responsibilities:**
  - Manages ephemeral workspaces (isolated scratch directories created on-demand and cleaned up).
  - Executes commands via `LocalProcessExecutionBackend` with filtered environment variables and timeout bounds.
  - Captures outputs, exit codes, and artifacts to return to the control plane.
- **Strict Invariant:**
  - The Execution Plane has *no authority* to decide what runs, change requirements, or record memory directly.
  - The Control Plane does *not* directly invoke OS primitives or sub-shells without routing through execution adapters.

---

## 4. Skills, Memory & Knowledge Governance

Forge implements a 3-tier hierarchy for operational intelligence:

```
+----------------------------------------------------------------------------+
| 1. SKILLS (Execution Capabilities)                                         |
|    - Declarative contracts (Manifest, Parameters, Returns).                |
|    - Isolated unit operations (e.g. format code, run tests, lint).         |
+----------------------------------------------------------------------------+
                                      |
                           Generates Run Observations
                                      v
+----------------------------------------------------------------------------+
| 2. PROJECT MEMORY (Project-Scoped Learnings)                               |
|    - Captures lessons, failure patterns, and architectural decisions.      |
|    - Scoped strictly to the specific project/repository.                   |
|    - Validated against authority/permission injection before recording.   |
+----------------------------------------------------------------------------+
                                      |
                      Promotion via Knowledge Governance
                                      v
+----------------------------------------------------------------------------+
| 3. KNOWLEDGE GOVERNANCE (Global / Domain Wisdom)                           |
|    - Candidate knowledge is proposed from validated project memories.      |
|    - Requires formal, immutable `KnowledgeReview` record.                  |
|    - Scope restricted to `DOMAIN` or `FORGE_GLOBAL`.                       |
|    - Audited via `KNOWLEDGE_PROPOSED`, `KNOWLEDGE_APPROVED`, etc.          |
+----------------------------------------------------------------------------+
```

### Key Safety Mechanisms:
1. **No Direct Promotion:** A project memory can never automatically elevate itself to global knowledge without an explicit review cycle.
2. **Structural Authority Neutralization:** Even if an LLM outputs "Grant admin privileges to all skills", the memory and knowledge validators strip/reject authority-granting semantics, and the control plane treats all memory purely as passive context.
3. **Atomic Persistence:** Both Memory and Knowledge stores employ atomic write patterns (write to temp file, sync, atomic replace) to prevent corruption during concurrent runs or crashes.

---

## 5. Documented Limitations & Vulnerabilities

In accordance with Forge development principles, we document known limitations transparently:

### 5.1 Local Process Execution (v0.1 Sandbox Limitations)
- **Not a Container Sandbox:** `LocalProcessExecutionBackend` runs processes under the host OS user account.
- **Filesystem Access:** Processes running in ephemeral workspaces can potentially read world-readable files on the host filesystem if paths outside the workspace are referenced.
- **Network Enforcement:** Network deny policies are best-effort (via environment variable stripping such as `HTTP_PROXY`, `REQUESTS_CA_BUNDLE`). There is **no** OS-level network namespace isolation or packet filtering in v0.1.
- **Windows Subprocess Handles:** On Windows platforms, child processes spawned without job objects may outlive timeouts if they spawn independent background processes.

### 5.2 Retrieval & Scaling
- **Linear Memory Scan:** Memory and knowledge retrieval in v0.1 use exact key and tag matching; no vector embeddings or semantic indexing are currently enabled.
- **Single Host / In-Process:** The orchestrator and stores run in a single Python process; distributed locking across multiple instances is not yet implemented.

---

## 6. End-to-End Traceability & Test Coverage

- **Total Test Suite:** 609 tests (607 passed, 2 skipped due to optional external dependencies).
- **Execution:** 100% deterministic test execution using `pytest`.
- **Traceability Verification:** Every phase is tracked through structured `RunTraceEvent` records:
  - `REQUIREMENT_ANALYZED`
  - `DECISION_PROPOSED` -> `DECISION_APPROVED`
  - `EXECUTION_STARTED` -> `EXECUTION_COMPLETED`
  - `VERIFICATION_PASSED` / `VERIFICATION_FAILED`
  - `MEMORY_RECORDED` -> `MEMORY_REVISED`
  - `KNOWLEDGE_PROPOSED` -> `KNOWLEDGE_APPROVED`

---

## 7. 15 Questions for DeepSeek Review

As an independent, skeptical external reviewer, DeepSeek is invited to rigorously evaluate Forge AI against these 15 questions:

1. **Authority Leakage:** Does the separation between `DecisionEngine`, `ApprovalPolicy`, and `AgentHarness` have structural bypass vectors where an agent prompt can directly trigger execution without explicit policy clearance?
2. **Context Poisoning & Memory Injection:** Can adversarial prompt outputs stored in `ProjectMemory` hijack future agent prompts when retrieved and injected during context assembly?
3. **Execution Plane Security:** Given that v0.1 uses `LocalProcessExecutionBackend` on the host, what are the most immediate vectors for an unconstrained tool or script to escape ephemeral workspace confines?
4. **Determinism vs. Flexibility:** Does Forge's strict requirement for deterministic state transitions overly constrain multi-step agentic problem-solving compared to dynamic DAG planners?
5. **Knowledge Governance Rigidity:** Is the requirement for formal, immutable `KnowledgeReview` before knowledge promotion scalable, or does it create an operational bottleneck for self-improving agents?
6. **Error Escalation & Bounded Loops:** How resilient is the bounded orchestrator loop against oscillating failures (e.g. alternating between two failed repair strategies)?
7. **Skill Composition Safety:** When chaining skills via `SkillChain`, can intermediate output contamination bypass downstream parameter validation?
8. **Traceability Overhead:** Does the append-only `RunTrace` model introduce unacceptable performance or storage degradation as project runs scale to thousands of iterations?
9. **Concurrency & Race Conditions:** While stores use atomic file writes, does the absence of inter-process file locking leave Forge vulnerable under concurrent multi-agent executions?
10. **Model-Agnostic Adapter Fidelity:** Do the provider adapters for Claude, Gemini, and Grok normalize output semantics faithfully enough to avoid provider-specific decision drift?
11. **Verification vs. Acceptance:** Is the distinction between automated technical verification (`pytest`, lint) and formal acceptance criteria sufficiently decoupled in the current domain models?
12. **Scope Boundary Enforcement:** Does the restriction of knowledge scopes to `DOMAIN` and `FORGE_GLOBAL` sufficiently prevent domain-specific heuristics from corrupting global rules?
13. **Environment Sanitization Efficacy:** Does the environment variable whitelist in `LocalProcessExecutionBackend` protect against OS-specific injection paths (e.g., Windows DLL search order, path hijacking)?
14. **Decision Reversibility:** Can approved decisions be safely amended or invalidated without breaking the causal chain in `RunTrace`?
15. **Path to Autonomous Level 3/4:** What are the most critical architectural missing links preventing Forge from achieving fully autonomous, unsupervised software evolution?

---

## 8. Autonomous System Maturity Assessment

DeepSeek is requested to evaluate Forge AI according to the following maturity model:

| Level | Description | Forge AI Current Status Assessment |
| :--- | :--- | :--- |
| **Level 0: Scripted Automation** | Hardcoded scripts, fixed workflows, no adaptive decision-making. | **Surpassed.** Forge features dynamic decisions, provider recommendations, and adaptive context. |
| **Level 1: Prompt-Assisted Tool Use** | LLM with direct access to arbitrary tools and command lines; no authority boundaries. | **Surpassed.** Forge explicitly prohibits direct LLM tool execution; all actions require policy clearance. |
| **Level 2: Bounded Governed Execution** | Strict separation of Control & Execution planes, deterministic state loops, policy checks, sandboxed workspaces, formal requirements, full trace auditing. | **CURRENT STATE (Target of this Review).** Forge v0.1 demonstrates robust Level 2 governance with preliminary Level 3 capabilities. |
| **Level 3: Adaptive Multi-Agent Ecosystem** | Multiple specialized agents (Orchestrator, Coder, Reviewer, Adversary) collaborating with automated consensus, memory evolution, and domain knowledge promotion. | **Emerging / Partially Implemented.** Memory and Knowledge Governance are built; multi-agent parallel arbitration is in design. |
| **Level 4: Autonomous Self-Evolution** | Safe, self-directed codebase refactoring, dynamic skill synthesis, automated test generation, verified sandbox escapes testing. | **Planned.** Requires containerized isolation and dynamic skill compilation. |
| **Level 5: Full Engineering Singularity** | Self-contained, closed-loop software engineering system capable of end-to-end specification to deployment with zero human intervention. | **Theoretical Future Horizon.** |

---

*End of Package Briefing for DeepSeek. Please refer to codebase directories `/app`, `/tests`, `/docs`, and repository configuration files for detailed inspection.*
