"""Record the call edges a fixture's test suite actually executes.

This is how the hand-written ground truth is kept honest. The trace runs a
fixture's own pytest suite under ``sys.setprofile`` and writes every
(caller, callee) pair between repo-defined functions to a JSON file.

It deliberately shares no code with the ripple indexer: it has its own tiny
AST pass to name functions, so a bug in ripple cannot make the ground truth
agree with ripple.

Usage (runs in a fresh interpreter so fixtures cannot see each other):

    python bench/trace.py bench/fixtures/shop /tmp/shop-edges.json
"""

from __future__ import annotations

import ast
import json
import os
import sys
from pathlib import Path

ANONYMOUS = {"<lambda>", "<listcomp>", "<setcomp>", "<dictcomp>", "<genexpr>"}


def module_name(root: Path, path: Path) -> str:
    parts = list(path.relative_to(root).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


class Namer:
    """Maps (file, line) to the qualified name of the enclosing def."""

    def __init__(self, root: Path):
        self.root = root
        self.defs: dict[str, list[tuple[int, int, str, str]]] = {}

    def _load(self, filename: str) -> list[tuple[int, int, str, str]]:
        if filename not in self.defs:
            path = Path(filename)
            tree = ast.parse(path.read_text(), filename)
            found: list[tuple[int, int, str, str]] = []

            def visit(node: ast.AST, prefix: str) -> None:
                for child in ast.iter_child_nodes(node):
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                        qual = f"{prefix}.{child.name}"
                        start = min([child.lineno] + [d.lineno for d in child.decorator_list])
                        found.append((start, child.end_lineno, child.name, qual))
                        visit(child, qual)
                    else:
                        visit(child, prefix)

            visit(tree, module_name(self.root, path))
            self.defs[filename] = found
        return self.defs[filename]

    def name(self, code) -> str | None:
        """Qualified name for a code object, or None if it is outside the fixture."""
        filename = os.path.abspath(code.co_filename)
        if not filename.startswith(str(self.root) + os.sep) or not filename.endswith(".py"):
            return None
        line = code.co_firstlineno
        if code.co_name == "<module>":
            return module_name(self.root, Path(filename))
        # Innermost def containing the line. A named function must match by
        # name; anonymous code (lambda, comprehension) belongs to its enclosing def.
        anonymous = code.co_name in ANONYMOUS
        candidates = [
            (start, qual)
            for start, end, name, qual in self._load(filename)
            if start <= line <= end and (anonymous or name == code.co_name)
        ]
        if not candidates:
            return module_name(self.root, Path(filename))
        return max(candidates)[1]


def main(fixture: str, out: str) -> int:
    root = Path(fixture).resolve()
    sys.path[0] = str(root)
    os.chdir(root)
    namer = Namer(root)
    edges: set[tuple[str, str]] = set()

    def profiler(frame, event, arg):
        if event != "call" or frame.f_code.co_name in ANONYMOUS or frame.f_back is None:
            return
        callee = namer.name(frame.f_code)
        if callee is None:
            return
        # Skip frames that are not repo code (runpy, functools, pytest), so
        # "no other repo-defined function in between" holds.
        back, caller = frame.f_back, None
        while back is not None and caller is None:
            caller = namer.name(back.f_code)
            back = back.f_back
        if caller is not None and caller != callee:
            edges.add((caller, callee))

    import pytest

    sys.setprofile(profiler)
    try:
        code = pytest.main(["-q", "-p", "no:cacheprovider", "--rootdir", str(root), str(root / "tests")])
    finally:
        sys.setprofile(None)
    Path(out).write_text(json.dumps(sorted(edges), indent=1))
    return int(code)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
