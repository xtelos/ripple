"""Invoices and credit notes."""

from __future__ import annotations

from .money import Money
from .money import format_amount as fmt
from .tax import TaxTable

TAXES = TaxTable()


class Invoice:
    def __init__(self, number: str, region: str = "CA"):
        self.number = number
        self.region = region
        self.lines: list[tuple[str, Money, int]] = []

    def add_line(self, description: str, amount: Money, quantity: int = 1) -> None:
        self.lines.append((description, amount, quantity))

    def subtotal(self) -> Money:
        total = Money(0)
        for _, amount, quantity in self.lines:
            total = total + amount.scale(quantity)
        return total

    def tax(self) -> Money:
        return TAXES.apply(self.subtotal(), self.region)

    def total(self) -> Money:
        return self.subtotal() + self.tax()

    def render(self) -> str:
        rows = [f"Invoice {self.number}"]
        for description, amount, quantity in self.lines:
            rows.append(f"  {description:<20} x{quantity:<3}{fmt(amount.scale(quantity), 12)}")
        rows.append(f"  {'total':<24}{fmt(self.total(), 12)}")
        return "\n".join(rows)


class CreditNote(Invoice):
    """A refund: the same lines as an invoice, owed the other way."""

    def total(self) -> Money:
        return -super().total()
