"""The per-file facts that a language front end hands to the resolver.

A front end reads one file at a time and writes down what each scope defines,
imports and calls: it never decides what a call points at. These dataclasses
are that record, and they hold no syntax tree and no reference to the language
they came from, so the resolver (resolver.py) can work on facts from any front
end. indexer.py is the Python one.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Everything defined here. indexer.py re-exports each name, so what used to import from ripple.indexer still does.
__all__ = [
    "TypeRef",
    "Binding",
    "RawCall",
    "Scope",
    "Table",
    "Mentions",
    "ExportingModule",
    "CallbackCall",
    "unknown_ref",
    "COMPUTED_SUBSCRIPT",
    "COMPUTED_OTHER",
    "UNANNOTATED",
    "ANNOTATION_NO_CLASS",
    "VARIADIC",
    "LOOP_VARIABLE",
    "WITH_TARGET",
    "CAUGHT",
    "COMPREHENSION_VARIABLE",
    "UNPACKED",
    "PATTERN",
    "LAMBDA_PARAMETER",
    "ASSIGNED_UNTYPED",
    "ASSIGNED_COMPUTED_CALL",
    "REBOUND",
]

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
    function a decorator call decorates. kind "decoration" (TypeScript) is a decorator written on a class, member or
    parameter, and is never a call: chain is its name (None when it is not a name), why says what is not followed,
    and the resolver makes it an unresolved call and, where the name resolves, a model.Decoration.

    Three more specials are for a language where reading or writing a property can run code.
    "access" (chain or path ends in the property read) and "assign" (the property written) are an
    edge only when the property is a getter (read) or setter (write) of a repo class, and nothing at
    all otherwise: an ordinary property is not a call and is not reported as an unresolved one.
    "callback" is a CallbackCall.
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


@dataclass
class ExportingModule(Scope):
    """A module scope of a language whose modules say what they export (ES modules).

    names holds what the module's own code can see, exports what an importer can: the exported name
    mapped to its binding. `export function f` puts f in both, `export { f as g }` and
    `export { x } from "./m"` only put g and x in exports. A Python module has no such list (every
    name is an attribute), so the Python front end writes a plain Scope and the resolver reads
    attributes from names. star_imports lists the modules `export *` re-exports.
    """

    exports: dict = field(default_factory=dict)


@dataclass
class CallbackCall(RawCall):
    """A function passed by name as the FIRST argument: chain is the dotted name that is passed.

    It is a call of that function only when host, the call it is passed to, proves to run it: a
    builtin method named in the profile's synchronous_callbacks (`[...].map(describe)`). Passed to
    anything else, the callee may call it, store it or ignore it, and no edge is made. method is the
    name of the host's method. special is "callback".
    """

    host: RawCall | None = None
    method: str = ""


COMPUTED_SUBSCRIPT = "the callee is looked up by subscript (a dict, list or registry)"
COMPUTED_OTHER = "the callee is computed at runtime (a lambda, a conditional expression or similar)"


@dataclass
class Table:
    """A module-level dict, list or tuple literal: what it holds, as dotted names.

    kind "dict": entries maps each constant key to the dotted name written as the value
    (a list of parts), or None when the value is anything else. kind "seq": entries is
    the list of dotted names, None where an element is anything else.
    """

    kind: str
    entries: object


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
