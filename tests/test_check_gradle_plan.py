"""Which build `check` runs for Java, what it asks Gradle for each request, and what it refuses before any process starts.

No JDK or Gradle needed: the request is turned into the exact argv the live tests ran against real Gradle 9.9.1, and
everything a caller can write is refused or turned into nothing but identifiers.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from gradle_check_support import install, lines, multi, project, record

from ripple import checker, gradle_check
from ripple import java_check as jc
from ripple.profile import JAVA

CART = "src/test/java/com/acme/shop/CartTest.java"


@pytest.fixture
def no_process(monkeypatch):
    """Any attempt to start a process fails the test: a refused request must be refused before one."""

    def boom(*args, **kwargs):
        raise AssertionError(f"a process was started: {args}")

    for module in (jc, checker, gradle_check):
        monkeypatch.setattr(module, "run_process", boom)


# ---- which build ----------------------------------------------------------------------------------------


def test_maven_and_gradle_together_are_refused_and_name_the_choice(tmp_path, no_process):
    repo = project(tmp_path, "shop")
    (repo / "pom.xml").write_text("<project/>")
    for name in ("build.gradle", "settings.gradle"):
        with pytest.raises(ValueError, match=rf"pom.xml and {name} are both at the repository root.*--test-command"):
            checker.run_check(repo, tests=[], profile=JAVA)
        (repo / name).rename(repo / f"{name}.off")
    assert jc.build_tool(repo) == "maven"


def test_a_kotlin_dsl_root_is_gradle_too(tmp_path):
    repo = tmp_path / "r"
    repo.mkdir()
    (repo / "build.gradle.kts").write_text("")
    assert jc.build_tool(repo) == "gradle"
    (repo / "pom.xml").write_text("<project/>")
    with pytest.raises(ValueError, match="build.gradle.kts and|pom.xml and build.gradle.kts"):
        jc.build_tool(repo)


def test_a_root_with_neither_is_maven_so_the_existing_refusal_applies(tmp_path, no_process):
    repo = tmp_path / "r"
    repo.mkdir()
    assert jc.build_tool(repo) == "maven"
    with pytest.raises(ValueError, match="no pom.xml at the repository root"):
        checker.run_check(repo, tests=[], profile=JAVA)


def test_a_gradle_root_goes_to_the_gradle_runner_and_a_pom_root_does_not(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(gradle_check, "run", lambda repo, tests, timeout: seen.append(("gradle", tests)) or {"passed": True})
    monkeypatch.setattr(jc, "find_project", lambda repo: seen.append(("maven",)) or (_ for _ in ()).throw(RuntimeError("maven")))
    repo = project(tmp_path, "shop")
    assert checker.run_check(repo, tests=[CART], profile=JAVA) == {"passed": True}
    assert seen == [("gradle", [f"{CART}"])]


def test_a_gradle_root_with_no_launcher_is_refused_naming_both(tmp_path, monkeypatch, no_process):
    repo = project(tmp_path, "shop")
    monkeypatch.setattr(shutil, "which", lambda name: None)
    with pytest.raises(ValueError, match="neither `gradlew` in the repository nor `gradle` on PATH"):
        checker.run_check(repo, tests=[], profile=JAVA)


def test_a_command_given_by_the_user_still_bypasses_the_runner(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(checker, "run_process", lambda argv, cwd, timeout, merge_stderr=True: seen.append(argv) or checker.Process("1 passed", "", 0, False))
    result = checker.run_check(project(tmp_path, "shop"), command=["echo", "hi"], profile=JAVA)
    assert seen == [["echo", "hi"]] and result["passed"] is True


# ---- which Gradle ---------------------------------------------------------------------------------------


def test_the_repos_own_wrapper_is_preferred_when_it_is_in_the_repo(tmp_path):
    repo = project(tmp_path, "shop")
    wrapper = repo / "gradlew"
    wrapper.write_text("#!/bin/sh\necho hi\n")
    wrapper.chmod(0o755)
    assert gradle_check.choose_tool(repo) == [str(wrapper)]


def test_a_wrapper_without_the_executable_bit_is_run_by_sh(tmp_path):
    repo = project(tmp_path, "shop")
    (repo / "gradlew").write_text("#!/bin/sh\necho hi\n")
    (repo / "gradlew").chmod(0o644)
    assert gradle_check.choose_tool(repo) == [shutil.which("sh"), str(repo / "gradlew")]


def test_a_wrapper_that_leads_outside_the_repo_is_refused_never_run(tmp_path, no_process):
    marker = tmp_path / "wrapper-ran"
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "gradlew").write_text(f"#!/bin/sh\ntouch {marker}\n")
    (outside / "gradlew").chmod(0o755)
    repo = project(tmp_path / "in", "shop")
    (repo / "gradlew").symlink_to(outside / "gradlew")
    with pytest.raises(ValueError, match="not a file inside the repository"):
        checker.run_check(repo, tests=[], profile=JAVA)
    assert not marker.exists()
    (repo / "gradlew").unlink()
    (repo / "gradlew").mkdir()  # a directory is not a wrapper either
    with pytest.raises(ValueError, match="not a file inside the repository"):
        gradle_check.choose_tool(repo)


def test_without_a_wrapper_gradle_on_path_is_used_and_none_is_an_error(tmp_path, monkeypatch):
    repo = project(tmp_path, "shop")
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/gradle" if name == "gradle" else None)
    assert gradle_check.choose_tool(repo) == ["/usr/bin/gradle"]
    monkeypatch.setattr(shutil, "which", lambda name: None)
    with pytest.raises(ValueError, match="gradle"):
        gradle_check.choose_tool(repo)


# ---- the command ------------------------------------------------------------------------------------------


FLAGS = "--no-daemon --console=plain --no-build-cache --no-configuration-cache --no-configure-on-demand --continue"


def wants_of(repo: Path, tests: list[str], modules: dict[str, str] | None = None):
    """The planned invocation for a request, with the projects as a build of `modules` ({dir: path}) would report them."""
    modules = modules or {"": ":"}
    manifest = gradle_check.Manifest(graph=True)
    for rel, path in modules.items():
        base = str(repo / rel) if rel else str(repo)
        manifest.projects.append(
            gradle_check.Project(
                path,
                base,
                [("test", (f"{base}/src/test/java",), (f"{base}/build/classes/java/test",))],
                [gradle_check.TestTask(f"{path}:test" if path != ":" else ":test", "test", path, False, True, False, (f"{base}/build/classes/java/test",), (), ())],
            )
        )
    built, paths = gradle_check.build_project(repo, manifest)
    wants = gradle_check._parse_wants(repo, built, gradle_check.normalize(repo, tests))
    return gradle_check.plan(paths, wants)


def test_no_request_is_every_projects_test_task():
    assert gradle_check.plan({}, []).argv(["gradle"], "I") == ["gradle", *FLAGS.split(), "-I", "I", "test"]


@pytest.mark.parametrize(
    "tests,tail",
    [
        (["com.acme.shop.CartTest.addsItems()"], [":test", "--tests", "com.acme.shop.CartTest.addsItems"]),
        (["com.acme.shop.CartTest.discounts(int)"], [":test", "--tests", "com.acme.shop.CartTest.discounts"]),
        (["com.acme.shop.CartTest.Receipts.receiptHasTotal()"], [":test", "--tests", "com.acme.shop.CartTest$Receipts.receiptHasTotal"]),
        ([CART], [":test", "--tests", "com.acme.shop.CartTest"]),
        ([f"{CART}::CartTest.Receipts"], [":test", "--tests", "com.acme.shop.CartTest$Receipts"]),
        ([".", CART], [":test"]),
        (["src/test/java/com/acme/shop"], [":test", "--tests", "com.acme.shop.CartTest", "--tests", "com.acme.shop.CartTests"]),
        ([CART, "com.acme.shop.CartTest.addsItems()"], [":test", "--tests", "com.acme.shop.CartTest"]),
        (
            ["com.acme.shop.CartTest.addsItems()", "com.acme.shop.CartTests.extraWorks()"],
            [":test", "--tests", "com.acme.shop.CartTest.addsItems", "--tests", "com.acme.shop.CartTests.extraWorks"],
        ),
    ],
)
def test_the_request_builds_exactly_these_tasks_and_filters(tmp_path, tests, tail):
    repo = project(tmp_path, "shop")
    invocation = wants_of(repo, tests)
    assert invocation.argv(["gradle"], "I")[len(["gradle", *FLAGS.split(), "-I", "I"]) :] == tail
    assert len(invocation.wants) >= 1


def test_two_projects_are_two_tasks_each_with_its_own_filters(tmp_path):
    repo = multi(tmp_path)
    modules = {"core": ":core", "web": ":web"}
    one = wants_of(repo, ["com.acme.core.CoreTest.works()", "com.acme.web.WebTest.works()"], modules)
    assert one.tasks == ((":core:test", ("com.acme.core.CoreTest.works",)), (":web:test", ("com.acme.web.WebTest.works",)))
    assert wants_of(repo, ["core"], modules).tasks == ((":core:test", ()),)
    assert wants_of(repo, ["."], modules).tasks == ((":core:test", ()), (":web:test", ()))
    assert wants_of(repo, ["web/src/test/java/com/acme/web/WebTest.java"], modules).tasks == ((":web:test", ("com.acme.web.WebTest",)),)


def test_a_project_path_that_cannot_be_a_task_name_is_refused():
    with pytest.raises(ValueError, match="cannot be named in a task"):
        gradle_check.plan({"a": ":a --foo"}, [jc.Want("x", "a", "p.T")])
    with pytest.raises(ValueError, match="cannot be named in a task"):
        gradle_check.plan({"a": "-x"}, [jc.Want("x", "a", "p.T")])


def test_the_patterns_are_names_read_from_source_never_the_callers_text(tmp_path):
    repo = project(tmp_path, "shop")
    for bad in ["com.acme.shop.CartTest.*", "com.acme.shop.CartTest.addsItems(--tests x)", "--tests", "@file", "com.acme.shop.*.addsItems()"]:
        with pytest.raises(ValueError):
            wants_of(repo, [bad])


# ---- ids and paths before Gradle is asked -------------------------------------------------------------------


def test_an_id_is_looked_up_in_the_test_source_sets_and_never_in_main(tmp_path):
    repo = project(tmp_path, "shop")
    assert gradle_check.normalize(repo, ["com.acme.shop.CartTest.addsItems()"]) == [f"{CART}::CartTest.addsItems()"]
    assert gradle_check.normalize(repo, ["com.acme.shop.CartTest.Receipts.receiptHasTotal()"]) == [f"{CART}::CartTest.Receipts.receiptHasTotal()"]
    with pytest.raises(ValueError, match="no test class or method of that name"):
        gradle_check.normalize(repo, ["com.acme.shop.Cart.add(String,int)"])  # a main class
    assert gradle_check.normalize(repo, [CART, "."]) == [CART, "."]  # a path is left for the checks that follow


def test_an_id_that_names_two_files_is_refused_not_guessed(tmp_path):
    repo = multi(tmp_path)
    for module in ("core", "web"):
        text = (repo / "core" / "src/test/java/com/acme/core/CoreTest.java").read_text().replace("com.acme.core", "com.acme.same")
        (repo / module / "src/test/java/com/acme/same").mkdir(parents=True)
        (repo / module / "src/test/java/com/acme/same/SameTest.java").write_text(text.replace("CoreTest", "SameTest"))
    with pytest.raises(ValueError, match="matches 2 files"):
        gradle_check.normalize(repo, ["com.acme.same.SameTest.works()"])


def test_a_file_outside_the_test_sources_of_a_project_is_refused_by_the_plan(tmp_path):
    repo = project(tmp_path, "shop")
    with pytest.raises(ValueError, match="not in the test sources of a project of the build"):
        wants_of(repo, ["src/main/java/com/acme/shop/Cart.java"])


def test_the_candidate_roots_are_every_source_set_but_main_and_never_build_output(tmp_path):
    repo = tmp_path / "r"
    for rel in ("src/test/java", "src/main/java", "m/src/integrationTest/java", "build/generated/src/test/java"):
        (repo / rel).mkdir(parents=True)
    (repo / "src/test/java/com/build/java").mkdir(parents=True)
    assert gradle_check._candidate_roots(repo.resolve()) == ("m/src/integrationTest/java", "src/test/java")


# ---- the init script and what ripple leaves behind ---------------------------------------------------------


def test_the_init_script_has_no_dollar_so_groovy_reads_no_string_template():
    assert "$" not in gradle_check.INIT_SCRIPT


def test_the_init_script_and_manifest_live_outside_the_repo_and_are_removed(tmp_path, monkeypatch):
    repo = project(tmp_path, "shop")
    seen = {}

    def script(argv, cwd, manifest):
        init = Path(argv[argv.index("-I") + 1])
        seen.update(init=init, manifest=manifest, text=init.read_text(), mode=init.stat().st_mode & 0o777, argv=argv)
        lines(manifest, *record(cwd))
        return 0, "BUILD SUCCESSFUL\n"

    install(monkeypatch, script)
    before = {p for p in repo.rglob("*")}
    checker.run_check(repo, tests=[], profile=JAVA)
    assert seen["text"] == gradle_check.init_text(seen["manifest"]) and seen["mode"] == 0o600
    assert "ripple.manifest" not in " ".join(seen["argv"]) and str(seen["manifest"]) in seen["text"]
    assert not Path(seen["init"]).exists() and not Path(seen["manifest"]).exists()
    assert not Path(seen["init"]).is_relative_to(repo)
    assert {p for p in repo.rglob("*")} - before == {p for p in repo.rglob("*") if "build" in p.relative_to(repo).parts}  # only what the fake build wrote


def test_a_second_check_in_the_same_repo_is_refused_naming_gradle(tmp_path, monkeypatch, no_process):
    repo = project(tmp_path, "shop")
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/gradle" if name == "gradle" else None)
    lock = jc.repo_lock(repo.resolve(), 5)
    with lock, pytest.raises(ValueError, match="another check is running in this repository.*two Gradle runs in one build/ directory"):
        gradle_check.run(repo, [], 0.3)


def test_the_maven_lock_message_is_what_it_was(tmp_path):
    repo = tmp_path / "r"
    repo.mkdir()
    with jc.repo_lock(repo, 5), pytest.raises(ValueError, match="two Maven runs in one target/ cannot be told apart"), jc.repo_lock(repo, 0.2):
        pass
