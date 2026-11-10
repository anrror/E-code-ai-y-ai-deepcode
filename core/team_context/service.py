"""Use cases for the team context layer: assemble, search, and record."""

from __future__ import annotations

import os
import shutil
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any

import yaml

from core.team_context.assembler import (
    DEFAULT_MAX_CHARS,
    SCOPE_RANK,
    assemble,
    referenced_entities,
    resolve_chain,
)
from core.team_context.catalog import (
    CATALOG_RELATIVE,
    LAYER_DIRECTORIES,
    PARENT_KINDS,
    TEAM_RELATIVE,
    Catalog,
    append_knowledge,
    load_catalog,
    remove_knowledge,
    save_catalog,
    update_knowledge,
)
from core.team_context.models import (
    ENTITY_KINDS,
    KNOWLEDGE_TYPES,
    KnowledgeEntry,
    TeamContextError,
    TeamEntity,
)


class TeamContextService:
    """File-backed team context for one workspace."""
    def __init__(self, workspace: str | Path, home: str | Path | None = None) -> None:
        self.workspace = Path(workspace)
        if home is None:
            # The shared team store sits next to the user's config: honour
            # DEEPCODE_HOME instead of assuming ~/.deepcode.
            from core.config import deepcode_home

            home = deepcode_home().parent
        self.home = Path(home)

    def catalog(self) -> Catalog:
        return load_catalog(self.workspace, self.home)

    def tree(self) -> dict[str, Any]:
        """Everything the Team explorer needs in one call, project-scoped."""

        catalog = self.catalog()
        bound = catalog.bind_for(self.workspace)
        counts: dict[str, int] = {}
        drafts: dict[str, int] = {}
        for entry in catalog.knowledge.values():
            counts[entry.scope] = counts.get(entry.scope, 0) + 1
            if entry.status == "draft":
                drafts[entry.scope] = drafts.get(entry.scope, 0) + 1
        entities = []
        for entity in sorted(
            catalog.entities.values(),
            key=lambda item: (SCOPE_RANK.get(item.kind, 99), item.id),
        ):
            entities.append(
                {
                    **entity.to_mapping(),
                    "configured": True,
                    "knowledgeCount": counts.get(entity.id, 0),
                    "draftCount": drafts.get(entity.id, 0),
                    "hasInstructions": entity.id in catalog.instructions,
                }
            )
        return {
            "bind": bound,
            "inChain": [
                entity.id for entity in resolve_chain(catalog, bound)
            ],
            "entities": entities,
        }

    def read_entity(self, entity_id: str) -> dict[str, Any]:
        """One layer: its metadata, instructions, and knowledge entries."""

        catalog = self.catalog()
        entity = catalog.entities.get(entity_id)
        if entity is None:
            raise TeamContextError(f"unknown entity {entity_id}")
        entries = [
            entry.to_mapping()
            for entry in sorted(
                catalog.knowledge.values(),
                key=lambda item: (
                    item.scope != entity_id,
                    item.status != "reviewed",
                    item.id,
                ),
            )
            if entry.scope == entity_id
        ]
        return {
            "entity": {
                **entity.to_mapping(),
                "configured": True,
                "knowledgeCount": len(entries),
                "draftCount": sum(
                    1 for entry in entries if entry.get("status") == "draft"
                ),
                "hasInstructions": entity_id in catalog.instructions,
            },
            "instructions": catalog.instructions.get(entity_id, ""),
            "knowledge": entries,
        }

    def create_entity(
        self,
        *,
        kind: str,
        slug: str,
        name: str = "",
        parent: str | None = None,
        external: bool = False,
        workspace: str | None = None,
        references: list[str] | None = None,
        designs: list[str] | None = None,
        codebases: list[str] | None = None,
        shared: bool = False,
    ) -> dict[str, Any]:
        """Create one layer directory in the workspace (``.deepcode/team``).

        A missing layer means "this level adds nothing"; creating one is the
        first write the Team page offers, before any knowledge exists.
        """

        kind = kind.strip()
        slug = slug.strip()
        if kind not in ENTITY_KINDS:
            raise TeamContextError(
                f"unknown layer {kind!r}; expected one of {ENTITY_KINDS}"
            )
        if not slug or any(part in slug for part in ("/", "\\", "..")):
            raise TeamContextError(
                "a layer directory name must not be empty or contain path separators"
            )
        if parent:
            parent_entity = self.catalog().entities.get(parent.strip())
            if parent_entity is None:
                raise TeamContextError(f"unknown parent {parent}")
            allowed = PARENT_KINDS.get(kind, ())
            if parent_entity.kind not in allowed:
                raise TeamContextError(
                    f"a {kind} layer can only sit under " + ", ".join(allowed)
                )
        if kind == "org":
            existing = [
                entity.id
                for entity in self.catalog().entities.values()
                if entity.kind == "org"
            ]
            if existing:
                raise TeamContextError(
                    f"the organization layer already exists ({existing[0]}); edit it instead of creating another"
                )
        base = (self.home or Path.home()) if shared else self.workspace
        directory = base / TEAM_RELATIVE / LAYER_DIRECTORIES[kind][0] / slug
        if (directory / "entity.yaml").exists():
            raise TeamContextError(f"the {kind} layer {slug!r} already exists")

        payload: dict[str, Any] = {
            "id": f"{kind}://{slug}",
            "kind": kind,
            "name": (name or slug).strip(),
        }
        if parent:
            payload["parent"] = parent.strip()
        if external:
            payload["external"] = True
        if workspace:
            payload["workspace"] = workspace.strip()
        for key, values in (
            ("references", references),
            ("designs", designs),
            ("codebases", codebases),
        ):
            cleaned = [
                str(value).strip() for value in values or [] if str(value).strip()
            ]
            if cleaned:
                payload[key] = cleaned

        directory.mkdir(parents=True, exist_ok=True)
        _write_yaml(directory / "entity.yaml", payload)
        return self.read_entity(f"{kind}://{slug}")

    def update_entity(
        self,
        entity_id: str,
        *,
        name: str | None = None,
        parent: str | None = None,
        external: bool | None = None,
        shared: bool = False,
    ) -> dict[str, Any]:
        """Edit a layer's name, parent, or flags; the id and kind stay fixed."""

        catalog = self.catalog()
        current = catalog.entities.get(entity_id)
        if current is None:
            raise TeamContextError(f"unknown entity {entity_id}")
        if parent is not None:
            new_parent = parent.strip() or None
            if new_parent is None:
                if current.kind == "org":
                    pass
            elif current.kind == "org":
                raise TeamContextError(
                    "the organization layer is a root and cannot have a parent"
                )
            else:
                candidate = catalog.entities.get(new_parent)
                if candidate is None:
                    raise TeamContextError(f"unknown parent {new_parent}")
                allowed = PARENT_KINDS.get(current.kind, ())
                if candidate.kind not in allowed:
                    raise TeamContextError(
                        f"a {current.kind} layer can only sit under "
                        + ", ".join(allowed)
                    )
                seen = {current.id}
                cursor: TeamEntity | None = candidate
                while cursor is not None:
                    if cursor.id in seen:
                        raise TeamContextError("that parent would create a cycle")
                    seen.add(cursor.id)
                    cursor = (
                        catalog.entities.get(cursor.parent) if cursor.parent else None
                    )
        else:
            new_parent = current.parent
        directory = catalog.directories.get(entity_id)
        if directory is None or not (
            _is_within(directory, self.workspace) or shared
        ):
            raise TeamContextError(
                "this layer lives in the shared team store; unlock it before editing"
            )
        updated = TeamEntity(
            id=current.id,
            kind=current.kind,
            name=(name if name is not None else current.name).strip(),
            parent=new_parent,
            external=current.external if external is None else external,
            workspace=current.workspace,
            references=current.references,
            designs=current.designs,
            codebases=current.codebases,
            archived=current.archived,
        )
        _write_yaml(directory / "entity.yaml", updated.to_mapping())
        return self.read_entity(entity_id)

    def assemble(
        self,
        *,
        task: str | None = None,
        include_external: bool = True,
        max_chars: int = DEFAULT_MAX_CHARS,
    ) -> dict[str, Any]:
        catalog = self.catalog()
        resolved = task or catalog.bind_for(self.workspace)
        result = assemble(
            catalog,
            task=resolved,
            include_external=include_external,
            max_chars=max_chars,
        )
        return {
            "task": result.task,
            "chain": [entity.id for entity in result.chain],
            "referenced": [entity.id for entity in result.referenced],
            "entryCount": len(result.entries),
            "externalExcluded": result.external_excluded,
            "blockedOverrides": list(result.blocked_overrides),
            "truncated": result.truncated,
            "text": result.text,
        }

    def search(
        self,
        query: str,
        *,
        scope: str | None = None,
        include_external: bool = True,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        catalog = self.catalog()
        needle = query.strip().casefold()
        allowed: set[str] | None = None
        if scope:
            chain = resolve_chain(catalog, scope)
            allowed = {entity.id for entity in chain}
            allowed.update(
                entity.id for entity in referenced_entities(catalog, chain)
            )
        rows = []
        for entry in catalog.knowledge.values():
            if entry.status == "deprecated":
                continue
            if allowed is not None and entry.scope not in allowed:
                continue
            if not include_external and entry.external:
                continue
            haystack = f"{entry.statement} {entry.rationale}".casefold()
            if needle and needle not in haystack:
                continue
            rows.append(entry)
        rows.sort(key=lambda entry: (entry.status != "reviewed", entry.external, entry.id))
        return [
            {
                "id": entry.id,
                "scope": entry.scope,
                "type": entry.type,
                "statement": entry.statement,
                "rationale": entry.rationale,
                "confidence": entry.confidence,
                "status": entry.status,
                "source": entry.source,
                "owner": entry.owner,
            }
            for entry in rows[: max(1, limit)]
        ]

    def add(
        self,
        *,
        scope: str,
        type: str,
        statement: str,
        rationale: str = "",
        evidence: str = "",
        confidence: str = "medium",
        supersedes: str | None = None,
        source: str = "internal",
        owner: str = "",
        shared: bool = False,
    ) -> dict[str, Any]:
        """Record a draft entry in the workspace layer (review promotes it)."""

        if type not in KNOWLEDGE_TYPES:
            raise TeamContextError(
                f"unknown knowledge type {type!r}; expected one of {KNOWLEDGE_TYPES}"
            )
        catalog = self.catalog()
        if scope not in catalog.entities:
            raise TeamContextError(f"unknown scope {scope!r}")
        entry = KnowledgeEntry(
            id=f"K-{uuid.uuid4().hex[:12]}",
            scope=scope,
            type=type,
            statement=statement,
            rationale=rationale,
            evidence=evidence,
            confidence=confidence,
            status="draft",
            owner=owner,
            source=source,
            supersedes=supersedes,
        )
        workspace_layer = self._workspace_layer()
        directory = catalog.directories.get(scope)
        if directory is not None:
            if _is_within(directory, self.workspace) or shared:
                path = append_knowledge(directory, entry)
                return {"entry": entry.to_mapping(), "path": str(path)}
            raise TeamContextError(
                "this layer lives in the shared team store; unlock it before editing"
            )
        saved = save_catalog(
            self.workspace,
            replace(
                workspace_layer,
                knowledge={**workspace_layer.knowledge, entry.id: entry},
            ),
            home=self.home,
        )
        return {"entry": entry.to_mapping(), "path": str(saved)}

    def promote(
        self, entry_id: str, *, owner: str = "", shared: bool = False
    ) -> dict[str, Any]:
        """Mark a draft as reviewed; governance stays an explicit act."""

        catalog = self.catalog()
        current = catalog.knowledge.get(entry_id)
        if current is None:
            raise TeamContextError(f"unknown knowledge entry {entry_id}")
        directory = catalog.directories.get(current.scope)
        if directory is not None:
            if not (_is_within(directory, self.workspace) or shared):
                raise TeamContextError(
                    "this entry lives in the shared team layer; unlock it before editing"
                )
            updated = replace(current, status="reviewed", owner=owner or current.owner)
            update_knowledge(directory, updated)
            return updated.to_mapping()
        workspace_layer = self._workspace_layer()
        entry = workspace_layer.knowledge.get(entry_id)
        if entry is None:
            raise TeamContextError(
                f"{entry_id} is not in {CATALOG_RELATIVE}; promote it where it is owned"
            )
        updated = replace(entry, status="reviewed", owner=owner or entry.owner)
        save_catalog(
            self.workspace,
            replace(
                workspace_layer,
                knowledge={**workspace_layer.knowledge, entry_id: updated},
            ),
            home=self.home,
        )
        return updated.to_mapping()

    def remove_entity(
        self, entity_id: str, *, shared: bool = False, force: bool = False
    ) -> dict[str, Any]:
        """Delete a layer directory; knowledge and children must be dealt with first."""

        catalog = self.catalog()
        current = catalog.entities.get(entity_id)
        if current is None:
            raise TeamContextError(f"unknown entity {entity_id}")
        directory = catalog.directories.get(entity_id)
        if directory is None or not (
            _is_within(directory, self.workspace) or shared
        ):
            raise TeamContextError(
                "this layer lives in the shared team store; unlock it before editing"
            )
        children = [
            entity.id
            for entity in catalog.entities.values()
            if entity.parent == entity_id
        ]
        if children:
            raise TeamContextError(
                "delete or reparent the child layers first: " + ", ".join(children)
            )
        owned = [
            entry for entry in catalog.knowledge.values() if entry.scope == entity_id
        ]
        if owned and not force:
            raise TeamContextError(
                f"this layer still owns {len(owned)} knowledge entries; confirm the delete"
            )
        shutil.rmtree(directory)
        return {"removed": entity_id, "path": str(directory)}

    def deprecate_entry(
        self, entry_id: str, *, shared: bool = False
    ) -> dict[str, Any]:
        """Retire a reviewed entry without erasing the record."""

        catalog = self.catalog()
        current = catalog.knowledge.get(entry_id)
        if current is None:
            raise TeamContextError(f"unknown knowledge entry {entry_id}")
        directory = catalog.directories.get(current.scope)
        if directory is None or not (
            _is_within(directory, self.workspace) or shared
        ):
            raise TeamContextError(
                "this entry lives in the shared team layer; unlock it before editing"
            )
        updated = replace(current, status="deprecated")
        update_knowledge(directory, updated)
        return {"entry": updated.to_mapping()}

    def update_entry(
        self,
        entry_id: str,
        *,
        type: str | None = None,
        statement: str | None = None,
        rationale: str | None = None,
        evidence: str | None = None,
        confidence: str | None = None,
        owner: str | None = None,
        shared: bool = False,
    ) -> dict[str, Any]:
        """Edit a draft entry in place; reviewed entries stay immutable."""

        catalog = self.catalog()
        current = catalog.knowledge.get(entry_id)
        if current is None:
            raise TeamContextError(f"unknown knowledge entry {entry_id}")
        if current.status != "draft":
            raise TeamContextError(
                "only draft entries can be edited; add a replacement and deprecate this one"
            )
        directory = catalog.directories.get(current.scope)
        if directory is None or not (
            _is_within(directory, self.workspace) or shared
        ):
            raise TeamContextError(
                "this entry lives in the shared team layer; unlock it before editing"
            )
        updated = KnowledgeEntry(
            id=current.id,
            scope=current.scope,
            type=type or current.type,
            statement=statement if statement is not None else current.statement,
            rationale=rationale if rationale is not None else current.rationale,
            evidence=evidence if evidence is not None else current.evidence,
            confidence=confidence or current.confidence,
            status=current.status,
            owner=owner if owner is not None else current.owner,
            source=current.source,
            supersedes=current.supersedes,
        )
        path = update_knowledge(directory, updated)
        return {"entry": updated.to_mapping(), "path": str(path)}

    def remove_entry(self, entry_id: str, *, shared: bool = False) -> dict[str, Any]:
        """Delete a draft; reviewed entries are part of the record, not erasable."""

        catalog = self.catalog()
        current = catalog.knowledge.get(entry_id)
        if current is None:
            raise TeamContextError(f"unknown knowledge entry {entry_id}")
        if current.status != "draft":
            raise TeamContextError(
                "only draft entries can be removed; reviewed entries are part of the record"
            )
        directory = catalog.directories.get(current.scope)
        if directory is None or not (
            _is_within(directory, self.workspace) or shared
        ):
            raise TeamContextError(
                "this entry lives in the shared team layer; unlock it before editing"
            )
        path = remove_knowledge(directory, entry_id)
        return {"removed": entry_id, "path": str(path)}

    def _workspace_layer(self) -> Catalog:
        path = self.workspace / CATALOG_RELATIVE
        if not path.is_file():
            return Catalog()
        from core.team_context.catalog import _read_catalog  # local: private helper

        return _read_catalog(path)


def _is_within(directory: Path, root: Path) -> bool:
    try:
        directory.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _write_yaml(path: Path, payload: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    os.replace(temporary, path)
