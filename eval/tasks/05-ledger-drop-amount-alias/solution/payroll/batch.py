"""A payroll run: pay each employee and post it to the company account."""

from __future__ import annotations

from datetime import date

from billing.fees import FeeRule
from ledger.accounts import Account, Entry
from ledger.money import Money, parse_money


class PayrollBatch:
    def __init__(self, period: date, region: str = "CA", rule: FeeRule = None):
        self.period = period
        self.region = region
        self.rule: FeeRule = rule or FeeRule()
        self.payments: list[tuple[str, Money]] = []

    def add(self, employee: str, pay: str) -> None:
        self.payments.append((employee, parse_money(pay)))

    def total(self) -> Money:
        total = Money(0)
        for _, pay in self.payments:
            total = total + pay
        return total

    def processing_fee(self) -> Money:
        return self.rule.fee(self.total())

    def apply(self, account: Account) -> Money:
        """Post every payment, then the processing fee; return the new balance."""
        balance = account.balance()
        for employee, pay in self.payments:
            balance = account.apply(Entry("pay {} {}".format(employee, self.period.isoformat()), pay))
        fee = self.processing_fee()
        if fee.cents:
            balance = account.apply(Entry("payroll fee", fee))
        return balance
