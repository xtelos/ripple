"""The TypeScript front end: read .ts, .tsx, .mts and .cts files and write the same facts indexer.py
writes for Python (facts.py), so the resolver can build the call graph from either.

Like the Python front end this is purely syntactic, one file at a time, and it never decides what a
call points at. It needs the repo in view for one thing only, which file an import names
(ts_modules.py), and it needs tree-sitter to read the syntax. tree-sitter is the optional extra
`pip install 'ripple-code[typescript]'` and is imported when the first TypeScript file is parsed, never
when ripple is imported: a missing parser raises TypeScriptNotInstalled, which says so.

Symbol ids follow bench/ts_tracer/README.md, because the ground truth is written in them:
  src/cart/totals                  a module (its path, an index file is its directory)
  src/cart/totals:sum              a function, or a const bound to an arrow or function expression
  src/cart/cart:Cart.add           a member; a getter and a setter of one name are one symbol
  src/cart/bulk:BulkCart           a class with no constructor: the class is its own constructor
  tests/a.test:suite > case        the callback of it() or test(), named by its describe and it titles
  src/m:f.P#2                      the second declaration of one name under one parent: a class P in each of two
                                   sibling blocks of f, a static and an instance member of one name (A.make,
                                   A.make#2). The first keeps the plain id; later ones count up in source order.
This `#N` suffix is the only one an id ever has. The private keys that bind a block-local name (`Parent#4`, see
FileBuilder._push) are internal to the facts: they are never a symbol id, an edge end, or text shown in a reason.
Every other function is anonymous: it is never a callee, and what it calls belongs to the named
function or module around it. A describe() callback is the module.

What a call can be resolved through is exactly what the source proves: a name declared or imported,
`this` and `super`, `new C()`, a parameter or variable annotated with a class, the return annotation
of a function, a namespace import. Interface-typed values, computed keys (`table[k]()`), methods chosen
by name, decorators, loop variables and values of an unannotated type stay unresolved. A function
passed by name is a call only to a builtin that runs it at once, and only as its first argument (`xs.map(f)`,
not the `init` of `xs.reduce(f, init)`). A field, a parameter property or `this.x = ...` is an own property
of the object: it hides a method of that name in a base class, and a call through it stays unresolved.

JSX (.tsx) makes two more kinds of edge, `renders` and `renderer`, written by this front end as JsxElement facts
(jsx_facts.py) and decided by jsx.py: `<Child />` written in a function is a fact of that function (an anonymous
function inside it counts as it, as for calls), carrying the tag as a reference, read with the same scoping as a
call's callee, and a Placement: where the element ends up in what React renders, read off the syntax around it. A
component also records the placements of its own `children`, its other props, and of the calls of function props.
A name that starts with a lowercase letter is a host element (<div />), never a component, and a member of `this` is
not followed.

A decorator (`@tag`, `@route("/x")`, on a class, a member or a parameter) is never an edge, because what it does to
what it decorates is not followed, and a member it decorates is not resolved. It is an unresolved call of the scope
that holds the class and, when its name is a name or a dotted name, a model.Decoration: the resolver says which
symbol the name resolves to, so a question about that symbol can list where it is applied. Calls in a decorator's
arguments are read, as calls of that same scope.

Not supported (the call is unresolved or the code is skipped, never guessed): what a decorator does, class fields that
hold an arrow function, class expressions, object literal methods, enums, namespaces, `require`, and a tsconfig
`extends` that names a package.
"""

from __future__ import annotations

import re
from contextlib import contextmanager
from pathlib import Path

from .facts import (
    ANNOTATION_NO_CLASS,
    ASSIGNED_COMPUTED_CALL,
    ASSIGNED_UNTYPED,
    CAUGHT,
    COMPUTED_OTHER,
    COMPUTED_SUBSCRIPT,
    LOOP_VARIABLE,
    UNANNOTATED,
    VARIADIC,
    Binding,
    CallbackCall,
    RawCall,
    Scope,
    TypeRef,
    unknown_ref,
)
from .jsx_facts import JsxElement, JsxModule, JsxScope, Placement
from .model import DECORATOR_SCOPE, UNREAD_CLASS, Graph
from .profile import TYPESCRIPT, LanguageProfile
from .ts_modules import ModuleMap, config_files  # noqa: F401 (config_files: the registry asks this module for it)

INSTALL_HINT = "pip install 'ripple-code[typescript]'"
REINSTALL = f"it needs tree-sitter>=0.25,<0.27 and tree-sitter-typescript>=0.23.2,<0.24, which {INSTALL_HINT} installs"
PACKAGE = "pkg:"  # what a module id never starts with: the target of an import from outside the repo

THIS_REBOUND = "the receiver is `this` inside a function expression, which binds its own `this`"
THIS_PLAIN = "the receiver is `this` in a function that is not a method, so its class is not known"
DECORATED = "the receiver is a member that a decorator may replace"
DECORATOR = "a decorator runs when the class is defined, and ripple does not follow what it does to the member"
BAD_IMPORT = "the receiver comes from an import that does not name a file ripple reads"
OWN_PROPERTY = "the receiver is a field or parameter property of the class, which hides what a base class defines"
ABSTRACT = "the receiver is an abstract member, which runs nothing and hides what a base class defines"
STATIC_AND_INSTANCE = "the receiver is a name that a static and an instance member of the class both declare"
TYPE_DECLARED = "the receiver is named like an interface, type, enum or namespace declared here, which ripple does not follow"
OWN_NAME = "the receiver is the name of a function expression, which is the function itself"
DESTRUCTURED = "the receiver is destructured from an object or array, whose type ripple does not follow"
CALLBACK_PARAMETER = "the receiver is a parameter of an anonymous function, whose type ripple does not infer"
THIS_TAG = "the tag is a member of `this`, which ripple does not follow for components"
COMPUTED_TAG = "the tag is not a name or a dotted name"


class TypeScriptNotInstalled(ImportError):
    """tree-sitter or its TypeScript grammar is missing. The message says what to install."""


_PARSERS: tuple | None = None


def load_parsers() -> tuple:
    """(the TypeScript parser, the TSX parser). tree-sitter is imported here, the first time it is needed."""
    global _PARSERS
    if _PARSERS is None:
        try:
            import tree_sitter_typescript as grammar
            from tree_sitter import Language, Parser
        except ModuleNotFoundError as error:
            raise TypeScriptNotInstalled(
                f"TypeScript support is not installed (missing module: {error.name}); install it with: {INSTALL_HINT}"
            ) from error
        except ImportError as error:
            raise TypeScriptNotInstalled(
                f"TypeScript support cannot be loaded (a tree-sitter that does not fit: {error}); {REINSTALL}"
            ) from error
        try:
            _PARSERS = (Parser(Language(grammar.language_typescript())), Parser(Language(grammar.language_tsx())))
        except Exception as error:  # noqa: BLE001 (an ABI mismatch is a ValueError, an old API a TypeError, a bad library an OSError)
            raise TypeScriptNotInstalled(
                f"TypeScript support cannot be loaded (tree-sitter and its grammar do not fit: "
                f"{type(error).__name__}: {error}); {REINSTALL}"
            ) from error
    return _PARSERS


# ---- small readers of tree-sitter nodes -------------------------------------------------------


def text(node) -> str:
    return node.text.decode("utf-8", "replace")


def line_of(node) -> int:
    return node.start_point[0] + 1


def short(node) -> str:
    return " ".join(text(node).split())[:80]


def callee_text(callee) -> str:
    """A callee as written, kept short but always ending in its own last name (`xs.a().b` is `... .b`), since a
    question matches a call by the name it ends with."""
    if callee.type == "member_expression":
        prop = field_of(callee, "property")
        obj = field_of(callee, "object")
        if prop is not None and obj is not None:
            return f"{short(obj)[:60].replace(' .', '.')}.{text(prop)}"
    return short(callee)


def field_of(node, name: str):
    return node.child_by_field_name(name)


def named(node) -> list:
    """The named children that are not comments."""
    return [c for c in node.named_children if c.type != "comment"]


def inner(node):
    """The expression inside parentheses, a non-null assertion or `satisfies`, however deep; else the node."""
    while node.type in ("parenthesized_expression", "non_null_expression", "satisfies_expression"):
        kids = named(node)
        if not kids:
            return node
        node = kids[0]
    return node


def field_nodes(node, name: str) -> list:
    """Every child the field holds (a switch case has one `body` field per statement)."""
    return [child for i, child in enumerate(node.children) if child.is_named and node.field_name_for_child(i) == name]


def member_name(member) -> str:
    name = field_of(member, "name")
    return text(name) if name is not None else ""


def string_value(node) -> str | None:
    """The text of a string literal or of a template with no substitution, else None."""
    if node.type == "string":
        return "".join(text(c) for c in node.children if c.type in ("string_fragment", "escape_sequence"))
    if node.type == "template_string" and not any(c.type == "template_substitution" for c in node.children):
        return "".join(text(c) for c in node.children if c.type in ("string_fragment", "escape_sequence"))
    return None


def dotted(node) -> list | None:
    """["a", "b", "c"] for the expression a.b.c, and ["this", "x"] for this.x. None for anything else."""
    parts = []
    while True:
        node = inner(node)
        if node.type == "member_expression":
            prop = field_of(node, "property")
            if prop is None or prop.type not in ("property_identifier", "private_property_identifier"):
                return None
            parts.append(text(prop))
            node = field_of(node, "object")
        elif node.type == "identifier":
            parts.append(text(node))
            break
        elif node.type == "this":
            parts.append("this")
            break
        else:
            return None
    return parts[::-1]


def call_path(node) -> list | None:
    """["make", "()", "run"] for make().run, ["Foo", "()", "run"] for new Foo().run. None unless the expression
    is a name followed by properties and calls only, with at least one call."""
    parts = []
    while True:
        node = inner(node)
        if node.type == "member_expression":
            prop = field_of(node, "property")
            if prop is None or prop.type not in ("property_identifier", "private_property_identifier"):
                return None
            parts.append(text(prop))
            node = field_of(node, "object")
        elif node.type == "call_expression":
            parts.append("()")
            node = field_of(node, "function")
        elif node.type == "new_expression":
            parts.append("()")
            node = field_of(node, "constructor")
        elif node.type in ("identifier", "this"):
            parts.append(text(node))
            break
        else:
            return None
    parts.reverse()
    return parts if "()" in parts else None


LITERALS = {
    "array": "Array",
    "string": "String",
    "template_string": "String",
    "number": "Number",
    "regex": "RegExp",
    "true": "Boolean",
    "false": "Boolean",
}


def literal_type(node) -> str | None:
    """The builtin type a literal evaluates to ("Array" for [1, 2]), else None.

    An object literal is not here on purpose: its properties are whatever the code put in them,
    so a method called on one is not a method of a builtin."""
    return LITERALS.get(inner(node).type)


INTRINSIC = "builtins."
PREDEFINED = {
    "string": INTRINSIC + "String",
    "number": INTRINSIC + "Number",
    "boolean": INTRINSIC + "Boolean",
    "symbol": INTRINSIC + "Symbol",
    "bigint": INTRINSIC + "BigInt",
}


def annotation_type(node) -> str | None:
    """The class a type annotation names, or None when it names no single class.

    Unwraps `T | null`, `T | undefined`, `readonly T[]`, parentheses, and the type arguments of
    `Promise<T>` or `Box<T>` (the value is a Promise or a Box). T[] and tuples are an Array.
    Interfaces, aliases, unions of classes, function and object types name no class here: an
    interface is a name ripple cannot resolve to a class, which is what leaves its calls unresolved.

    A name is returned as written, to be looked up where the annotation is. A type the language
    names itself (`string`, `T[]`, a tuple, a literal type) is returned as "builtins.String",
    "builtins.Array" and so on: those are never looked up, so a class of the repo that happens to be
    called String or Array cannot stand in for them.
    """
    if node is None:
        return None
    t = node.type
    if t == "type_annotation":
        kids = named(node)
        return annotation_type(kids[0]) if kids else None
    if t == "predefined_type":
        return PREDEFINED.get(text(node))
    if t == "type_identifier":
        return text(node)
    if t == "nested_type_identifier":
        return text(node).replace(" ", "")
    if t == "generic_type":
        return annotation_type(field_of(node, "name"))
    if t in ("array_type", "tuple_type"):
        return INTRINSIC + "Array"
    if t in ("parenthesized_type", "readonly_type"):
        kids = named(node)
        return annotation_type(kids[0]) if kids else None
    if t == "literal_type":
        kids = named(node)
        kind = kids[0].type if kids else ""
        found = {"string": "String", "number": "Number", "true": "Boolean", "false": "Boolean"}.get(kind)
        return INTRINSIC + found if found else None
    if t == "union_type":
        members = []
        for member in named(node):
            if member.type == "literal_type" and text(member) in ("null", "undefined"):
                continue
            if member.type == "predefined_type" and text(member) in ("undefined", "void", "never"):
                continue
            members.append(annotation_type(member))
        if members and None not in members and len(set(members)) == 1:
            return members[0]
        return None
    return None


def parameter_type_members(type_node) -> dict:
    """{property name: type annotation node} of an inline object type: the `{ item: Item }` in
    `({ item }: { item: Item }) => ...`."""
    members = {}
    if type_node is None:
        return members
    annotated = named(type_node)[0] if type_node.type == "type_annotation" and named(type_node) else type_node
    if annotated.type != "object_type":
        return members
    for prop in named(annotated):
        if prop.type == "property_signature":
            name, kind = field_of(prop, "name"), field_of(prop, "type")
            if name is not None and kind is not None:
                members[text(name)] = kind
    return members


TEST_BLOCKS = {"describe", "suite", "xdescribe", "fdescribe"}
TEST_CASES = {"it", "test", "xit", "fit"}
TEST_MODIFIERS = {"only", "skip", "todo", "concurrent", "sequential"}
FUNCTION_VALUES = ("arrow_function", "function_expression", "function", "generator_function")
FUNCTION_DECLARATIONS = ("function_declaration", "generator_function_declaration")
TYPE_DECLARATIONS = ("interface_declaration", "type_alias_declaration", "enum_declaration", "internal_module")
FUNCTION_NODES = FUNCTION_VALUES + FUNCTION_DECLARATIONS + ("method_definition",)
SKIPPED = {
    "interface_declaration",
    "type_alias_declaration",
    "type_annotation",
    "type_arguments",
    "type_parameters",
    "ambient_declaration",
    "comment",
    "string",
    "number",
    "regex",
    "identifier",
    "property_identifier",
}
WHERE_NOT_FOLLOWED = {
    "arguments": "it is passed to a function, which may do anything with it",
    "variable_declarator": "it is assigned to a variable",
    "assignment_expression": "it is assigned",
    "array": "it is an item of an array",
    "pair": "it is the value of a property of an object",
    "expression_statement": "it is a statement of its own",
    "await_expression": "it is awaited",
    "sequence_expression": "it is part of a sequence of expressions",
    "template_substitution": "it is part of a string",
    "spread_element": "it is spread",
    "yield_expression": "it is yielded",
}
RETURNING_CALLBACKS = {"map", "flatMap"}  # the callback's result is an item of the array the call returns
JSX_PRAGMA = re.compile(r"@(jsx|jsxImportSource|jsxFrag|jsxRuntime)[ \t]+([^\s*]+)")
REACT_PRAGMAS = {"jsx": "React.createElement", "jsxImportSource": "react", "jsxFrag": "React.Fragment"}
MUTATORS = (["Object", "assign"], ["Object", "defineProperty"], ["Object", "defineProperties"], ["Object", "setPrototypeOf"])
HOST_TAG = re.compile(r"[a-z]")  # a JSX tag that starts with a lowercase letter is a host element, whatever it is called
ACCESSOR_NAME = re.compile(r"\b(?:get|set)\s+#?([A-Za-z_$][\w$]*)\s*[(<]")


class FileBuilder:
    """Reads one parsed file and writes the Scope records of its module and everything inside."""

    def __init__(self, module: str, file: str, root, modules: ModuleMap, accessors: set, profile: LanguageProfile):
        self.module = JsxModule(module, "module", module, file, 1, max(root.end_point[0] + 1, 1), None)
        self.scopes: dict[str, Scope] = {module: self.module}
        self.stack: list[Scope] = [self.module]
        self.modules = modules
        self.accessors = accessors
        self.profile = profile
        self.file = file
        self.damaged = root.has_error  # tree-sitter recovered from a syntax error: where an element is placed is not trusted
        self.runtime = self._runtime_problem(root)  # why this file's JSX is not React's, "" when it is
        self.titles: list[str] = []  # the describe() titles whose callbacks are being read
        self.rebinds_this = 0  # inside a function expression, which has a `this` of its own
        self.exports: list[tuple[str, str]] = []  # (exported name, local name), settled when the file is read
        self.not_known: dict[str, str] = {}  # specifier -> why an import of it is not known to be a package or a file
        self._methods: dict = {}
        self.frames: list[dict[str, str]] = []  # the names each enclosing block or function declares (see _push)
        self.counter = 0
        self.statics: dict[str, bool] = {}  # scope id -> whether it is a static member
        self.own_accessors: dict[str, set] = {}  # class id -> the names it declares with get or set
        self.fn_nodes: dict[str, int] = {}  # scope id -> the tree-sitter id of the function node it is
        self.call_nodes: dict[int, RawCall] = {}  # tree-sitter id of a call expression -> the call recorded for it
        self.jsx_nodes: dict[int, JsxElement] = {}  # tree-sitter id of a component element -> its fact
        self.props: dict[str, tuple[dict, str | None]] = {}  # scope id -> ({local name: prop key}, name of the props object)
        for node in named(root):
            self.visit(node)
        for exported, local in self.exports:
            if local in self.module.names:
                self.module.exports[exported] = self.module.names[local]

    @property
    def scope(self) -> Scope:
        return self.stack[-1]

    # ---- the walk -------------------------------------------------------------------------

    def visit(self, node) -> None:
        t = node.type
        if t in SKIPPED:
            return
        method = self._methods.get(t)
        if method is None:
            method = getattr(self, "visit_" + t, self.generic)
            self._methods[t] = method
        method(node)

    def generic(self, node) -> None:
        for child in node.named_children:
            self.visit(child)

    # ---- scopes ---------------------------------------------------------------------------
    #
    # A Scope is a named function, a class, a method or the module: the calls a name makes belong to it and
    # a name is looked up through it. A block, a catch clause, a for loop head and the parameters of an
    # anonymous function are scopes of names only (TypeScript: `const`, `let`, `class` and `function`
    # declarations are block scoped, and an arrow function's parameters are its own). They are not Scopes:
    # their calls belong to the function around them. Instead each is a frame of names, and a name declared
    # in a nested frame is bound under a private key (`handle#3`) in the function that holds it, and every
    # reference written inside that frame uses the key. A reference outside it still means the name it
    # meant before, so a block-local `handle` is never what a call after the block reaches.

    def _fresh(self, name: str) -> str:
        self.counter += 1
        return f"{name}#{self.counter}"

    def _push(self, names, fresh: bool) -> None:
        """Open a frame for the names a block, a function or a clause declares."""
        self.frames.append({n: (self._fresh(n) if fresh else n) for n in dict.fromkeys(names)})

    def _pop(self) -> None:
        self.frames.pop()

    def _name(self, name: str) -> str:
        """The key a name written here is bound under: the innermost frame that declares it, else itself."""
        for frame in reversed(self.frames):
            if name in frame:
                return frame[name]
        return name

    @contextmanager
    def _outside(self):
        """Read names as written outside the innermost frame: a parameter's annotation is looked up where the
        function is, so a parameter that shares the name of a class does not stand in for it."""
        frame = self.frames.pop()
        try:
            yield
        finally:
            self.frames.append(frame)

    def _local(self, name: str) -> bool:
        """Whether some function or block around here declares the name (so it is not the module's, or a global)."""
        return any(name in frame for frame in self.frames)

    def _ref(self, parts: list) -> list:
        """A dotted reference as the resolver should read it: its first name, under the key it is bound by."""
        return [self._name(parts[0]), *parts[1:]] if parts else parts

    def _ref_text(self, dotted_name: str) -> str:
        head, dot, rest = dotted_name.partition(".")
        return self._name(head) + dot + rest

    def _declared_names(self, nodes) -> list[str]:
        """The names the statements declare directly (not in a nested block or function) with let, const,
        class or function: what a block of them makes its own, before any of them runs."""
        names: list[str] = []
        for node in nodes:
            if node.type == "export_statement":
                node = field_of(node, "declaration") or node
            t = node.type
            if t == "lexical_declaration":
                for declarator in named(node):
                    target = field_of(declarator, "name") if declarator.type == "variable_declarator" else None
                    if target is not None:
                        names += self._pattern_names(target, visit_defaults=False)
            elif t in FUNCTION_DECLARATIONS or t in ("class_declaration", "abstract_class_declaration") or t in TYPE_DECLARATIONS:
                name = field_of(node, "name")
                if name is not None:
                    names.append(text(name))
        return names

    def _bind_type_names(self, nodes) -> None:
        """An interface, a type alias, an enum or a namespace declared in a block hides a class of that name from
        the code around it, so the name is bound here to something ripple does not follow."""
        for node in nodes:
            if node.type == "export_statement":
                node = field_of(node, "declaration") or node
            name = field_of(node, "name") if node.type in TYPE_DECLARATIONS else None
            if name is not None and name.type == "identifier":
                self._bind(text(name), [unknown_ref(TYPE_DECLARED)])

    def _var_names(self, node) -> list[str]:
        """The names `var` declares anywhere in a function body (it is function scoped), not inside a nested function."""
        names: list[str] = []
        stack = list(node.named_children)
        while stack:
            child = stack.pop()
            if child.type in FUNCTION_NODES or child.type in ("class_declaration", "abstract_class_declaration", "class"):
                continue
            if child.type == "variable_declaration":
                for declarator in named(child):
                    target = field_of(declarator, "name") if declarator.type == "variable_declarator" else None
                    if target is not None:
                        names += self._pattern_names(target, visit_defaults=False)
            elif child.type == "for_in_statement" and field_of(child, "kind") is not None and text(field_of(child, "kind")) == "var":
                left = field_of(child, "left")
                if left is not None:
                    names += self._pattern_names(left, visit_defaults=False)
            stack.extend(child.named_children)
        return names

    def _param_names(self, node) -> list[str]:
        single = field_of(node, "parameter")
        if single is not None:
            return [text(single)]
        names: list[str] = []
        params = field_of(node, "parameters")
        for param in named(params) if params is not None else []:
            pattern = field_of(param, "pattern")
            if param.type in ("required_parameter", "optional_parameter") and pattern is not None and pattern.type != "this":
                names += self._pattern_names(pattern, visit_defaults=False)
        return names

    def _own_name(self, node) -> str | None:
        """The name of a function expression, which only its own body can see."""
        if node.type in ("function_expression", "function", "generator_function"):
            name = field_of(node, "name")
            return text(name) if name is not None else None
        return None

    def _push_function(self, node, fresh: bool) -> None:
        """The frame of a function: its own name if it is an expression, its parameters, the names its body
        declares, and its `var`s."""
        body = field_of(node, "body")
        own = self._own_name(node)
        names = ([own] if own else []) + self._param_names(node)
        if body is not None and body.type == "statement_block":
            names += self._declared_names(named(body)) + self._var_names(body)
        self._push(names, fresh)
        if body is not None and body.type == "statement_block":
            self._bind_type_names(named(body))

    def _child_id(self, parent: Scope, name: str) -> str:
        return f"{parent.id}{':' if parent.kind == 'module' else '.'}{name}"

    def _enter(
        self,
        name: str,
        kind: str,
        node,
        parent: Scope | None = None,
        scope_id: str | None = None,
        variable: bool = True,
        static: bool = False,
    ) -> Scope:
        """The scope of a declaration. A name declared again in the same frame (an overload's body, a getter and
        its setter) is the same symbol. A name declared in another block, or a static and an instance member of one
        name, is another declaration and gets an id of its own (`m.f`, then `m.f#2`)."""
        parent = parent or self.scope
        bound = self._name(name) if variable else name
        if scope_id is not None:  # a test callback, named by its titles
            existing, sid = self.scopes.get(scope_id), scope_id
        else:
            sid = self._child_id(parent, name)
            binding = parent.names.get(bound)
            existing = None
            if binding is not None and binding.kind == "def" and self.scopes.get(binding.target) is not None:
                candidate = self.scopes[binding.target]
                if candidate.parent == parent.id and self.statics.get(candidate.id, False) == static:
                    existing = candidate
            n = 1
            while existing is None and sid in self.scopes:
                n += 1
                sid = f"{self._child_id(parent, name)}#{n}"
        if existing is not None:
            return existing
        scope = JsxScope(sid, kind, parent.module, parent.file, line_of(node), node.end_point[0] + 1, parent.id)
        self.scopes[sid] = scope
        self.statics[sid] = static
        if scope_id is None:
            previous = parent.names.get(bound)
            parent.bind(bound, Binding("def", sid))
            if not variable and previous is not None and previous.kind == "def" and previous.target != sid:
                # a static and an instance member share the name: which one a call reaches is not known
                parent.names[bound] = Binding("var", evidence=[unknown_ref(STATIC_AND_INSTANCE)])
        return scope

    def _bind(self, name: str, evidence: list) -> None:
        self.scope.bind(self._name(name), Binding("var", evidence=list(evidence)))

    def _assign_name(self, name: str, evidence: list) -> None:
        """`x = value` where x was declared outside this function is the same variable, not a new local."""
        key = self._name(name)
        for scope in reversed(self.stack):
            if scope.kind != "class" and key in scope.names:
                scope.bind(key, Binding("var", evidence=list(evidence)))
                return
        self.scope.bind(key, Binding("var", evidence=list(evidence)))

    # ---- types ----------------------------------------------------------------------------

    def _annotation_evidence(self, type_node, scope_id: str) -> list:
        """Evidence from a written type: the class it names, or why it names none."""
        hint = annotation_type(type_node)
        if hint:
            return [self._hint_ref(hint, scope_id)]
        return [unknown_ref(UNANNOTATED if type_node is None else ANNOTATION_NO_CLASS)]

    def _hint_ref(self, hint: str, scope_id: str) -> TypeRef:
        """The evidence a class name from annotation_type is: a builtin as it is, any other name looked up
        where the annotation is written."""
        if hint.startswith(INTRINSIC):
            return TypeRef("builtin", hint[len(INTRINSIC) :], scope_id)
        return TypeRef("annotation", self._ref_text(hint), scope_id)

    def _hint_name(self, hint: str | None) -> str | None:
        """A class name from annotation_type as a Scope.returns string (the resolver looks it up in the scope
        that holds the def, and reads a builtins. name as the builtin)."""
        if hint is None or hint.startswith(INTRINSIC):
            return hint
        return self._ref_text(hint)

    def _evidence(self, value) -> list:
        """What an assigned value tells about its class, as TypeRefs. null and undefined add none."""
        here = self.scope.id
        if value is None:
            return []
        value = inner(value)
        if value.type in ("null", "undefined") or (value.type == "identifier" and text(value) == "undefined"):
            return []
        kind = literal_type(value)
        if kind:
            return [TypeRef("builtin", kind, here)]
        if value.type == "new_expression":
            chain = dotted(field_of(value, "constructor"))
            if chain and self.rebinds_this and chain[0] == "this":
                chain = None
            return [TypeRef("call", ".".join(self._ref(chain)), here)] if chain else [unknown_ref(ASSIGNED_COMPUTED_CALL)]
        if value.type == "call_expression":
            chain = dotted(field_of(value, "function"))
            if chain and self.rebinds_this and chain[0] == "this":
                chain = None
            return [TypeRef("call", ".".join(self._ref(chain)), here)] if chain else [unknown_ref(ASSIGNED_COMPUTED_CALL)]
        if value.type == "identifier":
            return [TypeRef("name", self._name(text(value)), here)]
        if value.type == "this" and not self.rebinds_this:
            return [TypeRef("name", "this", here)]
        return [unknown_ref(ASSIGNED_UNTYPED)]

    # ---- imports and exports --------------------------------------------------------------

    def _target(self, source_node) -> tuple[str | None, str]:
        """(module id or PACKAGE+specifier or None when the file does not exist, the specifier)."""
        spec = string_value(source_node) or ""
        kind, value = self.modules.resolve(spec, self.file)
        if kind == "repo":
            return value, spec
        if kind == "unresolved":
            self.not_known[spec] = value
        return (PACKAGE + spec if kind == "package" else None), spec

    def _bad_import(self, spec: str) -> TypeRef:
        """Why a name from an import that names no file is unknown: with the reason when the import is an alias that no
        config covering this file gives it (so it is not claimed to be a package)."""
        why = self.not_known.get(spec)
        return unknown_ref(f"{BAD_IMPORT}: {why}" if why else BAD_IMPORT)

    def _imported(self, local: str, target: str | None, member: str | None, spec: str) -> None:
        """Bind an imported name in the module: a member of target, or the whole module when member is None."""
        if target is None:
            self.module.bind(local, Binding("var", evidence=[self._bad_import(spec)]))
        elif member is None:
            self.module.bind(local, Binding("import", target))
        else:
            self.module.bind(local, Binding("from", target, member))

    def visit_import_statement(self, node) -> None:
        source = field_of(node, "source")
        clause = next((c for c in node.named_children if c.type == "import_clause"), None)
        if source is None or clause is None:
            return  # `import "./side-effect"` binds nothing
        target, spec = self._target(source)
        for part in named(clause):
            if part.type == "identifier":  # import def from "m"
                self._imported(text(part), target, "default", spec)
            elif part.type == "namespace_import":  # import * as ns from "m"
                ns = next((c for c in named(part) if c.type == "identifier"), None)
                if ns is not None:
                    self._imported(text(ns), target, None, spec)
            elif part.type == "named_imports":  # import { a, b as c, type D } from "m"
                for item in named(part):
                    if item.type != "import_specifier":
                        continue
                    name, alias = field_of(item, "name"), field_of(item, "alias")
                    if name is not None:
                        self._imported(text(alias or name), target, text(name), spec)

    def visit_export_statement(self, node) -> None:
        source = field_of(node, "source")
        kids = node.children
        is_type = any(c.type == "type" for c in kids)
        for child in named(node):
            if child.type == "decorator":
                self._decorator(child)
        is_default = any(c.type == "default" for c in kids)
        declaration = field_of(node, "declaration")
        if declaration is not None:
            declared = self._declare(declaration)
            if is_default:  # export default function label() {}: the name stays local, `default` is what is exported
                if declared:
                    self.exports.append(("default", declared[0]))
                else:
                    self.module.exports["default"] = Binding("var", evidence=[unknown_ref(ASSIGNED_UNTYPED)])
            elif not is_type:
                self.exports.extend((name, name) for name in declared)
            return
        if is_default:
            value = field_of(node, "value")
            if value is None:
                return
            value = inner(value)
            if value.type == "identifier":
                self.exports.append(("default", text(value)))
            elif value.type in ("function_expression", "function", "generator_function", "class", "arrow_function"):
                self.visit(value)
                self.module.exports["default"] = Binding("var", evidence=[unknown_ref(ASSIGNED_UNTYPED)])
            else:
                self.visit(value)
                self.module.exports["default"] = Binding("var", evidence=self._evidence(value))
            return
        clause = next((c for c in named(node) if c.type == "export_clause"), None)
        namespace = next((c for c in named(node) if c.type == "namespace_export"), None)
        if is_type:
            return  # `export type { T }` adds no value
        if source is not None:
            target, spec = self._target(source)
            if clause is not None:  # export { a, b as c } from "m"
                for item in named(clause):
                    name, alias = field_of(item, "name"), field_of(item, "alias")
                    if item.type != "export_specifier" or name is None or any(c.type == "type" for c in item.children):
                        continue
                    exported = text(alias or name)
                    if target is None:
                        self.module.exports[exported] = Binding("var", evidence=[self._bad_import(spec)])
                    else:
                        self.module.exports[exported] = Binding("from", target, text(name))
            elif namespace is not None:  # export * as ns from "m"
                ns = next((c for c in named(namespace) if c.type == "identifier"), None)
                if ns is not None:
                    bound = Binding("var", evidence=[self._bad_import(spec)]) if target is None else Binding("import", target)
                    self.module.exports[text(ns)] = bound
            elif target is not None:  # export * from "m"
                self.module.star_imports.append(target)
        elif clause is not None:  # export { a, b as c }
            for item in named(clause):
                name, alias = field_of(item, "name"), field_of(item, "alias")
                if item.type == "export_specifier" and name is not None and not any(c.type == "type" for c in item.children):
                    self.exports.append((text(alias or name), text(name)))

    def _declare(self, node) -> list[str]:
        """Visit a declaration and return the names it declares (what `export` exports)."""
        t = node.type
        if t in FUNCTION_DECLARATIONS:
            name = field_of(node, "name")
            self.visit(node)
            return [text(name)] if name is not None else []
        if t in ("class_declaration", "abstract_class_declaration"):
            name = field_of(node, "name")
            self.visit(node)
            return [text(name)] if name is not None else []
        if t in ("lexical_declaration", "variable_declaration"):
            names = []
            for declarator in named(node):
                if declarator.type == "variable_declarator":
                    target = field_of(declarator, "name")
                    if target is not None and target.type == "identifier":
                        names.append(text(target))
                    elif target is not None:
                        names += self._pattern_names(target)
            self.visit(node)
            return names
        self.visit(node)
        return []

    # ---- functions ------------------------------------------------------------------------

    def _function(self, node, name: str, kind: str = "function", accessor: str = "", static: bool = False) -> Scope:
        """A named function, method or accessor: its scope, its parameters, its body."""
        parent = self.scope
        scope = self._enter(name, kind, node, variable=kind != "method", static=static)
        self.fn_nodes[scope.id] = node.id
        self.props[scope.id] = self._prop_names(node)
        hint = self._hint_name(annotation_type(field_of(node, "return_type")))
        annotated = field_of(node, "return_type") is not None
        # The same bookkeeping Python's def does: a getter is a def that is a property, a setter is a def that is
        # not a plain one, and two defs that return different classes make what a call returns ambiguous.
        if accessor != "set":
            if annotated:
                scope.return_sigs.add(hint)
            scope.plain_defs += 1
            if not annotated:
                scope.bare_defs += 1
        if len(scope.return_sigs) > 1 or (scope.plain_defs > 1 and scope.bare_defs):
            scope.returns_ambiguous = True
        scope.returns = scope.returns or hint
        scope.return_annotated = scope.return_annotated or annotated
        scope.is_property = scope.is_property or accessor == "get"
        scope.defs += 1
        if scope.defs == 1:
            scope.is_async = any(c.type == "async" for c in node.children)
            scope.is_generator = node.type in ("generator_function_declaration", "generator_function") or any(
                c.type == "*" for c in node.children
            )
        if kind == "method":
            scope.bind("this", Binding("def", parent.id) if static else Binding("self"))
        elif node.type not in ("arrow_function",):
            scope.bind("this", Binding("var", evidence=[unknown_ref(THIS_PLAIN)]))
        self.stack.append(scope)
        self._push_function(node, fresh=False)
        own = self._own_name(node)
        if own:  # const f = function g() { g() }: g is the function itself, whatever the scope around calls g
            scope.bind(own, Binding("var", evidence=[unknown_ref(OWN_NAME)]))
        names = self._parameters(node, scope, parent.id)
        if scope.defs == 1:
            scope.params = names
        self._body(field_of(node, "body"))
        self._drop_mutated_props(node, scope)
        self._pop()
        self.stack.pop()
        return scope

    def _body(self, body) -> None:
        if body is None:
            return
        if body.type == "statement_block":
            for stmt in named(body):
                self.visit(stmt)
        else:
            self.visit(body)
            self._place_props(body)

    def _parameters(self, node, scope: Scope, defining: str) -> list[str]:
        """Bind the parameters in scope, visit their defaults there (a default runs in the callee), and
        return the positional names. Annotations are resolved where the function is defined."""
        names = []
        single = field_of(node, "parameter")  # x => ...
        if single is not None:
            scope.bind(text(single), Binding("var", evidence=[unknown_ref(UNANNOTATED)]))
            return [text(single)]
        params = field_of(node, "parameters")
        if params is None:
            return names
        owner = self.scopes.get(scope.parent) if scope.parent else None
        for param in named(params):
            if param.type not in ("required_parameter", "optional_parameter"):
                continue
            pattern, type_node, default = field_of(param, "pattern"), field_of(param, "type"), field_of(param, "value")
            if pattern is None or pattern.type == "this":
                continue
            if default is not None:
                self.visit(default)
            if pattern.type == "identifier":
                name = text(pattern)
                names.append(name)
                with self._outside():
                    evidence = self._annotation_evidence(type_node, defining)
                scope.bind(name, Binding("var", evidence=evidence))
                is_property = any(c.type in ("accessibility_modifier", "readonly", "override") for c in param.children)
                if is_property and owner is not None and owner.kind == "class" and scope.id == self._child_id(owner, "constructor"):
                    owner.attr_types.setdefault(name, []).extend(evidence)  # constructor(private repo: Repo)
                    self._own_property(owner, name)
            elif pattern.type == "rest_pattern":
                for name in self._pattern_names(pattern):
                    scope.bind(name, Binding("var", evidence=[unknown_ref(VARIADIC)]))
            else:
                self._bind_pattern(pattern, scope, defining, type_node)
        return names

    def _bind_pattern(self, pattern, scope: Scope, defining: str, type_node=None) -> None:
        """Bind the names of a destructuring parameter. A name typed by an inline `{ item: Item }` annotation
        is that class; every other is unpacked from something ripple cannot type."""
        members = parameter_type_members(type_node) if pattern.type == "object_pattern" else {}
        for child in named(pattern):
            if child.type == "shorthand_property_identifier_pattern":
                key = name = text(child)
            elif child.type == "pair_pattern":
                key_node, value_node = field_of(child, "key"), field_of(child, "value")
                if key_node is None or value_node is None or value_node.type != "identifier":
                    for name in self._pattern_names(child):
                        scope.bind(name, Binding("var", evidence=[unknown_ref(DESTRUCTURED)]))
                    continue
                key, name = text(key_node), text(value_node)
            else:
                for name in self._pattern_names(child):
                    scope.bind(name, Binding("var", evidence=[unknown_ref(DESTRUCTURED)]))
                continue
            if key in members and annotation_type(members[key]):
                with self._outside():
                    ref = self._hint_ref(annotation_type(members[key]), defining)
                scope.bind(name, Binding("var", evidence=[ref]))
            else:
                scope.bind(name, Binding("var", evidence=[unknown_ref(DESTRUCTURED)]))

    def _pattern_names(self, node, visit_defaults: bool = True) -> list[str]:
        """The names a destructuring pattern binds. Defaults inside it (`{ a = f() }`) are expressions: visited."""
        t = node.type
        if t in ("identifier", "shorthand_property_identifier_pattern"):
            return [text(node)]
        names = []
        if t in ("assignment_pattern", "object_assignment_pattern"):
            left, right = field_of(node, "left"), field_of(node, "right")
            if right is not None and visit_defaults:
                self.visit(right)
            return self._pattern_names(left, visit_defaults) if left is not None else []
        if t == "pair_pattern":
            value = field_of(node, "value")
            return self._pattern_names(value, visit_defaults) if value is not None else []
        for child in named(node):
            names += self._pattern_names(child, visit_defaults)
        return names

    def visit_function_declaration(self, node) -> None:
        name = field_of(node, "name")
        if name is None:
            self._anonymous(node)
        else:
            self._function(node, text(name))

    visit_generator_function_declaration = visit_function_declaration

    def _anonymous(self, node) -> None:
        """A function with no name of its own: its calls belong to the scope around it, and its parameters and
        the names its body declares are a frame of their own (see _push), not names of that scope."""
        arrow = node.type == "arrow_function"
        self._push_function(node, fresh=True)
        own = self._own_name(node)
        if own:
            self._bind(own, [unknown_ref(OWN_NAME)])
        single = field_of(node, "parameter")
        if single is not None:
            self._bind(text(single), [unknown_ref(CALLBACK_PARAMETER)])
        params = field_of(node, "parameters")
        for param in named(params) if params is not None else []:
            if param.type not in ("required_parameter", "optional_parameter"):
                continue
            pattern, type_node, default = field_of(param, "pattern"), field_of(param, "type"), field_of(param, "value")
            if default is not None:
                self.visit(default)
            if pattern is None or pattern.type == "this":
                continue
            if pattern.type == "identifier":
                hint = annotation_type(type_node)
                with self._outside():
                    evidence = [self._hint_ref(hint, self.scope.id)] if hint else [unknown_ref(CALLBACK_PARAMETER)]
                self._bind(text(pattern), evidence)
            else:
                for name in self._pattern_names(pattern):
                    self._bind(name, [unknown_ref(DESTRUCTURED)])
        if not arrow:
            self.rebinds_this += 1
        self._body(field_of(node, "body"))
        if not arrow:
            self.rebinds_this -= 1
        self._pop()

    visit_arrow_function = _anonymous
    visit_function_expression = _anonymous
    visit_function = _anonymous
    visit_generator_function = _anonymous

    def visit_method_definition(self, node) -> None:
        """A method of an object literal (a class body is read by _class): anonymous."""
        self._anonymous(node)

    # ---- classes --------------------------------------------------------------------------

    def visit_class_declaration(self, node) -> None:
        name = field_of(node, "name")
        for child in node.children:
            if child.type == "decorator":
                self._decorator(child)
        if name is None:  # the grammar reads `export default class {}` as a class expression (visit_class), so this is a guard
            self._unread_class(node)
            return
        self._class(node, text(name))

    visit_abstract_class_declaration = visit_class_declaration

    def visit_class(self, node) -> None:
        """A class expression, and `export default class {}`: its members are not read, and nothing in it is guessed.
        A decorator written on it (`export default @d class {}`) is still a decoration."""
        for child in node.children:
            if child.type == "decorator":
                self._decorator(child)
        self._unread_class(node)

    def _unread_class(self, node) -> None:
        """The body of a class that is not read still calls things. Each call written in it (a call, a `new`, at any
        depth) is recorded as an unresolved call of the scope around the class, with the reason that names the skip,
        so a question about what it calls lists it as a call ripple could not follow. None becomes an edge."""
        body = field_of(node, "body")
        self._unread_calls(body, self.scope, UNREAD_CLASS)

    def _unread_calls(self, root, scope: Scope, why: str) -> None:
        """Every call and `new` written under `root`, at any depth, as an unresolved call of `scope` with this reason."""
        pending = [root] if root is not None else []
        found = []
        while pending:
            current = pending.pop()
            pending.extend(current.children)
            if current.type == "call_expression":
                callee = field_of(current, "function")
            elif current.type == "new_expression":
                callee = field_of(current, "constructor")
            else:
                continue
            if callee is not None and inner(callee).type not in ("super", "import"):
                found.append((line_of(current), inner(callee)))
        for line, callee in sorted(found, key=lambda f: f[0]):
            scope.calls.append(RawCall(None, callee_text(callee), line, special="computed", why=why))

    def _class(self, node, name: str) -> None:
        scope = self._enter(name, "class", node)
        scope.names["this"] = Binding("self")  # what a field initializer's `this` is
        scope.bases = []
        for child in node.children:
            if child.type == "decorator":
                scope.decorators.append(RawCall(None, short(child), line_of(child), special="computed", why=DECORATOR))
        heritage = next((c for c in node.children if c.type == "class_heritage"), None)
        extends = next((c for c in named(heritage) if c.type == "extends_clause"), None) if heritage is not None else None
        if extends is not None:
            value = field_of(extends, "value")
            if value is not None:
                base = dotted(value)
                scope.bases.append(".".join(self._ref(base)) if base else short(value))
                self.visit(value)
        body = field_of(node, "body")
        members = named(body) if body is not None else []
        constructor = next((m for m in members if m.type == "method_definition" and member_name(m) == "constructor"), None)
        self.own_accessors[scope.id] = {
            member_name(m)
            for m in members
            if m.type in ("method_definition", "abstract_method_signature") and {"get", "set"} & {c.type for c in m.children}
        }
        self.stack.append(scope)
        if constructor is not None:
            self._member(constructor, scope, [])
        else:
            # No constructor in source: the class is its own constructor, and forwards to its base's.
            scope.names[self.profile.constructor_name] = Binding("def", scope.id)
            if scope.bases:
                scope.calls.append(RawCall([self.profile.constructor_name], "super(...args)", line_of(node), special="super"))
        pending: list = []
        for member in members:
            if member is constructor:
                continue
            if member.type == "decorator":
                pending.append(member)
                continue
            self._member(member, scope, pending)
            pending = []
        self.stack.pop()

    def _member(self, member, cls: Scope, decorators: list) -> None:
        """One member of a class body."""
        t = member.type
        if t == "method_definition":
            name_node = field_of(member, "name")
            if name_node is None or name_node.type in ("computed_property_name",):
                return  # [Symbol.iterator]() and the like: no name to give it
            name = text(name_node)
            kids = {c.type for c in member.children}
            accessor = "get" if "get" in kids else "set" if "set" in kids else ""
            for d in [*decorators, *(c for c in member.children if c.type == "decorator")]:
                self._decorator(d, cls)
            self._parameter_decorators(member, cls)
            self._function(member, name, "method", accessor, static="static" in kids)
            if decorators or "decorator" in kids:
                cls.names[name] = Binding("var", evidence=[unknown_ref(DECORATED)])
        elif t == "abstract_method_signature":
            name_node = field_of(member, "name")
            if name_node is not None and name_node.type != "computed_property_name":
                cls.bind(text(name_node), Binding("var", evidence=[unknown_ref(ABSTRACT)]))
        elif t == "public_field_definition":
            self._field(member, cls, decorators)
        elif t == "class_static_block":
            self.rebinds_this += 1
            self.generic(member)
            self.rebinds_this -= 1

    def _own_property(self, cls: Scope, name: str) -> None:
        """A field, a static field, a parameter property or `this.x = ...` is the object's own property: a member
        lookup stops at it and never reaches a method of that name further up."""
        cls.bind(name, Binding("var", evidence=[unknown_ref(OWN_PROPERTY)]))

    def _field(self, member, cls: Scope, decorators: list) -> None:
        """A field. Its type is recorded for `this.field.method()`; its initializer runs when the class is
        constructed (or defined, for a static one), so its calls belong to the constructor or the scope around."""
        name_node, type_node, value = field_of(member, "name"), field_of(member, "type"), field_of(member, "value")
        if name_node is None or name_node.type == "computed_property_name":
            return
        name = text(name_node)
        static = any(c.type == "static" for c in member.children)
        self._own_property(cls, name)
        for d in [*decorators, *(c for c in member.children if c.type == "decorator")]:
            self._decorator(d, cls)
        if decorators or any(c.type == "decorator" for c in member.children):
            cls.attr_types.setdefault(name, []).append(unknown_ref(DECORATED))
        elif not static:
            if type_node is not None:
                cls.attr_types.setdefault(name, []).extend(self._annotation_evidence(type_node, cls.id))
            elif value is not None:
                self.stack.append(cls)
                cls.attr_types.setdefault(name, []).extend(self._evidence(value))
                self.stack.pop()
        if value is None:
            return
        if inner(value).type in FUNCTION_VALUES:
            return  # a field that holds a function: it is not read (see the module docstring)
        if static:  # runs where the class is defined: the scope that holds the class, not the class
            self.stack.pop()
            self.rebinds_this += 1
            self.visit(value)
            self.rebinds_this -= 1
            self.stack.append(cls)
            return
        target = self.scopes.get(self._child_id(cls, self.profile.constructor_name)) or cls
        self.stack.append(target)
        self.visit(value)
        self.stack.pop()

    # ---- decorators -----------------------------------------------------------------------

    def _decorator(self, node, owner: Scope | None = None) -> None:
        """A decorator is applied when the class is defined, by the scope that holds the class (never the
        class itself). Ripple does not follow what it does to what it decorates, so it is recorded as an
        unresolved call whatever it is; its name, when it is a name or a dotted name, goes with it so the
        resolver can say which symbol it names (a Decoration, never an edge). Its arguments are read.

        `owner` is the class a member decorator is written in (a decorator on a method, a field or a parameter): that
        one scope is the decorated class and is stepped out of. A decorator on a class itself has no owner: the scope
        on top of the stack is already the one that holds the class. If that scope is a class (the class is written
        in a field initializer, a static block or a method of an object literal, where a class stands in for the
        constructor or for the scope that defines it), which scope runs the arguments is not provable: their calls
        are unresolved, never an edge."""
        stepped_out = owner is not None and self.stack[-1] is owner
        if stepped_out:
            self.stack.pop()
        try:
            holder = self.stack[-1] if self.stack else self.module
            expression = next(iter(named(node)), None)
            callee = inner(field_of(expression, "function") or expression) if expression is not None and expression.type == "call_expression" else expression
            chain = dotted(callee) if callee is not None else None
            if chain and self.rebinds_this and chain[0] == "this":
                chain = None
            holder.calls.append(
                RawCall(self._ref(chain) if chain else None, short(node), line_of(node), kind="decoration", why=DECORATOR)
            )
            provable = holder.kind != "class" and (owner is None or stepped_out)
            for child in named(node):
                if child.type == "call_expression":
                    args = field_of(child, "arguments")
                    if args is None:
                        continue
                    if provable:
                        self.visit(args)
                    else:
                        self._unread_calls(args, holder, DECORATOR_SCOPE)
        finally:
            if stepped_out:
                self.stack.append(owner)

    def _parameter_decorators(self, node, cls: Scope) -> None:
        """The decorators written on the parameters of a method: applied with the class, by the scope that holds it
        (so this runs before the method's own scope is entered)."""
        params = field_of(node, "parameters")
        for param in named(params) if params is not None else []:
            for child in param.children:
                if child.type == "decorator":
                    self._decorator(child, cls)

    # ---- declarations of variables --------------------------------------------------------

    def visit_lexical_declaration(self, node) -> None:
        for declarator in named(node):
            if declarator.type == "variable_declarator":
                self._declarator(declarator)

    visit_variable_declaration = visit_lexical_declaration

    def _declarator(self, node) -> None:
        target, type_node, value = field_of(node, "name"), field_of(node, "type"), field_of(node, "value")
        if target is None:
            return
        if target.type == "identifier":
            name = text(target)
            if value is not None and inner(value).type in FUNCTION_VALUES:
                self._function(inner(value), name)  # const f = () => ...: the function is named f
                return
            if value is not None and inner(value).type == "call_expression" and self._wrapped_component(name, inner(value)):
                return
            if value is not None:
                self.visit(value)
            if type_node is not None:
                evidence = self._annotation_evidence(type_node, self.scope.id)
            else:
                evidence = self._evidence(value)
            self._bind(name, evidence)
        else:
            if value is not None:
                self.visit(value)
            for name in self._pattern_names(target):
                self._bind(name, [unknown_ref(DESTRUCTURED)])

    # ---- statements that bind a name ------------------------------------------------------

    def visit_statement_block(self, node) -> None:
        self._push(self._declared_names(named(node)), fresh=True)
        self._bind_type_names(named(node))
        self.generic(node)
        self._pop()

    def visit_switch_body(self, node) -> None:
        """Every case of a switch shares one block."""
        statements = [stmt for case in named(node) for stmt in field_nodes(case, "body")]
        self._push(self._declared_names(statements), fresh=True)
        self._bind_type_names(statements)
        self.generic(node)
        self._pop()

    def visit_for_statement(self, node) -> None:
        init = field_of(node, "initializer")
        self._push(self._declared_names([init]) if init is not None else [], fresh=True)
        self.generic(node)
        self._pop()

    def visit_for_in_statement(self, node) -> None:
        left, right, body = field_of(node, "left"), field_of(node, "right"), field_of(node, "body")
        kind = field_of(node, "kind")
        declares = kind is not None and text(kind) in ("let", "const")
        names = []
        if left is not None and declares:
            names = self._pattern_names(left, visit_defaults=False)
        if right is not None:
            self.visit(right)
        self._push(names, fresh=True)
        if left is not None:
            if left.type == "identifier":
                if declares:
                    self._bind(text(left), [unknown_ref(LOOP_VARIABLE)])
                else:
                    self._assign_name(text(left), [unknown_ref(LOOP_VARIABLE)])
            elif left.type in ("object_pattern", "array_pattern"):
                for name in self._pattern_names(left):
                    if declares:
                        self._bind(name, [unknown_ref(DESTRUCTURED)])
                    else:
                        self._assign_name(name, [unknown_ref(DESTRUCTURED)])
            else:
                self.visit(left)
        if body is not None:
            self.visit(body)
        self._pop()

    def visit_catch_clause(self, node) -> None:
        parameter = field_of(node, "parameter")
        names = []
        if parameter is not None:
            names = self._pattern_names(parameter, visit_defaults=False) if parameter.type != "identifier" else [text(parameter)]
        self._push(names, fresh=True)
        for name in names:
            self._bind(name, [unknown_ref(CAUGHT)])
        body = field_of(node, "body")
        if body is not None:
            self.visit(body)
        self._pop()

    # ---- assignments and property access --------------------------------------------------

    def _property_effect(self, node, special: str) -> None:
        """Record a property read or write that may run a getter or setter: only for names some class of the
        repo declares as an accessor, so a plain property costs nothing."""
        prop = field_of(node, "property")
        if prop is None or text(prop) not in self.accessors:
            return
        chain = dotted(node)
        if chain:
            if self.rebinds_this and chain[0] == "this":
                return
            self.scope.calls.append(RawCall(self._ref(chain), ".".join(chain), line_of(node), special=special))
            return
        path = call_path(node)
        if path:
            if self.rebinds_this and path[0] == "this":
                return
            self.scope.calls.append(RawCall(None, short(node), line_of(node), special=special, path=self._ref(path)))

    def visit_member_expression(self, node) -> None:
        """A property read. The object is read too (`a().b.c` calls a)."""
        self._property_effect(node, "access")
        obj = field_of(node, "object")
        if obj is not None:
            self.visit(obj)

    def visit_assignment_expression(self, node) -> None:
        left, right = field_of(node, "left"), field_of(node, "right")
        if right is not None:
            self.visit(right)
        evidence = self._evidence(right)
        self._assign_target(left, evidence, own=not self._rebinds_itself(left, right))

    def visit_augmented_assignment_expression(self, node) -> None:
        left, right = field_of(node, "left"), field_of(node, "right")
        if right is not None:
            self.visit(right)
        if left is not None and inner(left).type == "member_expression":
            self._property_effect(inner(left), "access")  # x.n += 1 reads, then writes
        self._assign_target(left, [])

    def visit_update_expression(self, node) -> None:
        argument = field_of(node, "argument")
        if argument is not None and inner(argument).type == "member_expression":
            self._property_effect(inner(argument), "access")
        self._assign_target(argument, [])

    def _rebinds_itself(self, left, right) -> bool:
        """`this.h = this.h.bind(this)`: the property is the method itself, bound, so it hides nothing."""
        if left is None or right is None:
            return False
        target, value = dotted(left), inner(right)
        if value.type != "call_expression" or not target or len(target) != 2 or target[0] != "this":
            return False
        callee, args = dotted(field_of(value, "function")), field_of(value, "arguments")
        arguments = [a.type for a in named(args)] if args is not None else []
        return callee == [*target, "bind"] and arguments == ["this"]

    def _assign_target(self, target, evidence: list, own: bool = True) -> None:
        if target is None:
            return
        target = inner(target)
        if target.type == "identifier":
            self._assign_name(text(target), evidence)
        elif target.type == "member_expression":
            self._property_effect(target, "assign")
            chain = dotted(target)
            if chain and len(chain) == 2 and chain[0] == "this" and not self.rebinds_this:
                self._record_attribute(chain[1], evidence, own)
            obj = field_of(target, "object")
            if obj is not None:
                self.visit(obj)
        elif target.type in ("object_pattern", "array_pattern"):
            for name in self._pattern_names(target):
                self._assign_name(name, [unknown_ref(DESTRUCTURED)])
        else:
            self.visit(target)

    def _record_attribute(self, attr: str, evidence: list, own: bool = True) -> None:
        """this.x = value inside a method: remember x's type on the class."""
        scope = self.scope
        binding = scope.names.get("this")
        if scope.kind != "method" or binding is None or binding.kind != "self":
            return
        cls = self.scopes[scope.parent]
        cls.attr_types.setdefault(attr, []).extend(evidence)
        if own and attr not in self.own_accessors.get(cls.id, ()):  # this.x = v calls the setter when x is an accessor
            self._own_property(cls, attr)

    # ---- calls ----------------------------------------------------------------------------

    def _test_call(self, node, func, args) -> bool:
        """describe("x", () => ...) and it("y", () => ...): the callback of describe is the module, the callback
        of it is a function named by the titles. True when the call was read as one."""
        base, modifier = func, None
        if func.type == "member_expression":
            base, prop = field_of(func, "object"), field_of(func, "property")
            modifier = text(prop) if prop is not None else None
            if modifier not in TEST_MODIFIERS:
                return False
        if base is None or base.type != "identifier" or args is None:
            return False
        name = text(base)
        if self._local(name):
            return False  # a parameter or a local that happens to be called it()
        binding = self.module.names.get(name)
        if binding is not None:
            if binding.kind != "from" or not binding.target.startswith(PACKAGE):
                return False  # a repo function that happens to be called it()
            name = binding.name  # import { it as test } from "vitest"
        if name not in TEST_BLOCKS | TEST_CASES:
            return False
        kids = named(args)
        title = string_value(kids[0]) if kids else None
        callback = next((k for k in kids[1:] if inner(k).type in FUNCTION_VALUES), None)
        if title is None or callback is None:
            return False
        callback = inner(callback)
        if name in TEST_BLOCKS:
            self.titles.append(title)
            self._anonymous_in_place(callback)
            self.titles.pop()
            return True
        sid = f"{self.module.id}:{' > '.join([*self.titles, title])}"
        parent = self.scope
        scope = self._enter(title, "function", callback, parent=parent, scope_id=sid)
        scope.defs += 1
        scope.plain_defs += 1
        scope.is_async = any(c.type == "async" for c in callback.children)
        scope.bind("this", Binding("var", evidence=[unknown_ref(THIS_PLAIN)]))
        self.stack.append(scope)
        for stray in kids[1:]:
            if stray is not callback:
                self.visit(stray)
        self._push_function(callback, fresh=False)
        self._parameters(callback, scope, parent.id)
        self._body(field_of(callback, "body"))
        self._pop()
        self.stack.pop()
        return True

    def _anonymous_in_place(self, callback) -> None:
        """A describe() callback: the module's own code, read where it is written."""
        self._push_function(callback, fresh=True)
        self._parameters_unknown(callback)
        self._body(field_of(callback, "body"))
        self._pop()

    def _parameters_unknown(self, callback) -> None:
        params = field_of(callback, "parameters")
        for param in named(params) if params is not None else []:
            pattern = field_of(param, "pattern")
            if pattern is not None and pattern.type == "identifier":
                self._bind(text(pattern), [unknown_ref(CALLBACK_PARAMETER)])

    def visit_call_expression(self, node) -> None:
        func = field_of(node, "function")
        args = field_of(node, "arguments")
        if func is None:
            self.generic(node)
            return
        func = inner(func)
        if self._test_call(node, func, args):
            return
        here = self.scope
        line = line_of(node)
        call = None
        if func.type == "super":
            call = RawCall([self.profile.constructor_name], "super(...)", line, special="super")
        elif func.type == "import":
            call = RawCall(None, "import(...)", line, special="computed", why=COMPUTED_OTHER)
        elif func.type == "member_expression" and inner(field_of(func, "object")).type == "super":
            prop = field_of(func, "property")
            call = RawCall([text(prop)], short(func), line, special="super") if prop is not None else None
        elif func.type == "member_expression" and literal_type(field_of(func, "object")):
            call = RawCall(None, short(func), line, special="builtin")
        else:
            chain = dotted(func)
            if chain and self.rebinds_this and chain[0] == "this":
                call = RawCall(None, short(func), line, special="computed", why=THIS_REBOUND)
            elif chain:
                call = RawCall(self._ref(chain), ".".join(chain)[:80], line)
            else:
                path = call_path(func)
                if path and self.rebinds_this and path[0] == "this":
                    call = RawCall(None, short(func), line, special="computed", why=THIS_REBOUND)
                elif path:
                    call = RawCall(None, short(func), line, path=self._ref(path))
                else:
                    why = COMPUTED_SUBSCRIPT if func.type == "subscript_expression" else COMPUTED_OTHER
                    call = RawCall(None, short(func), line, special="computed", why=why)
        if call is not None:
            here.calls.append(call)
            self.call_nodes[node.id] = call
            self._callbacks(call, func, args, here)
        self._visit_callee(func)
        if args is not None:
            self.generic(args)
        key = self._prop_key(func)
        if key is not None:  # render(row): the result of a function prop, wherever this component places it
            placement = self._placement(node)
            if placement.kind != "none":
                self.scope.prop_calls.setdefault(key, []).append(placement)

    def _visit_callee(self, func) -> None:
        """What a callee expression itself calls or reads: a.b().c reads a.b's call, never `c` as a property."""
        if func.type == "member_expression":
            obj = field_of(func, "object")
            if obj is not None:
                self.visit(obj)
        elif func.type == "subscript_expression":
            self.generic(func)
        elif func.type not in ("identifier", "this", "super", "import"):
            self.visit(func)

    def _callbacks(self, host: RawCall, func, args, scope: Scope) -> None:
        """A function passed by name to a builtin method that runs it: the host call says which method. Only the
        first argument is the callback (`xs.reduce(f, init)` and `xs.map(f, thisArg)` never run init or thisArg)."""
        if args is None or func.type != "member_expression":
            return
        prop = field_of(func, "property")
        method = text(prop) if prop is not None else ""
        if method not in self.profile.synchronous_callbacks:
            return
        for arg in named(args)[:1]:
            chain = dotted(arg)
            if chain and not (self.rebinds_this and chain[0] == "this"):
                scope.calls.append(
                    CallbackCall(self._ref(chain), ".".join(chain), line_of(arg), special="callback", host=host, method=method)
                )

    def visit_new_expression(self, node) -> None:
        constructor, args = field_of(node, "constructor"), field_of(node, "arguments")
        line = line_of(node)
        chain = dotted(constructor) if constructor is not None else None
        if chain and self.rebinds_this and chain[0] == "this":
            self.scope.calls.append(RawCall(None, short(constructor), line, special="computed", why=THIS_REBOUND))
        elif chain:
            self.scope.calls.append(RawCall(self._ref(chain), ".".join(chain)[:80], line))
        elif constructor is not None:
            path = call_path(constructor)
            if path and self.rebinds_this and path[0] == "this":
                self.scope.calls.append(RawCall(None, short(constructor), line, special="computed", why=THIS_REBOUND))
            elif path:
                self.scope.calls.append(RawCall(None, short(constructor), line, path=self._ref(path)))
            else:
                self.scope.calls.append(RawCall(None, short(constructor), line, special="computed", why=COMPUTED_OTHER))
        if constructor is not None and chain is None:
            self.visit(constructor)
        if args is not None:
            self.generic(args)

    # ---- JSX ------------------------------------------------------------------------------
    #
    # A component element is read as a JsxElement of the function around it, and what the front end writes
    # down is syntax only: the tag as a reference, and where the element is placed (see jsx_facts.Placement).
    # Nothing here says which component renders it: that needs the resolver (jsx.py).

    def visit_jsx_element(self, node) -> None:
        opening = field_of(node, "open_tag")
        if opening is None:
            return
        self._element(node, opening)
        self._attributes(opening)
        for child in named(node):
            if child.type not in ("jsx_opening_element", "jsx_closing_element"):
                self.visit(child)

    def visit_jsx_self_closing_element(self, node) -> None:
        self._element(node, node)
        self._attributes(node)

    def visit_jsx_expression(self, node) -> None:
        self.generic(node)
        for child in named(node):
            self._place_props(child)

    def visit_return_statement(self, node) -> None:
        self.generic(node)
        for child in named(node):
            self._place_props(child)

    def _attributes(self, opening) -> None:
        """What an element's attributes read and call; the tag name and its type arguments are not expressions."""
        name = field_of(opening, "name")
        for child in named(opening):
            if (name is not None and child.id == name.id) or child.type == "type_arguments":
                continue
            if child.type == "jsx_attribute":
                self._handed(child)
            self.visit(child)

    def _handed(self, attribute) -> None:
        """`render={Home}`: a name handed on as a prop. Whatever receives it may call it as a function."""
        kids = named(attribute)
        value = kids[-1] if len(kids) > 1 else None
        if value is not None and value.type == "jsx_expression" and named(value):
            chain = dotted(named(value)[0])
            if chain and chain[0] != "this":
                self.scope.handed.append(self._ref(chain))

    def _transparent(self, name) -> bool:
        """A tag whose element renders its children where it stands: a host element, or React's Fragment and the like."""
        if self._host(name):
            return True
        chain = dotted(name) if name.type in ("identifier", "member_expression") else None
        return self._react_name(chain) in self.profile.fragment_components

    def _host(self, name) -> bool:
        """A tag that is a host element: <div />, <my-element />, <svg:rect />."""
        return name.type == "jsx_namespace_name" or (name.type == "identifier" and HOST_TAG.match(text(name)) is not None)

    def _element(self, node, opening) -> None:
        name = field_of(opening, "name")
        if name is None or self._transparent(name):
            return  # a fragment, or a host element: it is never a component
        why = ""
        chain = None
        if name.type in ("identifier", "member_expression"):
            parts = dotted(name)
            if parts is None:
                why = COMPUTED_TAG
            elif parts[0] == "this":
                why = THIS_TAG
            else:
                chain = parts
        else:
            why = COMPUTED_TAG
        written = ".".join(chain) if chain else short(name)
        element = JsxElement(self._ref(chain) if chain else None, written, line_of(node), why=why)
        self.jsx_nodes[node.id] = element
        element.placement = self._placement(node)
        self.scope.jsx.append(element)

    # -- where a value is placed

    def _none(self, why: str) -> Placement:
        return Placement("none", why=why)

    def _placement(self, start) -> Placement:
        """Where `start` (an element, or `children`, or a call of a function prop) ends up, by walking out of it through
        what keeps it in the tree of the component that holds it. See jsx_facts.Placement for the places it can end."""
        found = self._walk(start)
        found.runtime = self.runtime
        return found

    def _runtime_problem(self, root) -> str:
        """Why the JSX of this file is not compiled to React's calls (a pragma or the tsconfig names another factory or
        import source), else "". Under another runtime a component may run when its element is made, not when React
        renders it, so nothing that depends on React's order can be proven."""
        if b"@jsx" in root.text:
            for kind, value in JSX_PRAGMA.findall(text(root)):
                if kind in REACT_PRAGMAS and value != REACT_PRAGMAS[kind]:
                    return f"the file says @{kind} {value}, not React's runtime, so when a component runs is not known"
        return self.modules.config.jsx_runtime

    def _walk(self, start) -> Placement:
        if self.damaged:
            return self._none("the file has a syntax error that the parser recovered from, so the tree around it is not trusted")
        node, hosts = start, []
        while True:
            parent = node.parent
            if parent is None:
                return self._none("it is not inside a function")
            t = parent.type
            if t in ("parenthesized_expression", "non_null_expression", "satisfies_expression", "as_expression"):
                node = parent
            elif t == "binary_expression":
                right, operator = field_of(parent, "right"), field_of(parent, "operator")
                if right is None or right.id != node.id or operator is None or text(operator) not in ("&&", "||", "??"):
                    return self._none("it is an operand of an operator that does not hand it on")
                node = parent
            elif t == "ternary_expression":
                branches = {c.id for c in (field_of(parent, "consequence"), field_of(parent, "alternative")) if c is not None}
                if node.id not in branches:
                    return self._none("it is the condition of a conditional expression")
                node = parent
            elif t == "jsx_element" or (t == "jsx_expression" and parent.parent is not None and parent.parent.type == "jsx_element"):
                element = parent if t == "jsx_element" else parent.parent
                outcome = self._child_of(element, hosts)
                if isinstance(outcome, Placement):
                    return outcome
                node = outcome
            elif t in ("jsx_attribute",) or (t == "jsx_expression" and parent.parent is not None and parent.parent.type == "jsx_attribute"):
                return self._attribute(parent if t == "jsx_attribute" else parent.parent, False, hosts)
            elif t == "jsx_expression":
                return self._none("it is spread into the props of an element")
            elif t == "return_statement" or (t == "arrow_function" and self._is_body(parent, node)):
                function = parent
                while function is not None and function.type not in FUNCTION_NODES:
                    function = function.parent
                outcome = self._boundary(function, hosts)
                if isinstance(outcome, Placement):
                    return outcome
                node = outcome
            else:
                found = self._none(WHERE_NOT_FOLLOWED.get(t) or f"it is in {t.replace('_', ' ')}, where ripple does not follow it")
                if t == "arguments" and parent.parent is not None and parent.parent.type == "call_expression":
                    func = field_of(parent.parent, "function")
                    found.call = self._react_name(dotted(func) if func is not None else None) or ""
                return found

    def _is_body(self, function, node) -> bool:
        body = field_of(function, "body")
        return body is not None and body.id == node.id

    def _child_of(self, element, hosts: list):
        """The element a value is a child of: a Placement when that is a component, else the node to go on from."""
        opening = field_of(element, "open_tag")
        name = field_of(opening, "name") if opening is not None else None
        if name is None or self._transparent(name):
            return element  # a fragment or a host element holds it without being a component
        fact = self.jsx_nodes.get(element.id)
        if fact is None:
            return self._none("it is a child of a tag ripple did not read")
        return Placement("child", element=fact, hosts=hosts)

    def _attribute(self, attribute, called: bool, hosts: list) -> Placement:
        element = attribute.parent
        if element is not None and element.type == "jsx_opening_element":
            element = element.parent
        fact = self.jsx_nodes.get(element.id) if element is not None else None
        key = attribute.named_children[0] if attribute.named_children else None
        if fact is None:
            return self._none("it is a prop of a host element")
        if key is None or key.type != "property_identifier":
            return self._none("it is a prop with a name ripple does not read")
        return Placement("attr", element=fact, key=text(key), called=called, hosts=hosts)

    def _boundary(self, function, hosts: list):
        """What the value a function returns is: the scope's own return, the result of a function prop (a render prop),
        or the item of an array that `.map` builds (the node of that call, to go on from)."""
        if function is None:
            return self._none("it is not inside a function")
        if self.fn_nodes.get(self.scope.id) == function.id:
            return Placement("scope", scope=self.scope.id, hosts=hosts)
        holder = function.parent
        if holder is not None and holder.type == "jsx_expression" and holder.parent is not None and holder.parent.type == "jsx_attribute":
            return self._attribute(holder.parent, True, hosts)
        if holder is not None and holder.type == "arguments" and holder.parent is not None and holder.parent.type == "call_expression":
            call, args = holder.parent, named(holder)
            callee = inner(field_of(call, "function")) if field_of(call, "function") is not None else None
            prop = field_of(callee, "property") if callee is not None and callee.type == "member_expression" else None
            if args and args[0].id == function.id and prop is not None and text(prop) in RETURNING_CALLBACKS and call.id in self.call_nodes:
                hosts.append(self.call_nodes[call.id])
                return call
        return self._none("it is returned by a function that is not what the component returns")

    # -- what a component does with its props

    def _prop_names(self, node) -> tuple[dict, str | None]:
        """({local name: prop key}, name of the props object) of the first parameter: `({ children, render: draw })` or `props`."""
        single = field_of(node, "parameter")
        if single is not None:
            return {}, text(single)
        params = field_of(node, "parameters")
        first = next((p for p in named(params) if p.type in ("required_parameter", "optional_parameter")), None) if params is not None else None
        pattern = field_of(first, "pattern") if first is not None else None
        if pattern is None:
            return {}, None
        if pattern.type == "identifier":
            return {}, text(pattern)
        names: dict[str, str] = {}
        if pattern.type == "object_pattern":
            for child in named(pattern):
                if child.type == "shorthand_property_identifier_pattern":
                    names[text(child)] = text(child)
                elif child.type == "object_assignment_pattern":
                    left = field_of(child, "left")
                    if left is not None and left.type == "shorthand_property_identifier_pattern":
                        names[text(left)] = text(left)
                elif child.type == "pair_pattern":
                    key, value = field_of(child, "key"), field_of(child, "value")
                    if value is not None and value.type == "assignment_pattern":
                        value = field_of(value, "left")
                    if key is not None and key.type == "property_identifier" and value is not None and value.type == "identifier":
                        names[text(value)] = text(key)
        return names, None

    def _prop_key(self, expression) -> str | None:
        """The prop an expression reads: `children`, or `props.children`, in the function that receives them. A name that
        something nearer declares, or that the function assigns again, is not the prop."""
        props = self.props.get(self.scope.id)
        if props is None:
            return None
        names, holder = props
        expression = inner(expression)
        if expression.type == "identifier":
            name = text(expression)
            if name in names and self._name(name) == name:
                return names[name]
        elif expression.type == "member_expression" and holder is not None:
            owner, prop = inner(field_of(expression, "object")), field_of(expression, "property")
            if (
                owner.type == "identifier"
                and text(owner) == holder
                and self._name(holder) == holder
                and prop is not None
                and prop.type == "property_identifier"
            ):
                return text(prop)
        return None

    def _drop_mutated_props(self, node, scope) -> None:
        """Once the whole function is read: a prop that is assigned anywhere in it (a loop, a function declared further down,
        `children = x`, `props = {...props}`, `props.children = x`, `delete props.x`, `Object.assign(props, x)`) is not the
        value the caller passed where the function runs first, so nothing it places is proven. Position does not matter."""
        if not scope.slots and not scope.prop_calls:
            return
        names, holder = self.props.get(scope.id, ({}, None))
        hit = self._mutated(node, set(names) | ({holder} if holder else set()), holder)
        if holder in hit:
            scope.slots.clear()
            scope.prop_calls.clear()
        for local in hit:
            scope.slots.pop(names.get(local), None)
            scope.prop_calls.pop(names.get(local), None)

    def _mutated(self, node, watched: set, holder) -> set:
        hit: set = set()
        stack = [node]
        while stack:
            n = stack.pop()
            t = n.type
            if t in ("assignment_expression", "augmented_assignment_expression"):
                hit |= self._written(field_of(n, "left"), watched, holder)
            elif t == "update_expression":
                hit |= self._written(field_of(n, "argument"), watched, holder)
            elif t == "variable_declaration":  # `var props = ...` redeclares the parameter, hoisted
                for declarator in named(n):
                    target = field_of(declarator, "name") if declarator.type == "variable_declarator" else None
                    if target is not None:
                        hit |= set(self._pattern_names(target, visit_defaults=False)) & watched
            elif t in FUNCTION_DECLARATIONS and field_of(n, "name") is not None:  # `function children() {}` replaces it too
                hit |= {text(field_of(n, "name"))} & watched
            elif t == "unary_expression" and field_of(n, "operator") is not None and text(field_of(n, "operator")) == "delete":
                hit |= self._written(field_of(n, "argument"), watched, holder)
            elif t == "for_in_statement" and field_of(n, "kind") is None:
                hit |= self._written(field_of(n, "left"), watched, holder)
            elif t == "call_expression" and field_of(n, "function") is not None and dotted(field_of(n, "function")) in MUTATORS:
                args = field_of(n, "arguments")
                if args is not None and named(args):
                    hit |= self._written(named(args)[0], watched, holder)
            stack.extend(n.children)
        return hit

    def _written(self, target, watched: set, holder) -> set:
        """Which watched names a write to this target changes: the name itself, or anything of the props object."""
        if target is None:
            return set()
        target = inner(target)
        if target.type == "identifier":
            return {text(target)} & watched
        if target.type in ("member_expression", "subscript_expression"):
            root = target
            while root.type in ("member_expression", "subscript_expression"):
                root = inner(field_of(root, "object")) if field_of(root, "object") is not None else root.children[0]
            return {holder} if holder and root.type == "identifier" and text(root) == holder else set()
        if target.type in ("object_pattern", "array_pattern"):
            return set(self._pattern_names(target, visit_defaults=False)) & watched
        return set()

    def _leaves(self, expression) -> list:
        """The values an expression can have as its result, where it hands one of two on: `a && b` is b, `c ? d : e` is d or e."""
        expression = inner(expression)
        if expression.type == "as_expression" and named(expression):
            return self._leaves(named(expression)[0])
        if expression.type == "binary_expression" and field_of(expression, "operator") is not None:
            right = field_of(expression, "right")
            if text(field_of(expression, "operator")) in ("&&", "||", "??") and right is not None:
                return self._leaves(right)
        if expression.type == "ternary_expression":
            return [leaf for branch in ("consequence", "alternative") if field_of(expression, branch) is not None for leaf in self._leaves(field_of(expression, branch))]
        return [expression]

    def _place_props(self, expression) -> None:
        """If a value this expression results in is one of the props of the function (`children`), record where it is placed."""
        if self.scope.id not in self.props:
            return
        for leaf in self._leaves(expression):
            key = self._prop_key(leaf)
            if key is not None:
                placement = self._placement(leaf)
                if placement.kind != "none":
                    self.scope.slots.setdefault(key, []).append(placement)

    # -- memo, forwardRef and lazy

    def _react_name(self, chain) -> str | None:
        """The id of a name imported from a package, `pkg:react.memo` for `memo` and for `React.memo`, else None: decided from
        how the name was imported, never from the name alone."""
        if not chain or chain[0] == "this" or self._local(chain[0]):
            return None
        binding = self.module.names.get(chain[0])
        if binding is None or not binding.target.startswith(PACKAGE):
            return None
        rest = "".join("." + part for part in chain[1:])
        if binding.kind == "from" and binding.name != "default":
            return f"{binding.target}.{binding.name}{rest}"  # import { Children as C }: C.map is pkg:react.Children.map
        if rest and (binding.kind == "import" or (binding.kind == "from" and binding.name == "default")):
            return f"{binding.target}{rest}"  # import React / import * as React: React.memo is pkg:react.memo
        return None

    def _wrapper_of(self, call) -> str | None:
        """"wrap" for a call of React's memo or forwardRef, "lazy" for React's lazy, else None."""
        func = field_of(call, "function")
        ident = self._react_name(dotted(func) if func is not None else None)
        if ident is None:
            return None
        if ident in self.profile.component_wrappers:
            return "wrap"
        return "lazy" if ident in self.profile.lazy_wrappers else None

    def _wrapper_name(self, call) -> str:
        """`memo` for `memo(f)` and for `React.memo(f)`: the name the wrapper is imported under, without its package."""
        func = field_of(call, "function")
        return (self._react_name(dotted(func) if func is not None else None) or "").rsplit(".", 1)[-1]

    def _wrapped_component(self, name: str, call) -> bool:
        """`const X = memo(f)` and the like. A function written inside the call is named X, as `const X = () => ...` is;
        a name is what X means when used as a tag; lazy is a component ripple cannot see. True when it was read.

        What a name held is (kind, chain, how): how is the wrapping as written, `memo(forwardRef(f))`, which a question
        about the module that exports X quotes when it answers with f."""
        kind = self._wrapper_of(call)
        if kind is None:
            return False
        wrappers = [self._wrapper_name(call)]
        args = field_of(call, "arguments")
        rest = named(args) if args is not None else []
        first = rest[0] if rest else None
        while kind == "wrap" and first is not None and inner(first).type == "call_expression" and self._wrapper_of(inner(first)) == "wrap":
            wrappers.append(self._wrapper_name(inner(first)))
            nested = field_of(inner(first), "arguments")  # memo(forwardRef(f)): the same function
            rest = named(nested) if nested is not None else []
            first = rest[0] if rest else None
        if kind == "wrap" and first is not None and inner(first).type in FUNCTION_VALUES:
            self._function(inner(first), name)
            for other in rest[1:]:
                self.visit(other)
            return True
        if kind == "lazy":
            held = ("lazy", None, "")
        else:
            chain = dotted(first) if first is not None else None
            if chain and not (self.rebinds_this and chain[0] == "this"):
                held = ("wrap", self._ref(chain), "(".join(wrappers) + "(" + ".".join(chain) + ")" * len(wrappers))
            else:
                held = ("unknown", None, "")
        if args is not None:
            self.generic(args)
        self._bind(name, [unknown_ref(ASSIGNED_UNTYPED)])
        self.scope.wrapped[self._name(name)] = held
        return True


def _accessor_names(source: bytes) -> set[str]:
    """Every name a file declares with `get name(` or `set name(`. A superset is fine: a property read is
    recorded only for these names, and the resolver then checks the property really is one."""
    return set(ACCESSOR_NAME.findall(source.decode("utf-8", "replace")))


def collect_scopes(
    repo: Path, files: list[Path], profile: LanguageProfile = TYPESCRIPT
) -> tuple[dict[str, Scope], list[str], dict[str, str]]:
    """Parse the given source files. Returns (scopes by id, files that did not parse cleanly, {file read: its module id}).

    A file tree-sitter could not parse completely is listed and still contributes what it could read
    (the parser recovers locally, so the rest of the file is usually intact), and counts as read. A file
    that cannot be read at all is listed, contributes nothing and does not count as read."""
    typescript, tsx = load_parsers()
    modules = ModuleMap(repo, files)
    # One pass to learn which property names are accessors anywhere in the repo, one to parse: the files
    # are read twice rather than all held in memory at once.
    accessors: set[str] = set()
    errors: list[str] = []
    readable: list[Path] = []
    for path in files:
        try:
            accessors |= _accessor_names(path.read_bytes())
            readable.append(path)
        except OSError:
            errors.append(path.relative_to(repo).as_posix())
    scopes: dict[str, Scope] = {}
    read: dict[str, str] = {}
    # A file another file's module id clashes with (a.ts and a/index.ts) is indexed under an id of its own and
    # listed with the files that did not parse cleanly, as is a tsconfig.json that could not be read: both
    # change what ripple can say, and neither may be silent.
    errors += [rel for rel in modules.shadowed if rel in {p.relative_to(repo).as_posix() for p in readable}]
    errors += modules.problems  # the configs (the root one is tsconfig.json) that could not be read
    for path in readable:
        rel = path.relative_to(repo).as_posix()
        try:
            tree = (tsx if path.suffix == ".tsx" else typescript).parse(path.read_bytes())
            builder = FileBuilder(modules.modules[rel], rel, tree.root_node, modules, accessors, profile)
        except Exception:  # noqa: BLE001 (a file the front end cannot read must cost that file, not the index)
            # Most often RecursionError (nested deeper than even build_graph's raised limit allows) or an
            # OSError (the file went away since the walk). Nothing it had half-written is kept.
            errors.append(rel)
            continue
        if tree.root_node.has_error:
            errors.append(rel)
        scopes.update(builder.scopes)
        read[rel] = modules.modules[rel]
    return scopes, sorted(set(errors)), read


def build_ts_graph(repo: Path, files: list[Path], skipped: list[str], profile: LanguageProfile = TYPESCRIPT) -> Graph:
    """The call graph of a TypeScript repo (indexer.build_graph calls this for the TYPESCRIPT profile)."""
    from .resolver import Resolver

    scopes, errors, read = collect_scopes(repo, files, profile)
    graph = Resolver(scopes, None, errors, profile).run()
    graph.parse_errors = sorted(errors)
    graph.skipped_dirs = skipped
    graph.modules.update(read)
    return graph


# What the registry (registry.py) asks of a front end: index_files and NotInstalled are this module's names for
# build_ts_graph and TypeScriptNotInstalled, and config_files (imported above) is the tsconfig.json files
# the cache fingerprints.
index_files = build_ts_graph
NotInstalled = TypeScriptNotInstalled


def reads_as_source(path: Path, profile: LanguageProfile) -> bool:
    """False for a `.ts` file that is not TypeScript, by its first bytes. The suffix is shared: a Qt Linguist
    translation file is XML (`<?xml`, `<!DOCTYPE TS>` or `<TS`), and a transport stream (video) is
    binary. Neither is read, counted or warned about. Any other file, and a file that cannot be opened (so
    that reading it fails and is reported with the unparsable ones), counts as source."""
    if not path.name.endswith(".ts"):
        return True
    try:
        with open(path, "rb") as handle:
            head = handle.read(512)
    except OSError:
        return True
    head = head.removeprefix(b"\xef\xbb\xbf").lstrip()
    return b"\0" not in head and not head.startswith((b"<?xml", b"<!DOCTYPE TS", b"<TS"))
