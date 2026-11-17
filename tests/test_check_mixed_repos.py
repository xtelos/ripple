"""`check` in a repo of several languages does not pick one.

Which language's runner `check` uses decides what a `passed` means, so with files of two languages, no `--lang` and no
test command it refuses and names the choices (languages.check_profile). The walk is the one the indexer uses, which
reads file names only: none of these tests needs an optional parser.
"""

import json
import re
import sys
from contextlib import contextmanager

import pytest
from stdio_client import StdioClient

from ripple import cli, registry
from ripple.checker import RUNNERS, given_command, run_check
from ripple.cli import main
from ripple.languages import RepoError, check_profile
from ripple.profile import TYPESCRIPT

GO_PY = {"go.mod": "module ex.com/m\n\ngo 1.22\n", "a.go": "package m\n\nfunc A() {}\n", "tools/gen.py": "def f():\n    pass\n"}
ONE_OF_EACH = {
    "a.py": "x = 1\n",
    "b.ts": "export const b = 1;\n",
    "c.js": "exports.c = 1;\n",
    "d.go": "package m\n",
    "e.rs": "pub fn e() {}\n",
    "F.java": "class F {}\n",
}
PASS = f"{sys.executable} -c pass"


def refusal(repo, found: str, flags: str) -> str:
    return (
        f"cannot choose which tests to run in {repo}: it has {found} source files, and a verdict from one language's tests "
        f"says nothing about the others. Pass {flags} (`ripple serve` takes the same flag, and so does RIPPLE_LANG), "
        "or the command that runs every test (--test-command)."
    )


@pytest.fixture(autouse=True)
def no_ambient_settings(monkeypatch):
    monkeypatch.delenv("RIPPLE_LANG", raising=False)
    monkeypatch.delenv("RIPPLE_TEST_COMMAND", raising=False)


def test_the_cli_refuses_a_go_and_python_repo_and_names_both_languages_and_the_flag(make_repo, capsys):
    repo = make_repo(GO_PY)
    assert main(["check", "--repo", str(repo)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == f"ripple: {refusal(repo, 'Python and Go', '--lang python or --lang go')}\n"


def tool_refusal(repo, found: str, args: str) -> str:
    """What the server's tool says: its `lang` argument, which the agent can set on its next call."""
    return (
        f"Error executing tool check: cannot choose which tests to run in {repo}: it has {found} source files, and a verdict from "
        f"one language's tests says nothing about the others. Call check again with {args}, or start the server with "
        "`ripple serve --lang <name>` (or RIPPLE_LANG) to fix one for the session, or with the command that runs every test (--test-command)."
    )


@contextmanager
def served(repo, tmp_path, *options):
    """A real `ripple serve` for the repo; calls go through call(...)."""
    client = StdioClient([sys.executable, "-m", "ripple", "serve", "--repo", str(repo), *options], env={"RIPPLE_CACHE_DIR": str(tmp_path / "cache")})
    try:
        client.initialize()
        yield lambda arguments: _check(client, arguments)
    finally:
        assert client.close() == 0


def _check(client, arguments: dict):
    """(is it an error, the text of the reply)."""
    reply = client.request("tools/call", {"name": "check", "arguments": arguments})
    return reply["result"].get("isError") is True, reply["result"]["content"][0]["text"]


def test_the_server_tool_refuses_and_names_its_lang_argument_and_each_choice(make_repo, tmp_path):
    repo = make_repo(GO_PY)
    with served(repo, tmp_path) as call:
        assert call({}) == (True, tool_refusal(repo, "Python and Go", 'lang="python" or lang="go"'))


def test_the_server_tool_names_every_language_it_found(make_repo, tmp_path):
    repo = make_repo({**GO_PY, "e.rs": "pub fn e() {}\n"})
    with served(repo, tmp_path) as call:
        assert call({}) == (True, tool_refusal(repo, "Python, Go and Rust", 'lang="python", lang="go" or lang="rust"'))


def test_the_tool_description_says_when_lang_is_needed(tmp_path, make_repo):
    client = StdioClient([sys.executable, "-m", "ripple", "serve", "--repo", str(make_repo(GO_PY))], env={"RIPPLE_CACHE_DIR": str(tmp_path / "cache")})
    try:
        client.initialize()
        tool = {t["name"]: t for t in client.request("tools/list")["result"]["tools"]}["check"]
        assert "lang" in tool["inputSchema"]["properties"] and "lang" not in tool["inputSchema"].get("required", [])
        assert "lang (optional; auto, python, typescript, javascript, go, rust, java) is needed when the repo has source files of more than one language" in tool["description"]
    finally:
        assert client.close() == 0


@pytest.mark.parametrize(
    "lang, reaches",
    [
        ("python", r'"command": ".*pytest'),
        ("typescript", "does not know how to run TypeScript tests"),
        ("javascript", "does not know how to run JavaScript tests"),
        ("go", "no go.mod in the repository"),
        ("rust", "(?i)cargo"),
        ("java", "no pom.xml at the repository root"),
    ],
)
def test_the_tool_with_lang_reaches_that_languages_runner_in_a_mixed_repo(make_repo, tmp_path, lang, reaches):
    repo = make_repo(ONE_OF_EACH)
    with served(repo, tmp_path) as call:
        _, text = call({"lang": lang})
    assert "cannot choose which tests to run" not in text
    assert re.search(reaches, text), text


def test_an_unknown_lang_is_the_unknown_language_error(make_repo, tmp_path):
    with served(make_repo(GO_PY), tmp_path) as call:
        assert call({"lang": "cobol"}) == (True, "Error executing tool check: unknown language 'cobol': choose one of auto, python, typescript, javascript, go, rust, java")
        assert call({"lang": ""})[0] is True  # not "omitted"


def test_a_single_language_repo_is_the_same_with_and_without_lang(make_repo, tmp_path):
    repo = make_repo({"test_a.py": "def test_a():\n    pass\n"})
    with served(repo, tmp_path) as call:
        for arguments in ({}, {"lang": "python"}, {"lang": "auto"}):
            is_error, text = call(arguments)
            assert not is_error and "pytest" in json.loads(text)["command"]
        is_error, text = call({"lang": "go"})  # an explicit language is that language's, as on the command line
        assert is_error and "no go.mod" in text


def test_a_lang_on_one_call_overrides_the_servers_for_that_call_only(make_repo, tmp_path):
    repo = make_repo({"a.go": "package m\n", "tools/gen.py": "x = 1\n", "test_a.py": "def test_a():\n    pass\n"})  # no go.mod: the Go runner refuses before it looks for go
    with served(repo, tmp_path, "--lang", "go") as call:
        assert "no go.mod" in call({})[1]  # the server's own lang
        is_error, text = call({"lang": "python"})
        assert not is_error and "pytest" in json.loads(text)["command"]
        assert "no go.mod" in call({})[1]  # and it is back
        assert call({"lang": "auto"}) == (True, tool_refusal(repo, "Python and Go", 'lang="python" or lang="go"'))


def test_with_a_test_command_lang_picks_the_profile_that_reads_the_failures(make_repo, tmp_path):
    """Not refused without lang (the command decides the verdict); with lang, the failing lines are read by that language's prefixes."""
    repo = make_repo(GO_PY)
    command = f"{sys.executable} -c \"print('FAIL ts_style'); print('FAILED py_style'); raise SystemExit(1)\""
    with served(repo, tmp_path, "--test-command", command) as call:
        results = {}
        for arguments in ({}, {"lang": "python"}, {"lang": "typescript"}):
            is_error, text = call(arguments)
            assert not is_error
            results[arguments.get("lang")] = json.loads(text)
    assert all(result["passed"] is False and result["exit_code"] == 1 for result in results.values())
    assert results[None]["failures"] == results["python"]["failures"] == ["FAILED py_style"]
    assert results["typescript"]["failures"] == ["FAIL ts_style"]


def test_three_languages_are_all_named(make_repo):
    repo = make_repo({**GO_PY, "e.rs": "pub fn e() {}\n"})
    with pytest.raises(RepoError) as error:
        check_profile(repo)
    assert str(error.value) == refusal(repo, "Python, Go and Rust", "--lang python, --lang go or --lang rust")


@pytest.mark.parametrize("lang", list(registry.LANGUAGES))
def test_an_explicit_lang_wins_in_a_mixed_repo_and_runs_that_languages_check(make_repo, monkeypatch, capsys, lang):
    repo = make_repo(ONE_OF_EACH)
    seen = []
    monkeypatch.setattr(cli, "run_check", lambda repo, **kwargs: seen.append(kwargs["profile"].name) or {"passed": True})
    assert main(["check", "--repo", str(repo), "--lang", lang]) == 0
    assert seen == [lang]
    assert check_profile(repo, lang).name == lang
    assert capsys.readouterr().err == ""


def test_ripple_lang_is_an_explicit_lang_too(make_repo, monkeypatch):
    repo = make_repo(GO_PY)
    monkeypatch.setenv("RIPPLE_LANG", "go")
    monkeypatch.setattr(cli, "run_check", lambda repo, **kwargs: {"passed": True, "profile": kwargs["profile"].name})
    assert main(["check", "--repo", str(repo)]) == 0


def test_an_explicit_command_is_not_refused_because_the_profile_cannot_change_its_verdict(make_repo, capsys):
    """With a command, run_check takes the exit code as the verdict and never reaches a language's runner or its test ids
    (checker.run_check: `runner = None if command`); the profile only chooses which output lines are listed as failures."""
    repo = make_repo(GO_PY)
    assert main(["check", "--repo", str(repo), "--test-command", PASS]) == 0
    assert '"passed": true' in capsys.readouterr().out
    assert check_profile(repo, has_command=True).name == "python"  # registry order, as for a command given before this rule
    assert main(["check", "--repo", str(repo), "--test-command", f"{sys.executable} -c 'raise SystemExit(3)'"]) == 1
    assert '"exit_code": 3' in capsys.readouterr().out


def test_an_explicit_command_through_the_environment_and_the_server_is_not_refused_either(make_repo, monkeypatch, tmp_path, capsys):
    repo = make_repo(GO_PY)
    monkeypatch.setenv("RIPPLE_TEST_COMMAND", PASS)
    assert main(["check", "--repo", str(repo)]) == 0
    monkeypatch.delenv("RIPPLE_TEST_COMMAND")
    client = StdioClient(
        [sys.executable, "-m", "ripple", "serve", "--repo", str(repo), "--test-command", PASS], env={"RIPPLE_CACHE_DIR": str(tmp_path / "cache")}
    )
    try:
        client.initialize()
        reply = client.request("tools/call", {"name": "check", "arguments": {}})
        assert reply["result"].get("isError") is not True
        assert '"passed": true' in reply["result"]["content"][0]["text"]
    finally:
        assert client.close() == 0


# ---- a test command that comes to nothing is no command ---------------------------------------------------

BLANKS = ["", " ", "   ", "\t", "''", '""', "' '", "'' ''"]


def test_given_command_is_the_one_decision_and_a_command_that_splits_to_nothing_is_none():
    for blank in [*BLANKS, None, [], [""], [" "], ("",)]:
        assert given_command(blank) is None, repr(blank)
    assert given_command("python -m pytest -q") == ["python", "-m", "pytest", "-q"]
    assert given_command("'my tests' --flag") == ["my tests", "--flag"]
    assert given_command(["python", "-c", "pass"]) == ["python", "-c", "pass"]
    assert given_command(("make", "test")) == ["make", "test"]
    assert given_command(given_command("a b")) == ["a", "b"]  # run_check may be handed the answer again
    with pytest.raises(ValueError, match="No closing quotation"):
        given_command("python -c 'oops")


@pytest.mark.parametrize("blank", BLANKS)
def test_a_blank_test_command_on_the_command_line_does_not_skip_the_refusal(make_repo, capsys, blank):
    repo = make_repo(GO_PY)
    assert main(["check", "--repo", str(repo), "--test-command", blank]) == 2
    captured = capsys.readouterr()
    assert captured.err == f"ripple: {refusal(repo, 'Python and Go', '--lang python or --lang go')}\n"
    assert "passed" not in captured.out


@pytest.mark.parametrize("blank", BLANKS)
def test_a_blank_ripple_test_command_does_not_skip_the_refusal(make_repo, monkeypatch, capsys, blank):
    repo = make_repo(GO_PY)
    monkeypatch.setenv("RIPPLE_TEST_COMMAND", blank)
    assert main(["check", "--repo", str(repo)]) == 2
    captured = capsys.readouterr()
    assert "cannot choose which tests to run" in captured.err and "passed" not in captured.out


@pytest.mark.parametrize("blank", ["", " ", "''", "   "])
def test_a_server_started_with_a_blank_test_command_refuses_a_mixed_repo(make_repo, tmp_path, blank):
    repo = make_repo(GO_PY)
    with served(repo, tmp_path, "--test-command", blank) as call:
        expected = (True, tool_refusal(repo, "Python and Go", 'lang="python" or lang="go"'))
        assert call({}) == expected
        assert call({"lang": "auto"}) == expected
        assert call({"lang": None}) == expected


def test_a_blank_command_is_no_command_for_the_runner_too(make_repo, monkeypatch):
    """With --lang the runner of that language is chosen, and it is handed no command, never a blank one."""
    repo = make_repo(GO_PY)
    seen = []
    monkeypatch.setattr(cli, "run_check", lambda repo, **kwargs: seen.append((kwargs["profile"].name, kwargs["command"])) or {"passed": True})
    assert main(["check", "--repo", str(repo), "--lang", "go", "--test-command", "   "]) == 0
    assert main(["check", "--repo", str(repo), "--test-command", "echo hi"]) == 0
    assert seen == [("go", None), ("python", ["echo", "hi"])]


def test_run_check_reads_a_blank_command_as_none_and_a_list_as_given(make_repo):
    repo = make_repo({"a.ts": "export const a = 1;\n"})
    for blank in ("  ", "''", [], [""]):
        with pytest.raises(ValueError, match="does not know how to run TypeScript tests"):  # the profile's own refusal, no process
            run_check(repo, command=blank, profile=TYPESCRIPT)
    assert run_check(repo, command=[sys.executable, "-c", "pass"], profile=TYPESCRIPT)["passed"] is True


def test_a_command_that_cannot_be_split_is_an_error_not_a_traceback(make_repo, capsys, tmp_path):
    repo = make_repo(GO_PY)
    assert main(["check", "--repo", str(repo), "--test-command", "python -c 'oops"]) == 2
    assert "No closing quotation" in capsys.readouterr().out
    with served(repo, tmp_path, "--test-command", "python -c 'oops") as call:
        is_error, text = call({})
        assert is_error and "No closing quotation" in text


def test_the_evals_python_server_runs_pytest_in_a_checkout_with_a_stray_file_of_another_language(make_repo, tmp_path):
    """The shape of the werkzeug checkout (one debugger.js beside the Python files): the arguments the eval starts a Python
    task's server with (eval/run.py mcp_config) must not get the refusal, or the with-ripple arm's first `check` fails."""
    from eval.run import mcp_config

    repo = make_repo({"src/pkg/__init__.py": "", "src/pkg/debug/shared/debugger.js": "exports.x = 1;\n", "tests/test_a.py": "def test_a():\n    pass\n"})
    args = mcp_config("with-ripple", repo, tmp_path / "cache")["mcpServers"]["ripple"]["args"]
    assert args[:3] == ["serve", "--repo", str(repo)] and args[3:] == ["--lang", "python"]
    with served(repo, tmp_path, *args[3:]) as call:
        is_error, text = call({})
        assert not is_error and "pytest" in json.loads(text)["command"]
    with served(repo, tmp_path) as call:  # without it, the same checkout is refused
        assert call({})[1].startswith("Error executing tool check: cannot choose which tests to run")


def test_a_repo_mixed_only_through_skipped_directories_is_not_refused(make_repo, capsys, monkeypatch):
    repo = make_repo(
        {
            "app.py": "def f():\n    pass\n",
            "node_modules/dep/index.js": "exports.x = 1;\n",  # vendored
            "web/node_modules/dep/index.ts": "export const x = 1;\n",
            "build/gen.go": "package m\n",  # build output at the root
            "dist/lib.rs": "pub fn x() {}\n",
            "env/pkg/Thing.java": "class Thing {}\n",
            "ve/pyvenv.cfg": "home = /usr\n",  # a virtualenv, whatever it is called
            "ve/lib/vendored.go": "package v\n",
            "types.d.ts": "export declare const t: number;\n",  # a declaration file holds no code
        }
    )
    monkeypatch.setattr(cli, "run_check", lambda repo, **kwargs: {"passed": True, "profile": kwargs["profile"].name})
    assert main(["check", "--repo", str(repo)]) == 0
    assert '"profile": "python"' in capsys.readouterr().out


def test_the_same_files_outside_the_skipped_directories_do_refuse(make_repo):
    repo = make_repo({"app.py": "def f():\n    pass\n", "src/node_modules/x.js": "x\n", "lib/gen.go": "package m\n"})
    with pytest.raises(RepoError, match="it has Python and Go source files"):
        check_profile(repo)


def test_typescript_and_javascript_are_not_a_mixed_repo_for_check(make_repo):
    """Two profiles, but neither has a default command or a runner and both list failures by the same prefixes, so which of
    them check picks changes nothing (the pins below fail the day that stops being true, and then they are two choices)."""
    repo = make_repo({"a.ts": "export const a = 1;\n", "b.js": "exports.b = 1;\n"})
    assert check_profile(repo).name == "typescript"
    registry_ts, registry_js = registry.LANGUAGES["typescript"].profile, registry.LANGUAGES["javascript"].profile
    assert registry_ts.failure_prefixes == registry_js.failure_prefixes
    for profile in (registry_ts, registry_js):
        assert profile.name not in RUNNERS
        with pytest.raises(ValueError, match="does not know how to run"):
            profile.default_test_command(repo)


def test_typescript_and_javascript_beside_another_language_still_refuse_and_name_the_languages(make_repo):
    repo = make_repo({"a.ts": "export const a = 1;\n", "b.js": "exports.b = 1;\n", "c.go": "package m\n"})
    with pytest.raises(RepoError) as error:
        check_profile(repo)
    assert str(error.value) == refusal(repo, "TypeScript, JavaScript and Go", "--lang typescript, --lang javascript or --lang go")


def test_one_language_and_no_language_are_as_before(make_repo, tmp_path_factory):
    assert check_profile(make_repo({"a.go": "package m\n"})).name == "go"
    empty = tmp_path_factory.mktemp("empty")
    assert check_profile(empty).name == "python"  # no source at all: the registry's first profile, as documented


def test_the_graph_commands_still_load_every_language(make_repo, capsys):
    repo = make_repo(GO_PY)
    assert main(["index", str(repo)]) in (0, 3)  # answered (3 when a parser is missing); never the refusal's 2
