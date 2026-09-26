from datetime import date

from ledger.accounts import Account
from ledger.money import Money
from payroll.batch import PayrollBatch
from payroll.fees import CappedFee


def batch(rule=None):
    run = PayrollBatch(date(2026, 9, 30), rule=rule)
    run.add("ann", "2,000.00")
    run.add("bo", "1500")
    return run


def test_total():
    assert batch().total() == Money(350000)


def test_capped_fee():
    assert batch(CappedFee(1, 2500)).processing_fee() == Money(2500)
    assert batch(CappedFee(1, 9999)).processing_fee() == Money(3500)


def test_apply_posts_each_payment_and_the_fee():
    account = Account("company")
    assert batch(CappedFee(1, 2500)).apply(account) == Money(352500)
    assert [e.memo for e in account.entries] == ["pay ann 2026-09-30", "pay bo 2026-09-30", "payroll fee"]


def test_no_rule_means_no_fee():
    account = Account("company")
    batch().apply(account)
    assert len(account.entries) == 2
