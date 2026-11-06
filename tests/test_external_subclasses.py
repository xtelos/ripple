"""A value annotated with a class from outside the repo may be an instance of a repo subclass.

The subclass can sit anywhere below the annotated class, through classes ripple never reads
(logging.Handler -> StreamHandler -> JsonHandler), so the repo class's base is compared with the
annotated class by what the interpreter says about them, never by the last word of the name.
"""

import pytest

from ripple import queries
from ripple.indexer import build_graph


def missed(graph, symbol):
    return sorted((m["call"], m["caller"]) for m in queries.impact(graph, symbol)["possible_missed_callers"])


def test_a_repo_subclass_of_a_subclass_of_the_annotated_stdlib_class_is_not_ruled_out(make_repo):
    repo = make_repo(
        {
            "app.py": """\
                import logging

                class JsonHandler(logging.StreamHandler):
                    def emit(self, record):
                        return "json"

                def send(h: logging.Handler, record):
                    return h.emit(record)
                """
        }
    )
    g = build_graph(repo)
    assert missed(g, "app.JsonHandler.emit") == [("h.emit", "app.send")]
    assert "app.JsonHandler.emit" not in {e.callee for e in g.edges}


@pytest.mark.parametrize(
    "base, annotated, attr",
    [
        ("queue.PriorityQueue", "queue.Queue", "put"),
        ("threading.Timer", "threading.Thread", "run"),
        ("logging.FileHandler", "logging.Handler", "emit"),
    ],
)
def test_each_stdlib_chain_stays_possible(make_repo, base, annotated, attr):
    module = base.split(".")[0]
    repo = make_repo(
        {
            "m.py": f"""\
                import {module}

                class Sub({base}):
                    def {attr}(self, *a, **k):
                        return "sub"

                def go(x: {annotated}):
                    return x.{attr}()
                """
        }
    )
    assert missed(build_graph(repo), f"m.Sub.{attr}") == [(f"x.{attr}", "m.go")]


def test_a_base_ripple_cannot_ask_about_may_sit_anywhere(make_repo):
    # dep.Worker is a dependency's class: it may itself inherit threading.Thread.
    repo = make_repo(
        {
            "m.py": """\
                import threading
                import dep

                class Mine(dep.Worker):
                    def run(self):
                        return "mine"

                def go(t: threading.Thread):
                    return t.run()
                """
        }
    )
    assert missed(build_graph(repo), "m.Mine.run") == [("t.run", "m.go")]


def test_a_base_whose_class_is_not_a_subclass_of_the_annotation_is_still_outside_the_repo(make_repo):
    # Both are standard library classes and the interpreter says one is not below the other:
    # `put` on a queue.Queue cannot reach a logging handler.
    repo = make_repo(
        {
            "m.py": """\
                import logging
                import queue

                class Mine(logging.StreamHandler):
                    def put(self, item):
                        return "mine"

                def go(q: queue.Queue):
                    return q.put(1)
                """
        }
    )
    g = build_graph(repo)
    assert g.unresolved == []


def test_a_class_with_a_computed_base_may_sit_below_anything(make_repo):
    repo = make_repo(
        {
            "m.py": """\
                import logging

                def pick():
                    return logging.Handler

                class Mine(pick()):
                    def emit(self, record):
                        return "mine"

                def go(h: logging.Handler, r):
                    return h.emit(r)
                """
        }
    )
    assert missed(build_graph(repo), "m.Mine.emit") == [("h.emit", "m.go")]
