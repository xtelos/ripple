"""Two `check` calls in one repository must not read each other's fresh reports as their own.

The reports of a run are told from an earlier run's by a snapshot taken before it, so a second Maven run writing into the
same target/ while the first is still going is indistinguishable from this run's own writes. ripple's own calls are
serialized per repository (a lock file in the temporary directory, never in the repo); a build started by anything else
(an IDE) is not, and the README says so.
"""

from __future__ import annotations

import threading
import time

import pytest
from java_check_support import PROJECTS

from ripple import java_check as jc


def test_a_second_check_in_the_same_repo_waits_for_the_first_and_then_runs(tmp_path):
    repo = tmp_path / "r"
    repo.mkdir()
    order = []

    def second():
        with jc.repo_lock(repo, 10):
            order.append("second")

    with jc.repo_lock(repo, 10):
        thread = threading.Thread(target=second)
        thread.start()
        time.sleep(0.4)
        order.append("first done")
    thread.join(5)
    assert order == ["first done", "second"]


def test_a_check_that_cannot_get_the_lock_in_time_is_refused_not_run(tmp_path):
    repo = tmp_path / "r"
    repo.mkdir()
    with jc.repo_lock(repo, 5):
        with pytest.raises(ValueError, match="another check is running in this repository"):
            with jc.repo_lock(repo, 0.3):
                raise AssertionError("ran while the lock was held")


def test_the_lock_is_per_repository_and_leaves_nothing_in_the_repo(tmp_path):
    one, two = tmp_path / "one", tmp_path / "two"
    one.mkdir()
    two.mkdir()
    with jc.repo_lock(one, 5):
        with jc.repo_lock(two, 0.3):
            pass
    assert list(one.iterdir()) == [] and list(two.iterdir()) == []


def test_the_lock_is_released_when_the_check_fails(tmp_path):
    repo = tmp_path / "r"
    repo.mkdir()
    with pytest.raises(RuntimeError):
        with jc.repo_lock(repo, 5):
            raise RuntimeError("boom")
    with jc.repo_lock(repo, 0.3):
        pass


def test_run_takes_the_lock_before_it_starts_anything(tmp_path, monkeypatch):
    import shutil

    repo = tmp_path / "repo"
    shutil.copytree(PROJECTS / "shop", repo)
    started = []
    monkeypatch.setattr(jc, "run_process", lambda *a, **k: started.append(a) or (_ for _ in ()).throw(AssertionError("started")))
    monkeypatch.setattr(jc, "choose_tool", lambda repo: ["mvn"])
    with jc.repo_lock(repo.resolve(), 5):
        with pytest.raises(ValueError, match="another check is running"):
            jc.run(repo, [], 0.3)
    assert started == []
