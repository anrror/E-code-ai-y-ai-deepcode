"""``wiki`` and ``codegraph`` — retrieval seams over the team context pointers.

These tools are the DFcode side of two external systems the design explicitly
*does not* reimplement (see ``docs/design/team-context.md`` "非目标"): a
company wiki (Confluence/GitBook) and a CodeGraph index. DFcode only registers
where those systems live — via ``TeamEntity.designs`` (documents) and
``TeamEntity.codebases`` (code/git) — and these tools let the Agent *retrieve*
through those pointers on demand.

* ``wiki`` reads document pointers: local ``*.md`` paths are read directly
  (read-only, capped), remote URLs are handed back as a fetch instruction
  (the Agent uses its existing ``web`` tool for those).
* ``codegraph`` answers structural questions about code pointers: for local
  code paths it composes the existing ``grep``/``glob`` capabilities into a
  symbol/definition/reference lookup; for remote git URLs it returns the
  pointer so the Agent can clone or browse it.

Neither tool writes anything, and neither holds a secret: they only resolve
already-configured pointers.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path
from typing import Any

from core.agent_runtime.tools.base import Tool, tool_parameters
from core.harness.permissions import is_sensitive_path
from core.team_context.catalog import load_catalog

_MAX_READ_BYTES = 128 * 1024
_MAX_MATCHES = 200


def _bound_scope(catalog, workspace: str | None) -> str | None:
    if workspace is not None:
        return catalog.bind_for(workspace)
    return catalog.bind


def _layer_pointers(
    catalog,
    workspace: str | None,
) -> tuple[list[str], list[str]]:
    """Resolve the bound chain's document and code pointers, deduplicated."""
    scope = _bound_scope(catalog, workspace)
    if not scope:
        return [], []
    docs: list[str] = []
    codes: list[str] = []
    seen: set[str] = set()
    current = scope
    guard: set[str] = set()
    while current and current not in guard:
        guard.add(current)
        entity = catalog.entities.get(current)
        if entity is not None:
            for value in (*entity.designs, *entity.codebases):
                key = value.strip()
                if key and key not in seen:
                    seen.add(key)
                    if value in entity.designs:
                        docs.append(value)
                    else:
                        codes.append(value)
            current = entity.parent
        else:
            break
    return docs, codes


@tool_parameters(
    {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "read"],
                "description": "list: show configured document pointers; read: read a local document.",
            },
            "path": {
                "type": "string",
                "description": "The document path or URL to read when action is read.",
            },
        },
        "required": ["action"],
    }
)
class WikiTool(Tool):
    """Retrieve a document through the layer's ``designs`` pointers."""

    def __init__(self, workspace: str | None, *, home=None, protect: bool = True):
        self._workspace = workspace
        self._home = home
        self._protect = protect

    @property
    def name(self) -> str:
        return "wiki"

    @property
    def description(self) -> str:
        return (
            "Retrieve a design document through the team layer's document "
            "pointers (TeamEntity.designs). Lists the configured document "
            "pointers, or reads a local document (read-only, capped). For a "
            "remote URL it returns the pointer so the web tool can fetch it. "
            "Does not write, and does not expose secrets."
        )

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self, **kwargs: Any) -> str:
        action = str(kwargs.get("action") or "list").strip().lower()
        catalog = load_catalog(self._workspace, self._home)
        docs, _codes = _layer_pointers(catalog, self._workspace)
        if action == "list":
            if not docs:
                return "No document pointers configured on this layer."
            return "\n".join(f"- {d}" for d in docs)
        if action == "read":
            target = str(kwargs.get("path") or "").strip()
            if not target:
                return "Error: path is required for read."
            if not docs:
                return "Error: no document pointers configured on this layer."
            if target.startswith(("http://", "https://")):
                return (
                    f"Remote document: {target}\n"
                    "Use the web tool to fetch this URL; wiki only reads "
                    "local documents directly."
                )
            return await asyncio.to_thread(self._read_local, target, docs)
        return f"Error: unknown action {action!r}"

    def _read_local(self, target: str, docs: list[str]) -> str:
        # Resolve against the workspace when the target is relative; refuse to
        # leave it, mirroring the file tools' sandbox discipline.
        base = Path(self._workspace).resolve() if self._workspace else Path.cwd()
        candidate = (base / target).resolve() if not Path(target).is_absolute() else Path(target).resolve()
        if self._workspace and not str(candidate).startswith(str(base)):
            return "Error: path escapes the workspace."
        if self._protect and is_sensitive_path(str(candidate)):
            return "Error: protected path."
        if not candidate.is_file():
            return f"Error: not a file: {target}"
        raw = candidate.read_bytes()
        if b"\x00" in raw[:4096]:
            return "Error: binary file."
        text = raw[:_MAX_READ_BYTES].decode("utf-8", errors="replace")
        truncated = len(raw) > _MAX_READ_BYTES
        suffix = "\n…[truncated]" if truncated else ""
        return text + suffix


@tool_parameters(
    {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "definitions", "references"],
                "description": "list: show configured code pointers; definitions: find a symbol's definition; references: find a symbol's references.",
            },
            "symbol": {
                "type": "string",
                "description": "The symbol name to look up (definitions/references).",
            },
        },
        "required": ["action"],
    }
)
class CodeGraphTool(Tool):
    """Structural lookup through the layer's ``codebases`` pointers."""

    def __init__(self, workspace: str | None, *, home=None, protect: bool = True):
        self._workspace = workspace
        self._home = home
        self._protect = protect

    @property
    def name(self) -> str:
        return "codegraph"

    @property
    def description(self) -> str:
        return (
            "Look up code structure and call relationships through the team "
            "layer's code pointers (TeamEntity.codebases). For local code it "
            "finds symbol definitions and references by regex (composing the "
            "grep/glob capability); for a remote git URL it returns the "
            "pointer. Read-only; this is a retrieval seam over the configured "
            "code pointers, not a full index."
        )

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self, **kwargs: Any) -> str:
        action = str(kwargs.get("action") or "list").strip().lower()
        catalog = load_catalog(self._workspace, self._home)
        _docs, codes = _layer_pointers(catalog, self._workspace)
        if action == "list":
            if not codes:
                return "No code pointers configured on this layer."
            return "\n".join(f"- {c}" for c in codes)
        if action == "definitions":
            symbol = str(kwargs.get("symbol") or "").strip()
            if not symbol:
                return "Error: symbol is required for definitions."
            return await self._search(symbol, "definition", codes)
        if action == "references":
            symbol = str(kwargs.get("symbol") or "").strip()
            if not symbol:
                return "Error: symbol is required for references."
            return await self._search(symbol, "reference", codes)
        return f"Error: unknown action {action!r}"

    async def _search(self, symbol: str, mode: str, codes: list[str]) -> str:
        if not codes:
            return "Error: no code pointers configured on this layer."
        local_roots = [
            c
            for c in codes
            if not c.startswith(("http://", "https://", "git@", "ssh://"))
        ]
        remote = [c for c in codes if c not in local_roots]
        lines: list[str] = []
        if local_roots:
            for root in local_roots:
                base = Path(root).resolve()
                if not base.exists():
                    lines.append(f"(missing local code root: {root})")
                    continue
                matches = await asyncio.to_thread(self._ripgrep_symbol, symbol, base, mode)
                lines.extend(matches)
        if remote:
            lines.append("remote code pointers (clone or browse, then re-run):")
            lines.extend(f"  - {r}" for r in remote)
        if not lines:
            return "No matches."
        return "\n".join(lines[:_MAX_MATCHES])

    def _ripgrep_symbol(self, symbol: str, base: Path, mode: str) -> list[str]:
        # Definition-shaped pattern: `def name`, `class name`, `fn name`,
        # `const name =`, `function name`. Reference-shaped: a bare token.
        if mode == "definition":
            pattern = rf"(def|class|fn|function|const|let|var)\s+{symbol}\b"
        else:
            pattern = rf"\b{symbol}\b"
        argv = ["rg", "--line-number", "--no-heading", "--color", "never",
                "--with-filename", "--regexp", pattern, "--", str(base)]
        if not shutil.which("rg"):
            return []
        try:
            result = subprocess.run(
                argv,
                check=False,
                capture_output=True,
                timeout=30,
                text=True,
                errors="replace",
            )
        except (OSError, subprocess.TimeoutExpired):
            return []
        out: list[str] = []
        for line in result.stdout.splitlines():
            path, _sep, rest = line.partition(":")
            if self._protect and is_sensitive_path(path):
                continue
            out.append(f"{path}:{rest}")
        return out


__all__ = ["CodeGraphTool", "WikiTool"]
