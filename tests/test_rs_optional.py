"""Rust support is an optional extra: the base install must neither need it nor load it.

These tests run with or without tree-sitter-rust installed (the missing parser is simulated).
"""

import subprocess
import sys
from pathlib import Path

import pytest

from ripple import rs_indexer
from ripple.cache import load_repo
from ripple.indexer import build_graph
from ripple.languages import RepoError
from ripple.profile import RUST

ROOT = Path(__file__).resolve().parent.parent
INSTALL = "pip install 'ripple-code[rust]'"
CARGO = '[package]\nname = "app"\nversion = "0.1.0"\nedition = "2021"\n'


def run_python(code: str) -> str:
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, cwd=ROOT, env={"PYTHONPATH": str(ROOT)}, check=False
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


@pytest.fixture
def no_parser(monkeypatch):
    monkeypatch.setitem(sys.modules, "tree_sitter_rust", None)  # `import tree_sitter_rust` now raises ModuleNotFoundError
    monkeypatch.setattr(rs_indexer, "_PARSER", None)


def test_importing_the_rust_front_end_loads_no_parser():
    code = (
        "import sys, ripple.rs_indexer, ripple.rs_modules\n"
        "print(sorted(m for m in sys.modules if m.split('.')[0] in ('tree_sitter', 'tree_sitter_rust', 'tomllib', 'tomli')))\n"
    )
    assert run_python(code) == "[]"


def test_indexing_python_or_asking_for_a_rust_files_cache_key_loads_no_rust_front_end(tmp_path):
    (tmp_path / "m.py").write_text("def f():\n    pass\n")
    code = (
        "import sys, ripple.indexer, ripple.cache, ripple.queries\n"
        f"graph = ripple.indexer.build_graph({str(tmp_path)!r})\n"
        "print(len(graph.symbols), 'ripple.rs_indexer' in sys.modules, 'tree_sitter_rust' in sys.modules)\n"
    )
    assert run_python(code) == "2 False False"  # the module, and the function


def test_a_rust_only_repo_without_the_parser_is_an_error_that_names_the_extra(make_repo, no_parser):
    repo = make_repo({"Cargo.toml": CARGO, "src/lib.rs": "pub fn f() {}\n"})
    with pytest.raises(RepoError) as raised:
        load_repo(repo)
    assert "its 1 Rust file need Rust support" in str(raised.value) and INSTALL in str(raised.value)


def test_the_front_end_raises_its_own_not_installed_error_from_index_files(make_repo, no_parser):
    repo = make_repo({"Cargo.toml": CARGO, "src/lib.rs": "pub fn f() {}\n"})
    with pytest.raises(rs_indexer.NotInstalled) as raised:
        build_graph(repo, RUST)
    assert "missing module: tree_sitter_rust" in str(raised.value)


def test_a_mixed_repo_answers_for_python_and_says_rust_was_skipped(make_repo, no_parser):
    repo = make_repo({"app.py": "def total():\n    return 1\n", "Cargo.toml": CARGO, "src/lib.rs": "pub fn f() {}\npub fn g() {}\n"})
    result = load_repo(repo)
    assert "app.total" in result.graph.symbols and not any(":" in s for s in result.graph.symbols)
    assert result.incomplete and "Rust was not indexed (1 file found)" in result.skipped[0] and INSTALL in result.skipped[0]
    assert "These answers cover the Python files only" in result.skipped[0]
    assert [(language.name, language.skipped) for language in result.languages] == [("python", False), ("rust", True)]


def test_a_mixed_repo_with_the_parser_answers_for_both(make_repo):
    pytest.importorskip("tree_sitter_rust")
    repo = make_repo({"app.py": "def total():\n    return 1\n", "Cargo.toml": CARGO, "src/lib.rs": "pub fn f() {}\n"})
    result = load_repo(repo)
    assert {"app.total", "src/lib:f"} <= set(result.graph.symbols) and not result.incomplete


def test_a_tree_sitter_that_does_not_fit_is_the_same_error_with_the_versions_it_needs(monkeypatch):
    class Broken:
        @staticmethod
        def language():
            raise ValueError("Incompatible Language version 99")

    pytest.importorskip("tree_sitter")
    monkeypatch.setitem(sys.modules, "tree_sitter_rust", Broken)
    monkeypatch.setattr(rs_indexer, "_PARSER", None)
    with pytest.raises(rs_indexer.NotInstalled) as raised:
        rs_indexer.load_parser()
    assert "do not fit" in str(raised.value) and "tree-sitter-rust>=0.24.2,<0.25" in str(raised.value)


def test_a_cargo_target_directory_is_not_a_source_directory(tmp_path):
    (tmp_path / "Cargo.toml").write_text(CARGO)
    out = tmp_path / "target" / "debug" / "build" / "x" / "out"
    out.mkdir(parents=True)
    (out / "gen.rs").write_text("fn g() {}\n")
    (tmp_path / "other" / "target").mkdir(parents=True)  # no Cargo.toml beside it: an ordinary directory
    (tmp_path / "other" / "target" / "real.rs").write_text("fn r() {}\n")
    assert rs_indexer.reads_as_source(out / "gen.rs", RUST) is False
    assert rs_indexer.reads_as_source(tmp_path / "other" / "target" / "real.rs", RUST) is True
