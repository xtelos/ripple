"""A path call into a trait impl that may not be built, or into an impl whose header ripple could not read, draws no edge on a guess.

1. A conditional trait impl. `S::g()` finds the impl's function only where the impl is built. Where it is off (`#[cfg(not(feature = "a"))]`
   on the other side), the call finds whatever else gives `g` to `S`: a foreign crate's extension trait with a blanket impl, a provided method of
   a standard trait, a repo trait ripple does not see an impl of. ripple cannot close that set, so rs_indexer.Index.assoc_traits answers
   unresolved (CFG_UNKNOWN) for a trait impl, or a method of one, that the call is not also under, as inherent_hit does for an inherent function.
   Compiled and run by hand (a scratch crate, not committed): `use extblanket::Ext` under `#[cfg(not(feature = "a"))]`, `trait Tr { fn g() }`,
   `#[cfg(feature = "a")] impl Tr for S`, `pub fn go() { S::g() }`: `cargo run` prints `go=ext-blanket` with `a` off and `go=cfg-trait` with `a` on.
   An impl that is not conditional, and one the call shares its cfg with (the same attribute on a node around both, or a plain `#[cfg]` of the same
   tokens), keep the edge: the call is built only where the impl is.

2. A header ripple could not read. `impl<u8> W<[u8; 2]>` declares a generic parameter named `u8`, which is legal; tree-sitter parses it as an ERROR
   node with no `type_parameters`, so ripple read `u8` as the primitive and the impl as one for `W<[u8; 2]>` only. Compiled and run by hand: with
   `impl Tr for W<[u16; 2]> {}` the call `W::<[u16; 2]>::f()` prints `inherent` (the impl is for every `[T; 2]`), and `impl<u8> W<(u8)>` with
   `impl Tr for W<u16> {}` and `W::<u16>::f()` prints `inherent` too; ripple drew an edge to the trait's `f` for both. An impl whose header the parser did
   not read whole is compared with no type arguments at all (Index.relation answers unknown), so the answer is unresolved.
"""

import pytest

pytest.importorskip("tree_sitter_rust")

from ripple import rs_indexer as rs  # noqa: E402
from ripple.indexer import build_graph  # noqa: E402
from ripple.profile import RUST  # noqa: E402

FEATURES = '[package]\nname = "app"\nversion = "0.1.0"\nedition = "2021"\n[features]\na = []\nb = []\n'
EXT_CARGO = '[package]\nname = "extblanket"\nversion = "0.1.0"\nedition = "2021"\n'
EXT_LIB = 'pub trait Ext { fn g() -> &\'static str { "ext-blanket" } }\nimpl<T> Ext for T {}\n'


def result_of(make_repo, source, caller="src/lib:go", text=None, cargo=FEATURES, files=None):
    """("edge", callee id) or ("unresolved", reason) for the one call in `caller` (the one written `text`, when given)."""
    graph = build_graph(make_repo({"Cargo.toml": cargo, "src/lib.rs": source, **(files or {})}), RUST)
    edges = [e.callee for e in graph.edges if e.kind == "call" and e.caller == caller]
    refused = [u.reason for u in graph.unresolved if u.caller == caller and (text is None or u.text == text)]
    assert len(edges) + len(refused) == 1, (edges, refused)
    return ("edge", edges[0]) if edges else ("unresolved", refused[0])


def refused(make_repo, source, why, **kwargs):
    kind, got = result_of(make_repo, source, **kwargs)
    assert kind == "unresolved" and got == why, got


# ---- 1. a conditional trait impl

CFG_IMPL = """
pub trait Tr { fn g() -> &'static str; }
pub struct S;
#[cfg(feature = "a")]
impl Tr for S { fn g() -> &'static str { "cfg-trait" } }
pub fn go() -> &'static str { S::g() }
"""


def test_a_conditional_trait_impl_the_call_is_not_under_is_unresolved(make_repo):
    refused(make_repo, CFG_IMPL, rs.CFG_UNKNOWN)


def test_the_foreign_extension_trait_of_the_report_is_unresolved(make_repo, tmp_path_factory):
    """The crate that cargo ran: the call is `ext-blanket` with `a` off, and the impl's `cfg-trait` with `a` on. The extension crate is a path
    dependency outside the repo, as a foreign crate is: ripple reads no blanket impl of it."""
    outside = tmp_path_factory.mktemp("extblanket")
    (outside / "src").mkdir()
    (outside / "Cargo.toml").write_text(EXT_CARGO)
    (outside / "src" / "lib.rs").write_text(EXT_LIB)
    cargo = FEATURES + f'[dependencies]\nextblanket = {{ path = "{outside.as_posix()}" }}\n'
    source = '#[cfg(not(feature = "a"))]\nuse extblanket::Ext;\n' + CFG_IMPL
    refused(make_repo, source, rs.CFG_UNKNOWN, cargo=cargo)


def test_a_conditional_impl_that_gives_a_provided_default_is_unresolved(make_repo):
    """The impl writes no `g`: the trait's default is what the impl gives, and it is off with the impl."""
    source = """
    pub trait Tr { fn g() -> &'static str { "default" } }
    pub struct S;
    #[cfg(feature = "a")]
    impl Tr for S {}
    pub fn go() -> &'static str { S::g() }
    """
    refused(make_repo, source, rs.CFG_UNKNOWN)


def test_a_conditional_impl_in_a_function_body_is_unresolved(make_repo):
    source = """
    pub trait Tr { fn g() -> &'static str; }
    pub struct S;
    pub fn setup() {
        #[cfg(feature = "a")]
        impl Tr for S { fn g() -> &'static str { "cfg-trait" } }
    }
    pub fn go() -> &'static str { S::g() }
    """
    refused(make_repo, source, rs.CFG_UNKNOWN)


def test_a_conditional_impl_is_unresolved_through_self_in_a_method_of_the_type(make_repo):
    """`Self::g()` from an unconditional method of the type is no more under the impl's cfg than a free function is."""
    source = """
    pub trait Tr { fn g() -> &'static str; }
    pub struct S;
    impl S { pub fn go() -> &'static str { Self::g() } }
    #[cfg(feature = "a")]
    impl Tr for S { fn g() -> &'static str { "cfg-trait" } }
    """
    refused(make_repo, source, rs.CFG_UNKNOWN, caller="src/lib:S::go")


def test_an_unconditional_trait_impl_keeps_its_edge(make_repo):
    source = CFG_IMPL.replace('#[cfg(feature = "a")]\n', "")
    assert result_of(make_repo, source) == ("edge", "src/lib:<S as Tr>::g")


def test_an_unconditional_impl_that_gives_a_provided_default_keeps_its_edge(make_repo):
    source = """
    pub trait Tr { fn g() -> &'static str { "default" } }
    pub struct S;
    impl Tr for S {}
    pub fn go() -> &'static str { S::g() }
    """
    assert result_of(make_repo, source) == ("edge", "src/lib:Tr::g")


def test_the_call_under_the_same_cfg_as_the_impl_keeps_its_edge(make_repo):
    """The call is built only where `a` is on, and the impl is built there too: it is the impl's function that runs."""
    source = CFG_IMPL.replace("pub fn go()", '#[cfg(feature = "a")]\npub fn go()')
    assert result_of(make_repo, source) == ("edge", "src/lib:<S as Tr>::g")


def test_a_call_inside_the_conditional_impl_itself_keeps_its_edge(make_repo):
    source = """
    pub trait Tr { fn g() -> &'static str; fn h() -> &'static str; }
    pub struct S;
    #[cfg(feature = "a")]
    impl Tr for S {
        fn g() -> &'static str { "cfg-trait" }
        fn h() -> &'static str { Self::g() }
    }
    """
    assert result_of(make_repo, source, caller="src/lib:<S as Tr>::h") == ("edge", "src/lib:<S as Tr>::g")


def test_the_same_cfg_on_the_module_that_holds_the_call_keeps_its_edge(make_repo):
    source = """
    pub trait Tr { fn g() -> &'static str; }
    pub struct S;
    #[cfg(feature = "a")]
    mod imp {
        impl crate::Tr for crate::S { fn g() -> &'static str { "cfg-trait" } }
    }
    #[cfg(feature = "a")]
    mod user {
        use crate::{S, Tr};
        pub fn go() -> &'static str { S::g() }
    }
    """
    assert result_of(make_repo, source, caller="src/lib:user::go") == ("edge", "src/lib:imp::<S as Tr>::g")


def test_another_cfg_on_the_call_does_not_share_the_impls(make_repo):
    """Under `b` the call may exist with the impl (`a`) off."""
    source = CFG_IMPL.replace("pub fn go()", '#[cfg(feature = "b")]\npub fn go()')
    refused(make_repo, source, rs.CFG_UNKNOWN)


# ---- 1b. a conditional default method of the trait itself
#
# The impl writes no `g` and is not conditional: the trait's default is what it gives, and the default is under `#[cfg(feature = "a")]` on the trait's
# own function. Compiled and run by hand (scratch crate, not committed; `use extblanket::Ext` under `not(feature = "a")`): `cargo run` prints
# `go=ext-blanket` with `a` off and `go=decl-default` with `a` on. And with the whole trait and its impl under `#[cfg(feature = "a")]` (the call
# unconditional): `go=ext-blanket` and `go=decl-default` again.

CFG_DEFAULT = """
pub trait Tr {
    #[cfg(feature = "a")]
    fn g() -> &'static str { "decl-default" }
}
pub struct S;
impl Tr for S {}
pub fn go() -> &'static str { S::g() }
"""


def test_a_default_method_under_a_cfg_the_call_is_not_under_is_unresolved(make_repo):
    refused(make_repo, CFG_DEFAULT, rs.CFG_UNKNOWN)


def test_the_foreign_extension_trait_beside_a_conditional_default_is_unresolved(make_repo, tmp_path_factory):
    outside = tmp_path_factory.mktemp("extblanket")
    (outside / "src").mkdir()
    (outside / "Cargo.toml").write_text(EXT_CARGO)
    (outside / "src" / "lib.rs").write_text(EXT_LIB)
    cargo = FEATURES + f'[dependencies]\nextblanket = {{ path = "{outside.as_posix()}" }}\n'
    source = '#[cfg(not(feature = "a"))]\nuse extblanket::Ext;\n' + CFG_DEFAULT
    refused(make_repo, source, rs.CFG_UNKNOWN, cargo=cargo)


def test_a_conditional_default_the_call_is_under_the_same_cfg_as_keeps_its_edge(make_repo):
    source = CFG_DEFAULT.replace("pub fn go()", '#[cfg(feature = "a")]\npub fn go()')
    assert result_of(make_repo, source) == ("edge", "src/lib:Tr::g")


def test_a_conditional_default_the_call_is_under_the_same_predicate_of_keeps_its_edge(make_repo):
    """Another attribute on a module around the call with the identical tokens (cfg_covers): the call is built only where `a` is on."""
    source = """
    pub trait Tr {
        #[cfg(feature = "a")]
        fn g() -> &'static str { "decl-default" }
    }
    pub struct S;
    impl Tr for S {}
    #[cfg(feature = "a")]
    mod user {
        use crate::{S, Tr};
        pub fn go() -> &'static str { S::g() }
    }
    """
    assert result_of(make_repo, source, caller="src/lib:user::go") == ("edge", "src/lib:Tr::g")


def test_a_conditional_default_called_under_another_cfg_is_unresolved(make_repo):
    source = CFG_DEFAULT.replace("pub fn go()", '#[cfg(feature = "b")]\npub fn go()')
    refused(make_repo, source, rs.CFG_UNKNOWN)


def test_a_default_under_a_cfg_attr_that_carries_a_cfg_is_unresolved(make_repo):
    source = CFG_DEFAULT.replace('#[cfg(feature = "a")]', '#[cfg_attr(feature = "a", cfg(feature = "b"))]')
    kind, got = result_of(make_repo, source)
    assert kind == "unresolved", got


def test_the_whole_trait_and_its_impl_under_a_cfg_is_unresolved_for_an_unconditional_call(make_repo):
    """The conditional trait DECLARATION is no separate path: the impl of it is conditional too (or the crate does not build with `a` off), and the
    impl's own cfg is what refuses the call (compiled: `ext-blanket` with `a` off)."""
    source = """
    #[cfg(feature = "a")]
    pub trait Tr { fn g() -> &'static str { "decl-default" } }
    pub struct S;
    #[cfg(feature = "a")]
    impl Tr for S {}
    pub fn go() -> &'static str { S::g() }
    """
    refused(make_repo, source, rs.CFG_UNKNOWN)


def test_the_whole_trait_and_its_impl_under_a_cfg_keep_the_edge_for_a_call_under_it(make_repo):
    source = """
    #[cfg(feature = "a")]
    pub trait Tr { fn g() -> &'static str { "decl-default" } }
    pub struct S;
    #[cfg(feature = "a")]
    impl Tr for S {}
    #[cfg(feature = "a")]
    pub fn go() -> &'static str { S::g() }
    """
    assert result_of(make_repo, source) == ("edge", "src/lib:Tr::g")


def test_a_conditional_inherent_function_is_still_unresolved_the_same_way(make_repo):
    """PR 44's rule for the inherent arm is untouched."""
    source = """
    pub struct S;
    #[cfg(feature = "a")]
    impl S { pub fn g() -> &'static str { "cfg-inherent" } }
    pub fn go() -> &'static str { S::g() }
    """
    refused(make_repo, source, rs.CFG_UNKNOWN)


# ---- 2. a header ripple could not read

TRAIT_AND_W = "pub trait Tr { fn f() -> &'static str { \"trait\" } }\npub struct W<T: ?Sized>(pub core::marker::PhantomData<T>);\n"


def test_a_generic_parameter_named_u8_in_an_array_is_unresolved(make_repo):
    source = TRAIT_AND_W + """
    impl<u8> W<[u8; 2]> { pub fn f() -> &'static str { "inherent" } }
    impl Tr for W<[u16; 2]> {}
    pub fn go() -> &'static str { W::<[u16; 2]>::f() }
    """
    refused(make_repo, source, rs.IMPL_HEADER_UNREAD)


def test_a_generic_parameter_named_u8_in_parentheses_is_unresolved(make_repo):
    source = TRAIT_AND_W + """
    impl<u8> W<(u8)> { pub fn f() -> &'static str { "inherent" } }
    impl Tr for W<u16> {}
    pub fn go() -> &'static str { W::<u16>::f() }
    """
    refused(make_repo, source, rs.IMPL_HEADER_UNREAD)


def test_the_primitive_the_parameter_is_named_after_is_unresolved_too(make_repo):
    """`W::<u8>::f()` is the inherent function whichever way the parameter is read; the answer is still not drawn from the primitive."""
    source = TRAIT_AND_W + """
    impl<u8> W<(u8)> { pub fn f() -> &'static str { "inherent" } }
    impl Tr for W<u8> {}
    pub fn go() -> &'static str { W::<u8>::f() }
    """
    refused(make_repo, source, rs.IMPL_HEADER_UNREAD)


@pytest.mark.parametrize("name", ["bool", "char", "str", "f32", "i64", "usize"])
def test_any_primitive_name_as_a_generic_parameter_is_unresolved(make_repo, name):
    source = TRAIT_AND_W + f"""
    impl<{name}> W<({name})> {{ pub fn f() -> &'static str {{ "inherent" }} }}
    impl Tr for W<u16> {{}}
    pub fn go() -> &'static str {{ W::<u16>::f() }}
    """
    refused(make_repo, source, rs.IMPL_HEADER_UNREAD)


def test_a_trait_impl_with_an_unread_header_is_unresolved(make_repo):
    """The candidate is a trait impl this time: the parameter `u8` makes it an impl for every `[T; 2]`, which the call's `[u16; 2]` is."""
    source = """
    pub trait Tr { fn f() -> &'static str; }
    pub struct W<T: ?Sized>(pub core::marker::PhantomData<T>);
    impl<u8> Tr for W<[u8; 2]> { fn f() -> &'static str { "generic" } }
    pub fn go() -> &'static str { W::<[u16; 2]>::f() }
    """
    refused(make_repo, source, rs.IMPL_HEADER_UNREAD)


def test_a_readable_generic_parameter_keeps_its_answer(make_repo):
    """Control: `impl<T> W<T>` parses, `T` is a generic, and the inherent function covers every `W<_>`."""
    source = TRAIT_AND_W + """
    impl<T> W<T> { pub fn f() -> &'static str { "inherent" } }
    impl Tr for W<u16> {}
    pub fn go() -> &'static str { W::<u16>::f() }
    """
    assert result_of(make_repo, source) == ("edge", "src/lib:W::f")


def test_an_impl_for_a_primitive_argument_keeps_its_answer(make_repo):
    """Control: no generic parameter, no error. `impl W<u8>` is for `W<u8>` and the call `W::<u8>::f()` is an edge to it."""
    source = TRAIT_AND_W + """
    impl W<u8> { pub fn f() -> &'static str { "inherent" } }
    impl Tr for W<u16> {}
    pub fn go() -> &'static str { W::<u8>::f() }
    """
    assert result_of(make_repo, source) == ("edge", "src/lib:W::f")


def test_an_unread_header_costs_only_the_calls_to_its_own_type(make_repo):
    """The other type `V` has no unread header: its call stays an edge."""
    source = TRAIT_AND_W + """
    impl<u8> W<[u8; 2]> { pub fn f() -> &'static str { "inherent" } }
    pub struct V;
    impl V { pub fn f() -> &'static str { "v" } }
    pub fn go() -> &'static str { V::f() }
    """
    assert result_of(make_repo, source) == ("edge", "src/lib:V::f")


# ---- 2b. the body of an impl whose header ripple could not read
#
# Every reader of the impl's generics: `relation` (a candidate), `resolve_impl` (the self and trait types), `self_path` (`Self::f`), the type arguments
# a path in a body writes (`W::<[u8; 2]>::f()`), the first segment of a path (a parameter of the header), qualified paths, and a method call's receiver
# (`self_type`, `typed_bindings`). The first answers unknown, the second marks a bare self type lost, the paths of a body are refused at the call
# (classify_callee), and the method call is refused by method_taint (a file the parser read with errors), before its receiver's type is read.
# Compiled and run by hand (scratch crates, not committed): s1 prints `go=h-u16` (`Self::h()` in `impl<u8> W<[u8; 2]> where W<[u8; 2]>: Tr`, called
# on `W<[u16; 2]>`), s2 prints `go=h-u16` (the same written `W::<[u8; 2]>::h()`); both were an edge to the `[u8; 2]` impl's `h`.

TRAIT_H = """
pub trait Tr { fn h() -> &'static str; }
pub struct W<T: ?Sized>(pub core::marker::PhantomData<T>);
impl Tr for W<[u8; 2]> { fn h() -> &'static str { "h-u8" } }
impl Tr for W<[u16; 2]> { fn h() -> &'static str { "h-u16" } }
#[allow(non_camel_case_types)]
impl<u8> W<[u8; 2]> where W<[u8; 2]>: Tr { pub fn g() -> &'static str { BODY } }
pub fn go() -> &'static str { W::<[u16; 2]>::g() }
"""


@pytest.mark.parametrize("written", ["Self::h()", "W::<[u8; 2]>::h()", "<W<[u8; 2]> as Tr>::h()", "<Self as Tr>::h()"])
def test_a_path_in_the_body_of_an_unread_impl_is_unresolved(make_repo, written):
    source = TRAIT_H.replace("BODY", written)
    kind, got = result_of(make_repo, source, caller="src/lib:W::g")
    assert (kind, got) == ("unresolved", rs.IMPL_HEADER_UNREAD)


def test_a_path_whose_first_segment_may_be_a_parameter_of_the_header_is_unresolved(make_repo):
    """`T` is in the header's error as far as ripple can tell: it is not read as the repo's own `struct T`."""
    source = """
    pub struct T;
    impl T { pub fn f() -> &'static str { "t" } }
    pub struct W<X>(pub X);
    #[allow(non_camel_case_types)]
    impl<u8, T> W<(u8, T)> { pub fn g() -> &'static str { T::f() } }
    """
    kind, got = result_of(make_repo, source, caller="src/lib:W::g")
    assert (kind, got) == ("unresolved", rs.IMPL_HEADER_UNREAD)


def test_a_plain_call_in_the_body_of_an_unread_impl_keeps_its_edge(make_repo):
    """A one-segment call names a function (a value): the header's parameters are types."""
    source = """
    pub fn helper() -> &'static str { "helper" }
    pub struct W<T: ?Sized>(pub core::marker::PhantomData<T>);
    #[allow(non_camel_case_types)]
    impl<u8> W<[u8; 2]> { pub fn g() -> &'static str { helper() } }
    """
    assert result_of(make_repo, source, caller="src/lib:W::g") == ("edge", "src/lib:helper")


def test_a_method_call_in_the_body_of_an_unread_impl_is_unresolved(make_repo):
    source = """
    pub struct W<T>(pub T);
    #[allow(non_camel_case_types)]
    impl<u8> W<[u8; 2]> {
        pub fn h(&self) -> u8 { 1 }
        pub fn g(&self) -> u8 { self.h() }
    }
    """
    kind, got = result_of(make_repo, source, caller="src/lib:W::g")
    assert kind == "unresolved" and "could not read whole" in got, got


def test_a_bare_self_type_of_an_unread_header_may_be_a_blanket_impl(make_repo):
    """`impl<u8> Tr for u8` is an impl for every type (compiled: `S::f()` prints `blanket`): no table shows it, so no path call is an edge."""
    source = """
    pub trait Tr { fn f() -> &'static str; }
    #[allow(non_camel_case_types)]
    impl<u8> Tr for u8 { fn f() -> &'static str { "blanket" } }
    pub struct S;
    impl Tr for S { fn f() -> &'static str { "s" } }
    pub fn go() -> &'static str { S::f() }
    """
    kind, got = result_of(make_repo, source)
    assert kind == "unresolved" and got.startswith(rs.IMPLS_INCOMPLETE.format("the impl in")) and "could not read whole" in got, got


def test_a_header_with_a_missing_node_is_unread_too(make_repo):
    """`impl W<u8> where u8: {..}` is legal and parses with a MISSING node (the bound). Compiled: `W::<u8>::f()` prints `inherent`, so the edge the
    old reading drew was right: the refusal is a precision cost, the price of reading a header the parser did not read whole as unknown."""
    source = TRAIT_AND_W + """
    impl W<u8> where u8: { pub fn f() -> &'static str { "inherent" } }
    impl Tr for W<u16> {}
    pub fn go() -> &'static str { W::<u8>::f() }
    """
    refused(make_repo, source, rs.IMPL_HEADER_UNREAD)


# ---- 3. a trait that is in scope here only through an import under a `#[cfg]`
#
# `lookup` of a name that only a conditional import gives is that import (right for "what does the name mean", wrong for "is the trait in scope"),
# so trait_visible said yes. Compiled and run by hand (scratch crates, not committed; `extblanket` is a foreign crate with `impl<T> Ext for T`, `fn g`):
#   v1: `#[cfg(feature = "a")] use m::Tr;` with `#[cfg(not(feature = "a"))] use extblanket::Ext;` and `S::g()`: `ext-blanket` with `a` off, `tr-default` on.
#   v2: the same with `impl Tr for S { fn g }` written in m: `ext-blanket` off, `tr-impl` on.
#   c1: an unconditional `use m::Tr;`: `tr-default`. c2: the cfg'd import and the call under `#[cfg(feature = "a")]` too: `tr-default` with `a`
#   (the program without `a` has its own call, to `ext-blanket`). c3: an unconditional `use m::Tr;` and a `#[cfg(feature = "a")] use` in the body of the
#   calling function: `tr-default` both ways.

IN_M = """
mod m {
    pub trait Tr { fn g() -> &'static str { "tr-default" } }
    impl Tr for super::S {}
}
pub struct S;
"""
IN_M_OVERRIDE = IN_M.replace("impl Tr for super::S {}", 'impl Tr for super::S { fn g() -> &\'static str { "tr-impl" } }')


def foreign(tmp_path_factory):
    outside = tmp_path_factory.mktemp("extblanket")
    (outside / "src").mkdir()
    (outside / "Cargo.toml").write_text(EXT_CARGO)
    (outside / "src" / "lib.rs").write_text(EXT_LIB)
    return FEATURES + f'[dependencies]\nextblanket = {{ path = "{outside.as_posix()}" }}\n'


@pytest.mark.parametrize("module, callee", [(IN_M, "src/lib:m::Tr::g"), (IN_M_OVERRIDE, "src/lib:m::<S as Tr>::g")], ids=["default", "override"])
def test_a_trait_imported_under_a_cfg_the_call_is_not_under_is_unresolved(make_repo, tmp_path_factory, module, callee):
    source = module + '#[cfg(feature = "a")]\nuse m::Tr;\n#[cfg(not(feature = "a"))]\nuse extblanket::Ext;\npub fn go() -> &\'static str { S::g() }\n'
    refused(make_repo, source, rs.CFG_UNKNOWN, cargo=foreign(tmp_path_factory))


@pytest.mark.parametrize("module, callee", [(IN_M, "src/lib:m::Tr::g"), (IN_M_OVERRIDE, "src/lib:m::<S as Tr>::g")], ids=["default", "override"])
def test_an_unconditional_import_keeps_its_edge(make_repo, module, callee):
    source = module + "use m::Tr;\npub fn go() -> &'static str { S::g() }\n"
    assert result_of(make_repo, source) == ("edge", callee)


@pytest.mark.parametrize("module, callee", [(IN_M, "src/lib:m::Tr::g"), (IN_M_OVERRIDE, "src/lib:m::<S as Tr>::g")], ids=["default", "override"])
def test_an_import_under_the_cfg_the_call_is_under_keeps_its_edge(make_repo, module, callee):
    source = module + '#[cfg(feature = "a")]\nuse m::Tr;\n#[cfg(feature = "a")]\npub fn go() -> &\'static str { S::g() }\n'
    assert result_of(make_repo, source) == ("edge", callee)


def test_an_import_under_another_cfg_than_the_calls_is_unresolved(make_repo):
    source = IN_M + '#[cfg(feature = "a")]\nuse m::Tr;\n#[cfg(feature = "b")]\npub fn go() -> &\'static str { S::g() }\n'
    refused(make_repo, source, rs.CFG_UNKNOWN)


def test_a_trait_written_by_the_impl_the_call_is_in_keeps_its_edge(make_repo):
    """w2 compiled: `impl m::Tr for Z { fn g() { S::g() } }` with the only `use m::Tr;` under `#[cfg(feature = "a")]` prints `tr-default` with and
    without `a`: the trait an impl writes is in scope in its body."""
    source = """
    mod m { pub trait Tr { fn g() -> &'static str { "tr-default" } } }
    pub struct S;
    pub struct Z;
    impl m::Tr for S {}
    #[cfg(feature = "a")]
    use m::Tr;
    impl m::Tr for Z { fn g() -> &'static str { S::g() } }
    """
    assert result_of(make_repo, source, caller="src/lib:<Z as Tr>::g") == ("edge", "src/lib:m::Tr::g")


def test_a_trait_declared_in_the_calls_own_scope_keeps_its_edge(make_repo):
    source = "pub trait Tr { fn g() -> &'static str { \"tr-default\" } }\npub struct S;\nimpl Tr for S {}\npub fn go() -> &'static str { S::g() }\n"
    assert result_of(make_repo, source) == ("edge", "src/lib:Tr::g")


def test_a_trait_in_scope_unconditionally_and_by_a_conditional_import_is_still_unresolved(make_repo):
    """c3 compiled: in scope both ways, `tr-default` with and without `a`. ripple's lookup refuses a name found under a cfg beside another way
    (CFG_UNKNOWN, as on the base): an edge lost, not drawn. Pinned as the cost it is."""
    source = IN_M + 'use m::Tr;\npub fn go() -> &\'static str {\n    #[cfg(feature = "a")]\n    use crate::m::Tr;\n    S::g()\n}\n'
    refused(make_repo, source, rs.TRAIT_NOT_VISIBLE)


# ---- 3b. the same through a re-export or a glob, and the value namespace
#
# Every hop from the call's scope to the trait's declaration counts (Index.reach_trait). Compiled and run by hand (scratch crates, not committed;
# `cargo run` prints the answer with no feature, then with `--features a`; `extblanket` as above):
#   w1: the feature-gated prelude, `pub mod prelude { #[cfg(feature = "a")] pub use crate::m::Tr; }` with an unconditional `use prelude::*;` and
#       `#[cfg(not(feature = "a"))] use extblanket::Ext;`: `ext-blanket`, `tr-default`.
#   w2: an unconditional `use r::Tr;` where `mod r` re-exports `crate::m::Tr` under `a` and `extblanket::Ext as Tr` under `not(a)`: `ext-blanket`, `tr-default`.
#   w3: v1's cfg'd `use m::Tr;` and a call in `fn go() { fn Tr() {} S::g() }` (a value item of the trait's name stops no lookup of a type):
#       `ext-blanket`, `tr-default`.
#   k1: an unconditional prelude with `use prelude::*;`: `tr-default` both ways. k2: two unconditional re-export hops: `tr-default` both ways.
#   k3: two globs, one of an unconditional re-export and one of a conditional: `tr-default` both ways. k4: an unconditional `use m::Tr;` beside the
#   glob of a conditional prelude: `tr-default` both ways. k6, k7: the conditional re-export reached by an unconditional glob or use, and the call
#   under `#[cfg(feature = "a")]`: `tr-default` with `a` (without it there is no `go`). k8: a glob cycle (`pub use super::*;`) beside the glob that gives it: `tr-default`.

W1 = IN_M + """
pub mod prelude {
    #[cfg(feature = "a")]
    pub use crate::m::Tr;
}
use prelude::*;
#[cfg(not(feature = "a"))]
use extblanket::Ext;
pub fn go() -> &'static str { S::g() }
"""
W2 = IN_M + """
pub mod r {
    #[cfg(feature = "a")]
    pub use crate::m::Tr;
    #[cfg(not(feature = "a"))]
    pub use extblanket::Ext as Tr;
}
use r::Tr;
pub fn go() -> &'static str { S::g() }
"""
W3 = IN_M + """
#[cfg(feature = "a")]
use m::Tr;
#[cfg(not(feature = "a"))]
use extblanket::Ext;
#[allow(non_snake_case, dead_code)]
pub fn go() -> &'static str {
    fn Tr() {}
    S::g()
}
"""


@pytest.mark.parametrize("source", [W1, W2, W3], ids=["w1 gated prelude by a glob", "w2 gated re-export by a use", "w3 value item of the name"])
def test_a_trait_reached_through_a_conditional_hop_is_unresolved(make_repo, tmp_path_factory, source):
    refused(make_repo, source, rs.CFG_UNKNOWN, cargo=foreign(tmp_path_factory))


K1 = IN_M + "pub mod prelude { pub use crate::m::Tr; }\nuse prelude::*;\npub fn go() -> &'static str { S::g() }\n"
K2 = IN_M + "pub mod prelude { pub use crate::m::Tr; }\npub mod p2 { pub use crate::prelude::Tr; }\nuse p2::Tr;\npub fn go() -> &'static str { S::g() }\n"
K3 = IN_M + (
    'pub mod p1 { pub use crate::m::Tr; }\npub mod p2 { #[cfg(feature = "a")] pub use crate::m::Tr; }\n'
    "use p1::*;\nuse p2::*;\npub fn go() -> &'static str { S::g() }\n"
)
K4 = IN_M + 'pub mod prelude { #[cfg(feature = "a")] pub use crate::m::Tr; }\nuse m::Tr;\nuse prelude::*;\npub fn go() -> &\'static str { S::g() }\n'
K6 = IN_M + 'pub mod prelude { #[cfg(feature = "a")] pub use crate::m::Tr; }\nuse prelude::*;\n#[cfg(feature = "a")]\npub fn go() -> &\'static str { S::g() }\n'
K8 = IN_M + "pub mod p { pub use super::*; }\npub mod q { pub use crate::m::Tr; }\nuse p::*;\nuse q::*;\npub fn go() -> &'static str { S::g() }\n"
K7 = IN_M +'pub mod r { #[cfg(feature = "a")] pub use crate::m::Tr; }\nuse r::Tr;\n#[cfg(feature = "a")]\npub fn go() -> &\'static str { S::g() }\n'


@pytest.mark.parametrize(
    "source",
    [K1, K2, K3, K4, K6, K7, K8],
    ids=[
        "k1 unconditional prelude", "k2 two unconditional hops", "k3 two globs, one way unconditional", "k4 explicit use beside a gated glob",
        "k6 gated glob, call under the cfg", "k7 gated use, call under the cfg", "k8 a glob cycle beside the glob that gives the trait",
    ],
)
def test_a_trait_reached_through_hops_the_call_is_sure_of_keeps_its_edge(make_repo, source):
    assert result_of(make_repo, source) == ("edge", "src/lib:m::Tr::g")


def test_a_gated_prelude_behind_a_gated_glob_is_unresolved_by_the_lookup(make_repo):
    """k5: the glob is under `#[cfg(feature = "a")]` too. The lookup refuses a name that a conditional glob gives (as on the base), a cost: the
    call is under the same cfg, and the edge is right where it exists."""
    source = IN_M + 'pub mod prelude { #[cfg(feature = "a")] pub use crate::m::Tr; }\n#[cfg(feature = "a")]\nuse prelude::*;\n#[cfg(feature = "a")]\npub fn go() -> &\'static str { S::g() }\n'
    refused(make_repo, source, rs.TRAIT_NOT_VISIBLE)


# ---- 3c. the module a path goes through
#
# A hop through a module is certain only if that module is the one it names wherever the call is: declared once, and under no `#[cfg]` the call is
# not under (Index.module_hop). Twin `#[cfg]` modules make `use r::*;` valid in both builds. Compiled and run by hand (scratch crates, not committed;
# `cargo run` with no feature, then with `--features a`; `EXT` is `pub use extblanket::Ext as Tr;`):
#   t2: `#[cfg(a)] pub mod r { pub use crate::m::Tr; }` and `#[cfg(not(a))] pub mod r { EXT }` with `use r::*;`: `ext-blanket`, `tr-default`.
#   t1: the same with `use r::Tr;`. t3: twin `q` nested in `p`, `use p::q::Tr;`. Both `ext-blanket`, `tr-default`.
#   u1: twin `q`, a middle `pub mod p { pub use crate::q::*; }` and `use p::*;`: `ext-blanket`, `tr-default`.
#   u3: twin `o` each with a `pub mod r`, `use o::r::*;`: `ext-blanket`, `tr-default`.
#   u4: twin file modules by `#[path = "ra.rs"]` and `#[path = "rb.rs"]`: `ext-blanket`, `tr-default`.
#   u9: `#[cfg(a)] pub mod o { pub mod r {..} }` and an unconditional `use o::r::*;`: no program without `a`, `tr-default` with it.
#   u5: one `#[cfg(a)] pub mod r;` (r.rs) and an unconditional `use r::*;`: no program without `a`, `tr-default` with it.
#   j1, j2: `#[cfg(a)] pub mod r` with `use r::*;` or `use r::Tr;`, and the call under `#[cfg(a)]`: `tr-default` with `a`.
#   j3: `fn go() { use crate::m::Tr; S::g() }`, j4: unconditional `q` and `p`, `use p::*;`, j5: `super::r::Tr` from a module: `tr-default` both ways.

TWIN_R = IN_M + '#[cfg(feature = "a")] pub mod r { pub use crate::m::Tr; }\n#[cfg(not(feature = "a"))] pub mod r { pub use extblanket::Ext as Tr; }\n'
GOCALL = "pub fn go() -> &'static str { S::g() }\n"
EXT_TR = "pub use extblanket::Ext as Tr;"
HOP_REFUSED = {
    "t2 twin modules, glob": TWIN_R + "use r::*;\n" + GOCALL,
    "t1 twin modules, use": TWIN_R + "use r::Tr;\n" + GOCALL,
    "t3 twin module nested in a module": IN_M + 'pub mod p { #[cfg(feature = "a")] pub mod q { pub use crate::m::Tr; }\n #[cfg(not(feature = "a"))] pub mod q { ' + EXT_TR + " } }\nuse p::q::Tr;\n" + GOCALL,
    "u1 twin module behind a pub use glob": IN_M + '#[cfg(feature = "a")] pub mod q { pub use crate::m::Tr; }\n#[cfg(not(feature = "a"))] pub mod q { ' + EXT_TR
    + " }\npub mod p { pub use crate::q::*; }\nuse p::*;\n" + GOCALL,
    "u3 twin outer module": IN_M + '#[cfg(feature = "a")] pub mod o { pub mod r { pub use crate::m::Tr; } }\n#[cfg(not(feature = "a"))] pub mod o { pub mod r { ' + EXT_TR
    + " } }\nuse o::r::*;\n" + GOCALL,
    "u9 conditional outer module, unconditional inner": IN_M + '#[cfg(feature = "a")] pub mod o { pub mod r { pub use crate::m::Tr; } }\nuse o::r::*;\n' + GOCALL,
    "u5 one conditional module, glob":IN_M + '#[cfg(feature = "a")] pub mod r { pub use crate::m::Tr; }\nuse r::*;\n' + GOCALL,
}


@pytest.mark.parametrize("source", list(HOP_REFUSED.values()), ids=list(HOP_REFUSED))
def test_a_trait_reached_through_a_module_that_is_not_certainly_there_is_unresolved(make_repo, tmp_path_factory, source):
    refused(make_repo, source, rs.CFG_UNKNOWN, cargo=foreign(tmp_path_factory))


def test_a_trait_reached_through_twin_modules_by_path_is_unresolved(make_repo, tmp_path_factory):
    source = IN_M + '#[cfg(feature = "a")] #[path = "ra.rs"] pub mod r;\n#[cfg(not(feature = "a"))] #[path = "rb.rs"] pub mod r;\nuse r::*;\n' + GOCALL
    files = {"src/ra.rs": "pub use crate::m::Tr;\n", "src/rb.rs": EXT_TR + "\n"}
    refused(make_repo, source, rs.CFG_UNKNOWN, cargo=foreign(tmp_path_factory), files=files)


def test_a_trait_reached_through_one_conditional_file_module_is_unresolved(make_repo):
    """u5 by a file: `#[cfg(feature = "a")] pub mod r;` (r.rs) and an unconditional `use r::*;`."""
    source = IN_M + '#[cfg(feature = "a")] pub mod r;\nuse r::*;\n' + GOCALL
    refused(make_repo, source, rs.CFG_UNKNOWN, files={"src/r.rs": "pub use crate::m::Tr;\n"})


HOP_KEPT = {
    "j1 conditional module, glob, call under the cfg": IN_M + '#[cfg(feature = "a")] pub mod r { pub use crate::m::Tr; }\nuse r::*;\n#[cfg(feature = "a")]\n' + GOCALL,
    "j2 conditional module, use, call under the cfg": IN_M + '#[cfg(feature = "a")] pub mod r { pub use crate::m::Tr; }\nuse r::Tr;\n#[cfg(feature = "a")]\n' + GOCALL,
    "j3 unconditional block import": IN_M + "pub fn go() -> &'static str { use crate::m::Tr; S::g() }\n",
    "j4 unconditional modules, pub use glob": IN_M + "pub mod q { pub use crate::m::Tr; }\npub mod p { pub use crate::q::*; }\nuse p::*;\n" + GOCALL,
    "u8 conditional module in a conditional module, call under the cfg": IN_M
    + '#[cfg(feature = "a")] pub mod o { #[cfg(feature = "a")] pub mod r { pub use crate::m::Tr; } }\nuse o::r::*;\n#[cfg(feature = "a")]\n' + GOCALL,
}


@pytest.mark.parametrize("source", list(HOP_KEPT.values()), ids=list(HOP_KEPT))
def test_a_trait_reached_through_modules_the_call_is_sure_of_keeps_its_edge(make_repo, source):
    assert result_of(make_repo, source) == ("edge", "src/lib:m::Tr::g")


def test_super_to_an_unconditional_module_keeps_its_edge(make_repo):
    source = IN_M + "pub mod r { pub use crate::m::Tr; }\nmod user { use super::r::Tr; pub fn go() -> &'static str { super::S::g() } }\n"
    assert result_of(make_repo, source, caller="src/lib:user::go") == ("edge", "src/lib:m::Tr::g")


def test_super_to_twin_modules_is_unresolved(make_repo, tmp_path_factory):
    source = TWIN_R + "mod user { use super::r::Tr; pub fn go() -> &'static str { super::S::g() } }\n"
    refused(make_repo, source, rs.CFG_UNKNOWN, caller="src/lib:user::go", cargo=foreign(tmp_path_factory))


def test_a_conditional_module_the_call_is_under_a_different_cfg_than_is_unresolved(make_repo):
    """u7: the module is under `a` and the call under `not(a)`: not the same predicate, so not covered."""
    source = IN_M + '#[cfg(feature = "a")] pub mod r { pub use crate::m::Tr; }\nuse r::*;\n#[cfg(not(feature = "a"))]\n' + GOCALL
    refused(make_repo, source, rs.CFG_UNKNOWN)


# ---- 4. what the default branch of unshared_cfg reads
#
# t1 compiled: `#[cfg(feature = "b")] pub struct S;`, `#[cfg(feature = "a")] impl Tr for S {}`, a call under `a`: `go=default` with `a` and `b`, and
# E0433 without `b` (no program: the type is absent). The type's own cfg is part of what the call must share, as for an inherent function.


def test_the_type_under_a_cfg_the_call_is_not_under_is_unresolved(make_repo):
    source = """
    pub trait Tr { fn g() -> &'static str { "default" } }
    #[cfg(feature = "b")]
    pub struct S;
    #[cfg(feature = "a")]
    impl Tr for S {}
    #[cfg(feature = "a")]
    pub fn go() -> &'static str { S::g() }
    """
    refused(make_repo, source, rs.CFG_UNKNOWN)


def test_the_type_under_a_cfg_beside_an_unconditional_impl_and_default_keeps_its_edge(make_repo):
    """Only the impl and the default are asked about the type's keys when one of them is conditional (as shares_cfg is only asked of a conditional
    inherent function): here neither is, and the program has `S` wherever it has the call."""
    source = """
    pub trait Tr { fn g() -> &'static str { "default" } }
    #[cfg(feature = "b")]
    pub struct S;
    impl Tr for S {}
    pub fn go() -> &'static str { S::g() }
    """
    assert result_of(make_repo, source) == ("edge", "src/lib:Tr::g")
