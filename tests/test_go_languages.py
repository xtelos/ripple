"""Go beside the other languages: the registry entry, the cache, the command line and the queries on Go ids, a repo of
several languages, and an install without the Go parser.

The tests that read Go need tree-sitter-go (pip install 'ripple-code[go]') and are skipped without it; the ones about the
missing parser run either way (it is simulated).
"""

import importlib.util
import sys
from pathlib import Path

import pytest
from conftest import FIXTURES

from ripple import go_indexer, registry
from ripple.cache import fingerprint, load_graph, load_repo, profile_key
from ripple.cli import main
from ripple.languages import RepoError, check_profile, module_collisions
from ripple.profile import GO, PYTHON, TYPESCRIPT
from ripple.queries import callers, find_symbol

HAS_GO = importlib.util.find_spec("tree_sitter_go") is not None and importlib.util.find_spec("tree_sitter") is not None
needs_go = pytest.mark.skipif(not HAS_GO, reason="Go support is not installed (pip install 'ripple-code[go]')")

SHOP = FIXTURES / "go" / "shop"
CART = "example.com/shop/cart"

PACKAGE = {
    "go.mod": "module ex.com/m\n\ngo 1.22\n",
    "a.go": "package m\n\nfunc A() {}\n",
    "b.go": "package m\n\nfunc B() { A() }\n",
    "sub/s.go": 'package sub\n\nimport "ex.com/m"\n\nfunc S() { m.A() }\n',
}


# ---- the registry entry and the cache ---------------------------------------------------------------


def test_go_is_registered_last_and_is_the_only_language_that_folds_files():
    assert list(registry.LANGUAGES) == ["python", "typescript", "javascript", "go"]
    assert registry.language_of("x/y.go").label == "Go"
    assert [name for name, language in registry.LANGUAGES.items() if language.folds_files] == ["go"]
    assert registry.folds_files("pkg/a.go") and not registry.folds_files("a.py") and not registry.folds_files("a.ts")


def test_the_go_profile_adds_no_field_and_leaves_the_other_cache_keys_as_they_were():
    """cache.profile_key hashes every profile field: a new field, or a changed value, would change these keys and
    throw away every existing cache. They are the values origin/main computed. The TypeScript key hashes the constants
    of the profile's functions as the interpreter compiled them, so it has one value per Python version: only the
    versions pinned here are held to theirs."""
    assert profile_key(PYTHON) == ""
    known = {(3, 10): "typescript-c270b6d26bc3", (3, 12): "typescript-d69f1eeee6ab"}
    if sys.version_info[:2] in known:
        assert profile_key(TYPESCRIPT) == known[sys.version_info[:2]]
    assert profile_key(GO).startswith("go-") and profile_key(GO) not in (profile_key(PYTHON), profile_key(TYPESCRIPT))


def test_the_go_profile_has_the_values_a_go_front_end_reads():
    assert GO.source_suffixes == (".go",) and GO.excluded_suffixes == ()
    assert GO.config_files == ("go.mod", "go.work")
    assert {"len", "append", "error", "nil"} <= GO.builtin_names
    assert GO.builtin_types == frozenset()  # the standard library's types are read from go_stdlib.py, not listed by hand
    assert GO.constructor_name == ""  # Go has no constructors: NewT is a plain function


@needs_go
def test_editing_a_go_mod_rebuilds_the_index_and_so_does_a_go_work_or_a_nested_one(make_repo, tmp_path):
    repo = make_repo(PACKAGE)
    assert load_graph(repo, profile=GO)[1] is False
    assert load_graph(repo, profile=GO)[1] is True
    (repo / "go.mod").write_text("module ex.com/renamed\n\ngo 1.22\n")
    graph, cached = load_graph(repo, profile=GO)
    assert cached is False and "ex.com/renamed:A" in graph.symbols  # the module path is in every id
    assert load_graph(repo, profile=GO)[1] is True
    (repo / "go.work").write_text("go 1.22\nuse .\n")
    assert load_graph(repo, profile=GO)[1] is False
    assert load_graph(repo, profile=GO)[1] is True
    (repo / "sub" / "go.mod").write_text("module ex.com/sub\n")
    graph, cached = load_graph(repo, profile=GO)
    assert cached is False and "ex.com/sub:S" in graph.symbols
    assert sorted(k for k in fingerprint(repo, GO) if k.endswith(("go.mod", "go.work"))) == ["go.mod", "go.work", "sub/go.mod"]


# ---- the command line and the queries ----------------------------------------------------------------


@needs_go
@pytest.mark.parametrize("lang", ["go", "auto"])
def test_the_commands_answer_on_a_go_repo(capsys, lang):
    repo = str(SHOP)
    assert main(["index", repo, "--lang", lang]) == 0
    out = capsys.readouterr().out
    assert "Go: " in out and "could not parse" in out and "pricing/ptr.go" in out
    assert "files share a module name" not in out  # a package is many files by design

    assert main(["callers", f"{CART}:Cart.Add", "--repo", repo, "--lang", lang]) == 0
    out = capsys.readouterr().out
    assert f"callers of {CART}:Cart.Add (4):" in out and f"{CART}:Run" in out

    assert main(["callees", f"{CART}:Checkout", "--repo", repo, "--lang", lang]) == 0
    out = capsys.readouterr().out
    assert f"{CART}:Base.Lock" in out and "may be an interface" in out  # err.Error stays unresolved, with its reason

    assert main(["impact", "internal/money:Sum", "--repo", repo, "--lang", lang, "--depth", "3"]) == 0
    out = capsys.readouterr().out
    assert "example.com/shop/pricing:Total" in out and f"{CART}:Cart.Total" in out and f"{CART}:Checkout" in out
    assert "money.Sum  in example.com/shop/pricing:Ptr" in out  # a same-name call in an unparsable function is listed to check

    assert main(["path", f"{CART}:Run", f"{CART}:Audited.Record", "--repo", repo, "--lang", lang]) == 0
    out = capsys.readouterr().out
    assert f"{CART}:Run" in out and f"{CART}:Cart.Add" in out and f"{CART}:Audited.Record" in out


@needs_go
@pytest.mark.parametrize(
    "query",
    [
        f"{CART}:Cart.Add",  # the id
        "Cart.Add",  # Type.Method
        "cart:Cart.Add",  # the last directory and the member
        "cart/cart.go:Cart.Add",  # the file and the member
    ],
)
def test_a_go_id_is_found_by_its_import_path_by_type_and_method_and_by_a_path(query):
    graph = load_repo(SHOP, lang="go").graph
    assert find_symbol(graph, query) == {"symbol": f"{CART}:Cart.Add"}


@needs_go
def test_a_bare_name_a_directory_and_a_package_are_answered_or_listed_never_guessed():
    graph = load_repo(SHOP, lang="go").graph
    assert find_symbol(graph, "NewWithErr") == {"symbol": f"{CART}:NewWithErr"}
    ambiguous = find_symbol(graph, "Total")  # Cart.Total and pricing.Total
    assert ambiguous["candidates"] == [f"{CART}:Cart.Total", "example.com/shop/pricing:Total"]
    directory = find_symbol(graph, "cart/")  # a directory holds a package and its external test package
    assert directory["candidates"] == [CART, f"{CART}_test"]
    assert find_symbol(graph, "internal/money/") == {"symbol": "example.com/shop/internal/money"}
    assert find_symbol(graph, "example.com/shop/internal/money") == {"symbol": "example.com/shop/internal/money"}
    assert find_symbol(graph, "money.go:Sum") == {"symbol": "example.com/shop/internal/money:Sum"}
    assert "error" in find_symbol(graph, "cart:Nope")


@needs_go
def test_a_build_variant_is_asked_about_by_the_plain_name_and_found_with_both_variants():
    graph = load_repo(SHOP, lang="go").graph
    found = find_symbol(graph, f"{CART}:platform")
    assert found == {"symbol": f"{CART}:platform"}
    assert {e["symbol"] for e in callers(graph, f"{CART}:platform")["callers"]} == {f"{CART}:Platform"}


@needs_go
def test_files_of_one_package_are_not_a_collision_but_two_languages_files_still_would_be(make_repo):
    graph = load_repo(make_repo(PACKAGE)).graph
    assert module_collisions(graph) == {} and graph.module_collisions() != {}  # the model counts files, the registry knows better


# ---- a repo of several languages ---------------------------------------------------------------------


@needs_go
def test_a_repo_with_go_and_python_merges_without_a_false_ambiguity(make_repo):
    repo = make_repo({**PACKAGE, "tools/gen.py": "def a():\n    pass\n\ndef b():\n    a()\n", "A.py": "def A():\n    pass\n"})
    result = load_repo(repo)
    assert result.warnings == [] and [(x.name, x.found) for x in result.languages] == [("python", 2), ("go", 3)]
    ids = set(result.graph.symbols)
    assert {"ex.com/m:A", "ex.com/m:B", "tools.gen.a", "A.A"} <= ids and result.graph.ambiguous_ids == {}
    assert check_profile(repo).name == "python"  # the first language present runs the tests


@needs_go
def test_a_repo_with_go_and_typescript_keeps_both_graphs(make_repo):
    repo = make_repo({**PACKAGE, "web/src/util.ts": "export function a() {}\nexport function b() { a(); }\n"})
    pytest.importorskip("tree_sitter_typescript")
    result = load_repo(repo)
    assert result.warnings == []
    assert {"ex.com/m:B", "web/src/util:b"} <= set(result.graph.symbols)
    assert sorted((e.caller, e.callee) for e in result.graph.edges) == [
        ("ex.com/m/sub:S", "ex.com/m:A"), ("ex.com/m:B", "ex.com/m:A"), ("web/src/util:b", "web/src/util:a"),
    ]  # fmt: skip


@needs_go
def test_a_go_id_and_a_typescript_id_that_collide_are_listed_not_merged(make_repo):
    pytest.importorskip("tree_sitter_typescript")
    repo = make_repo({"go.mod": "module web/src/util\n", "util.go": "package util\nfunc a() {}\n", "web/src/util.ts": "export function a() {}\n"})
    result = load_repo(repo)
    # the TypeScript module web/src/util and its function are the ids the Go package web/src/util and its function would be
    assert set(result.graph.ambiguous_ids) == {"web/src/util", "web/src/util:a"}
    assert result.graph.symbols["web/src/util:a"].file == "web/src/util.ts"  # the earlier language keeps the id
    assert len(result.warnings) == 1 and "kept the TypeScript symbol and left out the Go one" in result.warnings[0]
    assert "both languages define" in find_symbol(result.graph, "web/src/util:a")["error"]


# ---- an install without the Go parser ----------------------------------------------------------------


@pytest.fixture
def no_go_parser(monkeypatch):
    monkeypatch.setattr(go_indexer, "_PARSER", None)
    monkeypatch.setitem(sys.modules, "tree_sitter_go", None)  # `import tree_sitter_go` now raises ImportError


def test_a_go_only_repo_without_the_parser_is_refused_with_the_extra_named(make_repo, no_go_parser):
    repo = make_repo(PACKAGE)
    with pytest.raises(RepoError) as raised:
        load_repo(repo)
    message = str(raised.value)
    assert "Go files need Go support" in message and "pip install 'ripple-code[go]'" in message


def test_a_mixed_python_and_go_repo_answers_for_python_and_says_go_was_skipped(make_repo, no_go_parser):
    repo = make_repo({**PACKAGE, "m.py": "def f():\n    pass\n\ndef g():\n    f()\n"})
    result = load_repo(repo)
    assert [(e.caller, e.callee) for e in result.graph.edges] == [("m.g", "m.f")]
    assert [language.name for language in result.languages if language.skipped] == ["go"]
    assert len(result.warnings) == 1 and "Go was not indexed (3 files found)" in result.warnings[0]
    assert "These answers cover the Python files only" in result.warnings[0] and "ripple-code[go]" in result.warnings[0]


def test_the_cli_says_go_was_skipped_on_a_mixed_repo(make_repo, no_go_parser, capsys):
    repo = make_repo({**PACKAGE, "m.py": "def f():\n    pass\n"})
    assert main(["index", str(repo)]) == 3  # answered, but something was left out
    assert "warning: Go was not indexed" in capsys.readouterr().out


def test_importing_ripple_and_the_go_front_end_loads_no_parser():
    import subprocess

    root = Path(__file__).resolve().parent.parent
    code = (
        "import sys, ripple, ripple.cache, ripple.queries, ripple.languages, ripple.go_indexer, ripple.go_modules\n"
        "print(sorted(m for m in sys.modules if m.split('.')[0] in ('tree_sitter', 'tree_sitter_go', 'tree_sitter_typescript')))\n"
    )
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=root, env={"PYTHONPATH": str(root)}, check=False)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "[]"


def test_the_go_extra_is_declared_beside_the_typescript_one():
    text = (Path(__file__).resolve().parent.parent / "pyproject.toml").read_text()
    assert 'go = ["tree-sitter>=0.25,<0.27", "tree-sitter-go>=0.25,<0.26"]' in text
