# Forge AI Development Rules

These rules apply to all AI agents working in the Forge AI repository.

## 1. Project safety

- Never read or modify files outside this repository for project work.
- Never expose, print, or commit secrets.
- Never create or modify a real `.env` file.
- Never commit API keys, tokens, passwords, credentials, or other secrets.
- Never force-push unless explicitly authorized for that operation.
- Never rewrite Git history unless explicitly authorized for that operation.
- Do not connect external AI providers or use credentials unless the task explicitly authorizes it.

## 2. Context economy

- Do not scan the entire repository for every task.
- At the start of a task, inspect `README.md`, this `AGENTS.md`, and only the documentation relevant to the task. If this root file does not exist yet, proceed with the README and relevant documentation.
- Identify the smallest set of files needed, and read only those files.
- Reuse documented project knowledge instead of rediscovering it.
- Avoid repeatedly reading files that have not changed.
- Do not ask multiple agents to independently scan the entire project unless the task explicitly requires a full audit.

## 3. Modularity

- Keep modules small and focused.
- Separate provider-specific logic from orchestration logic.
- Avoid unnecessary coupling.
- Prefer clear interfaces over hard-coded provider implementations.
- Add new functionality as isolated modules whenever practical.

## 4. Change control

- Before a significant change, inspect the existing implementation relevant to that change.
- For non-trivial tasks, make a short implementation plan before editing.
- Make the smallest safe change that solves the problem.
- Do not rewrite working code without a clear reason.
- Preserve existing behavior unless the task explicitly changes it.

## 5. Testing

- Add or update tests for important functionality.
- Run relevant tests after changes.
- Do not claim success without running the relevant verification.
- If tests fail, investigate the cause rather than hiding or bypassing the failure.

## 6. Git

- Keep commits focused and meaningful, and use descriptive commit messages when a commit is requested.
- Never commit secrets.
- Check Git status before and after significant work.
- Never force-push without explicit authorization.
- Never rewrite Git history without explicit authorization.

## 7. Multi-agent rules

Future agents may have these specialized roles:

- **Orchestrator:** coordinates work.
- **OpenAI/Codex:** implementation and execution.
- **Claude:** independent architecture and code review.
- **Gemini:** independent reasoning and large-context review.
- **Grok:** adversarial testing and edge cases.
- **Reviewer:** verifies proposed changes.
- **Tester:** validates functionality.

No agent should assume that another agent's output is automatically correct.

## 8. Decision making

- Document technical decisions when they affect architecture.
- The orchestrator reviews conflicting agent recommendations.
- When a decision materially affects architecture, the orchestrator explains why a proposed change was accepted or rejected.

## 9. Production safety

- Keep development, testing, and production environments clearly separated.
- Do not deploy to production automatically unless explicitly authorized.
- Destructive operations require explicit authorization.

## 10. Token and cost control

- Prefer targeted context over full-project context.
- Use the least expensive suitable model or agent for simple tasks.
- Use multiple agents only when independent perspectives provide meaningful value.
- Escalate to deeper reasoning only when task complexity justifies it.

## 11. Documentation

- Keep architecture documentation current.
- Record important architectural decisions in `docs/DECISIONS.md`.
- Maintain a clear project `README.md`.
