"""The findings of the adversarial review of the Rust front end, each as the answer ripple gives now at the probe's call site.

The differential suite (test_rs_probes.py) proves nothing ripple says there is false; this pins what it says, so a
finding that is fixed by making a call unresolved cannot slide back to an edge (or to outside the repo) unnoticed, and
says which reason it gives. H1 to H8 are the review's own numbers.
"""

import pytest

pytest.importorskip("tree_sitter_rust")

from ripple import rs_indexer as rs  # noqa: E402
from test_rs_probes import Probe  # noqa: E402

P1 = "review_p1_traits_externself_macros"
P2 = "review_p2_workspace_twins"

# (probe, line, callee as written, what ripple settles it to, the reason constant or the callee's id)
FINDINGS = [
    pytest.param(P1, 18, "fmt::Display::fmt", "unresolved", "EXT_TRAIT_REACH", id="H1 a std trait path whose repo impl runs"),
    pytest.param(P1, 31, "FromStr::from_str", "unresolved", "EXT_TRAIT_REACH", id="H1 FromStr::from_str, not in the prelude"),
    pytest.param("fmt_traits", 17, "Display::fmt", "unresolved", "EXT_TRAIT_REACH", id="H1 an imported trait"),
    pytest.param("tryfrom_trait", 11, "TryFrom::try_from", "unresolved", "EXT_TRAIT_REACH", id="H1 an imported prelude trait"),
    pytest.param(P1, 61, "p1::helper", "edge", "src/lib:helper", id="H2 extern crate self as p1, in the root"),
    pytest.param(P1, 66, "p1::helper", "edge", "src/lib:helper", id="H2 the same name in a submodule"),
    pytest.param("extern_crate_forms", 8, "c::cmp::max", "external", "STD_PATH", id="H2 extern crate core as c is still the standard library"),
    pytest.param(P2, 27, "g", "unresolved", "MACRO_BLOCK_ITEMS", id="H3 a block in format! that imports"),
    pytest.param(P2, 32, "g", "unresolved", "MACRO_BLOCK_ITEMS", id="H3 a block in vec! that declares a fn"),
    pytest.param(P1, 78, "helper", "unresolved", "MACRO_MAY_MAKE", id="H4 a local a macro binds"),
    pytest.param("macro_shadows_use", 17, "f", "unresolved", "MACRO_MAY_MAKE", id="H4 a macro's local shadows a block's use"),
    pytest.param("macro_defines", 30, "plain", "unresolved", "MACRO_MAY_MAKE", id="H4 a fn a macro declares in a body"),
    pytest.param(P1, 93, "drop", "external", "PRELUDE", id="H5 a private fn is not glob imported, so std's drop"),
    pytest.param("vis_glob", 17, "private_f", "edge", "src/lib:parent::private_f", id="H5 in a block too: the module's own"),
    pytest.param("review_p4_macro_item", 17, "f", "unresolved", "MACRO_MAY_MAKE", id="H6 a macro's item beats a glob's"),
    pytest.param(P1, 42, "Name::borrow", "unresolved", "IMPLS_INCOMPLETE", id="H7 std's blanket Borrow (the probe's `bind!` macro now says so first)"),
    pytest.param("borrow_asref", 16, "<Name as Borrow<Name>>::borrow", "unresolved", "EXT_TRAIT_IMPL", id="H7 qualified"),
    pytest.param(P1, 53, "<Celsius as From<Celsius>>::from", "unresolved", "EXT_TRAIT_IMPL", id="H8 std's reflexive From"),
    pytest.param("review_p5_blanket_qualified", 16, "<S as Conv<S>>::conv", "unresolved", "BLANKET_IMPL", id="H8 a repo blanket impl of the trait"),
]


@pytest.fixture(scope="module")
def probes():
    return {}


@pytest.mark.parametrize("probe, line, written, kind, expected", FINDINGS)
def test_a_finding_of_the_review_is_settled_as_it_should_be(probe, line, written, kind, expected, probes):
    p = probes.setdefault(probe, Probe(probe))
    sites = [(r, w) for _, _, _, r, _, ln, w, _ in p.index.resolved_sites() if ln == line and w == written]
    assert len(sites) == 1, [w for _, _, _, _, _, ln, w, _ in p.index.resolved_sites() if ln == line]
    result = sites[0][0]
    assert result[0] == kind
    if kind == "edge":
        assert p.graph.symbols[p.index.final[result[1]]].id == expected
    else:
        want = getattr(rs, expected)
        assert result[1].startswith(want.split("{}")[0]) and (result[1] == want or "{}" in want)
