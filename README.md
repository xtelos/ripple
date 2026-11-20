# ripple

**Know what breaks before you change it.** ripple is an MCP server that gives a coding agent
(Claude Code, Cursor, anything that speaks MCP) the call graph of a Python, TypeScript, JavaScript, Go, Rust or Java repository, so the agent
can ask "who calls this, and which tests reach it?" before it edits, and run just those tests after.

## Why

A coding agent dropped into unfamiliar code edits blind. It greps for a function name, sees a few
hits, and changes the signature. The callers that reach it through an import alias, a `self.` call in
a subclass or a re-export in a package `__init__` don't match the grep, and the first sign of trouble
is a failing test, or a failure in production. ripple answers the structural question directly and
says plainly where static analysis runs out.

## Quickstart

Requires Python 3.10+ and [uv](https://docs.astral.sh/uv/).

```sh
uv tool install git+https://github.com/xtelos/ripple   # puts `ripple` on your PATH
```

**Claude Code**, from your project directory:

```sh
claude mcp add ripple -- ripple serve --repo "$PWD"
```

**Cursor**, in `.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "ripple": { "command": "ripple", "args": ["serve", "--repo", "${workspaceFolder}"] }
  }
}
```

Or from a terminal: `ripple index .`, then `ripple impact price_order`.

## The five tools

| Tool | What it answers | Read-only |
|---|---|---|
| `callers(symbol)` | Who calls this directly, with the file:line of each call site | yes |
| `callees(symbol)` | What this calls, plus the calls ripple could not resolve and why | yes |
| `impact(symbol, depth=5, limit=100)` | Every transitive caller grouped by file, the tests that reach it listed separately as test ids (pytest node ids; `file_test.go::TestName` for Go), and same-named call sites ripple could not rule out | yes |
| `path(source, target)` | The shortest call chain from one symbol to another | yes |
| `check(tests=None, lang=None)` | Runs the test command (default `python -m pytest -q`, using the repo's `.venv` or `venv` if present, for a repo with only Python files; `go test -json` in every module for a Go repo, see [Go](#go); `cargo test` in every top-level workspace for a Rust repo, see [Rust](#rust); `mvn test` for a Maven repo, see [Java](#java); a TypeScript-only repo, or `--lang typescript`, has no default; a repo of several languages is refused until `lang` names one, unless the server has a `--lang` or a test command), or just the given test paths (for Python, pytest node ids too; for Go, `file_test.go::TestName[/subtest]`; for Rust, a `.rs` file, a directory or `file.rs::module::test_name`; for Java, the id `impact` lists, a test `.java` file or a directory), and returns pass/fail, the summary line and the first failures, never the full log | no, it runs code |

A symbol can be a full dotted path (`shop.pricing.price_order`), a suffix (`OrderService.place_order`)
or a bare name (`price_order`); in TypeScript it is a path and a member (`src/util/math:clamp`, see
[TypeScript](#typescript)). An ambiguous name returns the candidates rather than picking one.

A bare name (no dot, slash or colon) that matches a module is never answered as the module with no callers, with
one exception below. What answers when:

- The module declares a function or class of that name (`src/foo.ts` defining `foo`): that symbol.
- The module exports the name as a `memo`, `forwardRef` or alias of a function or class
  (`export const SubMenu = forwardRef(SubMenuFR)`, `export { Impl as Name }`, a barrel's `export { SubMenu as Menu }
  from "./SubMenu"`): that function, and the `note` of the answer says how (`SubMenu is exported by
  src/components/SubMenu as forwardRef(SubMenuFR); showing src/components/SubMenu:SubMenuFR`; a barrel's note names
  each hop and the wrapper at the end of it), in the text, the `--json` output and the tool result.
- Anything that leaves two readings of the name is ambiguous, and the answer lists them: a module that declares a
  `render` and also exports `render` as something else (`export { renderV2 as render }`, or a wrapper ripple cannot
  name) lists the declared symbol, the exported one when ripple can name it, and the module; two modules that export
  or define the name as different symbols (a `Card` in `src/Card` and another in `src/legacy/Widgets`, wrapped or not)
  list both, whatever the files are called; a module and a symbol of some other module list both.
- The module has no symbol of that name: an error that lists what the module defines and exports and names the
  full module id (`shop.tax`, `src/util/math`) to ask about the module itself. A full module id, or any path, is
  answered as before.

A TypeScript id written as the module, a colon and an exported name (`src/components/SubMenu:SubMenu`) resolves the
same way when no symbol has that id and the module exports the name as a `memo`, `forwardRef`, alias or re-export of
a function or class: the answer is that function, with the same `note`, for `callers`, `callees`, `impact` and
`path`, in the text, the `--json` output and the tool result. The module may be written as a path (`components/SubMenu`,
`src/components/SubMenu.tsx`), and `default` is a name a module can export (`website/pages/_app:default`). When a
path ends more than one module (`components/SubMenu` in a monorepo, or `x` for `x.ts` and `x.tsx`), the symbols the
modules declare under the name and what each of the others exports under it are all counted, and when they are more
than one symbol the answer is ambiguous and lists them, whether the modules declare the name, export it as a wrapper
or alias, or one of each. An id that is a symbol is never reinterpreted: `src/render:render` is the function `render`
the module declares, even if the module also exports `render` as something else (the bare name `render` is the
ambiguous answer). When the module exists and exports no such name, the answer is still `no symbol named ...`, and its
`suggestions` begin with the module's own top-level symbols and exports as the `module:Name` ids that resolve, closest
to the name asked first (at most 100; when it lists fewer than the module has, the error says how many there are, and
for a path that ends several modules how many modules match and how many symbols and exports they have in all),
followed by the five closest names in the repo (those not already listed) as before. A module that does not exist keeps the old suggestions. Python ids use dots, so a Python query with a colon
in it is answered as it always was.

The exception is a module at the repo root, whose whole id is the bare name (`SubMenu.tsx` is the module `SubMenu`,
`tax.py` is `tax`): the name cannot be told from a question about the module. If the module also defines or exports a
symbol of that name, the answer is ambiguous and lists the module and the symbol (in Python too, as it already was for
a module in a package, `shop/foo.py` defining `foo`); if it has none, the answer is about the module. A symbol that
is not exported by name is not found by it: `export const Name = Inner` is not an alias, because ripple does not read a
call of `Name` as a call of `Inner`, and `const Card = memo(Inner); export default Card` is not read either: the
module's candidates list `Inner`.

The four graph tools advertise `readOnlyHint: true`, which lets a client run them without prompting.
`check` is marked destructive instead, because a test suite can do anything its tests do.

`callers`, `callees`, `impact` and `path` also take `kinds` (`--kinds` on the command line): which edges to follow,
from `calls`, `renders`, `renderer` and `implements`, comma separated, default `all`. Only a TypeScript repo with JSX has
`renders` and `renderer` (see [JSX](#jsx)), only a Go repo has `implements` (see [Go](#go)); in a Python repo every edge is a
call and nothing is labelled. `implements` is never a call: a Go call through a repo interface may dispatch to the methods
of the repo types that provably satisfy the interface, so `callers` lists them under `may_be_called_through_interface`,
`impact` under `may_be_reached_through_interface` and `callees` under `may_dispatch_to`, each with a note, and
`--kinds calls` leaves them out. Those lists are possible, never complete, and not counted in `count`, `affected`, the
affected tests or the `edges` statistic (`ripple index` prints their number apart).

A TypeScript symbol that is applied as a decorator also lists where, apart from its callers (see
[TypeScript](#typescript)), and `callers` and `impact` of a symbol that a class ripple does not read may call say so.

`impact` lists at most `limit` callers and `limit` tests (100 by default, up to 1000 over MCP). When
it found more, it says so: `truncated` is true and `omitted_callers` / `omitted_tests` give the
counts (the CLI prints "... and N more callers not listed"). Decorator sites are bounded the same way:
`decorations_omitted` and `decorations_of_callers_omitted` give how many sites were found and not listed (the CLI
prints "... and N more decorator sites not listed"), and `impact` counts them in `truncated` too, so a true `truncated` with both
`omitted_*` counts at zero means decorator sites were left out. `callers` and `path` have no `truncated`: they carry
`decorations_omitted` alone. Each test comes with its pytest node id
(`tests/test_x.py::test_y`, or `tests/test_x.py::TestZ::test_y` for a method), found by pytest's
default collection rules: `test*` functions at module level in `test_*.py` and `*_test.py` files,
`test*` methods of `Test*` classes that have no `__init__`, and every `test*` method of a
`unittest.TestCase` subclass whatever its name. A class runs the test methods it inherits, so a
method defined on a base is listed once for each class that runs it, under that class's node id
(`TestChild::test_shared`). Pass those node ids to `check` to run just those tests. Helpers in test
files that are not tests themselves are listed with the other callers, since they may need updating.

`check(tests=[...])` accepts only test file paths and node ids inside the repository. Anything that
starts with `-` or `@` (pytest reads options from an `@file`) or points outside the repo is refused,
so a caller cannot slip in a pytest option (`--basetemp=DIR` deletes `DIR`). Output is read from a
temporary file and the timeout covers the whole run, so a test that leaves a process running in the
background cannot hold up the result.
The summary and each failure or output line `check` returns are cut to 240 characters, ending in
`[truncated N chars]` when something was cut.

Example, trimmed:

```
$ ripple impact tax_for --repo bench/fixtures/shop
impact of shop.tax.tax_for (depth 5): 6 callers
  shop/pricing.py
    shop.pricing.price_order  line 8  (1 hop)
  shop/service.py
    shop.service.OrderService.place_order  line 17  (2 hops)
    shop.service.OrderService.reprice  line 26  (2 hops)
tests that reach it (3):
  tests/test_pricing.py::test_percent_discount_and_tax
  ...

$ ripple check tests/test_pricing.py::test_percent_discount_and_tax --repo bench/fixtures/shop
{
  "passed": true,
  "summary": "1 passed in 0.01s",
  ...
```

## TypeScript

TypeScript is read by tree-sitter, which is an optional extra: it adds `tree-sitter` and
`tree-sitter-typescript` and nothing else (the base install needs only the `mcp` SDK).

```sh
uv tool install "ripple-code[typescript] @ git+https://github.com/xtelos/ripple"   # or pip install 'ripple-code[typescript]'
```

`--lang auto|python|typescript|javascript|go|rust|java` (default `auto`; `RIPPLE_LANG` sets it for every command, and
`build_server` takes a `lang` argument) chooses how the repo is read. `auto` looks at the file names: `.py`
for Python, `.ts`, `.tsx`, `.mts` and `.cts` for TypeScript, `.js`, `.jsx`, `.mjs` and `.cjs` for JavaScript (see
JavaScript below), `.go` for Go, `.rs` for Rust, `.java` for Java, skipping `.d.ts`, `node_modules` and virtualenvs. A repo with none of them, or one that does
not exist, is an error (exit status 2), not "0 files".

`check` does not pick a language for you. With files of two or more languages (found by the same walk, so
`node_modules`, virtualenvs, build output and declaration files count for nothing), no `--lang` and no test command,
it refuses with exit status 2 (a tool error from the server) and names the languages and the flag:
`cannot choose which tests to run in <repo>: it has Python and Go source files, and a verdict from one language's tests says
nothing about the others. Pass --lang python or --lang go ...`. A `passed` that covers one language of several is a guess,
so the answer is to say which: `--lang go` or `RIPPLE_LANG` on the command line, and for the server's `check` tool its
optional `lang` argument (`check(lang="go")`, the same names as `--lang`; the refusal names it, and `ripple serve --lang go`
fixes one for the session). A `lang` on a call overrides the server's `--lang` for that call only, and an unknown one is the
"unknown language ... choose one of" error. An explicit `--lang` always wins. A test command (`--test-command`, `RIPPLE_TEST_COMMAND`) is not refused: with one,
`check` takes its exit code as the verdict and the language (`--lang`, or the tool's `lang`) only chooses which output lines are listed as failures. A command that
comes to nothing (empty, only blanks, `''`) is no command, and is refused like none. TypeScript
and JavaScript count as one language here (neither has a runner or a default command, and both list failures the same way).
`index`, `impact`, `path` and the other graph commands read every language and are not affected.

**Symbols** are the file's path without the extension, a colon and the member: `src/util/math:clamp`,
`src/cart/cart:Cart.add` (an `index` file is its directory, a second declaration of a name under one parent
gets `#2`, a test callback is `tests/a.test:suite > case`). Any of these finds it, when it names one
symbol: that id, the path with its extension (`src/util/math.ts:clamp`), the tail of the path (`math:clamp`),
`Class.method` or the bare member (a bare name that is also a module's or a re-export's is answered as described
above). A path is read through the id the indexer gave that file, never folded by
a rule (with `src/a.ts` beside `src/a/index.ts`, the index file is `src/a/index`); `src/util/` is the index
file of that directory. More than one match returns the candidates; ripple never picks.

```
$ ripple impact clamp --repo bench/fixtures/ts/warehouse-ts --limit 4
impact of src/util/math:clamp (depth 5): 16 callers
  src/models/item.ts
    src/models/item:normalise  line 7  (1 hop)
    src/models/item:Item.quantity  line 22  (2 hops)
  ...
  ... and 12 more callers not listed; raise --limit to see them
tests that reach it (0):
```

**Resolved:** a name declared or imported (relative imports, `tsconfig` `baseUrl`, `paths` and a relative
`extends`, barrels, `export *`, with the tsconfig scoping below), `this` and `super`, `new C()`, a value annotated with a class, a call result
whose function has a return annotation, a namespace import, and a function passed by name as the first argument
of `map`, `forEach` and the other array methods that run it at once.
**Unresolved, with the reason:** an interface-typed value, `table[key]()`, a method chosen by name, a loop
variable, a value of no stated type, `require` and a `tsconfig` that extends a package. Class fields
holding arrow functions, object literal methods, enums and namespaces are not supported: their calls stay
unresolved, or the code is skipped. A call ripple cannot prove is listed as unresolved, never guessed, and `impact`
lists the same-named ones under `possible_missed_callers` as it does for Python.

**Which tsconfig a file gets.** A config's `baseUrl` and `paths` are the aliases of the files it governs and of no
other. Every `tsconfig.json` above an indexed file is read, and a file belongs to the nearest one whose `files`,
`include` and `exclude` cover it, read the way the compiler reads them: with neither `files` nor `include`, every
source file under the config's directory; `exclude` removes files (its `**` reaches hidden directories, which an
include's `**` and the default include do not), a `\` in a path a config writes is a `/` as it is for the compiler,
and when `exclude` is not written it defaults to `node_modules`, `bower_components`, `jspm_packages` and `outDir`.
When a nested config (an app's) governs a file, its
`paths` decide and the root's are not consulted. A relative `extends` contributes `paths`, `baseUrl`, `files`,
`include` and `exclude`, each pattern relative to the config that wrote it (a `${configDir}` in `baseUrl`, `paths`,
`files`, `include`, `exclude`, `rootDir` or `outDir` is the directory of the config being compiled, the extending one),
and a list the extending config writes replaces the base's. `references` are followed: a config that does not cover a file hands it to the first project it
references that does (the layout of a Vite or shadcn app, where the root `tsconfig.json` is `"files": []` plus
references to `tsconfig.app.json` and the like), and that project's `paths` and `baseUrl` apply. A reference is a path
relative to the config that lists it: a `.json` path is that config and any other is a directory holding a
`tsconfig.json`. References are read four levels down, each config once (so a cycle ends), a path that names no
config is ignored, and a config does not inherit the references of one it extends. A file no config governs has no
aliases, so a bare import in it is a package, never a file of the repo, with two exceptions. When a config above the
file lists references and a config in that tree maps the specifier (a `paths` entry it matches, or a repo file its
`baseUrl` reaches), the import is left unresolved with the reason (which project owns the file is the open question),
and a call through it is listed as an unresolved call, not counted as code outside the repo. That is the
case that matters in a monorepo: an app the root `include` leaves out, which depends on a published copy of a package
the root `paths` maps to its own source, is not given the source (the app runs the published copy). And when a config's
`include` names the file but a same-named file of higher extension priority (`x.tsx` beside `x.ts`) takes its place in
the program, the file is in no project yet is built like its twin, so an alias that config maps is unresolved, with the
reason, never a package and never an edge. A file that no `include` names has no aliases at all. A bare import whose
package name is the `name` of a `package.json` in the repo (a package importing itself, or a workspace package named by
another: `"pkg": "workspace:*"`) is unresolved, with the reason: it reaches repo code through that package's `exports`,
which ripple does not read. Ripple reads nothing else of a `package.json`, and only the `name` of those in a directory
above or holding a source file; that costs those edges, and none is guessed. A config that cannot be read
governs everything under it with no aliases and is listed with the files that did not parse. The JSX settings
(`jsxFactory` and the others, under JSX) are those of the config that governs the file, and the root config's too. A
`jsconfig.json` is a config like any other and beside a `tsconfig.json` it governs the files that one does not hold (see
JavaScript). A relative import that names no file is unresolved, not missing, under a config that sets `rootDirs`, which
ripple does not follow.

**Decorators.** A decorator (`@tag`, `@route("/x")`) written on a class, a method, a property or a parameter is not a
call edge: what it does to the thing it decorates is not followed, and a member it decorates is never resolved. It
stays an unresolved call of the scope that holds the class. When its name resolves to a symbol of the repo,
`callers`, `impact` and `path` for that symbol list each place it is applied under `decorations`, with a note that
these are not calls and are not modelled. A decorator written through a receiver typed as a base class (`@reg.add()`
with `reg: Base`) is listed for the subclass methods that override `add` too, as a call through that receiver gets an
override edge. A name that did not resolve but ends the way the symbol's does goes under
`possible_decorations`, and a name proven to come from outside the repo is never listed. `callees` of the scope that
writes the decorators lists them under `applies_decorators`, naming the decorator only when its name resolved.
`impact` also lists, under `decorations_of_callers`, each place where one of the callers it found is itself applied as
a decorator (with `decorator`, the caller it applies, and its `distance`): such a caller runs again each time a
decorated class is defined, so the reach of the change continues past it. The decorated classes are not followed or
counted as callers, `affected` and `more_beyond_depth` are unchanged, and only sites whose name resolved to the caller
are listed. The
keys exist only when there is something to list, and the callers and `affected` count of a symbol are what the call
edges prove, whatever its decorations. A factory call `@registry(args)` is one of these sites. The call of
`registry` does run when the class is defined, but the benchmark fixture has no decorator for its ground truth to
confirm an edge, so it is only listed. A call inside a decorator's arguments is an ordinary call, made by
the scope that holds the class (the module, or the function or method the class is written in). When the class is
written inside another class (a field initializer, a static block, a method of an object literal), the scope that
runs the call is not provable, so no edge is made: the call is an unresolved call with the reason "a decorator on a
class written inside another class", and `callers` and `impact` of what it calls list it under `possible_missed_callers`.

**Class expressions** (the class a decorator returns) and `export default class {}` are not read. Each call written
inside one is an unresolved call of the scope around it, with the reason "inside a class that ripple does not read".
`impact` lists the same-named ones under `possible_missed_callers` with that reason, and `callers`, which lists no
unresolved calls of its own, lists exactly these under the same key. In a repo of both languages, a decoration
or a class-expression call is only ever listed for a TypeScript symbol, never for a Python one of the same name.

```
$ ripple callers src/decorators/registry:registry --repo tsyringe
callers of src/decorators/registry:registry (0):
applied as a decorator (12), not calls; what a decorator does is not modelled:
  src/__tests__/global-container.test:doesn't blow up with empty args  src/__tests__/global-container.test.ts:577  written @registry()
  ...
```

**Measured**, the same way as the Python benchmark (`python -m bench.ts_score`): on the fixture
(`bench/fixtures/ts/warehouse-ts`, ground truth from a run of its jest and vitest tests) precision 1.000,
recall 0.967 (88 of 91 calls). Held out, on seven real libraries the indexer was never developed against
(neverthrow, tsyringe, superstruct, react-accessible-accordion, react-error-boundary, react-hot-toast and
react-pro-sidebar), recorded at runtime: 328 library edges, 312 seen at runtime, none false (precision 312 of 312,
so the false-edge rate is below about 1.2%, not zero); recall of the pairs that ran is 0.84 (302 of 359), and
45 percent of call sites stay unresolved. That is the honest cost of not guessing. An eighth, ts-pattern, could
not be recorded: 138 edges were indexed and 15 of them checked by hand, all correct, and it is in none of those
figures. The held-out figures were recorded before ripple read parameter decorators and flagged the calls inside
class expressions, and were not recorded again. On tsyringe those changes add two edges (`delay(...)` written in the
argument of a parameter decorator, now made by the module that holds the class) and 43 unresolved calls, and
remove or change none. The fixture figures above are measured on the current code.

**Not yet:** jest or vitest collection. `impact` lists every
TypeScript caller, tests included, as code to update, so its `tests` list is empty, and `check` refuses a
TypeScript-only repo until it is given the command (`--test-command` or `RIPPLE_TEST_COMMAND`, for example
`npx vitest run`), with the refusal as an ordinary error. A repo of TypeScript and another language is
refused until `--lang` or a command says what to run (see `--lang` above). It takes TypeScript test file paths only: ripple's own test ids
(`tests/a.test:suite > case`) and a runner's line filter (`tests/a.test.ts:12`) are refused.

### JavaScript

JavaScript is its own language, read by the same front end and the same tree-sitter extra as TypeScript (no new
dependency): the `tsx` grammar parses `.js`, `.jsx`, `.mjs` and `.cjs`, JSX included, because JSX is legal in a `.js` file.
Its ids are paths like TypeScript's (`lib/app:helper`, and `lib/app.js:helper`, `app:helper` or `lib/app.js` find the same
things). `--lang javascript` reads only the JavaScript files, `--lang typescript` only the TypeScript ones, and `auto`
reads both, each into a graph of its own, cached under its own key, with TypeScript first. `ripple index` counts each by
its label (`TypeScript: 21 files`, `JavaScript: 1 file`).

**ES modules only.** `import` and `export` are followed (default, `export *`, `export { a as b } from`, a directory's
`index.js`, `..` and `../` for the directory above). CommonJS (`require`, `module.exports`) is not modelled yet: a call
through one is unresolved with its reason, never an edge. That includes an ES import of a file that assigns
`module.exports` or `exports.x` (Node gives it named exports), whose reason says the module is CommonJS.

**Which file an import names** is the TypeScript compiler's answer where Node and the bundlers agree with it, and
unresolved, with the reason, where they do not. The compiler tries every TypeScript extension (`.ts`, `.tsx`, then the
directory's `index`) across all the places an alias names, and only then the JavaScript ones (`.js`, `.jsx`), for a
JavaScript importer too; `.mjs` and `.cjs` are named in full. A TypeScript importer takes the `x.ts` that `./x` and `./x.js`
name (the `x.js` beside it is what the build makes of it). Node and the bundlers run the `x.js` a JavaScript importer
names, so a JavaScript importer of `./x` or `./x.js` with an `x.ts` beside is unresolved. An extensionless `./dual` beside
`dual.js` and `dual.mjs` is `dual.js` for the compiler and for Node's `require` and `dual.mjs` for Vite and Rollup (whose
`resolve.extensions` put `.mjs` first, and `.js` before `.ts`), so it is unresolved too, as is `./x` with an `x.mjs` beside
`x/index.js`, and a TypeScript importer's `./x` with an `x.mts` beside the `x.ts`. A trailing slash names a directory. A
package that ships one source as `x.js`, `x.mjs` and `x.cjs` has one id for all three, which the first of them keeps (`.js`,
then `.mjs`, then `.cjs`); the others are read in full under their own path (`x.mjs:f`), and `ripple index` says so in a
note rather than listing them as unparsable.

**What a `package.json` decides, ripple leaves open.** A specifier that names a directory of the repo (written relative,
through `paths` or `baseUrl`, or reached by any of an alias's targets) is never a package and never a missing path: when
the directory has a `package.json` its `main`, `module`, `types` or `exports` picks the file, and the compiler, Node
and a bundler each read them their own way, so the import is unresolved with the reason even when an `index.js` is there
(a `package.json` with a `main` that is not `index.js` is the case that draws a wrong edge, and the one without a `main`
the case that merely loses a right one). A directory with no `package.json` and no index file ripple reads is unresolved
too. A `.js` file found beside such a directory (`x.js` beside `x/package.json`) is unresolved, for the compiler's
TypeScript pass enters the directory first. A `#` specifier is mapped by the nearest `package.json`'s `imports` and is always
unresolved. A bare specifier is a package unless a config maps it or it names a package of the repo (see "Which tsconfig a
file gets"). These rules apply to TypeScript importers too.

**A path that may name a file of the repo is never a package.** A relative import is never a package where it reaches source of
the repo: a file in a hidden directory or in the root `dist`, `build` or `env` (the walk skips them) is unresolved with the
reason, as is a path that leaves the repo by `..` and walks back in (`../repo/a.mjs` from the root of `repo`, found on the file
system, a link back in included), for either language. An entry of the repo whose name starts with two dots (`./..x/a.mjs`,
`./..lib.mjs`) does not leave it and is read like any other relative path: the file at the root is an edge, one in a skipped
directory is unresolved. A path that leaves for good, a JSON, CSS or image file, and a file of a package in `node_modules` are
packages as before, by the same rule for a relative path and for an alias (`./data.json` and `@/data.json` are both packages). A
relative import of a `.vue`, `.svelte` or `.astro` file, which ripple does not read, is unresolved (an alias that names one, or a
file the walk skips, is too), and a path with no extension, or a directory's `/index`, that a resolver gives one (`.js`, `.d.ts`,
`.vue`) to such a file names that file and is never "missing"; only a path that names nothing is. A relative specifier with a `?`,
a `#` or a `%` (`./a.mjs?v=1`, `./%61.mjs`) is unresolved, for ESM and the bundlers read it as a URL and the compiler and Node's
require as a path, and so is an alias with one (`@/a.mjs?v=1`, Vite's `?worker` and `?raw`) when the part before it is mapped. A bare name that is a Node builtin (`util`,
`fs/promises`, but not `test`) is the builtin to Node whatever `baseUrl` or `paths` map it to, and a bundler may load the repo's
file instead (esbuild for the browser does, for `node:util` as well), so for either language it is unresolved when a config
maps it into the repo; `node:util` with no such file is the builtin. An absolute path (`/abs/x.mjs`), a `file:` URL (any case,
any spaces the URL parser drops), a `data:` URL (code written in the file, which may import a file of the repo by its `file:` URL)
and a specifier written with an escape sequence (not decoded: `'\x2fabs/x'` is `/abs/x`) are unresolved in JavaScript and
TypeScript alike, because where the repo is on a machine is not known. A bare alias with a trailing slash (`import 'lib/'`
under `baseUrl: "src"`) names the directory to the compiler and to Node's require (its index file) and the file of that name to
a bundler (esbuild), so it is unresolved when `lib.js`, `lib.json` or another file a resolver adds an extension to is there, and
keeps the index edge when only `lib/index.js` is. A call through `exports`, `module` or `require` followed by a member (`exports.f()`, `module.exports.f()`,
`require.main.exports.f()`, `new exports.C()`, `<exports.C />`, `process.mainModule.exports.f()`, `require.resolve(...)`)
reaches the modules' own objects (this file's exports, the loader, the entry module), which ripple does not follow, so in a
JavaScript or TypeScript file it is unresolved and never counted as outside the repo. Only a declaration of the name that gives
it a value of its own makes it that value (a parameter, `var`, `let`, `const`, `function`, `class` or an import, in a function or
block around the call or at the top of the file): a bare assignment (`exports = new K()`) does not, since the name holds the
module's own object until the assignment runs, a `var` at the top of the file does not for the same reason (in a CommonJS
file it redeclares the parameter Node runs the file with), and at the top of the file an `enum`, a `namespace` and
`import a = N.M` do not either (`import process = require('./x')` does, as `const process = require('./x')` does). Inside a function or block, what exists at run time decides: an `enum` and a `namespace` that holds a
value are bindings of their block (or of the function, at its top level), so the call is unresolved with the reason that the name is
declared there (a `const enum` is erased by tsc and kept by Node's transform and by esbuild, so that is not known either), while an
`interface`, a type alias, anything after `declare` and a namespace of types only exist in no form, hide nothing, and leave the call a
member of the module's own objects. A `var` belongs to the function (or the file) around its block, whatever else of that name is
declared in the block (an interface, a namespace, anything), and a function declared in a block is also taken for a name of the function, as in code that is not strict; a `let`, `const`,
`class`, `enum` and `namespace` stop at their block. `STATEMENT_KINDS` in `ripple/ts_indexer.py` states the scope of each kind of
statement, and `tests/test_js_module_names.py` runs each kind, in each position, in Node. A call of `require(...)` itself is a call of
the builtin loader, as before. The same objects spelled another way are unresolved too, never outside the repo:
`globalThis.process.mainModule.exports.f()`, `global.process.mainModule...`, and `process` (or `ns.default`, or `mainModule`) imported
from `process` or `node:process` reach the entry module. `globalThis.module`, `globalThis.exports` and `globalThis.require` do not
reach anything in a file Node runs (they are parameters of the file's wrapper function, so the call throws) unless code of the repo stores a module
object on the global object, so they are unresolved with a reason that says that. A `globalThis` or `global` that a declaration gives a
value of its own (a parameter, `let`, `const`, a top-level `var`, an import) is that value, and the call is read as written.
`process` that a repo module re-exports (`export { default } from 'process'`, `export { mainModule as pm } from 'process'`,
`export * from 'process'`) reaches the importing file with the chain intact: when the chain ends at Node's `process` or its
`mainModule` and the next step is `mainModule` or a member of it (`p.mainModule.exports.f()`, `pm.exports.f()`, which run the entry
module's `f`), the call is unresolved, with a reason that says so; another member of `process` (`p.cwd()`) stays outside the repo, and
a repo export that is merely called `mainModule` keeps its edge. `import a = N.M` binds
`a`, so a call through it, even when `a` is called `process`, is unresolved and never outside the repo. So does an `enum`, or a namespace
that holds a value, at the top of the file: `enum process {}` then `process.cwd()` is the repo's `process` (a namespace of types only and
anything after `declare` emit nothing, and the name stays Node's). `import x = require('./x')` is read as `import * as x from './x'`
is, a package stays a package, and `import process = require('process')` stays Node's `process`. It binds only at the top level of a
TypeScript file: in a `.js` file, a namespace, a block or a function body the language rejects it, and nothing is bound. An
`import globalThis = N.M` (or `global`) hides the global object, as a `var` of the name does. A class declared beside another
declaration of its name in the same block, function, switch or file (a second class, `let`, `const`, a function, a `var` written in it
or in a block of it, or a parameter) is a SyntaxError when Node runs it as JavaScript (tsc emits the same text, and reports most of
these as a duplicate identifier), so a call through that name is unresolved and has no edge to the class. A class beside an interface, or one that a namespace of the same name follows, is a legal merge and is read as before.

**The two languages do not follow each other.** A TypeScript file that imports a `.js` file (or the reverse, through a relative
path, `baseUrl` or `paths`) has an unresolved call with the reason "names src/x.js, a JavaScript file, whose calls ripple does
not follow from TypeScript": not a package, so not counted as code outside the repo, and not an edge. That holds for a file
that only the other language's index reads (a `.js` file in `dist/` is no file to either). An `export *` from such a file
leaves the barrel's export set unknown: a name the barrel does not declare itself is unresolved with that reason, never
taken from another `export *` (two stars that both provide it are an error to the compiler and fail to link at runtime).
Where both define an id (`x.ts` beside `x.js`), the TypeScript symbol is kept and the JavaScript one left out, with the
warning and exit status 3 described under "Both languages in one repo".

**Config.** A `jsconfig.json` is read like a `tsconfig.json` (`baseUrl`, `paths`, `extends`, `include`, and the JSX settings
under JSX), for a file of either language. tsserver tries a directory's `tsconfig.json` and then its `jsconfig.json`, so a
`jsconfig.json` is the project of every file the `tsconfig.json` beside it does not hold: a `.js` file, when the
`tsconfig.json` has no `allowJs`, or a file its `include` leaves out. A config governs a `.js` file only when it allows
JavaScript (`allowJs` or `checkJs`, and a `jsconfig.json` always does). A `.ts` file is the build's: the nearest
`tsconfig.json` that holds it governs it, however near a `jsconfig.json` is (`tsc` builds it with that `tsconfig.json`'s
`paths`, where tsserver would open it in the nearer `jsconfig.json`), and only a file no `tsconfig.json` holds goes to the
`jsconfig.json` above it that does, which is the project tsserver gives it. A wildcard `include` also drops a file when a
same-named file of a higher extension priority is in it (`m.js` beside `m.ts`, `p.jsx` beside `p.js`, `r.mjs` beside
`r.mts`): the compiler holds the other, so the dropped one is in no project (a name in `files` is always held). In
JavaScript `f < a > (b)` is two comparisons, so a `.js` call that carries type arguments is left unresolved (the same
text in a `.ts` file is a call), and so is a call inside or right after a syntax error: a name the grammar takes for a
TypeScript keyword (`abstract.foo()`, `using.foo()`) breaks its statement and the grammar then reads a call of `foo`. Flow
annotations in a `.js` file are read as the TypeScript annotations they look like, at the same trust: ripple checks
neither. mocha's `before` and `after` are known globals.

**Compiled output is not source.** A directory that a `tsconfig.json` or `jsconfig.json` names as `outDir` (through the
configs it extends and the ones it references in the repo, `${configDir}` meaning the directory of the config that extends
the base) is skipped at any depth in the TypeScript and JavaScript walks, and listed with the skipped directories, for the
compiler leaves it out of the program: `packages/core/dist/math.js` beside `packages/core/src/math.ts` would otherwise
define every function twice and make `find_symbol("clamp")` ambiguous. It is not skipped when it is, or is above, the
directory of the config that names it, its `rootDir`, or a root of its `include` or `files` (emit in place, or the sources
themselves), nor when it holds a `.ts` file that is no declaration (a compiler writes none), and the Python walk never skips
it (a Python package may live where a TypeScript config writes its output; the directory is still listed, as skipped for
TypeScript and JavaScript). The root `build` and `dist` are skipped as
before. Emitted code that no config names (a build run with `--outDir`, or emit in place beside the sources) is read, and
an id both languages define is the ambiguity warning described above.

### JSX

A component element in a `.tsx` file (`<Child />`) is not a call, so it makes two other kinds of edge, and they
answer different questions:

- **`renders`, the creator.** The function whose body writes `<Child />`: who is running when the element is made.
  The tag is read the way a call's callee is (block scopes, imports, barrels, a namespace import `<ui.Panel />`),
  so a local `Child` hides the import. An element written in an arrow, a `.map` callback or a render prop belongs to
  the named function around it, as a call does. Host tags (`<div />`, anything that starts with a lowercase
  letter, `<svg:rect />`) are never components and make no edge. A tag that is a variable of unknown value, or a
  member of `this`, is unresolved. A component from outside the repo is counted as outside the repo.
- **`renderer`, the component React was rendering when it ran Child.** For `<Layout><Child /></Layout>` written in
  `Page`, `Page` creates `Child` and `Layout` renders it. A `renderer` edge is reported only when the source proves
  it, and otherwise the element is listed as unresolved with the reason, so a missing renderer edge costs recall
  and never makes a wrong one. It is proven when
  - the element is part of what a component returns, through host elements, fragments (`<>`, React's `Fragment`,
    `Suspense`, `StrictMode`, `Profiler`), `cond && <X />`, `||`, `??`, conditionals, and the callback of a `.map`
    on a value known to be an array: that component is the renderer, and also the creator;
  - the element is a child of `<P>...</P>`, and `P` is a function component that puts its `children` prop (`children`,
    `props.children`, `{ children: kids }`) where it returns it the same way: `P` is the renderer, not the component
    that wrote the child. When `P` hands its `children` to another component `Q` that places them (`<Q>{children}</Q>`),
    `Q` is the renderer and `P` is not (`P` is the renderer of `Q`);
  - the element is what a function written as a prop returns, `<List render={(row) => <Item />} />`, and `List` calls
    that prop where it places the result (`{render(row)}`, `props.render(row)`): `List` is the renderer. A call in an
    event handler or an effect is not render time and proves nothing.

  A function is a component, so that what it returns is rendered inside it, when some element in the repo names it
  and nothing in the repo calls it as a function or hands it on by name as a prop (`render={Home}`). Not proven, so
  unresolved: the element passed to a function (`mount(<Page />)`), assigned or stored first, children given to a
  wrapper from outside the repo (`styled.ul`), a context consumer, a component that does not place the prop, a
  `.map` whose receiver is not known to be an array, and any file the parser had to recover from. The children of an
  element that is itself an argument of `cloneElement`, `createElement` or `Children.map` (and `forEach`, `toArray`,
  `only`) get no `renderer` edge, because those calls may replace them; the `renders` edges stay. A prop the
  component assigns anywhere in its body, in any order (`children = x`, `props = { ...props }`, `props.children = x`,
  `delete props.x`, `Object.assign(props, x)`, in a loop or in a function declared below), is not what the caller
  passed, so nothing the component does with it is proven.

  All of this is React's order of work: a component runs when React renders it, not when its element is made. A file
  whose JSX is compiled by another runtime (a `@jsx h` or `@jsxImportSource` pragma that is not React's, or a
  tsconfig `jsxFactory`, `jsxFragmentFactory` or `jsxImportSource` that is not) gets `renders` edges only, and its
  elements are unresolved with a reason that names the runtime. A bundler's own JSX setting outside tsconfig is not read.

Class components, `memo`, `forwardRef`, `lazy`, styled wrappers, `Context.Consumer` and `cloneElement` are not in
the benchmark fixture, which has function components only, so they are checked only by small inline snippets
(`tests/test_ts_jsx.py`), and implemented to resolve only what is provable. A class component's creator edges come
from its `render` method (`Box.render -> Child`) and the renderer is the class React instantiates (`Box -> Child`),
when it extends React's `Component` or `PureComponent` (imported as `Component`, `React.Component`, `* as React` or
under an alias). That is the class a tag names, so a class that inherits `render` from a base is the renderer, never the
base that defines it, and a class no element names, or whose `render` is called by hand (`this.render()`,
`super.render()`, `card.render()`), is not proven. `memo`, `forwardRef` and `lazy` are named by the variable the tag uses:
`const X = memo(f)` resolves to `f` when `f` is a function ripple can see (a function written inside the call is
named `X`), and `lazy(...)` is never resolved. A wrapper from outside the repo between a parent and its child leaves the
renderer unresolved, never the outer component.

Every query follows all kinds by default, and an entry with a JSX edge says which kinds it has and where each was
written (`edge_kinds`, and `sites_by_kind` in the JSON; in the text, `renders at a.tsx:3; renderer at a.tsx:3`). `ripple index` counts the JSX edges apart ("93 resolved calls, 21 JSX edges, ...") and only when there are some. A
`renderer` edge is placed at the line where the element is written, which can be in another file than its caller.
`impact` follows every kind: a function that writes `<Child />` is affected when `Child` changes (it can break what
it renders), and so is the component that renders it. `--kinds calls` gives the answer without JSX, and
`--kinds renders` the creators alone. Elements whose tag or renderer is unresolved are in the `unresolved` list of
`callees` (and in the count of unresolved calls) with a reason that starts "the JSX tag is not a component ripple can
name" or "the component that renders it is not proven"; `impact` does not list an unproven renderer as a possible
missed caller, since its creator is already a caller.

**Measured** on the fixture, against ground truth derived by hand from the source and checked against a React 19 run
of its tests (`python -m bench.ts_score`): `renders` 12 expected, 12 reported, precision 1.000, recall 1.000;
`renderer` 9 expected, 9 reported, precision 1.000, recall 1.000. The fixture has the cases where the two kinds
differ on purpose (children placed by a wrapper, a render prop). Read by hand against the source, with no runtime
recorder behind it, on four real libraries (react-pro-sidebar, react-hot-toast, react-accessible-accordion,
react-error-boundary): 69 `renderer` edges, grouped by the pattern each is written in, and well over 20 `renders` edges,
none found false; the edges ripple left out there are the cost of not guessing, such as children handed to a
component that spreads its props. That reading is evidence of the patterns it covers and nothing more.

**At runtime, on a library outside the fixture.** react-day-picker v10.0.2 (commit `1bfd4f0`): its tests, and a smoke
test that renders every example, ran under a recorder of which component React was rendering for each element.
Before the paths fix above, ripple stated 446 JSX edges there: 445 true and 1 false, and the
false one was that defect (an app that pins a published `react-day-picker` was resolved to the repo's own
`DayPicker`; the same defect gave false `calls` edges). In detail:

- `renderer`: 87 of 87 true. 80 were seen at runtime, and at each stated element line every runtime instance had
  ripple's component as its renderer; 7 are in the website app and were read by hand.
- `renders`: 358 of 359 pairs true. 277 were seen at runtime (every stated site observed) and 82 were read by hand.
  The fix removes two pairs, the false one and a true one (the app that depends on the package as `workspace:*`,
  which ripple now leaves unresolved). That is arithmetic on the judgments above, not a second run.
- Following `references` (the shadcn apps' `tsconfig.json` is `"files": []` plus references to their `tsconfig.app.json`)
  adds 68 edges after that fix, all inside `apps/shadcn-app-v9` and `apps/shadcn-app-workspace`, 34 in each (31
  `calls`, 2 `renders`, 1 `renderer`): an app's `@/components/ui/button` and `@/lib/utils` now resolve to its own
  files. None reaches `packages/react-day-picker`. They were read neither by hand nor at runtime, so they are not in
  the figures above.
- Recall in the tested scope (`packages`, `examples`, `test`): `renders` 277 of 314 pairs and `renderer` 80 of 163. The
  misses are mostly `<components.X />` tags, members of an object a caller can override, which ripple does not resolve.
- 28 synthetic React 18.3.1 cases of render props and of children handed to a second component produced no false
  edge: ripple stated 70 `renderer` edges and all 70 agreed with runtime. The cases were written by the person who
  measured, so they test the rules as stated, not shapes found in other code.

The limits: one library, and it had been read from source once before (a later commit, with no false edge found), so
it is not a library nobody had looked at; the runtime was React 19.2.5, which the recorder was not validated on (it
agreed at the element line everywhere it was checked); the website app was not run, so its 7 `renderer` and 76
`renders` edges are hand-judged. One false edge in 446 is 0.2 percent for this library and a weak guide to another
library's configuration, which is where the false edge came from.

**Both languages in one repo** are indexed each with its own profile and merged into one graph; the cache
keeps one entry per language (the Python one is named as it always was, so a Python repo's cache and output
are what they were), and an edit to one language rebuilds only that language's graph. Python ids are dotted
and TypeScript and JavaScript ids are paths, so nearly always no symbol or edge can be confused with the other language's.
But any id can be defined by both: a root `app.config.ts` is the dotted module `app.config`, the id of `def
config` in `app.py`, and `util.py` and `util.ts` are both `util`. For such an id ripple keeps the Python symbol
and its edges, leaves out the TypeScript symbol and every TypeScript call that names it (so no chain runs from
one language into the other through it), and no query answers for the id. The ids are counted and the first
few named in the warnings. A `.ts` file that is Qt Linguist XML or binary (a video) is not TypeScript, and
does not make a Python project mixed. `ripple index` prints each language's files read, with how many of them
could not be parsed in brackets (the same for both languages), and the unparsable files one per line (the
first 10, then a count).
When part of the repo is left out, every result says so: the CLI exits 3 and prints the warning on stderr (a
`warnings` list in `--json`), and each MCP tool result carries the same `warnings` list. That happens for ids
both languages define, and when TypeScript cannot be indexed: a TypeScript-only repo is then refused with the
install command and the reason, and a mixed repo is indexed as Python. The parser is "unavailable" whether it is
missing or installed but does not fit (an ABI or version mismatch), and the reason is in the message. A result
with no `warnings` key covers everything the repo has.

## Go

Go is read by tree-sitter as well (`pip install 'ripple-code[go]'`, which adds `tree-sitter` and `tree-sitter-go`; without
it a Go-only repo is refused and a mixed repo is answered without its Go files, with a warning). `--lang go` or `auto` (`.go`).
It has a resolver of its own (`ripple/go_indexer.py`), because Go has no overriding and a call `T(x)` is a conversion.

**Symbols** are import paths: `github.com/spf13/cobra:Command.Execute` (package, colon, `Type.Method`; value and pointer
receivers are one method set), a package is `github.com/spf13/cobra`, and every file of a directory is that one package. An
external test package is `github.com/spf13/cobra_test`, a second declaration of a name under build constraints is `#2`. The
id is found by `Command.Execute`, `cobra:Command.Execute`, `command.go:Command.Execute`, or a directory (`doc/`).

**Which module governs an import.** The MAIN module of a build decides what an import path names: its `replace` and `exclude`
directives apply to the whole build, and the `replace` directives of every other module are ignored (so a program that imports
a module `a` whose go.mod replaces `other` with a checkout still gets the published `other`, unless its own module replaces it
too). go.work says which modules are the main modules (a workspace applies the replaces of every member, its own first, and a
module two members replace differently is refused). A module of the repo that is neither replaced nor in the workspace is built
from its published version, so it counts as outside. A `replace M v1.2.0 => ./b` applies only when the build selects exactly
v1.2.0: ripple reads that from the main module's `require` line when it states `go 1.17` or later (go lists the selected version
there, and refuses "updates to go.mod needed" when another is selected) unless the version is `exclude`d; under an older go
directive, in a workspace, or with two requires, a versioned replace is not known to apply and the import is unresolved, never
external and never the checkout. A main module with a committed `vendor/modules.txt` builds from `vendor/` by default, so its
imports of other modules are unresolved too. go.mod is read by its grammar (blocks, quotes, comments, `// indirect`, `retract`).
A call edge is resolved by the module its file is in, and drawn only when every module whose build contains that file (itself,
and each module that replaces it or shares its go.work) names the same package for the import, so a file that one build links
against the checkout and another against a published version has no callee. For an `implements` edge each program and test
binary is resolved under its own root's module, closure and all, and a library call, which has no single main module, is held to
every unit separately: one unit's module is enough, because each unit is a real build.

**Proven**: a function called by name or through an import (go.mod, `replace` to a local directory and go.work say which
directory an import names, by the rule above), and a method called on a value whose type is written in the source (receiver, parameter,
`x := T{}`, `NewT()`, a field, an element, a range variable, a type assertion, embedded structs by the language's depth
rule; a method declared on an alias is its target's, and keeps the alias's name). **Unresolved, each with its reason**: an
interface method call (the reason names the interface; it may also get `implements` edges, below), function values (a parameter, a local, a field, a package variable),
type parameters, a value whose type is not written, anything that depends on which of several build-constrained definitions
wins. Build constraints are never evaluated: a function defined under several has an edge to each variant, and a variable, a
type, a result that the variants read differently, a method that only some builds declare (a `//go:build` line, a `_linux.go`
name, a `_test.go` file) while another is promoted, or a method declared on an alias that is itself declared twice, is
unresolved. A `_test.go` method counts only for a call in a `_test.go` file of the same directory (its package and its
external `_test` package); a call in another directory's tests, whose binary links the package without its test files, runs
the promoted method.

**Interface implementers, the `implements` edge.** A call `v.M()` through a repo interface stays unresolved, and also gets an
edge of a kind of its own, `implements`, to `T.M` for each repo type `T` that provably satisfies the whole interface (`*T`'s
method set, the largest: a `T` or a `*T` is what an interface can hold). It is never a `call` edge and never joins one: the
answers list it under `may_be_called_through_interface` (callers), `may_be_reached_through_interface` (impact) and
`may_dispatch_to` (callees), in the words "may" and "possible", with a note that the list is **never complete**: a type from
outside the repo, a type declared in a function, or any type ripple cannot prove can satisfy the interface too, and the call
is still listed as unresolved. Each edge claims one thing, that the method belongs to a repo type that satisfies the
interface in a build that contains the call, and ripple leaves a candidate out unless the source proves it by the spec: the
interface and what it embeds are declared once, read whole, with no type parameters and nothing from outside the repo; every
parameter and result of each method is one canonical type on both sides (aliases resolved, `byte` is `uint8`; a function
type, a generic instance, a name a dot import binds, an array length that is not an integer or rune literal in Go's own
grammar (`[010]` is 8, `[0x10]` is 16) and an import of an outside package that is not the standard library and has no alias
are refused; channel directions are read as Go reads them, so `chan<- chan int` and `chan (<-chan int)` are two types); the
type is declared once, is not generic, is not a defined pointer type (`type P *S` has no methods, not even S's) and is in no
build-constrained or cgo file; each method is found by the depth rule with no outside embedded type that could provide it
first, is declared once in every build, and an unexported name belongs to the interface's own package.

*Which builds contain the call* is one rule, for the three places a call can be. A program links exactly its import closure,
and a test binary links its package with the `_test.go` files, its external test package and what those import, never another
package's test files. The closure counts only what every build of a package imports: a file under a build constraint, a
platform file name, or a file that imports `"C"` (CGO off leaves it out) adds its imports to the builds that have it and to no
other, so none of them is counted; a blank import links its package. A call in a `_test.go` file (with or without a
constraint) is in its directory's test binary and in no other build, so a type is a candidate only when that binary links its
package, and a `_test.go` type only for a call in the same directory's tests. A call in a package main is in that program and
in its directory's test binary and nowhere else (nobody imports a main package), so only a type those two link is a candidate.
A call in any other package is in every program and test binary that links it, and in a program of another repository that
imports it, so the claim is that some build has both: a type is a candidate when a program or test binary of the repo links
both packages (a type of a package main is in its program and its test binary alone, so that is its only way in), or when both
packages can be imported from outside (neither is a main package or has an `internal` element in its import path) and one
module of the repo builds against both modules, which is the rule an import across modules already follows (the same module,
or a `replace` or a `go.work` joins them). A module of the repo that is neither replaced nor in the workspace is built from its
published version, which may differ from the checkout, so none of its types is a candidate for a call of a module that does
not build against it. Where no build can be shown to hold both (an `internal` package nothing links with the call's package),
the candidate is left out.

`bench/go_oracle` (`-implementers`) writes what `types.Implements` and the method sets say for each interface call, **per
build unit** and never as a union of programs: the universe of a call in a package main is that program's closure, of a call
in a test file that test binary's, and of a call in a library the packages some unit links with it plus the packages that a
program outside could import with it (the modules the go tool's own build list says the run builds against are loaded with it).
A candidate is agreed when some build has it and no build that has its package lacks it. `tests/test_go_implements.py` holds
ripple to that and to the `// want:` comment on each call of the `impl_*` probes, and `tests/test_go_build_units.py` fails if
the oracle's answer for a call in a package main lists a type of a package that program does not link. Run against that
oracle on linux, windows and darwin: gin 175 edges (all 175 confirmed, 27 of the 87 interface calls ripple recognises have
one, 175 of the 238 candidates the compiler agrees on), etcd as one go.work run 6,689 edges (all 6,711 site and method pairs
confirmed, 3,704 of 6,059 calls, 6,711 of 11,356 candidates; the rest are left out for an interface or method signature ripple
does not follow, 3,024, a test binary or program whose imports are not provable, 1,399, a signature, 124, a method the depth
rule cannot place, 98; a second run with `-tags cluster_proxy` confirms the 5 edges of the one file the first run does not
build), cobra none, and none unconfirmed on bubbletea, chi, fzf, lo, testify, urfave-cli, viper or zap. The version before
this rule drew 6,708 etcd edges: 19 of them were a call in a package main given the type of a program it does not link, 12
contradicted by this oracle and 7 in files the go.work run does not build (the old oracle merged every program into one
build, so it confirmed such edges, and a probe wrote them into its `// want:`). Call edges and the externals are unchanged:
with the `implements` edges taken out the graphs of gin, cobra, the Python warehouse fixture and the TypeScript warehouse
fixture equal what the version before gave, and no call edge of etcd, bubbletea, chi, fzf, lo, testify, urfave-cli, viper or zap
moved with the `// +build` reading below.

**A call in a constrained file** (a platform file name or a build constraint, not a plain `_test.go`) is only in the builds that
have the file, so the type's package must build in one of them: for some GOOS/GOARCH pair (and the custom tags the call file's
header names) the package and every repo package it imports there must have a non-test file in that build that does not import
`"C"`, and no import may be unresolved; otherwise the candidate is left out. The compiler cannot answer this by a probe (the
oracle stops at the package error), so `tests/test_go_platform_package.py` holds it.

**Build constraints.** A file's constraint is read as `go/build` reads it, and a committed table proves it: `bench/go_oracle/go_build_table.py`
writes `go_build_table.json`, the verdict of `go list` (Go 1.27.4) for 70 header forms against 36 file-name forms on twelve
GOOS/GOARCH/tag runs (a byte order mark, no-break space, vertical tab, a second dot in `x_linux.pb.go`, an old-style line of more
than 100 operators, `//go:build` after the package clause, ...), and `tests/test_go_build_table.py` (no Go needed) holds ripple's
port of `parseFileHeader`, `shouldBuild`, `constraint.Parse`, `goodOSArchFile` and `matchTag` to every cell. A `//go:build` line decides alone; otherwise every
old-style `// +build` line must hold (spaces are or, commas and, `!` not; a term that is not a tag, `!!x` or an empty line is
the tag `ignore`, which no build has), and a line counts only when it is above the last blank line of the file's leading
comment stretch, so one with no blank line after it (package documentation), one after a block comment, or one after the
package clause is no constraint. The file-name suffixes `_GOOS`, `_GOARCH`, `_GOOS_GOARCH` and `_test`, and the files the go
tool ignores (a name that starts with `_` or `.`, a `testdata` or `vendor` directory, a file no build includes), are read as
before. This also fixes a call edge: a method declared in a `// +build linux` file while another is promoted is now
unresolved (it was the edge to the linux one, wrong on every other platform), as a `//go:build` one always was.

**External** (a call that provably cannot reach repo code, which an agent may skip) is only what the Go standard library's own
source proves: a func declaration of a standard library package, a method of a standard library type that is not an interface,
a method of a standard library interface that no repo type declares, a builtin. Ripple reads no package outside the repo, so a
member of one it cannot prove is unresolved, not external: `pkg.F(x)` of any other outside package (a function, or a variable
holding one), `flag.Usage()` (a variable), `tp.DialContext(...)` or `cmd.RunE(...)` (a field of function type, which can hold a
repo function), a value whose type ripple cannot write. The proof is `ripple/go_stdlib.py`, generated from GOROOT by
`bench/go_stdlib/generate.py` over every platform `go tool dist list` names. A `C.f(x)` call is unresolved as well: C code may
call back into an `//export`-ed function of the repo. On cobra, gin, zap, chi and etcd the external count fell by 286, 2617,
814, 272 and 12419 and the unresolved count rose by 286, 2598, 808, 109 and 12274 (the rest are conversions such as
`http.HandlerFunc(f)`, which are no call and are no longer counted); no edge changed.

`vendor`, `testdata` and `_x` directories (wherever a go.mod sits: a module fixture under `testdata` is not read) and files
no build includes (`//go:build ignore`) are skipped as the go tool skips them, and `ripple index` lists the directories
(`.x` ones, which no language reads, are left out like the other languages'); generated files are read, because the rest of
the package calls into them. A file tree-sitter-go 0.25 cannot parse (Go 1.26 `new(expr)`) is listed. If the broken code is
inside a function body whose braces, read with Go's lexical rules, close it at its last byte, only that function's calls are
unresolved. If it is anywhere else, tree-sitter loses the declarations after it, so the whole package is treated as having
unread declarations, whatever the layout or comments around them: a name or method is still proven when a declaration of
it is built in every build (no constraint, no platform file name), because a second one would not compile, but a name or
method declared only in constrained files, a promoted method, a missing member or a universe name (`len`) in that package
(or in a package that imports it) is unresolved with a reason naming the file, and a call on an outside type is not counted
external when the file's text declares a method of that name.

**`check` for Go** runs `go test -json -count=1 -skip= -list= -c=false -fuzz= -run=` (`-count=1`: a verdict of a run made
now, never a cached one; the rest is explained under GOFLAGS below, and `-skip` needs Go 1.20 or later) and reads the
verdict from the event stream (`ripple/go_check.py`), never from the exit code alone or from the log text. With no
arguments it runs `./...` in every module of the repo (a nested `go.mod` is its own run, since the parent's `./...`
leaves it out; `vendor`, `testdata` and `.x`/`_x` directories are skipped as Go skips them). `go` must be on `PATH`
(otherwise a refusal that names `--test-command`). The arguments:

- a directory (`./dir/...` in the module that holds it, when that module has a package below the directory outside the nested
  modules, and `./...` in every module below it: a directory of nested modules is only their runs, since `go test` fails on
  a pattern that matches no package. That holder run is also planned when the directory has only `.go` files Go leaves out
  of the build (a `//go:build ignore` generator next to the nested modules), where `go test` says `matched no packages` and
  exits 1 with no event: that run is an empty run, not a failure, but only when a nested module below the directory ran
  packages of its own. With nothing below it having run, the message stays a problem), a `_test.go` file (the tests it declares that `go test` runs: an Example with no
  `// Output:` or `// Unordered output:` comment is compiled and never run, so it is left out, and refused by name), or
  `file_test.go::TestName` and `file_test.go::TestName/sub/case` (a space in a subtest's name is written `_`, as Go names it);
- the id `impact` lists for a test function, `file_test.go::TestName` (see "Go tests in `impact`" below). `check` also takes
  the symbol id of a test, `example.com/mod/pkg:TestName` (`.../pkg_test:TestName` in an external test package), which an
  older `impact` listed among the callers, and finds the file that declares it.

Ids become `go test -run '^(TestA|TestB)$' <package dirs>` (with the flags above), run in the module that holds the package, with the pattern built
per package: a test name two packages share runs only in the package it was asked for in, and subtests are levels of
the pattern (regexp-quoted), one pattern per parent with only the last level alternated (`TestGrid/x/1` and
`TestGrid/y/2` are `^TestGrid$/^x$/^(1)$` and `^TestGrid$/^y$/^(2)$`, two runs, never `(x|y)/(1|2)`, which would also run
`x/2` and `y/1`), so no run is broader or narrower than the ids. An id for a test the file does not declare is refused
before anything runs. Nothing a caller supplies is read from outside the repo: paths and symbol ids are resolved and
contained before any file is listed or read, and a `go.mod` or `_test.go` that links out is not read.

What is never reported as passed (each has a stream captured from the real toolchain in `tests/fixtures/go_check/`, with
the module that made it and the command): a build or vet failure, a package with no test files when nothing else ran, a
`-run` pattern that matches nothing (Go exits 0 and says "no tests to run"), a panic or Go's own timeout, a test that
started and never finished, a test binary that exits early or is killed (`syscall.Exit(0)` in a test exits 0 and the package
never reports at all), a requested test or subtest with no result of its own (the parent passing does not count) and a
requested test that was skipped. Whatever `go test` exits with, a failing event is a failure, and an exit status with no
failing event is reported too. The result has the usual keys plus `problems` (why a run is not a clean pass),
`counts` (top-level tests, subtests and packages apart) and `notes`; failures read `FAILED file_test.go::TestName: first
message or panic line`, and a failed subtest is listed after its parent. A package that reports ok has to show that its
tests ran to the end or that it had none: a test binary that ran them prints `PASS`, and one that exited 0 before `m.Run`,
from a `TestMain` or an `init` that calls `os.Exit(0)`, prints only the ok line, a problem in every run shape (with other
packages, as a directory argument or alone). A package with no test event needs Go's own word that there was nothing to run,
`testing: warning: no tests to run` or the `[no tests to run]` suffix on its ok line (or `[no test files]`): benchmarks
only, helpers only, output-less examples only and a build-excluded test file print it and pass (a note when other tests
ran, "no tests ran" when none did), while a `TestMain` that prints `PASS` itself and exits 0 prints no such notice and is
a problem. A directory argument none of whose packages ran a test (no test files, or all skipped) is "no test ran there".
A benchmark has a run event and never a pass or fail one, so it is neither counted nor reported (a benchmark that fails is
a failure). When a request splits into several runs (two subtests of one test with different parents), each package, test
and subtest is counted and listed once, a test the runs disagree about is the worse of the two (a parent that passes in
one run and fails in another is a failure), and a problem two runs share is said once. An argument over a file system's
limits (a name over 255 bytes, a path over 4096, a NUL, a string that does not encode) is a refusal, not an exception.

`GOFLAGS` reaches every `go test`, from the environment or from the go env file (`go env -w GOFLAGS=...`), in spellings
ripple does not try to read (`-run`, `-test.run`, quoted, `--run`). So it does not read them: the flags that decide which
tests run, or whether any does, are on the command line, which wins over GOFLAGS for every spelling. `-skip=` leaves no
test out, `-list=` and `-c=false` stop `go test` from only listing or only compiling, `-fuzz=` from fuzzing in place of the
tests, and `-run=` (a run with no pattern of its own, where an id gives its own `-run`) runs everything, examples with an
output comment included. They change nothing in an ordinary run (the event streams are the same, checked live against a
run without them). What stays yours is your build configuration: `-tags`, `-short`, `-vet`, `-shuffle`, `-failfast`,
`-timeout`, `-race`, `-bench`, `-o`, `-json` and `-exec`, which can leave a test out (a `//go:build !foo` file under
`-tags=foo`, a test that returns under `-short`) the way a `go test ./...` by hand would. They are named in a note when
`go env GOFLAGS` (asked for within the run's timeout, and no note when it fails or hangs) says anything. `-exec` is your
runner: one that never runs the test binary leaves a package with no events, which is not a pass. With `-bench`, Go prints
no "no tests to run" notice, so a helpers-only package is reported as a problem there, the same as a `TestMain` that
prints `PASS` (a false failure under an unusual GOFLAGS, never a false pass).
`--test-command` still means exactly that command, read the generic way. `check` picks the Go runner from the profile
(`--lang go`, or a repo whose only language is Go: a stray Python or TypeScript file makes `auto` refuse
and name the languages, so pass `--lang go` there). A second language's runner goes in `checker.RUNNERS`
(profile name to a module with `normalize` and `run`), so that adding one leaves the profile, whose fields are hashed into
the cache key, alone.
Every stream was captured with Go 1.27.4; no older Go was run, so its event shapes are unverified (a package that fails with no
failing test and no build event is reported as such, never as a pass).

**Go tests in `impact`.** A Go test that reaches the symbol is listed under `tests` as `file_test.go::TestName` (the
file is the repo-relative path, `a/a_test.go::TestSame`), the form `check` takes first, so the ids go straight into
`check`: `ripple check --lang go a/a_test.go::TestSame`. The ids follow the go command's own selection rules (checked
against `go test -list`, `tests/test_go_impact_tests.py`): `TestXxx(t *testing.T)`, `FuzzXxx(f *testing.F)` (plain `go test`
runs only its seeds, so it is listed only when the target has an `f.Add(...)` statement before `f.Fuzz`) and `ExampleXxx()` whose last comment is `// Output:` or `// Unordered output:`, in a `_test.go` file
(an external test package, `package m_test`, included: it is built into the same test binary), where the name after the
prefix does not start with a lowercase letter. `TestMain(m *testing.M)` is not a test. A `t.Run` subtest is not collected on
its own; a change reached only through one lists the parent test, whose closure holds the call. A helper that takes
`*testing.T` but is not named like a test is a caller, as before. What `impact` gives no id, it says: `tests_not_listed`
holds each caller that looks like a test, with the reason, and it stays among the callers too. The rule behind it: every
caller `impact` reaches that runs in a test binary without being called by a listed test is named. So it also holds:

- `TestMain`, an `init` and a package variable's initializer in a `_test.go` file, which run before every test of the
  directory's binary (the package and its external test package): one entry names the directory, and `check` takes the directory
  (with the symbol broken, go fails the directory's tests, checked on a module for each of the three);
- an `init` or a package variable's initializer in any other file, which runs in every binary that links the package: one entry
  says so, and does not list those tests;
- a method named like a test in a `_test.go` file (a suite runs it by reflection), and any other symbol of a `_test.go` file that no
  listed test is known to call within the depth asked.

A call through a function value (`f := pkg.Fn; f(1)`) is not an edge, so a test that does that is not reached at all and is
not named: this is the call graph's recall gap, the same as for any other caller. The cases with no id:

- a benchmark (`go test` runs it only with `-bench`, and `check` does not take one);
- an example with no output comment (compiled, never run), or whose output comment `check` cannot read (`/* */`, or after code on its line);
- a test in a file only some builds have (a `//go:build` line, `x_linux_test.go`): which of its tests your `go test` runs is not known;
- every test of a directory where `go test` fails the package: a `Test`, `Benchmark` or `Fuzz` function with a signature go
  refuses, a malformed name (`Testfoo(t *testing.T)`) or an `// Output:` that is not the last comment, which go vet fails;
- a fuzz target with no visible seed (without `-fuzz` go runs no input, so the fuzz function never executes and a pass proves
  nothing; `testdata/fuzz` is not read, since the cache does not fingerprint it);
- a name declared more than once in the directory, in any `_test.go` file whatever its constraint (`go test -run` runs each);
- a directory with no `go.mod` above it, with a file ripple could not read completely, or a test file that imports `"C"`.

Not modelled: what go vet makes of an example's name (an `ExampleFoo` where `Foo` does not exist fails vet, and so
`check`, with go's own message), and which tags or platform your build has (a file with any constraint is left out, never
guessed). An unconstrained test file is assumed to build, as the rest of the package does. The ids live in the graph
(`Graph.test_ids`, decided where the files are read), so the cache moved with this change.

`bench/fixtures/go/probes` holds 157 small legal Go modules, each with what the Go compiler's type checker says every call
site calls on linux, windows and darwin (`bench/go_oracle/generate.py`, run by hand with Go installed), plus the cgo ones that
state their own answer. `tests/test_go_probes.py` needs no Go: an edge ripple makes must be the compiler's callee, declared in
the file ripple edged to, on every platform that builds the file, and a call it counts external must have no callee inside
the probe and must not be a function value, each checked at the call's own position (so two calls on one line are each held
to their own answer). `tests/test_go_layout.py` writes every probe and golden fixture again with comments after every token,
with the indentation stripped or doubled and with CRLF line ends, and requires the same graph.

## Rust

Rust is read by tree-sitter too, as an optional extra that adds `tree-sitter`, `tree-sitter-rust` (and `tomli` before
Python 3.11, to read Cargo.toml). The base install is unchanged, and a repo of any other language is read without it
(a Rust-only repo is refused with the install command; a mixed repo is answered without its Rust files, with a warning).

```sh
uv tool install "ripple-code[rust] @ git+https://github.com/xtelos/ripple"   # or pip install 'ripple-code[rust]'
```

`--lang rust` (or `auto`, which finds `.rs` files) reads the repo. The crate graph comes from Cargo.toml and the
source: every package and the crates it builds (the library, binaries, tests, examples, benches and `build.rs`, with
`[lib]` and `[[bin]]` path overrides), the dependencies each can name (path dependencies, renamed ones, hyphens as
underscores, a library under its own name), and the `mod` tree from each crate root (`mod x;`, `#[path]`, inline
modules, `mod.rs`). A `.rs` file no `mod` reaches and a `mod` that names no file are listed as not read (a note of
their own, apart from the files ripple could not parse), and so is a Cargo.toml that cannot be read; one with a field of
the wrong type costs its package, and whatever depends on it finds its library unread, never the rest of the repo.
A `target` directory beside a Cargo.toml is not walked.

**Symbols** are the file's path without `.rs` (`x/mod.rs` is `x`; `lib.rs` and `main.rs` are not folded), a colon and
the member joined by `::`: `src/cli:parse_args`, `src/x:Foo::new`, `src/lib:tests::it_works`, `src/x:Tr::m` (a trait's
declaration), `src/x:<Foo as Display>::fmt` (a trait impl: its file is the impl block's, not the type's). A second
definition of one id (two `fn imp` under different cfgs) gets `#2`. Any of these finds a symbol when it names one:
the id, the file with or without `.rs`, `Foo::new`, or the bare `new`.

**Resolved:** a call by path: `f()`, `m::f()`, `crate::`, `self::`, `super::`, `Type::f()`, `Self::f()`, `Trait::f(x)`
(the declaration, with each impl in the repo as an `override` edge) and `<T as Trait>::f()`, through `use` lists,
aliases, globs (only the names visible to the importing module) and `pub use` re-exports, and the arguments of the
standard macros (`println!`, `format!`, `vec!`, `assert_eq!`, ...), which are parsed again as expressions. A constructor
(`Some(x)`, `Pair(1, 2)`) runs no code and is neither an edge nor unresolved. `Type::f()` is an edge for an inherent
function of a repo type, and for a function of a repo trait that has one impl for the type and no blanket impl; a trait
from outside the repo is never one (the standard library may implement it for the type in ways ripple does not read).
A method call `recv.m()` is an edge in one case only, the first step of rustc's method probe: the receiver is `self`, or a name
bound once in the function by a parameter or a `let` that writes its type as a struct, enum or union of the repo (`S`, `&S`,
`&mut S`, `Self`, through an alias or a renamed import too), the call is where that `let` is in effect (after it, in its block), and an
inherent method `m` of that type takes the receiver in the form the receiver has: `self` for an `S`, `&self` for a `&S`, `&mut self`
for a `&mut S`. rustc tries a type by value before it takes a reference and an inherent method before a trait's, so that method is found
first, whatever traits are in scope and whatever other types the receiver derefs to. The type arguments written must select the one impl
(`impl W<u8>` and `impl W<u16>` are told apart; a generic receiver `W<T>` selects only an impl for every `T`). A type name in that proof is
resolved where it is written, never compared as text: arguments are compared only when each is built of primitive types (`u8`, `(u8, bool)`,
`[u8; 4]`, `&u8`) and every primitive name, looked up in the scope of the impl and of the call, is the primitive (a struct, an enum, an
alias, an import, a glob, a generic parameter or a macro that may make a name called `u8` there makes `W<u8>` another type; probe
`method_type_names`, compiled and run, for a method call and for a path call `W::<u8>::m(w)`, which had the same defect). A prelude type
(`Vec<u8>`, `Option<u8>`, `String`), a std path, `Self` and every other type are never compared: the scope may have replaced them. An impl ripple did not see
is not a reason to refuse: rustc rejects a second inherent method of one name for a type (E0592) whatever its receiver form, and takes
the inherent method before every trait's and every `Deref`'s at this step, so an impl a macro made cannot change which function the call
finds (compiled cases in `bench/fixtures/rust-probes/crates/method_macros`, `method_rebind_*`). What can change it is source ripple did not
read, which rebinds the receiver or the type name where no rule of a scope sees it (a textual `macro_rules! println` in an unread
`#[macro_use]` module makes `println!(s);` bind `s`: probe `method_unread_macro_use`), so a crate (or one that can name the type) is
refused wherever part of its source is unread: a `mod` that names no file, a file of the crate's source directory that no `mod` reaches,
a `#[path]` or a `cfg_attr(.., path = ..)` ripple does not follow (the latter whether or not a default file exists), modules nested beyond
what ripple follows, a `mod` written in a block, a file the parser could not read whole, `include!` (a build script's generated file too), and
a macro invoked where an item may stand that may expand to `mod x;` or `include!(..)` (probes: both build at the module level; neither in an
impl or a trait body, nor in the statements of a block, where a `mod` needs a `#[path]` and is a block module, nor in an expression): an attribute
macro or a derive that is not a standard one or a trusted one on an item of a module, and an invocation of a macro that is not a standard one
unless it is a `macro_rules!` of the repo that provably brings none. That proof is for two forms only: a bare name that a `macro_rules!` written
earlier in the same module reaches (not itself under an attribute that may remove it), or a path from `crate`, `self` or `super` where no
`#[macro_use] extern crate` and no glob from outside the repo may bring another macro of the name. The name is the repo's alone (no procedural
macro, and a `use` that gives it is the macro's own path), the body and the invocation's tokens have no `mod`, `include` or `macro_rules` token,
and every macro the body invokes is named so that one macro is meant: `std::`, `core::` or `alloc::` and a macro of a short written list
(`println!`, `format!`, `concat!`, `stringify!`, `vec!`, ...), or the same bare where no `use` of the crate gives the name (`use std::include as concat;`
makes `concat!` bring a file: probe `method_unread_alias_std`; `pub(crate) use declare as stringify;` is the same with a repo macro:
`method_unread_alias_repo`) and no `#[macro_use] extern crate` may, or a bare macro of the repo whose `macro_rules!` is in textual scope at the
invocation and is proven the same way (one defined in another module may be another macro there: `method_unread_nested_ext`). A path through
`$crate`, `crate`, `self`, `super` or another crate inside a body is not followed. A macro invoked in a block, an impl or a
trait body, or in expression position, costs nothing crate-wide: its reach is the block (rustc reports a `macro_rules!` that a macro made and
that redefines a standard macro as ambiguous, E0659), and the receiver rules below see it. The receiver name and the type name are
trusted only where a rule of the scope says nothing rebinds them, with no crate-wide refusal behind it (the tests run every probe with it off):
a macro invoked as a statement in a block around the call (or in a pattern: `let bind!() = x;`, an arm, an `if let`, a `for`, a closure parameter), a
name bound twice, and anything in a block around the call that may give the name another meaning: an item of the name in the value namespace (a
fn, a const, a static, a unit or tuple struct, a foreign item), an import that gives the name (`use m::s;`, `use m::t as s;`, in a list), any
glob import (it may give any name), a macro that may have made the name, or a statement of a kind with no rule (the allow-list is
`BLOCK_DECLARES`, `BLOCK_IMPORTS`, `BLOCK_TYPE_ONLY` and `BLOCK_NO_NAMES`: a type, trait, impl, macro or `extern crate` item, a `let`, an expression
statement and a comment cannot give a value name; probe `method_block_names`, each compiled and run), a glob or a
macro that may have made the type's name, an attribute macro on the function, the impl or the module, and the standard macros' own names
(`println!` and the rest are the standard ones only where no `macro_rules!` of the repo, no `use` and no `#[macro_use] extern crate` gives the name,
and no block holds a `#[macro_use] mod`; a glob that gives one is ambiguous with the prelude's, E0659). And only when nothing
the proof reads is conditional or hidden. Conditional compilation anywhere between the crate root and the thing read refuses the call (but for
a cfg the call is under too, below): the read
things are the method (with its parameters, generics and body's inner attributes), its impl (its generics, type and inner attributes, and every
function, impl, trait, block, match arm, statement, array, tuple or call element, struct-expression field and inline or file module around it),
the receiver type's generics and where clause, the receiver's own `let` or parameter (`#[cfg(..)] let s: &S`), and an argument of the call. An
attribute is read as harmless only if it is on a written list (`INERT_ATTRS`: `allow`, `warn`, `deny`, `forbid`, `expect`, `doc`, `must_use`,
`inline`, `cold`, `deprecated`, `track_caller`, `repr`, `non_exhaustive`, `automatically_derived`, `derive`, tool attributes such as `rustfmt::skip`, and
plainly `path`, `macro_use`, `macro_export`); a `cfg_attr` is harmless if every attribute it adds is. Every other attribute (`cfg`, `test`, an
attribute macro, a `cfg_attr` that adds one of those, `cfg_attr(.., path = ..)`) is conditional, in any position the grammar gives one (outer or
inner; `ATTRIBUTE_PARENTS`; a position it does not parse is a file the crate refuses whole). A `cfg` on the call's own statement, block or
function is no reason (an edge says "if this call exists, it calls that"). Probes `method_cfg_places` and `method_cfg_bindings` build every
position. This is the same reading for path calls: an inherent function the impl, a function body, an arm or a module file removes with an inner
`#![cfg(..)]` (`impl S { #![cfg(any())] fn m }`, which main drew an edge to, wrongly) or whose impl is inside any conditional node is
no longer chosen over a trait's function that would stand in its place, unless the call is under that cfg too (next paragraph). Not private to another module, not in an impl with bounds (`impl<T: Bound> W<T>`,
a where clause, `?Sized`), which a receiver may not meet. A method call is never an `override` edge and never counted as outside the repo.
A cfg that governs both the call and the declaration does not make the declaration conditional for that call (method calls and path calls alike,
`Index.shares_cfg`): an edge is drawn only if every conditional attribute on the path from the crate root to the declaration (the method, its
impl, the type, and everything enclosing them: modules, a `mod x;` declaration's attributes for a file module, a file's own `#![cfg(..)]`, functions,
blocks, arms) is also on the path from the crate root to the call, as the same attribute on the same syntactic ancestor in the same crate (an
attribute is its crate, file and byte). The call then exists only if the declaration does, and the program is read as if those attributes were not
there; a second definition under another cfg is still counted and refuses (`SEVERAL`), and one the tool does not see is a duplicate rustc rejects
(E0592) wherever both exist. An attribute on the impl or the method when the call is outside it, on a generic parameter of the impl, on the
receiver's own `let` or parameter (never an ancestor of the call), or in another crate's copy of a file is not shared; nor is an attribute that does
more than make its host exist or not (`head_shared`: `cfg`, and a `cfg_attr` of those and inert attributes; `test`, `path`, `no_implicit_prelude` and
attribute macros are not).
A path call (`Self::f()`, `<Self>::f()`, `T::f()`) into an inherent function that is not shared in that way is never an edge (`CFG_UNKNOWN`),
as for a method call. Where the function is not built, the call finds whatever else gives the name, and ripple cannot close that set: an impl of the
type under the opposite cfg (for `W<A>` with `type A = u8`, or an impl for every `T`, or a call that writes no type arguments, ripple cannot say
which impl applies), a repo trait (a blanket impl too), a provided method of a standard or foreign trait the type implements (`Iterator::count`,
`PartialEq::ne`), the reflexive `From` and `TryFrom`, a foreign crate's extension trait. Where nothing else gives the name the crate does not build
with the function off, so no edge is lost that a build with the call has; on ripgrep, clap, serde, mini-redis, fd and bat no site changes. Before,
the edge was drawn when no trait impl written for the type gave the name, which is wrong for each of those (probes `path_self_cfg` groups A and D
and `path_self_cfg_ext`, compiled and run under each feature setting; their oracle is rust-analyzer's). The call and the function under one
predicate, and an unconditional function, stay edges (group B).
The same rule holds for a path call into a trait impl (`assoc_traits`): the impl, or a method written in it, under a `#[cfg]` the call is not also under
is never an edge (`CFG_UNKNOWN`). Where the impl is off the call finds another giver of the name, a foreign extension trait for one: compiled and run, a
`#[cfg(feature = "a")] impl Tr for S` called as `S::g()` runs the impl with `a` on and `extblanket::Ext::g` (imported under `not(feature = "a")`) with it off.
An impl with no cfg, and one the call shares its cfg with, keep the edge. The same holds for the trait's own default method that the impl does not
override: a `#[cfg(feature = "a")]` on `fn g` in the trait, called as `S::g()` where the impl writes no `g`, is no edge (cargo: `ext-blanket` with `a` off,
the default with it on), and is one for a call under the same cfg or an identical predicate. A conditional trait (`#[cfg] trait Tr`) is no separate path: its
impl is conditional too, and the impl's cfg refuses the call. A trait is certainly in scope at the call only if some way to it has no hop the call can
exist without (`Index.reach_trait`): the ways are the name's own imports in the nearest scope that has a type of that name, else its globs, else the scope
around a block, and each hop is an import (its own `#[cfg]`, then the name in the module its path goes to, recursively, so a `pub use` re-export in
another module is read), or a glob (then the name in its module; a glob under a `#[cfg]` is refused by the lookup before this point). A hop under a `#[cfg]` is certain only if the
call is under the same attribute or an identical predicate. A trait that is not certainly in scope is no edge (`CFG_UNKNOWN`; the lookup of the name reads a
conditional import as the trait, which says what the name means, not that the trait is in scope). Compiled and run: `#[cfg(feature = "a")] use m::Tr;`
with `S::g()` runs `Tr::g` with `a` on and the foreign `Ext::g` with it off, and so do the same re-export in a prelude behind an unconditional `use
prelude::*;` and an unconditional `use r::Tr;` of a module that re-exports `Tr` under `a` and `Ext as Tr` under `not(a)`. A value item of the trait's name
(`fn Tr() {}` in the calling function) is not a hop: the search goes on to the scope around it. Kept edges: an unconditional `use` or glob, however many
unconditional re-exports it goes through; a conditional hop the call is under too; one whole way that is unconditional (two globs, or an explicit `use`
beside a glob); the trait an enclosing impl writes. Every module a path goes through is a hop too (`Index.module_hop`, each prefix of the path): one
under a `#[cfg]` that the call is not also under is not certain, so twin modules (`#[cfg(a)] mod r` with `#[cfg(not(a))] mod r`, also by `#[path]`
files, also nested in a conditional module) make `use r::*;` and `use r::Tr;` refused (cargo: the foreign trait runs with `a` off), and so does a single
conditional module (an edge lost where no build without it is a program). A glob under a `#[cfg]` is refused by the lookup before this point. What is
not read: the visibility of a re-export, and the cfg of an ancestor of a module that the path does not name.
An impl whose header the parser could not read whole is read as unknown everywhere (`IMPL_HEADER_UNREAD`): `impl<u8> W<[u8; 2]>` declares a parameter named
`u8`, which tree-sitter parses as an error with no generics, so ripple read `u8` as the primitive and `W::<[u16; 2]>::f()` got an edge to a trait impl where
the inherent function runs. As a candidate, such an impl is compared with no type arguments; in its own body every path call is refused (`Self::f()`, a type
argument written there, a first segment that may be a parameter of the header; a one-segment call to a function is not); a method call there is refused as in any
file the parser read with errors; and an impl whose self type is a bare name (`impl<u8> Tr for u8`, a blanket impl in disguise) taints the crate's impl tables
like an impl ripple cannot resolve. It costs the edges of an impl whose header merely has a missing node (`impl W<u8> where u8:`). CACHE_FORMAT -47.
The type arguments of an impl and of a call are compared as the type they name (`canonical_type`): `(u8)` is `u8`, `& u8` is `&u8`, and `[u8; 4usize]`,
`[u8; 0x4]` and `[u8; 04]` are `[u8; 4]`. Compared as written, a trait impl for `W<(u8)>` was another type than `W<u8>`, so `W::<(u8)>::f()` got an edge to
the trait impl where the inherent function of `W<u8>` runs (group E). A type argument that is not built of primitives, tuples, arrays and references
of them is still never compared.
Two rules satisfy a conditional attribute of the declaration's path: **the same attribute** on the call's path (above), and **the same predicate**
(`cfg_covers`): another attribute on the call's path whose predicate is the identical tokens, so ripgrep's `#[cfg(test)] mod testutil;` holding the
impl and its sibling `#[cfg(test)] mod tests` holding the calls is an edge (`SearcherTester::new` and its like: 69 of the path edges that the shared-attribute rule alone lost in ripgrep), under five conditions, each one a probe
that refuses when it is broken. (1) One crate reads both: a file that is a module of the library and of the binary is two crates (`cfg(test)` is
on in the binary's test build and off in the library it links), and so are an integration test, an example, a bench, a build script, a
procedural macro crate and every other member of a workspace (a feature is a crate's own too). (2) Both are plain `#[cfg(..)]` or `#![cfg(..)]` written in place:
never a `cfg_attr`, `cfg!`, a predicate a macro made, or an item an attribute macro rewrites (`#[stripcfg]` deletes the `cfg`s inside it). (3) The
predicates are compared token by token: comments and white space between tokens are nothing, every literal is compared byte for byte (`"a b"`
is not `"a  b"`, `r"x"` and `"\x61"` are not `"x"` and `"a"`), and the order matters (`all(a, b)` is not `all(b, a)`: no boolean reasoning; recall
lost, never an edge drawn). (4) Every other conditional attribute on the declaration's path is matched, one by one, by identity or by this rule, and
one that is not refuses; an attribute in the header of the impl or the method (a generic, a parameter, a where clause) is matched by identity only. (5)
A second definition that could win is refused as before: two under one predicate are an error rustc rejects (E0592), one under `not(P)` or
another predicate beside the one under `P` is `SEVERAL`, and a trait method found at an earlier probe step, a `use` or glob that gives the receiver
another type, and a type under another predicate than the call's refuse.
Probes `method_cfg_shared`, `method_cfg_shared_macro`, `method_cfg_crates` (one file that is a module of both the library and the binary),
`method_cfg_predicate`, `method_cfg_predicate_crates` (a workspace: the targets of a package, another member, a procedural macro crate) and
`method_cfg_predicate_macros` (compiled and run: `cargo test` has `cfg(test)` on, `cargo run --bin show` is the build without it, and a
feature off is `--no-default-features`) and the E0592 programs in `bench/fixtures/rust-probes/shared_cfg_errors` answer each case.
A macro in a pattern of a function (a `let`, an arm, an `if let`, a `for`, a parameter) may bind any name (a procedural macro binds `helper`
for a call of `helper()`), so the function's receivers, and its single-name path calls, are unresolved.
A block is a scope of its own wherever a body holds one: a function, a `const` or `static` initializer (also inside a function), the initializer
of an associated const of an impl or a trait, an inline `const { .. }`, an array length or const generic argument written in an expression
(a name the block declares or imports comes before a glob's, as rustc does; each position built). A call in an enum discriminant, or in an array
length or const generic default of a signature or type, is read by no body, so it has no verdict.
**Calls outside the repo** are counted with the reason (the standard library, a crate from outside, the prelude, a
primitive, a derive), and only when nothing of the repo can be what runs: a path through a trait (`fmt::Display::fmt(&x, f)`,
`FromStr::from_str(s)`) that a repo impl of that trait could answer is unresolved. None of the standard library's own names is taken as
the library's in a crate whose standard prelude, or `std`, `core` or `alloc`, may be another crate (a dependency of that key or library
name, a patch, a config of the repo, a path outside the repo, or the crate's own `#[prelude_import]`; each built with rustc): a name only
the prelude gives (`Vec`, `Default`, `drop`, `Some`, a primitive type's name, the blanket `S::to_string(&s)`), and a path that starts at
`std`, `core` or `alloc` (also a macro written from them), are unresolved, and `#[test]`, `#[bench]`, `#[derive]` and `#[global_allocator]`
are read as macros that may have made names. The macros written bare (`println!`, `vec!`, `assert_eq!`) stay the library's: they come
from the sysroot whatever the prelude is, unless the crate says `#[macro_use] extern crate std;` (or `core` or `alloc`, with an alias,
under a `#[cfg]` or `cfg_attr`, the `#![no_std]` idiom) where that name may be another crate: the extern crate is then that crate, its
`#[macro_export]` macros come before the sysroot's, and a bare macro is read as one ripple has not read, so its arguments are not calls (each
built with rustc). Where `std` is the real crate that line changes nothing.
**A `[patch]`, a `[replace]` or a `paths` override is never an edge; it makes a dependency unresolved (Rule P).** Whether cargo applies one
depends on more than a manifest reader can follow: the source it patches, the version it has against the requirement, a `Cargo.lock` that pins
the dependency, which root governs the package, `members` against `exclude`, a `package.workspace`, a git dependency with no version taking a
pre-release, a config that is read only from below its `.cargo` (each built with cargo, the probes in
`bench/fixtures/rust-probes/crates/_patch_source` and `_redirect_reach` pass only when cargo does what they say), so ripple does not decide it.
It refuses instead, by what it can read of each redirect, and in every case where it can see a redirect and cannot read it.
A dependency that is not a `path` dependency (a registry or git one) is unresolved, never an edge through the redirect and never external,
when it may be redirected: (1) its package name (the dependency's key, or its `package =`) is named by any `[patch.<anything>]` or `[replace]`
entry ripple can read in the analysed directory, in any manifest (a root, a member, a lone package, an excluded one, a member's own that cargo
ignores, one in a directory with no source file) or any cargo config of it, whether or not cargo would apply that entry (a name is a word of a
`[replace]` key, so `foo`, `foo:1.0.0`, `url#foo:1.0.0`); or a vendored source directory inside the analysed directory holds a package of that
name (below); or (2) a
manifest that may govern the package was not read (a `package.workspace` naming one outside the analysed directory, a Cargo.toml above the
analysed directory, one that cannot be read, a config that cannot be read) and the name is that of a package of the repo, which an unseen
patch could point at. Otherwise it is the fetched crate, outside the repo.
A vendored source is the one way besides a `[patch]` and a `[replace]` that a name becomes a package of the repo: `[source.<name>] directory = "dir"` in a cargo config, what `cargo vendor`
prints, makes cargo build the package of a registry or git dependency from that directory (built, with the table written as a header, as dotted
keys, as inline tables and through a chain of `replace-with`, and for a git source). Ripple indexes that package, so a registry or git dependency
that has a package of its name at or below such a directory inside the analysed directory is unresolved: never external, and no edge is drawn
into `vendor/`. The name is the one in the package's Cargo.toml, not its directory's (`vendor/foo-1.0.0` holds `foo`). Which source replaces which
is not asked, and every directory below it is read, a dot directory excepted (cargo reads only its direct children, so reading more only refuses more). A link is
not entered, so that a loop ends, except where cargo follows one (it keeps a direct child whose `Cargo.toml` exists, following links; built): a direct
child that is a link to a directory inside the analysed directory is a package of the repo, whose code runs, so its name counts; one that points
outside the analysed directory is a crate from outside it, and the dependency stays external; a link that dangles, loops or names a file holds no
package; the configured directory may itself be a link, read where it really is; and a `Cargo.toml` that is a link is read through it. A dependency
with no package of its name there stays external, and so does every dependency when the directory is outside the analysed directory (the crates there are crates from
outside the repo). A `[source]` table ripple cannot read, or a vendored directory holding a Cargo.toml it cannot read, makes every registry or git
dependency unresolved; a `path` dependency into `vendor/` is an edge, as any `path` dependency is.
A `path` dependency is an edge to the directory its `path` names unless it may be redirected, which cargo does in two ways ripple reads by name
(each built): an entry of a `[patch.<key>]` table of the dependency's name (the key, or its `package =`), unless the key is `crates-io` or an
http, https, ssh or git url, which cannot be a path source's url (cargo
leaves such a patch unused), where a `file:` url is the url of a path source and a registry name or anything else is read as one, since ripple
cannot tell which index url a name stands for; an entry of a `[replace]` of that name whose key holds `file:` or `path+`, the id of a package of
a path source, which cargo applies when the entry has a source of its own (`foo:1.0.0`, `foo@1.0.0` and the id of a registry or git source are
not a path package's, and cargo leaves the path dependency alone: the edge is right there). A `[patch.crates-io]` therefore leaves a path
dependency an edge.
A third redirect is the config's `paths`, and ripple does not follow it at all. A `paths = ["dir"]` entry makes cargo load every package at or
below `dir` and also every path dependency of each of those packages, transitively, in every
dependency table and wherever the path points (built: a registry dependency becomes the package a nested path names, a path dependency is replaced
by a package outside `dir` that a package below it reaches, through a `[dev-dependencies]` and a second hop too), and a dependency of the name of
any package so loaded is that package, whatever its source. Following that reach was missed in part three reviews running, so ripple does not
try: any `paths` entry in any cargo config of the analysed directory makes every dependency of every package unresolved, a path dependency too,
whatever the entry holds (a package of another name, no package, a directory outside the analysed directory) and wherever the config sits among the
directories the walk of the repo reads: a config inside a `.git`, `node_modules` or `__pycache__` directory, or a `target` beside a `Cargo.toml`, is
never read, so an entry there refuses nothing, though cargo run from inside such a directory would apply it (the assumption at the walk of the
configs, below). An
empty `paths = []` names no directory and is no entry (built: cargo overrides nothing for it). A `paths` that is no list of strings is a form
ripple cannot read, and refuses the same way as an entry.
Where ripple sees a redirect and cannot read it, every dependency is unresolved, a path dependency too: an `include` key in any form in a cargo
config (cargo applies the `[patch]` and the `paths` of the included file, built; ripple does not read it); a cargo config that cannot be read
(for a registry or git dependency, only as rule (2) says). A manifest that governs a package and cannot be
read (its own, one above it inside the analysed directory, or the root its `package.workspace` names when that root is inside the analysed
directory) makes its path dependencies unresolved as well. A `[patch]` or
`[replace]` of a form ripple does not read makes every registry or git dependency unresolved (cargo rejects those forms, so a path dependency
keeps its edge).
What cargo is given from outside what ripple reads is not seen, and is assumed to redirect nothing: a `--config` on the command line, the
`CARGO_*` environment (a config key set there), a cargo config in a directory above the analysed one or in `CARGO_HOME`, and for a `path`
dependency a manifest above the analysed directory or named by a `package.workspace` that points outside it (a `[patch."file:///..."]` or a
`[replace]` in it; for a registry or git dependency that manifest is rule (2)). A manifest in `.git`, `node_modules`, a `target` directory beside a Cargo.toml or
`__pycache__` is not read for a `[patch]`, which only matters where a build reaches an indexed package by it.
**Unresolved, with the reason:** every method call `recv.m()` that is not that one case, each with a reason of its own that begins
`a method call:` (`impact` lists same-named ones under `possible_missed_callers`): a receiver that is an expression (a call, a
field, a literal, `self.field`), a local whose binding writes no type, a name bound twice in the function (a parameter and a `let`,
a closure parameter, a match arm), a name a statement macro or a pattern macro may bind, an item, an import or a glob that may give that name in a block around the call, a binding under a `#[cfg]`, a generic
or `impl Trait` or trait object receiver (what its bounds give is not followed), a receiver of a type from outside the repo, a method
the form of the receiver does not match (an inherent `&self` method called on a value is found after a trait's by-value method of the
same name, so it is not proven), a method no inherent impl defines (a trait method, one reached through a `Deref`, one a macro made), and
every guard above. These are never counted as outside the repo, since a trait or a `Deref` of the repo may be what runs. Also unresolved: a name two definitions share (cfg twins),
a name a pattern or closure binds, a call inside a macro other than the standard ones, a glob from outside the repo in a
block, a generic parameter used as a path (`T::new()`), any path that goes through the `use` items of a crate of edition
2015 (and every call written in one), a file that several crates share (`mod common;` in each integration test) that
they do not resolve alike, and any name looked up in a scope where a macro may define names (a module or a block that
invokes a macro other than the standard ones that define nothing, or an attribute macro): the macro's expansion is not
visible, so it can neither be proven absent nor chosen between. The same goes for what a `#[cfg]` or `#[cfg_attr]` guards
(ripple evaluates no condition, so a definition that may not exist is not chosen over what would stand in its place), and
for an item under an attribute that is not a built-in one (`#[tracing::instrument]`, `#[pm::attr]`, any attribute macro, bar the three below): calls
inside it and the name it declares are unresolved, though a name beside a derive that no `use` brings in is that derive's
helper (`#[serde(..)]`) and rewrites nothing. A derive can still emit items beside the type, so a derive ripple does not know (`#[derive(Arbitrary)]`; seven of the crates.io derives are the exception, below), or a standard derive's
name (`Debug`, `Clone`, ...) that something other than the standard library could supply (a `use` of another path such as `use derive_more::Debug;` in the derive's own
module or in a block around it, since rustc reads the bare name in the scope of the item and a `use` of another module, another function body or a sibling block is not seen there;
a `#[macro_use] extern crate`, a macro of the repo, a `#[prelude_import]` or the `#![feature(prelude_import)]` of the crate root, or anything that may make the crate's `std` or `core` another crate (which makes its prelude another crate's: a dependency of that key in any table, `dev_dependencies` and `build_dependencies` spelled with an underscore and `[target]` tables included, or a dependency or library whose library is called so, also one a `[patch]` or `[replace]` anywhere in the repo may supply (Rule P); a path dependency outside the repo whose `Cargo.toml` (the one file of it that ripple reads, and fingerprints) names its library `std` or `core`, or cannot be read; a `[replace]`; a `.cargo/config` of the repo that sets a `[patch]`, `paths`, `include`, `[unstable]`, a compiler or wrapper, an `[env]` entry that names one, or rustflags other than codegen, lints and `--cfg`; not an `[alias]`, `[source]` (`cargo vendor`'s), `[registries]`, `registry` or a `[profile]` without rustflags, which cannot make `std` another crate), anywhere in the crate), makes its scope one where a macro may have generated names, so a name found only through a glob import or the prelude is
unresolved there. A `use` of the standard library's own derive (`use std::fmt::Debug;`) is not such a supply when ripple proves it: `std` or `core` is the real crate there (not a module,
alias, dependency or block item of that name) and the path is one the library exports the derive under, unrenamed. `#[test]`, `#[bench]`, `#[derive]` and `#[global_allocator]` are the standard library's attribute macros, not the compiler's, so a `use`
that gives one of those names in the item's module or in a block around it, or a `#[macro_use] extern crate`, makes the attribute another macro, and ripple reads it as one
(a glob import cannot: rustc rejects it as ambiguous with the prelude's macro). `#[rustfmt::skip]`, `#[clippy::..]` and `#[diagnostic::..]` are tool attributes, which a `mod`, a
`use`, an `extern crate` alias or a dependency key of that name makes another macro: they are read as inert only when nothing of that name is declared, imported, aliased or
depended on anywhere in the crate. A `mod` written inside a block shadows the items of its name
around it and its contents are not read, so a path that starts at it, from inside that block, is unresolved. What such a `mod` holds still acts outside it: it taints the impls
of its crate like any item ripple does not read, and a `#[macro_use]` one makes every macro name used in its block possibly the repo's. `Type::f()` and `<T as Trait>::f()` also need the impls ripple read to be all
there are: a macro invoked anywhere in a crate that can name the type (an item, an impl or trait body, a block), an
attribute macro, a derive that is not a standard one, or an impl header that names a path ripple could not resolve, may
add an impl, so the call is unresolved with the macro or the impl named, except an inherent function (rustc accepts one
impl of a name for a type) and the one impl of a trait with no parameters that a path names. An inherent function is the
target only when its impl is for every instantiation of the type or for the one the call writes, it is visible at the call
and it is a function. Calls that happen with no call site (`Drop`, `Display` through `format!`, `?` through `From`,
operators) are not edges, so a trait impl's method can show no callers.
**`check` for Rust** runs `cargo test` (`ripple/rs_check.py`). Stable cargo has no JSON test output (libtest's `--format json`
is refused on a stable compiler), so the verdict is read from the two things that are stable and structured: `--message-format
json` (a `compiler-artifact` for each test binary built, `compiler-message` for each compiler error, `build-finished`) and
libtest's own output, which cargo's `Running` and `Doc-tests` lines divide into one section per binary (`running N tests`, a
`test NAME ... ok|FAILED|ignored` line each, a closing `test result:` line). A section can hold more than one such run: on edition 2024
rustdoc merges the doc tests it can and runs the others (`compile_fail`, `standalone_crate`, an `edition2018`-style override, an
`ignore` snippet that is not Rust syntax) as a second run under the same `Doc-tests` line (measured on cargo 1.99, each case
captured; edition 2021 prints one run whatever the doc tests are, and on 2024 `no_run`, `should_panic` and crate attributes are merged), and a test binary can print a
run of its own. Each run is judged by
itself, a run with no result line is a problem whichever it is, and the section counts as the sum of its runs. The counts of a result
line are checked against its own lines, every binary the JSON says was built must have a section with a result, and cargo's exit
status can only take a pass away. Each section was captured from cargo 1.99 (`tests/fixtures/rs_check/`, with the crate that made it and the exact command);
no older cargo was run. `cargo` must be on `PATH` (otherwise a refusal that names `--test-command`).

With no arguments it runs `cargo test --workspace` in each top-level cargo workspace (a workspace inside another's directory that
is not a member of it, an `exclude`d fixture, is named in `notes` and run only when a path names it; a `Cargo.toml` cargo cannot read
inside a workspace is named too). The arguments:

- a directory, a `.rs` file or `file.rs::module::test_name` (`tests::adds` as written in the file). The path is read from the sources
  only to find the crate and the module path (`util`, `net::tests`) libtest names its tests by. What is IN that module is never read
  from the sources, because a test a macro or an attribute macro writes (`case!(..)`, `#[rstest]`, `#[test_case]`) is no `#[test]`
  function in any file: the test binary is asked (`cargo test <target> -- --list`, stable; it builds the binary once, the run after it
  finds it built), and the run is exactly the listed tests under that module path, `-- --exact`, judged against that list. A
  directory is the modules whose files are below it; a package directory, or a directory that holds whole packages, is those packages
  whole (doc tests included); a crate root file (`src/lib.rs`, `src/main.rs`), a directory that holds one and an example's file are that
  whole target, and a note says so; so is the root of an integration test (a file of `tests/`), with no note. A file is the tests of its module and its
  submodules (a note names the submodules in other files). A path whose module path cannot be taken as certain (`#[path]`, a file the
  crate declares twice, `include!`, a macro that may declare a module, modules ripple could not read) is the whole target, and a note
  says why. A `harness = false` target has no list, so the whole target runs, with a note;
- the id `impact` lists for a test function: a Rust repo has no collected tests, so `impact`'s `tests` is empty and a test
  function is listed among the callers as `src/lib:tests::adds` (file path without `.rs`, `mod.rs` folded into its directory)
  under its file. `check` takes that id and the `file.rs::tests::adds` form (`r#match` as written). A name the list does not have is
  "no test named X exists in TARGET (its test binary lists N tests)", a problem, and nothing is run for it; a function written with no
  attribute at all is refused before anything runs, since no attribute means no test.

Everything is one `cargo test` per package and target (`-p PACKAGE` with `--lib`, `--bin NAME`, `--test NAME` or `--example NAME`),
run from the workspace root, because a filter is shared by every binary of a run, a name is not unique across packages and targets,
and cargo's default is a substring match (`adds` would run `adds_zero` too). A file that two test targets include is a run per
target. Afterwards the tests that ran must be the tests asked for: each listed name needs its own result line in the target, a test
that ran without being asked for is a problem, an ignored one asked for by name is a problem (one below a file or directory is a note
unless every one of them is ignored), and the result line's `filtered out` has to be what the list leaves over. Refused before
cargo runs: an option, an `@file`, a path outside the repo (links resolved), a function with no attribute, a module the file
does not have (only where the source is certain: a file with a macro called among its items, an attribute that is not an inert
one such as `#[cfg]` or `#[test]`, or an `include!` may have modules ripple cannot see, so the test binary's list decides and a
name it lacks is "no test named X exists"), a bench, a target with `test = false` (an example is run all the same: cargo calls it `test = false` by default),
one whose `required-features` are off, a name for a `harness = false` target, a directory with no source of a target, and a directory
whose `Cargo.toml` cargo cannot read (with what cargo said). `cargo metadata --no-deps` (read-only) is the one process that may run
before the last of these.

What is never reported as passed (each has a capture): a build failure (including a test target that does not build, a build
script that fails and a manifest cargo cannot read), `running 0 tests` or a filter that matches nothing, an ignored test that was
asked for, a run in which nothing ran or only ignored tests ran, an integration test target with no test in it, a doctest failure,
a test binary that exits early (`std::process::exit(0)` in a test exits 0 and its result line never prints), aborts or is
killed, a `harness = false` target whose own harness failed, ripple's timeout, a binary the JSON says was built but that never ran,
and a result line whose counts disagree with its tests, a test line ripple cannot read (libtest 1.99 writes `test NAME[ - should
panic | - compile | - compile fail] ... ok|FAILED|ignored[, REASON]`, and a line outside that grammar is a problem naming it), a
binary that exits 0 before libtest starts (no libtest output, no result), and a listing that is empty or does not add up. An integration
test target (a file of `tests/`) with no test is a problem, unless another target of the package reads its file as a module
(`mod util;`, a shared helper): then it is a note. A `harness = false` target has no libtest output and is judged on its exit status
alone, with a note. The result has the usual keys plus `problems`, `counts` and `notes`; failures read
`FAILED src/lib.rs::tests::bad: panicked at src/lib.rs:6:21: message`, an id `check` takes back, and a failed doctest `FAILED
doctest src/lib.rs - add (line 3)`. What the environment can change is either overridden or named in `notes`: `--config
term.quiet=false term.verbose=false` and `--color never` outrank `CARGO_TERM_QUIET`, `CARGO_TERM_VERBOSE` and
`CARGO_TERM_COLOR` (measured: the first two remove or rewrite the `Running` lines); `RUSTFLAGS`, `CARGO_BUILD_TARGET`, a
compiler wrapper, a target runner, a `rust-toolchain` file and a repo's `.cargo/config` that sets flags, a target, a runner or
`[env]` are named; `RUST_TEST_NOCAPTURE` glues a test's output to libtest's lines (measured), which the count check reports as a
problem and the notes explain. A user-level `~/.cargo/config.toml` is not read. Not done: `cargo nextest`, benches, `--features`
(a test behind an off feature is refused when named, and listed in `notes` in a whole run), and a doc test as a named id.
`--test-command` still means exactly that command, read the generic way.

What `check` for Rust cannot promise: stable cargo gives no structured test results, so the output is libtest's text, cross-checked
but not authenticated. A test binary that deliberately imitates libtest's output (writes `test x ... ok` and a `test result: ok.`
line to stdout itself) and then exits with status 0 can still be read as passed wherever the run is the whole of a binary, which has no
list to compare with: no arguments, a package, a test target, and every request that maps to a whole target (an integration test
file such as `tests/it.rs`, a crate root file such as `src/lib.rs`, a directory that holds one, an example's file, and each fallback
to the whole target: `#[path]`, a file declared twice, `include!`, a macro that may declare a module, modules ripple could not
read, `harness = false`). The forgery is caught only where the run is narrower than the binary's list: an id, or a file or directory
below a crate root that ripple maps to a module path. There the result line's counts must agree with what the binary listed
before it ran (`filtered out` is what the list leaves over), which a binary that only imitates libtest's output does not know. A
test author who wants a green can also delete the assertion.

**Attribute macros ripple trusts.** Three attribute macros are the exception to the rule above: `#[tokio::main]`,
`#[tokio::test]` and `#[serial_test::serial]` (also written `#[serial]` after `use serial_test::serial;`). The published
expansions wrap the body of the function in an `async` block or a closure of the same function and add no name the body
can see and no item beside it. So on a free function (not an impl or trait method, not a module) ripple reads the calls in
the body as it reads any function's, but only where the repo proves that the macro is the one on crates.io. `serial` is
trusted only on a function that is not `async` and has no `inner_attrs` argument, and any other attribute macro on the
same item cancels the trust. The name the function declares stays unresolved, since old versions of both macro crates drop
its `pub`. A site the repo does not prove is read as under any other attribute macro, with the same reason.

Ripple trusts a site only when all of these hold:

- Every dependency section of the package (its own, `dev-dependencies`, `build-dependencies` and each `[target.*]`) names
  the crate plainly: a version, with at most `features`, `default-features` or `optional`, and no `path`, `git`,
  `registry`, `package` or other key. `workspace = true` counts when the workspace's entry is plain. The package depends on
  the crate at all.
- No `[replace]` entry and no `[patch.*]` entry, of any crate and written in any way, is in the package's manifest, in a
  manifest above it or in its workspace root. A patch of `quote`, `syn` or `proc-macro2` reaches the macro crates too.
- Every `Cargo.toml` at or above the package can be read. A `package.workspace` names a manifest that ripple has read and
  that has a `[workspace]`. The workspace does not exclude the package (an `exclude` entry that is its directory, one above
  it or a glob), since cargo then builds it as a workspace of its own, with its own `Cargo.lock`.
- Every cargo config file ripple finds can be read and sets only keys from an allowlist, because cargo reads the ones above
  the directory it is run in, which may be any directory of the repo. The walk looks for a file called `config` or
  `config.toml` in a directory called `.cargo`, with the names matched in any case (cargo on a case-insensitive file system
  reads `.Cargo/Config.toml`). It follows a link to a directory that stays inside the repo (each directory once, so a loop
  ends) and reads a `.cargo` that is a link wherever it points. It does not enter a link that leaves the repo. It skips
  `.git`, `node_modules` and a `target` directory with a `Cargo.toml` beside it (build output, where the source walk reads
  no file either); a `target` directory with none beside it is walked, because a package may sit in it. The allowed keys are:
  `build` (`jobs`, `target`, `target-dir`, `build-dir`, `artifact-dir`, `incremental`, `dep-info-basedir`, `pipelining`,
  `warnings`, `rustflags`), `target.<triple or cfg>` (`rustflags`, `linker`, `runner`, `ar`), `resolver`
  (`incompatible-rust-versions`, `feature-unification`), `future-incompat-report.frequency`, `doc.browser`, `[env]` entries
  that do not name a compiler, a wrapper, flags or a search path (names that start with `RUSTC`, `RUSTFLAGS`, `RUSTDOC`,
  `RUSTUP`, `CARGO`, `PATH`, `LD_` or `DYLD_`), and the `term`, `cache`, `net` (`retry`, `git-fetch-with-cli`, `offline`),
  `http` (not `proxy` or `cainfo`) and `install.root` settings. Rustflags may hold only `-C` codegen options, lint levels
  and `--cfg`. Anything else refuses every package of the repo, a key ripple does not know included: `paths`, `[source]`,
  `[patch]`, `include`, `[registries]`, `[registry]`, `[alias]`, `[profile]`, `build.rustc*`, `resolver.lockfile-path`,
  a `[target.<triple>.<links>]` build-script override, `--extern`, `-L`.
- A `Cargo.lock` in the workspace root, when there is one, can be read and takes the crate and its macro crate (`tokio-macros`,
  `serial_test_derive`) from crates.io.
- No package of the repo is named `tokio`, `tokio-macros`, `serial_test` or `serial_test_derive`.
- The crate that uses the attribute is of edition 2018 or later.
- The attribute as written reaches the crate. `tokio` is not a local module, an alias, a glob from outside the repo or a `mod`
  written in a block. `serial` is brought in by exactly one `use serial_test::serial;` that is not under a `#[cfg]`, and no
  macro of the repo is called `serial`.
- Nothing can make a `#[test]`, `#[bench]`, `#[derive]` or `#[global_allocator]` on the item another macro: no `use` that
  gives one of those names is in the item's module or a block around it, no `#[macro_use] extern crate` is in the crate and no
  macro of the repo has the name.
- Nothing can make a `#[rustfmt::..]`, `#[clippy::..]` or `#[diagnostic::..]` beside the attribute another macro: no `mod`,
  `use`, `extern crate` or dependency key of that name exists anywhere in the crate. A path that starts at any other built-in
  name (`#[doc::x]`) or a bare tool name is a macro whenever the program builds, so it is read as one.

What ripple cannot prove, and so assumes:

- What the published macros expand to. It was read for every version published when this was written (tokio-macros 0.2 to
  2.7, serial_test_derive 0.1 to 4.0) and is assumed for later ones: nothing checks the version, not even the one in a
  `Cargo.lock`.
- What lives outside the repo: `RUSTFLAGS`, `RUSTC_WRAPPER`, `--extern`, a cargo config file above the repo (including the
  one in `CARGO_HOME`), a workspace manifest above the repo, and a registry mirror that serves other code under the same
  names.
- A fetched dependency (from a registry or a git repository) does not name its library `std`, `core` or `alloc`. Ripple does
  not fetch or read one, so it cannot know; marking every package that has one would leave almost no prelude name resolved. A
  package that has one whose library is named so builds, and ripple gives the standard library's verdicts for the names that
  library replaces (a constructor for `Some(1)` where the library's `Some` is a function: `_assumed/fetched_lib_std`, built with
  cargo, in `bench/fixtures/rust-probes/crates`; `tests/test_rs_fetched_dependency_assumption.py` pins the wrong verdicts as
  the price of the assumption). The assumption stops at a dependency keyed `std` or `core`, a `package =
  "rustc-std-workspace-*"`, and a path dependency, whose `Cargo.toml` is read.
- Build artifacts committed in the repo that a config points cargo at: `build.target-dir` and `build.build-dir` (and
  `-C incremental=`) name a directory, and one that holds a pre-built, forged copy of a macro crate may be taken by cargo as
  up to date. Ripple does not look in it.

**Derives ripple trusts.** Seven derives of three crates.io crates are the exception to the rule that a derive ripple does not
know may emit items beside the type: serde's `Serialize` and `Deserialize` (reached through `serde` only), clap's
`Parser`, `Args`, `Subcommand` and `ValueEnum`, and thiserror's `Error`. Every published version of the three macro crates was
read (serde_derive 0.8 to 1.0.229, clap_derive 3.0.0-beta.1 to 4.6.7, thiserror-impl 1.0.0 to 2.0.21), and each adds impls of
its own crate's traits for the deriving type and nothing else, with three exceptions that ripple narrows: serde_derive up to
1.0.162 may write a const named `_IMPL_SERIALIZE_FOR_<Type>` (or `_IMPL_DESERIALIZE_FOR_<Type>`) beside the type, so ripple
reads that name as one a macro made, whatever version builds; `#[serde(remote = "..")]` adds an inherent `serialize` or
`deserialize`, which the derive functions of a type already cover (a call of one is both the derive's and a repo trait's,
unresolved); and `#[serde(crate = "..")]`, also under `cfg_attr`, makes the impls those of another path, which may be a module
of the repo, so a type that has one is not trusted. Where a derive is trusted its scope is not one where a macro may have made
names, and its impls are not a reason the impls ripple read may be incomplete. Everything else, structopt included, is read as
before.

Ripple trusts a derive only when everything the attribute macros need (the list above, with `serde`, `clap` or
`thiserror` for the crate and `serde_derive`, `clap_derive` or `thiserror-impl` for its macro crate, and no package of the repo
named like either, nor with a `[lib] name` like either, since rustc links the library of a package of the repo over a proc-macro
crate of its name; a repo with a manifest or a library name ripple cannot read refuses too) holds, and also:

- The bare name comes from exactly one `use serde::Name;`, `use clap::Name;` or `use thiserror::Name;`
  (a leading `::` allowed) in the nearest scope that imports it, a block before the module and never a parent module: not under
  a `#[cfg]`, not renamed, not beside another import of the name, with the crate there being the dependency. A path written
  in the derive (`serde::Serialize`, `::clap::Parser`) must resolve the same way. A glob or a macro that may make names met
  before the import, a macro of the repo of that name, a `#[macro_use] extern crate` and edition 2015 refuse it. The edition is the crate's own: a target
  table's `edition` (`[lib] edition = "2015"`, `[[bin]]`, `[[test]]`, `[[example]]`, `[[bench]]`) decides before the package's, and
  one that is not a known edition reads as 2015. The same edition decides every other rule that reads it, so it also keeps a
  standard derive from being proved by a `use core::fmt::Debug` that a 2015 target reads as its own module.
- For `use serde::Name;` and `use clap::Name;`, no glob import in the importing scope or in a block around it up to the module.
  Their `derive` feature is not asked for: with it off the import gives the trait alone and the name in the macro namespace
  goes on to a glob (built with a local proc-macro crate), and a manifest that turns it on says nothing of another
  target. `thiserror::Name` carries the derive always.
- Nothing is trusted through a macro crate (`serde_derive::Serialize`, `clap_derive::Parser`, `thiserror_impl::Error`), whatever the
  manifests say. A library of the build graph called `serde_derive` (a path or git dependency, a registry package that sets
  `[lib] name`, a package ripple never reads: its `lib.rs` in another directory, a hidden directory, a directory above the one
  analysed) is linked where the proc-macro crate was meant, rustc preferring an rlib of the name, and neither a manifest nor a
  `Cargo.lock` records library names, so no repo can prove it away. `serde::Serialize` is safe because serde's re-export
  resolves inside serde's own build, against serde's own dependency. Built and run (offline, with the real crates and with local
  ones, `bench/fixtures/rust-probes/crates/_derive_trust_lib`): a second rlib called `serde`, `clap` or `thiserror` beside the real
  one is E0464; a proc-macro library of the name loses to the real rlib; either reached under another dependency key, as a
  dependency of a dependency or as a dev-dependency is no `--extern` of that name; and a library called `serde_derive`,
  `clap_derive` or `thiserror_impl` beside the real macro crate or without it leaves the facade's derive the real one. The
  refusal for a library of the repo called like the crate or its macro crate stays as a second guard.
- serde: the `serde` dependency is plain, since the expansion says `extern crate serde`.
- clap: `clap` is the dependency where the derive is written, not a module, an alias or an extern crate of that name, since
  the impls are written `impl clap::Parser for ..` and a local `mod clap` captures that path (built with a local proc-macro crate).
- thiserror: `std` is the standard library where the derive is written, by the lookup and by a test that no dependency or library
  called `std` or `core` may make it another crate, since the impls are written `impl std::fmt::Display for ..` up to thiserror
  1.0.69. That test is a second guard: the `derive` attribute of the item is refused first, by the test of the next entry (which is
  wider: it covers a prelude the crate sets too), so the standard derives' replacement and this check are not the same test, and
  the derive tests stay green with this one removed.
- None of the three where the crate's `std` or `core` may be another crate's (the test above): the `derive` attribute is a macro of
  the standard prelude, which that crate may replace, so a derive written under it is no proof of anything.
- No other derive that is not a standard one and no attribute macro is on the same type, and each bare attribute beside the
  derive is one of its helpers in every version the build can use. A `Cargo.lock` that lists the macro crate says which versions
  those are, so a name counts when each listed version declares it (clap 4's `command`, `arg` and `group`, `group` on `Parser`
  from clap 4.0.7, thiserror 1.0.2's `from` and `backtrace`); with no lock, only the names every version declares (`serde`,
  `clap`, `error`, `source`). A lock counts only while it agrees with the manifests: each requirement written for the crate the
  derive comes through (`serde` and `serde_derive`; `clap`; `thiserror`), in every package the lock covers (the lock's
  workspace and the transitive closure of its path dependencies, members or not: cargo resolves one lock over all of them, so a
  path dependency that asks `serde = "=1.0.228"` downgrades what a lock at 1.0.229 says) and every dependency table, with
  `workspace = true` taken from the root, must be met by a version the lock lists, by cargo's own rules
  (caret with the 0.x rules, `=`, `~`, comparison lists, wildcards, pre-releases). One that is not met, or that ripple cannot
  read, makes the lock no lock for that crate, which cargo re-resolves on the next build. A path dependency ripple cannot read as a
  package (above the analysed directory, a directory with no manifest, a manifest that does not parse or that is no package, a
  path that is no string) is a requirement it cannot see: it counts as unreadable for every crate of the list, so the lock is no
  lock for the helpers and serde is refused with a lock and without. So is a workspace member ripple cannot read: one of the
  `members` of the `[workspace]` above the analysed directory, a `members` that is no list, or a `**` glob (other globs are
  expanded, hidden directories included). A manifest inside the analysed directory that the source
  walk skipped (a package whose `lib.rs` is elsewhere, a hidden directory, a member or path dependency with no source file in its
  directory) is read for this and is part of the cache's fingerprint; nothing above the analysed directory is read. A declared helper beats an attribute macro of that name that is imported (built with a local
  proc-macro crate), so a `use` of the name elsewhere in the crate does not cancel it, which the old rule did. A bare name that
  is not certain is read as it always was, and the derive beside it is not trusted.

A site the repo does not prove is read as the same derive was read before: the same marks and the same reasons.

The serde version rule: when the `Cargo.lock` lists a `serde_derive` that runs a precompiled binary, which no source ripple has
read describes (1.0.172-alpha.0 and 1.0.172 to 1.0.183, 13 versions), the serde derives are not trusted, and a lock that lists
several versions of `serde_derive` is refused if any is one of them. The clap and thiserror derives are not affected. With no
`Cargo.lock` that agrees with the manifests (none, or one a requirement no longer meets), cargo builds the newest published
version that satisfies the requirements, which ripple computes from the index's list of versions (`serde_derive` alone: the newest
that meets its requirements; with `serde`: the version its newest satisfying release pins, `=<its own version>` from serde 1.0.103
to 1.0.219, and both when `serde_derive` is also written, since the pin of the older ones belongs to the `derive` feature) and
refuses when it is one of the 13, or when a requirement cannot be read or nothing published meets it: `serde = "=1.0.180"` and
`serde = "<1.0.184"` are refused, `serde = "1"` is trusted. A version published after the list was made is newer than every one
of the 13.

What ripple cannot prove, and so assumes, for the derives: what the published macros expand to. It was read for every version
published when this was written, except the binaries of those 13 serde_derive versions (their source says `const _` and
nothing else; the binary was not read), and is assumed for later ones: the version of the crate is not checked, and a lock is
read only for the 13 above and for which helper attributes the listed versions declare. That cargo picks the newest version
that satisfies, and that nothing outside the manifests lowers it, is assumed too. What cannot be seen: the requirements of a
registry or git dependency, which are not in the repo (a crate of crates.io that itself asks `serde = "=1.0.180"` is not read
when there is no lock; with a lock that agrees with the manifests the lock lists what resolved, and one the dependency made stale
is not noticed), and the manifests of path dependencies above the analysed directory, which count as an unreadable requirement
instead. The same outside-the-repo limits as for the attribute macros apply.

## Java

**Experimental.** Java is the newest front end and the least proven. Its answers agree with javac on every call site of
the five source trees it was checked on (see "Checked against the compiler" below), but its resolver went through two
rounds of fixes for wrong edges in legal Java those trees did not contain, and the JDK table it reads is new.
Read its unresolved reasons, and report an edge that javac would not make.

**`check` for Java** runs Maven (`ripple/java_check.py`) and reads the verdict from surefire's XML reports
(`target/surefire-reports/TEST-*.xml`: a testcase per test, with its failure, error or skipped child), cross-checked against
Maven's exit status and against what was asked for, never from the exit code alone or the log text. `mvnw` in the repo is
preferred to `mvn` on `PATH` (it is the Maven version the repo pins, and a Maven that is too old or too new fails a build
for no fault of its tests; it may download a distribution on first use, which is the repo's own build, and a `mvnw` that is
a link to a file outside the repo is refused, never run). It needs a `pom.xml` at the root, and a root with both a `pom.xml`
and a Gradle file, or Gradle alone, is refused with the command to pass instead. With no arguments it runs
`mvn -B --fail-at-end test` (every module of the reactor; `--fail-at-end` so an independent module still runs after a
failure). The arguments:

- the id `impact` lists for a JUnit test method, `com.acme.shop.CartTest.addsItems()` (`...CartTest.Receipts.receiptHasTotal()`
  for a nested class, parameter types as `impact` writes them), a test `.java` file (the test classes it declares), or a
  directory (a module, or a directory of modules, as a whole, or the test classes under a test source directory);
  `file.java::Class[.Nested][.method[(types)]]` is the same thing with the file named. An id that names two files (the same
  class in two modules) is refused, never guessed.

A class is `-Dtest=com.acme.shop.CartTest` (its `@Nested` classes run with it) and a method `-Dtest=...CartTest#addsItems`, in
the module that holds it: `-pl <module> -am` in a build of several modules, always with `-am`. ripple does not read the poms to
guess which modules a module needs (a dependency in a profile, a parent or a plugin would be missed, and `-pl` alone then runs
against an installed copy of the sibling from the local repository, which can be an old one): Maven builds what the module needs
from the tree. A module asked for whole therefore also runs the tests of what it needs, and the verdict says so; a class or method
runs none of theirs unless a class of the same name is there (named in `notes`). Whole classes of a module share a run; a method
never shares one with another class, because several `#` filters in one `-Dtest` are a cross product in some surefire versions.
The `-Dtest` value is made of names read from the source file, never from the argument. Every argument that is an option, an
`@file`, a path outside the repo, a control character, or not a test is refused before a process starts, and nothing a caller
writes becomes a `-D` property or a build flag.

Merging requests merges the runs, never the requirements: `check([".", "OffTest.java"])` is one Maven run, and OffTest, asked for
by name, is still held to its own obligation (it ran, it was not skipped, it passed). The same goes for a file beside a method
of it, a class beside its nested class, a module beside a class of it, and the same id twice or in two spellings.

Two `check` calls in one repository are serialized (an advisory lock file named by the repo's path in the temporary directory,
never in the repo): two Maven runs writing one `target/` cannot be told apart by the snapshot below, and measured, they did
read each other's reports. A second call waits up to its time limit and then refuses; a build started by anything else (an IDE)
is not covered, and a report it writes during a check can be read as the check's own.

Which reports are this run's: a snapshot of every report file is taken before the run, and a report counts only if it is new or
changed since (mtime, size, inode) and was written no earlier than the run began. A report an earlier run left, saying
PASSED, is never read; it is counted in a note. What is never reported as passed (each has a run captured from real Maven 3.9.9
and surefire 3.5.2 in `tests/fixtures/java_check/`, with the project and the command): a selector that matches nothing (Maven
exits 0 with `Tests run: 0` and no report), a compile failure in main or test code, a module that never reached its tests (the
reactor says SKIPPED, and a module with test classes and no report is named even when it does not), a JVM that crashed or
called `System.exit`, a surefire fork timeout (the report says the test passed and Maven exits 1) and ripple's own time limit, a
requested test that was skipped or disabled, a build that skips its tests (`skipTests`, `maven.test.skip`), a test class
surefire's name patterns do not pick up (`CartChecks`: the run is green without it), and a failure the build ignores
(`testFailureIgnore` exits 0).

Which classes should have run (a whole-build or whole-module run) is predicted from the test sources (every class with a JUnit
method of its own) **and from the classes Maven compiled** (`target/test-classes`: javac has already resolved a test inherited
from an abstract base or an interface default method, a test method carrying a composed annotation such as `@FastTest` that is
itself annotated `@Test`, a `@RunWith`, and whatever test source root the build has, from a parent pom or from build-helper).
The class files are read by ripple (no JDK needed), and the rule has a middle ground that is stated, not left to a guess:

- **A class ripple knows holds tests, and that has no result, is a problem.** It knows when, in the part of the class's supertype
  closure it can read (its superclasses and superinterfaces, transitively, cycles cut, as far as they are classes of the repo),
  it finds one of the known set: on a method JUnit 4 `@Test` and `@Theory`, JUnit Jupiter `@Test`, `@RepeatedTest`,
  `@TestFactory`, `@TestTemplate` and `@ParameterizedTest`, TestNG `@Test`, and any annotation meta-annotated with the JUnit
  Platform's `@Testable` (jqwik's `@Property`, a project's own) where that annotation's class file is in the repo (composed to
  any depth, so `@FastTest` meta-annotated `@Test` counts); on the class JUnit 4 `@RunWith` (a `Suite`, `Enclosed`, any runner)
  or TestNG's class-level `@Test`; JUnit 3 as a superclass `junit.framework.TestCase`. What is proven in the part ripple read
  decides, even when the closure leaves the repo further up. Its name does not matter: `CartChecks`, which surefire's patterns
  (`Test*`, `*Test`, `*Tests`, `*TestCase`) never pick up, and a class a build `excludes`, are problems.
- **A class ripple cannot decide is a note, never a problem**: an annotation it does not know on a void method (JMH's `@Setup`,
  Spring's `@PostConstruct`, Guava's `@Subscribe`: helper code is full of them), or a supertype outside the repo (a library
  class, an interface of a library), or a name defined in several modules ripple cannot rank, with nothing known to hold tests
  in the part it read. The note names the class and why ("cannot tell whether X holds tests (X.setup carries
  org/openjdk/jmh/annotations/Setup), and no test of it ran"); five are named and the rest counted. A class surefire's default
  patterns name (when no pom sets `includes` or `excludes`) is not even that: surefire looked at it and found nothing. It is a
  problem when the request names it and no test of it ran.
- **The cost, stated:** a test class of a framework ripple does not know (a library's own base class, an annotation whose class
  is in a jar, such as jqwik's `@Property` on its own) whose name misses surefire's patterns is not run by `mvn test` either, and
  ripple reports it in a note, not as a failure. A repo that never runs such a class passes. The alternative, a problem for every
  class that cannot be decided, made every whole-build check of commons-lang read not passed (two JMH benchmark classes in its
  test tree) and would have made any repo with helpers read the same.

A class is looked up the way its own module's build would resolve it, never by name across the whole repo: in the module's
`target/test-classes`, then its `target/classes`, then the modules of this build it depends on (read from `<dependencies>`, in
profiles and parents too; their `target/classes`, and their `target/test-classes` only through a `test-jar` dependency). A name
found in two of those that ripple cannot rank is ambiguous, and the class is undecided. A compiled class counts only if the
module's current source roots (`src/main/java`, `src/test/java`, or what the poms set, build-helper included) hold a source for
it: the same package path and the outer class's name (`Outer$Inner` and `Outer$1` follow `Outer`), or the file the class file
names as its source. A class with none is left over from an earlier build (a module this run did not build, a deleted source) or
generated, and is evidence of nothing; it is named in a note ("compiled class with no hand-written source"), whatever its
package is called. Where the source roots cannot be read (a property), or a class was not compiled from a `.java` file (Kotlin,
Groovy), every class counts. Class files, sources and reports are read as regular files inside the repo only (a link, a FIFO or a
file over the size cap is refused, with a read that never blocks), and a class file that cannot be read is a note naming it.

A module with test sources (or a test root held in a property) and no current compiled test classes is "cannot predict"; results
from a module whose prediction is empty are "predicted no test class but tests ran". Left out, and named in `notes`: failsafe's
classes (`IT*`, `*IT`, `*ITCase`, run in `verify`). Not decided from bytecode: a class that gets its tests from an external
library base, the interface of a library, a `Cucumber`-style runner (its results come under feature names: it has a result
through its report file, when that holds tests), a class that exists only in a `target` directory other than Maven's default.
Results are matched by the class a testcase
names, or by the class the report file is of (a JUnit `AllTests` suite reports its tests under other names). `MAVEN_OPTS`, `MAVEN_ARGS`, `.mvn/maven.config` or `.mvn/jvm.config` that set `-DskipTests`,
`-Dtest`, `-Dgroups` and the like make the run not passed, named; a pom that configures `skipTests`, `groups`, `excludedGroups` or
`testFailureIgnore` is named in `notes` (an `excludes` the build sets shows as a test class with no result). Maven selects by method name, so an overloaded test method runs every overload: the verdict says so,
names the overload that failed, and fails when only other overloads ran. A parameterized test, a `@RepeatedTest` and a
`@TestFactory` report as `name()[1]`, `name()[2]`: the method name is what is matched, and the verdict says how many invocations
ran. The
result has the usual keys plus `problems`, `counts` and `notes`; failures read `FAILED file.java::Class.method: message`.
What it does not read: Gradle, TestNG, a build whose reports are written outside the repo (nothing is found, so it is not
passed), failsafe integration tests (`verify`, not `test`: named in `notes`, never expected), and Kotlin tests. `--test-command` still means exactly that command.
Java's profile is untouched (its fields are hashed into the cache key).

Java is read by tree-sitter too, as its own optional extra (`tree-sitter` and `tree-sitter-java`):

```sh
uv tool install "ripple-code[java] @ git+https://github.com/xtelos/ripple"   # or pip install 'ripple-code[java]'
```

`--lang java` or `auto` (which looks for `.java` files, skipping `package-info.java`, `module-info.java` and the
output directories `target/` beside a `pom.xml`, `build/` beside a `build.gradle` and an IDE's `out/` and `bin/`,
which `ripple index` lists with the other skipped directories) reads a repo as Java. A Java-only repo without the extra is refused with the install command, and a repo with Python
and Java answers for Python and says Java was skipped.

**Symbols** are dotted, like Python's, and a method carries the simple names of its parameter types as written, so
overloads have their own ids: `com.acme.shop.Cart` (a member class is `Cart.Line`), `com.acme.shop.Cart.add(Item)`,
`Cart.<init>(List)` for a constructor, `Log.info(String,Object...)` for varargs, `Ledger.runAll(List)$1` for the first
anonymous class in a method, `Op.ADD` for an enum constant with a body. A file is its path with the extension
(`src/main/java/com/acme/shop/Cart.java`, found by any tail of that path, `shop/Cart.java` too), so `Cart` is the class.
Any of these finds it: the full id, `Cart.add(Item)`,
`Cart.add` when only one method has that name, or the bare name. A name that two overloads share is not answered for
one of them: the overloads are listed as candidates. A class's callers include the callers of each of its constructors.

**What is an edge.** The Java resolver (`ripple/java_resolver.py`) does not use the shared one, because Java picks a
method by its name and its parameter types from the whole class hierarchy. A call is proven from declared types, imports,
the enclosing class that has the name (the "comb rule"), and the arguments' types: with several overloads of the right
number of arguments it picks one only when every argument's type is known and equals that overload's parameter types,
and otherwise says "N overloads of m take n arguments". Only the methods a type has as members and the caller may call
are candidates (a private method is not inherited, a package-private one is not seen from another package, a protected
one needs a subclass body), and a parameter written with type arguments takes only an argument written with the same
ones. Two imports of one method name, a name that a local class or a pattern variable may or may not hide, and a type
name that a supertype from outside the repo may have as a member type are not decided either. A call on an interface or a class with subtypes also gets an
`override` edge to each method in a repo subtype that overrides it (anonymous classes and enum constant bodies included;
a type variable counts as what the subtype's `implements Visitor<Item>` makes of it, and a method a subtype inherits from
a superclass counts too), but not `super.m()`, `Type.m()` or `new T().m()`. Generics are erased, so a value typed by a type variable, an array
element, the result of a call into the JDK or an untyped lambda parameter is unresolved.

**ripple does not know the JDK's or a library's members, so it does not guess them.** A method of a class that extends or
implements a type from outside the repo (`class Ledger extends Writer`) cannot be proven to be the one a call means,
because that type may declare another method of the name: the call is unresolved, with that reason. The exceptions are
the three types the language itself puts under every class, enum and record (`Object`, `Enum`, `Record`), whose members
are built in, and the types of the JDK table (`ripple/data/java_jdk.txt`): 1,812 types of `java.base` and `java.compiler`
with their supertypes, methods and the numbers of arguments each takes, fields and member types. A name such a type lacks
is not its own, and a marker interface like `Serializable` declares nothing. The table is generated from the class files of
every released JDK from 8 through 27 (twenty of them, each named with its exact version in the file's header), because the
repo may be built with any of them, and each fact is merged in the direction in which it is safe: a supertype, method, field
or member type is in the table when any of those JDKs has it, so only a name the table lacks proves anything. A member that
only some JDKs have (`Thread.destroy()`, `ArrayList.getFirst()`, `Process.close()`) blocks an edge and
never proves one: in `class Q extends Thread` nested in a class that declares `destroy()`, an unqualified `destroy()` is
unresolved. The table stops at JDK 27. A repo built with a later JDK may meet a member, supertype, field or member type
that the table lacks, and ripple cannot tell that from the repo. Its Java answers rest on the assumption that no JDK
after 27 adds one (JDK 26, for example, gave `Process` a `close()` and made it an `AutoCloseable`). The table is regenerated with
`bench/java_jdk/generate.py` when a JDK is released. A table file that is cut short is refused whole: the header holds the
number of rows, and when the file holds fewer, every type from outside the repo is unknown and `ripple index` notes that
the table was not read. Any other type from outside the repo is unknown, and an unknown type may declare, inherit and be anything. A repo
that declares a type in a package of the table (`package java.util;`) reaches members the table leaves out, so for that repo
every type from outside it is unknown. A comment can stand between any two tokens and changes no answer. A call is counted
as outside the repo only when it cannot run repo code: a call into the JDK or a library from a type it can name, or a call
of a name no repo source declares with that many arguments (unless a Lombok annotation is in the repo). A value typed
with an external class or interface, or `Object`, may be an instance of a repo class (`List l = new MyList(); l.add(x)`
runs `MyList.add`), so a call of a name such a class declares is unresolved, unless no repo class can be an instance of the
JDK class: it is final in every JDK (`String`), or it is a class with no public or protected constructor in any JDK and so is
every non-final table type below it (`java.util.Arrays`, which is final from JDK 21 only).

Not followed, and never an edge: method references (`this::show` names a method, it does not call it), reflection,
Lombok and annotation-processor output, qualified inner-class creation (`outer.new Inner()`), `check` for Gradle (a Gradle
repo has no default test command yet: pass `--test-command`; no Java test is collected, see `check` for Java), and JDK 25 syntax the grammar does not
know (those files are listed with the ones that could not be parsed, and read as far as the parser recovers).
A file whose directory does not end in its `package` path is not indexed and is listed with the reason (a Java file a test
compiles as data), and two files that define one fully qualified name (Android product flavours) are indexed as neither:
their types have no symbol, calls of them are unresolved, and `ripple index` warns that the files share a module name.

**What an on-demand import does not give.** A name that an on-demand import of a JDK package might supply (`import java.util.*;`,
then `Timer`) is NOT resolved to the JDK's type. It is `?.Name`, a type of a package that is not known, and the shipped JDK table
is not read for it. A type of the file's own package comes before every on-demand import (JLS 6.4.1), and a package is not
closed: a jar on the classpath, or generated code, can put a `Timer` into the file's package or into a repo package the file
imports, and ripple cannot see it. The table's absences ("`java.util.Timer` has no `foo`, so `foo()` is the enclosing class's",
"final", "no such constructor") would then be about the wrong type. So such a name supplies nothing: a class that extends it may
declare, inherit and be anything, a call on it is external only where no repo type may be an instance of it, and it never picks an
overload by its type. A package that no outside source can add to would allow more, and that is a named module,
`module-info.java`, which ripple does not read. What the repo's own sources supply is exact: a repo type an on-demand import
brings is the name, and a type of the package, in any file ripple read, comes first.

**What this closes.** Each of these gave a wrong edge or a wrong "external" before, and is now unresolved with the reason:
a single static import of the name (`import static p.Shapes.Queue;` may import a member type that comes before the on-demand
import); a static on-demand import of a repo type that has a member type of the name; `import module m;` (JDK 23 and later),
which the grammar cannot read and which may export repo types; a package that only a file in the wrong directory declares, which
is an unknown package and not a repo package; and a `.java` file of the repo that ripple did not read whole.

*Which files.* Every `.java` file below the repo that the walk does not hand over: `package-info.java` (javac accepts a class
there), `module-info.java`, build output under `target/`, `build/`, `out/`, `bin/` (where the build tool that writes it is
there), hidden directories and every other directory the walk skips, a file in a directory that is not its package's, a file the
parser read in part (a parse error: the structure it recovered is no evidence, so every method the text of such a file declares
counts as unseen, the ones the parser saw too), and a file that could not be read. Only `.git`, `node_modules`, `__pycache__`
and `site-packages` are not looked into, and a directory behind a symlink is not followed (the walk does not follow one either).
Ripple reads their text, not their tree. The text is decoded as UTF-8, else ISO-8859-1 (which gives every byte a character, so no
byte sequence hides a name), with UTF-16 and UTF-32 decoded by their byte-order mark or, with none, by the NUL bytes between the
letters. A scan errs toward more names: a name is any run of characters that is no blank, separator, operator or quote (Java
allows `$`, currency signs, connector punctuation, combining marks and every letter of Unicode, and characters javac ignores in an
identifier are dropped); a method name is noted wherever a declaration may stand (a name after a result type, so `record(...)`,
`yield()` and every contextual keyword count, and a name after something the scan does not know counts with no arity); every
component of a record is an accessor; every name after `class`, `interface`, `enum` and `record` is a type. The number of
parameters narrows a refusal only when the list is read with certainty (closed, no receiver parameter; a varargs one takes any
number from one less than its parameters).

*Which verdicts.* One question is asked wherever ripple would take a type for a library type, `unread_type(qualified name)`: may
an unread file declare a type of that package and name? If so the verdict is unresolved, never external. It is asked on every
route that makes a type external: a single-type import, a full name (`new q.Maker()`, a field, a parameter, a local, a cast, a
caught type), an on-demand import, a name of the file's own package, a single or on-demand static import (`make()`), a
superclass (`class Sub extends Maker`, `super.m()`, `super(...)`, a method a read subtype inherits from it), a member type, and
a full name in a package of the JDK. A test (`tests/test_java_unread_types.py`) reads every place the resolver builds an
external verdict off the module's own syntax tree and fails on one that is not classified, and runs a matrix of every route twice,
with the type in an unread file and in a jar. The other conclusion, "no repo code runs here" from the absence of a method name,
asks `unseen_method(name, arguments)` on every route: a call no repo method has the name of, a value of a type from outside the
repo that a repo type may be, a method inherited from outside the repo that a subtype may override. An enum, a record and a final
class have no subtype in a file ripple did not read, but only where their own file was read whole.

*Which files are never opened.* Only a regular file inside the repo is opened, and at most 4 MiB of it is read. A named pipe, a
device, a socket and a directory called `X.java` are never opened and declare nothing; neither does a link that leads nowhere
(dangling, or around a loop). A link to a file outside the repo is not read and may declare anything, a file that cannot be read
and a directory that cannot be listed may declare anything, and a file larger than 4 MiB is not scanned: it may declare any method
and any type of the package its first bytes say. Each of these is listed with the files that did not parse, with the reason.

*What it costs.* Recall only. A repo with a `.java` file that declares a method of a name ripple would otherwise have proven
external has those calls unresolved, whatever type they go through (a name is asked, not a receiver). A copy of the sources that
is not byte-identical (a `target/checkout` that the maven release plugin leaves, a checkout under `.claude/worktrees`) is a set of
unread files that declares what the sources do: on commons-lang with such a copy, 2,950 of 43,235 external calls become
unresolved. A byte-identical copy costs nothing. Measured on gson, commons-lang, junit5, okhttp, auto, petclinic and antennapod
(a quarter of a million call sites), two sites move from external to unresolved, both in auto, and none in the other six.

**What is assumed.** The names `java.lang` supplies keep the reading they had before this: `java.lang.Name` where every JDK has
the type or nothing else is imported on demand, `?.Name` otherwise, and a full name `java.lang.Name` is not asked of the unread
files. That reading has the same exposure (a type of the package that no source shows would come first), none of the guards above
applies to it, and it is not widened. "Every JDK" means JDK 8 through 27: a build for an older JDK, whose library had a type that
JDK 8 added to `java.util` (`Base64`), is outside it, as a JDK after 27 is. Code outside the repo is assumed to be what its name
says: a type from a jar is a library type, and a file that a build compiles from outside the repo directory (a source path above it,
a directory behind a symlink, generated output written elsewhere, an annotation processor's) is not seen. Other JVM languages are
not read: a Kotlin, Scala or Groovy source in the repo may declare a type that a `.java` file imports, and ripple does not know it.
A source in an encoding that is neither UTF-8, UTF-16 or UTF-32 nor ASCII-compatible (EBCDIC) is not read as the text it is, and
names in a script outside ISO-8859-1 in UTF-16 or UTF-32 with no byte-order mark are not decoded.



**Limits.** The JDK table covers JDK 8 through 27 (a later JDK is past its horizon, see above) and `java.base` and
`java.compiler` only: a type from any other JDK module (`java.desktop`,
`java.sql`) or from a library is unknown, so a call that a supertype of that kind could answer stays unresolved. A name that
an on-demand import supplies has no package in ripple's reading, so it is unknown, whatever the table holds.
The table is read for a name only through a single-type import, a fully written name, or java.lang. `check` has no
default test command for Gradle, and no Java test is collected: `impact` lists a test method among the callers.

**Checked against the compiler.** `bench/fixtures/java/probes` holds 195 small legal Java programs, each with `javac.json`:
every invoke instruction javac wrote for it (`bench/java_probes.py`, run by hand with a JDK). Five of them depend on the JDK
they are built with and also carry the answer of another javac (`javac-8.json`, `javac-25.json`, `javac-26.json`); ripple is
held to every one. (A source file whose first line is `// javac-min: 26` is compiled only by a javac of that release or later.) `tests/test_java_probes.py`
needs no JDK and requires every edge ripple makes to be the method javac invoked, every `override` edge a method the
receiver's subtypes really run, and every call it counts outside the repo one that cannot run repo code (ripple may be
silent, but it may not be wrong). `tests/test_java_comment_injection.py` writes every probe and the golden fixture again with
comments between the tokens and requires the same graph. `bench/java_corpus.py` is the same judgement on a real source tree
(javac compiles it, `javap` reads every class, each call site is paired with its invoke instruction) and its bar is zero wrong
calls, overrides and externals. It met that bar on gson (3,408 call sites), commons-lang (11,139), the JUnit platform modules
(7,047), auto's `common` (1,319) and the golden fixture, each compiled with JDK 21. That check is weaker in one place: javac
names `java.lang.Object` as the owner of an `Object` method the receiver's class does not declare (`equals`, `hashCode`), so
the bytecode does not say which class the receiver is, and those sites (52 in those corpora) are listed apart rather than
judged. `tests/test_java_javac.py` does it for the golden fixture and runs only when `javac -version` reports 21 or newer.

## How it works

The core has no parser dependency: the Python front end reads files with the standard library `ast`
module, and the base install needs only the `mcp` SDK. The TypeScript front end (`ripple/ts_indexer.py`)
writes the same per-file facts from a tree-sitter parse, so one resolver builds both graphs. The languages
are listed once, in precedence order, in `ripple/registry.py`: a language is a profile (`ripple/profile.py`)
and one entry there naming its front end module, which is imported only when a file of that language is read.
The Java front end (`ripple/java_indexer.py`) declares the types and members, and its own resolver
(`ripple/java_resolver.py`) decides each call and writes the graph, so only the first step below is shared with it.

1. **Collect** (`ripple/indexer.py`): each file is read on its own. For every module, class and
   function it records what names are bound (defs, imports, assignments and what they suggest about
   a value's class) and every call site.
2. **Resolve** (`ripple/resolver.py`): with the whole repository in view, each call site either
   becomes an edge, because the source proves its target, or is kept as *unresolved* with a reason.
   ripple never guesses. "There is only one method named `save`, so it must be that one" is exactly
   the kind of answer that looks right and is wrong in the cases an agent cannot check.

What counts as proof: names defined or imported in scope; `import x`, `from x import y`, aliases,
relative imports and package re-exports; `self.method()` through base classes; `super()`; calling a
class (edge to its `__init__`); and method calls on values whose class is known from a constructor
call, a type annotation, a return annotation or a `self.attr` assigned in the class, including a
value that is itself a call result (`Foo().run()`, `make().run()` where `make` is annotated `-> Foo`,
`Foo().current().place()`). When a method is called on `self` or a typed value, subclass overrides are
linked too (class hierarchy analysis), except on a value that is exactly `Foo()`.

A call written where a definition is made belongs to the scope that holds the definition: default
arguments and decorator arguments, and calls inside annotations (`qty: Annotated[int, Field(gt=0)]` on a
class attribute, a parameter or a return annotation), which are edges of kind `annotation`. With
`from __future__ import annotations` such an expression runs later, when pydantic or
`typing.get_type_hints` evaluates it, but it is still a call written in that scope.

**Decorators.** `@timed def hello()` binds the name `hello` to whatever `timed` returns. When `timed` is
a repo function that defines a wrapper and returns exactly that wrapper (`def timed(fn): def wrapper(*a):
return fn(*a); return wrapper`, with or without `functools.wraps`), a call to `hello` is a call of
`wrapper`, and `wrapper` calls every function decorated with it (edges of kind `wrapped`). A decorator
that returns its argument (`def register(fn): ...; return fn`) leaves the name alone, a factory such as
`@route("/x")` is read as the def it returns, and a stack is read from the bottom up. What ripple does
not read, it treats as before, the decorated function called directly: a decorator from outside the repo
(`lru_cache`, `property`, a framework's `route`), one that returns different things on different paths
(`if not enabled: return fn`), one that is itself decorated (flask's `setupmethod`), a generator or async
decorator, one that rebinds its parameter, a wrapper that has a decorator other than `functools.wraps`,
a call of the wrapped function made from a function nested in the wrapper, a decorator applied by hand
(`f = timed(f)`), a class decorator, and a function defined more than once (a def in each branch of an
`if`, decorated on one or on both): which def a call finds depends on the branch taken, so the call stays
a direct call of the function and no wrapper is added.

A call of a decorated function is typed by its def's return annotation (`compute(1).wait()` with
`def compute(x) -> Result`) only when every decorator is proven to hand the value back: one that returns its
argument, a wrapper whose every return is `return fn(...)` (not `result = fn(...); return result`, and not an
async or generator wrapper), or `property`, `staticmethod`, `classmethod`, `functools.cache`, `lru_cache`,
`cached_property`, `abstractmethod`, `overload`. Any other decorator, a repo one ripple cannot read or a
dependency's (`retry`, `validate_call`), leaves the result unknown and the call unresolved, with the
reason. A decorator also called as `timed(g)` keeps its wrapped call unresolved as well, since the
wrapper may be given `g` too. One wrapper body serves every function its
decorator is applied to, but in each decoration it calls one of them, so the edges of a decoration carry
the decorated function (`via`) and `impact` and `path` follow a chain through the wrapper only for the
function the chain entered it for: the callers of `run_unload` are not callers of `ship` because both
are `@audited`. `callers` of the wrapper itself still lists every caller of every function it wraps.
`callers` of a decorated function lists the wrapper, which is what calls it, and also, under
`through_wrapper`, the lines that write its name and reach it through the wrapper; `impact` labels such a
caller `via_wrapper`. So a signature change finds every call site to edit.

**Functions held in names.** `with_tax = functools.partial(apply_tax, 10)` then `with_tax(x)` is a call of
`apply_tax`, when `partial` is `functools.partial` (imported any way), every assignment to the name is such a
call, and they all name the same function or method of the repo (a bound method such as `self.k.step` also
reaches the overrides of that method). It stays unresolved, with the reason, for a partial of a class, of a
parameter or of anything ripple cannot resolve, a name assigned partials of different functions or also
assigned anything else, and a module-level name another module assigns to (`m.T = ...`, `m["T"] = ...`), a name
some function declares `global` and then rebinds (by assignment, `def`, `class`, `import` or `except ... as`),
or whose module has a star import that could define it. Stores written as calls are covered by one blunt rule
(see "Tables and partials, and the store words" below).

**Dispatch tables.** A module-level dict, list or tuple literal of plain function names is a fixed table when
no code in the repo can change it, and then `TABLE["k"](x)`, `TABLE.get("k")(x)`, `TABLE[1](x)`, a local
assigned `TABLE["k"]` and called, and `for f in TABLE: f(x)` (also `.values()` and `.items()`) are calls of
the entries they reach: the one under that key, or every entry for a loop. ripple proves "no code can change
it" by reading every module for any use of the name that is not a read: a subscript store, `update`, `pop`,
`append` or any other method that is not `get`/`items`/`values`/`keys`/`copy`, `del`, an augmented assignment,
passing the table to a call (`len(T)` included), aliasing it, returning it, storing it, `global T`, a second
assignment, `getattr(m, "T")`. Names are matched as text in every module, so an import
alias (`from m import T as U`, through any chain), `m.T` and a star import are all covered, and so is an
unrelated variable that happens to share the name (the call then stays unresolved, never guessed). A module
that has a star import that could define the name is not trusted either, nor is any table while a store word
appears in the repo (see below). The unresolved call says which use spoiled the proof and where
(`T may be changed or handed on: 'T' is used other than to read it, at m.py:60`). An entry that is not a plain name of a function or method, a missing
key, a variable key, a loop over a dict's keys, and a loop variable that is assigned anywhere else in its
function (two loops that reuse a variable name count), called after the loop, in the loop's `else`, in a
nested function, lambda or generator expression, all stay unresolved.

**Tables and partials, and the store words.** Code can replace a module's table or partial without writing
`module.T = ...`: `setattr(m, name, v)` in a loop over overrides, `mock.patch`, `vars(m)[k] = v`, `exec`. ripple
does not try to recognise the shapes of those stores (every shape it learned had another spelling). It
looks for the words instead: `setattr`, `delattr`, `__setattr__`, `__delattr__`, `__dict__`, `vars`, `globals`,
`locals`, `__globals__`, `f_globals`, `f_locals`, `exec`, `eval`, `__import__`, `importlib`, `patch`, `monkeypatch`,
`builtins`, `__builtins__` and `sys.modules`. If any file of the repo has one of them, as a name, an attribute,
or in a string or a comment, in any spelling (an alias, `self.module`, a line continuation, a look-alike Unicode
letter, a file in another encoding), no table or partial of the repo is followed, and the unresolved call
names the word and where it is. That disables tables and partials in any repo that uses those words anywhere,
a test file that only calls `monkeypatch` or `patch` included (so most repos with a test suite that
patches). A file the word is in is not excepted because it does not import the module: another module can
hand it one. Not covered: a word spelled by computing a string at run time
(`getattr(object, "__set" + "attr__")`), `gc.get_referrers`, `ctypes`.

The index is cached outside your repo (`RIPPLE_CACHE_DIR` if set, else `$XDG_CACHE_HOME/ripple` or
`~/.cache/ripple`) and keyed on every file's mtime and size, so each query re-checks the files and
rebuilds only when something changed. A TypeScript or JavaScript graph is also stored with what the resolver found on disk beyond
those files (a file in a skipped directory, a `.vue` component, a directory, a link), and is not served once any of it differs
(on create-react-app that is 471 paths and 20 ms of the 400 ms a cached load takes).
On my machine (a WSL2 laptop, timings vary by about 30% run to run) click, 91 files, takes 0.4 s cold and
0.08 s from the cache; pydantic's repository, 3,156 files with its core's tests and docs, 3.7 s and 0.4 s,
peaking at 190 MB; Django, 2,941 files, 16 to 20 s and 1.1 s, peaking at 445 MB (the cyclic garbage
collector is off while the graph is built, which is faster and costs memory).

## Benchmark

`python -m bench` scores ripple against hand-written ground truth: 63 caller, callee and impact
queries over four small fixture apps in `bench/fixtures` (an order and pricing service, a notes CLI,
a web handler layer, and a warehouse toolkit that holds the calls on standard library objects, on call
results, in annotations and defaults, through decorators that wrap, and through `functools.partial` and
dispatch tables). CI fails if any number drops below `bench/baseline.json`.

| Case | Queries | Exact | Precision | Recall |
|---|---:|---:|---:|---:|
| Imports, aliases, re-exports | 11 | 11 | 1.000 | 1.000 |
| Methods, `super()`, constructors | 6 | 6 | 1.000 | 1.000 |
| Typed receivers (annotations, call results, standard library values) | 8 | 8 | 1.000 | 1.000 |
| Callees | 3 | 2 | 1.000 | 0.750 |
| Decorators (including wrappers) | 10 | 10 | 1.000 | 1.000 |
| Dynamic (getattr, dispatch tables, partials, untyped values) | 12 | 4 | 1.000 | 0.438 |
| Calls made at definition time | 2 | 2 | 1.000 | 1.000 |
| Impact (transitive) | 7 | 4 | 1.000 | 0.633 |
| **Overall** | **59** | **47** | **1.000** | **0.779** |
| Missed callers (scored apart, not in overall) | 4 | 4 | 1.000 | 1.000 |

The missed-callers row scores the other half of the contract: for a symbol, the callers ripple lists
under `possible_missed_callers` because it could not rule them out. There, precision is the share of
the listed callers that really call the symbol (a caller that never does is a false alarm), and recall
is the share of the callers ripple missed that it still lists. It answers a different question from the
rows above, so it is not averaged into the overall figure.

How to read this honestly:

- **The truth was written before the code.** The first three fixtures and every expected answer were
  committed before any indexer code existed (see the first commit), and the dynamic cases were
  included on purpose to show where static analysis stops. The warehouse queries were added the same
  way: each group was committed, failing, before the rule that answers it.
- **The truth is checked against runtime.** `bench/trace.py` runs each fixture's own test suite under
  a profiler, records the calls that actually happen with the stack each was made under, and
  `tests/test_ground_truth.py` fails if any expected answer disagrees with that trace. An impact answer
  is read from the stacks, not from a closure over caller and callee pairs, which would also credit a
  chain no run takes (two functions sharing one wrapper). A caller no test runs through is listed apart,
  as `unexercised`, with the reason. The tracer shares no code with ripple.
- **It is small.** 63 queries over four fixtures I wrote, each query aimed at one resolution rule or
  one known limit. It shows which rules work, not what recall looks like on your codebase, where
  more of the code is likely to be untyped and dynamic.
- **Precision is 1.000 everywhere, recall is not.** ripple reports a call only when the source proves
  it, so every miss is a call it left unresolved (see "What the benchmark still misses" below), never a
  wrong edge. The benchmark does not cover a decorator ripple cannot read: there the decorated function
  is treated as called directly (see "Decorators" above), which is a design choice, not a proof.

## Agent eval

The benchmark checks that ripple's answers are right. The eval checks whether a coding agent does
better with them: nineteen refactoring tasks (ten on small fixtures, nine on real repositories), each run by Claude
Code in headless mode, once with a ripple MCP server and once without, three times each. Nine more, over
TypeScript repositories, have had one full run of the same shape (see "TypeScript tasks" and Results below).

- **The tasks** (`eval/tasks/`) are renames and signature changes where grep is noisy or
  incomplete: a method named `total` on four classes, a function reached only through a package
  re-export alias, an override in another package, a `getattr` lookup that neither grep nor ripple
  can see. Seven run over `eval/fixtures/ledger`, a small invoicing app written for the eval, and
  three reuse the benchmark fixtures. Each task's `why` field says what makes it hard.
- **Grading.** An edit counts as a success when the fixture's own tests pass against the agent's
  source and still number at least the task's minimum (`min_tests`), and a hidden test the agent never
  sees passes too. The tests that run are the starting repo's own, not the agent's copy of them (see
  "Graded the same way" below). The hidden test checks that the change was made and that every caller
  still works.
  `tests/test_eval.py` shows each check fails on the untouched fixture and passes on a reference
  solution, and CI runs it.
- **Same conditions.** Both modes get the same model, tools and permissions (edit files, run
  pytest) and the same prompt: a short note that the shell is only for running tests and edits go
  through the Edit or Write tool, then the task. The MCP server is the only other difference, plus
  one line in the with-ripple prompt saying the server is connected and naming its tools (it does
  not tell the agent to use them, so `ripple calls` is the agent's own choice). Each run starts
  from a fresh copy under `/tmp`, in a directory whose name says nothing about the task or mode,
  and leaves out the user's own settings, hooks and auto-memory.
- **Invalid runs.** A run is marked invalid and left out of the success rate, but counted and
  printed (per mode, per task and with its reason) in three cases: a with-ripple run whose ripple
  server fails to connect, a run that changed no file while an edit tool (Edit, Write,
  MultiEdit, NotebookEdit) was refused by the permissions, since the harness then blocked the very
  tool the prompt named, and a run the CLI refused before the agent did anything (an error result
  with no tool call and at most one turn, for example "You've hit your 5-hour limit"): reason
  `the agent did not run: <message>`. A usage or rate limit message also stops the batch at once,
  records it as `stopped_message` in the meta and prints how many runs were not started, so the
  batch can be re-run after the reset; a run cut off by the limit part way is `aborted` instead.
  Other refusals are routine and do not invalidate a run: an agent that
  tries `sed -i` despite the prompt, or a read-only command that is refused, and changes nothing,
  is a real failure. The result JSON keeps each run's refused tools and their inputs
  (`permission_denied`).
  Results from before this wording (the 2026-09-26 batch) used the bare task prompt.

### Tasks over real repositories

The first ten tasks sit at the ceiling (see Results), so nine more run over pinned, real open-source
repositories, where the agent can no longer read everything: [rich](https://github.com/Textualize/rich)
v15.0.0 (38k lines), [werkzeug](https://github.com/pallets/werkzeug) 3.1.9 (22k) and
[sqlglot](https://github.com/tobymao/sqlglot) v30.29.0 (86k, with a Dialect/Parser/Generator
hierarchy overridden across dozens of modules). They are in `eval/tasks/11-*` to `19-*`.

- **Pinned.** `eval/repos.json` names each repo by git URL and a full commit SHA (a tag or branch is
  refused), with the Python version and exact-version test dependencies. Nothing from these repos is
  committed or vendored: `python -m eval.fetch` downloads them into `$RIPPLE_EVAL_CACHE` (default
  `~/.cache/ripple-eval`), without `.git` and without the repos' own `CLAUDE.md` or `AGENTS.md`, and builds
  one virtualenv per repo with the test dependencies (the repo itself is never installed, so a run
  tests the agent's copy). Needs git and uv. `python -m eval.fetch --verify` also runs each repo's own
  suite and prints its summary line. A few test files are left out (they build C code, start a dev
  server that imports an installed copy, or take a minute): see `ignore` and `deselect` in `repos.json`.
  Only the top-level test dependencies are pinned to exact versions: their own dependencies (numpy,
  tzdata, pluggy and so on) resolve at fetch time and are not locked, so two fetches on different days
  can differ there. A venv counts as built only once its install finished (a `.ripple-ready` file in it),
  so re-run `python -m eval.fetch` after an interrupted fetch, or to upgrade an older cache.
- **Node repos.** An entry with `"runtime": "node"` names a test runner (`vitest` or `jest`) and a
  `package-lock.json` kept under `eval/lockfiles/<name>/`, because not every upstream commits one.
  `python -m eval.fetch` copies that lockfile into the checkout and runs `npm ci --ignore-scripts` (no
  package script ever runs) into `$RIPPLE_EVAL_CACHE/deps/<name>-<key>/`, where the key hashes the
  lockfile, and then makes that directory read-only. A working copy gets `node_modules` as a symlink to it,
  never a copy, and vitest runs with `--no-cache` because before vitest 4 it writes a results cache into
  `node_modules`. Needs npm; `--verify` runs the suite from a scratch copy and prints the runner's
  `Tests` line.
- **Hard on grep.** Each task is a rename or a parameter rename whose true callers are known and spread
  over 4 to 20 files, and each goes through something that defeats grep: a same-named method on another
  class (`Style.parse` next to `Color.parse`), a decoy that must not change (`Dialect.format_time` and a
  module function beside the generator method being renamed), an override in another module, a call
  through `self` or `super()`, an alias or a function-local import, a function passed as an argument, a
  subclass in a test or an example no test runs. Each task's `why` gives the caller count and the files.
- **Graded the same way.** The agent's tests are not the ones that grade it. When the agent finishes,
  its copy is copied again and the starting repo's own tests directory is put over it, with the reference
  solution's edits to those tests (the prompts name every new name and signature, so they are a fair
  oracle), along with every `conftest.py`, `pytest.ini`, `tox.ini`, `setup.cfg`, `pyproject.toml`,
  `sitecustomize.py` and any module added at the root of a source path that shadows pytest or the standard
  library. The whole suite then runs against the agent's source: it must pass, every test that passes on the
  reference solution must pass (a test that skips itself on the agent's source is caught), and at least
  `min_tests` must run. Deleting, gutting, skipping or conftest-patching a test therefore changes nothing;
  an agent that deleted or hollowed out a test the reference edited is also marked down (the identifiers
  the reference added to that test function must still be in the agent's). A hidden test passes too, run
  with no conftest over the agent's own files. The hidden tests scan every
  `.py` file with `ast` for stale uses of the old name (including examples and Windows-only code the
  suite never runs, tests the agent left on the old name, and a file that no longer parses is a failure,
  not a skip) and then exercise the callers; task 18's also checks the parameter reads inside the method
  bodies. Grading costs one reference run per task per process (cached) plus the hidden check and one full
  suite run per graded run, measured here at about 7 seconds per run for rich and werkzeug and about 40 for
  sqlglot (plus roughly 30 once per process for the reference run). What this cannot cover: anything the agent writes outside its copy
  (the venv is shared by every run), and source it changes to patch pytest itself at import time; the diff of
  every run is saved for that.
  A reference solution for each task is kept as a `solution.patch` (a diff, never upstream files).
  `tests/test_eval.py` shows each check fails on the untouched repo, passes on the patch, and fails
  again when the patch leaves out any one file of its callers. Those tests are skipped when the
  repos have not been fetched, so CI (which does not fetch them) stays green; run them locally after
  `python -m eval.fetch`.

```sh
.venv/bin/python -m eval.fetch --verify        # once: fetch the pinned repos, run their own suites
.venv/bin/python -m eval.run                   # 19 tasks x 2 modes x 3 repeats = 114 runs, into eval/results/
.venv/bin/python -m eval.run --tasks 11-rich-rename-style-parse,12-rich-rename-cell-len,13-rich-rename-highlighter-highlight,14-werkzeug-rename-http-date,15-werkzeug-rename-mapadapter-match,16-werkzeug-rename-get-response,17-sqlglot-rename-generator-format-time,18-sqlglot-rename-parse-table-parts-schema,19-sqlglot-rename-schema-column-names   # only the real repos: 54 runs
.venv/bin/python -m eval.run --tasks 04-ledger-rename-invoice-total --repeats 1 --out /tmp/smoke.json
```

Each batch writes `eval/results/<date>-<time>-<id>.json` (unless `--out` says otherwise), a `.md`
table (headed by an `arm: <label>` line, as is the printed summary; a results file from before the arm existed has
none) and a `-diffs/` directory beside it, and refuses to overwrite an earlier batch's files.

It calls the `claude` CLI on whatever account it is logged in to, so it never runs in CI.

### TypeScript tasks

Nine more tasks (`eval/tasks/20-*` to `28-*`) run over four pinned TypeScript repositories. They have had one full
run (54 runs, three repeats per task and arm; see Results). The harness was first proven with a fake agent (see
below). The grader (`eval/check_node.py`) is the same design as the Python one: the repo's own runner over the
agent's source with the reference's tests put back, plus a hidden TypeScript test.

| Task | Repo (runner) | What is renamed | What makes it ambiguous |
|---|---|---|---|
| 20 | neverthrow (vitest) | static `ResultAsync.combine` | `Result.combine` keeps its name; 49 of the 54 calls are in a file only `tsc` reads |
| 21 | neverthrow (vitest) | instance `ResultAsync.orElse` | three look-alike `orElse` methods; the receiver's type is the only clue |
| 22 | superstruct (vitest) | exported function `assert` | `Struct.assert` keeps its name; most uses are in typing tests and examples no test run reads |
| 23 | superstruct (vitest) | exported function `create` | a method and a boolean `create` flag in 19 fixtures share the name |
| 24 | tsyringe (jest) | interface member `Transform.transform` | `TransformDescriptor.transform` keeps its name, in the same expression; some implementers never write `implements` |
| 25 | tsyringe (jest) | decorator `registry` | applied as `@registry(...)`, never called by name; module paths and a `registry` variable keep the word |
| 26 | react-pro-sidebar (vitest) | component `SubMenu` | 237 identifiers plus code samples the website builds as strings; the module and test titles keep it |
| 27 | react-pro-sidebar (vitest) | hook `useMediaQuery` | a test names the export in a string (`vi.spyOn(..., 'useMediaQuery')`); module specifiers keep it |
| 28 | react-pro-sidebar (vitest) | the `Menu` prop `transitionDuration` | seven properties of that name; only the Menu's changes |

**What ripple answers on them**, from the task `why` fields and the checks made when the tasks were written (not
re-measured when the harness was wired; 24 and 25 were measured again on the pinned checkout after ripple began
to flag decorations and the calls inside class expressions). Where it falls short is part of what the eval can show:

- 20 and 21: complete on the symbols checked.
- 22: 77 of the 83 calls, with no false one. The 6 it misses are in `examples/*.js`, which import the package by
  its own name. 23: 4 of 5.
- 24: callers resolved 0 of 6. The member is declared on an interface, which ripple does not index, so no symbol
  has that name to ask about: `transform` is ambiguous between the methods of the classes that act as transformers.
  Three of the six calls are inside the class expression `autoInjectable` returns, which is not read. Those three used to
  be absent from every answer, and the same-name list found 3 of the 6. Now the 20 calls in that body are unresolved
  calls, and `impact` of a transformer's `transform` method lists all 6 under `possible_missed_callers`, the three
  from the class expression marked "inside a class ripple does not read" (`callers` lists those three). No call is
  resolved from that body, so callers still resolve 0 of 6.
- 25: `callers` and `impact` of `registry` return 0 callers, which is right: it is never called by name. They now
  also list the 12 places it is written as `@registry(...)` (3 files: 10 in `global-container.test.ts`, 2 test
  fixtures), under `decorations` with the note that a decorator's effect is not modelled. The word still appears
  in module paths, a `registry` variable and the README, which ripple does not index.
- 26: `renders` finds 84 of the 108 elements with precision 1.000 (the website demos import the package by name);
  `renderer` finds 0 of 108. 27: 6 of 6.
- 28: a prop rename. ripple does not index props.

**Fetch.** `python -m eval.fetch neverthrow tsyringe superstruct react-pro-sidebar` (or no names, for every repo)
needs git, node and npm and the network. It installs each repo's dependencies with `npm ci --ignore-scripts` from the
lockfile vendored under `eval/lockfiles/`, into a read-only cache outside this repository.

**Run.** `--runtime` picks the tasks and defaults to `python`, so a plain `python -m eval.run` is the same 19 tasks,
the same command line and the same cost it always was. `--runtime node` selects tasks 20 to 28, `all` selects both,
and `--tasks` runs exactly the ids it names (a node task by id needs no `--runtime`; a mixed list is fine). For
the with-ripple arm on a node task, `--ripple-python PATH` names an interpreter that has ripple with the `typescript`
extra (`pip install 'ripple-code[typescript]'`, which brings tree-sitter); it starts the server for the node tasks
only. A Python task in the same batch always starts the `ripple` next to the interpreter running `eval.run`, as it
always has, and a batch with no node task ignores the flag (one line says so, and the results file records it under
`ripple_python` with `ripple_python_ignored`). The default is the interpreter running `eval.run`, and a node run that
cannot index TypeScript with it is refused. `--dry` prints the plan and runs the readiness checks without
starting an agent or calling a model:

```sh
.venv/bin/python -m eval.run --runtime node --ripple-python /path/to/venv/bin/python --dry
.venv/bin/python -m eval.run --runtime node --ripple-python /path/to/venv/bin/python --repeats 1 --out /tmp/ts-smoke.json
.venv/bin/python -m eval.run --tasks 20-neverthrow-rename-resultasync-combine,24-tsyringe-rename-transform --modes without --dry
```

**What the agent has.** The same shape as the Python runs, with node's commands. The shell allows exactly the
repo's runner (`vitest` or `jest`) and `tsc`, and each is a small script in the run's own directory, not the binary:
- The runner script runs what the grader runs (`RepoSpec.node_argv`, in the same environment: the repo's own binaries
  first on `PATH`, `CI=true`, no colour, vitest with `--no-cache` because before vitest 4 it writes a cache into the
  read-only `node_modules`). With no arguments it runs the repo's whole suite exactly as the grader does, so the
  agent sees the pass count the grader sees; with file paths or `-t` they go after the same flags. Snapshot updates,
  report files, coverage output, watch mode and `init` are refused.
- `tsc` is for the type check only: `--noEmit` is forced and only `-p`/`--project` and a few read-only flags pass,
  so it cannot emit, build, init a config or write a build-info file. neverthrow's 49 `ResultAsync.combine` calls
  live in a file that only `tsc` reads, so `tsc` is the only way the agent can compile-check that task's stale calls.
- `npm test`, `npx`, `npm run`, `npm exec` and `node` are not allowed: each can run any script or any code, which
  reopens the sed, xargs and `node -e` rewrite channels the Python run closes. The repos' own `npm test` scripts also
  differ and some write (superstruct's builds into `dist/` first, tsyringe's runs `yarn lint`, neverthrow's runs `tsc`
  after vitest, and none passes `--no-cache`).
- The prompt's first paragraph is the node version of the Python one (it names `vitest run` or `jest` and `tsc
  --noEmit -p <tsconfig>`, and says to edit with the Edit or Write tool), the same in both arms.
- With ripple, the server is started with `--lang typescript` and a `--test-command` that is the runner script, so
  the `check` tool runs the same command; a TypeScript repo has no default test command and `check` would otherwise
  refuse. The ripple index skips `node_modules`, which is a link to the cache in the agent's copy.
- `changed_files` and the saved diff for a node task cover `.ts .tsx .js .jsx .mts .cts .mjs .cjs .json .md .mdx`
  files, never `node_modules`.
- A with-ripple node run whose server did not connect is invalid, as for Python.

**What a run records of ripple.** Besides the counts (`tool_calls`, `ripple_calls`), every run record carries
`ripple_exchanges`: for each `mcp__ripple__*` call its tool, its input (clipped like a denied tool's) and, from the
matching result, `is_error`, `result_chars` (the whole text) and `result_head` (the first 2000 characters). A call the
run ended before answering has `null` for those three. `toolsearch_queries` lists the inputs of every `ToolSearch`
call. The two share a 40 KB cap, the first ones kept, and `ripple_exchanges_truncated` says when it cut. The batch
table gains a "runs with a ripple error" column when the runs recorded exchanges. These keys are what shows whether
an agent that called ripple got an answer it could use (the earlier TypeScript batches recorded counts only).

**Test arms.** Three flags, all off by default; with none set a batch is exactly what it was
(`tests/test_eval_arms_pins.py` pins the command, prompts, tool lists, mcp config, shims and record to literals from
before they existed). They combine, and the arm is a label (`base`, `forced`, `hard`, `restricted`, joined with `+`
in that order) in the results `meta` and in every run record, so a results file says which arm it is. `--dry` prints
the arm, the text the agent is told before the task for each runtime and mode, and the allowed and disallowed tools.
Code in `eval/arms.py`.
- `--forced-use`: the with-ripple prompt gains one paragraph after the line that says the server is connected: "Before
  editing, call the ripple `callers` tool on the symbol you are changing and use the call sites it returns." The
  without arm is unchanged. Python and node tasks (not every Python task is a rename, so the sentence says
  "changing").
- `--hard-prompts`: each task's `hard_prompt` (an optional `task.json` field; tasks 20 to 28 have one) replaces its
  prompt, in both arms. It is the same task with no file paths, no lists or counts of sites and no hint of where uses
  live, but every rule about what keeps its name, "update every use", the no-alias rule and the suite rule are kept,
  and the four tasks whose hidden check reads documentation (24, 25, 26, 28) still say, in general words, that the
  documentation is part of the rename. A Python task selected with it is refused before anything starts.
  `tests/test_eval_hard_prompts.py` lists, per task, the phrases a hard prompt must keep and bans paths and counts.
- `--restrict-search`: both arms lose the Grep and Glob tools, `LS`, the web tools and the shell commands that search
  (`grep`, `rg`, `find`, `git grep`, ...: Claude Code lets read-only shell commands through without a rule, so denying
  the tools alone leaves the shell as a search channel; a deny rule beats the allow rules). A node task also loses
  its `tsc` script and `Bash(tsc:*)` rule, so the shell holds the runner only. The preamble names only the runner
  (`pytest` for Python) and says search is not available. A restricted run lists its shell commands under
  `bash_commands` (the first 60, 160 characters each), so a search that got through shows in the record.
  **Known open channel:** the arm does not remove search. The runner stays allowed and the agent can Write, so it can
  write a test file that walks the tree and prints the lines it wants, then run that file with `pytest`, `vitest` or
  `jest`. Nothing here stops it. It shows only after the fact: `bash_commands` lists a runner call on a file the agent
  just made, and the changed-files list holds that new test file. Read both before treating a restricted run as one
  that did not search.

**Checked before any agent starts** (in `--dry` too): node and the runner's and `tsc`'s binaries are in the install; and,
when the with-ripple arm is selected, the ripple interpreter imports ripple, tree-sitter and its TypeScript grammar,
and for each node repo the server (started with the config the runs use, on a scratch copy) lists its five tools and
answers a query without a `warnings` list. Any failure stops the batch with a message and nothing started.

What this does not prove: the shell rules were tested through the scripts and a model of Claude Code's
`Bash(prefix:*)` matching, not against a live agent, so how the CLI treats shell redirections under those rules is
assumed to match the Python runs. The fake-agent tests apply each task's reference solution through the same scripts,
`PATH`, linked `node_modules` and diff recording a real agent gets, and the grader marks them successes; that shows
the environment works, not that an agent can use it.

### Results

First full run, 2026-09-26, Claude Sonnet 5, 60 runs (`eval/results/2026-09-26.json`):

| Mode | Success | Median input tokens | Median output tokens | Median turns | Median wall time |
|---|---:|---:|---:|---:|---:|
| with ripple | 30/30 | 145,461 | 3,372 | 19.5 | 25.2 s |
| without | 30/30 | 129,108 | 3,211 | 15.0 | 22.9 s |

**ripple did not help on these tasks.** Both modes solved every task every time, so there is no
difference in success to measure, and with ripple the agent spent about 13% more input tokens and
four or five more turns, mostly on loading and calling the tools (78 ripple calls across the 30
runs).

The likely reason is the tasks, not the tool: the fixtures are a few hundred lines, small enough
that the agent can grep and read everything it needs, which puts both modes at the ceiling. The
cases where a call graph should matter (a large unfamiliar codebase, callers the agent never opens)
are exactly the ones this first task set does not have. The next version of the eval needs tasks
over bigger, real repositories, where the without-ripple agent has a real chance to miss a caller.

In this run each arm's working directory name included its condition label (such as
`--with-ripple--`), which the agent can see; that is fixed now, and the result is unlikely to depend
on it since both arms scored 30/30.

TypeScript tasks, 2026-11-09, Claude Sonnet (`--model sonnet`), nine tasks, three repeats per task and arm, 54 runs,
ripple at `5147bf7` (`eval/results/2026-11-09-typescript.json`):

| Mode | Success | Median input tokens | Median output tokens | Median turns | Median wall time | Median cost-equivalent |
|---|---:|---:|---:|---:|---:|---:|
| with ripple | 27/27 | 247,566 | 5,105 | 26 | 43.8 s | $0.155 |
| without | 27/27 | 218,666 | 5,250 | 29 | 38.9 s | $0.146 |

Both arms solved every task every time, so again there is no difference in success to measure. With ripple the
agent used about 13% more median input tokens and took slightly longer, but not more turns (median 26 against 29).
The whole batch cost $13.46 in cost-equivalent terms and took 74 minutes.

The agent barely used ripple. It called a ripple tool in 11 of the 27 with-ripple runs, 12 calls in all, and in none
of the 15 runs on tasks 24, 26, 27 and 28 (one run of task 25 called it twice). So this compares an agent that has
the tool with one that does not, and says nothing about whether ripple's answers help when they are used. Both arms
can run `tsc --noEmit` and the repo's test runner; I did not measure how often they used `tsc`, which finds stale
uses in code the suite never runs.

An earlier one-repeat look at the same tasks (9 of 9 against 8 of 9) is not part of these numbers: the one failure
was a task 26 prompt that let the agent rename a runtime warning text the suite asserts, fixed before this run.

#### Tool-use test arms

Because agents called ripple in only 11 of the 27 with-ripple TypeScript runs, four more batches were run on
2026-11-09 to find out why (records in `eval/results/2026-11-09-tool-use-arms/`, same model, three repeats per task and
arm, about $47 in all):

| Arm | What it changes | With ripple | Without | Ripple use (with-ripple runs) |
|---|---|---:|---:|---|
| P0: Python tasks 01 to 10 | nothing (current model, neutral working directory) | 30/30 | not run | 29 of 30 runs, 62 calls |
| F: `--forced-use`, TypeScript | the prompt tells the agent to call `callers` before editing | 27/27 | not run | 27 of 27 runs, 33 calls |
| H: `--hard-prompts`, TypeScript | prompts with no file paths or lists of sites | 25/27 | 27/27 | 7 of 27 runs, 11 calls |
| R: `--restrict-search`, TypeScript | no Grep, Glob, LS or web; shell search commands denied; no `tsc` | 23/27 | 27/27 | 27 of 27 runs, 53 calls |

- **P0.** Python agents still called ripple in nearly every run (30 `impact`, 32 `check`), as in the first run, so
  the TypeScript gap is not the model or the old working-directory label. Python `impact` returns pytest ids that
  feed `check`; TypeScript has no such ids. As graded, P0 task 05 failed 3 of 3 because the agent renamed a test
  function and the grader keyed tests by name; that grader bug is fixed, and the three real diffs replayed through
  the fixed grader pass, so 30/30 is after a regrade by replay, not a re-run.
- **F.** When told to, every agent called ripple and the tool path worked (no tool errors). But 11 of the 33 answers
  were "no symbol named ..." errors, on tasks 24, 26 and 28, where the agent wrote an id such as
  `src/components/SubMenu:SubMenu` for a symbol that is `SubMenuFR`. Four runs tried again, and none of the nine
  runs on those three tasks got a usable answer for the symbol being renamed.
- **H.** Short prompts did not raise usage, and the two with-ripple failures were not the same cause: one agent used
  `impact`, which gives one entry per module and no call-site lines, and left 4 stale calls in a file only `tsc` reads;
  the other made no ripple call and missed 11 uses in a test file after its `tsc` and `jest` commands were blocked.
  25/27 against 27/27 is not a significant difference.
- **R.** With search taken away, agents with ripple cost about 35% less (median $0.261 against $0.403 per run, 379k
  against 643k input tokens) and passed less often (23 of 27 against 27 of 27). Each of the four with-ripple failures
  is a site ripple cannot see: `examples/*.js` that import the package by its own name (tasks 22 and 23), the website
  demos (task 26), and a prop (task 28). The pattern fits an agent that stops when the list ends, but four failures
  against none is not significant (Fisher exact p about 0.11).

Read these with their limits. Cells are three runs each. Eleven R runs were lost to the account's usage limit
(ten ended after one turn, one at turn 39), the harness did not flag them, and they were excluded and re-run, with
the ten re-runs recorded as repeat 1. The R restriction leaked: in 11 of the 54 valid runs (7 with ripple, 4 without)
a shell command naming `grep`, `awk` or `find` ran, because a deny rule does not see a search word inside a compound
command that starts with an allowed one; on the 43 runs with no leak the result has the same shape (with ripple 17 of
20, without 23 of 23). R is "mostly restricted", not ripple-only, and an agent can still search by writing a test file.

## Known limits

Static analysis of a dynamic language misses things. ripple does not follow:

- method calls on values of unknown type: unannotated parameters, loop variables, results of
  functions without a return annotation (reported as unresolved, with the reason)
- `getattr`, registries that are added to, a dict or list looked up by a variable key or whose contents are not
  plain function names, callbacks passed as arguments, a `functools.partial` that is not bound to one name
  (passed on, stored in an attribute, or built by a call)
- decorators ripple cannot read: for those the decorated function is treated as called directly, so a
  call of it is not shown as a call of the wrapper. That covers a decorator from outside the repo
  (`lru_cache`, a framework's `route`), a decorator that is itself decorated, a generator or async
  decorator, and a function wrapped by hand (`f = timed(f)`). A class decorator is never read: the class
  keeps its name, and a decorator that may return something else than the class (a subclass, a wrapper)
  is not followed. What ripple does is stop calling a value built from such a class exactly that class,
  so its subclass overrides stay linked (see the exact-class rule below). It does not add an edge to the
  class the decorator builds, which it cannot see. See "Decorators" above for the full list
- calls ripple leaves unresolved on purpose, where it could have guessed:
  - a method on a value annotated with a class from outside the repo (`logging.Handler`) when a repo class that
    may inherit from it overrides the method. A repo class's base is compared with the annotation by what the
    interpreter says for standard library classes, and a dependency's class or a base ripple cannot read is
    never ruled out, so the call stays unresolved and `impact` lists it under `possible_missed_callers`
  - a call result through a decorator ripple cannot prove returns what the def returned, and through a function
    with a def that has no annotation among several (see "Decorators")
  - every table and partial while any file of the repo has a store word (`setattr`, `patch`, `monkeypatch`,
    `vars`, `exec`, `importlib`, `sys.modules` and the rest of the list in "Tables and partials, and the store words"),
    or while the repo has a file
    ripple cannot parse
- `__call__`, metaclasses, monkeypatching, `importlib`, `exec`, and a call on the result of a function
  with no return annotation naming a class (`make().run()` is followed when `make` has a return
  annotation or is a class; ripple does not read `return Foo()` to infer one, and an `@overload`ed function
  or two defs of one name that annotate differently, or only one of which is annotated, in either order
  (a def in each branch of an `if sys.platform`), give no type)
- calls written inside a string annotation (`x: "Annotated[int, Field(gt=0)]"`): the string is not
  parsed. Calls in an ordinary annotation are recorded (see above); the annotation of a local variable in a
  function (`x: Annotated[int, Field()] = 1` inside a def) is never evaluated by Python, so it is left out
- syntax the Python running ripple cannot parse (`except*` before 3.11, `type X = ...` and `def f[T]()`
  before 3.12): that file is listed as unparsable and left out. So are files with invalid bytes or a
  null byte, and files nested so deeply that the parser itself gives up (hundreds of thousands of terms).
  Such a file may rebind a table or a partial where ripple cannot see it, so while the repo has any, no
  table or partial is followed, and the unresolved call names the file
  Generated code nested far deeper than ordinary code (an `elif` chain of 1,500 branches) is indexed on
  Python 3.12 and later, where ripple raises the recursion limit while it indexes. Before 3.12 it leaves
  the limit alone, because another thread recursing without bound would then crash the process, so a file
  nested deeper than the default limit allows is listed as unparsable
- code imported under a name that differs from its path in the repo (modules are named by their
  path from the repo root, with a leading `src/` dropped; pydantic's `pydantic_core` lives under
  `pydantic-core/python/`, so about 8,000 calls into it count as outside the repo). Two files that get the same name
  (`tests/test_x.py` and `src/tests/test_x.py`) share one set of symbol ids, so a name defined in
  both is one symbol; `ripple index` warns when that happens and names the files.
- directories that hold dependencies or build output: hidden directories, `__pycache__`,
  `node_modules`, `site-packages`, `venv` and any directory with a `pyvenv.cfg` anywhere, any directory a
  `build`, `dist` and `env` at the repo root only (a directory a `tsconfig.json` names as `outDir` is skipped for TypeScript and
  JavaScript, never for Python). `ripple index` lists the skipped directories, except
  hidden ones and `__pycache__`.
- custom pytest collection settings (`python_files`, `python_functions`, `python_classes`): test node
  ids follow pytest's defaults. ripple sees only the repo's own classes, so a class counts as a
  `unittest.TestCase` subclass when it has a base from outside the repo whose name ends in `TestCase`
  (`unittest.TestCase`, `django.test.TestCase`), and an `__init__` inherited from outside the repo
  is not seen.

Override edges can over-approximate: a subclass override is linked even if that subclass never
reaches the call site. The exception is a variable whose every assignment is a call of one class
named directly (`field = Field()`, not `self.Field()`, which a subclass may redefine). It is exactly that
class, so no override is linked, but only when ripple can prove the call builds it: no class in its
repo hierarchy defines `__new__` or declares a metaclass (either can return a subclass, as anyio's
`Event.__new__` does), every base resolves, and every decorator on the class and on its bases hands the
same class back. A class decorator binds the name to whatever it returns, so one that is a repo function
or class, or that ripple cannot resolve, may return a subclass: the class is not exact. Outside the repo
only a short list is trusted: `dataclasses.dataclass` (bare or called), `attr.s`, `attr.attrs`,
`attr.attributes`, `attr.define`, `attr.mutable`, `attr.frozen`, `attr.dataclass` and `attrs.define`,
`attrs.mutable`, `attrs.frozen` (bare or called, imported from `attr` or `attrs`), and, written without a
call, `functools.total_ordering`, `typing.final` and `typing.runtime_checkable`. Any other decorator
from outside the repo (`six.add_metaclass`, `pytest.mark.x`) leaves the class not exact. A repo
decorator that only returns its argument is not told apart from one that returns a subclass, so it costs
the class its exactness too. Otherwise override edges stay. Method lookup walks bases depth-first, which
matches Python's C3 order except in diamond-shaped hierarchies.

To keep the blind spots visible, `callees` lists every unresolved call with its reason, and `impact`
lists unresolved call sites that use the same name as the target under `possible_missed_callers`.
A call counts as "code outside the repo" only when it must be: a method missing from a class's repo
bases is inherited from an external base such as `Exception`, unless a base could not be resolved
(`class Cached(make_base())`) or a subclass defines the method, and then it is unresolved too.
Subscripted bases such as `Repo[int]` or `Generic[T]` resolve to the class they subscript.

A method called on a value of a class from outside the repo is outside the repo too, and `impact` no
longer lists it as a possible missed caller, but only when ripple can prove the receiver's class:
a builtin value; a plain class of the standard library (`deque()`, `Path(...)`, `x: threading.Event`,
`self.ready = threading.Event()`, checked by asking the interpreter about a short list of modules that
are safe to import); or a class some class in the repo inherits from, since only a class can be a base.
It stays unresolved when a repo subclass overrides that method and the value is not built in place
(an annotation `buf: deque` may hold the `LoudDeque` that overrides `extend`), when the name is not
proven a class (`session = requests.Session()` where nothing in the repo inherits from `Session`, or an
annotation such as `Any`, a protocol or an ABC), and one attribute further (`local.chal.get()`: what is
stored on an object from outside the repo may be a repo object). An unresolved receiver says why in its
reason (an unannotated parameter, a loop variable, the result of a call to a function without a return
annotation, ...), and `ripple index --reasons` counts unresolved calls and calls outside the repo by cause.

### What the benchmark still misses, and why

`python -m bench` lists every query it does not answer exactly. Each remaining miss needs something ripple
does not do, and none of them is a bug that more rules would fix without guessing:

- **Receivers whose class only a type inferrer or the running program knows.** The loop variable over a list
  with no element type (`for item in self.items: item.subtotal()`), the parameter without an annotation
  (`store.search(...)`), the value returned by a function without a return annotation (`best_discount()`,
  `pick_three()`), and a lambda over an untyped list. Closing these needs return-type inference and
  element-type inference, and ripple reads annotations and constructor calls only. The `impact` queries
  that hang on one of these hops inherit the miss, since every caller further up the chain is missed too.
- **Names built at run time.** `getattr(module, "cmd_" + name)`, `getattr(self, "on_" + verb)` and a table
  looked up by a variable key. The target is a string value that exists only when the program runs.
- **A table something changes.** A registry that is added to after it is built (`REGISTRY["b"] = ...`)
  does not fix what a key finds, so `REGISTRY["a"](x)` stays unresolved. Only a table no code in the
  repo can change is read (see "Dispatch tables"). Proving a mutation does not matter would need
  knowing the order the code runs in.

In every case ripple reports the call as unresolved with its reason and, for `impact`, lists it under
`possible_missed_callers`, rather than choosing a target.

## Next

- **TypeScript, further**: jest and vitest collection, so that `impact` lists the tests that reach a symbol and
  `check` can run them.

## Development

```sh
uv venv && uv pip install -e ".[dev,typescript]"
.venv/bin/python -m pytest -q      # unit tests, plus the ground truth against its runtime trace;
                                   # the TypeScript tests are skipped, with the reason, without the extra
.venv/bin/python -m bench          # the benchmark; --write-baseline after a deliberate change
```

MIT licensed. Copyright (c) 2026 Dylan Fodor.
