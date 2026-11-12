"""Which languages a repository is written in, and one graph from them.

`--lang` (or RIPPLE_LANG, or the server's lang argument) is the name of a language in registry.py
("python", "typescript", "javascript", "go", "rust", "java") or "auto", the default. Auto reads the repo's file names: each profile's source suffixes (profile.py) are looked for
in one walk that skips node_modules, virtualenvs and declaration files like every other walk. A repo
with files of one language is indexed with that language's profile, exactly as before. A repo with
both is MIXED: each language is indexed with its own profile, and cached under its own key (cache.py),
and the graphs are merged here.

The merge is a union, and nearly always the languages cannot mix up their symbols, because of how ids
are written: a Python id is dotted (`app.core.total`, and a Java one too: `app.Cart.add(Item)`) and a TypeScript or JavaScript id is a
path (`web/src/cart:total`).
But ANY id can be defined by both, not only a module: a root file with a dotted name is a dotted module id
(`app.config.ts` is `app.config`, the id of `def config` in app.py, and `util.ts` is `util` like util.py), and
so on for a class or a member. The merge cannot keep both: one id would carry both languages' callers and
invent chains between them. So for every id an earlier graph already defines (the registry lists the languages
in order, Python first), the later graph's symbol is dropped with every edge, unresolved call and base of that
graph that names the id; the earlier symbol and its edges are untouched. The ids are listed in Graph.ambiguous_ids with both files, in the
warnings (so the exit status is 3, "answered, but something was left out"), and queries refuse to answer for
them (queries.find_symbol).

A language whose parser is not installed or cannot load (TypeScript without the extra, or with a tree-sitter
that does not fit) is skipped when the repo has another, and the result says so (RepoGraph.skipped); when it
is the only one, that is an error.
Nothing here imports a front end or its parser (tree-sitter for TypeScript) until a file of its language has to be read.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .indexer import walk_languages
from .model import Graph
from .profile import LanguageProfile
from .registry import LANGUAGES, default_language, folds_files, join, label, language_of, module_id, profiles, rank

SHOWN_UNPARSABLE = 10  # files listed per language before "... and N more"
SHOWN_AMBIGUOUS = 5


def choices() -> tuple[str, ...]:
    """What --lang takes: auto, and each registered language."""
    return ("auto", *LANGUAGES)


class RepoError(ValueError):
    """The repo cannot be indexed as asked: it is not there, has nothing to read, or needs a parser that is missing."""


def plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


@dataclass
class Scan:
    repo: Path  # resolved
    profiles: list[LanguageProfile]  # the languages the repo has files of, in registry order
    files: dict[str, list[Path]]  # profile name -> its source files
    skipped_dirs: list[str]


@dataclass
class LanguageIndex:
    """What one language contributed to a graph."""

    name: str
    found: int  # source files the walk found
    files: int = 0  # files read into the graph (0 when skipped)
    parse_errors: list[str] = field(default_factory=list)
    unread: list[str] = field(default_factory=list)  # files the front end did not read, which is not a parse failure
    cached: bool = False
    skipped: bool = False  # its parser is not installed

    @property
    def label(self) -> str:
        return label(self.name)


@dataclass
class RepoGraph:
    """A repository's graph, with what went into it and what was left out."""

    graph: Graph
    languages: list[LanguageIndex]
    skipped: list[str] = field(default_factory=list)  # one message per language left out
    warnings: list[str] = field(default_factory=list)  # skipped, then ids both languages define

    @property
    def incomplete(self) -> bool:
        """Something of the repo is not in the graph: a language was skipped, or ids were left out."""
        return bool(self.warnings)

    @property
    def cached(self) -> bool:
        return all(language.cached for language in self.languages if not language.skipped)


def check_repo(repo: str | Path) -> Path:
    """The resolved repo directory, or a RepoError that says why it is not one."""
    path = Path(repo)
    if not path.exists():
        raise RepoError(f"{repo} does not exist")
    if not path.is_dir():
        raise RepoError(f"{repo} is not a directory")
    return path.resolve()


def selected_profiles(lang: str) -> list[LanguageProfile]:
    if lang == "auto":
        return profiles()
    if lang not in LANGUAGES:
        raise RepoError(f"unknown language '{lang}': choose one of {', '.join(choices())}")
    return [LANGUAGES[lang].profile]


def scan_repo(repo: str | Path, lang: str = "auto") -> Scan:
    """Find the repo's source files, once, for every language asked for. Raises RepoError for a repo
    that is not there or has no file of any of them."""
    root = check_repo(repo)
    selected = selected_profiles(lang)
    files, skipped = walk_languages(root, selected)
    present = [profile for profile in selected if files[profile.name]]
    if not present:
        if lang == "auto":
            names = join([label(profile.name) for profile in selected], "or")
            suffixes = join([suffix for profile in selected for suffix in profile.source_suffixes])
            skipped_kinds = ["node_modules", "virtualenvs", "build output"]
            skipped_kinds += [f"{profile.excluded_suffixes[0]} files" for profile in selected if profile.excluded_suffixes]
            raise RepoError(f"no {names} source files in {repo} (looked for {suffixes}; {join(skipped_kinds)} are skipped)")
        profile = selected[0]
        raise RepoError(
            f"no {label(profile.name)} source files ({', '.join(profile.source_suffixes)}) in {repo}"
        )
    return Scan(root, present, files, skipped)


def check_profile(repo: str | Path, lang: str = "auto") -> LanguageProfile:
    """The profile whose way of running tests `check` uses: the first language (in registry order) the repo has
    files of. A repo with nothing but TypeScript uses TypeScript's (which has no default command and says so);
    one with any Python uses Python's pytest, and so does one with no source at all."""
    root = check_repo(repo)
    if lang != "auto":
        return selected_profiles(lang)[0]
    files, _ = walk_languages(root, profiles())
    return next((profile for profile in profiles() if files[profile.name]), default_language().profile)


def merge_graphs(graphs: list[Graph]) -> Graph:
    """One graph from the graphs of the languages of a repo (see the module comment).

    One graph is returned as it is, so a single-language repo is exactly what its profile built.
    The graphs are taken in order (load_repo lists them in registry order, Python first). An id that an earlier graph already
    defines is not merged: the later graph's symbol is dropped, with every edge, unresolved call, base
    and external base of that graph that names the id, and the id goes in ambiguous_ids with both
    files (every file, when three or more languages define it). The earlier graph is untouched, so nothing of it is
    ever joined to another language's code.
    """
    if len(graphs) == 1:
        return graphs[0]
    symbols, owners = {}, {}
    merged = {"edges": [], "unresolved": [], "decorations": [], "parse_errors": [], "unread": [], "modules": {}, "bases": {}, "external_bases": {}, "export_aliases": {}}
    skipped_dirs, external_calls, reasons = set(), 0, {}
    for graph in graphs:
        drop = {sid for sid in graph.symbols if sid in symbols}
        for sid, symbol in graph.symbols.items():
            if sid in drop:
                owners.setdefault(sid, [symbols[sid].file]).append(symbol.file)
            else:
                symbols[sid] = symbol
        merged["edges"] += [e for e in graph.edges if not drop & {e.caller, e.callee, e.via}]
        merged["unresolved"] += [u for u in graph.unresolved if u.caller not in drop]
        merged["decorations"] += [d for d in graph.decorations if not drop & {d.holder, d.target}]
        merged["export_aliases"].update(
            {key: alias for key, alias in graph.export_aliases.items() if key.partition(":")[0] not in drop and alias[0] not in drop}
        )
        merged["parse_errors"] += graph.parse_errors
        merged["unread"] += graph.unread
        merged["modules"].update(graph.modules)
        for cls, bases in graph.bases.items():
            kept = [base for base in bases if base not in drop]
            if cls not in drop and (kept or not bases):
                merged["bases"][cls] = kept
        merged["external_bases"].update({c: b for c, b in graph.external_bases.items() if c not in drop})
        skipped_dirs.update(graph.skipped_dirs)
        external_calls += graph.external_calls
        for reason, n in graph.external_reasons.items():
            reasons[reason] = reasons.get(reason, 0) + n
    return Graph(
        symbols=symbols,
        external_calls=external_calls,
        skipped_dirs=sorted(skipped_dirs),
        external_reasons=reasons,
        ambiguous_ids={sid: sorted(files) for sid, files in owners.items()},
        **merged,
    )


def ambiguity_warning(ids: dict[str, list[str]]) -> str:
    """The warning for ids more than one language defines, naming the languages by label. For each id the
    earliest language in the registry keeps it and the others lose it, so with three languages the kept
    and the left-out ones are lists ("kept the Python symbol and left out the TypeScript or Go one")."""
    kept, left_out = set(), set()
    for files in ids.values():
        names = sorted({language_of(file).profile.name for file in files}, key=list(LANGUAGES).index)
        kept.add(names[0])
        left_out.update(names[1:])
    involved = [name for name in LANGUAGES if name in kept | left_out]
    who = "both languages" if len(involved) == 2 else join([label(name) for name in involved])
    keeps = join([label(name) for name in LANGUAGES if name in kept], "or")
    loses = join([label(name) for name in LANGUAGES if name in left_out], "or")
    shown = ", ".join(f"{sid} ({', '.join(files)})" for sid, files in list(ids.items())[:SHOWN_AMBIGUOUS])
    more = f" and {len(ids) - SHOWN_AMBIGUOUS} more" if len(ids) > SHOWN_AMBIGUOUS else ""
    return (
        f"{who} define {plural(len(ids), 'id')}: ripple kept the {keeps} symbol and left out the {loses} one "
        f"and the calls that name it, and does not answer questions about them: {shown}{more}"
    )


def module_collisions(graph: Graph) -> dict[str, list[str]]:
    """Graph.module_collisions for each language on its own: files of one language that share a module
    name share ids. A Python file and a TypeScript file with the same id are the merge's business
    (ambiguity_warning), and their symbols are not mixed."""
    groups: dict[tuple[str, int], list[str]] = {}
    for file, module in graph.modules.items():
        groups.setdefault((module, rank(file)), []).append(file)
    found: dict[str, list[str]] = {}
    for (module, _), files in sorted(groups.items()):
        if len(files) > 1 and not folds_files(files[0]):  # a Go package is many files by design
            found.setdefault(module, []).extend(sorted(files))
    return found


def dual_format_files(graph: Graph) -> dict[str, str]:
    """{file: the file beside it that keeps the id} for each JavaScript file that is one source shipped in another format
    of a file with the same path (x.mjs and x.cjs beside x.js, the layout of a package with both module systems). Both are
    read in full, the one that an import of `./x` reaches under `x` and the others under their own path (`x.mjs`), so this
    is a note on what to ask about, not a file that failed to parse (which is what parse_errors lists)."""
    owners = {module: file for file, module in graph.modules.items()}
    unparsed = set(graph.parse_errors)
    found = {}
    for file, module in graph.modules.items():
        if not language_of(file).path_ids or folds_files(file) or file in unparsed:  # a Go package is many files, not one in two formats
            continue
        keeper = owners.get(module_id(file))
        if keeper is not None and keeper != file and module != module_id(file):
            found[file] = keeper
    return found


def skipped_message(language: LanguageIndex, error: Exception, indexed: list[LanguageIndex]) -> str:
    """What a mixed repo's answers lack, and what to do about it."""
    covered = join([other.label for other in indexed])
    return (
        f"{language.label} was not indexed ({plural(language.found, 'file')} found): {error}. "
        f"These answers cover the {covered} files only."
    )


def only_language_message(repo: str | Path, language: LanguageIndex, error: Exception) -> str:
    return f"cannot index {repo}: its {plural(language.found, language.label + ' file')} need {language.label} support. {error}"


def with_warnings(result: dict, warnings: list[str]) -> dict:
    """The result with a `warnings` key when there is something the reader must know; untouched when not."""
    return {**result, "warnings": list(warnings)} if warnings else result
