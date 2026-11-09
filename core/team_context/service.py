"""Use cases for the team context layer: assemble, search, and record."""

from __future__ import annotations

import os
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any

import yaml

from core.team_context.assembler import DEFAULT_MAX_CHARS, assemble, resolve_chain
from core.team_context.catalog import (
    CATALOG_RELATIVE,
    Catalog,
    append_knowledge,
    load_catalog,
    save_catalog,
)
from core.team_context.models import (
    KNOWLEDGE_TYPES,
    KnowledgeEntry,
    TeamContextError,
)


class TeamContextService:
    """File-backed team context for one workspace."""
    def __init__(self, workspace: str | Path, home: str | Path | None = None) -> None:
        self.workspace = Path(workspace)
        self.home = Path(home) if home is not None else None

    def catalog(self) -> Catalog:
        return load_catalog(self.workspace, self.home)

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
            allowed = {entity.id for entity in resolve_chain(catalog, scope)}
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
        if directory is not None and _is_within(directory, self.workspace):
            path = append_knowledge(directory, entry)
            return {"entry": entry.to_mapping(), "path": str(path)}
        saved = save_catalog(
            self.workspace,
            replace(
                workspace_layer,
                knowledge={**workspace_layer.knowledge, entry.id: entry},
            ),
            home=self.home,
        )
        return {"entry": entry.to_mapping(), "path": str(saved)}

    def promote(self, entry_id: str, *, owner: str = "") -> dict[str, Any]:
        """Mark a draft as reviewed; governance stays an explicit act."""

        catalog = self.catalog()
        current = catalog.knowledge.get(entry_id)
        if current is None:
            raise TeamContextError(f"unknown knowledge entry {entry_id}")
        directory = catalog.directories.get(current.scope)
        if directory is not None and _is_within(directory, self.workspace):
            path = directory / "knowledge.yaml"
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) if path.is_file() else []
            items = [raw] if isinstance(raw, dict) else (raw or [])
            if not isinstance(items, list):
                raise TeamContextError(f"{path} must contain a list of entries")
            for item in items:
                if isinstance(item, dict) and str(item.get("id")) == entry_id:
                    item["status"] = "reviewed"
                    if owner:
                        item["owner"] = owner
                    _write_yaml(path, items)
                    updated = replace(
                        current, status="reviewed", owner=owner or current.owner
                    )
                    return updated.to_mapping()
            raise TeamContextError(
                f"{entry_id} is not in {path}; promote it where it is owned"
            )
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
