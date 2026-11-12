"""The Go differential probes: ripple's answer at every call site against the Go compiler's.

bench/fixtures/go/probes holds small legal Go modules, each aimed at one way a call can look like one thing and be
another (an alias receiver, a method only some build has, a name a local shadows, a file tree-sitter cannot read, a
function-typed field of a type ripple does not read). Beside each, expected.json is what go/types says every call site
calls, recorded per platform (linux/amd64, linux/arm64, windows/amd64, darwin/arm64, and any tag set the probe's tags.txt
names), each once as `go build` sees the package and once as `go test` does. bench/go_oracle/generate.py writes it and
needs Go; this test only reads it.

A site is a call, told apart from every other call by the position of its closing parenthesis (the oracle writes it as
`end`, ripple's Resolver.call_sites keeps it), so two calls on one line (`a.F().G()`) are each held to their own answer.
Two rules per site, with no credit for recall (an unresolved call is never wrong):
  - an edge ripple makes is the compiler's callee on EVERY variant that builds the file the site is in (a static call of
    that function or method, not a function value or an interface method), so an edge that is right on only some
    platforms fails; ripple must have left the call unresolved. The edge is compared as the id the compiler names, so
    the `#2` of a name declared in two build variants is dropped, and the symbol's own file is compared too: an edge to
    the linux declaration is wrong on a platform that builds only the other one;
  - a call ripple counts external (it cannot reach repo code) has no callee inside the probe on any variant, and is
    never a call of a function value (the compiler's D) on any variant either, whatever the package or type.

A probe without a Go oracle (cgo needs CGO_ENABLED=1 and a C compiler, which the oracle does not run) has cgo.json
instead of expected.json: the sites it lists must be unresolved because of cgo, never external and never an edge.

These need tree-sitter-go (pip install 'ripple-code[go]') and are skipped without it.
"""

import hashlib
import importlib.util
import json
import re
from pathlib import Path

import pytest

from ripple.go_indexer import CGO, analyse
from ripple.indexer import walk_repo
from ripple.profile import GO

HAS_GO = importlib.util.find_spec("tree_sitter_go") is not None and importlib.util.find_spec("tree_sitter") is not None
pytestmark = pytest.mark.skipif(not HAS_GO, reason="Go support is not installed (pip install 'ripple-code[go]')")

PROBES = Path(__file__).resolve().parent.parent / "bench" / "fixtures" / "go" / "probes"
NAMES = sorted(p.name for p in PROBES.iterdir() if p.is_dir())
ORACLE_NAMES = [n for n in NAMES if (PROBES / n / "expected.json").exists()]
CGO_NAMES = [n for n in NAMES if (PROBES / n / "cgo.json").exists()]
MINIMUM_PROBES = 120
RESULTS: dict[str, int] = {}  # probe -> how many edges ripple made, for the sanity check below


def digest(probe: Path) -> str:
    """What generate.py hashed: every file of the probe but expected.json, so an edited probe is noticed."""
    found = hashlib.sha256()
    for path in sorted(p for p in probe.rglob("*") if p.is_file() and p.name != "expected.json"):
        found.update(path.relative_to(probe).as_posix().encode() + b"\0" + path.read_bytes() + b"\0")
    return found.hexdigest()


def ripple_in(probe: Path):
    files, skipped = walk_repo(probe, GO)
    program, resolver = analyse(probe, files, GO)
    return program, resolver, skipped


def stripped(callee: str) -> str:
    return re.sub(r"#\d+$", "", callee)


def symbol_files(resolver, ids) -> set[str]:
    """The files the symbols an edge goes to are declared in: an edge to `T.M` of x_linux.go is not the compiler's callee
    on a platform that builds x_windows.go's `T.M` instead, though both are the id `T.M`."""
    files = {s.id: s.file for p in resolver.program.packages.values() for s in p.symbols}
    return {files[i] for i in ids if i in files}


def disagreements(name: str, root: Path = PROBES) -> list[str]:
    probe = root / name
    expected = json.loads((probe / "expected.json").read_text())
    every = expected["variants"]
    built = {file: every if variants == "all" else variants for file, variants in expected["built"].items()}
    answers: dict[tuple[str, str, str], set[tuple[str, str, str]]] = {}  # (file, end, variant) -> {(kind, callee, declared in)}
    inside: dict[tuple[str, str], set[str]] = {}  # (file, end) -> callees declared inside the probe, on any variant
    dynamic: set[tuple[str, str]] = set()  # the sites that call a function value on some variant
    for call in expected["calls"]:
        key = (call["file"], call["end"])
        for variant in every if call["variants"] == "all" else call["variants"]:
            answers.setdefault((call["file"], call["end"], variant), set()).add((call["kind"], call["callee"], call["at"]))
        if call["at"] and not call["at"].startswith("vendor/"):
            inside.setdefault(key, set()).add(call["callee"])
        if call["kind"] == "D":
            dynamic.add(key)
    _, resolver, _ = ripple_in(probe)
    RESULTS[name] = len(resolver.edges)
    wrong = []
    for kind, file, end_line, end_col, detail in resolver.call_sites:
        end = f"{end_line}:{end_col}"
        where = f"{file}:{end_line}:{end_col}"
        if kind == "edge":
            callee = {stripped(d) for d in detail}
            variants = built.get(file, [])
            if not variants:
                wrong.append(f"{where} edge to {sorted(detail)}: no variant builds this file")
            for variant in variants:
                got = answers.get((file, end, variant), set())
                if {c for k, c, _ in got if k in ("E", "X")} != callee or any(k in ("D", "I") for k, _, _ in got):
                    wrong.append(f"{where} edge to {sorted(detail)}: on {variant} the compiler calls {sorted(got)}")
                    continue
                declared_in = {at for k, c, at in got if at}
                if declared_in - symbol_files(resolver, detail):
                    wrong.append(
                        f"{where} edge to {sorted(detail)} declared in {sorted(symbol_files(resolver, detail))}: "
                        f"on {variant} the compiler's callee is declared in {sorted(declared_in)}"
                    )
        elif kind == "external":
            if inside.get((file, end)):
                wrong.append(f"{where} counted external ({detail}) but the compiler calls {sorted(inside[(file, end)])}")
            if (file, end) in dynamic:
                wrong.append(f"{where} counted external ({detail}) but the compiler sees a call of a function value")
    return wrong


def cgo_disagreements(name: str) -> list[str]:
    """A cgo probe has no oracle: each site it lists must be unresolved for cgo, and no call of its file is external for cgo."""
    probe = PROBES / name
    listed = json.loads((probe / "cgo.json").read_text())
    _, resolver, _ = ripple_in(probe)
    RESULTS[name] = len(resolver.edges)
    wrong = []
    by_line = {}
    for kind, file, end_line, _, detail in resolver.call_sites:
        by_line.setdefault((file, end_line), []).append((kind, detail))
    for file, line in listed["unresolved"]:
        got = by_line.get((file, line), [])
        if not [1 for kind, detail in got if kind == "unresolved" and detail == CGO]:
            wrong.append(f"{file}:{line} is a C call and must be unresolved for cgo, ripple says {got}")
        wrong += [f"{file}:{line} a C call is {kind} ({detail})" for kind, detail in got if kind in ("edge", "external")]
    return wrong


def test_there_are_the_probes_and_each_has_what_the_compiler_said():
    assert len(NAMES) >= MINIMUM_PROBES
    assert sorted(ORACLE_NAMES + CGO_NAMES) == NAMES, "every probe has an expected.json (the oracle) or a cgo.json (the reason it has none)"
    for name in CGO_NAMES:
        assert json.loads((PROBES / name / "cgo.json").read_text())["why"], name


@pytest.mark.parametrize("name", ORACLE_NAMES)
def test_ripple_agrees_with_the_compiler_at_every_site_it_answers(name):
    expected = json.loads((PROBES / name / "expected.json").read_text())
    assert expected["digest"] == digest(PROBES / name), f"{name} changed since the oracle ran: rerun bench/go_oracle/generate.py {name}"
    assert all("end" in call for call in expected["calls"]), f"{name}: expected.json predates the end positions: rerun generate.py {name}"
    assert disagreements(name) == []


@pytest.mark.parametrize("name", CGO_NAMES)
def test_a_cgo_call_is_unresolved_and_never_external_or_an_edge(name):
    assert cgo_disagreements(name) == []


def test_ripple_still_proves_edges_across_the_probes():
    for name in NAMES:
        if name not in RESULTS:
            (disagreements if name in ORACLE_NAMES else cgo_disagreements)(name)
    assert sum(RESULTS.values()) >= 400  # recall is not asserted per probe, but a resolver that proved nothing would pass the rest


def test_two_calls_on_one_line_are_each_held_to_their_own_answer():
    """A line holding two calls of different callees: ripple answers each at its own position, so a wrong one cannot hide
    behind the other, and the compiler's answer is read at the same position."""
    name = "same_line_calls"
    expected = json.loads((PROBES / name / "expected.json").read_text())
    per_line: dict[tuple[str, int], set[tuple[str, str]]] = {}
    for call in expected["calls"]:
        per_line.setdefault((call["file"], call["line"]), set()).add((call["end"], call["callee"]))
    assert any(len({end for end, _ in calls}) >= 2 and len({callee for _, callee in calls}) >= 2 for calls in per_line.values())
    _, resolver, _ = ripple_in(PROBES / name)
    edge_sites = [(file, line, end_col, detail) for kind, file, line, end_col, detail in resolver.call_sites if kind == "edge"]
    on_a_line: dict[tuple[str, int], set[tuple]] = {}
    for file, line, end_col, detail in edge_sites:
        on_a_line.setdefault((file, line), set()).add((end_col, detail))
    assert any(len({detail for _, detail in sites}) >= 2 for sites in on_a_line.values())
    assert disagreements(name) == []
