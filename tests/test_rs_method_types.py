"""A type name in the proof is resolved where it is written, never compared as text.

Two impls' type arguments, and an impl's and a call's, are compared only when each names a type no scope can change: built only of the
primitive types (rs_indexer.CONCRETE_TYPE_KINDS), each primitive name looked up where it is written (Index.primitive_here). A struct, an
import, a glob, a generic parameter, an alias or a macro of the scope called `u8` makes `W<u8>` another type than the primitive's
(probe method_type_names: compiled and run, for a method call and a path call). Every other kind of type (a path, `Vec<u8>`, `Self`,
`impl Trait`, a macro) is refused: a prelude type and a std path can be replaced as well, and which one it is is not proven.
"""

import pytest

pytest.importorskip("tree_sitter_rust")

from test_rs_method_cfg import corpus, parse  # noqa: E402
from test_rs_method_guards import graph_of  # noqa: E402

from ripple import rs_indexer as rs  # noqa: E402

HEAD = "pub struct W<T>(pub T);\npub trait Tr { fn m(&self) -> u8 { 2 } }\n"


def calls(make_repo, source, caller):
    g = graph_of(make_repo, {"src/lib.rs": source})
    return [e.callee for e in g.edges if e.kind == "call" and e.caller == caller], [u.reason for u in g.unresolved if u.caller == caller]


CONTROL = HEAD + "impl W<u8> { pub fn m(&self) -> u8 { 1 } }\nimpl Tr for W<u16> {}\npub fn f(w: &W<u8>) -> u8 { w.m() }\npub fn p(w: &W<u8>) -> u8 { W::<u8>::m(w) }\n"

# an impl in the module `a`, written with a `u8` that the module's own scope gives (the call is at the root, where it is the primitive)
IMPL_SIDE = {
    "a struct": "pub mod a { pub struct u8; impl crate::W<u8> { pub fn m(&self) -> u8 { 1 } } }",
    "an enum": "pub mod a { pub enum u8 {} impl crate::W<u8> { pub fn m(&self) -> u8 { 1 } } }",
    "an alias": "pub mod a { pub type u8 = i64; impl crate::W<u8> { pub fn m(&self) -> u8 { 1 } } }",
    "an import": "pub struct R;\npub mod a { pub use super::R as u8; impl crate::W<u8> { pub fn m(&self) -> u8 { 1 } } }",
    "a glob": "pub mod t { pub struct u8; }\npub mod a { use crate::t::*; impl crate::W<u8> { pub fn m(&self) -> u8 { 1 } } }",
    "a macro that may make it": "macro_rules! mk { () => {}; }\npub mod a { mk!(); impl crate::W<u8> { pub fn m(&self) -> u8 { 1 } } }",
    "a block's struct": "pub fn blk() { struct u8; impl crate::W<u8> { pub fn m(&self) -> u8 { 1 } } }",
}


@pytest.mark.parametrize("what", sorted(IMPL_SIDE))
def test_an_impl_written_in_a_scope_that_gives_a_primitive_name_is_not_for_the_primitive(make_repo, what):
    source = HEAD + "impl Tr for W<u8> {}\n" + IMPL_SIDE[what] + "\npub fn f(w: &W<u8>) -> u8 { w.m() }\npub fn p(w: &W<u8>) -> u8 { W::<u8>::m(w) }\n"
    for caller in ("src/lib:f", "src/lib:p"):
        edges, _ = calls(make_repo, source, caller)
        assert not any(e.endswith("::m") and ":Tr" not in e and "W::m" in e or "a::" in e for e in edges), (what, caller, edges)


# the call's own scope gives the name: the receiver is `W<local u8>`, the impl is for the primitive
CALL_SIDE = {
    "a struct in the module": "pub mod c { pub struct u8; impl crate::Tr for crate::W<u8> {} pub fn f(w: &crate::W<u8>) -> u8 { use crate::Tr; w.m() } pub fn p(w: &crate::W<u8>) -> u8 { crate::W::<u8>::m(w) } }",
    "a generic parameter": "pub fn g<u8>(w: &W<u8>) -> u8 { w.m() }\nimpl<T> Tr for W<T> {}",
    "a struct in the block of the let": "pub fn g() -> u8 { struct u8; impl Tr for W<u8> {} let w: &W<u8> = &W(u8); w.m() }",
}


@pytest.mark.parametrize("what", sorted(CALL_SIDE))
def test_a_call_written_in_a_scope_that_gives_a_primitive_name_is_not_the_primitives_type(make_repo, what):
    source = HEAD + "impl W<u8> { pub fn m(&self) -> u8 { 1 } }\n" + CALL_SIDE[what] + "\n"
    g = graph_of(make_repo, {"src/lib.rs": source})
    assert not [e for e in g.edges if e.kind == "call" and e.callee.endswith("W::m")], (what, [(e.caller, e.callee) for e in g.edges if e.kind == "call"])


def test_the_control_is_an_edge_for_a_method_call_and_a_path_call(make_repo):
    assert calls(make_repo, CONTROL, "src/lib:f")[0] == ["src/lib:W::m"]
    assert calls(make_repo, CONTROL, "src/lib:p")[0] == ["src/lib:W::m"]


def test_a_prelude_type_or_a_std_path_is_never_the_same_type_as_text(make_repo):
    for arg in ("Vec<u8>", "Option<u8>", "String", "Box<u8>", "std::string::String", "Self"):
        source = HEAD + f"impl W<{arg}> {{ pub fn m(&self) -> u8 {{ 1 }} }}\npub fn f(w: &W<{arg}>) -> u8 {{ w.m() }}\n"
        edges, reasons = calls(make_repo, source, "src/lib:f")
        assert not edges and rs.M_INSTANTIATION in reasons, (arg, edges, reasons)


def test_a_primitive_name_is_the_primitive_only_where_the_prelude_is_the_standard_one(make_repo):
    """A crate whose own prelude (`#[prelude_import]`) or `std` may not be the standard library's gives a primitive's name to a prelude type."""
    source = "#![feature(prelude_import)]\n" + CONTROL
    edges, _ = calls(make_repo, source, "src/lib:f")
    assert edges == []


def test_primitive_names_are_not_generic_parameters_of_the_call_when_they_are_declared_as_one(make_repo):
    source = HEAD + "impl W<u8> { pub fn m(&self) -> u8 { 1 } }\nimpl<T> Tr for W<T> {}\npub fn g<u8>(w: &W<u8>) -> u8 { w.m() }\npub fn h(w: &W<u8>) -> u8 { w.m() }\n"
    assert calls(make_repo, source, "src/lib:g")[0] == []
    assert calls(make_repo, source, "src/lib:h")[0] == ["src/lib:W::m"]


# ---- the invariant: every kind a written type argument has is classified


def type_arguments(src):
    stack = [parse(src).root_node]
    while stack:
        n = stack.pop()
        if n.type in ("generic_type", "generic_type_with_turbofish"):
            args = n.child_by_field_name("type_arguments")
            if args is not None:
                yield from (a for a in args.named_children if a.type != "lifetime")
        stack.extend(n.children)


def descendants(node):
    stack = [node]
    while stack:
        n = stack.pop()
        yield n
        stack.extend(n.named_children)


def test_every_kind_of_type_argument_of_the_probe_crates_is_classified():
    seen = {}
    for name, src in corpus():
        for arg in type_arguments(src):
            for n in descendants(arg):
                if n.type not in ("comment", "line_comment", "block_comment"):
                    seen.setdefault(n.type, name)
    assert len(seen) >= 12, sorted(seen)
    unclassified = {k: v for k, v in seen.items() if k not in rs.CONCRETE_TYPE_KINDS and k not in rs.REFUSED_TYPE_KINDS}
    assert not unclassified, f"a kind of type nobody classified: {unclassified}"


def test_a_type_argument_is_concrete_in_shape_exactly_when_every_node_of_it_is_a_concrete_kind():
    for written, expected in [("u8", True), ("(u8, bool)", True), ("[u8; 4]", True), ("&u8", True), ("&mut u8", True), ("()", True), ("Vec<u8>", False), ("Self", False), ("S", False), ("a::u8", False), ("impl Tr", False), ("fn(u8)", False), ("*const u8", False), ("dyn Tr", False), ("!", False)]:
        root = parse(f"type T = W<{written}>;".encode()).root_node
        arg = root.named_children[0].child_by_field_name("type").child_by_field_name("type_arguments").named_children[0]
        assert rs.concrete_shape(arg) is expected, written


def test_the_concrete_and_refused_kinds_do_not_overlap():
    assert not rs.CONCRETE_TYPE_KINDS & rs.REFUSED_TYPE_KINDS
