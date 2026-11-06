from warehouse.receivers import Gate, Journal, LoudDeque, drain, load, recent, trim


def test_recent_keeps_the_tail():
    assert recent(2) == ["x", "y"]


def test_trim_a_loud_deque():
    assert trim(LoudDeque()) == 2


def test_load(tmp_path):
    (tmp_path / "log.txt").write_text("hi")
    assert load(tmp_path / "log.txt") == "hi"


def test_gate_opens():
    assert Gate().open() is True


def test_drain_into_a_journal():
    journal = Journal()
    drain(journal)
    assert journal.read_text() == "drained"
