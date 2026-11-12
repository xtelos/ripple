"""A block comment between two tokens changes nothing ripple says.

For every probe repo (tests/fixtures/js_probes) and the TypeScript and JavaScript bench fixtures, a copy of the repo gets a
`/* c */` before a seeded share of the token boundaries of every source file, and the graph of the copy (symbols, edges,
unresolved calls with their reasons, calls outside the repo and why, files that did not parse, skipped directories) must be the
graph of the original. A boundary is the start of a tree-sitter leaf; none is taken inside a string, a template literal, a regex
literal or JSX text, nor where JSX children begin (a comment there is text), and a file that does not parse cleanly is left alone.
The comment has no newline, so it adds none: lines do not move and the two graphs are compared as they are, after the
comment is taken out of the text of the strings that quote source. Every boundary gets its comment in exactly one of
VARIANTS copies per repo (a seeded shuffle says which), so each is tried and a repo is indexed VARIANTS times, not once per boundary.

This runs through the front end TypeScript and JavaScript share, so a difference found in a .ts file is a bug of the TypeScript
support. It needs tree-sitter and no node.
"""

import json
import random
import re
import shutil
from pathlib import Path

import pytest
from conftest import FIXTURES

pytest.importorskip("tree_sitter", reason="TypeScript support is not installed (pip install 'ripple-code[typescript]')")
pytest.importorskip("tree_sitter_typescript", reason="TypeScript support is not installed (pip install 'ripple-code[typescript]')")

from ripple.cache import load_repo  # noqa: E402
from ripple.ts_indexer import load_parsers  # noqa: E402

PROBES = Path(__file__).resolve().parent / "fixtures" / "js_probes" / "probes"
COMMENT = b" /* c */ "
SUFFIXES = (".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs")
INSIDE = {"string", "template_string", "regex", "jsx_text", "template_literal_type"}  # a comment inside these is text, not a comment
SKIP_LEAVES = {"jsx_text", "html_character_reference", "hash_bang_line", "comment"}
JSX_CHILDREN = {"jsx_element", "jsx_fragment"}
VARIANTS = 3
SEED = 20261112
REPOS = {f"probe:{p.name}": p for p in sorted(PROBES.iterdir()) if p.is_dir()}
REPOS["bench:warehouse-ts"] = FIXTURES / "ts" / "warehouse-ts"
REPOS["bench:inventory-js"] = FIXTURES / "js" / "inventory-js"


def boundaries(source: bytes, parser) -> list[int] | None:
    """The byte offsets a comment may be put at, or None when the file does not parse cleanly."""
    root = parser.parse(source).root_node
    if root.has_error:
        return None
    found: list[int] = []
    stack = [(root, ())]
    while stack:
        node, ancestors = stack.pop()
        if node.child_count:
            stack.extend((child, (*ancestors, node)) for child in reversed(node.children))
            continue
        point = node.start_byte
        if node.end_byte == point or node.type in SKIP_LEAVES or any(a.type in INSIDE and a.start_byte < point for a in ancestors):
            continue
        # the nearest node that starts before here holds the node that starts here: when that is a JSX element or fragment, the
        # point is between its children (or its fragment's `<>`), where a comment is text
        container = next((a for a in reversed(ancestors) if a.start_byte < point), None)
        if container is not None and container.type in JSX_CHILDREN:
            continue
        found.append(point)
    # Where the grammar cannot read the file with the comment (`a? /* c */ : T` is an error in tree-sitter-typescript, though not in
    # TypeScript), the file is listed with those that did not parse cleanly, as any damaged file is: that is the grammar's limit and
    # no answer of ripple's, so such a boundary is left out.
    return sorted(p for p in set(found) if not parser.parse(source[:p] + COMMENT + source[p:]).root_node.has_error)


def parser_for(path: Path):
    typescript, tsx = load_parsers()
    return tsx if path.suffix in (".tsx", ".jsx", ".js", ".mjs", ".cjs") else typescript


def plan(repo: Path) -> dict[str, list[int]]:
    """{file: its boundaries} for every source file of the repo that parses cleanly."""
    found = {}
    for path in sorted(repo.rglob("*")):
        if path.is_file() and path.suffix in SUFFIXES and "node_modules" not in path.parts:
            points = boundaries(path.read_bytes(), parser_for(path))
            if points:
                found[path.relative_to(repo).as_posix()] = points
    return found


def commented(source: bytes, points: list[int]) -> bytes:
    for point in sorted(points, reverse=True):
        source = source[:point] + COMMENT + source[point:]
    return source


def variants(repo: Path, key: str) -> list[dict[str, list[int]]]:
    """VARIANTS lists of {file: offsets}: every boundary of the repo is in exactly one of them."""
    everything = [(file, point) for file, points in plan(repo).items() for point in points]
    random.Random(f"{SEED}:{key}").shuffle(everything)
    out: list[dict[str, list[int]]] = [{} for _ in range(VARIANTS)]
    for index, (file, point) in enumerate(everything):
        out[index % VARIANTS].setdefault(file, []).append(point)
    return out


def write_variant(repo: Path, target: Path, chosen: dict[str, list[int]]) -> None:
    shutil.copytree(repo, target, ignore=shutil.ignore_patterns("expected.json", "__pycache__"))
    for file, points in chosen.items():
        path = target / file
        path.write_bytes(commented(path.read_bytes(), points))


def clean(value):
    """Every string with the comment out and no white space at all (the text of a call is its source, where a space or the comment
    sits between tokens, and which of them was written there is no difference in what ripple says)."""
    if isinstance(value, str):
        return re.sub(r"\s+", "", value.replace("/* c */", ""))
    if isinstance(value, list):
        return [clean(v) for v in value]
    if isinstance(value, dict):
        return {clean(k): clean(v) for k, v in value.items()}
    return value


def snapshot(repo: Path, cache: Path) -> dict:
    graph = load_repo(repo, cache_dir=cache, lang="auto").graph
    return clean(json.loads(json.dumps(graph.to_dict(), sort_keys=True)))


def unresolved_difference(left: list[dict], right: list[dict]) -> list[str]:
    """The unresolved calls that are not the same. The text of one is the first 80 characters of its source, comments included, so with
    comments in it is a shorter start of the same expression: texts count as equal when one begins with the other."""
    def keyed(items):
        out: dict[tuple, list[str]] = {}
        for u in items:
            out.setdefault((u["caller"], u["file"], u["line"], u["reason"]), []).append(u["text"].split("/*")[0])  # the cut may fall inside a comment
        return {k: sorted(v) for k, v in out.items()}

    a, b = keyed(left), keyed(right)
    found = [f"unresolved: only in the original: {k}" for k in a if k not in b][:4] + [f"unresolved: only with the comments: {k}" for k in b if k not in a][:4]
    for key in a.keys() & b.keys():
        # (two characters of leeway: the cut may fall between the `/` and the `*` that begin a comment)
        if len(a[key]) != len(b[key]) or any(x[: min(len(x), len(y)) - 2] != y[: min(len(x), len(y)) - 2] for x, y in zip(a[key], b[key])):
            found.append(f"unresolved: the text of {key} became {b[key]} from {a[key]}")
    return found


def difference(left: dict, right: dict) -> list[str]:
    found = unresolved_difference(left.get("unresolved", []), right.get("unresolved", []))
    left, right = {k: v for k, v in left.items() if k != "unresolved"}, {k: v for k, v in right.items() if k != "unresolved"}
    for key in sorted(set(left) | set(right)):
        if left.get(key) != right.get(key):
            a, b = left.get(key), right.get(key)
            if isinstance(a, list) and isinstance(b, list):
                found += [f"{key}: only in the original: {x}" for x in a if x not in b][:4] + [f"{key}: only with the comments: {x}" for x in b if x not in a][:4]
            elif isinstance(a, dict) and isinstance(b, dict):
                found += [f"{key}[{k}]: {a.get(k)!r} became {b.get(k)!r}" for k in sorted(set(a) | set(b)) if a.get(k) != b.get(k)][:4]
            else:
                found.append(f"{key}: {a!r} became {b!r}")
    return found


def culprits(repo: Path, chosen: dict[str, list[int]], tmp: Path, want: dict) -> list[str]:
    """The single boundaries whose comment alone changes the graph (only run when a variant failed)."""
    found = []
    for file, points in chosen.items():
        source = (repo / file).read_bytes()
        for point in points:
            target = tmp / f"single-{len(found)}-{abs(hash((file, point)))}"
            write_variant(repo, target, {file: [point]})
            if difference(want, snapshot(target, tmp / "cache-single")):
                line = source[:point].count(b"\n") + 1
                found.append(f"{file}:{line} before {source[point:point + 24]!r}")
                if len(found) >= 6:
                    return found
    return found


@pytest.mark.parametrize("key", list(REPOS))
def test_a_comment_between_tokens_changes_nothing_ripple_says(key, tmp_path):
    repo = REPOS[key]
    original = snapshot(repo, tmp_path / "cache-original")
    for number, chosen in enumerate(variants(repo, key)):
        if not chosen:
            continue
        target = tmp_path / f"variant-{number}"
        write_variant(repo, target, chosen)
        wrong = difference(original, snapshot(target, tmp_path / f"cache-{number}"))
        assert not wrong, f"{key} variant {number}: {len(wrong)} differences\n" + "\n".join(wrong) + "\nthe boundaries that do it alone: " + "; ".join(culprits(repo, chosen, tmp_path, original))


def test_the_injection_covers_many_boundaries_in_both_languages():
    total, languages = 0, set()
    for key, repo in REPOS.items():
        for file, points in plan(repo).items():
            total += len(points)
            languages.add("typescript" if file.endswith((".ts", ".tsx", ".mts", ".cts")) else "javascript")
    assert total >= 8000 and languages == {"typescript", "javascript"}, total
