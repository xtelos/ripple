"""Queries over a graph whose ids are TypeScript's: `src/util/math:clamp`, module = path, a colon before the member.

The graphs here are written by hand, so these run without tree-sitter. One test at the end indexes the real
fixture and is skipped when the parser is not installed.
"""

import pytest
from conftest import FIXTURES

from ripple.model import Edge, Graph, Symbol, Unresolved
from ripple.queries import callees, callers, find_symbol, impact, path


def sym(sid, kind, file, line=1):
    return Symbol(sid, kind, file, line, line + 1)


def ts_graph():
    symbols = [
        sym("src/util/math", "module", "src/util/math.ts"),
        sym("src/util/math:clamp", "function", "src/util/math.ts", 3),
        sym("src/util", "module", "src/util/index.ts"),
        sym("src/util:pad", "function", "src/util/index.ts", 2),
        sym("src/models/item", "module", "src/models/item.ts"),
        sym("src/models/item:Item", "class", "src/models/item.ts", 2),
        sym("src/models/item:Item.constructor", "method", "src/models/item.ts", 3),
        sym("src/models/item:Item.restock", "method", "src/models/item.ts", 6),
        sym("src/models/item:Item.describe", "method", "src/models/item.ts", 9),
        sym("src/models/perishable", "module", "src/models/perishable.ts"),
        sym("src/models/perishable:Perishable", "class", "src/models/perishable.ts", 2),
        sym("src/models/perishable:Perishable.describe", "method", "src/models/perishable.ts", 4),
        sym("src/m", "module", "src/m.ts"),
        sym("src/m:A.make", "method", "src/m.ts", 3),  # a static and an instance member of one name
        sym("src/m:A.make#2", "method", "src/m.ts", 6),
        sym("tests/item.test", "module", "tests/item.test.ts"),
        sym("tests/item.test:Item > restocks", "function", "tests/item.test.ts", 4),
        sym("tests/test_pad", "module", "tests/test_pad.ts"),  # a name pytest would collect, in a .ts file
        sym("tests/test_pad:test_pad_works", "function", "tests/test_pad.ts", 2),
    ]
    edges = [
        Edge("src/models/item:Item.restock", "src/util/math:clamp", "src/models/item.ts", 7),
        Edge("src/models/item:Item.describe", "src/util:pad", "src/models/item.ts", 10),
        Edge("tests/item.test:Item > restocks", "src/models/item:Item.constructor", "tests/item.test.ts", 5, "constructor"),
        Edge("tests/item.test:Item > restocks", "src/models/item:Item.restock", "tests/item.test.ts", 6),
        Edge("tests/test_pad:test_pad_works", "src/util:pad", "tests/test_pad.ts", 3),
    ]
    unresolved = [
        Unresolved("src/models/perishable:Perishable.describe", "src/models/perishable.ts", 5, "math.clamp", "the receiver is an unannotated parameter"),
        Unresolved("src/util:pad", "src/util/index.ts", 3, "other.pad", "x"),
    ]
    return Graph(symbols={s.id: s for s in symbols}, edges=edges, unresolved=unresolved, modules=modules_of(symbols))


def modules_of(symbols):
    """{file: module id} as the indexer records it."""
    return {s.file: s.id for s in symbols if s.kind == "module"}


def lookup_graph():
    """Files whose names invite the wrong answer: src/a.ts beside src/a/index.ts (the index file is src/a/index),
    util.ts at the root beside src/util.ts, and two files of one name in different directories."""
    symbols = [
        sym("src/a", "module", "src/a.ts"),
        sym("src/a:fb", "function", "src/a.ts", 2),
        sym("src/a/index", "module", "src/a/index.ts"),  # src/a is taken by a.ts
        sym("src/a/index:fb", "function", "src/a/index.ts", 2),
        sym("util", "module", "util.ts"),
        sym("util:f", "function", "util.ts", 2),
        sym("src/util", "module", "src/util.ts"),
        sym("src/util:f", "function", "src/util.ts", 2),
        sym("pkg1/shared", "module", "pkg1/shared.ts"),
        sym("pkg1/shared:g", "function", "pkg1/shared.ts", 2),
        sym("pkg2/shared", "module", "pkg2/shared.ts"),
        sym("pkg2/shared:g", "function", "pkg2/shared.ts", 2),
    ]
    return Graph(symbols={s.id: s for s in symbols}, modules=modules_of(symbols))


@pytest.fixture
def graph():
    return ts_graph()


# ---- the name of a symbol

def test_a_typescript_symbol_is_named_by_its_member_not_its_whole_id():
    assert sym("src/util/math:clamp", "function", "x.ts").name == "clamp"
    assert sym("src/models/item:Item.restock", "method", "x.ts").name == "restock"
    assert sym("src/m:A.make#2", "method", "x.ts").name == "make"  # the clash suffix is not part of the name
    assert sym("src/util/math", "module", "x.ts").name == "math"
    assert sym("tests/a.test:Item > restocks 1.5 kg", "function", "x.ts").name == "Item > restocks 1.5 kg"


def test_a_python_symbol_is_named_as_before():
    assert sym("shop.pricing.price_order", "function", "x.py").name == "price_order"
    assert sym("shop", "module", "x.py").name == "shop"


# ---- looking a symbol up

def test_the_full_id_is_found(graph):
    assert find_symbol(graph, "src/util/math:clamp") == {"symbol": "src/util/math:clamp"}


def test_the_path_and_member_with_or_without_the_extension_are_found(graph):
    for query in ("src/util/math:clamp", "src/util/math.ts:clamp", "util/math:clamp", "math:clamp"):
        assert find_symbol(graph, query) == {"symbol": "src/util/math:clamp"}, query


def test_an_index_file_is_its_directory_in_every_spelling(graph):
    for query in ("src/util:pad", "src/util/index:pad", "src/util/index.ts:pad"):
        assert find_symbol(graph, query) == {"symbol": "src/util:pad"}, query
    assert find_symbol(graph, "src/util/index.ts") == {"symbol": "src/util"}


def test_a_module_is_found_by_its_path_with_or_without_the_extension(graph):
    assert find_symbol(graph, "src/util/math.ts") == {"symbol": "src/util/math"}
    assert find_symbol(graph, "util/math") == {"symbol": "src/util/math"}


def test_a_bare_member_name_is_found_when_one_symbol_has_it(graph):
    assert find_symbol(graph, "clamp") == {"symbol": "src/util/math:clamp"}
    assert find_symbol(graph, "restock") == {"symbol": "src/models/item:Item.restock"}


def test_class_dot_method_is_found_when_one_symbol_has_it(graph):
    assert find_symbol(graph, "Item.restock") == {"symbol": "src/models/item:Item.restock"}
    assert find_symbol(graph, "Perishable.describe") == {"symbol": "src/models/perishable:Perishable.describe"}


def test_an_ambiguous_member_lists_every_candidate_and_picks_none(graph):
    found = find_symbol(graph, "describe")
    assert "symbol" not in found
    assert "ambiguous" in found["error"]
    assert found["candidates"] == ["src/models/item:Item.describe", "src/models/perishable:Perishable.describe"]


def test_a_clash_suffix_makes_the_plain_name_ambiguous_and_the_full_id_exact(graph):
    found = find_symbol(graph, "A.make")
    assert "symbol" not in found
    assert found["candidates"] == ["src/m:A.make", "src/m:A.make#2"]
    assert find_symbol(graph, "src/m:A.make#2") == {"symbol": "src/m:A.make#2"}
    assert find_symbol(graph, "make#2") == {"symbol": "src/m:A.make#2"}


def test_an_unknown_member_suggests_close_ids(graph):
    found = find_symbol(graph, "src/util/math:clmap")
    assert "no symbol named" in found["error"]
    assert "src/util/math:clamp" in found["suggestions"]


def test_a_python_style_dotted_query_does_not_match_a_path_id(graph):
    assert "symbol" not in find_symbol(graph, "src.util.math.clamp")


# ---- the queries that take a name

def test_callers_callees_and_path_accept_the_short_forms(graph):
    assert [c["symbol"] for c in callers(graph, "clamp")["callers"]] == ["src/models/item:Item.restock"]
    assert [c["symbol"] for c in callees(graph, "Item.restock")["callees"]] == ["src/util/math:clamp"]
    chain = path(graph, "tests/item.test:Item > restocks", "src/util/math.ts:clamp")
    assert [hop["symbol"] for hop in chain["path"]] == [
        "tests/item.test:Item > restocks",
        "src/models/item:Item.restock",
        "src/util/math:clamp",
    ]


def test_callers_of_a_class_include_the_callers_of_its_constructor(graph):
    assert [c["symbol"] for c in callers(graph, "src/models/item:Item")["callers"]] == ["tests/item.test:Item > restocks"]


def test_impact_prints_ids_as_they_are_and_lists_no_pytest_node_ids_for_typescript(graph):
    result = impact(graph, "clamp")
    assert result["symbol"] == "src/util/math:clamp"
    callers_listed = [e["symbol"] for entries in result["by_file"].values() for e in entries]
    assert callers_listed == ["src/models/item:Item.restock", "tests/item.test:Item > restocks"]
    assert result["tests"] == []  # no TypeScript test runner is read yet, so every caller is code to update


def test_a_typescript_file_named_like_a_pytest_file_is_not_given_a_node_id(graph):
    result = impact(graph, "src/util:pad")
    listed = [e["symbol"] for entries in result["by_file"].values() for e in entries]
    assert "tests/test_pad:test_pad_works" in listed
    assert result["tests"] == []


def test_possible_missed_callers_match_a_typescript_id_by_its_member_name(graph):
    result = impact(graph, "src/util/math:clamp")
    assert result["possible_missed_total"] == 1
    assert result["possible_missed_callers"] == [
        {"caller": "src/models/perishable:Perishable.describe", "call": "math.clamp", "location": "src/models/perishable.ts:5"}
    ]


# ---- the real fixture

def test_the_indexed_fixture_answers_to_the_short_forms():
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_typescript")
    from ripple.indexer import build_graph
    from ripple.profile import TYPESCRIPT

    g = build_graph(FIXTURES / "ts" / "warehouse-ts", TYPESCRIPT)
    assert find_symbol(g, "clamp") == {"symbol": "src/util/math:clamp"}
    assert find_symbol(g, "Item.restock") == {"symbol": "src/models/item:Item.restock"}
    assert find_symbol(g, "models/item.ts:Item.restock") == {"symbol": "src/models/item:Item.restock"}
    ambiguous = find_symbol(g, "describe")
    assert "src/models/item:Item.describe" in ambiguous["candidates"]
    assert "src/models/perishable:Perishable.describe" in ambiguous["candidates"]
    result = impact(g, "clamp")
    assert result["affected"] > 0 and result["tests"] == []


# ---- a path in a query is read through the ids the indexer gave the files

@pytest.mark.parametrize(
    ("query", "symbol"),
    [
        ("src/a/index.ts:fb", "src/a/index:fb"),  # not src/a.ts's fb
        ("src/a/index:fb", "src/a/index:fb"),
        ("a/index.ts:fb", "src/a/index:fb"),
        ("src/a/index.ts", "src/a/index"),
        ("src/a/:fb", "src/a/index:fb"),  # a directory is its index file
        ("src/a/", "src/a/index"),
        ("src/a.ts:fb", "src/a:fb"),
        ("src/a:fb", "src/a:fb"),
        ("src/a", "src/a"),
        ("util.ts", "util"),  # not src/util.ts
        ("util.ts:f", "util:f"),
        ("src/util.ts", "src/util"),
        ("src/util.ts:f", "src/util:f"),
        ("pkg1/shared.ts:g", "pkg1/shared:g"),
    ],
)
def test_a_path_names_the_file_the_indexer_gave_that_id_never_a_folded_one(query, symbol):
    assert find_symbol(lookup_graph(), query) == {"symbol": symbol}


@pytest.mark.parametrize(
    ("query", "candidates"),
    [
        ("shared.ts:g", ["pkg1/shared:g", "pkg2/shared:g"]),
        ("shared:g", ["pkg1/shared:g", "pkg2/shared:g"]),
        ("shared.ts", ["pkg1/shared", "pkg2/shared"]),
    ],
)
def test_a_path_that_is_no_file_but_the_tail_of_several_stays_ambiguous(query, candidates):
    found = find_symbol(lookup_graph(), query)
    assert "symbol" not in found and found["candidates"] == candidates


def test_a_trailing_slash_names_the_index_file_of_that_directory_only():
    graph = ts_graph()  # src/util/index.ts is the module src/util
    assert find_symbol(graph, "src/util/:pad") == {"symbol": "src/util:pad"}
    assert find_symbol(graph, "src/util/") == {"symbol": "src/util"}
    assert "symbol" not in find_symbol(graph, "src/models/")  # no index file there: no guess


def test_the_assigned_ids_decide_even_when_the_files_are_indexed_for_real(make_repo):
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_typescript")
    from ripple.indexer import build_graph
    from ripple.profile import TYPESCRIPT

    repo = make_repo(
        {
            "src/a.ts": "export function fb() {}\n",
            "src/a/index.ts": "export function fb() {}\n",
            "src/use_dir.ts": 'import { fb } from "./a/index";\nexport function viaDir() { fb() }\n',
        }
    )
    graph = build_graph(repo, TYPESCRIPT)
    assert "src/a/index:fb" in graph.symbols and "src/a:fb" in graph.symbols
    assert find_symbol(graph, "src/a/index.ts:fb") == {"symbol": "src/a/index:fb"}
    assert [c["symbol"] for c in callers(graph, "src/a/index.ts:fb")["callers"]] == ["src/use_dir:viaDir"]
