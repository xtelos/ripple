"""Rule P, how far it reaches: the spellings of a redirect cargo accepts that name a PATH dependency, an included file, or a `paths` entry (the
probes in bench/fixtures/rust-probes/crates/_redirect_reach are built with cargo by tests/test_rs_redirect_probes.py; the cargo facts each test
leans on are in its docstring).

A redirect ripple can see but not fully read refuses: the dependency is PATCH_UNPROVEN (never an edge, never external). Where a redirect can
only name a package ripple reads, the dependency is refused when that package's name is the dependency's. A `paths` entry is the one redirect
that is not followed at all: cargo loads every package below it and the path dependencies of each, transitively, wherever they point, and
overrides by name, so any entry refuses every dependency of every package. Each test here fails when the rule it names is removed.
"""

import pytest

pytest.importorskip("tree_sitter_rust")

from test_rs_redirect import OUT, UNPROVEN, pkg  # noqa: E402

from ripple import rs_modules as rm  # noqa: E402
from ripple.cache import fingerprint  # noqa: E402
from ripple.profile import RUST  # noqa: E402


@pytest.fixture
def deps(tmp_path_factory):
    """deps(files, package="") -> the `deps` of that package of the repo the files make, each call in a directory of its own."""

    def run(files, package=""):
        repo = tmp_path_factory.mktemp("repo")
        for rel, text in files.items():
            (repo / rel).parent.mkdir(parents=True, exist_ok=True)
            (repo / rel).write_text(text)
        return rm.load_workspace(repo, sorted(repo.rglob("*.rs"))).packages[package].deps

    return run


def loc_repo(manifest="", **more):
    """A lone package `p` with the path dependency `loc = { path = "loc" }` (and what `manifest` adds to its Cargo.toml), the package `loc` at
    loc/ and another package called `loc` at alt/."""
    return {
        "Cargo.toml": pkg("p", '[dependencies]\nloc = { path = "loc" }\n' + manifest),
        "src/lib.rs": "",
        "loc/Cargo.toml": pkg("loc"),
        "loc/src/lib.rs": "",
        "alt/Cargo.toml": pkg("loc"),
        "alt/src/lib.rs": "",
        **more,
    }


def foo_repo(**more):
    """A lone package `p` with `foo = "1"` and `serde = "1"` from the registry, and no package called either in the repo."""
    return {"Cargo.toml": pkg("p", '[dependencies]\nfoo = "1"\nserde = "1"\n'), "src/lib.rs": "", **more}


EDGE = {"loc": "loc"}


# ---- gap 1: a path dependency, redirected by a `paths` entry, a `[patch]` table of a file url, a `[replace]` of a file url


def test_a_path_dependency_is_an_edge_when_nothing_names_it(deps):
    assert deps(loc_repo()) == EDGE


def test_a_config_paths_entry_that_holds_the_package_of_a_path_dependencys_name_makes_it_unresolved(deps):
    """cargo built (probes path_dep_paths_override, path_dep_paths_below): the path dependency is the package the entry holds, `left: 1`."""
    assert deps(loc_repo(**{".cargo/config.toml": 'paths = ["alt"]\n'})) == {"loc": UNPROVEN}
    below = loc_repo(**{".cargo/config.toml": 'paths = ["third"]\n', "third/x/y/Cargo.toml": pkg("loc"), "third/x/y/src/lib.rs": ""})
    assert deps(below) == {"loc": UNPROVEN}


@pytest.mark.parametrize(
    "table",
    ['[patch."file:///anywhere/loc"]\nloc = { path = "alt" }\n', '[patch]\n"file:///anywhere/loc" = { loc = { path = "alt" } }\n', '[patch.myreg]\nloc = { path = "alt" }\n'],
    ids=["file_url", "inline_url_key", "registry_name"],
)
@pytest.mark.parametrize("where", ["manifest", "config"])
def test_a_patch_table_that_may_be_the_url_of_a_path_source_names_the_path_dependency(deps, table, where):
    """cargo built (probes path_dep_manifest_patch, path_dep_config_patch): `[patch."file:///<the path dependency's directory>"]` redirects a path
    dependency, from the manifest and from a config. A registry name is no url ripple can read (`[registries]` may be anywhere), so it counts."""
    files = loc_repo(table if where == "manifest" else "")
    if where == "config":
        files[".cargo/config.toml"] = table
    assert deps(files) == {"loc": UNPROVEN}


@pytest.mark.parametrize("key", ["crates-io", "https://github.com/x/loc", "http://x/loc", "ssh://git@x/loc", "git://x/loc"])
def test_a_patch_table_that_cannot_be_the_url_of_a_path_source_leaves_the_path_dependency_an_edge(deps, key):
    """cargo built (probe path_dep_patch_crates_io): a `[patch.crates-io]` and a patch of an https url are unused for a path dependency, `left: 3`."""
    assert deps(loc_repo(f'\n[patch."{key}"]\nloc = {{ path = "alt" }}\n')) == EDGE


def test_a_file_url_patch_of_another_name_leaves_the_path_dependency_an_edge(deps):
    assert deps(loc_repo('\n[patch."file:///anywhere/loc"]\nother = { path = "alt" }\n')) == EDGE


def test_a_file_url_patch_names_the_path_dependency_by_its_package_whatever_its_key(deps):
    files = loc_repo('\n[patch."file:///anywhere/loc"]\nloc = { path = "alt" }\n')
    files["Cargo.toml"] = pkg("p", '[dependencies]\nother = { path = "loc", package = "loc" }\n\n[patch."file:///anywhere/loc"]\nloc = { path = "alt" }\n')
    assert deps(files) == {"other": UNPROVEN}


def test_a_workspace_inherited_path_dependency_is_judged_the_same(deps):
    files = {
        "Cargo.toml": '[workspace]\nmembers = ["app"]\n\n[workspace.dependencies]\nloc = { path = "../loc" }\n',
        "app/Cargo.toml": pkg("app", "[dependencies]\nloc.workspace = true\n"),
        "app/src/lib.rs": "",
        "loc/Cargo.toml": pkg("loc"),
        "loc/src/lib.rs": "",
    }
    plain = {**files, "Cargo.toml": '[workspace]\nmembers = ["app"]\n\n[workspace.dependencies]\nloc = { path = "app/../loc" }\n'}
    assert deps(plain, "app") == {"loc": "loc"}
    redirected = {**plain, ".cargo/config.toml": '[patch."file:///anywhere/loc"]\nloc = { path = "loc" }\n'}
    assert deps(redirected, "app") == {"loc": UNPROVEN}


@pytest.mark.parametrize("key", ["file:///anywhere/loc#0.1.0", "file:///anywhere/loc#loc@0.1.0", "path+file:///anywhere/loc#loc@0.1.0", "FILE:///anywhere/loc#0.1.0"])
def test_a_replace_keyed_by_a_file_url_names_the_path_dependency(deps, key):
    """cargo built (probe path_dep_replace_file_id, with a source of its own to replace it with): a `[replace]` of a path source's package id
    is applied to a path dependency, `left: 1` (a path replacement collides with the replaced package in the lock file, so it never builds)."""
    assert deps(loc_repo(f'\n[replace]\n"{key}" = {{ path = "alt" }}\n')) == {"loc": UNPROVEN}


@pytest.mark.parametrize("key", ["loc:0.1.0", "loc@0.1.0", "https://github.com/x/loc#loc:0.1.0", "registry+https://github.com/rust-lang/crates.io-index#loc@0.1.0"])
def test_a_replace_that_cannot_be_the_id_of_a_path_package_leaves_the_path_dependency_an_edge(deps, key):
    """cargo built (probe path_dep_replace_crates_io_id): `loc:0.1.0` is crates.io's package, which a path dependency is not: unused, `left: 3`."""
    assert deps(loc_repo(f'\n[replace]\n"{key}" = {{ path = "alt" }}\n')) == EDGE


# ---- gap 2: any `paths` entry makes every dependency of every package unresolved, a path dependency too


def paths_repo(entries, below, config="paths"):
    """`foo = "1"` and `serde = "1"` of a lone package, a config with `paths = <entries>`, and the files `below`."""
    return foo_repo(**{".cargo/config.toml": f"{config} = {entries}\n", **below})


def pkg_at(directory, name="foo"):
    return {f"{directory}/Cargo.toml": pkg(name), f"{directory}/src/lib.rs": ""}


SHAPES = {  # name -> (the entries, the files below): whatever is there, or is not, an entry refuses
    "holds_the_name": ('["sub"]', {**pkg_at("sub", "subpkg"), **pkg_at("sub/foo")}),
    "holds_it_deep": ('["third_party"]', pkg_at("third_party/forks/deep/foo")),
    "holds_it_in_node_modules": ('["sub"]', {**pkg_at("sub", "subpkg"), **pkg_at("sub/node_modules/foo")}),
    "holds_it_in_a_hidden_directory": ('["sub"]', {**pkg_at("sub", "subpkg"), **pkg_at("sub/.hidden/foo")}),
    "holds_another_name": ('["sub"]', pkg_at("sub/inner", "bar")),
    "holds_no_package": ('["sub"]', {"sub/readme.txt": "no manifest"}),
    "holds_an_unreadable_manifest": ('["sub"]', {"sub/bad/Cargo.toml": "[package\nname =", "sub/bad/src/lib.rs": "", **pkg_at("sub/ok", "bar")}),
    "a_second_entry_holds_another_name": ('["sub", "other"]', {**pkg_at("sub", "subpkg"), **pkg_at("other", "bar")}),
    "outside": ('["../outside"]', {}),
    "absolute_outside": ('["/nowhere/at/all"]', {}),
    "missing": ('["nothing-here"]', {}),
}


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_any_paths_entry_makes_every_dependency_unresolved_whatever_it_holds(deps, shape):
    """cargo built (y1_paths_recursive, y1b_paths_deep, probe paths_below, and n1, n2, n5 below; b1_paths_outside applies an
    entry outside the tree, `left: 1`, and b5_paths_missing errors): an entry loads every package at or below it and the path dependencies of each
    wherever they point, and overrides by name. Ripple follows none of that, so the entry alone refuses, a registry or git dependency and a path
    dependency alike, whether the entry holds a package of the dependency's name or not."""
    entries, below = SHAPES[shape]
    assert deps(paths_repo(entries, below)) == {"foo": UNPROVEN, "serde": UNPROVEN}
    path_files = {**paths_repo(entries, below), "Cargo.toml": pkg("p", '[dependencies]\nloc = { path = "loc" }\n'), **pkg_at("loc", "loc")}
    assert deps(path_files) == {"loc": UNPROVEN}


def test_a_paths_entry_may_be_written_as_a_path_that_comes_back_into_the_directory(make_repo, tmp_path):
    entry = f'["../{tmp_path.name}/sub", "{tmp_path}/other"]'
    repo = make_repo(paths_repo(entry, {**pkg_at("sub"), **pkg_at("other", "serde")}))
    found = rm.load_workspace(repo, sorted(repo.rglob("*.rs"))).packages[""].deps
    assert found == {"foo": UNPROVEN, "serde": UNPROVEN}


def nested(**more):
    """`paths = ["sub"]`, and the package `subpkg` at sub/ (whose Cargo.toml `more` replaces)."""
    return {".cargo/config.toml": 'paths = ["sub"]\n', "sub/src/lib.rs": "", **more}


def test_a_paths_entry_reaches_the_path_dependencies_of_the_packages_below_it_n1(deps):
    """cargo built (n1_paths_nested_pathdep_registry): `sub` is below the entry and has `foo = { path = "../third/foo" }`, a package outside the
    entry. Cargo loads it with the override, so the registry `foo` of the root is that one, `left: 1`: external was wrong."""
    files = {
        "Cargo.toml": pkg("p", '[dependencies]\nfoo = "1"\n'),
        "src/lib.rs": "",
        **nested(**{"sub/Cargo.toml": pkg("subpkg", '[dependencies]\nfoo = { path = "../third/foo" }\n')}),
        **pkg_at("third/foo"),
    }
    assert deps(files) == {"foo": UNPROVEN}


def test_a_paths_entry_reaches_the_path_dependencies_of_the_packages_below_it_n2(deps):
    """cargo built (n2_paths_nested_pathdep_over_path): the root's `loc = { path = "loc" }` is not what runs when `sub` has
    `loc = { path = "../alt" }`: cargo loads alt with the override and it wins by name, `left: 1`: an edge to loc/ was wrong."""
    files = {
        **loc_repo(),
        **nested(**{"sub/Cargo.toml": pkg("subpkg", '[dependencies]\nloc = { path = "../alt" }\n')}),
    }
    assert deps(files) == {"loc": UNPROVEN}


def test_a_paths_entry_reaches_a_path_dependency_that_leaves_the_directory_n4(deps):
    """cargo built (n4_paths_nested_pathdep_outside, `left: 1`): the package below the entry reaches a crate outside the tree, and the override
    holds it. The old answer, external, was only true by luck of that crate not being in the repo."""
    files = {
        "Cargo.toml": pkg("p", '[dependencies]\nfoo = "1"\n'),
        "src/lib.rs": "",
        **nested(**{"sub/Cargo.toml": pkg("subpkg", '[dependencies]\nfoo = { path = "../../outside_foo" }\n')}),
    }
    assert deps(files) == {"foo": UNPROVEN}


def test_a_paths_entry_reaches_the_path_dependencies_transitively_and_through_every_table_n5(deps):
    """cargo built (n5_paths_transitive_devdep_over_path, `left: 1`): `sub` dev-depends on `../mid`, which depends on `loc = { path = "../alt" }`."""
    files = {
        **loc_repo(),
        **nested(**{"sub/Cargo.toml": pkg("subpkg", '[dev-dependencies]\nmid = { path = "../mid" }\n')}),
        "mid/Cargo.toml": pkg("mid", '[dependencies]\nloc = { path = "../alt" }\n'),
        "mid/src/lib.rs": "",
    }
    assert deps(files) == {"loc": UNPROVEN}


def test_a_paths_entry_refuses_the_dependencies_of_every_package_not_only_the_one_beside_it(deps):
    files = {
        **foo_repo(),
        ".cargo/config.toml": 'paths = ["sub"]\n',
        "tools/Cargo.toml": pkg("tools", '[dependencies]\nfoo = "1"\nloc = { path = "../loc" }\n'),
        "tools/src/lib.rs": "",
        **pkg_at("sub", "subpkg"),
        **pkg_at("loc", "loc"),
    }
    assert deps(files, "tools") == {"foo": UNPROVEN, "loc": UNPROVEN}
    assert deps(files) == {"foo": UNPROVEN, "serde": UNPROVEN}


def test_a_paths_entry_in_a_nested_config_refuses_the_whole_directory(deps):
    files = foo_repo(**{"tools/x/.cargo/config.toml": 'paths = ["."]\n', **pkg_at("tools/x", "x")})
    assert deps(files) == {"foo": UNPROVEN, "serde": UNPROVEN}


@pytest.mark.parametrize(
    "paths",
    ["[3]", '"sub"', '{ sub = "x" }', '["sub", 2]', "true", "{}", '""', "false", "0"],
    ids=["number", "string", "table", "mixed", "bool", "empty_table", "empty_string", "false", "zero"],
)
def test_a_paths_that_is_no_list_of_strings_refuses_every_dependency(deps, paths):
    """Cargo accepts nothing of these as a list of directories, and ripple cannot say which one it would have read: a refusal, for a path
    dependency too."""
    files = foo_repo(**{".cargo/config.toml": f"paths = {paths}\n"})
    assert deps(files) == {"foo": UNPROVEN, "serde": UNPROVEN}
    assert deps(loc_repo(**{".cargo/config.toml": f"paths = {paths}\n"})) == {"loc": UNPROVEN}


def test_an_empty_paths_is_no_entry_and_redirects_nothing(deps):
    """`paths = []` overrides no source: cargo loads no package for it (probe path_empty, built), so nothing is refused for it."""
    assert deps(foo_repo(**{".cargo/config.toml": "paths = []\n"})) == {"foo": OUT, "serde": OUT}
    assert deps(loc_repo(**{".cargo/config.toml": "paths = []\n"})) == EDGE


def test_ripple_reads_no_directory_for_a_paths_entry(make_repo):
    """The reach of a `paths` entry is not walked, so a manifest below one is no input of the index: the config that holds the entry is the file
    that is fingerprinted, and an edit of the entry (or a manifest below it) refuses nothing more or less."""
    below = {**pkg_at("sub", "subpkg"), **pkg_at("sub/node_modules/foo")}
    repo = make_repo(paths_repo('["sub"]', below))
    sources = [repo / "src/lib.rs"]
    listed = rm.config_files(repo, sources)
    assert ".cargo/config.toml" in listed and "sub/node_modules/foo/Cargo.toml" not in listed
    before = fingerprint(repo, RUST, sources)
    (repo / ".cargo/config.toml").write_text("paths = []\n")
    assert fingerprint(repo, RUST, sources) != before


# ---- gap 3: an `include` of a config, in any form, is not read, so nothing of the redirects can be proven


INCLUDES = [
    'include = ["patches.toml"]\n',
    'include = [{ path = "patches.toml", optional = true }]\n',
    '[[include]]\npath = "patches.toml"\noptional = true\n',
    'include = "patches.toml"\n',
    'include.path = "patches.toml"\n',
    "include = []\n",
]


@pytest.mark.parametrize("config", INCLUDES, ids=["array", "array_of_tables", "table_array", "string", "dotted", "empty"])
@pytest.mark.parametrize("name", ["config.toml", "config"])
def test_an_include_in_a_cargo_config_makes_every_dependency_unresolved(deps, config, name):
    """cargo built (stable, y2b_config_include_tbl and probes config_include, config_include_tables): the included file's `[patch]` is applied,
    `left: 1`. The array, array of tables and `[[include]]` forms are accepted by stable cargo, the bare string and the table are not
    (`could not load Cargo configuration`); ripple refuses all of them, and an empty list, since it reads none of the files."""
    files = foo_repo(**{f".cargo/{name}": config, ".cargo/patches.toml": '[patch.crates-io]\nfoo = { path = "foo" }\n'})
    assert deps(files) == {"foo": UNPROVEN, "serde": UNPROVEN}
    path_files = loc_repo(**{f".cargo/{name}": config})
    assert deps(path_files) == {"loc": UNPROVEN}  # the included file may hold a `paths` or a file-url `[patch]` for it


def test_an_include_in_a_nested_config_refuses_the_whole_directory(deps):
    files = foo_repo(**{"tools/x/.cargo/config.toml": 'include = ["p.toml"]\n', "tools/x/Cargo.toml": pkg("x"), "tools/x/src/lib.rs": ""})
    assert deps(files) == {"foo": UNPROVEN, "serde": UNPROVEN}


def test_a_config_that_cannot_be_read_refuses_every_path_dependency(deps):
    """It may hold a `paths` or a file-url `[patch]`. (A registry dependency is refused only where it is called like a package of the repo:
    test_rs_redirect.py, a cargo config that cannot be read.)"""
    assert deps(loc_repo(**{".cargo/config.toml": "paths = [\n"})) == {"loc": UNPROVEN}
    assert deps(foo_repo(**{".cargo/config.toml": "paths = [\n"})) == {"foo": OUT, "serde": OUT}


def test_a_manifest_above_a_package_that_cannot_be_read_refuses_its_path_dependencies(deps):
    """The root that governs `app` may hold a `[patch."file:///..."]` of the path dependency, and cargo may read what tomllib cannot."""
    files = {
        "Cargo.toml": "this is [not toml\n",
        "app/Cargo.toml": pkg("app", '[dependencies]\nloc = { path = "../loc" }\n'),
        "app/src/lib.rs": "",
        "loc/Cargo.toml": pkg("loc"),
        "loc/src/lib.rs": "",
    }
    assert deps(files, "app") == {"loc": UNPROVEN}
    assert deps({**files, "Cargo.toml": "[workspace]\nmembers = ['app']\n"}, "app") == {"loc": "loc"}
    sibling = {**files, "Cargo.toml": '[workspace]\nmembers = ["app"]\n', "tools/Cargo.toml": "this is [not toml\n"}
    assert deps(sibling, "app") == {"loc": "loc"}  # an unreadable manifest elsewhere governs nothing here


def named_root(root_manifest, named="../root", **more):
    """`app` with a path dependency on `loc`, whose `package.workspace` names the manifest at `named`, which holds `root_manifest`."""
    return {
        "app/Cargo.toml": pkg("app", f'workspace = "{named}"\n\n[dependencies]\nloc = {{ path = "../loc" }}\n'),
        "app/src/lib.rs": "",
        "loc/Cargo.toml": pkg("loc"),
        "loc/src/lib.rs": "",
        "alt/Cargo.toml": pkg("loc"),
        "alt/src/lib.rs": "",
        "root/Cargo.toml": root_manifest,
        "root/src/lib.rs": "",
        **more,
    }


def test_a_workspace_root_named_by_package_workspace_inside_the_tree_that_cannot_be_read_refuses_the_path_dependencies(deps):
    """The root sits beside the package, in the tree, and holds what may be a `[patch."file:///<loc>"]` that cargo reads and tomllib does not."""
    assert deps(named_root("this is [not toml\n"), "app") == {"loc": UNPROVEN}
    readable = '[workspace]\nmembers = ["../app"]\n'
    assert deps(named_root(readable), "app") == {"loc": "loc"}  # read, and it redirects nothing
    patched = readable + '[patch."file:///anywhere/loc"]\nloc = { path = "../alt" }\n'
    assert deps(named_root(patched), "app") == {"loc": UNPROVEN}


def test_a_workspace_root_named_by_package_workspace_outside_the_tree_is_assumed_to_redirect_nothing(deps):
    """README: a manifest outside the analysed directory is not seen, and for a path dependency it is assumed to hold no redirect."""
    assert deps(named_root("[workspace]\n", named="../../elsewhere/Cargo.toml"), "app") == {"loc": "loc"}


def test_a_config_with_no_include_changes_nothing(deps):
    assert deps(foo_repo(**{".cargo/config.toml": "[build]\njobs = 2\n"})) == {"foo": OUT, "serde": OUT}
    assert deps(loc_repo(**{".cargo/config.toml": "[build]\njobs = 2\n"})) == EDGE
