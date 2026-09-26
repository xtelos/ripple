"""A customer statement: every entry with the running balance."""

from __future__ import annotations

from ledger import money as m
from ledger.accounts import Account


def statement(account: Account) -> str:
    lines = [f"Statement for {account.customer}"]
    running = m.parse_money("0")
    for entry in account.entries:
        running = running + entry.amount
        lines.append(f"{entry.memo:<24}{m.format_amount(entry.amount, 12)}{m.format_amount(running, 12)}")
    lines.append(f"Balance due: {account.balance().format()}")
    return "\n".join(lines)
