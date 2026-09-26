from datetime import date

from billing import PercentFee, checkout
from billing.cart import Cart
from billing.dunning import refund_note, reminders
from billing.recurring import RecurringInvoice
from ledger import CreditNote, Invoice, Money
from ledger.accounts import Account
from ledger.store import InvoiceStore
from payroll.batch import PayrollBatch
from reports import AgingReport


def widgets(cls=Invoice, number="I1"):
    invoice = cls(number)
    invoice.add_line("widget", Money(1250), 2)
    return invoice


def test_invoices_and_credit_notes_are_renamed():
    assert "amount_due" in vars(Invoice) and "total" not in vars(Invoice)
    assert "amount_due" in vars(CreditNote) and "total" not in vars(CreditNote)
    assert not hasattr(Invoice("x"), "total")


def test_other_totals_keep_their_name():
    assert "total" in vars(Cart)
    assert "total" in vars(PayrollBatch)
    assert "total" in vars(AgingReport)


def test_amounts():
    assert widgets().amount_due() == Money(2681)
    assert widgets(CreditNote, "C1").amount_due() == Money(-2681)
    assert widgets().render().splitlines()[-1].endswith("$26.81")
    assert RecurringInvoice("R1", "pro", seats=3, region="OR").renew("R2").amount_due() == Money(8700)


def test_every_caller_still_works():
    cart = Cart()
    cart.add_line("widget", 2)
    cart.add_line("gizmo")
    account, store = Account("ada"), InvoiceStore()
    checkout(cart, account, store, "I1", "OR", PercentFee(2))
    assert account.balance() == Money(2856)
    store.save(widgets(number="I2"))
    assert reminders(store, {"I1"}) == ["I2: $26.81 is due"]
    assert refund_note(widgets(CreditNote, "C1")) == "C1: we owe you $26.81"
    report = AgingReport(store)
    assert report.rows() == [("I1", Money(2856)), ("I2", Money(2681))]
    assert report.total() == Money(5537)
    assert cart.total() == Money(2800)
    run = PayrollBatch(date(2026, 9, 30))
    run.add("ann", "10")
    assert run.total() == Money(1000)
