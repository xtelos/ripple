"""A JavaScript instance method that the class's own body replaces or hides is no edge.

`new C().m()` draws an edge to `C.m` only when the source says that is what runs. In these classes the body says it is not: the
constructor (or a method) gives the instance its own `m`, a field named `m` is spelled as a string or a literal computed key, a base
class gives every instance its own `m`, a constructor returns another object, a later member of the class is named `m` in another
spelling. Every case below was run in Node 24 and the replacement ran (or, in the controls, the original ran).

The answer for each is `unresolved`, never an edge to `C.m`. What it does not do is reason about code outside the class body: a write
to `C.prototype.m` in another file, or `obj.m = f` on an instance that came from anywhere, keeps its edge. Those pins are marked
ACCEPTED-LIMIT and are not claims that the edge is right (Node runs the replacement in each).

These need tree-sitter (the `typescript` extra) and are skipped, with the reason, without it.
"""

import pytest

pytest.importorskip("tree_sitter", reason="TypeScript support is not installed (pip install 'ripple-code[typescript]')")
pytest.importorskip("tree_sitter_typescript", reason="TypeScript support is not installed (pip install 'ripple-code[typescript]')")

from ripple.indexer import build_graph  # noqa: E402
from ripple.profile import JAVASCRIPT, TYPESCRIPT  # noqa: E402
from ripple.queries import callers  # noqa: E402

R = "function () { return 'REPLACED'; }"
M = "m() { return 'ORIGINAL'; }"
MAIN = "import { C } from './c.js';\nconsole.log(new C().m()); // CALL\n"
MAIN_GO = "import { C } from './c.js';\nconst c = new C();\nc.go();\nconsole.log(c.m()); // CALL\n"
MAIN_INIT = "import { C } from './c.js';\nconst c = new C();\nc.init();\nconsole.log(c.m()); // CALL\n"
MAIN_ARG = "import { C } from './c.js';\nconsole.log(new C({ m: " + R + " }).m()); // CALL\n"
MAIN_TS = "import { C } from './c.ts';\nconsole.log(new C().m()); // CALL\n"


def cls(body, prefix=""):
    return prefix + "export class C { " + body + " }\n"


def answer(make_repo, files, main="main.js", profile=JAVASCRIPT):
    """What ripple says about the call on the line marked CALL in `main`: ("edge", sorted callees) or ("unresolved", reason)."""
    graph = build_graph(make_repo(files), profile)
    line = next(i for i, text in enumerate(files[main].splitlines(), 1) if "// CALL" in text)
    edges = sorted(e.callee for e in graph.edges if e.file == main and e.line == line and e.kind != "constructor")
    if edges:
        return "edge", edges
    reasons = [u.reason for u in graph.unresolved if u.file == main and u.line == line]
    assert reasons, f"nothing at {main}:{line}: {[(e.file, e.line, e.callee) for e in graph.edges]}"
    return "unresolved", " | ".join(reasons)


def class_answer(make_repo, body, main=MAIN, prefix=""):
    return answer(make_repo, {"c.js": cls(body, prefix), "main.js": main})


def assert_edge(result, callee="c:C.m"):
    assert result == ("edge", [callee]), result


def assert_unresolved(result, *words):
    assert result[0] == "unresolved", result
    for word in words:
        assert word in result[1], result


# ---- the constructor gives the instance its own `m` (report rows I12 to I16) ------------------------------------------------------

OWN_BY_THE_CONSTRUCTOR = {
    "I11 this.m =": f"constructor() {{ this.m = {R}; }} {M}",  # already unresolved before this change; held here as the reference shape
    "I12 this['m'] =": f"constructor() {{ this['m'] = {R}; }} {M}",
    "I12 this[\"m\"] =": f"constructor() {{ this[\"m\"] = {R}; }} {M}",
    "I12 this[`m`] =": f"constructor() {{ this[`m`] = {R}; }} {M}",
    "I12 this['\\x6d'] = (an escape)": f"constructor() {{ this['\\x6d'] = {R}; }} {M}",
    "I13 Object.assign(this, {m})": f"constructor() {{ Object.assign(this, {{ m: {R} }}); }} {M}",
    "I13 Object.assign(this, {'m'})": f"constructor() {{ Object.assign(this, {{ 'm': {R} }}); }} {M}",
    "I13 Object.assign(this, {['m']})": f"constructor() {{ Object.assign(this, {{ ['m']: {R} }}); }} {M}",
    "I13 Object.assign(this, {m() {}})": f"constructor() {{ Object.assign(this, {{ m() {{ return 'REPLACED'; }} }}); }} {M}",
    "I13 Object.assign(this, {m}) shorthand": f"constructor() {{ const m = {R}; Object.assign(this, {{ m }}); }} {M}",
    "I13 Object.assign(this, a, {m})": f"constructor(a) {{ Object.assign(this, a, {{ m: {R} }}); }} {M}",
    "I14 Object.defineProperty(this, 'm')": f"constructor() {{ Object.defineProperty(this, 'm', {{ value: {R} }}); }} {M}",
    "I14 Object.defineProperties(this, {m})": f"constructor() {{ Object.defineProperties(this, {{ m: {{ value: {R} }} }}); }} {M}",
    "I14 Reflect.defineProperty(this, 'm')": f"constructor() {{ Reflect.defineProperty(this, 'm', {{ value: {R} }}); }} {M}",
    "I14 Reflect.set(this, 'm')": f"constructor() {{ Reflect.set(this, 'm', {R}); }} {M}",
    "I15 this.m = in an arrow": f"constructor() {{ const f = () => {{ this.m = {R}; }}; f(); }} {M}",
    "I15 an arrow in an arrow": f"constructor() {{ const f = () => () => {{ this.m = {R}; }}; f()(); }} {M}",
    "I16 const self = this": f"constructor() {{ const self = this; self.m = {R}; }} {M}",
    "I16 let self = this": f"constructor() {{ let self = this; self.m = {R}; }} {M}",
    "I16 an alias assigned later": f"constructor() {{ let that; that = this; that.m = {R}; }} {M}",
    "I16 an alias used in a nested function": f"constructor() {{ const self = this; function f() {{ self.m = {R}; }} f(); }} {M}",
    "I16 an alias written self['m'] =": f"constructor() {{ const self = this; self['m'] = {R}; }} {M}",
    "I16 an alias through Object.assign": f"constructor() {{ const self = this; Object.assign(self, {{ m: {R} }}); }} {M}",
}


@pytest.mark.parametrize("body", list(OWN_BY_THE_CONSTRUCTOR.values()), ids=list(OWN_BY_THE_CONSTRUCTOR))
def test_the_instance_gets_its_own_m_in_the_constructor_so_the_method_is_not_an_edge(make_repo, body):
    assert_unresolved(class_answer(make_repo, body))


def test_a_derived_class_that_writes_this_m_after_super_hides_its_own_method(make_repo):
    files = {"c.js": f"class B {{}}\nexport class C extends B {{ constructor() {{ super(); this['m'] = {R}; }} {M} }}\n", "main.js": MAIN}
    assert_unresolved(answer(make_repo, files))


def test_a_write_in_a_method_other_than_the_constructor_hides_the_method_too(make_repo):
    # init() is where the instance gets its own `m` (Node: REPLACED once init() has run)
    assert_unresolved(class_answer(make_repo, f"init() {{ [1].forEach(() => {{ this.m = {R}; }}); }} {M}", MAIN_INIT))
    assert_unresolved(class_answer(make_repo, f"init() {{ Object.assign(this, {{ m: {R} }}); }} {M}", MAIN_INIT))


def test_an_arrow_function_field_that_writes_this_m_hides_the_method(make_repo):
    # `go = () => { this.m = f }` is a field that holds a function: its body was never read. Node: REPLACED after c.go()
    assert_unresolved(class_answer(make_repo, f"go = () => {{ this.m = {R}; }}; {M}", MAIN_GO))


def test_a_constructor_that_gives_the_instance_a_new_prototype_hides_every_method(make_repo):
    # I23, and its spellings. `this` has another prototype afterwards, so no method of the class is what runs.
    for body, main in (
        (f"constructor() {{ Object.setPrototypeOf(this, {{ m: {R} }}); }} {M}", MAIN),
        (f"constructor() {{ Reflect.setPrototypeOf(this, {{ m: {R} }}); }} {M}", MAIN),
        (f"constructor() {{ this.__proto__ = {{ m: {R} }}; }} {M}", MAIN),
        (f"constructor() {{ const self = this; self['__proto__'] = {{ m: {R} }}; }} {M}", MAIN),
        (f"init() {{ Object.setPrototypeOf(this, {{ m: {R} }}); }} {M}", MAIN_INIT),
    ):
        assert_unresolved(class_answer(make_repo, body, main), "prototype")


# ---- what the constructor writes that is not `m` keeps the edge ------------------------------------------------------------------

KEEPS_THE_EDGE = {
    "other names through every spelling": (
        "constructor() { this.x = 1; this['y'] = 2; Object.assign(this, { z: 3 }); Object.defineProperty(this, 'w', { value: 4 }); "
        "const f = () => { this.v = 5; }; f(); const self = this; self.u = 6; Reflect.set(this, 't', 7); } " + M
    ),
    "names that are not literals": "constructor(o) { Object.assign(this, o); for (const k in o) this[k] = o[k]; } " + M,  # ACCEPTED-LIMIT: run-time keys
    "a function that is not an arrow has its own this": f"constructor() {{ function f() {{ this.m = {R}; }} }} {M}",
    "a nested class has its own this": f"constructor() {{ class Inner {{ constructor() {{ this.m = {R}; }} }} new Inner(); }} {M}",
    "a static method writes the class": f"static make() {{ this.m = {R}; }} {M}",
    "delete this.m only uncovers the prototype": "constructor() { delete this.m; delete this['m']; } " + M,
    "this.m = this.m.bind(this) is the method itself": "constructor() { this.m = this.m.bind(this); } " + M,
    "an accessor of that name takes the write": "constructor() { this.val = 1; } get val() { return 1; } set val(v) {} " + M,
    "return with no value, this, or a primitive": "constructor() { if (0) return; if (0) return this; if (0) return 1; return 'x'; } " + M,
    "a return inside a nested function is not the constructor's": f"constructor() {{ function g() {{ return {{ m: {R} }}; }} g(); const h = () => {{ return {{ m: 1 }}; }}; h(); }} {M}",
    "a computed key that is not a literal names no member": "[Symbol.iterator]() { return [][Symbol.iterator](); } " + M,
    "unrelated fields in every spelling": "'x' = 1; ['y'] = 2; 3 = 4; 'a-b' = 5; " + M,
    "a string-named constructor is the constructor": "'constructor'() { this.x = 1; } " + M,
}


@pytest.mark.parametrize("body", list(KEEPS_THE_EDGE.values()), ids=list(KEEPS_THE_EDGE))
def test_a_class_that_does_not_replace_m_keeps_its_edge(make_repo, body):
    assert_edge(class_answer(make_repo, body))


def test_a_static_and_an_instance_member_of_one_name_are_answered_alike_in_every_spelling(make_repo):
    # `static m() {}` beside `m() {}` is already unresolved (which one a call reaches is not read); `static 'm'() {}` is the same name.
    # That is a lost right edge (Node runs the instance m), not a correct answer: see the cost pinned further down.
    plain = class_answer(make_repo, "static m() { return 'STATIC'; } " + M)
    assert_unresolved(plain)
    assert class_answer(make_repo, "static 'm'() { return 'STATIC'; } " + M) == plain
    assert class_answer(make_repo, "static ['m']() { return 'STATIC'; } " + M) == plain


def test_a_write_in_the_methods_of_another_name_keeps_the_edge(make_repo):
    assert_edge(class_answer(make_repo, f"init() {{ [1].forEach(() => {{ this.q = 1; }}); }} {M}", MAIN_INIT))


# ---- a field named by a string or a literal computed key (I19, I20) --------------------------------------------------------------

def test_a_field_named_by_a_string_or_a_literal_computed_key_hides_the_method(make_repo):
    for field in (f"'m' = {R};", f"['m'] = {R};", f"[`m`] = {R};", f"\"m\" = {R};", f"'\\x6d' = {R};", f"['\\u006d'] = {R};"):
        assert_unresolved(class_answer(make_repo, f"{M} {field}"))
        assert_unresolved(class_answer(make_repo, f"{field} {M}"))


def test_an_escaped_field_name_is_the_name_it_spells(make_repo):
    assert_unresolved(class_answer(make_repo, f"{M} \\u006d = {R};"))


def test_a_field_with_no_initializer_named_by_a_string_hides_the_method_as_a_plain_one_does(make_repo):
    # `m;` and `'m';` define an own property m = undefined on every instance
    assert_unresolved(class_answer(make_repo, f"{M} m;"))
    assert_unresolved(class_answer(make_repo, f"{M} 'm';"))


# ---- a later member spelled another way replaces the earlier `m` (M02, M03, M05) -------------------------------------------------

def members_source(*members):
    """A class C whose members each span three lines and call one function (a, b, c3, in order), so which body a symbol is shows."""
    lines = ["export function a() {}", "export function b() {}", "export function c3() {}", "export class C {"]
    for n, header in enumerate(members):
        lines += [f"  {header} {{", f"    return {'abc'[n] if n < 2 else 'c3'}();", "  }"]
    return "\n".join([*lines, "}"]) + "\n"


def member_lines(n):
    """The first and last line of the n-th member of members_source."""
    return 5 + 3 * n, 7 + 3 * n


def dup_graph(make_repo, *members):
    return build_graph(make_repo({"c.js": members_source(*members), "main.js": MAIN}), JAVASCRIPT)


def calls_from(graph, caller):
    return sorted(e.callee for e in graph.edges if e.caller == caller and e.kind == "call")


LATER_SPELLINGS = ["m()", "'m'()", "['m']()", "[`m`]()", "\\u006d()", "\\u{6d}()", "'\\x6d'()", "async m()", "*m()"]


@pytest.mark.parametrize("later", LATER_SPELLINGS)
def test_the_last_member_of_a_name_is_the_symbol_and_the_earlier_one_is_a_symbol_nothing_reaches(make_repo, later):
    """Node puts the LAST `m` on the prototype, so the first body never runs. `C.m` is that last member: its range is its lines and
    its calls are the ones its body makes. The first is kept as a symbol of its own, under an id no name finds, with no calls and no
    callers, so a change to it selects no test and a change to the last one reaches the callers of `C.m`. (M01, M02, M03, M05)"""
    graph = dup_graph(make_repo, "m()", later)
    live, dead = graph.symbols["c:C.m"], graph.symbols["c:C.m#replaced1"]
    assert (live.line, live.end_line) == member_lines(1)
    assert (dead.line, dead.end_line) == member_lines(0)
    assert calls_from(graph, "c:C.m") == ["c:b"]  # the first body calls a(), and never runs
    assert calls_from(graph, "c:C.m#replaced1") == []
    assert [e for e in graph.edges if e.callee == "c:C.m#replaced1"] == []
    assert sorted(s for s in graph.symbols if s.startswith("c:C")) == ["c:C", "c:C.m", "c:C.m#replaced1"]
    assert_edge(answer(make_repo, {"c.js": members_source("m()", later), "main.js": MAIN}))
    assert callers(graph, "c:a")["count"] == 0
    assert callers(graph, "c:b")["count"] == 1


def test_three_definitions_leave_the_last_one_and_two_that_nothing_reaches(make_repo):
    graph = dup_graph(make_repo, "m()", "'m'()", "['m']()")
    assert (graph.symbols["c:C.m"].line, graph.symbols["c:C.m"].end_line) == member_lines(2)
    assert {(s.line, s.end_line) for k, s in graph.symbols.items() if "#replaced" in k} == {member_lines(0), member_lines(1)}
    assert sorted(k for k in graph.symbols if "#replaced" in k) == ["c:C.m#replaced1", "c:C.m#replaced2"]
    assert calls_from(graph, "c:C.m") == ["c:c3"]


def test_a_getter_after_a_method_leaves_no_method_and_a_method_after_a_getter_is_the_method(make_repo):
    # Node: `m(){} get m(){}` has no method m (`c.m()` calls what the getter returns); `get m(){} m(){}` is the method.
    last_getter = dup_graph(make_repo, "m()", "get m()")
    assert (last_getter.symbols["c:C.m"].line, last_getter.symbols["c:C.m"].end_line) == member_lines(1)
    assert "c:C.m#replaced1" in last_getter.symbols
    assert_unresolved(answer(make_repo, {"c.js": members_source("m()", "get m()"), "main.js": MAIN}))
    last_method = dup_graph(make_repo, "get m()", "set m(v)", "m()")
    assert sorted(k for k in last_method.symbols if "#replaced" in k) == ["c:C.m#replaced1", "c:C.m#replaced2"]
    assert (last_method.symbols["c:C.m"].line, last_method.symbols["c:C.m"].end_line) == member_lines(2)
    assert_edge(answer(make_repo, {"c.js": members_source("get m()", "set m(v)", "m()"), "main.js": MAIN}))


def test_a_getter_and_a_setter_of_one_name_are_one_accessor_not_a_replacement(make_repo):
    # Node: `get m(){} set m(v){}` is one property with both. Both bodies belong to the one symbol, as they did.
    graph = dup_graph(make_repo, "get m()", "set m(v)")
    assert not [k for k in graph.symbols if "#replaced" in k]
    assert calls_from(graph, "c:C.m") == ["c:a", "c:b"]
    assert_unresolved(answer(make_repo, {"c.js": members_source("get m()", "set m(v)"), "main.js": MAIN}))
    # a second getter replaces the first and keeps the setter
    graph = dup_graph(make_repo, "get m()", "set m(v)", "get m()")
    assert sorted(k for k in graph.symbols if "#replaced" in k) == ["c:C.m#replaced1"]
    assert calls_from(graph, "c:C.m") == ["c:b", "c:c3"]


def test_members_that_are_not_duplicates_make_no_replaced_symbol(make_repo):
    for body in (
        "static m() { return 'S'; } m() { return 'I'; }",  # a static and an instance member are two members
        "#m() { return 'P'; } m() { return 'M'; }",  # a private name is another name
        "m() { return 'M'; } m = () => 'F';",  # a field is not a method: see the own-property rule
        "m = () => 'F'; m() { return 'M'; }",
    ):
        graph = build_graph(make_repo({"c.js": cls(body), "main.js": MAIN}), JAVASCRIPT)
        assert not [k for k in graph.symbols if "#replaced" in k], body
    assert_edge(class_answer(make_repo, "#m() { return 'P'; } m() { return 'M'; }"))  # Node: M
    # Node runs the field in both orders: it is an own property, which comes before the prototype's method
    assert_unresolved(class_answer(make_repo, "m() { return 'M'; } m = () => 'F';"))
    assert_unresolved(class_answer(make_repo, "m = () => 'F'; m() { return 'M'; }"))


def test_a_static_member_beside_an_instance_member_of_one_name_is_unresolved_a_lost_right_edge(make_repo):
    # COST, not a correct answer: Node runs the instance `m` (`new C().m()` is 'INSTANCE'), and ripple answers unresolved because a
    # static and an instance member share one binding. `static m` beside `m` always did; the string spelling now does too.
    assert_unresolved(class_answer(make_repo, "static m() { return 'STATIC'; } m() { return 'INSTANCE'; }"))
    assert_unresolved(class_answer(make_repo, "static 'm'() { return 'STATIC'; } m() { return 'INSTANCE'; }"))


def test_duplicate_statics_are_one_static_too(make_repo):
    source = "export function a() {}\nexport function b() {}\nexport class C {\n  static m() {\n    return a();\n  }\n  static 'm'() {\n    return b();\n  }\n}\n"
    graph = build_graph(make_repo({"c.js": source, "main.js": "import { C } from './c.js';\nC.m();\n"}), JAVASCRIPT)
    assert calls_from(graph, "c:C.m") == ["c:b"]
    assert [(s.line, s.end_line) for k, s in graph.symbols.items() if "#replaced" in k] == [(4, 6)]


def test_a_decorated_duplicate_stays_unresolved(make_repo):
    source = "function dec(t: any, k: string, d: any) {}\nexport class C {\n  @dec m() { return 1; }\n  m() { return 2; }\n}\n"
    result = answer(make_repo, {"c.ts": source, "main.ts": MAIN_TS}, main="main.ts", profile=TYPESCRIPT)
    assert_unresolved(result)


def test_an_escaped_property_at_a_call_site_is_the_name_it_spells(make_repo):
    # c.<escape for m>() runs C.m (Node). Read as the raw text it matched nothing, and with a base class from a package that was
    # answered "a method inherited from outside the repo", a wrong claim.
    base = "import { Base } from 'pkg';\nexport class C extends Base { m() { return 1; } }\n"
    for call in ("c.\\u006d(); // CALL", "c.\\u{6d}(); // CALL", "(c).\\u006d(); // CALL"):
        files = {"c.js": base, "main.js": f"import {{ C }} from './c.js';\nconst c = new C();\n{call}\n"}
        assert_edge(answer(make_repo, files))
        graph = build_graph(make_repo(files), JAVASCRIPT)
        plain = build_graph(make_repo({"c.js": base, "main.js": "import { C } from './c.js';\nconst c = new C();\nc.m(); // CALL\n"}), JAVASCRIPT)
        assert graph.external_reasons == plain.external_reasons
    through_a_call = {"c.js": base, "main.js": "import { C } from './c.js';\nnew C().\\u006d(); // CALL\n"}
    assert_edge(answer(make_repo, through_a_call))


def test_a_replaced_getter_does_not_make_an_accessor_of_that_name(make_repo):
    # `get m(){} m(){}` leaves the method, so `this.m = f` in the constructor is an own property (Node: REPLACED), not a setter call
    body = f"get m() {{ return 1; }} constructor() {{ this.m = {R}; }} m() {{ return 'ORIGINAL'; }}"
    assert_unresolved(class_answer(make_repo, body))


def test_a_method_written_as_a_string_alone_is_the_method_the_call_runs(make_repo):
    # Node runs it: the method is `m` however it is spelled. It used to be keyed with its quotes, so `.m()` found nothing.
    for spelling in ("'m'() { return 'ORIGINAL'; }", "['m']() { return 'ORIGINAL'; }", "[`m`]() { return 'ORIGINAL'; }", "\\u006d() { return 'ORIGINAL'; }"):
        assert_edge(class_answer(make_repo, spelling))


def test_a_getter_written_as_a_string_is_still_an_accessor_of_that_name(make_repo):
    # `get 'm'() {}` is the accessor m: a call `new C().m()` is not a call of the method, so the answer stays unresolved
    result = class_answer(make_repo, f"get 'm'() {{ return {R}; }}")
    assert_unresolved(result)


def test_a_symbol_or_other_computed_key_is_not_read_as_a_name(make_repo):
    # `[k]() {}` may be any name, but ripple does not guess it: the method of the plain name keeps its edge (see KEPT-WIDE)
    assert_edge(class_answer(make_repo, f"[kMethod]() {{ return 1; }} {M}", prefix="const kMethod = Symbol('m');\n"))


# ---- a base class gives every instance its own `m` (I24, I25) ------------------------------------------------------------------

def test_a_base_class_constructor_that_writes_this_m_hides_the_derived_method(make_repo):
    files = {"c.js": f"class B {{ constructor() {{ this.m = {R}; }} }}\nexport class C extends B {{ {M} }}\n", "main.js": MAIN}
    assert_unresolved(answer(make_repo, files), "base class c:B")


def test_a_base_class_field_hides_the_derived_method(make_repo):
    files = {"c.js": f"class B {{ m = {R}; }}\nexport class C extends B {{ {M} }}\n", "main.js": MAIN}
    assert_unresolved(answer(make_repo, files), "base class c:B")


def test_a_base_in_another_file_and_a_base_two_levels_up_hide_it_too(make_repo):
    files = {"b.js": f"export class B {{ m = {R}; }}\n", "c.js": f"import {{ B }} from './b.js';\nexport class C extends B {{ {M} }}\n", "main.js": MAIN}
    assert_unresolved(answer(make_repo, files), "base class b:B")
    files = {"c.js": f"class A {{ this_ = 1; constructor() {{ this['m'] = {R}; }} }}\nclass B extends A {{}}\nexport class C extends B {{ {M} }}\n", "main.js": MAIN}
    assert_unresolved(answer(make_repo, files), "base class c:A")


def test_a_base_whose_arrow_in_the_constructor_writes_this_m_hides_it(make_repo):
    files = {"c.js": f"class B {{ constructor() {{ (() => {{ this.m = {R}; }})(); }} }}\nexport class C extends B {{ {M} }}\n", "main.js": MAIN}
    assert_unresolved(answer(make_repo, files), "base class c:B")


def test_a_base_with_other_own_properties_or_a_static_of_that_name_keeps_the_edge(make_repo):
    assert_edge(answer(make_repo, {"c.js": f"class B {{ q = 1; constructor() {{ this.r = 2; }} }}\nexport class C extends B {{ {M} }}\n", "main.js": MAIN}))
    # `static m` is a property of B itself, never of an instance
    assert_edge(answer(make_repo, {"c.js": f"class B {{ static m = 5; }}\nexport class C extends B {{ {M} }}\n", "main.js": MAIN}))


def test_a_method_written_in_the_derived_class_is_not_hidden_by_a_base_method(make_repo):
    # a base method of the same name is overridden: the edge is to the derived one, as before
    files = {"c.js": f"class B {{ {M} }}\nexport class C extends B {{ {M} }}\n", "main.js": MAIN}
    assert_edge(answer(make_repo, files))


def test_the_override_of_a_class_whose_ancestor_has_an_own_m_is_not_an_edge(make_repo):
    files = {
        "c.js": f"export class A {{ {M} run() {{ return this.m(); }} }} // CALL\nclass B extends A {{ m = {R}; }}\nexport class D extends B {{ {M} }}\n",
        "main.js": "import { D } from './c.js';\nconsole.log(new D().run());\n",
    }
    graph = build_graph(make_repo(files), JAVASCRIPT)
    calls = sorted((e.callee, e.kind) for e in graph.edges if e.file == "c.js" and e.line == 1)
    assert ("c:A.m", "call") in calls  # an A instance runs A.m
    assert ("c:D.m", "override") not in calls  # a D instance has B's own m (Node: REPLACED), so D.m never runs


def test_an_override_whose_ancestors_have_other_own_properties_stays(make_repo):
    files = {
        "c.js": f"export class A {{ {M} run() {{ return this.m(); }} }} // CALL\nclass B extends A {{ q = 1; }}\nexport class D extends B {{ {M} }}\n",
        "main.js": "import { D } from './c.js';\nconsole.log(new D().run());\n",
    }
    graph = build_graph(make_repo(files), JAVASCRIPT)
    assert ("c:D.m", "override") in sorted((e.callee, e.kind) for e in graph.edges if e.file == "c.js" and e.line == 1)


ACCESSOR_BELOW = "export class C extends B { get value() { return 2; } set value(v) { helper(); } }\nfunction helper() {}\n"
ACCESSOR_USE = "import { C } from './c.js';\nconst c = new C();\nc.value = 2;\nconsole.log(c.value);\n"


@pytest.mark.parametrize(
    "base, runs_the_accessor",
    [
        ("class B { constructor() { this.value = 1; } }", True),  # an assignment calls the setter of the class below (Node: setter, getter)
        ("class B { constructor(v) { this.value = v; } }", True),
        ("class B { value = 1; }", False),  # a field defines the property over the accessor (Node: neither runs)
        ("class B { constructor() { Object.defineProperty(this, 'value', { value: 1, writable: true }); } }", False),
    ],
    ids=["this.value = v", "this.value = v from a parameter", "field", "defineProperty"],
)
def test_a_base_assignment_does_not_hide_an_accessor_below_it_but_a_field_does(make_repo, base, runs_the_accessor):
    graph = build_graph(make_repo({"c.js": base + "\n" + ACCESSOR_BELOW, "main.js": ACCESSOR_USE}), JAVASCRIPT)
    accessor_edges = [(e.line, e.callee) for e in graph.edges if e.file == "main.js" and e.callee == "c:C.value"]
    assert bool(accessor_edges) == runs_the_accessor, accessor_edges


# ---- a constructor that returns an object (I21, I22, I26) -----------------------------------------------------------------------

RETURNS_AN_OBJECT = {
    "I21 return {m}": f"constructor() {{ return {{ m: {R} }}; }} {M}",
    "I22 return new Proxy(this)": f"constructor() {{ return new Proxy(this, {{ get(t, k) {{ return k === 'm' ? {R} : t[k]; }} }}); }} {M}",
    "return a parameter": f"constructor(o) {{ return o; }} {M}",
    "return a conditional": f"constructor(o) {{ return o ? o : this; }} {M}",
    "return inside an if": f"constructor() {{ if (1) {{ return {{ m: {R} }}; }} }} {M}",
}


@pytest.mark.parametrize("body", list(RETURNS_AN_OBJECT.values()), ids=list(RETURNS_AN_OBJECT))
def test_a_constructor_that_may_return_another_object_makes_the_instance_unknown(make_repo, body):
    main = MAIN_ARG if "(o)" in body else MAIN
    assert_unresolved(class_answer(make_repo, body, main), "constructor")


def test_a_base_constructor_that_returns_an_object_makes_the_derived_instance_unknown(make_repo):
    files = {"c.js": f"class B {{ constructor() {{ return {{ m: {R} }}; }} }}\nexport class C extends B {{ {M} }}\n", "main.js": MAIN}
    assert_unresolved(answer(make_repo, files), "constructor of c:B")
    files = {"c.js": f"class B {{ {M} constructor() {{ return {{ m: {R} }}; }} }}\nexport class C extends B {{}}\n", "main.js": MAIN}
    assert_unresolved(answer(make_repo, files), "constructor of c:B")


def test_a_constructor_that_returns_nothing_provably_other_keeps_the_edge(make_repo):
    assert_edge(class_answer(make_repo, f"constructor() {{ if (0) return; if (0) return this; return 1; }} {M}"))


# ---- TypeScript -----------------------------------------------------------------------------------------------------------------

def ts(make_repo, body):
    return answer(make_repo, {"c.ts": cls(body), "main.ts": MAIN_TS}, main="main.ts", profile=TYPESCRIPT)


def test_typescript_parameter_properties_declare_fields_and_overloads_keep_the_edge(make_repo):
    assert_edge(ts(make_repo, f"constructor(public x: number, private readonly y = 2) {{}} {M}"))
    assert_edge(ts(make_repo, f"declare x: number; {M}"))
    assert_edge(ts(make_repo, "m(a: string): string;\nm(a: number): string;\nm(a: any): string { return 'ORIGINAL'; }"))


def test_typescript_type_assertions_around_this_do_not_hide_the_write(make_repo):
    assert_unresolved(ts(make_repo, f"constructor() {{ (this as any).m = {R}; }} {M}"))
    assert_unresolved(ts(make_repo, f"constructor() {{ const self = this as any; self.m = {R}; }} {M}"))
    assert_unresolved(ts(make_repo, f"constructor() {{ (<any>this).m = {R}; }} {M}"))
    assert_unresolved(ts(make_repo, f"constructor() {{ this!.m = {R}; }} {M}"))


def test_typescript_string_named_members_and_fields_are_read_like_javascript(make_repo):
    assert_unresolved(ts(make_repo, f"{M} 'm': Function = {R};"))
    assert_edge(ts(make_repo, "'m'(): string { return 'ORIGINAL'; }"))


def test_declare_and_abstract_fields_make_no_property_so_they_hide_nothing(make_repo):
    # tsc emits nothing for either (checked with transpileModule at ES2022 and ES2020): Node runs the method in all three
    assert_edge(ts(make_repo, f"declare m: Function; {M}"))
    for base in ("class B { declare m: Function; }", "abstract class B { abstract m: Function; }"):
        files = {"c.ts": f"{base}\nexport class C extends B {{ {M} }}\n", "main.ts": MAIN_TS}
        assert_edge(answer(make_repo, files, main="main.ts", profile=TYPESCRIPT))


def test_a_typescript_field_with_a_type_and_no_declare_hides_a_base_method(make_repo):
    # `m: Function = f` is a field like any other (tsc emits it as a property of the instance)
    files = {"c.ts": f"class B {{ m: Function = () => 'REPLACED'; }}\nexport class C extends B {{ {M} }}\n", "main.ts": MAIN_TS}
    assert_unresolved(answer(make_repo, files, main="main.ts", profile=TYPESCRIPT), "base class c:B")


def test_a_parameter_property_named_m_still_hides_a_base_method(make_repo):
    # constructor(public m: Function) is an own property of every instance
    files = {"c.ts": f"class B {{ constructor(public m: Function) {{}} }}\nexport class C extends B {{ {M} }}\n", "main.ts": MAIN_TS}
    assert_unresolved(answer(make_repo, files, main="main.ts", profile=TYPESCRIPT), "base class c:B")


# ---- ACCEPTED-LIMIT: replacement from outside the class body is not detected -----------------------------------------------------

def test_accepted_limit_a_prototype_patch_in_another_file_keeps_the_edge(make_repo):
    # ACCEPTED-LIMIT: Node prints REPLACED here (report case A01), and ripple still draws C.m. This is not the right answer; it is
    # the answer ripple gives for a write from outside the class, which it does not look for (see the README).
    files = {
        "c.js": cls(M),
        "p.js": f"import {{ C }} from './c.js';\nC.prototype.m = {R};\n",
        "main.js": "import { C } from './c.js';\nimport './p.js';\nconsole.log(new C().m()); // CALL\n",
    }
    assert_edge(answer(make_repo, files))


def test_accepted_limit_a_write_on_an_instance_outside_the_class_keeps_the_edge(make_repo):
    # ACCEPTED-LIMIT: Node prints REPLACED in both (report cases I09 and I01), and ripple draws C.m in both. Not the right answer.
    same_scope = "import { C } from './c.js';\nconst c = new C();\nc.m = " + R + ";\nconsole.log(c.m()); // CALL\n"
    assert_edge(answer(make_repo, {"c.js": cls(M), "main.js": same_scope}))
    given_to_a_function = {
        "c.js": cls(M),
        "p.js": f"export function patch(o) {{ o.m = {R}; }}\n",
        "main.js": "import { C } from './c.js';\nimport { patch } from './p.js';\nconst c = new C();\npatch(c);\nconsole.log(c.m()); // CALL\n",
    }
    assert_edge(answer(make_repo, given_to_a_function))


def test_accepted_limit_a_constructor_that_hands_this_to_a_helper_keeps_the_edge(make_repo):
    # ACCEPTED-LIMIT: Node prints REPLACED (report case I17): install(this) writes o.m. What a callee does with the object is not
    # read, so the edge stays. Not the right answer.
    helper = f"function install(o) {{ o.m = {R}; }}\n"
    assert_edge(answer(make_repo, {"c.js": cls(f"constructor() {{ install(this); }} {M}", helper), "main.js": MAIN}))


def test_accepted_limit_a_computed_key_in_the_class_body_keeps_the_edge(make_repo):
    # ACCEPTED-LIMIT: `this[k] = f` and `[k]() {}` may name m at run time (family 2 of the report). Not read; the edge stays.
    assert_edge(class_answer(make_repo, f"constructor(k) {{ this[k] = {R}; }} {M}", MAIN))
    assert_edge(class_answer(make_repo, f"constructor(o) {{ Object.assign(this, o); }} {M}", MAIN))
    assert_edge(class_answer(make_repo, f"[kName]() {{ return 1; }} {M}", prefix="const kName = 'm';\n"))


# ---- the class rules beside what main's module-name rules do (a merge of two branches) ----------------------------------------------
# Every case below was run in Node 24 (tsc transpileModule to CommonJS); the output is in the comment.

CALL_TS = "import { C } from './c';\nconsole.log(new C().m()); // CALL\n"


def ts_graph(make_repo, files):
    return build_graph(make_repo(files), TYPESCRIPT)


def to_class_m(graph):
    return [e for e in graph.edges if e.callee.endswith(".m") and e.kind != "constructor"]


@pytest.mark.parametrize(
    "source",
    [
        f"class C {{ constructor() {{ this['m'] = {R}; }} {M} }}\nclass C {{}}\nexport {{ C }};\n",  # Node: SyntaxError, C declared twice
        "class C { m() { return 'F'; } m() { return 'L'; } }\nlet C = 1;\nexport { C };\n",  # SyntaxError
        f"class C {{ {M} }}\nfunction C() {{}}\nexport {{ C }};\n",  # SyntaxError
    ],
    ids=["redeclared class with an own write", "redeclared by a let, with a duplicate member", "redeclared by a function"],
)
def test_a_redeclared_class_is_not_given_an_edge_back_by_the_member_rules(make_repo, source):
    # the name is rebound to unknown (the program is rejected, so no call runs); the own-property and duplicate rules add nothing back
    graph = ts_graph(make_repo, {"c.ts": source, "main.ts": CALL_TS})
    assert to_class_m(graph) == []
    assert any("both declare" in u.reason for u in graph.unresolved if u.file == "main.ts")
    control = ts_graph(make_repo, {"c.ts": f"class C {{ {M} }}\nexport {{ C }};\n", "main.ts": CALL_TS})
    assert [e.callee for e in to_class_m(control)] == ["c:C.m"]  # Node: ORIGINAL, the same class not redeclared


@pytest.mark.parametrize(
    "body, node_prints",
    [
        (f"{M}", "ORIGINAL"),
        (f"constructor() {{ this['m'] = {R}; }} {M}", "REPLACED"),
        ("m() { return 'F'; } 'm'() { return 'L'; }", "L"),
    ],
    ids=["plain", "own write", "duplicate"],
)
def test_a_class_in_a_namespace_or_behind_an_import_alias_stays_unresolved_whatever_the_body_says(make_repo, body, node_prints):
    # ripple does not follow `N.C` or `import A = N.C` (main: "'N' is not exported", "named like an import alias"), before and after this
    # branch, so no body rule can draw an edge here; the test pins that none does. Node prints node_prints in each.
    namespace = f"export namespace N {{ export class C {{ {body} }} }}\n"
    through_the_namespace = ts_graph(make_repo, {"c.ts": namespace, "main.ts": "import { N } from './c';\nconsole.log(new N.C().m()); // CALL\n"})
    assert to_class_m(through_the_namespace) == []
    through_an_alias = ts_graph(make_repo, {"c.ts": namespace, "main.ts": "import { N } from './c';\nimport A = N.C;\nconsole.log(new A().m()); // CALL\n"})
    assert to_class_m(through_an_alias) == []
    assert any("import alias" in u.reason for u in through_an_alias.unresolved if u.file == "main.ts")


def test_an_instance_call_through_a_re_exported_name_follows_the_class_body_rules(make_repo):
    barrel = "export { C as D } from './c';\n"
    call = "import { D } from './barrel';\nconsole.log(new D().m()); // CALL\n"
    plain = ts_graph(make_repo, {"c.ts": f"export class C {{ {M} }}\n", "barrel.ts": barrel, "main.ts": call})
    assert [e.callee for e in to_class_m(plain)] == ["c:C.m"]  # Node: ORIGINAL
    written = ts_graph(make_repo, {"c.ts": f"export class C {{ constructor() {{ this['m'] = {R}; }} {M} }}\n", "barrel.ts": barrel, "main.ts": call})
    assert to_class_m(written) == []  # REPLACED
    in_base = ts_graph(make_repo, {"c.ts": f"class B {{ m = {R}; }}\nexport class C extends B {{ {M} }}\n", "barrel.ts": barrel, "main.ts": call})
    assert to_class_m(in_base) == []  # REPLACED
    star = ts_graph(make_repo, {"c.ts": "export class C { m() { return 'F'; } 'm'() { return 'L'; } }\n", "barrel.ts": "export * from './c';\n",
                                "main.ts": "import { C } from './barrel';\nconsole.log(new C().m()); // CALL\n"})
    assert [e.callee for e in to_class_m(star)] == ["c:C.m"]  # L, the last member, which is C.m


def test_an_external_base_named_through_import_require_follows_the_class_body_rules(make_repo):
    head = "import x = require('pkg');\n"
    plain = ts_graph(make_repo, {"c.ts": head + f"export class C extends x.Base {{ {M} }}\n", "main.ts": CALL_TS})
    assert [e.callee for e in to_class_m(plain)] == ["c:C.m"]  # Node: ORIGINAL
    written = ts_graph(make_repo, {"c.ts": head + f"export class C extends x.Base {{ constructor() {{ super(); this['m'] = {R}; }} {M} }}\n", "main.ts": CALL_TS})
    assert to_class_m(written) == []  # REPLACED
    duplicate = ts_graph(make_repo, {"c.ts": head + "export class C extends x.Base { m() { return 'F'; } 'm'() { return 'L'; } }\n", "main.ts": CALL_TS})
    assert [e.callee for e in to_class_m(duplicate)] == ["c:C.m"]  # L
    escaped = ts_graph(make_repo, {"c.ts": head + f"export class C extends x.Base {{ {M} }}\n",
                                   "main.ts": "import { C } from './c';\nconst c = new C();\nconsole.log(c.\\u006d()); // CALL\n"})
    assert [e.callee for e in to_class_m(escaped)] == ["c:C.m"]  # ORIGINAL, and never "inherited from outside the repo"
    assert "a method inherited from a base class outside the repo" not in escaped.external_reasons


@pytest.mark.parametrize(
    "files",
    [
        {"c.ts": "export class C { run() { return process.mainModule!.exports.f(); } }\n"},  # Node: F
        {"p.ts": "export { default as proc } from 'node:process';\n",
         "c.ts": "import { proc } from './p';\nexport class C { run() { return proc.mainModule!.exports.f(); } }\n"},  # F, through a re-export
    ],
    ids=["process.mainModule in a member", "a re-exported process in a member"],
)
def test_process_main_module_inside_a_class_member_is_unresolved_and_not_outside_the_repo(make_repo, files):
    main = "export function f() { return 'F'; }\nimport { C } from './c';\nconsole.log(new C().run()); // CALL\n"
    graph = ts_graph(make_repo, {**files, "main.ts": main})
    assert [e.callee for e in graph.edges if e.file == "main.ts" and e.kind != "constructor"] == ["c:C.run"]
    inside = [u for u in graph.unresolved if u.file == "c.ts"]
    assert len(inside) == 1 and "mainModule" in inside[0].reason
    assert not [e for e in graph.edges if e.caller == "c:C.run"]  # nothing proves it runs main's f
    assert graph.external_calls == 1  # only console.log


def test_process_main_module_in_a_member_and_a_write_that_replaces_that_member(make_repo):
    # Node: REPLACED (the own `run` is called, the member never is); the member's call stays unresolved as above
    source = f"export class C {{ constructor() {{ this['run'] = {R}; }} run() {{ return process.mainModule!.exports.f(); }} }}\n"
    main = "export function f() { return 'F'; }\nimport { C } from './c';\nconsole.log(new C().run()); // CALL\n"
    graph = ts_graph(make_repo, {"c.ts": source, "main.ts": main})
    assert [e for e in graph.edges if e.file == "main.ts" and e.kind != "constructor"] == []
    assert any(u.file == "main.ts" and "run" in u.reason for u in graph.unresolved)
