"""The method-call probes: what ripple says at each `recv.m()` site, against the compiler's answer committed beside each probe.

bench/fixtures/rust-probes/crates/method_* are crates that pass `cargo check`, organised by the rules of the Rust Reference's
method-call expressions (the receiver's candidate types, inherent before trait at one step, impls spread over modules, files and
crates, aliases, `Self`, generics and their bounds, shadowed names, `#[cfg]`, macros, Deref). Each call carries a marker comment:

    s.name() //~ name=E                  an edge, and the compiler (bench/fixtures/rust-probes/oracle, written by rust-analyzer)
                                         resolved the call to the method the edge goes to
    s.name() //~ name=U:later probe step unresolved, with a reason that contains the text

Several markers on one line are separated by `;`. A line holds at most one call of each name, so a marker is one site. No toolchain is
needed to run this: the compiler's answers are the oracle files. (tests/test_rs_probes.py runs the same crates for every edge and
every external ripple gives, marked or not.)
"""

import re

import pytest

pytest.importorskip("tree_sitter_rust")

from test_rs_probes import CRATES, Probe  # noqa: E402

MARKER = re.compile(r"//~\s*(.*)$")
NAMES = sorted(p.name for p in CRATES.iterdir() if p.is_dir() and p.name.startswith("method_"))


def markers(probe):
    """(file, line, method name, "E" or "U", text) for every marker in the probe's sources."""
    out = []
    for path in sorted((CRATES / probe).rglob("*.rs")):
        if "target" in path.relative_to(CRATES / probe).parts:
            continue
        for number, line in enumerate(path.read_text().splitlines(), 1):
            found = MARKER.search(line)
            if found:
                for part in found.group(1).split(";"):
                    name, _, what = part.strip().partition("=")
                    kind, _, text = what.partition(":")
                    out.append((path.relative_to(CRATES / probe).as_posix(), number, name, kind, text))
    return out


PROBES: dict[str, Probe] = {}


def probe_of(name):
    if name not in PROBES:
        PROBES[name] = Probe(name)
    return PROBES[name]


def sites(probe, file, line, name):
    return [
        (result, name_end)
        for f, _, _, result, _, ln, written, name_end in probe.index.resolved_sites()
        if f == file and ln == line and "." in written and written.rsplit(".", 1)[-1] == name
    ]


CASES = [(name, *m) for name in NAMES for m in markers(name)]


@pytest.mark.parametrize("probe, file, line, method, kind, text", CASES, ids=[f"{c[0]}:{c[1]}:{c[2]}:{c[3]}" for c in CASES])
def test_the_call_is_what_its_marker_says(probe, file, line, method, kind, text):
    p = probe_of(probe)
    found = sites(p, file, line, method)
    assert len(found) == 1, f"{file}:{line} `{method}`: {len(found)} sites"
    result, name_end = found[0]
    if kind == "E":
        assert result[0] == "edge", f"{file}:{line} `{method}` is {result}"
        row, col = p.at(file, name_end)
        answers = p.answers.get((file, row, col), [])
        assert len(answers) == 1 and answers[0]["kind"] == "repo" and len(answers[0]["defs"]) == 1, f"the compiler's answer at {file}:{line}: {answers}"
        assert p.where(result[1]) == tuple(answers[0]["defs"][0])
        assert result[2] == ()  # a method call has no override edges
    else:
        assert kind == "U", f"{file}:{line}: marker kind {kind!r}"
        assert result[0] == "unresolved", f"{file}:{line} `{method}` is {result}"
        assert text in result[1], f"{file}:{line} `{method}`: {result[1]}"
        assert result[1].startswith("a method call: ")


def test_every_probe_has_markers_and_the_edges_and_refusals_are_both_there():
    assert len(NAMES) >= 10, NAMES
    kinds = [m[3] for name in NAMES for m in markers(name)]
    assert kinds.count("E") >= 50 and kinds.count("U") >= 50, (kinds.count("E"), kinds.count("U"))


@pytest.mark.parametrize("probe", NAMES)
def test_no_method_call_is_outside_the_repo_and_none_is_an_edge_the_compiler_does_not_give(probe):
    """Slice one proves edges only: a method call is never counted as outside the repo, and every edge agrees with the compiler."""
    p = probe_of(probe)
    wrong, _ = p.judge()
    assert not wrong, wrong
    for file, _, _, result, _, _, written, _ in p.index.resolved_sites():
        if "." in written.rsplit("::", 1)[-1] and result[0] != "ctor":
            assert result[0] in ("edge", "unresolved"), (file, written, result)
