"""The findings of the second adversarial review of the Rust front end, each as the answer ripple gives now at the probe's call site.

The differential suite (test_rs_probes.py) proves nothing ripple says there is false; this pins what it says and which
reason it gives, so a finding fixed by making a call unresolved cannot slide back to an edge (or to outside the repo)
unnoticed. F1 to F10 are the review's own numbers. The probes beginning r2_ are the reviewer's crates, the ones
beginning r3_ were written for the classes the findings belong to.
"""

import pytest

pytest.importorskip("tree_sitter_rust")

from ripple import rs_indexer as rs  # noqa: E402
from test_rs_probes import Probe  # noqa: E402

Q1, Q2, Q3, Q8, Q9, Q10 = (
    "r2_q1_impl_tables",
    "r2_q2_glob_vis_macro_use",
    "r2_q3_private_const_cfg_raw",
    "r2_q8_attr_proc_macro",
    "r2_q9_edition_2015",
    "r2_q10_thread_local_glob",
)
INC, MAKE = "IMPLS_INCOMPLETE", "MACRO_MAY_MAKE"

# (probe, line, callee as written, what ripple settles it to, the reason constant or the callee's id)
FINDINGS = [
    # F1: the impl tables miss what a macro or an impl ripple could not resolve adds
    pytest.param(Q1, 9, "S::f", "unresolved", INC, id="F1 a macro in an inherent impl body"),
    pytest.param(Q1, 16, "U::f", "unresolved", INC, id="F1 a module-level macro that expands to an inherent impl"),
    pytest.param(Q1, 25, "W::f", "unresolved", INC, id="F1 the macro in another module"),
    pytest.param(Q1, 32, "X::d", "unresolved", INC, id="F1 a macro in a trait impl overrides the default"),
    pytest.param(Q1, 33, "<X as D>::d", "unresolved", "IMPL_MACRO", id="F1 qualified, the impl's own macro may define it"),
    pytest.param(Q1, 41, "<V as Conv<V>>::conv", "unresolved", INC, id="F1 a macro-made blanket impl"),
    pytest.param(Q10, 10, "Bar::f", "unresolved", INC, id="F1 an inherent impl in a thread_local! module through a glob"),
    pytest.param("r3_macro_inherent_in_impl_body", 8, "Self::f", "unresolved", INC, id="F1 Self::f beside a macro in the impl"),
    pytest.param("r3_macro_inherent_in_impl_body", 11, "<S as T>::f", "edge", "src/lib:<S as T>::f", id="F1 control: the named impl of a trait without parameters"),
    pytest.param("r3_macro_nested", 8, "S::f", "unresolved", INC, id="F1 a macro invoked by a macro"),
    pytest.param("r3_macro_in_fn_body_impl", 9, "S::f", "unresolved", INC, id="F1 a macro invoked in a function body"),
    pytest.param("r3_macro_named_like_std", 7, "S::f", "unresolved", INC, id="F1 a repo macro named thread_local"),
    pytest.param("r3_macro_use_and_export", 15, "A::f", "unresolved", INC, id="F1 #[macro_use] and #[macro_export] macros"),
    pytest.param("r3_macro_generic_trait_hidden", 7, "S::conv", "unresolved", INC, id="F1 another impl of a trait with parameters"),
    pytest.param("r3_macro_use_cross_crate", 9, "Z::make", "unresolved", INC, id="F1 a macro of another crate, #[macro_use]"),
    pytest.param("r3_dollar_crate_cross_crate", 11, "Z::make", "unresolved", INC, id="F1 a macro of another crate that names $crate"),
    pytest.param("r3_macro_controls", 12, "<S as T>::f", "edge", "src/lib:<S as T>::f", id="F1 control: a macro elsewhere leaves the named impl"),
    pytest.param("r3_macro_controls", 11, "S::make", "edge", "src/lib:S::make", id="F1 control: an inherent function needs no complete table"),
    # F2: the one inherent function is not always the one called
    pytest.param(Q3, 11, "P::f", "unresolved", "INHERENT_NOT_VISIBLE", id="F2 a private inherent function, called from outside"),
    pytest.param(Q3, 27, "C::f", "unresolved", "CALLS_CONST", id="F2 an inherent const of the name"),
    pytest.param(Q3, 39, "Self::f", "edge", "src/lib:<G as T>::f", id="F2 Self in an impl for G<u16>, the inherent f is G<u8>'s"),
    pytest.param(Q3, 41, "G16::f", "edge", "src/lib:<G as T>::f", id="F2 through an alias"),
    pytest.param("r3_inherent_instantiation", 12, "G::f", "edge", "src/lib:<G as T>::f", id="F2 G::<u16>::f is the trait impl"),
    pytest.param("r3_inherent_instantiation", 13, "G::f", "edge", "src/lib:G::f", id="F2 G::<u8>::f is the inherent one"),
    pytest.param("r3_inherent_private_levels", 26, "P::f", "unresolved", "INHERENT_NOT_VISIBLE", id="F2 private to the module"),
    pytest.param("r3_inherent_private_levels", 26, "P::g", "edge", "src/lib:m::P::g", id="F2 pub(super) is visible in the parent"),
    pytest.param("r3_inherent_private_levels", 26, "P::i", "unresolved", "INHERENT_NOT_VISIBLE", id="F2 pub(in path) is not visible outside it"),
    pytest.param("r3_inherent_private_levels", 22, "P::f", "edge", "src/lib:m::P::f", id="F2 private is visible in the module's descendants"),
    pytest.param("r3_inherent_two_instantiations", 6, "G::f", "edge", "src/lib:G::f#2", id="F2 the impl for the argument the call writes"),
    pytest.param("r3_inherent_bounds", 9, "W::other", "unresolved", "INSTANTIATION", id="F2 an impl for String is not shown to be the call's"),
    # F3: a #[cfg] ripple does not evaluate
    pytest.param(Q1, 48, "Fc::f", "unresolved", "CFG_UNKNOWN", id="F3 a cfg'd inherent impl beside a trait impl"),
    pytest.param(Q3, 19, "helper", "unresolved", "CFG_UNKNOWN", id="F3 a cfg'd item beside a glob"),
    pytest.param("r3_cfg_inherent_impl", 18, "Fm::g", "unresolved", "CFG_UNKNOWN", id="F3 a cfg'd impl item"),
    pytest.param("r3_cfg_inherent_impl", 19, "Only::z", "unresolved", "SEVERAL_INHERENT", id="F3 twins in one impl"),
    pytest.param("r3_cfg_inherent_impl", 20, "Solo::only", "edge", "src/lib:Solo::only", id="F3 a cfg'd item nothing else could replace"),
    pytest.param("r3_cfg_inherent_impl", 24, "Dm::d", "unresolved", "CFG_UNKNOWN", id="F3 a cfg'd override of a trait's default"),
    pytest.param("r3_cfg_shadow_glob_block", 11, "helper", "unresolved", "CFG_UNKNOWN", id="F3 a cfg'd fn in a block"),
    pytest.param("r3_cfg_use_lines", 8, "helper", "unresolved", "SEVERAL", id="F3 twin use lines"),
    pytest.param("r3_cfg_use_lines", 13, "helper", "unresolved", "CFG_UNKNOWN", id="F3 a cfg'd use beside a glob"),
    pytest.param("r3_cfg_use_lines", 17, "run_helper", "edge", "src/lib:b::helper", id="F3 a cfg'd use nothing else could replace"),
    pytest.param("r3_cfg_mods", 6, "imp::go", "unresolved", "SEVERAL", id="F3 twin modules"),
    pytest.param("r3_cfg_statements_arms", 6, "helper", "unresolved", "LOCAL", id="F3 a cfg'd let"),
    pytest.param("r3_cfg_attr_forms", 7, "helper", "unresolved", "CFG_UNKNOWN", id="F3 a cfg_attr on the item"),
    # F4: a restricted-visibility item through a glob re-export
    pytest.param("r3_vis_reexport_forms", 20, "drop", "external", "PRELUDE", id="F4 pub(super) through pub use inner::*"),
    pytest.param("r3_vis_reexport_forms", 21, "drop", "external", "PRELUDE", id="F4 a private item through pub use inner::*"),
    pytest.param("r3_vis_reexport_forms", 22, "drop", "external", "PRELUDE", id="F4 pub(in path)"),
    pytest.param("r3_vis_reexport_forms", 23, "drop", "edge", "src/lib:m4::inner::drop", id="F4 pub(crate) through pub(crate) use is visible"),
    pytest.param("r3_vis_reexport_forms", 24, "drop", "external", "PRELUDE", id="F4 a private glob is not a re-export"),
    pytest.param("r3_vis_reexport_forms", 25, "drop", "external", "PRELUDE", id="F4 through two re-exports"),
    pytest.param("r3_vis_reexport_forms", 26, "drop", "edge", "src/lib:m7::far::drop", id="F4 pub(crate) through two pub uses"),
    # F5: a #[macro_use] extern crate may export a macro named like a standard one
    pytest.param(Q2, 47, "repo_fn", "unresolved", "MACRO_BODY", id="F5 assert! from #[macro_use] extern crate"),
    pytest.param("r3_macro_use_extern_std_names", 10, "repo_fn", "unresolved", "MACRO_BODY", id="F5 assert! again"),
    pytest.param("r3_macro_use_extern_std_names", 11, "repo_fn2", "unresolved", "MACRO_BODY", id="F5 vec!"),
    pytest.param("r3_macro_use_extern_std_names", 13, "helper", "unresolved", MAKE, id="F5 a dbg! of #[macro_use] extern crate binds the name it is given"),
    # the reviewer's mutants that survived: each is a reason that only the pinned site shows
    pytest.param("r3_blanket_assoc_reason", 6, "S::f", "unresolved", "BLANKET_IMPL", id="mutant wild-trait-assoc: a trait with a blanket impl, through the type"),
    pytest.param("r3_cfg_derive_and_impl", 6, "S::default", "unresolved", "DERIVE_AND_IMPL", id="mutant derive-and-impl: a derive and an impl that cfg keeps apart"),
    pytest.param("blanket_from_into", 11, "Celsius::from", "unresolved", "STD_BLANKET_AMBIGUOUS", id="mutant std-blanket: std's reflexive From"),
    pytest.param("tostring_display", 15, "V::to_string", "unresolved", "STD_BLANKET_AMBIGUOUS", id="mutant std-blanket: std's ToString"),
    pytest.param("underscore_imports", 14, "crate::S::hello", "unresolved", "TRAIT_NOT_VISIBLE", id="mutant trait-visible: a trait only imported as _ elsewhere"),
    # F6: attribute macros
    pytest.param(Q8, 7, "helper", "unresolved", MAKE, id="F6 a fn an attribute macro appends, in a module with a glob"),
    pytest.param(Q8, 13, "helper", "unresolved", MAKE, id="F6 the same in a block"),
    pytest.param("r3_attr_macros", 18, "one", "unresolved", "ATTR_MACRO", id="F6 a call inside an attributed fn"),
    pytest.param("r3_attr_macros", 23, "S::f", "unresolved", "ATTR_MACRO", id="F6 a type an attribute macro rewrote"),
    pytest.param("r3_attr_macros", 27, "U::f", "unresolved", INC, id="F6 a derive that is not a standard one"),
    pytest.param("r3_attr_macros", 29, "two", "unresolved", "ATTR_MACRO", id="F6 a call inside an identity attribute: not known to be one"),
    pytest.param("r3_attr_macros", 32, "super::two", "unresolved", "ATTR_MACRO", id="F6 a call inside an attributed module"),
    pytest.param("r3_attr_macros", 37, "Imp::make", "unresolved", "ATTR_MACRO", id="F6 a function of an attributed impl"),
    pytest.param("r3_attr_macros", 43, "V::make", "unresolved", "ATTR_MACRO", id="F6 a single name beside a derive that an import makes an attribute macro"),
    pytest.param("r3_attr_macros", 48, "W::make", "edge", "app/src/lib:W::make", id="F6 a single name beside a derive that nothing imports: its helper"),
    pytest.param("r3_attr_inert_control", 15, "Self::make", "edge", "src/lib:S::make", id="F6 control: #[inline], #[track_caller], derive(Clone, ..)"),
    pytest.param("r3_attr_inert_control", 19, "S::go", "edge", "src/lib:S::go", id="F6 control: inert attributes"),
    # F7: a 2015 crate's use items
    pytest.param(Q9, 2, "old::api::f", "unresolved", "EDITION", id="F7 a pub use inside a 2015 crate"),
    pytest.param(Q9, 2, "old::api::g", "unresolved", "EDITION", id="F7 a glob inside a 2015 crate"),
    pytest.param(Q9, 2, "old::reexported", "unresolved", "EDITION", id="F7 a use ... as inside a 2015 crate"),
    # F8: a rename onto a standard type's name
    pytest.param(Q2, 31, "String::fmt", "unresolved", "EXT_TRAIT_REACH", id="F8 use Display as String"),
    pytest.param("r3_rename_onto_std", 13, "String::fmt", "unresolved", "EXT_TRAIT_REACH", id="F8 use Display as String, no macro around it"),
    pytest.param("r3_rename_onto_std", 23, "Vec::fmt", "unresolved", "EXT_TRAIT_REACH", id="F8 use Debug as Vec"),
    pytest.param("r3_rename_onto_std", 27, "Vec::new", "external", "STD_PATH", id="F8 control: use String as Vec is still std's String"),
    pytest.param("r3_rename_onto_std", 33, "Strng::make", "unresolved", "EXT_TRAIT_SHADOW", id="F8 an alias of a std type, a repo trait gives it the name"),
    # F9: raw identifiers
    pytest.param(Q3, 31, "r#helper", "unresolved", "LOCAL", id="F9 r#helper is the local closure helper"),
    pytest.param(Q3, 32, "helper", "edge", "src/lib:raw_block::helper", id="F9 a fn declared as r#helper"),
    pytest.param("r3_raw_identifiers", 6, "r#match", "edge", "src/lib:match", id="F9 r#match is match"),
    pytest.param("r3_raw_identifiers", 8, "type::fn", "edge", "src/lib:type::fn", id="F9 r#type::r#fn"),
    pytest.param("r3_raw_identifiers", 11, "struct::new", "edge", "src/lib:struct::new", id="F9 r#struct::new, written without r#"),
    pytest.param("r3_raw_identifiers", 13, "r#try", "edge", "src/lib:type::fn", id="F9 a use ... as r#try"),
]


@pytest.fixture(scope="module")
def probes():
    return {}


@pytest.mark.parametrize("probe, line, written, kind, expected", FINDINGS)
def test_a_finding_of_the_second_review_is_settled_as_it_should_be(probe, line, written, kind, expected, probes):
    p = probes.setdefault(probe, Probe(probe))
    sites = [(r, w) for _, _, _, r, _, ln, w, _ in p.index.resolved_sites() if ln == line and w == written]
    assert len(sites) == 1, [w for _, _, _, _, _, ln, w, _ in p.index.resolved_sites() if ln == line]
    result = sites[0][0]
    assert result[0] == kind, result
    if kind == "edge":
        assert p.graph.symbols[p.index.final[result[1]]].id == expected
    else:
        want = getattr(rs, expected)
        assert result[1] == want or ("{}" in want and result[1].startswith(want.split("{}")[0]))


def test_a_reason_names_the_macro_the_attribute_or_the_impl_that_may_hide_an_impl(probes):
    """The reason of an incomplete table says what to look at: the macro, the attribute macro or derive, or the impl header."""
    said = {}
    for probe, line, written in (
        (Q1, 9, "S::f"),
        ("r3_attr_macros", 27, "U::f"),
        (Q10, 10, "Bar::f"),
    ):
        p = probes.setdefault(probe, Probe(probe))
        said[probe] = next(r[1] for _, _, _, r, _, ln, w, _ in p.index.resolved_sites() if ln == line and w == written)
    assert "`gen_f!`" in said[Q1] and "src/lib.rs" in said[Q1]
    assert "the attribute `#[" in said["r3_attr_macros"] and "may be an attribute macro" in said["r3_attr_macros"] or "`#[pm::with_helper]`" in said["r3_attr_macros"]
    assert "`impl Bar`" in said[Q10]
