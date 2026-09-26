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


def test_impact_text_output(capsys):
    assert main(["impact", "tax_for", "--repo", SHOP]) == 0
    out = capsys.readouterr().out
    assert "shop.tax.tax_for" in out
    assert "shop/service.py" in out
    assert "tests.test_service.test_reprice_drops_discounts" in out


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
