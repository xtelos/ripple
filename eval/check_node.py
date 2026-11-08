"""The grader for a task over a node repository (vitest or jest): eval/check.py dispatches here.

The same design as the Python grader, in the node setting (see the docstring of eval/check.py):

- The suite is not the agent's. The agent's copy is copied, node_modules is given back as a link to the
  cached install (the copy drops it), and into the copy go the starting repo's tests with the reference
  solution's own edits: the tests directory, every file the runner would collect as a test wherever it
  lives, and everything that decides what the runner loads or reports (its config in any extension,
  package.json, tsconfig, babel and swc config, .env files, __mocks__ directories, the setup files the
  config names), each put back to the reference's or removed when the reference has none.
- The runner is the repo's own (spec.node_argv) with a JSON reporter. Pass and fail counts and the set of
  passed test ids (file + full test name) come from that JSON, never from scraped text. A runner that
  dies before it reports is a failed suite with a summary that says so.
- The hidden test is a TypeScript file the repo's own runner runs against the agent's source, from a
  directory that exists only in the graded copy, under a config the grader writes (the repo's include
  globs cannot exclude it; its aliases, transforms and setup files still apply).
- Tests the reference edited are checked for hollowing out with the compiler API of the repo's own
  `typescript`, run through node (eval/tasks/check_helpers_ts/test_names.cjs): an exact parse, where a token
  diff would trip on strings, templates and JSX, and no tree-sitter in this process.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
from collections import Counter
from pathlib import Path

from ripple.checker import MAX_FAILURES, clip

from .loader import IGNORE, NODE_TIMEOUT, TASKS_DIR, Task, remove  # noqa: F401  (NODE_TIMEOUT is part of this module's surface)

HELPERS_DIR = TASKS_DIR / "check_helpers_ts"
HELPERS_TS = HELPERS_DIR / "check_helpers.ts"
TEST_NAMES = HELPERS_DIR / "test_names.cjs"
HIDDEN_DIR = "_eval_check"
HIDDEN_TEST = "hidden.test.ts"

SOURCE_EXT = re.compile(r"\.[cm]?[jt]sx?$")
TEST_FILE = re.compile(r"\.(?:test|tests|spec)\.[cm]?[jt]sx?$")
# Names that decide what the runner loads or reports, anywhere in the repo: vitest/vite/jest config, workspace and
# setup files in any extension, package.json (scripts, the jest key, "type", "imports"), tsconfig and jsconfig
# (ts-jest and esbuild read them), babel and swc config, .env files, and __mocks__ (auto-loaded by name).
GUARDED_NAME = re.compile(
    r"^(?!.*\.d\.[cm]?ts$)(?:(?:vitest|vite|jest)[.-].*\.[cm]?[jt]sx?|(?:vitest|vite|jest)[.-].*\.json|babel\.config\.[cm]?[jt]s|babel\.config\.json"
    r"|\.babelrc(?:\.\w+)?|\.swcrc|package\.json|(?:ts|js)config(?:\.[\w.-]+)?\.json|\.env(?:\..+)?|\.mocharc(?:\..+)?|__mocks__)$"
)
CONFIG_FILE = re.compile(r"^(?:vitest|vite|jest)[.-].*\.[cm]?[jt]s$")  # the ones that are read for the setup files they name
# Keys of a runner config whose values are files the runner loads: not module maps or aliases, whose targets are source.
SETUP_KEYS = (
    "setupFiles|setupFilesAfterEnv|setupFilesAfterEach|globalSetup|globalTeardown|environment|testEnvironment|testRunner|"
    "resolver|snapshotResolver|reporters|transform|snapshotSerializers|runner"
)
SETUP_VALUE = re.compile(rf"\b(?:{SETUP_KEYS})\b[\"']?\s*:\s*(\[[^\]]*\]|\{{[^}}]*\}}|\"[^\"]*\"|'[^']*')")
QUOTED = re.compile(r"[\"']([^\"'\n]+)[\"']")
SKIPPED_WALK = {"node_modules", ".git"}


def is_guarded(name: str) -> bool:
    return bool(GUARDED_NAME.match(name))


def is_test_file(rel: str) -> bool:
    """A file the runner collects as a test, by name: *.test.ts, *.spec.tsx, *.tests.js, or anything under __tests__."""
    return bool(TEST_FILE.search(rel)) or "__tests__" in Path(rel).parts


def run_command(command: list[str], cwd: Path, timeout: float, env: dict[str, str] | None = None) -> tuple[int | None, str]:
    """(exit code, everything it prints), or (None, "") past the timeout (its whole process group is killed)."""
    with tempfile.TemporaryFile() as out:
        proc = subprocess.Popen(
            command, cwd=cwd, env=env, stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True
        )
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
            return None, ""
        out.seek(0)
        return proc.returncode, out.read().decode(errors="replace")


def runner_env(task: Task, repo: Path, **extra: str) -> dict[str, str]:
    """The ambient environment plus what the spec says a run needs (the repo's own binaries first on PATH)."""
    env = {**os.environ, "CI": "true", "NO_COLOR": "1", "FORCE_COLOR": "0", "EVAL_REPO_ROOT": str(repo), **extra}
    env.update(task.spec.env(repo, env.get("PATH", "")))
    return env


# ---------------------------------------------------------------------------------------- the suite


def read_report(report: Path, repo: Path, code: int | None, output: str) -> tuple[dict, frozenset[str]]:
    """A verdict like ripple's run_check and the ids of the tests that passed, from the runner's JSON
    (vitest --reporter=json and jest --json share the shape). `output` is only for explaining a crash."""
    try:
        data = json.loads(report.read_text())
        results = data["testResults"]
    except (OSError, ValueError, KeyError, TypeError):
        tail = next((line.strip() for line in reversed(output.splitlines()) if line.strip()), "no output")
        summary = f"the runner exited {code} without writing a report: {tail}"
        return {"passed": False, "summary": clip(summary), "failures": [clip(tail)], "tests_passed": 0}, frozenset()
    here = [str(repo.resolve()), str(repo)]

    def relative(name: str) -> str:
        for root in here:
            if name.startswith(root + os.sep):
                return name[len(root) + 1 :]
        return name

    def plain(text: str) -> str:
        return text.replace(here[0], "<repo>").replace(here[1], "<repo>")

    ids: set[str] = set()
    seen: Counter[str] = Counter()
    failures: list[str] = []
    skipped = failed = 0
    for result in results:
        file = relative(result.get("name", ""))
        assertions = result.get("assertionResults") or []
        for item in assertions:
            test_id = f"{file}::{plain(item.get('fullName') or item.get('title') or '')}"
            seen[test_id] += 1
            if seen[test_id] > 1:
                test_id += f" #{seen[test_id]}"
            status = item.get("status")
            if status == "passed":
                ids.add(test_id)
            elif status == "failed":
                failed += 1
                first = next((m.strip().splitlines()[0] for m in item.get("failureMessages") or [] if m.strip()), "")
                failures.append(clip(f"FAILED {test_id}" + (f": {first}" if first else "")))
            else:
                skipped += 1
        if result.get("status") == "failed" and not any(a.get("status") == "failed" for a in assertions):
            # jest opens the message with a "● Test suite failed to run" header, the reason is the next line
            message = [m.strip() for m in (result.get("message") or "").splitlines() if m.strip() and not m.lstrip().startswith("●")]
            failures.append(clip(f"ERROR {file}" + (f": {message[0]}" if message else "")))
    suite_errors = sum(1 for f in failures if f.startswith("ERROR "))
    total = len(ids) + failed + skipped
    summary = f"{len(ids)} passed, {failed} failed" + (f", {skipped} skipped" if skipped else "")
    if suite_errors:
        summary += f", {suite_errors} suite{'s' if suite_errors > 1 else ''} did not load"
    if not total and not suite_errors:
        summary = "no tests ran"
    passed = code == 0 and not failures and bool(ids) and data.get("success") is not False
    verdict = {"passed": passed, "summary": summary, "failures": failures[:MAX_FAILURES], "tests_passed": len(ids)}
    return verdict, frozenset(ids)


def report_flags(runner: str, report: Path) -> tuple[str, ...]:
    if runner == "vitest":
        return ("--reporter=json", f"--outputFile={report}")
    return ("--ci", "--json", f"--outputFile={report}", "--watchman=false")


def run_node_suite(
    task: Task, repo: Path, timeout: float, flags: tuple[str, ...] = (), targets: tuple[str, ...] | None = None
) -> tuple[dict, frozenset[str]]:
    """The task's whole suite in repo, run by the repo's own runner (by default over spec.tests)."""
    with tempfile.TemporaryDirectory(prefix="ripple-eval-report-") as scratch:
        report = Path(scratch) / "report.json"  # outside the repo: the agent's tree is never written to
        if task.spec.runner == "vitest" and not task.spec.config and not repo_vitest_config(repo):
            # vitest looks for a config in the directories above its root when the repo has none, and the graded copy
            # is in a temp directory under /tmp: say which config this is rather than take whatever lies above it
            bare = Path(scratch) / "vitest.bare.config.mjs"
            bare.write_text("// Written by the grader: this repository has no vitest config of its own.\nexport default {}\n")
            flags = ("--config", str(bare), *flags)
        command = task.spec.node_argv(targets, (*report_flags(task.spec.runner, report), *flags))
        code, output = run_command(command, repo, timeout, runner_env(task, repo))
        if code is None:
            return {"passed": False, "summary": f"the runner was killed after {timeout}s", "failures": [], "tests_passed": 0}, frozenset()
        return read_report(report, repo, code, output)


# ----------------------------------------------------------------- what the runner reads from disk


def walk_files(root: Path, skip_dirs: tuple[str, ...] = ()) -> list[Path]:
    """Repo-relative paths of every file under root, outside node_modules, dot directories and `skip_dirs`
    (repo-relative). A link is a file, never followed."""
    found = []
    for here, dirs, files in os.walk(root):
        rel = Path(here).relative_to(root)
        dirs[:] = [d for d in sorted(dirs) if d not in SKIPPED_WALK and not d.startswith(".") and (rel / d).as_posix() not in skip_dirs]
        found += [rel / name for name in sorted(files)]
        found += [rel / d for d in dirs if (Path(here) / d).is_symlink()]
    return found


def guarded_paths(root: Path, skip: str) -> list[Path]:
    """Every guarded file or directory under root (a guarded directory is one thing), outside the tests
    directory `skip`, which is handled as a whole. Dot files count (.babelrc), dot directories do not."""
    found = []
    for here, dirs, files in os.walk(root):
        rel = Path(here).relative_to(root)
        dirs[:] = [d for d in sorted(dirs) if d not in SKIPPED_WALK and not d.startswith(".") and (rel / d).as_posix() != skip]
        for name in [*dirs, *files]:
            if is_guarded(name):
                found.append(rel / name)
        dirs[:] = [d for d in dirs if not is_guarded(d)]
    return found


def named_by_config(reference: Path, configs: list[Path]) -> list[Path]:
    """Files the reference's runner config loads (setup files, a custom environment, a resolver), found by the
    quoted strings under the keys that name them. Read relative to the repo root (with <rootDir> too) and the
    config's own directory. Only a file that exists in the reference counts."""
    found = []
    for config in configs:
        try:
            text = (reference / config).read_text()
        except (OSError, UnicodeDecodeError):
            continue
        for value in SETUP_VALUE.findall(text):
            for raw in QUOTED.findall(value):
                for base in (reference, reference / config.parent):
                    candidate = base / raw.removeprefix("<rootDir>/")
                    try:
                        inside = candidate.resolve().relative_to(reference.resolve())
                    except ValueError:
                        continue
                    if candidate.is_file() and "node_modules" not in inside.parts:
                        found.append(candidate.relative_to(reference))
                        break
    return sorted(set(found))


def dependency_names(task: Task) -> frozenset[str]:
    """Top-level names in the cached install: a file or package at the repo root with one of these names is
    what a resolver that looks at the root before node_modules would pick up instead of the real package."""
    try:
        return frozenset(n for n in os.listdir(task.spec.node_modules) if not n.startswith("."))
    except OSError:
        return frozenset()


def looks_like_a_module(entry: Path) -> bool:
    if entry.is_dir() and not entry.is_symlink():
        return any((entry / n).exists() for n in ("package.json", "index.js", "index.ts", "index.mjs", "index.cjs", "index.tsx", "index.json"))
    return bool(SOURCE_EXT.search(entry.name)) or entry.suffix == ".json"


def neutralize_node(task: Task, graded: Path, reference: Path) -> None:
    """Put everything outside the tests that can change what the runner loads or reports back to the
    reference's: config in any extension, package.json, tsconfig, babel and swc config, .env, __mocks__ and the
    setup files the reference's config names; and remove any root-level module named like a dependency.
    The agent's source files are left alone."""
    # Unlike the Python grader this does not leave the tests directory out: the hidden test runs before the
    # tests are put back, and tsc over tests/tsconfig.tests.json must read the reference's, not the agent's.
    guarded = guarded_paths(reference, "")
    configs = [p for p in guarded if CONFIG_FILE.match(p.name)]
    restored = sorted({*guarded, *named_by_config(reference, configs)})
    for rel in guarded_paths(graded, ""):
        remove(graded / rel)
    for rel in restored:
        target = graded / rel
        remove(target)  # a setup file the agent edited in place goes back, one it left alone is rewritten
        target.parent.mkdir(parents=True, exist_ok=True)
        if (reference / rel).is_dir():
            shutil.copytree(reference / rel, target, symlinks=True, ignore=IGNORE)
        else:
            shutil.copy2(reference / rel, target, follow_symlinks=False)
    names = dependency_names(task)
    for entry in sorted(graded.iterdir()):
        if entry.name.split(".")[0] in names and not (reference / entry.name).exists() and looks_like_a_module(entry):
            remove(entry)


def test_files(root: Path, tests_dir: str) -> list[Path]:
    """Repo-relative paths of every file the runner reads as a test: all of the tests directory (helpers
    and fixtures too) and every file elsewhere that is collected by its name."""
    prefix = tests_dir.rstrip("/") + "/"
    return [p for p in walk_files(root) if (p.as_posix() + "/").startswith(prefix) or is_test_file(p.as_posix())]


def restore_tests_node(task: Task, graded: Path, reference: Path) -> None:
    """Replace the tests directory with the reference's (a link goes, what it pointed at stays) and swap every
    test file elsewhere (co-located *.test.tsx) for the reference's, removing the agent's new ones."""
    tests = task.tests_dir.rstrip("/")
    remove(graded / tests)
    shutil.copytree(reference / tests, graded / tests, symlinks=True, ignore=IGNORE)
    outside = lambda p: not (p.as_posix() + "/").startswith(tests + "/")  # noqa: E731
    for rel in filter(outside, test_files(graded, tests)):
        remove(graded / rel)
    for rel in filter(outside, test_files(reference, tests)):
        (graded / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(reference / rel, graded / rel, follow_symlinks=False)


# ------------------------------------------------------------------------------ weakened tests


def analyse_tests(root: Path, files: list[Path]) -> dict[str, dict]:
    """The test_names.cjs analysis of each file, by the repo at `root` (its node_modules holds the compiler).
    Raises when the analysis itself cannot run: a grader that cannot look must not grade."""
    if not files:
        return {}
    done = subprocess.run(
        ["node", str(TEST_NAMES), str(root), *map(str, files)], cwd=root, capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL
    )
    if done.returncode:
        raise RuntimeError(f"the test analysis failed ({done.returncode}): {(done.stderr or done.stdout).strip()[-500:]}")
    return json.loads(done.stdout)


def without_title(info: dict) -> set[str]:
    """The names a test carries, less its own title (a bare word like 'works' is in them, and a renamed test has another)."""
    return set(info["names"]) - {info.get("title")}


def weakened_tests_node(task: Task, agent: Path, reference: Path) -> list[str]:
    """Tests the agent hollowed out or stopped from running. The grade never runs the agent's tests (they are
    replaced), but (1) a test the reference edited was edited to use something the task introduced, so one the
    agent deleted, or kept without that use, was not done; and (2) a skip, todo, only, fails, skipIf or runIf (or
    a ctx.skip()) that the reference does not have, on any test, even an unedited one or a new one, changes what
    the agent's own suite runs. Empty when the agent's tests carry the same changes and no more."""
    prefix = task.tests_dir
    ref_tests = [p for p in test_files(reference, prefix) if SOURCE_EXT.search(p.name)]
    agent_tests = [p for p in test_files(agent, prefix) if SOURCE_EXT.search(p.name)]
    edited = [
        p for p in ref_tests if (task.fixture / p).is_file() and (task.fixture / p).read_bytes() != (reference / p).read_bytes()
    ]
    wanted = {*ref_tests, *agent_tests}
    in_agent = {p for p in wanted if (agent / p).is_file()}
    results = {
        **analyse_tests(reference, [reference / p for p in ref_tests]),
        **analyse_tests(reference, [task.fixture / p for p in edited]),
        **analyse_tests(reference, [agent / p for p in sorted(in_agent)]),
    }
    weak = []
    for rel in edited:
        wanted_, before, mine = (results.get(str(root / rel), {}) for root in (reference, task.fixture, agent))
        if "error" in wanted_ or "error" in before:
            continue
        mine_tests = {} if "error" in mine else {"<module>": {"names": mine.get("module", [])}, **mine.get("tests", {})}
        ref_all = {"<module>": {"names": wanted_["module"]}, **wanted_["tests"]}
        start_all = {"<module>": {"names": before["module"]}, **before["tests"]}
        # A title can name the thing that was renamed (`ResultAsync.combine`), so an agent that renames it along with
        # the calls in the test still has that test, under another key. Tests are told apart by what they use, never
        # by a title string: a title is in no test's names unless it is a bare identifier. A reference test whose
        # title is gone is taken to be the agent's test, in this file, that carries every name it carries, and each
        # agent test stands in for one reference test at most. A title that is still there is never second-guessed.
        spare = [k for k in mine_tests if k != "<module>" and k not in ref_all]
        missing = {}  # reference key -> the agent's untitled-alike tests that could be it
        for key, info in ref_all.items():
            names = set(info["names"])
            added = names - set(start_all.get(key, {"names": names})["names"])
            if not added:
                continue
            if key in mine_tests:
                have = set(mine_tests[key]["names"])
                if not added <= have:
                    weak.append(f"{rel.as_posix()}::{key} (dropped {', '.join(sorted(added - have))})")
            else:
                missing[key] = (added, [k for k in spare if without_title(info) <= without_title(mine_tests[k])])
        taken: dict[str, str] = {}  # agent test -> the reference key it stands in for

        def stand_in(key: str, seen: set[str]) -> bool:
            for candidate in missing[key][1]:
                if candidate in seen:
                    continue
                seen.add(candidate)
                if candidate not in taken or stand_in(taken[candidate], seen):
                    taken[candidate] = key
                    return True
            return False

        weak += [f"{rel.as_posix()}::{key} (dropped {', '.join(sorted(added))})" for key, (added, _) in missing.items() if not stand_in(key, set())]
    labels = {"skip": "skipped", "todo": "todo", "only": "only", "fails": "fails"}
    for rel in sorted(in_agent):
        mine = results.get(str(agent / rel), {})
        theirs = results.get(str(reference / rel), {}) if (reference / rel).is_file() else {}
        if "error" in mine:
            if "error" not in theirs:
                weak.append(f"{rel.as_posix()} ({mine['error']})")
            continue
        reference_tests = {} if "error" in theirs else theirs.get("tests", {})
        for key, info in mine.get("tests", {}).items():
            extra = set(info["mods"]) - set(reference_tests.get(key, {}).get("mods", []))
            weak += [f"{rel.as_posix()}::{key} ({labels[m]})" for m in sorted(extra)]
    return weak


# ----------------------------------------------------------------------------------- the hidden run

VITEST_CONFIG = """// Written by the grader (eval/check_node.py). The repo's own config, if it has one, is the base: its
// aliases, transform, environment and setup files apply to the hidden test. What chooses which files run is ours.
import {{ defineConfig }} from 'vitest/config'
{base_import}
export default defineConfig(async (env) => {{
  let base: any = {{}}
{base_load}
  const test = {{ ...(base.test ?? {{}}) }}
  delete test.projects
  delete test.workspace
  return {{
    ...base,
    test: {{
      ...test,
      dir: undefined,
      include: ['{hidden_dir}/{hidden_test}'],
      exclude: ['**/node_modules/**'],
      includeSource: [],
      coverage: {{ enabled: false }},
      passWithNoTests: false,
      watch: false,
    }},
  }}
}})
"""

JEST_CONFIG = """// Written by the grader (eval/check_node.py). The repo's own jest config is the base: its transform, setup files and
// environment apply to the hidden test. What chooses which files run is ours.
const path = require('path')
const baseFile = {base_file}
const base = baseFile ? require(baseFile) : {{}}
const baseRoot = baseFile ? path.resolve(path.dirname(baseFile), base.rootDir || '.') : path.resolve(__dirname, '..')
const {{ testRegex, testMatch, projects, ...rest }} = base
// ts-jest would type-check the grader's own files under the repo's tsconfig and refuse them for its strictness or
// target; the type-check a task wants is run explicitly by its hidden test (helpers.typecheck).
const tsJest = {{ ...((rest.globals || {{}})['ts-jest'] || {{}}), diagnostics: false }}
module.exports = {{
  ...rest,
  globals: {{ ...(rest.globals || {{}}), 'ts-jest': tsJest }},
  rootDir: baseRoot,
  roots: undefined,
  testMatch: ['<rootDir>/{hidden_dir}/{hidden_test}'],
  testPathIgnorePatterns: ['/node_modules/'],
  collectCoverage: false,
  bail: 0,
}}
"""


def repo_vitest_config(repo: Path) -> str | None:
    for stem in ("vitest.config", "vite.config"):
        for ext in ("ts", "mts", "cts", "js", "mjs", "cjs"):
            if (repo / f"{stem}.{ext}").is_file():
                return f"{stem}.{ext}"
    return None


def write_hidden_config(task: Task, repo: Path) -> Path:
    hidden = repo / HIDDEN_DIR
    if task.spec.runner == "vitest":
        config = repo_vitest_config(repo)
        load = (
            "  const loaded: any = (baseModule as any).default ?? baseModule\n"
            "  base = typeof loaded === 'function' ? await loaded(env) : await loaded"
        )
        text = VITEST_CONFIG.format(
            base_import=f"import * as baseModule from '../{config}'" if config else "",
            base_load=load if config else "",
            hidden_dir=HIDDEN_DIR,
            hidden_test=HIDDEN_TEST,
        )
        path = hidden / "vitest.hidden.config.mts"
    else:
        base = json.dumps(str((repo / task.spec.config).resolve())) if task.spec.config else "null"
        text = JEST_CONFIG.format(base_file=base, hidden_dir=HIDDEN_DIR, hidden_test=HIDDEN_TEST)
        path = hidden / "jest.hidden.config.js"
    path.write_text(text)
    return path


def run_hidden_node(task: Task, repo: Path, timeout: float | None = None) -> dict:
    """Copy the hidden test, the TypeScript helpers and a grader-written config into repo/_eval_check and run just
    that test with the repo's own runner. Called only after the agent has finished and its own suite has run, in a
    copy of its tree, so the agent never sees any of it."""
    repo = Path(repo)
    hidden = repo / HIDDEN_DIR
    # Emptied first: whatever the agent left under this name (a check_helpers.mjs resolves before the grader's .ts) is not ours.
    remove(hidden)
    hidden.mkdir()
    shutil.copy(task.hidden_test, hidden / HIDDEN_TEST)
    shutil.copy(HELPERS_TS, hidden / HELPERS_TS.name)
    config = write_hidden_config(task, repo)
    target = f"{HIDDEN_DIR}/{HIDDEN_TEST}"
    with tempfile.TemporaryDirectory(prefix="ripple-eval-report-") as scratch:
        report = Path(scratch) / "report.json"
        flags = report_flags(task.spec.runner, report)
        if task.spec.runner == "vitest":
            command = ["vitest", "run", "--no-cache", "--config", config.relative_to(repo).as_posix(), *flags, target]
        else:
            command = ["jest", "--config", config.relative_to(repo).as_posix(), *flags, "--runTestsByPath", target]
        seconds = timeout or task.check_timeout
        code, output = run_command(command, repo, seconds, runner_env(task, repo))
        if code is None:
            return {"passed": False, "summary": f"the runner was killed after {seconds}s", "failures": []}
        verdict, _ids = read_report(report, repo, code, output)
    verdict.pop("tests_passed", None)
    return verdict
