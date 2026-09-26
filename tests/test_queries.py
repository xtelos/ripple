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
    assert result["more_beyond_depth"] is True
    assert result["truncated"] is False


def test_impact_says_when_it_lists_fewer_callers_than_it_found(make_repo):
    calls = "".join(f"def caller_{i:03}():\n    hub()\n\n" for i in range(150))
    tests = "".join(f"def test_{i}():\n    hub()\n\n" for i in range(5))
    repo = make_repo({"m.py": "def hub():\n    pass\n\n" + calls, "tests/test_m.py": "from m import hub\n\n" + tests})
    g = build_graph(repo)

    result = impact(g, "hub")
    assert result["affected"] == 155
    assert result["caller_count"] == 150
    assert sum(len(v) for v in result["by_file"].values()) == 100
    assert (result["truncated"], result["omitted_callers"], result["omitted_tests"]) == (True, 50, 0)

    result = impact(g, "hub", limit=3)
    assert (result["omitted_callers"], result["omitted_tests"]) == (147, 2)
    assert len(result["tests"]) == 3

    result = impact(g, "hub", limit=500)
    assert (result["truncated"], result["omitted_callers"]) == (False, 0)


def test_impact_lists_tests_as_pytest_node_ids(make_repo):
    repo = make_repo(
        {
            "pkg/__init__.py": "",
            "pkg/core.py": "def work():\n    pass\n",
            "tests/helpers.py": "from pkg.core import work\n\ndef setup_work():\n    work()\n",
            "tests/test_core.py": """\
                from pkg.core import work
                from tests.helpers import setup_work

                def test_plain():
                    work()

                def test_via_helper():
                    setup_work()

                class TestGroup:
                    def test_method(self):
                        work()

                    def helper(self):
                        work()

                    class TestNested:
                        def test_inner(self):
                            work()

                def test_with_closure():
                    def inner():
                        work()
                    inner()
                """,
        }
    )
    result = impact(build_graph(repo), "work")
    assert [t["node_id"] for t in result["tests"]] == [
        "tests/test_core.py::TestGroup::TestNested::test_inner",
        "tests/test_core.py::TestGroup::test_method",
        "tests/test_core.py::test_plain",
        "tests/test_core.py::test_via_helper",
        "tests/test_core.py::test_with_closure",
    ]
    # helpers in test files are code that may need updating, not tests to run
    assert {e["symbol"] for e in result["by_file"]["tests/helpers.py"]} == {"tests.helpers.setup_work"}
    assert {e["symbol"] for e in result["by_file"]["tests/test_core.py"]} == {
        "tests.test_core.TestGroup.helper",
        "tests.test_core.test_with_closure.inner",
    }


def test_impact_tests_run_through_check(shop_repo):
    import sys

    from ripple.checker import run_check

    node_ids = [t["node_id"] for t in impact(build_graph(shop_repo), "OrderService.reprice")["tests"]]
    assert node_ids == ["tests/test_service.py::test_reprice_drops_discounts"]
    result = run_check(shop_repo, command=[sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"], tests=node_ids)
    assert result["passed"] is True
    assert result["summary"].startswith("1 passed")


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


def test_impact_lists_calls_behind_an_unresolvable_base_as_possible_missed(make_repo):
    repo = make_repo(
        {
            "m.py": """\
                from factory import make_base

                class Store:
                    def save(self):
                        pass

                class Cached(make_base()):
                    def flush(self):
                        self.save()
                """,
            "factory.py": "def make_base():\n    return object\n",
        }
    )
    g = build_graph(repo)
    result = impact(g, "Store.save")
    assert result["affected"] == 0
    assert [m["caller"] for m in result["possible_missed_callers"]] == ["m.Cached.flush"]
    assert [u["call"] for u in callees(g, "Cached.flush")["unresolved"]] == ["self.save"]
