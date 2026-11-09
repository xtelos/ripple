"""A bare name that is only a module's name is never answered as a module with no callers.

`callers SubMenu` in react-pro-sidebar used to resolve silently to the module src/components/SubMenu and answer 0 callers,
while the component (SubMenuFR, exported as `SubMenu = forwardRef(SubMenuFR)`) has dozens. Now a module's exported name
that wraps or aliases a function or class of the repo resolves to it, saying so; any other name that is only a module
is an error that lists what the module defines. A full module id, or a path, is asked as it always was.

The TypeScript tests need tree-sitter (the `typescript` extra) and are skipped without it.
"""

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest
from conftest import FIXTURES
from stdio_client import StdioClient

from ripple import __version__, queries
from ripple.cache import cache_file, load_graph, load_repo
from ripple.cli import main
from ripple.indexer import build_graph
from ripple.profile import PYTHON, TYPESCRIPT

HAS_TS = all(importlib.util.find_spec(m) for m in ("tree_sitter", "tree_sitter_typescript"))
ts = pytest.mark.skipif(not HAS_TS, reason="TypeScript support is not installed (pip install 'ripple-code[typescript]')")

SUBMENU = {
    "src/components/SubMenu.tsx": """
    import React from 'react';
    const SubMenuFR = (props: any, ref: any) => <li ref={ref} />;
    export const SubMenu = React.forwardRef(SubMenuFR);
    """,
    "src/App.tsx": """
    import { SubMenu } from './components/SubMenu';
    export function App() { return <SubMenu />; }
    export function Other() { return <SubMenu />; }
    """,
}
SUBMENU_NOTE = "SubMenu is exported by src/components/SubMenu as forwardRef(SubMenuFR); showing src/components/SubMenu:SubMenuFR"


def index(make_repo, files):
    return build_graph(make_repo(files), TYPESCRIPT)


# ---- a name the module exports that wraps or aliases a symbol resolves to the symbol, and says so


@ts
def test_a_forward_ref_component_named_like_its_module_resolves_to_the_wrapped_function(make_repo):
    graph = index(make_repo, SUBMENU)
    assert queries.find_symbol(graph, "SubMenu") == {"symbol": "src/components/SubMenu:SubMenuFR", "note": SUBMENU_NOTE}
    answer = queries.callers(graph, "SubMenu")
    assert answer["symbol"] == "src/components/SubMenu:SubMenuFR"
    assert answer["count"] == 2
    assert answer["note"] == SUBMENU_NOTE


@ts
def test_every_query_carries_the_note(make_repo):
    graph = index(make_repo, SUBMENU)
    callees = queries.callees(graph, "SubMenu")
    assert (callees["symbol"], callees["note"]) == ("src/components/SubMenu:SubMenuFR", SUBMENU_NOTE)
    impact = queries.impact(graph, "SubMenu")
    assert (impact["symbol"], impact["affected"], impact["note"]) == ("src/components/SubMenu:SubMenuFR", 2, SUBMENU_NOTE)
    found = queries.path(graph, "src/App:App", "SubMenu")
    assert found["to"] == "src/components/SubMenu:SubMenuFR" and found["path"] is not None
    assert found["note"] == SUBMENU_NOTE
    assert queries.path(graph, "SubMenu", "SubMenu")["note"] == SUBMENU_NOTE  # said once, whichever end it applies to
    none = queries.path(graph, "SubMenu", "src/App:App")  # no chain: the note still says which symbol the answer is about
    assert (none["from"], none["path"], none["note"]) == ("src/components/SubMenu:SubMenuFR", None, SUBMENU_NOTE)
    assert "note" not in queries.callers(graph, "src/components/SubMenu:SubMenuFR")  # a full id has nothing to explain


@ts
def test_memo_and_nested_wrappers_are_named_in_the_note(make_repo):
    graph = index(
        make_repo,
        {
            "src/Card.tsx": """
            import React, { memo } from 'react';
            function Inner() { return <b />; }
            export const Card = memo(Inner);
            """,
            "src/Both.tsx": """
            import React, { memo, forwardRef } from 'react';
            function Raw() { return <i />; }
            export const Both = React.memo(forwardRef(Raw));
            """,
            "src/Page.tsx": """
            import { Card } from './Card';
            import { Both } from './Both';
            export function Page() { return <><Card /><Both /></>; }
            """,
        },
    )
    card = queries.callers(graph, "Card")
    assert (card["symbol"], card["count"]) == ("src/Card:Inner", 1)
    assert card["note"] == "Card is exported by src/Card as memo(Inner); showing src/Card:Inner"
    both = queries.callers(graph, "Both")
    assert (both["symbol"], both["count"]) == ("src/Both:Raw", 1)
    assert both["note"] == "Both is exported by src/Both as memo(forwardRef(Raw)); showing src/Both:Raw"


@ts
def test_an_exported_alias_of_a_function_resolves_to_it(make_repo):
    graph = index(
        make_repo,
        {
            "src/Widget.ts": "function WidgetImpl() { return 1; }\nexport { WidgetImpl as Widget };\n",
            "src/use.ts": "import { Widget } from './Widget';\nexport function go() { return Widget(); }\n",
        },
    )
    answer = queries.callers(graph, "Widget")
    assert answer["symbol"] == "src/Widget:WidgetImpl"
    assert [c["symbol"] for c in answer["callers"]] == ["src/use:go"]
    assert answer["note"] == "Widget is exported by src/Widget as an alias of WidgetImpl; showing src/Widget:WidgetImpl"


@ts
def test_export_const_name_equals_inner_is_not_an_alias_because_the_indexer_does_not_read_it_as_one(make_repo):
    """`export const Name = Inner`: the indexer leaves a call of Name unresolved (a variable is not a function it holds),
    so a question about Name does not answer as Inner either: it lists Inner among the module's candidates. If the
    indexer ever reads this form, this test fails and the alias belongs in Resolver.export_aliases."""
    graph = index(
        make_repo,
        {
            "src/Name.ts": "function Inner() { return 1; }\nexport const Name = Inner;\n",
            "src/use.ts": "import { Name } from './Name';\nexport function go() { return Name(); }\n",
        },
    )
    assert not any(e.caller == "src/use:go" for e in graph.edges)
    assert [u.text for u in graph.unresolved if u.caller == "src/use:go"] == ["Name"]
    answer = queries.callers(graph, "Name")
    assert "symbol" not in answer
    assert answer["candidates"] == ["src/Name:Inner"]


@ts
def test_a_barrel_that_re_exports_the_name_is_not_ambiguous_with_the_symbol(make_repo):
    graph = index(
        make_repo,
        {
            "src/Button/index.ts": "export { Button } from './Button';\n",
            "src/Button/Button.ts": "export function Button() { return 1; }\n",
            "src/use.ts": "import { Button } from './Button';\nexport function go() { return Button(); }\n",
        },
    )
    answer = queries.callers(graph, "Button")
    assert answer["symbol"] == "src/Button/Button:Button"
    assert [c["symbol"] for c in answer["callers"]] == ["src/use:go"]
    assert "note" not in answer  # the answer is the symbol the name always meant


# ---- a module with the name of a function it defines is that function


@ts
def test_a_module_named_like_a_function_it_defines_resolves_to_the_function(make_repo):
    graph = index(make_repo, {"src/foo.ts": "export function foo() { return 1; }\nexport function other() { return foo(); }\n"})
    assert queries.find_symbol(graph, "foo") == {"symbol": "src/foo:foo"}  # no note: nothing was replaced
    assert queries.callers(graph, "foo")["count"] == 1


@ts
def test_two_functions_of_one_name_in_modules_of_that_name_are_still_ambiguous(make_repo):
    graph = index(make_repo, {"src/a/foo.ts": "export function foo() { return 1; }\n", "src/b/foo.ts": "export function foo() { return 2; }\n"})
    answer = queries.find_symbol(graph, "foo")
    assert answer == {"error": "'foo' is ambiguous; pass one of the candidates", "candidates": ["src/a/foo:foo", "src/b/foo:foo"]}


# ---- a name that is only a module is an error that lists what the module has


@ts
def test_a_module_name_matching_nothing_exported_lists_what_the_module_defines(make_repo):
    graph = index(
        make_repo,
        {"src/widgets/Gauge.ts": "export function draw() { return 1; }\nexport class Dial { turn() { return draw(); } }\n"},
    )
    for ask in (queries.callers, queries.callees, queries.impact):
        answer = ask(graph, "Gauge")
        assert "symbol" not in answer
        assert answer["error"].startswith("'Gauge' is the module src/widgets/Gauge, which has no symbol of that name")
        assert answer["error"].endswith("pass one of the candidates, or the module id src/widgets/Gauge to ask about the module")
        assert answer["candidates"] == ["src/widgets/Gauge:Dial", "src/widgets/Gauge:draw"]  # members are not listed
    assert "error" in queries.path(graph, "Gauge", "src/widgets/Gauge:draw")
    assert "error" in queries.path(graph, "src/widgets/Gauge:draw", "Gauge")


@ts
def test_a_module_that_defines_nothing_says_so(make_repo):
    graph = index(make_repo, {"src/empty.ts": "export const x = 1;\n"})
    answer = queries.callers(graph, "empty")
    assert answer["candidates"] == []
    assert "defines no function or class" in answer["error"]


@ts
def test_the_full_module_id_and_paths_still_ask_about_the_module(make_repo):
    graph = index(make_repo, {**SUBMENU, "src/widgets/Gauge.ts": "export function draw() { return 1; }\n"})
    for query in ("src/components/SubMenu", "components/SubMenu", "src/components/SubMenu.tsx", "components/SubMenu.tsx"):
        assert queries.find_symbol(graph, query) == {"symbol": "src/components/SubMenu"}, query
    assert queries.callers(graph, "src/widgets/Gauge") == {
        "symbol": "src/widgets/Gauge",
        "count": 0,
        "callers": [],
        "through_wrapper": [],
    }


@ts
def test_a_name_that_is_a_module_and_a_symbol_of_another_module_is_still_ambiguous(make_repo):
    graph = index(
        make_repo,
        {"src/a/thing.ts": "export function other() { return 1; }\n", "src/b.ts": "export function thing() { return 2; }\n"},
    )
    assert queries.find_symbol(graph, "thing") == {
        "error": "'thing' is ambiguous; pass one of the candidates",
        "candidates": ["src/a/thing", "src/b:thing"],
    }


@ts
def test_two_modules_of_one_name_are_still_ambiguous(make_repo):
    graph = index(make_repo, {"src/a/util.ts": "export const x = 1;\n", "src/b/util.ts": "export const y = 1;\n"})
    assert queries.find_symbol(graph, "util") == {
        "error": "'util' is ambiguous; pass one of the candidates",
        "candidates": ["src/a/util", "src/b/util"],
    }


# ---- a module that declares a name and also exports it as something else is not a silent pick of either


def ambiguous(query, *candidates):
    return {"error": f"'{query}' is ambiguous; pass one of the candidates", "candidates": list(candidates)}


@ts
def test_a_private_function_and_an_export_alias_of_the_same_name_are_ambiguous(make_repo):
    graph = index(
        make_repo,
        {
            "src/render.ts": """
            function render() { return 1; }
            function renderV2() { return 2; }
            export { renderV2 as render };
            export function legacy() { return render(); }
            """,
            "src/a.ts": "import { render } from './render';\nexport function a() { return render(); }\n",
            "src/b.ts": "import { render } from './render';\nexport function b() { return render(); }\n",
        },
    )
    expected = ambiguous("render", "src/render", "src/render:render", "src/render:renderV2")
    assert queries.find_symbol(graph, "render") == expected
    for ask in (queries.callers, queries.callees, queries.impact):
        assert ask(graph, "render") == expected
    # each reading stays one id away
    assert [c["symbol"] for c in queries.callers(graph, "src/render:renderV2")["callers"]] == ["src/a:a", "src/b:b"]
    assert [c["symbol"] for c in queries.callers(graph, "src/render:render")["callers"]] == ["src/render:legacy"]


@ts
def test_a_private_function_and_an_export_it_cannot_name_are_ambiguous_too(make_repo):
    """`export { Exported as Card }` where Exported is memo(Fast): ripple records no target for it, but the module
    declares a Card and exports the name as something else, so the declared one is not the answer."""
    files = {
        "src/Card.tsx": """
        import React, { memo } from 'react';
        function Card() { return <i />; }
        function Fast() { return <b />; }
        const Exported = memo(Fast);
        export { Exported as Card };
        export function Local() { return <Card />; }
        """,
        "src/a.tsx": "import { Card } from './Card';\nexport function A() { return <Card />; }\n",
    }
    graph = index(make_repo, files)
    assert queries.callers(graph, "Card") == ambiguous("Card", "src/Card", "src/Card:Card")


@ts
def test_a_function_exported_under_its_own_name_is_not_a_conflict(make_repo):
    graph = index(
        make_repo,
        {
            "src/render.ts": "export function render() { return 1; }\nexport { render as render };\nexport function legacy() { return render(); }\n",
            "src/plain.ts": "function plain() { return 1; }\nexport { plain };\nexport function user() { return plain(); }\n",
        },
    )
    assert queries.find_symbol(graph, "render") == {"symbol": "src/render:render"}
    assert queries.find_symbol(graph, "plain") == {"symbol": "src/plain:plain"}


@ts
def test_overloads_a_merged_namespace_and_a_merged_interface_are_not_conflicts(make_repo):
    graph = index(
        make_repo,
        {
            "src/over.ts": "export function over(a: string): string;\nexport function over(a: number): number;\nexport function over(a: any) { return a; }\n",
            "src/ns.ts": "export function ns() { return 1; }\nexport namespace ns { export const x = 1; }\n",
            "src/Shape.ts": "export class Shape { area() { return 1; } }\nexport interface Shape { name: string }\n",
            "src/dflt.ts": "export default function dflt() { return 1; }\n",
        },
    )
    for name, symbol in (("over", "src/over:over"), ("ns", "src/ns:ns"), ("Shape", "src/Shape:Shape"), ("dflt", "src/dflt:dflt")):
        assert queries.find_symbol(graph, name) == {"symbol": symbol}, name


# ---- a bare name that is a root-level module's whole id is also a bare name


@ts
def test_a_root_level_module_that_exports_its_own_name_as_a_wrapper_is_ambiguous(make_repo):
    graph = index(
        make_repo,
        {
            "SubMenu.tsx": """
            import React, { forwardRef } from 'react';
            const SubMenuFR = () => <li />;
            export const SubMenu = forwardRef(SubMenuFR);
            """,
            "use.tsx": "import { SubMenu } from './SubMenu';\nexport function U() { return <SubMenu />; }\n",
        },
    )
    assert queries.callers(graph, "SubMenu") == ambiguous("SubMenu", "SubMenu", "SubMenu:SubMenuFR")
    assert queries.callers(graph, "SubMenu:SubMenuFR")["count"] == 1


@ts
def test_a_root_level_module_that_defines_its_own_name_is_ambiguous(make_repo):
    graph = index(make_repo, {"foo.ts": "export function foo() { return 1; }\nexport function other() { return foo(); }\n"})
    assert queries.callers(graph, "foo") == ambiguous("foo", "foo", "foo:foo")
    assert queries.callers(graph, "foo:foo")["count"] == 1


@ts
def test_a_root_level_module_with_nothing_of_its_name_is_still_the_module(make_repo):
    graph = index(make_repo, {"foo.ts": "export function other() { return 1; }\n"})
    assert queries.callers(graph, "foo")["symbol"] == "foo"  # the whole id of a module: it says so as it is


def test_a_python_root_level_module_that_defines_its_own_name_is_ambiguous_like_a_nested_one(make_repo):
    graph = build_graph(make_repo({"tax.py": "def tax():\n    return 1\n\n\ndef other():\n    return tax()\n"}), PYTHON)
    assert queries.callers(graph, "tax") == ambiguous("tax", "tax", "tax.tax")
    assert queries.callers(graph, "tax.tax")["count"] == 1


def test_a_python_root_level_module_with_nothing_of_its_name_is_still_the_module(make_repo):
    graph = build_graph(make_repo({"tax.py": "def other():\n    return 1\n"}), PYTHON)
    assert queries.callers(graph, "tax")["symbol"] == "tax"


# ---- two modules that each export or define the name are never settled by the file name


@ts
def test_two_wrapped_exports_of_one_name_are_ambiguous(make_repo):
    graph = index(
        make_repo,
        {
            "src/Card.tsx": "import React, { memo } from 'react';\nfunction Inner() { return <b />; }\nexport const Card = memo(Inner);\n",
            "src/legacy/Widgets.tsx": "import React, { memo } from 'react';\nfunction OldCard() { return <b />; }\nexport const Card = memo(OldCard);\n",
            "src/use.tsx": "import { Card } from './legacy/Widgets';\nexport function U() { return <Card />; }\n",
        },
    )
    expected = ambiguous("Card", "src/Card:Inner", "src/legacy/Widgets:OldCard")
    assert queries.callers(graph, "Card") == expected
    assert [c["symbol"] for c in queries.callers(graph, "src/legacy/Widgets:OldCard")["callers"]] == ["src/use:U"]


@ts
def test_a_function_in_one_module_and_a_wrapped_export_in_another_are_ambiguous(make_repo):
    graph = index(
        make_repo,
        {
            "src/a.ts": "export function Card() { return 1; }\n",
            "src/b.tsx": "import React, { memo } from 'react';\nfunction Inner() { return <b />; }\nexport const Card = memo(Inner);\n",
        },
    )
    assert queries.callers(graph, "Card") == ambiguous("Card", "src/a:Card", "src/b:Inner")


@ts
def test_a_name_exported_by_a_barrel_and_a_module_that_both_mean_one_function_is_not_ambiguous(make_repo):
    graph = index(
        make_repo,
        {
            "src/Card.tsx": "import React, { memo } from 'react';\nfunction Inner() { return <b />; }\nexport const Card = memo(Inner);\n",
            "src/index.ts": "export { Card } from './Card';\n",
        },
    )
    assert queries.find_symbol(graph, "Card")["symbol"] == "src/Card:Inner"


# ---- the note of a re-export names the wrapper it passed through


@ts
def test_the_note_of_a_barrels_re_export_names_the_wrapper_of_what_it_re_exports(make_repo):
    graph = index(
        make_repo,
        {
            "src/Menu/index.ts": "export { SubMenu as Menu } from './SubMenu';\n",
            "src/Menu/SubMenu.tsx": """
            import React, { forwardRef } from 'react';
            const SubMenuFR = () => <li />;
            export const SubMenu = forwardRef(SubMenuFR);
            """,
            "src/use.tsx": "import { Menu } from './Menu';\nexport function U() { return <Menu />; }\n",
        },
    )
    answer = queries.callers(graph, "Menu")
    assert (answer["symbol"], answer["count"]) == ("src/Menu/SubMenu:SubMenuFR", 1)
    assert answer["note"] == (
        "Menu is exported by src/Menu as a re-export of SubMenu from src/Menu/SubMenu, which exports it as forwardRef(SubMenuFR); "
        "showing src/Menu/SubMenu:SubMenuFR"
    )


@ts
def test_the_note_of_a_chain_of_barrels_names_every_hop(make_repo):
    graph = index(
        make_repo,
        {
            "src/Outer/index.ts": "export { Inner as Outer } from './b';\n",
            "src/Outer/b.ts": "export { Wrapped as Inner } from './c';\n",
            "src/Outer/c.tsx": "import React, { memo } from 'react';\nfunction Raw() { return <b />; }\nexport const Wrapped = memo(Raw);\n",
        },
    )
    note = queries.find_symbol(graph, "Outer")["note"]
    assert note == (
        "Outer is exported by src/Outer as a re-export of Inner from src/Outer/b, which exports it as "
        "a re-export of Wrapped from src/Outer/c, which exports it as memo(Raw); showing src/Outer/c:Raw"
    )


@ts
def test_the_note_of_a_re_export_of_a_plain_function_is_as_it_was(make_repo):
    graph = index(
        make_repo,
        {
            "src/Menu/index.ts": "export { Item as Menu } from './Item';\n",
            "src/Menu/Item.ts": "export function Item() { return 1; }\n",
        },
    )
    assert queries.find_symbol(graph, "Menu") == {
        "symbol": "src/Menu/Item:Item",
        "note": "Menu is exported by src/Menu as a re-export of src/Menu/Item:Item; showing src/Menu/Item:Item",
    }


# ---- the note and the error reach every surface


@ts
def test_cli_text_and_json_carry_the_note(make_repo, capsys):
    repo = str(make_repo(SUBMENU))
    assert main(["callers", "SubMenu", "--repo", repo, "--lang", "typescript"]) == 0
    text = capsys.readouterr().out
    assert text.splitlines()[0] == f"note: {SUBMENU_NOTE}"
    assert "callers of src/components/SubMenu:SubMenuFR (2):" in text
    assert main(["callers", "SubMenu", "--repo", repo, "--lang", "typescript", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert (payload["symbol"], payload["note"]) == ("src/components/SubMenu:SubMenuFR", SUBMENU_NOTE)
    assert main(["impact", "SubMenu", "--repo", repo, "--lang", "typescript"]) == 0
    assert capsys.readouterr().out.splitlines()[0] == f"note: {SUBMENU_NOTE}"


@ts
def test_cli_prints_the_candidates_for_a_module_only_name(make_repo, capsys):
    repo = str(make_repo({"src/widgets/Gauge.ts": "export function draw() { return 1; }\n"}))
    assert main(["impact", "Gauge", "--repo", repo, "--lang", "typescript"]) == 1
    out = capsys.readouterr().out
    assert out.splitlines()[0].startswith("'Gauge' is the module src/widgets/Gauge")
    assert "  src/widgets/Gauge:draw" in out


@ts
def test_the_mcp_tool_answers_with_the_note_and_the_error(make_repo, tmp_path):
    repo = make_repo({**SUBMENU, "src/widgets/Gauge.ts": "export function draw() { return 1; }\n"})
    client = StdioClient(
        [sys.executable, "-m", "ripple", "serve", "--repo", str(repo), "--lang", "typescript"],
        env={"RIPPLE_CACHE_DIR": str(tmp_path / "cache")},
    )
    try:
        client.initialize()
        reply = client.request("tools/call", {"name": "callers", "arguments": {"symbol": "SubMenu"}})
        payload = json.loads(reply["result"]["content"][0]["text"])
        assert (payload["symbol"], payload["count"], payload["note"]) == ("src/components/SubMenu:SubMenuFR", 2, SUBMENU_NOTE)
        reply = client.request("tools/call", {"name": "impact", "arguments": {"symbol": "Gauge"}})
        payload = json.loads(reply["result"]["content"][0]["text"])
        assert payload["candidates"] == ["src/widgets/Gauge:draw"] and "'Gauge' is the module" in payload["error"]
    finally:
        client.close()


@ts
def test_the_alias_survives_the_cache(make_repo):
    repo = make_repo(SUBMENU)
    first = load_repo(repo, lang="typescript")
    again = load_repo(repo, lang="typescript")
    assert not first.cached and again.cached
    assert queries.callers(again.graph, "SubMenu") == queries.callers(first.graph, "SubMenu")
    assert queries.callers(again.graph, "SubMenu")["symbol"] == "src/components/SubMenu:SubMenuFR"


@ts
def test_the_alias_survives_the_merge_of_a_repo_with_both_languages(make_repo):
    repo = make_repo({**SUBMENU, "tools/helper.py": "def assist():\n    pass\n"})
    graph = load_repo(repo).graph  # auto: both languages, merged
    assert graph.export_aliases == {"src/components/SubMenu:SubMenu": ["src/components/SubMenu:SubMenuFR", "forwardRef(SubMenuFR)"]}
    answer = queries.callers(graph, "SubMenu")
    assert (answer["symbol"], answer["count"], answer["note"]) == ("src/components/SubMenu:SubMenuFR", 2, SUBMENU_NOTE)
    assert queries.callers(graph, "assist")["symbol"] == "tools.helper.assist"


def test_the_cache_format_moved_when_the_graph_gained_export_aliases(make_repo, tmp_path_factory):
    """Format 14: a cached TypeScript graph of format 13 has export aliases without the declared-and-exported-elsewhere
    entries and the chain wording of a re-export, so a name that is both a private function and a different export would
    settle on the private function. It is rebuilt, never reused."""
    repo = make_repo({"m.py": "def f():\n    pass\n"})
    cache_dir = tmp_path_factory.mktemp("cache")
    load_graph(repo, cache_dir)
    path = cache_file(repo.resolve(), cache_dir)
    saved = json.loads(path.read_text())
    assert saved["format"] != f"{__version__}-13"
    saved["format"] = f"{__version__}-13"
    saved["graph"]["symbols"] = []
    path.write_text(json.dumps(saved))
    graph, cached = load_graph(repo, cache_dir)
    assert cached is False
    assert "m.f" in graph.symbols


# ---- Python: a bare name that is only a module is an error too, and nothing else changes

# tests/bare_names_python.json holds the answer to every bare-name query over the Python fixtures as it was before this
# change: for each fixture and name, the first 16 hex of the sha1 of the JSON answer to callers, callees, impact and path
# (name to a fixed function), and `was`, the module the name resolved to when it was only a module (else empty).
GOLDEN = Path(__file__).parent / "bare_names_python.json"
COMMANDS = ("callers", "callees", "impact", "path")


def python_answers(fixture):
    graph = build_graph(FIXTURES / fixture, PYTHON)
    names = {s.name for s in graph.symbols.values()} | {s.id.rsplit(".", 1)[-1] for s in graph.symbols.values() if s.kind == "module"}
    names |= {"nosuchthing", "x"}
    fixed = sorted(s.id for s in graph.symbols.values() if s.kind == "function")[0]
    for name in sorted(names):
        answers = (
            queries.callers(graph, name),
            queries.callees(graph, name),
            queries.impact(graph, name),
            queries.path(graph, name, fixed),
        )
        yield name, answers


def test_python_answers_are_byte_identical_except_a_bare_name_that_was_only_a_module():
    golden = json.loads(GOLDEN.read_text())
    checked = changed = 0
    for fixture, recorded in golden.items():
        seen = set()
        for name, answers in python_answers(fixture):
            seen.add(name)
            entry = recorded[name]
            if entry["was"]:
                changed += 4
                for answer in answers:
                    assert "symbol" not in answer and "from" not in answer, (fixture, name)
                    assert answer["error"].startswith(f"'{name}' is the module {entry['was']}, which has no symbol of that name"), (fixture, name)
            else:
                for command, answer, digest in zip(COMMANDS, answers, entry["sha1"]):
                    checked += 1
                    assert hashlib.sha1(json.dumps(answer, sort_keys=True).encode()).hexdigest()[:16] == digest, (fixture, name, command)
        assert seen == set(recorded), fixture
    assert (checked, changed) == (604, 124)  # a recording that checks nothing proves nothing


def test_a_python_module_is_named_in_full_or_by_a_suffix_as_before():
    graph = build_graph(FIXTURES / "shop", PYTHON)
    assert queries.callers(graph, "shop.tax")["symbol"] == "shop.tax"
    assert queries.callers(graph, "shop")["symbol"] == "shop"  # a top-level module is its own id
    answer = queries.callers(graph, "tax")
    assert answer["error"].startswith("'tax' is the module shop.tax, which has no symbol of that name")
    assert answer["error"].endswith("pass one of the candidates, or the module id shop.tax to ask about the module")
    assert "shop.tax.tax_for" in answer["candidates"]


def test_a_dotted_suffix_of_a_python_module_is_still_the_module(make_repo):
    graph = build_graph(make_repo({"a/__init__.py": "", "a/b/__init__.py": "", "a/b/c.py": "def f():\n    pass\n"}), PYTHON)
    assert queries.callers(graph, "b.c")["symbol"] == "a.b.c"  # it says more than a name: a path the caller wrote
    assert queries.callers(graph, "a.b.c")["symbol"] == "a.b.c"
    assert "is the module" in queries.callers(graph, "c")["error"]


def test_a_python_module_named_like_a_function_it_defines_is_still_ambiguous(make_repo):
    graph = build_graph(make_repo({"pkg/__init__.py": "", "pkg/foo.py": "def foo():\n    pass\n"}), PYTHON)
    assert queries.find_symbol(graph, "foo") == {
        "error": "'foo' is ambiguous; pass one of the candidates",
        "candidates": ["pkg.foo", "pkg.foo.foo"],
    }
