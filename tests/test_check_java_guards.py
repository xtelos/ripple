"""Guards that survived mutation in the tests CI runs: each now has a captured-fixture test that fails without it.

(1) the snapshot half of fresh(): a report rewritten within the clock's slack of the run's start is told apart from this
run's only by its (mtime, size, inode) not having changed; (2) the "not run" problem when the time limit is used up
before a later Maven run begins; (3) a want of one module is not answered by the same class name in another module;
(4) a method want is not answered by a same-named method of a nested class.
"""

from __future__ import annotations

import os
import time

from java_check_support import CAPTURES, HOUR_NS, make_repo, place, run_of

from ripple import java_check as jc


def test_a_stale_report_stamped_inside_the_clocks_slack_of_the_start_is_still_stale(tmp_path):
    """The earlier run ended half a second before this one began: the clock floor lets it through, the snapshot does not."""
    repo = make_repo(tmp_path, "pass")
    place(repo, CAPTURES / "pass" / "reports", time.time_ns() - 500_000_000)
    before = jc.snapshot(repo)
    started = time.time_ns()
    mine, stale = jc.fresh(repo, before, started)
    assert mine == [] and stale == 3
    target = repo / "target" / "surefire-reports" / "TEST-com.acme.shop.CartTests.xml"
    assert target.stat().st_mtime_ns >= started - jc.CLOCK_SLACK_NS  # the floor alone would have accepted it
    target.write_bytes(target.read_bytes())  # rewritten by this run: now fresh
    mine, _ = jc.fresh(repo, before, started)
    assert [m.rsplit("/", 1)[1] for m in mine] == ["TEST-com.acme.shop.CartTests.xml"]


def test_back_to_back_runs_of_one_check_do_not_read_each_others_reports(tmp_path):
    """Two invocations of one check call: the second one's snapshot is taken after the first one wrote its reports."""
    repo = make_repo(tmp_path, "pass")
    place(repo, CAPTURES / "pass" / "reports", time.time_ns())  # the first run's reports, written just now
    second_started = time.time_ns()
    before = jc.snapshot(repo)
    mine, stale = jc.fresh(repo, before, second_started)
    assert mine == [] and stale == 3


def test_a_later_run_that_never_began_makes_the_verdict_not_passed(tmp_path):
    repo, project, run = run_of(tmp_path, "pass")
    never = jc.Run(jc.Invocation(None, ("com.acme.shop.CartTest",), False, ()), "", None, started=False)
    result = jc.judge(repo, project, [run, never], "mvn", 0.1, 300)
    assert result["passed"] is False
    assert any("not run: the 300s limit was used up before" in p for p in result["problems"]), result["problems"]


def test_a_want_of_one_module_is_not_answered_by_the_same_class_in_another(tmp_path):
    repo, project, run = run_of(tmp_path, "samename_b")  # SameTest ran in module b only
    want = jc.Want("a/src/test/java/com/acme/SameTest.java", "a", "com.acme.SameTest")
    run.invocation = jc.Invocation(("a", "b"), ("com.acme.SameTest",), True, (want,))
    result = jc.judge(repo, project, [run], "mvn", 0.1, 300)
    assert result["passed"] is False
    assert any("SameTest: no test ran" in p for p in result["problems"]), result["problems"]
    method = jc.Want("a/src/test/java/com/acme/SameTest.java::SameTest.same", "a", "com.acme.SameTest", "same")
    run.invocation = jc.Invocation(("a", "b"), ("com.acme.SameTest#same",), True, (method,))
    result = jc.judge(repo, project, [run], "mvn", 0.1, 300)
    assert any("SameTest.same: no test ran" in p for p in result["problems"]), result["problems"]


def test_a_method_want_is_not_answered_by_a_same_named_method_of_a_nested_class(tmp_path):
    repo, project, run = run_of(tmp_path, "method_nested")  # only CartTest$Receipts.receiptHasTotal ran
    want = jc.Want("x", "", "com.acme.shop.CartTest", "receiptHasTotal")
    run.invocation = jc.Invocation(None, ("com.acme.shop.CartTest#receiptHasTotal",), False, (want,))
    result = jc.judge(repo, project, [run], "mvn", 0.1, 300)
    assert result["passed"] is False
    assert any("CartTest.receiptHasTotal: no test ran" in p for p in result["problems"]), result["problems"]


def test_a_nested_class_want_is_answered_by_its_own_binary_name(tmp_path):
    repo, project, run = run_of(tmp_path, "method_nested")
    want = jc.Want("x", "", "com.acme.shop.CartTest$Receipts", "receiptHasTotal")
    run.invocation = jc.Invocation(None, ("com.acme.shop.CartTest$Receipts#receiptHasTotal",), False, (want,))
    assert jc.judge(repo, project, [run], "mvn", 0.1, 300)["passed"] is True


def test_the_floor_and_the_snapshot_agree_on_an_hour_old_report(tmp_path):
    repo = make_repo(tmp_path, "pass")
    place(repo, CAPTURES / "pass" / "reports", time.time_ns() - HOUR_NS)
    before = jc.snapshot(repo)
    assert jc.fresh(repo, before, time.time_ns())[0] == []
    assert os.path.isdir(repo / "target" / "surefire-reports")
