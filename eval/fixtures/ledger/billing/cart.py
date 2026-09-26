"""A shopping cart priced from the catalog."""

from __future__ import annotations

from ledger.money import Money

CATALOG = {"widget": Money(1250), "gadget": Money(4999), "gizmo": Money(300)}


class Cart:
    def __init__(self):
        self.lines: list[tuple[str, int]] = []

    def add_line(self, sku: str, quantity: int = 1) -> None:
        if sku not in CATALOG:
            raise KeyError("unknown sku {}".format(sku))
        self.lines.append((sku, quantity))

    def total(self) -> Money:
        total = Money(0)
        for sku, quantity in self.lines:
            total = total + CATALOG[sku].scale(quantity)
        return total
