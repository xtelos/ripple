"""`check` for Java end to end, with a real JDK and Maven: skipped, with the reason, when they are not there.

The tools are looked for in JAVA_HOME and on PATH, then in the portable copies under /tmp the measurements
used. The build runs offline against a Maven cache (RIPPLE_TEST_M2, else /tmp/java-inv-m2) that must already hold
JUnit 5.11.3, JMH 1.37 and the surefire 3.5.2, compiler and resources plugins of the Maven in use; nothing is ever
written to the home directory or the repo (the cache is named in the copy's own .mvn/maven.config). CI fills the
cache with ci/warm-maven.sh.

RIPPLE_REQUIRE_LIVE_MAVEN=1 (set by the CI jobs that install the java extra) turns the skip into a failure: a
suite that is meant to run there must not go back to skipping unseen.
"""

from __future__ import annotations

import glob
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from java_check_support import PROJECTS, meta

from ripple import checker
from ripple import java_check as jc
from ripple.profile import JAVA

CART = "src/test/java/com/acme/shop/CartTest.java"


def find_java() -> str | None:
    home = os.environ.get("JAVA_HOME")
    if home and (Path(home) / "bin" / "javac").exists():
        return home
    found = sorted(glob.glob("/tmp/java-inv-jdk/jdk-*"))
    return found[-1] if found and (Path(found[-1]) / "bin" / "javac").exists() else None


def find_mvn() -> str | None:
    return shutil.which("mvn") or next(iter(sorted(glob.glob("/tmp/java-inv-mvn/apache-maven-*/bin/mvn"))), None)


M2 = os.environ.get("RIPPLE_TEST_M2", "/tmp/java-inv-m2")
JAVA_HOME = find_java()
MVN = find_mvn()
if JAVA_HOME is None:
    SKIP = "no JDK (set JAVA_HOME)"
elif MVN is None:
    SKIP = "no Maven (put mvn on PATH)"
elif not (Path(M2) / "org" / "junit" / "jupiter" / "junit-jupiter" / "5.11.3").is_dir():
    SKIP = f"no Maven cache with JUnit 5.11.3 at {M2} (set RIPPLE_TEST_M2)"
else:
    SKIP = None

REQUIRED = os.environ.get("RIPPLE_REQUIRE_LIVE_MAVEN") == "1"
pytestmark = pytest.mark.skipif(SKIP is not None and not REQUIRED, reason=SKIP or "")


@pytest.fixture(autouse=True)
def tools(monkeypatch):
    if SKIP is not None:  # only reached when REQUIRED: without it the mark above skipped the test
        pytest.fail(f"RIPPLE_REQUIRE_LIVE_MAVEN=1 but the live Maven tests cannot run: {SKIP}", pytrace=False)
    monkeypatch.setenv("JAVA_HOME", JAVA_HOME)
    monkeypatch.setenv("PATH", f"{JAVA_HOME}/bin:{Path(MVN).parent}:{os.environ['PATH']}")
    for name in ("MAVEN_OPTS", "MAVEN_ARGS", "JAVA_TOOL_OPTIONS", "JDK_JAVA_OPTIONS"):
        monkeypatch.delenv(name, raising=False)


def project(tmp_path: Path, name: str) -> Path:
    repo = tmp_path / "repo"
    shutil.copytree(PROJECTS / name, repo)
    (repo / ".mvn").mkdir()
    (repo / ".mvn" / "maven.config").write_text(f"-Dmaven.repo.local={M2}\n-o\n")
    return repo.resolve()


def check(repo: Path, tests=None, timeout: float = 240) -> dict:
    return checker.run_check(repo, tests=tests, timeout=timeout, profile=JAVA)


def text(result: dict) -> str:
    return " | ".join(result["problems"] + result["failures"] + result.get("notes", []))


def test_a_green_build_passes_with_its_counts(tmp_path):
    result = check(project(tmp_path, "shop"))
    assert result["passed"] is True, result
    assert result["summary"] == "5 passed, 0 failed, 0 skipped in 3 test classes"
    assert result["command"].endswith("mvn -B --fail-at-end test")


def test_failures_and_errors_are_listed(tmp_path):
    result = check(project(tmp_path, "shop_fail"))
    assert result["passed"] is False and result["failure_count"] == 2 and result["problems"] == []
    assert "CartTest.totalIsWrong" in result["failures"][0] and "CartTest.boom" in result["failures"][1]


def test_one_method_by_the_id_ripple_lists_runs_that_method_only(tmp_path):
    repo = project(tmp_path, "shop")
    result = check(repo, ["com.acme.shop.CartTest.addsItems()"])
    assert result["passed"] is True and result["counts"]["passed"] == 1
    assert "-Dtest=com.acme.shop.CartTest#addsItems" in result["command"]


def test_a_nested_and_a_parameterized_method_run(tmp_path):
    repo = project(tmp_path, "shop")
    result = check(repo, ["com.acme.shop.CartTest.Receipts.receiptHasTotal()", "com.acme.shop.CartTest.discounts(int)"])
    assert result["passed"] is True and result["counts"]["passed"] == 3, result
    assert result["command"].count(" -B ") == 2  # one run per class: a method never shares a -Dtest with another class


def test_a_selector_that_matches_nothing_is_not_passed_although_maven_exits_zero(tmp_path, monkeypatch):
    repo = project(tmp_path, "shop")
    want = jc.Want("ghost", "", "com.acme.shop.CartTest", "nosuch")
    monkeypatch.setattr(jc, "parse_wants", lambda *a, **k: [want])
    result = check(repo, [f"{CART}::CartTest.addsItems()"])
    assert result["exit_code"] == 0
    assert result["passed"] is False and "CartTest.nosuch: no test ran" in text(result)


def test_a_stale_pass_is_not_this_runs_result(tmp_path):
    repo = project(tmp_path, "shop")
    assert check(repo)["passed"] is True  # leaves target/surefire-reports saying PASSED
    assert len(list((repo / "target" / "surefire-reports").glob("TEST-*.xml"))) == 3
    test = repo / CART
    test.write_text(test.read_text().replace("void addsItems() {", "void addsItems() {\n        new Cart().nosuch();"))
    result = check(repo)
    assert result["passed"] is False and result["counts"]["passed"] == 0
    assert "compilation failed" in text(result) and "3 report files from earlier runs ignored" in text(result)
    # and a run that selects nothing at all with the old reports still there
    test.write_text(test.read_text().replace("new Cart().nosuch();", ""))
    assert check(repo)["passed"] is True
    result = check(repo, [f"{CART}::CartTest.discounts(int)"])
    assert result["passed"] is True and result["counts"]["passed"] == 2 and "earlier runs ignored" in text(result)


def test_a_test_class_surefire_does_not_pick_up_is_not_passed(tmp_path):
    result = check(project(tmp_path, "not_picked_up"))
    assert result["exit_code"] == 0 and result["passed"] is False
    assert "com.acme.shop.CartChecks" in text(result)


def test_a_jvm_that_exits_inside_a_test_is_not_passed(tmp_path):
    result = check(project(tmp_path, "exit_zero"))
    assert result["passed"] is False and "System.exit" in text(result)


def test_a_compile_error_in_main_and_in_test_code_is_not_passed(tmp_path):
    for name, fragment in (("main_compile_error", "Cart.java:11"), ("test_compile_error", "CartTest.java:22")):
        result = check(project(tmp_path / name, name))
        assert result["passed"] is False and fragment in text(result)


def test_a_build_that_skips_its_tests_is_not_passed(tmp_path):
    result = check(project(tmp_path, "skip_tests"))
    assert result["exit_code"] == 0 and result["passed"] is False and "skips its tests" in text(result)


def test_a_failure_the_build_ignores_is_still_a_failure(tmp_path):
    result = check(project(tmp_path, "failure_ignored"))
    assert result["exit_code"] == 0 and result["passed"] is False and result["failure_count"] == 1


def test_a_skipped_method_that_was_asked_for_is_not_passed(tmp_path):
    result = check(project(tmp_path, "disabled"), [f"{CART}::CartTest.skipped()"])
    assert result["passed"] is False and "skipped or disabled" in text(result)


def test_a_two_module_build_whose_first_module_fails_is_not_passed_and_names_the_skipped_one(tmp_path):
    result = check(project(tmp_path, "multimod_fail"))
    assert result["passed"] is False
    assert "modules skipped" in text(result) and "no results in module web" in text(result)
    assert result["failures"][0].startswith("FAILED core/src/test/java/com/acme/core/CoreTest.java")
    assert result["counts"]["passed"] == 1  # util is independent: --fail-at-end still ran it


def test_a_module_that_depends_on_another_is_built_with_it(tmp_path):
    repo = project(tmp_path, "multimod_ok")
    result = check(repo, ["web/src/test/java/com/acme/web/WebTest.java"])
    assert result["passed"] is True and "-am" in result["command"], result
    assert any("-am" in n for n in result["notes"])


def test_the_same_class_in_two_modules_runs_only_the_one_asked_for(tmp_path):
    repo = project(tmp_path, "samename")
    ok = check(repo, ["a/src/test/java/com/acme/SameTest.java"])
    assert ok["passed"] is True and ok["counts"]["passed"] == 1, ok
    bad = check(repo, ["b/src/test/java/com/acme/SameTest.java"])
    assert bad["passed"] is False and bad["failures"][0].startswith("FAILED b/src/test/java/com/acme/SameTest.java")
    both = check(repo)
    assert both["counts"]["passed"] == 1 and both["counts"]["failed"] == 1


def test_a_directory_of_test_classes_runs_exactly_those(tmp_path):
    result = check(project(tmp_path, "shop"), ["src/test/java/com/acme/shop"])
    assert result["passed"] is True and result["counts"]["passed"] == 5


def test_overloads_are_run_together_and_the_verdict_says_so(tmp_path):
    """Maven selects by name: the other overload runs, fails, and is named as what it is (not folded into the one asked for)."""
    result = check(project(tmp_path, "overloads"), ["com.acme.shop.CartTest.check()"])
    assert result["passed"] is False and result["counts"] == {**result["counts"], "passed": 1, "failed": 1}, result
    assert "CartTest.check(TestInfo)" in result["failures"][0]
    assert any("overloaded" in n for n in result["notes"])


def test_a_run_killed_for_time_is_not_passed_and_leaves_no_process_behind(tmp_path):
    repo = project(tmp_path, "slow")
    result = check(repo, timeout=20)
    assert result["timed_out"] is True and result["passed"] is False and "timed out after 20s" in text(result)
    left = subprocess.run(["pgrep", "-f", str(repo)], capture_output=True, text=True)
    assert left.stdout.strip() == "", left.stdout


def test_the_repos_wrapper_is_the_command_when_it_has_one(tmp_path):
    repo = project(tmp_path, "shop")
    wrapper = repo / "mvnw"
    wrapper.write_text('#!/bin/sh\necho "wrapper ran" >&2\nexec mvn "$@"\n')
    wrapper.chmod(0o755)
    result = check(repo, ["com.acme.shop.CartTest.addsItems()"])
    assert result["passed"] is True and result["command"].startswith(str(wrapper))


def test_the_environment_that_skips_tests_is_a_named_problem(tmp_path, monkeypatch):
    monkeypatch.setenv("MAVEN_OPTS", "-DskipTests")
    result = check(project(tmp_path, "shop"))
    assert result["passed"] is False and "$MAVEN_OPTS sets -DskipTests" in text(result)


def test_the_captures_are_of_the_maven_this_runs(tmp_path):
    assert meta("pass")["maven"].startswith("Apache Maven 3.")


# ---- review round 1: requirements, predictions, the reactor, a race ----------------------------------------------------

OFF = "src/test/java/com/acme/shop/OffTest.java"


def test_a_disabled_class_asked_for_beside_its_module_is_not_passed_live(tmp_path):
    repo = project(tmp_path, "disabled")
    result = check(repo, [".", OFF])
    assert result["passed"] is False and "OffTest: skipped or disabled" in text(result)
    assert check(repo, [OFF])["passed"] is False
    assert check(repo, [CART, f"{CART}::CartTest.skipped()"])["passed"] is False


@pytest.mark.parametrize(
    "name,fragment",
    [
        ("inherited", "com.acme.shop.CartChecks"),
        ("meta_annotated", "com.acme.shop.CartChecks"),
        ("testdir_parent", "com.acme.core.CoreChecks"),
        ("testdir_helper", "com.acme.shop.ExtraChecks"),
        ("iface_chain", "com.acme.shop.CartChecks"),
        ("runwith", "com.acme.shop.CartSuite"),
    ],
)
def test_a_test_class_that_never_ran_and_ripple_could_not_read_off_the_source_is_not_passed_live(tmp_path, name, fragment):
    result = check(project(tmp_path, name))
    assert result["exit_code"] == 0 and result["passed"] is False and fragment in text(result), result


def test_a_class_ripple_cannot_decide_is_a_note_and_the_run_passes_live(tmp_path):
    result = check(project(tmp_path, "undecidable"))
    assert result["exit_code"] == 0 and result["passed"] is True and result["problems"] == [], result
    assert "cannot tell whether com.acme.shop.CartFailure holds tests" in text(result)


def test_a_benchmark_class_does_not_make_the_run_red_live(tmp_path):
    """JMH's annotations on a helper class: the shape that made every whole-build check of commons-lang read not passed."""
    result = check(project(tmp_path, "jmh"))
    assert result["exit_code"] == 0 and result["passed"] is True and result["problems"] == [], result
    assert "cannot tell whether com.acme.shop.CartBenchmark holds tests" in text(result)


def test_a_test_inherited_through_an_interface_that_extends_the_interface_holding_it_is_expected_live(tmp_path):
    """CartChecks fails when `mvn test -Dtest=CartChecks` runs it: its name misses the patterns, so the whole build never does."""
    repo = project(tmp_path, "iface_chain")
    result = check(repo)
    assert result["exit_code"] == 0 and result["passed"] is False
    assert "no results for 1 test class: com.acme.shop.CartChecks" in text(result)


def test_a_same_named_base_class_in_another_module_does_not_hide_a_class_that_never_ran_live(tmp_path):
    repo = project(tmp_path, "dupbase")
    whole = check(repo)
    assert whole["exit_code"] == 0 and whole["passed"] is False, whole
    assert "no results in module core for 1 test class: com.acme.core.CoreChecks" in text(whole)
    one = check(repo, ["core"])  # web's classes from the whole build are still on disk
    assert one["exit_code"] == 0 and one["passed"] is False, one
    assert "no results in module core for 1 test class: com.acme.core.CoreChecks" in text(one)


def test_a_class_that_is_not_a_test_does_not_make_the_run_red_live(tmp_path):
    result = check(project(tmp_path, "helper_class"))
    assert result["passed"] is True, result


def test_a_factory_and_a_repeated_test_run_by_method_name_live(tmp_path):
    result = check(project(tmp_path, "factory"), ["com.acme.shop.CartTest.dynamicTests()", "com.acme.shop.CartTest.repeated()"])
    assert result["counts"]["passed"] == 3 and result["counts"]["failed"] == 1 and not any("no test ran" in p for p in result["problems"])
    assert result["failures"][0].endswith("CartTest.dynamicTests()[2]: expected: <1> but was: <2>")


def test_a_module_is_never_run_against_an_installed_copy_of_its_sibling_live(tmp_path):
    """core-1.0.jar sits in the local repository with Core.one() == 1; the tree's Core returns 2. web's test asserts 1."""
    group = f"com.ripple.t{os.getpid()}x{abs(hash(str(tmp_path))) % 100000}"
    repo = project(tmp_path, "am_profile")
    for pom in repo.rglob("pom.xml"):
        pom.write_text(pom.read_text().replace("com.acme", group))
    stale = tmp_path / "stale"
    source = stale / "src" / "com" / "acme" / "core" / "Core.java"
    source.parent.mkdir(parents=True)
    source.write_text("package com.acme.core;\npublic class Core {\n    public int one() {\n        return 1;\n    }\n}\n")
    jdk = Path(JAVA_HOME) / "bin"
    classes = stale / "classes"
    classes.mkdir()
    subprocess.run([str(jdk / "javac"), "-d", str(classes), str(source)], check=True)
    jar = stale / "core-1.0.jar"
    subprocess.run([str(jdk / "jar"), "cf", str(jar), "-C", str(classes), "."], check=True)
    base = Path(M2) / Path(*group.split("."))
    try:
        installs = (
            ("core", {"core-1.0.jar": jar.read_bytes(), "core-1.0.pom": (repo / "core" / "pom.xml").read_bytes()}),
            ("root", {"root-1.0.pom": (repo / "pom.xml").read_bytes()}),
        )
        for artifact, files in installs:
            directory = base / artifact / "1.0"
            directory.mkdir(parents=True)
            for name, data in files.items():
                (directory / name).write_bytes(data)
        core = repo / "core" / "src" / "main" / "java" / "com" / "acme" / "core" / "Core.java"
        core.write_text(core.read_text().replace("return 1;", "return 2;"))
        assert check(repo)["passed"] is False  # the tree: Web.one() is 2
        result = check(repo, ["web/src/test/java/com/acme/web/WebTest.java"])
        assert result["passed"] is False, result
        assert "-pl web -am" in result["command"]
    finally:
        shutil.rmtree(Path(M2) / Path(*group.split(".")[:3]), ignore_errors=True)


def test_two_checks_racing_in_one_repo_each_get_their_own_verdict_live(tmp_path):
    import threading

    repo = project(tmp_path, "failure_ignored")  # testFailureIgnore: Maven exits 0 even when a test fails
    out = {}

    def whole():
        out["whole"] = check(repo)

    def one():
        out["one"] = check(repo, ["com.acme.shop.CartTest.addsItems()"])

    threads = [threading.Thread(target=whole), threading.Thread(target=one)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(600)
    assert out["whole"]["passed"] is False and out["whole"]["failure_count"] == 1, out["whole"]
    assert out["one"]["passed"] is True and out["one"]["counts"]["passed"] == 1, out["one"]
