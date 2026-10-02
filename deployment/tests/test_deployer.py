"""Deployment metadata collision tests."""

from __future__ import annotations

import unittest
from pathlib import Path
from typing import cast

from fabric_sap_deployer.deployer import (
    AcceleratorDeployer,
    DeploymentOptions,
)
from fabric_sap_deployer.fabric import FabricApiError, FabricClient, FabricItem


class ItemInventoryClient:
    """Minimal client for workspace inventory checks."""

    def __init__(self, items: list[FabricItem]) -> None:
        self.items = items

    def list_items(self, workspace_id: str) -> list[FabricItem]:
        del workspace_id
        return self.items


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


if __name__ == "__main__":
    unittest.main()
