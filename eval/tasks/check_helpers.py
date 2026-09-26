"""Shared by the hidden task checks, copied next to them at check time."""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def identifiers(root: Path = REPO) -> dict[str, set[str]]:
    """For each .py file in the repo, every name it defines, imports or uses.

    Names only, never strings or comments, so a docstring that mentions the
    old name is fine and a leftover call or import is not.
    """
    found = {}
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root)
        if rel.parts[0] == "_eval_check" or any(part.startswith(".") for part in rel.parts):
            continue
        names = set()
        for node in ast.walk(ast.parse(path.read_text(), str(path))):
            if isinstance(node, ast.Name):
                names.add(node.id)
            elif isinstance(node, ast.Attribute):
                names.add(node.attr)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(node.name)
            elif isinstance(node, ast.alias):
                names.add(node.name.split(".")[-1])
                if node.asname:
                    names.add(node.asname)
        found[str(rel)] = names
    return found


def files_using(name: str) -> list[str]:
    return sorted(f for f, names in identifiers().items() if name in names)


def params(fn) -> list[str]:
    return list(inspect.signature(fn).parameters)
