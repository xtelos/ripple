"""Judge ripple's Java answer on a real source tree against javac, call site by call site (run by hand, needs a JDK).

It compiles the tree with javac, reads every class with `javap -v -p`, reads the tree with ripple, and pairs each call
site ripple saw with the invoke instruction javac wrote for it by name, argument count and order (bench/java_oracle.py:
read its comment for what is pairing and what is checked). It prints, for the three ways ripple can be wrong:

  call      a call or constructor edge whose target is not the method javac invoked
  override  an override edge whose target is neither javac's target nor a method a subtype of the static receiver type
            really runs for the call (declared there, inherited, or the one a javac bridge calls)
  external  a call counted outside the repo whose javac target is a method of the tree, or an outside method that a
            class of the tree overrides or inherits an implementation of

and, apart, the sites it could not pair ("unmatched": code javac dropped, or a call written once and invoked more often)
and those it paired only on a second look ("reassigned"). Every WRONG line names the source line, what ripple said and
the invoke javac wrote. Zero WRONG lines is the bar; a line that is not a ripple fault is an explained exception and is
listed with the invoke that explains it, never skipped.

    python bench/java_corpus.py --jdk JDK_BIN --root REPO_DIR --src SRC_ROOT [--src SRC_ROOT ...]
                                [--classpath JAR:JAR] [--release 17] [--exclude PATTERN ...] [--save JAVAC.json | --load JAVAC.json]

--root is the directory ripple is pointed at (its file names are relative to it); each --src is a source root below it
(`src/main/java` of a module) that javac compiles. A tree javac cannot compile as a whole is not judged: say why in the
report and compile a module with its dependency jars on --classpath (fetched into /tmp, never into the repo).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bench import java_oracle, java_probes  # noqa: E402
from ripple.java_indexer import Declarations, load_parser  # noqa: E402
from ripple.profile import JAVA  # noqa: E402

CHUNK = 150


def compile_tree(javac: str, sources: list[Path], classpath: str, release: str, out: Path) -> None:
    arguments = [javac, "-g", "-proc:none", "-nowarn", "-implicit:none", "--release", release, "-d", str(out)]
    if classpath:
        arguments += ["-cp", classpath]
    out.mkdir(parents=True, exist_ok=True)
    listing = out / "sources.txt"
    listing.write_text("\n".join(str(p) for p in sources))
    done = subprocess.run([*arguments, f"@{listing}"], capture_output=True, text=True)
    if done.returncode:
        raise SystemExit(f"javac could not compile the tree (a corpus must compile whole, with --classpath for its dependencies):\n{done.stderr[-3000:]}")


def listing_of(javap: str, classes: Path) -> str:
    names = sorted(str(p) for p in classes.rglob("*.class"))
    return "".join(java_probes.run(javap, "-v", "-p", *names[i : i + CHUNK]) for i in range(0, len(names), CHUNK))


def read_tree(root: Path, sources: list[Path], excluded: list[str]):
    """(ripple's Sites over the .java files javac compiled that lie below root, {ripple's file name: the name javac gives
    the file}, how many files). A source root outside root (a stub that stands in for generated code) is not read."""
    declarations, parser = Declarations(), load_parser()
    below = [src for src in sources if src.resolve().is_relative_to(root.resolve())] or [root]
    files = sorted({p for src in below for p in src.rglob("*.java") if not any(x in p.relative_to(root).as_posix() for x in excluded)})
    for path in files:
        declarations.add_file(path, root, parser)
    declarations.finish()
    keys = {info.rel: (info.pkg.replace(".", "/") + "/" if info.pkg else "") + Path(info.rel).name for info in declarations.files}
    return java_oracle.Sites(declarations, JAVA).run(), keys, len(files)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--jdk", required=True, help="the JDK's bin directory")
    ap.add_argument("--root", required=True, type=Path)
    ap.add_argument("--src", action="append", default=[], type=Path)
    ap.add_argument("--classpath", default="")
    ap.add_argument(
        "--stub", action="append", default=[], type=Path,
        help="a source root that stands in for code the tree needs and does not have (generated sources, an annotation jar): compiled apart, so its classes are outside the tree, as a library's are",
    )  # fmt: skip
    ap.add_argument("--release", default="17")
    ap.add_argument("--exclude", action="append", default=[], help="a path fragment: a file with it is not compiled or read (module-info.java, java9/)")
    ap.add_argument("--save", type=Path, help="write javac's answer here")
    ap.add_argument("--load", type=Path, help="read javac's answer from here instead of compiling")
    ap.add_argument("--show", type=int, default=40, help="how many lines of each list to print")
    args = ap.parse_args(argv)
    jdk = Path(args.jdk)
    if args.load:
        data = json.loads(args.load.read_text())
    else:
        sources = [p for src in args.src for p in sorted(src.rglob("*.java")) if not any(x in p.as_posix() for x in args.exclude)]
        with tempfile.TemporaryDirectory(prefix="java-corpus-") as tmp:
            classes, stubs = Path(tmp) / "classes", Path(tmp) / "stubs"
            classpath = args.classpath
            if args.stub:
                compile_tree(str(jdk / "javac"), [p for stub in args.stub for p in sorted(stub.rglob("*.java"))], args.classpath, args.release, stubs)
                classpath = f"{stubs}:{classpath}" if classpath else str(stubs)
            compile_tree(str(jdk / "javac"), sources, classpath, args.release, classes)
            data = java_probes.answer(listing_of(str(jdk / "javap"), classes), java_probes.Javap(str(jdk / "javap")))
        if args.save:
            args.save.write_text(java_probes.dump(data))
    ripple, keys, count = read_tree(args.root, args.src, args.exclude)
    oracle = java_oracle.Oracle(ripple, java_oracle.Javac(data), file_keys=keys).run()
    print(f"{count} source files read, {len(data['classes'])} classes compiled, {len(data['invokes'])} invoke instructions, {len(ripple.sites)} call sites")
    print("answered:", dict(sorted(oracle.tally.items())))
    wrong = {k: v for k, v in oracle.tally.items() if k.startswith("WRONG")}
    print(f"WRONG: {sum(wrong.values())} {wrong}; reassigned: {len(oracle.reassigned)}; unmatched edge or external sites: {len(oracle.explained)}")
    for title, lines in (("WRONG", oracle.problems), ("REASSIGNED", oracle.reassigned), ("UNMATCHED", oracle.explained), ("OBJECT-OWNED", oracle.object_owned)):
        for line in lines[: args.show]:
            print(f"  {title} {line}")
        if len(lines) > args.show:
            print(f"  ... {len(lines) - args.show} more {title}")
    return 1 if oracle.problems else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
