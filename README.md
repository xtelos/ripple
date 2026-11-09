# ripple

**Know what breaks before you change it.** ripple is an MCP server that gives a coding agent
(Claude Code, Cursor, anything that speaks MCP) the call graph of a Python or TypeScript repository, so the agent
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
| `check(tests=None)` | Runs the test command (default `python -m pytest -q`, using the repo's `.venv` if present), or just the given tests, and returns pass/fail, the summary line and the first failures, never the full log | no, it runs code |

A symbol can be a full dotted path (`shop.pricing.price_order`), a suffix (`OrderService.place_order`)
or a bare name (`price_order`); in TypeScript it is a path and a member (`src/util/math:clamp`, see
[TypeScript](#typescript)). An ambiguous name returns the candidates rather than picking one.

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

`--lang auto|python|typescript` (default `auto`; `RIPPLE_LANG` sets it for every command, and
`build_server` takes a `lang` argument) chooses how the repo is read. `auto` looks at the file names: `.py`
for Python, `.ts`, `.tsx`, `.mts` and `.cts` for TypeScript, skipping `.d.ts`, `node_modules` and virtualenvs.
A repo with neither, or one that does not exist, is an error (exit status 2), not "0 files".

**Symbols** are the file's path without the extension, a colon and the member: `src/util/math:clamp`,
`src/cart/cart:Cart.add` (an `index` file is its directory, a second declaration of a name under one parent
gets `#2`, a test callback is `tests/a.test:suite > case`). Any of these finds it, when it names one
symbol: that id, the path with its extension (`src/util/math.ts:clamp`), the tail of the path (`math:clamp`),
`Class.method` or the bare member. A path is read through the id the indexer gave that file, never folded by
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
`include` and `exclude`, each pattern relative to the config that wrote it, and a list the extending config writes
replaces the base's. `references` are followed: a config that does not cover a file hands it to the first project it
references that does (the layout of a Vite or shadcn app, where the root `tsconfig.json` is `"files": []` plus
references to `tsconfig.app.json` and the like), and that project's `paths` and `baseUrl` apply. A reference is a path
relative to the config that lists it: a `.json` path is that config and any other is a directory holding a
`tsconfig.json`. References are read four levels down, each config once (so a cycle ends), a path that names no
config is ignored, and a config does not inherit the references of one it extends. A file no config governs has no
aliases, so a bare import in it is a package, never a file of the repo, with one exception: when a config above the
file lists references and a config in that tree maps the specifier (a `paths` entry it matches, or a repo file its
`baseUrl` reaches), the import is left unresolved with the reason (which project owns the file is the open question),
and a call through it is listed as an unresolved call, not counted as code outside the repo. That is the
case that matters in a monorepo: an app the root `include` leaves out, which depends on a published copy of a package
the root `paths` maps to its own source, is not given the source (the app runs the published copy). The same rule
leaves a workspace package that an app names by its package name (`"pkg": "workspace:*"`) unresolved, since ripple
does not read `package.json` dependencies; that costs those edges, and none is guessed. A config that cannot be read
governs everything under it with no aliases and is listed with the files that did not parse. The JSX settings
(`jsxFactory` and the others, under JSX) are read from the root config only.

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
`npx vitest run`), with the refusal as an ordinary error. In a mixed repo `check` runs pytest by default.

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
    `props.children`, `{ children: kids }`) where it returns it the same way (or hands it to another component that
    does): `P` is the renderer, not the component that wrote the child;
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
differ on purpose (children placed by a wrapper, a render prop). Read by hand against the source, without a runtime
recorder, on four real libraries (react-pro-sidebar, react-hot-toast, react-accessible-accordion,
react-error-boundary): 69 `renderer` edges, grouped by the pattern each is written in, and well over 20 `renders` edges,
none found false; the edges ripple left out there are the cost of not guessing, such as children handed to a
component that spreads its props.

**Both languages in one repo** are indexed each with its own profile and merged into one graph; the cache
keeps one entry per language (the Python one is named as it always was, so a Python repo's cache and output
are what they were), and an edit to one language rebuilds only that language's graph. Python ids are dotted
and TypeScript ids are paths, so nearly always no symbol or edge can be confused with the other language's.
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

## How it works

The core has no parser dependency: the Python front end reads files with the standard library `ast`
module, and the base install needs only the `mcp` SDK. The TypeScript front end (`ripple/ts_indexer.py`)
writes the same per-file facts from a tree-sitter parse, so one resolver builds both graphs.

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
TypeScript repositories, are set up but not yet run (see "TypeScript tasks" below).

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
  printed (per mode, per task and with its reason) in two cases: a with-ripple run whose ripple
  server fails to connect, and a run that changed no file while an edit tool (Edit, Write,
  MultiEdit, NotebookEdit) was refused by the permissions, since the harness then blocked the very
  tool the prompt named. Other refusals are routine and do not invalidate a run: an agent that
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
table and a `-diffs/` directory beside it, and refuses to overwrite an earlier batch's files.

It calls the `claude` CLI on whatever account it is logged in to, so it never runs in CI.

### TypeScript tasks

Nine more tasks (`eval/tasks/20-*` to `28-*`) run over four pinned TypeScript repositories. **No agent has run on
them yet.** The harness is wired and proven with a fake agent (see below), but spending on a real run is a separate
decision, and the Results table below has no TypeScript row until one exists. The grader (`eval/check_node.py`)
is the same design as the Python one: the repo's own runner over the agent's source with the reference's tests put
back, plus a hidden TypeScript test.

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
  `node_modules`, `site-packages`, `venv` and any directory with a `pyvenv.cfg` anywhere, and `build`,
  `dist` and `env` at the repo root only. `ripple index` lists the skipped directories, except
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
