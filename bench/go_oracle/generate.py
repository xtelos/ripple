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
is inside the probe, and the variants that give that answer, or "all"), and "implementers" (one entry per interface call site,
method and answer: the interface it is called through, the method, "candidates", the methods of the probe that a type
implementing the interface has under that name, each `id` with the file `at` that declares it, as the type checker's method
sets say (what the call may dispatch to inside the probe), "packages", the paths of the probe's packages that some build of the
call has, and the variants that give that answer).

A call is compiled in one or more universes, each ONE BUILD UNIT (see implementers.go, never a union of programs): a test binary
links the package under test with its test files, its external test package and what they import (a package that imports the
package under test is recompiled against it, so its types are that binary's own); a program links the closure of its package
main, so a call in a package main has only the types of what that program imports; a call in a library package has the packages
some program or test binary of the repo links with it, and the packages that a program outside the repo could import with it
(neither a main package nor below an `internal` directory) from the modules the go tool says the run builds against. A type of
one universe is never identical to a type of another. A candidate is agreed (listed) when SOME universe of the call has it and
NO universe that has its type's package lacks it: a type that is there and does not implement the interface is a counterexample,
one whose package is not in a universe is no evidence. Module runs are joined (a type of a module the call's module does not
build against exists only in the run of a module that does); see agreed_candidates and implementers.go.
tests/test_go_implements.py holds ripple's `implements` edges to this: every edge is a listed candidate in some variant of the
call, and in none a build that has the package lists it as absent.
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


PUBLISHED = HERE / "published"
PROXY: list[Path] = []  # the file proxy of the published modules, built once per run (see build_proxy)


def build_proxy(root: Path) -> Path:
    """A file-based module proxy of the modules under bench/go_oracle/published/<name>@<version>/ (the module path is the
    one of the go.mod inside; a published version a
    probe's module requires without replacing it, so that the oracle can build the program the way go does when it takes that
    module from the network). A probe lists it in its published.txt; the go tool then runs with GOPROXY pointing here."""
    import zipfile

    proxy = root / "proxy"
    for source in sorted(PUBLISHED.iterdir()) if PUBLISHED.is_dir() else []:
        version = source.name.rsplit("@", 1)[1]
        module = next(line.split()[1] for line in (source / "go.mod").read_text().splitlines() if line.startswith("module "))
        versions = proxy / module / "@v"
        versions.mkdir(parents=True, exist_ok=True)
        with (versions / "list").open("a") as listing:
            listing.write(version + "\n")
        (versions / f"{version}.info").write_text(json.dumps({"Version": version, "Time": "2024-01-01T00:00:00Z"}))
        (versions / f"{version}.mod").write_text((source / "go.mod").read_text())
        with zipfile.ZipFile(versions / f"{version}.zip", "w") as archive:
            for path in sorted(p for p in source.rglob("*") if p.is_file()):
                archive.write(path, f"{module}@{version}/{path.relative_to(source).as_posix()}")
    return proxy


def probe_env(probe: Path) -> dict[str, str]:
    """The environment a probe that lists published modules needs: the file proxy, no checksum database, and `-mod=mod` so the
    go tool may write the go.sum lines of the published modules (they are committed beside the probe)."""
    if not (probe / "published.txt").exists() or not PROXY:
        return {}
    return {"GOPROXY": PROXY[0].as_uri(), "GOSUMDB": "off", "GONOSUMDB": "*"}


def run_oracle(oracle: Path, directory: Path, goos: str, goarch: str, tags: str, repo: Path) -> list[list[str]]:
    extra = probe_env(repo)
    flags = (os.environ.get("GOFLAGS", "") + " -buildvcs=false" + (" -mod=mod" if extra else "")).strip()
    env = {**os.environ, "GOOS": goos, "GOARCH": goarch, "CGO_ENABLED": "0", **extra, "GOFLAGS": flags}
    command = [str(oracle), "-dir", str(directory), "-implementers", "-repo", str(repo)] + (["-tags", tags] if tags else []) + ["./..."]
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


def agreed_candidates(held: dict[str, dict[str, set[str]]], present) -> set[str]:
    """The `id@file` candidates of one interface call that no build contradicts. held is {module run: {universe: the
    methods that implement the interface there}}; present(module, universe) the paths of the packages that universe has.
    A method is a candidate when some universe has it, and every universe that has its type's package has it too: a type
    that is there and does not implement the interface is a counterexample, one that is not there is no evidence (a
    package that imports the package under test is not in that test's binary). Module runs are joined: a type of a
    module the call's module does not import exists only in the run of a module that has both."""
    found: set[str] = set()
    for module, run in held.items():
        every = set().union(*run.values())
        for candidate in every:
            path = candidate.split(":", 1)[0]
            if all(candidate in have or path not in present(module, universe) for universe, have in run.items()):
                found.add(candidate)
    return found


def generate(oracle: Path, probe: Path) -> dict:
    runs = [(goos, goarch, "") for goos, goarch in PLATFORMS] + [("linux", "amd64", tags) for tags in probe_tag_sets(probe)]
    jobs = [(directory, goos, goarch, tags) for directory in module_directories(probe) for goos, goarch, tags in runs]
    with ThreadPoolExecutor(max_workers=os.cpu_count() or 4) as pool:
        results = list(pool.map(lambda job: (job, run_oracle(oracle, job[0], *job[1:], probe)), jobs))
    root = str(probe.resolve()) + os.sep
    relative = lambda path: path[len(root):] if path.startswith(root) else ""  # noqa: E731
    variants: list[str] = []
    built: dict[str, set[str]] = {}
    calls: dict[tuple, set[str]] = {}
    # (variant, file, line, col, end, interface, method) -> {module run: {universe: candidates}}
    universes: dict[tuple, dict[str, dict[str, set[str]]]] = {}
    present: dict[tuple[str, str, str], set[str]] = {}  # (module run, variant, universe) -> the package paths that universe has
    for (module, goos, goarch, tags), rows in results:
        platform = f"{goos}-{goarch}" + (f"+{tags}" if tags else "")
        for row in rows:
            variant = f"{platform}/{row[1]}"
            if variant not in variants:
                variants.append(variant)
            if row[0] == "F":
                if relative(row[2]):  # the test main go builds is not the probe's
                    built.setdefault(relative(row[2]), set()).add(variant)
                continue
            if row[0] == "P":
                present[(str(module), variant, row[2])] = set(row[3].split(","))
                continue
            if row[0] == "M":
                _, _, universe, interface, method, found, where, end = row
                file, line, col = where.rsplit(":", 2)
                if relative(file):
                    candidates = {c.split("@", 1)[0] + "@" + relative(c.split("@", 1)[1]) for c in found.split(",") if c}
                    held = universes.setdefault((variant, relative(file), int(line), int(col), end, interface, method), {})
                    held.setdefault(str(module), {})[universe] = candidates
                continue
            _, _, kind, _, callee, declared, where, end = row
            file, line, col = where.rsplit(":", 2)
            if not relative(file):
                continue
            at = relative(declared) if declared not in ("", "-") else ""
            calls.setdefault((relative(file), int(line), int(col), end, kind, callee, at), set()).add(variant)
    order = {v: i for i, v in enumerate(sorted(variants))}
    implementers: dict[tuple, set[str]] = {}
    for (variant, file, line, col, end, interface, method), held in universes.items():
        agreed = agreed_candidates(held, lambda module, universe: present.get((module, variant, universe), set()))
        there = set().union(*(present.get((module, variant, universe), set()) for module, run in held.items() for universe in run))
        implementers.setdefault((file, line, col, end, interface, method, tuple(sorted(agreed)), tuple(sorted(there))), set()).add(variant)
    return {
        "digest": digest(probe),
        "variants": sorted(variants),
        "built": {file: sorted(vs, key=order.get) for file, vs in sorted(built.items())},
        "calls": [
            {"file": f, "line": ln, "col": col, "end": end, "kind": kind, "callee": callee, "at": at, "variants": sorted(vs, key=order.get)}
            for (f, ln, col, end, kind, callee, at), vs in sorted(calls.items())
        ],
        "implementers": [
            {
                "file": f, "line": ln, "col": col, "end": end, "interface": interface, "method": method,
                "candidates": [{"id": c.split("@", 1)[0], "at": c.split("@", 1)[1]} for c in cands], "packages": list(there),
                "variants": sorted(vs, key=order.get),
            }
            for (f, ln, col, end, interface, method, cands, there), vs in sorted(implementers.items())
        ],
    }


def dump(expected: dict) -> str:
    """One line per file and per call, so a diff of two generations reads as the sites that changed. "all" stands for every variant run."""
    every = expected["variants"]
    expected = {
        **expected,
        "built": {f: "all" if vs == every else vs for f, vs in expected["built"].items()},
        "calls": [{**c, "variants": "all" if c["variants"] == every else c["variants"]} for c in expected["calls"]],
        "implementers": [{**c, "variants": "all" if c["variants"] == every else c["variants"]} for c in expected["implementers"]],
    }
    lines = ["{", f' "digest": "{expected["digest"]}",', f' "variants": {json.dumps(expected["variants"])},', ' "built": {']
    lines += [f'  {json.dumps(f)}: {json.dumps(vs)}' + ("," if i < len(expected["built"]) - 1 else "") for i, (f, vs) in enumerate(expected["built"].items())]
    lines += [" },", ' "calls": [']
    lines += ["  " + json.dumps(c) + ("," if i < len(expected["calls"]) - 1 else "") for i, c in enumerate(expected["calls"])]
    lines += [" ],", ' "implementers": [']
    lines += ["  " + json.dumps(c) + ("," if i < len(expected["implementers"]) - 1 else "") for i, c in enumerate(expected["implementers"])]
    return "\n".join(lines + [" ]", "}"]) + "\n"


def main() -> None:
    # a probe with a cgo.json imports C, which the oracle (CGO off) cannot load: it states its own expected answer instead
    names = sys.argv[1:] or sorted(p.name for p in PROBES.iterdir() if p.is_dir() and not (p / "cgo.json").exists())
    with tempfile.TemporaryDirectory() as scratch:
        PROXY[:] = [build_proxy(Path(scratch))]
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
