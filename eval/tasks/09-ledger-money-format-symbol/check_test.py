import pytest
from check_helpers import params

from billing.cart import Cart
from billing.dunning import refund_note, reminders
from ledger import CreditNote, Invoice, Money
from ledger.accounts import Account, CreditLimitAccount
from ledger.money import format_amount
from ledger.store import InvoiceStore
from reports import AgingReport, statement


def test_symbol_is_required():
    assert params(Money.format) == ["self", "symbol"]
    assert Money(1250).format("€") == "€12.50"
    assert Money(-5).format("€") == "-€0.05"
    assert Money(123456).format("$") == "$1,234.56"


def widgets(cls=Invoice, number="I1"):
    invoice = cls(number)
    invoice.add_line("widget", Money(1250), 2)
    return invoice


def test_everything_prints_what_it_printed_before():
    assert format_amount(Money(1250), 10) == "    $12.50"
    assert widgets().render().splitlines()[-1].endswith("$26.81")
    store = InvoiceStore()
    store.save(widgets())
    assert reminders(store, set()) == ["I1: $26.81 is due"]
    assert refund_note(widgets(CreditNote, "C1")) == "C1: we owe you $26.81"
    assert AgingReport(store).render().splitlines()[-1].endswith("$26.81")
    account = Account("ada")
    account.charge("invoice I1", Money(2681))
    assert statement(account).splitlines()[-1] == "Balance due: $26.81"


def test_str_format_calls_still_work():
    with pytest.raises(KeyError, match="unknown sku nope"):
        Cart().add_line("nope")
    with pytest.raises(ValueError, match="bo would go over the credit limit"):
        CreditLimitAccount("bo", Money(0)).charge("x", Money(1))
