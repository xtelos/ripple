"""Decorators that wrap handlers."""

import functools
import time


def timed(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        start = time.perf_counter()
        result = fn(*args, **kwargs)
        result["elapsed_ms"] = round((time.perf_counter() - start) * 1000, 3)
        return result

    return wrapper


def require_user(fn):
    @functools.wraps(fn)
    def wrapper(request):
        if not request.get("user"):
            return {"status": 401, "body": "login required"}
        return fn(request)

    return wrapper
