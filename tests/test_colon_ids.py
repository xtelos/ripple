"""`::` in an id is a member separator only for a language that writes `::` members (Rust), decided by the symbol's file.

A TypeScript test callback is named by its title (`tests/a.test:parses a::b`), and a title may hold `::`, so reading `::`
by the look of an id changed the name and the lookup of some TypeScript ids. Every Python and TypeScript answer here is
what origin/main gave for the same graph (the expected values were taken from it).
"""

import pytest

from ripple import queries
from ripple.model import Edge, Graph, Symbol, Unresolved
from ripple.registry import writes_colon_members


def build(rust=False):
    symbols = [
        Symbol("tests/a.test", "module", "tests/a.test.ts", 1, 50),
        Symbol("tests/a.test:hover styles", "function", "tests/a.test.ts", 2, 4),
        Symbol("tests/a.test:parses a::b", "function", "tests/a.test.ts", 5, 7),
        Symbol("tests/a.test:suite > a::b case", "function", "tests/a.test.ts", 8, 9),
        Symbol("tests/a.test:run", "function", "tests/a.test.ts", 10, 12),
        Symbol("tests/b.test", "module", "tests/b.test.tsx", 1, 20),
        Symbol("tests/b.test:::weird", "function", "tests/b.test.tsx", 2, 3),
        Symbol("src/cart", "module", "src/cart.ts", 1, 30),
        Symbol("src/cart:Cart", "class", "src/cart.ts", 1, 30),
        Symbol("src/cart:Cart.add", "method", "src/cart.ts", 2, 3),
        Symbol("app.cart", "module", "app/cart.py", 1, 9),
        Symbol("app.cart.Cart.add", "method", "app/cart.py", 2, 3),
    ]
    if rust:
        symbols += [
            Symbol("src/lib", "module", "src/lib.rs", 1, 9),
            Symbol("src/lib:Foo::new", "method", "src/lib.rs", 2, 3),
            Symbol("src/lib:parses a::b", "function", "src/lib.rs", 5, 6),
        ]
    edges = [Edge("tests/a.test:parses a::b", "src/cart:Cart.add", "tests/a.test.ts", 6)]
    unresolved = [Unresolved("tests/a.test:run", "tests/a.test.ts", 11, "obj.parses a::b", "a method call")]
    graph = Graph(symbols={s.id: s for s in symbols}, edges=edges, unresolved=unresolved)
    graph.modules = {"tests/a.test.ts": "tests/a.test", "tests/b.test.tsx": "tests/b.test", "src/cart.ts": "src/cart", "app/cart.py": "app.cart"}
    if rust:
        graph.modules["src/lib.rs"] = "src/lib"
    return graph


def test_only_a_file_of_a_language_with_colon_members_reads_them():
    assert [writes_colon_members(f) for f in ("src/lib.rs", "a.ts", "a.tsx", "app.py", "x")] == [True, False, False, False, False]


@pytest.mark.parametrize(
    "sid, file, name",
    [
        ("tests/a.test:parses a::b", "tests/a.test.ts", "parses a::b"),  # a TypeScript title holding `::`
        ("tests/b.test:::weird", "tests/b.test.tsx", "::weird"),
        ("tests/a.test:suite > a::b case", "tests/a.test.ts", "suite > a::b case"),
        ("src/lib:parses a::b", "src/lib.rs", "b"),  # the same id of a Rust file is a member path
        ("src/lib:Foo::new", "src/lib.rs", "new"),
        ("src/cart:Cart.add#2", "src/cart.ts", "add"),
        ("app.cart.Cart.add", "app/cart.py", "add"),
    ],
)
def test_a_symbol_is_named_by_its_file_s_language_not_by_the_look_of_its_id(sid, file, name):
    assert Symbol(sid, "function", file, 1, 2).name == name


@pytest.mark.parametrize("rust", [False, True])
@pytest.mark.parametrize(
    "query, found",
    [
        ("tests/a.test:parses a::b", "tests/a.test:parses a::b"),
        ("tests/b.test:::weird", "tests/b.test:::weird"),
        ("tests/b.test.tsx:::weird", "tests/b.test:::weird"),
        ("weird", "tests/b.test:::weird"),
        ("tests/a.test:suite > a::b case", "tests/a.test:suite > a::b case"),
        ("tests/a.test:hover styles", "tests/a.test:hover styles"),
    ],
)
def test_typescript_ids_with_colons_in_a_title_resolve_as_they_did_with_or_without_rust_symbols_in_the_graph(query, found, rust):
    assert queries.find_symbol(build(rust), query) == {"symbol": found}


@pytest.mark.parametrize("rust", [False, True])
def test_the_title_a_colon_starts_is_the_path_colon_of_a_typescript_file(rust):
    """`tests/b.test.tsx:::weird` is the file, a colon, and the title `::weird`; the `::` reading of a Rust id must not take it."""
    graph = build(rust)
    assert queries._split_path(graph, "tests/b.test.tsx:::weird") == ("tests/b.test.tsx", ":", "::weird")
    assert queries._split_path(graph, "tests/a.test:parses a::b") == ("tests/a.test", ":", "parses a::b")


@pytest.mark.parametrize("rust", [False, True])
def test_a_typescript_title_is_a_top_level_member_and_suggested_for_a_near_miss(rust):
    graph = build(rust)
    assert queries._members(graph, "tests/a.test") == ["tests/a.test:hover styles", "tests/a.test:parses a::b", "tests/a.test:run"]
    answer = queries.find_symbol(graph, "tests/a.test:nope")
    assert "tests/a.test:parses a::b" in answer["suggestions"]


@pytest.mark.parametrize(
    "query",
    ["parses a::b", "a::b", "tests/a.test.ts::hover styles", "tests/a.test::hover styles", "src/cart::Cart.add"],
)
def test_queries_origin_main_could_not_resolve_are_still_not_resolved_in_a_graph_without_rust(query):
    assert "error" in queries.find_symbol(build(False), query)


def test_a_call_text_is_matched_to_a_symbol_by_the_name_its_language_ends_it_with():
    """`obj.parses a::b` is a TypeScript call text: its name is the part after the last dot, which is the title."""
    assert queries._called_name("obj.parses a::b", "tests/a.test.ts") == "parses a::b"
    assert queries._called_name("Foo::new", "src/lib.rs") == "new"
    assert queries._called_name("self.store.save", "src/lib.rs") == "save"
    for rust in (False, True):
        answer = queries.impact(build(rust), "tests/a.test:parses a::b")
        assert answer["possible_missed_callers"] == [{"call": "obj.parses a::b", "caller": "tests/a.test:run", "location": "tests/a.test.ts:11"}]
        assert [c["symbol"] for c in queries.callers(build(rust), "tests/a.test:parses a::b")["callers"]] == []


def test_in_a_graph_with_rust_a_rust_member_path_is_read_and_the_typescript_title_is_not():
    graph = build(True)
    assert queries.find_symbol(graph, "src/lib:Foo::new") == {"symbol": "src/lib:Foo::new"}
    assert queries.find_symbol(graph, "Foo::new") == {"symbol": "src/lib:Foo::new"}
    assert queries.find_symbol(graph, "src/lib.rs:Foo::new") == {"symbol": "src/lib:Foo::new"}
