"""The facts a front end hands to the resolver live apart from the Python front end.

A front end for another language will write these same facts, so the resolver has
to work on them without any syntax tree, and the names the Python front end used to
define must still import from where they always did.
"""

from ripple import facts, indexer
from ripple.resolver import Resolver


def test_every_name_that_moved_is_still_importable_from_the_indexer():
    # __all__ is the list of what moved. It must be everything facts defines, so a new fact
    # cannot be added without being re-exported, and each name must be the same object.
    defined = {n for n in vars(facts) if not n.startswith("_")} - {"annotations", "dataclass", "field"}
    assert set(facts.__all__) == defined
    assert len(facts.__all__) == 24
    for name in facts.__all__:
        assert getattr(indexer, name) is getattr(facts, name), name


def test_facts_import_no_syntax_tree():
    assert "ast" not in vars(facts)


def test_the_resolver_runs_on_facts_that_no_parser_wrote():
    util = facts.Scope("app.util", "module", "app.util", "app/util.src", 1, 3, None)
    helper = facts.Scope("app.util.helper", "function", "app.util", "app/util.src", 1, 2, "app.util")
    util.names["helper"] = facts.Binding("def", "app.util.helper")
    main = facts.Scope("app.main", "module", "app.main", "app/main.src", 1, 5, None)
    run = facts.Scope("app.main.run", "function", "app.main", "app/main.src", 3, 5, "app.main")
    main.names["helper"] = facts.Binding("from", "app.util", "helper")
    main.names["run"] = facts.Binding("def", "app.main.run")
    run.calls.append(facts.RawCall(["helper"], "helper", 4))
    scopes = {s.id: s for s in (util, helper, main, run)}

    graph = Resolver(scopes, None, []).run()

    assert [(e.caller, e.callee, e.line, e.kind) for e in graph.edges] == [("app.main.run", "app.util.helper", 4, "call")]
