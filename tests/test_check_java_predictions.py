"""What ripple PREDICTS should have run, and what it asks Maven instead of reading poms.

Where a prediction is incomplete in the generous direction a class that never ran goes unnoticed and the run passes. The
rule these tests hold: the prediction comes from the classes Maven compiled (target/test-classes, with the inheritance and
the composed annotations javac resolved) as well as the source; an EMPTY or undecidable prediction is a reason to distrust
the module, never to skip the check. Every case is a captured run of a real project (tests/fixtures/java_check).
"""

from __future__ import annotations

import shutil

import pytest
from java_check_support import PROJECTS, make_repo, meta, run_of, verdict

from ripple import java_check as jc


def text(result: dict) -> str:
    return " | ".join(result["problems"])


# ---- tests a surefire name pattern does not pick up, that ripple's own reading of the class cannot see ----------------


def test_a_concrete_class_that_inherits_its_tests_and_misses_the_patterns_is_not_passed(tmp_path):
    """CartChecks extends AbstractContract: no @Test of its own, name misses Test*/*Test/*Tests/*TestCase, never ran."""
    result = verdict(tmp_path, "not_picked_up_inherited")
    assert meta("not_picked_up_inherited")["exit_code"] == 0
    assert result["passed"] is False, result
    assert "no results for 1 test class: com.acme.shop.CartChecks" in text(result)


def test_a_test_carried_by_a_composed_annotation_that_misses_the_patterns_is_not_passed(tmp_path):
    """@FastTest is annotated @Test: the source has no literal @Test, the compiled class has the composed one."""
    result = verdict(tmp_path, "not_picked_up_meta")
    assert result["passed"] is False, result
    assert "no results for 1 test class: com.acme.shop.CartChecks" in text(result)


def test_a_class_that_extends_a_library_class_and_misses_the_patterns_is_undecided_and_named_in_a_note(tmp_path):
    """ripple cannot read a library's class: it does not know CartFailure holds tests, and `mvn test` does not run it either."""
    result = verdict(tmp_path, "undecidable_class")
    assert result["passed"] is True, result
    assert result["problems"] == []
    assert "cannot tell whether com.acme.shop.CartFailure holds tests" in " | ".join(result["notes"])
    assert "org/opentest4j/AssertionFailedError" in " | ".join(result["notes"])


def test_a_class_that_is_plainly_not_a_test_does_not_make_a_run_red(tmp_path):
    """A JDK superclass and a method that returns a value: decidable, no test, nothing to expect."""
    result = verdict(tmp_path, "helper_class")
    assert result["passed"] is True, result
    assert result["problems"] == []


def test_the_class_that_was_picked_up_and_ran_is_not_asked_about_again(tmp_path):
    result = verdict(tmp_path, "not_picked_up")  # CartChecks has its own @Test: the old guard, still there
    assert result["passed"] is False and "com.acme.shop.CartChecks" in text(result)


# ---- test source roots: Maven's, not a guess -------------------------------------------------------------------------


def test_a_test_source_directory_set_in_the_parent_pom_is_read_for_every_module(tmp_path):
    repo = make_repo(tmp_path, "testdir_parent")
    project = jc.find_project(repo)
    roots = {m.dir: m.test_roots for m in project.modules}
    assert roots["core"] == ("core/test",) and roots["web"] == ("web/test",)


def test_a_failing_class_outside_the_patterns_in_an_inherited_test_directory_is_not_passed(tmp_path):
    """CoreChecks (own @Test, failing) is in core/test, a root only the parent pom names: the whole build lied before."""
    result = verdict(tmp_path, "testdir_parent")
    assert meta("testdir_parent")["exit_code"] == 0
    assert result["passed"] is False, result
    assert "no results in module core for 1 test class: com.acme.core.CoreChecks" in text(result)


def test_a_test_source_root_added_by_build_helper_in_a_property_is_not_passed_over(tmp_path):
    """The root is `${extra.dir}`: ripple cannot read it, the compiled ExtraChecks says there is a test class there."""
    repo = make_repo(tmp_path, "testdir_helper")
    assert jc.find_project(repo).modules[0].roots_uncertain is True
    result = verdict(tmp_path / "v", "testdir_helper")
    assert result["passed"] is False, result
    assert "no results for 1 test class: com.acme.shop.ExtraChecks" in text(result)


def test_a_literal_build_helper_root_is_read(tmp_path):
    repo = make_repo(tmp_path, "testdir_helper")
    pom = repo / "pom.xml"
    pom.write_text(pom.read_text().replace("${extra.dir}", "extra-tests"))
    module = jc.find_project(repo).modules[0]
    assert module.test_roots == ("src/test/java", "extra-tests") and module.roots_uncertain is False


# ---- an empty or undecidable prediction is a reason to distrust ---------------------------------------------------------


def test_a_module_with_test_sources_and_no_compiled_classes_cannot_be_predicted_so_is_not_passed(tmp_path):
    """The compiled classes are missing (a build dir ripple does not look in): the prediction rests on the source alone."""
    result = verdict(tmp_path, "testdir_helper", classes=False)
    assert result["passed"] is False
    assert "no compiled test classes were found" in text(result)


def test_results_from_a_module_whose_prediction_is_empty_are_not_trusted(tmp_path):
    """Test sources on disk that ripple does not know about, and results from the module: the prediction is wrong."""
    repo, project, run = run_of(tmp_path, "pass", classes=False)
    shutil.rmtree(repo / "src" / "test")
    result = jc.judge(repo, project, [run], "mvn", 0.1, 300)
    assert result["passed"] is False
    assert "ripple predicted no test class in the module" in text(result)


def test_a_module_with_no_tests_anywhere_is_still_just_a_note(tmp_path):
    """No test source, no compiled test class, no result: nothing was predicted and nothing is missing."""
    repo, project, run = run_of(tmp_path, "multimod_class")
    shutil.rmtree(repo / "core" / "src" / "test")
    shutil.rmtree(repo / "core" / "target" / "test-classes")
    run.invocation = jc.plan(project, [])[0]
    result = jc.judge(repo, project, [run], "mvn", 0.1, 300)
    assert result["passed"] is True, result
    assert any(n.startswith("no test classes in") and "core" in n for n in result["notes"]), result["notes"]


# ---- surefire's default patterns decide only where the pom has not changed them ------------------------------------------


@pytest.mark.parametrize("name,matches", [("CartTest", True), ("TestCart", True), ("CartTests", True), ("CartTestCase", True), ("CartChecks", False), ("CartTestHelper", False), ("Test", True)])
def test_surefires_default_name_patterns(name, matches):
    assert jc.default_pattern(name) is matches


def test_a_pom_that_sets_includes_or_excludes_turns_the_pattern_shortcut_off(tmp_path):
    repo = make_repo(tmp_path, "undecidable_class")
    assert jc.find_project(repo).modules[0].custom_patterns is False
    pom = repo / "pom.xml"
    pom.write_text(pom.read_text().replace("<version>3.5.2</version>", "<version>3.5.2</version><configuration><includes><include>**/*Foo.java</include></includes></configuration>"))
    assert jc.find_project(repo).modules[0].custom_patterns is True


# ---- what Maven is asked for: the reactor, never the local repository -----------------------------------------------------


@pytest.mark.parametrize("name", ["am_profile", "module_dir", "samename_b", "multimod_class"])
def test_a_module_of_a_multi_module_build_is_always_built_with_the_modules_it_needs(tmp_path, name):
    data = meta(name)
    repo = make_repo(tmp_path, name)
    project = jc.find_project(repo)
    (run,) = jc.plan(project, jc.parse_wants(repo, project, jc.normalize(repo, data["request"])))
    assert "-am" in run.argv() and run.argv() == data["argv"]


def test_a_dependency_declared_in_a_profile_does_not_decide_whether_the_module_is_built_with_its_siblings(tmp_path):
    """web's dependency on core is in a profile: reading <dependencies> found none and `-pl web` used an installed core."""
    repo = make_repo(tmp_path, "am_profile")
    assert "<profiles>" in (repo / "web" / "pom.xml").read_text() and "<dependencies>" in (repo / "web" / "pom.xml").read_text()
    project = jc.find_project(repo)
    (run,) = jc.plan(project, jc.parse_wants(repo, project, [f"web/src/test/java/com/acme/web/WebTest.java"]))
    assert run.am is True


def test_a_single_module_build_has_no_pl_and_no_am(tmp_path):
    repo = make_repo(tmp_path, "pass")
    project = jc.find_project(repo)
    (run,) = jc.plan(project, jc.parse_wants(repo, project, ["src/test/java/com/acme/shop/CartTest.java"]))
    assert "-pl" not in run.argv() and "-am" not in run.argv()


def test_the_verdict_says_what_am_built(tmp_path):
    result = verdict(tmp_path, "am_profile")
    assert result["passed"] is True
    assert any("-am" in n and "local repository" in n for n in result.get("notes", [])), result.get("notes")


# ---- @TestFactory and @RepeatedTest: what names reach the report ----------------------------------------------------------


def test_a_factory_and_a_repeated_test_are_found_by_their_method_name(tmp_path):
    """surefire names the cases `dynamicTests()[1]`, `repeated()[2]`: the method name is what comes before `(`."""
    result = verdict(tmp_path, "method_factory")
    assert result["counts"]["passed"] == 3 and result["counts"]["failed"] == 1
    assert result["failures"][0].endswith("CartTest.dynamicTests()[2]: expected: <1> but was: <2>")
    assert not any("no test ran" in p for p in result["problems"])
    assert any("repeated: 2 invocations of a parameterized test" in n for n in result["notes"])


def test_a_factory_that_produced_no_test_is_not_passed(tmp_path):
    repo, project, run = run_of(tmp_path, "method_factory")
    run.cases = [c for c in run.cases if c.base != "dynamicTests"]
    result = jc.judge(repo, project, [run], "mvn", 0.1, 300)
    assert any("CartTest.dynamicTests: no test ran" in p for p in result["problems"])


# ---- what ripple must not call a problem: runners, failsafe's classes, generated code ------------------------------------


def test_a_runner_that_reports_its_tests_under_other_names_has_a_result_through_its_report(tmp_path):
    """JUnit's AllTests (gson's *SuiteTest) names every case after the generated test: the report is still of the class."""
    repo, project, run = run_of(tmp_path, "pass")
    for case in run.cases:
        if case.suite == "com.acme.shop.CartTests":
            case.classname = "com.generated.SomeGeneratedCase"
    result = jc.judge(repo, project, [run], "mvn", 0.1, 300)
    assert result["passed"] is True, result["problems"]


def test_a_report_with_no_tests_is_no_result_for_its_class(tmp_path):
    repo, project, run = run_of(tmp_path, "pass")
    run.cases = [c for c in run.cases if c.suite != "com.acme.shop.CartTests"]  # CartTests.xml held nothing
    result = jc.judge(repo, project, [run], "mvn", 0.1, 300)
    assert result["passed"] is False and "com.acme.shop.CartTests" in text(result)


def test_failsafes_classes_are_not_expected_from_mvn_test_and_are_named(tmp_path):
    assert jc.is_integration("OSGiManifestIT") and jc.is_integration("ITFoo") and jc.is_integration("FooITCase")
    assert not jc.is_integration("FooTest") and not jc.is_integration("ITTest") and not jc.is_integration("Exit")
    repo = make_repo(tmp_path, "not_picked_up")
    project = jc.find_project(repo)
    source = repo / "src" / "test" / "java" / "com" / "acme" / "shop"
    (source / "CartChecks.java").rename(source / "CartIT.java")
    (source / "CartIT.java").write_text((source / "CartIT.java").read_text().replace("CartChecks", "CartIT"))
    prediction = jc.predict(repo, project, project.modules[0], jc.ClassIndex(repo, project, []))
    assert prediction.integration == ["com.acme.shop.CartIT"] and "com.acme.shop.CartIT" not in prediction.classes


# ---- review round 2: what ripple KNOWS holds tests is a problem, what it cannot decide is a note ---------------------------


def notes(result: dict) -> str:
    return " | ".join(result.get("notes", []))


def test_a_benchmark_class_with_an_annotation_ripple_does_not_know_is_a_note_not_a_failure(tmp_path):
    """JMH's @Setup and @TearDown are on void methods and surefire never runs a benchmark: commons-lang read 'not passed' for it."""
    result = verdict(tmp_path, "jmh_benchmark")
    assert result["passed"] is True, result
    assert result["problems"] == []
    assert "cannot tell whether com.acme.shop.CartBenchmark holds tests" in notes(result)
    assert "org/openjdk/jmh/annotations/Setup" in notes(result)


def test_a_class_ripple_cannot_decide_that_was_asked_for_by_name_is_a_problem_when_it_did_not_run(tmp_path):
    repo, project, run = run_of(tmp_path, "jmh_benchmark")
    run.invocation = jc.Invocation(None, (), False, (jc.Want("the benchmark", "", "com.acme.shop.CartBenchmark"),))
    result = jc.judge(repo, project, [run], "mvn", 0.1, 300)
    assert result["passed"] is False and "CartBenchmark: no test ran" in text(result)
    run.invocation = jc.Invocation(None, (), False, (jc.Want("the module", ""), jc.Want("the benchmark", "", "com.acme.shop.CartBenchmark")))
    result = jc.judge(repo, project, [run], "mvn", 0.1, 300)
    assert result["passed"] is False and "CartBenchmark" in text(result)


def test_many_undecided_classes_are_named_in_a_few_notes_and_counted(tmp_path):
    repo, project, run = run_of(tmp_path, "jmh_benchmark")
    classes = repo / "target" / "test-classes" / "com" / "acme" / "shop"
    source = repo / "src" / "test" / "java" / "com" / "acme" / "shop"
    for i in range(8):
        name = f"Bench{i}Bench00"  # as long as CartBenchmark: every place the class file names itself is rewritten
        (classes / f"{name}.class").write_bytes((classes / "CartBenchmark.class").read_bytes().replace(b"CartBenchmark", name.encode()))
        (source / f"{name}.java").write_text(f"package com.acme.shop;\nclass {name} {{}}\n")
    result = jc.judge(repo, project, [run], "mvn", 0.1, 300)
    assert result["passed"] is True, result["problems"]
    assert len([n for n in result["notes"] if "cannot tell whether" in n]) <= jc.MAX_NAMES + 1
    assert any("more classes" in n for n in result["notes"])


def test_a_class_with_a_test_of_its_own_that_never_ran_is_still_a_problem_beside_an_undecided_one(tmp_path):
    result = verdict(tmp_path, "build_package")
    assert result["passed"] is False, result
    assert "no results for 1 test class: com.acme.target.CartChecks" in text(result)


def test_a_junit_4_runner_class_outside_the_patterns_that_never_ran_is_not_passed(tmp_path):
    """@RunWith(Suite.class) with no @Test method: the build has no vintage engine, so nothing ran it."""
    result = verdict(tmp_path, "runwith_suite")
    assert meta("runwith_suite")["exit_code"] == 0
    assert result["passed"] is False, result
    assert "no results for 1 test class: com.acme.shop.CartSuite" in text(result)


def test_tests_reported_only_under_a_nested_class_name_are_a_result_of_the_outer_class(tmp_path):
    """surefire files a nested class's cases under `Outer$Inner`: the outer class was run when its cases carry that name."""
    repo, project, run = run_of(tmp_path, "pass")
    for case in run.cases:
        if case.classname == "com.acme.shop.CartTest":
            case.classname = "com.acme.shop.CartTest$Receipts"
    result = jc.judge(repo, project, [run], "mvn", 0.1, 300)
    assert result["passed"] is True, result["problems"]


# ---- review round 2: tests inherited through the whole supertype closure ------------------------------------------------


def test_tests_inherited_through_an_interface_that_extends_the_interface_holding_them_are_expected(tmp_path):
    """CartChecks implements CartContract, which extends Contract, whose default method is the test: it fails alone and never ran."""
    result = verdict(tmp_path, "iface_chain")
    assert meta("iface_chain")["exit_code"] == 0
    assert result["passed"] is False, result
    assert "no results for 1 test class: com.acme.shop.CartChecks" in text(result)


# ---- review round 2: a class is looked up as its own module's build would -------------------------------------------------


def test_a_same_named_base_class_in_another_module_does_not_stand_in_for_the_real_one(tmp_path):
    """web has an empty AbstractContract of the same name, sorted after core's: the whole build read core's CoreChecks as no test."""
    result = verdict(tmp_path, "dupbase")
    assert meta("dupbase")["exit_code"] == 0
    assert result["passed"] is False, result
    assert "no results in module core for 1 test class: com.acme.core.CoreChecks" in text(result)


def test_classes_left_in_a_module_this_run_did_not_build_decide_nothing(tmp_path):
    """`check core` after a whole build: web's classes are still on disk, and are not core's or its dependencies'."""
    result = verdict(tmp_path, "dupbase_core")
    assert result["passed"] is False, result
    assert "no results in module core for 1 test class: com.acme.core.CoreChecks" in text(result)


def test_a_compiled_class_that_has_no_hand_written_source_is_not_evidence(tmp_path):
    """The base class's source is gone (a stale class file): CartChecks inherits from nothing ripple can rely on, so it is undecided."""
    repo, project, run = run_of(tmp_path, "not_picked_up_inherited")
    assert jc.judge(repo, project, [run], "mvn", 0.1, 300)["passed"] is False
    (repo / "src" / "test" / "java" / "com" / "acme" / "shop" / "AbstractContract.java").unlink()
    result = jc.judge(repo, project, [run], "mvn", 0.1, 300)
    assert result["passed"] is True, result["problems"]
    assert "cannot tell whether com.acme.shop.CartChecks holds tests" in notes(result)
    assert "com/acme/shop/AbstractContract" in notes(result)


# ---- review round 2: generated is decided by the absence of a source, never by a directory's name -------------------------


def test_an_undecided_class_in_a_package_named_build_is_not_taken_for_generated_code(tmp_path):
    """com.acme.build.CartFailure has a hand-written source; com.acme.target.CartChecks too (see the test above)."""
    result = verdict(tmp_path, "build_package")
    assert "cannot tell whether com.acme.build.CartFailure holds tests" in notes(result)
    assert "no hand-written source" not in notes(result)


def test_the_same_class_with_its_source_gone_is_generated_code(tmp_path):
    repo, project, run = run_of(tmp_path, "build_package")
    (repo / "src" / "test" / "java" / "com" / "acme" / "build" / "CartFailure.java").unlink()
    result = jc.judge(repo, project, [run], "mvn", 0.1, 300)
    assert "1 compiled class with no hand-written source" in notes(result) and "com.acme.build.CartFailure" in notes(result)
    assert "cannot tell whether com.acme.build.CartFailure" not in notes(result)


def test_a_class_compiled_from_generated_code_is_left_out_not_flagged(tmp_path):
    """protobuf's generated messages extend a library class: no one wrote a test there. With a source in the module it is asked about."""
    repo, project, run = run_of(tmp_path, "undecidable_class")
    first = jc.judge(repo, project, [run], "mvn", 0.1, 300)
    assert any("cannot tell whether com.acme.shop.CartFailure" in n for n in first["notes"])
    (repo / "src" / "test" / "java" / "com" / "acme" / "shop" / "CartFailure.java").unlink()
    result = jc.judge(repo, project, [run], "mvn", 0.1, 300)
    assert result["passed"] is True, result["problems"]
    assert any("1 compiled class with no hand-written source" in n and "CartFailure" in n for n in result["notes"])
    assert not any("cannot tell" in n for n in result["notes"])
