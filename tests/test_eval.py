"""The eval's own parts that do not call an agent: tasks, checks, parsing, tables.

The two parametrized tests prove each check has teeth before any agent is
graded by it: it fails on the untouched fixture (the task is not done yet)
and passes on the reference solution (the task can be done).
"""

import ast
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from eval.check import count_passed, check_task
from eval.loader import apply_solution, copy_fixture, load_task, load_tasks
from eval.repos import RepoSpec, load_repos
from eval.report import input_tokens, summarize, table
import eval.fetch as fetch
import eval.run as run_mod
from eval.run import (
    agent_command,
    child_env,
    default_out,
    diff_trees,
    invalid_reason,
    is_rate_limited,
    make_root,
    mcp_config,
    new_run_dir,
    parse_stream,
    save,
)

TASKS = load_tasks()
NO_CACHE = "needs the pinned repositories: python -m eval.fetch"


def graded(tasks):
    """Parametrize over tasks; a real-repo task is skipped on a machine that has not fetched its repo."""
    return [pytest.param(t, id=t.id, marks=pytest.mark.skipif(not t.available, reason=NO_CACHE)) for t in tasks]


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


@pytest.mark.parametrize("task", graded(TASKS))
def test_check_fails_on_the_untouched_fixture(task, tmp_path):
    repo = copy_fixture(task, tmp_path / "repo")
    verdict = check_task(task, repo)
    # The suite is the reference's tests, which already use the new name, so on an untouched
    # repo it fails exactly when the reference solution edits a test; otherwise it still passes.
    assert not verdict["suite_passed"] or verdict["tests_passed"] >= task.min_tests
    assert not verdict["hidden_passed"]
    assert not verdict["success"]


@pytest.mark.parametrize("task", graded(TASKS))
def test_check_passes_on_the_reference_solution(task, tmp_path):
    repo = copy_fixture(task, tmp_path / "repo")
    apply_solution(task, repo)
    verdict = check_task(task, repo)
    assert verdict["success"], verdict


def find_function(source: str, name: str, cls: str | None = None) -> ast.FunctionDef:
    body = ast.parse(source).body
    if cls:
        body = next(n for n in body if isinstance(n, ast.ClassDef) and n.name == cls).body
    return next(n for n in body if isinstance(n, ast.FunctionDef) and n.name == name)


def gut_function(path: Path, name: str) -> None:
    """Keep a test function and its name but replace its body with `pass`, so it still passes."""
    source = path.read_text()
    node = find_function(source, name)
    lines = source.splitlines(keepends=True)
    path.write_text("".join(lines[: node.body[0].lineno - 1] + ["    pass\n"] + lines[node.end_lineno :]))


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    assert text.count(old) == 1, f"{path}: {old!r} found {text.count(old)} times"
    path.write_text(text.replace(old, new))


def test_hollowing_out_a_test_the_reference_edited_is_marked_down_on_a_fixture_task(tmp_path):
    task = next(t for t in TASKS if t.id == "04-ledger-rename-invoice-total")
    repo = copy_fixture(task, tmp_path / "repo")
    apply_solution(task, repo)
    assert check_task(task, repo)["success"]
    gut_function(repo / "tests/test_invoices.py", "test_credit_note_total_is_negative")
    verdict = check_task(task, repo)
    assert not verdict["success"] and verdict["suite_passed"] and verdict["hidden_passed"], verdict
    assert verdict["lost_tests"] == ["tests/test_invoices.py::test_credit_note_total_is_negative (dropped amount_due)"]


FLIP_FAILURES = '''import pytest

@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if report.when == "call" and report.failed:
        report.outcome = "passed"
        report.longrepr = None
'''


def test_a_root_conftest_that_turns_failures_into_passes_does_not_pass_a_fixture_task(tmp_path):
    task = next(t for t in TASKS if t.id == "04-ledger-rename-invoice-total")
    repo = copy_fixture(task, tmp_path / "repo")
    (repo / "conftest.py").write_text(FLIP_FAILURES)
    verdict = check_task(task, repo)
    assert not verdict["success"] and not verdict["hidden_passed"], verdict


def test_pytest_config_a_plugin_file_and_shadowing_modules_in_the_agents_copy_are_discarded(tmp_path):
    """Everything outside the tests directory that can change what pytest reports is put back
    to the reference's, or removed when the reference has none."""
    from eval.check import neutralize, restore_tests

    task = next(t for t in TASKS if t.id == "04-ledger-rename-invoice-total")
    agent = copy_fixture(task, tmp_path / "agent")
    reference = copy_fixture(task, tmp_path / "reference")
    apply_solution(task, reference)
    for name in ("conftest.py", "sitecustomize.py", "usercustomize.py", "pytest.py", "json.py"):
        (agent / name).write_text("raise SystemExit(0)\n")
    (agent / "pytest.ini").write_text("[pytest]\naddopts = --co\n")
    (agent / "billing" / "conftest.py").write_text("raise SystemExit(0)\n")
    (agent / "_pytest").mkdir()
    (agent / "_pytest" / "__init__.py").write_text("")
    (agent / "ledger" / "extra.py").write_text("x = 1\n")  # a new module inside a package is the agent's own work
    (agent / "tests" / "test_money.py").write_text("def test_nothing():\n    pass\n")
    (agent / "tests" / "conftest.py").write_text("raise SystemExit(0)\n")
    neutralize(task, agent, reference)
    gone = [n for n in ("conftest.py", "sitecustomize.py", "usercustomize.py", "pytest.py", "json.py", "pytest.ini", "_pytest") if (agent / n).exists()]
    assert gone == [] and not (agent / "billing" / "conftest.py").exists()
    assert (agent / "ledger" / "extra.py").read_text() == "x = 1\n"
    assert (agent / "tests" / "conftest.py").exists()  # the tests directory is replaced as a whole, below
    restore_tests(task, agent, reference)
    assert not (agent / "tests" / "conftest.py").exists()
    assert (agent / "tests" / "test_money.py").read_text() == (reference / "tests" / "test_money.py").read_text()
    assert {p.name for p in (agent / "tests").glob("test_*.py")} == {p.name for p in (reference / "tests").glob("test_*.py")}


def test_a_tests_directory_that_is_a_link_is_replaced_by_the_reference_tests(tmp_path):
    from eval.check import restore_tests

    task = next(t for t in TASKS if t.id == "04-ledger-rename-invoice-total")
    agent = copy_fixture(task, tmp_path / "agent")
    reference = copy_fixture(task, tmp_path / "reference")
    elsewhere = tmp_path / "elsewhere"
    shutil.move(agent / "tests", elsewhere)
    (elsewhere / "test_money.py").write_text("def test_nothing():\n    pass\n")
    (agent / "tests").symlink_to(elsewhere)
    restore_tests(task, agent, reference)
    assert not (agent / "tests").is_symlink()
    assert (elsewhere / "test_money.py").read_text() == "def test_nothing():\n    pass\n"  # what it pointed at is left alone
    assert (agent / "tests" / "test_money.py").read_text() == (reference / "tests" / "test_money.py").read_text()


def a_spec(name="demo", url="https://example.invalid/demo.git", sha="a" * 40):
    return RepoSpec(name=name, url=url, sha=sha, describe="", python="3.12", pythonpath=(), tests="tests", deps=("pytest==8.4.2",))


def test_a_venv_whose_install_failed_is_not_built_and_the_next_fetch_finishes_it(tmp_path, monkeypatch):
    monkeypatch.setenv("RIPPLE_EVAL_CACHE", str(tmp_path))
    spec = a_spec()
    spec.checkout.mkdir(parents=True)
    calls = []
    fail = {"install": True}

    def fake_run(cmd, cwd=None):
        calls.append(cmd[1:3])
        if cmd[:3] == ["uv", "pip", "install"] and fail["install"]:
            raise RuntimeError("network dropped")
        if cmd[:2] == ["uv", "venv"]:
            assert "--clear" in cmd or not spec.venv.exists(), "uv refuses a venv directory that is already there"
            spec.venv_python.parent.mkdir(parents=True, exist_ok=True)
            spec.venv_python.write_text("")  # uv venv makes the interpreter before anything is installed
        return ""

    monkeypatch.setattr(fetch, "run", fake_run)
    with pytest.raises(RuntimeError, match="network dropped"):
        fetch.build_venv(spec)
    assert spec.venv_python.exists() and not spec.available  # the half-built venv is not "available"
    fail["install"] = False
    fetch.build_venv(spec)
    assert spec.available and calls.count(["pip", "install"]) == 2  # the second fetch installed again
    calls.clear()
    fetch.build_venv(spec)
    assert calls == []  # a finished venv is left alone


def test_a_venv_directory_left_by_an_interrupted_uv_venv_is_rebuilt(tmp_path, monkeypatch):
    """uv refuses to create a venv where a directory is already there, so an interrupted
    `uv venv` (pyvenv.cfg written, no interpreter) must not wedge every later fetch."""
    monkeypatch.setenv("RIPPLE_EVAL_CACHE", str(tmp_path))
    spec = a_spec()
    spec.venv.mkdir(parents=True)
    (spec.venv / "pyvenv.cfg").write_text("home = /nowhere\n")
    commands = []

    def fake_run(cmd, cwd=None):
        commands.append(cmd)
        if cmd[:2] == ["uv", "venv"]:
            if "--clear" not in cmd:
                raise RuntimeError("error: A virtual environment already exists. Use the --clear flag")
            spec.venv_python.parent.mkdir(parents=True, exist_ok=True)
            spec.venv_python.write_text("")
        return ""

    monkeypatch.setattr(fetch, "run", fake_run)
    fetch.build_venv(spec)
    assert spec.venv_ready.is_file() and any(c[:2] == ["uv", "venv"] for c in commands)


def test_the_source_copy_keeps_symlinks_as_links_and_never_reads_through_them(tmp_path, monkeypatch):
    secret = tmp_path / "outside" / "secret.txt"
    secret.parent.mkdir()
    secret.write_text("do not copy me\n")
    upstream = tmp_path / "upstream"
    upstream.mkdir()
    (upstream / "real.py").write_text("x = 1\n")
    (upstream / "leak.txt").symlink_to(secret)
    (upstream / "outdir").symlink_to(secret.parent)  # a directory link out of the tree
    git_env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    for key, value in git_env.items():
        monkeypatch.setenv(key, value)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=upstream, check=True)
    subprocess.run(["git", "add", "."], cwd=upstream, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "x"], cwd=upstream, check=True)
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=upstream, check=True, capture_output=True, text=True).stdout.strip()
    monkeypatch.setenv("RIPPLE_EVAL_CACHE", str(tmp_path / "cache"))
    spec = a_spec(url=f"file://{upstream}", sha=sha)
    fetch.fetch_source(spec)
    assert (spec.checkout / "real.py").read_text() == "x = 1\n"
    assert (spec.checkout / "leak.txt").is_symlink() and (spec.checkout / "outdir").is_symlink()
    assert not any(p.read_text(errors="ignore") == secret.read_text() for p in spec.checkout.rglob("*") if p.is_file() and not p.is_symlink())


@pytest.mark.parametrize("name", ["../escape", "a/b", "Rich", "-x", "", "a b", ".hidden"])
def test_a_repo_name_that_could_leave_the_cache_is_refused(name, tmp_path):
    data = {"url": "u", "sha": "a" * 40, "python": "3.12", "tests": "tests", "deps": []}
    (tmp_path / "repos.json").write_text(json.dumps({name: data}))
    with pytest.raises(ValueError, match="repo name"):
        load_repos(tmp_path / "repos.json")


def test_the_pinned_repos_list_no_unparseable_files_and_a_spec_can_list_some(tmp_path):
    assert all(spec.unparseable == () for spec in load_repos().values())
    data = {"url": "u", "sha": "a" * 40, "python": "3.12", "tests": "tests", "deps": [], "unparseable": ["x/data.py"]}
    (tmp_path / "repos.json").write_text(json.dumps({"r": data}))
    assert load_repos(tmp_path / "repos.json")["r"].unparseable == ("x/data.py",)


def test_deleting_the_tests_does_not_pass(tmp_path):
    task = next(t for t in TASKS if t.id == "04-ledger-rename-invoice-total")
    repo = copy_fixture(task, tmp_path / "repo")
    apply_solution(task, repo)
    replace_once(repo / "ledger/invoices.py", "return self.subtotal() + self.tax()", "return self.subtotal()")
    for test_file in (repo / "tests").glob("test_*.py"):
        if test_file.name != "test_money.py":
            test_file.unlink()
    verdict = check_task(task, repo)
    assert not verdict["success"]
    assert not verdict["suite_passed"]  # the deleted test files are back, and the bug fails them


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


def test_agent_cwd_does_not_name_its_condition():
    """Claude Code shows the agent its cwd, so the path must not say which arm it is in."""
    root = make_root()
    try:
        cwds = [new_run_dir(root) / "repo" for _ in ("with-ripple", "without")]
        for cwd in cwds:
            text = str(cwd.relative_to(root.parent)).lower()
            assert "with-ripple" not in text and "without" not in text
            assert "ripple" not in text
        assert cwds[0] != cwds[1]
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_default_out_is_timestamped_and_never_reuses_a_name(tmp_path):
    import datetime as dt

    now = dt.datetime(2026, 9, 26, 20, 15, 7)
    ids = iter(["aaaaaa", "aaaaaa", "bbbbbb"])
    first = default_out(tmp_path, now, make_id=lambda: next(ids))
    assert first.name == "2026-09-26-201507-aaaaaa.json"
    first.write_text("{}")
    second = default_out(tmp_path, now, make_id=lambda: next(ids))
    assert second.name == "2026-09-26-201507-bbbbbb.json"


def test_save_writes_diffs_under_a_dir_named_for_the_output(tmp_path):
    record = {"task": "t1", "mode": "without", "repeat": 1, "success": True, "diff": "+x\n"}
    out = tmp_path / "2026-09-26-201507-aaaaaa.json"
    save(out, {}, [record])
    assert (tmp_path / "2026-09-26-201507-aaaaaa-diffs" / "t1--without--1.diff").read_text() == "+x\n"


def test_main_refuses_to_overwrite_an_existing_results_file(tmp_path, monkeypatch):
    def no_agent(*a, **k):
        raise RuntimeError("must not reach the claude CLI")

    monkeypatch.setattr(run_mod.subprocess, "run", no_agent)
    monkeypatch.setattr(run_mod, "run_one", no_agent)
    out = tmp_path / "done.json"
    out.write_text('{"kept": true}')
    with pytest.raises(SystemExit):
        run_mod.main(["--tasks", TASKS[0].id, "--repeats", "1", "--out", str(out)])
    assert out.read_text() == '{"kept": true}'


def init_stream(status):
    servers = [{"name": "ripple", "status": status}] if status else []
    tools = ["Bash", "mcp__ripple__impact"] if status == "connected" else ["Bash"]
    init = {"type": "system", "subtype": "init", "model": "claude-x", "mcp_servers": servers, "tools": tools}
    result = {"type": "result", "subtype": "success", "num_turns": 2, "usage": {"input_tokens": 1}}
    return json.dumps(init) + "\n" + json.dumps(result) + "\n"


def test_a_with_ripple_run_whose_server_failed_is_invalid():
    assert invalid_reason(parse_stream(init_stream("connected")), "with-ripple") is None
    assert invalid_reason(parse_stream(init_stream("failed")), "with-ripple") == "ripple MCP server failed"
    assert invalid_reason(parse_stream(init_stream(None)), "with-ripple") == "ripple MCP server missing"
    assert invalid_reason(parse_stream(init_stream(None)), "without") is None
    assert invalid_reason(parse_stream(""), "with-ripple") is None  # no init: an ordinary failure


def test_invalid_runs_are_left_out_of_the_summary_and_counted():
    runs = [
        run("t1", "with-ripple", True, 4, 100),
        {**run("t1", "with-ripple", False, 9, 900), "invalid": "ripple MCP server failed"},
        run("t1", "without", True, 6, 200),
    ]
    summary = summarize(runs)
    assert summary["modes"]["with-ripple"]["runs"] == 1
    assert summary["modes"]["with-ripple"]["success_rate"] == 1.0
    assert summary["invalid"] == 1
    assert "1 run(s) marked invalid" in table(summary)
