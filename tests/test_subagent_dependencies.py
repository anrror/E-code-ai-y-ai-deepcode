"""Dependency ordering for delegated sub-agents (``depends_on``).

Design constraints these tests pin down:

* a spawn whose dependencies are unmet is *queued*, not started;
* the existing "too many running" refusal still applies to a spawn that is
  ready immediately (queuing must not silently swallow the limit);
* a failed dependency blocks its dependents and reports that to the parent
  instead of leaving them invisible;
* cancellation reaches queued work (they have no task handle to cancel), so a
  Turn teardown cannot leave a ghost subtask behind.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.harness.agents.control import AgentControl, AgentLimitError


async def _spin(times: int = 8) -> None:
    """Let scheduled tasks make progress without relying on wall-clock sleeps."""
    for _ in range(times):
        await asyncio.sleep(0)


class _GateControl(AgentControl):
    """Each sub-agent blocks until its own gate is released."""

    def __init__(self, workspace, **kwargs) -> None:
        super().__init__(workspace, **kwargs)
        self.gates: dict[str, asyncio.Event] = {}
        self.started: list[str] = []

    async def _run_subagent(self, sub, workspace):
        self.started.append(sub.id)
        gate = self.gates.setdefault(sub.id, asyncio.Event())
        await gate.wait()
        return f"done {sub.id}"

    def release(self, agent_id: str) -> None:
        self.gates.setdefault(agent_id, asyncio.Event()).set()


class _FailControl(AgentControl):
    """The first turn of every sub-agent fails."""

    async def _run_subagent(self, sub, workspace):
        raise RuntimeError("boom")


def test_dependent_is_queued_until_its_dependency_finishes(tmp_path):
    async def scenario():
        ctrl = _GateControl(str(tmp_path))
        first = ctrl.spawn("first", name="first", isolate=False)
        second = ctrl.spawn(
            "second", name="second", isolate=False, depends_on=[first]
        )
        await _spin()

        # Only the first one runs; the second waits without a task handle.
        assert ctrl.started == [first]
        queued = ctrl.get(second)
        assert queued is not None
        assert queued.status == "queued"
        assert queued.handle is None
        assert second in ctrl.busy_ids()

        ctrl.release(first)
        await _spin(12)

        assert ctrl.started == [first, second]
        assert ctrl.get(second).status in {"running", "idle"}
        await ctrl.cancel_running()

    asyncio.run(scenario())


def test_unknown_dependency_is_refused_at_spawn(tmp_path):
    async def scenario():
        ctrl = _GateControl(str(tmp_path))
        with pytest.raises(AgentLimitError) as excinfo:
            ctrl.spawn("x", name="x", isolate=False, depends_on=["ghost"])
        return str(excinfo.value)

    message = asyncio.run(scenario())

    assert "ghost" in message
    assert "unknown" in message.lower()


def test_failed_dependency_blocks_and_reports_the_dependent(tmp_path):
    async def scenario():
        ctrl = _FailControl(str(tmp_path))
        first = ctrl.spawn("first", name="first", isolate=False)
        second = ctrl.spawn(
            "second", name="second", isolate=False, depends_on=[first]
        )
        await _spin(12)
        return ctrl, first, second

    ctrl, first, second = asyncio.run(scenario())

    assert ctrl.get(first).status == "failed"
    blocked = ctrl.get(second)
    assert blocked.status == "blocked"
    assert "first" in blocked.result
    # The parent must learn about it, or the failure is silent.
    assert any("blocked" in payload.lower() for _, _, payload in ctrl._mailbox)
    assert ctrl.active_count() == 0


def test_wait_for_activity_does_not_claim_nothing_is_running_when_blocked(tmp_path):
    async def scenario():
        ctrl = _FailControl(str(tmp_path))
        first = ctrl.spawn("first", name="first", isolate=False)
        ctrl.spawn("second", name="second", isolate=False, depends_on=[first])
        await _spin(12)
        ctrl._mailbox.clear()  # isolate the activity answer from the result text
        return await ctrl.wait_for_activity(0.01)

    outcome = asyncio.run(scenario())

    assert "no sub-agents are running" not in outcome.lower()
    assert "blocked" in outcome.lower()


def test_cancellation_reaches_queued_work(tmp_path):
    async def scenario():
        ctrl = _GateControl(str(tmp_path))
        first = ctrl.spawn("first", name="first", isolate=False)
        second = ctrl.spawn(
            "second", name="second", isolate=False, depends_on=[first]
        )
        await _spin()
        await ctrl.cancel_running()
        return ctrl, first, second

    ctrl, first, second = asyncio.run(scenario())

    assert ctrl.get(first).status == "failed"
    assert ctrl.get(second).status == "cancelled"
    assert ctrl.active_count() == 0
    assert ctrl.get(second).settled.is_set()


def test_ready_spawn_still_respects_the_concurrency_limit(tmp_path):
    async def scenario():
        ctrl = _GateControl(str(tmp_path), max_threads=1)
        ctrl.spawn("first", name="first", isolate=False)
        await _spin()
        with pytest.raises(AgentLimitError):
            ctrl.spawn("second", name="second", isolate=False)
        await ctrl.cancel_running()

    asyncio.run(scenario())

def test_unclean_merge_blocks_dependents(monkeypatch, tmp_path):
    from core.team import worktree as worktree_mod

    class _FakeWorktree:
        def __init__(self, base, worktrees_root=None):
            self.base = Path(base)
            self.worktrees_root = self.base / ".deepcode-wt"

        def ensure_base(self):
            pass

        def create(self, worker_id):
            path = self.base / f".deepcode-wt-{worker_id}"
            path.mkdir(parents=True, exist_ok=True)
            return str(path)

        def merge(self, worker_id):
            return worktree_mod.MergeResult(
                worker_id=worker_id, clean=False, conflicts=["feature.py"]
            )

        def cleanup(self, worker_id):
            pass

    monkeypatch.setattr(worktree_mod, "WorktreeManager", _FakeWorktree)

    async def scenario():
        ctrl = _GateControl(str(tmp_path))
        first = ctrl.spawn("first", name="first", isolate=True)
        second = ctrl.spawn(
            "second", name="second", isolate=False, depends_on=[first]
        )
        await _spin()
        ctrl.release(first)
        await _spin(20)
        # Read inside the loop: a native child parks idle forever, so letting
        # asyncio.run() shut down would cancel it and rewrite the status.
        return (
            ctrl.get(first).status,
            ctrl.get(first).merge_ok,
            ctrl.get(second).status,
            ctrl.get(second).result,
            tuple(ctrl.started),
        )

    first_status, merge_ok, second_status, second_result, started = asyncio.run(
        scenario()
    )

    assert first_status == "idle", second_result
    assert merge_ok is False
    assert second_status == "blocked"
    assert "merge" in second_result.lower()
    assert started == ("first",)

class _FlakyControl(AgentControl):
    """Fails the first time, succeeds after ``fail`` is cleared."""

    def __init__(self, workspace, **kwargs) -> None:
        super().__init__(workspace, **kwargs)
        self.fail = True

    async def _run_subagent(self, sub, workspace):
        if self.fail:
            raise RuntimeError("boom")
        return f"done {sub.id}"


def test_retrying_a_failed_dependency_revives_blocked_dependents(tmp_path):
    async def scenario():
        ctrl = _FlakyControl(str(tmp_path))
        first = ctrl.spawn("first", name="first", isolate=False)
        second = ctrl.spawn(
            "second", name="second", isolate=False, depends_on=[first]
        )
        await _spin(12)
        assert ctrl.get(second).status == "blocked"

        ctrl.fail = False
        retried = ctrl.spawn("first, take two", name="first", isolate=False)
        await _spin(12)
        # Read before loop shutdown: a native child parks idle and would be
        # rewritten to "cancelled" when asyncio.run() tears pending tasks down.
        return (
            retried,
            ctrl.get(second).deps,
            ctrl.get(retried).status,
            ctrl.get(second).status,
        )

    retried, dependent_deps, retried_status, dependent_status = asyncio.run(scenario())

    assert retried != "first", "the failed attempt stays visible as evidence"
    assert dependent_deps == (retried,), "the dependent follows the retry"
    assert retried_status in {"running", "idle"}
    assert dependent_status in {"queued", "running", "idle"}


def test_send_message_is_refused_while_dependents_wait(tmp_path):
    async def scenario():
        ctrl = _GateControl(str(tmp_path))
        first = ctrl.spawn("first", name="first", isolate=False)
        held = ctrl.spawn("held", name="held", isolate=False)
        ctrl.spawn(
            "second",
            name="second",
            isolate=False,
            depends_on=[first, held],
        )
        await _spin()
        ctrl.release(first)
        await _spin(12)

        blocked = ctrl.get("second")
        assert blocked.status == "queued"
        assert ctrl.get(first).status == "idle"

        reply = ctrl.send_message(first, "start something else")
        return reply, ctrl.get(first).status, blocked.status

    reply, first_status, dependent_status = asyncio.run(scenario())

    assert reply.startswith("Error:")
    assert "depend" in reply.lower()
    assert first_status == "idle"
    assert dependent_status == "queued"


def test_queued_work_is_bounded(tmp_path):
    async def scenario():
        ctrl = _GateControl(str(tmp_path), max_threads=1)
        root = ctrl.spawn("root", name="root", isolate=False)
        await _spin()
        ctrl.spawn("q1", name="q1", isolate=False, depends_on=[root])
        ctrl.spawn("q2", name="q2", isolate=False, depends_on=[root])
        with pytest.raises(AgentLimitError) as excinfo:
            ctrl.spawn("q3", name="q3", isolate=False, depends_on=[root])
        await ctrl.cancel_running()
        return str(excinfo.value)

    message = asyncio.run(scenario())

    assert "wait" in message.lower()

def test_a_raising_mailbox_does_not_kill_the_child(tmp_path):
    def sink(_message):
        raise RuntimeError("mailbox down")

    async def scenario():
        ctrl = _FailControl(
            str(tmp_path),
            runtime_input_sink=sink,
            active_turn_id_provider=lambda: "turn-1",
        )
        first = ctrl.spawn("first", name="first", isolate=False)
        await _spin(12)
        return ctrl.get(first).status, ctrl.get(first).result

    status, result = asyncio.run(scenario())

    assert status == "failed"
    assert "boom" in result
    assert "mailbox down" not in result


def test_cancel_skips_the_turn_mailbox(tmp_path):
    calls = []

    def sink(message):
        calls.append(message)
        return True

    async def scenario():
        ctrl = _GateControl(
            str(tmp_path),
            runtime_input_sink=sink,
            active_turn_id_provider=lambda: "turn-1",
        )
        first = ctrl.spawn("first", name="first", isolate=False)
        ctrl.spawn("second", name="second", isolate=False, depends_on=[first])
        await _spin()
        await ctrl.cancel_running()

    asyncio.run(scenario())

    # Teardown has no Turn to inject into; the canonical note sink is the path.
    assert calls == []

class _FlakyOnceControl(AgentControl):
    """The first ``flaky`` task attempt fails; later attempts succeed."""

    def __init__(self, workspace, **kwargs) -> None:
        super().__init__(workspace, **kwargs)
        self.failed_once = False

    async def _run_subagent(self, sub, workspace):
        if sub.task.startswith("flaky") and not self.failed_once:
            self.failed_once = True
            raise RuntimeError("first attempt fails")
        return f"done {sub.id}"


class _HoldTurnControl(AgentControl):
    """Each turn blocks until it is interrupted or its task is cancelled."""

    async def _run_subagent(self, sub, workspace):
        await asyncio.Event().wait()


class _FakeWorktree:
    """Stands in for WorktreeManager: no git, but merge/cleanup are recorded."""

    def __init__(self, root) -> None:
        self.worktrees_root = Path(root) / ".fake-worktrees"
        self.merged: list[str] = []
        self.cleaned: list[str] = []

    def ensure_base(self) -> None:
        pass

    def create(self, agent_id: str) -> str:
        path = self.worktrees_root / agent_id
        path.mkdir(parents=True, exist_ok=True)
        return str(path)

    def merge(self, agent_id: str):
        from types import SimpleNamespace

        self.merged.append(agent_id)
        return SimpleNamespace(clean=True, conflicts=(), detail="")

    def cleanup(self, agent_id: str) -> None:
        self.cleaned.append(agent_id)


def test_retrying_a_root_revives_the_whole_blocked_chain(tmp_path):
    async def scenario():
        ctrl = _FlakyOnceControl(str(tmp_path))
        first = ctrl.spawn("flaky work", name="first", isolate=False)
        second = ctrl.spawn(
            "second work", name="second", isolate=False, depends_on=[first]
        )
        third = ctrl.spawn(
            "third work", name="third", isolate=False, depends_on=[second]
        )
        await _spin(16)

        assert ctrl.get(first).status == "failed"
        assert ctrl.get(second).status == "blocked"
        assert ctrl.get(third).status == "blocked"

        retry = ctrl.spawn("flaky work again", name="first", isolate=False)
        await _spin(80)
        return (
            ctrl.get(retry).status,
            ctrl.get(second).status,
            ctrl.get(third).status,
        )

    assert asyncio.run(scenario()) == ("idle", "idle", "idle")


def test_interrupting_an_isolated_child_keeps_dependents_waiting(
    tmp_path, monkeypatch
):
    import core.team.worktree as worktree_module

    monkeypatch.setattr(worktree_module, "WorktreeManager", _FakeWorktree)

    async def scenario():
        ctrl = _HoldTurnControl(str(tmp_path))
        first = ctrl.spawn("slow work", name="slow")
        await _spin(6)
        ctrl.interrupt(first)
        await _spin(6)
        dependent = ctrl.spawn(
            "after work", name="after", isolate=False, depends_on=[first]
        )
        await _spin(6)
        sub = ctrl.get(first)
        return sub.status, sub.merge_pending, ctrl.get(dependent).status

    status, merge_pending, dependent = asyncio.run(scenario())

    # The interrupted turn never merged, so its predecessor changes are not in
    # the base: a dependent must wait, not start on a stale workspace.
    assert status == "idle"
    assert merge_pending is True
    assert dependent == "queued"

def test_retry_does_not_revive_a_dependent_the_parent_already_replaced(tmp_path):
    async def scenario():
        ctrl = _FlakyOnceControl(str(tmp_path))
        first = ctrl.spawn("flaky work", name="first", isolate=False)
        second = ctrl.spawn(
            "second work", name="second", isolate=False, depends_on=[first]
        )
        await _spin(16)
        assert ctrl.get(second).status == "blocked"

        # The parent reacts to the blocked notice by re-doing the work itself.
        replacement = ctrl.spawn("second work", name="second", isolate=False)
        await _spin(8)
        assert ctrl.get(replacement).status in ("running", "idle")

        retry = ctrl.spawn("flaky work again", name="first", isolate=False)
        await _spin(80)
        return ctrl.get(second).status, ctrl.get(replacement).status, ctrl.get(retry).status

    original, replacement, retry = asyncio.run(scenario())

    assert original == "blocked"  # superseded, not revived into duplicate work
    assert replacement == "idle"
    assert retry == "idle"
