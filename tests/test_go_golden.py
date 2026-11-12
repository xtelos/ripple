"""The Go golden fixture: bench/fixtures/go/shop and its hand-written truth (bench/fixtures/go/truth.json).

The fixture is a compiling module (go vet ./... is clean) covering every rule of the minimal model in both directions: an
edge when proven, an unresolved call with its reason when not. The truth is scored exactly: every edge it lists is
found, no other edge is made, and each pinned unresolved call is unresolved with the stated reason. bench/fixtures/go/multimod
pins the three ways an import of another module of the repo is, or is not, the repo's copy.

These need tree-sitter-go (pip install 'ripple-code[go]') and are skipped without it.
"""

import importlib.util
import json
from pathlib import Path

import pytest

from ripple.indexer import build_graph
from ripple.profile import GO

HAS_GO = importlib.util.find_spec("tree_sitter_go") is not None and importlib.util.find_spec("tree_sitter") is not None
pytestmark = pytest.mark.skipif(not HAS_GO, reason="Go support is not installed (pip install 'ripple-code[go]')")

FIXTURES = Path(__file__).resolve().parent.parent / "bench" / "fixtures" / "go"
SHOP = FIXTURES / "shop"
TRUTH = json.loads((FIXTURES / "truth.json").read_text())
MODULE = "example.com/shop/"


def full(ident: str) -> str:
    return ident.replace("~", MODULE)


@pytest.fixture(scope="module")
def graph():
    return build_graph(SHOP, GO)


def test_every_edge_the_truth_lists_is_found_and_no_other_edge_is_made(graph):
    want = {(full(a), full(b)) for a, b in TRUTH["edges"]}
    got = {(e.caller, e.callee) for e in graph.edges}
    assert want - got == set(), "missed"
    assert got - want == set(), "wrong"


def test_the_edges_the_truth_allows_to_be_missed_are_missed(graph):
    got = {(e.caller, e.callee) for e in graph.edges}
    for pair, why in TRUTH["allowed_missing"].items():
        a, b = (full(x) for x in pair.split(" -> "))
        assert (a, b) not in got, why


def test_every_pinned_unresolved_call_is_unresolved_for_the_stated_reason(graph):
    for caller, text, fragment, why in TRUTH["unresolved"]:
        found = [u for u in graph.unresolved if u.caller == full(caller) and u.text == text]
        assert found, (caller, text, why)
        assert all(full(fragment) in u.reason or fragment in u.reason for u in found), (caller, text, [u.reason for u in found])


def test_no_unresolved_call_is_left_unpinned_except_in_the_unparsable_function(graph):
    pinned = {(full(caller), text) for caller, text, *_ in TRUTH["unresolved"]}
    got = {(u.caller, u.text) for u in graph.unresolved if u.caller != f"{MODULE}pricing:Ptr"}
    assert got == pinned


def test_the_unparsable_file_is_listed_and_its_function_is_still_a_symbol(graph):
    assert graph.parse_errors == TRUTH["parse_errors"]
    assert f"{MODULE}pricing:Ptr" in graph.symbols
    assert {u.caller for u in graph.unresolved if "tree-sitter could not read" in u.reason} == {f"{MODULE}pricing:Ptr"}


def test_no_non_edge_is_an_edge(graph):
    got = {(e.caller, e.callee) for e in graph.edges}
    for a, b, why in TRUTH["non_edges"]:
        assert (full(a), full(b)) not in got, why


def test_every_edge_end_is_a_symbol_and_every_edge_site_is_inside_its_caller(graph):
    for e in graph.edges:
        assert e.callee in graph.symbols, e
        caller = graph.symbols[e.caller]
        if caller.kind != "module":
            assert caller.file == e.file and caller.line <= e.line <= caller.end_line, e


def test_the_packages_are_the_import_paths_and_the_files_nobody_builds_are_not_read(graph):
    assert {s.id for s in graph.symbols.values() if s.kind == "module"} == set(TRUTH["packages"])
    for rel in TRUTH["not_read"]:
        assert rel not in graph.modules and not [s for s in graph.symbols.values() if s.file == rel], rel
    for rel in TRUTH["read_though_generated"]:
        assert rel in graph.modules
    assert f"{MODULE}inventory" == graph.modules["inventory/stock.go"] == graph.modules["inventory/items_gen.go"]
    assert graph.modules["cart/cart_test.go"] == f"{MODULE}cart_test"
    assert graph.modules["cart/internal_test.go"] == f"{MODULE}cart"


def test_a_build_variant_keeps_the_plain_id_for_the_first_declaration_and_counts_up(graph):
    assert {f"{MODULE}cart:platform", f"{MODULE}cart:platform#2", f"{MODULE}cart:init", f"{MODULE}cart:init#2"} <= set(graph.symbols)


def test_the_calls_proven_to_leave_the_repo_are_counted_with_their_reasons(graph):
    for reason, n in TRUTH["external"].items():
        assert graph.external_reasons[reason] == n, reason
    assert graph.external_calls == sum(graph.external_reasons.values())


def test_the_checked_in_fixture_has_no_go_files_ripple_reads_beyond_the_truth(graph):
    on_disk = {p.relative_to(SHOP).as_posix() for p in SHOP.rglob("*.go")}
    assert set(graph.modules) | set(TRUTH["not_read"]) == on_disk | set()


# ---- the module-level cases: published, replace and workspace imports ---------------------------------


@pytest.fixture(scope="module")
def multimod():
    return build_graph(FIXTURES / "multimod", GO)


def test_an_import_of_another_repo_module_is_the_repo_copy_only_with_a_replace_or_a_go_work(multimod):
    got = {(e.caller, e.callee) for e in multimod.edges}
    assert got == {
        ("ex.com/tool:main", "ex.com/lib/util:Do"),  # replace ex.com/lib => ../lib
        ("ex.com/wsapp:main", "ex.com/lib/util:Do"),  # go.work lists lib and wsapp
    }
    # app requires ex.com/lib v1.2.0 with neither: it builds the published version, which may differ from this checkout
    # and ripple does not read that version, so the call is unresolved: external would say it cannot reach repo code
    assert multimod.external_reasons == {}
    assert [(u.caller, u.text) for u in multimod.unresolved] == [("ex.com/app:main", "util.Do")]
    assert "published version" in multimod.unresolved[0].reason


def test_the_module_ids_of_the_modules_are_their_paths(multimod):
    assert {s.id for s in multimod.symbols.values() if s.kind == "module"} == {
        "ex.com/app", "ex.com/lib/util", "ex.com/tool", "ex.com/wsapp",
    }  # fmt: skip
