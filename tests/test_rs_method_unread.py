"""Method calls and the source ripple did not read, and the per-scope rules that hold when a crate is read whole.

A method call is an edge when the receiver's name and its type's name mean what the code ripple read says they mean. An impl nobody
read cannot change which function the call finds (rustc rejects a second inherent method of the name, E0592, and takes the inherent one
first at the probe step an edge is for), so a macro that may have made one is no reason to refuse. Source ripple did not read can change
the meaning of a name: a file or a module of the crate (a textual `macro_rules!` of an unread `#[macro_use]` module redefines `println!`, so
`println!(s);` rebinds `s`: probe method_unread_macro_use), so every way source enters a crate unread is a refusal, and so is a macro
invoked where an item may stand that may expand to `mod x;` or `include!(..)`: compiled probes (the README's Rust section) show an
expansion can do either at the module level, neither in an impl body, a block's statements (without a `#[path]`) or an expression.

What each test says is by a way in, one at a time, with the control (an edge) beside it, so a guard that is removed turns its test red.
tests/test_rs_method_probes.py holds the compiler's answers, one probe crate per way (method_unread_*, method_rebind_*).
"""

import pytest

pytest.importorskip("tree_sitter_rust")

from test_rs_method_guards import BASE, CARGO, graph_of, refused, result_of  # noqa: E402
from test_rs_method_probes import NAMES  # noqa: E402
from test_rs_probes import Probe  # noqa: E402

from ripple import rs_indexer as rs  # noqa: E402

GEN = {"gen/items.rs": "pub fn made() {}\n"}
EDGE = ("edge", "src/lib:S::go")


@pytest.fixture
def crate_wide_off(monkeypatch):
    """The refusal for source ripple did not read, off: what is left is the rules of each scope."""
    monkeypatch.setattr(rs.Index, "method_taint", lambda self, call, owner: None)


def wrapped(macros: str) -> str:
    """A crate whose module `other` holds `macros`, so the receiver's module has no macro of its own (a macro in the receiver's module may
    have made a const named like the receiver: M_PATTERN_ITEM, a rule of the scope)."""
    return "pub mod other {\n" + macros + "\n}\n" + BASE


# ---- every way source enters a crate unread is a refusal (the class), and a clean macro is not one


UNREAD = {
    "include": ('include!("../gen/items.rs");\n', GEN),
    "include_qualified": ('std::include!("../gen/items.rs");\n', GEN),
    "include_generated_by_a_build_script": ('include!(concat!(env!("OUT_DIR"), "/generated.rs"));\n', {}),
    "include_in_a_macro_body": ('macro_rules! inc { () => { include!("../gen/items.rs"); }; }\ninc!();\n', GEN),
    "mod_in_a_macro_body": ('macro_rules! decl { ($n:ident) => { #[path = "../gen/items.rs"] mod $n; }; }\ndecl!(extra);\n', GEN),
    "mod_in_the_invocation": ('macro_rules! pass { ($($t:tt)*) => { $($t)* }; }\npass! { #[path = "../gen/items.rs"] mod extra; }\n', GEN),
    "macro_made_by_a_macro": ('macro_rules! gen { () => { macro_rules! inner { () => { mod x; }; } }; }\ngen!();\n', {}),
    "nested_macro_that_includes": (
        'macro_rules! inner { () => { include!("../gen/items.rs"); }; }\nmacro_rules! outer { () => { inner!(); }; }\nouter!();\n',
        GEN,
    ),
    "name_given_in_the_body": ("macro_rules! by_name { ($m:ident) => { $m!(); }; }\nby_name!(whatever);\n", {}),
    # the proof is for two forms only (Index.expands_no_source): a bare name a `macro_rules!` of the module reaches textually, or a path from crate/self/super
    "a_bare_name_by_a_use_of_a_clean_macro_of_another_module": ("pub mod inner { macro_rules! m { () => {}; } pub(crate) use m; }\nuse self::inner::m;\nm!();\n", {}),
    "a_nested_macro_by_a_path_through_crate": ("macro_rules! inner { () => {}; }\nmacro_rules! outer { () => { crate::inner!(); }; }\nouter!();\n", {}),
    "a_nested_macro_by_a_path_through_dollar_crate": ("macro_rules! inner { () => {}; }\nmacro_rules! outer { () => { $crate::inner!(); }; }\nouter!();\n", {}),
    "a_nested_standard_name_given_by_a_use": ('use std::include as concat;\nmacro_rules! gen { () => { concat!("../gen/items.rs"); }; }\ngen!();\n', GEN),
    "a_nested_standard_name_given_by_an_alias_of_a_repo_macro": (
        'macro_rules! declare { () => { #[path = "../gen/items.rs"] mod extra; }; }\npub(crate) use declare as stringify;\nmacro_rules! gen { () => { stringify!(); }; }\ngen!();\n',
        GEN,
    ),
    "a_nested_repo_macro_defined_in_another_module": ("pub mod far { macro_rules! helper { () => {}; } }\nmacro_rules! gen { () => { helper!(); }; }\ngen!();\n", {}),
    "a_textual_definition_that_may_not_exist": ('#[cfg(any())]\nmacro_rules! gen { () => {}; }\ngen!();\n', {}),
    "macro_not_defined_in_the_repo": ("unknown_macro!();\n", {}),
    "macro_of_another_crate_by_path": ("dep::their_macro!();\n", {}),
    "macro_brought_by_a_use": ("use dep::their_macro;\ntheir_macro!();\n", {}),
    "macro_use_extern_crate": ("#[macro_use]\nextern crate dep;\ntheir_macro!();\n", {}),
    "two_macros_of_a_name_one_of_them_dirty": (
        'macro_rules! m { () => {}; }\npub mod a { macro_rules! m { () => { include!("../gen/items.rs"); }; } }\nm!();\n',
        GEN,
    ),
    "a_use_of_the_name_that_is_not_the_crates_own": ("macro_rules! m { () => {}; }\nuse dep::m;\nm!();\n", {}),
    "procedural_macro_of_the_repo": ("#[proc_macro]\npub fn m(t: TokenStream) -> TokenStream { t }\nm!();\n", {}),
    "attribute_macro": ("#[some::attr]\npub fn f() {}\n", {}),
    "derive_that_is_not_a_standard_one": ("#[derive(Mystery)]\npub struct T;\n", {}),
    "attribute_macro_on_an_impl": ("pub struct T;\n#[some::attr]\nimpl T {}\n", {}),
}


@pytest.mark.parametrize("way", sorted(UNREAD))
def test_a_way_source_may_enter_the_crate_unread_is_not_an_edge(make_repo, way):
    macros, files = UNREAD[way]
    g = graph_of(make_repo, {"src/lib.rs": wrapped(macros), **files})
    kind, why = result_of(g)
    assert kind == "unresolved" and "did not read" in why, why


def test_an_include_in_the_crate_that_holds_the_type_is_enough_for_a_call_from_another_crate(make_repo):
    files = {
        "Cargo.toml": '[workspace]\nmembers = ["kit", "app"]\nresolver = "2"\n',
        "kit/Cargo.toml": CARGO.replace("app", "kit"),
        "kit/src/lib.rs": 'include!("../gen/items.rs");\npub struct S;\nimpl S { pub fn go(&self) -> u8 { 1 } }\n',
        "kit/gen/items.rs": "pub fn made() {}\n",
        "app/Cargo.toml": CARGO + '[dependencies]\nkit = { path = "../kit" }\n',
        "app/src/lib.rs": "pub fn run(s: &kit::S) -> u8 { s.go() }\n",
    }
    g = graph_of(make_repo, files)
    (u,) = [u for u in g.unresolved if u.text == "s.go"]
    assert "did not read" in u.reason


CLEAN = {
    "a_macro_that_makes_an_impl": "macro_rules! make { ($t:ident) => { impl $t { pub fn made(&self) {} } }; }\nmake!(Local);\npub struct Local;\n",
    "a_macro_that_makes_items": "macro_rules! items { () => { pub fn made() {} pub struct Made; }; }\nitems!();\n",
    "the_use_idiom_that_exports_a_macro": "macro_rules! m { () => { pub fn made() {} }; }\npub(crate) use m;\nm!();\n",
    "a_macro_of_clean_macros": "macro_rules! a { () => { pub fn a() {} }; }\nmacro_rules! b { () => { a!(); }; }\nb!();\n",
    "macros_that_call_each_other": "macro_rules! a { (x) => { b!(y); }; (y) => {}; }\nmacro_rules! b { (x) => { a!(y); }; (y) => {}; }\na!(x);\n",
    "standard_macros_and_a_string_with_a_bang": 'macro_rules! m { () => { pub fn made() -> String { format!("{}!", concat!("a", "b")) } }; }\nm!();\n',
    "a_path_that_is_the_crates_own": "macro_rules! m { () => { pub fn made() {} }; }\ncrate::m!();\n",
    "a_string_in_the_invocation_that_says_include_or_mod": "macro_rules! m { ($s:expr) => { pub fn made() -> &'static str { $s } }; }\nm!(\"--include mod\");\n",
    "thread_local_and_a_standard_macro": "thread_local! { static X: u8 = 1; }\n",
    "a_standard_macro_item": 'const _: &str = concat!("a", "b");\n',
}


@pytest.mark.parametrize("shape", sorted(CLEAN))
def test_a_macro_that_provably_brings_no_file_costs_nothing(make_repo, shape):
    g = graph_of(make_repo, {"src/lib.rs": wrapped(CLEAN[shape])})
    assert result_of(g) == EDGE


def test_where_an_expansion_cannot_bring_a_file_an_unknown_macro_costs_nothing(make_repo):
    """In an impl body (rustc: "non-impl item macro in impl item position"), in the statements of a block (an `include!` makes no
    statement, a `mod` there needs a `#[path]`: a block module, which the crate refuses on its own), and in an expression."""
    for source in (
        "pub struct T;\nimpl T { unknown_macro!(); }\n",
        "pub fn other() { unknown_macro!(); }\n",
        "pub fn other() -> u8 { let x = unknown_macro!(); 1 }\n",
        "pub trait Tr { unknown_macro!(); }\n",
    ):
        assert result_of(graph_of(make_repo, {"src/lib.rs": source + BASE})) == EDGE, source


def test_a_cfg_attr_path_may_name_a_file_ripple_does_not_read_even_where_the_default_file_exists(make_repo):
    source = '#[cfg_attr(all(), path = "../shared/real.rs")]\nmod macros;\n' + BASE
    files = {"src/macros.rs": "pub fn unused() {}\n", "shared/real.rs": "pub fn built() {}\n"}
    why = refused(make_repo, source, "did not read", files)
    assert "cfg_attr" in why
    assert "path" in why


def test_a_cfg_attr_path_on_an_inline_module_is_the_same(make_repo):
    source = '#[cfg_attr(all(), path = "elsewhere")]\nmod inline { pub fn f() {} }\n' + BASE
    assert "cfg_attr" in refused(make_repo, source, "did not read")


def test_a_path_attribute_that_names_a_file_is_followed_and_costs_nothing(make_repo):
    source = '#[path = "../shared/real.rs"]\nmod macros;\n' + BASE
    assert result_of(graph_of(make_repo, {"src/lib.rs": source, "shared/real.rs": "pub fn built() {}\n"})) == EDGE


def test_modules_nested_deeper_than_ripple_follows_are_source_it_did_not_read(make_repo):
    source = "mod m {" * 62 + "mod leaf {}" + "}" * 62 + "\n" + BASE
    assert "nested" in refused(make_repo, source, "did not read")


# ---- the receiver name and the type name hold with the crate-wide refusal off


def test_a_macro_in_a_pattern_may_bind_the_receiver(make_repo):
    shapes = {
        "let": "let bind!() = other; s.go()",
        "match": "match other { bind!() => s.go() }",
        "if_let": "if let bind!() = other { s.go() } else { 0 }",
        "while_let": "while let bind!() = other { return s.go(); } 0",
        "for": "for bind!() in other { return s.go(); } 0",
        "closure": "let c = |bind!(): u8| s.go(); c(1)",
        "nested": "let (bind!(), _) = (other, 1); s.go()",
        "parameter_of_a_closure_with_a_type": "let c = |bind!(): &S| s.go(); c(s)",
        "an_arm_with_a_guard_around_it": "match other { Some(bind!()) if true => s.go(), _ => 0 }",
        "a_standard_macro_argument": 'println!("{}", { let bind!() = other; s.go() }); 0',
    }
    for name, body in shapes.items():
        source = f"pub struct S;\nimpl S {{ pub fn go(&self) -> u8 {{ 1 }} }}\npub fn run(s: &S, other: u8) -> u8 {{ {body} }}\n"
        for off in (False, True):
            g = graph_of(make_repo, {"src/lib.rs": source})
            assert not [e for e in g.edges if e.kind == "call" and e.caller == "src/lib:run"], name
            assert rs.M_PATTERN_MACRO in {u.reason for u in g.unresolved if u.text == "s.go"}, (name, off)


def test_a_macro_in_a_pattern_costs_the_receiver_of_that_function_only(make_repo):
    source = BASE + "pub fn other_fn(t: u8) { let bind!() = t; }\npub fn guard(s: &S, t: Option<u8>) -> u8 { match t { Some(_) if matches!(t, Some(1)) => s.go(), _ => 0 } }\n"
    g = graph_of(make_repo, {"src/lib.rs": source})
    assert ("src/lib:run", "src/lib:S::go") in [(e.caller, e.callee) for e in g.edges if e.kind == "call"]
    assert ("src/lib:guard", "src/lib:S::go") in [(e.caller, e.callee) for e in g.edges if e.kind == "call"]  # a guard is an expression, not a pattern


def test_a_user_macro_in_expression_position_binds_nothing(make_repo):
    source = BASE.replace("{ s.go() }", "{ let x = unknown_macro!(s); s.go() }")
    assert result_of(graph_of(make_repo, {"src/lib.rs": source})) == EDGE


@pytest.mark.parametrize(
    "shape",
    [
        "macro_rules! println { ($i:ident) => { let $i = &Other; }; }\n",  # a macro_rules! of the name, in the crate
        "use dep::println;\n",  # a use that gives the name
        "#[macro_use]\nextern crate dep;\n",  # a `#[macro_use] extern crate`
        "pub mod m { macro_rules! println { () => {}; } }\n",  # in another module: the name is distrusted whole
    ],
)
def test_a_standard_macro_name_is_trusted_only_where_nothing_read_can_redefine_it(make_repo, crate_wide_off, shape):
    source = shape + BASE.replace("{ s.go() }", "{ println!(s); s.go() }")
    g = graph_of(make_repo, {"src/lib.rs": source})
    assert [u.reason for u in g.unresolved if u.text == "s.go"] == [rs.M_SHADOWED]


def test_a_use_in_a_block_that_gives_a_standard_macro_name_is_not_the_standard_macro(make_repo, crate_wide_off):
    source = BASE.replace("{ s.go() }", "{ use dep::println; println!(s); s.go() }")
    g = graph_of(make_repo, {"src/lib.rs": source})
    assert [u.reason for u in g.unresolved if u.text == "s.go"] == [rs.M_SHADOWED]


def test_a_macro_of_the_crate_is_not_provably_the_one_an_invocation_means_where_another_may_come_first(make_repo):
    """`#[macro_use] extern crate` and a glob from outside the repo may bring a macro of the name, and a procedural macro of the repo may
    have it too: the body ripple read is then not certainly what expands."""
    clean = "m!();\nmacro_rules! m { () => { pub fn made() {} }; }\n"  # written after the invocation: the textual scope does not reach it
    for head in ("#[macro_use]\nextern crate dep;\n", "use dep::*;\n", "#[proc_macro]\npub fn m(t: TokenStream) -> TokenStream { t }\n"):
        refused(make_repo, head + wrapped(clean), "did not read")


def test_a_macro_of_the_crate_written_before_its_invocation_in_the_same_scope_is_the_one_it_names(make_repo):
    """The textual scope comes before a glob and the macro_use prelude, so an outside glob or a `#[macro_use] extern crate` is no reason."""
    clean = "macro_rules! m { () => { pub fn made() {} }; }\nm!();\n"
    for root, inner in (("#[macro_use]\nextern crate dep;\n", ""), ("", "use dep::*;\n")):  # (the glob in the receiver's own module may bring a const `s`: M_PATTERN_ITEM)
        assert result_of(graph_of(make_repo, {"src/lib.rs": root + wrapped(inner + clean)})) == EDGE, (root, inner)


def test_a_file_the_parser_failed_on_is_a_crate_ripple_cannot_tell(make_repo, monkeypatch):
    real = rs.blank_comments

    def failing(parser, src, tree):
        if b"BOOM" in src:
            raise ValueError("unreadable")
        return real(parser, src, tree)

    monkeypatch.setattr(rs, "blank_comments", failing)
    why = refused(make_repo, "mod broken;\n" + BASE, "could not be read at all", {"src/broken.rs": "pub fn boom() {} // BOOM\n"})
    assert "src/broken.rs" in why


def test_a_macro_use_module_of_a_block_holds_macros_ripple_did_not_read(make_repo, crate_wide_off):
    source = BASE.replace("{ s.go() }", '{ #[macro_use] #[path = "x.rs"] mod m; println!(s); s.go() }')
    g = graph_of(make_repo, {"src/lib.rs": source, "src/x.rs": "macro_rules! println { ($i:ident) => {}; }\n"})
    assert [u.reason for u in g.unresolved if u.text == "s.go"] == [rs.M_SHADOWED]


def test_the_standard_macro_is_trusted_where_nothing_redefines_it(make_repo):
    source = BASE.replace("{ s.go() }", '{ println!("{}", 1); s.go() }')
    assert result_of(graph_of(make_repo, {"src/lib.rs": source})) == EDGE


@pytest.mark.parametrize("name", [n for n in NAMES if not n.startswith("method_unread_")])
def test_the_rules_of_each_scope_hold_with_the_crate_wide_refusal_off(crate_wide_off, name):
    """Every probe crate whose point is not an unread file, run with Index.method_taint gone: no edge is one the compiler disproves."""
    wrong, _ = Probe(name).judge()
    assert not wrong, wrong


@pytest.mark.parametrize("name", ["method_unread_macro_use", "method_unread_cfg_attr_path"])
def test_without_the_refusal_for_unread_source_a_wrong_edge_is_drawn(crate_wide_off, name):
    """The probes where a wrong edge is what the refusal prevents: the compiler calls Other::m, and with the refusal off ripple draws S::m."""
    wrong, _ = Probe(name).judge()
    assert len(wrong) == 1 and "src/lib.rs:2" in wrong[0], wrong
