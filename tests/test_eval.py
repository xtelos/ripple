"""The eval's own parts that do not call an agent: tasks, checks, parsing, tables.

The two parametrized tests prove each check has teeth before any agent is
graded by it: it fails on the untouched fixture (the task is not done yet)
and passes on the reference solution (the task can be done).
"""

import json
import shutil
from pathlib import Path

import pytest

from eval.check import count_passed, check_task
from eval.loader import apply_solution, copy_fixture, load_task, load_tasks
from eval.report import input_tokens, summarize, table
from eval.run import agent_command, child_env, diff_trees, is_rate_limited, mcp_config, parse_stream

TASKS = load_tasks()


def test_ten_tasks_with_unique_ids_and_prompts():
    assert len(TASKS) == 10
    assert len({t.id for t in TASKS}) == 10
    assert all(t.prompt.strip() and t.why.strip() for t in TASKS)


def test_a_task_missing_a_field_is_refused(tmp_path):
    (tmp_path / "t1").mkdir()
    (tmp_path / "t1" / "task.json").write_text(json.dumps({"id": "t1", "fixture": "bench/fixtures/shop"}))
    with pytest.raises(ValueError, match="missing prompt, min_tests, why"):
        load_task(tmp_path / "t1")


def test_selecting_an_unknown_task_is_an_error():
    with pytest.raises(ValueError, match="unknown task: nope"):
        load_tasks(only=["nope"])


@pytest.mark.parametrize("task", TASKS, ids=lambda t: t.id)
def test_check_fails_on_the_untouched_fixture(task, tmp_path):
    repo = copy_fixture(task, tmp_path / "repo")
    verdict = check_task(task, repo)
    assert verdict["suite_passed"] and verdict["tests_passed"] >= task.min_tests
    assert not verdict["hidden_passed"]
    assert not verdict["success"]


@pytest.mark.parametrize("task", TASKS, ids=lambda t: t.id)
def test_check_passes_on_the_reference_solution(task, tmp_path):
    repo = copy_fixture(task, tmp_path / "repo")
    apply_solution(task, repo)
    verdict = check_task(task, repo)
    assert verdict["success"], verdict


def test_deleting_the_tests_does_not_pass(tmp_path):
    task = next(t for t in TASKS if t.id == "04-ledger-rename-invoice-total")
    repo = copy_fixture(task, tmp_path / "repo")
    apply_solution(task, repo)
    for test_file in (repo / "tests").glob("test_*.py"):
        if test_file.name != "test_money.py":
            test_file.unlink()
    verdict = check_task(task, repo)
    assert verdict["suite_passed"] and verdict["tests_passed"] < task.min_tests
    assert not verdict["success"]


def test_count_passed():
    assert count_passed("25 passed in 0.06s") == 25
    assert count_passed("1 failed, 24 passed in 0.1s") == 24
    assert count_passed("no tests ran in 0.01s") == 0


def test_the_command_never_uses_bare_and_leaves_out_user_settings():
    cmd = agent_command("do it", "without", Path("/tmp/m.json"), "sonnet")
    assert "--bare" not in cmd
    assert cmd[:3] == ["claude", "-p", "do it"]
    assert cmd[cmd.index("--setting-sources") + 1] == "project,local"
    assert "--no-session-persistence" in cmd and "--strict-mcp-config" in cmd
    assert cmd[cmd.index("--permission-mode") + 1] == "acceptEdits"
    assert "mcp__ripple" not in cmd[cmd.index("--allowedTools") + 1]
    with_ripple = agent_command("do it", "with-ripple", Path("/tmp/m.json"), "sonnet")
    assert "mcp__ripple" in with_ripple[with_ripple.index("--allowedTools") + 1]


def test_only_with_ripple_gets_a_server(tmp_path):
    assert mcp_config("without", tmp_path, tmp_path) == {"mcpServers": {}}
    server = mcp_config("with-ripple", tmp_path, tmp_path / "cache")["mcpServers"]["ripple"]
    assert server["args"] == ["serve", "--repo", str(tmp_path)]
    assert server["env"] == {"RIPPLE_CACHE_DIR": str(tmp_path / "cache")}


def test_child_env_drops_the_api_key_and_session_variables(tmp_path):
    env = child_env({"PATH": "/usr/bin", "ANTHROPIC_API_KEY": "x", "CLAUDECODE": "1", "HOME": "/h"}, tmp_path, tmp_path)
    assert "ANTHROPIC_API_KEY" not in env and "CLAUDECODE" not in env
    assert env["PATH"].startswith(str(tmp_path)) and env["HOME"] == "/h"
    assert env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] == "1"


STREAM = "\n".join(
    json.dumps(e)
    for e in [
        {"type": "system", "subtype": "init", "model": "claude-x", "tools": ["Read", "mcp__ripple__impact"],
         "mcp_servers": [{"name": "ripple", "status": "connected"}]},
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "mcp__ripple__impact"}]}},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "hi"},
                                                      {"type": "tool_use", "name": "Edit"}]}},
        {"type": "result", "subtype": "success", "is_error": False, "num_turns": 4, "duration_ms": 12300,
         "total_cost_usd": 0.12, "result": "done", "permission_denials": [{}],
         "usage": {"input_tokens": 10, "output_tokens": 50, "cache_creation_input_tokens": 100,
                   "cache_read_input_tokens": 1000}},
    ]
) + "\nnot json\n"  # fmt: skip


def test_parse_stream():
    parsed = parse_stream(STREAM)
    assert parsed["model"] == "claude-x"
    assert parsed["mcp_tools"] == ["mcp__ripple__impact"]
    assert parsed["tool_calls"] == {"mcp__ripple__impact": 1, "Edit": 1}
    assert parsed["ripple_calls"] == 1
    assert parsed["num_turns"] == 4 and parsed["agent_duration_s"] == 12.3
    assert parsed["permission_denials"] == 1
    assert input_tokens(parsed) == 1110


def test_rate_limit_is_recognised_only_on_an_error():
    assert not is_rate_limited(parse_stream(STREAM), "")
    assert is_rate_limited({"got_result": True, "is_error": True, "final_message": "Claude usage limit reached"}, "")
    assert is_rate_limited({"got_result": False}, "API Error: 429 rate_limit_error")
    assert not is_rate_limited({"got_result": True, "is_error": True, "final_message": "tool failed"}, "")


def test_diff_trees(tmp_path):
    before, after = tmp_path / "a", tmp_path / "b"
    (before / "pkg").mkdir(parents=True)
    (before / "pkg" / "x.py").write_text("x = 1\n")
    (before / "pkg" / "y.py").write_text("y = 1\n")
    shutil.copytree(before, after)
    (after / "pkg" / "x.py").write_text("x = 2\n")
    (after / "pkg" / "z.py").write_text("z = 1\n")
    (after / "pkg" / "__pycache__").mkdir()
    (after / "pkg" / "__pycache__" / "junk.py").write_text("")
    changed, diff = diff_trees(before, after)
    assert changed == ["pkg/x.py", "pkg/z.py"]
    assert "-x = 1\n+x = 2\n" in diff


def run(task, mode, success, turns, tokens, aborted=False):
    return {"task": task, "mode": mode, "success": success, "num_turns": turns, "wall_s": 10.0,
            "usage": {"input_tokens": tokens, "output_tokens": 5}, "aborted": aborted}  # fmt: skip


def test_summarize_and_table():
    runs = [
        run("t1", "with-ripple", True, 4, 100),
        run("t1", "with-ripple", False, 8, 300),
        run("t1", "without", True, 6, 200),
        run("t2", "without", False, 2, 50),
        run("t2", "with-ripple", False, 0, 0, aborted=True),
    ]
    summary = summarize(runs)
    assert summary["aborted"] == 1
    assert summary["modes"]["with-ripple"]["runs"] == 2
    assert summary["modes"]["with-ripple"]["success_rate"] == 0.5
    assert summary["modes"]["with-ripple"]["median_input_tokens"] == 200
    assert summary["modes"]["without"]["median_turns"] == 4
    assert summary["tasks"]["t2"] == {"without": {"runs": 1, "successes": 0}}
    text = table(summary)
    assert "| with-ripple | 2 | 1/2 (50%) | 200 |" in text
    assert "| t2 | - | 0/1 |" in text
    assert "1 run(s) aborted" in text
