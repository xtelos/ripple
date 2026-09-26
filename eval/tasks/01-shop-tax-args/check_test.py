from check_helpers import params

from shop import OrderService
from shop.tax import tax_for


def test_region_comes_first():
    assert params(tax_for) == ["region", "amount"]
    assert tax_for("NY", 1000) == 40


def test_prices_are_unchanged():
    svc = OrderService()
    order_id, totals = svc.place_order("c1", [("A", 1000, 2)], codes=["SAVE10"], region="NY")
    assert totals == {"gross": 2000, "net": 1800, "tax": 72, "total": 1872}
    assert svc.reprice(order_id, "CA")["tax"] == 145
