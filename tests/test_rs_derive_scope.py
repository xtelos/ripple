"""A derive can emit items beside the type it is written on (rustc builds each form below with a local proc-macro crate: the item
the derive emits beats a name a glob brings in), so a scope that holds a derive that may not be the standard library's is a scope
where a macro may have generated names, as one that holds an attribute macro is:

  * a derive that is not one of the standard library's (`#[derive(Serialize)]`, `#[derive(fake::Debug)]`);
  * a standard derive's name (`Debug`, `Clone`, ...) that something could replace: a `use` that gives the name in the derive's own
    scope (`use derive_more::Debug;` in its module, or in its block or a block around it: tests/test_rs_derive_per_scope.py), a
    `#[macro_use] extern crate`, or a macro of the repo called so (those two anywhere in the crate).

A name that nothing declares in that scope, and that a glob or the enclosing scope would bring, is then unresolved (MACRO_MAY_MAKE),
and so are the impls ripple read (IMPLS_INCOMPLETE). A `use` that gives std's own derive the name (`use std::fmt::Debug;`) does not
count as replacing it, but only when ripple proves it: the first segment is the real standard crate (no module, alias, dependency,
block item or glob of the scope can be what `std` names there), and the path is one rustc exports the derive under, not renamed.
"""

import pytest

pytest.importorskip("tree_sitter_rust")

from ripple import rs_indexer as rs  # noqa: E402
from ripple.indexer import build_graph  # noqa: E402
from ripple.profile import RUST  # noqa: E402

PKG = '[package]\nname = "app"\nversion = "0.1.0"\nedition = "2021"\n'
OTHER = '[dependencies]\nfake = "1"\n'
GLOB = "mod other {\n    pub fn one() {}\n}\nuse other::*;\n"
CALL = "fn f() {\n    one();\n}\n"
EDGE = {("src/lib:f", "edge", "src/lib:other::one")}
MAKE = {("src/lib:f", "unresolved", rs.MACRO_MAY_MAKE)}


def calls(make_repo, src, deps=OTHER):
    g = build_graph(make_repo({"Cargo.toml": PKG + deps, "src/lib.rs": src}), RUST)
    out = {(e.caller, "edge", e.callee) for e in g.edges if e.kind == "call"}
    out |= {(u.caller, "unresolved", u.reason) for u in g.unresolved}
    return out


def test_with_only_standard_derives_a_name_a_glob_brings_is_the_globs(make_repo):
    assert calls(make_repo, GLOB + "#[derive(Debug, Clone, PartialEq)]\nstruct S;\n" + CALL) == EDGE


@pytest.mark.parametrize(
    "derive",
    ["fake::Debug", "Serialize", "serde::Deserialize", "Debug, fake::Emit", "Clone, Parser", "std::fmt::Debug, Emit"],
)
def test_a_derive_that_is_not_the_standard_librarys_may_have_generated_the_name(make_repo, derive):
    """The review's derive_unknown (built: `fn one() { 2 }` emitted by the derive is what `one()` calls, over the glob's)."""
    got = calls(make_repo, GLOB + f"#[derive({derive})]\nstruct S;\n" + CALL)
    assert got == MAKE, got


@pytest.mark.parametrize(
    "pre",
    [
        "use fake::Debug;\n",
        "use fake::{Debug, other};\n",
        "use fake::derive_more::Debug;\n",
        "use fake::Dbg as Debug;\n",
        "#[macro_use]\nextern crate fake;\n",
        "macro_rules! Debug {\n    () => {};\n}\n",
    ],
)
def test_a_standard_derive_name_that_a_use_a_macro_use_extern_crate_or_a_repo_macro_could_replace_may_have_generated_the_name(make_repo, pre):
    """The review's derive_shadow (built: `use fake::Debug;` then `#[derive(Debug)]` emits `fn one() { 2 }`). A `use` in another module
    or function body no longer marks the derive of the root (tests/test_rs_derive_per_scope.py: it cannot be what the name is there)."""
    got = calls(make_repo, pre + GLOB + "#[derive(Debug)]\nstruct S;\n" + CALL)
    assert got == MAKE, got


def derive_call(make_repo, pre, derive="Debug", deps=OTHER, post=""):
    return calls(make_repo, pre + GLOB + f"#[derive({derive})]\nstruct S;\n" + CALL + post, deps)


def marked(make_repo, src, deps=OTHER):
    """The standard derives of the crate (by their order in the source) that ripple reads as replaced: {index: name}."""
    from ripple.indexer import walk_languages

    repo = make_repo({"Cargo.toml": PKG + deps, "src/lib.rs": src})
    files, skipped = walk_languages(repo, [RUST])
    [ctx] = rs.read_repo(repo, files[RUST.name], skipped)[0].ctxs
    return dict(ctx.derive_replaced)


def in_module_derive(body, outside=""):
    """A module that holds `body` (uses) and a `#[derive(Debug)]`: the derive sees what the module's own uses give."""
    return outside + "mod inner {\n" + body + "    #[derive(Debug)]\n    pub struct S;\n}\n"


def in_fn_derive(body, outside=""):
    return outside + "fn elsewhere() {\n" + body + "    #[derive(Debug)]\n    struct S;\n}\n"


# What rustc takes as the standard library's own derive under each name (built: a proc-macro crate that exports a derive of every name
# collides with `use pm::NAME;` exactly when the use under test also gives NAME's macro): the module the trait is in, and the preludes.
# alloc has only `alloc::fmt::Debug`, and `core::prelude`'s modules are the same as std's.
STD_PATHS = [
    ("Clone", "clone"), ("Copy", "marker"), ("Debug", "fmt"), ("Default", "default"), ("Eq", "cmp"), ("Hash", "hash"),
    ("Ord", "cmp"), ("PartialEq", "cmp"), ("PartialOrd", "cmp"),
]


@pytest.mark.parametrize("crate", ["std", "core"])
@pytest.mark.parametrize("name,module", STD_PATHS)
def test_a_use_of_the_standard_librarys_own_derive_does_not_replace_it(make_repo, crate, name, module):
    """`use std::fmt::Debug;` gives the trait and std's own derive the name, so the name is still std's (the review's derive_shadow
    is `use derive_more::Debug;`, which is not). The first segment is proved to be the real crate, and the path one rustc exports the
    derive under."""
    supers = {"Copy": "Clone", "Eq": "PartialEq", "Ord": "Eq, PartialOrd, PartialEq", "PartialOrd": "PartialEq"}.get(name)
    derive = f"{supers}, {name}" if supers else name
    assert derive_call(make_repo, f"use {crate}::{module}::{name};\n", derive) == EDGE


@pytest.mark.parametrize(
    "pre",
    [
        "use ::std::fmt::Debug;\n",
        "use ::core::fmt::Debug;\n",
        "extern crate alloc;\nuse ::alloc::fmt::Debug;\n",
        "use std::fmt::{self, Debug};\n",
        "use std::{fmt::{self, Debug}, hash::Hash};\n",
        "use std::{fmt::{Display, Debug}, clone::Clone};\n",
        "use std::fmt::{Debug as Debug};\n",
        "use std::fmt::Debug as Debug;\n",
        "pub use std::fmt::Debug;\n",
        "pub(crate) use std::fmt::Debug;\n",
        "use std::prelude::v1::Debug;\n",
        "use core::prelude::rust_2021::{Debug, Clone};\n",
        "use std::prelude::rust_2024::Debug;\n",
        "use std::fmt::Debug;\nuse std::fmt::Debug as D;\n",
        "use std::fmt::*;\n",
        "use std::prelude::v1::*;\n",
        "mod inner {\n    use std::fmt::Debug;\n}\n",
        "fn elsewhere() {\n    use std::fmt::Debug;\n}\n",
        "fn elsewhere() {\n    use core::fmt::Debug;\n    fn deeper() {\n        use std::fmt::Debug;\n    }\n}\n",
    ],
)
def test_standard_forms_of_the_use_leave_the_derive_the_standard_librarys(make_repo, pre):
    assert derive_call(make_repo, pre) == EDGE


@pytest.mark.parametrize(
    "pre",
    [
        "mod std {\n    pub mod fmt {\n        pub use fake::Debug;\n    }\n}\nuse std::fmt::Debug;\n",
        "mod core {\n    pub mod fmt {\n        pub use fake::Debug;\n    }\n}\nuse core::fmt::Debug;\n",
        "extern crate fake as std;\nuse std::fmt::Debug;\n",
        "extern crate fake as std;\nuse ::std::fmt::Debug;\n",
        "extern crate fake as core;\nuse core::fmt::Debug;\n",
        "use fake as core;\nuse core::fmt::Debug;\n",
        "use fake as std;\nuse std::fmt::Debug;\n",
        "#![no_std]\nextern crate std;\nuse std::fmt::Debug;\n",
        "extern crate std as core;\nuse core::fmt::Debug;\n",
        "extern crate core as std;\nuse ::std::fmt::Debug;\n",
    ],
)
def test_a_use_that_is_not_proved_to_name_the_standard_library_replaces_the_derive(make_repo, pre):
    """Each `std` below is not the standard library's crate (or ripple cannot tell): a module, an alias, a block's import or module."""
    got = derive_call(make_repo, pre)
    assert got == MAKE, got


STD_MOD = "mod std {\n    pub mod fmt {\n        pub use fake::Debug;\n    }\n}\n"


@pytest.mark.parametrize(
    "src",
    [
        in_fn_derive("    use fake as std;\n    use std::fmt::Debug;\n"),
        in_fn_derive("    mod std {\n        pub mod fmt {\n            pub use fake::Debug;\n        }\n    }\n    use std::fmt::Debug;\n"),
        in_module_derive("    use fake as std;\n    use std::fmt::Debug;\n"),
        in_module_derive("    use fake::*;\n    use std::fmt::Debug;\n"),
        in_module_derive("    use super::std::fmt::Debug;\n", STD_MOD),
        in_fn_derive("    use std::fmt::Debug;\n", STD_MOD),
    ],
)
def test_a_use_that_is_not_proved_to_name_the_standard_library_replaces_the_derive_in_its_own_scope(make_repo, src):
    """The same forms as above, written in the module or function body that holds the derive (another scope's use is not seen)."""
    assert marked(make_repo, src) == {0: "Debug"}, src


@pytest.mark.parametrize("key", ["std", "core"])
def test_a_dependency_called_std_or_core_is_not_the_standard_library(make_repo, key):
    got = derive_call(make_repo, f"use {key}::fmt::Debug;\n", deps=f'[dependencies]\nfake = "1"\n{key} = "1"\n')
    assert got == MAKE, got
    got = derive_call(make_repo, f"use ::{key}::fmt::Debug;\n", deps=f'[dependencies]\nfake = "1"\n{key} = {{ package = "fake", version = "1" }}\n')
    assert got == MAKE, got


@pytest.mark.parametrize(
    "pre",
    [
        "use std::marker::Debug;\n",
        "use std::borrow::Debug;\n",
        "use std::Debug;\n",
        "use std::fmt::inner::Debug;\n",
        "use std::fmt::{self as Debug};\n",
        "use std::fmt::Display as Debug;\n",
        "use core::cmp::Ord as Debug;\n",
        "use ::std::rc::Debug;\n",
        "extern crate alloc;\nuse alloc::fmt::Debug;\n",
        "extern crate alloc;\nuse ::alloc::clone::Debug;\n",
    ],
)
def test_a_path_the_standard_library_does_not_export_the_derive_under_replaces_it(make_repo, pre):
    """A use that gives the name by any other path, or by a rename onto it, is not followed (rustc: `std::marker::Debug` is a
    private import, `use std::fmt::Display as Debug` gives only Display's trait): refused, a recall cost only."""
    got = derive_call(make_repo, pre)
    assert got == MAKE, got


def test_alloc_exports_the_derive_only_as_alloc_fmt_debug(make_repo):
    """Built: `use ::alloc::fmt::Debug;` gives std's derive, and `alloc::clone::Clone` and `alloc::hash::Hash` are no imports at all
    (the paths that std and core export a derive under are not alloc's), so only the one path is followed."""
    assert derive_call(make_repo, "extern crate alloc;\nuse ::alloc::fmt::Debug;\n") == EDGE
    for pre, derive in (("use ::alloc::clone::Clone;\n", "Clone"), ("use ::alloc::hash::Hash;\n", "Hash"), ("use ::alloc::prelude::v1::Debug;\n", "Debug")):
        got = derive_call(make_repo, "extern crate alloc;\n" + pre, derive=derive)
        assert got == MAKE, (pre, got)


def test_a_2015_crate_reads_std_from_its_root_so_the_use_is_not_proved(make_repo):
    """In a 2015 crate `use std::fmt::Debug;` and `::std::fmt::Debug` start at the crate root, where a module can be called std: ripple
    reads no such use as std's (the 2018 crate beside it is the control). Read from the flag, as no call in a 2015 crate is resolved."""
    from ripple.indexer import walk_languages

    def replaced(edition, use):
        repo = make_repo({"Cargo.toml": PKG.replace("2021", edition), "src/lib.rs": use + "#[derive(Debug)]\nstruct S;\n"})
        files, skipped = walk_languages(repo, [RUST])
        return [dict(ctx.derive_replaced) for ctx in rs.read_repo(repo, files[RUST.name], skipped)[0].ctxs]

    for use in ("use std::fmt::Debug;\n", "use ::std::fmt::Debug;\n"):
        assert replaced("2018", use) == [{}], use
        assert replaced("2015", use) == [{0: "Debug"}], use


@pytest.mark.parametrize("pre,derive", [("use std::cmp::PartialOrd as Ord;\n", "Ord"), ("use core::fmt::Debug as Clone;\n", "Clone"), ("use ::std::cmp::Ord as Eq;\n", "Eq")])
def test_a_standard_derive_renamed_onto_another_standard_name_replaces_it(make_repo, pre, derive):
    """Built: `use std::cmp::PartialOrd as Ord;` makes `#[derive(Ord)]` expand std's PartialOrd derive. The path is std's and one rustc
    exports, but the name is not that item's, so the standard derive of that name is not what runs."""
    got = derive_call(make_repo, pre, derive=derive)
    assert got == MAKE, got


def test_a_rename_gives_another_name_and_leaves_the_derive_alone(make_repo):
    """`use std::fmt::Debug as D;` gives `D`, not `Debug` (built: the derive `Debug` after it is std's, and `#[derive(D)]` is the
    renamed one, whichever macro that is): the name stays std's, while a derive written `D` is not a standard name, so it is another macro."""
    assert derive_call(make_repo, "use std::fmt::Debug as D;\n") == EDGE
    assert derive_call(make_repo, "use std::fmt::Debug as _;\n") == EDGE
    got = derive_call(make_repo, "use std::fmt::Debug as D;\n", derive="D")
    assert got == MAKE, got


def test_one_use_that_is_not_the_standard_librarys_replaces_the_name_beside_a_proved_one(make_repo):
    """A proved use at the root, and one that is not in the module that derives: the module sees only its own."""
    proved = "use std::fmt::Debug;\n"
    assert marked(make_repo, in_module_derive("    use fake::Debug;\n", proved)) == {0: "Debug"}
    assert marked(make_repo, in_module_derive("    use fake::Clone;\n", proved)) == {}
    assert marked(make_repo, in_fn_derive("    use fake::Debug;\n", proved)) == {0: "Debug"}


@pytest.mark.parametrize(
    "pre",
    [
        "use std::fmt::Debug;\n#[macro_use]\nextern crate fake;\n",
        "use std::fmt::Debug;\nmacro_rules! Debug {\n    () => {};\n}\n",
    ],
)
def test_a_proved_use_does_not_stop_another_macro_from_replacing_the_name(make_repo, pre):
    got = derive_call(make_repo, pre)
    assert got == MAKE, got


@pytest.mark.parametrize(
    "src",
    [
        in_fn_derive("    use derive_more::Debug;\n", "use std::fmt::Debug;\nuse fake::Debug as Dbg;\n"),
        in_module_derive("    use fake::{self, Debug};\n", "use std::fmt::Debug;\n"),
        in_fn_derive("    use fake::Debug;\n", "use std::fmt::Debug;\n"),
    ],
)
def test_a_proved_use_of_the_scope_around_does_not_stop_a_use_here_from_replacing_the_name(make_repo, src):
    assert marked(make_repo, src) == {0: "Debug"}, src


def test_an_unknown_derive_still_marks_its_scope_beside_a_standard_use(make_repo):
    assert derive_call(make_repo, "use std::fmt::Debug;\n", derive="Debug, fake::Emit") == MAKE
    assert derive_call(make_repo, "use std::fmt::Debug;\n", derive="Serialize") == MAKE


def test_a_scope_where_a_macro_may_make_names_does_not_prove_std_there(make_repo):
    """`std` in a module with a derive that may emit a module of that name is not known to be the crate: that use replaces the name
    for the derive beside it (and, as the use is the module's, for no derive outside the module)."""
    body = "    use std::fmt::Debug;\n    #[derive(Serialize)]\n    pub struct T;\n"
    assert marked(make_repo, in_module_derive(body)) == {0: "Debug"}
    assert marked(make_repo, in_module_derive(body.replace("Serialize", "Clone"))) == {}  # the control: nothing may have made `std`
    assert derive_call(make_repo, "mod inner {\n" + body + "}\n") == EDGE


def test_a_derive_written_by_a_standard_path_with_a_proved_use_is_still_standard(make_repo):
    assert derive_call(make_repo, "use std::fmt::Debug;\nuse fake::Copy;\n", derive="std::clone::Clone, core::marker::Copy") == EDGE


@pytest.mark.parametrize("derive", ["std::fmt::Debug", "core::fmt::Debug", "std::clone::Clone, core::marker::Copy"])
def test_a_derive_written_by_a_standard_path_is_not_replaced_by_a_use_of_its_name(make_repo, derive):
    """`std::fmt::Debug` names the standard derive whatever `use` gives `Debug`: the path starts at the crate."""
    assert calls(make_repo, "use fake::Debug;\nuse fake::Copy;\n" + GLOB + f"#[derive({derive})]\nstruct S;\n" + CALL) == EDGE


def test_a_use_of_another_name_leaves_the_standard_derive_alone(make_repo):
    assert calls(make_repo, "use fake::Display;\nuse std::fmt::Debug as Dbg;\n" + GLOB + "#[derive(Debug)]\nstruct S;\n" + CALL) == EDGE


def test_only_the_scope_that_holds_the_derive_may_have_generated_names(make_repo):
    src = "mod a {\n    #[derive(Serialize)]\n    pub struct S;\n}\nmod b {\n    pub fn one() {}\n}\nmod c {\n    use super::b::*;\n    pub fn f() {\n        one();\n    }\n}\n"
    got = calls(make_repo, src)
    assert got == {("src/lib:c::f", "edge", "src/lib:b::one")}, got


def test_the_scope_of_a_derive_in_a_block_is_the_block(make_repo):
    src = "mod b {\n    pub fn one() {}\n}\nfn f() {\n    use b::*;\n    #[derive(Serialize)]\n    struct S;\n    one();\n}\nfn g() {\n    use b::*;\n    one();\n}\n"
    got = calls(make_repo, src)
    assert ("src/lib:f", "unresolved", rs.MACRO_MAY_MAKE) in got and ("src/lib:g", "edge", "src/lib:b::one") in got, got


def test_an_enum_and_a_union_are_read_the_same_way(make_repo):
    for kind in ("enum E {}", "union U { a: u8 }"):
        assert calls(make_repo, GLOB + f"#[derive(Serialize)]\n{kind}\n" + CALL) == MAKE


def test_a_derive_in_a_cfg_attr_is_read_the_same_way(make_repo):
    got = calls(make_repo, GLOB + '#[cfg_attr(feature = "x", derive(Serialize))]\nstruct S;\n' + CALL)
    assert got == MAKE, got


def test_the_impls_ripple_read_may_not_be_all_there_are_beside_such_a_derive(make_repo):
    src = "struct S;\ntrait Tr {\n    fn new();\n}\nimpl Tr for S {\n    fn new() {}\n}\n#[derive(Serialize)]\nstruct T;\nfn f() {\n    S::new();\n}\n"
    got = calls(make_repo, src)
    assert not any(k == "edge" for _, k, _ in got), got
    src = src.replace("#[derive(Serialize)]\nstruct T;\n", "#[derive(Debug)]\nstruct T;\n")
    assert ("src/lib:f", "edge", "src/lib:<S as Tr>::new") in calls(make_repo, src)


def test_a_name_that_is_cfg_declared_where_a_derive_may_have_made_names_says_so_not_that_it_names_two_definitions(make_repo):
    """`use crate::Glob;` looks the name up in each namespace: the type under a #[cfg] is not known, the value and the macro one may
    have been made by the derive. Two reasons it is not known, not two definitions (ripgrep's `Glob::new`): the macro one is reported."""
    src = (
        '#[cfg(feature = "x")]\npub struct Glob;\n#[cfg(feature = "x")]\nimpl Glob {\n    pub fn new() {}\n}\n'
        "#[derive(Serialize)]\npub struct T;\nmod m {\n    use crate::Glob;\n    pub fn f() {\n        Glob::new();\n    }\n}\n"
    )
    got = calls(make_repo, src)
    assert got == {("src/lib:m::f", "unresolved", rs.MACRO_MAY_MAKE)}, got
