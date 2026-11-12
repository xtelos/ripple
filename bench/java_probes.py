"""Write javac's answer beside each Java probe: bench/fixtures/java/probes/<area>/<probe>/javac.json.

A probe is a small, legal Java program (<probe>/src/...). javac decides what every call in it means, so the invoke
instructions it wrote ARE the expected answer. This script compiles each probe, reads `javap -c -p -l` of every class and
records, with the source line each came from:

  invokes   [file, line, opcode, owner, name, descriptor, method, offset] for every invoke instruction: method is the
            "class.name:descriptor" of the code that holds it and offset its bytecode offset, so the invokes of one
            method are in the order they run. A call of a synthetic `access$NNN` (how javac reaches `Outer.super.m()`
            from an inner class) is replaced by what that accessor invokes, because that is the method the source named.
  classes   for each class of the probe: every supertype it has (the JDK's included, read with javap too) and the
            methods it declares as [name, descriptor, access flags] ("methods"). This is what lets a test say that a
            call through `List` may run a probe class's `add`. The bridges javac wrote (a generic method's erased twin,
            which calls the real one) are [name, descriptor, descriptor of what the bridge calls] ("bridges"). An
            enum's values()/valueOf(), and a record's accessors, equals, hashCode and toString that the compiler wrote
            (or that look like it: a source accessor that only returns its field), run no source code and are listed
            apart ("compiler").

It needs a JDK (javac and javap on PATH, or the directory of them as the first argument) and is run by hand when a probe
is added or changed; tests/test_java_probes.py reads the result and needs no JDK.

    python bench/java_probes.py [JDK_BIN_DIR] [probe_dir ...]
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

PROBES = Path(__file__).resolve().parent / "fixtures" / "java" / "probes"
OBJECT = "java.lang.Object"


def run(tool: str, *args: str) -> str:
    done = subprocess.run([tool, *args], capture_output=True, text=True)
    if done.returncode:
        raise RuntimeError(f"{done.stdout}{done.stderr}")
    return done.stdout


def strip_generics(text: str) -> str:
    depth, kept = 0, []
    for ch in text:
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth -= 1
        elif depth == 0:
            kept.append(ch)
    return "".join(kept)


def header_supers(line: str) -> list[str]:
    """The direct supertypes a javap class header names: `public class a.B<T> extends a.C implements a.D, a.E {`."""
    line = strip_generics(line.rstrip(" {"))
    found = []
    for keyword in ("extends", "implements"):
        match = re.search(rf"\b{keyword} ([\w$.,\s]+?)(?= implements\b| extends\b|$)", line)
        if match:
            found += [part.strip() for part in match.group(1).split(",") if part.strip()]
    return found


class Javap:
    """Supertypes of classes outside the probe, by javap, remembered across probes."""

    def __init__(self, tool: str):
        self.tool, self.known = tool, {}

    def supers(self, name: str) -> list[str]:
        if name not in self.known:
            out = subprocess.run([self.tool, name], capture_output=True, text=True).stdout
            header = next((l for l in out.splitlines() if re.match(r"^[\w\s]*(class|interface|enum|record) ", l)), "")
            self.known[name] = header_supers(header)
        return self.known[name]

    def closure(self, name: str, local: dict[str, list[str]]) -> list[str]:
        seen, todo = [], list(local[name] if name in local else self.supers(name))
        todo.append(OBJECT)
        while todo:
            x = todo.pop()
            if x in seen or x == name:
                continue
            seen.append(x)
            todo += local[x] if x in local else self.supers(x)
        return sorted(seen)


def read_listing(listing: str):
    """(invokes: {(class, method, descriptor): [(offset, opcode, owner, name, descriptor)]}, lines: {(class, method,
    descriptor): [(line, offset)]}, declared: {class: [(name, descriptor)]}, headers: {class: header}, files: {class: file},
    flags: {(class, method, descriptor): the access flags}). The listing is `javap -v -p`."""
    invokes, lines, declared, headers, files = defaultdict(list), defaultdict(list), defaultdict(list), {}, {}
    flags: dict[tuple, int] = {}
    cls = fname = name = descriptor = None
    for raw in listing.splitlines():
        if raw.lstrip().startswith("Compiled from"):
            fname = re.search(r'Compiled from "(.+)"', raw).group(1)
        elif raw and not raw.startswith(" ") and re.match(r"^[\w\s]*?(class|interface|enum|record) ([\w.$]+)", raw):
            cls = re.match(r"^[\w\s]*?(?:class|interface|enum|record) ([\w.$]+)", raw).group(1)
            headers[cls] = raw
            files[cls] = (cls.rsplit(".", 1)[0].replace(".", "/") + "/" if "." in cls else "") + fname
        elif re.match(r"^  \S", raw) and ("(" in raw or raw.strip().startswith("static {}")):
            found = re.search(r"([^\s(]+)\(", raw)
            name = found.group(1) if found else "<clinit>"
            name = "<init>" if name == cls else name
            descriptor = None
        elif m := re.match(r"^    descriptor: (\(.*)", raw):
            descriptor = m.group(1)
            declared[cls].append((name, descriptor))
        elif (m := re.match(r"^    flags: \(0x([0-9a-f]+)\)", raw)) and descriptor is not None:
            flags[(cls, name, descriptor)] = int(m.group(1), 16)
        elif m := re.match(r"^\s+(\d+): (invoke(?:virtual|special|static|interface)) .*// (?:Interface)?Method (.+)$", raw):
            target = m.group(3)
            cut = target.index(":(")
            left, called_descriptor = target[:cut], target[cut + 1 :]
            if left.startswith('"['):  # an array's clone()
                owner, called = left[1:].split('".', 1)
            elif "." in left:
                owner, called = left.rsplit(".", 1)
            else:  # javap leaves the owner out when it is the class being listed
                owner, called = cls, left
            owner = owner.replace("/", ".")
            invokes[(cls, name, descriptor)].append((int(m.group(1)), m.group(2), owner, called.strip('"'), called_descriptor))
        elif m := re.match(r"^\s+line (\d+): (\d+)", raw):
            lines[(cls, name, descriptor)].append((int(m.group(1)), int(m.group(2))))
    return invokes, lines, declared, headers, files, flags


def line_at(table, offset: int):
    line = None
    for number, start in sorted(table, key=lambda row: row[1]):
        if start <= offset:
            line = number
    return line


def implicit(cls_header: str, name: str, descriptor: str, body: list[str]) -> bool:
    """A method the compiler wrote that runs no source: an enum's values()/valueOf(), a record's accessor, equals,
    hashCode or toString (an accessor is `aload_0; getfield; return`, the others call ObjectMethods)."""
    if "extends java.lang.Enum" in cls_header and name in ("values", "valueOf", "$values"):
        return True
    if "extends java.lang.Record" in cls_header:
        accessor = len(body) >= 2 and body[0] == "aload_0" and body[1].startswith("getfield") and f"Field {name}:" in body[1]
        return accessor or "invokedynamic" in " ".join(body)
    return False


def bodies(listing: str) -> dict:
    """{(class, name, descriptor): [instruction text]} for the methods that have code."""
    out, cls, name, descriptor, active = {}, None, None, None, False
    for raw in listing.splitlines():
        if raw and not raw.startswith(" ") and re.match(r"^[\w\s]*?(class|interface|enum|record) ([\w.$]+)", raw):
            cls = re.match(r"^[\w\s]*?(?:class|interface|enum|record) ([\w.$]+)", raw).group(1)
        elif re.match(r"^  \S", raw) and ("(" in raw or raw.strip().startswith("static {}")):
            found = re.search(r"([^\s(]+)\(", raw)
            name = found.group(1) if found else "<clinit>"
            name = "<init>" if name == cls else name
            active = False
        elif m := re.match(r"^    descriptor: (\(.*)", raw):
            descriptor = m.group(1)
            out[(cls, name, descriptor)] = []
        elif raw.strip() == "Code:":
            active = True
        elif active and (m := re.match(r"^\s+\d+: (\w+)", raw)):
            out[(cls, name, descriptor)].append(m.group(1) + (" " + raw.split("//", 1)[1].strip() if "//" in raw else ""))
        elif raw.strip().endswith("Table:"):
            active = False
    return out


BRIDGE = 0x0040


def answer(listing: str, jdk: Javap) -> dict:
    """javac's answer for the classes of one `javap -v -p` listing: {"invokes": [...], "classes": {...}} (see the top)."""
    invokes, lines, declared, headers, files, flags = read_listing(listing)
    code = bodies(listing)
    rows = []
    for (cls, name, descriptor), found in invokes.items():
        for offset, opcode, owner, called, called_descriptor in found:
            line = line_at(lines[(cls, name, descriptor)], offset)
            if line is None:
                continue
            where = f"{cls}.{name}:{descriptor}"
            if called.startswith("access$") and owner in headers:  # javac's way into `Outer.super.m()`
                for (c, n, d), inner in invokes.items():
                    if c == owner and n == called and d == called_descriptor:
                        rows += [[files[cls], line, o2, w2, called2, d2, where, offset] for _, o2, w2, called2, d2 in inner]
                continue
            rows.append([files[cls], line, opcode, owner, called, called_descriptor, where, offset])
    local = {cls: header_supers(header) for cls, header in headers.items()}
    info = {}
    for cls in sorted(headers):
        methods, written_by_compiler, bridges = [], [], []
        for n, d in declared[cls]:
            if n == "<clinit>" or n.startswith(("access$", "lambda$", "$values")):
                continue
            access = flags.get((cls, n, d), 0)
            if access & BRIDGE:
                calls = [dd for _, _, owner, c, dd in invokes.get((cls, n, d), []) if c == n and owner == cls]
                bridges.append([n, d, calls[0] if calls else ""])
            elif implicit(headers[cls], n, d, code.get((cls, n, d), [])):
                written_by_compiler.append([n, d, access])
            else:
                methods.append([n, d, access])
        info[cls] = {
            "supers": jdk.closure(cls, local),
            "methods": sorted(methods),
            "bridges": sorted(bridges),
            "compiler": sorted(written_by_compiler),
        }
    return {"invokes": sorted(rows, key=lambda r: (r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7])), "classes": info}


def probe(directory: Path, javac: str, javap: str, jdk: Javap) -> dict:
    sources = sorted((directory / "src").rglob("*.java"))
    with tempfile.TemporaryDirectory(prefix="java-probe-") as tmp:
        classes = Path(tmp) / "classes"
        classes.mkdir()
        run(javac, "-g", "-proc:none", "-nowarn", "--release", "21", "-d", str(classes), *map(str, sources))
        names = sorted(str(p) for p in classes.rglob("*.class"))
        listing = run(javap, "-v", "-p", *names)
    return answer(listing, jdk)


def dump(data: dict) -> str:
    out = ['{\n "invokes": [']
    out.append(",\n".join("  " + json.dumps(row) for row in data["invokes"]))
    out.append(" ],\n \"classes\": {")
    out.append(",\n".join(f"  {json.dumps(cls)}: {json.dumps(info)}" for cls, info in data["classes"].items()))
    out.append(" }\n}\n")
    return "\n".join(out)


def main(argv: list[str]) -> None:
    bin_dir = Path(argv[0]) if argv and (Path(argv[0]) / "javac").exists() else None
    rest = argv[1:] if bin_dir else argv
    javac = str(bin_dir / "javac") if bin_dir else shutil.which("javac")
    javap = str(bin_dir / "javap") if bin_dir else shutil.which("javap")
    if not (javac and javap):
        raise SystemExit("javac and javap are needed: pass the JDK's bin directory, or put them on PATH")
    directories = [Path(a).resolve() for a in rest] or sorted(p.parent for p in PROBES.rglob("src") if p.is_dir())
    jdk = Javap(javap)
    failed = 0
    for directory in directories:
        try:
            data = probe(directory, javac, javap, jdk)
        except RuntimeError as error:  # a probe must be legal Java: say which one is not
            print(f"{directory}: javac refused it:\n{error}")
            failed += 1
            continue
        (directory / "javac.json").write_text(dump(data))
        print(f"{directory.relative_to(PROBES)}: {len(data['invokes'])} invokes, {len(data['classes'])} classes")
    if failed:
        raise SystemExit(f"{failed} probes did not compile")


if __name__ == "__main__":
    main(sys.argv[1:])
