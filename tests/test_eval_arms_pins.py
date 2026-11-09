"""What a plain `python -m eval.run` (python runtime) and `--runtime node` build, pinned as literals taken from main at
7d57f88, before the test arms (--forced-use, --hard-prompts, --restrict-search) and the recording of ripple exchanges
existed. With no new flag the agent command, the prompts, the tool lists, the mcp config, the shims and the run record
must be what they were, apart from the keys NEW_KEYS names and the one `arm: base` line of the dry plan.

Nothing here calls a model. Tests over a node task skip without the fetched repository (`python -m eval.fetch`).
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import types
from pathlib import Path

import pytest

import eval.run as run_mod
from eval.loader import load_tasks
from eval.node_agent import node_preamble, runner_shim, tsc_shim, write_node_shims
from eval.run import RIPPLE_BIN, agent_command, allowed_tools, build_prompt, describe_plan, mcp_config, parse_stream

TASKS = {t.id: t for t in load_tasks()}
PY, VITEST, JEST = "01-shop-tax-args", "20-neverthrow-rename-resultasync-combine", "24-tsyringe-rename-transform"
NEEDS_CACHE = "needs the pinned repositories: python -m eval.fetch"
# What a run record, a parsed stream and the meta gain; every other key is pinned below.
NEW_KEYS = {"arm", "ripple_exchanges", "ripple_exchanges_truncated", "toolsearch_queries"}


def fetched(*ids):
    return [pytest.param(i, id=i, marks=pytest.mark.skipif(not TASKS[i].available, reason=NEEDS_CACHE)) for i in ids]


# sha256 of build_prompt(task.prompt, mode, task), captured from main: the whole prompt, the task text included
PROMPT_SHAS = {
    (PY, "without"): "2f896ab06e57da1d912f6783cfa4beb12fc65ef48044b0408dea80a623760a8b",
    (PY, "with-ripple"): "6bcfe6833a57819fdd7c490248f6599a0c03d98ccfcd108c80619f2b909d3d9b",
    (VITEST, "without"): "bb00d0f5eb428eeab07361697239f7d0662054e3149a748016dac85b371f6334",
    (VITEST, "with-ripple"): "3f4e86f2b1b58ced0c4718dd2e7c3cb3c0bf1e857bfc487e8b07facc0d308d00",
    (JEST, "without"): "5a0eb724883f102163a5ec6b9a5d1e3347ae6bc0ff17b88d177fc2b64b6d5325",
    (JEST, "with-ripple"): "94b1c6ac0a3ea6f1f80f09899580dbe94bee780c98915dbf7090a9dd1ad5b392",
}
TASK_PROMPT_SHAS = {
    PY: "9e4c0b5a2547a611cf19d67ebefc1b9d96b4ed937c2017d67c5341723ba7c009",
    VITEST: "fe61f25d2147304ad8813b6fb712054d3360bc6bcf30a39d66e4b397cc17342c",
    JEST: "141d1a1c304ae87cfd4b848268e1b7eb69d0559f482a5aac14d1b2cb6251b7bc",
}
SHIM_SHAS = {  # runner_shim and tsc_shim of a repo at /repo
    VITEST: ("cb6574d2f0c69647512ebceddbe9252ed72a1bc3259aaffc636d8ab05953facf", "bfea4e91c7b3e0cc546bf977e56ed5923089a1d0c1d0a9b6d27f6cc96fb9502c"),
    JEST: ("56d1baf19cae3f779ba39a28ec31e5a8ff4cd210e681b2865539d88d868179e8", "bfea4e91c7b3e0cc546bf977e56ed5923089a1d0c1d0a9b6d27f6cc96fb9502c"),
}
SHA = lambda text: hashlib.sha256(text.encode()).hexdigest()  # noqa: E731

PLAN_LINES = [
    "dry run: no agent is started, no model is called, nothing is written",
    "tasks (3):",
    "  01-shop-tax-args  [python]",
    "  20-neverthrow-rename-resultasync-combine  [node, neverthrow, vitest]",
    "  24-tsyringe-rename-transform  [node, tsyringe, jest]",
    "modes: with-ripple, without",
    "runs: 3 tasks x 2 modes x 3 repeats = 18",
    "ripple server (Python tasks): {RIPPLE_BIN} serve",
    "ripple server (node tasks): /py/bin/python -I -m ripple serve (the interpreter checked for node tasks: /py/bin/python)",
    "[python] shell preamble: The shell is there for running the tests: do not use it to edit files (sed, scripted rewrites). Make every edit with the Edit or Write tool.",
    "[python] agent command, with-ripple: claude -p '<task prompt>' --output-format stream-json --verbose --setting-sources project,local --no-session-persistence --permission-mode acceptEdits --allowedTools 'Bash(python -m pytest:*),Bash(python3 -m pytest:*),Bash(pytest:*),mcp__ripple' --mcp-config '<mcp.json>' --strict-mcp-config --model sonnet",
    "[python] ripple server config: {RIPPLE_BIN} serve --repo /path/to/repo (env set: RIPPLE_CACHE_DIR)",
    "[python] agent command, without: claude -p '<task prompt>' --output-format stream-json --verbose --setting-sources project,local --no-session-persistence --permission-mode acceptEdits --allowedTools 'Bash(python -m pytest:*),Bash(python3 -m pytest:*),Bash(pytest:*)' --mcp-config '<mcp.json>' --strict-mcp-config --model sonnet",
    "[node, vitest] shell preamble: The shell is there for running the tests (`vitest run`, optionally with file paths) and the type checker (`tsc --noEmit -p <tsconfig>`): do not use it to edit files (sed, scripted rewrites, node -e). Make every edit with the Edit or Write tool.",
    "[node, vitest] agent command, with-ripple: claude -p '<task prompt>' --output-format stream-json --verbose --setting-sources project,local --no-session-persistence --permission-mode acceptEdits --allowedTools 'Bash(vitest:*),Bash(tsc:*),mcp__ripple' --mcp-config '<mcp.json>' --strict-mcp-config --model sonnet",
    "[node, vitest] ripple server config: /py/bin/python -I -m ripple serve --repo /path/to/repo --lang typescript --test-command '/path/to/bin/vitest run' (env set: RIPPLE_CACHE_DIR, PATH)",
    "[node, vitest] agent command, without: claude -p '<task prompt>' --output-format stream-json --verbose --setting-sources project,local --no-session-persistence --permission-mode acceptEdits --allowedTools 'Bash(vitest:*),Bash(tsc:*)' --mcp-config '<mcp.json>' --strict-mcp-config --model sonnet",
    "[node, jest] shell preamble: The shell is there for running the tests (`jest`, optionally with file paths) and the type checker (`tsc --noEmit -p <tsconfig>`): do not use it to edit files (sed, scripted rewrites, node -e). Make every edit with the Edit or Write tool.",
    "[node, jest] agent command, with-ripple: claude -p '<task prompt>' --output-format stream-json --verbose --setting-sources project,local --no-session-persistence --permission-mode acceptEdits --allowedTools 'Bash(jest:*),Bash(tsc:*),mcp__ripple' --mcp-config '<mcp.json>' --strict-mcp-config --model sonnet",
    "[node, jest] ripple server config: /py/bin/python -I -m ripple serve --repo /path/to/repo --lang typescript --test-command /path/to/bin/jest (env set: RIPPLE_CACHE_DIR, PATH)",
    "[node, jest] agent command, without: claude -p '<task prompt>' --output-format stream-json --verbose --setting-sources project,local --no-session-persistence --permission-mode acceptEdits --allowedTools 'Bash(jest:*),Bash(tsc:*)' --mcp-config '<mcp.json>' --strict-mcp-config --model sonnet",
    "readiness (node and the runner's binaries, the ripple interpreter and a server handshake per repo):",
    "  ok",
]

PINNED_STREAM = "\n".join(
    json.dumps(e)
    for e in [
        {"type": "system", "subtype": "init", "model": "claude-x", "tools": ["Read", "mcp__ripple__impact"],
         "mcp_servers": [{"name": "ripple", "status": "connected"}]},
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "a", "name": "ToolSearch", "input": {"query": "ripple"}}]}},
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "b", "name": "mcp__ripple__impact", "input": {"symbol": "f"}}]}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "b", "content": "ok"}]}},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "hi"}, {"type": "tool_use", "id": "c", "name": "Edit", "input": {}}]}},
        {"type": "result", "subtype": "success", "is_error": False, "num_turns": 4, "duration_ms": 12300,
         "total_cost_usd": 0.12, "result": "done", "permission_denials": [{"tool_name": "Bash", "tool_input": {"command": "grep x"}}],
         "usage": {"input_tokens": 10, "output_tokens": 50, "cache_creation_input_tokens": 100, "cache_read_input_tokens": 1000}},
    ]
) + "\nnot json\n"  # fmt: skip

PARSED = {
    "agent_duration_s": 12.3, "cost_usd": 0.12, "edit_tools_denied": 0, "final_message": "done", "got_init": True,
    "got_result": True, "is_error": False, "mcp_servers": [{"name": "ripple", "status": "connected"}],
    "mcp_tools": ["mcp__ripple__impact"], "model": "claude-x", "num_turns": 4, "permission_denials": 1,
    "permission_denied": [{"input": {"command": "grep x"}, "tool": "Bash"}], "result_subtype": "success", "ripple_calls": 1,
    "tool_calls": {"Edit": 1, "ToolSearch": 1, "mcp__ripple__impact": 1},
    "usage": {"cache_creation_input_tokens": 100, "cache_read_input_tokens": 1000, "input_tokens": 10, "output_tokens": 50},
}  # fmt: skip


def record_of(task_id: str) -> dict:
    """The record run_one wrote for a with-ripple run of this task over PINNED_STREAM, as it was on main."""
    return {**PARSED, "changed_files": [], "diff": "", "exit_code": 0, "failures": [], "mode": "with-ripple", "repeat": 1,
            "success": False, "task": task_id, "timed_out": False}  # fmt: skip


def test_the_plain_python_and_node_prompts_are_the_ones_main_built():
    for (task_id, mode), sha in PROMPT_SHAS.items():
        task = TASKS[task_id]
        assert SHA(task.prompt) == TASK_PROMPT_SHAS[task_id]
        assert SHA(build_prompt(task.prompt, mode, task)) == sha, (task_id, mode)
    assert build_prompt("T", "with-ripple", TASKS[PY]) == (
        "The shell is there for running the tests: do not use it to edit files (sed, scripted rewrites). "
        "Make every edit with the Edit or Write tool.\n\n"
        "A ripple MCP server is connected for this repository. Its tools (impact, callers, callees, path, check) "
        "cover the call graph and running tests.\n\nT"
    )


@pytest.mark.parametrize("task_id", fetched(VITEST, JEST))
def test_the_plain_node_preamble_tools_and_shims_are_the_ones_main_built(task_id):
    spec = TASKS[task_id].spec
    runner = spec.runner
    assert node_preamble(spec).startswith(f"The shell is there for running the tests (`{'vitest run' if runner == 'vitest' else 'jest'}`, optionally with file paths) and the type checker (`tsc --noEmit -p <tsconfig>`)")
    assert allowed_tools("without", TASKS[task_id]) == [f"Bash({runner}:*)", "Bash(tsc:*)"]
    assert allowed_tools("with-ripple", TASKS[task_id]) == [f"Bash({runner}:*)", "Bash(tsc:*)", "mcp__ripple"]
    assert (SHA(runner_shim(Path("/repo"), spec)), SHA(tsc_shim(Path("/repo"), spec))) == SHIM_SHAS[task_id]
    command = agent_command("P", "with-ripple", Path("/tmp/m.json"), "sonnet", TASKS[task_id])
    assert command == [
        "claude", "-p", "P", "--output-format", "stream-json", "--verbose", "--setting-sources", "project,local",
        "--no-session-persistence", "--permission-mode", "acceptEdits", "--allowedTools",
        f"Bash({runner}:*),Bash(tsc:*),mcp__ripple", "--mcp-config", "/tmp/m.json", "--strict-mcp-config", "--model", "sonnet",
    ]  # fmt: skip


@pytest.mark.parametrize("task_id", fetched(VITEST, JEST))
def test_the_plain_node_run_writes_both_shims_as_main_did(task_id, tmp_path):
    spec = TASKS[task_id].spec
    write_node_shims(tmp_path / "bin", spec, Path("/repo"))
    written = {p.name: SHA(p.read_text()) for p in (tmp_path / "bin").iterdir()}
    assert written == {spec.runner: SHIM_SHAS[task_id][0], "tsc": SHIM_SHAS[task_id][1]}


@pytest.mark.parametrize("task_id", fetched(VITEST))
def test_the_plain_node_mcp_config_is_the_one_main_built(task_id, monkeypatch):
    monkeypatch.setenv("PATH", "/usr/bin")
    entry = mcp_config("with-ripple", Path("/r"), Path("/c"), TASKS[task_id], "/ts/bin/python", Path("/b"))
    assert entry == {
        "mcpServers": {"ripple": {
            "command": "/ts/bin/python",
            "args": ["-I", "-m", "ripple", "serve", "--repo", "/r", "--lang", "typescript", "--test-command", "/b/vitest run"],
            "env": {"RIPPLE_CACHE_DIR": "/c", "PATH": "/usr/bin"},
        }}
    }  # fmt: skip
    assert mcp_config("without", Path("/r"), Path("/c"), TASKS[task_id], None, Path("/b")) == {"mcpServers": {}}


@pytest.mark.skipif(not all(TASKS[i].available for i in (PY, VITEST, JEST)), reason=NEEDS_CACHE)
def test_the_plain_dry_plan_is_main_s_plus_one_arm_line():
    args = types.SimpleNamespace(repeats=3, model="sonnet", ripple_python="/py/bin/python")
    plan = describe_plan([TASKS[i] for i in (PY, VITEST, JEST)], ["with-ripple", "without"], args, [])
    lines = [line for line in plan.split("\n") if line != "arm: base"]
    assert lines == [line.replace("{RIPPLE_BIN}", str(RIPPLE_BIN)) for line in PLAN_LINES]


def test_a_stream_parses_to_what_main_made_of_it_apart_from_the_new_keys():
    parsed = parse_stream(PINNED_STREAM)
    assert {k: v for k, v in parsed.items() if k not in NEW_KEYS} == PARSED


class Fake:
    """Stands in for subprocess.Popen(claude ...): writes PINNED_STREAM to the run's stdout file."""

    def __init__(self, cmd, cwd, env, stdin, stdout, stderr, start_new_session):
        stdout.write(PINNED_STREAM.encode())
        self.returncode = 0
        self.pid = 0

    def wait(self, timeout=None):
        return 0


@pytest.mark.parametrize("task_id", [PY, *fetched(VITEST)])
def test_a_run_record_is_what_main_wrote_apart_from_the_new_keys(task_id, monkeypatch, tmp_path):
    monkeypatch.setattr(run_mod, "subprocess", types.SimpleNamespace(**{**vars(subprocess), "Popen": Fake}))
    monkeypatch.setattr(run_mod, "check_task", lambda task, repo: {"success": False, "failures": []})
    root = tmp_path / "root"
    root.mkdir()
    args = types.SimpleNamespace(model="sonnet", timeout=5, ripple_python=None)
    record = run_mod.run_one(TASKS[task_id], "with-ripple", 1, root, root / "bin", args)
    kept = {k: v for k, v in record.items() if k not in NEW_KEYS | {"run_dir", "wall_s"}}
    assert kept == record_of(task_id)
