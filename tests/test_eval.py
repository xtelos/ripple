"""The eval's own parts that do not call an agent: tasks, checks, parsing, tables.

The two parametrized tests prove each check has teeth before any agent is
graded by it: it fails on the untouched fixture (the task is not done yet)
and passes on the reference solution (the task can be done).
"""

import ast
import importlib.util
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from eval.check import HELPERS, count_passed, check_task, run_hidden
from eval.loader import REPO_ROOT, apply_solution, copy_fixture, load_task, load_tasks
from eval.repos import RepoSpec, load_repos
from eval.report import input_tokens, summarize, table
import eval.fetch as fetch
import eval.run as run_mod
from eval.run import (
    PREAMBLE,
    WITH_RIPPLE_LINE,
    agent_command,
    blocked_reason,
    build_prompt,
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
REAL = [t for t in TASKS if t.spec]
NO_CACHE = "needs the pinned repositories: python -m eval.fetch"


def graded(tasks):
    """Parametrize over tasks; a real-repo task is skipped on a machine that has not fetched its repo."""
    return [pytest.param(t, id=t.id, marks=pytest.mark.skipif(not t.available, reason=NO_CACHE)) for t in tasks]


def test_twenty_three_tasks_with_unique_ids_and_prompts():
    assert len(TASKS) == 23 and len(REAL) == 13
    assert len({t.id for t in TASKS}) == 23
    assert all(t.prompt.strip() and t.why.strip() for t in TASKS)


def test_real_repo_tasks_are_pinned_and_each_has_a_patch_solution():
    repos = load_repos()
    python_repos = {name for name, r in repos.items() if r.runtime == "python"}
    assert python_repos == {t.spec.name for t in REAL if not t.is_node} == {"rich", "werkzeug", "sqlglot"}
    assert {t.spec.name for t in REAL if t.is_node} == {"neverthrow", "superstruct"}  # tsyringe and react-pro-sidebar have no tasks yet
    assert all(len(r.sha) == 40 and r.url.startswith("https://") for r in repos.values())
    assert all(t.solution_patch.is_file() and not t.solution.exists() for t in REAL)
    assert all(t.fixture.name == f"{t.spec.name}-{t.spec.sha[:12]}" for t in REAL)
    assert all(not t.fixture.is_relative_to(REPO_ROOT) for t in REAL)  # the cache is outside this repository


def test_shims_for_a_real_repo_export_the_graders_test_environment(tmp_path):
    spec = next(t.spec for t in REAL if t.spec.name == "sqlglot")
    repo = tmp_path / "copy"
    run_mod.write_python_shims(tmp_path / "bin", "/venv/bin/python", spec.env(repo))
    text = (tmp_path / "bin" / "pytest").read_text()
    assert f"export PYTHONPATH={repo}" in text and "PYTEST_ADDOPTS" in text and "test_sqlglotc.py" in text
    assert "export PATH" not in text and text.rstrip().endswith('exec /venv/bin/python -m pytest "$@"')


def test_main_refuses_tasks_whose_repository_is_not_fetched(tmp_path, monkeypatch):
    monkeypatch.setenv("RIPPLE_EVAL_CACHE", str(tmp_path / "empty"))
    with pytest.raises(SystemExit):
        run_mod.main(["--tasks", REAL[0].id, "--repeats", "1", "--out", str(tmp_path / "x.json")])


def test_a_real_task_without_its_cache_says_how_to_get_it(tmp_path, monkeypatch):
    monkeypatch.setenv("RIPPLE_EVAL_CACHE", str(tmp_path / "empty"))
    task = load_tasks(only=[REAL[0].id])[0]
    assert not task.available
    with pytest.raises(FileNotFoundError, match="python -m eval.fetch"):
        copy_fixture(task, tmp_path / "repo")


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


def patch_files(patch: str) -> list[str]:
    return re.findall(r"(?m)^diff -ruN (?:-x \S+ )*a/(\S+) b/", patch)


def patch_without(patch: str, skip: int) -> str:
    """The patch with its skip-th file section left out."""
    sections = [x for x in re.split(r"(?m)^(?=diff -ruN )", patch) if x]
    return "".join(x for i, x in enumerate(sections) if i != skip)


@pytest.mark.parametrize("task", [pytest.param(t, id=t.id) for t in REAL])
def test_every_diff_section_of_a_solution_patch_is_seen_by_the_parser(task):
    """The drop-one test below loops over patch_files, so a header it cannot read would make
    it check nothing and still pass. Every section must be found, and there must be some."""
    patch = task.solution_patch.read_text()
    sections = re.findall(r"(?m)^diff ", patch)
    files = patch_files(patch)
    assert len(files) == len(sections) > 0, f"{task.id}: {len(files)} files parsed, {len(sections)} sections"
    assert len(files) == len(set(files))


@pytest.mark.parametrize("task", graded(REAL))
def test_a_solution_that_misses_any_one_file_fails_the_hidden_check(task, tmp_path, capsys):
    """Every file the reference solution touches holds a caller or a definition the hidden
    check must notice if it is left alone, so the check is about the whole caller set."""
    patch = task.solution_patch.read_text()
    files = patch_files(patch)
    assert files, f"{task.id}: the patch parsed to no files, so nothing would be checked"
    survivors = []
    omissions = 0
    for index, name in enumerate(files):
        repo = copy_fixture(task, tmp_path / f"repo{index}")
        partial = tmp_path / f"partial{index}.patch"
        partial.write_text(patch_without(patch, index))
        done = subprocess.run(
            ["git", "apply", "--whitespace=nowarn", str(partial)], cwd=repo, capture_output=True, text=True
        )
        assert done.returncode == 0, done.stderr
        omissions += 1
        if run_hidden(task, repo)["passed"]:
            survivors.append(name)
        shutil.rmtree(repo)
    with capsys.disabled():
        print(f"\n{task.id}: {omissions} omissions, {len(survivors)} passed the hidden check", end="")
    assert omissions == len(files)
    assert survivors == []


def find_function(source: str, name: str, cls: str | None = None) -> ast.FunctionDef:
    body = ast.parse(source).body
    if cls:
        body = next(n for n in body if isinstance(n, ast.ClassDef) and n.name == cls).body
    return next(n for n in body if isinstance(n, ast.FunctionDef) and n.name == name)


def delete_function(path: Path, name: str, cls: str | None = None) -> None:
    """Remove a test function (top level, or a method of cls), its decorators included."""
    source = path.read_text()
    node = find_function(source, name, cls)
    first = min([node.lineno] + [d.lineno for d in node.decorator_list])
    lines = source.splitlines(keepends=True)
    path.write_text("".join(lines[: first - 1] + lines[node.end_lineno :]))


def gut_function(path: Path, name: str) -> None:
    """Keep a test function and its name but replace its body with `pass`, so it still passes."""
    source = path.read_text()
    node = find_function(source, name)
    lines = source.splitlines(keepends=True)
    path.write_text("".join(lines[: node.body[0].lineno - 1] + ["    pass\n"] + lines[node.end_lineno :]))


def real_task(task_id: str):
    task = next(t for t in REAL if t.id == task_id)
    if not task.available:
        pytest.skip(NO_CACHE)
    return task


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    assert text.count(old) == 1, f"{path}: {old!r} found {text.count(old)} times"
    path.write_text(text.replace(old, new))


def apply_solution_without(task, repo, test_file):
    """The reference solution minus its edit to test_file: the agent left that file's callers alone."""
    patch = task.solution_patch.read_text()
    partial = repo.parent / "partial.patch"
    partial.write_text(patch_without(patch, patch_files(patch).index(test_file)))
    done = subprocess.run(["git", "apply", "--whitespace=nowarn", str(partial)], cwd=repo, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


def test_a_stale_read_whose_failing_tests_are_deleted_does_not_pass_even_in_untouched_test_files(tmp_path):
    """Task 18's solution edits no test file, so only a floor with 57 tests of slack once guarded
    it. The reference solution with one read of the old parameter left in the 4-part-name
    branch (a NameError on `FROM server.db.dbo.t`), and the three tests that reach it deleted."""
    task = real_task("18-sqlglot-rename-parse-table-parts-schema")
    repo = copy_fixture(task, tmp_path / "repo")
    apply_solution(task, repo)
    replace_once(
        repo / "sqlglot/parser.py",
        "exp.Dot(this=table, expression=self._parse_table_part(schema=is_schema))",
        "exp.Dot(this=table, expression=self._parse_table_part(schema=schema))",
    )
    delete_function(repo / "tests/dialects/test_tsql.py", "test_bare_table_hints", "TestTSQL")
    delete_function(repo / "tests/test_expressions.py", "test_table_name", "TestExprs")
    delete_function(repo / "tests/test_optimizer.py", "test_simplify", "TestOptimizer")
    verdict = check_task(task, repo)
    assert not verdict["success"], verdict
    assert not verdict["suite_passed"], verdict  # the suite itself, not only the hidden check, catches it
    assert any("test_table_name" in f for f in verdict["failures"]), verdict


@pytest.mark.parametrize(
    "path, old, new",
    [
        (
            "sqlglot/parser.py",
            "exp.Dot(this=table, expression=self._parse_table_part(schema=is_schema))",
            "exp.Dot(this=table, expression=self._parse_table_part(schema=schema))",
        ),
        (
            "sqlglot/parsers/tsql.py",
            "is_schema=is_schema,\n            is_db_reference=is_db_reference,",
            "is_schema=schema,\n            is_db_reference=is_db_reference,",
        ),
    ],
    ids=["base-body-read", "override-forwarded-value"],
)
def test_a_stale_read_of_the_renamed_parameter_inside_a_method_fails_the_hidden_check(path, old, new, tmp_path):
    """The prompt asks for the code that reads the parameter to change too. A read left behind is a
    NameError at run time, and the check must see it without the suite's help."""
    task = real_task("18-sqlglot-rename-parse-table-parts-schema")
    repo = copy_fixture(task, tmp_path / "repo")
    apply_solution(task, repo)
    assert run_hidden(task, repo)["passed"]
    replace_once(repo / path, old, new)
    hidden = run_hidden(task, repo)
    assert not hidden["passed"], hidden


def test_a_failing_test_gutted_to_pass_does_not_hide_a_source_bug(tmp_path):
    """The reference solution for task 11, then a source change the test of that code catches
    (the style a live render is given is dropped), and that test's body replaced with `pass`."""
    task = real_task("11-rich-rename-style-parse")
    repo = copy_fixture(task, tmp_path / "repo")
    apply_solution(task, repo)
    replace_once(repo / "rich/live_render.py", "style = console.get_style(self.style)", 'style = console.get_style("none")')
    gut_function(repo / "tests/test_live_render.py", "test_rich_console")
    verdict = check_task(task, repo)
    assert not verdict["success"], verdict
    assert not verdict["suite_passed"], verdict


def test_a_test_gutted_to_pass_is_marked_down_though_the_source_is_right(tmp_path):
    """The literal round-2 repro: task 11's reference solution minus its edit to
    tests/test_live_render.py (the source is complete), then the test that fails for the stale
    caller emptied to `pass`. The tests are replaced, so nothing runs wrong, but the agent hollowed
    out a test the reference edited."""
    task = real_task("11-rich-rename-style-parse")
    repo = copy_fixture(task, tmp_path / "repo")
    apply_solution_without(task, repo, "tests/test_live_render.py")
    gut_function(repo / "tests/test_live_render.py", "test_rich_console")
    verdict = check_task(task, repo)
    assert not verdict["success"], verdict
    assert any("test_live_render.py::test_rich_console" in t for t in verdict["lost_tests"]), verdict


@pytest.mark.parametrize(
    "task_id, test_file, function",
    [
        ("15-werkzeug-rename-mapadapter-match", "tests/middleware/test_proxy_fix.py", "test_proxy_fix"),
        ("11-rich-rename-style-parse", "tests/test_live_render.py", "test_rich_console"),
    ],
)
def test_deleting_the_one_failing_test_in_a_real_repo_does_not_pass(task_id, test_file, function, tmp_path):
    """The agent leaves a caller in one test file and deletes the test that now fails."""
    task = real_task(task_id)
    repo = copy_fixture(task, tmp_path / "repo")
    apply_solution_without(task, repo, test_file)
    delete_function(repo / test_file, function)
    verdict = check_task(task, repo)
    assert not verdict["success"], verdict
    assert verdict["tests_lost"] > 0 and any(function in t for t in verdict["lost_tests"]), verdict


def test_hollowing_out_a_test_the_reference_edited_is_marked_down_on_a_fixture_task(tmp_path):
    task = next(t for t in TASKS if t.id == "04-ledger-rename-invoice-total")
    repo = copy_fixture(task, tmp_path / "repo")
    apply_solution(task, repo)
    assert check_task(task, repo)["success"]
    gut_function(repo / "tests/test_invoices.py", "test_credit_note_total_is_negative")
    verdict = check_task(task, repo)
    assert not verdict["success"] and verdict["suite_passed"] and verdict["hidden_passed"], verdict
    assert verdict["lost_tests"] == ["tests/test_invoices.py::test_credit_note_total_is_negative (dropped amount_due)"]


def test_a_test_file_left_with_a_stale_caller_is_still_a_miss(tmp_path):
    """Replacing the agent's tests must not let it skip the callers the prompt asks it to update:
    the reference solution minus its edit to tests/test_live_render.py still calls the old name."""
    task = real_task("11-rich-rename-style-parse")
    repo = copy_fixture(task, tmp_path / "repo")
    apply_solution_without(task, repo, "tests/test_live_render.py")
    verdict = check_task(task, repo)
    assert not verdict["success"], verdict
    assert not verdict["hidden_passed"], verdict


FLIP_FAILURES = '''import pytest

@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if report.when == "call" and report.failed:
        report.outcome = "passed"
        report.longrepr = None
'''


def test_a_root_conftest_that_turns_failures_into_passes_does_not_pass_a_real_repo(tmp_path):
    """The untouched task 12 checkout, with one root conftest.py that rewrites every failing test
    to a pass: the suite, the required ids and the hidden check all used to see it."""
    task = real_task("12-rich-rename-cell-len")
    repo = copy_fixture(task, tmp_path / "repo")
    (repo / "conftest.py").write_text(FLIP_FAILURES)
    verdict = check_task(task, repo)
    assert not verdict["success"] and not verdict["hidden_passed"], verdict


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


def load_check_helpers():
    spec = importlib.util.spec_from_file_location("check_helpers_under_test", HELPERS)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_a_file_that_does_not_parse_is_a_failure_unless_the_repo_lists_it(tmp_path):
    helpers = load_check_helpers()
    (tmp_path / "ok.py").write_text("x = 1\n")
    (tmp_path / "broken.py").write_text("def f(:\n")
    with pytest.raises(AssertionError, match="broken.py does not parse"):
        list(helpers.sources(tmp_path, allowed=set()))
    seen = [rel for rel, _tree in helpers.sources(tmp_path, allowed={"broken.py"})]
    assert seen == ["ok.py"]


def test_the_pinned_repos_list_no_unparseable_files_and_a_spec_can_list_some(tmp_path):
    assert all(spec.unparseable == () for spec in load_repos().values())
    data = {"url": "u", "sha": "a" * 40, "python": "3.12", "tests": "tests", "deps": [], "unparseable": ["x/data.py"]}
    (tmp_path / "repos.json").write_text(json.dumps({"r": data}))
    assert load_repos(tmp_path / "repos.json")["r"].unparseable == ("x/data.py",)


def test_breaking_a_file_that_holds_stale_callers_does_not_hide_them(tmp_path):
    """The reference solution minus its edit to examples/log.py leaves six Style.parse calls
    there. A syntax error in that file must not take them out of the scan."""
    task = next(t for t in REAL if t.id == "11-rich-rename-style-parse")
    if not task.available:
        pytest.skip(NO_CACHE)
    repo = copy_fixture(task, tmp_path / "repo")
    apply_solution_without(task, repo, "examples/log.py")
    assert not run_hidden(task, repo)["passed"]  # the six calls are found while the file parses
    with (repo / "examples" / "log.py").open("a") as f:
        f.write("\ndef broken(:\n")
    hidden = run_hidden(task, repo)
    assert not hidden["passed"], hidden
    assert hidden["failures"] == ["FAILED _eval_check/test_eval_hidden.py::test_no_call_to_the_old_name_is_left_anywhere"]


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


def stream_with_denials(denials, tools=("Read",)):
    events = [{"type": "system", "subtype": "init", "model": "claude-x", "mcp_servers": [], "tools": []}]
    events += [{"type": "assistant", "message": {"content": [{"type": "tool_use", "name": t}]}} for t in tools]
    events.append({"type": "result", "subtype": "success", "num_turns": 3, "usage": {"input_tokens": 1},
                   "permission_denials": denials, "result": "I could not edit anything"})  # fmt: skip
    return "\n".join(json.dumps(e) for e in events) + "\n"


def refused(tool, **tool_input):
    return parse_stream(stream_with_denials([{"tool_name": tool, "tool_input": tool_input}]))


def test_a_refused_edit_tool_with_no_change_is_invalid_not_a_failure():
    for tool in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
        reason = blocked_reason(refused(tool, file_path="x.py"), [])
        assert reason and "edit tool" in reason, tool


def test_a_refused_shell_rewrite_with_no_change_is_a_real_failure():
    """The prompt told the agent not to edit through the shell, so trying sed -i anyway is its own doing."""
    assert blocked_reason(refused("Bash", command="sed -i s/a/b/ x.py"), []) is None


def test_a_refused_read_only_command_with_no_change_is_a_real_failure():
    assert blocked_reason(refused("Bash", command="python -c 'print(1)'"), []) is None


def test_a_run_that_changed_files_is_never_invalid_for_its_refusals():
    for parsed in (refused("Edit", file_path="x.py"), refused("Bash", command="sed -i s/a/b/ x.py")):
        assert blocked_reason(parsed, ["rich/style.py"]) is None


def test_a_run_with_no_refusal_or_no_result_is_an_ordinary_failure():
    assert blocked_reason(parse_stream(stream_with_denials([])), []) is None
    assert blocked_reason({}, []) is None


def test_an_edit_refusal_past_the_listed_ones_still_counts():
    shell = [{"tool_name": "Bash", "tool_input": {"command": f"c{i}"}} for i in range(30)]
    parsed = parse_stream(stream_with_denials(shell + [{"tool_name": "Edit", "tool_input": {}}]))
    assert len(parsed["permission_denied"]) == 20 and blocked_reason(parsed, []) is not None


def test_a_blocked_run_is_left_out_of_the_success_rate_but_counted_and_printed():
    blocked = {**run("t1", "without", False, 8, 100), "invalid": "no edits made, 5 tool calls denied by permissions"}
    runs = [run("t1", "with-ripple", True, 4, 100), blocked, run("t1", "without", True, 6, 200)]
    summary = summarize(runs)
    assert summary["modes"]["without"]["runs"] == 1 and summary["modes"]["without"]["success_rate"] == 1.0
    assert summary["modes"]["without"]["invalid"] == 1 and summary["modes"]["with-ripple"]["invalid"] == 0
    assert summary["invalid"] == 1
    text = table(summary)
    assert "1 run(s) marked invalid" in text and "no edits made, 5 tool calls denied" in text
    assert "| t1 | 1/1 | 1/1 (+1 invalid) |" in text


def test_a_mode_whose_every_run_was_invalid_still_appears_in_the_table():
    runs = [run("t1", "with-ripple", True, 4, 100), {**run("t1", "without", False, 8, 1), "invalid": "blocked"}]
    summary = summarize(runs)
    assert summary["modes"]["without"]["runs"] == 0 and summary["modes"]["without"]["invalid"] == 1
    assert "| without | 0 | n/a |" in table(summary)


def test_both_modes_get_the_same_prompt_but_for_the_one_ripple_line():
    task = "Rename `Style.parse` to `Style.from_string`."
    plain, with_ripple = build_prompt(task, "without"), build_prompt(task, "with-ripple")
    assert plain != with_ripple
    assert with_ripple.replace(WITH_RIPPLE_LINE + "\n\n", "", 1) == plain
    assert plain.endswith(task) and with_ripple.endswith(task)
    assert PREAMBLE in plain and PREAMBLE in with_ripple
    assert "ripple" not in plain.lower()
    # the shared guidance names the Edit tool and the shell restriction
    assert "Edit" in PREAMBLE and "shell" in PREAMBLE.lower()
    assert chr(0x2014) not in plain + with_ripple


def test_the_ripple_line_names_the_tools_and_does_not_tell_the_agent_to_use_them():
    assert all(name in WITH_RIPPLE_LINE for name in ("impact", "callers", "callees", "path", "check"))
    assert not re.search(r"\b(must|should|always|first|before)\b", WITH_RIPPLE_LINE, re.I)


FAKE_KEY = "sk-ant-abcdefghijklmnop1234"  # pragma: allowlist secret
FAKE_TOKEN = "ghp_abc123"  # pragma: allowlist secret


def test_denied_tools_are_recorded_with_truncated_inputs_and_no_secrets():
    long_cmd = "echo " + "x" * 5000
    denials = [
        {"tool_name": "Bash", "tool_use_id": "t1", "tool_input": {"command": "sed -i s/a/b/ rich/style.py"}},
        {"tool_name": "Bash", "tool_use_id": "t2", "tool_input": {"command": long_cmd}},
        {"tool_name": "Bash", "tool_use_id": "t3",
         "tool_input": {"command": f"ANTHROPIC_API_KEY={FAKE_KEY} curl x; GH_TOKEN={FAKE_TOKEN} ls"}},
    ]  # fmt: skip
    parsed = parse_stream(stream_with_denials(denials))
    assert parsed["permission_denials"] == 3
    shown = parsed["permission_denied"]
    assert [d["tool"] for d in shown] == ["Bash", "Bash", "Bash"]
    assert shown[0]["input"] == {"command": "sed -i s/a/b/ rich/style.py"}
    assert len(shown[1]["input"]["command"]) <= 300
    secrets_text = json.dumps(shown[2])
    assert FAKE_KEY not in secrets_text and FAKE_TOKEN not in secrets_text
    assert "tool_use_id" not in shown[0]


def test_only_the_first_denials_are_listed_in_full():
    parsed = parse_stream(stream_with_denials([{"tool_name": "Bash", "tool_input": {"command": f"c{i}"}} for i in range(40)]))
    assert parsed["permission_denials"] == 40 and len(parsed["permission_denied"]) == 20


class FakeAgent:
    """Stands in for subprocess.Popen(claude ...): writes a canned stream to the run's stdout file."""

    prompts: list = []
    stream = ""

    edits = False  # when set, the "agent" adds a .py file to its working copy

    def __init__(self, cmd, cwd, env, stdin, stdout, stderr, start_new_session):
        FakeAgent.prompts.append(cmd[2])
        if FakeAgent.edits:
            (Path(cwd) / "agent_made_this.py").write_text("x = 1\n")
        stdout.write(FakeAgent.stream.encode())
        self.returncode = 0
        self.pid = 0

    def wait(self, timeout=None):
        return 0


def fake_run(monkeypatch, tmp_path, stream, mode):
    FakeAgent.prompts, FakeAgent.stream = [], stream
    monkeypatch.setattr(run_mod.subprocess, "Popen", FakeAgent)
    monkeypatch.setattr(run_mod, "check_task", lambda task, repo: {"success": False, "failures": []})
    args = type("Args", (), {"model": "sonnet", "timeout": 5})()
    task = next(t for t in TASKS if not t.spec)
    root = tmp_path / "root"
    root.mkdir()
    return run_mod.run_one(task, mode, 1, root, tmp_path / "bin", args), task


def test_run_one_marks_a_run_whose_edit_tool_was_refused_invalid_and_keeps_what_was_denied(monkeypatch, tmp_path):
    stream = stream_with_denials([{"tool_name": "Edit", "tool_input": {"file_path": "x.py", "old_string": "a"}}])
    record, task = fake_run(monkeypatch, tmp_path, stream, "without")
    assert record["changed_files"] == []
    assert "edit tool" in record["invalid"]
    assert record["permission_denied"] == [{"tool": "Edit", "input": {"file_path": "x.py", "old_string": "a"}}]
    assert FakeAgent.prompts == [build_prompt(task.prompt, "without")]


def test_run_one_counts_a_refused_shell_rewrite_with_no_edit_as_a_failure(monkeypatch, tmp_path):
    stream = stream_with_denials([{"tool_name": "Bash", "tool_input": {"command": "sed -i s/a/b/ x.py"}}])
    record, _ = fake_run(monkeypatch, tmp_path, stream, "without")
    assert record["changed_files"] == [] and "invalid" not in record and record["success"] is False
    assert record["permission_denied"][0]["tool"] == "Bash"  # still recorded, for diagnosis


def test_run_one_counts_a_run_with_no_denial_and_no_edit_as_a_failure(monkeypatch, tmp_path):
    record, _ = fake_run(monkeypatch, tmp_path, stream_with_denials([]), "without")
    assert "invalid" not in record and record["success"] is False


def test_run_one_counts_a_run_that_edited_despite_a_refused_edit_tool(monkeypatch, tmp_path):
    FakeAgent.edits = True
    try:
        stream = stream_with_denials([{"tool_name": "Edit", "tool_input": {"file_path": "x.py"}}])
        record, _ = fake_run(monkeypatch, tmp_path, stream, "without")
    finally:
        FakeAgent.edits = False
    assert record["changed_files"] == ["agent_made_this.py"] and "invalid" not in record
