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
import tempfile
import time
from pathlib import Path

MAX_FAILURES = 10
MAX_LINE = 240
MAX_COMMAND = 2000
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


def check_tests(repo: Path, tests: list[str]) -> None:
    """Refuse anything that is not a test path or pytest node id inside repo.

    The entries are appended to the test command, so an option would change
    what the command does: --basetemp=DIR, for one, makes pytest delete DIR.
    A path outside the repo would run code nobody pointed ripple at.
    """
    for test in tests:
        if not isinstance(test, str) or not test or test.startswith("-"):
            raise ValueError(f"not a test path or node id: {test!r}")
        path = (repo / test.split("::")[0]).resolve()
        if not path.is_relative_to(repo) or not path.exists():
            raise ValueError(f"{test!r} is not a file or directory inside {repo}")


def clip(text: str, limit: int = MAX_LINE) -> str:
    """text cut to limit characters, saying how many were cut."""
    if len(text) <= limit:
        return text
    return f"{text[:limit]} [truncated {len(text) - limit} chars]"


def run_check(
    repo: str | Path,
    command: list[str] | str | None = None,
    timeout: float = 300,
    tests: list[str] | None = None,
) -> dict:
    """Run the test command, or just the given test paths / node ids, and summarize.

    Raises ValueError if an entry in tests is not a path or node id inside repo.
    """
    repo = Path(repo).resolve()
    check_tests(repo, tests or [])
    if isinstance(command, str):
        command = shlex.split(command)
    argv = list(command or default_test_command(repo)) + list(tests or [])
    started = time.monotonic()
    # Output goes to a temporary file rather than a pipe. With a pipe, reading
    # to the end waits for every process that inherited it, and a helper the
    # tests left running in the background could hold the result up forever.
    # With a file, the run is over when the test command itself exits.
    with tempfile.TemporaryFile() as out:
        # A new session gives the run its own process group, so a timeout can
        # kill the whole tree, including any workers the test runner started.
        proc = subprocess.Popen(
            argv,
            cwd=repo,
            stdout=out,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
        try:
            proc.wait(timeout=timeout)
            timed_out = False
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
            timed_out = True
        out.seek(0)
        output = out.read().decode(errors="replace")

    lines = [line.rstrip() for line in output.splitlines()]
    failures = [clip(line) for line in lines if line.startswith(("FAILED ", "ERROR "))]
    summary = next((line.strip("= ") for line in reversed(lines) if SUMMARY.search(line)), "")
    passed = proc.returncode == 0 and not timed_out
    result = {
        "passed": passed,
        "exit_code": None if timed_out else proc.returncode,
        "timed_out": timed_out,
        "duration_s": round(time.monotonic() - started, 2),
        "command": clip(shlex.join(argv), MAX_COMMAND),
        "summary": clip(summary),
        "failures": failures[:MAX_FAILURES],
        "failure_count": len(failures),
    }
    if not passed and not failures:
        # Collection errors, import errors, a crash: nothing to list, so show
        # the end of the output, where the cause usually is.
        result["output_tail"] = [clip(line) for line in lines[-TAIL_LINES:]]
    return result
