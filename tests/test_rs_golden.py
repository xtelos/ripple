"""The Rust golden fixture (bench/fixtures/rust/warehouse-rs) against hand-written truth.

The fixture is a cargo workspace that compiles (`cargo check --workspace --all-targets`), with a package whose lib and
bin share a name, a renamed dependency, a hyphenated one, a `[lib] name`, a `[[bin]] path`, `#[path]`, inline modules,
mod.rs, cfg twins, use lists, aliases, globs and re-exports, a file two test crates share, and a user macro. Every call
in it is listed below: the edges ripple must make (and no others), and the calls it must leave unresolved, each with
the reason's marker. The edges were also compared with rust-analyzer's resolution of the same file (every one agrees).
"""

import pytest

pytest.importorskip("tree_sitter_rust")

from conftest import FIXTURES  # noqa: E402
from ripple import rs_indexer as rs  # noqa: E402
from ripple.indexer import build_graph  # noqa: E402
from ripple.profile import RUST  # noqa: E402

WAREHOUSE = FIXTURES / "rust" / "warehouse-rs"
LIB = "app/src/lib"
CORE = "pricing-core/src/lib"


@pytest.fixture(scope="module")
def graph():
    return build_graph(WAREHOUSE, RUST)


# (caller, callee, line): one per call site, in the order the files have them
CALLS = [
    ("app/build:main", "app/build:generate", 3),
    ("app/reports/stock:main", "app/src/shipping:dispatch", 5),
    ("app/reports/stock:main", f"{CORE}:quote", 5),
    ("app/src/bin/tool:main", f"{LIB}:closures", 2),
    ("app/src/cart:Cart::new", "app/src/cart:Cart::empty", 11),  # Self::empty()
    ("app/src/cart:Cart::total", f"{LIB}:helper", 27),  # crate::helper(1)
    ("app/src/cart:<Cart as Priced>::price", "app/src/cart:Cart::total", 33),  # Self::total()
    ("app/src/cart:<Cart as Priced>::describe", f"{CORE}:Priced::label", 37),  # Self::label() inside format!
    ("app/src/cart:<Cart as Display>::fmt", "app/src/cart:<Cart as Priced>::price", 43),  # Self::price() inside write!
    ("app/src/cart:tests::totals", "app/src/cart:Cart::new", 53),
    ("app/src/cart:tests::totals", "app/src/cart:Cart::total", 53),
    (f"{LIB}:run", "tax-rules/src/lib:vat", 58),  # a use alias of a hyphenated dependency
    (f"{LIB}:run", f"{CORE}:quote", 59),  # the renamed dependency, whose package and lib are named otherwise
    (f"{LIB}:run", "pricing-core/src/discount/seasonal:holiday", 60),  # a use list
    (f"{LIB}:run", "pricing-core/src/discount:clamp", 61),  # `self` in a use list
    (f"{LIB}:run", "app/src/old/legacy_impl:old_price", 62),  # #[path]
    (f"{LIB}:run", "app/src/cart:Cart::new", 63),  # a re-exported type
    (f"{LIB}:run", f"{LIB}:<Crate as Priced>::price", 64),  # <Crate as Priced>::price()
    (f"{LIB}:run", f"{LIB}:<Crate as Priced>::price", 65),  # Crate::price(): the one impl, its trait imported
    (f"{LIB}:run", f"{CORE}:Priced::label", 66),  # a default body is the declaration
    (f"{LIB}:run", f"{LIB}:helper", 68),  # inside println!
    (f"{LIB}:run", f"{LIB}:helper", 69),  # inside vec!
    (f"{LIB}:run", "pricing-core/src/discount/seasonal:summer", 69),  # a glob re-export of the dependency
    (f"{LIB}:run", f"{LIB}:helper", 70),  # inside assert_eq!
    (f"{LIB}:run", f"{LIB}:support::fixture", 72),  # an inline module
    (f"{LIB}:run", "app/src/shipping:dispatch", 73),  # a glob of a repo module
    (f"{LIB}:run", f"{CORE}:Priced::describe", 74),  # Priced::describe(&Crate): the declaration ...
    (f"{LIB}:run", f"{CORE}:pick_max", 76),
    (f"{LIB}:run", f"{CORE}:pick_min", 76),
    (f"{LIB}:run", "app/src/shipping:Status::closed", 80),
    (f"{LIB}:run", "app/src/shipping:Status::open", 81),
    (f"{LIB}:run", f"{LIB}:generic_price", 82),
    (f"{LIB}:closures::inner", f"{LIB}:closures::helper_free", 94),  # a function inside a function
    (f"{LIB}:closures", f"{LIB}:closures::inner", 99),
    ("app/src/main:main", f"{LIB}:run", 4),  # the bin calls the lib of its own package
    ("app/src/main:main", "app/src/cart:Cart::new", 5),  # an alias in a use list
    ("app/src/main:main", "app/src/cart:Cart::total", 6),
    ("app/src/old/legacy_impl:old_price", "app/src/old/legacy_impl:scale", 2),
    ("app/src/shipping:dispatch", "app/src/shipping/rules:weight_limit", 21),  # a pub use
    ("app/src/shipping:dispatch", "app/src/shipping/rules:surcharge", 24),
    ("app/src/shipping/rules:surcharge", "app/src/shipping:dispatch", 6),  # super::
    ("app/tests/cart_flow:cart_flow", "app/tests/common:setup", 9),
    ("app/tests/common:setup", f"{LIB}:run", 3),  # the file two test crates share: both resolve it alike
    ("app/tests/common:price_it", f"{CORE}:quote", 7),
    ("app/tests/flow:flow", "app/tests/common:setup", 9),
    ("app/tests/flow:flow", "app/tests/common:price_it", 10),
    ("app/tests/flow:flow", "app/tests/common:price_it", 10),
    ("pricing-core/src/discount/seasonal:holiday", "pricing-core/src/discount:clamp", 2),  # super::
    ("pricing-core/src/discount/seasonal:summer", "pricing-core/src/discount:clamp", 6),  # crate::
    (f"{CORE}:Priced::twice", f"{CORE}:Priced::label", 19),  # Self:: in a default body, twice, inside format!
    (f"{CORE}:Priced::twice", f"{CORE}:Priced::label", 19),
    (f"{CORE}:quote", "pricing-core/src/rates:base_rate", 24),
    (f"{CORE}:quote", f"{CORE}:apply_tax", 25),
    ("pricing-core/src/rates:base_rate", "pricing-core/src/rates:Rate::flat", 6),
    ("pricing-core/src/rates:Rate::flat", "pricing-core/src/rates:Rate::new", 11),
]
# the impls of Priced::describe that `Priced::describe(&Crate)` may reach
OVERRIDES = [
    (f"{LIB}:run", "app/src/cart:<Cart as Priced>::describe", 74),
    (f"{LIB}:run", f"{LIB}:<Crate as Priced>::describe", 74),
]
# the calls ripple does not resolve, with what the reason says
REFUSED = {
    (f"{LIB}:run", "helper", 67): "inside a macro",  # doubled!(helper(4)): the user macro's expansion is not seen
    (f"{LIB}:run", "platform", 71): "more than one definition",  # cfg twins
    (f"{LIB}:generic_price", "T::price", 88): "generic parameter",
    (f"{LIB}:closures", "helper", 99): "local value",  # a closure of that name
    ("app/tests/common:setup", "crate::local_helper", 3): "several crates",  # each test crate has its own
    (f"{CORE}:pick_max", "max", 43): "glob import from outside the repo",
}
METHODS = {  # every method call, with one reason
    ("app/src/cart:Cart::add", "self.lines.push", 19), ("app/src/cart:Cart::count", "self.lines.len", 23),
    ("app/src/cart:Cart::total", "Cart::default().count", 27), ("app/src/cart:tests::totals", "Cart::new().count", 53),
    (f"{LIB}:run", "f.add", 83), (f"{LIB}:run", "items.len", 84), (f"{LIB}:run", "o.count", 84), (f"{LIB}:run", "k.len", 84),
    (f"{LIB}:run", "i.len", 84), (f"{LIB}:run", "n.len", 84), (f"{LIB}:run", "r.is_ok", 84), ("app/src/main:main", "cart.count", 6),
    ("pricing-core/src/discount:clamp", "price.min", 4),
}  # fmt: skip


def test_the_edges_are_exactly_the_calls_the_fixture_makes(graph):
    got = sorted((e.caller, e.callee, e.line) for e in graph.edges if e.kind == "call")
    assert got == sorted(CALLS)
    assert sorted((e.caller, e.callee, e.line) for e in graph.edges if e.kind == "override") == sorted(OVERRIDES)


def test_every_unresolved_call_is_listed_with_its_reason(graph):
    refused = {(u.caller, u.text, u.line): u.reason for u in graph.unresolved if u.reason != rs.METHOD}
    assert set(refused) == set(REFUSED)
    for key, marker in REFUSED.items():
        assert marker in refused[key], (key, refused[key])
    assert {(u.caller, u.text, u.line) for u in graph.unresolved if u.reason == rs.METHOD} == METHODS


def test_calls_outside_the_repo_are_counted_by_reason(graph):
    assert graph.external_reasons == {
        rs.PRELUDE: 3,  # String::from twice, Vec::new
        rs.DERIVED: 2,  # Cart::default() twice, on a type that derives Default
        rs.STD_PATH: 1,  # std::cmp::max
        rs.PRIMITIVE: 1,  # u32::from_str_radix
        rs.EXT_GLOB: 1,  # `min` from `use std::cmp::*` in a block, where nothing declares min
    }
    assert graph.external_calls == 8


def test_every_file_the_crates_reach_is_read_and_none_is_left_out(graph):
    assert graph.parse_errors == [] and len(graph.modules) == 17
    assert graph.modules["app/src/shipping/mod.rs"] == "app/src/shipping" and graph.modules["app/src/lib.rs"] == "app/src/lib"
    assert {"app/src/lib:platform", "app/src/lib:platform#2"} <= set(graph.symbols)


def test_the_cli_answers_with_a_language_flag_and_without_one(capsys):
    from ripple.cli import main

    # read from the app package alone, its path dependencies are outside the repo: ripple reads their Cargo.toml (and nothing else of them) for
    # the name of their library, so `Cart::new()` in main keeps its edge (tests/test_rs_outside_path_deps.py)
    for flags in ([], ["--lang", "rust"]):
        assert main(["callers", "src/cart:Cart::total", "--repo", str(WAREHOUSE / "app"), *flags]) == 0
        out = capsys.readouterr().out
        assert "app/src/main:main" not in out and "src/main:main" in out and "src/cart:tests::totals" in out
