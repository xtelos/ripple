"""What cargo does with a `[patch]` or a `[replace]`, and the one thing ripple says about the call into a dependency either may redirect.

bench/fixtures/rust-probes/crates/_patch_source holds one repo per shape. Each has a `foo` (or `bar`) in the repo that answers 1 and a fetched one
(a local git repository, `file://`, or a vendored crates.io) that answers 2, and a `#[test]` asserting which one ran. The test passes only when
cargo used the one the probe says it does (test_cargo_says_so), so the probes are the evidence of how much a manifest reader would have to follow:

  * url_dep_crates_io_patch, ws_url_dep_crates_io_patch: a `[patch.crates-io]` does not patch a git dependency (cargo: "patch was not used"),
    from a lone package and from a workspace member. url_dep_branch_patch: a git dependency on a branch is still the source the url names.
  * url_dep_version_patch, crates_io_patch_version_mismatch: the patch is of the dependency's source, but its version does not satisfy the
    requirement (`version = "1"`, 0.5.0 and 2.0.0): not used. git_any_pre: a git dependency with no version takes a pre-release patch.
  * ws_excluded_member: a root's patch reaches its member and not the package its `exclude` lists. ws_member_over_exclude,
    ws_member_in_excluded_dir: a package that `members` lists is a member whatever `exclude` says, so the patch does reach it.
    ws_member_own_patch, ws_member_over_exclude_own: a member's own `[patch]` is ignored.
  * config_patch: a `[patch]` in `.cargo/config.toml` applies to a build run from at or below the directory that holds `.cargo`, and only then.
    config_paths: so does a config's `paths = ["foo"]`, which is no `[patch]` and makes the registry's foo the repo's all the same.
  * lone_patch_reaches_path_dep: a lone package's patch reaches the dependency of its path dependency, when built from the lone package.
  * lock_pin, lock_pin_git_newer: a Cargo.lock that keeps the dependency at the version it was locked at leaves the patch unused;
    lock_pin_git: a patch at the locked version is used.
  * workspace_unread_root, above_root: the root whose patch reaches the member is outside the directory ripple is run on (named by
    `package.workspace`, or found above it).
  * replace_registry, replace_git: a `[replace]` entry, keyed by a registry package id (`foo:1.0.0`) and by a git one (`url#foo:1.0.0`).
  * patch_key_rename: a patch under another key with `package =` naming the package it patches.
  * lone_patch_applies_url, lone_patch_applies_crates_io: the controls, where the patch applies and the repo's package runs.

Ripple does not decide which of these cargo applies (Rule P, tests/test_rs_redirect.py): in every probe the call through the redirected
dependency is neither an edge nor external, it is unresolved, and the caller's package says so (PATCH_UNPROVEN). The rustc half runs only where a
toolchain and git exist (like tests/test_rs_prelude_replaced_probes.py); the ripple half always runs.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytest.importorskip("tree_sitter_rust")

from conftest import FIXTURES  # noqa: E402

from ripple import rs_indexer as rs  # noqa: E402
from ripple import rs_modules as rm  # noqa: E402
from ripple.indexer import walk_languages  # noqa: E402
from ripple.profile import RUST  # noqa: E402

PROBES = FIXTURES / "rust-probes" / "crates" / "_patch_source"
VENDORED = {"lone_patch_applies_crates_io", "crates_io_patch_version_mismatch", "lock_pin", "patch_key_rename", "replace_registry", "config_paths"}  # crates.io is replaced by a vendored directory with foo 1.0.0
RUN_IN = {  # the directories cargo is run from (default: the repo)
    "lone_patch_reaches_path_dep": ["a", "b"],
    "ws_excluded_member": [".", "ex"],
    "ws_member_over_exclude": [".", "ex"],
    "ws_member_over_exclude_own": [".", "ex"],
    "ws_member_in_excluded_dir": [".", "crates/a"],
    "workspace_unread_root": ["app"],
    "above_root": ["app"],
}
ANALYSE = {"workspace_unread_root": "app", "above_root": "app"}  # the directory ripple is run on (default: the repo)

# probe -> [(directory of the caller's package, the call as written)]: the call through the dependency a patch or replace may redirect
EXPECT = {
    "url_dep_crates_io_patch": [(".", "foo::f")],
    "url_dep_version_patch": [(".", "foo::f")],
    "ws_url_dep_crates_io_patch": [("app", "foo::f")],
    "ws_excluded_member": [("app", "foo::f"), ("ex", "foo::f")],
    "ws_member_own_patch": [("app", "foo::f")],
    "ws_member_over_exclude": [("ex", "foo::f")],
    "ws_member_over_exclude_own": [("ex", "foo::f")],
    "ws_member_in_excluded_dir": [("crates/a", "foo::f")],
    "config_patch": [(".", "foo::f")],
    "lone_patch_reaches_path_dep": [("b", "bar::f")],
    "lone_patch_applies_url": [(".", "foo::f")],
    "lone_patch_applies_crates_io": [(".", "foo::f")],
    "crates_io_patch_version_mismatch": [(".", "foo::f")],
    "url_dep_branch_patch": [(".", "foo::f")],
    "lock_pin": [(".", "foo::f")],
    "lock_pin_git_newer": [(".", "foo::f")],
    "lock_pin_git": [(".", "foo::f")],
    "git_any_pre": [(".", "foo::f")],
    "patch_key_rename": [(".", "foo::f")],
    "workspace_unread_root": [(".", "foo::f")],
    "above_root": [(".", "foo::f")],
    "replace_registry": [(".", "foo::f")],
    "replace_git": [(".", "foo::f")],
    "config_paths": [(".", "foo::f")],
}


def toolchain():
    root = Path(os.environ.get("RIPPLE_RUST_TOOLCHAIN", "/tmp/rust-toolchain"))
    ok = (root / "cargo" / "bin" / "cargo").is_file() and (root / "cargo" / "bin" / "rustc").is_file() and shutil.which("git")
    return root if ok else None


def run(cwd, *args):
    env = {**os.environ, "GIT_AUTHOR_NAME": "x", "GIT_AUTHOR_EMAIL": "x@y", "GIT_COMMITTER_NAME": "x", "GIT_COMMITTER_EMAIL": "x@y"}
    subprocess.run(args, cwd=cwd, check=True, capture_output=True, env=env)


def stage(name, tmp_path):
    """The probe as a repo in tmp_path/repo, with `@GIT@` the url of two local git repositories (`foo` and `bar`, each answering 2) and a vendored
    crates.io holding foo 1.0.0. Returns the repo."""
    for crate in ("foo", "bar"):
        src = tmp_path / "gitsrc" / crate
        (src / "src").mkdir(parents=True)
        (src / "Cargo.toml").write_text(f'[package]\nname = "{crate}"\nversion = "1.0.0"\nedition = "2021"\n')
        (src / "src" / "lib.rs").write_text("pub fn f() -> u32 { 2 }\n")
        if shutil.which("git"):
            run(src, "git", "init", "-q", "-b", "main")
            run(src, "git", "add", "Cargo.toml", "src")
            run(src, "git", "commit", "-qm", "init")
            run(src, "git", "branch", "dev")
    vendored = tmp_path / "vendor" / "foo"
    (vendored / "src").mkdir(parents=True)
    (vendored / "Cargo.toml").write_text('[package]\nname = "foo"\nversion = "1.0.0"\nedition = "2021"\n')
    (vendored / "src" / "lib.rs").write_text("pub fn f() -> u32 { 2 }\n")
    (vendored / ".cargo-checksum.json").write_text('{"files":{},"package":null}')
    repo = tmp_path / "repo"
    shutil.copytree(PROBES / name, repo)
    for path in repo.rglob("*"):
        if path.is_file() and path.suffix in (".toml", ".rs"):
            path.write_text(path.read_text().replace("@GIT@", f"file://{tmp_path}/gitsrc"))
    return repo


def cargo(root, tmp_path, cwd, name, *args):
    env = {**os.environ, "CARGO_HOME": str(tmp_path / "cargo-home"), "RUSTUP_HOME": str(root / "rustup"), "CARGO_TARGET_DIR": str(tmp_path / "target"), "PATH": f"{root}/cargo/bin:" + os.environ["PATH"]}
    full = ["cargo", *args]
    if name in VENDORED:
        full += ["--config", 'source.crates-io.replace-with="vend"', "--config", f'source.vend.directory="{tmp_path}/vendor"']
    return subprocess.run(full, cwd=cwd, env=env, capture_output=True, text=True, timeout=600)


def pin(root, repo, tmp_path, name):
    """Cargo.pin.toml is the manifest before the patch was added: lock with it, then put the manifest with the patch back, so the Cargo.lock keeps
    the dependency where it was (lock_pin, lock_pin_git, lock_pin_git_newer)."""
    pinned = repo / "Cargo.pin.toml"
    if pinned.is_file():
        final = (repo / "Cargo.toml").read_text()
        (repo / "Cargo.toml").write_text(pinned.read_text())
        result = cargo(root, tmp_path, repo, name, "generate-lockfile")
        assert result.returncode == 0, result.stderr[-1500:]
        (repo / "Cargo.toml").write_text(final)


def verdicts(repo):
    """{(caller's file, call as written): {the (verdict, reason) ripple gave it}} for the calls of the probes."""
    files, skipped = walk_languages(repo, [RUST])
    index = rs.read_repo(repo, files[RUST.name], skipped)[0]
    out: dict[tuple[str, str], set[tuple[str, str]]] = {}
    for _, _, _, result, caller, _, written, _ in index.resolved_sites():
        cid = rs.module_id(caller[0]) if caller[1] == -1 else index.final[caller]
        got = ("edge", index.final[result[1]]) if result[0] == "edge" else (result[0], result[1] if isinstance(result[1], str) else "")
        out.setdefault((cid, written), set()).add(got)
    return out


def test_every_probe_of_the_directory_is_in_the_table():
    assert sorted(p.name for p in PROBES.iterdir() if p.is_dir()) == sorted(EXPECT)


@pytest.mark.parametrize("name", sorted(EXPECT))
def test_cargo_says_so(name, tmp_path):
    root = toolchain()
    if root is None:
        pytest.skip("no Rust toolchain or no git (set RIPPLE_RUST_TOOLCHAIN to a directory with cargo/bin)")
    repo = stage(name, tmp_path)
    pin(root, repo, tmp_path, name)
    for directory in RUN_IN.get(name, ["."]):
        result = cargo(root, tmp_path, repo / directory, name, "test", "--quiet")
        out = result.stdout + result.stderr
        assert result.returncode == 0 and "test result: ok. 1 passed; 0 failed" in out, (directory, out[-1500:])


def test_cargo_reads_a_config_patch_only_from_at_or_below_its_directory(tmp_path):
    """The control for config_patch: the same repo built from the directory above it, where `.cargo/config.toml` is not read, runs the fetched foo."""
    root = toolchain()
    if root is None:
        pytest.skip("no Rust toolchain or no git (set RIPPLE_RUST_TOOLCHAIN to a directory with cargo/bin)")
    repo = stage("config_patch", tmp_path)
    result = cargo(root, tmp_path, tmp_path, "config_patch", "test", "--quiet", "--manifest-path", str(repo / "Cargo.toml"))
    assert result.returncode != 0 and "left: 2" in result.stdout + result.stderr and "right: 1" in result.stdout + result.stderr


@pytest.mark.parametrize("name", sorted(EXPECT))
def test_ripple_says_neither_edge_nor_external_for_the_call_through_a_dependency_a_patch_or_replace_may_redirect(name, tmp_path):
    repo = stage(name, tmp_path) / ANALYSE.get(name, "")
    got = verdicts(repo)
    files, _ = walk_languages(repo, [RUST])
    workspace = rm.load_workspace(repo, files[RUST.name])
    for directory, written in EXPECT[name]:
        callers = {key for key in got if key[1] == written and key[0].startswith(f"{directory}/src/" if directory != "." else "src/")}
        assert callers, (name, directory, written, sorted(got))  # ripple read the site
        for key in callers:
            assert {kind for kind, _ in got[key]} == {"unresolved"}, (name, key, got[key])
        assert rm.PATCH_UNPROVEN in workspace.packages["" if directory == "." else directory].deps.values(), (name, directory)
