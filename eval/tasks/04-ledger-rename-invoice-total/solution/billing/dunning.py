"""Reminders for invoices nobody has paid yet."""

from __future__ import annotations

from ledger.store import InvoiceStore


def reminders(store: InvoiceStore, paid: set) -> list[str]:
    notes = []
    for number in store.numbers():
        if number in paid:
            continue
        invoice = store.load(number)
        notes.append(f"{number}: {invoice.amount_due().format()} is due")
    return notes


def refund_note(invoice) -> str:
    """invoice is a CreditNote, so its total is negative."""
    return f"{invoice.number}: we owe you {(-invoice.amount_due()).format()}"
