"""Every call, constructor and override edge ripple makes in the Java fixture, against javac's own resolution.

The truth of bench/ground_truth_java.json is what Java does; this is the compiler's word on it. The fixture is compiled
with `javac -g` and disassembled with `javap -c -l -p -s`, and each edge ripple made is looked for among the invoke
instructions javac wrote: the same callee name, the same erased parameter types, an owner that is the callee's class or
a repo subtype of it, on a line of the call expression or the first line of its statement (javac puts a nested `new`
of a multi-line expression on an earlier line). An override edge is confirmed when the subclass declares the exact
descriptor javac used at the site, or a bridge for it.

The JDK is a development-time oracle only: these tests are skipped unless javac and javap are on PATH and `javac -version`
reports 21 or newer (the language level of the fixture, and the JDK the built-in member tables were read off), so a
machine that happens to have an older JDK, or none, runs every other test and passes.
"""

import re
import shutil
import subprocess
from collections import Counter, defaultdict
from pathlib import Path

import pytest
from conftest import FIXTURES

pytest.importorskip("tree_sitter_java")

from ripple.indexer import walk_repo  # noqa: E402
from ripple.java_indexer import Declarations, load_parser, strip_type  # noqa: E402
from ripple.java_resolver import JavaResolver  # noqa: E402
from ripple.profile import JAVA  # noqa: E402

FIXTURE = FIXTURES / "java" / "shop-java"
JAVAC, JAVAP = shutil.which("javac"), shutil.which("javap")
MINIMUM_JDK = 21


def javac_version(javac: str | None) -> tuple[int, str]:
    """(the major version `javac -version` reports, what it printed): 0 when there is no javac or it says nothing readable.
    Older javac versions are `1.8.0_392`, newer `17.0.9` or `25-ea`; the version goes to stdout, or to stderr in an old one."""
    if javac is None:
        return 0, "no javac on PATH"
    try:
        done = subprocess.run([javac, "-version"], capture_output=True, text=True, timeout=60, check=False)
    except (OSError, subprocess.SubprocessError):
        return 0, "javac -version did not run"
    said = (done.stdout + done.stderr).strip()
    found = re.search(r"javac (?:1\.)?(\d+)", said)
    return (int(found.group(1)) if found else 0), said or "javac -version printed nothing"


JAVAC_MAJOR, JAVAC_SAID = javac_version(JAVAC)
NO_ORACLE = (
    "no javap on PATH (javac and javap are the oracle)"
    if JAVAC_MAJOR >= MINIMUM_JDK and not JAVAP
    else f"the oracle needs a JDK {MINIMUM_JDK} or newer on PATH: javac -version said {JAVAC_SAID!r}"
)
needs_jdk = pytest.mark.skipif(JAVAC_MAJOR < MINIMUM_JDK or not JAVAP, reason=NO_ORACLE)

# What an annotation processor would write and the fixture's Chains.reflect names; the compile needs it, ripple must not.
GENERATED = "package com.acme.shop;\nclass AutoValue_Item {\n    static Item create(String s) { return Item.free(s); }\n}\n"
PRIMITIVES = {"I": "int", "J": "long", "Z": "boolean", "B": "byte", "C": "char", "S": "short", "F": "float", "D": "double", "V": "void"}


class Tracing(JavaResolver):
    """The resolver, keeping where each call expression starts and ends so an invoke can be looked for on its lines."""

    def __init__(self, *args):
        super().__init__(*args)
        self.sites = []

    def record(self, outcome, caller, at, shown, t):
        kind, info = outcome
        if kind == "edge":
            whole = at
            while whole.parent is not None and whole.type not in (
                "method_invocation", "object_creation_expression", "explicit_constructor_invocation", "enum_constant"
            ):
                whole = whole.parent
            stmt = whole
            while stmt.parent is not None and not stmt.type.endswith(("statement", "declaration", "declarator")) and stmt.type != "enum_constant":
                stmt = stmt.parent
            lines = (whole.start_point[0] + 1, whole.end_point[0] + 1, stmt.start_point[0] + 1)
            self.sites.append((t.file, at.start_point[0] + 1, info, lines))
        super().record(outcome, caller, at, shown, t)


def descriptor_params(descriptor: str) -> list[str]:
    inner, out, i = descriptor[descriptor.index("(") + 1 : descriptor.index(")")], [], 0
    while i < len(inner):
        dims = 0
        while inner[i] == "[":
            dims, i = dims + 1, i + 1
        if inner[i] == "L":
            end = inner.index(";", i)
            name, i = inner[i + 1 : end].rsplit("/", 1)[-1].rsplit("$", 1)[-1], end + 1
        else:
            name, i = PRIMITIVES[inner[i]], i + 1
        out.append(name + "[]" * dims)
    return out


def read_javap(path: Path):
    """({(source file, line): [(owner, name, params)]} of the invoke instructions, {class: {(name, params)}} declared)."""
    invokes, declared = defaultdict(list), defaultdict(set)
    cls = src = fname = name = None
    code, table = [], []

    def flush():
        nonlocal code, table
        for offset, owner, called, params in code:
            line = None
            for number, start in sorted(table, key=lambda row: row[1]):
                if start <= offset:
                    line = number
            if line is not None:
                invokes[(src, line)].append((owner, called, params))
        code, table = [], []

    for raw in path.read_text(errors="replace").splitlines():
        if (m := re.match(r'^Compiled from "(.+)"', raw)) is not None:
            flush()
            fname = m.group(1)
        elif (m := re.match(r"^[\w\s]*?(?:class|interface|enum) ([\w.$]+)", raw)) and not raw.startswith(" "):
            flush()
            cls = m.group(1)
            src = (cls.rsplit(".", 1)[0].replace(".", "/") + "/" if "." in cls else "") + fname
        elif re.match(r"^  \S", raw) and ("(" in raw or raw.strip().startswith("static {}")):
            flush()
            found = re.search(r"([\w$<>]+)\(", raw)
            name = found.group(1) if found else "<clinit>"
            name = "<init>" if name == cls.rsplit(".", 1)[-1] else name
        elif (m := re.match(r"^    descriptor: (\(.*)", raw)):
            declared[cls].add((name, tuple(descriptor_params(m.group(1)))))
        elif (m := re.match(r"^\s+(\d+): (invoke\w+) .*// (?:Interface)?Method (.+)$", raw)):
            ref = re.match(r'(?:([\w/$]+)\.)?"?([\w$<>]+)"?:(\(.*)$', m.group(3))
            if ref:
                owner = ref.group(1).replace("/", ".") if ref.group(1) else cls
                code.append((int(m.group(1)), owner, ref.group(2), descriptor_params(ref.group(3))))
        elif (m := re.match(r"^\s+line (\d+): (\d+)", raw)):
            table.append((int(m.group(1)), int(m.group(2))))
    flush()
    return invokes, declared


def binary(t) -> str:
    if t.outer is None:
        return t.fqn
    if t.kind in ("anon", "enumconst") or t.ctx_method is not None:
        return binary(t.outer) + "$?"  # javac numbers these its own way: matched by prefix
    return binary(t.outer) + "$" + t.simple


@pytest.fixture(scope="module")
def oracle(tmp_path_factory):
    work = tmp_path_factory.mktemp("javac")
    stub = work / "stub" / "com" / "acme" / "shop"
    stub.mkdir(parents=True)
    (stub / "AutoValue_Item.java").write_text(GENERATED)
    sources = [p for p in (FIXTURE / "src").rglob("*.java") if "resources" not in p.parts and p.name != "package-info.java"]
    sources += [FIXTURE / "variants" / "free" / "java" / "com" / "acme" / "flavor" / "Flavor.java", stub / "AutoValue_Item.java"]
    classes = work / "classes"
    classes.mkdir()
    subprocess.run([JAVAC, "-g", "-proc:none", "-nowarn", "-d", str(classes), *map(str, sources)], check=True, capture_output=True)
    listing = work / "fixture.javap"
    with open(listing, "w") as out:
        subprocess.run([JAVAP, "-c", "-l", "-p", "-s", *map(str, classes.rglob("*.class"))], check=True, stdout=out)
    declarations = Declarations()
    files, _ = walk_repo(FIXTURE, JAVA)
    parser = load_parser()
    for path in files:
        declarations.add_file(path, FIXTURE, parser)
    declarations.finish()
    resolver = Tracing(declarations, JAVA).run()
    return resolver, declarations, *read_javap(listing)


def check_edges(oracle) -> tuple[Counter, list]:
    resolver, declarations, invokes, declared = oracle
    info = {}
    for mid, m in declarations.methods.items():
        params = ["?" if resolver._is_type_variable(strip_type(w)[0], m.owner, m) else strip_type(w)[0].rsplit(".", 1)[-1] + "[]" * strip_type(w)[1] for w, _ in m.params]
        info[mid] = (binary(m.owner), m.name, params)
    for t in declarations.every_type:
        if not t.ctors and not t.dup:  # a class with no constructor written: the class is its own constructor
            info[t.fqn] = (binary(t), "<init>", [strip_type(w)[0].rsplit(".", 1)[-1] for w, _ in t.components] if t.kind == "record" else [])
    subs = {binary(t): {binary(s) for s in resolver.all_subs(t)} for t in declarations.every_type}
    roots = ("src/main/java/", "src/test/java/")

    def owner_ok(callee_binary, owner):
        if owner == callee_binary:
            return True
        if "$?" in callee_binary:
            return owner.startswith(callee_binary.split("$?")[0])
        return owner in subs.get(callee_binary, ()) or any("$?" in s and owner.startswith(s.split("$?")[0]) for s in subs.get(callee_binary, ()))

    def params_ok(want, got, constructor):
        if constructor:  # javac adds the outer instance, or an enum's name and ordinal, in front
            got = got[len(got) - len(want):] if want else []
        return len(want) == len(got) and all(w == "?" or w == g or w.rstrip("[]") == "?" for w, g in zip(want, got))

    tally, bad = Counter(), []
    for file, line, edges, (first, last, statement) in resolver.sites:
        root = next((r for r in roots if file.startswith(r)), None)
        if root is None:
            continue
        rel = file[len(root):]
        for callee, kind in edges:
            if kind == "override":
                continue
            binary_name, name, params = info[callee]
            found = any(
                called == name and owner_ok(binary_name, owner) and params_ok(params, got, name == "<init>")
                for number in dict.fromkeys([line, *range(first, last + 1), statement])
                for owner, called, got in invokes.get((rel, number), [])
            )
            tally["confirmed" if found else "NOT CONFIRMED"] += 1
            if not found:
                bad.append((file, line, callee))
        base = next((c for c in edges if c[1] == "call"), None)
        used = [got for owner, called, got in invokes.get((rel, line), []) if base and called == info[base[0]][1]]
        for callee, kind in edges:
            if kind != "override" or "$?" in info[callee][0] or info[callee][0] not in declared:
                continue
            ok = any((info[callee][1], tuple(got)) in declared[info[callee][0]] for got in used)
            tally["override confirmed" if ok else "override NOT CONFIRMED"] += 1
            if not ok:
                bad.append((file, line, callee))
    return tally, bad


@needs_jdk
def test_every_call_and_constructor_edge_is_one_javac_wrote(oracle):
    tally, bad = check_edges(oracle)
    assert bad == [], bad
    assert tally["confirmed"] >= 80 and tally["override confirmed"] >= 10  # the fixture has that many: a vacuous pass is no pass


@needs_jdk
def test_the_edges_the_rules_refuse_are_edges_javac_does_not_have(oracle):
    # the two measured wrong answers: a subclass overload for a superclass call, a repo overload for a Writer call
    resolver, _, invokes, _ = oracle
    fill = [(owner, name, params) for owner, name, params in invokes[("com/acme/shop/BulkCart.java", 24)] if name == "add"]
    assert fill == [("com.acme.shop.BulkCart", "add", ["String"])]  # javac: Cart.add(String), found through BulkCart
    write = [(owner, name, params) for owner, name, params in invokes[("com/acme/shop/Ledger.java", 27)] if name == "write"]
    assert write == [("com.acme.shop.Ledger", "write", ["String"])]  # javac: Writer.write(String), inherited
    assert not any(e.callee == "com.acme.shop.Ledger.write(Item)" and e.line == 27 for e in resolver.edges)
