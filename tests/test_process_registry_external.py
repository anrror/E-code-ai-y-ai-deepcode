"""Regression tests for the non-blocking external process scan.

Covers 4ccc1c65: listing processes started outside this session used to walk
every process synchronously inside the RPC handler, so a slow scan delayed the
Inspector panel. The fix returns the cached snapshot immediately and refreshes
on a daemon thread.

These tests assert the *contract* rather than timing a real scan: a caller must
never block, and a stale snapshot must be returned while a refresh is running.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from core import process_registry as mod


class _FakeInfo(dict):
    """Stand-in for a psutil Process.info mapping."""


class _FakeProcess:
    def __init__(self, info: _FakeInfo) -> None:
        self.info = info


class _FakePsutil:
    """A psutil replacement whose scan can be held open by the test."""

    def __init__(self, delay: float = 0.0) -> None:
        self.delay = delay
        self.calls = 0
        self.release = threading.Event()

    def process_iter(self, attrs):  # type: ignore[no-untyped-def]
        self.calls += 1
        if self.delay:
            time.sleep(self.delay)
        # One process inside the workspace, matching the dev-server allowlist.
        yield _FakeProcess(
            _FakeInfo(
                pid=4242,
                name="node.exe",
                cmdline=["node", "server.js"],
                create_time=time.time(),
                cwd=self.cwd,
                exe="C:/node/node.exe",
            )
        )

    cwd = ""


@pytest.fixture(autouse=True)
def reset_caches() -> None:
    mod._external_cache.clear()
    mod._external_scanning.clear()
    mod._log_cache.clear()


def test_absent_psutil_returns_an_empty_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mod, "_psutil", lambda: None)

    assert mod.external_processes("ws", set()) == []


def test_first_call_returns_immediately_without_waiting_for_the_scan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The scan runs on a daemon thread, so a cold cache still answers at once."""

    fake = _FakePsutil(delay=0.3)
    monkeypatch.setattr(mod, "_psutil", lambda: fake)
    monkeypatch.setattr(mod, "_scan_external", lambda *a, **k: None)

    started = time.perf_counter()
    result = mod.external_processes("ws", set())
    elapsed = time.perf_counter() - started

    assert result == [], "a cold cache has nothing to return yet"
    assert elapsed < 0.1, f"caller blocked for {elapsed:.2f}s — the scan must be async"
    assert "ws" in mod._external_scanning, "a background scan should be registered"


def test_only_one_scan_runs_per_workspace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Repeated RPCs must not spawn overlapping scans."""

    fake = _FakePsutil()
    monkeypatch.setattr(mod, "_psutil", lambda: fake)
    spawned: list[str] = []
    monkeypatch.setattr(
        mod, "_scan_external", lambda ws, known, psutil: spawned.append(ws)
    )

    for _ in range(5):
        mod.external_processes("ws", set())

    # _scan_external is replaced, so _external_scanning is never cleared; the
    # guard must therefore allow exactly one spawn.
    assert spawned == ["ws"], f"expected one scan, spawned {spawned}"


def test_cached_snapshot_is_returned_while_the_cache_is_fresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakePsutil()
    monkeypatch.setattr(mod, "_psutil", lambda: fake)
    monkeypatch.setattr(mod, "_scan_external", lambda *a, **k: None)

    snapshot = [{"pid": 1, "name": "server"}]
    mod._external_cache["ws"] = (time.monotonic(), snapshot)

    assert mod.external_processes("ws", set()) == snapshot


def test_cached_rows_are_copies_not_shared_references(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A caller mutating a row must not corrupt the cache for the next caller."""

    monkeypatch.setattr(mod, "_psutil", lambda: _FakePsutil())
    monkeypatch.setattr(mod, "_scan_external", lambda *a, **k: None)
    mod._external_cache["ws"] = (time.monotonic(), [{"pid": 1, "name": "server"}])

    first = mod.external_processes("ws", set())
    first[0]["name"] = "mutated"
    second = mod.external_processes("ws", set())

    assert second[0]["name"] == "server"


def test_scan_is_triggered_again_once_the_ttl_expires(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mod, "_psutil", lambda: _FakePsutil())
    spawned: list[str] = []
    monkeypatch.setattr(
        mod, "_scan_external", lambda ws, known, psutil: spawned.append(ws)
    )

    # A snapshot older than the TTL must not suppress a refresh.
    mod._external_cache["ws"] = (time.monotonic() - mod._EXTERNAL_TTL - 1, [])
    mod.external_processes("ws", set())

    assert spawned == ["ws"], "a stale snapshot must trigger a new scan"


def test_known_pids_are_excluded_by_the_scan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A process this session started must not be reported as external."""

    fake = _FakePsutil()
    monkeypatch.setattr(mod, "_psutil", lambda: fake)
    monkeypatch.setattr(mod, "_psutil", lambda: fake)
    # Run the real scan synchronously so the filter is exercised.
    monkeypatch.setattr(mod.threading, "Thread", _ImmediateThread)
    monkeypatch.setattr(mod.os, "getpid", lambda: 999999)

    rows = mod.external_processes("ws", {4242})

    assert rows == [], "a known pid must be filtered out of the external list"


class _ImmediateThread:
    """A Thread replacement that runs its target inline (test-only)."""

    def __init__(self, target=None, args=(), kwargs=None, **extra) -> None:  # type: ignore[no-untyped-def]
        self._target = target
        self._args = args
        self._kwargs = kwargs or {}

    def start(self) -> None:
        if self._target:
            self._target(*self._args, **self._kwargs)

    def join(self, timeout=None) -> None:  # type: ignore[no-untyped-def]
        return None
