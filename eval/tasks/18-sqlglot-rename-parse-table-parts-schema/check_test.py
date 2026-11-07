import ast
import inspect

import pytest
from check_helpers import references, sources

import sqlglot
from sqlglot import exp
from sqlglot.parser import Parser
from sqlglot.parsers.bigquery import BigQueryParser
from sqlglot.parsers.snowflake import SnowflakeParser
from sqlglot.parsers.tsql import TSQLParser

DEFINERS = (Parser, BigQueryParser, SnowflakeParser, TSQLParser)
CALLEES = {"self._parse_table_parts", "super()._parse_table_parts"}


def test_the_parameter_is_renamed_on_the_base_and_on_every_override():
    for cls in DEFINERS:
        assert "_parse_table_parts" in vars(cls), cls
        names = list(inspect.signature(vars(cls)["_parse_table_parts"]).parameters)
        assert names == ["self", "is_schema", "is_db_reference", "wildcard", "fast"], (cls, names)


def test_no_call_still_passes_schema():
    assert [r for r in references("schema") if r[2] == "param" and r[3] == "_parse_table_parts"] == []
    assert [r for r in references("schema") if r[2] == "kwarg" and r[3] in CALLEES] == []
    users = {f for f, _l, kind, callee in references("is_schema") if kind == "kwarg" and callee in CALLEES}
    # the base parser, the dialect parsers that override it, and the ones that call it with schema=True
    assert {"sqlglot/parser.py", "sqlglot/parsers/bigquery.py", "sqlglot/parsers/snowflake.py",
            "sqlglot/parsers/tsql.py", "sqlglot/parsers/clickhouse.py", "sqlglot/parsers/oracle.py"} == users  # fmt: skip
    # other functions called `schema=` are untouched: `_parse_table_part` and `_parse_table` still take it
    assert any(kind == "kwarg" and callee == "self._parse_table_part" for _f, _l, kind, callee in references("schema"))
    params = {fn for _f, _l, kind, fn in references("schema") if kind == "param"}
    assert {"_parse_table", "_parse_table_part"} <= params


def table_parts_bodies():
    """(file, line, the function's tree) for the base method and every override of _parse_table_parts."""
    return [
        (f, fn.lineno, fn)
        for f, tree in sources()
        for fn in ast.walk(tree)
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) and fn.name == "_parse_table_parts"
    ]


def test_no_method_body_still_reads_the_old_parameter():
    """The prompt asks for the code that reads the parameter to change too. A bare `schema` left
    inside one of these methods is a NameError at run time, whatever the signature says."""
    bodies = table_parts_bodies()
    assert {f for f, _l, _fn in bodies} == {"sqlglot/parser.py", "sqlglot/parsers/bigquery.py",
                                            "sqlglot/parsers/snowflake.py", "sqlglot/parsers/tsql.py"}  # fmt: skip
    stale = [(f, n.lineno) for f, _l, fn in bodies for n in ast.walk(fn) if isinstance(n, ast.Name) and n.id == "schema"]
    assert stale == []
    # and each one still reads the renamed parameter, so a body deleted or rewritten to ignore it is noticed
    unread = [(f, line) for f, line, fn in bodies if not any(isinstance(n, ast.Name) and n.id == "is_schema" for n in ast.walk(fn))]
    assert unread == []


ROUND_TRIPS = [
    ("", "INSERT INTO t (a, b) VALUES (1, 2)"),
    ("", "CREATE TABLE db.t (a INT)"),
    ("", "CREATE SCHEMA IF NOT EXISTS db"),
    ("", "DROP SCHEMA db"),
    ("", "SELECT * FROM c.d.t AS x"),
    ("oracle", "INSERT INTO t (a) VALUES (1)"),
    ("tsql", "CREATE TABLE #t (a INTEGER)"),
    ("tsql", "SELECT * FROM a..b"),
    ("tsql", "SELECT * FROM server.db.dbo.t"),  # the 4-part branch, which reads the parameter on its own
    ("", "SELECT * FROM a.b.c.d"),
    ("bigquery", "SELECT * FROM `proj.ds.tbl_*`"),
    ("snowflake", "SELECT * FROM @mystage/path (FILE_FORMAT => 'ff', PATTERN => '.*csv')"),
    ("snowflake", "CREATE TABLE db.sch.t (a INT)"),
    ("clickhouse", "CREATE TABLE db.t (a Int32) ENGINE=MergeTree ORDER BY a"),
]


@pytest.mark.parametrize("dialect, sql", ROUND_TRIPS)
def test_parsing_through_every_override_still_works(dialect, sql):
    tree = sqlglot.parse_one(sql, read=dialect or None)
    assert tree.sql(dialect=dialect or None) == sql
    assert tree.find(exp.Table) is not None or isinstance(tree, exp.Create)
