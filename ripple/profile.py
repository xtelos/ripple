"""What the resolver, the queries, the checker and the cache assume about the language.

The resolver decides what a call points at from facts (facts.py), and most of that
is language-neutral: scopes, imports, receivers, class hierarchies. A few things it
knows are about Python specifically: which names are builtins, which standard
library modules are safe to ask, which decorators hand a class back, what calling a
class runs, how tests are collected and run. They are gathered here, in one frozen
record, and the resolver, queries, checker and cache take it as a parameter instead
of reading module constants. PYTHON is the profile every one of them defaults to,
and holds exactly the values they used to hard-code.

A field is here only because Python code reads it today. A language that needs
another one adds it when it needs it.
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

    # Which files the indexer reads and the cache fingerprints (str.endswith takes the tuple).
    source_suffixes: tuple[str, ...]

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
    constructor_name="__init__",
    allocator_name="__new__",
    implicit_base="object",
    has_universal_member=lambda name: hasattr(object, name),
    test_collection=PytestCollection,
    default_test_command=_python_test_command,
    failure_prefixes=("FAILED ", "ERROR "),
)
