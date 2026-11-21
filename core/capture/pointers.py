"""Deterministic layer-pointer recognition (zero model).

A Session transcript often *mentions* a git URL, a document path, or the
workspace a layer should point at. This module recognizes those strings
deterministically — no model, no hallucination — so they can be proposed as
``pointer_git`` / ``pointer_doc`` / ``pointer_workspace`` suggestions.

The recognizer only *proposes*: it never writes ``entity.yaml``. A value the
layer already declares is skipped, and a value that is only *discussed* (not
the project's own) is exactly why the result stays pending review rather than
landing immediately.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

# A git URL: https://… , git@host:… , ssh://… . Kept deliberately broad on the
# scheme and narrow on the shape so it does not fire on prose like "see git".
_GIT_URL_RE = re.compile(
    r"(?:(?:https?|ssh)://|git@)[^\s\"'<>`]+?\.git(?:\b|/|$)|"
    r"(?:(?:https?|ssh)://|git@)[^\s\"'<>`]+/(?:[\w.-]+/)*[\w.-]+(?:\.git)?",
    re.IGNORECASE,
)

# A document path: docs/…, *.md, *.adoc, or a wiki/URL with a doc-ish tail.
_DOC_RE = re.compile(
    r"(?:^|[\s\"'(])((?:docs?|design|spec|wiki|documents)/[\w./\\-]+"
    r"\.(?:md|adoc|rst|txt|yaml|yml))",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class PointerCandidate:
    kind: str  # pointer_git | pointer_doc | pointer_workspace
    value: str

    def __post_init__(self) -> None:
        if not self.value.strip():
            raise ValueError("pointer candidate value must not be empty")


def _dedupe(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        key = value.strip().rstrip("/").casefold()
        if key and key not in seen:
            seen.add(key)
            out.append(value.strip())
    return out


def recognize_git_urls(text: str) -> list[str]:
    """Git URLs found in ``text``, de-duplicated, order-preserving."""
    return _dedupe(match.group(0).strip(".,;:!?'\"") for match in _GIT_URL_RE.finditer(text))


def recognize_doc_paths(text: str) -> list[str]:
    """Document paths (``docs/…`` style) found in ``text``."""
    return _dedupe(match.group(1) for match in _DOC_RE.finditer(text))


def recognize_workspace(text: str) -> str | None:
    """An absolute local directory the transcript treats as the workspace.

    Only fires for a drive/root-qualified path with no obvious file suffix, so
    prose about ``/tmp`` does not auto-bind a workspace. Returns a single best
    guess or ``None``.
    """
    pattern = re.compile(
        r"(?:^|[\s\"'(])((?:[A-Za-z]:[\\/]|/)[^\s\"'<>`]+?[\\/][\w.-]+"
        r"(?:[\\/][\w.-]+){0,3})(?:$|[\s\"')])"
    )
    candidates: list[str] = []
    for match in pattern.finditer(text):
        value = match.group(1).strip()
        if "." in Path(value).name:
            continue  # looks like a file, not a directory
        candidates.append(value)
    return candidates[0] if candidates else None


def propose_pointers(
    text: str,
    *,
    existing_git: tuple[str, ...] = (),
    existing_docs: tuple[str, ...] = (),
    existing_workspace: str | None = None,
) -> list[PointerCandidate]:
    """Recognize pointers in ``text`` and drop what the layer already has.

    ``existing_*`` come from the bound ``TeamEntity``, so a pointer already
    configured (or an obvious duplicate) is not re-proposed.
    """
    existing_git_set = {g.strip().rstrip("/").casefold() for g in existing_git}
    existing_doc_set = {d.strip().casefold() for d in existing_docs}
    out: list[PointerCandidate] = []
    for value in recognize_git_urls(text):
        if value.rstrip("/").casefold() not in existing_git_set:
            out.append(PointerCandidate("pointer_git", value))
    for value in recognize_doc_paths(text):
        if value.casefold() not in existing_doc_set:
            out.append(PointerCandidate("pointer_doc", value))
    workspace = recognize_workspace(text)
    if workspace and (
        not existing_workspace
        or workspace.rstrip("/").casefold()
        != existing_workspace.rstrip("/").casefold()
    ):
        out.append(PointerCandidate("pointer_workspace", workspace))
    return out


__all__ = [
    "PointerCandidate",
    "propose_pointers",
    "recognize_doc_paths",
    "recognize_git_urls",
    "recognize_workspace",
]
