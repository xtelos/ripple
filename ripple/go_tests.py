"""Which Go functions `go test` runs as tests, and under which ids.

This is the Go profile's answer to "which tests run this symbol" (see profile.py), as pytest_collection.py is Python's.
The graph holds the answer, decided when the files are read (the signature, the build constraint and the package are
read off the source, which a graph does not keep): `Graph.test_ids` maps the symbol id of each test function to the id
`check` takes for it (`<repo-relative file>::<Name>`, see go_check.parse_wants), and `Graph.tests_withheld` maps a
function that looks like a test, and has no id, to why. GoTestCollection reads the first.

An id is given only when `go test` would run that test, by the rules of the go command (cmd/go's load/test.go, which
`go test -list` follows) and go/doc (examples):

- TestXxx(t *testing.T), FuzzXxx(f *testing.F) and ExampleXxx() are run. The name after the prefix must not start with
  a lowercase letter (unicode.IsLower, `selected` below, which check uses too). TestMain(m *testing.M) is not a test.
- A fuzz target runs only its seeds under plain `go test`, and one with none runs nothing (a broken symbol passes it): it
  has an id only when its body's own statements call `f.Add(...)` before `f.Fuzz(...)`. testdata/fuzz is not read.
- An example is run only when its last comment group is an `// Output:` or `// Unordered output:` comment.
- A benchmark is listed by `go test -list` but run only with -bench, and `check` does not take one: withheld.
- The function must sit in a _test.go file that every build has (no //go:build line, no GOOS or GOARCH in its name):
  which tests run on a build ripple does not know is not known.
- go refuses a whole package for a Test, Benchmark or Fuzz name with a signature that is not the one its prefix
  names, and go vet (which `go test` runs) fails it for a malformed name or an `// Output:` that is not last. No
  test of such a directory runs, so none is given an id.
- A name declared more than once in a directory (the package and its external test package, a constrained file too) is
  run once for each by `go test -run`: withheld.
- Nothing is given for a directory with no go.mod above it (`check` cannot say where to run go test), one with a
  file ripple could not read completely, or a test file that imports "C".

What a question reaches that runs in a test binary and has no id is named by GoTestCollection.unlisted (TestMain, init and
package variables, suite methods, helpers no listed test calls): no such caller is silent.

A subtest made by t.Run is not collected on its own: its calls belong to the function that holds the closure, which is
the parent test, and that is the symbol impact lists. What go vet makes of an Example's name (that it refers to
something that exists) is not modelled: a package vet rejects fails `check` with go's own message.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter, defaultdict

from .model import Graph

PREFIXES = ("Test", "Benchmark", "Fuzz", "Example")
OUTPUT = re.compile(r"^[ \t\n\r\f\v]*(unordered )?output:", re.IGNORECASE)  # go/doc's outputPrefix and go vet's outputRe
CHECK_OUTPUT = re.compile(r"^//\s*(?:unordered output|output):", re.IGNORECASE)  # what go_check.OUTPUT_COMMENT can read

CONSTRAINED = (
    "{rel} is built only on some builds (a //go:build line, or a GOOS or GOARCH in the file name), and which of its tests "
    "`go test` runs on yours is not known"
)
NO_MODULE = "no go.mod holds {directory}, so `go test` has nowhere to run (check needs the module that holds the package)"
UNREAD = "ripple could not read all of {files}, so what `go test` builds for {directory} is not known"
BENCHMARK = "a benchmark: `go test` runs it only with -bench, and check does not take one"
NO_OUTPUT = "an example with no `// Output:` or `// Unordered output:` comment is compiled and never run"
BLOCK_OUTPUT = "the output comment is a /* */ comment or follows code on its line, which check does not read, so it would refuse the id"
UNWRITTEN = "check finds a test by `func {name}(` at the start of a line, and this declaration is not written so"
BROKEN = "`go test` fails the whole package ({directory}) on {name} ({where}): {why}"
TWICE = "{name} is declared more than once in {directory} (a package and its external test package each have one), and `go test -run` runs every one of them"
NO_SEED = (
    "a fuzz target runs under plain `go test` only on its seed corpus, and ripple sees no seed here (an `f.Add(...)` statement "
    "of the target before `f.Fuzz`; testdata/fuzz is not read): with no seed the fuzz function never runs, so a pass proves nothing"
)
ODD_PATH ="the path {rel} has `::` in it, which no id can carry"


class Testing:
    """What a file's imports make of the name `testing`: the names bound to the package, and whether it is dot-imported."""

    def __init__(self, root):
        self.names: set[str] = set()
        self.dot = False
        for declaration in root.children:
            if declaration.type != "import_declaration":
                continue
            specs = [c for c in declaration.children if c.type == "import_spec"]
            for block in (c for c in declaration.children if c.type == "import_spec_list"):
                specs += [c for c in block.children if c.type == "import_spec"]
            for spec in specs:
                path = spec.child_by_field_name("path")
                if path is None or path.text.decode("utf-8", "replace").strip("\"`") != "testing":
                    continue
                alias = spec.child_by_field_name("name")
                if alias is None:
                    self.names.add("testing")
                elif alias.type == "dot":
                    self.dot = True
                elif alias.type != "blank_identifier":
                    self.names.add(alias.text.decode("utf-8", "replace"))


def lowercase(letter: str) -> bool:
    """Go's unicode.IsLower: the category Ll (not Python's str.islower, which also counts Other_Lowercase)."""
    return unicodedata.category(letter) == "Ll"


def prefix_of(name: str) -> str | None:
    """The prefix `name` starts with (Test, Benchmark, Fuzz, Example), whatever follows."""
    return next((p for p in PREFIXES if name.startswith(p)), None)


def selected(name: str, prefix: str) -> bool:
    """cmd/go's isTest: the name after the prefix is empty or does not start with a lowercase letter."""
    rest = name[len(prefix):]
    return rest == "" or not lowercase(rest[0])


def signature(node, wanted: str, testing: Testing, clash: bool) -> str:
    """How a function's header reads as `func (x *testing.<wanted>)`, by cmd/go's isTestFunc: "ok" when it provably is,
    "bad" when go refuses it (a result, not one parameter, a parameter that is not `*T` or `*pkg.T`, the wrong type name),
    "unproven" when go's loose reading takes it and the type is not provably testing's (an alias ripple cannot trace)."""
    if node.child_by_field_name("result") is not None:
        return "bad"
    params = node.child_by_field_name("parameters")
    declared = [c for c in params.children if c.is_named and c.type != "comment"] if params is not None else []
    if len(declared) != 1 or declared[0].type != "parameter_declaration":
        return "bad"
    if len(declared[0].children_by_field_name("name")) > 1:
        return "bad"
    kind = declared[0].child_by_field_name("type")
    if kind is None or kind.type != "pointer_type":
        return "bad"
    inner = [c for c in kind.children if c.is_named and c.type != "comment"]
    if len(inner) != 1:
        return "bad"
    target = inner[0]
    if target.type == "qualified_type":
        if target.child_by_field_name("name").text.decode("utf-8", "replace") != wanted:
            return "bad"
        return "ok" if target.child_by_field_name("package").text.decode("utf-8", "replace") in testing.names else "unproven"
    if target.type == "type_identifier":
        if target.text.decode("utf-8", "replace") != wanted:
            return "bad"
        return "ok" if testing.dot and not clash else "unproven"
    return "bad"


def comments_of(node) -> list:
    found, stack = [], [node]
    while stack:
        current = stack.pop()
        if current.type == "comment":
            found.append(current)
        else:
            stack.extend(current.children)
    return sorted(found, key=lambda c: c.start_byte)


def comment_groups(body, source: bytes) -> list[list]:
    """The comment groups of a function body as go's parser makes them: comments with nothing but white space between
    them and no blank line; a comment that follows code on its line is a group of its own."""
    groups: list[list] = []
    previous_trailing = False
    for comment in comments_of(body):
        line_start = source.rfind(b"\n", 0, comment.start_byte) + 1
        trailing = source[line_start : comment.start_byte].strip(b" \t\r\f\v") != b""
        last = groups[-1][-1] if groups else None
        joins = (
            last is not None
            and not trailing
            and not previous_trailing
            and comment.start_point[0] - last.end_point[0] <= 1
            and source[last.end_byte : comment.start_byte].strip(b" \t\r\n\f\v") == b""
        )
        if joins:
            groups[-1].append(comment)
        else:
            groups.append([comment])
        previous_trailing = trailing
    return groups


def group_text(group) -> str:
    pieces = []
    for comment in group:
        text = comment.text.decode("utf-8", "replace")
        pieces.append(text[2:] if text.startswith("//") else text[2:-2] if text.endswith("*/") else text[2:])
    return "\n".join(pieces)


def example_output(body, source: bytes) -> tuple[str, str]:
    """("run", "") for an example go runs, ("broken", why) when go vet fails on its comments, else ("quiet", why) for
    one `go test` compiles and does not run (and ("quiet", why) too for an output only a `/* */` comment states)."""
    if body is None:
        return "quiet", NO_OUTPUT
    groups = comment_groups(body, source)
    outputs = [g for g in groups if OUTPUT.match(group_text(g))]
    if not outputs:
        return "quiet", NO_OUTPUT
    if outputs[-1] is not groups[-1] or len(outputs) > 1:
        return "broken", "go vet fails it: an `// Output:` comment must be the last comment block, and there can be only one"
    head = groups[-1][0]
    line_start = source.rfind(b"\n", 0, head.start_byte) + 1
    if source[line_start : head.start_byte].strip(b" \t\r\f\v") or not CHECK_OUTPUT.match(head.text.decode("utf-8", "replace")):
        return "quiet", BLOCK_OUTPUT
    return "run", ""


def seeded(node, source: bytes) -> bool:
    """Whether a fuzz target visibly has a seed: a statement of its body that is `f.Add(...)` on its own parameter, before a
    statement that is `f.Fuzz(...)`, with no `return` in between. A seed added in a loop or an if may add none, so only the
    body's own statements count. testdata/fuzz is not looked at: the cache does not fingerprint it."""
    params = node.child_by_field_name("parameters")
    declared = [c for c in params.children if c.type == "parameter_declaration"] if params is not None else []
    names = declared[0].children_by_field_name("name") if declared else []
    body = node.child_by_field_name("body")
    if len(names) != 1 or body is None:
        return False
    name = names[0].text.decode("utf-8", "replace")
    statements = []
    for child in body.children:
        if child.type == "statement_list":
            statements += child.children
    added = False
    for statement in statements:
        if statement.type == "return_statement":
            return False
        call = statement.children[0] if statement.type == "expression_statement" and statement.children else None
        function = call.child_by_field_name("function") if call is not None and call.type == "call_expression" else None
        if function is None or function.type != "selector_expression":
            continue
        operand, method = function.child_by_field_name("operand"), function.child_by_field_name("field")
        if operand is None or method is None or operand.type != "identifier" or operand.text.decode("utf-8", "replace") != name:
            continue
        if method.text == b"Fuzz":
            return added
        added = added or method.text == b"Add"
    return False


def collect(program) -> tuple[dict[str, str], dict[str, str]]:
    """({symbol id: the id check takes}, {symbol id: why a function that looks like a test has none}) for a go_indexer.Program."""
    ids: dict[str, str] = {}
    withheld: dict[str, str] = {}
    by_directory = defaultdict(list)
    for package in program.packages.values():
        by_directory[package.dir].append(package)
    for directory, packages in sorted(by_directory.items()):
        _directory(program, directory, packages, ids, withheld)
    return ids, withheld


def _directory(program, directory: str, packages: list, ids: dict[str, str], withheld: dict[str, str]) -> None:
    where = directory or "."
    candidates = []  # (decl, prefix) of every function in a _test.go file whose name is a test name
    for package in packages:
        for name, decls in package.funcs.items():
            prefix = prefix_of(name)
            if prefix is None:
                continue
            candidates += [(decl, prefix, package) for decl in decls if decl.file.rel.endswith("_test.go")]
    if not candidates:
        return
    blocked = None
    if program.modules.module_of(directory) is None:
        blocked = NO_MODULE.format(directory=where)
    elif any(p.unreadable for p in packages):
        blocked = UNREAD.format(files=", ".join(sorted({rel for p in packages for rel in p.unreadable})), directory=where)
    elif any("C" in info.imports for p in packages for info in p.files if info.rel.endswith("_test.go")):
        blocked = "a _test.go file imports \"C\", and go test does not support cgo in test files"
    runnable: list[tuple[str, str, object]] = []  # (name, symbol id, decl) of the tests go would run
    spoilers: list[str] = []
    own: dict[str, str] = {}
    texts: dict[str, bytes] = {}  # file -> its bytes, the tree's own copy
    for decl, prefix, package in candidates:
        name, sid = decl.name, decl.id
        loose = selected(name, prefix)
        texts.setdefault(decl.file.rel, program.sources[decl.file.rel].tree.root_node.text)
        if not decl.file.tests_only:
            if loose:
                own[sid] = CONSTRAINED.format(rel=decl.file.rel)
            continue
        if blocked is not None:
            if loose:
                own[sid] = blocked
            continue
        node = decl.node
        source = texts[decl.file.rel]
        at = f"{decl.file.rel}:{node.start_point[0] + 1}"
        testing = Testing(program.sources[decl.file.rel].tree.root_node)
        generic = node.child_by_field_name("type_parameters") is not None

        def broken(why: str, name=name, at=at) -> None:
            spoilers.append(BROKEN.format(directory=where, name=name, where=at, why=why))

        if prefix == "Example":
            if generic or signature_of_example(node):
                if loose:
                    broken("an example must be `func " + name + "()`, with no parameter, result or type parameter")
                continue
            if not loose:
                broken("go vet fails the package for a name with a lowercase letter after `Example`")
                continue
            state, why = example_output(node.child_by_field_name("body"), source)
            if state == "broken":
                broken(why)
            elif state == "quiet":
                own[sid] = why
            else:
                runnable.append((name, sid, decl))
            continue
        letter = {"Test": "T", "Benchmark": "B", "Fuzz": "F"}[prefix]
        clash = letter in package.names
        if name == "TestMain" and not generic:
            if signature(node, "T", testing, clash) == "ok":
                runnable.append((name, sid, decl))
                continue
            if signature(node, "M", testing, "M" in package.names) == "ok":
                continue  # the one function that is not a test whatever its name
        if not loose:
            # a malformed name go vet fails the package for, when the function looks like a test (one *T, *B or *F parameter)
            if signature(node, letter, testing, clash) != "bad":
                broken(f"go vet fails the package for a name with a lowercase letter after `{prefix}`, and it is not a {prefix.lower()}")
            continue
        verdict = "bad" if generic else signature(node, letter, testing, clash)
        if verdict == "bad":
            broken(f"go refuses a {prefix} function that is not `func {name}({letter.lower()} *testing.{letter})`")
        elif verdict == "unproven":
            broken(f"its parameter is not provably *testing.{letter} (the package `testing` is not imported under that name)")
        elif prefix == "Benchmark":
            own[sid] = BENCHMARK
        elif prefix == "Fuzz" and not seeded(node, source):
            own[sid] = NO_SEED
        else:
            runnable.append((name, sid, decl))
    # every _test.go declaration of the name counts, a constrained file's too: some build has it, and `go test -run` runs each
    names = Counter(
        decl.name for decl, prefix, _ in candidates if prefix in ("Test", "Fuzz", "Example") and decl.name != "TestMain" and selected(decl.name, prefix)
    )
    for name, sid, decl in runnable:
        line = texts[decl.file.rel][decl.node.start_byte :].split(b"\n", 1)[0]
        if spoilers:
            own[sid] = spoilers[0]
        elif names[name] > 1:
            own[sid] = TWICE.format(name=name, directory=where)
        elif "::" in decl.file.rel:
            own[sid] = ODD_PATH.format(rel=decl.file.rel)
        elif decl.node.start_point[1] != 0 or not line.startswith(f"func {name}(".encode()):
            own[sid] = UNWRITTEN.format(name=name)
        else:
            ids[sid] = f"{decl.file.rel}::{name}"
    withheld.update(own)


def signature_of_example(node) -> bool:
    """Whether an example's header is not the niladic `func ExampleX()` (a parameter or a result)."""
    params = node.child_by_field_name("parameters")
    declared = [c for c in params.children if c.is_named and c.type != "comment"] if params is not None else []
    return bool(declared) or node.child_by_field_name("result") is not None


BEFORE_EVERY_TEST = (
    "{what} runs before every test of {where} (the package and its external test package are one test binary), so every test "
    "in it runs this code and ripple lists none by id: pass the directory ({directory}) to check to run them"
)
AT_INITIALISATION = (
    "{what} runs when the package {package} is initialised, so every test binary that links it runs it: the tests of the "
    "package and of every package that imports it. ripple does not list them"
)
PACKAGE_LEVEL = (
    "package-level code of {package}: a package variable's initializer, which runs when the package is initialised, or a "
    "function a package variable holds, which runs when it is called. Any test binary that links the package may run it: the "
    "tests of the package and of every package that imports it. ripple does not list them"
)
BY_REFLECTION = (
    "a method named like a test, in a _test.go file: a test runner (a suite) may run it by reflection, and no listed test "
    "calls it, so which test runs it is not known. Pass the directory ({directory}) to check to run the package's tests"
)
UNCALLED_HELPER = (
    "declared in a _test.go file, and no listed test is known to call it (within the depth asked), so which tests run it "
    "is not known. Pass the directory ({directory}) to check to run the package's tests"
)


def directory_of(file: str) -> str:
    return file.rpartition("/")[0] or "."


class GoTestCollection:
    """The Go profile's test collection: the ids the indexer decided on (Graph.test_ids), read per symbol, and what a
    question about a symbol reaches that runs in a test binary and has no id (`unlisted`)."""

    def __init__(self, graph: Graph):
        self.graph = graph

    def node_ids(self, symbol_id: str) -> list[str]:
        found = self.graph.test_ids.get(symbol_id)
        return [found] if found else []

    def unlisted(self, distance: dict[str, int], targets: set[str]) -> list[dict]:
        """The `tests_not_listed` entries for the callers an impact answer reached (symbol id -> hops), beyond the functions
        the indexer withheld (Graph.tests_withheld, which the query lists itself).

        The invariant: every reached caller that runs as part of a test binary, without being called by a listed test, is
        named. A caller is one of these when it
          - is TestMain, an `init` or a package variable initialised in a _test.go file: it runs before every test of its
            directory's binary (the package and its external test package, one binary);
          - is an `init` or a package variable of any other file: it runs in every binary that links the package;
          - is declared in a _test.go file, is not a listed or withheld test, and no path of reached callers leads from it
            to a listed or withheld test (or to one of the above): a method a suite runs by reflection, or a helper nothing
            collected calls.
        A package variable's initializer belongs to the package symbol, whose file is the package's first: the call's own
        file says which kind it is, so the entry says where the call is.
        """
        graph = self.graph
        here = set(distance) | targets
        listed = {sid for sid in distance if sid in graph.test_ids or sid in graph.tests_withheld}
        entries: dict[str, dict] = {}

        def add(sid: str, file: str, line: int, reason: str) -> None:
            entries[sid] = {"symbol": sid, "line": line, "distance": distance[sid], "file": file, "reason": reason}

        for sid in distance:
            if sid in listed:
                continue
            symbol = graph.symbols[sid]
            in_test_file = symbol.file.endswith("_test.go")
            if symbol.kind == "module":  # a package variable's initializer: the calls say which file it is in
                sites = [e for e in graph.callees_index.get(sid, ()) if e.callee in here]
                # a call in a non-test file comes first: it is the wider claim (every binary that links the package, so the
                # tests of its importers too), and naming only the directory's own tests beside it would leave those out
                sites.sort(key=lambda e: (e.file.endswith("_test.go"), e.file, e.line))
                if not sites:
                    continue
                site = sites[0]
                if site.file.endswith("_test.go"):
                    what = "a package variable's initializer"
                    add(sid, site.file, site.line, BEFORE_EVERY_TEST.format(what=what, where=directory_of(site.file), directory=directory_of(site.file)))
                else:
                    add(sid, site.file, site.line, PACKAGE_LEVEL.format(package=f"`{sid}`"))
            elif symbol.kind == "function" and symbol.name == "init":
                if in_test_file:
                    where = directory_of(symbol.file)
                    add(sid, symbol.file, symbol.line, BEFORE_EVERY_TEST.format(what="`init` in a _test.go file", where=where, directory=where))
                else:
                    add(sid, symbol.file, symbol.line, AT_INITIALISATION.format(what="`init`", package=f"`{sid.split(':', 1)[0]}`"))
            elif symbol.kind == "function" and symbol.name == "TestMain" and in_test_file:
                where = directory_of(symbol.file)
                add(sid, symbol.file, symbol.line, BEFORE_EVERY_TEST.format(what="TestMain", where=where, directory=where))
        covered = set(listed) | set(entries)  # what a helper below may be reached from: a test, or code that runs in the binary
        stack = list(covered)
        while stack:  # a helper a covered symbol calls is run by that symbol
            for edge in graph.callees_index.get(stack.pop(), ()):
                if edge.callee in distance and edge.callee not in covered:
                    covered.add(edge.callee)
                    stack.append(edge.callee)
        for sid in distance:
            symbol = graph.symbols[sid]
            if sid in covered or not symbol.file.endswith("_test.go"):
                continue
            where = directory_of(symbol.file)
            suite = symbol.kind == "method" and symbol.name.startswith("Test") and selected(symbol.name, "Test")
            add(sid, symbol.file, symbol.line, (BY_REFLECTION if suite else UNCALLED_HELPER).format(directory=where))
        return list(entries.values())
