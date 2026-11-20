"""Execute retained MLV SELECTs against mixed-currency/ledger fixtures."""

from __future__ import annotations

import json
import re
import sqlite3
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TABLES = ROOT / "artefacts" / "semantic" / "SAP Finance.SemanticModel" / "definition" / "tables"


def mlv_selects() -> dict[str, str]:
    notebook = json.loads(
        (ROOT / "artefacts" / "medallion" / "06_Gold_Materialized_Lake_Views.ipynb").read_text()
    )
    result = {}
    for cell in notebook["cells"]:
        source = "".join(cell.get("source", []))
        match = re.search(r"CREATE OR REPLACE MATERIALIZED LAKE VIEW mlv\.(\w+)", source)
        if match:
            result[match[1]] = source.split("\nAS\n", 1)[1]
    return result


class FinancialServingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("ATTACH DATABASE ':memory:' AS fct")
        self.connection.execute("""
            CREATE TABLE fct.finance_transaction (
                rclnt TEXT, rldnr TEXT, rhcur TEXT, rkcur TEXT, rtcur TEXT, rwcur TEXT,
                company_sk TEXT, division_sk TEXT, profit_center_sk TEXT,
                cost_center_sk TEXT, gl_account_sk TEXT, vendor_sk TEXT,
                gjahr INT, poper INT, amount_local INT, amount_group INT,
                amount_transaction INT, quantity INT, belnr TEXT, drcrk TEXT
            )
        """)
        self.connection.execute("""
            CREATE TABLE fct.budget (
                rclnt TEXT, rldnr TEXT, rhcur TEXT,
                company_sk TEXT, division_sk TEXT, profit_center_sk TEXT,
                cost_center_sk TEXT, gl_account_sk TEXT, fiscal_year INT,
                fiscal_period INT, fiscal_period_key INT, plan_source TEXT,
                scenario TEXT, plan_version TEXT,
                plan_amount_local INT, forecast_amount_local INT
            )
        """)
        for client, ledger, local, transaction, amount in [
            ("100", "0L", "ZAR", "EUR", 100),
            ("100", "0L", "ZAR", "GBP", 200),
            ("100", "2L", "ZAR", "EUR", 50),
            ("200", "0L", "USD", "EUR", 300),
            ("100", "0L", "USD", "EUR", 400),
        ]:
            self.connection.execute(
                "INSERT INTO fct.finance_transaction VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (client, ledger, local, "USD", transaction, "GBP",
                 "company", "division", "profit", "cost", "gl", "vendor",
                 2026, 0, amount, amount, amount, 1, str(amount), "S"),
            )
        for currency, amount in [("ZAR", 500), ("USD", 600)]:
            self.connection.execute(
                "INSERT INTO fct.budget VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("100", "0L", currency, "company", "division", "profit", "cost",
                 "gl", 2026, 0, 202600, "BUDGET_FILE", "BASE", "V1", amount, amount),
            )

    def tearDown(self) -> None:
        self.connection.close()

    def test_all_serving_queries_preserve_client_ledger_and_currency(self) -> None:
        for name, query in mlv_selects().items():
            with self.subTest(view=name):
                rows = [dict(row) for row in self.connection.execute(query)]
                target = [
                    row for row in rows
                    if row["client"] == "100" and row["ledger"] == "0L"
                    and row["currency_local"] == "ZAR"
                ]
                if name == "finance_actual_monthly":
                    self.assertEqual(len(target), 2)
                    self.assertEqual(
                        {row["currency_transaction"] for row in target}, {"EUR", "GBP"}
                    )
                    self.assertEqual(sum(row["actual_amount_local"] for row in target), 300)
                elif name == "vendor_spend_monthly":
                    self.assertEqual(len(target), 1)
                    self.assertEqual(target[0]["spend_amount_local"], 300)
                elif name == "gl_balance_monthly":
                    self.assertEqual(len(target), 1)
                    self.assertEqual(target[0]["net_movement_local"], 300)
                else:
                    actual = [row for row in target if row["plan_source"] == "ACTUAL"]
                    plan = [row for row in target if row["plan_source"] == "BUDGET_FILE"]
                    self.assertEqual(actual[0]["actual_amount_local"], 300)
                    self.assertEqual(plan[0]["plan_amount_local"], 500)

    def test_semantic_transaction_currency_maps_to_balance_currency(self) -> None:
        source = (TABLES / "Finance Transaction.tmdl").read_text()
        block = source.split("column 'Transaction Currency'", 1)[1].split("\n\tcolumn ", 1)[0]
        self.assertIn("sourceColumn: RTCUR", block)
        self.assertNotIn("sourceColumn: RWCUR", block)

    def test_new_semantic_scope_references_resolve_to_declared_columns(self) -> None:
        for filename in [
            "Finance Transaction", "Budget and Plan", "Finance Actual Monthly",
            "Vendor Spend Monthly", "GL Balance Monthly", "Plan vs Actual Monthly",
        ]:
            source = (TABLES / f"{filename}.tmdl").read_text()
            columns = set(re.findall(r"^\tcolumn '([^']+)'", source, re.MULTILINE))
            own_references = re.findall(re.escape("'" + filename + "'") + r"\[([^\]]+)\]", source)
            self.assertFalse(set(own_references) - columns, filename)
            self.assertIn("SAP Client", columns)
            self.assertIn("Ledger", columns)
            self.assertIn("Local Currency", columns)
            self.assertIn('ERROR("Select one SAP client, ledger', source)


if __name__ == "__main__":
    unittest.main()
