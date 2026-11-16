"""Load the captured `go test -json` runs of tests/fixtures/go_check and judge them without Go."""

from __future__ import annotations

import json
from pathlib import Path

from ripple.go_check import Invocation, Run, find_modules, judge, parse_stream, parse_wants

ROOT = Path(__file__).resolve().parent / "fixtures" / "go_check"
MODULES = ROOT / "modules"
CAPTURES = ROOT / "captures"

# What every `go test` ripple starts has before its -run: the flags that win over GOFLAGS (see go_check.BASE_ARGV).
# Written out here on purpose: a test that built it from the code under test would never see it change.
BASE = ["go", "test", "-json", "-count=1", "-skip=", "-list=", "-c=false", "-fuzz="]
# The same without the program name, as a fake `go` on PATH receives it.
BASE_ARGS = BASE[1:]


def go_argv(*packages: str, run: str | None = None, program: bool = True) -> list[str]:
    """The argv of a `go test` run of the packages: with its own -run pattern, or with `-run=` (which says "everything")."""
    argv = BASE + (["-run", run] if run else ["-run="]) + list(packages)
    return argv if program else argv[1:]


def names() -> list[str]:
    return sorted(p.stem for p in CAPTURES.glob("*.json"))


def meta(name: str) -> dict:
    return json.loads((CAPTURES / f"{name}.json").read_text())


def stream_text(name: str) -> str:
    return (CAPTURES / f"{name}.jsonl").read_text()


def invocation(name: str) -> Invocation:
    """The run the capture recorded, as the planner would have described it."""
    data = meta(name)
    repo = MODULES / data["module"]
    cwd = "" if data["cwd"] == "." else data["cwd"]
    module = next(m for m in find_modules(repo) if m.dir == cwd)
    argv = data["argv"]
    run = argv[argv.index("-run") + 1] if "-run" in argv else None
    packages = [a for a in argv[len(BASE):] if a.startswith(".")]
    if argv[: len(BASE)] != BASE:  # a plain `go test` capture (GOFLAGS shown without ripple's overrides)
        packages = [a for a in argv[4:] if a.startswith(".")]
    return Invocation(module, run, packages)


def run_of(name: str, text: str | None = None, exit_code: int | None = None, timed_out: bool = False) -> Run:
    data = meta(name)
    code = data["exit_code"] if exit_code is None else exit_code
    return Run(invocation(name), parse_stream(stream_text(name) if text is None else text), code, timed_out, data["stderr"])


def verdict(name: str, text: str | None = None, timed_out: bool = False, runs: list[Run] | None = None) -> dict:
    """The verdict `check` gives for the captured run (its request is the capture's own, none for a hand-made one)."""
    data = meta(name)
    repo = MODULES / data["module"]
    wants = parse_wants(repo, data["request"]) if data["request"] else []
    runs = runs or [run_of(name, text, timed_out=timed_out)]
    # the GOFLAGS the capture was made with, when it had any: what `go env GOFLAGS` would have said
    extra = {"goflags": data["env"]["GOFLAGS"]} if data["env"].get("GOFLAGS") else {}
    return judge(repo, runs, wants, "go test (captured)", 0.5, 300, **extra)
