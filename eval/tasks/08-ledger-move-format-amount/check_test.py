from check_helpers import identifiers

import ledger.money as money
from ledger import Invoice, Money
from ledger.accounts import Account
from ledger.formatting import format_amount
from ledger.store import InvoiceStore
from reports import AgingReport, statement


def test_moved_without_a_re_export():
    assert format_amount(Money(1250), 10) == "    $12.50"
    assert not hasattr(money, "format_amount")
    assert "format_amount" not in identifiers()["ledger/money.py"]


def test_every_caller_still_works():
    invoice = Invoice("I1")
    invoice.add_line("widget", Money(1250), 2)
    assert invoice.render().splitlines()[-1] == "  " + "total".ljust(24) + "$26.81".rjust(12)
    store = InvoiceStore()
    store.save(invoice)
    assert AgingReport(store).render().splitlines()[-1] == "TOTAL".ljust(10) + "$26.81".rjust(12)
    account = Account("ada")
    account.charge("invoice I1", Money(2681))
    assert statement(account).splitlines()[1] == "invoice I1".ljust(24) + "$26.81".rjust(12) * 2
