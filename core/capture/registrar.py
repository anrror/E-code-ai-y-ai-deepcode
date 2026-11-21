"""Deterministic registration of durable Session records (zero model).

The Session already persists structured records — artifacts, workflow runs,
goals, and the execution profile — in SQLite. Re-deriving them from prose
would be lossy. This module *registers references* to those records instead:
given a record (or its projection), it produces a ``CapturedSuggestion`` whose
payload is the structured projection, so a reviewer can promote it into a
layer pointer or a knowledge entry without a second guess.

Nothing here calls a model. Every function is a pure projection over plain
values, so the whole module is unit-testable without a database.
"""

from __future__ import annotations

from typing import Any

from core.capture.models import CapturedSuggestion


def artifact_suggestion(
    scope: str,
    source: str,
    *,
    kind: str,
    name: str,
    media_type: str,
    storage_path: str,
    byte_size: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> CapturedSuggestion:
    """Register an :class:`core.domain.artifact.Artifact` reference."""
    payload: dict[str, Any] = {
        "kind": kind,
        "name": name,
        "mediaType": media_type,
        "storagePath": storage_path,
    }
    if byte_size is not None:
        payload["byteSize"] = byte_size
    if metadata:
        payload["metadata"] = metadata
    return CapturedSuggestion(
        kind="artifact",
        scope=scope,
        source=source,
        payload=payload,
    )


def workflow_suggestion(
    scope: str,
    source: str,
    *,
    kind: str,
    status: str,
    result: dict[str, Any] | None = None,
    checkpoint: dict[str, Any] | None = None,
    error_code: str | None = None,
) -> CapturedSuggestion:
    """Register a terminal :class:`core.domain.workflow.WorkflowRun` reference."""
    payload: dict[str, Any] = {"kind": kind, "status": status}
    if result:
        payload["result"] = result
    if checkpoint:
        payload["checkpoint"] = checkpoint
    if error_code:
        payload["errorCode"] = error_code
    return CapturedSuggestion(
        kind="workflow",
        scope=scope,
        source=source,
        payload=payload,
    )


def goal_suggestion(
    scope: str,
    source: str,
    *,
    objective: str,
    status: str,
    reason: str = "",
    evidence: list[dict[str, Any]] | None = None,
) -> CapturedSuggestion:
    """Register a :class:`core.domain.thread_goal.ThreadGoal` outcome."""
    payload: dict[str, Any] = {"objective": objective, "status": status}
    if reason:
        payload["reason"] = reason
    if evidence:
        payload["evidence"] = evidence
    return CapturedSuggestion(
        kind="goal",
        scope=scope,
        source=source,
        payload=payload,
    )


def runtime_profile_suggestion(
    scope: str,
    source: str,
    *,
    connection_id: str,
    provider_name: str,
    model_id: str,
    permission_mode: str | None = None,
    reasoning_effort: str | None = None,
    context_window: int | None = None,
) -> CapturedSuggestion:
    """Register the Session's execution posture for future review.

    Deliberately *not* injected as knowledge: it is an operational fingerprint
    (which model/connection/permission this task ran under), useful for
    reviewing "how was this last run" without polluting team facts.
    """
    payload: dict[str, Any] = {
        "connectionId": connection_id,
        "providerName": provider_name,
        "modelId": model_id,
    }
    if permission_mode:
        payload["permissionMode"] = permission_mode
    if reasoning_effort:
        payload["reasoningEffort"] = reasoning_effort
    if context_window is not None:
        payload["contextWindow"] = context_window
    return CapturedSuggestion(
        kind="runtime_profile",
        scope=scope,
        source=source,
        payload=payload,
    )


__all__ = [
    "artifact_suggestion",
    "goal_suggestion",
    "runtime_profile_suggestion",
    "workflow_suggestion",
]
