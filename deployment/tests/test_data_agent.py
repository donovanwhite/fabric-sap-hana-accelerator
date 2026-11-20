"""Verify deployed Data Agent knowledge and currency-safe query examples."""

from __future__ import annotations

import base64
import json
import re
import sqlite3
import unittest
from pathlib import Path
from unittest.mock import patch

from fabric_sap_deployer.definitions import data_agent_definition, extract_marker

ROOT = Path(__file__).resolve().parents[2]
MARKDOWN = ROOT / "artefacts" / "dataAgent" / "dataAgent.md"
SOURCE = "lakehouse-tables-lh_gld_finance"


def build_parts() -> dict[str, bytes]:
    definition = data_agent_definition(
        MARKDOWN,
        workspace_id="11111111-1111-1111-1111-111111111111",
        gold_lakehouse_id="22222222-2222-2222-2222-222222222222",
    )
    return {
        part["path"]: base64.b64decode(part["payload"])
        for part in definition["parts"]
    }


def table_nodes(source: dict) -> dict[tuple[str, str], dict]:
    tables = {}
    for grouping in source["elements"]:
        for schema in grouping["children"]:
            for table_group in schema["children"]:
                for table in table_group["children"]:
                    if schema["is_selected"] and table["is_selected"]:
                        tables[schema["display_name"], table["display_name"]] = table
    return tables


def sqlite_query(query: str) -> str:
    limit = re.search(r"\bSELECT TOP \((\d+)\)", query)
    if limit:
        query = query[:limit.start()] + query[limit.start():].replace(
            limit.group(0), "SELECT", 1
        )
        query = query.rstrip().removesuffix(";") + f" LIMIT {limit[1]}"
    return query.replace("COUNT_BIG(", "COUNT(")


class DataAgentDefinitionTests(unittest.TestCase):
    def test_draft_and_published_payloads_compile_all_authoritative_knowledge(self) -> None:
        parts = build_parts()
        text = MARKDOWN.read_text(encoding="utf-8")
        for stage in ("draft", "published"):
            prefix = f"Files/Config/{stage}/{SOURCE}"
            topics = parts[f"{prefix}/topics/topics.md"].decode("utf-8")
            self.assertEqual(topics, extract_marker(text, "TOPICS") + "\n")
            self.assertIn("RTCUR", topics)
            self.assertIn("PARTITION BY", topics)
            source = json.loads(parts[f"{prefix}/datasource.json"])
            self.assertEqual(source["dataSourceInstructions"], extract_marker(text, "SOURCE_INSTRUCTIONS"))
            self.assertNotIn("`AMOUNT_GROUP` is group currency", source["dataSourceInstructions"])
            self.assertIn("periods 0-16", source["dataSourceInstructions"])

    def test_new_gold_scope_columns_are_selected_and_described(self) -> None:
        required = {
            ("fct", "finance_transaction"): {"RTCUR"},
            ("fct", "budget"): {"RCLNT", "RLDNR", "RHCUR"},
            ("mlv", "finance_actual_monthly"): {
                "client", "ledger", "currency_local", "currency_group", "currency_transaction",
            },
            ("mlv", "vendor_spend_monthly"): {
                "client", "ledger", "currency_local", "currency_group",
            },
            ("mlv", "gl_balance_monthly"): {"client", "ledger", "currency_local"},
            ("mlv", "plan_vs_actual_monthly"): {"client", "ledger", "currency_local"},
        }
        parts = build_parts()
        for stage in ("draft", "published"):
            source = json.loads(parts[f"Files/Config/{stage}/{SOURCE}/datasource.json"])
            tables = table_nodes(source)
            self.assertEqual({schema for schema, _ in tables}, {"dim", "fct", "mlv"})
            for key, names in required.items():
                with self.subTest(stage=stage, table=key):
                    columns = {column["display_name"]: column for column in tables[key]["children"]}
                    self.assertTrue(names <= columns.keys(), names - columns.keys())
                    for name in names:
                        self.assertTrue(columns[name]["is_selected"])
                        self.assertEqual(columns[name]["data_type"], "varchar")
                        self.assertTrue(columns[name]["description"])
                        self.assertLessEqual(len(columns[name]["description"]), 500)

    def test_schema_descriptions_are_compiled_not_frozen_in_template(self) -> None:
        original_read = Path.read_text
        text = original_read(MARKDOWN, encoding="utf-8")
        old = "Signed amount in company-code local currency."
        replacement = "TEST authoritative local-currency description."
        changed = text.replace(old, replacement)
        self.assertNotEqual(text, changed)

        def read(path: Path, *args, **kwargs):
            return changed if path == MARKDOWN else original_read(path, *args, **kwargs)

        with patch.object(Path, "read_text", read):
            parts = build_parts()
        source = json.loads(parts[f"Files/Config/draft/{SOURCE}/datasource.json"])
        columns = table_nodes(source)["fct", "finance_transaction"]["children"]
        local = next(column for column in columns if column["display_name"] == "AMOUNT_LOCAL")
        self.assertTrue(local["description"].startswith(replacement))

    def test_deployable_fields_stay_within_platform_limits(self) -> None:
        parts = build_parts()
        for stage in ("draft", "published"):
            prefix = f"Files/Config/{stage}"
            instructions = json.loads(parts[f"{prefix}/stage_config.json"])["aiInstructions"]
            source = json.loads(parts[f"{prefix}/{SOURCE}/datasource.json"])
            examples = json.loads(parts[f"{prefix}/{SOURCE}/fewshots.json"])["fewShots"]
            self.assertLessEqual(len(instructions), 15000)
            self.assertLessEqual(len(source["userDescription"]), 800)
            self.assertLessEqual(len(source["dataSourceInstructions"]), 15000)
            self.assertLessEqual(len(parts[f"{prefix}/{SOURCE}/topics/topics.md"].decode()), 1000000)
            self.assertEqual(len(examples), 10)
            for example in examples:
                self.assertLessEqual(len(example["question"]), 500)
                self.assertLessEqual(len(example["query"]), 1000)
                self.assertNotIn("SELECT *", example["query"].upper())

    def test_unknown_schema_description_fails_instead_of_inventing_a_column(self) -> None:
        original_read = Path.read_text
        text = original_read(MARKDOWN, encoding="utf-8")
        changed = text.replace(
            "finance_transaction > Columns > RTCUR",
            "finance_transaction > Columns > UNVERIFIED_CURRENCY",
        )

        def read(path: Path, *args, **kwargs):
            return changed if path == MARKDOWN else original_read(path, *args, **kwargs)

        with patch.object(Path, "read_text", read), self.assertRaisesRegex(ValueError, "Unknown Data Agent schema"):
            build_parts()


class DataAgentQueryTests(unittest.TestCase):
    def setUp(self) -> None:
        parts = build_parts()
        examples = json.loads(parts[f"Files/Config/draft/{SOURCE}/fewshots.json"])["fewShots"]
        self.queries = [example["query"] for example in examples]
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("ATTACH DATABASE ':memory:' AS mlv")
        self.connection.execute("ATTACH DATABASE ':memory:' AS dim")
        self.connection.execute("ATTACH DATABASE ':memory:' AS fct")
        self.connection.execute("""
            CREATE TABLE dim.company (
                DIMENSION_SK TEXT, MANDT TEXT, BUKRS TEXT, BUTXT TEXT,
                ORT01 TEXT, LAND1 TEXT, WAERS TEXT, KTOPL TEXT, IS_CURRENT INT
            )
        """)
        self.connection.execute("""
            INSERT INTO dim.company VALUES (
                'company','100','1000','Demo','City','ZA','ZAR','DEMO',1
            )
        """)
        self.connection.execute("""
            CREATE TABLE dim.gl_account (DIMENSION_SK TEXT, SAKNR TEXT, TXT50 TEXT)
        """)
        self.connection.execute(
            "INSERT INTO dim.gl_account VALUES ('gl','0000400200','Account')"
        )
        self.connection.execute("""
            CREATE TABLE dim.vendor (DIMENSION_SK TEXT, LIFNR TEXT, NAME1 TEXT)
        """)
        self.connection.execute("INSERT INTO dim.vendor VALUES ('vendor','V1','Vendor')")
        self.connection.execute("""
            CREATE TABLE dim.cost_center (
                DIMENSION_SK TEXT, KOSTL TEXT, VERAK TEXT, KHINR TEXT
            )
        """)
        self.connection.execute("INSERT INTO dim.cost_center VALUES ('cost','C1','Owner','Division')")
        self.connection.execute("""
            CREATE TABLE dim.profit_center (DIMENSION_SK TEXT, PRCTR TEXT)
        """)
        self.connection.execute("INSERT INTO dim.profit_center VALUES ('profit','P1')")
        self.connection.execute("""
            CREATE TABLE dim.customer (DIMENSION_SK TEXT, KUNNR TEXT)
        """)
        self.connection.execute("INSERT INTO dim.customer VALUES ('customer','CU1')")
        self.connection.execute("""
            CREATE TABLE mlv.gl_balance_monthly (
                client TEXT, ledger TEXT, currency_local TEXT, fiscal_year INT,
                fiscal_period INT, fiscal_period_key INT, company_sk TEXT,
                gl_account_sk TEXT, debit_amount_local INT, credit_amount_local INT,
                net_movement_local INT
            )
        """)
        self.connection.execute("""
            CREATE TABLE mlv.plan_vs_actual_monthly (
                client TEXT, ledger TEXT, currency_local TEXT, fiscal_year INT,
                scenario TEXT, plan_version TEXT, actual_amount_local INT,
                plan_amount_local INT, gl_account_sk TEXT
            )
        """)
        self.connection.execute("""
            CREATE TABLE mlv.vendor_spend_monthly (
                client TEXT, ledger TEXT, currency_local TEXT, fiscal_year INT,
                vendor_sk TEXT, spend_amount_local INT, line_item_count INT
            )
        """)
        self.connection.execute("""
            CREATE TABLE fct.budget (
                RCLNT TEXT, RLDNR TEXT, RHCUR TEXT, FISCAL_YEAR INT,
                FISCAL_PERIOD INT, PLAN_SOURCE TEXT, PLAN_VERSION TEXT,
                PLAN_AMOUNT_LOCAL INT, FORECAST_AMOUNT_LOCAL INT, COMPANY_SK TEXT
            )
        """)
        self.connection.execute("""
            CREATE TABLE fct.finance_transaction (
                RCLNT TEXT, RLDNR TEXT, RHCUR TEXT, RBUKRS TEXT,
                GJAHR INT, BELNR TEXT, DOCLN TEXT, BLART TEXT, SGTXT TEXT,
                AMOUNT_LOCAL INT, REVERSAL_DOCUMENT TEXT,
                GL_ACCOUNT_SK TEXT, COST_CENTER_SK TEXT, PROFIT_CENTER_SK TEXT,
                VENDOR_SK TEXT, CUSTOMER_SK TEXT
            )
        """)
        for client, ledger, currency, first, second in [
            ("100", "0L", "ZAR", 10, 15),
            ("100", "2L", "ZAR", 100, 120),
            ("100", "0L", "USD", 1000, 1300),
            ("200", "0L", "ZAR", 10000, 14000),
        ]:
            for period, amount in [(1, first), (2, second)]:
                self.connection.execute(
                    "INSERT INTO mlv.gl_balance_monthly VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (client, ledger, currency, 2026, period, 202600 + period,
                     "company", "gl", amount, 0, amount),
                )
            for scenario, version, actual, plan in [
                ("ACTUAL", "ACTUAL", first, 0), ("PLAN", "F5", 0, second),
            ]:
                self.connection.execute(
                    "INSERT INTO mlv.plan_vs_actual_monthly VALUES (?,?,?,?,?,?,?,?,?)",
                    (client, ledger, currency, 2026, scenario, version, actual, plan, "gl"),
                )
            self.connection.execute(
                "INSERT INTO mlv.vendor_spend_monthly VALUES (?,?,?,?,?,?,?)",
                (client, ledger, currency, 2026, "vendor", second, 2),
            )
            for plan_source, version, forecast, plan in [
                ("FAGLFLEXP", "F5", None, first),
                ("BUDGET_FILE", "V1", second, first),
                ("BUDGET_FILE", "V1", None, 999999),
            ]:
                self.connection.execute(
                    "INSERT INTO fct.budget VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (client, ledger, currency, 2026, 13, plan_source, version,
                     plan, forecast, "company"),
                )
            self.connection.execute(
                "INSERT INTO fct.finance_transaction VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (client, ledger, currency, "1000", 2026, "1", "000001", "SA",
                 "Synthetic", first, "2", "gl", "cost", "profit", "vendor", "customer"),
            )

    def tearDown(self) -> None:
        self.connection.close()

    def test_executive_example_keeps_distinct_monetary_scopes(self) -> None:
        rows = [dict(row) for row in self.connection.execute(sqlite_query(self.queries[0]))]
        self.assertEqual(len(rows), 4)
        totals = {
            (row["client"], row["ledger"], row["currency_local"]): row["gross_local"]
            for row in rows
        }
        self.assertEqual(totals["100", "0L", "ZAR"], 25)
        self.assertEqual(totals["100", "2L", "ZAR"], 220)
        self.assertEqual(totals["100", "0L", "USD"], 2300)
        self.assertEqual(totals["200", "0L", "ZAR"], 24000)

    def test_prior_period_example_never_lags_across_currencies_or_ledgers(self) -> None:
        rows = [dict(row) for row in self.connection.execute(sqlite_query(self.queries[4]))]
        self.assertEqual(len(rows), 8)
        for row in rows:
            if row["fiscal_period"] == 1:
                self.assertIsNone(row["prior_local"])
        changes = {
            (row["client"], row["ledger"], row["currency_local"]): row["movement_change"]
            for row in rows if row["fiscal_period"] == 2
        }
        self.assertEqual(changes["100", "0L", "ZAR"], 5)
        self.assertEqual(changes["100", "2L", "ZAR"], 20)
        self.assertEqual(changes["100", "0L", "USD"], 300)
        self.assertEqual(changes["200", "0L", "ZAR"], 4000)

    def test_remaining_monetary_examples_return_separate_scope_groups(self) -> None:
        expected = {
            ("100", "0L", "ZAR"), ("100", "2L", "ZAR"),
            ("100", "0L", "USD"), ("200", "0L", "ZAR"),
        }
        for index in (1, 2, 3, 6, 7, 8):
            with self.subTest(example=index + 1):
                rows = [
                    dict(row) for row in self.connection.execute(sqlite_query(self.queries[index]))
                ]
                self.assertEqual(len(rows), 4)
                self.assertEqual({
                    (row["client"], row["ledger"], row["currency_local"]) for row in rows
                }, expected)
                if index == 1:
                    row = next(row for row in rows if row["client"] == "100"
                               and row["ledger"] == "0L" and row["currency_local"] == "ZAR")
                    self.assertEqual((row["actual"], row["plan_f5"], row["variance"]), (10, 15, -5))
                elif index == 2:
                    self.assertEqual(sum(row["budget_local"] for row in rows), 11110)
                    self.assertEqual(sum(row["forecast_local"] for row in rows), 15435)

    def test_detail_and_master_examples_remain_readable_with_client_identity(self) -> None:
        details = self.connection.execute(sqlite_query(self.queries[5])).fetchall()
        self.assertEqual(len(details), 4)
        self.assertIn("RCLNT", details[0].keys())
        companies = self.connection.execute(sqlite_query(self.queries[9])).fetchall()
        self.assertEqual(len(companies), 1)
        self.assertIn("MANDT", companies[0].keys())


if __name__ == "__main__":
    unittest.main()
