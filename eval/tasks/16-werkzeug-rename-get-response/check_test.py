from check_helpers import references

from werkzeug.exceptions import HTTPException, NotFound
from werkzeug.routing import Map, RequestRedirect, Rule
from werkzeug.test import Client, EnvironBuilder
from werkzeug.wrappers import Request


def test_the_exception_method_and_its_override_are_renamed():
    assert "build_response" in vars(HTTPException) and "get_response" not in vars(HTTPException)
    assert "build_response" in vars(RequestRedirect) and "get_response" not in vars(RequestRedirect)


def test_only_the_examples_unrelated_method_keeps_the_old_name():
    """examples/cupoftee defines its own Page.get_response, which has nothing to do with exceptions."""
    left = references("get_response")
    assert sorted({f for f, _l, _k, _d in left}) == ["examples/cupoftee/application.py", "examples/cupoftee/pages.py"]
    assert any(kind == "def" and cls == "Page" for _f, _l, kind, cls in left)
    used = {f for f, _l, kind, _d in references("build_response") if kind == "attr"}
    assert {"src/werkzeug/exceptions.py", "src/werkzeug/wrappers/request.py", "tests/test_exceptions.py"} <= used


def test_every_caller_still_works():
    environ = EnvironBuilder("/missing").get_environ()
    response = NotFound().build_response(environ)
    assert response.status_code == 404 and b"Not Found" in response.get_data()

    redirect = RequestRedirect("http://example.com/new/")
    assert redirect.build_response(environ).status_code == 308
    assert redirect.build_response(environ).headers["Location"] == "http://example.com/new/"

    seen = []
    assert NotFound()(environ, lambda status, headers: seen.append(status)) is not None
    assert seen == ["404 NOT FOUND"]

    @Request.application
    def app(request):
        raise NotFound("nothing here")

    reply = Client(app).get("/")
    assert reply.status_code == 404 and b"nothing here" in reply.get_data()

    routes = Map([Rule("/dir/", endpoint="dir")])

    @Request.application
    def redirecting(request):
        return routes.bind_to_environ(request.environ).match("/dir")

    assert Client(redirecting).get("/").status_code == 308
