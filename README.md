# ripple

**Know what breaks before you change it.** ripple is an MCP server that gives a coding agent
(Claude Code, Cursor, anything that speaks MCP) the call graph of a Python repository, so the agent
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
or a bare name (`price_order`). An ambiguous name returns the candidates rather than picking one.

The four graph tools advertise `readOnlyHint: true`, which lets a client run them without prompting.
`check` is marked destructive instead, because a test suite can do anything its tests do.

`impact` lists at most `limit` callers and `limit` tests (100 by default, up to 1000 over MCP). When
it found more, it says so: `truncated` is true and `omitted_callers` / `omitted_tests` give the
counts (the CLI prints "... and N more callers not listed"). Each test comes with its pytest node id
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

## How it works

Two passes over the standard library `ast` module, no other parser.

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
better with them: ten refactoring tasks, each run by Claude Code in headless mode, once with a
ripple MCP server and once without, three times each.

- **The tasks** (`eval/tasks/`) are renames and signature changes where grep is noisy or
  incomplete: a method named `total` on four classes, a function reached only through a package
  re-export alias, an override in another package, a `getattr` lookup that neither grep nor ripple
  can see. Seven run over `eval/fixtures/ledger`, a small invoicing app written for the eval, and
  three reuse the benchmark fixtures. Each task's `why` field says what makes it hard.
- **Grading.** An edit counts as a success when the fixture's own tests pass and still number at
  least the task's minimum (`min_tests`), and a hidden test the agent never sees passes too. The
  hidden test checks that the change was made and that every caller still works.
  `tests/test_eval.py` shows each check fails on the untouched fixture and passes on a reference
  solution, and CI runs it.
- **Same conditions.** Both modes get the same prompt, model, tools and permissions (edit files,
  run pytest). The MCP server is the only difference. Each run starts from a fresh copy under
  `/tmp`, in a directory whose name says nothing about the task or mode, and leaves out the user's
  own settings, hooks and auto-memory. A with-ripple run whose ripple server fails to connect is
  marked invalid and left out of the numbers.

```sh
.venv/bin/python -m eval.run                   # 10 tasks x 2 modes x 3 repeats, into eval/results/
.venv/bin/python -m eval.run --tasks 04-ledger-rename-invoice-total --repeats 1 --out /tmp/smoke.json
```

Each batch writes `eval/results/<date>-<time>-<id>.json` (unless `--out` says otherwise), a `.md`
table and a `-diffs/` directory beside it, and refuses to overwrite an earlier batch's files.

It calls the `claude` CLI on whatever account it is logged in to, so it never runs in CI.

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

- **TypeScript**, the other language agents spend most of their time in.

## Development

```sh
uv venv && uv pip install -e ".[dev]"
.venv/bin/python -m pytest -q      # unit tests, plus the ground truth against its runtime trace
.venv/bin/python -m bench          # the benchmark; --write-baseline after a deliberate change
```

MIT licensed. Copyright (c) 2026 Dylan Fodor.
