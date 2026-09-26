"""Run the agent A/B eval: every task, with and without ripple, N times each.

    python -m eval.run                          10 tasks x 2 modes x 3 repeats
    python -m eval.run --tasks 01-shop-tax-args --repeats 1 --out /tmp/smoke.json

Each run copies the task's fixture to a fresh directory under /tmp, runs
Claude Code headless there, then runs the check (eval/check.py). The two
modes differ in one thing: with-ripple gives the agent a ripple MCP server
for that directory, without gives it no MCP servers at all. Prompt, model,
tools and permissions are otherwise identical.

This calls the claude CLI on whatever account it is logged in to, so it
never runs in CI. The tests cover the parts that do not call it.
"""

from __future__ import annotations

import argparse
import datetime as dt
import difflib
import filecmp
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .check import check_task
from .loader import EVAL_DIR, REPO_ROOT, Task, copy_fixture, load_tasks
from .report import MODES, summarize, table

RIPPLE_BIN = Path(sys.executable).parent / "ripple"
RESULTS_DIR = EVAL_DIR / "results"
# The agent may edit files (acceptEdits) and run the tests; anything else that
# would need permission is refused, since nobody is there to answer.
TEST_COMMANDS = ["Bash(python -m pytest:*)", "Bash(python3 -m pytest:*)", "Bash(pytest:*)"]
RATE_LIMITED = re.compile(r"rate.?limit|usage limit|limit reached|quota|\b429\b|overloaded", re.I)


def mcp_config(mode: str, repo: Path, cache_dir: Path) -> dict:
    if mode == "without":
        return {"mcpServers": {}}
    return {
        "mcpServers": {
            "ripple": {
                "command": str(RIPPLE_BIN),
                "args": ["serve", "--repo", str(repo)],
                "env": {"RIPPLE_CACHE_DIR": str(cache_dir)},
            }
        }
    }


def allowed_tools(mode: str) -> list[str]:
    return TEST_COMMANDS + (["mcp__ripple"] if mode == "with-ripple" else [])


def agent_command(prompt: str, mode: str, mcp_file: Path, model: str) -> list[str]:
    """The claude invocation. --setting-sources project,local leaves out the
    user's own settings, and with them their hooks and plugins; the temp
    directory has no project settings, so the agent runs on defaults."""
    return [
        "claude", "-p", prompt,
        "--output-format", "stream-json", "--verbose",
        "--setting-sources", "project,local",
        "--no-session-persistence",
        "--permission-mode", "acceptEdits",
        "--allowedTools", ",".join(allowed_tools(mode)),
        "--mcp-config", str(mcp_file), "--strict-mcp-config",
        "--model", model,
    ]  # fmt: skip


def child_env(base: dict, bin_dir: Path, cache_dir: Path) -> dict:
    """The agent's environment: ours, minus anything that ties it to this
    session (CLAUDE*), minus an API key so it can only use the CLI login,
    with python and pytest on PATH."""
    env = {k: v for k, v in base.items() if not k.startswith("CLAUDE") and k != "ANTHROPIC_API_KEY"}
    env["PATH"] = f"{bin_dir}{os.pathsep}{base.get('PATH', '')}"
    env["RIPPLE_CACHE_DIR"] = str(cache_dir)
    return env


def write_python_shims(bin_dir: Path, python: str = sys.executable) -> None:
    """python, python3 and pytest for the agent, from an environment that has
    pytest. Shell scripts rather than symlinks, so the venv is still found."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    for name, target in (("python", python), ("python3", python), ("pytest", f"{python} -m pytest")):
        shim = bin_dir / name
        shim.write_text(f'#!/bin/sh\nexec {target} "$@"\n')
        shim.chmod(0o755)


def parse_stream(text: str) -> dict:
    """Pick the numbers out of claude's stream-json output: the init event
    (model, which MCP servers connected, which tools it had), every tool call,
    and the final result event (turns, tokens, cost, errors)."""
    tools: Counter = Counter()
    init, result = {}, None
    for line in text.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("type") == "system" and event.get("subtype") == "init":
            init = event
        elif event.get("type") == "assistant":
            for block in (event.get("message") or {}).get("content") or []:
                if block.get("type") == "tool_use":
                    tools[block.get("name", "?")] += 1
        elif event.get("type") == "result":
            result = event
    parsed = {
        "model": init.get("model"),
        "mcp_servers": init.get("mcp_servers", []),
        "mcp_tools": sorted(t for t in init.get("tools", []) if t.startswith("mcp__")),
        "tool_calls": dict(tools),
        "ripple_calls": sum(n for name, n in tools.items() if name.startswith("mcp__ripple__")),
        "got_result": result is not None,
    }
    if result:
        parsed.update(
            {
                "is_error": bool(result.get("is_error")),
                "result_subtype": result.get("subtype"),
                "num_turns": result.get("num_turns"),
                "agent_duration_s": round((result.get("duration_ms") or 0) / 1000, 1),
                "cost_usd": result.get("total_cost_usd"),
                "usage": {
                    k: (result.get("usage") or {}).get(k)
                    for k in ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
                },
                "permission_denials": len(result.get("permission_denials") or []),
                "final_message": str(result.get("result", ""))[:500],
            }
        )
    return parsed


def is_rate_limited(parsed: dict, stderr: str) -> bool:
    if parsed.get("got_result") and not parsed.get("is_error"):
        return False
    return bool(RATE_LIMITED.search(parsed.get("final_message", "") + "\n" + stderr))


def py_files(root: Path) -> dict[str, Path]:
    return {
        str(p.relative_to(root)): p
        for p in root.rglob("*.py")
        if not any(part.startswith(".") or part == "__pycache__" for part in p.relative_to(root).parts)
    }


def diff_trees(before: Path, after: Path) -> tuple[list[str], str]:
    """Which .py files the agent changed, and the unified diff."""
    old, new = py_files(before), py_files(after)
    changed, chunks = [], []
    for rel in sorted(set(old) | set(new)):
        if rel in old and rel in new and filecmp.cmp(old[rel], new[rel], shallow=False):
            continue
        changed.append(rel)
        a = old[rel].read_text().splitlines(keepends=True) if rel in old else []
        b = new[rel].read_text().splitlines(keepends=True) if rel in new else []
        chunks.append("".join(difflib.unified_diff(a, b, f"a/{rel}", f"b/{rel}")))
    return changed, "".join(chunks)


def run_one(task: Task, mode: str, repeat: int, root: Path, bin_dir: Path, args) -> dict:
    run_dir = root / f"{task.id}--{mode}--{repeat}"
    repo = copy_fixture(task, run_dir / "repo")
    cache_dir = run_dir / "cache"
    mcp_file = run_dir / "mcp.json"
    mcp_file.write_text(json.dumps(mcp_config(mode, repo, cache_dir)))
    stdout_path, stderr_path = run_dir / "stream.jsonl", run_dir / "stderr.txt"
    started = time.monotonic()
    with open(stdout_path, "wb") as out, open(stderr_path, "wb") as err:
        proc = subprocess.Popen(
            agent_command(task.prompt, mode, mcp_file, args.model),
            cwd=repo,
            env=child_env(dict(os.environ), bin_dir, cache_dir),
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=err,
            start_new_session=True,  # its own process group, so a timeout kills the MCP server too
        )
        try:
            proc.wait(timeout=args.timeout)
            timed_out = False
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
            timed_out = True
    wall = round(time.monotonic() - started, 1)
    parsed = parse_stream(stdout_path.read_text(errors="replace"))
    stderr = stderr_path.read_text(errors="replace")[-2000:]
    record = {
        "task": task.id,
        "mode": mode,
        "repeat": repeat,
        "wall_s": wall,
        "timed_out": timed_out,
        "exit_code": proc.returncode,
        **parsed,
    }
    if is_rate_limited(parsed, stderr):
        return {**record, "aborted": True, "success": False, "stderr_tail": stderr}
    record["changed_files"], record["diff"] = diff_trees(task.fixture, repo)
    record.update(check_task(task, repo))
    if not parsed["got_result"]:
        record["stderr_tail"] = stderr
    return record


def git_head() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def save(out: Path, meta: dict, runs: list[dict]) -> None:
    """Results JSON (without the diffs), the diffs beside it, and the summary table."""
    ordered = sorted(runs, key=lambda r: (r["task"], r["mode"], r["repeat"]))
    diff_dir = out.with_name(out.stem + "-diffs")
    for r in ordered:
        if r.get("diff"):
            diff_dir.mkdir(parents=True, exist_ok=True)
            (diff_dir / f"{r['task']}--{r['mode']}--{r['repeat']}.diff").write_text(r["diff"])
    slim = [{k: v for k, v in r.items() if k != "diff"} for r in ordered]
    summary = summarize(slim)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"meta": meta, "summary": summary, "runs": slim}, indent=2) + "\n")
    out.with_suffix(".md").write_text(table(summary) + "\n")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m eval.run", description=__doc__.split("\n\n")[0])
    parser.add_argument("--tasks", help="comma-separated task ids (default: all)")
    parser.add_argument("--modes", default=",".join(MODES), help="comma-separated: with-ripple,without")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--jobs", type=int, default=1, help="runs in parallel")
    parser.add_argument("--timeout", type=float, default=600, help="seconds per agent run")
    parser.add_argument("--model", default="sonnet")
    parser.add_argument("--out", type=Path, help=f"default: {RESULTS_DIR.relative_to(REPO_ROOT)}/<date>.json")
    parser.add_argument("--keep", action="store_true", help="keep the temp directories")
    args = parser.parse_args(argv)

    tasks = load_tasks(only=args.tasks.split(",") if args.tasks else None)
    modes = args.modes.split(",")
    if any(m not in MODES for m in modes):
        parser.error(f"modes must be among {', '.join(MODES)}")
    out = args.out or RESULTS_DIR / f"{dt.date.today().isoformat()}.json"
    version = subprocess.run(["claude", "--version"], capture_output=True, text=True).stdout.strip()
    meta = {
        "started": dt.datetime.now().isoformat(timespec="seconds"),
        "ripple_commit": git_head(),
        "claude_version": version,
        "model_requested": args.model,
        "repeats": args.repeats,
        "timeout_s": args.timeout,
        "jobs": args.jobs,
        "command": agent_command("<task prompt>", "with-ripple", Path("<mcp.json>"), args.model),
    }
    # Repeat-major order, both modes of a task back to back, so a batch cut
    # short by a rate limit still holds matched pairs.
    plan = [(t, m, r) for r in range(1, args.repeats + 1) for t in tasks for m in modes]
    root = Path(tempfile.mkdtemp(prefix="ripple-eval-"))
    bin_dir = root / "bin"
    write_python_shims(bin_dir)
    runs: list[dict] = []
    lock, stop = threading.Lock(), threading.Event()

    def one(item):
        task, mode, repeat = item
        if stop.is_set():
            return
        record = run_one(task, mode, repeat, root, bin_dir, args)
        with lock:
            runs.append(record)
            if record.get("aborted"):
                stop.set()
            save(out, {**meta, "stopped_early": stop.is_set()}, runs)
            status = "ABORTED (rate limit)" if record.get("aborted") else ("ok" if record["success"] else "FAIL")
            print(
                f"[{len(runs)}/{len(plan)}] {task.id} {mode} #{repeat}: {status} "
                f"turns={record.get('num_turns')} ripple_calls={record.get('ripple_calls')} wall={record['wall_s']}s",
                flush=True,
            )

    try:
        with ThreadPoolExecutor(max_workers=args.jobs) as pool:
            list(pool.map(one, plan))
    finally:
        if not args.keep:
            shutil.rmtree(root, ignore_errors=True)
    meta.update({"finished": dt.datetime.now().isoformat(timespec="seconds"), "stopped_early": stop.is_set()})
    save(out, meta, runs)
    print()
    print(table(summarize(runs)))
    print(f"\nresults: {out}")
    if stop.is_set():
        print(f"stopped early: rate limited after {len(runs)} of {len(plan)} runs")
    return 0


if __name__ == "__main__":
    sys.exit(main())
