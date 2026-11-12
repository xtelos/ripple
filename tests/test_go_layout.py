"""Layout and comment invariance of the Go front end: the graph must not depend on how the same Go is written.

For every probe and golden fixture of bench/fixtures/go (one module or several, with every kind of build constraint and
parse error among them) the test writes variants of each .go file and indexes them:
  - `/*~*/` inserted after N seeded tokens (the boundaries are the tree-sitter leaves, never inside a string, rune, raw
    string or comment); a block comment holding no newline is a space to Go's lexer, so it is safe anywhere between tokens
    and never changes where a statement ends;
  - `//~` appended to N seeded lines that already end in a token (the newline is there already, so automatic semicolon
    insertion is unchanged);
  - every line's leading indentation stripped, and doubled (gofmt's layout is not what ripple may rely on);
  - the line ends changed to CRLF.
None of these adds or removes a line, so the graph (symbols and their lines, edges, unresolved sites with their texts and
reasons, externals, parse errors, skipped directories, the file to package map) must equal the original's exactly.
No Go toolchain is needed.
"""

import importlib.util
import random
import re
import shutil
from pathlib import Path

import pytest

from ripple.go_indexer import DAMAGED, UNDECLARED, index_files, load_parser
from ripple.indexer import walk_repo
from ripple.profile import GO

HAS_GO = importlib.util.find_spec("tree_sitter_go") is not None and importlib.util.find_spec("tree_sitter") is not None
pytestmark = pytest.mark.skipif(not HAS_GO, reason="Go support is not installed (pip install 'ripple-code[go]')")

FIXTURES = Path(__file__).resolve().parent.parent / "bench" / "fixtures" / "go"
SUBJECTS = sorted(p for p in (FIXTURES / "probes").iterdir() if p.is_dir()) + [FIXTURES / "shop", FIXTURES / "multimod"]
LITERALS = ("interpreted_string_literal", "raw_string_literal", "rune_literal", "comment")
SEEDS = 3
PER_FILE = 6
MARK = b"/*~*/"


def snapshot(repo: Path, edited: dict[str, set[int]] | None = None) -> dict:
    """The graph as plain values. A position on an edited line is not compared (error recovery may end a damaged
    node before or after a comment that was added to its last line), every other one is."""
    edited = edited or {}

    def at(file: str, line: int) -> int:
        return -1 if line in edited.get(file, ()) else line

    files, skipped = walk_repo(repo, GO)
    graph = index_files(repo, files, skipped)
    # What a file tree-sitter could not read holds is whatever its error recovery made of the text, and recovery costs
    # skipped bytes (indentation included): which declarations of it survive, where a damaged node ends and which call
    # nodes it holds can change with a comment or a layout. Those are left out; every answer about a file read
    # completely is compared, and so is what ripple decides of the files around a damaged one.
    damaged = set(graph.parse_errors)
    damaged_dirs = {f.rpartition("/")[0] for f in damaged}
    return {
        "symbols": sorted((s.id, s.kind, s.file, at(s.file, s.line), at(s.file, s.end_line)) for s in graph.symbols.values() if s.file not in damaged),
        "edges": sorted(
            (e.caller, e.callee, e.file, at(e.file, e.line)) for e in graph.edges if graph.symbols[e.callee].file not in damaged and e.file not in damaged
        ),
        "unresolved": sorted(
            (u.caller, u.file, at(u.file, u.line), u.text, u.reason)
            for u in graph.unresolved
            if DAMAGED not in u.reason and u.file not in damaged and not (u.reason == UNDECLARED and u.file.rpartition("/")[0] in damaged_dirs)
        ),
        "external": (graph.external_calls, sorted(graph.external_reasons.items())),
        "parse_errors": graph.parse_errors,
        "skipped_dirs": graph.skipped_dirs,
        "modules": sorted(graph.modules.items()),
    }


def token_ends(data: bytes) -> list[int]:
    """The byte offset after every token of the file that is not part of a string, rune, raw string or comment."""
    found, stack = set(), [(load_parser().parse(data).root_node, False)]
    while stack:
        node, inside = stack.pop()
        inside = inside or node.type in LITERALS
        if not node.children:
            if not inside and node.end_byte > node.start_byte:
                found.add(node.end_byte)
        else:
            stack.extend((child, inside) for child in node.children)
    return sorted(found)


def with_comments(data: bytes, rng: random.Random | None, newline_only: bool) -> bytes:
    """A comment after N seeded tokens, or after every token (no rng)."""
    ends = [e for e in token_ends(data) if not newline_only or data[e : e + 1] in (b"\n", b"\r")]
    chosen = sorted(ends if rng is None else rng.sample(ends, min(PER_FILE, len(ends))), reverse=True)
    for end in chosen:
        data = data[:end] + (b" //~" if newline_only else MARK) + data[end:]
    return data


def reindented(data: bytes, doubled: bool) -> bytes:
    return b"\n".join(
        (lambda lead, rest: (lead * 2 if doubled else b"") + rest)(*re.match(rb"([ \t]*)(.*)", line, re.S).groups()) for line in data.split(b"\n")
    )


def variants(data: bytes, seed: int):
    yield f"comments-{seed}", with_comments(data, random.Random(f"c{seed}{len(data)}"), False)
    yield f"line-comments-{seed}", with_comments(data, random.Random(f"l{seed}{len(data)}"), True)
    yield "comments-all", with_comments(data, None, False)
    yield "line-comments-all", with_comments(data, None, True)


def written(subject: Path, scratch: Path, how: str, seed: int = 0) -> tuple[Path, dict[str, set[int]]]:
    """A copy of the subject with every .go file rewritten, and the lines of each file that the rewrite touched."""
    target = scratch / how
    shutil.copytree(subject, target)
    edited: dict[str, set[int]] = {}
    for path in sorted(target.rglob("*.go")):
        data = original = path.read_bytes()
        if how.startswith("unindented"):
            data = reindented(data, False)
        elif how.startswith("doubled"):
            data = reindented(data, True)
        elif how.startswith("crlf"):
            data = data.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
        else:
            kind = how.rsplit("@", 1)[0]
            parser = load_parser()
            # a comment after every token of a file tree-sitter already cannot read changes what its recovery returns, and a
            # comment must not be what makes a clean file unreadable (a grammar limit, not ripple's): both keep the file
            if not (kind.endswith("-all") and parser.parse(original).root_node.has_error):
                data = dict(variants(data, seed))[kind]
                if parser.parse(data).root_node.has_error and not parser.parse(original).root_node.has_error:
                    data = original
        path.write_bytes(data)
        before, after = original.split(b"\n"), data.split(b"\n")
        assert len(before) == len(after), (path, how)
        edited[path.relative_to(target).as_posix()] = {i + 1 for i, (a, b) in enumerate(zip(before, after)) if a != b}
    return target, edited


@pytest.mark.parametrize("subject", SUBJECTS, ids=lambda p: p.name)
def test_the_graph_does_not_depend_on_comments_between_tokens_or_on_layout(subject, tmp_path):
    hows = [f"{kind}-{seed}@{seed}" for seed in range(SEEDS) for kind in ("comments", "line-comments")]
    hows += ["comments-all@0", "line-comments-all@0", "unindented", "doubled", "crlf"]
    for how in hows:
        target, edited = written(subject, tmp_path, how, int(how.rsplit("@", 1)[1]) if "@" in how else 0)
        comments = how.split("-")[0] in ("comments", "line")  # a layout change moves no node, so every position must match
        got = snapshot(target, edited if comments else None)
        expected = snapshot(subject, edited if comments else None)
        shutil.rmtree(target)
        for key in expected:
            assert got[key] == expected[key], f"{subject.name} as {how}: {key} differs"


def test_the_injection_really_changes_the_files(tmp_path):
    """A guard against a test that edits nothing: the variants differ from the original and add no line."""
    subject = FIXTURES / "probes" / "swallow_indented"
    original = (subject / "broken.go").read_bytes()
    for name, data in variants(original, 1):
        assert data != original and data.count(b"\n") == original.count(b"\n") and (MARK in data or b" //~" in data), name
    assert reindented(original, False) != original and reindented(original, True) != original


def test_a_comment_between_the_operands_of_an_expression_changes_nothing(tmp_path):
    """The shapes the seeded boundaries may not reach: a comment in front of an operator, inside a conversion, a type
    assertion, a composite literal, a method expression and a type switch."""
    plain = (
        "package m\n\ntype N int\n\nfunc (N) M() {}\n\ntype T struct{}\n\nfunc (T) P() {}\n\n"
        "func f(n N, a any, t *T) {\n\t(n << 1).M()\n\t(n + n).M()\n\tN(1).M()\n\ta.(T).P()\n\t(&T{}).P()\n\t(*t).P()\n\tT.P(*t)\n"
        "\tswitch v := a.(type) {\n\tcase T:\n\t\tv.P()\n\t}\n}\n"
    )
    commented = (
        "package m\n\ntype N int\n\nfunc (N) M() {}\n\ntype T struct{}\n\nfunc (T) P() {}\n\n"
        "func f(n N, a any, t *T) {\n\t(n /*c*/ << /*c*/ 1).M()\n\t(n /*c*/ + /*c*/ n).M()\n\tN /*c*/ (/*c*/ 1).M()\n\ta /*c*/ .(/*c*/ T).P()\n"
        "\t(&/*c*/ T /*c*/ {}).P()\n\t(/*c*/ * /*c*/ t).P()\n\tT /*c*/ . /*c*/ P(* /*c*/ t)\n"
        "\tswitch v := a.(/*c*/ type) {\n\tcase /*c*/ T:\n\t\tv /*c*/ . /*c*/ P()\n\t}\n}\n"
    )
    answers = []
    for source in (plain, commented):
        repo = tmp_path / f"r{len(answers)}"
        repo.mkdir()
        (repo / "go.mod").write_text("module ex.com/m\n\ngo 1.26\n")
        (repo / "a.go").write_text(source)
        answers.append(snapshot(repo))
    assert [e for e in answers[0]["edges"]] and answers[0]["edges"] == answers[1]["edges"]
    assert answers[0]["unresolved"] == answers[1]["unresolved"]
