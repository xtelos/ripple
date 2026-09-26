"""Turns an order plus discount codes into a price breakdown."""

from . import tax
from .discounts import best_discount
from .models import Order


def price_order(order: Order, discounts: list, region: str) -> dict:
    gross = order.gross_total()
    best = best_discount(discounts, gross)
    net = best.apply(gross) if best else gross
    owed = tax.tax_for(net, region)
    return {"gross": gross, "net": net, "tax": owed, "total": net + owed}
