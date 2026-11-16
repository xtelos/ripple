"""Load the captured `cargo test` runs of tests/fixtures/rs_check and judge them without cargo."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from ripple.rs_check import (
    BASE_ARGV,
    Invocation,
    Model,
    Pick,
    Resolution,
    Run,
    judge,
    normalize,
    parse_metadata,
    parse_output,
    parse_wants,
    plan,
    resolve,
)

ROOT = Path(__file__).resolve().parent / "fixtures" / "rs_check"
CRATES = ROOT / "crates"
CAPTURES = ROOT / "captures"
TARGET = "/tmp/ripple-rs-check-target"  # where the captures' `@TARGET@` is written back to: nothing reads it


def _parser_missing() -> str | None:
    """Why ripple cannot read Rust source here (the grammar, tree-sitter or tomli is not installed), None when it can."""
    from ripple.rs_indexer import RustNotInstalled, load_parser

    try:
        load_parser()
    except RustNotInstalled as error:
        return str(error)
    return None


# A test that indexes a Rust fixture (a request for a file, a directory or an id reads the crate's modules, and a failure is placed
# in its file) needs the Rust grammar. CI jobs without the language parsers skip it, as the other Rust tests do.
needs_rust_parser = pytest.mark.skipif(_parser_missing() is not None, reason="Rust support is not installed (pip install 'ripple-code[rust]')")


def names() -> list[str]:
    return sorted(p.name[: -len(".json")] for p in CAPTURES.glob("*.json"))


def meta(name: str) -> dict:
    return json.loads((CAPTURES / f"{name}.json").read_text())


def repo_of(name: str) -> Path:
    return Path(os.path.realpath(CRATES / meta(name)["crate"]))


def restore(text: str, name: str) -> str:
    return text.replace("@ROOT@", str(repo_of(name))).replace("@TARGET@", TARGET)


def output_text(name: str) -> str:
    return restore((CAPTURES / f"{name}.txt").read_text(), name)


def list_text(name: str) -> str:
    """What `cargo test ... -- --list` printed for the capture's run (the capture has one when ripple lists first)."""
    return restore((CAPTURES / f"{name}.list.txt").read_text(), name)


def listed_names(name: str) -> list[str]:
    [section] = [s for s in parse_output(list_text(name)).sections if not s.doc]
    return list(section.listed)


def space(name: str):
    return parse_metadata(restore(meta(name)["metadata"], name), repo_of(name))


def planned(name: str) -> list[Invocation]:
    """The runs ripple plans for the capture's request, a listing resolved with the capture's own list where it needs one."""
    data = meta(name)
    repo = repo_of(name)
    runs = plan([space(name)], parse_wants(repo, normalize(repo, data["request"])), repo)
    return runs


def resolved(name: str) -> tuple[Invocation, Resolution | None]:
    """The invocation of the capture: planned from its request when it has one (a listing resolved with the capture's list), else
    rebuilt from its argv (a hand-made command: its names are all it says)."""
    data = meta(name)
    rest = data["argv"][len(BASE_ARGV):]
    selectors = rest[: rest.index("--")] if "--" in rest else rest
    filters = rest[rest.index("--") + 2 :] if "--" in rest else []
    cwd = "" if data["cwd"] == "." else data["cwd"]
    if data["request"]:
        for inv in planned(name):
            if inv.selectors != selectors:
                continue
            if not inv.listing:
                return inv, None
            resolution = resolve(inv, listed_names(name))
            inv.names = resolution.names or inv.names  # nothing listed to run: ripple makes no run, the capture has the one the ids named
            return inv, resolution
    return Invocation(cwd, selectors, tuple(filters)), None


def invocation(name: str) -> Invocation:
    return resolved(name)[0]


def run_of(name: str, text: str | None = None, exit_code: int | None = None, timed_out: bool | None = None, picks: list[Pick] | None = None) -> Run:
    data = meta(name)
    code = data["exit_code"] if exit_code is None else exit_code
    body = output_text(name) if text is None else text
    inv, resolution = resolved(name)
    if picks is not None:
        inv.picks = picks
        resolution = None
    return Run(
        inv, parse_output(body), code, data["timed_out"] if timed_out is None else timed_out,
        text=body, resolution=resolution, pre=list(resolution.problems) if resolution else [],
        skipped=bool(resolution is not None and not resolution.names),
    )  # fmt: skip


def verdict(name: str, text: str | None = None, runs: list[Run] | None = None, **kw) -> dict:
    """The verdict `check` gives for the captured run."""
    repo = repo_of(name)
    return judge(repo, runs or [run_of(name, text, **kw)], [], "cargo test (captured)", 0.5, meta(name)["timeout"], Model(repo))
