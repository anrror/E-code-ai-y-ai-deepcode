"""Sink: dedupe captured suggestions and block red lines.

Before a batch of suggestions is written to the pending area, it is de-duplicated
against the current queue and against the layer's existing knowledge, and any
suggestion whose knowledge type would be a ``redline`` is dropped (red lines are
human-only). This keeps the queue from growing a duplicate per session and keeps
the highest-precedence governance rule out of the automatic path.
"""

from __future__ import annotations

from collections.abc import Iterable

from core.capture.models import CapturedSuggestion, load_suggestions


def _knowledge_key(suggestion: CapturedSuggestion) -> str:
    """A normalized dedupe key for a knowledge suggestion."""
    statement = str(suggestion.payload.get("statement") or "").strip()
    return " ".join(statement.split()).casefold()


def _env_key(suggestion: CapturedSuggestion) -> str:
    return str(suggestion.payload.get("key") or "").strip()


def _pointer_key(suggestion: CapturedSuggestion) -> str:
    return str(suggestion.payload.get("value") or "").strip().rstrip("/").casefold()


def dedupe_suggestions(
    suggestions: Iterable[CapturedSuggestion],
    *,
    existing: Iterable[CapturedSuggestion] = (),
    existing_statements: Iterable[str] = (),
) -> list[CapturedSuggestion]:
    """Drop duplicates and red lines; keep first-seen order.

    ``existing_statements`` are the normalized statements already present in the
    layer's knowledge (reviewed or draft), so a suggestion that merely repeats
    an existing fact is not re-proposed.
    """
    seen: set[tuple[str, str]] = set()
    known_statements = {_norm(s) for s in existing_statements}
    for item in existing:
        if item.kind == "knowledge":
            key = _knowledge_key(item)
            if key:
                seen.add(("knowledge", key))
        elif item.kind == "environment":
            key = _env_key(item)
            if key:
                seen.add(("environment", key))
        elif item.kind in ("pointer_git", "pointer_doc", "pointer_workspace", "reference"):
            key = _pointer_key(item)
            if key:
                seen.add((item.kind, key))

    out: list[CapturedSuggestion] = []
    for suggestion in suggestions:
        if suggestion.kind == "knowledge":
            # redline is never emitted by extraction, but block it defensively.
            if str(suggestion.payload.get("type") or "") == "redline":
                continue
            key = _knowledge_key(suggestion)
            if not key or key in known_statements:
                continue
            if ("knowledge", key) in seen:
                continue
            seen.add(("knowledge", key))
        elif suggestion.kind == "environment":
            key = _env_key(suggestion)
            if not key or ("environment", key) in seen:
                continue
            seen.add(("environment", key))
        elif suggestion.kind in ("pointer_git", "pointer_doc", "pointer_workspace", "reference"):
            key = _pointer_key(suggestion)
            if not key or (suggestion.kind, key) in seen:
                continue
            seen.add((suggestion.kind, key))
        out.append(suggestion)
    return out


def _norm(text: str) -> str:
    return " ".join(str(text).split()).casefold()


def dedupe_against_queue(
    suggestions: Iterable[CapturedSuggestion],
    home: str | None = None,
) -> list[CapturedSuggestion]:
    """Dedupe against the on-disk pending queue (and block red lines)."""
    return dedupe_suggestions(suggestions, existing=load_suggestions(home))


__all__ = [
    "dedupe_against_queue",
    "dedupe_suggestions",
]
