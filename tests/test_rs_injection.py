"""Comments and inert attributes change nothing ripple says about a Rust repo.

A comment is not a token and an `#[allow(..)]` (or any attribute of the built-in inert list) generates nothing, so
inserting either at a token or an item boundary must leave the graph as it was: the same symbols, edges, unresolved
sites and reasons, outside-the-repo count and reasons, and files not read. Only the line a site is written on may
move (and is then not compared), because a `// c` and its newline push every later line down by one.

Every `.rs` file of every probe crate and of the golden fixture is edited at seeded boundaries, taken from the leaves of
its own parse tree: `/* c */` before or after any token, `// c` + newline before an item or a statement, and
`#[allow(unused)]` before an item or a `let`. Never inside a string, a char, a raw string, a lifetime, or a macro
token tree that ripple does not read as code (the arguments of a standard macro are read, so they are edited too).
Each variant is indexed again and its graph compared with the original's. This needs no Rust toolchain.
"""

import random
import shutil

import pytest

pytest.importorskip("tree_sitter_rust")

from conftest import FIXTURES  # noqa: E402
from ripple import rs_indexer as rs  # noqa: E402
from ripple.indexer import walk_languages  # noqa: E402
from ripple.profile import RUST  # noqa: E402

CRATES = FIXTURES / "rust-probes" / "crates"
GOLDEN = FIXTURES / "rust"
SEEDS = 4
PER_FILE = 14  # edits per file in one variant
ITEMS = frozenset(
    "function_item struct_item enum_item impl_item mod_item trait_item const_item static_item type_item use_declaration "
    "union_item extern_crate_declaration".split()
)
OPAQUE = frozenset(
    "string_literal raw_string_literal char_literal line_comment block_comment lifetime label loop_label shebang metavariable".split()
)
READ_MACROS = rs.STD_EXPR_MACROS | rs.STD_ARRAY_MACROS
INERT = b"#[allow(unused)] "
TARGETS = sorted([p.name for p in CRATES.iterdir() if p.is_dir() and not p.name.startswith("_")]) + ["golden"]


def candidates(src: bytes, root) -> list[tuple[int, str]]:
    """(byte offset, kind) of every place an edit may go: kind is block, line or attr."""
    found: list[tuple[int, str]] = []
    stack = [(root, False)]
    while stack:
        node, frozen = stack.pop()
        t = node.type
        if t in OPAQUE:
            continue
        if t == "token_tree":
            parent = node.parent
            mnode = parent.child_by_field_name("macro") if parent is not None and parent.type == "macro_invocation" else None
            name = rs.path_segs(mnode, src)[-1] if mnode is not None else ""
            frozen = frozen or name not in READ_MACROS
        if node.parent is not None and node.parent.type in ("source_file", "declaration_list", "block") and node.is_named:
            if t not in ("line_comment", "block_comment") and not frozen:
                found.append((node.start_byte, "line"))
                if t in ITEMS or t == "let_declaration":
                    found.append((node.start_byte, "attr"))
        if node.child_count == 0:
            if not frozen and t not in ("line_comment", "block_comment"):
                found.append((node.start_byte, "block"))
                found.append((node.end_byte, "block"))
            continue
        stack.extend((c, frozen) for c in node.children)
    return found


class Edits:
    """The edits made to one file, and the map back from a line of the edited file to the line it was written on."""

    def __init__(self, src: bytes, chosen: list[tuple[int, str]]):
        self.chosen = sorted(set(chosen))
        self.rows: set[int] = set()  # 1-based lines of the original that hold an edit
        starts = [0] + [i + 1 for i, b in enumerate(src) if b == 10]
        self.original_lines = len(starts)
        newlines: dict[int, int] = {}
        for offset, kind in self.chosen:
            row = max(i for i, s in enumerate(starts) if s <= offset)
            self.rows.add(row + 1)
            if kind == "line":
                newlines[row + 1] = newlines.get(row + 1, 0) + 1
        self.first: dict[int, int] = {}  # original line -> the first line of the edited file it became
        shift = 0
        for line in range(1, self.original_lines + 1):
            self.first[line] = line + shift
            shift += newlines.get(line, 0)
        self.last = {line: self.first[line] + newlines.get(line, 0) for line in self.first}

    def apply(self, src: bytes) -> bytes:
        out = src
        for offset, kind in reversed(self.chosen):
            text = {"block": b"/* c */", "line": b"// c\n", "attr": INERT}[kind]
            out = out[:offset] + text + out[offset:]
        return out

    def original(self, line: int) -> int | None:
        """The original line an edited-file line is part of, None when that line holds an edit (its place is not compared)."""
        for original in self.first:
            if self.first[original] <= line <= self.last[original]:
                return None if original in self.rows else original
        return None


def signature(graph, edits: dict[str, Edits]) -> dict:
    def at(file: str, line: int):
        return edits[file].original(line) if file in edits else line

    return {
        "symbols": sorted(((s.id, s.kind, s.file, at(s.file, s.line), at(s.file, s.end_line)) for s in graph.symbols.values()), key=repr),
        "edges": sorted(((e.caller, e.callee, e.file, at(e.file, e.line), e.kind) for e in graph.edges), key=repr),
        "unresolved": sorted(
            ((u.caller, u.file, at(u.file, u.line), "".join(u.text.split())[:40], u.reason) for u in graph.unresolved), key=repr
        ),
        "external": (graph.external_calls, sorted(graph.external_reasons.items())),
        "unread": graph.unread,
        "parse_errors": graph.parse_errors,
        "modules": sorted(graph.modules.items()),
    }


def read(root):
    files, skipped = walk_languages(root, [RUST])
    return rs.read_repo(root, files[RUST.name], skipped)[1]


def variants(root, tmp_path, name: str):
    """Yield (seed, edited copy of the crate, the edits by file) for each seed."""
    sources = sorted(p for p in root.rglob("*.rs") if not any(q.name == "target" and (q.parent / "Cargo.toml").is_file() for q in p.parents))
    parser = rs.load_parser()
    parsed = {p: (p.read_bytes(),) for p in sources}
    trees = {p: parser.parse(parsed[p][0]).root_node for p in sources}
    cands = {p: candidates(parsed[p][0], trees[p]) for p in sources}
    for seed in range(SEEDS):
        copy = tmp_path / f"{name}-{seed}"
        shutil.copytree(root, copy, ignore=lambda d, names: [n for n in names if n == "Cargo.lock" or (n == "target" and "Cargo.toml" in names)])
        edits: dict[str, Edits] = {}
        for p in sources:
            rng = random.Random(f"{name}:{p.relative_to(root)}:{seed}")
            pool = cands[p]
            chosen = rng.sample(pool, min(PER_FILE, len(pool)))
            rel = p.relative_to(root).as_posix()
            edits[rel] = Edits(parsed[p][0], chosen)
            (copy / rel).write_bytes(edits[rel].apply(parsed[p][0]))
        yield seed, copy, edits


@pytest.mark.parametrize("name", TARGETS)
def test_a_comment_or_an_inert_attribute_changes_nothing(name, tmp_path):
    root = GOLDEN if name == "golden" else CRATES / name
    before = read(root)
    for seed, copy, edits in variants(root, tmp_path, name):
        after = read(copy)
        # the original graph is read as written: its lines are the original lines, except those holding an edit
        expected = signature(before, {f: _identity(e) for f, e in edits.items()})
        got = signature(after, edits)
        for part in expected:
            assert got[part] == expected[part], f"{name} seed {seed}: {part} changed\n" + _diff(expected[part], got[part]) + _where(edits)


class _identity:
    """The original graph's own lines: unchanged, except None where an edit sits."""

    def __init__(self, edits: Edits):
        self.rows = edits.rows

    def original(self, line: int):
        return None if line in self.rows else line


def _diff(want, got) -> str:
    if isinstance(want, list):
        gone = [x for x in want if x not in got][:6]
        new = [x for x in got if x not in want][:6]
        return f"  missing: {gone}\n  extra: {new}"
    return f"  want {want}\n  got  {got}"


def _where(edits: dict[str, Edits]) -> str:
    return "\n  edits: " + "; ".join(f"{f}: {e.chosen[:20]}" for f, e in edits.items())


def test_the_suite_edits_a_meaningful_number_of_places(tmp_path):
    """A variant that edits nothing proves nothing: the probes must offer many boundaries of every kind."""
    kinds: dict[str, int] = {"block": 0, "line": 0, "attr": 0}
    parser = rs.load_parser()
    for source in CRATES.rglob("*.rs"):
        if "target" in source.parts:
            continue
        data = source.read_bytes()
        for _, kind in candidates(data, parser.parse(data).root_node):
            kinds[kind] += 1
    assert kinds["block"] > 5000 and kinds["line"] > 1000 and kinds["attr"] > 500, kinds
