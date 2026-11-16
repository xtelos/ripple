"""Go interface implementers: the `implements` edges, held to the Go type checker and to what each probe says it must be.

A call through an interface value has no proven target (it stays unresolved), but ripple can prove a narrower claim, the
one each `implements` edge makes: this repo method belongs to a repo type whose method set provably has every method of
the interface, so the call MAY dispatch to it. Two checks, both without a Go toolchain (the compiler's answers are
committed beside each probe, in expected.json under "implementers"):

  - soundness, over every probe that has an expected.json: each edge ripple makes at an interface call is a method the
    compiler's method sets give a type implementing the interface, on EVERY variant that builds the file the call is in
    (platform, `go build` and `go test`; a test binary recompiles its dependents, so it is a build of its own). Recall is
    not asserted here: a candidate ripple leaves out is not wrong, and a type outside the repo can implement a repo
    interface, so the list is never complete;
  - intent, over the probes that annotate their calls: a line ending `// want: A.M, b:B.M` must have exactly those edges
    (a method is `Type.Method`, a package below the module `dir:Type.Method`), `// want: none` no edge at all. A class of
    doubt (an unexported method, an alias that names another type per build, a promoted method that an outside type may
    hide) is a probe whose want is `none` although the compiler's method sets do have the type.
"""

import json
import re
from collections import defaultdict
from pathlib import Path

import pytest
from test_go_probes import HAS_GO, ORACLE_NAMES, PROBES, digest, ripple_in, stripped

pytestmark = pytest.mark.skipif(not HAS_GO, reason="Go support is not installed (pip install 'ripple-code[go]')")

WANT = re.compile(r"//\s*want:\s*(.*?)\s*$")
IMPL_PROBES = sorted(p.name for p in PROBES.iterdir() if p.is_dir() and p.name.startswith("impl_"))


def oracle_answers(name: str):
    """(built, {(file, end, variant): [(method, {(id, declared in)})]}) from the committed expected.json."""
    expected = json.loads((PROBES / name / "expected.json").read_text())
    every = expected["variants"]
    built = {file: every if variants == "all" else variants for file, variants in expected["built"].items()}
    answers: dict[tuple[str, str, str], list[tuple[str, set[tuple[str, str]], set[str]]]] = defaultdict(list)
    for site in expected["implementers"]:
        found = {(c["id"], c["at"]) for c in site["candidates"]}
        for variant in every if site["variants"] == "all" else site["variants"]:
            answers[(site["file"], site["end"], variant)].append((site["method"], found, set(site["packages"])))
    return expected, built, answers


def symbol_files(resolver) -> dict[str, str]:
    return {s.id: s.file for p in resolver.program.packages.values() for s in p.symbols}


def wrong_edges(name: str) -> list[str]:
    """Every edge of an interface call that the compiler's method sets do not give on some variant that builds the file."""
    _, built, answers = oracle_answers(name)
    _, resolver, _ = ripple_in(PROBES / name)
    files = symbol_files(resolver)
    wrong = []
    for file, end_line, end_col, interface, member, ids in resolver.implement_sites:
        where = f"{file}:{end_line}:{end_col}"
        variants = built.get(file, [])
        if ids and not variants:
            wrong.append(f"{where} edges to {sorted(ids)}: no variant builds this file")
        confirmed = {ident: False for ident in ids}
        for variant in variants if ids else []:
            seen = [(found, there) for method, found, there in answers.get((file, f"{end_line}:{end_col}", variant), []) if method == member]
            if not seen:
                wrong.append(f"{where} edges to {sorted(ids)}: on {variant} the compiler sees no call of {member} through an interface here")
                continue
            for ident in ids:
                found, there = seen[0]
                if (stripped(ident), files[ident]) in found:
                    confirmed[ident] = True
                elif stripped(ident).split(":", 1)[0] in there:  # the type's package is in a build of the site that has no such method
                    wrong.append(f"{where} edge to {ident}: on {variant} the package is there and the compiler's candidates are {sorted(found)}")
        # a build that does not have the type's package says nothing; some build must have it, and have the type implement
        wrong += [f"{where} edge to {ident}: no variant has it as a candidate" for ident, ok in confirmed.items() if not ok and variants]
    return wrong


def relative_id(ident: str, probe: str) -> str:
    """`Type.Method` for the module's own package, `dir:Type.Method` below it (the module path left off)."""
    return stripped(ident).removeprefix(f"example.com/{probe}").lstrip("/").lstrip(":").replace(":", ":", 1)


def wants(name: str) -> dict[tuple[str, int], set[str]]:
    found: dict[tuple[str, int], set[str]] = {}
    for path in sorted((PROBES / name).rglob("*.go")):
        for number, line in enumerate(path.read_text().splitlines(), 1):
            match = WANT.search(line)
            if match:
                said = match.group(1)
                found[(path.relative_to(PROBES / name).as_posix(), number)] = set() if said == "none" else {w.strip() for w in said.split(",")}
    return found


def edges_by_line(name: str) -> dict[tuple[str, int], set[str]]:
    _, resolver, _ = ripple_in(PROBES / name)
    found: dict[tuple[str, int], set[str]] = defaultdict(set)
    for edge in resolver.implement_edges:
        found[(edge.file, edge.line)].add(relative_id(edge.callee, name))
    return found


def test_there_are_implementer_probes_and_each_has_what_the_compiler_said():
    assert len(IMPL_PROBES) >= 15
    for name in IMPL_PROBES:
        expected = json.loads((PROBES / name / "expected.json").read_text())
        assert expected["digest"] == digest(PROBES / name), f"{name} changed since the oracle ran: rerun bench/go_oracle/generate.py {name}"
        assert expected["implementers"], f"{name} has no interface call the oracle answered"


@pytest.mark.parametrize("name", ORACLE_NAMES)
def test_every_implements_edge_is_a_method_the_compiler_gives_an_implementing_type(name):
    assert wrong_edges(name) == []


@pytest.mark.parametrize("name", IMPL_PROBES)
def test_each_annotated_call_has_exactly_the_edges_its_probe_says(name):
    wanted = wants(name)
    assert wanted, f"{name} has no // want: line"
    got = edges_by_line(name)
    for (file, line), methods in sorted(wanted.items()):
        assert got.get((file, line), set()) == methods, f"{name} {file}:{line}"
    unannotated = {key for key in got if key not in wanted}
    assert not unannotated, f"{name}: edges at lines with no want: {sorted(unannotated)}"


def test_a_type_of_a_cgo_file_is_never_a_candidate_though_its_sibling_in_a_plain_file_is():
    """The oracle runs CGO_ENABLED=0 and cannot load a file that imports C, so this probe has no expected.json: it is held to its
    want comments alone. Native is declared in c.go and only a build with cgo has it."""
    name = "cgo_type_implements"
    wanted = wants(name)
    got = edges_by_line(name)
    assert wanted and any(not methods for methods in wanted.values()) and any(methods for methods in wanted.values())
    for (file, line), methods in sorted(wanted.items()):
        assert got.get((file, line), set()) == methods, f"{name} {file}:{line}"
    assert not {key for key in got if key not in wanted}


def test_the_probes_between_them_prove_edges_and_refuse_them():
    proven = refused = 0
    for name in IMPL_PROBES:
        _, resolver, _ = ripple_in(PROBES / name)
        proven += sum(1 for site in resolver.implement_sites if site[5])
        refused += sum(1 for line, methods in wants(name).items() if not methods)
    assert proven >= 40 and refused >= 10


def test_an_implements_edge_is_not_a_call_edge_and_the_call_stays_unresolved():
    _, resolver, _ = ripple_in(PROBES / "impl_basic")
    assert resolver.implement_edges and all(e.kind == "implements" for e in resolver.implement_edges)
    assert all(e.kind == "call" for e in resolver.edges)
    unresolved_lines = {(u.file, u.line) for u in resolver.unresolved_calls if "a method call on the interface" in u.reason}
    assert {(e.file, e.line) for e in resolver.implement_edges} <= unresolved_lines
    assert {e.via for e in resolver.implement_edges} == {"example.com/impl_basic:Store", "example.com/impl_basic:Logger"}
