import os
import tempfile
from datetime import datetime, timezone

from check_helpers import references

from werkzeug import exceptions, http
from werkzeug.datastructures import IfRange
from werkzeug.middleware.shared_data import SharedDataMiddleware
from werkzeug.test import Client
from werkzeug.wrappers import Response

EPOCH = "Thu, 01 Jan 1970 00:00:00 GMT"
WHEN = datetime(2026, 9, 26, 20, 15, 7, tzinfo=timezone.utc)
WHEN_TEXT = "Sat, 26 Sep 2026 20:15:07 GMT"


def test_the_function_is_renamed():
    assert http.format_http_date(0) == EPOCH
    assert not hasattr(http, "http_date")


def test_no_mention_of_the_old_name_is_left():
    """Imports, calls, the function-local import in exceptions.py, `http.http_date`, and the
    places that pass the function itself as an argument (header_property) all count."""
    assert references("http_date") == []
    files = {f for f, _l, _k, _d in references("format_http_date")}
    assert {"src/werkzeug/sansio/response.py", "src/werkzeug/exceptions.py", "src/werkzeug/datastructures/range.py",
            "src/werkzeug/middleware/shared_data.py", "src/werkzeug/wrappers/response.py"} <= files  # fmt: skip


def test_every_caller_still_works():
    response = Response("x")
    response.expires = WHEN
    response.date = WHEN
    response.last_modified = WHEN
    assert response.headers["Expires"] == WHEN_TEXT
    assert response.headers["Date"] == WHEN_TEXT
    assert response.headers["Last-Modified"] == WHEN_TEXT
    assert IfRange(date=WHEN).to_header() == WHEN_TEXT
    headers = exceptions.TooManyRequests(retry_after=WHEN).get_headers()
    assert ("Retry-After", WHEN_TEXT) in headers
    assert Response("y").get_wsgi_headers({"REQUEST_METHOD": "GET", "SERVER_NAME": "x", "wsgi.url_scheme": "http"}) is not None
    with tempfile.TemporaryDirectory() as directory:
        with open(os.path.join(directory, "a.txt"), "w") as handle:
            handle.write("hello")
        client = Client(SharedDataMiddleware(Response("app"), {"/static": directory}))
        reply = client.get("/static/a.txt")
        assert reply.get_data() == b"hello"
        assert reply.headers["Date"].endswith(" GMT") and "Expires" in reply.headers
