"""Execute real Gold notebook definitions with bounded, in-memory Spark adapters.

These are deterministic unit/contract tests, not Spark or Delta integration tests.
No notebook orchestration, network access, package installation, or cloud writes run.
"""

from __future__ import annotations

import ast
import copy
import datetime
import hashlib
import json
import os
import re
import tempfile
import unittest
from decimal import Decimal
from functools import reduce
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2] / "artefacts" / "medallion"
NOTEBOOKS = [
    "03_Gold_SCD2_Dimensions",
    "04_Gold_Atomic_Finance_Fact",
    "05_Gold_Budget_Fact",
    "07_End_to_End_Validation",
]
NOW = datetime.datetime(2026, 11, 19, 12)


def notebook(name: str) -> dict:
    return json.loads((ROOT / f"{name}.ipynb").read_text(encoding="utf-8"))


def column(value):
    return value if isinstance(value, Column) else Column(lambda row, rows, i: value)


def cast(value, data_type):
    if value is None:
        return None
    if data_type in ("string", str):
        return value.isoformat() if isinstance(value, datetime.date) else str(value)
    if data_type in ("date", datetime.date):
        return value if isinstance(value, datetime.date) else datetime.date.fromisoformat(value)
    if data_type in ("int", "long", int):
        return int(value)
    if isinstance(data_type, str) and data_type.startswith("decimal"):
        scale = int(data_type.rstrip(")").split(",")[1])
        return Decimal(str(value)).quantize(Decimal(1).scaleb(-scale))
    return value


class Column:
    """Evaluate only expressions used by the notebook functions under test."""

    def __init__(self, evaluate, name=None):
        self.evaluate, self.name, self.fields = evaluate, name, None

    def get(self, row, rows=None, index=0):
        return self.evaluate(row, rows or [row], index)

    def __bool__(self):
        raise TypeError("Column expressions must be evaluated, not coerced to bool")

    def alias(self, name):
        result = copy.copy(self)
        result.name = name
        return result

    def cast(self, data_type):
        return Column(lambda row, rows, i: cast(self.get(row, rows, i), data_type), self.name)

    def binary(self, other, operation):
        other = column(other)
        return Column(lambda row, rows, i: operation(self.get(row, rows, i), other.get(row, rows, i)))

    def __eq__(self, other):
        return self.binary(other, lambda left, right: left is not None and right is not None and left == right)

    def __ne__(self, other):
        return self.binary(other, lambda left, right: left is not None and right is not None and left != right)

    def __gt__(self, other):
        return self.binary(other, lambda left, right: left is not None and right is not None and left > right)

    def __le__(self, other):
        return self.binary(other, lambda left, right: left is not None and right is not None and left <= right)

    def __and__(self, other):
        return self.binary(other, lambda left, right: bool(left and right))

    def __or__(self, other):
        return self.binary(other, lambda left, right: bool(left or right))

    def __invert__(self):
        return Column(lambda row, rows, i: not self.get(row, rows, i))

    def eqNullSafe(self, other):
        return self.binary(other, lambda left, right: left == right)

    def isNull(self):
        return Column(lambda row, rows, i: self.get(row, rows, i) is None)

    def isNotNull(self):
        return ~self.isNull()

    def isin(self, *values):
        return Column(lambda row, rows, i: self.get(row, rows, i) in values)

    def between(self, low, high):
        return (column(low) <= self) & (self <= column(high))


class Choice(Column):
    def __init__(self, condition, value):
        super().__init__(lambda row, rows, i: None)
        self.cases = [(condition, column(value))]

    def when(self, condition, value):
        self.cases.append((condition, column(value)))
        return self

    def otherwise(self, fallback):
        fallback = column(fallback)

        def evaluate(row, rows, index):
            for condition, value in self.cases:
                if condition.get(row, rows, index):
                    return value.get(row, rows, index)
            return fallback.get(row, rows, index)

        return Column(evaluate)


class Aggregate(Column):
    def __init__(self, kind, value):
        super().__init__(lambda row, rows, i: None)
        self.kind, self.value = kind, value

    def total(self, rows):
        if isinstance(self.value, str) and self.value == "*":
            return len(rows)
        values = [self.value.get(row) for row in rows]
        values = [value for value in values if value is not None]
        if self.kind == "count":
            return len(values)
        if not values:
            return None
        return sum(values) if self.kind == "sum" else max(values)

    def over(self, window):
        def evaluate(row, rows, index):
            partition = [item for item in rows if all(item[key] == row[key] for key in window.keys)]
            partition.sort(key=lambda item: tuple(item[key] for key in window.order))
            position = next(i for i, item in enumerate(partition) if item is row)
            return self.total(partition[:position])

        return Column(evaluate)


class Functions:
    @staticmethod
    def col(name):
        def evaluate(row, rows, index):
            if name in row:
                return row[name]
            value = row
            for part in name.split("."):
                value = value.get(part) if isinstance(value, dict) else None
            return value

        return Column(evaluate, name.split(".")[-1])

    lit = staticmethod(column)
    when = staticmethod(Choice)

    @staticmethod
    def coalesce(*values):
        values = [column(value) for value in values]
        return Column(lambda row, rows, i: next((value.get(row, rows, i) for value in values if value.get(row, rows, i) is not None), None))

    @staticmethod
    def trim(value):
        return Column(lambda row, rows, i: None if value.get(row, rows, i) is None else value.get(row, rows, i).strip())

    @staticmethod
    def concat_ws(separator, *values):
        return Column(lambda row, rows, i: separator.join(str(value.get(row, rows, i)) for value in values if value.get(row, rows, i) is not None))

    @staticmethod
    def sha2(value, bits):
        assert bits == 256
        return Column(lambda row, rows, i: hashlib.sha256(value.get(row, rows, i).encode()).hexdigest())

    @staticmethod
    def md5(value):
        return Column(lambda row, rows, i: hashlib.md5(value.get(row, rows, i).encode(), usedforsecurity=False).hexdigest())

    @staticmethod
    def greatest(*values):
        return Column(lambda row, rows, i: max(column(value).get(row, rows, i) for value in values))

    @staticmethod
    def least(*values):
        return Column(lambda row, rows, i: min(column(value).get(row, rows, i) for value in values))

    @staticmethod
    def make_date(year, month, day):
        return Column(lambda row, rows, i: datetime.date(*(value.get(row, rows, i) for value in [year, month, day])))

    @staticmethod
    def date_sub(value, days):
        value = Functions.col(value) if isinstance(value, str) else value
        return Column(lambda row, rows, i: value.get(row, rows, i) - datetime.timedelta(days=days))

    @staticmethod
    def lower(value):
        return Column(lambda row, rows, i: value.get(row, rows, i).lower())

    @staticmethod
    def json_object_keys(value):
        def evaluate(row, rows, index):
            raw = value.get(row, rows, index)
            try:
                parsed = json.loads(raw) if raw is not None else None
            except json.JSONDecodeError:
                return None
            return list(parsed) if isinstance(parsed, dict) else None

        return Column(evaluate)

    @staticmethod
    def array_contains(values, value):
        return Column(lambda row, rows, i: value in (values.get(row, rows, i) or []))

    @staticmethod
    def from_json(name, schema, options):
        assert options == {"mode": "FAILFAST"}
        result = Column(lambda row, rows, i: {field.name: cast(json.loads(row[name]).get(field.name), field.dataType) for field in schema})
        result.fields = [field.name for field in schema]
        return result

    @staticmethod
    def struct(*values):
        return Column(lambda row, rows, i: {value.name: value.get(row, rows, i) for value in values})

    @staticmethod
    def array(*values):
        return Column(lambda row, rows, i: [value.get(row, rows, i) for value in values])

    @staticmethod
    def explode(value):
        result = copy.copy(value)
        result.exploded = True
        return result

    @staticmethod
    def count(value):
        return Aggregate("count", value if isinstance(value, str) and value == "*" else Functions.col(value) if isinstance(value, str) else value)

    @staticmethod
    def sum(value):
        return Aggregate("sum", Functions.col(value) if isinstance(value, str) else value)

    @staticmethod
    def max(value):
        return Aggregate("max", Functions.col(value) if isinstance(value, str) else value)


class WindowSpec:
    def __init__(self, *keys):
        self.keys = keys

    def orderBy(self, *keys):
        self.order = keys
        return self

    def rowsBetween(self, start, end):
        assert end == -1
        return self


class Rows:
    """Tiny relational adapter; it does not emulate Spark execution/planning."""

    def __init__(self, rows, columns=None, spark=None):
        self.rows = [dict(row) for row in rows]
        self.columns = list(columns if columns is not None else dict.fromkeys(key for row in rows for key in row if "." not in key))
        self.spark, self.nested, self.alias_name, self.alias_fields = spark, {}, None, {}

    @property
    def schema(self):
        return {name: SimpleNamespace(name=name, dataType=next((type(row[name]) for row in self.rows if row.get(name) is not None), str)) for name in self.columns}

    def new(self, rows, columns=None):
        result = Rows(rows, self.columns if columns is None else columns, self.spark)
        result.alias_fields = dict(self.alias_fields)
        return result

    def alias(self, name):
        result = self.new([{**row, **{f"{name}.{key}": row.get(key) for key in self.columns}} for row in self.rows])
        result.alias_name = name
        result.alias_fields[name] = self.columns
        return result

    def select(self, *values):
        expressions, fields = [], {}
        for value in values:
            if isinstance(value, str) and value.endswith(".*"):
                key = value[:-2]
                names = self.alias_fields.get(key) or self.nested.get(key) or next((list(row[key]) for row in self.rows if isinstance(row.get(key), dict)), [])
                expressions.extend(Functions.col(f"{key}.{name}").alias(name) for name in names)
            else:
                expression = Functions.col(value) if isinstance(value, str) else value
                expressions.append(expression)
                if expression.fields:
                    fields[expression.name] = expression.fields
        result = self.new([{expression.name: expression.get(row, self.rows, i) for expression in expressions} for i, row in enumerate(self.rows)], [expression.name for expression in expressions])
        result.nested = fields
        return result

    def withColumn(self, name, value):
        rows = []
        for i, row in enumerate(self.rows):
            result = value.get(row, self.rows, i)
            for item in result if getattr(value, "exploded", False) else [result]:
                rows.append({**row, name: item})
        return self.new(rows, list(dict.fromkeys([*self.columns, name])))

    def drop(self, *names):
        return self.select(*(name for name in self.columns if name not in names))

    def filter(self, condition):
        return self.new([row for i, row in enumerate(self.rows) if condition.get(row, self.rows, i)])

    def count(self):
        return len(self.rows)

    def limit(self, size):
        return self.new(self.rows[:size])

    def distinct(self):
        return self.dropDuplicates(self.columns)

    def dropDuplicates(self, keys):
        rows, seen = [], set()
        for row in self.rows:
            value = tuple(row.get(key) for key in keys)
            if value not in seen:
                rows.append(row)
                seen.add(value)
        return self.new(rows)

    def unionByName(self, other, allowMissingColumns=False):
        names = list(dict.fromkeys([*self.columns, *other.columns]))
        if not allowMissingColumns:
            assert set(self.columns) == set(other.columns)
        return self.new([{name: row.get(name) for name in names} for row in self.rows + other.rows], names)

    def join(self, other, on, how="inner"):
        keys = [on] if isinstance(on, str) else on if isinstance(on, list) else []
        match = (lambda left, right: all(left.get(key) is not None and left.get(key) == right.get(key) for key in keys)) if keys else (lambda left, right: on.get({**left, **right}))
        rows, matched = [], set()
        null_right = {key: None for key in other.columns}
        if other.alias_name:
            null_right.update({f"{other.alias_name}.{key}": None for key in other.columns})
        for left in self.rows:
            found = [(i, right) for i, right in enumerate(other.rows) if match(left, right)]
            if how in ("left_semi", "left_anti"):
                if bool(found) == (how == "left_semi"):
                    rows.append(left)
                continue
            for index, right in found:
                rows.append({**left, **{key: value for key, value in right.items() if key not in keys}})
                matched.add(index)
            if not found and how in ("left", "full"):
                rows.append({**left, **{key: value for key, value in null_right.items() if key not in keys}})
        if how == "full":
            null_left = {key: None for key in self.columns}
            if self.alias_name:
                null_left.update({f"{self.alias_name}.{key}": None for key in self.columns})
            rows.extend({**null_left, **right} for index, right in enumerate(other.rows) if index not in matched)
        result = self.new(rows, self.columns if how in ("left_semi", "left_anti") else list(dict.fromkeys([*self.columns, *other.columns])))
        result.alias_fields.update(other.alias_fields)
        return result

    def groupBy(self, *keys):
        if len(keys) == 1 and isinstance(keys[0], list):
            keys = keys[0]
        source = self

        class Grouped:
            def agg(self, *aggregates):
                groups = {}
                for row in source.rows:
                    groups.setdefault(tuple(row.get(key) for key in keys), []).append(row)
                return source.new([{**dict(zip(keys, values)), **{aggregate.name: aggregate.total(rows) for aggregate in aggregates}} for values, rows in groups.items()], [*keys, *(aggregate.name for aggregate in aggregates)])

            def count(self):
                return self.agg(Functions.count("*").alias("count"))

        return Grouped()

    @property
    def write(self):
        source = self

        class Writer:
            def format(self, value):
                assert value == "delta"
                return self

            def mode(self, value):
                self.write_mode = value
                return self

            def saveAsTable(self, target):
                rows = source.select(*source.columns)
                if self.write_mode == "append" and target in source.spark.tables:
                    rows = source.spark.tables[target].unionByName(rows)
                source.spark.tables[target] = rows

        return Writer()


class Spark:
    def __init__(self, tables=None):
        self.tables, self.merges = {}, []
        self.catalog = SimpleNamespace(tableExists=lambda name: name in self.tables)
        for name, rows in (tables or {}).items():
            rows.spark = self
            self.tables[name] = rows

    def table(self, name):
        rows = self.tables[name]
        return Rows(rows.rows, rows.columns, self)


class Delta:
    @staticmethod
    def forName(spark, target):
        class Merge:
            delete = False
            update_all = False
            insert_all = False
            values = None

            def alias(self, name):
                return self

            def merge(self, actions, condition):
                self.actions = actions
                self.key = re.search(r"tgt\.`?(\w+)`?\s*=", condition)[1]
                self.source_key = re.search(r"=\s*src\.`?(\w+)`?", condition)[1]
                return self

            def withSchemaEvolution(self):
                self.evolved = True
                return self

            def whenMatchedDelete(self, condition):
                assert condition == "src._DELETE"
                self.delete = True
                return self

            def whenMatchedUpdate(self, condition, set):
                assert "NOT src._DELETE" in condition
                self.compared = re.findall(r"tgt\.`([^`]+)` <=>", condition)
                self.always_update = "(true)" in condition
                assert self.compared or self.always_update
                self.values = set
                return self

            def whenNotMatchedInsert(self, condition, values):
                assert condition == "NOT src._DELETE"
                self.values = values
                return self

            def whenMatchedUpdateAll(self):
                self.update_all = True
                return self

            def whenNotMatchedInsertAll(self):
                self.insert_all = True
                return self

            def execute(self):
                existing = spark.table(target)
                rows = [dict(row) for row in existing.rows]
                for action in self.actions.rows:
                    indices = [i for i, row in enumerate(rows) if row[self.key] == action[self.source_key]]
                    assert len(indices) <= 1
                    fields = self.actions.columns if self.values is None else list(self.values)
                    projected = {key: action.get(key) for key in fields}
                    if indices:
                        index = indices[0]
                        if self.delete and action["_DELETE"]:
                            rows.pop(index)
                        elif self.update_all or (not action.get("_DELETE") and (self.always_update or any(rows[index].get(key) != action.get(key) for key in self.compared))):
                            rows[index] = projected
                    elif self.insert_all or not action.get("_DELETE"):
                        rows.append(projected)
                names = existing.columns
                if getattr(self, "evolved", False):
                    names = list(dict.fromkeys([*names, *self.values]))
                spark.tables[target] = Rows(rows, names, spark)
                spark.merges.append(target)

        return Merge()


def definitions(name: str, spark=None, *, native: bool = False) -> dict:
    """Load real function ASTs/constants, never reimplement notebook business rules."""
    data = notebook(name)
    code = "\n".join("".join(cell["source"]) for cell in data["cells"] if cell["cell_type"] == "code")
    constants = {"DIM_CONFIG", "EXTRACTION_METADATA", "SOURCE_METADATA", "ACDOCA_KEYS", "BKPF_KEYS", "BUDGET_KEYS", "PLAN_KEYS", "grain", "FINANCE_MEASURES", "MASTER_SOURCE_KEYS", "FINANCE_MASTER_MAP", "PLAN_MASTER_MAP", "FINANCE_RECON_KEYS", "LEGACY_BUDGET_GRAIN", "BUDGET_SOURCE_GRAIN", "BUDGET_MIGRATION_RECOVERY"}
    nodes = [node for node in ast.parse(code).body if isinstance(node, ast.FunctionDef) or isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id in constants for target in node.targets)]
    namespace = {
        "F": Functions, "T": SimpleNamespace(StructType=list), "DeltaTable": Delta,
        "Window": SimpleNamespace(partitionBy=WindowSpec, unboundedPreceding=-999999),
        "spark": spark or Spark(), "datetime": datetime, "reduce": reduce,
        "SLV": "silver", "DIM": "dim", "AUD": "audit", "HIGH_DATE": "9999-12-31",
        "run_id": "run-1", "effective_dt": "", "BATCH_ID": "run-1", "RUN_TIMESTAMP": NOW,
        "_SAP_EXTRACTION_CONFIG": {"tables": [{"name": "BUDGET", "financeContext": {"client": "100", "ledger": "0L"}}]},
    }
    if not native and "dim.company" not in namespace["spark"].tables:
        # Explicit test master data, not a production fallback or inferred customer context.
        namespace["spark"].tables["dim.company"] = Rows([
            {"MANDT": "100", "BUKRS": code, "WAERS": currency, "EFFECTIVE_FROM": datetime.date(1900, 1, 1), "EFFECTIVE_TO": datetime.date(9999, 12, 31)}
            for code, currency in [("1000", "ZAR"), ("2000", "USD"), ("9999", "EUR")]
        ], spark=namespace["spark"])
    if native:
        from delta.tables import DeltaTable
        from pyspark.sql import Window, functions, types

        namespace.update(F=functions, T=types, Window=Window, DeltaTable=DeltaTable)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), f"{name}:definitions", "exec"), namespace)
    return namespace


class GoldIncrementalTests(unittest.TestCase):
    def budget_fact_row(self, module, *, client="100", ledger="0L", currency="ZAR", legacy=False):
        row = {
            "PLAN_SOURCE": "FAGLFLEXP", "SCENARIO": "PLAN", "COMPANY_CODE": "1000",
            "GL_ACCOUNT": "4000", "COST_CENTER": None, "PROFIT_CENTER": "P1",
            "FISCAL_YEAR": 2026, "FISCAL_PERIOD": 1, "PLAN_VERSION": "F1",
            "PLAN_AMOUNT_LOCAL": Decimal("12.34"), "GOLD_LOAD_TIMESTAMP": NOW,
        }
        if not legacy:
            row.update(RCLNT=client, RLDNR=ledger, RHCUR=currency)
        keys = module["LEGACY_BUDGET_GRAIN"] if legacy else module["grain"]
        row["BUDGET_FACT_SK"] = module["business_key_hash"]([Functions.col(key) for key in keys]).get(row)
        return row

    def budget_file_row(self, *, company="1000", year=2026):
        return {
            "COMPANY_CODE": company, "GL_ACCOUNT": "4000", "COST_CENTER": None,
            "PROFIT_CENTER": "P1", "FISCAL_YEAR": year, "FISCAL_PERIOD": 1,
            "PLAN_VERSION": None, "BUDGET_AMOUNT_LOCAL": Decimal("12.34"),
            "FORECAST_AMOUNT_LOCAL": Decimal("15.67"),
        }

    def test_budget_finance_context_is_explicit_metadata_without_customer_defaults(self):
        module = definitions(NOTEBOOKS[2])
        context = module["budget_finance_context"]
        self.assertEqual(context(), ("100", "0L"))
        self.assertEqual(context({"tables": [{"name": "BUDGET", "financeContext": {"client": "200", "ledger": "2L"}}]}), ("200", "2L"))
        for invalid in [{}, {"tables": []}, {"tables": [{"name": "BUDGET"}]},
                        {"tables": [{"name": "BUDGET", "financeContext": {"client": 100, "ledger": "0L"}}]},
                        {"tables": [{"name": "BUDGET", "financeContext": {"client": "100", "ledger": ""}}]}]:
            with self.subTest(config=invalid), self.assertRaises(ValueError):
                context(invalid)
        module.pop("_SAP_EXTRACTION_CONFIG")
        with self.assertRaisesRegex(ValueError, "no customer defaults"):
            context()

    def test_budget_file_currency_uses_explicit_client_and_asof_t001_history(self):
        module = definitions(NOTEBOOKS[2])
        module["spark"].tables["dim.company"] = Rows([
            {"MANDT": "100", "BUKRS": "1000", "WAERS": "ZAR", "EFFECTIVE_FROM": datetime.date(1900, 1, 1), "EFFECTIVE_TO": datetime.date(2025, 12, 31)},
            {"MANDT": "100", "BUKRS": "1000", "WAERS": "USD", "EFFECTIVE_FROM": datetime.date(2026, 1, 1), "EFFECTIVE_TO": datetime.date(9999, 12, 31)},
            {"MANDT": "200", "BUKRS": "1000", "WAERS": "EUR", "EFFECTIVE_FROM": datetime.date(1900, 1, 1), "EFFECTIVE_TO": datetime.date(9999, 12, 31)},
        ])
        rows = module["budget_projection"](Rows([self.budget_file_row(year=2024), self.budget_file_row(year=2027)]))
        self.assertEqual([row["RHCUR"] for row in rows.rows], ["ZAR", "USD"])
        self.assertTrue(all(row["RCLNT"] == "100" and row["RLDNR"] == "0L" for row in rows.rows))
        self.assertEqual(rows.rows[0]["PLAN_AMOUNT_LOCAL"], Decimal("12.34"))

    def test_budget_file_missing_or_ambiguous_t001_mapping_fails_instead_of_guessing(self):
        module = definitions(NOTEBOOKS[2])
        source = Rows([self.budget_file_row()])
        company = module["spark"].table("dim.company")
        module["spark"].tables["dim.company"] = company.filter(Functions.col("BUKRS") != "1000")
        with self.assertRaisesRegex(ValueError, "never infer a currency"):
            module["budget_projection"](source)
        module["spark"].tables["dim.company"] = company.unionByName(company)
        with self.assertRaisesRegex(RuntimeError, "ambiguous budget identity"):
            module["budget_projection"](source)

    def test_legacy_budget_migration_is_atomic_additive_and_replay_safe(self):
        module = definitions(NOTEBOOKS[2])
        target = "fact.budget"
        legacy = self.budget_fact_row(module, legacy=True)
        modern = self.budget_fact_row(module)
        module["spark"].tables[target] = Rows([legacy], spark=module["spark"])
        self.assertTrue(module["budget_requires_migration"](module["spark"].table(target)))
        output = Rows([modern], spark=module["spark"])
        prepared = module["prepare_budget_upserts"](output, module["spark"].table(target), migrating=True)
        self.assertEqual(prepared.rows[0]["_MATCH_SK"], legacy["BUDGET_FACT_SK"])
        empty = Rows([], ["BUDGET_FACT_SK"], module["spark"])
        module["merge_fact"](prepared, empty, target, "BUDGET_FACT_SK", additive_columns=("RCLNT", "RLDNR", "RHCUR"))
        migrated = module["spark"].table(target)
        self.assertEqual(migrated.count(), 1)
        self.assertEqual(migrated.rows[0]["BUDGET_FACT_SK"], modern["BUDGET_FACT_SK"])
        self.assertEqual(migrated.rows[0]["RHCUR"], "ZAR")
        self.assertNotIn("_MATCH_SK", migrated.columns)
        self.assertFalse(module["budget_requires_migration"](migrated))
        replay = module["prepare_budget_upserts"](output, migrated)
        module["merge_fact"](replay, empty, target, "BUDGET_FACT_SK", additive_columns=("RCLNT", "RLDNR", "RHCUR"))
        self.assertEqual(module["spark"].table(target).count(), 1)

    def test_legacy_budget_missing_ambiguous_or_overlapping_mappings_fail_before_fact_writes(self):
        module = definitions(NOTEBOOKS[2])
        legacy = self.budget_fact_row(module, legacy=True)
        modern = self.budget_fact_row(module)
        other = self.budget_fact_row(module, client="200", ledger="2L", currency="USD")
        existing = Rows([legacy])
        for output in [Rows([], list(modern)), Rows([modern, other])]:
            with self.subTest(candidates=output.count()), self.assertRaisesRegex(RuntimeError, "zero or multiple.*No Gold fact changes"):
                module["prepare_budget_upserts"](output, existing, migrating=True)
        mixed = Rows([legacy]).unionByName(Rows([modern]), allowMissingColumns=True)
        with self.assertRaisesRegex(RuntimeError, "Legacy and modern budget rows overlap"):
            module["prepare_budget_upserts"](Rows([modern]), mixed, migrating=True)
        known_wrong = {**legacy, "RCLNT": "200", "RLDNR": "2L", "RHCUR": "USD"}
        with self.assertRaisesRegex(RuntimeError, "identity conflicts"):
            module["prepare_budget_upserts"](Rows([modern]), Rows([known_wrong]), migrating=True)
        self.assertEqual(module["spark"].merges, [])

    def test_budget_currency_change_rekeys_once_and_deletes_use_exact_source_identity(self):
        module = definitions(NOTEBOOKS[2])
        target = "fact.budget"
        old = self.budget_fact_row(module)
        updated = self.budget_fact_row(module, currency="USD")
        other_ledger = self.budget_fact_row(module, client="200", ledger="2L", currency="EUR")
        module["spark"].tables[target] = Rows([old, other_ledger], spark=module["spark"])
        prepared = module["prepare_budget_upserts"](Rows([updated]), module["spark"].table(target))
        self.assertEqual(prepared.rows[0]["_MATCH_SK"], old["BUDGET_FACT_SK"])
        module["merge_fact"](prepared, Rows([], ["BUDGET_FACT_SK"]), target, "BUDGET_FACT_SK")
        result = module["spark"].table(target)
        self.assertEqual(result.count(), 2)
        self.assertEqual({row["RHCUR"] for row in result.rows}, {"USD", "EUR"})
        self.assertNotIn(old["BUDGET_FACT_SK"], {row["BUDGET_FACT_SK"] for row in result.rows})
        removed = Rows([{key: updated[key] for key in module["BUDGET_SOURCE_GRAIN"]}])
        deleted = module["budget_deleted_ids"](result, removed)
        self.assertEqual(deleted.select("BUDGET_FACT_SK").rows, [{"BUDGET_FACT_SK": updated["BUDGET_FACT_SK"]}])

    def test_finance_rtcur_backfill_requires_complete_explained_keys_and_is_additive(self):
        module = definitions(NOTEBOOKS[1])
        key = {"RCLNT": "100", "RLDNR": "0L", "RBUKRS": "1000", "GJAHR": 2026, "BELNR": "01", "DOCLN": "001"}
        sk = module["business_key_hash"]([Functions.col(column) for column in module["ACDOCA_KEYS"]]).get(key)
        legacy = {**key, "FINANCE_TRANSACTION_SK": sk, "AMOUNT_TRANSACTION": Decimal("12.34")}
        source = Rows([{**key, "RTCUR": "USD"}])
        empty_keys = Rows([], module["ACDOCA_KEYS"])
        module["validate_finance_currency_backfill"](Rows([legacy]), source, empty_keys)
        with self.assertRaisesRegex(RuntimeError, "Unresolved legacy finance"):
            module["validate_finance_currency_backfill"](Rows([legacy]), source.limit(0), empty_keys)
        module["validate_finance_currency_backfill"](Rows([legacy]), source.limit(0), Rows([key]))
        target = "fact.finance"
        module["spark"].tables[target] = Rows([legacy], spark=module["spark"])
        module["merge_fact"](Rows([{**legacy, "RTCUR": "USD"}]), Rows([], ["FINANCE_TRANSACTION_SK"]), target, "FINANCE_TRANSACTION_SK", additive_columns=("RTCUR",))
        self.assertEqual(module["spark"].table(target).rows[0]["RTCUR"], "USD")
        self.assertEqual(module["spark"].table(target).count(), 1)

    def test_notebooks_compile_and_expose_pipeline_parameters(self):
        for name in NOTEBOOKS:
            data = notebook(name)
            parameters = [cell for cell in data["cells"] if "parameters" in cell.get("metadata", {}).get("tags", [])]
            self.assertEqual(len(parameters), 1, name)
            self.assertIn('run_id = ""', "".join(parameters[0]["source"]))
            self.assertEqual(data["metadata"]["kernelspec"]["name"], "synapse_pyspark")
            for index, cell in enumerate(data["cells"]):
                if cell["cell_type"] == "code":
                    compile("".join(cell["source"]), f"{name}:{index}", "exec")
                    self.assertFalse(cell.get("outputs"), name)

    def test_complete_source_keys_include_client_ledger_and_master_interval(self):
        dimensions = definitions(NOTEBOOKS[0])["DIM_CONFIG"]
        finance = definitions(NOTEBOOKS[1])
        self.assertEqual(dimensions["cost_center"]["keys"], ["MANDT", "KOKRS", "KOSTL", "DATBI"])
        self.assertEqual(dimensions["profit_center"]["keys"], ["MANDT", "KOKRS", "PRCTR", "DATBI"])
        self.assertEqual(finance["ACDOCA_KEYS"], ["RCLNT", "RLDNR", "RBUKRS", "GJAHR", "BELNR", "DOCLN"])
        self.assertEqual(finance["BKPF_KEYS"], ["MANDT", "BUKRS", "GJAHR", "BELNR"])

    def test_carry_forward_000_and_special_periods_have_valid_calendar_fallbacks(self):
        def assigned(name, target):
            source = "\n".join("".join(cell["source"]) for cell in notebook(name)["cells"] if cell["cell_type"] == "code")
            return [node for node in ast.parse(source).body if isinstance(node, ast.Assign) and any(isinstance(item, ast.Name) and item.id == target for item in node.targets)]

        fiscal = assigned(NOTEBOOKS[0], "fiscal_period_numbers")[0]
        period_range = next(node for node in ast.walk(fiscal) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "range")
        self.assertEqual([ast.literal_eval(arg) for arg in period_range.args], [0, 17])
        finance = definitions(NOTEBOOKS[1])
        finance["ac"] = Rows([{"GJAHR": 2026, "POPER": period} for period in [0, 1, 13, 16]]).alias("a")
        exec(compile(ast.Module(body=assigned(NOTEBOOKS[1], "fallback_posting_date"), type_ignores=[]), "finance:fallback", "exec"), finance)
        dates = [finance["fallback_posting_date"].get(row) for row in finance["ac"].rows]
        self.assertEqual(dates, [datetime.date(2026, 1, 1), datetime.date(2026, 1, 1), datetime.date(2026, 12, 1), datetime.date(2026, 12, 1)])
        budget = definitions(NOTEBOOKS[2])
        exec(compile(ast.Module(body=assigned(NOTEBOOKS[2], "plan_date"), type_ignores=[]), "budget:date", "exec"), budget)
        self.assertEqual(budget["plan_date"].get({"FISCAL_YEAR": 2026, "FISCAL_PERIOD": 0}), datetime.date(2026, 1, 1))
        self.assertEqual(budget["plan_date"].get({"FISCAL_YEAR": 2026, "FISCAL_PERIOD": 16}), datetime.date(2026, 12, 31))

    def test_dimension_hash_ignores_all_extraction_metadata(self):
        module = definitions(NOTEBOOKS[0])
        metadata = {name: "new-technical-value" for name in module["EXTRACTION_METADATA"]}
        columns = ["MANDT", "LIFNR", "NAME1", *metadata]
        selected = module["business_hash_columns"](columns, columns)
        self.assertEqual(selected, ["MANDT", "LIFNR", "NAME1"])
        row = {"MANDT": "100", "LIFNR": "V1", "NAME1": " Name "}
        expected = module["sha256_all"](selected).get(row)
        self.assertEqual(module["sha256_all"](selected).get({**row, **metadata}), expected)

    def test_first_seen_keys_support_late_postings_and_reappearing_keys_do_not_backdate(self):
        policy = definitions(NOTEBOOKS[0])["dimension_version_start"]
        day = NOW.date()
        self.assertEqual(policy(None, False, day), datetime.date(1900, 1, 1))
        self.assertEqual(policy(datetime.date(2020, 1, 1), False, day), datetime.date(2020, 1, 1))
        self.assertEqual(policy(None, True, day), day)
        self.assertEqual(policy(None, False, day, "2024-01-01"), datetime.date(2024, 1, 1))
        with self.assertRaises(ValueError):
            policy(None, False, day, "not-a-date")

    def test_scd2_unchanged_replay_update_delete_and_history_retention(self):
        source = {"MANDT": "100", "LIFNR": "V1", "NAME1": "Old"}
        spark = Spark({"silver.lfa1": Rows([source])})
        module = definitions(NOTEBOOKS[0], spark)
        load, config = module["scd2_load"], module["DIM_CONFIG"]["vendor"]
        load("vendor", config)
        first = spark.table("dim.vendor").rows[0]
        self.assertEqual(first["EFFECTIVE_FROM"], datetime.date(1900, 1, 1))
        spark.tables["silver.lfa1"] = Rows([{**source, "ROW_HASH": "different", "BATCH_RUN_ID": "later"}], spark=spark)
        load("vendor", config)
        self.assertEqual(len(spark.merges), 1, "Unchanged hashes must not write new SCD2 versions")
        spark.tables["silver.lfa1"] = Rows([{**source, "NAME1": "New"}], spark=spark)
        load("vendor", config)
        history = spark.table("dim.vendor").rows
        self.assertEqual(len(history), 2)
        self.assertEqual(sum(row["IS_CURRENT"] for row in history), 1)
        self.assertEqual(history[0]["DIMENSION_SK"], first["DIMENSION_SK"])
        self.assertEqual(history[0]["EFFECTIVE_TO"], NOW.date() - datetime.timedelta(days=1))
        load("vendor", config)
        self.assertEqual(len(spark.merges), 2, "Same-run replay must not churn versions")
        spark.tables["silver.lfa1"] = Rows([], list(source), spark)
        load("vendor", config)
        self.assertEqual(len(spark.table("dim.vendor").rows), 2, "Removed masters must retain historical SKs")
        self.assertFalse(any(row["IS_CURRENT"] for row in spark.table("dim.vendor").rows))
        load("vendor", config)
        self.assertEqual(len(spark.merges), 3, "Deleted-source replay must be a no-op")

    def test_same_day_corrections_and_reappearance_reuse_existing_daily_sk(self):
        source = {"MANDT": "100", "LIFNR": "V1", "NAME1": "Old"}
        spark = Spark({"silver.lfa1": Rows([source])})
        module = definitions(NOTEBOOKS[0], spark)
        module["effective_dt"] = NOW.date().isoformat()
        load, config = module["scd2_load"], module["DIM_CONFIG"]["vendor"]
        load("vendor", config)
        key = spark.table("dim.vendor").rows[0]["DIMENSION_SK"]
        spark.tables["silver.lfa1"] = Rows([{**source, "NAME1": "Corrected"}], spark=spark)
        load("vendor", config)
        self.assertEqual(spark.table("dim.vendor").count(), 1)
        self.assertEqual(spark.table("dim.vendor").rows[0]["DIMENSION_SK"], key)
        spark.tables["silver.lfa1"] = Rows([], list(source), spark)
        load("vendor", config)
        spark.tables["silver.lfa1"] = Rows([source], spark=spark)
        load("vendor", config)
        self.assertEqual(spark.table("dim.vendor").count(), 1)
        self.assertTrue(spark.table("dim.vendor").rows[0]["IS_CURRENT"])

    def test_multiple_sap_intervals_and_historical_dimension_resolution(self):
        old_end = datetime.date(2022, 12, 31)
        source = [
            {"MANDT": "100", "KOKRS": "A", "KOSTL": "C1", "DATAB": datetime.date(2020, 1, 1), "DATBI": old_end, "VERAK": "Old"},
            {"MANDT": "100", "KOKRS": "A", "KOSTL": "C1", "DATAB": datetime.date(2023, 1, 1), "DATBI": datetime.date(9999, 12, 31), "VERAK": "New"},
        ]
        spark = Spark({"silver.csks": Rows(source)})
        module = definitions(NOTEBOOKS[0], spark)
        module["scd2_load"]("cost_center", module["DIM_CONFIG"]["cost_center"])
        self.assertEqual(spark.table("dim.cost_center").count(), 2)
        facts = Rows([{"KOSTL": "C1", "POSTING_DATE": datetime.date(2021, 1, 1)}, {"KOSTL": "C1", "POSTING_DATE": datetime.date(2024, 1, 1)}]).alias("s")
        resolve = definitions(NOTEBOOKS[1], spark)["dim_join"]
        result = resolve(facts, "cost_center", "cc", [Functions.col("s.KOSTL").eqNullSafe(Functions.col("cc.KOSTL"))])
        self.assertEqual([row["cc.VERAK"] for row in result.rows], ["Old", "New"])
        spark.tables["silver.csks"] = Rows([], list(source[0]), spark)
        module["scd2_load"]("cost_center", module["DIM_CONFIG"]["cost_center"])
        result = resolve(facts, "cost_center", "cc", [Functions.col("s.KOSTL").eqNullSafe(Functions.col("cc.KOSTL"))])
        self.assertEqual([row["cc.VERAK"] for row in result.rows], ["Old", "New"], "Historical lookup must include expired masters")

    def test_overlapping_sap_intervals_fail_instead_of_arbitrarily_collapsing(self):
        source = [
            {"MANDT": "100", "KOKRS": "A", "KOSTL": "C1", "DATAB": datetime.date(2020, 1, 1), "DATBI": datetime.date(2024, 1, 1)},
            {"MANDT": "100", "KOKRS": "A", "KOSTL": "C1", "DATAB": datetime.date(2023, 1, 1), "DATBI": datetime.date(9999, 12, 31)},
        ]
        module = definitions(NOTEBOOKS[0], Spark({"silver.csks": Rows(source)}))
        with self.assertRaisesRegex(ValueError, "overlapping SAP"):
            module["scd2_load"]("cost_center", module["DIM_CONFIG"]["cost_center"])

    def test_native_profit_centers_do_not_require_synthetic_cepc_bukrs(self):
        source = {"MANDT": "100", "KOKRS": "A", "PRCTR": "P1", "DATAB": datetime.date(2020, 1, 1), "DATBI": datetime.date(9999, 12, 31), "VERAK": "Owner", "KHINR": "D1", "SEGMENT": "Division"}
        spark = Spark({"silver.cepc": Rows([source])})
        module = definitions(NOTEBOOKS[0], spark)
        config = module["DIM_CONFIG"]["profit_center"]
        self.assertNotIn("BUKRS", config["keys"])
        module["scd2_load"]("profit_center", config)
        row = spark.table("dim.profit_center").rows[0]
        self.assertIn("BUKRS", row, "Preserve the existing semantic column without inventing a native source field")
        self.assertIsNone(row["BUKRS"])

    def test_journal_replay_uses_run_and_complete_typed_keys_not_source_batch(self):
        source = Rows([{"MANDT": "100", "YEAR": 2026, "DATBI": datetime.date(9999, 12, 31), "BATCH_RUN_ID": "later-run"}])
        raw = json.dumps({"MANDT": "100", "YEAR": 2026, "DATBI": "9999-12-31"})
        entry = {"WORK_ID": "work-1", "RUN_ID": "run-1", "TABLE_NAME": "csks", "KEY_JSON": raw, "CHANGE_TYPE": "DELETE"}
        spark = Spark({"audit.extraction_changes": Rows([entry, {**entry, "WORK_ID": "work-2"}, {**entry, "RUN_ID": "other"}])})
        function = definitions(NOTEBOOKS[1], spark)["read_change_keys"]
        result = function("csks", source, ["MANDT", "YEAR", "DATBI"])
        self.assertEqual(result.rows, [{"MANDT": "100", "YEAR": 2026, "DATBI": datetime.date(9999, 12, 31)}])
        self.assertEqual(function("csks", source, ["MANDT", "YEAR", "DATBI"], "INSERT").count(), 0)

    def test_journal_absence_incomplete_keys_and_invalid_types_fail(self):
        module = definitions(NOTEBOOKS[1])
        source = Rows([{"MANDT": "100", "YEAR": 2026}])
        with self.assertRaisesRegex(RuntimeError, "requires audit.extraction_changes"):
            module["read_change_keys"]("acdoca", source, ["MANDT", "YEAR"])
        spark = module["spark"]
        entry = {"WORK_ID": "1", "RUN_ID": "run-1", "TABLE_NAME": "acdoca", "CHANGE_TYPE": "DELETE"}
        for key_json, change_type in [('{"MANDT":"100"}', "DELETE"), ('{"MANDT":"100","YEAR":"wrong"}', "UPDATE"), ('{"MANDT":"100","YEAR":2026}', "BOGUS"), ("invalid-json", "DELETE")]:
            with self.subTest(key_json=key_json, change_type=change_type):
                spark.tables["audit.extraction_changes"] = Rows([{**entry, "KEY_JSON": key_json, "CHANGE_TYPE": change_type}], spark=spark)
                with self.assertRaises(ValueError):
                    module["read_change_keys"]("acdoca", source, ["MANDT", "YEAR"])

    def test_plan_expansion_preserves_all_16_periods_and_precise_decimals(self):
        source = {"RCLNT": "100", "RLDNR": "0L", "RHCUR": "ZAR", "RBUKRS": "1000", "RYEAR": 2026, "RACCT": "4000", "PRCTR": "P1", "VERSN": "F1"}
        source.update({f"TSL{i:02d}": Decimal("123456789012345678901.01") + i for i in range(1, 17)})
        module = definitions(NOTEBOOKS[2])
        expanded = module["plan_projection"](Rows([source]))
        self.assertEqual([row["FISCAL_PERIOD"] for row in expanded.rows], list(range(1, 17)))
        self.assertTrue(all(row["PLAN_SOURCE"] == "FAGLFLEXP" and row["SCENARIO"] == "PLAN" and row["COST_CENTER"] is None for row in expanded.rows))
        self.assertEqual(expanded.rows[-1]["PLAN_AMOUNT_LOCAL"], source["TSL16"])
        self.assertEqual(module["plan_projection"](Rows([source]), False).count(), 16)

    def test_plan_delete_scope_separates_clients_ledgers_sources_and_versions(self):
        source = {"RCLNT": "100", "RLDNR": "0L", "RBUKRS": "1000", "RYEAR": 2026, "RACCT": "4000", "PRCTR": "P1", "VERSN": "F1"}
        module = definitions(NOTEBOOKS[2])
        projection, subset, grain = module["plan_projection"], module["key_subset"], module["BUDGET_SOURCE_GRAIN"]
        removed = projection(Rows([source]), False)
        survivor = projection(Rows([{**source, "RCLNT": "200", "RLDNR": "2L"}]), False)
        self.assertEqual(subset(removed, survivor, grain, "left_anti").count(), 16, "Another client/ledger must not hide a confirmed removal")
        unrelated = projection(Rows([{**source, "VERSN": "F2"}, {**source, "RBUKRS": "2000"}, {**source, "PRCTR": "P2"}]), False)
        self.assertEqual(subset(unrelated, removed, grain).count(), 0)
        self.assertEqual(subset(removed, unrelated, grain, "left_anti").count(), 16)
        other_source = Rows([{**row, "PLAN_SOURCE": "BUDGET_FILE", "SCENARIO": "BUDGET"} for row in removed.rows])
        self.assertEqual(subset(other_source, removed, grain).count(), 0)

    def test_finance_scope_includes_header_fanout_but_not_other_clients_or_unchanged_lines(self):
        base = {"RCLNT": "100", "RLDNR": "0L", "RBUKRS": "1000", "GJAHR": 2026, "BELNR": "01", "DOCLN": "001", "BATCH_RUN_ID": "later"}
        current = [base, {**base, "BELNR": "02"}, {**base, "BELNR": "02", "DOCLN": "002"}, {**base, "BELNR": "03"}, {**base, "BELNR": "02", "RCLNT": "200"}, {**base, "BELNR": "02", "RLDNR": "2L"}]
        module = definitions(NOTEBOOKS[1])
        keys = module["ACDOCA_KEYS"]
        header = {"MANDT": "100", "BUKRS": "1000", "GJAHR": 2026, "BELNR": "02"}
        removed = {**base, "BELNR": "99"}
        entries = [
            {"RUN_ID": "run-1", "TABLE_NAME": table, "KEY_JSON": json.dumps(key), "CHANGE_TYPE": action, "WORK_ID": "w"}
            for table, key, action in [
                ("acdoca", {key: base[key] for key in keys}, "UPDATE"),
                ("acdoca", {key: base[key] for key in keys}, "DELETE"),  # Still present: must not delete.
                ("acdoca", {key: removed[key] for key in keys}, "DELETE"),
                ("bkpf", header, "DELETE"),  # Header removal refreshes its lines; it does not delete them.
            ]
        ]
        module["spark"].tables["audit.extraction_changes"] = Rows(entries)
        scoped, deleted = module["finance_scope"](Rows(current), Rows([header]), True)
        self.assertEqual(scoped.count(), 4)
        self.assertTrue(all(row["RCLNT"] == "100" and row["BELNR"] in {"01", "02"} for row in scoped.rows))
        self.assertEqual(deleted.select(*keys).rows, [{key: removed[key] for key in keys}])
        bootstrap, no_deletes = module["finance_scope"](Rows(current), Rows([header]), False)
        self.assertEqual(bootstrap.count(), len(current))
        self.assertEqual(no_deletes.count(), 0)
        module["run_id"] = ""
        full, no_deletes = module["finance_scope"](Rows(current), Rows([header]), True)
        self.assertEqual(full.count(), len(current))
        self.assertEqual(no_deletes.count(), 0)

    def test_bkpf_only_journal_refreshes_every_ledger_for_exact_historical_document(self):
        base = {"RCLNT": "100", "RLDNR": "0L", "RBUKRS": "1000", "GJAHR": 2020, "BELNR": "01", "DOCLN": "001", "BATCH_RUN_ID": "unchanged-acdoca"}
        current = [base, {**base, "RLDNR": "2L", "DOCLN": "002"}, {**base, "BELNR": "other"}, {**base, "GJAHR": 2021}, {**base, "RCLNT": "200"}, {**base, "RBUKRS": "2000"}]
        header = {"MANDT": "100", "BUKRS": "1000", "GJAHR": 2020, "BELNR": "01"}
        journal = {"RUN_ID": "run-1", "WORK_ID": "bkpf-only", "TABLE_NAME": "bkpf", "KEY_JSON": json.dumps(header), "CHANGE_TYPE": "UPDATE"}
        module = definitions(NOTEBOOKS[1], Spark({"audit.extraction_changes": Rows([journal])}))
        scoped, deleted = module["finance_scope"](Rows(current), Rows([header]), True)
        self.assertEqual(scoped.count(), 2)
        self.assertEqual({row["RLDNR"] for row in scoped.rows}, {"0L", "2L"})
        self.assertEqual(deleted.count(), 0)
        self.assertTrue(all(row["BATCH_RUN_ID"] == "unchanged-acdoca" for row in scoped.rows))

    def test_master_only_journal_repairs_matching_facts_without_using_current_dimension_versions(self):
        base = {"RCLNT": "100", "RLDNR": "0L", "RBUKRS": "1000", "GJAHR": 2020, "BELNR": "01", "DOCLN": "001"}
        header = {"MANDT": "100", "BUKRS": "1000", "GJAHR": 2020, "BELNR": "01"}
        journal = {"RUN_ID": "run-1", "WORK_ID": "master-only", "TABLE_NAME": "t001", "KEY_JSON": json.dumps({"MANDT": "100", "BUKRS": "1000"}), "CHANGE_TYPE": "UPDATE"}
        module = definitions(NOTEBOOKS[1], Spark({"audit.extraction_changes": Rows([journal]), "silver.t001": Rows([{"MANDT": "100", "BUKRS": "1000"}])}))
        scoped, deleted = module["finance_scope"](Rows([base, {**base, "GJAHR": 2027}, {**base, "RBUKRS": "2000"}]), Rows([header]), True)
        self.assertEqual(scoped.count(), 2, "Repair matching historic/future keys, then let the existing point-in-time join choose their versions")
        self.assertEqual(deleted.count(), 0, "A removed/changed master is never a source fact removal")

    def test_cepc_master_change_also_repairs_other_profit_centers_in_derived_division(self):
        high = datetime.date(9999, 12, 31)
        centers = [{"MANDT": "100", "KOKRS": "A", "PRCTR": code, "DATBI": high, "KHINR": division} for code, division in [("P1", "D1"), ("P2", "D1"), ("P3", "D2")]]
        changed = {"MANDT": "100", "KOKRS": "A", "PRCTR": "P1", "DATBI": high.isoformat()}
        journal = {"RUN_ID": "run-1", "WORK_ID": "cepc-only", "TABLE_NAME": "cepc", "KEY_JSON": json.dumps(changed), "CHANGE_TYPE": "UPDATE"}
        module = definitions(NOTEBOOKS[1], Spark({"audit.extraction_changes": Rows([journal]), "silver.cepc": Rows(centers), "dim.profit_center": Rows(centers)}))
        base = {"RCLNT": "100", "RLDNR": "0L", "RBUKRS": "1000", "GJAHR": 2027, "BELNR": "01", "DOCLN": "001", "KOKRS": "A"}
        rows = Rows([{**base, "PRCTR": code, "DOCLN": str(i)} for i, code in enumerate(["P1", "P2", "P3"])])
        affected = module["master_scope_keys"](rows, module["ACDOCA_KEYS"])
        self.assertEqual({row["DOCLN"] for row in affected.rows}, {"0", "1"})

    def test_master_only_journal_repairs_planning_grains_without_deleting_source_plans(self):
        key = {"RCLNT": "100", "RLDNR": "0L", "RBUKRS": "2000", "RYEAR": 2027, "RACCT": "4000", "PRCTR": "P1", "VERSN": "F1"}
        plan = {**key, "RHCUR": "USD", **{f"TSL{i:02d}": Decimal("12.34") for i in range(1, 17)}}
        base = {"COMPANY_CODE": "1000", "GL_ACCOUNT": "4000", "COST_CENTER": None, "PROFIT_CENTER": "P1", "FISCAL_PERIOD": 1, "PLAN_VERSION": None, "BUDGET_AMOUNT_LOCAL": Decimal("1.00"), "FORECAST_AMOUNT_LOCAL": None}
        budget = Rows([{**base, "FISCAL_YEAR": 2020}, {**base, "FISCAL_YEAR": 2027}])
        journal = {"RUN_ID": "run-1", "WORK_ID": "master-only", "TABLE_NAME": "t001", "KEY_JSON": json.dumps({"MANDT": "100", "BUKRS": "1000"}), "CHANGE_TYPE": "UPDATE"}
        module = definitions(NOTEBOOKS[2], Spark({"audit.extraction_changes": Rows([journal]), "silver.t001": Rows([{"MANDT": "100", "BUKRS": "1000"}])}))
        scoped, removed = module["budget_scope"](budget, Rows([plan]), True)
        self.assertEqual(scoped.count(), 2)
        self.assertTrue(all(row["PLAN_SOURCE"] == "BUDGET_FILE" for row in scoped.rows))
        self.assertEqual(removed.count(), 0)

    def test_budget_scope_does_not_merge_a_surviving_different_ledger_and_bootstraps_without_a_journal(self):
        key = {"RCLNT": "100", "RLDNR": "0L", "RBUKRS": "1000", "RYEAR": 2026, "RACCT": "4000", "PRCTR": "P1", "VERSN": "F1"}
        current = {**key, "RCLNT": "200", "RLDNR": "2L", "RHCUR": "USD", **{f"TSL{i:02d}": Decimal("12.34") for i in range(1, 17)}}
        budget = Rows([{"COMPANY_CODE": "9999", "GL_ACCOUNT": "4000", "COST_CENTER": None, "PROFIT_CENTER": "P2", "FISCAL_YEAR": 2026, "FISCAL_PERIOD": 1, "PLAN_VERSION": None, "BUDGET_AMOUNT_LOCAL": Decimal("1.00"), "FORECAST_AMOUNT_LOCAL": None}])
        module = definitions(NOTEBOOKS[2])
        bootstrap, removed = module["budget_scope"](budget, Rows([current]), False)
        self.assertEqual(bootstrap.count(), 17)
        self.assertEqual(removed.count(), 0)
        module["spark"].tables["audit.extraction_changes"] = Rows([{"RUN_ID": "run-1", "TABLE_NAME": "faglflexp", "KEY_JSON": json.dumps(key), "CHANGE_TYPE": "DELETE", "WORK_ID": "w"}])
        scoped, removed = module["budget_scope"](budget, Rows([current]), True)
        self.assertEqual(scoped.count(), 0, "A different client/ledger is not an affected Gold identity")
        self.assertEqual(removed.count(), 16)
        module["run_id"] = ""
        full, removed = module["budget_scope"](budget, Rows([current]), True)
        self.assertEqual(full.count(), 17)
        self.assertEqual(removed.count(), 0)

    def test_fact_merge_only_deletes_confirmed_absent_ids_and_replay_avoids_timestamp_churn(self):
        target = "fact.finance"
        spark = Spark({target: Rows([{"SK": "alive", "AMOUNT": Decimal("10.00"), "GOLD_LOAD_TIMESTAMP": NOW}, {"SK": "removed", "AMOUNT": Decimal("20.00"), "GOLD_LOAD_TIMESTAMP": NOW}, {"SK": "untouched", "AMOUNT": Decimal("30.00"), "GOLD_LOAD_TIMESTAMP": NOW}])})
        module = definitions(NOTEBOOKS[1], spark)
        output = Rows([{"SK": "alive", "AMOUNT": Decimal("10.00"), "GOLD_LOAD_TIMESTAMP": NOW + datetime.timedelta(days=1)}], spark=spark)
        deletes = Rows([{"SK": "removed"}, {"SK": "alive"}, {"SK": "absent"}], spark=spark)
        module["merge_fact"](output, deletes, target, "SK")
        self.assertEqual([row["SK"] for row in spark.table(target).rows], ["alive", "untouched"])
        self.assertEqual(spark.table(target).rows[0]["GOLD_LOAD_TIMESTAMP"], NOW)
        module["merge_fact"](output, deletes, target, "SK")
        self.assertEqual(spark.table(target).count(), 2)
        self.assertEqual(spark.table(target).rows[0]["GOLD_LOAD_TIMESTAMP"], NOW)

    def test_duplicate_or_lost_fact_grains_raise_before_merge(self):
        for name in NOTEBOOKS[1:3]:
            check = definitions(name)["assert_fact_grain"]
            check(2, 2, 0, "test")
            for counts in [(2, 1, 0), (1, 1, 1)]:
                with self.assertRaisesRegex(RuntimeError, "grain violation"):
                    check(*counts, "test")

    def test_amount_reconciliation_is_exact_currency_scoped_and_null_aware(self):
        function = definitions(NOTEBOOKS[3])["amount_mismatch_count"]
        source = Rows([{"CUR": "ZAR", "AMT": Decimal("100.01")}, {"CUR": "USD", "AMT": Decimal("200.02")}])
        self.assertEqual(function(source, source, ["CUR"], "AMT", "AMT"), 0)
        swapped = Rows([{"CUR": "ZAR", "AMT": Decimal("200.02")}, {"CUR": "USD", "AMT": Decimal("100.01")}])
        self.assertEqual(function(source, swapped, ["CUR"], "AMT", "AMT"), 2, "Equal grand totals must not hide currency discrepancies")
        null = Rows([{"CUR": None, "AMT": None}])
        zero = Rows([{"CUR": None, "AMT": Decimal("0.00")}])
        self.assertEqual(function(null, zero, ["CUR"], "AMT", "AMT"), 1)
        self.assertEqual(function(source, source.limit(1), ["CUR"], "AMT", "AMT"), 1)

    def test_actual_amount_reconciliation_includes_document_line_keys(self):
        module = definitions(NOTEBOOKS[3])
        self.assertTrue(set(module["ACDOCA_KEYS"]).issubset(module["FINANCE_RECON_KEYS"]))
        base = {"RCLNT": "100", "RLDNR": "0L", "RBUKRS": "1000", "GJAHR": 2020, "BELNR": "01", "POPER": 1, "RKCUR": "ZAR"}
        source = Rows([{**base, "DOCLN": "001", "AMT": Decimal("100.01")}, {**base, "DOCLN": "002", "AMT": Decimal("200.02")}])
        swapped = Rows([{**base, "DOCLN": "001", "AMT": Decimal("200.02")}, {**base, "DOCLN": "002", "AMT": Decimal("100.01")}])
        self.assertEqual(module["amount_mismatch_count"](source, swapped, module["FINANCE_RECON_KEYS"] + ["RKCUR"], "AMT", "AMT"), 2)

    def test_budget_amount_reconciliation_compares_scalars_without_currencyless_sums(self):
        module = definitions(NOTEBOOKS[3])
        source = Rows([{"COMPANY_CODE": "1000", "AMT": Decimal("1.01")}, {"COMPANY_CODE": "2000", "AMT": Decimal("2.02")}])
        swapped = Rows([{"COMPANY_CODE": "1000", "AMT": Decimal("2.02")}, {"COMPANY_CODE": "2000", "AMT": Decimal("1.01")}])
        function = module["row_amount_mismatch_count"]
        self.assertEqual(function(source, source, ["COMPANY_CODE"], "AMT", "AMT"), 0)
        self.assertEqual(function(source, swapped, ["COMPANY_CODE"], "AMT", "AMT"), 2)
        self.assertEqual(function(source, source.limit(1), ["COMPANY_CODE"], "AMT", "AMT"), 1)

    def test_currency_contract_and_real_assertion_failures(self):
        module = definitions(NOTEBOOKS[3])
        measures = {source: currency for _, source, _, currency, _ in module["FINANCE_MEASURES"]}
        self.assertEqual(measures, {"HSL": "RHCUR", "KSL": "RKCUR", "TSL": "RTCUR", "WSL": "RWCUR", "MSL": None})
        module["assert_checks"]([("amounts", True, 0)])
        with self.assertRaisesRegex(RuntimeError, "amounts=2, keys=1"):
            module["assert_checks"]([("amounts", False, 2), ("keys", False, 1)])


@unittest.skipUnless(os.environ.get("GOLD_SPARK_TESTS") == "1", "Set GOLD_SPARK_TESTS=1 in the prepared local Spark/Delta image")
class GoldSparkAdapterTests(unittest.TestCase):
    """Opt-in actual Spark/Delta execution; never resolve/download dependencies.

    Entrypoint in the caller's prepared image:
      GOLD_SPARK_TESTS=1 python -m unittest discover -s deployment/tests -p test_gold_incremental.py -v
    The image must already contain compatible Spark, Delta Python, and Delta JVM jars.
    """

    @classmethod
    def setUpClass(cls):
        from pyspark.sql import SparkSession

        cls.warehouse = tempfile.TemporaryDirectory(prefix="gold-notebook-tests-")
        cls.spark = (
            SparkSession.builder.master("local[2]").appName("gold-notebook-adapter-tests")
            .config("spark.ui.enabled", "false")
            .config("spark.sql.shuffle.partitions", "2")
            .config("spark.sql.warehouse.dir", cls.warehouse.name)
            .config("spark.sql.extensions", "io.delta.sql.DeltaSparkSessionExtension")
            .config("spark.sql.catalog.spark_catalog", "org.apache.spark.sql.delta.catalog.DeltaCatalog")
            .getOrCreate()
        )
        cls.spark.sparkContext.setLogLevel("ERROR")
        for namespace in ["gold_test_slv", "gold_test_dim", "gold_test_audit", "gold_test_fct"]:
            cls.spark.sql(f"CREATE DATABASE IF NOT EXISTS {namespace}")

    @classmethod
    def tearDownClass(cls):
        cls.spark.stop()
        cls.warehouse.cleanup()

    def module(self, name):
        module = definitions(name, self.spark, native=True)
        module.update(SLV="gold_test_slv", DIM="gold_test_dim", AUD="gold_test_audit")
        return module

    def test_actual_additive_currency_migration_rekey_and_asof_budget_context(self):
        from delta.tables import DeltaTable
        from pyspark.sql import functions as F

        budget = self.module(NOTEBOOKS[2])
        company = self.spark.createDataFrame(
            [("100", "1000", "ZAR", datetime.date(1900, 1, 1), datetime.date(2025, 12, 31)),
             ("100", "1000", "USD", datetime.date(2026, 1, 1), datetime.date(9999, 12, 31)),
             ("200", "1000", "EUR", datetime.date(1900, 1, 1), datetime.date(9999, 12, 31))],
            "MANDT string, BUKRS string, WAERS string, EFFECTIVE_FROM date, EFFECTIVE_TO date",
        )
        company.write.format("delta").mode("overwrite").option("overwriteSchema", "true").saveAsTable("gold_test_dim.company")
        file_rows = self.spark.createDataFrame(
            [("1000", "4000", None, "P1", 2024, 1, None, Decimal("12.34"), Decimal("15.67")),
             ("1000", "4000", None, "P1", 2027, 1, None, Decimal("12.34"), Decimal("15.67"))],
            "COMPANY_CODE string, GL_ACCOUNT string, COST_CENTER string, PROFIT_CENTER string, FISCAL_YEAR int, FISCAL_PERIOD int, PLAN_VERSION string, BUDGET_AMOUNT_LOCAL decimal(23,2), FORECAST_AMOUNT_LOCAL decimal(23,2)",
        )
        current = budget["budget_projection"](file_rows)
        actual = current.orderBy("FISCAL_YEAR").select("RCLNT", "RLDNR", "RHCUR").collect()
        self.assertEqual([(row["RCLNT"], row["RLDNR"], row["RHCUR"]) for row in actual], [("100", "0L", "ZAR"), ("100", "0L", "USD")])

        out = (current.filter("FISCAL_YEAR = 2027").select(*budget["grain"], "PLAN_AMOUNT_LOCAL")
               .withColumn("GOLD_LOAD_TIMESTAMP", F.lit(NOW))
               .withColumn("BUDGET_FACT_SK", budget["business_key_hash"]([F.col(key) for key in budget["grain"]])))
        legacy = (out.select(*budget["LEGACY_BUDGET_GRAIN"], "PLAN_AMOUNT_LOCAL", "GOLD_LOAD_TIMESTAMP")
                  .withColumn("BUDGET_FACT_SK", budget["business_key_hash"]([F.col(key) for key in budget["LEGACY_BUDGET_GRAIN"]])))
        target = "gold_test_fct.budget_currency"
        legacy.write.format("delta").mode("overwrite").option("delta.enableChangeDataFeed", "true").saveAsTable(target)
        self.assertTrue(budget["budget_requires_migration"](self.spark.table(target)))
        actions = budget["prepare_budget_upserts"](out, self.spark.table(target), migrating=True)
        empty_ids = out.select("BUDGET_FACT_SK").limit(0)
        budget["merge_fact"](actions, empty_ids, target, "BUDGET_FACT_SK", additive_columns=("RCLNT", "RLDNR", "RHCUR"))
        self.assertEqual(self.spark.table(target).count(), 1)
        self.assertNotIn("_MATCH_SK", self.spark.table(target).columns)
        self.assertFalse(budget["budget_requires_migration"](self.spark.table(target)))
        self.assertEqual(DeltaTable.forName(self.spark, target).detail().first()["properties"]["delta.enableChangeDataFeed"], "true")
        replay = budget["prepare_budget_upserts"](out, self.spark.table(target))
        budget["merge_fact"](replay, empty_ids, target, "BUDGET_FACT_SK", additive_columns=("RCLNT", "RLDNR", "RHCUR"))
        replacement = (out.withColumn("RHCUR", F.lit("EUR"))
                       .withColumn("BUDGET_FACT_SK", budget["business_key_hash"]([F.col(key) for key in budget["grain"]])))
        rekey = budget["prepare_budget_upserts"](replacement, self.spark.table(target))
        budget["merge_fact"](rekey, empty_ids, target, "BUDGET_FACT_SK")
        self.assertEqual(self.spark.table(target).count(), 1)
        self.assertEqual(self.spark.table(target).first()["RHCUR"], "EUR")
        with self.assertRaisesRegex(RuntimeError, "zero or multiple"):
            budget["prepare_budget_upserts"](out.limit(0), legacy, migrating=True)
        other_client = (out.withColumn("RCLNT", F.lit("200")).withColumn("RLDNR", F.lit("2L"))
                        .withColumn("BUDGET_FACT_SK", budget["business_key_hash"]([F.col(key) for key in budget["grain"]])))
        with self.assertRaisesRegex(RuntimeError, "zero or multiple"):
            budget["prepare_budget_upserts"](out.unionByName(other_client), legacy, migrating=True)

        finance = self.module(NOTEBOOKS[1])
        source = self.spark.createDataFrame([("100", "0L", "1000", 2026, "01", "001", "USD")], "RCLNT string, RLDNR string, RBUKRS string, GJAHR int, BELNR string, DOCLN string, RTCUR string")
        legacy_finance = (source.select(*finance["ACDOCA_KEYS"])
                          .withColumn("FINANCE_TRANSACTION_SK", finance["business_key_hash"]([F.col(key) for key in finance["ACDOCA_KEYS"]]))
                          .withColumn("AMOUNT_TRANSACTION", F.lit(Decimal("12.34")).cast("decimal(23,2)")))
        finance["validate_finance_currency_backfill"](legacy_finance, source, source.select(*finance["ACDOCA_KEYS"]).limit(0))
        finance_target = "gold_test_fct.finance_currency"
        legacy_finance.write.format("delta").mode("overwrite").saveAsTable(finance_target)
        finance["merge_fact"](legacy_finance.withColumn("RTCUR", F.lit("USD")), legacy_finance.select("FINANCE_TRANSACTION_SK").limit(0),
                              finance_target, "FINANCE_TRANSACTION_SK", additive_columns=("RTCUR",))
        self.assertEqual(self.spark.table(finance_target).first()["RTCUR"], "USD")

    def test_actual_delta_scd2_no_churn_update_delete_and_fact_replay(self):
        from delta.tables import DeltaTable
        from pyspark.sql import functions as F

        module = self.module(NOTEBOOKS[0])
        source = self.spark.createDataFrame([("100", "V1", "Old", "technical-1")], "MANDT string, LIFNR string, NAME1 string, ROW_HASH string")
        source.write.format("delta").mode("overwrite").saveAsTable("gold_test_slv.lfa1")
        load, config = module["scd2_load"], module["DIM_CONFIG"]["vendor"]
        load("vendor", config)
        target = DeltaTable.forName(self.spark, "gold_test_dim.vendor")
        version = target.history(1).first()["version"]
        source.withColumn("ROW_HASH", F.lit("technical-2")).write.format("delta").mode("overwrite").saveAsTable("gold_test_slv.lfa1")
        load("vendor", config)
        self.assertEqual(target.history(1).first()["version"], version)
        source.withColumn("NAME1", F.lit("New")).write.format("delta").mode("overwrite").saveAsTable("gold_test_slv.lfa1")
        load("vendor", config)
        self.assertEqual(self.spark.table("gold_test_dim.vendor").count(), 2)
        version = target.history(1).first()["version"]
        load("vendor", config)
        self.assertEqual(target.history(1).first()["version"], version)
        source.limit(0).write.format("delta").mode("overwrite").saveAsTable("gold_test_slv.lfa1")
        load("vendor", config)
        self.assertEqual(self.spark.table("gold_test_dim.vendor").filter("IS_CURRENT").count(), 0)
        self.assertEqual(self.spark.table("gold_test_dim.vendor").count(), 2)

        intervals = self.spark.createDataFrame(
            [("100", "A", "P1", datetime.date(2020, 1, 1), datetime.date(2022, 12, 31)),
             ("100", "A", "P1", datetime.date(2023, 1, 1), datetime.date(9999, 12, 31))],
            "MANDT string, KOKRS string, PRCTR string, DATAB date, DATBI date",
        )
        intervals.write.format("delta").mode("overwrite").saveAsTable("gold_test_slv.cepc")
        load("profit_center", module["DIM_CONFIG"]["profit_center"])
        profit_center = self.spark.table("gold_test_dim.profit_center")
        self.assertEqual(profit_center.count(), 2)
        self.assertEqual(profit_center.filter("BUKRS IS NOT NULL").count(), 0)

        finance = self.module(NOTEBOOKS[1])
        fact = self.spark.createDataFrame([("keep", Decimal("10.00"), NOW), ("remove", Decimal("20.00"), NOW)], "SK string, AMOUNT decimal(23,2), GOLD_LOAD_TIMESTAMP timestamp")
        fact.write.format("delta").mode("overwrite").saveAsTable("gold_test_fct.finance")
        output = fact.filter("SK = 'keep'").withColumn("GOLD_LOAD_TIMESTAMP", F.lit(NOW + datetime.timedelta(days=1)))
        # Materialize a source independent of the target being merged.
        output = self.spark.createDataFrame(output.collect(), output.schema)
        removed = self.spark.createDataFrame([("remove",)], "SK string")
        for _ in range(2):
            finance["merge_fact"](output, removed, "gold_test_fct.finance", "SK")
        rows = self.spark.table("gold_test_fct.finance").collect()
        self.assertEqual([(row["SK"], row["GOLD_LOAD_TIMESTAMP"]) for row in rows], [("keep", NOW)])

    def test_actual_typed_journal_header_fanout_plan_expansion_and_currency_reconciliation(self):
        module = self.module(NOTEBOOKS[1])
        current = self.spark.createDataFrame([("100", "0L", "1000", 2026, "01", "001"), ("100", "0L", "1000", 2026, "02", "001"), ("100", "0L", "1000", 2026, "02", "002"), ("100", "2L", "1000", 2026, "02", "001"), ("200", "0L", "1000", 2026, "02", "001")], "RCLNT string, RLDNR string, RBUKRS string, GJAHR int, BELNR string, DOCLN string")
        header = self.spark.createDataFrame([("100", "1000", 2026, "02")], "MANDT string, BUKRS string, GJAHR int, BELNR string")
        header_key = {"MANDT": "100", "BUKRS": "1000", "GJAHR": 2026, "BELNR": "02"}
        removed_key = {"RCLNT": "100", "RLDNR": "0L", "RBUKRS": "1000", "GJAHR": 2026, "BELNR": "99", "DOCLN": "001"}
        journal = self.spark.createDataFrame([("w1", "run-1", "bkpf", json.dumps(header_key), "UPDATE"), ("w2", "run-1", "acdoca", json.dumps(removed_key), "DELETE")], "WORK_ID string, RUN_ID string, TABLE_NAME string, KEY_JSON string, CHANGE_TYPE string")
        journal.write.format("delta").mode("overwrite").saveAsTable("gold_test_audit.extraction_changes")
        scoped, deleted = module["finance_scope"](current, header, True)
        self.assertEqual(scoped.count(), 3)
        self.assertEqual(deleted.first()["GJAHR"], 2026)
        self.assertEqual(deleted.count(), 1)
        journal.filter("TABLE_NAME = 'bkpf'").write.format("delta").mode("overwrite").saveAsTable("gold_test_audit.extraction_changes")
        header_only, no_deletes = module["finance_scope"](current, header, True)
        self.assertEqual(header_only.count(), 3)
        self.assertEqual({row["RLDNR"] for row in header_only.select("RLDNR").distinct().collect()}, {"0L", "2L"})
        self.assertEqual(no_deletes.count(), 0)

        budget = self.module(NOTEBOOKS[2])
        plan = {"RCLNT": "100", "RLDNR": "0L", "RHCUR": "ZAR", "RBUKRS": "1000", "RYEAR": 2026, "RACCT": "4000", "PRCTR": "P1", "VERSN": "F1"}
        plan.update({f"TSL{i:02d}": Decimal("123456789012345678901.01") for i in range(1, 17)})
        schema = "RCLNT string, RLDNR string, RHCUR string, RBUKRS string, RYEAR int, RACCT string, PRCTR string, VERSN string, " + ", ".join(f"TSL{i:02d} decimal(23,2)" for i in range(1, 17))
        expanded = budget["plan_projection"](self.spark.createDataFrame([plan], schema))
        self.assertEqual(expanded.count(), 16)
        self.assertEqual(expanded.filter("FISCAL_PERIOD = 16").first()["PLAN_AMOUNT_LOCAL"], plan["TSL16"])
        validation = self.module(NOTEBOOKS[3])
        expected = self.spark.createDataFrame([("ZAR", Decimal("100.01")), ("USD", Decimal("200.02"))], "CUR string, AMT decimal(23,2)")
        actual = self.spark.createDataFrame([("ZAR", Decimal("200.02")), ("USD", Decimal("100.01"))], expected.schema)
        self.assertEqual(validation["amount_mismatch_count"](expected, actual, ["CUR"], "AMT", "AMT"), 2)


if __name__ == "__main__":
    unittest.main()
