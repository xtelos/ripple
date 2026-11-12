"""The languages ripple reads, in the order they win an id, and the front end of each.

LANGUAGES is the one list. Adding a language is a LanguageProfile (profile.py) and one entry here:
the profile, the label its messages use, and the name of the module that is its front end. The order
is precedence: when two languages define one id (languages.merge_graphs) the earlier one keeps it,
a file no profile owns is read as the first language's, and the first language's cache is the one
written without a profile key (cache.profile_key). Python is first. A language whose module ids hold more than one
file by design (a Go package) says so with folds_files, so sharing an id is not reported as a collision. A language whose
members are joined by `::` (Rust) says so with colon_members, so a query is split at the colon that ends the path only for ids that are its.

A front end is a module, imported by name the first time something of it is needed, so nothing of a
language (and none of its parser, tree-sitter for TypeScript) loads until a file of it is read. It
defines
  index_files(repo, files, skipped, profile) -> Graph      reads the files into a graph (required)
and may define
  config_files(repo, sources) -> list[str]                 repo-relative files besides the profile's
                                                           config_files that the index depends on, which
                                                           the cache fingerprints too
  presence_files(repo, sources) -> list[str]               repo-relative files the index depends on only by
                                                           their being there (another language's sources),
                                                           fingerprinted without their size or time
  output_dirs(repo, directory, filenames) -> list[str]     repo-relative directories that the config files in
                                                           `directory` name as build output, which the walk
                                                           skips and lists (compiled code is not source)
  reads_as_source(path, profile, repo) -> bool             False for a file the walk should not count
                                                           though its suffix says it is source (repo is the
                                                           directory the walk started from)
  ignored_directory(path, profile, repo) -> str | None     the repo-relative directory a source file is not
                                                           indexed for (the go tool skips `vendor`, `testdata`
                                                           and `_x`), which the walk lists with the skipped
                                                           directories; None when the file is skipped for another reason
  module_id(file) -> str                                   the id a source file's path names (a language
                                                           whose Language.path_ids is set defines it)
  NotInstalled                                            the ImportError subclass that says its parser
                                                           is missing, so a mixed repo can go on without it
The walk and the cache fingerprint import a front end to ask it reads_as_source and config_files, so a
front end module must not import its parser at module level: load the parser inside index_files and raise
NotInstalled there, or a repo that merely contains one of its files would crash instead of skipping it.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from types import ModuleType

from .profile import GO, JAVASCRIPT, PYTHON, RUST, TYPESCRIPT, LanguageProfile


@dataclass(frozen=True)
class Language:
    profile: LanguageProfile
    label: str  # how messages name the language
    front_end: str  # the module that reads its sources, imported when first needed
    # The language's ids are a file path and a member (`src/cart:total`), so a query may name a symbol by its
    # file's path, with or without the extension (a Go id is an import path and a member, and a file's path finds it
    # the same way). Kept here and not on the profile: cache.profile_key hashes
    # every field of a profile, so a new field there would change the cache key of the languages that already exist.
    path_ids: bool = False
    folds_files: bool = False  # many files are one module id by design (a Go package), so sharing one is no collision
    # A member of its ids is joined by `::` (`src/lib:Foo::new`), so a `::` in the id is a member separator. Only for such a
    # language: a TypeScript test title may hold `::` (`tests/a.test:parses a::b`), so the look of an id never decides it.
    colon_members: bool = False


LANGUAGES: dict[str, Language] = {
    language.profile.name: language
    for language in (
        Language(PYTHON, "Python", "ripple.indexer"),
        Language(TYPESCRIPT, "TypeScript", "ripple.ts_indexer", path_ids=True),
        Language(JAVASCRIPT, "JavaScript", "ripple.ts_indexer", path_ids=True),
        Language(GO, "Go", "ripple.go_indexer", path_ids=True, folds_files=True),
        Language(RUST, "Rust", "ripple.rs_indexer", path_ids=True, colon_members=True),
    )
}


def default_language() -> Language:
    """The first language: what a file no profile owns is read as."""
    return next(iter(LANGUAGES.values()))


def profiles() -> list[LanguageProfile]:
    return [language.profile for language in LANGUAGES.values()]


def label(name: str) -> str:
    return LANGUAGES[name].label if name in LANGUAGES else name


def join(names: list[str], word: str = "and") -> str:
    """A list as prose: "Python", "Python and TypeScript", "Python, TypeScript and Go" (or with `or`)."""
    return f" {word} ".join([", ".join(names[:-1]), names[-1]] if len(names) > 2 else names)


def language_of(file: str) -> Language:
    """The language that reads a file, by its suffix: the earliest whose source suffixes end it and whose
    excluded suffixes (declaration files) do not. A file none of them claims is the default's."""
    for language in LANGUAGES.values():
        profile = language.profile
        if file.endswith(profile.source_suffixes) and not file.endswith(profile.excluded_suffixes):
            return language
    return default_language()


def folds_files(file: str) -> bool:
    """Whether the language that reads a file gives all the files of a directory one module id."""
    return language_of(file).folds_files


def rank(file: str) -> int:
    """Where the language that reads a file stands in LANGUAGES (0 is first)."""
    return list(LANGUAGES).index(language_of(file).profile.name)


def writes_colon_members(file: str) -> bool:
    """Whether the language that reads a file joins the members of its ids with `::`. Decided by the file of a symbol
    (or of an unresolved call), never by how an id looks."""
    return language_of(file).colon_members


def path_suffixes() -> tuple[str, ...]:
    """The source suffixes of every language whose ids are paths, in registry order."""
    return tuple(suffix for language in LANGUAGES.values() if language.path_ids for suffix in language.profile.source_suffixes)


def stem(file: str) -> str:
    """The file's path without the suffix its own language reads it by (the path unchanged when none does)."""
    suffixes = language_of(file).profile.source_suffixes
    return next((file[: -len(suffix)] for suffix in suffixes if file.endswith(suffix)), file)


def module_id(file: str) -> str:
    """The id a source file's path names, as the front end of its language folds it (an index file is its
    directory). A path no path-id language reads is folded as the first of them would, and a front end that
    defines no module_id leaves the path as it is."""
    language = language_of(file)
    if not language.path_ids:
        language = next((other for other in LANGUAGES.values() if other.path_ids), language)
    fold = getattr(front_end(language.profile), "module_id", None)
    return file if fold is None else fold(file)


def front_end(profile: LanguageProfile) -> ModuleType:
    """The module that reads the sources of a profile: the front end of the registered language that
    shares its front_end name, so a profile made from another's (a Python one with another constructor
    name) is read the same way."""
    for language in LANGUAGES.values():
        if language.profile.front_end == profile.front_end:
            return importlib.import_module(language.front_end)
    raise ValueError(f"no front end is registered as '{profile.front_end}'")


def is_missing_parser(error: ImportError, profile: LanguageProfile) -> bool:
    """Whether an ImportError from building a profile's graph is its front end saying its parser is not installed."""
    missing = getattr(front_end(profile), "NotInstalled", None)
    return missing is not None and isinstance(error, missing)
