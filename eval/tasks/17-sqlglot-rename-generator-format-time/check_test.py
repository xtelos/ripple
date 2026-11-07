import hashlib

from check_helpers import references

from sqlglot import exp
from sqlglot.dialects.dialect import Dialect
from sqlglot.generator import Generator
from sqlglot.generators.hive import HiveGenerator
from sqlglot.time import format_time

NEW = "render_time_format"
# Every remaining `.format_time` is a call on a dialect (the classmethod Dialect.format_time).
DIALECT_RECEIVERS = {"target_dialect", "Dialect['mysql']", "MySQL"}


def test_the_generator_method_is_renamed_and_the_dialect_one_is_not():
    assert NEW in vars(Generator) and "format_time" not in vars(Generator)
    assert NEW in vars(HiveGenerator) and "format_time" not in vars(HiveGenerator)
    assert isinstance(vars(Dialect)["format_time"], classmethod)
    assert callable(format_time)  # sqlglot.time.format_time, a module function, is a third thing of that name


def test_no_generator_still_calls_the_old_name():
    uses = references("format_time")
    assert [u for u in uses if u[2] == "attr" and u[3] not in DIALECT_RECEIVERS] == []
    assert sorted({u[3] for u in uses if u[2] == "def"}) == ["", "Dialect"]
    called_from = {f for f, _l, kind, _d in references(NEW) if kind == "attr"}
    # the base class, a closure over a generator in dialect.py, and every dialect's generator module
    assert {"sqlglot/generator.py", "sqlglot/dialects/dialect.py", "sqlglot/generators/hive.py",
            "sqlglot/generators/presto.py", "sqlglot/generators/snowflake.py", "sqlglot/generators/duckdb.py",
            "sqlglot/generators/singlestore.py", "sqlglot/generators/exasol.py"} <= called_from  # fmt: skip
    assert len(called_from) == 20


def render_everywhere():
    col = exp.column("x")
    fmt = exp.Literal.string("%Y-%m-%d %H:%M:%S")
    day = exp.Literal.string("%Y-%m-%d")
    expressions = [
        exp.TimeToStr(this=col.copy(), format=fmt.copy()),
        exp.StrToTime(this=col.copy(), format=fmt.copy()),
        exp.StrToDate(this=col.copy(), format=day.copy()),
        exp.TsOrDsToDate(this=col.copy(), format=day.copy()),
        exp.StrToUnix(this=col.copy(), format=fmt.copy()),
        exp.UnixToStr(this=col.copy(), format=fmt.copy()),
        exp.ParseTime(this=col.copy(), format=fmt.copy()),
    ]
    lines = []
    for name in sorted(k for k in Dialect.classes if k):
        for index, expression in enumerate(expressions):
            try:
                lines.append(f"{name}/{index}: {expression.sql(dialect=name)}")
            except Exception as error:  # an unsupported expression is part of the dialect's behaviour too
                lines.append(f"{name}/{index}: {type(error).__name__}")
    return lines


def test_every_dialects_time_rendering_is_unchanged():
    lines = render_everywhere()
    assert len(lines) >= 7 * 20
    by_key = dict(line.split(": ", 1) for line in lines)
    assert by_key["hive/0"] == "DATE_FORMAT(x, 'yyyy-MM-dd HH:mm:ss')"
    assert by_key["presto/0"] == "DATE_FORMAT(x, '%Y-%m-%d %T')"
    digest = hashlib.sha256("\n".join(lines).encode()).hexdigest()
    # sha256 of every line above, taken on the untouched checkout: the rename must change no output
    assert digest == "a317757e58ba12272f20655c0174906960fa86f8104b7da61ab6f98750ce3db7", digest
