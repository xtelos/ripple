import pytest

from ledger.accounts import Account, CreditLimitAccount, Entry
from ledger.money import Money


def test_charge_and_pay_move_the_balance():
    account = Account("ada")
    account.charge("invoice", Money(1000))
    assert account.pay("payment", Money(400)) == Money(600)


def test_apply_returns_the_new_balance():
    account = Account("ada")
    assert account.apply(Entry("opening", Money(250))) == Money(250)


def test_credit_limit_refuses_an_entry_over_the_limit():
    account = CreditLimitAccount("bo", Money(1000))
    account.charge("first", Money(800))
    with pytest.raises(ValueError):
        account.charge("second", Money(300))
    assert account.balance() == Money(800)
