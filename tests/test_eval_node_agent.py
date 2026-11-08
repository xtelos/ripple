"""The agent harness for the node tasks (eval/run.py with eval/node_agent.py): what the agent is given, what it is
refused, how a run is recorded, and what is checked before any agent starts. Nothing here calls a model: the agent is
a fake that does what a real session could do through the environment the run built.

Tests that need a fetched repository (`python -m eval.fetch`) skip without it. Those that need an interpreter with
ripple's typescript extra use this one if it has tree-sitter, else the one named in $RIPPLE_EVAL_TS_PYTHON, else skip.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest

import eval.node_agent as node_agent
import eval.run as run_mod
from eval.check_node import report_flags, run_node_suite
from eval.loader import apply_solution, copy_fixture, load_tasks
from eval.node_agent import node_files, node_preamble, write_node_shims
from eval.repos import load_repos
from eval.report import summarize, table
from eval.run import (
    PREAMBLE,
    RIPPLE_BIN,
    WITH_RIPPLE_LINE,
    agent_command,
    allowed_tools,
    blocked_reason,
    build_prompt,
    child_env,
    diff_trees,
    invalid_reason,
    mcp_config,
    parse_stream,
    select_tasks,
)

TASKS = {t.id: t for t in load_tasks()}
NODE = [t for t in TASKS.values() if t.is_node]
PYTHON = [t for t in TASKS.values() if not t.is_node]
VITEST_TASK, JEST_TASK, TSX_TASK = (
    "20-neverthrow-rename-resultasync-combine",
    "24-tsyringe-rename-transform",
    "27-react-pro-sidebar-rename-usemediaquery",
)
NEEDS_CACHE = "needs the pinned repositories: python -m eval.fetch"
SHELL = pytest.mark.skipif(shutil.which("sh") is None, reason="needs a POSIX shell")


def fetched(*ids):
    return [pytest.param(i, id=i, marks=pytest.mark.skipif(not TASKS[i].available, reason=NEEDS_CACHE)) for i in ids]


def ts_python() -> str | None:
    for candidate in (sys.executable, os.environ.get("RIPPLE_EVAL_TS_PYTHON")):
        if candidate and node_agent.import_problem(candidate) is None:
            return candidate
    return None


needs_ts = pytest.mark.skipif(ts_python() is None, reason="needs an interpreter with ripple[typescript]: set RIPPLE_EVAL_TS_PYTHON")


# ------------------------------------------------------------------ Python runs are exactly what they were


def test_python_agent_command_prompt_and_server_are_what_they_always_were(tmp_path):
    """Pinned as literals, taken from the code before the node harness existed: a plain `python -m eval.run` builds
    these and so costs what it did."""
    assert PREAMBLE == (
        "The shell is there for running the tests: do not use it to edit files (sed, scripted rewrites). "
        "Make every edit with the Edit or Write tool."
    )
    command = agent_command("P", "with-ripple", Path("/tmp/m.json"), "sonnet")
    assert command == [
        "claude", "-p", "P", "--output-format", "stream-json", "--verbose", "--setting-sources", "project,local",
        "--no-session-persistence", "--permission-mode", "acceptEdits", "--allowedTools",
        "Bash(python -m pytest:*),Bash(python3 -m pytest:*),Bash(pytest:*),mcp__ripple",
        "--mcp-config", "/tmp/m.json", "--strict-mcp-config", "--model", "sonnet",
    ]  # fmt: skip
    assert agent_command("P", "without", Path("/tmp/m.json"), "sonnet")[11:13] == [
        "--allowedTools", "Bash(python -m pytest:*),Bash(python3 -m pytest:*),Bash(pytest:*)"
    ]  # fmt: skip
    assert build_prompt("T", "without") == PREAMBLE + "\n\nT"
    assert build_prompt("T", "with-ripple") == PREAMBLE + "\n\n" + WITH_RIPPLE_LINE + "\n\nT"
    assert mcp_config("without", Path("/r"), Path("/c")) == {"mcpServers": {}}
    assert mcp_config("with-ripple", Path("/r"), Path("/c")) == {
        "mcpServers": {"ripple": {"command": str(RIPPLE_BIN), "args": ["serve", "--repo", "/r"], "env": {"RIPPLE_CACHE_DIR": "/c"}}}
    }  # fmt: skip


def test_a_python_task_passed_through_the_new_arguments_builds_the_same_things():
    """The node harness takes the task as an extra argument; for a Python task it must change nothing."""
    task = next(t for t in PYTHON if t.spec)
    for mode in ("with-ripple", "without"):
        assert agent_command("P", mode, Path("/m"), "sonnet", task) == agent_command("P", mode, Path("/m"), "sonnet")
        assert allowed_tools(mode, task) == allowed_tools(mode)
        assert build_prompt("T", mode, task) == build_prompt("T", mode)
        assert mcp_config(mode, Path("/r"), Path("/c"), task, None, Path("/b")) == mcp_config(mode, Path("/r"), Path("/c"))


def test_the_default_selection_is_the_nineteen_python_tasks_and_spending_on_node_is_a_choice():
    ids = [t.id for t in select_tasks(list(TASKS.values()), None, False)]
    assert ids == [t.id for t in PYTHON] and len(ids) == 19 and all(int(i[:2]) <= 19 for i in ids)
    assert [t.id for t in select_tasks(list(TASKS.values()), "python", False)] == ids
    assert [t.id for t in select_tasks(list(TASKS.values()), "node", False)] == [t.id for t in NODE]
    assert len(NODE) == 9 and all(20 <= int(t.id[:2]) <= 28 for t in NODE)
    assert len(select_tasks(list(TASKS.values()), "all", False)) == 28


def test_naming_tasks_runs_exactly_those_whatever_the_runtime_flag():
    mixed = [TASKS[VITEST_TASK], PYTHON[0]]
    for runtime in (None, "python", "node", "all"):
        assert select_tasks(mixed, runtime, True) == mixed


@pytest.mark.skipif(not all(t.available for t in PYTHON), reason=NEEDS_CACHE)
def test_a_plain_run_over_the_default_selection_prints_nineteen_tasks_and_114_runs(capsys):
    assert run_mod.main(["--dry", "--modes", "with-ripple,without"]) in (0, 1)
    out = capsys.readouterr().out
    assert "tasks (19):" in out and "runs: 19 tasks x 2 modes x 3 repeats = 114" in out
    assert "[node" not in out and "20-neverthrow" not in out


# --------------------------------------------------------------------------- the dry plan, no model call


@pytest.fixture
def no_claude(monkeypatch):
    """Any attempt to start claude (or the batch) fails the test."""

    def refuse(*a, **k):
        raise AssertionError(f"started something it should not have: {a}")

    monkeypatch.setattr(run_mod, "subprocess", types.SimpleNamespace(**{**vars(subprocess), "run": refuse, "Popen": refuse}))
    monkeypatch.setattr(run_mod, "run_one", refuse)


@SHELL
@pytest.mark.skipif(not all(t.available for t in NODE), reason=NEEDS_CACHE)
def test_the_node_dry_view_lists_the_nine_tasks_the_shell_rules_and_the_checks(monkeypatch, capsys, no_claude, tmp_path):
    monkeypatch.setattr(run_mod, "ripple_problems", lambda *a, **k: [])
    out_file = tmp_path / "x.json"
    code = run_mod.main(["--dry", "--runtime", "node", "--ripple-python", "/py/bin/python", "--out", str(out_file)])
    text = capsys.readouterr().out
    assert code == 0
    assert "tasks (9):" in text and "runs: 9 tasks x 2 modes x 3 repeats = 54" in text
    for task in NODE:
        assert f"  {task.id}  [node, {task.spec.name}, {task.spec.runner}]" in text
    assert "--allowedTools 'Bash(vitest:*),Bash(tsc:*),mcp__ripple'" in text
    assert "--allowedTools 'Bash(jest:*),Bash(tsc:*)'" in text
    assert "ripple server: /py/bin/python -I -m ripple serve" in text
    assert "--lang typescript --test-command" in text
    assert "readiness" in text and "  ok" in text and "PROBLEM" not in text
    assert chr(0x2014) not in text
    assert not out_file.exists() and not out_file.with_suffix(".md").exists()  # nothing written


@SHELL
@pytest.mark.skipif(not (TASKS[JEST_TASK].available), reason=NEEDS_CACHE)
def test_the_dry_view_of_a_mixed_named_selection_shows_each_runtimes_own_rules(monkeypatch, capsys, no_claude):
    monkeypatch.setattr(run_mod, "ripple_problems", lambda *a, **k: [])
    code = run_mod.main(["--dry", "--tasks", f"04-ledger-rename-invoice-total,{JEST_TASK}", "--runtime", "python"])
    text = capsys.readouterr().out
    assert code == 0 and "tasks (2):" in text and "runs: 2 tasks x 2 modes x 3 repeats = 12" in text
    assert "  04-ledger-rename-invoice-total  [python]" in text and f"  {JEST_TASK}  [node, tsyringe, jest]" in text
    assert "[python] shell preamble: " + PREAMBLE in text
    assert "[node, jest] shell preamble: " + node_preamble(TASKS[JEST_TASK].spec) in text
    assert "Bash(python -m pytest:*),Bash(python3 -m pytest:*),Bash(pytest:*),mcp__ripple" in text
    assert "Bash(jest:*),Bash(tsc:*),mcp__ripple" in text


@SHELL
@pytest.mark.skipif(not (TASKS[JEST_TASK].available), reason=NEEDS_CACHE)
def test_the_without_arm_never_needs_a_typescript_interpreter(tmp_path, capsys, no_claude):
    bad = tmp_path / "badpy"
    bad.write_text("#!/bin/sh\nexit 1\n")
    bad.chmod(0o755)
    code = run_mod.main(["--dry", "--tasks", JEST_TASK, "--modes", "without", "--ripple-python", str(bad)])
    out = capsys.readouterr().out
    assert code == 0 and "PROBLEM" not in out and "ripple server" not in out


# --------------------------------------------------------------------- the closed shell: what is allowed


def rules_of(task, mode="with-ripple"):
    return allowed_tools(mode, task)


def permits(rules, command):
    """Claude Code's Bash(prefix:*) rules, as far as these tests need them: a command matches a rule when it is the
    prefix or starts with it and a space, and a compound command (&&, ||, ;, |, $( ), backticks, newlines) is allowed
    only when every part is. A bare `Bash` rule would allow everything."""
    prefixes = []
    for rule in rules:
        if rule == "Bash":
            return True
        if rule.startswith("Bash(") and rule.endswith(":*)"):
            prefixes.append(rule[len("Bash(") : -len(":*)")])
    parts = [p.strip() for p in re.split(r"&&|\|\||;|\||\n|`|\$\(|\)", command) if p.strip()]
    return all(any(p == x or p.startswith(x + " ") for x in prefixes) for p in parts)


def test_a_node_tasks_shell_is_its_runner_and_tsc_and_nothing_else():
    for task in NODE:
        rules = rules_of(task, "without")
        assert rules == [f"Bash({task.spec.runner}:*)", "Bash(tsc:*)"], task.id
        assert rules_of(task) == [*rules, "mcp__ripple"]


DENIED = [
    'node -e "require(\'fs\').writeFileSync(\'src/a.ts\',\'\')"',
    "node script.js",
    "npx sed -i s/a/b/ src/a.ts",
    "npx some-arbitrary-package",
    "npx vitest run",
    "npx tsc --init",
    "npm run build",
    "npm run lint:fix",
    "npm run format",
    "npm exec sed",
    "npm test",
    "npm test -- -u",
    "npm install left-pad",
    "yarn lint --fix",
    "sed -i s/a/b/ src/a.ts",
    "ls | xargs sed -i s/a/b/",
    "python -c 'print(1)'",
    "cat src/a.ts",
    "vitest run && sed -i s/a/b/ src/a.ts",
    "vitest run; npx prettier --write .",
    "vitest run | xargs rm",
    "tsc --noEmit && node -e 1",
    "echo $(sed -i s/a/b/ src/a.ts)",
    "vitestx run",
    "tscx",
]


@pytest.mark.parametrize("task", [TASKS[VITEST_TASK], TASKS[JEST_TASK]], ids=lambda t: t.spec.runner)
@pytest.mark.parametrize("command", DENIED)
def test_no_rewrite_channel_is_allowed_to_a_node_agent(task, command):
    assert not permits(rules_of(task), command), command


def test_the_runner_of_the_other_kind_of_repo_is_not_allowed():
    assert not permits(rules_of(TASKS[VITEST_TASK]), "jest src")
    assert not permits(rules_of(TASKS[JEST_TASK]), "vitest run")


@pytest.mark.parametrize(
    "command",
    ["vitest", "vitest run", "vitest run tests/index.test.ts", "tsc --noEmit -p tests/tsconfig.tests.json", "vitest run && tsc --noEmit"],
)
def test_the_allowed_commands_are_allowed(command):
    assert permits(rules_of(TASKS[VITEST_TASK]), command)


def test_the_permission_model_used_here_is_not_vacuous():
    """permits() must deny what the python rules deny and allow what they allow, or the denial tests prove nothing."""
    assert permits(run_mod.TEST_COMMANDS, "pytest -x tests") and not permits(run_mod.TEST_COMMANDS, "sed -i s/a/b/ x.py")
    assert not permits(run_mod.TEST_COMMANDS, "pytest && sed -i s/a/b/ x.py") and permits(["Bash"], "anything")


# --------------------------------------------------------------------- the closed shell: what the shims do


def fake_repo(tmp_path, spec):
    """A repo whose node_modules/.bin has recording stand-ins for the runner and tsc: each prints its arguments
    one per line, so a test sees exactly what the shim handed on."""
    repo = tmp_path / "repo"
    bin_dir = repo / "node_modules" / ".bin"
    bin_dir.mkdir(parents=True)
    for name in (spec.runner, "tsc"):
        (bin_dir / name).write_text('#!/bin/sh\nfor a in "$@"; do echo "$a"; done\n')
        (bin_dir / name).chmod(0o755)
    write_node_shims(tmp_path / "shims", spec, repo)
    return repo


def through_shim(tmp_path, spec, name, *args):
    repo = fake_repo(tmp_path, spec) if not (tmp_path / "repo").exists() else tmp_path / "repo"
    env = {"PATH": f"{tmp_path / 'shims'}:/usr/bin:/bin"}
    done = subprocess.run([name, *args], cwd=repo, env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL)
    return done.returncode, done.stdout.splitlines(), done.stderr


VITEST_SPEC, JEST_SPEC = load_repos()["neverthrow"], load_repos()["tsyringe"]


@SHELL
@pytest.mark.parametrize("name", sorted(n for n, r in load_repos().items() if r.is_node))
def test_the_runner_shim_with_no_arguments_runs_what_the_grader_runs(tmp_path, name):
    spec = load_repos()[name]
    code, argv, _ = through_shim(tmp_path, spec, spec.runner)
    grader = spec.node_argv()[1:]
    expected = grader if spec.runner == "vitest" else [*grader[:-1], "--ci", "--watchman=false", grader[-1]]
    assert code == 0 and argv == expected, (argv, expected)
    if spec.runner == "jest":  # the two flags the shim adds are two the grader passes too
        assert {"--ci", "--watchman=false"} <= set(report_flags("jest", Path("r")))


@SHELL
def test_the_vitest_shim_runs_once_and_keeps_the_no_cache_flag_for_any_arguments(tmp_path):
    base = ["run", "--no-cache"]
    assert through_shim(tmp_path, VITEST_SPEC, "vitest", "run")[1] == [*base, "tests"]
    assert through_shim(tmp_path, VITEST_SPEC, "vitest", "run", "tests/a.test.ts", "-t", "x")[1] == [*base, "tests/a.test.ts", "-t", "x"]
    assert through_shim(tmp_path, VITEST_SPEC, "vitest", "tests/a.test.ts")[1] == [*base, "tests/a.test.ts"]


@SHELL
@pytest.mark.parametrize(
    "args",
    [("-u",), ("run", "-u"), ("--update",), ("run", "--outputFile=r.json"), ("run", "--outputFile.json=r.json"), ("run", "--coverage"),
     ("run", "--coverage.enabled"), ("init",), ("watch",), ("related", "src/a.ts"), ("-w",), ("run", "--watch"), ("run", "--ui")],
)  # fmt: skip
def test_the_vitest_shim_refuses_what_writes_or_watches(tmp_path, args):
    code, out, err = through_shim(tmp_path, VITEST_SPEC, "vitest", *args)
    assert code == 2 and out == [] and "not available in this environment" in err, (args, code, out, err)


@SHELL
@pytest.mark.parametrize(
    "args",
    [("-u",), ("--updateSnapshot",), ("--init",), ("--watch",), ("--watchAll",), ("--outputFile", "r.json"), ("--outputFile=r.json",),
     ("--coverage",), ("--collectCoverage",), ("--coverageDirectory=d",), ("src", "-u")],
)  # fmt: skip
def test_the_jest_shim_refuses_what_writes_or_watches(tmp_path, args):
    code, out, err = through_shim(tmp_path, JEST_SPEC, "jest", *args)
    assert code == 2 and out == [] and "not available in this environment" in err, (args, code, out, err)


@SHELL
def test_the_jest_shim_passes_paths_and_filters_through(tmp_path):
    code, argv, _ = through_shim(tmp_path, JEST_SPEC, "jest", "src/__tests__/a.test.ts", "-t", "x")
    assert code == 0 and argv == ["--config", "test/jest.config.js", "--ci", "--watchman=false", "src/__tests__/a.test.ts", "-t", "x"]


@SHELL
def test_the_tsc_shim_forces_noemit_and_passes_only_a_project_and_read_only_flags(tmp_path):
    assert through_shim(tmp_path, VITEST_SPEC, "tsc")[1] == ["--noEmit"]
    assert through_shim(tmp_path, VITEST_SPEC, "tsc", "--noEmit", "-p", "tests/tsconfig.tests.json")[1] == [
        "--noEmit", "--noEmit", "-p", "tests/tsconfig.tests.json"
    ]  # fmt: skip
    assert through_shim(tmp_path, VITEST_SPEC, "tsc", "--project", "x.json", "--pretty")[1] == ["--noEmit", "--project", "x.json", "--pretty"]


@SHELL
@pytest.mark.parametrize(
    "args",
    [("--init",), ("-b",), ("--build",), ("--outDir", "x"), ("--outFile", "x.js"), ("--declaration",), ("--incremental",),
     ("--tsBuildInfoFile", "x"), ("--generateTrace", "x"), ("src/a.ts",), ("-p",), ("--noEmit", "false"), ("-w",), ("--watch",),
     ("-p", "x.json", "--emitDeclarationOnly")],
)  # fmt: skip
def test_the_tsc_shim_refuses_everything_that_could_emit_or_write(tmp_path, args):
    code, out, err = through_shim(tmp_path, VITEST_SPEC, "tsc", *args)
    assert code == 2 and out == [] and "not available in this environment" in err, (args, code, out, err)


@SHELL
def test_the_shims_export_the_graders_environment_and_name_only_this_runs_copy(tmp_path):
    repo = fake_repo(tmp_path, VITEST_SPEC)
    text = (tmp_path / "shims" / "vitest").read_text()
    assert f"export PATH={repo}/node_modules/.bin:" in text and "export CI=true NO_COLOR=1 FORCE_COLOR=0" in text
    assert f"exec {repo}/node_modules/.bin/vitest run --no-cache" in text
    assert sorted(p.name for p in (tmp_path / "shims").iterdir()) == ["tsc", "vitest"]
    assert not any(n in text for n in ("npm", "npx", "node "))


# ------------------------------------------------------------------ the node prompt


def test_the_node_note_names_the_real_commands_and_both_arms_get_the_same_one():
    for task in (TASKS[VITEST_TASK], TASKS[JEST_TASK]):
        note = node_preamble(task.spec)
        assert ("`vitest run`" if task.spec.runner == "vitest" else "`jest`") in note and "tsc --noEmit" in note
        assert "Edit or Write tool" in note and "shell" in note.lower() and "ripple" not in note.lower()
        plain, with_ripple = build_prompt(task.prompt, "without", task), build_prompt(task.prompt, "with-ripple", task)
        assert plain == note + "\n\n" + task.prompt
        assert with_ripple.replace(WITH_RIPPLE_LINE + "\n\n", "", 1) == plain
        assert PREAMBLE not in plain and chr(0x2014) not in plain + with_ripple


def test_the_node_note_does_not_promise_npm_test_or_other_commands():
    note = node_preamble(TASKS[VITEST_TASK].spec)
    assert not re.search(r"\bnpm\b|\bnpx\b|\byarn\b", note)


# ------------------------------------------------------------- the ripple server entry for a node task


def test_the_node_ripple_entry_gives_check_a_test_command_and_names_the_language(tmp_path):
    task = TASKS[VITEST_TASK]
    entry = mcp_config("with-ripple", Path("/r"), Path("/c"), task, "/py/bin/python", tmp_path / "b")["mcpServers"]["ripple"]
    assert entry["command"] == "/py/bin/python"
    assert entry["args"] == ["-I", "-m", "ripple", "serve", "--repo", "/r", "--lang", "typescript", "--test-command", f"{tmp_path / 'b'}/vitest run"]
    assert entry["env"]["RIPPLE_CACHE_DIR"] == "/c" and entry["env"]["PATH"]
    jest = mcp_config("with-ripple", Path("/r"), Path("/c"), TASKS[JEST_TASK], None, tmp_path / "b")["mcpServers"]["ripple"]
    assert jest["command"] == str(RIPPLE_BIN) and jest["args"][-2:] == ["--test-command", f"{tmp_path / 'b'}/jest"]
    assert mcp_config("without", Path("/r"), Path("/c"), task, None, tmp_path) == {"mcpServers": {}}


# ---------------------------------------------------------------- changed_files and the diff for node


def lay(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    return root


def test_a_node_diff_sees_typescript_javascript_json_and_docs_and_never_node_modules(tmp_path):
    start = {
        "src/a.ts": "a\n", "src/b.tsx": "b\n", "src/c.js": "c\n", "src/d.mts": "d\n", "README.md": "r\n", "site/m.mdx": "m\n",
        "package.json": "{}\n", "src/keep.ts": "k\n", "src/z.py": "z = 1\n", ".hidden/h.ts": "h\n", "notes.txt": "n\n",
    }  # fmt: skip
    before = lay(tmp_path / "before", start)
    after = lay(tmp_path / "after", start)
    for rel in ("src/a.ts", "src/b.tsx", "src/c.js", "src/d.mts", "README.md", "site/m.mdx", "package.json", "src/z.py", ".hidden/h.ts", "notes.txt"):
        (after / rel).write_text("changed\n")
    lay(after, {"src/new.d.ts": "n\n"})
    shared = lay(tmp_path / "cache", {"pkg/index.ts": "x\n"})
    (after / "node_modules").symlink_to(shared)  # the working copy's link to the cached install
    changed, diff = diff_trees(before, after, node=True)
    assert changed == ["README.md", "package.json", "site/m.mdx", "src/a.ts", "src/b.tsx", "src/c.js", "src/d.mts", "src/new.d.ts"]
    assert "--- a/src/a.ts" in diff and "-a\n+changed\n" in diff and "+++ b/src/new.d.ts" in diff and "node_modules" not in diff
    assert "z.py" not in diff and "h.ts" not in diff and "notes.txt" not in diff


def test_the_python_diff_still_sees_only_py_files(tmp_path):
    before = lay(tmp_path / "before", {"a.py": "1\n", "b.ts": "1\n"})
    after = lay(tmp_path / "after", {"a.py": "2\n", "b.ts": "2\n"})
    assert diff_trees(before, after)[0] == ["a.py"] and diff_trees(before, after, node=False)[0] == ["a.py"]


# ---------------------------------------------------------------- the fake agent, end to end


def patch_files(task) -> list[str]:
    return sorted(re.findall(r"^diff -ruN a/(\S+) b/", task.solution_patch.read_text(), re.M))


def pass_count(output: str) -> int:
    """The pass count in a runner's own summary line (vitest `Tests  117 passed (117)`, jest `Tests:  133 passed, 133 total`)."""
    found = re.search(r"^\s*Tests:?\s+.*?(\d+) passed", output, re.M)
    assert found, output[-500:]
    return int(found.group(1))


class NodeAgent:
    """Stands in for Popen(claude ...): in the run's working copy and with the run's environment, runs the repo's
    tests through the shim, applies the task's reference solution as an Edit would, runs them again and the type
    check, and writes a stream the way claude does."""

    task = None
    session: dict = {}
    streams: dict = {}

    def __init__(self, cmd, cwd, env, stdin, stdout, stderr, start_new_session):
        cwd = Path(cwd)
        task, runner = NodeAgent.task, NodeAgent.task.spec.runner

        def run(*argv):
            done = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=600)
            return done.returncode, done.stdout + done.stderr

        s = NodeAgent.session = {"cmd": cmd, "env": env}
        s["runner_path"] = shutil.which(runner, path=env["PATH"])
        s["before"] = run(runner)
        apply_solution(task, cwd)
        s["after"] = run(runner)
        s["refused"] = run(runner, "-u")
        s["node_modules_is_link"] = (cwd / "node_modules").is_symlink()
        stdout.write(NodeAgent.streams.get("stream", "").encode())
        self.returncode, self.pid = 0, 0

    def wait(self, timeout=None):
        return 0


def with_the_fake_agent(monkeypatch, task, stream=""):
    NodeAgent.task, NodeAgent.session, NodeAgent.streams = task, {}, {"stream": stream}
    # only run_one's own Popen is the fake: the grader (eval/check_node.py) and the shims start real processes
    monkeypatch.setattr(run_mod, "subprocess", types.SimpleNamespace(**{**vars(subprocess), "Popen": NodeAgent}))


def args(**extra):
    return types.SimpleNamespace(model="sonnet", timeout=60, ripple_python=None, **extra)


@SHELL
@pytest.mark.parametrize("task_id", fetched(VITEST_TASK, JEST_TASK, TSX_TASK))
def test_a_fake_agent_that_applies_the_reference_through_the_run_environment_is_graded_success(monkeypatch, tmp_path, task_id):
    task = TASKS[task_id]
    with_the_fake_agent(monkeypatch, task)
    root = tmp_path / "root"
    root.mkdir()
    record = run_mod.run_one(task, "without", 1, root, tmp_path / "unused-bin", args())
    session = NodeAgent.session
    expected_files = patch_files(task)
    print(f"\n{task_id}: changed_files={record['changed_files']}\n{record['diff'][:600]}")
    assert record["success"] is True and "invalid" not in record, record
    assert record["changed_files"] == expected_files
    assert all(f"--- a/{f}" in record["diff"] for f in expected_files) and record["diff"].count("\n+") >= len(expected_files)
    # the environment the agent had: its shim first on PATH, the cached install linked, the closed list on the command line
    run_dir = root / record["run_dir"]
    assert session["runner_path"] == str(run_dir / "bin" / task.spec.runner)
    assert session["node_modules_is_link"] and "CLAUDE_CODE_DISABLE_AUTO_MEMORY" in session["env"]
    assert session["cmd"][session["cmd"].index("--allowedTools") + 1] == f"Bash({task.spec.runner}:*),Bash(tsc:*)"
    assert session["cmd"][2] == build_prompt(task.prompt, "without", task)
    # its own runs of the suite: the pass count it saw is the grader's, before and after the edit
    assert session["before"][0] == 0 and session["after"][0] == 0
    assert pass_count(session["after"][1]) == record["tests_passed"] >= task.min_tests
    assert session["refused"][0] == 2 and "not available in this environment" in session["refused"][1]


@SHELL
@pytest.mark.parametrize("task_id", fetched(VITEST_TASK, JEST_TASK, TSX_TASK))
def test_the_agents_test_command_sees_the_pass_count_the_grader_sees(tmp_path, task_id):
    task = TASKS[task_id]
    repo = copy_fixture(task, tmp_path / "repo")
    write_node_shims(tmp_path / "bin", task.spec, repo)
    env = child_env(dict(os.environ), tmp_path / "bin", tmp_path / "cache")
    done = subprocess.run([task.spec.runner], cwd=repo, env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=600)
    verdict, _ids = run_node_suite(task, repo, 600)
    assert done.returncode == 0 and verdict["passed"]
    assert pass_count(done.stdout + done.stderr) == verdict["tests_passed"]


@SHELL
@pytest.mark.parametrize("task_id", fetched(VITEST_TASK, JEST_TASK, TSX_TASK))
def test_the_runner_and_tsc_leave_no_file_behind_in_the_agents_copy(tmp_path, task_id):
    task = TASKS[task_id]
    repo = copy_fixture(task, tmp_path / "repo")
    write_node_shims(tmp_path / "bin", task.spec, repo)
    env = child_env(dict(os.environ), tmp_path / "bin", tmp_path / "cache")
    for argv in ([task.spec.runner], ["tsc"]):
        subprocess.run(argv, cwd=repo, env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=600)
    made = []
    for here, dirs, files in os.walk(repo):
        dirs[:] = [d for d in dirs if d != "node_modules"]
        made += [str((Path(here) / f).relative_to(repo)) for f in files if not (task.fixture / Path(here).relative_to(repo) / f).exists()]
    assert made == []


# --------------------------------------------- the invalid-run rules apply to node runs, edited files are seen


def init_stream(status, denials=()):
    servers = [{"name": "ripple", "status": status}] if status else []
    events = [{"type": "system", "subtype": "init", "model": "m", "mcp_servers": servers, "tools": ["Bash"]}]
    events.append({"type": "result", "subtype": "success", "num_turns": 2, "usage": {"input_tokens": 1}, "permission_denials": list(denials)})
    return "\n".join(json.dumps(e) for e in events) + "\n"


class EditingAgent(NodeAgent):
    edit = None

    def __init__(self, cmd, cwd, env, stdin, stdout, stderr, start_new_session):
        if EditingAgent.edit:
            rel, text = EditingAgent.edit
            (Path(cwd) / rel).write_text(text)
        stdout.write(NodeAgent.streams["stream"].encode())
        self.returncode, self.pid = 0, 0


def run_without_grading(monkeypatch, tmp_path, task, mode, stream, edit=None):
    NodeAgent.streams = {"stream": stream}
    EditingAgent.edit = edit
    monkeypatch.setattr(run_mod, "subprocess", types.SimpleNamespace(**{**vars(subprocess), "Popen": EditingAgent}))
    monkeypatch.setattr(run_mod, "check_task", lambda task, repo: {"success": False, "failures": []})
    root = tmp_path / "root"
    root.mkdir()
    return run_mod.run_one(task, mode, 1, root, tmp_path / "unused-bin", args()), root


@pytest.mark.parametrize("task_id", fetched(VITEST_TASK))
def test_a_node_with_ripple_run_whose_server_never_connected_is_invalid(monkeypatch, tmp_path, task_id):
    record, root = run_without_grading(monkeypatch, tmp_path, TASKS[task_id], "with-ripple", init_stream("failed"))
    assert record["invalid"] == "ripple MCP server failed"
    config = json.loads((root / record["run_dir"] / "mcp.json").read_text())["mcpServers"]["ripple"]
    assert config["args"][:2] == ["serve", "--repo"] and "--lang" in config["args"] and config["args"][-1].endswith("/bin/vitest run")
    assert invalid_reason(parse_stream(init_stream("connected")), "with-ripple") is None


@pytest.mark.parametrize("task_id", fetched(VITEST_TASK))
def test_a_node_run_that_edited_typescript_is_not_read_as_having_changed_nothing(monkeypatch, tmp_path, task_id):
    denied = [{"tool_name": "Edit", "tool_input": {"file_path": "src/result-async.ts"}}]
    record, _ = run_without_grading(
        monkeypatch, tmp_path, TASKS[task_id], "without", init_stream(None, denied), edit=("src/result-async.ts", "export {}\n")
    )
    assert record["changed_files"] == ["src/result-async.ts"] and "invalid" not in record
    assert "-" in record["diff"] and "+export {}" in record["diff"]


@pytest.mark.parametrize("task_id", fetched(VITEST_TASK))
def test_a_node_run_that_changed_nothing_with_an_edit_tool_refused_is_still_blocked(monkeypatch, tmp_path, task_id):
    denied = [{"tool_name": "Edit", "tool_input": {"file_path": "src/result-async.ts"}}]
    record, _ = run_without_grading(monkeypatch, tmp_path, TASKS[task_id], "without", init_stream(None, denied))
    assert record["changed_files"] == [] and record["invalid"] == run_mod.BLOCKED
    assert blocked_reason(parse_stream(init_stream(None, denied)), ["src/a.ts"]) is None


# ------------------------------------------------------------------ the with-ripple arm fails fast


STUB_SERVER = """\
import json, sys
tools, answer = {tools!r}, {answer!r}
for line in sys.stdin:
    msg = json.loads(line)
    if "id" not in msg:
        continue
    method = msg["method"]
    if method == "initialize":
        result = {{"protocolVersion": "2025-06-18", "capabilities": {{"tools": {{}}}}, "serverInfo": {{"name": "stub", "version": "0"}}}}
    elif method == "tools/list":
        result = {{"tools": [{{"name": n, "inputSchema": {{"type": "object"}}}} for n in tools]}}
    elif method == "tools/call":
        result = {{"content": [{{"type": "text", "text": json.dumps(answer)}}]}}
    else:
        result = {{}}
    print(json.dumps({{"jsonrpc": "2.0", "id": msg["id"], "result": result}}), flush=True)
"""
ALL_TOOLS = ["callers", "callees", "impact", "path", "check"]
NOT_FOUND = {"error": "no symbol named 'x'", "suggestions": []}


def stub_entry(tmp_path, tools=ALL_TOOLS, answer=NOT_FOUND):
    script = tmp_path / "stub_server.py"
    script.write_text(STUB_SERVER.format(tools=tools, answer=answer))
    return {"command": sys.executable, "args": [str(script)], "env": {}}


def test_a_server_that_lists_every_tool_and_answers_cleanly_passes_the_handshake(tmp_path):
    assert node_agent.handshake_problems(stub_entry(tmp_path), tmp_path, timeout=20) == []


def test_a_handshake_that_does_not_list_ripples_tools_is_a_problem_naming_the_missing_ones(tmp_path):
    problems = node_agent.handshake_problems(stub_entry(tmp_path, tools=["callers", "callees", "impact"]), tmp_path, timeout=20)
    assert len(problems) == 1 and "does not list its tools" in problems[0] and "missing: check, path" in problems[0]
    empty = node_agent.handshake_problems(stub_entry(tmp_path, tools=[]), tmp_path, timeout=20)
    assert "does not list its tools" in empty[0]


def test_a_server_that_indexed_only_part_of_the_repo_is_a_problem(tmp_path):
    partial = {**NOT_FOUND, "warnings": ["TypeScript was not indexed: no parser"]}
    problems = node_agent.handshake_problems(stub_entry(tmp_path, answer=partial), tmp_path, timeout=20)
    assert len(problems) == 1 and "only part of the repository" in problems[0]


def test_a_server_that_never_answers_is_a_problem_and_is_not_left_running(tmp_path, monkeypatch):
    started = []
    real = node_agent.Handshake.__init__

    def spy(self, entry, cwd):
        real(self, entry, cwd)
        started.append(self.proc)

    monkeypatch.setattr(node_agent.Handshake, "__init__", spy)
    entry = {"command": sys.executable, "args": ["-c", "import time; time.sleep(60)"], "env": {}}
    problems = node_agent.handshake_problems(entry, tmp_path, timeout=1)
    assert len(problems) == 1 and "did not answer" in problems[0]
    assert started and started[0].poll() is not None  # killed, not left to the timeout of whoever started it


def test_a_server_that_cannot_be_started_is_a_problem(tmp_path):
    problems = node_agent.handshake_problems({"command": str(tmp_path / "nothing-here"), "args": [], "env": {}}, tmp_path, timeout=5)
    assert len(problems) == 1 and "could not be started" in problems[0]


def fake_interpreter(tmp_path, imports_ok: bool, server: Path | None = None) -> str:
    """A stand-in `python`: it answers the import check as asked and runs `-m ripple serve ...` as the stub server."""
    path = tmp_path / ("goodpy" if imports_ok else "badpy")
    check = "exit 0" if imports_ok else "echo \"ModuleNotFoundError: No module named 'tree_sitter'\" >&2; exit 1"
    run = f'exec {sys.executable} {server} "$@"' if server else "exit 3"
    path.write_text(f'#!/bin/sh\ncase "$*" in *" -c "*) {check} ;; esac\n{run}\n')
    path.chmod(0o755)
    return str(path)


@SHELL
@pytest.mark.parametrize("task_id", fetched(JEST_TASK))
def test_asking_for_the_ripple_arm_on_a_node_task_with_an_interpreter_that_cannot_index_typescript_stops_before_anything_starts(
    tmp_path, capsys, no_claude, task_id
):
    out = tmp_path / "x.json"
    with pytest.raises(SystemExit) as stop:
        run_mod.main(["--tasks", task_id, "--ripple-python", fake_interpreter(tmp_path, False), "--out", str(out)])
    err = capsys.readouterr().err
    assert stop.value.code == 2
    assert "cannot index TypeScript" in err and "No module named 'tree_sitter'" in err and "--ripple-python" in err
    assert "nothing was started" in err and not out.exists()


@SHELL
@pytest.mark.parametrize("task_id", fetched(JEST_TASK))
def test_a_server_whose_handshake_misses_tools_stops_the_batch_before_anything_starts(tmp_path, capsys, no_claude, task_id):
    server = tmp_path / "server.py"
    server.write_text(STUB_SERVER.format(tools=["callers"], answer=NOT_FOUND))
    python = fake_interpreter(tmp_path, True, server)
    with pytest.raises(SystemExit) as stop:
        run_mod.main(["--tasks", task_id, "--ripple-python", python, "--out", str(tmp_path / "x.json")])
    err = capsys.readouterr().err
    assert stop.value.code == 2 and "tsyringe: the ripple server's handshake does not list its tools" in err
    assert "missing: callees, check, impact, path" in err


@SHELL
@pytest.mark.parametrize("task_id", fetched(JEST_TASK))
def test_a_ready_ripple_arm_passes_the_check_and_the_dry_view_says_so(tmp_path, capsys, no_claude, task_id):
    server = tmp_path / "server.py"
    server.write_text(STUB_SERVER.format(tools=ALL_TOOLS, answer=NOT_FOUND))
    python = fake_interpreter(tmp_path, True, server)
    assert run_mod.main(["--dry", "--tasks", task_id, "--ripple-python", python]) == 0
    assert "  ok" in capsys.readouterr().out


@SHELL
@pytest.mark.parametrize("task_id", fetched(JEST_TASK))
def test_a_dry_view_with_a_problem_prints_it_and_exits_nonzero_without_starting_anything(tmp_path, capsys, no_claude, task_id):
    assert run_mod.main(["--dry", "--tasks", task_id, "--ripple-python", fake_interpreter(tmp_path, False)]) == 1
    assert "PROBLEM" in capsys.readouterr().out


def test_missing_node_or_runner_binaries_are_reported_per_repo(tmp_path, monkeypatch):
    spec = load_repos()["neverthrow"]
    monkeypatch.setattr(node_agent.shutil, "which", lambda name: None)
    problems = node_agent.node_problems(types.SimpleNamespace(name="demo", runner="vitest", node_modules=tmp_path / "nm"))
    assert "node is not on PATH" in problems
    assert any("demo: vitest is not in the installed dependencies" in p for p in problems)
    assert any("demo: tsc is not in the installed dependencies" in p for p in problems)
    assert spec.runner == "vitest"


# ------------------------------------------------- with a real interpreter that has the typescript extra


@needs_ts
@pytest.mark.parametrize("task_id", fetched(VITEST_TASK))
def test_the_real_server_lists_ripples_tools_for_a_node_repo_and_check_runs_the_graders_command(tmp_path, task_id):
    task = TASKS[task_id]
    python = ts_python()
    problems = node_agent.ripple_problems(
        [task], python, lambda t, repo, cache, bin_dir: run_mod.ripple_entry(t, repo, cache, bin_dir, python), copy_fixture
    )
    assert problems == []
    repo = copy_fixture(task, tmp_path / "repo")
    write_node_shims(tmp_path / "bin", task.spec, repo)
    entry = run_mod.ripple_entry(task, repo, tmp_path / "cache", tmp_path / "bin", python)
    client = node_agent.Handshake(entry, repo)
    try:
        client.request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}, 60)
        client.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        names = {t["name"] for t in client.request("tools/list", None, 60)["result"]["tools"]}
        assert names == set(ALL_TOOLS)
        called = client.request("tools/call", {"name": "check", "arguments": {"tests": ["tests/index.test.ts"]}}, 300)["result"]
        verdict = json.loads(called["content"][0]["text"])
        assert verdict["passed"] is True and "98 passed" in verdict["summary"], verdict
    finally:
        client.close()


@needs_ts
@pytest.mark.parametrize("task_id", fetched(VITEST_TASK, TSX_TASK))
def test_the_index_of_an_agents_copy_leaves_node_modules_out(tmp_path, task_id):
    task = TASKS[task_id]
    repo = copy_fixture(task, tmp_path / "repo")
    assert (repo / "node_modules").is_symlink()
    done = subprocess.run(
        [ts_python(), "-I", "-m", "ripple", "index", str(repo)],
        capture_output=True, text=True, env={**os.environ, "RIPPLE_CACHE_DIR": str(tmp_path / "cache")}, timeout=300,
    )  # fmt: skip
    text = done.stdout
    files = int(re.search(r"(\d+) files", text).group(1))
    assert "skipped directories (not indexed): node_modules" in text
    in_deps = sum(1 for _ in Path(task.spec.node_modules).rglob("*.ts"))
    assert files < 200 < in_deps, (files, in_deps)


# ------------------------------------------------------------------------------ the report


def test_node_task_results_group_and_print_like_any_other():
    def record(task, mode, ok):
        return {"task": task, "mode": mode, "success": ok, "num_turns": 3, "wall_s": 5.0, "usage": {"input_tokens": 10, "output_tokens": 2}}

    runs = [record(t.id, m, i % 2 == 0) for i, t in enumerate(NODE) for m in ("with-ripple", "without")]
    summary = summarize(runs)
    assert list(summary["tasks"]) == sorted(t.id for t in NODE) and summary["modes"]["with-ripple"]["runs"] == 9
    text = table(summary)
    for t in NODE:
        assert f"| {t.id} |" in text
    assert "| 20-neverthrow-rename-resultasync-combine | 1/1 | 1/1 |" in text
    assert "| 21-neverthrow-rename-resultasync-orelse | 0/1 | 0/1 |" in text


# ------------------------------------------------ --ripple-python is for the node tasks only


class Stop(Exception):
    """Raised by the stand-in for save() once the batch's meta is known: nothing past it runs."""


def meta_of(monkeypatch, argv):
    """The meta main() would write for this batch, with no agent started: run_one returns a stub record and save() is the
    first thing past it."""
    seen = {}

    def fake_run(cmd, *a, **k):
        if cmd[0] == "claude":
            return subprocess.CompletedProcess(cmd, 0, stdout="0.0.0 (test)\n", stderr="")
        return subprocess.run(cmd, *a, **k)

    def save(out, meta, runs):
        seen["meta"] = dict(meta)
        raise Stop

    monkeypatch.setattr(run_mod, "subprocess", types.SimpleNamespace(**{**vars(subprocess), "run": fake_run}))
    monkeypatch.setattr(run_mod, "run_one", lambda *a, **k: {"success": True, "wall_s": 0.0})
    monkeypatch.setattr(run_mod, "ripple_problems", lambda *a, **k: [])
    monkeypatch.setattr(run_mod, "save", save)
    with pytest.raises(Stop):
        run_mod.main(argv)
    return seen["meta"]


@pytest.mark.parametrize("task", PYTHON, ids=[t.id for t in PYTHON])
def test_a_python_task_is_launched_the_same_way_whether_or_not_ripple_python_is_given(task):
    repo, cache = Path("/path/to/repo"), Path("/path/to/cache")
    plain = mcp_config("with-ripple", repo, cache, task)
    given = mcp_config("with-ripple", repo, cache, task, "/ts/venv/bin/python", Path("/path/to/bin"))
    assert given == plain
    assert plain["mcpServers"]["ripple"]["command"] == str(RIPPLE_BIN) and "-I" not in plain["mcpServers"]["ripple"]["args"]


def test_a_python_task_with_no_task_object_is_launched_the_same_way_too():
    assert mcp_config("with-ripple", Path("/r"), Path("/c"), None, "/ts/venv/bin/python") == mcp_config("with-ripple", Path("/r"), Path("/c"))


@pytest.mark.parametrize("task_id", fetched(VITEST_TASK))
def test_a_node_task_is_launched_by_the_interpreter_named(task_id):
    entry = mcp_config("with-ripple", Path("/r"), Path("/c"), TASKS[task_id], "/ts/venv/bin/python", Path("/b"))["mcpServers"]["ripple"]
    assert entry["command"] == "/ts/venv/bin/python" and entry["args"][:3] == ["-I", "-m", "ripple"]


def test_run_one_starts_the_default_server_for_a_python_task_even_when_ripple_python_is_given(monkeypatch, tmp_path):
    task = PYTHON[0]

    class Idle:
        def __init__(self, cmd, cwd, env, stdin, stdout, stderr, start_new_session):
            self.returncode, self.pid = 0, 0

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(run_mod, "subprocess", types.SimpleNamespace(**{**vars(subprocess), "Popen": Idle}))
    monkeypatch.setattr(run_mod, "check_task", lambda task, repo: {"success": False, "failures": []})
    configs = {}
    for given in (None, "/ts/venv/bin/python"):
        root = tmp_path / f"root-{given is not None}"
        root.mkdir()
        record = run_mod.run_one(task, "with-ripple", 1, root, tmp_path / "unused-bin", types.SimpleNamespace(model="sonnet", timeout=60, ripple_python=given))
        configs[given] = json.loads((root / record["run_dir"] / "mcp.json").read_text())["mcpServers"]["ripple"]
    for entry in configs.values():  # the repo and cache paths differ by run, nothing else may
        entry["args"] = [a for a in entry["args"] if not a.startswith(str(tmp_path))]
        entry["env"] = {}
    assert configs[None] == configs["/ts/venv/bin/python"]
    assert configs[None]["command"] == str(RIPPLE_BIN)


def test_a_python_only_batch_ignores_ripple_python_says_so_once_and_records_what_was_given(monkeypatch, capsys, tmp_path):
    meta = meta_of(monkeypatch, ["--tasks", PYTHON[0].id, "--repeats", "1", "--ripple-python", "/ts/venv/bin/python", "--out", str(tmp_path / "x.json")])
    said = [line for line in capsys.readouterr().out.splitlines() if "--ripple-python" in line]
    assert len(said) == 1 and "ignored" in said[0] and "no node task" in said[0], said
    assert meta["ripple_python"] == "/ts/venv/bin/python" and meta["ripple_python_ignored"] is True


def test_a_python_only_batch_without_the_flag_has_the_meta_it_always_had(monkeypatch, capsys, tmp_path):
    meta = meta_of(monkeypatch, ["--tasks", PYTHON[0].id, "--repeats", "1", "--out", str(tmp_path / "x.json")])
    assert "ripple_python" not in meta and "ripple_python_ignored" not in meta and "node_commands" not in meta
    assert "--ripple-python" not in capsys.readouterr().out


@pytest.mark.parametrize("task_id", fetched(VITEST_TASK))
def test_a_node_batch_records_ripple_python_and_does_not_call_it_ignored(monkeypatch, capsys, tmp_path, task_id):
    meta = meta_of(monkeypatch, ["--tasks", task_id, "--repeats", "1", "--ripple-python", "/ts/venv/bin/python", "--out", str(tmp_path / "x.json")])
    assert meta["ripple_python"] == "/ts/venv/bin/python" and "ripple_python_ignored" not in meta
    assert "ignored" not in capsys.readouterr().out


@pytest.mark.parametrize("task_id", fetched(VITEST_TASK))
def test_a_mixed_batch_applies_the_interpreter_to_the_node_tasks_only(monkeypatch, capsys, tmp_path, task_id):
    meta = meta_of(monkeypatch, ["--tasks", f"{PYTHON[0].id},{task_id}", "--repeats", "1", "--ripple-python", "/ts/venv/bin/python", "--out", str(tmp_path / "x.json")])
    assert meta["ripple_python"] == "/ts/venv/bin/python" and "ripple_python_ignored" not in meta
    assert "ignored" not in capsys.readouterr().out


def test_the_dry_view_of_a_python_only_batch_says_the_flag_is_ignored_and_shows_the_default_server(capsys, no_claude):
    code = run_mod.main(["--dry", "--tasks", PYTHON[0].id, "--ripple-python", "/ts/venv/bin/python"])
    out = capsys.readouterr().out
    assert code == 0 and "ignored" in out and f"ripple server: {RIPPLE_BIN} serve" in out and "-I -m ripple" not in out
