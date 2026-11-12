"""The Rust front end: what it proves from a path call, and what it leaves unresolved, rule by rule.

Each test builds a small cargo repo and asserts the exact edges (and, where the rule is a refusal, the exact
unresolved reason) in both directions: the call that is proven is an edge, the one that is not is not.
These need tree-sitter-rust (the `rust` extra) and skip without it; tests/test_rs_optional.py covers its absence.
"""

import pytest

pytest.importorskip("tree_sitter_rust")

from ripple import rs_indexer as rs  # noqa: E402
from ripple.indexer import build_graph  # noqa: E402
from ripple.profile import RUST  # noqa: E402

CARGO = '[package]\nname = "app"\nversion = "0.1.0"\nedition = "2021"\n'


def graph_of(make_repo, files, cargo=CARGO):
    return build_graph(make_repo({"Cargo.toml": cargo, **files}), RUST)


def edge_set(graph, kind="call"):
    return {(e.caller, e.callee) for e in graph.edges if e.kind == kind}


def reasons(graph):
    return {(u.caller, u.text): u.reason for u in graph.unresolved}


def one_reason(graph, caller, text):
    return reasons(graph)[(caller, text)]


# ---- ids


def test_a_module_is_its_file_with_mod_rs_folded_and_lib_rs_kept(make_repo):
    g = graph_of(make_repo, {"src/lib.rs": "mod a;\nmod b;\n", "src/a.rs": "pub fn f() {}\n", "src/b/mod.rs": "pub fn g() {}\n"})
    assert {s for s, v in g.symbols.items() if v.kind == "module"} == {"src/lib", "src/a", "src/b"}
    assert {"src/a:f", "src/b:g"} <= set(g.symbols)
    assert g.modules == {"src/lib.rs": "src/lib", "src/a.rs": "src/a", "src/b/mod.rs": "src/b"}


def test_items_methods_trait_impls_and_inline_modules_have_their_own_ids(make_repo):
    src = """
        pub struct Foo;
        pub trait Tr { fn m(&self); fn d() {} }
        impl Foo { pub fn new() -> Self { Foo } }
        impl Tr for Foo { fn m(&self) {} }
        mod tests { pub fn it_works() {} }
        pub fn outer() { fn inner() {} }
    """
    g = graph_of(make_repo, {"src/lib.rs": src})
    ids = set(g.symbols)
    assert {"src/lib:Foo", "src/lib:Tr", "src/lib:Tr::m", "src/lib:Tr::d", "src/lib:Foo::new", "src/lib:<Foo as Tr>::m"} <= ids
    assert {"src/lib:tests::it_works", "src/lib:outer", "src/lib:outer::inner"} <= ids
    kinds = {sid: g.symbols[sid].kind for sid in ("src/lib:Foo", "src/lib:Tr", "src/lib:Foo::new", "src/lib:outer")}
    assert kinds == {"src/lib:Foo": "class", "src/lib:Tr": "class", "src/lib:Foo::new": "method", "src/lib:outer": "function"}
    assert g.symbols["src/lib:Foo::new"].name == "new" and g.symbols["src/lib:<Foo as Tr>::m"].name == "m"


def test_the_impl_block_that_holds_a_method_names_its_file_not_the_types(make_repo):
    g = graph_of(make_repo, {"src/lib.rs": "mod a;\nmod b;\n", "src/a.rs": "pub struct Foo;\n", "src/b.rs": "impl crate::a::Foo { pub fn new() {} }\n"})
    assert "src/b:Foo::new" in g.symbols and "src/a:Foo" in g.symbols


def test_two_impls_of_one_trait_for_one_type_keep_the_generic_arguments_in_the_id(make_repo):
    src = """
        pub struct E;
        impl From<u8> for E { fn from(_: u8) -> Self { E } }
        impl From<u16> for E { fn from(_: u16) -> Self { E } }
    """
    g = graph_of(make_repo, {"src/lib.rs": src})
    assert {"src/lib:<E as From<u8>>::from", "src/lib:<E as From<u16>>::from"} <= set(g.symbols)


def test_definitions_that_still_share_an_id_take_a_numbered_suffix(make_repo):
    src = """
        #[cfg(unix)]
        fn imp() {}
        #[cfg(not(unix))]
        fn imp() {}
    """
    g = graph_of(make_repo, {"src/lib.rs": src})
    assert {"src/lib:imp", "src/lib:imp#2"} <= set(g.symbols)
    assert g.symbols["src/lib:imp#2"].name == "imp"


# ---- the crate graph


def test_a_binary_calls_the_library_of_its_own_package_by_name(make_repo):
    g = graph_of(make_repo, {"src/lib.rs": "pub fn run() {}\n", "src/main.rs": "fn main() { app::run(); }\n"})
    assert ("src/main:main", "src/lib:run") in edge_set(g)


def test_a_hyphen_in_a_package_name_is_an_underscore_and_a_lib_name_overrides_it(make_repo):
    cargo = '[package]\nname = "my-app"\nversion = "0.1.0"\nedition = "2021"\n[lib]\nname = "engine"\n'
    g = graph_of(make_repo, {"src/lib.rs": "pub fn run() {}\n", "src/main.rs": "fn main() { engine::run(); }\n"}, cargo)
    assert ("src/main:main", "src/lib:run") in edge_set(g)


def workspace(make_repo, extra=None, dep='core-lib = { path = "../core-lib" }'):
    files = {
        "Cargo.toml": '[workspace]\nmembers = ["app", "core-lib"]\nresolver = "2"\n',
        "core-lib/Cargo.toml": '[package]\nname = "core-lib"\nversion = "0.1.0"\nedition = "2021"\n',
        "core-lib/src/lib.rs": "pub fn shared() {}\npub mod inner { pub fn deep() {} }\n",
        "app/Cargo.toml": f'[package]\nname = "app"\nversion = "0.1.0"\nedition = "2021"\n[dependencies]\n{dep}\n',
        "app/src/lib.rs": "pub fn run() { core_lib::shared(); core_lib::inner::deep(); }\n",
        **(extra or {}),
    }
    return build_graph(make_repo(files), RUST)


def test_a_path_dependency_with_a_hyphenated_key_is_a_crate_of_the_repo(make_repo):
    g = workspace(make_repo)
    assert {("app/src/lib:run", "core-lib/src/lib:shared"), ("app/src/lib:run", "core-lib/src/lib:inner::deep")} <= edge_set(g)


def test_a_renamed_dependency_is_named_by_its_key_and_the_package_name_is_not_a_crate(make_repo):
    g = workspace(
        make_repo,
        {"app/src/lib.rs": "pub fn run() { money::shared(); core_lib::shared(); }\n"},
        'money = { package = "core-lib", path = "../core-lib" }',
    )
    assert ("app/src/lib:run", "core-lib/src/lib:shared") in edge_set(g)
    assert len(edge_set(g)) == 1  # `core_lib` is not a name this package can use: it is not an edge
    assert one_reason(g, "app/src/lib:run", "core_lib::shared").startswith("the name is not declared")


def test_a_dependency_from_a_registry_is_outside_the_repo_even_if_a_package_has_its_name(make_repo):
    g = workspace(make_repo, None, 'core-lib = "1"')
    assert ("app/src/lib:run", "core-lib/src/lib:shared") not in edge_set(g)
    assert g.external_calls == 2 and set(g.external_reasons) == {rs.CRATE_PATH}


def test_a_workspace_dependency_takes_its_path_from_the_workspace_table(make_repo):
    files = {
        "Cargo.toml": '[workspace]\nmembers = ["app", "libs/core-lib"]\n[workspace.dependencies]\ncore-lib = { path = "libs/core-lib" }\n',
        "libs/core-lib/Cargo.toml": '[package]\nname = "core-lib"\nversion = "0.1.0"\nedition = "2021"\n',
        "libs/core-lib/src/lib.rs": "pub fn shared() {}\n",
        "app/Cargo.toml": '[package]\nname = "app"\nversion = "0.1.0"\nedition = "2021"\n[dependencies]\ncore-lib.workspace = true\n',
        "app/src/lib.rs": "pub fn run() { core_lib::shared(); }\n",
    }
    g = build_graph(make_repo(files), RUST)
    assert edge_set(g) == {("app/src/lib:run", "libs/core-lib/src/lib:shared")}


def test_bin_path_overrides_src_bin_and_tests_are_crates_that_depend_on_the_library(make_repo):
    cargo = CARGO + '[[bin]]\nname = "rg"\npath = "crates/core/main.rs"\n'
    files = {
        "src/lib.rs": "pub fn run() {}\n",
        "crates/core/main.rs": "fn main() { app::run(); }\n",
        "src/bin/tool.rs": "fn main() { app::run(); }\n",
        "tests/it.rs": "#[test]\nfn t() { app::run(); }\n",
    }
    g = graph_of(make_repo, files, cargo)
    assert {("crates/core/main:main", "src/lib:run"), ("src/bin/tool:main", "src/lib:run"), ("tests/it:t", "src/lib:run")} <= edge_set(g)


def test_a_build_script_is_a_crate_of_its_own_and_does_not_see_the_library(make_repo):
    g = graph_of(make_repo, {"src/lib.rs": "pub fn run() {}\n", "build.rs": "fn main() { go(); app::run(); }\nfn go() {}\n"})
    assert edge_set(g) == {("build:main", "build:go")}
    assert one_reason(g, "build:main", "app::run").startswith("the name is not declared")


def test_a_manifest_that_cannot_be_read_is_reported_and_its_files_are_not_read(make_repo):
    g = graph_of(make_repo, {"src/lib.rs": "pub fn f() {}\n"}, cargo="[package\nname = 3")
    assert any("Cargo.toml could not be read" in e for e in g.parse_errors) and "src/lib:f" not in g.symbols


# ---- the mod tree


def test_a_path_attribute_inline_modules_and_nested_directories_are_followed(make_repo):
    files = {
        "src/lib.rs": '#[path = "elsewhere/odd.rs"]\nmod odd;\nmod a;\nmod inline { pub mod deeper { pub fn z() {} } }\npub fn go() { odd::o(); a::b::c(); inline::deeper::z(); }\n',
        "src/elsewhere/odd.rs": "pub fn o() {}\n",
        "src/a.rs": "pub mod b;\n",
        "src/a/b.rs": "pub fn c() {}\n",
    }
    g = graph_of(make_repo, files)
    assert edge_set(g) == {("src/lib:go", "src/elsewhere/odd:o"), ("src/lib:go", "src/a/b:c"), ("src/lib:go", "src/lib:inline::deeper::z")}


def test_a_file_no_mod_reaches_and_a_mod_with_no_file_are_reported_not_dropped(make_repo):
    files = {"src/lib.rs": "mod missing;\nmod real;\n", "src/real.rs": "pub fn f() {}\n", "src/stray.rs": "pub fn s() {}\n"}
    g = graph_of(make_repo, files)
    assert "src/stray:s" not in g.symbols and "src/real:f" in g.symbols
    assert any(e.startswith("src/stray.rs (no `mod` declaration reaches it") for e in g.unread)
    assert any("`mod missing;` names no file" in e for e in g.unread)
    assert g.parse_errors == []  # not read is not a parse failure, and the header counts them apart


def test_a_file_with_a_syntax_error_is_listed_and_still_read(make_repo):
    g = graph_of(make_repo, {"src/lib.rs": "pub fn ok() {}\npub fn bad( {\n"})
    assert "src/lib.rs" in g.parse_errors and "src/lib:ok" in g.symbols


def test_a_target_directory_beside_a_manifest_is_not_walked(make_repo):
    repo = make_repo({"Cargo.toml": CARGO, "src/lib.rs": "pub fn f() {}\n", "target/debug/build/x/out/gen.rs": "pub fn g() {}\n"})
    g = build_graph(repo, RUST)
    assert g.parse_errors == [] and "target/debug/build/x/out/gen" not in g.modules.values()


# ---- name resolution


def test_use_lists_aliases_self_super_crate_and_glob_imports_resolve(make_repo):
    files = {
        "src/lib.rs": "mod m;\nmod n;\nuse m::{f, g as h, sub::{self, k}};\nuse n::*;\n"
        "pub fn run() { f(); h(); sub::l(); k(); glob_fn(); crate::m::f(); self::local(); }\nfn local() {}\n",
        "src/m.rs": "pub fn f() {}\npub fn g() {}\npub mod sub { pub fn l() {}\n pub fn k() { super::f(); } }\n",
        "src/n.rs": "pub fn glob_fn() {}\n",
    }
    g = graph_of(make_repo, files)
    assert edge_set(g) == {
        ("src/lib:run", "src/m:f"), ("src/lib:run", "src/m:g"), ("src/lib:run", "src/m:sub::l"), ("src/lib:run", "src/m:sub::k"),
        ("src/lib:run", "src/n:glob_fn"), ("src/lib:run", "src/lib:local"), ("src/m:sub::k", "src/m:f"),
    }  # fmt: skip


def test_a_pub_use_chain_and_a_glob_re_export_are_followed_to_the_definition(make_repo):
    files = {
        "src/lib.rs": "mod a;\nmod b;\nmod c;\nuse a::deep;\nuse b::*;\npub fn run() { deep(); starred(); }\n",
        "src/a.rs": "pub use crate::b::hop as deep;\n",
        "src/b.rs": "pub use crate::c::*;\npub fn hop() {}\n",
        "src/c.rs": "pub fn starred() {}\n",
    }
    g = graph_of(make_repo, files)
    assert edge_set(g) == {("src/lib:run", "src/b:hop"), ("src/lib:run", "src/c:starred")}


def test_a_named_import_wins_over_a_glob_and_a_name_in_two_globs_is_unresolved(make_repo):
    files = {
        "src/lib.rs": "mod a;\nmod b;\nuse a::*;\nuse b::*;\nuse a::only_a;\npub fn run() { only_a(); both(); }\n",
        "src/a.rs": "pub fn only_a() {}\npub fn both() {}\n",
        "src/b.rs": "pub fn only_a() {}\npub fn both() {}\n",
    }
    g = graph_of(make_repo, files)
    assert edge_set(g) == {("src/lib:run", "src/a:only_a")}
    assert one_reason(g, "src/lib:run", "both").startswith("the path names more than one definition")


def test_a_glob_from_outside_the_repo_never_makes_a_name_a_repo_item(make_repo):
    files = {"src/lib.rs": "use std::cmp::*;\npub fn max(a: u32) -> u32 { a }\npub fn run() { max(1); min(1, 2); }\n"}
    g = graph_of(make_repo, files)
    # `max` is declared here, so an explicit item beats the glob; `min` can only come from the glob
    assert edge_set(g) == {("src/lib:run", "src/lib:max")}
    assert g.external_calls == 1 and set(g.external_reasons) == {rs.EXT_GLOB}


def test_a_glob_from_outside_the_repo_in_a_block_may_shadow_a_module_item(make_repo):
    files = {"src/lib.rs": "pub fn max(a: u32) -> u32 { a }\npub fn run() { use std::cmp::*; max(1, 2); }\n"}
    g = graph_of(make_repo, files)
    assert edge_set(g) == set()
    assert one_reason(g, "src/lib:run", "max") == rs.BLOCK_GLOB


def test_a_function_inside_a_block_is_visible_in_that_block_only(make_repo):
    files = {"src/lib.rs": "fn helper() {}\npub fn a() { fn helper() {}\n helper(); }\npub fn b() { helper(); }\n"}
    g = graph_of(make_repo, files)
    assert edge_set(g) == {("src/lib:a", "src/lib:a::helper"), ("src/lib:b", "src/lib:helper")}


def test_a_name_bound_by_a_parameter_a_let_or_a_pattern_is_a_local_value(make_repo):
    src = """
        fn f() {}
        pub fn a(f: fn()) { f(); }
        pub fn b() { let f = || (); f(); }
        pub fn c(o: Option<fn()>) { if let Some(f) = o { f(); } }
        pub fn d() { f(); }
    """
    g = graph_of(make_repo, {"src/lib.rs": src})
    assert edge_set(g) == {("src/lib:d", "src/lib:f")}
    assert {one_reason(g, f"src/lib:{c}", "f") for c in "abc"} == {rs.LOCAL}


def test_a_closure_parameter_shadows_inside_the_arguments_of_a_standard_macro(make_repo):
    g = graph_of(make_repo, {"src/lib.rs": "fn f() {}\npub fn a() { println!(\"{:?}\", Some(1).map(|f| f())); }\n"})
    assert edge_set(g) == set() and one_reason(g, "src/lib:a", "f") == rs.LOCAL


def test_a_crate_of_edition_2015_resolves_no_path_call(make_repo):
    g = graph_of(make_repo, {"src/lib.rs": "fn f() {}\npub fn a() { f(); }\n"}, '[package]\nname = "app"\nversion = "0.1.0"\n')
    assert edge_set(g) == set() and one_reason(g, "src/lib:a", "f") == rs.EDITION


# ---- calls of a type, Self, a trait


TYPES = """
    pub struct Foo;
    pub struct Pair(pub u8, pub u8);
    pub enum Shape { Dot, Ring(u8) }
    pub trait Tr { fn make() -> Self; fn name(&self) -> u8 { 1 } fn describe(&self) -> u8; }
    impl Foo {
        pub fn new() -> Self { Self::inner() }
        fn inner() -> Self { Foo }
    }
    impl Tr for Foo { fn make() -> Self { Foo } fn describe(&self) -> u8 { 0 } }
    impl Shape { pub fn ring() -> Self { Self::Ring(1) } pub fn dot() -> Shape { Shape::Dot } }
"""


def test_type_calls_self_calls_and_constructors(make_repo):
    src = TYPES + "pub fn run() { Foo::new(); Shape::ring(); Pair(1, 2); Shape::Ring(2); Shape::Dot; Foo::make(); }\n"
    g = graph_of(make_repo, {"src/lib.rs": src})
    assert edge_set(g) == {
        ("src/lib:run", "src/lib:Foo::new"), ("src/lib:run", "src/lib:Shape::ring"),
        ("src/lib:run", "src/lib:<Foo as Tr>::make"), ("src/lib:Foo::new", "src/lib:Foo::inner"),
    }  # fmt: skip
    assert g.unresolved == [] and g.external_calls == 0  # a constructor runs no code: neither an edge nor a refusal


def test_a_trait_path_call_is_the_declaration_with_each_impl_as_an_override(make_repo):
    src = TYPES + "pub struct Bar;\nimpl Tr for Bar { fn make() -> Self { Bar } fn describe(&self) -> u8 { 2 } }\npub fn run() { Tr::describe(&Foo); }\n"
    g = graph_of(make_repo, {"src/lib.rs": src})
    assert edge_set(g) - {("src/lib:Foo::new", "src/lib:Foo::inner")} == {("src/lib:run", "src/lib:Tr::describe")}
    assert edge_set(g, "override") == {("src/lib:run", "src/lib:<Foo as Tr>::describe"), ("src/lib:run", "src/lib:<Bar as Tr>::describe")}


def test_a_qualified_call_names_the_impl_of_its_type_and_a_default_body_is_the_declaration(make_repo):
    src = TYPES + "pub fn run() { <Foo as Tr>::make(); <Foo as Tr>::name(&Foo); }\n"
    g = graph_of(make_repo, {"src/lib.rs": src})
    assert edge_set(g) - {("src/lib:Foo::new", "src/lib:Foo::inner")} == {("src/lib:run", "src/lib:<Foo as Tr>::make"), ("src/lib:run", "src/lib:Tr::name")}


def test_self_in_a_trait_default_body_is_the_trait_declaration(make_repo):
    src = "pub trait Tr { fn a() -> u8; fn b() -> u8 { Self::a() } }\npub struct S;\nimpl Tr for S { fn a() -> u8 { 1 } }\n"
    g = graph_of(make_repo, {"src/lib.rs": src})
    assert edge_set(g) == {("src/lib:Tr::b", "src/lib:Tr::a")}
    assert edge_set(g, "override") == {("src/lib:Tr::b", "src/lib:<S as Tr>::a")}


def test_a_generic_parameter_as_a_path_is_unresolved(make_repo):
    g = graph_of(make_repo, {"src/lib.rs": "pub trait Tr { fn make() -> u8; }\npub fn f<T: Tr>() -> u8 { T::make() }\n"})
    assert edge_set(g) == set() and one_reason(g, "src/lib:f", "T::make") == rs.GENERIC_PATH


def test_two_definitions_of_one_function_are_unresolved_not_an_edge_to_each(make_repo):
    src = "#[cfg(unix)]\nfn imp() {}\n#[cfg(not(unix))]\nfn imp() {}\npub fn run() { imp(); }\n"
    g = graph_of(make_repo, {"src/lib.rs": src})
    assert edge_set(g) == set() and one_reason(g, "src/lib:run", "imp") == rs.SEVERAL


def test_cfg_twins_inside_one_impl_and_two_impls_for_different_arguments_are_unresolved(make_repo):
    src = """
        pub struct W<T>(T);
        impl W<u8> { pub fn make() {} }
        impl W<u16> { pub fn make() {} }
        pub struct C;
        impl C { #[cfg(unix)] pub fn go() {} #[cfg(not(unix))] pub fn go() {} }
        pub fn run() { W::make(); C::go(); }
    """
    g = graph_of(make_repo, {"src/lib.rs": src})
    assert edge_set(g) == set()
    assert {one_reason(g, "src/lib:run", t) for t in ("W::make", "C::go")} == {rs.SEVERAL_INHERENT}


def test_an_inherent_function_wins_over_a_trait_function_of_the_same_name(make_repo):
    src = "pub struct S;\npub trait Tr { fn go() {} }\nimpl Tr for S { fn go() {} }\nimpl S { pub fn go() {} }\npub fn run() { S::go(); }\n"
    g = graph_of(make_repo, {"src/lib.rs": src})
    assert edge_set(g) == {("src/lib:run", "src/lib:S::go")}


def test_from_is_never_the_one_impl_because_the_standard_library_has_a_blanket_one(make_repo):
    src = "pub struct S;\nimpl From<u8> for S { fn from(_: u8) -> S { S } }\npub fn run() { S::from(1); }\n"
    g = graph_of(make_repo, {"src/lib.rs": src})
    assert edge_set(g) == set() and one_reason(g, "src/lib:run", "S::from") == rs.STD_BLANKET_AMBIGUOUS


def test_a_trait_function_is_the_target_only_when_the_trait_is_in_scope(make_repo):
    files = {
        "src/lib.rs": "mod t;\nmod with;\nmod without;\n",
        "src/t.rs": "pub struct S;\npub trait Tr { fn go() {} }\nimpl Tr for S { fn go() {} }\n",
        "src/with.rs": "use crate::t::{S, Tr};\npub fn a() { S::go(); }\n",
        "src/without.rs": "pub fn b() { crate::t::S::go(); }\n",
    }
    g = graph_of(make_repo, files)
    assert edge_set(g) == {("src/with:a", "src/t:<S as Tr>::go")}
    assert one_reason(g, "src/without:b", "crate::t::S::go") == rs.TRAIT_NOT_VISIBLE


def test_a_derive_gives_the_type_a_standard_function(make_repo):
    g = graph_of(make_repo, {"src/lib.rs": "#[derive(Default, Clone)]\npub struct S;\npub fn run() { S::default(); S::new(); }\n"})
    assert edge_set(g) == set() and g.external_reasons == {rs.DERIVED: 1}
    assert one_reason(g, "src/lib:run", "S::new") == rs.NO_IMPL


def test_a_path_through_a_trait_from_outside_the_repo_that_a_repo_impl_defines_is_unresolved(make_repo):
    src = "pub struct S;\nimpl Default for S { fn default() -> S { S } }\npub fn run() { let _: S = Default::default(); Iterator::next(&mut 1..2); }\n"
    g = graph_of(make_repo, {"src/lib.rs": src})
    assert one_reason(g, "src/lib:run", "Default::default") == rs.EXT_TRAIT_REACH
    assert g.external_reasons == {rs.EXT_TRAIT: 1}  # `next` is defined by no impl in the repo


def test_a_standard_path_a_prelude_name_and_a_primitive_are_outside_the_repo_with_their_reason(make_repo):
    src = "pub fn run() { std::mem::drop(1); String::new(); u8::from_str_radix(\"1\", 10); drop(1); }\n"
    g = graph_of(make_repo, {"src/lib.rs": src})
    assert g.external_calls == 4
    assert g.external_reasons == {rs.STD_PATH: 1, rs.PRELUDE: 2, rs.PRIMITIVE: 1}


def test_an_external_path_whose_last_name_a_repo_trait_declares_may_be_that_trait_and_is_unresolved(make_repo):
    src = "pub trait Ext { fn shout(&self); fn new() -> Self; }\npub fn run() { Vec::<u8>::new(); }\n"
    g = graph_of(make_repo, {"src/lib.rs": src})
    assert g.external_calls == 0 and one_reason(g, "src/lib:run", "Vec::new") == rs.EXT_TRAIT_SHADOW


def test_a_foreign_function_is_outside_the_repo(make_repo):
    g = graph_of(make_repo, {"src/lib.rs": 'extern "C" { fn abs(x: i32) -> i32; }\npub fn run() { unsafe { abs(1) }; }\n'})
    assert g.external_reasons == {rs.FOREIGN: 1}


def test_an_extern_crate_item_names_a_crate_of_the_repo(make_repo):
    files = {
        "Cargo.toml": '[workspace]\nmembers = ["app", "dep"]\n',
        "dep/Cargo.toml": '[package]\nname = "dep"\nversion = "0.1.0"\nedition = "2021"\n',
        "dep/src/lib.rs": "pub fn f() {}\n",
        "app/Cargo.toml": '[package]\nname = "app"\nversion = "0.1.0"\nedition = "2021"\n[dependencies]\ndep = { path = "../dep" }\n',
        "app/src/lib.rs": "pub extern crate dep as renamed;\npub fn run() { renamed::f(); }\n",
    }
    g = build_graph(make_repo(files), RUST)
    assert edge_set(g) == {("app/src/lib:run", "dep/src/lib:f")}


# ---- macros


def test_the_arguments_of_standard_macros_are_read_as_expressions(make_repo):
    src = """
        fn f() -> u8 { 1 }
        fn g() -> u8 { 2 }
        fn h() -> u8 { 3 }
        pub fn run() {
            println!("{}", f());
            let _ = vec![g(), g()];
            assert_eq!(h(), 3);
            let _ = format!("{}", std::cmp::max(f(), 1));
        }
    """
    g = graph_of(make_repo, {"src/lib.rs": src})
    sites = sorted((e.callee, e.line) for e in g.edges)
    assert sites == sorted([("src/lib:f", 6), ("src/lib:g", 7), ("src/lib:g", 7), ("src/lib:h", 8), ("src/lib:f", 9)])
    assert g.external_calls == 1  # std::cmp::max, found inside format!'s arguments


def test_a_repo_macro_of_a_standard_name_shadows_it_and_its_arguments_are_not_read(make_repo):
    src = "macro_rules! println { ($($t:tt)*) => {}; }\nfn f() {}\npub fn run() { println!(\"{}\", f()); }\n"
    g = graph_of(make_repo, {"src/lib.rs": src})
    assert edge_set(g) == set() and one_reason(g, "src/lib:run", "f") == rs.MACRO_BODY


def test_a_call_inside_any_other_macro_is_unresolved_and_a_macro_rules_body_is_no_call(make_repo):
    src = "fn f() {}\nmacro_rules! twice { ($e:expr) => { f(); $e }; }\npub fn run() { twice!(f()); other::mac!(f()); }\n"
    g = graph_of(make_repo, {"src/lib.rs": src})
    assert edge_set(g) == set()
    assert [(u.text, u.reason) for u in g.unresolved] == [("f", rs.MACRO_BODY)] * 2  # one in each invocation; the body's `f()` is a template


def test_quote_bodies_are_generated_code_and_make_no_call(make_repo):
    g = graph_of(make_repo, {"src/lib.rs": "fn f() {}\npub fn run() { quote!(f()); }\n"})
    assert g.unresolved == [] and edge_set(g) == set()


def test_a_standard_macro_whose_arguments_do_not_parse_as_expressions_is_unresolved(make_repo):
    g = graph_of(make_repo, {"src/lib.rs": "fn f() {}\npub fn run() { println!(\"{}\", f() => 1); }\n"})
    assert edge_set(g) == set() and one_reason(g, "src/lib:run", "f") == rs.STD_MACRO_BODY


def test_an_item_declared_inside_a_const_initializer_shadows_the_module_item_there(make_repo):
    src = "fn f() -> u8 { 1 }\nconst X: u8 = { fn f() -> u8 { 2 }\n f() };\nstatic Y: u8 = f();\n"
    g = graph_of(make_repo, {"src/lib.rs": src})
    assert edge_set(g) == {("src/lib", "src/lib:X::f"), ("src/lib", "src/lib:f")}


def test_a_call_in_a_const_or_static_initializer_belongs_to_the_module(make_repo):
    g = graph_of(make_repo, {"src/lib.rs": "const fn f() -> u8 { 1 }\nconst X: u8 = f();\nstatic Y: u8 = f();\n"})
    assert edge_set(g) == {("src/lib", "src/lib:f")}


# ---- method calls


def test_every_method_call_is_unresolved_with_the_one_reason_even_when_the_method_is_in_the_repo(make_repo):
    src = "pub struct S;\nimpl S { pub fn go(&self) {} }\npub fn run(s: S, v: Vec<u8>) { s.go(); v.len(); S.go(); s.go().x.y(); }\n"
    g = graph_of(make_repo, {"src/lib.rs": src})
    assert edge_set(g) == set()
    assert {u.reason for u in g.unresolved} == {rs.METHOD} and len(g.unresolved) == 5  # `s.go().x.y()` is two: y, and the go call under it
    assert {u.text.rsplit(".", 1)[-1] for u in g.unresolved} == {"go", "len", "y"}


def test_a_path_call_at_the_head_of_a_method_chain_is_its_own_call(make_repo):
    g = graph_of(make_repo, {"src/lib.rs": "pub struct S;\nimpl S { pub fn new() -> S { S } }\npub fn run() { S::new().go(); }\n"})
    assert edge_set(g) == {("src/lib:run", "src/lib:S::new")}
    assert [u.text for u in g.unresolved] == ["S::new().go"]


# ---- several crates read one file


def test_a_file_two_crates_share_is_an_edge_only_where_both_resolve_it_alike(make_repo):
    files = {
        "src/lib.rs": "pub fn run() {}\n",
        "tests/common/mod.rs": "pub fn setup() { app::run(); crate::local(); }\n",
        "tests/a.rs": "mod common;\nfn local() {}\n",
        "tests/b.rs": "mod common;\nfn local() {}\n",
    }
    g = graph_of(make_repo, files)
    assert edge_set(g) == {("tests/common:setup", "src/lib:run")}
    assert one_reason(g, "tests/common:setup", "crate::local") == rs.SHARED_FILE


# ---- what is read, and how long


def test_the_graph_is_the_same_for_a_second_build_and_lists_edges_in_source_order(make_repo):
    files = {"src/lib.rs": "mod a;\nfn f() {}\npub fn run() { f(); a::g(); f(); }\n", "src/a.rs": "pub fn g() {}\n"}
    repo = make_repo({"Cargo.toml": CARGO, **files})
    first, second = build_graph(repo, RUST), build_graph(repo, RUST)
    assert first.to_dict() == second.to_dict()
    assert [(e.callee, e.line) for e in first.edges] == [("src/lib:f", 3), ("src/a:g", 3), ("src/lib:f", 3)]
