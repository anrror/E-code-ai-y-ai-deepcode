"""Company-assigned task identity.

The identifier is opaque to DFcode: a company's planning system issues it,
the product/project configuration carries it, and the team-context block
reports it so a Session can be attributed to that task.

The character set is deliberately narrow. The value is written into an
XML-like attribute of the team-context block and into logs, so whitespace,
quotes, angle brackets, and control characters are rejected rather than
escaped.
"""

from __future__ import annotations

import re

MAX_TASK_ID_CHARS = 128
_TASK_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/#+-]*$")


def normalize_task_id(value: object) -> str | None:
    """Return a clean company task id, or ``None`` for an empty value.

    Raises :class:`ValueError` for a value that cannot be represented safely in
    the team-context header.
    """

    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) > MAX_TASK_ID_CHARS or not _TASK_ID_PATTERN.fullmatch(text):
        raise ValueError(
            "a company task id must be 1-128 characters using letters, digits, "
            "and . _ : @ / # + - (no whitespace, quotes, or angle brackets)"
        )
    return text


__all__ = ["MAX_TASK_ID_CHARS", "normalize_task_id"]
