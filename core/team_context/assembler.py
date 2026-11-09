"""Assemble the team context for one task along its ownership chain.

Precedence follows the design document:

1. the chain is walked task -> version -> batch -> project -> line -> org, so
   a nearer scope is read first and wins on the same id;
2. organization red lines are never overridden by a narrower scope;
3. reviewed entries outrank drafts, which outrank external references;
4. external batches are labelled ``external`` and never presented as team
   facts unless the caller explicitly includes them;
5. the rendered block is capped, dropping the weakest entries first.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from core.team_context.catalog import Catalog
from core.team_context.models import KnowledgeEntry, TeamEntity

SCOPE_RANK = {"task": 0, "version": 1, "batch": 2, "project": 3, "line": 4, "org": 5}
_STATUS_RANK = {"reviewed": 0, "draft": 1}
_CONFIDENCE_RANK = {"high": 0, "medium": 1, "low": 2}
DEFAULT_MAX_CHARS = 6000


@dataclass(frozen=True, slots=True)
class AssembledContext:
    task: str | None
    chain: tuple[TeamEntity, ...]
    entries: tuple[KnowledgeEntry, ...]
    text: str
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


def _scope_rank(entry: KnowledgeEntry, catalog: Catalog, chain: Sequence[TeamEntity]) -> int:
    entity = catalog.entities.get(entry.scope)
    return SCOPE_RANK.get(entity.kind if entity else "", 9)


def _ordered_entries(
    catalog: Catalog,
    chain: Sequence[TeamEntity],
    *,
    include_external: bool,
) -> tuple[list[KnowledgeEntry], int, tuple[str, ...]]:
    scopes = [entity.id for entity in chain]
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
            # A narrower scope tried to override an organization red line.
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
            _scope_rank(entry, catalog, chain),
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


def _label(entry: KnowledgeEntry, catalog: Catalog) -> str:
    tags: list[str] = []
    if entry.type == "redline":
        tags.append("red line")
    if entry.status == "draft":
        tags.append("draft")
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
    entries: Sequence[KnowledgeEntry],
    catalog: Catalog,
    instructions: Sequence[tuple[str, str]],
    *,
    max_chars: int,
) -> tuple[str, bool]:
    header = (
        f'<team-context task="{task or "unbound"}" '
        f'chain="{" > ".join(entity.id for entity in chain)}">'
    )
    footer = "</team-context>"
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
    body: list[str] = []
    used = len(header) + len(footer)
    truncated = False
    if instructions:
        title = "layer instructions (a nearer layer is read later and wins):"
        body.append(title)
        used += len(title) + 1
        for entity_id, text in instructions:
            block = f"--- {entity_id} ---\n{text}"
            if used + len(block) + 1 > max_chars:
                truncated = True
                break
            body.append(block)
            used += len(block) + 1
    for title, group in groups:
        if truncated:
            break
        if not group:
            continue
        section = [title]
        used += len(title) + 1
        for entry in group:
            line = _label(entry, catalog)
            if used + len(line) + 1 > max_chars:
                truncated = True
                break
            section.append(line)
            used += len(line) + 1
        body.extend(section)
        if truncated:
            break
    text = "\n".join([header, *body, footer])
    return text, truncated


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
    entries, excluded, blocked = _ordered_entries(
        catalog, chain, include_external=include_external
    )
    # Far first, near last: the nearest layer's instructions are the last word,
    # matching "a nearer rule wins" without discarding the wider context.
    instruction_blocks = tuple(
        (entity.id, catalog.instructions[entity.id])
        for entity in reversed(chain)
        if entity.id in catalog.instructions
    )
    text, truncated = _render(
        start, chain, entries, catalog, instruction_blocks, max_chars=max_chars
    )
    included = tuple(
        entry for entry in entries if _label(entry, catalog) and entry.statement in text
    )
    return AssembledContext(
        task=start,
        chain=chain,
        entries=included,
        text=text,
        external_excluded=excluded,
        blocked_overrides=blocked,
        truncated=truncated,
    )
