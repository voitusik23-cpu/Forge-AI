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

---

# Правила разработки Forge AI — русская версия

Эти правила распространяются на всех AI-агентов, работающих в репозитории Forge AI.

## 1. Безопасность проекта

- В рамках работы над проектом не читайте и не изменяйте файлы за пределами этого репозитория.
- Никогда не раскрывайте, не выводите и не коммитьте секреты.
- Никогда не создавайте и не изменяйте настоящий файл `.env`.
- Никогда не коммитьте API-ключи, токены, пароли, учётные данные или другие секреты.
- Никогда не выполняйте force push, если это явно не разрешено для данной операции.
- Никогда не переписывайте историю Git, если это явно не разрешено для данной операции.
- Не подключайте внешних AI-провайдеров и не используйте credentials, если задача явно этого не разрешает.

## 2. Экономное использование контекста

- Не сканируйте весь репозиторий для каждой задачи.
- В начале задачи изучите `README.md`, этот `AGENTS.md` и только документацию, относящуюся к задаче. Если этого корневого файла ещё нет, продолжайте работу, используя README и релевантную документацию.
- Определите минимальный набор необходимых файлов и читайте только их.
- Используйте уже задокументированные сведения о проекте вместо повторного изучения того, что уже известно.
- Не перечитывайте без необходимости файлы, которые не изменялись.
- Не поручайте нескольким агентам независимо сканировать весь проект, если задача явно не требует полного аудита.

## 3. Модульность

- Делайте модули небольшими и специализированными.
- Отделяйте логику конкретных провайдеров от логики оркестрации.
- Избегайте ненужной связанности.
- Предпочитайте понятные интерфейсы жёстко заданным реализациям провайдеров.
- По возможности добавляйте новую функциональность в виде изолированных модулей.

## 4. Контроль изменений

- Перед существенным изменением изучите относящуюся к задаче существующую реализацию.
- Для нетривиальных задач составьте краткий план реализации до начала редактирования.
- Вносите минимальное безопасное изменение, решающее задачу.
- Не переписывайте работающий код без веской причины.
- Сохраняйте существующее поведение, если задача явно не требует его изменить.

## 5. Тестирование

- Добавляйте или обновляйте тесты для важной функциональности.
- После изменений запускайте релевантные тесты.
- Не заявляйте об успехе, не выполнив соответствующую проверку.
- Если тесты завершаются ошибкой, выясните причину, а не скрывайте её и не обходите проверку.

## 6. Git

- Делайте коммиты сфокусированными и содержательными; используйте понятные сообщения коммитов, когда создание коммита запрошено.
- Никогда не коммитьте секреты.
- Проверяйте `git status` до и после существенной работы.
- Никогда не выполняйте force push без явного разрешения.
- Никогда не переписывайте историю Git без явного разрешения.

## 7. Правила работы нескольких агентов

В будущей работе у агентов могут быть следующие специализированные роли:

- **Orchestrator:** координирует работу.
- **OpenAI/Codex:** реализация и выполнение задач.
- **Claude:** независимая проверка архитектуры и кода.
- **Gemini:** независимый анализ и работа с большим контекстом.
- **Grok:** проверка с поиском слабых мест и граничных случаев.
- **Reviewer:** проверяет предлагаемые изменения.
- **Tester:** проверяет функциональность.

Ни один агент не должен считать результат другого агента автоматически верным.

## 8. Принятие решений

- Документируйте технические решения, если они влияют на архитектуру.
- Оркестратор рассматривает рекомендации агентов, которые противоречат друг другу.
- Если решение существенно влияет на архитектуру, оркестратор объясняет, почему предложенное изменение принято или отклонено.

## 9. Безопасность production-среды

- Чётко разделяйте среды разработки, тестирования и production.
- Не выполняйте автоматический деплой в production, если это явно не разрешено.
- Для деструктивных операций требуется явное разрешение.

## 10. Контроль токенов и затрат

- Предпочитайте целевой контекст перед передачей контекста всего проекта.
- Для простых задач используйте подходящую модель или агента с наименьшей стоимостью.
- Используйте несколько агентов только тогда, когда их независимые мнения дают существенную пользу.
- Переходите к более глубокому анализу только тогда, когда этого требует сложность задачи.

## 11. Документация

- Поддерживайте архитектурную документацию в актуальном состоянии.
- Фиксируйте важные архитектурные решения в `docs/DECISIONS.md`.
- Поддерживайте понятный `README.md` проекта.
