"""System-prompt addendum: the assembled team context for this workspace."""

from __future__ import annotations

import logging
from pathlib import Path

from core.team_context.assembler import DEFAULT_MAX_CHARS, assemble
from core.team_context.catalog import load_catalog

logger = logging.getLogger(__name__)

_USAGE = (
    "Team context comes from the organization's catalog. Red lines are "
    "non-negotiable; drafts and external references are unverified, so confirm "
    "them with tools or a human before treating them as team facts. Use the "
    "team_context tool to search the catalog or record a draft finding."
)


def team_context_preamble(
    workspace: str | Path,
    home: str | Path | None = None,
    *,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> str:
    """The team context block, or ``""`` when the workspace declares no task.

    Prompt assembly must never fail a session: a malformed catalog is logged
    and skipped, exactly like an unreadable instruction file.
    """

    try:
        catalog = load_catalog(workspace, home)
        task = catalog.bind_for(workspace)
        if not task:
            return ""
        assembled = assemble(catalog, task=task, max_chars=max_chars)
    except Exception:
        logger.exception("team context could not be assembled for %s", workspace)
        return ""
    if not assembled.entries:
        return ""
    return f"{assembled.text}\n\n{_USAGE}"
