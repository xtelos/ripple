"""A decorator whose wrapper calls the function it wraps: the name is bound to the wrapper."""

from conftest import callees_of, callers_of

from ripple.indexer import build_graph

TIMED = """\
    import functools

    def timed(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            return fn(*args, **kwargs)

        return wrapper
    """


def kinds(graph, caller, callee):
    return sorted(e.kind for e in graph.edges if e.caller == caller and e.callee == callee)


def test_a_call_by_name_reaches_the_wrapper_and_the_wrapper_reaches_the_function(make_repo):
    repo = make_repo(
        {
            "m.py": TIMED
            + """\

    @timed
    def hello():
        pass

    def use():
        hello()
    """
        }
    )
    g = build_graph(repo)
    assert callers_of(g, "m.hello") == {"m.timed.wrapper"}
    assert callers_of(g, "m.timed.wrapper") == {"m.use"}
    assert kinds(g, "m.timed.wrapper", "m.hello") == ["wrapped"]
    assert kinds(g, "m", "m.timed") == ["decorator"]  # the module still applies the decorator
    assert g.unresolved == []


def test_a_stack_binds_the_name_to_the_top_wrapper_and_each_wrapper_calls_the_one_below(make_repo):
    repo = make_repo(
        {
            "m.py": TIMED
            + """\

    def guarded(fn):
        def check(*args):
            return fn(*args)

        return check

    @guarded
    @timed
    def hello():
        pass

    def use():
        hello()
    """
        }
    )
    g = build_graph(repo)
    assert callers_of(g, "m.guarded.check") == {"m.use"}
    assert callers_of(g, "m.timed.wrapper") == {"m.guarded.check"}
    assert callers_of(g, "m.hello") == {"m.timed.wrapper"}


def test_a_decorator_that_returns_its_argument_leaves_the_binding_alone(make_repo):
    repo = make_repo(
        {
            "m.py": """\
                REGISTRY = []

                def register(fn):
                    REGISTRY.append(fn)
                    return fn

                @register
                def hello():
                    pass

                def use():
                    hello()
                """
        }
    )
    g = build_graph(repo)
    assert callers_of(g, "m.hello") == {"m.use"}
    assert kinds(g, "m", "m.register") == ["decorator"]


def test_a_factory_that_returns_a_def_written_inside_it_is_the_decorator_applied(make_repo):
    repo = make_repo(
        {
            "m.py": """\
                ROUTES = {}

                def route(path):
                    def register(fn):
                        ROUTES[path] = fn
                        return fn

                    return register

                @route("/hello")
                def hello():
                    pass
                """
        }
    )
    g = build_graph(repo)
    assert callers_of(g, "m.route.register") == {"m"}
    assert kinds(g, "m", "m.route.register") == ["decorator"]


def test_a_factory_method_a_subclass_overrides_may_return_another_decorator(make_repo):
    repo = make_repo(
        {
            "m.py": """\
                class App:
                    def route(self, path):
                        def register(fn):
                            return fn

                        return register

                class Sub(App):
                    def route(self, path):
                        return lambda fn: fn

                app: App = App()

                @app.route("/x")
                def hello():
                    pass
                """
        }
    )
    g = build_graph(repo)
    assert callers_of(g, "m.App.route.register") == set()
    assert any(u.text == "app.route('/x')" and "factory" in u.reason for u in g.unresolved)


def test_two_return_paths_are_not_read(make_repo):
    repo = make_repo(
        {
            "m.py": """\
                def maybe(enabled):
                    def decorate(fn):
                        if not enabled:
                            return fn

                        def wrapper(*args):
                            return fn(*args)

                        return wrapper

                    return decorate

                @maybe(True)
                def hello():
                    pass

                def use():
                    hello()
                """
        }
    )
    g = build_graph(repo)
    assert callers_of(g, "m.hello") == {"m.use"}  # called directly, as before
    assert callers_of(g, "m.maybe.decorate.wrapper") == set()


def test_a_wrapper_with_another_decorator_is_not_the_returned_function(make_repo):
    repo = make_repo(
        {
            "m.py": """\
                import contextlib

                def deco(fn):
                    @contextlib.contextmanager
                    def wrapper(*args):
                        yield fn(*args)

                    return wrapper

                @deco
                def hello():
                    pass

                def use():
                    hello()
                """
        }
    )
    g = build_graph(repo)
    assert callers_of(g, "m.hello") == {"m.use"}


def test_a_decorator_from_outside_the_repo_above_a_wrapper_means_the_name_is_not_known(make_repo):
    repo = make_repo(
        {
            "m.py": TIMED
            + """\

    @functools.lru_cache(maxsize=None)
    @timed
    def hello():
        pass

    def use():
        hello()
    """
        }
    )
    g = build_graph(repo)
    # What hello is bound to is lru_cache's wrapper, so the direct call stays as before, and the
    # wrapper still calls the function it was given.
    assert callers_of(g, "m.hello") == {"m.use", "m.timed.wrapper"}


def test_a_wrapper_that_never_calls_its_argument_still_takes_the_calls(make_repo):
    repo = make_repo(
        {
            "m.py": """\
                def block(fn):
                    def wrapper(*args):
                        return None

                    return wrapper

                @block
                def hello():
                    pass

                def use():
                    hello()
                """
        }
    )
    g = build_graph(repo)
    assert callers_of(g, "m.block.wrapper") == {"m.use"}
    assert callers_of(g, "m.hello") == set()


def test_a_decorator_that_rebinds_its_parameter_is_not_read(make_repo):
    repo = make_repo(
        {
            "m.py": """\
                def other(fn):
                    return fn

                def deco(fn):
                    fn = other(fn)

                    def wrapper(*args):
                        return fn(*args)

                    return wrapper

                @deco
                def hello():
                    pass

                def use():
                    hello()
                """
        }
    )
    g = build_graph(repo)
    assert callers_of(g, "m.hello") == {"m.use"}


def test_one_wrapper_calls_every_function_decorated_with_it(make_repo):
    repo = make_repo(
        {
            "m.py": TIMED
            + """\

    @timed
    def one():
        pass

    @timed
    def two():
        pass
    """
        }
    )
    g = build_graph(repo)
    assert callees_of(g, "m.timed.wrapper") == {"m.one", "m.two"}


def test_a_decorator_also_called_directly_keeps_the_wrapped_call_unresolved_too(make_repo):
    repo = make_repo(
        {
            "m.py": TIMED
            + """\

    def plain():
        pass

    @timed
    def decorated():
        pass

    wrapped = timed(plain)
    """
        }
    )
    g = build_graph(repo)
    assert callees_of(g, "m.timed.wrapper") == {"m.decorated"}  # plain is not one it is known to wrap
    assert [u.text for u in g.unresolved if u.caller == "m.timed.wrapper"] == ["fn"]


def test_a_decorated_method_is_reached_through_its_wrapper_and_an_undecorated_override_directly(make_repo):
    repo = make_repo(
        {
            "m.py": TIMED
            + """\

    class Base:
        @timed
        def run(self):
            pass

    class Sub(Base):
        def run(self):
            pass

    def use(thing: Base):
        thing.run()
    """
        }
    )
    g = build_graph(repo)
    assert callers_of(g, "m.timed.wrapper") == {"m.use"}
    assert callers_of(g, "m.Sub.run") == {"m.use"}
    assert callers_of(g, "m.Base.run") == {"m.timed.wrapper"}


def test_a_decorated_function_that_calls_itself_calls_its_wrapper(make_repo):
    repo = make_repo(
        {
            "m.py": TIMED
            + """\

    @timed
    def fact(n):
        return 1 if n < 2 else n * fact(n - 1)
    """
        }
    )
    g = build_graph(repo)
    assert callers_of(g, "m.timed.wrapper") == {"m.fact"}


def test_a_generator_or_async_decorator_is_not_read(make_repo):
    repo = make_repo(
        {
            "m.py": """\
                def gen(fn):
                    def wrapper():
                        return fn()

                    yield wrapper
                    return wrapper

                @gen
                def hello():
                    pass

                def use():
                    hello()
                """
        }
    )
    assert callers_of(build_graph(repo), "m.hello") == {"m.use"}
