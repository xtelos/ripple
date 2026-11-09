"""The call graph ripple builds and answers questions from.

Everything here is plain data so it can be cached as JSON and explained in
one sitting: symbols (things you can point at), edges (proven calls), and
unresolved calls (call sites ripple saw but could not pin to one target).
"""

from __future__ import annotations

import re
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
        """The last part of the id, the name a call site writes: `price_order` for `shop.pricing.price_order`.

        A TypeScript id is a path, a colon and a member (`src/util/math:clamp`, `src/cart:Cart.add#2`):
        its name is the member's last segment without the clash suffix, `clamp` and `add`, and for a
        module the last directory or file name. A test callback (`tests/a.test:suite > case`) is named by
        its whole title chain, which a dot in a title could not be split from.
        """
        if ":" not in self.id:
            return self.id.rsplit(".", 1)[-1] if "/" not in self.id else self.id.rsplit("/", 1)[-1]
        member = self.id.split(":", 1)[1]
        if " > " in member:
            return member
        return re.sub(r"#\d+$", "", member).rsplit(".", 1)[-1]


@dataclass(frozen=True)
class Edge:
    """A call from one symbol to another that ripple could prove statically.

    kind says how it was proven: "call" (a name or attribute that resolves to
    one definition), "constructor" (calling a class), "override" (a subclass
    method that self.method() or a typed receiver may dispatch to),
    "decorator" (a decorator applied when the def statement runs) or
    "annotation" (a call written inside an annotation, made by the scope that
    holds the annotated def or class) or "wrapped" (a decorator's wrapper
    calling the function the decorator was given).

    via is set on the edges that belong to one decoration: a call by the name of a decorated
    function (it reaches the wrapper), and each "wrapped" edge of that function's decorator
    stack. It is the decorated function. One wrapper body serves every function its decorator
    is applied to, so a chain through it continues only along edges with the same via.
    """

    caller: str
    callee: str
    file: str
    line: int
    kind: str = "call"
    via: str = ""


# Why a call written inside a class whose members the front end does not read is unresolved (TypeScript: a class
# expression, `export default class {}`). Queries tell this reason from the others, so they can name it.
UNREAD_CLASS = "the call is inside a class that ripple does not read (a class expression, or a default export with no name), so what it calls is not followed"

# Why a call written in the arguments of a decorator is unresolved: the class the decorator is written on sits inside
# another class (a field initializer, a static block), so which scope runs the call is not provable, and no edge is
# made. Queries name this reason as they name UNREAD_CLASS.
DECORATOR_SCOPE = "the call is in the arguments of a decorator on a class written inside another class (a field initializer, a static block), where ripple cannot prove which scope runs it, so what it calls is not followed"


@dataclass(frozen=True)
class Unresolved:
    """A call site ripple saw but will not guess a target for."""

    caller: str
    file: str
    line: int
    text: str  # the callee expression, e.g. "store.search"
    reason: str


@dataclass(frozen=True)
class Decoration:
    """A decorator written on a class, a member or a parameter (TypeScript): `@tag`, `@route("/x")`, `@ns.tag()`.

    It is not an edge. What a decorator does to what it decorates is not modelled, so the decorator is not shown as
    called by anyone; this records only where its name is written, so a question about it can say so. holder is
    the symbol that holds the class (the decorator is written there, and runs when the class is defined), text is
    the decorator as written, name its last name (`route` for `@ns.route()`), and target the id of the repo
    symbol the name resolved to, empty when it did not resolve (a name proven to be from outside the repo is
    never recorded: it cannot be a symbol of the repo).
    """

    holder: str
    file: str
    line: int
    text: str
    name: str
    target: str = ""


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
    external_reasons: dict[str, int] = field(default_factory=dict)  # why calls count as outside the repo -> how many
    # Ids that a Python file and a TypeScript file of one repo both define, with the files (see languages.merge_graphs).
    # Held in memory only: a merged graph is never cached, each language's graph is.
    ambiguous_ids: dict[str, list[str]] = field(default_factory=dict)
    # Where a TypeScript decorator is written, whatever it resolved to (see Decoration). Empty for Python.
    decorations: list[Decoration] = field(default_factory=list)
    # TypeScript: `module:Name` -> [id, how] for a name a module exports that is a function or class declared under
    # another name or in another module (`export const Name = forwardRef(Inner)`, `export { Inner as Name }`, a
    # re-export): how says which. A bare name that is only a module's resolves through it. An empty id says the module
    # declares a function or class of that name AND exports the name as something ripple cannot name, so a question
    # about the name is ambiguous. Empty for Python.
    export_aliases: dict[str, list[str]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._index()

    def _index(self) -> None:
        """Build lookup tables once so queries do not rescan the edge list."""
        self.callers_index: dict[str, list[Edge]] = defaultdict(list)
        self.callees_index: dict[str, list[Edge]] = defaultdict(list)
        # decorated function -> the call sites of its name, which reach it through its wrapper
        self.via_index: dict[str, list[Edge]] = defaultdict(list)
        for edge in self.edges:
            self.callers_index[edge.callee].append(edge)
            self.callees_index[edge.caller].append(edge)
            if edge.via and edge.kind != "wrapped":
                self.via_index[edge.via].append(edge)

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
            "external_reasons": dict(self.external_reasons),
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
        data = {
            "symbols": [asdict(s) for s in self.symbols.values()],
            "edges": [asdict(e) for e in self.edges],
            "unresolved": [asdict(u) for u in self.unresolved],
            "external_calls": self.external_calls,
            "parse_errors": self.parse_errors,
            "skipped_dirs": self.skipped_dirs,
            "modules": self.modules,
            "bases": self.bases,
            "external_bases": self.external_bases,
            "external_reasons": self.external_reasons,
        }
        if self.decorations:  # only a graph that has some carries the key, so a Python graph is written as it always was
            data["decorations"] = [asdict(d) for d in self.decorations]
        if self.export_aliases:  # likewise: a Python graph has no key
            data["export_aliases"] = self.export_aliases
        return data

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
            external_reasons=data.get("external_reasons", {}),
            decorations=[Decoration(**d) for d in data.get("decorations", [])],
            export_aliases=data.get("export_aliases", {}),
        )
