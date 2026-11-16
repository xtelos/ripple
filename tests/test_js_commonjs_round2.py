"""The second review of the CommonJS model, as unit tests: what the probes against node cannot say by themselves.

The probes (test_js_commonjs_probes.py) pin what node reached. These pin the rules that made each class of finding impossible by
construction, one at a time, so a rule that is loosened fails by name: the shapes an exported value may have, the consumer's forms,
`this` in a sloppy file, what `process.on` hands out, the position of each watched name, a computed require, a path into a directory the
walk skips, a name bound twice, and the cache of a package.json no indexed file stands beside.
"""

import time
from pathlib import Path

import pytest

pytest.importorskip("tree_sitter", reason="TypeScript support is not installed (pip install 'ripple-code[typescript]')")
pytest.importorskip("tree_sitter_typescript", reason="TypeScript support is not installed (pip install 'ripple-code[typescript]')")

from ripple.cache import load_repo  # noqa: E402
from ripple.indexer import find_source_files  # noqa: E402
from ripple.profile import JAVASCRIPT  # noqa: E402
from ripple.ts_indexer import CommonJsReader, load_parsers  # noqa: E402
from ripple.ts_modules import ModuleMap  # noqa: E402

_, TSX = load_parsers()
HELPER = "function helper() { return 1; }\n"


def read(source: str) -> CommonJsReader:
    return CommonJsReader(TSX.parse(source.encode()).root_node)


def make(root: Path, files: dict[str, str]) -> Path:
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    return root


# ---- 1: the exports are closed only when every own property is a function written here with no `this`, or a primitive ----------------

OPENING_VALUES = {
    "an identifier bound by assignment": "var boot;\nboot = function () { this.x = 1; };\nexports.helper = helper;\nexports.boot = boot;\n",
    "a call result": "exports.helper = helper;\nexports.boot = make();\n",
    "a require member": "exports.helper = helper;\nexports.boot = require('./b').boot;\n",
    "a ternary": "exports.helper = helper;\nexports.boot = cond ? helper : null;\n",
    "a logical or": "exports.helper = helper;\nexports.boot = other || helper;\n",
    "a class expression": "exports.helper = helper;\nexports.K = class {};\n",
    "a class declaration by name": "class K {}\nexports.helper = helper;\nexports.K = K;\n",
    "an object": "exports.helper = helper;\nexports.o = {};\n",
    "an array": "exports.helper = helper;\nexports.xs = [];\n",
    "a getter": "module.exports = { helper, get boot() { return 1; } };\n",
    "a setter": "module.exports = { helper, set boot(v) {} };\n",
    "a new expression": "exports.helper = helper;\nexports.inst = new Thing();\n",
    "a function using this": "exports.helper = helper;\nexports.boot = function () { return this; };\n",
    "a method with super": "module.exports = { helper, boot() { super.x = 1; } };\n",
    "an arrow inside a function using this": "exports.helper = helper;\nexports.boot = function () { return () => this; };\n",
    "a function bound twice": "function twice() {}\nfunction twice() { this.x = 1; }\nexports.helper = helper;\nexports.twice = twice;\n",
    "a late property from a member": "module.exports = { helper };\nmodule.exports.boot = other.boot;\n",
}
CLOSED_VALUES = {
    "functions and primitives": "const arrow = () => 1;\nmodule.exports = { helper, n: 1, neg: -1, s: 'x', t: `y`, b: true, nothing: null, arrow, inline: function () { return 2; }, method() { return 3; } };\n",
    "dot properties": "exports.helper = helper;\nexports.version = '1';\nexports.fn = function () { return 1; };\n",
    "a late primitive": "module.exports = { helper };\nmodule.exports.version = 3;\n",
    "a function that uses this only in a function of its own": "exports.helper = helper;\nexports.make = function () { return function () { return this; }; };\n",
}


@pytest.mark.parametrize("shape", sorted(OPENING_VALUES))
def test_an_exported_value_that_is_not_a_function_written_here_without_this_opens_the_exports(shape):
    assert read(HELPER + OPENING_VALUES[shape]).open_why, shape


@pytest.mark.parametrize("shape", sorted(CLOSED_VALUES))
def test_functions_without_this_and_primitives_leave_the_exports_closed(shape):
    reader = read(HELPER + CLOSED_VALUES[shape])
    assert reader.open_why == "", (shape, reader.open_why)


# the consumer: only === and !== (and typeof, !, void, a test) call no hook on the object
CONSUMER_OPENS = ["a == 'x'", "a != 'x'", "a < 1", "a > 1", "a + ''", "`${a}`", "'k' in a", "1 instanceof a", "+a", "-a", "~a", "a * 2", "String(a)", "[a]", "{ a }", "a ?? 1", "a || 1", "a && 1", "(a, 1)", "await a"]
CONSUMER_ALLOWED = ["a === 1", "a !== 1", "typeof a", "!a", "void a", "a ? 1 : 2", "a.helper()", "a.helper", "a === a"]


@pytest.mark.parametrize("form", CONSUMER_OPENS)
def test_a_coercing_or_keeping_use_of_the_required_value_opens_it(form):
    reader = read(f"const a = require('./a');\nasync function f() {{ const r = {form}; }}\n")
    assert all(not site.safe for site in reader.sites.values()), form


@pytest.mark.parametrize("form", CONSUMER_ALLOWED)
def test_a_use_that_calls_no_hook_keeps_the_required_value_closed(form):
    reader = read(f"const a = require('./a');\nconst r = {form};\n")
    assert all(site.safe for site in reader.sites.values()), form


# ---- 2: routes to the loader ---------------------------------------------------------------------------------------------------------

SLOPPY_THIS = {
    "a function declaration": "function g() { return this; }\n",
    "a function expression": "const g = function () { return this; };\n",
    "an arrow in a function": "function g() { return () => this; }\n",
    "a method of an object": "const o = { f() { return this; } };\n",
    "a callback": "[1].map(function () { return this; });\n",
    "a generator": "function* g() { return this; }\n",
}


@pytest.mark.parametrize("shape", sorted(SLOPPY_THIS))
def test_this_in_a_function_of_a_file_that_is_not_strict_reflects(shape):
    assert "this" in read(SLOPPY_THIS[shape]).reflective, shape


@pytest.mark.parametrize("shape", sorted(SLOPPY_THIS))
def test_this_in_a_strict_file_is_harmless(shape):
    assert read("'use strict';\n" + SLOPPY_THIS[shape]).reflective == "", shape


def test_a_function_with_its_own_directive_is_strict_and_the_outer_one_is_not():
    assert read("function g() { 'use strict'; return this; }\n").reflective == ""
    assert read("function g() { 'use strict'; }\nfunction h() { return this; }\n").reflective != ""
    assert read("function g() { 'use strict'; return function () { return this; }; }\n").reflective == ""
    assert read("const o = { m() { return this; } };\nfunction g() { 'use strict'; }\n").reflective != ""


def test_this_in_a_class_body_is_strict_and_a_directive_after_a_statement_is_none():
    assert read("class K { m() { return this; } static s() { return this; } }\n").reflective == ""
    assert read("var x = 1;\n'use strict';\nfunction g() { return this; }\n").reflective != "", "a directive after a statement is a plain string"


def test_a_module_syntax_file_is_strict():
    assert read("import x from 'y';\nfunction g() { return this; }\n").reflective == ""


@pytest.mark.parametrize("source", [
    "arguments[1]('./a');\n", "const r = (() => arguments[1])();\n", "const [, req] = arguments;\n", "Array.prototype.slice.call(arguments);\n", "[...arguments];\n", "typeof arguments;\n",
])
def test_arguments_outside_a_function_of_its_own_reflects(source):
    assert "arguments" in read(source).reflective, source


def test_arguments_of_a_function_is_harmless_until_it_names_a_caller():
    assert read("function f() { return arguments.length; }\n").reflective == ""
    assert read("function f() { return arguments.callee; }\n").reflective != ""
    assert read("function f() { return f.caller; }\n").reflective != ""
    assert read("function f() { return f.arguments; }\n").reflective != ""


@pytest.mark.parametrize("source", [
    "const p = process.on('x', () => {});\n", "process.on('x', () => {}).foo;\n", "[process.once('x', () => {})];\n", "function f() { return process.off('x', () => {}); }\n",
    "const f = () => process.on('x', () => {});\n", "(process.on('x', () => {}), 1);\n", "const g = process.on;\n", "process.on.call(process, 'x', () => {});\n",
])
def test_the_result_of_process_on_is_process_and_using_it_reflects(source):
    assert "process" in read(source).reflective, source


@pytest.mark.parametrize("source", [
    "process.on('exit', () => {});\n", "process.on('exit', function () {});\n", "process.once('exit', function h() {});\n", "function h() {}\nprocess.on('exit', h);\n",
    "process.stdout.on('error', function () {}).write('');\n", "process.nextTick(() => {});\n", "const n = process.cwd().length;\n",
])
def test_process_on_as_a_statement_of_its_own_is_harmless(source):
    assert read("'use strict';\n" + source).reflective == "", source


@pytest.mark.parametrize("source", [
    "process.on('x', function () { this.x = 1; });\n", "function h() { this.x = 1; }\nprocess.on('x', h);\n", "process.on('x', obj.handler);\n", "process.on('x', makeHandler());\n",
])
def test_a_listener_of_process_is_called_with_process_as_this(source):
    assert "listener" in read("'use strict';\n" + source).reflective, source


# ---- 3: the position of each watched name ------------------------------------------------------------------------------------------

REFLECTS = [
    "const { constructor } = function () {};", "const { constructor: F } = function () {};", "const { 'constructor': F } = function () {};", "const { ['constructor']: F } = function () {};",
    "let F; ({ constructor: F } = function () {});", "const F = (function () {})['\\x63onstructor'];", "const F = (function () {})[`constructor`];", "const F = Reflect.get(f, 'x');",
    "const F = Object.getOwnPropertyDescriptor(f, 'constructor');", "const F = f.c\\u006fnstructor;", "const x = y.mainModule;", "const x = y.getBuiltinModule;", "const x = y.binding;",
    "const x = y.dlopen;", "const x = y.caller;", "const x = y.callee;", "const x = y.arguments;", "const x = new Proxy({}, {});", "const x = Reflect;", "const x = y.__proto__;",
    "const x = Object.getPrototypeOf(y);", "Error.prepareStackTrace = (e, s) => s;", "const x = frame.getThis();", "const x = frame.getFunction();", "const x = 'process';",
    "const x = 'require';", "const x = 'exports';", "const x = 'Function';", "foo('node:vm');", "foo('module');", "foo(`vm`);", "const x = Object.prototype;", "Object.prototype.m = f;",
    "function p(O) { O.prototype.m = f; }", "const O = Object;\nO.prototype.m = f;", "const x = ({}).constructor.prototype;", "Object['prototype'].m = f;",
    "const constructor = 1;\nconst o = { constructor };", "const x = 'module' === y;", "const x = { type: 'module' };",
]
HARMLESS = [
    "class K { constructor() {} }", "const o = { constructor: 1, prototype: 2, caller: 3, mainModule: 4, Proxy: 5, 'process': 6 };", "class K { process() {} caller() {} binding = 1; }",
    "const x = y.process;", "const x = y.module;", "const x = y.exports;", "const x = y.require;", "const x = y.global;", "const x = y.cache;", "const cache = 1;", "const x = require('x');",
    "function K() {}\nK.prototype.m = f;\nK.prototype.constructor = K;", "class K {}\nK.prototype.m = f;", "const x = Object.prototype.hasOwnProperty.call(y, 'k');", "const x = Array.prototype.slice.call(y);",
    "const EE = require('events');\nconst x = Object.create(EE.prototype);", "const x = Object.create(Array.prototype);", 
    "const x = require('process-nextick-args');", "const x = <div constructor=\"a\" caller={1} />;",
]


@pytest.mark.parametrize("source", REFLECTS)
def test_a_watched_name_in_no_listed_position_reflects(source):
    assert read(source + "\n").reflective, source


@pytest.mark.parametrize("source", HARMLESS)
def test_a_watched_name_in_a_listed_position_does_not_reflect(source):
    reader = read("'use strict';\n" + source + "\n")
    assert reader.reflective == "", (source, reader.reflective)


# ---- 4, 5: a computed require, and a relative path into a directory the walk skips -------------------------------------------------

@pytest.mark.parametrize("source", ["require('./' + name);", "require(`./lib/${name}`);", "require(name);", "require('./lib/pa\\x74ch');", "require();", "require(...names);"])
def test_every_computed_require_reflects_whatever_its_prefix(source):
    assert "computed" in read(source + "\n").reflective, source


def test_a_literal_require_does_not_reflect():
    assert read("require('./lib/a');\nrequire(`./lib/b`);\n").reflective == ""


@pytest.mark.parametrize("spec", ["../node_modules/pkg", "./node_modules/x/y", "./bower_components/z", "../jspm_packages/q/", "./x/../node_modules/pkg/index.js", "../node_modules/nothing"])
@pytest.mark.parametrize("present", [True, False])
def test_a_relative_path_through_a_skipped_directory_names_a_file_node_runs(tmp_path, spec, present):
    files = {"lib/main.js": "", "node_modules/pkg/index.js": "", "lib/node_modules/x/y.js": "", "lib/bower_components/z/index.js": "", "jspm_packages/q/index.js": ""}
    repo = make(tmp_path / "repo", {k: v for k, v in files.items() if present or k == "lib/main.js"})
    modules = ModuleMap(repo, find_source_files(repo, JAVASCRIPT))
    kind, value = modules.resolve_require(spec, "lib/main.js")
    assert kind == "executes", (kind, value)


def test_node_and_the_compiler_name_different_files_so_which_one_is_not_known(tmp_path):
    """x.cjs/index.js is what node's require finds for './x.cjs' (no .jsx is tried), and the compiler names x.cjs.jsx."""
    repo = make(tmp_path / "repo", {"main.js": "", "x.cjs.jsx": "exports.f = 1;\n", "x.cjs/index.js": "exports.f = 2;\n"})
    modules = ModuleMap(repo, find_source_files(repo, JAVASCRIPT))
    kind, value = modules.resolve_require("./x.cjs", "main.js")
    assert kind == "unresolved" and "x.cjs/index.js to Node" in value and "x.cjs.jsx to the compiler" in value, (kind, value)


# ---- 6: a name a call goes through is bound once ---------------------------------------------------------------------------------------

def graph_of(root: Path, cache: Path):
    return load_repo(root, cache_dir=cache, lang="auto").graph


def edges_at(graph, file: str, line: int) -> list[str]:
    return sorted(e.callee for e in graph.edges if e.file == file and e.line == line and e.kind in ("call", "constructor"))


@pytest.mark.parametrize("main", [
    "var a = require('./lib/a');\n{ function a() {} }\na.helper();\n", "var { helper } = require('./lib/a');\n{ function helper() {} }\nhelper();\n",
    "const a = require('./lib/a');\ntry {} catch (a) {}\na.helper();\n", "var a = require('./lib/a');\nfor (a of []) {}\na.helper();\n",
    "var a = require('./lib/a');\n[a] = [1];\na.helper();\n", "var a = require('./lib/a');\nvar a = 1;\na.helper();\n", "var a = require('./lib/a');\nfunction f(a) {}\na.helper();\n",
])
def test_a_required_name_bound_again_in_the_file_is_not_followed(tmp_path, main):
    repo = make(tmp_path / "repo", {"lib/a.js": "function helper() {}\nmodule.exports = { helper };\n", "main.js": main})
    assert edges_at(graph_of(repo, tmp_path / "c"), "main.js", main.count("\n")) == []


def test_a_name_bound_once_keeps_its_edge(tmp_path):
    repo = make(tmp_path / "repo", {"lib/a.js": "function helper() {}\nmodule.exports = { helper };\n", "main.js": "const a = require('./lib/a');\nconst { helper: h } = require('./lib/a');\na.helper();\nh();\n"})
    graph = graph_of(repo, tmp_path / "c")
    assert edges_at(graph, "main.js", 3) == ["lib/a:helper"] and edges_at(graph, "main.js", 4) == ["lib/a:helper"]


def test_a_function_declared_again_in_a_block_of_a_sloppy_file_is_not_followed_but_a_strict_one_is(tmp_path):
    body = "function helper() {}\n{ function helper() {} }\nhelper();\n"
    sloppy = make(tmp_path / "sloppy", {"main.js": body})
    strict = make(tmp_path / "strict", {"main.js": "'use strict';\n" + body})
    assert edges_at(graph_of(sloppy, tmp_path / "c1"), "main.js", 3) == []
    assert edges_at(graph_of(strict, tmp_path / "c2"), "main.js", 4) != []


# ---- 7: the cache notices a package.json in a directory that holds no indexed file -------------------------------------------------

def test_a_package_json_edited_where_no_source_file_stands_changes_the_answer_the_cache_gave(tmp_path):
    repo = make(tmp_path / "repo", {
        "lib/a.js": "function helper() {}\nmodule.exports = { helper };\n",
        "main.js": "const a = require('./lib/a');\ntry { require('./sub'); } catch (e) {}\na.helper();\n",
        "sub/package.json": '{"name": "sub"}',
        "sub/lib.txt": "require('../lib/a').helper = function () {};\n",
    })
    cache = tmp_path / "cache"
    assert edges_at(graph_of(repo, cache), "main.js", 3) == ["lib/a:helper"]
    time.sleep(0.02)
    (repo / "sub/package.json").write_text('{"main": "lib.txt"}')
    cached = edges_at(graph_of(repo, cache), "main.js", 3)
    fresh = edges_at(graph_of(repo, tmp_path / "fresh"), "main.js", 3)
    assert cached == fresh == [], (cached, fresh)
