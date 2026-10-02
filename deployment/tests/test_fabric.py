"""Focused Fabric connection preflight tests."""

from __future__ import annotations

import json
import unittest
from typing import Any

from fabric_sap_deployer.fabric import (
    ApiResponse,
    FabricApiError,
    FabricClient,
)

CONNECTION_ID = "11111111-1111-1111-1111-111111111111"


def response(payload: dict[str, Any]) -> ApiResponse:
    """Build one synchronous JSON response."""
    return ApiResponse(
        status_code=200,
        headers={},
        content=json.dumps(payload).encode("utf-8"),
    )


class FakeFabricClient(FabricClient):
    """Isolated Fabric client with deterministic connection responses."""

    def __init__(self, connection_status: dict[str, Any]) -> None:
        super().__init__(lambda: "token")
        self.connection_status = connection_status
        self.connection = {
            "id": CONNECTION_ID,
            "displayName": "conn_sap_finance_sql",
            "connectivityType": "ShareableCloud",
            "connectionDetails": {"type": "FabricSql"},
            "credentialDetails": {"credentialType": "OAuth2"},
        }
        self.connections = [self.connection]

    def paged(self, path: str) -> list[dict[str, Any]]:
        if path != "/connections":
            raise AssertionError(f"Unexpected collection request: {path}")
        return self.connections

    def request(
        self,
        method: str,
        path_or_url: str,
        *,
        body: dict[str, Any] | None = None,
        expected: tuple[int, ...] = (200,),
    ) -> ApiResponse:
        del body, expected
        if method == "GET" and path_or_url == f"/connections/{CONNECTION_ID}":
            return response(self.connection)
        if (
            method == "POST"
            and path_or_url
            == f"/connections/{CONNECTION_ID}/testConnection"
        ):
            return response(self.connection_status)
        raise AssertionError(f"Unexpected request: {method} {path_or_url}")


class FabricConnectionTests(unittest.TestCase):
    """Verify connection resolution and health gates."""

    def test_resolves_online_oauth_connection_by_name(self) -> None:
        client = FakeFabricClient({"status": "Online", "errors": []})

        connection = client.resolve_oauth2_connection(
            "conn_sap_finance_sql", "FabricSql"
        )
        client.validate_connection_online(connection)

        self.assertEqual(connection["id"], CONNECTION_ID)

    def test_resolves_online_oauth_connection_by_id(self) -> None:
        client = FakeFabricClient({"status": "Online", "errors": []})

        connection = client.resolve_oauth2_connection(
            CONNECTION_ID, "FabricSql"
        )

        self.assertEqual(connection["displayName"], "conn_sap_finance_sql")

    def test_rejects_expired_oauth_connection_before_deployment(self) -> None:
        client = FakeFabricClient(
            {
                "status": "Offline",
                "errors": [
                    {
                        "errorCode": "IncorrectCredentials",
                        "message": "OAuth refresh token expired",
                    }
                ],
            }
        )

        with self.assertRaisesRegex(
            FabricApiError, "OAuth refresh token expired"
        ):
            client.validate_connection_online(client.connection)

    def test_missing_connection_has_actionable_prerequisite_error(self) -> None:
        client = FakeFabricClient({"status": "Online", "errors": []})
        client.connections = []

        with self.assertRaisesRegex(
            FabricApiError, "Create and authorize the connection"
        ):
            client.resolve_oauth2_connection(
                "conn_sap_finance_sql", "FabricSql"
            )

    def test_duplicate_connection_name_requires_explicit_id(self) -> None:
        client = FakeFabricClient({"status": "Online", "errors": []})
        client.connections.append(
            {
                **client.connection,
                "id": "22222222-2222-2222-2222-222222222222",
            }
        )

        with self.assertRaisesRegex(FabricApiError, "pass its ID"):
            client.resolve_oauth2_connection(
                "conn_sap_finance_sql", "FabricSql"
            )


if __name__ == "__main__":
    unittest.main()
