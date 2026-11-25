"""The TypeScript front end: read .ts, .tsx, .mts and .cts files, and .js, .jsx, .mjs and .cjs files, and write the
same facts indexer.py writes for Python (facts.py), so the resolver can build the call graph from any of them.

JavaScript is read with the tsx grammar (JSX is legal in a .js file, and the grammar parses plain JavaScript) by the same code,
as its own language with its own graph (profile.JAVASCRIPT). What only TypeScript means is refused in a .js file: `f < a > (b)`
is two comparisons there, not a call with type arguments. Flow annotations are read as the TypeScript annotations they look like,
at the same trust. Only ES modules are followed: CommonJS (`require`, `module.exports`) is not modelled, so a call through one
stays unresolved, and a JavaScript file's calls into TypeScript, and the reverse, are not followed either (ts_modules.py).

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
hold an arrow function, class expressions, object literal methods, enums, namespaces, `require` and `module.exports`, and a tsconfig
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
from .resolver import InstanceFacts
from .ts_modules import (  # noqa: F401 (config_files, module_id, output_dirs, presence_files: the registry asks this module for them)
    JS_SUFFIXES,
    ModuleMap,
    config_files,
    module_id,
    output_dirs,
    presence_files,
)

INSTALL_HINT = "pip install 'ripple-code[typescript]'"
REINSTALL = f"it needs tree-sitter>=0.25,<0.27 and tree-sitter-typescript>=0.23.2,<0.24, which {INSTALL_HINT} installs"
PACKAGE = "pkg:"  # what a module id never starts with: the target of an import from outside the repo

THIS_REBOUND = "the receiver is `this` inside a function expression, which binds its own `this`"
THIS_PLAIN = "the receiver is `this` in a function that is not a method, so its class is not known"
DECORATED = "the receiver is a member that a decorator may replace"
DECORATOR = "a decorator runs when the class is defined, and ripple does not follow what it does to the member"
BAD_IMPORT = "the receiver comes from an import that does not name a file ripple reads"
OWN_PROPERTY = "the receiver is a field or parameter property of the class, which hides what a base class defines"
OWN_ASSIGNED = "the receiver is a property that a method of the class assigns, which hides what a base class defines"
PROTOTYPE_WRITE = "the receiver is a member that the class body also writes on its own prototype"
STATIC_PROPERTY = "the receiver is a static field of the class, which is a property of the class itself and hides what a base class defines there"
ABSTRACT = "the receiver is an abstract member, which runs nothing and hides what a base class defines"
STATIC_AND_INSTANCE = "the receiver is a name that a static and an instance member of the class both declare"
TYPE_DECLARED = "the receiver is named like an interface, type, enum or namespace declared here, which ripple does not follow"
BLOCK_FUNCTION = "the receiver is named like a function declared in a block, which in code that is not strict is a variable of the function around it too"
IMPORT_ALIAS = "the receiver is named like an import alias (`import a = N.M`) declared here, which ripple does not follow"
REDECLARED = (
    "the receiver is a name that a class and another declaration both declare in the same block, function or file, which the language "
    "rejects, so what it names is not known"
)
OWN_NAME = "the receiver is the name of a function expression, which is the function itself"
DESTRUCTURED = "the receiver is destructured from an object or array, whose type ripple does not follow"
CALLBACK_PARAMETER = "the receiver is a parameter of an anonymous function, whose type ripple does not infer"
THIS_TAG = "the tag is a member of `this`, which ripple does not follow for components"
COMPUTED_TAG = "the tag is not a name or a dotted name"
JS_TYPE_ARGUMENTS = (
    "the call has type arguments, which JavaScript does not have: `f < a > (b)` there is two comparisons, "
    "so whether it is a call is not known"
)
JS_AFTER_ERROR = (
    "the call is inside or right after a syntax error the parser recovered from, usually a name the TypeScript grammar reads "
    "as a keyword (`abstract.foo()` is `abstract.` and a call of foo there), so what it calls is not known"
)
# The names a CommonJS file is run with (a function's parameters, as Node wraps the file): what is reachable through them is the file's
# own exports and any other module's, so code of the repo, and a call through one is never "outside the repo"
MODULE_NAMES = ("require", "module", "exports")
MODULE_ROOTS = (*MODULE_NAMES, "process")  # `process.mainModule` is the entry module, which may be this file
GLOBAL_OBJECTS = ("globalThis", "global")  # `globalThis.module` is the module root again, spelled as a property of the global object
PROCESS_MODULES = ("process", "node:process")  # what `import process from "..."` names when it is Node's own
TRACKED = {*MODULE_ROOTS, *GLOBAL_OBJECTS}  # the names whose answer depends on what a declaration of the same name does (see `_declares`)
GLOBAL_EXPORTS = (
    "the callee is reached through `globalThis` or `global` as a member of `module`, `exports` or `require`, which a file Node runs does not "
    "have on the global object unless some code stores one there (code of the repo or of a package), so it is not known what runs, or "
    "that it is outside the repo"
)
OWN_EXPORTS = (
    "the callee is a member of `exports`, `module`, `require` or `process.mainModule`, which hold the modules' own objects (this file's "
    "exports, the loader, the entry module), and ripple does not follow what is stored on them, so it is not known to be outside the repo"
)
# what ends the climb from a call to see whether a syntax error comes right before it (the statement that holds it)
STATEMENT_END = ("statement_block", "program", "class_body", "switch_body")


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


class _Frame(dict):
    """The names one block or function declares ({name: the key it is bound under}). `erased` are those that only the types declare, and
    `kind` is "function" for a function's frame (where `var` names belong, wherever in the function they are written), "catch" for the
    frame of a catch clause with a plain parameter (the one block a `var` of the same name is bound in), else "block"."""

    erased: frozenset = frozenset()
    kind: str = "block"
    redeclared: frozenset = frozenset()  # the names a class and another declaration of the frame both declare (see FileBuilder._redeclared)


# What each kind of statement the grammar has does to the names around it, and so which frame (see `_Frame`) a name it declares belongs to.
# This table is read by the code (declaration_of, _declared_names, _var_names, _bind_type_names, TYPE_DECLARATIONS, NAMESPACES), and
# tests/test_js_module_names.py runs every row in Node 24 and tsc: the scope is what the language does, not a guess.
#   block           the name belongs to the block the statement stands in (`let`, `const`, `class`)
#   block type      the same, and it is a type too (an `enum`: `let` in a block, merged into what the file is run with at the top of a CommonJS file)
#   namespace       the same as `block type`, unless it holds only types, when it is erased (`namespace N {}`, `module N {}`)
#   function        the name belongs to the function (the file's, at top level) around the statement, whichever block it is written in: `var`.
#                   It is never decided by what else of that name is in the block
#   block function  the block's own name, and in sloppy-mode code the function's too (Annex B), which ripple cannot tell from strict code, so
#                   a function declared in a block is a name of the function as well
#   erased          no name at run time (an interface, a type alias)
#   ambient         `declare ...`: no name at run time, and nothing ripple reads
#   wrapper         holds the declaration in another node, which `declaration_of` looks through (see HOLDERS)
#   import          an import statement: its `import` clause binds names ripple follows (`import x = require()` is the same kind of node and binds none)
#   alias           `import a = N.M`: the name belongs to the block it stands in (the namespace body, or the file) and holds what ripple does
#                   not follow; at the top of the file it is left out of `_top_names`, as a `var` of a module name is (but not
#                   one of `globalThis` and `global`, which no wrapper parameter has: they hide the global object, as a `var` does)
#   none            binds no name that ripple reads: a control statement (the statements inside are read where they stand)
STATEMENT_KINDS = {
    "export_statement": "wrapper",
    "expression_statement": "wrapper",  # the grammar holds `namespace N {}` in one
    "labeled_statement": "wrapper",
    "ambient_declaration": "ambient",
    "lexical_declaration": "block",
    "variable_declaration": "function",
    "function_declaration": "block function",
    "generator_function_declaration": "block function",
    "class_declaration": "block",
    "abstract_class_declaration": "block",
    "enum_declaration": "block type",
    "internal_module": "namespace",  # `namespace N {}`
    "module": "namespace",  # `module N {}`
    "interface_declaration": "erased",
    "type_alias_declaration": "erased",
    "import_statement": "import",
    "import_alias": "alias",
    "function_signature": "none",
    "statement_block": "none",
    "if_statement": "none",
    "switch_statement": "none",
    "for_statement": "none",
    "for_in_statement": "none",
    "while_statement": "none",
    "do_statement": "none",
    "try_statement": "none",
    "with_statement": "none",
    "break_statement": "none",
    "continue_statement": "none",
    "debugger_statement": "none",
    "return_statement": "none",
    "throw_statement": "none",
    "empty_statement": "none",
}
CLASSES = ("class_declaration", "abstract_class_declaration")  # the statements of role "block" that are a class, which a redeclaration is read for
NAMESPACES = tuple(kind for kind, role in STATEMENT_KINDS.items() if role == "namespace")
TYPE_DECLARATIONS = tuple(kind for kind, role in STATEMENT_KINDS.items() if role in ("block type", "namespace", "erased"))


def _held_by_export(node):
    return field_of(node, "declaration")


def _held_by_label(node):
    return field_of(node, "body")


def _held_by_expression(node):
    """The grammar holds `namespace N {}` in an expression statement; any other expression holds no declaration."""
    kids = named(node)
    return kids[0] if len(kids) == 1 and STATEMENT_KINDS.get(kids[0].type) == "namespace" else None


HOLDERS = {"export_statement": _held_by_export, "labeled_statement": _held_by_label, "expression_statement": _held_by_expression}


def declaration_of(node):
    """(the declaration a statement holds, whether it follows `declare`), seen through every node whose row of STATEMENT_KINDS says
    "wrapper" (`export` and `export default`, a label, the expression statement that holds a `namespace`) or "ambient" (`declare`).
    A statement that holds no declaration is returned as it is."""
    ambient = False
    while True:
        role = STATEMENT_KINDS.get(node.type)
        if role == "ambient":
            ambient = True
            kids = named(node)
            if not kids:
                return node, True
            node = kids[0]
        elif role == "wrapper":
            held = HOLDERS[node.type](node)
            if held is None:
                return node, ambient
            node = held
        else:
            return node, ambient


def declared_name(node) -> str | None:
    """The name a declaration binds: `A` for `namespace A.B {}`, which declares `A`. None for what has no plain name."""
    if node.type == "import_alias":  # `import a = N.M`: the grammar gives it no `name` field, and reads `import type a = N.M` as an error
        kids = named(node)
        return text(kids[0]) if kids and kids[0].type == "identifier" and not any(c.type == "ERROR" for c in node.children) else None
    name = field_of(node, "name")
    if name is not None and name.type == "nested_identifier":
        name = next(iter(named(name)), None)
    return text(name) if name is not None and name.type in ("identifier", "type_identifier") else None


def _erased(node) -> bool:
    """Whether a declaration of the types exists in no form at run time: an interface, a type alias, and a namespace that holds only
    these (and ambient declarations, and namespaces like it), which TypeScript emits nothing for. Run in Node 24 (`--experimental-
    transform-types`), such a namespace left `module` the module's own object, where an enum, a `const enum` and a namespace with a value
    bound it. Anything not shown to be erased is not."""
    if STATEMENT_KINDS.get(node.type) == "erased":
        return True
    if node.type not in NAMESPACES:
        return False
    body = field_of(node, "body")
    for statement in named(body) if body is not None else ():
        held, ambient = declaration_of(statement)
        if not ambient and not _erased(held):
            return False
    return True


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


def string_value(node) -> str | None:
    """The text of a string literal or of a template with no substitution, else None."""
    if node.type == "string":
        return "".join(text(c) for c in node.children if c.type in ("string_fragment", "escape_sequence"))
    if node.type == "template_string" and not any(c.type == "template_substitution" for c in node.children):
        return "".join(text(c) for c in node.children if c.type in ("string_fragment", "escape_sequence"))
    return None


_ESCAPE = re.compile(r"\\(u\{[0-9a-fA-F]+\}|u[0-9a-fA-F]{4}|x[0-9a-fA-F]{2}|\r\n|[\s\S])")
_SIMPLE_ESCAPES = {"n": "\n", "r": "\r", "t": "\t", "b": "\b", "f": "\f", "v": "\v"}


def unescape(raw: str) -> str | None:
    """The characters the escape sequences in `raw` spell (a string literal's, a template's, an identifier's `\\u006d`), or None
    when one is not decoded here: a malformed `\\u` or `\\x`, a legacy octal escape (`\\1`, `\\012`), a code point past U+10FFFF."""
    out, at = [], 0
    for found in _ESCAPE.finditer(raw):
        out.append(raw[at : found.start()])
        at = found.end()
        body = found.group(1)
        if body[0] == "u" and len(body) > 1:
            code = int(body[2:-1] if body[1] == "{" else body[1:], 16)
            if code > 0x10FFFF:
                return None
            out.append(chr(code))
        elif body[0] == "x" and len(body) > 1:
            out.append(chr(int(body[1:], 16)))
        elif body in ("\r\n", "\n", "\r", "\u2028", "\u2029"):
            pass  # a line continuation
        elif body in _SIMPLE_ESCAPES:
            out.append(_SIMPLE_ESCAPES[body])
        elif body == "0" and not raw[at : at + 1].isdigit():
            out.append("\0")
        elif body.isdigit() or body in ("u", "x"):
            return None
        else:
            out.append(body)  # `\'`, `\\`, and any character that needs no escape
    out.append(raw[at:])
    return "".join(out)


def cooked(node) -> str | None:
    """The text a string literal, or a template with no substitution, spells once its escapes are read, else None."""
    if node.type not in ("string", "template_string") or any(c.type == "template_substitution" for c in node.children):
        return None
    parts = []
    for child in node.children:
        if child.type == "string_fragment":
            parts.append(text(child))
        elif child.type == "escape_sequence":
            value = unescape(text(child))
            if value is None:
                return None
            parts.append(value)
    return "".join(parts)


def property_key(node) -> str | None:
    """The name a property key spells when it can be read from the source, else None: an identifier (with its `\\u006d` escapes
    read), a string, a number, a private name, or a `[...]` holding a string or a template with no substitution. `m`, `'m'`,
    `["m"]`, `[`m`]` and `\\u006d` are all the one name m. A `[...]` that holds anything else (a symbol, a variable) is not read."""
    if node is None:
        return None
    kind = node.type
    if kind in ("property_identifier", "private_property_identifier"):
        raw = text(node)
        return unescape(raw) if "\\" in raw else raw
    if kind in ("string", "template_string"):
        return cooked(node)
    if kind == "number":
        return text(node)
    if kind == "computed_property_name":
        kids = named(node)
        value = inner(kids[0]) if len(kids) == 1 else None
        return property_key(value) if value is not None and value.type in ("string", "template_string", "number") else None
    return None


def unwrap(node):
    """The expression inside parentheses, a non-null assertion, `as`, `satisfies` or a `<T>` assertion, however deep: `(this as any)` is `this`."""
    while node is not None and node.type in ("parenthesized_expression", "non_null_expression", "satisfies_expression", "as_expression", "type_assertion"):
        kids = named(node)
        if not kids:
            return node
        node = kids[-1] if node.type == "type_assertion" else kids[0]
    return node


def pattern_targets(node) -> list:
    """The things a destructuring assignment pattern assigns to: names, members and subscripts (`({ m: this.m, ...rest } = o)`,
    `[this.m = 1] = xs`), not the defaults or the keys."""
    kind = node.type
    if kind in ("object_pattern", "array_pattern", "rest_pattern"):
        return [leaf for child in named(node) for leaf in pattern_targets(child)]
    if kind == "pair_pattern":
        value = field_of(node, "value")
        return pattern_targets(value) if value is not None else []
    if kind in ("assignment_pattern", "object_assignment_pattern"):
        left = field_of(node, "left")
        return pattern_targets(left) if left is not None else []
    return [node]


def dotted(node) -> list | None:
    """["a", "b", "c"] for the expression a.b.c, and ["this", "x"] for this.x. None for anything else."""
    parts = []
    while True:
        node = inner(node)
        if node.type == "member_expression":
            prop = field_of(node, "property")
            if prop is None or prop.type not in ("property_identifier", "private_property_identifier"):
                return None
            name = property_key(prop)  # an escaped name is the name it spells
            if name is None:
                return None
            parts.append(name)
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
            name = property_key(prop)
            if name is None:
                return None
            parts.append(name)
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
FUNCTION_NODES = FUNCTION_VALUES + FUNCTION_DECLARATIONS + ("method_definition",)
CLASS_NODES = ("class_declaration", "class", "abstract_class_declaration")
# Calls that write to the object they are given first (see FileBuilder._own_writes), and what a constructor may return without
# handing `new C()` another object: `undefined`, `this` and anything that is a primitive whatever it is made of.
WRITES_TO_AN_OBJECT = (
    "Object.assign",
    "Object.defineProperty",
    "Object.defineProperties",
    "Object.setPrototypeOf",
    "Reflect.set",
    "Reflect.defineProperty",
    "Reflect.setPrototypeOf",
)
PRIMITIVE_VALUES = ("number", "string", "template_string", "true", "false", "null", "unary_expression")
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
_GAP = r"(?:\s|/\*[\s\S]*?\*/|//[^\n]*)"  # white space and comments, which may stand between the tokens of `get name(`
ACCESSOR_NAME = re.compile(rf"\b(?:get|set){_GAP}+#?([A-Za-z_$][\w$]*){_GAP}*[(<]")


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
        self.js = file.endswith(JS_SUFFIXES)  # JavaScript, read with the TypeScript grammar: what only TypeScript means is not trusted
        self.damaged = root.has_error  # tree-sitter recovered from a syntax error: where an element is placed is not trusted
        self.runtime = self._runtime_problem(root)  # why this file's JSX is not React's, "" when it is
        self.titles: list[str] = []  # the describe() titles whose callbacks are being read
        self.rebinds_this = 0  # inside a function expression, which has a `this` of its own
        self.exports: list[tuple[str, str]] = []  # (exported name, local name), settled when the file is read
        self.not_known: dict[str, str] = {}  # specifier -> why an import of it is not known to be a package or a file
        self._methods: dict = {}
        self.frames: list[_Frame] = []  # the names each enclosing block or function declares (see _push)
        self.counter = 0
        self.statics: dict[str, bool] = {}  # scope id -> whether it is a static member
        self.own_accessors: dict[str, set] = {}  # class id -> the names it declares with get or set
        self.class_names: dict[str, set] = {}  # class id -> the names the class body calls itself by (`C.prototype` there is its own)
        self.replaced: set[int] = set()  # tree-sitter ids of the class members that a later member of the same name replaces
        self.fn_nodes: dict[str, int] = {}  # scope id -> the tree-sitter id of the function node it is
        self.call_nodes: dict[int, RawCall] = {}  # tree-sitter id of a call expression -> the call recorded for it
        self.jsx_nodes: dict[int, JsxElement] = {}  # tree-sitter id of a component element -> its fact
        self.props: dict[str, tuple[dict, str | None]] = {}  # scope id -> ({local name: prop key}, name of the props object)
        self.top_names = self._top_names(root)  # the module names the file declares at its top level (see _module_name_of)
        self.process_imports = self._process_imports(root)  # local name -> how Node's `process` is imported under it (see _canonical)
        for node in named(root):
            self.visit(node)
        for name in self._redeclared(named(root)):  # the file's own names have no frame: see _pop for a block's and a function's
            self.module.names[name] = Binding("var", evidence=[unknown_ref(REDECLARED)])
        self._bind_top_values(named(root))
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

    def _push(self, names, fresh: bool, erased=(), kind: str = "block", redeclared=()) -> None:
        """Open a frame for the names a block, a function or a clause declares. `erased` are the names among them that only an
        interface, a type alias or a namespace of types declares: they are no value at run time (see `_declares`). `redeclared` are the
        names that a class and another declaration of the frame both declare (see `_redeclared`)."""
        frame = _Frame({n: (self._fresh(n) if fresh else n) for n in dict.fromkeys(names)})
        frame.erased = frozenset(erased)
        frame.kind = kind
        frame.redeclared = frozenset(redeclared)
        self.frames.append(frame)

    def _pop(self) -> None:
        """Close the frame. A name that a class and another declaration both declare in it is bound to nothing known now, whatever the class
        bound it to while the frame was read: the language rejects the program, so no call through the name runs, and an edge to the class
        would say a function runs that Node never reaches. It is rebound here, after the class, so the later binding is the one kept."""
        frame = self.frames.pop()
        for name in frame.redeclared:
            self.scope.names[frame[name]] = Binding("var", evidence=[unknown_ref(REDECLARED)])

    def _name(self, name: str) -> str:
        """The key a name written here (read or written) is bound under: the innermost frame that holds it as a value, else itself. A frame
        that only an interface or a type alias of that name stands in is skipped: it is erased, so the name there is still the one outside.
        Reads and writes of a value go through this one function; a name in the position of a type is `_type_name`."""
        for frame in reversed(self.frames):
            if name in frame and name not in frame.erased:
                return frame[name]
        return name

    def _type_name(self, name: str) -> str:
        """The key a name written in the position of a type is looked up under: an interface or a type alias of that name hides a class."""
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

    def _declares(self, name: str) -> bool:
        """Whether a function or block around here gives the name a value of its own at run time. `_local` also counts what only the
        types declare (`interface module {}`): that is erased, so `module` there is still the module's own object. An enum is a
        value (`const enum` is erased by tsc and bound by Node and esbuild, which ripple cannot tell apart, so it counts as one: the
        call is then unresolved, never a call of the module's own)."""
        return any(name in frame and name not in frame.erased for frame in self.frames)

    def _ref(self, parts: list) -> list:
        """A dotted reference as the resolver should read it: its first name, under the key it is bound by."""
        return [self._name(parts[0]), *parts[1:]] if parts else parts

    def _module_name_of(self, parts: list) -> str:
        """The name (`exports`, `module`, `require`, or `process` for `process.mainModule`) a reference goes through to reach the
        exports of a module, "" when it does not: a name followed by a member (`require.main.exports.f`, not the call `require(...)`
        or `require(...).f`, whose result is what a call of `require` returns), or `process.mainModule`. A name that a declaration
        around the reference gives a value of its own (a parameter, a `var`, `let`, `const`, `function` or `class` of a function or
        block around it, or of the file, an import) is that value: it is not the module's. A bare assignment (`exports = new K()`) is
        not a declaration: the name still holds the module's own object until it runs, and which it holds at a call is not known."""
        if len(parts) < 2 or parts[1] == "()":
            return ""
        parts, spelled = self._canonical(parts)
        if len(parts) < 2 or parts[1] == "()":
            return ""
        if parts[0] in MODULE_NAMES or parts[:2] == ["process", "mainModule"]:
            return "" if not spelled and (self._declares(parts[0]) or parts[0] in self.top_names) else parts[0]
        return ""

    def _canonical(self, parts: list) -> tuple[list, str]:
        """(the reference as Node's own names spell it, how it was spelled another way: "global", "import" or "" when it was not).
        `globalThis.module.exports.f` and `global.process.mainModule.exports.f` are `module.exports.f` and
        `process.mainModule.exports.f`, and the name a declaration around the reference gives `module` does not hide the property of the
        global object; a `globalThis` or `global` that a declaration gives a value of its own (a parameter, a `let`, `const`, `var`,
        `function` or `class`, an import) is not the global object, so the reference is left as written. `process` (or a namespace of
        it, or its `mainModule`) imported from Node's `process` or `node:process` is `process`, which is reached through
        `process.mainModule` the same way."""
        head = parts[0]
        if head in GLOBAL_OBJECTS and not (self._declares(head) or head in self.top_names):
            rest = parts[1:]
            while rest and rest[0] in GLOBAL_OBJECTS:
                rest = rest[1:]
            return rest, "global"
        how = self.process_imports.get(head)
        if how is None or self._declares(head):
            return parts, ""
        if how == "mainModule":
            return ["process", "mainModule", *parts[1:]], "import"
        if how == "*" and parts[1:2] == ["default"]:  # `import * as ns from "process"`: ns.default is process, and ns.mainModule too
            return ["process", *parts[2:]], "import"
        return ["process", *parts[1:]], "import"

    def _process_imports(self, root) -> dict[str, str]:
        """The names the file imports Node's `process` under: {local name: "default" | "*" | "mainModule"}. `import process from`,
        `import { default as p } from` and `import * as ns from` of `process` or `node:process`, and `import { mainModule }`
        (which is `process.mainModule`). Any other member of it is no module object. A type-only import is no value."""
        found: dict[str, str] = {}
        for node in named(root):
            if node.type != "import_statement" or any(c.type == "type" for c in node.children):
                continue
            required = self._require_clause(node)
            if required is not None:  # `import p = require("node:process")` is `const p = require("node:process")`: the process object, as a default import is
                if not self.js and string_value(required[1]) in PROCESS_MODULES:
                    found[required[0]] = "default"
                continue
            source = field_of(node, "source")
            if source is None or string_value(source) not in PROCESS_MODULES:
                continue
            clause = next((c for c in named(node) if c.type == "import_clause"), None)
            for part in named(clause) if clause is not None else ():
                if part.type == "identifier":
                    found[text(part)] = "default"
                elif part.type == "namespace_import":
                    found.update({text(c): "*" for c in named(part) if c.type == "identifier"})
                elif part.type == "named_imports":
                    for item in named(part):
                        name, alias = field_of(item, "name"), field_of(item, "alias")
                        if item.type != "import_specifier" or name is None or any(c.type == "type" for c in item.children):
                            continue
                        if text(name) in ("default", "mainModule"):
                            found[text(alias or name)] = "default" if text(name) == "default" else "mainModule"
        return found

    def _own_exports(self, call: RawCall | JsxElement, parts: list) -> None:
        """A call (or JSX tag) through a module name that no declaration shadows reaches the module's own exports, or another
        module's: it may run code of the repo, so it is unresolved, never outside the repo."""
        name = self._module_name_of(parts)
        if not name:
            return
        # `globalThis.module` is not `module`: Node's own `module`, `exports` and `require` are the file's parameters, so through the
        # global object they are what code stored there, or nothing (a call that throws), and the reason must not say they are the module's
        why = GLOBAL_EXPORTS if name in MODULE_NAMES and self._canonical(parts)[1] == "global" else OWN_EXPORTS
        if isinstance(call, JsxElement):  # <exports.Widget />: a tag the resolver reads as it reads a callee
            call.chain, call.why = None, why
        else:
            call.chain, call.path, call.special, call.why = None, None, "computed", why

    def _top_names(self, root) -> set[str]:
        """The names of the module names (`exports`, `module`, `require`, `process`) that the file declares at its top level, wherever
        in the file, as a value of its own: `let`, `const`, `function`, `class` and an import. What ripple binds for none of these is
        left out on purpose, so a call through such a name stays unresolved: an `enum` or a `namespace` (a namespace of the name merges
        with the module's own object where the file is CommonJS), an `import x = N.M`, an `interface`, a type alias, and everything after `declare`, which makes no value. A top-level `var` is left out as well: where the file is
        CommonJS it redeclares the parameter Node runs the file with, so the name holds the module's own object until the assignment
        runs, as it does for a bare assignment."""
        found = set(self._declared_names(named(root), types=False))  # let, const, class, function, not the type-only declarations
        for node in named(root):
            if node.type != "import_statement" or any(c.type == "type" for c in node.children):
                continue
            required = self._require_clause(node)
            if required is not None:  # `import process = require("./x")` is `const process = require("./x")`; of a CommonJS file's own parameters
                name, source = required  # a name here is a redeclaration (a SyntaxError), and Node's own `process` stays Node's
                if not self.js and name not in MODULE_NAMES and string_value(source) not in PROCESS_MODULES:
                    found.add(name)
                continue
            clause = next((c for c in named(node) if c.type == "import_clause"), None)
            for part in named(clause) if clause is not None else ():
                if part.type == "identifier":
                    found.add(text(part))
                elif part.type == "namespace_import":
                    found.update(text(c) for c in named(part) if c.type == "identifier")
                elif part.type == "named_imports":
                    for item in named(part):
                        local = field_of(item, "alias") or field_of(item, "name")
                        if item.type == "import_specifier" and local is not None and not any(c.type == "type" for c in item.children):
                            found.add(text(local))
        # `globalThis` and `global` are no parameters of the file: a top-level `var` of the name is an ordinary variable that hides the global
        # object (the `var` of a module name is not left out because of the parameter Node runs the file with, which these two are not)
        found |= set(self._var_names(root, namespaces=False)) & set(GLOBAL_OBJECTS)
        # `import globalThis = N.M` is `var globalThis = N.M` (tsc), the same variable that hides the global object
        for node, ambient in map(declaration_of, named(root)):
            if not ambient and node.type == "import_alias" and declared_name(node) in GLOBAL_OBJECTS:
                found.add(declared_name(node))
        return found & {*MODULE_ROOTS, *GLOBAL_OBJECTS}

    def _ref_text(self, dotted_name: str) -> str:
        head, dot, rest = dotted_name.partition(".")
        return self._type_name(head) + dot + rest

    def _declared_names(self, nodes, types: bool = True, erased: bool = True) -> list[str]:
        """The names the statements declare directly (not in a nested block or function) with let, const,
        class or function: what a block of them makes its own, before any of them runs. An interface, a type alias, an
        enum or a namespace is a declaration of its block too (`_bind_type_names` binds it there), unless `types` is False:
        the file's own top level leaves them out (see `_top_names`), which is the one place where they must not count.
        With `erased` False the ones that exist only in the types (see `_erased`) are left out as well: the names that are values."""
        names: list[str] = []
        for node, ambient in map(declaration_of, nodes):  # `export`, a label, a namespace's expression statement: see declaration_of
            role = STATEMENT_KINDS.get(node.type)
            if ambient:
                continue
            if role in ("block", "block function"):
                names += self._names_declared(node)
            elif role in ("block type", "namespace", "erased", "alias") and types and (erased or not _erased(node)):
                names += self._names_declared(node)
        return names

    def _redeclared(self, statements, params=()) -> frozenset[str]:
        """The names that a class declares and another declaration of the same frame declares too: a second class, a `let`, a `const`, a
        function, a `var` (written in the frame or in a block of it, not in a function), or a parameter of the function. The language
        rejects each of these (Node: SyntaxError, the file never runs), so no call through the name runs, and ripple does not answer one
        with an edge to the class. Left out on purpose: a class beside an interface (a merge), beside a namespace (TypeScript merges a
        namespace that follows the class, and Node runs it), an enum, and anything after `declare`, none of which is shown to be rejected."""
        classes: list[str] = []
        others: set[str] = set(params)
        read = []
        for statement in statements:
            held, ambient = declaration_of(statement)
            if ambient:
                continue
            role = STATEMENT_KINDS.get(held.type)
            if role == "block" and held.type in CLASSES:
                classes += self._names_declared(held)
            elif role in ("block", "block function"):  # `let`, `const` and a function declaration; a `var` is read below, wherever it is written
                others.update(self._names_declared(held))
            read.append(statement)
        if classes:
            others.update(self._var_names(read, namespaces=False, annex_b=False))
        return frozenset(name for name in classes if name in others or classes.count(name) > 1)

    def _names_declared(self, node) -> list[str]:
        """The names one declaration binds: what its declarators bind (`let`, `const`, `var`), or its own name."""
        if node.type in ("lexical_declaration", "variable_declaration"):
            names: list[str] = []
            for declarator in named(node):
                target = field_of(declarator, "name") if declarator.type == "variable_declarator" else None
                if target is not None:
                    names += self._pattern_names(target, visit_defaults=False)
            return names
        name = declared_name(node)
        return [name] if name is not None else []

    def _erased_names(self, nodes) -> set[str]:
        """The names the statements declare only in the types (an interface, a type alias, a namespace of types), which exist at run time
        in no form: `interface module {}` does not hide the module's own `module`, though it hides a class of that name to the types.
        Only the names a module name can be spelled with are recorded: they are the ones whose answer depends on it (see `_declares`)."""
        if not any(declaration_of(n)[0].type in TYPE_DECLARATIONS for n in nodes):
            return set()  # nearly every block: nothing here is a declaration of the types
        return (set(self._declared_names(nodes)) - set(self._declared_names(nodes, erased=False))) & TRACKED

    def _bind_type_names(self, nodes) -> None:
        """An interface, a type alias, an enum or a namespace declared in a block hides a class of that name from
        the code around it, so the name is bound here, under the key of the block, to something ripple does not follow."""
        for node, ambient in map(declaration_of, nodes):
            # an interface and a type alias are erased: they bind no value to answer for (their name only hides a class in the types)
            if not ambient and STATEMENT_KINDS.get(node.type) in ("block type", "namespace"):
                for name in self._names_declared(node):
                    self.scope.bind(self._name(name), Binding("var", evidence=[unknown_ref(TYPE_DECLARED)]))

    def _bind_top_values(self, nodes) -> None:
        """An enum, or a namespace that holds a value, declared at the top of the file binds its name there (tsc emits a `var` of it): the name is
        the repo's, not a builtin's, so `enum process {}` then `process.cwd()` is not a call of Node's. A namespace of types only and anything
        after `declare` emit nothing and are left out (see `_erased`). A name that something else of the file already binds (a class or a
        function that a namespace merges with) keeps that binding, so a merge is answered as it was."""
        for node, ambient in map(declaration_of, nodes):
            if ambient or STATEMENT_KINDS.get(node.type) not in ("block type", "namespace") or _erased(node):
                continue
            for name in self._names_declared(node):
                if name not in self.module.names:
                    self.module.bind(name, Binding("var", evidence=[unknown_ref(TYPE_DECLARED)]))

    def _var_names(self, node, namespaces: bool = True, annex_b: bool = True) -> list[str]:
        """The names `var` declares anywhere in a function body (it is function scoped), not inside a nested function. With `namespaces`
        False not inside a namespace either, whose `var` is that namespace's own. A function declared in a block is a name of the
        function as well, where the code is not strict (Annex B), and which it is cannot be read: it is listed here when a module name
        can be spelled with it (see `_declares`), so the name after the block is not taken for the module's own."""
        names: list[str] = []
        stack = list(node) if isinstance(node, (list, tuple)) else list(node.named_children)  # a list: the statements of a block, each read as a child
        while stack:
            child = stack.pop()
            role = STATEMENT_KINDS.get(child.type)
            if role == "function":  # `var`: the function's, whichever block it is written in and whatever else of that name is there
                names += self._names_declared(child)
            elif annex_b and role == "block function" and declared_name(child) in TRACKED:
                names.append(declared_name(child))
            if child.type in FUNCTION_NODES or child.type in ("class_declaration", "abstract_class_declaration", "class"):
                continue
            if not namespaces and child.type in NAMESPACES:
                continue
            if child.type == "for_in_statement" and field_of(child, "kind") is not None and text(field_of(child, "kind")) == "var":
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
        erased: set[str] = set()
        redeclared: frozenset = frozenset()
        if body is not None and body.type == "statement_block":
            variables = self._var_names(body)
            erased = self._erased_names(named(body)) - set(names) - set(variables)  # a parameter or a `var` of that name is a value
            redeclared = self._redeclared(named(body), params=self._param_names(node))
            names += self._declared_names(named(body)) + variables
        self._push(names, fresh, erased, redeclared=redeclared)
        self.frames[-1].kind = "function"
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
        var: bool = False,
    ) -> Scope:
        """The scope of a declaration. A name declared again in the same frame (an overload's body, a getter and
        its setter) is the same symbol. A name declared in another block, or a static and an instance member of one
        name, is another declaration and gets an id of its own (`m.f`, then `m.f#2`)."""
        parent = parent or self.scope
        bound = self._var_key(name) if var else self._name(name) if variable else name
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

    def _var_key(self, name: str) -> str:
        """The key a `var` of the name is bound under: the function's (or the file's, which has no frame), whatever block it is written in and
        whatever else of that name is declared in that block, a namespace or an interface or anything. The one block it reaches is the catch
        clause of a plain parameter of the same name, where the initializer is assigned to the parameter (Annex B.3.5)."""
        for frame in reversed(self.frames):
            if frame.kind == "catch" and name in frame:
                return frame[name]
            if frame.kind == "function":
                return frame.get(name, name)
        return name

    def _function_key(self, name: str) -> str:
        """The key the name has in the function around here (or the file): where a function declared in a block belongs as well, in code
        that is not strict."""
        for frame in reversed(self.frames):
            if frame.kind == "function":
                return frame.get(name, name)
        return name

    def _bind(self, name: str, evidence: list, var: bool = False) -> None:
        self.scope.bind(self._var_key(name) if var else self._name(name), Binding("var", evidence=list(evidence)))

    def _assign_name(self, name: str, evidence: list, var: bool = False) -> None:
        """`x = value` where x was declared outside this function is the same variable, not a new local."""
        key = self._var_key(name) if var else self._name(name)
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

    @staticmethod
    def _require_clause(node):
        """(the name, the source node) of `import x = require("m")`, else None. `import type x = require("m")` binds no value."""
        clause = next((c for c in node.named_children if c.type == "import_require_clause"), None)
        if clause is None or any(c.type == "type" for c in node.children):
            return None
        name, source = next((c for c in named(clause) if c.type == "identifier"), None), field_of(clause, "source")
        return (text(name), source) if name is not None and source is not None else None

    def _requires_here(self) -> bool:
        """Whether `import x = require("m")` is valid where the walk is: in a TypeScript file (a `.js` file is a SyntaxError to Node, TS8002 to tsc),
        at its top level (in a namespace tsc says TS1147 and emits no require, in a function body or a block TS1232 and Node a SyntaxError)."""
        return not self.js and not self.frames

    def visit_import_statement(self, node) -> None:
        required = self._require_clause(node)
        if required is not None:  # import x = require("m"): TypeScript emits `const x = require("m")`, so x is the module as `import * as x` is
            if self._requires_here():  # in a .js file, a namespace, a block or a function body the language rejects it: no program runs, nothing is bound
                name, source = required
                target, spec = self._target(source)
                self._imported(name, target, None, spec)
            return
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
            else:
                # a star onto a file ripple does not read into this graph (the other language's, say, or a declaration file
                # or a path that names no file): which names it provides is not known, so no name this module does not
                # declare is taken from another star
                self.module.unread_stars.append(self.not_known.get(spec) or f"`{spec}` names no file ripple reads (it is missing, or a declaration file, which holds types and no code)")
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

    def _function(self, node, name: str, kind: str = "function", accessor: str = "", static: bool = False, var: bool = False) -> Scope:
        """A named function, method or accessor: its scope, its parameters, its body."""
        parent = self.scope
        scope = self._enter(name, kind, node, variable=kind != "method", static=static, var=var)
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
                    self._own_property(owner, name, how="it is a parameter property")  # emitted as a field at ES2022: defined, not assigned
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
            if STATEMENT_KINDS.get(node.type) == "block function" and text(name) in TRACKED and self.frames and self.frames[-1].kind != "function":
                # declared in a block: the block's own name, and where the code is not strict (Annex B) the function's too, which ripple
                # cannot read, so the name after the block is not taken for the module's own or for a builtin
                self.scope.bind(self._function_key(text(name)), Binding("var", evidence=[unknown_ref(BLOCK_FUNCTION)]))
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
        constructor = next((m for m in members if m.type == "method_definition" and self._member_key(m) == "constructor"), None)
        self.replaced |= self._replaced_members(members)
        self.own_accessors[scope.id] = {
            self._member_key(m)
            for m in members
            if m.type in ("method_definition", "abstract_method_signature")
            and m.id not in self.replaced
            and {"get", "set"} & {c.type for c in m.children}
            and "static" not in {c.type for c in m.children}  # a static accessor is on the class, which `this.x = v` in an instance does not reach
        }
        own_name = field_of(node, "name")
        self.class_names[scope.id] = {n for n in (name, text(own_name) if own_name is not None else "") if n}
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
            kids = {c.type for c in member.children}
            gone = member.id in self.replaced
            name = self._member_key(member)
            if not gone:
                self._own_writes(
                    member, cls, constructor=name == "constructor" and "static" not in kids, static="static" in kids,
                    where="the constructor" if name == "constructor" else f"the method {name}()" if name else "a method",
                )
            if name is None:
                return  # [Symbol.iterator]() and the like: no name to give it
            accessor = "get" if "get" in kids else "set" if "set" in kids else ""
            for d in [*decorators, *(c for c in member.children if c.type == "decorator")]:
                self._decorator(d, cls)
            self._parameter_decorators(member, cls)
            if gone:
                self._replaced_method(member, cls, name, "static" in kids)
            else:
                self._function(member, name, "method", accessor, static="static" in kids)
            if decorators or "decorator" in kids:
                cls.names[name] = Binding("var", evidence=[unknown_ref(DECORATED)])
        elif t == "abstract_method_signature":
            name = self._member_key(member)
            if name is not None:
                cls.bind(name, Binding("var", evidence=[unknown_ref(ABSTRACT)]))
        elif t == "public_field_definition":
            self._field(member, cls, decorators)
        elif t == "class_static_block":
            self._own_writes(member, cls, static=True, where="a static block")
            self.rebinds_this += 1
            self.generic(member)
            self.rebinds_this -= 1

    def _facts(self, cls: Scope) -> InstanceFacts:
        facts = getattr(cls, "instance_facts", None)
        if facts is None:
            facts = cls.instance_facts = InstanceFacts()
        return facts

    def _own_property(self, cls: Scope, name: str, static: bool = False, assigned: bool = False, how: str = "", bind: bool = True) -> None:
        """A field, a static field, a parameter property or `this.x = ...` is the object's own property: a member
        lookup stops at it and never reaches a method of that name further up. A static one is a property of the class
        itself, so a class below it that has a method of that name is not hidden by it (see Resolver.own_property_above).
        One that is `assigned` (`this.x = v`, `Object.assign`, `Reflect.set`) calls a setter that a class below declares for
        the name, where a field, a parameter property or `Object.defineProperty` defines the property over it.

        The class's own binding of the name (`bind`) is what stops its own lookup (`bind` would also make a setter-assigned name
        unknown, so it is skipped for one). What a class below this one must know, whatever its own bindings of the name are, is
        kept apart on the class as InstanceFacts, where `Scope.bind` cannot overwrite it: a class that both declares `m` and writes
        `this.m = f` has the one binding of `m` unknown, and the fact that every instance has its own `m` stays."""
        if bind:
            reason = STATIC_PROPERTY if static else OWN_ASSIGNED if assigned else OWN_PROPERTY
            cls.bind(name, Binding("var", evidence=[unknown_ref(reason)]))
        if not static:
            entries = self._facts(cls).own.setdefault(name, [])
            if (not assigned, how) not in entries:
                entries.append((not assigned, how))

    def _proto_write(self, cls: Scope, name: str) -> None:
        """The class body writes `name` on its own prototype (`C.prototype.m = f`, `this.prototype.m = f` in a static member): the
        method of that name is not the one declared, for this class and every class below it that does not declare its own."""
        cls.bind(name, Binding("var", evidence=[unknown_ref(PROTOTYPE_WRITE)]))

    def _replaced_members(self, members: list) -> set:
        """The tree-sitter ids of the methods and accessors of a class body that a later member of the same name (and the same
        side, static or instance) replaces, so that what the class ends up with is the last one. A later method replaces
        everything before it; a later getter replaces an earlier method and an earlier getter, and keeps an earlier setter (the
        two make one accessor), and a setter likewise. Run in Node: `m(){} m(){}`, `m(){} 'm'(){}` and `m(){} ['m'](){}` run the
        last, `get m(){} set m(v){}` is one accessor, and `m(){} get m(){}` leaves no method m."""
        slots: dict = {}
        gone: set = set()
        for member in members:
            key = self._member_key(member) if member.type == "method_definition" else None
            if key is None or key == "constructor":
                continue
            kids = {c.type for c in member.children}
            slot = slots.get((key, "static" in kids), {})
            kind = "get" if "get" in kids else "set" if "set" in kids else "method"
            if kind == "method":
                gone.update(slot.values())
                slots[(key, "static" in kids)] = {"method": member.id}
            else:
                other = "set" if kind == "get" else "get"
                gone.update(slot.get(x) for x in ("method", kind))
                slots[(key, "static" in kids)] = {kind: member.id, other: slot.get(other)} if slot.get(other) else {kind: member.id}
        gone.discard(None)
        return gone

    def _replaced_method(self, node, cls: Scope, name: str, static: bool) -> None:
        """A member that a later one of the same name replaces never runs. It is a symbol of its own (so a change to it maps to
        something that nothing calls and nothing reaches, and no test is picked for it), under an id no name finds, and its body
        is not read: code that never runs makes no calls, and it is not what `C.name` is."""
        n = 1
        while f"{cls.id}.{name}#replaced{n}" in self.scopes:
            n += 1
        self._enter(name, "method", node, scope_id=f"{cls.id}.{name}#replaced{n}", variable=False, static=static)

    def _member_key(self, member) -> str | None:
        """The name a method, accessor or signature is declared under, else None (`[Symbol.iterator]`, `[k]`). `m`, `'m'`, `["m"]`
        and `\\u006d` are the one name. A `["constructor"]` is a method of that name, never the constructor, so it has none here."""
        name = field_of(member, "name")
        key = property_key(name)
        return None if key == "constructor" and name.type == "computed_property_name" else key

    # ---- what a class body shows about the properties of its instances -----------------------------

    @staticmethod
    def _instance_walk(root):
        """Every node under `root` with whether `this` there is the `this` of root (an arrow function keeps it, any other function and
        any class has its own). A method, a function value or a static block is entered by its children; anything else, itself."""
        stack = [(child, True) for child in (root.children if root.type in FUNCTION_NODES or root.type == "class_static_block" else [root])]
        while stack:
            node, here = stack.pop()
            yield node, here
            kind = node.type
            if kind in FUNCTION_NODES:
                inside = here if kind == "arrow_function" else False
            else:
                inside = here and kind not in CLASS_NODES
            stack.extend((child, inside) for child in node.children)

    @staticmethod
    def _scope_of(declarator):
        """The node a name declared by this declarator can be read in: the function around a `var`, the block, `for`, `switch` or
        function around a `let` or `const`."""
        holder = declarator.parent
        var = holder is not None and holder.type == "variable_declaration"
        node = holder
        while node is not None:
            if node.type in FUNCTION_NODES or node.type == "program":
                return node
            if not var and node.type in ("statement_block", "for_statement", "for_in_statement", "switch_body", "class_static_block"):
                return node
            node = node.parent
        return declarator

    def _this_aliases(self, root) -> dict:
        """{name: the node it can be read in} for the names that hold the instance's `this` and nothing else, under `root` (a method, a
        constructor or a field initializer): declared exactly once in it, by a `const`, `let` or `var` (not a parameter, a function, a
        class, a catch clause or a pattern), and every write to it, the declaration's own and any `name = value` after, is the instance's
        `this` (written where `this` is the instance's, never in a function with a `this` of its own) or another such name. A name that
        is declared twice (a parameter or a block that shadows it), written another value, updated, destructured into or looped into is
        not the instance, and neither is a read of it outside the scope of its declaration. A name that cannot be shown to be the
        instance is not."""
        count: dict = {}
        sites: dict = {}
        writes: dict = {}
        bad: set = set()

        def declared(names, spoils=True):
            for n in names:
                count[n] = count.get(n, 0) + 1
                if spoils:
                    bad.add(n)

        for node, here in self._instance_walk(root):
            kind = node.type
            if kind == "variable_declarator":
                target, value = field_of(node, "name"), field_of(node, "value")
                if target is None:
                    continue
                names = self._pattern_names(target, visit_defaults=False)
                declared(names, spoils=target.type != "identifier")
                if target.type == "identifier":
                    sites[text(target)] = node
                    if value is not None:
                        writes.setdefault(text(target), []).append((value, here))
            elif kind in ("required_parameter", "optional_parameter"):
                pattern = field_of(node, "pattern")
                if pattern is not None and pattern.type != "this":
                    declared(self._pattern_names(pattern, visit_defaults=False))
            elif kind == "arrow_function" and field_of(node, "parameter") is not None:
                declared([text(field_of(node, "parameter"))])
            elif kind in (*FUNCTION_DECLARATIONS, "function_expression", "generator_function", "class_declaration", "abstract_class_declaration", "class"):
                name = field_of(node, "name")
                if name is not None:
                    declared([text(name)])
            elif kind == "catch_clause":
                parameter = field_of(node, "parameter")
                if parameter is not None:
                    declared(self._pattern_names(parameter, visit_defaults=False))
            elif kind == "for_in_statement":
                left = field_of(node, "left")
                if left is not None and field_of(node, "kind") is not None:
                    declared(self._pattern_names(left, visit_defaults=False))
                elif left is not None:
                    bad.update(text(leaf) for leaf in pattern_targets(left) if leaf.type in ("identifier", "shorthand_property_identifier_pattern"))
            elif kind == "assignment_expression":
                left, right = unwrap(field_of(node, "left")), field_of(node, "right")
                if left is not None and left.type == "identifier":
                    writes.setdefault(text(left), []).append((right, here))
                elif left is not None and left.type in ("object_pattern", "array_pattern"):
                    bad.update(text(leaf) for leaf in pattern_targets(left) if leaf.type in ("identifier", "shorthand_property_identifier_pattern"))
            elif kind in ("augmented_assignment_expression", "update_expression"):
                target = unwrap(field_of(node, "left" if kind == "augmented_assignment_expression" else "argument"))
                if target is not None and target.type == "identifier":
                    bad.add(text(target))
        aliases: dict = {}
        grown = True
        while grown:
            grown = False
            for name, site in sites.items():
                if name in aliases or name in bad or count.get(name) != 1 or not writes.get(name):
                    continue
                if all(self._holds_this(value, here, aliases) for value, here in writes[name]):
                    aliases[name] = self._scope_of(site)
                    grown = True
        return aliases

    @staticmethod
    def _holds_this(value, here: bool, aliases: dict) -> bool:
        value = unwrap(value)
        return value is not None and ((value.type == "this" and here) or (value.type == "identifier" and text(value) in aliases))

    def _own_writes(self, root, cls: Scope, constructor: bool = False, static: bool = False, where: str = "") -> None:
        """Read a member of a class body (a method, a constructor, an accessor, a field initializer, a static block) for what it
        writes on the instance, or on the class's own prototype, with a name that is written out, and record it on the class.

        On the instance (an instance member; `this`, `super`, or a name that is the instance's `this` and nothing else, see
        _this_aliases): `this.k =`, `this['k'] =`, `this.k++`, `this.k += 1`, a destructuring assignment into `this.k`,
        `for (this.k of ...)`, `Object.assign(this, {k})` (also through a spread of an object literal),
        `Object.defineProperty(this, 'k', ...)`, `Object.defineProperties`, `Reflect.set` and `Reflect.defineProperty` are an own
        property k, which hides a method k that the class (or a class below it) writes. `Object.setPrototypeOf(this, ...)` and
        `this.__proto__ =` give the instance another prototype, so no method of the class is provable. A constructor that returns
        something other than `this` or a primitive may hand `new C()` another object altogether.

        On the prototype: the same writes through `C.prototype`, with `C` the class's own name, anywhere in its body, and through
        `this.prototype` in a static member or a static block, where `this` is the class. The method of that name is then not the one
        the class declares. A prototype reached any other way (a value, an alias, another file) is not read.

        What stays unread, because the name or the object is only known when the code runs or the write is in code that this
        one hands `this` to: a key that is not a literal (`this[k] = v`, `Object.assign(this, options)`), a call that is
        given `this` (`install(this)`), a `this` inside a function that is not an arrow, a write through a name that may not be
        the instance, and a write to a prototype that is not named as above. The answer for those is the one ripple always gave."""
        if constructor:
            self._constructor_returns(root, cls)
        source = text(root)
        class_names = self.class_names.get(cls.id, ())
        if "this" not in source and "super" not in source and not any(n in source for n in class_names):
            return
        aliases = {} if static else self._this_aliases(root)
        accessors = self.own_accessors.get(cls.id, ())

        def instance(node, here: bool) -> bool:
            node = unwrap(node)
            if node is None or static:
                return False
            if node.type in ("this", "super"):
                return here
            if node.type == "identifier":
                scope = aliases.get(text(node))
                return scope is not None and scope.start_byte <= node.start_byte and node.end_byte <= scope.end_byte
            return False

        def prototype(node, here: bool) -> bool:
            node = unwrap(node)
            if node is None or node.type != "member_expression":
                return False
            prop, obj = field_of(node, "property"), unwrap(field_of(node, "object"))
            if prop is None or prop.type != "property_identifier" or text(prop) != "prototype" or obj is None:
                return False
            return (obj.type == "this" and here and static) or (obj.type == "identifier" and text(obj) in class_names)

        def literal_key(argument):
            argument = unwrap(argument)
            return property_key(argument) if argument is not None and argument.type in ("string", "template_string", "number") else None

        def key_of(target):
            """(the object, the literal key or None) of a member or subscript expression."""
            if target.type == "member_expression":
                prop = field_of(target, "property")
                return field_of(target, "object"), (property_key(prop) if prop is not None and prop.type == "property_identifier" else None)
            return field_of(target, "object"), literal_key(field_of(target, "index"))

        def swapped() -> None:
            facts = self._facts(cls)
            facts.swapped = facts.swapped or where

        def own(key, assigned: bool) -> None:
            if key is not None and key != "__proto__" and not key.startswith("#"):
                self._own_property(cls, key, assigned=assigned, how=f"{where} writes it on the instance", bind=not (assigned and key in accessors))

        def proto(key) -> None:
            if key == "__proto__":
                swapped()
            elif key is not None and not key.startswith("#"):
                self._proto_write(cls, key)

        def written(target, here: bool) -> None:
            target = unwrap(target)
            if target is None or target.type not in ("member_expression", "subscript_expression"):
                return
            obj, key = key_of(target)
            if instance(obj, here):
                swapped() if key == "__proto__" else own(key, True)
            elif prototype(obj, here):
                proto(key)

        def literal_keys(argument, depth: int = 0) -> list:
            argument = unwrap(argument)
            keys = []
            for child in named(argument) if argument is not None and argument.type == "object" else []:
                if child.type == "pair":
                    keys.append(property_key(field_of(child, "key")))
                elif child.type == "shorthand_property_identifier":
                    keys.append(text(child))
                elif child.type == "method_definition":
                    keys.append(property_key(field_of(child, "name")))
                elif child.type == "spread_element" and depth < 4:
                    keys += literal_keys(next(iter(named(child)), None), depth + 1)  # `...{ m: f }` is `m: f`; any other spread is not read
            return keys

        def rebinds(left, right, here: bool) -> bool:
            """`this.m = this.m.bind(this)` (through `this`, a name that is the instance, or a literal subscript): the property is the
            method itself, bound, so it hides nothing."""
            left, right = unwrap(left), unwrap(right)
            if left is None or right is None or left.type not in ("member_expression", "subscript_expression") or right.type != "call_expression":
                return False
            obj, key = key_of(left)
            function = unwrap(field_of(right, "function"))
            if key is None or not instance(obj, here) or function is None or function.type != "member_expression":
                return False
            prop, held = field_of(function, "property"), unwrap(field_of(function, "object"))
            if prop is None or text(prop) != "bind" or held is None or held.type not in ("member_expression", "subscript_expression"):
                return False
            held_obj, held_key = key_of(held)
            arguments = named(field_of(right, "arguments")) if field_of(right, "arguments") is not None else []
            return held_key == key and instance(held_obj, here) and len(arguments) == 1 and instance(arguments[0], here)

        for node, here in self._instance_walk(root):
            kind = node.type
            if kind in ("assignment_expression", "augmented_assignment_expression"):
                left, right = field_of(node, "left"), field_of(node, "right")
                if kind == "augmented_assignment_expression" or not rebinds(left, right, here):
                    for leaf in pattern_targets(unwrap(left)) if left is not None else []:
                        written(leaf, here)
            elif kind == "update_expression":
                written(field_of(node, "argument"), here)
            elif kind == "for_in_statement" and field_of(node, "kind") is None and field_of(node, "left") is not None:
                for leaf in pattern_targets(unwrap(field_of(node, "left"))):
                    written(leaf, here)
            elif kind == "call_expression":
                function, arguments = field_of(node, "function"), field_of(node, "arguments")
                callee = ".".join(dotted(function) or []) if function is not None else ""
                args = named(arguments) if arguments is not None else []
                if callee not in WRITES_TO_AN_OBJECT or not args:
                    continue
                on_instance = instance(args[0], here)
                if not on_instance and not prototype(args[0], here):
                    continue
                if callee in ("Object.setPrototypeOf", "Reflect.setPrototypeOf"):
                    swapped()
                elif callee == "Object.assign":
                    for argument in args[1:]:
                        for key in literal_keys(argument):
                            own(key, True) if on_instance else proto(key)
                elif callee == "Object.defineProperties" and len(args) > 1:
                    for key in literal_keys(args[1]):
                        own(key, False) if on_instance else proto(key)
                elif len(args) > 1:  # Object.defineProperty, Reflect.defineProperty, Reflect.set
                    key = literal_key(args[1])
                    own(key, callee == "Reflect.set") if on_instance else proto(key)

    def _constructor_returns(self, constructor, cls: Scope) -> None:
        """Mark the class when its constructor has a `return` of something that is not `this` and not a primitive: `new C()` is then
        that object, whatever the class writes. A `return` in a function or class inside the constructor is not the constructor's."""
        pending = [field_of(constructor, "body")]
        while pending:
            node = pending.pop()
            if node is None or node.type in FUNCTION_NODES or node.type in CLASS_NODES:
                continue
            if node.type == "return_statement":
                value = next(iter(named(node)), None)
                value = unwrap(value)
                if value is not None and not (
                    value.type in PRIMITIVE_VALUES or value.type == "this" or (value.type == "identifier" and text(value) == "undefined")
                ):
                    facts = self._facts(cls)
                    facts.returns = facts.returns or "the constructor"
                    return
                continue
            pending.extend(node.children)

    def _field(self, member, cls: Scope, decorators: list) -> None:
        """A field. Its type is recorded for `this.field.method()`; its initializer runs when the class is
        constructed (or defined, for a static one), so its calls belong to the constructor or the scope around."""
        name_node, type_node, value = field_of(member, "name"), field_of(member, "type"), field_of(member, "value")
        static = any(c.type == "static" for c in member.children)
        if value is not None:
            self._own_writes(value, cls, static=static, where="a static field initializer" if static else "a field initializer")
        name = property_key(name_node)
        if name is None:
            return
        if not any(c.type in ("declare", "abstract") for c in member.children):  # tsc emits no property for either
            self._own_property(cls, name, static, how="it is a field")
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

    def visit_variable_declaration(self, node) -> None:
        """`var`: its names belong to the function (see `_var_key`)."""
        for declarator in named(node):
            if declarator.type == "variable_declarator":
                self._declarator(declarator, var=True)

    def _declarator(self, node, var: bool = False) -> None:
        target, type_node, value = field_of(node, "name"), field_of(node, "type"), field_of(node, "value")
        if target is None:
            return
        if target.type == "identifier":
            name = text(target)
            if value is not None and inner(value).type in FUNCTION_VALUES:
                self._function(inner(value), name, var=var)  # const f = () => ...: the function is named f
                return
            if value is not None and inner(value).type == "call_expression" and self._wrapped_component(name, inner(value)):
                return
            if value is not None:
                self.visit(value)
            if type_node is not None:
                evidence = self._annotation_evidence(type_node, self.scope.id)
            else:
                evidence = self._evidence(value)
            self._bind(name, evidence, var=var)
        else:
            if value is not None:
                self.visit(value)
            for name in self._pattern_names(target):
                self._bind(name, [unknown_ref(DESTRUCTURED)], var=var)

    # ---- statements that bind a name ------------------------------------------------------

    def visit_statement_block(self, node) -> None:
        self._push(self._declared_names(named(node)), fresh=True, erased=self._erased_names(named(node)), redeclared=self._redeclared(named(node)))
        self._bind_type_names(named(node))
        self.generic(node)
        self._pop()

    def visit_import_alias(self, node) -> None:
        """`import a = N.M` binds `a` (TypeScript emits `var a = N.M`, a name of the namespace body or the file): what it holds is not followed, but
        it is no builtin or global of that name, so a call through it is never outside the repo."""
        name = declared_name(node)
        if name is not None:
            self.scope.bind(self._name(name), Binding("var", evidence=[unknown_ref(IMPORT_ALIAS)]))

    def visit_switch_body(self, node) -> None:
        """Every case of a switch shares one block."""
        statements = [stmt for case in named(node) for stmt in field_nodes(case, "body")]
        self._push(self._declared_names(statements), fresh=True, erased=self._erased_names(statements), redeclared=self._redeclared(statements))
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
        is_var = kind is not None and text(kind) == "var"
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
                    self._assign_name(text(left), [unknown_ref(LOOP_VARIABLE)], var=is_var)
            elif left.type in ("object_pattern", "array_pattern"):
                for name in self._pattern_names(left):
                    if declares:
                        self._bind(name, [unknown_ref(DESTRUCTURED)])
                    else:
                        self._assign_name(name, [unknown_ref(DESTRUCTURED)], var=is_var)
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
        self._push(names, fresh=True, kind="catch" if parameter is not None and parameter.type == "identifier" else "block")
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
        chain = dotted(left) if left is not None else None
        if chain and (chain[:2] == ["module", "exports"] or (chain[0] == "exports" and len(chain) > 1)):
            self.module.commonjs = True
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
        if own:  # this.x = v calls the setter when x is an accessor of the class: then it is no binding of x here, but still a fact below
            method = scope.id.rsplit(".", 1)[-1]
            where = "the constructor" if method == "constructor" else f"the method {method}()"
            self._own_property(cls, attr, assigned=True, how=f"{where} writes it on the instance", bind=attr not in self.own_accessors.get(cls.id, ()))

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

    def _after_error(self, node) -> bool:
        """Whether the call is inside a syntax error node, or its statement comes right after one. A name the grammar
        takes for a TypeScript keyword breaks the statement that starts with it: `abstract.foo();` is an error holding
        `abstract.` and then the call `foo()`, which no JavaScript text makes."""
        if not self.damaged:
            return False
        cursor = node
        while cursor is not None and cursor.type not in STATEMENT_END:
            if cursor.type == "ERROR" or (cursor.prev_sibling is not None and cursor.prev_sibling.type == "ERROR"):
                return True
            cursor = cursor.parent
        return False

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
        typed = self.js and field_of(node, "type_arguments") is not None
        refused = typed or (self.js and self._after_error(node))
        if refused:
            # The TypeScript grammar reads `f < a > (b)` as the call f<a>(b); in a .js file it is two comparisons. It also
            # reads a name it takes for a keyword (`abstract.foo()`) as an error and a call of what follows.
            call = RawCall(None, short(func), line, special="computed", why=JS_TYPE_ARGUMENTS if typed else JS_AFTER_ERROR)
        elif func.type == "super":
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
                self._own_exports(call, chain)
            else:
                path = call_path(func)
                if path and self.rebinds_this and path[0] == "this":
                    call = RawCall(None, short(func), line, special="computed", why=THIS_REBOUND)
                elif path:
                    call = RawCall(None, short(func), line, path=self._ref(path))
                    self._own_exports(call, path)
                else:
                    why = COMPUTED_SUBSCRIPT if func.type == "subscript_expression" else COMPUTED_OTHER
                    call = RawCall(None, short(func), line, special="computed", why=why)
        if call is not None:
            here.calls.append(call)
            self.call_nodes[node.id] = call
            if not refused:
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
            call = RawCall(self._ref(chain), ".".join(chain)[:80], line)
            self.scope.calls.append(call)
            self._own_exports(call, chain)
        elif constructor is not None:
            path = call_path(constructor)
            if path and self.rebinds_this and path[0] == "this":
                self.scope.calls.append(RawCall(None, short(constructor), line, special="computed", why=THIS_REBOUND))
            elif path:
                call = RawCall(None, short(constructor), line, path=self._ref(path))
                self.scope.calls.append(call)
                self._own_exports(call, path)
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
        if chain:
            self._own_exports(element, chain)
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
        return self.modules.jsx_runtime(self.file)

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
    # change what ripple can say, and neither may be silent. The exception is a dual-format JavaScript package
    # (x.js beside x.mjs): that is one source shipped twice, read in full under its own id, and cli.py says so apart.
    errors += [rel for rel in modules.shadowed if rel in {p.relative_to(repo).as_posix() for p in readable} and rel not in modules.dual_format]
    errors += modules.problems  # the configs (the root one is tsconfig.json) that could not be read
    for path in readable:
        rel = path.relative_to(repo).as_posix()
        try:
            # JSX is legal in a .js file, so every JavaScript suffix goes to the tsx grammar, which reads plain JavaScript as well
            tree = (tsx if path.suffix == ".tsx" or path.name.endswith(JS_SUFFIXES) else typescript).parse(path.read_bytes())
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


def reads_as_source(path: Path, profile: LanguageProfile, repo: Path | None = None) -> bool:
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
