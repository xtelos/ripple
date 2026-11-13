"""The cache and the trusted derives: a derive is trusted from what the manifests and the lock say, so an edit to the lock (the versions
it lists decide the serde_derive that runs a precompiled binary, and which helper attributes are certain) rebuilds the graph, and a
graph cached before the rule existed, which has every call beside such a derive unresolved, is not served.
"""

import json
from pathlib import Path

import pytest

pytest.importorskip("tree_sitter_rust")

from test_rs_derive_trust import CIO, SERDE, lib, lock  # noqa: E402
from test_rs_derive_trust_rules import HERE  # noqa: E402

from ripple import __version__  # noqa: E402
from ripple.cache import CACHE_FORMAT, cache_file, fingerprint, load_graph, profile_key  # noqa: E402
from ripple.indexer import build_graph  # noqa: E402
from ripple.profile import RUST  # noqa: E402

SRC = "use serde::Serialize;\n#[derive(Serialize)]\npub struct S;\n" + HERE
EDGE = {("src/lib:g", "src/lib:<T as Tr>::make")}


def calls(graph):
    return {(e.caller, e.callee) for e in graph.edges if e.kind == "call"}


def locked(version):
    return lock(("serde", "1.0.228", CIO), ("serde_derive", version, CIO))


def test_the_format_moved_with_the_rule():
    assert CACHE_FORMAT == f"{__version__}-28"


def test_the_lock_beside_the_manifest_is_fingerprinted_for_the_derives_too(make_repo):
    repo = make_repo(lib(SRC, files={"Cargo.lock": locked("1.0.228")}))
    assert "Cargo.lock" in fingerprint(repo, RUST)


@pytest.mark.parametrize("before, after", [("1.0.228", "1.0.180"), ("1.0.180", "1.0.228")])
def test_editing_the_serde_derive_version_in_the_lock_rebuilds_the_graph_with_the_new_verdict(make_repo, tmp_path, before, after):
    repo = make_repo(lib(SRC, files={"Cargo.lock": locked(before)}))
    cache = tmp_path / "cache"
    graph, cached = load_graph(repo, cache, profile=RUST)
    want_before = EDGE if before == "1.0.228" else set()
    assert not cached and calls(graph) == want_before
    assert load_graph(repo, cache, profile=RUST)[1] is True
    Path(repo, "Cargo.lock").write_text(locked(after) + "\n# edited\n")
    graph, cached = load_graph(repo, cache, profile=RUST)
    assert not cached and calls(graph) == (EDGE if after == "1.0.228" else set())


def test_adding_and_deleting_the_lock_rebuilds_the_graph_with_the_new_verdict(make_repo, tmp_path):
    repo = make_repo(lib(SRC))
    cache = tmp_path / "cache"
    assert calls(load_graph(repo, cache, profile=RUST)[0]) == EDGE  # no lock: the manifests alone
    Path(repo, "Cargo.lock").write_text(locked("1.0.180"))
    graph, cached = load_graph(repo, cache, profile=RUST)
    assert not cached and calls(graph) == set()
    Path(repo, "Cargo.lock").unlink()
    graph, cached = load_graph(repo, cache, profile=RUST)
    assert not cached and calls(graph) == EDGE


def test_a_lock_above_the_package_in_a_workspace_is_the_one_whose_versions_count(make_repo, tmp_path):
    files = {
        "Cargo.toml": '[workspace]\nmembers = ["app"]\n',
        "app/Cargo.toml": lib(SRC, SERDE)["Cargo.toml"].replace('name = "case"', 'name = "app"'),
        "app/src/lib.rs": SRC,
        "Cargo.lock": locked("1.0.228"),
    }
    repo = make_repo(files)
    cache = tmp_path / "cache"
    assert calls(load_graph(repo, cache, profile=RUST)[0]) == {("app/src/lib:g", "app/src/lib:<T as Tr>::make")}
    Path(repo, "Cargo.lock").write_text(locked("1.0.175"))
    graph, cached = load_graph(repo, cache, profile=RUST)
    assert not cached and calls(graph) == set()


def test_a_graph_cached_before_derives_were_trusted_is_not_served_for_a_rust_repo(make_repo, tmp_path):
    repo = make_repo(lib(SRC))
    cache = tmp_path / "cache"
    old = {"format": f"{__version__}-19", "files": fingerprint(repo, RUST), "graph": build_graph(repo, RUST).to_dict(), "profile": profile_key(RUST)}
    old["graph"]["edges"] = []  # what the code before the rule drew for this repo
    written = cache_file(Path(repo).resolve(), cache, RUST)
    written.parent.mkdir(parents=True, exist_ok=True)
    written.write_text(json.dumps(old))
    graph, cached = load_graph(repo, cache, profile=RUST)
    assert not cached and calls(graph) == EDGE
    assert load_graph(repo, cache, profile=RUST)[1] is True
