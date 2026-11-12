"""The Java front end: read .java files, declare what they define, and hand the resolver the call sites.

Unlike the Python and TypeScript front ends this one does not write facts for the shared Resolver. Java picks a
method by its name AND its parameter types, from the whole hierarchy, and the shared Resolver binds one thing per
name (the first class that has it wins), which was measured to make a wrong edge (a subclass's `add(Item, int)`
chosen for a call of the superclass's `add(String)`) and to call every overloaded method "not a method". So the
declarations are read here (java_decls below, one pass over every file) and java_resolver.py decides what each call
site is, and writes the Graph itself. The edge kinds, queries, cache and merge are the shared ones.

Like the others it is purely syntactic and never runs the code. It needs tree-sitter and its Java grammar, the
optional extra `pip install 'ripple-code[java]'`, imported when the first Java file is parsed and never when ripple
is imported: a missing parser raises JavaNotInstalled, which says so.

Ids are dotted, like Python's, and a method carries the simple names of its parameter types as written (generics
dropped, a varargs parameter as `String...`), so reordering methods never renames one and an id reads in a list:
  com.acme.shop.Cart                       a class, interface, enum or record (a member type: Cart.Line)
  com.acme.shop.Cart.add(Item)             a method; a constructor is `<init>`: Cart.<init>(List)
  com.acme.shop.Cart.add(Item)#2           the second of two overloads written alike (type variables with other bounds)
  com.acme.shop.Ledger.runAll(List)$1      an anonymous class, numbered in source order within its member
  com.acme.shop.Ledger.runAll(List).Local  a local class, like a def inside a def
  com.acme.shop.Ledger.Op.ADD              an enum constant with a body: a class that extends Op
  src/main/java/com/acme/shop/Cart.java    the file: its path, with the extension, so it is never a class's id and a
                                           bare `Cart` names the class
A lambda or method reference is never a symbol: what it calls belongs to the member around it.

The source root of a file is proven, not guessed: its directory minus its `package` path. A file whose directory does
not end in its package path (a Java file a test compiles as data) is not indexed, and is listed with the files that
did not parse, with that reason. A default-package file is indexed with no package. Two files that define one fully
qualified name (Android product flavours, tooling samples) are indexed as neither: which one a build compiles is not
provable, so their types have no symbol, calls to them are unresolved, and the files share a module name, which the
index command warns about.

Not supported (the call is unresolved or the code is not read, never guessed): the members of a supertype from outside
the repo (see java_resolver.py), reflection, Lombok and annotation-processor output, qualified inner-class creation
(`outer.new Inner()`), and JDK 25 syntax the grammar does not know (`import module`, statements before `super(...)`),
whose file is listed as unparsable and read as far as the parser recovers.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from pathlib import Path

from .model import Graph, Symbol
from .profile import LanguageProfile

INSTALL_HINT = "pip install 'ripple-code[java]'"
REINSTALL = f"it needs tree-sitter>=0.25,<0.27 and tree-sitter-java>=0.23.5,<0.24, which {INSTALL_HINT} installs"


class JavaNotInstalled(ImportError):
    """tree-sitter or its Java grammar is missing. The message says what to install."""


NotInstalled = JavaNotInstalled  # the name the registry asks a front end for (registry.py)

_PARSER = None


def load_parser():
    """The Java parser. tree-sitter is imported here, the first time it is needed."""
    global _PARSER
    if _PARSER is None:
        try:
            import tree_sitter_java as grammar
            from tree_sitter import Language, Parser
        except ModuleNotFoundError as error:
            raise JavaNotInstalled(
                f"Java support is not installed (missing module: {error.name}); install it with: {INSTALL_HINT}"
            ) from error
        except ImportError as error:
            raise JavaNotInstalled(
                f"Java support cannot be loaded (a tree-sitter that does not fit: {error}); {REINSTALL}"
            ) from error
        try:
            _PARSER = Parser(Language(grammar.language()))
        except Exception as error:  # noqa: BLE001 (an ABI mismatch is a ValueError, an old API a TypeError, a bad library an OSError)
            raise JavaNotInstalled(
                f"Java support cannot be loaded (tree-sitter and its grammar do not fit: "
                f"{type(error).__name__}: {error}); {REINSTALL}"
            ) from error
    return _PARSER


# ---- which files are source -----------------------------------------------------------------------------------------

# Build output sits below the root in a multi-module repo, where the walk's skip of a root `build` does not reach. A
# directory is output only when the build tool that writes it is provably there: `target` beside a pom.xml, `build`
# beside a build.gradle(.kts), and an IDE's `out` or `bin` beside that IDE's project file.
_OUTPUT_OF = {
    "target": ("pom.xml",),
    "build": ("build.gradle", "build.gradle.kts"),
    "out": (".idea", "*.iml"),
    "bin": (".classpath",),
}


def _is_output_dir(directory: Path) -> bool:
    markers = _OUTPUT_OF.get(directory.name)
    if markers is None:
        return False
    try:
        return any(any(directory.parent.glob(marker)) if "*" in marker else (directory.parent / marker).exists() for marker in markers)
    except OSError:
        return False


def _is_repo_root(directory: Path) -> bool:
    try:
        return (directory / ".git").exists()
    except OSError:
        return False


def _output_directory(path: Path) -> Path | None:
    """The topmost build output directory above a file (see _OUTPUT_OF), or None. Only the directories above the file
    are looked at, up to the repo's own (the one with a .git), so a package that is really called `build` in a repo
    with no build file is still read, and a repo that sits inside a directory called `target` is not skipped whole."""
    found = None
    for parent in path.parents:
        if _is_output_dir(parent):
            found = parent
        if _is_repo_root(parent):
            break
    return found


def reads_as_source(path: Path, profile: LanguageProfile, repo: Path | None = None) -> bool:
    """False for a .java file under build output (Maven's target/, Gradle's build/, an IDE's out/ or bin/), which
    holds copies and generated sources of the real ones (see _output_directory)."""
    return _output_directory(path) is None


def ignored_directory(path: Path, profile: LanguageProfile, repo: Path) -> str | None:
    """The build output directory, repo-relative, that keeps a .java file from being read, which the walk lists with the
    skipped directories (`target` for `target/generated-sources/p/A.java`). None for a directory above the repo."""
    found = _output_directory(path)
    if found is None or not found.is_relative_to(repo):
        return None
    return found.relative_to(repo).as_posix()


def module_id(file: str) -> str:
    """The id of a source file: its path with the extension, so it is never a class's id (see the top of this file)."""
    return file


# ---- small readers of tree-sitter nodes -----------------------------------------------------------------------------


# tree-sitter keeps a comment as an "extra" node that may stand between ANY two tokens, so a comment can be a child of an
# argument list, a type list, a modifier list or a type, and sit inside the text of a name or a type. Everything below
# that counts, indexes or takes the first or last of a node's children goes through named(), and every text that is
# compared or parsed goes through text(), which cuts the comments out. (A walk that only looks for a node type may use
# named_children directly: a comment holds no code.)
COMMENTS = ("line_comment", "block_comment")


def named(node) -> list:
    """The named children of a node without its comments."""
    return [c for c in node.named_children if c.type not in COMMENTS]


def text(node) -> str:
    """The source of a node, each comment inside it replaced by a space."""
    if node is None:
        return ""
    raw = node.text
    if b"/" in raw:  # only then can a comment be inside
        cuts, stack = [], [node]
        while stack:
            n = stack.pop()
            if n.type in COMMENTS:
                cuts.append((n.start_byte, n.end_byte))
            else:
                stack.extend(n.named_children)
        if cuts:
            kept, at = [], 0
            for start, end in sorted(cuts):
                kept += [raw[at : start - node.start_byte], b" "]
                at = end - node.start_byte
            raw = b"".join(kept) + raw[at:]
    return raw.decode("utf-8", "replace")


def dotted(node) -> str:
    """A package or import name, written without the blanks and comments that may sit around its dots."""
    return "".join(text(node).split())


def line_of(node) -> int:
    return node.start_point[0] + 1


TYPE_DECLS = ("class_declaration", "interface_declaration", "enum_declaration", "record_declaration", "annotation_type_declaration")
KINDS = {
    "class_declaration": "class",
    "interface_declaration": "interface",
    "enum_declaration": "enum",
    "record_declaration": "record",
    "annotation_type_declaration": "interface",
}
CALLABLES = ("method_declaration", "constructor_declaration", "compact_constructor_declaration")
PRIMITIVES = frozenset({"int", "long", "short", "byte", "char", "boolean", "float", "double", "void"})
CONSTRUCTOR = "<init>"
# Annotations that put members in a class that no source holds (Lombok): a call of a name the class does not
# declare may be one of these, so it is never read as "no repo code has this name".
LOMBOK = frozenset(
    {
        "Data", "Getter", "Setter", "Value", "Builder", "SuperBuilder", "AllArgsConstructor", "NoArgsConstructor",
        "RequiredArgsConstructor", "With", "Delegate", "ToString", "EqualsAndHashCode", "Slf4j", "Log", "Log4j2", "Accessors",
    }
)  # fmt: skip


def modifiers_of(node) -> tuple[set[str], set[str]]:
    """(the modifier words, the simple names of the annotations) written on a declaration."""
    found = next((c for c in node.children if c.type == "modifiers"), None)
    words, annotations = set(), set()
    for child in found.children if found is not None else ():
        if child.type in COMMENTS:
            continue
        if child.type in ("marker_annotation", "annotation"):
            annotations.add(text(child.child_by_field_name("name")).rsplit(".", 1)[-1])
        else:
            words.add(text(child))
    return words, annotations


def type_parameters_of(node) -> list[str]:
    """The names of the type parameters a class or method declares, in order."""
    declared = node.child_by_field_name("type_parameters")
    found = []
    for child in named(declared) if declared is not None else ():
        if child.type == "type_parameter":
            name = next((x for x in named(child) if x.type in ("type_identifier", "identifier")), None)
            if name is not None:
                found.append(text(name))
    return found


def parameters_of(node) -> tuple[list[tuple[str, str]], bool]:
    """([(type as written, name)], whether the last is varargs). A varargs parameter is its array type."""
    declared = node.child_by_field_name("parameters")
    found, varargs = [], False
    for child in named(declared) if declared is not None else ():
        if child.type == "formal_parameter":
            found.append((text(child.child_by_field_name("type")), text(child.child_by_field_name("name"))))
        elif child.type == "spread_parameter":
            kind = next((c for c in named(child) if c.type not in ("modifiers", "variable_declarator")), None)
            declarator = next((c for c in named(child) if c.type == "variable_declarator"), None)
            found.append((text(kind) + "[]", text(declarator.child_by_field_name("name")) if declarator else ""))
            varargs = True
    return found, varargs


_ANNOTATION = re.compile(r"@[\w.]+(\([^)]*\))?\s*")
_BRACKETS = re.compile(r"\s*\[\s*\]")  # `char [ ]`
_DOTS = re.compile(r"(?<=\w)\s*\.\s*(?=\w)")  # `java . util`, as a comment between the parts leaves it (not the `...` of a varargs)


def strip_type(written: str) -> tuple[str, int]:
    """(`java.util.List`, 1) for `final @Ann java.util.List<Cart>[]`: the name without annotations and type
    arguments, and the number of array dimensions (a varargs `...` counts as one)."""
    written = _ANNOTATION.sub("", " ".join(written.split()))
    depth, kept = 0, []
    for ch in written:
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth -= 1
        elif depth == 0:
            kept.append(ch)
    name = _BRACKETS.sub("[]", _DOTS.sub(".", "".join(kept))).strip()
    dims = name.count("[]") + name.count("...")
    return name.replace("[]", "").replace("...", "").strip(), dims


def parameter_label(written: str, varargs: bool = False) -> str:
    """How an id names one parameter: the simple name of its type as written (`Entry` for `Map.Entry<K, V>`),
    its array dimensions, and `...` for a varargs one."""
    name, dims = strip_type(written)
    name = name.rsplit(".", 1)[-1]
    if varargs:
        return name + "[]" * (dims - 1) + "..."
    return name + "[]" * dims


# ---- what the files declare -----------------------------------------------------------------------------------------


class JavaType:
    """A type the repo declares: a class, interface, enum, record, or the anonymous class of a `new`, the body of an
    enum constant, or a local class (kind "anon", "enumconst", and a local class keeps its own kind)."""

    def __init__(self, fqn, kind, file, node, outer, pkg, finfo):
        self.fqn, self.kind, self.file, self.node, self.outer, self.pkg, self.finfo = fqn, kind, file, node, outer, pkg, finfo
        self.simple = fqn.rsplit(".", 1)[-1]
        self.members: dict[str, JavaType] = {}  # simple name -> member type
        self.methods: dict[str, list[JavaMethod]] = defaultdict(list)
        self.ctors: list[JavaMethod] = []
        self.fields: dict[str, tuple[str, bool]] = {}  # name -> (type as written, static)
        self.field_mods: dict[str, set[str]] = {}  # name -> the modifier words written on it (access decides who inherits it)
        self.supers: list[tuple[str, bool | None]] = []  # (as written, is `extends` of a class)
        self.fixed_supers: list[JavaType] | None = None  # an enum constant's body extends its enum
        self.type_parameters: set[str] = set()
        self.type_parameter_order: list[str] = []  # (a generic class's, in the order its supertypes' arguments follow)
        self.mods: set[str] = set()
        self.annotations: set[str] = set()
        self.components: list[tuple[str, str]] = []  # a record's components
        self.subs: list[JavaType] = []
        self.dup = False  # another file defines this fully qualified name
        self.ctx_method: JavaMethod | str | None = None  # the member a local or anonymous class sits in ("init": none)
        self.is_local = False
        self.name_node = None

    def __repr__(self):
        return self.fqn


class JavaMethod:
    """A method or constructor, with the id the graph gives it."""

    def __init__(self, id, name, owner, node, params, varargs, ret, mods, type_parameters):
        self.id, self.name, self.owner, self.node = id, name, owner, node
        self.params, self.varargs, self.ret, self.mods, self.type_parameters = params, varargs, ret, mods, type_parameters
        self.static = "static" in mods
        self.private = "private" in mods
        self.final = "final" in mods
        # who may call it, and who inherits it (JLS 6.6, 8.4.8): an interface's members are public, an enum's
        # constructors private, and what is written on nothing is package-private
        if "private" in mods or (name == CONSTRUCTOR and owner.kind == "enum"):
            self.access = "private"
        elif "public" in mods or owner.kind == "interface":
            self.access = "public"
        else:
            self.access = "protected" if "protected" in mods else "package"
        body = node.child_by_field_name("body") if node is not None else None
        has_body = body is not None
        # The parser recovers from an error in the header (a type annotation on a varargs parameter is one it does not
        # know) by leaving the parameter out, so what the method takes is not known: it may be any call's target.
        self.broken = node is not None and any(c.has_error for c in node.children if body is None or c.id != body.id)
        self.abstract = "abstract" in mods or (owner.kind == "interface" and "default" not in mods and not self.static and not has_body)

    def arity_ok(self, n: int) -> bool:
        if self.broken:
            return True
        k = len(self.params)
        return n == k or (self.varargs and n >= k - 1)


class FileInfo:
    """One read file: its package and imports, and the top-level types it declares."""

    def __init__(self, rel, pkg, root, tree):
        self.rel, self.pkg, self.root, self.tree = rel, pkg, root, tree
        self.single: dict[str, str] = {}  # simple name -> fully qualified (`import a.B;`)
        self.ondemand: list[str] = []  # `import a.*;` (a package, or a type)
        self.static_single: dict[str, list[str]] = defaultdict(list)  # member -> [type] (`import static a.B.m;`)
        self.static_ondemand: list[str] = []  # `import static a.B.*;`
        self.tops: dict[str, JavaType] = {}
        self.clash = ""  # the first fully qualified name here that another file defines too


class Declarations:
    """Every file's types, members, supertypes and imports: what the resolver looks things up in."""

    def __init__(self):
        self.types: dict[str, JavaType] = {}  # fully qualified name -> type (the first, for a name two files define)
        self.every_type: list[JavaType] = []
        self.by_pkg: dict[str, dict[str, JavaType]] = defaultdict(dict)
        self.files: list[FileInfo] = []
        self.methods: dict[str, JavaMethod] = {}  # id -> method, of the types that are not duplicated
        self.errors: list[str] = []  # files not read, or read in part
        self.anonymous: Counter = Counter()  # member id -> anonymous classes numbered in it so far
        self.anonymous_at: dict[int, JavaType] = {}  # the `new T() { ... }` node -> its anonymous class
        self.clashes: dict[str, list[str]] = {}  # fully qualified name -> the files that define it

    # -- reading a file --

    def add_file(self, path: Path, repo: Path, parser) -> None:
        rel = path.relative_to(repo).as_posix()
        mark = len(self.every_type)
        try:
            tree = parser.parse(path.read_bytes())
            self._declare_file(rel, tree, path.parent.relative_to(repo).as_posix())
        except Exception:  # noqa: BLE001 (a file the front end cannot read must cost that file, not the index)
            # Most often an OSError (the file went away since the walk) or a RecursionError (nested deeper than
            # the raised limit allows). Nothing it had half-declared is kept.
            for t in self.every_type[mark:]:
                if self.types.get(t.fqn) is t:
                    del self.types[t.fqn]
                if t.outer is None and self.by_pkg[t.pkg].get(t.simple) is t:
                    del self.by_pkg[t.pkg][t.simple]
            del self.every_type[mark:]
            self.errors.append(rel)

    def _declare_file(self, rel: str, tree, directory: str) -> None:
        root = tree.root_node
        pkg = ""
        for child in root.named_children:
            if child.type == "package_declaration":
                pkg = next((dotted(n) for n in named(child) if n.type in ("scoped_identifier", "identifier")), "")
        path = pkg.replace(".", "/")
        if pkg and not (directory == path or directory.endswith("/" + path)):
            self.errors.append(f"{rel} (package {pkg} does not match its directory, so its source root is not provable)")
            return
        info = FileInfo(rel, pkg, root, tree)
        for child in root.named_children:
            if child.type == "import_declaration":
                self._import(info, child)
        for child in root.named_children:
            if child.type in TYPE_DECLS:
                top = self.declare(child, None, pkg, info)
                info.tops[top.simple] = top
        if root.has_error:  # the parser recovers locally: the rest of the file is read, and the file is listed
            self.errors.append(rel)
        self.files.append(info)

    @staticmethod
    def _import(info: FileInfo, node) -> None:
        static = any(c.type == "static" for c in node.children)
        star = any(c.type == "asterisk" for c in node.children)
        name = next((dotted(n) for n in named(node) if n.type in ("scoped_identifier", "identifier")), "")
        if static and star:
            info.static_ondemand.append(name)
        elif static:
            owner, _, member = name.rpartition(".")
            info.static_single[member].append(owner)
        elif star:
            info.ondemand.append(name)
        else:
            info.single[name.rsplit(".", 1)[-1]] = name

    # -- declaring a type --

    def _add_type(self, t: JavaType) -> None:
        self.every_type.append(t)
        first = self.types.get(t.fqn)
        if first is None:
            self.types[t.fqn] = t
            return
        first.dup = t.dup = True
        files = self.clashes.setdefault(t.fqn, [first.file])
        if t.file not in files:
            files.append(t.file)

    def declare(self, node, outer, pkg, info, fqn=None, ctx_method=None) -> JavaType:
        name_node = node.child_by_field_name("name")
        name = text(name_node)
        if fqn is None:
            fqn = (outer.fqn + "." if outer else (pkg + "." if pkg else "")) + name
        t = JavaType(fqn, KINDS[node.type], info.rel, node, outer, pkg, info)
        t.name_node = name_node
        t.ctx_method = ctx_method
        self._add_type(t)
        if outer is not None and ctx_method is None:
            outer.members[name] = t
        if outer is None:
            self.by_pkg[pkg][name] = t
        t.mods, t.annotations = modifiers_of(node)
        t.type_parameter_order = type_parameters_of(node)
        t.type_parameters = set(t.type_parameter_order)
        superclass = node.child_by_field_name("superclass")
        for x in named(superclass) if superclass is not None else ():
            t.supers.append((text(x), True))
        interfaces = node.child_by_field_name("interfaces")
        for x in named(interfaces) if interfaces is not None else ():
            if x.type == "type_list":
                t.supers += [(text(y), False) for y in named(x)]
        for x in node.children:  # an interface's `extends`
            if x.type == "extends_interfaces":
                for y in named(x):
                    if y.type == "type_list":
                        t.supers += [(text(z), False) for z in named(y)]
        if node.type == "record_declaration":
            t.components, _ = parameters_of(node)
            t.fields.update({component: (kind, False) for kind, component in t.components})
            t.field_mods.update({component: {"private", "final"} for _, component in t.components})
        body = node.child_by_field_name("body")
        if body is not None:
            self.declare_body(t, body, pkg, info)
        return t

    def declare_body(self, t: JavaType, body, pkg, info) -> None:
        items = named(body)
        for c in list(items):
            if c.type == "enum_body_declarations":
                items += named(c)
        for c in items:
            if c.type in TYPE_DECLS:
                self.declare(c, t, pkg, info)
            elif c.type in CALLABLES:
                self._declare_callable(t, c)
            elif c.type in ("field_declaration", "constant_declaration"):
                mods, _ = modifiers_of(c)
                kind = text(c.child_by_field_name("type"))
                for d in named(c):
                    if d.type == "variable_declarator":
                        field = text(d.child_by_field_name("name"))
                        t.fields[field] = (kind, "static" in mods or t.kind == "interface")
                        t.field_mods[field] = mods | {"public"} if t.kind == "interface" else mods
            elif c.type == "enum_constant":
                name = text(c.child_by_field_name("name"))
                t.fields[name] = (t.simple, True)
                t.field_mods[name] = {"public", "static", "final"}
                constant_body = c.child_by_field_name("body")
                if constant_body is not None:
                    sub = JavaType(f"{t.fqn}.{name}", "enumconst", info.rel, c, t, pkg, info)
                    sub.name_node = c.child_by_field_name("name")
                    self._add_type(sub)
                    sub.supers = [(t.fqn, True)]
                    sub.fixed_supers = [t]
                    self.declare_body(sub, constant_body, pkg, info)
        # an overload written like an earlier one (type variables with other bounds) gets `#N`, in source order
        for name, found in [*t.methods.items(), (CONSTRUCTOR, t.ctors)]:
            written: Counter = Counter()
            for m in found:
                written[m.id] += 1
                if written[m.id] > 1:
                    m.id = f"{m.id}#{written[m.id]}"
        # local and anonymous classes inside method bodies, initializers and enum constant arguments
        for c in items:
            if c.type in (*CALLABLES, "field_declaration", "static_initializer", "block", "constant_declaration", "enum_constant"):
                owner = next((m for m in [*(x for ms in t.methods.values() for x in ms), *t.ctors] if m.node == c), None)
                target = c.child_by_field_name("body") if c.type in CALLABLES else c
                if c.type == "enum_constant":
                    target = c.child_by_field_name("arguments")
                if target is not None:
                    self._declare_nested(t, owner, target, pkg, info)

    def _declare_callable(self, t: JavaType, c) -> None:
        mods, _ = modifiers_of(c)
        if c.type == "method_declaration":
            name = text(c.child_by_field_name("name"))
            params, varargs = parameters_of(c)
            label = ",".join(parameter_label(w, varargs and i == len(params) - 1) for i, (w, _) in enumerate(params))
            m = JavaMethod(f"{t.fqn}.{name}({label})", name, t, c, params, varargs, text(c.child_by_field_name("type")), mods, set(type_parameters_of(c)))
            t.methods[name].append(m)
            return
        if c.type == "constructor_declaration":
            params, varargs = parameters_of(c)
        else:  # a compact canonical constructor of a record takes the components
            params, varargs = t.components, False
        label = ",".join(parameter_label(w, varargs and i == len(params) - 1) for i, (w, _) in enumerate(params))
        t.ctors.append(JavaMethod(f"{t.fqn}.{CONSTRUCTOR}({label})", CONSTRUCTOR, t, c, params, varargs, "", mods, set(type_parameters_of(c))))

    def _declare_nested(self, t: JavaType, owner, node, pkg, info) -> None:
        """Declare the local and anonymous classes written in one member, in source order."""
        base = owner.id if owner else t.fqn
        stack = list(reversed(node.named_children))  # (a walk)
        while stack:
            n = stack.pop()
            if n.type in TYPE_DECLS:
                local = self.declare(n, t, pkg, info, fqn=f"{base}.{text(n.child_by_field_name('name'))}", ctx_method=owner or "init")
                local.is_local = True
                continue
            body = next((c for c in n.named_children if c.type == "class_body"), None) if n.type == "object_creation_expression" else None
            if body is not None:
                self.anonymous[base] += 1
                anon = JavaType(f"{base}${self.anonymous[base]}", "anon", info.rel, n, t, pkg, info)
                anon.ctx_method = owner or "init"
                anon.name_node = n
                self.anonymous_at[n.id] = anon
                self._add_type(anon)
                anon.supers = [(text(n.child_by_field_name("type")), None)]
                self.declare_body(anon, body, pkg, info)
                stack += reversed([c for c in n.named_children if c.type != "class_body"])  # the arguments
                continue
            stack += reversed(n.named_children)

    # -- after every file --

    def finish(self) -> None:
        """Mark what a clash taints, and index the methods of the types that are not duplicated."""
        for t in self.every_type:
            outer = t
            while outer is not None and not t.dup:
                t.dup = outer.dup
                outer = outer.outer
        for info in self.files:
            info.clash = next((t.fqn for t in info.tops.values() if t.dup), "")
        for t in self.every_type:
            if t.dup:
                continue
            for m in [*(x for ms in t.methods.values() for x in ms), *t.ctors]:
                self.methods[m.id] = m


# ---- the front end --------------------------------------------------------------------------------------------------


def index_files(repo: Path, files: list[Path], skipped: list[str], profile: LanguageProfile) -> Graph:
    """The call graph of a Java repo (registry.py: the front end of the java profile)."""
    from .java_resolver import JavaResolver

    parser = load_parser()
    declarations = Declarations()
    for path in files:
        declarations.add_file(path, repo, parser)
    declarations.finish()
    resolved = JavaResolver(declarations, profile).run()

    symbols: dict[str, Symbol] = {}
    modules: dict[str, str] = {}
    for info in declarations.files:
        if info.clash:  # the files that define one name share the module name, which is what the index warns about
            modules[info.rel] = info.clash
        else:
            modules[info.rel] = info.rel
            symbols[info.rel] = Symbol(info.rel, "module", info.rel, 1, info.root.end_point[0] + 1)
    bases: dict[str, list[str]] = {}
    external_bases: dict[str, list[str]] = {}
    for t in declarations.every_type:
        if t.dup:
            continue
        symbols[t.fqn] = Symbol(t.fqn, "class", t.file, line_of(t.name_node), t.node.end_point[0] + 1)
        supers = resolved.supertypes(t)
        bases[t.fqn] = [s.fqn for s in supers if isinstance(s, JavaType) and not s.dup]
        if any(not isinstance(s, JavaType) for s in supers):
            external_bases[t.fqn] = [s[1] for s in supers if not isinstance(s, JavaType)]
    for m in declarations.methods.values():
        node = m.node.child_by_field_name("name") or m.node
        symbols[m.id] = Symbol(m.id, "method", m.owner.file, line_of(node), m.node.end_point[0] + 1)
    return Graph(
        symbols=symbols,
        edges=resolved.edges,
        unresolved=resolved.unresolved,
        external_calls=sum(resolved.external.values()),
        parse_errors=sorted(set(declarations.errors)),
        skipped_dirs=skipped,
        modules=modules,
        bases=bases,
        external_bases=external_bases,
        external_reasons=dict(resolved.external),
    )
