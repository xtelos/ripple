"""The entry point the rest of the app talks to."""

import shop.discounts as disc
from shop.models import Order
from shop.pricing import price_order as quote
from shop.repository import OrderRepository


class OrderService:
    def __init__(self, repo: OrderRepository = None):
        self.repo = repo or OrderRepository()
        self.codes = {
            "SAVE10": disc.PercentOff("SAVE10", 10),
            "FIVEOFF": disc.FixedOff("FIVEOFF", 500),
        }

    def place_order(self, customer_id, items, codes=(), region="CA"):
        order = Order(customer_id)
        for sku, price, qty in items:
            order.add_item(sku, price, qty)
        discounts = self._lookup_codes(codes)
        totals = quote(order, discounts, region)
        order_id = self.repo.save(order)
        return order_id, totals

    def reprice(self, order_id, region):
        order = self.repo.get(order_id)
        return quote(order, [], region)

    def _lookup_codes(self, codes):
        return [self.codes[c] for c in codes if c in self.codes]
