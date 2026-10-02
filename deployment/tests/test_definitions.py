"""Focused deployment definition tests."""

from __future__ import annotations

import base64
import json
import unittest
from pathlib import Path

from fabric_sap_deployer.definitions import (
    LEGACY_ENVIRONMENT_IDS,
    OLD_SEMANTIC_MODEL_ID,
    OLD_WORKSPACE_ID,
    data_agent_definition,
    directory_definition,
    notebook_definition,
    pipeline_definition,
    sql_database_definition,
    validate_deployable_definition,
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
        validate_deployable_definition(definition)
        notebook = json.loads(
            decode_part(definition, "artifact.content.ipynb").decode("utf-8")
        )
        binding = notebook["metadata"]["dependencies"]["lakehouse"]
        self.assertEqual(binding["default_lakehouse_name"], "bronze")
        self.assertTrue(binding["default_lakehouse"].startswith("2222"))
        self.assertEqual(len(binding["known_lakehouses"]), 2)

    def test_replay_notebooks_import_delta_table(self) -> None:
        for name in [
            "02_Bronze_CSV_to_Silver_Delta",
            "03_Gold_SCD2_Dimensions",
            "04_Gold_Atomic_Finance_Fact",
            "05_Gold_Budget_Fact",
        ]:
            notebook = json.loads(
                (ARTEFACTS / "medallion" / f"{name}.ipynb").read_text(
                    encoding="utf-8"
                )
            )
            source = "\n".join(
                line
                for cell in notebook["cells"]
                for line in cell.get("source", [])
            )
            self.assertIn("from delta.tables import DeltaTable", source, name)

    def test_setup_initializes_gold_namespaces(self) -> None:
        notebook = json.loads(
            (
                ARTEFACTS
                / "medallion"
                / "00_Setup_and_Namespace_Validation.ipynb"
            ).read_text(encoding="utf-8")
        )
        source = "\n".join(
            line
            for cell in notebook["cells"]
            for line in cell.get("source", [])
        )
        self.assertIn('["dim", "fct"]', source)
        self.assertIn(
            'CREATE SCHEMA IF NOT EXISTS {GLD_LH}.{namespace}',
            source,
        )

    def test_materialized_views_are_replay_safe(self) -> None:
        notebook = json.loads(
            (
                ARTEFACTS
                / "medallion"
                / "06_Gold_Materialized_Lake_Views.ipynb"
            ).read_text(encoding="utf-8")
        )
        source = "\n".join(
            line
            for cell in notebook["cells"]
            for line in cell.get("source", [])
        )
        self.assertEqual(
            source.count("CREATE MATERIALIZED LAKE VIEW"),
            0,
        )
        self.assertGreaterEqual(
            source.count("CREATE OR REPLACE MATERIALIZED LAKE VIEW"),
            1,
        )

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
            sql_database_id="22222222-2222-2222-2222-222222222222",
            bronze_lakehouse_id="33333333-3333-3333-3333-333333333333",
            sql_connection_id="44444444-4444-4444-4444-444444444444",
        )
        pipeline = json.loads(
            decode_part(definition, "pipeline-content.json").decode("utf-8")
        )
        validate_deployable_definition(definition)
        serialized = json.dumps(pipeline)
        self.assertNotIn("0448ed4f-990e-4171-83f1-ea2a293e12d9", serialized)
        self.assertNotIn("nb_source_full_snapshot", serialized)
        self.assertIn("fe_copy_sap_full_snapshot", serialized)
        self.assertIn("cpy_full_table_snapshot", serialized)
        self.assertIn("22222222-2222-2222-2222-222222222222", serialized)
        self.assertIn("33333333-3333-3333-3333-333333333333", serialized)
        self.assertIn("44444444-4444-4444-4444-444444444444", serialized)
        self.assertEqual(
            len(pipeline["properties"]["parameters"]["p_table_config"][
                "defaultValue"
            ]),
            13,
        )
        copy_activity = pipeline["properties"]["activities"][1][
            "typeProperties"
        ]["activities"][0]
        self.assertNotIn(
            "externalReferences",
            copy_activity["typeProperties"]["sink"]["datasetSettings"],
        )
        self.assertNotIn(
            "batchCount",
            pipeline["properties"]["activities"][1]["typeProperties"],
        )
        self.assertEqual(
            copy_activity["typeProperties"]["source"]["sqlReaderQuery"]["value"],
            "@concat('SELECT ',item().columnList,' FROM ',item().sourceObject)",
        )
        for notebook_id in notebook_ids.values():
            self.assertIn(notebook_id, serialized)

    def test_pipeline_template_contains_no_legacy_environment_ids(self) -> None:
        template = (
            ARTEFACTS / "pipeline" / "pipeline-content.json"
        ).read_text(encoding="utf-8")
        for legacy_id in LEGACY_ENVIRONMENT_IDS:
            self.assertNotIn(legacy_id, template)

    def test_definition_validation_rejects_stale_metadata(self) -> None:
        stale = {
            "parts": [
                {
                    "path": "definition.json",
                    "payload": base64.b64encode(
                        json.dumps(
                            {"workspaceId": next(iter(LEGACY_ENVIRONMENT_IDS))}
                        ).encode("utf-8")
                    ).decode("ascii"),
                    "payloadType": "InlineBase64",
                }
            ]
        }
        with self.assertRaisesRegex(ValueError, "legacy IDs"):
            validate_deployable_definition(stale)

    def test_definition_validation_rejects_unresolved_tokens(self) -> None:
        unresolved = {
            "parts": [
                {
                    "path": "definition.json",
                    "payload": base64.b64encode(
                        b'{"workspaceId":"__WORKSPACE_ID__"}'
                    ).decode("ascii"),
                    "payloadType": "InlineBase64",
                }
            ]
        }
        with self.assertRaisesRegex(ValueError, "unresolved tokens"):
            validate_deployable_definition(unresolved)

    def test_sql_project_contains_schema_and_all_seed_tables(self) -> None:
        definition = sql_database_definition(ARTEFACTS)
        validate_deployable_definition(definition)
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
        validate_deployable_definition(definition)
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
        validate_deployable_definition(semantic)
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
            replacements={
                OLD_WORKSPACE_ID: "11111111-1111-1111-1111-111111111111",
                OLD_SEMANTIC_MODEL_ID: (
                    "22222222-2222-2222-2222-222222222222"
                ),
                "ws_finance": "target_workspace",
            },
        )
        validate_deployable_definition(report)
        report_paths = {item["path"] for item in report["parts"]}
        self.assertIn("definition.pbir", report_paths)
        self.assertIn("definition/report.json", report_paths)


if __name__ == "__main__":
    unittest.main()
