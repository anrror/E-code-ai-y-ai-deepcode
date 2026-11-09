"""Validated value objects for the team context layer."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

ENTITY_KINDS = ("org", "line", "product", "project", "batch", "version", "task")
KNOWLEDGE_TYPES = (
    "redline",
    "decision",
    "convention",
    "domain",
    "design",
    "lesson",
    "external",
)
CONFIDENCE_LEVELS = ("high", "medium", "low")
REVIEW_STATES = ("draft", "reviewed", "deprecated")
# Old names kept for the document's wording (company is the org layer).
KIND_ALIASES = {"company": "org"}


class TeamContextError(ValueError):
    """A catalog or entry violates the six-layer rules."""


def _string_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value.strip(),) if value.strip() else ()
    if isinstance(value, (list, tuple)):
        return tuple(str(item).strip() for item in value if str(item).strip())
    raise TeamContextError("expected a string or a list of strings")


@dataclass(frozen=True, slots=True)
class TeamEntity:
    """One node of the six-layer organization model.

    ``product`` is an asset node, not a seventh management layer: an organization
    owns PaaS products, a line owns its standard products, and a project
    *references* them instead of inheriting from them. A referenced product
    contributes design and codebase pointers plus its own knowledge, but never
    overrides the project's chain.
    """

    id: str
    kind: str
    name: str
    parent: str | None = None
    external: bool = False
    workspace: str | None = None
    # Multi-reference: projects cite products (and other capabilities) without
    # copying them, so a product stays single-owned.
    references: tuple[str, ...] = ()
    # Pointers, not content: the agent opens them through its normal tools.
    designs: tuple[str, ...] = ()
    codebases: tuple[str, ...] = ()
    # A historical product stays searchable and referenceable, but is labelled
    # historical and never counts as the current standard.
    archived: bool = False

    def __post_init__(self) -> None:
        if self.kind not in ENTITY_KINDS:
            raise TeamContextError(
                f"unknown entity kind {self.kind!r}; expected one of {ENTITY_KINDS}"
            )
        if not self.id.startswith(f"{self.kind}://") or len(self.id) <= len(self.kind) + 3:
            raise TeamContextError(
                f"entity id must look like {self.kind}://<slug>, got {self.id!r}"
            )
        if not self.name.strip():
            raise TeamContextError(f"entity {self.id} needs a name")
        if self.parent is not None and self.parent == self.id:
            raise TeamContextError(f"entity {self.id} cannot be its own parent")
        if self.kind == "org" and self.parent:
            raise TeamContextError("org entities are roots and cannot have a parent")

    @classmethod
    def from_mapping(cls, raw: Any) -> TeamEntity:
        if not isinstance(raw, dict):
            raise TeamContextError("each entity must be a mapping")
        try:
            return cls(
                id=str(raw["id"]).strip(),
                kind=str(raw["kind"]).strip(),
                name=str(raw.get("name") or raw["id"]).strip(),
                parent=(str(raw["parent"]).strip() if raw.get("parent") else None),
                external=bool(raw.get("external", False)),
                workspace=(str(raw["workspace"]).strip() if raw.get("workspace") else None),
                references=_string_tuple(raw.get("references")),
                designs=_string_tuple(raw.get("designs")),
                codebases=_string_tuple(raw.get("codebases")),
                archived=bool(raw.get("archived", False)),
            )
        except KeyError as exc:
            raise TeamContextError(f"entity is missing {exc.args[0]!r}") from exc

    def to_mapping(self) -> dict[str, Any]:
        data: dict[str, Any] = {"id": self.id, "kind": self.kind, "name": self.name}
        if self.parent:
            data["parent"] = self.parent
        if self.external:
            data["external"] = True
        if self.workspace:
            data["workspace"] = self.workspace
        if self.references:
            data["references"] = list(self.references)
        if self.designs:
            data["designs"] = list(self.designs)
        if self.codebases:
            data["codebases"] = list(self.codebases)
        if self.archived:
            data["archived"] = True
        return data


@dataclass(frozen=True, slots=True)
class KnowledgeEntry:
    """One knowledge item, owned by exactly one scope."""

    id: str
    scope: str
    type: str
    statement: str
    rationale: str = ""
    evidence: str = ""
    confidence: str = "medium"
    status: str = "draft"
    owner: str = ""
    source: str = "internal"
    valid_from: str | None = None
    valid_to: str | None = None
    supersedes: str | None = None

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise TeamContextError("knowledge entry needs an id")
        if "://" not in self.scope:
            raise TeamContextError(
                f"knowledge scope must be an entity id, got {self.scope!r}"
            )
        if self.type not in KNOWLEDGE_TYPES:
            raise TeamContextError(
                f"unknown knowledge type {self.type!r}; expected one of {KNOWLEDGE_TYPES}"
            )
        if self.confidence not in CONFIDENCE_LEVELS:
            raise TeamContextError(
                f"confidence must be one of {CONFIDENCE_LEVELS}, got {self.confidence!r}"
            )
        if self.status not in REVIEW_STATES:
            raise TeamContextError(
                f"status must be one of {REVIEW_STATES}, got {self.status!r}"
            )
        if not self.statement.strip():
            raise TeamContextError(f"knowledge entry {self.id} has an empty statement")

    @property
    def external(self) -> bool:
        return self.source != "internal" or self.type == "external"

    @classmethod
    def from_mapping(cls, raw: Any) -> KnowledgeEntry:
        if not isinstance(raw, dict):
            raise TeamContextError("each knowledge entry must be a mapping")
        try:
            return cls(
                id=str(raw["id"]).strip(),
                scope=str(raw["scope"]).strip(),
                type=str(raw["type"]).strip(),
                statement=str(raw["statement"]).strip(),
                rationale=str(raw.get("rationale") or "").strip(),
                evidence=str(raw.get("evidence") or "").strip(),
                confidence=str(raw.get("confidence") or "medium").strip(),
                status=str(raw.get("status") or "draft").strip(),
                owner=str(raw.get("owner") or "").strip(),
                source=str(raw.get("source") or "internal").strip(),
                valid_from=(str(raw["valid_from"]) if raw.get("valid_from") else None),
                valid_to=(str(raw["valid_to"]) if raw.get("valid_to") else None),
                supersedes=(str(raw["supersedes"]) if raw.get("supersedes") else None),
            )
        except KeyError as exc:
            raise TeamContextError(f"knowledge entry is missing {exc.args[0]!r}") from exc

    def to_mapping(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "id": self.id,
            "scope": self.scope,
            "type": self.type,
            "statement": self.statement,
        }
        for key in (
            "rationale",
            "evidence",
            "confidence",
            "status",
            "owner",
            "source",
            "valid_from",
            "valid_to",
            "supersedes",
        ):
            value = getattr(self, key)
            if value:
                data[key] = value
        return data
