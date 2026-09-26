"""Sales tax by region."""

from __future__ import annotations

from .money import Money

RATES = {"CA": 0.0725, "NY": 0.04, "OR": 0.0}


def rate_for(region: str) -> float:
    return RATES.get(region, 0.0)


class TaxTable:
    """The regional rates, with optional per-region overrides."""

    def __init__(self, overrides=None):
        self.overrides = dict(overrides or {})

    def apply(self, money: Money, region: str) -> Money:
        rate = self.overrides.get(region, rate_for(region))
        return money.scale(rate)
