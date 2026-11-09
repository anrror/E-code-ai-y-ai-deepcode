# Team context

A Session starts from the same organization context for every teammate, instead
of each person re-explaining the same architecture, conventions, and past
mistakes. DeepCode implements the repository side of that idea: a six-layer
ownership chain, a reviewed knowledge catalog, and an assembly step that every
Turn reads.

## The six layers

```text
org      org://auto-sub              compliance, red lines, toolchain
line     line://auto-outsourcing     industry knowledge, reusable frameworks
project  project://changan-lead-center  customer constraints, ADRs, contracts
batch    batch://phase-1             contract scope, acceptance criteria
version  version://v1.2.0            tag, API contract, migrations
task     task://LEAD-1234            requirement card, impact, verification
```

Every entity has exactly one parent, so each workspace resolves to **one main
ownership chain**. Cross-line reuse is a reference, never a copy: copying a
line's knowledge into a project is how two sources of truth start.

Red lines (`type: redline`) belong at the organization level and can never be
overridden by a narrower scope.

## Products are assets, not a seventh layer

A company usually owns **PaaS products**; a line owns several **standard
products** plus **historical products** whose designs and codebases still
matter. Those are assets, not management layers: making a product a parent would
force every project to inherit from one product. A product is owned by the org
or by a line, and a project **references** the products it builds on:

```yaml
# .deepcode/team/product/paas/entity.yaml
name: PaaS
parent: org://auto-sub
codebases: ["https://git/paas"]
designs: ["docs/paas/design.md"]
```

```yaml
# .deepcode/team/product/legacy-crm/entity.yaml
name: Legacy CRM
parent: line://auto-outsourcing
archived: true            # historical: searchable, labelled, never the standard
codebases: ["https://git/legacy-crm"]
designs: ["docs/legacy/design.md"]
```

```yaml
# .deepcode/team/project/changan/entity.yaml
name: Changan Lead Center
parent: line://auto-outsourcing
references: [product://paas, product://std-crm, product://legacy-crm]
```

A referenced product contributes its instructions, design knowledge, and
codebase/design pointers **after** the project's own chain: it informs, it never
overrides. `search` follows references one hop, so a task finds the PaaS
convention or the historical design without the graph growing transitively.
`archived: true` keeps a historical product searchable and referenceable while
marking every line `[historical]`; it never counts as the current standard.

## Every layer is its own directory

```text
~/.deepcode/team/                       shared by every workspace on this machine
  company/auto-sub/
    entity.yaml                         name, external, workspace binding
    AGENTS.md                           that layer's standing instructions
    knowledge.yaml                      entries owned by this layer
    knowledge/*.yaml                    one entry (or a list) per file
<workspace>/.deepcode/team/             the workspace's own layers, committed
  line/auto-outsourcing/…
  project/changan/…
  batch/phase-1/…
  version/v1.2.0/…
  task/LEAD-1234/
```

Each layer is configured independently: changing the project layer does not
touch the line or the task; a reviewer edits one directory and the diff stays
readable. `entity.yaml` may omit `id` (derived as `kind://directory-name`) and
`name` (defaults to the directory name); `parent` builds the chain, and
`workspace` binds a task to a folder without any other file.

```yaml
# .deepcode/team/task/LEAD-1234/entity.yaml
name: Lead importer
parent: version://v1.2.0
workspace: E:\code\team\lead-center
```

```yaml
# .deepcode/team/task/LEAD-1234/knowledge.yaml
- id: K-004
  scope: task://LEAD-1234
  type: lesson
  statement: The importer retries twice before surfacing an error
  status: draft
```

The workspace root wins over the shared root on the same id. A single
`.deepcode/team/catalog.yaml` is still read (and is where programmatic `add`
lands when a layer has no directory), but the per-layer directories are the
configurable form: one folder per level, each with its own instructions,
knowledge, and metadata.

`AGENTS.md` at every layer is injected far-to-near, so the nearest layer is the
last word and therefore wins on a conflict.

## How a Turn sees it

`build_agent_session` appends the assembled block to the system prompt:

```text
<team-context task="task://LEAD-1234" chain="task://LEAD-1234 > version://v1.2.0 > ...">
layer instructions (a nearer layer is read later and wins):
--- org://auto-sub ---
Never commit production keys. Use the shared toolchain.
--- task://LEAD-1234 ---
Run pytest -q before claiming done.
red lines (organization, never overridden):
- [red line] Never commit production keys
team facts (reviewed):
- Lead Center stores leads in PostgreSQL — Shared failover with the CRM
drafts (unreviewed):
- [draft] The importer retries twice before surfacing an error
external references (unverified; not team facts):
- [external: external-phase-2, unverified] Phase 2 added a GraphQL endpoint
</team-context>
```

Precedence and safety:

1. the chain is read task → version → batch → project → line → org;
2. a red line is never removed by a `supersedes` pointer — the pointer is
   rejected and the attempt is reported;
3. `deprecated` entries are dropped, `reviewed` outranks `draft`, drafts are
   labelled;
4. external batches are included only as **unverified references**. A Turn that
   needs facts excludes them (`include_external: false` on the tool);
5. the block is capped (`6000` characters by default) and drops the weakest
   entries first, never silently mid-line.

## The `team_context` tool

| Action | What it does |
|---|---|
| `assemble` | The current task's block, with counts and a `blockedOverrides` list |
| `search` | Text search inside a scope's chain, reviewed first, external filtered |
| `add` | Writes a **draft** entry to the workspace catalog for review |

`add` never publishes: governance is an explicit act. A reviewer promotes a
draft by setting `status: reviewed` in the catalog (or through
`TeamContextService.promote`), which is where an organization's approval process
attaches.

## What this does and does not cover

Implemented here, in the repository:

- the six-layer model and single ownership;
- inheritance, red-line protection, review precedence, external isolation;
- permission-aware retrieval (scope chain first, then text; external filtered);
- prompt assembly and a tool for search and recording;
- the same `AGENTS.md` layering DeepCode already had (user-global → repo root →
  workspace), which stays the mechanism for rules that must not drift.

Left to the systems that own them:

- a company wiki (Confluence, GitBook) as the searchable knowledge backend —
  the catalog format is the shape to import from and export to;
- a CodeGraph-style index (callers, callees, impact) — DeepCode's existing
  search tools cover the first step; a persisted index is a separate project;
- an MCP aggregator that merges code and knowledge retrieval — the
  `team_context` tool is the seam it would wrap;
- CI enforcement of ADRs and `AGENTS.md` review — the repository already treats
  these as reviewable files, so a CI check is additive.
