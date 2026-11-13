"""The resolution claims behind the per-scope derive rule, built by rustc, and what ripple says about each.

bench/fixtures/rust-probes/crates/_derive_scope holds one crate per claim. Each writes `#[derive(Debug)]` beside a `use genmacros::Debug;` or
`use genmacros::*;` (crates/_ext/genmacros exports a derive called `Debug` that generates `fn one() -> u32 { 2 }` and no impl of the
trait), and builds one of three ways:

  * "std": it builds and its `#[test]` passes, and the test formats the type with `{:?}`, which only std's derive makes possible:
    the name is the standard library's here;
  * "other": it builds and its `#[test]` passes, and the test calls a function that genmacros' derive replaced (`one()` is 2): the
    name is genmacros' here;
  * "E0659": rustc refuses it as ambiguous (`Debug` from a glob, or from a `use` a macro expanded, against the prelude's `Debug`), so
    no crate that builds has that glob or that expansion be what the name is.

The rustc half runs only where a toolchain exists (cargo and rustc under $RIPPLE_RUST_TOOLCHAIN or /tmp/rust-toolchain, offline: each
crate depends on a path); the ripple half always runs. Ripple must mark ("other") every derive that rustc says is genmacros', and marks
none of the "std" ones: each is a use the derive cannot see.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytest.importorskip("tree_sitter_rust")

from conftest import FIXTURES  # noqa: E402

from ripple import rs_indexer as rs  # noqa: E402
from ripple.indexer import walk_languages  # noqa: E402
from ripple.profile import RUST  # noqa: E402

CRATES = FIXTURES / "rust-probes" / "crates"
SCOPE = CRATES / "_derive_scope"
STD, OTHER, AMBIGUOUS = "std", "other", "E0659"

# probe -> what rustc does with it
OUTCOME = {
    # a glob, or a use a macro expanded, that gives another `Debug` beside the prelude's: the compiler refuses
    "glob_local_reexport": AMBIGUOUS,  # `mod a { pub use genmacros::Debug; } use a::*;`
    "glob_extern": AMBIGUOUS,  # `use genmacros::*;`
    "glob_parent_private_use": AMBIGUOUS,  # a child's `use super::*;` of the parent's private `use genmacros::Debug;`
    "glob_2015": AMBIGUOUS,
    "glob_2018": AMBIGUOUS,
    "glob_2024": AMBIGUOUS,
    "macro_expanded_use": AMBIGUOUS,  # `macro_rules! imp { () => { use genmacros::Debug; } } imp!();` at module level
    "macro_expanded_use_in_block": AMBIGUOUS,
    "no_implicit_prelude_glob": AMBIGUOUS,  # the standard derives stay in scope under #![no_implicit_prelude]
    "no_implicit_prelude_mod_glob": AMBIGUOUS,
    "glob_in_block_sibling_module_use": AMBIGUOUS,  # a block's `use std::fmt::*;` against the module's `use genmacros::Debug;`
    # a use the derive cannot see, or a name that is no macro's: std's derive runs
    "module_not_parent": STD,  # the parent's and a sibling's `use genmacros::Debug;`
    "module_child_not_seen": STD,
    "module_not_parent_2015": STD,
    "block_other_fn_not_seen": STD,  # another function body's use
    "block_sibling_block_not_seen": STD,
    "block_outer_not_seeing_inner": STD,  # a block inside the derive's block
    "block_mod_in_block_not_seen": STD,  # a `mod` written in a block is a module: the block's use is not its
    "glob_std_same": STD,  # `use std::fmt::*;` gives the same macro
    "extern_crate_named_debug": STD,  # `extern crate genmacros as Debug;` is no macro
    "type_named_debug": STD,
    # a use the derive sees: genmacros' derive runs
    "module_own_use": OTHER,
    "use_2015_own": OTHER,
    "block_own_use": OTHER,
    "block_use_after_item": OTHER,  # an item is visible from the whole block
    "block_nested_sees_outer_block": OTHER,
    "block_sees_module_use": OTHER,
    "block_inner_fn_sees_outer_block_use": OTHER,  # a function inside a block sees that block's `use`
    "block_closure_use": OTHER,
    "block_method_body": OTHER,
    "prelude_import_child": OTHER,  # nightly: `#[prelude_import] use mypre::*;` makes the crate's prelude its own, and `mypre` gives `Debug` (the probe needs RUSTC_BOOTSTRAP)
    "prelude_import_macro": OTHER,  # the attribute written in a macro_rules body: ripple does not read it, the gate in the root does
    "prelude_import_include": OTHER,  # in an include!d file
    "prelude_import_blockmod": OTHER,  # in a mod written in a block
    "block_impl_in_block": OTHER,  # a method of an impl written in a block sees that block's use
    "static_block_own_use": OTHER,  # the block of a static's initializer
}


BLOCK_MOD = "block_mod_in_block_not_seen"  # the one probe whose derive ripple does not read (see the ripple half)
NIGHTLY = {"prelude_import_child", "prelude_import_macro", "prelude_import_include", "prelude_import_blockmod"}  # the probes that build only with RUSTC_BOOTSTRAP=1 (an unstable feature)


def toolchain():
    root = Path(os.environ.get("RIPPLE_RUST_TOOLCHAIN", "/tmp/rust-toolchain"))
    return root if (root / "cargo" / "bin" / "cargo").is_file() and (root / "cargo" / "bin" / "rustc").is_file() else None


@pytest.fixture(scope="module")
def target(tmp_path_factory):
    return tmp_path_factory.mktemp("derive-scope-target")


def test_every_probe_of_the_directory_is_in_the_table():
    assert sorted(p.name for p in SCOPE.iterdir() if p.is_dir()) == sorted(OUTCOME)


@pytest.mark.parametrize("name", sorted(OUTCOME))
def test_rustc_says_so(name, target, tmp_path):
    root = toolchain()
    if root is None:
        pytest.skip("no Rust toolchain (set RIPPLE_RUST_TOOLCHAIN to a directory with cargo/bin)")
    shutil.copytree(CRATES / "_ext", tmp_path / "crates" / "_ext", ignore=shutil.ignore_patterns("target", "Cargo.lock"))
    shutil.copytree(SCOPE / name, tmp_path / "crates" / "_derive_scope" / name, ignore=shutil.ignore_patterns("target", "Cargo.lock"))
    env = {**os.environ, "CARGO_HOME": str(root / "cargo"), "RUSTUP_HOME": str(root / "rustup"), "CARGO_TARGET_DIR": str(target), "PATH": f"{root}/cargo/bin:" + os.environ["PATH"]}
    if name in NIGHTLY:
        env["RUSTC_BOOTSTRAP"] = "1"  # the probe uses an unstable feature
    run = subprocess.run(
        ["cargo", "test", "--offline", "--quiet", "--manifest-path", str(tmp_path / "crates" / "_derive_scope" / name / "Cargo.toml")],
        env=env, capture_output=True, text=True, timeout=600,
    )
    out = run.stdout + run.stderr
    if OUTCOME[name] == AMBIGUOUS:
        assert run.returncode != 0 and "error[E0659]: `Debug` is ambiguous" in out, out[-1500:]
    else:
        assert run.returncode == 0 and "test result: ok. 1 passed; 0 failed" in out, out[-1500:]


@pytest.mark.parametrize("name", sorted(n for n, o in OUTCOME.items() if o != AMBIGUOUS))
def test_ripple_marks_the_derive_exactly_where_rustc_says_it_is_another_macro(name):
    files, skipped = walk_languages(SCOPE / name, [RUST])
    holding = [c for c in rs.read_repo(SCOPE / name, files[RUST.name], skipped)[0].ctxs if c.pending_derives]
    if name == BLOCK_MOD:
        # a `mod` written in a block is opaque to ripple (Index.collect_items): its derive is never collected, so there is nothing for
        # the scope rule to judge, and this probe says nothing about ripple's scope model: it is asserted, not passed by default
        assert holding == []
        return
    # the check below is vacuous for a derive ripple never collected: exactly one crate of the probe holds the derive it is about
    [ctx] = holding
    assert [names for _, names, _, _ in ctx.pending_derives] == [("Debug",)], ctx.pending_derives
    assert bool(ctx.derive_replaced) == (OUTCOME[name] == OTHER), (name, ctx.derive_replaced, ctx.pending_derives)
    if OUTCOME[name] == OTHER:
        assert ctx.derive_replaced == {0: "Debug"}
