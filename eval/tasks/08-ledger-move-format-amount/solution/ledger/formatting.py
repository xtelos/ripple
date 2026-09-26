"""Formatting money for reports."""

from __future__ import annotations


def format_amount(money, width: int = 0) -> str:
    """Right-aligned for report columns."""
    return money.format().rjust(width)
