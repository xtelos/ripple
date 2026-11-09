"""In a repo of Python and TypeScript, the decorator and class-expression hints stay inside their own language.

A TypeScript `@missing()` that did not resolve is a possible site for a TypeScript symbol called `missing`, never for a
Python function of that name, and a call in a TypeScript class expression is a possible caller of a TypeScript symbol
only. These need tree-sitter and are skipped, with the reason, without it.
"""

import pytest
from conftest import FIXTURES  # noqa: F401  (keeps the conftest path set up)

pytest.importorskip("tree_sitter", reason="TypeScript support is not installed (pip install 'ripple-code[typescript]')")
pytest.importorskip("tree_sitter_typescript", reason="TypeScript support is not installed (pip install 'ripple-code[typescript]')")

from ripple.cache import load_repo  # noqa: E402
from ripple.queries import callers, impact  # noqa: E402

FILES = {
    "tool.py": "def missing():\n    return 1\n\ndef helper():\n    return 2\n",
    "src/z.ts": """
    import { api } from "./gone";
    export function missing() { return 1; }
    export function helper() { return 2; }
    @api.missing()
    export class A {}
    export function build() { return class { go() { helper(); } }; }
    """,
    "tsconfig.json": "{}",
}


@pytest.fixture
def graph(make_repo):
    return load_repo(make_repo(FILES), lang="auto").graph


def test_the_fixture_has_the_typescript_flags_it_is_about(graph):
    assert [d["written"] for d in callers(graph, "src/z:missing")["possible_decorations"]] == ["@api.missing()"]
    assert len(callers(graph, "src/z:helper")["possible_missed_callers"]) == 1


@pytest.mark.parametrize("query", [callers, impact])
def test_a_python_symbol_picks_up_no_typescript_decoration(graph, query):
    result = query(graph, "tool.missing")
    assert "possible_decorations" not in result and "decoration_note" not in result


@pytest.mark.parametrize("query", [callers, impact])
def test_a_python_symbol_picks_up_no_call_from_a_typescript_class_expression(graph, query):
    result = query(graph, "tool.helper")
    assert not result.get("possible_missed_callers")
    assert "possible_missed_total" not in result or result["possible_missed_total"] == 0


def test_the_typescript_symbols_keep_theirs_in_impact_too(graph):
    assert [d["written"] for d in impact(graph, "src/z:missing")["possible_decorations"]] == ["@api.missing()"]
    assert len(impact(graph, "src/z:helper")["possible_missed_callers"]) == 1
