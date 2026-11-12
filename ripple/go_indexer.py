"""The Go front end: read .go files with tree-sitter-go and write the call graph straight into model.Graph.

Go is not run through the shared resolver (resolver.py). That resolver models classes: a call to a typed
receiver also reaches every subclass that overrides the method, calling a class runs its constructor, and a name
bound twice is unknown. Go has none of that: a struct has no subclasses, `T(x)` is a conversion that runs no
code, and `x, err := f()` keeps the static type of every variable it redeclares. Measured on seven public
corpora, the shared rules would have added hundreds of wrong override edges and turned thousands of conversions
into constructor edges. So Go gets a small resolver of its own, built on one fact: a variable's static type
never changes after its declaration, and a method called on a value of a concrete named type has exactly one
target, the method of that type's method set (found by the spec's depth rule through embedded fields).

Symbol ids are import paths (go_modules.py): a package is `github.com/spf13/cobra`, a function
`github.com/spf13/cobra:Execute`, a type `github.com/spf13/cobra:Command`, a method `github.com/spf13/cobra:Command.Execute`
(value and pointer receivers are one method set, one id). Every file of a directory folds into one package id, the
files of an external test package (`package foo_test`) into `foo_test`'s own. As in the TypeScript ids, `#N`
(`init#2`, `platform#2`) is the only suffix: the second declaration of a name that the language allows more than once.

What is proven (an edge), each by what the source writes:
  - a package-level function called by name, or through an import of a repo package, when no local binds the name;
  - a method called on a value whose static type is written in the source and is a repo type: a receiver, a
    parameter, a named result, `var x T`, `x := T{}`, `&T{}`, `new(T)`, `NewT()` (by the callee's declared results),
    a struct field with a declared type, an element of a slice, array or map, a range variable, a type assertion
    or the single type of a type switch case, a channel receive, a conversion, a method expression, and chains of these;
  - a method promoted through embedded structs, by the shallowest depth, unless an embedded type from outside the repo
    sits shallower and could provide the method first;
  - `go f()` and `defer f()` (they run f). A function literal is anonymous: its calls belong to the named function
    around it, and package-level initialisers belong to the package (the module symbol).
Conversions and calls of a universe builtin are not edges. A method declared on an alias (`type A = B`, `func (A) M()`)
is B's own method and keeps the id `A.M`, which is the compiler's. Build constraints are never evaluated (that would
guess the target): a name defined more than once across files of a package has an edge to every variant when it is a
function or method, and anything that depends on WHICH definition wins is unresolved: a variable or a type, a variable
or a result whose variants read their imports differently, and a method declared only in some builds (a constraint, a
GOOS or GOARCH file name, a `_test.go` file) while another method of the name is promoted, or the type is declared twice.
A method declared on an alias that is declared twice (or that no file ripple read declares) may be a method of any type
of the package, depending on the build: that name is unresolved on every type of the package that does not declare it.

Everything else is unresolved with its own reason, never guessed: an interface method call (the reason names the
interface), a function value (a parameter, a local, a field, a package variable, a result), a type parameter, a value
whose type is not written, a method of an outside type that a repo type also declares (the outside type may be an
interface it implements), and a name no scope ripple read declares. A call proven to leave the repo is counted as
external with its reason, and the proof is the source of the standard library, which go_stdlib.py holds (generated from
GOROOT by bench/go_stdlib): a func declaration of a standard library package, a method of a standard library type that
is not an interface, a method of a standard library interface that no repo type declares, a universe builtin. Ripple
does not read any other outside package, and `pkg.F(x)` may be a function or a variable that holds one, `v.F(x)` a
method or a struct field of function type, and a variable or field can hold a function of the repo: so a member of a
package or type outside the repo that the table does not prove (every non-standard-library one, a variable like
`flag.Usage`, a function-typed field like `http.Transport.DialContext`, a value whose type ripple cannot write) is
unresolved with that reason, never external. A call into C (`C.f(x)`) is unresolved too: C code may call any
`//export`-ed function of the repo.

A file tree-sitter could not read completely is listed in parse_errors. When the broken code is inside a function
body (the header read, the body closed, and the body's braces, read with Go's lexical rules whatever the layout, close
it at its last byte and not earlier: nothing the compiler would see as a declaration follows inside it), the function
keeps its symbol and its calls are unresolved ("tree-sitter could not read ..."), and the rest of the file is read as
usual. When it is anywhere else (a package-level declaration, a missing brace, braces that close before the node
ends), tree-sitter's recovery swallows the declarations after it, so the file may hold names and methods ripple never
saw. Its package is then `unreadable`: a package-level name (a function, variable, type) or a method of a type of the
package is proven only when a declaration of it is built wherever the call is (no build constraint and no platform
file name: a second declaration in the unreadable file would be a redeclaration), otherwise it is unresolved with a
reason naming the file, and so is everything that a type declared only in constrained files would decide. A promoted
method or a missing member is unresolved too, a universe name (`len`, `error`) is unresolved because the file may
redeclare it, and a call of a method of an outside type is not external when the file's text has a method of that
name. A file with no package clause or no tree is the same for every package of its directory. Go 1.26 `new(expr)` and
generic aliases are the cases tree-sitter-go 0.25 cannot parse. Generated files are read: they compile into the
package and the repo calls into them. Go files below `vendor`, `testdata` and `_x` directories are not read (the go
tool skips them) and the walk lists those directories with the skipped ones.

tree-sitter is the optional extra `pip install 'ripple-code[go]'`, imported when the first Go file is parsed, never
when ripple is imported: a missing parser raises GoNotInstalled, which says so.
"""

from __future__ import annotations

import functools
import itertools
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from .go_modules import MISSING, OWN, PUBLISHED, REPLACE, WORK, Modules, is_ignored_dir
from .model import Edge, Graph, Symbol, Unresolved
from .profile import GO, GO_BUILTIN_FUNCTIONS, GO_BUILTIN_TYPES, GO_BUILTIN_VALUES, LanguageProfile

INSTALL_HINT = "pip install 'ripple-code[go]'"
REINSTALL = f"it needs tree-sitter>=0.25,<0.27 and tree-sitter-go>=0.25,<0.26, which {INSTALL_HINT} installs"


class GoNotInstalled(ImportError):
    """tree-sitter or its Go grammar is missing. The message says what to install."""


_PARSER = None


def load_parser():
    """The Go parser. tree-sitter is imported here, the first time it is needed."""
    global _PARSER
    if _PARSER is None:
        try:
            import tree_sitter_go as grammar
            from tree_sitter import Language, Parser
        except ModuleNotFoundError as error:
            raise GoNotInstalled(
                f"Go support is not installed (missing module: {error.name}); install it with: {INSTALL_HINT}"
            ) from error
        except ImportError as error:
            raise GoNotInstalled(
                f"Go support cannot be loaded (a tree-sitter that does not fit: {error}); {REINSTALL}"
            ) from error
        try:
            _PARSER = Parser(Language(grammar.language()))
        except Exception as error:  # noqa: BLE001 (an ABI mismatch is a ValueError, an old API a TypeError, a bad library an OSError)
            raise GoNotInstalled(
                f"Go support cannot be loaded (tree-sitter and its grammar do not fit: "
                f"{type(error).__name__}: {error}); {REINSTALL}"
            ) from error
    return _PARSER


# ---- the reasons a call is unresolved or external ----------------------------------------------

DAMAGED = "tree-sitter could not read all of this function (a Go syntax it does not know), so what its names mean is not followed"
UNDECLARED = "the name is not declared in the files of its package that ripple read (a build constraint, cgo, or generated code can hide it)"
OTHER_PACKAGE_NAME = "the name is not declared in the imported package's files that ripple read"
CONFLICT = "the name is declared more than once with different meanings (build constraints), and which one wins is not known"
BUILD_VARIANT_TYPE = "the type is declared more than once with different meanings (build constraints), and which one wins is not known"
FLOATING_METHOD = (
    "a method of this name is declared on a name that may be an alias of any type of the package (an alias declared more "
    "than once, or one ripple could not read), so which type has it depends on the build"
)
BUILD_VARIANT_VAR = "the receiver is a package variable declared differently in several files (build constraints), and which declaration wins is not known"
PACKAGE_VARIABLE = "the callee is a package-level variable holding a function value"
PARAMETER = "the callee is a parameter holding a function value (a callback)"
LOCAL_FUNCTION = "the callee is a local holding a function value"
FIELD_FUNCTION = "the callee is a struct field holding a function value"
RETURNED_FUNCTION = "the callee is a function returned by a call"
INDEXED_FUNCTION = "the callee is looked up by index (a map or slice of functions)"
TYPE_PARAMETER = "the receiver's type is a type parameter: the call goes to whatever type argument is given"
OUTSIDE_MAY_BE_INTERFACE = (
    "the receiver's type is from outside the repo and may be an interface, and a repo type declares a method of this name"
)
BUILD_VARIANT_RESULT = "the receiver is the result of a function declared more than once with different results (build constraints), and which declaration wins is not known"
NO_SUCH_MEMBER = "the type has no method or field of this name in the files ripple read (a build constraint, cgo, or generated code can hide it)"
SHALLOWER_OUTSIDE = "a type embedded from outside the repo, at a shallower depth, may provide this method first"
TWO_EMBEDDED = "two embedded types at one depth provide this name"
UNREADABLE_TYPE = "the receiver's type is declared in a way ripple does not read"
REFLECTION = "a method called Call or CallSlice of a type from outside the repo may be reflect.Value's, which runs a function chosen at run time"
UNBOUND = "the receiver is a name no scope ripple read declares (an import whose package name ripple cannot read, or a binding it missed)"
MISSING_IMPORT = "the import is inside a module of this repo but names no package directory ripple read"
BUILTIN = "a Go builtin"
STDLIB_FUNCTION = "a function declared by a standard library package"
DOT_OUTSIDE = "a function of a standard library package, named by a dot import"
STDLIB_METHOD = "a method of a standard library type that is not an interface"
OUTSIDE_METHOD = "a method of a standard library interface which no repo type declares"
PROMOTED_OUTSIDE = "a method promoted from an embedded standard library type"
# What ripple cannot prove of anything outside the repo: it does not read those packages, so `pkg.F(x)` may be a
# function or a package variable that holds one, and `v.F(x)` a method or a struct field of function type, and a
# variable or field can hold a function of the repo. Only the standard library, whose source the table in go_stdlib.py
# was generated from, can prove a name is a function or a method.
OUTSIDE_PACKAGE_NAME = (
    "the callee is a member of a package outside the repo that ripple does not read: it may be a function or a package "
    "variable holding a function (which may hold repo code)"
)
STDLIB_NOT_FUNCTION = "the callee is a name of a standard library package that is not a function declaration there (a variable holding a function, or a name ripple cannot prove)"
PUBLISHED_PACKAGE_NAME = (
    "the callee is a member of another module of this repo built from its published version (no replace or go.work): ripple does not "
    "read that version, so it may be a function or a package variable holding a function"
)
OUTSIDE_TYPE_MEMBER = (
    "the receiver is a value of a type ripple has no member table for (outside the repo and not the standard library, or a "
    "result it cannot type): the callee may be a method or a struct field holding a function (which may hold repo code)"
)
STDLIB_NOT_MEMBER = "the standard library type has no method or function-typed field of this name that ripple can prove"
UNPROVEN_PROMOTED = (
    "the name may come from an embedded type outside the repo as a method or as a struct field holding a function (which may hold repo code)"
)
CGO = "cgo: a C function, which may call back into an //export-ed function of the repo"
BUILD_VARIANT_METHOD = (
    "the method is declared only in some builds (a build constraint, a platform file or a _test.go file) "
    "and the name is also promoted from an embedded type, so which one runs depends on the build"
)
UNREAD_FILE = "tree-sitter could not read all of {files}, which may declare a method or a name that changes what this call is"
UNREAD_REPO = "tree-sitter could not read all of {files}, which may declare a method of this name"

UNIVERSE_FUNCTIONS = frozenset(GO_BUILTIN_FUNCTIONS)
UNIVERSE_TYPES = frozenset(GO_BUILTIN_TYPES)
UNIVERSE_VALUES = frozenset(GO_BUILTIN_VALUES)
UNIVERSE_INTERFACES = frozenset({"error", "any", "comparable"})
NO_NAMES: frozenset[str] = frozenset()
UNIVERSE_MEMBERS = {"error": ("i", frozenset({"Error"}), NO_NAMES)}  # the one universe type with a method (`any` has none: a call on it is unresolved)
RECEIVER_LIMIT = 8  # how many embedding levels a method is looked for through

GENERATED = re.compile(rb"^// Code generated .* DO NOT EDIT\.$", re.M)
PACKAGE_CLAUSE = re.compile(rb"^[ \t]*package\b", re.M)
BUILD_LINE = re.compile(rb"^//go:build (.*)$", re.M)
PLUS_BUILD_LINE = re.compile(rb"^// \+build (.*)$", re.M)
KNOWN_OS = frozenset(
    "aix android darwin dragonfly freebsd hurd illumos ios js linux nacl netbsd openbsd plan9 solaris wasip1 windows zos".split()
)
KNOWN_ARCH = frozenset(
    "386 amd64 amd64p32 arm armbe arm64 arm64be loong64 mips mipsle mips64 mips64le mips64p32 mips64p32le ppc ppc64 ppc64le "
    "riscv riscv64 s390 s390x sparc sparc64 wasm".split()
)
DECLARATIONS = frozenset(
    ("package_clause", "import_declaration", "function_declaration", "method_declaration", "type_declaration", "var_declaration", "const_declaration")
)
# The method names a file ripple could not parse may declare, read off its text with the comments out (a receiver may hold
# one parenthesis level, `(s S[func()])`).
METHOD_NAME = re.compile(rb"\bfunc\s*\((?:[^()]|\([^()]*\))*\)\s*([A-Za-z_]\w*)")
# What Go's lexer does not read as code: a comment, an interpreted or raw string, a rune. An unterminated one runs to the
# end of its line (the end of the file for a block comment or a raw string), as the compiler's error recovery reads it.
NOT_CODE = re.compile(rb'//[^\n]*|/\*.*?(?:\*/|\Z)|"(?:[^"\\\n]|\\.)*(?:"|(?=\n)|\Z)|`[^`]*(?:`|\Z)|\'(?:[^\'\\\n]|\\.)*(?:\'|(?=\n)|\Z)', re.S)
BRACE = re.compile(rb"[{}]")


# ---- the standard library table (go_stdlib.py, generated from the library's own source) ---------


@functools.lru_cache(maxsize=None)
def stdlib_funcs(path: str) -> frozenset[str] | None:
    """The func declarations of a standard library package, None when the path is not one."""
    from . import go_stdlib

    return frozenset(go_stdlib.FUNCS.get(path, "").split()) if path in go_stdlib.PACKAGES else None


@functools.lru_cache(maxsize=None)
def outside_type(written: str):
    """(kind, methods, function-typed fields) of the type `import/path.Name` a value of ("ext", written) has, when it is
    a standard library type or the universe's `error`, None when ripple has no table for it (written is also the shape of
    the value `pkg.Func`, which is no type at all, and that is no table entry either)."""
    from . import go_stdlib

    if written in UNIVERSE_MEMBERS:
        return UNIVERSE_MEMBERS[written]
    path, _, name = written.rpartition(".")
    found = go_stdlib.TYPES.get(path, {}).get(name)
    return (found[0], frozenset(found[1].split()), frozenset(found[2].split())) if found else None


# ---- small readers of tree-sitter nodes ---------------------------------------------------------


def text(node) -> str:
    return node.text.decode("utf-8", "replace")


def uncommented(data: bytes) -> bytes:
    """The source with every comment a space: what the compiler reads, whatever the layout and the comments around it."""
    return NOT_CODE.sub(lambda found: b" " if found.group(0).startswith(b"/") else found.group(0), data)


def closes_only_at_end(body: bytes) -> bool:
    """Whether a `{ ... }` block, read with Go's lexical rules (a brace inside a string, rune or comment is no brace), is
    closed by its last byte and by no earlier one: nothing that follows the block's own closing brace is inside it."""
    code = NOT_CODE.sub(lambda found: b" " * len(found.group(0)), body)
    depth = 0
    for brace in BRACE.finditer(code):
        depth += 1 if brace.group(0) == b"{" else -1
        if depth <= 0:
            return depth == 0 and brace.end() == len(code.rstrip())
    return False


def code(node) -> str:
    """A node's text on one line without the comments inside it: two spellings of one construct are one text whatever
    layout and comments sit between its tokens."""
    comments = []
    stack = [node]
    while stack:
        current = stack.pop()
        if current.type == "comment":
            comments.append((current.start_byte, current.end_byte))
        else:
            stack.extend(current.children)
    data, base, out, at = node.text, node.start_byte, [], 0
    for start, end in sorted(comments):
        out.append(data[at : start - base])
        at = end - base
    out.append(data[at:])
    return " ".join(b"".join(out).decode("utf-8", "replace").split())


def field_of(node, name):
    return node.child_by_field_name(name)


def fields_of(node, name):
    return node.children_by_field_name(name)


def named(node) -> list:
    return [c for c in node.children if c.is_named and c.type != "comment"]


def line_of(node) -> int:
    return node.start_point[0] + 1


def shown(node) -> str:
    """A callee as written, on one line and no longer than a reason can carry."""
    words = code(node)
    return words if len(words) <= 80 else words[:77] + "..."


def specs(declaration, kind: str) -> list:
    """The `kind` specs of a var, const or type declaration, grouped in parentheses or not."""
    found = []
    for child in named(declaration):
        if child.type == kind:
            found.append(child)
        elif child.type.endswith("_spec_list"):
            found += [s for s in named(child) if s.type == kind]
    return found


def unwrap(node, kinds=("parenthesized_type", "parenthesized_expression", "pointer_type")):
    """The node inside parentheses and pointer marks, None when there is nothing inside."""
    while node is not None and node.type in kinds:
        inner = named(node)
        node = inner[0] if inner else None
    return node


def receiver_name(param) -> str | None:
    """The type name a method's receiver is declared on: `Cart` for `(c *Cart)`, `S` for `(s S[T])`."""
    node = unwrap(field_of(param, "type")) if param is not None else None
    if node is not None and node.type == "generic_type":
        node = field_of(node, "type")
    return text(node) if node is not None and node.type == "type_identifier" else None


def type_parameter_names(declaration) -> frozenset[str]:
    parameters = field_of(declaration, "type_parameters")
    return frozenset(text(n) for d in named(parameters) for n in fields_of(d, "name")) if parameters is not None else frozenset()


def embedded_name(node) -> str:
    """The field name an embedded type gives: `Base` for `Base`, `*Base`, `pkg.Base` and `Base[T]`."""
    node = unwrap(node, ("pointer_type",))
    if node.type == "generic_type":
        node = field_of(node, "type")
    return text(field_of(node, "name")) if node.type == "qualified_type" else text(node)


# ---- what is declared ---------------------------------------------------------------------------


@dataclass
class Import:
    kind: str  # "repo", "outside", "published", "missing" or "cgo"
    path: str
    package: Package | None = None


@dataclass
class FileInfo:
    rel: str
    package: Package
    constrained: bool = False  # built on some builds only: a //go:build line, a GOOS or GOARCH file name, or a _test.go file
    tests_only: bool = False  # constrained by being a _test.go file and nothing else: built exactly when every other _test.go file is
    imports: dict[str, Import] = field(default_factory=dict)  # the names a file's imports bind (alias, or a repo package's name)
    guesses: dict[str, list[Import]] = field(default_factory=dict)  # outside packages, by the name their path suggests
    dots: list[Import] = field(default_factory=list)


@dataclass
class FuncDecl:
    id: str
    name: str
    file: FileInfo
    node: object
    tparams: frozenset[str]
    owner: TypeInfo | None = None


@dataclass
class VarInfo:
    type_node: object
    value: object
    file: FileInfo
    conflict: bool = False  # declared in several files with different type or value: no one declaration is the variable


class TypeInfo:
    """A named type of a package, with what a method lookup needs: its methods, fields and embedded types."""

    def __init__(self, package: Package, name: str):
        self.package, self.name = package, name
        self.id = f"{package.id}:{name}"
        self.kind = "undeclared"  # struct, interface, alias, named (`type T U`), other; undeclared: only methods were read
        self.declarations = 0
        self.aliased = False  # some declaration of the name is an alias: which type it names may depend on the build
        self.node = None
        self.file: FileInfo | None = None
        self.target = None  # the type node an alias or `type T U` names
        self.tparams: frozenset[str] = frozenset()
        self.fields: dict[str, object] = {}  # field name -> type node (an embedded type is a field named as its type)
        self.embedded: list = []  # type nodes
        self.interface_methods: dict[str, object] = {}
        self.interface_embedded: list = []
        self.methods: dict[str, list[FuncDecl]] = {}  # more than one declaration of a name only under build constraints
        self.damaged = False

    @property
    def ambiguous(self) -> bool:
        return self.declarations > 1


class Package:
    def __init__(self, ident: str, name: str, directory: str):
        self.id, self.name, self.dir = ident, name, directory
        self.files: list[FileInfo] = []
        self.funcs: dict[str, list[FuncDecl]] = defaultdict(list)
        self.types: dict[str, TypeInfo] = {}
        self.vars: dict[str, VarInfo] = {}
        self.names: dict[str, set[str]] = defaultdict(set)  # every package-level name -> what it is declared as
        self.declared_in: dict[str, list[FileInfo]] = defaultdict(list)  # every package-level name -> the files that declare it
        self.floating: set[str] = set()  # method names that may belong to any type of the package (see Resolver.float_methods)
        self.symbols: list[Symbol] = []
        self.inits: list[FuncDecl] = []
        self.initialisers: list[tuple[object, FileInfo]] = []  # var and const value nodes: run by the package itself
        self.unreadable: list[str] = []  # files of the package that may declare more than ripple read (see Program.read)

    def declare_name(self, name: str, kind: str, info: FileInfo) -> None:
        self.names[name].add(kind)
        self.declared_in[name].append(info)

    def type_info(self, name: str) -> TypeInfo:
        if name not in self.types:
            self.types[name] = TypeInfo(self, name)
        return self.types[name]


@dataclass
class Source:
    rel: str
    tree: object
    clause: str
    generated: bool
    constrained: bool
    tests_only: bool
    incomplete: bool  # its top level did not parse cleanly: a declaration may be missing from what ripple read


def before_package(head: bytes) -> bytes:
    """The start of a file up to its package clause, which is where build constraints may be (`package/**/x` is a clause too)."""
    found = PACKAGE_CLAUSE.search(head)
    return head[: found.start()] if found else head


def build_constraint(head: bytes) -> str:
    found = BUILD_LINE.search(before_package(head))
    return found.group(1).decode("utf-8", "replace").strip() if found else ""


def satisfiable(expression: str) -> bool:
    """Whether a //go:build expression can be true when the tag `ignore` is false and every other tag may be either: a file
    that says `ignore`, or `ignore && linux`, is a generator script no build includes. A line it cannot read is satisfiable."""
    tokens = re.findall(r"&&|\|\||!|\(|\)|[\w.]+", expression)
    tags = sorted({t for t in tokens if t[0] not in "&|!()"} - {"ignore"})
    if not tokens or len(tags) > 10:
        return True

    def evaluate(values: dict[str, bool]) -> bool:
        at = 0

        def take() -> str:
            nonlocal at
            at += 1
            return tokens[at - 1]

        def either() -> bool:
            value = both()
            while at < len(tokens) and tokens[at] == "||":
                take()
                value = both() or value
            return value

        def both() -> bool:
            value = unary()
            while at < len(tokens) and tokens[at] == "&&":
                take()
                value = unary() and value
            return value

        def unary() -> bool:
            token = take()
            if token == "!":
                return not unary()
            if token == "(":
                value = either()
                take()
                return value
            return values.get(token, False)

        return either()

    try:
        return any(evaluate(dict(zip(tags, bits))) for bits in itertools.product((False, True), repeat=len(tags)))
    except IndexError:
        return True


def platform_file(name: str) -> bool:
    """Whether a file name says a GOOS or GOARCH (`x_linux.go`, `x_linux_arm64_test.go`), as the go tool reads it."""
    stem = name.removesuffix(".go")
    cut = stem.find("_")
    if cut < 0:
        return False
    parts = stem[cut:].split("_")
    if parts[-1] == "test":
        parts = parts[:-1]
    return (len(parts) >= 2 and parts[-2] in KNOWN_OS and parts[-1] in KNOWN_ARCH) or (len(parts) >= 1 and parts[-1] in KNOWN_OS | KNOWN_ARCH)


def is_constrained(name: str, head: bytes) -> bool:
    return name.endswith("_test.go") or platform_file(name) or bool(build_constraint(head))


def is_tests_only(name: str, head: bytes) -> bool:
    return name.endswith("_test.go") and not platform_file(name) and not build_constraint(head)


def never_built(head: bytes) -> bool:
    """A file no build includes: `//go:build ignore` (also with other terms and'ed on) or the older `// +build ignore`."""
    constraint = build_constraint(head)
    if constraint:
        return not satisfiable(constraint)
    legacy = PLUS_BUILD_LINE.findall(before_package(head))
    return any(line.decode("utf-8", "replace").strip() == "ignore" for line in legacy)


def reads_as_source(path: Path, profile: LanguageProfile, repo: Path | None = None) -> bool:
    """False for a .go file the go tool never builds: one whose name starts with `_` or `.`, one below a `vendor`,
    `testdata`, `_x` or `.x` directory of the repo (wherever a go.mod sits: a module fixture under testdata has its own
    go.mod and is no more built for it), and one no build includes (`//go:build ignore`, a generator script). The
    walk passes the repo root, so a repo that itself sits below a directory with such a name is read."""
    if path.name.startswith(("_", ".")):
        return False
    if repo is not None and any(is_ignored_dir(part) for part in path.parent.relative_to(repo).parts):
        return False
    try:
        with open(path, "rb") as handle:
            head = handle.read(8192)
    except OSError:
        return True  # reading it fails later and is reported with the unparsable ones
    return not never_built(head)


def ignored_directory(path: Path, profile: LanguageProfile, repo: Path) -> str | None:
    """The topmost directory of the repo that keeps a .go file from being read, as the walk reports it (a hidden one the
    walk never enters): `_examples` for `_examples/rest/main.go`, `vendor` for `vendor/x/y.go`."""
    parts = path.parent.relative_to(repo).parts
    for index, part in enumerate(parts):
        if is_ignored_dir(part):
            return "/".join(parts[: index + 1])
    return None


def top_level_incomplete(root) -> bool:
    """Whether a declaration of the file may be missing from what tree-sitter returned. An ERROR at the top level
    swallows the declarations after it, so a file whose top level is not all declarations may hold names and methods
    ripple did not see. A function with the broken code inside its body is the one error that stays where it is: the
    header read, the body closed, and the body's braces (read with Go's lexical rules, whatever the layout) close it at
    its last byte and not before, so no declaration the compiler would see follows the real end of the function inside it."""
    for child in root.children:
        if child.type == "ERROR" or child.is_missing:
            return True
        if not child.is_named or child.type == "comment":
            continue
        if child.type not in DECLARATIONS:
            return True
        if child.has_error and not confined(child):
            return True
    return False


def confined(declaration) -> bool:
    if declaration.type not in ("function_declaration", "method_declaration"):
        return False
    body = field_of(declaration, "body")
    header = [field_of(declaration, name) for name in ("name", "receiver", "parameters", "result")]
    if body is None or any(h is not None and h.has_error for h in header) or declaration.type == "method_declaration" and header[1] is None:
        return False
    closing = body.children[-1]
    return closing.type == "}" and not closing.is_missing and closes_only_at_end(body.text)


def config_files(repo: Path, sources: list[Path]) -> list[str]:
    """Every go.mod and go.work above a Go source directory of the repo, which the cache fingerprints too: a module
    path or a replace changes what an import names."""
    directories = {"" if path.parent == repo else path.parent.relative_to(repo).as_posix() for path in sources}
    return sorted(Modules(repo, directories).files)


def module_id(file: str) -> str:
    """The package part of a repo-relative path as written, `doc` for `doc/man_docs.go` and `` for a root file. Only a
    guess for a query (the module path that prefixes the real id is in go.mod); the graph's own file map decides."""
    return file.rpartition("/")[0]


# ---- the program: files, packages, declarations --------------------------------------------------


class Program:
    def __init__(self, repo: Path, files: list[Path], profile: LanguageProfile):
        self.repo, self.profile = repo, profile
        self.parse_errors: list[str] = []
        self.lost: list[str] = []  # files with no tree or no package clause: their declarations are unknown
        self.hidden_methods: set[str] = set()  # the method names the files ripple could not read completely may declare, by their text
        self.unread_text: list[str] = []  # files whose bytes could not be read at all: no name is known of what they declare
        self.sources: dict[str, Source] = {}
        self.read(files)
        self.directories = {s.rel.rpartition("/")[0] for s in self.sources.values()}
        self.modules = Modules(repo, self.directories)
        self.packages: dict[tuple[str, bool], Package] = {}
        self.by_id: dict[str, Package] = {}
        self.file_infos: dict[str, FileInfo] = {}
        self.ids: Counter = Counter()  # how many times each symbol id was declared, for the #N suffix
        self.group()
        self.declare_all()

    # -- reading ------------------------------------------------------------

    def read(self, files: list[Path]) -> None:
        parser = load_parser()
        for path in files:
            rel = path.relative_to(self.repo).as_posix()
            data = None
            try:
                data = path.read_bytes()
                tree = parser.parse(data)
            except Exception:  # noqa: BLE001 (a file that cannot be read or parsed costs that file, not the index)
                self.parse_errors.append(rel)
                self.lost.append(rel)
                self.hide(rel, data)
                continue
            clause = next((c for c in named(tree.root_node) if c.type == "package_clause"), None)
            if tree.root_node.has_error:
                self.parse_errors.append(rel)
            if clause is None or not named(clause):
                self.parse_errors.append(rel)
                self.lost.append(rel)
                self.hide(rel, data)
                continue
            incomplete = tree.root_node.has_error and top_level_incomplete(tree.root_node)
            if incomplete:
                self.hide(rel, data)
            head = data[:8192]
            self.sources[rel] = Source(
                rel, tree, text(named(clause)[0]), bool(GENERATED.search(head)), is_constrained(path.name, head), is_tests_only(path.name, head), incomplete
            )

    def hide(self, rel: str, data: bytes | None) -> None:
        """A file whose declarations ripple may not have: what it may declare is read off its text, which no parser is needed for."""
        if data is None:
            self.unread_text.append(rel)
        else:
            self.hidden_methods |= {m.decode("utf-8", "replace") for m in METHOD_NAME.findall(uncommented(data))}

    def group(self) -> None:
        """Make the packages: the files of a directory with its package name, and `foo_test` files as their own."""
        per_dir: dict[str, list[Source]] = defaultdict(list)
        for rel in sorted(self.sources):
            per_dir[rel.rpartition("/")[0]].append(self.sources[rel])
        names: dict[str, str] = {}
        for directory, sources in per_dir.items():
            counts = Counter(s.clause for s in sources if not (s.rel.endswith("_test.go") and s.clause.endswith("_test")))
            if not counts:
                counts = Counter(s.clause.removesuffix("_test") for s in sources)
            names[directory] = counts.most_common(1)[0][0]
        used: set[str] = set()
        for directory, sources in sorted(per_dir.items()):
            for source in sources:
                external = source.rel.endswith("_test.go") and source.clause == names[directory] + "_test"
                if not external and source.clause != names[directory]:
                    self.parse_errors.append(source.rel)  # a file that is in neither package of its directory: left out
                    self.lost.append(source.rel)
                    continue
                key = (directory, external)
                if key not in self.packages:
                    path = self.modules.import_path(directory) or directory or source.clause
                    ident = path + "_test" if external else path
                    if ident in used:  # two directories with one import path (two go.mod of one name): the directory tells them apart
                        ident = (directory or source.clause) + ("_test" if external else "")
                    used.add(ident)
                    self.packages[key] = Package(ident, source.clause, directory)
                    self.by_id[ident] = self.packages[key]
                info = FileInfo(source.rel, self.packages[key], source.constrained, source.tests_only)
                self.packages[key].files.append(info)
                self.file_infos[source.rel] = info
                if source.incomplete:
                    self.packages[key].unreadable.append(source.rel)
        for rel in self.lost:  # a file ripple has no tree of may belong to either package of its directory
            for (directory, _), package in self.packages.items():
                if directory == rel.rpartition("/")[0]:
                    package.unreadable.append(rel)
        self.importable = {d for (d, external) in self.packages if not external}

    # -- imports ------------------------------------------------------------

    def read_imports(self, info: FileInfo, root) -> None:
        for declaration in named(root):
            if declaration.type != "import_declaration":
                continue
            found = [s for s in declaration.children if s.type == "import_spec"]
            for block in (c for c in declaration.children if c.type == "import_spec_list"):
                found += [s for s in block.children if s.type == "import_spec"]
            for spec in found:
                path = text(field_of(spec, "path")).strip("\"`")
                alias = field_of(spec, "name")
                if path == "C":
                    info.imports["C"] = Import("cgo", path)
                    continue
                how, target = self.modules.resolve(info.package.dir, path, self.importable)
                if how in (OWN, REPLACE, WORK):
                    imported = Import("repo", path, self.packages[(target, False)])
                else:
                    imported = Import({PUBLISHED: "published", MISSING: "missing"}.get(how, "outside"), path)
                if alias is not None and alias.type == "dot":
                    info.dots.append(imported)
                elif alias is not None and alias.type == "blank_identifier":
                    continue
                elif alias is not None:
                    info.imports[text(alias)] = imported
                elif imported.package is not None:
                    info.imports[imported.package.name] = imported
                else:
                    info.guesses.setdefault(guess_name(path), []).append(imported)

    # -- declarations -------------------------------------------------------

    def declare_all(self) -> None:
        for package in self.packages.values():
            for info in package.files:
                self.read_imports(info, self.sources[info.rel].tree.root_node)
        for package in sorted(self.packages.values(), key=lambda p: p.id):
            for info in package.files:
                self.declare(package, info, self.sources[info.rel].tree.root_node)

    def clash(self, ids: Counter, ident: str) -> str:
        ids[ident] += 1
        return ident if ids[ident] == 1 else f"{ident}#{ids[ident]}"

    def declare(self, package: Package, info: FileInfo, root) -> None:
        ids = self.ids
        for declaration in named(root):
            kind = declaration.type
            if kind == "function_declaration":
                name = text(field_of(declaration, "name"))
                ident = self.clash(ids, f"{package.id}:{name}")
                decl = FuncDecl(ident, name, info, declaration, type_parameter_names(declaration))
                if name in ("init", "_"):
                    package.inits.append(decl)  # the language allows many: run by the runtime, called by nobody
                else:
                    package.funcs[name].append(decl)
                    package.declare_name(name, "func", info)
                package.symbols.append(self.symbol(ident, "function", info, declaration))
            elif kind == "method_declaration":
                self.declare_method(package, info, declaration, ids)
            elif kind == "type_declaration":
                for spec in named(declaration):
                    if spec.type in ("type_spec", "type_alias"):
                        self.declare_type(package, info, spec, declaration.has_error, ids)
            elif kind == "var_declaration":
                self.declare_vars(package, info, declaration)
            elif kind == "const_declaration":
                for spec in specs(declaration, "const_spec"):
                    for name in fields_of(spec, "name"):
                        package.declare_name(text(name), "const", info)
                    value = field_of(spec, "value")
                    if value is not None:
                        package.initialisers.append((value, info))
            elif kind == "ERROR":
                package.initialisers.append((declaration, info))

    def symbol(self, ident: str, kind: str, info: FileInfo, node) -> Symbol:
        return Symbol(ident, kind, info.rel, line_of(node), node.end_point[0] + 1)

    def declare_method(self, package: Package, info: FileInfo, declaration, ids: Counter) -> None:
        receiver = named(field_of(declaration, "receiver"))
        owner = receiver_name(receiver[0] if receiver else None)
        if owner is None:
            return
        name = text(field_of(declaration, "name"))
        info_type = package.type_info(owner)
        ident = self.clash(ids, f"{package.id}:{owner}.{name}")
        tparams = type_parameter_names(declaration)
        generic = unwrap(field_of(receiver[0], "type"))
        if generic is not None and generic.type == "generic_type" and field_of(generic, "type_arguments") is not None:
            tparams |= frozenset(text(a) for a in named(field_of(generic, "type_arguments")))
        decl = FuncDecl(ident, name, info, declaration, tparams, info_type)
        if name != "_":
            info_type.methods.setdefault(name, []).append(decl)
        package.symbols.append(self.symbol(ident, "method", info, declaration))

    def declare_type(self, package: Package, info: FileInfo, spec, damaged: bool, ids: Counter) -> None:
        name = text(field_of(spec, "name"))
        if name == "_":
            return
        package.declare_name(name, "type", info)
        type_info = package.type_info(name)
        type_info.declarations += 1
        type_info.aliased = type_info.aliased or spec.type == "type_alias"
        type_info.file, type_info.node, type_info.damaged = info, spec, damaged or spec.has_error
        type_info.tparams = type_parameter_names(spec)
        package.symbols.append(self.symbol(self.clash(ids, f"{package.id}:{name}"), "class", info, spec))
        body = field_of(spec, "type")
        if type_info.ambiguous or type_info.damaged or body is None:
            type_info.kind = "other"
        elif spec.type == "type_alias":
            type_info.kind, type_info.target = "alias", body
        elif body.type == "struct_type":
            type_info.kind = "struct"
            listing = next((c for c in named(body) if c.type == "field_declaration_list"), None)
            for member in named(listing) if listing is not None else []:
                if member.type != "field_declaration":
                    continue
                declared = field_of(member, "type")
                if fields_of(member, "name"):
                    for each in fields_of(member, "name"):
                        type_info.fields[text(each)] = declared
                elif declared is not None:
                    type_info.embedded.append(declared)
                    type_info.fields[embedded_name(declared)] = declared
        elif body.type == "interface_type":
            type_info.kind = "interface"
            for element in named(body):
                if element.type == "method_elem":
                    type_info.interface_methods[text(field_of(element, "name"))] = element
                elif element.type == "type_elem":
                    type_info.interface_embedded += named(element)
        elif body.type in ("type_identifier", "qualified_type", "generic_type", "pointer_type", "slice_type", "array_type", "map_type", "channel_type", "function_type"):
            type_info.kind, type_info.target = "named", body
        else:
            type_info.kind = "other"

    def declare_vars(self, package: Package, info: FileInfo, declaration) -> None:
        for spec in specs(declaration, "var_spec"):
            names = fields_of(spec, "name")
            value = field_of(spec, "value")
            values = named(value) if value is not None else []
            declared = field_of(spec, "type")
            for index, name in enumerate(names):
                one = values[index] if len(values) == len(names) else None
                package.declare_name(text(name), "var", info)
                existing = package.vars.get(text(name))
                if existing is not None:
                    same = (existing.type_node and code(existing.type_node), existing.value and code(existing.value)) == (
                        declared is not None and code(declared) or None, one is not None and code(one) or None
                    ) and import_signature(existing.file, existing.type_node, existing.value) == import_signature(info, declared, one)
                    if not same:
                        existing.conflict = True
                    continue
                package.vars[text(name)] = VarInfo(declared, one, info)
            if value is not None:
                package.initialisers.append((value, info))


def import_signature(info: FileInfo, *nodes) -> frozenset:
    """What a file's imports make of the names these nodes write. One text means two things when the files that write it
    bind its qualifiers differently (`foo.New()` under two build constraints, each importing another `foo`)."""
    names, stack = set(), [n for n in nodes if n is not None]
    while stack:
        node = stack.pop()
        if node.type in ("identifier", "package_identifier", "type_identifier"):
            names.add(text(node))
        stack.extend(node.children)
    meant = {(n, info.imports[n].kind, info.imports[n].path) for n in names if n in info.imports}
    meant |= {(n, "guess", tuple(sorted(i.path for i in info.guesses[n]))) for n in names if n in info.guesses}
    return frozenset(meant | {("dot", i.path) for i in info.dots})


def guess_name(path: str) -> str:
    """The name an import of an outside package most likely binds, from its path by the Go naming conventions
    (`go-humanize` is `humanize`, `yaml.v3` is `yaml`, `/v5` takes the element before). A wrong guess cannot steal a
    name another scope declares: it is looked up only when none does."""
    parts = path.split("/")
    last = parts[-1]
    if re.fullmatch(r"v\d+", last) and len(parts) > 1:
        last = parts[-2]
    last = re.sub(r"\.v\d+$", "", last)
    return re.sub(r"^go-", "", re.sub(r"[-.]go$", "", last)).replace("-", "")


# ---- scopes -------------------------------------------------------------------------------------


class Env:
    """The names a function body binds, as a stack of blocks (a name's scope starts after its declaration)."""

    def __init__(self, info: FileInfo, tparams: frozenset[str]):
        self.info, self.tparams = info, tparams
        self.frames: list[dict[str, tuple]] = [{}]
        self.local_types: set[str] = set()

    def child(self) -> Env:
        inner = Env(self.info, self.tparams)
        inner.frames = [dict(frame) for frame in self.frames] + [{}]
        inner.local_types = set(self.local_types)
        return inner

    def push(self) -> None:
        self.frames.append({})

    def pop(self) -> None:
        self.frames.pop()

    def bind(self, name: str, ty, how: str) -> None:
        self.frames[-1][name] = (ty, how)

    def declared_here(self, name: str) -> bool:
        return name in self.frames[-1]

    def local(self, name: str):
        for frame in reversed(self.frames):
            if name in frame:
                return frame[name]
        return None


# ---- the resolver -------------------------------------------------------------------------------
# A static type is a tuple: ("named", TypeInfo) ("ptr", t) ("slice", t) ("map", k, v) ("chan", t) ("func", [results])
# ("builtin", name) ("ext", text) ("tparam", name) ("iface_lit",) ("struct_lit",) ("localtype", name)
# ("tuple", [types]) ("variants",) (the result of a function whose build variants disagree); None is not known.


class Resolver:
    def __init__(self, program: Program):
        self.program = program
        self.edges: list[Edge] = []
        self.unresolved_calls: list[Unresolved] = []
        self.external_reasons: Counter = Counter()
        # (kind, file, end line, end column, detail) of every call ripple answered: "edge" (the callee ids), "unresolved" or
        # "external" (the reason). Kept for the probe tests, which hold each call to the compiler's answer at its own position.
        self.call_sites: list[tuple[str, str, int, int, object]] = []
        self.quiet = 0  # while typing a package variable's initialiser, nothing is recorded
        self.unread_files: list[str] = sorted({f for p in program.packages.values() for f in p.unreadable})
        self.hidden_methods = program.hidden_methods
        self.file: FileInfo | None = None
        self.move_alias_methods()
        self.float_methods()
        self.method_names: set[str] = {m for p in program.packages.values() for t in p.types.values() for m in t.methods}
        self.result_cache: dict[int, list] = {}
        self.typing: set[tuple[int, str]] = set()
        self.var_types: dict[tuple[int, str], object] = {}
        self.caller = ""

    def move_alias_methods(self) -> None:
        """A method declared on an alias (`type A = B`, `func (A) M()`) is a method of B, which the compiler names A.M:
        it is filed under B, and its id keeps the name it was declared with. An alias that is not settled (see settled)
        stays where it is and its method names float."""
        for package in self.program.packages.values():
            for type_info in package.types.values():
                if type_info.kind == "alias" and type_info.methods and self.settled(type_info):
                    target = self.named(type_info)
                    if target is not None and target[0] == "named" and target[1] is not type_info:
                        for name, declarations in type_info.methods.items():
                            target[1].methods.setdefault(name, []).extend(declarations)
                        type_info.methods = {}

    def float_methods(self) -> None:
        """A method declared on a name whose declaration is not settled may be a method of any type of the package: when
        the name is an alias in some build (`type A = B` in one file, `type A = C` in another), `func (A) M()` is B's
        method in one build and C's in the next. Such names go in Package.floating, and a lookup of one on any type of
        the package that does not declare it itself is unresolved. A name with methods that no file declares, or whose
        declaration tree-sitter could not read, may be an alias too."""
        for package in self.program.packages.values():
            for type_info in package.types.values():
                if not type_info.methods:
                    continue
                # (an alias that was settled and named a repo type has had its methods moved and has none left here)
                if type_info.kind == "undeclared" or type_info.damaged or type_info.aliased or self.unsure(package, type_info.name):
                    package.floating |= set(type_info.methods)

    def always(self, file: FileInfo) -> bool:
        """Whether a file is built in every build that has the file being read: one with no build constraint is, and so
        is a _test.go file for a call in a _test.go file (a test build has them all)."""
        return not file.constrained or (file.tests_only and self.file is not None and self.file.tests_only and self.file.package is file.package)

    def unsure(self, package: Package, name: str) -> bool:
        """Whether a package-level name may be declared another way in a file ripple could not read. A file with a
        declaration of the name that is built wherever the call is makes a second one in the unreadable file a
        redeclaration, so the name is what ripple found; every other declaration (a platform file, a build tag) may be
        one of several builds' and the unreadable file may hold the rest. Only a package with an unreadable file is unsure."""
        return bool(package.unreadable) and not any(self.always(f) for f in package.declared_in.get(name, ()))

    def settled(self, type_info: TypeInfo) -> bool:
        """Whether one declaration of a type is the one every build has: declared once, read completely, and not
        one of several builds' of a package with a file ripple could not read."""
        return not (type_info.ambiguous or type_info.damaged or type_info.kind == "undeclared" or self.unsure(type_info.package, type_info.name))

    # -- recording ----------------------------------------------------------

    def site(self, kind: str, node, detail) -> None:
        """One call's outcome at the position of its closing parenthesis, which no two calls share (an edge carries
        a line only, and `a.F().G()` has two calls on one)."""
        self.call_sites.append((kind, self.file.rel, node.end_point[0] + 1, node.end_point[1], detail))

    def edge(self, decls: list[FuncDecl], node) -> None:
        if not self.quiet:
            for decl in decls:
                self.edges.append(Edge(self.caller, decl.id, self.file.rel, line_of(node)))
            self.site("edge", node, tuple(sorted(d.id for d in decls)))

    def unresolved(self, node, call_text: str, reason: str) -> None:
        if not self.quiet:
            self.unresolved_calls.append(Unresolved(self.caller, self.file.rel, line_of(node), call_text, reason))
            self.site("unresolved", node, reason)

    def external(self, node, reason: str) -> None:
        if not self.quiet:
            self.external_reasons[reason] += 1
            self.site("external", node, reason)

    # -- running ------------------------------------------------------------

    def run(self) -> Resolver:
        for package in sorted(self.program.packages.values(), key=lambda p: p.id):
            for declarations in package.funcs.values():
                for decl in declarations:
                    self.function(decl)
            for type_info in package.types.values():
                for declarations in type_info.methods.values():
                    for decl in declarations:
                        self.function(decl)
            for decl in package.inits:
                self.function(decl)
            self.caller = package.id
            for node, info in package.initialisers:
                self.file = info
                if node.has_error:
                    self.damaged(node)
                else:
                    self.expr(node, Env(info, frozenset()))
        return self

    def function(self, decl: FuncDecl) -> None:
        self.caller, self.file = decl.id, decl.file
        node = decl.node
        if node.has_error:
            self.damaged(node)
            return
        env = Env(decl.file, decl.tparams)
        if decl.owner is not None:
            receiver = named(field_of(node, "receiver"))[0]
            for name in fields_of(receiver, "name"):
                env.bind(text(name), self.named(decl.owner), "receiver")
        self.bind_parameters(field_of(node, "parameters"), env)
        result = field_of(node, "result")
        if result is not None and result.type == "parameter_list":
            self.bind_parameters(result, env)
        body = field_of(node, "body")
        if body is not None:
            self.block(body, env, share=True)

    def damaged(self, node) -> None:
        """Every call written in broken code is unresolved: a binding the parse lost could change what a name means."""
        stack = [node]
        while stack:
            current = stack.pop()
            if current.type == "call_expression":
                self.unresolved(current, shown(field_of(current, "function")), DAMAGED)
            stack.extend(current.children)

    def bind_parameters(self, parameters, env: Env) -> None:
        for p in named(parameters) if parameters is not None else []:
            if p.type not in ("parameter_declaration", "variadic_parameter_declaration"):
                continue
            ty = self.type_node(field_of(p, "type"), env)
            if p.type == "variadic_parameter_declaration":
                ty = ("slice", ty)
            for name in fields_of(p, "name"):
                env.bind(text(name), ty, "param")

    # -- types --------------------------------------------------------------

    def type_node(self, node, env: Env):
        return self.type_of(node, env.info, env.tparams, env.local_types)

    def type_of(self, node, info: FileInfo, tparams=frozenset(), local_types=None):
        """The static type a type expression writes, None when it is not one ripple follows."""
        if node is None:
            return None
        kind = node.type
        if kind == "parenthesized_type":
            inner = named(node)
            return self.type_of(inner[0], info, tparams, local_types) if inner else None
        if kind == "pointer_type":
            inner = named(node)
            ty = self.type_of(inner[0], info, tparams, local_types) if inner else None
            return ("ptr", ty) if ty is not None else None
        if kind in ("slice_type", "array_type", "implicit_length_array_type"):
            return ("slice", self.type_of(field_of(node, "element"), info, tparams, local_types))
        if kind == "map_type":
            return ("map", self.type_of(field_of(node, "key"), info, tparams, local_types), self.type_of(field_of(node, "value"), info, tparams, local_types))
        if kind == "channel_type":
            return ("chan", self.type_of(field_of(node, "value"), info, tparams, local_types))
        if kind == "function_type":
            return ("func", self.result_types(field_of(node, "result"), info, tparams))
        if kind == "interface_type":
            return ("iface_lit",)
        if kind == "struct_type":
            return ("struct_lit",)
        if kind == "generic_type":
            return self.type_of(field_of(node, "type"), info, tparams, local_types)
        if kind in ("type_elem", "type_constraint"):
            inner = named(node)
            return self.type_of(inner[0], info, tparams, local_types) if len(inner) == 1 else None
        if kind == "type_identifier":
            name = text(node)
            if name in tparams:
                return ("tparam", name)
            if local_types is not None and name in local_types:
                return ("localtype", name)
            declared = info.package.types.get(name)
            if declared is not None and declared.declarations:
                return self.named(declared)
            if info.package.unreadable and (name in UNIVERSE_INTERFACES or name in UNIVERSE_TYPES):
                return None  # a type ripple could not read may be named so
            if name in UNIVERSE_INTERFACES:
                return ("ext", name)
            if name in UNIVERSE_TYPES:
                return ("builtin", name)
            for dot in info.dots:
                if dot.package is not None and name in dot.package.types and dot.package.types[name].declarations:
                    return self.named(dot.package.types[name])
            return None
        if kind == "qualified_type":
            entry = self.import_entry(info, text(field_of(node, "package")))
            name = text(field_of(node, "name"))
            if entry is None:
                return None
            if entry.kind == "repo":
                declared = entry.package.types.get(name)
                return self.named(declared) if declared is not None and declared.declarations else None
            if entry.kind in ("outside", "published", "cgo"):
                return ("ext", f"{entry.path}.{name}")
        return None

    def named(self, type_info: TypeInfo, depth: int = 0):
        if type_info.kind == "alias" and depth < 10 and not self.unsure(type_info.package, type_info.name):
            target = self.type_of(type_info.target, type_info.file, type_info.tparams)
            if target and target[0] == "named":
                return self.named(target[1], depth + 1)
            return target
        return ("named", type_info)

    def result_types(self, result, info: FileInfo, tparams=frozenset()) -> list:
        if result is None:
            return []
        if result.type == "parameter_list":
            out = []
            for p in named(result):
                if p.type in ("parameter_declaration", "variadic_parameter_declaration"):
                    out += [self.type_of(field_of(p, "type"), info, tparams)] * max(1, len(fields_of(p, "name")))
            return out
        return [self.type_of(result, info, tparams)]

    def results_of(self, decls: list[FuncDecl]) -> list:
        """The declared result types of a function or method, when every variant agrees on them (and reads its imports alike)."""
        first = decls[0]
        key = id(first)
        if key not in self.result_cache:
            result = field_of(first.node, "result")
            same = all(
                (code(field_of(d.node, "result")) if field_of(d.node, "result") is not None else "") == (code(result) if result is not None else "")
                and import_signature(d.file, field_of(d.node, "result")) == import_signature(first.file, result)
                for d in decls
            )
            if first.node.has_error:
                self.result_cache[key] = []
            elif not same:
                self.result_cache[key] = [("variants",)]
            else:
                self.result_cache[key] = self.result_types(result, first.file, first.tparams)
        return self.result_cache[key]

    def import_entry(self, info: FileInfo, name: str) -> Import | None:
        if name in info.imports:
            return info.imports[name]
        guessed = info.guesses.get(name, [])
        return guessed[0] if len(guessed) == 1 else None

    @staticmethod
    def deref(ty):
        while ty is not None and ty[0] == "ptr":
            ty = ty[1]
        return ty

    def structure(self, ty):
        """A type's structure for indexing and ranging: a named type whose declaration is `type T []E` is a slice."""
        ty = self.deref(ty)
        for _ in range(10):
            if ty is None or ty[0] != "named" or ty[1].kind != "named":
                break
            target = self.deref(self.type_of(ty[1].target, ty[1].file, ty[1].tparams))
            if target is None:
                return None
            ty = target
        return ty

    # -- method lookup through embedded types ------------------------------------

    def underlying(self, type_info: TypeInfo):
        """The struct or interface TypeInfo whose fields or methods a type has: itself, or what `type T U` names."""
        seen = set()
        while type_info is not None and type_info.kind == "named" and id(type_info) not in seen:
            seen.add(id(type_info))
            target = self.deref(self.type_of(type_info.target, type_info.file, type_info.tparams))
            if target is None or target[0] != "named":
                return None if target is None else ("other", target)
            type_info = target[1]
        return type_info

    def interface_methods(self, type_info: TypeInfo, seen=None):
        """({method name: TypeInfo that declares it}, whether an embedded part is outside the repo or unreadable)."""
        seen = seen if seen is not None else set()
        if id(type_info) in seen:
            return {}, False
        seen.add(id(type_info))
        found = {name: type_info for name in type_info.interface_methods}
        open_ = False
        for embedded in type_info.interface_embedded:
            ty = self.type_of(embedded, type_info.file, type_info.tparams)
            if ty and ty[0] == "named":
                inner = self.underlying(ty[1])
                if isinstance(inner, TypeInfo) and inner.kind == "interface":
                    more, more_open = self.interface_methods(inner, seen)
                    found.update({k: v for k, v in more.items() if k not in found})
                    open_ = open_ or more_open
                    continue
            open_ = True
        return found, open_

    def unread(self, package: Package) -> str:
        return UNREAD_FILE.format(files=", ".join(package.unreadable[:3]) + (", ..." if len(package.unreadable) > 3 else ""))

    def outside_method(self, name: str, reason: str):
        """A call to a method of a type from outside the repo that no repo type declares: external, unless a file ripple
        could not read completely may declare one (which would make the outside type an interface it implements)."""
        if name in self.hidden_methods:
            return ("unknown", UNREAD_REPO.format(files=", ".join(self.unread_files[:3]) + (", ..." if len(self.unread_files) > 3 else "")))
        if self.program.unread_text:
            return ("unknown", UNREAD_REPO.format(files=", ".join(self.program.unread_text[:3])))
        return ("external", reason)

    @staticmethod
    def outside_member(ty, name: str) -> str:
        """What `name` is on a value of a type from outside the repo, as far as the standard library table proves:
        "method" (of a type that is not an interface), "dynamic" (a method of an interface, or promoted through one),
        "field" (of function type), "none" (the table says it is neither) or "unknown" (no table for the type)."""
        found = outside_type(ty[1]) if ty is not None and ty[0] == "ext" else None
        if found is None:
            return "unknown"
        kind, methods, fields = found
        if name in fields:
            return "field"
        if name in methods:
            return "dynamic" if kind == "i" else "method"
        return "none"

    def lookup(self, ty, name: str):
        """What `x.name` is for x of static type ty: ("method", decls) ("field", type) ("iface", TypeInfo)
        ("external", reason) ("unknown", reason)."""
        base = self.deref(ty)
        if base is None:
            return ("unknown", None)
        kind = base[0]
        if kind == "tparam":
            return ("unknown", TYPE_PARAMETER)
        if kind == "ext":
            if name in ("Call", "CallSlice"):  # reflect.Value, which a chain of calls from outside the repo loses the name of
                return ("unknown", REFLECTION)
            member = self.outside_member(base, name)
            if member == "method":
                return ("external", STDLIB_METHOD)  # a concrete type's method: the library's own code
            if member == "dynamic":  # a method of an interface, which a repo type may implement
                if name in self.method_names:
                    return ("unknown", OUTSIDE_MAY_BE_INTERFACE)
                return self.outside_method(name, OUTSIDE_METHOD)
            if member == "field":
                return ("unknown", FIELD_FUNCTION)  # a function value, which may hold repo code
            return ("unknown", STDLIB_NOT_MEMBER if outside_type(base[1]) is not None else OUTSIDE_TYPE_MEMBER)
        if kind == "variants":
            return ("unknown", BUILD_VARIANT_RESULT)
        if kind == "builtin":
            return ("unknown", None)
        if kind != "named":
            return ("unknown", None)
        return self.walk([base[1]], name, set())

    def in_every_build(self, decl: FuncDecl) -> bool:
        """Whether the declaration is there in every build that has the call being resolved: a declaration in no
        constrained file is, and so is one in a _test.go file for a call in a _test.go file (a test build has them all)."""
        return not decl.file.constrained or (decl.file.tests_only and self.file is not None and self.file.tests_only)

    def walk(self, level: list[TypeInfo], name: str, seen: set[int]):
        """The spec's depth rule: the shallowest level of embedded types that has `name`, and unresolved whenever a
        shallower type might have had it (an embedded type from outside the repo, a package with a file ripple could
        not read, a method declared only in some builds)."""
        shallower_outside, hidden = [], None  # the types from outside the repo that sit shallower (None: one ripple cannot read)
        for _ in range(RECEIVER_LIMIT):
            matches, outside_here, hidden_here, following = [], [], None, []
            for type_info in level:
                if id(type_info) in seen:
                    continue
                seen.add(id(type_info))
                declared = type_info.methods.get(name)
                if declared and any(self.in_every_build(d) for d in declared):
                    matches.append(("method", declared, type_info))  # a declaration that every build of this call has
                    continue
                if name in type_info.package.floating:
                    return ("unknown", FLOATING_METHOD)  # a method of this name is declared on an alias whose target depends on the build
                if type_info.package.unreadable:
                    hidden_here = hidden_here or self.unread(type_info.package)
                if not self.settled(type_info):
                    if declared:  # in some builds only, and what the other builds have instead is not read
                        return ("unknown", BUILD_VARIANT_METHOD)
                    return ("unknown", self.unsettled(type_info))
                if declared:
                    matches.append(("method", declared, type_info, "some builds"))  # and the embedded types below are read too
                inner = self.underlying(type_info)
                if isinstance(inner, tuple):
                    if inner[1][0] == "ext":
                        outside_here.append(inner[1])
                    continue
                if inner is None:
                    outside_here.append(None)
                    continue
                if not self.settled(inner):
                    return ("unknown", self.unsettled(inner))
                if inner.kind == "interface":
                    interface_declared, open_ = self.interface_methods(inner)
                    if name in interface_declared:
                        matches.append(("iface", inner))
                    elif open_:
                        outside_here.append(None)
                    continue
                if inner.kind != "struct":
                    continue
                if name in inner.fields and not any(embedded_name(e) == name for e in inner.embedded):
                    matches.append(("field", self.type_of(inner.fields[name], inner.file, inner.tparams)))
                    continue
                for embedded in inner.embedded:
                    ty_embedded = self.deref(self.type_of(embedded, inner.file, inner.tparams))
                    if ty_embedded is not None and ty_embedded[0] == "named":
                        if embedded_name(embedded) == name:
                            matches.append(("field", ty_embedded))
                        else:
                            following.append(ty_embedded[1])
                    else:
                        outside_here.append(ty_embedded)  # an embedded type from outside the repo (or one ripple cannot read): it may provide the name
            if matches:
                if shallower_outside:
                    return ("unknown", SHALLOWER_OUTSIDE)
                if hidden:
                    return ("unknown", hidden)
                if len(matches) > 1:
                    return ("unknown", TWO_EMBEDDED)
                match = matches[0]
                if len(match) == 4:  # the method is declared in some builds only: proven when no build could have another
                    if outside_here or hidden_here or self.walk(following, name, seen) != ("unknown", NO_SUCH_MEMBER):
                        return ("unknown", BUILD_VARIANT_METHOD)
                if match[0] == "method":
                    return ("method", match[1])
                return (match[0], match[1])
            shallower_outside += outside_here
            hidden = hidden or hidden_here
            level = following
            if not level:
                break
        if hidden:
            return ("unknown", hidden)
        if shallower_outside:
            return self.promoted_outside(name, shallower_outside)
        return ("unknown", NO_SUCH_MEMBER)

    def promoted_outside(self, name: str, outside: list):
        """`name` may be promoted from an embedded type outside the repo: what it is depends on which, and the table
        proves it only for standard library types."""
        if name in ("Call", "CallSlice"):
            return ("unknown", REFLECTION)
        found = {self.outside_member(ty, name) for ty in outside}
        if found & {"unknown", "field"}:
            return ("unknown", UNPROVEN_PROMOTED)
        if "dynamic" in found:  # promoted from an interface: a repo type may implement it
            if name in self.method_names:
                return ("unknown", "the method may come from an embedded type outside the repo, which may be an interface a repo type implements")
            return self.outside_method(name, PROMOTED_OUTSIDE)
        if "method" in found:
            return ("external", PROMOTED_OUTSIDE)
        return ("unknown", NO_SUCH_MEMBER)

    def unsettled(self, type_info: TypeInfo) -> str:
        """Why a type that is not settled cannot be followed."""
        if type_info.kind == "undeclared" or (type_info.damaged and not type_info.ambiguous):
            return UNREADABLE_TYPE
        return BUILD_VARIANT_TYPE if type_info.ambiguous else self.unread(type_info.package)

    # -- statements ---------------------------------------------------------

    def block(self, node, env: Env, share: bool = False) -> None:
        """A block of statements. A function body shares its scope with the parameters (share): `x, err := f()` in it
        reuses a parameter x and keeps its type."""
        if not share:
            env.push()
        for statement in named(node):
            if statement.type == "statement_list":
                for each in named(statement):
                    self.stmt(each, env)
            else:
                self.stmt(statement, env)
        if not share:
            env.pop()

    def stmt(self, s, env: Env) -> None:
        kind = s.type
        if kind == "short_var_declaration":
            left, right = named(field_of(s, "left")), named(field_of(s, "right"))
            types = self.right_types(right, len(left), env)
            for i, name in enumerate(left):
                if name.type == "identifier" and text(name) != "_" and not env.declared_here(text(name)):
                    env.bind(text(name), types[i] if i < len(types) else None, "local")
        elif kind == "var_declaration":
            for spec in specs(s, "var_spec"):
                value = field_of(spec, "value")
                values = named(value) if value is not None else []
                names = fields_of(spec, "name")
                declared = field_of(spec, "type")
                types = self.right_types(values, len(names), env) if values else [None] * len(names)
                written = self.type_node(declared, env) if declared is not None else None
                for i, name in enumerate(names):
                    env.bind(text(name), written if declared is not None else (types[i] if i < len(types) else None), "local")
        elif kind == "const_declaration":
            for spec in specs(s, "const_spec"):
                value = field_of(spec, "value")
                if value is not None:
                    self.expr(value, env)
                for name in fields_of(spec, "name"):
                    env.bind(text(name), ("builtin", "const"), "const")
        elif kind == "type_declaration":
            for spec in named(s):
                if spec.type in ("type_spec", "type_alias"):
                    env.local_types.add(text(field_of(spec, "name")))
                    env.bind(text(field_of(spec, "name")), ("localtype", text(field_of(spec, "name"))), "type")
        elif kind == "block":
            self.block(s, env)
        elif kind == "if_statement":
            env.push()
            if field_of(s, "initializer") is not None:
                self.stmt(field_of(s, "initializer"), env)
            self.expr(field_of(s, "condition"), env)
            self.block(field_of(s, "consequence"), env)
            if field_of(s, "alternative") is not None:
                self.stmt(field_of(s, "alternative"), env)
            env.pop()
        elif kind == "for_statement":
            self.for_statement(s, env)
        elif kind in ("expression_switch_statement", "type_switch_statement", "select_statement"):
            self.switch(s, env)
        elif kind == "receive_statement":
            ty = self.expr(field_of(s, "right"), env)
            left = field_of(s, "left")
            if left is not None:
                if any(c.type == ":=" for c in s.children):
                    for i, name in enumerate(named(left)):  # ty is the received value's type: `<-ch` already unwraps the channel
                        if name.type == "identifier":
                            env.bind(text(name), ty if i == 0 else ("builtin", "bool"), "local")
                else:
                    for name in named(left):
                        self.expr(name, env)
        elif kind == "labeled_statement":
            for child in named(s):
                if child.type != "label_name":
                    self.stmt(child, env)
        elif kind in ("break_statement", "continue_statement", "goto_statement", "fallthrough_statement", "empty_statement"):
            pass
        else:  # go and defer statements, expression, return, send, inc, dec and assignment statements: their expressions
            for child in named(s):
                self.expr(child, env)

    def for_statement(self, s, env: Env) -> None:
        env.push()
        for child in named(s):
            if child.type == "range_clause":
                ty = self.expr(field_of(child, "right"), env)
                left = field_of(child, "left")
                names = named(left) if left is not None else []
                types = self.range_types(ty)
                if any(c.type == ":=" for c in child.children):
                    for i, name in enumerate(names):
                        if name.type == "identifier":
                            env.bind(text(name), types[i] if i < len(types) else None, "range")
                else:
                    for name in names:
                        self.expr(name, env)
            elif child.type == "for_clause":
                if field_of(child, "initializer") is not None:
                    self.stmt(field_of(child, "initializer"), env)
                self.expr(field_of(child, "condition"), env)
                if field_of(child, "update") is not None:
                    self.stmt(field_of(child, "update"), env)
            elif child.type == "block":
                self.block(child, env)
            else:
                self.expr(child, env)
        env.pop()

    def switch(self, s, env: Env) -> None:
        env.push()
        if field_of(s, "initializer") is not None:
            self.stmt(field_of(s, "initializer"), env)
        alias, value_type = None, None
        if s.type == "type_switch_statement":
            node = field_of(s, "alias")
            alias = text(named(node)[0]) if node is not None else None
            value_type = self.expr(field_of(s, "value"), env)
        elif field_of(s, "value") is not None:
            self.expr(field_of(s, "value"), env)
        for case in named(s):
            if case.type not in ("expression_case", "default_case", "type_case", "communication_case"):
                continue
            env.push()
            if case.type == "type_case" and alias:
                types = fields_of(case, "type")
                single = len(types) == 1 and text(types[0]) != "nil"
                env.bind(alias, self.type_node(types[0], env) if single else value_type, "typeswitch")
            elif case.type == "default_case" and alias:
                env.bind(alias, value_type, "typeswitch")
            elif case.type == "expression_case" and field_of(case, "value") is not None:
                self.expr(field_of(case, "value"), env)
            elif case.type == "communication_case" and field_of(case, "communication") is not None:
                self.stmt(field_of(case, "communication"), env)
            for child in named(case):
                if child.type == "statement_list":
                    for statement in named(child):
                        self.stmt(statement, env)
            env.pop()
        env.pop()

    def range_types(self, ty) -> list:
        base = self.structure(ty)
        if base is None:
            return [None, None]
        if base[0] == "slice":
            return [("builtin", "int"), base[1]]
        if base[0] == "map":
            return [base[1], base[2]]
        if base[0] == "chan":
            return [base[1]]
        if base[0] == "builtin" and base[1] == "string":
            return [("builtin", "int"), ("builtin", "rune")]
        if base[0] == "builtin":
            return [base]
        return [None, None]

    def right_types(self, right: list, count: int, env: Env) -> list:
        """The types of the values on the right of a declaration, one per name on its left."""
        if len(right) == 1 and count > 1:
            ty = self.expr(right[0], env, want_tuple=True)
            if ty is not None and ty[0] == "tuple":
                return ty[1] + [None] * (count - len(ty[1]))
            if right[0].type in ("type_assertion_expression", "index_expression", "unary_expression"):
                return [ty, ("builtin", "bool")]
            return [None] * count
        return [self.expr(r, env) for r in right]

    # -- expressions --------------------------------------------------------

    def expr(self, node, env: Env, want_tuple: bool = False):
        """Visit a node for the calls in it and return its static type, None when it is not known."""
        if node is None:
            return None
        kind = node.type
        if kind == "call_expression":
            return self.call(node, env, want_tuple)
        if kind == "type_conversion_expression":
            return self.conversion(node, env, want_tuple)
        if kind == "func_literal":
            inner = env.child()
            self.bind_parameters(field_of(node, "parameters"), inner)
            result = field_of(node, "result")
            if result is not None and result.type == "parameter_list":
                self.bind_parameters(result, inner)
            self.block(field_of(node, "body"), inner, share=True)  # an anonymous function's calls are its enclosing function's
            return ("func", self.result_types(result, env.info, env.tparams))
        if kind == "identifier":
            return self.identifier_type(text(node), env)
        if kind == "selector_expression":
            return self.selector_type(node, env)
        if kind == "composite_literal":
            ty = self.type_node(field_of(node, "type"), env)
            self.expr(field_of(node, "body"), env)
            return ty
        if kind == "unary_expression":
            operator = node.child(0).type
            inner = self.expr(field_of(node, "operand"), env)
            if operator == "&":
                return ("ptr", inner) if inner is not None else None
            if operator == "*":
                return inner[1] if inner and inner[0] == "ptr" else None
            if operator == "<-":
                base = self.deref(inner)
                return base[1] if base and base[0] == "chan" else None
            return inner if operator in ("-", "+", "^") else ("builtin", "bool") if operator == "!" else None
        if kind == "parenthesized_expression":
            inner = named(node)
            return self.expr(inner[0], env) if inner else None
        if kind == "type_assertion_expression":
            self.expr(field_of(node, "operand"), env)
            return self.type_node(field_of(node, "type"), env)
        if kind == "index_expression":
            operand = self.structure(self.expr(field_of(node, "operand"), env))
            self.expr(field_of(node, "index"), env)
            if operand and operand[0] == "slice":
                return operand[1]
            if operand and operand[0] == "map":
                return operand[2]
            return ("builtin", "byte") if operand and operand[0] == "builtin" and operand[1] == "string" else None
        if kind == "slice_expression":
            ty = self.expr(field_of(node, "operand"), env)
            for child in named(node)[1:]:
                self.expr(child, env)
            return ty
        if kind in ("interpreted_string_literal", "raw_string_literal"):
            return ("builtin", "string")
        if kind == "int_literal":
            return ("builtin", "int")
        if kind == "float_literal":
            return ("builtin", "float64")
        if kind == "binary_expression":
            left = self.expr(field_of(node, "left"), env)
            right = self.expr(field_of(node, "right"), env)
            operator = field_of(node, "operator")  # a comment may sit between the left operand and the operator
            operator = operator.type if operator is not None else ""
            if operator in ("==", "!=", "<", ">", "<=", ">=", "&&", "||"):
                return ("builtin", "bool")
            # An untyped constant takes the type of the other operand, so two different operand types say nothing about
            # the result's; a shift has the type of its left operand.
            return left if operator in ("<<", ">>") or left == right else None
        for child in named(node):
            self.expr(child, env)
        return None

    def identifier_type(self, name: str, env: Env):
        local = env.local(name)
        if local is not None:
            return local[0] if local[1] != "type" else None
        package = env.info.package
        if len(package.names.get(name, ())) > 1 or self.unsure(package, name):
            return None
        if name in package.vars:
            return self.var_type(package, name)
        if name in package.funcs:
            return ("func", self.results_of(package.funcs[name]))
        return None

    def var_type(self, package: Package, name: str):
        info = package.vars[name]
        if info.conflict:
            return None
        if info.type_node is not None:
            return self.type_of(info.type_node, info.file)
        key = (id(package), name)
        if info.value is None or key in self.typing:
            return None
        if key in self.var_types:  # a table with thousands of elements is read once, not at every use of its name
            return self.var_types[key]
        outermost = not self.typing
        self.typing.add(key)
        self.quiet += 1
        caller_file, self.file = self.file, info.file  # the initialiser's own file decides which builds its methods are in
        try:
            ty = self.expr(info.value, Env(info.file, frozenset()))
        finally:
            self.file = caller_file
            self.quiet -= 1
            self.typing.discard(key)
        if outermost:  # one typed while another was being typed may have met the cycle guard: not kept
            self.var_types[key] = ty
        return ty

    def selector_type(self, node, env: Env):
        operand, member = field_of(node, "operand"), text(field_of(node, "field"))
        entry = self.qualifier(operand, env)
        if entry is not None:
            if entry.kind == "repo":
                package = entry.package
                if self.unsure(package, member):
                    return None
                if member in package.vars and len(package.names[member]) == 1:
                    return self.var_type(package, member)
                if member in package.funcs and len(package.names[member]) == 1:
                    return ("func", self.results_of(package.funcs[member]))
                return None
            if entry.kind in ("outside", "published", "cgo"):
                return ("ext", f"{entry.path}.{member}")
            return None
        if operand.type == "identifier" and self.is_unbound(text(operand), env):
            return None
        found = self.lookup(self.expr(operand, env), member)
        if found[0] == "field":
            return found[1]
        return ("ext", "value") if found[0] == "external" else None

    def qualifier(self, operand, env: Env) -> Import | None:
        """The import an identifier names when it is a package qualifier: no local, no package-level name, no
        universe name is it, and the file imports it."""
        if operand.type != "identifier":
            return None
        name = text(operand)
        if env.local(name) is not None or env.info.package.names.get(name) or name in UNIVERSE_FUNCTIONS | UNIVERSE_TYPES | UNIVERSE_VALUES:
            return None
        return self.import_entry(env.info, name)

    def is_unbound(self, name: str, env: Env) -> bool:
        """An identifier no scope declares and no import of the file is named for."""
        return (
            env.local(name) is None
            and not env.info.package.names.get(name)
            and name not in UNIVERSE_FUNCTIONS | UNIVERSE_TYPES | UNIVERSE_VALUES
            and self.import_entry(env.info, name) is None
            and not any(dot.package is not None and name in dot.package.names for dot in env.info.dots)
        )

    def name_value(self, name: str, env: Env):
        """What a bare name is when no local binds it: ("func", decls) ("type", ty) ("var", VarInfo) ("const",)
        ("universe", name) ("dot", imports) ("conflict",) ("hidden", package whose unreadable file may declare it) ("missing",)."""
        package = env.info.package
        kinds = package.names.get(name, set())
        if len(kinds) > 1:
            return ("conflict",)
        if kinds and self.unsure(package, name):
            return ("hidden", package)  # the file ripple could not read may declare it another way in a build that does not have the declaration found
        if "func" in kinds:
            return ("func", package.funcs[name])
        if "type" in kinds:
            return ("type", self.named(package.types[name]))
        if "var" in kinds:
            return ("var", package.vars[name])
        if "const" in kinds:
            return ("const",)
        if env.info.package.unreadable and (name in UNIVERSE_FUNCTIONS | UNIVERSE_TYPES | UNIVERSE_INTERFACES | UNIVERSE_VALUES):
            return ("hidden", package)  # a declaration ripple could not read may shadow it
        for dot in env.info.dots:
            if dot.package is not None and name in dot.package.names:
                package = dot.package
                if len(package.names[name]) > 1:
                    return ("conflict",)
                if self.unsure(package, name):
                    return ("hidden", package)
                if "func" in package.names[name]:
                    return ("func", package.funcs[name])
                if "type" in package.names[name]:
                    return ("type", self.named(package.types[name]))
                return ("var", package.vars.get(name))
        if name in UNIVERSE_FUNCTIONS:
            return ("universe", name)
        if name in UNIVERSE_TYPES or name in UNIVERSE_INTERFACES:
            return ("type", ("builtin", name))
        if name in UNIVERSE_VALUES:
            return ("const",)
        if env.info.dots:
            return ("dot", env.info.dots)
        return ("missing",)

    # -- calls --------------------------------------------------------------

    def conversion(self, node, env: Env, want_tuple: bool):
        """`G[A, B](x)` parses as a conversion: it is a call when G is a generic function. Otherwise it is a conversion,
        which runs no code."""
        ty = field_of(node, "type")
        self.expr(field_of(node, "operand"), env)
        if ty is not None and ty.type == "generic_type":
            function = field_of(ty, "type")
            found = self.function_named(function, env)
            if found is not None:
                self.edge(found, node)
                return self.first(self.results_of(found), want_tuple)
            if function.type in ("type_identifier", "identifier") and not self.is_type_expression(function, env):
                # `fns[k](x)` with a name as the index reads the same as `G[T](x)`: a value that is not a type is a
                # map or slice of functions, called by index.
                self.unresolved(node, shown(ty), INDEXED_FUNCTION)
                return None
        return self.type_node(ty, env)

    def function_named(self, node, env: Env) -> list[FuncDecl] | None:
        """The declarations of the repo function a bare or imported name denotes, None when it is not one."""
        if node.type in ("identifier", "type_identifier"):
            name = text(node)
            if env.local(name) is not None:
                return None
            found = self.name_value(name, env)
            return found[1] if found[0] == "func" else None
        entry = None
        if node.type == "selector_expression":
            entry = self.qualifier(field_of(node, "operand"), env)
            member = text(field_of(node, "field"))
        elif node.type == "qualified_type":
            entry = self.import_entry(env.info, text(field_of(node, "package")))
            member = text(field_of(node, "name"))
        if (
            entry is not None
            and entry.kind == "repo"
            and member in entry.package.funcs
            and len(entry.package.names[member]) == 1
            and not self.unsure(entry.package, member)
        ):
            return entry.package.funcs[member]
        return None

    @staticmethod
    def first(results: list, want_tuple: bool):
        if want_tuple:
            return ("tuple", results)
        return results[0] if results else None

    def call(self, node, env: Env, want_tuple: bool):
        function, arguments = field_of(node, "function"), field_of(node, "arguments")
        written = shown(function)
        # The arguments run first. A function literal among them is anonymous: its calls are the caller's.
        argument_types = [self.expr(a, env) for a in (named(arguments) if arguments is not None else [])]
        callee = unwrap(function, ("parenthesized_expression",))
        if callee.type == "index_expression" and field_of(callee, "operand") is not None:
            operand = field_of(callee, "operand")  # F[int](x): the operand is a function, or a map or slice of functions
            imported = operand.type == "selector_expression" and self.qualifier(field_of(operand, "operand"), env) is not None
            if imported or self.function_named(operand, env) is not None:
                callee = operand
        if callee.type == "generic_type":
            callee = field_of(callee, "type")
        if callee.type == "identifier":
            return self.call_name(node, callee, written, env, want_tuple, argument_types)
        if callee.type == "selector_expression":
            return self.call_selector(node, callee, written, env, want_tuple)
        if callee.type == "unary_expression" and callee.child(0).type == "*":
            return self.type_node(callee, env) if self.is_type_expression(field_of(callee, "operand"), env) else self.computed(node, callee, written, env)
        if callee.type == "func_literal":
            self.expr(callee, env)
            return None  # called where it is written: its calls are already the caller's
        if callee.type == "call_expression":
            ty = self.expr(callee, env)
            self.unresolved(node, written, RETURNED_FUNCTION)
            return self.first(ty[1], want_tuple) if ty and ty[0] == "func" else None
        if callee.type == "index_expression":
            self.expr(callee, env)
            self.unresolved(node, written, INDEXED_FUNCTION)
            return None
        if callee.type in ("parenthesized_type", "pointer_type", "array_type", "slice_type", "map_type", "function_type", "interface_type", "channel_type", "qualified_type", "type_identifier", "struct_type"):
            return self.type_node(callee, env)  # a conversion
        return self.computed(node, callee, written, env)

    def computed(self, node, callee, written: str, env: Env):
        self.expr(callee, env)
        self.unresolved(node, written, f"the callee is computed ({callee.type})")
        return None

    def is_type_expression(self, node, env: Env) -> bool:
        if node.type in ("qualified_type", "pointer_type", "slice_type", "map_type", "array_type", "channel_type", "function_type", "struct_type", "interface_type"):
            return True
        if node.type in ("identifier", "type_identifier"):
            local = env.local(text(node))
            if local is not None:
                return local[1] == "type"
            return self.name_value(text(node), env)[0] == "type"
        if node.type == "selector_expression":
            entry = self.qualifier(field_of(node, "operand"), env)
            return entry is not None and entry.kind == "repo" and "type" in entry.package.names.get(text(field_of(node, "field")), ())
        return False

    def call_name(self, node, callee, written: str, env: Env, want_tuple: bool, argument_types: list):
        name = text(callee)
        local = env.local(name)
        if local is not None:
            ty, how = local
            if how == "type":
                return ty  # a conversion to a type declared in the function
            self.unresolved(node, written, PARAMETER if how == "param" else LOCAL_FUNCTION)
            return self.first(ty[1], want_tuple) if ty and ty[0] == "func" else None
        found = self.name_value(name, env)
        kind = found[0]
        if kind == "func":
            self.edge(found[1], node)
            return self.first(self.results_of(found[1]), want_tuple)
        if kind == "type":
            return found[1]  # a conversion: no code runs
        if kind == "universe":
            self.external(node, BUILTIN)
            return self.builtin_result(name, node, env, argument_types)
        if kind == "var":
            ty = self.var_type(found[1].file.package, name) if found[1] is not None else None
            self.unresolved(node, written, PACKAGE_VARIABLE)
            return self.first(ty[1], want_tuple) if ty and ty[0] == "func" else None
        if kind == "dot":
            # Only a standard library dot import can prove the name is its function: a second package providing it would be a redeclaration.
            if any(dot.kind == "outside" and name in (stdlib_funcs(dot.path) or ()) for dot in found[1]):
                self.external(node, DOT_OUTSIDE)
            else:
                self.unresolved(node, written, UNDECLARED)
            return None
        if kind == "hidden":
            self.unresolved(node, written, self.unread(found[1]))
        else:
            self.unresolved(node, written, CONFLICT if kind == "conflict" else UNDECLARED)
        return None

    def builtin_result(self, name: str, node, env: Env, argument_types: list):
        arguments = named(field_of(node, "arguments")) if field_of(node, "arguments") is not None else []
        if name == "new" and arguments:
            inner = self.type_node(arguments[0], env)
            return ("ptr", inner) if inner else None
        if name == "make" and arguments:
            return self.type_node(arguments[0], env)
        if name == "append" and argument_types:
            return argument_types[0]
        if name in ("len", "cap", "copy"):
            return ("builtin", "int")
        return None

    def call_selector(self, node, callee, written: str, env: Env, want_tuple: bool):
        operand, member = field_of(callee, "operand"), text(field_of(callee, "field"))
        entry = self.qualifier(operand, env)
        if entry is not None:
            return self.call_package(node, entry, member, written, want_tuple)
        if operand.type == "identifier" and self.is_unbound(text(operand), env):
            self.unresolved(node, written, UNBOUND)
            return None
        owner = self.method_expression_type(operand, env)
        if owner is not None:
            found = self.lookup(owner, member)
            if found[0] == "method":
                self.edge(found[1], node)
                return None
            self.unresolved(node, written, "method expression: " + (found[1] if isinstance(found[1], str) else "the method is not provable"))
            return None
        receiver = self.expr(operand, env)
        found = self.lookup(receiver, member)
        kind = found[0]
        if kind == "method":
            self.edge(found[1], node)
            return self.first(self.results_of(found[1]), want_tuple)
        if kind == "field":
            self.unresolved(node, written, FIELD_FUNCTION)
            return self.first(found[1][1], want_tuple) if found[1] and found[1][0] == "func" else None
        if kind == "iface":
            self.unresolved(node, written, f"a method call on the interface {found[1].id}: which type's method runs is decided at run time")
            declared, _ = self.interface_methods(found[1])
            owner_type = declared.get(member)
            if owner_type is not None:
                element = owner_type.interface_methods[member]
                return self.first(self.result_types(field_of(element, "result"), owner_type.file, owner_type.tparams), want_tuple)
            return None
        if kind == "external":
            self.external(node, found[1])
            return ("ext", "value")
        self.unresolved(node, written, found[1] or self.untyped_reason(operand, env))
        return None

    def method_expression_type(self, operand, env: Env):
        """T for the operand of `T.M(x)` and `(*T).M(x)`, None when the operand is not a repo type."""
        operand = unwrap(operand, ("parenthesized_expression",))
        if operand.type == "unary_expression" and operand.child(0).type == "*":
            operand = unwrap(field_of(operand, "operand"), ("parenthesized_expression",))
        if operand.type == "identifier" and env.local(text(operand)) is None:
            found = self.name_value(text(operand), env)
            if found[0] == "type":
                return found[1]
        return None

    def call_package(self, node, entry: Import, member: str, written: str, want_tuple: bool):
        kind = entry.kind
        if kind == "repo":
            package = entry.package
            kinds = package.names.get(member, set())
            if len(kinds) > 1:
                self.unresolved(node, written, CONFLICT)
            elif kinds and self.unsure(package, member):
                self.unresolved(node, written, self.unread(package))
            elif "func" in kinds:
                self.edge(package.funcs[member], node)
                return self.first(self.results_of(package.funcs[member]), want_tuple)
            elif "type" in kinds:
                return self.named(package.types[member])  # a conversion
            elif "var" in kinds:
                self.unresolved(node, written, PACKAGE_VARIABLE)
            else:
                self.unresolved(node, written, OTHER_PACKAGE_NAME)
            return None
        if kind == "cgo":
            self.unresolved(node, written, CGO)  # C code may call any //export-ed Go function of the repo
            return None
        if kind == "missing":
            self.unresolved(node, written, MISSING_IMPORT)
            return None
        if kind == "published":
            self.unresolved(node, written, PUBLISHED_PACKAGE_NAME)
            return None
        funcs = stdlib_funcs(entry.path)
        if funcs is None:
            self.unresolved(node, written, OUTSIDE_PACKAGE_NAME)
            return None
        if member in funcs:
            self.external(node, STDLIB_FUNCTION)
        elif outside_type(f"{entry.path}.{member}") is None:
            self.unresolved(node, written, STDLIB_NOT_FUNCTION)
            return None
        return ("ext", f"{entry.path}.{member}")  # a function's result or, when the name is a type, a conversion to it (no call)

    def untyped_reason(self, operand, env: Env) -> str:
        if operand.type == "identifier":
            name = text(operand)
            local = env.local(name)
            if local is not None:
                return f"the receiver is a {local[1]} whose type ripple cannot tell"
            package = env.info.package
            if name in package.vars:
                return BUILD_VARIANT_VAR if package.vars[name].conflict else "the receiver is a package-level variable whose type ripple cannot tell"
            return "the receiver is a name ripple cannot find"
        words = {
            "call_expression": "the result of a call",
            "selector_expression": "a field or member",
            "index_expression": "an element of a collection",
        }
        return f"the receiver is {words.get(operand.type, 'an expression')} whose type ripple cannot tell"


def analyse(repo: Path, files: list[Path], profile: LanguageProfile = GO) -> tuple[Program, Resolver]:
    """The files read and resolved: the program and its resolver, which keeps what a Graph does not (the site of each external call)."""
    program = Program(Path(repo), files, profile)
    return program, Resolver(program).run()


def index_files(repo: Path, files: list[Path], skipped: list[str], profile: LanguageProfile = GO) -> Graph:
    """The Go front end (registry.py): the graph of the files read with tree-sitter-go."""
    program, resolver = analyse(repo, files, profile)
    graph = Graph(
        edges=resolver.edges,
        unresolved=resolver.unresolved_calls,
        external_calls=sum(resolver.external_reasons.values()),
        parse_errors=sorted(set(program.parse_errors)),
        skipped_dirs=skipped,
        external_reasons=dict(resolver.external_reasons),
    )
    for package in sorted(program.packages.values(), key=lambda p: p.id):
        first = package.files[0].rel
        graph.symbols[package.id] = Symbol(package.id, "module", first, 1, program.sources[first].tree.root_node.end_point[0] + 1)
        for symbol in package.symbols:
            graph.symbols[symbol.id] = symbol
        for info in package.files:
            graph.modules[info.rel] = package.id
    return graph


# What the registry (registry.py) asks of a front end: NotInstalled is this module's name for GoNotInstalled.
NotInstalled = GoNotInstalled
