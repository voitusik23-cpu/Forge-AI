# Forge AI — Owner's Manual (beta)

This guide is written for the **owner of the product**, not for a developer. It describes what the
interface really does today, what the backend really stores, and what Forge will never claim on
its own. Technical names (`COMPLETED`, `/api/tasks/run`, `.forge/projects`) are kept as they are,
because those exact words appear on the screen and in the files.

Two rules explain almost every behaviour below:

* **A status is a fact from the backend, not a mood of the screen.** If the screen says a project is
  `COMPLETED`, the project record itself says so — and the record only reaches that status when the
  run's verification *and* acceptance both passed.
* **A refusal is information.** When an action is impossible, Forge shows the backend's own answer
  (for example `HTTP 409 · illegal_project_transition`) instead of hiding the button. If a button
  seems to do nothing, read the message under it.

Contents — the fourteen points:

1. Start a project
2. Answer the questions
3. Confirm the specification
4. Where the project appears
5. How to know the project is ready
6. What to do on `NEEDS_CHANGES`
7. Add a feature
8. Create a new version
9. View the history
10. Archive and restore
11. Check the provider status
12. Why `NO_CREDITS`, `NETWORK_BLOCKED`, "no credentials" or `NOT_CHECKED`
13. Check the Forge version
14. How to read the run events

---

## 1. Start a project

A project is born from an **idea**, on the «Кузница» screen, not from a "create project" button.

1. Open «Кузница» (the anvil icon).
2. In **Идея проекта** write one or two sentences in your own words: what should work and for whom.
   Example: *"A hotel booking service with search, payment and notifications."*
3. Press **Продолжить**.

That call opens a *discovery session*: the backend reads your sentence and decides which questions
it still needs to ask. Nothing is planned and nothing is executed at this step.

## 2. Answer the questions

The backend asks at most six questions, and only about the areas it could not derive from your
text: goal, project type, users, core features, platform, integrations, data storage, auth,
notifications, constraints, acceptance criteria, priorities.

* Write the answer in **your own words**, in the field under the question. An empty field is not
  sent.
* A question marked **обязательный** (required) is one the backend needs; the others can stay empty.
* **Пропустить уточнения** ends the round early. It does not skip the next step: you still confirm
  the specification explicitly.
* If an answer looks like a password, a token or an API key, Forge replaces it with a redaction
  marker (`[REDACTED:...]`) before storing it. The value never reaches the specification, the
  project record or a file. Do not type real credentials into these fields.

## 3. Confirm the specification

On step 3 the screen shows the **specification**: what the backend understood, field by field. An
empty field means *unknown* — never a guess.

Press **Подтвердить** (step 3). The backend freezes the specification, and this is the exact moment
the project record is created. Your project now exists as a real, stored record with its own id
(`proj-xxxxxxxxxxxx`), and the response also returns a `project` summary so the interface knows the
id without a second request.

After confirmation the session state is `CONFIRMED`. Only a `CONFIRMED` session can be run: a run
attempt before that is refused with `HTTP 409 · discovery_not_confirmed`.

## 4. Where the project appears

Every project owns one folder inside the Forge repository:

```
.forge/projects/<project_id>/
    <project_id>.json          the project record (status, specification, versions, verdicts)
    workspace/                 the project's working tree — the files a run wrote
.forge/projects/index.json     the list + the link from a confirmed session to its project
```

The «Проекты» screen shows the path **relative to the repository**, in the block
**«Где находится проект»**:

```
Workspace: proj-fddee46d6079
Project folder: .forge/projects/proj-fddee46d6079/workspace
Files: 3
Last version: v2
```

`Workspace` is the workspace id the record names, `Project folder` is where the files really are,
`Files` is how many files the current version recorded, `Last version` is the version number the
project advanced to. The interface never prints an absolute path (`C:\...`): a path in a payload is
always repository-relative.

## 5. How to know the project is ready

Look at the **status badge** on the project row and on the card. The word is always printed next to
the colour, so the colour alone never carries the meaning.

| Status | What it means for you |
| --- | --- |
| `DISCOVERY` | The project came from an idea; requirements are being collected. |
| `NEEDS_CLARIFICATION` | Answers are needed before planning. |
| `READY_FOR_PLANNING` | There is enough specification; the next step is the plan. |
| `PLANNED` | A plan exists; no recorded run yet. |
| `WAITING_FOR_CONFIRMATION` | The specification has not been confirmed. |
| `RUNNING` | A run is in progress. |
| `REVIEWING` | The run finished; its result is being judged. |
| `NEEDS_CHANGES` | One of the verdicts did not pass — see point 6. |
| `COMPLETED` | **Ready.** Verification and acceptance both passed. |
| `FAILED` | The run finished with an error. |
| `ARCHIVED` | Frozen: restore it before anything else. |

`COMPLETED` is the only "ready" answer, and it is deliberately hard to reach: a run that wrote no
files can never be `COMPLETED`, because there is nothing that could have passed verification. The
project card also shows the two verdicts behind the status:

* **Проверка** (`verification`): `passed`, `failed`, `not_applicable`, `not_run` or `pending`;
* **Приёмка** (`acceptance`): `passed`, `failed`, `not_evaluated` or `pending`.

## 6. What to do on `NEEDS_CHANGES`

`NEEDS_CHANGES` means the run finished but one of the verdicts did not pass, so the project did not
qualify as complete. The card's **Итог выполнения** block shows:

* **what failed** — the two verdicts in plain words;
* **why** — the run's own classification (`outcome`, `reason`, `detail`) read from the run record,
  with its evidence tags. If the record cannot be read, the card says that instead of inventing a
  reason;
* **affected files** — the artifact list of the version, with size and `create`/`update`;
* two actions:
  * **Создать доработку** — describe what to fix in «Добавить функцию / новая версия»;
  * **Повторить проверку** — re-run the verification of the *recorded* files. No model is called:
    every recorded file must still exist at its recorded size and SHA-256, and every `.py` file
    must still compile. If that passes and the recorded acceptance passed, the project moves to
    `COMPLETED`; otherwise it honestly stays in `NEEDS_CHANGES`.

## 7. Add a feature

In the project card, section **«Добавить функцию / новая версия»**:

1. Write what to change, in your own words: *"Add a CSV export of the monthly report."*
2. Choose the **provider** and the **model** (the list is what the backend reported; an empty list
   means the backend decides).
3. Decide about **Записать изменения в проект**. For the offline provider `mock` the switch is
   disabled and the run is labelled **«ответ без изменения файлов»** — that provider answers without
   touching a file, so promising a write would be a lie.
4. Press **Создать версию**. The backend stages the next version and returns the exact **task text**
   it composed from your request and the project specification. The screen shows that text
   unchanged, then offers **Отправить задачу в Core**.

**One honest limitation.** The task submitted from this screen carries no `discovery_session_id`:
the backend keeps the session↔project link in `index.json` and does not publish it in the project
record. A version is therefore *filled* by a run that carries the confirmed session — the run you
start from «Кузница» after confirming a session. This is stated in the form itself, under the
button, rather than hidden.

## 8. Create a new version

A version is **one recorded attempt** at the project. Versions are append-only:

* `v1` is created with the project; `v2`, `v3`… are staged by «Создать версию»;
* a staged version has `run_id` empty and the mark **ожидает запуска** (`pending`);
* when a run finishes and is attached to the project, the version is filled with that run's id, its
  artifact files, its verification and its acceptance;
* a failed run never overwrites an earlier version: the previous version stays current and the
  project becomes `FAILED`.

Staging a new version is refused in some statuses. An archived project answers
`HTTP 409 · illegal_project_transition` with the sentence *"an archived project is frozen: restore it
before any other event"* — this is the real backend answer, shown as it arrived.

## 9. View the history

Press **История** in the project card (or click a version row). The **История версий** block reads
`GET /api/projects/{id}/history` and lists every version, newest first, with:

* the version number `vN`, its creation time;
* the run id (`run_id`), or *ожидает запуска* when no run filled it;
* verification and acceptance in words;
* provider · model, file count, and the change request that produced it.

The same version rows are clickable on the card: selecting one shows **that** version's files, read
from `GET /api/projects/{id}/files?version=N`.

## 10. Archive and restore

* **Архивировать** freezes the project without deleting anything: the record, its versions, its
  files and its workspace all stay. The status becomes `ARCHIVED`, and the status it had before is
  stored in `previous_status`.
* While archived, every other action is refused with `HTTP 409` — that is intentional; a frozen
  project cannot gain a version behind your back.
* **Восстановить** returns the project to exactly the status recorded in `previous_status`. If that
  value is somehow missing, the backend refuses instead of inventing a status.

Archived projects are hidden behind the **Архив** filter on the «Проекты» screen; **Активные**
shows the rest.

## 11. Check the provider status

Three places, all read-only:

* **«Провайдеры»** — the readiness table from `/api/models` (each provider with its reason and its
  model count), plus links to the screens below.
* **`/api/status`** — the summary: `provider_status`, `providers_ready` / `providers_total`,
  `default_provider`. The top bar of the interface shows the same numbers.
* **Балансы** (on «Провайдеры» / «Расходы») — each provider's own balance, with
  `connection_status`, the amounts, and a `stale` flag when the figure is the last confirmed value
  after a failed refresh. A missing amount is *unknown*, never zero.

## 12. Why `NO_CREDITS`, `NETWORK_BLOCKED`, "no credentials" or `NOT_CHECKED`

These are the backend's own reasons, not guesses:

* **No credentials configured** → `NOT_CONFIGURED`: the key is not set in the environment or the
  secret store. Forge does not ask you for a key in the interface.
* **Key rejected** → `AUTH_FAILED`: the provider answered that the key is invalid or unauthorised.
* **`NO_CREDITS`** → the account authenticated but has no balance or credits left. The project is
  fine; the provider is not.
* **`NETWORK_BLOCKED`** → a connection error, a geo/firewall block or a timeout. Nothing was sent,
  or nothing came back.
* **`NOT_CHECKED`** → the runtime deliberately did not probe the provider (for example a test
  runtime that must not open a socket). The state is *unknown*, and Forge says so rather than
  reporting a verdict it never observed.

What you should do: fix the provider on the «Провайдеры» screen, then repeat the run. Forge never
prints a key or a header value anywhere in the interface; the reasons above are words, not secrets.

## 13. Check the Forge version

* `GET /api/version` returns the running version, the commit the backend was started from (full and
  short), when it resolved that identity, the backend state and the build time of the interface
  bundle.
* The same facts are in **`/api/status` → `details`** and on the **«Справка»** screen, in the line
  under the title: version · short commit · backend and UI build times.
* `unknown` and `missing` are honest answers: an unresolved fact is never filled with a plausible
  value.

Use this line when you report a problem: the version and the commit identify exactly what you ran.

## 14. How to read the run events

Every run is a durable record, and the interface reads it back rather than remembering it:

* `GET /api/runs/{run_id}` returns `state`, `status`, `event_count`, `last_sequence_number`,
  `attempt_number`, `interrupted`, `project_state` and a `classification` computed from the
  persisted snapshot (`outcome`, `reason`, `detail`, `evidence`, `is_terminal`, `is_success`).
* On «Кузница», the execution card shows those fields verbatim, and the **Ход выполнения** list
  walks the nine stages of the pipeline. A stage is marked **подтверждено** only when the record
  proves it: one stage per four persisted events, and the last stage only when the record is
  terminal *and* successful. There is **no percentage** anywhere — the backend publishes no
  per-step document and no rate, so a drawn percentage would be invented.
* When the run is terminal, the card prints the terminal state and **stops** its animation; an
  unproven stage stays **ожидается**. With reduced motion (Windows setting, or Settings → reduced
  motion) the list is plain text with no animation at all.
* On «Проекты» the same classification supplies the "why" of a `NEEDS_CHANGES` or `FAILED` project.

A record that says `COMPLETED` is a run that finished; it is not automatically a success. The
project status is the verdict, and it comes from verification and acceptance — never from the fact
that a run ended.

## 15. Run the project on this machine

**What "project created" means in practice.** A project exists when its record is on disk — the
confirmed specification, the status, the versions and the verdicts — and its workspace folder holds
the files a run really wrote:

```
.forge/projects/<project_id>/<project_id>.json   the record
.forge/projects/<project_id>/workspace/          the files the current version wrote
.forge/projects/<project_id>/run.log             the redacted, bounded log of the local process
```

"Created" is not "ready". The card says **ГОТОВО** only when the current version has at least one
file, its verification passed, its acceptance passed and the workspace still exists. A folder with
no file is **НЕ СОЗДАН**, not a project. Every path Forge publishes is relative to the repository;
an absolute host path (`C:\...`) never appears in a payload or on screen.

**Run a Python project.** The entry point is detected from the recorded artifact manifest only —
`app.py`, `main.py` or `server.py` in the project root, or a root package with `__main__.py`. Forge
runs it with this machine's own Python interpreter. Nothing is installed and no dependency is
resolved.

**Run a Node project.** `server.js` or `app.js` in the root runs as `node <file>`; a `package.json`
runs only a declared `start` or `dev` script, through the local `npm` CLI. Forge never runs
`npm install`: when the declared script needs packages that are not present, the run is reported as
missing dependencies instead of installing them.

**Open the preview.** When the process binds a loopback port, Forge publishes the URL, and
**«Открыть preview»** opens it. The port is observed, never declared, so a process that binds no port
has no URL. A static project (`index.html`) has a preview and no process.

**Stop the process.** **«Остановить»** ends the whole process tree and unregisters the preview.
Stopping is always accepted, and it is the safe move when anything looks wrong.

**When the run does not start.** The run payload says what happened, and each answer has one next
step:

| What the payload says | What it means | What to do |
| --- | --- | --- |
| `RUN_BLOCKED` · `runtime_missing` | the interpreter or Node is not installed here | install it, then run again |
| `RUN_BLOCKED` · dependencies | the declared script needs packages that are not installed | install them explicitly, then run again |
| `REFUSED` · `approval_required` | no explicit approval, or no digest | confirm the command whose digest is shown |
| `REFUSED` · `approval_stale` | the command changed after the approval | read the new command and confirm again |
| `REFUSED` · `command_not_allowed` | the command is outside the allow-list | nothing to do from the interface: the allow-list is the boundary |
| `REFUSED` · `unsafe_entry_path` / `entry_missing` | the entry path escapes the workspace, or the file is gone | check the project's files |
| `TIMED_OUT` | the process outlived the timeout and its tree was killed | run again, or stop and read the log |
| `FAILED` with `exit_code` | the process exited non-zero | read the log; the card shows the same state |

## 16. What needs your confirmation

Three actions are never automatic, and each one fails closed:

1. **Writing the project's files.** A run writes only in write mode, and only through the guarded
   writer: every path is validated, every `.py` file must compile, and the report names what was
   created or updated. An answer that carries no file leaves the project exactly where it was.
2. **Launching the process.** The command is resolved on the server, and the interface shows the
   exact command and its digest. Nothing starts without `{"confirm": true}` plus that digest, and
   one approval buys exactly one start.
3. **Paying for a model.** A model priced `PAID` runs only when you name **both** the provider and
   the model *and* the deployment enables paid providers. Otherwise the request is refused before
   anything is dispatched: nothing was sent and nothing was spent, and the refusal names the pair
   and its published rate.

`NEEDS_CHANGES`, `FAILED`, `REFUSED`, `RUN_BLOCKED` and `TIMED_OUT` are all honest answers, and none
of them is `COMPLETED`.

---

# Forge AI — руководство владельца (beta)

Это руководство написано для **владельца продукта**, а не для программиста. Оно описывает, что
интерфейс действительно делает сегодня, что backend действительно хранит и чего Forge никогда не
заявит сам. Технические слова (`COMPLETED`, `/api/tasks/run`, `.forge/projects`) оставлены как есть,
потому что именно они видны на экране и в файлах.

Почти всё поведение ниже объясняют два правила:

* **Статус — это факт из backend, а не настроение экрана.** Если экран пишет `COMPLETED`, это
  записано в самой записи проекта — и запись доходит до этого статуса только когда пройдены и
  проверка, и приёмка запуска.
* **Отказ — это информация.** Когда действие невозможно, Forge показывает собственный ответ
  backend (например `HTTP 409 · illegal_project_transition`), а не прячет кнопку. Если кнопка
  «ничего не делает», прочитайте сообщение под ней.

Содержание — четырнадцать пунктов:

1. Начать проект
2. Ответить на вопросы
3. Подтвердить спецификацию
4. Где появляется проект
5. Как понять, что проект готов
6. Что делать при `NEEDS_CHANGES`
7. Добавить функцию
8. Создать новую версию
9. Посмотреть историю
10. Архивировать и восстановить
11. Проверить состояние провайдеров
12. Почему `NO_CREDITS`, `NETWORK_BLOCKED`, «нет ключа» или `NOT_CHECKED`
13. Проверить версию Forge
14. Как читать события запуска

---

## 1. Начать проект

Проект рождается из **идеи**, на экране «Кузница», а не из кнопки «создать проект».

1. Откройте «Кузницу» (значок наковальни).
2. В поле **Идея проекта** напишите одно-два предложения своими словами: что должно работать и для
   кого. Пример: *«Сервис бронирования отелей с поиском, оплатой и уведомлениями»*.
3. Нажмите **Продолжить**.

Этот вызов открывает *сессию уточнений*: backend читает ваше предложение и решает, какие вопросы
ещё нужно задать. На этом шаге ничего не планируется и не выполняется.

## 2. Ответить на вопросы

Backend задаёт не более шести вопросов и только по тем областям, которые не удалось вывести из
вашего текста: цель, тип проекта, пользователи, ключевые функции, платформа, интеграции, хранение
данных, вход и доступ, уведомления, ограничения, критерии приёмки, приоритеты.

* Пишите ответ **своими словами** в поле под вопросом. Пустое поле не отправляется.
* Вопрос с пометкой **обязательный** нужен backend; остальные можно оставить пустыми.
* **Пропустить уточнения** завершает раунд досрочно. Это не пропускает следующий шаг:
  спецификацию вы всё равно подтверждаете явно.
* Если ответ похож на пароль, токен или API-ключ, Forge заменяет его маркером редактирования
  (`[REDACTED:...]`) до сохранения. Значение не попадает ни в спецификацию, ни в запись проекта, ни
  в файл. Не вводите настоящие ключи в эти поля.

## 3. Подтвердить спецификацию

На шаге 3 экран показывает **спецификацию**: что backend понял, поле за полем. Пустое поле значит
*неизвестно* — никогда не догадку.

Нажмите **Подтвердить** (шаг 3). Backend фиксирует спецификацию, и именно в этот момент создаётся
запись проекта. Проект теперь существует как реальная сохранённая запись со своим идентификатором
(`proj-xxxxxxxxxxxx`), а ответ дополнительно возвращает сводку `project`, чтобы интерфейс знал
идентификатор без второго запроса.

После подтверждения состояние сессии — `CONFIRMED`. Запускать можно только подтверждённую сессию:
попытка раньше отклоняется с `HTTP 409 · discovery_not_confirmed`.

## 4. Где появляется проект

У каждого проекта одна папка внутри репозитория Forge:

```
.forge/projects/<project_id>/
    <project_id>.json          запись проекта (статус, спецификация, версии, вердикты)
    workspace/                 рабочее дерево проекта — файлы, которые записал запуск
.forge/projects/index.json     список + связь подтверждённой сессии с её проектом
```

Экран «Проекты» показывает путь **относительно репозитория**, в блоке **«Где находится проект»**:

```
Workspace: proj-fddee46d6079
Project folder: .forge/projects/proj-fddee46d6079/workspace
Files: 3
Last version: v2
```

`Workspace` — идентификатор рабочей области из записи, `Project folder` — где реально лежат файлы,
`Files` — сколько файлов записала текущая версия, `Last version` — номер версии, до которой дошёл
проект. Интерфейс никогда не печатает абсолютный путь (`C:\...`): путь в ответе всегда относительный
к репозиторию.

## 5. Как понять, что проект готов

Смотрите на **значок статуса** в строке проекта и в карточке. Слово всегда написано рядом с цветом,
поэтому один цвет ничего не значит.

| Статус | Что это значит для вас |
| --- | --- |
| `DISCOVERY` | Проект создан из идеи; идёт сбор требований. |
| `NEEDS_CLARIFICATION` | Нужны ответы, без них нельзя планировать. |
| `READY_FOR_PLANNING` | Спецификации достаточно; следующий шаг — план. |
| `PLANNED` | План есть; записанного запуска ещё нет. |
| `WAITING_FOR_CONFIRMATION` | Спецификация не подтверждена. |
| `RUNNING` | Запуск выполняется. |
| `REVIEWING` | Запуск завершён; результат разбирается. |
| `NEEDS_CHANGES` | Один из вердиктов не пройден — см. пункт 6. |
| `COMPLETED` | **Готово.** Проверка и приёмка пройдены. |
| `FAILED` | Запуск завершился ошибкой. |
| `ARCHIVED` | Заморожен: сначала восстановите. |

`COMPLETED` — единственный ответ «готово», и он намеренно труднодостижим: запуск, который не записал
ни одного файла, не может стать `COMPLETED`, потому что проверять было нечего. Карточка проекта
показывает два вердикта, из которых складывается статус:

* **Проверка** (`verification`): `passed`, `failed`, `not_applicable`, `not_run` или `pending`;
* **Приёмка** (`acceptance`): `passed`, `failed`, `not_evaluated` или `pending`.

## 6. Что делать при `NEEDS_CHANGES`

`NEEDS_CHANGES` значит: запуск завершился, но один из вердиктов не пройден, поэтому проект не стал
завершённым. Блок **«Итог выполнения»** показывает:

* **что не прошло** — оба вердикта простыми словами;
* **почему** — собственная классификация запуска (`outcome`, `reason`, `detail`) из записи запуска,
  вместе с тегами оснований. Если запись не читается, карточка так и пишет, а не выдумывает причину;
* **затронутые файлы** — список файлов версии с размером и `create`/`update`;
* два действия:
  * **Создать доработку** — опишите, что исправить, в блоке «Добавить функцию / новая версия»;
  * **Повторить проверку** — повторная проверка *записанных* файлов. Модель не вызывается: каждый
    записанный файл должен по-прежнему существовать с записанным размером и SHA-256, а каждый файл
    `.py` — компилироваться. Если это проходит и записанная приёмка пройдена, проект переходит в
    `COMPLETED`; иначе он честно остаётся в `NEEDS_CHANGES`.

## 7. Добавить функцию

В карточке проекта, раздел **«Добавить функцию / новая версия»**:

1. Напишите, что изменить, своими словами: *«Добавить экспорт месячного отчёта в CSV»*.
2. Выберите **провайдера** и **модель** (список — это то, что сообщил backend; пустой список значит,
   что провайдера выбирает он сам).
3. Решите про **Записать изменения в проект**. Для offline-провайдера `mock` переключатель
   заблокирован, а запуск помечен **«ответ без изменения файлов»** — этот провайдер отвечает, не
   трогая файлы, и обещать запись было бы обманом.
4. Нажмите **Создать версию**. Backend подготавливает следующую версию и возвращает точный **текст
   задачи**, составленный из вашей просьбы и спецификации проекта. Экран показывает этот текст без
   изменений и предлагает **Отправить задачу в Core**.

**Одно честное ограничение.** Задача, отправленная с этого экрана, не несёт `discovery_session_id`:
backend хранит связь «сессия ↔ проект» в `index.json` и не публикует её в записи проекта. Поэтому
версию *заполняет* запуск, который несёт подтверждённую сессию — тот, что вы запускаете из «Кузницы»
после подтверждения сессии. Это написано в самой форме, под кнопкой, а не спрятано.

## 8. Создать новую версию

Версия — это **один записанный подход** к проекту. Версии только добавляются:

* `v1` создаётся вместе с проектом; `v2`, `v3`… подготавливает «Создать версию»;
* у подготовленной версии `run_id` пуст и стоит пометка **ожидает запуска** (`pending`);
* когда запуск завершается и привязывается к проекту, версия заполняется его идентификатором,
  файлами, проверкой и приёмкой;
* неудачный запуск никогда не перезаписывает предыдущую версию: прежняя версия остаётся текущей, а
  проект становится `FAILED`.

В некоторых статусах подготовка новой версии отклоняется. Архивный проект отвечает
`HTTP 409 · illegal_project_transition` с фразой *«an archived project is frozen: restore it before
any other event»* — это настоящий ответ backend, показанный как есть.

## 9. Посмотреть историю

Нажмите **История** в карточке проекта (или строку версии). Блок **«История версий»** читает
`GET /api/projects/{id}/history` и перечисляет все версии, новые сверху, с:

* номером версии `vN` и временем создания;
* идентификатором запуска (`run_id`) или пометкой *ожидает запуска*, если запуска не было;
* проверкой и приёмкой словами;
* провайдером · моделью, числом файлов и просьбой об изменении, из которой версия появилась.

Те же строки версий кликабельны в карточке: выбранная версия показывает **свои** файлы, прочитанные
из `GET /api/projects/{id}/files?version=N`.

## 10. Архивировать и восстановить

* **Архивировать** замораживает проект, ничего не удаляя: запись, версии, файлы и рабочая область
  остаются. Статус становится `ARCHIVED`, а прежний статус сохраняется в `previous_status`.
* Пока проект в архиве, любое другое действие отклоняется с `HTTP 409` — это намеренно:
  замороженный проект не может получить версию в обход вас.
* **Восстановить** возвращает проект ровно в тот статус, что записан в `previous_status`. Если
  значение почему-то отсутствует, backend отказывает, а не придумывает статус.

Архивные проекты скрыты за фильтром **Архив** на экране «Проекты»; **Активные** показывает
остальные.

## 11. Проверить состояние провайдеров

Три места, все только для чтения:

* **«Провайдеры»** — таблица готовности из `/api/models` (каждый провайдер с причиной и числом
  моделей) и ссылки на экраны ниже.
* **`/api/status`** — сводка: `provider_status`, `providers_ready` / `providers_total`,
  `default_provider`. Верхняя панель интерфейса показывает те же числа.
* **Балансы** (на «Провайдерах» / «Расходах») — собственный баланс каждого провайдера с
  `connection_status`, суммами и флагом `stale`, когда это последнее подтверждённое значение после
  неудачного обновления. Отсутствующая сумма — это *неизвестно*, а не ноль.

## 12. Почему `NO_CREDITS`, `NETWORK_BLOCKED`, «нет ключа» или `NOT_CHECKED`

Это собственные причины backend, а не догадки:

* **Ключ не настроен** → `NOT_CONFIGURED`: ключа нет в переменных окружения или в хранилище
  секретов. Интерфейс не спрашивает у вас ключ.
* **Ключ отклонён** → `AUTH_FAILED`: провайдер ответил, что ключ неверный или неавторизован.
* **`NO_CREDITS`** → аккаунт авторизовался, но баланс или кредиты закончились. С проектом всё в
  порядке; проблема у провайдера.
* **`NETWORK_BLOCKED`** → ошибка соединения, блокировка по географии/фаерволу или таймаут. Либо
  ничего не ушло, либо ничего не вернулось.
* **`NOT_CHECKED`** → runtime намеренно не опрашивал провайдера (например, тестовый режим, которому
  запрещено открывать сокет). Состояние *неизвестно*, и Forge так и говорит, вместо вердикта,
  которого не наблюдал.

Что делать: привести провайдера в порядок на экране «Провайдеры» и повторить запуск. Forge никогда и
нигде в интерфейсе не печатает ключ или значение заголовка; причины выше — это слова, а не секреты.

## 13. Проверить версию Forge

* `GET /api/version` возвращает работающую версию, коммит, из которого запущен backend (полный и
  короткий), время определения этой идентичности, состояние backend и время сборки интерфейса.
* Те же факты есть в **`/api/status` → `details`** и на экране **«Справка»**, в строке под заголовком:
  версия · короткий коммит · время сборки backend и UI.
* `unknown` и `missing` — честные ответы: неопределённый факт никогда не заполняется правдоподобным
  значением.

Используйте эту строку, когда сообщаете о проблеме: версия и коммит точно определяют, что у вас
запущено.

## 14. Как читать события запуска

Каждый запуск — это сохранённая запись, и интерфейс читает её заново, а не держит в памяти:

* `GET /api/runs/{run_id}` возвращает `state`, `status`, `event_count`, `last_sequence_number`,
  `attempt_number`, `interrupted`, `project_state` и `classification`, посчитанную по сохранённому
  снимку (`outcome`, `reason`, `detail`, `evidence`, `is_terminal`, `is_success`).
* На «Кузнице» карточка выполнения показывает эти поля дословно, а список **«Ход выполнения»**
  проходит девять этапов конвейера. Этап помечается **подтверждено** только когда запись это
  доказывает: один этап на каждые четыре записанных события, а последний этап — только когда запись
  терминальна *и* успешна. **Процента нет нигде** — backend не публикует ни пошаговый документ, ни
  темп, поэтому нарисованный процент был бы выдумкой.
* Когда запуск терминален, карточка печатает терминальное состояние и **останавливает** анимацию;
  неподтверждённый этап остаётся **ожидается**. При уменьшенной анимации (настройка Windows или
  Settings → уменьшенная анимация) список становится простым текстом без анимации вовсе.
* На «Проектах» та же классификация даёт «почему» для проекта в `NEEDS_CHANGES` или `FAILED`.

Запись, которая говорит `COMPLETED`, — это запуск, который завершился; он не обязательно успешен.
Вердикт — это статус проекта, и он складывается из проверки и приёмки, а не из факта, что запуск
закончился.

## 15. Запустить проект на этой машине

**Что значит «проект создан» на практике.** Проект существует, когда его запись лежит на диске —
подтверждённая спецификация, статус, версии и вердикты — и его папка workspace содержит файлы,
которые запуск действительно записал:

```
.forge/projects/<project_id>/<project_id>.json   запись проекта
.forge/projects/<project_id>/workspace/          файлы текущей версии
.forge/projects/<project_id>/run.log             очищенный и ограниченный лог локального процесса
```

«Создан» — это не «готов». Карточка говорит **ГОТОВО** только когда у текущей версии есть хотя бы
один файл, её проверка пройдена, её приёмка пройдена и workspace всё ещё существует. Папка без
файлов — это **НЕ СОЗДАН**, а не проект. Любой путь, который публикует Forge, относителен
репозитория; абсолютный путь хоста (`C:\...`) не появляется ни в payload, ни на экране.

**Запустить Python-проект.** Точка входа определяется только по записанному манифесту артефактов —
`app.py`, `main.py` или `server.py` в корне проекта, либо корневой пакет с `__main__.py`. Forge
запускает её собственным Python-интерпретатором этой машины. Ничего не устанавливается и никакая
зависимость не подбирается.

**Запустить Node-проект.** `server.js` или `app.js` в корне запускается как `node <file>`;
`package.json` запускает только объявленный скрипт `start` или `dev`, через локальный `npm` CLI.
Forge никогда не выполняет `npm install`: если объявленному скрипту нужны отсутствующие пакеты,
запуск сообщает об отсутствующих зависимостях вместо их установки.

**Открыть preview.** Когда процесс занимает loopback-порт, Forge публикует URL, и **«Открыть
preview»** открывает его. Порт наблюдается, а не объявляется, поэтому процесс, который не занял
порт, URL не имеет. У статического проекта (`index.html`) есть preview и нет процесса.

**Остановить процесс.** **«Остановить»** завершает всё дерево процессов и снимает регистрацию
preview. Остановка принимается всегда, и это безопасное действие, когда что-то выглядит не так.

**Когда запуск не стартовал.** Payload запуска говорит, что произошло, и у каждого ответа есть
следующий шаг:

| Что в payload | Что это значит | Что делать |
| --- | --- | --- |
| `RUN_BLOCKED` · `runtime_missing` | интерпретатор или Node здесь не установлен | установить, затем запустить снова |
| `RUN_BLOCKED` · зависимости | объявленному скрипту нужны неустановленные пакеты | установить их явно, затем запустить снова |
| `REFUSED` · `approval_required` | нет явного подтверждения или нет отпечатка | подтвердить команду, отпечаток которой показан |
| `REFUSED` · `approval_stale` | команда изменилась после подтверждения | прочитать новую команду и подтвердить снова |
| `REFUSED` · `command_not_allowed` | команда вне allow-list | из интерфейса делать нечего: allow-list — это граница |
| `REFUSED` · `unsafe_entry_path` / `entry_missing` | путь точки входа выходит из workspace или файл исчез | проверить файлы проекта |
| `TIMED_OUT` | процесс прожил дольше таймаута, и его дерево было убито | запустить снова или остановить и прочитать лог |
| `FAILED` с `exit_code` | процесс завершился с ненулевым кодом | прочитать лог; карточка показывает то же состояние |

## 16. Что требует вашего подтверждения

Три действия никогда не происходят автоматически, и каждое отказывает в закрытую сторону:

1. **Запись файлов проекта.** Запуск пишет только в write mode и только через защищённый writer:
   каждый путь проверяется, каждый `.py` файл обязан компилироваться, а отчёт называет то, что
   создано или обновлено. Ответ без файлов оставляет проект ровно там, где он был.
2. **Запуск процесса.** Команда определяется на сервере, а интерфейс показывает точную команду и её
   отпечаток. Ничего не стартует без `{"confirm": true}` и этого отпечатка, и одно подтверждение
   даёт ровно один запуск.
3. **Оплата модели.** Модель с ценой `PAID` запускается только когда вы назвали **и** провайдера,
   **и** модель, *и* развёртывание разрешает платных провайдеров. Иначе запрос отклоняется до
   отправки: ничего не отправлено и ничего не потрачено, а отказ называет пару и её опубликованную
   цену.

`NEEDS_CHANGES`, `FAILED`, `REFUSED`, `RUN_BLOCKED` и `TIMED_OUT` — все это честные ответы, и
ни один из них не `COMPLETED`.
