"""Where a crate's standard prelude may not be the standard library's, read from the manifests and the cargo configs of the repo.

tests/test_rs_derive_std_replaced.py pins the dependency keyed `std` or `core`, a library of that name, and the package's own. These are the
other ways cargo gives a crate another `std`, each built with rustc (the probes under bench/fixtures/rust-probes/crates/_derive_scope, run by
tests/test_rs_derive_scope_probes.py: the derive of `c` is genmacros' under every one):

  * `dev_underscore_std`, `target_dev_underscore_std`, `build_underscore_std`: the tables written `dev_dependencies` and `build_dependencies`;
  * `manifest_patch_std`: a `[patch]` in a package that has no `[workspace]` table (the same with one is `manifest_patch_ws_std`);
  * `cfg_patch_std`: a `[patch]` in a `.cargo/config.toml` of the repo;
  * `outside_path_lib_std`: a path dependency outside the repo whose library is called `std`;
  * `crate_attr_prelude_include`: `-Zcrate-attr=feature(prelude_import)` in the rustflags of a `.cargo/config.toml`, which opens the gate of
    the crate's own prelude with nothing in the source naming the feature.

A derive that ripple marks is read as possibly another macro; one it does not is std's, so a glob's name reaches the call (EDGE). The
controls pin what stays readable: a workspace `[patch.crates-io]` to packages of the repo (the serde corpus), rustflags that only set
codegen, and a registry dependency.
"""

import pytest

pytest.importorskip("tree_sitter_rust")

from test_rs_derive_scope import PKG  # noqa: E402
from test_rs_derive_std_replaced import BODY, EDGE, MAKE  # noqa: E402

from ripple.indexer import build_graph  # noqa: E402
from ripple.profile import RUST  # noqa: E402

LIB_STD = '[package]\nname = "stdlibname"\nversion = "0.1.0"\nedition = "2021"\n[lib]\nname = "std"\n'
LIB_PLAIN = '[package]\nname = "stdlibname"\nversion = "0.1.0"\nedition = "2021"\n'
NOTHING = "pub fn nothing() {}\n"


def verdict(make_repo, manifest_tail, extra=None, src=BODY):
    """`calls` of the derive probe with a manifest tail and more files beside it."""
    files = {"Cargo.toml": PKG + manifest_tail, "src/lib.rs": src, **(extra or {})}
    g = build_graph(make_repo(files), RUST)
    return {(e.caller, "edge", e.callee) for e in g.edges if e.kind == "call" and e.caller.startswith("src/")} | {
        (u.caller, "unresolved", u.reason) for u in g.unresolved if u.caller.startswith("src/")
    }


def std_lib(extra=None, plain=False):
    return {"stdlibname/Cargo.toml": LIB_PLAIN if plain else LIB_STD, "stdlibname/src/lib.rs": NOTHING, **(extra or {})}


@pytest.mark.parametrize(
    "tail",
    [
        '[dev_dependencies]\nstd = "1"\n',
        '[build_dependencies]\ncore = "1"\n',
        "[target.'cfg(unix)'.dev_dependencies]\nstd = \"1\"\n",
        "[target.'cfg(unix)'.build_dependencies]\ncore = \"1\"\n",
        '[dev_dependencies]\nstd = { package = "fakestd", path = "../fakestd" }\n',
    ],
)
def test_the_underscore_spelling_of_the_dev_and_build_tables_gives_the_prelude_too(make_repo, tail):
    """The probes dev_underscore_std, target_dev_underscore_std and build_underscore_std: cargo reads these tables before edition 2024."""
    assert verdict(make_repo, tail) == MAKE, tail


def test_a_dependency_of_the_underscore_tables_is_a_dependency_of_the_package(make_repo):
    """Not only the prelude: a path dependency written in them is a package of the repo that the crate can name, as in the hyphen tables."""
    files = {
        "Cargo.toml": '[workspace]\nmembers = ["app", "util"]\n',
        "app/Cargo.toml": '[package]\nname = "app"\nversion = "0.1.0"\nedition = "2021"\n[dev_dependencies]\nutil = { path = "../util" }\n',
        "app/src/lib.rs": "#[test]\nfn t() {\n    util::helper();\n}\n",
        "util/Cargo.toml": '[package]\nname = "util"\nversion = "0.1.0"\nedition = "2021"\n',
        "util/src/lib.rs": "pub fn helper() {}\n",
    }
    g = build_graph(make_repo(files), RUST)
    assert {(e.caller, e.callee) for e in g.edges if e.kind == "call"} == {("app/src/lib:t", "util/src/lib:helper")}


def test_a_patch_in_a_package_without_a_workspace_table_is_read_as_the_workspaces_is(make_repo):
    """The probe manifest_patch_std: cargo treats a lone package as its own workspace root and applies its `[patch]`."""
    patch = '[dependencies]\nstdlibname = "0.1"\n[patch.crates-io]\nstdlibname = { path = "stdlibname" }\n'
    assert verdict(make_repo, patch, std_lib()) == MAKE
    assert verdict(make_repo, patch + "[workspace]\n", std_lib()) == MAKE  # the probe manifest_patch_ws_std
    assert verdict(make_repo, patch, std_lib(plain=True)) == EDGE  # the control: the patch is a library of an ordinary name


def test_a_patched_dependency_is_never_an_edge_into_the_package_of_the_repo_that_patches_it(make_repo):
    """Rule P (tests/test_rs_redirect.py): `stdlibname::g()` is unresolved where a patch names the package, not an edge to the patched package, and not
    external."""
    files = {
        "Cargo.toml": PKG + '[dependencies]\nstdlibname = "0.1"\n[patch.crates-io]\nstdlibname = { path = "stdlibname" }\n',
        "src/lib.rs": "pub fn f() {\n    stdlibname::g();\n}\n",
        "stdlibname/Cargo.toml": LIB_PLAIN,
        "stdlibname/src/lib.rs": "pub fn g() {}\n",
    }
    g = build_graph(make_repo(files), RUST)
    assert {(e.caller, e.callee) for e in g.edges if e.kind == "call"} == set()


def test_a_patch_in_a_cargo_config_of_the_repo_may_make_a_dependency_a_library_called_std(make_repo):
    """The probe cfg_patch_std. The config is read as the trust proof reads it (a patch is refused there too)."""
    deps = '[dependencies]\nstdlibname = "0.1"\n'
    config = {".cargo/config.toml": '[patch.crates-io]\nstdlibname = { path = "stdlibname" }\n'}
    assert verdict(make_repo, deps, {**std_lib(), **config}) == MAKE
    assert verdict(make_repo, deps, {**std_lib(plain=True), **config}) == MAKE  # a config is not followed: marked whichever library it names


@pytest.mark.parametrize("flags", ['["-Zcrate-attr=feature(prelude_import)"]', '["-Z", "crate-attr=feature(prelude_import)"]', '["--extern", "std=/x/libstd.rlib"]', '["--sysroot", "/x"]'])
@pytest.mark.parametrize("table", ["[build]", "[target.'cfg(unix)']", "[target.x86_64-unknown-linux-gnu]"])
def test_rustflags_that_are_not_codegen_or_lints_may_change_the_prelude(make_repo, table, flags):
    """The probe crate_attr_prelude_include: the gate of `#[prelude_import]` comes from a flag, with nothing in the source naming it."""
    config = {".cargo/config.toml": f"{table}\nrustflags = {flags}\n"}
    assert verdict(make_repo, "", config) == MAKE, (table, flags)


@pytest.mark.parametrize(
    "config",
    [
        '[build]\nrustflags = ["-C", "target-feature=+crt-static"]\n',
        "[target.x86_64-pc-windows-msvc]\nrustflags = [\"-C\", \"link-arg=/DELAYLOAD:shell32.dll\"]\n",
        '[resolver]\nincompatible-rust-versions = "fallback"\n',
        '[build]\nrustflags = ["-D", "warnings", "--cfg", "foo"]\n',
        '[net]\nretry = 3\n',
    ],
)
def test_a_config_that_only_sets_codegen_and_how_cargo_runs_leaves_the_prelude_alone(make_repo, config):
    """The configs of the bat, fd, clap and ripgrep corpora: marking them would lose their derive edges for no reason."""
    assert verdict(make_repo, "", {".cargo/config.toml": config}) == EDGE, config


def test_a_cargo_config_in_a_directory_of_the_repo_that_is_not_the_packages_is_read_too(make_repo):
    """Cargo reads the config of the directory it is run in, which may be any directory of the repo."""
    config = {"scripts/.cargo/config.toml": '[build]\nrustflags = ["-Zcrate-attr=feature(prelude_import)"]\n'}
    assert verdict(make_repo, "", config) == MAKE


@pytest.mark.parametrize(
    "dep",
    [
        'stdlibname = { path = "../outside/stdlibname" }',
        'stdlibname = { path = "../outside/stdlibname", package = "other" }',
    ],
)
def test_a_dependency_from_outside_the_repo_may_be_a_library_called_std(make_repo, dep):
    """The probe outside_path_lib_std: ripple cannot read that library's name, so what it cannot read it does not call std's."""
    assert verdict(make_repo, f"[dependencies]\n{dep}\n") == MAKE, dep


@pytest.mark.parametrize(
    "dep",
    [
        'stdlibname = "0.1"',
        'stdlibname = { version = "0.1", features = ["a"] }',
        'stdlibname = { version = "0.1", registry = "other" }',
        'stdlibname = { git = "https://example.com/stdlibname" }',
    ],
)
def test_a_registry_or_git_dependency_is_assumed_to_have_an_ordinary_library_name(make_repo, dep):
    """The one assumption left, which the README states: a crate from a registry or a git repository is not a library named std or core.
    (Unproved: whether crates.io refuses such a name needs a publish, which is not offline. Marking every crate with a git dependency would
    make every prelude name of it unresolved.)"""
    assert verdict(make_repo, f"[dependencies]\n{dep}\n") == EDGE, dep


def test_a_replace_entry_may_make_a_dependency_another_crate(make_repo):
    tail = '[dependencies]\nstdlibname = "0.1"\n[replace]\n"stdlibname:0.1.0" = { path = "stdlibname" }\n'
    assert verdict(make_repo, tail, std_lib(plain=True)) == MAKE


def test_a_workspace_patch_to_packages_of_the_repo_leaves_the_rest_of_the_crate_readable(make_repo):
    """The serde corpus: `[patch.crates-io]` to paths of the repo, whose libraries are named as the packages are. The dependency a patch names is
    unresolved (Rule P); the derive of the crate is still the standard library's, since the patched library is not called std."""
    files = {
        "Cargo.toml": '[workspace]\nmembers = ["app", "serde"]\n[patch.crates-io]\nserde = { path = "serde" }\n',
        "app/Cargo.toml": '[package]\nname = "app"\nversion = "0.1.0"\nedition = "2021"\n[dependencies]\nserde = "1"\n',
        "app/src/lib.rs": BODY + "pub fn user() {\n    serde::go();\n}\n",
        "serde/Cargo.toml": '[package]\nname = "serde"\nversion = "1.0.0"\nedition = "2021"\n',
        "serde/src/lib.rs": "pub fn go() {}\n",
    }
    g = build_graph(make_repo(files), RUST)
    got = {(e.caller, e.callee) for e in g.edges if e.kind == "call"}
    assert ("app/src/lib:c::f", "app/src/lib:other::one") in got, got  # the derive is std's: the glob's name
    assert ("app/src/lib:user", "serde/src/lib:go") not in got, got  # and the patch is not followed


def test_a_patch_of_a_name_the_package_does_not_depend_on_marks_nothing(make_repo):
    tail = '[dependencies]\nfake = "1"\n[patch.crates-io]\nother = { path = "stdlibname" }\n'
    assert verdict(make_repo, tail, std_lib()) == EDGE
