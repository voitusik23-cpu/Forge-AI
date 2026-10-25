"""The deterministic "model answer" used by ``MOCK_DETERMINISTIC`` runs.

Why this exists
---------------

The offline ``mock`` provider cannot answer for work: its provider call is one
fixed sentence, and the offline task path's write mode deliberately applies the
deterministic *plan* text, which names no file and therefore writes nothing
(``tests/test_code_writer.py`` asserts exactly that). That is the right behaviour
for a read-only run, but it means no offline path can ever prove materialisation
end to end.

``MOCK_DETERMINISTIC`` is the second write mode, and this module is its answer. It
produces a small, fixed, non-executable project -- one page and one importable
module -- through the **same** fenced-block shape a real model uses, so the whole
pipeline (parse -> workspace boundary -> deny list -> atomic write -> ``py_compile``
-> manifest -> preview) runs for real. Everything it writes is labelled as a mock
scaffold in the files themselves; nothing here pretends a model designed the
project.

It is deterministic: the same task text always produces the same bytes, so a smoke
or an end-to-end test can assert on them.

**Only the offline ``mock`` provider may receive it.** :func:`deterministic_project_answer`
builds the text; :func:`deterministic_answer_for_provider` is the one entry point the
run path calls, and it returns ``""`` for every other provider. That guard is what
stops a real provider's empty answer from being answered with this scaffold: a
project must never hold a template the model did not write.
"""

from __future__ import annotations

import html
from pathlib import Path

__all__ = [
    "MOCK_ANSWER_BANNER",
    "MOCK_PROVIDER_NAME",
    "SCAFFOLD_PATHS",
    "deterministic_answer_for_provider",
    "deterministic_project_answer",
    "scaffold_conflicts_with",
]


#: The one provider name this scaffold belongs to: the offline ``mock``.
MOCK_PROVIDER_NAME = "mock"

#: The paths the answer declares, in order. Applying the scaffold writes exactly these,
#: which is why :func:`scaffold_conflicts_with` can answer "would this overwrite the
#: owner's own code?" without parsing the answer.
SCAFFOLD_PATHS = ("index.html", "app.py")


#: Printed in the run's output and written into both files, so a reader never
#: mistakes mock scaffolding for generated design.
MOCK_ANSWER_BANNER = "MOCK_DETERMINISTIC: this project was scaffolded deterministically."

#: The longest title derived from the task text.
MAX_TITLE_CHARS = 80


def _title(task_text: object) -> str:
    collapsed = " ".join(str(task_text or "").split())
    if not collapsed:
        return "Forge project"
    return collapsed[:MAX_TITLE_CHARS]


def deterministic_answer_for_provider(
    provider_name: object, task_text: object = ""
) -> str:
    """The scaffold for the offline ``mock`` provider, and for **nothing else**.

    A real provider returned no file block is a fact about the model, not an
    invitation for Forge to write its own template: this returns ``""`` for every
    provider except ``mock``, so the caller has nothing to apply and must say
    honestly that the project was not created. The comparison is case-insensitive
    and whitespace-insensitive, and an unknown/empty provider name (which cannot
    name the offline path anyway) is refused too.
    """

    if str(provider_name or "").strip().lower() != MOCK_PROVIDER_NAME:
        return ""
    return deterministic_project_answer(task_text)


def scaffold_conflicts_with(folder: object) -> bool:
    """Whether applying the scaffold would overwrite a file the project already has.

    This is the guard that keeps the offline scaffold on the right side of the line: it
    may **fill** a project (the intended ``MOCK_DETERMINISTIC`` behaviour, used by the
    smoke and by the tests), and it may never **replace** the owner's own ``index.html``
    or ``app.py`` with Forge's template. Measured consequence of getting it wrong: a
    "change" on a delivered project would silently replace the application with the
    generic scaffold while the card said the project had changed.

    An unreadable folder reports a conflict: refusing to write is the safe direction.
    """

    try:
        root = Path(str(folder))
        if not root.is_dir():
            return False
        return any((root / name).is_file() for name in SCAFFOLD_PATHS)
    except Exception:  # noqa: BLE001 - an unreadable folder is "do not write"
        return True


def deterministic_project_answer(task_text: object = "") -> str:
    """A fenced answer that creates ``index.html`` and ``app.py``.

    The two files are deliberately minimal and side-effect free: the page is a
    static document the preview can serve, and the module imports and compiles
    without touching the network, the filesystem or a process.
    """

    title = _title(task_text)
    safe_title = html.escape(title, quote=True)
    page = f"""<!doctype html>
<html lang="ru">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>{safe_title}</title>
    <style>
      body {{ margin: 0; font-family: system-ui, sans-serif; background: #0f1115; color: #e8eaed; }}
      main {{ max-width: 720px; margin: 0 auto; padding: 48px 24px; }}
      h1 {{ font-size: 1.6rem; margin: 0 0 12px; }}
      p {{ line-height: 1.6; color: #b6bbc4; }}
      code {{ background: #1b1f27; padding: 2px 6px; border-radius: 4px; }}
    </style>
  </head>
  <body>
    <main>
      <h1>{safe_title}</h1>
      <p>{MOCK_ANSWER_BANNER}</p>
      <p>This page exists so the local preview can serve a real file. It is a
      deterministic scaffold, not a design produced by a model.</p>
      <p><code>app.py</code> is the entry module and has no side effects.</p>
    </main>
  </body>
</html>
"""

    module = f'''"""{MOCK_ANSWER_BANNER}

This module is a deterministic scaffold written through the guarded code writer.
It has no dependency and no side effect on import.
"""

PROJECT_TITLE = {title!r}


def describe() -> str:
    """Return one line describing this project."""

    return PROJECT_TITLE
'''

    # The two fenced headers are exactly :data:`SCAFFOLD_PATHS`, in order: the guard
    # above answers "would this overwrite the owner's file?" from that tuple, so the two
    # must stay the same list. `tests/test_materialization.py` writes through this answer.
    return (
        f"{MOCK_ANSWER_BANNER}\n"
        "The answer below is produced by Forge itself, not by a model.\n\n"
        f"```html path={SCAFFOLD_PATHS[0]}\n"
        f"{page}"
        "```\n\n"
        f"```python path={SCAFFOLD_PATHS[1]}\n"
        f"{module}"
        "```\n"
    )
