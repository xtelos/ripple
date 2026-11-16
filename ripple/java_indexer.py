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

import codecs
import hashlib
import os
import re
import stat
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

from .model import Graph, Symbol
from .profile import LanguageProfile

# What a .java file that was not read whole may declare, read from its text (the parser's tree is not used: the file is one it
# could not read, or one the walk never hands over). The text is first read as javac reads it (JLS 3.3: a Unicode escape is
# the character it spells, before anything is a token), then cut into tokens: a comment, a string, a text block and a character
# literal are tokens that name nothing, so the prose of a Javadoc ("this class provides ...") declares no type.
#
# The scan errs toward MORE names, never fewer (a name it takes that nothing declares costs a refusal, a declaration it misses
# costs a wrong answer). So a name is every run of characters that is not white space, a separator, an operator or a quote: Java
# allows `$`, a currency sign, a connector punctuation mark, a combining mark and any letter of Unicode in an identifier, and a
# list of the ones it allows is a list that goes out of date (Python's `\w` is not it). The blanks are Java's own (JLS 3.6), not
# Python's `\s`, which also takes the no-break space.
UNICODE_ESCAPE = re.compile(r"(?<!\\)((?:\\\\)*)\\u+([0-9a-fA-F]{4})")
BAD_ESCAPE = re.compile(r"(?<!\\)(?:\\\\)*\\u")  # a `\u` that spells nothing (javac refuses the file), or one a `\` made
_NOT_A_NAME = " \\t\\f\\r\\n(){}\\[\\];,.@=<>!~?:&|+\\-*/^%\"'\\\\"
TOKEN = re.compile(
    r"""(?P<ws>[\x20\t\f\r\n]+)
    |(?P<line>//[^\r\n]*)
    |(?P<block>/\*.*?\*/|/\*.*)
    |(?P<text>\"\"\"(?:\\.|[^\\])*?\"\"\")
    |(?P<string>"(?:\\.|[^"\\\r\n])*")
    |(?P<char>'(?:\\.|[^'\\\r\n])*')
    |(?P<number>\d[\w.]*)
    |(?P<name>[^NOTNAME0-9][^NOTNAME]*)
    |(?P<arrow>->|::)
    |(?P<other>.)""".replace("NOTNAME", _NOT_A_NAME),
    re.S | re.X,
)
TYPE_KEYWORDS = frozenset({"class", "interface", "enum", "record"})
# JLS 3.9: a reserved word is no method's name. The contextual keywords (`record`, `yield`, `var`, `sealed`, `permits`, `module`,
# `open`, `opens`, `requires`, `exports`, `provides`, `uses`, `to`, `with`, `transitive`, `when`) are not reserved, and each is a
# legal name for a method, so none is listed.
RESERVED = frozenset(
    {
        "abstract", "assert", "boolean", "break", "byte", "case", "catch", "char", "class", "const", "continue", "default", "do",
        "double", "else", "enum", "extends", "final", "finally", "float", "for", "goto", "if", "implements", "import", "instanceof",
        "int", "interface", "long", "native", "new", "package", "private", "protected", "public", "return", "short", "static",
        "strictfp", "super", "switch", "synchronized", "this", "throw", "throws", "transient", "try", "void", "volatile", "while",
        "true", "false", "null",
    }
)  # fmt: skip
# What may stand before the name of a call and never before the name of a method declaration. A declared method has a result type
# written before its name (JLS 8.4), and a type ends in a name, a primitive type or `void` (names), the `>` of its type arguments
# or the `]` of an array: so a `name(` after anything else is a call, a cast, a pattern or a statement, and a `name(` after a
# word that begins an expression (`return foo(x)`, `new Foo(x)`, `else foo()`) is one too. Any other token before it (a token
# this list does not know) is not taken to be that: the name is then noted with no arity.
EXPRESSION_AHEAD = frozenset(
    {".", "(", ",", "=", ";", "{", "}", ")", ":", "?", "->", "::", "@", "!", "~", "+", "-", "*", "/", "%", "&", "|", "^", "<"}
    | {"return", "new", "throw", "else", "case", "assert", "yield", "do", "instanceof"}
)
# A source of at most this many bytes is read whole. A larger one is a generated file ripple does not scan: a scan is linear but
# dense Java costs about a second a megabyte and a hundred megabytes of tokens at 8 MiB, and javac's own limits (65,535 methods and
# constant pool entries to a class) keep generated sources of real projects well below this. Its first HEAD bytes say its package,
# and the rest of it may declare anything (a bound on the cost of one file, so that a repo cannot make the walk cost what it likes).
UNREAD_LIMIT = 4 * 1024 * 1024
UNREAD_HEAD = 1024 * 1024

# directories note_unread_sources does not look into: a dependency's or a cache's, never the project's own, as the walk says
UNREAD_DIRECTORIES = {".git", "node_modules", "__pycache__", "site-packages"}

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


class Unread:
    """What the text of a .java file says it declares (scan_unread): its package ("" when none is written), whether the word
    `package` is written at all, the top-level type names (every type name where the braces do not balance, since then no
    depth says what is top level), the methods it may declare as (name, parameters, whether the last is varargs) where
    `parameters` is None when the number is not read with certainty (the list is not closed, or has a receiver parameter),
    and whether an escape in it spells nothing."""

    __slots__ = ("package", "package_keyword", "types", "methods", "bad_escape", "tokens")

    def __init__(self) -> None:
        self.tokens = 0  # how many tokens the text has that are not a comment
        self.package = ""
        self.package_keyword = False
        self.types: set[str] = set()
        self.methods: set[tuple[str, int | None, bool]] = set()
        self.bad_escape = False


def _parameters(tokens: list, i: int) -> tuple[int | None, bool]:
    """(how many parameters, whether the last is varargs) of a method declaration whose `(` is tokens[i]: the commas that are
    outside every bracket and every `<...>` (a `<` outside the parentheses is a type argument's; inside an annotation's they
    are an operator's, and are not counted). The number is None where it is not read with certainty: the parentheses are not
    closed, or the list has a receiver parameter (`void m(Foo this, int a)`: JLS 8.4.1, it takes one argument, and its `this`
    is no parameter), whatever a call of it then passes."""
    depth = angle = commas = 0
    empty, varargs, closed, receiver = True, False, False, False
    for j in range(i + 1, len(tokens)):
        token = tokens[j]
        if token in ("(", "[", "{"):
            depth += 1
        elif token in (")", "]", "}"):
            if depth == 0:
                closed = token == ")"
                break
            depth -= 1
        elif depth == 0 and token == "<":
            angle += 1
        elif depth == 0 and token == ">" and angle:
            angle -= 1
        elif token == "," and depth == 0 and angle == 0:
            commas += 1
        elif token == "." and tokens[j - 1] == "." and tokens[j - 2] == ".":
            varargs = True
        elif token == "this" and depth == 0:
            receiver = True
        empty = False
    if not closed or receiver:
        return None, False
    return (0 if empty else commas + 1), varargs


_CONTROLS = frozenset(chr(c) for c in (*range(0, 9), *range(0xE, 0x1B), *range(0x7F, 0xA0)))


def _squash(name: str) -> str:
    """A name without the characters javac ignores in an identifier (JLS 3.8: the Cf characters and the controls
    Character.isIdentifierIgnorable lists): `fo\\u00ADo` is `foo`."""
    if name.isascii():
        return name
    return "".join(c for c in name if c not in _CONTROLS and unicodedata.category(c) != "Cf")


def decode_source(data: bytes) -> str:
    """The text of a source file. javac's default encoding is the platform's, so the bytes may be anything: a byte-order mark names
    UTF-16 or UTF-32, UTF-8 is tried strictly, and any other bytes are read as ISO-8859-1, which gives every byte a character, so no
    byte sequence hides a name written in ASCII or in the Latin-1 letters (a name in another script, in an encoding that is neither
    UTF-8 nor marked, is read as the bytes it is: javac reads it as the platform says, and the repo's own callers say it the
    same way). A file that is a quarter NUL bytes is UTF-16 or UTF-32 with no mark: the NULs are dropped, which spells ASCII and
    Latin-1 names right in both byte orders. (An EBCDIC source is not read: it is no source that ASCII punctuation tells apart.)"""
    for mark, codec in (
        (codecs.BOM_UTF32_LE, "utf-32"),
        (codecs.BOM_UTF32_BE, "utf-32"),
        (codecs.BOM_UTF16_LE, "utf-16"),
        (codecs.BOM_UTF16_BE, "utf-16"),
    ):
        if data.startswith(mark):
            return data.decode(codec, "replace")
    if len(data) >= 8 and data.count(b"\x00") * 4 >= len(data):
        data = data.replace(b"\x00", b"")
    try:
        source = data.decode("utf-8")
    except UnicodeDecodeError:
        source = data.decode("latin-1")
    return source[1:] if source.startswith(chr(0xFEFF)) else source


def read_unread_source(path: str, repo_real: str | None) -> tuple[str, bytes]:
    """(what the file is, its bytes) for a .java name the walk met: only a REGULAR file inside the repo is ever opened, and no more
    than UNREAD_LIMIT bytes of it are read.
      "ok"       a regular file: its bytes.
      "huge"     a regular file larger than UNREAD_LIMIT: its first UNREAD_HEAD bytes.
      "absent"   nothing there to declare anything: a link that leads nowhere (dangling, or around a loop), a named pipe, a
                 device, a socket, a directory called X.java. None of them is opened (a read of a pipe blocks for ever, and of
                 /dev/zero never ends); the type is looked at first, and again on the descriptor, so a name swapped for a pipe
                 between the two is not read either (and the open does not block: O_NONBLOCK).
      "outside"  a link to a file outside the repo (`repo_real` is the repo's real path): not read.
      "error"    a regular file that could not be opened or read (a permission, it went away)."""
    try:
        info = os.lstat(path)
    except (FileNotFoundError, NotADirectoryError):
        return "absent", b""
    except OSError:
        return "error", b""
    target = path
    if stat.S_ISLNK(info.st_mode):
        try:
            target = os.path.realpath(path, strict=True)
        except (OSError, RuntimeError):
            return "absent", b""
        try:
            info = os.stat(target)  # (the type of what the link leads to; nothing of it is read)
        except OSError:
            return "absent", b""
        if stat.S_ISREG(info.st_mode) and repo_real is not None and not (target == repo_real or target.startswith(repo_real + os.sep)):
            return "outside", b""
    if not stat.S_ISREG(info.st_mode):
        return "absent", b""
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    chunks: list[bytes] = []
    try:
        fd = os.open(target, flags)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                return "absent", b""
            size = 0
            while size <= UNREAD_LIMIT:
                chunk = os.read(fd, min(1 << 20, UNREAD_LIMIT + 1 - size))
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
        finally:
            os.close(fd)
    except OSError:
        return "error", b""
    data = b"".join(chunks)
    if len(data) > UNREAD_LIMIT:
        return "huge", data[:UNREAD_HEAD]
    return "ok", data


def scan_unread(source: str) -> Unread:
    """Read the declarations out of the text of a file the parser did not read whole (see Unread). It over-reads: a name that
    is not declared costs a refusal, a declared name it misses would cost a wrong answer. So a method name is noted wherever the
    syntax allows one (see EXPRESSION_AHEAD), whatever else the text there may be, a name with no arity wherever the arity is
    not read with certainty, and every name that follows `class`, `interface`, `enum`, `record` (and so `@interface`) is a type."""
    found = Unread()
    source = UNICODE_ESCAPE.sub(lambda m: m.group(1) + chr(int(m.group(2), 16)), source)
    found.bad_escape = BAD_ESCAPE.search(source) is not None
    tokens, names = [], []  # the tokens that are not a comment, and whether each is a name (a word, not a string or a mark)
    for m in TOKEN.finditer(source):
        kind = m.lastgroup
        if kind not in ("ws", "line", "block"):
            tokens.append(_squash(m.group()) if kind == "name" else m.group())
            names.append(kind == "name")
    found.tokens = len(tokens)
    top: set[str] = set()
    every: set[str] = set()
    depth, balanced = 0, True
    package: list[str] = []
    for i, token in enumerate(tokens):
        prev = tokens[i - 1] if i else ""
        prev_is_name = names[i - 1] if i else False
        if names[i]:
            if token == "package":
                found.package_keyword = True
                if depth == 0 and not package:  # the declaration: the names and dots up to the `;`
                    words = []
                    for word in tokens[i + 1 : i + 1 + 4096]:
                        if word == ";":
                            break
                        words.append(word)
                    package = words
                    names_only = all(w == "." or names[i + 1 + k] for k, w in enumerate(words))
                    found.package = "".join(words) if names_only else ""
            elif prev in TYPE_KEYWORDS and prev_is_name:
                every.add(token)
                if depth == 0:
                    top.add(token)
                if prev == "record":
                    found.methods.update(_accessors(tokens, i))
        elif token == "(" and prev_is_name and prev not in RESERVED:
            before = tokens[i - 2] if i > 1 else ""
            if before in TYPE_KEYWORDS and i > 1 and names[i - 2]:
                pass  # a record's header (its components are read at the name: _accessors) is no method
            elif before in EXPRESSION_AHEAD:
                pass  # a call, a cast or a pattern: a declared method has its result type before its name
            elif (i > 1 and names[i - 2]) or before in (">", "]"):
                found.methods.add((prev, *_parameters(tokens, i)))
            else:  # something no declaration has before its name, and no call: the name is noted, with no arity
                found.methods.add((prev, None, False))
        elif token == "{":
            depth += 1
        elif token == "}":
            if depth == 0:
                balanced = False
            else:
                depth -= 1
    found.types = top if balanced and depth == 0 else every
    return found


def _accessors(tokens: list, i: int) -> set[tuple[str, int | None, bool]]:
    """The accessor method every component of a record has (JLS 8.10.3): `record R<T>(int a, List<T> b)` declares `a()` and
    `b()`, which no `a(` of the text shows. tokens[i] is the record's name; a name before a `,` or the `)` of the header,
    outside every inner bracket, is taken to be a component (so is a name of a type argument there: it costs a refusal)."""
    j = i + 1
    if j < len(tokens) and tokens[j] == "<":  # the type parameters
        angle = 0
        while j < len(tokens):
            angle += tokens[j] == "<"
            angle -= tokens[j] == ">"
            j += 1
            if angle == 0:
                break
    components: set[str] = set()
    if j >= len(tokens) or tokens[j] != "(":
        return set()
    depth = 0
    for k in range(j, len(tokens)):
        token = tokens[k]
        if token in ("(", "[", "{"):
            depth += 1
        elif token in (")", "]", "}"):
            depth -= 1
            if depth == 0:
                if token == ")" and k > j + 1:
                    components.add(tokens[k - 1])
                break
        elif token == "," and depth == 1:
            components.add(tokens[k - 1])
    return {(name, 0, False) for name in components if name not in RESERVED and name[:1] not in ",()<>[]{}.;@"}


class FileInfo:
    """One read file: its package and imports, and the top-level types it declares."""

    def __init__(self, rel, pkg, root, tree):
        self.rel, self.pkg, self.root, self.tree = rel, pkg, root, tree
        self.single: dict[str, str] = {}  # simple name -> fully qualified (`import a.B;`)
        self.ondemand: list[str] = []  # `import a.*;` (a package, or a type)
        self.static_single: dict[str, list[str]] = defaultdict(list)  # member -> [type] (`import static a.B.m;`)
        self.static_ondemand: list[str] = []  # `import static a.B.*;`
        self.unread_import = False  # an import declaration the parser met an error in: it may supply any type name
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
        # What a file that was not read whole may have declared (a file whose directory is not its package's, one the parser met an
        # error in, one the walk never hands over: scan_unread reads their text). For each package, the top-level type names such
        # files write, which may be types that no declaration shows, and the packages where no name can be told (an escape that
        # spells nothing). `unseen_methods`: for each method name, the (parameters, varargs) such a file may declare it with
        # (parameters None: any number). `unseen_anywhere`: a file went unread
        # and its package is not known, so a type of any package that no file shows may be what a simple name means (an on-demand
        # import cannot say what it is: java_resolver._on_demand_type; a qualified name cannot either: JavaResolver.unread_type).
        # `unseen_unreadable`: a file could not even be read as
        # text, so it may declare any method. A call goes to repo code only through a method some file declares: those the
        # parser read, or those named here (java_resolver.JavaResolver.call). `partial_files`: the files the parser read in part
        # (its tree is not evidence of what the file declares: a parse error recovers a structure that is not the file's).
        self.unseen_names: dict[str, set[str]] = defaultdict(set)
        self.unseen_packages: set[str] = set()
        self.unseen_methods: dict[str, set[tuple[int | None, bool]]] = defaultdict(set)
        self.partial_files: set[str] = set()
        self._repo_real: str | None = None  # the repo's real path: a symlink that leads out of it is not read
        self._repo_given: str | None = None  # the repo's path as the walk was given it (the paths it yields start with it)
        self.unseen_anywhere = False
        self.unseen_unreadable = False
        self._handed_over: set[bytes] = set()  # the digest of the text of every file the walk handed over and that was taken in
        self.anonymous: Counter = Counter()  # member id -> anonymous classes numbered in it so far
        self.anonymous_at: dict[int, JavaType] = {}  # the `new T() { ... }` node -> its anonymous class
        self.clashes: dict[str, list[str]] = {}  # fully qualified name -> the files that define it

    # -- reading a file --

    def add_file(self, path: Path, repo: Path, parser) -> None:
        rel = path.relative_to(repo).as_posix()
        self._repo_real = self._repo_real or os.path.realpath(repo)
        self._repo_given = self._repo_given or str(repo)
        mark = len(self.every_type)
        try:
            data = path.read_bytes()
            tree = parser.parse(data)
            self._declare_file(rel, tree, path.parent.relative_to(repo).as_posix())
            self._handed_over.add(hashlib.blake2b(data, digest_size=16).digest())
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
            self.note_unseen_file(path)

    @staticmethod
    def _package_of(root) -> str:
        pkg = ""
        for child in root.named_children:
            if child.type == "package_declaration":
                pkg = next((dotted(n) for n in named(child) if n.type in ("scoped_identifier", "identifier")), "")
        return pkg

    def note_unread_sources(self, repo: Path, read: list[Path]) -> None:
        """Note every .java file below the repo that the walk did not hand over as unseen (see unseen_names). The walk leaves out
        package-info.java (javac accepts a class there), module-info.java, build output (target/, build/, out/, bin/ below the
        file that makes it output), hidden directories and a few more: each is still repo code that javac may compile, and a type it
        declares comes before every on-demand import of its package, and a method it declares is one a call may run. Only a
        dependency directory the walk never reads (node_modules, site-packages, __pycache__) and .git are not looked into. The text is
        read, not parsed: a file that declares no type and no method (an ordinary package-info.java, a module-info.java) notes nothing.

        Only a regular file inside the repo is ever opened (read_unread_source). A name that is a named pipe, a device, a socket or a
        directory, a link that leads nowhere (dangling, or around a loop), is no source file and declares nothing. A link that leads
        out of the repo is source the build may compile and ripple will not read: it may declare anything, and is listed. A directory
        that cannot be listed may hold any file, and is listed too."""
        self._repo_real = self._repo_real or os.path.realpath(repo)
        self._repo_given = self._repo_given or str(repo)
        seen = {str(p) for p in read}

        def unlistable(error: OSError) -> None:
            self._cannot_read(str(error.filename), "directory cannot be listed: it may hold any source file")

        for dirpath, dirnames, filenames in os.walk(repo, onerror=unlistable):
            dirnames[:] = sorted(d for d in dirnames if d not in UNREAD_DIRECTORIES)
            for name in sorted(filenames):
                if name.endswith(".java") and os.path.join(dirpath, name) not in seen:
                    kind, data = read_unread_source(os.path.join(dirpath, name), self._repo_real)
                    if kind != "absent":
                        self._note_source(Path(dirpath) / name, kind, data, "")

    def note_unseen_file(self, path: Path, pkg: str = "", read_as: str = "") -> None:
        """Note what the text of one file declares (see unseen_names); `pkg` is the package its tree gave, when it gave one. A file
        that cannot be read (it went away since the walk saw it, a permission) may declare anything in any package."""
        kind, data = read_unread_source(str(path), self._repo_real)
        self._note_source(path, "error" if kind == "absent" else kind, data, pkg, read_as)

    def _cannot_read(self, shown: str, why: str) -> None:
        """A file ripple cannot read may declare any type of any package and any method: the repo's calls are unresolved until
        something says otherwise. The file is listed with the ones that did not parse, with the reason."""
        self.unseen_anywhere = self.unseen_unreadable = True
        self.errors.append(f"{self._shown(shown)} ({why})")

    def _shown(self, path: str) -> str:
        """A path as the list of files that did not parse shows it: relative to the repo, as the walk was given it."""
        return os.path.relpath(path, self._repo_given) if self._repo_given and os.path.isabs(path) else path

    def _note_source(self, path: Path, kind: str, data: bytes, pkg: str = "", read_as: str = "") -> None:
        """Note what the bytes of an unread source (read_unread_source) declare. `read_as` is the file's repo-relative path when the
        parser read it in part (see partial_files)."""
        shown = read_as or self._shown(str(path))
        if kind == "outside":
            self._cannot_read(shown, "a link out of the repo: not read, and it may declare anything")
        elif kind == "error":
            self._cannot_read(shown, "could not be read: it may declare anything")
        elif kind == "huge":
            # Too large to scan, so any method may be declared in it (the question a call asks, unseen_method, names no type, and
            # no part of the file says which). Its package is read from the head, since a package declaration is the first thing a
            # file says: any type of that package may be declared in it. Only a head that says no package (an unreadable name, or
            # nothing but comments) leaves the package unknown, and then any type of any package may be.
            head = decode_source(data)
            found = scan_unread(head[: head.rfind("\n") + 1] or head)  # (without the line the cut left half of: it may be a half token)
            self.unseen_unreadable = True
            self.errors.append(f"{shown} (larger than {UNREAD_LIMIT // 2**20} MiB: not scanned, it may declare any method and any type of its package)")
            package = pkg or found.package
            if (not package and found.package_keyword) or found.tokens == 0:
                self.unseen_anywhere = True
            else:
                self.unseen_packages.add(package)
        else:
            if hashlib.blake2b(data, digest_size=16).digest() in self._handed_over:
                return  # a copy of a file the walk handed over (build output keeps copies of sources): it declares what that file does
            self._note_unseen(pkg, decode_source(data))

    def _declare_file(self, rel: str, tree, directory: str) -> None:
        root = tree.root_node
        pkg = self._package_of(root)
        path = pkg.replace(".", "/")
        if pkg and not (directory == path or directory.endswith("/" + path)):
            self.errors.append(f"{rel} (package {pkg} does not match its directory, so its source root is not provable)")
            self._note_parsed(rel, root.text, pkg, "")
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
            self._note_parsed(rel, root.text, pkg, rel)
        self.files.append(info)

    def _note_parsed(self, rel: str, data: bytes, pkg: str, read_as: str) -> None:
        """Note what the text of a file the parser read says it declares: the file read in part (`read_as` is its path, see
        partial_files), or one whose directory is not its package's. The text is read from the bytes, not from the tree's decoding."""
        if read_as:
            self.partial_files.add(read_as)
        huge = len(data) > UNREAD_LIMIT
        self._note_source(Path(rel), "huge" if huge else "ok", data[:UNREAD_HEAD] if huge else data, pkg, read_as or rel)

    def _note_unseen(self, pkg: str, source: str) -> None:
        """Remember what a file that was not read whole may declare (see unseen_names); `pkg` is the package the parser read, if it
        did. The methods of a file the parser read in part are all unseen, the ones the parser saw too: after an error the
        structure it recovered, the owner of a method and the supertypes of the owner, is not evidence of what the file declares."""
        found = scan_unread(source)
        pkg = pkg or found.package
        if not pkg and found.package_keyword:
            self.unseen_anywhere = True  # a package declaration is written and its name cannot be told
        if found.bad_escape:
            self.unseen_packages.add(pkg)  # an escape that spells nothing may be part of any name
        self.unseen_names[pkg].update(found.types)
        for name, parameters, varargs in found.methods:
            self.unseen_methods[name].add((parameters, varargs))

    @staticmethod
    def _import(info: FileInfo, node) -> None:
        if node.has_error:  # `import module java.base;` (JDK 23 and later) is no import the grammar knows: it imports whole modules
            info.unread_import = True
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
    from . import java_jdk
    from .java_resolver import JavaResolver

    parser = load_parser()
    declarations = Declarations()
    for path in files:
        declarations.add_file(path, repo, parser)
    declarations.note_unread_sources(repo, files)
    declarations.finish()
    resolved = JavaResolver(declarations, profile).run()
    refused = java_jdk.refusal()  # a JDK table that was needed and could not be trusted: every JDK type was unknown, say so

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
        unread=[refused] if refused else [],
    )
