"""`check` for Java on Gradle 8, end to end with a real Gradle 8: skipped, with the reason, when there is none on the machine.

Most repos pin Gradle 8 in their wrapper, and the wrapper wins over `gradle` on PATH, so the oldest Gradle ripple claims
is run here, not only the newest. Gradle 8.10.2 has no `TestResult.assumptionFailure`: the first version of the listener threw
on every test and every run was "unverified". These tests run the shapes of test_check_gradle_live.py on 8.10.2.

The Gradle is RIPPLE_TEST_GRADLE8 (a `gradle` binary), else /tmp/java-inv-gradle/gradle-8.*/bin/gradle; its user home is
RIPPLE_TEST_GRADLE8_HOME, else /tmp/java-inv-gradle/home, and must already hold JUnit 5.11.3. A JDK is found as in the 9.x module.
RIPPLE_REQUIRE_LIVE_GRADLE=1 turns the skip into a failure.
"""

from __future__ import annotations

import glob
import os
from pathlib import Path

import pytest
from gradle_check_support import (
    BUILD,
    COMMONS,
    DYN,
    EXIT_SHAPES,
    JUNIT,
    find_java,
    multi,
    project,
    shop,
    write,
)

from ripple import checker
from ripple.profile import JAVA

GRADLE_HOME = os.environ.get("RIPPLE_TEST_GRADLE8_HOME", "/tmp/java-inv-gradle/home")
JAVA_HOME = find_java()
GRADLE = os.environ.get("RIPPLE_TEST_GRADLE8") or next(iter(sorted(glob.glob("/tmp/java-inv-gradle/gradle-8.*/bin/gradle"))), None)
if JAVA_HOME is None:
    SKIP = "no JDK (set JAVA_HOME)"
elif GRADLE is None or not Path(GRADLE).exists():
    SKIP = "no Gradle 8 (set RIPPLE_TEST_GRADLE8 to a gradle binary)"
elif not (Path(GRADLE_HOME) / "caches" / "modules-2" / "files-2.1" / "org.junit.jupiter" / "junit-jupiter" / "5.11.3").is_dir():
    SKIP = f"no Gradle cache with JUnit 5.11.3 at {GRADLE_HOME} (set RIPPLE_TEST_GRADLE8_HOME)"
else:
    SKIP = None

REQUIRED = os.environ.get("RIPPLE_REQUIRE_LIVE_GRADLE") == "1"
pytestmark = pytest.mark.skipif(SKIP is not None and not REQUIRED, reason=SKIP or "")


@pytest.fixture(autouse=True)
def tools(monkeypatch):
    if SKIP is not None:  # only reached when REQUIRED
        pytest.fail(f"RIPPLE_REQUIRE_LIVE_GRADLE=1 but the live Gradle 8 tests cannot run: {SKIP}", pytrace=False)
    monkeypatch.setenv("JAVA_HOME", JAVA_HOME)
    monkeypatch.setenv("PATH", f"{JAVA_HOME}/bin:{Path(GRADLE).parent}:{os.environ['PATH']}")
    monkeypatch.setenv("GRADLE_USER_HOME", GRADLE_HOME)
    for name in ("GRADLE_OPTS", "JAVA_TOOL_OPTIONS", "JDK_JAVA_OPTIONS", "JAVA_OPTS"):
        monkeypatch.delenv(name, raising=False)


def check(repo: Path, tests=None, timeout: float = 1500) -> dict:
    return checker.run_check(repo, tests=tests, timeout=timeout, profile=JAVA)


def text(result: dict) -> str:
    return " | ".join(result["problems"] + result["failures"] + result.get("notes", []))


def test_the_gradle_under_test_is_gradle_8():
    import subprocess

    out = subprocess.run([GRADLE, "--version"], capture_output=True, text=True, check=False, timeout=300).stdout
    assert "Gradle 8." in out, out


def test_a_green_build_passes_on_gradle_8_with_the_counts_read_from_the_record(tmp_path):
    result = check(project(tmp_path, "shop"))
    assert result["passed"] is True and result["problems"] == [], result
    assert result["counts"] == {"passed": 5, "failed": 0, "errored": 0, "skipped": 0, "classes": 3, "flaky": 0}


def test_failures_are_listed_on_gradle_8(tmp_path):
    result = check(project(tmp_path, "shop_fail"))
    assert result["passed"] is False and result["failure_count"] == 2 and result["problems"] == [], result


@pytest.mark.parametrize("shape", sorted(EXIT_SHAPES))
def test_a_test_jvm_that_exits_with_status_zero_is_not_a_pass_on_gradle_8(tmp_path, shape):
    result = check(shop(tmp_path, EXIT_SHAPES[shape]))
    assert result["exit_code"] == 0 and result["passed"] is False, result
    assert "SKIPPED without finishing it" in text(result), result
    assert result["failures"] == []


def test_a_real_suite_of_parameterized_nested_inherited_dynamic_templated_and_assumed_tests_passes_on_gradle_8(tmp_path):
    """Includes `assumeTrue(false)` in a test and in `@BeforeAll`, a nested class under a condition, a disabled class: Gradle 8 ends their suites SUCCESS."""
    result = check(shop(tmp_path, COMMONS))
    assert result["passed"] is True and result["problems"] == [], result
    assert (result["counts"]["passed"], result["counts"]["failed"], result["counts"]["skipped"]) == (26, 0, 10), result["counts"]


def test_a_module_with_junit_5_tests_and_no_use_junit_platform_is_not_a_pass_on_gradle_8(tmp_path):
    """On Gradle 8 this is the DEFAULT: the task executes, finds nothing, and the build is green."""
    repo = multi(tmp_path)
    plain = "plugins { id 'java' }\nrepositories { mavenCentral() }\n" + JUNIT + "test { }\n"
    write(repo / "web" / "build.gradle", plain.replace("dependencies {", "dependencies {\n  implementation project(':core')", 1))
    write(repo / "web/src/test/java/com/acme/web/WebFailTest.java", "package com.acme.web;\nimport org.junit.jupiter.api.*;\nclass WebFailTest { @Test void mustFail() { Assertions.fail(\"never ran\"); } }\n")
    result = check(repo)
    assert result["exit_code"] == 0 and result["passed"] is False, result
    assert ":web:test executed and ran no test" in text(result), result


def test_ids_are_proved_on_gradle_8_by_the_filter_gradle_applied_and_the_names_it_gives(tmp_path):
    result = check(shop(tmp_path, DYN), ["com.acme.shop.CartTest2.discounts", "com.acme.shop.CartTest2.addsItems()"])
    assert result["passed"] is True and result["counts"]["passed"] == 3, result
    refunds = check(shop(tmp_path / "r", DYN), ["com.acme.shop.CartTest2.discounts", "com.acme.shop.CartTest2.refunds"])
    assert refunds["passed"] is False and "skipped or disabled, so it did not run" in text(refunds), refunds
    nested = check(project(tmp_path / "n", "shop"), ["com.acme.shop.CartTest.Receipts.receiptHasTotal()"])
    assert nested["passed"] is True and nested["counts"]["passed"] == 1, nested


def test_buildsrc_tests_do_not_change_the_verdict_on_gradle_8(tmp_path):
    repo = shop(tmp_path)
    write(repo / "buildSrc" / "build.gradle", "plugins { id 'java' }\nrepositories { mavenCentral() }\n" + BUILD.split("repositories { mavenCentral() }\n", 1)[1] + "jar.dependsOn test\n")
    write(repo / "buildSrc/src/main/java/Helper.java", "public class Helper { public static int one() { return 1; } }\n")
    write(repo / "buildSrc/src/test/java/HelperTest.java", "import org.junit.jupiter.api.*;\nclass HelperTest { @Test void one() { Assertions.assertEquals(1, Helper.one()); } }\n")
    result = check(repo)
    assert result["passed"] is True and result["counts"]["passed"] == 5 and "HelperTest" not in text(result), result
