"""Top-level request entry point."""

from webapp import handlers  # noqa: F401  (importing registers the routes)
from webapp.router import dispatch
from webapp.views import ProfileView

PROFILE = ProfileView()


def handle_request(path, request):
    if path == "/profile":
        return PROFILE.handle(request)
    return dispatch(path, request)
