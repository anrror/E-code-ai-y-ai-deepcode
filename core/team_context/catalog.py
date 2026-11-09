"""Read, validate, and write the file-backed team catalog.

Two layers, near one wins by id:

``~/.deepcode/team/catalog.yaml``      organization/line knowledge shared by
                                       every workspace on this machine;
``<workspace>/.deepcode/team/catalog.yaml``  the workspace's entities, its
                                       task binding, and local knowledge.

The catalog is repository content (committed, reviewed), not private state, so
it is written as plain YAML with a stable key order.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from pathlib import Path

import yaml

from core.team_context.models import (
    KnowledgeEntry,
    TeamContextError,
    TeamEntity,
)

CATALOG_RELATIVE = Path(".deepcode") / "team" / "catalog.yaml"
PARENT_KIND: dict[str, str] = {
    "line": "org",
    "project": "line",
    "batch": "project",
    "version": "batch",
    "task": "version",
}


@dataclass(frozen=True, slots=True)
class Catalog:
    entities: dict[str, TeamEntity] = field(default_factory=dict)
    knowledge: dict[str, KnowledgeEntry] = field(default_factory=dict)
    bind: str | None = None

    def merged(self, other: Catalog) -> Catalog:
        """Nearer catalog wins for the same id; its binding wins if set."""

        return Catalog(
            entities={**self.entities, **other.entities},
            knowledge={**self.knowledge, **other.knowledge},
            bind=other.bind or self.bind,
        )

    def bind_for(self, workspace: str | Path) -> str | None:
        """The task the workspace declares, or its entity's ``workspace``."""

        if self.bind:
            return self.bind
        target = _normalized_path(workspace)
        for entity in self.entities.values():
            if entity.workspace and _normalized_path(entity.workspace) == target:
                return entity.id
        return None


def default_catalog_paths(workspace: str | Path, home: str | Path | None) -> tuple[Path, Path]:
    root = Path(home) if home is not None else Path.home()
    return root / CATALOG_RELATIVE, Path(workspace) / CATALOG_RELATIVE


def load_catalog(workspace: str | Path, home: str | Path | None = None) -> Catalog:
    """Load global then workspace catalogs, validating the merged result."""

    global_path, workspace_path = default_catalog_paths(workspace, home)
    catalog = Catalog()
    for path in (global_path, workspace_path):
        if path.is_file():
            catalog = catalog.merged(_read_catalog(path))
    _validate(catalog)
    return catalog


def save_catalog(
    workspace: str | Path,
    catalog: Catalog,
    *,
    home: str | Path | None = None,
) -> Path:
    """Write the workspace layer atomically, leaving the global layer alone."""

    global_path, path = default_catalog_paths(workspace, home)
    merged = _read_catalog(global_path) if global_path.is_file() else Catalog()
    _validate(merged.merged(catalog))
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "entities": [entry.to_mapping() for entry in catalog.entities.values()],
        "knowledge": [entry.to_mapping() for entry in catalog.knowledge.values()],
    }
    if catalog.bind:
        payload["bind"] = catalog.bind
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    os.replace(temporary, path)
    return path


def _read_catalog(path: Path) -> Catalog:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise TeamContextError(f"could not read {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise TeamContextError(f"{path} must contain a mapping")
    entities = {}
    for item in raw.get("entities") or []:
        entity = TeamEntity.from_mapping(item)
        if entity.id in entities:
            raise TeamContextError(f"duplicate entity id {entity.id}")
        entities[entity.id] = entity
    knowledge = {}
    for item in raw.get("knowledge") or []:
        entry = KnowledgeEntry.from_mapping(item)
        if entry.id in knowledge:
            raise TeamContextError(f"duplicate knowledge id {entry.id}")
        knowledge[entry.id] = entry
    bind = raw.get("bind")
    return Catalog(
        entities=entities,
        knowledge=knowledge,
        bind=str(bind).strip() if bind else None,
    )


def _validate(catalog: Catalog) -> None:
    for entity in catalog.entities.values():
        if entity.parent is None:
            continue
        parent = catalog.entities.get(entity.parent)
        if parent is None:
            raise TeamContextError(
                f"entity {entity.id} references missing parent {entity.parent}"
            )
        expected = PARENT_KIND.get(entity.kind)
        if expected is not None and parent.kind != expected:
            raise TeamContextError(
                f"{entity.kind} {entity.id} must sit under a {expected}, "
                f"but {parent.id} is a {parent.kind}"
            )
    for entity in catalog.entities.values():
        seen = {entity.id}
        current = entity
        while current.parent:
            if current.parent in seen:
                raise TeamContextError(f"cycle in entity parents at {current.parent}")
            seen.add(current.parent)
            current = catalog.entities[current.parent]
    for entry in catalog.knowledge.values():
        if entry.scope not in catalog.entities:
            raise TeamContextError(
                f"knowledge {entry.id} refers to unknown scope {entry.scope}"
            )
    if catalog.bind and catalog.bind not in catalog.entities:
        raise TeamContextError(f"bind refers to unknown entity {catalog.bind}")


def _normalized_path(value: str | Path) -> str:
    return os.path.normcase(os.path.abspath(str(value)))


def with_entry(catalog: Catalog, entry: KnowledgeEntry) -> Catalog:
    return replace(catalog, knowledge={**catalog.knowledge, entry.id: entry})
