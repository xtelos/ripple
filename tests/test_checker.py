import sys

from ripple.checker import default_test_command, run_check

PASSING = {"test_ok.py": "def test_ok():\n    assert True\n"}
FAILING = {
    "test_mixed.py": "def test_ok():\n    assert True\n\ndef test_bad():\n    assert 1 == 2, 'numbers differ'\n",
}
PYTEST = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"]


def test_passing_suite(make_repo):
    result = run_check(make_repo(PASSING), command=PYTEST)
    assert result["passed"] is True
    assert result["exit_code"] == 0
    assert "1 passed" in result["summary"]
    assert result["failures"] == []


def test_failing_suite_reports_first_failures_not_the_log(make_repo):
    result = run_check(make_repo(FAILING), command=PYTEST)
    assert result["passed"] is False
    assert "1 failed, 1 passed" in result["summary"]
    assert len(result["failures"]) == 1
    assert result["failures"][0].startswith("FAILED test_mixed.py::test_bad")
    assert "output_tail" not in result


def test_extra_args_select_tests(make_repo):
    result = run_check(make_repo(FAILING), command=PYTEST, extra_args=["test_mixed.py::test_ok"])
    assert result["passed"] is True


def test_timeout_is_reported(make_repo):
    result = run_check(make_repo(PASSING), command=[sys.executable, "-c", "import time; time.sleep(30)"], timeout=1)
    assert result["timed_out"] is True
    assert result["passed"] is False


def test_unparsed_failure_falls_back_to_a_short_tail(make_repo):
    command = [sys.executable, "-c", "import sys; print('\\n'.join(map(str, range(200)))); sys.exit(3)"]
    result = run_check(make_repo(PASSING), command=command)
    assert result["passed"] is False
    assert len(result["output_tail"]) <= 30
    assert result["output_tail"][-1] == "199"


def test_default_command_prefers_the_repo_virtualenv(make_repo):
    repo = make_repo({"m.py": ""})
    python = repo / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text("")
    assert default_test_command(repo) == [str(python), "-m", "pytest", "-q"]
