"""A decoration through a receiver that may be a subclass is listed for the subclass method too, as a call is.

`r.add()` on a receiver typed `Base` reaches `Base.add` and, as an `override` edge, each subclass's `add`. `@reg.add()`
on the same receiver is not an edge (what a decorator does is not followed), but it is a site that may run `Sub.add`,
so it is a decoration of `Sub.add` as well. These need tree-sitter and are skipped, with the reason, without it.
"""

import pytest
from conftest import FIXTURES  # noqa: F401  (keeps the conftest path set up)

pytest.importorskip("tree_sitter", reason="TypeScript support is not installed (pip install 'ripple-code[typescript]')")
pytest.importorskip("tree_sitter_typescript", reason="TypeScript support is not installed (pip install 'ripple-code[typescript]')")

from ripple.indexer import build_graph  # noqa: E402
from ripple.profile import TYPESCRIPT  # noqa: E402
from ripple.queries import callers, impact  # noqa: E402

FILES = {
    "src/r.ts": """
    export class Base {
      add(): (t: unknown) => void { return () => {}; }
    }
    export class Sub extends Base {
      add(): (t: unknown) => void { return () => {}; }
    }
    export class Other {
      add(): (t: unknown) => void { return () => {}; }
    }
    export function pick(): Base { return new Sub(); }
    export const reg: Base = pick();
    export const exact = new Sub();
    export function viaCall(r: Base) { r.add(); }
    """,
    "src/u.ts": """
    import { reg, exact } from "./r";
    @reg.add()
    export class Thing {}
    @exact.add()
    export class Other2 {}
    """,
    "tsconfig.json": "{}",
}


BASE_TYPED = 3  # the line of `@reg.add()` in src/u.ts: the source starts with a newline


@pytest.fixture
def graph(make_repo):
    return build_graph(make_repo(FILES), TYPESCRIPT)


def sites(result, key="decorations"):
    return [d["applied_at"] for d in result.get(key, [])]


def test_a_plain_call_on_a_base_typed_receiver_gets_an_override_edge(graph):
    reached = {(e.callee, e.kind) for e in graph.edges if e.caller == "src/r:viaCall"}
    assert reached == {("src/r:Base.add", "call"), ("src/r:Sub.add", "override")}


def test_a_decoration_on_that_receiver_is_listed_for_the_override_too(graph):
    assert sorted(d.target for d in graph.decorations if d.holder == "src/u" and d.line == BASE_TYPED) == ["src/r:Base.add", "src/r:Sub.add"]
    assert "src/u.ts:3" in sites(callers(graph, "src/r:Sub.add"))
    assert "src/u.ts:3" in sites(callers(graph, "src/r:Base.add"))
    assert "src/u.ts:3" in sites(impact(graph, "src/r:Sub.add"))


def test_it_is_never_an_edge_and_never_listed_for_an_unrelated_class(graph):
    assert [e for e in graph.edges if e.file == "src/u.ts"] == []
    assert "decorations" not in callers(graph, "src/r:Other.add")


def test_a_receiver_of_known_exact_type_lists_only_that_class(graph):
    assert "src/u.ts:5" not in sites(callers(graph, "src/r:Base.add"))
    assert sites(callers(graph, "src/r:Sub.add")) == ["src/u.ts:3", "src/u.ts:5"]
