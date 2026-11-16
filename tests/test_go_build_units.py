"""The builds a Go call is in: what links a program and a test binary, the oracle's answer per build unit, and the module
rule an `implements` edge across modules follows.

An `implements` edge is proven only for a type that some build has together with the call. A program links its import
closure and nothing else; a test binary links its package with the `_test.go` files, the external test package, and what
those import, never another package's test files. These tests hold the committed oracle answers (expected.json, written by
bench/go_oracle against the real go tool) to that rule with an import reader that shares no code with ripple.
"""

import json
import re
from pathlib import Path

from ripple.go_indexer import internal_path
from ripple.go_modules import Modules

PROBES = Path(__file__).resolve().parent.parent / "bench" / "fixtures" / "go" / "probes"
QUOTED = re.compile(r'"([^"\n]+)"')


def single_module(probe: Path) -> str | None:
    mods = list(probe.rglob("go.mod"))
    if len(mods) != 1 or list(probe.rglob("go.work")):
        return None
    return re.search(r"^module\s+(\S+)", mods[0].read_text(), re.M).group(1)


def imports_of(directory: Path, module: str, tests: bool) -> set[str]:
    """The module-relative directories ("" is the root) that the files of a directory import, read from the source text."""
    found = set()
    for path in directory.glob("*.go"):
        if path.name.startswith(("_", ".")) or path.name.endswith("_test.go") != tests:
            continue
        for quoted in QUOTED.findall(path.read_text()):
            if quoted == module or quoted.startswith(module + "/"):
                found.add(quoted[len(module) :].lstrip("/"))
    return found


def program_directories(probe: Path, module: str, start: str, tests: bool) -> set[str]:
    """The directories a program built from `start` links, and with `tests` its test binary: the closure of the imports."""
    seen, stack = {start}, [(start, tests)]
    while stack:
        here, with_tests = stack.pop()
        directory = probe / here if here else probe
        for found in imports_of(directory, module, False) | (imports_of(directory, module, True) if with_tests else set()):
            if found not in seen:
                seen.add(found)
                stack.append((found, False))
    return seen


def candidate_directory(ident: str, module: str) -> str:
    return ident.split(":", 1)[0].removeprefix(module).lstrip("/")


def main_sites():
    """(probe, file, candidates) for every interface call in a non-test file of a package main of a single-module probe."""
    for expected_file in sorted(PROBES.glob("*/expected.json")):
        probe = expected_file.parent
        module = single_module(probe)
        if module is None:
            continue
        for site in json.loads(expected_file.read_text())["implementers"]:
            path = probe / site["file"]
            if path.name.endswith("_test.go") or not re.search(r"^package main\b", path.read_text(), re.M):
                continue
            yield probe, module, site


def test_the_oracle_gives_a_call_in_a_package_main_only_the_types_its_program_or_its_test_binary_links():
    sites = list(main_sites())
    assert len(sites) >= 6
    for probe, module, site in sites:
        directory = str(Path(site["file"]).parent).removeprefix(".")
        linked = program_directories(probe, module, directory, True)
        for candidate in site["candidates"]:
            where = candidate_directory(candidate["id"], module)
            assert where in linked, f"{probe.name} {site['file']}:{site['line']}: the oracle lists {candidate['id']}, of a package this program does not link"


def test_the_oracle_does_not_merge_the_programs_of_a_repo_into_one_build():
    expected = json.loads((PROBES / "impl_main_program" / "expected.json").read_text())
    by_file = {}
    for site in expected["implementers"]:
        by_file.setdefault(site["file"], []).append({c["id"].removeprefix("example.com/impl_main_program").lstrip(":/") for c in site["candidates"]})
    assert by_file["cmd/m/main.go"] == [{"Real.Run", "cmd/m:M.Run"}]
    assert by_file["cmd/n/main.go"] == [{"Real.Run", "b:Dep.Run", "internal/e:E.Run", "cmd/n:N.Run"}]
    # the test binary of cmd/k links d, the program does not: both are builds of the call, each with its own answer
    assert sorted(map(sorted, by_file["cmd/k/main.go"])) == sorted([["Real.Run", "cmd/k:K.Run"], ["Real.Run", "cmd/k:K.Run", "d:D.Run"]])


# ---- the modules a build runs against ---------------------------------------------------------------------------------


def write_modules(root: Path, files: dict[str, str]) -> Modules:
    for rel, content in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(content)
    return Modules(root, {"" if Path(rel).parent.as_posix() == "." else Path(rel).parent.as_posix() for rel in files})


def test_a_module_builds_against_itself_a_replaced_one_and_a_workspace_member_and_no_other(tmp_path):
    modules = write_modules(
        tmp_path,
        {
            "a/go.mod": "module example.com/a\n\nrequire example.com/r v0.0.0\n\nreplace example.com/r => ../r\n",
            "r/go.mod": "module example.com/r\n",
            "u/go.mod": "module example.com/u\n",
        },
    )
    a, r, u = (modules.mods[d] for d in ("a", "r", "u"))
    assert modules.builds_against(a, a) and modules.builds_against(a, r)
    assert not modules.builds_against(r, a), "a replace is the importer's: r has no replace of a"
    assert not modules.builds_against(a, u) and not modules.builds_against(u, a) and not modules.builds_against(r, u)


def test_the_members_of_a_workspace_build_against_each_other(tmp_path):
    modules = write_modules(
        tmp_path,
        {
            "go.work": "go 1.22\n\nuse (\n\t./a\n\t./b\n)\n",
            "a/go.mod": "module example.com/a\n",
            "b/go.mod": "module example.com/b\n",
            "c/go.mod": "module example.com/c\n",
        },
    )
    a, b, c = (modules.mods[d] for d in ("a", "b", "c"))
    assert modules.builds_against(a, b) and modules.builds_against(b, a)
    assert not modules.builds_against(a, c) and not modules.builds_against(c, a)


def test_an_internal_path_is_one_with_an_internal_element():
    assert internal_path("example.com/m/internal/x") and internal_path("example.com/m/x/internal")
    assert not internal_path("example.com/m/internals") and not internal_path("example.com/internalx/y") and not internal_path("example.com/m")
