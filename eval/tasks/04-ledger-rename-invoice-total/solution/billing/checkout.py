"""Turn a cart into an invoice and charge the customer's account for it."""

from __future__ import annotations

from ledger import Invoice
from ledger.accounts import Account, Entry
from ledger.store import InvoiceStore

from .cart import CATALOG, Cart
from .fees import FeeRule


def checkout(
    cart: Cart,
    account: Account,
    store: InvoiceStore,
    number: str,
    region: str = "CA",
    rule: FeeRule = None,
) -> Invoice:
    invoice = Invoice(number, region)
    for sku, quantity in cart.lines:
        invoice.add_line(sku, CATALOG[sku], quantity)
    if rule is not None:
        fee = rule.fee(invoice.subtotal())
        if fee.cents:
            invoice.add_line("processing fee", fee)
    account.apply(Entry(f"invoice {number}", invoice.amount_due()))
    store.save(invoice)
    return invoice
