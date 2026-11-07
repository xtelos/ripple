"""Read bench/ground_truth_ts.json, check its symbols against the fixture's text, and compare it with
the edges a trace observed (bench/ts_tracer).

The truth is written as `callers` queries (one per callee and edge kind, like bench/ground_truth.json).
A query's callers split three ways: `expected` (ripple should find them), `unresolved` (they happen at
run time but ripple must not resolve them) and `untraceable` (expected, but the tracer cannot see them).
Each unresolved and untraceable entry names a `why` from a closed set and the `site` (file:line) of the call;
`truth_problems` checks that the site's text carries the marker of the why, so a caller cannot be moved
between buckets without the source saying why.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

KINDS = ("calls", "renders", "renderer")

HERE = Path(__file__).resolve().parent
TRUTH = HERE / "ground_truth_ts.json"
FIXTURE = HERE / "fixtures" / "ts" / "warehouse-ts"

Edge = tuple[str, str]


UNRESOLVED_WHY = ("computed-key", "method-by-name", "interface-typed-receiver", "decorator", "decorator-wrapper")
UNTRACEABLE_WHY = ("debug-flag",)


def load(path: Path = TRUTH) -> dict:
    """The truth, or ValueError if a bucket entry lacks a closed `why`, a real `site` or a `reason`."""
    truth = json.loads(path.read_text())
    problems = validate(truth)
    if problems:
        raise ValueError("\n".join(problems))
    return truth


def edge_sets(truth: dict) -> dict[str, dict[str, set[Edge]]]:
    """Per edge kind: the expected, unresolved, untraceable and non-edge (caller, callee) pairs."""
    sets = {kind: {"expected": set(), "unresolved": set(), "untraceable": set(), "non_edge": set()} for kind in KINDS}
    for q in truth["queries"]:
        s = sets[q["edge"]]
        s["expected"] |= {(caller, q["symbol"]) for caller in q["expected"]}
        s["unresolved"] |= {(caller, q["symbol"]) for caller in q.get("unresolved", {})}
        s["untraceable"] |= {(caller, q["symbol"]) for caller in q.get("untraceable", {})}
    for n in truth["non_edges"]:
        sets[n["edge"]]["non_edge"].add((n["from"], n["to"]))
    return sets


def compare(truth: dict, observed: dict[str, list[list[str]]]) -> list[str]:
    """What disagrees: an empty list means the trace and the truth say the same thing.

    Per kind, the observed edges must be exactly the expected ones the tracer can see plus the
    unresolved ones. An untraceable edge that was observed is a wrong mark, and a non-edge that was
    observed is a wrong truth.
    """
    problems = []
    for kind, s in edge_sets(truth).items():
        seen = {(a, b) for a, b in observed.get(kind, [])}
        want = (s["expected"] - s["untraceable"]) | s["unresolved"]
        problems += [f"{kind}: observed but not in the truth: {a} -> {b}" for a, b in sorted(seen - want - s["untraceable"])]
        problems += [f"{kind}: in the truth but never observed: {a} -> {b}" for a, b in sorted(want - seen)]
        problems += [f"{kind}: marked untraceable but observed: {a} -> {b}" for a, b in sorted(s["untraceable"] & seen)]
        problems += [f"{kind}: a non-edge was observed: {a} -> {b}" for a, b in sorted(s["non_edge"] & seen)]
        problems += [f"{kind}: a non-edge is also listed as an edge: {a} -> {b}" for a, b in sorted(s["non_edge"] & (s["expected"] | s["unresolved"]))]
    return problems


# ---- the symbols the truth names must exist in the fixture's sources

_DECLARES = (
    r"(?:function\s*\*?\s+{n}\b|class\s+{n}\b|(?:const|let|var)\s+{n}\b"
    r"|^[ \t]*(?:(?:public|private|protected|static|async|override|readonly|get|set)\s+)*{n}\s*[(<])"
)


def source_file(module: str) -> Path | None:
    """The file a module id names: path.ts, path.tsx, or path/index.ts(x). The root is index."""
    base = FIXTURE / module  # not with_suffix: a module id like tests/ui.test already contains a dot
    for candidate in (FIXTURE / f"{module}.ts", FIXTURE / f"{module}.tsx", base / "index.ts", base / "index.tsx"):
        if candidate.is_file():
            return candidate
    return None


def symbol_problem(symbol: str) -> str | None:
    """None when the fixture's text declares the symbol, else why not. A plain text check, not a parse."""
    module, colon, name = symbol.partition(":")
    path = source_file(module)
    if path is None:
        return f"no source file for module {module!r}"
    if not colon:
        return None
    text = path.read_text()
    if " > " in name or name in _test_titles(text):
        titles = name.split(" > ")
        at = 0
        for title in titles:
            found = re.search(r"\b(?:describe|it|test)\(\s*(['\"`])" + re.escape(title) + r"\1", text[at:])
            if not found:
                return f"no describe/it titled {title!r} in {path.name} (in order)"
            at += found.end()
        return None
    at = 0
    for part in name.split("."):
        found = re.search(_DECLARES.format(n=re.escape(part)), text[at:], re.MULTILINE)
        if not found:
            return f"{part!r} is not declared in {path.name} (in order)"
        at += found.end()
    return None


def _test_titles(text: str) -> set[str]:
    return set(re.findall(r"\b(?:it|test)\(\s*['\"`]([^'\"`]+)['\"`]", text))



# ---- the bucket a caller is in must be justified by the source

_SITE = re.compile(r"^(?P<file>[\w./-]+):(?P<line>\d+)$")
_INDEXED_CALL = re.compile(r"\b(?P<obj>\w+)\[[^\]\n]+\]\(")
_DEBUG_FLAG = re.compile(r"__[A-Z_]*DEBUG[A-Z_]*__")


def validate(truth: dict) -> list[str]:
    """Structure only: every unresolved and untraceable entry has a closed why, a real file:line, a reason."""
    problems = []
    for q in truth["queries"]:
        for bucket, allowed in (("unresolved", UNRESOLVED_WHY), ("untraceable", UNTRACEABLE_WHY)):
            for caller, entry in q.get(bucket, {}).items():
                where = f"{q['id']} {bucket} {caller}"
                if not isinstance(entry, dict):
                    problems.append(f"{where}: needs {{why, site, reason}}")
                    continue
                if entry.get("why") not in allowed:
                    problems.append(f"{where}: why {entry.get('why')!r} is not one of {allowed}")
                if not str(entry.get("reason", "")).strip():
                    problems.append(f"{where}: no reason")
                found = _SITE.match(str(entry.get("site", "")))
                if not found:
                    problems.append(f"{where}: site must be file:line, not {entry.get('site')!r}")
                elif not (FIXTURE / found["file"]).is_file():
                    problems.append(f"{where}: no file {found['file']}")
                elif not 1 <= int(found["line"]) <= len((FIXTURE / found["file"]).read_text().splitlines()):
                    problems.append(f"{where}: {entry['site']} is past the end of the file")
    return problems


def _name_of(caller: str) -> str:
    return re.split(r"[.:]", caller)[-1]


def _guarded(lines: list[str], index: int) -> bool:
    return any(_DEBUG_FLAG.search(line) for line in lines[max(0, index - 3) : index + 1])


def _marker_problem(why: str, caller: str, site: str) -> str | None:
    """Why the text at `site` does not carry the marker of `why`, or None when it does."""
    file, _, number = site.rpartition(":")
    lines = (FIXTURE / file).read_text().splitlines()
    text, index = lines[int(number) - 1], int(number) - 1
    whole = "\n".join(lines)
    if why in ("computed-key", "method-by-name"):
        found = _INDEXED_CALL.search(text)
        if not found:
            return "no `obj[key](` call on that line"
        obj = found["obj"]
        is_table = re.search(rf"\bconst\s+{obj}\b", whole) is not None
        if why == "computed-key" and not is_table:
            return f"{obj} is not a table declared with const in {file}"
        if why == "method-by-name" and (is_table or not re.search(rf"\b{obj}\s*:", whole)):
            return f"{obj} is not a parameter whose method is chosen by name"
        return None
    if why == "interface-typed-receiver":
        for recv in re.findall(r"\b(\w+)\.\w+\(", text):
            for declared in re.findall(rf"\b{recv}\s*:\s*(\w+)", whole):
                if re.search(rf"\binterface\s+{declared}\b", whole):
                    return None
        return "no call on a receiver declared with an interface from this file"
    if why == "decorator":
        return None if re.match(r"\s*@\w+", text) else "not a decorator line"
    if why == "decorator-wrapper":
        applied = any(f"@{_name_of(caller)}" in p.read_text() for p in FIXTURE.glob("src/**/*.ts*"))
        return None if re.search(r"\.(apply|call)\(", text) and applied else "not an apply or call in a function used as @decorator"
    if why == "debug-flag":
        if _guarded(lines, index):
            return None
        name = _name_of(caller)
        sites = [i for i, line in enumerate(lines) if re.search(rf"(?<!\w){name}\(", line) and not line.rstrip().endswith("{")]
        return None if sites and all(_guarded(lines, i) for i in sites) else "neither the call nor every call of its caller is under a flag guard"
    return f"unknown why {why!r}"


def _caller_text(caller: str) -> str | None:
    """The source text of a caller: the whole file for a module, else its declaration through its closing line."""
    module, colon, name = caller.partition(":")
    path = source_file(module)
    if path is None:
        return None
    text = path.read_text()
    if not colon:
        return text
    lines = text.splitlines()
    starts = []
    if " > " in name or name in _test_titles(text):
        found = re.search(r"\b(?:describe|it|test)\(\s*(['\"`])" + re.escape(name.split(" > ")[-1]) + r"\1", text)
        starts = [text.count("\n", 0, found.start())] if found else []
    else:
        at, parts = 0, name.split(".")
        for part in parts[:-1]:
            found = re.search(_DECLARES.format(n=re.escape(part)), text[at:], re.MULTILINE)
            if not found:
                return None
            at += found.end()
        # an accessor pair shares one symbol, so every declaration of the last part counts
        starts = [text.count("\n", 0, at + m.start()) for m in re.finditer(_DECLARES.format(n=re.escape(parts[-1])), text[at:], re.MULTILINE)]
    chunks = []
    for start in starts:
        indent = len(lines[start]) - len(lines[start].lstrip())
        end = start + 1
        while end < len(lines):
            line = lines[end]
            if line.strip() and len(line) - len(line.lstrip()) <= indent:
                if line.lstrip()[0] == ")" and line.rstrip().endswith("{"):  # the end of a multi-line signature, not of the body
                    end += 1
                    continue
                end += 1 if line.lstrip()[0] in "})]" else 0
                break
            end += 1
        chunks.append("\n".join(lines[start:end]))
    return "\n".join(chunks) or None


def _edge_problem(caller: str, callee: str) -> str | None:
    """Why `caller` could not be an expected (resolvable) caller of `callee`, from their source text."""
    text = _caller_text(caller)
    module, colon, qual = callee.partition(":")
    if text is None or not colon:
        return None if text is not None else f"no source text for {caller}"
    *owners, last = qual.split(".")
    cls = owners[-1] if owners else None
    needles = [r"\bsuper\s*\(", rf"\b{cls}\b"] if last == "constructor" else [rf"\b{re.escape(last)}\b"]
    if not any(re.search(n, text) for n in needles):
        return f"the text of {caller} never names {last}"
    callee_path = source_file(module)
    implemented = re.search(rf"\bclass\s+{cls}\b[^{{]*\bimplements\s+(\w+)", callee_path.read_text()) if cls and callee_path else None
    if implemented and last != "constructor":
        iface = implemented[1]
        if re.search(rf"\b\w+\s*:\s*{iface}\b", text) and re.search(rf"\.{last}\(", text) and not re.search(rf"\b{cls}\b", text):
            return f"{caller} reaches {last} only through a receiver typed {iface}: that is unresolved"
    return None


def truth_problems(truth: dict) -> list[str]:
    """Everything wrong with how callers are bucketed, judged from the fixture's text alone.

    Structure (validate), the marker of each why at its site, and for every expected caller that its
    text names the callee and does not reach it only through an interface-typed receiver.
    """
    problems = validate(truth)
    if problems:
        return problems
    for q in truth["queries"]:
        for bucket in ("unresolved", "untraceable"):
            for caller, entry in q.get(bucket, {}).items():
                missing = _marker_problem(entry["why"], caller, entry["site"])
                if missing:
                    problems.append(f"{q['id']} {bucket} {caller}: {entry['why']} at {entry['site']}: {missing}")
        if q["edge"] in ("calls", "renders"):  # a renderer is placed by React, so its text names nothing
            for caller in q["expected"]:
                missing = _edge_problem(caller, q["symbol"])
                if missing:
                    problems.append(f"{q['id']} expected {caller}: {missing}")
    return problems
