"""A comment changes nothing: the graph of a Java file with comments written into it is the graph of the file without them.

tree-sitter keeps a comment as an "extra" node that can stand between any two tokens (inside an argument list, a type
list, a modifier list, a type, a name), so every place that counts, indexes or takes the first of a node's children is
open to it. This takes every Java file of every probe (bench/fixtures/java/probes) and of the golden fixture, writes
comments into it, indexes the result and requires the graph to be the original's: the same symbols, edges, unresolved
sites with their reasons, and external calls. No JDK is needed.

  * block variants: ` /* c */ ` (padded: `/` and `/* c */` side by side would be a line comment) at seeded token boundaries (BLOCK_VARIANTS variants, BLOCK_AT boundaries of each file in
    each), then one variant with a comment at EVERY token boundary of every file;
  * line variants: `// c` and a newline after seeded statement ends (`;`, `{`, `}`), then at every statement end.

A token boundary is where two leaf nodes of the parse tree meet, never inside a string, a char or a text block. The
newlines a line variant adds move the lines after them, so a line number is compared as the line the original had.
"""

import random
from collections import defaultdict

import pytest
from conftest import FIXTURES

pytest.importorskip("tree_sitter_java")

from ripple.java_indexer import index_files, load_parser  # noqa: E402
from ripple.profile import JAVA  # noqa: E402

JAVA_FIXTURES = FIXTURES / "java"
ROOTS = sorted([*(p.parent / "src" for p in (JAVA_FIXTURES / "probes").rglob("src") if p.is_dir()), JAVA_FIXTURES / "shop-java"])
BLOCK_VARIANTS, BLOCK_AT, LINE_VARIANTS, LINE_AT = 4, 8, 2, 4
LITERALS = ("string_literal", "character_literal")
STATEMENT_ENDS = (";", "{", "}")


def leaves(node):
    found, stack = [], [node]
    while stack:
        n = stack.pop()
        if n.child_count == 0:
            found.append(n)
        else:
            stack.extend(reversed(n.children))
    return found


def boundaries(source: bytes) -> tuple[list[int], list[int]]:
    """(the offsets between two tokens, the offsets after a statement end): where a comment may be written."""
    tokens = leaves(load_parser().parse(source).root_node)
    every, ends = [], []
    for a, b in zip(tokens, tokens[1:]):
        if a.type == "line_comment":  # what follows it on its line is inside it
            continue
        inside, parent = False, a.parent
        while parent is not None:
            if parent.type in LITERALS and parent.start_byte < a.end_byte < parent.end_byte:
                inside = True
            parent = parent.parent
        if inside:
            continue
        every.append(a.end_byte)
        if a.type in STATEMENT_ENDS:
            ends.append(a.end_byte)
    return every, ends


def write_into(source: bytes, offsets: list[int], piece: bytes) -> tuple[bytes, list[int]]:
    """The source with the piece written at each offset, and the lines (in the result) that the pieces' newlines end."""
    out, last, line, added = [], 0, 1, []
    for offset in sorted(offsets):
        chunk = source[last:offset]
        out.append(chunk)
        line += chunk.count(b"\n")
        out.append(piece)
        if b"\n" in piece:
            added.append(line)
            line += piece.count(b"\n")
        last = offset
    out.append(source[last:])
    return b"".join(out), added


def graph_of(root, files: dict[str, bytes], tmp, added: dict[str, list[int]]):
    """The graph of the files written below tmp, as plain data with every line mapped back to the original's."""
    for rel, source in files.items():
        path = tmp / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(source)
    graph = index_files(tmp, sorted(tmp.rglob("*.java")), [], JAVA)

    def back(file, line):
        return line - sum(1 for a in added.get(file, ()) if a < line)

    def bare(text):  # a comment leaves a blank where it was: the text shown for a call is compared without them
        return "".join(text.split())[:40]

    return {
        "symbols": sorted((s.id, s.kind, s.file, back(s.file, s.line), back(s.file, s.end_line)) for s in graph.symbols.values()),
        "edges": sorted((e.caller, e.callee, e.file, back(e.file, e.line), e.kind) for e in graph.edges),
        "unresolved": sorted((u.caller, u.file, back(u.file, u.line), bare(u.text), "".join(u.reason.split())) for u in graph.unresolved),
        "external": (graph.external_calls, sorted(graph.external_reasons.items())),
        "modules": sorted(graph.modules.items()),
        "bases": (sorted(graph.bases.items()), sorted(graph.external_bases.items())),
        "parse_errors": graph.parse_errors,
    }


def differences(original: dict, variant: dict) -> list[str]:
    found = []
    for key in original:
        if original[key] != variant[key]:
            a, b = original[key], variant[key]
            if isinstance(a, list):
                found += [f"{key}: lost {x}" for x in a if x not in b][:3] + [f"{key}: gained {x}" for x in b if x not in a][:3]
            else:
                found.append(f"{key}: {a} became {b}")
    return found


def variants(root, files: dict[str, bytes]):
    """(a description, the changed files, the lines each added) for every variant."""
    points = {rel: boundaries(source) for rel, source in files.items()}
    for kind, piece, at, count, index in (("block", b" /* c */ ", BLOCK_AT, BLOCK_VARIANTS, 0), ("line", b" // c\n", LINE_AT, LINE_VARIANTS, 1)):
        for n in [*range(count), None]:  # None: every boundary
            changed, added = {}, {}
            for rel, source in files.items():
                found = points[rel][index]
                chosen = found if n is None else random.Random(f"{root}:{rel}:{kind}:{n}").sample(found, min(at, len(found)))
                changed[rel], added[rel] = write_into(source, chosen, piece)
            yield f"{kind} variant {'at every boundary' if n is None else n}", changed, added


@pytest.mark.parametrize("root", ROOTS, ids=[str(r.relative_to(JAVA_FIXTURES)) for r in ROOTS])
def test_comments_written_into_the_source_change_nothing(root, tmp_path_factory):
    files = {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*.java"))}
    original = graph_of(root, files, tmp_path_factory.mktemp("original"), {})
    assert original["symbols"], "the probe indexes to nothing"
    problems = []
    for description, changed, added in variants(root, files):
        variant = graph_of(root, changed, tmp_path_factory.mktemp("variant"), added)
        problems += [f"{description}: {line}" for line in differences(original, variant)]
        if problems:
            break
    assert problems == []


def test_the_variants_are_many_and_really_write_comments():
    sizes, files, comments = defaultdict(int), 0, 0
    for root in ROOTS:
        sources = {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*.java"))}
        files += len(sources)
        for description, changed, _ in variants(root, sources):
            sizes[description.split(" at ")[0].split(" variant")[0]] += 1
            comments += sum(c.count(b"/* c */") + c.count(b"// c") for c in changed.values()) - sum(
                s.count(b"/* c */") + s.count(b"// c") for s in sources.values()
            )
    assert files >= 200 and sizes["block"] >= 5 * len(ROOTS) and sizes["line"] >= 3 * len(ROOTS)
    assert comments >= 20000, comments
