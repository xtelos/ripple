"""TypeScript support is an optional extra: the base install must neither need it nor load it.

These tests run with or without tree-sitter installed (the missing parser is simulated).
"""

import subprocess
import sys
from pathlib import Path

import pytest

from ripple import ts_indexer
from ripple.indexer import build_graph
from ripple.profile import TYPESCRIPT

ROOT = Path(__file__).resolve().parent.parent


def run_python(code: str) -> str:
    """The output of `python -c code` in a fresh interpreter that imports ripple from this checkout."""
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, cwd=ROOT, env={"PYTHONPATH": str(ROOT)}, check=False
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def test_importing_ripple_and_indexing_python_loads_no_tree_sitter(tmp_path):
    (tmp_path / "m.py").write_text("def f():\n    pass\n\ndef g():\n    f()\n")
    code = (
        "import sys, ripple, ripple.indexer, ripple.cache, ripple.queries, ripple.checker, ripple.resolver\n"
        "from ripple.profile import PYTHON, TYPESCRIPT\n"
        "from ripple.ts_modules import ModuleMap\n"
        f"graph = ripple.indexer.build_graph({str(tmp_path)!r})\n"
        "loaded = sorted(m for m in sys.modules if m.split('.')[0] in ('tree_sitter', 'tree_sitter_typescript'))\n"
        "print(len(graph.edges), loaded, 'ripple.ts_indexer' in sys.modules)\n"
    )
    assert run_python(code) == "1 [] False"  # the Python path does not even import the TypeScript front end


def test_importing_the_typescript_front_end_loads_the_parser_only_when_a_file_is_parsed(tmp_path):
    code = (
        "import sys, ripple.ts_indexer\n"
        "print(sorted(m for m in sys.modules if m.split('.')[0] in ('tree_sitter', 'tree_sitter_typescript')))\n"
    )
    assert run_python(code) == "[]"


def test_a_missing_parser_is_a_clear_error_that_names_the_extra(make_repo, monkeypatch):
    repo = make_repo({"src/a.ts": "export function f() {}\n"})
    monkeypatch.setattr(ts_indexer, "_PARSERS", None)
    monkeypatch.setitem(sys.modules, "tree_sitter", None)  # `import tree_sitter` now raises ImportError
    monkeypatch.setitem(sys.modules, "tree_sitter_typescript", None)
    with pytest.raises(ts_indexer.TypeScriptNotInstalled) as raised:
        build_graph(repo, TYPESCRIPT)
    message = str(raised.value)
    assert "pip install 'ripple-code[typescript]'" in message
    assert "TypeScript support is not installed" in message
    assert isinstance(raised.value, ImportError)


def test_a_missing_parser_does_not_stop_python_from_being_indexed(make_repo, monkeypatch):
    repo = make_repo({"m.py": "def f():\n    pass\n"})
    monkeypatch.setattr(ts_indexer, "_PARSERS", None)
    monkeypatch.setitem(sys.modules, "tree_sitter", None)
    assert [s.id for s in build_graph(repo).symbols.values() if s.kind == "function"] == ["m.f"]


def test_asking_for_typescript_says_what_is_missing_even_when_there_is_nothing_to_read(make_repo, monkeypatch):
    repo = make_repo({"m.py": "def f():\n    pass\n"})  # no .ts file at all
    monkeypatch.setattr(ts_indexer, "_PARSERS", None)
    monkeypatch.setitem(sys.modules, "tree_sitter", None)
    monkeypatch.setitem(sys.modules, "tree_sitter_typescript", None)
    with pytest.raises(ts_indexer.TypeScriptNotInstalled):
        build_graph(repo, TYPESCRIPT)  # an empty graph would look like "no TypeScript here" and it is not
