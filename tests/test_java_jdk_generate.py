"""The table generator (bench/java_jdk/generate.py): the class file reader and the merge, on class files built here.

The generator's output is checked against the real JDKs by `generate.py --check` (javap, a second reader); these tests pin the
parts that do not need a JDK: the descriptor count, a nested type's canonical name, access, and the direction of each merge.
"""

from __future__ import annotations

import importlib.util
import struct
from collections import defaultdict
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location("java_jdk_generate", Path(__file__).resolve().parent.parent / "bench" / "java_jdk" / "generate.py")
gen = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gen)


class Pool:
    def __init__(self) -> None:
        self.entries: list[bytes] = []
        self.seen: dict[tuple, int] = {}

    def add(self, key: tuple, body: bytes) -> int:
        if key not in self.seen:
            self.entries.append(body)
            self.seen[key] = len(self.entries)
        return self.seen[key]

    def utf(self, text: str) -> int:
        data = text.encode()
        return self.add(("u", text), b"\x01" + struct.pack(">H", len(data)) + data)

    def ref(self, tag: int, text: str) -> int:
        return self.add((tag, text), bytes([tag]) + struct.pack(">H", self.utf(text)))

    def klass(self, text: str) -> int:
        return self.ref(7, text)


def class_file(name, super_name="java/lang/Object", access=0x21, interfaces=(), fields=(), methods=(), inner=(), exports=None) -> bytes:
    pool = Pool()
    this, sup = pool.klass(name), pool.klass(super_name) if super_name else 0
    above = [pool.klass(i) for i in interfaces]
    body = struct.pack(">HHHH", access, this, sup, len(above)) + b"".join(struct.pack(">H", i) for i in above)
    body += struct.pack(">H", len(fields)) + b"".join(struct.pack(">HHHH", a, pool.utf(n), pool.utf(d), 0) for n, d, a in fields)
    body += struct.pack(">H", len(methods)) + b"".join(struct.pack(">HHHH", a, pool.utf(n), pool.utf(d), 0) for n, d, a in methods)
    attributes = []
    if inner:
        rows = b"".join(
            struct.pack(">HHHH", pool.klass(i), pool.klass(o) if o else 0, pool.utf(n) if n else 0, f) for i, o, n, f in inner
        )
        attributes.append(struct.pack(">HI", pool.utf("InnerClasses"), 2 + len(rows)) + struct.pack(">H", len(inner)) + rows)
    if exports is not None:
        rows = b"".join(
            struct.pack(">HHH", pool.ref(20, package), 0, len(to)) + b"".join(struct.pack(">H", pool.ref(19, t)) for t in to)
            for package, to in exports
        )
        content = struct.pack(">HHH", pool.ref(19, "m"), 0, 0) + struct.pack(">H", 0) + struct.pack(">H", len(exports)) + rows
        content += struct.pack(">HH", 0, 0)  # no opens, no uses
        content += struct.pack(">H", 0)  # no provides
        attributes.append(struct.pack(">HI", pool.utf("Module"), len(content)) + content)
    body += struct.pack(">H", len(attributes)) + b"".join(attributes)
    return b"\xca\xfe\xba\xbe\x00\x00\x00\x34" + struct.pack(">H", len(pool.entries) + 1) + b"".join(pool.entries) + body


def jdk_of(*classes: bytes) -> "gen.Jdk":
    jdk = object.__new__(gen.Jdk)
    jdk._zips, jdk._paths, jdk.major, jdk.legacy = {}, {}, 21, False
    jdk._parsed = {cf.name: cf for cf in map(gen.parse_class, classes)}
    jdk.names = set(jdk._parsed)
    return jdk


def test_parameters_are_counted_from_the_descriptor_not_the_declaration():
    assert gen.count_parameters("()V") == 0
    assert gen.count_parameters("(Ljava/util/Map;)V") == 1  # Map<K, V> has a comma in the declaration
    assert gen.count_parameters("(Ljava/lang/String;[Ljava/lang/Object;)Ljava/io/PrintStream;") == 2
    assert gen.count_parameters("([[IJD[JLjava/lang/Object;)V") == 5  # arrays, long and double take one each
    assert gen.count_parameters("(IJLjava/util/Map$Entry;)Z") == 3


def test_a_class_file_is_read_and_a_varargs_method_has_k_minus_one_or_more():
    flags = 0x0001 | 0x0080
    cf = gen.parse_class(
        class_file(
            "a/W", interfaces=("a/I",),
            fields=(("lock", "Ljava/lang/Object;", 0x0004), ("hidden", "I", 0x0002)),
            methods=(("printf", "(Ljava/lang/String;[Ljava/lang/Object;)V", flags), ("<init>", "()V", 1)),
        )
    )
    assert (cf.name, cf.super_name, cf.interfaces) == ("a/W", "java/lang/Object", ["a/I"])
    jdk = jdk_of(class_file("a/W", fields=(("lock", "Ljava/lang/Object;", 0x0004), ("hidden", "I", 0x0002)), methods=(
        ("printf", "(Ljava/lang/String;[Ljava/lang/Object;)V", flags),
        ("printf", "(Ljava/util/Locale;Ljava/lang/String;[Ljava/lang/Object;)V", flags),
        ("write", "(I)V", 1), ("write", "(Ljava/lang/String;II)V", 1), ("secret", "()V", 0), ("shy", "()V", 0x0002), ("<init>", "()V", 1),
        ("hook", "()V", 0x0004),
    )))
    methods, fields, _ = gen.own_members(jdk, "a/W")
    assert methods["printf"] == [set(), 1]  # (String, Object...) is 1 or more, and (Locale, String, Object...) is 2 or more
    assert methods["write"] == [{1, 3}, None]
    assert "secret" not in methods and "shy" not in methods and "<init>" not in methods  # package-private, private, a constructor
    assert "hook" in methods  # protected: a subclass inherits it
    assert fields == {"lock"}


def test_a_nested_type_is_named_by_its_inner_classes_entry_and_a_dollar_in_a_name_is_kept():
    outer = class_file("p/Map", access=0x601, inner=(("p/Map$Entry", "p/Map", "Entry", 0x609),))
    nested = class_file("p/Map$Entry", access=0x601, inner=(("p/Map$Entry", "p/Map", "Entry", 0x609),))
    odd = class_file("p/Money$USD", access=0x21)  # a top-level class whose legal name has a `$`
    deep = class_file("p/Map$Entry$Deep", access=0x21, inner=(("p/Map$Entry", "p/Map", "Entry", 0x609), ("p/Map$Entry$Deep", "p/Map$Entry", "Deep", 0x4)))
    anonymous = class_file("p/Map$1", access=0x20, inner=(("p/Map$1", None, None, 0),))
    local = class_file("p/Map$1Local", access=0x20, inner=(("p/Map$1Local", None, "Local", 0),))
    hidden = class_file("p/Map$Node", access=0x20, inner=(("p/Map$Node", "p/Map", "Node", 0x8),))  # package-private member
    jdk = jdk_of(outer, nested, odd, deep, anonymous, local, hidden)
    assert jdk.canonical("p/Map") == "p.Map"
    assert jdk.canonical("p/Map$Entry") == "p.Map.Entry"
    assert jdk.canonical("p/Money$USD") == "p.Money$USD"
    assert jdk.canonical("p/Map$Entry$Deep") == "p.Map.Entry.Deep"
    assert jdk.canonical("p/Map$1") is None and jdk.canonical("p/Map$1Local") is None
    assert jdk.accessible("p/Map$Entry") and jdk.accessible("p/Map$Entry$Deep")  # protected inside a public member of a public type
    assert not jdk.accessible("p/Map$Node") and not jdk.accessible("p/Map$1")
    _, _, types = gen.own_members(jdk, "p/Map")
    assert types == {"Entry"}  # Node is package-private: not inherited from another package


def test_module_info_exports_only_what_is_exported_to_everyone():
    cf = gen.parse_class(class_file("module-info", super_name=None, access=0x8000, exports=[("java/util", ()), ("jdk/internal/misc", ("java.logging",))]))
    assert cf.exports == ["java/util"]


def row(kind="class", final=False, jdks=(21,), supers=(), methods=None, fields=(), types=(), ctor=False):
    r = gen.Row(kind, final, ctor)
    r.jdks.update(jdks)
    r.supers.update(supers)
    r.fields.update(fields)
    r.types.update(types)
    for name, (exact, minimum) in (methods or {}).items():
        r.add_method(name, set(exact), minimum)
    return r


def merged(*per_jdk):
    return gen.merge(list(per_jdk), defaultdict(list))


def test_methods_supertypes_fields_and_member_types_are_unions():
    out = merged(
        {"a.T": row(jdks=(8,), supers={"a.Old"}, methods={"gone": ([0], None), "same": ([1], None)}, fields={"f8"}, types={"N8"}),
         "a.Old": row(jdks=(8,))},
        {"a.T": row(jdks=(25,), supers={"a.New"}, methods={"added": ([2], None), "same": ([3], None)}, fields={"f25"}, types={"N25"}),
         "a.New": row(jdks=(25,))},
    )
    t = out["a.T"]
    assert t.supers == {"a.Old", "a.New"} and t.fields == {"f8", "f25"} and t.types == {"N8", "N25"}
    assert t.jdks == {8, 25}
    assert gen.format_arities(*t.methods["gone"]) == "0" and gen.format_arities(*t.methods["added"]) == "2"
    assert gen.format_arities(*t.methods["same"]) == "1,3"


def test_a_varargs_minimum_absorbs_the_exact_counts_above_it_and_the_lowest_minimum_wins():
    out = merged({"a.T": row(methods={"m": ([0, 1, 4], 3)})}, {"a.T": row(methods={"m": ([2], 2)})})
    assert gen.format_arities(*out["a.T"].methods["m"]) == "0,1,2+"


def test_final_is_the_intersection_and_a_type_only_some_jdks_have_uses_those():
    out = merged(
        {"a.Both": row(final=False, jdks=(8,)), "a.Late": row(final=True, jdks=(21,))},
        {"a.Both": row(final=True, jdks=(21,)), "a.Late": row(final=True, jdks=(25,))},
    )
    assert out["a.Both"].final is False
    assert out["a.Late"].final is True and out["a.Late"].jdks == {21, 25}


def test_a_type_whose_kind_changes_and_what_stands_on_it_are_left_out():
    dropped = defaultdict(list)
    out = gen.merge(
        [
            {"a.S": row(kind="class"), "a.S.Child": row(supers={"a.S"}), "a.Ok": row()},
            {"a.S": row(kind="interface", jdks=(25,)), "a.S.Child": row(supers={"a.S"}, jdks=(25,)), "a.Ok": row(jdks=(25,))},
        ],
        dropped,
    )
    assert set(out) == {"a.Ok"}
    assert dropped["kind changed between JDKs"] == ["a.S"]
    assert len(dropped["supertype not in the table"]) == 1


def test_a_type_with_a_supertype_that_is_not_in_the_table_is_left_out_transitively():
    dropped = defaultdict(list)
    out = gen.merge([{"a.A": row(supers={"x.Unknown"}), "a.B": row(supers={"a.A"}), "a.C": row()}], dropped)
    assert set(out) == {"a.C"}
    assert len(dropped["supertype not in the table"]) == 2


def test_an_unreadable_chain_in_any_jdk_leaves_the_type_out_of_all():
    dropped = defaultdict(list)
    dropped["_unreadable_types"].append("a.T")
    out = gen.merge([{"a.T": row(jdks=(8,))}, {"a.T": row(jdks=(21,)), "a.U": row(jdks=(21,))}], dropped)
    assert set(out) == {"a.U"}


# ---- a drop must not break the proof "no repo class is an instance of a ctor=no class" ------------------------------------
# no_repo_instance (java_resolver.py) concludes from the ABSENCE of an extendable table type below a `ctor=no` class: a type the
# generator leaves out is absent too, so a dropped type below such a class would make that proof silently wrong.


def drops_below(proof_class_row, *, dropped_by="kind", unreadable=False):
    """merge() over two JDKs where `a.D`, a class below `a.Base`, is dropped (its kind changes, or its chain is unreadable)."""
    dropped = defaultdict(list)
    first = {"a.Base": proof_class_row, "a.D": row(supers={"a.Base"}, jdks=(8,))}
    second = {"a.Base": proof_class_row, "a.D": row(kind="interface" if dropped_by == "kind" else "class", supers={"a.Base"}, jdks=(25,))}
    if unreadable:
        dropped["_unreadable_types"].append("a.D")
        return gen.merge([first, second], dropped, unreadable_supers={"a.D": {"a.Base"}}), dropped
    return gen.merge([first, second], dropped), dropped


def test_a_dropped_type_below_a_non_final_ctor_no_class_stops_the_generator():
    for unreadable in (False, True):
        with pytest.raises(SystemExit) as stop:
            drops_below(row(ctor=False, final=False), unreadable=unreadable)
        assert "a.D" in str(stop.value) and "a.Base" in str(stop.value) and "ctor=no" in str(stop.value)


def test_a_dropped_type_below_a_class_that_cannot_break_the_proof_is_fine():
    out, dropped = drops_below(row(ctor=True))  # a constructor a repo class could call: no_repo_instance never concludes from it
    assert "a.D" not in out and dropped["kind changed between JDKs"] == ["a.D"]
    drops_below(row(ctor=False, final=True))  # final: nothing extends it at all
    drops_below(row(kind="interface"))  # an interface is `ctor=na`: no_repo_instance never concludes from one
    drops_below(row(ctor=True), unreadable=True)


def test_a_type_dropped_for_a_supertype_that_is_not_in_the_table_is_checked_too():
    dropped = defaultdict(list)
    with pytest.raises(SystemExit) as stop:
        gen.merge([{"a.Base": row(ctor=False), "a.D": row(supers={"a.Base", "x.Unknown"})}], dropped)
    assert "a.D" in str(stop.value) and "a.Base" in str(stop.value)


# ---- the JDK's version string: a JDK 9 to 14 `release` file has no FULL_VERSION ---------------------------------------------


def home_with(tmp_path, release, banner=None):
    home = tmp_path / "home"
    (home / "bin").mkdir(parents=True)
    (home / "release").write_text(release)
    if banner is not None:
        java = home / "bin" / "java"
        java.write_text(f"#!/bin/sh\necho '{banner}' >&2\n")
        java.chmod(0o755)
    return home


def test_the_version_is_full_version_when_the_release_file_has_it(tmp_path):
    home = home_with(tmp_path, 'JAVA_VERSION="17.0.21"\nFULL_VERSION="17.0.21+9"\nIMPLEMENTOR="Eclipse Adoptium"\n')
    assert gen.describe(home) == (17, "17.0.21+9 (Eclipse Adoptium)")


def test_the_version_comes_from_the_runtime_banner_when_the_release_file_lacks_full_version(tmp_path):
    home = home_with(tmp_path, 'JAVA_VERSION="9.0.4"\nIMPLEMENTOR="N/A"\n', banner='OpenJDK Runtime Environment (build 9.0.4+11)')
    assert gen.describe(home) == (9, "9.0.4+11 (N/A)")
    other = tmp_path / "second"
    other.mkdir()
    home = home_with(other, 'JAVA_VERSION="10.0.2"\nIMPLEMENTOR="AdoptOpenJDK"\n', banner='OpenJDK Runtime Environment AdoptOpenJDK (build 10.0.2+13)')
    assert gen.describe(home) == (10, "10.0.2+13 (AdoptOpenJDK)")


def test_a_release_file_with_no_version_to_read_is_refused(tmp_path):
    home = home_with(tmp_path, 'JAVA_VERSION="9.0.4"\nIMPLEMENTOR="N/A"\n', banner="not a java banner")
    with pytest.raises(SystemExit):
        gen.describe(home)


def test_the_output_carries_the_row_count_that_the_loader_checks():
    text = gen.render({"a.A": row(), "a.B": row()}, [], ("java.base",))
    assert "# rows: 2\n" in text
    from ripple.java_jdk import JdkTable

    assert JdkTable(text).problem is None and len(JdkTable(text)) == 2


@pytest.mark.parametrize("exact,minimum,text", [({3, 1}, None, "1,3"), (set(), 0, "0+"), ({0, 5}, 2, "0,2+")])
def test_arities_are_written_in_a_normal_form(exact, minimum, text):
    assert gen.format_arities(exact, minimum) == text


# ---- the constructor column ----------------------------------------------------------------------------------------


@pytest.mark.parametrize("access,accessible", [(0x0001, True), (0x0004, True), (0x0000, False), (0x0002, False)])
def test_a_public_or_protected_constructor_is_accessible_and_a_package_private_or_private_one_is_not(access, accessible):
    jdk = jdk_of(class_file("a/W", methods=(("<init>", "()V", access), ("hook", "()V", 0x0001))))
    assert jdk.has_accessible_constructor("a/W") is accessible


def test_a_method_is_not_a_constructor_and_a_class_without_one_has_none():
    jdk = jdk_of(class_file("a/U", methods=(("make", "()V", 0x0001),)), class_file("a/I", access=0x601, methods=(("m", "()V", 0x0401),)))
    assert not jdk.has_accessible_constructor("a/U") and not jdk.has_accessible_constructor("a/I")


def test_ctor_is_the_union_over_the_jdks_and_an_interface_is_not_applicable():
    out = merged(
        {"a.Arrays": row(jdks=(8,), ctor=False), "a.Mod": row(jdks=(8,), ctor=True), "a.I": row(kind="interface", jdks=(8,))},
        {"a.Arrays": row(jdks=(21,), final=True, ctor=False), "a.Mod": row(jdks=(21,), ctor=False), "a.I": row(kind="interface", jdks=(21,))},
    )
    assert [gen.ctor_column(out[n]) for n in ("a.Arrays", "a.Mod", "a.I")] == ["ctor=no", "ctor=yes", "ctor=na"]
    assert gen.ctor_column(row(kind="annotation")) == "ctor=na"


# ---- JDK 8 reads every jar of its boot class path ------------------------------------------------------------------------


def legacy_home(tmp_path, jars, ext=None):
    """A JDK 8 shaped directory: release, and jre/lib/<jar> (and jre/lib/ext/<jar>) holding the class files given."""
    import zipfile

    home = tmp_path / "jdk8"
    (home / "jre" / "lib" / "ext").mkdir(parents=True)
    (home / "release").write_text('JAVA_VERSION="1.8.0_512"\nFULL_VERSION="1.8.0_512-b09"\nIMPLEMENTOR="test"\n')
    for folder, held in (("", jars), ("ext/", ext or {})):
        for jar, classes in held.items():
            with zipfile.ZipFile(home / "jre" / "lib" / (folder + jar), "w") as archive:
                for name, data in classes.items():
                    archive.writestr(name + ".class", data)
    return home


def test_jdk_8_reads_jce_jar_beside_rt_jar_and_not_the_extension_jars(tmp_path):
    home = legacy_home(
        tmp_path,
        {"rt.jar": {"java/util/List": class_file("java/util/List")}, "jce.jar": {"javax/crypto/Cipher": class_file("javax/crypto/Cipher")}},
        ext={"sunjce_provider.jar": {"com/sun/crypto/provider/Own": class_file("com/sun/crypto/provider/Own")}},
    )
    jdk = gen.Jdk(str(home), ("java.base",), tmp_path)
    assert jdk.legacy and jdk.major == 8
    assert jdk.names == {"java/util/List", "javax/crypto/Cipher"}
    assert jdk.parse("javax/crypto/Cipher").name == "javax/crypto/Cipher"  # read out of jce.jar
    assert [j.name for j in jdk.boot_jars] == ["rt.jar", "jce.jar"]


def test_a_class_in_two_boot_jars_must_be_identical(tmp_path, capsys):
    same = class_file("java/util/List")
    home = legacy_home(tmp_path, {"rt.jar": {"java/util/List": same}, "jce.jar": {"java/util/List": same}})
    assert gen.Jdk(str(home), ("java.base",), tmp_path).names == {"java/util/List"}
    other = tmp_path / "other"
    other.mkdir()
    home = legacy_home(other, {"rt.jar": {"java/util/List": same}, "jce.jar": {"java/util/List": class_file("java/util/List", access=0x31)}})
    with pytest.raises(SystemExit) as stop:
        gen.Jdk(str(home), ("java.base",), tmp_path)
    assert "java/util/List" in str(stop.value) and "differ" in str(stop.value)


def test_a_jdk_8_without_rt_jar_is_refused(tmp_path):
    home = legacy_home(tmp_path, {"jce.jar": {"javax/crypto/Cipher": class_file("javax/crypto/Cipher")}})
    with pytest.raises(SystemExit):
        gen.Jdk(str(home), ("java.base",), tmp_path)


# ---- the reflection check (bench/java_jdk/reflect.py) ----------------------------------------------------------------------

REFLECT = importlib.util.spec_from_file_location("java_jdk_reflect", Path(__file__).resolve().parent.parent / "bench" / "java_jdk" / "reflect.py")
reflect = importlib.util.module_from_spec(REFLECT)
REFLECT.loader.exec_module(reflect)

from ripple.java_jdk import JdkTable  # noqa: E402

TABLE = JdkTable(
    "# rows: 2\n"
    "a.W | class | final | ctor=no | jdk=11,17 | supers=a.I | types=N | fields=lock | methods=m/0,2 v/1+\n"
    "a.I | interface | open | ctor=na | jdk=11,17 | supers= | types= | fields= | methods=\n"
)


def problems(jdk, *facts):
    return reflect.compare(jdk, ["\t".join(fact) for fact in facts], TABLE)[1]


def test_reflection_facts_the_row_admits_are_not_problems():
    assert not problems(
        11, ("a.W", "PRESENT", "final"), ("a.W", "S", "a.I"), ("a.W", "S", "java.lang.AutoCloseable"),
        ("a.W", "M", "m", "2", "-"), ("a.W", "M", "v", "4", "v"), ("a.W", "F", "lock"), ("a.W", "T", "N"), ("a.I", "PRESENT", "open"),
    )


def test_reflection_omissions_and_disagreements_are_each_a_problem():
    assert problems(11, ("a.W", "M", "gone", "0", "-")) == ["a.W: method gone/0 is not in the row"]
    assert problems(11, ("a.W", "M", "m", "1", "-")) == ["a.W: m/1 is not admitted"]
    assert problems(11, ("a.W", "M", "v", "0", "v")) == ["a.W: varargs v with -1 fixed, the row's minimum is 1"]
    assert problems(11, ("a.W", "F", "other")) == ["a.W: field other is not in the row"]
    assert problems(11, ("a.W", "T", "Other")) == ["a.W: member type Other is not in the row"]
    assert problems(11, ("a.W", "S", "a.I"), ("a.I", "S", "a.W")) == ["a.I: supertype a.W is not in the row"]
    assert problems(11, ("a.W", "S", "java.lang.AutoCloseable")) == []  # not a type of the table: not a row's to list
    assert problems(11, ("a.W", "C")) == ["a.W: the JDK declares an accessible constructor, the row says ctor=no"]
    assert problems(11, ("a.I", "C")) == ["a.I: the JDK declares an accessible constructor, the row says ctor=na"]
    assert problems(11, ("a.W", "PRESENT", "open")) == ["a.W: the row says final, jdk 11 says open"]
    assert problems(8, ("a.W", "PRESENT", "final")) == ["a.W: the JDK has it, the row's jdk list [11, 17] lacks 8"]
    assert problems(11, ("a.W", "ABSENT")) == ["a.W: the row lists jdk 11, the JDK cannot load it"]
    assert problems(8, ("a.W", "ABSENT")) == []
    assert problems(11, ("a.Z", "ABSENT")) == ["a.Z: asked about, but not in the table"]


def reflection_home():
    import os

    home = os.environ.get("JAVA_HOME")
    if not home or not (Path(home) / "bin" / "javac").exists() or not (Path(home) / "release").exists():
        pytest.skip("JAVA_HOME is not a JDK home with javac")
    return Path(home)


def test_the_reflection_run_finds_nothing_in_the_table_and_bites_on_a_copy_with_a_method_a_field_and_a_supertype_removed(tmp_path):
    home = reflection_home()
    major = reflect.major_of(home)
    text = (Path(__file__).resolve().parent.parent / "ripple" / "data" / "java_jdk.txt").read_text(encoding="utf-8")
    table = JdkTable(text)
    lines = reflect.run_reflection(home, table.names(), tmp_path)
    assert reflect.compare(major, lines, table)[1] == []

    def without(name, old, new):
        """The table with `old` replaced by `new` on the row of `name` (which must hold it)."""
        out = []
        for line in text.splitlines():
            if line.startswith(name + " | "):
                assert old in line, (name, old)
                line = line.replace(old, new, 1)
            out.append(line)
        return "\n".join(out) + "\n"

    cases = {
        "a method": (without("java.io.Writer", " flush/0", ""), "java.io.Writer: method flush/0 is not in the row"),
        "a field": (without("java.io.Writer", "fields=lock", "fields="), "java.io.Writer: field lock is not in the row"),
        "a supertype": (
            without("java.util.ArrayList", "java.util.RandomAccess,", ""),
            "java.util.ArrayList: supertype java.util.RandomAccess is not in the row",
        ),
        "a constructor": (
            without("java.io.Writer", "ctor=yes", "ctor=no"),
            "java.io.Writer: the JDK declares an accessible constructor, the row says ctor=no",
        ),
    }
    for what, (copy, expected) in cases.items():
        found = reflect.compare(major, lines, JdkTable(copy))[1]
        assert expected in found, (what, found[:3])
