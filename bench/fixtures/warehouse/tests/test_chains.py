from warehouse.chains import chained, from_classmethod, pick_one, pick_three, pick_two, place_via_registry


def test_pickers():
    assert pick_one("a") == "picked a"
    assert pick_two("b") == "picked b"


def test_scanner():
    assert pick_three("c") == "scanned c"


def test_dispatchers():
    assert place_via_registry(1) == "placed 1"
    assert chained(2) == "placed 2"
    assert from_classmethod(3) == "placed 3"
