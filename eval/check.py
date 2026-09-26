"""Decide whether an agent's edit succeeded. Both parts must pass:

1. The fixture's own test suite, as the agent left it, passes and still runs
   at least task.min_tests tests, so deleting the failing tests does not count.
2. The task's hidden test passes. The agent never sees it. It checks that the
   change was made (the new name exists, the old one is gone) and that every
   caller still works end to end, which is the part a missed caller breaks.

Tests run with ripple's own checker (ripple/checker.py), which returns pytest's
summary line and the failing test ids rather than the whole log.
"""

from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

from ripple.checker import run_check

from .loader import TASKS_DIR, Task

HIDDEN_DIR = "_eval_check"
HIDDEN_TEST = "test_eval_hidden.py"
HELPERS = TASKS_DIR / "check_helpers.py"
PASSED = re.compile(r"\b(\d+) passed\b")


def count_passed(summary: str) -> int:
    match = PASSED.search(summary or "")
    return int(match.group(1)) if match else 0


def pytest_command(python: str, target: str) -> list[str]:
    return [python, "-m", "pytest", "-q", "-p", "no:cacheprovider", target]


def check_task(task: Task, repo: Path, python: str = sys.executable, timeout: float = 180) -> dict:
    """Run the fixture's suite, then the hidden test, in repo. Returns the verdict and why."""
    repo = Path(repo)
    suite = run_check(repo, command=pytest_command(python, "tests"), timeout=timeout)
    tests_passed = count_passed(suite["summary"])

    # Copied in only now, after the agent has finished and its suite has run.
    hidden_dir = repo / HIDDEN_DIR
    hidden_dir.mkdir(exist_ok=True)
    shutil.copy(task.hidden_test, hidden_dir / HIDDEN_TEST)
    shutil.copy(HELPERS, hidden_dir / HELPERS.name)
    hidden = run_check(repo, command=pytest_command(python, f"{HIDDEN_DIR}/{HIDDEN_TEST}"), timeout=timeout)

    suite_ok = suite["passed"] and tests_passed >= task.min_tests
    return {
        "success": bool(suite_ok and hidden["passed"]),
        "suite_passed": suite["passed"],
        "tests_passed": tests_passed,
        "min_tests": task.min_tests,
        "hidden_passed": hidden["passed"],
        "suite_summary": suite["summary"],
        "hidden_summary": hidden["summary"],
        "failures": suite["failures"] + hidden["failures"],
    }
