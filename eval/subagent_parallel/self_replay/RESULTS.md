# Self-replay gate — 2026-11-04

The first gate used five tasks that each took seconds. That is the case where
orchestration overhead is pure loss, so it could not test the claim "parallelism
may pay on long tasks". This run uses the work this repository actually did.

## Workload

The three kernel features shipped earlier: per-category model routing,
dependency ordering, and per-agent cost attribution. The base keeps HEAD's tests
and reverts the eight implementation files to `aa6ed16`:

```powershell
.venv\Scripts\python.exe eval/subagent_parallel/self_replay/prepare.py \
  --out eval/subagent_parallel/.runs/self-replay/base
```

Baseline: **9 failed, 26 passed, 2 collection errors** across the acceptance
targets. Turns are real work: 84–90 model calls per serial run.

## Results

Model `deepseek-flash`; tokens are summed from the process LLM ledger
(`~/.deepcode/logs/llm.jsonl`) inside the run window, so they include every
sub-agent call. `agents` is the number of distinct `agent_path` labels.

| arm | run | seconds | tokens | model calls | agents | passed |
|---|---:|---:|---:|---:|---:|---|
| serial | 1 | 635.6 | 7,337,707 | 90 | 1 | yes |
| serial | 2 | 773.5 | 6,363,296 | 84 | 1 | yes |
| parallel | 1 | 792.0 | 6,193,073 | 153 | 4 | yes |
| parallel | 2 | 741.9 | 6,974,806 | 176 | 4 | yes |

- median wall clock: **704.6s serial vs 767.0s parallel — 0.92×**
- pass rate: **100% vs 100%**
- token spend: essentially equal (≈6.6–6.9M per run)
- model calls: parallel makes ~1.9× as many, because each child runs its own loop

The first parallel run was labelled invalid by the harness's old default of five
required spawns; it spawned three, as instructed. Use `--min-spawns 3` for this
workload. Both runs are valid on the merits.

## Verdict

**Still no evidence to build graph-level orchestration.** Delegation overhead is
mostly fixed cost: it dominates short tasks (0.31× on the five-task fixture) and
amortises on long ones, but at three agents on overlapping files it lands ~8%
slower, not faster.

## Limits

- n=2 per arm. Directional, not decision-grade; a third repetition is cheap
  (~25 minutes) if the margin matters.
- Same model for parent and children, one machine, no network contention.
- The three features overlap heavily in `control.py`; disjoint work is the case
  where this could flip, and it has not been measured.
- Wall clock includes the agent's own verification loop, which both arms pay.
