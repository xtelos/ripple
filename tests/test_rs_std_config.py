"""Which cargo config files make `std` another crate, a question of its own (rs_modules.STD_CONFIG_KEYS).

The trust proof for `tokio` and `serial_test` refuses every config key it does not know (rs_modules.CONFIG_KEYS): it has to show that a
dependency is crates.io's own. The question here is smaller: can the config make the crate's `std`, `core` or `alloc` another crate, or
`rustc` another compiler? `[alias]` cannot shadow `build`, `test` or `check`, `[source]` and `[registries]` only change where registry
and git dependencies come from (an assumption the README states about what they are called), and a `[profile]` sets how code is built
unless it sets rustflags (unstable). Refusing them unresolved every prelude name of a repo with an `xtask` alias or a `cargo vendor`
config (bench/fixtures/rust/warehouse-rs: 55 edges and 8 externals, down to 41 and 0).
"""

import shutil
from collections import Counter

import pytest

pytest.importorskip("tree_sitter_rust")

from conftest import FIXTURES  # noqa: E402

from ripple import rs_indexer as rs  # noqa: E402
from ripple import rs_modules  # noqa: E402
from ripple.indexer import walk_languages  # noqa: E402
from ripple.profile import RUST  # noqa: E402

WAREHOUSE = FIXTURES / "rust" / "warehouse-rs"

# what changes nothing about std
HARMLESS = {
    "alias": '[alias]\nxtask = "run --package xtask --"\n',
    "alias_with_config": '[alias]\nt = "test --config patch"\n',
    "vendor": '[source.crates-io]\nreplace-with = "vendored-sources"\n\n[source.vendored-sources]\ndirectory = "vendor"\n',
    "source_git": '[source."https://example.invalid/x"]\ngit = "https://example.invalid/x"\nreplace-with = "v"\n',
    "registries": '[registries.corp]\nindex = "https://example.invalid/index"\n',
    "registry_default": '[registry]\ndefault = "corp"\n',
    "profile": "[profile.dev]\nopt-level = 1\n\n[profile.release.package.foo]\nopt-level = 3\n",
    "codegen_and_resolver": '[build]\nrustflags = ["-C", "target-cpu=native"]\n\n[resolver]\nincompatible-rust-versions = "fallback"\n',
}
# what can: each is refused, whatever else the file holds
HARMFUL = {
    "patch": '[patch.crates-io]\nfoo = { path = "foo" }\n',
    "paths": 'paths = ["../std"]\n',
    "include": 'include = ["more.toml"]\n',
    "unstable": '[unstable]\nbuild-std = ["core"]\n',
    "env_rustc": '[env]\nRUSTC = "my-rustc"\n',
    "env_rustflags": '[env]\nRUSTFLAGS = "--extern std=/x"\n',
    "rustflags_extern": '[build]\nrustflags = ["--extern", "std=/x/libfake.rlib"]\n',
    "rustflags_sysroot": '[target.x86_64-unknown-linux-gnu]\nrustflags = ["--sysroot", "/x"]\n',
    "profile_rustflags": '[profile.dev]\nrustflags = ["--extern", "std=/x"]\n',
    "profile_package_rustflags": '[profile.dev.package."*"]\nrustflags = ["-Zfoo"]\n',
    "profile_build_override_rustflags": '[profile.dev.build-override]\nrustflags = ["--sysroot=/x"]\n',
    "profile_not_a_table": "profile = 1\n",
    "build_rustc": '[build]\nrustc = "my-rustc"\n',
    "build_rustc_wrapper": '[build]\nrustc-wrapper = "wrap"\n',
    "an_unknown_key": "frobnicate = 1\n",
    "unreadable": "this is [not toml\n",
    "alias_beside_a_patch": '[alias]\nxtask = "run"\n\n[patch.crates-io]\nfoo = { path = "foo" }\n',
}


def workspace(tmp_path, config):
    """The warehouse fixture, with a `.cargo/config.toml` of `config` at its root (none for None)."""
    repo = tmp_path / "warehouse-rs"
    shutil.copytree(WAREHOUSE, repo, ignore=shutil.ignore_patterns("target", "Cargo.lock"))
    if config is not None:
        (repo / ".cargo").mkdir(exist_ok=True)
        (repo / ".cargo" / "config.toml").write_text(config)
    return repo


def kinds(repo):
    files, skipped = walk_languages(repo, [RUST])
    index = rs.read_repo(repo, files[RUST.name], skipped)[0]
    return Counter(site[3][0] for site in index.resolved_sites())


def std_why(tmp_path, config):
    repo = workspace(tmp_path, config)
    files, _ = walk_languages(repo, [RUST])
    ws = rs_modules.load_workspace(repo, files[RUST.name])
    return {p.std_why for p in ws.packages.values()}


def test_the_baseline_the_configs_are_held_against():
    got = kinds(WAREHOUSE)
    assert got["edge"] == 55 and got["external"] == 8, got


@pytest.mark.parametrize("name", HARMLESS)
def test_a_config_that_cannot_change_std_leaves_the_warehouse_as_it_reads_with_none(tmp_path, name):
    assert kinds(workspace(tmp_path, HARMLESS[name])) == kinds(WAREHOUSE)
    assert std_why(tmp_path / "again", HARMLESS[name]) == {None}


@pytest.mark.parametrize("name", HARMFUL)
def test_a_config_that_can_change_std_marks_every_package(tmp_path, name):
    assert None not in std_why(tmp_path, HARMFUL[name])
    got = kinds(workspace(tmp_path / "again", HARMFUL[name]))
    assert got["edge"] < 55 and got["external"] < 8, got


def test_the_trust_proof_keeps_its_own_stricter_set(tmp_path):
    """An alias, a source and a profile do not mark std, and still make a tokio dependency untrusted (tests/test_rs_attr_trust_config.py)."""
    for name in ("alias", "vendor", "registries", "profile"):
        repo = workspace(tmp_path / name, HARMLESS[name])
        files, _ = walk_languages(repo, [RUST])
        for package in rs_modules.load_workspace(repo, files[RUST.name]).packages.values():
            assert package.std_why is None
        assert rs_modules._config_problem(repo) is not None, name
