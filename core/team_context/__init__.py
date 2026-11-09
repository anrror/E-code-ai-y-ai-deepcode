"""Team-level context: six-layer scopes, knowledge entries, and assembly.

The design this implements (see ``docs/guide/team-context.md``) separates what
must be true (repository facts, CI-checked) from what is worth knowing (team
knowledge). This package is the repository side plus a file-backed knowledge
catalog:

* entities form the chain org -> line -> project -> batch -> version -> task;
* every knowledge entry belongs to exactly one scope (single ownership), and
  other scopes reference it instead of copying it;
* assembly walks the chain near-to-far, keeps organization red lines
  non-overridable, and labels external batches as unverified instead of
  silently promoting them to team facts.
"""

from core.team_context.assembler import AssembledContext, assemble, resolve_chain
from core.team_context.catalog import (
    Catalog,
    default_catalog_paths,
    load_catalog,
    save_catalog,
)
from core.team_context.models import (
    ENTITY_KINDS,
    KNOWLEDGE_TYPES,
    KnowledgeEntry,
    TeamContextError,
    TeamEntity,
)
from core.team_context.prompt import team_context_preamble
from core.team_context.service import TeamContextService

__all__ = [
    "ENTITY_KINDS",
    "KNOWLEDGE_TYPES",
    "AssembledContext",
    "Catalog",
    "KnowledgeEntry",
    "TeamContextError",
    "TeamContextService",
    "TeamEntity",
    "assemble",
    "default_catalog_paths",
    "load_catalog",
    "resolve_chain",
    "save_catalog",
    "team_context_preamble",
]
