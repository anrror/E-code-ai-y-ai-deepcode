"""Environment-variable semi-automatic capture.

An environment variable the transcript surfaces (e.g. ``DATABASE_URL=…`` or
``export FOO=bar``) is worth remembering as a *key* so a future Session knows
what to configure. Its *value* is captured only when (a) the caller explicitly
allows it and (b) the target layer does not already hold that key — matching
the user's rule: "if team context has not configured it, and it appeared in
the session, capture it". The value is always scoped to the 0600 environment
file at review time, never to a knowledge entry or the prompt.

Recognizes two shapes: ``KEY=value`` (inline or after ``export`` / ``set``) and
``env: KEY value``. Integration secrets (Jenkins tokens, kubeconfig) are
excluded by name.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from core.capture.models import CapturedSuggestion

_ASSIGNMENT_RE = re.compile(
    r"(?:^|[\s;])(?:export\s+|set\s+)?([A-Za-z_][A-Za-z0-9_]{1,64})="
    r"([^\s;\"'`]+|\"[^\"]*\"|'[^']*')"
)

# Keys whose value is an integration secret, never captured.
_EXCLUDED_KEYS = {
    "JENKINS_TOKEN",
    "KUBECONFIG",
    "AWS_SECRET_ACCESS_KEY",
    "K8S_TOKEN",
    "GITHUB_TOKEN",
    "GH_TOKEN",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
}


@dataclass(frozen=True, slots=True)
class EnvCandidate:
    key: str
    value: str | None

    def __post_init__(self) -> None:
        if not self.key.strip():
            raise ValueError("env key must not be empty")


def _clean_value(raw: str) -> str:
    value = raw.strip()
    if len(value) >= 2 and value[0] in "\"'" and value[-1] == value[0]:
        return value[1:-1]
    return value


def recognize_env_assignments(text: str) -> list[EnvCandidate]:
    """``KEY=value`` assignments found in ``text``, de-duplicated by key."""
    seen: set[str] = set()
    out: list[EnvCandidate] = []
    for match in _ASSIGNMENT_RE.finditer(text or ""):
        key = match.group(1)
        if key.upper() in _EXCLUDED_KEYS:
            continue
        if key in seen:
            continue
        seen.add(key)
        out.append(EnvCandidate(key, _clean_value(match.group(2))))
    return out


def env_suggestions(
    candidates: list[EnvCandidate],
    *,
    scope: str,
    source: str,
    allow_value: bool,
    existing_keys: frozenset[str] | set[str] = frozenset(),
) -> list[CapturedSuggestion]:
    """Turn candidates into ``environment`` suggestions.

    ``allow_value`` gates value capture; a key the layer already holds keeps
    its existing value and is proposed only if its *value* differs (then as a
    value-free key reminder, so the reviewer decides). Existing keys are never
    silently overwritten here — applying a suggestion is the review step.
    """
    existing = {str(k).strip() for k in existing_keys}
    out: list[CapturedSuggestion] = []
    for candidate in candidates:
        if candidate.key in existing:
            continue  # already configured; nothing to propose
        payload: dict[str, object] = {"key": candidate.key}
        if allow_value and candidate.value:
            payload["value"] = candidate.value
        out.append(
            CapturedSuggestion(
                kind="environment",
                scope=scope,
                source=source,
                payload=payload,
            )
        )
    return out


__all__ = [
    "EnvCandidate",
    "env_suggestions",
    "recognize_env_assignments",
]
