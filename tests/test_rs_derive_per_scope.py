"""A standard derive's name (`Debug`, `Clone`, ...) is replaced only by what can be that name at the derive's own site.

rustc resolves the bare `Debug` in `#[derive(Debug)]` as a macro path in the scope of the item it is written on: that scope is the
module (its own `use`s, never a parent's or a child's) or, for an item in a function body or a nested block, the block and every
block around it up to the module. The probes under bench/fixtures/rust-probes/crates/_derive_scope are each built by rustc
(tests/test_rs_derive_scope_probes.py), and say the same thing the tests here pin:

  * a `use` in another module, another function body or a sibling block is not seen from the derive (std's derive runs);
  * a `use` in the module, or in the block or any block around it, is (the other derive runs);
  * a glob (`use other::*;`, `use genmacros::*;`), or a `use` a macro expanded, that gives another `Debug` beside the prelude's
    is rustc's error E0659, so no crate that builds has the glob or the expansion be what the name is;
  * `#[macro_use] extern crate` and a macro of the repo of that name are not scoped by module: they replace the name everywhere.

Narrowing is a recall gain only where the use cannot be seen. Whatever ripple cannot place stays marked, as it was per crate.
"""

import pytest

pytest.importorskip("tree_sitter_rust")

from test_rs_derive_scope import CALL, EDGE, MAKE, OTHER as OTHER_DEP, PKG, calls, derive_call, marked  # noqa: E402

from ripple import rs_indexer as rs  # noqa: E402
from ripple.indexer import walk_languages  # noqa: E402
from ripple.profile import RUST  # noqa: E402

OTHER = "mod other {\n    pub fn one() {}\n}\n"
ONE = "src/lib:other::one"


def edge(caller):
    return {(caller, "edge", ONE)}


def make(caller):
    return {(caller, "unresolved", rs.MACRO_MAY_MAKE)}


def in_module(outside="", inside=""):
    """The derive written in `mod c`, which globs `other` and calls `one()` from `c::f`."""
    return OTHER + outside + "mod c {\n    use crate::other::*;\n" + inside + "    #[derive(Debug)]\n    pub struct S;\n    pub fn f() {\n        one();\n    }\n}\n"


def in_fn(outside="", before="", after=""):
    """The derive written in the body of `f`, which globs `other` and calls `one()` there."""
    return OTHER + outside + "fn f() {\n" + before + "    use crate::other::*;\n    #[derive(Debug)]\n    struct S;\n" + after + "    one();\n}\n"


USE = "use fake::Debug;\n"
IN_MOD = "mod sib {\n    use fake::Debug;\n}\n"
IN_FN = "fn elsewhere() {\n    use fake::Debug;\n}\n"


@pytest.mark.parametrize(
    "src, caller",
    [
        pytest.param(in_module(IN_MOD), "src/lib:c::f", id="a sibling module's use"),
        pytest.param(in_module(USE), "src/lib:c::f", id="the parent module's use, which a child module does not see"),
        pytest.param(in_module(IN_FN), "src/lib:c::f", id="a function body's use, from a derive in a module"),
        pytest.param(in_module("mod sib {\n    use derive_more::{self, Debug};\n}\n"), "src/lib:c::f", id="a braced use in a sibling"),
        pytest.param(in_fn(IN_MOD), "src/lib:f", id="a module's use, from a derive in a block of another module"),
        pytest.param(in_fn(IN_FN), "src/lib:f", id="another function's use, from a derive in a function body"),
        pytest.param(in_fn(before="    {\n        use fake::Debug;\n    }\n"), "src/lib:f", id="a use in a block beside it"),
        pytest.param(in_fn(after="    {\n        use fake::Debug;\n    }\n"), "src/lib:f", id="a use in a block inside it: the outer block does not see the inner"),
        pytest.param(in_fn(IN_MOD + IN_FN, before="    fn deeper() {\n        use fake::Debug;\n    }\n"), "src/lib:f", id="a use in a nested function: its own scope"),
        pytest.param(in_module("mod sib {\n    use fake::Clone;\n    use fake::Hash as Debug_;\n}\n"), "src/lib:c::f", id="another name, and a rename onto another name"),
    ],
)
def test_a_use_the_derive_cannot_see_leaves_the_name_the_standard_librarys(make_repo, src, caller):
    got = calls(make_repo, src)
    assert got == edge(caller), got


@pytest.mark.parametrize(
    "pre",
    [
        "mod inner {\n    use fake::Debug;\n}\n",
        "mod inner {\n    use fake::{Debug, other};\n}\n",
        "mod inner {\n    use fake::derive_more::Debug;\n}\n",
        "mod inner {\n    mod deeper {\n        use fake::Debug;\n    }\n}\n",
        "fn elsewhere() {\n    use fake::Debug;\n}\n",
        "fn elsewhere() {\n    fn deeper() {\n        use fake::Debug;\n    }\n}\n",
        "struct W;\nimpl W {\n    fn m(&self) {\n        use fake::Debug;\n    }\n}\n",
    ],
)
def test_a_derive_in_the_crate_root_does_not_see_a_use_of_another_scope(make_repo, pre):
    """The old rule marked each of these (one use anywhere in the crate); the derive in the root cannot see any of them."""
    got = derive_call(make_repo, pre)
    assert got == EDGE, got


@pytest.mark.parametrize(
    "src, caller",
    [
        pytest.param(in_module(inside=USE), "src/lib:c::f", id="the module's own use"),
        pytest.param(in_module(inside="    use fake::{self, Debug};\n"), "src/lib:c::f", id="a braced use of its own"),
        pytest.param(in_module(inside="    pub use fake::Debug;\n"), "src/lib:c::f", id="a pub use of its own"),
        pytest.param(in_module(inside="    use fake::Dbg as Debug;\n"), "src/lib:c::f", id="a rename onto the name"),
        pytest.param(in_fn(before="    use fake::Debug;\n"), "src/lib:f", id="the block's own use"),
        pytest.param(in_fn(after="    use fake::Debug;\n"), "src/lib:f", id="the block's own use after the derive (an item is visible from the whole block)"),
        pytest.param(in_fn(USE), "src/lib:f", id="the module's use, seen from a derive in a block of the module"),
        pytest.param(
            OTHER + "mod m {\n    use fake::Debug;\n    pub fn g() {\n        use crate::other::*;\n        #[derive(Debug)]\n        struct S;\n        one();\n    }\n}\n",
            "src/lib:m::g",
            id="a use of a module that is not the root, seen from a derive in a block of it",
        ),
    ],
)
def test_a_use_the_derive_can_see_replaces_the_name(make_repo, src, caller):
    got = calls(make_repo, src)
    assert got == make(caller), got


@pytest.mark.parametrize(
    "src, caller",
    [
        pytest.param(
            OTHER + "fn f() {\n    use fake::Debug;\n    {\n        use crate::other::*;\n        #[derive(Debug)]\n        struct S;\n        one();\n    }\n}\n",
            "src/lib:f",
            id="a block around it",
        ),
        pytest.param(
            OTHER + "fn f() {\n    use fake::Debug;\n    fn g() {\n        use crate::other::*;\n        #[derive(Debug)]\n        struct S;\n        one();\n    }\n}\n",
            "src/lib:f::g",
            id="a function inside the block with the use (rustc: block_inner_fn_sees_outer_block_use)",
        ),
        pytest.param(
            OTHER + "fn f() {\n    let c = || {\n        use fake::Debug;\n        use crate::other::*;\n        #[derive(Debug)]\n        struct S;\n        one();\n    };\n}\n",
            "src/lib:f",
            id="a closure's block",
        ),
        pytest.param(
            OTHER + "use fake::Debug;\nstruct W;\nimpl W {\n    fn m(&self) {\n        use crate::other::*;\n        #[derive(Debug)]\n        struct S;\n        one();\n    }\n}\n",
            "src/lib:W::m",
            id="the module's use, from a method body",
        ),
        pytest.param(
            OTHER + "struct W;\nimpl W {\n    fn m(&self) {\n        use fake::Debug;\n        use crate::other::*;\n        #[derive(Debug)]\n        struct S;\n        one();\n    }\n}\n",
            "src/lib:W::m",
            id="a method body's own use",
        ),
        pytest.param(
            OTHER + "trait T {\n    fn m(&self) {\n        use fake::Debug;\n        use crate::other::*;\n        #[derive(Debug)]\n        struct S;\n        one();\n    }\n}\n",
            "src/lib:T::m",
            id="a default method's own use",
        ),
        pytest.param(
            OTHER + "fn f() {\n    use std::fmt::Debug;\n    {\n        use fake::Debug;\n        use crate::other::*;\n        #[derive(Debug)]\n        struct S;\n        one();\n    }\n}\n",
            "src/lib:f",
            id="a proved use in the outer block does not clear an unproved one in the inner",
        ),
        pytest.param(
            OTHER + "fn f() {\n    use fake::Debug;\n    {\n        use std::fmt::Debug;\n        use crate::other::*;\n        #[derive(Debug)]\n        struct S;\n        one();\n    }\n}\n",
            "src/lib:f",
            id="nor one in the outer block clear one in the inner (the inner proved use would win in rustc: kept marked, a recall cost)",
        ),
    ],
)
def test_a_use_in_a_block_around_the_derive_replaces_the_name(make_repo, src, caller):
    got = calls(make_repo, src)
    assert make(caller) <= got, got


def test_a_use_of_the_module_in_which_a_nested_fn_sits_is_seen_by_the_derive_in_its_body(make_repo):
    src = OTHER + "mod m {\n    use fake::Debug;\n    pub fn f() {\n        fn g() {\n            use crate::other::*;\n            #[derive(Debug)]\n            struct S;\n            one();\n        }\n    }\n}\n"
    got = calls(make_repo, src)
    assert make("src/lib:m::f::g") <= got, got


@pytest.mark.parametrize(
    "outside",
    [
        "#[macro_use]\nextern crate fake;\n",
        "macro_rules! Debug {\n    () => {};\n}\n",
        "mod sib {\n    macro_rules! Debug {\n        () => {};\n    }\n}\n",
        "fn elsewhere() {\n    macro_rules! Debug {\n        () => {};\n    }\n}\n",
        "mod sib {\n    #[macro_export]\n    macro_rules! Debug {\n        () => {};\n    }\n}\n",
    ],
)
def test_a_macro_use_extern_crate_and_a_macro_of_the_repo_replace_the_name_in_every_scope(make_repo, outside):
    """Neither is scoped by module for ripple (`macro_rules!` is textual and `#[macro_export]` goes to the root), so a derive anywhere is marked."""
    assert make("src/lib:c::f") <= calls(make_repo, in_module(outside)), outside
    assert make("src/lib:f") <= calls(make_repo, in_fn(outside)), outside
    assert derive_call(make_repo, outside) == MAKE, outside


def edition_flags(make_repo, edition, src):
    from test_rs_derive_scope import PKG

    repo = make_repo({"Cargo.toml": PKG.replace("2021", edition), "src/lib.rs": src})
    files, skipped = walk_languages(repo, [RUST])
    return [dict(ctx.derive_replaced) for ctx in rs.read_repo(repo, files[RUST.name], skipped)[0].ctxs]


def test_a_2015_crate_reads_std_from_its_root_so_only_its_own_scopes_use_is_unproved(make_repo):
    """A `use std::fmt::Debug;` in a 2015 crate is never proved the standard library's (it starts at the crate root), but it
    still replaces the name only for the derives that see it: the module's own, and the blocks in it."""
    use = "use std::fmt::Debug;\n"
    own = use + "#[derive(Debug)]\nstruct S;\n"
    assert edition_flags(make_repo, "2018", own) == [{}]
    assert edition_flags(make_repo, "2015", own) == [{0: "Debug"}]
    assert edition_flags(make_repo, "2015", "mod m {\n    " + use + "}\n#[derive(Debug)]\nstruct S;\n") == [{}]
    assert edition_flags(make_repo, "2015", use + "mod m {\n    #[derive(Debug)]\n    pub struct S;\n}\n") == [{}]
    assert edition_flags(make_repo, "2015", use + "fn f() {\n    #[derive(Debug)]\n    struct S;\n}\n") == [{0: "Debug"}]
    assert edition_flags(make_repo, "2015", "fn f() {\n    " + use + "}\n#[derive(Debug)]\nstruct S;\n") == [{}]


def test_each_derive_of_a_crate_is_judged_at_its_own_site(make_repo):
    """Two derives of the same name: the one beside the use is marked, the other is not (and its glob's name is an edge)."""
    src = (
        OTHER
        + "mod seen {\n    use crate::other::*;\n    use fake::Debug;\n    #[derive(Debug)]\n    pub struct S;\n    pub fn f() {\n        one();\n    }\n}\n"
        + "mod blind {\n    use crate::other::*;\n    #[derive(Debug)]\n    pub struct S;\n    pub fn f() {\n        one();\n    }\n}\n"
    )
    got = calls(make_repo, src)
    assert got == make("src/lib:seen::f") | edge("src/lib:blind::f"), got


def test_a_name_replaced_by_one_use_leaves_the_derives_of_other_names_there_alone(make_repo):
    src = in_module(inside="    use fake::Clone;\n")
    assert calls(make_repo, src) == edge("src/lib:c::f")
    assert calls(make_repo, src.replace("derive(Debug)", "derive(Clone)")) == make("src/lib:c::f")


@pytest.mark.parametrize("derive", ["Debug, Clone", "Clone, Debug"])
def test_one_name_of_several_in_a_derive_that_the_scope_replaces_marks_it(make_repo, derive):
    src = in_module(inside="    use fake::Clone;\n").replace("derive(Debug)", f"derive({derive})")
    assert calls(make_repo, src) == make("src/lib:c::f")


# ---- the settle loop: a scope a derive marks is one where `std` may be a name a macro made, so a use of std there is no longer proved


def test_a_use_of_std_in_a_scope_a_derive_marked_no_longer_proves_the_name_for_the_blocks_in_it(make_repo):
    """`mod m` is marked by the first step (its `Clone` is replaced by `use fake::Clone;`), so its `use std::fmt::Debug;` is not
    proved the standard library's in the second step, which marks the derive in the block of `m::f` that sees it."""
    body = "    use fake::Clone;\n    use std::fmt::Debug;\n    #[derive(Clone)]\n    pub struct A;\n    pub fn f() {\n        use crate::other::*;\n        #[derive(Debug)]\n        struct B;\n        one();\n    }\n"
    got = calls(make_repo, OTHER + "mod m {\n" + body + "}\n")
    assert got == make("src/lib:m::f"), got
    # the control: the same block under a module whose `Clone` nothing replaces, so its std use is proved and the name is std's
    control = OTHER + "mod m {\n" + body.replace("    use fake::Clone;\n", "") + "}\n"
    assert calls(make_repo, control) == edge("src/lib:m::f")


def test_the_chain_through_two_marked_scopes_settles(make_repo):
    """Three steps: `m` marks itself (its `Clone` is replaced), which unproves its `use std::fmt::Debug;` for the block of `m::f`
    (second step), which unproves that block's `use std::cmp::Eq;` for the block inside it, and the derive there is marked (third step)."""
    src = (
        OTHER
        + "mod m {\n    use fake::Clone;\n    use std::fmt::Debug;\n    #[derive(Clone)]\n    pub struct A;\n"
        + "    pub fn f() {\n        use std::cmp::Eq;\n        #[derive(Debug)]\n        struct B;\n"
        + "        {\n            use crate::other::*;\n            #[derive(Eq)]\n            struct C;\n            one();\n        }\n    }\n}\n"
    )
    got = calls(make_repo, src)
    assert make("src/lib:m::f") <= got, got


def test_a_chain_of_marked_modules_settles(make_repo):
    """A module that globs a module where a macro may have made names is one where `std` may be a name that glob brings, so its own
    `use std::..::Name;` is no longer proved and its derive is marked, which does the same for the module that globs it: one step
    each, however many times the settle loop is entered. The control has no `use fake::Clone;` to start the chain."""
    steps = [("cmp", "Eq"), ("hash", "Hash"), ("cmp", "Ord"), ("default", "Default"), ("cmp", "PartialEq"), ("fmt", "Debug")]
    src = "mod m0 {\n    use fake::Clone;\n    #[derive(Clone)]\n    pub struct A;\n}\n"
    for i, (home, name) in enumerate(steps, 1):
        src += f"mod m{i} {{\n    use crate::m{i - 1}::*;\n    use std::{home}::{name};\n    #[derive({name})]\n    pub struct B;\n}}\n"
    assert marked(make_repo, src) == {0: "Clone", **{i: name for i, (_, name) in enumerate(steps, 1)}}
    assert marked(make_repo, src.replace("    use fake::Clone;\n", "").replace("derive(Clone)", "derive(Copy)")) == {}


# ---- `#[prelude_import]`: the crate's own prelude, which is not std's, and which no scope's `use` shows


PRELUDE = "#![feature(prelude_import)]\nmod mypre {\n    pub use ::std::prelude::rust_2021::*;\n    pub use ::fake::Debug;\n}\n#[prelude_import]\nuse mypre::*;\n"


@pytest.mark.parametrize(
    "prelude",
    [
        PRELUDE,
        PRELUDE.replace("#[prelude_import]", '#[cfg_attr(feature = "x", prelude_import)]'),
        PRELUDE.replace("#[prelude_import]\nuse mypre::*;\n", "fn elsewhere() {\n    #[prelude_import]\n    use crate::mypre::*;\n}\n"),
    ],
    ids=["prelude_import", "cfg_attr", "written in a block"],
)
def test_a_crate_with_its_own_prelude_import_replaces_the_name_at_every_derive(make_repo, prelude):
    """`#[prelude_import] use mypre::*;` replaces the standard prelude for the whole crate (built with RUSTC_BOOTSTRAP: the probe
    prelude_import_child, whose bare `Debug` in a child module is genmacros'), and the `use` that gives the name is in `mypre`,
    on no derive's chain. So every bare standard derive of the crate is marked, as with a `#[macro_use] extern crate`."""
    assert make("src/lib:c::f") <= calls(make_repo, prelude + in_module()), prelude
    assert make("src/lib:f") <= calls(make_repo, prelude + in_fn()), prelude
    assert marked(make_repo, prelude + "mod d {\n    #[derive(Clone)]\n    pub struct S;\n}\n") == {0: "Clone"}


GATE = "#![feature(prelude_import)]\n"
MYPRE = "mod mypre {\n    pub use ::std::prelude::rust_2021::*;\n    pub use ::fake::Debug;\n}\n"
IMPORT = "#[prelude_import]\nuse mypre::*;\n"


def lib_calls(make_repo, files):
    from ripple.indexer import build_graph

    g = build_graph(make_repo({"Cargo.toml": PKG + OTHER_DEP, **files}), RUST)
    return {(e.caller, "edge", e.callee) for e in g.edges if e.kind == "call"} | {(u.caller, "unresolved", u.reason) for u in g.unresolved}


@pytest.mark.parametrize(
    "files",
    [
        pytest.param({"src/lib.rs": GATE + MYPRE + "macro_rules! setup {\n    () => {\n        " + IMPORT.replace("\n", " ") + "\n    };\n}\nsetup!();\n" + in_module()}, id="the attribute written in a macro_rules body"),
        pytest.param({"src/lib.rs": GATE + MYPRE + 'include!("pre.in");\n' + in_module(), "src/pre.in": IMPORT}, id="the attribute in an include!d file"),
        pytest.param({"src/lib.rs": GATE + MYPRE + "pub fn hidden() {\n    mod m {\n        #[prelude_import]\n        use crate::mypre::*;\n    }\n}\n" + in_module()}, id="the attribute in a mod written in a block"),
    ],
)
def test_a_prelude_import_ripple_does_not_read_still_marks_the_derive(make_repo, files):
    """Built with RUSTC_BOOTSTRAP (probes prelude_import_macro, _include, _blockmod): the derive in the sibling module is genmacros' in each.
    ripple sees the attribute only on an item it parses, so the flag is the gate: `#![feature(prelude_import)]` in the crate root,
    without which each is E0658 (`the prelude_import attribute is for use by rustc only`)."""
    assert make("src/lib:c::f") <= lib_calls(make_repo, files), files


@pytest.mark.parametrize(
    "gate",
    [
        "#![feature(prelude_import)]\n",
        "#![feature(let_chains, prelude_import)]\n",
        "#![feature(let_chains, prelude_import,)]\n",
        "#![feature(\n    let_chains,\n    // a comment\n    prelude_import,\n)]\n",
        '#![cfg_attr(feature = "x", feature(prelude_import))]\n',
        "#![cfg_attr(all(), cfg_attr(all(), feature(let_chains, prelude_import)))]\n",
        "#![cfg_attr(all(), allow(unused), feature(prelude_import))]\n",
    ],
)
def test_the_feature_gate_of_the_crate_root_marks_every_bare_derive_whatever_its_spelling(make_repo, gate):
    """The gate alone, with no `#[prelude_import]` anywhere ripple could read: the crate may still use one it cannot see."""
    assert make("src/lib:c::f") <= lib_calls(make_repo, {"src/lib.rs": gate + in_module()}), gate


def test_another_feature_does_not_mark_and_neither_does_a_crate_without_the_gate(make_repo):
    for gate in ("#![feature(let_chains)]\n", "#![feature(let_chains, never_type)]\n", "#![cfg_attr(all(), feature(let_chains))]\n", ""):
        assert lib_calls(make_repo, {"src/lib.rs": gate + in_module()}) == edge("src/lib:c::f"), gate


def test_a_root_ripple_cannot_parse_marks_when_it_may_hold_the_gate(make_repo):
    """tree-sitter still reads the module around a syntax error, so the gate is searched for in the text of the root, not in its tree."""
    src = GATE + "mod d {\n    #[derive(Clone)]\n    pub struct S;\n}\nfn broken( {\n"
    assert marked(make_repo, src) == {0: "Clone"}
    assert marked(make_repo, src.replace(GATE, "")) == {}


def test_a_gate_in_the_root_marks_the_derives_of_a_module_file_too(make_repo):
    files = {"src/lib.rs": GATE + "mod d;\n", "src/d.rs": "#[derive(Clone)]\npub struct S;\n"}
    from ripple.indexer import walk_languages as walk

    repo = make_repo({"Cargo.toml": PKG + OTHER_DEP, **files})
    found, skipped = walk(repo, [RUST])
    [ctx] = rs.read_repo(repo, found[RUST.name], skipped)[0].ctxs
    assert ctx.derive_replaced == {0: "Clone"}


def test_a_crate_without_it_reads_the_same_source_as_before(make_repo):
    """The control: the module of the probe, with the attribute and the gate removed, is a plain module and its derive is std's."""
    plain = PRELUDE.replace("#[prelude_import]\n", "").replace(GATE, "")
    assert calls(make_repo, plain + in_module()) == edge("src/lib:c::f")
