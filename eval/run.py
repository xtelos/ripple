"""Run the agent A/B eval: every task, with and without ripple, N times each.

    python -m eval.run                          19 Python tasks x 2 modes x 3 repeats
    python -m eval.run --tasks 01-shop-tax-args --repeats 1 --out /tmp/smoke.json
    python -m eval.run --runtime node --dry     the TypeScript tasks' plan and readiness checks, no model call
    python -m eval.run --runtime node --forced-use --restrict-search --dry     the test arms (eval/arms.py), also off by default

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

from .arms import FORCED_USE_LINE, RESTRICTED_DISALLOWED, RESTRICTED_PREAMBLE, Arms, arms_of, task_prompt
from .check import check_task
from .loader import EVAL_DIR, REPO_ROOT, Task, copy_fixture, load_tasks
from .node_agent import (
    node_allowed_tools,
    node_files,
    node_preamble,
    node_problems,
    node_restricted_preamble,
    ripple_problems,
    write_node_shims,
)
from .report import MODES, summarize, table

RIPPLE_BIN = Path(sys.executable).parent / "ripple"
RESULTS_DIR = EVAL_DIR / "results"
RUNTIMES = ("python", "node", "all")
# The agent may edit files (acceptEdits) and run the tests; anything else that
# would need permission is refused, since nobody is there to answer.
TEST_COMMANDS = ["Bash(python -m pytest:*)", "Bash(python3 -m pytest:*)", "Bash(pytest:*)"]
# The CLI's refusals when the account or the API cannot serve a run. The first form it printed for a spent usage limit,
# "You've hit your 5-hour limit · resets 6:30pm (America/New_York)", matched none of the older patterns.
RATE_LIMITED = re.compile(
    r"rate.?limit|usage limit|limit reached|quota|\b429\b|overloaded|hit your .{0,60}limit|\bresets?\s+(?:at\s+|in\s+)?\d|credit balance",
    re.I,
)

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
# What a run's record keeps of its ripple tool calls (and ToolSearch queries): each result's first
# MAX_RESULT_HEAD characters, and at most MAX_RECORDED_BYTES of JSON for all of them together.
MAX_RESULT_HEAD, MAX_RECORDED_BYTES = 2000, 40_000
MAX_SHELL_COMMANDS, MAX_SHELL_CHARS = 60, 160  # the shell log of a --restrict-search run
SECRET = re.compile(
    r"sk-[A-Za-z0-9_-]{10,}|gh[pousr]_[A-Za-z0-9]{6,}|AKIA[0-9A-Z]{12,}|"
    r"((?:API|ACCESS|AUTH|SECRET)[_-]?KEY|TOKEN|SECRET|PASSWORD|PASSWD)\w*=\S+",
    re.I,
)


def preamble_for(task: Task | None, arms: Arms | None = None) -> str:
    """The shared note on the shell: the node variant for a node task, the restricted variant (no tsc, search tools
    gone) when --restrict-search is on."""
    restricted = arms is not None and arms.restrict_search
    if task is not None and task.is_node:
        return node_restricted_preamble(task.spec) if restricted else node_preamble(task.spec)
    return RESTRICTED_PREAMBLE if restricted else PREAMBLE


def build_prompt(task_prompt: str, mode: str, task: Task | None = None, arms: Arms | None = None) -> str:
    """What the agent is told: the shared preamble (the node variant for a node task), in with-ripple the
    ripple line (and under --forced-use the sentence that tells it to call `callers`), then the task."""
    forced = arms is not None and arms.forced_use
    ripple = [WITH_RIPPLE_LINE] + ([FORCED_USE_LINE] if forced else []) if mode == "with-ripple" else []
    return "\n\n".join([preamble_for(task, arms), *ripple, task_prompt])


def ripple_launch(ripple_python: str | None = None) -> tuple[str, list[str]]:
    """The command and leading arguments that start the ripple MCP server. By default the `ripple` script next
    to this interpreter (what every Python run has always used); with --ripple-python, that interpreter's
    `-m ripple` (isolated, so a directory the agent works in cannot shadow the package). mcp_config passes it for a
    node task only: a Python task is always started by the default."""
    if ripple_python is None:
        return str(RIPPLE_BIN), []
    return ripple_python, ["-I", "-m", "ripple"]


def mcp_config(
    mode: str,
    repo: Path,
    cache_dir: Path,
    task: Task | None = None,
    ripple_python: str | None = None,
    bin_dir: Path | None = None,
) -> dict:
    if mode == "without":
        return {"mcpServers": {}}
    # --ripple-python is for the node tasks (a TypeScript index needs tree-sitter): a Python task's server never changes
    command, lead = ripple_launch(ripple_python if task is not None and task.is_node else None)
    entry = {
        "command": command,
        "args": [*lead, "serve", "--repo", str(repo)],
        "env": {"RIPPLE_CACHE_DIR": str(cache_dir)},
    }
    if task is not None and task.is_node:
        # A TypeScript repo has no default test command, so the `check` tool needs one: the runner shim, which
        # runs what the grader runs (and, started by this server, needs node on its PATH).
        runner = shlex.join([f"{bin_dir}/{task.spec.runner}", *(["run"] if task.spec.runner == "vitest" else [])])
        entry["args"] += ["--lang", "typescript", "--test-command", runner]
        entry["env"]["PATH"] = os.environ.get("PATH", "")
    return {"mcpServers": {"ripple": entry}}


def allowed_tools(mode: str, task: Task | None = None, arms: Arms | None = None) -> list[str]:
    restricted = arms is not None and arms.restrict_search
    shell = node_allowed_tools(task.spec, restricted) if task is not None and task.is_node else TEST_COMMANDS
    return shell + (["mcp__ripple"] if mode == "with-ripple" else [])


def agent_command(prompt: str, mode: str, mcp_file: Path, model: str, task: Task | None = None, arms: Arms | None = None) -> list[str]:
    """The claude invocation. --setting-sources project,local leaves out the
    user's own settings, and with them their hooks and plugins; the temp
    directory has no project settings, so the agent runs on defaults. Under
    --restrict-search a --disallowedTools list follows the allowed one."""
    denied = ["--disallowedTools", ",".join(RESTRICTED_DISALLOWED)] if arms is not None and arms.restrict_search else []
    return [
        "claude", "-p", prompt,
        "--output-format", "stream-json", "--verbose",
        "--setting-sources", "project,local",
        "--no-session-persistence",
        "--permission-mode", "acceptEdits",
        "--allowedTools", ",".join(allowed_tools(mode, task, arms)),
        *denied,
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


def result_text(content) -> str:
    """The text of a tool_result's content, which is a string or a list of content blocks (a text block's text; any
    other block as its JSON, so it still counts toward the size)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(b.get("text", "")) if isinstance(b, dict) and b.get("type") == "text" else b if isinstance(b, str) else json.dumps(b, default=str)
            for b in content
        )
    return "" if content is None else str(content)


def cap_records(records: list[tuple[str, dict]]) -> tuple[list[dict], list[dict], bool]:
    """The ripple exchanges and ToolSearch queries (in stream order) that fit MAX_RECORDED_BYTES of JSON together, and
    whether anything was left out. The first ones are kept: once one does not fit, it and every later one is dropped."""
    exchanges: list[dict] = []
    queries: list[dict] = []
    used = 0
    for kind, item in records:
        used += len(json.dumps(item)) + 2  # the ", " (or brackets) it costs in the record's JSON list
        if used > MAX_RECORDED_BYTES:
            return exchanges, queries, True
        (exchanges if kind == "exchange" else queries).append(item)
    return exchanges, queries, False


def parse_stream(text: str, shell_log: bool = False) -> dict:
    """Pick the numbers out of claude's stream-json output: the init event
    (model, which MCP servers connected, which tools it had), every tool call,
    and the final result event (turns, tokens, cost, errors). Besides the counts it
    records what the agent asked ripple and was answered (`ripple_exchanges`: the
    clipped input, and from the matching tool_result its error flag, size and first
    MAX_RESULT_HEAD characters; None for all three when the run ended before one came)
    and what it searched the deferred tools for (`toolsearch_queries`), together
    capped at MAX_RECORDED_BYTES (`ripple_exchanges_truncated` says it was cut). With
    shell_log (a --restrict-search run) the first MAX_SHELL_COMMANDS shell commands are
    listed too (`bash_commands`), so a search that got through the denied list shows."""
    tools: Counter = Counter()
    init, result = {}, None
    shell: list[str] = []
    records: list[tuple[str, dict]] = []
    waiting: dict[str, dict] = {}  # tool_use id -> its exchange, until the result arrives
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
                    name = block.get("name", "?")
                    tools[name] += 1
                    if name.startswith("mcp__ripple__"):
                        exchange = {"tool": name, "input": clip_input(block.get("input")), "is_error": None, "result_chars": None, "result_head": None}
                        records.append(("exchange", exchange))
                        if block.get("id") is not None:
                            waiting[str(block["id"])] = exchange
                    elif name == "ToolSearch":
                        records.append(("query", clip_input(block.get("input"))))
                    elif name == "Bash" and shell_log and len(shell) < MAX_SHELL_COMMANDS:
                        command = (block.get("input") or {}).get("command") if isinstance(block.get("input"), dict) else None
                        shell.append(clip_input(str(command), MAX_SHELL_CHARS))
        elif event.get("type") == "user":
            content = (event.get("message") or {}).get("content")
            for block in content if isinstance(content, list) else []:
                exchange = waiting.pop(str(block.get("tool_use_id")), None) if isinstance(block, dict) and block.get("type") == "tool_result" else None
                if exchange is not None:
                    answer = result_text(block.get("content"))
                    exchange.update(
                        {"is_error": bool(block.get("is_error")), "result_chars": len(answer), "result_head": SECRET.sub("[redacted]", answer[:MAX_RESULT_HEAD])}
                    )
        elif event.get("type") == "result":
            result = event
    exchanges, queries, cut = cap_records(records)
    parsed = {
        "got_init": bool(init),
        "model": init.get("model"),
        "mcp_servers": init.get("mcp_servers", []),
        "mcp_tools": sorted(t for t in init.get("tools", []) if t.startswith("mcp__")),
        "tool_calls": dict(tools),
        "ripple_calls": sum(n for name, n in tools.items() if name.startswith("mcp__ripple__")),
        "ripple_exchanges": exchanges,
        "toolsearch_queries": queries,
        "ripple_exchanges_truncated": cut,
        "got_result": result is not None,
    }
    if shell_log:
        parsed["bash_commands"] = shell
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


def limit_message(parsed: dict, stderr: str = "") -> str:
    """What the CLI said when it refused or cut off a run: the final message, else the last line of stderr."""
    return str(parsed.get("final_message") or "").strip() or (stderr.strip().splitlines() or ["no message"])[-1]


def py_files(root: Path) -> dict[str, Path]:
    return {
        str(p.relative_to(root)): p
        for p in root.rglob("*.py")
        if not any(part.startswith(".") or part == "__pycache__" for part in p.relative_to(root).parts)
    }


def diff_trees(before: Path, after: Path, node: bool = False) -> tuple[list[str], str]:
    """Which code files the agent changed, and the unified diff: .py files, or for a node task (node=True) the
    TypeScript, JavaScript, JSON and Markdown files (eval/node_agent.py node_files), never node_modules."""
    files = node_files if node else py_files
    old, new = files(before), files(after)
    changed, chunks = [], []
    for rel in sorted(set(old) | set(new)):
        if rel in old and rel in new and filecmp.cmp(old[rel], new[rel], shallow=False):
            continue
        changed.append(rel)
        errors = "replace" if node else "strict"
        a = old[rel].read_text(errors=errors).splitlines(keepends=True) if rel in old else []
        b = new[rel].read_text(errors=errors).splitlines(keepends=True) if rel in new else []
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


def did_not_run_reason(parsed: dict, stderr: str = "") -> str | None:
    """Why a run that ended in an error before the agent did anything says nothing about its mode, or None. A run
    whose result is an error, with no tool call and at most one turn, was refused by the CLI (a spent usage limit, an
    API error), not failed by the agent; what the message says does not matter, so one that matches no known pattern is
    caught too. A run that used a tool or took a second turn did run, and an error at the end of it is its own outcome."""
    if not parsed.get("is_error") or parsed.get("tool_calls") or (parsed.get("num_turns") or 0) > 1:
        return None
    return f"the agent did not run: {limit_message(parsed, stderr)[:120]}"


def invalid_reason(parsed: dict, mode: str) -> str | None:
    """Why a run cannot count for its mode, or None. A run the CLI refused before the agent did anything is invalid
    (did_not_run_reason). A with-ripple run whose
    init event shows the ripple MCP server anything but connected had no
    ripple tools, so it would be a second control run under the wrong label.
    A run with no init event at all is never invalid for the ripple server (it failed before either mode could
    differ, so the server check is skipped); it counts as a failure like any other unless it is also a run that did
    not run: an error result with no tool call and at most one turn, which is invalid whether or not it has an init."""
    idle = did_not_run_reason(parsed)
    if idle:
        return idle
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
    arms = arms_of(args)
    run_dir = new_run_dir(root)
    repo = copy_fixture(task, run_dir / "repo")
    if task.spec:
        # A real repository needs its own venv's python and its test settings; the shims are
        # per run because they name this run's copy (the repo is never installed).
        bin_dir = run_dir / "bin"
        if task.is_node:
            # the runner and tsc, nothing else (eval/node_agent.py); the runner only under --restrict-search
            write_node_shims(bin_dir, task.spec, repo, restricted=arms.restrict_search)
        else:
            write_python_shims(bin_dir, task.python, task.spec.env(repo))
    cache_dir = run_dir / "cache"
    mcp_file = run_dir / "mcp.json"
    ripple_python = getattr(args, "ripple_python", None)
    mcp_file.write_text(json.dumps(mcp_config(mode, repo, cache_dir, task, ripple_python, bin_dir)))
    stdout_path, stderr_path = run_dir / "stream.jsonl", run_dir / "stderr.txt"
    started = time.monotonic()
    with open(stdout_path, "wb") as out, open(stderr_path, "wb") as err:
        proc = subprocess.Popen(
            agent_command(build_prompt(task_prompt(task, arms), mode, task, arms), mode, mcp_file, args.model, task, arms),
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
    parsed = parse_stream(stdout_path.read_text(errors="replace"), shell_log=arms.restrict_search)
    stderr = stderr_path.read_text(errors="replace")[-2000:]
    record = {
        "task": task.id,
        "mode": mode,
        "repeat": repeat,
        "arm": arms.label,
        "run_dir": run_dir.name,
        "wall_s": wall,
        "timed_out": timed_out,
        "exit_code": proc.returncode,
        **parsed,
    }
    if is_rate_limited(parsed, stderr):
        idle = did_not_run_reason(parsed, stderr)
        if not idle:
            return {**record, "aborted": True, "success": False, "stderr_tail": stderr}
        # Refused before it began: invalid, never graded (nothing to grade), and the batch stops on `limit_hit`.
        return {**record, "changed_files": [], "success": False, "invalid": idle, "limit_hit": limit_message(parsed, stderr)}
    record["changed_files"], record["diff"] = diff_trees(task.fixture, repo, node=task.is_node)
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
    out.with_suffix(".md").write_text(table(summary, meta.get("arm")) + "\n")


def select_tasks(tasks: list[Task], runtime: str | None, named: bool) -> list[Task]:
    """The tasks a batch runs. Tasks named with --tasks are run as named, whatever their runtime (naming one is
    asking for it); otherwise --runtime picks, and its default python is the nineteen tasks every batch ran before
    the node tasks existed, so spending on a node task is always a choice."""
    if named:
        return tasks
    runtime = runtime or "python"
    return [t for t in tasks if runtime == "all" or t.runtime == runtime]


def ripple_entry(task: Task, repo: Path, cache_dir: Path, bin_dir: Path, ripple_python: str | None) -> dict:
    """The server entry a with-ripple run of this task gets, as the readiness check starts it."""
    return mcp_config("with-ripple", repo, cache_dir, task, ripple_python, bin_dir)["mcpServers"]["ripple"]


def readiness_problems(tasks: list[Task], modes: list[str], ripple_python: str | None) -> list[str]:
    """What stops a node task from running, found before any agent starts (none of it calls a model). Python tasks
    have no such checks: their environment is this interpreter and the cached venvs, which `available` covers."""
    node = [t for t in tasks if t.is_node]
    problems: list[str] = []
    for spec in {t.spec.name: t.spec for t in node}.values():
        problems += node_problems(spec)
    if node and "with-ripple" in modes:
        problems += ripple_problems(
            node,
            ripple_python or sys.executable,
            lambda task, repo, cache, bin_dir: ripple_entry(task, repo, cache, bin_dir, ripple_python),
            copy_fixture,
        )
    return problems


def describe_plan(tasks: list[Task], modes: list[str], args, problems: list[str]) -> str:
    """The --dry output: what a batch would do, as text. It calls nothing that costs."""
    arms = arms_of(args)
    runs = len(tasks) * len(modes) * args.repeats
    lines = [
        "dry run: no agent is started, no model is called, nothing is written",
        f"tasks ({len(tasks)}):",
        *(f"  {t.id}  [{t.runtime}" + (f", {t.spec.name}, {t.spec.runner}" if t.is_node else "") + "]" for t in tasks),
        f"modes: {', '.join(modes)}",
        f"runs: {len(tasks)} tasks x {len(modes)} modes x {args.repeats} repeats = {runs}",
        f"arm: {arms.label}",
    ]
    if arms.hard_prompts:
        lines.append(f"hard prompts: {sum(1 for t in tasks if t.hard_prompt)} of {len(tasks)} selected tasks have one (a task without one runs its normal prompt)")
    if "with-ripple" in modes:
        python_tasks = [t for t in tasks if not t.is_node]
        if python_tasks:
            command, lead = ripple_launch(None)
            lines.append(f"ripple server{' (Python tasks)' if len(python_tasks) < len(tasks) else ''}: {shlex.join([command, *lead, 'serve'])}")
        if len(python_tasks) < len(tasks):
            command, lead = ripple_launch(args.ripple_python)
            lines.append(
                f"ripple server{' (node tasks)' if python_tasks else ''}: {shlex.join([command, *lead, 'serve'])} "
                f"(the interpreter checked for node tasks: {args.ripple_python or sys.executable})"
            )
    seen = set()
    for task in tasks:
        key = (task.runtime, task.spec.runner if task.is_node else "")
        if key in seen:
            continue
        seen.add(key)
        label = f"{task.runtime}" + (f", {task.spec.runner}" if task.is_node else "")
        lines.append(f"[{label}] shell preamble: {build_prompt('<task prompt>', 'without', task, arms).split(chr(10) * 2)[0]}")
        for mode in modes:
            command = agent_command("<task prompt>", mode, Path("<mcp.json>"), args.model, task, arms)
            lines.append(f"[{label}] agent command, {mode}: {shlex.join(command)}")
            if arms.label != "base":
                # an armed batch spells out what the agent is told before the task and which tools it has
                before = build_prompt("<task prompt>", mode, task, arms).split(chr(10) * 2)[:-1]
                lines.append(f"[{label}] prompt before the task, {mode}:")
                lines += [f"    {paragraph}" for paragraph in before]
                lines.append(f"[{label}] allowed tools, {mode}: {','.join(allowed_tools(mode, task, arms))}")
                lines.append(f"[{label}] disallowed tools, {mode}: {','.join(RESTRICTED_DISALLOWED) if arms.restrict_search else '(none)'}")
            if mode == "with-ripple":
                entry = mcp_config(mode, Path("/path/to/repo"), Path("/path/to/cache"), task, args.ripple_python, Path("/path/to/bin"))
                entry = entry["mcpServers"]["ripple"]
                lines.append(
                    f"[{label}] ripple server config: {shlex.join([entry['command'], *entry['args']])} (env set: {', '.join(entry['env'])})"
                )
    node = [t for t in tasks if t.is_node]
    lines.append("readiness (node and the runner's binaries" + (", the ripple interpreter and a server handshake per repo" if node and "with-ripple" in modes else "") + "):")
    if not node:
        lines.append("  nothing to check: no node task is selected")
    elif problems:
        lines += [f"  PROBLEM {p}" for p in problems]
    else:
        lines.append("  ok")
    return "\n".join(lines)


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
    parser.add_argument(
        "--runtime",
        choices=RUNTIMES,
        help="which tasks to run: python (the default: tasks 01 to 19), node (the TypeScript tasks) or all. "
        "Ignored when --tasks names the tasks, which runs exactly those whatever their runtime",
    )
    parser.add_argument(
        "--ripple-python",
        help="the interpreter that runs the ripple server for the node tasks in the with-ripple arm (default: this one). "
        "A TypeScript task needs one that has ripple's typescript extra (tree-sitter); the run is refused before it "
        "starts if not. Python tasks never use it: they always start the ripple next to this interpreter",
    )
    parser.add_argument(
        "--forced-use",
        action="store_true",
        help="test arm: tell the with-ripple agent, in the prompt, to call ripple's `callers` tool on the symbol it is "
        f"changing before editing ({FORCED_USE_LINE!r}). The without arm is unchanged",
    )
    parser.add_argument(
        "--hard-prompts",
        action="store_true",
        help="test arm: run each task's `hard_prompt` (task.json) instead of its prompt: the same task with no file "
        "paths, no lists or counts of sites and no hint of where uses live. Only the TypeScript tasks 20 to 28 have one",
    )
    parser.add_argument(
        "--restrict-search",
        action="store_true",
        help="test arm: for both arms, deny the Grep and Glob tools, the web tools and the shell commands that search "
        "(grep, find, rg, ...), and for a node task leave tsc out of the shell (the runner only); the preamble says so. Not airtight: an agent can still search by writing a test file "
        "and running it with the runner; the record's bash_commands and changed files show that after the fact",
    )
    parser.add_argument(
        "--dry",
        action="store_true",
        help="print the plan (tasks, runs, the agent command, the readiness checks) and stop: no agent, no model call, nothing written",
    )
    args = parser.parse_args(argv)

    tasks = select_tasks(load_tasks(only=args.tasks.split(",") if args.tasks else None), args.runtime, bool(args.tasks))
    if args.hard_prompts:
        no_hard = [t.id for t in tasks if not t.hard_prompt and not t.is_node]
        if no_hard:
            shown = ", ".join(no_hard[:3]) + (f" and {len(no_hard) - 3} more" if len(no_hard) > 3 else "")
            parser.error(
                f"--hard-prompts: {shown} {'has' if len(no_hard) == 1 else 'have'} no hard prompt (hard prompts exist for "
                "tasks 20 to 28 only, the TypeScript tasks); drop --hard-prompts, or pick node tasks with --runtime node or --tasks"
            )
    missing = [t.id for t in tasks if not t.available]
    if missing:
        parser.error(f"these tasks need pinned repositories that are not fetched: {', '.join(missing)}\n"
                     "run `python -m eval.fetch`, or pick other tasks with --tasks")  # fmt: skip
    modes = args.modes.split(",")
    if any(m not in MODES for m in modes):
        parser.error(f"modes must be among {', '.join(MODES)}")
    if args.forced_use and "with-ripple" not in modes:
        parser.error("--forced-use changes the with-ripple arm only, and --modes does not include it: nothing would differ")
    args.arms = Arms(forced_use=args.forced_use, hard_prompts=args.hard_prompts, restrict_search=args.restrict_search)
    if args.out and any(p.exists() for p in result_paths(args.out)):
        parser.error(f"{args.out} (or its .md or -diffs) already exists; pick a new --out")
    no_node = not any(t.is_node for t in tasks)
    if args.ripple_python and no_node:
        print(f"--ripple-python {args.ripple_python} is ignored: no node task is selected (Python tasks always start this interpreter's ripple)")
    problems = readiness_problems(tasks, modes, args.ripple_python)
    if args.dry:
        print(describe_plan(tasks, modes, args, problems))
        return 1 if problems else 0
    if problems:
        parser.error("not ready, nothing was started:\n  " + "\n  ".join(problems))
    out = args.out or default_out(RESULTS_DIR)
    version = subprocess.run(["claude", "--version"], capture_output=True, text=True).stdout.strip()
    arms = args.arms
    meta = {
        "started": dt.datetime.now().isoformat(timespec="seconds"),
        "ripple_commit": git_head(),
        "claude_version": version,
        "model_requested": args.model,
        "repeats": args.repeats,
        "timeout_s": args.timeout,
        "jobs": args.jobs,
        "prompt_preamble": preamble_for(None, arms),
        "with_ripple_line": WITH_RIPPLE_LINE,
        "command": agent_command("<task prompt>", "with-ripple", Path("<mcp.json>"), args.model, None, arms),
        "arm": arms.label,
    }
    if arms.label != "base":
        # the arm, spelled out, so a results file says what its batch was given
        meta["arm_flags"] = arms.flags
        if arms.hard_prompts:
            meta["hard_prompt_tasks"] = [t.id for t in tasks if t.hard_prompt]
        if arms.forced_use:
            meta["forced_use_line"] = FORCED_USE_LINE
        if arms.restrict_search:
            meta["disallowed_tools"] = list(RESTRICTED_DISALLOWED)
    if any(t.is_node for t in tasks):
        # What differs for the node tasks, which have their own shell rules and note on it: recorded only for a
        # batch that has them, so a Python-only batch's meta is what it always was.
        node = [t for t in tasks if t.is_node]
        meta["node_prompt_preambles"] = {t.spec.runner: preamble_for(t, arms) for t in node}
        meta["node_commands"] = {
            t.spec.runner: agent_command("<task prompt>", "with-ripple", Path("<mcp.json>"), args.model, t, arms) for t in node
        }
        meta["ripple_python"] = args.ripple_python or sys.executable
    if args.ripple_python and no_node:
        # given and not used: the results file says which interpreter was named and that no run was started by it
        meta["ripple_python"] = args.ripple_python
        meta["ripple_python_ignored"] = True
    # Repeat-major order, both modes of a task back to back, so a batch cut
    # short by a rate limit still holds matched pairs.
    plan = [(t, m, r) for r in range(1, args.repeats + 1) for t in tasks for m in modes]
    root = make_root()
    bin_dir = root / "bin"
    write_python_shims(bin_dir)
    runs: list[dict] = []
    lock, stop = threading.Lock(), threading.Event()
    stopped_by: list[str] = []  # the message of the first run that hit a limit

    def one(item):
        task, mode, repeat = item
        if stop.is_set():
            return
        record = run_one(task, mode, repeat, root, bin_dir, args)
        with lock:
            runs.append(record)
            if record.get("aborted") or record.get("limit_hit"):
                stop.set()
                if not stopped_by:
                    stopped_by.append(record.get("limit_hit") or limit_message(record, record.get("stderr_tail") or ""))
                    meta["stopped_message"] = stopped_by[0]
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
    print(table(summarize(runs), meta.get("arm")))
    print(f"\nresults: {out}")
    invalid = Counter(r["invalid"] for r in runs if r.get("invalid") and not r.get("aborted"))
    if invalid:
        why = "; ".join(f"{n} x {reason}" for reason, n in sorted(invalid.items()))
        print(f"WARNING: {sum(invalid.values())} INVALID run(s) of {len(runs)} are not counted in any success rate: {why}")
    if stop.is_set():
        print(
            f"stopped early: a usage or rate limit was hit ({stopped_by[0]}) after {len(runs)} of {len(plan)} runs; "
            f"{len(plan) - len(runs)} not started. Resume after the limit resets, with a new --out."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
