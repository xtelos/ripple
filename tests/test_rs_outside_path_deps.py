"""A path dependency outside the analysed directory: its Cargo.toml, and nothing else of it, says what its library is called.

`--repo app` of a workspace reads a package whose path dependencies are outside the repo. Ripple cannot read their sources, and a library of
theirs may be called `std` (which makes the package's prelude another crate's: tests/test_rs_prelude_replaced.py), so it marked the whole package
(edges 32 to 21 on bench/fixtures/rust/warehouse-rs/app). It now reads the one file that answers the question, the dependency's Cargo.toml (the
package's name, or the `[lib]` table's), and the cache fingerprints it (rs_modules.outside_manifests): an edit to it rebuilds. Where it cannot be
read, the package is marked as before.
"""

from collections import Counter

import pytest

pytest.importorskip("tree_sitter_rust")

from conftest import FIXTURES  # noqa: E402

from ripple import rs_indexer as rs  # noqa: E402
from ripple import rs_modules as rm  # noqa: E402
from ripple.cache import fingerprint  # noqa: E402
from ripple.indexer import build_graph, walk_languages  # noqa: E402
from ripple.profile import RUST  # noqa: E402

WAREHOUSE_APP = FIXTURES / "rust" / "warehouse-rs" / "app"
LIB = 'pub fn mark() -> u32 { 2 }\n'
APP_SRC = "pub fn t_vec() { let _v = Vec::new(); }\npub fn t_std() { std::mem::drop(1); }\n"


def pkg(name, extra="", dep=""):
    return f'[package]\nname = "{name}"\nversion = "0.1.0"\nedition = "2021"\n{extra}\n[dependencies]\n{dep}'


def files(lib_manifest, dep='lib = { path = "../lib" }', app_extra=""):
    out = {"app/Cargo.toml": pkg("app", app_extra, dep), "app/src/lib.rs": APP_SRC, "lib/src/lib.rs": LIB}
    if lib_manifest is not None:
        out["lib/Cargo.toml"] = lib_manifest
    return out


def read_app(make_repo, spec):
    """The `app` directory of the repo as the repo to read (so `lib` is outside it): ripple's index, and the package."""
    repo = make_repo(spec) / "app"
    sources, skipped = walk_languages(repo, [RUST])
    index = rs.read_repo(repo, sources[RUST.name], skipped)[0]
    ctx = next(c for c in index.ctxs if c.crate.kind == "lib")
    return repo, index, ctx


def kinds(index):
    return Counter(site[3][0] for site in index.resolved_sites())


@pytest.mark.parametrize(
    "lib_manifest",
    [pkg("lib"), pkg("lib", '[lib]\nname = "util"\n'), pkg("my-lib"), pkg("std", '[lib]\nname = "mylib"\n')],
    ids=["named_lib", "lib_table", "hyphen", "package_named_std_with_another_lib"],
)
def test_an_outside_dependency_whose_library_is_not_called_std_leaves_the_prelude_the_standard_ones(make_repo, lib_manifest):
    _, index, ctx = read_app(make_repo, files(lib_manifest))
    assert not index.prelude_replaced(ctx) and index.foreign_crates(ctx) == frozenset()
    assert kinds(index)["external"] == 2 and kinds(index)["unresolved"] == 0


@pytest.mark.parametrize(
    "lib_manifest,why",
    [(pkg("std"), "the package's name"), (pkg("lib", '[lib]\nname = "std"\n'), "the [lib] table"), (pkg("core"), "core"), (pkg("lib", '[lib]\nname = "core"\n'), "[lib] core")],
    ids=["package_std", "lib_std", "package_core", "lib_core"],
)
def test_an_outside_dependency_whose_library_is_called_std_or_core_replaces_the_prelude(make_repo, lib_manifest, why):
    _, index, ctx = read_app(make_repo, files(lib_manifest))
    assert index.prelude_replaced(ctx), why
    assert "std" in index.foreign_crates(ctx) or "core" in index.foreign_crates(ctx)
    got = kinds(index)  # Vec::new is not known to be the standard prelude's; `std::mem::drop` is not known to be the library's only where `std` may be another crate
    assert got["external"] == (1 if "core" in lib_manifest and "std" not in lib_manifest else 0) and got["unresolved"] == 2 - got["external"]


@pytest.mark.parametrize(
    "lib_manifest",
    [None, "this is [not toml\n", "[workspace]\nmembers = []\n", '[package]\nname = 1\n', pkg("lib", "[lib]\nname = 1\n"), "lib = 1\n" + pkg("lib")],
    ids=["missing", "unreadable", "no_package", "name_not_a_string", "lib_name_not_a_string", "lib_not_a_table"],
)
def test_an_outside_dependency_whose_manifest_cannot_be_read_marks_the_package_as_before(make_repo, lib_manifest):
    _, index, ctx = read_app(make_repo, files(lib_manifest))
    assert index.prelude_replaced(ctx) and index.foreign_crates(ctx) == rs.STD_CRATES


def test_a_workspace_dependency_that_leaves_the_repo_is_read_the_same_way(make_repo):
    spec = {
        "app/Cargo.toml": '[workspace]\nmembers = ["."]\n[workspace.dependencies]\nlib = { path = "../lib" }\n' + pkg("app", dep="lib.workspace = true\n"),
        "app/src/lib.rs": APP_SRC,
        "lib/Cargo.toml": pkg("lib", '[lib]\nname = "std"\n'),
        "lib/src/lib.rs": LIB,
    }
    _, index, ctx = read_app(make_repo, spec)
    assert index.prelude_replaced(ctx)
    spec["lib/Cargo.toml"] = pkg("lib")
    _, index, ctx = read_app(make_repo, spec)
    assert not index.prelude_replaced(ctx)


def test_a_path_dependency_that_wanders_but_stays_in_the_repo_is_a_package_of_the_repo(make_repo):
    spec = {"app/Cargo.toml": pkg("app", dep='lib = { path = "x/../lib" }\n'), "app/src/lib.rs": APP_SRC, "app/lib/Cargo.toml": pkg("lib", '[lib]\nname = "std"\n'), "app/lib/src/lib.rs": LIB}
    repo, index, ctx = read_app(make_repo, spec)
    assert rm.outside_manifests(repo, [repo / "src/lib.rs"]) == []
    assert index.prelude_replaced(ctx)  # read as a package of the repo, whose library is called std


def test_the_outside_manifest_is_in_the_fingerprint_and_an_edit_to_it_changes_it(make_repo):
    repo, _, _ = read_app(make_repo, files(pkg("lib")))
    sources = [repo / "src/lib.rs"]
    assert rm.outside_manifests(repo, sources) == ["../lib/Cargo.toml"]
    assert "../lib/Cargo.toml" in rm.config_files(repo, sources)
    before = fingerprint(repo, RUST, sources)
    assert "../lib/Cargo.toml" in before
    (repo.parent / "lib" / "Cargo.toml").write_text(pkg("lib", '[lib]\nname = "std"\n'))
    after = fingerprint(repo, RUST, sources)
    assert after["../lib/Cargo.toml"] != before["../lib/Cargo.toml"]


def test_a_path_dependency_written_as_an_absolute_path_is_read_and_fingerprinted_too(make_repo, tmp_path_factory):
    outside = tmp_path_factory.mktemp("abs-lib")
    (outside / "src").mkdir()
    (outside / "Cargo.toml").write_text(pkg("lib", '[lib]\nname = "std"\n'))
    (outside / "src" / "lib.rs").write_text(LIB)
    repo, index, ctx = read_app(make_repo, files(None, dep=f'lib = {{ path = "{outside}" }}'))
    assert index.prelude_replaced(ctx)
    key = rm.outside_manifests(repo, [repo / "src/lib.rs"])[0]
    assert key == f"{outside}/Cargo.toml"
    assert any(name.endswith("lib/Cargo.toml") or "abs-lib" in name for name in fingerprint(repo, RUST, [repo / "src/lib.rs"]))


def test_an_edit_to_the_outside_manifest_rebuilds_the_cached_graph(make_repo):
    from ripple.cache import load_repo

    repo, _, _ = read_app(make_repo, files(pkg("lib")))
    first = load_repo(repo, lang="rust").graph
    assert not [u for u in first.unresolved if "may be a crate other than the standard library" in u.reason]
    (repo.parent / "lib" / "Cargo.toml").write_text(pkg("lib", '[lib]\nname = "std"\n'))
    second = load_repo(repo, lang="rust").graph
    assert [u for u in second.unresolved if "may be a crate other than the standard library" in u.reason]


def test_the_warehouse_app_read_alone_keeps_its_edges_and_main_calls_cart_new():
    """Marked as a whole, `app` read alone had 21 edges: the path dependencies pricing-core and tax-rules are outside it, and neither is called std."""
    graph = build_graph(WAREHOUSE_APP, RUST)
    assert Counter(e.kind for e in graph.edges)["call"] == 32
    assert any(e.caller.endswith("src/main:main") and e.callee.endswith("Cart::new") for e in graph.edges)
