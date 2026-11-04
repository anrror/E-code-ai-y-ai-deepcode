"""Per-sub-agent model routing: a category resolves to its own ExecutionProfile.

The trap this suite exists to catch: ``build_agent_session`` ignores its
``model`` / ``connection_id`` arguments whenever ``execution_profile`` is
present (``core/agent_setup.py:455-463``), and ``_run_subagent`` always passes
one. Asserting on a stored model name would therefore pass while the child
still ran on the parent's model. These tests assert on the profile the child
is actually built with.
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import replace
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.domain.execution_profile import ExecutionProfile
from core.harness.agents.control import (
    AgentControl,
    AgentLimitError,
    UnknownCategoryError,
)


def _profile(model: str, connection: str = "conn-parent") -> ExecutionProfile:
    return ExecutionProfile(
        connection_id=connection,
        provider_name="deepseek",
        adapter="openai_compat",
        model_id=model,
        context_window=128_000,
        max_output_tokens=8_192,
        max_tokens=8_192,
        temperature=0.1,
        reasoning_effort=None,
        config_revision="rev-1",
    )


class _FakeMsg:
    type = "task_complete"
    final_text = "done"


class _FakeEvent:
    msg = _FakeMsg()


class _FakeSession:
    """Just enough of AgentSession for ``_run_subagent`` to finish one turn."""

    def __init__(self) -> None:
        self.history: list[dict] = []
        self.closed = False

    def load_history(self, history: list) -> None:
        self.history = list(history)

    async def run_stream(self, user_input):
        yield _FakeEvent()

    async def aclose(self) -> None:
        self.closed = True


def _capture_build(monkeypatch) -> dict:
    captured: dict = {}

    def fake_build(**kwargs):
        captured.update(kwargs)
        return _FakeSession(), kwargs.get("model"), None

    import core.agent_setup as setup

    monkeypatch.setattr(setup, "build_agent_session", fake_build)
    return captured


def _resolver(parent: ExecutionProfile):
    """Mirror ``runtime.resolve_execution_profile``: return a full profile."""

    def resolve(*, connection_id=None, model=None, reasoning_effort=None, phase=None):
        assert phase == "implementation"
        return replace(
            parent,
            model_id=model or parent.model_id,
            connection_id=connection_id or parent.connection_id,
        )

    return resolve


def test_category_builds_child_with_its_own_profile(monkeypatch, tmp_path):
    captured = _capture_build(monkeypatch)
    parent = _profile("parent-model")

    async def scenario():
        ctrl = AgentControl(
            str(tmp_path),
            parent.model_id,
            execution_profile=parent,
            categories={"fast": ("cheap-model", "conn-cheap")},
            model_resolver=_resolver(parent),
        )
        ctrl.spawn("do the cheap thing", isolate=False, category="fast")
        await ctrl.wait_for_activity(5.0)

    asyncio.run(scenario())

    child = captured["execution_profile"]
    assert child.model_id == "cheap-model"
    assert child.connection_id == "conn-cheap"
    # The provider/adapter follow the resolved profile, not a patched name.
    assert child.provider_name == "deepseek"


def test_no_category_inherits_the_parent_profile(monkeypatch, tmp_path):
    captured = _capture_build(monkeypatch)
    parent = _profile("parent-model")

    async def scenario():
        ctrl = AgentControl(
            str(tmp_path),
            parent.model_id,
            execution_profile=parent,
            categories={"fast": ("cheap-model", "conn-cheap")},
            model_resolver=_resolver(parent),
        )
        ctrl.spawn("ordinary work", isolate=False)
        await ctrl.wait_for_activity(5.0)

    asyncio.run(scenario())

    assert captured["execution_profile"].model_id == "parent-model"
    assert captured["execution_profile"].connection_id == "conn-parent"


def test_unknown_category_is_refused_with_the_valid_names(tmp_path):
    parent = _profile("parent-model")

    async def scenario():
        ctrl = AgentControl(
            str(tmp_path),
            parent.model_id,
            execution_profile=parent,
            categories={"fast": ("cheap-model", "conn-cheap")},
            model_resolver=_resolver(parent),
        )
        with pytest.raises(UnknownCategoryError) as excinfo:
            ctrl.spawn("x", isolate=False, category="turbo")
        return str(excinfo.value)

    message = asyncio.run(scenario())

    assert "turbo" in message
    assert "fast" in message


def test_explicit_model_overrides_the_category(monkeypatch, tmp_path):
    captured = _capture_build(monkeypatch)
    parent = _profile("parent-model")

    async def scenario():
        ctrl = AgentControl(
            str(tmp_path),
            parent.model_id,
            execution_profile=parent,
            categories={"fast": ("cheap-model", "conn-cheap")},
            model_resolver=_resolver(parent),
        )
        ctrl.spawn(
            "pinned work",
            isolate=False,
            category="fast",
            model="pinned-model",
        )
        await ctrl.wait_for_activity(5.0)

    asyncio.run(scenario())

    assert captured["execution_profile"].model_id == "pinned-model"
    # The category's connection still applies when only the model is pinned.
    assert captured["execution_profile"].connection_id == "conn-cheap"


def test_parent_profile_is_never_mutated(monkeypatch, tmp_path):
    _capture_build(monkeypatch)
    parent = _profile("parent-model")

    async def scenario():
        ctrl = AgentControl(
            str(tmp_path),
            parent.model_id,
            execution_profile=parent,
            categories={"fast": ("cheap-model", "conn-cheap")},
            model_resolver=_resolver(parent),
        )
        ctrl.spawn("routed work", isolate=False, category="fast")
        await ctrl.wait_for_activity(5.0)
        return ctrl

    asyncio.run(scenario())

    assert parent.model_id == "parent-model"
    assert parent.connection_id == "conn-parent"


class _RecordingControl:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def spawn(self, task: str, **kwargs):
        self.calls.append((task, kwargs))
        return "agent-1"


def test_spawn_tool_forwards_the_routing_arguments():
    from core.harness.tools.spawn_agent import SpawnAgentTool

    control = _RecordingControl()
    result = asyncio.run(
        SpawnAgentTool(control).execute(
            task="do it",
            name="job",
            category="fast",
            model="pinned-model",
            connection_id="conn-cheap",
        )
    )

    assert "agent-1" in result
    _, kwargs = control.calls[0]
    assert kwargs["category"] == "fast"
    assert kwargs["model"] == "pinned-model"
    assert kwargs["connection_id"] == "conn-cheap"


def test_spawn_tool_rejects_a_non_string_category():
    from core.harness.tools.spawn_agent import SpawnAgentTool

    control = _RecordingControl()
    result = asyncio.run(
        SpawnAgentTool(control).execute(task="do it", name="job", category=["fast"])
    )

    assert result.startswith("Error:")
    assert control.calls == []

def test_connection_override_inherits_the_parent_model(monkeypatch, tmp_path):
    captured = _capture_build(monkeypatch)
    parent = _profile("parent-model")

    async def scenario():
        ctrl = AgentControl(
            str(tmp_path),
            parent.model_id,
            execution_profile=parent,
            model_resolver=_resolver(parent),
        )
        ctrl.spawn("connection only", isolate=False, connection_id="conn-extra")
        await ctrl.wait_for_activity(5.0)

    asyncio.run(scenario())

    child = captured["execution_profile"]
    assert child.connection_id == "conn-extra"
    # Omitted model must inherit the parent, not agents.implementation.model.
    assert child.model_id == "parent-model"


def test_external_backend_rejects_routing_arguments(tmp_path):
    async def scenario():
        ctrl = AgentControl(str(tmp_path))
        with pytest.raises(AgentLimitError) as excinfo:
            ctrl.spawn("x", backend="codex", category="fast")
        return str(excinfo.value)

    message = asyncio.run(scenario())

    assert "codex" in message
    assert "category" in message
