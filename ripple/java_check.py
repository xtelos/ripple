"""`check` for Java on Maven: run `mvn test` and read the verdict from surefire's XML reports.

The verdict is never the exit code alone and never a match on the text of the log. Surefire writes one
TEST-*.xml per test class (a testcase element per test, with a failure, error or skipped child when it did
not pass), and what comes out here is what the reports of THIS run prove, cross-checked against Maven's exit
status and against what was asked for. These cases come out as what they are, never as "passed", and each has
a captured run under tests/fixtures/java_check/captures:

- a selector that matches nothing in a class that exists (`-Dtest=A#nosuch`: Maven exits 0 and runs 0 tests,
  and writes no report), and a class that matches nothing (Maven fails),
- a compile failure in main or test code, a module that never reached its test phase (reactor SKIPPED),
- a JVM that crashed or called System.exit (surefire fails the build and writes no report for the class),
- a surefire fork timeout (the report says the test passed, Maven exits 1) and ripple's own time limit,
- a test that was skipped or disabled when it was asked for, and every test of the run skipped,
- a test class in src/test that surefire's name patterns do not pick up (the run is green without it),
- `skipTests` and `testFailureIgnore` in the pom (Maven exits 0 with no report, or with a failure in one),
- a report left by an EARLIER run: see snapshot() and fresh().

A test class that the plan expects results of (every class with a JUnit test method in the modules a run
covers, from the source and from the classes Maven compiled, and every class or method asked for, whatever
broader request covers it) must have a testcase in a fresh report, per module. A module that reports success
with no such evidence makes the run not passed, and a prediction that cannot be relied on (a module with test
sources and no current compiled classes, results from a module that was predicted empty) is a problem, never a
reason to skip the check. The middle ground is explicit (see MARKERS and `decide`): a class ripple KNOWS holds
tests (the known set, through the whole supertype closure as its own module's classpath resolves it) that has no
result is a problem; a class ripple cannot decide (an annotation it does not know on a void method, a supertype
outside the repo) is a note that names it, unless it was asked for by name. Two checks in one repo are serialized.

This is a runner for checker.py: `run(repo, tests, timeout)` returns the keys of the generic check (passed,
exit_code, timed_out, duration_s, command, summary, failures, failure_count) plus `problems` (why a run is
not a clean pass), `counts` and `notes`. checker.RUNNERS names it. Which build tool a repo has is
decided once, by `build_tool`: a repo with only Gradle files at the root goes to gradle_check.py (same contract, the
verdict read from what Gradle's own test listener records), one with a pom.xml and Gradle files both is refused (ripple does not
guess which build is the repo's), and one with a pom.xml (or none: refused for that) is run here.
"""

from __future__ import annotations

import contextlib
import errno
import hashlib
import os
import re
import shlex
import shutil
import stat
import time
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from .checker import MAX_COMMAND, MAX_FAILURES, check_tests, clip, run_process

MAX_PROBLEMS = 10
MAX_NAMES = 5
MAX_CLASSES = 1500  # a directory that holds more test classes than this is refused: one -Dtest value must stay a sane size
CLOCK_SLACK_NS = 2_000_000_000  # a report written by this run is never older than its start by more than the clock's granularity
MAX_REPORT_BYTES = 64_000_000
MAX_CLASS_BYTES = 5_000_000  # a class file larger than this is not read: javac's own limit for one is far below it
MAX_SOURCE_BYTES = 8_000_000
IDENT =r"[^\W\d][\w$]*"
IDENT_RE = re.compile(rf"^{IDENT}$")
PACKAGE_RE = re.compile(rf"^{IDENT}(?:\.{IDENT})*$")
# What the build can be told from the environment or .mvn/ that narrows or skips tests.
HAZARD = re.compile(
    r"(?<![\w.])-D(skipTests|maven\.test\.skip|skipITs|test|groups|excludedGroups|maven\.test\.failure\.ignore|"
    r"testFailureIgnore|surefire\.[\w.]+|failIfNoTests|skip)(?=[=\s]|$)"
)
POM_HAZARD = re.compile(r"<(skipTests|maven\.test\.skip|groups|excludedGroups|testFailureIgnore)>")


# ---- reading Java source -----------------------------------------------------------------------

_LITERAL = re.compile(r'//[^\n]*|/\*.*?\*/|"""(?:\\.|.)*?"""|"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\'', re.S)
_DECL = re.compile(rf"(?<![\w.$])(class|interface|enum|record|@interface)\s+({IDENT})")
_PACKAGE = re.compile(rf"^\s*package\s+({IDENT}(?:\s*\.\s*{IDENT})*)\s*;", re.M)
_ARGS = r"(?:\s*\((?:[^()]|\((?:[^()]|\([^()]*\))*\))*\))?"
_TEST_ANNOTATION = re.compile(
    r"@(?:[\w.]+\.)?(?:Test|ParameterizedTest|RepeatedTest|TestFactory|TestTemplate|CartesianTest)\b" + _ARGS
)
_AFTER_ANNOTATION = re.compile(
    r"\s*(?:@[\w.]+" + _ARGS + r"\s*)*(?:(?:public|protected|private|static|final|abstract|synchronized)\s+)*"
    rf"(?:<[^>]*>\s*)?[\w<>\[\],.? ]+?\s+({IDENT})\s*\("
)
_JUNIT3 = re.compile(r"\bextends\s+(?:junit\.framework\.)?TestCase\b")
_JUNIT3_METHOD = re.compile(rf"\bpublic\s+void\s+(test\w*)\s*\(")


def blank(text: str) -> str:
    """text with comments, strings and char literals blanked (newlines kept), so braces and keywords in them count for nothing."""
    return _LITERAL.sub(lambda m: re.sub(r"[^\n]", " ", m.group()), text)


@dataclass(frozen=True)
class JavaClass:
    name: str  # the simple name
    chain: tuple[str, ...]  # Outer, Inner: the classes it is nested in, outermost first, itself last
    abstract: bool
    tests: tuple[str, ...]  # the names of its own JUnit test methods (a name twice: two overloads)
    mentions: frozenset[str]  # every name followed by a parenthesis in its own body (for "declared but not a test")
    nested_tests: bool  # a class nested in it has tests


@dataclass(frozen=True)
class JavaFile:
    package: str
    classes: tuple[JavaClass, ...]

    def binary(self, cls: JavaClass) -> str:
        name = "$".join(cls.chain)
        return f"{self.package}.{name}" if self.package else name


def read_java(text: str) -> JavaFile:
    """The package and the classes (with their JUnit test methods) a Java source file declares. Read from text, no parser needed."""
    clean = blank(text)
    match = _PACKAGE.search(clean)
    package = re.sub(r"\s+", "", match.group(1)) if match else ""
    frames: list[dict] = []  # classes whose body is open
    done: list[dict] = []
    depth, pending = 0, None
    decls = {m.start(): m for m in _DECL.finditer(clean)}
    i = 0
    while i < len(clean):
        ch = clean[i]
        if i in decls:
            member = (not frames and depth == 0) or (frames and depth == frames[-1]["depth"])
            if member:
                pending = decls[i]
            i = decls[i].end()
            continue
        if ch == "{":
            depth += 1
            if pending is not None:
                head = clean[max(0, pending.start() - 200) : pending.start()]
                head = re.split(r"[;{}]", head)[-1]
                parent = frames[-1]["chain"] if frames else ()
                frame = {
                    "name": pending.group(2),
                    "chain": parent + (pending.group(2),),
                    "abstract": bool(re.search(r"\babstract\b", head)) or pending.group(1) in ("interface", "@interface"),
                    "depth": depth,
                    "start": i + 1,
                    "header": clean[pending.end() : i],
                    "kids": [],
                }
                if frames:
                    frames[-1]["kids"].append(frame)
                frames.append(frame)
                done.append(frame)
                pending = None
        elif ch == "}":
            if frames and depth == frames[-1]["depth"]:
                frames.pop().__setitem__("end", i)
            depth -= 1
        elif ch == ";" and pending is not None:
            pending = None
        i += 1
    for frame in frames:  # unterminated: the rest of the file
        frame.setdefault("end", len(clean))
    classes = []
    for frame in done:
        body = list(clean[frame["start"] : frame["end"]])
        for kid in frame["kids"]:
            lo, hi = kid["start"] - frame["start"], kid["end"] - frame["start"]
            for k in range(max(lo, 0), min(hi, len(body))):
                if body[k] != "\n":
                    body[k] = " "
        depth_in = 0  # what is inside a method body or an initializer is no member: a local class there is not this class's
        for k, c in enumerate(body):
            if c == "{":
                depth_in += 1
            elif c == "}":
                depth_in -= 1
            elif depth_in > 0 and c != "\n":
                body[k] = " "
        own = "".join(body)
        names = []
        for ann in _TEST_ANNOTATION.finditer(own):
            after = _AFTER_ANNOTATION.match(own, ann.end())
            if after:
                names.append(after.group(1))
        if _JUNIT3.search(frame["header"]):
            names.extend(_JUNIT3_METHOD.findall(own))
        mentions = frozenset(re.findall(rf"({IDENT})\s*\(", own))
        classes.append(
            JavaClass(frame["name"], frame["chain"], frame["abstract"], tuple(names), mentions, False)
        )
    # a class has nested tests when a class below it (same chain prefix) has tests of its own
    result = []
    for cls in classes:
        below = any(o.chain[: len(cls.chain)] == cls.chain and o is not cls and o.tests for o in classes)
        result.append(JavaClass(cls.name, cls.chain, cls.abstract, cls.tests, cls.mentions, below))
    return JavaFile(package, tuple(result))


def read_regular(path: Path | str, root: Path | str, cap: int, follow: bool = False) -> bytes:
    """The bytes of a regular file inside `root`, at most `cap` of them. Raises OSError for anything else, and never blocks.

    A repo's own files are read after Maven, inside the repo's lock: a FIFO named X.class would block the read for ever, a
    link to /dev/zero would never end, and a link out of the repo would read what is none of the repo's. So: the path's
    directory must be inside the root, the last component is looked at with lstat (a link is refused, unless `follow`, which
    resolves it and requires the target to be inside the root too), the file is opened O_NONBLOCK | O_NOFOLLOW, fstat on the
    descriptor must still say a regular file of the same identity, and the read stops one byte past the cap.
    """
    path = os.path.abspath(os.fspath(path))
    top = os.path.realpath(os.fspath(root))
    if follow:
        target = os.path.realpath(path)
    else:
        target = os.path.join(os.path.realpath(os.path.dirname(path)), os.path.basename(path))
    if target != top and not target.startswith(top.rstrip(os.sep) + os.sep):
        raise OSError(errno.EXDEV, "not inside the repository", path)
    before = os.lstat(target)
    if not stat.S_ISREG(before.st_mode):
        raise OSError(errno.EINVAL, "not a regular file", path)
    fd = os.open(target, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0))
    try:
        after = os.fstat(fd)
        if not stat.S_ISREG(after.st_mode) or (after.st_dev, after.st_ino) != (before.st_dev, before.st_ino):
            raise OSError(errno.EINVAL, "not the regular file it was", path)
        if after.st_size > cap:
            raise OSError(errno.EFBIG, "over the size cap", path)
        chunks: list[bytes] = []
        got = 0
        while True:
            chunk = os.read(fd, min(65536, cap + 1 - got))
            if not chunk:
                return b"".join(chunks)
            chunks.append(chunk)
            got += len(chunk)
            if got > cap:
                raise OSError(errno.EFBIG, "over the size cap", path)
    finally:
        os.close(fd)


def read_java_file(path: Path, root: Path | None = None) -> JavaFile | None:
    """The source file read, or None: not a regular file inside `root` (the file's own directory when not given), too large, unreadable."""
    try:
        data = read_regular(path, root if root is not None else path.parent, MAX_SOURCE_BYTES, follow=True)
        return read_java(data.decode("utf-8", errors="replace"))
    except OSError:
        return None


# ---- reading compiled classes: what javac resolved --------------------------------------------------
#
# Which classes hold tests is a question the compiler has already answered where the source only hints at it: a class
# that inherits its tests, a method carrying an annotation that is itself annotated @Test, a test source root the pom
# sets in a parent or in a plugin. After a run, target/test-classes holds every class Maven compiled from whatever test
# roots the build has. ripple reads the class files (constant pool, superclass, interfaces, the runtime annotations of the
# class and of its methods) and decides test-ness from them; where it cannot decide, it says so.
#
# THE KNOWN SET. A class is one ripple KNOWS holds tests when, in the part of its supertype closure ripple could read (its
# superclasses and superinterfaces, transitively, as far as they are classes of the repo), it finds one of these:
#   - on a method: JUnit 4 @Test and @Theory; JUnit Jupiter @Test, @RepeatedTest, @TestFactory, @TestTemplate and
#     @ParameterizedTest; TestNG @Test; any annotation meta-annotated with the JUnit Platform's @Testable (jqwik's @Property,
#     a project's own @Scenario), where the annotation's class file is in the repo (a composed annotation, any depth);
#   - on the class: JUnit 4 @RunWith (a Suite, Enclosed, Cucumber or any runner), TestNG's class-level @Test, or any of the
#     annotations above composed;
#   - JUnit 3: a superclass junit.framework.TestCase.
# A class that is known to hold tests and has no result is a PROBLEM. A class that is not known, and that ripple cannot decide
# (an annotation it does not know on a void method, a supertype outside the repo or one it cannot choose between) is a NOTE
# that names it and why, unless the request asked for it by name.

TESTABLE = "org/junit/platform/commons/annotation/Testable"
MARKERS = frozenset(
    {
        "org/junit/Test",
        "org/junit/jupiter/api/Test",
        "org/junit/jupiter/api/RepeatedTest",
        "org/junit/jupiter/api/TestFactory",
        "org/junit/jupiter/api/TestTemplate",
        "org/junit/jupiter/params/ParameterizedTest",
        "org/junit/experimental/theories/Theory",
        "org/testng/annotations/Test",
        TESTABLE,
    }
)
CLASS_MARKERS = frozenset({"org/junit/runner/RunWith"})
JUNIT3_BASE = "junit/framework/TestCase"
JDK_PREFIXES = ("java/", "javax/", "jdk/")
# Annotations on a method that say it is not a test (and the packages whose annotations never make one).
BENIGN_PREFIXES = (
    "java/", "javax/", "jdk/", "kotlin/", "org/junit/", "org/mockito/", "org/jetbrains/", "org/checkerframework/",
    "org/assertj/", "org/hamcrest/", "org/jspecify/", "org/apiguardian/", "lombok/", "org/opentest4j/",
)  # fmt: skip
DEFAULT_PATTERN = re.compile(r"^(Test.*|.*Test|.*Tests|.*TestCase)$")
# failsafe's default includes: integration tests run by `verify`, which `mvn test` never reaches
INTEGRATION_PATTERN = re.compile(r"^(IT.*|.*IT|.*ITCase)$")
ACC_ABSTRACT, ACC_INTERFACE, ACC_ANNOTATION = 0x0400, 0x0200, 0x2000


def default_pattern(simple_name: str) -> bool:
    """Whether surefire's default includes (Test*, *Test, *Tests, *TestCase) name this class (the simple name, no package)."""
    return DEFAULT_PATTERN.match(simple_name) is not None


def is_integration(simple_name: str) -> bool:
    """Whether failsafe's default includes name this class (IT*, *IT, *ITCase) and surefire's do not: `mvn test` never runs it."""
    return INTEGRATION_PATTERN.match(simple_name) is not None and not default_pattern(simple_name)


@dataclass(frozen=True)
class MethodInfo:
    name: str
    annotations: frozenset[str]  # runtime-visible annotation types, as internal names (org/junit/jupiter/api/Test)
    void: bool


@dataclass(frozen=True)
class ClassInfo:
    name: str  # internal name, a/b/Outer$Inner
    superclass: str | None
    abstract: bool  # abstract, or an interface
    annotation_type: bool
    annotations: frozenset[str]  # runtime-visible annotations of the class
    methods: tuple[MethodInfo, ...]
    interfaces: tuple[str, ...]
    source_file: str | None = None  # the SourceFile attribute: Foo.java


@dataclass(frozen=True)
class Compiled:
    relative: str  # repo-relative path of the .class file
    kind: str  # "classes" or "test-classes"
    info: ClassInfo | None  # None: not a readable class file


@dataclass(frozen=True)
class Decision:
    kind: str  # test, scanned, no, unknown, skip, integration
    why: str = ""


class _Reader:
    def __init__(self, data: bytes):
        self.data, self.pos = data, 0

    def take(self, count: int) -> bytes:
        if count < 0 or self.pos + count > len(self.data):
            raise ValueError("cut off")
        chunk = self.data[self.pos : self.pos + count]
        self.pos += count
        return chunk

    def skip(self, count: int) -> None:
        self.take(count)

    def u1(self) -> int:
        return self.take(1)[0]

    def u2(self) -> int:
        return int.from_bytes(self.take(2), "big")

    def u4(self) -> int:
        return int.from_bytes(self.take(4), "big")


def _element_value(r: _Reader, depth: int = 0) -> None:
    if depth > 16:
        raise ValueError("too deep")
    tag = chr(r.u1())
    if tag in "BCDFIJSZsc":
        r.skip(2)
    elif tag == "e":
        r.skip(4)
    elif tag == "@":
        _annotation(r, depth + 1)
    elif tag == "[":
        for _ in range(r.u2()):
            _element_value(r, depth + 1)
    else:
        raise ValueError("unknown element value")


def _annotation(r: _Reader, depth: int = 0) -> int:
    type_index = r.u2()
    for _ in range(r.u2()):
        r.skip(2)
        _element_value(r, depth)
    return type_index


def _annotations(body: bytes, pool: list) -> frozenset[str]:
    r = _Reader(body)
    found = set()
    for _ in range(r.u2()):
        descriptor = pool[_annotation(r)]
        if isinstance(descriptor, str) and descriptor.startswith("L") and descriptor.endswith(";"):
            found.add(descriptor[1:-1])
    return frozenset(found)


def _members(r: _Reader, pool: list) -> list[tuple[str, str, frozenset[str]]]:
    out = []
    for _ in range(r.u2()):
        r.skip(2)
        name, descriptor = pool[r.u2()], pool[r.u2()]
        annotations: frozenset[str] = frozenset()
        for _ in range(r.u2()):
            attribute, length = pool[r.u2()], r.u4()
            body = r.take(length)
            if attribute == "RuntimeVisibleAnnotations":
                annotations = _annotations(body, pool)
        out.append((str(name), str(descriptor), annotations))
    return out


def read_class(data: bytes) -> ClassInfo | None:
    """The shape of a compiled class (see the section comment), or None when `data` is not a class file javac wrote."""
    try:
        return _read_class(data)
    except Exception:  # noqa: BLE001  the bytes are a repo's own and may be anything: a class that cannot be read is that class only
        return None


def _read_class(data: bytes) -> ClassInfo | None:
    r = _Reader(data)
    if r.u4() != 0xCAFEBABE:
        return None
    r.skip(4)
    count = r.u2()
    if count < 1 or count - 1 > len(data) - r.pos:  # every slot of the constant pool takes at least a byte of the file
        return None
    pool: list = [None] * count
    i = 1
    while i < count:
        tag = r.u1()
        if tag == 1:
            pool[i] = r.take(r.u2()).decode("utf-8", "replace")
        elif tag in (3, 4, 9, 10, 11, 12, 17, 18):
            r.skip(4)
        elif tag in (5, 6):
            r.skip(8)
            i += 1
        elif tag == 7:
            pool[i] = ("class", r.u2())
        elif tag in (8, 16, 19, 20):
            r.skip(2)
        elif tag == 15:
            r.skip(3)
        else:
            return None
        i += 1

    def class_name(index: int) -> str | None:
        entry = pool[index] if index else None
        named = pool[entry[1]] if isinstance(entry, tuple) else None
        return named if isinstance(named, str) else None  # a Class entry names a Utf8 entry, never another Class entry

    flags = r.u2()
    name, superclass = class_name(r.u2()), class_name(r.u2())
    interfaces = tuple(n for n in (class_name(r.u2()) for _ in range(r.u2())) if n)
    _members(r, pool)  # fields
    methods = tuple(MethodInfo(n, a, d.endswith(")V")) for n, d, a in _members(r, pool))
    annotations: frozenset[str] = frozenset()
    source = None
    for _ in range(r.u2()):
        attribute, length = pool[r.u2()], r.u4()
        body = r.take(length)
        if attribute == "RuntimeVisibleAnnotations":
            annotations = _annotations(body, pool)
        elif attribute == "SourceFile" and length == 2:
            found = pool[int.from_bytes(body, "big")]
            source = found if isinstance(found, str) else None
    if not name:
        return None
    return ClassInfo(
        name, superclass, bool(flags & (ACC_ABSTRACT | ACC_INTERFACE)), bool(flags & ACC_ANNOTATION), annotations, methods, interfaces, source
    )


def compiled_classes(root: Path) -> list[Compiled]:
    """Every class file under a `classes` or `test-classes` directory in the repo (Maven's default output directories), read."""
    found: list[Compiled] = []
    for current, dirs, _files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in (".git", "node_modules"))
        for kind in ("classes", "test-classes"):
            if kind in dirs and os.path.basename(current) == "target":
                dirs.remove(kind)
                base = Path(current) / kind
                if base.is_symlink():  # walk never follows a link below the top, and this is the top of a walk: look first
                    continue
                for inner, _dirs, files in os.walk(base):
                    for name in sorted(files):
                        if not name.endswith(".class"):
                            continue
                        path = Path(inner) / name
                        try:
                            info = read_class(read_regular(path, root, MAX_CLASS_BYTES))
                        except OSError:
                            info = None
                        found.append(Compiled(path.relative_to(root).as_posix(), kind, info))
    return found


class _Ambiguous:
    """The answer of a lookup that found a name in several places ripple cannot rank: not one class, and not 'no such class'."""

    def __repr__(self) -> str:
        return "AMBIGUOUS"


AMBIGUOUS = _Ambiguous()


def _lookup(index, name: str):
    """The class `name` as the index resolves it: a ClassInfo, None (not a class of the repo), or AMBIGUOUS. A plain dict works too."""
    return index.resolve(name) if hasattr(index, "resolve") else index.get(name)


def _marked(annotation: str, index, depth: int = 0) -> bool:
    """Whether the annotation is a test marker of the known set, or a repo annotation type composed of one (any depth)."""
    if annotation in MARKERS:
        return True
    info = _lookup(index, annotation)
    if info is None or info is AMBIGUOUS or not info.annotation_type or depth > 6:
        return False
    return any(_marked(a, index, depth + 1) for a in info.annotations if a != annotation)


def decide(info: ClassInfo, index, custom_patterns: bool) -> Decision:
    """Whether a compiled class holds tests, from what javac resolved. `index` resolves a class name the way the build would
    (see ClassIndex.scope; a dict of internal name to ClassInfo works too).

    The closure is every superclass and every superinterface, transitively, with cycles cut. Anything in it that is known to
    hold tests (the known set, see MARKERS) decides at once. Otherwise: where the closure leaves the repo's classes (a library,
    a class with no source here, a name defined in several places that ripple cannot rank) or a void method carries an
    annotation ripple does not know, the class is undecided.

    kind: "test" (known to hold tests), "scanned" (no test, but surefire's default patterns name it and the pom has not changed
    them, so surefire and JUnit looked at it and found none), "no" (the closure is all in the repo or the JDK and has no
    unknown annotation: it cannot hold one), "unknown" (undecided, `why` says what is missing), "skip" (abstract, an annotation
    type, a nested class), "integration" (failsafe's names).
    """
    simple = info.name.rpartition("/")[2]
    if info.abstract or info.annotation_type or "$" in simple:
        return Decision("skip")
    if is_integration(simple):
        return Decision("integration")
    reasons: list[str] = []
    seen = {info.name}
    work = [info]
    test = False
    while work and not test:
        current = work.pop()
        if current.annotations & CLASS_MARKERS or any(_marked(a, index) for a in current.annotations):
            test = True
            break
        for method in current.methods:
            for annotation in method.annotations:
                if _marked(annotation, index):
                    test = True
                elif method.void and not annotation.startswith(BENIGN_PREFIXES):
                    found = _lookup(index, annotation)
                    if found is None or found is AMBIGUOUS:
                        reasons.append(f"{current.name.rpartition('/')[2]}.{method.name} carries {annotation}")
        for parent in (current.superclass, *current.interfaces):
            if parent is None or parent == "java/lang/Object" or parent in seen:
                continue
            seen.add(parent)
            if parent == JUNIT3_BASE:
                test = True
                break
            found = _lookup(index, parent)
            if found is AMBIGUOUS:
                reasons.append(f"{parent} is defined in more than one module it can see")
            elif found is not None:
                work.append(found)
            elif not parent.startswith(JDK_PREFIXES):
                reasons.append(f"it inherits {parent}, which is outside the repo")
    if test:
        return Decision("test")
    if default_pattern(simple) and not custom_patterns:
        return Decision("scanned")
    if reasons:
        return Decision("unknown", "; ".join(dict.fromkeys(reasons))[:200])
    return Decision("no")


# ---- the build: poms, modules ------------------------------------------------------------------


@dataclass
class Module:
    dir: str  # repo-relative, "" for the root
    artifact: str
    test_roots: tuple[str, ...]  # repo-relative directories of its test sources, as far as the poms say (never empty)
    hazards: tuple[str, ...] = ()
    roots_uncertain: bool = False  # a test source root is held in a property or can't be read: there may be roots not in test_roots
    custom_patterns: bool = False  # a pom of its chain sets includes or excludes: surefire's default name patterns may not hold
    main_roots: tuple[str, ...] = ()  # the same for its main sources (src/main/java, or what the poms set)
    main_uncertain: bool = False  # a main source root is held in a property or can't be read
    depends: tuple[tuple[str, bool], ...] = ()  # (directory of a module of this build it depends on, whether on its test-jar)


@dataclass
class Project:
    modules: list[Module]

    def module_of(self, rel: str) -> Module | None:
        """The module whose directory is the deepest one that holds the repo-relative path."""
        holders = [m for m in self.modules if _within(rel, m.dir)]
        return max(holders, key=lambda m: len(m.dir)) if holders else None

    @property
    def single(self) -> bool:
        return len(self.modules) == 1


def _within(path: str, parent: str) -> bool:
    return parent == "" or path == parent or path.startswith(parent + "/")


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child(element, name: str):
    return next((c for c in element if _local(c.tag) == name), None)


def _text(element, name: str) -> str:
    child = _child(element, name) if element is not None else None
    return (child.text or "").strip() if child is not None else ""


def _parse_pom(path: Path):
    try:
        if path.stat().st_size > MAX_REPORT_BYTES:
            raise ValueError(f"{path.name} is too large to read")
        return ET.parse(path).getroot()
    except (ET.ParseError, OSError) as error:
        raise ValueError(f"{path} is not a readable pom: {error}") from error


def _pom_chain(repo: Path, directory: str, cache: dict) -> list[tuple[Path, object]]:
    """The module's pom and the poms its <parent> elements lead to by relativePath (default ../pom.xml), nearest first.

    Maven inherits the build section down this chain, so a testSourceDirectory or a plugin set in a parent applies to the
    module. Only poms inside the repo are followed (a parent that is found in a repository is not read).
    """
    chain: list[tuple[Path, object]] = []
    path = (repo / directory / "pom.xml") if directory else repo / "pom.xml"
    seen: set[Path] = set()
    for _ in range(12):
        resolved = path.resolve()
        if resolved in seen or not resolved.is_file() or not resolved.is_relative_to(repo):
            break
        seen.add(resolved)
        if resolved not in cache:
            cache[resolved] = _parse_pom(resolved)
        pom = cache[resolved]
        chain.append((resolved, pom))
        parent = _child(pom, "parent")
        if parent is None:
            break
        relative = _child(parent, "relativePath")
        text = "../pom.xml" if relative is None else (relative.text or "").strip()
        if not text:
            break  # an empty relativePath: the parent comes from a repository
        target = resolved.parent / text
        path = target / "pom.xml" if target.is_dir() else target
    return chain


def _literal_root(directory: str, text: str) -> str | None:
    """A source root written in a pom as a repo-relative path of the module, or None when it is not one ripple can read."""
    text = re.sub(r"^\$\{(?:project\.)?basedir\}/?", "", text.strip())
    if not text or "${" in text or text.startswith("/") or ".." in text.split("/"):
        return None
    return f"{directory}/{text}".strip("/") if directory else text.strip("/")


def _test_roots(directory: str, chain: list[tuple[Path, object]]) -> tuple[tuple[str, ...], bool]:
    """The test source roots of a module and whether there may be others ripple could not read.

    The first testSourceDirectory along the parent chain (a relative one is the module's own: Maven aligns it to each
    module's directory), else src/test/java; plus every source a build-helper `add-test-source` execution adds.
    """
    return _roots(directory, chain, "testSourceDirectory", "src/test/java", "add-test-source")


def _main_roots(directory: str, chain: list[tuple[Path, object]]) -> tuple[tuple[str, ...], bool]:
    """The same for the main sources: sourceDirectory, else src/main/java, plus build-helper `add-source`."""
    return _roots(directory, chain, "sourceDirectory", "src/main/java", "add-source")


def _roots(directory: str, chain: list[tuple[Path, object]], element: str, subdir: str, goal: str) -> tuple[tuple[str, ...], bool]:
    uncertain = False
    roots: list[str] = []
    for _path, pom in chain:
        build = _child(pom, "build")
        configured = _text(build, element) if build is not None else ""
        if configured:
            literal = _literal_root(directory, configured)
            if literal is None:
                uncertain = True
            else:
                roots.append(literal)
            break
    default = f"{directory}/{subdir}".strip("/") if directory else subdir
    roots = roots or [default]
    for _path, pom in chain:
        for plugin in pom.iter():
            if _local(plugin.tag) != "plugin" or _text(plugin, "artifactId") != "build-helper-maven-plugin":
                continue
            for execution in plugin.iter():
                if _local(execution.tag) not in ("execution", "plugin"):
                    continue
                goals = _child(execution, "goals")
                if goals is None or not any((g.text or "").strip() == goal for g in goals):
                    continue
                configuration = _child(execution, "configuration")
                sources = _child(configuration, "sources") if configuration is not None else None
                listed = [(s.text or "").strip() for s in (sources if sources is not None else [])]
                if not listed:
                    uncertain = True
                for item in listed:
                    literal = _literal_root(directory, item)
                    if literal is None:
                        uncertain = True
                    else:
                        roots.append(literal)
    return tuple(dict.fromkeys(roots)), uncertain


def find_project(repo: Path) -> Project:
    """The reactor of the repo's root pom.xml: the root and every module it lists, recursively. Raises ValueError if there is none.

    ripple reads from the poms only what it needs before a run: the module list and directories (to build -pl), the test
    source roots (to find the file a test id names, and which directories are tests) and settings that may narrow a run.
    Which module depends on which, and so what must be built beside it, it does not read: it always asks Maven for the
    modules a module needs (-am).
    """
    root_pom = repo / "pom.xml"
    if not root_pom.is_file():
        raise ValueError(
            "no pom.xml at the repository root: ripple runs Maven from the root and cannot tell which module "
            "to run from (pass --test-command, for example `mvn -B -f path/to/pom.xml test`)"
        )
    modules: list[Module] = []
    seen: set[str] = set()
    cache: dict = {}

    def visit(directory: str, depth: int) -> None:
        if directory in seen or depth > 12:
            return
        seen.add(directory)
        base = repo / directory if directory else repo
        pom = _parse_pom(base / "pom.xml")
        cache[(base / "pom.xml").resolve()] = pom
        chain = _pom_chain(repo, directory, cache)
        roots, uncertain = _test_roots(directory, chain)
        main_roots, main_uncertain = _main_roots(directory, chain)
        hazards: list[str] = []
        custom = False
        for path, _element in chain:
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                text = ""
            where = path.relative_to(repo).as_posix()
            hazards.extend(f"{where} configures <{found}>" for found in POM_HAZARD.findall(text))
            custom = custom or re.search(r"<(includes|excludes)>", text) is not None
        modules.append(
            Module(directory, _text(pom, "artifactId"), roots, tuple(dict.fromkeys(hazards)), uncertain, custom, main_roots, main_uncertain)
        )
        listed = _child(pom, "modules")
        for item in (listed if listed is not None else []):
            name = (item.text or "").strip()
            if not name:
                continue
            if name.endswith(".xml"):
                name = os.path.dirname(name)
            target = (base / name).resolve()
            try:
                rel = target.relative_to(repo).as_posix()
            except ValueError as error:
                raise ValueError(f"module {name!r} of {directory or 'the root'} pom.xml is outside the repository") from error
            rel = "" if rel == "." else rel
            if not (target / "pom.xml").is_file():
                raise ValueError(f"module {name!r} of {directory or 'the root'} pom.xml has no pom.xml")
            visit(rel, depth + 1)

    visit("", 0)
    _read_dependencies(repo, modules, cache)
    return Project(sorted(modules, key=lambda m: m.dir))


class Scope:
    """The classes the tests of one module are compiled and run against, in the order the build puts them on the classpath.

    The module's own test classes and main classes come first (a name found there is that class), then the modules of this
    build it depends on. A name found in two of those is one ripple cannot rank (the order is the pom's, and a test-jar and a
    jar can differ): AMBIGUOUS, never silently one of them.
    """

    def __init__(self, own: list[dict[str, ClassInfo]], others: list[dict[str, ClassInfo]]):
        self.own, self.others = own, others

    def resolve(self, name: str):
        for classes in self.own:
            if name in classes:
                return classes[name]
        hits = [classes[name] for classes in self.others if name in classes]
        if not hits:
            return None
        return hits[0] if len(hits) == 1 else AMBIGUOUS


class ClassIndex:
    """Every compiled class of the repo, by the module it was built in, and what makes one of them evidence of THIS run.

    A name is never looked up across the whole repo: test trees of different modules are different classpaths, and a
    module this run did not build leaves stale classes behind. `scope(module)` is the classpath of one module's tests.
    A compiled class counts only if the module's CURRENT source roots hold a source for it: the same package path and the
    outer class's name (`Outer$Inner` and `Outer$1` follow `Outer`), or the file the class file says it came from
    (SourceFile, for a second top-level class in a file). A class with none is left over from an earlier build, or generated
    by something that is not a hand-written source: it is evidence of nothing. Where the roots cannot be read, or a class
    was not compiled from a .java file (Kotlin, Groovy, Scala), every class counts: ripple cannot prove it stale.
    """

    def __init__(self, repo: Path, project: Project, compiled: list[Compiled]):
        self.repo, self.project = repo, project
        self._by_dir = {m.dir: m for m in project.modules}
        self._items: dict[tuple[str, str], list[Compiled]] = {}
        for item in compiled:
            owner = project.module_of(item.relative)
            if owner is not None:
                self._items.setdefault((owner.dir, item.kind), []).append(item)
        self._sources: dict[tuple[str, str], set[str]] = {}
        self._classes: dict[tuple[str, str], dict[str, ClassInfo]] = {}
        self._scopes: dict[str, Scope] = {}

    def items(self, module: Module, kind: str) -> list[Compiled]:
        """Every class file of the module in `kind` ("classes" or "test-classes"), readable or not, current or not."""
        return self._items.get((module.dir, kind), [])

    def sources(self, module: Module, kind: str) -> set[str]:
        """The .java files under the module's source roots for `kind`, as paths relative to the root (a/b/C.java)."""
        key = (module.dir, kind)
        if key not in self._sources:
            found: set[str] = set()
            for root in module.test_roots if kind == "test-classes" else module.main_roots:
                base = self.repo / root
                for current, _dirs, files in os.walk(base):
                    found.update((Path(current) / f).relative_to(base).as_posix() for f in files if f.endswith(".java"))
            self._sources[key] = found
        return self._sources[key]

    def sourced(self, module: Module, item: Compiled) -> bool:
        info = item.info
        if info is None:
            return False
        if module.roots_uncertain if item.kind == "test-classes" else module.main_uncertain:
            return True
        if info.source_file is not None and not info.source_file.endswith(".java"):
            return True
        package, _, simple = info.name.rpartition("/")
        files = {simple.split("$")[0] + ".java"} | ({info.source_file} if info.source_file else set())
        held = self.sources(module, item.kind)
        return any(f"{package}/{name}".lstrip("/") in held for name in files)

    def classes(self, directory: str, kind: str) -> dict[str, ClassInfo]:
        """The readable, current classes of one module in `kind`, by internal name."""
        key = (directory, kind)
        if key not in self._classes:
            module = self._by_dir.get(directory)
            self._classes[key] = (
                {c.info.name: c.info for c in self._items.get(key, []) if module is not None and self.sourced(module, c)}
            )
        return self._classes[key]

    def scope(self, module: Module) -> Scope:
        if module.dir not in self._scopes:
            own =[self.classes(module.dir, "test-classes"), self.classes(module.dir, "classes")]
            test_jars = {directory for directory, test_jar in module.depends if test_jar}
            order: list[str] = []
            seen = {module.dir}
            queue = [directory for directory, _ in module.depends]
            while queue:  # the modules it depends on, and theirs, once each: a cycle in the poms ends here
                directory = queue.pop(0)
                if directory in seen:
                    continue
                seen.add(directory)
                order.append(directory)
                dependency = self._by_dir.get(directory)
                if dependency is not None:
                    queue.extend(d for d, _ in dependency.depends)
            others: list[dict[str, ClassInfo]] = []
            for directory in order:
                others.append(self.classes(directory, "classes"))
                if directory in test_jars:  # its tests are on the classpath only through a test-jar dependency
                    others.append(self.classes(directory, "test-classes"))
            self._scopes[module.dir] = Scope(own, others)
        return self._scopes[module.dir]


def _dependencies(element):
    """Every <dependency> a pom declares for its own build: in <dependencies> and in profiles, not in dependencyManagement or a plugin."""
    for child in element:
        name = _local(child.tag)
        if name in ("dependencyManagement", "plugins", "pluginManagement", "reporting"):
            continue
        if name == "dependency":
            yield child
        else:
            yield from _dependencies(child)


def _read_dependencies(repo: Path, modules: list[Module], cache: dict) -> None:
    """Which modules of this build each module depends on, read from its pom and the parents it inherits from (profiles too).

    This is only used to say where a class a test inherits is looked up (see ClassIndex), never to decide what to build:
    a dependency ripple cannot read (a property in the artifact id) leaves a name outside the repo, which is undecided.
    """
    by_artifact: dict[str, list[Module]] = {}
    for module in modules:
        by_artifact.setdefault(module.artifact, []).append(module)
    for module in modules:
        found: dict[tuple[str, bool], None] = {}
        for _path, pom in _pom_chain(repo, module.dir, cache):
            for dependency in _dependencies(pom):
                kind, classifier, artifact = _text(dependency, "type"), _text(dependency, "classifier"), _text(dependency, "artifactId")
                for target in by_artifact.get(artifact, []) if artifact else []:
                    if target is not module:
                        found[(target.dir, kind == "test-jar" or classifier == "tests")] = None
        module.depends = tuple(sorted(found))


def choose_tool(repo: Path) -> list[str]:
    """The command that is Maven: the repo's own `mvnw` if it has one, else `mvn` on PATH. Raises ValueError for neither.

    The wrapper is preferred because it is the version the repo pins (the Java form of preferring a repo's own
    venv): a Maven that is too old or too new for the build fails it for no fault of its tests. It may download
    a distribution on first use, and that is the repo's own build. It is only ever run from inside the repo: a
    `mvnw` that is a link to a file outside it is refused, never run.
    """
    wrapper = repo / "mvnw"
    if os.path.lexists(wrapper):
        real = wrapper.resolve()
        if not real.is_file() or not real.is_relative_to(repo):
            raise ValueError(f"{wrapper} is not a file inside the repository (it is {real}): ripple will not run it (pass --test-command)")
        if os.access(real, os.X_OK):
            return [str(real)]
        sh = shutil.which("sh")
        if sh:
            return [sh, str(real)]
    found = shutil.which("mvn")
    if found is None:
        raise ValueError("neither `mvnw` in the repository nor `mvn` on PATH: ripple runs Java tests with Maven (or pass --test-command)")
    return [found]


GRADLE_FILES = ("build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts")


def gradle_files(repo: Path) -> list[str]:
    """The Gradle build files at the repository root, by name."""
    return [n for n in GRADLE_FILES if (repo / n).exists()]


def check_build_files(repo: Path) -> None:
    """Refuse a repo ripple cannot tell how to run: both Maven and Gradle at the root."""
    gradle = gradle_files(repo)
    if (repo / "pom.xml").exists() and gradle:
        raise ValueError(
            f"pom.xml and {gradle[0]} are both at the repository root, so ripple cannot tell which build is the repo's: "
            "pass the command that runs the tests (`--test-command`, for example `mvn -B test` or `./gradlew test`)"
        )


def build_tool(repo: Path) -> str:
    """"gradle" for a root with Gradle files and no pom.xml, else "maven". A root with both is refused (check_build_files).

    The one place that decides, so `normalize` and `run` can never disagree about it: Maven's own rules (a pom.xml at
    the root, or the refusal that says there is none) apply to everything that is not a Gradle-only root.
    """
    check_build_files(repo)
    return "gradle" if gradle_files(repo) else "maven"


def hazards(repo: Path, environ: dict[str, str] | None = None) -> list[str]:
    """What the environment or .mvn/ of the repo sets that can narrow or skip the tests, named."""
    environ = os.environ if environ is None else environ
    found = []
    sources = [(f"${name}", environ.get(name, "")) for name in ("MAVEN_OPTS", "MAVEN_ARGS", "JAVA_TOOL_OPTIONS", "JDK_JAVA_OPTIONS")]
    for name in ("maven.config", "jvm.config"):
        try:
            sources.append((f".mvn/{name}", (repo / ".mvn" / name).read_text(encoding="utf-8", errors="replace")))
        except OSError:
            continue
    for where, text in sources:
        for match in HAZARD.finditer(text):
            found.append(f"{where} sets -D{match.group(1)}")
    return found


# ---- the request: what the caller asked to run -------------------------------------------------


@dataclass(frozen=True)
class Want:
    """One thing asked for: a module's whole suite (cls None), a test class, or a method of one."""

    given: str
    module: str
    cls: str | None = None  # the binary name (com.acme.Outer$Inner)
    method: str | None = None
    params: str | None = None  # the parameter types the id gave, for telling overloads apart
    overloads: int = 1  # how many test methods of that name the class declares


METHOD_ID = re.compile(r"^(?P<q>[^()#]+?)(?:\((?P<p>[^()]*)\))?(?:#\d+)?$")
_PARAM_CHARS = re.compile(r"^[\w\s.,<>\[\]?&$]*$")


def _control(text: str) -> bool:
    return any(ord(c) < 0x20 or ord(c) == 0x7F for c in text)


def _repo_path(repo: Path, given: str) -> Path:
    """The path `given` names, resolved against the repo and proved to be inside it, before anything reads it."""
    if _control(given):
        raise ValueError(f"not a Java test path or id: {given!r} (control character)")
    path = (repo / given).resolve()
    if not path.is_relative_to(repo):
        raise ValueError(f"{given!r} is not a file or directory inside {repo}")
    return path


def test_files(repo: Path, project: Project) -> list[str]:
    """Every .java file under a module's test sources, repo-relative."""
    found = []
    for module in project.modules:
        for test_root in module.test_roots:
            for root, dirs, files in os.walk(repo / test_root):
                dirs[:] = sorted(d for d in dirs if not d.startswith("."))
                found.extend((Path(root) / f).relative_to(repo).as_posix() for f in sorted(files) if f.endswith(".java"))
    return sorted(set(found))


def normalize(repo: Path, tests: list[str]) -> list[str]:
    """The tests with each Java id as `impact` hands it out turned into the `file::Class.method(types)` form.

    `impact` lists a JUnit test method among the callers to update (Java tests are not collected) as
    `com.acme.shop.CartTest.addsItems()`, or `com.acme.shop.CartTest.Receipts.receiptHasTotal()` for a nested
    class, with the parameter types of the method. That id names no path, so it is looked up in the repo's own
    test sources (never under a path the caller wrote). Input that is not a string, and a path-shaped argument,
    is left for the checks that follow to refuse or accept. An id that is Java-shaped and does not resolve, or
    is malformed, or matches two files (the same class in two modules), is refused here with the reason.
    """
    if tests and build_tool(repo) == "gradle":
        from . import gradle_check

        return gradle_check.normalize(repo, tests)
    result = []
    project = None
    for given in tests:
        if not isinstance(given, str) or not given or given.startswith(("-", "@")) or "::" in given or _control(given):
            result.append(given)
            continue
        if "/" in given or "\\" in given or given.startswith("."):
            result.append(given)  # path-shaped: parse_wants proves it is inside the repo before reading it
            continue
        match = METHOD_ID.match(given)
        if match is None:
            raise ValueError(f"{given!r} is not a Java test id: expected a.b.Class.method(types), a class or a path")
        qualified, params = match["q"], match["p"]
        parts = qualified.split(".")
        if not all(IDENT_RE.match(p) for p in parts) or (params is not None and not _PARAM_CHARS.match(params)):
            raise ValueError(f"{given!r} is not a Java test id: expected a.b.Class.method(types), a class or a path")
        existing = _repo_path(repo, given)
        if existing.exists():  # a file named like an id (Foo.java at the root): it is the path
            result.append(given)
            continue
        if len(parts) < 2 and params is None:
            raise ValueError(f"{given!r} is not a Java test id or a path inside {repo}")
        if project is None:
            project = find_project(repo)
        result.append(_resolve_id(repo, project, given, parts, params))
    return result


def _resolve_id(repo: Path, project: Project, given: str, parts: list[str], params: str | None) -> str:
    found: dict[tuple[str, tuple[str, ...], str | None], None] = {}
    by_stem: dict[str, list[str]] = {}
    for rel in test_files(repo, project):
        by_stem.setdefault(Path(rel).stem, []).append(rel)
    for i in range(len(parts)):
        for rel in by_stem.get(parts[i], []):
            java = read_java_file(repo / rel, repo)
            if java is None or java.package != ".".join(parts[:i]):
                continue
            rest = parts[i:]
            splits = [(rest[:-1], rest[-1])] if params is not None else [(rest, None), (rest[:-1], rest[-1])]
            for chain, method in splits:
                if not chain:
                    continue
                cls = next((c for c in java.classes if c.chain == tuple(chain)), None)
                if cls is None:
                    continue
                if method is None or method in cls.tests or method in cls.mentions:
                    found[(rel, tuple(chain), method)] = None
                    break
    if not found:
        raise ValueError(
            f"{given!r}: no test class or method of that name in the repo's test sources "
            "(ids look like com.acme.shop.CartTest.addsItems(); or pass the path of the test file)"
        )
    files = sorted({rel for rel, _, _ in found})
    if len(files) > 1:
        raise ValueError(f"{given!r} matches {len(files)} files ({', '.join(files[:3])}): pass the path of the one you mean")
    rel, chain, method = next(iter(found))
    text = f"{rel}::{'.'.join(chain)}"
    if method is not None:
        text += f".{method}" + (f"({params})" if params is not None else "")
    return text


def parse_wants(repo: Path, project: Project, tests: list[str]) -> list[Want]:
    """The tests asked for, as module suites, classes and methods. Raises ValueError for one Maven cannot be asked for.

    The forms: a directory (a module or modules, or the test classes under a test source directory), a test
    .java file (the test classes it declares), and `file.java::Class[.Nested][.method[(types)]]`. The caller has
    already run checker.check_tests (no option, no `@file`, a path that exists inside the repo); every path is
    proved to be inside the repo here again, before it is read, because run() can be called without it.
    """
    wants: list[Want] = []
    for given in tests:
        if not isinstance(given, str) or not given or given.startswith(("-", "@")) or _control(given):
            raise ValueError(f"not a test path or id: {given!r}")
        path_part, sep, rest = given.partition("::")
        path = _repo_path(repo, path_part)
        rel = path.relative_to(repo).as_posix()
        rel = "" if rel == "." else rel
        if path.is_dir():
            if sep:
                raise ValueError(f"{given!r}: a directory has no test names; name a .java file")
            wants.extend(_dir_wants(repo, project, rel, given))
            continue
        if not path.is_file() or not path.name.endswith(".java"):
            raise ValueError(f"{given!r} is not a Java test file (.java) or a directory")
        module = project.module_of(rel)
        if module is None or not any(_within(rel, root) for root in module.test_roots):
            hint = " (the pom holds a test source root in a property ripple cannot read)" if module is not None and module.roots_uncertain else ""
            raise ValueError(
                f"{given!r} is not in the test sources of a module of the build "
                f"(under {', '.join(module.test_roots) if module else 'src/test/java'}){hint}"
            )
        java = read_java_file(path, repo)
        if java is None or not PACKAGE_RE.match(java.package or "x"):
            raise ValueError(f"{given!r} could not be read as Java source")
        if not sep:
            classes = [c for c in java.classes if (c.tests or c.nested_tests) and not c.abstract and len(c.chain) == 1]
            if not classes:
                raise ValueError(f"{given!r} declares no JUnit test (@Test, @ParameterizedTest, ...) in a class that can run")
            wants.extend(Want(given, module.dir, java.binary(c)) for c in classes)
            continue
        wants.append(_member_want(java, module, rest, given))
    return list(dict.fromkeys(wants))


def _member_want(java: JavaFile, module: Module, rest: str, given: str) -> Want:
    match = METHOD_ID.match(rest)
    if match is None or (match["p"] is not None and not _PARAM_CHARS.match(match["p"])):
        raise ValueError(f"{given!r}: not a test name (expected Class, Class.method or Class.method(types))")
    parts = match["q"].split(".")
    if not all(IDENT_RE.match(p) for p in parts):
        raise ValueError(f"{given!r}: not a test name (expected Class, Class.method or Class.method(types))")
    options = [(parts[:-1], parts[-1])] if match["p"] is not None else [(parts, None), (parts[:-1], parts[-1])]
    for chain, method in options:
        cls = next((c for c in java.classes if chain and c.chain == tuple(chain)), None)
        if cls is None:
            continue
        if cls.abstract:
            raise ValueError(f"{given!r}: {'.'.join(chain)} is abstract: name the class that extends it")
        if method is None:
            if not (cls.tests or cls.nested_tests):
                raise ValueError(f"{given!r}: {'.'.join(chain)} declares no JUnit test")
            return Want(given, module.dir, java.binary(cls))
        if method not in cls.tests:
            hint = "it is declared but is not a JUnit test method" if method in cls.mentions else "it is not declared"
            shown = ", ".join(dict.fromkeys(cls.tests))[:200] or "none"
            raise ValueError(f"{given!r}: {'.'.join(chain)}.{method} is not a test ({hint}); its tests: {shown}")
        return Want(given, module.dir, java.binary(cls), method, match["p"], cls.tests.count(method))
    names = ", ".join(sorted({".".join(c.chain) for c in java.classes})[:5])
    raise ValueError(f"{given!r}: no such class or method in the file (it declares: {names or 'no class'})")


def _dir_wants(repo: Path, project: Project, rel: str, given: str) -> list[Want]:
    """The wants for a directory: every module at or below it as a whole, or the test classes under a test source directory."""
    below = [m for m in project.modules if _within(m.dir, rel)]
    holder = project.module_of(rel)
    if rel == "":
        return [Want(given, m.dir) for m in project.modules] if not project.single else [Want(given, "")]
    if holder is not None and holder.dir == rel:
        return [Want(given, m.dir) for m in below]
    if below:  # the directory holds modules (and is no module): each one, as a whole, never the directory itself
        return [Want(given, m.dir) for m in below]
    if holder is None:
        raise ValueError(f"{given!r} is not part of the build: no module's pom.xml is at or above it")
    if any(_within(root, rel) for root in holder.test_roots):  # it holds a test source root of the module: the whole module
        return [Want(given, holder.dir)]
    if not any(_within(rel, root) for root in holder.test_roots):
        raise ValueError(f"{given!r} holds no tests: it is not a module, and not under {', '.join(holder.test_roots)}")
    wants = []
    for file in test_files(repo, project):
        if not _within(file, rel):
            continue
        java = read_java_file(repo / file, repo)
        if java is None or not PACKAGE_RE.match(java.package or "x"):
            continue
        wants.extend(Want(given, holder.dir, java.binary(c)) for c in java.classes if (c.tests or c.nested_tests) and not c.abstract and len(c.chain) == 1)
    if not wants:
        raise ValueError(f"{given!r} holds no JUnit test class")
    if len(wants) > MAX_CLASSES:
        raise ValueError(f"{given!r} holds {len(wants)} test classes: name a module directory or a narrower one")
    return wants


# ---- the plan: one Maven run per thing that cannot share one ------------------------------------


@dataclass
class Invocation:
    """One Maven run: the modules it is limited to (None: the whole reactor), the -Dtest value, and the wants it answers."""

    modules: tuple[str, ...] | None
    selectors: tuple[str, ...] = ()
    am: bool = False
    wants: tuple[Want, ...] = ()
    label: str = ""

    def argv(self, tool: list[str] | None = None) -> list[str]:
        out = list(tool or ["mvn"]) + ["-B", "--fail-at-end"]
        if self.modules is not None:
            out += ["-pl", ",".join(m or "." for m in self.modules)]
        if self.am:
            out.append("-am")
        out.append("test")
        if self.selectors:
            out.append("-Dtest=" + ",".join(self.selectors))
        if self.selectors and (self.am or (self.modules is not None and len(self.modules) > 1)):
            # the modules built before the selected one have none of its classes: that is not a failure
            out.append("-Dsurefire.failIfNoSpecifiedTests=false")
        return out

    def command(self, tool: list[str] | None = None) -> str:
        return shlex.join(self.argv(tool))


def plan(project: Project, wants: list[Want]) -> list[Invocation]:
    """The Maven runs that make exactly what was asked, and nothing broader. A run may cover several requests; none loses its own.

    With nothing asked: the whole reactor. A module asked for whole is `-pl` that module. A class asked for whole is
    `-Dtest=` its binary name, the classes of one module sharing one run (a class that is nested in another asked for
    whole adds nothing to the selector). A method is `-Dtest=Class#m1+m2` in a run of its own per class: several `#`
    filters in one `-Dtest` are a cross product in some surefire versions (every filter on every class), so a method never
    shares a run with another class.

    Merging requests merges the RUNS, never the REQUIREMENTS: a want that a broader run covers (a class inside a module
    asked for whole, a method of a class asked for whole, a nested class of one) is kept on that run, and the verdict
    holds it to its own obligation (it ran, it was not skipped, it passed). Whatever a module is run with in a build of
    several modules, it is run with `-am`: ripple does not read the poms to guess which modules it needs, Maven builds them.
    """
    if not wants:
        return [Invocation(None, label="the whole build")]
    whole =sorted({w.module for w in wants if w.cls is None})
    runs: list[Invocation] = []
    if whole:
        runs.append(_module_run(project, whole, tuple(w for w in wants if w.module in whole)))
    rest = [w for w in wants if w.module not in whole]

    def covers(selected: str, cls: str) -> bool:
        return cls == selected or cls.startswith(selected + "$")

    for module in sorted({w.module for w in rest if w.method is None}):
        asked = list(dict.fromkeys(w.cls for w in rest if w.module == module and w.method is None))
        selectors = tuple(c for c in asked if not any(c != o and c.startswith(o + "$") for o in asked))
        group = tuple(w for w in rest if w.module == module and any(covers(s, w.cls) for s in selectors))
        runs.append(_module_run(project, [module], group, selectors))
    held = {w for r in runs for w in r.wants}
    methods: dict[tuple[str, str], list[Want]] = {}
    for want in rest:
        if want not in held:
            methods.setdefault((want.module, want.cls), []).append(want)
    for (module, cls), group in sorted(methods.items()):
        names = "+".join(dict.fromkeys(w.method for w in group))
        runs.append(_module_run(project, [module], tuple(group), (f"{cls}#{names}",)))
    return runs


def _module_run(project: Project, dirs: list[str], wants: tuple[Want, ...], selectors: tuple[str, ...] = ()) -> Invocation:
    chosen = set(dirs)
    everything = chosen == {m.dir for m in project.modules}
    modules = None if project.single or everything else tuple(sorted(chosen))
    label = ", ".join(d or "the root" for d in sorted(chosen))
    return Invocation(modules, selectors, modules is not None, wants, label)


# ---- the reports: which ones are this run's ---------------------------------------------------------


Signature = tuple[int, int, int]


def snapshot(repo: Path) -> dict[str, Signature]:
    """Every surefire report file under the repo now, with the (mtime, size, inode) it has.

    Taken before a Maven run, so that afterwards a file that was already there and was not written is known
    for what it is: a report of an earlier run, which is never read as this run's result.
    """
    found = {}
    for root, dirs, files in os.walk(repo):
        dirs[:] = [d for d in dirs if d not in (".git", "node_modules")]
        if os.path.basename(root) != "surefire-reports":
            continue
        for name in files:
            if name.startswith("TEST-") and name.endswith(".xml"):
                path = Path(root) / name
                try:
                    stat = path.stat()
                except OSError:
                    continue
                found[path.relative_to(repo).as_posix()] = (stat.st_mtime_ns, stat.st_size, stat.st_ino)
    return found


def fresh(repo: Path, before: dict[str, Signature], started_ns: int) -> tuple[list[str], int]:
    """The report files this run wrote, and how many older ones it left alone.

    A report is this run's when it is new or its (mtime, size, inode) changed since `before` AND it was
    written no earlier than the run's start (less the clock's granularity). Either alone is not enough: a
    file restored with an old time would pass the second, a stale one rewritten by nothing passes neither.
    """
    after = snapshot(repo)
    mine = [
        rel
        for rel, signature in sorted(after.items())
        if before.get(rel) != signature and signature[0] >= started_ns - CLOCK_SLACK_NS
    ]
    return mine, len(after) - len(mine)


@dataclass
class Case:
    module: str
    classname: str
    name: str
    status: str  # pass, fail, error, skip
    message: str = ""
    flaky: bool = False
    suite: str = ""  # the test class the report file is of: a runner (a Suite, AllTests) reports its tests under other names

    @property
    def base(self) -> str:
        return re.split(r"[(\[]", self.name, maxsplit=1)[0]

    @property
    def signature(self) -> str | None:
        """The parameter types in the name (`discounts(int)[1]` -> int), None when it has none (a method with no parameters)."""
        match = re.match(rf"{IDENT}\(([^)]*)\)", self.name)
        return match.group(1) if match else None


def read_report(repo: Path, project: Project, rel: str) -> tuple[list[Case], str | None]:
    """The testcases of one report file and a reason it could not be read (None: read, and its counts agree)."""
    path = repo / rel
    try:
        try:
            data = read_regular(path, repo, MAX_REPORT_BYTES, follow=True)
        except OSError as error:
            if error.errno == errno.EFBIG:
                return [], f"{rel} is too large to read"
            raise
        root = ET.fromstring(data)
    except (ET.ParseError, OSError) as error:
        return [], f"{rel} is not readable ({clip(str(error), 80)})"
    if _local(root.tag) not in ("testsuite", "testsuites"):
        return [], f"{rel} is not a surefire report"
    module = project.module_of(rel)
    cases = []
    for element in root.iter():
        if _local(element.tag) != "testcase":
            continue
        kinds = {_local(c.tag): c for c in element}
        status, message, flaky = "pass", "", False
        for kind, state in (("error", "error"), ("failure", "fail"), ("skipped", "skip")):
            if kind in kinds:
                status = state
                node = kinds[kind]
                body = (node.text or "").strip()
                message = node.get("message") or (body.splitlines()[0] if body else "")
                break
        else:
            if "rerunFailure" in kinds or "rerunError" in kinds:
                status, message = "fail", "failed on every rerun"
            elif "flakyFailure" in kinds or "flakyError" in kinds:
                flaky = True
        suite = (root.get("name") or "") if _local(root.tag) == "testsuite" else ""
        cases.append(
            Case(module.dir if module else "", element.get("classname") or "", element.get("name") or "", status, clip(str(message or ""), 200), flaky, suite)
        )
    declared = sum(int(root.get(k) or 0) for k in ("failures", "errors")) if _local(root.tag) == "testsuite" else 0
    seen = sum(1 for c in cases if c.status in ("fail", "error"))
    if declared > seen:
        return cases, f"{rel} says {declared} failed or errored but lists {seen}"
    return cases, None


# ---- the console -------------------------------------------------------------------------------


_REACTOR = re.compile(r"^\[INFO\] (.+?) \.{3,} (SUCCESS|FAILURE|SKIPPED)(?: \[.*)?$", re.M)
_GOAL = re.compile(r"^\[ERROR\] Failed to execute goal (.*)$", re.M)
_COMPILE = re.compile(r"^\[ERROR\] .*?([^/\s]+\.java):\[(\d+),(\d+)\] (.*)$", re.M)


@dataclass
class Console:
    built: bool  # a line `BUILD SUCCESS`
    skipped_modules: list[str]
    goal_failures: list[str]  # the goals that failed, except surefire's own "There are test failures"
    empty_test_goals: int  # surefire:test failures with no message of their own (a test failure, or a crash: the reports say which)
    tests_skipped: bool  # a line `Tests are skipped.` (skipTests or maven.test.skip)
    compile_errors: list[str]
    crashed: bool
    headline: list[str]


def read_console(text: str) -> Console:
    lines = text.splitlines()
    goals, empty = [], 0
    for goal in _GOAL.findall(text):
        short = clip(goal.strip(), 200)
        if "There are test failures" in goal:
            continue
        if "maven-surefire-plugin" in goal and re.search(r":test \([\w-]+\) on project [^:\s]+:\s*$", goal):
            empty += 1
            continue
        goals.append(short)
    compile_errors = list(dict.fromkeys(f"{f}:{ln} {clip(msg, 120)}" for f, ln, _c, msg in _COMPILE.findall(text)))
    headline = []
    for line in lines:
        if line.startswith("[ERROR] ") and line[8:].strip() and not line[8:].lstrip().startswith(("at ", "-> [Help", "[Help", "To see", "Re-run", "For more", "See ", "Command was")):
            headline.append(clip(line[8:].strip(), 160))
    return Console(
        built=any(line.strip() == "[INFO] BUILD SUCCESS" for line in lines),
        skipped_modules=[name for name, status in _REACTOR.findall(text) if status == "SKIPPED"],
        goal_failures=goals,
        empty_test_goals=empty,
        tests_skipped=any(line.strip() == "[INFO] Tests are skipped." for line in lines),
        compile_errors=compile_errors,
        crashed="The forked VM terminated" in text or "crashed tests" in text.lower(),
        headline=headline[:3],
    )


# ---- the verdict -------------------------------------------------------------------------------


@dataclass
class Run:
    """One Maven run and what came of it."""

    invocation: Invocation
    output: str
    exit_code: int | None  # None: it was killed for taking too long
    timed_out: bool = False
    started: bool = True  # False: the time limit had passed before it began
    cases: list[Case] = field(default_factory=list)  # the testcases of the reports this run wrote
    unreadable: list[str] = field(default_factory=list)
    stale: int = 0  # report files that were there and not written by this run


def _plural(count: int, noun: str) -> str:
    if count == 1:
        return f"{count} {noun}"
    return f"{count} {noun}es" if noun.endswith("ss") else f"{count} {noun}s"


def _names(names: list[str]) -> str:
    shown = ", ".join(names[:MAX_NAMES])
    return shown + (f" and {len(names) - MAX_NAMES} more" if len(names) > MAX_NAMES else "")


def _simple(types: str) -> str:
    """Parameter types as written, compared loosely: no generics, no packages, no spaces, varargs as arrays."""
    types = re.sub(r"\s+", "", types)
    while re.search(r"<[^<>]*>", types):
        types = re.sub(r"<[^<>]*>", "", types)
    return ",".join(re.sub(r"^.*\.", "", t).replace("...", "[]") for t in types.split(",") if t)


@dataclass
class Prediction:
    """What ripple expects a module's whole run to have results for, and how far to trust that."""

    classes: list[str] = field(default_factory=list)  # binary names that hold tests and must have a result
    uncertain: list[tuple[str, str]] = field(default_factory=list)  # (binary name, why): may hold tests, ripple cannot tell
    untrusted: str | None = None  # why the prediction as a whole cannot be relied on (None: it can)
    sources: int = 0  # test source files found under the roots the poms name
    integration: list[str] = field(default_factory=list)  # failsafe's classes (IT*, *IT, *ITCase): `mvn test` does not run them
    generated: list[str] = field(default_factory=list)  # compiled classes with no source in the module's source roots, left out
    unreadable: list[str] = field(default_factory=list)  # class files of the module's test-classes ripple could not read


def predict(repo: Path, project: Project, module: Module, index: ClassIndex) -> Prediction:
    """The test classes a module should have results for, from its test sources AND the classes Maven compiled from them.

    Source: every class with a JUnit test method of its own under the module's test roots. Bytecode: every concrete
    top-level class in the module's target/test-classes that `decide` calls a test (the known set, own or inherited through
    the whole supertype closure as the module's own classpath resolves it), which is where a test source root the poms only
    describe in a property, an inherited test, or a class outside surefire's name patterns shows up. A class `decide` cannot
    decide is `uncertain` (a note, see `_check_modules`). A compiled class with no source in the module's source roots is
    left over from an earlier build or generated, and goes to `generated`, never to the prediction or to `uncertain`.
    The prediction is `untrusted` when the module has test sources (or a root ripple cannot read) and no compiled test
    class that is current was found, because then only the source stands behind it.
    """
    result = Prediction()
    for test_root in module.test_roots:
        for root, dirs, files in os.walk(repo / test_root):
            dirs[:] = sorted(d for d in dirs if not d.startswith("."))
            for name in sorted(files):
                if name.endswith(".java"):
                    result.sources += 1
                    java = read_java_file(Path(root) / name, repo)
                    if java is not None:
                        result.classes.extend(java.binary(c) for c in java.classes if c.tests and not c.abstract)
    result.integration = [n for n in result.classes if is_integration(n.rpartition(".")[2].split("$")[0])]
    result.classes = [n for n in result.classes if n not in result.integration]
    scope = index.scope(module)
    items = index.items(module, "test-classes")
    current = 0
    for item in items:
        if item.info is None:
            result.unreadable.append(item.relative.rpartition("/")[2])
            current += 1  # a class file that is there and cannot be read is not proof that the module has none
            continue
        sourced = index.sourced(module, item)
        current += sourced
        decision = decide(item.info, scope, module.custom_patterns)
        binary = item.info.name.replace("/", ".")
        if decision.kind not in ("test", "integration", "unknown"):
            continue
        if not sourced:
            result.generated.append(binary)
        elif decision.kind == "test":
            result.classes.append(binary)
        elif decision.kind == "integration":
            result.integration.append(binary)
        else:
            result.uncertain.append((binary, decision.why))
    result.classes = list(dict.fromkeys(result.classes))
    result.integration = list(dict.fromkeys(result.integration))
    if not current and (result.sources or module.roots_uncertain):
        reason = "a test source root is held in a property ripple cannot read" if module.roots_uncertain else f"{result.sources} test source files"
        result.untrusted = f"{reason}, and no compiled test classes were found (target/test-classes), so only the source stands behind the prediction"
    return result


def _file_of(repo: Path, project: Project, module: str, classname: str) -> str | None:
    top = classname.split("$")[0]
    package, _, simple = top.rpartition(".")
    owner = next((m for m in project.modules if m.dir == module), None)
    for base in owner.test_roots if owner is not None else ("src/test/java",):
        rel = f"{base}/{package.replace('.', '/')}/{simple}.java".replace("//", "/")
        if (repo / rel).is_file():
            return rel
    return None


def judge(repo: Path, project: Project, runs: list[Run], command: str, duration: float, timeout: float, settings: list[str] | None = None) -> dict:
    """The verdict of the runs, from the reports they wrote. See the module docstring for what is never a pass."""
    problems: list[str] = []
    notes: list[str] = []
    failures: list[str] = []
    counts = {"passed": 0, "failed": 0, "errored": 0, "skipped": 0, "classes": 0, "flaky": 0}
    several = len(runs) > 1
    ran_any = False
    ctx: dict = {}  # the compiled classes of the repo, read once, when a module's prediction needs them
    for line in settings or []:
        problems.append(f"{line}: that can narrow or skip tests, so a pass would prove nothing")

    for number, run in enumerate(runs, 1):
        invocation = run.invocation
        label = f" (run {number}: {invocation.label})" if several else ""
        if not run.started:
            problems.append(f"not run: the {timeout:g}s limit was used up before `{invocation.command()}` began")
            continue
        explained = len(problems)
        if run.timed_out:
            problems.append(f"timed out after {timeout:g}s{label}: Maven was killed before it finished, so its results are partial")
        console = read_console(run.output)
        failing = [c for c in run.cases if c.status in ("fail", "error")]
        for rel in run.unreadable:
            problems.append(f"{rel}: report not usable{label}")
        for error in console.compile_errors[:3]:
            problems.append(f"compilation failed{label}: {error}")
        for goal in console.goal_failures:
            if console.compile_errors and "compil" in goal.lower():
                continue
            problems.append(f"build failed{label}: {goal}")
        if console.empty_test_goals and (console.crashed or not failing):
            problems.append(f"the surefire goal failed with no failing test in a report{label}: " + ("; ".join(console.headline) or "no reason given"))
        if console.tests_skipped:
            problems.append(f"the build skips its tests{label} (skipTests or maven.test.skip is set): nothing was checked")
        if console.crashed:
            problems.append(f"a test JVM crashed or called System.exit{label}: the class that was running has no report")
        if console.skipped_modules:
            problems.append(f"modules skipped{label} (an earlier module failed, so their tests never ran): {_names(console.skipped_modules)}")
        if run.exit_code not in (0, None):
            if len(problems) == explained and not failing and not run.timed_out:
                tail = "; ".join(console.headline)
                problems.append(f"maven exited {run.exit_code} but its reports show no failure{label}" + (f": {tail}" if tail else ""))
        elif run.exit_code == 0 and not console.built:
            problems.append(f"maven exited 0 without reporting BUILD SUCCESS{label}")
        if run.exit_code == 0 and failing:
            notes.append(f"maven exited 0 although tests failed{label} (testFailureIgnore or the like): the failures are real")
        if run.stale:
            notes.append(f"{_plural(run.stale, 'report file')} from earlier runs ignored{label}")

        for case in run.cases:
            ran_any = True
            if case.flaky:
                counts["flaky"] += 1
            counts[{"pass": "passed", "fail": "failed", "error": "errored", "skip": "skipped"}[case.status]] += 1
            if case.status in ("fail", "error"):
                file = _file_of(repo, project, case.module, case.classname)
                chain = case.classname.rpartition(".")[2].replace("$", ".")
                ident = f"{file or case.classname}::{chain}.{case.name}"
                failures.append(clip(f"FAILED {ident}" + (f": {case.message}" if case.message else "")))
        counts["classes"] += len({(c.module, c.classname) for c in run.cases})

        scope = _scope(project, invocation)
        for want in invocation.wants:
            _check_want(want, run, problems, notes, label)
        if scope:
            _check_modules(repo, project, scope, run, problems, notes, label, len(problems) == explained, ctx)
        if invocation.wants and invocation.selectors:
            _check_extra(invocation, run, notes, label)
        if invocation.am:
            named = "none of their tests ran unless a class of the same name is in them" if invocation.selectors else "their tests ran too"
            notes.append(
                f"-am: the modules this one needs were built from the tree by Maven (never taken from the local repository), {named}{label}"
            )
        if not run.cases and not run.timed_out and len(problems) == explained:
            problems.append(f"no tests ran{label}: Maven wrote no test report (a selector that matched nothing exits 0)")

    problems = list(dict.fromkeys(problems))  # two things asked for in two spellings fail once, not twice
    ran = counts["passed"] + counts["failed"] + counts["errored"]
    if counts["skipped"] and ran:
        notes.append(f"{_plural(counts['skipped'], 'test')} skipped or disabled, so not run")
    if ran_any and ran == 0:
        problems.append("every test that ran was skipped, so nothing was checked")
    notes.extend(f"{h}" for run in runs for h in project_hazards(project, run) if h not in notes)

    timed_out = any(r.timed_out for r in runs)
    codes = [r.exit_code for r in runs if r.started]
    exit_code = None if timed_out else next((c for c in codes if c), 0)
    bad = counts["failed"] + counts["errored"]
    passed = not problems and not failures and bad == 0 and all(c == 0 for c in codes) and bool(codes) and ran > 0
    summary = f"{counts['passed']} passed, {bad} failed, {counts['skipped']} skipped in {_plural(counts['classes'], 'test class')}"
    summary += "; timed out" if timed_out else ""
    result = {
        "passed": passed,
        "exit_code": exit_code,
        "timed_out": timed_out,
        "duration_s": round(duration, 2),
        "command": clip(command, MAX_COMMAND),
        "summary": clip(summary),
        "failures": failures[:MAX_FAILURES],
        "failure_count": len(failures),
        "problems": [clip(p) for p in problems[:MAX_PROBLEMS]],
        "counts": counts,
    }
    if len(problems) > MAX_PROBLEMS:
        result["problems_omitted"] = len(problems) - MAX_PROBLEMS
    if notes:
        result["notes"] = [clip(n) for n in dict.fromkeys(notes)]
    return result


def project_hazards(project: Project, run: Run) -> list[str]:
    """The pom settings of the modules a run covered that can narrow or skip tests (named, not judged: the reports are)."""
    if run.invocation.wants:
        dirs = {w.module for w in run.invocation.wants}
        modules = [m for m in project.modules if m.dir in dirs]
    else:
        modules = project.modules
    return [f"{h}: tests may be narrowed or skipped by it" for m in modules for h in m.hazards]


def _scope(project: Project, invocation: Invocation) -> list[Module]:
    """The modules whose whole suite the run was asked for: every module for no request, else the ones asked for whole."""
    if not invocation.wants:
        return list(project.modules)
    dirs = {w.module for w in invocation.wants if w.cls is None}
    return [m for m in project.modules if m.dir in dirs]


def _check_modules(
    repo: Path, project: Project, scope: list[Module], run: Run, problems: list[str], notes: list[str], label: str, hint: bool, ctx: dict
) -> None:
    """Every module asked for needs evidence that its test classes ran: a module that built and shows none is not a pass.

    The rule for what is expected (see `predict`): classes with a test method of their own in the test sources, and classes
    the compiler resolved as tests (inherited, composed annotation, runner). The middle ground is explicit:
    - a class ripple KNOWS holds tests (the known set, see MARKERS) and that has no result is a problem, whatever its name;
    - a class ripple cannot decide (an annotation it does not know on a void method, a supertype outside the repo or one it
      cannot choose between) with no result is a NOTE that names it and why, unless the request asked for it by name, which
      makes it a problem: `mvn test` runs only what a surefire pattern names, and a helper is not a test;
    - a module with test sources (or a root ripple cannot read) and no current compiled test classes is a problem;
    - results that came from a module whose prediction is empty are a problem: the prediction is wrong about that module.
    """
    if run.timed_out:
        return
    if "index" not in ctx:
        ctx["index"] = ClassIndex(repo, project, compiled_classes(repo))
    # a class has a result when a testcase says it ran in it, or when the report of that class holds tests: a runner such as
    # JUnit's AllTests or a Suite reports its tests under the names of the cases it generated
    seen = {(c.module, c.classname) for c in run.cases} | {(c.module, c.suite) for c in run.cases if c.suite}

    def has_result(module: str, cls: str) -> bool:
        return (module, cls) in seen or ("$" not in cls and any(m == module and n.startswith(cls + "$") for m, n in seen))

    empty: list[str] = []
    integration: list[str] = []
    generated: list[str] = []
    unreadable: list[str] = []
    undecided: list[tuple[str, str, str]] = []  # (class, why, where)
    for module in scope:
        prediction = predict(repo, project, module, ctx["index"])
        integration.extend(prediction.integration)
        generated.extend(prediction.generated)
        unreadable.extend(prediction.unreadable)
        where = f" in module {module.dir}" if module.dir else ""
        ran_here = any(m == module.dir for m, _ in seen)
        missing = [name for name in prediction.classes if not has_result(module.dir, name)]
        if missing:
            why = (
                " (never run: surefire picks up Test*, *Test, *Tests and *TestCase by default, and the build can exclude more)"
                if hint
                else ""
            )
            problems.append(f"no results{where}{label} for {_plural(len(missing), 'test class')}: {_names(missing)}{why}")
        for cls, reason in prediction.uncertain:
            if has_result(module.dir, cls):
                continue
            if any(w.cls == cls and w.module == module.dir for w in run.invocation.wants):
                problems.append(f"{cls} was asked for and ripple cannot tell whether it holds tests ({reason}), and no test of it ran{where}{label}")
            else:
                undecided.append((cls, reason, where))
        if prediction.untrusted and hint:
            problems.append(f"cannot predict the test classes of the module{where}{label}: {prediction.untrusted}")
        if not prediction.classes and not prediction.uncertain:
            if ran_here:
                problems.append(
                    f"ripple predicted no test class in the module{where}{label} but tests ran there: "
                    "its prediction is incomplete, so a class that never ran could go unnoticed"
                )
            elif not prediction.untrusted:
                empty.append(module.dir or ".")
    if empty and len(empty) < len(scope):
        notes.append(f"no test classes in {_names([e if e != '.' else 'the root module' for e in empty])}")
    if integration:
        notes.append(f"{_plural(len(integration), 'integration test class')} not run by `mvn test` (failsafe's, in `verify`): {_names(integration)}")
    if generated:
        notes.append(
            f"{_plural(len(generated), 'compiled class')} with no hand-written source (generated, or left over from an earlier build) "
            f"left out of the prediction: {_names(generated)}"
        )
    for cls, reason, where in undecided[:MAX_NAMES]:
        notes.append(f"cannot tell whether {cls} holds tests ({clip(reason, 110)}), and no test of it ran{where}{label}: not a failure")
    if len(undecided) > MAX_NAMES:
        rest = [cls for cls, _, _ in undecided[MAX_NAMES:]]
        notes.append(f"{_plural(len(rest), 'more class')} ripple cannot tell hold tests, and none of them ran{label}: {_names(rest)}")
    if unreadable:
        notes.append(
            f"{_plural(len(unreadable), 'compiled class file')} could not be read (not a class file javac wrote, over the size cap, "
            f"or not a regular file inside the repo){label}: {_names(unreadable)}"
        )


def _check_want(want: Want, run: Run, problems: list[str], notes: list[str], label: str) -> None:
    """What was asked for must have a result of its own: another class's or another module's does not stand in."""
    if want.cls is None:
        return  # a module's whole suite: _check_modules
    here = [c for c in run.cases if c.module == want.module]
    if want.method is None:
        mine = [c for c in here if c.classname == want.cls or c.classname.startswith(want.cls + "$")]
        subject = f"{want.given}: {want.cls.rpartition('.')[2]}"
    else:
        mine = [c for c in here if c.classname == want.cls and c.base == want.method]
        subject = f"{want.given}: {want.cls.rpartition('.')[2].replace('$', '.')}.{want.method}"
    if run.timed_out:
        return
    if not mine:
        reason = "the selector matched nothing" if want.method else "no test of it ran"
        if any(c.classname == want.cls for c in here):
            reason += f", though other tests of the class ran"
        problems.append(f"{subject}: no test ran{label} ({reason})")
        return
    if all(c.status == "skip" for c in mine):
        problems.append(f"{subject}: skipped or disabled, so it did not run{label}")
        return
    if any(c.status == "skip" for c in mine):
        notes.append(f"{subject}: {sum(c.status == 'skip' for c in mine)} of {len(mine)} skipped{label}")
    if want.method is not None:
        if want.overloads > 1 or any(c.signature is not None for c in mine) and want.params is None:
            sigs = sorted({c.signature or "" for c in mine})
            notes.append(
                f"{subject} is overloaded ({want.overloads} test methods of that name) and Maven selects by name, "
                f"so every overload that ran counts: {', '.join(f'({s})' for s in sigs)}{label}"
            )
        if want.params is not None:
            wanted = _simple(want.params)
            exact = [c for c in mine if _simple(c.signature or "") == wanted]
            if not exact:
                problems.append(f"{subject}({want.params}): only other overloads ran ({', '.join(sorted({c.signature or '' for c in mine}))}){label}")
            elif all(c.status == "skip" for c in exact):
                problems.append(f"{subject}({want.params}): the overload asked for was skipped or disabled, so it did not run{label}")
        invocations = len([c for c in mine if "[" in c.name])
        if invocations > 1:
            notes.append(f"{subject}: {invocations} invocations of a parameterized test{label}")


def _check_extra(invocation: Invocation, run: Run, notes: list[str], label: str) -> None:
    """Tests that ran and were not asked for (the selector was a name, and the build decides what a name covers)."""

    def asked(case: Case) -> bool:
        for want in invocation.wants:
            if want.module != case.module or want.cls is None:
                continue
            if want.method is None and (case.classname == want.cls or case.classname.startswith(want.cls + "$")):
                return True
            if want.method is not None and case.classname == want.cls and case.base == want.method:
                return True
        return False

    extra = [c for c in run.cases if not asked(c)]
    if extra:
        names = [f"{c.classname}.{c.name}" for c in extra]
        notes.append(f"{_plural(len(extra), 'test')} ran that were not asked for{label}: {_names(names)}")


# ---- running it --------------------------------------------------------------------------------


@contextlib.contextmanager
def repo_lock(repo: Path, timeout: float, builds: str = "Maven runs in one target/"):
    """Hold the repository's check lock, waiting up to `timeout` seconds for it. Raises ValueError when another check holds it.

    Two Maven runs writing one target/ at once cannot be told apart by the snapshot that picks a run's reports out, so
    one check at a time per repository: an advisory flock on a file in the temporary directory (never in the repo),
    named by the repo's path, so it covers the threads of one server and separate ripple processes. A build ripple
    did not start (an IDE) is not covered. Where flock does not exist, or the file cannot be opened, there is no lock.
    """
    try:
        import fcntl
    except ImportError:
        yield
        return
    name = hashlib.sha256(str(repo).encode()).hexdigest()[:20]
    try:
        fd = os.open(os.path.join(tempfile.gettempdir(), f"ripple-check-{name}.lock"), os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    except OSError:
        yield
        return
    try:
        deadline = time.monotonic() + max(timeout, 0.0)
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise ValueError(
                        "another check is running in this repository (ripple runs one at a time per repository, "
                        f"because two {builds} cannot be told apart): try again when it is done"
                    ) from None
                time.sleep(0.05)
        yield
    finally:
        os.close(fd)  # closing the descriptor releases the lock


def run(repo: Path, tests: list[str] | None, timeout: float) -> dict:
    """Run the Java tests asked for (or the whole build's), and judge the reports. Raises ValueError for a request it cannot make."""
    repo = repo.resolve()
    check_tests(repo, tests or [])
    if build_tool(repo) == "gradle":
        from . import gradle_check

        return gradle_check.run(repo, tests, timeout)
    project = find_project(repo)
    wants = parse_wants(repo, project, tests or [])
    invocations = plan(project, wants)
    tool = choose_tool(repo)
    settings = hazards(repo)
    started = time.monotonic()
    with repo_lock(repo, timeout):  # the whole of it: a second check's reports must not land between a snapshot and its read
        runs = []
        for invocation in invocations:
            left = timeout - (time.monotonic() - started)
            if left <= 0:
                runs.append(Run(invocation, "", None, started=False))
                continue
            before = snapshot(repo)
            started_ns = time.time_ns()
            outcome = run_process(invocation.argv(tool), repo, left)
            mine, stale = fresh(repo, before, started_ns)
            cases, unreadable = [], []
            for rel in mine:
                got, reason = read_report(repo, project, rel)
                cases.extend(got)
                if reason:
                    unreadable.append(reason)
            runs.append(Run(invocation, outcome.stdout, outcome.returncode, outcome.timed_out, True, cases, unreadable, stale))
        command = "; ".join(r.invocation.command(tool) for r in runs)
        return judge(repo, project, runs, command, time.monotonic() - started, timeout, settings)
