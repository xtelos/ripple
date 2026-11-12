"""An id may carry the parameter list of an overload (`shop.Cart.add(Item)`), and questions read it.

These tests build graphs by hand, for a language they register themselves, so that what is pinned here is how the
model and the queries treat such ids whichever front ends exist, and that every Python and TypeScript id is read as before
(tests/test_java_languages.py reads the ids of the registered languages the same way).
"""

import re
from dataclasses import replace

import pytest

from ripple import registry
from ripple.indexer import build_graph
from ripple.model import Edge, Graph, Symbol, without_parameters
from ripple.profile import PYTHON
from ripple.queries import callers, callees, find_symbol, impact, path
from ripple.registry import Language


@pytest.fixture(autouse=True)
def a_language_whose_ids_carry_parameter_lists(monkeypatch):
    """These tests register a language that writes parameter lists in its ids and reads `.x` files, so they do not depend on
    the front end of any real one."""
    profile = replace(PYTHON, name="overloaded", source_suffixes=(".x",))
    monkeypatch.setitem(registry.LANGUAGES, "overloaded", Language(profile, "Overloaded", "ripple.indexer", parameter_ids=True))


def _symbol(sid: str, kind: str = "method", file: str = "src/shop/Cart.x") -> Symbol:
    return Symbol(sid, kind, file, 1, 2)


def _graph(ids: list[str], edges: list[tuple[str, str]] = ()) -> Graph:
    symbols = {sid: _symbol(sid, "method" if ")" in sid else "class") for sid in ids}
    return Graph(symbols=symbols, edges=[Edge(a, b, "src/shop/Cart.x", 3) for a, b in edges])


OVERLOADED = [
    "a.shop.Cart",
    "a.shop.Cart.<init>()",
    "a.shop.Cart.<init>(List)",
    "a.shop.Cart.add(Item)",
    "a.shop.Cart.add(String)",
    "a.shop.Cart.add(String,int)",
    "a.shop.Cart.total()",
    "a.shop.Cart.join(String...)",
    "a.shop.Cart.copy(int[],Map)",
    "a.shop.Cart.same(T)",
    "a.shop.Cart.same(T)#2",
]


@pytest.mark.parametrize(
    "sid, name",
    [
        ("a.shop.Cart.add(Item)", "add"),
        ("a.shop.Cart.add(String,int)", "add"),
        ("a.shop.Cart.total()", "total"),
        ("a.shop.Cart.<init>(List)", "<init>"),
        ("a.shop.Cart.join(String...)", "join"),
        ("a.shop.Cart.copy(int[],Map)", "copy"),
        ("a.shop.Cart.same(T)#2", "same"),
        ("a.shop.Cart.run(String...)$1", "run$1"),  # an anonymous class of a method with a parameter list
        ("a.shop.Cart.run(String...)$1.go()", "go"),
    ],
)
def test_the_name_of_an_id_with_a_parameter_list_is_the_name_a_call_writes(sid, name):
    assert _symbol(sid).name == name


@pytest.mark.parametrize(
    "sid, name",
    [
        ("shop.pricing.price_order", "price_order"),
        ("shop", "shop"),
        ("src/util/math:clamp", "clamp"),
        ("src/cart:Cart.add#2", "add"),
        ("src/util/math", "math"),
        ("tests/a.test:suite > case (1)", "suite > case (1)"),
        ("pkg.foo(1)", "foo(1)"),  # a file called foo(1).py: digits are no type name, so no parameter list
        ("pkg.foo(a b)", "foo(a b)"),
        ("pkg.run#1", "run#1"),  # a clash suffix alone is not an overload's
    ],
)
def test_ids_that_carry_no_parameter_list_are_named_as_they_always_were(sid, name):
    assert _symbol(sid).name == name
    assert without_parameters(sid) == sid


def test_without_parameters_takes_every_list_out_of_an_id():
    assert without_parameters("a.Cart.add(Item)#2") == "a.Cart.add"
    assert without_parameters("a.Cart.run(String...)$1.go()") == "a.Cart.run$1.go"


def test_an_overload_set_queried_without_parameter_types_lists_its_candidates():
    # The latent bug this pins: `Cart.add` used to be answered for one of the overloads.
    found = find_symbol(_graph(OVERLOADED), "Cart.add")
    assert "symbol" not in found
    assert found["error"] == "'Cart.add' is ambiguous; pass one of the candidates"
    assert found["candidates"] == ["a.shop.Cart.add(Item)", "a.shop.Cart.add(String)", "a.shop.Cart.add(String,int)"]


def test_a_bare_name_shared_by_overloads_lists_them_too():
    found = find_symbol(_graph(OVERLOADED), "add")
    assert found["candidates"] == ["a.shop.Cart.add(Item)", "a.shop.Cart.add(String)", "a.shop.Cart.add(String,int)"]


def test_the_full_id_with_its_parameter_types_names_one_overload():
    graph = _graph(OVERLOADED)
    assert find_symbol(graph, "a.shop.Cart.add(String)") == {"symbol": "a.shop.Cart.add(String)"}
    assert find_symbol(graph, "Cart.add(String,int)") == {"symbol": "a.shop.Cart.add(String,int)"}
    assert find_symbol(graph, "add(Item)") == {"symbol": "a.shop.Cart.add(Item)"}


def test_a_method_with_one_declaration_is_found_without_its_parameter_types():
    graph = _graph(OVERLOADED)
    assert find_symbol(graph, "Cart.total") == {"symbol": "a.shop.Cart.total()"}
    assert find_symbol(graph, "total") == {"symbol": "a.shop.Cart.total()"}
    assert find_symbol(graph, "Cart.join") == {"symbol": "a.shop.Cart.join(String...)"}


def test_two_overloads_written_alike_are_two_candidates_and_the_types_alone_do_not_pick_one():
    found = find_symbol(_graph(OVERLOADED), "Cart.same(T)")
    assert found["candidates"] == ["a.shop.Cart.same(T)", "a.shop.Cart.same(T)#2"]
    assert find_symbol(_graph(OVERLOADED), "a.shop.Cart.same(T)#2") == {"symbol": "a.shop.Cart.same(T)#2"}


def test_a_name_matches_only_at_a_dot():
    found = find_symbol(_graph(OVERLOADED), "dd")
    assert "symbol" not in found and "candidates" not in found


def test_callers_of_a_class_include_the_callers_of_every_constructor_overload():
    graph = _graph(
        [*OVERLOADED, "a.use.one()", "a.use.two()", "a.use.three()"],
        [("a.use.one()", "a.shop.Cart.<init>()"), ("a.use.two()", "a.shop.Cart.<init>(List)"), ("a.use.three()", "a.shop.Cart.add(Item)")],
    )
    profile = replace(PYTHON, constructor_name="<init>")  # these files read as Python: name the constructor as these ids do
    assert {c["symbol"] for c in callers(graph, "a.shop.Cart", profile)["callers"]} == {"a.use.one()", "a.use.two()"}
    assert impact(graph, "a.shop.Cart", profile=profile)["affected"] == 2
    assert path(graph, "a.use.two()", "a.shop.Cart", profile=profile)["path"][-1]["symbol"] == "a.shop.Cart.<init>(List)"


def test_callers_of_one_overload_do_not_include_its_siblings():
    graph = _graph(
        [*OVERLOADED, "a.use.one()", "a.use.two()"],
        [("a.use.one()", "a.shop.Cart.add(Item)"), ("a.use.two()", "a.shop.Cart.add(String)")],
    )
    assert {c["symbol"] for c in callers(graph, "a.shop.Cart.add(Item)")["callers"]} == {"a.use.one()"}
    assert "error" in callers(graph, "Cart.add")  # the set is a question to narrow, not one to answer for a member
    assert [c["symbol"] for c in callees(graph, "a.use.two()")["callees"]] == ["a.shop.Cart.add(String)"]
    assert path(graph, "a.use.one()", "a.shop.Cart.add(Item)")["path"][-1]["symbol"] == "a.shop.Cart.add(Item)"


# ---- nothing of Python or TypeScript moves ----------------------------------------------------------------------------


def _old_name(sid: str) -> str:
    """Symbol.name as it was written before ids could carry a parameter list."""
    if ":" not in sid:
        return sid.rsplit(".", 1)[-1] if "/" not in sid else sid.rsplit("/", 1)[-1]
    member = sid.split(":", 1)[1]
    if " > " in member:
        return member
    return re.sub(r"#\d+$", "", member).rsplit(".", 1)[-1]


def test_every_symbol_of_the_python_fixtures_is_named_as_before(shop_repo):
    from conftest import FIXTURES

    names = 0
    for repo in (shop_repo, FIXTURES / "warehouse", FIXTURES / "webapp", FIXTURES / "notes"):
        for symbol in build_graph(repo).symbols.values():
            assert symbol.name == _old_name(symbol.id), symbol.id
            names += 1
    assert names > 100


def test_every_symbol_of_the_typescript_fixture_is_named_as_before():
    pytest.importorskip("tree_sitter_typescript")
    from conftest import FIXTURES
    from ripple.indexer import build_graph as build
    from ripple.profile import TYPESCRIPT

    graph = build(FIXTURES / "ts" / "warehouse-ts", TYPESCRIPT)
    assert len(graph.symbols) > 50
    for symbol in graph.symbols.values():
        assert symbol.name == _old_name(symbol.id), symbol.id


# ---- an id that merely has parentheses ------------------------------------------------------------------------------
# These shapes read as a parameter list to anything that looks only at the id. The language of the file decides.


def test_an_id_is_read_as_having_a_parameter_list_by_the_language_of_its_file_never_by_its_look():
    assert _symbol("pkg.report(old)", file="src/pkg/report.x").name == "report"  # a language that writes parameter lists
    assert _symbol("pkg.report(old)", "module", "pkg/report(old).py").name == "report(old)"
    assert _symbol("app/(shop)", "module", "app/(shop)/index.ts").name == "(shop)"
    assert _symbol("tests/cart.test:cart > calls Cart.add(item)", "function", "tests/cart.test.ts").name == "cart > calls Cart.add(item)"
    assert _symbol("a.shop.Cart.add(Item)", file="src/Cart.py").name == "add(Item)"  # the same id in a Python file


def test_the_parameter_list_and_the_colon_member_readings_each_belong_to_their_languages_files(monkeypatch):
    colons = Language(replace(PYTHON, name="colons", source_suffixes=(".c",)), "Colons", "ripple.indexer", colon_members=True)
    monkeypatch.setitem(registry.LANGUAGES, "colons", colons)
    assert registry.writes_parameter_lists("src/Cart.x") and not registry.writes_parameter_lists("src/cart.c")
    assert registry.writes_colon_members("src/cart.c") and not registry.writes_colon_members("src/Cart.x")
    assert _symbol("src/cart:Foo::new", "function", "src/cart.c").name == "new"
    assert _symbol("src/cart:<Foo as Display>::fmt(Item)", "method", "src/cart.c").name == "fmt(Item)"  # no list in a `::` language
    assert _symbol("a.shop.Cart.add(Item)").name == "add"
    assert _symbol("a/b:Foo::new", "function", "src/Cart.x").name == "Foo::new"  # as before: only a `::` language's file splits at it


def test_a_python_file_named_with_parentheses_changes_no_answer(make_repo):
    repo = make_repo(
        {
            "pkg/report.py": "def build():\n    return 1\n\n\ndef total():\n    return build()\n",
            "pkg/report(old).py": "def build():\n    return 2\n\n\ndef legacy():\n    return build()\n",
        }
    )
    graph = build_graph(repo)
    assert graph.symbols["pkg.report(old)"].name == "report(old)"
    assert find_symbol(graph, "report.build") == {"symbol": "pkg.report.build"}
    assert find_symbol(graph, "report")["candidates"] == ["pkg.report.build", "pkg.report.total"]
    assert find_symbol(graph, "report(old)")["candidates"] == ["pkg.report(old).build", "pkg.report(old).legacy"]
    assert find_symbol(graph, "build")["candidates"] == ["pkg.report(old).build", "pkg.report.build"]
    assert find_symbol(graph, "pkg.report(old).build") == {"symbol": "pkg.report(old).build"}


def test_typescript_ids_with_parentheses_change_no_answer(make_repo):
    pytest.importorskip("tree_sitter_typescript")
    from ripple.profile import TYPESCRIPT

    repo = make_repo(
        {
            "src/cart.ts": "export class Cart {\n  add(item: string): void {}\n  total(): number {\n    return 0;\n  }\n}\n",
            "tests/cart.test.ts": 'import { Cart } from "../src/cart";\n\ndescribe("cart", () => {\n  it("calls Cart.add(item)", () => {\n    new Cart().add("x");\n  });\n});\n',
            "app/(shop)/index.ts": 'import { Cart } from "../../src/cart";\n\nexport function render(): number {\n  return new Cart().total();\n}\n',
        }
    )
    graph = build_graph(repo, TYPESCRIPT)
    assert graph.symbols["app/(shop)"].name == "(shop)"  # a route group is a module, not a parameter list
    assert graph.symbols["tests/cart.test:cart > calls Cart.add(item)"].name == "cart > calls Cart.add(item)"
    assert find_symbol(graph, "add") == {"symbol": "src/cart:Cart.add"}  # not ambiguous with the test titled for it
    assert find_symbol(graph, "Cart.add") == {"symbol": "src/cart:Cart.add"}
    assert find_symbol(graph, "render") == {"symbol": "app/(shop):render"}
