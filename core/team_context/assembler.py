"""Assemble the team context for one task along its ownership chain.

Precedence follows the design document:

1. the chain is walked task -> version -> batch -> project -> line -> org, so
   a nearer scope is read first and wins on the same id;
2. organization red lines are never overridden by a narrower scope;
3. reviewed entries outrank drafts, which outrank external references;
4. external batches are labelled ``external`` and never presented as team
   facts unless the caller explicitly includes them;
5. referenced products (a company PaaS product, a line's standard or
   historical product) contribute their design/codebase pointers and knowledge
   **after** the chain: a reference informs, it never overrides the project;
6. the rendered block is capped, dropping the weakest entries first.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from core.team_context.catalog import Catalog
from core.team_context.models import KnowledgeEntry, TeamEntity

SCOPE_RANK = {
    "task": 0,
    "version": 1,
    "batch": 2,
    "project": 3,
    "line": 4,
    "org": 5,
    "product": 6,
}
_STATUS_RANK = {"reviewed": 0, "draft": 1}
_CONFIDENCE_RANK = {"high": 0, "medium": 1, "low": 2}
DEFAULT_MAX_CHARS = 6000


@dataclass(frozen=True, slots=True)
class AssembledContext:
    task: str | None
    chain: tuple[TeamEntity, ...]
    entries: tuple[KnowledgeEntry, ...]
    text: str
    referenced: tuple[TeamEntity, ...] = ()
    descendants: tuple[TeamEntity, ...] = ()
    external_excluded: int = 0
    blocked_overrides: tuple[str, ...] = ()
    truncated: bool = False


def resolve_chain(catalog: Catalog, start_id: str | None) -> tuple[TeamEntity, ...]:
    """The task's main-ownership chain, nearest first."""

    if not start_id:
        return ()
    chain: list[TeamEntity] = []
    current = catalog.entities.get(start_id)
    guard: set[str] = set()
    while current is not None and current.id not in guard:
        guard.add(current.id)
        chain.append(current)
        current = catalog.entities.get(current.parent) if current.parent else None
    return tuple(chain)


def referenced_entities(
    catalog: Catalog, chain: Sequence[TeamEntity]
) -> tuple[TeamEntity, ...]:
    """Products (or other capabilities) the chain cites, one hop out.

    ``references`` is deliberately not transitive: a product references what it
    needs, and an outside capability is not silently pulled in through a second
    hop.
    """

    seen = {entity.id for entity in chain}
    result: list[TeamEntity] = []
    for entity in chain:
        for reference in entity.references:
            target = catalog.entities.get(reference)
            if target is not None and target.id not in seen:
                seen.add(target.id)
                result.append(target)
    return tuple(result)


def descendant_entities(catalog: Catalog, start_id: str | None) -> tuple[TeamEntity, ...]:
    """Every layer below ``start``, breadth first.

    A preview of a product or a line must show the projects and batches that
    actually consume it, not only the ancestors above it.
    """

    if not start_id:
        return ()
    guard = {entity.id for entity in resolve_chain(catalog, start_id)}
    frontier = [start_id]
    found: list[TeamEntity] = []
    while frontier:
        current = frontier.pop(0)
        for entity in catalog.entities.values():
            if entity.parent != current or entity.id in guard:
                continue
            guard.add(entity.id)
            found.append(entity)
            frontier.append(entity.id)
    return tuple(found)


def _scope_rank(entry: KnowledgeEntry, catalog: Catalog) -> int:
    entity = catalog.entities.get(entry.scope)
    return SCOPE_RANK.get(entity.kind if entity else "", 9)


def _ordered_entries(
    catalog: Catalog,
    chain: Sequence[TeamEntity],
    referenced: Sequence[TeamEntity],
    *,
    include_external: bool,
    descendants: Sequence[TeamEntity] = (),
) -> tuple[list[KnowledgeEntry], int, tuple[str, ...]]:
    chain_ids = {entity.id for entity in chain}
    descendant_ids = {entity.id for entity in descendants}
    referenced_ids = {entity.id for entity in referenced}
    scopes = chain_ids | descendant_ids | referenced_ids
    if not scopes:
        return [], 0, ()
    candidates = [
        entry
        for entry in catalog.knowledge.values()
        if entry.scope in scopes and entry.status != "deprecated"
    ]
    superseded = {entry.supersedes for entry in candidates if entry.supersedes}
    redline_ids = {entry.id for entry in candidates if entry.type == "redline"}
    blocked: list[str] = []
    kept: list[KnowledgeEntry] = []
    for entry in candidates:
        # A red line is never removed by a supersedes pointer: that is exactly
        # the override the rule forbids, so the pointer is rejected below.
        if entry.id in superseded and entry.type != "redline":
            continue
        if entry.supersedes in redline_ids and entry.type != "redline":
            blocked.append(entry.id)
            continue
        kept.append(entry)
    external_excluded = 0
    if not include_external:
        before = len(kept)
        kept = [entry for entry in kept if not _is_external(entry, catalog)]
        external_excluded = before - len(kept)
    kept.sort(
        key=lambda entry: (
            0 if entry.type == "redline" else 1,
            0
            if entry.scope in chain_ids
            else 1
            if entry.scope in descendant_ids
            else 2,
            _scope_rank(entry, catalog),
            _STATUS_RANK.get(entry.status, 2),
            1 if _is_external(entry, catalog) else 0,
            _CONFIDENCE_RANK.get(entry.confidence, 3),
            entry.id,
        )
    )
    return kept, external_excluded, tuple(blocked)


def _is_external(entry: KnowledgeEntry, catalog: Catalog) -> bool:
    if entry.external:
        return True
    entity = catalog.entities.get(entry.scope)
    return bool(entity and entity.external)


def _is_historical(entity: TeamEntity | None) -> bool:
    return bool(entity and entity.archived)


def _label(
    entry: KnowledgeEntry,
    catalog: Catalog,
    referenced_ids: set[str],
    descendant_ids: set[str] | None = None,
) -> str:
    entity = catalog.entities.get(entry.scope)
    tags: list[str] = []
    if entry.type == "redline":
        tags.append("red line")
    if entry.status == "draft":
        tags.append("draft")
    if entry.scope in referenced_ids:
        tags.append(f"referenced: {entry.scope}")
    if descendant_ids and entry.scope in descendant_ids:
        tags.append(f"sub-layer: {entry.scope}")
    if _is_historical(entity):
        tags.append("historical")
    if _is_external(entry, catalog):
        tags.append(f"external: {entry.source}, unverified")
    prefix = f"[{', '.join(tags)}] " if tags else ""
    line = f"- {prefix}{entry.statement}"
    if entry.rationale:
        line += f" — {entry.rationale}"
    return line


def _render(
    task: str | None,
    chain: Sequence[TeamEntity],
    referenced: Sequence[TeamEntity],
    descendants: Sequence[TeamEntity],
    entries: Sequence[KnowledgeEntry],
    catalog: Catalog,
    instructions: Sequence[tuple[str, str]],
    *,
    max_chars: int,
) -> tuple[str, bool]:
    header = (
        f'<team-context task="{task or "unbound"}" '
        f'chain="{" > ".join(entity.id for entity in chain)}"'
    )
    if referenced:
        header += f' referenced="{" ".join(entity.id for entity in referenced)}"'
    if descendants:
        header += f' descendants="{" ".join(entity.id for entity in descendants)}"'
    header += ">"
    footer = "</team-context>"
    body: list[str] = []
    used = len(header) + len(footer)
    truncated = False

    def append(text: str) -> bool:
        nonlocal used, truncated
        if used + len(text) + 1 > max_chars:
            truncated = True
            return False
        body.append(text)
        used += len(text) + 1
        return True

    if instructions:
        append("layer instructions (a nearer layer is read later and wins):")
        for entity_id, text in instructions:
            if not append(f"--- {entity_id} ---\n{text}"):
                break

    referenced_ids = {entity.id for entity in referenced}
    descendant_ids = {entity.id for entity in descendants}
    groups: list[tuple[str, list[KnowledgeEntry]]] = [
        ("red lines (organization, never overridden):", []),
        ("team facts (reviewed):", []),
        ("drafts (unreviewed):", []),
        ("external references (unverified; not team facts):", []),
    ]
    for entry in entries:
        if entry.type == "redline":
            groups[0][1].append(entry)
        elif _is_external(entry, catalog):
            groups[3][1].append(entry)
        elif entry.status == "draft":
            groups[2][1].append(entry)
        else:
            groups[1][1].append(entry)
    for title, group in groups:
        if truncated:
            break
        if not group:
            continue
        if not append(title):
            break
        for entry in group:
            if not append(_label(entry, catalog, referenced_ids, descendant_ids)):
                break

    pointers = [
        entity
        for entity in referenced
        if entity.designs or entity.codebases
    ]
    if pointers and not truncated:
        append("referenced products (design and codebase pointers):")
        for entity in pointers:
            tag = " [historical]" if entity.archived else ""
            parts = [f"{entity.id}{tag}"]
            if entity.codebases:
                parts.append("codebases: " + ", ".join(entity.codebases))
            if entity.designs:
                parts.append("designs: " + ", ".join(entity.designs))
            if not append("- " + " | ".join(parts)):
                break

    return "\n".join([header, *body, footer]), truncated


def assemble(
    catalog: Catalog,
    *,
    task: str | None = None,
    include_external: bool = True,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> AssembledContext:
    """Render the context block for ``task`` (or the catalog binding)."""

    start = task or catalog.bind
    chain = resolve_chain(catalog, start)
    descendants = descendant_entities(catalog, start)
    referenced = referenced_entities(catalog, chain)
    entries, excluded, blocked = _ordered_entries(
        catalog,
        chain,
        referenced,
        include_external=include_external,
        descendants=descendants,
    )
    # Far first, near last: the nearest layer's instructions are the last word,
    # matching "a nearer rule wins" without discarding the wider context.
    # Referenced product instructions come after the chain, labelled.
    blocks = [
        (entity.id, catalog.instructions[entity.id])
        for entity in reversed(chain)
        if entity.id in catalog.instructions
    ]
    blocks.extend(
        (f"sub-layer: {entity.id}", catalog.instructions[entity.id])
        for entity in descendants
        if entity.id in catalog.instructions
    )
    blocks.extend(
        (f"referenced: {entity.id}", catalog.instructions[entity.id])
        for entity in referenced
        if entity.id in catalog.instructions
    )
    text, truncated = _render(
        start,
        chain,
        referenced,
        descendants,
        entries,
        catalog,
        blocks,
        max_chars=max_chars,
    )
    included = tuple(
        entry for entry in entries if entry.statement and entry.statement in text
    )
    return AssembledContext(
        task=start,
        chain=chain,
        referenced=referenced,
        descendants=descendants,
        entries=included,
        text=text,
        external_excluded=excluded,
        blocked_overrides=blocked,
        truncated=truncated,
    )
