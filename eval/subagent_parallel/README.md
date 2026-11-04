# Serial vs parallel sub-agents

A decision gate, not a benchmark. It answers one question before the project
invests in graph-level orchestration:

> Does delegating five small tasks to five isolated sub-agents (merged back from
> worktrees) beat one agent doing them in order?

## Method

- **Fixture** — `fixture/` is a small but multi-file Python project. All five
  tasks in `fixture/TASKS.md` touch `app/cart.py` and `app/pricing.py`, so
  isolated parallel work *must* merge. That is deliberate: it measures the cost
  that optimistic benchmarks omit.
- **Acceptance** — `fixture/tests/test_acceptance.py` ships failing and is
  SHA-256 hashed before and after each run. A run that edits the tests is not a
  pass.
- **Arms** — same model, same fixture, same five tasks:
  - `serial`: one agent implements all five itself.
  - `parallel`: the agent must call `spawn_agent(..., isolate=true)` once per
    task and then `wait_agent` until all report. A run with fewer than
    `--min-spawns` (default 5) calls is marked invalid and excluded from the
    medians.
- **Tokens** — summed from the process LLM ledger (`~/.deepcode/logs/llm.jsonl`)
  inside the run window. This is the only source that includes sub-agent calls;
  the per-session event stream sees the parent only. Run the experiment on an
  otherwise idle machine, or unrelated sessions land in the window.
- **Isolation** — every run copies the fixture to `.runs/<stamp>/`, runs
  `git init` there, and executes `deepcode exec --access full-access --trust`.
  The repository's own working tree is never touched.
- **Credentials** — the key is read from the local `auth.json` at run time and
  passed only as an environment variable to the child process. It is never
  written to the report.

## Run

```powershell
# plumbing only: copies the fixture, runs the baseline suite, no model calls
.venv\Scripts\python.exe eval\subagent_parallel\run_experiment.py --dry-run

# the gate: 3 repetitions per arm
.venv\Scripts\python.exe eval\subagent_parallel\run_experiment.py --reps 3

# the same gate on the repository's own kernel work (see self_replay/)
.venv\Scripts\python.exe eval\subagent_parallel\self_replay\prepare.py \
  --out eval\subagent_parallel\.runs\self-replay\base
.venv\Scripts\python.exe eval\subagent_parallel\run_experiment.py \
  --fixture eval\subagent_parallel\.runs\self-replay\base --min-spawns 3 \
  --prompts eval\subagent_parallel\self_replay\prompts.json --reps 1
```

Results so far: [`RESULTS.md`](RESULTS.md) for the fixture,
[`self_replay/RESULTS.md`](self_replay/RESULTS.md) for the real workload.

## Decision rules (pre-registered)

| Metric | Gate |
|---|---|
| Wall-clock speedup (median serial / median parallel) | ≥ 1.30× |
| Pass rate | parallel not worse than serial by > 10 percentage points |
| Delegation validity | ≥ `--min-spawns` per parallel run (5 by default, 3 for the self-replay) |

Both gates must pass to justify building graph-level orchestration on top of
this. Otherwise the project stops at category routing + post-merge verification.

## When the answer would differ

This fixture is the *worst case* for delegation: tiny tasks, overlapping files.
Parallelism can still win when subtasks are long (minutes each), independent
(disjoint files), or dominated by I/O. Run the same gate on your own workload
before generalising this result.
