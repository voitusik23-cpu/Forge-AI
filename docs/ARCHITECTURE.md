# Planned architecture

```text
User
  ↓
Forge AI Orchestrator
  ↓
Agent adapters
  ↓
OpenAI / Claude / Gemini / Grok
  ↓
Review / Testing
  ↓
Controlled changes
  ↓
Git
```

The orchestrator will coordinate tasks, manage the context provided to agents,
and route work. Provider-specific behavior, request formats, and response
handling must remain isolated behind agent/provider adapters. Adding or changing
a provider should not require rewriting orchestration logic.

Proposed supporting modules cover tasks, project memory, project-specific
knowledge, reviews, tools, and shared utilities. Changes should be reviewed and
validated before controlled application to a project. Git will keep the
authoritative project history and make significant changes traceable.

## Orchestrator v0.1

The initial orchestration core uses provider-neutral task and result models, an
agent interface, an in-memory registry, and a local mock agent. Dispatch is
explicit: the caller supplies `agent_name`; the orchestrator does not choose an
agent or automatically route between providers.

```text
Task
  -> Orchestrator
  -> Registry
  -> Agent
  -> TaskResult
```

This version does not connect to real AI providers. Real API integrations and
automatic routing remain future work.

## Provider Layer v0.1

The provider layer sits behind the existing Agent interface. The orchestrator
continues to dispatch to an explicitly selected agent; a generic `ProviderAgent`
adapts a task into a provider-neutral request and adapts the response back into
a `TaskResult`.

```text
User
  -> Orchestrator
  -> Agent
  -> Provider
  -> AI API (future integration only)
```

- **Orchestrator** dispatches a task to the caller-selected agent and does not
  contain provider-specific behavior.
- **Agent** receives the task and owns the generic task-to-result contract.
- **Provider** accepts a provider-neutral request and returns a provider-neutral
  response; future provider-specific protocol and SDK details stay behind its
  implementation.
- **AI API** is a future integration point. The v0.1 provider placeholders make
  no network requests.

`ProviderFactory` creates a provider only when its name is explicitly supplied.
`ProviderRegistry` manages provider instances separately from `AgentRegistry`.
Provider settings store only an environment variable name as a credential
reference; they never read or store the referenced secret. `MockProvider` is
the deterministic, offline implementation for tests.

## Runtime & Configuration Layer v0.1

The runtime is assembled explicitly and has no global singleton:

```text
Runtime
  -> Settings
  -> Registries
  -> Orchestrator
  -> Agent
  -> Provider
```

`RuntimeSettings` contains application-wide values such as environment, debug
mode, default provider/model, timeout, retry count, and log level. It is loaded
from the `FORGE_*` environment variables and validated before runtime assembly.
The configured default provider is used to construct the initial provider and
agent; it does not automatically route individual tasks.

`ProviderConfig` remains separate and contains provider-specific metadata,
including only the name of an environment variable that may hold a credential.
Runtime settings do not load API keys, and provider configuration does not
resolve the referenced variable. Secrets are not logged or stored in Git.
Startup performs local dependency assembly only and makes no external API calls.

## Execution Pipeline v0.1

```text
User
  -> Task
  -> Orchestrator
  -> ExecutionService (TaskExecutor)
  -> Agent
  -> Provider
  -> ProviderResponse
  -> TaskResult
  -> User
```

The user supplies a `Task` and explicitly names `agent_name`. The Orchestrator
delegates to `TaskExecutor`, which validates the task, looks up that one agent,
and normalizes expected domain/provider failures and empty results. It does not
select or route agents automatically. `ProviderAgent` adapts the task to a
provider-neutral request; the provider returns a `ProviderResponse` with output
and `Usage`. The agent carries provider, agent, and usage metadata into the
`TaskResult` returned to the caller. Unknown agents and invalid tasks raise
clear domain exceptions; unexpected exceptions are not swallowed.

`MockProvider` runs synchronously and offline, returning deterministic output
and zero token/cost usage. Provider-specific implementations remain behind the
Provider interface and outside the execution service.
