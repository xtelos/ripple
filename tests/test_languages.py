"""Which languages ripple indexes a repo as, and what it says when it cannot do all of them.

The tests that need no parser run everywhere (a missing parser is simulated); the ones that index real
TypeScript are skipped, with the reason, when tree-sitter is not installed.
"""

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from conftest import FIXTURES
from stdio_client import StdioClient

from ripple import registry, ts_indexer
from ripple.cache import RepoIndex, cache_file, load_repo
from ripple.cli import main
from ripple.indexer import build_graph
from ripple.languages import RepoError, merge_graphs, module_collisions, scan_repo
from ripple.model import Edge, Graph, Symbol, Unresolved
from ripple.profile import PYTHON, TYPESCRIPT
from ripple.queries import callers, impact, path

ROOT = Path(__file__).resolve().parent.parent
WAREHOUSE_TS = FIXTURES / "ts" / "warehouse-ts"
INSTALL = "pip install 'ripple-code[typescript]'"


def has_parser() -> bool:
    try:
        import tree_sitter  # noqa: F401
        import tree_sitter_typescript  # noqa: F401
    except ImportError:
        return False
    return True


needs_parser = pytest.mark.skipif(not has_parser(), reason=f"TypeScript support is not installed ({INSTALL})")


@pytest.fixture
def no_parser(monkeypatch):
    """tree-sitter cannot be imported, whether or not it is installed."""
    monkeypatch.setattr(ts_indexer, "_PARSERS", None)
    monkeypatch.setitem(sys.modules, "tree_sitter", None)
    monkeypatch.setitem(sys.modules, "tree_sitter_typescript", None)


@pytest.fixture
def parser_blocked(tmp_path_factory):
    """The environment of a server process in which `import tree_sitter` fails, whether or not it is installed."""
    blocker = tmp_path_factory.mktemp("blocker")  # outside any repo under test
    (blocker / "tree_sitter.py").write_text("raise ModuleNotFoundError(\"No module named 'tree_sitter'\", name='tree_sitter')\n")
    return {"PYTHONPATH": str(blocker), "RIPPLE_CACHE_DIR": str(tmp_path_factory.mktemp("cache"))}


PY_ONLY = {
    "app/__init__.py": "",
    "app/core.py": "def helper(xs):\n    return sum(xs)\n\ndef total(xs):\n    return helper(xs)\n",
    "app/cli.py": "from app.core import total\n\ndef main(x):\n    x.go()\n    return total([1])\n",
}
TS_ONLY = {
    "web/src/cart.ts": "export function sum(xs: number[]): number { return xs.length }\n"
    "export function total(xs: number[]): number { return sum(xs) }\n",
    "web/src/main.ts": 'import { total } from "./cart";\nexport function run(item: any) { item.go(); return total([1]) }\n',
}


@pytest.fixture
def mixed(make_repo):
    return make_repo({**PY_ONLY, **TS_ONLY})


def language_of(graph, sid):
    return "typescript" if graph.symbols[sid].file.endswith(TYPESCRIPT.source_suffixes) else "python"


# ---- choosing the language

def test_a_python_repo_is_python_and_its_graph_is_the_one_build_graph_makes(make_repo):
    repo = make_repo(PY_ONLY)
    result = load_repo(repo, lang="auto")
    assert [language.name for language in result.languages] == ["python"]
    assert result.warnings == [] and result.skipped == []
    assert result.graph.to_dict() == build_graph(repo, PYTHON).to_dict()


def test_a_python_repo_never_loads_the_typescript_front_end(make_repo):
    repo = make_repo(PY_ONLY)
    code = (
        "import sys\n"
        "from ripple.cache import load_repo\n"
        f"result = load_repo({str(repo)!r})\n"
        "print(len(result.graph.edges), 'ripple.ts_indexer' in sys.modules, 'tree_sitter' in sys.modules)\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, cwd=ROOT, check=True,
        env={"PYTHONPATH": str(ROOT), "RIPPLE_CACHE_DIR": str(repo / ".cache")},
    ).stdout.strip()  # fmt: skip
    assert out == f"{len(build_graph(repo, PYTHON).edges)} False False"


def test_auto_reads_the_suffixes_of_each_profile_and_skips_declarations_and_node_modules(make_repo):
    repo = make_repo(
        {
            "a.py": "x = 1\n",
            "b.mts": "export const x = 1\n",
            "c.d.ts": "export declare const x: number\n",
            "node_modules/dep/index.ts": "export const y = 1\n",
            "README.md": "text\n",
        }
    )
    scan = scan_repo(repo, "auto")
    assert [p.name for p in scan.profiles] == ["python", "typescript"]
    assert [f.name for f in scan.files["python"]] == ["a.py"]
    assert [f.name for f in scan.files["typescript"]] == ["b.mts"]


def test_a_repo_with_declaration_files_only_is_not_typescript(make_repo):
    repo = make_repo({"a.py": "x = 1\n", "types.d.ts": "export declare const x: number\n"})
    assert [p.name for p in scan_repo(repo, "auto").profiles] == ["python"]


def test_an_explicit_language_reads_only_that_language(mixed):
    assert [p.name for p in scan_repo(mixed, "python").profiles] == ["python"]
    assert [p.name for p in scan_repo(mixed, "typescript").profiles] == ["typescript"]
    result = load_repo(mixed, lang="python")
    assert set(result.graph.symbols) >= {"app.core.total"} and not any(":" in s for s in result.graph.symbols)
    assert result.warnings == []  # asking for Python is not a skipped TypeScript


def test_an_unknown_language_is_refused(make_repo):
    with pytest.raises(RepoError, match="cobol"):
        scan_repo(make_repo(PY_ONLY), "cobol")


# ---- a repo with nothing to read

def test_a_nonexistent_repo_is_an_error_not_zero_files(tmp_path, capsys):
    missing = tmp_path / "nope"
    with pytest.raises(RepoError, match="does not exist"):
        load_repo(missing)
    assert main(["index", str(missing)]) == 2
    captured = capsys.readouterr()
    assert "does not exist" in captured.err and "0 files" not in captured.out + captured.err
    assert main(["callers", "f", "--repo", str(missing)]) == 2
    assert main(["check", "--repo", str(missing)]) == 2
    assert "Traceback" not in capsys.readouterr().err


def test_a_file_is_not_a_repo(make_repo):
    repo = make_repo({"a.py": "x = 1\n"})
    with pytest.raises(RepoError, match="not a directory"):
        load_repo(repo / "a.py")


def test_an_empty_directory_is_an_error_that_says_what_it_looked_for(tmp_path, capsys):
    (tmp_path / "notes.md").write_text("nothing to index\n")
    with pytest.raises(RepoError) as raised:
        load_repo(tmp_path)
    looked_for = f"no {registry.join([language.label for language in registry.LANGUAGES.values()], 'or')} source files"
    assert looked_for == "no Python, TypeScript, JavaScript, Go or Rust source files"
    assert looked_for in str(raised.value)
    assert main(["index", str(tmp_path)]) == 2
    assert looked_for in capsys.readouterr().err


def test_an_explicit_language_the_repo_has_none_of_is_an_error(make_repo):
    repo = make_repo(PY_ONLY)
    with pytest.raises(RepoError, match="no TypeScript source files"):
        load_repo(repo, lang="typescript")
    with pytest.raises(RepoError, match="no Python source files"):
        load_repo(make_repo(TS_ONLY | {"a.txt": ""}) / "web", lang="python")


def test_serving_a_nonexistent_repo_fails_at_start_with_a_message(tmp_path):
    done = subprocess.run(
        [sys.executable, "-m", "ripple", "serve", "--repo", str(tmp_path / "nope")],
        capture_output=True, text=True, cwd=ROOT, timeout=60, stdin=subprocess.DEVNULL,
    )  # fmt: skip
    assert done.returncode == 2
    assert "does not exist" in done.stderr and "Traceback" not in done.stderr
    assert done.stdout == ""  # stdout is the protocol channel: nothing may be written to it


# ---- the parser is missing

def test_typescript_only_without_the_parser_is_a_clear_error_naming_the_extra(make_repo, no_parser, capsys):
    repo = make_repo(TS_ONLY)
    with pytest.raises(RepoError) as raised:
        load_repo(repo)
    assert INSTALL in str(raised.value) and "2 TypeScript files" in str(raised.value)
    assert main(["index", str(repo)]) == 2
    err = capsys.readouterr().err
    assert INSTALL in err and "Traceback" not in err
    assert main(["callers", "total", "--repo", str(repo)]) == 2
    assert INSTALL in capsys.readouterr().err


def test_a_mixed_repo_without_the_parser_indexes_python_and_says_typescript_was_skipped(mixed, no_parser):
    result = load_repo(mixed)
    assert [language.name for language in result.languages] == ["python", "typescript"]
    assert result.languages[1].files == 0 and result.languages[1].skipped is True
    assert result.languages[0].files == 3
    assert not any(":" in sid for sid in result.graph.symbols)  # only the Python half is in the graph
    assert len(result.skipped) == 1
    message = result.skipped[0]
    assert INSTALL in message and "2 files" in message and "Python files only" in message
    assert result.warnings[0] == message


def test_the_cli_exit_code_and_output_say_what_a_mixed_repo_lost(mixed, no_parser, capsys):
    assert main(["callers", "total", "--repo", str(mixed)]) == 3  # answered, but not for the whole repo
    captured = capsys.readouterr()
    assert "app.cli.main" in captured.out
    assert INSTALL in captured.err and "not indexed" in captured.err

    assert main(["callers", "total", "--repo", str(mixed), "--json"]) == 3
    payload = json.loads(capsys.readouterr().out)
    assert payload["symbol"] == "app.core.total" and INSTALL in payload["warnings"][0]

    assert main(["index", str(mixed)]) == 3
    out = capsys.readouterr().out
    assert INSTALL in out and "  TypeScript: 2 files found, not indexed" in out and "  Python: 3 files" in out


def test_a_lookup_that_fails_in_a_mixed_repo_still_says_typescript_was_skipped(mixed, no_parser, capsys):
    assert main(["callers", "run", "--repo", str(mixed)]) == 1  # run is a TypeScript function
    captured = capsys.readouterr()
    assert "no symbol named" in captured.out and INSTALL in captured.err


def test_the_server_names_the_skipped_language_in_every_tool_result(mixed, parser_blocked):
    c = StdioClient([sys.executable, "-m", "ripple", "serve", "--repo", str(mixed)], env=parser_blocked)
    try:
        c.initialize()
        reply = c.request("tools/call", {"name": "callers", "arguments": {"symbol": "total"}})
        assert reply["result"]["isError"] is False
        payload = json.loads(reply["result"]["content"][0]["text"])
        assert [x["symbol"] for x in payload["callers"]] == ["app.cli.main"]
        assert INSTALL in payload["warnings"][0] and "Python files only" in payload["warnings"][0]
        reply = c.request("tools/call", {"name": "impact", "arguments": {"symbol": "helper"}})
        assert INSTALL in json.loads(reply["result"]["content"][0]["text"])["warnings"][0]
    finally:
        assert c.close() == 0


def test_the_server_refuses_a_typescript_only_repo_without_the_parser_as_a_tool_error(make_repo, parser_blocked):
    repo = make_repo(TS_ONLY)
    c = StdioClient([sys.executable, "-m", "ripple", "serve", "--repo", str(repo)], env=parser_blocked)
    try:
        c.initialize()
        reply = c.request("tools/call", {"name": "callers", "arguments": {"symbol": "total"}})
        assert reply["result"]["isError"] is True
        assert INSTALL in reply["result"]["content"][0]["text"]
    finally:
        assert c.close() == 0


# ---- the checker for TypeScript

def test_check_in_a_typescript_repo_is_a_refusal_not_a_traceback(make_repo, capsys):
    repo = make_repo(TS_ONLY)
    assert main(["check", "--repo", str(repo)]) == 2
    out = capsys.readouterr().out
    assert "does not know how to run TypeScript tests" in out and "pass the command" in out
    assert main(["check", "--repo", str(repo), "--test-command", f"{sys.executable} -c pass"]) == 0


def test_check_in_a_mixed_repo_defaults_to_pytest(mixed, monkeypatch, capsys):
    monkeypatch.setenv("RIPPLE_TEST_COMMAND", f"{sys.executable} -c pass")
    assert main(["check", "--repo", str(mixed)]) == 0
    assert '"passed": true' in capsys.readouterr().out


def test_the_server_check_tool_refuses_a_typescript_repo_with_a_tool_error(make_repo, tmp_path):
    repo = make_repo(TS_ONLY)
    c = StdioClient(
        [sys.executable, "-m", "ripple", "serve", "--repo", str(repo)], env={"RIPPLE_CACHE_DIR": str(tmp_path)}
    )
    try:
        c.initialize()
        reply = c.request("tools/call", {"name": "check", "arguments": {}})
        assert reply["result"]["isError"] is True
        assert "does not know how to run TypeScript tests" in reply["result"]["content"][0]["text"]
    finally:
        assert c.close() == 0


# ---- the language flag and its environment variable

def test_the_lang_option_and_ripple_lang_choose_the_language(mixed, capsys, monkeypatch):
    assert main(["index", str(mixed), "--lang", "python"]) == 0
    out = capsys.readouterr().out
    assert "TypeScript" not in out
    monkeypatch.setenv("RIPPLE_LANG", "python")
    assert main(["callers", "total", "--repo", str(mixed), "--json"]) == 0  # not ambiguous with the TypeScript total
    assert json.loads(capsys.readouterr().out)["symbol"] == "app.core.total"
    with pytest.raises(SystemExit):
        main(["index", str(mixed), "--lang", "cobol"])
    monkeypatch.setenv("RIPPLE_LANG", "cobol")
    assert main(["index", str(mixed)]) == 2
    assert "cobol" in capsys.readouterr().err


# ---- the cache is one entry per language

def test_each_language_has_its_own_cache_entry_and_only_the_changed_one_is_rebuilt(mixed, tmp_path):
    cache = tmp_path / "cache"
    first = load_repo(mixed, cache)
    assert first.cached is False and len(list(cache.glob("*.json"))) == 1 + (1 if has_parser() else 0)
    assert cache_file(mixed.resolve(), cache).exists()  # the Python entry keeps the name it has always had
    assert cache_file(mixed.resolve(), cache, TYPESCRIPT).exists() is has_parser()
    second = load_repo(mixed, cache)
    assert second.cached is True
    assert all(language.cached for language in second.languages if not language.skipped)
    (mixed / "app" / "core.py").write_text(PY_ONLY["app/core.py"] + "\ndef extra():\n    pass\n")
    third = load_repo(mixed, cache)
    assert [(language.name, language.cached) for language in third.languages if not language.skipped] == (
        [("python", False), ("typescript", True)] if has_parser() else [("python", False)]
    )
    assert "app.core.extra" in third.graph.symbols


# ---- what the listings say

def test_unparsable_files_are_listed_one_per_line_and_capped(make_repo, capsys):
    files = {f"bad{n:02d}.py": "def (:\n" for n in range(13)}
    repo = make_repo({"ok.py": "def f():\n    pass\n", **files})
    assert main(["index", str(repo)]) == 0
    lines = capsys.readouterr().out.splitlines()
    start = lines.index("could not parse (13 files):")
    assert lines[start + 1 : start + 11] == [f"  bad{n:02d}.py" for n in range(10)]
    assert lines[start + 11] == "  ... and 3 more"
    assert not any(line.startswith("could not parse:") for line in lines)


def test_a_python_only_repo_prints_no_language_block(make_repo, capsys):
    repo = make_repo(PY_ONLY)
    assert main(["index", str(repo)]) == 0
    out = capsys.readouterr().out
    assert "languages:" not in out
    assert out.splitlines()[0].startswith(f"{repo}: 3 files")


# ---- real TypeScript, alone and mixed

@needs_parser
def test_a_typescript_repo_is_indexed_as_typescript_and_the_cli_answers_in_its_ids(capsys):
    assert main(["index", str(WAREHOUSE_TS)]) == 0
    out = capsys.readouterr().out
    assert "languages:" in out and "  TypeScript: 21 files" in out and "  Python" not in out
    assert main(["callers", "clamp", "--repo", str(WAREHOUSE_TS)]) == 0
    assert "src/util/math:clamp" in capsys.readouterr().out
    assert main(["impact", "Item.restock", "--repo", str(WAREHOUSE_TS), "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["symbol"] == "src/models/item:Item.restock" and result["tests"] == []


@needs_parser
def test_a_mixed_repo_has_both_languages_edges_and_no_edge_between_them(mixed):
    result = load_repo(mixed)
    graph = result.graph
    assert [(language.name, language.files) for language in result.languages] == [("python", 3), ("typescript", 2)]
    edges = {(e.caller, e.callee) for e in graph.edges}
    assert ("app.cli.main", "app.core.total") in edges and ("app.core.total", "app.core.helper") in edges
    assert ("web/src/main:run", "web/src/cart:total") in edges and ("web/src/cart:total", "web/src/cart:sum") in edges
    for edge in graph.edges:
        assert language_of(graph, edge.caller) == language_of(graph, edge.callee), edge
        assert language_of(graph, edge.caller) == ("typescript" if ":" in edge.caller or "/" in edge.caller else "python")
    for call in graph.unresolved:  # each language's unresolved calls are its own
        assert language_of(graph, call.caller) == ("typescript" if call.file.endswith(".ts") else "python")
    assert {u.caller for u in graph.unresolved} >= {"app.cli.main", "web/src/main:run"}
    assert graph.ambiguous_ids == {} and result.warnings == []
    python, typescript = build_graph(mixed, PYTHON), build_graph(mixed, TYPESCRIPT)
    assert set(graph.symbols) == set(python.symbols) | set(typescript.symbols)
    assert len(graph.edges) == len(python.edges) + len(typescript.edges)
    assert len(graph.unresolved) == len(python.unresolved) + len(typescript.unresolved)
    assert graph.external_calls == python.external_calls + typescript.external_calls
    assert graph.modules == {**python.modules, **typescript.modules}


@needs_parser
def test_the_queries_on_a_mixed_repo_answer_in_each_languages_ids(mixed, capsys):
    assert main(["callers", "app.core.total", "--repo", str(mixed)]) == 0
    assert "app.cli.main" in capsys.readouterr().out
    assert main(["callers", "web/src/cart:total", "--repo", str(mixed)]) == 0
    out = capsys.readouterr().out
    assert "web/src/main:run" in out and "app.cli.main" not in out
    assert main(["callers", "total", "--repo", str(mixed)]) == 1  # both languages have a total: listed, not picked
    out = capsys.readouterr().out
    assert "ambiguous" in out and "app.core.total" in out and "web/src/cart:total" in out
    assert main(["impact", "sum", "--repo", str(mixed), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["symbol"] == "web/src/cart:sum"  # builtin sum is not in the graph


@needs_parser
def test_the_index_report_counts_each_language_and_lists_its_unparsable_files(mixed, capsys):
    (mixed / "app" / "broken.py").write_text("def (:\n")
    (mixed / "web" / "src" / "broken.ts").write_text("export function (: {\n")
    assert main(["index", str(mixed)]) == 0
    out = capsys.readouterr().out
    # both count the files read, the unparsable ones among them, and say how many of those could not be parsed
    assert "  Python: 4 files (1 could not be parsed)" in out
    assert "  TypeScript: 3 files (1 could not be parsed)" in out
    assert "could not parse (Python, 1 file):\n  app/broken.py" in out
    assert "could not parse (TypeScript, 1 file):\n  web/src/broken.ts" in out


@needs_parser
def test_an_id_both_languages_define_is_listed_and_never_answered(make_repo, capsys):
    repo = make_repo(
        {
            "util.py": "def f():\n    pass\n\ndef g():\n    f()\n",
            "util.ts": "export function f() {}\nexport function g() { f() }\n",
        }
    )
    result = load_repo(repo)
    assert result.graph.ambiguous_ids == {"util": ["util.py", "util.ts"]}
    assert "util (util.py, util.ts)" in result.warnings[0]
    assert {"util.f", "util:f"} <= set(result.graph.symbols)  # the members have different ids: no collision there
    assert main(["callers", "util", "--repo", str(repo)]) == 1
    assert "both languages define" in capsys.readouterr().out
    # answered, but something was left out (the TypeScript module `util`): exit status 3, as for a skipped language
    assert main(["callers", "util:f", "--repo", str(repo), "--json"]) == 3
    assert [c["symbol"] for c in json.loads(capsys.readouterr().out)["callers"]] == ["util:g"]
    assert main(["callers", "util.f", "--repo", str(repo), "--json"]) == 3
    assert [c["symbol"] for c in json.loads(capsys.readouterr().out)["callers"]] == ["util.g"]
    assert main(["index", str(repo)]) == 3
    out = capsys.readouterr().out
    assert "util (util.py, util.ts)" in out
    assert "files share a module name" not in out  # a Python and a TypeScript file sharing an id is not that warning


@needs_parser
def test_the_server_index_follows_an_edit_to_either_language(mixed, tmp_path):
    index = RepoIndex(mixed, cache_dir=tmp_path / "cache")
    assert "web/src/cart:extra" not in index.graph().symbols
    (mixed / "web" / "src" / "cart.ts").write_text(TS_ONLY["web/src/cart.ts"] + "export function extra() {}\n")
    assert "web/src/cart:extra" in index.graph().symbols
    (mixed / "app" / "core.py").write_text(PY_ONLY["app/core.py"] + "\ndef extra():\n    pass\n")
    assert {"app.core.extra", "web/src/cart:extra"} <= set(index.graph().symbols)


@needs_parser
def test_the_mcp_server_answers_a_mixed_repo(mixed, tmp_path):
    c = StdioClient(
        [sys.executable, "-m", "ripple", "serve", "--repo", str(mixed)], env={"RIPPLE_CACHE_DIR": str(tmp_path)}
    )
    try:
        c.initialize()
        reply = c.request("tools/call", {"name": "callers", "arguments": {"symbol": "web/src/cart.ts:sum"}})
        payload = json.loads(reply["result"]["content"][0]["text"])
        assert [x["symbol"] for x in payload["callers"]] == ["web/src/cart:total"]
        assert "warnings" not in payload
        reply = c.request("tools/call", {"name": "callers", "arguments": {"symbol": "helper"}})
        assert json.loads(reply["result"]["content"][0]["text"])["symbol"] == "app.core.helper"
    finally:
        assert c.close() == 0


def test_the_server_instructions_describe_both_languages():
    from ripple.server import INSTRUCTIONS

    text = textwrap.dedent(INSTRUCTIONS)
    assert "Python" in text and "TypeScript" in text
    assert "src/util/math:clamp" in text


# ---- an id both languages define: the Python symbol stays, the TypeScript one and its calls are left out

def hand(sid, file, kind="function"):
    return Symbol(sid, kind, file, 1, 2)


def py_graph(*extra_symbols, edges=()):
    symbols = [hand("app", "app.py", "module"), hand("app.config", "app.py"), hand("main", "main.py", "module"),
               hand("main.run", "main.py"), *extra_symbols]  # fmt: skip
    return Graph(
        symbols={s.id: s for s in symbols},
        edges=[Edge("main.run", "app.config", "main.py", 4), *edges],
        modules={"app.py": "app", "main.py": "main"},
    )


def ts_clash_graph():
    """app.config.ts is the module `app.config`: the id of the Python function in app.py."""
    symbols = [hand("app.config", "app.config.ts", "module"), hand("lib/setup", "lib/setup.ts", "module"),
               hand("lib/setup:setup", "lib/setup.ts"), hand("lib/setup:Child", "lib/setup.ts", "class")]  # fmt: skip
    return Graph(
        symbols={s.id: s for s in symbols},
        edges=[
            Edge("app.config", "lib/setup:setup", "app.config.ts", 3),  # the module's own top-level call
            Edge("lib/setup:setup", "app.config", "lib/setup.ts", 9),  # a call into the id both define
            Edge("lib/setup:setup", "lib/setup:Child", "lib/setup.ts", 10),
            Edge("lib/setup:Child", "lib/setup:setup", "lib/setup.ts", 11, "call", "app.config"),  # via the clashing id
        ],
        unresolved=[Unresolved("app.config", "app.config.ts", 5, "x.y", "why"), Unresolved("lib/setup:setup", "lib/setup.ts", 6, "a.b", "why")],
        bases={"lib/setup:Child": ["app.config"], "app.config": ["lib/setup:Child"]},
        external_bases={"app.config": ["Base"], "lib/setup:Child": ["Base"]},
        modules={"app.config.ts": "app.config", "lib/setup.ts": "lib/setup"},
    )  # fmt: skip


def test_a_clashing_function_id_keeps_python_and_drops_the_typescript_symbol_and_every_call_that_touches_it():
    merged = merge_graphs([py_graph(), ts_clash_graph()])
    assert merged.symbols["app.config"].file == "app.py" and merged.symbols["app.config"].kind == "function"
    assert merged.ambiguous_ids == {"app.config": ["app.config.ts", "app.py"]}
    assert {(e.caller, e.callee) for e in merged.edges} == {("main.run", "app.config"), ("lib/setup:setup", "lib/setup:Child")}
    assert [u.caller for u in merged.unresolved] == ["lib/setup:setup"]
    assert merged.bases == {} and merged.external_bases == {"lib/setup:Child": ["Base"]}
    assert "lib/setup:setup" in merged.symbols and "app.config.ts" in merged.modules  # what is not in the clash stays


def test_no_edge_joins_a_python_symbol_to_a_typescript_one_through_a_clashing_id():
    merged = merge_graphs([py_graph(), ts_clash_graph()])
    for edge in merged.edges:
        assert {language_of(merged, edge.caller), language_of(merged, edge.callee), language_of(merged, edge.caller)} == {
            "typescript" if edge.file.endswith(".ts") else "python"
        }, edge
    assert impact(merged, "lib/setup:setup")["affected"] == 0  # main.run does not reach it, and nothing else calls it
    assert [e["symbol"] for v in impact(merged, "lib/setup:Child")["by_file"].values() for e in v] == ["lib/setup:setup"]
    assert path(merged, "main.run", "lib/setup:setup")["path"] is None
    assert callers(merged, "lib/setup:Child")["callers"][0]["symbol"] == "lib/setup:setup"
    assert "error" in callers(merged, "app.config") and "both languages define" in callers(merged, "app.config")["error"]


def test_a_clashing_member_id_is_dropped_the_same_way():
    py = py_graph(hand("pkg:run", "pkg.py", "method"), edges=[Edge("main.run", "pkg:run", "main.py", 5)])
    ts = Graph(
        symbols={s.id: s for s in [hand("pkg:run", "pkg.ts"), hand("pkg:other", "pkg.ts"), hand("lib:z", "lib.ts")]},
        edges=[Edge("pkg:other", "pkg:run", "pkg.ts", 2), Edge("pkg:run", "lib:z", "pkg.ts", 3), Edge("pkg:other", "lib:z", "pkg.ts", 4)],
    )  # fmt: skip
    merged = merge_graphs([py, ts])
    assert merged.symbols["pkg:run"].file == "pkg.py" and merged.ambiguous_ids == {"pkg:run": ["pkg.py", "pkg.ts"]}
    assert {(e.caller, e.callee) for e in merged.edges} == {("main.run", "app.config"), ("main.run", "pkg:run"), ("pkg:other", "lib:z")}
    assert impact(merged, "lib:z")["affected"] == 1  # pkg:other; main.run does not reach lib:z


def test_a_clashing_class_id_is_dropped_and_python_keeps_its_bases():
    py = py_graph(hand("lib/setup:Child", "kid.py", "class"))
    py.bases["lib/setup:Child"] = ["app.config"]
    merged = merge_graphs([py, ts_clash_graph()])
    assert set(merged.ambiguous_ids) == {"app.config", "lib/setup:Child"}
    assert merged.symbols["lib/setup:Child"].kind == "class" and merged.symbols["lib/setup:Child"].file == "kid.py"
    assert merged.bases == {"lib/setup:Child": ["app.config"]}  # Python's, and none of the TypeScript class's
    assert [e for e in merged.edges if e.file.endswith(".ts")] == []


def test_the_merge_keeps_the_first_graph_and_is_the_same_every_time():
    first = merge_graphs([py_graph(), ts_clash_graph()])
    again = merge_graphs([py_graph(), ts_clash_graph()])
    assert first.to_dict() == again.to_dict() and list(first.symbols) == list(again.symbols)
    swapped = merge_graphs([ts_clash_graph(), py_graph()])
    assert swapped.symbols["app.config"].file == "app.config.ts"  # first listed wins: load_repo lists Python first


def test_a_merge_with_no_clash_is_the_plain_union():
    ts = Graph(symbols={s.id: s for s in [hand("lib/setup", "lib/setup.ts", "module"), hand("lib/setup:setup", "lib/setup.ts")]},
               edges=[Edge("lib/setup", "lib/setup:setup", "lib/setup.ts", 1)])  # fmt: skip
    merged = merge_graphs([py_graph(), ts])
    assert merged.ambiguous_ids == {} and len(merged.edges) == 2
    assert set(merged.symbols) == set(py_graph().symbols) | set(ts.symbols)


MIXED1 = {
    "app.py": "def config():\n    return 1\n",
    "main.py": "from app import config\n\ndef run():\n    config()\n",
    "app.config.ts": 'import { setup } from "./lib/setup";\n\nsetup();\n',
    "lib/setup.ts": "export function setup() {}\n",
}


@needs_parser
def test_a_python_function_is_not_reported_as_affected_by_a_typescript_one_it_cannot_reach(make_repo, capsys):
    repo = make_repo(MIXED1)
    assert main(["impact", "lib/setup:setup", "--repo", str(repo), "--json"]) == 3
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert result["affected"] == 0 and result["by_file"] == {}
    assert "app.config (app.config.ts, app.py)" in result["warnings"][0] and "warning:" not in captured.out
    assert main(["path", "main.run", "lib/setup:setup", "--repo", str(repo)]) == 3
    assert "no call chain from main.run to lib/setup:setup" in capsys.readouterr().out
    assert main(["callers", "app.config", "--repo", str(repo)]) == 1  # the clashing id is refused
    assert "both languages define 'app.config'" in capsys.readouterr().out
    assert main(["callers", "main.run", "--repo", str(repo), "--json"]) == 3  # everything else is answered
    assert json.loads(capsys.readouterr().out)["callers"] == []


@needs_parser
def test_the_merge_is_the_same_whichever_file_was_written_first(tmp_path_factory):
    graphs = []
    for order in (MIXED1, dict(reversed(MIXED1.items()))):
        repo = tmp_path_factory.mktemp("order")
        for name, body in order.items():
            (repo / name).parent.mkdir(parents=True, exist_ok=True)
            (repo / name).write_text(body)
        graphs.append(load_repo(repo, repo.parent / f"{repo.name}-cache").graph.to_dict())
    assert graphs[0] == graphs[1]


@needs_parser
def test_a_module_level_call_in_a_clashing_typescript_module_is_not_a_caller_in_the_python_file(make_repo, capsys):
    repo = make_repo(
        {
            "util.py": "def helper():\n    pass\n\nhelper()\n",
            "util.ts": "export function helper() {}\nhelper();\n",
        }
    )
    assert main(["callers", "util:helper", "--repo", str(repo), "--json"]) == 3
    assert json.loads(capsys.readouterr().out)["callers"] == []  # the call at util.ts:2 had the dropped module as caller
    assert main(["callers", "util.helper", "--repo", str(repo), "--json"]) == 3
    callers_listed = json.loads(capsys.readouterr().out)["callers"]
    assert [(c["symbol"], c["call_sites"]) for c in callers_listed] == [("util", ["util.py:4"])]


# ---- a tree-sitter that is installed but cannot load is "TypeScript unavailable", with the reason

def stub_tree_sitter(monkeypatch, mode):
    """A tree_sitter and tree_sitter_typescript that fail the way a mismatched install does."""
    import types

    monkeypatch.setattr(ts_indexer, "_PARSERS", None)
    tree_sitter, grammar = types.ModuleType("tree_sitter"), types.ModuleType("tree_sitter_typescript")

    class Parser:
        def __init__(self, language):
            if mode == "parser-type":
                raise TypeError("Parser.__init__() takes no arguments (1 given)")

    def Language(pointer):  # noqa: N802
        if mode == "abi":
            raise ValueError("Incompatible Language version 14. Must be between 15 and 15")
        if mode == "oserror":
            raise OSError("cannot open shared object file")
        if mode == "language-type":
            raise TypeError("Language() takes no arguments")

    tree_sitter.Language, tree_sitter.Parser = Language, Parser
    if mode != "no-grammar-attribute":
        grammar.language_typescript = grammar.language_tsx = lambda: 1
    if mode == "import-name":
        monkeypatch.delattr(tree_sitter, "Language")  # `from tree_sitter import Language` is an ImportError
    monkeypatch.setitem(sys.modules, "tree_sitter", tree_sitter)
    monkeypatch.setitem(sys.modules, "tree_sitter_typescript", grammar)


BROKEN = {
    "abi": "Incompatible Language version 14",
    "language-type": "Language() takes no arguments",
    "parser-type": "Parser.__init__() takes no arguments",
    "oserror": "cannot open shared object file",
    "no-grammar-attribute": "language_typescript",
    "import-name": "cannot import name 'Language'",
}


@pytest.mark.parametrize("mode", BROKEN)
def test_a_parser_that_cannot_load_skips_typescript_in_a_mixed_repo_and_says_why(mixed, monkeypatch, capsys, mode):
    stub_tree_sitter(monkeypatch, mode)
    result = load_repo(mixed)  # no traceback: Python is indexed
    assert result.languages[1].skipped is True and "app.core.total" in result.graph.symbols
    assert BROKEN[mode] in result.skipped[0] and INSTALL in result.skipped[0] and "Python files only" in result.skipped[0]
    assert main(["callers", "total", "--repo", str(mixed)]) == 3
    captured = capsys.readouterr()
    assert "app.cli.main" in captured.out and BROKEN[mode] in captured.err
    assert main(["callers", "total", "--repo", str(mixed), "--json"]) == 3
    assert BROKEN[mode] in json.loads(capsys.readouterr().out)["warnings"][0]


@pytest.mark.parametrize("mode", BROKEN)
def test_a_parser_that_cannot_load_is_a_clean_error_for_a_typescript_only_repo(make_repo, monkeypatch, capsys, mode):
    stub_tree_sitter(monkeypatch, mode)
    repo = make_repo(TS_ONLY)
    with pytest.raises(RepoError) as raised:
        load_repo(repo)
    assert INSTALL in str(raised.value) and BROKEN[mode] in str(raised.value)
    assert main(["index", str(repo)]) == 2
    err = capsys.readouterr().err
    assert INSTALL in err and BROKEN[mode] in err and "Traceback" not in err


def test_the_server_answers_a_mixed_repo_when_the_parser_cannot_load(mixed, tmp_path_factory):
    stub = tmp_path_factory.mktemp("stub")
    (stub / "tree_sitter.py").write_text(
        "class Parser:\n    def __init__(self, language):\n        pass\n\n"
        "def Language(pointer):\n    raise ValueError('Incompatible Language version 14. Must be between 15 and 15')\n"
    )
    (stub / "tree_sitter_typescript.py").write_text("language_typescript = language_tsx = lambda: 1\n")
    env = {"PYTHONPATH": str(stub), "RIPPLE_CACHE_DIR": str(tmp_path_factory.mktemp("cache"))}
    c = StdioClient([sys.executable, "-m", "ripple", "serve", "--repo", str(mixed)], env=env)
    try:
        c.initialize()
        reply = c.request("tools/call", {"name": "callers", "arguments": {"symbol": "total"}})
        assert reply["result"]["isError"] is False
        payload = json.loads(reply["result"]["content"][0]["text"])
        assert [x["symbol"] for x in payload["callers"]] == ["app.cli.main"]
        assert "Incompatible Language version 14" in payload["warnings"][0]
    finally:
        assert c.close() == 0


# ---- a .ts file that is not TypeScript (Qt Linguist translations, video) is not a TypeScript repo

QT = {
    "app.py": "def main():\n    return 1\n",
    "ui.py": "from app import main\n\ndef show():\n    return main()\n",
    "i18n/app_de.ts": '<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE TS>\n<TS version="2.1" language="de_DE"></TS>\n',
    "i18n/app_fr.ts": '﻿\n  <!DOCTYPE TS><TS version="2.1" language="fr_FR"></TS>\n',
}


def test_qt_linguist_files_do_not_make_a_python_project_mixed(make_repo, capsys, monkeypatch):
    repo = make_repo(QT)
    (repo / "movie.ts").write_bytes(b"\x47\x40\x00\x10\x00\x00\xb0\x0d" + bytes(range(256)))  # an MPEG transport stream
    assert [p.name for p in scan_repo(repo, "auto").profiles] == ["python"]
    for blocked in (False, True):  # with or without the parser, nothing is skipped and nothing is said
        if blocked:
            monkeypatch.setattr(ts_indexer, "_PARSERS", None)
            monkeypatch.setitem(sys.modules, "tree_sitter", None)
        assert main(["index", str(repo)]) == 0
        captured = capsys.readouterr()
        assert "languages:" not in captured.out and "TypeScript" not in captured.out + captured.err
        assert main(["callers", "main", "--repo", str(repo), "--json"]) == 0
        captured = capsys.readouterr()
        assert "warnings" not in json.loads(captured.out) and captured.err == ""
    with pytest.raises(RepoError, match="no TypeScript source files"):
        load_repo(repo, lang="typescript")


def test_a_real_typescript_file_that_mentions_xml_in_a_comment_is_still_typescript(make_repo):
    repo = make_repo({"a.ts": "// <?xml version='1.0'?>\nexport const a = 1\n", "b.ts": "﻿  export const b = 2\n"})
    assert [f.name for f in scan_repo(repo, "typescript").files["typescript"]] == ["a.ts", "b.ts"]


@needs_parser
def test_the_typescript_indexer_never_reads_a_translation_file(make_repo):
    repo = make_repo({k: v for k, v in QT.items() if k.endswith(".ts")} | {"x.ts": "export function f() {}\n"})
    graph = build_graph(repo, TYPESCRIPT)
    assert graph.parse_errors == [] and list(graph.modules) == ["x.ts"]


# ---- the report

def test_module_collisions_are_each_languages_own():
    graph = Graph(modules={"a.py": "x", "b.py": "x", "c.ts": "x", "d.ts": "y", "y.py": "y", "e.ts": "z", "f.ts": "z"})
    assert module_collisions(graph) == {"x": ["a.py", "b.py"], "z": ["e.ts", "f.ts"]}  # a.py with c.ts is not one


def test_a_python_repo_s_module_collision_warning_is_unchanged(make_repo, capsys):
    repo = make_repo({"tests/test_dup.py": "def test_one():\n    pass\n", "src/tests/test_dup.py": "def test_two():\n    pass\n"})
    assert main(["index", str(repo)]) == 0
    assert "  tests.test_dup: src/tests/test_dup.py, tests/test_dup.py" in capsys.readouterr().out
