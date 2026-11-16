"""The allow-lists of the CommonJS model are closed, by two nets that walk every token of every probe.

An allow-list rots when a new kind of node, a new spelling or a new position appears that nobody listed. Both nets here are walks of the
tree that do not ask the reader which tokens it looked at: they find the tokens themselves, so a token the reader never saw is a failure.

1. The REFERENCE net (the first test): every named leaf whose text is a name the model watches (`require`, `module`, `exports`,
   `arguments`, `eval`, `Function`, `process`, `global`, `globalThis`, and the names a require was bound to) has an outcome in
   reader.refs: a listed harmless form, or an opener ("open": this file's exports, "reflect": every module's). An unlisted kind of node
   opens in the reader and fails here.
2. The LEXICAL net (the tests after it): every token of every file, whatever its node kind, that spells a watched name after its
   escapes are decoded (an identifier, a property name, a shorthand property, a destructuring key, a string, a template with no
   substitution) has an outcome in reader.net: a position the reader lists as harmless, or a reflect. The names that hand out the loader,
   the prototype every object inherits from or a function's constructor may be harmless only as a key that is defined. The decoder
   here is written separately from the reader's, so a spelling both read the same way is not shared by a common mistake.

Each net is shown to bite: a source with a spelling the net is told not to see fails the walk (the monkeypatched tests).
"""

import re
from pathlib import Path

import pytest

pytest.importorskip("tree_sitter", reason="TypeScript support is not installed (pip install 'ripple-code[typescript]')")
pytest.importorskip("tree_sitter_typescript", reason="TypeScript support is not installed (pip install 'ripple-code[typescript]')")

from ripple import ts_indexer  # noqa: E402
from ripple.ts_indexer import NOT_A_NAME, OK, OPEN, REFLECT, WATCHED, CommonJsReader, load_parsers, walk  # noqa: E402

PROBES = Path(__file__).resolve().parent / "fixtures" / "js_probes" / "cjs"
FILES = sorted(p for p in PROBES.rglob("*") if p.is_file() and not p.is_symlink() and p.name not in ("PROBE.json", "expected.json") and ".json" not in p.suffixes[-1:])
_, TSX = load_parsers()

# the watched names, spelled out here so that dropping one from the reader's list fails (the reader's own list is imported only to check it holds these)
NET_NAMES = {
    "require", "module", "exports", "arguments", "eval", "Function", "process", "global", "globalThis",
    "constructor", "__proto__", "prototype", "getPrototypeOf",
    "mainModule", "getBuiltinModule", "binding", "dlopen", "caller", "callee", "Reflect", "Proxy", "prepareStackTrace", "getThis", "getFunction", "cache",
}  # fmt: skip
BUILTIN_STRINGS = {"module", "vm", "inspector", "inspector/promises", "repl", "process", "node:module", "node:vm", "node:inspector", "node:inspector/promises", "node:repl", "node:process"}
# names that are harmless only as a key that is defined (`{ caller: 1 }`, `class K { binding() {} }`): in any position that reads one, the file reflects
ONLY_AS_A_DEFINED_KEY = {"mainModule", "getBuiltinModule", "binding", "dlopen", "caller", "callee", "Reflect", "Proxy", "prepareStackTrace", "getThis", "getFunction", "__proto__", "getPrototypeOf"}
DEFINED = "a key that is defined here"
# the node kinds that are no token of a name: the text of a comment, a regex, JSX text; the pieces of a string (judged as the string), a label, a type, a keyword value
NOT_TOKENS = set(NOT_A_NAME) - {"property_identifier"} | {"string_fragment", "escape_sequence", "html_comment", "comment", "regex_pattern", "regex_flags", "jsx_text", "hash_bang_line"}
STRINGS = ("string", "template_string")

_ESCAPE = re.compile(r"\\(?:u\{([0-9a-fA-F]+)\}|u([0-9a-fA-F]{4})|x([0-9a-fA-F]{2})|([0-7]{1,3})|(\r\n|[\s\S]))")
_SIMPLE = dict(zip("ntbfvr", "\n\t\b\f\v\r"))


def decode(raw: str) -> str:
    """JavaScript escapes decoded, written apart from the reader's decoder (a regex with one group per kind of escape)."""

    def one(m):
        braced, short, hexa, octal, other = m.groups()
        if braced or short:
            return chr(int(braced or short, 16))
        if hexa:
            return chr(int(hexa, 16))
        if octal:
            return chr(int(octal, 8))
        return "" if other in ("\n", "\r\n", "\r") else _SIMPLE.get(other, other)

    return _ESCAPE.sub(one, raw)


def spelled(node) -> str | None:
    """The name a token spells, or None when the node is no token of one."""
    if node.type in STRINGS:
        if any(c.type == "template_substitution" for c in node.children):
            return None
        return decode("".join(c.text.decode("utf-8", "replace") for c in node.children if c.type in ("string_fragment", "escape_sequence")))
    if node.child_count or not node.is_named or node.type in NOT_TOKENS or (node.parent is not None and node.parent.type in STRINGS):
        return None
    return decode(node.text.decode("utf-8", "replace"))


def problems(source: bytes, label: str) -> tuple[list[str], dict]:
    """(what is wrong with the outcomes the reader has for the references in this source, how many references of each family and outcome)."""
    root = TSX.parse(source).root_node
    reader = CommonJsReader(root)
    names = {name.encode() for name in WATCHED | reader.tracked}
    wrong: list[str] = []
    seen = {"tracked": 0, "wrapper": 0, "reflective": 0, OK: 0, OPEN: 0, REFLECT: 0}
    for node in walk(root):
        if node.child_count or not node.is_named or node.type in NOT_A_NAME or node.text not in names:
            continue
        name = node.text.decode()
        where = f"{label}:{node.start_point[0] + 1} `{name}` ({node.type})"
        if node.type not in ts_indexer.REFERENCE_TYPES:
            wrong.append(f"{where}: a kind of node that is in no list (add it to REFERENCE_TYPES or NOT_A_NAME, and handle it)")
        outcome = reader.refs.get(node.id)
        if outcome is None:
            wrong.append(f"{where}: no outcome (looked at by nothing)")
            continue
        kind, form = outcome
        if kind not in (OK, OPEN, REFLECT) or not form:
            wrong.append(f"{where}: outcome {outcome!r} is neither a harmless form nor an opener")
        if "unlisted" in form:
            wrong.append(f"{where}: an unlisted kind of node came to an opener only because it was not known: {form}")
        seen[kind] = seen.get(kind, 0) + 1
        family = "tracked" if name in reader.tracked else "wrapper" if name in ("require", "module", "exports", "arguments") else "reflective"
        seen[family] += 1
    for call_id in reader.sites:
        if call_id not in reader.refs:
            wrong.append(f"{label}: a require call at node {call_id} has no outcome")
    bound = {site.name for site in reader.sites.values() if site.form == "decl"}
    if not bound <= reader.tracked:
        wrong.append(f"{label}: names bound by a require that are not tracked: {sorted(bound - reader.tracked)}")
    return wrong, seen


def net_problems(source: bytes, label: str) -> tuple[list[str], dict]:
    """(what is wrong with the lexical net's outcomes for this source, how many tokens of each kind it classified)."""
    root = TSX.parse(source).root_node
    reader = CommonJsReader(root)
    wrong: list[str] = []
    seen: dict = {"tokens": 0, "strings": 0, "templates": 0, "escaped": 0, "keys": 0, "patterns": 0, "properties": 0, OK: 0, OPEN: 0, REFLECT: 0}
    for node in walk(root):
        name = spelled(node)
        if name is None or name not in (NET_NAMES | (BUILTIN_STRINGS if node.type in STRINGS else set())):
            continue
        where = f"{label}:{node.start_point[0] + 1} `{name}` ({node.type})"
        outcome = reader.net.get(node.id)
        if outcome is None:
            wrong.append(f"{where}: the net never looked at this token (a name spelled this way, in a kind of node the net does not read)")
            continue
        kind, form = outcome
        if kind not in (OK, OPEN, REFLECT) or not form:  # a wrapper name's identifier is judged by its own handler, which may open this file's exports
            wrong.append(f"{where}: outcome {outcome!r} is neither a harmless position nor an opener")
        if "unlisted" in form:
            wrong.append(f"{where}: a position no list names came to a reflect only because it was not known: {form}")
        if kind == OK and name in ONLY_AS_A_DEFINED_KEY and not form.startswith(DEFINED) and node.id not in reader.refs:
            wrong.append(f"{where}: `{name}` is classified harmless as {form!r}, and may be harmless only as a key that is defined")
        seen["tokens"] += 1
        seen[kind] += 1
        seen["strings"] += node.type == "string"
        seen["templates"] += node.type == "template_string"
        seen["escaped"] += b"\\" in node.text
        parent = node.parent.type if node.parent is not None else ""
        seen["keys"] += parent in ("pair", "pair_pattern", "method_definition", "public_field_definition")
        seen["patterns"] += parent in ("pair_pattern", "object_assignment_pattern") or node.type == "shorthand_property_identifier_pattern"
        seen["properties"] += node.type == "property_identifier"
    return wrong, seen


def test_every_reference_in_every_probe_is_classified():
    wrong, total = [], {"tracked": 0, "wrapper": 0, "reflective": 0, OK: 0, OPEN: 0, REFLECT: 0}
    for path in FILES:
        found, seen = problems(path.read_bytes(), str(path.relative_to(PROBES)))
        wrong += found
        for key, count in seen.items():
            total[key] += count
    assert not wrong, f"{len(wrong)} references with no classification\n" + "\n".join(wrong[:40])
    # not vacuous: the probes hold references of every family and every outcome
    assert total["tracked"] >= 150 and total["wrapper"] >= 150 and total["reflective"] >= 30, total
    assert total[OK] >= 150 and total[OPEN] >= 50 and total[REFLECT] >= 15, total


def test_every_watched_name_is_referenced_by_some_probe():
    seen: set[str] = set()
    for path in FILES:
        root = TSX.parse(path.read_bytes()).root_node
        seen |= {n.text.decode() for n in walk(root) if not n.child_count and n.is_named and n.type == "identifier" and n.text.decode() in WATCHED}
    assert seen == WATCHED, sorted(WATCHED - seen)


def test_the_reader_watches_every_name_the_net_is_told_to():
    assert NET_NAMES <= set(ts_indexer.NET), sorted(NET_NAMES - set(ts_indexer.NET))
    assert BUILTIN_STRINGS <= set(ts_indexer.NET_BUILTIN_STRINGS), sorted(BUILTIN_STRINGS - set(ts_indexer.NET_BUILTIN_STRINGS))


def test_every_token_that_spells_a_watched_name_in_every_probe_is_classified():
    wrong, total = [], {"tokens": 0, "strings": 0, "templates": 0, "escaped": 0, "keys": 0, "patterns": 0, "properties": 0, OK: 0, OPEN: 0, REFLECT: 0}
    for path in FILES:
        found, seen = net_problems(path.read_bytes(), str(path.relative_to(PROBES)))
        wrong += found
        for key, count in seen.items():
            total[key] += count
    assert not wrong, f"{len(wrong)} tokens with no classification\n" + "\n".join(wrong[:40])
    # not vacuous: the probes spell the names every way the net reads, and the net comes to both outcomes
    assert total["tokens"] >= 500 and total[OK] >= 150 and total[REFLECT] >= 150, total
    assert total["strings"] >= 20 and total["templates"] >= 3 and total["escaped"] >= 8 and total["keys"] >= 8 and total["patterns"] >= 8 and total["properties"] >= 150, total


def test_every_net_name_is_spelled_by_some_probe():
    seen: set[str] = set()
    for path in FILES:
        root = TSX.parse(path.read_bytes()).root_node
        seen |= {n for n in (spelled(node) for node in walk(root)) if n}
    assert NET_NAMES <= seen, sorted(NET_NAMES - seen)


def test_every_node_kind_the_net_reads_is_one_a_probe_spells():
    """The kinds of node the net reads each have a probe: an identifier, a property name, a shorthand and a pattern shorthand, a string, a template."""
    kinds: set[str] = set()
    for path in FILES:
        root = TSX.parse(path.read_bytes()).root_node
        kinds |= {node.type for node in walk(root) if spelled(node) in NET_NAMES}
    assert {"identifier", "property_identifier", "shorthand_property_identifier", "shorthand_property_identifier_pattern", "string", "template_string"} <= kinds, sorted(kinds)


SPELLINGS = {
    "a template": b"const F = (function () {})[`constructor`];\n",
    "a string": b"const F = (function () {})['constructor'];\n",
    "an escape": b"const F = (function () {})['\\x63onstructor'];\n",
    "a shorthand pattern": b"const { constructor } = function () {};\n",
    "a pair pattern key": b"const { constructor: F } = function () {};\n",
    "an escaped identifier": b"const F = (function () {}).c\\u006fnstructor;\n",
    "a string argument": b"const F = Reflect.get(function () {}, 'constructor');\n",
}


@pytest.mark.parametrize("spelling", sorted(SPELLINGS))
def test_the_net_reflects_every_spelling_and_says_so(spelling):
    source = SPELLINGS[spelling]
    clean, _ = net_problems(source, "x.js")
    assert clean == []
    reader = CommonJsReader(TSX.parse(source).root_node)
    assert reader.reflective, "a watched name in no position the net lists must reflect"


@pytest.mark.parametrize("hidden", ["template_string", "string", "shorthand_property_identifier_pattern", "pair_pattern", "escape"])
def test_a_net_that_does_not_see_a_spelling_fails_the_walk(monkeypatch, hidden):
    """Make the reader blind to one kind of token, as a reader that forgot it would be: the walk, which finds tokens itself, must say so."""
    source = {
        "template_string": SPELLINGS["a template"],
        "string": SPELLINGS["a string"],
        "shorthand_property_identifier_pattern": SPELLINGS["a shorthand pattern"],
        "pair_pattern": SPELLINGS["a pair pattern key"],
        "escape": SPELLINGS["an escape"],
    }[hidden]
    real = ts_indexer.decoded

    def blind(node):
        if hidden in ("template_string", "string", "shorthand_property_identifier_pattern") and node.type == hidden:
            return None
        if hidden == "pair_pattern" and node.parent is not None and node.parent.type == "pair_pattern":
            return None
        if hidden == "escape" and node.type == "string" and b"\\" in node.text:
            return None
        return real(node)

    assert net_problems(source, "x.js")[0] == []
    monkeypatch.setattr(ts_indexer, "decoded", blind)
    broken, _ = net_problems(source, "x.js")
    assert any("never looked at" in message for message in broken), broken


def test_a_kind_of_node_nobody_listed_fails_here_and_opens_in_the_reader(monkeypatch):
    """Drop a node kind from the list the reader knows: its references must open, and this test's own check must say so."""
    source = b"const a = require('./a');\nconst o = { a };\no.a.helper = function () {};\n"
    clean, _ = problems(source, "x.js")
    assert clean == []
    monkeypatch.setattr(ts_indexer, "REFERENCE_TYPES", tuple(t for t in ts_indexer.REFERENCE_TYPES if t != "shorthand_property_identifier"))
    broken, _ = problems(source, "x.js")
    assert any("shorthand_property_identifier" in message and "in no list" in message for message in broken), broken
    reader = CommonJsReader(TSX.parse(source).root_node)
    assert any(not site.safe for site in reader.sites.values()), "an unlisted kind of node must open the exports, not pass"


def test_a_name_that_is_harmless_only_as_a_key_fails_when_a_position_lets_it_through(monkeypatch):
    """A reader that let `.getBuiltinModule` pass as a property read would be wrong: the walk says which token it passed."""
    source = b"const x = {}.getBuiltinModule;\n"
    assert net_problems(source, "x.js")[0] == []
    real = ts_indexer.CommonJsReader._net_position

    def lenient(self, node, name):
        return (OK, "a property read of any object") if name == "getBuiltinModule" else real(self, node, name)

    monkeypatch.setattr(ts_indexer.CommonJsReader, "_net_position", lenient)
    broken, _ = net_problems(source, "x.js")
    assert any("may be harmless only as a key that is defined" in message for message in broken), broken
