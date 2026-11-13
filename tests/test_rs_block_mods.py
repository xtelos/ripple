"""A `mod` written inside a block declares a name there, which shadows the item of that name outside the block: the block's
own `x` is what `x::f()` reaches (built with cargo: the call gives the block's `f`, not the module's). ripple does not read
what is in it, so a path that starts at that name, from anywhere in the block, is unresolved, and says why. That holds for
the path of an attribute too: `#[tokio::main]` under a block `mod tokio` is not tokio's.
"""

import pytest

pytest.importorskip("tree_sitter_rust")

from ripple import rs_indexer as rs  # noqa: E402
from ripple.indexer import build_graph  # noqa: E402
from ripple.profile import RUST  # noqa: E402

PKG = '[package]\nname = "app"\nversion = "0.1.0"\nedition = "2021"\n'
TOKIO, SERIAL_TEST, OTHER = '[dependencies]\ntokio = "1"\n', '[dev-dependencies]\nserial_test = "2"\n', 'other = "1"\n'


def verdicts(make_repo, src, deps=""):
    g = build_graph(make_repo({"Cargo.toml": PKG + deps, "src/lib.rs": src}), RUST)
    return {(e.caller, e.callee) for e in g.edges if e.kind == "call"}, {(u.caller, u.text): u.reason for u in g.unresolved}


def test_a_path_through_a_module_of_a_block_is_not_the_module_of_that_name_outside(make_repo):
    """origin/main drew `g -> x::f` here: the module's `f`, which the block's `x` shadows."""
    src = "mod x {\n    pub fn f() {}\n}\nfn g() {\n    mod x {\n        pub fn f() {}\n    }\n    x::f();\n}\n"
    edges, unresolved = verdicts(make_repo, src)
    assert edges == set()
    assert unresolved == {("src/lib:g", "x::f"): rs.BLOCK_MOD}


def test_the_same_call_outside_the_block_still_reaches_the_module(make_repo):
    src = "mod x {\n    pub fn f() {}\n}\nfn g() {\n    mod x {\n        pub fn f() {}\n    }\n}\nfn h() {\n    x::f();\n}\n"
    edges, unresolved = verdicts(make_repo, src)
    assert edges == {("src/lib:h", "src/lib:x::f")}
    assert unresolved == {}


def test_a_block_with_a_module_and_nothing_else_has_its_scope(make_repo):
    src = "mod x {\n    pub fn f() {}\n}\nfn g() {\n    mod x {}\n    x::f();\n}\n"
    edges, unresolved = verdicts(make_repo, src)
    assert edges == set() and unresolved == {("src/lib:g", "x::f"): rs.BLOCK_MOD}


def test_the_module_of_a_block_is_seen_from_a_function_nested_in_the_block_and_from_a_use_there(make_repo):
    src = (
        "mod x {\n    pub fn f() {}\n}\nfn g() {\n    mod x {}\n    use x::f;\n    fn nested() {\n        x::f();\n    }\n    f();\n}\n"
    )
    edges, unresolved = verdicts(make_repo, src)
    assert edges == set()
    assert unresolved == {("src/lib:g::nested", "x::f"): rs.BLOCK_MOD, ("src/lib:g", "f"): rs.BLOCK_MOD}


def test_a_module_of_a_block_is_no_name_of_another_namespace(make_repo):
    """`mod x` is a type-namespace name: a function called x is still the function."""
    src = "fn x() {}\nfn g() {\n    mod x {}\n    x();\n}\n"
    edges, unresolved = verdicts(make_repo, src)
    assert edges == {("src/lib:g", "src/lib:x")} and unresolved == {}


def test_a_module_of_a_block_does_not_reach_a_sibling_function_of_the_module(make_repo):
    src = "mod x {\n    pub fn f() {}\n}\nfn g() {\n    mod y {}\n    x::f();\n}\n"
    edges, unresolved = verdicts(make_repo, src)
    assert edges == {("src/lib:g", "src/lib:x::f")} and unresolved == {}


def test_a_self_or_crate_path_skips_the_block_module(make_repo):
    src = "mod x {\n    pub fn f() {}\n}\nfn g() {\n    mod x {}\n    self::x::f();\n    crate::x::f();\n}\n"
    edges, unresolved = verdicts(make_repo, src)
    assert edges == {("src/lib:g", "src/lib:x::f")} and unresolved == {}


# ---- the path of an attribute

MAIN = HELPER = "pub fn helper() {}\n"


def test_a_tokio_main_under_a_block_module_called_tokio_is_not_tokios(make_repo):
    src = (
        "pub fn run() {\n    mod tokio {\n        pub use other::main;\n    }\n    #[tokio::main]\n    async fn inner() {\n        helper();\n    }\n    inner();\n}\n"
        + MAIN
    )
    edges, unresolved = verdicts(make_repo, src, TOKIO + OTHER)
    assert edges == set()
    assert unresolved[("src/lib:run::inner", "helper")] == rs.ATTR_MACRO


def test_a_block_module_called_tokio_in_a_trusted_function_refuses_the_site_beside_it(make_repo):
    src = (
        "#[tokio::main]\nasync fn main() {\n    helper();\n    mod tokio {\n        pub use other::main;\n    }\n    #[tokio::main]\n    async fn inner() {\n        helper();\n    }\n}\n"
        + MAIN
    )
    edges, unresolved = verdicts(make_repo, src, TOKIO + OTHER)
    assert unresolved[("src/lib:main::inner", "helper")] == rs.ATTR_MACRO
    assert ("src/lib:main::inner", "src/lib:helper") not in edges


def test_a_block_module_called_serial_test_makes_the_path_form_of_serial_not_serial_tests(make_repo):
    src = (
        "pub fn outer() {\n    mod serial_test {\n        pub use other::serial;\n    }\n    #[test]\n    #[serial_test::serial]\n    fn t() {\n        helper();\n    }\n    t();\n}\n"
        + HELPER
    )
    edges, unresolved = verdicts(make_repo, src, SERIAL_TEST + OTHER)
    assert edges == set()
    assert unresolved[("src/lib:outer::t", "helper")] == rs.ATTR_MACRO


def test_a_block_module_called_serial_test_beside_the_use_of_serial_in_the_block_refuses_the_site(make_repo):
    src = (
        "pub fn outer() {\n    mod serial_test {\n        pub use other::serial;\n    }\n    use serial_test::serial;\n    #[test]\n    #[serial]\n    fn t() {\n        helper();\n    }\n    t();\n}\n"
        + HELPER
    )
    edges, unresolved = verdicts(make_repo, src, SERIAL_TEST + OTHER)
    assert edges == set()
    assert unresolved[("src/lib:outer::t", "helper")] == rs.ATTR_MACRO


def test_a_block_module_called_serial_test_does_not_change_a_use_of_the_module_above_it(make_repo):
    """The `use` names `serial_test` from the module's scope, where the block's module is not: the site stays trusted."""
    src = (
        "use serial_test::serial;\npub fn outer() {\n    mod serial_test {\n        pub use other::serial;\n    }\n    #[test]\n    #[serial]\n    fn t() {\n        helper();\n    }\n    t();\n}\n"
        + HELPER
    )
    edges, unresolved = verdicts(make_repo, src, SERIAL_TEST + OTHER)
    assert ("src/lib:outer::t", "src/lib:helper") in edges
    assert unresolved == {("src/lib:outer", "t"): rs.ATTR_MACRO}  # the name of a trusted function stays unresolved


# ---- what a block module holds acts outside it, and what it is called is no name of the module above

OTHER_DEP = '[dependencies]\nextcrate = "1"\n'
OUTSIDE_GLOB = (
    "use extcrate::*;\nmod other {\n    pub fn helper() {}\n}\nuse other::*;\n#[derive(extcrate::D)]\n#[thing]\nstruct S;\n"
    "fn f() {\n    helper();\n}\n"
)


def test_a_block_module_of_the_name_of_an_outside_glob_does_not_make_that_glob_local(make_repo):
    """The review's outside_glob_rustc (built: `RESULT 2`, the macro-made `helper` wins). A block `mod extcrate` is never what the
    module-level `use extcrate::*` names, so the glob stays one from outside and a name it may bring (or a macro beside the
    derive may make) is unresolved. On the branch this drew `f -> other::helper` once any block anywhere declared `mod extcrate`."""
    for src in (OUTSIDE_GLOB, OUTSIDE_GLOB + "fn unrelated() {\n    mod extcrate {}\n}\n"):
        edges, unresolved = verdicts(make_repo, src, OTHER_DEP)
        assert edges == set(), src
        assert unresolved[("src/lib:f", "helper")] == rs.MACRO_MAY_MAKE


def test_a_module_level_module_of_that_name_still_makes_the_glob_local(make_repo):
    """The set of local names is the one the rule had: a real module `extcrate` is what `use extcrate::*` names, so the glob is not one
    from outside the repo (Index.settle_helpers: CrateCtx.outside_glob). The unknown derive beside it makes the scope one where a
    macro may have made names all the same (test_rs_derive_scope), so what is read here is the flag, not the call."""
    from ripple.indexer import walk_languages

    def outside_glob(src):
        repo = make_repo({"Cargo.toml": PKG + OTHER_DEP, "src/lib.rs": src})
        files, skipped = walk_languages(repo, [RUST])
        return [ctx.outside_glob for ctx in rs.read_repo(repo, files[RUST.name], skipped)[0].ctxs]

    assert outside_glob(OUTSIDE_GLOB) == [True]
    assert outside_glob(OUTSIDE_GLOB + "fn unrelated() {\n    mod extcrate {}\n}\n") == [True]
    assert outside_glob("mod extcrate {\n    pub fn x() {}\n}\n" + OUTSIDE_GLOB) == [False]


MACRO_MOD = (
    "fn one() {}\nfn two() {}\nfn macro_mod() {\n    #[macro_use]\n    mod m {\n        macro_rules! dbg {\n            ($e:expr) => { two() };\n        }\n    }\n"
    "    dbg!(one());\n}\n"
)
IMPL_MOD = (
    "struct S;\ntrait Tr {\n    fn new();\n}\nimpl Tr for S {\n    fn new() {}\n}\nfn inherent() {\n    mod m2 {\n        impl crate::S {\n            pub fn new() {}\n        }\n    }\n"
    "    S::new();\n}\n"
)


def test_a_macro_use_block_module_makes_the_macro_names_of_its_block_the_repos(make_repo):
    """The review's blockmods (built: `dbg!(one())` there calls `two`): a `#[macro_use] mod` of a block leaves its macro_rules in
    scope for the rest of the block, so no standard macro of that block is known to be the standard one: its arguments are not read."""
    edges, unresolved = verdicts(make_repo, MACRO_MOD)
    assert edges == set(), edges
    assert unresolved[("src/lib:macro_mod", "one")] == rs.MACRO_BODY  # the arguments of a macro that may be the repo's are not read as expressions


def test_a_macro_use_block_module_leaves_the_names_of_its_block_unresolved(make_repo):
    src = "fn one() {}\nfn run() {\n    #[macro_use]\n    mod m {\n        macro_rules! dbg {\n            () => {};\n        }\n    }\n    println!();\n    one();\n}\n"
    edges, unresolved = verdicts(make_repo, src)
    assert edges == set()
    assert unresolved[("src/lib:run", "one")] == rs.MACRO_MAY_MAKE


def test_a_block_module_without_macro_use_leaves_the_standard_macros_of_its_block_alone(make_repo):
    src = "fn one() {}\nfn run() {\n    mod m {}\n    println!();\n    one();\n}\n"
    edges, unresolved = verdicts(make_repo, src)
    assert edges == {("src/lib:run", "src/lib:one")} and unresolved == {}


def test_a_macro_use_block_module_leaves_a_block_beside_it_alone(make_repo):
    src = "fn one() {}\nfn run() {\n    {\n        #[macro_use]\n        mod m {}\n    }\n    println!();\n    one();\n}\n"
    edges, _ = verdicts(make_repo, src)
    assert ("src/lib:run", "src/lib:one") in edges


def test_an_impl_in_a_block_module_may_be_the_inherent_function_a_path_reaches(make_repo):
    """The review's blockmods (built: `S::new()` calls the inherent `new` the block module's impl gives, not the trait's). ripple does not
    read the block module, so the impls it read may not be all there are, as for a module file it did not read."""
    edges, unresolved = verdicts(make_repo, IMPL_MOD)
    assert edges == set(), edges
    assert unresolved[("src/lib:inherent", "S::new")].startswith(rs.IMPLS_INCOMPLETE.split("{")[0]), unresolved


def test_without_the_block_module_the_same_path_is_the_traits_function(make_repo):
    src = IMPL_MOD.replace("    mod m2 {\n        impl crate::S {\n            pub fn new() {}\n        }\n    }\n", "")
    edges, _ = verdicts(make_repo, src)
    assert ("src/lib:inherent", "src/lib:<S as Tr>::new") in edges


def test_a_block_module_in_a_function_of_another_crate_taints_only_the_crates_that_can_name_the_type(make_repo):
    files = {
        "Cargo.toml": '[workspace]\nmembers = ["a", "b"]\n',
        "a/Cargo.toml": PKG.replace('"app"', '"a"'),
        "a/src/lib.rs": "pub struct S;\npub trait Tr {\n    fn new();\n}\nimpl Tr for S {\n    fn new() {}\n}\nfn f() {\n    S::new();\n}\n",
        "b/Cargo.toml": PKG.replace('"app"', '"b"'),
        "b/src/lib.rs": "fn g() {\n    mod m {}\n}\n",
    }
    g = build_graph(make_repo(files), RUST)
    assert {(e.caller, e.callee) for e in g.edges if e.kind == "call"} == {("a/src/lib:f", "a/src/lib:<S as Tr>::new")}


def test_a_glob_through_a_block_module_says_so(make_repo):
    """The review's bm_use: the call is unresolved because the glob is of the block's module, and the reason says that."""
    src = "fn g() {\n    mod x {\n        pub fn g() {}\n    }\n    use x::*;\n    g();\n}\n"
    edges, unresolved = verdicts(make_repo, src)
    assert edges == set()
    assert unresolved == {("src/lib:g", "g"): rs.BLOCK_MOD}
