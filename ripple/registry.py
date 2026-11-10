"""The languages ripple reads, in the order they win an id, and the front end of each.

LANGUAGES is the one list. Adding a language is a LanguageProfile (profile.py) and one entry here:
the profile, the label its messages use, and the name of the module that is its front end. The order
is precedence: when two languages define one id (languages.merge_graphs) the earlier one keeps it,
a file no profile owns is read as the first language's, and the first language's cache is the one
written without a profile key (cache.profile_key). Python is first.

A front end is a module, imported by name the first time something of it is needed, so nothing of a
language (and none of its parser, tree-sitter for TypeScript) loads until a file of it is read. It
defines
  index_files(repo, files, skipped, profile) -> Graph      reads the files into a graph (required)
and may define
  config_files(repo, sources) -> list[str]                 repo-relative files besides the profile's
                                                           config_files that the index depends on, which
                                                           the cache fingerprints too
  reads_as_source(path, profile) -> bool                   False for a file the walk should not count
                                                           though its suffix says it is source
  NotInstalled                                             the ImportError subclass that says its parser
                                                           is missing, so a mixed repo can go on without it
The walk and the cache fingerprint import a front end to ask it reads_as_source and config_files, so a
front end module must not import its parser at module level: load the parser inside index_files and raise
NotInstalled there, or a repo that merely contains one of its files would crash instead of skipping it.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from types import ModuleType

from .profile import PYTHON, TYPESCRIPT, LanguageProfile


@dataclass(frozen=True)
class Language:
    profile: LanguageProfile
    label: str  # how messages name the language
    front_end: str  # the module that reads its sources, imported when first needed


LANGUAGES: dict[str, Language] = {
    language.profile.name: language
    for language in (
        Language(PYTHON, "Python", "ripple.indexer"),
        Language(TYPESCRIPT, "TypeScript", "ripple.ts_indexer"),
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


def rank(file: str) -> int:
    """Where the language that reads a file stands in LANGUAGES (0 is first)."""
    return list(LANGUAGES).index(language_of(file).profile.name)


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
