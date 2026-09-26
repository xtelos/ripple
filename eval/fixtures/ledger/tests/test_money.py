from ledger import amount
from ledger.money import Money, format_amount, parse_amount


def test_parse_amount_handles_symbols_commas_and_signs():
    assert parse_amount("12.50") == Money(1250)
    assert parse_amount("$1,200") == Money(120000)
    assert parse_amount("-3.5") == Money(-350)


def test_the_package_alias_parses_too():
    assert amount("7") == Money(700)


def test_format():
    assert Money(123456).format() == "$1,234.56"
    assert Money(-5).format() == "-$0.05"


def test_format_amount_right_aligns():
    assert format_amount(Money(1250), 10) == "    $12.50"
