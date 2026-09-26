import json
import subprocess
import sys

from conftest import FIXTURES

from ripple.cli import main

SHOP = str(FIXTURES / "shop")


def test_index_prints_stats(capsys):
    assert main(["index", SHOP]) == 0
    out = capsys.readouterr().out
    assert "shop" in out
    assert "unresolved calls" in out


def test_index_reports_skipped_directories(make_repo, capsys):
    repo = make_repo({"app.py": "def f():\n    pass\n", "build/lib/app.py": "def f():\n    pass\n"})
    assert main(["index", str(repo)]) == 0
    assert "skipped directories (not indexed): build" in capsys.readouterr().out


def test_impact_text_output(capsys):
    assert main(["impact", "tax_for", "--repo", SHOP]) == 0
    out = capsys.readouterr().out
    assert "shop.tax.tax_for" in out
    assert "shop/service.py" in out
    assert "tests/test_service.py::test_reprice_drops_discounts" in out


def test_impact_text_output_says_how_many_callers_it_left_out(capsys):
    assert main(["impact", "tax_for", "--repo", SHOP, "--limit", "1"]) == 0
    out = capsys.readouterr().out
    assert "... and 2 more callers not listed; raise --limit to see them" in out
    assert "... and 2 more tests not listed; raise --limit to see them" in out


def test_check_runs_only_the_named_tests(capsys, monkeypatch):
    monkeypatch.setenv("RIPPLE_TEST_COMMAND", f"{sys.executable} -m pytest -q -p no:cacheprovider")
    assert main(["check", "tests/test_pricing.py::test_percent_discount_and_tax", "--repo", SHOP]) == 0
    assert '"summary": "1 passed' in capsys.readouterr().out


def test_check_refuses_options(capsys):
    assert main(["check", "--repo", SHOP, "--", "--basetemp=x"]) == 2
    assert "not a test path or node id" in capsys.readouterr().out


def test_impact_json_output(capsys):
    assert main(["impact", "tax_for", "--repo", SHOP, "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["affected"] == 6


def test_callers_callees_and_path(capsys):
    assert main(["callers", "price_order", "--repo", SHOP]) == 0
    assert "OrderService.reprice" in capsys.readouterr().out
    assert main(["callees", "price_order", "--repo", SHOP]) == 0
    assert "best.apply" in capsys.readouterr().out
    assert main(["path", "test_percent_discount_and_tax", "tax_for", "--repo", SHOP]) == 0
    assert "shop.pricing.price_order" in capsys.readouterr().out


def test_unknown_symbol_exits_nonzero(capsys):
    assert main(["impact", "does_not_exist", "--repo", SHOP]) == 1
    assert "no symbol named" in capsys.readouterr().out


def test_module_entry_point_runs(tmp_path):
    result = subprocess.run(
        [sys.executable, "-m", "ripple", "index", SHOP],
        capture_output=True,
        text=True,
        env={"RIPPLE_CACHE_DIR": str(tmp_path), "PATH": ""},
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
