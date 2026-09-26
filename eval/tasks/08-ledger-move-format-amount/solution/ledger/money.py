"""Money as integer cents, so sums never drift."""

from __future__ import annotations


class Money:
    def __init__(self, cents: int):
        self.cents = int(cents)

    def __add__(self, other: Money) -> Money:
        return Money(self.cents + other.cents)

    def __neg__(self) -> Money:
        return Money(-self.cents)

    def __eq__(self, other) -> bool:
        return isinstance(other, Money) and self.cents == other.cents

    def __repr__(self) -> str:
        return f"Money({self.cents})"

    def scale(self, factor: float) -> Money:
        return Money(round(self.cents * factor))

    def format(self) -> str:
        sign = "-" if self.cents < 0 else ""
        dollars, cents = divmod(abs(self.cents), 100)
        return f"{sign}${dollars:,}.{cents:02d}"


def parse_amount(text: str) -> Money:
    """'12.50', '$1,200' or '-3.5' as Money."""
    cleaned = text.strip().replace("$", "").replace(",", "")
    negative = cleaned.startswith("-")
    dollars, _, cents = cleaned.lstrip("-").partition(".")
    value = int(dollars or 0) * 100 + int((cents + "00")[:2])
    return Money(-value if negative else value)
