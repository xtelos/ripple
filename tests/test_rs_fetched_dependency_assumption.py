"""The one assumption ripple makes about a dependency it does not read: a fetched dependency does not name its library `std`, `core` or `alloc`.

A path dependency outside the analysed directory has its Cargo.toml read for its library's name (tests/test_rs_outside_path_deps.py). A registry or
a git dependency is not fetched and not read, so ripple cannot know its library's name, and marking every package that has one would unresolve the
prelude names of almost every repo. The README states the assumption beside the RUSTFLAGS one (the list of what ripple cannot prove). This pins
what it costs when it is false, with the compiler's side of it: a git dependency whose `[lib]` is named `std` builds and its fake prelude runs, and
ripple, which assumed an ordinary name, answers with the standard library's verdicts. These verdicts are WRONG, and are pinned as the documented
price of the assumption (a test that fails the day the assumption is dropped), not as a claim that they are right. The key `std`, a dependency's
`package = "rustc-std-workspace-*"` and a path dependency still mark (tests/test_rs_prelude_replaced.py).

bench/fixtures/rust-probes/crates/_assumed/fetched_lib_std: the git package gstd (its library is the fake prelude of _ext/fakeprelude, named std).
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
README = Path(__file__).resolve().parent.parent / "README.md"
ASSUMPTION = "does not name its library `std`, `core` or `alloc`"


def toolchain():
    root = Path(os.environ.get("RIPPLE_RUST_TOOLCHAIN", "/tmp/rust-toolchain"))
    ok = (root / "cargo" / "bin" / "cargo").is_file() and (root / "cargo" / "bin" / "rustc").is_file() and shutil.which("git")
    return root if ok else None


def stage(tmp_path):
    """The probe as a repo, with `@GIT@` the url of a local git repository holding gstd: the fake prelude, as a library called std."""
    src = tmp_path / "gitsrc" / "gstd"
    (src / "src").mkdir(parents=True)
    (src / "Cargo.toml").write_text('[package]\nname = "gstd"\nversion = "0.1.0"\nedition = "2021"\n\n[lib]\nname = "std"\n')
    shutil.copy(CRATES / "_ext" / "fakeprelude" / "src" / "lib.rs", src / "src" / "lib.rs")
    if shutil.which("git"):
        env = {**os.environ, "GIT_AUTHOR_NAME": "x", "GIT_AUTHOR_EMAIL": "x@y", "GIT_COMMITTER_NAME": "x", "GIT_COMMITTER_EMAIL": "x@y"}
        for args in (("init", "-q", "-b", "main"), ("add", "Cargo.toml", "src"), ("commit", "-qm", "init")):
            subprocess.run(["git", *args], cwd=src, check=True, capture_output=True, env=env)
    repo = tmp_path / "repo"
    shutil.copytree(CRATES / "_assumed" / "fetched_lib_std", repo)
    manifest = repo / "Cargo.toml"
    manifest.write_text(manifest.read_text().replace("@GIT@", f"file://{tmp_path}/gitsrc"))
    return repo


def test_the_compiler_runs_the_fetched_dependencys_prelude(tmp_path):
    root = toolchain()
    if root is None:
        pytest.skip("no Rust toolchain or no git (set RIPPLE_RUST_TOOLCHAIN to a directory with cargo/bin)")
    repo = stage(tmp_path)
    env = {**os.environ, "CARGO_HOME": str(tmp_path / "cargo-home"), "RUSTUP_HOME": str(root / "rustup"), "CARGO_TARGET_DIR": str(tmp_path / "target"), "PATH": f"{root}/cargo/bin:" + os.environ["PATH"]}
    run = subprocess.run(["cargo", "test", "--quiet"], cwd=repo, env=env, capture_output=True, text=True, timeout=600)
    assert run.returncode == 0 and "test result: ok. 1 passed; 0 failed" in run.stdout + run.stderr, (run.stdout + run.stderr)[-1500:]


def test_ripple_takes_the_standard_libraries_verdicts_for_a_name_the_fetched_library_replaced(tmp_path):
    """The documented price: each of these is the fake's function in rustc (the test above), and the standard library's in ripple."""
    repo = stage(tmp_path)
    files, skipped = walk_languages(repo, [RUST])
    index = rs.read_repo(repo, files[RUST.name], skipped)[0]
    got = {written: result[0] for _, _, _, result, _, _, written, _ in index.resolved_sites()}
    assert {k: got[k] for k in ("Some", "drop", "Vec::new", "u8::from_str_radix")} == {"Some": "ctor", "drop": "external", "Vec::new": "external", "u8::from_str_radix": "external"}
    ctx = next(c for c in index.ctxs if c.crate.kind == "lib")
    assert not index.prelude_replaced(ctx) and index.foreign_crates(ctx) == frozenset()


def test_the_readme_states_the_assumption_in_the_list_of_what_ripple_cannot_prove():
    text = README.read_text()
    assert ASSUMPTION in text
    cannot = text.index("What ripple cannot prove, and so assumes:")
    assert cannot < text.index(ASSUMPTION) < text.index("\n## Java\n", cannot)  # beside the RUSTFLAGS one, not in the sentence that lists what ripple reads
    assert "RUSTFLAGS" in text[cannot : text.index("\n## Java\n", cannot)]
    assert "_assumed/fetched_lib_std" in text


def test_the_assumption_does_not_cover_the_key_or_a_path_dependency(make_repo):
    """The other half of the line the README draws: a key called std, or a path dependency whose manifest says std, still marks."""
    for dep in ('std = { git = "https://example.invalid/x" }\n', 'gstd = { path = "../gstd" }\n'):
        files = {"app/Cargo.toml": '[package]\nname = "app"\nversion = "0.1.0"\nedition = "2021"\n[dependencies]\n' + dep, "app/src/lib.rs": "pub fn f() { let _v = Vec::new(); }\n"}
        if "path" in dep:
            files["gstd/Cargo.toml"] = '[package]\nname = "gstd"\nversion = "0.1.0"\n[lib]\nname = "std"\n'
            files["gstd/src/lib.rs"] = ""
        repo = make_repo(files) / "app"
        sources, skipped = walk_languages(repo, [RUST])
        index = rs.read_repo(repo, sources[RUST.name], skipped)[0]
        assert index.prelude_replaced(next(c for c in index.ctxs if c.crate.kind == "lib")), dep
