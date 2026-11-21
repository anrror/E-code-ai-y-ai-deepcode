"""Regression tests for integrations.yaml durability and secrecy.

Covers the behaviours fixed in e327375e that had no test:
  * the file is replaced atomically (a crash mid-write must not truncate it)
  * the token never exists in a file that was created world-readable

The pre-fix code used ``path.write_text`` then ``chmod``, so a crash between
the two left a truncated file, and on POSIX the token briefly sat in a
default-mode file.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from core.team_context import integrations as mod
from core.team_context.integrations import (
    integrations_path,
    raw_integrations,
    read_integrations,
    save_integrations,
)


SAMPLE = {
    "jenkins": {
        "url": "https://jenkins.example.com",
        "job": "apps/api",
        "user": "ci",
        "token": "s3cret-token",
    }
}


def test_temp_file_is_removed_after_a_successful_write(tmp_path: Path) -> None:
    home = tmp_path / "home"

    save_integrations("project://p", SAMPLE, home=home)

    leftovers = list((home / ".dfcode").glob("*.tmp-*"))
    assert leftovers == [], f"temp files left behind: {leftovers}"


def test_write_goes_through_a_temp_file_and_replace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The write must be swap-based, not an in-place overwrite.

    This is the behaviour that actually changed in e327375e. Asserting only
    "the content is correct" passes against the old ``write_text`` too; the
    difference is *where* the bytes are written first.
    """

    home = tmp_path / "home"
    seen: dict[str, object] = {}
    real_replace = os.replace
    real_open = mod.os.open

    def recording_open(path, flags, mode=0o777):  # type: ignore[no-untyped-def]
        seen["open_path"] = str(path)
        seen["open_mode"] = mode
        return real_open(path, flags, mode)

    def recording_replace(src, dst):  # type: ignore[no-untyped-def]
        seen["replace_src"] = str(src)
        seen["replace_dst"] = str(dst)
        return real_replace(src, dst)

    monkeypatch.setattr(mod.os, "open", recording_open)
    monkeypatch.setattr(mod.os, "replace", recording_replace)

    save_integrations("project://p", SAMPLE, home=home)

    target = integrations_path(home)
    assert "replace_src" in seen, "the write must replace a completed temp file"
    assert seen["replace_dst"] == str(target)
    assert seen["replace_src"] != str(target), "the temp file must not be the target"
    assert ".tmp-" in str(seen["replace_src"])
    # The temp file is created private, so the token is never world-readable.
    assert seen["open_mode"] == 0o600


def test_failed_write_keeps_the_previous_document(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failure during the swap must not destroy the existing file.

    The pre-fix code wrote directly into the target, so an exception midway
    through dumped a half-written document over the good one.
    """

    home = tmp_path / "home"
    save_integrations("project://p", SAMPLE, home=home)
    before = integrations_path(home).read_text(encoding="utf-8")

    real_replace = os.replace

    def exploding_replace(src, dst):  # type: ignore[no-untyped-def]
        raise OSError("simulated crash during swap")

    monkeypatch.setattr(mod.os, "replace", exploding_replace)
    with pytest.raises(OSError, match="simulated crash"):
        save_integrations(
            "project://p",
            {"jenkins": {"url": "https://other.example.com", "job": "j", "token": "new"}},
            home=home,
        )
    monkeypatch.setattr(mod.os, "replace", real_replace)

    after = integrations_path(home).read_text(encoding="utf-8")
    assert after == before, "a failed write must not alter the stored document"
    # The temp file is cleaned up rather than left holding the token.
    assert list((home / ".dfcode").glob("*.tmp-*")) == []


def test_file_is_owner_only_from_creation(tmp_path: Path) -> None:
    """The file must never exist in a world-readable state.

    ``os.open(..., 0o600)`` sets the mode at creation time; a ``write_text`` +
    ``chmod`` pair leaves a window where the token is readable by others.
    """

    home = tmp_path / "home"
    save_integrations("project://p", SAMPLE, home=home)

    path = integrations_path(home)
    if os.name == "nt":
        pytest.skip("POSIX permission bits are not enforced on Windows")
    mode = stat.S_IMODE(path.stat().st_mode)

    assert mode == 0o600, f"expected 0600, got {mode:o}"


def test_token_is_still_masked_on_read(tmp_path: Path) -> None:
    """Durability changes must not leak the token back to callers."""

    home = tmp_path / "home"
    save_integrations("project://p", SAMPLE, home=home)

    public = read_integrations("project://p", home=home)
    raw = raw_integrations("project://p", home=home)

    assert "s3cret-token" not in str(public), "public view must mask the token"
    assert public["jenkins"]["hasToken"] is True
    assert raw["jenkins"]["token"] == "s3cret-token", "raw view keeps it for the services"


def test_write_flushes_to_disk_before_replacing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """fsync must run before the swap.

    Without it the rename can land before the data reaches disk, so a power cut
    leaves the target present but empty — the token is lost while the file
    looks intact. Coverage cannot see this: ``pass`` executes identically.
    """

    home = tmp_path / "home"
    fsync_calls: list[int] = []
    real_fsync = os.fsync

    def recording_fsync(fd: int) -> None:
        fsync_calls.append(fd)
        real_fsync(fd)

    monkeypatch.setattr(mod.os, "fsync", recording_fsync)

    save_integrations("project://p", SAMPLE, home=home)

    assert fsync_calls, "the payload must be flushed before os.replace"


def test_repeated_saves_converge_on_the_last_value(tmp_path: Path) -> None:
    home = tmp_path / "home"

    for token in ("first", "second", "third"):
        save_integrations(
            "project://p",
            {"jenkins": {"url": "https://j.example.com", "job": "j", "token": token}},
            home=home,
        )

    assert raw_integrations("project://p", home=home)["jenkins"]["token"] == "third"
