"""Queries over Rust ids: `src/x:Foo::new`, `Foo::new`, a bare `new`, a file with or without `.rs`.

A Rust member is joined by `::`, so a colon is half of a `::` as often as it ends the path. The first group of tests
builds a graph by hand (no parser needed); the second reads the golden fixture (needs tree-sitter-rust).
"""

import pytest

from ripple import queries
from ripple.model import Edge, Graph, Symbol, Unresolved


def symbol(sid, kind="method", file=None, line=1):
    file = file or (sid.split(":")[0] + (".rs" if "/mod" not in sid else ""))
    return Symbol(sid, kind, file, line, line + 2)


def small_graph():
    symbols = [
        symbol("src/x", "module", "src/x/mod.rs"),
        symbol("src/lib", "module", "src/lib.rs"),
        symbol("src/x:Foo", "class", "src/x/mod.rs"),
        symbol("src/x:Foo::new", "method", "src/x/mod.rs"),
        symbol("src/x:Bar::new", "method", "src/x/mod.rs"),
        symbol("src/x:<Foo as Display>::fmt", "method", "src/x/mod.rs"),
        symbol("src/x:helper", "function", "src/x/mod.rs"),
        symbol("src/lib:tests::it_works", "function", "src/lib.rs"),
        symbol("src/lib:run", "function", "src/lib.rs"),
    ]
    edges = [Edge("src/lib:run", "src/x:Foo::new", "src/lib.rs", 5), Edge("src/lib:tests::it_works", "src/lib:run", "src/lib.rs", 9)]
    unresolved = [Unresolved("src/lib:run", "src/lib.rs", 6, "foo.new", "a method call")]
    graph = Graph(symbols={s.id: s for s in symbols}, edges=edges, unresolved=unresolved)
    graph.modules = {"src/x/mod.rs": "src/x", "src/lib.rs": "src/lib"}
    return graph


@pytest.mark.parametrize(
    "sid, file, name",
    [
        ("src/x:Foo::new", "src/x.rs", "new"),
        ("src/x:<Foo as Display>::fmt", "src/x.rs", "fmt"),
        ("src/x:<Error as From<io::Error>>::from#2", "src/x.rs", "from"),
        ("src/lib:tests::it_works", "src/lib.rs", "it_works"),
        ("src/lib:outer::inner", "src/lib.rs", "inner"),
        ("src/x:Foo", "src/x.rs", "Foo"),
        ("src/x", "src/x.rs", "x"),
        # the names of Python and TypeScript ids are what they were
        ("src/cart:Cart.add#2", "src/cart.ts", "add"),
        ("src/util/math:clamp", "src/util/math.ts", "clamp"),
        ("tests/a.test:suite > the case. with a dot", "tests/a.test.ts", "suite > the case. with a dot"),
        ("pkg.mod.Class.method", "pkg/mod.py", "method"),
    ],
)
def test_a_symbol_is_named_by_the_last_part_of_its_member(sid, file, name):
    assert Symbol(sid, "method", file, 1, 2).name == name


@pytest.mark.parametrize(
    "query, expected",
    [
        ("src/x:Foo::new", "src/x:Foo::new"),  # the id
        ("src/x/mod.rs:Foo::new", "src/x:Foo::new"),  # the file, with its extension
        ("src/x/mod:Foo::new", "src/x:Foo::new"),  # the file without it
        ("x:Foo::new", "src/x:Foo::new"),  # the tail of a path
        ("Foo::new", "src/x:Foo::new"),  # the type and the function
        ("tests::it_works", "src/lib:tests::it_works"),  # a function in an inline module
        ("it_works", "src/lib:tests::it_works"),
        ("<Foo as Display>::fmt", "src/x:<Foo as Display>::fmt"),
        ("src/lib.rs:run", "src/lib:run"),
        ("src/lib:run", "src/lib:run"),
        ("helper", "src/x:helper"),
    ],
)
def test_a_rust_symbol_is_found_by_every_way_it_is_written(query, expected):
    assert queries.find_symbol(small_graph(), query) == {"symbol": expected}


def mixed_graph(rust_ids=("src/lib:Foo::new",)):
    """A Rust crate and a TypeScript file named Foo, whose module name is the type's."""
    symbols = [symbol("src/lib", "module", "src/lib.rs"), symbol("web/Foo", "module", "web/Foo.ts"), symbol("web/Foo:make", "function", "web/Foo.ts")]
    symbols += [symbol(sid, "method", "src/lib.rs") for sid in rust_ids]
    graph = Graph(symbols={s.id: s for s in symbols})
    graph.modules = {"src/lib.rs": "src/lib", "web/Foo.ts": "web/Foo"}
    return graph


def test_a_rust_member_is_found_when_a_typescript_file_has_the_types_name():
    """`Foo::new` was read as the path Foo and the member `:new` whenever a TypeScript file was called Foo, which said
    "no symbol named" for a symbol it then listed as a suggestion."""
    assert queries.find_symbol(mixed_graph(), "Foo::new") == {"symbol": "src/lib:Foo::new"}
    assert queries.find_symbol(mixed_graph(), "src/lib:Foo::new") == {"symbol": "src/lib:Foo::new"}
    assert queries.find_symbol(mixed_graph(), "Foo:make") == {"symbol": "web/Foo:make"}  # the TypeScript reading is as it was


def test_a_rust_member_two_crates_share_is_ambiguous_beside_a_typescript_module_of_that_name():
    found = queries.find_symbol(mixed_graph(("src/lib:Foo::new", "src/other:Foo::new")), "Foo::new")
    assert found["error"].endswith("is ambiguous; pass one of the candidates")
    assert found["candidates"] == ["src/lib:Foo::new", "src/other:Foo::new"]


def test_a_bare_function_name_two_types_share_is_ambiguous_and_lists_both():
    found = queries.find_symbol(small_graph(), "new")
    assert found["error"].endswith("is ambiguous; pass one of the candidates")
    assert found["candidates"] == ["src/x:Bar::new", "src/x:Foo::new"]


def test_a_module_is_found_by_its_file_with_or_without_the_extension():
    graph = small_graph()
    for query in ("src/x/mod.rs", "src/x/mod", "src/x"):
        assert queries.find_symbol(graph, query) == {"symbol": "src/x"}


def test_a_member_of_a_type_is_not_a_top_level_member_of_its_module():
    graph = small_graph()
    assert queries._members(graph, "src/x") == ["src/x:Foo", "src/x:helper"]
    assert queries._members(graph, "src/lib") == ["src/lib:run"]


def test_an_unknown_member_lists_what_the_module_declares():
    found = queries.find_symbol(small_graph(), "src/x:Foo::nope")
    assert found["error"] == "no symbol named 'src/x:Foo::nope'"
    assert set(found["suggestions"][:2]) == {"src/x:Foo", "src/x:helper"}  # what the module declares comes first


def test_callers_impact_and_possible_missed_callers_read_the_names_after_a_double_colon():
    graph = small_graph()
    assert [c["symbol"] for c in queries.callers(graph, "Foo::new")["callers"]] == ["src/lib:run"]
    impact = queries.impact(graph, "src/x:Foo::new")
    assert [e["symbol"] for e in impact["by_file"]["src/lib.rs"]] == ["src/lib:run", "src/lib:tests::it_works"]
    # `foo.new()` is a call ripple could not resolve whose name is `new`: a possible caller by name
    assert [m["call"] for m in impact["possible_missed_callers"]] == ["foo.new"]
    graph.unresolved.append(Unresolved("src/lib:run", "src/lib.rs", 7, "Foo::new", "a path"))
    assert [m["call"] for m in queries.impact(graph, "src/x:Foo::new")["possible_missed_callers"]] == ["foo.new", "Foo::new"]


# ---- on a graph the front end built


pytest.importorskip("tree_sitter_rust")

from conftest import FIXTURES  # noqa: E402
from ripple.indexer import build_graph  # noqa: E402
from ripple.profile import RUST  # noqa: E402


@pytest.fixture(scope="module")
def warehouse():
    return build_graph(FIXTURES / "rust" / "warehouse-rs", RUST)


def test_a_graph_the_rust_front_end_just_built_answers_callers_without_a_cache(warehouse):
    found = queries.callers(warehouse, "Cart::total")
    assert [c["symbol"] for c in found["callers"]] == [
        "app/src/cart:<Cart as Priced>::price", "app/src/cart:tests::totals", "app/src/main:main",
    ]  # fmt: skip


def test_impact_path_and_callees_work_across_crates_and_through_overrides(warehouse):
    impact = queries.impact(warehouse, "pricing-core/src/lib:quote")
    callers = {e["symbol"] for entries in impact["by_file"].values() for e in entries}
    assert {"app/src/lib:run", "app/reports/stock:main", "app/tests/common:price_it", "app/tests/flow:flow"} <= callers
    chain = queries.path(warehouse, "app/src/main:main", "pricing-core/src/rates:Rate::new")
    assert [hop["symbol"] for hop in chain["path"]] == [
        "app/src/main:main", "app/src/lib:run", "pricing-core/src/lib:quote", "pricing-core/src/rates:base_rate",
        "pricing-core/src/rates:Rate::flat", "pricing-core/src/rates:Rate::new",
    ]  # fmt: skip
    callees = queries.callees(warehouse, "app/src/lib:run")
    assert "pricing-core/src/lib:Priced::describe" in {c["symbol"] for c in callees["callees"]}
    assert {u["call"] for u in callees["unresolved"]} >= {"platform", "helper", "f.add"}


def test_a_method_call_is_a_possible_missed_caller_of_the_methods_of_that_name(warehouse):
    impact = queries.impact(warehouse, "app/src/cart:Cart::count")
    assert {m["call"] for m in impact["possible_missed_callers"]} == {"o.count", "cart.count", "Cart::default().count", "Cart::new().count"}
