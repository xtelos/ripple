"""What a value name written in a block can mean between the receiver's binding and the call, and what proves it is the binding.

A receiver `s` bound by a parameter or a `let` is that binding at the call only if nothing between them gives the name another
meaning. Scopes nest (Reference, Names, Scopes): the items and imports of a block are a scope inside the parameters and outside a `let` of
the same block, so an item of the name, an import that gives it, or a glob (which may give any name) in any block around the call may be what
the name is (probe method_block_names: compiled and run). This is an allow-list over what a block's statements can be
(rs_indexer.BLOCK_*): every kind is classified as one that cannot give a value name, one that may (and Scope.items, Scope.uses and
Scope.globs say which), or one whose effect on the name is settled elsewhere (a `let` is counted by count_bindings, a statement macro by
Scope.item_macros). A kind not classified marks its block opaque, and the call is refused.
"""

import pytest

pytest.importorskip("tree_sitter_rust")

from test_rs_method_cfg import corpus, parse  # noqa: E402
from test_rs_method_guards import BASE, graph_of, result_of  # noqa: E402

from ripple import rs_indexer as rs  # noqa: E402

EDGE = ("edge", "src/lib:S::go")

PRELUDE = "pub struct S;\nimpl S { pub fn go(&self) -> u8 { 1 } }\npub mod other { pub fn s() -> u8 { 0 } pub fn t() -> u8 { 1 } pub const k: u8 = 0; pub struct u; }\n"


def run_with(body, params="s: &S"):
    return PRELUDE + "pub fn run(" + params + ") -> u8 { " + body + " }\n"


# a block around the call that gives the name `s` (or may): refused
GIVES = {
    "a fn item": "fn s() -> u8 { 0 } s.go()",
    "a const item": "const s: u8 = 0; s.go()",
    "a static item": "static s: u8 = 0; s.go()",
    "a unit struct": "struct s; s.go()",
    "a tuple struct": "struct s(u8); s.go()",
    "a foreign item": 'extern "C" { fn s(); } s.go()',
    "an import of the name": "use crate::other::s; s.go()",
    "an import under another name": "use crate::other::t as s; s.go()",
    "an import in a list": "use crate::other::{s, t}; s.go()",
    "an import of a const": "use crate::other::k as s; s.go()",
    "a glob": "use crate::other::*; s.go()",
    "a glob of a std module": "use std::fmt::*; s.go()",
    "a glob of an enum": "enum E { A } use E::*; s.go()",
    "an import after the call": "let r = s.go(); use crate::other::s; r",
    "an import in an outer block": "{ use crate::other::*; { s.go() } }",
}
# an item of a block that cannot give a value name: the call is an edge
HARMLESS = {
    "a type alias": "type s = u8; s.go()",
    "an enum": "enum s {} s.go()",
    "a union": "union s { a: u8 } s.go()",
    "a trait": "trait s {} s.go()",
    "an extern crate": "extern crate std as s; s.go()",
    "a macro_rules!": "macro_rules! s { () => {}; } s.go()",
    "an impl": "impl S { pub fn other(&self) {} } s.go()",
    "an import of another name": "use crate::other::t; s.go()",
    "an import of a type": "use std::fmt::Debug; s.go()",
    "a fn of another name": "fn t() -> u8 { 0 } s.go()",
    "a comment and an attribute": "// s\n#[allow(unused)] let x = 1; /* s */ s.go()",
    "an expression statement": "let x = 1; x; s.go()",
    "an empty statement": ";; s.go()",
    "a sibling block with the import": "{ use crate::other::s; let _ = s(); } s.go()",
    "a block expression": "let x = { 1 }; s.go()",
    "a standard macro": 'println!("{}", 1); s.go()',
}


@pytest.mark.parametrize("what", sorted(GIVES))
def test_a_block_around_the_call_that_may_give_the_name_refuses_the_call(make_repo, what):
    g = graph_of(make_repo, {"src/lib.rs": run_with(GIVES[what])})
    assert not [e for e in g.edges if e.kind == "call" and e.caller == "src/lib:run" and e.callee.endswith("S::go")], what
    assert rs.M_SHADOWED in {u.reason for u in g.unresolved if u.text == "s.go"}, what


@pytest.mark.parametrize("what", sorted(HARMLESS))
def test_an_item_that_cannot_give_a_value_name_costs_nothing(make_repo, what):
    assert result_of(graph_of(make_repo, {"src/lib.rs": run_with(HARMLESS[what])})) == EDGE, what


def test_a_module_written_in_a_block_gives_no_value_name_but_is_a_file_ripple_does_not_read(make_repo):
    """`mod s {}` is in the type namespace, so the receiver is still the parameter (probe method_block_names), but a module in a block is source
    the crate refuses as a whole (method_taint)."""
    g = graph_of(make_repo, {"src/lib.rs": run_with("mod s {} s.go()")})
    reasons = {u.reason for u in g.unresolved if u.text == "s.go"}
    assert reasons and all("did not read" in r for r in reasons) and rs.M_SHADOWED not in reasons


def test_the_same_holds_for_a_let(make_repo):
    for what in ("use crate::other::s;", "use crate::other::*;", "fn s() -> u8 { 0 }"):
        source = PRELUDE + "pub fn run() -> u8 { let s: &S = &S; { " + what + " s.go() } }\n"
        g = graph_of(make_repo, {"src/lib.rs": source})
        assert rs.M_SHADOWED in {u.reason for u in g.unresolved if u.text == "s.go"}, what


def test_a_let_after_the_import_is_refused_too_where_the_compiler_takes_the_let(make_repo):
    """`use crate::other::s; let s: &S = &S; s.go()` is S::go for rustc (the `let` is innermost); ripple does not tell, and refuses."""
    source = PRELUDE + "pub fn run() -> u8 { use crate::other::s; let s: &S = &S; s.go() }\n"
    g = graph_of(make_repo, {"src/lib.rs": source})
    assert rs.M_SHADOWED in {u.reason for u in g.unresolved if u.text == "s.go"}


def test_an_import_of_the_name_at_the_module_level_is_not_a_block_around_the_call(make_repo):
    """A module's import is outside the parameter: the parameter is the name. (A const or a unit struct of the name there is a pattern that
    binds nothing, which M_PATTERN_ITEM says.)"""
    source = "pub struct S;\nimpl S { pub fn go(&self) -> u8 { 1 } }\nmod other { pub fn s() -> u8 { 0 } }\nuse other::s;\npub fn run(s: &S) -> u8 { s.go() }\n"
    assert result_of(graph_of(make_repo, {"src/lib.rs": source})) == EDGE


def test_a_module_level_const_of_the_name_makes_the_binding_a_pattern(make_repo):
    source = "pub struct S;\nimpl S { pub fn go(&self) -> u8 { 1 } }\npub const s: u8 = 0;\npub fn run() -> u8 { let s: &S = &S; s.go() }\n"
    g = graph_of(make_repo, {"src/lib.rs": source})
    assert rs.M_PATTERN_ITEM in {u.reason for u in g.unresolved if u.text == "s.go"}


# ---- the invariant: every kind a block holds is classified, and each class is one the code reads


def blocks_of(src):
    stack = [parse(src).root_node]
    while stack:
        n = stack.pop()
        if n.type == "block":
            yield n
        stack.extend(n.children)


def test_every_kind_of_node_a_block_of_the_probe_crates_holds_is_classified():
    seen = {}
    for name, src in corpus():
        for block in blocks_of(src):
            for c in block.named_children:
                seen.setdefault(c.type, name)
    assert len(seen) >= 30, sorted(seen)
    unclassified = {kind: name for kind, name in seen.items() if not rs.block_child_known(kind)}
    assert not unclassified, f"a kind of statement or expression nobody classified: {unclassified}"


def test_the_classes_do_not_overlap_and_the_kinds_that_declare_are_the_items_scopes_read():
    classes = [rs.BLOCK_DECLARES, rs.BLOCK_IMPORTS, rs.BLOCK_TYPE_ONLY, rs.BLOCK_NO_NAMES]
    for i, a in enumerate(classes):
        for b in classes[i + 1:]:
            assert not a & b, a & b
    items = rs.BLOCK_DECLARES | rs.BLOCK_IMPORTS | rs.BLOCK_TYPE_ONLY
    assert items <= rs.ITEM_NODES | {"struct_item"}, items - rs.ITEM_NODES  # a block with any of them is a scope of its own (collect_blocks)
    assert rs.BLOCK_DECLARES | rs.BLOCK_IMPORTS | rs.BLOCK_TYPE_ONLY >= {"function_item", "use_declaration", "const_item", "static_item", "struct_item", "foreign_mod_item"}


def test_a_kind_nobody_classified_marks_its_block_opaque_and_refuses_the_call(make_repo, monkeypatch):
    monkeypatch.setattr(rs, "BLOCK_NO_NAMES", rs.BLOCK_NO_NAMES - {"empty_statement"})
    g = graph_of(make_repo, {"src/lib.rs": run_with(";; s.go()")})
    assert rs.M_SHADOWED in {u.reason for u in g.unresolved if u.text == "s.go"}


def test_the_control_is_an_edge(make_repo):
    assert result_of(graph_of(make_repo, {"src/lib.rs": BASE})) == EDGE
