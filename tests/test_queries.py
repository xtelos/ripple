import pytest

from ripple.indexer import build_graph
from ripple.queries import callees, callers, find_symbol, impact, path


@pytest.fixture(scope="module")
def shop():
    from conftest import FIXTURES

    return build_graph(FIXTURES / "shop")


def test_find_symbol_by_full_path_suffix_and_bare_name(shop):
    assert find_symbol(shop, "shop.pricing.price_order")["symbol"] == "shop.pricing.price_order"
    assert find_symbol(shop, "OrderService.place_order")["symbol"] == "shop.service.OrderService.place_order"
    assert find_symbol(shop, "price_order")["symbol"] == "shop.pricing.price_order"


def test_find_symbol_ambiguous_returns_candidates(shop):
    found = find_symbol(shop, "amount_off")
    assert "symbol" not in found
    assert set(found["candidates"]) == {
        "shop.discounts.Discount.amount_off",
        "shop.discounts.PercentOff.amount_off",
        "shop.discounts.FixedOff.amount_off",
    }


def test_find_symbol_unknown_suggests_close_names(shop):
    found = find_symbol(shop, "price_ordr")
    assert "symbol" not in found
    assert "shop.pricing.price_order" in found["suggestions"]


def test_callers_lists_call_sites(shop):
    result = callers(shop, "price_order")
    by_caller = {c["symbol"]: c for c in result["callers"]}
    assert set(by_caller) == {
        "shop.service.OrderService.place_order",
        "shop.service.OrderService.reprice",
        "tests.test_pricing.test_percent_discount_and_tax",
    }
    assert by_caller["shop.service.OrderService.place_order"]["call_sites"] == ["shop/service.py:22"]


def test_callers_of_a_class_include_constructor_calls(shop):
    names = {c["symbol"] for c in callers(shop, "shop.discounts.PercentOff")["callers"]}
    assert names == {"shop.service.OrderService.__init__", "tests.test_pricing.test_percent_discount_and_tax"}


def test_callees_report_unresolved_calls_separately(shop):
    result = callees(shop, "shop.pricing.price_order")
    assert {c["symbol"] for c in result["callees"]} == {
        "shop.models.Order.gross_total",
        "shop.discounts.best_discount",
        "shop.tax.tax_for",
    }
    assert [u["call"] for u in result["unresolved"]] == ["best.apply"]


def test_impact_groups_by_file_and_separates_tests(shop):
    result = impact(shop, "tax_for", depth=5)
    assert set(result["by_file"]) == {"shop/pricing.py", "shop/service.py"}
    assert {t["symbol"] for t in result["tests"]} == {
        "tests.test_pricing.test_percent_discount_and_tax",
        "tests.test_service.test_place_order_applies_best_discount",
        "tests.test_service.test_reprice_drops_discounts",
    }
    assert result["affected"] == 6
    assert result["truncated"] is False


def test_impact_respects_depth(shop):
    result = impact(shop, "tax_for", depth=1)
    assert result["affected"] == 1
    assert result["truncated"] is True


def test_impact_flags_unresolved_calls_with_the_same_name(shop):
    result = impact(shop, "LineItem.subtotal", depth=3)
    assert result["affected"] == 0
    assert [u["call"] for u in result["possible_missed_callers"]] == ["item.subtotal"]


def test_path_returns_shortest_chain(shop):
    result = path(shop, "test_percent_discount_and_tax", "tax_for")
    assert [hop["symbol"] for hop in result["path"]] == [
        "tests.test_pricing.test_percent_discount_and_tax",
        "shop.pricing.price_order",
        "shop.tax.tax_for",
    ]


def test_path_reports_when_no_chain_exists(shop):
    result = path(shop, "tax_for", "price_order")
    assert result["path"] is None


def test_queries_pass_ambiguity_through(shop):
    assert "candidates" in impact(shop, "amount_off")
