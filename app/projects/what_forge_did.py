"""«Что сделал Forge»: the model's own words, bounded and screened.

The owner's complaint was not that a change failed -- it was that the screen said
nothing about *what the model answered*. A failed change and a successful one looked
the same, and the only way to find out was to open the run record by hand.

This module is the one place that turns a model answer into a short excerpt a card can
print. Three rules, and nothing else:

* **Two lines, bounded.** A run's prose can be thousands of lines; the excerpt is the
  first :data:`MAX_EXCERPT_LINES` non-empty lines, collapsed and cut to
  :data:`MAX_EXCERPT_CHARS` characters, so it can never become a wall of text under a
  button.
* **Screened before it leaves the backend.** The repository's existing credential-shape
  screen (:func:`app.context.validation.find_forbidden_value_content`) runs over the
  excerpt, and a hit replaces the whole excerpt with the repository's own redaction
  marker. A model that echoed a token into its answer cannot leak it through this
  field.
* **Empty means empty.** No answer produces ``""``, never a sentence Forge invented.
  The card says «Модель не вернула файлы» on its own, and the excerpt is shown only
  when there really is one.
"""

from __future__ import annotations

from typing import List, Optional

from app.context.validation import find_forbidden_value_content
from app.discovery.models import redaction_marker

__all__ = ["MAX_EXCERPT_CHARS", "MAX_EXCERPT_LINES", "answer_excerpt"]

#: How many of the answer's first non-empty lines are published.
MAX_EXCERPT_LINES = 2

#: The longest excerpt published, in characters.
MAX_EXCERPT_CHARS = 400


def answer_excerpt(
    answer: object,
    *,
    lines: int = MAX_EXCERPT_LINES,
    max_chars: int = MAX_EXCERPT_CHARS,
) -> str:
    """The first ``lines`` non-empty lines of a model answer, screened and bounded."""

    raw = "" if answer is None else str(answer)
    if not raw.strip():
        return ""
    limit = max(1, int(lines))
    picked: List[str] = []
    for line in raw.splitlines():
        collapsed = " ".join(line.split())
        if not collapsed:
            continue
        picked.append(collapsed)
        if len(picked) >= limit:
            break
    if not picked:
        return ""
    text = "\n".join(picked)[: max(1, int(max_chars))]
    reason: Optional[str] = find_forbidden_value_content(text)
    if reason is not None:
        return redaction_marker(reason)
    return text
