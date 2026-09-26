"""Walk a repository and record what each scope defines, imports and calls.

This pass is purely syntactic: it reads one file at a time and writes down
facts ("module m binds name x to an import of y", "function f calls
self.repo.save on line 12"). It does not decide what a call points at. That
needs the whole repository in view, so it happens afterwards in resolver.py.

Keeping the two apart means each piece can be read, and tested, on its own.
"""

from __future__ import annotations

import ast
import os
from dataclasses import dataclass, field
from pathlib import Path

from .model import Graph

# Directories that hold dependencies or caches, never the project's own
# source, wherever they appear. Hidden directories (.git, .venv, .tox, ...),
# *.egg-info and any directory holding a virtualenv (pyvenv.cfg) are skipped too.
SKIP_ANYWHERE = {"__pycache__", "node_modules", "venv", "site-packages"}
# Build output and a virtualenv called env, skipped only at the repo root:
# deeper down these names can be real packages (pypa/build keeps its code in
# src/build).
SKIP_AT_ROOT = {"build", "dist", "env"}


@dataclass
class TypeRef:
    """One piece of evidence about the class of a value.

    kind is "annotation" (expr is a type written in the source), "call"
    (expr is the thing that was called; it only counts if it is a class),
    "name" (the value was copied from another variable, named by expr) or
    "builtin" (a literal; expr is the builtin type name, e.g. "list").
    scope is the id of the scope the expression should be resolved in.
    """

    kind: str
    expr: str
    scope: str


@dataclass
class Binding:
    """What a name means inside one scope.

    kind is "def" (a function or class defined here; target is its id),
    "import" (a module; target is its dotted name), "from" (target is the
    module and name is the member imported from it), "self" (the first
    parameter of a method) or "var" (anything assigned; evidence lists what
    we know about its type, with None for an assignment we cannot type).
    """

    kind: str
    target: str = ""
    name: str = ""
    evidence: list = field(default_factory=list)


@dataclass
class RawCall:
    """A call site before resolution.

    chain is the callee as dotted parts (["self", "repo", "save"]), or None
    when the callee is computed (a subscript, the result of another call).
    special marks "super" (chain is the part after super()), "computed",
    "builtin" (a method on a literal such as "-".join) or "decorator_result"
    (applying what a decorator factory returned).
    """

    chain: list | None
    text: str
    line: int
    kind: str = "call"
    special: str = ""


@dataclass
class Scope:
    id: str
    kind: str  # "module" | "class" | "function" | "method"
    module: str
    file: str
    line: int
    end_line: int
    parent: str | None
    names: dict = field(default_factory=dict)
    calls: list = field(default_factory=list)
    globals: set = field(default_factory=set)
    nonlocals: set = field(default_factory=set)
    # functions only: the return annotation, and whether it is a @property
    returns: str | None = None
    is_property: bool = False
    # classes only
    bases: list = field(default_factory=list)
    attr_types: dict = field(default_factory=dict)
    # modules only
    is_package: bool = False
    star_imports: list = field(default_factory=list)

    def bind(self, name: str, binding: Binding) -> None:
        """Record a binding. A name bound two different ways becomes unknown."""
        old = self.names.get(name)
        if old is None:
            self.names[name] = binding
        elif old.kind == "var" and binding.kind == "var":
            old.evidence.extend(binding.evidence)
        elif (old.kind, old.target, old.name) != (binding.kind, binding.target, binding.name):
            self.names[name] = Binding("var", evidence=[None])


def walk_repo(repo: Path) -> tuple[list[Path], list[str]]:
    """(the .py files to index, the directories skipped as not project source).

    The skipped list is for reporting, so it leaves out hidden directories,
    __pycache__ and *.egg-info: they never hold source and would be noise.
    """
    files, skipped = [], []
    for dirpath, dirnames, filenames in os.walk(repo):
        here = Path(dirpath)
        keep = []
        for d in sorted(dirnames):
            if d.startswith(".") or d == "__pycache__" or d.endswith(".egg-info"):
                continue
            if d in SKIP_ANYWHERE or (here == repo and d in SKIP_AT_ROOT) or (here / d / "pyvenv.cfg").exists():
                skipped.append((here / d).relative_to(repo).as_posix())
            else:
                keep.append(d)
        dirnames[:] = keep
        files.extend(here / f for f in sorted(filenames) if f.endswith(".py"))
    return files, sorted(skipped)


def find_python_files(repo: Path) -> list[Path]:
    return walk_repo(repo)[0]


def module_name(repo: Path, path: Path) -> tuple[str, bool]:
    """Dotted module name for a file, and whether it is a package __init__.

    A leading src/ is dropped, since in the src layout that directory is on
    sys.path and never appears in import statements.
    """
    parts = list(path.relative_to(repo).with_suffix("").parts)
    if len(parts) > 1 and parts[0] == "src":
        parts = parts[1:]
    is_package = parts[-1] == "__init__"
    if is_package:
        parts = parts[:-1]
    return ".".join(parts) or "__init__", is_package


def dotted(node: ast.AST) -> list | None:
    """["a", "b", "c"] for the expression a.b.c, None for anything else."""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        return [node.id] + parts[::-1]
    return None


def base_name(node: ast.AST) -> str:
    """How a base class is written: "pkg.Base" for pkg.Base and for pkg.Base[int].

    Subscripting a generic class (Repo[int], Generic[T]) still gives you that
    class, so the subscript is dropped. Anything else (a call such as
    with_metaclass(...)) is kept as source text, which the resolver reports
    as a base it cannot resolve.
    """
    if isinstance(node, ast.Subscript):
        node = node.value
    parts = dotted(node)
    return ".".join(parts) if parts else ast.unparse(node)[:80]


def annotation_type(node: ast.AST | None) -> str | None:
    """The class an annotation names, unwrapping Optional[X], X | None and "X".

    Containers such as list[X] return None: the value is a list, not an X.
    """
    if node is None:
        return None
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        try:
            return annotation_type(ast.parse(node.value, mode="eval").body)
        except SyntaxError:
            return None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        sides = [s for s in (node.left, node.right) if not _is_none(s)]
        return annotation_type(sides[0]) if len(sides) == 1 else None
    if isinstance(node, ast.Subscript):
        outer = dotted(node.value) or []
        if outer and outer[-1] in ("Optional", "Union"):
            inner = node.slice.elts if isinstance(node.slice, ast.Tuple) else [node.slice]
            inner = [s for s in inner if not _is_none(s)]
            return annotation_type(inner[0]) if len(inner) == 1 else None
        if outer and outer[-1] in CONTAINER_HINTS:
            return CONTAINER_HINTS[outer[-1]]
        return None
    parts = dotted(node)
    return ".".join(parts) if parts else None


def _is_none(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and node.value is None


LITERALS = {
    ast.List: "list",
    ast.ListComp: "list",
    ast.Dict: "dict",
    ast.DictComp: "dict",
    ast.Set: "set",
    ast.SetComp: "set",
    ast.Tuple: "tuple",
    ast.JoinedStr: "str",
}


def literal_type(node: ast.AST) -> str | None:
    """The builtin type a literal evaluates to ("list" for [1, 2]), else None."""
    for node_type, name in LITERALS.items():
        if isinstance(node, node_type):
            return name
    if isinstance(node, ast.Constant) and node.value is not None and node.value is not Ellipsis:
        return type(node.value).__name__
    return None


# Container annotations whose value is known to be a builtin, e.g. Dict[str, int].
CONTAINER_HINTS = {
    "list": "list",
    "List": "list",
    "dict": "dict",
    "Dict": "dict",
    "set": "set",
    "Set": "set",
    "frozenset": "frozenset",
    "FrozenSet": "frozenset",
    "tuple": "tuple",
    "Tuple": "tuple",
}


class ScopeBuilder(ast.NodeVisitor):
    """Visits one module and fills in Scope records for it and everything inside."""

    def __init__(self, module: str, file: str, is_package: bool, tree: ast.Module, line_count: int):
        self.scopes: dict[str, Scope] = {}
        root = Scope(module, "module", module, file, 1, max(line_count, 1), None, is_package=is_package)
        self.scopes[module] = root
        self.stack = [root]
        for stmt in tree.body:
            self.visit(stmt)

    @property
    def scope(self) -> Scope:
        return self.stack[-1]

    # -- definitions -------------------------------------------------------

    def _enter(self, node, kind: str) -> Scope:
        parent = self.scope
        existing = self.scopes.get(f"{parent.id}.{node.name}")
        if existing is not None:
            # Redefinition (a property setter, a def in each branch of an if):
            # fold it into the first so no call sites are lost.
            return existing
        scope = Scope(
            f"{parent.id}.{node.name}",
            kind,
            parent.module,
            parent.file,
            node.lineno,
            node.end_lineno,
            parent.id,
        )
        self.scopes[scope.id] = scope
        parent.bind(node.name, Binding("def", scope.id))
        return scope

    def _decorators(self, node) -> None:
        """A decorator is called by the enclosing scope when the def runs."""
        for dec in node.decorator_list:
            self.visit(dec)  # records calls inside it, e.g. route("/x")
            text = ast.unparse(dec)[:80]
            chain = dotted(dec) or []
            if len(chain) == 2 and chain[1] in ("setter", "getter", "deleter"):
                # @value.setter is a method of the builtin property object.
                self.scope.calls.append(RawCall(None, text, dec.lineno, "decorator", "builtin"))
            elif isinstance(dec, ast.Call):
                self.scope.calls.append(RawCall(dotted(dec.func), text, dec.lineno, "decorator", "decorator_result"))
            else:
                self.scope.calls.append(RawCall(dotted(dec), text, dec.lineno, "decorator"))

    def visit_FunctionDef(self, node) -> None:
        self._decorators(node)
        for default in node.args.defaults + [d for d in node.args.kw_defaults if d is not None]:
            self.visit(default)
        is_method = self.scope.kind == "class"
        scope = self._enter(node, "method" if is_method else "function")
        decorators = {(dotted(d) or [""])[-1] for d in node.decorator_list}
        # "or": a property setter reuses the getter's scope and must not reset these.
        scope.returns = scope.returns or annotation_type(node.returns)
        scope.is_property = scope.is_property or bool(decorators & {"property", "cached_property"})
        params = node.args.posonlyargs + node.args.args
        static = "staticmethod" in decorators
        for i, arg in enumerate(params + node.args.kwonlyargs):
            if i == 0 and is_method and not static and params:
                scope.bind(arg.arg, Binding("self"))
                continue
            hint = annotation_type(arg.annotation)
            # Annotations are evaluated where the def runs (for a method, the
            # class body), so they are resolved there, not inside the function.
            evidence = [TypeRef("annotation", hint, self.scope.id) if hint else None]
            scope.bind(arg.arg, Binding("var", evidence=evidence))
        for extra in (node.args.vararg, node.args.kwarg):
            if extra is not None:
                scope.bind(extra.arg, Binding("var", evidence=[None]))
        self.stack.append(scope)
        for stmt in node.body:
            self.visit(stmt)
        self.stack.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node) -> None:
        self._decorators(node)
        for base in node.bases + [k.value for k in node.keywords]:
            self.visit(base)
        scope = self._enter(node, "class")
        scope.bases = [base_name(b) for b in node.bases]
        self.stack.append(scope)
        for stmt in node.body:
            self.visit(stmt)
        self.stack.pop()

    # -- imports -----------------------------------------------------------

    def visit_Import(self, node) -> None:
        for alias in node.names:
            if alias.asname:
                self.scope.bind(alias.asname, Binding("import", alias.name))
            else:
                head = alias.name.split(".")[0]
                self.scope.bind(head, Binding("import", head))

    def visit_ImportFrom(self, node) -> None:
        base = self._absolute(node.module, node.level)
        for alias in node.names:
            if alias.name == "*":
                self.stack[0].star_imports.append(base)
            else:
                self.scope.bind(alias.asname or alias.name, Binding("from", base, alias.name))

    def _absolute(self, module: str | None, level: int) -> str:
        """Turn a relative import (from ..x import y) into an absolute module name."""
        if level == 0:
            return module or ""
        root = self.stack[0]
        package = root.id.split(".") if root.is_package else root.id.split(".")[:-1]
        if level > 1:
            package = package[: len(package) - (level - 1)]
        return ".".join(package + ([module] if module else []))

    # -- assignments and other name bindings -------------------------------

    def _evidence(self, value: ast.AST | None) -> list:
        """What an assigned value tells us about its class, as TypeRefs.

        None adds no evidence: nothing can be called on None, so a later
        `self.conn = Conn()` still decides what self.conn.send() means.
        """
        here = self.scope.id
        if value is None or _is_none(value):
            return []
        if literal_type(value):
            return [TypeRef("builtin", literal_type(value), here)]
        if isinstance(value, ast.Call):
            chain = dotted(value.func)
            return [TypeRef("call", ".".join(chain), here) if chain else None]
        if isinstance(value, ast.Name):
            return [TypeRef("name", value.id, here)]
        if isinstance(value, ast.BoolOp) and isinstance(value.op, ast.Or):
            refs = []
            for operand in value.values:
                if not _is_none(operand):
                    refs.extend(self._evidence(operand))
            return refs or [None]
        return [None]

    def _bind_target(self, target: ast.AST, evidence: list) -> None:
        if isinstance(target, ast.Name):
            if target.id in self.scope.globals:
                # `global x; x = ...` rebinds the module's x.
                self.stack[0].bind(target.id, Binding("var", evidence=list(evidence)))
            elif target.id in self.scope.nonlocals:
                # `nonlocal x; x = ...` rebinds x in the enclosing function.
                self._nonlocal_scope(target.id).bind(target.id, Binding("var", evidence=list(evidence)))
            else:
                self.scope.bind(target.id, Binding("var", evidence=list(evidence)))
        elif isinstance(target, (ast.Tuple, ast.List)):
            for elt in target.elts:
                self._bind_target(elt, [None])
        elif isinstance(target, ast.Starred):
            self._bind_target(target.value, [None])
        elif isinstance(target, ast.Attribute):
            self._record_self_attribute(target, evidence)

    def _record_self_attribute(self, target: ast.Attribute, evidence: list) -> None:
        """self.x = value inside a method: remember x's type on the class."""
        chain = dotted(target)
        if not chain or len(chain) != 2 or self.scope.kind != "method":
            return
        binding = self.scope.names.get(chain[0])
        if binding is None or binding.kind != "self":
            return
        cls = self.scopes[self.scope.parent]
        cls.attr_types.setdefault(chain[1], []).extend(evidence)

    def visit_Assign(self, node) -> None:
        self.visit(node.value)
        evidence = self._evidence(node.value)
        for target in node.targets:
            self._bind_target(target, evidence)
            self._visit_target_expressions(target)

    def visit_AnnAssign(self, node) -> None:
        if node.value is not None:
            self.visit(node.value)
        hint = annotation_type(node.annotation)
        evidence = [TypeRef("annotation", hint, self.scope.id) if hint else None]
        if self.scope.kind == "class" and isinstance(node.target, ast.Name):
            self.scope.attr_types.setdefault(node.target.id, []).extend(evidence)
        self._bind_target(node.target, evidence)
        self._visit_target_expressions(node.target)

    def visit_AugAssign(self, node) -> None:
        # x += y adds no evidence: for lists, strings and numbers the type is
        # unchanged, and a class whose __iadd__ returns another type is rare.
        self.visit(node.value)
        self._bind_target(node.target, [])
        self._visit_target_expressions(node.target)

    def _visit_target_expressions(self, target: ast.AST) -> None:
        """Calls can hide inside a target, e.g. d[key()] = 1."""
        if isinstance(target, (ast.Subscript, ast.Attribute)):
            self.generic_visit(target)

    def visit_NamedExpr(self, node) -> None:
        self.visit(node.value)
        self._bind_target(node.target, self._evidence(node.value))

    def visit_For(self, node) -> None:
        self._bind_target(node.target, [None])
        self.generic_visit(node)

    visit_AsyncFor = visit_For

    def visit_withitem(self, node) -> None:
        if node.optional_vars is not None:
            self._bind_target(node.optional_vars, [None])
        self.generic_visit(node)

    def visit_ExceptHandler(self, node) -> None:
        if node.name:
            self.scope.bind(node.name, Binding("var", evidence=[None]))
        self.generic_visit(node)

    def visit_comprehension(self, node) -> None:
        # Comprehension variables live in their own scope at runtime; binding
        # them here as untyped values keeps them from being mistaken for
        # module-level names of the same spelling.
        self._bind_target(node.target, [None])
        self.generic_visit(node)

    def visit_MatchAs(self, node) -> None:
        # case Point() as p / case [x, *rest] / case {"k": v, **extra}
        if node.name:
            self.scope.bind(node.name, Binding("var", evidence=[None]))
        self.generic_visit(node)

    def visit_MatchStar(self, node) -> None:
        if node.name:
            self.scope.bind(node.name, Binding("var", evidence=[None]))

    def visit_MatchMapping(self, node) -> None:
        if node.rest:
            self.scope.bind(node.rest, Binding("var", evidence=[None]))
        self.generic_visit(node)

    def visit_Lambda(self, node) -> None:
        for arg in node.args.posonlyargs + node.args.args + node.args.kwonlyargs:
            self.scope.bind(arg.arg, Binding("var", evidence=[None]))
        self.generic_visit(node)

    def visit_Global(self, node) -> None:
        self.scope.globals.update(node.names)

    def visit_Nonlocal(self, node) -> None:
        self.scope.nonlocals.update(node.names)

    def _nonlocal_scope(self, name: str) -> Scope:
        """The nearest enclosing function that binds name (class bodies do not count)."""
        enclosing = [s for s in self.stack[:-1] if s.kind in ("function", "method")]
        for scope in reversed(enclosing):
            if name in scope.names:
                return scope
        return enclosing[-1] if enclosing else self.stack[0]

    # -- calls -------------------------------------------------------------

    def visit_Call(self, node) -> None:
        func = node.func
        text = ast.unparse(func)[:80]
        if (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Call)
            and isinstance(func.value.func, ast.Name)
            and func.value.func.id == "super"
        ):
            self.scope.calls.append(RawCall([func.attr], text, node.lineno, special="super"))
        elif isinstance(func, ast.Attribute) and literal_type(func.value):
            # "-".join(parts), f"{x}".upper(): a method on a builtin literal.
            self.scope.calls.append(RawCall(None, text, node.lineno, special="builtin"))
        else:
            chain = dotted(func)
            self.scope.calls.append(RawCall(chain, text, node.lineno, special="" if chain else "computed"))
        self.generic_visit(node)


def collect_scopes(repo: Path, files: list[Path]) -> tuple[dict[str, Scope], list[str]]:
    """Parse the given .py files. Returns (scopes by id, unparsable files)."""
    scopes: dict[str, Scope] = {}
    errors: list[str] = []
    for path in files:
        rel = path.relative_to(repo).as_posix()
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, rel)
        except (SyntaxError, UnicodeDecodeError, ValueError):
            errors.append(rel)
            continue
        module, is_package = module_name(repo, path)
        builder = ScopeBuilder(module, rel, is_package, tree, source.count("\n") + 1)
        scopes.update(builder.scopes)
    return scopes, errors


def build_graph(repo: str | Path) -> Graph:
    """Index a repository from scratch. Most callers want cache.load_graph."""
    from .resolver import Resolver

    repo = Path(repo).resolve()
    files, skipped = walk_repo(repo)
    scopes, errors = collect_scopes(repo, files)
    graph = Resolver(scopes).run()
    graph.parse_errors = errors
    graph.skipped_dirs = skipped
    return graph
