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

## Dispatcher v0.2

- Task routing uses a small deterministic category policy: coding prefers OpenAI then Anthropic; reasoning prefers Anthropic then OpenAI; large-context selects Google/Gemini; cheap/free selects OpenRouter; fast/cheap selects DeepSeek; other tasks use the configured default.
- Callers may explicitly choose a provider. Dispatcher v0.2 executes only one selected provider and does not add scoring, automatic fallback execution, or multi-agent execution.
- Task parameters are provider-neutral; the optional `model` parameter overrides the configured model for the chosen provider.
- Groq uses the common OpenAI-compatible adapter and existing OpenAI SDK with Groq's official OpenAI-compatible endpoint; its key is resolved by `SecretStore` under `GROQ_API_KEY`.
- Provider account email metadata is handled by `ProviderAccountConfig`, independently of `SecretStore`. It is local-only metadata and is never copied into `ProviderResponse` or `TaskResult` or written to logs.
