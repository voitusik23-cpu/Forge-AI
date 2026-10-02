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

Automatic routing is introduced separately in Dispatcher v0.2. Real
integrations for providers other than OpenAI and Anthropic remain future work.

## Dispatcher v0.2

`Task` carries a coarse `TaskCategory` and a provider-neutral `parameters`
mapping (including an optional model override and required capabilities).
`Dispatcher` applies a deterministic preference order, checks candidate
availability/configuration/capabilities, and tries each eligible candidate at
most once through the existing `AgentRegistry` and `Provider` adapter:

```text
Task(category, parameters)
  -> Orchestrator
  -> Dispatcher / DispatchPolicy
  -> AgentRegistry
  -> ProviderAgent
  -> Provider interface
  -> TaskResult
```

Coding prefers OpenAI then Anthropic; reasoning prefers Anthropic then OpenAI;
large-context uses Google/Gemini; cheap/free uses OpenRouter; fast/cheap uses
DeepSeek; other categories use `FORGE_DEFAULT_PROVIDER` and cost tiers. A
candidate must be enabled in `FORGE_ENABLED_PROVIDERS`, registered, have its
required API key, and support the requested task capabilities. OpenRouter's
model ID determines whether its capability tier is free (`:free` or
`openrouter/free`) or cheap. Paid candidates require
`FORGE_ALLOW_PAID_PROVIDERS=true`. After category candidates fail or are
unavailable, the finite `FORGE_PROVIDER_FALLBACK_CHAIN` is tried sequentially.
Explicit `provider_name` bypasses automatic choice and fallback; the caller's
selection is never replaced. Dispatcher uses only common registries, metadata,
and SecretStore presence checks, not provider SDKs.

## Task Classification + Specialized Routing v0.1

`TaskCategory` is shared by task models and routing. A caller-supplied category
is honored unchanged. When omitted, a small deterministic classifier examines
the task description, context, and parameters and assigns `code`, `analysis`,
`review`, or `other`. Review patterns take precedence over code patterns, then
analysis; unmatched tasks remain `other`. No model or external service is used
for classification.

For `code`, `analysis`, and `review`, the dispatcher filters providers by the
declarative `task_categories` capability metadata and then orders eligible
providers by the existing free, cheap, and permitted paid tiers. Other tasks
retain the existing cost-aware route, and legacy `TaskCategory` values remain
supported. The same configuration yields the same classification and provider
order. Explicit `provider_name` continues to override automatic routing, and
the configured finite fallback chain remains in effect after automatic
candidates. The `python -m app.smoke_classified_routing` command exercises one
real request for each new category.

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
mode, default provider/model, enabled providers, model overrides, timeout,
retry count, and log level. It is loaded
from the `FORGE_*` environment variables and validated before runtime assembly.
The configured default provider is used for tasks in the `other` category;
the v0.2 dispatcher applies the category policy for other task categories.

`ProviderConfig` remains separate and contains provider-specific metadata,
including only the name of an environment variable that may hold a credential.
Runtime settings do not load API keys, and provider configuration does not
resolve the referenced variable. Secrets are not logged or stored in Git.
Startup performs local dependency assembly only and makes no external API calls.

`ProviderAccountConfig` separately holds optional local account email metadata
for providers. It reads only `PROVIDER_*_EMAIL` values and does not put account
emails in provider responses, task results, or logs. API keys continue to be
resolved only through `SecretStore`.

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

## Provider Fallback v0.1

`RuntimeSettings.provider_fallback_chain`, loaded from the optional
`FORGE_PROVIDER_FALLBACK_CHAIN` comma-separated setting, supplies an ordered,
finite list tried after the category policy's primary provider. Dispatcher
checks each candidate through `ProviderRegistry` and its existing capability
metadata, then invokes its adapter through the registered agent. Dispatcher
attempts the primary once, then each distinct configured fallback at most once,
sequentially. Missing providers/agents and normalized provider execution
failures move to the next entry; success stops the chain. Exhaustion returns a
failed `TaskResult` containing the reasons. Explicit `provider_name` and legacy
`agent_name` dispatch bypass fallback. No scoring, retry loop, parallel calls,
or fallback based on cost/quality is involved.

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

`DeepSeekProvider`, `OpenRouterProvider`, and `GroqProvider` share the local
`OpenAICompatibleProvider` adapter and the existing OpenAI Python SDK, using
the Chat Completions interface with each service's configured base URL and
SecretStore key reference. Groq uses `GROQ_API_KEY` and
`https://api.groq.com/openai/v1`. `ProviderConfig.model_name` accepts provider
model IDs directly; OpenRouter's `openrouter/free` route is available as
`OpenRouterProvider.FREE_MODEL_ID` and can be selected as the configured model.
The runtime separately loads `FORGE_OPENROUTER_MODEL`, defaulting to
`cohere/north-mini-code:free`, without changing other providers' model settings.
The explicit `python -m app.smoke_openrouter` command sends one real request
through the runtime, dispatcher, `ProviderAgent`, and OpenRouter adapter. Tests
use fake clients; ordinary startup makes no request.
These adapters are invoked only by task dispatch, and tests use fake clients
only.

## Gemini provider

`GoogleProvider` implements Gemini through Google's official `google-genai`
SDK. It resolves `GEMINI_API_KEY` lazily through `SecretStore`, uses the model
from `FORGE_GEMINI_MODEL` (default `gemini-3.8-flash`), and maps text and usage
to the shared `ProviderResponse`. Startup and automated tests do not make API
requests. The explicit `python -m app.smoke_gemini` command performs one real
request.

## Provider Capabilities v0.1

`ProviderCapabilitiesRegistry` exposes immutable metadata for each built-in
provider: canonical name, API-key environment-variable name, streaming and tool
support, large-context support, coarse `free`/`cheap`/`paid` cost tier, and
`enabled_by_config`. The
key field contains only a variable name, never the key. `enabled_by_config`
mirrors the existing `ProviderConfig.enabled` flag. Capability metadata is
separate from provider request/response types. Dispatcher uses cost tiers for
the ordinary-task policy described below, while streaming/tool declarations
describe provider API capability in general. Tool support can depend on model,
and these declarations do not claim those features are implemented by Forge
AI's current text-only adapter. Cost tiers are labels, not price data or
calculated estimates.

## Cost-aware Provider Routing v0.1

For ordinary `TaskCategory.OTHER` tasks, `DispatchPolicy` orders registered
providers by the existing `ProviderCapabilitiesRegistry`: `free`, then
`cheap`, then `paid`. Candidates must be present in provider and agent
registries, enabled by configuration, have their required key, and satisfy
task capability requirements. Paid candidates are omitted unless the
`RuntimeSettings.allow_paid_providers` policy is enabled through
`FORGE_ALLOW_PAID_PROVIDERS=true`; its default is false. Each candidate is
tried through the existing bounded execution/fallback flow, so a missing
key/model moves to the next permitted candidate. A configured fallback chain
is retained after cost-ordered candidates. Explicit `provider_name` bypasses
cost selection and retains absolute priority. No actual costs are calculated,
and Dispatcher does not score by quality, latency, or budget.
