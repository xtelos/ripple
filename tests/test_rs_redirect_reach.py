"""Rule P, how far it reaches: the spellings of a redirect cargo accepts that name a PATH dependency, an included file, a `paths` entry, or a
vendored source directory (the probes in bench/fixtures/rust-probes/crates/_redirect_reach are built with cargo by tests/test_rs_redirect_probes.py; the cargo facts each test
leans on are in its docstring).

A redirect ripple can see but not fully read refuses: the dependency is PATCH_UNPROVEN (never an edge, never external). Where a redirect can
only name a package ripple reads, the dependency is refused when that package's name is the dependency's. A `paths` entry is the one redirect
that is not followed at all: cargo loads every package below it and the path dependencies of each, transitively, wherever they point, and
overrides by name, so any entry refuses every dependency of every package. Each test here fails when the rule it names is removed.
"""

import threading

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


# ---- gap 4: a vendored source directory inside the analysed directory


VENDOR = '[source.crates-io]\nreplace-with = "vendored-sources"\n\n[source.vendored-sources]\ndirectory = "vendor"\n'


def vendor_repo(config=VENDOR, below=None, name=".cargo/config.toml"):
    """`foo = "1"` and `serde = "1"` of a lone package, the config, and the files `below` (by default package foo in vendor/foo)."""
    return foo_repo(**{name: config, **(pkg_at("vendor/foo") if below is None else below)})


@pytest.mark.parametrize(
    "config",
    [
        VENDOR,
        'source.crates-io.replace-with = "vs"\nsource.vs.directory = "vendor"\n',
        'source = { crates-io = { replace-with = "vs" }, vs = { directory = "vendor" } }\n',
        '[source.crates-io]\nreplace-with = "a"\n[source.a]\nregistry = "https://example.invalid/index"\nreplace-with = "b"\n[source.b]\ndirectory = "vendor"\n',
        '[source."https://example.invalid/x"]\ngit = "https://example.invalid/x"\nreplace-with = "vs"\n[source.vs]\ndirectory = "vendor"\n',
        '[source.unused]\ndirectory = "vendor"\n',
    ],
    ids=["header", "dotted", "inline", "chain", "git_source", "replaced_by_nothing"],
)
def test_a_dependency_with_a_package_of_its_name_in_a_vendored_directory_is_unresolved(deps, config):
    """cargo built (probes vendored_directory, vendored_dotted, vendored_inline, vendored_chain; and a git source replaced by a directory): the
    vendored package is what runs, `left: 7`, and it is a package ripple indexes: neither external nor an edge. Which source replaces which is not
    asked (the table of a directory that replaces nothing counts too)."""
    assert deps(vendor_repo(config)) == {"foo": UNPROVEN, "serde": OUT}


def test_a_vendored_package_is_found_by_its_name_not_its_directorys(deps):
    """`cargo vendor` writes `foo-1.0.0` when it holds two versions, and cargo reads any directory name (probes vendored_directory, vendored_inline)."""
    assert deps(vendor_repo(below=pkg_at("vendor/foo-1.0.0"))) == {"foo": UNPROVEN, "serde": OUT}
    assert deps(vendor_repo(below=pkg_at("vendor/anything"))) == {"foo": UNPROVEN, "serde": OUT}
    assert deps(vendor_repo(below=pkg_at("vendor/foo", "serde"))) == {"foo": OUT, "serde": UNPROVEN}  # the package's name, not the directory's


def test_a_dependency_with_no_package_in_the_vendored_directory_stays_external(deps):
    assert deps(vendor_repo(below=pkg_at("vendor/other", "other"))) == {"foo": OUT, "serde": OUT}
    assert deps(vendor_repo(below={})) == {"foo": OUT, "serde": OUT}  # the directory is not there


def test_a_dependency_renamed_to_a_vendored_package_is_unresolved_and_so_is_a_git_one(deps):
    files = vendor_repo()
    files["Cargo.toml"] = pkg("p", '[dependencies]\nrenamed = { version = "1", package = "foo" }\nother = { git = "https://example.invalid/other" }\nfoo = { git = "https://example.invalid/foo" }\n')
    assert deps(files) == {"renamed": UNPROVEN, "other": OUT, "foo": UNPROVEN}


def test_a_vendored_directory_outside_the_analysed_directory_leaves_the_dependency_external(tmp_path_factory):
    """cargo built (vendored_outside, `left: 2`): the crates there are crates from outside the repo, which external says."""
    base = tmp_path_factory.mktemp("base")
    files = vendor_repo('[source.crates-io]\nreplace-with = "vs"\n[source.vs]\ndirectory = "../outside-vendor"\n', below={})
    for rel, text in {**{f"repo/{k}": v for k, v in files.items()}, **pkg_at("outside-vendor/foo")}.items():
        (base / rel).parent.mkdir(parents=True, exist_ok=True)
        (base / rel).write_text(text)
    repo = base / "repo"
    assert rm.load_workspace(repo, sorted(repo.rglob("*.rs"))).packages[""].deps == {"foo": OUT, "serde": OUT}


def test_a_vendored_directory_is_placed_by_where_it_is_not_how_it_is_written(make_repo, tmp_path):
    inside = vendor_repo(f'[source.crates-io]\nreplace-with = "vs"\n[source.vs]\ndirectory = "{tmp_path}/vendor"\n')
    repo = make_repo(inside)
    assert rm.load_workspace(repo, sorted(repo.rglob("*.rs"))).packages[""].deps == {"foo": UNPROVEN, "serde": OUT}  # an absolute path inside the directory
    back = vendor_repo(f'[source.crates-io]\nreplace-with = "vs"\n[source.vs]\ndirectory = "../{tmp_path.name}/vendor"\n')
    repo = make_repo(back)
    assert rm.load_workspace(repo, sorted(repo.rglob("*.rs"))).packages[""].deps == {"foo": UNPROVEN, "serde": OUT}


def test_the_directory_of_a_config_below_the_root_is_relative_to_it(deps):
    files = foo_repo(**{"crates/a/.cargo/config.toml": VENDOR, "crates/a/Cargo.toml": pkg("a"), "crates/a/src/lib.rs": "", **pkg_at("crates/a/vendor/foo")})
    assert deps(files) == {"foo": UNPROVEN, "serde": OUT}
    elsewhere = foo_repo(**{"crates/a/.cargo/config.toml": VENDOR, "crates/a/Cargo.toml": pkg("a"), "crates/a/src/lib.rs": "", **pkg_at("vendor/foo")})
    assert deps(elsewhere) == {"foo": OUT, "serde": OUT}  # `vendor` beside the config, not beside the root


@pytest.mark.parametrize(
    "config",
    ["source = 3\n", "[source]\nx = 3\n", '[source.x]\ndirectory = 3\n', '[source.x]\ndirectory = ["vendor"]\n'],
    ids=["not_a_table", "source_not_a_table", "directory_not_a_string", "directory_a_list"],
)
def test_a_source_table_ripple_cannot_read_makes_every_registry_or_git_dependency_unresolved(deps, config):
    assert deps(vendor_repo(config)) == {"foo": UNPROVEN, "serde": UNPROVEN}
    assert deps(loc_repo(**{".cargo/config.toml": config})) == EDGE  # a source is no source of a path dependency


def test_a_vendored_directory_holding_a_manifest_ripple_cannot_read_refuses_every_registry_dependency(deps):
    files = vendor_repo(below={"vendor/bad/Cargo.toml": "[package\nname =", **pkg_at("vendor/ok", "bar")})
    assert deps(files) == {"foo": UNPROVEN, "serde": UNPROVEN}


def test_a_path_dependency_into_a_vendored_directory_is_an_edge(deps):
    """Only a dependency that cargo takes from a source is unresolved: one the manifest gives a path is the package at that path."""
    files = vendor_repo(below=pkg_at("vendor/foo"))
    files["Cargo.toml"] = pkg("p", '[dependencies]\nfoo = { path = "vendor/foo" }\nserde = "1"\n')
    assert deps(files) == {"foo": "vendor/foo", "serde": OUT}


def test_a_vendored_packages_own_dependencies_are_judged_by_the_same_rule(deps):
    bar = {"vendor/foo/Cargo.toml": pkg("foo", '[dependencies]\nbar = "1"\n'), "vendor/foo/src/lib.rs": "", **pkg_at("vendor/bar", "bar")}
    assert deps(vendor_repo(below=bar), "vendor/foo") == {"bar": UNPROVEN}


# ---- a link between the vendored directory and a package: cargo's directory source keeps every direct child whose `Cargo.toml` exists, following links


def linked_repo(tmp_path_factory, files, links, outside=None):
    """The repo `files` make in base/repo, `outside` (paths from base: crates from outside the analysed directory), and each link (repo-relative path ->
    what it points at, as the link says it) made last. The repo is returned."""
    base = tmp_path_factory.mktemp("base")
    for rel, text in {**{f"repo/{k}": v for k, v in files.items()}, **(outside or {})}.items():
        (base / rel).parent.mkdir(parents=True, exist_ok=True)
        (base / rel).write_text(text)
    for rel, target in links.items():
        (base / "repo" / rel).parent.mkdir(parents=True, exist_ok=True)
        (base / "repo" / rel).symlink_to(target)
    return base / "repo"


def linked_deps(repo):
    return rm.load_workspace(repo, sorted(repo.rglob("*.rs"))).packages[""].deps


def test_a_vendored_child_that_is_a_link_to_a_package_of_the_repo_is_unresolved(tmp_path_factory):
    """cargo built (probe vendored_symlink, `left: 7`): `vendor/foo` is a link to `crates/foo`, and the repo's own code is what runs. The walk of a
    directory does not enter a link, so the package was never read, and `foo` read external for code that is in the repo."""
    files = vendor_repo(below=pkg_at("crates/foo"))
    for target in ("../crates/foo", "../crates/foo/", "{abs}/crates/foo"):
        repo = linked_repo(tmp_path_factory, files, {})
        (repo / "vendor").mkdir()
        (repo / "vendor/foo").symlink_to(target.format(abs=repo))
        assert linked_deps(repo) == {"foo": UNPROVEN, "serde": OUT}, target
    chained = linked_repo(tmp_path_factory, files, {"vendor/mid": "../crates/foo", "vendor/foo": "mid"})  # a link to a link
    assert linked_deps(chained) == {"foo": UNPROVEN, "serde": OUT}


def test_a_vendored_child_that_is_a_link_to_a_directory_outside_the_analysed_directory_stays_external(tmp_path_factory):
    """cargo built (probe vendored_symlink_outside, `left: 2`): the crate is from outside the repo, as a vendored directory outside is."""
    repo = linked_repo(tmp_path_factory, vendor_repo(below={}), {"vendor/foo": "../../outside/foo"}, pkg_at("outside/foo"))
    assert linked_deps(repo) == {"foo": OUT, "serde": OUT}


def test_a_vendored_child_that_is_a_link_to_nothing_a_file_or_itself_is_no_package(tmp_path_factory):
    """cargo keeps a child only when `child/Cargo.toml` exists: a dangling link, a link to a file and a link that loops are skipped."""
    files = vendor_repo(below={"vendor/file": "not a directory"})
    links = {"vendor/foo": "../nowhere", "vendor/file2": "file", "vendor/self": "self", "vendor/ring": "ring2", "vendor/ring2": "ring"}
    assert linked_deps(linked_repo(tmp_path_factory, files, links)) == {"foo": OUT, "serde": OUT}


def test_a_link_back_into_the_vendored_directory_or_the_repo_does_not_loop(tmp_path_factory):
    """Cargo looks at direct children only and so does the read: a link to the directory itself, to the repo root or to an ancestor is one child,
    and nothing below a link is entered, so a loop ends."""
    files = vendor_repo(below={**pkg_at("vendor/foo", "bar"), **pkg_at("vendor/real/deep", "baz")})
    links = {"vendor/up": "..", "vendor/same": ".", "vendor/real/up": "../..", "vendor/real/deep/loop": ".", "vendor/real/self": "self"}
    repo = linked_repo(tmp_path_factory, files, links)
    done: list = []
    worker = threading.Thread(target=lambda: done.append(linked_deps(repo)), daemon=True)  # a hang is a failure, not a stuck suite
    worker.start()
    worker.join(60)
    assert done, "reading the vendored directory did not end"
    assert done[0] == {"foo": OUT, "serde": OUT}  # no package called either; `vendor/up` is the package `p`, which no dependency names


def test_a_vendored_directory_that_is_a_loop_or_a_link_reads_nothing_it_may_not(tmp_path_factory):
    """cargo built (probe vendored_dir_link, `left: 7`): the configured directory may itself be a link, and it is read where it really is. A link to
    a directory outside is a vendored directory outside (external); one that loops holds nothing."""
    inside = linked_repo(tmp_path_factory, vendor_repo(below=pkg_at("crates/vend/foo")), {"vendor": "crates/vend"})
    assert linked_deps(inside) == {"foo": UNPROVEN, "serde": OUT}
    outside = linked_repo(tmp_path_factory, vendor_repo(below={}), {"vendor": "../outside"}, pkg_at("outside/foo"))
    assert linked_deps(outside) == {"foo": OUT, "serde": OUT}
    assert linked_deps(linked_repo(tmp_path_factory, vendor_repo(below={}), {"vendor": "vendor"})) == {"foo": OUT, "serde": OUT}


def test_a_vendored_package_whose_manifest_is_a_link_is_read_through_it(tmp_path_factory):
    """cargo built (probe vendored_manifest_link, `left: 7`): `vendor/foo/Cargo.toml` is a link, and the package is what it says. A manifest outside
    the repo is read the same (the sources of the package are in the repo); one that dangles or loops cannot be read, which refuses."""
    files = vendor_repo(below={"vendor/foo/src/lib.rs": "", "manifests/foo.toml": pkg("foo")})
    assert linked_deps(linked_repo(tmp_path_factory, files, {"vendor/foo/Cargo.toml": "../../manifests/foo.toml"})) == {"foo": UNPROVEN, "serde": OUT}
    away = vendor_repo(below={"vendor/foo/src/lib.rs": ""})
    assert linked_deps(linked_repo(tmp_path_factory, away, {"vendor/foo/Cargo.toml": "../../../outside/foo.toml"}, {"outside/foo.toml": pkg("foo")})) == {"foo": UNPROVEN, "serde": OUT}
    for target in ("../../nowhere.toml", "Cargo.toml"):
        assert linked_deps(linked_repo(tmp_path_factory, away, {"vendor/foo/Cargo.toml": target})) == {"foo": UNPROVEN, "serde": UNPROVEN}, target


def test_the_manifest_of_a_linked_vendored_child_is_fingerprinted_where_it_is_read(tmp_path_factory):
    """A manifest the rule reads through a link is in the cache fingerprint, so a link made, removed or pointed elsewhere rebuilds the graph; one
    that is not read (a link to a directory outside the analysed directory, a dangling one) is not listed."""
    files = vendor_repo(below=pkg_at("crates/foo"))
    repo = linked_repo(tmp_path_factory, files, {})
    sources = [repo / "src/lib.rs"]
    configs = [".cargo/config.toml"]  # the walk of the repo may reach the link itself, so ask what the vendored rule lists, too
    assert "vendor/foo/Cargo.toml" not in rm.redirect_manifests(repo, configs)
    (repo / "vendor").mkdir()
    (repo / "vendor/foo").symlink_to("../crates/foo")
    assert "vendor/foo/Cargo.toml" in rm.redirect_manifests(repo, configs)
    assert "vendor/foo/Cargo.toml" in rm.config_files(repo, sources)
    assert "vendor/foo/Cargo.toml" in fingerprint(repo, RUST, sources)
    (repo / "vendor/foo").unlink()
    (repo / "vendor/foo").symlink_to("../../outside/foo")
    assert "vendor/foo/Cargo.toml" not in rm.redirect_manifests(repo, configs)
    (repo / "vendor/foo").unlink()
    (repo / "vendor/foo").symlink_to("../nowhere")
    assert "vendor/foo/Cargo.toml" not in rm.redirect_manifests(repo, configs)


# ---- the fingerprint: a file ripple reads for these rules is one an edit of changes it


def test_a_manifest_below_a_vendored_directory_the_walk_skips_is_fingerprinted(make_repo, tmp_path_factory):
    """The package a vendored directory holds in a `node_modules` is read for its name, so an edit of that Cargo.toml (or adding one) changes the
    fingerprint and the graph is rebuilt."""
    config = '[source.crates-io]\nreplace-with = "vs"\n[source.vs]\ndirectory = "node_modules/vendor"\n'
    repo = make_repo(vendor_repo(config, below=pkg_at("node_modules/vendor/foo")))
    sources = [repo / "src/lib.rs"]
    assert "node_modules/vendor/foo/Cargo.toml" in rm.config_files(repo, sources)
    before = fingerprint(repo, RUST, sources)
    (repo / "node_modules/vendor/foo/Cargo.toml").write_text(pkg("renamed"))
    assert fingerprint(repo, RUST, sources) != before
    (repo / "node_modules/vendor/foo/Cargo.toml").unlink()
    assert "node_modules/vendor/foo/Cargo.toml" not in fingerprint(repo, RUST, sources)
    # a manifest in a `node_modules` that no config names is no business of the cache
    plain = tmp_path_factory.mktemp("plain")
    for rel, text in {"Cargo.toml": pkg("p"), "src/lib.rs": "", **pkg_at("node_modules/vendor/foo")}.items():
        (plain / rel).parent.mkdir(parents=True, exist_ok=True)
        (plain / rel).write_text(text)
    assert "node_modules/vendor/foo/Cargo.toml" not in rm.config_files(plain, [plain / "src/lib.rs"])
