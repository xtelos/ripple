"""Shared by the hidden task checks, copied next to them at check time."""

from __future__ import annotations

import ast
import inspect
import json
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


def expected_unparseable() -> set[str]:
    """Repo-relative files the pinned repository lists as not meant to parse (RepoSpec.unparseable),
    written next to this file at check time. None of the pinned repositories has any."""
    listed = Path(__file__).resolve().parent / "expected_unparseable.json"
    return set(json.loads(listed.read_text())) if listed.is_file() else set()


def sources(root: Path = REPO, allowed: set[str] | None = None):
    """(repo-relative path, parsed tree) for each .py file in the repo.

    A file that does not parse is a failure, not a skip: the files an agent breaks are the
    ones whose stale callers this scan most needs to see. Only a file the repo's spec lists
    as expected to fail on the untouched pin (RepoSpec.unparseable) is left out."""
    allowed = expected_unparseable() if allowed is None else allowed
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root)
        if rel.parts[0] == "_eval_check" or any(part.startswith(".") for part in rel.parts):
            continue
        try:
            yield str(rel), ast.parse(path.read_text(), str(path))
        except (SyntaxError, UnicodeDecodeError, ValueError) as err:
            if rel.as_posix() in allowed:
                continue
            raise AssertionError(f"{rel.as_posix()} does not parse ({type(err).__name__}: {err})") from err


def references(name: str, root: Path = REPO) -> list[tuple[str, int, str, str]]:
    """Every place the repo mentions `name` as a name (never in strings or comments).

    Each is (file, line, kind, detail). kind is one of:
      attr    x.name            detail: the receiver's source, e.g. "self" or "Color"
      name    a bare name       detail: ""
      import  from m import name         detail: the module
      def     def name(...)     detail: the enclosing class, "" at module level
      param   a parameter called name    detail: the function it belongs to
      kwarg   f(name=...)       detail: the callee's source
    """
    found = []
    for rel, tree in sources(root):
        classes = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                for child in node.body:
                    classes[id(child)] = node.name
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == name:
                found.append((rel, node.end_lineno, "attr", ast.unparse(node.value)))
            elif isinstance(node, ast.Name) and node.id == name:
                found.append((rel, node.lineno, "name", ""))
            elif isinstance(node, ast.ImportFrom):
                found += [(rel, node.lineno, "import", node.module or "") for a in node.names if a.name == name]
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name == name:
                    found.append((rel, node.lineno, "def", classes.get(id(node), "")))
                a = node.args
                for arg in a.posonlyargs + a.args + a.kwonlyargs:
                    if arg.arg == name:
                        found.append((rel, arg.lineno, "param", node.name))
            elif isinstance(node, ast.Call):
                found += [(rel, node.lineno, "kwarg", ast.unparse(node.func)) for k in node.keywords if k.arg == name]
    return sorted(set(found))


def files_using(name: str) -> list[str]:
    return sorted(f for f, names in identifiers().items() if name in names)


def params(fn) -> list[str]:
    return list(inspect.signature(fn).parameters)
