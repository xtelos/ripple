"""A failure id `check` prints is one `check` accepts back, whatever wrote the test (captures, no cargo needed).

The up-front refusal "the file has no such module" is a reading of the source, and source cannot see what a macro, an attribute macro or
`include!` makes. It is made only where the source is certain; otherwise the test binary's own list decides, as it does for names.
"""

import pytest
from rs_check_support import meta, names, needs_rust_parser, output_text, repo_of, verdict

from ripple.rs_check import Model, _bare_path, normalize, parse_output, parse_wants


def libtest_failures(name):
    """(the libtest name of each unit test that failed in the capture, in the order the verdict lists them)."""
    out = parse_output(output_text(name))
    return [test for s in out.sections if not s.doc for run in s.runs for test, state in run.tests.items() if state == "FAILED"]


def failure_ids(result):
    return [f[len("FAILED ") :].split(": ", 1)[0] for f in result["failures"] if not f.startswith("FAILED doctest")]


@needs_rust_parser
def test_every_failure_id_of_every_capture_is_taken_back_and_asks_for_exactly_that_test():
    seen = 0
    for name in names():
        if meta(name)["by_hand"]:
            continue  # a command ripple never builds (or refuses): the ids of its failures are not ripple's to print
        result = verdict(name)
        ids, failed = failure_ids(result), libtest_failures(name)
        if not ids:
            continue
        assert len(ids) == len(failed), (name, ids, failed)
        repo = repo_of(name)
        for ident, libtest in zip(ids, failed):
            try:
                request = parse_wants(repo, normalize(repo, [ident]), Model(repo))
            except ValueError as error:
                raise AssertionError(f"{name}: check refuses the id it printed, {ident!r}: {error}") from error
            asked = {_bare_path(n) for pick in request.picks for n in (pick.names or ())}
            assert _bare_path(libtest) in asked, (name, ident, libtest, asked)
            seen += 1
    assert seen >= 15  # the captures hold plenty of failures: a loop over none would pass


@needs_rust_parser
@pytest.mark.parametrize("name", ["genmod_all", "genmod_made_id", "genmod_here_id", "genmod_file"])
def test_a_failure_in_a_module_a_macro_writes_is_named_by_the_file_of_the_macro_call(name):
    ids = failure_ids(verdict(name))
    expected = {
        "genmod_all": ["src/genmod.rs::made::g", "src/lib.rs::here::g"],
        "genmod_made_id": ["src/genmod.rs::made::g"],
        "genmod_here_id": ["src/lib.rs::here::g"],
        "genmod_file": ["src/genmod.rs::made::g"],
    }[name]
    assert sorted(ids) == expected


@needs_rust_parser
def test_a_file_with_a_macro_that_writes_a_module_runs_the_generated_tests_of_that_file():
    result = verdict("genmod_file")
    assert result["counts"]["passed"] == 1 and result["counts"]["failed"] == 1
    assert result["problems"] == []


@needs_rust_parser
def test_a_module_a_macro_writes_is_not_refused_up_front_the_list_decides():
    repo = repo_of("genmod_all")
    for given, name in (
        ("src/genmod.rs::made::g", "genmod::made::g"),  # a macro defined in another file, called in this one
        ("src/lib.rs::here::g", "here::g"),  # a macro_rules of the same file
    ):
        [pick] = parse_wants(repo, [given], Model(repo)).picks
        assert pick.names == (name,) and pick.kind == "lib"


@needs_rust_parser
def test_a_typo_in_the_module_of_a_file_with_macros_is_a_test_the_list_does_not_have():
    result = verdict("genmod_typo")
    assert result["passed"] is False
    assert any("no test named genmod::made_typo::g exists in genmod" in p for p in result["problems"]), result["problems"]


@needs_rust_parser
def test_a_typo_in_the_module_of_a_file_with_no_macros_is_still_refused_before_anything_runs():
    repo = repo_of("genmod_all")
    with pytest.raises(ValueError, match="src/nomacro.rs has no module 'tpyo'"):
        parse_wants(repo, ["src/nomacro.rs::tpyo::fine"], Model(repo))
    [pick] = parse_wants(repo, ["src/nomacro.rs::tests::fine"], Model(repo)).picks
    assert pick.names == ("nomacro::tests::fine",)


# ---- where the source is certain and where it is not ------------------------------------------------


def crate(tmp_path, body, extra=None):
    (tmp_path / "Cargo.toml").write_text('[package]\nname = "c"\nversion = "0.1.0"\nedition = "2021"\n')
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "lib.rs").write_text("mod m;\n")
    (tmp_path / "src" / "m.rs").write_text(body)
    for rel, text in (extra or {}).items():
        (tmp_path / rel).write_text(text)
    return tmp_path


CERTAIN = [
    "#[cfg(test)]\nmod tests {\n    #[test]\n    fn t() {}\n}\n",
    "#[derive(Debug)]\n#[allow(dead_code)]\npub struct S;\n#[cfg(test)]\nmod tests {\n    #[test]\n    #[ignore]\n    #[should_panic]\n    fn t() { println!(\"x\"); assert_eq!(1, 1); }\n}\n",
    "#[inline]\n#[must_use]\npub fn f() -> i32 { vec![1].len() as i32 }\n",
]
UNCERTAIN = [
    "macro_rules! mk { ($n:ident) => { mod $n {} } }\nmk!(made);\n",  # an item macro
    "mk! { made }\n",
    "include!(\"gen.rs\");\n",
    "macro_rules! m { () => { include!(\"gen.rs\"); } }\n",  # an include! the source only mentions: read as opaque all the same
    "#[cfg(test)]\nmod tests {\n    #[rstest]\n    fn t() {}\n}\n",  # an attribute macro (it makes a module of its own)
    "#[some_crate::generate]\nmod tests {}\n",
    "#[cfg(test)]\nmod tests {\n    mk!(inner);\n}\n",  # a macro in an inline module
]


@needs_rust_parser
@pytest.mark.parametrize("body", CERTAIN)
def test_where_the_source_is_certain_a_module_it_does_not_have_is_refused(tmp_path, body):
    repo = crate(tmp_path, body)
    with pytest.raises(ValueError, match="src/m.rs has no module 'nope'"):
        parse_wants(repo, ["src/m.rs::nope::t"], Model(repo))


@needs_rust_parser
@pytest.mark.parametrize("body", UNCERTAIN)
def test_where_a_macro_an_attribute_macro_or_include_may_write_modules_the_list_decides(tmp_path, body):
    repo = crate(tmp_path, body, {"src/gen.rs": ""})
    [pick] = parse_wants(repo, ["src/m.rs::nope::t"], Model(repo)).picks
    assert pick.names == ("m::nope::t",)


@needs_rust_parser
def test_the_known_part_of_the_module_path_is_kept_when_the_rest_is_left_to_the_list(tmp_path):
    repo = crate(tmp_path, "#[cfg(test)]\nmod tests {\n    mk!(inner);\n}\n")
    [pick] = parse_wants(repo, ["src/m.rs::tests::inner::t"], Model(repo)).picks
    assert pick.names == ("m::tests::inner::t",)
