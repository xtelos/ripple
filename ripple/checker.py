"""Run the repository's tests and hand back a verdict an agent can read.

A full pytest log can be thousands of lines, and pasting it into an agent's
context crowds out the code it is working on. So this returns pass or fail,
pytest's one-line summary, and the first few failing test ids, trimmed. The
agent can run the full command itself if it needs more.
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import signal
import subprocess
import time
from pathlib import Path

MAX_FAILURES = 10
MAX_LINE = 240
TAIL_LINES = 30
SUMMARY = re.compile(r"\b(passed|failed|errors?|skipped|no tests ran)\b")


def default_test_command(repo: Path) -> list[str]:
    """python -m pytest -q, using the repo's own virtualenv when it has one.

    ripple usually runs from its own environment, which does not have the
    project's dependencies installed, so its own interpreter is the wrong one.
    """
    for venv in (".venv", "venv"):
        python = repo / venv / "bin" / "python"
        if python.exists():
            return [str(python), "-m", "pytest", "-q"]
    python = shutil.which("python") or shutil.which("python3") or "python"
    return [python, "-m", "pytest", "-q"]


def run_check(
    repo: str | Path,
    command: list[str] | str | None = None,
    timeout: float = 300,
    extra_args: list[str] | None = None,
) -> dict:
    repo = Path(repo).resolve()
    if isinstance(command, str):
        command = shlex.split(command)
    argv = list(command or default_test_command(repo)) + list(extra_args or [])
    started = time.monotonic()
    # A new session lets a timeout kill the whole process tree, including
    # any workers the test runner started.
    proc = subprocess.Popen(
        argv,
        cwd=repo,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        text=True,
        errors="replace",
        start_new_session=True,
    )
    try:
        output, _ = proc.communicate(timeout=timeout)
        timed_out = False
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        output, _ = proc.communicate()
        timed_out = True

    lines = [line.rstrip() for line in (output or "").splitlines()]
    failures = [line[:MAX_LINE] for line in lines if line.startswith(("FAILED ", "ERROR "))]
    summary = next((line.strip("= ") for line in reversed(lines) if SUMMARY.search(line)), "")
    passed = proc.returncode == 0 and not timed_out
    result = {
        "passed": passed,
        "exit_code": None if timed_out else proc.returncode,
        "timed_out": timed_out,
        "duration_s": round(time.monotonic() - started, 2),
        "command": shlex.join(argv),
        "summary": summary,
        "failures": failures[:MAX_FAILURES],
        "failure_count": len(failures),
    }
    if not passed and not failures:
        # Collection errors, import errors, a crash: nothing to list, so show
        # the end of the output, where the cause usually is.
        result["output_tail"] = [line[:MAX_LINE] for line in lines[-TAIL_LINES:]]
    return result
