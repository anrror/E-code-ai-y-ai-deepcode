"""Per-agent cost attribution for routed sub-agents.

A category that is cheap on paper is only cheap if the ledger says so. These
tests pin the three links: the scope that labels a child's calls, the record
field that persists the label, and the summary that groups by it.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.observability.context import agent_path_scope, current_agent_path
from core.observability.llmops import summarize_llm_calls
from core.observability.records import LLMLogRecord


def _record(*, agent_path: str | None, model: str = "deepseek-flash"):
    return LLMLogRecord.make(
        task_id=None,
        session_id="s",
        provider="deepseek",
        model=model,
        phase="implementation",
        duration_ms=12,
        status="ok",
        usage={"prompt_tokens": 1_000, "completion_tokens": 500},
        agent_path=agent_path,
    )


def test_scope_sets_and_restores_the_agent_path():
    assert current_agent_path() is None
    with agent_path_scope("fast:agent-1"):
        assert current_agent_path() == "fast:agent-1"
        with agent_path_scope("hard:agent-2"):
            assert current_agent_path() == "hard:agent-2"
        assert current_agent_path() == "fast:agent-1"
    assert current_agent_path() is None


def test_record_persists_agent_path_only_when_set():
    labelled = _record(agent_path="fast:agent-1").to_jsonl()
    unlabelled = _record(agent_path=None).to_jsonl()

    assert '"agent_path": "fast:agent-1"' in labelled
    assert "agent_path" not in unlabelled


def test_summary_groups_cost_by_agent_path():
    summary = summarize_llm_calls(
        [
            json.loads(_record(agent_path="fast:agent-1").to_jsonl()),
            json.loads(_record(agent_path="fast:agent-1").to_jsonl()),
            json.loads(_record(agent_path=None, model="deepseek-v4-pro").to_jsonl()),
        ]
    )

    assert set(summary.by_agent_path) == {"fast:agent-1", "parent"}
    assert summary.by_agent_path["fast:agent-1"]["calls"] == 2
    assert summary.by_agent_path["parent"]["calls"] == 1
    # The child's dollars are real numbers, not None: the catalog prices it.
    assert summary.by_agent_path["fast:agent-1"]["usd"] is not None
    assert 0 < summary.by_agent_path["fast:agent-1"]["usd"] < summary.usd
    assert summary.to_dict()["by_agent_path"]["parent"]["calls"] == 1
