"""A Cargo.toml with a field of the wrong type (cargo would reject it) costs its package, never the index.

The manifests under bench/fixtures/rust-probes/malformed are each wrong in one way (`target = 1`, `[lib] path = 5`, a
`[patch]` that is not a table, a bin with a number for a name, ...). Indexing must not raise for any, must say what it did
not read, and in a repo that also holds Python the Python side must still answer.
"""

import pytest

pytest.importorskip("tree_sitter_rust")

from conftest import FIXTURES  # noqa: E402
from ripple import rs_indexer, rs_modules  # noqa: E402
from ripple.cache import load_repo  # noqa: E402
from ripple.indexer import build_graph  # noqa: E402
from ripple.profile import RUST  # noqa: E402

MALFORMED = FIXTURES / "rust-probes" / "malformed"
NAMES = sorted(p.name for p in MALFORMED.iterdir())
# the manifests whose wrong field ripple can ignore (the package is read as if it were not there) and the ones that cost the package
IGNORED = {"bad_target", "bad_libpath", "bad_patch", "bad_binname", "bad_deps", "bad_workspace", "bad_arrays"}


@pytest.mark.parametrize("name", NAMES)
def test_a_manifest_of_the_wrong_type_never_stops_the_index(name):
    graph = build_graph(MALFORMED / name, RUST)  # does not raise
    edges = {(e.caller, e.callee) for e in graph.edges}
    if name in IGNORED:
        assert ("src/lib:f", "src/lib:g") in edges, graph.parse_errors
    else:
        assert not edges
        assert graph.parse_errors or graph.unread, "what was not read is said"


def test_a_package_whose_field_has_the_wrong_type_is_named_in_the_problems():
    ws = rs_modules.load_workspace(MALFORMED / "bad_package", [MALFORMED / "bad_package" / "src" / "lib.rs"])
    assert ws.crates == []
    graph = build_graph(MALFORMED / "bad_package", RUST)
    assert any("src/lib.rs" in problem and "no `mod` declaration reaches it" in problem for problem in graph.unread)


def test_a_wrong_type_deep_in_one_package_costs_that_package_and_the_one_that_needs_it_finds_its_library_unread(make_repo):
    repo = make_repo(
        {
            "Cargo.toml": '[workspace]\nmembers = ["a", "b"]\n',
            "a/Cargo.toml": '[package]\nname = "a"\nversion = "0.1.0"\nedition = "2021"\n\n[dependencies]\nb = { path = "../b" }\n',
            "a/src/lib.rs": "pub fn go() -> u32 { b::f() + local() }\nfn local() -> u32 { 1 }\n",
            "b/Cargo.toml": '[package]\nname = "b"\nversion = "0.1.0"\nedition = "2021"\n\n[dependencies]\nx = { package = [] }\n',
            "b/src/lib.rs": "pub fn f() -> u32 { 2 }\n",
        }
    )
    graph = build_graph(repo, RUST)
    assert {"a/src/lib:go", "a/src/lib:local"} <= set(graph.symbols) and "b/src/lib:f" not in graph.symbols
    assert [(e.caller, e.callee) for e in graph.edges] == [("a/src/lib:go", "a/src/lib:local")]
    assert [(u.text, u.reason) for u in graph.unresolved] == [("b::f", rs_indexer.DEP_NOT_READ)]
    assert any(problem.startswith("b/Cargo.toml (a field has the wrong type") for problem in graph.parse_errors)


def test_python_still_answers_in_a_mixed_repo_whose_cargo_toml_has_the_wrong_type(make_repo):
    repo = make_repo(
        {
            "app.py": "def b():\n    return 1\n\n\ndef a():\n    return b()\n",
            "rs/Cargo.toml": 'target = 1\n\n[package]\nname = "p"\nversion = "0.1.0"\nedition = "2021"\n',
            "rs/src/lib.rs": "pub fn f() -> u32 { g() }\nfn g() -> u32 { 1 }\n",
        }
    )
    result = load_repo(repo)  # does not raise
    assert {"app.a", "app.b"} <= set(result.graph.symbols)
    assert any(e.caller == "app.a" and e.callee == "app.b" for e in result.graph.edges)
    assert not result.incomplete


def test_python_still_answers_when_the_manifest_cost_its_package(make_repo):
    repo = make_repo(
        {
            "app.py": "def b():\n    return 1\n\n\ndef a():\n    return b()\n",
            "rs/Cargo.toml": '[package]\nname = 12\n',
            "rs/src/lib.rs": "pub fn f() {}\n",
        }
    )
    result = load_repo(repo)
    assert any(e.caller == "app.a" and e.callee == "app.b" for e in result.graph.edges)
