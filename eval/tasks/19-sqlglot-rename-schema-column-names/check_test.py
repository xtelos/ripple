import ast

from check_helpers import references, sources

from sqlglot import exp, parse_one
from sqlglot.lineage import lineage
from sqlglot.optimizer import optimize
from sqlglot.optimizer.annotate_types import annotate_types
from sqlglot.optimizer.isolate_table_selects import isolate_table_selects
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.qualify_tables import qualify_tables
from sqlglot.schema import MappingSchema, Schema


def test_the_schema_methods_are_renamed():
    assert "get_column_names" in vars(Schema) and "column_names" not in vars(Schema)
    assert "get_column_names" in vars(MappingSchema) and "column_names" not in vars(MappingSchema)
    assert not hasattr(MappingSchema({}), "column_names")
    assert MappingSchema({"t": {"a": "INT", "b": "TEXT"}}).get_column_names("t") == ["a", "b"]


def test_no_method_or_call_of_the_old_name_is_left():
    """What may remain is plain local variables called column_names (10 of them, in 4 files)."""
    uses = references("column_names")
    assert [u for u in uses if u[2] in ("attr", "def", "import", "kwarg", "param")] == []
    assert {u[0] for u in uses} == {"sqlglot/executor/table.py", "sqlglot/generator.py", "sqlglot/generators/bigquery.py", "tests/test_optimizer.py"}
    called_from = {f for f, _l, kind, _d in references("get_column_names") if kind == "attr"}
    assert {"sqlglot/lineage.py", "sqlglot/schema.py", "sqlglot/optimizer/annotate_types.py",
            "sqlglot/optimizer/isolate_table_selects.py", "sqlglot/optimizer/qualify_columns.py",
            "sqlglot/optimizer/resolver.py", "tests/test_optimizer.py", "tests/test_schema.py"} == called_from  # fmt: skip
    # one test refers to the method by its name as a string, in patch.object(schema, "column_names", ...)
    strings = [f for f, tree in sources() for n in ast.walk(tree) if isinstance(n, ast.Constant) and n.value == "column_names"]
    assert strings == []
    # a schema class written in a test is an override too
    assert ("tests/test_schema.py", "def", "CustomSchema") in {(f, k, d) for f, _l, k, d in references("get_column_names")}


class OnlyZ(MappingSchema):
    """A schema that overrides the method, as a user's own schema class would."""

    def get_column_names(self, table, only_visible=False, dialect=None, normalize=None):
        return ["z"]


def test_every_caller_still_works():
    schema = MappingSchema({"t": {"a": "INT", "b": "TEXT"}, "u": {"a": "INT", "c": "DATE"}})
    star = qualify(parse_one("SELECT * FROM t"), schema=schema, validate_qualify_columns=False)
    assert star.sql() == 'SELECT "t"."a" AS "a", "t"."b" AS "b" FROM "t" AS "t"'
    optimized = optimize("SELECT t.a, c FROM t JOIN u ON t.a = u.a", schema=schema)
    assert optimized.sql().startswith('SELECT "t"."a" AS "a", "u"."c" AS "c" FROM "t" AS "t" JOIN "u" AS "u"')
    typed = annotate_types(qualify(parse_one("SELECT a + 1 AS x FROM t"), schema=schema), schema=schema)
    assert typed.selects[0].type.this == exp.DataType.Type.INT
    isolated = isolate_table_selects(qualify_tables(parse_one("SELECT * FROM t JOIN u ON t.a = u.a")), schema=schema)
    assert isolated.sql().count("SELECT") == 3
    node = lineage("a", "SELECT a FROM t", schema=schema)
    assert node.source.sql() == "SELECT t.a AS a FROM t AS t"
    # the overriding subclass is what the optimizer asks
    assert qualify(parse_one("SELECT * FROM t"), schema=OnlyZ({"t": {"a": "INT"}}),
                   validate_qualify_columns=False).sql() == 'SELECT "t"."z" AS "z" FROM "t" AS "t"'  # fmt: skip
