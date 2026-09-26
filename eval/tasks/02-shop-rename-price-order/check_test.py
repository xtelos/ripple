from check_helpers import files_using

import shop.pricing as pricing
from shop import OrderService


def test_renamed_without_an_alias():
    assert hasattr(pricing, "quote_order")
    assert not hasattr(pricing, "price_order")
    assert files_using("price_order") == []


def test_the_service_still_prices_orders():
    svc = OrderService()
    order_id, totals = svc.place_order("c1", [("A", 1000, 2)], codes=["SAVE10", "FIVEOFF"])
    assert totals["net"] == 1500
    assert svc.reprice(order_id, "NY")["total"] == 2080
