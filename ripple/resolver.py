"""Decide what each recorded call site points at, or say that we cannot.

The rule throughout: a call becomes an edge only when the source code proves
its target. When it does not (a value of unknown type, a dict lookup, a
getattr), the call is kept as Unresolved with a reason. Guessing, for example
"there is only one method called save in the repo, so it must be that one",
would make the graph look more complete and make impact answers wrong in
exactly the cases an agent cannot check.

What counts as proof:
  * a name defined in, or imported into, the calling scope
  * module.attr chains through import, from-import and aliases, including
    re-exports from a package __init__
  * self.method() and cls.method(), looked up through the class's bases
  * super().method()
  * calling a class, which runs its __init__
  * a method called on a value whose class is known from a constructor call,
    a type annotation, or a self.attr assigned in the class
When a method is called on self or on a typed value, subclasses that
override it are also linked (kind "override"): the runtime object may be one
of them. This is class hierarchy analysis, a standard static technique.
"""

from __future__ import annotations

import builtins
import importlib
import sys
import warnings
from collections import Counter
from dataclasses import dataclass, replace

from .indexer import Binding, RawCall, Scope, TypeRef
from .model import Edge, Graph, Symbol, Unresolved

BUILTIN_NAMES = set(dir(builtins))
# Values of these types run only builtin methods: "x".strip(), {}.get(...).
BUILTIN_TYPES = {f"builtins.{n}" for n in BUILTIN_NAMES if isinstance(getattr(builtins, n), type)}
MAX_IMPORT_HOPS = 8

# A type stored as "external:threading.Event" is a class that lives outside the
# repo: the standard library, or a dependency the repo subclasses.
EXTERNAL = "external:"

# Why a call counts as reaching code outside the repo (Graph.external_reasons).
EXT_BUILTIN = "a builtin function or type"
EXT_IMPORT = "a name imported from outside the repo"
EXT_BUILTIN_VALUE = "a method of a builtin value (str, list, dict, ...)"
EXT_INHERITED = "a method inherited from a base class outside the repo"
EXT_SUPER = "super() into a base class outside the repo"
EXT_INSTANCE = "a method of an instance of a class outside the repo"

# Standard library modules ripple may import to ask whether a name is a class.
# Importing runs the module, so this is a list of ones that are safe and quiet
# to import (not `this`, `antigravity`, `idlelib`, `tkinter`), never "any stdlib name".
SAFE_STDLIB_MODULES = frozenset(
    {
        "argparse", "array", "bisect", "calendar", "collections", "concurrent.futures", "configparser",
        "contextlib", "copy", "csv", "datetime", "decimal", "difflib", "email.message", "enum", "fractions",
        "functools", "gzip", "hashlib", "heapq", "hmac", "html.parser", "http.client", "http.cookies",
        "io", "ipaddress", "itertools", "json", "logging", "logging.handlers", "pathlib", "pprint", "queue",
        "random", "re", "sched", "secrets", "shlex", "shutil", "socket", "sqlite3", "statistics", "string",
        "struct", "subprocess", "tarfile", "tempfile", "textwrap", "threading", "time", "types", "urllib.parse",
        "uuid", "weakref", "xml.etree.ElementTree", "zipfile",
    }
)  # fmt: skip
# Names that may be satisfied by a class that never inherits from them (protocols, ABCs).
NOMINAL_ONLY_EXCLUDED = ("typing.", "typing_extensions.", "collections.abc.", "abc.")


@dataclass(frozen=True)
class Target:
    """What an expression refers to, as far as static analysis can tell.

    kind is one of:
      symbol    a function, method or class in the repo (value is its id)
      module    a module in the repo (value is its dotted name)
      instance  a value whose class is known (value is the class id)
      foreign   a value whose class is known and lives outside the repo (value
                is its dotted name, e.g. threading.Event)
      bound     a method looked up on an instance (value is the method id,
                receiver is the instance's class, used to find overrides)
    exact marks a variable whose every assignment is a constructor call of one
    class (field = Field()), not merely a class the value could be (a parameter
    annotated Field, self, a self.attr): it cannot be a subclass, so no overrides.
      external  something outside the repo: stdlib, a dependency, a builtin
                (reason says which kind, for counting)
      unknown   cannot be determined statically (reason says why)
    """

    kind: str
    value: str = ""
    receiver: str = ""
    reason: str = ""
    exact: bool = False


def unknown(reason: str) -> Target:
    return Target("unknown", reason=reason)


def external(value: str, reason: str) -> Target:
    return Target("external", value, reason=reason)


class Resolver:
    def __init__(self, scopes: dict[str, Scope]):
        self.scopes = scopes
        self.modules = {s.id for s in scopes.values() if s.kind == "module"}
        self._mro_cache: dict[str, list[str]] = {}
        self._types: dict[int, str | None] = {}
        self._reasons: dict[int, str] = {}
        self._bases: dict[str, list[str]] = {}
        self._external_bases: dict[str, list[str]] = {}  # class id -> dotted names of bases outside the repo
        self._unresolved_bases: dict[str, str] = {}  # class id -> a base ripple could not resolve
        self.subclasses: dict[str, list[str]] = {}
        self._all_subclasses: dict[str, list[str]] = {}
        self._stdlib_classes: dict[str, bool] = {}
        self._stdlib_objects: dict[str, object] = {}
        self._subclass_defines: dict[tuple[str, str], bool] = {}
        self._external_base_index: dict[str, list[str]] | None = None  # dotted name of an external base -> repo classes with such a base
        self._base_proven: set[str] | None = None  # dotted names a repo class inherits from, so they are classes
        # Resolving a base (`class X(self.Attr)`) can look a member up before every
        # class has its bases, so until the hierarchy exists a member that is missing
        # is unknown, and nothing computed meanwhile is kept.
        self._building = True
        self.subclasses = self._direct_subclasses()
        self._building = False
        self._mro_cache.clear()
        self._types.clear()
        self._reasons.clear()
        self.external_reasons: Counter = Counter()

    # -- output ------------------------------------------------------------

    def run(self) -> Graph:
        symbols = {
            s.id: Symbol(s.id, s.kind, s.file, s.line, s.end_line) for s in self.scopes.values()
        }
        edges, unresolved, external_count = [], [], 0
        for scope in self.scopes.values():
            for call in scope.calls:
                try:
                    outcome = self.resolve_call(scope, call)
                except RecursionError:  # a chain of definitions deeper than the raised limit
                    outcome = unknown("too deeply nested to follow")
                if outcome.kind == "external":
                    external_count += 1
                    self.external_reasons[outcome.reason or EXT_IMPORT] += 1
                elif outcome.kind == "unknown":
                    unresolved.append(Unresolved(scope.id, scope.file, call.line, call.text, outcome.reason))
                else:
                    for callee, kind in self.edge_targets(outcome, call):
                        edges.append(Edge(scope.id, callee, scope.file, call.line, kind))
        classes = [s.id for s in self.scopes.values() if s.kind == "class"]
        bases = {cls: self.base_classes(cls) for cls in classes}
        return Graph(
            symbols,
            edges,
            unresolved,
            external_count,
            bases={cls: found for cls, found in bases.items() if found},
            external_bases={cls: self._external_bases[cls] for cls in classes if cls in self._external_bases},
            external_reasons=dict(sorted(self.external_reasons.items())),
        )

    def edge_targets(self, target: Target, call: RawCall) -> list[tuple[str, str]]:
        """Turn a resolved callee into (symbol id, edge kind) pairs."""
        default = "decorator" if call.kind == "decorator" else "call"
        if self.scopes[target.value].kind == "class":
            return [(self.constructor(target.value), "constructor")]
        if target.kind == "symbol":
            return [(target.value, default)]
        # A bound method: the method found, plus overrides in subclasses.
        found = [(target.value, default)]
        if target.exact:
            return found
        name = target.value.rsplit(".", 1)[-1]
        owner = target.value.rsplit(".", 1)[0]
        for sub in self.all_subclasses(target.receiver):
            method = self.scopes[sub].names.get(name)
            if sub != owner and method is not None and method.kind == "def":
                found.append((method.target, "override"))
        return found

    # -- calls -------------------------------------------------------------

    def resolve_call(self, scope: Scope, call: RawCall) -> Target:
        if call.special == "super":
            target = self.super_member(scope, call.chain[0])
        elif call.special == "decorator_result":
            factory = self.resolve_chain(scope, call.chain) if call.chain else unknown("")
            if factory.kind == "external":
                return factory
            return unknown("applies the function a decorator factory returned")
        elif call.special == "builtin":
            return external(call.text, EXT_BUILTIN_VALUE)
        elif call.chain is None:
            return unknown("the callee is computed at runtime (subscript, call result or similar)")
        else:
            target = self.resolve_chain(scope, call.chain)
        if target.kind in ("instance", "foreign"):
            return unknown("calls an instance (its __call__ is not followed)")
        if target.kind == "module":
            return unknown("calls a module")
        return target

    def resolve_chain(self, scope: Scope, chain: list) -> Target:
        """Resolve a dotted expression like a.b.c as seen from scope."""
        found = self.lookup(scope, chain[0])
        if found is None:
            if chain[0] in BUILTIN_NAMES:
                return external("builtins." + ".".join(chain), EXT_BUILTIN)
            return unknown(f"name '{chain[0]}' is not defined anywhere ripple can see")
        where, binding = found
        if binding.kind == "import":
            # import a.b binds a; resolve the whole a.b.c path at once so
            # namespace packages (no __init__.py) still work.
            return self.resolve_dotted(".".join([binding.target] + chain[1:]))
        target = self.binding_target(where, binding)
        for attr in chain[1:]:
            target = self.member(target, attr)
        return target

    def lookup(self, scope: Scope, name: str) -> tuple[Scope, Binding] | None:
        """Python's name lookup: local, enclosing functions, module.

        A class body is visible to code directly in it, but not to the
        methods inside it, which is why a method must say self.x.
        """
        current, first = scope, True
        while current is not None:
            if name in current.globals:
                current = self.scopes[current.module]
            if current.kind != "class" or first:
                binding = current.names.get(name)
                if binding is not None:
                    return current, binding
            first = False
            current = self.scopes.get(current.parent) if current.parent else None
        return None

    def binding_target(self, where: Scope, binding: Binding, depth: int = 0) -> Target:
        if binding.kind == "def":
            return Target("symbol", binding.target)
        if binding.kind == "import":
            return self.resolve_dotted(binding.target)
        if binding.kind == "from":
            return self.module_member(binding.target, binding.name, depth + 1)
        if binding.kind == "self":
            cls = self.enclosing_class(where)
            return Target("instance", cls) if cls else unknown("self outside a class")
        cls = self.var_type(binding)
        if cls is None:
            return unknown(self.why_unknown(binding.evidence))
        value = self.value_of_type(cls)
        if value.kind in ("instance", "foreign") and self.constructed(binding.evidence):
            value = replace(value, exact=True)
        return value

    def constructed(self, evidence: list) -> bool:
        """Whether every piece of evidence is a call of a class: the value is exactly that class."""
        return bool(evidence) and all(ref.kind == "call" and self.names_class(ref) for ref in evidence)

    def names_class(self, ref: TypeRef) -> bool:
        """Whether ref calls a class named statically (never through self or cls, which may be a
        subclass that redefines it) and calling it provably builds exactly that class."""
        named = self.resolve_chain(self.scopes[ref.scope], ref.expr.split("."))
        if named.kind == "external":
            return self.stdlib_class(named.value)  # nothing in the standard library builds a repo subclass
        return named.kind == "symbol" and self.scopes[named.value].kind == "class" and self.builds_itself(named.value)

    def builds_itself(self, cls: str) -> bool:
        """Whether C() can only give an instance of exactly C.

        A __new__ or a metaclass can return an instance of another class, usually a
        subclass (anyio's Event.__new__ picks a backend's), so any in the hierarchy
        spoils the proof, and so does a base ripple could not resolve: it may define one.
        Bases outside the repo are trusted not to.
        """
        if self.unresolved_base(cls):
            return False
        return not any(
            "__new__" in self.scopes[klass].names or self.scopes[klass].redirects_construction for klass in self.mro(cls)
        )

    def value_of_type(self, cls: str | None) -> Target:
        """A value whose class is cls: a repo class, a builtin such as dict, a class from
        outside the repo, or unknown."""
        if cls is None:
            return unknown("the receiver's class is not known statically")
        if cls in BUILTIN_TYPES:
            return external(cls, EXT_BUILTIN_VALUE)
        if cls.startswith(EXTERNAL):
            return Target("foreign", cls[len(EXTERNAL) :])
        return Target("instance", cls)

    # -- classes outside the repo ------------------------------------------

    def stdlib_class(self, dotted: str) -> bool:
        """Whether a dotted name is a plain class of the standard library.

        Asked of the interpreter ripple runs on, for modules on SAFE_STDLIB_MODULES only.
        "Plain" means its metaclass is `type`: an ABC or an Enum can be satisfied by classes
        that never inherit from them, and a function (threading.Lock before 3.13) builds
        whatever it likes. A name that cannot be asked about is not proven a class.
        """
        found = self._stdlib_classes.get(dotted)
        if found is None:
            found = self._stdlib_classes[dotted] = type(self.stdlib_object(dotted)) is type
        return found

    def stdlib_object(self, dotted: str):
        """What a dotted name is in the standard library (None when it cannot be asked about)."""
        if dotted not in self._stdlib_objects:
            self._stdlib_objects[dotted] = self._ask_stdlib(dotted)
        return self._stdlib_objects[dotted]

    @staticmethod
    def _ask_stdlib(dotted: str):
        parts = dotted.split(".")
        for i in range(len(parts) - 1, 0, -1):
            module = ".".join(parts[:i])
            if module in SAFE_STDLIB_MODULES:
                try:
                    with warnings.catch_warnings():
                        warnings.simplefilter("ignore")
                        obj = importlib.import_module(module)
                        for attr in parts[i:]:
                            obj = getattr(obj, attr)
                except Exception:  # an import or attribute error: not proven
                    return None
                return obj
        return None

    def outside_class(self, dotted: str) -> bool:
        """Whether a name from outside the repo is provably a class: a plain standard
        library class, or one a repo class lists as a base (only classes can be bases).
        Protocols and ABCs stay out, since a class that never inherits may stand in for them."""
        if self.stdlib_class(dotted):
            return True
        if self._building:  # the hierarchy is still being worked out: nothing is proven yet
            return False
        if self._base_proven is None:
            self._base_proven = {name for names in self._external_bases.values() for name in names}
        return dotted in self._base_proven and not dotted.startswith(NOMINAL_ONLY_EXCLUDED)

    def subclass_defines(self, dotted: str, attr: str) -> bool:
        """Whether a repo class that may inherit from the outside class defines attr itself or in
        a subclass, so the value (typed as the outside class) could be one of its instances.

        A repo class may sit anywhere below the outside class, through classes ripple never reads
        (logging.Handler -> StreamHandler -> JsonHandler), so its external bases are compared with
        the outside class by name and by what the interpreter says (may_inherit), never by the
        last word of the name, and a class whose bases ripple could not resolve is never ruled out.
        """
        key = (dotted, attr)
        found = self._subclass_defines.get(key)
        if found is None:
            if self._external_base_index is None:
                index: dict[str, list[str]] = {}
                for cls, names in self._external_bases.items():
                    for name in names:
                        index.setdefault(name, []).append(cls)
                self._external_base_index = index
            candidates = list(self._unresolved_bases)
            for base, classes in self._external_base_index.items():
                if self.may_inherit(base, dotted):
                    candidates.extend(classes)
            found = False
            for cls in candidates:
                for klass in [cls, *self.all_subclasses(cls)]:
                    scope = self.scopes[klass]
                    if attr in scope.names or attr in scope.attr_types:
                        found = True
                        break
                if found:
                    break
            self._subclass_defines[key] = found
        return found

    def may_inherit(self, base: str, above: str) -> bool:
        """Whether a class named base (outside the repo) may be, or inherit from, the class named above.

        Both in the standard library and on the list ripple may import: the interpreter answers.
        A standard library class never inherits from a dependency's. Anything else (a dependency's
        class, a standard library module not on the list) may sit anywhere in a hierarchy, so
        it is not ruled out.
        """
        if base == above:
            return True
        below, over = self.stdlib_object(base), self.stdlib_object(above)
        if isinstance(below, type) and isinstance(over, type):
            try:
                return issubclass(below, over)
            except TypeError:
                return True
        return not (self.in_stdlib(base) and not self.in_stdlib(above))

    @staticmethod
    def in_stdlib(dotted: str) -> bool:
        return dotted.split(".")[0] in sys.stdlib_module_names

    # -- modules -----------------------------------------------------------

    def resolve_dotted(self, path: str) -> Target:
        """Resolve pkg.mod.Class.method: longest module prefix, then members."""
        parts = path.split(".")
        for i in range(len(parts), 0, -1):
            prefix = ".".join(parts[:i])
            if prefix in self.modules:
                target = Target("module", prefix)
                for attr in parts[i:]:
                    target = self.member(target, attr)
                return target
        return external(path, EXT_IMPORT)

    def module_member(self, module: str, name: str, depth: int = 0) -> Target:
        """What `from module import name` gives you, following re-exports."""
        full = f"{module}.{name}"
        if depth > MAX_IMPORT_HOPS:
            return unknown(f"import cycle while resolving {full}")
        if module not in self.modules:
            return Target("module", full) if full in self.modules else external(full, EXT_IMPORT)
        scope = self.scopes[module]
        binding = scope.names.get(name)
        # `from . import util` in pkg/__init__.py binds util to "member util of
        # pkg", which is the question being asked: it names the submodule, not a loop.
        if binding is not None and (binding.kind, binding.target, binding.name) != ("from", module, name):
            return self.binding_target(scope, binding, depth)
        for star in scope.star_imports:
            if star not in self.modules and full in self.modules:
                # A module outside the repo may bind the name, and then it is not the submodule.
                return unknown(f"'{name}' may come from the star import of {star} in {module}")
            found = self.module_member(star, name, depth + 1)
            if found.kind != "unknown" and not (found.kind == "module" and found.value == full):
                return found
        if full in self.modules:  # the attribute is missing, so the import machinery loads the submodule
            return Target("module", full)
        return unknown(f"'{name}' is not defined in module {module}")

    # -- attributes --------------------------------------------------------

    def member(self, target: Target, attr: str) -> Target:
        if target.kind == "module":
            return self.module_member(target.value, attr)
        if target.kind == "external":
            if target.reason == EXT_INSTANCE:
                # x.a.b() with x from outside the repo: x.a is whatever was stored on x, which may be
                # a repo object (threading.local holds anything), so only x.a() itself is outside.
                return unknown("an attribute of an object from outside the repo may hold anything")
            return external(f"{target.value}.{attr}", target.reason)
        if target.kind == "foreign":
            if self._building:
                return unknown(f"'{attr}' on {target.value}: the repo's subclasses of it are not known yet")
            if not target.exact and self.subclass_defines(target.value, attr):
                return unknown(
                    f"'{attr}' may be overridden by a repo subclass of {target.value}, which the value could be"
                )
            return external(f"{target.value}.{attr}", EXT_INSTANCE)
        if target.kind == "symbol":
            if self.scopes[target.value].kind != "class":
                return unknown("attribute of a function")
            found = self.class_member(target.value, attr)
            return Target("symbol", found) if found else unknown(f"no '{attr}' on class {target.value}")
        if target.kind == "instance":
            method = self.class_member(target.value, attr)
            if method is not None and self.scopes[method].is_property:
                return self.value_of_type(self.return_type(method)) if self.return_type(method) else unknown(
                    f"property {method} has no return annotation naming a class"
                )
            if method is not None:
                return Target("bound", method, receiver=target.value, exact=target.exact)
            cls = self.attribute_type(target.value, attr)
            if cls:
                # Never exact: a subclass of the holder, or code outside it, may assign another class.
                return self.value_of_type(cls)
            if self.assigns(target.value, attr):
                return unknown(f"'{attr}' is not a method or typed attribute of {target.value}")
            return self.missing_member(target.value, attr)
        if target.kind == "bound":
            return unknown("attribute of a method")
        return target  # unknown stays unknown, keeping the first reason

    def assigns(self, cls: str, attr: str) -> bool:
        """Whether a class in cls's hierarchy binds attr (in its body, or as self.attr)."""
        return any(attr in self.scopes[k].names or attr in self.scopes[k].attr_types for k in self.mro(cls))

    def missing_member(self, cls: str, attr: str) -> Target:
        """cls.attr where no class of cls's hierarchy in the repo defines attr.

        It is external only when it must come from a base outside the repo
        (ast.NodeVisitor, Exception). If a base could not be resolved, or a
        repo subclass defines attr (the template method pattern), the call may
        well reach repo code, so it is reported as unresolved.
        """
        blocked = self.unresolved_base(cls)
        if blocked:
            klass, base = blocked
            return unknown(f"'{attr}' is not defined on {cls}, and base class {base} of {klass} could not be resolved")
        if self._building:
            return unknown(f"'{attr}' is not defined on {cls}, and its subclasses are not known yet")
        if any(attr in self.scopes[sub].names for sub in self.all_subclasses(cls)):
            return unknown(f"'{attr}' is not defined on {cls} or its bases in the repo, but subclasses define it")
        if self.has_external_base(cls):
            return external(f"{cls}.{attr}", EXT_INHERITED)
        return unknown(f"'{attr}' is not a method or typed attribute of {cls}")

    def class_member(self, cls: str, name: str) -> str | None:
        """The def (method or nested class) that cls.name finds, walking bases."""
        for klass in self.mro(cls):
            binding = self.scopes[klass].names.get(name)
            if binding is not None:
                return binding.target if binding.kind == "def" else None
        return None

    def super_member(self, scope: Scope, name: str) -> Target:
        cls = self.enclosing_class(scope)
        if cls is None:
            return unknown("super() outside a class")
        for klass in self.mro(cls)[1:]:
            binding = self.scopes[klass].names.get(name)
            if binding is not None and binding.kind == "def":
                return Target("symbol", binding.target)
        blocked = self.unresolved_base(cls)
        if blocked:
            klass, base = blocked
            return unknown(f"'{name}' is not defined on the bases of {cls}, and base class {base} of {klass} could not be resolved")
        if not hasattr(object, name) and not self.has_external_base(cls):
            # Nothing the class inherits defines it and object does not either, so
            # it is a mixin counting on a sibling class in the concrete MRO.
            return unknown(f"super().{name}: no base of {cls} defines it, so it may reach a sibling class in a subclass's MRO")
        return external(f"super().{name}", EXT_SUPER)

    # -- classes -----------------------------------------------------------

    def enclosing_class(self, scope: Scope) -> str | None:
        while scope is not None and scope.kind != "class":
            scope = self.scopes.get(scope.parent) if scope.parent else None
        return scope.id if scope else None

    def base_classes(self, cls: str) -> list[str]:
        """Bases that resolve to classes in the repo.

        Any other base is left out of the list and noted: in _external_bases
        when it comes from outside the repo (the stdlib or a dependency), in
        _unresolved_bases when ripple cannot tell what it is (a name bound two
        ways, the result of a call). The difference matters: a member missing
        from the repo's classes is inherited from an external base, but it
        could be anywhere behind an unresolved one.
        """
        if cls not in self._bases:
            self._bases[cls] = []  # guards against a class that inherits itself
            scope = self.scopes[cls]
            parent = self.scopes[scope.parent]
            found = []
            for base in scope.bases:
                parts = base.split(".")
                if all(part.isidentifier() for part in parts):
                    target = self.resolve_chain(parent, parts)
                else:
                    target = unknown("the base is computed")  # e.g. with_metaclass(Meta)
                if target.kind == "symbol" and self.scopes[target.value].kind == "class":
                    found.append(target.value)
                elif target.kind == "external":
                    if base != "object":
                        self._external_bases.setdefault(cls, []).append(target.value)
                else:
                    self._unresolved_bases.setdefault(cls, base)
            self._bases[cls] = found
        return self._bases[cls]

    def has_external_base(self, cls: str) -> bool:
        return any(klass in self._external_bases for klass in self.mro(cls))

    def unresolved_base(self, cls: str) -> tuple[str, str] | None:
        """(class, base) for the first class in cls's hierarchy with a base ripple could not resolve."""
        for klass in self.mro(cls):
            if klass in self._unresolved_bases:
                return klass, self._unresolved_bases[klass]
        return None

    def mro(self, cls: str) -> list[str]:
        """Method lookup order: the class, then bases depth-first, left to right.

        Python uses C3 linearization, which differs from this only for
        diamond-shaped hierarchies. The simple order is easier to reason about
        and right for the single and linear inheritance most code uses.
        """
        if cls not in self._mro_cache:
            self._mro_cache[cls] = [cls]  # guards against a class that inherits itself
            order = {cls: None}  # a dict keeps insertion order and tests membership in O(1)
            for base in self.base_classes(cls):
                for klass in self.mro(base):
                    order.setdefault(klass)
            self._mro_cache[cls] = list(order)
        return self._mro_cache[cls]

    def _direct_subclasses(self) -> dict[str, list[str]]:
        children: dict[str, list[str]] = {}
        for scope in self.scopes.values():
            if scope.kind == "class":
                for base in self.base_classes(scope.id):
                    children.setdefault(base, []).append(scope.id)
        return children

    def all_subclasses(self, cls: str) -> list[str]:
        """Every class that inherits from cls, transitively. Callers must not modify the list.

        Asked once per call site through cls, and a base such as Django's Model has
        hundreds of subclasses, so each class's answer is worked out once.
        """
        found = self._all_subclasses.get(cls)
        if found is None:
            seen, todo = {}, list(self.subclasses.get(cls, []))
            while todo:
                sub = todo.pop()
                if sub not in seen:
                    seen[sub] = None
                    todo.extend(self.subclasses.get(sub, []))
            found = self._all_subclasses[cls] = list(seen)
        return found

    # -- types -------------------------------------------------------------

    def var_type(self, binding: Binding) -> str | None:
        """The class of a variable, if every assignment to it agrees."""
        return self.agreed_type(binding.evidence)

    def attribute_type(self, cls: str, attr: str) -> str | None:
        for klass in self.mro(cls):
            evidence = self.scopes[klass].attr_types.get(attr)
            if evidence:
                return self.agreed_type(evidence)
        return None

    def agreed_type(self, evidence: list) -> str | None:
        """One class that every piece of evidence agrees on, else None.

        Answers are memoized per evidence list. A list that is still being
        worked out when it is asked about again is part of a cycle, such as
        `line = line.rstrip()`, and counts as unknown: circular evidence
        proves nothing.
        """
        key = id(evidence)
        if key in self._types:
            return self._types[key]
        self._types[key] = None  # in progress: a cycle back here sees "unknown"
        result = None
        if evidence and all(ref.kind != "unknown" for ref in evidence):
            classes = {self.type_of(ref) for ref in evidence}
            if len(classes) == 1:
                result = classes.pop()
        self._types[key] = result
        return result

    def why_unknown(self, evidence: list) -> str:
        """The first reason a variable's class is not known, in words that finish "the receiver is ...".

        Memoized like agreed_type, and for the same reason: `x = y` and `y = x` ask about each other.
        """
        key = id(evidence)
        if key in self._reasons:
            return self._reasons[key]
        self._reasons[key] = result = "the receiver's class is not known statically"
        classes = set()
        for ref in evidence:
            if ref.kind == "unknown":
                result = ref.expr
                break
            cls = self.type_of(ref)
            if cls is None:
                result = self.why_not_a_class(ref)
                break
            classes.add(cls)
        else:
            if len(classes) > 1:
                result = "the receiver is assigned values of different classes"
        self._reasons[key] = result
        return result

    def why_not_a_class(self, ref: TypeRef) -> str:
        """Why one piece of evidence (annotation, call or copy) gives no class."""
        if ref.kind == "name":
            return "the receiver is copied from a variable whose class is not known"
        named = self.resolve_chain(self.scopes[ref.scope], ref.expr.split("."))
        if ref.kind == "annotation":
            if named.kind == "external":
                return "the receiver is annotated with a name from outside the repo that ripple cannot prove is a class"
            if named.kind == "unknown":
                return "the receiver is annotated with a name ripple cannot resolve"
            return "the receiver is annotated with something that is not a class"
        if named.kind == "external":
            return "the receiver is the result of a call into code outside the repo, not proven to build a class"
        if named.kind in ("symbol", "bound"):
            if not self.scopes[named.value].return_annotated:
                return "the receiver is the result of a call to a function with no return annotation"
            return "the receiver is the result of a call whose return annotation does not name a class ripple can follow"
        if named.kind == "unknown":
            return "the receiver is the result of a call ripple cannot resolve"
        return "the receiver is the result of calling something that is not a function or class"

    def type_of(self, ref: TypeRef) -> str | None:
        """The class one piece of evidence points at: a repo class id, a
        builtins.* type, an "external:" class, or None when it proves nothing."""
        if ref.kind == "builtin":
            return f"builtins.{ref.expr}"
        if ref.kind == "unknown":
            return None
        scope = self.scopes[ref.scope]
        if ref.kind == "name":
            found = self.lookup(scope, ref.expr)
            if found is None:
                return None
            target = self.binding_target(found[0], found[1])
            if target.kind == "instance" or (target.kind == "external" and target.value in BUILTIN_TYPES):
                return target.value
            if target.kind == "foreign":
                return EXTERNAL + target.value
            return None
        # "annotation" or "call": the expression must name a class, or (for a
        # call) a repo function whose return annotation names one.
        named = self.resolve_chain(scope, ref.expr.split("."))
        if named.kind == "external":
            if named.value in BUILTIN_TYPES:
                return named.value
            return EXTERNAL + named.value if self.outside_class(named.value) else None
        if named.kind not in ("symbol", "bound"):
            return None
        if self.scopes[named.value].kind == "class":
            return named.value
        return self.return_type(named.value) if ref.kind == "call" else None

    def return_type(self, function: str) -> str | None:
        """The class a function's return annotation names, if any.

        Like parameter annotations, it is resolved in the scope that holds the
        def (for a method, the class body).
        """
        scope = self.scopes[function]
        return self.type_of(TypeRef("annotation", scope.returns, scope.parent)) if scope.returns else None

    def constructor(self, cls: str) -> str:
        """Calling a class runs the first __init__ in its bases, else the class itself."""
        return self.class_member(cls, "__init__") or cls
