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
- Provide a Russian translation after canonical English technical/product documentation intended for both AI agents and the human project owner. Preserve the English text and its structure and meaning; do not translate code, paths, commands, identifiers, commit hashes, or technical literals unnecessarily.
- When documented architecture, product, safety, workflow, or other rules change, identify and update only the affected Forge documents, update their Russian translations where present, and check for contradictions. Do not mechanically edit every document after every change.
- Keep the document hierarchy clear: `AGENTS.md` defines agent rules; `FORGE_VISION.md` defines product direction; `TECHNICAL_SPECIFICATION.md` defines target architecture and constraints; `ARCHITECTURE.md` describes current implementation; `ROADMAP.md` defines implementation order and progress; `DECISIONS.md` records architectural decisions and rationale.
- Apply these translation and synchronization rules to future permanent documentation. Keep current implementation distinct from target/future architecture; update current-state, roadmap, architecture, specification, vision, or decision records only when the source change warrants it.

## 12. Operating contract for coding agents

These four rules are mandatory for every agent task. They are ordered: each one
assumes the previous one was followed.

### UNDERSTAND BEFORE ACT

- Inspect the relevant architecture and the existing implementation before
  editing anything. Read `docs/SOURCE_OF_TRUTH.md` for verified current state and
  `docs/ARCHITECTURE.md` for the subsystem you are touching.
- Never assume a planned or specified capability already exists. Verify it in
  `app/` before relying on it, and never describe planned work in the present
  tense.
- Never rely on a previous chat, a review document, or a stage document as
  evidence of current behavior. The code and the passing test suite are the
  evidence.

### SMALLEST SUFFICIENT CHANGE

- Make the smallest change that fully solves the stated problem. Prefer
  removing duplicated implementations over adding another one.
- Do not rewrite working code without a clear reason, and do not restructure
  unrelated subsystems.
- Do not resolve a backward-compatibility question in the permissive direction
  when the change is security-relevant.

### SURGICAL SCOPE

- Do not silently expand scope. If the task grows beyond what was asked, stop and
  report the discovered scope instead of implementing it.
- Never modify unrelated uncommitted work in the working tree. Inspect
  `git status` first and leave other people's or other agents' pending changes
  untouched.
- Never modify a real `.env` file, and never touch secret values.

### DONE MUST BE PROVEN

- Tests are required for important functionality. A security fix requires a
  regression test that fails before the fix and passes after it.
- Run the full test suite before declaring completion, not only the touched
  test files.
- Run `python -m compileall -q app tests` and `git diff --check` before
  completion and report their results.
- Inspect `git diff` before any commit, keep commits scoped to the task, and
  never commit secrets.
- Do not claim success without the verification evidence. If a check could not
  be run, say so explicitly.
- Do not push, and do not commit, unless the task explicitly requests it.


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
- Добавляйте перевод на русский язык после канонического английского текста технической и продуктовой документации, предназначенной как для AI-агентов, так и для владельца проекта. Сохраняйте английский текст, его структуру и смысл; не переводите без необходимости код, пути, команды, идентификаторы, хеши коммитов и технические literals.
- При изменении задокументированных архитектурных, продуктовых, безопасностных, рабочих или иных правил определяйте и обновляйте только затронутые документы Forge, обновляйте имеющиеся русские переводы и проверяйте документы на противоречия. Не изменяйте механически все документы после каждого изменения.
- Соблюдайте иерархию документов: `AGENTS.md` задаёт правила для агентов; `FORGE_VISION.md` описывает направление продукта; `TECHNICAL_SPECIFICATION.md` определяет целевую архитектуру и ограничения; `ARCHITECTURE.md` описывает текущую реализацию; `ROADMAP.md` задаёт порядок реализации и прогресс; `DECISIONS.md` фиксирует архитектурные решения и их обоснование.
- Применяйте эти правила перевода и синхронизации ко всей будущей постоянной документации. Отделяйте текущую реализацию от целевой и будущей архитектуры; обновляйте описание текущего состояния, roadmap, архитектуру, спецификацию, vision или записи решений только если этого требует исходное изменение.

## 12. Рабочий контракт для coding-агентов

Эти четыре правила обязательны для каждой задачи агента. Они упорядочены: каждое
предполагает выполнение предыдущего.

### UNDERSTAND BEFORE ACT (сначала понять, потом действовать)

- Перед любым редактированием изучите относящуюся к задаче архитектуру и существующую реализацию. Проверенное текущее состояние — в `docs/SOURCE_OF_TRUTH.md`, описание подсистемы — в `docs/ARCHITECTURE.md`.
- Никогда не предполагайте, что запланированная или описанная в спецификации возможность уже реализована. Проверьте её в `app/`, прежде чем на неё опираться, и никогда не описывайте запланированную работу в настоящем времени.
- Никогда не считайте предыдущий чат, документ ревью или stage-документ доказательством текущего поведения. Доказательство — код и проходящий набор тестов.

### SMALLEST SUFFICIENT CHANGE (минимальное достаточное изменение)

- Вносите минимальное изменение, которое полностью решает поставленную задачу. Предпочитайте удаление дублирующих реализаций добавлению ещё одной.
- Не переписывайте работающий код без веской причины и не перестраивайте посторонние подсистемы.
- Если изменение затрагивает безопасность, не решайте вопрос обратной совместимости в разрешающую сторону.

### SURGICAL SCOPE (хирургическая область изменений)

- Не расширяйте область задачи молча. Если задача разрастается за пределы запрошенного, остановитесь и сообщите об обнаруженной области вместо её реализации.
- Никогда не изменяйте постороннюю незакоммиченную работу в рабочем дереве. Сначала проверьте `git status` и оставьте чужие и ожидающие изменения нетронутыми.
- Никогда не изменяйте настоящий файл `.env` и не касайтесь значений секретов.

### DONE MUST BE PROVEN (готово только то, что доказано)

- Для важной функциональности тесты обязательны. Исправление безопасности требует регрессионного теста, который падает до исправления и проходит после.
- Перед объявлением завершения запускайте полный набор тестов, а не только затронутые файлы.
- Перед завершением запустите `python -m compileall -q app tests` и `git diff --check` и сообщите их результаты.
- Перед любым коммитом просмотрите `git diff`, держите коммиты в границах задачи и никогда не коммитьте секреты.
- Не заявляйте об успехе без доказательств проверки. Если проверку выполнить не удалось, скажите об этом прямо.
- Не делайте push и не делайте commit, если задача явно этого не требует.
