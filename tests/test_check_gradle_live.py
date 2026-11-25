"""`check` for Java on Gradle end to end, with a real JDK and Gradle: skipped, with the reason, when they are not there.

The tools are looked for in JAVA_HOME and on PATH, then in the portable copies under /tmp the measurements used. The build
runs offline against a Gradle user home (RIPPLE_TEST_GRADLE_HOME, else /tmp/gradle-toolchain/home) that must already hold
JUnit 5.11.3 (a build that resolved it once with network, `repositories { mavenCentral() }`); nothing is written to the home
directory (GRADLE_USER_HOME is set for every run) and no daemon is started (the runner always passes --no-daemon).

KNOWN TO PASS ON: Gradle 9.9.1 with JDK 21.0.13. Nothing here has run on another Gradle: the listener API and the way
Gradle ends a suite when the test JVM stops were read from 9.9.1.

RIPPLE_REQUIRE_LIVE_GRADLE=1 turns the skip into a failure, for a job that is meant to run these: the CI jobs that install the
java extra set it, after ci/warm-gradle.sh installed the Gradles and the homes (see .github/workflows/ci.yml). The one test that
points a wrapper at a distribution takes the zip from RIPPLE_TEST_GRADLE_DIST, else gradle-*-bin.zip under /tmp/gradle-toolchain/dl.

What these pin, each against real Gradle: a test JVM that stops (every shape measured) is never a pass; the suites a real
project is made of (parameterized, nested, inherited, dynamic, templated, display-named, disabled and assumed tests, a base
class beside several parameterized methods) are a pass, because Gradle's listener and not ripple's reading of the source
says what ran; a `buildSrc` or an included build is not the repo's tests.
"""

from __future__ import annotations

import glob
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest
from gradle_check_support import (
    BUILD,
    CART,
    COMMONS,
    DYN,
    EXIT_SHAPES,
    JUNIT,
    find_gradle,
    find_java,
    multi,
    project,
    shop,
    write,
)

from ripple import checker, gradle_check
from ripple.profile import JAVA

JUNIT_DEPS = JUNIT


GRADLE_HOME = os.environ.get("RIPPLE_TEST_GRADLE_HOME", "/tmp/gradle-toolchain/home")
JAVA_HOME = find_java()
GRADLE = find_gradle()
if JAVA_HOME is None:
    SKIP = "no JDK (set JAVA_HOME)"
elif GRADLE is None:
    SKIP = "no Gradle (put gradle on PATH)"
elif not (Path(GRADLE_HOME) / "caches" / "modules-2" / "files-2.1" / "org.junit.jupiter" / "junit-jupiter" / "5.11.3").is_dir():
    SKIP = f"no Gradle cache with JUnit 5.11.3 at {GRADLE_HOME} (set RIPPLE_TEST_GRADLE_HOME)"
else:
    SKIP = None

REQUIRED = os.environ.get("RIPPLE_REQUIRE_LIVE_GRADLE") == "1"
pytestmark = pytest.mark.skipif(SKIP is not None and not REQUIRED, reason=SKIP or "")


@pytest.fixture(autouse=True)
def tools(monkeypatch):
    if SKIP is not None:  # only reached when REQUIRED: without it the mark above skipped the test
        pytest.fail(f"RIPPLE_REQUIRE_LIVE_GRADLE=1 but the live Gradle tests cannot run: {SKIP}", pytrace=False)
    monkeypatch.setenv("JAVA_HOME", JAVA_HOME)
    monkeypatch.setenv("PATH", f"{JAVA_HOME}/bin:{Path(GRADLE).parent}:{os.environ['PATH']}")
    monkeypatch.setenv("GRADLE_USER_HOME", GRADLE_HOME)
    for name in ("GRADLE_OPTS", "JAVA_TOOL_OPTIONS", "JDK_JAVA_OPTIONS", "JAVA_OPTS"):
        monkeypatch.delenv(name, raising=False)


def check(repo: Path, tests=None, timeout: float = 1500) -> dict:
    return checker.run_check(repo, tests=tests, timeout=timeout, profile=JAVA)


def text(result: dict) -> str:
    return " | ".join(result["problems"] + result["failures"] + result.get("notes", []))


# ---- a green run, and what it reads --------------------------------------------------------------------


def test_a_green_build_passes_with_its_counts_read_from_the_record(tmp_path):
    result = check(project(tmp_path, "shop"))
    assert result["passed"] is True, result
    assert result["summary"] == "5 passed, 0 failed, 0 skipped in 3 test classes"
    assert result["counts"] == {"passed": 5, "failed": 0, "errored": 0, "skipped": 0, "classes": 3, "flaky": 0}
    assert result["problems"] == [] and result["exit_code"] == 0
    argv = result["command"]
    for flag in ("--no-daemon", "--console=plain", "--no-build-cache", "--no-configuration-cache", "--no-configure-on-demand", "--continue"):
        assert flag in argv
    assert argv.endswith(" test") and "-I " in argv and "ripple.manifest" not in argv


def test_failures_and_errors_are_listed_as_file_class_method(tmp_path):
    result = check(project(tmp_path, "shop_fail"))
    assert result["passed"] is False and result["failure_count"] == 2 and result["problems"] == [], result
    assert "CartTest.totalIsWrong" in result["failures"][0] and "CartTest.boom" in result["failures"][1]
    assert result["failures"][0].startswith(f"FAILED {CART}::CartTest.totalIsWrong")
    assert result["exit_code"] == 1


def test_a_compile_error_before_the_tests_is_not_passed_and_names_the_line(tmp_path):
    result = check(project(tmp_path, "test_compile_error"))
    assert result["passed"] is False and result["counts"]["passed"] == 0
    assert any(p.startswith("compilation failed") and ".java:" in p for p in result["problems"]), result["problems"]


def test_a_stale_passing_report_of_an_earlier_run_is_not_this_runs_result(tmp_path):
    repo = project(tmp_path, "shop")
    assert check(repo)["passed"] is True  # leaves build/test-results saying PASSED
    test = repo / CART
    test.write_text(test.read_text().replace("void addsItems() {", "void addsItems() {\n        new Cart().nosuch();"))
    result = check(repo)
    assert result["passed"] is False and result["counts"]["passed"] == 0, result
    assert any(p.startswith("compilation failed") for p in result["problems"])
    assert list((repo / "build" / "test-results" / "test").glob("TEST-*.xml")), "the earlier run's reports are still on disk: the point of the test"


def test_a_build_that_already_ran_is_run_again_not_taken_as_up_to_date(tmp_path):
    repo = project(tmp_path, "shop")
    assert check(repo)["passed"] is True
    again = check(repo)
    assert again["passed"] is True and again["counts"]["passed"] == 5, again


# ---- a test JVM that stops: never a pass ---------------------------------------------------------------------
#
# Gradle calls a JVM that exits with status 0 a normal end: BUILD SUCCESSFUL, the later tests never run. The listener says
# otherwise by ending the JVM's own suite, and the class it stopped in, SKIPPED. Each project is the shop fixture plus one
# class whose LAST test would fail (1 == 2) if it ran.

@pytest.mark.parametrize("shape", sorted(EXIT_SHAPES))
def test_a_test_jvm_that_exits_with_status_zero_is_not_a_pass(tmp_path, shape):
    result = check(shop(tmp_path, EXIT_SHAPES[shape]))
    assert result["exit_code"] == 0, result  # Gradle itself says BUILD SUCCESSFUL: only the record shows it
    assert result["passed"] is False, result
    assert "SKIPPED without finishing it" in text(result), result
    assert result["failures"] == [], "the test after the exit never ran, so nothing failed: this is not a failure verdict"


def test_a_failure_of_the_init_script_inside_gradle_is_said_as_that_and_not_as_a_dead_jvm(tmp_path, monkeypatch):
    """What Gradle 8 did to the first listener (a property it does not have): every test threw, and the verdict blamed the test JVM."""
    monkeypatch.setattr(gradle_check, "INIT_SCRIPT", gradle_check.INIT_SCRIPT.replace("result: r.resultType.name(), ms:", "result: r.noSuchProperty, ms:"))
    result = check(project(tmp_path, "shop"))
    assert result["passed"] is False, result
    assert "ripple's own init script failed inside Gradle (No such property: noSuchProperty" in text(result), result
    assert result["problems"][0].startswith("ripple's own init script failed inside Gradle"), result["problems"]


def test_a_forked_jvm_that_exits_is_not_a_pass_when_other_jvms_finished(tmp_path):
    build = BUILD.replace("test { useJUnitPlatform() }", "test { useJUnitPlatform(); forkEvery = 1; maxParallelForks = 2 }")
    result = check(shop(tmp_path, EXIT_SHAPES["plain_exit_zero"], build=build))
    assert result["passed"] is False and "SKIPPED without finishing it" in text(result), result


def test_a_jvm_exiting_nonzero_or_killed_is_not_a_pass_and_names_the_exit(tmp_path):
    exit3 = shop(tmp_path / "a", {"ZzzExitTest.java": "class ZzzExitTest {\n  @Test void quits() { System.exit(3); }\n}\n"})
    result = check(exit3)
    assert result["passed"] is False and result["exit_code"] == 1 and "non-zero exit value 3" in text(result), result
    kill = "new ProcessBuilder(\"kill\", \"-9\", String.valueOf(ProcessHandle.current().pid())).start().waitFor(); Thread.sleep(5000);"
    killed = shop(tmp_path / "b", {"ZzzKillTest.java": f"class ZzzKillTest {{\n  @Test void first() {{}}\n  @Test void killed() throws Exception {{ {kill} }}\n}}\n"})
    result = check(killed)
    assert result["passed"] is False and result["exit_code"] == 1 and "non-zero exit value 137" in text(result), result


def test_the_checks_own_time_limit_kills_gradle_and_leaves_no_daemon(tmp_path):
    slow = shop(tmp_path, {"SlowTest.java": "class SlowTest {\n  @Test void sleeps() throws Exception { Thread.sleep(60000); }\n}\n"})
    result = check(slow, timeout=25)
    assert result["passed"] is False and result["timed_out"] is True and "timed out after 25s" in text(result), result
    # the build JVM dies with the process group at once; the test worker notices its parent is gone and follows within moments
    deadline = time.monotonic() + 30
    while True:
        left = [line for line in subprocess.run(["pgrep", "-af", "GradleWorkerMain|GradleDaemon"], capture_output=True, text=True, check=False).stdout.splitlines() if str(tmp_path) in line]
        if not left or time.monotonic() > deadline:
            break
        time.sleep(0.5)
    assert not left, left


# ---- the false reds: suites that are real projects ----------------------------------------------------------


def test_a_real_suite_of_parameterized_nested_inherited_dynamic_and_templated_tests_passes(tmp_path):
    """The shapes ripple's own reading of the source could not prove (about 12% of commons-lang's test classes, junit5's suites): Gradle's listener says what ran."""
    result = check(shop(tmp_path, COMMONS))
    assert result["passed"] is True, result
    assert result["problems"] == [] and result["failures"] == [] and result["exit_code"] == 0
    counts = result["counts"]
    assert (counts["passed"], counts["failed"], counts["skipped"]) == (26, 0, 10), counts  # tallied by hand from COMMONS and the shop fixture
    assert "skipped or disabled, so not run" in text(result)


def test_two_parameterized_methods_in_a_class_that_extends_another_are_a_pass_in_a_whole_run(tmp_path):
    files = {"AbstractLangTest.java": COMMONS["AbstractLangTest.java"], "PricingTest.java": COMMONS["PricingTest.java"]}
    result = check(shop(tmp_path, files))
    assert result["passed"] is True and result["counts"]["passed"] == 5 + 1 + 1 + 3 + 2 + 2 + 0, result  # shop 5, inherited(PricingTest too: 2), zero, discounts 3, codes 2, positive 2


def test_a_whole_project_of_one_such_class_is_a_pass_by_class_id_too(tmp_path):
    files = {"AbstractLangTest.java": COMMONS["AbstractLangTest.java"], "PricingTest.java": COMMONS["PricingTest.java"]}
    result = check(shop(tmp_path, files), ["com.acme.shop.PricingTest"])
    assert result["passed"] is True and result["counts"]["passed"] == 1 + 1 + 3 + 2 + 2, result


# ---- ids -------------------------------------------------------------------------------------------------------


def test_a_method_with_a_display_name_asked_by_its_method_id_ran_and_is_a_pass(tmp_path):
    files = {"NamedTest.java": "class NamedTest {\n  @Test @DisplayName(\"adds items to the cart\") void addsItems() { assertEquals(1, 1); }\n  @Test void other() {}\n}\n"}
    result = check(shop(tmp_path, files), ["com.acme.shop.NamedTest.addsItems()"])
    assert result["passed"] is True and result["counts"]["passed"] == 1, result
    assert result["command"].endswith(":test --tests com.acme.shop.NamedTest.addsItems") or ":test --tests com.acme.shop.NamedTest.addsItems" in result["command"]


def test_two_parameterized_methods_asked_together_one_of_which_ran_none_of_its_invocations_is_not_a_pass(tmp_path):
    """The shape that read as a pass: `refunds` skipped every invocation and `discounts` ran, and a joint reading credited both."""
    result = check(shop(tmp_path, DYN), ["com.acme.shop.CartTest2.discounts", "com.acme.shop.CartTest2.refunds"])
    assert result["passed"] is False, result
    assert "CartTest2.refunds" in text(result) and "skipped or disabled, so it did not run" in text(result), result


def test_a_parameterized_method_and_a_display_named_one_asked_together_are_each_proved(tmp_path):
    result = check(shop(tmp_path, DYN), ["com.acme.shop.CartTest2.discounts", "com.acme.shop.CartTest2.addsItems()"])
    assert result["passed"] is True and result["counts"]["passed"] == 3, result


def test_a_method_of_a_nested_class_and_a_class_asked_by_id_are_proved_by_their_own_tests(tmp_path):
    result = check(project(tmp_path, "shop"), ["com.acme.shop.CartTest.Receipts.receiptHasTotal()"])
    assert result["passed"] is True and result["counts"]["passed"] == 1, result
    result = check(project(tmp_path / "c", "shop"), ["com.acme.shop.CartTest"])
    assert result["passed"] is True and result["counts"]["passed"] == 4, result  # CartTest and its @Nested class
    result = check(project(tmp_path / "f", "shop"), [CART])
    assert result["passed"] is True, result


def test_a_method_id_in_a_run_whose_jvm_exits_is_not_a_pass(tmp_path):
    files = {"AaaExitTest.java": "class AaaExitTest {\n  @Test void first() {}\n  @Test void quits() { System.exit(0); }\n}\n"}
    result = check(shop(tmp_path, files), ["com.acme.shop.AaaExitTest", "com.acme.shop.CartTest.addsItems()"])
    assert result["passed"] is False and "SKIPPED without finishing it" in text(result), result
    # asked for `first()` alone, the filter never selects `quits()`, so the JVM never exits and the run is whole
    alone = check(shop(tmp_path / "alone", files), ["com.acme.shop.AaaExitTest.first()"])
    assert alone["passed"] is True and alone["counts"]["passed"] == 1, alone


# ---- what the build narrows, and what is not the repo's tests ----------------------------------------------------


def test_tests_the_build_excludes_by_tag_or_pattern_are_a_note_and_the_rest_passes(tmp_path):
    files = {"SlowTest.java": "class SlowTest {\n  @Test void fast() {}\n  @Test @Tag(\"integration\") void needsDb() { fail(\"needs a db\"); }\n}\n", "CartIT.java": "class CartIT {\n  @Test void needsDb() { fail(\"needs a db\"); }\n}\n"}
    build = BUILD.replace("test { useJUnitPlatform() }", "test { useJUnitPlatform { excludeTags 'integration' }; exclude '**/*IT.class' }")
    result = check(shop(tmp_path, files, build=build))
    assert result["passed"] is True and result["counts"]["passed"] == 6, result
    assert ":test narrows its tests (excludes **/*IT.class; excludeTags integration)" in text(result) or "excludeTags integration" in text(result)


def test_a_test_task_the_build_leaves_out_of_the_graph_is_a_problem(tmp_path):
    repo = multi(tmp_path)
    write(repo / "settings.gradle", "rootProject.name = 'root'\ninclude 'core', 'web'\ngradle.startParameter.excludedTaskNames += ':web:test'\n")
    result = check(repo)
    assert result["exit_code"] == 0 and result["passed"] is False, result
    assert ":web:test was left out of the task graph" in text(result)


@pytest.mark.parametrize(
    "setting,notes",
    [
        ("test { enabled = false }", [":web:test was skipped by the build (Gradle said SKIPPED: enabled = false), so its tests did not run"]),
        ("test { onlyIf { System.getenv('RIPPLE_NEVER_SET_RUN_WEB_TESTS') != null } }", [":web:test was skipped by the build (Gradle said SKIPPED: an onlyIf condition that is false), so its tests did not run"]),
        (
            "test { exclude '**/*' }",
            ["Gradle found no test classes for :web:test (NO-SOURCE), so no test ran in it", ":web:test narrows its tests (excludes **/*): the tests it leaves out did not run"],
        ),
    ],
)
def test_a_module_whose_tests_the_build_itself_switches_off_is_a_note_whichever_way_it_is_spelled(tmp_path, setting, notes):
    """`enabled = false`, an `onlyIf`, and `exclude '**/*'` are three spellings of one intent and get one verdict. The module's test fails if it runs."""
    repo = multi(tmp_path)
    web = (repo / "web" / "build.gradle").read_text()
    write(repo / "web" / "build.gradle", web + "\n" + setting + "\n")
    write(repo / "web/src/test/java/com/acme/web/WebFailTest.java", "package com.acme.web;\nimport org.junit.jupiter.api.*;\nclass WebFailTest { @Test void mustFail() { Assertions.fail(\"never runs\"); } }\n")
    result = check(repo)
    assert result["passed"] is True and result["problems"] == [] and result["failures"] == [], result
    for note in notes:
        assert note in result["notes"], result["notes"]


def test_a_build_whose_only_test_task_is_disabled_is_still_not_a_pass(tmp_path):
    repo = project(tmp_path, "shop", build=BUILD + "\ntest { enabled = false }\n")
    result = check(repo)
    assert result["passed"] is False and "no tests ran" in text(result), result
    assert ":test was skipped by the build (Gradle said SKIPPED: enabled = false)" in text(result)


def test_a_project_with_no_test_sources_is_a_note_and_the_others_still_pass(tmp_path):
    repo = multi(tmp_path)
    shutil.rmtree(repo / "web" / "src" / "test")
    result = check(repo)
    assert result["passed"] is True, result
    assert "Gradle found no test classes for :web:test (NO-SOURCE), so no test ran in it" in text(result)


def test_a_test_task_that_executes_and_finds_no_test_is_a_problem_beside_a_project_that_ran_tests(tmp_path):
    """JUnit 5 tests in a module that leaves out `useJUnitPlatform()` and opts out of Gradle 9's failure: the task executes, runs nothing, and its failing test never runs."""
    repo = multi(tmp_path)
    plain = "plugins { id 'java' }\nrepositories { mavenCentral() }\n" + JUNIT_DEPS + "test { failOnNoDiscoveredTests = false }\n"
    write(repo / "web" / "build.gradle", plain.replace("dependencies {", "dependencies {\n  implementation project(':core')", 1))
    write(repo / "web/src/test/java/com/acme/web/WebFailTest.java", "package com.acme.web;\nimport org.junit.jupiter.api.*;\nclass WebFailTest { @Test void mustFail() { Assertions.fail(\"never ran\"); } }\n")
    result = check(repo)
    assert result["exit_code"] == 0 and result["passed"] is False, result
    assert ":web:test executed and ran no test" in text(result), result


def _web_with(repo: Path, setting: str) -> None:
    """:web gets JUnit 5 tests of which one fails, and `setting` in its test block: nothing in it may pass unseen."""
    junit5 = "plugins { id 'java' }\nrepositories { mavenCentral() }\n" + JUNIT_DEPS.replace("dependencies {", "dependencies {\n  implementation project(':core')", 1)
    write(repo / "web" / "build.gradle", junit5 + setting + "\n")
    (repo / "web/src/test/java/com/acme/web/WebTest.java").unlink()  # the fixture's own passing test: web's only test is the slow one
    write(repo / "web/src/test/java/com/acme/web/WebSlowTest.java", "package com.acme.web;\nimport org.junit.jupiter.api.*;\n@Tag(\"slow\")\nclass WebSlowTest { @Test void mustFail() { Assertions.fail(\"slow\"); } }\n")


def test_a_module_whose_every_test_is_excluded_by_tag_is_a_note_and_the_other_modules_still_pass(tmp_path):
    """Measured: Gradle 9.9.1 exits 0 when `excludeTags` leaves no test, so tags are the one narrowing that explains an empty task."""
    repo = multi(tmp_path)
    _web_with(repo, "test { useJUnitPlatform { excludeTags 'slow' } }")
    result = check(repo)
    assert result["passed"] is True and result["failures"] == [], result
    assert ":web:test executed and ran no test, and the build narrows it by tags" in result["notes"], result["notes"]


@pytest.mark.parametrize("setting", ["test { include '**/*Test.class' }", "test { filter { excludeTestsMatching '*IT' } }", "test { exclude '**/*IT.class' }"])
def test_a_module_with_no_use_junit_platform_and_an_include_exclude_or_filter_is_not_a_pass(tmp_path, setting):
    """On 9.9.1 Gradle fails these builds itself ("test sources present and no filters are applied", "No tests found"): include and exclude are not the reason a task ran nothing."""
    repo = multi(tmp_path)
    _web_with(repo, setting)
    result = check(repo)
    assert result["passed"] is False and result["exit_code"] != 0, result


def test_another_test_task_is_not_run_by_test_and_is_named(tmp_path):
    build = BUILD + "\ntesting {\n  suites {\n    integrationTest(JvmTestSuite) {\n      useJUnitJupiter('5.11.3')\n      dependencies { implementation project() }\n    }\n  }\n}\n"
    repo = project(tmp_path, "shop", build=build)
    write(repo / "src/integrationTest/java/com/acme/shop/CartIntegrationTest.java", "package com.acme.shop;\nimport org.junit.jupiter.api.Test;\nclass CartIntegrationTest {\n  @Test void reallyWorks() { new Cart().total(); }\n}\n")
    result = check(repo)
    assert result["passed"] is True and result["counts"]["classes"] == 3, result
    assert ":integrationTest is a test task that `test` does not run" in text(result)


# ---- buildSrc and included builds are not the repo's tests -------------------------------------------------------

BUILDSRC_TEST = "import org.junit.jupiter.api.*;\nclass HelperTest { @Test void one() { Assertions.assertEquals(1, Helper.one()); } }\n"
BUILDSRC_FAIL = "import org.junit.jupiter.api.*;\nclass HelperTest { @Test void one() { Assertions.assertEquals(2, Helper.one()); } }\n"


def with_build_src(tmp_path: Path, test: str, root_files: dict[str, str] | None = None) -> Path:
    repo = shop(tmp_path, root_files)
    write(repo / "buildSrc" / "build.gradle", "plugins { id 'java' }\nrepositories { mavenCentral() }\n" + BUILD.split("repositories { mavenCentral() }\n", 1)[1] + "jar.dependsOn test\n")
    write(repo / "buildSrc" / "src" / "main" / "java" / "Helper.java", "public class Helper { public static int one() { return 1; } }\n")
    write(repo / "buildSrc" / "src" / "test" / "java" / "HelperTest.java", test)
    return repo


def test_buildsrc_tests_that_run_do_not_change_the_verdict_of_the_root_build(tmp_path):
    result = check(with_build_src(tmp_path, BUILDSRC_TEST))
    assert result["passed"] is True and result["counts"] == {"passed": 5, "failed": 0, "errored": 0, "skipped": 0, "classes": 3, "flaky": 0}, result
    assert "HelperTest" not in text(result)
    assert [n for n in result["notes"] if "hold" in n and "test files" in n] == [
        "buildSrc/ holds test files that no project of this build owns (a project missing from settings.gradle, a sample or a build of its own such as buildSrc): Gradle did not run them as this build's tests"
    ]


def test_a_failing_root_test_is_the_verdict_whatever_buildsrcs_tests_say(tmp_path):
    root_fail = {"ZzzFailTest.java": "class ZzzFailTest {\n  @Test void fails() { assertEquals(1, 2); }\n}\n"}
    result = check(with_build_src(tmp_path, BUILDSRC_TEST, root_fail))
    assert result["passed"] is False and result["failure_count"] == 1 and "ZzzFailTest.fails" in result["failures"][0], result
    assert "HelperTest" not in text(result)


def test_a_buildsrc_whose_own_test_fails_stops_the_build_and_is_never_a_pass(tmp_path):
    result = check(with_build_src(tmp_path, BUILDSRC_FAIL))
    assert result["passed"] is False and result["exit_code"] != 0, result
    assert result["counts"]["passed"] == 0 and result["counts"]["failed"] == 0, result  # buildSrc's test is not counted as the repo's, and the build never got to the root's


def test_a_build_logic_included_build_with_no_tests_of_its_own_leaves_the_root_build_judged(tmp_path):
    """`pluginManagement { includeBuild }` convention plugins: configured first, with the same init script, and not the repo's tests."""
    repo = shop(tmp_path, settings="pluginManagement { includeBuild 'build-logic' }\nrootProject.name = 'shop'\n")
    (repo / "build.gradle").write_text("plugins { id 'acme.java-conventions' }\n" + BUILD.split("repositories { mavenCentral() }\n", 1)[1])
    write(repo / "build-logic" / "settings.gradle", "rootProject.name = 'build-logic'\n")
    write(repo / "build-logic" / "build.gradle", "plugins { id 'groovy-gradle-plugin' }\n")
    write(repo / "build-logic" / "src" / "main" / "groovy" / "acme.java-conventions.gradle", "plugins { id 'java' }\nrepositories { mavenCentral() }\n")
    result = check(repo)
    assert result["passed"] is True and result["counts"]["passed"] == 5, result


def test_a_sample_project_among_the_test_resources_is_not_the_repos_tests(tmp_path):
    repo = project(tmp_path, "shop")
    write(repo / "src/test/resources/samples/hello/src/test/java/HelloTest.java", "import org.junit.jupiter.api.*;\nclass HelloTest { @Test void hi() { Assertions.fail(\"a sample, never compiled\"); } }\n")
    result = check(repo)
    assert result["passed"] is True and result["counts"]["passed"] == 5, result


# ---- test files Gradle never runs: PASS, and one note each -----------------------------------------------------------

FAILING_TEST = "package {pkg};\nimport org.junit.jupiter.api.*;\nclass {cls} {{ @Test void mustFail() {{ Assertions.fail(\"never runs\"); }} }}\n"
UNRUN = ("holds test files", "hold test files", "no `test` task", "reads its test classes", "(includeBuild)")


def unrun(result: dict) -> list[str]:
    return [n for n in result.get("notes", []) if any(word in n for word in UNRUN)]


def test_a_project_missing_from_settings_with_a_failing_test_is_a_pass_with_a_note_naming_it(tmp_path):
    repo = multi(tmp_path)
    write(repo / "legacy" / "build.gradle", BUILD)
    write(repo / "legacy/src/test/java/com/acme/legacy/LegacyTest.java", FAILING_TEST.format(pkg="com.acme.legacy", cls="LegacyTest"))
    result = check(repo)
    assert result["passed"] is True and result["failures"] == [], result
    [note] = unrun(result)
    assert note.startswith("legacy/src/test/java holds test files that no project of this build owns"), note


def test_a_project_with_a_test_directory_and_no_test_task_is_a_pass_with_a_note_naming_it(tmp_path):
    repo = multi(tmp_path)
    write(repo / "web" / "build.gradle", "")  # no java plugin: no `test` task
    result = check(repo)
    assert result["passed"] is True, result
    assert ":web has a test directory (web/src/test/java) and no `test` task, so none of its tests ran" in result["notes"], result["notes"]


def test_an_included_build_with_a_failing_test_is_a_pass_with_a_note_naming_the_build(tmp_path):
    repo = shop(tmp_path, settings="rootProject.name = 'shop'\nincludeBuild 'lib'\n")
    write(repo / "lib" / "settings.gradle", "rootProject.name = 'lib'\n")
    write(repo / "lib" / "build.gradle", BUILD)
    write(repo / "lib/src/test/java/com/acme/lib/LibTest.java", FAILING_TEST.format(pkg="com.acme.lib", cls="LibTest"))
    result = check(repo)
    assert result["passed"] is True and result["failures"] == [], result
    assert unrun(result) == ["the build includes lib (includeBuild): their tests are not run by this build, and this verdict says nothing about them"], result["notes"]


def test_a_test_source_set_pointed_away_from_a_directory_of_test_files_is_a_pass_with_a_note_naming_both(tmp_path):
    repo = multi(tmp_path)
    web = (repo / "web" / "build.gradle").read_text()
    write(repo / "web" / "build.gradle", web + "\nsourceSets { test { java { srcDirs = ['src/unit/java'] } } }\n")
    write(repo / "web/src/test/java/com/acme/web/WebFailTest.java", FAILING_TEST.format(pkg="com.acme.web", cls="WebFailTest"))
    result = check(repo)
    assert result["passed"] is True and result["failures"] == [], result
    assert ":web reads its test classes from web/src/unit/java, not from web/src/test/java, which holds test files: they did not run" in result["notes"], result["notes"]
    assert "Gradle found no test classes for :web:test (NO-SOURCE), so no test ran in it" in result["notes"]


LIFECYCLE = """plugins { id 'java-base' }
repositories { mavenCentral() }
sourceSets { test { } }
""" + JUNIT_DEPS + """
def unit = tasks.register('unitTest', Test) {
  useJUnitPlatform()
  testClassesDirs = sourceSets.test.output.classesDirs
  classpath = sourceSets.test.runtimeClasspath
}
tasks.register('test') { dependsOn unit }
"""


LIFECYCLE_TEST = "package com.acme.web;\nimport org.junit.jupiter.api.*;\nclass WebTest {{ @Test void {name}() {{ {body} }} }}\n"


def lifecycle_project(tmp_path: Path, failing: bool) -> Path:
    """Android's shape: `test` is a lifecycle task, a Test task named `unitTest` runs `sourceSets.test`. The Test task is not named `test`."""
    repo = multi(tmp_path)
    write(repo / "web" / "build.gradle", LIFECYCLE)
    shutil.rmtree(repo / "web" / "src")  # java-base compiles no main sources: web's tests stand alone
    write(repo / "web/src/test/java/com/acme/web/WebTest.java", LIFECYCLE_TEST.format(name="works", body=""))
    if failing:
        write(repo / "web/src/test/java/com/acme/web/WebFailTest.java", FAILING_TEST.format(pkg="com.acme.web", cls="WebFailTest"))
    return repo


def test_a_test_task_with_another_name_under_a_lifecycle_test_is_counted_and_not_called_a_directory_with_no_test_task(tmp_path):
    result = check(lifecycle_project(tmp_path, failing=False))
    assert result["passed"] is True and result["counts"]["classes"] == 2 and result["counts"]["passed"] == 2, result
    assert unrun(result) == [], result["notes"]


def test_a_failing_test_of_a_test_task_with_another_name_under_a_lifecycle_test_is_a_failure(tmp_path):
    result = check(lifecycle_project(tmp_path, failing=True))
    assert result["passed"] is False and result["failure_count"] == 1 and "WebFailTest.mustFail" in result["failures"][0], result


def test_a_single_project_whose_only_test_task_has_another_name_under_a_lifecycle_test_is_judged_by_that_task(tmp_path):
    repo = tmp_path / "repo"
    write(repo / "settings.gradle", "rootProject.name = 'life'\n")
    write(repo / "gradle.properties", "org.gradle.offline=true\n")
    write(repo / "build.gradle", LIFECYCLE)
    write(repo / "src/test/java/com/acme/web/WebTest.java", LIFECYCLE_TEST.format(name="works", body=""))
    result = check(repo)
    assert result["passed"] is True and result["counts"]["passed"] == 1 and result["problems"] == [], result


def test_an_ordinary_project_gets_none_of_these_notes(tmp_path):
    for name, repo in (("one", project(tmp_path / "one", "shop")), ("many", multi(tmp_path / "many"))):
        result = check(repo)
        assert result["passed"] is True and unrun(result) == [], (name, result.get("notes"))


# ---- the launcher ----------------------------------------------------------------------------------------


def test_the_repos_own_wrapper_is_preferred_and_runs(tmp_path):
    repo = project(tmp_path, "shop")
    dist = os.environ.get("RIPPLE_TEST_GRADLE_DIST") or next(iter(sorted(glob.glob("/tmp/gradle-toolchain/dl/gradle-*-bin.zip"))), None)
    if dist is None and REQUIRED:
        pytest.fail("RIPPLE_REQUIRE_LIVE_GRADLE=1 but there is no Gradle distribution zip (set RIPPLE_TEST_GRADLE_DIST, or gradle-*-bin.zip under /tmp/gradle-toolchain/dl) to point a wrapper at")
    if dist is None:
        pytest.skip("no Gradle distribution zip to point a wrapper at")
    made = subprocess.run(
        [GRADLE, "--no-daemon", "--offline", "wrapper", "--gradle-distribution-url", f"file://{dist}"],
        cwd=repo, capture_output=True, text=True, timeout=600, check=False,
    )
    assert made.returncode == 0, made.stdout + made.stderr
    assert gradle_check.choose_tool(repo) == [str(repo / "gradlew")]
    result = check(repo)
    assert result["passed"] is True and result["command"].startswith(str(repo / "gradlew")), result
