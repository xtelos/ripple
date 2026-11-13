"""A crate whose `std` (or, in a `#![no_std]` crate, `core`) is not the standard library has another prelude, and its bare `#[derive(Debug)]`
is whatever that prelude gives the name, with no `use` of the name anywhere.

Built with rustc on stable (the probes under bench/fixtures/rust-probes/crates/_derive_scope, run by tests/test_rs_derive_scope_probes.py):

  * a dependency keyed `std` (`std = { package = "fakestd", path = ".." }`) whose `prelude::rust_2021` re-exports genmacros' `Debug`
    (std_dep_prelude), and a dependency keyed `core` in a `#![no_std]` crate (core_dep_no_std): the crate's derive is genmacros';
  * a dependency whose library is named `std`, though its key is not (std_lib_named_std): the same;
  * a package whose own library is named `std`: its binary's derive is genmacros' (std_own_lib_named_std);
  * `extern crate fakestd as std;` and `extern crate self as std;` at the root do NOT replace the prelude: the derive stays std's.

So every bare standard derive of such a crate is marked, as with a `#[macro_use] extern crate`. The two `extern crate` forms are left alone,
and so is a crate whose `std` is only the standard library (the control). What ripple cannot see is a library named `std` or `core` of a
package outside the repo, or a flag cargo is given from outside it (RUSTFLAGS, a config above the repo): neither is read.
"""

import pytest

pytest.importorskip("tree_sitter_rust")

from test_rs_derive_scope import PKG, calls  # noqa: E402

from ripple import rs_indexer as rs  # noqa: E402
from ripple.indexer import build_graph, walk_languages  # noqa: E402
from ripple.profile import RUST  # noqa: E402

BODY = "mod other {\n    pub fn one() {}\n}\npub mod c {\n    use super::other::*;\n    #[derive(Debug)]\n    pub struct S;\n    pub fn f() {\n        one();\n    }\n}\n"
EDGE = {("src/lib:c::f", "edge", "src/lib:other::one")}
MAKE = {("src/lib:c::f", "unresolved", rs.MACRO_MAY_MAKE)}


def verdict(make_repo, tail, src=BODY):
    return calls(make_repo, src, deps=tail)


@pytest.mark.parametrize(
    "tail",
    [
        '[dependencies]\nstd = "1"\n',
        '[dependencies]\ncore = "1"\n',
        '[dependencies]\nstd = { package = "fakestd", version = "1" }\n',
        '[dependencies]\ncore = { package = "fakestd", path = "../fakestd" }\n',
        '[dev-dependencies]\nstd = "1"\n',
        '[build-dependencies]\ncore = "1"\n',
        "[target.'cfg(unix)'.dependencies]\nstd = \"1\"\n",
        '[dependencies]\nrustc-std-workspace-core = "1"\n',
        '[dependencies]\nrustc-std-workspace-std = "1"\n',
    ],
)
def test_a_dependency_keyed_std_or_core_replaces_the_prelude_so_the_bare_derive_is_marked(make_repo, tail):
    assert verdict(make_repo, tail) == MAKE, tail


def test_a_dependency_of_another_key_leaves_the_derive_the_standard_librarys(make_repo):
    assert verdict(make_repo, '[dependencies]\nfakestd = "1"\nalloc = "1"\n') == EDGE
    assert verdict(make_repo, "") == EDGE


@pytest.mark.parametrize("alias", ["extern crate fake as std;\n", "extern crate fake as core;\n", "extern crate self as std;\n", "#![no_std]\nextern crate std;\n"])
def test_an_extern_crate_alias_of_std_or_core_leaves_the_prelude_alone(make_repo, alias):
    """Built: `extern crate fakestd as std;` and `extern crate self as std;` at the root leave the derive std's (the probes
    std_extern_crate_alias and std_self_alias: `one()` is the glob's, 1). Marked here would only cost recall, but the rule is what rustc does."""
    assert verdict(make_repo, '[dependencies]\nfake = "1"\n', alias + BODY) == EDGE, alias


def test_a_member_whose_library_is_named_std_replaces_the_prelude_of_the_package_that_depends_on_it(make_repo):
    """The key is not `std` (the probe std_lib_named_std: cargo passes `--extern std` for the library's name), but the library is."""
    files = {
        "Cargo.toml": '[workspace]\nmembers = ["app", "stdlib"]\n',
        "app/Cargo.toml": '[package]\nname = "app"\nversion = "0.1.0"\nedition = "2021"\n[dependencies]\nstdlib = { path = "../stdlib" }\n',
        "app/src/lib.rs": BODY,
        "stdlib/Cargo.toml": '[package]\nname = "stdlib"\nversion = "0.1.0"\nedition = "2021"\n[lib]\nname = "std"\n',
        "stdlib/src/lib.rs": "pub fn nothing() {}\n",
    }
    g = build_graph(make_repo(files), RUST)
    got = {(e.caller, "edge", e.callee) for e in g.edges if e.kind == "call"} | {(u.caller, "unresolved", u.reason) for u in g.unresolved}
    assert ("app/src/lib:c::f", "unresolved", rs.MACRO_MAY_MAKE) in got, got
    assert not any(k == "edge" and c.startswith("app/") for c, k, _ in got), got
    files["app/Cargo.toml"] = files["app/Cargo.toml"].replace("stdlib", "stdlibx").replace('"../stdlibx"', '"../stdlib"')
    files["stdlib/Cargo.toml"] = '[package]\nname = "stdlibx"\nversion = "0.1.0"\nedition = "2021"\n'
    got = {(e.caller, "edge", e.callee) for e in build_graph(make_repo(files), RUST).edges if e.kind == "call"}
    assert ("app/src/lib:c::f", "edge", "app/src/lib:other::one") in got, got  # the control: a library of an ordinary name


def test_a_package_whose_own_library_is_named_std_marks_the_derives_of_its_binary(make_repo):
    """The probe std_own_lib_named_std: the binary gets `--extern std=<the package's library>`, so its prelude is that library's."""
    repo = make_repo(
        {
            "Cargo.toml": PKG + '[lib]\nname = "std"\n\n[[bin]]\nname = "own"\npath = "src/main.rs"\n',
            "src/lib.rs": "pub fn nothing() {}\n",
            "src/main.rs": "#[derive(Debug)]\nstruct S;\nfn main() {}\n",
        }
    )
    files, skipped = walk_languages(repo, [RUST])
    ctxs = rs.read_repo(repo, files[RUST.name], skipped)[0].ctxs
    assert [dict(c.derive_replaced) for c in ctxs if c.pending_derives] == [{0: "Debug"}]


def test_a_library_called_core_is_marked_too(make_repo):
    repo = make_repo({"Cargo.toml": PKG + '[lib]\nname = "core"\n', "src/lib.rs": "#[derive(Debug)]\nstruct S;\n"})
    files, skipped = walk_languages(repo, [RUST])
    [ctx] = rs.read_repo(repo, files[RUST.name], skipped)[0].ctxs
    assert ctx.derive_replaced == {0: "Debug"}, "a crate that is itself called core: not proved std's prelude, so marked (recall only)"
