"""A broader request may merge the RUNS but never the REQUIREMENTS: whatever was asked for keeps its own obligation.

`check([".", "OffTest.java"])` ran everything in one Maven run and, having dropped the second request because the first
covered it, called an `@Disabled` OffTest passed. Every pairing below is judged both ways: the plan must keep each thing
asked for on a run that answers it (no JDK needed), and the verdict of the captured run must hold each one to it.
"""

from __future__ import annotations

import pytest
from java_check_support import make_repo, meta, run_of, verdict

from ripple import java_check as jc

CART = "src/test/java/com/acme/shop/CartTest.java"
OFF = "src/test/java/com/acme/shop/OffTest.java"
WEB = "web/src/test/java/com/acme/web/WebTest.java"


def wants_and_runs(repo, request):
    project = jc.find_project(repo)
    wants = jc.parse_wants(repo, project, jc.normalize(repo, request))
    return wants, jc.plan(project, wants)


def kept(runs: list[jc.Invocation]) -> list[jc.Want]:
    return [w for run in runs for w in run.wants]


PAIRINGS = [
    ("disabled", [".", OFF]),  # directory plus file
    ("disabled", [CART, f"{CART}::CartTest.skipped()"]),  # file plus method
    ("shop", [CART, f"{CART}::CartTest.Receipts"]),  # class plus nested class
    ("shop", [CART, f"{CART}::CartTest.Receipts.receiptHasTotal()"]),  # class plus a method of its nested class
    ("shop", [f"{CART}::CartTest.Receipts", CART]),  # the other order
    ("shop", [".", CART, f"{CART}::CartTest.addsItems()"]),  # module, class, method
    ("shop", ["com.acme.shop.CartTest.addsItems()", f"{CART}::CartTest.addsItems"]),  # the same id in two spellings
    ("shop", ["com.acme.shop.CartTest.addsItems()", "com.acme.shop.CartTest.addsItems()"]),  # the same id twice
    ("multimod_ok", ["core", WEB]),  # a module and a class of another module
    ("multimod_ok", ["web", WEB]),  # a module and a class of it
    ("multimod_ok", ["web", f"{WEB}::WebTest.works()"]),
    ("multimod_ok", [".", WEB]),
    ("shop", ["src/test/java/com/acme/shop", CART]),  # a directory of test classes plus one of them
]


@pytest.mark.parametrize("project,request_", PAIRINGS)
def test_every_thing_asked_for_stays_on_a_run_that_answers_it(tmp_path, project, request_):
    repo = make_repo_of(tmp_path, project)
    wants, runs = wants_and_runs(repo, request_)
    assert wants
    for want in wants:
        assert want in kept(runs), f"{want} was dropped from the plan: {[r.argv() for r in runs]}"


def make_repo_of(tmp_path, project):
    import shutil

    from java_check_support import PROJECTS

    repo = tmp_path / "repo"
    shutil.copytree(PROJECTS / project, repo)
    return repo.resolve()


def test_the_run_that_covers_a_want_is_the_one_that_holds_it(tmp_path):
    repo = make_repo_of(tmp_path, "multimod_ok")
    wants, runs = wants_and_runs(repo, ["core", WEB, f"{WEB}::WebTest.works()"])
    by_module = {r.modules: r for r in runs}
    assert set(by_module) == {("core",), ("web",)}
    assert {w.module for w in by_module[("core",)].wants} == {"core"}
    assert {(w.cls, w.method) for w in by_module[("web",)].wants} == {("com.acme.web.WebTest", None), ("com.acme.web.WebTest", "works")}


def test_a_selector_is_still_the_narrowest_one_when_requests_overlap(tmp_path):
    repo = make_repo_of(tmp_path, "shop")
    _, runs = wants_and_runs(repo, [CART, f"{CART}::CartTest.Receipts", f"{CART}::CartTest.addsItems()"])
    assert [r.selectors for r in runs] == [("com.acme.shop.CartTest",)]  # the nested class and the method add nothing to the run


# ---- the verdict holds each one to its own obligation -------------------------------------------------------------


def test_a_disabled_class_asked_for_beside_its_module_is_not_passed(tmp_path):
    """The reviewer's case: [".", "OffTest.java"] returned passed with OffTest fully @Disabled."""
    result = verdict(tmp_path, "disabled_dir_plus_file")
    assert result["passed"] is False, result
    assert any("OffTest: skipped or disabled, so it did not run" in p for p in result["problems"]), result["problems"]
    assert result["counts"]["skipped"] == 2


def test_the_module_alone_is_still_passed_with_its_disabled_class_counted(tmp_path):
    result = verdict(tmp_path, "disabled_dir_plus_file", request=["."])
    assert result["passed"] is True and result["counts"]["skipped"] == 2


def test_a_disabled_method_asked_for_beside_its_class_is_not_passed(tmp_path):
    result = verdict(tmp_path, "disabled_file_plus_method")
    assert result["passed"] is False, result
    assert any("CartTest.skipped: skipped or disabled, so it did not run" in p for p in result["problems"]), result["problems"]


def test_the_class_alone_without_the_method_asked_is_passed(tmp_path):
    result = verdict(tmp_path, "disabled_file_plus_method", request=[CART])
    assert result["passed"] is True


def test_a_nested_class_asked_for_beside_its_outer_class_must_have_run(tmp_path):
    ok = verdict(tmp_path / "ok", "class_plus_nested")
    assert ok["passed"] is True, ok
    repo, project, run = run_of(tmp_path / "gone", "class_plus_nested")
    run.cases = [c for c in run.cases if "$Receipts" not in c.classname]  # the outer class ran, its nested class did not
    result = jc.judge(repo, project, [run], "mvn", 0.1, 300)
    assert result["passed"] is False
    assert any("Receipts: no test ran" in p for p in result["problems"]), result["problems"]


def test_a_method_asked_for_beside_its_class_must_have_run(tmp_path):
    repo, project, run = run_of(tmp_path, "disabled_file_plus_method")
    for case in run.cases:
        if case.name == "skipped":
            run.cases.remove(case)
            break
    result = jc.judge(repo, project, [run], "mvn", 0.1, 300)
    assert result["passed"] is False
    assert any("CartTest.skipped: no test ran" in p for p in result["problems"]), result["problems"]


def test_the_same_id_twice_or_in_two_spellings_is_judged_once_and_still_passes(tmp_path):
    result = verdict(tmp_path, "method_twice")
    assert result["passed"] is True and result["counts"]["passed"] == 1, result


def test_a_module_asked_for_beside_a_class_of_another_module_keeps_both(tmp_path):
    repo = make_repo(tmp_path, "multimod_class") if False else make_repo_of(tmp_path, "multimod_ok")
    _, runs = wants_and_runs(repo, ["core", WEB])
    assert [r.argv() for r in runs] == [
        ["mvn", "-B", "--fail-at-end", "-pl", "core", "-am", "test"],
        ["mvn", "-B", "--fail-at-end", "-pl", "web", "-am", "test", "-Dtest=com.acme.web.WebTest", "-Dsurefire.failIfNoSpecifiedTests=false"],
    ]


def test_the_captured_runs_are_of_the_command_the_plan_builds(tmp_path):
    for name in ("disabled_dir_plus_file", "disabled_file_plus_method", "class_plus_nested", "method_twice", "module_dir"):
        data = meta(name)
        repo = make_repo(tmp_path / name, name)
        _, runs = wants_and_runs(repo, data["request"])
        assert [r.argv() for r in runs] == [data["argv"]], name
