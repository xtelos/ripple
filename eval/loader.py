"""Load the eval tasks and lay out a fresh copy of a task's fixture.

Each task is a directory under eval/tasks/<id>/ holding:

    task.json      id, prompt, min_tests, why, and where the starting repo comes from:
                   "fixture" (a path from this repo's root) or "repo" (a name in eval/repos.json,
                   a pinned open-source repository fetched into a cache by `python -m eval.fetch`)
    check_test.py  the hidden test: the agent never sees it, it runs after the agent is done
    solution/      a reference edit, overlaid on the fixture, that proves the check can pass
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .repos import RepoSpec, load_repos

EVAL_DIR = Path(__file__).resolve().parent
REPO_ROOT = EVAL_DIR.parent
TASKS_DIR = EVAL_DIR / "tasks"
REQUIRED = ("id", "prompt", "min_tests", "why")
IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache")
FIXTURE_TIMEOUT, REAL_TIMEOUT = 180, 900  # seconds for each of the two test runs in the check


@dataclass(frozen=True)
class Task:
    id: str
    fixture: Path  # the starting repo, absolute (for a real repo: its cache checkout, which may not exist yet)
    prompt: str  # exactly what the agent is told
    min_tests: int  # the starting repo's suite must still run at least this many tests
    why: str  # what makes the safe edit hard to find with grep alone
    dir: Path
    spec: RepoSpec | None = None  # set for a task over a pinned real repository

    @property
    def hidden_test(self) -> Path:
        return self.dir / "check_test.py"

    @property
    def solution(self) -> Path:
        return self.dir / "solution"

    @property
    def solution_patch(self) -> Path:
        return self.dir / "solution.patch"

    @property
    def available(self) -> bool:
        """Whether the starting repo (and, for a real repo, its test venv) is on this machine."""
        return self.spec.available if self.spec else self.fixture.is_dir()

    @property
    def python(self) -> str:
        return str(self.spec.venv_python) if self.spec else sys.executable

    @property
    def tests_dir(self) -> str:
        return self.spec.tests if self.spec else "tests"

    @property
    def pythonpath(self) -> tuple[str, ...]:
        return self.spec.pythonpath if self.spec else ()

    @property
    def check_timeout(self) -> int:
        return REAL_TIMEOUT if self.spec else FIXTURE_TIMEOUT


def load_task(task_dir: Path, repos: dict[str, RepoSpec] | None = None) -> Task:
    data = json.loads((task_dir / "task.json").read_text())
    missing = [key for key in REQUIRED if key not in data]
    if missing:
        raise ValueError(f"{task_dir.name}: task.json is missing {', '.join(missing)}")
    if data["id"] != task_dir.name:
        raise ValueError(f"{task_dir.name}: task.json says its id is {data['id']!r}")
    if ("fixture" in data) == ("repo" in data):
        raise ValueError(f"{task_dir.name}: task.json needs exactly one of fixture and repo")
    spec = None
    if "repo" in data:
        repos = load_repos() if repos is None else repos
        if data["repo"] not in repos:
            raise ValueError(f"{task_dir.name}: repo {data['repo']!r} is not in eval/repos.json")
        spec = repos[data["repo"]]
        fixture = spec.checkout
    else:
        fixture = (REPO_ROOT / data["fixture"]).resolve()
        if not fixture.is_dir():
            raise ValueError(f"{task_dir.name}: fixture {data['fixture']} is not a directory")
    task = Task(
        id=data["id"],
        fixture=fixture,
        prompt=data["prompt"],
        min_tests=int(data["min_tests"]),
        why=data["why"],
        dir=task_dir,
        spec=spec,
    )
    if not task.hidden_test.is_file():
        raise ValueError(f"{task_dir.name}: no check_test.py")
    return task


def load_tasks(tasks_dir: Path = TASKS_DIR, only: list[str] | None = None) -> list[Task]:
    repos = load_repos()
    tasks = [load_task(p, repos) for p in sorted(tasks_dir.iterdir()) if (p / "task.json").is_file()]
    if only:
        known = {t.id for t in tasks}
        unknown = [name for name in only if name not in known]
        if unknown:
            raise ValueError(f"unknown task: {', '.join(unknown)}")
        tasks = [t for t in tasks if t.id in only]
    return tasks


def copy_fixture(task: Task, dest: Path) -> Path:
    """A fresh copy of the task's starting repo at dest (which must not exist)."""
    if not task.available:
        raise FileNotFoundError(f"{task.id}: {task.fixture} is not there; run `python -m eval.fetch`")
    shutil.copytree(task.fixture, dest, symlinks=True, ignore=IGNORE)
    return dest


def apply_solution(task: Task, repo: Path) -> None:
    """Apply the reference solution to a copy of the starting repo: a solution.patch
    (a real-repo task keeps only its diff, never upstream files) or a solution/ overlay."""
    if task.solution_patch.is_file():
        done = subprocess.run(
            ["git", "apply", "--whitespace=nowarn", str(task.solution_patch)], cwd=repo, capture_output=True, text=True
        )
        if done.returncode:
            raise RuntimeError(f"{task.id}: solution.patch does not apply: {done.stderr.strip()[-500:]}")
        return
    shutil.copytree(task.solution, repo, ignore=IGNORE, dirs_exist_ok=True)
