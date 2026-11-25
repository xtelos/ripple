"""`check` for Java on Gradle with JUnit 4, end to end on a real Gradle 9 and a real Gradle 8: skipped, with the reason, where one is missing.

JUnit 4.13.2 and hamcrest-core 1.3 come from a local Maven repository (RIPPLE_TEST_M2, else /tmp/java-inv-m2), so no network is
needed. The Gradles are the ones of test_check_gradle_live.py (9.x) and test_check_gradle_live8.py (8.x): each test runs on both.
These pin what a review ran by hand: JUnit 4 runs under the same listener, a Parameterized method can be asked for by id, and
JUnit 4 tests under `useJUnitPlatform()` with no vintage engine are not a pass (nothing was discovered).
The Gradle 8 half runs on RIPPLE_TEST_GRADLE8_JAVA_HOME (else the JDK of the rest), which must be Java 8 to 23.
RIPPLE_REQUIRE_LIVE_GRADLE=1 turns a skip into a failure.
"""

from __future__ import annotations

import glob
import os
from pathlib import Path

import pytest
from gradle_check_support import find_gradle, find_java, gradle8_java, write

from ripple import checker
from ripple.profile import JAVA

M2 = Path(os.environ.get("RIPPLE_TEST_M2", "/tmp/java-inv-m2"))
JAVA_HOME = find_java()
# the JDK each Gradle runs on, and why it cannot: Gradle 8.10.2 dies on a JDK newer than 23, Gradle 9 does not
JDKS = {"9": (JAVA_HOME, None if JAVA_HOME else "no JDK (set JAVA_HOME)"), "8": gradle8_java()}
GRADLES = {
    "9": (find_gradle(), os.environ.get("RIPPLE_TEST_GRADLE_HOME", "/tmp/gradle-toolchain/home")),
    "8": (
        os.environ.get("RIPPLE_TEST_GRADLE8") or next(iter(sorted(glob.glob("/tmp/java-inv-gradle/gradle-8.*/bin/gradle"))), None),
        os.environ.get("RIPPLE_TEST_GRADLE8_HOME", "/tmp/java-inv-gradle/home"),
    ),
}
JARS = (M2 / "junit/junit/4.13.2/junit-4.13.2.jar", M2 / "org/hamcrest/hamcrest-core/1.3")
REQUIRED = os.environ.get("RIPPLE_REQUIRE_LIVE_GRADLE") == "1"


def unavailable(version: str) -> str | None:
    gradle, _home = GRADLES[version]
    if JDKS[version][1] is not None:
        return JDKS[version][1]
    if gradle is None or not Path(gradle).exists():
        return f"no Gradle {version} (see test_check_gradle_live{'8' if version == '8' else ''}.py)"
    if not all(j.exists() for j in JARS):
        return f"no JUnit 4.13.2 and hamcrest-core 1.3 under {M2} (set RIPPLE_TEST_M2)"
    return None


@pytest.fixture(params=["9", "8"])
def gradle(request, monkeypatch):
    version = request.param
    why = unavailable(version)
    if why is not None:
        if REQUIRED:
            pytest.fail(f"RIPPLE_REQUIRE_LIVE_GRADLE=1 but the live JUnit 4 tests cannot run on Gradle {version}: {why}", pytrace=False)
        pytest.skip(why)
    binary, home = GRADLES[version]
    java_home = JDKS[version][0]
    monkeypatch.setenv("JAVA_HOME", java_home)
    monkeypatch.setenv("PATH", f"{java_home}/bin:{Path(binary).parent}:{os.environ['PATH']}")
    monkeypatch.setenv("GRADLE_USER_HOME", home)
    for name in ("GRADLE_OPTS", "JAVA_TOOL_OPTIONS", "JDK_JAVA_OPTIONS", "JAVA_OPTS"):
        monkeypatch.delenv(name, raising=False)
    return version


def build(tmp_path: Path, files: dict[str, str], test_block: str = "test { useJUnit() }", dependencies: str = "testImplementation 'junit:junit:4.13.2'") -> Path:
    repo = tmp_path / "repo"
    write(repo / "settings.gradle", "rootProject.name = 'j4'\n")
    write(repo / "gradle.properties", "org.gradle.offline=true\n")
    write(
        repo / "build.gradle",
        f"plugins {{ id 'java' }}\nrepositories {{ maven {{ url = uri('{M2.as_uri()}') }} }}\ndependencies {{ {dependencies} }}\n{test_block}\n",
    )
    write(repo / "src/main/java/com/acme/Nothing.java", "package com.acme;\npublic class Nothing { }\n")
    for name, body in files.items():
        write(repo / "src/test/java/com/acme" / name, "package com.acme;\nimport org.junit.*;\nimport org.junit.runner.RunWith;\nimport org.junit.runners.*;\nimport java.util.*;\n" + body)
    return repo


def check(repo: Path, tests=None) -> dict:
    return checker.run_check(repo, tests=tests, timeout=1500, profile=JAVA)


def text(result: dict) -> str:
    return " | ".join(result["problems"] + result["failures"] + result.get("notes", []))


OK = {"OkTest.java": "public class OkTest {\n  @Test public void a() { Assert.assertEquals(1, 1); }\n  @Test public void b() { Assert.assertTrue(true); }\n}\n"}
PARAM = {
    "ParamTest.java": (
        "@RunWith(Parameterized.class)\npublic class ParamTest {\n"
        "  @Parameterized.Parameters(name = \"{0}\") public static Collection<Object[]> data() { return Arrays.asList(new Object[][] {{1}, {2}, {3}}); }\n"
        "  private final int n;\n  public ParamTest(int n) { this.n = n; }\n"
        "  @Test public void positive() { Assert.assertTrue(n > 0); }\n  @Test public void small() { Assert.assertTrue(n < 10); }\n}\n"
    ),
    **OK,
}


def test_a_green_junit_4_build_passes(gradle, tmp_path):
    result = check(build(tmp_path, OK))
    assert result["passed"] is True and result["counts"]["passed"] == 2 and result["problems"] == [], result


def test_a_failing_junit_4_test_is_a_failure(gradle, tmp_path):
    files = {"BadTest.java": "public class BadTest {\n  @Test public void bad() { Assert.assertEquals(1, 2); }\n  @Test public void good() { }\n}\n"}
    result = check(build(tmp_path, files))
    assert result["passed"] is False and result["failure_count"] == 1 and "BadTest.bad" in result["failures"][0], result


def test_a_junit_4_test_that_exits_the_jvm_with_status_zero_is_not_a_pass(gradle, tmp_path):
    files = {
        "ZzzExitTest.java": (
            "@FixMethodOrder(MethodSorters.NAME_ASCENDING)\npublic class ZzzExitTest {\n  @Test public void a_first() { }\n"
            "  @Test public void b_quits() { System.exit(0); }\n  @Test public void c_fails() { Assert.assertEquals(1, 2); }\n}\n"
        ),
        **OK,
    }
    result = check(build(tmp_path, files))
    assert result["exit_code"] == 0 and result["passed"] is False, result
    assert "SKIPPED without finishing it" in text(result), result


def test_ignored_junit_4_tests_are_skips_and_the_rest_passes(gradle, tmp_path):
    files = {
        "IgnoreTest.java": "public class IgnoreTest {\n  @Test @Ignore public void off() { }\n  @Test @Ignore(\"later\") public void offWhy() { }\n  @Test public void on() { }\n}\n",
        "OffClassTest.java": "@Ignore(\"class\")\npublic class OffClassTest {\n  @Test public void x() { }\n}\n",
    }
    result = check(build(tmp_path, files))
    assert result["passed"] is True and result["counts"]["passed"] == 1 and result["counts"]["skipped"] >= 3, result
    assert "skipped or disabled, so not run" in text(result)


def test_a_parameterized_class_asked_by_class_passes(gradle, tmp_path):
    result = check(build(tmp_path, PARAM), ["com.acme.ParamTest"])
    assert result["passed"] is True and result["counts"]["passed"] == 6, result


def test_a_parameterized_method_asked_by_id_passes_and_only_it_runs(gradle, tmp_path):
    """Gradle's `--tests Class.positive` matches none of `positive[1]`.. : the id is asked as `Class.positive` and `Class.positive[*]`."""
    result = check(build(tmp_path, PARAM), ["com.acme.ParamTest.positive"])
    assert result["passed"] is True and result["counts"]["passed"] == 3 and result["problems"] == [], result
    assert "--tests com.acme.ParamTest.positive --tests 'com.acme.ParamTest.positive[*]'" in result["command"], result["command"]


def test_a_junit_4_plain_method_and_two_ids_asked_together_pass(gradle, tmp_path):
    both = check(build(tmp_path / "a", PARAM), ["com.acme.OkTest.a", "com.acme.ParamTest.positive"])
    assert both["passed"] is True and both["counts"]["passed"] == 4, both
    one = check(build(tmp_path / "b", PARAM), ["com.acme.OkTest.a()"])
    assert one["passed"] is True and one["counts"]["passed"] == 1, one


def test_a_failing_parameterized_invocation_asked_by_method_is_a_failure(gradle, tmp_path):
    files = {"ParamTest.java": PARAM["ParamTest.java"].replace("n > 0", "n > 1")}
    result = check(build(tmp_path, files), ["com.acme.ParamTest.positive"])
    assert result["passed"] is False and result["failure_count"] == 1 and "ParamTest.positive[1]" in result["failures"][0], result


def test_junit_4_tests_under_the_junit_platform_with_no_vintage_engine_are_not_a_pass(gradle, tmp_path):
    """Nothing is discovered: Gradle 9 fails the task itself, and on Gradle 8 the task executes with 0 tests, which ripple refuses."""
    repo = build(tmp_path, OK, test_block="test { useJUnitPlatform() }", dependencies="testImplementation 'junit:junit:4.13.2'")
    result = check(repo)
    assert result["passed"] is False and result["counts"]["passed"] == 0, result
