import json
import sys

import pytest
from conftest import FIXTURES
from stdio_client import StdioClient

from ripple.checker import check_tests, default_test_command
from ripple.languages import check_profile
from ripple.profile import PYTHON, TYPESCRIPT

READ_ONLY = {"callers", "callees", "impact", "path"}


@pytest.fixture
def client(tmp_path):
    c = StdioClient(
        [sys.executable, "-m", "ripple", "serve", "--repo", str(FIXTURES / "shop")],
        env={"RIPPLE_CACHE_DIR": str(tmp_path)},
    )
    c.initialize()
    yield c
    assert c.close() == 0


def test_lists_exactly_five_tools_with_honest_annotations(client):
    tools = {t["name"]: t for t in client.request("tools/list")["result"]["tools"]}
    assert set(tools) == READ_ONLY | {"check"}
    for name in READ_ONLY:
        assert tools[name]["annotations"]["readOnlyHint"] is True
    assert tools["check"]["annotations"]["readOnlyHint"] is False
    # running a test suite can do anything its tests do, so check must not claim to be harmless
    assert tools["check"]["annotations"].get("destructiveHint", True) is True
    assert tools["impact"]["inputSchema"]["required"] == ["symbol"]
    assert set(tools["path"]["inputSchema"]["required"]) == {"source", "target"}
    for tool in tools.values():
        assert len(tool["description"]) > 40
    # check is for both languages: it names each one's default and which ids each takes
    # (test_check_description_says_what_check_does pins each claim against what check does)
    check = tools["check"]["description"]
    assert "TypeScript profile" in check and "python -m pytest -q" in check
    assert "pytest node ids" in check and "the form the runner takes" not in check


def test_check_description_says_what_check_does(client, make_repo, tmp_path_factory):
    """Each claim the `check` description makes about its default command and its test ids, against the code that decides it.
    Tool definitions are pinned by hash by some clients, so a change to the text is a change to re-approve."""
    check = {t["name"]: t for t in client.request("tools/list")["result"]["tools"]}["check"]["description"]
    mixed = make_repo({"a.py": "def f():\n    pass\n", "b.ts": "export function g() { return 1; }\n"})
    typescript_only = tmp_path_factory.mktemp("tsonly")
    (typescript_only / "b.ts").write_text("export function g() { return 1; }\n")
    # "with none, the Python profile (a repo with any Python file, or --lang python) runs python -m pytest -q ... the
    # TypeScript profile (a repo with only TypeScript files, or --lang typescript) has no default and refuses"
    assert "a repo with any Python file, or --lang python" in check
    assert "a repo with only TypeScript files, or --lang typescript" in check
    assert check_profile(mixed, "auto") is PYTHON and check_profile(mixed, "python") is PYTHON
    assert check_profile(typescript_only, "auto") is TYPESCRIPT and check_profile(mixed, "typescript") is TYPESCRIPT
    assert default_test_command(mixed, PYTHON)[-3:] == ["-m", "pytest", "-q"]
    with pytest.raises(ValueError, match="does not know how to run TypeScript tests"):
        default_test_command(mixed, TYPESCRIPT)
    # "the repo's .venv or venv if it has one"
    assert ".venv or venv" in check
    # "test file or directory paths inside the repo ... pytest node ids (path::name) for Python ... a TypeScript id
    # (ripple's own, or a runner's line filter) is refused: pass the file"
    assert "for Python pytest node ids (path::name" in check and "A TypeScript test id" in check and "is refused" in check
    (mixed / "tests").mkdir()
    (mixed / "tests" / "a.test.ts").write_text("test('x', () => {});\n")
    (mixed / "tests" / "test_a.py").write_text("def test_x():\n    pass\n")
    check_tests(mixed.resolve(), ["tests/a.test.ts", "tests", "tests/test_a.py::test_x"])
    for refused in ("tests/a.test:suite > case", "tests/a.test.ts:12"):
        with pytest.raises(ValueError, match="is not a file or directory"):
            check_tests(mixed.resolve(), [refused])


def test_impact_round_trip(client):
    reply = client.request("tools/call", {"name": "impact", "arguments": {"symbol": "tax_for", "depth": 5}})
    assert reply["result"]["isError"] is False
    payload = json.loads(reply["result"]["content"][0]["text"])
    assert payload["symbol"] == "shop.tax.tax_for"
    assert payload["affected"] == 6
    assert len(payload["tests"]) == 3


def test_ambiguous_symbol_returns_candidates(client):
    reply = client.request("tools/call", {"name": "callers", "arguments": {"symbol": "amount_off"}})
    payload = json.loads(reply["result"]["content"][0]["text"])
    assert len(payload["candidates"]) == 3


def test_check_runs_the_configured_command(tmp_path):
    command = f"{sys.executable} -m pytest -q -p no:cacheprovider"
    c = StdioClient(
        [sys.executable, "-m", "ripple", "serve", "--repo", str(FIXTURES / "shop"), "--test-command", command],
        env={"RIPPLE_CACHE_DIR": str(tmp_path)},
    )
    try:
        c.initialize()
        reply = c.request("tools/call", {"name": "check", "arguments": {}})
        payload = json.loads(reply["result"]["content"][0]["text"])
        assert payload["passed"] is True
        assert "3 passed" in payload["summary"]

        impact = c.request("tools/call", {"name": "impact", "arguments": {"symbol": "OrderService.reprice"}})
        node_ids = [t["node_id"] for t in json.loads(impact["result"]["content"][0]["text"])["tests"]]
        reply = c.request("tools/call", {"name": "check", "arguments": {"tests": node_ids}})
        payload = json.loads(reply["result"]["content"][0]["text"])
        assert payload["passed"] is True
        assert payload["summary"].startswith("1 passed")

        reply = c.request("tools/call", {"name": "check", "arguments": {"tests": ["--basetemp=."]}})
        assert reply["result"]["isError"] is True
        assert "not a test path or node id" in reply["result"]["content"][0]["text"]
    finally:
        assert c.close() == 0


def test_kinds_is_an_optional_argument_of_each_query_and_a_python_answer_does_not_change(client):
    tools = {t["name"]: t for t in client.request("tools/list")["result"]["tools"]}
    for name in READ_ONLY:
        assert "kinds" in tools[name]["inputSchema"]["properties"]
        assert "kinds" not in tools[name]["inputSchema"]["required"]
    first = client.request("tools/call", {"name": "impact", "arguments": {"symbol": "tax_for"}})
    second = client.request("tools/call", {"name": "impact", "arguments": {"symbol": "tax_for", "kinds": "calls"}})
    assert first["result"]["content"][0]["text"] == second["result"]["content"][0]["text"]
    assert "edge_kinds" not in first["result"]["content"][0]["text"]


def test_an_unknown_kind_is_an_error_the_agent_can_read(client):
    reply = client.request("tools/call", {"name": "callers", "arguments": {"symbol": "tax_for", "kinds": "render"}})
    assert reply["result"]["isError"] is True
    assert "unknown edge kind 'render'" in reply["result"]["content"][0]["text"]
