"""Class-based views: one method per HTTP verb, looked up by name."""

from webapp import services


class View:
    def handle(self, request):
        verb = request.get("method", "get").lower()
        handler = getattr(self, "on_" + verb, None)
        if handler is None:
            return {"status": 405, "body": "method not allowed"}
        return handler(request)


class ProfileView(View):
    def on_get(self, request):
        return {"status": 200, "body": self.render(request.get("user", ""))}

    def render(self, username):
        return services.greeting_for(username)
