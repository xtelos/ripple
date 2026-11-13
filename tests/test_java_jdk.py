"""The JDK member table (ripple/data/java_jdk.txt) and its loader (ripple/java_jdk.py).

The table is merged from JDK 8, 11, 17, 21 and 25, each fact in the direction its use is safe. The witnesses below were each
confirmed against the real JDKs with `javap -protected` before they were written down (the facts in the comments).
"""

from __future__ import annotations

import fnmatch
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from ripple import java_resolver
from ripple.java_jdk import Arities, JdkTable, jdk_type, parse_row, table

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "ripple" / "data" / "java_jdk.txt"


def known(name: str):
    row = jdk_type(name)
    assert row is not None, f"{name} is not in the table"
    return row


# ---- the file ------------------------------------------------------------------------------------------------------


HORIZON = 27  # the newest GA feature release the table was generated from: the next GA needs a regeneration (and this number)


def since(first: int) -> tuple[int, ...]:
    """The JDKs of the table from `first` on: a type only the later JDKs have is `jdk=` those."""
    return tuple(range(first, HORIZON + 1))


def test_header_names_the_exact_version_of_each_jdk():
    jdk_lines = [h for h in table().headers if h.startswith("jdk ")]
    # every GA feature release from 8 through the horizon, none skipped: only absence proves, and a JDK left out is a place a member hides
    assert [h.split(":")[0] for h in jdk_lines] == [f"jdk {n}" for n in range(8, HORIZON + 1)]
    for line in jdk_lines:  # a version string, not just a feature number: 1.8.0_512-b09, 21.0.13+9-LTS
        assert any(ch.isdigit() for ch in line.split(": ", 1)[1].split()[0]) and "." in line
    assert "modules: java.base java.compiler" in table().headers


def test_the_header_carries_the_row_count_and_it_is_the_files():
    count = [h for h in table().headers if h.startswith("rows: ")]
    assert count == [f"rows: {len(table())}"] and table().problem is None
    assert len(table()) == sum(1 for line in DATA.read_text(encoding="utf-8").splitlines() if line and not line.startswith("#"))


# ---- a table the loader cannot trust is refused whole: every type unknown, and the reason is kept ----------------------------

GOOD = (
    "# rows: 2\n"
    "a.W | class | final | ctor=no | jdk=11,17 | supers=a.I | types=N | fields=lock | methods=m/0,2 v/1+\n"
    "a.I | interface | open | ctor=na | jdk=11,17 | supers= | types= | fields= | methods=\n"
)


def test_a_table_that_is_whole_is_read():
    t = JdkTable(GOOD)
    assert t.problem is None and len(t) == 2 and t.get("a.W") is not None and t.names() == ["a.I", "a.W"]


@pytest.mark.parametrize(
    "text,why",
    [
        (GOOD.rsplit("a.I |", 1)[0], "2"),  # cut at a row boundary: the count says two, one is there
        (GOOD[:-30], "newline"),  # cut inside the last row
        (GOOD.replace("# rows: 2\n", ""), "no row count"),  # the count itself is missing
        (GOOD.replace("# rows: 2", "# rows: 3"), "3"),  # the count disagrees the other way
        (GOOD.replace("# rows: 2", "# rows: two"), "row count"),
        (GOOD.replace("# rows: 2\n", "# rows: 2\n# rows: 2\n"), "more than one"),
        (GOOD.replace("a.I | interface", "a.W | interface"), "twice"),  # one name twice: two lines, one row
        (GOOD.replace(" | fields= | methods=\n", "\n"), "columns"),  # a row that lost its tail, newline intact
        ("", "no row count"),
    ],
)
def test_a_table_that_is_truncated_or_whose_count_disagrees_is_refused_whole(text, why):
    t = JdkTable(text)
    assert t.problem is not None and why in t.problem and "every type" in t.problem and "unknown" in t.problem
    assert t.get("a.W") is None and t.get("a.I") is None and "a.W" not in t and len(t) == 0 and t.names() == []


def test_the_shipped_file_cut_at_a_row_boundary_or_anywhere_is_refused():
    text = DATA.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    assert JdkTable(text).problem is None
    assert JdkTable("".join(lines[:-1])).problem is not None  # the last row gone
    assert JdkTable("".join(lines[: len(lines) // 2])).problem is not None  # half of it
    assert JdkTable(text[:-1]).problem is not None  # only the final newline gone
    assert JdkTable(text[: len(text) // 3]).problem is not None  # cut in a row


def test_rows_are_sorted_unique_canonical_and_closed():
    names = [line.split(" | ", 1)[0] for line in DATA.read_text(encoding="utf-8").splitlines() if not line.startswith("#")]
    assert names == sorted(set(names))
    assert len(names) > 1500
    assert not any("$" in n for n in names), "a nested type is named java.util.Map.Entry, never java.util.Map$Entry"
    for name in names:
        row = known(name)
        assert row.kind in ("class", "interface", "enum", "annotation")
        assert "java.lang.Object" not in row.supers
        assert name not in row.supers
        missing = [s for s in row.supers if s not in table()]
        assert not missing, f"{name} has a supertype that is not in the table: {missing}"
        for arities in row.methods.values():  # normal form: no exact count at or above a varargs minimum
            assert arities.minimum is None or all(e < arities.minimum for e in arities.exact)
            assert arities.exact or arities.minimum is not None


def test_a_type_that_is_not_in_the_table_is_unknown_not_empty():
    assert jdk_type("com.example.Nope") is None
    assert jdk_type("java.util.Map$Entry") is None  # the binary name is not the canonical one
    assert jdk_type("java.util.concurrent.StructuredTaskScope") is None  # a class in JDK 21 and an interface in 25: left out


def test_the_file_is_read_lazily():
    code = (
        "import sys\n"
        "opened = []\n"
        "sys.addaudithook(lambda event, args: opened.append(str(args[0])) if event == 'open' and 'java_jdk.txt' in str(args[0]) else None)\n"
        "import ripple, ripple.cli, ripple.java_jdk as j\n"
        "assert not opened, opened\n"
        "assert j.jdk_type('java.io.Writer') is not None\n"
        "assert opened, 'the audit hook never saw the read, so it proves nothing'\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", code], cwd=ROOT, env={"PYTHONPATH": str(ROOT), "PATH": ""}, capture_output=True, text=True
    )
    assert done.returncode == 0, done.stderr


def test_the_data_file_is_in_the_package_data_of_the_wheel():
    try:
        import tomllib
    except ModuleNotFoundError:  # Python 3.10
        tomllib = pytest.importorskip("tomli")
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    patterns = config["tool"]["setuptools"]["package-data"]["ripple"]
    relative = DATA.relative_to(ROOT / "ripple").as_posix()
    assert any(fnmatch.fnmatch(relative, p) for p in patterns), f"{relative} is not matched by package-data {patterns}"


def test_a_built_wheel_holds_the_data_file(tmp_path):
    setuptools = pytest.importorskip("setuptools")
    if int(setuptools.__version__.split(".")[0]) < 64:  # pyproject.toml asks for 64: an older one reads no [project] table
        pytest.skip("setuptools older than 64 cannot build this project")
    try:
        from setuptools.command.bdist_wheel import bdist_wheel  # noqa: F401  (setuptools 70.1 and later)
    except ImportError:
        pytest.importorskip("wheel")  # an older setuptools builds wheels through this package
    import shutil

    source = tmp_path / "src"
    shutil.copytree(ROOT / "ripple", source / "ripple", ignore=shutil.ignore_patterns("__pycache__"))
    for name in ("pyproject.toml", "README.md", "LICENSE"):
        shutil.copy(ROOT / name, source / name)
    out = tmp_path / "out"
    out.mkdir()
    done = subprocess.run(
        [sys.executable, "-c", f"from setuptools import build_meta; print(build_meta.build_wheel({str(out)!r}))"],
        cwd=source, capture_output=True, text=True,
    )
    assert done.returncode == 0, done.stderr[-2000:]
    with zipfile.ZipFile(out / done.stdout.strip().splitlines()[-1]) as wheel:
        assert "ripple/data/java_jdk.txt" in wheel.namelist()


# ---- version witnesses, each confirmed with javap on the five JDKs -----------------------------------------------------


def test_a_method_removed_after_jdk_8_is_still_there():
    # Thread.destroy() and Thread.stop(Throwable) are declared by JDK 8 and by none of 11, 17, 21, 25
    thread = known("java.lang.Thread")
    assert thread.may_declare_method("destroy", 0) and not thread.may_declare_method("destroy", 1)
    assert thread.may_declare_method("stop", 1)
    assert 8 in thread.jdks


def test_a_method_and_a_supertype_added_in_jdk_21_are_there():
    # List.getFirst() exists in 21 and 25 only, and List extends SequencedCollection there (Collection in 8, 11, 17)
    list_ = known("java.util.List")
    assert list_.may_declare_method("getFirst", 0)
    assert {"java.util.SequencedCollection", "java.util.Collection", "java.lang.Iterable"} <= list_.supers
    assert list_.may_be_instance_of("java.util.SequencedCollection") and list_.may_be_instance_of("java.util.List")
    assert not list_.may_be_instance_of("java.util.Set") and not list_.may_be_instance_of("java.lang.Object")


def test_a_member_a_supertype_and_a_member_type_the_newest_jdks_added_are_there():
    # each is a fact only the JDKs added to the table after 25 (or between the five it was first made from) give; javap on that
    # JDK confirms it. A table that stopped at JDK 25 lacked them, and every one was a wrong edge or a wrong "external" waiting.
    process = known("java.lang.Process")  # 26: implements Closeable, declares close()
    assert process.may_declare_method("close", 0) and {"java.io.Closeable", "java.lang.AutoCloseable"} <= process.supers
    assert known("java.util.Comparator").may_declare_method("max", 2) and known("java.util.Comparator").may_declare_method("min", 2)  # 26
    assert known("java.math.BigInteger").may_declare_method("rootn", 1) and known("java.math.BigDecimal").may_declare_method("rootn", 2)  # 26, 27
    assert "java.security.BinaryEncodable" in known("java.security.PublicKey").supers  # 27
    assert known("java.security.BinaryEncodable").jdks == (27,)
    assert known("java.security.KeyStore").may_declare_method("getCreationInstant", 1)  # 27
    # preview-only members of the JDKs between: a preview API is in the class file, and a repo built with --enable-preview sees it
    assert known("java.lang.foreign.SymbolLookup").may_declare_method("lookup", 1) and 19 in known("java.lang.foreign.SymbolLookup").jdks
    assert "ResolvedTransform" in known("java.lang.classfile.ClassTransform").member_types
    assert known("java.lang.classfile.ClassTransform").may_declare_method("resolve", 1)


def test_a_type_that_is_not_nameable_in_a_jdk_is_not_a_member_of_that_jdks_row():
    # java.lang.foreign.PaddingLayout is package-private in JDK 19 and public from 20
    assert known("java.lang.foreign.PaddingLayout").jdks == since(20)


def test_a_type_whose_kind_changes_between_jdks_is_unknown():
    # ByteOrder: a class in 8 to 25, an enum in 26; a foreign layout's kind changes between the JDKs that have it as a preview
    for name in ("java.nio.ByteOrder", "java.util.concurrent.StructuredTaskScope", "java.lang.foreign.ValueLayout", "java.lang.foreign.FunctionDescriptor"):
        assert jdk_type(name) is None, name


@pytest.mark.parametrize("n", [1, 2, 3, 10, 200])
def test_a_varargs_method_may_take_every_count_from_its_minimum(n):
    # printf(String, Object...) and printf(Locale, String, Object...) in every JDK: "1 or more", never an exact 2 and 3
    printf = known("java.io.PrintStream").methods["printf"]
    assert printf.minimum == 1 and printf.exact == frozenset()
    assert printf.admits(n)
    assert known("java.io.PrintStream").may_declare_method("printf", n)


def test_a_varargs_method_does_not_take_fewer_arguments_than_its_fixed_ones():
    assert not known("java.io.PrintStream").may_declare_method("printf", 0)
    assert not known("java.io.PrintStream").may_declare_method("noSuchMethod", 1)


def test_exact_counts_are_exact():
    write = known("java.io.Writer").methods["write"]  # write(int), write(char[]), write(String); the 3-argument pair
    assert write.exact == frozenset({1, 3}) and write.minimum is None
    assert not write.admits(2) and not write.admits(0) and not write.admits(4)


def test_arities_parse_and_admit():
    assert Arities.parse("0,2,3+") == Arities(frozenset({0, 2}), 3)
    assert Arities.parse("1+").admits(1) and Arities.parse("1+").admits(9) and not Arities.parse("1+").admits(0)
    assert Arities.parse("0").admits(0) and not Arities.parse("0").admits(1)


def test_nested_types_have_canonical_names_and_are_inherited_by_name():
    assert "java.util.Map.Entry" in table() and "java.util.AbstractMap.SimpleEntry" in table()
    assert known("java.util.Map").member_types >= {"Entry"}
    assert known("java.util.AbstractMap").member_types >= {"SimpleEntry", "SimpleImmutableEntry", "Entry"}  # Entry: from Map
    assert known("java.util.HashMap").member_types >= {"SimpleEntry", "Entry"}
    assert known("java.util.HashMap").may_declare_member_type("SimpleEntry")
    assert not known("java.util.HashMap").may_declare_member_type("Node")  # package-private in the JDK
    assert known("java.util.AbstractMap.SimpleEntry").may_be_instance_of("java.util.Map.Entry")


def test_protected_fields_are_there_and_a_field_that_is_not_declared_is_not():
    assert known("java.io.FilterInputStream").may_declare_field("in")  # protected volatile InputStream in
    assert known("java.io.Writer").may_declare_field("lock")  # protected Object lock
    assert known("java.io.BufferedInputStream").may_declare_field("in")  # inherited from FilterInputStream
    assert not known("java.io.Writer").may_declare_field("nope")
    assert known("java.lang.Integer").may_declare_field("MAX_VALUE")  # public static final, and static ones count


def test_only_public_and_protected_members_are_in_a_row():
    string_builder = known("java.lang.StringBuilder")
    assert not string_builder.may_declare_method("ensureCapacityInternal", 1)  # package-private in AbstractStringBuilder
    assert string_builder.may_declare_method("append", 1)  # public, declared again in StringBuilder
    assert not known("java.util.HashMap").may_declare_method("newNode", 4)  # package-private
    assert known("java.lang.Object").may_declare_method("clone", 0)  # protected


def test_final_is_the_intersection_over_the_jdks_that_have_the_type():
    # TimeUnit: abstract in JDK 8, final from 11. Arrays: final only from 21. Console: final in 8, 11, 17 and not from 21.
    for name in ("java.util.concurrent.TimeUnit", "java.util.Arrays", "java.io.Console"):
        assert not known(name).final, name
    # final in every JDK that has it
    for name in ("java.lang.String", "java.lang.Integer", "java.util.Objects", "java.util.Optional", "java.util.regex.Pattern"):
        assert known(name).final, name
    assert not known("java.util.ArrayList").final and not known("java.util.List").final


def test_a_type_that_only_some_jdks_have_is_described_over_those():
    assert known("java.lang.Record").jdks == since(14)  # a preview API in 14 and 15, final from 16: preview APIs are in the table
    assert known("java.util.SequencedCollection").jdks == since(21)
    assert known("java.lang.String").jdks == since(8)
    assert known("java.util.Optional").jdks == since(8) and known("java.util.Optional").final
    assert known("java.lang.ProcessHandle").jdks == since(9)  # JDK 9 and 10 are among the JDKs the table is read from
    assert known("java.lang.Enum").member_types >= {"EnumDesc"}  # EnumDesc is JDK 12 and later: the union has it


def test_javax_crypto_is_in_jdk_8_although_it_lives_in_jce_jar_there():
    # JDK 8 keeps javax.crypto in jre/lib/jce.jar, not rt.jar: reading rt.jar alone listed these from JDK 11 and omitted what
    # JDK 8 declares. ExemptionMechanism.finalize() is protected, in JDK 8 only.
    for name in ("javax.crypto.Cipher", "javax.crypto.ExemptionMechanism", "javax.crypto.spec.SecretKeySpec", "javax.crypto.interfaces.DHKey"):
        assert known(name).jdks == since(8), name
    assert known("javax.crypto.spec.ChaCha20ParameterSpec").jdks == since(11)  # new in JDK 11
    exemption = known("javax.crypto.ExemptionMechanism")
    assert exemption.may_declare_method("finalize", 0) and not exemption.may_declare_method("finalize", 1)
    assert not known("javax.crypto.Cipher").may_declare_method("finalize", 0)  # Cipher has no finalize in any JDK


def test_ctor_says_whether_a_class_has_a_constructor_a_repo_subclass_could_call():
    # confirmed with javap -protected on the five JDKs. ArrayList: public. Writer: PROTECTED (and abstract), which counts.
    assert known("java.util.ArrayList").ctor is True
    assert known("java.io.Writer").ctor is True
    # Arrays is final from JDK 21 only (open in 8, 11, 17) and has had only a private constructor throughout
    arrays = known("java.util.Arrays")
    assert arrays.ctor is False and not arrays.final
    assert known("java.util.Objects").ctor is False  # private constructor, final
    assert known("java.lang.Math").ctor is False


def test_ctor_is_not_applicable_to_an_interface_or_an_annotation():
    assert known("java.util.List").ctor is None
    assert known("java.lang.Override").kind == "annotation" and known("java.lang.Override").ctor is None
    for name in table().names():
        row = known(name)
        assert (row.ctor is None) == (row.kind in ("interface", "annotation")), name


def test_ctor_is_the_union_over_the_jdks_that_have_the_type():
    # public constructor in JDK 8 and 11, private from 17 (Modifier); public in JDK 11 only (ToolProvider): a repo on that JDK
    # may extend it, so a later JDK's private constructor does not prove "no accessible constructor"
    assert known("java.lang.reflect.Modifier").ctor is True
    assert known("javax.tools.ToolProvider").ctor is True
    assert known("java.lang.Thread").ctor is True
    assert known("java.util.concurrent.TimeUnit").ctor is False  # an enum: private in every JDK


def test_object_members_are_in_a_row_only_when_the_type_declares_them():
    assert known("java.lang.Object").may_declare_method("hashCode", 0)
    assert not known("java.io.Serializable").may_declare_method("hashCode", 0)  # an interface does not redeclare it
    assert known("java.util.List").may_declare_method("hashCode", 0)  # List redeclares it
    assert known("java.lang.Enum").may_declare_method("toString", 0)


def test_parse_row_round_trip():
    row = parse_row("a.B | interface | final | ctor=na | jdk=11,17 | supers=a.C,a.D | types=E | fields=x,y | methods=m/0,2 v/1+")
    assert (row.name, row.kind, row.final, row.ctor, row.jdks) == ("a.B", "interface", True, None, (11, 17))
    assert row.supers == {"a.C", "a.D"} and row.member_types == {"E"} and row.fields == {"x", "y"}
    assert row.methods == {"m": Arities(frozenset({0, 2})), "v": Arities(frozenset(), 1)}
    empty = parse_row("a.F | class | open | ctor=yes | jdk=8 | supers= | types= | fields= | methods=")
    assert empty.supers == frozenset() and empty.methods == {} and not empty.final and empty.ctor is True
    assert parse_row("a.G | class | final | ctor=no | jdk=8 | supers= | types= | fields= | methods=").ctor is False
    assert JdkTable("# a header\n# rows: 1\n" + "a.F | class | open | ctor=yes | jdk=8 | supers= | types= | fields= | methods=\n").get("a.F") == empty


# ---- the hand tables of java_resolver, an independent source ------------------------------------------------------------
#
# They were read off JDK 21 by hand and reviewed twice. Where the table disagrees, the JDK settles it: these are the
# disagreements, each named with the side the class files support. A disagreement that is not listed here fails.

OUT_OF_SCOPE = {"java.beans.VetoableChangeListener"}  # java.desktop: the table is java.base and java.compiler
NOT_FINAL_IN_EVERY_JDK = {"java.util.Arrays"}  # hand: final. javap: not final in 8, 11 and 17 (its constructor is private)
NOT_A_JDK_MEMBER = {("java.lang.Enum", "values")}  # hand: an enum's `values` (the language adds it per enum); no JDK type declares it


def test_every_hand_supertype_list_is_a_subset_of_the_tables():
    for name, above in java_resolver.KNOWN_SUPERTYPES.items():
        if name in OUT_OF_SCOPE:
            continue
        assert set(above) <= known(name).supers, (name, set(above) - known(name).supers)


def test_every_hand_declared_member_arity_is_admitted_by_the_table():
    for name, members in java_resolver.DECLARED_MEMBERS.items():
        if name in OUT_OF_SCOPE:
            continue
        for member, counts in members.items():
            for n in counts:
                assert known(name).may_declare_method(member, n), (name, member, n)


def test_every_hand_known_member_is_a_member_in_the_table():
    for name, members in java_resolver.KNOWN_MEMBERS.items():
        if name in OUT_OF_SCOPE:
            continue
        for member, counts in members.items():
            if (name, member) in NOT_A_JDK_MEMBER:
                assert not known(name).may_declare_method(member)
                continue
            for n in (None,) if java_resolver.ANY[0] in counts else counts:
                assert known(name).may_declare_method(member, n), (name, member, n)


def test_the_hand_member_type_claims_equal_the_tables():
    for name in java_resolver.KNOWN_SUPERTYPES:
        if name in OUT_OF_SCOPE:
            continue
        assert known(name).member_types == java_resolver.MEMBER_TYPES.get(name, frozenset()), name


def test_every_hand_final_external_type_is_final_in_the_table_but_the_jdk_disagrees_on_arrays():
    for name in java_resolver.FINAL_EXTERNAL:
        assert (name in NOT_FINAL_IN_EVERY_JDK) != known(name).final, name
