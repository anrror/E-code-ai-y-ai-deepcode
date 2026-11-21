"""Startup backfill — capture Sessions that closed without a capture pass.

Session-end capture is wired into ``AgentSession.aclose``, which covers normal
and most abnormal teardowns. But a hard kill (power loss, OOM, a crash before
``aclose`` ran) can leave a Session's transcript on disk with no capture pass.
This module is the cheap, filesystem-only safety net: on startup it scans the
session store for Session directories that have a ``session.jsonl`` but no
``.capture.done`` marker, and runs the deterministic capture pass over their
message text.

The marker is written *after* a successful pass, so an interrupted pass is
simply re-run next startup (the pending queue de-duplicates, so a re-run is
harmless). No database migration is required: the marker lives next to the
session's own JSONL.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_MARKER = ".capture.done"


def capture_marker_path(session_dir: Path) -> Path:
    return session_dir / _MARKER


def mark_captured(session_dir: Path) -> None:
    """Write the capture marker for a session directory (best-effort)."""
    try:
        session_dir.mkdir(parents=True, exist_ok=True)
        capture_marker_path(session_dir).touch()
    except OSError:
        logger.debug("could not write capture marker for %s", session_dir)


def _session_text(session_jsonl: Path) -> str:
    """Concatenate the readable message contents of a ``session.jsonl``."""
    parts: list[str] = []
    try:
        import json

        with session_jsonl.open("r", encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    parsed = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if not isinstance(parsed, dict):
                    continue
                if parsed.get("_type") == "metadata":
                    continue
                content = parsed.get("content")
                if isinstance(content, str) and content.strip():
                    parts.append(content)
    except OSError:
        return ""
    return "\n".join(parts)


def backfill_captures(
    sessions_root: str | Path,
    *,
    home: str | None = None,
    workspace: str | None = None,
    allow_model: bool = False,
) -> int:
    """Scan ``sessions_root`` for uncaptured Sessions and run a capture pass.

    Returns the number of Sessions captured. Never raises: a malformed session
    or a failed pass is logged and skipped, so startup is never blocked.
    """
    from core.capture.hooks import capture_from_text

    root = Path(sessions_root).expanduser().resolve()
    if not root.is_dir():
        return 0
    captured = 0
    for entry in root.iterdir():
        if not entry.is_dir():
            continue
        jsonl = entry / "session.jsonl"
        if not jsonl.is_file():
            continue
        if capture_marker_path(entry).exists():
            continue
        text = _session_text(jsonl)
        if not text.strip():
            mark_captured(entry)  # nothing worth capturing; mark so we stop retrying
            continue
        try:
            result = capture_from_text(
                text,
                workspace=workspace,
                home=home,
                source=f"backfill:{entry.name}",
                allow_model=allow_model,
                allow_env_value=True,
            )
            mark_captured(entry)
            if result:
                captured += 1
        except Exception:
            logger.debug("backfill capture failed for %s", entry.name, exc_info=True)
    return captured


__all__ = [
    "backfill_captures",
    "capture_marker_path",
    "mark_captured",
]
