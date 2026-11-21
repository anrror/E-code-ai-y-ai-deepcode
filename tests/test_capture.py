from __future__ import annotations

import json

import pytest

from core.capture.backfill import (
    backfill_captures,
    capture_marker_path,
    mark_captured,
)
from core.capture.envs import EnvCandidate, env_suggestions, recognize_env_assignments
from core.capture.extract import (
    parse_extractions,
    suggestions_from_extractions,
)
from core.capture.models import (
    CapturedSuggestion,
    load_suggestions,
    save_suggestions,
)
from core.capture.pointers import (
    propose_pointers,
    recognize_git_urls,
    recognize_workspace,
)
from core.capture.sink import dedupe_suggestions

# ---------------------------------------------------------------------------
# models
# ---------------------------------------------------------------------------


def test_suggestion_roundtrip(tmp_path):
    s = CapturedSuggestion(
        kind="knowledge",
        scope="project://x",
        source="session",
        payload={"type": "lesson", "statement": "retry twice"},
    )
    assert s.to_mapping()["id"] == s.id
    assert CapturedSuggestion.from_mapping(s.to_mapping()) == s


def test_suggestion_rejects_secret_value_outside_environment(tmp_path):
    with pytest.raises(ValueError):
        CapturedSuggestion(
            kind="knowledge",
            scope="project://x",
            source="session",
            payload={"type": "lesson", "statement": "ok", "value": "secret"},
        )


def test_pending_roundtrip(tmp_path):
    s = CapturedSuggestion(
        kind="pointer_git",
        scope="project://x",
        source="session",
        payload={"value": "https://git/x.git"},
    )
    save_suggestions([s], home=tmp_path)
    loaded = load_suggestions(home=tmp_path)
    assert len(loaded) == 1
    assert loaded[0] == s


# ---------------------------------------------------------------------------
# pointers
# ---------------------------------------------------------------------------


def test_recognize_git_urls():
    text = "the repo lives at https://git.example.com/acme/crm.git and ssh://git@host/team/x.git"
    urls = recognize_git_urls(text)
    assert "https://git.example.com/acme/crm.git" in urls


def test_propose_pointers_skips_existing():
    text = "see https://git.example.com/acme/crm.git and docs/design.md"
    candidates = propose_pointers(
        text,
        existing_git=("https://git.example.com/acme/crm.git",),
        existing_docs=("docs/design.md",),
    )
    assert candidates == []


def test_propose_pointers_new_values():
    text = "see https://git.example.com/acme/new.git"
    candidates = propose_pointers(text, existing_git=())
    assert any(c.kind == "pointer_git" and "new.git" in c.value for c in candidates)


def test_recognize_workspace():
    text = "working in E:\\code\\acme\\crm"
    ws = recognize_workspace(text)
    assert ws and ws.endswith("crm")


# ---------------------------------------------------------------------------
# envs
# ---------------------------------------------------------------------------


def test_recognize_env_assignments():
    text = "export DATABASE_URL=postgres://x AND FOO=bar"
    found = recognize_env_assignments(text)
    keys = {c.key for c in found}
    assert "DATABASE_URL" in keys
    assert "FOO" in keys


def test_env_suggestions_respect_allow_value_and_existing():
    candidates = [EnvCandidate("FOO", "bar"), EnvCandidate("BAR", "baz")]
    out = env_suggestions(
        candidates,
        scope="project://x",
        source="session",
        allow_value=True,
        existing_keys=frozenset({"BAR"}),
    )
    assert len(out) == 1  # BAR already exists -> skipped
    assert out[0].payload["key"] == "FOO"
    assert out[0].payload["value"] == "bar"


def test_env_excludes_secret_keys():
    text = "JENKINS_TOKEN=abc123 KUBECONFIG=/path"
    found = recognize_env_assignments(text)
    assert found == []


# ---------------------------------------------------------------------------
# extract
# ---------------------------------------------------------------------------


def test_parse_extractions_drops_redline_and_secret():
    text = json.dumps(
        [
            {"type": "lesson", "statement": "retry twice", "rationale": "", "evidence": "", "confidence": "high"},
            {"type": "redline", "statement": "never commit keys", "rationale": "", "evidence": "", "confidence": "high"},
            {"type": "decision", "statement": "api_key=abcdefghijklmnopqrstuvwxyz123456", "rationale": "", "evidence": "", "confidence": "high"},
        ]
    )
    out = parse_extractions(text)
    assert len(out) == 1
    assert out[0]["type"] == "lesson"


def test_parse_extractions_handles_fence_and_prose():
    text = 'Here are the results:\n```json\n[{"type": "convention", "statement": "use UTC", "rationale": "", "evidence": "", "confidence": "medium"}]\n```'
    out = parse_extractions(text)
    assert len(out) == 1
    assert out[0]["statement"] == "use UTC"


def test_suggestions_from_extractions():
    out = suggestions_from_extractions(
        [{"type": "lesson", "statement": "x", "rationale": "", "evidence": "", "confidence": "high"}],
        scope="project://x",
        source="session",
    )
    assert len(out) == 1
    assert out[0].kind == "knowledge"


# ---------------------------------------------------------------------------
# sink (dedupe + redline block)
# ---------------------------------------------------------------------------


def _knowledge(statement, type="lesson"):
    return CapturedSuggestion(
        kind="knowledge",
        scope="project://x",
        source="session",
        payload={"type": type, "statement": statement},
    )


def test_dedupe_drops_duplicate_knowledge():
    a = _knowledge("use UTC")
    b = _knowledge("use UTC")
    out = dedupe_suggestions([a, b])
    assert len(out) == 1


def test_dedupe_blocks_redline_defensively():
    redline = _knowledge("never commit keys", type="redline")
    out = dedupe_suggestions([redline])
    assert out == []


def test_dedupe_against_existing_statements():
    a = _knowledge("use UTC")
    out = dedupe_suggestions([a], existing_statements=["use UTC"])
    assert out == []


def test_dedupe_pointer_by_value():
    p1 = CapturedSuggestion(kind="pointer_git", scope="project://x", source="s", payload={"value": "https://git/x.git"})
    p2 = CapturedSuggestion(kind="pointer_git", scope="project://x", source="s", payload={"value": "https://git/x.git"})
    out = dedupe_suggestions([p1, p2])
    assert len(out) == 1


# ---------------------------------------------------------------------------
# backfill
# ---------------------------------------------------------------------------


def _write_session(session_dir, content="repo is https://git.example.com/acme/crm.git"):
    session_dir.mkdir(parents=True, exist_ok=True)
    meta = {"_type": "metadata", "session_id": session_dir.name, "updated_at": "2026-11-21T00:00:00"}
    msg = {"role": "user", "content": content}
    (session_dir / "session.jsonl").write_text(
        json.dumps(meta) + "\n" + json.dumps(msg) + "\n", encoding="utf-8"
    )


def test_backfill_writes_marker(tmp_path):
    sessions_root = tmp_path / "sessions"
    sessions_root.mkdir()
    sdir = sessions_root / "sess1"
    _write_session(sdir)
    assert not capture_marker_path(sdir).exists()
    backfill_captures(str(sessions_root), home=str(tmp_path / "home"), workspace=None)
    assert capture_marker_path(sdir).exists()


def test_backfill_skips_marked(tmp_path):
    sessions_root = tmp_path / "sessions"
    sessions_root.mkdir()
    sdir = sessions_root / "sess1"
    _write_session(sdir)
    mark_captured(sdir)
    # Should not re-process; the marker is already there. Calling again is a
    # no-op (no error, no duplicate marker).
    backfill_captures(str(sessions_root), home=str(tmp_path / "home"), workspace=None)
    assert capture_marker_path(sdir).exists()


def test_backfill_marks_empty_session(tmp_path):
    sessions_root = tmp_path / "sessions"
    sessions_root.mkdir()
    sdir = sessions_root / "empty"
    _write_session(sdir, content="")
    backfill_captures(str(sessions_root), home=str(tmp_path / "home"), workspace=None)
    assert capture_marker_path(sdir).exists()  # nothing to capture, but marked
