"""Function handlers. Importing this module registers their routes."""

from webapp import services
from webapp.middleware import require_user, timed
from webapp.router import route


@route("/hello")
@timed
def hello(request):
    return {"status": 200, "body": services.greeting_for(request.get("user", ""))}


@route("/me")
@require_user
def me(request):
    return {"status": 200, "body": services.greeting_for(request["user"])}
