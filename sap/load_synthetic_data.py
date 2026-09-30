#!/usr/bin/env python3
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "pyodbc>=5.3,<6",
# ]
# ///
"""Load synthetic SAP Finance CSV snapshots into Fabric SQL Database.

Usage:
    uv run load_synthetic_data.py --mode initial
    uv run load_synthetic_data.py --mode delta --business-date 2026-09-30
    uv run load_synthetic_data.py --mode delta --touch-percent 5 \
        --forecast-adjustment-percent 1
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import os
import re
import shutil
import struct
import subprocess
import sys
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Literal

import pyodbc

EXIT_SUCCESS = 0
EXIT_FAILURE = 1
EXIT_ERROR = 2

LOGGER = logging.getLogger(__name__)
SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_SOURCE_ROOT = SCRIPT_DIR.parent / "sap_synthetic_data" / "sap"
DEFAULT_CONNECTION = SCRIPT_DIR / "connection.json"
EXTRACT_DATE_PATTERN = re.compile(r"_(\d{8})\.csv$", re.IGNORECASE)


@dataclass(frozen=True)
class TableSpec:
    """Describe one source CSV and its Fabric SQL target."""

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
    "RWCUR", "HSL", "KSL", "TSL", "WSL", "MSL",
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
         "BLART", "STBLG", "BKTXT"),
        ("MANDT", "BUKRS", "GJAHR", "BELNR"),
        numc_widths=(("GJAHR", 4), ("MONAT", 2)),
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
        ("MANDT", "KOKRS", "KOSTL"),
    ),
    TableSpec(
        "CEPC", "SAPABAP1", "cepc",
        ("MANDT", "KOKRS", "PRCTR", "BUKRS", "VERAK", "KHINR", "SEGMENT",
         "DATAB", "DATBI"),
        ("MANDT", "KOKRS", "PRCTR"),
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


def create_parser() -> argparse.ArgumentParser:
    """Create the command-line parser."""
    parser = argparse.ArgumentParser(
        description="Load or incrementally touch synthetic SAP Finance data."
    )
    parser.add_argument(
        "--mode", choices=("initial", "delta"), default="initial"
    )
    parser.add_argument(
        "--source-root", type=Path, default=DEFAULT_SOURCE_ROOT
    )
    parser.add_argument(
        "--connection-config", type=Path, default=DEFAULT_CONNECTION
    )
    parser.add_argument(
        "--business-date",
        type=date.fromisoformat,
        default=datetime.now(timezone.utc).date(),
    )
    parser.add_argument(
        "--touch-percent",
        type=float,
        default=5.0,
        help="Percentage of each table touched in delta mode.",
    )
    parser.add_argument(
        "--forecast-adjustment-percent",
        type=Decimal,
        default=Decimal("1.0"),
        help="Deterministic adjustment applied to selected budget forecasts.",
    )
    parser.add_argument(
        "--tables",
        default="",
        help="Optional comma-separated table names.",
    )
    parser.add_argument("--verbose", action="store_true")
    return parser


def configure_logging(verbose: bool) -> None:
    """Configure console logging."""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )


def azure_cli_path() -> str:
    """Resolve the Azure CLI executable."""
    candidate = (
        os.environ.get("AZURE_CLI_PATH")
        or shutil.which("az")
        or shutil.which("az.cmd")
    )
    if not candidate:
        raise RuntimeError(
            "Azure CLI was not found. Set AZURE_CLI_PATH or install az."
        )
    return candidate


def get_access_token() -> str:
    """Acquire a database token from the authenticated Azure CLI."""
    result = subprocess.run(
        [
            azure_cli_path(),
            "account",
            "get-access-token",
            "--resource",
            "https://database.windows.net/",
            "--query",
            "accessToken",
            "--output",
            "tsv",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    token = result.stdout.strip()
    if not token:
        raise RuntimeError("Azure CLI returned an empty SQL access token.")
    return token


def connect(config_path: Path) -> pyodbc.Connection:
    """Connect to Fabric SQL Database with an Entra access token."""
    config = json.loads(config_path.read_text(encoding="utf-8"))
    token_bytes = get_access_token().encode("utf-16-le")
    token_struct = struct.pack(
        f"<I{len(token_bytes)}s", len(token_bytes), token_bytes
    )
    connection_string = (
        "Driver={ODBC Driver 18 for SQL Server};"
        f"Server={config['serverFqdn']};"
        f"Database={config['databaseName']};"
        "Encrypt=yes;TrustServerCertificate=no;"
    )
    return pyodbc.connect(
        connection_string,
        attrs_before={1256: token_struct},
        timeout=60,
        autocommit=False,
    )


def source_file(spec: TableSpec, root: Path) -> Path:
    """Resolve the single CSV file for a table."""
    files = sorted((root / spec.folder).glob("*.csv"))
    if len(files) != 1:
        raise ValueError(
            f"{spec.name}: expected one CSV under {root / spec.folder}, "
            f"found {len(files)}"
        )
    return files[0]


def filename_extract_date(path: Path) -> date:
    """Parse the source extract date from the file name."""
    match = EXTRACT_DATE_PATTERN.search(path.name)
    if not match:
        raise ValueError(f"Extract date missing from file name: {path.name}")
    return datetime.strptime(match.group(1), "%Y%m%d").date()


def normalize_value(spec: TableSpec, column: str, value: str) -> object:
    """Normalize one CSV value to its SQL representation."""
    value = value.strip()
    if not value:
        return None
    if column in spec.decimal_columns:
        return Decimal(value)
    width = spec.numc_map.get(column)
    if width and value.isdigit():
        return value.zfill(width)
    return value


def canonical_value(value: object) -> str:
    """Return a stable string for hashing."""
    if value is None:
        return "__NULL__"
    if isinstance(value, Decimal):
        return format(value, "f")
    return str(value)


def business_key(spec: TableSpec, row: dict[str, object]) -> str:
    """Create the canonical key used by ETL tracking views."""
    return "|".join(canonical_value(row[column]) for column in spec.keys)


def row_hash(spec: TableSpec, row: dict[str, object]) -> bytes:
    """Create a SHA-256 hash across all source business columns."""
    content = "||".join(
        canonical_value(row[column]) for column in spec.columns
    )
    return hashlib.sha256(content.encode("utf-8")).digest()


def selected_for_delta(key: str, business_date: date, percent: float) -> bool:
    """Select a deterministic percentage of keys for the daily delta."""
    threshold = max(0, min(10_000, round(percent * 100)))
    digest = hashlib.sha256(
        f"{business_date.isoformat()}|{key}".encode("utf-8")
    ).digest()
    return int.from_bytes(digest[:4], "big") % 10_000 < threshold


def load_rows(
    spec: TableSpec,
    root: Path,
    mode: Literal["initial", "delta"],
    business_date: date,
    touch_percent: float,
    forecast_adjustment: Decimal,
) -> tuple[list[tuple[object, ...]], int]:
    """Read, normalize, validate, and select source rows."""
    path = source_file(spec, root)
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        actual_columns = tuple(reader.fieldnames or ())
        if actual_columns != spec.columns:
            raise ValueError(
                f"{spec.name}: columns differ. Expected {spec.columns}; "
                f"found {actual_columns}"
            )
        normalized: list[dict[str, object]] = []
        for csv_row in reader:
            row = {
                column: normalize_value(spec, column, csv_row[column])
                for column in spec.columns
            }
            normalized.append(row)

    selected: list[dict[str, object]]
    if mode == "initial":
        selected = normalized
        extract_date = filename_extract_date(path)
        output_name = path.name
        force_touch = False
    else:
        selected = [
            row for row in normalized
            if selected_for_delta(
                business_key(spec, row), business_date, touch_percent
            )
        ]
        if not selected and normalized:
            selected = [normalized[0]]
        extract_date = business_date
        output_name = f"{spec.folder}_{business_date:%Y%m%d}.csv"
        force_touch = True

    if mode == "delta" and spec.name == "BUDGET":
        multiplier = Decimal("1") + forecast_adjustment / Decimal("100")
        for row in selected:
            current = row["FORECAST_AMOUNT_LOCAL"]
            if isinstance(current, Decimal):
                row["FORECAST_AMOUNT_LOCAL"] = (
                    current * multiplier
                ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    prepared: list[tuple[object, ...]] = []
    for row in selected:
        key = business_key(spec, row)
        prepared.append(
            (
                *(row[column] for column in spec.columns),
                key,
                row_hash(spec, row),
                extract_date,
                output_name,
                force_touch,
            )
        )
    return prepared, len(normalized)


def quote_list(columns: tuple[str, ...]) -> str:
    """Return a quoted comma-separated column list."""
    return ",".join(f"[{column}]" for column in columns)


def sql_type_declarations(
    cursor: pyodbc.Cursor, spec: TableSpec
) -> dict[str, str]:
    """Read target SQL types for OPENJSON conversion."""
    cursor.execute(
        """
        SELECT
            c.name,
            TYPE_NAME(c.user_type_id) AS type_name,
            c.max_length,
            c.precision,
            c.scale
        FROM sys.columns c
        WHERE c.object_id = OBJECT_ID(?)
        ORDER BY c.column_id;
        """,
        f"{spec.schema}.{spec.name}",
    )
    declarations: dict[str, str] = {}
    for name, type_name, max_length, precision, scale in cursor.fetchall():
        if type_name in {"varchar", "char"}:
            length = "max" if max_length == -1 else str(max_length)
            declarations[name] = f"{type_name}({length})"
        elif type_name in {"nvarchar", "nchar"}:
            length = "max" if max_length == -1 else str(max_length // 2)
            declarations[name] = f"{type_name}({length})"
        elif type_name in {"decimal", "numeric"}:
            declarations[name] = f"{type_name}({precision},{scale})"
        else:
            declarations[name] = type_name
    missing = set(spec.columns) - declarations.keys()
    if missing:
        raise ValueError(f"{spec.name}: SQL columns not found: {sorted(missing)}")
    return declarations


def json_value(value: object) -> object:
    """Convert a staged value to a JSON-compatible representation."""
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.hex()
    return value


def merge_table(
    connection: pyodbc.Connection,
    spec: TableSpec,
    rows: list[tuple[object, ...]],
) -> None:
    """Merge source rows into the Fabric SQL source simulator."""
    cursor = connection.cursor()
    source_columns = quote_list(spec.columns)
    stage_table = f"[etl].[stage_{spec.name}]"
    cursor.execute(
        f"""
        IF OBJECT_ID(N'etl.stage_{spec.name}', N'U') IS NULL
        BEGIN
            SELECT TOP (0) {source_columns}
            INTO {stage_table}
            FROM {spec.target};

            ALTER TABLE {stage_table} ADD
                BUSINESS_KEY nvarchar(512) NULL,
                ROW_HASH binary(32) NULL,
                SOURCE_EXTRACT_DATE date NULL,
                SOURCE_FILE_NAME nvarchar(260) NULL,
                FORCE_TOUCH bit NULL;
        END;

        TRUNCATE TABLE {stage_table};
        """
    )
    all_columns = (
        *spec.columns,
        "BUSINESS_KEY",
        "ROW_HASH",
        "SOURCE_EXTRACT_DATE",
        "SOURCE_FILE_NAME",
        "FORCE_TOUCH",
    )
    insert_sql = (
        f"INSERT INTO {stage_table} ({quote_list(all_columns)}) "
        f"SELECT {source_columns}, [BUSINESS_KEY], "
        f"CONVERT(binary(32), [ROW_HASH_HEX], 2), "
        f"[SOURCE_EXTRACT_DATE], [SOURCE_FILE_NAME], [FORCE_TOUCH] "
        f"FROM OPENJSON(CONVERT(varchar(max), CONVERT(varbinary(max), ?))) WITH ("
    )
    declarations = sql_type_declarations(cursor, spec)
    with_columns = [
        f"[{column}] {declarations[column]} '$.\"{column}\"'"
        for column in spec.columns
    ]
    with_columns.extend(
        [
            "[BUSINESS_KEY] nvarchar(512) '$.\"BUSINESS_KEY\"'",
            "[ROW_HASH_HEX] varchar(64) '$.\"ROW_HASH_HEX\"'",
            "[SOURCE_EXTRACT_DATE] date '$.\"SOURCE_EXTRACT_DATE\"'",
            "[SOURCE_FILE_NAME] nvarchar(260) '$.\"SOURCE_FILE_NAME\"'",
            "[FORCE_TOUCH] bit '$.\"FORCE_TOUCH\"'",
        ]
    )
    insert_sql += ",".join(with_columns) + ");"
    json_columns = (
        *spec.columns,
        "BUSINESS_KEY",
        "ROW_HASH_HEX",
        "SOURCE_EXTRACT_DATE",
        "SOURCE_FILE_NAME",
        "FORCE_TOUCH",
    )
    for start in range(0, len(rows), 200):
        batch = []
        for staged_row in rows[start : start + 200]:
            row_values = list(staged_row)
            row_values[len(spec.columns) + 1] = json_value(
                row_values[len(spec.columns) + 1]
            )
            batch.append(
                {
                    column: json_value(value)
                    for column, value in zip(json_columns, row_values)
                }
            )
        payload = json.dumps(
            batch, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
        cursor.execute(insert_sql, payload)

    on_clause = " AND ".join(
        f"target.[{key}] = source.[{key}]" for key in spec.keys
    )
    non_keys = tuple(
        column for column in spec.columns if column not in spec.keys
    )
    update_clause = ",".join(
        f"target.[{column}] = source.[{column}]" for column in non_keys
    )
    values = ",".join(f"source.[{column}]" for column in spec.columns)
    cursor.execute(
        f"""
        MERGE {spec.target} AS target
        USING {stage_table} AS source
           ON {on_clause}
        WHEN MATCHED THEN
            UPDATE SET {update_clause}
        WHEN NOT MATCHED THEN
            INSERT ({source_columns}) VALUES ({values});
        """
    )
    cursor.execute(f"TRUNCATE TABLE {stage_table};")


def selected_specs(selection: str) -> tuple[TableSpec, ...]:
    """Filter table specifications from a CLI selection."""
    if not selection.strip():
        return TABLE_SPECS
    requested = {
        value.strip().upper()
        for value in selection.split(",")
        if value.strip()
    }
    available = {spec.name for spec in TABLE_SPECS}
    unknown = sorted(requested - available)
    if unknown:
        raise ValueError(f"Unknown tables: {unknown}")
    return tuple(spec for spec in TABLE_SPECS if spec.name in requested)


def run(args: argparse.Namespace) -> int:
    """Execute one initial or daily delta load."""
    if not 0 < args.touch_percent <= 100:
        raise ValueError("--touch-percent must be greater than 0 and at most 100")
    specs = selected_specs(args.tables)
    batch_id = uuid.uuid4()
    started = datetime.now(timezone.utc).replace(tzinfo=None)
    connection = connect(args.connection_config)
    total_selected = 0
    try:
        cursor = connection.cursor()
        cursor.execute(
            """
            INSERT INTO etl.load_batch
            (
                LOAD_BATCH_ID, LOAD_MODE, BUSINESS_DATE, STARTED_UTC, STATUS
            )
            VALUES (?, ?, ?, ?, 'RUNNING');
            """,
            str(batch_id),
            args.mode,
            args.business_date,
            started,
        )
        connection.commit()

        for spec in specs:
            rows, source_count = load_rows(
                spec,
                args.source_root,
                args.mode,
                args.business_date,
                args.touch_percent,
                args.forecast_adjustment_percent,
            )
            merge_table(connection, spec, rows)
            connection.commit()
            total_selected += len(rows)
            LOGGER.info(
                "%s: source=%d selected=%d", spec.name, source_count, len(rows)
            )

        cursor.execute(
            """
            UPDATE etl.load_batch
            SET COMPLETED_UTC = SYSUTCDATETIME(),
                STATUS = 'SUCCEEDED',
                TABLE_COUNT = ?,
                SOURCE_ROW_COUNT = ?
            WHERE LOAD_BATCH_ID = ?;
            """,
            len(specs),
            total_selected,
            str(batch_id),
        )
        connection.commit()
        print(
            json.dumps(
                {
                    "batchId": str(batch_id),
                    "mode": args.mode,
                    "businessDate": args.business_date.isoformat(),
                    "tables": len(specs),
                    "selectedRows": total_selected,
                },
                indent=2,
            )
        )
        return EXIT_SUCCESS
    except Exception as error:
        connection.rollback()
        try:
            cursor = connection.cursor()
            cursor.execute(
                """
                UPDATE etl.load_batch
                SET COMPLETED_UTC = SYSUTCDATETIME(),
                    STATUS = 'FAILED',
                    ERROR_MESSAGE = ?
                WHERE LOAD_BATCH_ID = ?;
                """,
                str(error)[:2_000],
                str(batch_id),
            )
            connection.commit()
        except Exception:
            LOGGER.exception("Could not record failed batch status")
        raise
    finally:
        connection.close()


def main() -> int:
    """Run the loader with standard exit handling."""
    parser = create_parser()
    args = parser.parse_args()
    configure_logging(args.verbose)
    try:
        return run(args)
    except KeyboardInterrupt:
        LOGGER.error("Interrupted")
        return 130
    except (OSError, ValueError, RuntimeError, pyodbc.Error) as error:
        LOGGER.error("%s", error)
        return EXIT_ERROR
    except Exception:
        LOGGER.exception("Unexpected loader failure")
        return EXIT_FAILURE


if __name__ == "__main__":
    sys.exit(main())
