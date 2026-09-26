"""Path-to-handler registry. Handlers register themselves with @route."""

ROUTES = {}


def route(path):
    def register(fn):
        ROUTES[path] = fn
        return fn

    return register


def dispatch(path, request):
    handler = ROUTES.get(path)
    if handler is None:
        return not_found(request)
    return handler(request)


def not_found(request):
    return {"status": 404, "body": "not found"}
