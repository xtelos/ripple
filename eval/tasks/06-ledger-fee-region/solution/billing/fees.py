"""Processing fees charged on top of a subtotal."""

from __future__ import annotations

from ledger.money import Money


class FeeRule:
    """No fee. Subclasses charge one."""

    def fee(self, subtotal: Money, region: str) -> Money:
        return Money(0)


class FlatFee(FeeRule):
    def __init__(self, cents: int):
        self.cents = cents

    def fee(self, subtotal: Money, region: str) -> Money:
        return Money(self.cents) if subtotal.cents else Money(0)


class PercentFee(FeeRule):
    def __init__(self, percent: float):
        self.percent = percent

    def fee(self, subtotal: Money, region: str) -> Money:
        if region == "DE":
            return Money(0)
        return subtotal.scale(self.percent / 100)
