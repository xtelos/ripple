"""Run a TypeScript fixture's own tests under the tracer and return the call edges they executed.

The TypeScript counterpart of bench/trace.py. The fixture's node_modules never live in the
repository: `install` puts them in a cache directory (RIPPLE_TS_CACHE, default ~/.cache/ripple-ts),
and every run copies the fixture into a directory of its own under the cache, with that node_modules
linked in, so node finds the packages above the sources and two runs never share a copy.

    python -m bench.ts_tracer install            npm ci --ignore-scripts into the cache
    python -m bench.ts_tracer trace --out f.json run the tests under the tracer and write the edges
    python -m bench.ts_tracer status             say whether node and the cache are usable

The edges come back in the form the ground truth is written in: three kinds, each a sorted list of
[from, to] symbol ids. See bench/ts_tracer/README.md.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from bench.ts_truth import FIXTURE, KINDS

HERE = Path(__file__).resolve().parent
RUNNERS = ("vitest", "jest")
_KIND_OF = {"call": "calls", "creator": "renders", "renderer": "renderer"}
_PROJECT_SYMBOL = re.compile(r"^(src|tests)(/|:|$)")  # edges to <root>, ?external and host:div are not repo symbols
_COPIED = ("src", "tests", "tsconfig.json", "vitest.config.mts", "jest.config.cjs", "package.json", "package-lock.json")


def cache_dir() -> Path:
    return Path(os.environ.get("RIPPLE_TS_CACHE") or Path.home() / ".cache" / "ripple-ts")


def work_dir() -> Path:
    return cache_dir() / "work"


def _lock_stamp() -> str:
    return hashlib.sha256((FIXTURE / "package.json").read_bytes() + (FIXTURE / "package-lock.json").read_bytes()).hexdigest()


def unavailable() -> str | None:
    """Why the tracer cannot run here, or None when it can."""
    if shutil.which("node") is None:
        return "needs node on PATH to run the TypeScript tracer"
    stamp = work_dir() / ".installed"
    if not stamp.is_file() or stamp.read_text().strip() != _lock_stamp():
        return f"needs the TypeScript fixture's packages: python -m bench.ts_tracer install (cache {cache_dir()}, set RIPPLE_TS_CACHE to move it)"
    return None


def install() -> None:
    """npm ci --ignore-scripts from the committed lockfile into <cache>/work (the shared, read-only node_modules)."""
    work = work_dir()
    work.mkdir(parents=True, exist_ok=True)
    for name in ("package.json", "package-lock.json"):
        shutil.copy2(FIXTURE / name, work / name)
    (work / ".installed").unlink(missing_ok=True)
    subprocess.run(["npm", "ci", "--ignore-scripts", "--no-audit", "--no-fund"], cwd=work, check=True)
    (work / ".installed").write_text(_lock_stamp() + "\n")


@contextlib.contextmanager
def workspace():
    """A private copy of the fixture in a directory of its own, with the shared node_modules linked in.

    Every run gets its own directory (and removes it afterwards, also on failure), so two runs at once,
    from two worktrees say, never delete or overwrite each other's copy. The installed packages are
    shared and only read.
    """
    runs = cache_dir() / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    run = Path(tempfile.mkdtemp(prefix="run-", dir=runs))
    try:
        for name in _COPIED:
            source = FIXTURE / name
            if source.is_dir():
                shutil.copytree(source, run / name)
            else:
                shutil.copy2(source, run / name)
        (run / "node_modules").symlink_to(work_dir() / "node_modules", target_is_directory=True)
        yield run
    finally:
        shutil.rmtree(run, ignore_errors=True)


def typecheck() -> subprocess.CompletedProcess:
    """tsc --noEmit over the fixture, so the fixture is known to be valid TypeScript."""
    with workspace() as work:
        return subprocess.run([str(work / "node_modules" / ".bin" / "tsc"), "--noEmit"], cwd=work, capture_output=True, text=True, timeout=300)


def _command(runner: str, work: Path) -> tuple[list[str], dict[str, str]]:
    if runner == "vitest":
        return (
            ["node", str(work / "node_modules/vitest/vitest.mjs"), "run", "--config", str(HERE / "vitest.trace.config.mjs"), "--root", str(work)],
            {"RIPPLE_VITEST_BASE": str(work / "vitest.config.mts")},
        )
    if runner == "jest":
        return (
            ["node", str(work / "node_modules/jest/bin/jest.js"), "--config", str(HERE / "jest.trace.config.cjs"), "--no-cache", "--ci"],
            {"RIPPLE_JEST_CONFIG": str(work / "jest.config.cjs")},
        )
    raise ValueError(f"runner must be one of {RUNNERS}, not {runner!r}")


def canonical(raw_files: list[dict]) -> dict:
    """Merge the per-worker recorder files into sorted [from, to] pairs per kind, repo symbols only."""
    pairs: dict[str, set[tuple[str, str]]] = {kind: set() for kind in KINDS}
    sites: dict[str, dict[str, set[str]]] = {kind: {} for kind in KINDS}
    diag: dict[str, int] = {}
    for data in raw_files:
        for key, count in data.get("diag", {}).items():
            diag[key] = diag.get(key, 0) + count
        for edge in data["edges"]:
            kind = _KIND_OF.get(edge["kind"])
            a, b = edge["from"], edge["to"]
            if kind is None or a == b or not (_PROJECT_SYMBOL.match(a) and _PROJECT_SYMBOL.match(b)):
                continue
            pairs[kind].add((a, b))
            sites[kind].setdefault(f"{a} -> {b}", set()).add(edge["site"])
    return {
        "edges": {kind: sorted([a, b] for a, b in pairs[kind]) for kind in KINDS},
        "sites": {kind: {k: sorted(v) for k, v in sorted(sites[kind].items())} for kind in KINDS},
        "diag": dict(sorted(diag.items())),
    }


def trace(runner: str = "vitest", timeout: int = 600) -> dict:
    """Run the fixture's tests under `runner` with the tracer and return its edges (see canonical)."""
    reason = unavailable()
    if reason:
        raise RuntimeError(reason)
    with workspace() as work, tempfile.TemporaryDirectory(prefix="ripple-ts-trace-") as out:
        command, extra = _command(runner, work)
        env = {**os.environ, "RIPPLE_TS_ROOT": str(work), "RIPPLE_TS_OUT_DIR": out, "CI": "1", **extra}
        result = subprocess.run(command, cwd=work, env=env, capture_output=True, text=True, timeout=timeout)
        if result.returncode != 0:
            raise RuntimeError(f"{runner} failed under the tracer:\n{result.stdout[-3000:]}\n{result.stderr[-3000:]}")
        raw = [json.loads(p.read_text()) for p in sorted(Path(out).glob("*.json"))]
    traced = canonical(raw)
    traced["runner"] = runner
    traced["workers"] = len(raw)
    return traced
