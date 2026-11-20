"""Executable source SQL, boundary, hashing and financial contract checks."""

from __future__ import annotations

import copy
import json
import sqlite3
import sys
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "artefacts" / "medallion"))

from sap_extraction import (  # noqa: E402
    EntryBoundary,
    ExtractionConfig,
    ExtractionMode,
    build_query,
    canonical_hash,
    compare_rows,
    normalize_row,
    plan_extractions,
    validate_envelope,
)


def configuration() -> ExtractionConfig:
    return ExtractionConfig.from_dict(json.loads(
        (ROOT / "artefacts" / "sap" / "factory-copy-contract.json").read_text(
            encoding="utf-8"
        )
    ))


class ExtractionTests(unittest.TestCase):
    def test_metadata_rejects_unsafe_identifiers_and_incomplete_keys(self) -> None:
        original = json.loads(
            (ROOT / "artefacts" / "sap" / "factory-copy-contract.json").read_text()
        )
        for field, value in [
            ("sourceObject", "SAPABAP1.ACDOCA; DELETE FROM BKPF"),
            ("businessKey", ["BELNR"]),
            ("extractionMode", "CDC"),
        ]:
            payload = copy.deepcopy(original)
            payload["tables"][0][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                ExtractionConfig.from_dict(payload)

    def test_entry_overlap_uses_source_timezone_and_fixed_upper_bound(self) -> None:
        boundary = EntryBoundary.window(
            "20261115", "000000",
            datetime(2026, 11, 16, 8, 0, 0, 999999, tzinfo=timezone.utc),
            overlap_hours=48, source_timezone="Africa/Johannesburg",
        )
        self.assertEqual(boundary.start, "20261113000000")
        self.assertEqual(boundary.end, "20261116100000")
        with self.assertRaises(ValueError):
            EntryBoundary.window(
                "20261117", "000000",
                datetime(2026, 11, 16, tzinfo=timezone.utc),
                overlap_hours=0, source_timezone="UTC",
            )

    def test_acdoca_query_discovers_late_postings_and_preserves_ledgers(self) -> None:
        config = configuration()
        table = config.table("ACDOCA")
        window = EntryBoundary("20261115090000", "20261115090000")
        query = build_query(table, ExtractionMode.DOCUMENT_INCREMENTAL, window)
        self.assertIn('A."RCLNT" = B."MANDT"', query)
        self.assertIn('A."GJAHR" = B."GJAHR"', query)
        self.assertNotIn('B."BUDAT" >=', query)
        connection = sqlite3.connect(":memory:")
        connection.execute("ATTACH DATABASE ':memory:' AS SAPABAP1")
        connection.execute(
            "CREATE TABLE SAPABAP1.BKPF "
            "(MANDT TEXT,BUKRS TEXT,GJAHR TEXT,BELNR TEXT,CPUDT TEXT,CPUTM TEXT)"
        )
        columns = ",".join(f'"{c}" TEXT' for c in table.columns)
        connection.execute(f"CREATE TABLE SAPABAP1.ACDOCA ({columns})")
        headers = [
            ("100", "1000", "2025", "1", "20261115", "090000"),
            ("100", "1000", "2026", "2", "20261115", "090000"),
            ("200", "1000", "2026", "2", "20261114", "090000"),
        ]
        connection.executemany("INSERT INTO SAPABAP1.BKPF VALUES (?,?,?,?,?,?)", headers)
        for client, year, document, ledger in [
            ("100", "2025", "1", "0L"),
            ("100", "2025", "1", "2L"),
            ("100", "2026", "2", "0L"),
            ("200", "2026", "2", "0L"),
        ]:
            row = {
                "RCLNT": client, "RLDNR": ledger, "RBUKRS": "1000",
                "GJAHR": year, "BELNR": document, "DOCLN": "000001",
                "BUDAT": "20251031",
            }
            connection.execute(
                f"INSERT INTO SAPABAP1.ACDOCA VALUES "
                f"({','.join('?' for _ in table.columns)})",
                [row.get(column) for column in table.columns],
            )
        selected = connection.execute(query).fetchall()
        self.assertEqual(len(selected), 3)
        self.assertEqual(
            {row[table.columns.index("RCLNT")] for row in selected}, {"100"}
        )
        connection.close()

    def test_partition_refresh_has_no_entry_predicate(self) -> None:
        table = configuration().table("ACDOCA")
        sql = build_query(
            table, ExtractionMode.PARTITION_REFRESH, None,
            {"GJAHR": "2025", "RBUKRS": "1000", "RLDNR": "0L"},
        )
        self.assertIn('A."GJAHR" = \'2025\'', sql)
        self.assertNotIn("INNER JOIN", sql)
        with self.assertRaises(ValueError):
            build_query(table, ExtractionMode.PARTITION_REFRESH, None, {})
        with self.assertRaises(ValueError):
            build_query(table, ExtractionMode.PARTITION_REFRESH, None, {"BELNR": "1"})
        with self.assertRaisesRegex(ValueError, "supported partition"):
            plan_extractions(
                configuration(), "invalid-refresh",
                datetime(2026, 11, 15, tzinfo=timezone.utc), "PARTITION_REFRESH", {},
                requested_partitions=[{"table": "T001", "partition": {}}],
            )

    def test_checkpoints_are_source_bound_but_not_reset_by_scheduling(self) -> None:
        original = json.loads(
            (ROOT / "artefacts/sap/factory-copy-contract.json").read_text()
        )
        base = ExtractionConfig.from_dict(original)
        operational = copy.deepcopy(original)
        operational.update(maxConcurrency=4, overlapHours=24, currentFiscalYear=2027)
        operational["reconciliation"]["CURRENT_FISCAL_YEAR"] = 2
        changed = ExtractionConfig.from_dict(operational)
        self.assertNotEqual(base.fingerprint, changed.fingerprint)
        self.assertEqual(
            base.checkpoint_scope(base.table("ACDOCA")),
            changed.checkpoint_scope(changed.table("ACDOCA")),
        )
        other_source = copy.deepcopy(original)
        other_source["sourceIdentity"]["name"] = "ANOTHER_SOURCE"
        other = ExtractionConfig.from_dict(other_source)
        self.assertNotEqual(
            base.checkpoint_scope(base.table("ACDOCA")),
            other.checkpoint_scope(other.table("ACDOCA")),
        )

    def test_discovery_mapping_must_match_the_configured_header_scope(self) -> None:
        original = json.loads((ROOT / "artefacts/sap/factory-copy-contract.json").read_text())
        mismatched = copy.deepcopy(original)
        mismatched["tables"][1]["sourceObject"] = "OTHER_SCHEMA.BKPF"
        with self.assertRaisesRegex(ValueError, "discovery mapping"):
            ExtractionConfig.from_dict(mismatched)
        narrowed = copy.deepcopy(original)
        narrowed["tables"][1]["filters"] = {"MANDT": ["100"]}
        with self.assertRaisesRegex(ValueError, "does not cover"):
            ExtractionConfig.from_dict(narrowed)

    def test_planner_initializes_once_and_keeps_empty_window_checkpoint(self) -> None:
        config = configuration()
        upper = datetime(2026, 11, 15, 9, tzinfo=timezone.utc)
        initial = plan_extractions(config, "initial", upper, "INITIALIZE", {})
        fiscal_jobs = [job for job in initial if job.table_name == "acdoca"]
        self.assertEqual(
            {job.partition["GJAHR"] for job in fiscal_jobs}, {"2025", "2026"}
        )
        self.assertTrue(all(job.advances_checkpoint for job in fiscal_jobs))
        checkpoint = {"acdoca": "20261115090000", "bkpf": "20261115090000"}
        jobs = plan_extractions(config, "next", upper, "INCREMENTAL", checkpoint)
        document_jobs = [job for job in jobs if job.mode == "DOCUMENT_INCREMENTAL"]
        self.assertEqual(len(document_jobs), 2)
        self.assertTrue(all(job.checkpoint_end == "20261115090000" for job in document_jobs))
        with self.assertRaisesRegex(ValueError, "initial"):
            plan_extractions(config, "bad", upper, "INCREMENTAL", {})

    def test_hashes_are_typed_unambiguous_and_normalized(self) -> None:
        columns = ("A", "B")
        self.assertEqual(
            canonical_hash({"A": Decimal("1.0"), "B": None}, columns),
            canonical_hash({"A": Decimal("1.00"), "B": None}, columns),
        )
        for other in [
            {"A": "1", "B": None},
            {"A": Decimal("1"), "B": "__NULL__"},
            {"A": "a|b", "B": "c"},
        ]:
            self.assertNotEqual(
                canonical_hash({"A": Decimal("1"), "B": None}, columns),
                canonical_hash(other, columns),
            )
        self.assertNotEqual(
            canonical_hash({"A": "a|b", "B": "c"}, columns),
            canonical_hash({"A": "a", "B": "b|c"}, columns),
        )
        self.assertNotEqual(
            canonical_hash({"A": Decimal("123456789012345678901234567890.01")}, ("A",)),
            canonical_hash({"A": Decimal("123456789012345678901234567890.02")}, ("A",)),
        )

    def test_snapshot_and_incremental_have_distinct_deletion_semantics(self) -> None:
        old = [{"K": "1", "V": Decimal("1.00")}, {"K": "2", "V": Decimal("2.00")}]
        new = [{"K": "1", "V": Decimal("1")}, {"K": "3", "V": Decimal("3")}]
        incremental = compare_rows(old, new, ("K",), ("K", "V"), authoritative=False)
        self.assertEqual(incremental, {"insert": [new[1]], "update": [], "delete": []})
        snapshot = compare_rows(old, new, ("K",), ("K", "V"), authoritative=True)
        self.assertEqual(snapshot["delete"], [old[1]])
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            compare_rows([], new + new, ("K",), ("K", "V"), authoritative=True)

    def test_envelope_rejects_truncation_and_currency_amount_corruption(self) -> None:
        table = configuration().table("ACDOCA")
        row = dict.fromkeys(table.columns)
        row.update(
            RCLNT="100", RLDNR="0L", RBUKRS="1000", GJAHR="2026",
            BELNR="1", DOCLN="000001", RACCT="0000500100",
            POPER="11", BUDAT="20261115", DRCRK="S",
            RHCUR="ZAR", RKCUR="USD", RTCUR="EUR", RWCUR="EUR",
            HSL="1234567890123456789.12", KSL="1.23", TSL="2.34", WSL="2.34",
        )
        data = {**row, "__ROW_KIND": "DATA", "__ROW_COUNT": "1"}
        control = {**row, "__ROW_KIND": "CONTROL", "__ROW_COUNT": "1"}
        total = {
            **dict.fromkeys(table.columns), "__ROW_KIND": "TOTAL", "__ROW_COUNT": "1",
        }
        self.assertEqual(validate_envelope(table, [data, control, total])["source_rows"], 1)
        with self.assertRaisesRegex(ValueError, "reconcil"):
            validate_envelope(table, [data, total])
        broken = {**data, "HSL": "1234567890123456789.13"}
        with self.assertRaisesRegex(ValueError, "reconcil"):
            validate_envelope(table, [broken, control, total])
        empty = {**total, "__ROW_COUNT": "0"}
        self.assertEqual(validate_envelope(table, [empty])["source_rows"], 0)

    def test_scalar_plan_controls_preserve_nullable_forecasts(self) -> None:
        table = configuration().table("BUDGET")
        row = dict.fromkeys(table.columns)
        row.update(
            COMPANY_CODE="1000", GL_ACCOUNT="0000500100", COST_CENTER="C1",
            PROFIT_CENTER="P1", FISCAL_YEAR="2026", FISCAL_PERIOD="1",
            PLAN_VERSION="V1", BUDGET_AMOUNT_LOCAL="1234567890123456789.12",
        )
        data = {**row, "__ROW_KIND": "DATA", "__ROW_COUNT": "1"}
        control = {**row, "__ROW_KIND": "CONTROL", "__ROW_COUNT": "1"}
        total = {
            **dict.fromkeys(table.columns), "__ROW_KIND": "TOTAL", "__ROW_COUNT": "1",
        }
        self.assertEqual(validate_envelope(table, [data, control, total])["source_rows"], 1)
        with self.assertRaisesRegex(ValueError, "financial reconciliation"):
            validate_envelope(
                table, [{**data, "BUDGET_AMOUNT_LOCAL": "1234567890123456789.13"}, control, total]
            )
        with self.assertRaisesRegex(ValueError, "null amount reconciliation"):
            validate_envelope(table, [{**data, "FORECAST_AMOUNT_LOCAL": "0.00"}, control, total])

    def test_existing_synthetic_sources_conform_to_shared_keys_and_types(self) -> None:
        sys.path.insert(0, str(ROOT / "artefacts" / "sap"))
        from synthetic_scenarios import load_seed_rows
        source = load_seed_rows(ROOT / "artefacts" / "sap_synthetic_data" / "sap")
        for table in configuration().tables:
            with self.subTest(table=table.name):
                keys = set()
                for raw in source[table.name]:
                    row = normalize_row(table, raw)
                    key = tuple(row[column] for column in table.keys)
                    self.assertNotIn(key, keys, table.name)
                    keys.add(key)


if __name__ == "__main__":
    unittest.main()
