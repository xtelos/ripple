"""The grader's node path (eval/check_node.py): the same anti-cheat design as the Python one, over
a vitest or jest repository, proved by a real pilot task (20-neverthrow-rename-resultasync-combine).

Everything that needs the fetched neverthrow checkout (python -m eval.fetch) skips when it is absent,
the way tests/test_eval.py does for the real Python repositories. The cheat tests are real check_task
runs: the agent's tree is built, the grader runs the hidden test and the suite over a copy of it.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

import eval.check_node as node
from eval.check import check_task, neutralize, restore_tests, run_hidden, weakened_tests
from eval.loader import Task, apply_solution, copy_fixture, load_tasks
from eval.repos import load_repos

PILOT_ID = "20-neverthrow-rename-resultasync-combine"
NEVERTHROW = load_repos()["neverthrow"]
TASKS = {t.id: t for t in load_tasks()}
PILOT = TASKS.get(PILOT_ID)
needs_nt = pytest.mark.skipif(not NEVERTHROW.available, reason="needs the pinned neverthrow checkout: python -m eval.fetch")
needs_pilot = pytest.mark.skipif(PILOT is None or not PILOT.available, reason="needs the pilot task and neverthrow: python -m eval.fetch")
needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="needs node")

# ---------------------------------------------------------------------------------------- the reports


VITEST_REPORT = {
    "numTotalTests": 3,
    "numPassedTests": 2,
    "numFailedTests": 1,
    "success": False,
    "testResults": [
        {
            "name": "/work/repo/tests/a.test.ts",
            "status": "failed",
            "assertionResults": [
                {"ancestorTitles": ["Result"], "fullName": "Result adds", "status": "passed", "title": "adds", "failureMessages": []},
                {"ancestorTitles": ["Result"], "fullName": "Result maps", "status": "failed", "title": "maps",
                 "failureMessages": ["AssertionError: expected 1 to be 2\n    at stack"]},
                {"ancestorTitles": [], "fullName": "later", "status": "passed", "title": "later", "failureMessages": []},
            ],
        }
    ],
}


def write_report(tmp_path, data):
    path = tmp_path / "report.json"
    path.write_text(json.dumps(data))
    return path


def test_a_vitest_report_gives_counts_ids_and_the_message_of_each_failure(tmp_path):
    verdict, ids = node.read_report(write_report(tmp_path, VITEST_REPORT), Path("/work/repo"), 1, "")
    assert ids == {"tests/a.test.ts::Result adds", "tests/a.test.ts::later"}
    assert verdict["passed"] is False and verdict["tests_passed"] == 2
    assert verdict["summary"] == "2 passed, 1 failed"
    assert verdict["failures"] == ["FAILED tests/a.test.ts::Result maps: AssertionError: expected 1 to be 2"]


def test_a_passing_report_needs_a_zero_exit_and_at_least_one_test(tmp_path):
    ok = {"numTotalTests": 1, "numPassedTests": 1, "numFailedTests": 0, "success": True, "testResults": [
        {"name": "/r/tests/a.test.ts", "status": "passed", "assertionResults": [{"fullName": "x", "status": "passed", "failureMessages": []}]}]}
    assert node.read_report(write_report(tmp_path, ok), Path("/r"), 0, "")[0]["passed"] is True
    assert node.read_report(write_report(tmp_path, ok), Path("/r"), 1, "")[0]["passed"] is False  # the runner itself said it failed
    empty = {"numTotalTests": 0, "numPassedTests": 0, "numFailedTests": 0, "success": True, "testResults": []}
    verdict, ids = node.read_report(write_report(tmp_path, empty), Path("/r"), 0, "")
    assert verdict["passed"] is False and ids == frozenset() and "no tests" in verdict["summary"]


def test_a_suite_that_could_not_load_is_a_failure_with_its_message(tmp_path):
    broken = {"numTotalTests": 0, "numPassedTests": 0, "numFailedTests": 0, "success": False, "testResults": [
        {"name": "/r/tests/a.test.ts", "status": "failed", "message": "SyntaxError: Unexpected token\n at x", "assertionResults": []}]}
    verdict, ids = node.read_report(write_report(tmp_path, broken), Path("/r"), 1, "")
    assert verdict["passed"] is False and ids == frozenset()
    assert verdict["failures"] == ["ERROR tests/a.test.ts: SyntaxError: Unexpected token"]


def test_a_runner_that_crashes_before_reporting_is_a_failed_suite_with_a_clear_summary(tmp_path):
    verdict, ids = node.read_report(tmp_path / "never-written.json", Path("/r"), 1, "warming up\nError: cannot find config 'x'\n")
    assert verdict["passed"] is False and ids == frozenset()
    assert verdict["summary"] == "the runner exited 1 without writing a report: Error: cannot find config 'x'"
    garbled = tmp_path / "garbled.json"
    garbled.write_text("{not json")
    assert "without writing a report" in node.read_report(garbled, Path("/r"), 0, "")[0]["summary"]


def test_a_test_that_ran_twice_under_one_name_is_two_ids_and_the_repo_path_is_not_in_an_id(tmp_path):
    report = {"numTotalTests": 3, "numPassedTests": 3, "numFailedTests": 0, "success": True, "testResults": [
        {"name": "/work/repo/tests/a.test.ts", "status": "passed", "assertionResults": [
            {"fullName": "same", "status": "passed", "failureMessages": []},
            {"fullName": "same", "status": "passed", "failureMessages": []},
            {"fullName": "writes /work/repo/tmp/out", "status": "passed", "failureMessages": []}]}]}
    _, ids = node.read_report(write_report(tmp_path, report), Path("/work/repo"), 0, "")
    assert ids == {"tests/a.test.ts::same", "tests/a.test.ts::same #2", "tests/a.test.ts::writes <repo>/tmp/out"}


def test_skipped_and_todo_tests_are_not_passes(tmp_path):
    report = {"numTotalTests": 3, "numPassedTests": 1, "numFailedTests": 0, "success": True, "testResults": [
        {"name": "/r/t.test.ts", "status": "passed", "assertionResults": [
            {"fullName": "a", "status": "passed", "failureMessages": []},
            {"fullName": "b", "status": "skipped", "failureMessages": []},
            {"fullName": "c", "status": "todo", "failureMessages": []}]}]}
    verdict, ids = node.read_report(write_report(tmp_path, report), Path("/r"), 0, "")
    assert ids == {"t.test.ts::a"} and verdict["tests_passed"] == 1


def test_a_run_past_the_timeout_is_a_failed_suite(monkeypatch, tmp_path):
    monkeypatch.setattr(node, "run_command", lambda *a, **k: (None, ""))
    spec = NEVERTHROW
    task = Task(id="t", fixture=tmp_path, prompt="p", min_tests=1, why="w", dir=tmp_path, spec=spec)
    verdict, ids = node.run_node_suite(task, tmp_path, 7)
    assert verdict["passed"] is False and ids == frozenset() and verdict["summary"] == "the runner was killed after 7s"


# ---------------------------------------------------------------------- which files the runner reads


@pytest.mark.parametrize(
    "name, guarded",
    [
        ("vitest.config.ts", True), ("vitest.config.mts", True), ("vitest.config.cjs", True), ("vitest.workspace.ts", True),
        ("vitest.setup.ts", True), ("vite.config.js", True), ("jest.config.js", True), ("jest.config.json", True),
        ("jest.setup.ts", True), ("package.json", True), ("tsconfig.json", True), ("tsconfig.tests.json", True),
        ("jsconfig.json", True), ("babel.config.cjs", True), (".babelrc", True), (".babelrc.json", True), (".swcrc", True),
        (".env", True), (".env.test", True), ("__mocks__", True),
        ("index.ts", False), ("package-lock.json", False), ("README.md", False), ("vitest.d.ts", False), ("tsconfig-paths.ts", False),
    ],
)
def test_the_names_that_decide_what_the_runner_loads_are_guarded(name, guarded):
    assert node.is_guarded(name) is guarded


@pytest.mark.parametrize(
    "rel, is_test",
    [
        ("tests/index.test.ts", True), ("src/a.spec.tsx", True), ("src/a.tests.ts", True), ("src/__tests__/x.ts", True),
        ("src/a.test.mjs", True), ("src/index.ts", False), ("src/latest.ts", False), ("docs/test.md", False),
    ],
)
def test_a_file_the_runner_would_collect_is_found_wherever_it_lives(rel, is_test):
    assert node.is_test_file(rel) is is_test


def lay(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    return root


def a_task(tmp_path, spec=NEVERTHROW) -> Task:
    return Task(id="t", fixture=tmp_path / "fixture", prompt="p", min_tests=1, why="w", dir=tmp_path, spec=spec)


COLOCATED = {
    "package.json": '{"name": "demo"}',
    "vitest.config.ts": "export default { test: { setupFiles: ['./setupTests.ts'], include: ['src/**/*.test.tsx'] } }",
    "setupTests.ts": "export {}\n",
    "src/Menu.tsx": "export const Menu = 1\n",
    "src/Menu.test.tsx": "test('menu', () => {})\n",
    "tests/helpers.ts": "export const h = 1\n",
}


def test_config_package_json_mocks_and_the_setup_files_the_config_names_go_back_to_the_references(tmp_path):
    task = a_task(tmp_path)
    reference = lay(tmp_path / "reference", COLOCATED | {"__mocks__/dep.ts": "export default 1\n", "tsconfig.json": "{}"})
    agent = lay(tmp_path / "agent", {k: v + "// agent\n" if not k.endswith("json") else v.replace("demo", "evil") for k, v in COLOCATED.items()})
    lay(agent, {"vite.config.mjs": "export default { test: { exclude: ['**'] } }", "jest.config.cjs": "module.exports = {}", ".babelrc": "{}",
                "__mocks__/dep.ts": "export default 'evil'\n", "src/__mocks__/menu.ts": "export {}\n", "tests/tsconfig.json": "{}",
                ".swcrc": "{}", "jsconfig.json": "{}"})
    neutralize(task, agent, reference)
    for rel in ("package.json", "vitest.config.ts", "setupTests.ts", "__mocks__/dep.ts", "tsconfig.json"):
        assert (agent / rel).read_text() == (reference / rel).read_text(), rel
    for rel in ("vite.config.mjs", "jest.config.cjs", ".babelrc", "src/__mocks__", ".swcrc", "jsconfig.json", "tests/tsconfig.json"):
        assert not (agent / rel).exists(), rel  # the reference has none, so none runs
    assert (agent / "src/Menu.tsx").read_text().endswith("// agent\n")  # the agent's source is its own work


def test_a_setup_file_the_config_names_is_found_through_rootdir_and_the_config_directory(tmp_path):
    task = a_task(tmp_path)
    reference = lay(tmp_path / "reference", {
        "package.json": "{}",
        "test/jest.config.js": 'module.exports = { rootDir: "..", setupFilesAfterEnv: ["<rootDir>/test/bootstrap.ts"], globalSetup: "./global.js", moduleNameMapper: {"^x$": "<rootDir>/src/x.ts"} }',
        "test/bootstrap.ts": "export {}\n", "test/global.js": "module.exports = 1\n", "src/x.ts": "export const x = 1\n"})
    agent = lay(tmp_path / "agent", {"package.json": "{}", "test/jest.config.js": "module.exports = {}", "test/bootstrap.ts": "evil()\n",
                                     "test/global.js": "evil()\n", "src/x.ts": "export const x = 'agent'\n"})
    neutralize(task, agent, reference)
    assert (agent / "test/bootstrap.ts").read_text() == "export {}\n" and (agent / "test/global.js").read_text() == "module.exports = 1\n"
    assert (agent / "src/x.ts").read_text() == "export const x = 'agent'\n"  # a module the config only maps to is source, not a hook


def test_a_root_file_or_package_named_like_a_dependency_is_removed_unless_the_reference_has_it(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    monkeypatch.setenv("RIPPLE_EVAL_CACHE", str(cache))
    spec = load_repos()["neverthrow"]
    task = a_task(tmp_path, spec)
    for pkg in ("vitest", "lodash", "typescript"):
        (spec.node_modules / pkg).mkdir(parents=True, exist_ok=True)
        (spec.node_modules / pkg / "package.json").write_text("{}")
    reference = lay(tmp_path / "reference", {"package.json": "{}", "src/index.ts": "", "lodash.js": "// the reference's own\n"})
    agent = lay(tmp_path / "agent", {"package.json": "{}", "src/index.ts": "", "lodash.js": "// agent\n", "vitest.js": "module.exports = {}",
                                     "vitest/index.js": "", "typescript.ts": "", "notes.md": "x", "docs/typescript.md": "x"})
    (agent / "node_modules").symlink_to(spec.node_modules)
    neutralize(task, agent, reference)
    assert not (agent / "vitest.js").exists() and not (agent / "vitest").exists() and not (agent / "typescript.ts").exists()
    assert (agent / "lodash.js").exists() and (agent / "notes.md").exists() and (agent / "docs/typescript.md").exists()


def test_every_test_file_goes_back_and_a_new_one_goes_away_wherever_it_is(tmp_path):
    task = a_task(tmp_path)
    reference = lay(tmp_path / "reference", COLOCATED)
    agent = lay(tmp_path / "agent", {k: "// agent\n" for k in COLOCATED} | {"src/New.test.tsx": "x", "tests/extra.test.ts": "x", "src/deep/a.spec.ts": "x"})
    restore_tests(task, agent, reference)
    assert (agent / "src/Menu.test.tsx").read_text() == COLOCATED["src/Menu.test.tsx"]
    assert (agent / "tests/helpers.ts").read_text() == COLOCATED["tests/helpers.ts"]
    assert not (agent / "src/New.test.tsx").exists() and not (agent / "tests/extra.test.ts").exists() and not (agent / "src/deep/a.spec.ts").exists()
    assert (agent / "src/Menu.tsx").read_text() == "// agent\n"


def test_a_tests_directory_made_a_link_is_replaced_and_what_it_pointed_at_is_left_alone(tmp_path):
    task = a_task(tmp_path)
    reference = lay(tmp_path / "reference", COLOCATED)
    elsewhere = lay(tmp_path / "elsewhere", {"helpers.ts": "evil\n"})
    agent = lay(tmp_path / "agent", {"package.json": "{}"})
    (agent / "tests").symlink_to(elsewhere)
    restore_tests(task, agent, reference)
    assert not (agent / "tests").is_symlink() and (agent / "tests/helpers.ts").read_text() == COLOCATED["tests/helpers.ts"]
    assert (elsewhere / "helpers.ts").read_text() == "evil\n"


# ------------------------------------------------------------------------------ the pilot task


@needs_pilot
def test_the_pilot_task_is_a_node_task_with_a_patch_solution_and_a_typescript_hidden_test():
    assert PILOT.is_node and PILOT.spec.name == "neverthrow" and PILOT.hidden_test.name == "check_test.ts"
    assert PILOT.solution_patch.is_file() and not PILOT.solution.exists()
    assert PILOT.python is None and PILOT.tests_dir == "tests" and PILOT.pythonpath == ()
    assert PILOT.check_timeout == node.NODE_TIMEOUT


def an_agent_copy(task, tmp_path, name="agent", solved=True) -> Path:
    repo = copy_fixture(task, tmp_path / name)
    if solved:
        apply_solution(task, repo)
    return repo


def solution_without_all_but(task, repo, left_out):
    """The reference solution minus its edit to one file: the callers there keep the old name."""
    patch = task.solution_patch.read_text()
    sections = [s for s in re.split(r"(?m)^(?=diff -ruN )", patch) if s]
    kept = "".join(s for s in sections if not re.match(rf"diff -ruN (?:-x \S+ )*a/{re.escape(left_out)} ", s))
    assert kept != patch, f"{left_out} is not in the solution"
    partial = repo.parent / "partial.patch"
    partial.write_text(kept)
    done = subprocess.run(["git", "apply", "--whitespace=nowarn", str(partial)], cwd=repo, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


@needs_pilot
def test_the_reference_solution_is_graded_success(tmp_path):
    verdict = check_task(PILOT, an_agent_copy(PILOT, tmp_path))
    assert verdict["success"], verdict
    assert verdict["suite_passed"] and verdict["hidden_passed"] and verdict["tests_lost"] == 0
    assert verdict["tests_passed"] >= PILOT.min_tests


@needs_pilot
def test_the_untouched_checkout_fails_and_the_hidden_test_names_the_missed_callers(tmp_path):
    verdict = check_task(PILOT, an_agent_copy(PILOT, tmp_path, solved=False))
    assert not verdict["success"] and not verdict["hidden_passed"], verdict
    message = " ".join(verdict["failures"])
    assert "src/result-async.ts" in message and "tests/typecheck-tests.ts" in message and "tests/index.test.ts" in message, message


# --------------------------------------------------------------------------- the cheat matrix


def find(path: Path, title_part: str, repo: Path) -> dict:
    """The test (from the grader's own analysis) whose key contains title_part, with its text span."""
    report = node.analyse_tests(repo, [path])[str(path)]
    keys = [k for k in report["tests"] if title_part in k]
    assert len(keys) == 1, (title_part, keys)
    return {"key": keys[0], **report["tests"][keys[0]]}


def edit_test(path: Path, title_part: str, change, repo: Path) -> str:
    info = find(path, title_part, repo)
    text = path.read_text()
    chars = text.encode()
    new = change(chars[info["start"] : info["end"]].decode())
    path.write_text((chars[: info["start"]].decode()) + new + chars[info["end"] :].decode())
    return info["key"]


ASYNC_COMBINE_TEST = "`ResultAsync.combine` > Combines a list of async results into an Ok value"  # titles keep the old name: only the calls are renamed
TESTDOUBLE_TEST = "Combines `testdouble` proxies"


@needs_pilot
@needs_node
def test_deleting_the_edited_test_is_marked_down_though_everything_else_is_right(tmp_path):
    repo = an_agent_copy(PILOT, tmp_path)
    key = edit_test(repo / "tests/index.test.ts", TESTDOUBLE_TEST, lambda t: "", repo)
    verdict = check_task(PILOT, repo)
    assert not verdict["success"] and verdict["suite_passed"] and verdict["hidden_passed"], verdict
    assert any("tests/index.test.ts::" in t and "gather" in t and "dropped" in t for t in verdict["lost_tests"]), verdict
    assert key


@needs_pilot
@needs_node
@pytest.mark.parametrize("opener, label", [
    ("it.skip(", "skipped"), ("it.todo(", "todo"), ("xit(", "skipped"), ("it.only(", "only"),
    ("it.skipIf(true)(", "skipped"), ("it.fails(", "fails"),
], ids=["skip", "todo", "xit", "only", "skipIf", "fails"])
def test_skipping_or_isolating_the_edited_test_is_marked_down(opener, label, tmp_path):
    repo = an_agent_copy(PILOT, tmp_path)
    edit_test(repo / "tests/index.test.ts", ASYNC_COMBINE_TEST, lambda t: t.replace("it(", opener, 1), repo)
    verdict = check_task(PILOT, repo)
    assert not verdict["success"], verdict
    assert any(f"({label})" in t for t in verdict["lost_tests"]), verdict


@needs_pilot
@needs_node
def test_keeping_the_test_but_dropping_the_new_name_from_it_is_marked_down(tmp_path):
    repo = an_agent_copy(PILOT, tmp_path)
    edit_test(repo / "tests/index.test.ts", ASYNC_COMBINE_TEST, lambda t: t.replace("ResultAsync.gather(asyncResultList)", "okAsync([123, 456, 789])"), repo)
    verdict = check_task(PILOT, repo)
    assert not verdict["success"] and verdict["hidden_passed"], verdict
    assert any("dropped gather" in t for t in verdict["lost_tests"]), verdict


def retitle_combine(text: str) -> str:
    return text.replace("`ResultAsync.combine`", "`ResultAsync.gather`")


def retitle_orelse(text: str) -> str:
    start = text.rindex("  describe('orElse'")  # the ResultAsync block: the calls in it are already recoverWith, the titles say orElse
    end = text.index("\n  describe(", start + 1)
    return text[:start] + text[start:end].replace("orElse", "recoverWith") + text[end:]


@pytest.mark.parametrize("tid, retitle", [
    ("20-neverthrow-rename-resultasync-combine", retitle_combine),
    ("21-neverthrow-rename-resultasync-orelse", retitle_orelse),
], ids=["combine", "orElse"])
@needs_node
def test_renaming_the_titles_that_name_the_renamed_method_is_still_graded_success(tid, retitle, tmp_path):
    """The prompt does not say the titles keep their text, so an agent that renames `ResultAsync.combine` in a describe
    title along with the calls inside it did the task right: the test it kept is found under its new title."""
    task = TASKS[tid]
    if not task.available:
        pytest.skip("needs the pinned neverthrow checkout: python -m eval.fetch")
    repo = an_agent_copy(task, tmp_path)
    path = repo / "tests/index.test.ts"
    before = path.read_text()
    path.write_text(retitle(before))
    assert path.read_text() != before
    verdict = check_task(task, repo)
    assert verdict["success"] and verdict["tests_lost"] == 0 and not verdict["lost_tests"], verdict


HOSTILE = {
    "vitest.config.ts": "export default { test: { include: ['nothing/**/*.test.ts'] } }\n",
    "vitest.config.mts": "export default { test: { exclude: ['**/*'] } }\n",
    "vite.config.js": "export default { test: { exclude: ['**/*'], setupFiles: ['./evil.ts'] } }\n",
    "vitest.workspace.ts": "export default []\n",
    "jest.config.js": "module.exports = { testMatch: [] }\n",
    "vitest.js": "module.exports = { describe() {}, it() {}, expect() {} }\n",
    "typescript.js": "throw new Error('shadow')\n",
}


def plant(repo: Path, which: str) -> None:
    if which == "package.json":
        data = json.loads((repo / "package.json").read_text())
        data["scripts"]["test"] = "echo all passed"
        data["type"] = "module"
        data["vitest"] = {"include": []}
        data["jest"] = {"testMatch": []}
        (repo / "package.json").write_text(json.dumps(data))
    elif which == "tsconfig":
        (repo / "tsconfig.json").write_text('{"compilerOptions": {"nonsense": true}, "include": []}')
        (repo / "tests/tsconfig.tests.json").write_text("{not json")
    elif which == "vitest-dir":
        (repo / "vitest").mkdir()
        (repo / "vitest/index.js").write_text("module.exports = {}\n")
    elif which == "node_modules":
        (repo / "node_modules").unlink()
        fake = repo / "node_modules" / ".bin"
        fake.mkdir(parents=True)
        (fake / "vitest").write_text("#!/bin/sh\necho fake vitest says all passed\nexit 0\n")
        (fake / "vitest").chmod(0o755)
        (repo / "node_modules" / "vitest").mkdir()
        (repo / "node_modules" / "vitest" / "package.json").write_text('{"name": "vitest", "main": "i.js"}')
        (repo / "node_modules" / "vitest" / "i.js").write_text("module.exports = {}\n")
        (repo / "tests" / "node_modules").mkdir()
        (repo / "tests" / "node_modules" / "vitest.js").write_text("module.exports = {}\n")
    elif which == "tests-link":
        shutil.move(repo / "tests", repo.parent / "elsewhere")
        (repo / "tests").symlink_to(repo.parent / "elsewhere")
    else:
        (repo / which).write_text(HOSTILE[which])


CHEATS = ["vitest.config.ts", "vitest.config.mts", "vite.config.js", "vitest.workspace.ts", "jest.config.js", "package.json", "tsconfig",
          "vitest.js", "typescript.js", "vitest-dir", "node_modules"]


@needs_pilot
@needs_node
@pytest.mark.parametrize("which", CHEATS)
def test_a_cheat_in_the_agents_copy_does_not_turn_an_unfinished_task_into_a_pass(which, tmp_path):
    """The task is not done, and the agent has tried to make the runner not see that: the tests run and fail."""
    repo = an_agent_copy(PILOT, tmp_path, solved=False)
    plant(repo, which)
    verdict = check_task(PILOT, repo)
    assert not verdict["success"] and not verdict["hidden_passed"], verdict
    assert not verdict["suite_passed"] and verdict["tests_passed"] >= PILOT.min_tests - 20, verdict  # ran, and the new-name tests failed


@needs_pilot
@needs_node
@pytest.mark.parametrize("which", CHEATS)
def test_the_same_cheat_does_not_break_a_finished_task_either(which, tmp_path):
    """Nothing is lost by the neutralising: the right solution still passes with the files planted."""
    repo = an_agent_copy(PILOT, tmp_path)
    plant(repo, which)
    verdict = check_task(PILOT, repo)
    assert verdict["success"], verdict


@needs_pilot
@needs_node
@pytest.mark.parametrize("solved", [False, True], ids=["unfinished", "finished"])
def test_a_tests_directory_made_a_link_never_passes_because_the_scan_will_not_follow_it(solved, tmp_path):
    """Moving the tests out and linking them back would hide any stale caller in them from a scan that
    skips links, so the hidden test fails on the link itself, loudly, whether or not the task was done."""
    repo = an_agent_copy(PILOT, tmp_path, solved=solved)
    plant(repo, "tests-link")
    verdict = check_task(PILOT, repo)
    assert not verdict["success"] and not verdict["hidden_passed"], verdict
    assert any("tests is a symbolic link" in f for f in verdict["failures"]), verdict


@needs_pilot
@needs_node
def test_keeping_the_old_name_as_an_alias_fails_the_hidden_test(tmp_path):
    repo = an_agent_copy(PILOT, tmp_path)
    path = repo / "src/result-async.ts"
    text = path.read_text()
    assert text.count("  static gather<") >= 1
    path.write_text(text.replace("  static gather<", "  static combine = ResultAsync.gather\n\n  static gather<", 1))
    verdict = check_task(PILOT, repo)
    assert not verdict["success"] and not verdict["hidden_passed"], verdict
    assert any("combine" in f for f in verdict["failures"]), verdict


@needs_pilot
@needs_node
@pytest.mark.parametrize("rel, where", [
    ("tests/typecheck-tests.ts", "the file the suite never runs"),
    ("tests/index.test.ts", "the tests"),
    ("src/result-async.ts", "the declaration"),
])
def test_renaming_only_some_of_the_callers_fails(rel, where, tmp_path):
    repo = an_agent_copy(PILOT, tmp_path, solved=False)
    solution_without_all_but(PILOT, repo, rel)
    verdict = check_task(PILOT, repo)
    assert not verdict["success"] and not verdict["hidden_passed"], (where, verdict)
    assert any(rel in f for f in verdict["failures"]), verdict


@needs_pilot
@needs_node
def test_a_correct_solution_written_differently_is_graded_success(tmp_path):
    """The control: more than the reference did, none of it wrong. An unrelated edit to the source, a new
    test file and a new test of the renamed method in an existing one (neither is ever run), and a local
    alias of the new name in the agent's own code."""
    repo = an_agent_copy(PILOT, tmp_path)
    utils = repo / "src/_internals/utils.ts"
    utils.write_text(utils.read_text() + "\n// agent: an unrelated note\nexport const AGENT_NOTE = 'note'\n")
    (repo / "tests/agent-extra.test.ts").write_text(
        "import { describe, expect, it } from 'vitest'\nimport { okAsync, ResultAsync } from '../src'\n"
        "describe('agent', () => {\n  it('gathers', async () => {\n    const { gather } = ResultAsync\n"
        "    expect((await gather([okAsync(1)])).isOk()).toBe(true)\n  })\n})\n"
    )
    path = repo / "tests/index.test.ts"
    path.write_text(path.read_text() + "\ndescribe('agent addition', () => {\n  it('works', () => {\n    expect(typeof ResultAsync.gather).toBe('function')\n  })\n})\n")
    verdict = check_task(PILOT, repo)
    assert verdict["success"] and verdict["tests_lost"] == 0, verdict


# -------------------------------------------------------------------- the weakened-test analysis


@needs_nt
@needs_node
class TestWeakened:
    """The analysis alone, on small files (the pilot's cheat tests above run it end to end)."""

    START = (
        "import { describe, it, expect } from 'vitest'\nimport { a } from '../src'\n"
        "describe('suite', () => {\n  it('one', () => {\n    expect(a.old()).toBe(1)\n  })\n  it('two', () => {\n    expect(a.keep()).toBe(2)\n  })\n})\n"
    )
    REFERENCE = START.replace("a.old()", "a.fresh()")

    def build(self, tmp_path, agent_text, start=None, reference=None):
        task = Task(id="t", fixture=tmp_path / "fixture", prompt="p", min_tests=1, why="w", dir=tmp_path, spec=NEVERTHROW)
        for name, text in (("fixture", start or self.START), ("reference", reference or self.REFERENCE)):
            lay(tmp_path / name, {"tests/a.test.ts": text, "package.json": "{}"})
            (tmp_path / name / "node_modules").symlink_to(NEVERTHROW.node_modules)
        if agent_text is not None:
            lay(tmp_path / "agent", {"tests/a.test.ts": agent_text})
        else:
            (tmp_path / "agent").mkdir()
        return task, tmp_path / "agent", tmp_path / "reference"

    def test_the_same_edit_is_not_weak(self, tmp_path):
        assert weakened_tests(*self.build(tmp_path, self.REFERENCE)) == []

    def test_a_different_way_of_using_the_new_name_is_not_weak(self, tmp_path):
        text = self.REFERENCE.replace("expect(a.fresh()).toBe(1)", "const v = a.fresh()\n    expect(v).toBe(1)")
        assert weakened_tests(*self.build(tmp_path, text)) == []

    @pytest.mark.parametrize("agent", [
        "import { describe, it, expect } from 'vitest'\ndescribe('suite', () => {\n  it('two', () => {\n    expect(a.keep()).toBe(2)\n  })\n})\n",
        None,
        "this is not ( typescript",
    ], ids=["test deleted", "file deleted", "file broken"])
    def test_a_deleted_or_broken_test_is_weak(self, tmp_path, agent):
        task, a, r = self.build(tmp_path, agent)
        weak = weakened_tests(task, a, r)
        assert weak and "tests/a.test.ts::suite > one (dropped fresh)" in weak[0]

    def test_the_name_kept_and_the_use_dropped_is_weak(self, tmp_path):
        weak = weakened_tests(*self.build(tmp_path, self.START))
        assert weak == ["tests/a.test.ts::suite > one (dropped fresh)"]

    @pytest.mark.parametrize("old, new", [("describe('suite'", "describe('renamed suite'"), ("it('one'", "it('first'")], ids=["describe", "it"])
    def test_a_test_kept_whole_under_a_renamed_title_is_not_weak(self, tmp_path, old, new):
        """A title that names the renamed thing (`ResultAsync.combine`) is edited along with the calls in it."""
        assert weakened_tests(*self.build(tmp_path, self.REFERENCE.replace(old, new))) == []

    def test_a_test_renamed_and_gutted_is_weak(self, tmp_path):
        """Moved to a new title, and the new name dropped from its body: the same as keeping the title."""
        text = self.START.replace("describe('suite'", "describe('renamed suite'")
        assert weakened_tests(*self.build(tmp_path, text)) == ["tests/a.test.ts::suite > one (dropped fresh)"]

    def test_a_deleted_test_is_weak_though_another_in_the_file_has_other_names(self, tmp_path):
        text = self.REFERENCE.replace("describe('suite'", "describe('renamed suite'").replace("a.fresh()", "a.keep()")
        assert weakened_tests(*self.build(tmp_path, text)) == ["tests/a.test.ts::suite > one (dropped fresh)"]

    def test_one_agent_test_stands_in_for_only_one_reference_test(self, tmp_path):
        """Two reference tests carry the same names: a single renamed test of the agent cannot be both."""
        head = "import { describe, it, expect } from 'vitest'\nimport { a } from '../src'\ndescribe('suite', () => {\n"
        start = head + "  it('one', () => {\n    expect(a.old()).toBe(1)\n  })\n  it('again', () => {\n    expect(a.old()).toBe(1)\n  })\n})\n"
        reference = start.replace("a.old()", "a.fresh()")
        agent = head.replace("'suite'", "'renamed'") + "  it('both', () => {\n    expect(a.fresh()).toBe(1)\n  })\n})\n"
        weak = weakened_tests(*self.build(tmp_path, agent, start, reference))
        assert len(weak) == 1 and weak[0].endswith("(dropped fresh)"), weak
        two = agent.replace("})\n})\n", "})\n  it('second', () => {\n    expect(a.fresh()).toBe(1)\n  })\n})\n")
        assert weakened_tests(*self.build(tmp_path / "two", two, start, reference)) == []

    def test_stand_ins_are_paired_so_that_each_reference_test_gets_one_that_covers_it(self, tmp_path):
        """The smaller reference test fits both agent tests, the larger only one: the pairing may not give the larger one away."""
        head = "import { describe, it, expect } from 'vitest'\nimport { a } from '../src'\ndescribe('SUITE', () => {\n"
        small = "  it('TITLE', () => {\n    expect(a.ONE()).toBe(1)\n  })\n"
        large = "  it('TITLE', () => {\n    expect(a.ONE()).toBe(1)\n    expect(a.TWO()).toBe(1)\n  })\n"

        def file(suite, *tests):
            return head.replace("SUITE", suite) + "".join(t for t in tests) + "})\n"

        start = file("suite", small.replace("TITLE", "p").replace("ONE", "old"), large.replace("TITLE", "q").replace("ONE", "old").replace("TWO", "old2"))
        reference = file("suite", small.replace("TITLE", "p").replace("ONE", "fresh"), large.replace("TITLE", "q").replace("ONE", "fresh").replace("TWO", "extra"))
        agent = file("renamed", large.replace("TITLE", "big").replace("ONE", "fresh").replace("TWO", "extra"), small.replace("TITLE", "little").replace("ONE", "fresh"))
        assert weakened_tests(*self.build(tmp_path, agent, start, reference)) == []

    def test_a_test_gutted_under_its_own_title_is_not_rescued_by_another_test_that_has_the_name(self, tmp_path):
        """`one` keeps its title and loses the new name; a different test of the agent's has it. Still weak: the title says
        which test it is, and a renamed stand-in is only looked for when the title is gone."""
        text = self.START + "it('extra', () => {\n  expect(a.fresh()).toBe(1)\n})\n"
        assert weakened_tests(*self.build(tmp_path, text)) == ["tests/a.test.ts::suite > one (dropped fresh)"]

    @pytest.mark.parametrize("old, new, label", [
        ("it('two',", "it.skip('two',", "skipped"), ("it('two',", "it.todo('two',", "todo"), ("it('two',", "xit('two',", "skipped"),
        ("it('two',", "it.only('two',", "only"), ("it('two',", "test.skip('two',", "skipped"), ("it('two',", "it.skipIf(true)('two',", "skipped"),
        ("it('two',", "it.runIf(false)('two',", "skipped"), ("it('two',", "it.fails('two',", "fails"), ("describe('suite',", "describe.skip('suite',", "skipped"),
        ("describe('suite',", "xdescribe('suite',", "skipped"), ("describe('suite',", "describe.only('suite',", "only"),
        ("it('two', () => {", "it('two', (ctx) => {\n    ctx.skip()", "skipped"),
    ])
    def test_every_way_to_stop_a_test_running_is_marked_down_even_in_a_test_the_reference_did_not_edit(self, tmp_path, old, new, label):
        weak = weakened_tests(*self.build(tmp_path, self.REFERENCE.replace(old, new, 1)))
        assert weak and all(f"({label})" in w for w in weak), weak

    def test_a_modifier_the_reference_itself_has_is_not_held_against_the_agent(self, tmp_path):
        task, agent, reference = self.build(tmp_path, None)
        text = self.REFERENCE.replace("it('two',", "it.skip('two',", 1)
        (reference / "tests/a.test.ts").write_text(text)
        lay(agent, {"tests/a.test.ts": text})
        assert weakened_tests(task, agent, reference) == []

    def test_a_new_test_added_with_only_is_marked_down(self, tmp_path):
        weak = weakened_tests(*self.build(tmp_path, self.REFERENCE + "it.only('mine', () => {})\n"))
        assert weak == ["tests/a.test.ts::mine (only)"]

    def test_a_dropped_import_of_the_new_name_is_weak_at_file_level(self, tmp_path):
        start = "import { old } from '../src'\nit('t', () => { old() })\n"
        reference = "import { fresh } from '../src'\nit('t', () => { fresh() })\n"
        task = Task(id="t", fixture=tmp_path / "fixture", prompt="p", min_tests=1, why="w", dir=tmp_path, spec=NEVERTHROW)
        for name, text in (("fixture", start), ("reference", reference), ("agent", start)):
            lay(tmp_path / name, {"tests/a.test.ts": text})
            (tmp_path / name / "node_modules").symlink_to(NEVERTHROW.node_modules)
        weak = weakened_tests(task, tmp_path / "agent", tmp_path / "reference")
        assert "tests/a.test.ts::t (dropped fresh)" in weak and "tests/a.test.ts::<module> (dropped fresh)" in weak, weak


# ---------------------------------------------------- the hidden run and the TS helpers


HELPER_TEST = """
import { describe, expect, it } from 'vitest'
import { filesUsing, references } from './check_helpers'

describe('helpers', () => {
  it('finds each kind', () => {
    const got = references('target').map((r) => `${r.file}:${r.line} ${r.kind} ${r.detail}`)
    expect(got).toEqual(JSON.parse(process.env.EXPECTED_REFS as string))
  })
  it('lists files', () => {
    expect(filesUsing('target')).toEqual(['src/a.ts', 'src/b.tsx'])
  })
})
"""

HELPER_FIXTURE = {
    "src/a.ts": """import { target } from './b'
export class Box {
  target(): number { return 1 }
  static make(target: number) { return Box.target + target }
}
export namespace Space { export function target() { return 2 } }
interface Shape { target: string }
const o = { target: 1, 'target': 2 }
call({ target: 3 })
const { target: renamed } = o
const t = o['target']
const s = 'target in a string'
// target in a comment
export { target as other } from './b'
""",
    "src/b.tsx": "export const target = 1\nexport const View = () => <Item target=\"x\" other={target} />\nconst Item = (p: any) => null\n",
    "src/gen/dist/skip.ts": "target\n",
    ".hidden/x.ts": "target\n",
}


@needs_nt
@needs_node
def test_the_ts_helpers_classify_every_kind_of_reference_and_skip_generated_directories(tmp_path, monkeypatch):
    task_dir = lay(tmp_path / "task", {"check_test.ts": HELPER_TEST})
    task = Task(id="t", fixture=tmp_path / "fixture", prompt="p", min_tests=1, why="w", dir=task_dir, spec=NEVERTHROW)
    repo = lay(tmp_path / "repo", HELPER_FIXTURE)
    NEVERTHROW.link_deps(repo)
    expected = [
        "src/a.ts:1 import ./b", "src/a.ts:3 def Box", "src/a.ts:4 attr Box", "src/a.ts:4 name ", "src/a.ts:4 param make",
        "src/a.ts:6 def Space", "src/a.ts:7 def Shape", "src/a.ts:8 kwarg ", "src/a.ts:9 kwarg call", "src/a.ts:10 kwarg ",
        "src/a.ts:11 attr o", "src/a.ts:14 import ./b", "src/b.tsx:1 name ", "src/b.tsx:2 kwarg <Item>", "src/b.tsx:2 name ",
    ]
    monkeypatch.setenv("EXPECTED_REFS", json.dumps(expected))
    hidden = node.run_hidden_node(task, repo, 120)
    assert hidden["passed"], hidden


BROKEN_TEST = """
import { expect, it } from 'vitest'
import { references } from './check_helpers'
it('scans', () => { expect(references('zzz')).toEqual([]) })
"""


@needs_nt
@needs_node
def test_a_source_file_that_does_not_parse_fails_the_hidden_test_loudly(tmp_path):
    task_dir = lay(tmp_path / "task", {"check_test.ts": BROKEN_TEST})
    task = Task(id="t", fixture=tmp_path / "fixture", prompt="p", min_tests=1, why="w", dir=task_dir, spec=NEVERTHROW)
    repo = lay(tmp_path / "repo", {"src/ok.ts": "export const x = 1\n", "src/broken.ts": "export const = (\n"})
    NEVERTHROW.link_deps(repo)
    hidden = node.run_hidden_node(task, repo, 120)
    assert not hidden["passed"] and any("src/broken.ts does not parse" in f for f in hidden["failures"]), hidden
    (repo / "src/broken.ts").write_text("export const y = 2\n")
    assert node.run_hidden_node(task, repo, 120)["passed"]


ISOLATION_TEST = """
import { expect, it } from 'vitest'
it('the only test that runs', () => { expect(1 + 1).toBe(2) })
"""


@needs_nt
@needs_node
def test_the_hidden_run_reads_only_the_hidden_file_whatever_the_repos_config_and_tests_say(tmp_path):
    task_dir = lay(tmp_path / "task", {"check_test.ts": ISOLATION_TEST})
    task = Task(id="t", fixture=tmp_path / "fixture", prompt="p", min_tests=1, why="w", dir=task_dir, spec=NEVERTHROW)
    repo = lay(tmp_path / "repo", {
        "src/ok.ts": "export const x = 1\n",
        "vitest.config.ts": "export default { test: { include: ['nothing/**'], exclude: ['**/*'] } }\n",
        "tests/loud.test.ts": "import { it, expect } from 'vitest'\nit('would fail', () => { expect(1).toBe(2) })\n",
    })
    NEVERTHROW.link_deps(repo)
    hidden = node.run_hidden_node(task, repo, 120)
    assert hidden["passed"] and hidden["summary"] == "1 passed, 0 failed", hidden  # the repo's failing test did not run, its config did not hide ours


TSYRINGE_SPEC = load_repos()["tsyringe"]


@needs_pilot
@needs_node
def test_a_vitest_config_in_a_parent_of_the_graded_copy_is_not_picked_up_by_a_repo_that_has_none(tmp_path):
    """vitest searches the directories above its root for a config, and the graded copy lives in a temp directory under /tmp:
    a stray /tmp/vitest.config.ts would reach neverthrow and superstruct, which have none of their own."""
    (tmp_path / "vitest.config.ts").write_text("export default { test: { include: ['nothing/**/*.test.ts'] } }\n")
    graded = copy_fixture(PILOT, tmp_path / "graded")
    PILOT.spec.link_deps(graded)
    assert node.repo_vitest_config(graded) is None
    verdict, ids = node.run_node_suite(PILOT, graded, 300)
    assert verdict["passed"] and len(ids) >= PILOT.min_tests, verdict


def suite_command(task, repo, monkeypatch):
    seen = []
    monkeypatch.setattr(node, "run_command", lambda command, cwd, timeout, env=None: (seen.append(command), (None, ""))[1])
    node.run_node_suite(task, repo, 5)
    return seen[0]


@needs_nt
@needs_node
def test_the_suite_gets_an_explicit_config_only_where_the_repo_has_none(tmp_path, monkeypatch):
    task = Task(id="t", fixture=tmp_path / "fixture", prompt="p", min_tests=1, why="w", dir=tmp_path, spec=NEVERTHROW)
    bare = lay(tmp_path / "bare", {"src/a.ts": "export {}\n"})
    command = suite_command(task, bare, monkeypatch)
    assert command.count("--config") == 1, command
    config = Path(command[command.index("--config") + 1])
    assert not str(config).startswith(str(bare)), config  # outside the repo: nothing is written to the agent's tree or the graded copy
    own = lay(tmp_path / "own", {"vitest.config.mts": "export default {}\n"})
    assert "--config" not in suite_command(task, own, monkeypatch)


@pytest.mark.skipif(not TSYRINGE_SPEC.config, reason="tsyringe names no config")
@needs_node
def test_a_repo_whose_spec_names_its_config_is_given_that_one_only(tmp_path, monkeypatch):
    spec = TSYRINGE_SPEC
    task = Task(id="t", fixture=tmp_path / "fixture", prompt="p", min_tests=1, why="w", dir=tmp_path, spec=spec)
    command = suite_command(task, lay(tmp_path / "r", {"src/a.ts": "export {}\n"}), monkeypatch)
    assert command.count("--config") == 1 and command[command.index("--config") + 1] == spec.config, command


STALE_TEST = """
import { expect, it } from 'vitest'
import { references } from './check_helpers'
it('no stale uses', () => { expect(references('stale')).toEqual([]) })
"""


@needs_nt
@needs_node
@pytest.mark.parametrize("planted", ["check_helpers.mjs", "check_helpers.js", "check_helpers.mts", "check_helpers.cjs"])
def test_a_helper_the_agent_planted_in_the_hidden_directory_is_never_the_one_that_runs(planted, tmp_path):
    """A planted check_helpers.mjs resolves before the grader's .ts under vite and would answer "no references" to every
    scan, so the directory is emptied before the grader writes into it."""
    task_dir = lay(tmp_path / "task", {"check_test.ts": STALE_TEST})
    task = Task(id="t", fixture=tmp_path / "fixture", prompt="p", min_tests=1, why="w", dir=task_dir, spec=NEVERTHROW)
    repo = lay(tmp_path / "repo", {"src/ok.ts": "export const stale = 1\n", f"_eval_check/{planted}": "export const references = () => []\nmodule.exports = { references: () => [] }\n"})
    NEVERTHROW.link_deps(repo)
    hidden = node.run_hidden_node(task, repo, 120)
    assert not hidden["passed"] and any("no stale uses" in f for f in hidden["failures"]), hidden
    assert not (repo / "_eval_check" / planted).exists()


@needs_nt
@needs_node
def test_run_hidden_removes_nothing_from_the_agents_view_because_it_is_never_there(tmp_path):
    """The hidden directory is made in the graded copy only; check_task deletes it before the suite runs."""
    task_dir = lay(tmp_path / "task", {"check_test.ts": ISOLATION_TEST})
    task = Task(id="t", fixture=tmp_path / "fixture", prompt="p", min_tests=1, why="w", dir=task_dir, spec=NEVERTHROW)
    repo = lay(tmp_path / "repo", {"src/ok.ts": "export const x = 1\n"})
    NEVERTHROW.link_deps(repo)
    run_hidden(task, repo)
    assert (repo / "_eval_check" / "hidden.test.ts").is_file()  # run_hidden leaves it for check_task to remove
    assert not (task_dir / "_eval_check").exists() and not (NEVERTHROW.checkout / "_eval_check").exists()


@needs_pilot
@needs_node
def test_check_task_leaves_no_hidden_directory_in_the_agents_tree_and_does_not_edit_it(tmp_path):
    repo = an_agent_copy(PILOT, tmp_path)
    before = sorted(p.relative_to(repo).as_posix() for p in repo.rglob("*") if "node_modules" not in p.parts)
    check_task(PILOT, repo)
    after = sorted(p.relative_to(repo).as_posix() for p in repo.rglob("*") if "node_modules" not in p.parts)
    assert after == before and (repo / "node_modules").is_symlink()


@needs_pilot
@needs_node
def test_the_graded_copy_is_given_the_cached_node_modules_whatever_the_agent_left(tmp_path, monkeypatch):
    """The link is made right after the copy: the real directory the agent planted was dropped by IGNORE."""
    seen = {}
    real = node.run_node_suite

    def spy(task, repo, timeout, flags=()):
        seen["link"] = (Path(repo) / "node_modules").is_symlink() and (Path(repo) / "node_modules").resolve() == task.spec.node_modules.resolve()
        return real(task, repo, timeout, flags)

    monkeypatch.setattr(node, "run_node_suite", spy)
    repo = an_agent_copy(PILOT, tmp_path)
    plant(repo, "node_modules")
    check_task(PILOT, repo)
    assert seen["link"] is True


# -------------------------------------------------------------------- jest, on the real tsyringe run


TSYRINGE = load_repos()["tsyringe"]


@pytest.mark.skipif(not TSYRINGE.available, reason="needs the pinned tsyringe checkout: python -m eval.fetch")
@needs_node
def test_a_real_jest_run_is_read_the_same_way(tmp_path):
    """tsyringe's jest 24 suite, one test file, through the same code that grades a vitest repo."""
    repo = tmp_path / "repo"
    shutil.copytree(TSYRINGE.checkout, repo, symlinks=True, ignore=shutil.ignore_patterns("node_modules"))
    TSYRINGE.link_deps(repo)
    task = Task(id="t", fixture=TSYRINGE.checkout, prompt="p", min_tests=1, why="w", dir=tmp_path, spec=TSYRINGE)
    verdict, ids = node.run_node_suite(task, repo, 300, targets=("src/__tests__/scoped.test.ts",))
    assert verdict["passed"], verdict
    assert ids and all(i.startswith("src/__tests__/scoped.test.ts::") for i in ids), sorted(ids)[:3]
    assert verdict["tests_passed"] == len(ids)


# ------------------------------------------------ the hidden run on every pinned node repository

SMOKE_TEST = """
import { expect, it } from 'vitest'
import { filesUsing, references } from './check_helpers'
it('scans this repository without a failure', () => {
  expect(references('zzz_not_here')).toEqual([])
  expect(filesUsing('export').length >= 0).toBe(true)
})
it('runs where the repo says (its environment applied)', () => {
  expect(typeof document !== 'undefined').toBe(process.env.EXPECT_DOM === '1')
})
"""

SMOKE_TEST_JEST = SMOKE_TEST.replace("import { expect, it } from 'vitest'\n", "")


@pytest.mark.parametrize("name, dom", [("neverthrow", "0"), ("superstruct", "0"), ("react-pro-sidebar", "1"), ("tsyringe", "0")])
@needs_node
def test_the_hidden_run_works_on_each_pinned_repository_under_its_own_runner_and_config(name, dom, tmp_path, monkeypatch):
    """vitest 2, vitest 1.6, vitest 4 with a jsdom config and a setup file, jest 24 with ts-jest: the grader's config
    wraps each repo's own, and the TypeScript helpers load under each (the compiler comes from the repo's node_modules)."""
    spec = load_repos()[name]
    if not spec.available:
        pytest.skip("needs the pinned repository: python -m eval.fetch")
    repo = tmp_path / "repo"
    shutil.copytree(spec.checkout, repo, symlinks=True, ignore=shutil.ignore_patterns("node_modules"))
    spec.link_deps(repo)
    task_dir = lay(tmp_path / "task", {"check_test.ts": SMOKE_TEST if spec.runner == "vitest" else SMOKE_TEST_JEST})
    task = Task(id="t", fixture=spec.checkout, prompt="p", min_tests=1, why="w", dir=task_dir, spec=spec)
    monkeypatch.setenv("EXPECT_DOM", dom)
    hidden = node.run_hidden_node(task, repo, 300)
    assert hidden["passed"] and hidden["summary"] == "2 passed, 0 failed", hidden


@pytest.mark.skipif(PILOT is None, reason="needs the pilot task")
def test_the_agent_harness_refuses_a_node_task_until_its_environment_is_wired(tmp_path, capsys):
    """Grading works for node tasks; the agent's PATH and shims do not yet, so no agent is started on one."""
    import eval.run as run_mod

    with pytest.raises(SystemExit):
        run_mod.main(["--tasks", PILOT_ID, "--repeats", "1", "--out", str(tmp_path / "x.json")])
    assert "cannot run node tasks yet" in capsys.readouterr().err


# ------------------------------------------------------------------ the rename tasks over node repos, one grade each way

# task id -> (old name, new name): the rename the task asks for, which a text rename of the old name over every
# source file would also apply to look-alikes the task says keep theirs
RENAMES = {
    "21-neverthrow-rename-resultasync-orelse": ("orElse", "recoverWith"),
    "22-superstruct-rename-assert": ("assert", "assertValid"),
    "23-superstruct-rename-create": ("create", "createFrom"),
    "24-tsyringe-rename-transform": ("transform", "convert"),
    "25-tsyringe-rename-registry": ("registry", "registerAll"),
}
RENAME_PARAMS = [
    pytest.param(tid, old, new, id=tid, marks=pytest.mark.skipif(tid not in TASKS or not TASKS[tid].available, reason="needs the pinned repository: python -m eval.fetch"))
    for tid, (old, new) in RENAMES.items()
]


def text_rename(repo: Path, old: str, new: str) -> int:
    """What a sed over every source file does: each file that spells `old` as a word now spells `new`."""
    changed = 0
    for path in sorted(repo.rglob("*")):
        if "node_modules" in path.relative_to(repo).parts or path.is_symlink() or not path.is_file():
            continue
        if re.search(r"\.[cm]?[jt]sx?$", path.name):
            text = path.read_text()
            out = re.sub(rf"\b{old}\b", new, text)
            if out != text:
                path.write_text(out)
                changed += 1
    return changed


@pytest.mark.parametrize("tid, old, new", RENAME_PARAMS)
@needs_node
def test_a_rename_task_passes_with_the_reference_and_fails_untouched_and_under_a_text_rename(tid, old, new, tmp_path):
    task = TASKS[tid]
    reference = check_task(task, an_agent_copy(task, tmp_path, "reference"))
    assert reference["success"] and reference["tests_passed"] >= task.min_tests and reference["tests_lost"] == 0, reference
    untouched = check_task(task, an_agent_copy(task, tmp_path, "untouched", solved=False))
    assert not untouched["success"] and not untouched["hidden_passed"], untouched
    naive = an_agent_copy(task, tmp_path, "naive", solved=False)
    assert text_rename(naive, old, new) > 0
    verdict = check_task(task, naive)
    assert not verdict["success"] and not verdict["hidden_passed"], verdict
