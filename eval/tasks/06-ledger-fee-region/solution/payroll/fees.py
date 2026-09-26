"""Payroll's fee: a percentage of the run, capped."""

from __future__ import annotations

from billing.fees import PercentFee
from ledger.money import Money


class CappedFee(PercentFee):
    def __init__(self, percent: float, cap_cents: int):
        super().__init__(percent)
        self.cap = Money(cap_cents)

    def fee(self, subtotal: Money, region: str) -> Money:
        charged = super().fee(subtotal, region)
        return charged if charged.cents <= self.cap.cents else self.cap
