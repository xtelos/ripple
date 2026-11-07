import io

from check_helpers import references

import rich.cells as cells
from rich.console import Console
from rich.panel import Panel
from rich.pretty import Pretty
from rich.rule import Rule
from rich.segment import Segment
from rich.syntax import Syntax
from rich.text import Text


def test_the_function_is_renamed_and_the_text_property_is_not():
    assert cells.cell_width("日本語") == 6 and cells.cell_width("abc" * 200) == 600
    assert not hasattr(cells, "cell_len")
    assert cells.cached_cell_len("abc") == 3  # similarly named, a different function: it stays
    assert Text("😀").cell_len == 2 and isinstance(vars(Text)["cell_len"], property)


def test_no_mention_of_the_old_function_is_left():
    uses = references("cell_len")
    # What may remain is the Text.cell_len property and reads of it on Text objects.
    assert [u for u in uses if u[2] in ("name", "import", "kwarg", "param")] == []
    assert [u for u in uses if u[2] == "def" and u[3] != "Text"] == []
    assert [u for u in uses if u[2] == "attr" and u[3] == "cells"] == []
    # the modules that used the function use the new name
    importers = {f for f, _l, kind, mod in references("cell_width") if kind == "import"}
    assert {"rich/segment.py", "rich/pretty.py", "rich/rule.py", "rich/text.py", "rich/containers.py",
            "rich/syntax.py", "rich/panel.py", "rich/_wrap.py"} <= importers  # fmt: skip


def render(renderable, width=40):
    console = Console(file=io.StringIO(), width=width, color_system=None, force_terminal=False)
    console.print(renderable)
    return console.file.getvalue()


def test_every_caller_still_works():
    assert render(Rule("日本語 title")).splitlines()[0].count("─") > 20
    assert "日本語" in render(Panel("日本語 text", title="タイトル", width=20))
    assert render(Text("one two three four five six", justify="center"), width=12).splitlines()[0].strip() == "one two"
    assert render(Pretty({"key": [1, 2, 3], "名前": "値"}), width=20).count("\n") >= 3
    assert "def f" in render(Syntax("def f(x):\n    return x\n", "python"))
    segment = Segment("日本語abc")
    left, right = Segment.split_cells(segment, 4)
    assert (left.text, right.text) == ("日本", "語abc")
    assert Segment.get_line_length([segment]) == 9
    assert len(Text("a日b").wrap(Console(width=3), 3)) == 2
