# Architectural decisions

- Project name: Forge AI.
- Forge AI is intended to be a reusable AI development and orchestration
  platform.
- The architecture must be modular.
- AI providers must be isolated behind adapters.
- The orchestrator coordinates agents rather than containing
  provider-specific logic.
- Secrets must never be stored in Git.
- Git is the source of truth for project history.
- Significant changes must be traceable.
- Agents should receive only the context required for their task whenever
  possible.
- The system must actively optimize token and context usage.
- Agents should not repeatedly scan the entire project unnecessarily.
- Autonomous production changes must be controlled.
- New functionality should be developed in small, understandable modules.
- Tests should accompany important functionality.
- "В Orchestrator v0.1 выбор агента выполняется явно через agent_name. Автоматическая маршрутизация между AI-провайдерами откладывается до отдельного этапа."
- Provider-specific code is isolated from the orchestrator.
- Every provider implementation uses the common provider interface.
- OpenAI v0.1 uses the official Python SDK and Responses API; other real provider
  SDK integrations remain deferred.
- Secrets are not stored in code or Git; provider configuration may hold only an environment variable name reference.
- MockProvider is used for deterministic tests without network access.
- Runtime configuration is separate from provider-specific configuration.
- Secrets are never stored in Git.
- Application startup does not make external API calls.
- Runtime components are assembled through an explicit bootstrap function.
- The configured default provider is not an automatic task-routing mechanism.
- Execution flow is separated from provider-specific implementation.
- The Orchestrator does not execute provider-specific code.
- Providers return provider-neutral responses.
- Usage metadata is carried with the task result.
- MockProvider is used for deterministic end-to-end testing in v0.1.
- Automatic agent/provider routing is not implemented in v0.1.
- The OpenAI API key is read only from the local process environment at explicit
  generation time and is never stored in configuration or logged.
- OpenAI uses `client.responses.create`; startup and ordinary offline tests do
  not perform inference.
- Provider credentials are resolved lazily by a shared `SecretStore`, with
  process environment values taking precedence over the ignored repository-root
  `.env` file. Secret values never enter runtime/provider configuration.
- Anthropic v0.1 uses the official Python SDK Messages API; inference happens
  only when a task is explicitly dispatched to its provider.
- DeepSeek and OpenRouter share an OpenAI-compatible Chat Completions adapter
  and the existing OpenAI SDK; their endpoint, key name, and provider name are
  isolated in their small provider modules.
- OpenRouter model IDs are passed through `ProviderConfig`; `openrouter/free`
  is an explicit configurable model choice, not an automatic routing default.
- OpenRouter has a dedicated `FORGE_OPENROUTER_MODEL` runtime setting, defaulting
  to `cohere/north-mini-code:free`, independent of the general default model.
  `python -m app.smoke_openrouter` is an explicit opt-in real-request check;
  automated tests use fake clients and do not contact the provider.

## Dispatcher v0.2

- Task routing uses a small deterministic category policy: coding prefers OpenAI then Anthropic; reasoning prefers Anthropic then OpenAI; large-context selects Google/Gemini; cheap/free selects OpenRouter; fast/cheap selects DeepSeek; other tasks use the configured default.
- Callers may explicitly choose a provider. Dispatcher v0.2 executes only one selected provider and does not add scoring, automatic fallback execution, or multi-agent execution.
- Task parameters are provider-neutral; the optional `model` parameter overrides the configured model for the chosen provider.
- Groq uses the common OpenAI-compatible adapter and existing OpenAI SDK with Groq's official OpenAI-compatible endpoint; its key is resolved by `SecretStore` under `GROQ_API_KEY`.
- Provider account email metadata is handled by `ProviderAccountConfig`, independently of `SecretStore`. It is local-only metadata and is never copied into `ProviderResponse` or `TaskResult` or written to logs.

## Provider Fallback v0.1

- Fallback is an optional ordered finite provider list configured by `FORGE_PROVIDER_FALLBACK_CHAIN`. The dispatcher tries the policy-selected primary first, then each distinct configured provider sequentially after a missing provider or failed execution.
- A successful attempt ends dispatch. Chain exhaustion returns a failed `TaskResult` with per-provider reasons. Explicit provider or agent selection is never silently changed.
- Fallback does not use cost/quality scoring, retries, parallel calls, or multi-agent execution.

## Provider Capabilities v0.1

- Provider capabilities are immutable declarative metadata kept outside the provider interface. The registry mirrors `ProviderConfig.enabled` and API-key variable names without resolving secrets.
- Streaming/tool booleans describe general provider API support and may vary by model; they do not imply that Forge AI's current adapter implements streaming or tool execution.
- Cost metadata is represented only by a coarse `free`, `cheap`, or `paid` label; it does not calculate API cost.

## Cost-aware Provider Routing v0.1

- Ordinary tasks use existing `ProviderCapabilitiesRegistry` cost tiers in `free`, `cheap`, `paid` order, limited to providers registered with both provider and agent registries.
- Paid providers are excluded by default and may be permitted with `FORGE_ALLOW_PAID_PROVIDERS=true`. This policy does not calculate token prices or apply cost/quality scoring.
- Explicit `provider_name` selection overrides cost ordering. The existing bounded fallback chain runs after cost-ordered candidates and remains sequential.

## Deterministic provider routing

- Automatic routing checks provider registration, the configured enabled set, required API-key presence, task capability requirements, and cost policy before executing a candidate. Candidate preference and fallback order are deterministic and each provider is attempted at most once.
- `FORGE_ENABLED_PROVIDERS` defaults to `mock,openrouter`. An OpenRouter model ending in `:free` (or `openrouter/free`) is declared free for routing; other OpenRouter models remain cheap. Paid providers, including Gemini, require `FORGE_ALLOW_PAID_PROVIDERS=true` for automatic routing.
- Gemini uses Google's official `google-genai` SDK, `GEMINI_API_KEY` through `SecretStore`, and `FORGE_GEMINI_MODEL`. Its default model is `gemini-3.8-flash`. The `python -m app.smoke_gemini` command is opt-in and performs a real request; unit tests use fake clients.

## Revision Loop v0.1

- Multi-agent execution permits at most one primary revision after the reviewer returns `CHANGES_REQUESTED`, followed by one final review.
- The revision receives the original task, initial primary response, and review feedback. The task category and any explicit primary provider selection are retained.
- A further `CHANGES_REQUESTED` ends the workflow without another revision. Revision and reviewer failures preserve the available primary result and never imply approval.

## Planner v0.1

- Project plans are generated locally from deterministic templates; no provider or LLM is called.
- Planner reuses `TaskCategory` and assigns stable task IDs and a deterministic plan ID from the normalized goal.
- Template tasks form an explicit sequential dependency chain. The first task starts `READY`; dependent tasks start `PENDING`. Planner v0.1 creates plans only and does not execute or update task statuses.
