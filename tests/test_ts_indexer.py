"""The TypeScript front end, from small snippets of source to the graph the resolver builds from the facts.

Every test indexes a few files with the TYPESCRIPT profile. They need tree-sitter (the `typescript` extra)
and are skipped, with the reason, when it is not installed.
"""

import pytest

pytest.importorskip("tree_sitter", reason="TypeScript support is not installed (pip install 'ripple-code[typescript]')")
pytest.importorskip("tree_sitter_typescript", reason="TypeScript support is not installed (pip install 'ripple-code[typescript]')")

from ripple.indexer import build_graph  # noqa: E402
from ripple.profile import TYPESCRIPT  # noqa: E402


def index(make_repo, files):
    return build_graph(make_repo(files), TYPESCRIPT)


def edges(graph):
    return {(e.caller, e.callee) for e in graph.edges}


def kinds(graph, caller, callee):
    return sorted(e.kind for e in graph.edges if (e.caller, e.callee) == (caller, callee))


def unresolved(graph):
    return {(u.caller, u.text) for u in graph.unresolved}


# ---- what is a symbol

def test_functions_consts_bound_to_functions_classes_and_members_are_named_by_declaration(make_repo):
    graph = index(
        make_repo,
        {
            "src/m.ts": """
            export function plain() {}
            export const arrow = (x: number) => x;
            let expr = function () {};
            const generated = async function* () {};
            export class Box {
              static make() { return new Box(); }
              get size(): number { return 1; }
              set size(v: number) {}
              private hidden() {}
              async load() {}
            }
            """
        },
    )
    assert {s.id: s.kind for s in graph.symbols.values()} == {
        "src/m": "module",
        "src/m:plain": "function",
        "src/m:arrow": "function",
        "src/m:expr": "function",
        "src/m:generated": "function",
        "src/m:Box": "class",
        "src/m:Box.make": "method",
        "src/m:Box.size": "method",  # a getter and a setter of one name are one symbol
        "src/m:Box.hidden": "method",
        "src/m:Box.load": "method",
    }
    assert graph.modules == {"src/m.ts": "src/m"}


def test_an_index_file_is_its_directory_and_a_dotted_file_name_keeps_its_dot(make_repo):
    graph = index(make_repo, {"src/util/index.ts": "export const a = 1;", "index.ts": "", "tests/a.test.ts": "export const b = 1;"})
    assert set(graph.modules.values()) == {"src/util", "index", "tests/a.test"}


def test_anonymous_functions_belong_to_the_function_or_module_around_them(make_repo):
    graph = index(
        make_repo,
        {
            "src/m.ts": """
            function helper() {}
            function outer(xs: number[]) {
              xs.forEach((x) => helper());
              const held = [1].map(function () { return helper(); });
              return { run() { helper(); } };
            }
            [1].forEach(() => helper());
            """
        },
    )
    assert {s.id for s in graph.symbols.values()} == {"src/m", "src/m:helper", "src/m:outer"}
    assert edges(graph) == {("src/m:outer", "src/m:helper"), ("src/m", "src/m:helper")}


def test_a_call_made_where_the_module_runs_belongs_to_the_module_and_a_default_parameter_to_the_callee(make_repo):
    graph = index(
        make_repo,
        {
            "src/m.ts": """
            function start(): number { return 1; }
            function other(): number { return 2; }
            export const LEVEL = start();
            export function level(base: number = other()): number { return base; }
            export function caller() { return level(3); }
            """
        },
    )
    assert edges(graph) == {("src/m", "src/m:start"), ("src/m:level", "src/m:other"), ("src/m:caller", "src/m:level")}


# ---- classes

def test_a_class_with_no_constructor_is_its_own_constructor_and_forwards_to_its_base(make_repo):
    graph = index(
        make_repo,
        {
            "src/m.ts": """
            export class Base { constructor(public n: number) {} }
            export class Crate extends Base {}
            export class Plain {}
            export class Derived extends Base { constructor() { super(1); } }
            export function make() { return [new Crate(1), new Plain(), new Derived(), new Base(2)]; }
            """
        },
    )
    assert edges(graph) == {
        ("src/m:make", "src/m:Crate"),
        ("src/m:Crate", "src/m:Base.constructor"),  # the implicit super(...args)
        ("src/m:make", "src/m:Plain"),
        ("src/m:make", "src/m:Derived.constructor"),
        ("src/m:Derived.constructor", "src/m:Base.constructor"),
        ("src/m:make", "src/m:Base.constructor"),
    }
    assert kinds(graph, "src/m:make", "src/m:Crate") == ["constructor"]


def test_this_super_and_overrides(make_repo):
    graph = index(
        make_repo,
        {
            "src/m.ts": """
            export class Item {
              describe(): string { return 'item'; }
              restock(): string { return this.describe(); }
              static create(): Item { return new Item(); }
              static twice(): Item { return this.create(); }
            }
            export class Perishable extends Item {
              override describe(): string { return `${super.describe()} (p)`; }
            }
            """
        },
    )
    assert edges(graph) == {
        ("src/m:Item.restock", "src/m:Item.describe"),
        ("src/m:Item.restock", "src/m:Perishable.describe"),  # this may be a Perishable
        ("src/m:Perishable.describe", "src/m:Item.describe"),  # super.describe()
        ("src/m:Item.create", "src/m:Item"),  # new Item(): no constructor in source
        ("src/m:Item.twice", "src/m:Item.create"),  # `this` in a static method is the class
        ("src/m:Perishable", "src/m:Item"),  # the implicit constructor forwards to Item's, which is Item itself
    }
    assert kinds(graph, "src/m:Item.restock", "src/m:Perishable.describe") == ["override"]


def test_a_parameter_property_and_a_field_type_the_member_they_declare(make_repo):
    graph = index(
        make_repo,
        {
            "src/m.ts": """
            class Repo { save() {} }
            class Cache { flush() {} }
            export class Service {
              private cache = new Cache();
              loose: Repo | undefined;
              constructor(private repo: Repo, public readonly name: string) {}
              run() { this.repo.save(); this.cache.flush(); this.loose?.save(); this.name.trim(); }
            }
            """
        },
    )
    assert edges(graph) == {
        ("src/m:Service.run", "src/m:Repo.save"),
        ("src/m:Service.run", "src/m:Cache.flush"),
        ("src/m:Service.constructor", "src/m:Cache"),  # the field initializer runs in the constructor
    }
    assert graph.unresolved == []  # name.trim() is a method of a string, outside the repo


def test_this_dot_field_assigned_a_new_instance_types_that_field(make_repo):
    graph = index(
        make_repo,
        {
            "src/m.ts": """
            class Conn { send() {} }
            export class Client {
              conn;
              constructor() { this.conn = new Conn(); }
              go() { this.conn.send(); }
            }
            """
        },
    )
    assert ("src/m:Client.go", "src/m:Conn.send") in edges(graph)


# ---- accessors and callbacks

def test_reading_a_getter_and_writing_a_setter_are_calls_and_a_plain_property_is_not(make_repo):
    graph = index(
        make_repo,
        {
            "src/m.ts": """
            export class Item {
              private qty = 1;
              sku = 'a';
              get quantity(): number { return this.qty; }
              set quantity(next: number) { this.qty = next; }
              get total(): number { return this.qty * 2; }
              restock(by: number) { this.quantity = this.qty + by; this.sku = 'b'; return this.total; }
            }
            export function read(item: Item) { item.quantity += 1; return item.quantity + item.total + item.sku.length; }
            """
        },
    )
    assert edges(graph) == {
        ("src/m:Item.restock", "src/m:Item.quantity"),
        ("src/m:Item.restock", "src/m:Item.total"),
        ("src/m:read", "src/m:Item.quantity"),
        ("src/m:read", "src/m:Item.total"),
    }
    assert graph.unresolved == []  # a plain property is no unresolved call


def test_a_function_passed_by_name_to_a_builtin_that_runs_it_is_called_and_other_passing_is_not(make_repo):
    graph = index(
        make_repo,
        {
            "src/m.ts": """
            function show(x: number): string { return String(x); }
            function later(): void {}
            function register(cb: () => void): void {}
            export function run(xs: number[]) {
              const a = xs.map(show);
              const b = [1, 2].filter(show);
              setTimeout(later, 10);
              register(later);
              return a.concat(b);
            }
            """
        },
    )
    # register(later) is a call of register, and passing `later` to it says nothing about who calls it
    assert edges(graph) == {("src/m:run", "src/m:show"), ("src/m:run", "src/m:register")}
    assert kinds(graph, "src/m:run", "src/m:show") == ["callback", "callback"]  # xs.map(show) and [1, 2].filter(show)


def test_async_await_and_promise_chains(make_repo):
    graph = index(
        make_repo,
        {
            "src/m.ts": """
            async function tick(): Promise<void> { await Promise.resolve(); }
            function done(n: number): number { return n; }
            export async function fetchRate(zone: string): Promise<number> { await tick(); return 1; }
            export async function cost(zone: string) { const r = await fetchRate(zone); return done(r); }
            export function later(zone: string): Promise<number> { return fetchRate(zone).then((r) => done(r)); }
            """
        },
    )
    assert edges(graph) == {
        ("src/m:fetchRate", "src/m:tick"),
        ("src/m:cost", "src/m:fetchRate"),
        ("src/m:cost", "src/m:done"),
        ("src/m:later", "src/m:fetchRate"),
        ("src/m:later", "src/m:done"),
    }
    assert graph.unresolved == []


# ---- receiver typing: what is proven, and nothing else

def test_a_receiver_is_typed_by_new_an_annotation_a_return_annotation_and_this_and_by_nothing_else(make_repo):
    graph = index(
        make_repo,
        {
            "src/m.ts": """
            class Dog { bark() {} }
            interface Animal { bark(): void }
            function adopt(): Dog { return new Dog(); }
            function guess() { return new Dog(); }
            export function run(a: Dog, b: Animal, c, d: Dog | null, e: Dog[], f: Map<string, Dog>) {
              const made = new Dog();
              const annotated: Dog = adopt();
              const returned = adopt();
              const unknown = guess();
              const copied = made;
              made.bark();       // new
              a.bark();          // annotated parameter
              annotated.bark();  // annotated variable
              returned.bark();   // return annotation
              copied.bark();     // copy of a typed variable
              d?.bark();         // nullable annotation
              b.bark();          // interface: unresolved
              c.bark();          // unannotated parameter: unresolved
              unknown.bark();    // function with no return annotation: unresolved
              e.map((x) => x.bark());   // callback parameter: unresolved
              for (const x of e) { x.bark(); }  // loop variable: unresolved
            }
            """
        },
    )
    assert edges(graph) == {
        ("src/m:run", "src/m:Dog.bark"),
        ("src/m:run", "src/m:adopt"),
        ("src/m:run", "src/m:guess"),
        ("src/m:run", "src/m:Dog"),
        ("src/m:adopt", "src/m:Dog"),
        ("src/m:guess", "src/m:Dog"),
    }
    # one edge per typed call: made, a, annotated, returned, copied and d
    assert kinds(graph, "src/m:run", "src/m:Dog.bark") == ["call"] * 6
    lines = sorted(u.line for u in graph.unresolved if u.text.endswith("bark"))
    assert len(lines) == 5  # b, c, unknown, the callback's x, the loop's x


def test_computed_keys_and_methods_chosen_by_name_stay_unresolved(make_repo):
    graph = index(
        make_repo,
        {
            "src/m.ts": """
            function one(): void {}
            const TABLE: Record<string, () => void> = { one };
            class Inv { labels() {} }
            export function dispatch(kind: string) { TABLE[kind](); }
            export function byName(inv: Inv, name: 'labels') { return inv[name](); }
            """
        },
    )
    assert edges(graph) == set()
    assert unresolved(graph) >= {("src/m:dispatch", "TABLE[kind]"), ("src/m:byName", "inv[name]")}


def test_a_decorated_member_is_never_resolved_and_the_decorator_is_an_unresolved_call(make_repo):
    graph = index(
        make_repo,
        {
            "src/m.ts": """
            function seal(s: string) { return s; }
            export function logged(_t: object, _k: string, d: PropertyDescriptor) { return d; }
            export class Audited {
              @logged
              stamp(s: string) { return seal(s); }
            }
            export function use() { return new Audited().stamp('a'); }
            """
        },
    )
    assert edges(graph) == {("src/m:Audited.stamp", "src/m:seal"), ("src/m:use", "src/m:Audited")}
    assert ("src/m", "@logged") in unresolved(graph)  # applied by the module, not by the class


def test_this_inside_a_function_expression_is_not_the_class(make_repo):
    graph = index(
        make_repo,
        {
            "src/m.ts": """
            export class A {
              go() {}
              arrow() { [1].forEach(() => this.go()); }
              plain() { [1].forEach(function () { this.go(); }); }
            }
            function loose() { this.go(); }
            """
        },
    )
    assert edges(graph) == {("src/m:A.arrow", "src/m:A.go")}
    assert {("src/m:A.plain", "this.go"), ("src/m:loose", "this.go")} <= unresolved(graph)


# ---- imports and re-exports

def test_imports_named_aliased_default_namespace_and_the_js_extension_rewrite(make_repo):
    graph = index(
        make_repo,
        {
            "src/util/math.ts": "export function clamp() {}\nexport const roundTo = () => 1;\n",
            "src/util/format.ts": "export default function label() {}\n",
            "src/main.ts": """
            import { clamp as limit } from './util/math.js';
            import * as math from './util/math';
            import label from './util/format';
            import type { roundTo } from './util/math';
            export function run() { limit(); math.roundTo(); label(); }
            """,
        },
    )
    assert edges(graph) == {
        ("src/main:run", "src/util/math:clamp"),
        ("src/main:run", "src/util/math:roundTo"),
        ("src/main:run", "src/util/format:label"),
    }


def test_barrels_default_re_exports_namespace_re_exports_and_export_star(make_repo):
    graph = index(
        make_repo,
        {
            "src/models/item.ts": "export class Item { go() {} }\n",
            "src/models/index.ts": "export { Item } from './item';\n",
            "src/util/format.ts": "export function formatQty() {}\nexport default function label() {}\n",
            "src/util/index.ts": "export * from './format';\nexport { default as label } from './format';\n",
            "src/pricing.ts": "export function lineTotal() {}\n",
            "src/index.ts": "export * from './models';\nexport * as pricing from './pricing.ts';\nexport { label } from './util';\n",
            "tests/a.test.ts": """
            import { Item, pricing, label } from '../src';
            import { formatQty } from '../src/util';
            export function check(item: Item) { item.go(); pricing.lineTotal(); label(); formatQty(); }
            """,
        },
    )
    assert edges(graph) == {
        ("tests/a.test:check", "src/models/item:Item.go"),
        ("tests/a.test:check", "src/pricing:lineTotal"),
        ("tests/a.test:check", "src/util/format:label"),
        ("tests/a.test:check", "src/util/format:formatQty"),
    }


def test_export_star_does_not_forward_the_default_export(make_repo):
    graph = index(
        make_repo,
        {
            "src/f.ts": "export default function label() {}\nexport function named() {}\n",
            "src/barrel.ts": "export * from './f';\n",
            "src/main.ts": "import { default as viaStar, named } from './barrel';\nexport function run() { viaStar(); named(); }\n",
        },
    )
    assert edges(graph) == {("src/main:run", "src/f:named")}
    assert ("src/main:run", "viaStar") in unresolved(graph)


def test_a_re_export_is_not_a_call_and_a_type_only_import_adds_nothing(make_repo):
    graph = index(
        make_repo,
        {
            "src/a.ts": "export function f() {}\nexport interface T {}\n",
            "src/b.ts": "import type { T } from './a';\nimport { type T as U, f } from './a';\nexport { f } from './a';\nexport type { T };\n",
        },
    )
    assert graph.edges == [] and graph.unresolved == []


def test_tsconfig_paths_and_base_url_resolve_aliased_imports(make_repo):
    graph = index(
        make_repo,
        {
            "tsconfig.json": '{ // aliases\n "compilerOptions": { "baseUrl": ".", "paths": { "@wh/*": ["src/*"], }, }, }',
            "src/policy.ts": "export function levelFor() {}\n",
            "lib/tool.ts": "export function run() {}\n",
            "src/main.ts": "import { levelFor } from '@wh/policy';\nimport { run } from 'lib/tool';\nimport { x } from 'react';\n"
            "export function go() { levelFor(); run(); x(); }\n",
        },
    )
    assert edges(graph) == {("src/main:go", "src/policy:levelFor"), ("src/main:go", "lib/tool:run")}
    assert graph.external_calls == 1  # react


def test_an_import_that_names_no_file_is_unresolved_not_external(make_repo):
    graph = index(make_repo, {"src/main.ts": "import { gone } from './gone';\nexport function go() { gone(); }\n"})
    assert graph.edges == [] and graph.external_calls == 0
    assert unresolved(graph) == {("src/main:go", "gone")}


# ---- tests

def test_test_callbacks_are_named_by_their_describe_and_it_titles_and_describe_is_the_module(make_repo):
    graph = index(
        make_repo,
        {
            "src/m.ts": "export function sum() {}\nexport function product() {}\n",
            "tests/m.test.ts": """
            import { sum, product } from '../src/m';
            const helper = () => sum();
            describe('math', () => {
              const local = () => product();
              beforeEach(() => { sum(); });
              it('adds', () => { sum(); local(); });
              describe('inner', () => {
                it(`multiplies`, async () => { await product(); });
              });
            });
            it('works outside a describe', () => { helper(); });
            """,
        },
    )
    ids = {s.id for s in graph.symbols.values() if s.file == "tests/m.test.ts"}
    assert ids == {
        "tests/m.test",
        "tests/m.test:helper",
        "tests/m.test:local",  # declared in a describe callback, which is the module
        "tests/m.test:math > adds",
        "tests/m.test:math > inner > multiplies",
        "tests/m.test:works outside a describe",
    }
    assert edges(graph) == {
        ("tests/m.test:helper", "src/m:sum"),
        ("tests/m.test:local", "src/m:product"),
        ("tests/m.test", "src/m:sum"),  # beforeEach's callback is anonymous, so it is the module's
        ("tests/m.test:math > adds", "src/m:sum"),
        ("tests/m.test:math > adds", "tests/m.test:local"),
        ("tests/m.test:math > inner > multiplies", "src/m:product"),
        ("tests/m.test:works outside a describe", "tests/m.test:helper"),
    }


def test_a_test_function_imported_from_the_runner_is_still_a_test_and_a_repo_function_named_it_is_not(make_repo):
    graph = index(
        make_repo,
        {
            "src/m.ts": "export function sum() {}\n",
            "tests/a.test.ts": "import { describe, it as test } from 'vitest';\nimport { sum } from '../src/m';\n"
            "describe('s', () => { test('t', () => { sum(); }); });\n",
            "src/it.ts": "export function it(title: string, cb: () => void) {}\nexport function go() { it('x', () => {}); }\n",
        },
    )
    assert "tests/a.test:s > t" in graph.symbols
    assert set(graph.symbols) & {"src/it:x"} == set()


# ---- files

def test_a_tsx_file_is_read_without_render_edges_and_calls_inside_jsx_are_found(make_repo):
    graph = index(
        make_repo,
        {
            "src/ui.tsx": """
            import { useMemo } from 'react';
            function pct(n: number) { return n; }
            export function Badge({ text }: { text: string }) { return <b>{text}</b>; }
            export function Row({ n }: { n: number }) {
              const p = useMemo(() => pct(n), [n]);
              return <li>{pct(p)}<Badge text="x" />{[1].map((i) => <Badge key={i} text={String(pct(i))} />)}</li>;
            }
            """
        },
    )
    assert graph.parse_errors == []
    assert edges(graph) == {("src/ui:Row", "src/ui:pct")}  # <Badge/> is not a call


def test_a_file_with_a_parse_error_contributes_what_it_can_and_is_listed_unparsable(make_repo):
    graph = index(
        make_repo,
        {
            "src/a.ts": "function ok() {}\nexport function before() { ok(); }\n"
            "interface Bad<in out T> { x: T }\n"  # variance annotations: the grammar cannot read them
            "export function after() { ok(); }\n",
            "src/b.ts": "export function fine() {}\n",
        },
    )
    assert graph.parse_errors == ["src/a.ts"]
    assert {("src/a:before", "src/a:ok"), ("src/a:after", "src/a:ok")} <= edges(graph)
    assert graph.modules == {"src/a.ts": "src/a", "src/b.ts": "src/b"}


def test_a_file_the_front_end_fails_on_costs_that_file_and_not_the_index(make_repo, monkeypatch):
    from ripple import ts_indexer

    def boom(self, node):
        raise RuntimeError("a syntax tree nobody wrote a case for")

    monkeypatch.setattr(ts_indexer.FileBuilder, "visit_new_expression", boom)
    graph = index(
        make_repo,
        {"src/bad.ts": "export function f() { return new Date(); }\n", "src/ok.ts": "export function g() {}\nexport function h() { g(); }\n"},
    )
    assert graph.parse_errors == ["src/bad.ts"]
    assert edges(graph) == {("src/ok:h", "src/ok:g")}
    assert graph.modules == {"src/ok.ts": "src/ok"}  # the failed file was not indexed at all


def test_declaration_files_and_dependencies_are_not_indexed(make_repo):
    graph = index(
        make_repo,
        {
            "src/a.ts": "export function a() {}\n",
            "src/types.d.ts": "export declare function a(): void;\n",
            "node_modules/dep/index.ts": "export function dep() {}\n",
        },
    )
    assert set(graph.modules) == {"src/a.ts"} and graph.skipped_dirs == ["node_modules"]


def test_two_files_that_fold_to_one_module_id_each_keep_a_module_of_their_own_and_the_loser_is_listed(make_repo):
    graph = index(
        make_repo,
        {
            "src/util.ts": "export function foo() {}\n",
            "src/util/index.ts": "export function foo() {}\nexport function bar() {}\n",
            "src/main.ts": "import { foo, bar } from './util';\nexport function m() { foo(); bar(); }\n",
        },
    )
    # `./util` is util.ts (the compiler tries the file before the directory), so foo is its foo and bar is nobody's
    assert edges(graph) == {("src/main:m", "src/util:foo")}
    assert graph.modules["src/util.ts"] == "src/util" and graph.modules["src/util/index.ts"] == "src/util/index"
    assert graph.module_collisions() == {} and graph.parse_errors == ["src/util/index.ts"]
    assert graph.symbols["src/util/index:foo"].file == "src/util/index.ts"


def test_callers_and_impact_answer_on_a_typescript_graph_given_full_ids_and_list_no_tests(make_repo):
    from ripple.queries import callers, impact

    graph = index(
        make_repo,
        {
            "src/m.ts": "export function a() {}\nexport function b() { a(); }\nexport function c() { b(); }\n"
            "export class K { constructor() { a(); } }\n",
            "tests/m.test.ts": "import { c } from '../src/m';\ndescribe('s', () => { it('t', () => { c(); }); });\n",
        },
    )
    assert [c["symbol"] for c in callers(graph, "src/m:a", TYPESCRIPT)["callers"]] == ["src/m:K.constructor", "src/m:b"]
    result = impact(graph, "src/m:a", profile=TYPESCRIPT)
    found = {e["symbol"] for entries in result["by_file"].values() for e in entries}
    assert found == {"src/m:b", "src/m:c", "src/m:K.constructor", "tests/m.test:s > t"}
    assert result["tests"] == []  # no test collection yet: the test callback is listed as code


def test_the_profile_chooses_the_front_end_and_the_cache_does_not_mix_languages(make_repo):
    from ripple.cache import load_graph

    repo = make_repo({"src/a.ts": "export function f() {}\n", "src/b.py": "def g():\n    pass\n"})
    ts, cached = load_graph(repo, profile=TYPESCRIPT)
    assert cached is False and {"src/a:f"} == {s.id for s in ts.symbols.values() if s.kind == "function"}
    assert load_graph(repo, profile=TYPESCRIPT)[1] is True
    py, cached = load_graph(repo)
    assert cached is False and {"b.g"} == {s.id for s in py.symbols.values() if s.kind == "function"}


# ---- blocks, catch clauses and anonymous functions are scopes of names, never of calls


def at(graph, caller):
    """[(line, callee)] of the calls a symbol makes."""
    return sorted((e.line, e.callee) for e in graph.edges if e.caller == caller)


A_HANDLE = {"a.ts": "export function handle() {}\nexport function other() {}\n"}


def test_a_const_arrow_in_a_block_does_not_capture_a_call_after_the_block(make_repo):
    graph = index(
        make_repo,
        {
            **A_HANDLE,
            "main.ts": """\
            import { handle } from './a';
            export function m(flag: boolean) {
              if (flag) { const handle = () => 'local'; handle(); }
              handle();
            }
            """,
        },
    )
    # inside the block `handle` is the block's own, after it the import
    assert at(graph, "main:m") == [(3, "main:m.handle"), (4, "a:handle")]


def test_a_function_declaration_in_a_block_is_block_scoped(make_repo):
    graph = index(
        make_repo,
        {
            **A_HANDLE,
            "main.ts": """\
            import { handle } from './a';
            export function m() {
              {
                function handle() { return 'local'; }
                handle();
              }
              handle();
            }
            """,
        },
    )
    assert at(graph, "main:m") == [(5, "main:m.handle"), (7, "a:handle")]


def test_a_class_in_a_block_does_not_capture_a_use_of_the_class_after_it(make_repo):
    graph = index(
        make_repo,
        {
            "m.ts": """\
            class Svc { run() {} }
            export function m(f: boolean) {
              if (f) { class Svc { run() {} } new Svc().run(); }
              new Svc().run();
            }
            """
        },
    )
    assert at(graph, "m:m") == [(3, "m:m.Svc"), (3, "m:m.Svc.run"), (4, "m:Svc"), (4, "m:Svc.run")]


def test_a_local_of_an_anonymous_function_does_not_leak_to_the_function_around_it(make_repo):
    graph = index(
        make_repo,
        {
            "m.ts": """\
            class Cat { speak() {} }
            class Dog { speak() {} }
            const pet = new Cat();
            export function g() { pet.speak(); }
            [1].forEach(() => { const pet = new Dog(); pet.speak(); });
            (() => { const pet = new Dog(); pet.speak(); })();
            """
        },
    )
    assert at(graph, "m:g") == [(4, "m:Cat.speak")]  # the module's pet, not the callback's
    assert at(graph, "m") == [(3, "m:Cat"), (5, "m:Dog"), (5, "m:Dog.speak"), (6, "m:Dog"), (6, "m:Dog.speak")]


def test_a_local_function_in_a_callback_is_the_callbacks_and_not_the_module_functions(make_repo):
    graph = index(
        make_repo,
        {
            **A_HANDLE,
            "main.ts": """\
            import { handle } from './a';
            [1].forEach(() => { const handle = () => 1; handle(); });
            handle();
            """,
        },
    )
    assert at(graph, "main") == [(2, "main:handle"), (3, "a:handle")]


def test_a_catch_parameter_a_callback_parameter_and_a_loop_variable_hide_the_name_only_inside(make_repo):
    graph = index(
        make_repo,
        {
            "a.ts": "export function e() {}\nexport function cb() {}\nexport function item() {}\nexport function k() {}\n",
            "main.ts": """\
            import { e, cb, item, k } from './a';
            export function caught() { try { e(); } catch (e) { e(); } e(); }
            export function param(xs: any[]) { xs.forEach((cb: any) => cb()); cb(); }
            export function loop(xs: any[]) { for (const item of xs) { item(); } item(); }
            export function late() { k(); for (let k = 0; k < 1; k++) { k(); } }
            """,
        },
    )
    assert at(graph, "main:caught") == [(2, "a:e"), (2, "a:e")]  # the one in the catch clause is the parameter
    assert at(graph, "main:param") == [(3, "a:cb")]
    assert at(graph, "main:loop") == [(4, "a:item")]
    assert at(graph, "main:late") == [(5, "a:k")]


def test_a_name_declared_in_one_switch_case_or_block_is_not_the_name_in_another(make_repo):
    graph = index(
        make_repo,
        {
            "a.ts": "export function pick() {}\nexport function alt() {}\n",
            "main.ts": """\
            import { pick, alt } from './a';
            export function m(k: number) {
              switch (k) { case 1: { let pick = alt; pick(); break; } }
              pick();
            }
            export function n(k: number) {
              switch (k) { case 1: const pick = alt; break; default: pick(); }
              pick();
            }
            """,
        },
    )
    assert at(graph, "main:m") == [(4, "a:pick")]  # `let pick = alt` is an alias ripple does not follow
    assert at(graph, "main:n") == [(8, "a:pick")]  # `pick` is the switch's own in every case, and the import after it


def test_a_block_local_in_module_code_does_not_replace_what_a_function_reads(make_repo):
    graph = index(
        make_repo,
        {
            **A_HANDLE,
            "main.ts": """\
            import { handle, other } from './a';
            if (process.env.X) { const handle = other; }
            {
              function inner() {}
            }
            export function m() { handle(); }
            """,
        },
    )
    assert at(graph, "main:m") == [(6, "a:handle")]


def test_a_function_expressions_own_name_is_the_function_not_what_the_module_binds(make_repo):
    graph = index(
        make_repo,
        {
            **A_HANDLE,
            "main.ts": """\
            import { handle } from './a';
            export const f = function handle() { handle(); };
            export function g() { handle(); }
            """,
        },
    )
    assert at(graph, "main:f") == []
    assert at(graph, "main:g") == [(3, "a:handle")]


def test_describe_locals_are_each_blocks_own_and_still_reach_their_own_tests(make_repo):
    graph = index(
        make_repo,
        {
            "src.ts": "export class A { run() {} }\nexport class B { run() {} }\n",
            "x.test.ts": """\
            import { A, B } from './src';
            describe('a', () => {
              const svc = new A();
              it('x', () => { svc.run(); });
            });
            describe('b', () => {
              const svc = new B();
              it('y', () => { svc.run(); });
            });
            """,
        },
    )
    assert at(graph, "x.test:a > x") == [(4, "src:A.run")]
    assert at(graph, "x.test:b > y") == [(8, "src:B.run")]


def test_an_annotation_names_the_class_outside_a_parameter_that_has_the_same_name(make_repo):
    graph = index(
        make_repo,
        {
            "m.ts": """\
            class Svc { run() {} }
            export function named(Svc: Svc) { Svc.run(); }
            export function anonymous() { [1].forEach((Svc: Svc) => Svc.run()); }
            """
        },
    )
    assert at(graph, "m:named") == [(2, "m:Svc.run")]
    assert at(graph, "m:anonymous") == [(3, "m:Svc.run")]


def test_a_block_local_used_inside_its_own_block_and_a_hoisted_function_still_resolve(make_repo):
    graph = index(
        make_repo,
        {
            "m.ts": """\
            export function use() {
              early();
              if (true) { const local = () => 1; local(); nested(); function nested() {} }
              function early() {}
            }
            """
        },
    )
    assert at(graph, "m:use") == [(2, "m:use.early"), (3, "m:use.local"), (3, "m:use.nested")]


def test_an_interface_declared_in_a_block_hides_a_class_of_that_name_only_there(make_repo):
    graph = index(
        make_repo,
        {
            "m.ts": """\
            class Foo { go() {} }
            export function m() {
              { interface Foo { go(): void } const f: Foo = {} as any; f.go(); }
              const g: Foo = new Foo();
              g.go();
            }
            """
        },
    )
    assert at(graph, "m:m") == [(4, "m:Foo"), (5, "m:Foo.go")]


def test_a_class_in_a_block_or_callback_extends_a_class_of_the_same_block_and_inherits_its_methods(make_repo):
    graph = index(
        make_repo,
        {
            "m.ts": """\
            if (process.env.X) {
              class P { hello() {} }
              class C extends P { go() { this.hello(); } }
              new C().go();
            }
            [1].forEach(() => {
              class Q { hi() {} }
              class D extends Q { constructor() { super(); this.hi(); } }
              new D();
            });
            """
        },
    )
    assert at(graph, "m:C") == [(3, "m:P")]
    assert at(graph, "m:C.go") == [(3, "m:P.hello")]
    assert at(graph, "m:D.constructor") == [(8, "m:Q"), (8, "m:Q.hi")]
    assert at(graph, "m") == [(4, "m:C"), (4, "m:C.go"), (9, "m:D.constructor")]


def test_a_class_in_a_block_extends_an_outer_class_and_an_imported_one(make_repo):
    graph = index(
        make_repo,
        {
            "a.ts": "export class Imported { hello() {} }\n",
            "m.ts": """\
            import { Imported } from './a';
            class Outer { hi() {} }
            if (process.env.X) {
              class C extends Outer { go() { this.hi(); } }
              class D extends Imported { go() { this.hello(); } }
            }
            """,
        },
    )
    assert at(graph, "m:C") == [(4, "m:Outer")]
    assert at(graph, "m:C.go") == [(4, "m:Outer.hi")]
    assert at(graph, "m:D") == [(5, "a:Imported")]
    assert at(graph, "m:D.go") == [(5, "a:Imported.hello")]


def test_two_sibling_blocks_that_each_declare_a_class_p_resolve_each_to_its_own(make_repo):
    graph = index(
        make_repo,
        {
            "m.ts": """\
            export function f(c: boolean) {
              if (c) {
                class P { one() {} }
                class C extends P { go() { this.one(); } }
              } else {
                class P { two() {} }
                class C extends P { go() { this.two(); } }
              }
            }
            """
        },
    )
    assert at(graph, "m:f.C.go") == [(4, "m:f.P.one")]
    assert at(graph, "m:f.C#2.go") == [(7, "m:f.P#2.two")]
    # a private frame key is never an id: only the second declaration of a name in one parent gets a #2
    assert {s.id for s in graph.symbols.values() if "#" in s.id} == {"m:f.P#2", "m:f.P#2.two", "m:f.C#2", "m:f.C#2.go"}


def test_an_unresolved_base_in_a_block_stays_unresolved_and_its_reason_names_the_source_name(make_repo):
    graph = index(
        make_repo,
        {
            "m.ts": """\
            declare function make(): any;
            export function f(c: boolean) {
              if (c) {
                const Base = make();
                class C extends Base { go() { this.hello(); } }
                const handle = make();
                handle.run();
              }
            }
            """
        },
    )
    assert at(graph, "m:f.C.go") == []
    reasons = [u.reason for u in graph.unresolved if u.caller == "m:f.C.go"]
    assert reasons and all("Base" in r and "#" not in r for r in reasons)
    assert not [u for u in graph.unresolved if "#" in u.reason or "#" in u.text]


# ---- members that hide a base class's method


def test_a_field_a_static_field_and_a_parameter_property_hide_the_base_method_of_that_name(make_repo):
    graph = index(
        make_repo,
        {
            "m.ts": """\
            class Base { run() { return 1; } static make() { return 2; } size() { return 3; } }
            class Child extends Base { run = () => 3; static make = () => 4; }
            class Param extends Base { constructor(public size: () => number) { super(); } }
            export function h() { new Child().run(); Child.make(); }
            export function p() { new Param(() => 1).size(); }
            """
        },
    )
    assert not any(e.callee in ("m:Base.run", "m:Base.make", "m:Base.size") for e in graph.edges)
    assert {("m:h", "new Child().run"), ("m:h", "Child.make"), ("m:p", "new Param(() => 1).size")} <= unresolved(graph)


def test_a_field_assigned_in_a_method_hides_the_base_method_unless_it_is_an_accessor(make_repo):
    graph = index(
        make_repo,
        {
            "m.ts": """\
            class Base { run() { return 1; } go() { return 2; } }
            class Child extends Base {
              constructor() { super(); this.run = () => 3; }
            }
            class Setter extends Base {
              get go() { return 9; }
              set go(v: number) { this.go = v; }
            }
            export function h(c: Child) { c.run(); }
            export function s(c: Setter) { c.go = 4; }
            """
        },
    )
    assert not any(e.callee == "m:Base.run" for e in graph.edges)
    assert ("m:s", "m:Setter.go") in edges(graph)  # `this.go = v` in a setter calls the setter: go is not an own property


def test_a_field_of_an_unrelated_class_or_of_the_class_itself_changes_nothing_else(make_repo):
    graph = index(
        make_repo,
        {
            "m.ts": """\
            class Base { run() { return 1; } }
            class Other { run = () => 2; }
            class Child extends Base { name = 'x'; other() {} }
            export function h() { new Child().run(); new Child().other(); }
            """
        },
    )
    assert ("m:h", "m:Base.run") in edges(graph) and ("m:h", "m:Child.other") in edges(graph)


def test_an_abstract_member_hides_the_concrete_method_a_grandparent_defines(make_repo):
    graph = index(
        make_repo,
        {
            "m.ts": """\
            class G { area() { return 1; } }
            abstract class S extends G { abstract area(): number; abstract get size(): number; }
            class C extends S { area() { return 2; } get size() { return 3; } }
            export function m(s: S) { s.area(); }
            """
        },
    )
    assert not any(e.callee == "m:G.area" for e in graph.edges)


def test_a_static_and_an_instance_member_of_one_name_are_two_symbols_and_a_call_reaches_neither(make_repo):
    graph = index(
        make_repo,
        {
            "m.ts": """\
            export class A { static make() { return 's'; } make() { return 'i'; } static only() {} also() {} }
            export function m() { A.make(); new A().make(); A.only(); new A().also(); }
            """
        },
    )
    assert {s.id for s in graph.symbols.values() if s.kind == "method"} == {
        "m:A.make",
        "m:A.make#2",
        "m:A.only",
        "m:A.also",
    }
    assert {("m:m", "A.make"), ("m:m", "new A().make")} <= unresolved(graph)
    assert {("m:m", "m:A.only"), ("m:m", "m:A.also")} <= edges(graph)


def test_a_getter_and_a_setter_of_one_name_are_still_one_symbol(make_repo):
    graph = index(make_repo, {"m.ts": "export class A { get v() { return 1; } set v(x: number) {} }\n"})
    assert {s.id for s in graph.symbols.values() if s.kind == "method"} == {"m:A.v"}


def test_a_method_rebound_to_this_by_the_constructor_is_still_that_method_and_any_other_value_hides_it(make_repo):
    graph = index(
        make_repo,
        {
            "m.ts": """\
            function other() {}
            export class C {
              constructor() { this.h = this.h.bind(this); this.k = other; }
              h() {}
              k() {}
              go() { this.h(); this.k(); }
            }
            """
        },
    )
    assert at(graph, "m:C.go") == [(6, "m:C.h")]


# ---- what a call reaches through `this`, a callback, a type the language names itself


def test_only_the_first_argument_of_a_callback_method_is_called(make_repo):
    graph = index(
        make_repo,
        {
            "m.ts": """\
            export function wrap(acc: any, f: any) { return acc; }
            export function base() {}
            export function cb(x: number) { return x; }
            export function ctx() {}
            export function m(xs: number[]) {
              xs.reduce(wrap, base);
              xs.map(cb, ctx);
              xs.forEach(cb);
            }
            """
        },
    )
    assert at(graph, "m:m") == [(6, "m:wrap"), (7, "m:cb"), (8, "m:cb")]


def test_a_getter_read_through_a_call_on_this_in_a_function_that_rebinds_this_is_not_the_class(make_repo):
    graph = index(
        make_repo,
        {
            "m.ts": """\
            export class Item { get qty() { return 1; } }
            export class Other { get qty() { return 2; } }
            export class Box {
              make(): Item { return new Item(); }
              run() {
                const o = { make(): Other { return new Other(); }, go() { return this.make().qty; } };
                return this.make().qty;
              }
            }
            """
        },
    )
    # `new Other()` belongs to run (the literal's methods are anonymous); only the read where `this` is the box is a getter call
    assert at(graph, "m:Box.run") == [(6, "m:Other"), (7, "m:Box.make"), (7, "m:Item.qty")]


def test_a_local_named_it_or_test_is_a_call_not_a_test_case(make_repo):
    graph = index(
        make_repo,
        {
            "a.ts": "export function g() {}\n",
            "m.ts": """\
            import { g } from './a';
            export function f(it: (t: string, cb: () => void) => void) { it('x', () => g()); }
            export function h() { const test = 1; test('y', () => g()); }
            """,
        },
    )
    assert {s.id for s in graph.symbols.values() if s.kind == "function"} == {"a:g", "m:f", "m:h"}
    assert at(graph, "m:f") == [(2, "a:g")] and ("m:f", "it") in unresolved(graph)
    assert at(graph, "m:h") == [(3, "a:g")]


def test_a_primitive_array_or_tuple_type_is_the_language_builtin_whatever_the_repo_calls_a_class(make_repo):
    graph = index(
        make_repo,
        {
            "m.ts": """\
            class Array { map() {} }
            class String { trim() {} }
            class Number { fixed() {} }
            export function m(xs: number[]) { xs.map(x => x); }
            export function n(s: string) { s.trim(); }
            export function t(pair: [number, string]) { pair.map(x => x); }
            export function r(): string { return ''; }
            export function q() { r().trim(); }
            export function lit(x: 'a' | 'b', y: 3) { x.trim(); y.fixed(); }
            export function user() { new Array().map(); new String().trim(); }
            """
        },
    )
    for caller in ("m:m", "m:n", "m:t", "m:lit"):
        assert at(graph, caller) == [], caller
    assert at(graph, "m:q") == [(8, "m:r")]  # r() is called, and what it returns is the builtin String
    assert {("m:user", "m:Array.map"), ("m:user", "m:String.trim")} <= edges(graph)


# ---- files whose ids clash, a config that cannot be read, a stale config


def test_a_tsconfig_that_cannot_be_read_is_listed_with_the_files_that_did_not_parse(make_repo):
    graph = index(make_repo, {"src/a.ts": "export function f() {}\n", "tsconfig.json": "{ nope"})
    assert graph.parse_errors == ["tsconfig.json"]
    assert "src/a:f" in {s.id for s in graph.symbols.values()}


def test_a_file_that_clashes_by_extension_gets_an_id_of_its_own_and_is_listed(make_repo):
    graph = index(
        make_repo,
        {
            "a.ts": "export function foo() {}\n",
            "a.tsx": "export function foo() {}\n",
            "main.ts": "import { foo } from './a';\nexport function m() { foo(); }\n",
        },
    )
    assert edges(graph) == {("main:m", "a:foo")}
    assert graph.symbols["a:foo"].file == "a.ts" and graph.symbols["a.tsx:foo"].file == "a.tsx"
    assert graph.parse_errors == ["a.tsx"]


def test_an_edit_to_a_config_that_tsconfig_extends_is_not_served_from_the_cache(make_repo, tmp_path_factory):
    import json
    import os

    from ripple.cache import load_graph

    repo = make_repo(
        {
            "a/util.ts": "export function helper() {}\n",
            "b/util.ts": "export function helper() {}\n",
            "main.ts": "import { helper } from '@x/util';\nexport function m() { helper(); }\n",
            "tsconfig.json": json.dumps({"extends": "./tsconfig.base.json"}),
            "tsconfig.base.json": json.dumps({"compilerOptions": {"paths": {"@x/*": ["a/*"]}}}),
        }
    )
    cache = tmp_path_factory.mktemp("cache")
    graph, hit = load_graph(repo, cache, profile=TYPESCRIPT)
    assert not hit and edges(graph) == {("main:m", "a/util:helper")}
    base = repo / "tsconfig.base.json"
    base.write_text(json.dumps({"compilerOptions": {"paths": {"@x/*": ["b/*"]}}}))
    stat = base.stat()
    os.utime(base, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
    graph, hit = load_graph(repo, cache, profile=TYPESCRIPT)
    assert not hit and edges(graph) == {("main:m", "b/util:helper")}
