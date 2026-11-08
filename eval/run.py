"""Run the agent A/B eval: every task, with and without ripple, N times each.

    python -m eval.run                          19 tasks x 2 modes x 3 repeats
    python -m eval.run --tasks 01-shop-tax-args --repeats 1 --out /tmp/smoke.json

Each run copies the task's fixture to a fresh directory under /tmp, runs
Claude Code headless there, then runs the check (eval/check.py). The two
modes differ in one thing: with-ripple gives the agent a ripple MCP server
for that directory, without gives it no MCP servers at all. Model, tools and
permissions are identical, and so is the prompt (a shared note on the shell
limit, then the task) apart from one line in with-ripple saying the server is
connected. A run that changed no file and had an edit tool (Edit, Write,
MultiEdit, NotebookEdit) refused is marked invalid and left out of the success
rate: the harness blocked the very tool the prompt named. Any other refusal
(a shell rewrite the prompt said not to try, a read-only command) is routine
and the run still counts, as a failure if it changed nothing.

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
import secrets
import shlex
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

# Told to the agent in both modes, ahead of the task. The first real-repo smoke run showed why: with
# only the test commands allowed in the shell, one agent tried sed -i, xargs sed and a python rewrite,
# was refused every time, never tried the Edit tool, and was graded a failure for it. That says nothing
# about ripple, so the limit is stated up front, in words that mention neither mode.
PREAMBLE = (
    "The shell is there for running the tests: do not use it to edit files (sed, scripted rewrites). "
    "Make every edit with the Edit or Write tool."
)
# The one difference between the two prompts: the sort of line a user's own CLAUDE.md would carry. It
# says the tools exist and what they cover, and not to use them, so how often the agent reaches for
# them (ripple_calls) stays its own choice and is still reported as it is.
WITH_RIPPLE_LINE = (
    "A ripple MCP server is connected for this repository. Its tools (impact, callers, callees, path, "
    "check) cover the call graph and running tests."
)
# The tools that write files. Refusals are routine (most runs of the first batch had one), so only a
# refused edit tool with nothing changed means the harness stopped the run: it blocked the very tool
# the prompt told the agent to use.
EDIT_TOOLS = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit"})
BLOCKED = "no file changed and an edit tool was refused by the permissions"
MAX_DENIALS_LISTED, MAX_INPUT_CHARS = 20, 300
SECRET = re.compile(
    r"sk-[A-Za-z0-9_-]{10,}|gh[pousr]_[A-Za-z0-9]{6,}|AKIA[0-9A-Z]{12,}|"
    r"((?:API|ACCESS|AUTH|SECRET)[_-]?KEY|TOKEN|SECRET|PASSWORD|PASSWD)\w*=\S+",
    re.I,
)


def build_prompt(task_prompt: str, mode: str) -> str:
    """What the agent is told: the shared preamble, in with-ripple the ripple line, then the task."""
    parts = [PREAMBLE] + ([WITH_RIPPLE_LINE] if mode == "with-ripple" else []) + [task_prompt]
    return "\n\n".join(parts)


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
    with python and pytest on PATH, and with auto-memory off. Auto-memory
    lives in the user's ~/.claude, outside --setting-sources, and a first
    smoke run showed it creating a memory directory there for each temp repo."""
    env = {k: v for k, v in base.items() if not k.startswith("CLAUDE") and k != "ANTHROPIC_API_KEY"}
    env["PATH"] = f"{bin_dir}{os.pathsep}{base.get('PATH', '')}"
    env["RIPPLE_CACHE_DIR"] = str(cache_dir)
    env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] = "1"
    return env


def write_python_shims(bin_dir: Path, python: str = sys.executable, env: dict[str, str] | None = None) -> None:
    """python, python3 and pytest for the agent, from an environment that has
    pytest. Shell scripts rather than symlinks, so the venv is still found.
    env is exported first: for a real-repo task it carries the variables the
    grader runs the tests with (RepoSpec.env), so the agent's runs match."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    exports = "".join(f"export {k}={shlex.quote(v)}\n" for k, v in (env or {}).items() if k != "PATH")
    for name, target in (("python", python), ("python3", python), ("pytest", f"{python} -m pytest")):
        shim = bin_dir / name
        shim.write_text(f'#!/bin/sh\n{exports}exec {target} "$@"\n')
        shim.chmod(0o755)


def clip_input(value, limit: int = MAX_INPUT_CHARS):
    """A denied tool's input, kept short and with anything that looks like a credential masked."""
    if isinstance(value, dict):
        return {str(k): clip_input(v, limit) for k, v in value.items()}
    if isinstance(value, list):
        return [clip_input(v, limit) for v in value[:10]]
    if isinstance(value, str):
        return SECRET.sub("[redacted]", value)[:limit]
    return value


def denied_tools(denials) -> list[dict]:
    """The tool calls the permissions refused, as the result event lists them: which tool and
    what it was asked to do, so a run that was blocked can be told from one that gave up."""
    return [
        {"tool": str(d.get("tool_name", "?")), "input": clip_input(d.get("tool_input"))}
        for d in denials[:MAX_DENIALS_LISTED]
        if isinstance(d, dict)
    ]


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
        "got_init": bool(init),
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
                "permission_denied": denied_tools(result.get("permission_denials") or []),
                # counted over every denial, not just the listed ones
                "edit_tools_denied": sum(
                    1 for d in result.get("permission_denials") or [] if isinstance(d, dict) and d.get("tool_name") in EDIT_TOOLS
                ),
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


def make_root() -> Path:
    """The batch's temp directory. Claude Code shows the agent its working
    directory, so no path it can see names ripple, the task or the mode."""
    return Path(tempfile.mkdtemp())


def new_run_dir(root: Path) -> Path:
    """A fresh, neutrally named directory for one run. The task, mode and
    repeat it belongs to live only in the run's record."""
    return Path(tempfile.mkdtemp(prefix="run-", dir=root))


def invalid_reason(parsed: dict, mode: str) -> str | None:
    """Why a run cannot count for its mode, or None. A with-ripple run whose
    init event shows the ripple MCP server anything but connected had no
    ripple tools, so it would be a second control run under the wrong label.
    A run with no init event at all is left alone: it failed before either
    mode could differ, and it counts as a failure like any other."""
    if mode != "with-ripple" or not parsed.get("got_init"):
        return None
    status = next((s.get("status") for s in parsed.get("mcp_servers") or [] if s.get("name") == "ripple"), None)
    if status == "connected":
        return None
    return f"ripple MCP server {status or 'missing'}"


def blocked_reason(parsed: dict, changed_files: list[str]) -> str | None:
    """Why a run that made no edit says nothing about its mode, or None. Only a run that changed
    no file and had an edit tool (Edit, Write, ...) refused was stopped by the harness. A refused
    shell rewrite the prompt said not to try, or a refused read-only command, is a routine refusal:
    with no edit made it is a real failure, and a run that edited was not stopped at all."""
    if changed_files or not parsed.get("edit_tools_denied"):
        return None
    return BLOCKED


def run_one(task: Task, mode: str, repeat: int, root: Path, bin_dir: Path, args) -> dict:
    run_dir = new_run_dir(root)
    repo = copy_fixture(task, run_dir / "repo")
    if task.spec:
        # A real repository needs its own venv's python and its test settings; the shims are
        # per run because they name this run's copy (the repo is never installed).
        bin_dir = run_dir / "bin"
        write_python_shims(bin_dir, task.python, task.spec.env(repo))
    cache_dir = run_dir / "cache"
    mcp_file = run_dir / "mcp.json"
    mcp_file.write_text(json.dumps(mcp_config(mode, repo, cache_dir)))
    stdout_path, stderr_path = run_dir / "stream.jsonl", run_dir / "stderr.txt"
    started = time.monotonic()
    with open(stdout_path, "wb") as out, open(stderr_path, "wb") as err:
        proc = subprocess.Popen(
            agent_command(build_prompt(task.prompt, mode), mode, mcp_file, args.model),
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
        "run_dir": run_dir.name,
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
    reason = invalid_reason(parsed, mode) or blocked_reason(parsed, record["changed_files"])
    if reason:
        record["invalid"] = reason
    return record


def git_head() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def result_paths(out: Path) -> list[Path]:
    """Everything one batch writes: the JSON, the table and the diffs dir."""
    return [out, out.with_suffix(".md"), diff_dir(out)]


def diff_dir(out: Path) -> Path:
    return out.with_name(out.stem + "-diffs")


def default_out(results_dir: Path, now: dt.datetime | None = None, make_id=None) -> Path:
    """<date>-<time>-<id>.json, with a short random id, and never a name any
    earlier batch has used, so a batch can never overwrite another's results
    or mix its diffs into another's diffs dir."""
    now = now or dt.datetime.now()
    make_id = make_id or (lambda: secrets.token_hex(3))
    while True:
        out = results_dir / f"{now:%Y-%m-%d-%H%M%S}-{make_id()}.json"
        if not any(p.exists() for p in result_paths(out)):
            return out


def save(out: Path, meta: dict, runs: list[dict]) -> None:
    """Results JSON (without the diffs), the diffs beside it, and the summary table."""
    ordered = sorted(runs, key=lambda r: (r["task"], r["mode"], r["repeat"]))
    diffs = diff_dir(out)
    for r in ordered:
        if r.get("diff"):
            diffs.mkdir(parents=True, exist_ok=True)
            (diffs / f"{r['task']}--{r['mode']}--{r['repeat']}.diff").write_text(r["diff"])
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
    parser.add_argument(
        "--out",
        type=Path,
        help=f"default: {RESULTS_DIR.relative_to(REPO_ROOT)}/<date>-<time>-<id>.json; never overwritten",
    )
    parser.add_argument("--keep", action="store_true", help="keep the temp directories")
    args = parser.parse_args(argv)

    tasks = load_tasks(only=args.tasks.split(",") if args.tasks else None)
    # The grader handles node tasks (eval/check_node.py) but the agent side does not yet: no node/vitest on the
    # agent's PATH, python shims written from task.python (None for them). Until it is wired, never start an agent on one.
    node_tasks = [t.id for t in tasks if t.is_node]
    if node_tasks and args.tasks:
        parser.error(f"the agent harness cannot run node tasks yet (grading is wired, the agent's environment is not): {', '.join(node_tasks)}")
    tasks = [t for t in tasks if not t.is_node]
    missing =[t.id for t in tasks if not t.available]
    if missing:
        parser.error(f"these tasks need pinned repositories that are not fetched: {', '.join(missing)}\n"
                     "run `python -m eval.fetch`, or pick other tasks with --tasks")  # fmt: skip
    modes = args.modes.split(",")
    if any(m not in MODES for m in modes):
        parser.error(f"modes must be among {', '.join(MODES)}")
    if args.out and any(p.exists() for p in result_paths(args.out)):
        parser.error(f"{args.out} (or its .md or -diffs) already exists; pick a new --out")
    out = args.out or default_out(RESULTS_DIR)
    version = subprocess.run(["claude", "--version"], capture_output=True, text=True).stdout.strip()
    meta = {
        "started": dt.datetime.now().isoformat(timespec="seconds"),
        "ripple_commit": git_head(),
        "claude_version": version,
        "model_requested": args.model,
        "repeats": args.repeats,
        "timeout_s": args.timeout,
        "jobs": args.jobs,
        "prompt_preamble": PREAMBLE,
        "with_ripple_line": WITH_RIPPLE_LINE,
        "command": agent_command("<task prompt>", "with-ripple", Path("<mcp.json>"), args.model),
    }
    # Repeat-major order, both modes of a task back to back, so a batch cut
    # short by a rate limit still holds matched pairs.
    plan = [(t, m, r) for r in range(1, args.repeats + 1) for t in tasks for m in modes]
    root = make_root()
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
            if record.get("aborted"):
                status = "ABORTED (rate limit)"
            elif record.get("invalid"):
                status = f"INVALID ({record['invalid']}), not counted"
            else:
                status = "ok" if record["success"] else "FAIL"
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
    invalid = Counter(r["invalid"] for r in runs if r.get("invalid") and not r.get("aborted"))
    if invalid:
        why = "; ".join(f"{n} x {reason}" for reason, n in sorted(invalid.items()))
        print(f"WARNING: {sum(invalid.values())} run(s) are invalid and not counted: {why}")
    if stop.is_set():
        print(f"stopped early: rate limited after {len(runs)} of {len(plan)} runs")
    return 0


if __name__ == "__main__":
    sys.exit(main())
