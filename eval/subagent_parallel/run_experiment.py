"""Decision-gate experiment: one agent doing five tasks vs five delegated ones.

The question this answers is deliberately narrow: does the dependency graph pay
for itself? Both arms do the same work on the same fixture with the same model;
only the delegation differs. The script refuses to score a parallel run that did
not actually spawn the five sub-agents.

Read-only w.r.t. the repository: every run happens in a throwaway copy under
``eval/subagent_parallel/.runs/``. It never touches the working tree, and it
never prints the API key.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import statistics
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
FIXTURE = HERE / "fixture"
RUNS = HERE / ".runs"
PY = ROOT / ".venv" / "Scripts" / "python.exe"
DEEPCODE = ROOT / "deepcode.py"
AUTH = Path.home() / ".local" / "share" / "opencode" / "auth.json"
# The process-wide LLM ledger. Unlike the per-session event stream, it records
# every call 鈥?including the ones made by sub-agents 鈥?with a timestamp and an
# agent_path, so it is the only place where both arms can be compared on cost.
LEDGER = Path.home() / ".deepcode" / "logs" / "llm.jsonl"

SERIAL_PROMPT = (
    "Open TASKS.md and implement all five tasks yourself, in this repository. "
    "Do not edit anything under tests/. Run `python -m pytest -q` until the "
    "acceptance suite passes, then report what you changed."
)
PARALLEL_PROMPT = (
    "Open TASKS.md. Delegate all five tasks to sub-agents; do not edit project "
    "files yourself. For each task N call "
    'spawn_agent(name="task-N", task="Implement task N from TASKS.md. Read the '
    'file first. Do not edit tests/.", isolate=true). After all five are '
    "spawned, call wait_agent repeatedly until every sub-agent reports. Then run "
    "`python -m pytest -q` and report the result."
)
ARMS = {"serial": SERIAL_PROMPT, "parallel": PARALLEL_PROMPT}


def _api_key(auth_path: Path) -> str:
    if not auth_path.exists():
        raise SystemExit(f"no auth file at {auth_path}; pass --auth-json")
    data = json.loads(auth_path.read_text(encoding="utf-8"))
    try:
        return str(data["deepseek"]["key"])
    except KeyError as exc:  # pragma: no cover - configuration error
        raise SystemExit("auth.json has no deepseek key") from exc


def _ledger_usage(started: datetime, ended: datetime) -> dict:
    """Sum every LLM call in the run window, children included.

    The window is padded on both sides for flush delay. Run the experiment on
    an otherwise idle machine, or unrelated sessions will be counted too.
    """

    total = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "cached_tokens": 0}
    by_agent: dict[str, dict] = {}
    if not LEDGER.exists():
        return {"usage": total, "by_agent_path": by_agent, "available": False}
    low = started - timedelta(seconds=2)
    high = ended + timedelta(seconds=15)
    with LEDGER.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                record = json.loads(line)
                when = datetime.fromisoformat(str(record.get("timestamp")))
            except (json.JSONDecodeError, ValueError):
                continue
            if not low <= when <= high:
                continue
            agent = str(record.get("agent_path") or "parent")
            bucket = by_agent.setdefault(
                agent, {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0}
            )
            for key in ("prompt_tokens", "completion_tokens", "cached_tokens"):
                value = int(record.get(key) or 0)
                total[key] += value
                if key in bucket:
                    bucket[key] += value
            total["calls"] += 1
            bucket["calls"] += 1
    return {"usage": total, "by_agent_path": by_agent, "available": True}


def _tests_digest(workspace: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted((workspace / "tests").rglob("*.py")):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _prepare(destination: Path, fixture: Path = FIXTURE) -> Path:
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(
        fixture,
        destination,
        ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"),
    )
    def run(*args: str) -> None:
        subprocess.run(
            ["git", *args], cwd=destination, check=True, capture_output=True
        )
    run("init", "-q")
    run("add", "-A")
    run(
        "-c",
        "user.name=experiment",
        "-c",
        "user.email=experiment@local",
        "commit",
        "-qm",
        "fixture",
    )
    return destination


def _pytest(workspace: Path, targets: list[str] | None = None) -> tuple[int, str]:
    result = subprocess.run(
        [
            str(PY),
            "-m",
            "pytest",
            "-q",
            "--continue-on-collection-errors",
            *(targets or []),
        ],
        cwd=workspace,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
        check=False,
    )
    tail = (result.stdout or "").strip().splitlines()
    return result.returncode, tail[-1] if tail else ""


def _summarize(ndjson: str) -> dict:
    usage = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "cached_tokens": 0}
    tools: dict[str, int] = {}
    stop_reason = None
    merge_reports = 0
    for line in ndjson.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        message = event.get("msg") or {}
        kind = message.get("type")
        if kind == "model_usage_recorded":
            payload = message.get("usage") or {}
            usage["calls"] += 1
            for key in ("prompt_tokens", "completion_tokens", "cached_tokens"):
                usage[key] += int(payload.get(key) or 0)
        if kind == "tool_completed":
            name = str(message.get("name") or "?")
            tools[name] = tools.get(name, 0) + 1
        if kind == "task_complete":
            stop_reason = message.get("stop_reason")
        # Only completed messages/tool results: reasoning traces quote the same
        # text and would inflate the count.
        if (
            kind in ("tool_completed", "agent_message_completed")
            and "NOT merged" in json.dumps(message)
        ):
            merge_reports += 1
    return {
        "usage": usage,
        "tools": tools,
        "spawn_calls": tools.get("spawn_agent", 0),
        "merge_reports": merge_reports,
        "stop_reason": stop_reason,
    }


def _run_once(arm: str, rep: int, *, model: str, connection: str, timeout: int,
              env_key: str, key: str, out: Path, prompt: str,
              fixture: Path, acceptance: list[str], min_spawns: int) -> dict:
    workspace = _prepare(out / f"{arm}-{rep}" / "workspace", fixture)
    before = _tests_digest(workspace)
    baseline_rc, baseline_tail = _pytest(workspace, acceptance)

    env = dict(os.environ)
    env[env_key] = key
    # Make ``python``/``pytest`` inside the agent's shell resolve to the venv
    # that actually has the test dependencies.
    env["VIRTUAL_ENV"] = str(PY.parent.parent)
    env["PATH"] = os.pathsep.join([str(PY.parent), env.get("PATH", "")])
    command = [
        str(PY),
        str(DEEPCODE),
        "exec",
        prompt,
        "--workspace",
        str(workspace),
        "--connection",
        connection,
        "--model",
        model,
        "--access",
        "full-access",
        "--trust",
        "--json",
    ]
    started = time.monotonic()
    started_utc = datetime.now(UTC)
    timed_out = False
    try:
        result = subprocess.run(
            command,
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=timeout,
            check=False,
        )
        output, returncode = result.stdout or "", result.returncode
        error_output = result.stderr or ""
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        output = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
        error_output = (exc.stderr or "") if isinstance(exc.stderr, str) else ""
        returncode = -1
    seconds = round(time.monotonic() - started, 1)
    ended_utc = datetime.now(UTC)
    ledger = _ledger_usage(started_utc, ended_utc)

    after = _tests_digest(workspace)
    pytest_rc, pytest_tail = _pytest(workspace, acceptance)
    summary = _summarize(output)
    record = {
        "arm": arm,
        "rep": rep,
        "seconds": seconds,
        "started_at": started_utc.isoformat(),
        "ended_at": ended_utc.isoformat(),
        "ledger": ledger,
        "returncode": returncode,
        "timed_out": timed_out,
        "baseline_pytest_rc": baseline_rc,
        "baseline_pytest_tail": baseline_tail,
        "pytest_rc": pytest_rc,
        "pytest_tail": pytest_tail,
        "passed": pytest_rc == 0 and after == before,
        "tests_modified": after != before,
        **summary,
        "workspace": str(workspace),
    }
    if error_output.strip():
        record["stderr_tail"] = error_output.strip().splitlines()[-1][:500]
    if arm == "parallel" and summary["spawn_calls"] < min_spawns:
        record["valid"] = False
        record["invalid_reason"] = (
            f"only {summary['spawn_calls']} spawn_agent calls; the model did not "
            "delegate as instructed"
        )
    else:
        record["valid"] = True
    (out / f"{arm}-{rep}" / "events.ndjson").write_text(output, encoding="utf-8")
    (out / f"{arm}-{rep}" / "stderr.log").write_text(error_output, encoding="utf-8")
    return record


def _median(records: list[dict], field: str) -> float | None:
    values = [r[field] for r in records if r.get(field) is not None]
    return round(statistics.median(values), 1) if values else None


def _total_tokens(record: dict) -> int:
    """Ledger total when available (children included), else the event stream."""
    ledger = (record.get("ledger") or {}).get("usage") or {}
    if ledger.get("calls"):
        return int(ledger.get("prompt_tokens", 0)) + int(
            ledger.get("completion_tokens", 0)
        )
    usage = record.get("usage") or {}
    return int(usage.get("prompt_tokens", 0)) + int(usage.get("completion_tokens", 0))


def _report(records: list[dict]) -> str:
    lines = [
        "# Serial vs parallel sub-agents",
        "",
        "Tokens come from the process LLM ledger (sub-agents included).",
        "",
        "| arm | rep | seconds | tokens | ledger calls | spawn | merge reports | passed | valid |",
        "|---|---:|---:|---:|---:|---:|---:|---|---|",
    ]
    for r in records:
        ledger = (r.get("ledger") or {}).get("usage") or {}
        lines.append(
            f"| {r['arm']} | {r['rep']} | {r['seconds']} | "
            f"{_total_tokens(r)} | {ledger.get('calls', '-')} | "
            f"{r['spawn_calls']} | {r.get('merge_reports', '-')} | "
            f"{'yes' if r['passed'] else 'no'} | "
            f"{'yes' if r['valid'] else 'NO: ' + r['invalid_reason']} |"
        )
    valid = [r for r in records if r["valid"]]
    serial = [r for r in valid if r["arm"] == "serial"]
    parallel = [r for r in valid if r["arm"] == "parallel"]
    lines.append("")
    if serial and parallel:
        s_seconds = _median(serial, "seconds")
        p_seconds = _median(parallel, "seconds")
        speedup = round(s_seconds / p_seconds, 2) if p_seconds else None
        s_pass = sum(1 for r in serial if r["passed"]) / len(serial)
        p_pass = sum(1 for r in parallel if r["passed"]) / len(parallel)
        lines += [
            f"- median seconds: serial {s_seconds}s, parallel {p_seconds}s",
            f"- speedup: {speedup}x (gate: >= 1.30x)",
            (
                f"- pass rate: serial {s_pass:.0%}, parallel {p_pass:.0%} "
                f"(gate: parallel not worse than serial by > 10pp)"
            ),
            "",
        ]
        pays = speedup is not None and speedup >= 1.30 and p_pass >= s_pass - 0.10
        lines.append(
            "**Verdict: the graph pays for itself 鈥?proceed.**"
            if pays
            else "**Verdict: stop at category routing + post-merge verification.**"
        )
    else:
        lines.append("Not enough valid runs to decide.")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arms", default="serial,parallel")
    parser.add_argument("--reps", type=int, default=1)
    parser.add_argument("--model", default="deepseek-flash")
    parser.add_argument("--connection", default="exp-ds")
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--auth-json", type=Path, default=AUTH)
    parser.add_argument("--env-key", default="DEEPCODE_EXP_KEY")
    parser.add_argument("--out", type=Path, default=RUNS)
    parser.add_argument(
        "--fixture",
        type=Path,
        default=FIXTURE,
        help="source tree copied per run (default: the small fixture)",
    )
    parser.add_argument(
        "--acceptance",
        default="",
        help="space-separated pytest targets; empty runs the whole suite",
    )
    parser.add_argument(
        "--prompts",
        type=Path,
        default=None,
        help="JSON file overriding the per-arm prompts",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--min-spawns",
        type=int,
        default=5,
        help="minimum spawn_agent calls for a parallel run to be scored "
        "(5 for the five-task fixture, 3 for the self-replay)",
    )
    args = parser.parse_args()

    if not PY.exists():
        raise SystemExit(f"missing venv python at {PY}")
    arms = [a.strip() for a in args.arms.split(",") if a.strip()]
    unknown = [a for a in arms if a not in ARMS]
    if unknown:
        raise SystemExit(f"unknown arm(s): {unknown}")

    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = args.out / stamp
    out.mkdir(parents=True, exist_ok=True)

    prompts = dict(ARMS)
    if args.prompts is not None:
        prompts.update(
            json.loads(args.prompts.read_text(encoding="utf-8"))
        )
    acceptance = [part for part in args.acceptance.split() if part]

    if args.dry_run:
        workspace = _prepare(out / "dry-run" / "workspace", args.fixture)
        rc, tail = _pytest(workspace, acceptance)
        print(f"workspace: {workspace}")
        print(f"baseline pytest rc={rc}: {tail}")
        print("dry run only; no model calls")
        return 0

    key = _api_key(args.auth_json)
    records: list[dict] = []
    for arm in arms:
        for rep in range(1, args.reps + 1):
            print(f"[{arm} rep {rep}] running...", flush=True)
            record = _run_once(
                arm,
                rep,
                model=args.model,
                connection=args.connection,
                timeout=args.timeout,
                env_key=args.env_key,
                key=key,
                out=out,
                prompt=prompts[arm],
                fixture=args.fixture,
                acceptance=acceptance,
                min_spawns=args.min_spawns,
            )
            records.append(record)
            print(
                f"[{arm} rep {rep}] {record['seconds']}s "
                f"tokens={_total_tokens(record)} "
                f"spawn={record['spawn_calls']} "
                f"merge_reports={record.get('merge_reports', '-')} "
                f"passed={record['passed']} valid={record['valid']}",
                flush=True,
            )

    (out / "report.json").write_text(
        json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    report = _report(records)
    (out / "report.md").write_text(report, encoding="utf-8")
    print()
    print(report)
    print(f"artifacts: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

