"""Run real local Spark/Delta recovery tests in the isolated test container."""

from __future__ import annotations

import csv
import json
import tempfile
import time
import unittest
import warnings
import sys
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from pyspark.sql import SparkSession, types as T

from sap_extraction import ExtractionConfig, build_query, ExtractionMode, EntryBoundary
from sap_runtime import FabricExtractionRuntime

ROOT = Path(__file__).resolve().parents[2]


class SparkRecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        warnings.filterwarnings("ignore", message=r"unclosed <socket.socket", category=ResourceWarning)
        cls.spark = (
            SparkSession.builder.appName("sap-finance-regression")
            .config("spark.sql.shuffle.partitions", "1")
            .config("spark.default.parallelism", "1")
            .config("spark.databricks.delta.snapshotPartitions", "1")
            .config("spark.sql.ansi.enabled", "true")
            .config("spark.sql.ansi.doubleQuotedIdentifiers", "true")
            .config("spark.sql.warehouse.dir", tempfile.mkdtemp(prefix="sap-warehouse-"))
            .getOrCreate()
        )
        cls.spark.sparkContext.setLogLevel("ERROR")
        for namespace in ("audit", "slv", "quarantine", "SAPABAP1"):
            if cls.spark.catalog.databaseExists(namespace):
                raise RuntimeError(f"Refusing to overwrite an existing local test namespace: {namespace}")
        cls.spark.sql("CREATE DATABASE SAPABAP1")
        payload = json.loads((ROOT / "artefacts/sap/factory-copy-contract.json").read_text())
        payload["tables"] = [
            table for table in payload["tables"] if table["name"] in {"ACDOCA", "BKPF", "T001"}
        ]
        for table in payload["tables"]:
            for column in table.get("partitionValues", {}):
                if column in {"RBUKRS", "BUKRS"}:
                    table["partitionValues"][column] = ["1000"]
        cls.payload = payload
        cls.config = ExtractionConfig.from_dict(payload)
        cls.source = {"ACDOCA": [], "BKPF": [], "T001": [
            {"MANDT": "100", "BUKRS": "1000", "BUTXT": "Original",
             "ORT01": "Demo", "LAND1": "ZA", "WAERS": "ZAR", "KTOPL": "DEMO"},
        ]}
        cls.add_document("1", "2026", "20261101", "20261101", "090000")
        cls.write_source()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.spark.stop()

    @classmethod
    def add_document(
        cls, number: str, year: str, posting: str, entry_date: str, entry_time: str,
        ledgers: tuple[str, ...] = ("0L",),
    ) -> None:
        cls.source["BKPF"].append({
            "MANDT": "100", "BUKRS": "1000", "GJAHR": year, "BELNR": number,
            "MONAT": "11", "BUDAT": posting, "BLDAT": posting,
            "CPUDT": entry_date, "CPUTM": entry_time, "BLART": "SA",
            "STBLG": None, "STJAH": None, "BSTAT": None, "BKTXT": "Synthetic",
        })
        for ledger in ledgers:
            for line, sign in (("000001", Decimal("1")), ("000002", Decimal("-1"))):
                cls.source["ACDOCA"].append({
                    "RCLNT": "100", "RLDNR": ledger, "RBUKRS": "1000", "GJAHR": year,
                    "POPER": "11", "BELNR": number, "DOCLN": line, "RACCT": "0000500100",
                    "RCNTR": None, "PRCTR": None, "KOKRS": "DEMO", "LIFNR": None,
                    "KUNNR": None, "BUDAT": posting, "BLDAT": posting,
                    "DRCRK": "S" if sign > 0 else "H", "BLART": "SA", "AWTYP": "SYNTH",
                    "SGTXT": "Synthetic", "RHCUR": "ZAR", "RKCUR": "USD",
                    "RTCUR": "EUR", "RWCUR": "GBP",
                    "HSL": sign * Decimal("1000000000000.01"),
                    "KSL": sign * Decimal("1.23"), "TSL": sign * Decimal("2.34"),
                    "WSL": sign * Decimal("3.45"), "MSL": sign * Decimal("1.001"),
                })

    @classmethod
    def write_source(cls) -> None:
        for table in cls.config.tables:
            schema = T.StructType([
                T.StructField(
                    column,
                    T.DecimalType(
                        *map(int, table.types[column][8:-1].split(","))
                    ) if table.types.get(column, "").startswith("decimal") else T.StringType(),
                    True,
                )
                for column in table.columns
            ])
            rows = [
                tuple(row.get(column) for column in table.columns)
                for row in cls.source[table.name]
            ]
            (cls.spark.createDataFrame(rows, schema).write.format("delta").mode("overwrite")
             .option("overwriteSchema", "true").saveAsTable(f"SAPABAP1.{table.name}"))

    def new_runtime(self) -> FabricExtractionRuntime:
        return FabricExtractionRuntime(self.spark, None, self.payload, lakehouse="")

    def extract(self, runtime, plan: dict, corrupt: bool = False) -> dict:
        started = time.monotonic()
        output = self.spark.sql(plan["source_query"]).cache()
        count = output.count()
        folder = Path("Files") / plan["sink_folder"]
        folder.mkdir(parents=True, exist_ok=True)
        intermediate = folder / "copy"
        (output.coalesce(1).write.mode("errorifexists")
         .option("header", True).option("quoteAll", True).csv(str(intermediate)))
        file = next(intermediate.glob("part-*.csv"))
        file.replace(folder / "data.csv")
        output.unpersist()
        if corrupt:
            with (folder / "data.csv").open(newline="") as stream:
                reader = csv.DictReader(stream)
                columns, rows = reader.fieldnames, list(reader)
            for row in rows:
                if row["__ROW_KIND"] == "DATA":
                    row["HSL"] = str(Decimal(row["HSL"]) + Decimal("0.01"))
                    break
            with (folder / "data.csv").open("w", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=columns)
                writer.writeheader()
                writer.writerows(rows)
        return runtime.receipt(plan["work_id"], json.dumps({
            "rowsRead": count, "rowsCopied": count,
            "dataRead": (folder / "data.csv").stat().st_size,
            "dataWritten": (folder / "data.csv").stat().st_size,
            "copyDuration": time.monotonic() - started,
        }))

    def test_hybrid_publication_durability_and_recovery(self) -> None:
        runtime = self.new_runtime()
        initial = runtime.plan("initial", "2026-11-15T09:00:00Z", "INITIALIZE")
        self.assertEqual(len(initial["work_items"]), 5)
        missing_work = runtime._record(initial["work_items"][0]["work_id"])
        runtime.DeltaTable.forName(self.spark, runtime.control).delete(
            runtime.F.col("WORK_ID") == missing_work["WORK_ID"]
        )
        with self.assertRaisesRegex(RuntimeError, "Incomplete durable run plan"):
            runtime.plan("initial", "2026-11-20T09:00:00Z", "INITIALIZE")
        runtime._save(json.loads(missing_work["PLAN_JSON"]), "PLANNED")
        for plan in initial["work_items"]:
            self.extract(runtime, plan)
        runtime.publish_run("initial")
        self.assertEqual(self.spark.table("slv.acdoca").count(), 2)
        self.assertEqual(
            self.spark.table("audit.extraction_batch").filter("STATUS='COMMITTED'").count(), 0
        )
        runtime.commit("initial")

        self.add_document("2", "2025", "20251031", "20261116", "090000")
        self.add_document("3", "2026", "20261101", "20261116", "090000", ("0L", "2L"))
        self.source["T001"][0]["BUTXT"] = "Changed"
        self.write_source()
        incremental = runtime.plan("delta", "2026-11-16T09:00:00Z", "INCREMENTAL")
        failed = None
        for plan in incremental["work_items"]:
            if plan["table_name"] == "acdoca":
                failed = deepcopy(plan)
                with self.assertRaisesRegex(ValueError, "reconciliation"):
                    self.extract(runtime, plan, corrupt=True)
            else:
                self.extract(runtime, plan)
        with self.assertRaisesRegex(RuntimeError, "no checkpoints"):
            runtime.publish_run("delta")
        self.assertEqual(self.spark.table("slv.acdoca").count(), 2)
        self.assertEqual(self.spark.table("slv.t001").first()["BUTXT"], "Changed")
        with self.assertRaisesRegex(RuntimeError, "incomplete"):
            runtime.commit("delta")
        with self.assertRaisesRegex(RuntimeError, "unfinished run delta"):
            runtime.plan("competing", "2026-11-17T09:00:00Z", "INCREMENTAL")

        # A new adapter instance reads the durable plan, not Python state.
        runtime = self.new_runtime()
        resumed = runtime.plan("delta", "2026-11-20T09:00:00Z", "INCREMENTAL")
        self.assertEqual(len(resumed["work_items"]), 1)
        work = resumed["work_items"][0]
        self.assertNotEqual(work["sink_folder"], failed["sink_folder"])
        self.assertEqual(work["checkpoint_end"], "20261116090000")
        self.assertTrue((Path("Files") / failed["sink_folder"] / "data.csv").exists())
        history = json.loads(runtime._record(work["work_id"])["PLAN_JSON"])["attempt_history"]
        self.assertEqual(history[-1]["sink_folder"], failed["sink_folder"])
        self.assertEqual(history[-1]["status"], "FAILED")
        self.extract(runtime, work)

        # Simulate lost acknowledgement after a successful atomic Silver MERGE.
        save = runtime._save
        def lose_publication_ack(plan, status, metrics=None):
            if status == "PUBLISHED":
                raise RuntimeError("Simulated interruption after MERGE")
            return save(plan, status, metrics)
        runtime._save = lose_publication_ack
        with self.assertRaisesRegex(RuntimeError, "interruption"):
            runtime.publish(work["work_id"])
        runtime._save = save
        journal_count = self.spark.table("audit.extraction_changes").filter("RUN_ID='delta'").count()
        runtime.publish_run("delta")
        self.assertEqual(self.spark.table("slv.acdoca").count(), 8)
        self.assertEqual(
            self.spark.table("audit.extraction_changes").filter("RUN_ID='delta'").count(),
            journal_count,
        )
        runtime.commit("delta")
        replay = runtime.plan("delta", "2026-11-21T09:00:00Z", "INCREMENTAL")
        self.assertEqual(replay["work_items"], [])
        runtime.publish_run("delta")
        runtime.commit("delta")
        self.assertEqual(self.spark.table("slv.acdoca").count(), 8)

        for row in self.source["ACDOCA"]:
            if row["BELNR"] == "1":
                row["HSL"] += Decimal("0.10") if row["HSL"] > 0 else Decimal("-0.10")
        self.write_source()
        undiscovered = build_query(
            self.config.table("ACDOCA"), ExtractionMode.DOCUMENT_INCREMENTAL,
            EntryBoundary("20261114090000", "20261121090000"),
        )
        self.assertEqual(self.spark.sql(undiscovered).filter("BELNR='1'").count(), 0)
        request = json.dumps([{
            "table": "ACDOCA",
            "partition": {"GJAHR": "2026", "RBUKRS": "1000", "RLDNR": "0L"},
        }])
        adjusted = runtime.plan("adjusted", "2026-11-21T09:00:00Z", "PARTITION_REFRESH", request)
        self.extract(runtime, adjusted["work_items"][0])
        changes = runtime.publish_run("adjusted")
        self.assertEqual(changes[0]["updated"], 2)
        self.assertEqual(changes[0]["deleted"], 0)
        runtime.commit("adjusted")

        # No new entry timestamp: discovery cannot detect this physical removal.
        self.source["ACDOCA"] = [
            row for row in self.source["ACDOCA"]
            if not (row["GJAHR"] == "2026" and row["RLDNR"] == "0L")
        ]
        self.write_source()
        refresh = runtime.plan("refresh", "2026-11-22T09:00:00Z", "PARTITION_REFRESH", request)
        self.assertEqual(len(refresh["work_items"]), 1)
        self.extract(runtime, refresh["work_items"][0])
        runtime.publish_run("refresh")
        self.assertEqual(self.spark.table("slv.acdoca").count(), 4)
        self.assertEqual(self.spark.table("slv.acdoca").filter("GJAHR=2025").count(), 2)
        self.assertEqual(self.spark.table("slv.acdoca").filter("RLDNR='2L'").count(), 2)
        runtime.commit("refresh")

        # Empty entry windows still commit the captured upper boundary.
        self.source["T001"] = []
        self.source["BKPF"] = []
        self.write_source()
        operational = deepcopy(self.payload)
        operational["maxConcurrency"] = 3
        operational["reconciliation"] = {}
        runtime = FabricExtractionRuntime(self.spark, None, operational, lakehouse="")
        empty = runtime.plan("empty", "2027-01-01T09:00:00Z", "AUTO")
        self.assertEqual(len(empty["work_items"]), 3)
        for work in empty["work_items"]:
            metrics = self.extract(runtime, work)
            if work["mode"] == "DOCUMENT_INCREMENTAL":
                self.assertEqual(metrics["source_rows"], 0)
        runtime.publish_run("empty")
        self.assertEqual(self.spark.table("slv.t001").count(), 0)
        self.assertEqual(self.spark.table("slv.acdoca").count(), 4)
        runtime.commit("empty")
        records = runtime._run_records("empty")
        self.assertTrue(all(record["STATUS"] == "COMMITTED" for record in records))
        for record in records:
            plan = json.loads(record["PLAN_JSON"])
            if plan["advances_checkpoint"]:
                self.assertEqual(plan["checkpoint_end"], "20270101090000")
        noop = runtime.plan("not-due", "2027-01-02T09:00:00Z", "RECONCILE")
        self.assertEqual(noop["work_items"], [])
        runtime.publish_run("not-due")
        self.assertEqual(runtime.commit("not-due")["committed_partitions"], 0)
        self.assertEqual(
            runtime.plan("not-due", "2030-01-01T09:00:00Z", "RECONCILE")["work_items"], [],
        )
        header = json.loads(runtime._record(runtime._header_id("not-due"))["PLAN_JSON"])
        self.assertEqual(header["upper_utc"], "2027-01-02T09:00:00+00:00")
        runtime.publish_run("not-due")
        runtime.commit("not-due")
        print("SPARK_EVIDENCE: exact-decimal controls, partial failure, immutable retry, "
              "lost MERGE acknowledgement, persisted checkpoint, tied entries, multiple ledgers, "
              "prior-year posting, replay, scoped empty partition and confirmed master deletion PASS")

        self.measure_source_volume()

    def measure_source_volume(self) -> None:
        """Measure actual CSV output, including controls, over the retained seed."""
        sys.path.insert(0, str(ROOT / "artefacts/sap"))
        from synthetic_scenarios import apply_scenario, load_seed_rows
        seed = load_seed_rows(ROOT / "artefacts/sap_synthetic_data/sap")
        latest = max(
            datetime.strptime(row["CPUDT"] + row["CPUTM"], "%Y%m%d%H%M%S")
            for row in seed["BKPF"]
        ).replace(tzinfo=timezone.utc)
        entered = latest + timedelta(days=30)
        source = apply_scenario(seed, "new_documents", entered)
        type(self).source = {table.name: source[table.name] for table in self.config.tables}
        self.write_source()
        table = self.config.table("ACDOCA")
        window = EntryBoundary(
            (entered - timedelta(hours=48)).strftime("%Y%m%d%H%M%S"),
            entered.strftime("%Y%m%d%H%M%S"),
        )
        requests = [
            ("FULL_SNAPSHOT", build_query(table, ExtractionMode.FULL_SNAPSHOT, envelope=True)),
            ("DOCUMENT_INCREMENTAL", build_query(
                table, ExtractionMode.DOCUMENT_INCREMENTAL, window, envelope=True,
            )),
            ("PARTITION_REFRESH", build_query(
                table, ExtractionMode.PARTITION_REFRESH, None,
                {"GJAHR": "2025", "RBUKRS": "1000", "RLDNR": "0L"}, envelope=True,
            )),
        ]
        measurements = {}
        for name, query in requests:
            started = time.monotonic()
            frame = self.spark.sql(query).cache()
            source_rows = frame.filter("__ROW_KIND='DATA'").count()
            copied_rows = frame.count()
            output = Path(tempfile.mkdtemp(prefix="sap-volume-")) / "csv"
            frame.coalesce(1).write.option("header", True).option("quoteAll", True).csv(str(output))
            byte_count = sum(file.stat().st_size for file in output.glob("part-*.csv"))
            measurements[name] = {
                "source_rows": source_rows, "transferred_rows_including_controls": copied_rows,
                "csv_bytes_including_controls": byte_count,
                "local_spark_query_and_csv_seconds": round(time.monotonic() - started, 3),
            }
            frame.unpersist()
        self.assertGreater(measurements["DOCUMENT_INCREMENTAL"]["source_rows"], 0)
        self.assertLess(
            measurements["DOCUMENT_INCREMENTAL"]["csv_bytes_including_controls"],
            measurements["FULL_SNAPSHOT"]["csv_bytes_including_controls"],
        )
        print("BENCHMARK_EVIDENCE: " + json.dumps({
            "environment": "local Python 3.11 / Spark " + self.spark.version + " / Delta 3.2.1",
            "measurements": measurements,
            "hana_query_plan_or_network_measurement": False,
        }, sort_keys=True))


if __name__ == "__main__":
    unittest.main(verbosity=2)
