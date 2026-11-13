"""The claims the trusted-derive rules rest on, built by rustc with local proc-macro crates (bench/fixtures/rust-probes/crates/_derive_trust).

`pm` holds derives that have the shape of what the published ones expand to (a named unit const, an unnamed one, an inherent
`serialize`, an `impl clap::Tr for T`, an `impl std::fmt::Display for T`), a derive that declares the helper `command` and an
attribute macro called `command`; `fakeserde` is serde without its `derive` feature (a trait and no derive) and `reexp` re-exports a
derive called `Serialize`. Each bin of `u` is one claim, and what rustc does with it is pinned here. The rules and where they are tested:

  * p1a, p1b, p1c: the const serde_derive up to 1.0.162 names `_IMPL_SERIALIZE_FOR_<T>` enters the scope and shadows a glob's
    function, and a call of it is E0618 (so it is never a call target that builds); the unnamed one of 1.0.163 on enters nothing
    (tests/test_rs_derive_trust_rules.py: the placeholder);
  * p2: an inherent `serialize` from `#[serde(remote = ..)]` beats a repo trait impl of the name (the DERIVE_FNS pin);
  * p3a to p3d: a helper a derive declares beats an attribute macro imported by the same name when written after it, and is
    ambiguous with it (E0659) when written before; with the import in another module the helper is used (the helper rule);
  * p4, p5: a `clap::` or `std::` path in a derive's output is resolved where the derive is written, so a local `mod clap` or `mod std`
    makes the impl one of the repo's trait (the clap and thiserror site checks);
  * p6: with the crate's `derive` feature off `use serde::Serialize;` gives only the trait, and a glob that brings another derive of the
    name supplies it (the no-glob rule).

The rustc half runs only where a toolchain exists ($RIPPLE_RUST_TOOLCHAIN or /tmp/rust-toolchain, offline: every crate is a path).
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from conftest import FIXTURES

TRUST = FIXTURES / "rust-probes" / "crates" / "_derive_trust"

# bin -> (builds, a fragment of what it prints on stdout when it runs or of the compiler's output when it does not)
OUTCOME = {
    "p1a_named_const_call": (False, "error[E0618]: expected function, found `()`"),
    "p1b_named_const_value": (True, "value is the generated const: ()"),
    "p1c_underscore_const_call": (True, "glob fn ran: 1"),
    "p2_remote_inherent": (True, "inherent from the derive"),
    "p3a_helper_vs_import": (True, "warning: unused import: `pm::command`"),
    "p3b_helper_import_elsewhere": (False, "error[E0425]: cannot find function `injected_by_command` in this scope"),
    "p3c_macro_without_helper": (True, ""),
    "p3d_helper_before_derive": (False, "error[E0659]: `command` is ambiguous"),
    "p4_rel_clap": (True, "local clap module"),
    "p5_rel_std": (True, "S implements the local std::fmt::Display"),
    "p6_trait_import_glob_derive": (True, "fake derive"),
}


def toolchain():
    root = Path(os.environ.get("RIPPLE_RUST_TOOLCHAIN", "/tmp/rust-toolchain"))
    return root if (root / "cargo" / "bin" / "cargo").is_file() and (root / "cargo" / "bin" / "rustc").is_file() else None


@pytest.fixture(scope="module")
def workspace(tmp_path_factory):
    root = tmp_path_factory.mktemp("derive-trust")
    shutil.copytree(TRUST, root / "ws", ignore=shutil.ignore_patterns("target", "Cargo.lock"))
    return root


def test_every_bin_of_the_directory_is_in_the_table():
    assert sorted(p.stem for p in (TRUST / "u" / "src" / "bin").glob("*.rs")) == sorted(OUTCOME)


@pytest.mark.parametrize("name", sorted(OUTCOME))
def test_rustc_says_so(name, workspace):
    root = toolchain()
    if root is None:
        pytest.skip("no Rust toolchain (set RIPPLE_RUST_TOOLCHAIN to a directory with cargo/bin)")
    env = {**os.environ, "CARGO_HOME": str(root / "cargo"), "RUSTUP_HOME": str(root / "rustup"), "CARGO_TARGET_DIR": str(workspace / "target"), "PATH": f"{root}/cargo/bin:" + os.environ["PATH"]}
    run = subprocess.run(
        ["cargo", "run", "--offline", "--quiet", "--manifest-path", str(workspace / "ws" / "Cargo.toml"), "-p", "u", "--bin", name],
        env=env, capture_output=True, text=True, timeout=600,
    )
    builds, fragment = OUTCOME[name]
    out = run.stdout + run.stderr
    assert (run.returncode == 0) == builds, out[-1500:]
    assert fragment in out, out[-1500:]


# ---- a library of the repo called like a crate (crates/_derive_trust_lib): which one rustc links
#
# `real` is an rlib called thing, `real_pm` a proc-macro crate called thing2 (as serde_derive is). Beside each a package of the repo whose
# `[lib] name` is the same: an rlib, or a proc-macro crate, or an rlib that re-exports another derive.
LIB = FIXTURES / "rust-probes" / "crates" / "_derive_trust_lib"
LIB_OUTCOME = {
    "app1": (False, "error[E0464]: multiple candidates for `rlib` dependency `thing` found"),  # rlib and rlib: no program
    "app2": (True, "real"),  # an rlib and a proc-macro crate of the name: the rlib is taken
    "app3": (True, "the shim's derive"),  # a proc-macro crate and an rlib of the name: the rlib is taken, so the repo's library captures `serde_derive`
    # round 3: only the name the app imports the derive through can be captured. `facade` is what serde is to serde_derive (an rlib that
    # re-exports the derive of the proc-macro crate it was built against), so the app that imports through it gets that crate's, whether
    # the real proc-macro crate is also a dependency of the app (app4) or not (app5), with a library of its name beside it
    "app4": (True, "the real derive"),
    "app5": (True, "the real derive"),
    # and a library of the facade's own name that the app reaches under another key (a renamed dependency) is no `--extern thing=`
    "app6": (True, "real"),
    "app7": (True, "real"),
}


@pytest.mark.parametrize("app", sorted(LIB_OUTCOME))
def test_rustc_links_a_library_of_the_repo_over_a_proc_macro_crate_of_its_name_and_never_beside_an_rlib(app, tmp_path):
    root = toolchain()
    if root is None:
        pytest.skip("no Rust toolchain (set RIPPLE_RUST_TOOLCHAIN to a directory with cargo/bin)")
    shutil.copytree(LIB, tmp_path / "ws", ignore=shutil.ignore_patterns("target", "Cargo.lock"))
    env = {**os.environ, "CARGO_HOME": str(root / "cargo"), "RUSTUP_HOME": str(root / "rustup"), "CARGO_TARGET_DIR": str(tmp_path / "target"), "PATH": f"{root}/cargo/bin:" + os.environ["PATH"]}
    run = subprocess.run(["cargo", "run", "--offline", "--quiet", "--manifest-path", str(tmp_path / "ws" / "Cargo.toml"), "-p", app], env=env, capture_output=True, text=True, timeout=600)
    builds, fragment = LIB_OUTCOME[app]
    out = run.stdout + run.stderr
    assert (run.returncode == 0) == builds, out[-1500:]
    assert fragment in out, out[-1500:]
