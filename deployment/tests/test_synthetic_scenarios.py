"""Offline unittest coverage for the synthetic SAP Finance source, not extraction."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from collections import defaultdict
from contextlib import redirect_stdout
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Context, Decimal, ROUND_DOWN, localcontext
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from artefacts.sap import load_synthetic_data as loader
from artefacts.sap.synthetic_scenarios import (
    AMOUNT_CURRENCY_PAIRS,
    LEGACY_ADDITIONS,
    SCENARIOS,
    SPECS_BY_NAME,
    TABLE_SPECS,
    Row,
    SourceRows,
    TableSpec,
    apply_scenario,
    load_seed_rows,
    normalize_value,
    read_seed_table,
)

SOURCE_ROOT = ROOT / "artefacts" / "sap_synthetic_data" / "sap"
ENTERED_AT = datetime(2026, 9, 30, 12, 34, 56, 987654, tzinfo=timezone.utc)


def key_for(table: str, row: Row) -> tuple[object, ...]:
    return tuple(row[column] for column in SPECS_BY_NAME[table].keys)


def header_key(row: Row, *, line: bool = False) -> tuple[object, ...]:
    columns = ("RCLNT", "RBUKRS", "GJAHR", "BELNR") if line else SPECS_BY_NAME["BKPF"].keys
    return tuple(row[column] for column in columns)


def new_headers(before: SourceRows, after: SourceRows) -> list[Row]:
    old = {key_for("BKPF", row) for row in before["BKPF"]}
    return [row for row in after["BKPF"] if key_for("BKPF", row) not in old]


def write_table(root: Path, spec: TableSpec, rows: list[Row], columns: tuple[str, ...]) -> None:
    folder = root / spec.folder
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / f"{spec.folder}_20260928.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


class RecordingCursor:
    """Record SQL and values; never execute SQL or acquire credentials."""

    def __init__(self, connection: RecordingConnection) -> None:
        self.connection = connection
        self.sql = ""

    def execute(self, sql: str, *parameters: object) -> RecordingCursor:
        self.sql = sql
        self.connection.statements.append((sql, parameters))
        return self

    def fetchone(self) -> tuple[str, str | None]:
        return self.connection.target

    def fetchall(self) -> list[tuple[object, ...]]:
        for spec in TABLE_SPECS:
            if f"FROM {spec.target}" in self.sql:
                return [
                    tuple(row[column] for column in spec.columns)
                    for row in self.connection.source[spec.name]
                ]
        raise AssertionError(f"Unexpected fake query: {self.sql}")


class RecordingConnection:
    """A transaction/call recorder, not a substitute SQL Server implementation."""

    def __init__(self, source: SourceRows | None = None) -> None:
        self.source = source or {}
        self.target: tuple[str, str | None] = ("SYNTHETIC_TEST", "1")
        self.statements: list[tuple[str, tuple[object, ...]]] = []
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def cursor(self) -> RecordingCursor:
        return RecordingCursor(self)

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        self.closed = True


class SyntheticSourceTests(unittest.TestCase):
    """Prove scenario contracts using compact documents from the real seed."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.seed = load_seed_rows(ROOT)
        # Retain one real document per company/year and all referenced masters.
        chosen: dict[tuple[object, ...], Row] = {}
        for row in sorted(cls.seed["BKPF"], key=lambda item: key_for("BKPF", item)):
            chosen.setdefault((row["MANDT"], row["BUKRS"], row["GJAHR"]), row)
        keys = {header_key(row) for row in chosen.values()}
        cls.compact = {
            table: deepcopy(rows[:4] if table in {"BUDGET", "FAGLFLEXP"} else rows)
            for table, rows in cls.seed.items() if table not in {"ACDOCA", "BKPF"}
        }
        cls.compact["BKPF"] = deepcopy(list(chosen.values()))
        cls.compact["ACDOCA"] = deepcopy([
            row for row in cls.seed["ACDOCA"] if header_key(row, line=True) in keys
        ])

    def assert_source_invariants(
        self, source: SourceRows, *, expected_headerless: set[tuple[object, ...]] | None = None,
    ) -> None:
        for spec in TABLE_SPECS:
            keys = [key_for(spec.name, row) for row in source[spec.name]]
            self.assertEqual(len(keys), len(set(keys)), spec.name)
            self.assertTrue(all(None not in key for key in keys), spec.name)
            for row in source[spec.name]:
                self.assertTrue(set(spec.columns) <= row.keys(), spec.name)
                for column in spec.decimal_columns:
                    self.assertIsInstance(row[column], Decimal, (spec.name, column))
                    self.assertTrue(row[column].is_finite(), (spec.name, column))

        headers = {header_key(row): row for row in source["BKPF"]}
        companies = {key_for("T001", row): row for row in source["T001"]}
        accounts = {key_for("SKA1", row) for row in source["SKA1"]}
        vendors = {key_for("LFA1", row) for row in source["LFA1"]}
        customers = {key_for("KNA1", row) for row in source["KNA1"]}
        missing_headers: set[tuple[object, ...]] = set()
        balances: dict[tuple[object, ...], Decimal] = defaultdict(Decimal)
        for row in source["ACDOCA"]:
            key = header_key(row, line=True)
            if key not in headers:
                missing_headers.add(key)
            else:
                header = headers[key]
                self.assertEqual(header["BUDAT"], row["BUDAT"])
                self.assertEqual(header["BLDAT"], row["BLDAT"])
                self.assertEqual(int(header["MONAT"]), int(row["POPER"]))
            company = companies[(row["RCLNT"], row["RBUKRS"])]
            self.assertIn((row["RCLNT"], company["KTOPL"], row["RACCT"]), accounts)
            if row["LIFNR"]:
                self.assertIn((row["RCLNT"], row["LIFNR"]), vendors)
            if row["KUNNR"]:
                self.assertIn((row["RCLNT"], row["KUNNR"]), customers)
            for table, foreign, primary in (("CSKS", "RCNTR", "KOSTL"), ("CEPC", "PRCTR", "PRCTR")):
                if row[foreign]:
                    matches = [
                        master for master in source[table]
                        if (master["MANDT"], master["KOKRS"], master[primary])
                        == (row["RCLNT"], row["KOKRS"], row[foreign])
                        and master["DATAB"] <= row["BUDAT"] <= master["DATBI"]
                    ]
                    self.assertEqual(len(matches), 1, (table, row[foreign], row["BUDAT"]))
                    self.assertEqual(matches[0]["BUKRS"], row["RBUKRS"])
            document = tuple(row[column] for column in ("RCLNT", "RLDNR", "RBUKRS", "GJAHR", "BELNR"))
            for amount, currency in AMOUNT_CURRENCY_PAIRS:
                self.assertTrue(row[currency], (amount, currency))
                balances[(*document, amount, row[currency])] += row[amount]
            # The original seed includes negative postings. Amounts are already
            # signed: DRCRK must not be used to re-sign or "repair" those values.
            self.assertIn(row["DRCRK"], {"S", "H"})
        self.assertEqual(missing_headers, expected_headerless or set())
        self.assertFalse({key: value for key, value in balances.items() if value != Decimal("0.00")})
        legacy = {
            header_key(row): (row["STBLG"], row["STJAH"])
            for row in self.seed["BKPF"] if row["STBLG"]
        }
        for header in headers.values():
            self.assertIn((header["MANDT"], header["BUKRS"]), companies)
            if header["STBLG"]:
                # Preserve the exact pre-existing incomplete legacy links.
                # New scenario links must satisfy full reciprocal/year keys.
                if legacy.get(header_key(header)) == (header["STBLG"], header["STJAH"]):
                    continue
                reversal = headers[
                    (header["MANDT"], header["BUKRS"], header["STJAH"], header["STBLG"])
                ]
                self.assertEqual(reversal["STBLG"], header["BELNR"])
                self.assertEqual(reversal["STJAH"], header["GJAHR"])

    def test_seed_counts_full_keys_references_and_exact_balances(self) -> None:
        self.assertEqual(len(self.seed["BKPF"]), 6000)
        self.assertEqual(len(self.seed["ACDOCA"]), 12000)
        self.assertEqual({row["RCLNT"] for row in self.seed["ACDOCA"]}, {"100"})
        self.assertEqual({row["RLDNR"] for row in self.seed["ACDOCA"]}, {"0L"})
        self.assertEqual({row["GJAHR"] for row in self.seed["ACDOCA"]}, {"2025", "2026"})
        self.assertEqual({row["RBUKRS"] for row in self.seed["ACDOCA"]}, {"1000", "1100", "1200", "1300"})
        self.assert_source_invariants(self.seed)

    def test_original_csv_fields_and_financial_amounts_are_unchanged(self) -> None:
        expected = {
            "BKPF": "9ffe4b8c2cac8dfbd34a8b23206c1c8c85260c100c6a93f60bda4453277e2ff2",
            "ACDOCA": "2603de2a4be57e4807d050c8b2812be2167b6c5411bae1d0488d6c53d4d6baef",
        }
        for table, digest in expected.items():
            spec = SPECS_BY_NAME[table]
            path = SOURCE_ROOT / spec.folder / f"{spec.folder}_20260928.csv"
            with path.open(newline="", encoding="utf-8-sig") as handle:
                reader = csv.DictReader(handle)
                self.assertEqual(tuple(reader.fieldnames), spec.columns)
                old_columns = [column for column in spec.columns if column not in LEGACY_ADDITIONS[table]]
                old_rows = [{column: row[column] for column in old_columns} for row in reader]
            actual = hashlib.sha256(
                json.dumps(old_rows, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
            ).hexdigest()
            self.assertEqual(actual, digest, table)

    def test_seed_extensions_and_numc_normalization(self) -> None:
        for row in self.seed["BKPF"]:
            self.assertEqual((row["CPUDT"], row["CPUTM"], row["BSTAT"]),
                             ("20260928", "000000", None))
            if not row["STBLG"]:
                self.assertEqual(row["STJAH"], "0000")
            self.assertEqual(len(row["MONAT"]), 2)
        for row in self.seed["ACDOCA"]:
            self.assertEqual(row["RTCUR"], row["RWCUR"])
            self.assertEqual(len(row["POPER"]), 3)
            self.assertEqual(len(row["DOCLN"]), 6)
            self.assertEqual(len(row["RACCT"]), 10)
        self.assertEqual(SPECS_BY_NAME["ACDOCA"].keys,
                         ("RCLNT", "RLDNR", "RBUKRS", "GJAHR", "BELNR", "DOCLN"))
        self.assertEqual(SPECS_BY_NAME["CSKS"].keys, ("MANDT", "KOKRS", "KOSTL", "DATBI"))
        self.assertEqual(SPECS_BY_NAME["CEPC"].keys, ("MANDT", "KOKRS", "PRCTR", "DATBI"))

    def test_seed_legacy_reversal_exceptions_remain_explicit_not_fabricated(self) -> None:
        by_number: dict[tuple[object, ...], list[Row]] = defaultdict(list)
        for row in self.seed["BKPF"]:
            by_number[(row["MANDT"], row["BUKRS"], row["BELNR"])].append(row)
        links = [row for row in self.seed["BKPF"] if row["STBLG"]]
        self.assertEqual(len(links), 139)
        resolved = 0
        for row in links:
            targets = by_number.get((row["MANDT"], row["BUKRS"], row["STBLG"]), [])
            if len(targets) == 1:
                self.assertEqual(row["STJAH"], targets[0]["GJAHR"])
                resolved += 1
            else:
                self.assertEqual(row["STJAH"], "0000")
        self.assertEqual(resolved, 36)
        self.assertEqual(sum(row["STJAH"] == "0000" for row in links), 103)

    def test_all_scenarios_are_deterministic_isolated_and_balanced(self) -> None:
        original = deepcopy(self.compact)
        for name in SCENARIOS:
            with self.subTest(scenario=name):
                result = apply_scenario(self.compact, name, ENTERED_AT)
                self.assertEqual(result, apply_scenario(self.compact, name.replace("_", "-"), ENTERED_AT))
                self.assertEqual(self.compact, original)
                missing = {("100", "1000", "2026", "9900000012")} if name == "headerless_adjustment" else set()
                self.assert_source_invariants(result, expected_headerless=missing)

    def test_scenario_returns_deep_not_shallow_copy(self) -> None:
        source = deepcopy(self.compact)
        source["T001"][0]["nested_test_value"] = {"tags": ["original"]}
        result = apply_scenario(source, "new_documents", ENTERED_AT)
        result["T001"][0]["nested_test_value"]["tags"].append("changed")
        self.assertEqual(source["T001"][0]["nested_test_value"], {"tags": ["original"]})

    def test_new_documents_reuse_seed_amounts_and_master_relationships(self) -> None:
        result = apply_scenario(self.compact, "new_documents", ENTERED_AT)
        added = new_headers(self.compact, result)
        self.assertEqual(len(added), 2)
        seed_amounts = {tuple(row[column] for column in ("HSL", "KSL", "TSL", "WSL", "MSL"))
                        for row in self.compact["ACDOCA"]}
        for header in added:
            self.assertEqual((header["BUDAT"], header["CPUDT"], header["CPUTM"]),
                             ("20260930", "20260930", "123456"))
            lines = [row for row in result["ACDOCA"] if header_key(row, line=True) == header_key(header)]
            self.assertEqual(len(lines), 2)
            for line in lines:
                self.assertIn(tuple(line[column] for column in ("HSL", "KSL", "TSL", "WSL", "MSL")), seed_amounts)

    def test_late_current_month_uses_entry_not_posting_date(self) -> None:
        result = apply_scenario(self.compact, "late_current_month", ENTERED_AT)
        header, = new_headers(self.compact, result)
        self.assertEqual((header["BUDAT"], header["CPUDT"], header["GJAHR"], header["MONAT"]),
                         ("20260901", "20260930", "2026", "09"))
        with self.assertRaisesRegex(ValueError, "after the first"):
            apply_scenario(self.compact, "late_current_month", ENTERED_AT.replace(day=1))

    def test_prior_fiscal_year_posting_has_current_entry_time(self) -> None:
        result = apply_scenario(self.compact, "prior_fiscal_year", ENTERED_AT)
        header, = new_headers(self.compact, result)
        self.assertEqual((header["GJAHR"], header["BUDAT"], header["CPUDT"]),
                         ("2025", "20251231", "20260930"))

    def test_reversal_retains_original_and_offsets_exact_amounts(self) -> None:
        result = apply_scenario(self.compact, "reversal", ENTERED_AT)
        reversal, = new_headers(self.compact, result)
        original = next(row for row in result["BKPF"] if row["BELNR"] == reversal["STBLG"])
        original_key = header_key(original)
        self.assertEqual(original["STBLG"], reversal["BELNR"])
        self.assertEqual((original["STJAH"], reversal["STJAH"]), ("2026", "2025"))
        self.assertEqual((original["BSTAT"], reversal["BSTAT"]), (None, None))
        before_lines = [row for row in self.compact["ACDOCA"] if header_key(row, line=True) == original_key]
        after_original = [row for row in result["ACDOCA"] if header_key(row, line=True) == original_key]
        self.assertEqual(after_original, before_lines)
        reversed_lines = [row for row in result["ACDOCA"] if row["BELNR"] == reversal["BELNR"]]
        for before, after in zip(before_lines, reversed_lines, strict=True):
            for column in SPECS_BY_NAME["ACDOCA"].decimal_columns:
                self.assertEqual(after[column], -before[column])
            self.assertNotEqual(before["DRCRK"], after["DRCRK"])

    def test_multiple_ledgers_distinguish_all_amount_currency_pairs(self) -> None:
        result = apply_scenario(self.compact, "multiple_ledgers", ENTERED_AT)
        header, = new_headers(self.compact, result)
        lines = [row for row in result["ACDOCA"] if row["BELNR"] == header["BELNR"]]
        self.assertEqual({row["RLDNR"] for row in lines}, {"0L", "2L"})
        second = next(row for row in lines if row["RLDNR"] == "2L")
        self.assertEqual(tuple(second[column] for _, column in AMOUNT_CURRENCY_PAIRS),
                         ("ZAR", "USD", "EUR", "GBP"))
        self.assertNotEqual(second["TSL"], second["WSL"])
        self.assert_source_invariants(result)

    def test_cross_company_reuses_number_but_not_company_master_references(self) -> None:
        result = apply_scenario(self.compact, "cross_company", ENTERED_AT)
        added = new_headers(self.compact, result)
        self.assertEqual(len(added), 2)
        self.assertEqual(len({row["BELNR"] for row in added}), 1)
        self.assertEqual({row["BUKRS"] for row in added}, {"1000", "1100"})
        self.assert_source_invariants(result)

    def test_master_changes_change_attributes_not_keys_or_financials(self) -> None:
        result = apply_scenario(self.compact, "master_changes", ENTERED_AT)
        changed = {table for table in result if result[table] != self.compact[table]}
        self.assertEqual(changed, {"T001", "CSKS", "CEPC", "SKAT", "LFA1", "KNA1"})
        for table in changed:
            self.assertEqual({key_for(table, row) for row in result[table]},
                             {key_for(table, row) for row in self.compact[table]})
        self.assertEqual(result["ACDOCA"], self.compact["ACDOCA"])

    def test_validity_slices_are_contiguous_non_overlapping_and_fully_keyed(self) -> None:
        result = apply_scenario(self.compact, "validity_slices", ENTERED_AT)
        for table in ("CSKS", "CEPC"):
            self.assertEqual(len(result[table]), len(self.compact[table]) + 1)
            families: dict[tuple[object, ...], list[Row]] = defaultdict(list)
            for row in result[table]:
                families[key_for(table, row)[:-1]].append(row)
            split, = [sorted(rows, key=lambda row: row["DATAB"]) for rows in families.values() if len(rows) == 2]
            self.assertEqual((split[0]["DATBI"], split[1]["DATAB"], split[1]["DATBI"]),
                             ("20260929", "20260930", "99991231"))
            self.assertNotEqual(key_for(table, split[0]), key_for(table, split[1]))
            self.assertNotEqual(loader.business_key(SPECS_BY_NAME[table], split[0]),
                                loader.business_key(SPECS_BY_NAME[table], split[1]))
        self.assert_source_invariants(result)

    def test_reopened_period_changes_old_financials_without_new_entry_watermark(self) -> None:
        result = apply_scenario(self.compact, "reopened_historical_period", ENTERED_AT)
        self.assertEqual(result["BKPF"], self.compact["BKPF"])
        before = {key_for("ACDOCA", row): row for row in self.compact["ACDOCA"]}
        changed = [row for row in result["ACDOCA"] if row != before[key_for("ACDOCA", row)]]
        self.assertEqual(len(changed), 2)
        for row in changed:
            old = before[key_for("ACDOCA", row)]
            self.assertLess(row["BUDAT"], "20260901")
            self.assertEqual(row["BUDAT"], old["BUDAT"])
            self.assertEqual(abs(row["HSL"] - old["HSL"]), Decimal("17.25"))

    def test_source_row_removal_retains_header_and_removes_balanced_line_group(self) -> None:
        result = apply_scenario(self.compact, "source_row_removal", ENTERED_AT)
        self.assertEqual(result["BKPF"], self.compact["BKPF"])
        self.assertEqual(len(result["ACDOCA"]), len(self.compact["ACDOCA"]) - 2)
        line_headers = {header_key(row, line=True) for row in result["ACDOCA"]}
        self.assertEqual(sum(header_key(row) not in line_headers for row in result["BKPF"]), 1)
        self.assert_source_invariants(result)

    def test_source_document_removal_removes_header_and_all_its_rows(self) -> None:
        result = apply_scenario(self.compact, "source_document_removal", ENTERED_AT)
        self.assertEqual(len(result["BKPF"]), len(self.compact["BKPF"]) - 1)
        self.assertEqual(len(result["ACDOCA"]), len(self.compact["ACDOCA"]) - 2)
        self.assert_source_invariants(result)

    def test_headerless_adjustment_is_the_only_intentional_missing_header(self) -> None:
        result = apply_scenario(self.compact, "headerless_adjustment", ENTERED_AT)
        self.assertEqual(result["BKPF"], self.compact["BKPF"])
        headers = {header_key(row) for row in result["BKPF"]}
        headerless = [row for row in result["ACDOCA"] if header_key(row, line=True) not in headers]
        self.assertEqual(len(headerless), 2)
        self.assertEqual({row["BELNR"] for row in headerless}, {"9900000012"})
        self.assert_source_invariants(result, expected_headerless={("100", "1000", "2026", "9900000012")})

    def test_same_second_ties_require_full_document_checkpoint_key(self) -> None:
        result = apply_scenario(self.compact, "checkpoint_same_second", ENTERED_AT)
        added = new_headers(self.compact, result)
        self.assertEqual(len(added), 2)
        self.assertEqual({(row["CPUDT"], row["CPUTM"]) for row in added}, {("20260930", "123456")})
        checkpoints = sorted((row["CPUDT"], row["CPUTM"], *header_key(row)) for row in added)
        self.assertLess(checkpoints[0], checkpoints[1])
        self.assertEqual(sum(key > checkpoints[0] for key in checkpoints), 1)

    def test_entry_clock_is_explicit_utc_and_truncated_to_one_second(self) -> None:
        local = ENTERED_AT.astimezone(timezone(timedelta(hours=2)))
        self.assertEqual(apply_scenario(self.compact, "new_documents", local),
                         apply_scenario(self.compact, "new_documents", ENTERED_AT.replace(tzinfo=None)))

    def test_scenario_amounts_do_not_depend_on_ambient_decimal_context(self) -> None:
        names = ("reversal", "multiple_ledgers", "reopened_historical_period")
        expected = {name: apply_scenario(self.compact, name, ENTERED_AT) for name in names}
        with localcontext(Context(prec=6, rounding=ROUND_DOWN)):
            for name in names:
                with self.subTest(scenario=name):
                    self.assertEqual(apply_scenario(self.compact, name, ENTERED_AT), expected[name])

    def test_invalid_scenarios_and_inputs_fail_without_source_mutation(self) -> None:
        original = deepcopy(self.compact)
        for name in ("unknown", "recovery", "duplicate_extraction", "interruption"):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "Unknown"):
                apply_scenario(self.compact, name, ENTERED_AT)
        with self.assertRaisesRegex(ValueError, "datetime"):
            apply_scenario(self.compact, "new_documents", date(2026, 9, 30))
        with self.assertRaisesRegex(ValueError, "missing tables"):
            apply_scenario({}, "new_documents", ENTERED_AT)
        self.assertEqual(self.compact, original)

    def test_reserved_document_collision_is_rejected_not_silently_overwritten(self) -> None:
        result = apply_scenario(self.compact, "new_documents", ENTERED_AT)
        with self.assertRaisesRegex(ValueError, "already exists"):
            apply_scenario(result, "new_documents", ENTERED_AT)

    def test_legacy_csvs_normalize_to_current_columns_without_driver(self) -> None:
        for table in ("BKPF", "ACDOCA"):
            with self.subTest(table=table), tempfile.TemporaryDirectory() as temporary:
                spec = SPECS_BY_NAME[table]
                original = self.seed[table][:2]
                columns = tuple(column for column in spec.columns if column not in LEGACY_ADDITIONS[table])
                write_table(Path(temporary), spec, original, columns)
                self.assertEqual(read_seed_table(spec, Path(temporary)), original)

    def test_legacy_full_header_seed_resolves_only_unique_reversal_years(self) -> None:
        spec = SPECS_BY_NAME["BKPF"]
        columns = tuple(column for column in spec.columns if column not in LEGACY_ADDITIONS["BKPF"])
        with tempfile.TemporaryDirectory() as temporary:
            write_table(Path(temporary), spec, self.seed["BKPF"], columns)
            self.assertEqual(read_seed_table(spec, Path(temporary)), self.seed["BKPF"])

    def test_csv_normalization_rejects_schema_shape_missing_and_duplicate_keys(self) -> None:
        spec = SPECS_BY_NAME["ACDOCA"]
        row = deepcopy(self.compact["ACDOCA"][0])
        for label, rows, columns in (
            ("missing-column", [row], spec.columns[:-1]),
            ("reordered", [row], tuple(reversed(spec.columns))),
            ("duplicate-key", [row, row], spec.columns),
            ("missing-key", [{**row, "DOCLN": None}], spec.columns),
        ):
            with self.subTest(case=label), tempfile.TemporaryDirectory() as temporary:
                write_table(Path(temporary), spec, rows, columns)
                with self.assertRaises(ValueError):
                    read_seed_table(spec, Path(temporary))

    def test_decimal_normalization_preserves_precision_rejects_nonfinite_and_bad_values(self) -> None:
        spec = SPECS_BY_NAME["ACDOCA"]
        exact = "123456789012345678901.23"
        self.assertEqual(normalize_value(spec, "HSL", exact), Decimal(exact))
        self.assertIsNone(normalize_value(spec, "LIFNR", "  "))
        self.assertEqual(normalize_value(spec, "DOCLN", "1"), "000001")
        for invalid in ("NaN", "Infinity", "-Infinity", "not-an-amount"):
            with self.subTest(value=invalid), self.assertRaises(ValueError):
                normalize_value(spec, "HSL", invalid)

    def test_legacy_loader_initial_delta_and_budget_adjustments_are_preserved(self) -> None:
        spec = SPECS_BY_NAME["BUDGET"]
        initial, count = loader.load_rows(spec, SOURCE_ROOT, "initial", date(2026, 9, 30), 100, Decimal("1"))
        delta, delta_count = loader.load_rows(spec, SOURCE_ROOT, "delta", date(2026, 9, 30), 100, Decimal("1"))
        self.assertEqual((count, delta_count, len(initial), len(delta)), (6336,) * 4)
        forecast_index = spec.columns.index("FORECAST_AMOUNT_LOCAL")
        self.assertEqual(delta[0][forecast_index], (initial[0][forecast_index] * Decimal("1.01")).quantize(Decimal("0.01")))
        self.assertEqual((initial[0][-1], delta[0][-1]), (False, True))
        self.assertEqual(initial[0][-3], date(2026, 9, 28))
        self.assertEqual(delta[0][-3], date(2026, 9, 30))


class SyntheticLoaderTests(unittest.TestCase):
    """Validate generated SQL, CLI safety and orchestration without any SQL I/O."""

    @classmethod
    def setUpClass(cls) -> None:
        if not hasattr(SyntheticSourceTests, "compact"):
            SyntheticSourceTests.setUpClass()
        cls.source = SyntheticSourceTests.compact

    def scenario_args(self, *additional: str):
        return loader.create_parser().parse_args([
            "--mode", "scenario", "--scenario", "new_documents",
            "--entered-at", ENTERED_AT.isoformat(), "--synthetic-target", "SYNTHETIC_TEST",
            *additional,
        ])

    def test_pure_module_and_loader_import_and_help_without_pyodbc(self) -> None:
        result = subprocess.run(
            [sys.executable, "-B", "-c",
             "import sys; sys.modules['pyodbc'] = None; "
             "from artefacts.sap import synthetic_scenarios, load_synthetic_data; "
             "assert len(synthetic_scenarios.SCENARIOS) == 13; "
             "load_synthetic_data.create_parser().parse_args(['--help'])"],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--synthetic-target", result.stdout)

    def test_scenario_cli_rejects_missing_confirmation_and_partial_tables_before_connect(self) -> None:
        for arguments in (
            ["--mode", "scenario"],
            ["--scenario", "reversal"],
            ["--mode", "scenario", "--scenario", "reversal", "--entered-at", ENTERED_AT.isoformat()],
        ):
            with self.subTest(arguments=arguments), mock.patch.object(loader, "connect") as connect:
                with self.assertRaises(ValueError):
                    loader.run(loader.create_parser().parse_args(arguments))
                connect.assert_not_called()
        with mock.patch.object(loader, "connect") as connect, self.assertRaisesRegex(ValueError, "partially"):
            loader.run(self.scenario_args("--tables", "ACDOCA"))
        connect.assert_not_called()

    def test_unmarked_or_wrong_database_refuses_even_control_writes(self) -> None:
        for target in (("OTHER_DB", "1"), ("SYNTHETIC_TEST", None), ("SYNTHETIC_TEST", "0")):
            with self.subTest(target=target):
                connection = RecordingConnection()
                connection.target = target
                with mock.patch.object(loader, "connect", return_value=connection), self.assertRaises(ValueError):
                    loader.run(self.scenario_args())
                self.assertTrue(connection.closed)
                self.assertEqual(connection.commits, 0)
                self.assertEqual(len(connection.statements), 1)
                self.assertTrue(connection.statements[0][0].lstrip().startswith("SELECT"))

    def test_scenario_reads_current_simulator_business_columns_in_full_key_order(self) -> None:
        connection = RecordingConnection(self.source)
        self.assertEqual(loader.read_simulator_rows(connection), self.source)
        self.assertEqual(len(connection.statements), len(TABLE_SPECS))
        for spec, (sql, _) in zip(TABLE_SPECS, connection.statements, strict=True):
            self.assertIn(f"ORDER BY {loader.quote_list(spec.keys)}", sql)
            self.assertNotIn("SELECT *", sql)

    def test_scenario_upserts_only_changed_rows_using_original_staging_representation(self) -> None:
        connection = RecordingConnection(self.source)
        with mock.patch.object(loader, "merge_table") as merge:
            counts = loader.apply_simulator_scenario(connection, "new_documents", ENTERED_AT, "SYNTHETIC_TEST")
        self.assertEqual(counts, {"tables": 2, "selectedRows": 6, "deletedRows": 0})
        self.assertEqual([call.args[1].name for call in merge.call_args_list], ["ACDOCA", "BKPF"])
        for call in merge.call_args_list:
            spec, rows = call.args[1:]
            for row in rows:
                self.assertEqual(len(row), len(spec.columns) + 5)
                self.assertEqual(len(row[-4]), 32)
                self.assertEqual(row[-3], date(2026, 9, 30))
                self.assertFalse(row[-1])
        self.assertEqual((connection.commits, connection.rollbacks), (0, 0))

    def test_source_deletes_are_parameterized_and_scoped_by_all_keys(self) -> None:
        for name, expected in (("source_row_removal", 2), ("source_document_removal", 3)):
            with self.subTest(scenario=name):
                connection = RecordingConnection(self.source)
                with mock.patch.object(loader, "merge_table") as merge:
                    counts = loader.apply_simulator_scenario(connection, name, ENTERED_AT, "SYNTHETIC_TEST")
                merge.assert_not_called()
                deletes = [(sql, values) for sql, values in connection.statements if sql.startswith("DELETE")]
                self.assertEqual(len(deletes), expected)
                self.assertEqual(counts["deletedRows"], expected)
                for sql, values in deletes:
                    spec = SPECS_BY_NAME["BKPF" if ".[BKPF]" in sql else "ACDOCA"]
                    self.assertEqual(len(values), len(spec.keys))
                    self.assertEqual(sql.count("?"), len(spec.keys))
                    self.assertIn(" WHERE " + " AND ".join(f"[{key}] = ?" for key in spec.keys), sql)
                    self.assertNotIn("NOT MATCHED", sql)
                    self.assertNotIn("TRUNCATE", sql)

    def test_validity_slice_upserts_keep_both_datbi_keys(self) -> None:
        connection = RecordingConnection(self.source)
        with mock.patch.object(loader, "merge_table") as merge:
            counts = loader.apply_simulator_scenario(connection, "validity_slices", ENTERED_AT, "SYNTHETIC_TEST")
        self.assertEqual(counts, {"tables": 2, "selectedRows": 4, "deletedRows": 0})
        for call in merge.call_args_list:
            spec, rows = call.args[1:]
            self.assertIn(spec.name, ("CSKS", "CEPC"))
            self.assertEqual({row[spec.columns.index("DATBI")] for row in rows}, {"20260929", "99991231"})

    def test_merge_upgrades_legacy_staging_columns_and_serializes_decimals_exactly(self) -> None:
        for table in ("ACDOCA", "BKPF", "CSKS", "CEPC"):
            with self.subTest(table=table):
                spec = SPECS_BY_NAME[table]
                connection = RecordingConnection()
                row = self.source[table][0]
                staged = loader.prepare_rows(spec, [row], date(2026, 9, 30), "synthetic.csv")
                declarations = {
                    column: "decimal(23,3)" if column == "MSL" else "decimal(23,2)"
                    if column in spec.decimal_columns else "varchar(100)"
                    for column in spec.columns
                }
                with mock.patch.object(loader, "sql_type_declarations", return_value=declarations):
                    loader.merge_table(connection, spec, staged)
                sql = "\n".join(statement for statement, _ in connection.statements)
                for column in LEGACY_ADDITIONS.get(table, ()):
                    self.assertIn(f"COL_LENGTH(N'etl.stage_{table}', N'{column}') IS NULL", sql)
                    self.assertIn(f"ADD [{column}]", sql)
                for key in spec.keys:
                    self.assertIn(f"target.[{key}] = source.[{key}]", sql)
                payload, = [values[0] for statement, values in connection.statements if "OPENJSON" in statement]
                decoded, = json.loads(payload)
                for column in spec.decimal_columns:
                    self.assertEqual(decoded[column], format(row[column], "f"))
                self.assertNotIn("DROP TABLE", sql)

    def test_successful_scenario_commits_the_whole_diff_once_after_batch_start(self) -> None:
        connection = RecordingConnection(self.source)
        output = io.StringIO()
        with mock.patch.object(loader, "connect", return_value=connection), \
                mock.patch.object(loader, "merge_table"), redirect_stdout(output):
            result = loader.run(self.scenario_args("--business-date", "2040-01-01"))
        self.assertEqual(result, loader.EXIT_SUCCESS)
        summary = json.loads(output.getvalue())
        self.assertEqual((summary["mode"], summary["businessDate"], summary["selectedRows"]),
                         ("scenario", "2026-09-30", 6))
        self.assertEqual((connection.commits, connection.rollbacks, connection.closed), (2, 0, True))

    def test_failed_scenario_rolls_back_diff_and_records_failed_control_status(self) -> None:
        connection = RecordingConnection(self.source)
        with mock.patch.object(loader, "connect", return_value=connection), \
                mock.patch.object(loader, "merge_table", side_effect=[None, RuntimeError("offline test failure")]):
            with self.assertRaisesRegex(RuntimeError, "offline test failure"):
                loader.run(self.scenario_args())
        self.assertEqual((connection.commits, connection.rollbacks, connection.closed), (2, 1, True))
        statements = "\n".join(sql for sql, _ in connection.statements)
        self.assertIn("STATUS = 'FAILED'", statements)
        self.assertNotIn("STATUS = 'SUCCEEDED'", statements)

    def test_schema_declares_additive_guarded_simulator_migrations(self) -> None:
        sql = (ROOT / "artefacts" / "sap" / "schema.sql").read_text(encoding="utf-8")
        for spec in TABLE_SPECS:
            self.assertIn(f"CREATE TABLE {spec.schema}.{spec.name}", sql)
        self.assertIn("CREATE TABLE etl.load_batch", sql)
        for table, columns in LEGACY_ADDITIONS.items():
            for column in columns:
                self.assertIn(f"COL_LENGTH(N'SAPABAP1.{table}', N'{column}') IS NULL", sql)
        self.assertIn("PRIMARY KEY (MANDT, KOKRS, KOSTL, DATBI)", sql)
        self.assertIn("PRIMARY KEY (MANDT, KOKRS, PRCTR, DATBI)", sql)
        self.assertIn("ALTER COLUMN DATBI varchar(8) NOT NULL", sql)
        self.assertIn("SET DATBI = ''99991231''", sql)
        self.assertIn("HAVING COUNT_BIG(*) > 1", sql)
        self.assertIn("ROLLBACK TRANSACTION", sql)
        self.assertIn("'initial','delta','scenario'", sql)
        self.assertIn("SAP_FINANCE_SYNTHETIC_SIMULATOR", sql)
        self.assertNotIn("DROP TABLE", sql)


if __name__ == "__main__":
    unittest.main()
