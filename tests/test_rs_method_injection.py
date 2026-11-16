"""A comment or an inert attribute at every boundary of a method call's source changes nothing ripple says.

tests/test_rs_injection.py edits a few seeded places in every probe crate. A method call is read from many small pieces (the
receiver name, the dot, the name, the binding that writes its type, the `&` and `mut` of a reference type, `self` and its form, the
`let` it is after), and a comment tree-sitter puts between any two of them must not move one. So here every method probe crate is
edited at EVERY token boundary at once (`/* c */`), before every item, statement and `let` (`// c` and a newline, then
`#[allow(unused)]`), and the graph is compared with the original's: the same edges, the same reasons for each unresolved call.
"""

import shutil

import pytest

pytest.importorskip("tree_sitter_rust")

from test_rs_injection import CRATES, Edits, _identity, candidates, read, signature  # noqa: E402

from ripple import rs_indexer as rs  # noqa: E402

NAMES = sorted(p.name for p in CRATES.iterdir() if p.is_dir() and p.name.startswith("method_"))


def edited(root, tmp_path, name, kind):
    parser = rs.load_parser()
    sources = sorted(p for p in root.rglob("*.rs") if "target" not in p.relative_to(root).parts)
    copy = tmp_path / f"{name}-{kind}"
    if not (tmp_path / "_ext").exists():  # a probe's dependencies outside the repo (`../_ext/x`) stay where the copy finds them
        shutil.copytree(CRATES / "_ext", tmp_path / "_ext", ignore=lambda d, names: [n for n in names if n in ("Cargo.lock", "target")])
    shutil.copytree(root, copy, ignore=lambda d, names: [n for n in names if n == "Cargo.lock" or (n == "target" and "Cargo.toml" in names)])
    edits = {}
    for p in sources:
        data = p.read_bytes()
        chosen = [c for c in candidates(data, parser.parse(data).root_node) if c[1] == kind]
        rel = p.relative_to(root).as_posix()
        edits[rel] = Edits(data, chosen)
        (copy / rel).write_bytes(edits[rel].apply(data))
    return copy, edits


@pytest.mark.parametrize("kind", ["block", "line", "attr"])
@pytest.mark.parametrize("name", NAMES)
def test_an_edit_at_every_boundary_changes_nothing(name, kind, tmp_path):
    root = CRATES / name
    before = read(root)
    copy, edits = edited(root, tmp_path, name, kind)
    after = read(copy)
    expected = signature(before, {f: _identity(e) for f, e in edits.items()})
    got = signature(after, edits)
    for part in expected:
        assert got[part] == expected[part], f"{name} ({kind}): {part} changed\n  missing: {[x for x in expected[part] if x not in got[part]][:5]}\n  extra: {[x for x in got[part] if x not in expected[part]][:5]}"


def test_the_edits_reach_the_method_calls():
    """Every kind of edit has many places to go in the method probes, and the graph they are compared on has edges and refusals."""
    parser = rs.load_parser()
    counts = {"block": 0, "line": 0, "attr": 0}
    for name in NAMES:
        for source in (CRATES / name).rglob("*.rs"):
            if "target" in source.relative_to(CRATES / name).parts:
                continue
            data = source.read_bytes()
            for _, kind in candidates(data, parser.parse(data).root_node):
                counts[kind] += 1
    assert counts["block"] > 4000 and counts["line"] > 400 and counts["attr"] > 150, counts
    graph = read(CRATES / "method_first_step")
    assert len([e for e in graph.edges if e.kind == "call"]) >= 10 and len(graph.unresolved) >= 8
