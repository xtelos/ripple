import json
import sys

import pytest
from conftest import FIXTURES
from stdio_client import StdioClient

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
