"""Focused deployment definition tests."""

from __future__ import annotations

import base64
import json
import unittest
from pathlib import Path

from fabric_sap_deployer.definitions import (
    data_agent_definition,
    directory_definition,
    notebook_definition,
    pipeline_definition,
    sql_database_definition,
)

ROOT = Path(__file__).resolve().parents[2]
ARTEFACTS = ROOT / "artefacts"


def decode_part(definition: dict, path: str) -> bytes:
    """Decode one definition part by path."""
    item = next(part for part in definition["parts"] if part["path"] == path)
    return base64.b64decode(item["payload"])


class DefinitionTests(unittest.TestCase):
    """Verify all environment-specific definition transformations."""

    def test_notebook_definition_rebinds_default_lakehouse(self) -> None:
        definition = notebook_definition(
            ARTEFACTS / "medallion" / "01_Bronze_CSV_Inventory.ipynb",
            workspace_id="11111111-1111-1111-1111-111111111111",
            default_lakehouse_id="22222222-2222-2222-2222-222222222222",
            default_lakehouse_name="bronze",
            known_lakehouse_ids=[
                "22222222-2222-2222-2222-222222222222",
                "33333333-3333-3333-3333-333333333333",
            ],
        )
        notebook = json.loads(
            decode_part(definition, "artifact.content.ipynb").decode("utf-8")
        )
        binding = notebook["metadata"]["dependencies"]["lakehouse"]
        self.assertEqual(binding["default_lakehouse_name"], "bronze")
        self.assertTrue(binding["default_lakehouse"].startswith("2222"))
        self.assertEqual(len(binding["known_lakehouses"]), 2)

    def test_pipeline_definition_rebinds_every_dependency(self) -> None:
        notebook_ids = {
            path.stem: f"00000000-0000-0000-0000-{index:012d}"
            for index, path in enumerate(
                sorted((ARTEFACTS / "medallion").glob("*.ipynb")), start=1
            )
        }
        definition = pipeline_definition(
            ARTEFACTS / "pipeline" / "pipeline-content.json",
            workspace_id="11111111-1111-1111-1111-111111111111",
            notebook_ids=notebook_ids,
            sql_server="test.database.fabric.microsoft.com",
            sql_database="SAP_FIN_DB-test",
        )
        pipeline = json.loads(
            decode_part(definition, "pipeline-content.json").decode("utf-8")
        )
        serialized = json.dumps(pipeline)
        self.assertNotIn("0448ed4f-990e-4171-83f1-ea2a293e12d9", serialized)
        self.assertIn("test.database.fabric.microsoft.com", serialized)
        self.assertNotIn("externalReferences", serialized)
        for notebook_id in notebook_ids.values():
            self.assertIn(notebook_id, serialized)

    def test_sql_project_contains_schema_and_all_seed_tables(self) -> None:
        definition = sql_database_definition(ARTEFACTS)
        sql = decode_part(definition, "Scripts/Deploy.sql").decode("utf-8")
        self.assertIn("CREATE TABLE SAPABAP1.ACDOCA", sql)
        self.assertIn("MERGE SAPABAP1.ACDOCA", sql)
        self.assertIn("MERGE SAPDEMO.GL_ACCOUNT_REFERENCE", sql)
        self.assertNotIn("LAST_MODIFIED_UTC", sql)

    def test_data_agent_contains_instructions_and_ten_examples(self) -> None:
        definition = data_agent_definition(
            ARTEFACTS / "dataAgent" / "dataAgent.md",
            workspace_id="11111111-1111-1111-1111-111111111111",
            gold_lakehouse_id="22222222-2222-2222-2222-222222222222",
        )
        fewshots = json.loads(
            decode_part(
                definition,
                (
                    "Files/Config/draft/"
                    "lakehouse-tables-lh_gld_finance/fewshots.json"
                ),
            ).decode("utf-8")
        )
        self.assertEqual(len(fewshots["fewShots"]), 10)
        self.assertTrue(
            all(
                example["query"].startswith(("WITH", "SELECT"))
                for example in fewshots["fewShots"]
            )
        )

    def test_semantic_and_report_definitions_exclude_local_only_files(self) -> None:
        semantic = directory_definition(
            ARTEFACTS / "semantic" / "SAP Finance.SemanticModel",
            format_name="TMDL",
            replacements={
                "__GOLD_SQL_SERVER__": "test.datawarehouse.fabric.microsoft.com",
                "__GOLD_SQL_DATABASE__": "lh_gld_finance",
            },
        )
        semantic_paths = {item["path"] for item in semantic["parts"]}
        self.assertIn("definition/model.tmdl", semantic_paths)
        self.assertNotIn("model-manifest.json", semantic_paths)
        expression = decode_part(
            semantic, "definition/expressions.tmdl"
        ).decode("utf-8")
        self.assertIn("Sql.Database", expression)
        self.assertNotIn("__GOLD_SQL_SERVER__", expression)

        report = directory_definition(
            ARTEFACTS / "semantic" / "SAP Finance Executive Insights.Report",
            format_name="PBIR",
            replacements={},
        )
        report_paths = {item["path"] for item in report["parts"]}
        self.assertIn("definition.pbir", report_paths)
        self.assertIn("definition/report.json", report_paths)


if __name__ == "__main__":
    unittest.main()
