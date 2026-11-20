"""Fabric adapter for the existing Copy -> Silver -> Gold pipeline.

Only the Copy activity reads SAP. This adapter manages immutable Bronze
receipts, distributed validation, atomic Delta publication and commit records.
Spark and Delta imports are deferred so SQL contracts remain locally testable.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import datetime, timezone

from sap_extraction import (
    ExtractionConfig,
    ExtractionMode,
    TableContract,
    json_text,
    plan_extractions,
)

LOGGER = logging.getLogger(__name__)
CONTROL_SCHEMA = (
    "WORK_ID string, RUN_ID string, TABLE_NAME string, STATUS string, "
    "PLAN_JSON string, METRICS_JSON string, UPDATED_UTC timestamp"
)
CHANGE_SCHEMA = (
    "WORK_ID string, RUN_ID string, TABLE_NAME string, KEY_JSON string, CHANGE_TYPE string"
)


class FabricExtractionRuntime:
    """Use one source-system writer and the existing schema-enabled Lakehouses."""

    def __init__(
        self, spark, files, configuration: dict, table_config: str = "", *,
        lakehouse: str = "lh_brz_slv_finance",
    ) -> None:
        from delta.tables import DeltaTable
        from pyspark.sql import functions as F, types as T

        self.spark, self.files = spark, files
        spark.conf.set("spark.sql.session.timeZone", "UTC")
        self.F, self.T, self.DeltaTable = F, T, DeltaTable
        configuration = dict(configuration)
        if table_config.strip():
            configuration["tables"] = json.loads(table_config)
        self.configuration = configuration
        self.config = ExtractionConfig.from_dict(configuration)
        self.lakehouse = lakehouse
        prefix = lakehouse + "." if lakehouse else ""
        self.audit = prefix + "audit"
        self.silver = prefix + "slv"
        self.quarantine = prefix + "quarantine"
        self.control = f"{self.audit}.extraction_batch"
        self.changes = f"{self.audit}.extraction_changes"
        for namespace in ("slv", "quarantine", "audit"):
            spark.sql(f"CREATE SCHEMA IF NOT EXISTS {prefix}{namespace}")
        if not spark.catalog.tableExists(self.control):
            seed = spark.createDataFrame(
                [("__LOCK__", "", "__CONTROL__", "AVAILABLE", "{}", "{}", self.now())],
                CONTROL_SCHEMA,
            )
            try:
                seed.write.format("delta").mode("errorifexists").saveAsTable(self.control)
            except Exception as error:
                if "already exists" not in str(error).lower() and "ALREADY_EXISTS" not in str(error):
                    raise
                LOGGER.warning("Another planner initialized the extraction control table")
        if not spark.catalog.tableExists(self.changes):
            try:
                (spark.createDataFrame([], CHANGE_SCHEMA).write.format("delta")
                 .mode("errorifexists").saveAsTable(self.changes))
            except Exception as error:
                if "already exists" not in str(error).lower() and "ALREADY_EXISTS" not in str(error):
                    raise
                LOGGER.warning("Another planner initialized the affected-key journal")

    @staticmethod
    def now() -> datetime:
        return datetime.now(timezone.utc).replace(tzinfo=None)

    def _retry(self, action):
        for attempt in range(5):
            try:
                return action()
            except Exception as error:
                description = type(error).__name__ + ":" + str(error)
                conflicts = (
                    "ConcurrentAppendException", "ConcurrentWriteException",
                    "ConcurrentDeleteReadException", "MetadataChangedException",
                    "DELTA_CONCURRENT", "DELTA_METADATA_CHANGED",
                )
                if attempt == 4 or not any(name in description for name in conflicts):
                    raise
                LOGGER.warning("Retrying a Delta control conflict, attempt %d", attempt + 1)
                time.sleep(0.2 * (2 ** attempt))
        raise RuntimeError("Delta control retry limit exceeded")

    def _save(self, plan: dict, status: str, metrics: dict | None = None) -> None:
        self._save_batch([(plan, status, metrics or {})])

    def _save_batch(self, entries: list[tuple[dict, str, dict]]) -> None:
        """Persist a complete new plan in one Delta transaction."""
        if not entries:
            return
        frame = self.spark.createDataFrame(
            [(
                plan["work_id"], plan["run_id"], plan["table_name"], status,
                json_text(plan), json_text(metrics), self.now(),
            ) for plan, status, metrics in entries],
            CONTROL_SCHEMA,
        )
        self._retry(lambda: (
            self.DeltaTable.forName(self.spark, self.control).alias("t")
            .merge(frame.alias("s"), "t.WORK_ID = s.WORK_ID")
            .whenMatchedUpdateAll().whenNotMatchedInsertAll().execute()
        ))

    def _record(self, work_id: str) -> dict:
        records = (
            self.spark.table(self.control)
            .filter(self.F.col("WORK_ID") == work_id).limit(2).collect()
        )
        if len(records) != 1:
            raise RuntimeError(f"Expected one durable work record for {work_id}")
        return records[0].asDict()

    def _run_records(self, run_id: str) -> list[dict]:
        frame = (
            self.spark.table(self.control)
            .filter(
                (self.F.col("RUN_ID") == run_id) & (self.F.col("WORK_ID") != "__LOCK__")
                & (self.F.col("TABLE_NAME") != "__RUN__")
            )
        )
        records = frame.limit(1001).collect()
        if len(records) > 1000:
            raise RuntimeError("A run exceeded the bounded extraction plan")
        return [row.asDict() for row in records]

    @staticmethod
    def _header_id(run_id: str) -> str:
        return str(uuid.uuid5(uuid.NAMESPACE_URL, "sap-extraction-run:" + run_id))

    def _acquire(self, run_id: str) -> None:
        F = self.F
        self._retry(lambda: self.DeltaTable.forName(self.spark, self.control).update(
            (F.col("WORK_ID") == "__LOCK__")
            & ((F.col("STATUS") == "AVAILABLE") | (F.col("RUN_ID") == run_id)),
            {"RUN_ID": F.lit(run_id), "STATUS": F.lit("LOCKED"), "UPDATED_UTC": F.lit(self.now())},
        ))
        lock = self._record("__LOCK__")
        if lock["RUN_ID"] != run_id or lock["STATUS"] != "LOCKED":
            raise RuntimeError(
                f"Source writer is owned by unfinished run {lock['RUN_ID']}. "
                "Resume that run with p_run_id; do not start a competing extraction."
            )

    def _assert_owner(self, run_id: str) -> None:
        lock = self._record("__LOCK__")
        if lock["RUN_ID"] != run_id or lock["STATUS"] != "LOCKED":
            raise RuntimeError(f"Run {run_id} does not own the source writer")

    def plan(
        self, run_id: str, upper_utc: str, operation: str,
        requested_partitions: str = "[]", max_parallelism: int = 2,
    ) -> dict:
        if not 1 <= int(max_parallelism) <= self.config.max_concurrency:
            raise ValueError(f"p_max_parallelism must be between 1 and {self.config.max_concurrency}")
        upper = datetime.fromisoformat(upper_utc.replace("Z", "+00:00"))
        if upper.tzinfo is None:
            raise ValueError("run_upper_bound_utc must include its UTC offset")
        requested = json.loads(requested_partitions)
        if not isinstance(requested, list):
            raise ValueError("p_partitions must be an array")
        # Validate before taking a durable writer lock.
        from sap_extraction import SAFE_VALUE
        if not SAFE_VALUE.fullmatch(run_id):
            raise ValueError("Invalid run_id")
        self._acquire(run_id)
        existing = self._run_records(run_id)
        headers = (
            self.spark.table(self.control)
            .filter(self.F.col("WORK_ID") == self._header_id(run_id)).limit(2).collect()
        )
        resuming = bool(headers)
        if existing or headers:
            if len(headers) != 1:
                raise RuntimeError("Incomplete durable run plan: missing or duplicate run header")
            header = json.loads(headers[0]["PLAN_JSON"])
            if set(header["work_ids"]) != {row["WORK_ID"] for row in existing}:
                raise RuntimeError("Incomplete durable run plan: planned work membership differs")
            if header["scope"] != self.config.fingerprint or header["operation"] != operation:
                raise ValueError("A replay must retain its original operation and extraction contract")
            plans = [json.loads(row["PLAN_JSON"]) for row in existing]
            if any(plan["scope"] != self.config.fingerprint for plan in plans):
                raise ValueError("Cannot replay a run after changing its extraction contract")
            if any(plan["operation"] != operation for plan in plans):
                raise ValueError("A replay must retain its original operation")
        else:
            F = self.F
            compatible = F.lit(False)
            for table in self.config.tables:
                compatible = compatible | (
                    (F.col("TABLE_NAME") == table.name.lower())
                    & (
                        F.get_json_object("PLAN_JSON", "$.checkpoint_scope")
                        == self.config.checkpoint_scope(table)
                    )
                )
            committed = (
                self.spark.table(self.control)
                .filter(
                    (F.col("STATUS") == "COMMITTED") & compatible
                )
            )
            boundaries = (
                committed.filter(F.get_json_object("PLAN_JSON", "$.advances_checkpoint") == "true")
                .groupBy("TABLE_NAME")
                .agg(F.max(F.get_json_object("PLAN_JSON", "$.checkpoint_end")).alias("BOUNDARY"))
                .collect()
            )
            checkpoints = {row["TABLE_NAME"]: row["BOUNDARY"] for row in boundaries}
            refreshed = (
                committed.filter(F.get_json_object("PLAN_JSON", "$.mode") == "PARTITION_REFRESH")
                .select(
                    "TABLE_NAME", "UPDATED_UTC",
                    F.get_json_object("PLAN_JSON", "$.partition").alias("PARTITION"),
                )
                .groupBy("TABLE_NAME", "PARTITION").agg(F.max("UPDATED_UTC").alias("LAST"))
                .collect()
            )
            last = {
                row["TABLE_NAME"] + ":" + json_text(json.loads(row["PARTITION"])):
                row["LAST"].replace(tzinfo=timezone.utc) for row in refreshed
            }
            resolved_operation = operation
            if operation == "AUTO":
                resolved_operation = (
                    "INCREMENTAL" if all(
                        table.name.lower() in checkpoints
                        for table in self.config.tables if table.discovery
                    ) else "INITIALIZE"
                )
            try:
                works = plan_extractions(
                    self.config, run_id, upper, resolved_operation, checkpoints,
                    requested_partitions=requested, last_reconciled=last,
                )
            except ValueError:
                self._retry(lambda: self.DeltaTable.forName(self.spark, self.control).update(
                    (F.col("WORK_ID") == "__LOCK__") & (F.col("RUN_ID") == run_id),
                    {"STATUS": F.lit("AVAILABLE"), "UPDATED_UTC": F.lit(self.now())},
                ))
                raise
            plans = [work.to_dict() | {"operation": operation} for work in works]
            for plan in plans:
                plan["attempt_id"] = str(uuid.uuid4())
                plan["sink_folder"] += "/attempt_id=" + plan["attempt_id"]
            header = {
                "work_id": self._header_id(run_id), "run_id": run_id,
                "table_name": "__RUN__", "scope": self.config.fingerprint,
                "operation": operation, "upper_utc": upper.isoformat(),
                "work_ids": sorted(plan["work_id"] for plan in plans),
            }
            if len(json_text({"run_id": run_id, "work_items": plans}).encode()) > 3_500_000:
                raise ValueError(
                    "Extraction plan exceeds the pipeline exit-value limit; "
                    "split the request and retry this run ID before extraction"
                )
            self._save_batch([
                (header, "PLANNED", {}),
                *((plan, "PLANNED", {}) for plan in plans),
            ])
            existing = self._run_records(run_id)
        items = []
        for record in existing:
            plan = json.loads(record["PLAN_JSON"])
            if record["STATUS"] in {"PLANNED", "FAILED"}:
                if resuming:
                    plan.setdefault("attempt_history", []).append({
                        "attempt_id": plan["attempt_id"],
                        "sink_folder": plan["sink_folder"],
                        "status": record["STATUS"],
                        "metrics": json.loads(record["METRICS_JSON"]),
                        "updated_utc": record["UPDATED_UTC"].isoformat(),
                    })
                    base = plan["sink_folder"].rsplit("/attempt_id=", 1)[0]
                    plan["attempt_id"] = str(uuid.uuid4())
                    plan["sink_folder"] = base + "/attempt_id=" + plan["attempt_id"]
                    self._save(plan, "PLANNED")
                items.append({key: value for key, value in plan.items() if key != "attempt_history"})
        output = {"run_id": run_id, "work_items": items}
        if len(json_text(output).encode()) > 3_500_000:
            raise ValueError("Extraction plan exceeds the pipeline exit-value limit; split the request")
        return output

    def _raw(self, plan: dict):
        path = "Files/" + plan["sink_folder"] + "/data.csv"
        table = self.config.table(plan["table_name"])
        expected = [*table.columns, "__ROW_KIND", "__ROW_COUNT"]
        frame = (
            self.spark.read.option("header", True).option("inferSchema", False)
            .option("mode", "FAILFAST").option("multiLine", True).csv(path)
        )
        for name in frame.columns:
            if name.startswith("\ufeff"):
                frame = frame.withColumnRenamed(name, name.lstrip("\ufeff"))
        if frame.columns != expected:
            raise ValueError(f"{table.name}: Bronze header mismatch; expected {expected}, found {frame.columns}")
        return frame

    def receipt(self, work_id: str, copy_output: str) -> dict:
        record = self._record(work_id)
        plan = json.loads(record["PLAN_JSON"])
        if plan["scope"] != self.config.fingerprint:
            raise ValueError("Receipt contract differs from the durable extraction plan")
        self._assert_owner(plan["run_id"])
        if record["STATUS"] in {"EXTRACTED", "PUBLISHED", "COMMITTED"}:
            return json.loads(record["METRICS_JSON"])
        output = json.loads(copy_output)
        raw = None
        try:
            raw = self._raw(plan)
            count = raw.count()
            if output.get("rowsRead") != count or output.get("rowsCopied") != count:
                raise ValueError(
                    f"Copy row reconciliation failed: read={output.get('rowsRead')}, "
                    f"copied={output.get('rowsCopied')}, landed={count}"
                )
            table = self.config.table(plan["table_name"])
            data, controls = self._validated_frames(table, raw)
            totals = self._reconcile(table, data, controls, raw)
            metrics = {
                **totals, "rows_transferred": count,
                "extracted_at_utc": self.now().isoformat() + "Z",
                "bytes_read": output.get("dataRead"), "bytes_written": output.get("dataWritten"),
                "copy_duration_seconds": output.get("copyDuration"),
                "throughput": output.get("throughput"),
                "retry_count": output.get("retryCount"),
                "copy_execution_details": output.get("executionDetails"),
                "source_peak_connections": output.get("sourcePeakConnections"),
                "source_query_duration_seconds": output.get("sourceQueryDuration"),
            }
            self._save(plan, "EXTRACTED", metrics)
            return metrics
        except Exception as error:
            self._save(plan, "FAILED", {"error": str(error)[:2000], "copy": output})
            if raw is not None:
                (
                    raw.filter(self.F.col("__ROW_KIND") == "DATA")
                    .withColumn("BATCH_RUN_ID", self.F.lit(plan["run_id"]))
                    .withColumn("WORK_ID", self.F.lit(work_id))
                    .withColumn(
                        "SOURCE_FILE_NAME",
                        self.F.lit("Files/" + plan["sink_folder"] + "/data.csv"),
                    )
                    .withColumn("DQ_FAILURE_REASON", self.F.lit("REJECTED_EXTRACTION:" + str(error)[:1000]))
                    .write.format("delta").mode("append").option("mergeSchema", "true")
                    .saveAsTable(f"{self.quarantine}.{plan['table_name']}")
                )
            raise

    def _cast(self, column: str, data_type: str):
        F = self.F
        raw = F.when(F.trim(F.col(column)) == "", None).otherwise(F.trim(F.col(column)))
        if data_type == "date":
            return F.when(
                raw.rlike(r"^\d{8}$"),
                F.expr(
                    f"try_cast(concat(substr(trim(`{column}`),1,4),'-',"
                    f"substr(trim(`{column}`),5,2),'-',substr(trim(`{column}`),7,2)) as date)"
                ),
            ).otherwise(F.expr(f"try_cast(trim(`{column}`) as date)"))
        if data_type == "string":
            return raw
        return F.expr(f"try_cast(`{column}` as {data_type})")

    def _validated_frames(self, table: TableContract, raw):
        F = self.F
        if raw.filter(~F.col("__ROW_KIND").isin("DATA", "CONTROL", "TOTAL") | F.col("__ROW_KIND").isNull()).limit(1).count():
            raise ValueError(f"{table.name}: unknown source envelope record")
        data_raw = raw.filter(F.col("__ROW_KIND") == "DATA")
        converted = data_raw.select(*[
            self._cast(column, table.types.get(column, "string")).alias(column)
            for column in table.columns
        ])
        failures = []
        for column in table.required:
            failures.append(F.col(column).isNull())
        from functools import reduce
        if failures and converted.filter(reduce(lambda a, b: a | b, failures)).limit(1).count():
            raise ValueError(f"{table.name}: required key or business value is null")
        for column, data_type in table.types.items():
            original = F.trim(F.col(column))
            cast_failed = original.isNotNull() & (original != "") & self._cast(column, data_type).isNull()
            if data_type.startswith("decimal"):
                scale = int(data_type.rstrip(")").split(",")[1])
                fraction = F.regexp_extract(original, r"\.(\d+)$", 1)
                cast_failed = cast_failed | (
                    F.substring(fraction, scale + 1, 1000).rlike("[1-9]")
                )
            if data_raw.filter(cast_failed).limit(1).count():
                raise ValueError(f"{table.name}.{column}: invalid value or precision loss")
        for column in ("POPER", "MONAT", "FISCAL_PERIOD"):
            if column in table.columns and converted.filter(~F.col(column).between(0, 16)).limit(1).count():
                raise ValueError(f"{table.name}: invalid fiscal period in {column}")
        for column in ("GJAHR", "RYEAR", "FISCAL_YEAR"):
            if column in table.columns and converted.filter(
                ~F.col(column).between(*table.fiscal_year_range)
            ).limit(1).count():
                raise ValueError(f"{table.name}: invalid fiscal year in {column}")
        if "CPUTM" in table.columns:
            invalid = (
                ~F.col("CPUTM").rlike(r"^\d{6}$")
                | (F.substring("CPUTM", 1, 2).cast("int") > 23)
                | (F.substring("CPUTM", 3, 2).cast("int") > 59)
                | (F.substring("CPUTM", 5, 2).cast("int") > 59)
            )
            if converted.filter(invalid).limit(1).count():
                raise ValueError(f"{table.name}: invalid document-entry time")
        if {"DATAB", "DATBI"} <= set(table.columns):
            if converted.filter(F.col("DATAB") > F.col("DATBI")).limit(1).count():
                raise ValueError(f"{table.name}: inverted source validity interval")
        if "DRCRK" in table.columns:
            if converted.filter(~F.col("DRCRK").isin("S", "H")).limit(1).count():
                raise ValueError(f"{table.name}: invalid debit/credit indicator")
        for amount, currency in table.amounts.items():
            if converted.filter(F.col(amount).isNull() | F.col(currency).isNull()).limit(1).count():
                raise ValueError(f"{table.name}: missing {amount}/{currency}")
        if converted.groupBy(*table.keys).count().filter("count > 1").limit(1).count():
            raise ValueError(f"{table.name}: duplicate source business keys")
        controls = raw.filter(F.col("__ROW_KIND") == "CONTROL").select(
            *[self._cast(column, table.types.get(column, "string")).alias(column) for column in table.columns],
            F.expr("try_cast(`__ROW_COUNT` as bigint)").alias("__ROW_COUNT"),
        )
        return converted, controls

    def _reconcile(self, table: TableContract, data, controls, raw=None) -> dict:
        F = self.F
        groups = list(dict.fromkeys([*table.keys, *table.amounts.values()]))
        if controls.filter(F.col("__ROW_COUNT").isNull() | (F.col("__ROW_COUNT") != 1)).limit(1).count():
            raise ValueError(f"{table.name}: nonunique or invalid source controls")
        if controls.groupBy(*groups).count().filter("count > 1").limit(1).count():
            raise ValueError(f"{table.name}: duplicate source controls")
        actual = data.groupBy(*groups).agg(
            F.count("*").alias("__ROW_COUNT"),
            *[F.sum(amount).alias(amount) for amount in table.measures],
        )
        expected = controls.select(*groups, "__ROW_COUNT", *table.measures)
        joined = actual.alias("a").join(expected.alias("e"), groups, "full")
        mismatch = (
            F.col("a.__ROW_COUNT").isNull() | F.col("e.__ROW_COUNT").isNull()
            | (F.col("a.__ROW_COUNT") != F.col("e.__ROW_COUNT"))
        )
        for amount in table.measures:
            mismatch = (
                mismatch
                | (F.col(f"a.{amount}").isNull() != F.col(f"e.{amount}").isNull())
                | F.coalesce(
                    F.abs(F.col(f"a.{amount}") - F.col(f"e.{amount}")) > F.lit(table.tolerance),
                    F.lit(False),
                )
            )
        if joined.filter(mismatch).limit(1).count():
            diagnostics = {
                "key_or_document_groups": joined.filter(
                    F.col("a.__ROW_COUNT").isNull() | F.col("e.__ROW_COUNT").isNull()
                    | (F.col("a.__ROW_COUNT") != F.col("e.__ROW_COUNT"))
                ).count(),
            }
            for amount in table.measures:
                diagnostics[amount] = joined.filter(
                    (F.col(f"a.{amount}").isNull() != F.col(f"e.{amount}").isNull())
                    | F.coalesce(
                        F.abs(F.col(f"a.{amount}") - F.col(f"e.{amount}")) > F.lit(table.tolerance),
                        F.lit(False),
                    )
                ).count()
            raise ValueError(
                f"{table.name}: reconciliation failed; mismatched group counts="
                f"{json_text(diagnostics)}; tolerance={table.tolerance}"
            )
        count = data.count()
        if raw is not None:
            markers = raw.filter(F.col("__ROW_KIND") == "TOTAL").select("__ROW_COUNT").limit(2).collect()
            if len(markers) != 1 or int(markers[0]["__ROW_COUNT"]) != count:
                raise ValueError(f"{table.name}: complete partition row-count reconciliation failed")
        return {
            "source_rows": count, "target_rows": count, "reconciled": True,
            "financial_amount_currencies": dict(table.amounts),
            "scalar_amount_columns": list(table.scalar_amounts),
            "financial_tolerance": str(table.tolerance),
            "business_key": list(table.keys),
        }

    def _scope(self, table: TableContract, plan: dict, alias: str = ""):
        F = self.F
        prefix = alias + "." if alias else ""
        predicate = F.lit(True)
        for column, value in plan["partition"].items():
            predicate = predicate & (F.col(prefix + column) == F.lit(value).cast(table.types.get(column, "string")))
        for column, values in table.filters.items():
            predicate = predicate & F.col(prefix + column).isin(list(values))
        return predicate

    def _hash(self, table: TableContract):
        return self.F.sha2(
            self.F.to_json(self.F.struct(*[
                self.F.col(column).alias(column) for column in table.columns
            ]), {"ignoreNullFields": "false"}), 256,
        )

    def _journal(self, table: TableContract, plan: dict, frame, change_type: str) -> None:
        F = self.F
        journal = frame.select(
            F.lit(plan["work_id"]).alias("WORK_ID"), F.lit(plan["run_id"]).alias("RUN_ID"),
            F.lit(table.name.lower()).alias("TABLE_NAME"),
            F.to_json(F.struct(*table.keys), {"ignoreNullFields": "false"}).alias("KEY_JSON"),
            F.lit(change_type).alias("CHANGE_TYPE"),
        )
        self._retry(lambda: (
            self.DeltaTable.forName(self.spark, self.changes).alias("t")
            .merge(
                journal.alias("s"),
                "t.WORK_ID=s.WORK_ID AND t.KEY_JSON=s.KEY_JSON AND t.CHANGE_TYPE=s.CHANGE_TYPE",
            )
            .whenNotMatchedInsertAll().execute()
        ))

    def publish(self, work_id: str) -> dict:
        F = self.F
        record = self._record(work_id)
        plan, metrics = json.loads(record["PLAN_JSON"]), json.loads(record["METRICS_JSON"])
        if plan["scope"] != self.config.fingerprint:
            raise ValueError("Silver contract differs from the durable extraction plan")
        self._assert_owner(plan["run_id"])
        if record["STATUS"] in {"PUBLISHED", "COMMITTED"}:
            return metrics
        if record["STATUS"] != "EXTRACTED":
            raise RuntimeError(f"{work_id}: source extraction is not durably validated")
        table = self.config.table(plan["table_name"])
        target = f"{self.silver}.{table.name.lower()}"
        started = time.monotonic()
        raw = self._raw(plan)
        data, controls = self._validated_frames(table, raw)
        self._reconcile(table, data, controls, raw)
        if data.filter(~self._scope(table, plan)).limit(1).count():
            raise ValueError(f"{table.name}: source row outside the authoritative scope")
        if "baseline_version" not in metrics:
            metrics["baseline_version"] = (
                self.DeltaTable.forName(self.spark, target).history(1).select("version").first()[0]
                if self.spark.catalog.tableExists(target) else -1
            )
            self._save(plan, "EXTRACTED", metrics)
        baseline = metrics["baseline_version"]
        old = (
            self.spark.read.format("delta").option("versionAsOf", baseline).table(target)
            if baseline >= 0 else data.limit(0)
        )
        for column in table.columns:
            if column not in old.columns:
                old = old.withColumn(column, F.lit(None).cast(table.types.get(column, "string")))
        old = old.filter(self._scope(table, plan)).select(*table.columns)
        if old.groupBy(*table.keys).count().filter("count > 1").limit(1).count():
            raise ValueError(f"{target}: existing duplicate SAP keys require repair")
        old_hashed, new_hashed = old.withColumn("_HASH", self._hash(table)), data.withColumn("_HASH", self._hash(table))
        inserted = new_hashed.join(old_hashed.select(*table.keys), list(table.keys), "left_anti").drop("_HASH")
        updated = (
            new_hashed.alias("n").join(old_hashed.alias("o"), list(table.keys))
            .filter(F.col("n._HASH") != F.col("o._HASH"))
            .select("n.*").drop("_HASH")
        )
        authoritative = plan["mode"] in {"PARTITION_REFRESH", "SNAPSHOT_COMPARE"}
        deleted = old.join(data.select(*table.keys), list(table.keys), "left_anti") if authoritative else old.limit(0)
        self._journal(table, plan, inserted, "INSERT")
        self._journal(table, plan, updated, "UPDATE")
        self._journal(table, plan, deleted, "DELETE")
        lineage = {
            "BATCH_RUN_ID": plan["run_id"], "SOURCE_SYSTEM": self.config.source_system,
            "SOURCE_SCHEMA": table.schema, "SOURCE_TABLE": table.source_table,
            "EXTRACTION_MODE": plan["mode"], "PARTITION_ID": json_text(plan["partition"]),
            "CHECKPOINT_START": plan["checkpoint_start"] or None,
            "CHECKPOINT_END": plan["checkpoint_end"] or None,
            "SOURCE_FILE_NAME": "Files/" + plan["sink_folder"] + "/data.csv",
        }
        extracted_utc = datetime.fromisoformat(
            metrics["extracted_at_utc"].replace("Z", "+00:00")
        ).astimezone(timezone.utc).replace(tzinfo=None)
        source = data.withColumn("ROW_HASH", self._hash(table))
        for column, value in lineage.items():
            source = source.withColumn(column, F.lit(value).cast("string"))
        source = (
            source.withColumn("EXTRACTED_AT_UTC", F.lit(extracted_utc))
            .withColumn("EXTRACT_DATE", F.lit(extracted_utc.date()))
            .withColumn("LOAD_DATE", F.current_date()).withColumn("DQ_PASSED", F.lit(True))
            .withColumn("DQ_FAILURE_REASON", F.lit(None).cast("string"))
            .withColumn("DQ_WARNING", F.lit(None).cast("string"))
        )
        if not self.spark.catalog.tableExists(target):
            source.limit(0).write.format("delta").mode("errorifexists").saveAsTable(target)
        else:
            fields = {item.name for item in self.spark.table(target).schema}
            additions = [item for item in source.schema if item.name not in fields]
            if additions:
                ddl = ", ".join(f"`{item.name}` {item.dataType.simpleString()}" for item in additions)
                self.spark.sql(f"ALTER TABLE {target} ADD COLUMNS ({ddl})")
        condition = " AND ".join(f"t.`{key}` = s.`{key}`" for key in table.keys)
        applied = False
        try:
            merge = (
                self.DeltaTable.forName(self.spark, target).alias("t")
                .merge(source.alias("s"), condition)
                .whenMatchedUpdateAll(condition="t.ROW_HASH IS NULL OR t.ROW_HASH <> s.ROW_HASH")
                .whenNotMatchedInsertAll()
            )
            if authoritative:
                merge = merge.whenNotMatchedBySourceDelete(condition=self._scope(table, plan, "t"))
            merge.execute()
            applied = True
            published = self.spark.table(target).filter(self._scope(table, plan))
            if not authoritative:
                published = published.join(data.select(*table.keys), list(table.keys), "inner")
            self._reconcile(table, published, controls)
            if (
                published.select(*table.columns).withColumn("_HASH", self._hash(table))
                .select(*table.keys, "_HASH")
                .exceptAll(new_hashed.select(*table.keys, "_HASH")).limit(1).count()
            ):
                raise ValueError(f"{target}: published business values differ from validated Bronze")
        except Exception:
            if applied:
                if baseline >= 0:
                    self.DeltaTable.forName(self.spark, target).restoreToVersion(baseline)
                else:
                    self.DeltaTable.forName(self.spark, target).delete()
                LOGGER.error("Restored last valid state of %s after publication validation failure", target)
            raise
        metrics.update(
            inserted=inserted.count(), updated=updated.count(), deleted=deleted.count(),
            silver_duration_ms=round((time.monotonic() - started) * 1000),
        )
        self._save(plan, "PUBLISHED", metrics)
        return metrics

    def publish_run(self, run_id: str) -> list[dict]:
        self._assert_owner(run_id)
        records = self._run_records(run_id)
        header = self._record(self._header_id(run_id))
        if set(json.loads(header["PLAN_JSON"])["work_ids"]) != {row["WORK_ID"] for row in records}:
            raise RuntimeError("Incomplete durable run plan; cannot publish a partial membership")
        outcomes, failures = [], []
        for record in records:
            try:
                outcomes.append(self.publish(record["WORK_ID"]))
            except Exception as error:
                message = f"{record['TABLE_NAME']} / {record['WORK_ID']}: {error}"
                LOGGER.exception("Extraction partition publication failed: %s", message)
                failures.append(message)
                plan = json.loads(record["PLAN_JSON"])
                metrics = json.loads(self._record(record["WORK_ID"])["METRICS_JSON"])
                metrics["error"] = str(error)[:2000]
                # Preserve a validated immutable extraction for retry; never
                # turn a failed Copy into an authoritative empty partition.
                status = "EXTRACTED" if record["STATUS"] == "EXTRACTED" else "FAILED"
                self._save(plan, status, metrics)
        if failures:
            raise RuntimeError(
                "Independent healthy partitions were preserved; no checkpoints committed. "
                "Resume the same p_run_id: " + " | ".join(failures)
            )
        header = self._record(self._header_id(run_id))
        if header["STATUS"] != "COMMITTED":
            self._save(
                json.loads(header["PLAN_JSON"]), "PUBLISHED",
                {"published_partitions": len(outcomes)},
            )
        return outcomes

    def commit(self, run_id: str) -> dict:
        """Called only after Gold, MLV and end-to-end validation succeed."""
        self._assert_owner(run_id)
        records = self._run_records(run_id)
        header = self._record(self._header_id(run_id))
        expected = set(json.loads(header["PLAN_JSON"])["work_ids"])
        if expected != {row["WORK_ID"] for row in records}:
            raise RuntimeError("Cannot commit incomplete durable run membership")
        if header["STATUS"] not in {"PUBLISHED", "COMMITTED"}:
            raise RuntimeError("Cannot commit incomplete run publication; all Silver scopes must be acknowledged")
        failures = [row["WORK_ID"] for row in records if row["STATUS"] not in {"PUBLISHED", "COMMITTED"}]
        if failures:
            raise RuntimeError(f"Cannot advance checkpoints for incomplete work: {failures}")
        F = self.F
        self._retry(lambda: self.DeltaTable.forName(self.spark, self.control).update(
            (F.col("RUN_ID") == run_id) & (F.col("STATUS") == "PUBLISHED"),
            {"STATUS": F.lit("COMMITTED"), "UPDATED_UTC": F.lit(self.now())},
        ))
        self._retry(lambda: self.DeltaTable.forName(self.spark, self.control).update(
            (F.col("WORK_ID") == "__LOCK__") & (F.col("RUN_ID") == run_id),
            {"STATUS": F.lit("AVAILABLE"), "UPDATED_UTC": F.lit(self.now())},
        ))
        return {"run_id": run_id, "committed_partitions": len(records), "bronze_retained": True}
