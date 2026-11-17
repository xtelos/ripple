"""A path call into an inherent function that may not be built, and that the call is not also under, draws no edge.

An inherent function under a `#[cfg]` the call is not also under may not exist where the call does. The call then finds whatever else gives the
name: another impl of the type under the opposite cfg, a repo trait (a blanket impl too), a provided method of a standard or foreign trait the type
implements (`Iterator::count`, `PartialEq::ne`), the reflexive `From` and `TryFrom`, a foreign crate's extension trait. ripple cannot close that
set, so rs_indexer.Index.inherent_hit returns unresolved for such a function, whatever else it knows. (Before, it drew the edge when
`assoc_traits` found no trait impl written for the type, which is wrong for every competitor above, and it ignored an inherent competitor whose type
arguments ripple read as other than the call's: `W<A>` with `type A = u8`, `W<(u8)>`.) Where nothing else gives the name the crate does not
build with the function off, so the edge lost is one no build with the call has. A function the call shares its cfg with, and an unconditional one,
stay edges (group B).

The type arguments of an impl are compared as the type they name (rs_indexer.canonical_type): `(u8)` is `u8`, `& u8` is `&u8`, `[u8; 4usize]`,
`[u8; 0x4]` and `[u8; 04]` are `[u8; 4]`. Before, they were compared as written, and `relation` called two spellings of one type `disjoint`, so a trait
impl for `W<(u8)>` was skipped for a call on `W<u8>` and the call got an edge to it where the inherent function of `W<u8>` is what runs (group E,
with no cfg anywhere).

The probe crates bench/fixtures/rust-probes/crates/path_self_cfg and path_self_cfg_ext are compiled and run under each feature setting (every
`#[test]` asserts which function ran, under `--no-default-features`, `--features a`, `--features b` and both); their oracles are rust-analyzer's, with the
features on. Groups:
  A  the other `f` is the same type written another way, or an impl for every `T`: the feature picks the function.
  B  the call and the function under one predicate, and an unconditional impl: edges.
  C  a plain type whose other `f` is a trait's default, or a second conditional impl.
  D  the other `f` is a trait's that no impl written for the type shows (blanket impl, `Iterator::count`, `PartialEq::ne`, `From`, `TryFrom`, a foreign
     crate's extension trait: path_self_cfg_ext), or a trait impl for the type written another way.
  E  nothing conditional: the inherent function wins over a trait impl for the type written another way, and the call is an edge to it.
"""

import pytest

pytest.importorskip("tree_sitter_rust")

from test_rs_method_guards import graph_of  # noqa: E402
from test_rs_method_probes import probe_of  # noqa: E402
from test_rs_probes import CRATES  # noqa: E402
from test_rs_shared_cfg import path_markers, path_sites  # noqa: E402

from ripple import rs_indexer as rs  # noqa: E402

PROBE = "path_self_cfg"
PROBES = [PROBE, "path_self_cfg_ext"]
CASES = [(probe, *m) for probe in PROBES for m in path_markers(probe)]


@pytest.mark.parametrize("probe, file, line, name, kind, text", CASES, ids=[f"{c[0]}:{c[2]}:{c[3]}" for c in CASES])
def test_the_call_is_what_its_marker_says(probe, file, line, name, kind, text):
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


def test_the_probe_holds_every_group():
    kinds = [c[4] for c in CASES]
    assert kinds.count("E") >= 13 and kinds.count("U") >= 29, (kinds.count("E"), kinds.count("U"))
    lines = (CRATES / PROBE / "src/lib.rs").read_text()
    for group in ("// ---- group A", "// ---- group B", "// ---- group C", "// ---- group D", "// ---- group E"):
        assert group in lines
    for shape in ("<Self>::f()", "impl Go for", "#[cfg_attr(all(), cfg(", "mod m5", "W19<(u8)>", "#[cfg(feature = \"a\")]\n    pub fn f()"):
        assert shape in lines, shape
    for called in ("D1::count(", "D2::from(", "D3::g(", "D4::ne(", "D6::try_from(", "Self::count("):  # the competitors group D names
        assert called in lines, called
    for spelled in ("W20<(u8)>", "X21<[u8; 4usize]>", "W22::<(u8)>", "X24::<[u8; 04]>", "Y25::<& u8>"):
        assert spelled in lines, spelled
    assert "extblanket" in (CRATES / "path_self_cfg_ext" / "Cargo.toml").read_text()


def test_an_associated_constant_through_self_is_no_call_site():
    """`Self::K` is not a call (compiled: it is the inherent constant where `a` is on and the trait's where it is off)."""
    p = probe_of(PROBE)
    source = (CRATES / PROBE / "src/lib.rs").read_text().splitlines()
    line = next(n for n, text in enumerate(source, 1) if text.strip() == "Self::K")
    assert not [s for s in p.index.resolved_sites() if s[5] == line]


def kind_of(graph, caller, callee_text):
    edges = [e.callee for e in graph.edges if e.kind == "call" and e.caller == caller]
    refused = [u.reason for u in graph.unresolved if u.caller == caller and u.text == callee_text]
    assert len(edges) + len(refused) == 1, (edges, refused)
    return ("edge", edges[0]) if edges else ("unresolved", refused[0])


FEATURES = '[package]\nname = "app"\nversion = "0.1.0"\nedition = "2021"\n[features]\na = []\n'


def test_a_call_with_no_type_arguments_does_not_pick_the_impl_for_every_t(make_repo):
    """Compiled and run by hand (a scratch crate with the features `a`, not committed; the shape of probe case A7 without `Self`):
    `cargo run` prints `u8` with `a` off, and with `a` on rustc stops with E0592 and E0034, so the program is the build with `a` off, where the impl
    for every `T` is not there. An edge to that impl was an edge to a function the program that compiles does not contain."""
    source = """
    pub struct W<T>(pub T);
    impl W<u8> { pub fn f() -> &'static str { "u8" } }
    #[cfg(feature = "a")]
    impl<T> W<T> { pub fn f() -> &'static str { "generic" } }
    pub fn run() -> &'static str { W::f() }
    """
    g = graph_of(make_repo, {"src/lib.rs": source}, FEATURES)
    kind, reason = kind_of(g, "src/lib:run", "W::f")
    assert kind == "unresolved" and "declared under a `#[cfg]" in reason, reason


def test_a_conditional_function_nothing_else_gives_is_unresolved_too(make_repo):
    """Where the function is off the crate does not build (the call names nothing), so the edge refused here is one no build has: the cost of
    one rule instead of a list of what else could give the name. Measured on ripgrep, clap, serde, mini-redis, fd and bat: no site changes."""
    source = """
    pub struct S;
    #[cfg(feature = "a")]
    impl S { pub fn f() -> &'static str { "a" } }
    impl S { pub fn go() -> &'static str { Self::f() } }
    """
    g = graph_of(make_repo, {"src/lib.rs": source}, FEATURES)
    kind, reason = kind_of(g, "src/lib:S::go", "Self::f")
    assert kind == "unresolved" and "declared under a `#[cfg]" in reason, reason


def test_an_unconditional_function_stays_the_target_beside_another_impl_of_the_name(make_repo):
    """Nothing is conditional: `impl W<u8>` and `impl W<u16>` are both built, and the call writes which (`Self` is `W<u8>`)."""
    source = """
    pub struct W<T>(pub T);
    impl W<u8> { pub fn f() -> &'static str { "u8" } pub fn go() -> &'static str { Self::f() } }
    impl W<u16> { pub fn f() -> &'static str { "u16" } }
    """
    g = graph_of(make_repo, {"src/lib.rs": source}, FEATURES)
    assert kind_of(g, "src/lib:W::go", "Self::f") == ("edge", "src/lib:W::f")


def test_a_shared_conditional_function_stays_the_target_beside_another_impl_of_the_name(make_repo):
    """The call is under the predicate of the function, so the function is built wherever the call is: the other impl, for `W<A>` (`type A = u8`)
    under the opposite cfg, is not built there. An edge, as before (probe path_self_cfg, B1)."""
    source = """
    type A = u8;
    pub struct W<T>(pub T);
    #[cfg(feature = "a")]
    impl W<u8> { pub fn f() -> &'static str { "u8" } pub fn go() -> &'static str { Self::f() } }
    #[cfg(not(feature = "a"))]
    impl W<A> { pub fn f() -> &'static str { "alias" } }
    """
    g = graph_of(make_repo, {"src/lib.rs": source}, FEATURES)
    assert kind_of(g, "src/lib:W::go", "Self::f") == ("edge", "src/lib:W::f")


# ---- the type arguments of an impl are compared as the type they name

SPELLINGS = [
    ("u8", "u8"),
    ("(u8)", "u8"),
    ("((u8))", "u8"),
    ("(u8,)", "(u8,)"),
    ("()", "()"),
    ("(u8, u16)", "(u8, u16)"),
    ("(  u8 ,u16 )", "(u8, u16)"),
    ("&u8", "&u8"),
    ("& u8", "&u8"),
    ("&  mut u8", "&mut u8"),
    ("[u8; 4]", "[u8; 4]"),
    ("[u8; 4usize]", "[u8; 4]"),
    ("[u8; 0x4]", "[u8; 4]"),
    ("[u8; 0b100]", "[u8; 4]"),
    ("[u8; 0o4]", "[u8; 4]"),
    ("[u8; 04]", "[u8; 4]"),
    ("[u8; 4_]", "[u8; 4]"),
    ("[u8; 1_0]", "[u8; 10]"),
    ("[(u8); 4]", "[u8; 4]"),
    ("[[u8; 2usize]; 0x3]", "[[u8; 2]; 3]"),
]


@pytest.mark.parametrize("written, canonical", SPELLINGS)
def test_a_concrete_type_argument_is_compared_as_the_type_it_names(written, canonical):
    root = rs.load_parser().parse(f"type T = W<{written}>;".encode()).root_node
    args = root.named_children[0].child_by_field_name("type").child_by_field_name("type_arguments")
    assert rs.canonical_type(args.named_children[0], f"type T = W<{written}>;".encode()) == canonical


DIFFERENT = ["u8", "u16", "(u8,)", "()", "(u8, u16)", "(u16, u8)", "&u8", "&mut u8", "[u8; 4]", "[u8; 5]", "[u16; 4]", "[[u8; 2]; 3]", "[[u8; 3]; 2]"]


def test_two_different_types_never_share_a_canonical_text():
    """`disjoint` is said of texts that differ, so two types must not collapse to one text (one type read as two is a lost edge, two read as one is a wrong one)."""
    texts = []
    for written in DIFFERENT:
        src = f"type T = W<{written}>;".encode()
        root = rs.load_parser().parse(src).root_node
        args = root.named_children[0].child_by_field_name("type").child_by_field_name("type_arguments")
        texts.append(rs.canonical_type(args.named_children[0], src))
    assert None not in texts and len(set(texts)) == len(DIFFERENT), texts


@pytest.mark.parametrize("digits", ["9" * 5000, "0x" + "f" * 5000])
def test_an_array_length_no_usize_could_hold_is_not_concrete_and_does_not_stop_the_run(digits):
    # int() raises ValueError for a decimal of more than 4300 digits; the impl may be cfg'd off in a crate that builds
    src = f"type T = W<[u8; {digits}]>;".encode()
    root = rs.load_parser().parse(src).root_node
    args = root.named_children[0].child_by_field_name("type").child_by_field_name("type_arguments")
    assert rs.canonical_type(args.named_children[0], src) is None


@pytest.mark.parametrize("written", ["Foo", "Vec<u8>", "[u8; N]", "[u8; 2 + 2]", "[u8; { 4 }]", "&'static u8", "*const u8", "u8::MAX"])
def test_anything_else_is_not_concrete(written):
    src = f"type T = W<{written}>;".encode()
    root = rs.load_parser().parse(src).root_node
    arg = rs.written_args_of(root.named_children[0].child_by_field_name("type"), src)[0]
    assert not arg.shape


def test_the_trait_impl_for_a_respelled_type_does_not_take_the_call_from_the_inherent_function(make_repo):
    """Compiled and run (probe path_self_cfg, group E; a scratch crate for the first two): the inherent function of `W<u8>` is what `W::<(u8)>::f()`
    and `X::<[u8; 4usize]>::f()` run, and the trait impl for `W<(u8)>` is the one for the same type. ripple read the two as other types, found no
    inherent function for the call, and drew an edge to the trait impl."""
    source = """
    pub trait Tr { fn f() -> &'static str; }
    pub struct W<T>(pub T);
    impl W<u8> { pub fn f() -> &'static str { "inherent" } }
    impl Tr for W<(u8)> { fn f() -> &'static str { "trait" } }
    pub fn e1() -> &'static str { W::<(u8)>::f() }
    pub struct X<T>(pub T);
    impl X<[u8; 4]> { pub fn f() -> &'static str { "inherent" } }
    impl Tr for X<[u8; 4usize]> { fn f() -> &'static str { "trait" } }
    pub fn e2() -> &'static str { X::<[u8; 4usize]>::f() }
    """
    g = graph_of(make_repo, {"src/lib.rs": source}, FEATURES)
    assert kind_of(g, "src/lib:e1", "W::<(u8)>::f") == ("edge", "src/lib:W::f")
    assert kind_of(g, "src/lib:e2", "X::<[u8; 4usize]>::f") == ("edge", "src/lib:X::f")
