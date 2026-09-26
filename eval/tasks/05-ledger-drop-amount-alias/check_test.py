import ast
from datetime import date
from pathlib import Path

from check_helpers import REPO, files_using

import ledger
import ledger.money as money
from billing.recurring import RecurringInvoice
from ledger.accounts import Account
from ledger.money import Money
from payroll.batch import PayrollBatch
from reports import statement


def test_renamed_and_alias_removed():
    assert money.parse_money("$1,200") == Money(120000)
    assert not hasattr(money, "parse_amount")
    assert not hasattr(ledger, "amount")
    assert "amount" not in getattr(ledger, "__all__", [])
    assert files_using("parse_amount") == []


def test_nothing_imports_the_old_alias():
    for path in REPO.rglob("*.py"):
        if "_eval_check" in path.parts:
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom) and node.module == "ledger":
                assert "amount" not in [a.name for a in node.names], path
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                assert (node.value.id, node.attr) != ("ledger", "amount"), path


def test_every_caller_still_works():
    run = PayrollBatch(date(2026, 9, 30))
    run.add("ann", "2,000.00")
    run.add("bo", "1500")
    assert run.total() == Money(350000)
    invoice = RecurringInvoice("R1", "pro", seats=3, region="OR")
    invoice.add_setup_fee("50")
    assert invoice.total() == Money(13700)
    account = Account("ada")
    account.charge("invoice", Money(2681))
    assert statement(account).splitlines()[-1] == "Balance due: $26.81"
