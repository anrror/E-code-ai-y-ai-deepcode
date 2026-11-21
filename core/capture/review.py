"""Pending-review operations — list, accept, reject captured suggestions.

Accepting a suggestion *applies* it to the team-context layer:

* ``pointer_git`` / ``pointer_doc`` / ``pointer_workspace`` → merge into the
  bound entity's ``codebases`` / ``designs`` / ``workspace`` via
  ``TeamContextService.update_entity``.
* ``knowledge`` / ``reference`` → a ``KnowledgeEntry`` draft via
  ``TeamContextService.add``.
* ``environment`` → write into the layer's ``environments/<active>.yaml`` via
  ``TeamContextService.save_environment`` (value optional; the 0600 file is
  the only place a value ever lands).

Rejecting simply marks the suggestion rejected and leaves it out of future
acceptance. Applying is always explicit — this module is the reviewed step the
automatic pipeline deliberately does not perform.
"""

from __future__ import annotations

from typing import Any

from core.capture.models import (
    CapturedSuggestion,
    load_suggestions,
    save_suggestions,
)
from core.team_context.service import TeamContextService


def list_pending(home: str | None = None) -> list[dict[str, Any]]:
    return [s.to_mapping() for s in load_suggestions(home)]


def _mark(suggestion_id: str, status: str, home: str | None) -> CapturedSuggestion | None:
    current = load_suggestions(home)
    target = next((s for s in current if s.id == suggestion_id), None)
    if target is None:
        return None
    replaced = [
        CapturedSuggestion(
            kind=s.kind,
            scope=s.scope,
            source=s.source,
            payload=s.payload,
            status=status,
            id=s.id,
            created_at=s.created_at,
        )
        if s.id == suggestion_id
        else s
        for s in current
    ]
    save_suggestions(replaced, home)
    return next((s for s in replaced if s.id == suggestion_id), None)


def accept(
    suggestion_id: str,
    *,
    workspace: str | None,
    home: str | None = None,
    owner: str = "",
) -> dict[str, Any]:
    """Apply one pending suggestion to the team-context layer."""
    suggestion = next(
        (s for s in load_suggestions(home) if s.id == suggestion_id), None
    )
    if suggestion is None:
        return {"error": f"unknown suggestion {suggestion_id}"}
    if suggestion.status != "pending":
        return {"error": f"suggestion {suggestion_id} is already {suggestion.status}"}

    service = TeamContextService(workspace, home)
    try:
        if suggestion.kind == "pointer_git":
            service.update_entity(
                suggestion.scope,
                codebases=_merge(
                    _current(service, suggestion.scope, "codebases"),
                    str(suggestion.payload.get("value") or ""),
                ),
            )
        elif suggestion.kind == "pointer_doc":
            service.update_entity(
                suggestion.scope,
                designs=_merge(
                    _current(service, suggestion.scope, "designs"),
                    str(suggestion.payload.get("value") or ""),
                ),
            )
        elif suggestion.kind == "pointer_workspace":
            service.update_entity(
                suggestion.scope,
                workspace=str(suggestion.payload.get("value") or ""),
            )
        elif suggestion.kind in ("knowledge", "reference"):
            service.add(
                scope=suggestion.scope,
                type=str(suggestion.payload.get("type") or "lesson"),
                statement=str(suggestion.payload.get("statement") or ""),
                rationale=str(suggestion.payload.get("rationale") or ""),
                evidence=str(suggestion.payload.get("evidence") or ""),
                confidence=str(suggestion.payload.get("confidence") or "medium"),
                owner=owner,
            )
        elif suggestion.kind == "environment":
            key = str(suggestion.payload.get("key") or "")
            value = suggestion.payload.get("value")
            if not key:
                return {"error": "environment suggestion has no key"}
            active = service.environments(suggestion.scope).get("active") or "default"
            current = service.environment_values(suggestion.scope, active).get("values", {})
            merged = dict(current)
            merged[key] = str(value) if value is not None else merged.get(key, "")
            service.save_environment(suggestion.scope, active, merged)
        else:
            # artifact/workflow/goal/runtime_profile are references; they are
            # informational and have no direct team-context mutation. Accept
            # means "acknowledge" only.
            pass
    except Exception as exc:  # noqa: BLE001 - report, do not crash review
        return {"error": f"apply failed: {exc}"}

    _mark(suggestion_id, "accepted", home)
    return {"accepted": suggestion_id, "kind": suggestion.kind}


def reject(suggestion_id: str, *, home: str | None = None) -> dict[str, Any]:
    suggestion = _mark(suggestion_id, "rejected", home)
    if suggestion is None:
        return {"error": f"unknown suggestion {suggestion_id}"}
    return {"rejected": suggestion_id}


def _current(service: TeamContextService, scope: str, field: str) -> list[str]:
    try:
        view = service.read_entity(scope)["entity"]
        return [str(v) for v in (view.get(field) or [])]
    except Exception:  # noqa: BLE001
        return []


def _merge(existing: list[str], value: str) -> list[str]:
    if value and value not in existing:
        return [*existing, value]
    return list(existing)


__all__ = ["accept", "list_pending", "reject"]
