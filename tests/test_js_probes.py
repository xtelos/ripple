"""Module resolution against the real TypeScript compiler and node, over the probe repos in tests/fixtures/js_probes/probes.

Each probe is a small repo (PROBE.json lists its import sites and what the probe is for) written by make_probes.py, and
expected.json beside it says, for every site, what the real tools make of it (generate_expected.mjs, run by hand with a real
`typescript` and node; this test needs neither): the file ts.resolveModuleName reaches under the project tsserver puts the
importer in, the project `tsc -p` builds it in, and, for a .js, .mjs or .cjs importer, the file node ran when it imported the
specifier. ripple may be less certain than the tools, never more:

  - a site ripple resolves to a repo file names the compiler's file (or, for a declaration file, the code it describes), and
    the file node ran where it ran one;
  - a site whose compiler file is of the other language is unresolved with the cross-language reason, never an edge;
  - a site the compiler cannot resolve is never resolved, and a file of the repo is never called a package. Nor is a
    directory of the repo, a `#` import, the name of a package.json of the repo, an alias that a config holding a same-named twin of
    the importer maps, or anything node ran a repo file for;
  - where node and the compiler name different files, the site is unresolved;
  - a site PROBE.json lists under "unresolved" is one where the compiler answers but something ripple does not read (a
    package.json, a bundler, the repo's own unread files) could name another file, so ripple must not resolve it.

For a TypeScript importer a tsconfig.json that holds it is the project it is built in, whatever project tsserver opens it in.

Every importer the maker wrote calls `mN.f()` once per site, and that call is checked on the graph: an edge to f in the file
ripple resolved the import to (which is the compiler's), and no edge where the import is not a repo file. The sites that call a
function by name (PROBE.json "calls") are checked against the definition tsserver finds, "jsx" names a file whose project's JSX
settings decide whether ripple claims React's order, "symbols" (written by hand) are queries that must keep finding the one
symbol, and "walk" lists the files the walk must and must not read and the directories it skips.
"""

import filecmp
import json
import posixpath
import re
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("tree_sitter", reason="TypeScript support is not installed (pip install 'ripple-code[typescript]')")
pytest.importorskip("tree_sitter_typescript", reason="TypeScript support is not installed (pip install 'ripple-code[typescript]')")

from ripple.cache import load_repo  # noqa: E402
from ripple.indexer import find_source_files  # noqa: E402
from ripple.queries import find_symbol  # noqa: E402
from ripple.registry import LANGUAGES, language_of  # noqa: E402
from ripple.ts_modules import ModuleMap  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "js_probes"
PROBES = FIXTURES / "probes"
NAMES = sorted(p.name for p in PROBES.iterdir() if p.is_dir())
DECLARATIONS = (".d.ts", ".d.mts", ".d.cts")
WRITTEN = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts")
TYPESCRIPT = (".ts", ".tsx", ".mts", ".cts")
CROSS = "names {file}, a {language} file, whose calls ripple does not follow from {here}"
LABEL = {"typescript": "TypeScript", "javascript": "JavaScript"}


def stem(rel: str) -> str:
    return re.sub(r"\.(d\.)?[cm]?[jt]sx?$", "", rel)


def read(name: str) -> tuple[Path, dict, dict]:
    root = PROBES / name
    return root, json.loads((root / "PROBE.json").read_text()), json.loads((root / "expected.json").read_text())


def package_names(root: Path) -> set[str]:
    """The `name` of every package.json in the probe, which Node (through workspace links, or the package's own `exports`) reads."""
    names = set()
    for path in root.rglob("package.json"):
        name = json.loads(path.read_text()).get("name")
        if isinstance(name, str):
            names.add(name)
    return names


def truth_of(importer: str, site: dict) -> str | None:
    """The file the repo's own build resolves the import to: for a TypeScript file a tsconfig.json holds, that project's answer."""
    return site["build"] if site["build_project"] and importer.endswith(TYPESCRIPT) else site["resolved"]


def judge_import(modules: ModuleMap, root: Path, importer: str, spec: str, site: dict, must_not_resolve: str | None, names: set[str]) -> str:
    """"" when ripple's answer for the site is one the tools' answers allow, else what is wrong."""
    kind, value = modules.resolve(spec, importer)
    own = language_of(importer).profile.name
    expected = truth_of(importer, site)
    node_file = (site.get("node") or {}).get("file")
    there = language_of(expected).profile.name if expected else None
    declaration = bool(expected) and expected.endswith(DECLARATIONS)
    shown = f"{kind} {value!r}, the compiler says {expected}" + (f", node ran {node_file}" if node_file else "")
    if kind == "repo":
        files = [rel for rel, module in modules.modules.items() if module == value]
        if must_not_resolve:
            return f"resolved to {files} where {must_not_resolve}: {shown}"
        if len(files) != 1 or expected is None:
            return f"resolved where the compiler does not: {shown}"
        if node_file is not None and node_file != files[0]:
            return f"resolved to {files[0]}, where node ran {node_file}: {shown}"
        if declaration:
            return "" if stem(files[0]) == stem(expected) else f"resolved to {files[0]}, not what the declaration {expected} describes"
        return "" if files[0] == expected else f"resolved to {files[0]}: {shown}"
    if kind == "unresolved":
        if must_not_resolve or expected is None or declaration or (node_file is not None and node_file != expected):
            return ""
        if there != own and CROSS.format(file=expected, language=LABEL[there], here=LABEL[own]) in value:
            return ""
        return f"unresolved where the compiler resolves a file ripple should name: {shown}"
    if must_not_resolve:
        return f"{kind} where {must_not_resolve}: {shown}"
    if node_file is not None:
        return f"{kind} where node runs the repo's {node_file}: {shown}"
    if site["reach_dropped"]:
        return f"{kind} where {sorted(site['reach_dropped'])} maps the alias for the twin that holds {importer} in its include: {shown}"
    target = posixpath.normpath(posixpath.join(posixpath.dirname(importer), spec)) if spec.startswith(".") else ""
    if target and not target.startswith("..") and (root / target).is_dir():
        return f"a directory of the repo called {kind}: {shown}"
    if spec.startswith("#"):
        return f"a `#` import called {kind}: {shown}"
    parts = spec.split("/")
    if not spec.startswith((".", "/")) and ("/".join(parts[:2]) if spec.startswith("@") else parts[0]) in names:
        return f"a package of the repo called {kind}: {shown}"
    if kind == "package":
        if expected is not None:
            return f"a repo file called a package: {shown}"
        # a file that is not source (a logo, a stylesheet) is rightly a package; an existing .js, or x for an x.ts, is not
        if target and any((root / (target + suffix)).is_file() for suffix in (("",) if target.endswith(WRITTEN) else ()) + (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")):
            return f"a repo file called a package: {kind} {value!r}"
        return ""
    # missing: no file at all. The compiler also takes `./x.jsx` for x.js and `./x.tsx` for x.ts, which nothing runs: a path
    # written with an extension that no file has is missing, whatever the compiler makes of it.
    written = posixpath.normpath(posixpath.join(posixpath.dirname(importer), spec))
    if spec.endswith(WRITTEN) and not (root / written).exists():
        return ""
    return "" if expected is None or declaration else f"missing where the compiler finds a file: {shown}"


def judge_site_call(graph, modules: ModuleMap, root: Path, importer: str, line: int, spec: str) -> str:
    """The call `mN.f()` the maker wrote for a site: an edge to f in the file the import resolved to, or no edge."""
    kind, value = modules.resolve(spec, importer)
    edges = [e for e in graph.edges if e.file == importer and e.line == line and e.kind == "call"]
    shown = f"{importer}:{line} through `{spec}` ({kind} {value!r})"
    if kind != "repo":
        return f"an edge {[e.callee for e in edges]} where the import is {kind}: {shown}" if edges else ""
    target = next(rel for rel, module in modules.modules.items() if module == value)
    files = {graph.symbols[e.callee].file for e in edges}
    if files - {target}:
        return f"an edge to {sorted(files)}, not {target}: {shown}"
    if not edges and re.search(r"export (declare )?function f\b", (root / target).read_text()):
        return f"the import resolved to {target}, which exports f, but the call has no edge: {shown}"
    return ""


def judge_call(graph, root: Path, site: dict, defined_in: str | None, must_not_resolve: str | None) -> str:
    file, call = site["in"], site["call"]
    pattern = re.compile(rf"(?<!function )\b{re.escape(call)}\s*\(")
    line = next(n for n, text in enumerate((root / file).read_text().splitlines(), 1) if pattern.search(text))
    edges = [e for e in graph.edges if e.file == file and e.line == line and e.kind == "call"]
    left_open = [u for u in graph.unresolved if u.file == file and u.line == line]
    shown = f"{file}:{line} {call}(), the compiler says {defined_in}"
    if edges:
        if must_not_resolve:
            return f"an edge where {must_not_resolve}: {shown}: {[e.callee for e in edges]}"
        files = {graph.symbols[e.callee].file for e in edges}
        return "" if defined_in is not None and files == {defined_in} else f"an edge to {sorted(files)}: {shown}"
    if must_not_resolve or defined_in is None or language_of(defined_in).profile.name != language_of(file).profile.name:
        return "" if left_open else f"no edge and no unresolved call: {shown}"
    return f"a call of the same language is unresolved: {shown}: {[u.reason for u in left_open]}"


@pytest.mark.parametrize("name", NAMES)
def test_ripple_never_claims_more_than_the_tools_prove(name):
    root, probe, expected = read(name)
    assert [[s["importer"], s["specifier"]] for s in expected["imports"]] == probe["imports"], "expected.json is stale: run generate_expected.mjs"
    maps = {}
    for profile in (language.profile for language in LANGUAGES.values() if language.path_ids):
        maps[profile.name] = ModuleMap(root, find_source_files(root, profile))
    notes = probe.get("unresolved", {})
    names = package_names(root)
    graph = load_repo(root, lang="auto").graph
    wrong = []
    seen: dict[str, int] = {}
    for site in expected["imports"]:
        importer, spec = site["importer"], site["specifier"]
        modules = maps[language_of(importer).profile.name]
        message = judge_import(modules, root, importer, spec, site, notes.get(f"{importer}|{spec}"), names)
        if message:
            wrong.append(f"{importer} imports {spec!r}: {message}")
        index = seen.get(importer, 0)
        seen[importer] = index + 1
        if importer in probe["generated"]:
            message = judge_site_call(graph, modules, root, importer, 2 * index + 2, spec)
            if message:
                wrong.append(f"call through {importer} {spec!r}: {message}")
    for query, symbol in probe.get("symbols", {}).items():
        if find_symbol(graph, query) != {"symbol": symbol}:
            wrong.append(f"find_symbol({query!r}) is {find_symbol(graph, query)}, not {symbol}")
    for site, found in zip(probe.get("calls", []), expected["definitions"]):
        message = judge_call(graph, root, site, found["defined_in"], notes.get(f"calls:{site['in']}|{site['call']}"))
        if message:
            wrong.append(message)
    for key, part in probe.get("reasons", {}).items():
        file, callee = key.split("|")
        reasons = [u.reason for u in graph.unresolved if u.file == file and u.text == callee]
        if not reasons or not all(part in reason for reason in reasons):
            wrong.append(f"{key}: the unresolved call's reasons {reasons} do not say {part!r}")
    walk = probe.get("walk", {})
    wrong += [f"{rel} is not indexed" for rel in walk.get("indexed", []) if rel not in graph.modules]
    wrong += [f"{rel} is indexed" for rel in walk.get("not_indexed", []) if rel in graph.modules]
    if "skipped" in walk and sorted(graph.skipped_dirs) != sorted(walk["skipped"]):
        wrong.append(f"skipped directories are {graph.skipped_dirs}, not {walk['skipped']}")
    for site in expected["jsx"]:
        own_runtime = site["jsxFactory"] or site["jsxFragmentFactory"] or site["jsxImportSource"]
        claimed = [e for e in graph.edges if e.file == site["file"] and e.kind == "renderer"]
        if own_runtime and claimed:
            wrong.append(f"{site['file']}: {len(claimed)} renderer edges where {site['project']} sets {own_runtime}, not React's runtime")
    assert not wrong, f"{name}: {len(wrong)} wrong\n" + "\n".join(wrong)


def test_the_probes_cover_every_importer_language_and_the_cases_the_reviews_found():
    suffixes = {Path(site["importer"]).suffix for name in NAMES for site in read(name)[2]["imports"]}
    assert suffixes >= {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"}
    assert len(NAMES) >= 100 and sum(len(read(name)[1]["imports"]) for name in NAMES) >= 1300
    assert {read(name)[2]["typescript"] for name in NAMES} == {"5.9.3"}
    ran = [s["node"] for name in NAMES for s in read(name)[2]["imports"] if s.get("node")]
    assert len(ran) >= 500 and any(n["file"] for n in ran), "node ran no JavaScript importer's imports"
    calls = sum(len(read(name)[1]["generated"]) for name in NAMES)
    assert calls >= 250, "the maker wrote few importers with a call"


def test_the_committed_probes_are_exactly_what_the_maker_writes(tmp_path):
    """A probe is never edited by hand: make_probes.py rewrites every file of every probe but expected.json."""
    subprocess.run([sys.executable, str(FIXTURES / "make_probes.py"), str(tmp_path)], check=True, capture_output=True)
    made = sorted(p.name for p in tmp_path.iterdir())
    assert made == NAMES

    def compare(left: Path, right: Path) -> list[str]:
        diff = filecmp.dircmp(left, right, ignore=["expected.json"])
        found = [f"only in the maker's: {n}" for n in diff.left_only] + [f"only committed: {n}" for n in diff.right_only] + [f"differs: {n}" for n in diff.diff_files]
        for sub in diff.common_dirs:
            found += [f"{sub}/{x}" for x in compare(left / sub, right / sub)]
        return found

    wrong = [f"{name}: {message}" for name in NAMES for message in compare(tmp_path / name, PROBES / name)]
    assert not wrong, "\n".join(wrong)
