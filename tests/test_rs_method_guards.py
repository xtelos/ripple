"""A method call is an edge only when the inherent impls of the receiver's type are provably all read, and nothing about the method is conditional.

Each test is one guard of the first slice: the same call, `s.go()` on a `&S` whose type is written, is an edge in the control and
unresolved, with the reason that names the guard, once the source adds the one thing the guard is about. A guard that is removed
turns its test red. (tests/test_rs_method_probes.py holds the compiler's answers; these are the refusals the compiler's
answers do not show, because a repo the compiler accepts has the impl this slice would find.)
"""

import pytest

pytest.importorskip("tree_sitter_rust")

from ripple import rs_indexer as rs  # noqa: E402
from ripple.indexer import build_graph  # noqa: E402
from ripple.profile import RUST  # noqa: E402

CARGO = '[package]\nname = "app"\nversion = "0.1.0"\nedition = "2021"\n'
BASE = "pub struct S;\nimpl S { pub fn go(&self) -> u8 { 1 } }\npub fn run(s: &S) -> u8 { s.go() }\n"


def graph_of(make_repo, files, cargo=CARGO):
    return build_graph(make_repo({"Cargo.toml": cargo, **files}), RUST)


def result_of(graph, text="s.go"):
    """("edge", callee) or ("unresolved", reason) for the one call written `text`."""
    calls = [e.callee for e in graph.edges if e.kind == "call" and e.caller == "src/lib:run"]
    refused = [u.reason for u in graph.unresolved if u.text == text and u.caller == "src/lib:run"]
    assert len(calls) + len(refused) == 1, (calls, refused)
    return ("edge", calls[0]) if calls else ("unresolved", refused[0])


def refused(make_repo, source, why, files=None, cargo=CARGO, text="s.go"):
    g = graph_of(make_repo, {"src/lib.rs": source, **(files or {})}, cargo)
    kind, got = result_of(g, text)
    assert kind == "unresolved" and why in got, got
    return got


def test_control_the_call_is_an_edge_to_the_inherent_method(make_repo):
    assert result_of(graph_of(make_repo, {"src/lib.rs": BASE})) == ("edge", "src/lib:S::go")


def test_a_standard_derive_a_standard_macro_and_an_inert_attribute_cost_nothing(make_repo):
    source = "#[derive(Debug, Clone, PartialEq)]\n#[allow(dead_code)]\npub struct T;\n" + BASE.replace("pub fn run(s: &S) -> u8 {", "pub fn run(s: &S) -> u8 { println!(\"{}\", 1);")
    assert result_of(graph_of(make_repo, {"src/lib.rs": source})) == ("edge", "src/lib:S::go")


# ---- an impl the source may hold unseen does not matter; source ripple did not read does (tests/test_rs_method_unread.py has the class)


def test_a_macro_that_may_have_made_an_impl_does_not_stop_the_edge(make_repo):
    """rustc rejects a second inherent method of the name for the type (E0592) and takes the inherent one before every trait's at the
    probe step the edge is for, so an impl a macro made cannot change which function the call finds (probes method_macros, and the
    compiled cases in the verifier's report)."""
    for shape in (
        "pub mod elsewhere { macro_rules! m { () => {}; }\nm!(); }\n",  # an item macro (in another module: a macro in the call's own module may have made a const named `s`, M_PATTERN_ITEM) whose body is read and brings no file in
        "macro_rules! m { () => {}; }\npub fn other() { m!(); }\n",  # a statement macro of a function
        "impl Missing { pub fn x() {} }\n",  # an impl for a type ripple cannot resolve
        "#[derive(Debug)]\npub struct T;\n",
        "pub fn other() { #[derive(Mystery)] struct T; }\n",  # a derive that is not a standard one, in a block: what it makes stays in the block
        "pub fn other() { #[some::attr] fn inner() {} }\n",
    ):
        assert result_of(graph_of(make_repo, {"src/lib.rs": shape + BASE})) == ("edge", "src/lib:S::go"), shape


def test_an_attribute_macro_or_a_derive_at_the_module_level_may_emit_a_file(make_repo):
    for shape in ("#[some::attr]\npub fn other() {}\n", "#[derive(Mystery)]\npub struct T;\n"):
        refused(make_repo, shape + BASE, "did not read")


def test_a_mod_written_in_a_block_is_a_file_ripple_does_not_read(make_repo):
    refused(make_repo, "pub fn other() { mod inner { impl super::S {} } }\n" + BASE, "mod` written in a block")


def test_a_file_the_parser_could_not_read_whole_is_not_all_read(make_repo):
    source = "mod broken;\n" + BASE
    why = refused(make_repo, source, "could not read whole", {"src/broken.rs": "pub fn bad( {\nimpl super::S { pub fn go(self) {} }\n"})
    assert "src/broken.rs" in why


def test_a_mod_that_names_no_file_means_the_crate_is_not_all_read(make_repo):
    why = refused(make_repo, "mod missing;\n" + BASE, "did not read")
    assert "names no file" in why


def test_a_file_of_the_crates_sources_that_no_mod_reaches_may_still_be_built(make_repo):
    why = refused(make_repo, BASE, "did not read", {"src/stray.rs": "impl crate::S { pub fn go(self) {} }\n"})
    assert "src/stray.rs" in why


def test_a_file_outside_the_crates_source_directory_that_no_mod_reaches_costs_nothing(make_repo):
    g = graph_of(make_repo, {"src/lib.rs": BASE, "docs/example.rs": "fn main() {}\n"})
    assert result_of(g) == ("edge", "src/lib:S::go")


def test_source_not_read_in_the_crate_that_defines_the_type_is_enough_when_the_call_is_in_another_crate(make_repo):
    files = {
        "Cargo.toml": '[workspace]\nmembers = ["kit", "app"]\nresolver = "2"\n',
        "kit/Cargo.toml": '[package]\nname = "kit"\nversion = "0.1.0"\nedition = "2021"\n',
        "kit/src/lib.rs": "include!(\"../gen/items.rs\");\npub struct S;\nimpl S { pub fn go(&self) -> u8 { 1 } }\n",
        "kit/gen/items.rs": "pub fn made() {}\n",
        "app/Cargo.toml": '[package]\nname = "app"\nversion = "0.1.0"\nedition = "2021"\n[dependencies]\nkit = { path = "../kit" }\n',
        "app/src/lib.rs": "pub fn run(s: &kit::S) -> u8 { s.go() }\n",
    }
    g = build_graph(make_repo(files), RUST)
    (u,) = [u for u in g.unresolved if u.text == "s.go"]
    assert "did not read" in u.reason and not [e for e in g.edges if e.kind == "call"]


def test_a_macro_in_the_calling_crate_is_enough_when_the_type_is_in_another_crate(make_repo):
    files = {
        "Cargo.toml": '[workspace]\nmembers = ["kit", "app"]\nresolver = "2"\n',
        "kit/Cargo.toml": '[package]\nname = "kit"\nversion = "0.1.0"\nedition = "2021"\n',
        "kit/src/lib.rs": "pub struct S;\nimpl S { pub fn go(&self) -> u8 { 1 } }\n",
        "app/Cargo.toml": '[package]\nname = "app"\nversion = "0.1.0"\nedition = "2021"\n[dependencies]\nkit = { path = "../kit" }\n',
        "app/src/lib.rs": "macro_rules! m { () => {}; }\nm!();\npub fn run(s: &kit::S) -> u8 { s.go() }\n",
    }
    g = build_graph(make_repo(files), RUST)
    (u,) = [u for u in g.unresolved if u.text == "s.go"]  # (the type's path is not read either: a macro of the module may have made a `kit`)
    assert u.reason.startswith(rs.METHOD_PREFIX) and not [e for e in g.edges if e.kind == "call"]


def test_a_crate_that_cannot_name_the_type_does_not_make_the_impls_incomplete(make_repo):
    files = {
        "Cargo.toml": '[workspace]\nmembers = ["kit", "app", "other"]\nresolver = "2"\n',
        "kit/Cargo.toml": '[package]\nname = "kit"\nversion = "0.1.0"\nedition = "2021"\n',
        "kit/src/lib.rs": "pub struct S;\nimpl S { pub fn go(&self) -> u8 { 1 } }\n",
        "app/Cargo.toml": '[package]\nname = "app"\nversion = "0.1.0"\nedition = "2021"\n[dependencies]\nkit = { path = "../kit" }\n',
        "app/src/lib.rs": "pub fn run(s: &kit::S) -> u8 { s.go() }\n",
        "other/Cargo.toml": '[package]\nname = "other"\nversion = "0.1.0"\nedition = "2021"\n',
        "other/src/lib.rs": "macro_rules! m { () => {}; }\nm!();\n",
    }
    g = build_graph(make_repo(files), RUST)
    assert [(e.caller, e.callee) for e in g.edges if e.kind == "call"] == [("app/src/lib:run", "kit/src/lib:S::go")]


# ---- the method may not exist


def test_a_cfg_on_the_method_the_impl_a_module_or_a_function_makes_it_conditional(make_repo):
    for shape in (
        "impl S { #[cfg(a)] pub fn go(&self) -> u8 { 2 } }\n",
        "#[cfg(a)]\nimpl S { pub fn go(&self) -> u8 { 2 } }\n",
        "#[cfg(a)]\nmod m { impl super::S { pub fn go(&self) -> u8 { 2 } } }\n",
        "mod m { #![cfg(a)]\nimpl super::S { pub fn go(&self) -> u8 { 2 } } }\n",
        "#[cfg(a)]\nfn make() { impl S { pub fn go(&self) -> u8 { 2 } } }\n",
        "fn make() { #[cfg(a)] { impl S { pub fn go(&self) -> u8 { 2 } } } }\n",
    ):
        refused(make_repo, shape + BASE.replace("impl S { pub fn go(&self) -> u8 { 1 } }", "impl S { pub fn other(&self) {} }\npub trait Tr { fn go(&self) -> u8; }\nimpl Tr for S { fn go(&self) -> u8 { 1 } }"), "cfg")


def test_a_cfg_in_a_file_module_declared_with_a_cfg_makes_its_impls_conditional(make_repo):
    refused(make_repo, '#[cfg(a)]\nmod extra;\n' + BASE.replace("impl S { pub fn go(&self) -> u8 { 1 } }", ""), "cfg", {"src/extra.rs": "impl crate::S { pub fn go(&self) -> u8 { 2 } }\n"})


def test_two_lets_of_one_name_under_opposite_cfgs_are_two_bindings(make_repo):
    source = "pub struct T;\nimpl T { pub fn go(&self) -> u8 { 2 } }\n" + BASE.replace("{ s.go() }", "{\n    #[cfg(a)] let s: &T = &T;\n    #[cfg(not(a))] let s: &S = s;\n    s.go()\n}")
    refused(make_repo, source, "more than once")


def test_an_attribute_macro_on_the_method_or_its_impl(make_repo):
    for shape in ("impl S { #[some::attr] pub fn go(&self) -> u8 { 1 } }\n", "#[some::attr]\nimpl S { pub fn go(&self) -> u8 { 1 } }\n"):
        refused(make_repo, shape + BASE.replace("impl S { pub fn go(&self) -> u8 { 1 } }\n", ""), "")


# ---- the method is not the one the probe finds first


def test_the_edition_of_a_2015_crate_is_not_read(make_repo):
    refused(make_repo, BASE, "edition 2015", cargo=CARGO.replace('edition = "2021"\n', ""))


def test_a_method_the_impl_for_other_arguments_defines_is_not_the_method(make_repo):
    source = "pub struct W<T>(pub T);\nimpl W<u8> { pub fn go(&self) -> u8 { 1 } }\nimpl W<u16> { pub fn go(&self) -> u8 { 2 } }\n"
    g = graph_of(make_repo, {"src/lib.rs": source + "pub fn run(s: &W<u8>) -> u8 { s.go() }\npub fn other(s: &W<u32>) {}\npub fn gen<T>(s: &W<T>) { s.go(); }\n"})
    assert [(e.caller, e.callee) for e in g.edges if e.kind == "call"] == [("src/lib:run", "src/lib:W::go")]
    assert [u.reason for u in g.unresolved if u.caller == "src/lib:gen"] == [rs.M_INSTANTIATION]


def test_an_impl_with_a_bound_is_not_the_inherent_method_of_a_type_that_may_not_meet_it(make_repo):
    for impl in ("impl<T: Clone> W<T> {", "impl<T> W<T> where T: Clone {", "impl<T: ?Sized> W<T> {"):
        source = f"pub struct W<T>(pub T);\n{impl} pub fn go(&self) -> u8 {{ 1 }} }}\npub fn run(s: &W<u8>) -> u8 {{ s.go() }}\n"
        got = refused(make_repo, source, "bounds")
        assert got == rs.M_BOUNDED


def test_a_type_with_an_unsized_parameter_is_not_matched_by_an_impl_for_every_type(make_repo):
    source = "pub struct W<T: ?Sized>(pub Box<T>);\nimpl<T> W<T> { pub fn go(&self) -> u8 { 1 } }\npub fn run(s: &W<u8>) -> u8 { s.go() }\n"
    assert refused(make_repo, source, "unsized") == rs.M_UNSIZED


def test_a_private_method_of_another_module_is_not_visible(make_repo):
    source = "mod a { pub struct S; impl S { fn go(&self) -> u8 { 1 } } }\npub fn run(s: &a::S) -> u8 { s.go() }\n"
    assert refused(make_repo, source, "not visible") == rs.M_NOT_VISIBLE


def test_a_method_in_a_module_below_the_impl_sees_a_private_method_of_the_impl(make_repo):
    source = "pub struct S;\nimpl S { fn go(&self) -> u8 { 1 } }\nmod sub { pub fn run(s: &super::S) -> u8 { s.go() } }\n"
    g = graph_of(make_repo, {"src/lib.rs": source})
    assert [(e.caller, e.callee) for e in g.edges if e.kind == "call"] == [("src/lib:sub::run", "src/lib:S::go")]


def test_a_receiver_form_the_method_does_not_take_is_found_at_a_later_step(make_repo):
    for params, method in (("s: S", "&self"), ("s: &S", "self"), ("s: &mut S", "&self"), ("s: &S", "&mut self"), ("s: &mut S", "self"), ("s: S", "&mut self")):
        source = f"pub struct S;\nimpl S {{ pub fn go({method}) -> u8 {{ 1 }} }}\npub fn run({params}) -> u8 {{ s.go() }}\n"
        refused(make_repo, source, "later probe step")


def test_each_form_that_matches_is_an_edge(make_repo):
    for params, method in (("s: S", "self"), ("s: &S", "&self"), ("s: &mut S", "&mut self"), ("mut s: S", "mut self"), ("s: &'static S", "&'static self")):
        source = f"pub struct S;\nimpl S {{ pub fn go({method}) -> u8 {{ 1 }} }}\npub fn run({params}) -> u8 {{ s.go() }}\n"
        assert result_of(graph_of(make_repo, {"src/lib.rs": source})) == ("edge", "src/lib:S::go"), (params, method)


def test_a_method_the_type_gets_from_a_trait_or_does_not_have_is_unresolved_never_outside_the_repo(make_repo):
    source = "pub struct S;\npub trait Tr { fn go(&self) -> u8; }\nimpl Tr for S { fn go(&self) -> u8 { 1 } }\npub fn run(s: &S) -> u8 { s.go() }\n"
    assert refused(make_repo, source, "no inherent impl") == rs.M_NO_INHERENT
    g = graph_of(make_repo, {"src/lib.rs": "pub struct S;\npub fn run(s: &S) -> u8 { s.nothing_defines_this() }\n"})
    assert g.external_calls == 0 and g.external_reasons == {}


def test_a_trait_default_method_cannot_say_what_self_is(make_repo):
    source = "pub struct S;\nimpl S { pub fn go(&self) -> u8 { 1 } }\npub trait Tr { fn d(&self) { self.go(); } }\n"
    g = graph_of(make_repo, {"src/lib.rs": source})
    assert [u.reason for u in g.unresolved] == [rs.M_SELF_TRAIT] and not [e for e in g.edges if e.kind == "call"]


def test_an_impl_for_a_generic_type_or_a_reference_says_nothing_of_self(make_repo):
    source = "pub struct S;\nimpl S { pub fn go(&self) -> u8 { 1 } }\npub trait Tr { fn t(&self) -> u8; }\nimpl<T> Tr for T { fn t(&self) -> u8 { self.go() } }\nimpl Tr for &S { fn t(&self) -> u8 { self.go() } }\n"
    g = graph_of(make_repo, {"src/lib.rs": source})
    assert {u.reason for u in g.unresolved} <= {rs.M_SELF_TYPE, rs.M_GENERIC} and len(g.unresolved) == 2 and not [e for e in g.edges if e.kind == "call"]


def test_a_call_inside_an_item_under_an_attribute_macro_is_unresolved(make_repo):
    source = BASE.replace("pub fn run", "#[some::attr]\npub fn run")
    g = graph_of(make_repo, {"src/lib.rs": source})
    assert not [e for e in g.edges if e.kind == "call"]


# ---- which binding a name is


def test_a_name_bound_twice_in_a_function_is_not_one_binding(make_repo):
    for body in (
        "let s: &S = &S; s.go()",
        "let f = |s: &S| s.go(); f(&S)",
        "match Some(&S) { Some(s) => s.go(), None => 0 }",
        "let mut t = 0; for s in [&S] { t += s.go(); } t",
    ):
        source = f"pub struct S;\nimpl S {{ pub fn go(&self) -> u8 {{ 1 }} }}\npub fn run(s: &S) -> u8 {{ {body} }}\n"
        g = graph_of(make_repo, {"src/lib.rs": source})
        assert not [e for e in g.edges if e.kind == "call"], body
        assert rs.M_REBOUND in {u.reason for u in g.unresolved}, body


def test_a_block_item_of_the_name_is_what_the_name_is_in_the_block(make_repo):
    source = "pub struct S;\npub struct B;\nimpl S { pub fn go(&self) -> u8 { 1 } }\nimpl B { pub fn go(&self) -> u8 { 2 } }\npub fn run(s: &S) -> u8 { const s: B = B; s.go() }\n"
    g = graph_of(make_repo, {"src/lib.rs": source})
    assert [u.reason for u in g.unresolved if u.text == "s.go"] == [rs.M_SHADOWED]


def test_a_let_is_in_effect_from_after_its_statement_to_the_end_of_its_block(make_repo):
    base = "pub struct S;\nimpl S { pub fn go(&self) -> u8 { 1 } }\n"
    g = graph_of(make_repo, {"src/lib.rs": base + "pub fn run() -> u8 { let s: &S = &S; s.go() }\n"})
    assert [(e.caller, e.callee) for e in g.edges if e.kind == "call"] == [("src/lib:run", "src/lib:S::go")]
    g = graph_of(make_repo, {"src/lib.rs": base + "pub fn s() {}\npub fn run() -> u8 { { let s: &S = &S; } 0 }\n"})
    source = base + "pub fn s() {}\npub trait Callable { fn go(&self) -> u8; }\nimpl<F: Fn()> Callable for F { fn go(&self) -> u8 { 2 } }\npub fn run() -> u8 {\n    { let s: &S = &S; s.go(); }\n    s.go()\n}\n"
    g = graph_of(make_repo, {"src/lib.rs": source})
    outside = [u.reason for u in g.unresolved if u.text == "s.go" and u.line == 8]
    assert outside == [rs.M_OUT_OF_SCOPE]
    assert [(e.callee, e.line) for e in g.edges if e.kind == "call"] == [("src/lib:S::go", 7)]


def test_a_macro_invoked_as_a_statement_may_bind_the_name(make_repo):
    source = "pub struct S;\npub struct B;\nimpl S { pub fn go(&self) -> u8 { 1 } }\nimpl B { pub fn go(&self) -> u8 { 2 } }\nmacro_rules! bind { ($n:ident) => { let $n: &B = &B; }; }\npub fn run(s: &S) -> u8 { bind!(s); s.go() }\n"
    g = graph_of(make_repo, {"src/lib.rs": source})
    assert not [e for e in g.edges if e.kind == "call"]
    assert [u.reason for u in g.unresolved if u.text == "s.go"] == [rs.M_SHADOWED]
