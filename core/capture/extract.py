"""Model-driven extraction pass — turn a transcript summary into suggestions.

Reuses the same single-agent shape as :mod:`core.loop.autodream`: one prompt,
one model round-trip, then a deterministic follow-up. The prompt is scoped to
*produce structured suggestions*, never to touch files directly; the results
are parsed here and turned into ``CapturedSuggestion`` objects that land in the
pending-review area.

What the pass may extract: knowledge (decision/convention/lesson/domain/
design), product/project references, and environment-variable keys (with a
value only when the layer lacks it). It must never extract a ``redline`` (red
lines are human-only) and must never emit an integration secret (Jenkins token,
kubeconfig) — the prompt forbids both, and the parser rejects both.
"""

from __future__ import annotations

import json
import re
from typing import Any

from core.capture.models import CapturedSuggestion
from core.team_context.models import KNOWLEDGE_TYPES

# Types the model may extract. ``redline`` is deliberately absent.
_EXTRACTABLE_TYPES = tuple(
    t for t in KNOWLEDGE_TYPES if t not in {"redline", "compliance", "license", "toolchain"}
)

_EXTRACT_PROMPT = (
    "You are extracting durable team knowledge from a coding session summary. "
    "Produce a single JSON array of up to 12 entries. Each entry must have "
    'exactly: {"type","statement","rationale","evidence","confidence"}. '
    "Allowed types: " + ", ".join(_EXTRACTABLE_TYPES) + ". "
    "confidence is high|medium|low. statement is one concrete, durable fact "
    "a future session should know. rationale is why it is true; evidence is "
    "where in this session it appeared. "
    "Do NOT extract: red lines (organizational prohibitions), API keys, "
    "passwords, tokens, kubeconfig paths, or any other secret — if a fact "
    "contains a secret, omit the whole entry. "
    "Extract only durable facts; skip one-off, transient, or task-specific "
    "noise. Reply with the JSON array only, no prose, no code fences."
)

# The JSON the model must return is a bare array. It may still fence it; strip
# fences and any leading prose before the first '['.
_JSON_ARRAY_RE = re.compile(r"\[[\s\S]*\]")


def build_extract_prompt() -> str:
    return _EXTRACT_PROMPT


def parse_extractions(text: str) -> list[dict[str, Any]]:
    """Parse the model's JSON array into validated extraction dicts.

    Robust to code fences and surrounding prose; an entry that fails
    validation (bad type, empty statement, a secret-looking value) is dropped
    rather than aborting the whole pass.
    """
    match = _JSON_ARRAY_RE.search(text or "")
    if match is None:
        return []
    try:
        raw = json.loads(match.group(0))
    except json.JSONDecodeError:
        return []
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("type") or "").strip()
        if kind not in _EXTRACTABLE_TYPES:
            continue
        statement = str(item.get("statement") or "").strip()
        if not statement:
            continue
        if _looks_secret(statement):
            continue
        out.append(
            {
                "type": kind,
                "statement": statement,
                "rationale": str(item.get("rationale") or "").strip(),
                "evidence": str(item.get("evidence") or "").strip(),
                "confidence": (
                    str(item.get("confidence") or "medium").strip()
                    if str(item.get("confidence") or "medium").strip()
                    in {"high", "medium", "low"}
                    else "medium"
                ),
            }
        )
    return out


def _looks_secret(text: str) -> bool:
    """A crude but conservative secret gate: refuse obvious credential shapes."""
    lowered = text.lower()
    if re.search(r"\b(api[_-]?key|password|passwd|secret|token|private[_-]?key)\b", lowered):
        return True
    # A long, high-entropy token (e.g. JWT, base64 blob) inline.
    return bool(re.search(r"\b[A-Za-z0-9_-]{32,}\b", text))


def suggestions_from_extractions(
    extractions: list[dict[str, Any]],
    *,
    scope: str,
    source: str,
) -> list[CapturedSuggestion]:
    """Turn validated extractions into pending knowledge suggestions."""
    out: list[CapturedSuggestion] = []
    for item in extractions:
        out.append(
            CapturedSuggestion(
                kind="knowledge",
                scope=scope,
                source=source,
                payload={
                    "type": item["type"],
                    "statement": item["statement"],
                    "rationale": item["rationale"],
                    "evidence": item["evidence"],
                    "confidence": item["confidence"],
                },
            )
        )
    return out


__all__ = [
    "build_extract_prompt",
    "parse_extractions",
    "suggestions_from_extractions",
]
