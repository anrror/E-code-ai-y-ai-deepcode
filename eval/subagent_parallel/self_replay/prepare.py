"""Build the replay base for the self-experiment.

The workload is the work this repository actually did: three features on the
sub-agent kernel, specified by the tests that were added with them. The base
keeps HEAD's tests and documentation, but reverts the eight implementation
files to the commit before the features, so the acceptance suite fails and the
task is real.

The base is generated, not committed: it is a full source tree and does not
belong in history.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
BASELINE = "aa6ed16"

# Exactly the files the feature commits touched. Reverting these restores the
# pre-feature kernel while leaving the acceptance tests in place.
SOURCE_FILES = (
    "core/agent_setup.py",
    "core/config.py",
    "core/harness/agents/control.py",
    "core/harness/tools/spawn_agent.py",
    "core/observability/bus.py",
    "core/observability/context.py",
    "core/observability/llmops.py",
    "core/observability/records.py",
)

TASKS = """# Tasks

Implement three features on the sub-agent kernel. The tests in `tests/` are the
contract; do not edit them. Everything currently fails because the features are
absent.

1. **Per-category model routing**
   - `agents.categories` maps a name to a complete routing target.
   - `spawn_agent(category=...)` builds the child with that target's model and
     connection; an unknown category is an error that lists the valid names.
   - Explicit `model` / `connection_id` override the category; the omitted half
     inherits the parent profile, not `agents.implementation`.
   - The parent's `ExecutionProfile` must never be mutated.
   - See `tests/test_subagent_model_routing.py` and
     `tests/test_routing_categories.py`.

2. **Dependency ordering**
   - `spawn_agent(depends_on=[...])` queues the child until every dependency has
     a result; queued work does not consume the fan-out limit.
   - A failed, cancelled, or unclean-merge dependency blocks its dependents and
     reports which one broke it.
   - Turn teardown cancels queued children; `send_message` refuses to wake a
     child that still has waiting dependents; the queue is bounded.
   - Retrying a failed task re-points its blocked dependents at the new attempt.
   - See `tests/test_subagent_dependencies.py`.

3. **Per-agent cost attribution**
   - Every LLM call made by a sub-agent carries an `agent_path` label, and the
     cost summary groups by it (`by_agent_path`).
   - See `tests/test_agent_path_attribution.py`.

Acceptance:

```powershell
python -m pytest -q tests/test_subagent_model_routing.py tests/test_subagent_dependencies.py tests/test_agent_path_attribution.py tests/test_spawn_agent.py
```
"""


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=ROOT, check=True, capture_output=True
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    out = args.out.resolve()
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    archive = out.parent / f"{out.name}.tar"
    with archive.open("wb") as handle:
        subprocess.run(["git", "archive", "HEAD"], cwd=ROOT, stdout=handle, check=True)
    subprocess.run(["tar", "-xf", str(archive), "-C", str(out)], check=True)
    archive.unlink()

    for rel in ("desktop", "eval/subagent_parallel", ".venv", ".omo"):
        target = out / rel
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)

    for rel in SOURCE_FILES:
        data = _git("show", f"{BASELINE}:{rel}").stdout
        (out / rel).write_bytes(data)

    (out / "TASKS.md").write_text(TASKS, encoding="utf-8")
    print(f"replay base: {out}")
    print(f"reverted {len(SOURCE_FILES)} files to {BASELINE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
