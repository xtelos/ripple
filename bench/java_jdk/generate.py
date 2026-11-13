"""Write ripple/data/java_jdk.txt: what the JDK's own class files say each type of java.base and java.compiler has.

Run by hand, like bench/go_stdlib/generate.py, with the home of each JDK to merge (no network; ripple reads the result
and never needs a JDK):

    python bench/java_jdk/generate.py JDK_HOME [JDK_HOME ...]                  write the table
    python bench/java_jdk/generate.py --check JDK_HOME [JDK_HOME ...]          read it a second way (javap) and diff
    python bench/java_jdk/generate.py --reflect JDK_HOME [JDK_HOME ...]        hold it against each JDK's own reflection
                                                                               (bench/java_jdk/reflect.py, Reflect.java)

The homes ripple ships are the newest build of every GA feature release from 8 through 27 (twenty), Temurin or AdoptOpenJDK,
each downloaded with the sha256 its distribution API publishes checked before it was unpacked. ripple cannot know which JDK a
repo is built against, so every fact is merged across ALL of them in the direction its use is safe (the loader,
ripple/java_jdk.py, says how each is used). The merge is the union, so ONLY ABSENCE PROVES: a JDK left out of the homes is a
place where a member, a supertype or a member type can hide, and the table's horizon is the newest home. A repo built with
a later JDK may meet a fact the table lacks. The order of the homes does not matter: the output is byte-identical.
  - methods, fields, member types and supertypes: the UNION. A method a later JDK removed, or one it added, is a name a
    supertype may declare, and "may declare" only ever makes a call unresolved.
  - final: the INTERSECTION over the JDKs that have the type. A type that is final in one JDK and not in another may
    have a repo subtype.
  - ctor: the UNION. `yes` when any JDK that has the type gives it a public or protected constructor; only `no` proves "no
    repo class extends it directly". `na` for an interface or an annotation.
  - a type that only some JDKs have is described over the JDKs that have it.
  - a type whose class file chain is not fully readable, or one of whose supertypes is not in the table, or whose kind
    (class, interface, enum, annotation) differs between JDKs, is LEFT OUT, and every one is printed with its reason. A type
    whose inherited members are silently missing would let a wrong edge stand, and an unknown is safe. The generator
    STOPS when a type it leaves out is below a `ctor=no` class that is not final: ripple's no_repo_instance takes the
    absence of a row below such a class as proof, and a dropped row would break it.
  - the header carries `# rows: N`; the loader refuses a file that does not hold exactly N rows.

The class files are read directly (a small parser below): for JDK 8, which has no modules, the jars its runtime puts on the
boot class path (rt.jar and jce.jar, which holds javax.crypto, among them; a class in two jars must be identical), and for JDK 9 and later
`jimage extract` of the requested modules out of lib/modules (Temurin 24 and later ship no jmods). The count of a method's
parameters comes from its descriptor, never from the declaration's text. A nested type is named by its InnerClasses entry
(`java.util.Map.Entry`), never by replacing `$`.
"""

from __future__ import annotations

import argparse
import os
import re
import struct
import subprocess
import sys
import tempfile
import zipfile
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUTPUT = HERE.parent.parent / "ripple" / "data" / "java_jdk.txt"
DEFAULT_MODULES = ("java.base", "java.compiler")

ACC_PUBLIC, ACC_PROTECTED, ACC_FINAL, ACC_VARARGS = 0x0001, 0x0004, 0x0010, 0x0080
ACC_INTERFACE, ACC_ANNOTATION, ACC_ENUM = 0x0200, 0x2000, 0x4000
OBJECT = "java/lang/Object"


class ClassFile:
    """The parts of a class file the table needs: access flags, supertypes, members, InnerClasses, module exports."""

    __slots__ = ("name", "access", "super_name", "interfaces", "fields", "methods", "inner", "exports")

    def __init__(self) -> None:
        self.interfaces: list[str] = []
        self.fields: list[tuple[str, int]] = []
        self.methods: list[tuple[str, str, int]] = []
        self.inner: list[tuple[str, str | None, str | None, int]] = []
        self.exports: list[str] = []  # module-info: the packages exported to everyone (internal form)


def parse_class(data: bytes) -> ClassFile:
    pos = 8  # magic, minor, major
    (count,) = struct.unpack_from(">H", data, pos)
    pos += 2
    pool: list = [None] * count
    i = 1
    while i < count:
        tag = data[pos]
        pos += 1
        if tag == 1:
            (length,) = struct.unpack_from(">H", data, pos)
            pool[i] = (1, data[pos + 2 : pos + 2 + length].decode("utf-8", "replace"))
            pos += 2 + length
        elif tag in (3, 4):
            pos += 4
        elif tag in (5, 6):
            pos += 8
            i += 1  # a long or double takes two slots
        elif tag in (7, 8, 16, 19, 20):
            pool[i] = (tag, struct.unpack_from(">H", data, pos)[0])
            pos += 2
        elif tag in (9, 10, 11, 12, 17, 18):
            pos += 4
        elif tag == 15:
            pos += 3
        else:
            raise ValueError(f"unknown constant pool tag {tag}")
        i += 1

    def utf(index: int) -> str:
        return pool[index][1]

    def named(index: int) -> str:  # a Class, Module or Package constant: the name it points at
        return utf(pool[index][1])

    cf = ClassFile()
    cf.access, this_index, super_index, n_interfaces = struct.unpack_from(">HHHH", data, pos)
    pos += 8
    cf.name = named(this_index)
    cf.super_name = named(super_index) if super_index else None
    for _ in range(n_interfaces):
        cf.interfaces.append(named(struct.unpack_from(">H", data, pos)[0]))
        pos += 2

    def skip_attributes(p: int) -> int:
        (n,) = struct.unpack_from(">H", data, p)
        p += 2
        for _ in range(n):
            (length,) = struct.unpack_from(">I", data, p + 2)
            p += 6 + length
        return p

    for kind in ("fields", "methods"):
        (n,) = struct.unpack_from(">H", data, pos)
        pos += 2
        for _ in range(n):
            access, name_index, desc_index = struct.unpack_from(">HHH", data, pos)
            pos = skip_attributes(pos + 6)
            if kind == "fields":
                cf.fields.append((utf(name_index), access))
            else:
                cf.methods.append((utf(name_index), utf(desc_index), access))

    (n,) = struct.unpack_from(">H", data, pos)
    pos += 2
    for _ in range(n):
        name_index, length = struct.unpack_from(">HI", data, pos)
        body = pos + 6
        attribute = utf(name_index)
        if attribute == "InnerClasses":
            (entries,) = struct.unpack_from(">H", data, body)
            for k in range(entries):
                inner, outer, name, flags = struct.unpack_from(">HHHH", data, body + 2 + 8 * k)
                cf.inner.append((named(inner), named(outer) if outer else None, utf(name) if name else None, flags))
        elif attribute == "Module":
            p = body + 6  # module name, flags, version
            (requires,) = struct.unpack_from(">H", data, p)
            p += 2 + 6 * requires
            (exports,) = struct.unpack_from(">H", data, p)
            p += 2
            for _ in range(exports):
                package, _flags, to_count = struct.unpack_from(">HHH", data, p)
                if to_count == 0:  # a qualified export is not the repo's to use
                    cf.exports.append(named(package))
                p += 6 + 2 * to_count
        pos = body + length
    return cf


def count_parameters(descriptor: str) -> int:
    i, n = 1, 0
    while descriptor[i] != ")":
        while descriptor[i] == "[":
            i += 1
        if descriptor[i] == "L":
            i = descriptor.index(";", i)
        i += 1
        n += 1
    return n


LEGACY_BOOT_JARS = ("rt.jar", "resources.jar", "sunrsasign.jar", "jsse.jar", "jce.jar", "charsets.jar", "jfr.jar")


def legacy_boot_jars(home: Path) -> list[Path]:
    """The jars of a JDK 8 that its runtime puts on the boot class path (`sun.boot.class.path`), the ones that exist in this
    build: javax.crypto is in jce.jar, not rt.jar. jre/lib/ext is the extension class loader's and is not read: none of the
    packages in its jars is in the table's scope (looked at on Temurin 8u512)."""
    jars = [home / "jre" / "lib" / name for name in LEGACY_BOOT_JARS if (home / "jre" / "lib" / name).exists()]
    if home / "jre" / "lib" / "rt.jar" not in jars:
        sys.exit(f"{home} has no jre/lib/rt.jar")
    return jars


def describe(home: Path) -> tuple[int, str]:
    """(the feature number, the exact version string with its vendor) of a JDK home. The `release` file of a JDK 9 to 14
    has no FULL_VERSION, so the build comes from the banner of its own `java -version` (`(build 9.0.4+11)`)."""
    release = dict(re.findall(r'^(\w+)="?([^"\n]*)"?$', (home / "release").read_text(), re.M))
    raw = release.get("JAVA_VERSION")
    if raw is None:
        sys.exit(f"{home}/release has no JAVA_VERSION")
    major = int(raw.split(".")[1] if raw.startswith("1.") else raw.split(".")[0])
    full = release.get("FULL_VERSION")
    if full is None:
        try:
            banner = subprocess.run([str(home / "bin" / "java"), "-version"], capture_output=True, text=True, errors="replace").stderr
        except OSError as error:
            sys.exit(f"{home}/release has no FULL_VERSION and {home}/bin/java cannot be run: {error}")
        found = re.search(r"\(build ([^\s,)]+)", banner)
        if found is None:
            sys.exit(f"{home}/release has no FULL_VERSION and `java -version` printed no build: {banner[:200]!r}")
        full = found.group(1)
    return major, f"{full} ({release.get('IMPLEMENTOR', 'unknown vendor')})"


class Jdk:
    """The class files of one JDK that the table is built from."""

    def __init__(self, home: str, modules: tuple[str, ...], work: Path) -> None:
        self.home = Path(home)
        self.modules = modules
        self.major, self.version = describe(self.home)
        self.legacy = not (self.home / "lib" / "modules").exists()
        self.module_of: dict[str, str] = {}  # internal name -> module (JDK 9 and later)
        self.exports: dict[str, set[str]] = {}  # module -> the packages it exports to everyone
        self._paths: dict[str, Path] = {}
        self._zips: dict[str, zipfile.ZipFile] = {}  # JDK 8: internal name -> the boot class path jar that holds it
        self._parsed: dict[str, ClassFile] = {}
        if self.legacy:
            self.boot_jars = legacy_boot_jars(self.home)
            for jar in self.boot_jars:
                archive = zipfile.ZipFile(jar)
                for entry in archive.namelist():
                    if not entry.endswith(".class"):
                        continue
                    held = self._zips.setdefault(entry[:-6], archive)
                    if held is not archive and held.read(entry) != archive.read(entry):
                        sys.exit(f"JDK {self.major}: {entry[:-6]} is in {held.filename} and in {jar} and the two differ")
            self.names = set(self._zips)
        else:
            self.names = set()
            for module in modules:
                target = work / f"jdk{self.major}"
                subprocess.run(
                    [str(self.home / "bin" / "jimage"), "extract", "--dir", str(target), "--include",
                     f"regex:/{re.escape(module)}/.*", str(self.home / "lib" / "modules")],
                    check=True, capture_output=True,
                )
                root = target / module
                self.exports[module] = set(parse_class((root / "module-info.class").read_bytes()).exports)
                for path in sorted(root.rglob("*.class")):
                    internal = path.relative_to(root).with_suffix("").as_posix()
                    if internal == "module-info":
                        continue
                    self.names.add(internal)
                    self._paths[internal] = path
                    self.module_of[internal] = module

    def parse(self, internal: str) -> ClassFile:
        cf = self._parsed.get(internal)
        if cf is None:
            data = self._zips[internal].read(internal + ".class") if self.legacy else self._paths[internal].read_bytes()
            cf = self._parsed[internal] = parse_class(data)
        return cf

    def package(self, internal: str) -> str:
        return internal.rpartition("/")[0]

    def in_scope(self, internal: str, legacy_packages: set[str]) -> bool:
        """Whether the type is one a repo can name: in a package that java.base or java.compiler exports."""
        if self.legacy:
            return self.package(internal) in legacy_packages
        return self.package(internal) in self.exports[self.module_of[internal]]

    def own_entry(self, internal: str) -> tuple[str | None, str | None, int] | None:
        """(outer, simple name, flags) of a nested type, from its own InnerClasses entry; None when it is top level."""
        for inner, outer, name, flags in self.parse(internal).inner:
            if inner == internal:
                return outer, name, flags
        return None

    def canonical(self, internal: str) -> str | None:
        """`java.util.Map.Entry` for java/util/Map$Entry; None for a local or anonymous class (it has no such name)."""
        entry = self.own_entry(internal)
        if entry is None:
            return internal.replace("/", ".")
        outer, name, _ = entry
        if outer is None or name is None or outer not in self.names:
            return None
        parent = self.canonical(outer)
        return None if parent is None else f"{parent}.{name}"

    def accessible(self, internal: str) -> bool:
        """Public (a nested type: public or protected, and every type around it too): a repo type could name it or inherit it."""
        entry = self.own_entry(internal)
        if entry is None:
            return bool(self.parse(internal).access & ACC_PUBLIC)
        outer, name, flags = entry
        if outer is None or name is None or outer not in self.names:
            return False
        return bool(flags & (ACC_PUBLIC | ACC_PROTECTED)) and self.accessible(outer)

    def is_final(self, internal: str) -> bool:
        return bool(self.parse(internal).access & ACC_FINAL)

    def has_accessible_constructor(self, internal: str) -> bool:
        """Whether the class declares a public or protected constructor: one a class of a repo could call, as `super(..)`."""
        return any(name == "<init>" and access & (ACC_PUBLIC | ACC_PROTECTED) for name, _, access in self.parse(internal).methods)

    def kind(self, internal: str) -> str:
        access = self.parse(internal).access
        if access & ACC_ANNOTATION:
            return "annotation"
        if access & ACC_INTERFACE:
            return "interface"
        return "enum" if access & ACC_ENUM else "class"


class Row:
    """What one type has, over the JDKs that have it: sets, merged by union (final: by intersection)."""

    __slots__ = ("kind", "final", "ctor", "jdks", "supers", "types", "fields", "methods")

    def __init__(self, kind: str, final: bool, ctor: bool = False) -> None:
        self.kind, self.final, self.ctor = kind, final, ctor
        self.jdks: set[int] = set()
        self.supers: set[str] = set()
        self.types: set[str] = set()
        self.fields: set[str] = set()
        self.methods: dict[str, list] = {}  # name -> [exact arities, minimum arity of a varargs one or None]

    def add_method(self, name: str, exact: set[int], minimum: int | None) -> None:
        have = self.methods.setdefault(name, [set(), None])
        have[0] |= exact
        if minimum is not None and (have[1] is None or minimum < have[1]):
            have[1] = minimum


def own_members(jdk: Jdk, internal: str) -> tuple[dict[str, list], set[str], set[str]]:
    """The public and protected methods (name -> [exact arities, varargs minimum]), fields and member types the class
    itself declares: what a subclass outside its package can inherit."""
    cf = jdk.parse(internal)
    methods: dict[str, list] = {}
    for name, descriptor, access in cf.methods:
        if name in ("<init>", "<clinit>") or not access & (ACC_PUBLIC | ACC_PROTECTED):
            continue
        n = count_parameters(descriptor)
        have = methods.setdefault(name, [set(), None])
        if access & ACC_VARARGS:
            have[1] = n - 1 if have[1] is None else min(have[1], n - 1)
        else:
            have[0].add(n)
    fields = {name for name, access in cf.fields if access & (ACC_PUBLIC | ACC_PROTECTED)}
    types = {name for inner, outer, name, flags in cf.inner if outer == internal and name and flags & (ACC_PUBLIC | ACC_PROTECTED)}
    return methods, fields, types


def jdk_rows(
    jdk: Jdk, legacy_packages: set[str], dropped: dict[str, list[str]], unreadable_supers: dict[str, set[str]] | None = None
) -> dict[str, Row]:
    """Every nameable type of the JDK with what it declares and inherits. java.lang.Object's members are inherited by
    all and checked on their own by ripple, so they are in a row only for Object itself and for a type that declares
    them again. A type left out because its chain is not fully readable has its readable supertypes put in
    `unreadable_supers` (see merge, which checks every type left out)."""
    rows: dict[str, Row] = {}
    for internal in sorted(jdk.names):
        if not jdk.in_scope(internal, legacy_packages) or internal.endswith(("module-info", "package-info")):
            continue
        canonical = jdk.canonical(internal)
        if canonical is None or not jdk.accessible(internal):
            continue
        chain: list[str] = []
        pending = [internal]
        unreadable: list[str] = []
        while pending:
            cf = jdk.parse(pending.pop())
            for above in (cf.super_name, *cf.interfaces):
                if above is None or above == OBJECT or above in chain:
                    continue
                if above not in jdk.names:
                    unreadable.append(above)
                    continue
                chain.append(above)
                pending.append(above)
        if unreadable:
            dropped["unreadable"].append(f"{canonical} (JDK {jdk.major}: {', '.join(sorted(set(unreadable)))})")
            dropped["_unreadable_types"].append(canonical)
            if unreadable_supers is not None:
                unreadable_supers.setdefault(canonical, set()).update(
                    name for above in chain if jdk.accessible(above) and (name := jdk.canonical(above)) is not None
                )
            continue
        row = Row(jdk.kind(internal), jdk.is_final(internal), jdk.has_accessible_constructor(internal))
        row.jdks.add(jdk.major)
        for above in chain:
            if jdk.accessible(above):
                name = jdk.canonical(above)
                if name is None:
                    raise SystemExit(f"{above} is a supertype of {canonical} and has no canonical name")
                row.supers.add(name)
        for source in (internal, *chain):
            methods, fields, types = own_members(jdk, source)
            for name, (exact, minimum) in methods.items():
                row.add_method(name, exact, minimum)
            row.fields |= fields
            row.types |= types
        rows[canonical] = row
    return rows


def merge(
    per_jdk: list[dict[str, Row]], dropped: dict[str, list[str]], unreadable_supers: dict[str, set[str]] | None = None
) -> dict[str, Row]:
    """The union of the JDKs' rows, with the types that cannot be known left out (and listed in `dropped`, by reason).

    ripple's one reader that takes a MISSING row as proof is java_resolver.no_repo_instance: "this class has no accessible
    constructor, and no table type below it can be extended". A type left out is a missing row, so one that is below a
    `ctor=no` class that is not final would make that proof wrong without a sign. That stops the generator (`sys.exit`),
    with every such type named. Only the supertypes the generator could read are checked: a type left out because it extends
    something outside java.base and java.compiler is seen only up to that point."""
    merged: dict[str, Row] = {}
    unreadable = set(dropped["_unreadable_types"])
    clashes: list[str] = []
    above_dropped: dict[str, set[str]] = defaultdict(set)  # every type left out -> the supertypes it had
    for name, supers in (unreadable_supers or {}).items():
        above_dropped[name] |= supers
    for rows in per_jdk:
        for name, row in rows.items():
            if name in unreadable:
                above_dropped[name] |= row.supers
                continue
            have = merged.get(name)
            if have is None:
                merged[name] = have = Row(row.kind, row.final, row.ctor)
            elif have.kind != row.kind:
                clashes.append(name)
            have.final = have.final and row.final
            have.ctor = have.ctor or row.ctor
            have.jdks |= row.jdks
            have.supers |= row.supers
            have.types |= row.types
            have.fields |= row.fields
            for method, (exact, minimum) in row.methods.items():
                have.add_method(method, exact, minimum)
    for name in sorted(set(clashes)):  # once each, however many JDKs disagree
        above_dropped[name] |= merged.pop(name).supers
        dropped["kind changed between JDKs"].append(name)
    changed = True
    while changed:  # a type whose supertype is not in the table is not known
        changed = False
        for name in sorted(merged):
            missing = sorted(s for s in merged[name].supers if s not in merged)
            if missing:
                above_dropped[name] |= merged.pop(name).supers
                dropped["supertype not in the table"].append(f"{name} ({', '.join(missing)})")
                changed = True
    breaks = [
        f"{name} is left out and is below {above}, a class the table marks ctor=no and open"
        for name in sorted(above_dropped)
        for above in sorted(above_dropped[name])
        if above in merged and merged[above].kind not in ("interface", "annotation") and not merged[above].ctor and not merged[above].final
    ]
    if breaks:
        sys.exit(
            "a type left out of the table is below a class that no_repo_instance proves unextendable from the ABSENCE of a row "
            "below it (ctor=no, open), so that proof would silently go wrong; resolve each before regenerating:\n  " + "\n  ".join(breaks)
        )
    return merged


def format_arities(exact: set[int], minimum: int | None) -> str:
    keep = sorted(e for e in exact if minimum is None or e < minimum)
    return ",".join([*map(str, keep), *([f"{minimum}+"] if minimum is not None else [])])


def ctor_column(row: Row) -> str:
    """`ctor=yes` when a JDK that has the type gives it a public or protected constructor, `ctor=no` when none does, and
    `ctor=na` for an interface or an annotation (it has no constructor to call)."""
    if row.kind in ("interface", "annotation"):
        return "ctor=na"
    return "ctor=yes" if row.ctor else "ctor=no"


def render(rows: dict[str, Row], jdks: list[Jdk], modules: tuple[str, ...]) -> str:
    lines = [
        "# ripple's JDK member table. GENERATED by bench/java_jdk/generate.py from the class files of the JDKs below: do not edit.",
        "# How each column is read, and in which direction it may be used: ripple/java_jdk.py.",
        f"# modules: {' '.join(modules)}",
        f"# rows: {len(rows)}",  # the loader refuses a file with another number of rows: a truncated one
    ]
    lines += [f"# jdk {jdk.major}: {jdk.version}" for jdk in sorted(jdks, key=lambda j: j.major)]
    for name in sorted(rows):
        row = rows[name]
        methods = " ".join(f"{m}/{format_arities(*row.methods[m])}" for m in sorted(row.methods))
        lines.append(
            " | ".join(
                (
                    name, row.kind, "final" if row.final else "open", ctor_column(row), "jdk=" + ",".join(map(str, sorted(row.jdks))),
                    "supers=" + ",".join(sorted(row.supers)), "types=" + ",".join(sorted(row.types)),
                    "fields=" + ",".join(sorted(row.fields)), "methods=" + methods,
                )
            )
        )
    return "\n".join(lines) + "\n"


def build(homes: list[str], modules: tuple[str, ...], work: Path) -> tuple[str, dict[str, Row], list[Jdk], dict[str, list[str]]]:
    jdks = sorted((Jdk(home, modules, work) for home in homes), key=lambda j: j.major)
    if len({j.major for j in jdks}) != len(jdks):
        sys.exit("two of the JDK homes are the same major version")
    # JDK 8 has no modules: its scope is the rt.jar types in a package that a later JDK's modules export
    legacy_packages = {p for jdk in jdks if not jdk.legacy for module in modules for p in jdk.exports[module]}
    dropped: dict[str, list[str]] = defaultdict(list)
    unreadable_supers: dict[str, set[str]] = {}
    per_jdk = [jdk_rows(jdk, legacy_packages, dropped, unreadable_supers) for jdk in jdks]
    rows = merge(per_jdk, dropped, unreadable_supers)
    return render(rows, jdks, modules), rows, jdks, dropped


JAVAP_NAME = re.compile(r"^\s{2}(?P<decl>[^\s].*);$")


def javap_types(jdk: Jdk, legacy_packages: set[str], work: Path) -> dict[str, dict]:
    """What `javap -protected -s` prints for every in-scope class of the JDK: binary name (`java.util.Map$Entry`) ->
    {"public", "final", "interface", "supers" (direct, binary names), "methods": {name: [exact, minimum]}, "fields"}."""
    wanted: dict[str | None, list[str]] = defaultdict(list)  # module (None for JDK 8) -> binary names
    for internal in sorted(jdk.names):
        if jdk.in_scope(internal, legacy_packages):
            wanted[jdk.module_of.get(internal)].append(internal.replace("/", "."))
    found: dict[str, dict] = {}
    outputs: list[str] = []
    for module, names in wanted.items():
        command = [str(jdk.home / "bin" / "javap"), "-protected", "-s"]
        command += ["-cp", os.pathsep.join(map(str, jdk.boot_jars))] if jdk.legacy else ["--module", module]
        for start in range(0, len(names), 400):  # javap 8 has no @argfile, so the names go on the command line in batches
            done = subprocess.run([*command, *names[start : start + 400]], capture_output=True, text=True, errors="replace")
            if not done.returncode:
                outputs.append(done.stdout)
                continue
            # javap 24 and later STOPS the batch at a generated class it refuses (java.lang.invoke.*$Holder: an access flag it
            # does not know) and exits 1: read the batch again, one class at a time, and name each class it refuses
            for name in names[start : start + 400]:
                one = subprocess.run([*command, name], capture_output=True, text=True, errors="replace")
                if one.returncode and "Error: Access Flags" not in one.stdout:
                    raise SystemExit(f"javap failed on JDK {jdk.major} {module}, class {name} (exit {one.returncode}):\n{one.stderr[:2000]}\n{one.stdout[-500:]}")
                outputs.append(one.stdout)
    for stdout in outputs:
        current: dict | None = None
        pending: str | None = None
        compiled_from = ""
        refused = False
        for line in stdout.splitlines():
            if line.startswith("Compiled from"):
                compiled_from, refused = line, False
                continue
            if line.startswith("Error:"):  # javap 23 refuses a generated class (java.lang.invoke.*$Holder) with a flag it does not know
                refused, current, pending = True, None, None
                print(f"javap on JDK {jdk.major} refused a class file ({compiled_from}): {line}", file=sys.stderr)
                continue
            if refused or not line.strip() or line == "}":
                continue
            if not line.startswith(" "):
                header = re.sub(r"\s*\{$", "", line)
                while (stripped := re.sub(r"<[^<>]*>", "", header)) != header:  # generics, nested ones too
                    header = stripped
                kind = re.search(r"\b(class|interface)\s+(\S+)", header)
                if kind is None:
                    raise SystemExit(f"javap on JDK {jdk.major}: a type header with no `class` or `interface` in it: {line!r} (after {compiled_from!r})")
                words = header[: kind.start()].split()
                current = found[kind.group(2)] = {
                    "public": "public" in words, "final": "final" in words, "interface": kind.group(1) == "interface",
                    "supers": [], "methods": {}, "fields": set(), "ctor": False,
                }
                for clause in re.split(r"\s+(?=extends\s|implements\s)", header[kind.end() :].strip()):
                    if clause:
                        current["supers"] += [s.strip() for s in clause.split(None, 1)[1].split(",")]
                # only an enum type extends java.lang.Enum directly (javap prints it as a class): the kind the generator reads from
                # ACC_ENUM, so that a class that becomes an enum in a later JDK (java.nio.ByteOrder in 26) is left out here too
                current["enum"] = not current["interface"] and "java.lang.Enum" in current["supers"]
            elif line.startswith("    descriptor: "):
                assert current is not None and pending is not None
                descriptor = line.split(": ", 1)[1]
                declaration, pending = pending, None
                before_parameters = declaration.split("(")[0].split()
                if descriptor.startswith("("):
                    name = before_parameters[-1] if before_parameters else "{}"
                    if "." in name or name == "static" or name == "{}":  # a constructor (named by its class) or a static block
                        current["ctor"] = current["ctor"] or ("." in name)  # -protected: javap prints only public and protected ones
                        continue
                    n = count_parameters(descriptor)
                    have = current["methods"].setdefault(name, [set(), None])
                    if "..." in declaration:
                        have[1] = n - 1 if have[1] is None else min(have[1], n - 1)
                    else:
                        have[0].add(n)
                else:
                    current["fields"].add(declaration.rstrip().split()[-1])
            elif (m := JAVAP_NAME.match(line)) is not None:
                pending = m.group("decl")
    return found


def check_javap(jdks: list[Jdk], modules: tuple[str, ...], text: str, work: Path) -> int:
    """Rebuild the table from `javap -protected -s` (the closure, the merge and the drops written again, from javap's text
    alone) and diff it with `text`, column by column. Member types are not in javap's output, so that column is not
    compared here. Prints the numbers compared and every disagreement; returns the number of disagreements."""
    legacy_packages = {p for jdk in jdks if not jdk.legacy for module in modules for p in jdk.exports[module]}
    per_jdk: list[dict[str, Row]] = []
    for jdk in jdks:
        info = javap_types(jdk, legacy_packages, work)
        accessible: dict[str, bool] = {}

        def reachable(binary: str) -> bool:
            if binary not in accessible:
                accessible[binary] = False
                entry = info.get(binary)
                outer = binary.rsplit("$", 1)[0] if "$" in binary else None
                accessible[binary] = bool(entry and entry["public"]) and (outer is None or outer not in info or reachable(outer))
            return accessible[binary]

        def canonical(binary: str) -> str | None:  # `$` is a nesting separator exactly when its prefix is itself a type
            outer, _, simple = binary.rpartition("$")
            if outer and outer in info and not simple.isdigit():
                parent = canonical(outer)
                return None if parent is None else f"{parent}.{simple}"
            return binary

        rows: dict[str, Row] = {}
        for binary, entry in info.items():
            name = canonical(binary)
            if name is None or not reachable(binary) or binary.endswith(("module-info", "package-info")):
                continue
            chain: list[str] = []
            pending = [binary]
            unreadable = False
            while pending:
                for above in info[pending.pop()]["supers"]:
                    if above == "java.lang.Object" or above in chain:
                        continue
                    if above not in info:
                        unreadable = True
                        continue
                    chain.append(above)
                    pending.append(above)
            if unreadable:
                continue
            row = Row("interface" if entry["interface"] else "enum" if entry["enum"] else "class", entry["final"], entry["ctor"])
            row.jdks.add(jdk.major)
            row.supers = {canonical(a) for a in chain if reachable(a)}
            for source in (binary, *chain):
                row.fields |= info[source]["fields"]
                for method, (exact, minimum) in info[source]["methods"].items():
                    row.add_method(method, exact, minimum)
            rows[name] = row
        per_jdk.append(rows)
    merged: dict[str, Row] = {}
    clashes: set[str] = set()
    for rows in per_jdk:
        for name, row in rows.items():
            have = merged.setdefault(name, Row(row.kind, row.final, row.ctor))
            if have.kind != row.kind:
                clashes.add(name)
            have.final = have.final and row.final
            have.ctor = have.ctor or row.ctor
            have.jdks |= row.jdks
            have.supers |= row.supers
            have.fields |= row.fields
            for method, (exact, minimum) in row.methods.items():
                have.add_method(method, exact, minimum)
    for name in clashes:  # a class in one JDK and an interface in another: left out, as the generator does
        del merged[name]
    changed = True
    while changed:
        changed = False
        for name in sorted(merged):
            if any(s not in merged for s in merged[name].supers):
                del merged[name]
                changed = True
    table: dict[str, list[str]] = {}
    for line in text.splitlines():
        if not line.startswith("#"):
            name, *columns = line.split(" | ")
            table[name] = columns
    problems: list[str] = []
    compared = {"types": 0, "method names": 0, "arities (a `k+` counts once)": 0, "fields": 0, "supertypes": 0}
    for name in sorted(table.keys() | merged.keys()):
        if name not in merged:
            problems.append(f"{name}: in the table, not rebuilt from javap")
            continue
        if name not in table:
            problems.append(f"{name}: rebuilt from javap, not in the table")
            continue
        kind, final, ctor, jdk_column, supers, _types, fields, methods = table[name]
        row = merged[name]
        compared["types"] += 1
        if (kind in ("interface", "annotation")) != (row.kind == "interface") or (kind == "enum") != (row.kind == "enum"):
            problems.append(f"{name}: kind {kind} versus javap {row.kind}")
        if final != ("final" if row.final else "open"):
            problems.append(f"{name}: {final} versus javap {'final' if row.final else 'open'}")
        if ctor != ctor_column(row):
            problems.append(f"{name}: {ctor} versus javap {ctor_column(row)}")
        if jdk_column != "jdk=" + ",".join(map(str, sorted(row.jdks))):
            problems.append(f"{name}: {jdk_column} versus javap {sorted(row.jdks)}")
        for label, mine, theirs in (
            ("supers", set(filter(None, supers[7:].split(","))), row.supers),
            ("fields", set(filter(None, fields[7:].split(","))), row.fields),
            ("methods", set(methods[8:].split()), {f"{m}/{format_arities(*row.methods[m])}" for m in row.methods}),
        ):
            if label == "methods":
                compared["method names"] += len({e.split("/")[0] for e in mine | theirs})
                compared["arities (a `k+` counts once)"] += sum(e.count(",") + 1 for e in mine | theirs)
            else:
                compared["supertypes" if label == "supers" else "fields"] += len(mine | theirs)
            if mine != theirs:
                problems.append(f"{name}: {label} only in the table: {sorted(mine - theirs)}; only from javap: {sorted(theirs - mine)}")
    print("compared (union of both sides): " + ", ".join(f"{v} {k}" for k, v in compared.items()))
    print(f"disagreements: {len(problems)}")
    for problem in problems[:60]:
        print("  " + problem)
    return len(problems)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("homes", nargs="+", help="a JDK home each (8 is read from jre/lib/rt.jar, the rest from lib/modules)")
    parser.add_argument("--modules", default=",".join(DEFAULT_MODULES), help="the modules whose exported types are in the table")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--check", action="store_true", help="read the same facts through javap and diff against the table (see check_javap)")
    parser.add_argument("--reflect", action="store_true", help="run each JDK's reflection over the table's types and report omissions (see reflect.py)")
    parser.add_argument("--against", type=Path, help="with --check or --reflect: use this table file instead of the one just built from the class files")
    args = parser.parse_args(argv)
    modules = tuple(m for m in args.modules.split(",") if m)
    if args.reflect and args.against:  # nothing to build: the table is the file
        import reflect

        return 1 if reflect.check_reflection(args.homes, args.against.read_text(encoding="utf-8")) else 0
    with tempfile.TemporaryDirectory(prefix="ripple-jdk-") as work:
        text, rows, jdks, dropped = build(args.homes, modules, Path(work))
        if args.reflect:
            import reflect

            return 1 if reflect.check_reflection(args.homes, text) else 0
        if args.check:
            return check_javap(jdks, modules, args.against.read_text(encoding='utf-8') if args.against else text, Path(work))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    print(f"{len(rows)} types written to {args.output} ({len(text)} bytes)", file=sys.stderr)
    for why, names in dropped.items():  # every one, with its reason: a type left out is a type that is unknown
        if not why.startswith("_"):
            print(f"left out, {why}: {len(set(names))}", file=sys.stderr)
            for name in sorted(set(names)):
                print(f"  {name}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
