"""`rustfmt`, `clippy` and `diagnostic` are tool namespaces: `#[rustfmt::skip]` is inert, unless something of that name is in scope
that makes the path another macro. rustc builds each of these (rustc 1.99, local proc-macro crates): a `mod`, a `use` (as the
name or as an alias), an `extern crate x as name`, a dependency key of that name, and the bare form `use m as diagnostic;
#[diagnostic]`. The tool namespace has the lowest priority and the shadowing is not ambiguous, where every other name on
BUILTIN_ATTRS is. A glob that brings the name in is E0659, a macro that makes the `mod` is E0659, and a bare `#[rustfmt]`
with nothing in scope is an error: so a bare tool name is always a macro in a program that builds.

ripple keys on the simplest rule: when the first segment of such a head is declared, imported, aliased or a dependency key
anywhere in the crate, the attribute is an attribute macro like any other (the item is read as rewritten, a trusted
attribute beside it is not trusted). Every other path whose first segment is on the list (`#[doc::x]`, `#[test::x]`)
resolves only through a module of that name, so it is a macro whenever the program builds.
"""

import pytest

pytest.importorskip("tree_sitter_rust")

from ripple import rs_indexer as rs  # noqa: E402
from ripple.indexer import build_graph  # noqa: E402
from ripple.profile import RUST  # noqa: E402

PKG = '[package]\nname = "app"\nversion = "0.1.0"\nedition = "2021"\n'
OTHER = '[dependencies]\nother = "1"\n'
TOKIO = '[dependencies]\ntokio = { version = "1", features = ["macros", "rt"] }\nother = "1"\n'
ITEM = "fn one() {}\nfn t() {\n    one();\n}\n"
EDGE = {("src/lib:t", "edge", "src/lib:one")}
REWRITTEN = {("src/lib:t", "unresolved", rs.ATTR_MACRO)}


def calls(make_repo, src, deps=OTHER, extra=None):
    g = build_graph(make_repo({"Cargo.toml": PKG + deps, "src/lib.rs": src, **(extra or {})}), RUST)
    out = {(e.caller, "edge", e.callee) for e in g.edges if e.kind == "call"}
    out |= {(u.caller, "unresolved", u.reason) for u in g.unresolved}
    return out


@pytest.mark.parametrize("tool, head", [("rustfmt", "rustfmt::skip"), ("clippy", "clippy::cognitive_complexity"), ("diagnostic", "diagnostic::on_unimplemented")])
def test_a_tool_attribute_with_nothing_of_that_name_is_inert(make_repo, tool, head):
    assert calls(make_repo, f"#[{head}]\n" + ITEM) == EDGE


FORMS = {
    "mod": "mod {tool} {{\n    pub use other::skip;\n}}\n",
    "mod of a file": "mod {tool};\n",
    "use as an alias": "use other as {tool};\n",
    "use of the name": "use other::{tool};\n",
    "use in a list": "use other::{{x, y as {tool}}};\n",
    "use under a cfg": '#[cfg(feature = "x")]\nuse other::x as {tool};\n',
    "extern crate alias": "extern crate other as {tool};\n",
    "mod in a block elsewhere": "fn unrelated() {{\n    mod {tool} {{}}\n}}\n",
    "use in a block elsewhere": "fn unrelated() {{\n    use other as {tool};\n}}\n",
    "mod in another module": "mod m {{\n    mod {tool} {{}}\n}}\n",
    "use in another module": "mod m {{\n    use other as {tool};\n}}\n",
}


@pytest.mark.parametrize("tool, head", [("rustfmt", "rustfmt::skip"), ("clippy", "clippy::cognitive_complexity"), ("diagnostic", "diagnostic::on_unimplemented")])
@pytest.mark.parametrize("form", FORMS)
def test_a_tool_attribute_whose_name_is_declared_anywhere_in_the_crate_is_an_attribute_macro(make_repo, tool, head, form):
    """origin/main read these as inert (the review's formsb, toolshadow): `mod rustfmt {{ pub use fake::skip }}` makes `#[rustfmt::skip]` the fake."""
    extra = {"src/rustfmt.rs": "", "src/clippy.rs": "", "src/diagnostic.rs": ""}
    assert calls(make_repo, FORMS[form].format(tool=tool) + f"#[{head}]\n" + ITEM, extra=extra) == REWRITTEN


def test_a_dependency_key_of_the_tool_name_is_an_attribute_macro(make_repo):
    for tool in ("rustfmt", "clippy", "diagnostic"):
        deps = OTHER + f'{tool} = {{ package = "fake2", version = "1" }}\n'
        assert calls(make_repo, f"#[{tool}::skip]\n" + ITEM, deps) == REWRITTEN


def test_a_dependency_key_is_read_in_every_dependency_table(make_repo):
    deps = OTHER + '[dev-dependencies]\nclippy = { package = "fake2", version = "1" }\n'
    assert calls(make_repo, "#[clippy::skip]\n" + ITEM, deps) == REWRITTEN


def test_a_package_that_is_called_by_the_tool_name_is_in_the_extern_prelude_of_its_binaries(make_repo):
    """A binary or a test of a package with a library has that library in its extern prelude, under its name."""
    extra = {"src/main.rs": "#[rustfmt::skip]\n" + ITEM + "fn main() {}\n"}
    g = build_graph(make_repo({"Cargo.toml": PKG.replace('"app"', '"rustfmt"') + OTHER, "src/lib.rs": "", **extra}), RUST)
    assert not [e for e in g.edges if e.kind == "call"]
    assert {u.reason for u in g.unresolved if u.caller == "src/main:t"} == {rs.ATTR_MACRO}


@pytest.mark.parametrize("tool", ["rustfmt", "clippy", "diagnostic"])
def test_the_bare_form_of_a_tool_name_is_an_attribute_macro(make_repo, tool):
    """`use other::rewrite as diagnostic;` then `#[diagnostic]` builds (formsd/diagnostic_use); with nothing in scope it does not build."""
    assert calls(make_repo, f"use other::rewrite as {tool};\n#[{tool}]\n" + ITEM) == REWRITTEN
    assert calls(make_repo, f"#[{tool}]\n" + ITEM) == REWRITTEN


def test_a_shadowed_tool_beside_a_trusted_attribute_cancels_the_trust(make_repo):
    """The review's toolshadow: `mod rustfmt { pub use fake::rewrite as skip; }` makes `#[rustfmt::skip]` a macro that
    swaps `one()` for `two()`, and `#[tokio::main]` beside it was trusted (an edge to `one`, which did not run)."""
    body = "#[rustfmt::skip]\n#[tokio::main]\nasync fn t() {\n    one();\n}\nfn one() {}\n"
    assert calls(make_repo, body, TOKIO) == EDGE
    shadowed = "mod rustfmt {\n    pub use other::rewrite as skip;\n}\n" + body
    assert calls(make_repo, shadowed, TOKIO) == REWRITTEN


def test_the_bare_shadowed_tool_beside_a_trusted_attribute_cancels_the_trust(make_repo):
    body = "use other::rewrite as diagnostic;\n#[diagnostic]\n#[tokio::main]\nasync fn t() {\n    one();\n}\nfn one() {}\n"
    assert calls(make_repo, body, TOKIO) == REWRITTEN


def test_a_shadowed_tool_leaves_a_name_the_item_declares_unresolved_and_its_scope_open(make_repo):
    src = "use other::rewrite as rustfmt;\n#[rustfmt::skip]\nfn t() {}\nfn run() {\n    t();\n}\n"
    got = calls(make_repo, src)
    assert ("src/lib:run", "unresolved", rs.ATTR_MACRO) in got and not any(k == "edge" for _, k, _ in got), got


def test_a_shadowed_tool_on_a_method_of_an_impl_rewrites_the_method(make_repo):
    src = "mod rustfmt {\n    pub use other::skip;\n}\nstruct S;\nimpl S {\n    #[rustfmt::skip]\n    fn t() {\n        one();\n    }\n}\nfn one() {}\n"
    got = calls(make_repo, src)
    assert not any(k == "edge" for _, k, _ in got), got


def test_a_shadowed_tool_in_a_cfg_attr_is_read_the_same_way(make_repo):
    src = "mod rustfmt {\n    pub use other::skip;\n}\n#[cfg_attr(all(), rustfmt::skip)]\n" + ITEM
    got = calls(make_repo, src)
    assert not any(k == "edge" for _, k, _ in got), got


def test_a_tool_declared_in_another_package_does_not_reach_this_one(make_repo):
    files = {
        "Cargo.toml": '[workspace]\nmembers = ["a", "b"]\n',
        "a/Cargo.toml": PKG.replace('"app"', '"a"'),
        "a/src/lib.rs": "mod rustfmt {}\n#[rustfmt::skip]\n" + ITEM,
        "b/Cargo.toml": PKG.replace('"app"', '"b"'),
        "b/src/lib.rs": "#[rustfmt::skip]\n" + ITEM,
    }
    g = build_graph(make_repo(files), RUST)
    edges = {e.caller for e in g.edges if e.kind == "call"}
    assert edges == {"b/src/lib:t"}, edges


# ---- a path whose first segment is another name on the list

@pytest.mark.parametrize("head", ["doc::foo", "test::foo", "inline::foo", "derive::foo", "cfg::foo", "allow::foo", "must_use::foo"])
@pytest.mark.parametrize("module", [True, False])
def test_a_path_that_starts_at_a_name_of_the_builtin_list_is_an_attribute_macro(make_repo, head, module):
    """`mod doc {{ pub use fake::foo; }}` makes `#[doc::foo]` the macro (rustc builds it, as for `test`, `inline`, `derive`, `cfg`
    and `allow`); with no such module the program does not build, so what is written is a macro in both."""
    first = head.split("::")[0]
    src = (f"mod {first} {{\n    pub use other::foo;\n}}\n" if module else "") + f"#[{head}]\n" + ITEM
    assert calls(make_repo, src) == REWRITTEN
