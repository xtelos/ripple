"""Price a cart before the customer commits to it."""

from __future__ import annotations

from ledger.money import Money
from ledger.tax import TaxTable

from .cart import Cart
from .fees import FeeRule


def quote(cart: Cart, region: str, rule: FeeRule, taxes: TaxTable) -> dict:
    subtotal = cart.total()
    fee = rule.fee(subtotal, region)
    tax = taxes.apply(subtotal + fee, region)
    return {"subtotal": subtotal, "fee": fee, "tax": tax, "total": subtotal + fee + tax}


def cheapest_rule(cart: Cart, region: str, rules: list, taxes: TaxTable) -> FeeRule:
    """The fee rule that gives the customer the lowest total."""
    return min(rules, key=lambda rule: quote(cart, region, rule, taxes)["total"].cents)


def free_quote(cart: Cart) -> Money:
    return cart.total()
