from ledger import CreditNote, Invoice, Money
from ledger.store import InvoiceStore


def widgets(cls=Invoice, number="I1", region="CA"):
    invoice = cls(number, region)
    invoice.add_line("widget", 2, Money(1250))
    return invoice


def test_total_adds_regional_tax():
    invoice = widgets()
    assert invoice.subtotal() == Money(2500)
    assert invoice.tax() == Money(181)
    assert invoice.total() == Money(2681)


def test_credit_note_total_is_negative():
    assert widgets(CreditNote, "C1").total() == Money(-2681)


def test_render_ends_with_the_total():
    assert widgets().render().splitlines()[-1].endswith("$26.81")


def test_store_round_trip():
    store = InvoiceStore()
    number = store.save(widgets())
    assert store.load(number).total() == Money(2681)
    assert store.numbers() == ["I1"]
