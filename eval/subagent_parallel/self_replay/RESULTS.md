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
targets. Turns are real work: 71–90 model calls per serial run.

## Results (n=3 per arm)

Model `deepseek-flash`; tokens are summed from the process LLM ledger
(`~/.deepcode/logs/llm.jsonl`) inside the run window, so they include every
sub-agent call. `agents` is the number of distinct `agent_path` labels.

| arm | run | seconds | tokens | model calls | agents | passed |
|---|---:|---:|---:|---:|---:|---|
| serial | 1 | 635.6 | 7,337,707 | 90 | 1 | yes |
| serial | 2 | 773.5 | 6,363,296 | 84 | 1 | yes |
| serial | 3 | 575.1 | 5,526,774 | 71 | 1 | yes |
| parallel | 1 | 792.0 | 6,193,073 | 153 | 4 | yes |
| parallel | 2 | 741.9 | 6,974,806 | 176 | 4 | yes |
| parallel | 3 | 1524.7 | 9,691,954 | 189 | 4 | yes |

- median wall clock: **635.6s serial vs 792.0s parallel — 0.80×**
- pass rate: **100% vs 100%**
- median tokens: **6.36M vs 6.97M** (+10%)
- model calls: parallel makes ~2.1× as many, because each child runs its own loop
- spread: serial 575–774s (1.3×), parallel 742–1525s (**2.1×**)

The first parallel run was labelled invalid by the harness's old default of five
required spawns; it spawned three, as instructed. Use `--min-spawns 3` for this
workload. All six runs are valid on the merits.

## Verdict

**No evidence to build graph-level orchestration.** Delegation overhead is mostly
fixed cost: it dominates short tasks (0.31× on the five-task fixture) and
amortises on long ones, but at three agents on overlapping files it is still
~20% slower, spends ~10% more tokens, and is twice as variable.

The variance matters as much as the median: a 742s run and a 1525s run of the
same workload means a user cannot predict when the delegated version will
finish. That is a product problem, not a benchmarking detail.

## Limits

- Same model for parent and children, one machine, no network contention.
- The three features overlap heavily in `control.py`; disjoint work is the case
  where this could flip, and it has not been measured.
- Wall clock includes the agent's own verification loop, which both arms pay.
- Three runs per arm is enough to see direction and spread, not enough for a
  tight confidence interval.
