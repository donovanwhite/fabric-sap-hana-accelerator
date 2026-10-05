"""Deployment metadata collision tests."""

from __future__ import annotations

import unittest
from pathlib import Path
from typing import cast

from fabric_sap_deployer.deployer import (
    AcceleratorDeployer,
    DeploymentOptions,
    PLANNING_SQL_CONNECTION_NAME,
    SEMANTIC_MODEL_CONNECTION_NAME,
    SQL_CONNECTION_NAME,
)
from fabric_sap_deployer.fabric import FabricApiError, FabricClient, FabricItem


class ItemInventoryClient:
    """Minimal client for workspace inventory checks."""

    def __init__(self, items: list[FabricItem]) -> None:
        self.items = items

    def list_items(self, workspace_id: str) -> list[FabricItem]:
        del workspace_id
        return self.items


class DefinitionItemClient:
    """Capture definition update calls for an existing item."""

    def __init__(self) -> None:
        self.item = FabricItem(
            id="plan-id",
            display_name="SAP Finance Planning Enterprise",
            type="Plan",
        )
        self.updated = False
        self.moved = False

    def find_item(
        self, workspace_id: str, display_name: str, item_type: str
    ) -> FabricItem:
        del workspace_id, display_name, item_type
        return self.item

    def update_definition(
        self,
        workspace_id: str,
        item_id: str,
        definition: dict[str, object],
    ) -> None:
        del workspace_id, item_id, definition
        self.updated = True

    def move_items(
        self, workspace_id: str, item_ids: list[str], folder_id: str
    ) -> None:
        del workspace_id, item_ids, folder_id
        self.moved = True


def deployer_with(items: list[FabricItem]) -> AcceleratorDeployer:
    """Create an overwrite deployer with a fixed item inventory."""
    return AcceleratorDeployer(
        Path.cwd(),
        DeploymentOptions(workspace="workspace", overwrite=True),
        client=cast(FabricClient, ItemInventoryClient(items)),
    )


class DeploymentCollisionTests(unittest.TestCase):
    """Verify safe handling of Fabric item name collisions."""

    def test_ignores_system_managed_sql_endpoint_name(self) -> None:
        deployer = deployer_with(
            [
                FabricItem(
                    id="endpoint",
                    display_name="lh_brz_slv_finance",
                    type="SQLEndpoint",
                ),
                FabricItem(
                    id="lakehouse",
                    display_name="lh_brz_slv_finance",
                    type="Lakehouse",
                ),
            ]
        )

        deployer._check_conflicts("workspace")

    def test_rejects_name_owned_by_incompatible_item_type(self) -> None:
        deployer = deployer_with(
            [
                FabricItem(
                    id="report",
                    display_name="lh_brz_slv_finance",
                    type="Report",
                )
            ]
        )

        with self.assertRaisesRegex(FabricApiError, "expected Lakehouse"):
            deployer._check_conflicts("workspace")

    def test_rejects_duplicate_compatible_items(self) -> None:
        deployer = deployer_with(
            [
                FabricItem(
                    id="first",
                    display_name="SAP Finance",
                    type="SemanticModel",
                ),
                FabricItem(
                    id="second",
                    display_name="SAP Finance",
                    type="SemanticModel",
                ),
            ]
        )

        with self.assertRaisesRegex(FabricApiError, "Multiple SemanticModel"):
            deployer._check_conflicts("workspace")

    def test_connection_defaults_follow_naming_standard(self) -> None:
        for name in [
            SQL_CONNECTION_NAME,
            PLANNING_SQL_CONNECTION_NAME,
            SEMANTIC_MODEL_CONNECTION_NAME,
        ]:
            self.assertTrue(name.startswith("conn_"), name)
            self.assertEqual(name, name.lower())
        self.assertNotEqual(SQL_CONNECTION_NAME, PLANNING_SQL_CONNECTION_NAME)

    def test_preserves_existing_plan_runtime_definition(self) -> None:
        client = DefinitionItemClient()
        deployer = AcceleratorDeployer(
            Path.cwd(),
            DeploymentOptions(workspace="workspace", overwrite=True),
            client=cast(FabricClient, client),
        )
        deployer.folders = {"planner": "folder-id"}

        item = deployer._ensure_definition_item(
            "workspace-id",
            route="plans",
            item_type="Plan",
            display_name="SAP Finance Planning Enterprise",
            description="Finance planning",
            definition={"parts": []},
            preserve_existing_definition=True,
        )

        self.assertEqual(item.id, "plan-id")
        self.assertFalse(client.updated)
        self.assertTrue(client.moved)


if __name__ == "__main__":
    unittest.main()
