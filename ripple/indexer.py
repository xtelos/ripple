"""Walk a repository and record what each scope defines, imports and calls.

This pass is purely syntactic: it reads one file at a time and writes down
facts ("module m binds name x to an import of y", "function f calls
self.repo.save on line 12"). It does not decide what a call points at. That
needs the whole repository in view, so it happens afterwards in resolver.py.

Keeping the two apart means each piece can be read, and tested, on its own.
"""

from __future__ import annotations

import ast
import gc
import io
import os
import re
import sys
import threading
import tokenize
import unicodedata
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
    "name" (the value was copied from another variable, named by expr),
    "builtin" (a literal; expr is the builtin type name, e.g. "list"),
    "item" (the value is TABLE[key] or TABLE.get(key): expr is the table, key is (key,)) or
    "unknown" (an assignment that proves nothing; expr says why, in words that
    finish "the receiver is ...", so the unresolved calls can be counted by cause).
    scope is the id of the scope the expression should be resolved in. For a "call", arg is
    the dotted first positional argument, if it is one (partial(f, 1) has arg "f").
    """

    kind: str
    expr: str
    scope: str
    arg: str = ""
    key: tuple | None = None


# Why a value's class is unknown, as the unresolved calls report it.
UNANNOTATED = "the receiver is an unannotated parameter"
ANNOTATION_NO_CLASS = "the receiver is annotated with something that does not name one class"
VARIADIC = "the receiver is *args or **kwargs"
LOOP_VARIABLE = "the receiver is a loop variable"
WITH_TARGET = "the receiver is bound by a with statement"
CAUGHT = "the receiver is a caught exception"
COMPREHENSION_VARIABLE = "the receiver is a comprehension variable"
UNPACKED = "the receiver is unpacked from a tuple, list or star target"
PATTERN = "the receiver is bound by a match pattern"
LAMBDA_PARAMETER = "the receiver is a lambda parameter"
ASSIGNED_UNTYPED = "the receiver is a local assigned from an expression ripple cannot type"
ASSIGNED_COMPUTED_CALL = "the receiver is assigned the result of a call whose callee is computed"
REBOUND = "the receiver is a name bound more than one way (an import, a def and an assignment, say)"


def unknown_ref(reason: str) -> TypeRef:
    return TypeRef("unknown", reason, "")


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
    (applying what a decorator factory returned). kind "decorator" and
    "annotation" say where the call is written; decorates is the id of the
    function a decorator call decorates.
    """

    chain: list | None
    text: str
    line: int
    kind: str = "call"
    special: str = ""
    # A callee that goes through the result of a call: ["make", "()", "run"] for make().run().
    # Names and attributes are as in chain, "()" is a call. chain is None when this is set.
    path: list | None = None
    # Why a computed callee (chain and path both None) is computed: one of the COMPUTED_* texts.
    why: str = ""
    decorates: str = ""
    # special "table": TABLE[key](...) or TABLE.get(key)(...) with a constant key (form "item", key is
    # (key,)), or a call of the variable of an enclosing `for x in TABLE` (form "iter"), `.values()`
    # ("values") or `.items()` ("items") loop (var is the variable). table is the dotted name of TABLE.
    table: list | None = None
    key: tuple | None = None
    form: str = ""
    var: str = ""


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
    return_annotated: bool = False  # written, though it may not name one class (list[int] | dict)
    returns_ambiguous: bool = False  # two defs of the name that return different classes
    overloaded: bool = False
    return_sigs: set = field(default_factory=set)  # the class each annotated def of the name returns
    plain_defs: int = 0  # defs of the name other than property setters and deleters
    bare_defs: int = 0  # of those, the ones with no return annotation (an @overload's implementation excepted)
    is_property: bool = False
    # functions only, for reading what a decorator does (see Resolver.decorator_shape)
    defs: int = 0  # how many def statements share this id (a redefinition folds into the first)
    params: list = field(default_factory=list)  # positional parameter names, in order
    rebound: set = field(default_factory=set)  # names bound again after their first binding (a parameter, say)
    return_names: list = field(default_factory=list)  # per return statement: the name returned, "" if not a bare name
    return_calls: list = field(default_factory=list)  # per return statement: the dotted callee if it returns a call, else None
    ends_with_return: bool = False  # the last statement is a return or a raise, so no path falls off the end
    is_generator: bool = False
    is_async: bool = False
    # classes only
    bases: list = field(default_factory=list)
    redirects_construction: bool = False  # metaclass=... (or **kwargs): calling the class may run other code
    decorators: list = field(default_factory=list)  # the decorator RawCalls written on it, top to bottom
    attr_types: dict = field(default_factory=dict)
    # modules only
    is_package: bool = False
    star_imports: list = field(default_factory=list)
    # Names this module sets or deletes as an attribute or an item of something (`mod.NAME = x`,
    # `d["NAME"] = x`), which can rebind a name another module defines. setattr and the like are not here:
    # they are store tokens, see STORE_TOKENS.
    rebinds: set = field(default_factory=set)
    tables: dict = field(default_factory=dict)  # NAME = {...} / [...] / (...): Table
    aliases: list = field(default_factory=list)  # what it imports under a new name, as (name, asname)

    def bind(self, name: str, binding: Binding) -> None:
        """Record a binding. A name bound two different ways becomes unknown."""
        old = self.names.get(name)
        if old is not None:
            self.rebound.add(name)
        if old is None:
            self.names[name] = binding
        elif old.kind == "var" and binding.kind == "var":
            old.evidence.extend(binding.evidence)
        elif (old.kind, old.target, old.name) != (binding.kind, binding.target, binding.name):
            self.names[name] = Binding("var", evidence=[unknown_ref(REBOUND)])


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
            if d in SKIP_ANYWHERE or (here == repo and d in SKIP_AT_ROOT) or _holds_venv_or_is_locked(here / d):
                skipped.append((here / d).relative_to(repo).as_posix())
            else:
                keep.append(d)
        dirnames[:] = keep
        # isfile follows symlinks, so a dangling or looping link, a named pipe
        # (reading one blocks forever) and a directory called x.py are all left out.
        files.extend(here / f for f in sorted(filenames) if f.endswith(".py") and _may_be_file(here / f))
    return files, sorted(skipped)


def _holds_venv_or_is_locked(directory: Path) -> bool:
    """Whether the directory is a virtualenv, or one that cannot be entered (listable, not searchable)."""
    try:
        return (directory / "pyvenv.cfg").exists()
    except OSError:
        return True


def _may_be_file(path: Path) -> bool:
    """False for what is certainly not a file we can read. A path whose type cannot be
    determined counts as a file, so that reading it fails and is reported with the unparsable ones."""
    try:
        return path.is_file()
    except OSError:
        return True


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


COMPUTED_SUBSCRIPT = "the callee is looked up by subscript (a dict, list or registry)"
COMPUTED_OTHER = "the callee is computed at runtime (a lambda, a conditional expression or similar)"


def call_path(node: ast.AST) -> list | None:
    """["make", "()", "run"] for make().run, None unless the expression is a name followed by
    attributes and calls only, with at least one call."""
    parts = []
    while True:
        if isinstance(node, ast.Attribute):
            parts.append(node.attr)
            node = node.value
        elif isinstance(node, ast.Call):
            parts.append("()")
            node = node.func
        elif isinstance(node, ast.Name):
            parts.append(node.id)
            break
        else:
            return None
    parts.reverse()
    return parts if "()" in parts else None


@dataclass
class Table:
    """A module-level dict, list or tuple literal: what it holds, as dotted names.

    kind "dict": entries maps each constant key to the dotted name written as the value
    (a list of parts), or None when the value is anything else. kind "seq": entries is
    the list of dotted names, None where an element is anything else.
    """

    kind: str
    entries: object


def const_key(node: ast.AST):
    """(key,) for a constant str, bytes or int subscript or dict key, else None."""
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        inner = const_key(node.operand)
        return (-inner[0],) if inner and type(inner[0]) is int else None
    if isinstance(node, ast.Constant) and type(node.value) in (str, bytes, int):
        return (node.value,)
    return None


def table_of(value: ast.AST) -> Table | None:
    """The Table a literal is, or None: not a dict, list or tuple, or one with **unpacking,
    *star elements, a key that is not a plain constant, or the same key twice."""
    if isinstance(value, ast.Dict):
        entries = {}
        for key_node, item in zip(value.keys, value.values):
            key = const_key(key_node) if key_node is not None else None
            if key is None or key[0] in entries:
                return None
            entries[key[0]] = dotted(item)
        return Table("dict", entries)
    if isinstance(value, (ast.List, ast.Tuple)):
        if any(isinstance(item, ast.Starred) for item in value.elts):
            return None
        return Table("seq", [dotted(item) for item in value.elts])
    return None


def table_get(node: ast.AST):
    """(table chain, (key,)) for TABLE[key] or TABLE.get(key) with a constant key, else None."""
    if isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Load):
        chain, key = dotted(node.value), const_key(node.slice)
    elif (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "get"
        and len(node.args) == 1
        and not node.keywords
    ):
        chain, key = dotted(node.func.value), const_key(node.args[0])
    else:
        return None
    return (chain, key) if chain and key else None


# Methods that read a dict or list without changing it. Anything else called on a table
# (update, append, pop, setdefault, sort, ...) may change it.
READ_METHODS = {"get", "items", "values", "keys", "copy", "count", "index"}
STRING_LOOKUPS = {"getattr", "setattr", "delattr", "hasattr"}


def _reads(node: ast.AST) -> list:
    """Ids of the nodes a for-loop or comprehension reads from: `for x in T`, `for x in T.values()`."""
    if isinstance(node, (ast.Name, ast.Attribute)):
        return [id(node)]
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in ("values", "items", "keys")
        and not node.args
        and not node.keywords
    ):
        return [id(node.func.value)]
    return []


@dataclass
class Mentions:
    """How a module could change a table unseen: what the resolver asks of it when a table needs to be
    proven, and not before (see Rescanner).

    risky: each name (or attribute) the module mentions other than to read a table, with the line of
    the first such mention. A name (or attribute) that is only subscripted for reading, iterated, read
    with get/items/values/..., or tested with `in` cannot change what it names. Every other mention
    (assigned, deleted, passed on, aliased, returned, called with append or update, declared global)
    is risky, so a table whose name is in no module's risky set (following `from m import T as U`)
    is not changed by any code in the repo. Names are matched as text on purpose: that holds through
    any alias, import form or module.
    readable is False when the module could not be read again.
    """

    risky: dict = field(default_factory=dict)
    readable: bool = True


def scan_module(tree: ast.AST) -> Mentions:
    """The Mentions of one parsed module.

    One pass: ast.walk visits a parent before its children, so a parent can clear its children.
    """
    found = Mentions()
    ok: set = set()
    risky = found.risky
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            if id(node) not in ok:
                risky.setdefault(node.id, node.lineno)
        elif isinstance(node, ast.Attribute):
            if id(node) not in ok:
                risky.setdefault(node.attr, node.lineno)
        elif isinstance(node, ast.Subscript):
            if isinstance(node.ctx, ast.Load):
                ok.add(id(node.value))
        elif isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
            ok.update(_reads(node.iter))
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr in READ_METHODS:
                ok.add(id(func.value))
            if isinstance(func, ast.Name) and func.id in STRING_LOOKUPS:
                for arg in node.args[1:2]:
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        risky.setdefault(arg.value, node.lineno)
        elif isinstance(node, ast.Compare):
            if any(isinstance(op, (ast.In, ast.NotIn)) for op in node.ops):
                ok.update(id(c) for c in node.comparators)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            if node.value is not None and table_of(node.value):
                for target in node.targets if isinstance(node, ast.Assign) else [node.target]:
                    ok.add(id(target))  # the definition itself
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            for name in node.names:
                risky.setdefault(name, node.lineno)
    return found


# A table or a partial is followed only when no file of the repo has any of these words. They are how code
# stores a value under a name in a module without writing `module.NAME = value` (which Scope.rebinds sees):
# setattr and its dunders, the module's dict and the frames' (vars, globals, locals, __dict__, __globals__,
# f_globals, f_locals), running or importing code by name (exec, eval, __import__, importlib), mock.patch
# and pytest's monkeypatch, and the builtins (builtins, __builtins__, which hold every one of these under
# another name). sys.modules is the same kind of word and is matched on its own (see _SYS_MODULES).
# Any spelling counts, because this is a match of the word, not of the shape of a call: an alias, an
# attribute of self, a continuation line, a string, a comment. A file with the word that cannot touch a
# given module is not excepted either: another module can hand it that module.
STORE_TOKENS = (
    "setattr", "delattr", "__setattr__", "__delattr__", "__dict__", "vars", "globals", "locals", "exec", "eval",
    "__import__", "importlib", "patch", "monkeypatch", "__builtins__", "builtins", "__globals__", "f_globals", "f_locals",
)  # fmt: skip
_SYS_MODULES = (
    r"\.[\s\\]*modules\b"  # sys.modules, s.modules, importlib.sys.modules
    r"|[\"']modules[\"']"  # getattr(sys, "modules")
    r"|\bfrom\s+sys\s+import\s*\*"
    r"|\bfrom\s+sys\s+import\b(?:[^\n\\()#]|\\\r?\n)*\bmodules\b"  # from sys import argv, modules as m
    r"|\bfrom\s+sys\s+import\s*\([^)]*\bmodules\b"
)
_STORE_TOKEN = re.compile(r"\b(?P<word>" + "|".join(STORE_TOKENS) + r")\b|(?P<modules>" + _SYS_MODULES + ")")


def find_store_token(data: bytes) -> tuple[str, int] | None:
    """The first store token in a module's text (see STORE_TOKENS) as (what it is, its line), or None.

    The text is what Python reads, not the bytes: the module's own encoding (a BOM, a coding cookie,
    utf-7), and the letters an identifier is normalized to (`ｓｅｔａｔｔｒ` is `setattr`). A file that
    cannot be decoded has no text to look in and counts as having one.
    """
    if data.isascii() and b"coding" not in data:
        views = [data.decode("ascii")]
    else:
        try:
            text = data.decode(tokenize.detect_encoding(io.BytesIO(data).readline)[0])
        except (SyntaxError, LookupError, UnicodeDecodeError, ValueError):
            return "text ripple cannot decode", 1
        views = [text]
        if not text.isascii():
            views.append(unicodedata.normalize("NFKC", text))
        if b"coding" in data:
            views.append(data.decode("latin-1"))  # what the bytes say, whatever the cookie claims
    for view in views:
        found = _STORE_TOKEN.search(view)
        if found:
            return repr(found.group("word")) if found.group("word") else "sys.modules", view.count("\n", 0, found.start()) + 1
    return None


class Rescanner:
    """Reads what the resolver asks of a module's file, once, the first time it asks.

    Only a table or a functools.partial that the resolver has to prove needs this, and most
    repositories never ask, so the indexing pass does not pay for it.
    """

    def __init__(self, repo: Path):
        self.repo = repo
        self.cache: dict[str, Mentions] = {}
        self.token: tuple[str, str, int] | None | bool = False  # False: not asked yet

    def store_token(self, files: list[str]) -> tuple[str, str, int] | None:
        """(what, file, line) of the first store token in any of the files, or None. An unreadable file counts."""
        if self.token is False:
            self.token = None
            for file in files:
                try:
                    found = find_store_token((self.repo / file).read_bytes())
                except OSError:
                    found = ("a file that could not be read again", 1)
                if found:
                    self.token = (found[0], file, found[1])
                    break
        return self.token

    def __call__(self, module: Scope) -> Mentions:
        found = self.cache.get(module.id)
        if found is None:
            try:
                found = scan_module(ast.parse((self.repo / module.file).read_bytes(), module.file))
            except (SyntaxError, UnicodeDecodeError, ValueError, OSError, RecursionError, MemoryError):
                found = Mentions(readable=False)
            self.cache[module.id] = found
        return found


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
        self.in_annotation = 0
        self.loops: list = []  # (scope id, variable, table, form) of the `for x in TABLE` loops whose body is being visited
        self.delayed = 0  # inside a lambda or generator expression, which run after the loop moved on
        for stmt in tree.body:
            self.visit(stmt)

    @property
    def scope(self) -> Scope:
        return self.stack[-1]

    # -- the walk itself ---------------------------------------------------
    #
    # ast.NodeVisitor looks the method up by name for every node and walks the fields through a
    # generator. This is the same walk with the method found once per node class and the fields
    # read directly, and without a visit of the leaves nothing here reads (names, constants, and
    # the Load/Store/Del markers).

    _methods: dict = {}

    def visit(self, node):
        cls = node.__class__
        method = self._methods.get(cls)
        if method is None:
            method = getattr(self.__class__, "visit_" + cls.__name__, self.__class__.generic_visit)
            self._methods[cls] = method
        return method(self, node)

    def generic_visit(self, node) -> None:
        for name in node._fields:
            value = getattr(node, name, None)
            if isinstance(value, list):
                for item in value:
                    if isinstance(item, ast.AST):
                        self.visit(item)
            elif isinstance(value, ast.AST):
                self.visit(value)

    def visit_leaf(self, node) -> None:
        """A node with no children that matter: a name, a constant, a Load/Store/Del marker."""

    visit_Name = visit_Constant = visit_Load = visit_Store = visit_Del = visit_leaf

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
        self._binding_scope(node.name).bind(node.name, Binding("def", scope.id))
        return scope

    def _binding_scope(self, name: str) -> Scope:
        """The scope a statement that binds name binds it in: the module for a name declared
        `global`, the enclosing function for one declared `nonlocal`, else the current scope."""
        scope = self.scope
        if name in scope.globals:
            return self.stack[0]
        if name in scope.nonlocals:
            return self._nonlocal_scope(name)
        return scope

    def _decorators(self, node) -> list:
        """A decorator is called by the enclosing scope when the def runs. Returns the calls recorded."""
        recorded = []
        # Only a function can be read as wrapped (the resolver follows what the stack returns).
        target = f"{self.scope.id}.{node.name}" if not isinstance(node, ast.ClassDef) else ""
        for dec in node.decorator_list:
            self.visit(dec)  # records calls inside it, e.g. route("/x")
            chain = dotted(dec) or []
            text = ".".join(chain)[:80] if chain else ast.unparse(dec)[:80]  # a name or a.b.c unparses to itself
            if len(chain) == 2 and chain[1] in ("setter", "getter", "deleter"):
                # @value.setter is a method of the builtin property object.
                call = RawCall(None, text, dec.lineno, "decorator", "builtin", decorates=target)
            elif isinstance(dec, ast.Call):
                call = RawCall(dotted(dec.func), text, dec.lineno, "decorator", "decorator_result", decorates=target)
            else:
                call = RawCall(dotted(dec), text, dec.lineno, "decorator", decorates=target)
            self.scope.calls.append(call)
            recorded.append(call)
        return recorded

    def visit_FunctionDef(self, node) -> None:
        self._decorators(node)
        for default in node.args.defaults + [d for d in node.args.kw_defaults if d is not None]:
            self.visit(default)
        args = node.args
        for arg in args.posonlyargs + args.args + args.kwonlyargs + [a for a in (args.vararg, args.kwarg) if a]:
            self._visit_annotation(arg.annotation)
        self._visit_annotation(node.returns)
        is_method = self.scope.kind == "class"
        scope = self._enter(node, "method" if is_method else "function")
        decorators = {(dotted(d) or [""])[-1] for d in node.decorator_list}
        # "or": a property setter reuses the getter's scope and must not reset these.
        hint = annotation_type(node.returns)
        accessor = any(
            len(chain) == 2 and chain[1] in ("setter", "getter", "deleter")
            for chain in (dotted(d) or [] for d in node.decorator_list)
        )
        if "overload" in decorators:
            scope.overloaded = True
        if node.returns is not None and not accessor:
            scope.return_sigs.add(hint)
        # Defs of one name that return different classes (@overload, a def in each branch of an if):
        # which one runs depends on the arguments or the platform. A def with no annotation among
        # several, in whatever order, may be the one that runs, except after @overload defs, where it is
        # just their implementation.
        if not accessor:
            scope.plain_defs += 1
            if node.returns is None and not scope.overloaded:
                scope.bare_defs += 1
        if len(scope.return_sigs) > 1 or (scope.plain_defs > 1 and scope.bare_defs):
            scope.returns_ambiguous = True
        scope.returns = scope.returns or hint
        scope.return_annotated = scope.return_annotated or node.returns is not None
        scope.is_property = scope.is_property or bool(decorators & {"property", "cached_property"})
        params = node.args.posonlyargs + node.args.args
        scope.defs += 1
        if scope.defs == 1:
            scope.params = [p.arg for p in params]
            scope.is_async = isinstance(node, ast.AsyncFunctionDef)
            scope.ends_with_return = bool(node.body) and isinstance(node.body[-1], (ast.Return, ast.Raise))
        static = "staticmethod" in decorators
        for i, arg in enumerate(params + node.args.kwonlyargs):
            if i == 0 and is_method and not static and params:
                scope.bind(arg.arg, Binding("self"))
                continue
            hint = annotation_type(arg.annotation)
            # Annotations are evaluated where the def runs (for a method, the
            # class body), so they are resolved there, not inside the function.
            if hint:
                evidence = [TypeRef("annotation", hint, self.scope.id)]
            else:
                evidence = [unknown_ref(UNANNOTATED if arg.annotation is None else ANNOTATION_NO_CLASS)]
            scope.bind(arg.arg, Binding("var", evidence=evidence))
        for extra in (node.args.vararg, node.args.kwarg):
            if extra is not None:
                scope.bind(extra.arg, Binding("var", evidence=[unknown_ref(VARIADIC)]))
        self.stack.append(scope)
        for stmt in node.body:
            self.visit(stmt)
        self.stack.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Return(self, node) -> None:
        if self.scope.kind in ("function", "method"):
            self.scope.return_names.append(node.value.id if isinstance(node.value, ast.Name) else "")
            self.scope.return_calls.append(dotted(node.value.func) if isinstance(node.value, ast.Call) else None)
        self.generic_visit(node)

    def visit_Yield(self, node) -> None:
        self.scope.is_generator = True
        self.generic_visit(node)

    visit_YieldFrom = visit_Yield

    def visit_ClassDef(self, node) -> None:
        decorators = self._decorators(node)
        for base in node.bases + [k.value for k in node.keywords]:
            self.visit(base)
        scope = self._enter(node, "class")
        scope.decorators.extend(decorators)
        scope.bases = [base_name(b) for b in node.bases]
        scope.redirects_construction = any(k.arg in ("metaclass", None) for k in node.keywords)
        self.stack.append(scope)
        for stmt in node.body:
            self.visit(stmt)
        self.stack.pop()

    # -- imports -----------------------------------------------------------

    def visit_Import(self, node) -> None:
        for alias in node.names:
            if alias.asname:
                self._binding_scope(alias.asname).bind(alias.asname, Binding("import", alias.name))
            else:
                head = alias.name.split(".")[0]
                self._binding_scope(head).bind(head, Binding("import", head))

    def visit_ImportFrom(self, node) -> None:
        base = self._absolute(node.module, node.level)
        for alias in node.names:
            if alias.name == "*":
                self.stack[0].star_imports.append(base)
            else:
                bound = alias.asname or alias.name
                self._binding_scope(bound).bind(bound, Binding("from", base, alias.name))
                if alias.asname and alias.asname != alias.name:
                    self.stack[0].aliases.append((alias.name, alias.asname))

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
            if not chain:
                return [unknown_ref(ASSIGNED_COMPUTED_CALL)]
            first = dotted(value.args[0]) if value.args and not isinstance(value.args[0], ast.Starred) else None
            return [TypeRef("call", ".".join(chain), here, arg=".".join(first) if first else "")]
        if isinstance(value, ast.Name):
            return [TypeRef("name", value.id, here)]
        got = table_get(value)
        if got:
            return [TypeRef("item", ".".join(got[0]), here, key=got[1])]
        if isinstance(value, ast.BoolOp) and isinstance(value.op, ast.Or):
            refs = []
            for operand in value.values:
                if not _is_none(operand):
                    refs.extend(self._evidence(operand))
            return refs or [unknown_ref(ASSIGNED_UNTYPED)]
        return [unknown_ref(ASSIGNED_UNTYPED)]

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
            # Each element keeps the reason it was given (a loop variable stays one), else it is unpacked.
            inner = evidence if evidence and all(ref.kind == "unknown" for ref in evidence) else [unknown_ref(UNPACKED)]
            for elt in target.elts:
                self._bind_target(elt, inner)
        elif isinstance(target, ast.Starred):
            self._bind_target(target.value, [unknown_ref(UNPACKED)])
        elif isinstance(target, ast.Attribute):
            self.stack[0].rebinds.add(target.attr)
            self._record_self_attribute(target, evidence)
        elif isinstance(target, ast.Subscript):
            self._note_item_store(target)

    def _note_item_store(self, target: ast.Subscript) -> None:
        """`x["NAME"] = v` or `del x["NAME"]` can set a module's NAME (x may be globals() or its __dict__)."""
        if isinstance(target.slice, ast.Constant) and isinstance(target.slice.value, str):
            self.stack[0].rebinds.add(target.slice.value)

    def _note_deleted(self, target: ast.AST) -> None:
        if isinstance(target, (ast.Tuple, ast.List)):
            for elt in target.elts:
                self._note_deleted(elt)
        elif isinstance(target, ast.Attribute):
            self.stack[0].rebinds.add(target.attr)
        elif isinstance(target, ast.Subscript):
            self._note_item_store(target)

    def visit_Delete(self, node) -> None:
        for target in node.targets:
            self._note_deleted(target)
        self.generic_visit(node)

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

    def _note_table(self, target: ast.AST, value: ast.AST | None) -> None:
        if self.scope.kind == "module" and isinstance(target, ast.Name) and value is not None:
            table = table_of(value)
            if table:
                self.scope.tables[target.id] = table

    def visit_Assign(self, node) -> None:
        self.visit(node.value)
        if len(node.targets) == 1:
            self._note_table(node.targets[0], node.value)
        evidence = self._evidence(node.value)
        for target in node.targets:
            self._bind_target(target, evidence)
            self._visit_target_expressions(target)

    def visit_AnnAssign(self, node) -> None:
        if node.value is not None:
            self.visit(node.value)
        if self.scope.kind in ("module", "class"):  # a local variable's annotation is never evaluated
            self._visit_annotation(node.annotation)
        hint = annotation_type(node.annotation)
        evidence = [TypeRef("annotation", hint, self.scope.id) if hint else unknown_ref(ANNOTATION_NO_CLASS)]
        self._note_table(node.target, node.value)
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
        self._bind_target(node.target, [unknown_ref(LOOP_VARIABLE)])
        loop = self._table_loop(node)
        self.visit(node.target)
        self.visit(node.iter)
        if loop:
            self.loops.append(loop)
        for stmt in node.body:
            self.visit(stmt)
        if loop:
            self.loops.pop()
        for stmt in node.orelse:
            self.visit(stmt)  # runs after the loop: the variable is the last element, not each one

    def _table_loop(self, node) -> tuple | None:
        """(scope id, variable, table, form) when this loop reads a table whole: `for f in T`,
        `for f in T.values()`, `for k, f in T.items()`."""
        source = node.iter
        form, target = "iter", node.target
        if isinstance(source, ast.Call) and isinstance(source.func, ast.Attribute) and not source.args and not source.keywords:
            if source.func.attr == "values":
                form, source = "values", source.func.value
            elif source.func.attr == "items":
                form, source = "items", source.func.value
                if isinstance(target, ast.Tuple) and len(target.elts) == 2 and all(isinstance(e, ast.Name) for e in target.elts):
                    target = target.elts[1]
        table = dotted(source)
        if not table or not isinstance(target, ast.Name):
            return None
        return (self.scope.id, target.id, table, form)

    visit_AsyncFor = visit_For

    def visit_withitem(self, node) -> None:
        if node.optional_vars is not None:
            self._bind_target(node.optional_vars, [unknown_ref(WITH_TARGET)])
        self.generic_visit(node)

    def visit_ExceptHandler(self, node) -> None:
        if node.name:
            self._binding_scope(node.name).bind(node.name, Binding("var", evidence=[unknown_ref(CAUGHT)]))
        self.generic_visit(node)

    def visit_comprehension(self, node) -> None:
        # Comprehension variables live in their own scope at runtime; binding
        # them here as untyped values keeps them from being mistaken for
        # module-level names of the same spelling.
        self._bind_target(node.target, [unknown_ref(COMPREHENSION_VARIABLE)])
        self.generic_visit(node)

    def visit_MatchAs(self, node) -> None:
        # case Point() as p / case [x, *rest] / case {"k": v, **extra}
        if node.name:
            self._binding_scope(node.name).bind(node.name, Binding("var", evidence=[unknown_ref(PATTERN)]))
        self.generic_visit(node)

    def visit_MatchStar(self, node) -> None:
        if node.name:
            self._binding_scope(node.name).bind(node.name, Binding("var", evidence=[unknown_ref(PATTERN)]))

    def visit_MatchMapping(self, node) -> None:
        if node.rest:
            self._binding_scope(node.rest).bind(node.rest, Binding("var", evidence=[unknown_ref(PATTERN)]))
        self.generic_visit(node)

    def visit_Lambda(self, node) -> None:
        for arg in node.args.posonlyargs + node.args.args + node.args.kwonlyargs:
            self.scope.bind(arg.arg, Binding("var", evidence=[unknown_ref(LAMBDA_PARAMETER)]))
        self.delayed += 1
        self.generic_visit(node)
        self.delayed -= 1

    def visit_GeneratorExp(self, node) -> None:
        self.delayed += 1
        self.generic_visit(node)
        self.delayed -= 1

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
        chain = dotted(func)
        text = ".".join(chain)[:80] if chain else ast.unparse(func)[:80]  # a name or a.b.c unparses to itself
        kind = "annotation" if self.in_annotation else "call"
        if (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Call)
            and isinstance(func.value.func, ast.Name)
            and func.value.func.id == "super"
        ):
            self.scope.calls.append(RawCall([func.attr], text, node.lineno, kind, special="super"))
        elif isinstance(func, ast.Attribute) and literal_type(func.value):
            # "-".join(parts), f"{x}".upper(): a method on a builtin literal.
            self.scope.calls.append(RawCall(None, text, node.lineno, kind, special="builtin"))
        else:
            path = None if chain else call_path(func)
            got = table_get(func)
            loop = next((lp for lp in reversed(self.loops) if lp[0] == self.scope.id and chain == [lp[1]]), None)
            if got:
                # TABLE["k"](...) and TABLE.get("k")(...): a table's entry, if the resolver can prove the table;
                # otherwise the call is what it was before (a computed callee, or the result of a call)
                why = COMPUTED_SUBSCRIPT if isinstance(func, ast.Subscript) else COMPUTED_OTHER
                call = RawCall(None, text, node.lineno, kind, special="table", path=path, why=why)
                call.table, call.key, call.form = got[0], got[1], "item"
                self.scope.calls.append(call)
            elif loop and not self.delayed:
                call = RawCall(chain, text, node.lineno, kind, special="table")
                call.table, call.form, call.var = loop[2], loop[3], loop[1]
                self.scope.calls.append(call)
            elif chain:
                self.scope.calls.append(RawCall(chain, text, node.lineno, kind))
            elif path:
                self.scope.calls.append(RawCall(None, text, node.lineno, kind, path=path))
            else:
                why = COMPUTED_SUBSCRIPT if isinstance(func, ast.Subscript) else COMPUTED_OTHER
                self.scope.calls.append(RawCall(None, text, node.lineno, kind, special="computed", why=why))
        self.generic_visit(node)

    def _visit_annotation(self, node: ast.AST | None) -> None:
        """Record the calls inside an annotation (Annotated[int, Field(gt=0)]) as calls of this scope.

        Python evaluates annotations where the def or class statement runs, so the scope that
        holds the definition is the caller. A string annotation is not parsed, and with
        `from __future__ import annotations` the expression runs later, when something
        (pydantic, typing.get_type_hints) evaluates it; the call is still written in this scope.
        """
        if node is not None:
            self.in_annotation += 1
            self.visit(node)
            self.in_annotation -= 1


def collect_scopes(repo: Path, files: list[Path]) -> tuple[dict[str, Scope], list[str]]:
    """Parse the given .py files. Returns (scopes by id, unparsable files)."""
    scopes: dict[str, Scope] = {}
    errors: list[str] = []
    for path in files:
        rel = path.relative_to(repo).as_posix()
        try:
            # Bytes, not text: the parser then applies PEP 263 itself (a BOM, a
            # "coding: latin-1" cookie) the way the interpreter would.
            source = path.read_bytes()
            tree = ast.parse(source, rel)
            module, is_package = module_name(repo, path)
            builder = ScopeBuilder(module, rel, is_package, tree, source.count(b"\n") + 1)
        except (SyntaxError, UnicodeDecodeError, ValueError, OSError, RecursionError, MemoryError):
            # Nested deeper than even build_graph's raised limit allows: only this file is lost.
            errors.append(rel)
            continue
        scopes.update(builder.scopes)
    return scopes, errors


# Generated code (one elif per enum member, a table written as a + b + c ..., a
# long builder chain) nests deeper than the 1000 frames Python allows by default,
# and every recursive pass over it (the visitor here, the resolver's type
# lookups) would crash. build_graph runs on a thread with a large stack and a
# higher limit instead; the limit is put back afterwards.
#
# The limit is process-wide while only that thread has the big stack. Before 3.12 a
# Python call uses the C stack, so another thread that recursed without bound would
# overflow its small stack and kill the process instead of raising RecursionError.
# There the limit is left alone, and code nested deeper than it costs its file.
DEEP_RECURSION_LIMIT = 100_000
DEEP_STACK_BYTES = 512 * 1024 * 1024
RAISE_LIMIT = sys.version_info >= (3, 12)
_deep_lock = threading.Lock()


def run_deep(function, *args):
    """Call function(*args) with room for very deep recursion, and return its result."""
    outcome: dict = {}

    def target() -> None:
        old_limit = sys.getrecursionlimit()
        try:
            if RAISE_LIMIT:  # only from here, so the window is the work itself
                sys.setrecursionlimit(max(old_limit, DEEP_RECURSION_LIMIT))
            outcome["value"] = function(*args)
        except BaseException as error:  # handed back to the caller's thread
            outcome["error"] = error
        finally:
            sys.setrecursionlimit(old_limit)

    with _deep_lock:  # the recursion limit and thread stack size are process-wide
        old_stack = threading.stack_size()
        try:
            threading.stack_size(DEEP_STACK_BYTES)
            worker = threading.Thread(target=target, daemon=True)
            worker.start()
        except (ValueError, RuntimeError):  # no such stack here: run with the default limits
            worker = None
        finally:
            threading.stack_size(old_stack)
        if worker is None:
            return function(*args)
        worker.join()
    if "error" in outcome:
        raise outcome["error"]
    return outcome["value"]


def build_graph(repo: str | Path) -> Graph:
    """Index a repository from scratch. Most callers want cache.load_graph."""
    # The index is a large graph of objects that all stay alive until the end, so the cyclic
    # collector, which rescans every live object each time it runs, finds almost nothing and
    # costs a share of the build that grows with the repo (django: about 40% of a cold index).
    # It stays off while the graph is built, and goes back on if it was on.
    was_enabled = gc.isenabled()
    gc.disable()
    try:
        return run_deep(_build_graph, Path(repo))
    finally:
        if was_enabled:
            gc.enable()


def _build_graph(repo: Path) -> Graph:
    from .resolver import Resolver

    repo = Path(repo).resolve()
    files, skipped = walk_repo(repo)
    scopes, errors = collect_scopes(repo, files)
    graph = Resolver(scopes, Rescanner(repo), errors).run()
    graph.parse_errors = errors
    graph.skipped_dirs = skipped
    unparsed = set(errors)
    for path in files:
        rel = path.relative_to(repo).as_posix()
        if rel not in unparsed:
            graph.modules[rel] = module_name(repo, path)[0]
    return graph
