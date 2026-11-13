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
| `impact(symbol, depth=5, limit=100)` | Every transitive caller grouped by file, the tests that reach it listed separately as pytest node ids, and same-named call sites ripple could not rule out | yes |
| `path(source, target)` | The shortest call chain from one symbol to another | yes |
| `check(tests=None)` | Runs the test command (default `python -m pytest -q`, using the repo's `.venv` or `venv` if present, for a repo with Python files; a TypeScript-only repo, or `--lang typescript`, has no default), or just the given test paths (for Python, pytest node ids too), and returns pass/fail, the summary line and the first failures, never the full log | no, it runs code |

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
from `calls`, `renders` and `renderer`, comma separated, default `all`. Only a TypeScript repo with JSX has the last
two (see [JSX](#jsx)); in a Python repo every edge is a call and nothing is labelled.

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
`npx vitest run`), with the refusal as an ordinary error. In a mixed repo `check` runs pytest by default, unless
the server was started with `--lang typescript`. It takes TypeScript test file paths only: ripple's own test ids
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

**Proven**: a function called by name or through an import (go.mod, `replace` to a local directory and go.work say which
directory an import names; a module of the repo that is neither replaced nor in the workspace is built from its published
version, so it counts as outside), and a method called on a value whose type is written in the source (receiver, parameter,
`x := T{}`, `NewT()`, a field, an element, a range variable, a type assertion, embedded structs by the language's depth
rule; a method declared on an alias is its target's, and keeps the alias's name). **Unresolved, each with its reason**: an
interface method call (the reason names the interface), function values (a parameter, a local, a field, a package variable),
type parameters, a value whose type is not written, anything that depends on which of several build-constrained definitions
wins. Build constraints are never evaluated: a function defined under several has an edge to each variant, and a variable, a
type, a result that the variants read differently, a method that only some builds declare (a `//go:build` line, a `_linux.go`
name, a `_test.go` file) while another is promoted, or a method declared on an alias that is itself declared twice, is
unresolved.

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
external when the file's text declares a method of that name. `check` has no Go test runner yet: pass `--test-command`.

`bench/fixtures/go/probes` holds 131 small legal Go modules, each with what the Go compiler's type checker says every call
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
**A `[patch]` or `[replace]` is never an edge; it makes a dependency unresolved (Rule P).** Whether cargo applies a patch depends on more than a
manifest reader can follow: the source it patches, the version it has against the requirement, a `Cargo.lock` that pins the dependency, which
root governs the package, `members` against `exclude`, a `package.workspace`, a git dependency with no version taking a pre-release, a config
that is read only from below its `.cargo` (each built with cargo, the probes in `bench/fixtures/rust-probes/crates/_patch_source` pass only when
cargo does what they say), so ripple does not decide it. A dependency that is not a `path` dependency is unresolved, never an edge through the
patch and never external, when it may be redirected: (1) its package name (the dependency's key, or its `package =`) is named by any
`[patch.<anything>]` or `[replace]` entry ripple can read in the analysed directory, in any manifest (a root, a member, a lone package, an
excluded one, a member's own that cargo ignores, one in a directory with no source file) or any cargo config of it (also a config's `paths`
override), whether or not cargo would apply that entry (a name is a word of a `[replace]` key, so `foo`, `foo:1.0.0`, `url#foo:1.0.0`); a patch,
replace or `paths` table ripple cannot parse names every dependency; or (2) a manifest that may govern the package was not read (a
`package.workspace` naming one outside the analysed directory, a Cargo.toml above the analysed directory, one that cannot be read, a config that
cannot be read) and the name is that of a package of the repo, which an unseen patch could point at. Otherwise it is the fetched crate, outside
the repo. A `path` dependency is unchanged. What cargo is given from outside the repo (a patch in `~/.cargo/config.toml` or a `--config`) is not
seen, which is the assumption below.
**Unresolved, with the reason:** every method call `recv.m()` (what it calls depends on the receiver's type, which is not
followed yet: `impact` lists same-named ones under `possible_missed_callers`), a name two definitions share (cfg twins),
a name a pattern or closure binds, a call inside a macro other than the standard ones, a glob from outside the repo in a
block, a generic parameter used as a path (`T::new()`), any path that goes through the `use` items of a crate of edition
2015 (and every call written in one), a file that several crates share (`mod common;` in each integration test) that
they do not resolve alike, and any name looked up in a scope where a macro may define names (a module or a block that
invokes a macro other than the standard ones that define nothing, or an attribute macro): the macro's expansion is not
visible, so it can neither be proven absent nor chosen between. The same goes for what a `#[cfg]` or `#[cfg_attr]` guards
(ripple evaluates no condition, so a definition that may not exist is not chosen over what would stand in its place), and
for an item under an attribute that is not a built-in one (`#[tracing::instrument]`, `#[pm::attr]`, any attribute macro, bar the three below): calls
inside it and the name it declares are unresolved, though a name beside a derive that no `use` brings in is that derive's
helper (`#[serde(..)]`) and rewrites nothing. A derive can still emit items beside the type, so a derive ripple does not know (`#[derive(Serialize)]`), or a standard derive's
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
`check` has no default command for Rust yet (pass `--test-command`).

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

## Java

**Experimental.** Java is the newest front end and the least proven. Its answers agree with javac on every call site of
the five source trees it was checked on (see "Checked against the compiler" below), but its resolver went through two
rounds of fixes for wrong edges in legal Java those trees did not contain, and the JDK table it reads is new.
Read its unresolved reasons, and report an edge that javac would not make.

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
Lombok and annotation-processor output, qualified inner-class creation (`outer.new Inner()`), `check` for Maven and
Gradle (a Java repo has no default test command yet, and no test is collected), and JDK 25 syntax the grammar does not
know (those files are listed with the ones that could not be parsed, and read as far as the parser recovers).
A file whose directory does not end in its `package` path is not indexed and is listed with the reason (a Java file a test
compiles as data), and two files that define one fully qualified name (Android product flavours) are indexed as neither:
their types have no symbol, calls of them are unresolved, and `ripple index` warns that the files share a module name.

**Limits.** The JDK table covers JDK 8 through 27 (a later JDK is past its horizon, see above) and `java.base` and
`java.compiler` only: a type from any other JDK module (`java.desktop`,
`java.sql`) or from a library is unknown, so a call that a supertype of that kind could answer stays unresolved. A name that
an on-demand import supplies (`import java.util.*;`) has no package in ripple's reading, so it is unknown too. `check` has no
default test command for Java, and no test is collected.

**Checked against the compiler.** `bench/fixtures/java/probes` holds 185 small legal Java programs, each with `javac.json`:
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
rebuilds only when something changed.
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
