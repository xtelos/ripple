"""Rule P: which dependencies of a package may a `[patch]` or a `[replace]` make a package of the repo, and so are unresolved.

Ripple does not decide whether cargo applies a patch (its source, the version, a lock, the root that governs the package, `members` against
`exclude`: tests/test_rs_patch_probes.py builds what cargo does with each, and it is more than a manifest reader can follow). A dependency that is
not a `path` dependency is PATCH_UNPROVEN, never an edge and never external, when it MAY be redirected:

  1. its package name (the key, or its `package =` value) is named by any `[patch.<anything>]` or `[replace]` entry ripple can read anywhere in the
     analysed directory: every manifest, every cargo config, whether or not cargo would apply it; a table it cannot parse names everything;
  2. a manifest that may govern the package was not read (a `package.workspace` naming one, one above the analysed directory, an unreadable one,
     an unreadable config) and the name is that of a package of the repo.

Otherwise it is the fetched crate: external. A `path` dependency, a `paths` entry (any entry refuses every dependency), an `include` and what a
vendored source directory holds are judged in tests/test_rs_redirect_reach.py. Each test here fails when the branch it names is removed.
"""

import pytest

pytest.importorskip("tree_sitter_rust")

from ripple import rs_indexer as rs  # noqa: E402
from ripple import rs_modules as rm  # noqa: E402
from ripple.indexer import walk_languages  # noqa: E402
from ripple.profile import RUST  # noqa: E402

UNPROVEN, OUT = rm.PATCH_UNPROVEN, None


def pkg(name, extra=""):
    return f'[package]\nname = "{name}"\nversion = "1.2.0"\nedition = "2021"\n{extra}'


def load(make_repo, files, analysed=""):
    """The workspace of the directory `analysed` of the repo the files make (the repo, by default)."""
    repo = make_repo(files)
    base = repo / analysed if analysed else repo
    return repo, base, rm.load_workspace(base, sorted(base.rglob("*.rs")))


def deps(make_repo, files, package="", analysed=""):
    return load(make_repo, files, analysed)[2].packages[package].deps


def lone(manifest="", **more):
    """A lone package `p` with `foo = "1"` (and what `manifest` adds to its Cargo.toml), and the package `foo` of the repo."""
    return {"Cargo.toml": pkg("p") + '[dependencies]\nfoo = "1"\n' + manifest, "src/lib.rs": "", "foo/Cargo.toml": pkg("foo"), "foo/src/lib.rs": "", **more}


PATCH = '\n[patch.crates-io]\nfoo = { path = "foo" }\n'


# ---- condition 1: an entry names the package


def test_nothing_that_names_the_package_leaves_the_fetched_crate_external(make_repo):
    assert deps(make_repo, lone()) == {"foo": OUT}
    assert deps(make_repo, lone('\n[patch.crates-io]\nbar = { path = "foo" }\n')) == {"foo": OUT}  # a patch of another package
    assert deps(make_repo, lone('\n[replace]\n"foobar:1.0.0" = { path = "foo" }\n')) == {"foo": OUT}  # a name is a whole word of the key, not a part of one


@pytest.mark.parametrize(
    "files",
    [
        lone(PATCH),
        {
            "Cargo.toml": '[workspace]\nmembers = ["app"]\n' + PATCH,
            "app/Cargo.toml": pkg("app", '[dependencies]\nfoo = "1"\n'),
            "app/src/lib.rs": "",
            "foo/Cargo.toml": pkg("foo"),
            "foo/src/lib.rs": "",
        },
        {
            "Cargo.toml": '[workspace]\nmembers = ["app"]\n',
            "app/Cargo.toml": pkg("app", '[dependencies]\nfoo = "1"\n' + PATCH),
            "app/src/lib.rs": "",
            "foo/Cargo.toml": pkg("foo"),
            "foo/src/lib.rs": "",
        },
        {
            "Cargo.toml": '[workspace]\nmembers = ["app"]\nexclude = ["ex"]\n',
            "app/Cargo.toml": pkg("app", '[dependencies]\nfoo = "1"\n'),
            "app/src/lib.rs": "",
            "ex/Cargo.toml": pkg("ex", PATCH),
            "ex/src/lib.rs": "",
            "foo/Cargo.toml": pkg("foo"),
            "foo/src/lib.rs": "",
        },
        {
            "tools/Cargo.toml": '[workspace]\nmembers = ["../app"]\n' + PATCH,  # a root with no source file beside or below it
            "app/Cargo.toml": pkg("app", '[dependencies]\nfoo = "1"\n'),
            "app/src/lib.rs": "",
            "foo/Cargo.toml": pkg("foo"),
            "foo/src/lib.rs": "",
        },
    ],
    ids=["lone", "workspace_root", "member_own", "excluded_package", "root_without_sources"],
)
def test_a_patch_in_any_manifest_of_the_repo_makes_the_dependency_unresolved(make_repo, files):
    """Whether cargo applies it (a member's own patch is ignored, an excluded package's is its own root's, a root with no sources is read by cargo
    only when it is the one built) is not asked."""
    assert deps(make_repo, files, "app" if "app/Cargo.toml" in files else "") == {"foo": UNPROVEN}


@pytest.mark.parametrize(
    "dependency,entry",
    [
        ('foo = "1"', 'foo = { path = "foo" }'),
        ('foo = "1"', 'bar = { path = "foo", package = "foo" }'),  # the entry's `package`
        ('bar = { version = "1", package = "foo" }', 'foo = { path = "foo" }'),  # the dependency's `package`
        ('foo = "1"', 'foo = { path = "foo", package = "other" }'),  # the entry's key, whatever it renames
        ('foo_bar = "1"', 'foo-bar = { path = "foo" }'),  # a hyphen is an underscore
        ('Foo = "1"', 'foo = { path = "foo" }'),  # case
    ],
    ids=["key", "entry_package", "dependency_package", "key_of_a_renaming_entry", "hyphen", "case"],
)
def test_an_entry_names_its_key_and_its_package_and_a_dependency_its_key_and_its_package(make_repo, dependency, entry):
    files = lone(PATCH)
    files["Cargo.toml"] = pkg("p", f"[dependencies]\n{dependency}\n\n[patch.crates-io]\n{entry}\n")
    [value] = deps(make_repo, files).values()
    assert value == UNPROVEN


@pytest.mark.parametrize(
    "table",
    ["patch.crates-io", 'patch."https://example.invalid/other"', 'patch."file:///x/foo"', "patch.corp"],
)
def test_the_table_a_patch_is_in_does_not_matter(make_repo, table):
    """A patch of a registry the dependency does not come from, or of a url it does not name, is not applied by cargo (probes url_dep_crates_io_patch
    and url_dep_version_patch); ripple does not tell, so the dependency is unresolved in each."""
    assert deps(make_repo, lone(f'\n[{table}]\nfoo = {{ path = "foo" }}\n')) == {"foo": UNPROVEN}


def test_a_patch_whose_package_is_not_in_the_repo_names_the_dependency_all_the_same(make_repo):
    assert deps(make_repo, lone('\n[patch.crates-io]\nfoo = { path = "../elsewhere" }\n')) == {"foo": UNPROVEN}
    assert deps(make_repo, lone('\n[patch.crates-io]\nfoo = { git = "https://example.invalid/foo" }\n')) == {"foo": UNPROVEN}


@pytest.mark.parametrize(
    "key",
    ["foo", "foo:1.0.0", "foo@1.0.0", "https://github.com/x/foo#1.0.0", "file:///x/foo#foo:1.0.0", "registry+https://github.com/rust-lang/crates.io-index#foo@1.0.0", "https://github.com/x/foo.git"],
)
def test_a_replace_entry_names_the_package_in_its_key(make_repo, key):
    """A `[replace]` key is a package id in any of cargo's spellings: a name, `name:version`, `name@version`, `url#version` (the name is the last
    segment of the url), `url#name:version`. Its name is read as a whole word of the key (probes replace_registry and replace_git build the
    first and the last of the entries cargo applies)."""
    assert deps(make_repo, lone(f'\n[replace]\n"{key}" = {{ path = "foo" }}\n')) == {"foo": UNPROVEN}


def test_a_replace_entry_in_a_member_names_it_too(make_repo):
    member = {
        "Cargo.toml": '[workspace]\nmembers = ["app"]\n',
        "app/Cargo.toml": pkg("app", '[dependencies]\nfoo = "1"\n\n[replace]\n"foo:1.0.0" = { path = "../foo" }\n'),
        "app/src/lib.rs": "",
    }
    assert deps(make_repo, member, "app") == {"foo": UNPROVEN}


def test_a_replace_entry_that_renames_its_package_names_that(make_repo):
    assert deps(make_repo, lone('\n[replace]\n"bar:1.0.0" = { path = "foo", package = "foo" }\n')) == {"foo": UNPROVEN}


@pytest.mark.parametrize(
    "config",
    [
        {".cargo/config.toml": '[patch.crates-io]\nfoo = { path = "foo" }\n'},
        {"crates/x/.cargo/config": '[patch.crates-io]\nfoo = { path = "../../../foo" }\n', "crates/x/Cargo.toml": pkg("x"), "crates/x/src/lib.rs": ""},
    ],
    ids=["at_the_root", "nested"],
)
def test_a_patch_in_a_cargo_config_of_the_repo_names_the_package(make_repo, config):
    """cargo reads it only for a build run from at or below its `.cargo` (probe config_patch), which is not asked: any config of the repo."""
    assert deps(make_repo, lone(**config)) == {"foo": UNPROVEN}


def test_a_config_paths_entry_makes_every_dependency_unresolved_not_only_the_one_it_holds(make_repo):
    """`paths = ["foo"]` in a cargo config is no `[patch]`, and cargo builds it as the repo's foo for the registry's (probe config_paths). Cargo
    loads the packages below the entry and the path dependencies of each, wherever they point, so the entry refuses every dependency, `serde`
    too, whose package is not at `foo`."""
    files = lone(**{".cargo/config.toml": 'paths = ["foo"]\n'})
    files["Cargo.toml"] += 'serde = "1"\n'
    assert deps(make_repo, files) == {"foo": UNPROVEN, "serde": UNPROVEN}


@pytest.mark.parametrize("paths", ['["../outside"]', '["nowhere"]', '[3]', '"foo"'], ids=["outside", "no_package", "not_a_string", "not_a_list"])
def test_a_config_paths_ripple_cannot_read_makes_every_dependency_unresolved(make_repo, paths):
    files = lone(**{".cargo/config.toml": f"paths = {paths}\n"})
    files["Cargo.toml"] += 'serde = "1"\n'
    assert deps(make_repo, files) == {"foo": UNPROVEN, "serde": UNPROVEN}


def test_a_config_patch_of_another_package_leaves_the_fetched_crate(make_repo):
    assert deps(make_repo, lone(**{".cargo/config.toml": '[patch.crates-io]\nbar = { path = "foo" }\n'})) == {"foo": OUT}


def test_a_path_dependency_is_as_it_was_whatever_a_patch_names(make_repo):
    files = lone(PATCH)
    files["Cargo.toml"] = pkg("p", '[dependencies]\nfoo = { path = "foo" }\n' + PATCH)
    assert deps(make_repo, files) == {"foo": "foo"}
    files["Cargo.toml"] = pkg("p", '[dependencies]\nfoo = { path = "nowhere" }\n' + PATCH)
    assert deps(make_repo, files) == {"foo": OUT}


def test_a_workspace_dependency_without_a_path_is_judged_by_its_package_name(make_repo):
    def files(entry):
        return {
            "Cargo.toml": '[workspace]\nmembers = ["app"]\n\n[workspace.dependencies]\nshared = { version = "1", package = "foo" }\n' + entry,
            "app/Cargo.toml": pkg("app", "[dependencies]\nshared.workspace = true\n"),
            "app/src/lib.rs": "",
        }

    assert deps(make_repo, files('\n[patch.crates-io]\nfoo = { path = "foo" }\n'), "app") == {"shared": UNPROVEN}
    assert deps(make_repo, files(""), "app") == {"shared": OUT}


# ---- the table ripple cannot read


@pytest.mark.parametrize(
    "head,tail",
    [
        ("patch = 3\n", ""),
        ("", "[patch]\ncrates-io = 3\n"),
        ("", "[patch.crates-io]\nfoo = 3\n"),
        ("", '[patch.crates-io]\nfoo = { path = "foo", package = 3 }\n'),
        ("replace = 3\n", ""),
        ("", '[replace]\n"foo:1.0.0" = 3\n'),
    ],
    ids=["patch_not_a_table", "registry_not_a_table", "entry_not_a_table", "package_not_a_string", "replace_not_a_table", "replace_entry_not_a_table"],
)
def test_a_patch_or_replace_table_ripple_cannot_read_makes_every_dependency_unresolved(make_repo, head, tail):
    files = {"Cargo.toml": head + pkg("p", '[dependencies]\nserde = "1"\n') + tail, "src/lib.rs": ""}
    assert deps(make_repo, files) == {"serde": UNPROVEN}


def test_a_config_table_ripple_cannot_read_makes_every_dependency_unresolved(make_repo):
    files = {"Cargo.toml": pkg("p", '[dependencies]\nserde = "1"\n'), "src/lib.rs": "", ".cargo/config.toml": "patch = 3\n"}
    assert deps(make_repo, files) == {"serde": UNPROVEN}


def test_tables_that_parse_make_nothing_unresolved(make_repo):
    files = {"Cargo.toml": pkg("p", '[dependencies]\nserde = "1"\n[patch]\n[patch.crates-io]\n[replace]\n'), "src/lib.rs": "", ".cargo/config.toml": "[patch.crates-io]\n"}
    assert deps(make_repo, files) == {"serde": OUT}


# ---- condition 2: a manifest that may govern the package was not read, and the name is a package of the repo


def governed_by_unread(workspace):
    return {"Cargo.toml": pkg("app", f'workspace = "{workspace}"\n\n[dependencies]\nfoo = "1"\nserde = "1"\n'), "src/lib.rs": "", "foo/Cargo.toml": pkg("foo"), "foo/src/lib.rs": ""}


def test_a_package_workspace_naming_a_manifest_ripple_did_not_read_may_hold_a_patch_of_a_package_of_the_repo(make_repo):
    """Only a dependency that is called like a package of the repo (`foo`): a patch can only make a dependency the package of its own name."""
    assert deps(make_repo, governed_by_unread("../root")) == {"foo": UNPROVEN, "serde": OUT}


def test_a_package_workspace_naming_a_manifest_ripple_read_is_no_reason(make_repo):
    files = {
        "root/Cargo.toml": '[workspace]\nmembers = ["../app"]\n',
        "app/Cargo.toml": pkg("app", 'workspace = "../root"\n\n[dependencies]\nfoo = "1"\n'),
        "app/src/lib.rs": "",
        "foo/Cargo.toml": pkg("foo"),
        "foo/src/lib.rs": "",
    }
    assert deps(make_repo, files, "app") == {"foo": OUT}


def test_a_manifest_above_the_analysed_directory_may_hold_a_patch_of_a_package_of_the_repo(make_repo):
    files = {
        "Cargo.toml": '[workspace]\nmembers = ["app"]\n',
        "app/Cargo.toml": pkg("app", '[dependencies]\nfoo = "1"\nserde = "1"\n'),
        "app/src/lib.rs": "",
        "app/foo/Cargo.toml": pkg("foo"),
        "app/foo/src/lib.rs": "",
    }
    assert deps(make_repo, files, "", analysed="app") == {"foo": UNPROVEN, "serde": OUT}


def test_without_a_manifest_above_the_analysed_directory_it_is_the_fetched_crate(make_repo):
    files = {"app/Cargo.toml": pkg("app", '[dependencies]\nfoo = "1"\n'), "app/src/lib.rs": "", "app/foo/Cargo.toml": pkg("foo"), "app/foo/src/lib.rs": ""}
    assert deps(make_repo, files, "", analysed="app") == {"foo": OUT}


def test_a_manifest_that_cannot_be_read_may_hold_a_patch_of_a_package_of_the_repo(make_repo):
    files = lone(**{"tools/Cargo.toml": "this is [not toml\n"})
    files["Cargo.toml"] += "serde = \"1\"\n"
    assert deps(make_repo, files) == {"foo": UNPROVEN, "serde": OUT}


def test_the_manifest_of_a_package_that_cannot_be_read_may_hold_a_patch_of_a_package_of_the_repo(make_repo):
    """The same where the manifest sits above a source file (a package ripple does not read), not only where the walk of the repo finds it alone."""
    files = lone(**{"app/Cargo.toml": "this is [not toml\n", "app/src/lib.rs": ""})
    files["Cargo.toml"] += 'serde = "1"\n'
    assert deps(make_repo, files) == {"foo": UNPROVEN, "serde": OUT}


def test_a_cargo_config_that_cannot_be_read_may_hold_a_patch_of_a_package_of_the_repo(make_repo):
    files = lone(**{".cargo/config.toml": "this is [not toml\n"})
    files["Cargo.toml"] += "serde = \"1\"\n"
    assert deps(make_repo, files) == {"foo": UNPROVEN, "serde": OUT}


def test_a_package_of_the_repo_that_is_called_like_the_dependency_is_what_an_unseen_patch_may_make_it(make_repo):
    """Package.maybe: for the question of what `std` is, an unseen patch may make the dependency the package of that name."""
    files = governed_by_unread("../root")
    files["foo/Cargo.toml"] = pkg("foo", '\n[lib]\nname = "std"\n')
    repo, base, ws = load(make_repo, files)
    assert ws.packages[""].maybe == ("foo",)
    index_files, skipped = walk_languages(repo, [RUST])
    index = rs.read_repo(repo, index_files[RUST.name], skipped)[0]
    ctx = next(c for c in index.ctxs if c.crate.package.dir == "" and c.crate.kind == "lib")
    assert "std" in index.foreign_crates(ctx)


# ---- the manifests and configs are in the cache's fingerprint


def test_every_manifest_and_config_ripple_reads_for_a_patch_is_a_file_the_cache_watches(make_repo):
    files = lone(**{"tools/Cargo.toml": "[workspace]\n", ".cargo/config.toml": ""})
    repo = make_repo(files)
    watched = rm.config_files(repo, sorted(repo.rglob("*.rs")))
    assert {"tools/Cargo.toml", ".cargo/config.toml", "Cargo.toml", "foo/Cargo.toml"} <= set(watched)
    above = make_repo({"Cargo.toml": "[workspace]\n", "app/Cargo.toml": pkg("app"), "app/src/lib.rs": ""})
    assert "../Cargo.toml" in rm.config_files(above / "app", [above / "app/src/lib.rs"])


# ---- what governs a package as a whole (std_why), which Rule P leaves as it was


def test_a_manifest_above_a_package_that_cannot_be_read_may_hold_a_patch(make_repo):
    files = {"Cargo.toml": "this is [not toml\n", "app/Cargo.toml": pkg("app"), "app/src/lib.rs": ""}
    repo, base, ws = load(make_repo, files)
    assert ws.packages["app"].std_why == "Cargo.toml could not be read"
    assert ws.problems == ["Cargo.toml (Cargo.toml could not be read)"]


def test_a_readable_manifest_above_a_package_is_no_reason_to_mark_it(make_repo):
    assert load(make_repo, {"Cargo.toml": "[workspace]\nmembers = []\n", "app/Cargo.toml": pkg("app"), "app/src/lib.rs": ""})[2].packages["app"].std_why is None


GIT = "file:///x/foo"


def member_patch(name_key, package_key=""):
    """A workspace member whose own `[patch]` (which cargo ignores) names a git dependency with a package whose library is called std."""
    return {
        "Cargo.toml": '[workspace]\nmembers = ["app", "stdlib"]\nresolver = "2"\n',
        "app/Cargo.toml": pkg("app") + f'[dependencies]\nfoo = {{ git = "{GIT}" }}\n\n[patch."{GIT}"]\n{name_key} = {{ path = "../stdlib"{package_key} }}\n',
        "app/src/lib.rs": "pub fn t_call() -> u32 { std::mark() }\n",
        "stdlib/Cargo.toml": pkg("foo") + '\n[lib]\nname = "std"\n',
        "stdlib/src/lib.rs": "pub fn mark() -> u32 { 2 }\n",
    }


@pytest.mark.parametrize("files", [member_patch("foo"), member_patch("bar", ', package = "foo"')], ids=["the key", "the package it renames"])
def test_a_patch_a_member_writes_is_ignored_by_cargo_and_still_may_be_the_package(make_repo, files):
    """Cargo says "patch for the non root package will be ignored". Rule P reads it all the same: the dependency is unresolved, and the package it
    names has a library called std, so `std` may be that crate in the member (Package.maybe, Index.foreign_crates)."""
    repo, base, ws = load(make_repo, files)
    package = ws.packages["app"]
    assert package.deps == {"foo": UNPROVEN}
    assert package.maybe == ("stdlib",)
    files_, skipped = walk_languages(repo, [RUST])
    index = rs.read_repo(repo, files_[RUST.name], skipped)[0]
    ctx = next(c for c in index.ctxs if c.crate.package.dir == "app" and c.crate.kind == "lib")
    assert "std" in index.foreign_crates(ctx)
    assert index.prelude_replaced(ctx)
