"""The type text ripple compares method signatures by: an array length has Go's integer-literal grammar, a channel direction is read
as Go reads it, and two type expressions have one text only when the spec makes them one type."""

import importlib.util

import pytest

from ripple.go_indexer import array_length
from ripple.profile import GO

HAS_GO = importlib.util.find_spec("tree_sitter_go") is not None and importlib.util.find_spec("tree_sitter") is not None
needs_parser = pytest.mark.skipif(not HAS_GO, reason="Go support is not installed (pip install 'ripple-code[go]')")

# ---- array lengths --------------------------------------------------------------------------------------------------


class Node:
    def __init__(self, kind: str, written: str):
        self.type, self.text = kind, written.encode()


@pytest.mark.parametrize(
    ("kind", "written", "length"),
    [
        ("int_literal", "0", 0), ("int_literal", "7", 7), ("int_literal", "10", 10), ("int_literal", "010", 8), ("int_literal", "00", 0),
        ("int_literal", "0777", 511), ("int_literal", "0_7", 7), ("int_literal", "1_000", 1000), ("int_literal", "0x10", 16),
        ("int_literal", "0XfF", 255), ("int_literal", "0x_1F", 31), ("int_literal", "0o17", 15), ("int_literal", "0O17", 15),
        ("int_literal", "0b101", 5), ("int_literal", "0B1_1", 3), ("rune_literal", "'a'", 97), ("rune_literal", "'\\n'", 10),
        ("rune_literal", "'\\x41'", 65), ("rune_literal", "'\\101'", 65), ("rune_literal", "'\\u00e9'", 233), ("rune_literal", "'\\''", 39),
        ("rune_literal", "'\\\\'", 92), ("rune_literal", "'é'", 233),
        ("int_literal", "08", None), ("int_literal", "1__0", None), ("int_literal", "0x", None), ("int_literal", "0b12", None),
        ("int_literal", "1_", None), ("rune_literal", "'\\400'", None), ("rune_literal", "'\\\"'", None), ("rune_literal", "'ab'", None),
        ("float_literal", "1e1", None), ("identifier", "N", None), ("binary_expression", "1<<3", None), ("interpreted_string_literal", '"a"', None),
    ],
)
def test_an_array_length_has_gos_integer_literal_grammar_and_anything_else_is_refused(kind, written, length):
    assert array_length(Node(kind, written)) == length


def test_there_is_no_length_without_a_node():
    assert array_length(None) is None


# ---- the type text of every constructor ------------------------------------------------------------------------------


def canonical_texts(tmp_path, expressions: list[str]) -> list[str | None]:
    from ripple.go_indexer import analyse
    from ripple.indexer import walk_repo

    fields = "\n".join(f"\tf{i} {e}" for i, e in enumerate(expressions))
    (tmp_path / "go.mod").write_text("module example.com/c\n\ngo 1.22\n")
    (tmp_path / "a.go").write_text(f"package a\n\ntype Named struct{{}}\n\ntype X struct {{\n{fields}\n}}\n")
    files, _ = walk_repo(tmp_path, GO)
    program, resolver = analyse(tmp_path, files, GO)
    package = next(p for p in program.packages.values())
    holder = package.types["X"]
    return [resolver.canonical(holder.fields[f"f{i}"], holder.file) for i in range(len(expressions))]


@needs_parser
def test_two_type_expressions_have_one_text_only_when_they_are_one_type(tmp_path):
    same = [
        ["chan<- chan int", "chan <-chan int", "chan<- (chan int)"],
        ["<-chan (<-chan int)", "<-chan <-chan int"],
        ["chan chan int", "chan (chan int)"],
        ["*int", "(*int)", "*(int)"],
        ["[]int", "([]int)"],
        ["byte", "uint8"],
        ["rune", "int32"],
        ["interface{}", "any"],
        ["[010]int", "[8]int", "[0o10]int", "[0x8]int", "[0b1000]int", "[0_10]int"],
    ]
    different = [
        "chan (<-chan int)", "<-chan chan int", "chan<- <-chan int", "<-chan chan<- int",
        "chan int", "<-chan int", "chan<- int", "*[3]int", "[3]*int", "*[]int", "[]*int", "[][]int", "*[][]int", "[]*[]int",
        "[2][3]int", "[3][2]int", "map[string]chan<- int", "map[string]<-chan int", "map[chan int]string", "map[string]chan int",
        "map[int][]string", "[]map[int]string", "[10]int", "[1]int", "Named", "*Named", "[]Named", "string", "[]string",
    ]
    groups = same + [[d] for d in different]
    flat = [e for g in groups for e in g]
    texts = canonical_texts(tmp_path, flat)
    assert None not in texts
    seen: dict[str, int] = {}
    at = 0
    for index, group in enumerate(groups):
        for _ in group:
            assert seen.setdefault(texts[at], index) == index, f"{flat[at]} shares its text with another type"
            at += 1
    assert len(set(texts)) == len(groups)


@needs_parser
def test_a_type_that_ripple_cannot_say_has_no_text(tmp_path):
    texts = canonical_texts(tmp_path, ["func() func() int", "[N]int", "[1e1]int", "[1<<3]int", "struct{}", "interface{ M() }", "chan func()"])
    assert texts == [None] * 7
