"""What a call of a decorated function gives is its def's return annotation only when its decorators
are proven to hand back what the def returned. A wrapper that returns something else, or a decorator
ripple cannot read, leaves the call result unknown.
"""

import pytest
from conftest import callers_of

from ripple.indexer import build_graph

CLASSES = """\
    class Result:
        def wait(self):
            pass

    class Task:
        def wait(self):
            pass
    """


def repo_graph(make_repo, source):
    return build_graph(make_repo({"m.py": "import functools\n\n" + _dedent(CLASSES) + _dedent(source)}))


def _dedent(text):
    import textwrap

    return textwrap.dedent(text)


def waiters(graph):
    return callers_of(graph, "m.Result.wait")


CHANGING = """\
    def deferred(fn):
        def wrapper(*args) -> Task:
            return Task()

        return wrapper

    @deferred
    def compute(x) -> Result:
        return Result()

    def run():
        return compute(1).wait()

    def run_local():
        job = compute(1)
        return job.wait()
    """


def test_a_wrapper_that_returns_something_else_gives_the_call_result_no_type(make_repo):
    g = repo_graph(make_repo, CHANGING)
    assert waiters(g) == set()
    assert callers_of(g, "m.deferred.wrapper") == {"m.run", "m.run_local"}  # the call itself is still the wrapper's
    reasons = {u.caller: u.reason for u in g.unresolved if u.text.endswith("wait")}
    assert "decorator" in reasons["m.run"] and "decorator" in reasons["m.run_local"], reasons


PASSING = """\
    def timed(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            return fn(*args, **kwargs)

        return wrapper

    @timed
    def compute(x) -> Result:
        return Result()

    def run():
        return compute(1).wait()

    def run_local():
        job = compute(1)
        return job.wait()
    """


def test_a_wrapper_that_returns_what_the_function_returned_keeps_the_annotation(make_repo):
    assert waiters(repo_graph(make_repo, PASSING)) == {"m.run", "m.run_local"}


def test_a_stack_keeps_it_only_if_every_wrapper_does(make_repo):
    g = repo_graph(
        make_repo,
        PASSING.replace("    @timed\n", "    @deferred\n    @timed\n")
        + """\

    def deferred(fn):
        def wrapper2(*args) -> Task:
            return Task()

        return wrapper2
    """,
    )
    assert waiters(g) == set()


def test_a_decorator_that_returns_its_argument_keeps_it(make_repo):
    g = repo_graph(
        make_repo,
        """\
    REGISTRY = []

    def register(fn):
        REGISTRY.append(fn)
        return fn

    @register
    def compute(x) -> Result:
        return Result()

    def run():
        return compute(1).wait()
    """,
    )
    assert waiters(g) == {"m.run"}


@pytest.mark.parametrize(
    "body",
    [
        "result = fn(*args)\n            return result",  # not read: a bare name, not the call
        "return fn(*args) or None",
        "fn(*args)",  # returns None
        "if args:\n                return fn(*args)\n            return fn()",  # two returns, still calls of fn
    ],
)
def test_a_wrapper_not_read_as_returning_the_call_gives_no_type(make_repo, body):
    source = f"""\
    def timed(fn):
        def wrapper(*args):
            {body}

        return wrapper

    @timed
    def compute(x) -> Result:
        return Result()

    def run():
        return compute(1).wait()
    """
    g = repo_graph(make_repo, source)
    if body.startswith("if args"):
        assert waiters(g) == {"m.run"}  # every path returns a call of fn: kept
    else:
        assert waiters(g) == set()


def test_an_async_or_generator_wrapper_gives_no_type(make_repo):
    g = repo_graph(
        make_repo,
        """\
    def timed(fn):
        async def wrapper(*args):
            return fn(*args)

        return wrapper

    @timed
    def compute(x) -> Result:
        return Result()

    def run():
        return compute(1).wait()
    """,
    )
    assert waiters(g) == set()


def test_a_decorator_from_outside_the_repo_ripple_cannot_read_gives_no_type(make_repo):
    g = repo_graph(
        make_repo,
        """\
    import retrying

    @retrying.retry
    def compute(x) -> Result:
        return Result()

    def run():
        return compute(1).wait()
    """,
    )
    assert waiters(g) == set()


@pytest.mark.parametrize(
    "decorator",
    ["functools.lru_cache(maxsize=None)", "functools.lru_cache", "functools.cache", "staticmethod", "classmethod"],
)
def test_standard_decorators_that_hand_back_the_value_keep_the_annotation(make_repo, decorator):
    g = repo_graph(
        make_repo,
        f"""\
    class Box:
        @{decorator}
        def {"compute(x)" if "method" not in decorator else "compute(cls, x)"} -> Result:
            return Result()

    def run(b: Box):
        return b.compute(1).wait()
    """,
    )
    assert waiters(g) == {"m.run"}


def test_a_property_through_a_decorator_from_outside_the_repo_says_why_it_gives_no_type(make_repo):
    g = repo_graph(
        make_repo,
        """\
    from werkzeug.utils import cached_property

    class Box:
        @cached_property
        def result(self) -> Result:
            return Result()

    def run(b: Box):
        return b.result.wait()
    """,
    )
    assert waiters(g) == set()
    reasons = [u.reason for u in g.unresolved if u.text == "b.result.wait"]
    assert reasons and "decorator that may change what it gives" in reasons[0], reasons


def test_a_function_decorated_on_one_branch_only_gives_no_type(make_repo):
    g = repo_graph(
        make_repo,
        """\
    import sys

    def deferred(fn):
        def wrapper(*args) -> Task:
            return Task()

        return wrapper

    if sys.platform == "win32":
        @deferred
        def compute(x) -> Result:
            return Result()
    else:
        def compute(x) -> Result:
            return Result()

    def run():
        return compute(1).wait()
    """,
    )
    assert waiters(g) == set()


@pytest.mark.parametrize(
    "rebinding",
    [
        "fn = make_task",
        "def fn(*b):\n                return Task()",
        "if (fn := make_task):\n                pass",
        "for fn in (make_task,):\n                pass",
        "with open_task() as fn:\n                pass",
        "from tasks import fn",
    ],
)
def test_a_wrapper_that_returns_a_local_named_like_the_decorators_parameter_gives_no_type(make_repo, rebinding):
    # `return fn(*a)` is a call of the decorated function only when fn is the decorator's parameter.
    # A local fn in the wrapper is something else, and the call gives what that returns (a Task).
    g = repo_graph(
        make_repo,
        f"""\
    def make_task(*a):
        return Task()

    def swap(fn):
        def wrapper(*a):
            {rebinding}
            return fn(*a)

        return wrapper

    @swap
    def compute(x) -> Result:
        return Result()

    def run():
        return compute(1).wait()
    """,
    )
    assert waiters(g) == set()
