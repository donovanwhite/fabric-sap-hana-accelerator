#!/usr/bin/env python3
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT
"""Deterministic, offline source mutations for the SAP Finance simulator.

No database driver, credentials, clocks, randomness, or Fabric dependencies are
used here. Amounts are signed Decimals, never floats. ``load_seed_rows`` accepts
the repository root, the ``sap_synthetic_data`` directory, or its ``sap`` child.
``apply_scenario`` returns an independent full source snapshot, not an extract.

The fixture uses calendar fiscal years for scenario dates; this is not a model
of customer fiscal-year variants or installed-release SAP certification.
Legacy seed STBLG links include incomplete/non-reciprocal references; these are
preserved, not silently repaired or treated as complete reversal pairs.
"""

from __future__ import annotations

import csv
import re
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Context, Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
from pathlib import Path
from typing import TypeAlias

Row: TypeAlias = dict[str, object]
SourceRows: TypeAlias = dict[str, list[Row]]
EXTRACT_DATE_PATTERN = re.compile(r"_(\d{8})\.csv$", re.IGNORECASE)


@dataclass(frozen=True)
class TableSpec:
    """Describe the existing CSV/SQL simulator contract (not HANA DDL)."""

    name: str
    schema: str
    folder: str
    columns: tuple[str, ...]
    keys: tuple[str, ...]
    decimal_columns: tuple[str, ...] = ()
    numc_widths: tuple[tuple[str, int], ...] = ()

    @property
    def target(self) -> str:
        """Return the qualified SQL target."""
        return f"[{self.schema}].[{self.name}]"

    @property
    def numc_map(self) -> dict[str, int]:
        """Return configured ABAP NUMC widths."""
        return dict(self.numc_widths)


ACDOCA_COLUMNS = (
    "RCLNT", "RLDNR", "RBUKRS", "GJAHR", "POPER", "BELNR", "DOCLN",
    "RACCT", "RCNTR", "PRCTR", "KOKRS", "LIFNR", "KUNNR", "BUDAT",
    "BLDAT", "DRCRK", "BLART", "AWTYP", "SGTXT", "RHCUR", "RKCUR",
    "RTCUR", "RWCUR", "HSL", "KSL", "TSL", "WSL", "MSL",
)
FAGLFLEXP_COLUMNS = (
    "RCLNT", "RLDNR", "RBUKRS", "RYEAR", "RACCT", "PRCTR", "KOKRS",
    "VERSN", "CURTYPE", "RHCUR",
    *(f"TSL{period:02d}" for period in range(1, 17)),
)

TABLE_SPECS = (
    TableSpec(
        "ACDOCA", "SAPABAP1", "acdoca", ACDOCA_COLUMNS,
        ("RCLNT", "RLDNR", "RBUKRS", "GJAHR", "BELNR", "DOCLN"),
        ("HSL", "KSL", "TSL", "WSL", "MSL"),
        (("GJAHR", 4), ("POPER", 3), ("DOCLN", 6), ("RACCT", 10)),
    ),
    TableSpec(
        "BKPF", "SAPABAP1", "bkpf",
        ("MANDT", "BUKRS", "GJAHR", "BELNR", "MONAT", "BUDAT", "BLDAT",
         "CPUDT", "CPUTM", "BLART", "STBLG", "STJAH", "BSTAT", "BKTXT"),
        ("MANDT", "BUKRS", "GJAHR", "BELNR"),
        numc_widths=(("GJAHR", 4), ("MONAT", 2), ("CPUTM", 6), ("STJAH", 4)),
    ),
    TableSpec(
        "T001", "SAPABAP1", "t001",
        ("MANDT", "BUKRS", "BUTXT", "ORT01", "LAND1", "WAERS", "KTOPL"),
        ("MANDT", "BUKRS"),
    ),
    TableSpec(
        "CSKS", "SAPABAP1", "csks",
        ("MANDT", "KOKRS", "KOSTL", "BUKRS", "PRCTR", "VERAK", "KOSAR",
         "KHINR", "DATAB", "DATBI"),
        ("MANDT", "KOKRS", "KOSTL", "DATBI"),
    ),
    TableSpec(
        "CEPC", "SAPABAP1", "cepc",
        ("MANDT", "KOKRS", "PRCTR", "BUKRS", "VERAK", "KHINR", "SEGMENT",
         "DATAB", "DATBI"),
        ("MANDT", "KOKRS", "PRCTR", "DATBI"),
    ),
    TableSpec(
        "SKA1", "SAPABAP1", "ska1",
        ("MANDT", "KTOPL", "SAKNR", "XBILK", "GVTYP", "KTOKS"),
        ("MANDT", "KTOPL", "SAKNR"),
        numc_widths=(("SAKNR", 10),),
    ),
    TableSpec(
        "SKAT", "SAPABAP1", "skat",
        ("MANDT", "SPRAS", "KTOPL", "SAKNR", "TXT20", "TXT50"),
        ("MANDT", "SPRAS", "KTOPL", "SAKNR"),
        numc_widths=(("SAKNR", 10),),
    ),
    TableSpec(
        "LFA1", "SAPABAP1", "lfa1",
        ("MANDT", "LIFNR", "NAME1", "NAME2", "LAND1", "ORT01", "REGIO",
         "KTOKK", "STCD1"),
        ("MANDT", "LIFNR"),
    ),
    TableSpec(
        "KNA1", "SAPABAP1", "kna1",
        ("MANDT", "KUNNR", "NAME1", "NAME2", "LAND1", "ORT01", "REGIO",
         "KTOKD", "STCD1"),
        ("MANDT", "KUNNR"),
    ),
    TableSpec(
        "FAGLFLEXP", "SAPABAP1", "faglflexp", FAGLFLEXP_COLUMNS,
        ("RCLNT", "RLDNR", "RBUKRS", "RYEAR", "RACCT", "PRCTR", "VERSN"),
        tuple(f"TSL{period:02d}" for period in range(1, 17)),
        (("RYEAR", 4), ("RACCT", 10)),
    ),
    TableSpec(
        "BUDGET", "SAPDEMO", "budget",
        ("COMPANY_CODE", "GL_ACCOUNT", "COST_CENTER", "PROFIT_CENTER",
         "FISCAL_YEAR", "FISCAL_PERIOD", "BUDGET_AMOUNT_LOCAL",
         "FORECAST_AMOUNT_LOCAL", "PLAN_VERSION", "DATA_CLASSIFICATION"),
        ("COMPANY_CODE", "GL_ACCOUNT", "COST_CENTER", "PROFIT_CENTER",
         "FISCAL_YEAR", "FISCAL_PERIOD", "PLAN_VERSION"),
        ("BUDGET_AMOUNT_LOCAL", "FORECAST_AMOUNT_LOCAL"),
        (("GL_ACCOUNT", 10), ("FISCAL_YEAR", 4), ("FISCAL_PERIOD", 3)),
    ),
    TableSpec(
        "DIVISION_REFERENCE", "SAPDEMO", "division_reference",
        ("DIVISION_CODE", "DIVISION_NAME"),
        ("DIVISION_CODE",),
    ),
    TableSpec(
        "GL_ACCOUNT_REFERENCE", "SAPDEMO", "gl_account_reference",
        ("GL_ACCOUNT", "GL_ACCOUNT_NAME", "FINANCE_CATEGORY"),
        ("GL_ACCOUNT",),
        numc_widths=(("GL_ACCOUNT", 10),),
    ),
)

SPECS_BY_NAME = {spec.name: spec for spec in TABLE_SPECS}
LEGACY_ADDITIONS = {
    "ACDOCA": ("RTCUR",),
    "BKPF": ("CPUDT", "CPUTM", "STJAH", "BSTAT"),
}
AMOUNT_CURRENCY_PAIRS = (
    ("HSL", "RHCUR"), ("KSL", "RKCUR"), ("TSL", "RTCUR"), ("WSL", "RWCUR"),
)
SCENARIOS = (
    "new_documents",
    "late_current_month",
    "prior_fiscal_year",
    "reversal",
    "multiple_ledgers",
    "cross_company",
    "master_changes",
    "validity_slices",
    "reopened_historical_period",
    "source_row_removal",
    "source_document_removal",
    "headerless_adjustment",
    "checkpoint_same_second",
)


def source_file(spec: TableSpec, root: Path) -> Path:
    """Resolve the single CSV file for a table, as in the original loader."""
    files = sorted((root / spec.folder).glob("*.csv"))
    if len(files) != 1:
        raise ValueError(
            f"{spec.name}: expected one CSV under {root / spec.folder}, "
            f"found {len(files)}"
        )
    return files[0]


def filename_extract_date(path: Path) -> date:
    """Parse the source extract date from the existing snapshot file name."""
    match = EXTRACT_DATE_PATTERN.search(path.name)
    if not match:
        raise ValueError(f"Extract date missing from file name: {path.name}")
    return datetime.strptime(match.group(1), "%Y%m%d").date()


def normalize_value(spec: TableSpec, column: str, value: str) -> object:
    """Normalize blanks, NUMC fields and exact, finite decimal values."""
    value = value.strip()
    if not value:
        return None
    if column in spec.decimal_columns:
        try:
            amount = Decimal(value)
        except InvalidOperation as error:
            raise ValueError(f"{spec.name}.{column}: invalid decimal {value!r}") from error
        if not amount.is_finite():
            raise ValueError(f"{spec.name}.{column}: amount must be finite")
        return amount
    width = spec.numc_map.get(column)
    if width and value.isdigit():
        return value.zfill(width)
    return value


def read_seed_table(spec: TableSpec, root: Path) -> list[Row]:
    """Read one CSV, accepting only the known pre-refactor missing columns.

    Legacy entry timestamps default to the *snapshot* date at midnight, not
    BUDAT. This is a deterministic simulator backfill, not recovered SAP history.
    RTCUR defaults to RWCUR only for the existing equal-currency seed. Legacy
    STJAH is inferred only from a unique same-client/company reversal target;
    otherwise ``0000`` explicitly leaves the unknown year unresolved.
    """
    path = source_file(spec, root)
    optional = set(LEGACY_ADDITIONS.get(spec.name, ()))
    snapshot_date = filename_extract_date(path).strftime("%Y%m%d")
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        actual = tuple(reader.fieldnames or ())
        if (
            set(spec.columns) - set(actual) - optional
            or actual != tuple(column for column in spec.columns if column in actual)
        ):
            raise ValueError(
                f"{spec.name}: columns differ. Expected {spec.columns}; found {actual}"
            )
        rows: list[Row] = []
        seen: set[tuple[object, ...]] = set()
        for number, raw in enumerate(reader, start=2):
            if None in raw or any(value is None for value in raw.values()):
                raise ValueError(f"{spec.name}: malformed CSV row {number}")
            defaults = {
                "CPUDT": snapshot_date,
                "CPUTM": "000000",
                "STJAH": "0000",
                "BSTAT": "",
                "RTCUR": raw.get("RWCUR", ""),
            }
            row = {
                column: normalize_value(spec, column, raw.get(column, defaults.get(column, "")))
                for column in spec.columns
            }
            key = tuple(row[column] for column in spec.keys)
            if any(value is None for value in key) or key in seen:
                raise ValueError(f"{spec.name}: missing or duplicate key at CSV row {number}: {key}")
            seen.add(key)
            rows.append(row)
    if spec.name == "BKPF" and "STJAH" not in actual:
        years: dict[tuple[object, ...], list[object]] = {}
        for row in rows:
            years.setdefault((row["MANDT"], row["BUKRS"], row["BELNR"]), []).append(row["GJAHR"])
        for row in rows:
            targets = years.get((row["MANDT"], row["BUKRS"], row["STBLG"]), [])
            if row["STBLG"] and len(targets) == 1:
                row["STJAH"] = targets[0]
    return rows


def load_seed_rows(root: Path) -> SourceRows:
    """Load all existing seed tables with the same normalization as the loader."""
    for candidate in (root, root / "sap", root / "artefacts" / "sap_synthetic_data" / "sap"):
        if (candidate / "acdoca").is_dir():
            return {spec.name: read_seed_table(spec, candidate) for spec in TABLE_SPECS}
    raise ValueError(f"Cannot find the synthetic SAP CSV source under {root}")


def _header_key(row: Row, *, line: bool = False) -> tuple[object, ...]:
    fields = ("RCLNT", "RBUKRS", "GJAHR", "BELNR") if line else SPECS_BY_NAME["BKPF"].keys
    return tuple(row[field] for field in fields)


def _template(
    source: SourceRows, company: object | None = None, *, all_ledgers: bool = False,
) -> tuple[Row, list[Row]]:
    """Select a seed document deterministically, independent of source row order."""
    headers = {
        _header_key(row): row for row in source["BKPF"]
        if company is None or row["BUKRS"] == company
    }
    groups: dict[tuple[object, ...], list[Row]] = {}
    for row in source["ACDOCA"]:
        key = _header_key(row, line=True)
        if key in headers and row["RLDNR"] == "0L":
            groups.setdefault(key, []).append(row)
    if not groups:
        raise ValueError(f"Scenario requires a BKPF-backed 0L document for company {company}")
    key = min(groups)
    lines = (
        [row for row in source["ACDOCA"] if _header_key(row, line=True) == key]
        if all_ledgers else groups[key]
    )
    return headers[key], sorted(lines, key=lambda row: (str(row["RLDNR"]), str(row["DOCLN"])))


def _append_document(
    source: SourceRows,
    clock: datetime,
    slot: int,
    posting_date: date,
    label: str,
    *,
    company: object | None = None,
    all_ledgers: bool = False,
) -> tuple[Row, list[Row]]:
    """Copy, rather than regenerate, a complete existing balanced document."""
    header, lines = deepcopy(_template(source, company, all_ledgers=all_ledgers))
    header.update(
        BELNR=str(9_900_000_000 + slot),
        GJAHR=f"{posting_date.year:04d}",
        MONAT=f"{posting_date.month:02d}",
        BUDAT=f"{posting_date:%Y%m%d}",
        BLDAT=f"{posting_date:%Y%m%d}",
        CPUDT=f"{clock:%Y%m%d}",
        CPUTM=f"{clock:%H%M%S}",
        STBLG=None,
        STJAH="0000",
        BSTAT=None,
        BKTXT=f"SYNTHETIC SCENARIO {label}",
    )
    if any(_header_key(row) == _header_key(header) for row in source["BKPF"]):
        raise ValueError(f"Reserved scenario document already exists: {_header_key(header)}")
    for line in lines:
        line.update(
            BELNR=header["BELNR"], GJAHR=header["GJAHR"],
            POPER=f"{posting_date.month:03d}", BUDAT=header["BUDAT"],
            BLDAT=header["BLDAT"], SGTXT=header["BKTXT"],
        )
    existing = {
        tuple(row[key] for key in SPECS_BY_NAME["ACDOCA"].keys)
        for row in source["ACDOCA"]
    }
    if any(
        tuple(row[key] for key in SPECS_BY_NAME["ACDOCA"].keys) in existing
        for row in lines
    ):
        raise ValueError("Reserved scenario ACDOCA key already exists")
    source["BKPF"].append(header)
    source["ACDOCA"].extend(lines)
    return header, lines


def _split_validity(rows: list[Row], selected: Row, split: date) -> None:
    start = datetime.strptime(str(selected["DATAB"]), "%Y%m%d").date()
    end = datetime.strptime(str(selected["DATBI"]), "%Y%m%d").date()
    if not start < split <= end:
        raise ValueError("Validity split must fall after DATAB and on or before DATBI")
    successor = deepcopy(selected)
    selected["DATBI"] = f"{split - timedelta(days=1):%Y%m%d}"
    successor.update(DATAB=f"{split:%Y%m%d}", VERAK="SYNTHETIC_NEW_OWNER")
    rows.append(successor)


def apply_scenario(source: SourceRows, scenario: str, entered_at: datetime) -> SourceRows:
    """Return a deep-copied source snapshot after one named source mutation.

    Names in ``SCENARIOS`` also accept hyphens instead of underscores. Naive
    datetimes mean UTC; aware values are converted to UTC and floored to SAP's
    one-second CPUDT/CPUTM precision. Reserved document numbers make a replay
    against the *same input* deterministic; applying twice to its own output is
    deliberately rejected on document-number collisions.

    ``source_row_removal`` removes one balanced ACDOCA line group, retaining
    BKPF; ``source_document_removal`` removes the header and all its ledgers.
    ``headerless_adjustment`` is the explicit BKPF referential exception.
    Reopened financial changes leave the original header entry time unchanged.
    Recovery, duplicate extraction and interruption are runtime, not source,
    scenarios and are intentionally absent.
    """
    name = scenario.replace("-", "_")
    if name not in SCENARIOS:
        raise ValueError(f"Unknown synthetic scenario {scenario!r}; choose from {SCENARIOS}")
    if not isinstance(entered_at, datetime):
        raise ValueError("entered_at must be a datetime")
    clock = (
        entered_at.replace(tzinfo=timezone.utc) if entered_at.tzinfo is None
        else entered_at.astimezone(timezone.utc)
    ).replace(microsecond=0)
    if clock.year < 2:
        raise ValueError("Scenario entry year must be at least 0002")
    missing = set(SPECS_BY_NAME) - source.keys()
    if missing:
        raise ValueError(f"Scenario source is missing tables: {sorted(missing)}")
    result = deepcopy(source)
    today = clock.date()

    if name in {"new_documents", "checkpoint_same_second"}:
        slots = (1, 2) if name == "new_documents" else (13, 14)
        for slot in slots:
            _append_document(result, clock, slot, today, name)
    elif name == "late_current_month":
        if today.day == 1:
            raise ValueError("A late current-month posting needs an entry day after the first")
        _append_document(result, clock, 3, today.replace(day=1), name)
    elif name == "prior_fiscal_year":
        _append_document(result, clock, 4, date(today.year - 1, 12, 31), name)
    elif name == "reversal":
        original, original_lines = _template(result, all_ledgers=True)
        reversal, reversal_lines = _append_document(
            result, clock, 5, today, name, all_ledgers=True,
        )
        original.update(STBLG=reversal["BELNR"], STJAH=reversal["GJAHR"])
        reversal.update(STBLG=original["BELNR"], STJAH=original["GJAHR"])
        for line, original_line in zip(reversal_lines, original_lines, strict=True):
            for column in SPECS_BY_NAME["ACDOCA"].decimal_columns:
                line[column] = original_line[column].copy_negate()
            line["DRCRK"] = {"S": "H", "H": "S"}[str(original_line["DRCRK"])]
    elif name == "multiple_ledgers":
        _, lines = _append_document(result, clock, 6, today, name)
        second_ledger = deepcopy(lines)
        with localcontext(Context(prec=38, rounding=ROUND_HALF_UP)):
            for line in second_ledger:
                line["RLDNR"] = "2L"
                for amount, currency, unit, divisor in (
                    ("KSL", "RKCUR", "USD", "20"),
                    ("TSL", "RTCUR", "EUR", "18"),
                    ("WSL", "RWCUR", "GBP", "22"),
                ):
                    line[currency] = unit
                    line[amount] = (line[amount] / Decimal(divisor)).quantize(Decimal("0.01"))
        result["ACDOCA"].extend(second_ledger)
    elif name == "cross_company":
        companies = sorted({row["BUKRS"] for row in result["T001"]})
        if len(companies) < 2:
            raise ValueError("Cross-company scenario requires at least two companies")
        for company in companies[:2]:
            _append_document(result, clock, 7, today, name, company=company)
    elif name == "master_changes":
        for table, field, value in (
            ("T001", "BUTXT", "SYNTHETIC RENAMED COMPANY"),
            ("CSKS", "VERAK", "SYNTHETIC_CHANGED_OWNER"),
            ("CEPC", "VERAK", "SYNTHETIC_CHANGED_OWNER"),
            ("SKAT", "TXT50", "SYNTHETIC RENAMED GL ACCOUNT"),
            ("LFA1", "NAME1", "SYNTHETIC RENAMED VENDOR"),
            ("KNA1", "NAME1", "SYNTHETIC RENAMED CUSTOMER"),
        ):
            spec = SPECS_BY_NAME[table]
            row = min(result[table], key=lambda item: tuple(item[key] for key in spec.keys))
            row[field] = value
    elif name == "validity_slices":
        cost = min(
            result["CSKS"],
            key=lambda row: tuple(row[key] for key in SPECS_BY_NAME["CSKS"].keys),
        )
        profit = next(
            row for row in result["CEPC"]
            if (row["MANDT"], row["KOKRS"], row["PRCTR"], row["DATBI"])
            == (cost["MANDT"], cost["KOKRS"], cost["PRCTR"], cost["DATBI"])
        )
        _split_validity(result["CSKS"], cost, today)
        _split_validity(result["CEPC"], profit, today)
    elif name == "reopened_historical_period":
        eligible = dict(result)
        eligible["ACDOCA"] = [
            row for row in result["ACDOCA"] if str(row["BUDAT"]) < f"{today.replace(day=1):%Y%m%d}"
        ]
        header, _ = _template(eligible)
        with localcontext(Context(prec=38, rounding=ROUND_HALF_UP)):
            for line in result["ACDOCA"]:
                if _header_key(line, line=True) == _header_key(header):
                    for amount, _ in AMOUNT_CURRENCY_PAIRS:
                        value = line[amount]
                        if value:
                            line[amount] = value + (Decimal("17.25") if value > 0 else Decimal("-17.25"))
                    line["SGTXT"] = "SYNTHETIC REOPENED HISTORICAL PERIOD"
    elif name in {"source_row_removal", "source_document_removal"}:
        header, lines = _template(result)
        if name == "source_row_removal":
            removed = {tuple(row[key] for key in SPECS_BY_NAME["ACDOCA"].keys) for row in lines}
            result["ACDOCA"] = [
                row for row in result["ACDOCA"]
                if tuple(row[key] for key in SPECS_BY_NAME["ACDOCA"].keys) not in removed
            ]
        else:
            key = _header_key(header)
            result["BKPF"] = [row for row in result["BKPF"] if _header_key(row) != key]
            result["ACDOCA"] = [
                row for row in result["ACDOCA"] if _header_key(row, line=True) != key
            ]
    elif name == "headerless_adjustment":
        header, _ = _append_document(result, clock, 12, today, name)
        result["BKPF"].remove(header)
    return result
