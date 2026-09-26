"""Subscriptions: the same plan billed every period."""

from __future__ import annotations

from ledger.invoices import Invoice
from ledger.money import parse_amount

PLANS = {"basic": ("Basic plan", "9.99"), "pro": ("Pro plan", "29.00")}


class RecurringInvoice(Invoice):
    def __init__(self, number: str, plan: str, seats: int = 1, region: str = "CA"):
        super().__init__(number, region)
        self.plan = plan
        self.seats = seats
        label, price = PLANS[plan]
        self.add_line(label, seats, parse_amount(price))

    def add_setup_fee(self, price: str) -> None:
        self.add_line("one-time setup", 1, parse_amount(price))

    def renew(self, number: str) -> RecurringInvoice:
        return RecurringInvoice(number, self.plan, self.seats, self.region)
