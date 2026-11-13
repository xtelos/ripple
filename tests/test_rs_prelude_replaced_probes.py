"""What rustc does with a crate whose standard prelude, or whose `std`, `core` or `alloc`, is not the standard library's, and what ripple says
about each call that leans on "the standard library gives this name".

bench/fixtures/rust-probes/crates/_prelude_replaced holds one crate per claim. Each builds and its `#[test]` passes, which only holds when the
name under test was NOT the standard library's (the fake answers 2; the real one does not type-check, or answers differently):

  * std_dep_names, prelude_import_names: a dependency keyed `std` (stable) or `#[prelude_import] use mypre::*;` (nightly) whose prelude gives
    `Some`, `drop`, `Vec`, `u8`, `Default` and `ToString` of its own. Every call that ripple read as the standard library's (a constructor, an
    external "a name the standard prelude brings in", a primitive's function, the blanket `to_string`) runs the fake's.
  * std_dep_test, prelude_import_test: the same prelude gives `test` another macro (genmacros' `thing`, which adds `fn helper() -> u32 { 2 }`):
    `#[test]` is the prelude's macro, not the compiler's.
  * lib_std_calls, lib_core_calls, lib_alloc_calls: a dependency of another key whose library is called std, core or alloc. `std::mark()` is its
    function, in the repo, not "a path into the standard library".
  * lib_std_macro_path: `std::println!(..)` written with a path from a library called std is that library's macro, which evaluates nothing.
  * std_dep_macros, core_dep_macros: the control that LIMITS the claim: an unqualified `println!`, `vec!`, `assert_eq!`, `matches!`, `todo!` is
    still the standard library's under a replaced `std` or `core` (the macro_use prelude is the sysroot's), so those calls keep their edges.
  * control_names: the real prelude, where every verdict stays what it was.

The rustc half runs only where a toolchain exists (like tests/test_rs_derive_scope_probes.py); the ripple half always runs and fails if ripple
never read a site it is about.
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
SCOPE = CRATES / "_prelude_replaced"

NAMES = ["Some", "drop", "Vec::new", "Default::default", "u8::from_str_radix", "S::to_string"]
UNRESOLVED_NAMES = {name: "unresolved" for name in NAMES}

# probe -> the verdict ripple must give each call written so (a call written so anywhere in the probe, and at least one)
EXPECT = {
    "std_dep_names": UNRESOLVED_NAMES,
    "prelude_import_names": UNRESOLVED_NAMES,
    "std_dep_test": {"helper": "unresolved"},
    "prelude_import_test": {"helper": "unresolved"},
    "lib_std_calls": {"std::mark": "unresolved", "::std::mark": "unresolved"},
    "lib_core_calls": {"core::mark": "unresolved"},
    "lib_alloc_calls": {"alloc::mark": "unresolved"},
    "lib_std_macro_path": {"helper": "unresolved"},
    "std_dep_macros": {"other::helper": "edge"},
    "core_dep_macros": {"other::helper": "edge"},
    # `#[macro_use] extern crate std;` where `std` is another crate binds that crate's macros: rustc runs the fake `println!` and `vec!`, which evaluate
    # nothing, so `other::helper` is never called (rustc warns it is unused). Every spelling: the plain one, a library called std, an alias, under
    # `#![no_std]` with a cfg (the common idiom), and under cfg_attr
    "macro_use_std": {"helper": "unresolved"},
    "macro_use_libstd": {"helper": "unresolved"},
    "macro_use_std_alias": {"helper": "unresolved"},
    "macro_use_std_cfg": {"helper": "unresolved"},
    "macro_use_std_cfg_attr": {"helper": "unresolved"},
    "macro_use_core": {"helper": "unresolved"},  # the same for `core` (fakecore's `matches!`; core_dep_macros is the control without the extern crate)
    # the controls: the real `std`, where the macros are the sysroot's whichever way it is brought in and the arguments are evaluated
    "macro_use_std_real": {"other::helper": "edge"},
    "macro_use_std_real_cfg": {"other::helper": "edge"},
    "control_names": {"Some": "ctor", "drop": "external", "Vec::new": "external", "Default::default": "external", "u8::from_str_radix": "external", "S::to_string": "external"},
}
FORBIDDEN = {"lib_std_macro_path": {("other::helper", "edge")}, **{name: {("helper", "edge"), ("other::helper", "edge")} for name in EXPECT if name.startswith("macro_use_") and "real" not in name}}  # the wrong answer, which the expectation above would not catch by itself
NIGHTLY = {"prelude_import_names", "prelude_import_test"}


def toolchain():
    root = Path(os.environ.get("RIPPLE_RUST_TOOLCHAIN", "/tmp/rust-toolchain"))
    return root if (root / "cargo" / "bin" / "cargo").is_file() and (root / "cargo" / "bin" / "rustc").is_file() else None


@pytest.fixture(scope="module")
def target(tmp_path_factory):
    return tmp_path_factory.mktemp("prelude-replaced-target")


def stage(name, tmp_path):
    shutil.copytree(CRATES / "_ext", tmp_path / "crates" / "_ext", ignore=shutil.ignore_patterns("target", "Cargo.lock"))
    shutil.copytree(SCOPE / name, tmp_path / "crates" / "_prelude_replaced" / name, ignore=shutil.ignore_patterns("target", "Cargo.lock"))
    return tmp_path / "crates"


def test_every_probe_of_the_directory_is_in_the_table():
    assert sorted(p.name for p in SCOPE.iterdir() if p.is_dir()) == sorted(EXPECT)


@pytest.mark.parametrize("name", sorted(EXPECT))
def test_rustc_says_so(name, target, tmp_path):
    root = toolchain()
    if root is None:
        pytest.skip("no Rust toolchain (set RIPPLE_RUST_TOOLCHAIN to a directory with cargo/bin)")
    crates = stage(name, tmp_path)
    env = {**os.environ, "CARGO_HOME": str(root / "cargo"), "RUSTUP_HOME": str(root / "rustup"), "CARGO_TARGET_DIR": str(target), "PATH": f"{root}/cargo/bin:" + os.environ["PATH"]}
    if name in NIGHTLY:
        env["RUSTC_BOOTSTRAP"] = "1"  # the probe uses an unstable feature
    run = subprocess.run(["cargo", "test", "--offline", "--quiet"], cwd=crates / "_prelude_replaced" / name, env=env, capture_output=True, text=True, timeout=600)
    out = run.stdout + run.stderr
    assert run.returncode == 0 and "test result: ok. 1 passed; 0 failed" in out, out[-1500:]


def verdicts(repo):
    """{call as written: {the kinds of verdict ripple gave it}}."""
    files, skipped = walk_languages(repo, [RUST])
    index = rs.read_repo(repo, files[RUST.name], skipped)[0]
    out: dict[str, set[str]] = {}
    for _, _, _, result, _, _, written, _ in index.resolved_sites():
        out.setdefault(written, set()).add(result[0])
    return out


@pytest.mark.parametrize("name", sorted(EXPECT))
def test_ripple_gives_each_call_the_verdict_the_compiler_allows(name):
    # each probe is a repo of its own: the crates it names by a path outside it (`_ext`) are outside the repo. Beside it they would be read,
    # and the macros they define (fakeprelude's println, fakecore's assert_eq) are macros of the repo, which ripple refuses by name wherever they are called
    got = verdicts(SCOPE / name)
    for written, kind in EXPECT[name].items():
        assert written in got, (name, written, sorted(got))  # ripple read the site
        assert got[written] == {kind}, (name, written, got[written])
    for written, kind in FORBIDDEN.get(name, ()):
        assert kind not in got.get(written, set()), (name, written, kind)
