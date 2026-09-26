import inspect

from check_helpers import params

from billing import PercentFee, checkout
from billing.cart import Cart
from billing.recurring import RecurringInvoice
from ledger import Invoice, Money
from ledger.accounts import Account
from ledger.store import InvoiceStore


def test_quantity_comes_first_and_is_required():
    assert params(Invoice.add_line) == ["self", "description", "quantity", "amount"]
    defaults = [p for p in inspect.signature(Invoice.add_line).parameters.values() if p.default is not p.empty]
    assert defaults == []
    invoice = Invoice("I1")
    invoice.add_line("widget", 2, Money(1250))
    assert invoice.total() == Money(2681)


def test_cart_add_line_is_unchanged():
    assert params(Cart.add_line) == ["self", "sku", "quantity"]
    cart = Cart()
    cart.add_line("widget")
    assert cart.total() == Money(1250)


def test_every_caller_still_works():
    cart = Cart()
    cart.add_line("widget", 2)
    cart.add_line("gizmo")
    invoice = checkout(cart, Account("ada"), InvoiceStore(), "I1", "OR", PercentFee(2))
    assert invoice.lines[-1] == ("processing fee", Money(56), 1)
    assert invoice.total() == Money(2856)
    subscription = RecurringInvoice("R1", "pro", seats=3, region="OR")
    subscription.add_setup_fee("50")
    assert subscription.lines == [("Pro plan", Money(2900), 3), ("one-time setup", Money(5000), 1)]
    assert subscription.renew("R2").total() == Money(8700)
