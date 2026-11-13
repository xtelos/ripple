"""`#[test]`, `#[bench]`, `#[derive]` and `#[global_allocator]` are the standard library's attribute macros, not attributes of the
compiler, so a `use` of another macro by one of those names makes the attribute that macro, which may rewrite the item.

Which forms do, was built with local proc-macro crates (rustc 1.99): an explicit `use` (in a module or in a block), and a
`#[macro_use] extern crate`. A glob import that brings the name in is ambiguous with the prelude's macro, which rustc rejects,
and so is a macro that makes a `use`; a `use` of another module does not reach this one; every other name of ripple's list of
built-in attributes (`inline`, `allow`, `repr`, ...) is ambiguous with a macro of that name, which rustc rejects too.

So a site is read as an attribute macro (its body and name unresolved, its scope possibly holding names it made) exactly where
one of those forms can be, and as the built-in everywhere else.
"""

import json

import pytest

pytest.importorskip("tree_sitter_rust")

from ripple import rs_indexer as rs  # noqa: E402
from ripple.indexer import build_graph  # noqa: E402
from ripple.profile import RUST  # noqa: E402

PKG = '[package]\nname = "app"\nversion = "0.1.0"\nedition = "2021"\n'
OTHER = '[dependencies]\nother = "1"\n'
SERIAL_TEST = '[dev-dependencies]\nserial_test = "2"\n'
TEST = "#[test]\nfn t() {\n    one();\n}\nfn one() {}\n"


def calls(make_repo, src, deps=OTHER):
    """What the call of `one` or `helper` in the file came to: ("edge", callee) or ("unresolved", reason)."""
    g = build_graph(make_repo({"Cargo.toml": PKG + deps, "src/lib.rs": src}), RUST)
    out = {(e.caller, "edge", e.callee) for e in g.edges if e.kind == "call"}
    out |= {(u.caller, "unresolved", u.reason) for u in g.unresolved}
    return out


EDGE = {("src/lib:t", "edge", "src/lib:one")}
REWRITTEN = {("src/lib:t", "unresolved", rs.ATTR_MACRO)}


def test_a_test_with_nothing_else_in_scope_is_the_built_in(make_repo):
    assert calls(make_repo, TEST) == EDGE


@pytest.mark.parametrize(
    "src",
    [
        "use other::test;\n" + TEST,
        "use other::{rewrite as test};\n" + TEST,
        "use other::rewrite as test;\n" + TEST,
        "pub(crate) use other::test;\n" + TEST,
        "#[cfg(feature = \"x\")]\nuse other::test;\n" + TEST,
        "#[macro_use]\nextern crate other;\n" + TEST,
        "#[macro_use]\nextern crate other;\nmod m {\n    #[test]\n    fn t() {\n        super::one();\n    }\n}\nfn one() {}\n",
        "fn outer() {\n    use other::test;\n    #[test]\n    fn t() {\n        one();\n    }\n}\nfn one() {}\n",
        "mod m {\n    use other::test;\n    #[test]\n    fn t() {\n        super::one();\n    }\n}\nfn one() {}\n",
    ],
)
def test_a_test_under_a_use_or_a_macro_use_extern_crate_of_that_name_is_an_attribute_macro(make_repo, src):
    got = calls(make_repo, src)
    assert not any(kind == "edge" for _, kind, _ in got), got
    assert got and all(reason == rs.ATTR_MACRO for _, _, reason in got), got


@pytest.mark.parametrize(
    "src",
    [
        "use other::rewrite;\n" + TEST,  # a use of another name
        "use other::test as t2;\n" + TEST,
        "use std::collections::*;\n" + TEST,
        "mod m {\n    use other::test;\n}\n" + TEST,  # another module's use does not reach this one
        "use other::test;\nmod m {\n    #[test]\n    fn t() {\n        super::one();\n    }\n}\nfn one() {}\n",  # nor does the parent's reach a child
        "fn outer() {\n    use other::test;\n}\n" + TEST,  # nor does a block's reach the module
        "macro_rules! other { () => {} }\n" + TEST,  # a macro of another name
        "#[macro_use]\nextern crate std;\n" + TEST,
    ],
)
def test_a_test_where_no_form_can_make_it_another_macro_is_the_built_in(make_repo, src):
    got = calls(make_repo, src)
    assert got and all(kind == "edge" and callee == "src/lib:one" for _, kind, callee in got), got


def test_the_macro_of_the_repo_called_test_may_be_the_attribute(make_repo):
    """`macro_rules!` attributes are unstable, so no stable program has one: refused all the same, as the form that could."""
    got = calls(make_repo, "macro_rules! test { () => {} }\n" + TEST)
    assert got == REWRITTEN


def test_a_bench_is_read_as_a_test_is(make_repo):
    src = "#[bench]\nfn t() {\n    one();\n}\nfn one() {}\n"
    assert calls(make_repo, src) == EDGE
    assert calls(make_repo, "use other::bench;\n" + src) == REWRITTEN


def test_a_use_of_one_name_does_not_make_the_other_one_a_macro(make_repo):
    assert calls(make_repo, "use other::bench;\n" + TEST) == EDGE


def test_the_name_a_shadowed_test_declares_is_unresolved_too(make_repo):
    src = "use other::test;\n#[test]\nfn t() {}\nfn run() {\n    t();\n}\n"
    got = calls(make_repo, src)
    assert ("src/lib:run", "unresolved", rs.ATTR_MACRO) in got and not any(k == "edge" for _, k, _ in got), got


@pytest.mark.parametrize(
    "attribute", ["#[cfg_attr(all(), test)]", "#[cfg_attr(all(), test, inline)]", "#[cfg_attr(all(), inline, test)]", "#[cfg_attr(unix, cfg_attr(all(), test))]"]
)
def test_a_shadowed_test_written_in_a_cfg_attr_on_a_method_is_an_attribute_macro(make_repo, attribute):
    """The review's cfgattr_method (built: `use fake::test;` then this method gives 2, the macro's `two()`). A method of an impl
    cannot carry the built-in `#[test]`, so what is written there is read as a macro: the cfg_attr must pass that reading on."""
    src = f"use other::test;\nfn one() {{}}\nstruct S;\nimpl S {{\n    {attribute}\n    fn m() {{\n        one();\n    }}\n}}\n"
    got = calls(make_repo, src)
    assert got and not any(k == "edge" for _, k, _ in got), got


def test_a_test_written_in_a_cfg_attr_on_a_method_is_read_as_a_macro_even_where_nothing_is_imported(make_repo):
    """An impl method cannot be a `#[test]` of the compiler: so this is no program that builds without a use of another `test`."""
    src = "fn one() {}\nstruct S;\nimpl S {\n    #[cfg_attr(all(), test)]\n    fn m() {\n        one();\n    }\n}\n"
    got = calls(make_repo, src)
    assert got and not any(k == "edge" for _, k, _ in got), got


def test_a_shadowed_test_may_have_generated_names_beside_it(make_repo):
    src = "use other::test;\n#[test]\nfn t() {}\nfn run() {\n    helper();\n}\nmod m {\n    pub fn helper() {}\n}\nuse m::*;\n"
    got = calls(make_repo, src)
    assert not any(k == "edge" for _, k, _ in got), got


def test_a_shadowed_test_in_an_item_that_is_rewritten_is_no_new_reason(make_repo):
    """Inside an item an attribute macro may have rewritten, every name is already unresolved: no second reason is added."""
    src = "use other::test;\n#[other::trace]\nfn outer() {\n    #[test]\n    fn t() {\n        one();\n    }\n}\nfn one() {}\n"
    assert calls(make_repo, src) == {("src/lib:outer::t", "unresolved", rs.ATTR_MACRO)}


# ---- the same for a derive and an allocator

DERIVE = "#[derive(Clone)]\nstruct S;\nimpl S {\n    fn make() {}\n}\nfn run() {\n    S::make();\n}\n"


def test_a_derive_is_the_built_in_with_nothing_else_in_scope(make_repo):
    assert calls(make_repo, DERIVE) == {("src/lib:run", "edge", "src/lib:S::make")}


def test_a_derive_under_a_use_of_that_name_is_an_attribute_macro_that_may_rewrite_the_struct(make_repo):
    got = calls(make_repo, "use other::derive;\n" + DERIVE)
    assert not any(k == "edge" for _, k, _ in got), got
    assert ("src/lib:run", "unresolved", rs.ATTR_MACRO) in got, got


def test_a_derive_under_a_macro_use_extern_crate_is_one_too(make_repo):
    got = calls(make_repo, "#[macro_use]\nextern crate other;\n" + DERIVE)
    assert not any(k == "edge" for _, k, _ in got), got


def test_a_derive_of_a_block_is_decided_by_the_block_and_the_module(make_repo):
    src = "fn outer() {\n    use other::derive;\n    #[derive(Clone)]\n    struct S;\n    impl S {\n        fn make() {}\n    }\n    S::make();\n}\n"
    got = calls(make_repo, src)
    assert not any(k == "edge" for _, k, _ in got), got


def test_a_global_allocator_static_is_decided_the_same_way(make_repo):
    src = "struct A;\nfn make() -> A { A }\n#[global_allocator]\nstatic G: A = make();\n"
    assert any(kind == "edge" for _, kind, _ in calls(make_repo, src))
    got = calls(make_repo, "use other::global_allocator;\n" + src)
    assert not any(kind == "edge" for _, kind, _ in got), got


# ---- what it does to a trusted attribute beside it

HELPER = "pub fn helper() {}\n"
SERIAL = "#[test]\n#[serial]\nfn t() {\n    helper();\n}\n"


def serial_calls(make_repo, src):
    return calls(make_repo, src, OTHER + SERIAL_TEST)


def test_serial_beside_a_test_is_trusted_when_nothing_makes_test_another_macro(make_repo):
    got = serial_calls(make_repo, "use serial_test::serial;\n" + SERIAL + HELPER)
    assert got == {("src/lib:t", "edge", "src/lib:helper")}


@pytest.mark.parametrize(
    "pre",
    [
        "use other::test;\nuse serial_test::serial;\n",
        "use serial_test::serial;\nuse other::test;\n",
        "#[macro_use]\nextern crate other;\nuse serial_test::serial;\n",
    ],
)
def test_serial_beside_a_test_that_another_macro_may_be_is_not_trusted(make_repo, pre):
    got = serial_calls(make_repo, pre + SERIAL + HELPER)
    assert got == {("src/lib:t", "unresolved", rs.ATTR_MACRO)}


def test_serial_beside_a_test_in_a_block_that_uses_another_macro_called_test(make_repo):
    src = "use serial_test::serial;\npub fn outer() {\n    use other::test;\n    #[test]\n    #[serial]\n    fn t() {\n        helper();\n    }\n    t();\n}\n" + HELPER
    got = serial_calls(make_repo, src)
    assert not any(k == "edge" for _, k, _ in got), got


def test_a_trusted_tokio_test_beside_a_shadowed_test_is_not_trusted(make_repo):
    """`#[test]` beside `#[tokio::test]` is a mistake of its own, but a macro that is another `test` could be anywhere."""
    src = "use other::test;\n#[test]\n#[tokio::test]\nasync fn t() {\n    helper();\n}\n" + HELPER
    got = calls(make_repo, src, OTHER + '[dev-dependencies]\ntokio = "1"\n')
    assert not any(k == "edge" for _, k, _ in got), got


# ---- where the use is, and where the attribute is

def test_a_use_of_the_module_reaches_an_item_in_a_block_of_it(make_repo):
    src = "use other::test;\nfn outer() {\n    #[test]\n    fn t() {\n        one();\n    }\n}\nfn one() {}\n"
    assert calls(make_repo, src) == {("src/lib:outer::t", "unresolved", rs.ATTR_MACRO)}


@pytest.mark.parametrize(
    "src, caller",
    [
        ("struct S;\nimpl S {\n    #[test]\n    fn m() {\n        one();\n    }\n}\nfn one() {}\n", "src/lib:S::m"),
        ("trait T {\n    #[bench]\n    fn m() {\n        one();\n    }\n}\nfn one() {}\n", "src/lib:T::m"),
        ("struct S;\nimpl S {\n    #[test]\n    fn m() {}\n    fn n() {\n        Self::m();\n    }\n}\n", "src/lib:S::n"),
    ],
)
def test_the_built_in_cannot_be_written_on_a_method_so_what_is_there_is_another_macro(make_repo, src, caller):
    """`#[test]` on a method of an impl or a trait is an error: it compiles only if the name is another macro, so it is read as one,
    whatever is in scope."""
    got = calls(make_repo, src)
    assert not any(k == "edge" for _, k, _ in got), got
    assert any(c == caller and k == "unresolved" and r == rs.ATTR_MACRO for c, k, r in got), got


# ---- a shadowed attribute inside what a macro rewrites says nothing more

def test_a_shadowed_test_inside_a_rewritten_item_changes_nothing_a_twin_without_the_use_would_not(make_repo, tmp_path_factory):
    body = "#[other::trace]\nfn outer() {\n    #[test]\n    fn t() {\n        one();\n    }\n}\nfn one() {}\ntrait Conv<T> {\n    fn f();\n}\nstruct S;\nimpl Conv<u8> for S {\n    fn f() {}\n}\nfn run() {\n    <S as Conv<u8>>::f();\n}\n"
    dumps = []
    for n, use in enumerate(("use other::test;\n", "use other::zzz;\n")):
        root = tmp_path_factory.mktemp(f"rewritten{n}")
        (root / "src").mkdir()
        (root / "Cargo.toml").write_text(PKG + OTHER)
        (root / "src/lib.rs").write_text(use + body)
        dumps.append(json.dumps(build_graph(root, RUST).to_dict(), sort_keys=True))
    assert dumps[0] == dumps[1]


def test_a_shadowed_test_inside_a_module_a_macro_rewrites_changes_nothing_a_twin_without_the_use_would_not(make_repo, tmp_path_factory):
    body = "#[other::trace]\nmod m {\n    #[test]\n    pub fn t() {\n        super::one();\n    }\n}\nfn one() {}\ntrait Conv<T> {\n    fn f();\n}\nstruct S;\nimpl Conv<u8> for S {\n    fn f() {}\n}\nfn run() {\n    <S as Conv<u8>>::f();\n}\n"
    dumps = []
    for n, use in enumerate(("use other::test;\n", "use other::zzz;\n")):
        root = tmp_path_factory.mktemp(f"module{n}")
        (root / "src").mkdir()
        (root / "Cargo.toml").write_text(PKG + OTHER)
        (root / "src/lib.rs").write_text(use + body)
        dumps.append(json.dumps(build_graph(root, RUST).to_dict(), sort_keys=True))
    assert dumps[0] == dumps[1]


def test_a_shadowed_test_inside_a_refused_trusted_site_changes_nothing_a_twin_without_the_use_would_not(make_repo, tmp_path_factory):
    deps = '[dependencies]\ntokio = { path = "t" }\nother = "1"\n'
    body = "#[tokio::main]\nasync fn main() {\n    #[test]\n    fn t() {\n        one();\n    }\n}\nfn one() {}\ntrait Conv<T> {\n    fn f();\n}\nstruct S;\nimpl Conv<u8> for S {\n    fn f() {}\n}\nfn run() {\n    <S as Conv<u8>>::f();\n}\n"
    dumps = []
    for n, use in enumerate(("use other::test;\n", "use other::zzz;\n")):
        root = tmp_path_factory.mktemp(f"twin{n}")
        (root / "src").mkdir()
        (root / "Cargo.toml").write_text(PKG + deps)
        (root / "src/lib.rs").write_text(use + body)
        dumps.append(json.dumps(build_graph(root, RUST).to_dict(), sort_keys=True))
    assert dumps[0] == dumps[1]
