"""Shared SAP extraction contracts, read-only SQL and reconciliation rules.

The deployment embeds this module in the existing medallion notebooks.
It has no dependency on SAP, Spark, credentials, or a particular clock.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, localcontext
from enum import StrEnum
from typing import Mapping, Sequence
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
SAFE_VALUE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
SAP_KEYS = {
    "ACDOCA": ("RCLNT", "RLDNR", "RBUKRS", "GJAHR", "BELNR", "DOCLN"),
    "BKPF": ("MANDT", "BUKRS", "GJAHR", "BELNR"),
    "BSEG": ("MANDT", "BUKRS", "GJAHR", "BELNR", "BUZEI"),
    "CSKS": ("MANDT", "KOKRS", "KOSTL", "DATBI"),
    "CEPC": ("MANDT", "KOKRS", "PRCTR", "DATBI"),
    "T001": ("MANDT", "BUKRS"),
    "SKA1": ("MANDT", "KTOPL", "SAKNR"),
    "SKAT": ("MANDT", "SPRAS", "KTOPL", "SAKNR"),
    "LFA1": ("MANDT", "LIFNR"),
    "KNA1": ("MANDT", "KUNNR"),
}
SCALAR = str | int | Decimal | date | datetime | bool | None
Row = dict[str, SCALAR]


class ExtractionMode(StrEnum):
    FULL_SNAPSHOT = "FULL_SNAPSHOT"
    DOCUMENT_INCREMENTAL = "DOCUMENT_INCREMENTAL"
    PARTITION_REFRESH = "PARTITION_REFRESH"
    SNAPSHOT_COMPARE = "SNAPSHOT_COMPARE"


def json_text(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def identifier(value: str) -> str:
    if not IDENTIFIER.fullmatch(value):
        raise ValueError(f"Invalid SQL identifier: {value!r}")
    return value


def source_zone(name: str) -> timezone | ZoneInfo:
    if name == "UTC":
        return timezone.utc
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as error:
        raise ValueError(
            f"Unknown or unavailable source timezone {name!r}; "
            "install the deployment package (including tzdata on Windows)"
        ) from error


def literal(value: object) -> str:
    text = str(value)
    if not SAFE_VALUE.fullmatch(text):
        raise ValueError(f"Invalid extraction predicate value: {text!r}")
    return "'" + text + "'"


@dataclass(frozen=True)
class TableContract:
    name: str
    schema: str
    source_table: str
    columns: tuple[str, ...]
    keys: tuple[str, ...]
    mode: ExtractionMode
    types: Mapping[str, str] = field(default_factory=dict)
    required: tuple[str, ...] = ()
    partition_columns: tuple[str, ...] = ()
    partition_values: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    amounts: Mapping[str, str] = field(default_factory=dict)
    discovery: Mapping[str, object] = field(default_factory=dict)
    filters: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    tolerance: Decimal = Decimal("0")
    schema_verified: bool = False
    scalar_amounts: tuple[str, ...] = ()
    fiscal_year_range: tuple[int, int] = (1, 9999)
    sink_folder: str = ""

    @property
    def measures(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys([*self.amounts, *self.scalar_amounts]))

    @classmethod
    def from_dict(
        cls, value: dict, fiscal_year_range: Sequence[int] = (1, 9999),
    ) -> TableContract:
        missing = {"name", "sourceObject", "columns", "businessKey", "extractionMode"} - value.keys()
        if missing:
            raise ValueError(f"Incomplete source contract; missing {sorted(missing)}")
        name = identifier(value["name"]).upper()
        parts = value["sourceObject"].split(".")
        if len(parts) != 2:
            raise ValueError(f"{name}: sourceObject must be schema.table")
        schema, source_table = (identifier(part) for part in parts)
        folder = value.get("sinkFolder", f"Files/bronze/sap_finance/{name.lower()}")
        folder = folder.removeprefix("Files/")
        if (
            not folder.startswith("bronze/sap_finance/")
            or any(not SAFE_VALUE.fullmatch(piece) for piece in folder.split("/"))
        ):
            raise ValueError(f"{name}: sinkFolder must remain below bronze/sap_finance")
        columns = tuple(identifier(column) for column in value["columns"])
        keys = tuple(value["businessKey"])
        if len(columns) != len(set(columns)) or not columns:
            raise ValueError(f"{name}: empty or duplicate projection")
        if not keys or len(keys) != len(set(keys)) or not set(keys) <= set(columns):
            raise ValueError(f"{name}: invalid business key")
        if name in SAP_KEYS and set(keys) != set(SAP_KEYS[name]):
            raise ValueError(f"{name}: complete SAP business key is {SAP_KEYS[name]}")
        mode = ExtractionMode(value["extractionMode"])
        partitions = tuple(value.get("partitionColumns", []))
        if len(partitions) != len(set(partitions)):
            raise ValueError(f"{name}: duplicate partition columns")
        types = value.get("types", {})
        required = tuple(dict.fromkeys([*keys, *value.get("required", [])]))
        amounts = value.get("amountCurrencies", {})
        scalar_amounts = tuple(value.get("amountColumns", []))
        referenced = (
            set(partitions) | set(types) | set(required) | set(amounts)
            | set(amounts.values()) | set(scalar_amounts)
        )
        if not referenced <= set(columns):
            raise ValueError(f"{name}: metadata refers to unprojected columns: {referenced - set(columns)}")
        for data_type in types.values():
            match = re.fullmatch(r"decimal\(([1-9]|[12]\d|3[0-8]),(\d+)\)", data_type)
            if data_type not in {"int", "date"} and (
                match is None or int(match[2]) > int(match[1])
            ):
                raise ValueError(f"{name}: unsupported data type {data_type!r}")
        if any(not types.get(amount, "").startswith("decimal(") for amount in [*amounts, *scalar_amounts]):
            raise ValueError(f"{name}: financial amounts require explicit decimal types")
        if any(currency in types for currency in amounts.values()):
            raise ValueError(f"{name}: currency identifiers must remain strings")
        partition_values = {
            column: tuple(str(item) for item in values)
            for column, values in value.get("partitionValues", {}).items()
        }
        filters = {
            column: tuple(str(item) for item in values)
            for column, values in value.get("filters", {}).items()
        }
        if not set(partition_values) <= set(partitions) or not set(filters) <= set(columns):
            raise ValueError(f"{name}: invalid partition or filter mapping")
        for values in [*partition_values.values(), *filters.values()]:
            if not values or len(values) != len(set(values)):
                raise ValueError(f"{name}: empty or duplicate predicate values")
            for item in values:
                literal(item)
        discovery = value.get("documentDiscovery", {})
        if mode == ExtractionMode.DOCUMENT_INCREMENTAL or discovery:
            if name not in {"ACDOCA", "BKPF", "BSEG"}:
                raise ValueError(f"{name}: no verified BKPF document mapping")
            if discovery.get("dateColumn") != "CPUDT" or discovery.get("timeColumn") != "CPUTM":
                raise ValueError(f"{name}: document entry requires BKPF.CPUDT/CPUTM")
            identifier(str(discovery.get("schema", schema)))
            if discovery.get("table") != "BKPF":
                raise ValueError(f"{name}: document discovery must use BKPF")
        tolerance = Decimal(str(value.get("financialTolerance", "0")))
        if not tolerance.is_finite() or tolerance < 0:
            raise ValueError(f"{name}: invalid financial tolerance")
        if mode == ExtractionMode.PARTITION_REFRESH and not partitions:
            raise ValueError(f"{name}: partition refresh requires partition columns")
        return cls(
            name, schema, source_table, columns, keys, mode, types, required,
            partitions, partition_values, amounts, discovery, filters,
            tolerance, value.get("schemaVerified", False), scalar_amounts,
            (fiscal_year_range[0], fiscal_year_range[1]),
            folder,
        )


@dataclass(frozen=True)
class ExtractionConfig:
    tables: tuple[TableContract, ...]
    source_system: str
    source_timezone: str
    max_concurrency: int
    overlap_hours: int
    current_fiscal_year: int
    reconciliation: Mapping[str, int]
    fingerprint: str
    source_identity: Mapping[str, str]

    @classmethod
    def from_dict(cls, value: dict) -> ExtractionConfig:
        years = value.get("fiscalYearRange", [1, 9999])
        if (
            not isinstance(years, (list, tuple)) or len(years) != 2
            or any(type(year) is not int for year in years)
            or not 1 <= years[0] <= years[1] <= 9999
        ):
            raise ValueError("fiscalYearRange must be [minimum, maximum] within 1..9999")
        tables = tuple(TableContract.from_dict(table, years) for table in value["tables"])
        if not tables or len({table.name for table in tables}) != len(tables):
            raise ValueError("Source tables must be nonempty and unique")
        header = next((table for table in tables if table.name == "BKPF"), None)
        if header is not None:
            for table in tables:
                if not table.discovery or table.name == "BKPF":
                    continue
                if header.schema != table.discovery.get("schema", table.schema) or header.source_table != "BKPF":
                    raise ValueError(f"{table.name}: discovery mapping differs from the configured BKPF source")
                client = "RCLNT" if table.name == "ACDOCA" else "MANDT"
                company = "RBUKRS" if table.name == "ACDOCA" else "BUKRS"
                for source_column, header_column in ((client, "MANDT"), (company, "BUKRS"), ("GJAHR", "GJAHR")):
                    allowed = header.filters.get(header_column)
                    selected = table.filters.get(source_column)
                    if allowed is not None and (selected is None or not set(selected) <= set(allowed)):
                        raise ValueError(f"{table.name}: BKPF filter does not cover {source_column}")
        zone = value["sourceTimezone"]
        source_zone(zone)
        maximum = value.get("maxConcurrency", 2)
        overlap = value.get("overlapHours", 48)
        year = value["currentFiscalYear"]
        if type(maximum) is not int or not 1 <= maximum <= 50:
            raise ValueError("maxConcurrency must be between 1 and 50")
        if type(overlap) is not int or not 0 <= overlap <= 24 * 366:
            raise ValueError("overlapHours is outside the supported range")
        if type(year) is not int or not 1900 <= year <= 9999:
            raise ValueError("currentFiscalYear must be an explicit SAP fiscal year")
        policies = value.get("reconciliation", {})
        allowed = {"CURRENT_FISCAL_YEAR", "PREVIOUS_FISCAL_YEAR", "HISTORICAL_FISCAL_YEARS"}
        if set(policies) - allowed or any(type(days) is not int or days < 1 for days in policies.values()):
            raise ValueError("Invalid reconciliation policy or day interval")
        identity = value.get("sourceIdentity")
        if not isinstance(identity, dict) or not identity or any(
            not isinstance(key, str) or not isinstance(item, str) or not item.strip()
            for key, item in identity.items()
        ):
            raise ValueError("sourceIdentity must explicitly identify the source instance")
        return cls(
            tables, identifier(value.get("sourceSystem", "SAP_S4HANA")),
            zone, maximum, overlap, year, policies,
            hashlib.sha256(json_text(value).encode()).hexdigest(),
            identity,
        )

    def table(self, name: str) -> TableContract:
        for table in self.tables:
            if table.name == name.upper():
                return table
        raise ValueError(f"Unknown source table {name!r}")

    def checkpoint_scope(self, table: TableContract) -> str:
        contract = asdict(table)
        contract.pop("schema_verified")
        contract.pop("sink_folder")
        return hashlib.sha256(json_text({
            "source_system": self.source_system, "source_timezone": self.source_timezone,
            "source_identity": dict(self.source_identity), "table": contract,
        }).encode()).hexdigest()


@dataclass(frozen=True)
class EntryBoundary:
    start: str
    end: str

    def __post_init__(self) -> None:
        for value in (self.start, self.end):
            if len(value) != 14:
                raise ValueError("Entry boundaries must use YYYYMMDDHHMMSS")
            datetime.strptime(value, "%Y%m%d%H%M%S")
        if self.start > self.end:
            raise ValueError("Entry checkpoint is later than the captured upper bound")

    @classmethod
    def window(
        cls, last_date: str, last_time: str, upper_utc: datetime, *,
        overlap_hours: int, source_timezone: str,
    ) -> EntryBoundary:
        if upper_utc.tzinfo is None:
            raise ValueError("Upper extraction boundary must be timezone aware")
        if type(overlap_hours) is not int or not 0 <= overlap_hours <= 24 * 366:
            raise ValueError("Invalid entry overlap_hours")
        last = datetime.strptime(last_date + last_time, "%Y%m%d%H%M%S")
        upper = upper_utc.astimezone(source_zone(source_timezone)).replace(tzinfo=None, microsecond=0)
        if last > upper:
            raise ValueError("Committed entry checkpoint is in the future")
        return cls(
            (last - timedelta(hours=overlap_hours)).strftime("%Y%m%d%H%M%S"),
            upper.strftime("%Y%m%d%H%M%S"),
        )


def build_query(
    table: TableContract, mode: ExtractionMode | str,
    boundary: EntryBoundary | None = None,
    partition: Mapping[str, str] | None = None, *, envelope: bool = False,
) -> str:
    """Build SELECT-only SQL valid for HANA and the SQL source simulator.

    The connector accepts query text, not bind parameters. Identifiers are
    allowlisted and predicate literals are restricted to validated SAP tokens.
    """
    mode = ExtractionMode(mode)
    partition = dict(partition or {})
    if mode == ExtractionMode.PARTITION_REFRESH:
        if not partition or set(partition) != set(table.partition_columns):
            raise ValueError(f"{table.name}: complete logical partition required")
    elif partition and not set(partition) <= set(table.partition_columns):
        raise ValueError(f"{table.name}: invalid partition columns")
    columns = ", ".join(f'A."{column}"' for column in table.columns)
    query = f'SELECT {columns} FROM "{table.schema}"."{table.source_table}" A'
    predicates = []
    if mode == ExtractionMode.DOCUMENT_INCREMENTAL:
        if boundary is None or not table.discovery:
            raise ValueError(f"{table.name}: document entry boundary is required")
        if table.name == "BKPF":
            alias = "A"
        else:
            alias = "B"
            client = "RCLNT" if table.name == "ACDOCA" else "MANDT"
            company = "RBUKRS" if table.name == "ACDOCA" else "BUKRS"
            schema = identifier(str(table.discovery.get("schema", table.schema)))
            query += (
                f' INNER JOIN "{schema}"."BKPF" B ON '
                f'A."{client}" = B."MANDT" AND A."{company}" = B."BUKRS" '
                'AND A."GJAHR" = B."GJAHR" AND A."BELNR" = B."BELNR"'
            )
        for operator, bound in ((">=", boundary.start), ("<=", boundary.end)):
            equality = ">=" if operator == ">=" else "<="
            strict = ">" if operator == ">=" else "<"
            predicates.append(
                f'({alias}."CPUDT" {strict} {literal(bound[:8])} OR '
                f'({alias}."CPUDT" = {literal(bound[:8])} AND '
                f'{alias}."CPUTM" {equality} {literal(bound[8:])}))'
            )
    for column, values in table.filters.items():
        predicates.append(f'A."{column}" IN ({", ".join(literal(value) for value in values)})')
    for column, value in partition.items():
        predicates.append(f'A."{identifier(column)}" = {literal(value)}')
    if predicates:
        query += " WHERE " + " AND ".join(predicates)
    if not envelope:
        return query

    # Control rows are calculated by the source in the SAME SELECT statement,
    # including a TOTAL marker for empty authoritative partitions.
    groups = tuple(dict.fromkeys([*table.keys, *table.amounts.values()]))
    projection = ", ".join(f'E."{column}"' for column in table.columns)
    control = ", ".join(
        f'SUM(E."{column}")' if column in table.measures
        else f'E."{column}"' if column in groups else "NULL"
        for column in table.columns
    )
    nulls = ", ".join("NULL" for _ in table.columns)
    group_by = ", ".join(f'E."{column}"' for column in groups)
    return (
        f"WITH E AS ({query}) "
        f"SELECT {projection}, 'DATA' AS \"__ROW_KIND\", CAST(1 AS BIGINT) AS \"__ROW_COUNT\" FROM E "
        f"UNION ALL SELECT {control}, 'CONTROL', COUNT(*) FROM E GROUP BY {group_by} "
        f"UNION ALL SELECT {nulls}, 'TOTAL', COUNT(*) FROM E"
    )


@dataclass(frozen=True)
class ExtractionWork:
    work_id: str
    run_id: str
    table_name: str
    mode: str
    partition: dict[str, str]
    checkpoint_start: str
    checkpoint_end: str
    advances_checkpoint: bool
    source_query: str
    sink_folder: str
    scope: str
    checkpoint_scope: str

    def to_dict(self) -> dict:
        return dict(self.__dict__)


def plan_extractions(
    config: ExtractionConfig, run_id: str, upper_utc: datetime, operation: str,
    checkpoints: Mapping[str, str], *,
    requested_partitions: Sequence[dict] = (),
    last_reconciled: Mapping[str, datetime] | None = None,
) -> list[ExtractionWork]:
    """Select bounded logical work, never infer a checkpoint from returned rows."""
    if not SAFE_VALUE.fullmatch(run_id):
        raise ValueError("run_id must be a nonempty safe token of at most 40 characters")
    if upper_utc.tzinfo is None:
        raise ValueError("Upper extraction boundary must be timezone aware")
    if operation not in {"INITIALIZE", "INCREMENTAL", "FULL_SNAPSHOT", "PARTITION_REFRESH", "RECONCILE"}:
        raise ValueError(f"Unknown operation {operation!r}")
    if requested_partitions and operation not in {"PARTITION_REFRESH", "RECONCILE"}:
        raise ValueError("Explicit partitions require refresh or reconciliation")
    for item in requested_partitions:
        if not isinstance(item, dict) or set(item) != {"table", "partition"}:
            raise ValueError("Each partition request must contain table and partition")
        target = config.table(item["table"])
        partition = item["partition"]
        if not target.partition_columns or not isinstance(partition, dict) or set(partition) != set(target.partition_columns):
            raise ValueError(f"{target.name}: complete supported partition mapping is required")
        for value in partition.values():
            literal(value)
    upper = upper_utc.astimezone(source_zone(config.source_timezone)).strftime("%Y%m%d%H%M%S")
    jobs = []
    for table in config.tables:
        selections: list[tuple[ExtractionMode, dict[str, str], bool]] = []
        if operation in {"PARTITION_REFRESH", "RECONCILE"}:
            if not table.partition_columns:
                continue
            if requested_partitions:
                partitions = [
                    item["partition"] for item in requested_partitions
                    if item["table"].upper() == table.name
                ]
            else:
                partitions = _partitions(table)
                if operation == "RECONCILE":
                    partitions = [
                        partition for partition in partitions
                        if reconciliation_due(config, table, partition, upper_utc, last_reconciled or {})
                    ]
            selections.extend((ExtractionMode.PARTITION_REFRESH, partition, False) for partition in partitions)
        elif operation == "INITIALIZE" and table.discovery:
            selections.extend((ExtractionMode.PARTITION_REFRESH, partition, True) for partition in _partitions(table))
        else:
            mode = ExtractionMode.FULL_SNAPSHOT if operation == "FULL_SNAPSHOT" else table.mode
            selections.append((mode, {}, False))
        for mode, partition, initial in selections:
            boundary = None
            checkpoint = checkpoints.get(table.name.lower(), "")
            if mode == ExtractionMode.DOCUMENT_INCREMENTAL:
                if not checkpoint:
                    raise ValueError(f"{table.name}: initial load required before incremental extraction")
                boundary = EntryBoundary.window(
                    checkpoint[:8], checkpoint[8:], upper_utc,
                    overlap_hours=config.overlap_hours, source_timezone=config.source_timezone,
                )
            advances = initial or mode == ExtractionMode.DOCUMENT_INCREMENTAL
            work_id = str(uuid.uuid5(uuid.NAMESPACE_URL, json_text([run_id, table.name, mode, partition])))
            pieces = "/".join(f"{key.lower()}={value}" for key, value in sorted(partition.items()))
            folder = table.sink_folder + "/"
            if pieces:
                folder += pieces + "/"
            folder += f"run_id={run_id}/work_id={work_id}"
            jobs.append(ExtractionWork(
                work_id, run_id, table.name.lower(), mode.value, dict(partition),
                boundary.start if boundary else "", upper if advances else "",
                advances, build_query(table, mode, boundary, partition, envelope=True),
                folder, config.fingerprint, config.checkpoint_scope(table),
            ))
    if len(jobs) > 1000:
        raise ValueError("Split extraction requests into at most 1000 logical partitions per run")
    if len({job.work_id for job in jobs}) != len(jobs):
        raise ValueError("Duplicate extraction partitions")
    return jobs


def _partitions(table: TableContract) -> list[dict[str, str]]:
    if not table.partition_columns or set(table.partition_values) != set(table.partition_columns):
        raise ValueError(f"{table.name}: initial partitionValues are required for every logical partition column")
    return [
        dict(zip(table.partition_columns, values))
        for values in itertools.product(*(table.partition_values[column] for column in table.partition_columns))
    ]


def reconciliation_due(
    config: ExtractionConfig, table: TableContract, partition: Mapping[str, str],
    now: datetime, last_reconciled: Mapping[str, datetime],
) -> bool:
    year_column = next((column for column in ("GJAHR", "RYEAR", "FISCAL_YEAR") if column in partition), None)
    if year_column is None:
        raise ValueError(f"{table.name}: fiscal-year partition mapping is required")
    year = int(partition[year_column])
    if year > config.current_fiscal_year:
        return False
    policy = (
        "CURRENT_FISCAL_YEAR" if year == config.current_fiscal_year else
        "PREVIOUS_FISCAL_YEAR" if year == config.current_fiscal_year - 1 else
        "HISTORICAL_FISCAL_YEARS"
    )
    days = config.reconciliation.get(policy)
    if days is None:
        return False
    last = last_reconciled.get(table.name.lower() + ":" + json_text(partition))
    return last is None or now - last >= timedelta(days=days)


def typed_value(value: SCALAR) -> list:
    if value is None:
        return ["null", None]
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("Non-finite financial amount")
        text = format(value, "f")
        if "." in text:
            text = text.rstrip("0").rstrip(".")
        return ["decimal", "0" if value == 0 else text]
    if isinstance(value, datetime):
        return ["datetime", value.isoformat()]
    if isinstance(value, date):
        return ["date", value.isoformat()]
    if isinstance(value, bool):
        return ["bool", value]
    if isinstance(value, int):
        return ["int", str(value)]
    if isinstance(value, str):
        return ["string", value.strip()]
    raise ValueError(f"Unsupported source value type: {type(value).__name__}; floats are not financial values")


def canonical_hash(row: Mapping[str, SCALAR], columns: Sequence[str]) -> str:
    return hashlib.sha256(json_text([[column, typed_value(row.get(column))] for column in columns]).encode()).hexdigest()


def row_key(row: Mapping[str, SCALAR], keys: Sequence[str]) -> str:
    if any(row.get(key) is None or str(row[key]).strip() == "" for key in keys):
        raise ValueError(f"Null business key: {keys}")
    return json_text([typed_value(row[key]) for key in keys])


def compare_rows(
    old: Sequence[Row], new: Sequence[Row], keys: Sequence[str], columns: Sequence[str], *,
    authoritative: bool,
) -> dict[str, list[Row]]:
    def index(rows: Sequence[Row]) -> dict[str, Row]:
        result = {}
        for row in rows:
            key = row_key(row, keys)
            if key in result:
                raise ValueError(f"Duplicate business key {key}")
            result[key] = row
        return result
    before, after = index(old), index(new)
    return {
        "insert": [row for key, row in after.items() if key not in before],
        "update": [
            row for key, row in after.items()
            if key in before and canonical_hash(row, columns) != canonical_hash(before[key], columns)
        ],
        "delete": [row for key, row in before.items() if key not in after] if authoritative else [],
    }


def normalize_row(table: TableContract, row: Mapping[str, object]) -> Row:
    result: Row = {}
    for column in table.columns:
        value = row.get(column)
        if isinstance(value, float):
            raise ValueError(f"{table.name}.{column}: floating-point source values are not accepted")
        value = str(value).strip() if value is not None else None
        if value == "":
            value = None
        data_type = table.types.get(column, "string")
        if value is not None:
            if data_type.startswith("decimal"):
                value = Decimal(value)
                if not value.is_finite():
                    raise ValueError(f"{table.name}.{column}: non-finite amount")
                scale = int(data_type.rstrip(")").split(",")[1])
                precision = int(data_type[8:].split(",")[0])
                with localcontext() as context:
                    context.prec = 80
                    exact = value.quantize(Decimal(1).scaleb(-scale))
                    if abs(exact) >= Decimal(10) ** (precision - scale):
                        raise ValueError(f"{table.name}.{column}: decimal precision overflow")
                if value != exact:
                    raise ValueError(f"{table.name}.{column}: decimal scale loss")
                value = exact
            elif data_type == "int":
                value = int(value)
            elif data_type == "date":
                value = date.fromisoformat(value) if "-" in value else datetime.strptime(value, "%Y%m%d").date()
        result[column] = value
    for column in table.required:
        if result[column] is None:
            raise ValueError(f"{table.name}: missing required {column}")
    for column in ("GJAHR", "RYEAR", "FISCAL_YEAR"):
        if column in result and result[column] is not None:
            if not table.fiscal_year_range[0] <= int(result[column]) <= table.fiscal_year_range[1]:
                raise ValueError(f"{table.name}: invalid fiscal year in {column}")
    for amount, currency in table.amounts.items():
        if result[amount] is None or result[currency] is None:
            raise ValueError(f"{table.name}: amount/currency pair {amount}/{currency} is required")
    return result


def validate_envelope(table: TableContract, rows: Sequence[Mapping[str, object]]) -> dict[str, int]:
    """Small-fixture equivalent of distributed source-control reconciliation."""
    total = [row for row in rows if row.get("__ROW_KIND") == "TOTAL"]
    data = [normalize_row(table, row) for row in rows if row.get("__ROW_KIND") == "DATA"]
    controls = [row for row in rows if row.get("__ROW_KIND") == "CONTROL"]
    if len(total) != 1 or len(data) != int(str(total[0]["__ROW_COUNT"])):
        raise ValueError(f"{table.name}: source row-count reconciliation failed")
    if len(data) + len(controls) + len(total) != len(rows):
        raise ValueError(f"{table.name}: unknown envelope record kind")
    groups = tuple(dict.fromkeys([*table.keys, *table.amounts.values()]))
    actual = {}
    for row in data:
        key = row_key(row, groups)
        if key in actual:
            raise ValueError(f"{table.name}: duplicate business key reconciliation failed")
        actual[key] = row
    expected = {}
    for raw in controls:
        row = dict(raw)
        for column in groups:
            data_type = table.types.get(column)
            if data_type == "int":
                row[column] = int(str(row[column]))
            elif data_type == "date":
                text = str(row[column])
                row[column] = date.fromisoformat(text) if "-" in text else datetime.strptime(text, "%Y%m%d").date()
        key = row_key(row, groups)
        if key in expected or int(str(raw["__ROW_COUNT"])) != 1:
            raise ValueError(f"{table.name}: duplicate source control reconciliation failed")
        expected[key] = raw
    if actual.keys() != expected.keys():
        raise ValueError(f"{table.name}: missing-record reconciliation failed")
    for key, row in actual.items():
        for amount in table.measures:
            left = row[amount]
            right = expected[key].get(amount)
            if right == "":
                right = None
            if left is None or right is None:
                if left is right:
                    continue
                raise ValueError(f"{table.name}: null amount reconciliation failed for {amount}")
            with localcontext() as context:
                context.prec = 80
                difference = abs(Decimal(str(left)) - Decimal(str(right)))
            if difference > table.tolerance:
                raise ValueError(f"{table.name}: financial reconciliation failed for {amount}")
    return {"source_rows": len(data), "control_rows": len(controls)}
