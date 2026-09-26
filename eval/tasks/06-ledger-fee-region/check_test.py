from datetime import date

from check_helpers import params

from billing import FeeRule, FlatFee, PercentFee, checkout
from billing.cart import Cart
from billing.quotes import quote
from ledger.accounts import Account
from ledger.money import Money
from ledger.store import InvoiceStore
from ledger.tax import TaxTable
from payroll.batch import PayrollBatch
from payroll.fees import CappedFee


def test_every_rule_takes_the_region():
    for rule in (FeeRule, FlatFee, PercentFee, CappedFee):
        assert params(rule.fee) == ["self", "subtotal", "region"], rule.__name__


def test_percent_rules_are_waived_in_de():
    assert PercentFee(2).fee(Money(1000), "DE") == Money(0)
    assert PercentFee(2).fee(Money(1000), "CA") == Money(20)
    assert CappedFee(1, 2500).fee(Money(350000), "DE") == Money(0)
    assert CappedFee(1, 2500).fee(Money(350000), "CA") == Money(2500)
    assert FlatFee(100).fee(Money(1), "DE") == Money(100)
    assert FeeRule().fee(Money(1000), "CA") == Money(0)


def cart():
    c = Cart()
    c.add_line("widget", 2)
    c.add_line("gizmo")
    return c


def test_checkout_passes_its_region():
    assert checkout(cart(), Account("a"), InvoiceStore(), "I1", "DE", PercentFee(2)).total() == Money(2800)
    assert checkout(cart(), Account("a"), InvoiceStore(), "I2", "NY", PercentFee(2)).total() == Money(2970)


def test_quote_passes_its_region():
    assert quote(cart(), "DE", PercentFee(5), TaxTable())["fee"] == Money(0)
    assert quote(cart(), "NY", PercentFee(5), TaxTable())["fee"] == Money(140)


def test_payroll_passes_its_region():
    for region, fee in (("DE", 0), ("CA", 2500)):
        run = PayrollBatch(date(2026, 9, 30), region=region, rule=CappedFee(1, 2500))
        run.add("ann", "3500")
        assert run.processing_fee() == Money(fee)
        account = Account("company")
        assert run.apply(account) == Money(350000 + fee)
