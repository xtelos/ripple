"""Hold ripple's JDK table against what each JDK's own reflection says: the check that does not share the generator's reading.

    python bench/java_jdk/generate.py --reflect JDK_HOME [JDK_HOME ...]                  rebuild the table and check it
    python bench/java_jdk/generate.py --reflect --against FILE JDK_HOME [JDK_HOME ...]   check FILE (no rebuild)

It compiles Reflect.java with each JDK's own javac and runs it with that JDK's own java, over the canonical names of the
table's rows: for each type, every public or protected method, field, member type and supertype that the JDK's reflection
says it declares or inherits, and whether it declares an accessible constructor. The table is read through ripple's loader
(ripple.java_jdk) and nothing here reads a class file or imports the generator, so a defect in the generator's reader (the
jar it forgot, the descriptor it miscounted) cannot be shared by this check.

Every fact is used in one direction (ripple/java_jdk.py), so what is counted is a way to be WRONG:
  - OMISSION: the JDK has a method (with that many arguments, or varargs with that many fixed), a field, a member type or a
    supertype that the row does not admit; or declares an accessible constructor and the row says `ctor=no` or `ctor=na`.
  - the row says `final` and the JDK's type is open.
  - the row's `jdk=` list and whether the JDK has the type as one a repo can NAME (public; a member type public or protected,
    and every type around it too) disagree, either way.
A row that admits MORE than the JDK has is not counted: that is the union over the JDKs, by design.
"""

from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ripple.java_jdk import JdkTable  # noqa: E402  (the table only: this module reads no class file)

SOURCE = HERE / "Reflect.java"


def major_of(home: Path) -> int:
    """The feature number of a JDK home, from its `release` file (`1.8.0_512` is 8)."""
    raw = re.search(r'^JAVA_VERSION="?([^"\n]*)"?$', (home / "release").read_text(), re.M)
    if raw is None:
        sys.exit(f"{home}/release has no JAVA_VERSION")
    parts = raw.group(1).split(".")
    return int(parts[1] if parts[0] == "1" else parts[0])


def compare(jdk: int, lines: list[str], table: JdkTable) -> tuple[Counter, list[str]]:
    """The facts `Reflect` printed for one JDK (its lines) against the table: (how many facts of each kind, the problems)."""
    problems: list[str] = []
    seen: Counter = Counter()
    for line in lines:
        parts = line.rstrip("\n").split("\t")
        name, what = parts[0], parts[1]
        row = table.get(name)
        if row is None:
            problems.append(f"{name}: asked about, but not in the table")
            continue
        seen[what] += 1
        if what == "ABSENT":
            if jdk in row.jdks:
                problems.append(f"{name}: the row lists jdk {jdk}, the JDK cannot load it")
        elif what == "PRESENT":
            if jdk not in row.jdks:
                problems.append(f"{name}: the JDK has it, the row's jdk list {list(row.jdks)} lacks {jdk}")
            if row.final and parts[2] != "final":
                problems.append(f"{name}: the row says final, jdk {jdk} says open")
        elif what == "C":
            if not row.ctor:
                problems.append(f"{name}: the JDK declares an accessible constructor, the row says ctor={'no' if row.ctor is False else 'na'}")
        elif what == "M":
            method, count, varargs = parts[2], int(parts[3]), parts[4] == "v"
            arities = row.methods.get(method)
            if arities is None:
                problems.append(f"{name}: method {method}/{count}{'+' if varargs else ''} is not in the row")
            elif varargs:
                if arities.minimum is None or arities.minimum > count - 1:
                    problems.append(f"{name}: varargs {method} with {count - 1} fixed, the row's minimum is {arities.minimum}")
            elif not arities.admits(count):
                problems.append(f"{name}: {method}/{count} is not admitted")
        elif what == "F":
            if parts[2] not in row.fields:
                problems.append(f"{name}: field {parts[2]} is not in the row")
        elif what == "T":
            if parts[2] not in row.member_types:
                problems.append(f"{name}: member type {parts[2]} is not in the row")
        elif what == "S":
            if parts[2] in table and parts[2] not in row.supers:  # a supertype outside the table is not a row's to list
                problems.append(f"{name}: supertype {parts[2]} is not in the row")
    return seen, problems


def run_reflection(home: Path, names: list[str], work: Path) -> list[str]:
    """Compile Reflect.java with the home's javac and run it with the home's java over `names`; its output lines."""
    major = major_of(home)
    classes = work / f"classes{major}"
    classes.mkdir()
    done = subprocess.run([str(home / "bin" / "javac"), "-nowarn", "-d", str(classes), str(SOURCE)], capture_output=True, text=True)
    if done.returncode:
        sys.exit(f"javac of JDK {major} failed:\n{done.stderr[:2000]}")
    listing = work / f"names{major}.txt"
    listing.write_text("\n".join(names) + "\n", encoding="utf-8")
    out = work / f"reflect{major}.tsv"
    with open(out, "w", encoding="utf-8") as sink:
        done = subprocess.run([str(home / "bin" / "java"), "-cp", str(classes), "Reflect", str(listing)], stdout=sink, stderr=subprocess.PIPE, text=True)
    if done.returncode:
        sys.exit(f"java of JDK {major} failed:\n{done.stderr[:2000]}")
    return out.read_text(encoding="utf-8").splitlines()


def check_reflection(homes: list[str], text: str) -> int:
    """Run the reflection of every home against the table `text`; print one summary line per JDK and the problems; return
    how many there are in all."""
    table = JdkTable(text)
    names = table.names()
    total = 0
    with tempfile.TemporaryDirectory(prefix="ripple-jdk-reflect-") as scratch:
        for home in sorted(map(Path, homes), key=major_of):
            major = major_of(home)
            lines = run_reflection(home, names, Path(scratch))
            seen, problems = compare(major, lines, table)
            present = seen["PRESENT"]
            facts = {"types": len(names), "loadable": present, "methods": seen["M"], "fields": seen["F"], "member types": seen["T"],
                     "supertypes": seen["S"], "constructors": seen["C"]}
            print(f"jdk {major}: {', '.join(f'{v} {k}' for k, v in facts.items())}; omissions and disagreements: {len(problems)}")
            for problem in problems[:40]:
                print("  " + problem)
            total += len(problems)
    return total
