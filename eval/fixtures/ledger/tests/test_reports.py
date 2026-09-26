from ledger import Invoice, Money
from ledger.accounts import Account
from ledger.store import InvoiceStore
from reports import AgingReport, statement


def two_invoices():
    store = InvoiceStore()
    first = Invoice("I1")
    first.add_line("widget", Money(1250), 2)
    second = Invoice("I2", "NY")
    second.add_line("gadget", Money(4999))
    store.save(first)
    store.save(second)
    return store


def test_aging_rows_and_total():
    report = AgingReport(two_invoices())
    assert report.rows() == [("I1", Money(2681)), ("I2", Money(5199))]
    assert report.total() == Money(7880)
    assert report.render().splitlines()[-1] == "TOTAL" + " " * 5 + "$78.80".rjust(12)


def test_aging_skips_paid():
    assert AgingReport(two_invoices(), paid={"I1"}).total() == Money(5199)


def test_statement_shows_the_running_balance():
    account = Account("ada")
    account.charge("invoice I1", Money(2681))
    account.pay("payment", Money(1000))
    lines = statement(account).splitlines()
    assert lines[0] == "Statement for ada"
    assert lines[2].endswith("$16.81")
    assert lines[-1] == "Balance due: $16.81"
