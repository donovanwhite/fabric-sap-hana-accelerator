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
    uv run load_synthetic_data.py --mode scenario --scenario reversal \
        --entered-at 2026-09-30T12:34:56Z --synthetic-target MY_SIMULATOR_DB
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import shutil
import struct
import subprocess
import sys
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    import pyodbc

if __package__:
    from .synthetic_scenarios import (
        ACDOCA_COLUMNS, EXTRACT_DATE_PATTERN, FAGLFLEXP_COLUMNS,
        LEGACY_ADDITIONS, SCENARIOS, TABLE_SPECS, Row, SourceRows, TableSpec,
        apply_scenario, filename_extract_date, normalize_value, read_seed_table,
        source_file,
    )
else:
    from synthetic_scenarios import (
        ACDOCA_COLUMNS, EXTRACT_DATE_PATTERN, FAGLFLEXP_COLUMNS,
        LEGACY_ADDITIONS, SCENARIOS, TABLE_SPECS, Row, SourceRows, TableSpec,
        apply_scenario, filename_extract_date, normalize_value, read_seed_table,
        source_file,
    )

EXIT_SUCCESS = 0
EXIT_FAILURE = 1
EXIT_ERROR = 2

LOGGER = logging.getLogger(__name__)
SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_SOURCE_ROOT = SCRIPT_DIR.parent / "sap_synthetic_data" / "sap"
DEFAULT_CONNECTION = SCRIPT_DIR / "connection.example.json"


def create_parser() -> argparse.ArgumentParser:
    """Create the command-line parser."""
    parser = argparse.ArgumentParser(
        description="Load or incrementally touch synthetic SAP Finance data."
    )
    parser.add_argument(
        "--mode", choices=("initial", "delta", "scenario"), default="initial"
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
    parser.add_argument(
        "--scenario",
        type=lambda value: value.replace("-", "_"),
        choices=SCENARIOS,
        help="Named source mutation; requires --mode scenario.",
    )
    parser.add_argument(
        "--entered-at",
        type=datetime.fromisoformat,
        help="Required scenario entry timestamp; naive values mean UTC.",
    )
    parser.add_argument(
        "--synthetic-target",
        help="Confirm the exact simulator DB name; its synthetic marker is also required.",
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
    try:
        import pyodbc
    except ImportError as error:
        raise RuntimeError("SQL loading requires pyodbc; use the script's uv environment.") from error
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
    normalized = read_seed_table(spec, root)

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

    return prepare_rows(
        spec, selected, extract_date, output_name, force_touch=force_touch,
    ), len(normalized)


def prepare_rows(
    spec: TableSpec,
    rows: list[Row],
    extract_date: date,
    output_name: str,
    *,
    force_touch: bool = False,
) -> list[tuple[object, ...]]:
    """Reuse the original staging/hash representation for CSVs and scenarios."""
    prepared: list[tuple[object, ...]] = []
    for row in rows:
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
    return prepared


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
    declarations = sql_type_declarations(cursor, spec)
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
    # Staging tables persist between loads. Upgrade those created before the
    # additive source fields, without dropping any simulator/control tables.
    for column in LEGACY_ADDITIONS.get(spec.name, ()):
        cursor.execute(
            f"""
            IF COL_LENGTH(N'etl.stage_{spec.name}', N'{column}') IS NULL
                ALTER TABLE {stage_table}
                    ADD [{column}] {declarations[column]} NULL;
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


def require_synthetic_target(
    connection: pyodbc.Connection, confirmed_database: str,
) -> None:
    """Refuse scenario writes without both a named confirmation and DB marker."""
    if not confirmed_database or not confirmed_database.strip():
        raise ValueError("Scenario writes require --synthetic-target with the exact database name")
    cursor = connection.cursor()
    cursor.execute(
        """
        SELECT DB_NAME(),
            CONVERT(nvarchar(128), (
                SELECT ep.value FROM sys.extended_properties AS ep
                WHERE ep.class = 0 AND ep.major_id = 0 AND ep.minor_id = 0
                    AND ep.name = N'SAP_FINANCE_SYNTHETIC_SIMULATOR'
            ));
        """
    )
    target = cursor.fetchone()
    if not target or target[0] != confirmed_database or target[1] != "1":
        raise ValueError(
            "Scenario target was not confirmed as the explicitly marked synthetic simulator; "
            "apply schema.sql only to the intended simulator and confirm DB_NAME() exactly."
        )


def read_simulator_rows(connection: pyodbc.Connection) -> SourceRows:
    """Read declared business columns, in full-key order, from the simulator."""
    source: SourceRows = {}
    cursor = connection.cursor()
    for spec in TABLE_SPECS:
        cursor.execute(
            f"SELECT {quote_list(spec.columns)} FROM {spec.target} "
            f"ORDER BY {quote_list(spec.keys)};"
        )
        source[spec.name] = [
            dict(zip(spec.columns, row, strict=True)) for row in cursor.fetchall()
        ]
    return source


def _index_rows(spec: TableSpec, rows: list[Row]) -> dict[tuple[object, ...], Row]:
    indexed: dict[tuple[object, ...], Row] = {}
    for row in rows:
        key = tuple(row[column] for column in spec.keys)
        if any(value is None for value in key) or key in indexed:
            raise ValueError(f"{spec.name}: missing or duplicate source key: {key}")
        indexed[key] = row
    return indexed


def apply_simulator_scenario(
    connection: pyodbc.Connection,
    scenario: str,
    entered_at: datetime,
    confirmed_database: str,
) -> dict[str, int]:
    """Apply a deterministic source diff; the caller owns commit/rollback.

    Only explicitly removed full keys are deleted. There is deliberately no
    broad anti-join delete, reseed, target truncate, or extraction recovery.
    Use a single simulator writer, as with the original shared staging loader.
    """
    require_synthetic_target(connection, confirmed_database)
    before = read_simulator_rows(connection)
    after = apply_scenario(before, scenario, entered_at)
    clock = (
        entered_at.replace(tzinfo=timezone.utc) if entered_at.tzinfo is None
        else entered_at.astimezone(timezone.utc)
    )
    changes: list[tuple[TableSpec, list[Row], list[Row]]] = []
    for spec in TABLE_SPECS:
        old = _index_rows(spec, before[spec.name])
        new = _index_rows(spec, after[spec.name])
        removed = [row for key, row in old.items() if key not in new]
        changed = [
            row for key, row in new.items()
            if key not in old
            or any(row[column] != old[key][column] for column in spec.columns)
        ]
        if removed or changed:
            changes.append((spec, removed, changed))

    deleted_count = 0
    selected_count = 0
    cursor = connection.cursor()
    for spec, removed, changed in changes:
        predicate = " AND ".join(f"[{key}] = ?" for key in spec.keys)
        for row in removed:
            cursor.execute(
                f"DELETE FROM {spec.target} WHERE {predicate};",
                *(row[key] for key in spec.keys),
            )
        deleted_count += len(removed)
        if changed:
            staged = prepare_rows(
                spec, changed, clock.date(), f"{spec.folder}_{clock:%Y%m%d}.csv",
            )
            merge_table(connection, spec, staged)
            selected_count += len(staged)
        LOGGER.info(
            "%s: scenario=%s upserted=%d deleted=%d",
            spec.name, scenario, len(changed), len(removed),
        )
    return {
        "tables": len(changes),
        "selectedRows": selected_count,
        "deletedRows": deleted_count,
    }


def run(args: argparse.Namespace) -> int:
    """Execute one initial/delta load or an atomic, explicitly synthetic scenario."""
    if not 0 < args.touch_percent <= 100:
        raise ValueError("--touch-percent must be greater than 0 and at most 100")
    scenario = getattr(args, "scenario", None)
    entered_at = getattr(args, "entered_at", None)
    synthetic_target = getattr(args, "synthetic_target", None)
    if args.mode == "scenario":
        if not scenario or not entered_at or not synthetic_target:
            raise ValueError("--mode scenario requires --scenario, --entered-at and --synthetic-target")
        if args.tables.strip():
            raise ValueError("--tables cannot partially apply a multi-table source scenario")
        if scenario.replace("-", "_") not in SCENARIOS:
            raise ValueError(f"Unknown synthetic scenario: {scenario}")
        business_date = (
            entered_at.replace(tzinfo=timezone.utc) if entered_at.tzinfo is None
            else entered_at.astimezone(timezone.utc)
        ).date()
    else:
        if scenario or entered_at or synthetic_target:
            raise ValueError("--scenario, --entered-at and --synthetic-target require --mode scenario")
        business_date = args.business_date
    specs = selected_specs(args.tables)
    batch_id = uuid.uuid4()
    started = datetime.now(timezone.utc).replace(tzinfo=None)
    connection = connect(args.connection_config)
    total_selected = 0
    total_deleted = 0
    table_count = len(specs)
    batch_started = False
    try:
        if args.mode == "scenario":
            # Guard even control-table writes; the diff helper also guards its
            # own public entry point when used outside this command.
            require_synthetic_target(connection, synthetic_target)
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
            business_date,
            started,
        )
        connection.commit()
        batch_started = True

        if args.mode == "scenario":
            counts = apply_simulator_scenario(
                connection, scenario, entered_at, synthetic_target,
            )
            total_selected = counts["selectedRows"]
            total_deleted = counts["deletedRows"]
            table_count = counts["tables"]
        else:
            for spec in specs:
                rows, source_count = load_rows(
                    spec,
                    args.source_root,
                    args.mode,
                    business_date,
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
            table_count,
            total_selected + total_deleted,
            str(batch_id),
        )
        connection.commit()
        print(
            json.dumps(
                {
                    "batchId": str(batch_id),
                    "mode": args.mode,
                    "businessDate": business_date.isoformat(),
                    "tables": table_count,
                    "selectedRows": total_selected,
                    **(
                        {"scenario": scenario, "deletedRows": total_deleted}
                        if args.mode == "scenario" else {}
                    ),
                },
                indent=2,
            )
        )
        return EXIT_SUCCESS
    except Exception as error:
        connection.rollback()
        if not batch_started:
            raise
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
    except (OSError, ValueError, RuntimeError) as error:
        LOGGER.error("%s", error)
        return EXIT_ERROR
    except Exception as error:
        if isinstance(error, getattr(sys.modules.get("pyodbc"), "Error", ())):
            LOGGER.error("%s", error)
            return EXIT_ERROR
        LOGGER.exception("Unexpected loader failure")
        return EXIT_FAILURE


if __name__ == "__main__":
    sys.exit(main())
