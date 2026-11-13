"""The cache of a Rust repo: its own key, and a Cargo.toml that is part of what it is keyed on.

The first two tests pin the keys of the languages that were there before: a cache written by an earlier ripple for
Python or TypeScript must still be read, so those keys must not move when a language is added (test_go_languages.py
holds the same line for JavaScript and Go).
"""

import json
import os
import sys
from pathlib import Path

import pytest

from ripple.cache import cache_file, fingerprint, load_graph, profile_key
from ripple.profile import GO, JAVASCRIPT, PYTHON, RUST, TYPESCRIPT
from ripple.registry import front_end

CARGO = '[package]\nname = "app"\nversion = "0.1.0"\nedition = "2021"\n'


def test_the_python_key_is_empty_as_it_always_was():
    assert profile_key(PYTHON) == ""


# The hash covers the constants of a profile's functions as the interpreter compiled them, so it is one value per Python
# minor version: only the versions pinned here are held to theirs (the values origin/main computed for each).
def test_the_typescript_key_is_what_it_was_before_rust():
    known = {(3, 10): "typescript-c270b6d26bc3", (3, 12): "typescript-d69f1eeee6ab"}
    if sys.version_info[:2] in known:
        assert profile_key(TYPESCRIPT) == known[sys.version_info[:2]]


def test_rust_has_a_key_of_its_own_and_its_own_cache_file(tmp_path):
    key = profile_key(RUST)
    others = (PYTHON, TYPESCRIPT, JAVASCRIPT, GO)
    assert key.startswith("rust-") and key not in [profile_key(p) for p in others]
    files = {cache_file(tmp_path, tmp_path / "c", p) for p in (*others, RUST)}
    assert len(files) == 5


def test_every_cargo_manifest_above_a_source_file_is_fingerprinted(make_repo):
    repo = make_repo(
        {
            "Cargo.toml": '[workspace]\nmembers = ["a"]\n',
            "a/Cargo.toml": CARGO,
            "a/src/lib.rs": "pub fn f() {}\n",
            "unrelated/Cargo.toml": CARGO,  # holds no source and is above none: no package, but a `[patch]` in it may name a dependency (Rule P)
        }
    )
    found = fingerprint(repo, RUST, [repo / "a/src/lib.rs"])
    assert sorted(found) == ["Cargo.toml", "a/Cargo.toml", "a/src/lib.rs", "unrelated/Cargo.toml"]
    assert front_end(RUST).config_files(repo, [repo / "a/src/lib.rs"]) == ["Cargo.toml", "a/Cargo.toml", "unrelated/Cargo.toml"]


def test_a_path_with_no_relative_form_is_keyed_by_its_absolute_path_not_a_crash(make_repo, tmp_path_factory, monkeypatch):
    """On Windows os.path.relpath raises ValueError for a path on another drive, and a path dependency may be an absolute path on one: the
    Cargo.toml of it is fingerprinted under its absolute path, and every other key is as it was."""
    outside = tmp_path_factory.mktemp("elsewhere")
    (outside / "Cargo.toml").write_text('[package]\nname = "far"\nversion = "0.1.0"\nedition = "2021"\n')
    repo = make_repo({"Cargo.toml": CARGO + f'[dependencies]\nfar = {{ path = "{outside.as_posix()}" }}\n', "src/lib.rs": "pub fn f() {}\n"})
    expected = fingerprint(repo, RUST)
    real = os.path.relpath
    relative = Path(real(outside / "Cargo.toml", repo)).as_posix()
    assert relative in expected  # as relpath writes it where it can

    def no_other_drive(path, start=os.curdir):
        if not str(path).startswith(str(repo)):
            raise ValueError("path is on mount 'D:', start on mount 'C:'")
        return real(path, start)

    monkeypatch.setattr(os.path, "relpath", no_other_drive)
    found = fingerprint(repo, RUST)  # does not raise
    absolute = (outside / "Cargo.toml").as_posix()
    assert found[absolute] == expected[relative]
    assert {k: v for k, v in found.items() if k != absolute} == {k: v for k, v in expected.items() if k != relative}


def test_editing_a_manifest_rebuilds_the_graph_and_the_new_crate_graph_is_used(make_repo, tmp_path):
    pytest.importorskip("tree_sitter_rust")
    files = {
        "Cargo.toml": '[workspace]\nmembers = ["app", "lib-a", "lib-b"]\n',
        "lib-a/Cargo.toml": '[package]\nname = "lib-a"\nversion = "0.1.0"\nedition = "2021"\n',
        "lib-a/src/lib.rs": "pub fn go() {}\n",
        "lib-b/Cargo.toml": '[package]\nname = "lib-b"\nversion = "0.1.0"\nedition = "2021"\n',
        "lib-b/src/lib.rs": "pub fn go() {}\n",
        "app/Cargo.toml": CARGO + '[dependencies]\nlib = { package = "lib-a", path = "../lib-a" }\n',
        "app/src/lib.rs": "pub fn run() { lib::go(); }\n",
    }
    repo = make_repo(files)
    cache = tmp_path / "cache"
    graph, cached = load_graph(repo, cache, profile=RUST)
    assert not cached and {(e.caller, e.callee) for e in graph.edges} == {("app/src/lib:run", "lib-a/src/lib:go")}
    assert load_graph(repo, cache, profile=RUST)[1] is True  # nothing changed: served from the cache
    manifest = Path(repo) / "app" / "Cargo.toml"
    manifest.write_text(manifest.read_text().replace("lib-a", "lib-b"))
    graph, cached = load_graph(repo, cache, profile=RUST)
    assert not cached and {(e.caller, e.callee) for e in graph.edges} == {("app/src/lib:run", "lib-b/src/lib:go")}
    saved = json.loads(cache_file(Path(repo).resolve(), cache, RUST).read_text())
    assert saved["profile"] == profile_key(RUST) and "app/Cargo.toml" in saved["files"]
