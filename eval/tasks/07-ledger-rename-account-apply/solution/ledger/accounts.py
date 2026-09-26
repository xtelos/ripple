"""Customer accounts: a running balance of posted entries."""

from __future__ import annotations

from dataclasses import dataclass

from .money import Money


@dataclass
class Entry:
    memo: str
    amount: Money  # positive is a charge, negative a payment


class Account:
    def __init__(self, customer: str):
        self.customer = customer
        self.entries: list[Entry] = []

    def post(self, entry: Entry) -> Money:
        """Post an entry and return the new balance."""
        self.entries.append(entry)
        return self.balance()

    def balance(self) -> Money:
        total = Money(0)
        for entry in self.entries:
            total = total + entry.amount
        return total

    def charge(self, memo: str, money: Money) -> Money:
        return self.post(Entry(memo, money))

    def pay(self, memo: str, money: Money) -> Money:
        return self.post(Entry(memo, -money))


class CreditLimitAccount(Account):
    """Refuses any entry that would push the balance over the limit."""

    def __init__(self, customer: str, limit: Money):
        super().__init__(customer)
        self.limit = limit

    def post(self, entry: Entry) -> Money:
        if (self.balance() + entry.amount).cents > self.limit.cents:
            raise ValueError("{} would go over the credit limit".format(self.customer))
        return super().post(entry)
