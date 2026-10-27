"""The deterministic output contract a write-mode run gives the model.

The product problem this module solves is not a model problem: a model answered a
code task with a plan and the question "which option, A/B/C?", and the app treated
that as an ordinary finished run. Nobody using the app should have to know that a
file has to arrive inside a fenced block with a ``path=`` header -- that is the
app's job to ask for, in the app's own words, appended to the task text by the
code path rather than typed by the user.

Two texts live here and nothing else:

* :data:`OUTPUT_CONTRACT` -- appended to every write-mode prompt
  (:func:`append_output_contract`). It states the answer shape, the allowed file
  root, the forbidden paths (``.env``, secrets, ``.git``, ...), the "only what the
  plan needs" rule, that a ``code`` run in write mode is expected to produce
  files rather than a report, and the closing list the answer must end with:
  what it checked, what it assumes, what it does not know.
* :data:`RETRY_INSTRUCTION` -- the shorter, harder text used for the single retry
  (:func:`build_retry_prompt`). If an answer carries no file block at all, Forge
  asks exactly once more; :data:`MAX_WRITE_RETRIES` is what caps that, and the rule
  lives in the service so there is one place that can count attempts.

Both texts are constants, not templates over user input: the same task produces
byte-identical text on every run, and neither text carries a credential, a file
body, or an absolute path. The workspace *context* (a bounded list of file names)
is composed by :func:`app.agent_runtime.code_writer.build_workspace_context` and
is passed in, never copied here.
"""

from __future__ import annotations

#: How many times a write-mode answer that carries no file block may be re-asked.
#: One, and the service enforces it: this is a single corrective instruction, not a
#: loop.
MAX_WRITE_RETRIES = 1

#: The deterministic output contract appended to every write-mode prompt.
OUTPUT_CONTRACT = """OUTPUT CONTRACT — обязателен для этого запуска
Сначала коротко: что делаешь. Затем — файлы.
Для каждого файла верни ровно один блок:
```<язык> path=<относительный путь>
<содержимое файла>
```
Язык блока — по типу файла: html, python, js, css, json, md.
Никаких вопросов, никаких вариантов A/B/C, никаких отчётов вместо кода.
Если данных не хватает — задай НЕ БОЛЕЕ 3 вопросов в конце и верни intent=CLARIFICATION_REQUIRED.

Разрешённый корень записи — рабочая папка проекта (её файлы перечислены в контексте выше).
Пути в блоках — только относительные, внутри этой папки: абсолютные пути, «..» и выход за папку запрещены.
Запрещено писать: .env и .env.*, любые файлы с ключами, токенами и секретами, .git, .venv, node_modules, dist.
Пиши только те файлы, которые нужны плану этой задачи; ничего сверх плана не создавай.
Это запуск категории code в режиме записи (write_mode): от тебя ожидаются файлы, а не отчёт.
В конце ответа перечисли: что ты проверил, что предполагаешь, что осталось неизвестным; не утверждай, что работает, если не проверял."""

#: The one corrective instruction used when an answer carried no file block.
RETRY_INSTRUCTION = """Ты не вернул ни одного файла. Верни только блоки с path=… — по одному блоку на файл, без плана, без отчёта и без вопросов.
Формат: ```<язык> path=<относительный путь> и содержимое файла ``` (html, python, js, css, json, md).
Пути только относительные, внутри рабочей папки проекта. .env, секреты, .git, .venv, node_modules, dist — запрещены."""


def append_output_contract(prompt: str) -> str:
    """Return ``prompt`` with the contract appended as the final instruction.

    The contract goes last on purpose: it is the instruction that decides the shape
    of the answer, and the text a model reads last is the one it follows. The input
    is returned unchanged when it is empty, so no caller can turn a missing task
    into a prompt that carries only an instruction.
    """

    text = prompt if isinstance(prompt, str) else ""
    if not text.strip():
        return text
    if OUTPUT_CONTRACT in text:
        # Idempotent: applying the contract twice must not double the prompt.
        return text
    return f"{text}\n\n{OUTPUT_CONTRACT}"


def build_retry_prompt(workspace_context: str = "") -> str:
    """The task text for the single retry: the hard instruction, then the context.

    The workspace context is repeated so an answer can still name a real path; it is
    the same bounded, name-only listing the first prompt carried. When there is no
    context the instruction stands alone.
    """

    context = workspace_context if isinstance(workspace_context, str) else ""
    if context.strip():
        return f"{RETRY_INSTRUCTION}\n\n{context}"
    return RETRY_INSTRUCTION
