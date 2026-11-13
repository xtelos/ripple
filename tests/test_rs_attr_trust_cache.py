"""The cache of a Rust repo and the files a trusted attribute macro is proven from: Cargo.lock and the cargo config files.

An edit to either (or adding or deleting one) changes whether `tokio::main` is the published macro, so it must rebuild the
graph. And a graph cached before the rule existed has every call under such an attribute unresolved: CACHE_FORMAT moved
with it, so that cache is a miss.
"""

import json
from pathlib import Path

import pytest

from ripple import __version__
from ripple.cache import cache_file, fingerprint, load_graph, profile_key
from ripple.profile import RUST
from ripple.registry import front_end

CARGO = '[package]\nname = "app"\nversion = "0.1.0"\nedition = "2021"\n'
TOKIO = CARGO + '[dependencies]\ntokio = "1"\n'
MAIN = "#[tokio::main]\nasync fn main() {\n    helper();\n}\nfn helper() {}\n"
CRATES_IO = "registry+https://github.com/rust-lang/crates.io-index"
LOCK = 'version = 3\n[[package]]\nname = "tokio"\nversion = "1.32.0"\nsource = "%s"\n[[package]]\nname = "tokio-macros"\nversion = "2.1.0"\nsource = "%s"\n'
EDGE = {("src/main:main", "src/main:helper")}


def edges(graph):
    return {(e.caller, e.callee) for e in graph.edges}


def test_a_lock_above_a_source_file_and_every_cargo_config_file_of_the_repo_are_fingerprinted(make_repo):
    repo = make_repo(
        {
            "Cargo.toml": '[workspace]\nmembers = ["a"]\n',
            "Cargo.lock": "version = 3\n",
            ".cargo/config.toml": "[build]\njobs = 1\n",
            "a/Cargo.toml": CARGO,
            "a/.cargo/config": "[build]\njobs = 1\n",
            "a/src/lib.rs": "pub fn f() {}\n",
            "b/.cargo/config.toml": "[build]\njobs = 1\n",  # a directory above no source file: cargo run from there reads it
            "a/src/deep/.cargo/config.toml": "[build]\njobs = 1\n",  # below the package, where cargo may be run
            "b/Cargo.lock": "version = 3\n",  # no manifest beside it: no lock of any package
            "node_modules/x/.cargo/config.toml": "[build]\njobs = 1\n",  # not walked
        }
    )
    sources = [repo / "a/src/lib.rs"]
    configs = [".cargo/config.toml", "Cargo.lock", "Cargo.toml", "a/.cargo/config", "a/Cargo.toml", "a/src/deep/.cargo/config.toml", "b/.cargo/config.toml"]
    assert front_end(RUST).config_files(repo, sources) == configs
    assert sorted(fingerprint(repo, RUST, sources)) == sorted([*configs, "a/src/lib.rs"])


def test_a_config_file_above_the_package_is_fingerprinted_where_no_manifest_sits_beside_it(make_repo):
    repo = make_repo({".cargo/config.toml": "[build]\njobs = 1\n", "app/Cargo.toml": CARGO, "app/src/lib.rs": "pub fn f() {}\n"})
    assert front_end(RUST).config_files(repo, [repo / "app/src/lib.rs"]) == [".cargo/config.toml", "app/Cargo.toml"]


def test_a_config_file_that_appears_in_a_directory_with_no_source_rebuilds_the_graph(make_repo, tmp_path):
    pytest.importorskip("tree_sitter_rust")
    repo = make_repo({"Cargo.toml": TOKIO, "src/main.rs": MAIN})
    cache = tmp_path / "cache"
    graph, cached = load_graph(repo, cache, profile=RUST)
    assert not cached and edges(graph) == EDGE
    assert load_graph(repo, cache, profile=RUST)[1] is True
    target = Path(repo) / "docs/.cargo/config.toml"
    target.parent.mkdir(parents=True)
    target.write_text('paths = ["../tokio-macros"]\n')
    graph, cached = load_graph(repo, cache, profile=RUST)
    assert not cached and edges(graph) == set()


def test_a_graph_cached_before_attribute_macros_were_trusted_is_not_served_for_a_rust_repo(make_repo, tmp_path):
    pytest.importorskip("tree_sitter_rust")
    from ripple.indexer import build_graph

    repo = make_repo({"Cargo.toml": TOKIO, "src/main.rs": MAIN})
    cache = tmp_path / "cache"
    old = {"format": f"{__version__}-17", "files": fingerprint(repo, RUST), "graph": build_graph(repo, RUST).to_dict(), "profile": profile_key(RUST)}
    written = cache_file(Path(repo).resolve(), cache, RUST)
    written.parent.mkdir(parents=True, exist_ok=True)
    written.write_text(json.dumps(old))
    graph, cached = load_graph(repo, cache, profile=RUST)
    assert not cached
    assert edges(graph) == EDGE
    assert load_graph(repo, cache, profile=RUST)[1] is True


@pytest.mark.parametrize(
    "path, written, after",
    [
        (".cargo/config.toml", 'paths = ["../tokio-macros"]\n', set()),
        (".cargo/config", '[source.crates-io]\nreplace-with = "v"\n[source.v]\ndirectory = "vendor"\n', set()),
        (".cargo/config.toml", '[resolver]\nlockfile-path = "x/Cargo.lock"\n', set()),
        ("Cargo.lock", LOCK % (CRATES_IO, "git+https://example.invalid/m#abc"), set()),
        ("Cargo.lock", LOCK % (CRATES_IO, CRATES_IO), EDGE),
    ],
    ids=["config paths", "config source", "config lockfile-path", "lock from git", "lock from crates.io"],
)
def test_adding_editing_and_deleting_a_lock_or_a_config_file_rebuilds_the_graph_with_the_new_verdict(make_repo, tmp_path, path, written, after):
    pytest.importorskip("tree_sitter_rust")
    repo = make_repo({"Cargo.toml": TOKIO, "src/main.rs": MAIN})
    cache = tmp_path / "cache"
    graph, cached = load_graph(repo, cache, profile=RUST)
    assert not cached and edges(graph) == EDGE
    assert load_graph(repo, cache, profile=RUST)[1] is True
    target = Path(repo) / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(written)
    graph, cached = load_graph(repo, cache, profile=RUST)
    assert not cached and edges(graph) == after
    assert load_graph(repo, cache, profile=RUST)[1] is True
    target.write_text(written + "\n# edited\n")
    assert load_graph(repo, cache, profile=RUST)[1] is False
    target.unlink()
    graph, cached = load_graph(repo, cache, profile=RUST)
    assert not cached and edges(graph) == EDGE
