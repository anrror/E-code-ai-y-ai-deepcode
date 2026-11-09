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

## Where the catalog lives

```text
~/.deepcode/team/catalog.yaml              organization and line entries,
                                           shared by every workspace here
<workspace>/.deepcode/team/catalog.yaml    the workspace's entities, its task
                                           binding, and local knowledge
```

The workspace layer wins on the same id. Commit it with the repository: a
convention that changes build output is repository content, not a personal note.

```yaml
bind: task://LEAD-1234

entities:
  - { id: org://auto-sub, kind: org, name: Auto Sub }
  - { id: line://auto-outsourcing, kind: line, name: Outsourcing, parent: org://auto-sub }
  - { id: project://changan-lead-center, kind: project, name: Lead Center, parent: line://auto-outsourcing }
  - { id: batch://phase-1, kind: batch, name: Phase 1, parent: project://changan-lead-center }
  - { id: batch://phase-2-external, kind: batch, name: Phase 2 (external),
      parent: project://changan-lead-center, external: true }
  - { id: version://v1.2.0, kind: version, name: v1.2.0, parent: batch://phase-1 }
  - { id: task://LEAD-1234, kind: task, name: Lead importer, parent: version://v1.2.0 }

knowledge:
  - id: K-001
    scope: org://auto-sub
    type: redline
    statement: Never commit production keys
    status: reviewed
    confidence: high
    owner: platform
  - id: K-002
    scope: project://changan-lead-center
    type: decision
    statement: Lead Center stores leads in PostgreSQL
    rationale: Shared failover with the CRM
    status: reviewed
  - id: K-003
    scope: batch://phase-2-external
    type: external
    statement: Phase 2 added a GraphQL endpoint
    source: external-phase-2
    status: reviewed
  - id: K-004
    scope: task://LEAD-1234
    type: lesson
    statement: The importer retries twice before surfacing an error
    status: draft
```

A workspace without this file, or without a binding, behaves exactly as before:
no extra prompt text, no tool calls. A malformed catalog is logged and skipped,
never allowed to fail a Turn.

## How a Turn sees it

`build_agent_session` appends the assembled block to the system prompt:

```text
<team-context task="task://LEAD-1234" chain="task://LEAD-1234 > version://v1.2.0 > ...">
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
