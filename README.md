# Forge AI

Forge AI is a planned reusable platform for coordinating AI-assisted software
development, review, testing, and improvement across projects.

## Development stage

This repository contains the initial scaffold only: a small Python startup
health check, environment-backed settings placeholders, package structure, and
architecture notes. It does not connect to AI providers or modify projects.
The scaffold uses only the Python standard library.

## Long-term purpose and architecture

The planned system will coordinate multiple agents through a central
orchestrator. Provider adapters will isolate provider-specific behavior for
OpenAI/Codex, Anthropic/Claude, Google/Gemini, and xAI/Grok. Supporting modules
will handle tasks, project memory, context and token use, reviews, testing, and
controlled changes. See [the architecture plan](docs/ARCHITECTURE.md) and
[planned agent roles](docs/AGENTS.md).

Git will be the source of truth for project history, and significant changes
should be traceable. Secrets must never be committed to Git; keep credentials
in environment variables or an appropriately secured local environment. The
`.env.example` file contains empty placeholders only.

## Getting started

Run the startup health check from the repository root:

```bash
python -m app
```

Run the basic test with:

```bash
python -m unittest discover -s tests
```
