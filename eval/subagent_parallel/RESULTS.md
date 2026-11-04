# Gate result — 2026-11-04

Model: `deepseek-flash` (connection `exp-ds`), 3 repetitions per arm,
same fixture, same five tasks.

| arm | rep | seconds | tokens | model calls | spawn calls | passed |
|---|---:|---:|---:|---:|---:|---|
| serial | 1 | 109.5 | 171,628 | 14 | 0 | yes |
| serial | 2 | 92.2 | 144,238 | 13 | 0 | yes |
| serial | 3 | 97.1 | 190,538 | 17 | 0 | yes |
| parallel | 1 | 360.1 | 192,571 | 17 | 6 | yes |
| parallel | 2 | 268.6 | 177,597 | 16 | 6 | yes |
| parallel | 3 | 315.3 | 171,298 | 16 | 6 | yes |

- median wall clock: **97.1s serial vs 315.3s parallel — 0.31×**
- pass rate: **100% vs 100%**
- tokens: roughly equal (parallel spends its budget rebuilding five contexts
  instead of doing five tasks faster)

## Verdict

**Stop at category routing + post-merge verification.** Do not build
graph-level orchestration (DAG scheduling, plan UI, observer passes) on this
evidence.

The mechanism is not mysterious: five isolated children each rebuild a full
system prompt and tool context, then pay a merge step; on tasks this small the
overhead is 3× the work. Two of the three parallel runs also hit merge
conflicts in `app/cart.py`, which the parent then had to reconcile.

## What this does not say

- It does not say delegation is useless. It says delegation does not pay for
  **short, overlapping** subtasks.
- It does not test disjoint-file tasks, long-running tasks, or per-category
  cheap models for sub-agents. Those are the conditions under which the
  calculus can flip; rerun this gate on such a workload before changing course.
- The parallel arm completed the work despite conflicts, so the merge/blocked
  machinery works — it is a cost, not a defect.
