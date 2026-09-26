"""Which invoices are still open, and how much is owed in all."""

from __future__ import annotations

from ledger.money import Money, format_amount
from ledger.store import InvoiceStore


class AgingReport:
    def __init__(self, store: InvoiceStore, paid=()):
        self.store = store
        self.paid = set(paid)

    def rows(self) -> list[tuple[str, Money]]:
        rows = []
        for number in self.store.numbers():
            if number not in self.paid:
                invoice = self.store.load(number)
                rows.append((number, invoice.total()))
        return rows

    def total(self) -> Money:
        total = Money(0)
        for _, owed in self.rows():
            total = total + owed
        return total

    def render(self) -> str:
        lines = [f"{number:<10}{format_amount(owed, 12)}" for number, owed in self.rows()]
        lines.append(f"{'TOTAL':<10}{format_amount(self.total(), 12)}")
        return "\n".join(lines)
