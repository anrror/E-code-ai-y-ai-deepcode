"""Captured suggestions — the pending-review layer for auto-capture (P4).

The auto-capture pipeline (compaction hook + session-end hook) produces
*structured suggestions* from a Session's transcript and durable records. A
suggestion is deliberately **not** applied directly: it lands in a
pending-review area under ``~/.dfcode/team/pending/``, and a human (or a
reviewed step) accepts or rejects it. This keeps the governance boundary of
``team_context`` intact — reviewed team facts are an explicit human act.

What a suggestion can carry (``kind``):

* ``pointer_git`` / ``pointer_doc`` / ``pointer_workspace`` — a layer pointer
  (``codebases`` / ``designs`` / ``workspace``) the transcript mentioned that
  the layer does not already declare. Deterministic, zero-model extraction.
* ``artifact`` / ``workflow`` / ``goal`` — a durable record reference the
  Session produced. Deterministic, zero-model registration.
* ``runtime_profile`` — the Session's model/connection/permission posture,
  registered for future review, not injected as knowledge.
* ``knowledge`` — a semantic fact (decision/convention/lesson/…) a model
  extracted from the transcript. Never ``redline`` (red lines are human-only).
* ``reference`` — a product/project the transcript cited (``references``).
* ``environment`` — an environment variable key (and, only when the layer has
  no value yet, its value) the transcript surfaced. Semi-automatic by design.

Sensitive values are scoped as tightly as the design allows: an ``environment``
suggestion carries ``value`` only when ``allow_value`` was set and the target
layer does not already hold that key; integration secrets (Jenkins tokens,
kubeconfig) are never captured — see :mod:`core.team_context.integrations`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from core.domain.common import (
    JsonObject,
    new_id,
    require_aware,
    require_non_empty,
    require_prefixed_id,
    utc_now,
    validate_json_object,
)

PENDING_RELATIVE = Path(".dfcode") / "team" / "pending"
PENDING_FILE = "suggestions.yaml"

# The suggestion kinds the pipeline can emit.
SUGGESTION_KINDS = (
    "pointer_git",
    "pointer_doc",
    "pointer_workspace",
    "artifact",
    "workflow",
    "goal",
    "runtime_profile",
    "knowledge",
    "reference",
    "environment",
)
# Kinds whose payload legitimately carries a ``value`` field: pointer kinds
# store a non-secret pointer value; ``environment`` stores a (possibly secret)
# key value. Every other kind is value-free.
VALUE_CAPABLE_KINDS = (
    "pointer_git",
    "pointer_doc",
    "pointer_workspace",
    "reference",
    "environment",
)
# Only ``environment`` may carry a *secret*; the pointer ``value`` is a path or
# URL, never a credential.
SECRET_CAPABLE_KINDS = ("environment",)
SUGGESTION_STATUSES = ("pending", "accepted", "rejected")


def pending_dir(home: str | Path | None = None) -> Path:
    """The pending-review area (``~/.dfcode/team/pending`` by default)."""
    base = Path(home) if home is not None else Path.home()
    return base / PENDING_RELATIVE


@dataclass(frozen=True, slots=True)
class CapturedSuggestion:
    """One pending suggestion produced by the auto-capture pipeline.

    ``payload`` is a JSON object whose shape is determined by ``kind``:
    ``pointer_*`` and ``reference`` carry ``{"value": str}``; ``artifact`` /
    ``workflow`` / ``goal`` / ``runtime_profile`` carry their structured
    projection; ``knowledge`` carries the ``KnowledgeEntry``-shaped mapping;
    ``environment`` carries ``{"key": str, "value": str | None, "env": str}``.
    """

    kind: str
    scope: str
    source: str
    payload: JsonObject = field(default_factory=dict)
    status: str = "pending"
    id: str = field(default_factory=lambda: new_id("cap"))
    created_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        require_prefixed_id(self.id, "cap")
        if self.kind not in SUGGESTION_KINDS:
            raise ValueError(f"unknown suggestion kind {self.kind!r}")
        require_non_empty(self.scope, "scope")
        require_non_empty(self.source, "source")
        if self.status not in SUGGESTION_STATUSES:
            raise ValueError(f"unknown suggestion status {self.status!r}")
        require_aware(self.created_at, "created_at")
        validate_json_object(self.payload, "payload")
        if self.kind not in VALUE_CAPABLE_KINDS and "value" in self.payload:
            raise ValueError(
                f"a {self.kind} suggestion must not carry a value field"
            )

    @classmethod
    def from_mapping(cls, raw: Any) -> CapturedSuggestion:
        if not isinstance(raw, dict):
            raise TypeError("each suggestion must be a mapping")
        try:
            created_raw = raw.get("created_at")
            created = utc_now()
            if isinstance(created_raw, str) and created_raw.strip():
                try:
                    created = datetime.fromisoformat(created_raw)
                except ValueError:
                    created = utc_now()
            return cls(
                kind=str(raw["kind"]).strip(),
                scope=str(raw["scope"]).strip(),
                source=str(raw["source"]).strip(),
                payload=dict(raw.get("payload") or {}),
                status=str(raw.get("status") or "pending").strip(),
                id=str(raw["id"]),
                created_at=created,
            )
        except KeyError as exc:
            raise ValueError(f"suggestion is missing {exc.args[0]!r}") from exc

    def to_mapping(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "scope": self.scope,
            "source": self.source,
            "payload": dict(self.payload),
            "status": self.status,
            "created_at": self.created_at.isoformat(),
        }


def load_suggestions(home: str | Path | None = None) -> list[CapturedSuggestion]:
    """Read the pending area, newest last (file order). Missing area = empty."""
    path = pending_dir(home) / PENDING_FILE
    if not path.is_file():
        return []
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    except (OSError, yaml.YAMLError):
        return []
    if not isinstance(raw, list):
        return []
    suggestions: list[CapturedSuggestion] = []
    for item in raw:
        try:
            suggestions.append(CapturedSuggestion.from_mapping(item))
        except (ValueError, TypeError):
            continue  # a malformed entry must not break the whole queue
    return suggestions


def _atomic_yaml(path: Path, payload: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def save_suggestions(
    suggestions: list[CapturedSuggestion],
    home: str | Path | None = None,
) -> Path:
    """Overwrite the pending queue with ``suggestions`` (atomic)."""
    directory = pending_dir(home)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / PENDING_FILE
    _atomic_yaml(path, [s.to_mapping() for s in suggestions])
    return path


def append_suggestion(
    suggestion: CapturedSuggestion,
    home: str | Path | None = None,
) -> Path:
    """Append one suggestion to the pending queue (atomic, dedup by id)."""
    current = load_suggestions(home)
    current = [s for s in current if s.id != suggestion.id]
    current.append(suggestion)
    return save_suggestions(current, home)


__all__ = [
    "PENDING_FILE",
    "PENDING_RELATIVE",
    "SECRET_CAPABLE_KINDS",
    "SUGGESTION_KINDS",
    "SUGGESTION_STATUSES",
    "VALUE_CAPABLE_KINDS",
    "CapturedSuggestion",
    "append_suggestion",
    "load_suggestions",
    "pending_dir",
    "save_suggestions",
]
