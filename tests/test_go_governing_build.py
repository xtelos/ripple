"""The governing module through the Go indexer: what a call edge and a build unit take from go.mod (ripple/go_indexer.py).

The oracle's answers per probe are held to by test_go_probes.py and test_go_implements.py; what the oracle cannot show is
asserted here. A library file of module a is in the builds of every module that builds against a, and a call edge from it is one
callee only when they all name the same package: the oracle's runs see a file of a as a root only in a's own run, so a callee a
published version of `other` takes in the root module's build is invisible to it.
"""

from pathlib import Path

import pytest
from test_go_probes import HAS_GO, PROBES, ripple_in

pytestmark = pytest.mark.skipif(not HAS_GO, reason="Go support is not installed (pip install 'ripple-code[go]')")


def edges_in(name: str, file: str):
    _, resolver, _ = ripple_in(PROBES / name)
    return [e for e in resolver.edges if e.file == file], resolver


def test_a_call_from_a_dependency_file_has_no_callee_when_two_builds_name_different_packages_for_it():
    # a/a.go calls other.F; module a replaces other with the checkout b/, the root module (which replaces a) does not
    mine, resolver = edges_in("impl_chain_published", "a/a.go")
    assert mine == [], "the root's build takes the published other, a's own build takes b/: no one callee"
    assert any(u.file == "a/a.go" for u in resolver.unresolved_calls)
    assert not any(e.file == "cmd/p/main.go" and "other" in e.callee for e in resolver.edges)
    from_main, _ = edges_in("impl_chain_published", "cmd/p/main.go")
    assert [e.callee for e in from_main] == ["example.com/a:G"], "the root replaces a by the checkout: one build, one answer"


def test_a_call_to_a_module_the_build_takes_published_is_no_call_edge_into_the_checkout():
    # the replace is of v1.2.0 and the build selects v1.3.0
    mine, resolver = edges_in("impl_replace_version", "a.go")
    assert mine == []
    assert resolver.implement_edges == []


def test_a_vendored_build_takes_neither_the_checkout_nor_outside_code_for_a_module_it_replaces():
    mine, resolver = edges_in("impl_vendored", "a.go")
    assert mine == [] and resolver.implement_edges == []


def test_the_main_module_that_replaces_a_dependency_of_a_dependency_links_the_checkout():
    _, resolver, _ = ripple_in(Path(PROBES / "impl_chain_replaced_transitive"))
    assert {e.callee for e in resolver.implement_edges} == {"example.com/other:T.M"}


def test_a_module_in_a_directory_that_is_not_its_path_is_found_by_its_path():
    _, resolver, _ = ripple_in(Path(PROBES / "impl_module_dir_path"))
    assert {e.callee for e in resolver.implement_edges} == {"example.com/y/pkg:T.M"}
