from conftest import callers_of

from ripple.indexer import build_graph


def test_an_overloaded_function_gives_no_return_type(make_repo):
    # f(1) is an A and f("s") is a B: taking the first signature would be a guess.
    repo = make_repo(
        {
            "m.py": """\
                from typing import overload

                class A:
                    def run(self):
                        pass

                class B:
                    def run(self):
                        pass

                @overload
                def f(x: int) -> A: ...

                @overload
                def f(x: str) -> B: ...

                def f(x):
                    return A() if isinstance(x, int) else B()

                def use():
                    f("s").run()
                    made = f(1)
                    made.run()
                """
        }
    )
    g = build_graph(repo)
    assert callers_of(g, "m.A.run") == set() and callers_of(g, "m.B.run") == set()


def test_overloads_that_all_return_one_class_keep_it(make_repo):
    # click.get_current_context is Context, or Context | None: the class is the same either way.
    repo = make_repo(
        {
            "m.py": """\
                from typing import overload

                class Ctx:
                    def run(self):
                        pass

                @overload
                def current(silent: bool = False) -> Ctx: ...

                @overload
                def current(silent: bool = ...) -> Ctx | None: ...

                def current(silent=False):
                    return Ctx()

                def use():
                    current().run()
                """
        }
    )
    assert callers_of(build_graph(repo), "m.Ctx.run") == {"m.use"}


def test_two_defs_of_a_name_that_annotate_differently_give_no_return_type(make_repo):
    repo = make_repo(
        {
            "m.py": """\
                import sys

                class A:
                    def run(self):
                        pass

                class B:
                    def run(self):
                        pass

                if sys.platform == "win32":
                    def make() -> A:
                        return A()
                else:
                    def make() -> B:
                        return B()

                def use():
                    make().run()
                """
        }
    )
    assert callers_of(build_graph(repo), "m.A.run") == set()


def test_a_property_setter_does_not_make_the_getter_ambiguous(make_repo):
    repo = make_repo(
        {
            "m.py": """\
                class Engine:
                    def start(self):
                        pass

                class Car:
                    @property
                    def engine(self) -> Engine:
                        return Engine()

                    @engine.setter
                    def engine(self, value) -> None:
                        pass

                def f(car: Car):
                    car.engine.start()
                """
        }
    )
    assert callers_of(build_graph(repo), "m.Engine.start") == {"m.f"}


WINDOWS_FIRST = """\
    import sys

    class WinThing:
        def run(self):
            pass

    class PosixThing:
        def run(self):
            pass

    if sys.platform == "win32":
        def opener():
            return WinThing()
    else:
        def opener() -> PosixThing:
            return PosixThing()

    def go():
        return opener().run()

    def go2():
        t = opener()
        return t.run()
    """


def test_an_unannotated_def_before_an_annotated_one_gives_no_return_type(make_repo):
    # On Windows opener() is a WinThing, so the annotation on the other branch's def proves nothing.
    g = build_graph(make_repo({"m.py": WINDOWS_FIRST}))
    assert callers_of(g, "m.PosixThing.run") == set() and callers_of(g, "m.WinThing.run") == set()


def test_an_annotated_def_before_an_unannotated_one_gives_no_return_type(make_repo):
    source = WINDOWS_FIRST.replace("def opener():", "def opener() -> PosixThing:").replace(
        "    else:\n        def opener() -> PosixThing:", "    else:\n        def opener():"
    )
    g = build_graph(make_repo({"m.py": source}))
    assert callers_of(g, "m.PosixThing.run") == set()


def test_a_method_defined_twice_with_one_annotation_gives_no_return_type(make_repo):
    repo = make_repo(
        {
            "m.py": """\
                import sys

                class Conn:
                    def run(self):
                        pass

                class Pool:
                    if sys.platform == "win32":
                        def get(self):
                            return None
                    else:
                        def get(self) -> Conn:
                            return Conn()

                def use(p: Pool):
                    p.get().run()
                """
        }
    )
    assert callers_of(build_graph(repo), "m.Conn.run") == set()


def test_overloads_with_an_unannotated_implementation_still_keep_one_class(make_repo):
    repo = make_repo(
        {
            "m.py": """\
                from typing import overload

                class Ctx:
                    def run(self):
                        pass

                @overload
                def current(silent: bool = False) -> Ctx: ...

                @overload
                def current(silent: int = ...) -> Ctx: ...

                def current(silent=False):
                    return Ctx()

                def use():
                    current().run()
                """
        }
    )
    assert callers_of(build_graph(repo), "m.Ctx.run") == {"m.use"}
