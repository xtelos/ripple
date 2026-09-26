"""In-memory invoice storage. A real app would put a database here."""

from __future__ import annotations

from .invoices import Invoice


class InvoiceStore:
    def __init__(self):
        self._invoices: dict[str, Invoice] = {}

    def save(self, invoice: Invoice) -> str:
        self._invoices[invoice.number] = invoice
        return invoice.number

    def load(self, number: str) -> Invoice:
        return self._invoices[number]

    def numbers(self) -> list[str]:
        return sorted(self._invoices)
