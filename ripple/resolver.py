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

import importlib
import re
import warnings
from collections import Counter
from dataclasses import dataclass, replace

from .facts import (
    ASSIGNED_UNTYPED,
    COMPUTED_OTHER,
    LOOP_VARIABLE,
    Binding,
    CallbackCall,
    Mentions,
    RawCall,
    Scope,
    Table,
    TypeRef,
)
from .model import Decoration, Edge, Graph, Symbol, Unresolved
from .profile import PYTHON, LanguageProfile

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

# RawCall.special values that are an edge only when proven (see facts.RawCall).
EFFECT_SPECIALS = ("access", "assign", "callback")

# A decorated function's call gives what its decorators return, which ripple cannot tell is what the def returned.
DECORATED_RESULT = "the callee is the result of a call to a decorated function, and its decorator may change what it returns"
DECORATED_RESULT_RECEIVER = "the receiver is the result of a call to a decorated function, and its decorator may change what it returns"


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
      each      a call that reaches one of several defs, whichever the loop variable holds
                (members are their ids, receiver is unused)
      external  something outside the repo: stdlib, a dependency, a builtin
                (reason says which kind, for counting)
      unknown   cannot be determined statically (reason says why)
    """

    kind: str
    value: str = ""
    receiver: str = ""
    reason: str = ""
    exact: bool = False
    members: tuple = ()


@dataclass(frozen=True)
class Shape:
    """What a decorator function does to the function it is given.

    kind "identity" returns its argument unchanged. kind "wrapper" returns the def
    nested in it (wrapper, an id), which calls the parameter named param.
    """

    kind: str
    function: str
    wrapper: str = ""
    param: str = ""


def unknown(reason: str) -> Target:
    return Target("unknown", reason=reason)


def external(value: str, reason: str) -> Target:
    return Target("external", value, reason=reason)


class Resolver:
    def __init__(
        self,
        scopes: dict[str, Scope],
        mentions=None,
        unparsed: list[str] | None = None,
        profile: LanguageProfile = PYTHON,
    ):
        self.scopes = scopes
        self.profile = profile
        # files that could not be parsed: they have no scope, and may do anything to a table or a partial
        self._unparsed = list(unparsed or [])
        # module Scope -> Mentions (what a module could change unseen), asked only to prove a table or a partial
        self._mentions_of = mentions
        self.modules = {s.id for s in scopes.values() if s.kind == "module"}
        self._mro_cache: dict[str, list[str]] = {}
        self._types: dict[int, str | None] = {}
        self._reasons: dict[int, str] = {}
        self._bases: dict[str, list[str]] = {}
        self._external_bases: dict[str, list[str]] = {}  # class id -> dotted names of bases outside the repo
        self._unresolved_bases: dict[str, str] = {}  # class id -> a base ripple could not resolve
        self.subclasses: dict[str, list[str]] = {}
        self._all_subclasses: dict[str, list[str]] = {}
        self._decorations: dict[str, list[tuple[Scope, RawCall]]] | None = None
        self._shapes: dict[str, Shape | None] = {}
        self._wrapping: dict[str, tuple[list[tuple[Shape, str]], str | None]] = {}
        self._wrappers: dict[str, tuple[Shape, list[tuple[str, str]]]] | None = None
        self._keeps_result: dict[str, bool] = {}
        self._stdlib_classes: dict[str, bool] = {}
        self._stdlib_objects: dict[str, object] = {}
        self._subclass_defines: dict[tuple[str, str], bool] = {}
        self._function_values: dict[int, Target | None] = {}
        self._rebound_names: set[str] | None = None  # names some module sets as an attribute, see Scope.rebinds
        self._risky: dict[str, tuple[str, int]] | None = None  # name -> (file, line) of the first non-reading mention
        self._unreadable: str | None = None  # a module that could not be read again, so its mentions are unknown
        self._store_reason: str | None | bool = False  # see store_problem (False: not asked yet)
        self._aliases: dict[str, set[str]] = {}
        self._alias_pairs: list[tuple[str, str]] | None = None
        self._tables: dict[tuple[str, str], str | None] = {}
        self._replaced: dict[str, bool] = {}  # class id -> whether a decorator may have replaced it
        self._external_base_index: dict[str, list[str]] | None = None  # dotted name of an external base -> repo classes with such a base
        self._base_proven: set[str] | None = None  # dotted names a repo class inherits from, so they are classes
        # Resolving a base (`class X(self.Attr)`) can look a member up before every
        # class has its bases, so until the hierarchy exists a member that is missing
        # is unknown, and nothing computed meanwhile is kept.
        self._tag_mode = False  # reading the tag of a JSX element, where `const X = memo(f)` means f (see jsx.py)
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
        decorations: list[Decoration] = []
        wrappers = self.wrappers()
        direct_users: set[str] = set()  # callees of an ordinary call, to spot a decorator also called as D(f)
        wrapper_calls = []
        for scope in self.scopes.values():
            wrapper = wrappers.get(scope.id)
            for call in scope.calls:
                if call.kind == "decoration":
                    # Applied when the class is defined. Never an edge: what it does to the decorated thing is not
                    # followed, so it stays an unresolved call, and where its name is written is kept apart.
                    unresolved.append(Unresolved(scope.id, scope.file, call.line, call.text, call.why))
                    decorations += self.decoration_sites(scope, call)
                    continue
                if call.special in EFFECT_SPECIALS:
                    # A property read or write, or a function passed by name: an edge when proven, and never
                    # an unresolved call or an outside one (most properties are plain values).
                    try:
                        proven = self.resolve_effect(scope, call)
                    except RecursionError:
                        proven = None
                    if proven is not None:
                        for callee, kind, via in self.edge_targets(proven, call):
                            edges.append(Edge(scope.id, callee, scope.file, call.line, kind, via))
                    continue
                if wrapper and self.calls_what_it_wraps(scope, call, wrapper[0]):
                    wrapper_calls.append((scope, call, wrapper))
                    continue
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
                    if call.kind == "call":
                        direct_users.update(outcome.members or (outcome.value,))
                    for callee, kind, via in self.edge_targets(outcome, call):
                        edges.append(Edge(scope.id, callee, scope.file, call.line, kind, via))
        for scope, call, (shape, wrapped) in wrapper_calls:
            for function, decorated in wrapped:
                edges.append(Edge(scope.id, function, scope.file, call.line, "wrapped", decorated))
            if shape.function in direct_users:
                reason = f"'{shape.param}' may also be a function passed straight to {shape.function}(...), not only one it decorates"
                unresolved.append(Unresolved(scope.id, scope.file, call.line, call.text, reason))
        if self.profile.component_bases:  # a language with JSX: its elements are edges too (see jsx.py)
            from .jsx import JsxEdges

            found, not_found, outside = JsxEdges(self, edges).run()
            edges += found
            unresolved += not_found
            external_count += outside
        classes = [s.id for s in self.scopes.values() if s.kind == "class"]
        bases = {cls: self.base_classes(cls) for cls in classes}
        return Graph(
            symbols,
            edges,
            unresolved,
            external_count,
            export_aliases=self.export_aliases(),
            bases={cls: found for cls, found in bases.items() if found},
            external_bases={cls: self._external_bases[cls] for cls in classes if cls in self._external_bases},
            external_reasons=dict(sorted(self.external_reasons.items())),
            decorations=decorations,
        )

    def export_aliases(self) -> dict[str, list[str]]:
        """{`module:Name`: [id, how]} for each name an ES module exports that is a function or class of the repo
        declared under another name or in another module: `export const Name = forwardRef(Inner)` (read as a JSX
        tag reads it), `export { Inner as Name }`, `export { Name } from "./x"`. how says which, in words a question
        about the name quotes; a re-export of a name another module exports under a wrapper or alias says so too
        (`a re-export of SubMenu from src/SubMenu, which exports it as forwardRef(SubMenuFR)`). A name that is the
        symbol the module declares under that name is not listed (its id already names it), nor is a name that does
        not resolve to one function or class: nothing is guessed.

        One more is listed, with an empty id: a name the module declares as a function or class AND exports as
        something else it cannot name (`function Card() {}` beside `export { Exported as Card }`). The declared
        symbol is then not what an importer of the name gets, and a question about the name must not settle on it."""
        if self.profile.package_imports:
            return {}
        found: dict[str, list[str]] = {}
        sources: dict[str, tuple[str, str]] = {}  # a re-export -> the module and name it re-exports
        self._tag_mode = True
        try:
            for scope in self.scopes.values():
                for name, binding in getattr(scope, "exports", {}).items():
                    key = f"{scope.id}:{name}"
                    try:
                        target = self.binding_target(scope, binding, 0, name)
                    except RecursionError:
                        continue
                    if target.kind == "symbol" and target.value == key:
                        continue
                    how = self._export_how(scope, name, binding, target)
                    if how is not None:
                        found[key] = [target.value, how]
                        if binding.kind == "from":
                            sources[key] = (binding.target, binding.name)
                    elif self._declared_elsewhere(scope, name, binding, key):
                        found[key] = ["", "something ripple cannot name as one function or class"]
        finally:
            self._tag_mode = False
        for key in sources:
            self._name_the_chain(found, sources, key, ())
        return found

    def _export_how(self, scope: Scope, name: str, binding: Binding, target: Target) -> str | None:
        """How an exported name came to be a function or class of the repo (see export_aliases), None when it did not."""
        if target.kind != "symbol" or self.scopes[target.value].kind not in ("function", "class"):
            return None
        held = getattr(scope, "wrapped", {}).get(name)
        if held and held[2] and name not in scope.rebound and scope.names.get(name) is binding:
            return held[2]
        if binding.kind == "def":
            return f"an alias of {target.value.partition(':')[2]}"
        if binding.kind == "from":
            return f"a re-export of {target.value}"
        return None

    def _declared_elsewhere(self, scope: Scope, name: str, binding: Binding, key: str) -> bool:
        """Whether the module declares a function or class `name` and exports the name as a different binding."""
        declared = scope.names.get(name)
        return (
            key in self.scopes
            and self.scopes[key].kind in ("function", "class")
            and declared is not None
            and declared is not binding
            and declared.kind == "def"
            and declared.target == key
        )

    def _name_the_chain(self, found: dict[str, list[str]], sources: dict[str, tuple[str, str]], key: str, seen: tuple) -> str:
        """The how of a re-export, saying what the module it comes from exports the name as when that is a wrapper or an alias."""
        module, name = sources[key]
        inner = f"{module}:{name}"
        if inner in found and inner not in seen and key not in seen and found[inner][0] == found[key][0]:
            if inner in sources:
                self._name_the_chain(found, sources, inner, (*seen, key))
            found[key][1] = f"a re-export of {name} from {module}, which exports it as {found[inner][1]}"
        return found[key][1]

    def decoration_sites(self, scope: Scope, call: RawCall) -> list[Decoration]:
        """The Decoration of a decorator written in scope: what its name resolves to, if it resolves to repo symbols.

        A name proven to come from outside the repo, or one that is no name (`@(a ? b : c)`), gives none: neither
        can be about a symbol of the repo. A name that does not resolve gives one with no target, so a symbol of
        that name can list it as a possible site.
        """
        if not call.chain:
            return []
        try:
            outcome = self.resolve_chain(scope, call.chain)
        except RecursionError:
            outcome = unknown("too deeply nested to follow")
        if outcome.kind == "external":
            return []
        name = re.sub(r"#\d+$", "", call.chain[-1])  # a block-local head is keyed `name#N` (see the TypeScript front end)
        if outcome.kind == "each":
            targets = outcome.members
        elif outcome.kind == "bound" and self.scopes[outcome.value].kind != "class":
            # A method on a receiver that may be a subclass: a call of it reaches the overrides too (an edge of kind
            # override), so a decoration is a site of each of them. Never an edge: only listed, as for the method.
            targets = tuple(dict.fromkeys(target for target, _ in self._edge_targets(outcome, call)))
        elif outcome.kind in ("symbol", "bound"):
            targets = (outcome.value,)
        else:
            targets = ("",)
        return [Decoration(scope.id, scope.file, call.line, call.text, name, target) for target in targets or ("",)]

    def calls_what_it_wraps(self, scope: Scope, call: RawCall, shape: Shape) -> bool:
        """Whether this call, inside a wrapper, is the call of the decorator's parameter: fn(...)."""
        if call.chain != [shape.param] or call.special or call.kind != "call":
            return False
        found = self.lookup(scope, shape.param)
        return found is not None and found[0].id == shape.function

    def edge_targets(self, target: Target, call: RawCall) -> list[tuple[str, str, str]]:
        """Turn a resolved callee into (symbol id, edge kind, via) triples.

        A name bound to a decorated function is bound to what the decorators returned, so a
        call by that name reaches the top wrapper, not the function (see wrapping). The wrapper
        body is shared by every function its decorator is applied to, so the edge says which
        function the name belonged to (via): what comes in through it goes on to that function
        only. via is empty for any other edge.
        """
        found = []
        for callee, kind in self._edge_targets(target, call):
            top = self.redirect(callee)
            found.append((top, kind, callee if top != callee else ""))
        return found

    def _edge_targets(self, target: Target, call: RawCall) -> list[tuple[str, str]]:
        default = call.kind if call.kind in ("decorator", "annotation") else "callback" if call.special == "callback" else "call"
        if target.kind == "each":
            return [(member, default) for member in target.members]
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

    # -- decorators ---------------------------------------------------------
    #
    # `@timed def hello()` binds the name hello to whatever timed returns. When timed
    # is a repo function that defines a wrapper and returns exactly that wrapper
    # (`def timed(fn): def wrapper(*a): return fn(*a); return wrapper`), the name is bound
    # to the wrapper, a call to hello reaches the wrapper, and the wrapper calls hello's
    # body. When timed returns its argument (`def register(fn): ...; return fn`) the
    # binding is unchanged. Anything else (a decorator from outside the repo, one that
    # returns different things on different paths, one built by a call) is not read, and
    # a decorated function is then treated as called directly, as before.

    def decorations(self) -> dict[str, list[tuple[Scope, RawCall]]]:
        """{decorated function id: its decorator calls, top to bottom, each with the scope that writes it}."""
        if self._decorations is None:
            found: dict[str, list[tuple[Scope, RawCall]]] = {}
            for scope in self.scopes.values():
                for call in scope.calls:
                    if call.decorates:
                        found.setdefault(call.decorates, []).append((scope, call))
            self._decorations = found
        return self._decorations

    def plain_function(self, function: str, kinds: tuple = ("function",)) -> Scope | None:
        """The scope of a def written once and not decorated, whose body can be read as it stands."""
        scope = self.scopes.get(function)
        if scope is None or scope.kind not in kinds or scope.defs != 1 or function in self.decorations():
            return None
        return scope

    def returned_def(self, function: Scope) -> str | None:
        """The def nested in function that every path returns, when that is all it returns."""
        if function.is_generator or function.is_async or not function.ends_with_return:
            return None
        names = set(function.return_names)
        binding = function.names.get(names.pop()) if len(names) == 1 else None
        if binding is None or binding.kind != "def":
            return None
        nested = self.scopes[binding.target]
        return binding.target if nested.parent == function.id and nested.kind == "function" and nested.defs == 1 else None

    def decorator_shape(self, function: str) -> Shape | None:
        """What a decorator function does to the function it is given, if it can be read."""
        if function not in self._shapes:
            self._shapes[function] = self._read_decorator(function)
        return self._shapes[function]

    def _read_decorator(self, function: str) -> Shape | None:
        scope = self.plain_function(function)
        if scope is None or not scope.params or scope.params[0] in scope.rebound:
            return None
        param = scope.params[0]
        if (
            scope.ends_with_return
            and not scope.is_generator
            and not scope.is_async
            and set(scope.return_names) == {param}
        ):
            return Shape("identity", function)
        wrapper = self.returned_def(scope)
        if wrapper is None or not self.plain_wrapper(wrapper):
            return None
        return Shape("wrapper", function, wrapper, param)

    def plain_wrapper(self, wrapper: str) -> bool:
        """Whether the wrapper is what the decorator returns: undecorated, or only given functools.wraps."""
        for scope, call in self.decorations().get(wrapper, []):
            if call.special != "decorator_result" or not call.chain:
                return False
            target = self.resolve_chain(scope, call.chain)
            if target.kind != "external" or target.value not in self.profile.wraps_names:
                return False
        return True

    def factory_result(self, factory: Target) -> str | None:
        """The decorator a factory returns (route("/x") gives register), when it returns a def written inside it."""
        if factory.kind not in ("symbol", "bound"):
            return None
        if factory.kind == "bound" and not factory.exact:
            name = factory.value.rsplit(".", 1)[-1]
            if any(name in self.scopes[sub].names for sub in self.all_subclasses(factory.receiver)):
                return None  # a subclass may return another decorator
        function = self.plain_function(factory.value, ("function", "method"))
        return self.returned_def(function) if function else None

    def applied_decorator(self, scope: Scope, call: RawCall) -> Shape | None:
        """What one decorator line does, or None when it cannot be read."""
        if call.chain is None or call.special not in ("", "decorator_result"):
            return None
        target = self.resolve_chain(scope, call.chain)
        if call.special == "decorator_result":
            inner = self.factory_result(target)
            return self.decorator_shape(inner) if inner else None
        return self.decorator_shape(target.value) if target.kind == "symbol" else None

    def wrapping(self, function: str) -> tuple[list[tuple[Shape, str]], str | None]:
        """How a function's decorator stack wraps it: [(shape, what that wrapper calls)] from the
        bottom up, and what the function's name is bound to (None when a decorator cannot be read)."""
        if function not in self._wrapping:
            pairs: list[tuple[Shape, str]] = []
            bound: str | None = function
            # A def written twice (in each branch of an if, or one after the other) is one symbol here,
            # and which def a call finds depends on the branch taken: a decoration on one of them says
            # nothing about the name, and the same on both is not applied twice.
            conditional = self.scopes[function].defs > 1
            for scope, call in reversed(self.decorations().get(function, [])):
                if conditional:
                    bound = None
                    break
                shape = self.applied_decorator(scope, call)
                if shape is None:
                    bound = None
                    break
                if shape.kind == "wrapper":
                    pairs.append((shape, bound))
                    bound = shape.wrapper
            self._wrapping[function] = (pairs, bound)
        return self._wrapping[function]

    def keeps_result(self, function: str) -> bool:
        """Whether calling the function's name gives what its def returns, so its return annotation
        says what a call gives. True for an undecorated def. For a decorated one, only when every
        decorator is proven to hand the value back: one that returns its argument, a wrapper whose every
        return is a call of the function it wraps, or a standard decorator that does (profile.result_keeping)."""
        if function not in self.decorations():
            return True
        if function not in self._keeps_result:
            scope = self.scopes[function]
            conditional = scope.plain_defs > 1 and not scope.overloaded  # see wrapping
            self._keeps_result[function] = not conditional and all(
                self.decoration_keeps_result(scope, call) for scope, call in self.decorations()[function]
            )
        return self._keeps_result[function]

    def decoration_keeps_result(self, scope: Scope, call: RawCall) -> bool:
        if call.special == "builtin":  # @x.setter, @x.getter, @x.deleter: the property still gives what the getter returns
            return True
        if call.chain is None or call.special not in ("", "decorator_result"):
            return False
        named = self.resolve_chain(scope, call.chain)
        if named.kind == "external":
            return named.value in self.profile.result_keeping
        shape = self.applied_decorator(scope, call)
        if shape is None:
            return False
        if shape.kind == "identity":
            return True
        wrapper = self.scopes[shape.wrapper]
        return (
            not wrapper.is_async
            and not wrapper.is_generator
            and wrapper.ends_with_return
            and bool(wrapper.return_calls)
            and all(callee == [shape.param] for callee in wrapper.return_calls)
            # fn is the decorator's parameter, seen through the wrapper's closure: no binding of any kind
            # (a local, a def, a loop or with target, an import) in the wrapper itself makes it something else.
            and shape.param not in wrapper.names
            and shape.param not in wrapper.nonlocals
        )

    def redirect(self, function: str) -> str:
        """The def a call by the function's name reaches: the top wrapper of its decorators, else itself."""
        if function not in self.decorations():
            return function
        return self.wrapping(function)[1] or function

    def wrappers(self) -> dict[str, tuple[Shape, list[tuple[str, str]]]]:
        """{wrapper id: (its decorator, every (def it is given, decorated function whose stack that is))}.

        The def it is given is the decorated function itself, or the wrapper below it in a stack.
        """
        if self._wrappers is None:
            found: dict[str, tuple[Shape, set[tuple[str, str]]]] = {}
            for function in self.decorations():
                for shape, wrapped in self.wrapping(function)[0]:
                    found.setdefault(shape.wrapper, (shape, set()))[1].add((wrapped, function))
            self._wrappers = {w: (shape, sorted(wrapped)) for w, (shape, wrapped) in found.items()}
        return self._wrappers

    # -- calls -------------------------------------------------------------

    def resolve_call(self, scope: Scope, call: RawCall) -> Target:
        if call.special == "super":
            target = self.super_member(scope, call.chain[0])
        elif call.special == "decorator_result":
            factory = self.resolve_chain(scope, call.chain) if call.chain else unknown("its callee is computed")
            if factory.kind == "external":
                return factory
            if factory.kind == "unknown":
                return unknown(f"applies the function a decorator factory returned, and the factory is unresolved ({factory.reason})")
            inner = self.factory_result(factory)
            if inner:
                return Target("symbol", inner)
            return unknown("applies the function a decorator factory returned, which is not a def inside the factory")
        elif call.special == "builtin":
            return external(call.text, EXT_BUILTIN_VALUE)
        elif call.special == "table":
            target = self.table_call(scope, call)
        elif call.path:
            target = self.resolve_path(scope, call.path)
        elif call.chain is None:
            return unknown(call.why or COMPUTED_OTHER)
        else:
            target = self.resolve_chain(scope, call.chain)
        if target.kind in ("instance", "foreign"):
            return unknown("calls an instance (its __call__ is not followed)")
        if target.kind == "module":
            return unknown("calls a module")
        return target

    def resolve_effect(self, scope: Scope, call: RawCall) -> Target | None:
        """What a property access or a function passed by name runs, or None when that is not proven."""
        if call.special == "callback":
            return self.callback_target(scope, call)
        parts = call.path or call.chain
        if not parts or len(parts) < 2:
            return None
        receiver = self.resolve_path(scope, parts[:-1]) if call.path else self.resolve_chain(scope, parts[:-1])
        if receiver.kind == "instance":
            cls = receiver.value
        elif receiver.kind == "symbol" and self.scopes[receiver.value].kind == "class":
            cls = receiver.value  # a static accessor, read on the class
        else:
            return None
        method = self.class_member(cls, parts[-1])
        accessor = self.scopes[method] if method else None
        if accessor is None or accessor.kind != "method":
            return None
        # A getter is a def that is_property. A setter is a def that is not a plain one (a property
        # setter in Python, the same facts), so a setter-only property has defs but no plain def.
        runs = accessor.is_property if call.special == "access" else accessor.defs > accessor.plain_defs
        if not runs:
            return None
        if receiver.kind == "symbol":
            return Target("symbol", method)
        return Target("bound", method, receiver=cls, exact=receiver.exact)

    def callback_target(self, scope: Scope, call: CallbackCall) -> Target | None:
        """The function or method passed by name, when what it is passed to is a builtin method that runs it."""
        if call.host is None or call.method not in self.profile.synchronous_callbacks or not call.chain:
            return None
        host = self.resolve_call(scope, call.host)
        if host.kind != "external" or host.reason != EXT_BUILTIN_VALUE:
            return None
        passed = self.resolve_chain(scope, call.chain)
        if passed.kind in ("symbol", "bound") and self.scopes[passed.value].kind in ("function", "method"):
            return passed
        return None

    def plain(self, name: str) -> str:
        """A name as the source wrote it. An ES-module front end binds a name declared in a block or a callback under a
        private key (`Parent#4`, see ts_indexer.py), which is looked up as it is and never shown or tested as syntax."""
        return name if self.profile.package_imports else re.sub(r"#\d+(?=\.|$)", "", name)

    def resolve_chain(self, scope: Scope, chain: list) -> Target:
        """Resolve a dotted expression like a.b.c as seen from scope."""
        found = self.lookup(scope, chain[0])
        if found is None:
            if chain[0] in self.profile.builtin_names:
                return external("builtins." + ".".join(chain), EXT_BUILTIN)
            return unknown(f"name '{self.plain(chain[0])}' is not defined anywhere ripple can see")
        where, binding = found
        if binding.kind == "import" and self.profile.package_imports:
            # import a.b binds a; resolve the whole a.b.c path at once so
            # namespace packages (no __init__.py) still work.
            return self.resolve_dotted(".".join([binding.target] + chain[1:]))
        target = self.binding_target(where, binding, name=chain[0])
        for attr in chain[1:]:
            target = self.member(target, attr)
        return target

    def resolve_path(self, scope: Scope, path: list) -> Target:
        """Resolve an expression that goes through call results, like make().run or Foo().a.b()."""
        first = path.index("()")
        target = self.resolve_chain(scope, path[:first]) if first else unknown(COMPUTED_OTHER)
        for step in path[first:]:
            target = self.call_result(target) if step == "()" else self.member(target, step)
        return target

    def call_result(self, target: Target) -> Target:
        """What calling target gives: an instance of the class it builds, or of the class its
        return annotation names. Anything else is unknown, never a guess."""
        if target.kind in ("symbol", "bound"):
            if self.scopes[target.value].kind == "class":
                instance = Target("instance", target.value)
                # Foo() is exactly a Foo when nothing can make it a subclass: no override edges then.
                return replace(instance, exact=True) if self.builds_itself(target.value) else instance
            cls = self.return_type(target.value)
            if cls:
                return self.value_of_type(cls)
            if not self.keeps_result(target.value):
                return unknown(DECORATED_RESULT)
            if self.scopes[target.value].return_annotated:
                return unknown("the callee is the result of a call whose return annotation does not name a class ripple can follow")
            return unknown("the callee is the result of a call to a function with no return annotation")
        if target.kind == "external":
            if target.reason == EXT_BUILTIN and target.value in self.profile.builtin_types and target.value not in self.profile.not_built_as_instance:
                return external(target.value, EXT_BUILTIN_VALUE)
            if target.reason == EXT_IMPORT and self.stdlib_class(target.value):
                return Target("foreign", target.value, exact=True)
            return unknown("the callee is the result of a call into code outside the repo, not proven to build a class")
        if target.kind in ("instance", "foreign"):
            return unknown("calls an instance (its __call__ is not followed)")
        if target.kind == "module":
            return unknown("calls a module")
        return target  # unknown stays unknown, keeping the first reason

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

    def binding_target(self, where: Scope, binding: Binding, depth: int = 0, name: str = "") -> Target:
        if binding.kind == "def":
            return Target("symbol", binding.target)
        if binding.kind == "import":
            return self.resolve_dotted(binding.target)
        if binding.kind == "from":
            return self.module_member(binding.target, binding.name, depth + 1)
        if binding.kind == "self":
            cls = self.enclosing_class(where)
            return Target("instance", cls) if cls else unknown("self outside a class")
        # `let X = memo(A); X = B`: a name that is bound again is not the one thing it was first, here as for a call
        if self._tag_mode and name and getattr(where, "wrapped", None) and name in where.wrapped and name not in where.rebound:
            return self.wrapped_component(where, where.wrapped[name])
        held = self.function_held(where, binding, name)
        if held is not None:
            return held
        cls = self.var_type(binding)
        if cls is None:
            return unknown(self.why_unknown(binding.evidence))
        value = self.value_of_type(cls)
        if value.kind in ("instance", "foreign") and self.constructed(binding.evidence):
            value = replace(value, exact=True)
        return value

    def wrapped_component(self, where: Scope, held: tuple) -> Target:
        """What `const X = memo(f)` means as a JSX tag: f, when it is a function (or class) of the repo that ripple can name."""
        kind, chain, _ = held
        if kind == "lazy":
            return unknown("a lazy component is loaded when it first renders, so ripple cannot see which one")
        if kind != "wrap":
            return unknown("what memo or forwardRef is given is not a function ripple can see")
        found = self.resolve_chain(where, chain)
        if found.kind == "symbol" and self.scopes[found.value].kind in ("function", "class"):
            return found
        if found.kind == "unknown":
            return unknown(f"what memo or forwardRef is given cannot be resolved: {found.reason}")
        return unknown("what memo or forwardRef is given is not a function ripple can see")

    # -- functions held in names ---------------------------------------------
    #
    # `with_tax = partial(apply_tax, 10)` then `with_tax(x)` runs apply_tax. That is proven when
    # partial is functools.partial, every assignment to the name is such a call, and they all name
    # the same def of the repo (a function or a method, not a class). `handler = TABLE["k"]` is
    # proven the same way when TABLE is a table no code changes (see the tables section below).
    # Anything else is unresolved, with the reason.

    def function_held(self, where: Scope, binding: Binding, name: str) -> Target | None:
        """The def a variable holds, unknown (with the reason it is not proven) when it holds a
        partial or a table entry that is not proven, None when it is not about either."""
        if not name or not any((ref.kind == "call" and ref.arg) or ref.kind == "item" for ref in binding.evidence):
            return None
        key = id(binding.evidence)
        if key not in self._function_values:
            self._function_values[key] = unknown("a functools.partial that refers to itself")  # in progress: a cycle
            self._function_values[key] = self._held_value(where, binding, name)
        return self._function_values[key]

    def _held_value(self, where: Scope, binding: Binding, name: str) -> Target | None:
        found = [self.held_by(ref) for ref in binding.evidence]
        if all(target is None for target in found):
            return None
        if any(target is None for target in found):
            return unknown("a name assigned a function held in a partial or a table, and other values")
        if any(target.kind == "unknown" for target in found):
            return next(target for target in found if target.kind == "unknown")
        if any(target != found[0] for target in found):
            return unknown("a name assigned different functions")
        if where.kind == "module":
            problem = self.rebind_problem(where, name)
            if problem:
                return unknown(f"a module-level name that other code may rebind ('{name}'): {problem}")
        return found[0]

    def held_by(self, ref: TypeRef) -> Target | None:
        """The def this one assignment holds, unknown if it holds one that is not proven, None if it
        holds something else."""
        if ref.kind == "item":
            return self.item_of(ref)
        if self.is_partial(ref):
            return self.partial_of(ref)
        return None

    def is_partial(self, ref: TypeRef) -> bool:
        if ref.kind != "call":
            return False
        named = self.resolve_chain(self.scopes[ref.scope], ref.expr.split("."))
        return named.kind == "external" and named.reason == EXT_IMPORT and named.value in self.profile.partial_names

    def partial_of(self, ref: TypeRef) -> Target:
        """The def partial(f, ...) wraps, or unknown."""
        if not ref.arg:
            return unknown("a functools.partial whose function is not a plain name")
        wrapped = self.resolve_chain(self.scopes[ref.scope], ref.arg.split("."))
        if wrapped.kind in ("symbol", "bound") and self.scopes[wrapped.value].kind in ("function", "method"):
            return wrapped
        if wrapped.kind == "unknown":
            return unknown(f"a functools.partial of '{ref.arg}', which ripple cannot resolve")
        return unknown(f"a functools.partial of '{ref.arg}', which is not one function or method of the repo")

    # -- tables ----------------------------------------------------------------
    #
    # A module-level dict, list or tuple literal that holds plain function names, assigned once and
    # never changed or handed on by any code in the repo (Mentions.risky, matched on the name as text
    # in every module), is a fixed table: TABLE["k"](x) calls what the literal puts under "k", and
    # `for f in TABLE: f(x)` calls each of its entries. The slightest use ripple does not know is a
    # read (passed to a call, aliased, returned, added to, deleted from) leaves the call unresolved,
    # and the reason names the use.

    def table_call(self, scope: Scope, call: RawCall) -> Target:
        home = self.table_home(scope, call.table)
        if home is None:  # not a table ripple can read: the call is what it was before
            if call.form != "item":
                return self.resolve_chain(scope, call.chain)
            return self.resolve_path(scope, call.path) if call.path else unknown(call.why)
        module, name = home
        problem = self.table_unsafe(module, name)
        if problem:
            return unknown(problem)
        table = module.tables[name]
        if call.form == "item":
            return self.table_entry(module, name, table, call.key[0])
        found = self.lookup(scope, call.var)
        loop_only = (
            found is not None
            and found[0] is scope
            and call.var not in scope.globals
            and call.var not in scope.nonlocals
            and found[1].kind == "var"
            and len(found[1].evidence) == 1
            and found[1].evidence[0].kind == "unknown"
            and found[1].evidence[0].expr == LOOP_VARIABLE
        )
        if not loop_only:
            return unknown(f"'{call.var}' is assigned somewhere else besides the loop over {name}")
        if call.form == "iter" and table.kind == "dict":
            return unknown(f"a loop over the dict {name} gives its keys, not the functions in it")
        if call.form in ("values", "items") and table.kind == "seq":
            return unknown(f"{name} is a list or tuple, which has no .{call.form}()")
        entries = list(table.entries.values()) if table.kind == "dict" else table.entries
        members = []
        for entry in entries:
            target = self.table_function(module, name, entry)
            if target.kind == "unknown":
                return target
            if target.value not in members:
                members.append(target.value)
        if not members:
            return unknown(f"{name} is empty")
        return Target("symbol", members[0]) if len(members) == 1 else Target("each", members=tuple(members))

    def item_of(self, ref: TypeRef) -> Target | None:
        """The def `x = TABLE[key]` holds: None when TABLE is not a table ripple can read."""
        home = self.table_home(self.scopes[ref.scope], ref.expr.split("."))
        if home is None:
            return None
        module, name = home
        problem = self.table_unsafe(module, name)
        if problem:
            return unknown(problem)
        return self.table_entry(module, name, module.tables[name], ref.key[0])

    def table_entry(self, module: Scope, name: str, table: Table, key) -> Target:
        """What TABLE[key] is, when the literal puts one plain function there."""
        if table.kind == "dict":
            if key not in table.entries:
                return unknown(f"{name} has no key {key!r}")
            entry = table.entries[key]
        else:
            if type(key) is not int or not -len(table.entries) <= key < len(table.entries):
                return unknown(f"{name} has no index {key!r}")
            entry = table.entries[key]
        return self.table_function(module, name, entry)

    def table_function(self, module: Scope, name: str, entry: list | None) -> Target:
        if not entry:
            return unknown(f"{name} holds something other than a plain function name")
        found = self.resolve_chain(module, entry)
        if found.kind == "symbol" and self.scopes[found.value].kind in ("function", "method"):
            return found
        return unknown(f"{name} holds '{'.'.join(entry)}', which is not one function or method of the repo")

    def table_home(self, scope: Scope, chain: list) -> tuple[Scope, str] | None:
        """The module and name of the table literal a dotted name refers to, or None."""
        found = self.lookup(scope, chain[0])
        if found is None:
            return None
        where, binding = found
        if len(chain) == 1:
            if binding.kind == "var":
                return (where, chain[0]) if where.kind == "module" and chain[0] in where.tables else None
            if binding.kind == "from":
                return self.table_in(binding.target, binding.name, 0)
            return None
        if binding.kind == "import":
            head = self.resolve_dotted(".".join([binding.target] + chain[1:-1]))
        elif binding.kind == "from":
            head = self.module_member(binding.target, binding.name)
            for attr in chain[1:-1]:
                head = self.member(head, attr)
        else:
            return None
        return self.table_in(head.value, chain[-1], 0) if head.kind == "module" else None

    def table_in(self, module: str, name: str, depth: int) -> tuple[Scope, str] | None:
        """The table `name` is in a module, following `from x import name` re-exports."""
        if module not in self.modules or depth > MAX_IMPORT_HOPS:
            return None
        scope = self.scopes[module]
        binding = scope.names.get(name)
        if binding is None:
            return None
        if binding.kind == "var":
            return (scope, name) if name in scope.tables else None
        if binding.kind == "from":
            return self.table_in(binding.target, binding.name, depth + 1)
        return None

    def table_unsafe(self, module: Scope, name: str) -> str | None:
        """Why this table might change (the reason an unresolved call gives), or None when no code can."""
        key = (module.id, name)
        if key not in self._tables:
            self._tables[key] = self._table_unsafe(module, name)
        return self._tables[key]

    def _table_unsafe(self, module: Scope, name: str) -> str | None:
        if name in module.rebound:
            return f"{name} is assigned more than once"
        if self._unparsed:
            return f"{name} may be changed: {self._unparsed[0]} could not be parsed, so what it does to {name} is not known"
        problem = self.store_problem()  # before the scan below, which parses every module
        if problem:
            return f"{name} may be changed: {problem}"
        self.scan_mentions()
        if self._unreadable:
            return f"{name} may be changed: {self._unreadable} could not be read again to check"
        names = self.aliases_of(name)
        for word in sorted(names):
            if word in self._risky:
                file, line = self._risky[word]
                return f"{name} may be changed or handed on: '{word}' is used other than to read it, at {file}:{line}"
        problem = self.rebind_problem(module, name)
        return f"{name} may be rebound: {problem}" if problem else None

    def scan_mentions(self) -> None:
        """Read every module's Mentions (once): what is used other than to read. Only a table needs this."""
        if self._risky is None:
            self._risky = {}
            for other in self.scopes.values():
                if other.kind == "module":
                    mentions = self.mentions(other)
                    if not mentions.readable and self._unreadable is None:
                        self._unreadable = other.file
                    for word, line in mentions.risky.items():
                        self._risky.setdefault(word, (other.file, line))

    def store_problem(self) -> str | None:
        """Why some code in the repo may store a value under a name in any module, or None when no file
        has a store token (see indexer.STORE_TOKENS). One answer for the whole repo, asked once: ripple
        does not try to tell which module a `setattr` lands on, because it cannot do that soundly."""
        if self._store_reason is False:
            asked = getattr(self._mentions_of, "store_token", None)
            if asked is None:
                self._store_reason = "ripple cannot read the repo's files again to check what they store"
            else:
                found = asked([s.file for s in self.scopes.values() if s.kind == "module"])
                self._store_reason = f"{found[0]} at {found[1]}:{found[2]} may store a value on any module" if found else None
        return self._store_reason

    def mentions(self, module: Scope):
        """What the module could change unseen (read from its file the first time it is asked)."""
        if self._mentions_of is None:
            return Mentions(readable=False)
        return self._mentions_of(module)

    def aliases_of(self, name: str) -> set[str]:
        """The name and every name it is imported under (`from m import T as U`), through any chain."""
        if name not in self._aliases:
            if self._alias_pairs is None:
                self._alias_pairs = [pair for scope in self.scopes.values() if scope.kind == "module" for pair in scope.aliases]
            pairs = self._alias_pairs
            names = {name}
            grew = True
            while grew:
                grew = False
                for original, alias in pairs:
                    if original in names and alias not in names:
                        names.add(alias)
                        grew = True
            self._aliases[name] = names
        return self._aliases[name]

    def rebind_problem(self, module: Scope, name: str) -> str | None:
        """Why something in the repo may rebind a module's name without the module's own assignments
        showing it, or None when nothing can: no attribute or item store of that name anywhere, no star
        import that could define it, and no store token in the repo (see store_problem)."""
        if self._unparsed:
            return f"{self._unparsed[0]} could not be parsed, so what it does to {name} is not known"
        problem = self.store_problem()
        if problem:
            return problem
        if self._rebound_names is None:
            self._rebound_names = {n for s in self.scopes.values() if s.kind == "module" for n in s.rebinds}
        if name in self._rebound_names:
            return "another module assigns the name"
        for star in module.star_imports:
            if star not in self.modules:
                return "its module has a star import that could define it"
            found = self.module_member(star, name, 1)
            if found.kind != "unknown" or "cycle" in found.reason:
                return "its module has a star import that could define it"
        return None

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
        Bases outside the repo are trusted not to. A class decorator returns what the name is
        bound to afterwards, so one that may replace the class (see class_replaced) spoils the
        proof too, on the class and on every base: `class Foo(Base)` subclasses what Base became.
        """
        if self.unresolved_base(cls):
            return False
        return not any(
            self.profile.allocator_name in self.scopes[klass].names
            or self.scopes[klass].redirects_construction
            or self.class_replaced(klass)
            for klass in self.mro(cls)
        )

    def class_replaced(self, cls: str) -> bool:
        """Whether a decorator on the class may bind its name to something else than the class.

        Only a decorator from outside the repo that is known to return the class it is given
        (profile.same_class_decorators) is trusted. A decorator that is a repo function or class may
        return a subclass or a wrapper, and so may one ripple cannot resolve.
        """
        if cls not in self._replaced:
            self._replaced[cls] = True  # in progress: a decorator that depends on the class itself is not trusted
            scope = self.scopes[cls]
            self._replaced[cls] = any(not self.keeps_class(scope, call) for call in scope.decorators)
        return self._replaced[cls]

    def keeps_class(self, scope: Scope, call: RawCall) -> bool:
        """Whether this decorator line hands the class back as it was."""
        if call.special not in ("", "decorator_result") or not call.chain:
            return False
        named = self.resolve_chain(self.scopes[scope.parent], call.chain)
        if named.kind != "external" or named.reason != EXT_IMPORT:
            return False
        if call.special == "decorator_result":
            return named.value in self.profile.same_class_decorators
        return named.value in self.profile.same_class_decorators or named.value in self.profile.same_class_plain_decorators

    def value_of_type(self, cls: str | None) -> Target:
        """A value whose class is cls: a repo class, a builtin such as dict, a class from
        outside the repo, or unknown."""
        if cls is None:
            return unknown("the receiver's class is not known statically")
        if cls in self.profile.builtin_types:
            return external(cls, EXT_BUILTIN_VALUE)
        if cls.startswith(EXTERNAL):
            return Target("foreign", cls[len(EXTERNAL) :])
        return Target("instance", cls)

    # -- classes outside the repo ------------------------------------------

    def stdlib_class(self, dotted: str) -> bool:
        """Whether a dotted name is a plain class of the standard library.

        Asked of the interpreter ripple runs on, for modules on profile.safe_import_modules only.
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

    def _ask_stdlib(self, dotted: str):
        parts = dotted.split(".")
        for i in range(len(parts) - 1, 0, -1):
            module = ".".join(parts[:i])
            if module in self.profile.safe_import_modules:
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
        return dotted in self._base_proven and not dotted.startswith(self.profile.nominal_only_excluded)

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

    def in_stdlib(self, dotted: str) -> bool:
        return dotted.split(".")[0] in self.profile.stdlib_module_names

    # -- modules -----------------------------------------------------------

    def resolve_dotted(self, path: str) -> Target:
        """Resolve pkg.mod.Class.method: longest module prefix, then members.

        Under ES modules (package_imports False) the path is one module id, which is a file path and
        may hold dots (tests/ui.test), so it is looked up whole and never cut at a dot."""
        if not self.profile.package_imports:
            return Target("module", path) if path in self.modules else external(path, EXT_IMPORT)
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
        if not self.profile.package_imports:
            return self.exported_member(module, name, depth)
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
            return self.binding_target(scope, binding, depth, name)
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

    def exported_member(self, module: str, name: str, depth: int = 0) -> Target:
        """What `import { name } from module` gives you under ES modules, following re-exports.

        Only what the module exports is there (ExportingModule.exports): a name it declares and keeps
        to itself is not importable, and a submodule is never an attribute. `export *` forwards every
        name but the default one. A package from outside the repo has whatever name you ask it for.
        """
        full = f"{module}.{name}"
        if depth > MAX_IMPORT_HOPS:
            return unknown(f"import cycle while resolving {full}")
        if module not in self.modules:
            return external(full, EXT_IMPORT)
        scope = self.scopes[module]
        binding = getattr(scope, "exports", {}).get(name)
        if binding is not None:
            return self.binding_target(scope, binding, depth, name)
        if name != "default":
            # A name two `export *` provide is exported by neither (the compiler reports it as ambiguous and
            # a native import fails to link), and a package's star may provide any name: the repo's own
            # answer stands only when it is the one answer.
            found_here: list[Target] = []
            from_package = False
            for star in scope.star_imports:
                if star not in self.modules:
                    from_package = True
                    continue
                found = self.exported_member(star, name, depth + 1)
                if found.kind != "unknown" and found not in found_here:
                    found_here.append(found)
            if len(found_here) == 1 and not from_package:
                return found_here[0]
            if found_here:
                return unknown(f"'{name}' is exported by more than one `export *` of module {module}, so it is ambiguous")
            if from_package:
                return external(full, EXT_IMPORT)
        return unknown(f"'{name}' is not exported by module {module}")

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
                if self.return_type(method):
                    return self.value_of_type(self.return_type(method))
                if not self.keeps_result(method):
                    return unknown(f"property {method} has a decorator that may change what it gives, so its annotation is not trusted")
                return unknown(f"property {method} has no return annotation naming a class")
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
        if not self.profile.has_universal_member(name) and not self.has_external_base(cls):
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
                if all(part.isidentifier() for part in self.plain(base).split(".")):
                    target = self.resolve_chain(parent, parts)
                else:
                    target = unknown("the base is computed")  # e.g. with_metaclass(Meta)
                if target.kind == "symbol" and self.scopes[target.value].kind == "class":
                    found.append(target.value)
                elif target.kind == "external":
                    if base != self.profile.implicit_base:
                        self._external_bases.setdefault(cls, []).append(target.value)
                else:
                    self._unresolved_bases.setdefault(cls, self.plain(base))
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
        if ref.kind == "item":
            return ASSIGNED_UNTYPED
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
            if not self.keeps_result(named.value):
                return DECORATED_RESULT_RECEIVER
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
        if ref.kind in ("unknown", "item"):
            return None
        scope = self.scopes[ref.scope]
        if ref.kind == "name":
            found = self.lookup(scope, ref.expr)
            if found is None:
                return None
            target = self.binding_target(found[0], found[1])
            if target.kind == "instance" or (target.kind == "external" and target.value in self.profile.builtin_types):
                return target.value
            if target.kind == "foreign":
                return EXTERNAL + target.value
            return None
        # "annotation" or "call": the expression must name a class, or (for a
        # call) a repo function whose return annotation names one.
        named = self.resolve_chain(scope, ref.expr.split("."))
        if named.kind == "external":
            if named.value in self.profile.builtin_types:
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
        if scope.returns_ambiguous or not self.keeps_result(function):
            return None
        if not self.profile.package_imports and scope.returns in self.profile.builtin_types:
            # a front end writes "builtins.Array" for a type the language names itself (T[], string): the
            # builtin, whatever the scope binds to the names Array or String
            return scope.returns
        return self.type_of(TypeRef("annotation", scope.returns, scope.parent)) if scope.returns else None

    def constructor(self, cls: str) -> str:
        """Calling a class runs the first constructor (__init__ in Python) in its bases, else the class itself."""
        return self.class_member(cls, self.profile.constructor_name) or cls
