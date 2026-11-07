"""What the resolver does with facts a TypeScript front end writes, and the TYPESCRIPT profile.

The facts here are built by hand, so nothing in this file needs a parser: it is the contract
between a front end for an ES-module language and the resolver. (tests/test_ts_indexer.py is the
other side, from TypeScript source to facts.)
"""

import pytest

from ripple.cache import fingerprint, profile_key
from ripple.checker import run_check
from ripple.facts import Binding, CallbackCall, ExportingModule, RawCall, Scope, TypeRef, unknown_ref
from ripple.indexer import find_source_files, walk_repo
from ripple.model import Graph
from ripple.profile import PYTHON, TYPESCRIPT
from ripple.queries import impact
from ripple.resolver import Resolver


def module(name):
    return ExportingModule(name, "module", name, f"{name}.ts", 1, 30, None)


def define(parent, name, kind="function", line=1):
    """A scope for `name` in parent, bound there. Members of a module follow a colon, others a dot."""
    sep = ":" if parent.kind == "module" else "."
    scope = Scope(f"{parent.id}{sep}{name}", kind, parent.module, parent.file, line, line + 1, parent.id)
    parent.names[name] = Binding("def", scope.id)
    return scope


def export(mod, name, binding=None):
    mod.exports[name] = binding or mod.names[name]


def resolve(*scopes):
    return Resolver({s.id: s for s in scopes}, None, [], TYPESCRIPT).run()


def edges(graph):
    return sorted((e.caller, e.callee, e.kind) for e in graph.edges)


def unresolved(graph):
    return sorted((u.caller, u.text) for u in graph.unresolved)


def test_a_named_import_follows_an_export_clause_a_default_and_export_star():
    util = module("src/util")
    helper = define(util, "helper")
    shown = define(util, "shown")
    export(util, "helper")
    util.exports["default"] = util.names["shown"]
    barrel = module("src/index")
    barrel.exports["renamed"] = Binding("from", "src/util", "helper")  # export { helper as renamed } from "./util"
    barrel.exports["label"] = Binding("from", "src/util", "default")  # export { default as label } from "./util"
    barrel.star_imports.append("src/util")  # export * from "./util"
    main = module("src/main")
    run = define(main, "run")
    for local, name in (("renamed", "renamed"), ("label", "label"), ("helper", "helper"), ("other", "default")):
        main.names[local] = Binding("from", "src/index", name)
        run.calls.append(RawCall([local], local, 4))

    graph = resolve(util, helper, shown, barrel, main, run)

    # export * leaves the default export out, so `import { default as other } from "./index"` finds nothing
    assert edges(graph) == [
        ("src/main:run", "src/util:helper", "call"),
        ("src/main:run", "src/util:helper", "call"),
        ("src/main:run", "src/util:shown", "call"),
    ]
    assert unresolved(graph) == [("src/main:run", "other")]


def test_a_module_exports_what_it_says_and_nothing_else():
    util = module("src/util")
    secret = define(util, "secret")  # declared, not exported
    main = module("src/main")
    run = define(main, "run")
    main.names["secret"] = Binding("from", "src/util", "secret")
    run.calls.append(RawCall(["secret"], "secret", 3))

    graph = resolve(util, secret, main, run)

    assert graph.edges == []
    assert [u.reason for u in graph.unresolved] == ["'secret' is not exported by module src/util"]


def test_a_namespace_import_is_one_module_even_when_module_ids_have_dots_in_them():
    # Python reads `a.b.c` as the longest module prefix. A TypeScript module id is a path and may hold dots.
    dotted = module("src/math.helpers")
    plain = module("src/math")
    area = define(dotted, "area")
    other = define(plain, "helpers")  # a name `helpers` in module src/math: not the module src/math.helpers
    export(dotted, "area")
    export(plain, "helpers")
    main = module("src/main")
    run = define(main, "run")
    main.names["ns"] = Binding("import", "src/math.helpers")
    main.names["math"] = Binding("import", "src/math")
    run.calls.append(RawCall(["ns", "area"], "ns.area", 3))
    run.calls.append(RawCall(["math", "helpers"], "math.helpers", 4))

    graph = resolve(dotted, plain, area, other, main, run)

    assert edges(graph) == [
        ("src/main:run", "src/math.helpers:area", "call"),
        ("src/main:run", "src/math:helpers", "call"),
    ]


def test_a_package_from_outside_the_repo_is_external_not_unresolved():
    main = module("src/main")
    run = define(main, "run")
    main.names["useMemo"] = Binding("from", "pkg:react", "useMemo")
    main.names["React"] = Binding("import", "pkg:react")
    run.calls.append(RawCall(["useMemo"], "useMemo", 3))
    run.calls.append(RawCall(["React", "createElement"], "React.createElement", 4))

    graph = resolve(main, run)

    assert graph.edges == [] and graph.unresolved == []
    assert graph.external_calls == 2


def test_export_star_from_a_package_next_to_a_repo_star_that_has_the_name_is_ambiguous():
    first = module("src/a")
    foo = define(first, "foo")
    export(first, "foo")
    barrel = module("src/index")
    barrel.star_imports += ["pkg:lib", "src/a"]  # the repo defines foo, and the package may too
    main = module("src/main")
    run = define(main, "run")
    main.names["foo"] = Binding("from", "src/index", "foo")
    main.names["bar"] = Binding("from", "src/index", "bar")  # defined nowhere in the repo: it may come from the package
    run.calls += [RawCall(["foo"], "foo", 3), RawCall(["bar"], "bar", 4)]

    graph = resolve(first, foo, barrel, main, run)

    # foo may be the package's or the repo's, so it is ambiguous and no edge is claimed; bar can only be the package's
    assert edges(graph) == []
    assert graph.external_calls == 1
    assert [u.caller for u in graph.unresolved] == ["src/main:run"] and "ambiguous" in graph.unresolved[0].reason


def access(chain, special):
    return RawCall(chain, ".".join(chain), 7, special=special)


def test_reading_a_getter_and_writing_a_setter_are_calls_and_nothing_else_about_a_property_is():
    # class Box { get total(); set total(v); get size(); set label(v); go() } and `use(b: Box)`.
    # A getter is a def that is_property; a setter is a def that is not a plain one (the same
    # facts Python writes for @property and @x.setter); a getter and a setter of a name share a scope.
    mod = module("src/box")
    box = define(mod, "Box", "class")
    members = {}
    for name, defs, plain, prop in (("total", 2, 1, True), ("size", 1, 1, True), ("label", 1, 0, False), ("go", 1, 1, False)):
        members[name] = define(box, name, "method")
        members[name].defs, members[name].plain_defs, members[name].is_property = defs, plain, prop
    export(mod, "Box")
    user_mod = module("src/user")
    use = define(user_mod, "use")
    use.names["b"] = Binding("var", evidence=[TypeRef("annotation", "Box", user_mod.id)])
    user_mod.names["Box"] = Binding("from", "src/box", "Box")
    scopes = {s.id: s for s in (mod, box, user_mod, use, *members.values())}
    use.calls += [
        access(["b", "total"], "access"),  # read of a getter+setter pair: edge
        access(["b", "total"], "assign"),  # write: edge
        access(["b", "size"], "access"),  # read of a getter-only: edge
        access(["b", "size"], "assign"),  # write to a getter-only: no setter runs
        access(["b", "label"], "assign"),  # write of a setter-only: edge
        access(["b", "label"], "access"),  # read of a setter-only: no getter runs
        access(["b", "go"], "access"),  # a method read, not called: not a call
        access(["b", "nothing"], "access"),  # not a member at all
        access(["c", "total"], "access"),  # a name that is not defined: no noise
    ]

    graph = Resolver(scopes, None, [], TYPESCRIPT).run()

    assert [(e.callee, e.line) for e in graph.edges] == [
        ("src/box:Box.total", 7),
        ("src/box:Box.total", 7),
        ("src/box:Box.size", 7),
        ("src/box:Box.label", 7),
    ]
    assert graph.unresolved == [] and graph.external_calls == 0


def test_a_function_passed_by_name_to_a_builtin_that_runs_it_is_called_and_otherwise_is_not():
    mod = module("src/m")
    named = define(mod, "describe")
    caller = define(mod, "run")
    caller.names["xs"] = Binding("var", evidence=[TypeRef("annotation", "Array", mod.id)])
    caller.names["param"] = Binding("var", evidence=[unknown_ref("the receiver is an unannotated parameter")])
    map_literal = RawCall(None, "[1].map", 3, special="builtin")
    map_typed = RawCall(["xs", "map"], "xs.map", 4)
    then_typed = RawCall(["xs", "then"], "xs.then", 5)
    unknown_host = RawCall(["nothing", "map"], "nothing.map", 6)
    caller.calls += [
        map_literal,
        CallbackCall(["describe"], "describe", 3, special="callback", host=map_literal, method="map"),
        map_typed,
        CallbackCall(["describe"], "describe", 4, special="callback", host=map_typed, method="map"),
        then_typed,  # then runs it later: not a call of it by this caller
        CallbackCall(["describe"], "describe", 5, special="callback", host=then_typed, method="then"),
        unknown_host,  # the host is not something ripple can see: it may or may not run it
        CallbackCall(["describe"], "describe", 6, special="callback", host=unknown_host, method="map"),
        CallbackCall(["param"], "param", 4, special="callback", host=map_typed, method="map"),  # a parameter is no function of the repo
    ]

    graph = resolve(mod, named, caller)

    assert [(e.caller, e.callee, e.line, e.kind) for e in graph.edges] == [
        ("src/m:run", "src/m:describe", 3, "callback"),
        ("src/m:run", "src/m:describe", 4, "callback"),
    ]
    # the host calls themselves are reported as they always are; a callback adds no unresolved call of its own
    assert unresolved(graph) == [("src/m:run", "nothing.map")]


def test_a_builtin_class_of_the_language_types_a_receiver_without_failing():
    # A TS-shaped builtin such as Array used to crash the resolver with KeyError: 'builtins.Array'
    mod = module("src/m")
    run = define(mod, "run")
    run.names["items"] = Binding("var", evidence=[TypeRef("annotation", "Array", mod.id)])
    run.names["seen"] = Binding("var", evidence=[TypeRef("call", "Map", mod.id)])
    run.names["when"] = Binding("var", evidence=[TypeRef("call", "Date", mod.id)])
    run.calls += [
        RawCall(["items", "push"], "items.push", 3),
        RawCall(["seen", "get"], "seen.get", 4),
        RawCall(["when", "getTime"], "when.getTime", 5),
        RawCall(["Math", "max"], "Math.max", 6),
        RawCall(["console", "log"], "console.log", 7),
        RawCall(None, "items.map(f).join", 8, path=["items", "map", "()", "join"]),
    ]

    graph = resolve(mod, run)

    assert graph.edges == []
    assert graph.external_calls == 5
    # what a builtin method returns is not known (as in Python: "x".strip().lower()), so the chain is unresolved
    assert unresolved(graph) == [("src/m:run", "items.map(f).join")]


def test_a_class_with_no_constructor_is_constructed_by_itself_and_forwards_to_its_bases():
    # the front end binds the class's own id as its constructor, and gives it `super(...args)`
    mod = module("src/m")
    base = define(mod, "Base", "class")
    ctor = define(base, "constructor", "method")
    crate = define(mod, "Crate", "class")
    crate.names["constructor"] = Binding("def", crate.id)
    crate.bases = ["Base"]
    crate.calls.append(RawCall(["constructor"], "super(...args)", 5, special="super"))
    make = define(mod, "make")
    make.calls.append(RawCall(["Crate"], "Crate", 9))
    make.calls.append(RawCall(["Base"], "Base", 10))

    graph = resolve(mod, base, ctor, crate, make)

    assert edges(graph) == [
        ("src/m:Crate", "src/m:Base.constructor", "call"),
        ("src/m:make", "src/m:Base.constructor", "constructor"),
        ("src/m:make", "src/m:Crate", "constructor"),
    ]


def test_the_typescript_profile_names_what_a_typescript_program_can_use_undeclared():
    assert TYPESCRIPT.name == "typescript" and TYPESCRIPT.front_end == "typescript"
    assert TYPESCRIPT.source_suffixes == (".ts", ".tsx", ".mts", ".cts")
    assert TYPESCRIPT.excluded_suffixes == (".d.ts", ".d.mts", ".d.cts")
    assert {"Array", "Map", "Set", "Promise", "Object", "JSON", "Math", "Date", "RegExp", "Error", "console"} <= TYPESCRIPT.builtin_names
    assert {"String", "Number", "Boolean", "Symbol", "Function", "parseInt", "describe", "it", "expect"} <= TYPESCRIPT.builtin_names
    assert {"builtins.Array", "builtins.Map", "builtins.Promise", "builtins.Date"} <= TYPESCRIPT.builtin_types
    # a type is a name too, and what is a namespace or function is not a type
    assert {t.removeprefix("builtins.") for t in TYPESCRIPT.builtin_types} <= TYPESCRIPT.builtin_names
    assert not {"builtins.Math", "builtins.JSON", "builtins.console", "builtins.parseInt"} & TYPESCRIPT.builtin_types
    assert TYPESCRIPT.safe_import_modules == frozenset()  # no interpreter to ask
    assert (TYPESCRIPT.constructor_name, TYPESCRIPT.implicit_base) == ("constructor", "")
    assert TYPESCRIPT.package_imports is False and PYTHON.package_imports is True
    assert TYPESCRIPT.has_universal_member("toString") and not TYPESCRIPT.has_universal_member("save")


def test_typescript_has_no_test_collection_and_the_checker_refuses_instead_of_running_pytest(tmp_path):
    graph = Graph()
    assert TYPESCRIPT.test_collection(graph).node_ids("tests/a.test:suite > case") == []
    with pytest.raises(ValueError, match="does not know how to run TypeScript tests"):
        run_check(tmp_path, profile=TYPESCRIPT)
    # a command the caller gives is still run, as for Python
    assert run_check(tmp_path, command=["true"], profile=TYPESCRIPT)["passed"] is True
    assert impact(Graph(), "x", profile=TYPESCRIPT)["error"]


def test_the_walk_reads_ts_tsx_mts_cts_and_leaves_out_declarations_and_dependencies(make_repo):
    repo = make_repo(
        {
            "src/a.ts": "",
            "src/b.tsx": "",
            "src/c.mts": "",
            "src/d.cts": "",
            "src/types.d.ts": "",
            "src/types.d.mts": "",
            "src/e.js": "",
            "src/f.py": "",
            "node_modules/dep/index.ts": "",
            "dist/out.ts": "",
        }
    )
    files, skipped = walk_repo(repo, TYPESCRIPT)
    assert [p.relative_to(repo).as_posix() for p in files] == ["src/a.ts", "src/b.tsx", "src/c.mts", "src/d.cts"]
    assert skipped == ["dist", "node_modules"]
    assert [p.name for p in find_source_files(repo)] == ["f.py"]  # the Python walk is unchanged


def test_the_cache_fingerprints_tsconfig_json_for_typescript_and_never_for_python(make_repo):
    repo = make_repo({"src/a.ts": "", "tsconfig.json": "{}", "mod.py": ""})
    assert sorted(fingerprint(repo, TYPESCRIPT)) == ["src/a.ts", "tsconfig.json"]
    assert sorted(fingerprint(repo)) == ["mod.py"]
    assert profile_key(TYPESCRIPT).startswith("typescript-") and profile_key(PYTHON) == ""


def star_world(*providers, explicit=None, packages=()):
    """Modules src/a, src/b ... each exporting `dup`, a barrel that `export *`s them (and the packages), and a
    caller that imports dup from the barrel."""
    scopes, barrel = [], module("src/index")
    for name in providers:
        mod = module(f"src/{name}")
        scopes += [mod, define(mod, "dup")]
        export(mod, "dup")
        barrel.star_imports.append(mod.id)
    barrel.star_imports += [f"pkg:{p}" for p in packages]
    if explicit is not None:
        barrel.exports["dup"] = explicit
    main = module("src/main")
    run = define(main, "run")
    main.names["dup"] = Binding("from", "src/index", "dup")
    run.calls.append(RawCall(["dup"], "dup", 3))
    return resolve(*scopes, barrel, main, run)


def test_a_name_two_export_stars_provide_is_ambiguous_not_the_first_ones():
    graph = star_world("a", "b")
    assert edges(graph) == []
    assert [u.caller for u in graph.unresolved] == ["src/main:run"] and "ambiguous" in graph.unresolved[0].reason


def test_a_name_one_export_star_provides_through_two_paths_is_still_that_one_name():
    first = module("src/a")
    foo = define(first, "dup")
    export(first, "dup")
    left, right = module("src/left"), module("src/right")
    left.star_imports.append("src/a")
    right.star_imports.append("src/a")
    barrel = module("src/index")
    barrel.star_imports += ["src/left", "src/right"]
    main = module("src/main")
    run = define(main, "run")
    main.names["dup"] = Binding("from", "src/index", "dup")
    run.calls.append(RawCall(["dup"], "dup", 3))

    graph = resolve(first, foo, left, right, barrel, main, run)

    assert edges(graph) == [("src/main:run", "src/a:dup", "call")]


def test_an_export_a_module_writes_itself_wins_over_every_export_star():
    scopes = []
    for name in ("mine", "a", "b"):
        mod = module(f"src/{name}")
        scopes += [mod, define(mod, "dup")]
        export(mod, "dup")
    barrel = module("src/index")
    barrel.exports["dup"] = Binding("from", "src/mine", "dup")  # export { dup } from "./mine", beside two stars that have dup
    barrel.star_imports += ["src/a", "src/b"]
    main = module("src/main")
    run = define(main, "run")
    main.names["dup"] = Binding("from", "src/index", "dup")
    run.calls.append(RawCall(["dup"], "dup", 3))

    graph = resolve(*scopes, barrel, main, run)

    assert edges(graph) == [("src/main:run", "src/mine:dup", "call")]


def test_a_package_star_next_to_a_repo_star_that_has_the_name_makes_it_ambiguous_and_alone_it_is_external():
    graph = star_world("a", packages=["lib"])
    assert edges(graph) == [] and [u.caller for u in graph.unresolved] == ["src/main:run"]
    graph = star_world(packages=["lib"])
    assert edges(graph) == [] and graph.unresolved == [] and graph.external_calls == 1
    graph = star_world("a")
    assert edges(graph) == [("src/main:run", "src/a:dup", "call")]  # one star, no package: the one answer
