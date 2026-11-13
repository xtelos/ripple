"""Every place ripple takes a name from the standard library or its prelude, once the crate's `std`, `core` or prelude may be another crate's.

The compiler-built claims are in tests/test_rs_prelude_replaced_probes.py (the probes of crates/_prelude_replaced). These pin the forms a probe
does not need: each prelude name of the lists, `extern crate std as s;`, a macro written from `std`, and the control of each (the standard
library's own prelude, where every verdict stays what it was).

Where ripple leans on "the standard library gives this name", and what it does when the prelude may be another crate's:

  * a type, a trait, a function or a constructor of the prelude (PRELUDE_TYPES, PRELUDE_VALUES) and a primitive type's name (PRIMITIVES):
    unresolved (PRELUDE_REPLACED), not external or a constructor;
  * `std`, `core`, `alloc`, `proc_macro` and `test` as a crate name, in a path, a global path, an `extern crate` alias: unresolved
    (STD_NAME_REPLACED) when a dependency key, a library or something ripple cannot read may be that name;
  * the blanket functions of the prelude's traits (`S::to_string(..)`, STD_BLANKET_FNS): unresolved (PRELUDE_REPLACED);
  * the attributes of the prelude (SHADOWABLE_ATTRS: test, bench, derive, global_allocator): read as macros (Index.shadow_problem);
  * a standard derive written bare: marked (Index.derive_step, as before);
  * a macro written from `std`/`core`/`alloc` (is_std_macro, macro_is_std): not the library's, so its arguments are not read as calls;
  * NOT changed: the macros written bare (`println!`, `vec!`, `assert_eq!`, STD_PURE_MACROS, STD_EXPR_MACROS): the macro_use prelude is the
    sysroot's whatever the prelude is (probes std_dep_macros, core_dep_macros), so they keep their edges.
"""

import pytest

pytest.importorskip("tree_sitter_rust")

from test_rs_derive_scope import PKG  # noqa: E402

from ripple import rs_indexer as rs  # noqa: E402
from ripple.indexer import build_graph  # noqa: E402
from ripple.profile import RUST  # noqa: E402

REPLACED = '[dependencies]\nstd = "1"\n'  # a dependency keyed std: the crate's std, and so its prelude, may be that crate
STD_LIB = {
    "stdlib/Cargo.toml": '[package]\nname = "stdlib"\nversion = "0.1.0"\nedition = "2021"\n[lib]\nname = "std"\n',
    "stdlib/src/lib.rs": "pub fn mark() {}\n",
}
LIB_STD = '[dependencies]\nstdlib = { path = "stdlib" }\n'  # a dependency of another key whose library is called std


def verdicts(make_repo, src, tail="", extra=None):
    """{call as written: the reason constant name, or edge/external/ctor} of a one-file crate."""
    from ripple.indexer import walk_languages

    repo = make_repo({"Cargo.toml": PKG + tail, "src/lib.rs": src, **(extra or {})})
    files, skipped = walk_languages(repo, [RUST])
    index = rs.read_repo(repo, files[RUST.name], skipped)[0]
    out = {}
    for _, _, _, result, _, _, written, _ in index.resolved_sites():
        reason = next((n for n in ("PRELUDE_REPLACED", "STD_NAME_REPLACED", "PRELUDE", "STD_PATH", "PRIMITIVE", "STD_BLANKET", "MACRO_MAY_MAKE", "MACRO_BODY") if getattr(rs, n) == result[1]), result[1]) if len(result) > 1 and isinstance(result[1], str) else result[0]
        out[written] = reason if result[0] in ("external", "unresolved") else result[0]
    return out


NAME_CALLS = {
    "Box::new": "PRELUDE",
    "String::from": "PRELUDE",
    "Vec::with_capacity": "PRELUDE",
    "Option::unwrap_or": "PRELUDE",
    "drop": "PRELUDE",
    "u32::from_str_radix": "PRIMITIVE",
    "char::from_u32": "PRIMITIVE",
    "Default::default": "EXT_TRAIT",
}


@pytest.mark.parametrize("written", sorted(NAME_CALLS))
def test_each_prelude_name_is_the_standard_librarys_only_where_the_prelude_is(make_repo, written):
    src = f"pub fn f() {{\n    {written}(1);\n}}\n"
    assert verdicts(make_repo, src)[written] != "PRELUDE_REPLACED"  # the control: the standard prelude
    assert verdicts(make_repo, src, REPLACED)[written] == "PRELUDE_REPLACED", written
    assert verdicts(make_repo, "#![feature(prelude_import)]\n" + src)[written] == "PRELUDE_REPLACED", written  # the gate of a prelude of its own, in the root
    assert verdicts(make_repo, src, LIB_STD, STD_LIB)[written] == "PRELUDE_REPLACED", written


@pytest.mark.parametrize("name", ["Ok", "Err", "Some"])
def test_a_constructor_of_the_prelude_is_one_only_where_the_prelude_is(make_repo, name):
    src = f"pub fn f() {{\n    {name}(1);\n}}\n"
    assert verdicts(make_repo, src)[name] == "ctor"
    assert verdicts(make_repo, src, REPLACED)[name] == "PRELUDE_REPLACED"


def test_a_path_that_starts_at_a_crate_called_std_is_the_librarys_only_where_the_crate_is(make_repo):
    src = "pub fn f() {\n    std::mem::drop(1);\n    ::core::mem::drop(1);\n    alloc::vec::Vec::new();\n}\n"
    assert set(verdicts(make_repo, src).values()) == {"STD_PATH"}
    lib = {"stdlib/src/lib.rs": "pub fn mark() {}\npub mod mem { pub fn drop(_: u32) {} }\n"}
    got = verdicts(make_repo, src, LIB_STD, {**STD_LIB, **lib})
    assert got["std::mem::drop"] == "STD_NAME_REPLACED" and got["::core::mem::drop"] == "STD_PATH", got  # only the names a library may take
    for lib_name in ("core", "alloc"):
        tail = f'[dependencies]\nother = {{ path = "other" }}\n'
        files = {"other/Cargo.toml": f'[package]\nname = "other"\nversion = "0.1.0"\nedition = "2021"\n[lib]\nname = "{lib_name}"\n', "other/src/lib.rs": "pub mod vec { pub struct Vec; impl Vec { pub fn new() {} } }\n"}
        got = verdicts(make_repo, src, tail, files)
        assert got["alloc::vec::Vec::new"] == ("STD_NAME_REPLACED" if lib_name == "alloc" else "STD_PATH"), (lib_name, got)
        assert got["::core::mem::drop"] == ("STD_NAME_REPLACED" if lib_name == "core" else "STD_PATH"), (lib_name, got)


def test_an_extern_crate_alias_of_a_crate_called_std_is_not_known_to_be_the_library(make_repo):
    src = "extern crate std as s;\npub fn f() {\n    s::mem::drop(1);\n}\npub mod c {\n    pub fn g() {\n        s::mem::swap(&mut 1, &mut 2);\n    }\n}\n"  # c reaches the alias through the extern prelude
    assert verdicts(make_repo, src)["s::mem::drop"] == "STD_PATH"
    assert verdicts(make_repo, src)["s::mem::swap"] == "STD_PATH"
    got = verdicts(make_repo, src, LIB_STD, STD_LIB)
    assert got["s::mem::drop"] == "STD_NAME_REPLACED" and got["s::mem::swap"] == "STD_NAME_REPLACED", got


def test_a_blanket_function_of_a_prelude_trait_is_the_librarys_only_where_the_prelude_is(make_repo):
    src = "pub struct S;\npub fn f() {\n    S::to_string(&S);\n    S::into(S);\n}\n"
    assert set(verdicts(make_repo, src).values()) == {"STD_BLANKET"}
    assert set(verdicts(make_repo, src, REPLACED).values()) == {"PRELUDE_REPLACED"}
    assert set(verdicts(make_repo, "#![feature(prelude_import)]\n" + src).values()) == {"PRELUDE_REPLACED"}


@pytest.mark.parametrize("attr", ["test", "bench", "derive(Clone)", "global_allocator"])
def test_an_attribute_of_the_prelude_is_another_macro_where_the_prelude_may_be_another_crates(make_repo, attr):
    """`#[test]` and its three are macros of the prelude (the probes std_dep_test and prelude_import_test): another prelude gives another."""
    body = f"mod other {{\n    pub fn helper() {{}}\n}}\nmod c {{\n    use super::other::*;\n    #[{attr}]\n    static S: u8 = 1;\n    pub fn two() {{\n        helper();\n    }}\n}}\n"
    got = verdicts(make_repo, body.replace("static S: u8 = 1;", "fn t() {}" if attr in ("test", "bench") else "struct S;" if attr.startswith("derive") else "static S: u8 = 1;"))
    assert got["helper"] == "edge", got  # the control: the attribute is the compiler's, so nothing was made beside it
    assert verdicts(make_repo, body.replace("static S: u8 = 1;", "fn t() {}" if attr in ("test", "bench") else "struct S;" if attr.startswith("derive") else "static S: u8 = 1;"), REPLACED)["helper"] != "edge"


def test_a_macro_written_from_a_crate_called_std_is_the_librarys_only_where_the_crate_is(make_repo):
    src = "mod other {\n    pub fn helper() {}\n}\nuse other::*;\npub fn f() {\n    std::println!(\"{}\", 1);\n    helper();\n}\n"
    assert verdicts(make_repo, src)["helper"] == "edge"  # the control: `std::println!` defines no name
    got = verdicts(make_repo, src, LIB_STD, STD_LIB)
    assert got["helper"] == "MACRO_MAY_MAKE", got


def test_a_macro_written_bare_stays_the_librarys_whatever_the_prelude_is(make_repo):
    """The probes std_dep_macros and core_dep_macros: the macro_use prelude is the sysroot's. Marking these would lose edges for nothing."""
    src = "mod other {\n    pub fn helper() -> u32 { 1 }\n}\npub fn f() {\n    println!(\"{}\", other::helper());\n    let _ = vec![other::helper()];\n}\n"
    for tail in ("", REPLACED, LIB_STD):
        got = verdicts(make_repo, src, tail, STD_LIB if tail == LIB_STD else None)
        assert got["other::helper"] == "edge", (tail, got)
    assert verdicts(make_repo, "#![feature(prelude_import)]\n" + src)["other::helper"] == "edge"


def test_the_names_the_crate_declares_or_imports_are_looked_up_before_the_prelude_as_before(make_repo):
    src = "struct Vec;\nimpl Vec {\n    fn new() {}\n}\npub fn f() {\n    Vec::new();\n}\n"
    got = verdicts(make_repo, src, REPLACED)
    assert got["Vec::new"] == "edge", got  # its own `Vec` is no prelude name
