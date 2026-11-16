"""Conditional compilation anywhere on the way to what a method call's proof reads refuses the call, and nothing else does.

A method call is an edge only where the impl, the method, the receiver's type and its binding are the ones the source says. A `#[cfg]`
(or a `#[cfg_attr]` that may add anything but an inert attribute) in any place the Reference lets an attribute stand, outer or inner,
may remove one of them, so the call may find a trait's method instead (probes method_cfg_places, method_cfg_bindings: compiled and run,
with the compiler's answers). This is an allow-list: an attribute is read as harmless only if it is on rs_indexer.INERT_ATTRS
(PLAIN_INERT_ATTRS written plainly); every other attribute (`cfg`, `test`, an attribute macro, `path` in a `cfg_attr`) is conditional.

The invariants here fail on a position or an attribute nobody classified, instead of passing it silently:
  * every attribute node of every probe crate, and of POSITIONS (every place the Reference allows one that the grammar parses), has a
    parent type in rs_indexer.ATTRIBUTE_PARENTS, and is found on the node that holds it by rs_indexer.attributes_on;
  * for every position that can hold an impl, a method, a type or a binding, the same source with `cfg(any())` there is refused and
    with an inert attribute there is an edge.
"""

import pytest

pytest.importorskip("tree_sitter_rust")

from test_rs_method_guards import BASE, graph_of, result_of  # noqa: E402
from test_rs_probes import CRATES  # noqa: E402

from ripple import rs_indexer as rs  # noqa: E402

PARSER = None


def parse(src: bytes):
    global PARSER
    if PARSER is None:
        PARSER = rs.load_parser()
    return PARSER.parse(src)


def attribute_nodes(root):
    stack = [root]
    while stack:
        n = stack.pop()
        if n.type in rs.ATTRIBUTE_TYPES:
            yield n
        stack.extend(n.children)


def host_of(attribute):
    """The node an attribute is written on: its parent for an inner one (the match for the first arm's), the node after it for an outer one
    (its parent when the grammar puts the attribute inside the node)."""
    parent = attribute.parent
    if attribute.type == "inner_attribute_item":
        return parent.parent if parent.type == "match_arm" else parent
    if parent.type in ("match_arm", "field_initializer"):
        return parent
    nxt = attribute.next_named_sibling
    while nxt is not None and nxt.type in ("attribute_item", "line_comment", "block_comment"):
        nxt = nxt.next_named_sibling
    return nxt


# Every place the Reference allows an attribute (items and associated items, extern items, variants, fields, generic parameters,
# parameters, statements, array, tuple, call and struct-expression elements, match arms, the tail of a block; inner ones of a module,
# a function, an impl, a trait, an extern block, a block, a match), written once each. Where the grammar does not parse the position
# (a where-clause predicate, a struct pattern's field, a closure parameter) the file is one its parser could not read whole, which the
# crate refuses on its own (test_where_the_grammar_cannot_read_a_position_the_file_is_not_read_whole).
POSITIONS = """
#![cfg(a0)]
#[cfg(a1)] mod m1 { #![cfg(a2)] #[cfg(a3)] fn q() {} }
#[cfg(a4)] struct S1 { #[cfg(a5)] x: u8 }
#[cfg(a6)] struct S2(#[cfg(a7)] u8);
#[cfg(a8)] union U1 { #[cfg(a9)] x: u8 }
#[cfg(b0)] enum E1 { #[cfg(b1)] V { #[cfg(b2)] x: u8 }, #[cfg(b3)] W(#[cfg(b4)] u8) = 3 }
#[cfg(b5)] trait T1 { #![cfg(b6)] #[cfg(b7)] fn t(&self); #[cfg(b8)] const C: u8; #[cfg(b9)] type A; }
#[cfg(c0)] impl S1 { #![cfg(c1)] #[cfg(c2)] fn m(#[cfg(c3)] &self, #[cfg(c4)] x: u8) {} #[cfg(c5)] const K: u8 = 0; #[cfg(c6)] type B = u8; }
#[cfg(c7)] extern "C" { #![cfg(c8)] #[cfg(c9)] fn ext(); #[cfg(d0)] static X: u8; }
#[cfg(d1)] use a::b;
#[cfg(d2)] extern crate foo;
#[cfg(d3)] const K1: u8 = 0;
#[cfg(d4)] static K2: u8 = 0;
#[cfg(d5)] type Ty = u8;
#[cfg(d6)] macro_rules! mr { () => {}; }
#[cfg(d7)] mr!();
fn gen<#[cfg(d8)] 'a, #[cfg(d9)] T, #[cfg(e0)] const N: usize>() {}
fn body() {
    #![cfg(e2)]
    #[cfg(e3)] let x = 1;
    #[cfg(e4)] x;
    #[cfg(e5)] fn inner() {}
    #[cfg(e6)] println!();
    #[cfg(e7)] { #![cfg(e8)] 1 };
    let a = [#[cfg(e9)] 1, 2];
    let b = (#[cfg(f0)] 1, 2);
    f(#[cfg(f1)] 1, 2);
    let c = S2(#[cfg(f2)] 1);
    let d = S1 { #[cfg(f3)] x: 1 };
    match x { #![cfg(f4)] #[cfg(f5)] 0 => 1, _ => 2 };
    #[cfg(f8)] if x { 1 } else { 2 };
    unsafe { #![cfg(f9)] };
    loop { #![cfg(g0)] }
    #[cfg(g1)] 5
}
"""

UNPARSED = {
    "a where-clause predicate": "fn f<T>() where #[cfg(e1)] T: Copy {}",
    "a struct pattern's field": "fn f(d: S1) { let S1 { #[cfg(f6)] x, .. } = d; }",
    "a closure parameter": "fn f() { let cl = |#[cfg(f7)] y| y; }",
}


def corpus():
    """(name, source bytes) of every probe source and of POSITIONS."""
    out = [("POSITIONS", POSITIONS.encode())]
    for path in sorted(CRATES.rglob("*.rs")):
        if "target" not in path.relative_to(CRATES).parts:
            out.append((path.relative_to(CRATES).as_posix(), path.read_bytes()))
    return out


def test_the_corpus_is_not_vacuous():
    sources = corpus()
    assert len(sources) > 300
    kinds = {(a.type, a.parent.type) for _, src in sources for a in attribute_nodes(parse(src).root_node)}
    assert len({parent for _, parent in kinds}) >= 12, kinds


@pytest.mark.parametrize("name, src", [c for c in corpus() if b"#[" in c[1] or b"#![" in c[1]], ids=lambda v: v if isinstance(v, str) else "")
def test_every_attribute_of_the_corpus_is_in_a_position_this_knows_and_is_found_on_its_host(name, src):
    tree = parse(src)
    for attribute in attribute_nodes(tree.root_node):
        parent = attribute.parent
        if parent.type == "ERROR" or tree.root_node.has_error and parent.type == "source_file" and attribute.next_named_sibling is not None and attribute.next_named_sibling.type == "ERROR":
            continue  # a file the parser could not read whole: refused as a whole (Index.erroneous)
        assert parent.type in rs.ATTRIBUTE_PARENTS, f"{name}: an attribute whose parent is a `{parent.type}`: a position nobody classified"
        host = host_of(attribute)
        assert host is not None, f"{name}:{attribute.start_point[0] + 1}: an attribute with nothing after it"
        assert any(a.id == attribute.id for a in rs.attributes_on(host)), f"{name}:{attribute.start_point[0] + 1}: `{host.type}` does not see its attribute"
        mode = "inner" if attribute.type == "inner_attribute_item" else rs.ATTRIBUTE_PARENTS[parent.type]
        if mode == "child":
            assert host.id == parent.id
        elif mode == "sibling":
            assert host.id != parent.id


@pytest.mark.parametrize("what", sorted(UNPARSED))
def test_where_the_grammar_cannot_read_a_position_the_file_is_not_read_whole(what):
    assert parse(UNPARSED[what].encode()).root_node.has_error


def test_the_positions_cover_every_parent_type_the_table_names():
    seen = {a.parent.type for a in attribute_nodes(parse(POSITIONS.encode()).root_node)}
    assert seen == set(rs.ATTRIBUTE_PARENTS) | {"match_arm"}, seen ^ set(rs.ATTRIBUTE_PARENTS)


# ---- which attribute is inert (the allow-list), and where `cfg_attr` stands


def attribute(text_: str):
    src = (text_ + "\nfn f() {}\n").encode()
    node = next(attribute_nodes(parse(src).root_node))
    return node, src


INERT = [
    "#[allow(dead_code)]", "#[warn(unused)]", "#[deny(unused)]", "#[forbid(unsafe_code)]", "#[expect(dead_code)]", '#[doc = "x"]', "#[must_use]",
    "#[inline]", "#[inline(always)]", "#[cold]", "#[deprecated]", "#[track_caller]", "#[repr(C)]", "#[non_exhaustive]", "#[derive(Debug)]",
    "#[automatically_derived]", "#[rustfmt::skip]", "#[clippy::too_many_arguments]", '#[path = "x.rs"]', "#[macro_use]", "#[macro_export]",
    "#[cfg_attr(a, allow(dead_code))]", '#[cfg_attr(a, allow(dead_code), doc = "x")]', "#[cfg_attr(docsrs, doc(cfg(feature = \"x\")))]",
    "#[cfg_attr(a, cfg_attr(b, inline))]", "#[cfg_attr(rustfmt, rustfmt::skip)]", "#[cfg_attr(a, derive(Debug))]", "#[cfg_attr(a, allow(x),)]", "#[cfg_attr(a)]",
]  # fmt: skip
CONDITIONAL = [
    "#[cfg(a)]", "#[cfg(any())]", "#[cfg(all())]", "#[cfg_attr(a, cfg(b))]", '#[cfg_attr(a, path = "x.rs")]', "#[cfg_attr(a, test)]",
    "#[cfg_attr(a, allow(x), cfg(b))]", "#[cfg_attr(a, feature(x))]", "#[cfg_attr(a, cfg_attr(b, cfg(c)))]", "#[cfg_attr(a, tokio::main)]",
    "#[cfg_attr(a, my_macro)]", "#[cfg_attr(a, macro_use)]", "#[test]", "#[bench]", "#[tokio::main]", "#[my_macro]", "#[global_allocator]",
    "#[no_implicit_prelude]", "#[feature(x)]", "#[proc_macro]", "#[ignore]", "#[should_panic]", "#[cfg_attr]",
]  # fmt: skip


@pytest.mark.parametrize("written", INERT)
def test_an_inert_attribute_is_not_conditional(written):
    node, src = attribute(written)
    assert not rs.attribute_conditional(node, src, frozenset()), written


@pytest.mark.parametrize("written", CONDITIONAL)
def test_every_other_attribute_is_conditional(written):
    node, src = attribute(written)
    assert rs.attribute_conditional(node, src, frozenset()), written


def test_a_tool_attribute_is_inert_only_where_no_item_of_the_crate_is_a_macro_of_its_name():
    node, src = attribute("#[rustfmt::skip]")
    assert not rs.attribute_conditional(node, src, frozenset())
    assert rs.attribute_conditional(node, src, frozenset({"rustfmt"}))


def test_the_inert_list_is_of_builtin_attributes_only():
    assert rs.INERT_ATTRS <= rs.BUILTIN_ATTRS | {"derive"}, rs.INERT_ATTRS - rs.BUILTIN_ATTRS
    assert not rs.INERT_ATTRS & {"cfg", "cfg_attr", "test", "bench", "path", "feature", "macro_use"}
    assert rs.PLAIN_INERT_ATTRS >= rs.INERT_ATTRS


def test_attrs_cfg_is_a_cfg_attr_that_may_add_anything_but_an_inert_attribute():
    """Attrs.cfg (every `name declared under a cfg` rule) is not set by a lint-only `cfg_attr` (`#![cfg_attr(.., allow(..))]` is on most
    crates), and is by anything that may remove or replace what it is on."""
    for written, expected in [("#[cfg_attr(a, allow(x))]", False), ("#[cfg_attr(a, cfg(b))]", True), ('#[cfg_attr(a, path = "x")]', True), ("#[cfg(a)]", True), ("#[inline]", False)]:
        node, src = attribute(written)
        assert rs.read_attributes([node], src, frozenset()).cfg is expected, written


# ---- one test per position: the same source is refused with a cfg there and is an edge with an inert attribute there

GO = "impl S { pub fn go(&self) -> u8 { 1 } }"
ARM = "match x { %s 0 => { " + GO + " 1 } _ => 2 }"
# (name, source with {A} (an outer attribute) or {I} (an inner one) where the position is; the call is `s.go()` of `run`)
IMPL_POSITIONS = {
    "an impl, outer": "pub struct S;\n{A}\n" + GO + "\npub fn run(s: &S) -> u8 { s.go() }\n",
    "an impl's own inner": "pub struct S;\nimpl S { {I} pub fn go(&self) -> u8 { 1 } }\npub fn run(s: &S) -> u8 { s.go() }\n",
    "an inner attribute of the function the impl is in": "pub struct S;\npub fn setup() { {I} " + GO + " }\npub fn run(s: &S) -> u8 { s.go() }\n",
    "the function the impl is in": "pub struct S;\n{A}\npub fn setup() { " + GO + " }\npub fn run(s: &S) -> u8 { s.go() }\n",
    "a statement block": "pub struct S;\npub fn setup() { {A} { " + GO + " } }\npub fn run(s: &S) -> u8 { s.go() }\n",
    "a match arm": "pub struct S;\npub fn setup(x: u8) -> u8 { match x { {A} 0 => { " + GO + " 1 } _ => 2 } }\npub fn run(s: &S) -> u8 { s.go() }\n",
    "the second arm's match, by its first arm's inner attribute": "pub struct S;\npub fn setup(x: u8) -> u8 { match x { {I} 1 => 1, 0 => { " + GO + " 1 } _ => 2 } }\npub fn run(s: &S) -> u8 { s.go() }\n",
    "an array element": "pub struct S;\npub fn setup() -> usize { let v = [{A} { " + GO + " 1u8 }, 2u8]; v.len() }\npub fn run(s: &S) -> u8 { s.go() }\n",
    "a tuple element": "pub struct S;\npub fn setup() -> u8 { let v = ({A} { " + GO + " 1u8 }, 2u8); v.0 }\npub fn run(s: &S) -> u8 { s.go() }\n",
    "a call argument": "pub struct S;\nfn two(x: u8) -> u8 { x }\npub fn setup() -> u8 { two({A} { " + GO + " 1u8 }, 2u8) }\npub fn run(s: &S) -> u8 { s.go() }\n",
    "a struct expression's field": "pub struct S;\npub struct P { pub b: u8 }\npub fn setup() -> u8 { let p = P { {A} a: { " + GO + " 0u8 }, b: 1 }; p.b }\npub fn run(s: &S) -> u8 { s.go() }\n",
    "a let statement": "pub struct S;\npub fn setup() { {A} let _x = { " + GO + " 1u8 }; }\npub fn run(s: &S) -> u8 { s.go() }\n",
    "an inline module": "pub struct S;\n{A}\nmod m { " + GO.replace("impl S", "impl super::S") + " }\npub fn run(s: &S) -> u8 { s.go() }\n",
    "an inline module's inner": "pub struct S;\nmod m { {I} " + GO.replace("impl S", "impl super::S") + " }\npub fn run(s: &S) -> u8 { s.go() }\n",
    "an impl in an impl's method": "pub struct S;\npub struct T;\nimpl T { {A} pub fn setup() { " + GO + " } }\npub fn run(s: &S) -> u8 { s.go() }\n",
    "a trait's default method": "pub struct S;\npub trait Tr { {A} fn setup() { " + GO + " } }\npub fn run(s: &S) -> u8 { s.go() }\n",
    "a statement that is an expression": "pub struct S;\npub fn setup() { {A} unsafe { " + GO + " } }\npub fn run(s: &S) -> u8 { s.go() }\n",
}  # fmt: skip
HEADER_POSITIONS = {
    "the method's own": "pub struct S;\nimpl S { {A} pub fn go(&self) -> u8 { 1 } }\npub fn run(s: &S) -> u8 { s.go() }\n",
    "the method's inner": "pub struct S;\nimpl S { pub fn go(&self) -> u8 { {I} 1 } }\npub fn run(s: &S) -> u8 { s.go() }\n",
    "the method's self parameter": "pub struct S;\nimpl S { pub fn go({A} &self) -> u8 { 1 } }\npub fn run(s: &S) -> u8 { s.go() }\n",
    "the method's other parameter": "pub struct S;\nimpl S { pub fn go(&self, {A} x: u8) -> u8 { 1 } }\npub fn run(s: &S) -> u8 { s.go() }\n",
    "the impl's generic parameter": "pub struct S;\nimpl<{A} 'a> S { pub fn go(&self) -> u8 { 1 } }\npub fn run(s: &S) -> u8 { s.go() }\n",
    "the type's generic parameter": "pub struct S<{A} 'a>(&'a u8);\nimpl S<'_> { pub fn go(&self) -> u8 { 1 } }\npub fn run(s: &S) -> u8 { s.go() }\n",
    "the call's argument": "pub struct S;\nimpl S { pub fn go(&self) -> u8 { 1 } }\npub fn run(s: &S) -> u8 { s.go({A} 1) }\n",
    "the receiver's let": "pub struct S;\nimpl S { pub fn go(&self) -> u8 { 1 } }\npub fn run() -> u8 { {A} let s: &S = &S; s.go() }\n",
    "the receiver's parameter": "pub struct S;\nimpl S { pub fn go(&self) -> u8 { 1 } }\npub fn run({A} s: &S) -> u8 { s.go() }\n",
    "self's parameter": "pub struct S;\nimpl S { pub fn go(&self) -> u8 { 1 } pub fn run({A} &self) -> u8 { self.go() } }\n",
}  # fmt: skip


def outcome(make_repo, source, caller="src/lib:run", text="s.go"):
    g = graph_of(make_repo, {"src/lib.rs": source})
    edges = [e.callee for e in g.edges if e.kind == "call" and e.caller == caller]
    refused = [u.reason for u in g.unresolved if u.text == text and u.caller == caller]
    assert len(edges) + len(refused) == 1, (edges, refused)
    return ("edge", edges[0]) if edges else ("unresolved", refused[0])


def fill(template, attribute_text):
    return template.replace("{A}", attribute_text).replace("{I}", attribute_text.replace("#[", "#!["))


POSITIONS_UNDER_TEST = {**IMPL_POSITIONS, **HEADER_POSITIONS}


@pytest.mark.parametrize("position", sorted(POSITIONS_UNDER_TEST))
def test_a_cfg_in_the_position_refuses_the_call(make_repo, position):
    caller = "src/lib:S::run" if position == "self's parameter" else "src/lib:run"
    text = "self.go" if position == "self's parameter" else "s.go"
    for attribute_text in ("#[cfg(any())]", "#[cfg_attr(all(), cfg(any()))]"):
        kind, why = outcome(make_repo, fill(POSITIONS_UNDER_TEST[position], attribute_text), caller, text)
        assert kind == "unresolved" and "`#[cfg]`" in why, (position, attribute_text, why)


@pytest.mark.parametrize("position", sorted(POSITIONS_UNDER_TEST))
def test_an_inert_attribute_in_the_position_costs_nothing(make_repo, position):
    caller = "src/lib:S::run" if position == "self's parameter" else "src/lib:run"
    text = "self.go" if position == "self's parameter" else "s.go"
    for attribute_text in ("#[allow(dead_code)]", "#[cfg_attr(all(), allow(dead_code))]"):
        assert outcome(make_repo, fill(POSITIONS_UNDER_TEST[position], attribute_text), caller, text)[0] == "edge", (position, attribute_text)


def test_the_call_site_itself_being_conditional_does_not_refuse_it(make_repo):
    """An edge is `if this call exists, it calls S::go`: a cfg on the call's own statement, block or function is not a reason (every call edge of ripple is
    that), as long as nothing the proof reads is conditional."""
    source = "pub struct S;\nimpl S { pub fn go(&self) -> u8 { 1 } }\n#[cfg(feature = \"x\")]\npub fn run(s: &S) -> u8 { #[cfg(feature = \"y\")] { s.go() } }\n"
    assert outcome(make_repo, source)[0] == "edge"


@pytest.mark.parametrize(
    "first_line, refused",
    [
        ("#![cfg(any())]", True),
        ("#! /* c */ [cfg(any())]", True),
        ("#!/* c */[cfg(any())]", True),
        ("#! // c\n[cfg(any())]", True),
        ("#! /* c */ [allow(dead_code)]", False),
        ("#![allow(dead_code)]", False),
        ("#!/usr/bin/env rust-script", False),
    ],
)
def test_an_inner_attribute_of_a_module_file_that_tree_sitter_reads_as_a_shebang_is_still_read(make_repo, first_line, refused):
    """tree-sitter takes `#!` and the rest of the first line for a shebang; rustc takes `#!` not followed by `[` (comments aside) for one."""
    files = {"src/lib.rs": "mod x;\npub struct S;\npub fn run(s: &S) -> u8 { s.go() }\n", "src/x.rs": first_line + "\nimpl crate::S { pub fn go(&self) -> u8 { 1 } }\n"}
    kind, why = result_of(graph_of(make_repo, files))
    assert (kind == "unresolved") is refused, (kind, why)


# ---- the path call of main: the same function


def test_a_path_call_does_not_reach_an_inherent_function_that_may_not_exist(make_repo):
    """The review's c7, path call: `impl S { #![cfg(any())] fn m }` and a trait's `m` for S: rustc runs the trait's, so an edge to S::m is wrong."""
    for body in ("impl S { #![cfg(any())] pub fn m(&self) -> u8 { 1 } }", "pub fn setup() { #![cfg(any())] impl S { pub fn m(&self) -> u8 { 1 } } }", "mod x { #![cfg(any())] impl super::S { pub fn m(&self) -> u8 { 1 } } }"):
        source = "pub struct S;\npub trait Tr { fn m(&self) -> u8 { 2 } }\nimpl Tr for S {}\n" + body + "\npub fn run(s: &S) -> u8 { S::m(s) }\n"
        g = graph_of(make_repo, {"src/lib.rs": source})
        assert not [e for e in g.edges if e.kind == "call" and e.callee.endswith("S::m")], body


def test_a_path_call_still_reaches_an_inherent_function_under_an_inert_cfg_attr(make_repo):
    source = "pub struct S;\npub trait Tr { fn m(&self) -> u8 { 2 } }\nimpl Tr for S {}\nimpl S { #![cfg_attr(all(), allow(dead_code))] pub fn m(&self) -> u8 { 1 } }\npub fn run(s: &S) -> u8 { S::m(s) }\n"
    g = graph_of(make_repo, {"src/lib.rs": source})
    assert [e.callee for e in g.edges if e.kind == "call" and e.caller == "src/lib:run"] == ["src/lib:S::m"]


def test_the_base_source_of_these_positions_is_an_edge(make_repo):
    assert result_of(graph_of(make_repo, {"src/lib.rs": BASE})) == ("edge", "src/lib:S::go")
