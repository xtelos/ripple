"""A cfg that governs both the call and the declaration, through an ancestor they share, does not make the declaration conditional for that call.

The rule (rs_indexer, at head_shared): an edge to an inherent method or associated function is drawn only if every conditional attribute on the
path from the crate root to the declaration (the method, its impl, the type, and everything enclosing them) is also on the path from the crate root
to the call, as the same attribute on the same syntactic ancestor, in the same crate; and an attribute is one a call may share only if it does no
more than make its host exist or not (`cfg`, or a `cfg_attr` of those and inert attributes). Where that holds the call exists only if every one of
those attributes is on, so the declaration exists, and the call is read as if they were not there. Anything else is refused as before. (An attribute of the
declaration's path is also matched by another plain `cfg` on the call's path with the identical predicate, in the same crate: tests/test_rs_same_predicate.py.)

Compiled and run: the probes method_cfg_shared, method_cfg_shared_macro and method_cfg_crates (one file that is a module of both the library and the
binary of a package: the same attribute at the same byte is on in one crate's test build and off in the other's; every `#[test]` asserts which function ran under `cargo test`;
`cargo run --bin show` is the build without `cfg(test)`), and bench/fixtures/rust-probes/shared_cfg_errors (the second definitions that rustc
rejects: E0592). The compiler's answers are the oracle files; this runs without a toolchain.
"""

import re

import pytest

pytest.importorskip("tree_sitter_rust")

from test_rs_method_guards import graph_of  # noqa: E402
from test_rs_method_probes import markers, probe_of  # noqa: E402
from test_rs_probes import CRATES  # noqa: E402

from ripple import rs_indexer as rs  # noqa: E402

TR = "pub trait Tr { fn go(&self) -> u8 { 2 } fn make() -> u8 { 3 } }\n"
IMPL = "impl S { pub fn go(&self) -> u8 { 1 } pub fn make() -> u8 { 4 } }"
CALLS = "pub fn run(s: &S) -> u8 { s.go() }\npub fn run_path() -> u8 { S::make() }\n"


def outcomes(make_repo, files):
    """("edge" | "refused") of the one method call `s.go` and of the one path call `S::make`, in whichever function they are written."""
    g = graph_of(make_repo, files)
    out = []
    for text, suffix in (("s.go", "S::go"), ("S::make", "S::make")):
        edges = [e for e in g.edges if e.kind == "call" and e.callee.endswith(suffix)]
        refused = [u for u in g.unresolved if u.text in (text, text.replace("s.go", "self.go"))]
        assert len(edges) + len(refused) == 1, (text, edges, refused)
        out.append("edge" if edges else "refused")
    return tuple(out)


def lib(source):
    return {"src/lib.rs": source}


# ---- the cases where the call and the declaration are under the same attribute: an edge, for a method call and a path call

SHARED = {
    "an inline module that holds the type, the impl and the calls": lib(TR + "#[cfg(test)]\nmod m {\n pub struct S;\n impl super::Tr for S {}\n" + IMPL + "\n" + CALLS.replace("pub fn", "fn") + "}\n"),
    "a file module declared `#[cfg(test)] mod m;` that holds the impl and the calls": {
        "src/lib.rs": TR + "#[cfg(test)]\nmod m;\n",
        "src/m.rs": "pub struct S;\nimpl super::Tr for S {}\n" + IMPL + "\n" + CALLS,
    },
    "a file module whose own `#![cfg(test)]` is the attribute": {
        "src/lib.rs": TR + "mod m;\n",
        "src/m.rs": "#![cfg(test)]\npub struct S;\nimpl super::Tr for S {}\n" + IMPL + "\n" + CALLS,
    },
    "an inline module's own inner attribute": lib(TR + "mod m {\n #![cfg(test)]\n pub struct S;\n impl super::Tr for S {}\n" + IMPL + "\n" + CALLS.replace("pub fn", "fn") + "}\n"),
    "two attributes on the one module": lib(TR + "#[cfg(test)]\n#[cfg(not(any()))]\nmod m {\n pub struct S;\n impl super::Tr for S {}\n" + IMPL + "\n" + CALLS.replace("pub fn", "fn") + "}\n"),
    "a cfg_attr that adds a cfg": lib(TR + "#[cfg_attr(not(test), cfg(any()))]\nmod m {\n pub struct S;\n impl super::Tr for S {}\n" + IMPL + "\n" + CALLS.replace("pub fn", "fn") + "}\n"),
    "a cfg_attr of a cfg_attr of a cfg and an inert attribute": lib(TR + "#[cfg_attr(a, cfg_attr(b, cfg(c)), allow(dead_code))]\nmod m {\n pub struct S;\n impl super::Tr for S {}\n" + IMPL + "\n" + CALLS.replace("pub fn", "fn") + "}\n"),
    "a module inside the one the impl is in, under one attribute more": lib(TR + "#[cfg(a)]\nmod m {\n pub struct S;\n impl super::Tr for S {}\n" + IMPL + "\n #[cfg(b)]\n mod n {\n use super::S;\n" + CALLS.replace("pub fn", "fn") + " }\n}\n"),
    "the impl and the call both in a module inside the one under the first attribute": lib(TR + "#[cfg(a)]\nmod m {\n #[cfg(b)]\n mod n {\n pub struct S;\n impl super::super::Tr for S {}\n" + IMPL + "\n" + CALLS.replace("pub fn", "fn") + " }\n}\n"),
    "a call in the arguments of a macro in the shared module": lib(TR + "#[cfg(a)]\nmod m {\n pub struct S;\n impl super::Tr for S {}\n" + IMPL + "\n fn run(s: &S) -> u8 { println!(\"{}\", s.go()); 0 }\n fn run_path() -> u8 { assert_eq!(S::make(), 4); 0 }\n}\n"),
    "a call inside the impl that is under the attribute": lib(TR + "pub struct S;\nimpl Tr for S {}\n#[cfg(a)]\nimpl S {\n pub fn go(&self) -> u8 { 1 }\n pub fn make() -> u8 { 4 }\n pub fn a(&self) -> u8 { self.go() }\n pub fn b() -> u8 { S::make() }\n}\n"),
    "a call inside an impl under its own inner attribute": lib(TR + "pub struct S;\nimpl Tr for S {}\nimpl S {\n #![cfg(a)]\n pub fn go(&self) -> u8 { 1 }\n pub fn make() -> u8 { 4 }\n pub fn a(&self) -> u8 { self.go() }\n pub fn b() -> u8 { S::make() }\n}\n"),
    "a call in the body of the method that is under the attribute": lib(TR + "pub struct S;\nimpl Tr for S {}\nimpl S {\n #[cfg(a)]\n pub fn go(&self) -> u8 { self.go() }\n #[cfg(a)]\n pub fn make() -> u8 { S::make() }\n}\n"),
    "a function under the attribute that holds the type, the impl and the calls": lib(TR + "#[cfg(a)]\nfn f() {\n struct S;\n impl Tr for S {}\n" + IMPL + "\n let s: &S = &S;\n let a = s.go();\n let b = S::make();\n}\n"),
    "a block under the attribute that holds the type, the impl and the calls": lib(TR + "fn f() {\n #[cfg(a)]\n {\n struct S;\n impl Tr for S {}\n" + IMPL + "\n let s: &S = &S;\n let a = s.go();\n let b = S::make();\n }\n}\n"),
    "an arm under the attribute that holds the impl and the calls": lib(TR + "fn f(x: u8) -> u8 {\n match x {\n #[cfg(a)]\n 0 => {\n struct S;\n impl Tr for S {}\n" + IMPL + "\n let s: &S = &S;\n let a = s.go();\n S::make()\n }\n _ => 2,\n }\n}\n"),
    "a child file of the file module, the call in the child and the impl in the parent file": {
        "src/lib.rs": TR + "#[cfg(test)]\nmod m;\n",
        "src/m.rs": "pub struct S;\nimpl super::Tr for S {}\n" + IMPL + "\nmod n;\n",
        "src/m/n.rs": "use super::S;\n" + CALLS,
    },
    "a child file of the file module, the impl in the child (no attribute of its own) and the call in the parent file": {
        "src/lib.rs": TR + "#[cfg(test)]\nmod m;\n",
        "src/m.rs": "pub struct S;\nimpl super::Tr for S {}\nmod n;\n" + CALLS,
        "src/m/n.rs": "use super::S;\n" + IMPL.replace("impl S", "impl S") + "\n",
    },
}

# ---- every other conditional declaration: refused

NOT_SHARED = {
    "two sibling modules under different attributes": lib(TR + "pub struct S;\nimpl Tr for S {}\n#[cfg(a)]\nmod a { " + IMPL.replace("impl S", "impl crate::S") + " }\n#[cfg(b)]\nmod b { use crate::S;\n" + CALLS.replace("pub fn", "fn") + "}\n"),
    "the call under an attribute of its own and the impl under another": lib(TR + "pub struct S;\nimpl Tr for S {}\n#[cfg(a)]\n" + IMPL + "\n#[cfg(b)]\n" + CALLS),
    "the call under any attribute at all": lib(TR + "pub struct S;\nimpl Tr for S {}\n#[cfg(a)]\n" + IMPL + "\npub fn run(s: &S) -> u8 { #[cfg(b)] { s.go() } }\npub fn run_path() -> u8 { #[cfg(b)] { S::make() } }\n"),
    "the impl's own attribute and the call outside it": lib(TR + "pub struct S;\nimpl Tr for S {}\n#[cfg(a)]\n" + IMPL + "\n" + CALLS),
    "the impl's own inner attribute and the call outside it": lib(TR + "pub struct S;\nimpl Tr for S {}\nimpl S { #![cfg(a)] pub fn go(&self) -> u8 { 1 } pub fn make() -> u8 { 4 } }\n" + CALLS),
    "the method's own attribute and the call outside it": lib(TR + "pub struct S;\nimpl Tr for S {}\nimpl S { #[cfg(a)] pub fn go(&self) -> u8 { 1 } #[cfg(a)] pub fn make() -> u8 { 4 } }\n" + CALLS),
    "the method's own attribute and the call in another method of the impl": lib(TR + "pub struct S;\nimpl Tr for S {}\nimpl S {\n #[cfg(a)]\n pub fn go(&self) -> u8 { 1 }\n #[cfg(a)]\n pub fn make() -> u8 { 4 }\n pub fn x(&self) -> u8 { self.go() }\n pub fn y() -> u8 { S::make() }\n}\n"),
    "an impl in a block that does not hold the call": lib(TR + "pub struct S;\nimpl Tr for S {}\nfn setup() { #[cfg(a)] { " + IMPL + " } }\n" + CALLS),
    "an impl in a function that does not hold the call": lib(TR + "pub struct S;\nimpl Tr for S {}\n#[cfg(a)]\nfn setup() { " + IMPL + " }\n" + CALLS),
    "an impl in a module and the call in the module's parent": lib(TR + "pub struct S;\nimpl Tr for S {}\n#[cfg(a)]\nmod m { " + IMPL.replace("impl S", "impl super::S") + " }\n" + CALLS),
    "an impl in an inner module under one attribute more and the call in the outer one": lib(TR + "#[cfg(a)]\nmod m {\n pub struct S;\n impl super::Tr for S {}\n #[cfg(b)]\n mod n { " + IMPL.replace("impl S", "impl super::S") + " }\n" + CALLS.replace("pub fn", "fn") + "}\n"),
    "a file module declared under an attribute and the call in the file that declares it": {
        "src/lib.rs": TR + "pub struct S;\nimpl Tr for S {}\n#[cfg(test)]\nmod m;\n" + CALLS,
        "src/m.rs": IMPL.replace("impl S", "impl crate::S") + "\n",
    },
    "a file module under its own inner attribute and the call in the file that declares it": {
        "src/lib.rs": TR + "pub struct S;\nimpl Tr for S {}\nmod m;\n" + CALLS,
        "src/m.rs": "#![cfg(test)]\n" + IMPL.replace("impl S", "impl crate::S") + "\n",
    },
    "a file module with the attribute on its declaration and a child file under one of its own": {
        "src/lib.rs": TR + "#[cfg(a)]\nmod m;\n",
        "src/m.rs": "pub struct S;\nimpl super::Tr for S {}\n#[cfg(b)]\nmod n;\n" + CALLS,
        "src/m/n.rs": "use super::S;\n" + IMPL + "\n",
    },
    "the same inner attribute offset in two files": {
        "src/lib.rs": TR + "pub struct S;\nimpl Tr for S {}\nmod a;\nmod b;\n",
        "src/a.rs": "#![cfg(x)]\nuse crate::S;\n" + CALLS,
        "src/b.rs": "#![cfg(y)]\n" + IMPL.replace("impl S", "impl crate::S") + "\n",
    },
    "an attribute on a generic parameter of the impl": lib(TR + "pub struct S<'a>(&'a u8);\nimpl Tr for S<'_> {}\nimpl<#[cfg(a)] 'a> S<'a> { pub fn go(&self) -> u8 { 1 } pub fn make() -> u8 { 4 } pub fn x(&self) -> u8 { self.go() } pub fn y() -> u8 { S::make() } }\n"),
    "the type's own attribute inside the shared module (another predicate)": lib(TR + "#[cfg(a)]\nmod m {\n #[cfg(b)]\n pub struct S;\n #[cfg(b)]\n impl super::Tr for S {}\n" + IMPL + "\n" + CALLS.replace("pub fn", "fn") + "}\n"),
    "an attribute that is no cfg on the module (not what the call shares)": lib(TR + "#[cfg_attr(a, no_implicit_prelude)]\nmod m {\n pub struct S;\n impl super::Tr for S {}\n" + IMPL + "\n" + CALLS.replace("pub fn", "fn") + "}\n"),
    "a test function that holds the type, the impl and the calls": lib(TR + "#[test]\nfn f() {\n struct S;\n impl Tr for S {}\n" + IMPL + "\n let s: &S = &S;\n let a = s.go();\n let b = S::make();\n}\n"),
    "a cfg_attr with a payload that is no cfg": lib(TR + "#[cfg_attr(a, path = \"x.rs\")]\nmod m {\n pub struct S;\n impl super::Tr for S {}\n" + IMPL + "\n" + CALLS.replace("pub fn", "fn") + "}\n"),
}


@pytest.mark.parametrize("name", sorted(SHARED))
def test_a_call_under_the_cfg_of_its_declaration_is_an_edge(make_repo, name):
    assert outcomes(make_repo, SHARED[name]) == ("edge", "edge"), name


@pytest.mark.parametrize("name", sorted(NOT_SHARED))
def test_a_declaration_under_a_cfg_the_call_is_not_under_is_refused(make_repo, name):
    assert outcomes(make_repo, NOT_SHARED[name]) == ("refused", "refused"), name


def test_the_receiver_binding_under_a_cfg_is_never_shared(make_repo):
    """The `let` is a statement before the call and the parameter is not an ancestor of the body: neither is on the call's path, whatever the impl is under."""
    for body in (
        "fn run() -> u8 { #[cfg(a)] let s: &S = &S; s.go() }",
        "fn run(#[cfg(a)] s: &S) -> u8 { s.go() }",
    ):
        source = TR + "#[cfg(a)]\nmod m {\n pub struct S;\n impl super::Tr for S {}\n impl S { pub fn go(&self) -> u8 { 1 } }\n " + body + "\n}\n"
        g = graph_of(make_repo, lib(source))
        assert not [e for e in g.edges if e.kind == "call" and e.callee.endswith("S::go")], body
        assert [u for u in g.unresolved if u.text == "s.go"], body


def test_self_in_an_impl_under_its_own_attribute_is_read_where_the_method_is_in_another_impl(make_repo):
    """`Self` of an impl that is under a cfg is the same type for a call inside the impl, which is under that attribute too; an attribute on a
    generic parameter of the impl is not one the call is under, and `Self` may depend on it."""
    own = "pub struct S;\nimpl Tr for S {}\nimpl S { pub fn go(&self) -> u8 { 1 } }\n#[cfg(a)]\nimpl S { pub fn x(&self) -> u8 { self.go() } }\n"
    g = graph_of(make_repo, lib(TR + own))
    assert [e.callee for e in g.edges if e.kind == "call" and e.caller == "src/lib:S::x"] == ["src/lib:S::go"]
    generic = "pub struct S<'a>(&'a u8);\nimpl Tr for S<'_> {}\nimpl S<'_> { pub fn go(&self) -> u8 { 1 } }\nimpl<#[cfg(a)] 'a> S<'a> { pub fn x(&self) -> u8 { self.go() } }\n"
    g = graph_of(make_repo, lib(TR + generic))
    assert not [e for e in g.edges if e.kind == "call" and e.callee.endswith("S::go")]
    assert [u for u in g.unresolved if u.text == "self.go"]


def test_a_cfg_is_read_by_the_crate_that_has_it_so_a_call_from_another_crate_shares_none(make_repo):
    """`cfg(test)` and the features are the crate's own: the binary of a package is built with the library's cfg as the library's own build has it, so a call
    from `main.rs` to an impl the library has under a cfg is not under that cfg."""
    files = {
        "src/lib.rs": TR + "pub struct S;\nimpl Tr for S {}\n#[cfg(test)]\nmod m { " + IMPL.replace("impl S", "impl super::S") + " }\n",
        "src/main.rs": "fn main() { let _ = app::S::make(); }\n",
    }
    g = graph_of(make_repo, files)
    assert not [e for e in g.edges if e.kind == "call" and e.callee.endswith("S::make")]
    assert [u for u in g.unresolved if u.text == "app::S::make"]


TWIN_X = (
    "#[cfg(test)]\npub mod inner {\n use app::Tr;\n"
    + IMPL.replace("impl S", "impl crate::S")
    + "\n pub fn run(s: &app::S) -> u8 { s.go() }\n pub fn run_path() -> u8 { app::S::make() }\n}\n"
)
TWIN_LIB = "extern crate self as app;\npub mod x;\npub struct S;\n" + TR + "impl Tr for S {}\n"


def twin_outcomes(make_repo, main):
    """("edge" | "refused", the reason or the callee) of the method call `s.go` and of the path call `app::S::make` of `x.rs`."""
    g = graph_of(make_repo, {"src/lib.rs": TWIN_LIB, "src/main.rs": main, "src/x.rs": TWIN_X})
    out = []
    for text, suffix in (("s.go", "S::go"), ("app::S::make", "S::make")):
        edges = [e for e in g.edges if e.kind == "call" and e.file == "src/x.rs" and e.callee.endswith(suffix)]
        refused = [u for u in g.unresolved if u.file == "src/x.rs" and u.text == text]
        assert len(edges) + len(refused) == 1, (text, edges, refused)
        out.append(("edge", edges[0].callee) if edges else ("refused", refused[0].reason))
    return out


def test_a_file_that_is_a_module_of_two_crates_shares_no_cfg_across_them(make_repo):
    """`x.rs` is a module of the library (`mod x;` in lib.rs) and of the binary (`mod x;` in main.rs): the attribute at its first byte is one
    attribute to the eye and one per crate to rustc, and `cfg(test)` is on in the binary's test build while the library that build links has it
    off, where `cargo test --bin app` runs `Tr::go` (the compiled probe method_cfg_crates). The call's crate and the declaration's are not one
    crate here (the binary's call is to the library's type, whose impl is the library's copy of the file), so the key has the crate in it."""
    shared = twin_outcomes(make_repo, "mod x;\npub struct S;\nfn main() {}\n")
    assert [k for k, _ in shared] == ["refused", "refused"], shared
    assert all("several modules" in why for _, why in shared), shared


def test_the_compiled_probe_of_a_file_in_two_crates_refuses_both_calls_of_the_shared_file():
    """The compiled probe method_cfg_crates (both builds run: the library's test finds `S::m`, the binary's finds `Tr::m`): neither call of x.rs is an
    edge. The method call is checked here and the path call by its marker, because a shared file's reason has no `a method call:` prefix."""
    probe = probe_of("method_cfg_crates")
    got = {written: result for file, _, _, result, _, _, written, _ in probe.index.resolved_sites() if file == "src/x.rs"}
    assert set(got) == {"s.m", "twin::S::make"}, got
    assert all(r == rs.unresolved(rs.SHARED_FILE) for r in got.values()), got


def test_the_same_file_in_one_crate_only_is_an_edge(make_repo):
    """The control of the test above: with no `mod x;` in main.rs the file is the library's alone, and the call and the impl share its attribute."""
    assert [k for k, _ in twin_outcomes(make_repo, "fn main() {}\n")] == ["edge", "edge"]


def test_the_control_without_the_attribute_is_an_edge(make_repo):
    assert outcomes(make_repo, lib(TR + "pub struct S;\nimpl Tr for S {}\n" + IMPL + "\n" + CALLS)) == ("edge", "edge")


# ---- which attributes a call may share


SHAREABLE = ["#[cfg(a)]", "#[cfg(test)]", "#[cfg_attr(a, cfg(b))]", "#[cfg_attr(a, cfg_attr(b, cfg(c)))]", "#[cfg_attr(a, allow(x), cfg(b))]", "#[cfg_attr(a, cfg(b), inline)]"]
NOT_SHAREABLE = [
    "#[test]", "#[bench]", "#[tokio::main]", "#[my_macro]", "#[no_implicit_prelude]", '#[path = "x.rs"]', '#[cfg_attr(a, path = "x.rs")]', "#[cfg_attr(a, test)]",
    "#[cfg_attr(a, my_macro)]", "#[cfg_attr(a, cfg(b), my_macro)]", "#[cfg_attr(a, no_implicit_prelude)]", "#[cfg_attr]", "#[feature(x)]",
]  # fmt: skip


def parsed_attribute(written):
    src = (written + "\nfn f() {}\n").encode()
    tree = rs.load_parser().parse(src)
    return next(c for c in tree.root_node.children if c.type == "attribute_item"), src


@pytest.mark.parametrize("written", SHAREABLE)
def test_an_attribute_that_only_makes_its_host_exist_or_not_is_shareable(written):
    node, src = parsed_attribute(written)
    assert rs.attribute_conditional(node, src, frozenset()) and rs.attribute_shared(node, src, frozenset()), written


@pytest.mark.parametrize("written", NOT_SHAREABLE)
def test_every_other_attribute_is_not_shareable(written):
    node, src = parsed_attribute(written)
    assert not rs.attribute_shared(node, src, frozenset()), written


def test_an_inert_attribute_is_not_conditional_and_so_is_in_no_key_set():
    node, src = parsed_attribute("#[allow(dead_code)]")
    assert not rs.attribute_conditional(node, src, frozenset())
    assert rs.attribute_keys([node], object(), "f.rs", src, frozenset()) == set()


# ---- the keys agree with the booleans they refine, over every probe crate


def method_defs(probe):
    for defs in probe.index.inherent.values():
        for named in defs.values():
            yield from (d for d in named if d.kind == "method")


@pytest.mark.parametrize("name", sorted(p.name for p in CRATES.iterdir() if p.is_dir() and not p.name.startswith("_")))
def test_an_inherent_method_has_cfg_keys_exactly_when_it_may_not_exist(name):
    """Def.extra["cfg"] (every rule that reads whether a declaration may not exist) and Def.extra["cfg_keys"] (which attributes make it so) are one
    fact: a method with the flag and no key would be read as shared by every call."""
    probe = probe_of(name)
    for d in method_defs(probe):
        assert bool(d.extra["cfg_keys"]) == bool(d.extra.get("cfg")), (name, d.name, d.key)
        assert bool(d.extra["impl"].cfg_keys) == bool(d.extra["impl"].cfg), (name, d.name, d.key)
        if d.extra["impl"].header_cfg:
            assert d.extra["impl"].header_keys, (name, d.name, d.key)


# ---- the probes: the call is what its marker says (method calls are in test_rs_method_probes: //~; path calls here: //@)

PATH_MARKER = re.compile(r"//@\s*(.*)$")
PROBES = ["method_cfg_shared", "method_cfg_shared_macro", "method_cfg_crates", "method_cfg_predicate", "method_cfg_predicate_crates", "method_cfg_predicate_macros"]


def path_markers(probe):
    out = []
    for path in sorted((CRATES / probe).rglob("*.rs")):
        if "target" in path.relative_to(CRATES / probe).parts:
            continue
        for number, line in enumerate(path.read_text().splitlines(), 1):
            found = PATH_MARKER.search(line)
            if found:
                for part in found.group(1).split(";"):
                    name, _, what = part.strip().partition("=")
                    kind, _, text = what.partition(":")
                    out.append((path.relative_to(CRATES / probe).as_posix(), number, name, kind, text))
    return out


PATH_CASES = [(p, *m) for p in PROBES for m in path_markers(p)]


def path_sites(probe, file, line, name):
    return [
        (result, name_end)
        for f, _, _, result, _, ln, written, name_end in probe.index.resolved_sites()
        if f == file and ln == line and "." not in written and written.rsplit("::", 1)[-1] == name
    ]


@pytest.mark.parametrize("probe, file, line, name, kind, text", PATH_CASES, ids=[f"{c[0]}:{c[1]}:{c[2]}" for c in PATH_CASES])
def test_the_path_call_is_what_its_marker_says(probe, file, line, name, kind, text):
    p = probe_of(probe)
    found = path_sites(p, file, line, name)
    assert len(found) == 1, f"{file}:{line} `{name}`: {len(found)} sites"
    result, name_end = found[0]
    if kind == "E":
        assert result[0] == "edge", f"{file}:{line} `{name}` is {result}"
        row, col = p.at(file, name_end)
        answers = p.answers.get((file, row, col), [])
        assert len(answers) == 1 and answers[0]["kind"] == "repo" and len(answers[0]["defs"]) == 1, f"the compiler's answer at {file}:{line}: {answers}"
        assert p.where(result[1]) == tuple(answers[0]["defs"][0])
    else:
        assert kind == "U", f"{file}:{line}: marker kind {kind!r}"
        assert result[0] == "unresolved" and text in result[1], f"{file}:{line} `{name}` is {result}"


def test_the_probes_hold_every_kind_of_case():
    method = [m for p in PROBES for m in markers(p)]
    assert [m[3] for m in method].count("E") >= 20 and [m[3] for m in method].count("U") >= 14, [m[3] for m in method]
    assert [c[4] for c in PATH_CASES].count("E") >= 20 and [c[4] for c in PATH_CASES].count("U") >= 13, [c[4] for c in PATH_CASES]
