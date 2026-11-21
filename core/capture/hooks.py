"""Capture orchestrator — the entry point that wires the pipeline together.

The pipeline is fire-and-forget by contract: every entry point here never
raises, because capture runs on the session/compaction path and must never
break a turn or a session close. Failures are logged and swallowed.

Two entry points:

* :func:`capture_from_text` — the core: run deterministic recognition (layer
  pointers, environment keys) and, when a model runner is supplied, the model
  extraction pass over ``text``; dedupe; append to the pending queue.
* :func:`make_compaction_capture_sink` — a drop-in callable shaped like the
  existing compaction summary sink, so it can be chained after
  ``write_compaction_summary`` with no change to the summarizer.

Deterministic record registration (artifact/workflow/goal/runtime profile) is
deliberately *not* wired to the database here: those records are projected by
the caller (which already holds them) via :mod:`core.capture.registrar`, keeping
this module dependency-light and unit-testable.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from core.capture.envs import env_suggestions, recognize_env_assignments
from core.capture.extract import (
    build_extract_prompt,
    parse_extractions,
    suggestions_from_extractions,
)
from core.capture.models import (
    CapturedSuggestion,
    append_suggestion,
    load_suggestions,
)
from core.capture.pointers import propose_pointers
from core.capture.sink import dedupe_suggestions
from core.team_context.catalog import load_catalog

logger = logging.getLogger(__name__)

# A model runner has the autodream shape: prompt -> (stop_reason, final_text).
ModelRunner = Callable[[str], Awaitable[tuple[str, str]]]


async def _extract_via_agent_session(
    text: str, workspace: str | None, scope: str
) -> list[dict[str, Any]]:
    """Run the semantic extraction through a lightweight agent session.

    Mirrors ``core.loop.autodream.consolidate_memory``: one session, one prompt,
    one model round-trip, using the same extraction prompt. The transcript is
    folded into the prompt so the model sees the material it extracts from.
    """
    from core.agent_setup import build_agent_session
    from core.events import UserInput
    from core.harness.hooks.trust import ProjectHookApproval

    session, _model, _engine = build_agent_session(
        workspace=workspace or "",
        model=None,
        connection_id=None,
        reasoning_effort=None,
        max_iterations=1,
        project_hook_approval=ProjectHookApproval(None),
    )
    prompt = (
        f"{build_extract_prompt()}\n\n"
        f"The session summary to extract from is:\n\n{text}"
    )
    final_text = ""
    try:
        async for event in session.run_stream(UserInput(text=prompt)):
            if event.msg.type == "task_complete":
                final_text = event.msg.final_text or ""
    finally:
        await session.aclose()
    return parse_extractions(final_text)


def _bound_scope(workspace: str | None, home: str | None) -> str | None:
    try:
        catalog = load_catalog(workspace, home)
        return catalog.bind_for(workspace) if workspace else catalog.bind
    except Exception:
        logger.debug("capture: could not resolve bound scope", exc_info=True)
        return None


def _existing_pointer_fields(workspace: str | None, home: str | None, scope: str) -> dict[str, Any]:
    try:
        catalog = load_catalog(workspace, home)
        entity = catalog.entities.get(scope)
        if entity is None:
            return {}
        return {
            "git": entity.codebases,
            "docs": entity.designs,
            "workspace": entity.workspace,
        }
    except Exception:  # noqa: BLE001
        return {}


def _existing_env_keys(workspace: str | None, home: str | None, scope: str) -> frozenset[str]:
    try:
        from core.team_context.service import TeamContextService

        service = TeamContextService(workspace, home)
        # Environments are a project/product layer feature; read the layer the
        # suggestion is bound to. A layer without environments yields no keys.
        if scope not in {"product", "project"} and "://" not in scope:
            return frozenset()
        result = service.environments(scope)
        keys: set[str] = set()
        for env in result.get("environments", []):
            keys.update(env.get("keys", []))
        return frozenset(keys)
    except Exception:  # noqa: BLE001
        return frozenset()


def capture_from_text(
    text: str,
    *,
    workspace: str | None,
    home: str | None = None,
    scope: str | None = None,
    source: str = "session",
    allow_model: bool = True,
    allow_env_value: bool = True,
    model_runner: ModelRunner | None = None,
) -> list[CapturedSuggestion]:
    """Run one capture pass over ``text``; append accepted suggestions.

    Returns the suggestions that were actually appended (after dedupe). Never
    raises. ``scope`` defaults to the workspace's bound layer.
    """
    try:
        scope = scope or _bound_scope(workspace, home)
        if not scope:
            logger.debug("capture: no bound scope; skipping")
            return []
        text = str(text or "").strip()
        suggestions: list[CapturedSuggestion] = []

        # Deterministic: layer pointers (git/doc/workspace).
        fields = _existing_pointer_fields(workspace, home, scope)
        for candidate in propose_pointers(
            text,
            existing_git=tuple(fields.get("git", ())),
            existing_docs=tuple(fields.get("docs", ())),
            existing_workspace=fields.get("workspace"),
        ):
            suggestions.append(
                CapturedSuggestion(
                    kind=candidate.kind,
                    scope=scope,
                    source=source,
                    payload={"value": candidate.value},
                )
            )

        # Deterministic: environment keys (value only when the layer lacks it).
        existing_keys = _existing_env_keys(workspace, home, scope)
        env_candidates = recognize_env_assignments(text)
        suggestions.extend(
            env_suggestions(
                env_candidates,
                scope=scope,
                source=source,
                allow_value=allow_env_value,
                existing_keys=existing_keys,
            )
        )

        # Model extraction (optional): knowledge + references are the model's
        # semantic judgement. The prompt forbids red lines and secrets, and the
        # parser re-checks both. When no runner is injected, reuse the
        # autodream shape — a lightweight agent session over the workspace —
        # so the semantic pass works out of the box.
        if allow_model and text:
            try:
                import asyncio

                if model_runner is not None:
                    async def _run_injected() -> list[dict[str, Any]]:
                        _stop, final_text = await model_runner(build_extract_prompt())
                        return parse_extractions(final_text)

                    extractions = asyncio.run(_run_injected())
                else:
                    extractions = asyncio.run(
                        _extract_via_agent_session(
                            text, workspace, scope
                        )
                    )
                suggestions.extend(
                    suggestions_from_extractions(
                        extractions, scope=scope, source=source
                    )
                )
            except Exception:
                logger.debug("capture: model extraction failed", exc_info=True)

        if not suggestions:
            return []

        accepted = dedupe_suggestions(
            suggestions,
            existing=load_suggestions(home),
        )
        for suggestion in accepted:
            append_suggestion(suggestion, home)
        return accepted
    except Exception:
        logger.debug("capture pass failed", exc_info=True)
        return []


def make_compaction_capture_sink(
    workspace: str | None,
    *,
    home: str | None = None,
    allow_model: bool = True,
    model_runner: ModelRunner | None = None,
) -> Callable[[str, dict[str, Any] | None], None]:
    """Build a ``(summary, anchor) -> None`` sink, shaped like the compaction
    summary sink, so it can be chained after ``write_compaction_summary``.

    The sink runs capture on a daemon thread and never raises, matching the
    existing compaction-memory sink's non-blocking contract.
    """
    import threading

    def _deposit(summary: str, anchor: dict[str, Any] | None = None) -> None:
        if workspace is None:
            return
        source = f"compaction:{anchor.get('session_key', '')}" if anchor else "compaction"

        def _work() -> None:
            capture_from_text(
                summary,
                workspace=workspace,
                home=home,
                source=source,
                allow_model=allow_model,
                model_runner=model_runner,
            )

        try:
            thread = threading.Thread(target=_work, name="capture-compaction", daemon=True)
            thread.start()
        except Exception:  # noqa: BLE001, S110
            pass

    return _deposit


__all__ = [
    "capture_from_text",
    "make_compaction_capture_sink",
]
