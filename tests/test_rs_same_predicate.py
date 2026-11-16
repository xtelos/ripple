"""A cfg on the declaration is satisfied by another cfg on the call's path that has the identical predicate, under five conditions.

An inherent method (or associated function) under a `#[cfg(P)]` that the call is not under as the same attribute (tests/test_rs_shared_cfg.py)
is still one the call finds if the call is under another plain `#[cfg(P)]` with the same predicate, read in the one crate: the shape of ripgrep's
`#[cfg(test)] mod testutil;` holding the impl and the sibling `#[cfg(test)] mod tests` holding the calls. The conditions (the review's, each
shown with rustc; the compiled probes are method_cfg_predicate and method_cfg_predicate_crates, and `shared_cfg_errors` has the second definitions
rustc rejects):

  1. both attributes are read by one crate (a file that is a module of two crates is two contexts, and an attribute is on in one's build and off
     in the other's);
  2. both are plain `cfg`s written in place: never a `cfg_attr`, never a predicate that came out of a macro, never `cfg!`;
  3. the predicates are compared token by token: comments and white space between tokens are nothing, every literal is compared byte for byte
     (`"a b"` is not `"a  b"`, and `r"x"` is not `"x"`), and the order of the tokens matters (`all(a, b)` is not `all(b, a)`);
  4. every other conditional attribute on the declaration's path is matched, one by one: one that is not refuses;
  5. a second definition that could win is refused as it is for the shared ancestor (the other-definition cases below).

What the rule does not reach: the receiver's own `let` or parameter, `#[test]` (never shared), an attribute in the header of the impl or the method
(a generic, a parameter, a where clause), and any other crate: the integration tests, the examples, the benches, a build script, a procedural macro
crate and the other members of a workspace are crates of their own, whose `cfg(test)` and features are their own.
"""

import pytest

pytest.importorskip("tree_sitter_rust")

from test_rs_method_guards import graph_of  # noqa: E402
from test_rs_shared_cfg import CALLS, IMPL, TR, lib, outcomes  # noqa: E402

from ripple import rs_indexer as rs  # noqa: E402

PRE = TR + "pub struct S;\nimpl Tr for S {}\n"
CRATE_IMPL = IMPL.replace("impl S", "impl crate::S")


def impl_in(attr, name="a", body=CRATE_IMPL):
    return f"{attr}\nmod {name} {{ {body} }}\n"


def calls_in(attr, name="b", body=CALLS.replace("pub fn", "fn")):
    return f"{attr}\nmod {name} {{ use crate::S;\n{body}}}\n"


# ---- the predicate of an attribute, as the tokens it is made of


def attribute_of(written):
    src = (written + "\nfn f() {}\n").encode()
    tree = rs.load_parser().parse(src)
    return next(c for c in tree.root_node.children if c.type in rs.ATTRIBUTE_TYPES), src


def predicate(written):
    node, src = attribute_of(written)
    return rs.attribute_predicate(node, src)


SAME = [
    ("#[cfg(test)]", "#[cfg(test)]"),
    ("#[cfg(test)]", "#![cfg(test)]"),
    ("#[cfg(test)]", "#[ cfg ( test ) ]"),
    ("#[cfg(test)]", "#[cfg(/* a */ test /* b */)]"),
    ('#[cfg(all(test, feature = "a"))]', '#[cfg( all( test , feature="a" ) )]'),
    ('#[cfg(all(test, feature = "a"))]', '#[cfg(all(test, // one\n feature /* two */ = /* three */ "a"))]'),
    ('#[cfg(feature = "a  b")]', '#[cfg(feature = "a  b")]'),
]
DIFFERENT = [
    ('#[cfg(feature = "a b")]', '#[cfg(feature = "a  b")]'),  # white space inside a literal is part of it
    ('#[cfg(feature = "a b")]', '#[cfg(feature = "ab")]'),
    ('#[cfg(feature = "a b")]', '#[cfg(feature = " a b")]'),
    ('#[cfg(feature = "a")]', '#[cfg(feature = r"a")]'),  # another spelling of one string is another token
    ('#[cfg(feature = "a")]', '#[cfg(feature = r#"a"#)]'),
    ('#[cfg(feature = "a")]', '#[cfg(feature = "\\x61")]'),
    ('#[cfg(feature = "a")]', '#[cfg(feature = "\\u{61}")]'),
    ("#[cfg(all(a, b))]", "#[cfg(all(b, a))]"),  # the order of the tokens matters: no boolean reasoning
    ("#[cfg(any(a, b))]", "#[cfg(any(b, a))]"),
    ("#[cfg(a)]", "#[cfg(all(a))]"),
    ("#[cfg(a)]", "#[cfg(not(not(a)))]"),
    ("#[cfg(a)]", "#[cfg(r#a)]"),
    ("#[cfg(a)]", "#[cfg(b)]"),
    ("#[cfg(not(a))]", "#[cfg(a)]"),
]
NO_PREDICATE = [
    "#[cfg_attr(a, cfg(test))]",
    "#[cfg_attr(test, cfg(test))]",
    "#![cfg_attr(a, cfg(test))]",
    "#[cfg]",
    '#[cfg = "x"]',
    "#[cfg()]",
    "#[cfg[test]]",
    "#[cfg{test}]",
    "#[core::cfg(test)]",
    "#[::cfg(test)]",
    "#[cfg(a::b)]",
    "#[cfg(a!())]",
    "#[cfg(a, 1)]",
    "#[cfg(a, 'c')]",
    "#[cfg(a, 1.5)]",
    "#[cfg(a, b'x')]",
    "#[cfg(a, 'lifetime)]",
    "#[cfg(a, $b)]",
    "#[cfg(a, /// a doc comment\n b)]",
    "#[cfg(a, /** a doc comment */ b)]",
    "#[test]",
    "#[bench]",
    "#[allow(dead_code)]",
    "#[my_macro(test)]",
    "#[path = \"x.rs\"]",
]  # fmt: skip


@pytest.mark.parametrize("a, b", SAME)
def test_two_attributes_with_the_same_tokens_have_one_predicate(a, b):
    assert predicate(a) is not None and predicate(a) == predicate(b), (a, b)


@pytest.mark.parametrize("a, b", DIFFERENT)
def test_predicates_that_are_not_the_same_tokens_are_two_predicates(a, b):
    pa, pb = predicate(a), predicate(b)
    assert pa is not None and pb is not None and pa != pb, (a, b, pa, pb)


@pytest.mark.parametrize("written", NO_PREDICATE)
def test_an_attribute_that_is_no_plain_cfg_of_plain_tokens_has_no_predicate(written):
    assert predicate(written) is None, written


def test_the_tokens_of_a_predicate_are_kept_byte_for_byte():
    assert predicate('#[cfg(feature = "a  b")]') == (b"(", b"feature", b"=", b'"a  b"', b")")


def test_the_key_of_an_attribute_carries_its_crate_file_and_byte_and_the_predicate():
    node, src = attribute_of("#[cfg(test)]")
    ctx = object()
    assert rs.attribute_key(ctx, "f.rs", node, src) == (id(ctx), "f.rs", 0, (b"(", b"test", b")"))
    assert rs.attribute_key(ctx, "f.rs", node) == (id(ctx), "f.rs", 0, None)


def test_cfg_covers_matches_a_predicate_only_within_one_crate_and_only_for_every_key():
    a, b = object(), object()
    test, other = (b"(", b"test", b")"), (b"(", b"x", b")")
    mine = (id(a), "m.rs", 0, test)
    assert rs.cfg_covers(frozenset({mine}), frozenset({mine}))  # the same attribute
    assert rs.cfg_covers(frozenset({mine}), frozenset({(id(a), "n.rs", 9, test)}))  # the same predicate in the same crate
    assert not rs.cfg_covers(frozenset({mine}), frozenset({(id(b), "n.rs", 9, test)}))  # another crate
    assert not rs.cfg_covers(frozenset({mine}), frozenset({(id(a), "n.rs", 9, other)}))
    assert not rs.cfg_covers(frozenset({mine}), frozenset({(id(a), "n.rs", 9, None)}))
    assert not rs.cfg_covers(frozenset({(id(a), "m.rs", 0, None)}), frozenset({(id(a), "n.rs", 9, None)}))
    assert not rs.cfg_covers(frozenset({mine, (id(a), "m.rs", 5, other)}), frozenset({(id(a), "n.rs", 9, test)}))  # one is not matched
    assert rs.cfg_covers(frozenset({mine, (id(a), "m.rs", 5, test)}), frozenset({(id(a), "n.rs", 9, test)}))  # both are, by the one
    assert rs.cfg_covers(frozenset(), frozenset())


# ---- the cases that are an edge: the declaration's attributes are each the call's, or one with its predicate

EDGE = {
    "a sibling module under the same attribute (the shape of ripgrep's testutil and tests)": lib(PRE + impl_in("#[cfg(test)]") + calls_in("#[cfg(test)]")),
    "a sibling file module under the same attribute": {
        "src/lib.rs": PRE + "#[cfg(test)]\nmod testutil;\n#[cfg(test)]\nmod tests;\n",
        "src/testutil.rs": CRATE_IMPL + "\n",
        "src/tests.rs": "use crate::S;\n" + CALLS,
    },
    "a file module whose own inner attribute has the predicate of the call's module": {
        "src/lib.rs": PRE + "mod testutil;\n#[cfg(test)]\nmod tests;\n",
        "src/testutil.rs": "#![cfg(test)]\n" + CRATE_IMPL + "\n",
        "src/tests.rs": "use crate::S;\n" + CALLS,
    },
    "the call's own inner attribute and the declaration's outer one": {
        "src/lib.rs": PRE + "#[cfg(test)]\nmod testutil;\nmod tests;\n",
        "src/testutil.rs": CRATE_IMPL + "\n",
        "src/tests.rs": "#![cfg(test)]\nuse crate::S;\n" + CALLS,
    },
    "the predicate written with other spacing and comments between its tokens": lib(
        PRE + impl_in('#[cfg(all(test, not(any()), feature = "a"))]') + calls_in('#[cfg( all( /* c */ test,\n not( any() ) , feature="a" ) )]')
    ),
    "a predicate of a feature and a literal": lib(PRE + impl_in('#[cfg(feature = "a b")]') + calls_in('#[cfg(feature = "a b")]')),
    "the call in functions under the attribute": lib(PRE + impl_in("#[cfg(test)]") + "#[cfg(test)]\n" + CALLS.replace("pub fn run_path", "#[cfg(test)]\npub fn run_path")),
    "the call in a block under the attribute": lib(PRE + impl_in("#[cfg(test)]") + "pub fn run(s: &S) -> u8 { #[cfg(test)] { s.go() } }\npub fn run_path() -> u8 { #[cfg(test)] { S::make() } }\n"),
    "the impl under the attribute and the calls in a module under it": lib(PRE + "#[cfg(test)]\n" + IMPL + "\n" + calls_in("#[cfg(test)]")),
    "the method under the attribute and the calls in a module under it": lib(
        PRE + "impl S { #[cfg(test)] pub fn go(&self) -> u8 { 1 } #[cfg(test)] pub fn make() -> u8 { 4 } }\n" + calls_in("#[cfg(test)]")
    ),
    "the type's own attribute and the call's module": lib(TR + "#[cfg(test)]\npub struct S;\n#[cfg(test)]\nimpl Tr for S {}\n" + impl_in("#[cfg(test)]") + calls_in("#[cfg(test)]")),
    "the type's attribute, the impl's module and the call's module each under it": lib(
        TR + "#[cfg(test)]\npub struct S;\n#[cfg(test)]\nimpl Tr for S {}\n" + impl_in("#[cfg(test)]") + calls_in("#[cfg(test)]")
    ),
    "two attributes on the declaration's path and two on the call's, in the other order": lib(
        PRE + "#[cfg(a)]\nmod x {\n #[cfg(b)]\n mod y {\n use crate::S;\n" + CRATE_IMPL + "\n }\n}\n#[cfg(b)]\nmod p {\n #[cfg(a)]\n mod q {\n use crate::S;\n" + CALLS.replace("pub fn", "fn") + " }\n}\n"
    ),
    "two attributes on one module, matched by two on the call's module": lib(PRE + impl_in("#[cfg(a)]\n#[cfg(b)]") + calls_in("#[cfg(b)]\n#[cfg(a)]")),
    "one attribute of the call matching two on the declaration's path": lib(
        PRE + "#[cfg(a)]\nmod x {\n #[cfg(a)]\n mod y {\n use crate::S;\n" + CRATE_IMPL + "\n }\n}\n" + calls_in("#[cfg(a)]")
    ),
}

# ---- every case where one of the conditions is not met: refused (a method call and a path call each)

REFUSED = {
    # 2. plain cfg, in place
    "the declaration under a cfg_attr that adds the cfg": lib(PRE + impl_in("#[cfg_attr(a, cfg(test))]") + calls_in("#[cfg(test)]")),
    "the call under a cfg_attr that adds the cfg": lib(PRE + impl_in("#[cfg(test)]") + calls_in("#[cfg_attr(a, cfg(test))]")),
    "both under a cfg_attr with one text": lib(PRE + impl_in("#[cfg_attr(a, cfg(test))]") + calls_in("#[cfg_attr(a, cfg(test))]")),
    "the declaration under an inner cfg_attr": lib(PRE + "mod a {\n #![cfg_attr(a, cfg(test))]\n " + CRATE_IMPL + " }\n" + calls_in("#[cfg(test)]")),
    "the call in an `if cfg!(..)` and the declaration under the cfg": lib(
        PRE + impl_in("#[cfg(test)]") + "pub fn run(s: &S) -> u8 { if cfg!(test) { s.go() } else { 0 } }\npub fn run_path() -> u8 { if cfg!(test) { S::make() } else { 0 } }\n"
    ),
    "the impl made by a macro under a predicate the macro takes": lib(
        PRE + "macro_rules! under { ($p:meta) => { #[cfg($p)] impl S { pub fn go(&self) -> u8 { 1 } pub fn make() -> u8 { 4 } } } }\nunder!(test);\n" + calls_in("#[cfg(test)]")
    ),
    "the impl made by a macro that writes the cfg": lib(
        PRE + "macro_rules! under { () => { #[cfg(test)] impl S { pub fn go(&self) -> u8 { 1 } pub fn make() -> u8 { 4 } } } }\nunder!();\n" + calls_in("#[cfg(test)]")
    ),
    # 3. the tokens
    "white space inside a literal": lib(PRE + impl_in('#[cfg(feature = "a b")]') + calls_in('#[cfg(feature = "a  b")]')),
    "a literal with no white space against one with": lib(PRE + impl_in('#[cfg(feature = "ab")]') + calls_in('#[cfg(feature = "a b")]')),
    "a raw string against the same string": lib(PRE + impl_in('#[cfg(feature = "a")]') + calls_in('#[cfg(feature = r"a")]')),
    "an escape against the same string": lib(PRE + impl_in('#[cfg(feature = "a")]') + calls_in('#[cfg(feature = "\\x61")]')),
    "the same tokens in another order": lib(PRE + impl_in("#[cfg(all(a, b))]") + calls_in("#[cfg(all(b, a))]")),
    "a predicate and the same one in `all`": lib(PRE + impl_in("#[cfg(test)]") + calls_in("#[cfg(all(test))]")),
    "a predicate and a different one": lib(PRE + impl_in("#[cfg(test)]") + calls_in("#[cfg(debug_assertions)]")),
    "a predicate and its negation": lib(PRE + impl_in("#[cfg(test)]") + calls_in("#[cfg(not(test))]")),
    # 4. one attribute of the declaration's path that nothing matches
    "the declaration under two attributes and the call under one": lib(PRE + impl_in("#[cfg(a)]\n#[cfg(b)]") + calls_in("#[cfg(a)]")),
    "the declaration under two attributes and the call under one and another": lib(PRE + impl_in("#[cfg(a)]\n#[cfg(b)]") + calls_in("#[cfg(a)]\n#[cfg(c)]")),
    "a module inside the one the impl is in, under one attribute more": lib(
        PRE + "#[cfg(a)]\nmod x {\n #[cfg(b)]\n mod y {\n use crate::S;\n" + CRATE_IMPL + "\n }\n}\n" + calls_in("#[cfg(a)]")
    ),
    "the type under an attribute the call is not under": lib(TR + "#[cfg(b)]\npub struct S;\n#[cfg(b)]\nimpl Tr for S {}\n" + impl_in("#[cfg(a)]") + calls_in("#[cfg(a)]")),
    "the method under an attribute the call is not under": lib(
        PRE + "#[cfg(a)]\nmod x {\n use crate::S;\n impl S { #[cfg(b)] pub fn go(&self) -> u8 { 1 } #[cfg(b)] pub fn make() -> u8 { 4 } }\n}\n" + calls_in("#[cfg(a)]")
    ),
    "the impl under an attribute the call is not under": lib(PRE + "#[cfg(a)]\nmod x {\n #[cfg(b)]\n " + CRATE_IMPL + "\n}\n" + calls_in("#[cfg(a)]")),
    # an attribute in the header of the impl or of the method is never on the path of a call
    "an attribute on a generic of the impl, with the call's predicate": lib(
        TR + "pub struct S<'a>(&'a u8);\nimpl Tr for S<'_> {}\n#[cfg(test)]\nmod a { impl<#[cfg(test)] 'a> crate::S<'a> { pub fn go(&self) -> u8 { 1 } pub fn make() -> u8 { 4 } } }\n"
        + "#[cfg(test)]\nmod b { use crate::S;\n fn run(s: &S<'_>) -> u8 { s.go() }\n fn run_path() -> u8 { S::make() } }\n"
    ),
    "an attribute on a parameter of the method, with the call's predicate": lib(
        PRE + "#[cfg(test)]\nmod a { impl crate::S { pub fn go(&self, #[cfg(test)] _x: u8) -> u8 { 1 } pub fn make(#[cfg(test)] _x: u8) -> u8 { 4 } } }\n"
        + "#[cfg(test)]\nmod b { use crate::S;\n fn run(s: &S) -> u8 { s.go(0) }\n fn run_path() -> u8 { S::make(0) } }\n"
    ),
    # what is not a `cfg` on the call's path
    "a test function that holds the calls": lib(PRE + impl_in("#[cfg(test)]") + "#[test]\nfn run_it() {\n let s: &S = &S;\n let a = s.go();\n let b = S::make();\n}\n"),
    "a function under an attribute macro": lib(PRE + impl_in("#[cfg(test)]") + calls_in("#[cfg(test)]\n#[my_macro]")),
    "the declaration's module under an attribute macro": lib(PRE + impl_in("#[cfg(test)]\n#[my_macro]") + calls_in("#[cfg(test)]")),
    "the declaration's module under a cfg_attr that may add a path": lib(PRE + impl_in('#[cfg(test)]\n#[cfg_attr(a, path = "x.rs")]') + calls_in("#[cfg(test)]")),
}


@pytest.mark.parametrize("name", sorted(EDGE))
def test_the_calls_under_the_same_predicate_are_edges(make_repo, name):
    assert outcomes(make_repo, EDGE[name]) == ("edge", "edge"), name


@pytest.mark.parametrize("name", sorted(REFUSED))
def test_a_condition_that_is_not_met_refuses_the_method_call_and_the_path_call(make_repo, name):
    assert outcomes(make_repo, REFUSED[name]) == ("refused", "refused"), name


# ---- 5. a second definition that could win


def method_edges(g):
    return [e for e in g.edges if e.kind == "call" and e.callee.endswith("S::go")]


def test_a_second_inherent_method_under_the_same_predicate_is_refused_as_two_definitions(make_repo):
    """rustc rejects it where both exist (E0592: shared_cfg_errors/dup_sibling_same_cfg.rs), so no program has it, and ripple does not choose."""
    files = lib(PRE + impl_in("#[cfg(test)]", "a") + impl_in("#[cfg(test)]", "c") + calls_in("#[cfg(test)]"))
    g = graph_of(make_repo, files)
    assert not method_edges(g) and [u for u in g.unresolved if u.text == "s.go"]
    assert not [e for e in g.edges if e.kind == "call" and e.callee.endswith("S::make")]


def test_an_impl_under_not_the_predicate_beside_the_one_under_it_is_refused_as_two_definitions(make_repo):
    """`#[cfg(not(test))] impl S` and the module under `#[cfg(test)]`: legal (only one exists in a build), and the call is read as having one of two."""
    files = lib(PRE + "#[cfg(not(test))]\n" + IMPL + "\n" + impl_in("#[cfg(test)]") + calls_in("#[cfg(test)]"))
    assert outcomes(make_repo, files) == ("refused", "refused")


def test_an_impl_under_another_predicate_beside_the_one_under_the_call_s_is_refused(make_repo):
    files = lib(PRE + impl_in("#[cfg(feature = \"x\")]", "c") + impl_in("#[cfg(test)]") + calls_in("#[cfg(test)]"))
    assert outcomes(make_repo, files) == ("refused", "refused")


def test_an_unconditional_impl_beside_the_one_under_the_predicate_is_refused_as_two_definitions(make_repo):
    files = lib(PRE + IMPL + "\n" + impl_in("#[cfg(test)]") + calls_in("#[cfg(test)]"))
    assert outcomes(make_repo, files) == ("refused", "refused")


def test_a_trait_method_at_an_earlier_probe_step_under_the_predicate_is_refused(make_repo):
    """`s: S` by value: the trait's `go(self)` for `S` is found at the first step, before the inherent `go(&self)` (at the autoref step)."""
    files = lib(
        PRE
        + "pub trait ByVal { fn go(self) -> u8; }\n#[cfg(test)]\nmod t { impl super::ByVal for super::S { fn go(self) -> u8 { 3 } } }\n"
        + impl_in("#[cfg(test)]")
        + calls_in("#[cfg(test)]", body="fn run(s: S) -> u8 { s.go() }\n")
    )
    g = graph_of(make_repo, files)
    assert not method_edges(g) and [u for u in g.unresolved if u.text == "s.go"]


def test_a_use_under_the_predicate_that_names_another_type_for_the_receiver_is_refused(make_repo):
    """In the call's module `S` is `other::S` (an explicit import under the module's own cfg beats the glob), which has no `go`."""
    files = lib(
        PRE
        + "pub mod other { pub struct S; impl super::Tr for S {} }\n"
        + impl_in("#[cfg(test)]")
        + "#[cfg(test)]\nmod b {\n use crate::*;\n #[cfg(test)]\n use crate::other::S;\n fn run(s: &S) -> u8 { s.go() }\n fn run_path() -> u8 { S::make() }\n}\n"
    )
    g = graph_of(make_repo, files)
    assert not method_edges(g)
    assert not [e for e in g.edges if e.kind == "call" and e.callee.endswith("S::make")]


def test_a_glob_under_the_predicate_that_may_name_the_type_is_refused(make_repo):
    """`use super::*` under the module's cfg may bring a `S` of its own (`other` has one, and the explicit `use crate::S` of the same module wins
    over it, so `S` is `crate::S`, and the impl is found: the glob only matters where nothing explicit names the type)."""
    files = lib(
        PRE
        + "pub mod other { pub struct S; impl super::Tr for S {} }\n"
        + impl_in("#[cfg(test)]")
        + "#[cfg(test)]\nmod b {\n #[cfg(test)]\n use crate::other::*;\n fn run(s: &S) -> u8 { s.go() }\n fn run_path() -> u8 { S::make() }\n}\n"
    )
    g = graph_of(make_repo, files)
    assert not method_edges(g)  # `S` is `other::S` here (the glob is the only import): the inherent `go` is crate::S's
    assert not [e for e in g.edges if e.kind == "call" and e.callee.endswith("S::make")]


def test_a_module_made_by_a_macro_has_no_call_ripple_reads(make_repo):
    """A call inside a macro's body is no call site (the macro may be invoked under any attribute, or never): there is nothing to draw an edge from."""
    files = lib(PRE + impl_in("#[cfg(test)]") + "macro_rules! calls { () => { #[cfg(test)] mod b { use crate::S;\n" + CALLS.replace("pub fn", "fn") + "} } }\ncalls!();\n")
    g = graph_of(make_repo, files)
    assert not [e for e in g.edges if e.kind == "call" and e.callee.endswith(("S::go", "S::make"))]


def test_the_type_under_another_predicate_than_the_call_is_refused(make_repo):
    files = lib(TR + "#[cfg(not(any()))]\npub struct S;\n#[cfg(not(any()))]\nimpl Tr for S {}\n" + impl_in("#[cfg(test)]") + calls_in("#[cfg(test)]"))
    assert outcomes(make_repo, files) == ("refused", "refused")


def test_the_compiled_probe_of_an_attribute_macro_that_deletes_the_cfgs_refuses_the_calls_inside_it():
    """method_cfg_predicate_macros: `#[stripcfg]` deletes the `#[cfg(test)]` of the call's functions (they are in every build, and find the trait's
    method without `cfg(test)`) and of the impl's module (the impl is in every build): the `cfg`s ripple reads inside the item are not the compiler's."""
    from test_rs_method_probes import probe_of

    probe = probe_of("method_cfg_predicate_macros")
    got = {(line, written): result for _, _, _, result, _, line, written, _ in probe.index.resolved_sites() if line in (67, 71, 94, 98)}
    assert len(got) == 4, got
    assert all(r[0] == "unresolved" and "attribute macro" in r[1] for r in got.values()), got


# ---- what the rule does not reach


def test_the_receiver_s_own_let_or_parameter_under_the_predicate_is_refused(make_repo):
    for body in (
        "fn run() -> u8 { #[cfg(test)] let s: &S = &S; s.go() }",
        "fn run(#[cfg(test)] s: &S) -> u8 { s.go() }",
    ):
        g = graph_of(make_repo, lib(PRE + impl_in("#[cfg(test)]") + calls_in("#[cfg(test)]", body=body + "\n")))
        assert not method_edges(g), body
        assert [u for u in g.unresolved if u.text == "s.go"], body


def test_the_test_attribute_is_never_a_predicate_the_call_shares(make_repo):
    """`#[test]` is no `cfg`: the function exists only in a test build, and ripple does not take that for `cfg(test)`."""
    files = lib(PRE + impl_in("#[cfg(test)]") + "#[test]\nfn run_it() { let s: &S = &S; s.go(); }\n")
    g = graph_of(make_repo, files)
    assert not method_edges(g) and [u for u in g.unresolved if u.text == "s.go"]


# ---- 1. one crate: every other target of a package, and every other member of a workspace, is a crate of its own

APP = '[package]\nname = "app"\nversion = "0.1.0"\nedition = "2021"\n'
LIB_UNDER_TEST = "pub struct S;\n" + TR + "impl Tr for S {}\n#[cfg(test)]\nmod testutil { " + IMPL.replace("impl S", "impl super::S") + " }\n"
LIB_PLAIN = "pub struct S;\n" + TR + "impl Tr for S {}\n" + IMPL + "\n"
OTHER_CALLS = "#[cfg(test)]\nmod t {\n use app::{S, Tr};\n pub fn run(s: &S) -> u8 { s.go() }\n pub fn run_path() -> u8 { S::make() }\n}\n"
OTHER_PLAIN = "use app::{S, Tr};\npub fn run(s: &S) -> u8 { s.go() }\npub fn run_path() -> u8 { S::make() }\n"

TARGETS = {
    "the binary of the package": ("src/main.rs", {}),
    "another binary": ("src/bin/other.rs", {}),
    "an integration test": ("tests/it.rs", {}),
    "an example": ("examples/ex.rs", {}),
    "a bench": ("benches/b.rs", {}),
}


def calls_from(make_repo, lib_source, path, source, extra=None):
    files = {"Cargo.toml": APP, "src/lib.rs": lib_source, path: source, **(extra or {})}
    if path != "src/main.rs":
        files.setdefault("src/main.rs", "fn main() {}\n")
    g = graph_of(make_repo, files)
    edges = [e for e in g.edges if e.kind == "call" and e.file == path and e.callee.endswith(("S::go", "S::make"))]
    refused = [u for u in g.unresolved if u.file == path and u.text.endswith(("s.go", "S::make"))]
    return edges, refused


@pytest.mark.parametrize("name", sorted(TARGETS))
def test_a_target_of_the_package_is_a_crate_of_its_own_whose_cfg_is_not_the_library_s(make_repo, name):
    path, _ = TARGETS[name]
    edges, refused = calls_from(make_repo, LIB_PLAIN, path, OTHER_PLAIN)  # the control: the call finds the library's type, and an unconditional impl is an edge
    assert len(edges) == 2 and not refused, (edges, refused)
    edges, refused = calls_from(make_repo, LIB_UNDER_TEST, path, OTHER_CALLS)
    assert not edges and len(refused) == 2, (edges, refused)


def test_the_library_s_own_module_under_the_same_predicate_is_an_edge(make_repo):
    """The control of the targets above: the same shape inside the library is one crate."""
    source = LIB_UNDER_TEST + "#[cfg(test)]\nmod t {\n use crate::{S, Tr};\n pub fn run(s: &S) -> u8 { s.go() }\n pub fn run_path() -> u8 { S::make() }\n}\n"
    g = graph_of(make_repo, {"Cargo.toml": APP, "src/lib.rs": source})
    assert len([e for e in g.edges if e.kind == "call" and e.callee.endswith(("S::go", "S::make"))]) == 2


WORKSPACE = '[workspace]\nmembers = ["app", "tool"]\nresolver = "2"\n'
TOOL = '[package]\nname = "tool"\nversion = "0.1.0"\nedition = "2021"\n\n[dependencies]\napp = { path = "../app" }\n'


def workspace_calls(make_repo, tool_cargo, tool_files, lib_source=LIB_UNDER_TEST, where="tool/src/lib.rs", source=OTHER_CALLS, features=""):
    files = {
        "Cargo.toml": WORKSPACE,
        "app/Cargo.toml": APP + features,
        "app/src/lib.rs": lib_source,
        "tool/Cargo.toml": tool_cargo,
        "tool/src/lib.rs": "",
        **tool_files,
        where: source,
    }
    g = build_graph_of(make_repo, files)
    edges = [e for e in g.edges if e.kind == "call" and e.file == where and e.callee.endswith(("S::go", "S::make"))]
    refused = [u for u in g.unresolved if u.file == where and u.text.endswith(("s.go", "S::make"))]
    return edges, refused


def build_graph_of(make_repo, files):
    from ripple.indexer import build_graph
    from ripple.profile import RUST

    return build_graph(make_repo(files), RUST)


def test_another_member_of_a_workspace_is_a_crate_of_its_own(make_repo):
    edges, refused = workspace_calls(make_repo, TOOL, {}, lib_source=LIB_PLAIN, source=OTHER_PLAIN)
    assert len(edges) == 2 and not refused, (edges, refused)  # the control
    edges, refused = workspace_calls(make_repo, TOOL, {})
    assert not edges and len(refused) == 2, (edges, refused)


def test_a_feature_is_the_crate_s_own_so_a_member_s_feature_is_not_the_library_s(make_repo):
    """`default = ["f"]` of the tool turns its `f` on; the library's `f` is its own (a dependency's features are not the dependent's)."""
    lib_source = "pub struct S;\n" + TR + "impl Tr for S {}\n#[cfg(feature = \"f\")]\nmod f { " + IMPL.replace("impl S", "impl super::S") + " }\n"
    tool_calls = '#[cfg(feature = "f")]\nmod t {\n use app::{S, Tr};\n pub fn run(s: &S) -> u8 { s.go() }\n pub fn run_path() -> u8 { S::make() }\n}\n'
    tool = TOOL + '\n[features]\nf = []\ndefault = ["f"]\n'
    edges, refused = workspace_calls(make_repo, tool, {}, lib_source=lib_source, source=tool_calls, features='\n[features]\nf = []\n')
    assert not edges and len(refused) == 2, (edges, refused)


def test_a_procedural_macro_crate_is_a_crate_of_its_own(make_repo):
    tool = TOOL + "\n[lib]\nproc-macro = true\n"
    edges, refused = workspace_calls(make_repo, tool, {})
    assert not edges and len(refused) == 2, (edges, refused)


def test_a_build_script_is_a_crate_of_its_own(make_repo):
    tool = TOOL.replace("[dependencies]", "[build-dependencies]")
    edges, refused = workspace_calls(make_repo, tool, {}, where="tool/build.rs", source=OTHER_CALLS + "fn main() {}\n")
    assert not edges and len(refused) == 2, (edges, refused)
    edges, refused = workspace_calls(make_repo, tool, {}, lib_source=LIB_PLAIN, where="tool/build.rs", source=OTHER_PLAIN + "fn main() {}\n")
    assert len(edges) == 2 and not refused, (edges, refused)  # the control: the script reaches the library's type
