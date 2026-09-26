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
from dataclasses import dataclass

from .indexer import Binding, RawCall, Scope, TypeRef
from .model import Edge, Graph, Symbol, Unresolved

BUILTIN_NAMES = set(dir(builtins))
# Values of these types run only builtin methods: "x".strip(), {}.get(...).
BUILTIN_TYPES = {f"builtins.{n}" for n in BUILTIN_NAMES if isinstance(getattr(builtins, n), type)}
MAX_IMPORT_HOPS = 8


@dataclass(frozen=True)
class Target:
    """What an expression refers to, as far as static analysis can tell.

    kind is one of:
      symbol    a function, method or class in the repo (value is its id)
      module    a module in the repo (value is its dotted name)
      instance  a value whose class is known (value is the class id)
      bound     a method looked up on an instance (value is the method id,
                receiver is the instance's class, used to find overrides)
      external  something outside the repo: stdlib, a dependency, a builtin
      unknown   cannot be determined statically (reason says why)
    """

    kind: str
    value: str = ""
    receiver: str = ""
    reason: str = ""


def unknown(reason: str) -> Target:
    return Target("unknown", reason=reason)


class Resolver:
    def __init__(self, scopes: dict[str, Scope]):
        self.scopes = scopes
        self.modules = {s.id for s in scopes.values() if s.kind == "module"}
        self._mro_cache: dict[str, list[str]] = {}
        self._types: dict[int, str | None] = {}
        self._bases: dict[str, list[str]] = {}
        self._external_bases: dict[str, list[str]] = {}  # class id -> dotted names of bases outside the repo
        self._unresolved_bases: dict[str, str] = {}  # class id -> a base ripple could not resolve
        self.subclasses = self._direct_subclasses()

    # -- output ------------------------------------------------------------

    def run(self) -> Graph:
        symbols = {
            s.id: Symbol(s.id, s.kind, s.file, s.line, s.end_line) for s in self.scopes.values()
        }
        edges, unresolved, external = [], [], 0
        for scope in self.scopes.values():
            for call in scope.calls:
                outcome = self.resolve_call(scope, call)
                if outcome.kind == "external":
                    external += 1
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
            external,
            bases={cls: found for cls, found in bases.items() if found},
            external_bases={cls: self._external_bases[cls] for cls in classes if cls in self._external_bases},
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
            return Target("external", call.text)
        elif call.chain is None:
            return unknown("the callee is computed at runtime (subscript, call result or similar)")
        else:
            target = self.resolve_chain(scope, call.chain)
        if target.kind == "instance":
            return unknown("calls an instance (its __call__ is not followed)")
        if target.kind == "module":
            return unknown("calls a module")
        return target

    def resolve_chain(self, scope: Scope, chain: list) -> Target:
        """Resolve a dotted expression like a.b.c as seen from scope."""
        found = self.lookup(scope, chain[0])
        if found is None:
            if chain[0] in BUILTIN_NAMES:
                return Target("external", "builtins." + ".".join(chain))
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
        return self.value_of_type(self.var_type(binding))

    def value_of_type(self, cls: str | None) -> Target:
        """A value whose class is cls: a repo class, a builtin such as dict, or unknown."""
        if cls is None:
            return unknown("the receiver's class is not known statically")
        if cls in BUILTIN_TYPES:
            return Target("external", cls)
        return Target("instance", cls)

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
        return Target("external", path)

    def module_member(self, module: str, name: str, depth: int = 0) -> Target:
        """What `from module import name` gives you, following re-exports."""
        full = f"{module}.{name}"
        if depth > MAX_IMPORT_HOPS:
            return unknown(f"import cycle while resolving {full}")
        if module not in self.modules:
            return Target("module", full) if full in self.modules else Target("external", full)
        scope = self.scopes[module]
        binding = scope.names.get(name)
        if binding is not None:
            return self.binding_target(scope, binding, depth)
        if full in self.modules:
            return Target("module", full)
        for star in scope.star_imports:
            found = self.module_member(star, name, depth + 1)
            if found.kind != "unknown":
                return found
        return unknown(f"'{name}' is not defined in module {module}")

    # -- attributes --------------------------------------------------------

    def member(self, target: Target, attr: str) -> Target:
        if target.kind == "module":
            return self.module_member(target.value, attr)
        if target.kind == "external":
            return Target("external", f"{target.value}.{attr}")
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
                return Target("bound", method, receiver=target.value)
            cls = self.attribute_type(target.value, attr)
            if cls:
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
        if any(attr in self.scopes[sub].names for sub in self.all_subclasses(cls)):
            return unknown(f"'{attr}' is not defined on {cls} or its bases in the repo, but subclasses define it")
        if self.has_external_base(cls):
            return Target("external", f"{cls}.{attr}")
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
        return Target("external", f"super().{name}")

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
            order = [cls]
            for base in self.base_classes(cls):
                for klass in self.mro(base):
                    if klass not in order:
                        order.append(klass)
            self._mro_cache[cls] = order
        return self._mro_cache[cls]

    def _direct_subclasses(self) -> dict[str, list[str]]:
        children: dict[str, list[str]] = {}
        for scope in self.scopes.values():
            if scope.kind == "class":
                for base in self.base_classes(scope.id):
                    children.setdefault(base, []).append(scope.id)
        return children

    def all_subclasses(self, cls: str) -> list[str]:
        seen, todo = [], list(self.subclasses.get(cls, []))
        while todo:
            sub = todo.pop()
            if sub not in seen:
                seen.append(sub)
                todo.extend(self.subclasses.get(sub, []))
        return seen

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
        if evidence and None not in evidence:
            classes = {self.type_of(ref) for ref in evidence}
            if len(classes) == 1:
                result = classes.pop()
        self._types[key] = result
        return result

    def type_of(self, ref: TypeRef) -> str | None:
        """The class one piece of evidence points at: a repo class id, a
        builtins.* type, or None when it proves nothing."""
        if ref.kind == "builtin":
            return f"builtins.{ref.expr}"
        scope = self.scopes[ref.scope]
        if ref.kind == "name":
            found = self.lookup(scope, ref.expr)
            if found is None:
                return None
            target = self.binding_target(found[0], found[1])
            if target.kind == "instance" or (target.kind == "external" and target.value in BUILTIN_TYPES):
                return target.value
            return None
        # "annotation" or "call": the expression must name a class, or (for a
        # call) a repo function whose return annotation names one.
        named = self.resolve_chain(scope, ref.expr.split("."))
        if named.kind == "external" and named.value in BUILTIN_TYPES:
            return named.value
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
