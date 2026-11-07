# TypeScript tracer

The TypeScript counterpart of `bench/trace.py`. It runs the fixture's own tests
(`bench/fixtures/ts/warehouse-ts/`) and records which repo function called which, so the hand-written
`bench/ground_truth_ts.json` can be checked against what actually ran. It shares no code with ripple.

## Regenerate a trace

```
python -m bench.ts_tracer install                  # once: npm ci --ignore-scripts into the cache
python -m bench.ts_tracer trace --out /tmp/t.json  # vitest (default); --runner jest for the other
python -m bench.ts_tracer typecheck                # tsc --noEmit over the fixture
python -m bench.ts_tracer status                   # "ready", or why the tracer cannot run
```

Dependencies never live in the repository. `install` copies the fixture's `package.json` and committed
`package-lock.json` into `$RIPPLE_TS_CACHE/work` (default `~/.cache/ripple-ts/work`) and runs
`npm ci --ignore-scripts` there. Every run copies the fixture into a directory of its own under
`$RIPPLE_TS_CACHE/runs` with that `node_modules` linked in (read only), and removes it afterwards, so two
runs at once never share a copy. The one lockfile pins everything: the tracer reads the TypeScript compiler
API, and the runners come from the same install. Do not run `install` while a trace is running.

`tests/test_ts_trace.py` runs the trace and compares it with the truth. It skips, with the reason in the
`-rs` output, when `node` is missing or `install` has not been run, so CI without node stays green.

## What a trace contains

```
{"edges": {"calls": [[from, to], ...], "renders": [...], "renderer": [...]},
 "sites": {"calls": {"from -> to": ["src/x.ts:12:5"]}, ...},     # where each edge was seen
 "diag": {...}, "runner": "vitest", "workers": 5}
```

The edges are the form the truth is written in: sorted `[from, to]` symbol ids, repo symbols only, no
self edges.

| kind | meaning |
| --- | --- |
| `calls` | `from` calls `to`: an ordinary call, `new`, a getter or setter access, or a callback passed by name to a library function. React rendering a component is not a call. |
| `renders` | the creator edge: the function whose body contains `<To/>` (or `createElement`). Children and render props are created where they are written. |
| `renderer` | the nearest enclosing project component in React's tree, to the component React invoked. A component passed as children is rendered by the wrapper that places it. |

`renders` and `renderer` disagree for children passed through a wrapper and for render props. The fixture
has both on purpose, and the truth lists the pairs that look like an edge in one kind and are not in the other.

## Symbol ids

`<path from the fixture root, no extension, index collapsed>:<Outer.inner>`.

- A module is its path alone. Code that runs while the module is evaluated belongs to the module.
- Functions, classes and class members are named by declaration. A constructor is `Class.constructor`;
  a class with no constructor in source gets one inserted by the transform (`super(...args)`) and is
  named by the class alone, as Python names a class with no `__init__`.
- A getter and setter with the same name share one symbol.
- An arrow or function expression that initialises `const x` is named `x`.
- The callback of `it()` or `test()` is `<describe titles> > <it title>`. A `describe()` callback is the module.
- Every other function is anonymous. It is never a callee, and what it calls belongs to the named function
  or module around it (as a Python lambda does).

Test titles must be literal strings and unique within a file.

## How it works

`core.cjs` inserts `__rip(file, k)` at the entry of each named function. `__rip` reads the V8 stack, takes the
first frame in an instrumented file, maps it back to TypeScript with the runner's source map, and names the
function whose source range contains that position. That is the caller. Default parameters, which run before
the function body, land in the callee's range for the same reason. Compiler helpers such as `__decorate` have
no source position and are skipped, and a class member's range starts at its parameter list, so a decorator
is attributed to the module that applies it.

`creator` edges come from patching React's `jsx`, `jsxs`, `jsxDEV` and `createElement`: the function running
when one is called is the creator. `renderer` edges come from React's current fiber and its `return` chain,
because React calls a child from its work loop and the parent is never on the stack. That reads React's
private owner API and is only exercised on React 19.2.0 development builds.

Both runners run the same tests. Vitest (esbuild transform) is the default and jest (ts-jest) is a second
opinion on the tracer; on this fixture they observe identical edges.

## Reading the truth

`bench/ground_truth_ts.json` splits each callee's callers three ways. `expected` callers are the ones ripple
should find. `unresolved` callers really call it (the trace sees them) but fall under a deferral decided for v1,
and `untraceable` callers are expected but the tracer cannot see them. Each of those entries carries a `why`
from a closed set, the `site` (file:line) of the call and a reason, and `tests/test_ts_ground_truth.py` checks
that the text at the site carries the marker of the `why`:

| why | bucket | marker at the site |
| --- | --- | --- |
| `computed-key` | unresolved | `TABLE[key](...)` on a table declared with `const` |
| `method-by-name` | unresolved | `obj[name](...)` on a parameter |
| `interface-typed-receiver` | unresolved | a call on a receiver declared with an interface |
| `decorator` | unresolved | an `@decorator` line |
| `decorator-wrapper` | unresolved | `.apply(` or `.call(` in a function used as a decorator |
| `debug-flag` | untraceable | a global debug flag guards the call, and the edge must not appear in any trace |

Overrides are scored as the Python truth scores S10 and K2: a call that reaches a subclass override at run time
is a call of the override, through `this`, a base-typed parameter or an untyped value, so those callers are
`expected`.

## Not covered (decided deferrals, or no naming rule yet)

Out of the fixture and so unchecked: class components, `memo`, `forwardRef`, `lazy`, styled wrappers,
`Context.Consumer`, `cloneElement`, class property initializers and arrow class fields, object literal
methods, anonymous default exports, `it.each`, namespaces and enums, generators, dynamic `import()`, `.d.ts`
and `.bind`. Interfaces, decorators and dispatch tables appear in the fixture only as calls ripple must leave
unresolved.
