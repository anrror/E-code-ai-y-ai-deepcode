"""Read, validate, and write the file-backed team catalog.

Every layer is configured by its own directory, so an organization can change
one layer without touching the others:

```text
.deepcode/team/
  company/auto-sub/          entity.yaml  knowledge.yaml  AGENTS.md
  line/auto-outsourcing/     entity.yaml  knowledge.yaml  AGENTS.md
  project/changan/           entity.yaml  knowledge.yaml  AGENTS.md
  batch/phase-1/             entity.yaml  knowledge.yaml  AGENTS.md
  version/v1.2.0/            entity.yaml  knowledge.yaml  AGENTS.md
  task/LEAD-1234/            entity.yaml  knowledge.yaml  AGENTS.md
```

``entity.yaml`` may omit ``id`` (derived as ``kind://directory-name``) and
``name`` (defaults to the directory name); ``parent`` makes the chain. Entries
live in ``knowledge.yaml`` (a list) and ``knowledge/*.yaml`` (one entry or a
list per file). ``AGENTS.md`` is that layer's standing instructions.

Both roots are read, the workspace one winning by id:

``~/.deepcode/team/``        organization/line content shared by every workspace
``<workspace>/.deepcode/team/``  the workspace's layers and its task binding

The single-file ``catalog.yaml`` form is still read for compatibility, and is
where programmatic additions land when a scope has no directory of its own.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import yaml

from core.team_context.models import (
    KIND_ALIASES,
    KnowledgeEntry,
    TeamContextError,
    TeamEntity,
)

TEAM_RELATIVE = Path(".deepcode") / "team"
CATALOG_RELATIVE = TEAM_RELATIVE / "catalog.yaml"
# Directory name per layer; ``company`` is the design document's word for org.
LAYER_DIRECTORIES = {
    "org": ("company", "org"),
    "line": ("line",),
    "project": ("project",),
    "batch": ("batch",),
    "version": ("version",),
    "task": ("task",),
}
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
    instructions: dict[str, str] = field(default_factory=dict)
    directories: dict[str, Path] = field(default_factory=dict)
    bind: str | None = None

    def merged(self, other: Catalog) -> Catalog:
        """Nearer layer wins for the same id; its binding wins if set."""

        return Catalog(
            entities={**self.entities, **other.entities},
            knowledge={**self.knowledge, **other.knowledge},
            instructions={**self.instructions, **other.instructions},
            directories={**self.directories, **other.directories},
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


def default_catalog_paths(
    workspace: str | Path, home: str | Path | None
) -> tuple[Path, Path]:
    root = Path(home) if home is not None else Path.home()
    return root / CATALOG_RELATIVE, Path(workspace) / CATALOG_RELATIVE


def load_catalog(workspace: str | Path, home: str | Path | None = None) -> Catalog:
    """Load the global then workspace roots, validating the merged result."""

    root = Path(home) if home is not None else Path.home()
    catalog = Catalog()
    for base in (root / TEAM_RELATIVE, Path(workspace) / TEAM_RELATIVE):
        if base.is_dir():
            catalog = catalog.merged(_read_root(base))
    _validate(catalog)
    return catalog


def save_catalog(
    workspace: str | Path,
    catalog: Catalog,
    *,
    home: str | Path | None = None,
) -> Path:
    """Write the workspace ``catalog.yaml``, leaving the layer dirs alone."""

    root = Path(home) if home is not None else Path.home()
    merged = (
        _read_root(root / TEAM_RELATIVE)
        if (root / TEAM_RELATIVE).is_dir()
        else Catalog()
    )
    _validate(merged.merged(catalog))
    path = Path(workspace) / CATALOG_RELATIVE
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "entities": [entry.to_mapping() for entry in catalog.entities.values()],
        "knowledge": [entry.to_mapping() for entry in catalog.knowledge.values()],
    }
    if catalog.bind:
        payload["bind"] = catalog.bind
    _atomic_yaml(path, payload)
    return path


def append_knowledge(directory: Path, entry: KnowledgeEntry) -> Path:
    """Add or replace one entry in a layer directory's ``knowledge.yaml``."""

    path = directory / "knowledge.yaml"
    items: list[dict[str, Any]] = []
    if path.is_file():
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or []
        if isinstance(raw, dict):
            raw = [raw]
        if not isinstance(raw, list):
            raise TeamContextError(f"{path} must contain a list of entries")
        items = [item for item in raw if isinstance(item, dict)]
    items = [item for item in items if str(item.get("id")) != entry.id]
    items.append(entry.to_mapping())
    _atomic_yaml(path, items)
    return path


def _atomic_yaml(path: Path, payload: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _read_root(base: Path) -> Catalog:
    catalog = Catalog()
    single = base / "catalog.yaml"
    if single.is_file():
        catalog = catalog.merged(_read_catalog(single))
    for kind, names in LAYER_DIRECTORIES.items():
        for name in names:
            layer_root = base / name
            if not layer_root.is_dir():
                continue
            for directory in sorted(layer_root.iterdir()):
                if directory.is_dir():
                    catalog = catalog.merged(_read_layer(kind, directory))
    return catalog


def _read_layer(kind: str, directory: Path) -> Catalog:
    slug = directory.name
    raw: dict[str, Any] = {}
    entity_file = directory / "entity.yaml"
    if entity_file.is_file():
        loaded = yaml.safe_load(entity_file.read_text(encoding="utf-8")) or {}
        if not isinstance(loaded, dict):
            raise TeamContextError(f"{entity_file} must contain a mapping")
        raw = loaded
    declared_kind = str(raw.get("kind") or kind)
    entity = TeamEntity(
        id=str(raw.get("id") or f"{kind}://{slug}"),
        kind=KIND_ALIASES.get(declared_kind, declared_kind),
        name=str(raw.get("name") or slug),
        parent=(str(raw["parent"]).strip() if raw.get("parent") else None),
        external=bool(raw.get("external", False)),
        workspace=(str(raw["workspace"]).strip() if raw.get("workspace") else None),
    )
    if entity.kind != kind:
        raise TeamContextError(
            f"{directory} declares kind {entity.kind} but sits under {kind}/"
        )
    knowledge: dict[str, KnowledgeEntry] = {}
    files = [directory / "knowledge.yaml"]
    files.extend(sorted((directory / "knowledge").glob("*.yaml")))
    for path in files:
        if not path.is_file():
            continue
        loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or []
        items = [loaded] if isinstance(loaded, dict) else loaded
        if not isinstance(items, list):
            raise TeamContextError(f"{path} must contain an entry or a list of entries")
        for item in items:
            entry = KnowledgeEntry.from_mapping(item)
            if entry.scope != entity.id:
                raise TeamContextError(
                    f"{path} holds {entry.scope} but sits under {entity.id}"
                )
            knowledge[entry.id] = entry
    instructions = ""
    instructions_file = directory / "AGENTS.md"
    if instructions_file.is_file():
        instructions = instructions_file.read_text(encoding="utf-8").strip()
    return Catalog(
        entities={entity.id: entity},
        knowledge=knowledge,
        instructions={entity.id: instructions} if instructions else {},
        directories={entity.id: directory},
    )


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
