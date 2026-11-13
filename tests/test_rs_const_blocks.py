"""The initializer of an associated const is a body like a function's: a block in it is a scope of its own (a name the block declares, imports or
may have a macro make comes before a glob's), and what it declares is a symbol. tests/test_rs_const_blocks_probes.py has the compiler-built
claims; these are the forms around them.
"""

import pytest

pytest.importorskip("tree_sitter_rust")

from ripple import rs_indexer as rs  # noqa: E402
from ripple.indexer import build_graph  # noqa: E402
from ripple.profile import RUST  # noqa: E402

PKG = '[package]\nname = "app"\nversion = "0.1.0"\nedition = "2021"\n'
HEAD = "mod other {\n    pub fn one() -> u32 { 1 }\n    pub fn two() -> u32 { 2 }\n}\nuse other::*;\npub struct W;\n"


def graph(make_repo, src):
    return build_graph(make_repo({"Cargo.toml": PKG, "src/lib.rs": src}), RUST)


def calls(g):
    return {(e.caller, e.callee) for e in g.edges if e.kind == "call"}


def test_a_function_of_the_initializer_block_is_what_the_call_names_and_a_symbol(make_repo):
    g = graph(make_repo, HEAD + "impl W {\n    pub const F: fn() -> u32 = {\n        fn one() -> u32 { 3 }\n        || one()\n    };\n}\n")
    assert calls(g) == {("src/lib", "src/lib:W::F::one")}
    assert "src/lib:W::F::one" in g.symbols


def test_a_use_in_the_initializer_block_gives_the_name_there_only(make_repo):
    src = HEAD + "impl W {\n    pub const F: fn() -> u32 = {\n        use other::two as one;\n        || one()\n    };\n    pub const G: fn() -> u32 = || one();\n}\n"
    assert calls(graph(make_repo, src)) == {("src/lib", "src/lib:other::two"), ("src/lib", "src/lib:other::one")}


def test_each_const_has_its_own_block_scope(make_repo):
    src = HEAD + "impl W {\n    pub const F: fn() -> u32 = {\n        fn one() -> u32 { 3 }\n        || one()\n    };\n    pub const G: fn() -> u32 = || one();\n}\n"
    got = {(e.callee, e.line) for e in graph(make_repo, src).edges if e.kind == "call"}
    assert got == {("src/lib:W::F::one", 10), ("src/lib:other::one", 12)}, got  # G's call sees no block of F's


def test_a_macro_in_the_initializer_block_may_make_the_name(make_repo):
    src = HEAD + "macro_rules! m {\n    () => {};\n}\nimpl W {\n    pub const F: fn() -> u32 = {\n        m!();\n        || one()\n    };\n}\n"
    g = graph(make_repo, src)
    assert calls(g) == set()
    assert [u.reason for u in g.unresolved] == [rs.MACRO_MAY_MAKE]


def test_the_default_of_a_trait_const_and_the_const_of_a_trait_impl_have_their_blocks_too(make_repo):
    src = HEAD + "pub trait T {\n    const D: fn() -> u32 = {\n        fn one() -> u32 { 3 }\n        || one()\n    };\n    const E: fn() -> u32;\n}\nimpl T for W {\n    const E: fn() -> u32 = {\n        fn one() -> u32 { 4 }\n        || one()\n    };\n}\n"
    got = calls(graph(make_repo, src))
    assert got == {("src/lib", "src/lib:T::D::one"), ("src/lib", "src/lib:<W as T>::E::one")}, got


def test_an_impl_written_in_a_function_body_has_them_too(make_repo):
    src = HEAD + "pub fn make() -> u32 {\n    struct V;\n    impl V {\n        const F: fn() -> u32 = {\n            fn one() -> u32 { 3 }\n            || one()\n        };\n    }\n    (V::F)()\n}\n"
    assert {e.callee for e in graph(make_repo, src).edges if e.kind == "call"} == {"src/lib:make::V::F::one"}


def test_a_const_without_a_block_reads_as_before(make_repo):
    src = HEAD + "impl W {\n    pub const F: u32 = one();\n}\n"
    assert calls(graph(make_repo, src)) == {("src/lib", "src/lib:other::one")}
