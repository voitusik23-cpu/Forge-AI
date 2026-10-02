# Planned architecture

```text
User
  ↓
Forge AI Orchestrator
  ↓
Agent adapters
  ↓
OpenAI / Anthropic / Gemini / xAI / DeepSeek / OpenRouter
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

Automatic routing and real integrations for providers other than OpenAI and
Anthropic remain future work.

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
  response; provider-specific protocol and SDK details stay behind its
  implementation.
- **AI API** integrations remain isolated inside provider implementations.

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

## OpenAI Provider v0.1

`OpenAIProvider` uses the official OpenAI Python SDK and its Responses API. It
is called only when an explicitly dispatched task reaches the provider; runtime
startup, normal health checks, and offline tests make no API requests. The
provider reads the API key from `OPENAI_API_KEY` at generation time and never
copies it into `ProviderConfig`. The SDK key and request data are not logged.
`output_text` becomes the provider-neutral output, Responses usage supplies
input/output token counts, and estimated cost remains unset. Authentication,
rate-limit, timeout, connection/API, and malformed-response failures become
safe provider errors; unexpected exceptions continue to propagate. Google and
xAI provider adapters remain unconfigured, and task routing stays explicit.

## Local secrets

`SecretStore` lazily resolves a provider's environment-variable reference from
the process environment first, then the repository-root `.env` file. It does
not load secrets into `RuntimeSettings`, `ProviderConfig`, or process-wide
environment state. Providers receive the shared store through `ProviderFactory`
and request their own key only when generating. `.env` is ignored by Git;
`.env.example` contains blank key fields and remains tracked as documentation.
The same lookup flow supports OpenAI, Anthropic, Gemini, xAI, DeepSeek, and
OpenRouter adapters.

## Anthropic Provider v0.1

`AnthropicProvider` uses the official Python SDK Messages API when a task is
explicitly dispatched to it. It resolves `ANTHROPIC_API_KEY` through the shared
`SecretStore` at generation time, sends the prompt and serialized context as a
user message, and maps text blocks and token usage into `ProviderResponse`.
Startup and offline tests do not make requests.

## OpenAI-Compatible Providers v0.1

`DeepSeekProvider` and `OpenRouterProvider` share the local
`OpenAICompatibleProvider` adapter and the existing OpenAI Python SDK, using
the Chat Completions interface with each service's configured base URL and
SecretStore key reference. `ProviderConfig.model_name` accepts provider model
IDs directly; OpenRouter's `openrouter/free` route is available as
`OpenRouterProvider.FREE_MODEL_ID` and can be selected as the configured model.
Neither provider is selected automatically, and tests use fake clients only.
