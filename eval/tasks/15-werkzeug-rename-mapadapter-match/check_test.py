import pytest
from check_helpers import references

from werkzeug.exceptions import HTTPException, MethodNotAllowed, NotFound
from werkzeug.routing import Map, MapAdapter, Rule, RequestRedirect
from werkzeug.routing.matcher import StateMachineMatcher

# Every other `.match` in the repository is a regular expression or the matcher inside Map.
OTHER_MATCHES = {
    "_charset_value_re", "_parameter_key_re", "_etag_re", "_parameter_token_value_re", "_part_re", "_cookie_re",
    "LINE_BREAK_RE", "regex", "re.compile(test_part.content)", "self.map._matcher",
}  # fmt: skip


def test_the_adapter_method_is_renamed_and_the_matcher_keeps_its_own():
    assert "match_request" in vars(MapAdapter) and "match" not in vars(MapAdapter)
    assert "match" in vars(StateMachineMatcher)


def test_no_call_to_the_old_name_is_left_on_an_adapter():
    """The 7 example apps and the tests count as much as the library."""
    uses = references("match")
    stale = [(f, line, recv) for f, line, kind, recv in uses if kind == "attr" and recv not in OTHER_MATCHES]
    assert stale == []
    assert [u for u in uses if u[2] == "def" and u[3] == "MapAdapter"] == []
    renamed = {f for f, _l, kind, _d in references("match_request") if kind == "attr"}
    assert "src/werkzeug/routing/map.py" in renamed and "tests/test_routing.py" in renamed
    assert len([f for f in renamed if f.startswith("examples/")]) == 7


def test_every_caller_still_works():
    routes = Map([Rule("/", endpoint="index"), Rule("/post/<int:id>", endpoint="post", methods=["POST"]),
                  Rule("/dir/", endpoint="dir")])  # fmt: skip
    adapter = routes.bind("example.com")
    assert adapter.match_request("/") == ("index", {})
    assert adapter.match_request("/post/7", method="POST") == ("post", {"id": 7})
    rule, args = adapter.match_request("/post/7", method="POST", return_rule=True)
    assert rule.endpoint == "post" and args == {"id": 7}
    with pytest.raises(MethodNotAllowed):
        adapter.match_request("/post/7")
    with pytest.raises(NotFound):
        adapter.match_request("/missing")
    with pytest.raises(RequestRedirect):
        adapter.match_request("/dir")
    # the methods that call it through self
    assert adapter.test("/") is True and adapter.test("/missing") is False
    assert adapter.allowed_methods("/post/7") == ["POST"] or set(adapter.allowed_methods("/post/7")) == {"POST", "OPTIONS"}
    assert adapter.dispatch(lambda endpoint, values: (endpoint, values), "/post/3", method="POST") == ("post", {"id": 3})
    assert adapter.dispatch(lambda endpoint, values: endpoint, "/dir", catch_http_exceptions=True) is not None
    with pytest.raises(HTTPException):
        adapter.dispatch(lambda endpoint, values: endpoint, "/nowhere")
