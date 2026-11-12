"""Write expected.json beside every Go probe: what the Go compiler's type checker says each call site calls, per platform.

Run by hand, like a fixture generator; it needs a Go toolchain (and golang.org/x/tools, which go.mod pins; the first
build fetches it unless the module cache has it). The test that reads the result, tests/test_go_probes.py, needs no Go.

    python bench/go_oracle/generate.py [probe-name ...]      (every probe when none is named)
    GO=/path/to/go python bench/go_oracle/generate.py

A probe is a directory of bench/fixtures/go/probes holding a legal Go module (or several, with a go.work or a replace).
For each of the platforms below (GOOS/GOARCH, CGO off) the oracle (main.go) type-checks every module of the probe, once as
`go build` sees it and once as `go test` does, so a call that goes to one method on one platform and another on the
next is recorded as two entries. A probe's optional `tags.txt` lists extra tag sets (one per line, comma separated):
each is one more run on linux/amd64, for files that `//go:build` names a tag no platform sets.

expected.json: "digest" (of the probe's files), "variants" (every variant run), "built" (file -> the variants that compile it, or "all"), "calls" (one entry per
distinct call site and answer: file, line, col, end (line:col of the closing parenthesis, which no two calls share), kind E/X/I/D, callee id, `at` the file that declares the callee when it
is inside the probe, and the variants that give that answer, or "all").
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROBES = HERE.parent / "fixtures" / "go" / "probes"
PLATFORMS = (("linux", "amd64"), ("linux", "arm64"), ("windows", "amd64"), ("darwin", "arm64"))


def go_binary() -> str:
    found = os.environ.get("GO") or shutil.which("go")
    if not found:
        sys.exit("generate.py needs a Go toolchain: put go on PATH or set GO")
    return found


def module_directories(probe: Path) -> list[Path]:
    """Every directory of the probe that holds a go.mod, except below vendor, testdata, `_x` and `.x` (the go tool's own skips)."""
    found = []
    for directory, subdirectories, files in os.walk(probe):
        subdirectories[:] = sorted(d for d in subdirectories if d not in ("vendor", "testdata") and not d.startswith(("_", ".")))
        if "go.mod" in files:
            found.append(Path(directory))
    return found


def run_oracle(oracle: Path, directory: Path, goos: str, goarch: str, tags: str) -> list[list[str]]:
    env = dict(os.environ, GOOS=goos, GOARCH=goarch, CGO_ENABLED="0", GOFLAGS=(os.environ.get("GOFLAGS", "") + " -buildvcs=false").strip())
    command = [str(oracle), "-dir", str(directory)] + (["-tags", tags] if tags else []) + ["./..."]
    done = subprocess.run(command, capture_output=True, text=True, env=env)
    if done.returncode:
        sys.exit(f"the oracle failed in {directory} for {goos}/{goarch} {tags}:\n{done.stderr}")
    return [line.split("\t") for line in done.stdout.splitlines()]


def digest(probe: Path) -> str:
    """A hash of every file of the probe but expected.json: the test refuses an expected.json whose probe has changed since."""
    found = hashlib.sha256()
    for path in sorted(p for p in probe.rglob("*") if p.is_file() and p.name != "expected.json"):
        found.update(path.relative_to(probe).as_posix().encode() + b"\0" + path.read_bytes() + b"\0")
    return found.hexdigest()


def probe_tag_sets(probe: Path) -> list[str]:
    path = probe / "tags.txt"
    return [line.strip() for line in path.read_text().splitlines() if line.strip()] if path.exists() else []


def generate(oracle: Path, probe: Path) -> dict:
    runs = [(goos, goarch, "") for goos, goarch in PLATFORMS] + [("linux", "amd64", tags) for tags in probe_tag_sets(probe)]
    jobs = [(directory, goos, goarch, tags) for directory in module_directories(probe) for goos, goarch, tags in runs]
    with ThreadPoolExecutor(max_workers=os.cpu_count() or 4) as pool:
        results = list(pool.map(lambda job: (job, run_oracle(oracle, job[0], *job[1:])), jobs))
    root = str(probe.resolve()) + os.sep
    relative = lambda path: path[len(root):] if path.startswith(root) else ""  # noqa: E731
    variants: list[str] = []
    built: dict[str, set[str]] = {}
    calls: dict[tuple, set[str]] = {}
    for (_, goos, goarch, tags), rows in results:
        platform = f"{goos}-{goarch}" + (f"+{tags}" if tags else "")
        for row in rows:
            variant = f"{platform}/{row[1]}"
            if variant not in variants:
                variants.append(variant)
            if row[0] == "F":
                if relative(row[2]):  # the test main go builds is not the probe's
                    built.setdefault(relative(row[2]), set()).add(variant)
                continue
            _, _, kind, _, callee, declared, where, end = row
            file, line, col = where.rsplit(":", 2)
            if not relative(file):
                continue
            at = relative(declared) if declared not in ("", "-") else ""
            calls.setdefault((relative(file), int(line), int(col), end, kind, callee, at), set()).add(variant)
    order = {v: i for i, v in enumerate(sorted(variants))}
    return {
        "digest": digest(probe),
        "variants": sorted(variants),
        "built": {file: sorted(vs, key=order.get) for file, vs in sorted(built.items())},
        "calls": [
            {"file": f, "line": ln, "col": col, "end": end, "kind": kind, "callee": callee, "at": at, "variants": sorted(vs, key=order.get)}
            for (f, ln, col, end, kind, callee, at), vs in sorted(calls.items())
        ],
    }


def dump(expected: dict) -> str:
    """One line per file and per call, so a diff of two generations reads as the sites that changed. "all" stands for every variant run."""
    every = expected["variants"]
    expected = {
        **expected,
        "built": {f: "all" if vs == every else vs for f, vs in expected["built"].items()},
        "calls": [{**c, "variants": "all" if c["variants"] == every else c["variants"]} for c in expected["calls"]],
    }
    lines = ["{", f' "digest": "{expected["digest"]}",', f' "variants": {json.dumps(expected["variants"])},', ' "built": {']
    lines += [f'  {json.dumps(f)}: {json.dumps(vs)}' + ("," if i < len(expected["built"]) - 1 else "") for i, (f, vs) in enumerate(expected["built"].items())]
    lines += [" },", ' "calls": [']
    lines += ["  " + json.dumps(c) + ("," if i < len(expected["calls"]) - 1 else "") for i, c in enumerate(expected["calls"])]
    return "\n".join(lines + [" ]", "}"]) + "\n"


def main() -> None:
    # a probe with a cgo.json imports C, which the oracle (CGO off) cannot load: it states its own expected answer instead
    names = sys.argv[1:] or sorted(p.name for p in PROBES.iterdir() if p.is_dir() and not (p / "cgo.json").exists())
    with tempfile.TemporaryDirectory() as scratch:
        oracle = Path(scratch) / "oracle"
        build = subprocess.run([go_binary(), "build", "-o", str(oracle), "."], cwd=HERE, capture_output=True, text=True)
        if build.returncode:
            sys.exit(f"could not build the oracle:\n{build.stderr}")
        for name in names:
            probe = PROBES / name
            (probe / "expected.json").write_text(dump(generate(oracle, probe)))
            print("wrote", probe / "expected.json")


if __name__ == "__main__":
    main()
