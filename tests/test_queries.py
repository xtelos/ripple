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


MOMUS_SHAPE = {
    "conftest.py": "import os, sys\nsys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))\n",
    "src/app/__init__.py": "",
    "src/app/core.py": "def target():\n    return 1\n",
    "tests/unit/test_core.py": """\
        import unittest
        import pytest
        from app.core import target

        def helper():
            return target()

        def test_plain():
            assert helper() == 1

        @pytest.mark.parametrize("x", [1, 2])
        def test_param(x):
            assert target() == 1

        class TestOuter:
            def test_m(self):
                assert target() == 1
            class TestInner:
                def test_n(self):
                    assert target() == 1

        class CoreTests(unittest.TestCase):
            def test_unittest_style(self):
                self.assertEqual(target(), 1)

        class TestBase:
            def test_shared(self):
                assert target() == 1

        class TestChild(TestBase):
            pass
        """,
}


def test_impact_lists_unittest_and_inherited_tests_the_way_pytest_collects_them(make_repo):
    import subprocess
    import sys

    from ripple.checker import run_check

    repo = make_repo(MOMUS_SHAPE)
    result = impact(build_graph(repo), "app.core.target")
    node_ids = [t["node_id"] for t in result["tests"]]
    assert sorted(node_ids) == [
        "tests/unit/test_core.py::CoreTests::test_unittest_style",
        "tests/unit/test_core.py::TestBase::test_shared",
        "tests/unit/test_core.py::TestChild::test_shared",
        "tests/unit/test_core.py::TestOuter::TestInner::test_n",
        "tests/unit/test_core.py::TestOuter::test_m",
        "tests/unit/test_core.py::test_param",
        "tests/unit/test_core.py::test_plain",
    ]
    assert {e["symbol"] for e in result["by_file"]["tests/unit/test_core.py"]} == {"tests.unit.test_core.helper"}
    pytest_cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"]
    collected = subprocess.run(pytest_cmd + ["--collect-only", "-q"], cwd=repo, capture_output=True, text=True)
    assert "tests/unit/test_core.py: 8" in collected.stdout
    checked = run_check(repo, command=pytest_cmd, tests=node_ids)
    assert checked["passed"] is True
    assert checked["summary"].startswith("8 passed")


def test_impact_follows_test_classes_through_their_bases(make_repo):
    repo = make_repo(
        {
            "core.py": "def work():\n    pass\n",
            "tests/mixins.py": """\
                from core import work

                class SharedChecks:
                    def test_from_mixin(self):
                        work()
                """,
            "tests/test_bases.py": """\
                from unittest import TestCase
                from core import work
                from tests.mixins import SharedChecks

                class Base(TestCase):
                    def test_base(self):
                        work()

                class Derived(Base):
                    pass

                class Overrides(Base):
                    def test_base(self):
                        pass

                class TestWithMixin(SharedChecks):
                    pass

                class MixedIntoCase(SharedChecks, TestCase):
                    pass

                class TestHasInit:
                    def __init__(self):
                        pass

                    def test_never_collected(self):
                        work()

                class TestInheritsInit(TestHasInit):
                    pass

                class Helper(TestCase):
                    def check_it(self):
                        work()
                """,
        }
    )
    result = impact(build_graph(repo), "core.work")
    assert sorted(t["node_id"] for t in result["tests"]) == [
        "tests/test_bases.py::Base::test_base",
        "tests/test_bases.py::Derived::test_base",
        "tests/test_bases.py::MixedIntoCase::test_from_mixin",
        "tests/test_bases.py::TestWithMixin::test_from_mixin",
    ]
    # an inherited method is one caller, listed once per class pytest collects it from
    assert {t["symbol"] for t in result["tests"]} == {
        "tests.test_bases.Base.test_base",
        "tests.mixins.SharedChecks.test_from_mixin",
    }
    assert {e["symbol"] for e in result["by_file"]["tests/test_bases.py"]} == {
        "tests.test_bases.TestHasInit.test_never_collected",
        "tests.test_bases.Helper.check_it",
    }
    assert "tests/mixins.py" not in result["by_file"]


def test_impact_survives_two_files_with_the_same_module_name(make_repo):
    repo = make_repo(
        {
            "core.py": "def target():\n    return 1\n",
            "tests/test_dup.py": "from core import target\n\ndef test_one():\n    target()\n",
            "src/tests/test_dup.py": "from core import target\n\ndef test_two():\n    target()\n",
        }
    )
    graph = build_graph(repo)
    result = impact(graph, "core.target")
    assert sorted(t["node_id"] for t in result["tests"]) == [
        "src/tests/test_dup.py::test_two",
        "tests/test_dup.py::test_one",
    ]
    assert graph.module_collisions() == {"tests.test_dup": ["src/tests/test_dup.py", "tests/test_dup.py"]}
