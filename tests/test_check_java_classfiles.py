"""The class-file reader that decides which compiled classes are tests (no JDK: the classes are captured from real javac runs)."""

from __future__ import annotations

from pathlib import Path

import pytest
from java_check_support import CAPTURES

from ripple import java_check as jc


def read(capture: str, relative: str) -> jc.ClassInfo:
    path = CAPTURES / capture / "classes" / relative
    info = jc.read_class(path.read_bytes())
    assert info is not None
    return info


def test_an_abstract_class_with_a_test_method_is_read():
    info = read("not_picked_up_inherited", "_/test-classes/com/acme/shop/AbstractContract.class")
    assert info.name == "com/acme/shop/AbstractContract" and info.abstract and info.superclass == "java/lang/Object"
    assert [(m.name, m.void) for m in info.methods if "org/junit/jupiter/api/Test" in m.annotations] == [("wouldFail", True)]


def test_a_class_that_only_extends_is_read_with_its_superclass_and_no_methods_of_its_own():
    info = read("not_picked_up_inherited", "_/test-classes/com/acme/shop/CartChecks.class")
    assert info.superclass == "com/acme/shop/AbstractContract" and not info.abstract
    assert [m.name for m in info.methods if m.name != "<init>"] == []


def test_a_composed_annotation_is_an_annotation_type_carrying_test():
    info = read("not_picked_up_meta", "_/test-classes/com/acme/shop/FastTest.class")
    assert info.annotation_type and "org/junit/jupiter/api/Test" in info.annotations
    user = read("not_picked_up_meta", "_/test-classes/com/acme/shop/CartChecks.class")
    assert any("com/acme/shop/FastTest" in m.annotations for m in user.methods)


def test_an_external_superclass_is_named():
    info = read("undecidable_class", "_/test-classes/com/acme/shop/CartFailure.class")
    assert info.superclass == "org/opentest4j/AssertionFailedError"


def test_a_nested_class_has_a_dollar_name_and_the_outer_class_is_plain():
    outer = read("pass", "_/test-classes/com/acme/shop/CartTest.class")
    nested = read("pass", "_/test-classes/com/acme/shop/CartTest$Receipts.class")
    assert "$" not in outer.name and nested.name == "com/acme/shop/CartTest$Receipts"
    assert any("org/junit/jupiter/api/Nested" in nested.annotations for _ in [0])


@pytest.mark.parametrize("data", [b"", b"\xca\xfe\xba\xbe", b"not a class", b"\xca\xfe\xba\xbe\x00\x00\x00\x41\xff\xff", bytes(range(256))])
def test_a_file_that_is_not_a_class_file_is_not_a_class_not_an_error(data):
    assert jc.read_class(data) is None


def test_a_class_file_cut_off_anywhere_is_not_a_class_not_an_error():
    path = CAPTURES / "pass" / "classes" / "_" / "test-classes" / "com" / "acme" / "shop" / "CartTest.class"
    whole = path.read_bytes()
    for size in range(0, len(whole), 37):
        jc.read_class(whole[:size])  # never raises


def decide_for(
    name, superclass="java/lang/Object", methods=(), abstract=False, annotation_type=False, annotations=frozenset(), index=None, patterns=False, interfaces=()
):
    info = jc.ClassInfo(name, superclass, abstract, annotation_type, frozenset(annotations), tuple(methods), tuple(interfaces))
    return jc.decide(info, index or {}, custom_patterns=patterns)


def method(name, annotations=(), void=True):
    return jc.MethodInfo(name, frozenset(annotations), void)


def test_a_class_with_a_junit_method_is_a_test():
    assert decide_for("a/FooChecks", methods=[method("t", ["org/junit/jupiter/api/Test"])]).kind == "test"
    assert decide_for("a/FooChecks", methods=[method("t", ["org/junit/Test"])]).kind == "test"


def test_a_class_inherits_test_ness_through_the_repos_own_classes():
    base = jc.ClassInfo("a/Base", "java/lang/Object", True, False, frozenset(), (method("t", ["org/junit/jupiter/api/Test"]),), ())
    mid = jc.ClassInfo("a/Mid", "a/Base", True, False, frozenset(), (), ())
    index = {"a/Base": base, "a/Mid": mid}
    assert decide_for("a/FooChecks", superclass="a/Mid", index=index).kind == "test"


def test_a_test_through_an_interface_default_method_is_a_test():
    iface = jc.ClassInfo("a/Contract", "java/lang/Object", True, False, frozenset(), (method("t", ["org/junit/jupiter/api/Test"]),), ())
    info = jc.ClassInfo("a/FooChecks", "java/lang/Object", False, False, frozenset(), (), ("a/Contract",))
    assert jc.decide(info, {"a/Contract": iface}, custom_patterns=False).kind == "test"


def test_a_composed_annotation_is_followed_and_an_unknown_one_on_a_void_method_is_not_decidable():
    fast = jc.ClassInfo("a/Fast", "java/lang/annotation/Annotation", True, True, frozenset(["org/junit/jupiter/api/Test"]), (), ())
    assert decide_for("a/FooChecks", methods=[method("t", ["a/Fast"])], index={"a/Fast": fast}).kind == "test"
    unknown = decide_for("a/FooChecks", methods=[method("t", ["com/lib/Scenario"])])
    assert unknown.kind == "unknown" and "com/lib/Scenario" in unknown.why


def test_an_unknown_annotation_on_a_method_that_returns_a_value_is_not_a_reason_to_doubt():
    assert decide_for("a/FooConfig", methods=[method("bean", ["org/springframework/context/annotation/Bean"], void=False)]).kind == "no"


def test_an_external_superclass_is_not_decidable_and_a_jdk_one_is_not_a_test():
    assert decide_for("a/FooChecks", superclass="com/lib/BaseChecks").kind == "unknown"
    assert decide_for("a/FooChecks", superclass="java/util/ArrayList").kind == "no"
    assert decide_for("a/FooChecks", superclass="junit/framework/TestCase").kind == "test"


def test_a_class_a_surefire_pattern_names_was_looked_at_by_surefire_unless_the_pom_changed_the_patterns():
    assert decide_for("a/FooTest", superclass="com/lib/BaseChecks").kind == "scanned"
    assert decide_for("a/FooTest", superclass="com/lib/BaseChecks", patterns=True).kind == "unknown"


def test_abstract_interface_and_annotation_types_and_nested_classes_are_not_asked_about():
    assert decide_for("a/Foo", abstract=True, methods=[method("t", ["org/junit/jupiter/api/Test"])]).kind == "skip"
    assert decide_for("a/Foo", annotation_type=True).kind == "skip"
    assert decide_for("a/Foo$Inner", methods=[method("t", ["org/junit/jupiter/api/Test"])]).kind == "skip"


def test_the_index_of_a_repos_compiled_classes_reads_every_class_dir(tmp_path):
    root = tmp_path
    for kind in ("classes", "test-classes"):
        target = root / "m" / "target" / kind / "p"
        target.mkdir(parents=True)
        source = CAPTURES / "pass" / "classes" / "_" / "test-classes" / "com" / "acme" / "shop" / "CartTests.class"
        (target / f"X{kind[0]}.class").write_bytes(source.read_bytes())
    (root / "m" / "target" / "test-classes" / "p" / "bad.class").write_bytes(b"junk")
    found = jc.compiled_classes(root)
    assert sorted(Path(c.relative).name for c in found) == ["Xc.class", "Xt.class", "bad.class"]
    assert {c.kind for c in found} == {"classes", "test-classes"}


# ---- review round 2: what ripple KNOWS holds tests ----------------------------------------------------------------------

JUPITER_TEST = "org/junit/jupiter/api/Test"
TESTABLE = "org/junit/platform/commons/annotation/Testable"


@pytest.mark.parametrize(
    "annotation",
    [
        "org/junit/Test",
        "org/junit/experimental/theories/Theory",
        "org/junit/jupiter/api/Test",
        "org/junit/jupiter/api/RepeatedTest",
        "org/junit/jupiter/api/TestFactory",
        "org/junit/jupiter/api/TestTemplate",
        "org/junit/jupiter/params/ParameterizedTest",
        "org/testng/annotations/Test",
        TESTABLE,
    ],
)
def test_each_annotation_of_the_known_set_on_a_method_makes_a_test(annotation):
    assert annotation in jc.MARKERS
    assert decide_for("a/FooChecks", methods=[method("t", [annotation])]).kind == "test"


def test_a_repo_annotation_meta_annotated_testable_makes_a_test_through_any_depth_of_composition():
    """jqwik's @Property, a project's own @Scenario: every JUnit Platform test annotation carries @Testable."""
    scenario = jc.ClassInfo("a/Scenario", "java/lang/annotation/Annotation", True, True, frozenset([TESTABLE]), (), ())
    quick = jc.ClassInfo("a/Quick", "java/lang/annotation/Annotation", True, True, frozenset(["a/Scenario"]), (), ())
    index = {"a/Scenario": scenario, "a/Quick": quick}
    assert decide_for("a/FooChecks", methods=[method("t", ["a/Quick"])], index=index).kind == "test"
    assert decide_for("a/FooChecks", methods=[method("t", ["a/Scenario"])], index=index).kind == "test"


def test_a_library_annotation_whose_class_file_is_not_in_the_repo_is_unknown_even_when_it_would_be_testable():
    result = decide_for("a/FooChecks", methods=[method("t", ["net/jqwik/api/Property"])])
    assert result.kind == "unknown" and "net/jqwik/api/Property" in result.why


@pytest.mark.parametrize("annotation", ["org/junit/runner/RunWith", "org/testng/annotations/Test"])
def test_a_class_level_marker_makes_a_test_without_any_test_method(annotation):
    assert decide_for("a/FooChecks", annotations=[annotation]).kind == "test"


def test_junit_3_is_known_through_testcase_at_any_depth_of_the_superclasses():
    base = jc.ClassInfo("a/Base", "junit/framework/TestCase", True, False, frozenset(), (), ())
    assert decide_for("a/FooChecks", superclass="a/Base", index={"a/Base": base}).kind == "test"


def test_a_helper_class_with_an_unknown_annotation_is_undecided_and_the_reason_names_it():
    result = decide_for("a/Bench", methods=[method("setup", ["org/openjdk/jmh/annotations/Setup"]), method("t", ["org/openjdk/jmh/annotations/Benchmark"], void=False)])
    assert result.kind == "unknown" and "Bench.setup carries org/openjdk/jmh/annotations/Setup" in result.why


def test_a_known_test_wins_over_an_unknown_annotation_beside_it():
    result = decide_for("a/FooChecks", methods=[method("a", ["org/openjdk/jmh/annotations/Setup"]), method("t", [JUPITER_TEST])])
    assert result.kind == "test"


# ---- review round 2: the whole supertype closure ------------------------------------------------------------------------


def interface(name, extends=(), methods=(), annotations=()):
    return jc.ClassInfo(name, "java/lang/Object", True, False, frozenset(annotations), tuple(methods), tuple(extends))


def test_a_test_inherited_through_interfaces_that_extend_interfaces_is_a_test():
    index = {
        "a/Contract": interface("a/Contract", methods=[method("t", [JUPITER_TEST])]),
        "a/Typed": interface("a/Typed", extends=["a/Contract"]),
        "a/Deep": interface("a/Deep", extends=["a/Typed"]),
    }
    assert decide_for("a/FooChecks", interfaces=["a/Deep"], index=index).kind == "test"


def test_a_test_inherited_through_the_interface_of_an_interface_of_a_superclass_is_a_test():
    index = {
        "a/Contract": interface("a/Contract", methods=[method("t", [JUPITER_TEST])]),
        "a/Typed": interface("a/Typed", extends=["a/Contract"]),
        "a/Base": jc.ClassInfo("a/Base", "java/lang/Object", True, False, frozenset(), (), ("a/Typed",)),
        "a/Mid": jc.ClassInfo("a/Mid", "a/Base", True, False, frozenset(), (), ()),
    }
    assert decide_for("a/FooChecks", superclass="a/Mid", index=index).kind == "test"


def test_cycles_in_the_supertypes_end():
    index = {
        "a/I1": interface("a/I1", extends=["a/I2"]),
        "a/I2": interface("a/I2", extends=["a/I1", "a/I2"]),
        "a/B": jc.ClassInfo("a/B", "a/C", True, False, frozenset(), (), ()),
        "a/C": jc.ClassInfo("a/C", "a/B", True, False, frozenset(), (), ("a/I1",)),
    }
    assert decide_for("a/FooChecks", interfaces=["a/I1"], index=index).kind == "no"
    assert decide_for("a/FooChecks", superclass="a/B", index=index).kind == "no"


def test_a_closure_that_leaves_the_repo_is_undecided_and_names_where():
    index = {"a/Typed": interface("a/Typed", extends=["com/lib/Contract"])}
    result = decide_for("a/FooChecks", interfaces=["a/Typed"], index=index)
    assert result.kind == "unknown" and "com/lib/Contract" in result.why and "outside the repo" in result.why


def test_what_is_proven_in_the_part_ripple_read_decides_even_when_the_closure_leaves_the_repo():
    index = {
        "a/Typed": interface("a/Typed", extends=["com/lib/Contract"], methods=[method("t", [JUPITER_TEST])]),
    }
    assert decide_for("a/FooChecks", interfaces=["a/Typed"], index=index).kind == "test"


def test_jdk_interfaces_are_no_reason_to_doubt():
    assert decide_for("a/FooChecks", interfaces=["java/io/Serializable", "java/lang/Comparable"]).kind == "no"
