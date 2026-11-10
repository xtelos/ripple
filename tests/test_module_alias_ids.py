"""A `module:Name` id that names an export alias of the module resolves like the bare name does.

Agents build ids as `src/components/SubMenu:SubMenu` (the module, a colon, the exported name). When the module exports
that name as a `memo`, `forwardRef` or alias of a function or class declared under another name, the id is no symbol,
and used to answer `no symbol named ...`. It now resolves to the wrapped symbol with the same note the bare name gets.
A name the module does not define or export stays an error, whose suggestions now begin with the module's own symbols
and export aliases (the `module:Name` forms that would resolve). An id that already resolves is answered as before.

The TypeScript tests need tree-sitter (the `typescript` extra) and are skipped without it.
"""

import importlib.util
import json
import sys

import pytest
from conftest import FIXTURES
from stdio_client import StdioClient

from ripple import queries
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
    export function helper() { return 1; }
    """,
    "src/App.tsx": """
    import { SubMenu } from './components/SubMenu';
    export function App() { return <SubMenu />; }
    export function Other() { return <SubMenu />; }
    """,
}
SUBMENU_NOTE = "SubMenu is exported by src/components/SubMenu as forwardRef(SubMenuFR); showing src/components/SubMenu:SubMenuFR"
SUBMENU_FR = "src/components/SubMenu:SubMenuFR"


def index(make_repo, files):
    return build_graph(make_repo(files), TYPESCRIPT)


# ---- an alias id resolves to the wrapped symbol, with the note the bare name gets


@ts
def test_a_forward_ref_alias_id_resolves_with_the_note(make_repo):
    graph = index(make_repo, SUBMENU)
    assert queries.find_symbol(graph, "src/components/SubMenu:SubMenu") == {"symbol": SUBMENU_FR, "note": SUBMENU_NOTE}
    assert queries.find_symbol(graph, "SubMenu") == queries.find_symbol(graph, "src/components/SubMenu:SubMenu")
    answer = queries.callers(graph, "src/components/SubMenu:SubMenu")
    assert (answer["symbol"], answer["count"], answer["note"]) == (SUBMENU_FR, 2, SUBMENU_NOTE)
    assert answer == queries.callers(graph, "SubMenu")


@ts
def test_every_question_resolves_the_alias_id(make_repo):
    graph = index(make_repo, SUBMENU)
    alias = "src/components/SubMenu:SubMenu"
    callees = queries.callees(graph, alias)
    assert (callees["symbol"], callees["note"]) == (SUBMENU_FR, SUBMENU_NOTE)
    impact = queries.impact(graph, alias)
    assert (impact["symbol"], impact["affected"], impact["note"]) == (SUBMENU_FR, 2, SUBMENU_NOTE)
    found = queries.path(graph, "src/App:App", alias)
    assert (found["to"], found["note"]) == (SUBMENU_FR, SUBMENU_NOTE) and found["path"] is not None
    assert queries.path(graph, alias, alias)["note"] == SUBMENU_NOTE  # said once, whichever end it applies to
    none = queries.path(graph, alias, "src/App:App")
    assert (none["from"], none["path"], none["note"]) == (SUBMENU_FR, None, SUBMENU_NOTE)


@ts
def test_the_module_may_be_named_by_a_path_as_any_id_can(make_repo):
    graph = index(make_repo, SUBMENU)
    for query in ("components/SubMenu:SubMenu", "src/components/SubMenu.tsx:SubMenu", "components/SubMenu.tsx:SubMenu"):
        assert queries.find_symbol(graph, query) == {"symbol": SUBMENU_FR, "note": SUBMENU_NOTE}, query


@ts
def test_a_memo_alias_id_resolves_and_names_nested_wrappers(make_repo):
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
    card = queries.callers(graph, "src/Card:Card")
    assert (card["symbol"], card["count"]) == ("src/Card:Inner", 1)
    assert card["note"] == "Card is exported by src/Card as memo(Inner); showing src/Card:Inner"
    both = queries.callers(graph, "src/Both:Both")
    assert (both["symbol"], both["count"]) == ("src/Both:Raw", 1)
    assert both["note"] == "Both is exported by src/Both as memo(forwardRef(Raw)); showing src/Both:Raw"


@ts
def test_a_plain_alias_id_resolves(make_repo):
    graph = index(
        make_repo,
        {
            "src/Widget.ts": "function WidgetImpl() { return 1; }\nexport { WidgetImpl as Widget };\n",
            "src/use.ts": "import { Widget } from './Widget';\nexport function go() { return Widget(); }\n",
        },
    )
    answer = queries.callers(graph, "src/Widget:Widget")
    assert answer["symbol"] == "src/Widget:WidgetImpl"
    assert [c["symbol"] for c in answer["callers"]] == ["src/use:go"]
    assert answer["note"] == "Widget is exported by src/Widget as an alias of WidgetImpl; showing src/Widget:WidgetImpl"


BARREL = {
    "src/Menu/index.ts": "export { SubMenu as Menu } from './SubMenu';\nexport { Item } from './Item';\n",
    "src/Menu/SubMenu.tsx": """
    import React, { forwardRef } from 'react';
    const SubMenuFR = () => <li />;
    export const SubMenu = forwardRef(SubMenuFR);
    """,
    "src/Menu/Item.ts": "export function Item() { return 1; }\n",
    "src/use.tsx": "import { Menu } from './Menu';\nimport { Item } from './Menu';\nexport function U() { Item(); return <Menu />; }\n",
}


@ts
def test_a_barrel_alias_id_resolves_and_names_every_hop(make_repo):
    graph = index(make_repo, BARREL)
    answer = queries.callers(graph, "src/Menu:Menu")
    assert (answer["symbol"], answer["count"]) == ("src/Menu/SubMenu:SubMenuFR", 1)
    assert answer["note"] == (
        "Menu is exported by src/Menu as a re-export of SubMenu from src/Menu/SubMenu, which exports it as forwardRef(SubMenuFR); "
        "showing src/Menu/SubMenu:SubMenuFR"
    )
    assert answer == queries.callers(graph, "Menu")


@ts
def test_a_barrels_plain_re_export_id_resolves_with_a_note_because_the_id_is_not_the_symbols(make_repo):
    graph = index(make_repo, BARREL)
    assert queries.find_symbol(graph, "src/Menu:Item") == {
        "symbol": "src/Menu/Item:Item",
        "note": "Item is exported by src/Menu as a re-export of src/Menu/Item:Item; showing src/Menu/Item:Item",
    }


@ts
def test_two_modules_that_export_the_name_by_a_shared_path_tail_are_ambiguous_when_they_mean_two_symbols(make_repo):
    graph = index(
        make_repo,
        {
            "src/a/Card.tsx": "import React, { memo } from 'react';\nfunction InnerA() { return <b />; }\nexport const Card = memo(InnerA);\n",
            "src/b/Card.tsx": "import React, { memo } from 'react';\nfunction InnerB() { return <b />; }\nexport const Card = memo(InnerB);\n",
        },
    )
    assert queries.find_symbol(graph, "src/a/Card:Card")["symbol"] == "src/a/Card:InnerA"
    assert queries.find_symbol(graph, "src/b/Card:Card")["symbol"] == "src/b/Card:InnerB"
    assert queries.find_symbol(graph, "Card:Card") == {
        "error": "'Card:Card' is ambiguous; pass one of the candidates",
        "candidates": ["src/a/Card:InnerA", "src/b/Card:InnerB"],
    }


MONOREPO = {
    "packages/a/src/components/SubMenu.tsx": "export function SubMenu() { return <li />; }\n",
    "packages/b/src/components/SubMenu.tsx": """
    import React from 'react';
    const SubMenuFR = () => <li />;
    export const SubMenu = React.forwardRef(SubMenuFR);
    """,
    "packages/a/src/use.tsx": "import { SubMenu } from './components/SubMenu';\nexport function useA() { return <SubMenu />; }\n",
}
MONOREPO_AMBIGUOUS = [
    "packages/a/src/components/SubMenu:SubMenu",
    "packages/b/src/components/SubMenu:SubMenuFR",
]


@ts
def test_a_path_tail_of_a_declaring_module_and_an_aliasing_module_is_ambiguous(make_repo):
    """The bare name `SubMenu` lists both; a tail that names both modules must not settle on the one that declares it."""
    graph = index(make_repo, MONOREPO)
    expected = {"error": "'components/SubMenu:SubMenu' is ambiguous; pass one of the candidates", "candidates": MONOREPO_AMBIGUOUS}
    assert queries.find_symbol(graph, "components/SubMenu:SubMenu") == expected
    for ask in (queries.callers, queries.callees, queries.impact):
        assert ask(graph, "components/SubMenu:SubMenu") == expected
    assert queries.path(graph, "components/SubMenu:SubMenu", "packages/a/src/use:useA") == expected
    assert queries.find_symbol(graph, "src/components/SubMenu:SubMenu")["candidates"] == MONOREPO_AMBIGUOUS
    assert queries.find_symbol(graph, "SubMenu:SubMenu")["candidates"] == MONOREPO_AMBIGUOUS
    assert queries.find_symbol(graph, "SubMenu")["candidates"] == MONOREPO_AMBIGUOUS  # as the bare name answers


@ts
def test_a_tail_that_names_one_module_and_a_full_id_are_as_before(make_repo):
    graph = index(make_repo, MONOREPO)
    assert queries.find_symbol(graph, "packages/a/src/components/SubMenu:SubMenu") == {"symbol": "packages/a/src/components/SubMenu:SubMenu"}
    assert queries.find_symbol(graph, "packages/a/src/components/SubMenu.tsx:SubMenu") == {"symbol": "packages/a/src/components/SubMenu:SubMenu"}
    assert queries.find_symbol(graph, "a/src/components/SubMenu:SubMenu") == {"symbol": "packages/a/src/components/SubMenu:SubMenu"}  # one module ends so
    assert queries.find_symbol(graph, "b/src/components/SubMenu:SubMenu") == {
        "symbol": "packages/b/src/components/SubMenu:SubMenuFR",
        "note": "SubMenu is exported by packages/b/src/components/SubMenu as forwardRef(SubMenuFR); showing packages/b/src/components/SubMenu:SubMenuFR",
    }


@ts
def test_a_ts_and_a_tsx_file_of_one_stem_that_declare_and_alias_the_name_are_ambiguous_by_a_tail(make_repo):
    graph = index(
        make_repo,
        {
            "src/x.ts": "export function Thing() { return 1; }\n",
            "src/x.tsx": "import React from 'react';\nconst ThingFR = () => <li />;\nexport const Thing = React.forwardRef(ThingFR);\n",
        },
    )
    assert queries.find_symbol(graph, "x:Thing") == {
        "error": "'x:Thing' is ambiguous; pass one of the candidates",
        "candidates": ["src/x.tsx:ThingFR", "src/x:Thing"],
    }
    assert queries.find_symbol(graph, "src/x:Thing") == {"symbol": "src/x:Thing"}  # a full id, never reinterpreted
    assert queries.find_symbol(graph, "src/x.tsx:Thing")["symbol"] == "src/x.tsx:ThingFR"


@ts
def test_a_tail_whose_modules_mean_one_symbol_is_that_symbol(make_repo):
    graph = index(
        make_repo,
        {"src/a/lib.ts": "export function Item() { return 1; }\n", "src/b/lib.ts": "export { Item } from '../a/lib';\n"},
    )  # src/b/lib re-exports the function src/a/lib declares: both modules of the tail mean one symbol
    assert queries.find_symbol(graph, "lib:Item") == {"symbol": "src/a/lib:Item"}


# ---- a name the module exports as something ripple cannot name is never settled on a symbol


CARD_CONFLICT = {
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


@ts
def test_a_conflict_alias_whose_id_is_the_declared_symbol_still_answers_as_that_symbol(make_repo):
    """The id `src/Card:Card` is the declared function's own id, so it is not a name that resolves nothing: it was and stays
    that symbol (the bare name `Card` is the ambiguous answer)."""
    graph = index(make_repo, CARD_CONFLICT)
    assert graph.export_aliases["src/Card:Card"][0] == ""
    assert queries.find_symbol(graph, "src/Card:Card") == {"symbol": "src/Card:Card"}
    assert queries.callers(graph, "Card")["error"] == "'Card' is ambiguous; pass one of the candidates"


@ts
def test_a_tail_of_a_module_that_declares_the_name_and_exports_it_as_something_else_is_the_declared_symbol(make_repo):
    """`render:render` ends the id of a symbol (`src/render:render`), so it is that symbol, as the full id is; only the
    bare name `render` is the ambiguous answer. The alias of the same module is not a second meaning of the tail."""
    graph = index(
        make_repo,
        {
            "src/render.ts": """
            function render() { return 1; }
            function renderV2() { return 2; }
            export { renderV2 as render };
            """,
        },
    )
    assert graph.export_aliases["src/render:render"][0] == "src/render:renderV2"
    assert queries.find_symbol(graph, "render:render") == {"symbol": "src/render:render"}
    assert queries.find_symbol(graph, "src/render:render") == {"symbol": "src/render:render"}
    assert "error" in queries.find_symbol(graph, "render")


@ts
def test_a_conflict_alias_with_no_declared_symbol_answers_an_error_that_picks_nothing(make_repo):
    graph = index(make_repo, CARD_CONFLICT)
    del graph.symbols["src/Card:Card"]  # no real graph has this (a conflict needs the declared symbol), so build it
    for ask in (queries.callers, queries.callees, queries.impact):
        answer = ask(graph, "src/Card:Card")
        assert "symbol" not in answer
        assert answer["error"] == "'src/Card:Card': src/Card exports Card as something ripple cannot name as one function or class"
        assert answer["candidates"] == ["src/Card:Fast", "src/Card:Local"]


# ---- a name the module has nothing of: the suggestions begin with what would resolve


@ts
def test_a_missing_name_in_an_existing_module_suggests_the_modules_own_symbols_and_aliases_first(make_repo):
    graph = index(make_repo, SUBMENU)
    answer = queries.callers(graph, "src/components/SubMenu:SubMenuF")
    assert answer == {
        "error": "no symbol named 'src/components/SubMenu:SubMenuF'",
        "suggestions": [
            "src/components/SubMenu:SubMenuFR",
            "src/components/SubMenu:SubMenu",
            "src/components/SubMenu:helper",
            "src/components/SubMenu",
        ],
    }
    for ask in (queries.callees, queries.impact):
        assert ask(graph, "src/components/SubMenu:SubMenuF") == answer
    assert queries.path(graph, "src/components/SubMenu:SubMenuF", "src/App:App") == answer
    assert queries.path(graph, "src/App:App", "src/components/SubMenu:SubMenuF") == answer
    # every suggestion that is a `module:Name` of that module resolves
    for form in answer["suggestions"]:
        assert "symbol" in queries.find_symbol(graph, form), form


@ts
def test_the_module_of_a_missing_name_may_be_named_by_a_path_too(make_repo):
    graph = index(make_repo, SUBMENU)
    for module in ("components/SubMenu", "src/components/SubMenu.tsx", "components/SubMenu.tsx"):
        answer = queries.callers(graph, f"{module}:SubMenuF")
        assert answer["suggestions"][:3] == ["src/components/SubMenu:SubMenuFR", "src/components/SubMenu:SubMenu", "src/components/SubMenu:helper"], module


@ts
def test_the_modules_own_forms_leave_out_members_and_other_modules_and_come_before_the_fuzzy_ones(make_repo):
    graph = index(
        make_repo,
        {
            "src/shape.ts": "export class Shape { area() { return 1; } }\nexport function make() { return new Shape(); }\n",
            "src/other.ts": "export function makeShape() { return 1; }\n",
        },
    )
    answer = queries.callers(graph, "src/shape:makeShapes")
    assert answer["error"] == "no symbol named 'src/shape:makeShapes'"
    assert answer["suggestions"] == ["src/shape:Shape", "src/shape:make", "src/other:makeShape"]


@ts
def test_a_module_with_more_forms_than_the_cap_says_how_many_it_left_out(make_repo):
    names = [f"fn{i:03d}" for i in range(queries.MAX_LISTED + 5)]
    graph = index(make_repo, {"src/big.ts": "".join(f"export function {n}() {{ return 1; }}\n" for n in names)})
    answer = queries.callers(graph, "src/big:nothing")
    assert len([s for s in answer["suggestions"] if s.startswith("src/big:")]) == queries.MAX_LISTED
    assert answer["error"] == f"no symbol named 'src/big:nothing' (the module has {len(names)} symbols and exports, showing {queries.MAX_LISTED})"


@ts
def test_a_tail_that_names_many_modules_counts_what_it_lists_across_them(make_repo):
    per_module = 40
    files = {f"pkg{m}/big.ts": "".join(f"export function f{m}x{i:02d}() {{ return 1; }}\n" for i in range(per_module)) for m in range(3)}
    graph = index(make_repo, files)
    answer = queries.callers(graph, "big:nothing")
    assert answer["error"] == f"no symbol named 'big:nothing' (3 modules match, {3 * per_module} symbols and exports in all, showing {queries.MAX_LISTED})"
    assert len(answer["suggestions"]) >= queries.MAX_LISTED


@ts
def test_a_tail_that_names_several_modules_but_lists_them_all_says_nothing_of_a_cut(make_repo):
    graph = index(make_repo, {"pkgA/s.ts": "export function one() { return 1; }\n", "pkgB/s.ts": "export function two() { return 1; }\n"})
    assert queries.callers(graph, "s:nothing")["error"] == "no symbol named 's:nothing'"


@ts
def test_a_module_with_no_symbols_of_its_own_keeps_the_old_suggestions(make_repo):
    graph = index(make_repo, {"src/types/transform.ts": "export interface Transform { transform(): void }\n", "src/use.ts": "export function use() { return 1; }\n"})
    assert queries.callers(graph, "src/types/transform:Transform.transform") == {
        "error": "no symbol named 'src/types/transform:Transform.transform'",
        "suggestions": ["src/types/transform"],
    }


@ts
def test_a_nonexistent_module_keeps_the_old_error(make_repo):
    graph = index(make_repo, SUBMENU)
    assert queries.callers(graph, "src/nope/Thing:SubMenu") == {"error": "no symbol named 'src/nope/Thing:SubMenu'", "suggestions": ["src/components/SubMenu", "src/components/SubMenu:SubMenuFR"]}
    assert queries.callers(graph, "src/nope/Thing:Nothing") == {"error": "no symbol named 'src/nope/Thing:Nothing'", "suggestions": []}


# ---- an id that already resolves is answered exactly as before (pinned from the answers before this change)


@ts
def test_ids_that_resolved_are_unchanged(make_repo):
    graph = index(make_repo, SUBMENU)
    assert queries.find_symbol(graph, SUBMENU_FR) == {"symbol": SUBMENU_FR}
    assert queries.find_symbol(graph, "components/SubMenu:SubMenuFR") == {"symbol": SUBMENU_FR}
    assert queries.find_symbol(graph, "src/components/SubMenu") == {"symbol": "src/components/SubMenu"}
    assert queries.find_symbol(graph, "src/components/SubMenu:helper") == {"symbol": "src/components/SubMenu:helper"}
    assert queries.callers(graph, SUBMENU_FR) == {
        "symbol": SUBMENU_FR,
        "count": 2,
        "callers": [
            {"symbol": "src/App:App", "kind": "function", "location": "src/App.tsx:3", "call_sites": ["src/App.tsx:3"], "edge_kinds": ["renders"], "sites_by_kind": {"renders": ["src/App.tsx:3"]}},
            {"symbol": "src/App:Other", "kind": "function", "location": "src/App.tsx:4", "call_sites": ["src/App.tsx:4"], "edge_kinds": ["renders"], "sites_by_kind": {"renders": ["src/App.tsx:4"]}},
        ],
        "through_wrapper": [],
    }
    # a name that is ambiguous between modules still lists them, and a Class.member id is unchanged
    two = index(make_repo, {"src/z/util.ts": "export function f() { return 1; }\n"})
    assert queries.find_symbol(two, "util:f") == {"symbol": "src/z/util:f"}


# ---- the alias id on every surface


@ts
def test_cli_text_and_json_carry_the_note_for_an_alias_id(make_repo, capsys):
    repo = str(make_repo(SUBMENU))
    assert main(["callers", "src/components/SubMenu:SubMenu", "--repo", repo, "--lang", "typescript"]) == 0
    text = capsys.readouterr().out
    assert text.splitlines()[0] == f"note: {SUBMENU_NOTE}"
    assert "callers of src/components/SubMenu:SubMenuFR (2):" in text
    assert main(["callers", "src/components/SubMenu:SubMenu", "--repo", repo, "--lang", "typescript", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert (payload["symbol"], payload["note"]) == (SUBMENU_FR, SUBMENU_NOTE)
    assert main(["impact", "src/components/SubMenu:SubMenu", "--repo", repo, "--lang", "typescript"]) == 0
    assert capsys.readouterr().out.splitlines()[0] == f"note: {SUBMENU_NOTE}"


@ts
def test_cli_prints_the_modules_own_forms_first_for_a_missing_name(make_repo, capsys):
    repo = str(make_repo(SUBMENU))
    assert main(["callers", "src/components/SubMenu:SubMenuF", "--repo", repo, "--lang", "typescript"]) == 1
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "no symbol named 'src/components/SubMenu:SubMenuF'"
    assert lines[1:3] == ["  src/components/SubMenu:SubMenuFR", "  src/components/SubMenu:SubMenu"]


@ts
def test_the_mcp_tool_answers_an_alias_id_with_the_note(make_repo, tmp_path):
    repo = make_repo(SUBMENU)
    client = StdioClient(
        [sys.executable, "-m", "ripple", "serve", "--repo", str(repo), "--lang", "typescript"],
        env={"RIPPLE_CACHE_DIR": str(tmp_path / "cache")},
    )
    try:
        client.initialize()
        for tool in ("callers", "callees", "impact"):
            reply = client.request("tools/call", {"name": tool, "arguments": {"symbol": "src/components/SubMenu:SubMenu"}})
            payload = json.loads(reply["result"]["content"][0]["text"])
            assert (payload["symbol"], payload["note"]) == (SUBMENU_FR, SUBMENU_NOTE), tool
        reply = client.request("tools/call", {"name": "path", "arguments": {"source": "src/App:App", "target": "src/components/SubMenu:SubMenu"}})
        payload = json.loads(reply["result"]["content"][0]["text"])
        assert (payload["to"], payload["note"]) == (SUBMENU_FR, SUBMENU_NOTE)
        reply = client.request("tools/call", {"name": "callers", "arguments": {"symbol": "src/components/SubMenu:SubMenuF"}})
        payload = json.loads(reply["result"]["content"][0]["text"])
        assert payload["suggestions"][0] == "src/components/SubMenu:SubMenuFR"
    finally:
        client.close()


# ---- Python has no `module:Name` form: its answers are as they were


def test_a_python_colon_query_is_the_old_error_with_the_old_suggestions():
    graph = build_graph(FIXTURES / "shop", PYTHON)
    for query in ("shop.tax:tax_for", "shop/tax:tax_for", "shop.tax.py:tax_for", "shop.tax:nothing", "tax:tax_for"):
        answer = queries.callers(graph, query)
        assert answer["error"] == f"no symbol named '{query}'", query
        assert "note" not in answer
    assert queries.callers(graph, "shop.tax:tax_for") == {
        "error": "no symbol named 'shop.tax:tax_for'",
        "suggestions": ["shop.tax.tax_for", "shop.tax"],
    }
