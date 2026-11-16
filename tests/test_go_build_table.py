"""Which Go files a build has: ripple's reading equals go/build's, over a committed table `go list` wrote.

bench/go_oracle/go_build_table.py writes bench/go_oracle/go_build_table.json: every header form (a `//go:build` line, `// +build`
lines, a byte order mark, no-break spaces, a vertical tab, a header after the package clause, ...) against every file-name form
(a platform suffix, a second dot, `_test`, ...), the verdict of `go list -e` for twelve GOOS/GOARCH/tag runs. The test needs no Go
toolchain and no parser: it holds ripple.go_indexer's port of shouldBuild, parseFileHeader and goodOSArchFile to that table, and
the platform-free predicates the indexer uses (a file is constrained, never built, tests only) to the same verdicts.
"""

import importlib.util
import json
from pathlib import Path

import pytest

from ripple.go_indexer import KNOWN_ARCH, KNOWN_OS, UNIX_OS, file_in_build, is_constrained, is_tests_only, never_built, platform_file, reads_as_source
from ripple.profile import GO

ORACLE = Path(__file__).resolve().parent.parent / "bench" / "go_oracle"
SPEC = importlib.util.spec_from_file_location("go_build_table", ORACLE / "go_build_table.py")
GENERATOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GENERATOR)
TABLE = json.loads((ORACLE / "go_build_table.json").read_text())
PLATFORMS = [(goos, goarch, frozenset(tags.split(",")) if tags else frozenset()) for goos, goarch, tags in TABLE["platforms"]]
NAMES = TABLE["names"]
CASES = [(h, n) for h in range(len(TABLE["headers"])) for n in range(len(NAMES))]
BUILT = {"B": True, "T": True, "I": False, "-": False, "E": None}


def head(index: int) -> bytes:
    return bytes.fromhex(TABLE["headers"][index]["before"]) + b"package p\n" + bytes.fromhex(TABLE["headers"][index]["after"])


def test_the_table_is_the_one_the_generator_describes():
    assert [(h["label"], bytes.fromhex(h["before"]), bytes.fromhex(h["after"])) for h in TABLE["headers"]] == GENERATOR.HEADERS
    assert TABLE["names"] == GENERATOR.NAMES and [tuple(p) for p in TABLE["platforms"]] == GENERATOR.PLATFORMS
    assert len(TABLE["verdicts"]) == len(GENERATOR.HEADERS) and all(len(row) == len(NAMES) for row in TABLE["verdicts"])
    assert all(len(cell) == len(PLATFORMS) for row in TABLE["verdicts"] for cell in row)
    assert len(CASES) * len(PLATFORMS) >= 20000


def test_the_known_operating_systems_and_architectures_are_the_ones_go_lists():
    assert sorted(KNOWN_OS) == TABLE["known_os"] and sorted(KNOWN_ARCH) == TABLE["known_arch"] and sorted(UNIX_OS) == TABLE["unix_os"]


@pytest.mark.parametrize("platform", range(len(PLATFORMS)))
def test_ripple_puts_a_file_in_a_build_exactly_when_go_does(platform):
    goos, goarch, tags = PLATFORMS[platform]
    wrong = []
    for h, n in CASES:
        name, label = NAMES[n], TABLE["headers"][h]["label"]
        want = TABLE["verdicts"][h][n][platform]
        got = file_in_build(name, head(h), goos, goarch, tags) if not name.startswith(("_", ".")) else False
        # B or T: in the build; I and -: out; E: go refuses the header, which ripple reports as None
        if got != BUILT[want]:
            wrong.append(f"{label!r} in {name}: go says {want}, ripple {got}")
    assert wrong == []


def test_a_test_file_is_a_test_file_to_go_and_to_ripple():
    for h, n in CASES:
        if NAMES[n].startswith(("_", ".")):
            continue
        cell = TABLE["verdicts"][h][n]
        assert ("T" in cell) <= NAMES[n].endswith("_test.go") and ("B" in cell) <= (not NAMES[n].endswith("_test.go"))


def test_what_the_indexer_reads_as_unconstrained_is_built_on_every_platform_and_a_never_built_file_on_none():
    wrong = []
    for h, n in CASES:
        name, label = NAMES[n], TABLE["headers"][h]["label"]
        if name.startswith(("_", ".")):
            continue
        cell = TABLE["verdicts"][h][n]
        data = head(h)
        constrained, never = is_constrained(name, data), never_built(data)
        if not constrained and any(c in "IE" for c in cell):
            wrong.append(f"{label!r} in {name}: read as in every build, go says {cell}")
        if never and any(c in "BT" for c in cell):
            wrong.append(f"{label!r} in {name}: read as in no build, go says {cell}")
        if is_tests_only(name, data) and (not name.endswith("_test.go") or any(c in "IE" for c in cell)):
            wrong.append(f"{label!r} in {name}: read as tests only in every build, go says {cell}")
        if not platform_file(name) and not never and any(c == "I" for c in cell) and not build_header_says(data):
            wrong.append(f"{label!r} in {name}: a name that says no platform and a header that says none is ignored: {cell}")
    assert wrong == []


def build_header_says(data: bytes) -> bool:
    from ripple.go_indexer import build_constraint

    return bool(build_constraint(data))


def test_a_file_go_refuses_is_never_read_as_in_every_build_or_dropped_as_never_built():
    for h, n in CASES:
        name = NAMES[n]
        if name.startswith(("_", ".")) or "E" not in TABLE["verdicts"][h][n]:
            continue
        assert is_constrained(name, head(h)), (TABLE["headers"][h]["label"], name)


def test_the_walk_never_drops_a_file_some_build_includes_or_go_refuses(tmp_path):
    dropped = 0
    for h in range(len(TABLE["headers"])):
        path = tmp_path / f"h{h:03d}.go"
        path.write_bytes(head(h))
        cell = TABLE["verdicts"][h][NAMES.index("a.go")]
        if not reads_as_source(path, GO, tmp_path):
            dropped += 1
            assert set(cell) <= {"I"}, (TABLE["headers"][h]["label"], cell)
    assert dropped >= 4, "the table has the never-built forms (ignore, an empty +build line, ...)"


def test_the_table_has_every_kind_of_verdict_it_is_a_proof_by():
    kinds = {c for row in TABLE["verdicts"] for cell in row for c in cell}
    assert kinds == set("BTIE-")
    assert sum(cell.count("E") for row in TABLE["verdicts"] for cell in row) >= 1000
