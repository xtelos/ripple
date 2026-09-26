import os
import signal
import sys
import time

import pytest

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


def test_tests_select_node_ids(make_repo):
    result = run_check(make_repo(FAILING), command=PYTEST, tests=["test_mixed.py::test_ok"])
    assert result["passed"] is True
    assert result["command"].endswith("test_mixed.py::test_ok")


@pytest.mark.parametrize(
    "bad",
    ["--basetemp={victim}", "-k", "-x", "", "../outside.py", "/etc", "test_missing.py::test_x", "{victim}"],
)
def test_tests_must_be_paths_or_node_ids_inside_the_repo(make_repo, tmp_path_factory, bad):
    # --basetemp=DIR makes pytest delete DIR, so an option must never get through.
    victim = tmp_path_factory.mktemp("victim")
    (victim / "precious.txt").write_text("keep me")
    repo = make_repo({"test_uses_tmp.py": "def test_tmp(tmp_path):\n    assert tmp_path.exists()\n"})
    with pytest.raises(ValueError):
        run_check(repo, command=PYTEST, tests=[bad.format(victim=victim)])
    assert (victim / "precious.txt").read_text() == "keep me"


@pytest.mark.parametrize("arg", ["@tests/opts.txt", "@tests/../tests/opts.txt", "@{victim}/opts.txt"])
def test_tests_refuse_pytest_argument_files(make_repo, tmp_path_factory, arg):
    # pytest reads options from any argument that starts with @, so it is an option in disguise.
    victim = tmp_path_factory.mktemp("victim")
    (victim / "precious.txt").write_text("keep me")
    (victim / "opts.txt").write_text(f"--basetemp={victim}\n")
    repo = make_repo(
        {
            "tests/test_uses_tmp.py": "def test_tmp(tmp_path):\n    assert tmp_path.exists()\n",
            "tests/opts.txt": f"--basetemp={victim}\ntests/test_uses_tmp.py\n",
        }
    )
    with pytest.raises(ValueError):
        run_check(repo, command=PYTEST, tests=[arg.format(victim=victim)])
    assert (victim / "precious.txt").read_text() == "keep me"


def _kill_pid_in(path):
    if path.exists() and path.read_text().strip():
        try:
            os.kill(int(path.read_text()), signal.SIGKILL)
        except ProcessLookupError:
            pass


def test_a_detached_child_holding_the_output_does_not_delay_the_result(make_repo, tmp_path_factory):
    pid_file = tmp_path_factory.mktemp("pid") / "child.pid"
    script = (
        "import subprocess; "
        "p = subprocess.Popen(['sleep', '30'], start_new_session=True); "
        f"open({str(pid_file)!r}, 'w').write(str(p.pid)); "
        "print('1 passed')"
    )
    try:
        started = time.monotonic()
        result = run_check(make_repo(PASSING), command=[sys.executable, "-c", script], timeout=10)
        assert time.monotonic() - started < 5
        assert result["passed"] is True
        assert result["summary"] == "1 passed"
    finally:
        _kill_pid_in(pid_file)


def test_timeout_bounds_wall_time_even_with_a_detached_child(make_repo, tmp_path_factory):
    pid_file = tmp_path_factory.mktemp("pid") / "child.pid"
    script = (
        "import subprocess, time; "
        "p = subprocess.Popen(['sleep', '8'], start_new_session=True); "
        f"open({str(pid_file)!r}, 'w').write(str(p.pid)); "
        "time.sleep(30)"
    )
    try:
        started = time.monotonic()
        result = run_check(make_repo(PASSING), command=[sys.executable, "-c", script], timeout=1)
        assert time.monotonic() - started < 4
        assert result["timed_out"] is True
    finally:
        _kill_pid_in(pid_file)


def test_every_returned_line_is_capped_with_a_marker(make_repo):
    script = (
        "import sys; "
        "print('FAILED test_x.py::test_y - ' + 'y' * 5000); "
        "print('RuntimeError: connection error: ' + 'x' * 200000); "
        "sys.exit(1)"
    )
    result = run_check(make_repo(PASSING), command=[sys.executable, "-c", script])
    assert result["summary"].endswith("[truncated 199792 chars]")
    assert len(result["summary"]) < 300
    assert result["failures"][0].endswith("[truncated 4787 chars]")


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
