"""The redirects of cargo that name a path dependency, a package below a directory, or an included file: built with cargo, then read by ripple.

bench/fixtures/rust-probes/crates/_redirect_reach holds one repo per shape, each with a `#[test]` asserting which `foo` cargo built (the repo's
own, a redirect's, the fetched one), as tests/test_rs_patch_probes.py does for `[patch]` and `[replace]` of a registry or git dependency. The
cargo half passes only when cargo does what the probe says (test_cargo_says_so); the ripple half always runs. The probes:

  * path_dep_paths_override, path_dep_paths_below: a config's `paths` entry replaces a `path` dependency whose package is at or below it.
  * path_dep_config_patch, path_dep_manifest_patch: a `[patch."file:///<the dependency's directory>"]` table redirects a `path` dependency.
  * path_dep_replace_file_id: a `[replace]` keyed by the file url of a path package, with a source of its own (a path replacement collides with
    the replaced package in the lock file and never builds), is applied to a `path` dependency.
  * path_dep_config_include: the `paths` of an included file is applied to a `path` dependency.
  * the controls, where cargo builds the path as written and ripple draws the edge: a `[patch.crates-io]`, a `[patch]` of an https url, and a
    `[replace]` of `foo:1.0.0` (crates.io's package, which a path dependency is not); and `paths = []`, which is no entry.
  * paths_below: a `paths` entry loads every package below its directory.
  * paths_nested_pathdep_registry, paths_nested_pathdep_over_path, paths_transitive_devdep_over_path: a `paths` entry also loads the path
    dependencies of every package it holds (a registry dependency becomes the repo's package that such a path names, a path dependency is
    overridden by name by a package outside the entry, and the reach is transitive and covers `[dev-dependencies]`), so ripple does not follow
    an entry: any entry refuses every dependency.
  * config_include, config_include_tables: the `[patch]` of a file a config `include`s (an array of paths, `[[include]]` tables) is applied.
"""

import pytest

pytest.importorskip("tree_sitter_rust")

from test_rs_patch_probes import cargo, stage, toolchain, verdicts  # noqa: E402

from conftest import FIXTURES  # noqa: E402
from ripple import rs_modules as rm  # noqa: E402
from ripple.indexer import walk_languages  # noqa: E402
from ripple.profile import RUST  # noqa: E402

PROBES = FIXTURES / "rust-probes" / "crates" / "_redirect_reach"
VENDORED = {"paths_below", "paths_nested_pathdep_registry", "config_include", "config_include_tables"}  # `foo = "1"` is the vendored crates.io's
CALLS = {"paths_nested_pathdep_over_path": "loc::f", "paths_transitive_devdep_over_path": "loc::f"}  # the call through the dependency (default `foo::f`)
UNRESOLVED = {  # probe -> what ripple says of the call through `foo`: neither an edge nor external
    "path_dep_paths_override",
    "path_dep_paths_below",
    "path_dep_config_patch",
    "path_dep_manifest_patch",
    "path_dep_replace_file_id",
    "path_dep_config_include",
    "paths_below",
    "paths_nested_pathdep_registry",
    "paths_nested_pathdep_over_path",
    "paths_transitive_devdep_over_path",
    "config_include",
    "config_include_tables",
}
CONTROLS = {  # probe -> the one verdict ripple gives the call, where nothing of the probe reaches the dependency
    "path_dep_patch_crates_io": ("edge", "foo/src/lib:f"),
    "path_dep_patch_https_url": ("edge", "foo/src/lib:f"),
    "path_dep_replace_crates_io_id": ("edge", "foo/src/lib:f"),
    "paths_empty": ("edge", "foo/src/lib:f"),
}


def test_every_probe_of_the_directory_is_in_a_table():
    assert sorted(p.name for p in PROBES.iterdir() if p.is_dir()) == sorted(UNRESOLVED | CONTROLS.keys())


@pytest.mark.parametrize("name", sorted(UNRESOLVED | CONTROLS.keys()))
def test_cargo_says_so(name, tmp_path):
    root = toolchain()
    if root is None:
        pytest.skip("no Rust toolchain or no git (set RIPPLE_RUST_TOOLCHAIN to a directory with cargo/bin)")
    repo = stage(name, tmp_path, PROBES)
    result = cargo(root, tmp_path, repo, name, "test", "--quiet", vendored=VENDORED)
    out = result.stdout + result.stderr
    assert result.returncode == 0 and "test result: ok. 1 passed; 0 failed" in out, out[-1500:]


@pytest.mark.parametrize("name", sorted(UNRESOLVED))
def test_ripple_says_neither_edge_nor_external_for_the_call_through_a_dependency_a_redirect_may_make_another_package(name, tmp_path):
    repo = stage(name, tmp_path, PROBES)
    got = verdicts(repo)
    callers = {key for key in got if key[1] == CALLS.get(name, "foo::f") and key[0].startswith("src/")}
    assert callers, (name, sorted(got))  # ripple read the site
    for key in callers:
        assert {kind for kind, _ in got[key]} == {"unresolved"}, (name, key, got[key])
    files, _ = walk_languages(repo, [RUST])
    assert rm.PATCH_UNPROVEN in rm.load_workspace(repo, files[RUST.name]).packages[""].deps.values()


@pytest.mark.parametrize("name", sorted(CONTROLS))
def test_ripple_keeps_the_verdict_of_a_control(name, tmp_path):
    repo = stage(name, tmp_path, PROBES)
    got = verdicts(repo)
    callers = {key for key in got if key[1] == "foo::f" and key[0].startswith("src/")}
    assert callers, (name, sorted(got))
    for key in callers:
        assert got[key] == {CONTROLS[name]}, (name, key, got[key])
