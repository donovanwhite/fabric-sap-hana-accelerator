"""Chronological deployment orchestration for the SAP Finance accelerator."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .auth import azure_cli_token
from .definitions import (
    OLD_GOLD_LAKEHOUSE_ID,
    OLD_SEMANTIC_MODEL_ID,
    OLD_WORKSPACE_ID,
    data_agent_definition,
    directory_definition,
    notebook_definition,
    pipeline_definition,
    sql_database_definition,
)
from .fabric import FabricApiError, FabricClient, FabricItem

LOGGER = logging.getLogger(__name__)

NOTEBOOK_NAMES = [
    "00_Source_Full_Snapshot",
    "00_Setup_and_Namespace_Validation",
    "01_Bronze_CSV_Inventory",
    "02_Bronze_CSV_to_Silver_Delta",
    "03_Gold_SCD2_Dimensions",
    "04_Gold_Atomic_Finance_Fact",
    "05_Gold_Budget_Fact",
    "06_Gold_Materialized_Lake_Views",
    "07_End_to_End_Validation",
    "08_Bronze_Snapshot_Cleanup",
]

FOLDER_BY_TYPE = {
    "DataAgent": "agents",
    "SQLDatabase": "database",
    "Lakehouse": "lakehouses",
    "Notebook": "notebooks",
    "DataPipeline": "pipelines",
    "Report": "reports",
    "SemanticModel": "semantic models",
}


@dataclass(frozen=True)
class DeploymentOptions:
    """Deployment input parameters."""

    workspace: str
    overwrite: bool = False
    dry_run: bool = False


class AcceleratorDeployer:
    """Deploy all accelerator items in dependency order."""

    def __init__(
        self,
        repository_root: Path,
        options: DeploymentOptions,
        *,
        client: FabricClient | None = None,
    ) -> None:
        self.repository_root = repository_root
        self.artifacts_root = repository_root / "artefacts"
        self.options = options
        self.client = client or FabricClient(azure_cli_token)
        self.workspace: dict[str, Any] = {}
        self.items: dict[str, FabricItem] = {}
        self.folders: dict[str, str] = {}

    def deploy(self) -> dict[str, FabricItem]:
        """Run the complete deployment."""
        self._validate_local_assets()
        self.workspace = self.client.resolve_workspace(self.options.workspace)
        workspace_id = self.workspace["id"]
        capacity_id = self.workspace.get("capacityId")
        if not capacity_id:
            raise FabricApiError(
                f"Workspace {self.workspace['displayName']!r} is not assigned "
                "to Fabric capacity"
            )
        capacities = {
            capacity["id"]: capacity for capacity in self.client.paged("/capacities")
        }
        capacity = capacities.get(capacity_id)
        if not capacity or capacity.get("state") != "Active":
            state = capacity.get("state") if capacity else "not visible"
            raise FabricApiError(
                f"Workspace capacity {capacity_id} is not active: {state}"
            )
        LOGGER.info(
            "Target workspace: %s (%s), capacity %s",
            self.workspace["displayName"],
            workspace_id,
            capacity_id,
        )
        self._check_conflicts(workspace_id)
        if self.options.dry_run:
            self._print_plan()
            return {}
        self.folders = self._ensure_folders(workspace_id)

        bronze = self._ensure_lakehouse(
            workspace_id,
            "lh_brz_slv_finance",
            "Bronze, Silver, quarantine, and audit Lakehouse",
        )
        gold = self._ensure_lakehouse(
            workspace_id,
            "lh_gld_finance",
            "Governed Gold Finance Lakehouse",
        )
        sql_database = self._ensure_definition_item(
            workspace_id,
            route="sqlDatabases",
            item_type="SQLDatabase",
            display_name="SAP_FIN_DB",
            description="Synthetic SAP Finance source-system simulator",
            definition=sql_database_definition(self.artifacts_root),
            creation_payload={
                "collation": "Latin1_General_100_BIN2_UTF8",
                "creationMode": "new",
            },
        )
        sql_properties = self.client.request(
            "GET",
            f"/workspaces/{workspace_id}/sqlDatabases/{sql_database.id}",
            expected=(200,),
        ).json()["properties"]

        notebook_ids: dict[str, str] = {}
        gold_default_notebooks = {
            "03_Gold_SCD2_Dimensions",
            "04_Gold_Atomic_Finance_Fact",
            "05_Gold_Budget_Fact",
            "06_Gold_Materialized_Lake_Views",
            "07_End_to_End_Validation",
        }
        for name in NOTEBOOK_NAMES:
            default_item = gold if name in gold_default_notebooks else bronze
            definition = notebook_definition(
                self.artifacts_root / "medallion" / f"{name}.ipynb",
                workspace_id=workspace_id,
                default_lakehouse_id=default_item.id,
                default_lakehouse_name=default_item.display_name,
                known_lakehouse_ids=[bronze.id, gold.id],
            )
            notebook = self._ensure_definition_item(
                workspace_id,
                route="notebooks",
                item_type="Notebook",
                display_name=name,
                description=f"SAP Finance accelerator notebook {name}",
                definition=definition,
            )
            notebook_ids[name] = notebook.id

        semantic_model = self._ensure_definition_item(
            workspace_id,
            route="semanticModels",
            item_type="SemanticModel",
            display_name="SAP Finance",
            description="Direct Lake SAP Finance semantic model",
            definition=directory_definition(
                self.artifacts_root / "semantic" / "SAP Finance.SemanticModel",
                format_name="TMDL",
                replacements={
                    OLD_WORKSPACE_ID: workspace_id,
                    OLD_GOLD_LAKEHOUSE_ID: gold.id,
                },
            ),
        )
        self._ensure_definition_item(
            workspace_id,
            route="reports",
            item_type="Report",
            display_name="SAP Finance Executive Insights",
            description="Executive SAP Finance demonstration report",
            definition=directory_definition(
                self.artifacts_root
                / "semantic"
                / "SAP Finance Executive Insights.Report",
                format_name="PBIR",
                replacements={
                    OLD_WORKSPACE_ID: workspace_id,
                    OLD_SEMANTIC_MODEL_ID: semantic_model.id,
                    "ws_finance": self.workspace["displayName"],
                },
            ),
        )
        self._ensure_definition_item(
            workspace_id,
            route="dataAgents",
            item_type="DataAgent",
            display_name="Finance Agent",
            description=(
                "Conversational analysis over synthetic SAP Finance Gold data"
            ),
            definition=data_agent_definition(
                self.artifacts_root / "dataAgent" / "dataAgent.md",
                workspace_id=workspace_id,
                gold_lakehouse_id=gold.id,
            ),
        )

        self._ensure_definition_item(
            workspace_id,
            route="dataPipelines",
            item_type="DataPipeline",
            display_name="pl_full_medallion_sap_finance",
            description="Full-snapshot SAP Finance medallion pipeline",
            definition=pipeline_definition(
                self.artifacts_root / "pipeline" / "pipeline-content.json",
                workspace_id=workspace_id,
                notebook_ids=notebook_ids,
                sql_server=sql_properties["serverFqdn"].replace(",", ":"),
                sql_database=sql_properties["databaseName"],
            ),
        )
        self._validate_inventory(workspace_id)
        return self.items

    def _validate_local_assets(self) -> None:
        required = [
            self.artifacts_root / "pipeline" / "pipeline-content.json",
            self.artifacts_root / "sap" / "schema.sql",
            self.artifacts_root / "semantic" / "SAP Finance.SemanticModel",
            self.artifacts_root / "semantic" / "SAP Finance Executive Insights.Report",
            self.artifacts_root / "dataAgent" / "dataAgent.md",
        ]
        required.extend(
            self.artifacts_root / "medallion" / f"{name}.ipynb"
            for name in NOTEBOOK_NAMES
        )
        missing = [str(path) for path in required if not path.exists()]
        if missing:
            raise FileNotFoundError(
                "Required deployment assets are missing: " + ", ".join(missing)
            )

    def _expected_items(self) -> dict[tuple[str, str], str]:
        expected = {
            ("lh_brz_slv_finance", "Lakehouse"): "Lakehouse",
            ("lh_gld_finance", "Lakehouse"): "Lakehouse",
            ("SAP_FIN_DB", "SQLDatabase"): "SQL Database",
            ("SAP Finance", "SemanticModel"): "semantic model",
            ("SAP Finance Executive Insights", "Report"): "report",
            ("Finance Agent", "DataAgent"): "Data Agent",
            (
                "pl_full_medallion_sap_finance",
                "DataPipeline",
            ): "pipeline",
        }
        for name in NOTEBOOK_NAMES:
            expected[(name, "Notebook")] = "notebook"
        return expected

    def _check_conflicts(self, workspace_id: str) -> None:
        existing = {
            (item.display_name, item.type.casefold()): item
            for item in self.client.list_items(workspace_id)
        }
        conflicts = [
            f"{name} ({item_type})"
            for name, item_type in self._expected_items()
            if (name, item_type.casefold()) in existing
        ]
        if conflicts and not self.options.overwrite:
            raise FabricApiError(
                "Deployment item names already exist. Re-run with --overwrite "
                "to update compatible items: " + ", ".join(conflicts)
            )

    def _print_plan(self) -> None:
        LOGGER.info("Dry-run deployment order:")
        LOGGER.info("  1. Workload folders: %s", ", ".join(FOLDER_BY_TYPE.values()))
        LOGGER.info("  2. Lakehouses: lh_brz_slv_finance, lh_gld_finance")
        LOGGER.info("  3. SQL Database: SAP_FIN_DB with synthetic seed data")
        LOGGER.info("  4. Notebooks: %d", len(NOTEBOOK_NAMES))
        LOGGER.info("  5. Semantic model and report")
        LOGGER.info("  6. Finance Data Agent")
        LOGGER.info("  7. Rebound Data Pipeline")

    def _ensure_lakehouse(
        self, workspace_id: str, display_name: str, description: str
    ) -> FabricItem:
        existing = self.client.find_item(workspace_id, display_name, "Lakehouse")
        if existing:
            details = self.client.request(
                "GET",
                f"/workspaces/{workspace_id}/lakehouses/{existing.id}",
                expected=(200,),
            ).json()
            if not details.get("properties", {}).get("defaultSchema"):
                raise FabricApiError(
                    f"Existing Lakehouse {display_name!r} is not schema-enabled"
                )
            LOGGER.info("Reuse Lakehouse: %s (%s)", display_name, existing.id)
            self.client.move_items(
                workspace_id,
                [existing.id],
                self.folders[FOLDER_BY_TYPE["Lakehouse"]],
            )
            self.items[display_name] = existing
            return existing
        item = self.client.create_item(
            workspace_id,
            "lakehouses",
            display_name,
            description=description,
            creation_payload={"enableSchemas": True},
            folder_id=self.folders[FOLDER_BY_TYPE["Lakehouse"]],
        )
        LOGGER.info("Created Lakehouse: %s (%s)", display_name, item.id)
        self.items[display_name] = item
        return item

    def _ensure_definition_item(
        self,
        workspace_id: str,
        *,
        route: str,
        item_type: str,
        display_name: str,
        description: str,
        definition: dict[str, Any],
        creation_payload: dict[str, Any] | None = None,
    ) -> FabricItem:
        existing = self.client.find_item(workspace_id, display_name, item_type)
        if existing:
            if not self.options.overwrite:
                raise FabricApiError(
                    f"{item_type} {display_name!r} already exists"
                )
            self.client.update_definition(workspace_id, existing.id, definition)
            self.client.move_items(
                workspace_id,
                [existing.id],
                self.folders[FOLDER_BY_TYPE[item_type]],
            )
            LOGGER.info("Updated %s: %s (%s)", item_type, display_name, existing.id)
            self.items[display_name] = existing
            return existing
        create_definition = definition if creation_payload is None else None
        item = self.client.create_item(
            workspace_id,
            route,
            display_name,
            description=description,
            definition=create_definition,
            creation_payload=creation_payload,
            folder_id=self.folders[FOLDER_BY_TYPE[item_type]],
        )
        if creation_payload is not None:
            self.client.update_definition(workspace_id, item.id, definition)
        LOGGER.info("Created %s: %s (%s)", item_type, display_name, item.id)
        self.items[display_name] = item
        return item

    def _ensure_folders(self, workspace_id: str) -> dict[str, str]:
        existing = {
            folder["displayName"]: folder["id"]
            for folder in self.client.list_folders(workspace_id)
            if not folder.get("parentFolderId")
        }
        folders: dict[str, str] = {}
        for name in FOLDER_BY_TYPE.values():
            folder_id = existing.get(name)
            if folder_id is None:
                folder_id = self.client.create_folder(workspace_id, name)
                LOGGER.info("Created folder: %s (%s)", name, folder_id)
            else:
                LOGGER.info("Reuse folder: %s (%s)", name, folder_id)
            folders[name] = folder_id
        return folders

    def _validate_inventory(self, workspace_id: str) -> None:
        items = {
            (item.display_name, item.type.casefold()): item
            for item in self.client.list_items(workspace_id)
        }
        missing = [
            f"{name} ({item_type})"
            for name, item_type in self._expected_items()
            if (name, item_type.casefold()) not in items
        ]
        if missing:
            raise FabricApiError(
                "Deployment completed with missing items: " + ", ".join(missing)
            )
        misplaced = []
        for name, item_type in self._expected_items():
            item = items[(name, item_type.casefold())]
            expected_folder = self.folders[FOLDER_BY_TYPE[item_type]]
            if item.folder_id != expected_folder:
                misplaced.append(f"{name} ({item_type})")
        if misplaced:
            raise FabricApiError(
                "Deployment completed with misplaced items: "
                + ", ".join(misplaced)
            )
        LOGGER.info("Deployment inventory validation passed")
