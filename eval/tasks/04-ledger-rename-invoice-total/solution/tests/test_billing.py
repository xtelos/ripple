from billing import FlatFee, PercentFee, checkout
from billing.cart import Cart
from billing.dunning import refund_note, reminders
from billing.quotes import cheapest_rule, quote
from billing.recurring import RecurringInvoice
from ledger import CreditNote, Invoice, Money
from ledger.accounts import Account
from ledger.store import InvoiceStore
from ledger.tax import TaxTable


def cart_of(*items):
    cart = Cart()
    for sku, quantity in items:
        cart.add_line(sku, quantity)
    return cart


def test_cart_total():
    assert cart_of(("widget", 2), ("gizmo", 1)).total() == Money(2800)


def test_checkout_charges_the_account_and_stores_the_invoice():
    account, store = Account("ada"), InvoiceStore()
    invoice = checkout(cart_of(("widget", 2), ("gizmo", 1)), account, store, "I1", "OR", PercentFee(2))
    assert invoice.amount_due() == Money(2856)
    assert account.balance() == Money(2856)
    assert store.load("I1") is invoice


def test_checkout_without_a_fee_rule_adds_tax_only():
    account = Account("ada")
    invoice = checkout(cart_of(("widget", 2), ("gizmo", 1)), account, InvoiceStore(), "I2")
    assert invoice.amount_due() == Money(3003)


def test_quote_and_cheapest_rule():
    cart, taxes = cart_of(("widget", 1)), TaxTable()
    assert quote(cart, "NY", FlatFee(100), taxes)["total"] == Money(1404)
    rules = [FlatFee(100), PercentFee(5)]
    assert cheapest_rule(cart, "NY", rules, taxes) is rules[1]


def test_recurring_invoice_bills_every_seat_and_renews():
    invoice = RecurringInvoice("R1", "pro", seats=3, region="OR")
    assert invoice.amount_due() == Money(8700)
    invoice.add_setup_fee("50")
    assert invoice.amount_due() == Money(13700)
    assert invoice.renew("R2").amount_due() == Money(8700)


def test_reminders_skip_paid_invoices():
    store = InvoiceStore()
    for number in ("I1", "I2"):
        invoice = Invoice(number)
        invoice.add_line("widget", Money(1250), 2)
        store.save(invoice)
    assert reminders(store, {"I2"}) == ["I1: $26.81 is due"]


def test_refund_note():
    note = CreditNote("C1")
    note.add_line("widget", Money(1250), 2)
    assert refund_note(note) == "C1: we owe you $26.81"
