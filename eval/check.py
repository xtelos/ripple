"""Decide whether an agent's edit succeeded. Both parts must pass:

1. The task's own test suite passes against the agent's source. The suite is not
   the agent's: after the agent finishes, its copy of the repository is copied,
   and into that copy go the starting repo's pristine tests directory (with the
   reference solution's own test edits for a task whose solution edits tests) and
   every conftest.py, pytest and packaging config, sitecustomize and shadowing
   module of the reference. Deleting, gutting, skipping or conftest-patching a
   test therefore changes nothing: the agent's source is judged by known-good tests.
   Every test the reference solution passes must pass, and at least task.min_tests run.
   An agent that deleted or hollowed out a test the reference edited is also marked down
   (weakened_tests), though its version is never run; one that only renamed it is not.
2. The task's hidden test passes. The agent never sees it. It checks that the
   change was made (the new name exists, the old one is gone) and that every
   caller still works end to end, which is the part a missed caller breaks. It
   scans the agent's own files, tests included, so a test caller left on the old
   name is still a miss.

Tests run with ripple's own checker conventions (ripple/checker.py): pytest's
summary line and the failing test ids rather than the whole log.

A task over a node repository (vitest or jest, spec.is_node) is graded the same way by eval/check_node.py:
the functions below that differ for it (run_suite, neutralize, restore_tests, weakened_tests, run_hidden) hand
over to it first, and check_task gives the graded copy its node_modules link. Python tasks never reach it.
"""

from __future__ import annotations

import ast
import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
from pathlib import Path

from ripple.checker import MAX_FAILURES, SUMMARY, clip, run_check

from . import check_node
from .loader import IGNORE, TASKS_DIR, Task, apply_solution, copy_fixture, remove
from .repos import RepoSpec

HIDDEN_DIR = "_eval_check"
HIDDEN_TEST = "test_eval_hidden.py"
HELPERS = TASKS_DIR / "check_helpers.py"
PASSED = re.compile(r"\b(\d+) passed\b")
PASSED_ID = re.compile(r"^PASSED (\S+\.py::.+)$")  # a line of `pytest -rp`

# Files outside the tests directory that decide what pytest loads or reports: the agent's
# versions are replaced by the reference's, or removed when the reference has none.
CONFIG_FILES = frozenset({"pytest.ini", "tox.ini", "setup.cfg", "pyproject.toml"})
# Python imports these by name at startup (sitecustomize) or pytest does (conftest), as a module or a package.
HOOK_MODULES = frozenset({"conftest", "sitecustomize", "usercustomize"})
# The interpreter's own and the venv's importable names: a module of the same name at the repo
# root, which is on sys.path first, would replace pytest or the standard library.
IMPORTABLE = (
    "import os, site, sys; "
    "print(*{n.split('.')[0] for d in site.getsitepackages() for n in os.listdir(d)} | set(sys.stdlib_module_names), sep=chr(10))"
)


def count_passed(summary: str) -> int:
    match = PASSED.search(summary or "")
    return int(match.group(1)) if match else 0


def pytest_command(
    python: str,
    target: str | list[str],
    repo: Path | None = None,
    spec: RepoSpec | None = None,
    flags: tuple[str, ...] = (),
) -> list[str]:
    """pytest for one target (or several). For a real repo it runs under `env` with the
    variables from RepoSpec.env (the repo is never installed)."""
    targets = [target] if isinstance(target, str) else list(target)
    cmd = [python, "-m", "pytest", "-q", "-p", "no:cacheprovider", *flags, *targets]
    if spec is not None and repo is not None:
        cmd = ["env", *(f"{k}={v}" for k, v in spec.env(repo).items())] + cmd
    return cmd


def run_pytest(command: list[str], cwd: Path, timeout: float) -> tuple[int | None, str]:
    """(exit code, everything it prints), or (None, "") past the timeout. Bytecode is not
    written, so a run over a pinned cache copy would leave it as it found it."""
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
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


def run_suite(task: Task, repo: Path, python: str | None, timeout: float) -> tuple[dict, frozenset[str]]:
    """The task's whole suite in repo: a verdict like ripple's run_check, and the ids of the tests
    that ran to a pass. An id that holds the repo's own path (a parametrized tmp path) is written
    with a placeholder, so the same test has the same id in two copies. A node task is run by the
    repo's own runner (check_node)."""
    if task.is_node:
        return check_node.run_node_suite(task, repo, timeout)
    command = pytest_command(python or task.python, task.tests_dir, repo, task.spec, ("-rfEp",))
    code, output = run_pytest(command, repo, timeout)
    lines = [line.rstrip() for line in output.splitlines()]
    failures = [clip(line) for line in lines if line.startswith(("FAILED ", "ERROR "))]
    summary = next((line.strip("= ") for line in reversed(lines) if SUMMARY.search(line)), "")
    ids = (m.group(1) for m in map(PASSED_ID.match, lines) if m)
    here = [str(repo.resolve()), str(repo)]
    passed = frozenset(i.replace(here[0], "<repo>").replace(here[1], "<repo>") for i in ids)
    verdict = {"passed": code == 0, "summary": clip(summary), "failures": failures[:MAX_FAILURES]}
    return verdict, passed


def is_guarded(name: str) -> bool:
    return name in CONFIG_FILES or name.removesuffix(".py") in HOOK_MODULES


def guarded_paths(root: Path, skip: str) -> list[Path]:
    """Repo-relative paths of every guarded file or package under root, outside the directory
    `skip` (the tests directory, which is handled as a whole) and outside dot directories."""
    found = []
    for here, dirs, files in os.walk(root):
        rel = Path(here).relative_to(root)
        dirs[:] = [d for d in dirs if not d.startswith(".") and (rel / d).as_posix() != skip]
        for name in [*dirs, *files]:
            if is_guarded(name):
                found.append(rel / name)
        dirs[:] = [d for d in dirs if not is_guarded(d)]  # a guarded package is one thing
    return found


_IMPORTABLE: dict[str, frozenset[str]] = {}


def importable_names(python: str) -> frozenset[str]:
    if python not in _IMPORTABLE:
        _, out = run_pytest([python, "-I", "-c", IMPORTABLE], Path(tempfile.gettempdir()), 60)
        _IMPORTABLE[python] = frozenset(out.split())
    return _IMPORTABLE[python]


def neutralize(task: Task, graded: Path, reference: Path, python: str | None = None) -> None:
    """Put everything outside the tests directory that can change what pytest reports back to the
    reference's: conftest.py files, pytest and packaging config, sitecustomize, and any module
    added at the root of a source path that has the name of one pytest or the standard library
    uses. The agent's source files are left alone. A node task has its own list (check_node)."""
    if task.is_node:
        return check_node.neutralize_node(task, graded, reference)
    skip = task.tests_dir.rstrip("/")
    for rel in guarded_paths(graded, skip):
        remove(graded / rel)
    for rel in guarded_paths(reference, skip):
        target = graded / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if (reference / rel).is_dir():
            shutil.copytree(reference / rel, target, symlinks=True, ignore=IGNORE)
        else:
            shutil.copy2(reference / rel, target, follow_symlinks=False)
    names = importable_names(python or task.python)
    for sub in (*task.pythonpath, "."):
        root = graded / sub
        for entry in sorted(root.iterdir()) if root.is_dir() else []:
            if entry.name.split(".")[0] in names and not (reference / sub / entry.name).exists():
                remove(entry)


def restore_tests(task: Task, graded: Path, reference: Path) -> None:
    """Replace the tests directory with the reference's. If the agent made it a link, the link
    goes and what it pointed at is left alone. A node task also gets back every co-located test file."""
    if task.is_node:
        return check_node.restore_tests_node(task, graded, reference)
    target = graded / task.tests_dir
    remove(target)
    shutil.copytree(reference / task.tests_dir, target, symlinks=True, ignore=IGNORE)


def function_names(source: str) -> dict[str, frozenset[str]]:
    """For each function in a test file (a method is `Class::name`), every identifier it uses or
    defines: names, attributes, keyword arguments, parameters, imports, nested definitions and
    string constants that look like an identifier (a name given to `patch.object`)."""
    found: dict[str, frozenset[str]] = {}

    def visit(body: list[ast.stmt], prefix: str) -> None:
        for node in body:
            if isinstance(node, ast.ClassDef):
                visit(node.body, f"{prefix}{node.name}::")
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                names: set[str] = set()
                for n in ast.walk(node):
                    if isinstance(n, ast.Name):
                        names.add(n.id)
                    elif isinstance(n, ast.Attribute):
                        names.add(n.attr)
                    elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                        names.add(n.name)
                    elif isinstance(n, ast.arg):
                        names.add(n.arg)
                    elif isinstance(n, ast.keyword) and n.arg:
                        names.add(n.arg)
                    elif isinstance(n, ast.alias):
                        names.update({n.name.split(".")[-1], n.asname or ""} - {""})
                    elif isinstance(n, ast.Constant) and isinstance(n.value, str) and n.value.isidentifier():
                        names.add(n.value)
                found[prefix + node.name] = frozenset(names)

    visit(ast.parse(source).body, "")
    return found


def without_own_name(key: str, names: frozenset[str]) -> frozenset[str]:
    """The identifiers a test function carries less its own name (`Class::name` is keyed with its class): a
    function's name is among them, and a renamed test has another."""
    return names - {key.rsplit("::", 1)[-1]}


def is_test_name(key: str) -> bool:
    """Whether a function key (`Class::name` or `name`) is one pytest collects by default: its name starts with `test`."""
    return key.rsplit("::", 1)[-1].startswith("test")


def weakened_tests(task: Task, agent: Path, reference: Path) -> list[str]:
    """Tests the agent hollowed out. The grade never runs the agent's tests (they are replaced), but
    a test the reference solution edited was edited to use something the task introduced, and an
    agent that deletes it, or keeps its name and drops that use, has not done what it was asked in
    that file. A test function the reference changed must still use every identifier the change
    added to it. One the agent renamed (the prompt allows it) is found again by what it uses, not by
    its name. Empty when the agent's tests carry the same changes."""
    if task.is_node:
        return check_node.weakened_tests_node(task, agent, reference)
    weak = []
    tests = reference / task.tests_dir
    for path in sorted(tests.rglob("*.py")) if tests.is_dir() else []:
        rel = path.relative_to(reference)
        start = task.fixture / rel
        if not start.is_file() or start.read_bytes() == path.read_bytes():
            continue
        try:
            wanted, before = function_names(path.read_text()), function_names(start.read_text())
        except (SyntaxError, UnicodeDecodeError, ValueError):
            continue
        try:
            mine = function_names((agent / rel).read_text())
        except (OSError, SyntaxError, UnicodeDecodeError, ValueError):
            mine = {}
        # A test function can be renamed along with the calls in it (a test named for the function the task renames),
        # so one the agent no longer has by name is taken to be an unclaimed test of the agent's, in this file, that
        # carries every identifier it carries bar its own name, one agent test per reference test at most. A name
        # that is still there is never second-guessed.
        spare = [k for k in mine if k not in wanted]
        missing: dict[str, tuple[frozenset[str], list[str]]] = {}  # reference test -> what it added, the agent tests that could be it
        for name, names in wanted.items():
            added = names - before.get(name, names)
            if not added:
                continue
            if name in mine:
                if not added <= mine[name]:
                    weak.append(f"{rel.as_posix()}::{name} (dropped {', '.join(sorted(added - mine[name]))})")
            else:
                needs = without_own_name(name, names)
                fits = [k for k in spare if needs <= without_own_name(k, mine[k])]
                if is_test_name(name):  # only a test stands in for a test, never a fixture or a helper
                    fits = [k for k in fits if is_test_name(k)]
                missing[name] = (added, fits)
        taken: dict[str, str] = {}  # agent test -> the reference test it stands in for

        def stand_in(name: str, seen: set[str]) -> bool:
            """Give `name` an agent test, moving one already given away to another it fits if that frees it."""
            for candidate in missing[name][1]:
                if candidate in seen:
                    continue
                seen.add(candidate)
                if candidate not in taken or stand_in(taken[candidate], seen):
                    taken[candidate] = name
                    return True
            return False

        weak += [f"{rel.as_posix()}::{name} (dropped {', '.join(sorted(added))})" for name, (added, _) in missing.items() if not stand_in(name, set())]
    return weak


_REFERENCE: dict[str, frozenset[str]] = {}


def reference_ids(task: Task) -> tuple[frozenset[str], str]:
    """The tests that pass on the reference solution, with the starting repo's tests plus the
    solution's own edits to them (task 05 deletes one on purpose; a test a platform skips, rich's
    Windows-only ones on Linux, never passes, so it is not required). The run is in a scratch copy,
    so a test that writes files leaves the pinned cache alone. Cached for the process; a run that
    did not pass is not, and is reported as why the grade cannot be trusted."""
    if task.id not in _REFERENCE:
        with tempfile.TemporaryDirectory() as scratch:
            reference = copy_fixture(task, Path(scratch) / "reference")
            apply_solution(task, reference)
            verdict, ids = run_suite(task, reference, None, task.check_timeout)
        if not verdict["passed"] or not ids:
            return frozenset(), f"(the reference solution's own suite did not pass: {verdict['summary'] or 'no summary'})"
        _REFERENCE[task.id] = ids
    return _REFERENCE[task.id], ""


def run_hidden(task: Task, repo: Path, python: str | None = None, timeout: float | None = None) -> dict:
    """Copy the hidden test into repo and run just it, with no conftest loaded. Called only after
    the agent has finished and its own suite has run, so the agent never sees it."""
    if task.is_node:
        return check_node.run_hidden_node(task, Path(repo), timeout)
    repo = Path(repo)
    hidden_dir = repo / HIDDEN_DIR
    hidden_dir.mkdir(exist_ok=True)
    shutil.copy(task.hidden_test, hidden_dir / HIDDEN_TEST)
    shutil.copy(HELPERS, hidden_dir / HELPERS.name)
    (hidden_dir / "expected_unparseable.json").write_text(json.dumps(list(task.spec.unparseable) if task.spec else []))
    command = pytest_command(python or task.python, f"{HIDDEN_DIR}/{HIDDEN_TEST}", repo, task.spec, ("--noconftest",))
    return run_check(repo, command=command, timeout=timeout or task.check_timeout)


def check_task(task: Task, repo: Path, python: str | None = None, timeout: float | None = None) -> dict:
    """Grade the agent's copy in repo: the hidden test, then the suite against the reference's tests.

    Both run in a scratch copy of repo, so the agent's own tree is left as it ended. A real-repo
    task runs under that repo's own venv and test directory; a fixture task under this
    interpreter. The timeout is the task's own."""
    repo = Path(repo)
    timeout = timeout or task.check_timeout
    required, why = reference_ids(task)
    with tempfile.TemporaryDirectory() as scratch:
        graded = Path(scratch) / "graded"
        shutil.copytree(repo, graded, symlinks=True, ignore=IGNORE)
        if task.is_node:
            task.spec.link_deps(graded)  # IGNORE dropped any node_modules the agent had; this one is the cached install
        reference = copy_fixture(task, Path(scratch) / "reference")
        apply_solution(task, reference)
        neutralize(task, graded, reference, python)
        hidden = run_hidden(task, graded, python, timeout)  # the agent's own tests, for the stale callers in them
        shutil.rmtree(graded / HIDDEN_DIR)
        weak = weakened_tests(task, graded, reference)
        restore_tests(task, graded, reference)
        suite, passed = run_suite(task, graded, python, timeout)
    tests_passed = suite["tests_passed"] if "tests_passed" in suite else count_passed(suite["summary"])
    lost = (sorted(required - passed) if required else [why]) + weak
    suite_ok = suite["passed"] and tests_passed >= task.min_tests and not lost
    return {
        "success": bool(suite_ok and hidden["passed"]),
        "suite_passed": suite["passed"],
        "tests_passed": tests_passed,
        "min_tests": task.min_tests,
        "tests_lost": len(lost),
        "lost_tests": lost[:10],
        "hidden_passed": hidden["passed"],
        "suite_summary": suite["summary"],
        "hidden_summary": hidden["summary"],
        "failures": suite["failures"] + hidden["failures"],
    }
