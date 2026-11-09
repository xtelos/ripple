"""Which scope a call in a decorator's arguments belongs to.

A decorator is applied when the decorated class is defined, so a call in its arguments runs in the scope that holds
the class: the module, or the function the class is written in. Ripple credits the call there, as an exact edge, only
when it can prove it. Where the class is written inside a class (a field initializer, a static block, a method of an
object literal) the scope that runs it is not provable: no edge is made, and the call is an unresolved call that says
so. These need tree-sitter and are skipped, with the reason, without it.
"""

import pytest
from conftest import FIXTURES  # noqa: F401  (keeps the conftest path set up)

pytest.importorskip("tree_sitter", reason="TypeScript support is not installed (pip install 'ripple-code[typescript]')")
pytest.importorskip("tree_sitter_typescript", reason="TypeScript support is not installed (pip install 'ripple-code[typescript]')")

from ripple.indexer import build_graph  # noqa: E402
from ripple.profile import TYPESCRIPT  # noqa: E402
from ripple.queries import callers  # noqa: E402

LIB = "".join(f"export function c{n}(..._a: unknown[]): number {{ return {n}; }}\n" for n in range(1, 17)) + (
    "export function dec(..._a: unknown[]) { return (..._b: unknown[]) => {}; }\n"
)

SOURCE = """\
import { dec, c1, c2, c3, c4, c5, c6, c7, c8, c9, c10, c11, c12, c13, c14, c15, c16 } from "./lib";

@dec(c1()) export class OnModuleClass {}
export class OnMembers {
  @dec(c2()) method(): void {}
  @dec(c3()) prop = 1;
  param(@dec(c4()) a: number): void {}
}
export function inFunction() {
  @dec(c5()) class Local {}
  class Members { @dec(c6()) method(): void {} }
  return [Local, Members];
}
export class Host {
  run() { @dec(c7()) class Nested {} return Nested; }
}
export class StaticField { static x = @dec(c8()) class {}; }
export class WithCtor {
  constructor() {}
  x = @dec(c9()) class {};
}
export class NoCtor { x = @dec(c10()) class {}; }
export class WithObj { y = { make() { @dec(c11()) class Q {} return Q; } }; }
export const AtModule = @dec(c12()) class {};
export function expression() { return @dec(c13()) class {}; }
export class StaticBlock { static { @dec(c14()) class Q {} } }
export class PlainField { z = c15(); }
export class MemberOfLocal { m() { class L { @dec(c16()) p = 1; } return L; } }
"""


@pytest.fixture(scope="module")
def graph(tmp_path_factory):
    root = tmp_path_factory.mktemp("decscope")
    (root / "src").mkdir()
    (root / "src/lib.ts").write_text(LIB)
    (root / "src/m.ts").write_text(SOURCE)
    (root / "tsconfig.json").write_text("{}")
    return build_graph(root, TYPESCRIPT)


def edge_of(graph, n):
    return sorted(e.caller for e in graph.edges if e.callee == f"src/lib:c{n}")


def unresolved_of(graph, n):
    return [u for u in graph.unresolved if u.text.endswith(f"c{n}") and u.line > 1 and u.reason != ""]


@pytest.mark.parametrize(
    "n, holder",
    [
        (1, "src/m"),  # a decorator on a class at module level: the module
        (2, "src/m"),  # on a method of a module-level class: the scope that holds the class
        (3, "src/m"),  # on a property
        (4, "src/m"),  # on a parameter
        (5, "src/m:inFunction"),  # on a class written in a function: the function
        (6, "src/m:inFunction"),  # on a member of a class written in a function: the function
        (7, "src/m:Host.run"),  # on a class written in a method: the method
        (8, "src/m"),  # a class expression in a static field initializer runs where the class is defined
        (9, "src/m:WithCtor.constructor"),  # in an instance field initializer: the constructor that runs it
        (12, "src/m"),  # a class expression at module level
        (13, "src/m:expression"),  # a class expression in a function
        (16, "src/m:MemberOfLocal.m"),  # a member of a class written in a method
    ],
)
def test_a_provable_scope_is_credited_with_an_exact_edge(graph, n, holder):
    assert edge_of(graph, n) == [holder]


@pytest.mark.parametrize("n", [10, 11, 14])
def test_a_class_written_inside_a_class_gives_no_edge_and_an_unresolved_call_that_says_why(graph, n):
    assert edge_of(graph, n) == []
    found = [u for u in graph.unresolved if u.file == "src/m.ts" and u.text == f"c{n}"]
    assert len(found) == 1
    assert "scope" in found[0].reason and found[0].caller.startswith("src/m")


def test_callers_of_such_a_callee_say_it_may_be_called_there(graph):
    result = callers(graph, "src/lib:c10")
    assert result["count"] == 0
    assert [m["location"] for m in result["possible_missed_callers"]] == [f"src/m.ts:{SOURCE.splitlines().index(next(l for l in SOURCE.splitlines() if 'c10' in l and 'NoCtor' in l)) + 1}"]


def test_the_plain_field_initializer_call_is_untouched(graph):
    assert edge_of(graph, 15) == ["src/m:PlainField"]
