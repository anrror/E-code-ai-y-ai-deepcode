from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from core.team_context import (
    Catalog,
    KnowledgeEntry,
    TeamContextError,
    TeamContextService,
    TeamEntity,
    assemble,
    load_catalog,
    resolve_chain,
    save_catalog,
    team_context_preamble,
)


def _catalog() -> Catalog:
    entities = [
        TeamEntity(id="org://auto-sub", kind="org", name="Auto Sub"),
        TeamEntity(
            id="line://auto-outsourcing",
            kind="line",
            name="Outsourcing",
            parent="org://auto-sub",
        ),
        TeamEntity(
            id="project://changan-lead-center",
            kind="project",
            name="Lead Center",
            parent="line://auto-outsourcing",
        ),
        TeamEntity(
            id="batch://phase-1",
            kind="batch",
            name="Phase 1",
            parent="project://changan-lead-center",
        ),
        TeamEntity(
            id="batch://phase-2-external",
            kind="batch",
            name="Phase 2 (external)",
            parent="project://changan-lead-center",
            external=True,
        ),
        TeamEntity(
            id="version://v1.2.0",
            kind="version",
            name="v1.2.0",
            parent="batch://phase-1",
        ),
        TeamEntity(
            id="task://LEAD-1234",
            kind="task",
            name="Task",
            parent="version://v1.2.0",
        ),
    ]
    knowledge = [
        KnowledgeEntry(
            id="K-org-red",
            scope="org://auto-sub",
            type="redline",
            statement="Never commit production keys",
            status="reviewed",
            confidence="high",
            owner="platform",
        ),
        KnowledgeEntry(
            id="K-line",
            scope="line://auto-outsourcing",
            type="convention",
            statement="Use the shared logging facade",
            status="reviewed",
        ),
        KnowledgeEntry(
            id="K-project",
            scope="project://changan-lead-center",
            type="decision",
            statement="Lead Center uses PostgreSQL",
            status="reviewed",
        ),
        KnowledgeEntry(
            id="K-task",
            scope="task://LEAD-1234",
            type="lesson",
            statement="The importer retries twice",
            status="draft",
        ),
        KnowledgeEntry(
            id="K-ext",
            scope="batch://phase-2-external",
            type="external",
            statement="Phase 2 added a GraphQL endpoint",
            source="external-phase-2",
            status="reviewed",
        ),
        KnowledgeEntry(
            id="K-deprecated",
            scope="project://changan-lead-center",
            type="decision",
            statement="Old decision",
            status="deprecated",
        ),
    ]
    return Catalog(
        entities={entity.id: entity for entity in entities},
        knowledge={entry.id: entry for entry in knowledge},
        bind="task://LEAD-1234",
    )


def _workspace_with_catalog(tmp_path: Path, catalog: Catalog) -> Path:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    save_catalog(workspace, catalog, home=tmp_path / "home")
    return workspace


def test_the_chain_runs_from_task_to_org() -> None:
    chain = resolve_chain(_catalog(), "task://LEAD-1234")
    assert [entity.kind for entity in chain] == [
        "task",
        "version",
        "batch",
        "project",
        "line",
        "org",
    ]


def test_assembly_labels_scopes_and_orders_red_lines_first() -> None:
    result = assemble(_catalog(), task="task://LEAD-1234")
    assert "red lines" in result.text
    assert result.text.index("Never commit production keys") < result.text.index(
        "Use the shared logging facade"
    )
    assert "[draft]" in result.text
    assert "Old decision" not in result.text


def test_external_references_are_labelled_and_can_be_excluded() -> None:
    included = assemble(_catalog(), task="batch://phase-2-external")
    assert "GraphQL" in included.text
    assert "external: external-phase-2, unverified" in included.text

    excluded = assemble(
        _catalog(), task="batch://phase-2-external", include_external=False
    )
    assert "GraphQL" not in excluded.text
    assert excluded.external_excluded == 1


def test_a_narrow_scope_cannot_override_an_org_red_line() -> None:
    catalog = _catalog()
    override = KnowledgeEntry(
        id="K-rogue",
        scope="task://LEAD-1234",
        type="convention",
        statement="Keys may live in the repo",
        status="reviewed",
        supersedes="K-org-red",
    )
    catalog = Catalog(
        entities=catalog.entities,
        knowledge={**catalog.knowledge, override.id: override},
        bind=catalog.bind,
    )
    result = assemble(catalog, task="task://LEAD-1234")
    assert "Keys may live in the repo" not in result.text
    assert "Never commit production keys" in result.text
    assert result.blocked_overrides == ("K-rogue",)


def test_the_budget_truncates_the_weakest_entries() -> None:
    result = assemble(_catalog(), task="task://LEAD-1234", max_chars=260)
    assert result.truncated is True
    assert "red lines" in result.text
    assert len(result.text) <= 400


def test_catalog_round_trip_and_workspace_binding(tmp_path: Path) -> None:
    catalog = _catalog()
    workspace = _workspace_with_catalog(tmp_path, catalog)
    loaded = load_catalog(workspace, home=tmp_path / "home")
    assert loaded.bind == "task://LEAD-1234"
    assert loaded.entities.keys() == catalog.entities.keys()
    raw = yaml.safe_load(
        (workspace / ".deepcode/team/catalog.yaml").read_text(encoding="utf-8")
    )
    assert raw["bind"] == "task://LEAD-1234"
    assert raw["knowledge"][0]["scope"] == "org://auto-sub"


def test_global_catalog_merges_under_the_workspace_layer(tmp_path: Path) -> None:
    home = tmp_path / "home"
    global_path = home / ".deepcode/team/catalog.yaml"
    global_path.parent.mkdir(parents=True)
    global_path.write_text(
        yaml.safe_dump(
            {
                "entities": [
                    {"id": "org://auto-sub", "kind": "org", "name": "Auto Sub"}
                ],
                "knowledge": [
                    {
                        "id": "K-global",
                        "scope": "org://auto-sub",
                        "type": "redline",
                        "statement": "Company red line",
                        "status": "reviewed",
                    }
                ],
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    save_catalog(
        workspace,
        Catalog(
            entities=_catalog().entities,
            knowledge={
                key: value
                for key, value in _catalog().knowledge.items()
                if key != "K-org-red"
            },
            bind="task://LEAD-1234",
        ),
        home=home,
    )
    loaded = load_catalog(workspace, home=home)
    assert "K-global" in loaded.knowledge
    assert "K-org-red" not in loaded.knowledge
    assert "Company red line" in assemble(loaded, task="task://LEAD-1234").text


def test_unknown_scope_and_wrong_parent_kind_are_rejected(tmp_path: Path) -> None:
    home = tmp_path / "home"
    entities = {
        "org://auto-sub": TeamEntity(id="org://auto-sub", kind="org", name="org"),
        "task://x": TeamEntity(
            id="task://x", kind="task", name="x", parent="org://auto-sub"
        ),
    }
    with pytest.raises(TeamContextError):
        save_catalog(tmp_path, Catalog(entities=entities), home=home)

    catalog = _catalog()
    orphan = KnowledgeEntry(
        id="K-orphan",
        scope="project://does-not-exist",
        type="lesson",
        statement="orphan",
    )
    with pytest.raises(TeamContextError):
        save_catalog(
            tmp_path,
            Catalog(
                entities=catalog.entities,
                knowledge={**catalog.knowledge, orphan.id: orphan},
                bind=catalog.bind,
            ),
            home=home,
        )


def test_service_searches_within_a_scope_and_records_drafts(tmp_path: Path) -> None:
    workspace = _workspace_with_catalog(tmp_path, _catalog())
    service = TeamContextService(workspace, home=tmp_path / "home")

    hits = service.search("logging", scope="task://LEAD-1234")
    assert [row["id"] for row in hits] == ["K-line"]

    without_external = service.search("GraphQL", include_external=False)
    assert without_external == []

    added = service.add(
        scope="project://changan-lead-center",
        type="lesson",
        statement="The legacy importer ignores BOM headers",
        rationale="observed twice",
    )
    entry = added["entry"]
    assert entry["status"] == "draft"
    reloaded = load_catalog(workspace, home=tmp_path / "home")
    assert reloaded.knowledge[entry["id"]].status == "draft"

    promoted = service.promote(entry["id"], owner="tech-lead")
    assert promoted["status"] == "reviewed"
    assert load_catalog(workspace, home=tmp_path / "home").knowledge[
        entry["id"]
    ].owner == "tech-lead"


def test_prompt_preamble_is_empty_without_a_binding(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    assert team_context_preamble(workspace, home=tmp_path / "home") == ""

    save_catalog(workspace, _catalog(), home=tmp_path / "home")
    preamble = team_context_preamble(workspace, home=tmp_path / "home")
    assert preamble.startswith('<team-context task="task://LEAD-1234"')
    assert "Never commit production keys" in preamble
    assert "team_context tool" in preamble


def test_a_malformed_catalog_does_not_break_prompt_assembly(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    path = workspace / ".deepcode/team/catalog.yaml"
    path.parent.mkdir(parents=True)
    path.write_text("entities: [", encoding="utf-8")
    assert team_context_preamble(workspace, home=tmp_path / "home") == ""

def test_each_layer_is_configured_by_its_own_directory(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    team = workspace / ".deepcode/team"

    def layer(directory: str, slug: str, meta: dict, *, agents: str = "", knowledge=None):
        target = team / directory / slug
        target.mkdir(parents=True)
        (target / "entity.yaml").write_text(
            yaml.safe_dump(meta, allow_unicode=True), encoding="utf-8"
        )
        if agents:
            (target / "AGENTS.md").write_text(agents, encoding="utf-8")
        if knowledge:
            (target / "knowledge.yaml").write_text(
                yaml.safe_dump(knowledge, allow_unicode=True), encoding="utf-8"
            )

    layer("company", "auto-sub", {"name": "Auto Sub"}, agents="Company red line: never commit keys")
    layer("line", "auto-outsourcing", {"name": "Outsourcing", "parent": "org://auto-sub"})
    layer("project", "changan", {"name": "Changan", "parent": "line://auto-outsourcing"})
    layer("batch", "phase-1", {"name": "Phase 1", "parent": "project://changan"})
    layer("version", "v1.2.0", {"name": "v1.2.0", "parent": "batch://phase-1"})
    layer(
        "task",
        "LEAD-1234",
        {"name": "Task", "parent": "version://v1.2.0", "workspace": str(workspace)},
        agents="Task instruction: run pytest -q",
        knowledge=[
            {
                "id": "K-task",
                "scope": "task://LEAD-1234",
                "type": "lesson",
                "statement": "retries twice",
                "status": "reviewed",
            }
        ],
    )

    catalog = load_catalog(workspace, home=tmp_path / "home")
    assert set(catalog.entities) == {
        "org://auto-sub",
        "line://auto-outsourcing",
        "project://changan",
        "batch://phase-1",
        "version://v1.2.0",
        "task://LEAD-1234",
    }
    assert catalog.bind_for(workspace) == "task://LEAD-1234"

    text = assemble(catalog, task="task://LEAD-1234").text
    assert "Company red line: never commit keys" in text
    assert "Task instruction: run pytest -q" in text
    assert text.index("Company red line") < text.index("Task instruction")
    assert "retries twice" in text

    service = TeamContextService(workspace, home=tmp_path / "home")
    added = service.add(
        scope="task://LEAD-1234", type="lesson", statement="draft in the layer"
    )
    assert Path(added["path"]) == team / "task/LEAD-1234/knowledge.yaml"
    promoted = service.promote(added["entry"]["id"])
    assert promoted["status"] == "reviewed"

def test_a_project_references_company_and_line_products(tmp_path: Path) -> None:
    entities = [
        TeamEntity(id="org://auto-sub", kind="org", name="Auto Sub"),
        TeamEntity(
            id="line://auto-outsourcing",
            kind="line",
            name="Outsourcing",
            parent="org://auto-sub",
        ),
        TeamEntity(
            id="product://paas",
            kind="product",
            name="PaaS",
            parent="org://auto-sub",
            codebases=("https://git/paas",),
            designs=("docs/paas/design.md",),
        ),
        TeamEntity(
            id="product://std-crm",
            kind="product",
            name="Standard CRM",
            parent="line://auto-outsourcing",
            codebases=("https://git/std-crm",),
        ),
        TeamEntity(
            id="product://legacy-crm",
            kind="product",
            name="Legacy CRM",
            parent="line://auto-outsourcing",
            archived=True,
            codebases=("https://git/legacy-crm",),
            designs=("docs/legacy/design.md",),
        ),
        TeamEntity(
            id="project://changan",
            kind="project",
            name="Changan",
            parent="line://auto-outsourcing",
            references=(
                "product://paas",
                "product://std-crm",
                "product://legacy-crm",
            ),
        ),
        TeamEntity(
            id="batch://phase-1",
            kind="batch",
            name="Phase 1",
            parent="project://changan",
        ),
        TeamEntity(
            id="version://v1.2.0",
            kind="version",
            name="v1.2.0",
            parent="batch://phase-1",
        ),
        TeamEntity(
            id="task://LEAD-1234",
            kind="task",
            name="Task",
            parent="version://v1.2.0",
        ),
    ]
    knowledge = [
        KnowledgeEntry(
            id="K-org",
            scope="org://auto-sub",
            type="redline",
            statement="Never commit keys",
            status="reviewed",
        ),
        KnowledgeEntry(
            id="K-proj",
            scope="project://changan",
            type="decision",
            statement="Use PostgreSQL",
            status="reviewed",
        ),
        KnowledgeEntry(
            id="K-paas",
            scope="product://paas",
            type="convention",
            statement="PaaS calls go through the gateway",
            status="reviewed",
        ),
        KnowledgeEntry(
            id="K-std",
            scope="product://std-crm",
            type="design",
            statement="Standard CRM uses the shared lead schema",
            status="reviewed",
        ),
        KnowledgeEntry(
            id="K-legacy",
            scope="product://legacy-crm",
            type="design",
            statement="Legacy CRM stored leads in XML",
            status="reviewed",
        ),
    ]
    catalog = Catalog(
        entities={entity.id: entity for entity in entities},
        knowledge={entry.id: entry for entry in knowledge},
    )

    result = assemble(catalog, task="task://LEAD-1234")
    text = result.text
    assert (
        'referenced="product://paas product://std-crm product://legacy-crm"'
        in text
    )
    assert "[referenced: product://std-crm]" in text
    assert "PaaS calls go through the gateway" in text
    # A referenced product informs; the project's own chain comes first.
    assert text.index("Use PostgreSQL") < text.index(
        "Standard CRM uses the shared lead schema"
    )
    assert "- product://legacy-crm [historical]" in text
    assert "codebases: https://git/legacy-crm" in text
    assert "designs: docs/legacy/design.md" in text
    assert [entity.id for entity in result.referenced] == [
        "product://paas",
        "product://std-crm",
        "product://legacy-crm",
    ]

    service = TeamContextService(
        _workspace_with_catalog(tmp_path, catalog), home=tmp_path / "home"
    )
    hits = service.search("lead schema", scope="task://LEAD-1234")
    assert [row["id"] for row in hits] == ["K-std"]

def test_unconfigured_layers_are_skipped_and_end_the_chain(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    task_dir = workspace / ".deepcode/team/task/LEAD-1"
    task_dir.mkdir(parents=True)
    (task_dir / "entity.yaml").write_text(
        yaml.safe_dump(
            {"name": "Task", "parent": "version://v9", "workspace": str(workspace)},
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    (task_dir / "AGENTS.md").write_text("Task rule", encoding="utf-8")
    (task_dir / "knowledge.yaml").write_text(
        yaml.safe_dump(
            [
                {
                    "id": "K1",
                    "scope": "task://LEAD-1",
                    "type": "lesson",
                    "statement": "local rule",
                    "status": "reviewed",
                }
            ],
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    # version://v9 is not configured: loading must not fail, the chain just stops.
    catalog = load_catalog(workspace, home=tmp_path / "home")
    result = assemble(catalog, task="task://LEAD-1")
    assert [entity.id for entity in result.chain] == ["task://LEAD-1"]
    assert "Task rule" in result.text
    assert "local rule" in result.text


def test_a_configured_layer_without_a_parent_still_contributes(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    version_dir = workspace / ".deepcode/team/version/v9"
    version_dir.mkdir(parents=True)
    (version_dir / "entity.yaml").write_text(
        yaml.safe_dump({"name": "v9"}, allow_unicode=True), encoding="utf-8"
    )
    (version_dir / "knowledge.yaml").write_text(
        yaml.safe_dump(
            [
                {
                    "id": "K9",
                    "scope": "version://v9",
                    "type": "convention",
                    "statement": "v9 contract",
                    "status": "reviewed",
                }
            ],
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    task_dir = workspace / ".deepcode/team/task/LEAD-2"
    task_dir.mkdir(parents=True)
    (task_dir / "entity.yaml").write_text(
        yaml.safe_dump(
            {"name": "Task 2", "parent": "version://v9", "workspace": str(workspace)},
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    catalog = load_catalog(workspace, home=tmp_path / "home")
    result = assemble(catalog, task="task://LEAD-2")
    assert [entity.id for entity in result.chain] == [
        "task://LEAD-2",
        "version://v9",
    ]
    assert "v9 contract" in result.text

def test_tree_and_read_expose_what_the_team_page_needs(tmp_path: Path) -> None:
    service = TeamContextService(
        _workspace_with_catalog(tmp_path, _catalog()), home=tmp_path / "home"
    )

    tree = service.tree()
    assert tree["bind"] == "task://LEAD-1234"
    assert tree["inChain"][0] == "task://LEAD-1234"
    org = next(
        entity for entity in tree["entities"] if entity["id"] == "org://auto-sub"
    )
    assert org["kind"] == "org"
    assert org["knowledgeCount"] == 1
    assert org["hasInstructions"] is False
    task = next(
        entity for entity in tree["entities"] if entity["id"] == "task://LEAD-1234"
    )
    assert task["draftCount"] == 1

    detail = service.read_entity("task://LEAD-1234")
    assert detail["entity"]["knowledgeCount"] == 1
    assert detail["knowledge"][0]["status"] == "draft"

def test_create_entity_seeds_a_layer_and_round_trips_knowledge(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    service = TeamContextService(workspace, home=tmp_path / "home")

    created = service.create_entity(kind="project", slug="changan", name="Changan")
    assert created["entity"]["id"] == "project://changan"
    assert created["entity"]["name"] == "Changan"
    assert (
        workspace / ".deepcode" / "team" / "project" / "changan" / "entity.yaml"
    ).is_file()

    added = service.add(
        scope="project://changan",
        type="decision",
        statement="Tests run with pytest.",
    )
    assert added["entry"]["status"] == "draft"

    promoted = service.promote(added["entry"]["id"], owner="reviewer")
    assert promoted["status"] == "reviewed"

    detail = service.read_entity("project://changan")
    assert detail["entity"]["knowledgeCount"] == 1
    assert detail["entity"]["draftCount"] == 0
    assert detail["knowledge"][0]["status"] == "reviewed"
