from datetime import date

import pytest

from billing import checkout
from billing.cart import Cart
from ledger.accounts import Account, CreditLimitAccount, Entry
from ledger.money import Money
from ledger.store import InvoiceStore
from ledger.tax import TaxTable
from payroll.batch import PayrollBatch
from payroll.fees import CappedFee
from reports import statement


def test_accounts_are_renamed():
    assert "post" in vars(Account) and "apply" not in vars(Account)
    assert "post" in vars(CreditLimitAccount) and "apply" not in vars(CreditLimitAccount)
    assert not hasattr(Account("x"), "apply")


def test_other_applies_keep_their_name():
    assert "apply" in vars(TaxTable)
    assert "apply" in vars(PayrollBatch)
    assert TaxTable().apply(Money(1000), "NY") == Money(40)


def test_post_and_the_credit_limit():
    account = CreditLimitAccount("bo", Money(1000))
    assert account.post(Entry("first", Money(800))) == Money(800)
    with pytest.raises(ValueError):
        account.charge("second", Money(300))
    assert account.pay("refund", Money(100)) == Money(700)


def test_every_caller_still_works():
    cart = Cart()
    cart.add_line("widget", 2)
    account = Account("ada")
    checkout(cart, account, InvoiceStore(), "I1")
    assert account.balance() == Money(2681)
    run = PayrollBatch(date(2026, 9, 30), rule=CappedFee(1, 2500))
    run.add("ann", "3500")
    company = Account("company")
    assert run.apply(company) == Money(352500)
    assert [e.memo for e in company.entries] == ["pay ann 2026-09-30", "payroll fee"]
    assert statement(account).splitlines()[-1] == "Balance due: $26.81"
