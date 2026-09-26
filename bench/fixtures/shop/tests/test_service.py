from shop import OrderService


def test_place_order_applies_best_discount():
    svc = OrderService()
    order_id, totals = svc.place_order("c1", [("A", 1000, 2)], codes=["SAVE10", "FIVEOFF"])
    assert order_id == 1
    assert totals["net"] == 1500


def test_reprice_drops_discounts():
    svc = OrderService()
    order_id, _ = svc.place_order("c1", [("A", 1000, 1)], codes=["SAVE10"])
    assert svc.reprice(order_id, "NY")["net"] == 1000
