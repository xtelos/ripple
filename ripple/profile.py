"""What the resolver, the queries, the checker and the cache assume about the language.

The resolver decides what a call points at from facts (facts.py), and most of that
is language-neutral: scopes, imports, receivers, class hierarchies. A few things it
knows are about Python specifically: which names are builtins, which standard
library modules are safe to ask, which decorators hand a class back, what calling a
class runs, how tests are collected and run. They are gathered here, in one frozen
record, and the resolver, queries, checker and cache take it as a parameter instead
of reading module constants. PYTHON is the profile every one of them defaults to,
and holds exactly the values they used to hard-code.

A field is here only because some code reads it. A language that needs another one adds it
when it needs it, and gives PYTHON the value it has always had.

TYPESCRIPT, at the bottom, is the second profile. Its front end is ts_indexer.py.
"""

from __future__ import annotations

import builtins
import shutil
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .model import Graph
from .pytest_collection import PytestCollection


@dataclass(frozen=True)
class LanguageProfile:
    name: str

    # Which files the indexer reads and the cache fingerprints (str.endswith takes the tuple), the
    # suffixes among them that are not source (.d.ts is declarations only), and the files outside
    # the sources whose contents change what the indexer makes of them (tsconfig.json: path aliases),
    # which the cache fingerprints too. front_end names the front end that reads the sources.
    source_suffixes: tuple[str, ...]
    excluded_suffixes: tuple[str, ...]
    config_files: tuple[str, ...]
    front_end: str

    # Builtins: a name no scope binds that is still defined, and the ids ("builtins.list") of those
    # that are types, whose values run only builtin methods. not_built_as_instance are builtin types
    # that, called, do not give an instance of themselves (type(x)(...) reaches the class of x).
    builtin_names: frozenset[str]
    builtin_types: frozenset[str]
    not_built_as_instance: frozenset[str]

    # What may be asked of the running interpreter. safe_import_modules is the only list of modules
    # ripple will import to learn whether a name is a class (importing runs the module), and an empty
    # one means nothing is ever imported. stdlib_module_names tells a standard library name from a
    # dependency's. nominal_only_excluded are prefixes of names a class may satisfy without inheriting
    # from them (protocols, ABCs).
    safe_import_modules: frozenset[str]
    stdlib_module_names: frozenset[str]
    nominal_only_excluded: tuple[str, ...]

    # Decorators from outside the repo, as dotted names. same_class_decorators hand the class back
    # whether called or not, same_class_plain_decorators only when written without a call, and
    # result_keeping give a call of the decorated function the value the def returned.
    same_class_decorators: frozenset[str]
    same_class_plain_decorators: frozenset[str]
    result_keeping: frozenset[str]

    # functools.partial and functools.wraps, as the dotted names they resolve to.
    partial_names: frozenset[str]
    wraps_names: frozenset[str]

    # How imports bind. True is Python's: a module is a package whose submodules are its attributes,
    # `import a.b` binds a and a.b.c is read as the longest module prefix, and `from m import *`
    # re-exports everything. False is ES modules: a module is exactly what it exports (see
    # ExportingModule in facts.py), a namespace import is one module and members follow it, and
    # `export *` leaves out the default export.
    package_imports: bool
    # Methods of a builtin value that call a function passed by name before they return (forEach,
    # map, ...), so passing it is calling it, when it is the FIRST argument (`xs.reduce(f, init)` runs f, never
    # init, and `xs.map(f, thisArg)` never thisArg). Empty: ripple does not follow functions passed on.
    synchronous_callbacks: frozenset[str]

    # JSX. The names, as the dotted ids they resolve to, of what wraps a function into a component without changing
    # which function renders (memo, forwardRef), of what wraps one that is loaded later (lazy: ripple cannot see it),
    # and of the base classes whose `render` method React calls. Empty: the language has no components.
    component_wrappers: frozenset[str]
    lazy_wrappers: frozenset[str]
    component_bases: frozenset[str]
    # Built-in components that render their children where they stand (Fragment, Suspense): what is inside one is
    # rendered by whatever renders it, as inside a <div>.
    fragment_components: frozenset[str]
    # React functions that may replace the children of an element they are given (cloneElement, Children.map, ...), so
    # what an element passed to one holds is not known to be rendered.
    child_replacers: frozenset[str]

    # What calling a class runs (the member looked up in its bases), the member that may hand back an
    # instance of another class (so a class that defines it is not "exactly" itself), the base every
    # class has without writing it, and whether a name is a member every object has.
    constructor_name: str
    allocator_name: str
    implicit_base: str
    has_universal_member: Callable[[str], bool]

    # How a repo's tests are found and run: the class that says which symbols are collected as tests
    # and under which ids (built from the graph, with node_ids(symbol_id)), the command a repo runs
    # its tests with when none is given, and the prefixes of the lines that name a failing test.
    test_collection: Callable[[Graph], Any]
    default_test_command: Callable[[Path], list[str]]
    failure_prefixes: tuple[str, ...]


def _python_test_command(repo: Path) -> list[str]:
    """python -m pytest -q, using the repo's own virtualenv when it has one.

    ripple usually runs from its own environment, which does not have the
    project's dependencies installed, so its own interpreter is the wrong one.
    """
    for venv in (".venv", "venv"):
        python = repo / venv / "bin" / "python"
        if python.exists():
            return [str(python), "-m", "pytest", "-q"]
    python = shutil.which("python") or shutil.which("python3") or "python"
    return [python, "-m", "pytest", "-q"]


_BUILTIN_NAMES = frozenset(dir(builtins))

PYTHON = LanguageProfile(
    name="python",
    source_suffixes=(".py",),
    excluded_suffixes=(),
    config_files=(),
    front_end="python",
    builtin_names=_BUILTIN_NAMES,
    # Values of these types run only builtin methods: "x".strip(), {}.get(...).
    builtin_types=frozenset(f"builtins.{n}" for n in _BUILTIN_NAMES if isinstance(getattr(builtins, n), type)),
    not_built_as_instance=frozenset({"builtins.type", "builtins.super"}),
    # Standard library modules ripple may import to ask whether a name is a class.
    # Importing runs the module, so this is a list of ones that are safe and quiet
    # to import (not `this`, `antigravity`, `idlelib`, `tkinter`), never "any stdlib name".
    safe_import_modules=frozenset(
        {
            "argparse", "array", "bisect", "calendar", "collections", "concurrent.futures", "configparser",
            "contextlib", "copy", "csv", "datetime", "decimal", "difflib", "email.message", "enum", "fractions",
            "functools", "gzip", "hashlib", "heapq", "hmac", "html.parser", "http.client", "http.cookies",
            "io", "ipaddress", "itertools", "json", "logging", "logging.handlers", "pathlib", "pprint", "queue",
            "random", "re", "sched", "secrets", "shlex", "shutil", "socket", "sqlite3", "statistics", "string",
            "struct", "subprocess", "tarfile", "tempfile", "textwrap", "threading", "time", "types", "urllib.parse",
            "uuid", "weakref", "xml.etree.ElementTree", "zipfile",
        }
    ),  # fmt: skip
    stdlib_module_names=frozenset(sys.stdlib_module_names),
    nominal_only_excluded=("typing.", "typing_extensions.", "collections.abc.", "abc."),
    # Class decorators from outside the repo that return the class they are given (dataclass, attrs and
    # typing.final may build a new class object of the same name and bases, never a subclass or a wrapper).
    # Called or not: @dataclass, @dataclass(frozen=True). The attrs names are those of attr and attrs.
    same_class_decorators=frozenset(
        {"dataclasses.dataclass"}
        | {f"attr.{name}" for name in ("s", "attrs", "attributes", "define", "mutable", "frozen", "dataclass")}
        | {f"attrs.{name}" for name in ("define", "mutable", "frozen")}
    ),
    same_class_plain_decorators=frozenset({"functools.total_ordering", "typing.final", "typing.runtime_checkable"}),
    # Decorators from outside the repo that give a call the value the def returned (a call of what they
    # return gives that value), so the def's return annotation still says what a call gives.
    result_keeping=frozenset(
        {
            "builtins.property", "builtins.staticmethod", "builtins.classmethod",
            "functools.cached_property", "functools.cache", "functools.lru_cache",
            "abc.abstractmethod", "typing.overload", "typing.final", "typing.override",
            "typing_extensions.overload", "typing_extensions.final", "typing_extensions.override",
        }
    ),  # fmt: skip
    partial_names=frozenset({"functools.partial"}),
    wraps_names=frozenset({"functools.wraps"}),
    package_imports=True,
    synchronous_callbacks=frozenset(),
    component_wrappers=frozenset(),
    lazy_wrappers=frozenset(),
    component_bases=frozenset(),
    fragment_components=frozenset(),
    child_replacers=frozenset(),
    constructor_name="__init__",
    allocator_name="__new__",
    implicit_base="object",
    has_universal_member=lambda name: hasattr(object, name),
    test_collection=PytestCollection,
    default_test_command=_python_test_command,
    failure_prefixes=("FAILED ", "ERROR "),
)


class NoTestCollection:
    """The test collection of a language whose test runner ripple does not read yet.

    No symbol is a test, so impact lists every caller as code to update. jest and vitest
    name a test by its describe and it titles; the TypeScript front end already makes those
    the ids of the test callbacks (ts_indexer.py), but nothing maps them back to a command.
    """

    def __init__(self, graph: Graph):
        self.graph = graph

    def node_ids(self, symbol_id: str) -> list[str]:
        return []


def _no_test_command(repo: Path) -> list[str]:
    raise ValueError(
        "ripple does not know how to run TypeScript tests yet: pass the command that runs them "
        "(`--test-command` or RIPPLE_TEST_COMMAND, for example `npx vitest run`)"
    )


# What a TypeScript program can name without declaring it. A name that is not declared in the repo
# and is in builtin_names resolves to code outside the repo ("external"), never to an unresolved name,
# and the ones in builtin_types are classes whose instances run only builtin methods
# (`new Map().get(k)`, `"x".trim()`, `Promise.then`).
#
# The classes are the constructors the language itself defines (ECMAScript 2024, the global object's
# constructor properties) plus the few host classes every runtime has in common (WHATWG URL, text,
# abort and fetch types, which both browsers and Node define). Proxy, Object and Function are names
# but not types: a Proxy returns whatever its handler says, an Object has whatever properties the
# code gave it, and a value typed Function is called, which is calling code ripple cannot see.
_TS_BUILTIN_TYPES = (
    "Array String Number Boolean Symbol BigInt Date RegExp "
    "Error EvalError RangeError ReferenceError SyntaxError TypeError URIError AggregateError "
    "Map Set WeakMap WeakSet WeakRef FinalizationRegistry Promise ArrayBuffer SharedArrayBuffer DataView "
    "Int8Array Uint8Array Uint8ClampedArray Int16Array Uint16Array Int32Array Uint32Array "
    "Float32Array Float64Array BigInt64Array BigUint64Array "
    "URL URLSearchParams TextEncoder TextDecoder AbortController AbortSignal Headers Request Response FormData Blob"
).split()
# Namespaces and functions that are not classes: the ECMAScript global functions and namespace
# objects, the timers and console every runtime has, Node's module globals and the browser's window
# and document. Calling any of them is calling code outside the repo.
_TS_BUILTIN_VALUES = (
    "Object Function Math JSON Reflect Atomics Intl Proxy globalThis undefined NaN Infinity "
    "parseInt parseFloat isNaN isFinite eval encodeURI encodeURIComponent decodeURI decodeURIComponent escape unescape "
    "console setTimeout setInterval setImmediate clearTimeout clearInterval clearImmediate queueMicrotask "
    "structuredClone fetch atob btoa performance "
    "process Buffer require module exports global __dirname __filename "
    "window document navigator location history localStorage sessionStorage alert confirm prompt "
    "requestAnimationFrame cancelAnimationFrame"
).split()
# jest and vitest put these in scope without an import (vitest with `globals: true`). A test that
# imports them from "vitest" binds them in the module and they are no longer looked up here.
_TS_TEST_GLOBALS = (
    "describe it test expect beforeAll beforeEach afterAll afterEach jest vi xdescribe xit fdescribe fit"
).split()
_TS_BUILTIN_NAMES = frozenset(_TS_BUILTIN_TYPES + _TS_BUILTIN_VALUES + _TS_TEST_GLOBALS)
# What Object.prototype gives every object, so `super.toString()` needs no base that defines it.
_OBJECT_PROTOTYPE = frozenset(
    {"constructor", "hasOwnProperty", "isPrototypeOf", "propertyIsEnumerable", "toLocaleString", "toString", "valueOf"}
)

TYPESCRIPT = LanguageProfile(
    name="typescript",
    # .d.ts files declare types only and hold no code, and node_modules is skipped by the walk itself.
    source_suffixes=(".ts", ".tsx", ".mts", ".cts"),
    excluded_suffixes=(".d.ts", ".d.mts", ".d.cts"),
    # Path aliases and baseUrl change what an import means, so the cache must notice an edit to it.
    config_files=("tsconfig.json",),
    front_end="typescript",
    builtin_names=_TS_BUILTIN_NAMES,
    builtin_types=frozenset(f"builtins.{n}" for n in _TS_BUILTIN_TYPES),
    not_built_as_instance=frozenset(),
    # ripple never runs the code it reads: there is no interpreter to ask, so nothing is imported
    # and a name from outside the repo is never proven to be a class by asking for it.
    safe_import_modules=frozenset(),
    stdlib_module_names=frozenset(),
    nominal_only_excluded=(),
    # Decorators are not followed (they stay unresolved), so no decorator is trusted to hand a class
    # or a result back.
    same_class_decorators=frozenset(),
    same_class_plain_decorators=frozenset(),
    result_keeping=frozenset(),
    partial_names=frozenset(),
    wraps_names=frozenset(),
    package_imports=False,
    # The Array methods (and Map.forEach, Set.forEach) that run the function they are given before
    # they return, by their first argument. Not then/catch/setTimeout: those run it later, from the event loop.
    synchronous_callbacks=frozenset(
        {
            "forEach", "map", "filter", "reduce", "reduceRight", "find", "findIndex", "findLast",
            "findLastIndex", "some", "every", "flatMap", "sort", "toSorted",
        }
    ),  # fmt: skip
    # React, as an import from the package "react" resolves (pkg:react.memo): the wrappers a component may be
    # written through, and the classes whose `render` method React calls.
    component_wrappers=frozenset({"pkg:react.memo", "pkg:react.forwardRef"}),
    lazy_wrappers=frozenset({"pkg:react.lazy"}),
    component_bases=frozenset({"pkg:react.Component", "pkg:react.PureComponent"}),
    fragment_components=frozenset({f"pkg:react.{name}" for name in ("Fragment", "StrictMode", "Suspense", "Profiler")}),
    child_replacers=frozenset(
        {f"pkg:react.{name}" for name in ("cloneElement", "createElement", "isValidElement")}
        | {f"pkg:react.Children.{name}" for name in ("map", "forEach", "toArray", "only", "count")}
    ),
    constructor_name="constructor",
    allocator_name="",  # no member to look for: ripple does not read a constructor that returns another object
    implicit_base="",  # a class with no `extends` has no base to name
    has_universal_member=lambda name: name in _OBJECT_PROTOTYPE,
    test_collection=NoTestCollection,
    default_test_command=_no_test_command,
    failure_prefixes=("FAIL ", " FAIL "),
)
