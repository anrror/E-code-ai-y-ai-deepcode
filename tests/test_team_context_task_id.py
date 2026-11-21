"""Company task identity configured inside the team context.

The task id belongs to the team catalog: a product, project, task or any other
layer declares ``task_id`` in its ``entity.yaml``. The nearest layer on the
bound chain wins and the assembled block reports it for Session attribution.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from core.harness.tools import default_coding_tools
from core.team_context import (
    Catalog,
    TeamContextError,
    TeamContextService,
    TeamEntity,
    assemble,
    team_context_preamble,
)


def _write_layer(
    workspace: Path,
    kind: str,
    slug: str,
    meta: dict,
    *,
    knowledge: list[dict] | None = None,
) -> Path:
    directory_name = "company" if kind == "org" else kind
    directory = workspace / ".dfcode" / "team" / directory_name / slug
    directory.mkdir(parents=True)
    (directory / "entity.yaml").write_text(
        yaml.safe_dump(meta, allow_unicode=True), encoding="utf-8"
    )
    if knowledge:
        (directory / "knowledge.yaml").write_text(
            yaml.safe_dump(knowledge, allow_unicode=True), encoding="utf-8"
        )
    return directory


def test_a_project_layer_reports_its_task_id_in_the_preamble(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_layer(
        workspace,
        "project",
        "demo",
        {"name": "Demo", "workspace": str(workspace), "task_id": "TASK-77"},
    )

    preamble = team_context_preamble(workspace, home=tmp_path / "home")

    assert 'task-id="TASK-77"' in preamble
    assert "task id (company-assigned): TASK-77 (from project://demo)" in preamble
    assert "team_context tool" in preamble


def test_a_product_layer_can_carry_the_task_id(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_layer(
        workspace,
        "product",
        "paas",
        {"name": "PaaS", "workspace": str(workspace), "task_id": "PROD-1"},
    )

    preamble = team_context_preamble(workspace, home=tmp_path / "home")

    assert 'task-id="PROD-1"' in preamble
    assert "task id (company-assigned): PROD-1 (from product://paas)" in preamble


def test_without_a_bound_layer_the_preamble_stays_empty(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    assert team_context_preamble(workspace, home=tmp_path / "home") == ""


def test_an_ownership_chain_is_injected_without_knowledge(tmp_path: Path) -> None:
    """A bound product under a line and an org is team context on its own."""

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    home = tmp_path / "home"
    team = home / ".dfcode" / "team"
    layers = {
        "company/default": {"name": "Org"},
        "line/ai-tools": {"name": "AI tools", "parent": "org://default"},
        "product/dfcodex": {
            "name": "DFcodeX",
            "parent": "line://ai-tools",
            "workspace": str(workspace),
        },
    }
    for relative, meta in layers.items():
        directory = team / relative
        directory.mkdir(parents=True)
        (directory / "entity.yaml").write_text(
            yaml.safe_dump(meta, allow_unicode=True), encoding="utf-8"
        )

    preamble = team_context_preamble(workspace, home=home)

    assert 'task="product://dfcodex"' in preamble
    assert "product://dfcodex > line://ai-tools > org://default" in preamble
    # The header carries the ids; the body says what they are, including a
    # display name that differs from the id's slug.
    assert (
        "ownership (nearest first): "
        "product://dfcodex > line://ai-tools (AI tools) > org://default (Org)"
    ) in preamble
    # A pointer back at the agent's own workspace is noise, not context.
    assert "layer pointers" not in preamble


def test_a_lone_layer_instruction_still_reaches_the_model(tmp_path: Path) -> None:
    """Instructions are content, even without a chain or knowledge entries."""

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    directory = _write_layer(
        workspace, "project", "demo", {"name": "Demo", "workspace": str(workspace)}
    )
    (directory / "AGENTS.md").write_text("Run pytest -q", encoding="utf-8")

    preamble = team_context_preamble(workspace, home=tmp_path / "home")

    assert "Run pytest -q" in preamble
    # One layer has no ownership chain to summarize.
    assert "ownership (nearest first)" not in preamble


def test_a_lone_layer_pointer_still_reaches_the_model(tmp_path: Path) -> None:
    """A design pointer is real context, not workspace noise."""

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_layer(
        workspace,
        "product",
        "dfcodex",
        {
            "name": "DFcodeX",
            "workspace": str(workspace),
            "designs": ["docs/design.md"],
        },
    )

    preamble = team_context_preamble(workspace, home=tmp_path / "home")

    assert "docs/design.md" in preamble
    assert "ownership (nearest first)" not in preamble


def test_a_lone_unconfigured_project_stays_silent(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    home = tmp_path / "home"
    directory = home / ".dfcode" / "team" / "project" / "demo"
    directory.mkdir(parents=True)
    (directory / "entity.yaml").write_text(
        yaml.safe_dump(
            {"name": "Demo", "workspace": str(workspace)}, allow_unicode=True
        ),
        encoding="utf-8",
    )

    assert team_context_preamble(workspace, home=home) == ""


def test_the_nearest_chain_layer_wins_and_a_reference_does_not_relabel() -> None:
    entities = {
        "org://o": TeamEntity(id="org://o", kind="org", name="Org", task_id="ORG-1"),
        "product://standard": TeamEntity(
            id="product://standard",
            kind="product",
            name="Standard",
            parent="org://o",
            task_id="PROD-1",
        ),
        "project://p": TeamEntity(
            id="project://p",
            kind="project",
            name="Project",
            parent="org://o",
            task_id="PRJ-1",
            references=("product://standard",),
        ),
        "task://t": TeamEntity(
            id="task://t", kind="task", name="Task", parent="project://p"
        ),
    }
    catalog = Catalog(entities=entities)

    assembled = assemble(catalog, task="task://t")
    assert assembled.task_id == "PRJ-1"
    assert assembled.task_id_source == "project://p"
    assert 'task-id="PRJ-1"' in assembled.text

    entities["task://t"] = replace(entities["task://t"], task_id="TASK-1")
    closest = assemble(Catalog(entities=entities), task="task://t")
    assert closest.task_id == "TASK-1"
    assert closest.task_id_source == "task://t"

    entities["project://p"] = replace(entities["project://p"], task_id=None)
    entities["org://o"] = replace(entities["org://o"], task_id=None)
    entities.pop("task://t")
    entities["task://t2"] = TeamEntity(
        id="task://t2", kind="task", name="Task 2", parent="project://p"
    )
    no_relabel = assemble(Catalog(entities=entities), task="task://t2")
    assert no_relabel.task_id is None
    assert "PROD-1" not in no_relabel.text


def test_task_id_is_validated_on_entities() -> None:
    with pytest.raises(TeamContextError):
        TeamEntity(id="project://p", kind="project", name="P", task_id="bad id")

    with pytest.raises(TeamContextError):
        TeamEntity.from_mapping(
            {
                "id": "project://p",
                "kind": "project",
                "name": "P",
                "task_id": "bad id",
            }
        )

    camel = TeamEntity.from_mapping(
        {
            "id": "project://p",
            "kind": "project",
            "name": "P",
            "taskId": "T-1",
        }
    )
    assert camel.task_id == "T-1"


def test_service_and_tool_expose_the_layer_task_id(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_layer(
        workspace,
        "project",
        "demo",
        {"name": "Demo", "workspace": str(workspace), "task_id": "TASK-7"},
    )

    service = TeamContextService(workspace, home=tmp_path / "home")
    assembled = service.assemble()
    assert assembled["taskId"] == "TASK-7"
    assert assembled["taskIdSource"] == "project://demo"
    assert service.tree()["taskId"] == "TASK-7"

    registry = default_coding_tools(str(workspace), skills=())
    tool = registry.get("team_context")
    assert tool is not None
    output = asyncio.run(tool.execute(action="assemble"))
    payload = json.loads(output)
    assert payload["taskId"] == "TASK-7"
    assert 'task-id="TASK-7"' in payload["text"]


def test_create_and_update_can_set_and_clear_a_layer_task_id(tmp_path: Path) -> None:
    service = TeamContextService(None, home=tmp_path / "home")

    created = service.create_entity(
        kind="project",
        slug="demo",
        shared=True,
        task_id="TASK-1",
    )
    assert created["entity"]["taskId"] == "TASK-1"

    updated = service.update_entity(
        "project://demo",
        shared=True,
        task_id="TASK-2",
    )
    assert updated["entity"]["taskId"] == "TASK-2"

    cleared = service.update_entity(
        "project://demo",
        shared=True,
        task_id="",
    )
    assert "taskId" not in cleared["entity"]
