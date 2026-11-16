"""Load the captured `mvn test` runs of tests/fixtures/java_check and judge them without a JDK."""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import signal
import time
from pathlib import Path

from ripple import java_check as jc

ROOT = Path(__file__).resolve().parent / "fixtures" / "java_check"
PROJECTS = ROOT / "projects"
CAPTURES = ROOT / "captures"
HOUR_NS = 3_600_000_000_000


class Hung(BaseException):
    """Raised by `deadline`. Not an Exception (nor a TimeoutError, which is an OSError): the code under test catches those."""


@contextlib.contextmanager
def deadline(seconds: int = 10):
    """Fail the test, instead of hanging it, when the block is still running after `seconds` (main thread, SIGALRM)."""

    def expired(*_):
        raise Hung(f"still running after {seconds}s")

    previous = signal.signal(signal.SIGALRM, expired)
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


def names() -> list[str]:
    return sorted(p.name for p in CAPTURES.iterdir() if p.is_dir())


def meta(name: str) -> dict:
    return json.loads((CAPTURES / name / "meta.json").read_text())


def stdout_of(name: str) -> str:
    return (CAPTURES / name / "stdout.txt").read_text()


def place(repo: Path, source: Path, when_ns: int | None, only: list[str] | None = None) -> None:
    """Put the report files a capture kept back where Maven wrote them (<module>/target/surefire-reports), written at when_ns."""
    for file in sorted(source.rglob("*.xml")) if source.exists() else []:
        rel = file.relative_to(source)
        module = rel.parent.as_posix()
        if only is not None and rel.as_posix() not in {Path(o).as_posix() for o in only}:
            continue
        target = repo / ("" if module == "." else module) / "target" / "surefire-reports" / rel.name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(file, target)
        if when_ns is not None:
            os.utime(target, ns=(when_ns, when_ns))


def place_classes(repo: Path, source: Path) -> None:
    """Put the compiled classes a capture kept (<module>/<kind>/...) where Maven left them: <module>/target/<kind>/..."""
    for file in sorted(source.rglob("*.class")) if source.exists() else []:
        rel = file.relative_to(source)
        module, kind, rest = rel.parts[0], rel.parts[1], rel.parts[2:]
        target = repo / ("" if module == "_" else module) / "target" / kind / Path(*rest)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(file, target)


def make_repo(tmp: Path, name: str) -> Path:
    """The capture's tiny project, as it was when the run started (with the edits the capture made after its earlier run)."""
    data = meta(name)
    repo = tmp / "repo"
    shutil.copytree(PROJECTS / data["project"], repo)
    for rel, text in (data.get("edits") or {}).items():
        (repo / rel).write_text(text)
    return repo.resolve()


def run_of(tmp: Path, name: str, invocation: jc.Invocation | None = None, output: str | None = None, exit_code=..., timed_out=None, request=..., classes=True):
    """(repo, project, Run) for a capture: the earlier run's reports are stale, this run's are fresh, found the way run() finds them."""
    data = meta(name)
    repo = make_repo(tmp, name)
    now = time.time_ns()
    if classes:
        place_classes(repo, CAPTURES / name / "classes")
    place(repo, CAPTURES / name / "before", now - HOUR_NS)
    before = jc.snapshot(repo)
    started = time.time_ns()
    place(repo, CAPTURES / name / "reports", time.time_ns(), data["written"])
    mine, stale = jc.fresh(repo, before, started)
    project = jc.find_project(repo)
    cases, unreadable = [], []
    for rel in mine:
        got, reason = jc.read_report(repo, project, rel)
        cases.extend(got)
        if reason:
            unreadable.append(reason)
    if invocation is None:
        request = data["request"] if request is ... else request
        wants = jc.parse_wants(repo, project, jc.normalize(repo, request)) if request else []
        invocation = jc.plan(project, wants)[0]
    code = data["exit_code"] if exit_code is ... else exit_code
    run = jc.Run(
        invocation,
        stdout_of(name) if output is None else output,
        code,
        data["timed_out"] if timed_out is None else timed_out,
        True,
        cases,
        unreadable,
        stale,
    )
    return repo, project, run


def verdict(tmp: Path, name: str, **kwargs) -> dict:
    repo, project, run = run_of(tmp, name, **kwargs)
    return jc.judge(repo, project, [run], "mvn (captured)", 0.5, meta(name)["timeout_s"])


def by_hand(cls: str, method: str | None = None, module: str = "", selectors: tuple[str, ...] = (), params=None) -> jc.Invocation:
    """The invocation of a run the planner would never make (a method that exists in source and matches no test at run time)."""
    want = jc.Want("by hand", module, cls, method, params)
    return jc.Invocation(None, selectors or (f"{cls}#{method}",), False, (want,))
