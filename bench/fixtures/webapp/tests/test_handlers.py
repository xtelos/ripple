from webapp.app import handle_request
from webapp.handlers import hello


def test_hello_direct():
    assert hello({"user": "ada"})["body"] == "Hello, Ada"


def test_routes():
    assert handle_request("/hello", {"user": "ada"})["status"] == 200
    assert handle_request("/me", {})["status"] == 401
    assert handle_request("/me", {"user": "ada"})["body"] == "Hello, Ada"
    assert handle_request("/nope", {})["status"] == 404


def test_profile_view():
    response = handle_request("/profile", {"method": "GET", "user": "ada"})
    assert response["body"] == "Hello, Ada"
