"""javac as the judge of ripple's Java answer, call site by call site.

Given the compiled classes of a source tree (javac's invoke instructions with their lines and order, and each class's
supertypes, methods, access flags and bridges: bench/java_probes.py writes that as `javac.json`) and ripple's reading
of the same tree, this pairs every call site ripple saw with the invoke instruction javac wrote for it and checks the
three ways ripple can be wrong:

  call         a call or constructor edge whose target is not the method javac invoked: the invoke's owner is the target's
               class or a subtype of it, no class on the way overrides it, and the target's own descriptor is the invoke's
               (for a constructor, javac's parameters may have the outer instance, an enum's name and ordinal, or captured
               locals around the declared ones)
  override     an override edge whose target is neither javac's target nor a method that a subtype of the static receiver
               type (the invoke's owner) really runs for it: declared there with the invoked descriptor, or the one a
               javac bridge of that class calls, or inherited by that subtype from a superclass that is not under the
               owner, and not hidden by a closer declaration; a package-private method is overridden only in its package
  external     a call counted as outside the repo although the invoke's target is a method of the tree, or an outside
               method that a class of the tree overrides, or inherits an implementation of, for a subtype of the owner

A site is paired by more than its name and line: the call's name, its argument count against the descriptor's, and the
ORDER of the invokes (the bytecode's order within a method against the source's evaluation order, which is the end
position of each call: an argument is evaluated before the call it is in), so that two calls on one line cannot mask
each other. A site whose paired invoke fails the check but which passes with another unclaimed invoke of the same name on
those lines is reported as "reassigned" (the pairing, not ripple, may be at fault: a javac that writes two invokes for
one source construct, as it does for an enum constant with a body), and a site no invoke is left for as "unmatched" (code
javac dropped, such as a branch of `if (false)`).
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from ripple.java_indexer import Declarations, load_parser
from ripple.java_resolver import JavaResolver
from ripple.profile import JAVA

CALLS = ("method_invocation", "object_creation_expression", "explicit_constructor_invocation", "enum_constant")
COMMENTS = ("line_comment", "block_comment")
MEMBERS = ("method_declaration", "constructor_declaration", "compact_constructor_declaration", "field_declaration", "static_initializer", "enum_constant")
PRIMITIVES = {"I": "int", "J": "long", "Z": "boolean", "B": "byte", "C": "char", "S": "short", "F": "float", "D": "double", "V": "void"}
OBJECT = "java.lang.Object"
PUBLIC, PRIVATE, PROTECTED, STATIC, ABSTRACT = 0x0001, 0x0002, 0x0004, 0x0008, 0x0400
VIRTUAL = ("invokevirtual", "invokeinterface")
ANONYMOUS = re.compile(r"\$\d+$")


class Site:
    """One call site ripple recorded: where, what it is called, how many arguments, and ripple's answer to it."""

    def __init__(self, file, at, shown, outcome, t):
        whole = at
        while whole.parent is not None and whole.type not in CALLS:
            whole = whole.parent
        statement = whole
        while statement.parent is not None and not statement.type.endswith(("statement", "declaration", "declarator")) and statement.type != "enum_constant":
            statement = statement.parent
        self.file, self.kind, self.info = file, outcome[0], outcome[1]
        self.name = at.text.decode() if whole.type == "method_invocation" else "<init>"
        arguments = whole.child_by_field_name("arguments")
        self.nargs = len([c for c in arguments.named_children if c.type not in COMMENTS]) if arguments is not None else 0
        self.line = at.start_point[0] + 1
        self.lines = {self.line, statement.start_point[0] + 1, *range(whole.start_point[0] + 1, whole.end_point[0] + 2)}
        if statement.parent is not None and statement.parent.type in ("field_declaration", "local_variable_declaration", "constant_declaration"):
            self.lines.add(statement.parent.start_point[0] + 1)  # javac puts a declaration's initializer on the line of its modifiers
        # where javac may have put it when the statement's own lines do not hold it: anywhere in the member around
        member = statement
        while member.parent is not None and not (member.parent.type.endswith("_body") or member.parent.type == "enum_body_declarations"):
            member = member.parent
        self.member = set(range(member.start_point[0] + 1, member.end_point[0] + 2))
        self.order = (whole.end_byte, -whole.start_byte)
        self.shown = shown
        self.holder = binary(t)  # the class whose code holds the call (javac's class: Outer$1 for the anonymous class t)
        climbing, self.in_lambda = whole, False
        while climbing.parent is not None and climbing.type not in MEMBERS and not climbing.parent.type.endswith("_body"):
            self.in_lambda = self.in_lambda or climbing.type == "lambda_expression"  # javac writes a lambda's body as a method of its own
            climbing = climbing.parent
        self.anonymous = ""  # "super" or "new" for the two sites of `new T() { ... }`
        # `super(...)`, `this(...)` and an enum constant (whose body makes javac write the call of the enum's constructor
        # inside the body's own constructor) are the invokes at the head of a constructor, which javac writes for itself
        self.explicit = whole.type == "explicit_constructor_invocation" or (whole.type == "enum_constant" and whole.child_by_field_name("body") is not None)
        self.text = ""


class Sites(JavaResolver):
    """The resolver, keeping every call site it records, in the order recorded."""

    def __init__(self, *args):
        super().__init__(*args)
        self.sites: list[Site] = []

    def record(self, outcome, caller, at, shown, t):
        site = Site(t.file, at, shown, outcome, t)
        whole = at
        while whole.parent is not None and whole.type not in CALLS:
            whole = whole.parent
        anonymous = self.d.anonymous_at.get(whole.id)
        if anonymous is not None and outcome[0] == "edge":
            site.anonymous = "new" if [c for c, _ in outcome[1]] == [anonymous.fqn] else "super"
        self.sites.append(site)
        super().record(outcome, caller, at, shown, t)


def parameters(descriptor: str) -> list[str]:
    inner, found, i = descriptor[descriptor.index("(") + 1 : descriptor.index(")")], [], 0
    while i < len(inner):
        dims = 0
        while inner[i] == "[":
            dims, i = dims + 1, i + 1
        if inner[i] == "L":
            end = inner.index(";", i)
            name, i = inner[i + 1 : end].replace("/", ".").replace("$", "."), end + 1
        else:
            name, i = PRIMITIVES[inner[i]], i + 1
        found.append(name + "[]" * dims)
    return found


def same_types(mine: tuple[str, ...], theirs: list[str]) -> bool:
    """Ripple's qualified parameter types against javac's erased ones: "?" (a type variable, a package not known) and a
    local class (javac names it Outer$1Local) are not compared."""
    return len(mine) == len(theirs) and all(m == t or "?" in m or "(" in m for m, t in zip(mine, theirs))


def package_of(cls: str) -> str:
    return cls.rsplit(".", 1)[0] if "." in cls else ""


def binary(t) -> str:
    if t.outer is None:
        return t.fqn
    if t.kind in ("anon", "enumconst") or t.ctx_method is not None:
        return binary(t.outer) + "$?"  # javac numbers these its own way: matched by prefix
    return binary(t.outer) + "$" + t.simple


class Javac:
    """javac's answer for a tree: the invokes (by file) and the classes."""

    def __init__(self, data: dict):
        self.classes = data["classes"]
        for info in self.classes.values():
            info["supers"] = [self.binary_name(s) for s in info["supers"]]
        self.by_file: dict[str, list] = defaultdict(list)
        for row in data["invokes"]:
            self.by_file[row[0]].append(row)
        self._below: dict[str, set[str]] = {}
        self.subs: dict[str, set[str]] = defaultdict(set)
        for c, info in self.classes.items():
            for s in info["supers"]:
                self.subs[s].add(c)

    def binary_name(self, name: str) -> str:
        """The key of `classes` that a supertype's name stands for. javap's header writes a member class of a generic class
        as `pkg.Outer.Inner` (`class Outer<T> { class Inner {} }`, then `new Inner() {}`), where the key is `pkg.Outer$Inner`;
        a name that is no class of the tree (the JDK's, or one already a key) is left as it is."""
        if name in self.classes:
            return name
        parts = name.split(".")
        for i in range(len(parts) - 1, 0, -1):
            candidate = ".".join(parts[:i]) + "$" + "$".join(parts[i:])
            if candidate in self.classes:
                return candidate
        return name

    def supers(self, name: str) -> set[str]:
        return set(self.classes[name]["supers"]) if name in self.classes else set()

    def below(self, name: str) -> set[str]:
        """The class and every class of the tree that has it as a supertype."""
        return {name} | self.subs.get(name, set())

    def like(self, name: str) -> set[str]:
        """The classes a binary name stands for (`Outer$?` is every local and anonymous class of Outer)."""
        if "$?" in name:
            prefix = name.split("$?")[0] + "$"
            return {c for c in self.classes if c.startswith(prefix)}
        return {name} if name in self.classes else set()

    def declared(self, cls: str, name: str, descriptor: str) -> bool:
        info = self.classes.get(cls)
        return info is not None and any(m[0] == name and m[1] == descriptor for m in info["methods"] + info["bridges"])

    def concrete(self, cls: str, name: str, descriptor: str) -> bool:
        """Declared by the class with a body (or a bridge): a method that runs code, which an abstract one does not."""
        info = self.classes.get(cls)
        return info is not None and (
            any(m[0] == name and m[1] == descriptor and not m[2] & ABSTRACT for m in info["methods"])
            or any(b[0] == name and b[1] == descriptor for b in info["bridges"])
        )

    def written(self, cls: str, name: str, descriptor: str) -> bool:
        """Declared by the class itself, a bridge not counted: what overrides, or hides, a method of a supertype. A
        record's compiler-written methods count, since a source accessor that only returns its field reads like one."""
        info = self.classes.get(cls)
        return info is not None and any(m[0] == name and m[1] == descriptor for m in info["methods"] + info["compiler"])

    def flags(self, cls: str, name: str, descriptor: str):
        info = self.classes.get(cls)
        found = [m[2] for m in info["methods"] if m[0] == name and m[1] == descriptor] if info else []
        return found[0] if found else None


def row_ok_arity(site: Site, row) -> bool:
    """Whether the invoke's descriptor can be a call with the site's argument count: a constructor's may carry extra
    leading or captured parameters, and a varargs one takes any number."""
    params = parameters(row[5])
    k = len(params)
    if site.name == "<init>":
        return k >= site.nargs or (k >= 1 and params[-1].endswith("[]"))
    return k == site.nargs or (k >= 1 and params[-1].endswith("[]") and k - 1 <= site.nargs)


class Oracle:
    """javac's answer and ripple's, paired site by site and judged (see the module comment)."""

    def __init__(self, ripple: Sites, javac: Javac, declarations: Declarations | None = None, file_keys: dict[str, str] | None = None):
        """file_keys: the name javac gives a file (its package path and file name) for each file name ripple has, when ripple
        was pointed at more than the one source root."""
        self.ripple, self.javac, self.d = ripple, javac, declarations or ripple.d
        self.file_keys = file_keys or {}
        self.problems: list[str] = []
        self.tally: Counter = Counter()
        self.explained: list[str] = []
        self.reassigned: list[str] = []
        self.object_owned: list[str] = []

    # -- the targets of an edge --

    def callee(self, callee_id: str):
        """(binary class name, method name, ripple's parameter types or None, the method or None) of an edge's target."""
        m = self.d.methods.get(callee_id)
        if m is not None:
            return binary(m.owner), m.name, self.ripple.signature(m), m
        return binary(self.d.types[callee_id]), "<init>", None, None  # a class with no constructor written is its own

    def descriptors(self, classes: set[str], name: str, signature) -> set[str]:
        """The descriptors of the methods the classes declare that ripple's target may be (its name and parameter types)."""
        found = set()
        for c in classes:
            for m in self.javac.classes[c]["methods"] + self.javac.classes[c]["compiler"]:
                if m[0] == name and (signature is None or same_types(signature, parameters(m[1]))):
                    found.add(m[1])
        return found

    @staticmethod
    def constructor_params(signature: tuple[str, ...], descriptor: str) -> bool:
        got = parameters(descriptor)
        return any(same_types(signature, got[i : i + len(signature)]) for i in range(len(got) - len(signature) + 1))

    def overridden(self, cls: str, row) -> bool:
        """Whether a class between javac's owner and the class ripple chose declares the invoked method too: the one
        ripple chose is then overridden on the way and is not what the call means."""
        if "$?" in cls:
            return False
        j = self.javac
        return any(
            d != cls and cls in j.supers(d) and j.written(d, row[4], row[5])
            for d in {row[3]} | j.supers(row[3])
            if d in j.classes
        )

    # -- the three checks --

    def runs_tree_code(self, row) -> bool:
        """Whether the invoke may run a method that a class of the tree declares: the one it names (found in the owner or
        above it), or, for a virtual call, the one a subtype of the owner declares or inherits from the tree's classes."""
        _, _, opcode, owner, name, descriptor = row[:6]
        j = self.javac
        reach = {owner} | j.supers(owner)
        if opcode in VIRTUAL:
            for b in j.below(owner):
                reach |= {b} | j.supers(b)
        return any(j.concrete(c, name, descriptor) for c in reach if c in j.classes)

    def override_ok(self, target_id: str, row) -> bool:
        """Whether the method is what a subtype of the invoke's owner runs for this call (see the module comment)."""
        _, _, opcode, owner, name, descriptor = row[:6]
        if opcode not in VIRTUAL:
            return False
        j = self.javac
        cls, called, signature, method = self.callee(target_id)
        if method is None:
            return False
        wanted = self.descriptors(j.like(cls), name, signature)
        declaring = [c for c in [owner, *j.supers(owner)] if c in j.classes and j.flags(c, name, descriptor) is not None]
        package_private = bool(declaring) and all(not (j.flags(c, name, descriptor) & (PUBLIC | PROTECTED | PRIVATE)) for c in declaring)
        for b in j.below(owner):
            if b not in j.classes:
                continue
            chain = [b, *(c for c in j.supers(b) if c in j.classes)]
            for c in j.like(cls):
                if c not in chain:
                    continue
                if any(e != c and c in j.supers(e) and any(j.written(e, name, w) for w in wanted) for e in chain):
                    continue  # a closer declaration hides it for b
                if package_private and not any(package_of(c) == package_of(x) for x in declaring):
                    continue
                between = [e for e in chain if e == c or c in j.supers(e)]
                if descriptor in wanted or any(br[0] == name and br[1] == descriptor and br[2] in wanted for e in between for br in j.classes[e]["bridges"]):
                    return True
        return False

    def edge_ok(self, edge, row) -> bool:
        callee_id, kind = edge
        cls, called, signature, method = self.callee(callee_id)
        j = self.javac
        targets = j.like(cls)
        if kind == "override":
            return self.override_ok(callee_id, row)
        if kind == "constructor":
            if row[4] != "<init>" or not (row[3] in targets or row[3] == cls):
                return False
            return signature is None or self.constructor_params(signature, row[5])
        if row[4] != called or not (row[3] in targets | {cls} or cls in j.supers(row[3]) or any(c in j.supers(row[3]) for c in targets)):
            return False
        if self.overridden(cls, row):
            return False
        if signature is None:
            return True
        return row[5] in self.descriptors(targets, called, signature)

    def site_ok(self, site: Site, row) -> list[tuple[str, bool]]:
        """For each answer at the site, whether the row bears it out: (what, ok)."""
        if site.kind == "ext":
            if row[3] == OBJECT and self.runs_tree_code(row):
                # javac names Object as the owner of an Object method that the receiver's class does not declare, so the
                # bytecode no longer says which class the receiver was, and some class of the tree overrides it: the
                # call is neither proven outside nor shown to reach the tree. It is counted apart and listed.
                self.object_owned.append(f"{site.file}:{site.line} {site.shown}: ripple says `{site.info}`, javac calls {self.show(row)} and a class of the tree overrides it")
                return [("external, Object owner (receiver class unknown)", True)]
            return [("external", not self.runs_tree_code(row)) if not self.runs_tree_code(row) else ("WRONG external", False)]
        if site.kind == "edge":
            return [(kind if ok else "WRONG " + kind, ok) for (cid, kind) in site.info for ok in [self.edge_ok((cid, kind), row)]]
        return []

    # -- pairing --

    def candidates(self, site: Site, rows: dict[tuple[str, int], list], wide: bool = False) -> list:
        found = []
        for number in sorted(site.member if wide else site.lines):
            for row in rows.get((site.file, number), ()):
                if row[4] != site.name or not row_ok_arity(site, row):
                    continue
                where = row[6].split(":")[0]
                holder, method = where.rsplit(".", 1)
                # the class whose code it is; the head of a constructor that javac writes into the anonymous class of
                # `new T() { }` or an enum constant's body is that class's, one level below the class the call is in
                inside = holder in self.javac.like(site.holder) or ((site.explicit or site.anonymous == "super") and holder.startswith(site.holder.split("$?")[0] + "$"))
                if not inside or method.startswith("lambda$") != site.in_lambda:
                    continue
                holder = row[6].split(".<init>:")[0] if ".<init>:" in row[6] else ""
                if site.anonymous == "super" and not (ANONYMOUS.search(holder) and row[3] != holder):
                    continue
                if site.anonymous == "new" and not ANONYMOUS.search(row[3]):
                    continue
                found.append(row)
        return found

    def run(self) -> Oracle:
        rows: dict[tuple[str, int], list] = defaultdict(list)
        for file_rows in self.javac.by_file.values():
            for row in file_rows:
                rows[(row[0], row[1])].append(row)
        taken: set[int] = set()
        by_file: dict[str, list[Site]] = defaultdict(list)
        for s in self.ripple.sites:
            s.file = self.file_keys.get(s.file, s.file)
            by_file[s.file].append(s)
        method_index: dict[str, int] = {}
        for row in sorted((r for rs in self.javac.by_file.values() for r in rs), key=lambda r: (r[0], r[1], r[7])):
            method_index.setdefault(row[6], len(method_index))
        # the first invoke of a constructor is its `super(...)` or `this(...)`, which javac writes itself when the source
        # does not: it is no `new` of the source, so a `new` prefers any other invoke
        first = {}
        for r in (r for rs in self.javac.by_file.values() for r in rs):
            holder = r[6].split(".<init>:")[0] if ".<init>:" in r[6] else ""
            if holder and r[4] == "<init>" and (r[3] == holder or r[3] in self.javac.supers(holder)) and (r[6] not in first or r[7] < first[r[6]][7]):
                first[r[6]] = r
        first_ids = {id(r) for r in first.values()}
        for file, in_file in by_file.items():
            for site in sorted(in_file, key=lambda s: s.order):
                explicit = site.explicit or site.anonymous == "super"
                order = lambda r: (explicit != (id(r) in first_ids), method_index[r[6]], r[7])  # noqa: E731
                free = [r for r in sorted(self.candidates(site, rows), key=order) if id(r) not in taken]
                if not free:  # javac put it on a line outside the statement's own (a multi-line expression): look in the whole member
                    free = [r for r in sorted(self.candidates(site, rows, wide=True), key=order) if id(r) not in taken]
                if not free:
                    self.tally["unmatched " + site.kind] += 1
                    if site.kind != "unres":
                        said = site.info if site.kind == "ext" else [c for c, _ in site.info]
                        self.explained.append(f"{file}:{site.line} {site.shown}: {site.kind} {said} has no javac invoke left (javac dropped it, or invoked it more often than it is written)")
                    continue
                if site.kind == "unres":
                    taken.add(id(free[0]))  # ripple says nothing: the invoke it is most likely is still its own
                    self.tally["unresolved"] += 1
                    continue
                chosen, verdict = free[0], self.site_ok(site, free[0])
                if not all(ok for _, ok in verdict):
                    for other in free[1:]:
                        again = self.site_ok(site, other)
                        if all(ok for _, ok in again):
                            self.reassigned.append(f"{file}:{site.line} {site.shown}: paired with {self.show(chosen)} fails, passes with {self.show(other)}")
                            chosen, verdict = other, again
                            break
                taken.add(id(chosen))
                for what, ok in verdict:
                    self.tally[what] += 1
                    if not ok:
                        self.problems.append(f"{file}:{site.line} {site.shown}: {what} at a site where javac calls {self.show(chosen)}" + self.detail(site))
        return self

    @staticmethod
    def show(row) -> str:
        return f"{row[3]}.{row[4]}{row[5]}"

    @staticmethod
    def detail(site: Site) -> str:
        return f" (ripple: {site.info})"


def read_javac(directory: Path, name: str = "javac.json") -> Javac:
    return Javac(json.loads((directory / name).read_text()))


def other_answers(directory: Path) -> list[str]:
    """The names of the answers of other javac versions beside javac.json (`javac-8.json`): a probe whose meaning depends
    on the JDK it is built with keeps one for each, and ripple has to be right (or silent) for every one of them."""
    return sorted(p.name for p in directory.glob("javac-*.json"))


def read_ripple(src: Path) -> Sites:
    declarations = Declarations()
    parser = load_parser()
    for path in sorted(src.rglob("*.java")):
        declarations.add_file(path, src, parser)
    declarations.finish()
    return Sites(declarations, JAVA).run()


def judge(src: Path, answer: Javac) -> Oracle:
    return Oracle(read_ripple(src), answer).run()
