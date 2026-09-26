"""Load the eval tasks and lay out a fresh copy of a task's fixture.

Each task is a directory under eval/tasks/<id>/ holding:

    task.json      id, fixture (path from the repo root), prompt, min_tests, why
    check_test.py  the hidden test: the agent never sees it, it runs after the agent is done
    solution/      a reference edit, overlaid on the fixture, that proves the check can pass
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent
REPO_ROOT = EVAL_DIR.parent
TASKS_DIR = EVAL_DIR / "tasks"
REQUIRED = ("id", "fixture", "prompt", "min_tests", "why")
IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache")


@dataclass(frozen=True)
class Task:
    id: str
    fixture: Path  # the starting repo, absolute
    prompt: str  # exactly what the agent is told
    min_tests: int  # the fixture's suite must still run at least this many tests
    why: str  # what makes the safe edit hard to find with grep alone
    dir: Path

    @property
    def hidden_test(self) -> Path:
        return self.dir / "check_test.py"

    @property
    def solution(self) -> Path:
        return self.dir / "solution"


def load_task(task_dir: Path) -> Task:
    data = json.loads((task_dir / "task.json").read_text())
    missing = [key for key in REQUIRED if key not in data]
    if missing:
        raise ValueError(f"{task_dir.name}: task.json is missing {', '.join(missing)}")
    if data["id"] != task_dir.name:
        raise ValueError(f"{task_dir.name}: task.json says its id is {data['id']!r}")
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
    )
    if not task.hidden_test.is_file():
        raise ValueError(f"{task_dir.name}: no check_test.py")
    return task


def load_tasks(tasks_dir: Path = TASKS_DIR, only: list[str] | None = None) -> list[Task]:
    tasks = [load_task(p) for p in sorted(tasks_dir.iterdir()) if (p / "task.json").is_file()]
    if only:
        known = {t.id for t in tasks}
        unknown = [name for name in only if name not in known]
        if unknown:
            raise ValueError(f"unknown task: {', '.join(unknown)}")
        tasks = [t for t in tasks if t.id in only]
    return tasks


def copy_fixture(task: Task, dest: Path) -> Path:
    """A fresh copy of the task's starting repo at dest (which must not exist)."""
    shutil.copytree(task.fixture, dest, ignore=IGNORE)
    return dest


def apply_solution(task: Task, repo: Path) -> None:
    """Overlay the reference solution's files onto a copy of the fixture."""
    shutil.copytree(task.solution, repo, ignore=IGNORE, dirs_exist_ok=True)
