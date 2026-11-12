"""Java as a language of the registry: the optional extra, a repo that mixes it with Python, and the CLI.

The tests about a missing parser run everywhere (the parser is made unimportable); the ones that index Java are
skipped, with the reason, when tree-sitter-java is not installed.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import FIXTURES

from ripple import java_indexer, registry
from ripple.cache import load_repo
from ripple.cli import main
from ripple.indexer import build_graph
from ripple.languages import RepoError
from ripple.profile import JAVA

ROOT = Path(__file__).resolve().parent.parent
SHOP_JAVA = FIXTURES / "java" / "shop-java"
INSTALL = "pip install 'ripple-code[java]'"


def has_parser() -> bool:
    try:
        import tree_sitter  # noqa: F401
        import tree_sitter_java  # noqa: F401
    except ImportError:
        return False
    return True


needs_parser = pytest.mark.skipif(not has_parser(), reason=f"Java support is not installed ({INSTALL})")

PY = {"app/core.py": "def helper(xs):\n    return sum(xs)\n\ndef total(xs):\n    return helper(xs)\n"}
JAVA_FILES = {
    "src/main/java/shop/Cart.java": "package shop;\npublic class Cart {\n  int total() { return 1; }\n  int twice() { return total() * 2; }\n}\n",
    "src/main/java/shop/Main.java": "package shop;\npublic class Main { int run(Cart c) { return c.twice(); } }\n",
}


@pytest.fixture
def no_parser(monkeypatch):
    """tree-sitter cannot be imported, whether or not it is installed."""
    monkeypatch.setattr(java_indexer, "_PARSER", None)
    monkeypatch.setitem(sys.modules, "tree_sitter", None)
    monkeypatch.setitem(sys.modules, "tree_sitter_java", None)


def run_python(code: str) -> str:
    """The output of `python -c code` in a fresh interpreter that imports ripple from this checkout."""
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=ROOT, env={"PYTHONPATH": str(ROOT)}, check=False)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


# ---- the registry --------------------------------------------------------------------------------------------------


def test_java_is_registered_last_and_python_is_still_first():
    names = list(registry.LANGUAGES)
    assert names[0] == "python" and names[-1] == "java"
    assert registry.LANGUAGES["java"].profile is JAVA and registry.label("java") == "Java"
    assert registry.language_of("src/main/java/shop/Cart.java").profile is JAVA
    assert registry.language_of("src/main/java/shop/package-info.java").profile.name == "python"  # claimed by no profile: the default's


def test_java_adds_no_field_to_the_profile_and_gives_neutral_values_to_the_ones_it_does_not_use():
    from dataclasses import fields

    from ripple.profile import PYTHON

    assert [f.name for f in fields(JAVA)] == [f.name for f in fields(PYTHON)]
    assert JAVA.constructor_name == "<init>" and JAVA.config_files == () and JAVA.synchronous_callbacks == frozenset()
    assert "String" in JAVA.builtin_names and "List" not in JAVA.builtin_names  # java.lang only


# ---- the optional extra --------------------------------------------------------------------------------------------


def test_importing_ripple_and_indexing_python_loads_no_tree_sitter_and_no_java_front_end(tmp_path):
    (tmp_path / "m.py").write_text("def f():\n    pass\n\ndef g():\n    f()\n")
    code = (
        "import sys, ripple, ripple.indexer, ripple.cache, ripple.queries, ripple.checker, ripple.resolver, ripple.cli\n"
        f"graph = ripple.indexer.build_graph({str(tmp_path)!r})\n"
        "loaded = sorted(m for m in sys.modules if m.split('.')[0] in ('tree_sitter', 'tree_sitter_java'))\n"
        "print(len(graph.edges), loaded, 'ripple.java_indexer' in sys.modules, 'ripple.java_resolver' in sys.modules)\n"
    )
    assert run_python(code) == "1 [] False False"


def test_importing_the_java_front_end_loads_the_parser_only_when_a_file_is_parsed():
    code = "import sys, ripple.java_indexer, ripple.java_resolver\nprint(sorted(m for m in sys.modules if m.split('.')[0] in ('tree_sitter', 'tree_sitter_java')))\n"
    assert run_python(code) == "[]"


def test_a_missing_parser_is_a_clear_error_that_names_the_extra(make_repo, no_parser):
    repo = make_repo(JAVA_FILES)
    with pytest.raises(java_indexer.JavaNotInstalled) as raised:
        build_graph(repo, JAVA)
    assert INSTALL in str(raised.value) and "Java support is not installed" in str(raised.value)
    assert isinstance(raised.value, ImportError) and java_indexer.NotInstalled is java_indexer.JavaNotInstalled


def test_a_java_only_repo_without_the_parser_is_a_refusal_that_names_the_extra(make_repo, no_parser, capsys):
    repo = make_repo(JAVA_FILES)
    with pytest.raises(RepoError) as raised:
        load_repo(repo)
    assert INSTALL in str(raised.value) and "2 Java files need Java support" in str(raised.value)
    assert main(["index", str(repo)]) == 2
    err = capsys.readouterr().err
    assert INSTALL in err and "need Java support" in err and "Traceback" not in err


def test_a_mixed_python_and_java_repo_without_the_parser_answers_for_python_and_says_java_was_skipped(make_repo, no_parser, capsys):
    repo = make_repo({**PY, **JAVA_FILES})
    result = load_repo(repo)
    assert [language.name for language in result.languages] == ["python", "java"]
    assert result.languages[1].skipped and result.languages[1].files == 0 and result.languages[0].files == 1
    assert set(result.graph.symbols) == {"app.core", "app.core.helper", "app.core.total"}
    assert INSTALL in result.skipped[0] and "2 files" in result.skipped[0] and "Python files only" in result.skipped[0]
    assert main(["callers", "helper", "--repo", str(repo)]) == 3
    captured = capsys.readouterr()
    assert "app.core.total" in captured.out and INSTALL in captured.err


# ---- a repo with Java in it ----------------------------------------------------------------------------------------


@needs_parser
def test_a_mixed_repo_has_both_languages_and_no_edge_between_them(make_repo):
    result = load_repo(make_repo({**PY, **JAVA_FILES}))
    graph = result.graph
    assert [language.name for language in result.languages] == ["python", "java"] and not result.warnings
    python = {s for s in graph.symbols if s.startswith("app.")}
    java = {s for s in graph.symbols if s.startswith("shop.")}
    assert python and java
    assert all((e.caller in python) == (e.callee in python) for e in graph.edges)


@needs_parser
def test_an_id_python_and_java_both_define_stays_python_and_is_announced(make_repo, capsys):
    repo = make_repo(
        {
            "com/acme/shop/Cart.py": "class Cart:\n    def total(self):\n        return 1\n\ndef use(c):\n    return c.total()\n",
            "src/main/java/com/acme/shop/Cart.java": "package com.acme.shop;\npublic class Cart {\n  int total() { return 1; }\n  int twice() { return total(); }\n}\n",
        }
    )
    result = load_repo(repo)
    assert result.graph.ambiguous_ids == {
        "com.acme.shop.Cart": ["com/acme/shop/Cart.py", "src/main/java/com/acme/shop/Cart.java"]
    }
    assert result.graph.symbols["com.acme.shop.Cart"].file == "com/acme/shop/Cart.py"  # Python keeps the id
    assert result.graph.symbols["com.acme.shop.Cart.twice()"].file.endswith(".java")  # no Python id names it: it stays
    assert "kept the Python symbol and left out the Java one" in result.warnings[0] and "both languages" in result.warnings[0]
    assert main(["callers", "com.acme.shop.Cart", "--repo", str(repo)]) == 1  # not answered: both languages define it
    captured = capsys.readouterr()
    assert "both languages define" in captured.out and "warning:" in captured.err


@needs_parser
def test_the_cli_answers_a_java_repo_with_lang_java_and_with_auto(capsys):
    for lang in ("java", "auto"):
        assert main(["index", str(SHOP_JAVA), "--lang", lang]) == 0
        out = capsys.readouterr().out
        assert "18 files" in out and "resolved calls" in out and "could not parse (Java, 1 file)" in out
    assert main(["index", str(SHOP_JAVA), "--lang", "python"]) == 2  # a Java repo is no Python repo
    assert "no Python source files" in capsys.readouterr().err


@needs_parser
@pytest.mark.parametrize(
    "command, query, expected",
    [
        ("callers", "com.acme.shop.Cart.add(String)", ["com.acme.shop.BulkCart.fill()", "com.acme.shop.Chains.typed()"]),
        ("callers", "Cart.add(String,int)", ["com.acme.shop.CartTest.addsItems()"]),
        ("callers", "Cart.total", ["com.acme.shop.CartTest.addsItems()", "com.acme.shop.Reports.run()"]),  # the one method named so: no types needed
        ("callers", "BulkCart.<init>(int)", ["com.acme.shop.CartTest.addsItems()", "com.acme.shop.Reports.run()"]),
        ("callers", "Cart", ["com.acme.shop.Chains", "com.acme.shop.Reports.run()", "com.acme.shop.Cart.copy()"]),  # a class: its constructors' callers
        ("callers", "receipt", ["com.acme.shop.Cart.Line.show()", "com.acme.shop.Cart.loud()"]),  # a bare name
    ],
)
def test_callers_take_an_id_with_parameter_types_a_class_or_a_name(capsys, command, query, expected):
    assert main([command, query, "--repo", str(SHOP_JAVA), "--json"]) == 0
    found = {c["symbol"] for c in json.loads(capsys.readouterr().out)["callers"]}
    assert set(expected) <= found, found


@needs_parser
def test_a_name_that_is_an_overload_set_lists_its_candidates_in_the_cli(capsys):
    assert main(["callers", "Cart.add", "--repo", str(SHOP_JAVA)]) == 1
    out = capsys.readouterr().out
    assert "'Cart.add' is ambiguous" in out
    for candidate in ("Cart.add(Item)", "Cart.add(String)", "Cart.add(String,int)"):
        assert f"  com.acme.shop.{candidate}" in out


@needs_parser
@pytest.mark.parametrize("tail", ["shop/Cart.java", "acme/shop/Cart.java", "main/java/com/acme/shop/Cart.java", "Cart.java", "src/main/java/com/acme/shop/Cart.java"])
def test_a_java_file_is_found_by_the_tail_of_its_path(tail):
    from ripple.queries import find_symbol

    found = find_symbol(build_graph(SHOP_JAVA, JAVA), tail)
    assert found == {"symbol": "src/main/java/com/acme/shop/Cart.java"}, found


def test_only_java_writes_parameter_lists_and_the_registry_reads_it_by_file():
    assert {name: lang.parameter_ids for name, lang in registry.LANGUAGES.items()} == {
        "python": False,
        "typescript": False,
        "javascript": False,
        "go": False,
        "rust": False,
        "java": True,
    }
    assert registry.LANGUAGES["java"].path_ids and not registry.LANGUAGES["java"].colon_members
    assert ".java" in registry.path_suffixes()
    assert [registry.writes_parameter_lists(f) for f in ("a/B.java", "a/b.ts", "a/b.js", "a/b.go", "a/b.rs", "a/b.py")] == [True] + [False] * 5


def test_an_id_is_read_by_the_language_of_its_file_whichever_other_languages_are_registered():
    from ripple.model import Symbol

    assert Symbol("com.acme.Cart.add(Item)", "method", "src/Cart.java", 1, 2).name == "add"
    assert Symbol("src/x:Foo::new", "function", "src/x.rs", 1, 2).name == "new"  # `::` is Rust's, and parentheses are not a list there
    assert Symbol("src/x:<Foo as Display>::fmt", "method", "src/x.rs", 1, 2).name == "fmt"
    assert Symbol("tests/a.test:suite > case (1)", "function", "tests/a.test.ts", 1, 2).name == "suite > case (1)"
    assert Symbol("pkg.foo(Item)", "method", "pkg/foo.py", 1, 2).name == "foo(Item)"  # only Java's files carry a parameter list
    assert Symbol("com.acme.Cart.add(Item)", "method", "src/Cart.rs", 1, 2).name == "add(Item)"


@needs_parser
def test_callees_impact_and_path_work_on_a_java_repo(capsys):
    assert main(["callees", "BulkCart.fill()", "--repo", str(SHOP_JAVA), "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert {c["symbol"] for c in result["callees"]} == {
        "com.acme.shop.Cart.add(String)", "com.acme.shop.BulkCart.add(Item,int)", "com.acme.shop.Cart.add(Item)", "com.acme.shop.Item.free(String)",
    }  # fmt: skip
    assert main(["impact", "Cart.total", "--repo", str(SHOP_JAVA), "--json"]) == 0
    impacted = json.loads(capsys.readouterr().out)
    callers_found = {e["symbol"] for entries in impacted["by_file"].values() for e in entries}
    assert {"com.acme.shop.Cart.receipt()", "com.acme.shop.CartTest.addsItems()", "com.acme.shop.Reports.sum(List)"} <= callers_found
    assert impacted["tests"] == []  # JUnit is not collected yet: every caller is code to update
    assert main(["path", "CartTest.main", "Item.free", "--repo", str(SHOP_JAVA), "--json"]) == 0
    chain = json.loads(capsys.readouterr().out)["path"]
    assert chain[0]["symbol"] == "com.acme.shop.CartTest.main(String[])" and chain[-1]["symbol"] == "com.acme.shop.Item.free(String)"


@needs_parser
def test_check_in_a_java_repo_is_a_refusal_until_it_is_given_the_command(capsys):
    assert main(["check", "--repo", str(SHOP_JAVA), "--lang", "java"]) == 2
    assert "does not know how to run Java tests yet" in capsys.readouterr().out


@needs_parser
def test_a_file_the_front_end_cannot_read_costs_that_file_and_nothing_it_half_declared(make_repo, monkeypatch):
    repo = make_repo({"a/A.java": "package a;\nclass A { void f() {} }\n", "b/B.java": "package b;\nclass B { void g() {} }\n"})
    original = java_indexer.Declarations._declare_nested

    def fail_in_b(self, t, owner, node, pkg, info):
        if info.rel == "b/B.java":
            raise RecursionError("nested too deep")
        return original(self, t, owner, node, pkg, info)

    monkeypatch.setattr(java_indexer.Declarations, "_declare_nested", fail_in_b)
    graph = build_graph(repo, JAVA)
    assert graph.parse_errors == ["b/B.java"] and "b/B.java" not in graph.modules
    assert "a.A.f()" in graph.symbols and "b.B" not in graph.symbols and "b.B.g()" not in graph.symbols


@needs_parser
def test_the_index_of_a_java_repo_is_cached_and_follows_an_edit(make_repo, tmp_path):
    repo = make_repo(JAVA_FILES)
    first = load_repo(repo, cache_dir=tmp_path / "cache")
    assert not first.cached and load_repo(repo, cache_dir=tmp_path / "cache").cached
    (repo / "src/main/java/shop/Main.java").write_text("package shop;\npublic class Main { int run(Cart c) { return c.total(); } }\n")
    after = load_repo(repo, cache_dir=tmp_path / "cache")
    assert not after.cached
    assert {e.callee for e in after.graph.edges if e.caller == "shop.Main.run(Cart)"} == {"shop.Cart.total()"}
