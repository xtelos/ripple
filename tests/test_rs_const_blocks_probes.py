"""Every position of a block that the compiler gives a scope of its own, built by rustc, and what ripple does with the call inside it.

bench/fixtures/rust-probes/crates/_const_blocks holds one crate per position. Each has `mod other { pub const fn one() -> T { 1 } }` and
`use other::*;` at its root, and a block that declares `const fn one() -> T { 2 }` and calls it; its `#[test]` passes only when the block's own
`one` is what the call means (a name the block declares comes before a glob's). A call of `one` that ripple resolved to `other::one` is therefore wrong.
The rustc half runs only where a toolchain exists; the ripple half always runs.

  * ripple scopes the block (an edge to the block's own `one`): a `const` or `static` inside a function body, the length of an array type
    written in a `let`, the length of a repeat expression, a const generic argument, an inline `const { .. }` block;
  * ripple does not read the call at all (no site, so no verdict to be wrong): an enum discriminant (in a module or in a function body), an array
    length in a parameter type, a return type or a field type, a const generic default (they sit in a signature or a type, which is no body);
  * ripple scopes the block now (`impl_const`, `impl_const_trait`, `trait_const_default`, `impl_const_in_fn`): the initializer of an associated const
    of an impl or a trait, whose blocks were never collected, so the call went to the glob's `one`.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytest.importorskip("tree_sitter_rust")

from conftest import FIXTURES  # noqa: E402

from ripple import rs_indexer as rs  # noqa: E402
from ripple.indexer import build_graph, walk_languages  # noqa: E402
from ripple.profile import RUST  # noqa: E402

CRATES = FIXTURES / "rust-probes" / "crates"
SCOPE = CRATES / "_const_blocks"
SCOPED, NOT_READ = "scoped", "not read"

# probe -> what ripple does with the call of `one` in the block
EXPECT = {
    "impl_const": SCOPED,
    "impl_const_trait": SCOPED,
    "trait_const_default": SCOPED,
    "impl_const_in_fn": SCOPED,
    "fn_const": SCOPED,
    "fn_static": SCOPED,
    "array_len_let_type": SCOPED,
    "array_repeat_len": SCOPED,
    "const_generic_arg": SCOPED,
    "inline_const": SCOPED,
    "enum_discriminant": NOT_READ,
    "enum_discriminant_in_fn": NOT_READ,
    "array_len_param": NOT_READ,
    "array_len_return": NOT_READ,
    "array_len_field": NOT_READ,
    "const_generic_default": NOT_READ,
}


def toolchain():
    root = Path(os.environ.get("RIPPLE_RUST_TOOLCHAIN", "/tmp/rust-toolchain"))
    return root if (root / "cargo" / "bin" / "cargo").is_file() and (root / "cargo" / "bin" / "rustc").is_file() else None


@pytest.fixture(scope="module")
def target(tmp_path_factory):
    return tmp_path_factory.mktemp("const-blocks-target")


def test_every_probe_of_the_directory_is_in_the_table():
    assert sorted(p.name for p in SCOPE.iterdir() if p.is_dir()) == sorted(EXPECT)


@pytest.mark.parametrize("name", sorted(EXPECT))
def test_rustc_says_the_blocks_own_name_wins(name, target, tmp_path):
    root = toolchain()
    if root is None:
        pytest.skip("no Rust toolchain (set RIPPLE_RUST_TOOLCHAIN to a directory with cargo/bin)")
    shutil.copytree(SCOPE / name, tmp_path / name, ignore=shutil.ignore_patterns("target", "Cargo.lock"))
    env = {**os.environ, "CARGO_HOME": str(root / "cargo"), "RUSTUP_HOME": str(root / "rustup"), "CARGO_TARGET_DIR": str(target), "PATH": f"{root}/cargo/bin:" + os.environ["PATH"]}
    run = subprocess.run(["cargo", "test", "--offline", "--quiet"], cwd=tmp_path / name, env=env, capture_output=True, text=True, timeout=600)
    out = run.stdout + run.stderr
    assert run.returncode == 0 and "test result: ok. 1 passed; 0 failed" in out, out[-1500:]


@pytest.mark.parametrize("name", sorted(EXPECT))
def test_ripple_never_gives_the_call_the_globs_function_where_the_block_declares_its_own(name):
    repo = SCOPE / name
    graph = build_graph(repo, RUST)
    to_glob = [e for e in graph.edges if e.kind == "call" and e.callee == "src/lib:other::one"]
    assert to_glob == [], (name, to_glob)  # the wrong answer
    files, skipped = walk_languages(repo, [RUST])
    sites = [(result[0], result[1]) for _, _, _, result, _, _, written, _ in rs.read_repo(repo, files[RUST.name], skipped)[0].resolved_sites() if written == "one"]
    if EXPECT[name] == NOT_READ:
        assert sites == [], (name, sites)  # a position that is no body: ripple reads no call there, so there is nothing to be wrong
    else:
        # the scope is read: the one site is an edge, to the declaration inside the block (not `mod other`'s, which starts at byte 12)
        [(kind, callee)] = sites
        assert kind == "edge" and callee[1] != 12, (name, sites)
