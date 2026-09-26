from shop.discounts import PercentOff
from shop.models import Order
from shop.pricing import price_order


def test_percent_discount_and_tax():
    order = Order("c1")
    order.add_item("A", 1000)
    totals = price_order(order, [PercentOff("P10", 10)], "NY")
    assert totals["net"] == 900
    assert totals["tax"] == 36
