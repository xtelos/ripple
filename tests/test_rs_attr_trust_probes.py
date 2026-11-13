"""What ripple says at the call sites of the trust_* probes (bench/fixtures/rust-probes), pinned site by site.

The differential suite (test_rs_probes.py) proves nothing ripple says there is false, but it cannot tell an edge that is
missing from one that is drawn: a ripple that trusted no attribute macro would pass it. This pins both directions.

The positive probes run the published tokio 1.32 and serial_test 2.0 (their `#[test]`s assert which function ran, and
bench/rs_probe_oracle.py ran them offline), and each call under such an attribute is an edge. In the negative probes the
attribute is a macro of crates/_ext/ that rewrites `one()` into `two()`: the oracle gives the call as `two` (proven by the
crate's own assertion, where rust-analyzer says `one`), so an edge to `one` would be false, and ripple says it is
unresolved with ATTR_MACRO.
"""

import pytest

pytest.importorskip("tree_sitter_rust")

from ripple import rs_indexer as rs  # noqa: E402
from test_rs_probes import Probe  # noqa: E402

EDGE, ATTR, MAKE = "edge", "ATTR_MACRO", "MACRO_MAY_MAKE"

# (probe, file, line, callee as written, "edge" and the callee's id, or the reason constant it is unresolved with)
SITES = [
    # ---- the published macros, proven by the crate's tests: the body is an ordinary function's
    pytest.param("trust_tokio_main", "src/lib.rs", 9, "helper", EDGE, "src/lib:helper", id="tokio::main: a free async fn, the local helper beats the glob"),
    pytest.param("trust_tokio_main", "src/lib.rs", 12, "run", ATTR, "", id="tokio::main: the name the function declares stays unresolved"),
    pytest.param("trust_tokio_main", "src/main.rs", 5, "tick", EDGE, "src/main:tick", id="tokio::main on main in a binary"),
    pytest.param("trust_tokio_test_dev_dep", "tests/t.rs", 10, "helper", EDGE, "src/lib:helper", id="tokio::test, tokio only a dev-dependency"),
    pytest.param("trust_tokio_args", "src/lib.rs", 9, "helper", EDGE, "src/lib:helper", id="tokio::main(flavor = current_thread)"),
    pytest.param("trust_tokio_args", "src/lib.rs", 17, "helper", EDGE, "src/lib:helper", id="tokio::test(flavor = current_thread)"),
    pytest.param("trust_tokio_args", "src/lib.rs", 22, "helper", EDGE, "src/lib:helper", id="tokio::test(flavor = multi_thread, worker_threads = 2)"),
    pytest.param("trust_tokio_args", "src/lib.rs", 27, "helper", EDGE, "src/lib:helper", id="tokio::test(start_paused = true)"),
    pytest.param("trust_tokio_args", "src/lib.rs", 32, "helper", EDGE, "src/lib:helper", id="tokio::test(crate = tokio)"),
    pytest.param("trust_tokio_args", "src/lib.rs", 37, "super::run", ATTR, "", id="the name of a function with arguments stays unresolved"),
    pytest.param("trust_comment_in_attr", "src/lib.rs", 15, "helper", EDGE, "src/lib:helper", id="comment between tokio :: test"),
    pytest.param("trust_comment_in_attr", "src/lib.rs", 21, "helper", EDGE, "src/lib:helper", id="line comment after tokio::"),
    pytest.param("trust_comment_in_attr", "src/lib.rs", 26, "helper", EDGE, "src/lib:helper", id="comments inside # [ tokio::test ]"),
    pytest.param("trust_comment_in_attr", "src/lib.rs", 32, "helper", EDGE, "src/lib:helper", id="comment inside serial_test :: serial, and in its use"),
    pytest.param("trust_comment_in_attr", "src/lib.rs", 38, "helper", EDGE, "src/lib:helper", id="a comment that says inner_attrs in the arguments of serial"),
    pytest.param("trust_global_path_local_mod", "src/lib.rs", 14, "helper", EDGE, "src/lib:helper", id="::tokio::test beats a module called tokio"),
    pytest.param("trust_global_path_local_mod", "src/lib.rs", 14, "tokio::marker", EDGE, "src/lib:t::tokio::marker", id="and the module is still the module"),
    pytest.param("trust_serial_forms", "src/lib.rs", 15, "helper", EDGE, "src/lib:helper", id="#[serial]"),
    pytest.param("trust_serial_forms", "src/lib.rs", 21, "helper", EDGE, "src/lib:helper", id="#[serial(key)]"),
    pytest.param("trust_serial_forms", "src/lib.rs", 27, "helper", EDGE, "src/lib:helper", id="#[serial(one, two)]"),
    pytest.param("trust_serial_forms", "src/lib.rs", 33, "helper", EDGE, "src/lib:helper", id="#[serial_test::serial]"),
    pytest.param("trust_serial_forms", "src/lib.rs", 39, "helper", EDGE, "src/lib:helper", id="#[cfg_attr(test, serial)]"),
    pytest.param("trust_workspace_inherited", "app/src/lib.rs", 9, "helper", EDGE, "app/src/lib:helper", id="tokio = { workspace = true, features } and a plain root entry"),
    pytest.param("trust_workspace_inherited", "app/src/lib.rs", 17, "helper", EDGE, "app/src/lib:helper", id="tokio::test of the same"),
    pytest.param("trust_workspace_inherited", "app/src/lib.rs", 22, "super::run", ATTR, "", id="the name stays unresolved here too"),
    pytest.param("trust_scope_not_marked", "src/lib.rs", 9, "helper", EDGE, "src/lib:helper", id="a trusted test reaching a name through a glob"),
    pytest.param("trust_scope_not_marked", "src/lib.rs", 13, "helper", EDGE, "src/lib:helper", id="a function beside it finds the name through the glob: the scope is not marked"),
    pytest.param("trust_scope_not_marked", "src/lib.rs", 18, "found_only_through_the_glob", EDGE, "src/lib:t::found_only_through_the_glob", id="and an ordinary test calls it"),
    # ---- the same attributes where the repo does not prove them, or on what the rule leaves alone
    pytest.param("trust_path_dep_named_tokio", "src/lib.rs", 5, "one", ATTR, "", id="tokio = { path }"),
    pytest.param("trust_renamed_dep", "src/lib.rs", 5, "one", ATTR, "", id="tokio = { package = faketokio, path }"),
    pytest.param("trust_workspace_path", "member/src/lib.rs", 5, "one", ATTR, "", id="the workspace's own entry for tokio is a path"),
    pytest.param("trust_target_table_path", "src/lib.rs", 5, "one", ATTR, "", id="a [target.cfg] table with a path"),
    pytest.param("trust_patch_macro_crate", "src/lib.rs", 5, "one", ATTR, "", id="[patch.crates-io] tokio-macros"),
    pytest.param("trust_config_paths", "src/lib.rs", 5, "one", ATTR, "", id=".cargo/config.toml paths"),
    pytest.param("trust_replace", "src/lib.rs", 5, "one", ATTR, "", id="[replace] tokio-macros"),
    pytest.param("trust_extern_crate_alias", "src/lib.rs", 6, "one", ATTR, "", id="extern crate fakemacros as tokio"),
    pytest.param("trust_repo_is_tokio", "tests/own_main.rs", 5, "one", ATTR, "", id="the repo is the package tokio"),
    pytest.param("trust_shadowed_names", "src/lib.rs", 13, "super::one", ATTR, "", id="a module called tokio that re-exports another macro"),
    pytest.param("trust_shadowed_names", "src/lib.rs", 18, "super::one", ATTR, "", id="use fakemacros as tokio"),
    pytest.param("trust_shadowed_names", "src/lib.rs", 23, "super::one", ATTR, "", id="use fakemacros::serial"),
    pytest.param("trust_shadowed_names", "src/lib.rs", 28, "super::one", ATTR, "", id="beside another attribute macro"),
    pytest.param("trust_shadowed_names", "src/lib.rs", 38, "super::one", ATTR, "", id="tokio::main on an impl method: the published macro, left alone"),
    pytest.param("trust_shadowed_names", "src/lib.rs", 40, "super::one", ATTR, "", id="serial on an impl method: the published macro, left alone"),
    pytest.param("trust_member_named_macro_crate", "app/src/lib.rs", 7, "helper", ATTR, "", id="a repo package called tokio-macros: refused, though the macro is the real one"),
    pytest.param("trust_serial_after_tokio_test", "src/lib.rs", 17, "_u_internal", MAKE, "", id="serial on an async fn: refused whatever the order of the attributes (stricter than needed here)"),
]


@pytest.fixture(scope="module")
def probes():
    return {}


@pytest.mark.parametrize("probe, file, line, written, kind, callee", SITES)
def test_what_ripple_says_at_the_site(probes, probe, file, line, written, kind, callee):
    p = probes.setdefault(probe, Probe(probe))
    found = [(result, f) for f, _, _, result, _, ln, w, _ in p.index.resolved_sites() if f == file and ln == line and w == written]
    assert len(found) == 1, f"{probe} {file}:{line} `{written}`: {len(found)} sites"
    result = found[0][0]
    if kind == EDGE:
        assert result[0] == "edge" and p.index.final[result[1]] == callee, result
    else:
        assert result == ("unresolved", getattr(rs, kind)), result


def test_the_oracle_of_each_negative_probe_gives_the_function_the_crates_own_test_proved_ran():
    """The oracle says the call of `one()` is `two` (proven by the crate's test), where rust-analyzer says `one`."""
    import json

    from test_rs_probes import CRATES, ORACLE

    negatives = (
        ("trust_path_dep_named_tokio", "src/lib.rs", 5),
        ("trust_renamed_dep", "src/lib.rs", 5),
        ("trust_workspace_path", "member/src/lib.rs", 5),
        ("trust_target_table_path", "src/lib.rs", 5),
        ("trust_patch_macro_crate", "src/lib.rs", 5),
        ("trust_config_paths", "src/lib.rs", 5),
        ("trust_replace", "src/lib.rs", 5),
        ("trust_extern_crate_alias", "src/lib.rs", 6),
        ("trust_repo_is_tokio", "tests/own_main.rs", 5),
        ("trust_shadowed_names", "src/lib.rs", 13),
        ("trust_shadowed_names", "src/lib.rs", 18),
        ("trust_shadowed_names", "src/lib.rs", 23),
        ("trust_shadowed_names", "src/lib.rs", 28),
    )
    for name, file, line in negatives:
        source = (CRATES / name / file).read_text().splitlines()
        two = 1 + next(i for i, written in enumerate(source) if written.startswith("fn two()"))
        calls = [c for c in json.loads((ORACLE / f"{name}.json").read_text())["calls"] if c["file"] == file and c["line"] == line and c["text"] == "one"]
        assert [(c.get("proof"), c["kind"], [tuple(d) for d in c["defs"]]) for c in calls] == [("runtime", "repo", [(file, two)])], (name, calls)
