import io

from check_helpers import references

from rich.ansi import AnsiDecoder
from rich.color import Color
from rich.console import Console
from rich.style import Style
from rich.theme import Theme

# Every other attribute access to `.parse` in the repository is one of these two receivers.
OTHER_PARSES = {"Color", "parser"}


def test_style_parse_is_renamed_and_color_parse_is_not():
    assert "from_string" in vars(Style) and "parse" not in vars(Style)
    assert "parse" in vars(Color)
    assert Style.from_string("bold red on blue") == Style(color="red", bgcolor="blue", bold=True)
    assert Color.parse("red").name == "red"


def test_no_call_to_the_old_name_is_left_anywhere():
    """Every file counts, the ones the tests never run (the Windows console, examples, benchmarks) too."""
    uses = references("parse")
    stale = [(f, line, recv) for f, line, kind, recv in uses if kind == "attr" and recv not in OTHER_PARSES]
    assert stale == []
    new_uses = {f for f, _line, kind, _recv in references("from_string") if kind == "attr"}
    assert {"rich/console.py", "rich/theme.py", "rich/ansi.py", "rich/_win32_console.py", "rich/style.py"} <= new_uses


def test_every_caller_still_works():
    theme = Theme({"warn": "bold red"})
    console = Console(file=io.StringIO(), force_terminal=True, color_system="truecolor", theme=theme)
    assert console.get_style("warn") == Style(color="red", bold=True)
    assert console.get_style("italic green on white") == Style(color="green", bgcolor="white", italic=True)
    assert Style.normalize("BOLD   Red") == "bold red"
    loaded = Theme.from_file(io.StringIO("[styles]\ninfo = dim cyan\nbad = bold red on black\n"))
    assert loaded.styles["bad"] == Style(color="red", bgcolor="black", bold=True)
    (line,) = AnsiDecoder().decode("\x1b[1;31mred\x1b[0m")
    assert line.plain == "red"
