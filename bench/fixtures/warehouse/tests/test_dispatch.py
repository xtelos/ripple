from warehouse.dispatch import priced, run_audit, run_discount, run_registry, run_steps, taxed


def test_partials():
    assert priced(100) == 110
    assert taxed(100) == 120


def test_tables():
    assert run_discount(100) == 95
    assert run_audit(7) == 7
    assert run_steps(1) == 4
    assert run_registry(1) == 2
