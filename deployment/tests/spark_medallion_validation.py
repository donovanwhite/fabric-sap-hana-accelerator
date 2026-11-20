"""Execute deployed Gold notebook cells over the complete synthetic seed.

Local Spark has no Fabric lakehouse catalog. The test removes only the two
known lakehouse prefixes at the catalog boundary; transformation code and
Delta operations run unchanged. MLV SELECT results are materialized locally,
not claimed as validation of Fabric's MLV DDL or refresh service.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
import warnings
from contextlib import ExitStack, contextmanager
from pathlib import Path
from unittest.mock import patch

from delta.tables import DeltaTable
from pyspark.sql import SparkSession, types as T
from pyspark.sql.catalog import Catalog
from pyspark.sql.readwriter import DataFrameReader, DataFrameWriter

from fabric_sap_deployer.definitions import notebook_definition
from sap_extraction import ExtractionConfig
from sap_runtime import FabricExtractionRuntime
from test_financial_serving import mlv_selects

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "artefacts" / "sap"))
from synthetic_scenarios import load_seed_rows  # noqa: E402


def local_name(value: str) -> str:
    for name in ("lh_brz_slv_finance", "lh_gld_finance"):
        for prefix in (name, f"`{name}`", f'"{name}"'):
            value = value.replace(prefix + ".", "")
    return value


@contextmanager
def fabric_namespace_adapter():
    sql, table = SparkSession.sql, SparkSession.table
    reader, writer = DataFrameReader.table, DataFrameWriter.saveAsTable
    exists, list_tables = Catalog.tableExists, Catalog.listTables
    delta = DeltaTable.forName
    with ExitStack() as stack:
        stack.enter_context(patch.object(
            SparkSession, "sql",
            lambda self, sqlQuery, *args, **kwargs: sql(self, local_name(sqlQuery), *args, **kwargs),
        ))
        stack.enter_context(patch.object(
            SparkSession, "table", lambda self, tableName: table(self, local_name(tableName)),
        ))
        stack.enter_context(patch.object(
            DataFrameReader, "table", lambda self, tableName: reader(self, local_name(tableName)),
        ))
        stack.enter_context(patch.object(
            DataFrameWriter, "saveAsTable",
            lambda self, name, *args, **kwargs: writer(self, local_name(name), *args, **kwargs),
        ))
        stack.enter_context(patch.object(
            Catalog, "tableExists",
            lambda self, tableName, *args, **kwargs: exists(self, local_name(tableName), *args, **kwargs),
        ))
        stack.enter_context(patch.object(
            Catalog, "listTables",
            lambda self, dbName=None, *args, **kwargs:
            list_tables(self, local_name(dbName) if dbName else dbName, *args, **kwargs),
        ))
        stack.enter_context(patch.object(
            DeltaTable, "forName",
            classmethod(lambda cls, sparkSession, tableOrViewName:
                        delta(sparkSession, local_name(tableOrViewName))),
        ))
        yield


class CompleteMedallionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        warnings.filterwarnings("ignore", message=r"unclosed <socket.socket", category=ResourceWarning)
        cls.spark = (
            SparkSession.builder.appName("sap-complete-medallion")
            .config("spark.sql.shuffle.partitions", "1")
            .config("spark.default.parallelism", "1")
            .config("spark.databricks.delta.snapshotPartitions", "1")
            .config("spark.sql.ansi.enabled", "true")
            .config("spark.sql.ansi.doubleQuotedIdentifiers", "true")
            .config("spark.sql.warehouse.dir", tempfile.mkdtemp(prefix="sap-full-warehouse-"))
            .getOrCreate()
        )
        cls.spark.sparkContext.setLogLevel("ERROR")
        for namespace in ("audit", "slv", "quarantine", "dim", "fct", "mlv", "SAPABAP1", "SAPDEMO"):
            if cls.spark.catalog.databaseExists(namespace):
                raise RuntimeError(f"Refusing an existing test namespace: {namespace}")
            cls.spark.sql(f"CREATE DATABASE {namespace}")
        cls.payload = json.loads((ROOT / "artefacts/sap/factory-copy-contract.json").read_text())
        cls.config = ExtractionConfig.from_dict(cls.payload)
        cls.seed = load_seed_rows(ROOT / "artefacts/sap_synthetic_data/sap")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.spark.stop()

    def copy_and_publish_sources(self) -> None:
        for table in self.config.tables:
            schema = T.StructType([
                T.StructField(
                    column,
                    T.DecimalType(*map(int, table.types[column][8:-1].split(",")))
                    if table.types.get(column, "").startswith("decimal") else T.StringType(),
                    True,
                )
                for column in table.columns
            ])
            rows = [
                tuple(row.get(column) for column in table.columns)
                for row in self.seed[table.name]
            ]
            (self.spark.createDataFrame(rows, schema).write.format("delta")
             .mode("errorifexists").saveAsTable(f"{table.schema}.{table.source_table}"))
        self.runtime = FabricExtractionRuntime(self.spark, None, self.payload, lakehouse="")
        planned = self.runtime.plan("complete-seed", "2026-11-19T14:28:06Z", "FULL_SNAPSHOT")
        for plan in planned["work_items"]:
            frame = self.spark.sql(plan["source_query"]).cache()
            count = frame.count()
            root = Path("Files") / plan["sink_folder"]
            root.mkdir(parents=True, exist_ok=True)
            (frame.coalesce(1).write.option("header", True).option("quoteAll", True)
             .csv(str(root / "copy")))
            next((root / "copy").glob("part-*.csv")).replace(root / "data.csv")
            frame.unpersist()
            self.runtime.receipt(plan["work_id"], json.dumps({
                "rowsRead": count, "rowsCopied": count,
                "dataRead": (root / "data.csv").stat().st_size,
                "dataWritten": (root / "data.csv").stat().st_size,
            }))
        self.runtime.publish_run("complete-seed")

    def execute_notebook(self, name: str) -> None:
        definition = notebook_definition(
            ROOT / "artefacts/medallion" / f"{name}.ipynb",
            workspace_id="11111111-1111-1111-1111-111111111111",
            default_lakehouse_id="22222222-2222-2222-2222-222222222222",
            default_lakehouse_name="lh_gld_finance",
            known_lakehouse_ids=[],
            extraction_contract=self.payload,
        )
        import base64
        notebook = json.loads(base64.b64decode(definition["parts"][0]["payload"]))
        scope = {"spark": self.spark, "display": lambda *args, **kwargs: None}
        for index, cell in enumerate(notebook["cells"]):
            if cell["cell_type"] != "code":
                continue
            source = "".join(cell["source"])
            if source.lstrip().startswith("%"):
                raise RuntimeError(f"Unexpected notebook magic in executable Gold cell {name}:{index}")
            exec(compile(source, f"{name}:cell-{index}", "exec"), scope)
            if "parameters" in cell.get("metadata", {}).get("tags", []):
                scope["run_id"] = "complete-seed"
        print(f"NOTEBOOK_EVIDENCE: {name} PASS")

    def test_complete_source_to_gold_and_serving_replay(self) -> None:
        self.copy_and_publish_sources()
        with fabric_namespace_adapter():
            for name in [
                "03_Gold_SCD2_Dimensions",
                "04_Gold_Atomic_Finance_Fact",
                "05_Gold_Budget_Fact",
            ]:
                self.execute_notebook(name)
            self.assertEqual(self.spark.table("fct.finance_transaction").count(), len(self.seed["ACDOCA"]))
            for name, query in mlv_selects().items():
                (self.spark.sql(query).write.format("delta").mode("errorifexists")
                 .saveAsTable("mlv." + name))
            self.execute_notebook("07_End_to_End_Validation")
            before_dimensions = {
                item.name: self.spark.table("dim." + item.name).count()
                for item in self.spark.catalog.listTables("dim")
            }
            before_finance = DeltaTable.forName(
                self.spark, "fct.finance_transaction"
            ).history(1).first()["version"]
            for name in [
                "03_Gold_SCD2_Dimensions",
                "04_Gold_Atomic_Finance_Fact",
                "05_Gold_Budget_Fact",
            ]:
                self.execute_notebook(name)
            self.assertEqual(before_dimensions, {
                item.name: self.spark.table("dim." + item.name).count()
                for item in self.spark.catalog.listTables("dim")
            })
            financial_columns = [
                "RCLNT", "RLDNR", "RBUKRS", "GJAHR", "BELNR", "DOCLN",
                "RHCUR", "RKCUR", "RTCUR", "RWCUR",
                "AMOUNT_LOCAL", "AMOUNT_GROUP", "AMOUNT_TRANSACTION", "AMOUNT_OTHER",
            ]
            old = (
                self.spark.read.option("versionAsOf", before_finance)
                .table("fct.finance_transaction").select(*financial_columns)
            )
            current = self.spark.table("fct.finance_transaction").select(*financial_columns)
            self.assertEqual(old.exceptAll(current).count(), 0)
            self.assertEqual(current.exceptAll(old).count(), 0)
            self.execute_notebook("07_End_to_End_Validation")
            result = self.runtime.commit("complete-seed")
            self.assertEqual(result["committed_partitions"], 13)
            print("FULL_MEDALLION_EVIDENCE: " + json.dumps({
                "source_acdoca_rows": len(self.seed["ACDOCA"]),
                "silver_acdoca_rows": self.spark.table("slv.acdoca").count(),
                "gold_finance_rows": current.count(),
                "gold_budget_rows": self.spark.table("fct.budget").count(),
                "dimension_rows": before_dimensions,
                "mlv_selects": list(mlv_selects()),
                "financial_reconciliation": "PASS",
                "replay_business_state": "UNCHANGED",
                "fabric_mlv_ddl_executed": False,
            }, sort_keys=True))


if __name__ == "__main__":
    unittest.main(verbosity=2)
