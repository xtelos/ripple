"""The call graph ripple builds and answers questions from.

Everything here is plain data so it can be cached as JSON and explained in
one sitting: symbols (things you can point at), edges (proven calls), and
unresolved calls (call sites ripple saw but could not pin to one target).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass, field


@dataclass(frozen=True)
class Symbol:
    """A module, class, function or method, identified by its dotted path."""

    id: str
    kind: str  # "module" | "class" | "function" | "method"
    file: str  # repo-relative, forward slashes
    line: int
    end_line: int

    @property
    def name(self) -> str:
        return self.id.rsplit(".", 1)[-1]


@dataclass(frozen=True)
class Edge:
    """A call from one symbol to another that ripple could prove statically.

    kind says how it was proven: "call" (a name or attribute that resolves to
    one definition), "constructor" (calling a class), "override" (a subclass
    method that self.method() or a typed receiver may dispatch to), or
    "decorator" (a decorator applied when the def statement runs).
    """

    caller: str
    callee: str
    file: str
    line: int
    kind: str = "call"


@dataclass(frozen=True)
class Unresolved:
    """A call site ripple saw but will not guess a target for."""

    caller: str
    file: str
    line: int
    text: str  # the callee expression, e.g. "store.search"
    reason: str


@dataclass
class Graph:
    symbols: dict[str, Symbol] = field(default_factory=dict)
    edges: list[Edge] = field(default_factory=list)
    unresolved: list[Unresolved] = field(default_factory=list)
    external_calls: int = 0
    parse_errors: list[str] = field(default_factory=list)
    skipped_dirs: list[str] = field(default_factory=list)  # not indexed: virtualenvs, build output, ...
    modules: dict[str, str] = field(default_factory=dict)  # every indexed file -> its module name
    bases: dict[str, list[str]] = field(default_factory=dict)  # class id -> bases that are repo classes
    external_bases: dict[str, list[str]] = field(default_factory=dict)  # class id -> bases from outside the repo

    def __post_init__(self) -> None:
        self._index()

    def _index(self) -> None:
        """Build lookup tables once so queries do not rescan the edge list."""
        self.callers_index: dict[str, list[Edge]] = defaultdict(list)
        self.callees_index: dict[str, list[Edge]] = defaultdict(list)
        for edge in self.edges:
            self.callers_index[edge.callee].append(edge)
            self.callees_index[edge.caller].append(edge)

    def stats(self) -> dict:
        kinds: dict[str, int] = defaultdict(int)
        for s in self.symbols.values():
            kinds[s.kind] += 1
        files = self.modules or {s.file for s in self.symbols.values() if s.kind == "module"}
        return {
            "files": len(files),
            "symbols": dict(sorted(kinds.items())),
            "edges": len(self.edges),
            "unresolved_calls": len(self.unresolved),
            "external_calls": self.external_calls,
            "parse_errors": len(self.parse_errors),
            "module_collisions": len(self.module_collisions()),
        }

    def module_collisions(self) -> dict[str, list[str]]:
        """{module name: files} for module names more than one file maps to.

        Symbol ids start with the module name, so these files share one id
        space: a name defined in two of them is one symbol, the last file
        indexed wins, and its callers and callees are mixed together.
        """
        files: dict[str, list[str]] = defaultdict(list)
        for file, module in self.modules.items():
            files[module].append(file)
        return {module: sorted(fs) for module, fs in sorted(files.items()) if len(fs) > 1}

    def to_dict(self) -> dict:
        return {
            "symbols": [asdict(s) for s in self.symbols.values()],
            "edges": [asdict(e) for e in self.edges],
            "unresolved": [asdict(u) for u in self.unresolved],
            "external_calls": self.external_calls,
            "parse_errors": self.parse_errors,
            "skipped_dirs": self.skipped_dirs,
            "modules": self.modules,
            "bases": self.bases,
            "external_bases": self.external_bases,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Graph:
        return cls(
            symbols={s["id"]: Symbol(**s) for s in data["symbols"]},
            edges=[Edge(**e) for e in data["edges"]],
            unresolved=[Unresolved(**u) for u in data["unresolved"]],
            external_calls=data["external_calls"],
            parse_errors=data["parse_errors"],
            skipped_dirs=data["skipped_dirs"],
            modules=data["modules"],
            bases=data["bases"],
            external_bases=data["external_bases"],
        )
