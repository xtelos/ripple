"""What `check` asks Maven to run for each request, and what it refuses before any process starts.

No JDK needed: the request is turned into the exact argv a capture recorded (tests/fixtures/java_check), and
everything a caller can write is refused or turned into nothing but identifiers.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest
from java_check_support import PROJECTS, make_repo, meta, names

from ripple import checker
from ripple import java_check as jc
from ripple.profile import JAVA

CART = "src/test/java/com/acme/shop/CartTest.java"


def invocations(repo: Path, tests: list[str]) -> list[jc.Invocation]:
    project = jc.find_project(repo)
    return jc.plan(project, jc.parse_wants(repo, project, jc.normalize(repo, tests)))


def copy(tmp_path: Path, project: str) -> Path:
    repo = tmp_path / "repo"
    shutil.copytree(PROJECTS / project, repo)
    return repo.resolve()


@pytest.fixture
def no_process(monkeypatch):
    """Any attempt to start a process fails the test: a refused request must be refused before one."""

    def boom(*args, **kwargs):
        raise AssertionError(f"a process was started: {args}")

    monkeypatch.setattr(jc, "run_process", boom)
    monkeypatch.setattr(checker, "run_process", boom)
    monkeypatch.setattr(jc, "choose_tool", boom)


# ---- the command for each request -------------------------------------------------------------------


REQUESTS = [n for n in names() if meta(n)["request"] and not meta(n)["before"]]


@pytest.mark.parametrize("name", REQUESTS)
def test_the_request_builds_exactly_the_captured_command(tmp_path, name):
    data = meta(name)
    repo = make_repo(tmp_path, name)
    runs = invocations(repo, data["request"])
    assert len(runs) == 1, [r.argv() for r in runs]
    assert runs[0].argv() == data["argv"]


def test_no_request_is_the_whole_reactor(tmp_path):
    repo = copy(tmp_path, "multimod_fail")
    assert [r.argv() for r in invocations(repo, [])] == [["mvn", "-B", "--fail-at-end", "test"]]


def test_the_default_profile_command_is_untouched_and_the_runner_is_java_check():
    assert checker.runner_for(JAVA) is jc
    assert checker.RUNNERS["go"] == ".go_check"


# ---- ids as `impact` lists them ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "given,expected",
    [
        ("com.acme.shop.CartTest.addsItems()", f"{CART}::CartTest.addsItems()"),
        ("com.acme.shop.CartTest.discounts(int)", f"{CART}::CartTest.discounts(int)"),
        ("com.acme.shop.CartTest.Receipts.receiptHasTotal()", f"{CART}::CartTest.Receipts.receiptHasTotal()"),
        ("com.acme.shop.CartTest.addsItems", f"{CART}::CartTest.addsItems"),  # a method with no types written
        ("com.acme.shop.CartTest", f"{CART}::CartTest"),  # a class
        ("com.acme.shop.CartTest.Receipts", f"{CART}::CartTest.Receipts"),
        ("com.acme.shop.CartTest.addsItems()#2", f"{CART}::CartTest.addsItems()"),  # ripple's suffix for two identical signatures
    ],
)
def test_ids_ripple_lists_are_anchored_to_their_file(tmp_path, given, expected):
    repo = copy(tmp_path, "shop")
    assert jc.normalize(repo, [given]) == [expected]
    checker.check_tests(repo, jc.normalize(repo, [given]))  # the next step accepts them


def test_the_id_impact_prints_for_a_test_method_is_accepted(tmp_path):
    pytest.importorskip("tree_sitter_java")
    from ripple import queries
    from ripple.cache import load_repo

    repo = copy(tmp_path, "shop")
    graph = load_repo(str(repo), lang="java").graph
    impact = queries.impact(graph, "com.acme.shop.Cart.add(String,int)")
    listed = [e["symbol"] for entries in impact["by_file"].values() for e in entries]
    assert listed == ["com.acme.shop.CartTest.addsItems()"]
    assert jc.normalize(repo, listed) == [f"{CART}::CartTest.addsItems()"]
    other = queries.impact(graph, "com.acme.shop.Cart.total()")
    for symbol in (e["symbol"] for entries in other["by_file"].values() for e in entries):
        jc.normalize(repo, [symbol])  # every test method it lists resolves


def test_a_file_and_a_member_form_are_accepted_as_given(tmp_path):
    repo = copy(tmp_path, "shop")
    assert jc.normalize(repo, [CART, f"{CART}::CartTest.addsItems()", "src/test/java/com/acme/shop"]) == [
        CART,
        f"{CART}::CartTest.addsItems()",
        "src/test/java/com/acme/shop",
    ]


# ---- refused before any process starts --------------------------------------------------------------------


REFUSED = [
    "-Dtest=CartTest#addsItems",
    "-DskipTests",
    "--projects=x",
    "-pl",
    "-T4",
    "@args.txt",
    "@/etc/passwd",
    "../outside/Foo.java",
    "/etc/passwd",
    "/tmp",
    "src/../../outside",
    f"{CART}::../x",
    "com.acme.shop.CartTest#addsItems",  # Maven's own form is not ripple's
    "com.acme.shop.CartTest.addsItems(",
    "com.acme.shop.CartTest.addsItems()x",
    "com.acme.shop..CartTest",
    "a.b.C.m(int;rm -rf)",
    "com.acme.shop.CartTest.nosuch()",
    "com.acme.shop.Nope.addsItems()",
    "com.acme.shop.Cart.add(String,int)",  # a main method: not a test
    "",
    "Foo",
    "nosuch/Dir",
    "pom.xml",
    f"{CART}::CartTest.addsItems,nosuch",
    f"{CART}::CartTest.addsItems+discounts",
    f"{CART}::CartTest.add*",
    f"{CART}::*",
    f"{CART}::CartTest.Receipts.nosuch",
    f"{CART}::Nope",
    f"{CART}::CartTest.addsItems\n",
    f"{CART}::CartTest.addsItems\x00",
]


@pytest.mark.parametrize("given", REFUSED)
def test_every_argument_that_is_not_a_test_is_refused_before_a_process_starts(tmp_path, no_process, given):
    repo = copy(tmp_path, "shop")
    with pytest.raises(ValueError):
        checker.run_check(repo, tests=[given], profile=JAVA)


@pytest.mark.parametrize("given", [None, 3, ["x"], b"x", {"a": 1}])
def test_a_test_that_is_not_a_string_is_refused_before_anything_is_read(tmp_path, no_process, given, monkeypatch):
    repo = copy(tmp_path, "shop")
    monkeypatch.setattr(jc, "read_java_file", lambda path: (_ for _ in ()).throw(AssertionError("a file was read")))
    with pytest.raises(ValueError):
        checker.run_check(repo, tests=[given], profile=JAVA)


def test_a_path_outside_the_repo_is_refused_without_reading_it(tmp_path, no_process, monkeypatch):
    outside = tmp_path / "outside"
    (outside / "src" / "test" / "java").mkdir(parents=True)
    (outside / "src" / "test" / "java" / "FooTest.java").write_text("package x;\nclass FooTest { @org.junit.Test void a() {} }\n")
    repo = copy(tmp_path / "in", "shop")
    reads = []
    monkeypatch.setattr(jc, "read_java_file", lambda path: reads.append(path))
    monkeypatch.setattr(jc.os, "walk", lambda *a, **k: reads.append(a) or iter(()))
    link = repo / "link"
    link.symlink_to(outside)
    for given in (str(outside), "../outside", "link", "link/src/test/java/FooTest.java", f"{outside}/src/test/java/FooTest.java::FooTest.a"):
        with pytest.raises(ValueError):
            checker.run_check(repo, tests=[given], profile=JAVA)
    assert reads == []


def test_parse_wants_proves_the_path_is_inside_the_repo_without_check_tests(tmp_path, monkeypatch):
    """run() can be called by something that skipped checker.check_tests: the runner proves containment itself."""
    repo = copy(tmp_path / "in", "shop")
    project = jc.find_project(repo)
    outside = tmp_path / "out"
    outside.mkdir()
    (outside / "FooTest.java").write_text("class FooTest {}\n")
    monkeypatch.setattr(jc, "read_java_file", lambda path: (_ for _ in ()).throw(AssertionError("read")))
    with pytest.raises(ValueError, match="inside"):
        jc.parse_wants(repo, project, [f"../out/FooTest.java"])
    with pytest.raises(ValueError, match="inside"):
        jc.parse_wants(repo, project, [str(outside)])


def test_the_option_check_runs_in_front_of_the_runner_for_every_form(tmp_path, no_process):
    repo = copy(tmp_path, "shop")
    for given in ("-x", "@f", "--"):
        with pytest.raises(ValueError):
            checker.run_check(repo, tests=[given], profile=JAVA)


# ---- nothing a caller writes becomes a property or a flag --------------------------------------------------

ALLOWED_FLAGS = {"-B", "--fail-at-end", "-pl", "-am", "test", "-Dsurefire.failIfNoSpecifiedTests=false"}


def flags_of(argv: list[str]) -> list[str]:
    return [a for a in argv[1:] if a not in ALLOWED_FLAGS and not a.startswith("-Dtest=")]


@pytest.mark.parametrize("name", REQUESTS)
def test_ripples_own_flags_are_the_only_flags(tmp_path, name):
    repo = make_repo(tmp_path, name)
    for inv in invocations(repo, meta(name)["request"]):
        argv = inv.argv()
        flags = [a for a in flags_of(argv) if a.startswith("-")]
        assert flags == []
        # the -pl value is a module path read from a pom, the -Dtest value is identifiers and `.`, `$`, `#`, `+`, `,`
        for a in argv:
            if a.startswith("-Dtest="):
                assert set(a[len("-Dtest="):]) <= set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_$.#+,"), a


def test_the_selector_is_made_of_names_read_from_the_source_not_from_the_argument(tmp_path):
    repo = copy(tmp_path, "shop")
    (inv,) = invocations(repo, [f"{CART}::CartTest.addsItems()"])
    assert inv.selectors == ("com.acme.shop.CartTest#addsItems",)
    # the parameter types the caller wrote are for telling overloads apart in the verdict and never reach the command
    (inv,) = invocations(repo, [f"{CART}::CartTest.discounts(int)"])
    assert inv.selectors == ("com.acme.shop.CartTest#discounts",)
    assert "int" not in " ".join(inv.argv())


def test_a_hostile_parameter_list_never_reaches_the_command(tmp_path):
    repo = copy(tmp_path, "shop")
    for types in ("int", "int,String", "List<String>", "a.b.C[]", "int ... xs"):
        (inv,) = invocations(repo, [f"{CART}::CartTest.discounts({types})"])
        assert inv.argv()[-1] == "-Dtest=com.acme.shop.CartTest#discounts" if False else "(" not in " ".join(inv.argv())


# ---- a selector never widens the run ---------------------------------------------------------------------


def write_two_classes(repo: Path) -> None:
    body = "package com.acme.shop;\nimport org.junit.jupiter.api.Test;\nclass {name} {{\n    @Test void m1() {{}}\n    @Test void m2() {{}}\n}}\n"
    for name in ("ATest", "BTest"):
        (repo / "src" / "test" / "java" / "com" / "acme" / "shop" / f"{name}.java").write_text(body.format(name=name))


def test_methods_of_different_classes_never_share_one_dtest(tmp_path):
    """`-Dtest=A#m1,B#m2` is a cross product (every filter on every class) in some surefire versions: one run per class."""
    repo = copy(tmp_path, "shop")
    write_two_classes(repo)
    base = "src/test/java/com/acme/shop"
    runs = invocations(repo, [f"{base}/ATest.java::ATest.m1", f"{base}/BTest.java::BTest.m2", f"{base}/ATest.java::ATest.m2"])
    assert [r.selectors for r in runs] == [("com.acme.shop.ATest#m1+m2",), ("com.acme.shop.BTest#m2",)]


def test_a_method_and_its_whole_class_are_the_whole_class(tmp_path):
    repo = copy(tmp_path, "shop")
    write_two_classes(repo)
    base = "src/test/java/com/acme/shop"
    runs = invocations(repo, [f"{base}/ATest.java::ATest.m1", f"{base}/ATest.java"])
    assert [r.selectors for r in runs] == [("com.acme.shop.ATest",)]


def test_whole_classes_share_a_run_because_a_class_name_has_no_filter_to_cross(tmp_path):
    repo = copy(tmp_path, "shop")
    write_two_classes(repo)
    base = "src/test/java/com/acme/shop"
    runs = invocations(repo, [f"{base}/ATest.java", f"{base}/BTest.java"])
    assert [r.selectors for r in runs] == [("com.acme.shop.ATest", "com.acme.shop.BTest")]


def test_a_nested_class_method_and_an_outer_method_are_two_runs(tmp_path):
    repo = copy(tmp_path, "shop")
    runs = invocations(repo, [f"{CART}::CartTest.Receipts.receiptHasTotal()", f"{CART}::CartTest.addsItems()"])
    assert sorted(r.selectors for r in runs) == [("com.acme.shop.CartTest#addsItems",), ("com.acme.shop.CartTest$Receipts#receiptHasTotal",)]


def test_the_same_class_name_in_two_modules_is_two_runs_each_limited_to_its_module(tmp_path):
    repo = copy(tmp_path, "samename")
    base = "src/test/java/com/acme/SameTest.java"
    runs = invocations(repo, [f"a/{base}", f"b/{base}"])
    assert [r.argv() for r in runs] == [
        ["mvn", "-B", "--fail-at-end", "-pl", "a", "-am", "test", "-Dtest=com.acme.SameTest", "-Dsurefire.failIfNoSpecifiedTests=false"],
        ["mvn", "-B", "--fail-at-end", "-pl", "b", "-am", "test", "-Dtest=com.acme.SameTest", "-Dsurefire.failIfNoSpecifiedTests=false"],
    ]


def test_the_id_of_a_class_that_two_modules_have_is_refused_not_guessed(tmp_path):
    repo = copy(tmp_path, "samename")
    with pytest.raises(ValueError, match="matches 2 files"):
        jc.normalize(repo, ["com.acme.SameTest.same()"])


def test_a_module_that_depends_on_another_is_built_with_it_and_one_that_does_not_is_not(tmp_path):
    repo = copy(tmp_path, "multimod_fail")
    web, util = "web/src/test/java/com/acme/web/WebTest.java", "util/src/test/java/com/acme/util/UtilTest.java"
    (inv,) = invocations(repo, [web])
    assert inv.am and "-am" in inv.argv() and "-Dsurefire.failIfNoSpecifiedTests=false" in inv.argv()
    (inv,) = invocations(repo, [util])
    # util needs nothing from core, and is built with `-am` all the same: ripple does not guess what a module needs
    assert inv.am and inv.argv() == ["mvn", "-B", "--fail-at-end", "-pl", "util", "-am", "test", "-Dtest=com.acme.util.UtilTest", "-Dsurefire.failIfNoSpecifiedTests=false"]


# ---- directories ------------------------------------------------------------------------------------------


def write_tree(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


def pom(artifact: str, modules: list[str] | None = None, packaging: str = "jar") -> str:
    listed = "<modules>" + "".join(f"<module>{m}</module>" for m in modules) + "</modules>" if modules else ""
    return (
        '<project xmlns="http://maven.apache.org/POM/4.0.0"><modelVersion>4.0.0</modelVersion>'
        f"<groupId>g</groupId><artifactId>{artifact}</artifactId><version>1</version><packaging>{packaging}</packaging>{listed}</project>"
    )


TEST = "package p;\nimport org.junit.jupiter.api.Test;\nclass {name}Test {{\n    @Test void t() {{}}\n}}\n"


@pytest.fixture
def nested(tmp_path):
    """A root whose modules sit under `libs/` and `apps/`, and `libs/` itself has no pom: a directory of modules only."""
    repo = tmp_path / "nested"
    write_tree(
        repo,
        {
            "pom.xml": pom("root", ["libs/a", "libs/b", "apps/c"], "pom"),
            "libs/a/pom.xml": pom("a"),
            "libs/a/src/test/java/p/ATest.java": TEST.format(name="A"),
            "libs/b/pom.xml": pom("b"),
            "libs/b/src/test/java/p/BTest.java": TEST.format(name="B"),
            "apps/c/pom.xml": pom("c"),
            "apps/c/src/test/java/p/CTest.java": TEST.format(name="C"),
            "apps/c/src/test/java/p/sub/DTest.java": TEST.format(name="D").replace("package p;", "package p.sub;"),
            "docs/readme.txt": "x",
        },
    )
    return repo.resolve()


def test_a_directory_of_modules_is_each_module_never_the_directory(nested):
    """`-pl libs` would match no project and fail every time: the modules below it are named instead."""
    (inv,) = invocations(nested, ["libs"])
    assert inv.argv() == ["mvn", "-B", "--fail-at-end", "-pl", "libs/a,libs/b", "-am", "test"]
    assert not any(a == "libs" for a in inv.argv())


def test_a_module_directory_is_that_module_whole(nested):
    (inv,) = invocations(nested, ["apps/c"])
    assert inv.argv() == ["mvn", "-B", "--fail-at-end", "-pl", "apps/c", "-am", "test"]


def test_a_test_source_directory_is_the_classes_under_it(nested):
    (inv,) = invocations(nested, ["apps/c/src/test/java/p/sub"])
    assert inv.argv() == ["mvn", "-B", "--fail-at-end", "-pl", "apps/c", "-am", "test", "-Dtest=p.sub.DTest", "-Dsurefire.failIfNoSpecifiedTests=false"]
    (inv,) = invocations(nested, ["apps/c/src/test/java/p"])
    assert inv.selectors == ("p.CTest", "p.sub.DTest")


def test_the_module_src_directory_is_the_module_whole(nested):
    (inv,) = invocations(nested, ["apps/c/src"])
    assert inv.argv() == ["mvn", "-B", "--fail-at-end", "-pl", "apps/c", "-am", "test"]


def test_the_repo_root_is_the_whole_build(nested):
    (inv,) = invocations(nested, ["."])
    assert inv.modules is None and inv.argv() == ["mvn", "-B", "--fail-at-end", "test"]


@pytest.mark.parametrize("given", ["docs", "apps/c/src/main", "libs/a/src/main/java"])
def test_a_directory_that_holds_no_tests_is_refused_not_run(nested, given):
    (nested / "libs/a/src/main/java").mkdir(parents=True, exist_ok=True)
    (nested / "apps/c/src/main").mkdir(parents=True, exist_ok=True)
    with pytest.raises(ValueError):
        invocations(nested, [given])


def test_a_directory_in_the_test_sources_with_no_test_class_is_refused(nested):
    (nested / "apps/c/src/test/java/p/empty").mkdir()
    with pytest.raises(ValueError, match="no JUnit test class"):
        invocations(nested, ["apps/c/src/test/java/p/empty"])


def test_a_module_the_root_pom_does_not_list_is_not_part_of_the_build(nested):
    write_tree(nested, {"extra/pom.xml": pom("extra"), "extra/src/test/java/p/XTest.java": TEST.format(name="X")})
    with pytest.raises(ValueError):
        invocations(nested, ["extra/src/test/java/p/XTest.java"])


def test_more_classes_than_one_value_can_carry_is_refused(nested, monkeypatch):
    monkeypatch.setattr(jc, "MAX_CLASSES", 1)
    with pytest.raises(ValueError, match="narrower"):
        invocations(nested, ["apps/c/src/test/java/p"])


# ---- which Maven ---------------------------------------------------------------------------------------------


def test_the_repos_own_wrapper_is_preferred_when_it_is_in_the_repo(tmp_path):
    repo = copy(tmp_path, "shop")
    wrapper = repo / "mvnw"
    wrapper.write_text("#!/bin/sh\necho hi\n")
    wrapper.chmod(0o755)
    assert jc.choose_tool(repo) == [str(wrapper)]


def test_a_wrapper_without_the_executable_bit_is_run_by_sh(tmp_path):
    repo = copy(tmp_path, "shop")
    (repo / "mvnw").write_text("#!/bin/sh\necho hi\n")
    (repo / "mvnw").chmod(0o644)
    assert jc.choose_tool(repo) == [shutil.which("sh"), str(repo / "mvnw")]


def test_a_wrapper_that_leads_outside_the_repo_is_refused_never_run(tmp_path, monkeypatch):
    marker = tmp_path / "wrapper-ran"
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "mvnw").write_text(f"#!/bin/sh\ntouch {marker}\n")
    (outside / "mvnw").chmod(0o755)
    repo = copy(tmp_path / "in", "shop")
    (repo / "mvnw").symlink_to(outside / "mvnw")
    monkeypatch.setattr(jc, "run_process", lambda *a, **k: (_ for _ in ()).throw(AssertionError("a process was started")))
    with pytest.raises(ValueError, match="not a file inside the repository"):
        checker.run_check(repo, tests=[], profile=JAVA)
    assert not marker.exists()
    (repo / "mvnw").unlink()
    (repo / "mvnw").mkdir()  # a directory is not a wrapper either
    with pytest.raises(ValueError, match="not a file inside the repository"):
        jc.choose_tool(repo)


def test_without_a_wrapper_mvn_on_path_is_used_and_none_is_an_error(tmp_path, monkeypatch):
    repo = copy(tmp_path, "shop")
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/mvn" if name == "mvn" else None)
    assert jc.choose_tool(repo) == ["/usr/bin/mvn"]
    monkeypatch.setattr(shutil, "which", lambda name: None)
    with pytest.raises(ValueError, match="mvn"):
        jc.choose_tool(repo)


# ---- the build files --------------------------------------------------------------------------------------


def test_a_repo_with_no_root_pom_is_refused_with_what_to_pass(tmp_path, no_process):
    repo = tmp_path / "r"
    repo.mkdir()
    (repo / "A.java").write_text("class A {}\n")
    with pytest.raises(ValueError, match="no pom.xml at the repository root"):
        checker.run_check(repo, tests=[], profile=JAVA)


def test_gradle_alone_is_not_run_as_maven_and_without_a_launcher_is_refused(tmp_path, no_process, monkeypatch):
    # was: refused as "only Maven". Gradle roots now go to gradle_check.py (tests/test_check_gradle_*.py); with neither
    # gradlew in the repo nor gradle on PATH there is nothing to run, and the refusal says so.
    repo = tmp_path / "r"
    repo.mkdir()
    (repo / "build.gradle").write_text("plugins { id 'java' }\n")
    monkeypatch.setattr(shutil, "which", lambda name: None)
    with pytest.raises(ValueError, match="neither .gradlew. in the repository nor .gradle. on PATH"):
        checker.run_check(repo, tests=[], profile=JAVA)


def test_maven_and_gradle_together_are_ambiguous(tmp_path, no_process):
    repo = copy(tmp_path, "shop")
    (repo / "build.gradle").write_text("plugins { id 'java' }\n")
    with pytest.raises(ValueError, match="both at the repository root"):
        checker.run_check(repo, tests=[], profile=JAVA)


def test_a_command_given_by_the_user_still_bypasses_the_runner(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(checker, "run_process", lambda argv, cwd, timeout, merge_stderr=True: seen.append(argv) or checker.Process("1 passed", "", 0, False))
    repo = copy(tmp_path, "shop")
    result = checker.run_check(repo, command=["echo", "hi"], profile=JAVA)
    assert seen == [["echo", "hi"]] and result["passed"] is True


# ---- the source reader ---------------------------------------------------------------------------------------


def test_the_reader_finds_tests_nested_classes_and_overloads():
    java = jc.read_java(
        "package a.b;\n"
        "// class Fake { @Test void no() {} }\n"
        'class Real { String s = "class Not { @Test void x() {} }"; \n'
        "  @Test void one() {}\n  @Test\n  public void two(int x) {}\n  @org.junit.Test public void one(String s) {}\n"
        "  void helper() {}\n"
        "  @Nested class In { @ParameterizedTest @ValueSource(ints = {1, 2}) void many(int i) {} }\n"
        "  Runnable r = new Runnable() { public void run() { class Local { @Test void z() {} } } };\n"
        "}\n"
        "abstract class Base { @Test void inherited() {} }\n"
        "interface Marker {}\n"
    )
    by = {".".join(c.chain): c for c in java.classes}
    assert set(by) == {"Real", "Real.In", "Base", "Marker"}
    assert java.package == "a.b" and java.binary(by["Real.In"]) == "a.b.Real$In"
    assert by["Real"].tests == ("one", "two", "one") and by["Real"].nested_tests
    assert by["Real.In"].tests == ("many",)
    assert by["Base"].abstract and by["Marker"].abstract and not by["Real"].abstract
    assert "helper" in by["Real"].mentions and "helper" not in by["Real"].tests


def test_the_reader_reads_the_default_package_and_junit3():
    java = jc.read_java("public class OldTest extends TestCase {\n  public void testA() {}\n  public void helper() {}\n}\n")
    assert java.package == "" and java.binary(java.classes[0]) == "OldTest"
    assert java.classes[0].tests == ("testA",)


def test_every_module_of_a_directory_run_needs_evidence_that_its_tests_ran(nested):
    """A directory of modules that Maven builds and reports success for, with no report from `libs/b`: not passed."""
    project = jc.find_project(nested)
    (inv,) = invocations(nested, ["libs"])
    run = jc.Run(inv, "[INFO] BUILD SUCCESS\n", 0, cases=[jc.Case("libs/a", "p.ATest", "t", "pass")])
    result = jc.judge(nested, project, [run], "mvn", 0.1, 300)
    assert result["passed"] is False
    assert any("no results in module libs/b for 1 test class: p.BTest" in p for p in result["problems"]), result
    run.cases.append(jc.Case("libs/b", "p.BTest", "t", "pass"))
    result = jc.judge(nested, project, [run], "mvn", 0.1, 300)
    assert not any("no results in module libs/b" in p for p in result["problems"])  # (no compiled classes here: that is its own problem)
