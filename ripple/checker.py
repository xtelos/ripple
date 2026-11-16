"""Run the repository's tests and hand back a verdict an agent can read.

A full pytest log can be thousands of lines, and pasting it into an agent's
context crowds out the code it is working on. So this returns pass or fail,
pytest's one-line summary, and the first few failing test ids, trimmed. The
agent can run the full command itself if it needs more.
"""

from __future__ import annotations

import importlib
import os
import re
import shlex
import signal
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from .profile import PYTHON, LanguageProfile

MAX_FAILURES = 10
MAX_LINE = 240
MAX_COMMAND = 2000
TAIL_LINES = 30
SUMMARY = re.compile(r"\b(passed|failed|errors?|skipped|no tests ran)\b")


def default_test_command(repo: Path, profile: LanguageProfile = PYTHON) -> list[str]:
    """The command the profile's language runs a repo's tests with (for Python, see profile.py)."""
    return profile.default_test_command(repo)


def check_tests(repo: Path, tests: list[str]) -> None:
    """Refuse anything that is not a test path or pytest node id inside repo.

    The entries are appended to the test command, so an option would change
    what the command does: --basetemp=DIR, for one, makes pytest delete DIR.
    An argument starting with @ is one too: pytest reads options from the
    file it names. A path outside the repo would run code nobody pointed
    ripple at.
    """
    for test in tests:
        if not isinstance(test, str) or not test or test.startswith(("-", "@")):
            raise ValueError(f"not a test path or node id: {test!r}")
        path = (repo / test.split("::")[0]).resolve()
        if not path.is_relative_to(repo) or not path.exists():
            raise ValueError(f"{test!r} is not a file or directory inside {repo}")


def clip(text: str, limit: int = MAX_LINE) -> str:
    """text cut to limit characters, saying how many were cut."""
    if len(text) <= limit:
        return text
    return f"{text[:limit]} [truncated {len(text) - limit} chars]"


@dataclass
class Process:
    """What a finished (or killed) test process left: its output, exit code and whether it was killed for time."""

    stdout: str
    stderr: str
    returncode: int
    timed_out: bool


def run_process(argv: list[str], cwd: Path, timeout: float, merge_stderr: bool = True) -> Process:
    """Run argv in cwd for at most timeout seconds, killing the whole process tree if it takes longer.

    merge_stderr puts stderr in stdout, in order (what the log-reading checks want). A runner that reads
    structured output on stdout asks for it apart (merge_stderr=False), so a stray line on stderr cannot
    corrupt it.
    """
    # Output goes to temporary files rather than pipes. With a pipe, reading
    # to the end waits for every process that inherited it, and a helper the
    # tests left running in the background could hold the result up forever.
    # With a file, the run is over when the test command itself exits.
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        # A new session gives the run its own process group, so a timeout can
        # kill the whole tree, including any workers the test runner started.
        proc = subprocess.Popen(
            argv,
            cwd=cwd,
            stdout=out,
            stderr=subprocess.STDOUT if merge_stderr else err,
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
        err.seek(0)
        return Process(out.read().decode(errors="replace"), err.read().decode(errors="replace"), proc.returncode, timed_out)


def runner_for(profile: LanguageProfile):
    """The module that runs this language's tests itself when `check` is given no command, or None.

    This is the seam for a language whose runner reads structured output instead of a log: a module with
    `normalize(repo, tests) -> tests` (turns the ids `impact` hands out for that language into
    path-shaped ones, before check_tests refuses anything) and `run(repo, tests, timeout) -> dict` (the
    keys of run_check's result, plus whatever it can prove beyond them), added to RUNNERS under the
    profile's name. go_check.py is the one for Go. The profile is untouched on purpose: its
    fields are hashed into the cache key, so a profile's default_test_command stays what it was.
    """
    module = RUNNERS.get(profile.name)
    return importlib.import_module(module, __package__) if module else None


# profile name -> module of the runner (see runner_for)
RUNNERS = {"go": ".go_check"}


def run_check(
    repo: str | Path,
    command: list[str] | str | None = None,
    timeout: float = 300,
    tests: list[str] | None = None,
    profile: LanguageProfile = PYTHON,
) -> dict:
    """Run the test command, or just the given test paths / node ids, and summarize.

    Raises ValueError if an entry in tests is not a path or node id inside repo.
    """
    repo = Path(repo).resolve()
    if isinstance(command, str):
        command = shlex.split(command)
    runner = None if command else runner_for(profile)
    if runner is not None:
        tests = runner.normalize(repo, list(tests or []))
    check_tests(repo, tests or [])
    if runner is not None:
        return runner.run(repo, list(tests or []), timeout)
    argv = list(command or default_test_command(repo, profile)) + list(tests or [])
    started = time.monotonic()
    outcome = run_process(argv, repo, timeout)
    output, timed_out = outcome.stdout, outcome.timed_out

    lines = [line.rstrip() for line in output.splitlines()]
    failures = [clip(line) for line in lines if line.startswith(profile.failure_prefixes)]
    summary = next((line.strip("= ") for line in reversed(lines) if SUMMARY.search(line)), "")
    passed = outcome.returncode == 0 and not timed_out
    result = {
        "passed": passed,
        "exit_code": None if timed_out else outcome.returncode,
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
