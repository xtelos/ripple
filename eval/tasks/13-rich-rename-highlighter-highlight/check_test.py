import io

from check_helpers import references

from rich.console import Console
from rich.highlighter import Highlighter, ISO8601Highlighter, JSONHighlighter, NullHighlighter, RegexHighlighter, ReprHighlighter
from rich.progress import Progress, TextColumn
from rich.syntax import Syntax
from rich.text import Text

NEW = "apply_highlights"


def all_subclasses(cls):
    return {cls} | {sub for child in cls.__subclasses__() for sub in all_subclasses(child)}


def test_every_highlighter_class_defines_the_new_name_and_no_old_one():
    for cls in (Highlighter, NullHighlighter, RegexHighlighter, JSONHighlighter, ReprHighlighter, ISO8601Highlighter):
        assert "highlight" not in vars(cls), cls
    for cls in (Highlighter, NullHighlighter, RegexHighlighter, JSONHighlighter):
        assert NEW in vars(cls), cls
    assert "highlight" in vars(Syntax)  # an unrelated method of the same name
    assert NEW in Highlighter.__abstractmethods__


def test_nothing_in_the_repository_still_uses_the_old_name_for_a_highlighter():
    uses = references("highlight")
    # What remains is Syntax.highlight and the `highlight` attribute of tables, columns and options.
    assert [u for u in uses if u[2] == "def" and u[3] != "Syntax"] == []
    assert [u for u in uses if u[2] == "attr" and u[3] in ("super()", "self.highlighter", "highlighter")] == []
    assert [u for u in uses if u[2] == "attr" and u[3] == "self" and u[0] == "rich/highlighter.py"] == []
    renamed = {(f, kind, detail) for f, _l, kind, detail in references(NEW)}
    assert ("examples/rainbow.py", "def", "RainbowHighlighter") in renamed  # a subclass outside the package
    assert ("rich/progress.py", "attr", "self.highlighter") in renamed


def test_every_caller_still_works():
    assert ReprHighlighter()("x = 1, y = 'text'").spans
    assert any(span.style == "json.key" for span in JSONHighlighter()('{"a": 1}').spans)
    assert NullHighlighter()("plain").spans == []
    assert ISO8601Highlighter()("2026-09-26T20:15:07Z").spans

    class Shout(Highlighter):
        def apply_highlights(self, text):
            text.stylize("bold", 0, len(text))

    assert Shout()("hey").spans[0].style == "bold"
    assert Shout()(Text("hey")).spans[0].style == "bold"

    class OnlyOldName(Highlighter):
        def highlight(self, text):  # pragma: no cover
            pass

    try:
        OnlyOldName()
    except TypeError:
        pass
    else:
        raise AssertionError("the abstract method must be the new name")

    console = Console(file=io.StringIO(), force_terminal=True, color_system="truecolor", width=40)
    column = TextColumn("{task.description}", highlighter=ReprHighlighter())
    with Progress(column, console=console, auto_refresh=False) as progress:
        progress.add_task("n = 5")
        assert column.render(progress.tasks[0]).spans
    with Progress(TextColumn("{task.description}", highlighter=JSONHighlighter()), console=console) as progress:
        progress.add_task('{"k": 1}')
    assert "\x1b[" in console.file.getvalue()
