from warehouse.guards import Dock, run_ship, run_unload, ship


def test_ship():
    assert run_ship("a") == "shipped a"


def test_ship_direct():
    assert ship("b") == "shipped b"


def test_unload():
    assert run_unload(Dock(), "c") == "unloaded c"
