"""``team_context`` — read the team catalog and record draft findings."""

from __future__ import annotations

import json
from typing import Any

from core.agent_runtime.tools.base import Tool, tool_parameters
from core.team_context.models import KNOWLEDGE_TYPES, TeamContextError
from core.team_context.service import TeamContextService


@tool_parameters(
    {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["assemble", "search", "add"],
                "description": (
                    "assemble: the task's assembled context block; search: find "
                    "entries by text; add: record a draft entry for review."
                ),
            },
            "query": {
                "type": "string",
                "description": "Text to look for when action is search.",
            },
            "scope": {
                "type": "string",
                "description": (
                    "Entity id to scope a search to (for example task://LEAD-1234), "
                    "or the owning scope for add."
                ),
            },
            "statement": {
                "type": "string",
                "description": "The fact to record when action is add.",
            },
            "type": {
                "type": "string",
                "enum": list(KNOWLEDGE_TYPES),
                "description": "Knowledge type when action is add.",
            },
            "rationale": {"type": "string", "description": "Why it is true."},
            "evidence": {"type": "string", "description": "Where it was observed."},
            "confidence": {
                "type": "string",
                "enum": ["high", "medium", "low"],
                "description": "How sure the recorder is.",
            },
            "include_external": {
                "type": "boolean",
                "description": (
                    "Include external-batch references, clearly labelled as "
                    "unverified. Default false for facts, true for search."
                ),
            },
            "limit": {
                "type": "integer",
                "description": "Maximum search results (default 20).",
            },
        },
        "required": ["action"],
    }
)
class TeamContextTool(Tool):
    """Six-layer team context: assemble, search, and record drafts."""

    def __init__(
        self,
        workspace,
        *,
        home=None,
        allow_write: bool = True,
    ) -> None:
        self._workspace = workspace
        self._home = home
        self._allow_write = allow_write

    @property
    def name(self) -> str:
        return "team_context"

    @property
    def description(self) -> str:
        return (
            "Read the organization's six-layer team context (org, line, project, "
            "batch, version, task). assemble returns the task's context with red "
            "lines, reviewed facts, drafts, and labelled external references; "
            "search finds entries by text; add records a draft finding that a "
            "reviewer can promote. Red lines cannot be overridden, and external "
            "entries are never team facts until confirmed."
        )

    async def execute(self, **kwargs: Any) -> str:
        action = str(kwargs.get("action") or "assemble").strip().lower()
        service = TeamContextService(self._workspace, self._home)
        try:
            if action == "assemble":
                result = service.assemble(
                    include_external=bool(kwargs.get("include_external", False)),
                )
                return json.dumps(result, ensure_ascii=False, indent=2)
            if action == "search":
                query = str(kwargs.get("query") or "").strip()
                if not query:
                    return "error: query is required for search"
                rows = service.search(
                    query,
                    scope=(str(kwargs["scope"]).strip() if kwargs.get("scope") else None),
                    include_external=bool(kwargs.get("include_external", True)),
                    limit=int(kwargs.get("limit") or 20),
                )
                return json.dumps(rows, ensure_ascii=False, indent=2)
            if action == "add":
                if not self._allow_write:
                    return "error: this Session is read only"
                scope = str(kwargs.get("scope") or "").strip()
                statement = str(kwargs.get("statement") or "").strip()
                if not scope or not statement:
                    return "error: scope and statement are required for add"
                result = service.add(
                    scope=scope,
                    type=str(kwargs.get("type") or "lesson"),
                    statement=statement,
                    rationale=str(kwargs.get("rationale") or ""),
                    evidence=str(kwargs.get("evidence") or ""),
                    confidence=str(kwargs.get("confidence") or "medium"),
                )
                return json.dumps(result, ensure_ascii=False, indent=2)
            return f"error: unknown action {action!r}"
        except (TeamContextError, ValueError, OSError) as exc:
            return f"error: {exc}"
