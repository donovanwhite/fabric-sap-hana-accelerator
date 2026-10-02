"""Microsoft Fabric REST client used by the deployment orchestrator."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from typing import Any, Callable

FABRIC_API = "https://api.fabric.microsoft.com/v1"


class FabricApiError(RuntimeError):
    """Raised when a Fabric REST operation fails."""


@dataclass(frozen=True)
class FabricItem:
    """Minimal Fabric item identity."""

    id: str
    display_name: str
    type: str
    folder_id: str | None = None


@dataclass(frozen=True)
class ApiResponse:
    """HTTP response data used by the client."""

    status_code: int
    headers: Any
    content: bytes

    def json(self) -> dict[str, Any]:
        """Decode the JSON response body."""
        return json.loads(self.content.decode("utf-8")) if self.content else {}


class FabricClient:
    """Small Fabric REST client with pagination and LRO handling."""

    def __init__(
        self,
        token_provider: Callable[[], str],
        *,
        timeout_seconds: int = 120,
        lro_timeout_seconds: int = 1800,
    ) -> None:
        self.token_provider = token_provider
        self.timeout_seconds = timeout_seconds
        self.lro_timeout_seconds = lro_timeout_seconds

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token_provider()}",
            "Content-Type": "application/json",
            "x-ms-fabric-skill": "deployment-pipelines-authoring-cli",
        }

    def request(
        self,
        method: str,
        path_or_url: str,
        *,
        body: dict[str, Any] | None = None,
        expected: tuple[int, ...] = (200,),
    ) -> ApiResponse:
        """Send one request and raise an actionable error on failure."""
        url = (
            path_or_url
            if path_or_url.startswith("https://")
            else f"{FABRIC_API}{path_or_url}"
        )
        data = json.dumps(body).encode("utf-8") if body is not None else None
        for attempt in range(5):
            request = urllib.request.Request(
                url,
                data=data,
                headers=self._headers(),
                method=method,
            )
            try:
                with urllib.request.urlopen(
                    request, timeout=self.timeout_seconds
                ) as response:
                    result = ApiResponse(
                        status_code=response.status,
                        headers=response.headers,
                        content=response.read(),
                    )
                break
            except urllib.error.HTTPError as error:
                retryable = error.code == 429 or error.code >= 500
                if retryable and attempt < 4:
                    delay = int(error.headers.get("Retry-After", "5").split(",")[0])
                    time.sleep(max(delay, 1))
                    continue
                detail = error.read().decode("utf-8", errors="replace").strip()
                raise FabricApiError(
                    f"{method} {url} failed with HTTP {error.code}: {detail}"
                ) from error
        else:
            raise FabricApiError(f"{method} {url} exhausted retry attempts")
        if result.status_code not in expected:
            detail = result.content.decode("utf-8", errors="replace").strip()
            raise FabricApiError(
                f"{method} {url} failed with HTTP "
                f"{result.status_code}: {detail}"
            )
        return result

    def request_lro(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        synchronous: tuple[int, ...] = (200, 201),
    ) -> dict[str, Any]:
        """Send a Fabric operation and wait for an accepted LRO."""
        response = self.request(
            method,
            path,
            body=body,
            expected=(*synchronous, 202),
        )
        if response.status_code in synchronous:
            return response.json()

        location = response.headers.get("Location")
        if not location:
            raise FabricApiError(f"{method} {path} returned 202 without Location")
        return self._wait_for_operation(location, f"{method} {path}")

    def _wait_for_operation(
        self, location: str, operation_name: str
    ) -> dict[str, Any]:
        """Wait for one Fabric long-running operation."""
        delay = 5
        deadline = time.monotonic() + self.lro_timeout_seconds
        while time.monotonic() < deadline:
            time.sleep(max(delay, 1))
            poll = self.request("GET", location, expected=(200,))
            payload = poll.json()
            status = payload.get("status")
            if status == "Succeeded":
                return payload
            if status == "Failed":
                raise FabricApiError(
                    f"{operation_name} failed: {payload.get('error', payload)}"
                )
            delay = int(poll.headers.get("Retry-After", str(delay)).split(",")[0])
        raise FabricApiError(
            f"{operation_name} did not complete before timeout"
        )

    def paged(self, path: str) -> list[dict[str, Any]]:
        """Return every value from a paginated Fabric collection."""
        results: list[dict[str, Any]] = []
        next_url: str | None = path
        while next_url:
            payload = self.request("GET", next_url, expected=(200,)).json()
            results.extend(payload.get("value", []))
            next_url = payload.get("continuationUri")
        return results

    def resolve_workspace(self, name_or_id: str) -> dict[str, Any]:
        """Resolve exactly one existing workspace."""
        if len(name_or_id) == 36 and name_or_id.count("-") == 4:
            return self.request(
                "GET", f"/workspaces/{name_or_id}", expected=(200,)
            ).json()
        matches = [
            workspace
            for workspace in self.paged("/workspaces")
            if workspace.get("displayName") == name_or_id
        ]
        if len(matches) != 1:
            raise FabricApiError(
                f"Expected one workspace named {name_or_id!r}; "
                f"found {len(matches)}"
            )
        return self.request(
            "GET", f"/workspaces/{matches[0]['id']}", expected=(200,)
        ).json()

    def resolve_oauth2_connection(
        self, name_or_id: str, connection_type: str
    ) -> dict[str, Any]:
        """Resolve one OAuth2 cloud connection by name and type."""
        try:
            connection_id = str(uuid.UUID(name_or_id))
        except ValueError:
            matches = [
                connection
                for connection in self.paged("/connections")
                if connection.get("displayName") == name_or_id
                and connection.get("connectionDetails", {}).get("type")
                == connection_type
            ]
            if len(matches) != 1:
                raise FabricApiError(
                    f"Expected one {connection_type} OAuth2 connection named "
                    f"{name_or_id!r}; found {len(matches)}. Create and authorize "
                    "the connection in Fabric, or pass its ID with "
                    "--sql-connection."
                )
            connection_id = matches[0]["id"]
        connection = self.request(
            "GET", f"/connections/{connection_id}", expected=(200,)
        ).json()
        actual_type = connection.get("connectionDetails", {}).get("type")
        if actual_type != connection_type:
            raise FabricApiError(
                f"Connection {name_or_id!r} must have type {connection_type}; "
                f"found {actual_type or 'no connection type'}"
            )
        if connection.get("connectivityType") != "ShareableCloud":
            raise FabricApiError(
                f"Connection {name_or_id!r} must be a ShareableCloud "
                f"connection; found {connection.get('connectivityType')}"
            )
        credential_type = connection.get("credentialDetails", {}).get(
            "credentialType"
        )
        if credential_type != "OAuth2":
            raise FabricApiError(
                f"Connection {name_or_id!r} must use OAuth2 credentials; "
                f"found {credential_type or 'no credential type'}"
            )
        return connection

    def validate_connection_online(self, connection: dict[str, Any]) -> None:
        """Fail before deployment when a connection is offline or expired."""
        connection_id = connection["id"]
        path = f"/connections/{connection_id}/testConnection"
        response = self.request("POST", path, expected=(200, 202))
        if response.status_code == 200:
            result = response.json()
        else:
            location = response.headers.get("Location")
            if not location:
                raise FabricApiError(
                    f"POST {path} returned 202 without Location"
                )
            self._wait_for_operation(location, f"Test connection {connection_id}")
            result = self.request(
                "GET", f"{location}/result", expected=(200,)
            ).json()
        if result.get("status") != "Online":
            errors = result.get("errors") or []
            details = "; ".join(
                error.get("message", str(error)) for error in errors
            )
            if not details:
                details = (
                    "Reauthorize or repair it in Manage connections and "
                    "gateways before deployment."
                )
            raise FabricApiError(
                f"Connection {connection.get('displayName', connection_id)!r} "
                f"is {result.get('status', 'not online')}. {details}"
            )

    def list_items(self, workspace_id: str) -> list[FabricItem]:
        """List generic workspace items."""
        return [
            FabricItem(
                id=item["id"],
                display_name=item["displayName"],
                type=item["type"],
                folder_id=item.get("folderId"),
            )
            for item in self.paged(f"/workspaces/{workspace_id}/items")
        ]

    def find_item(
        self, workspace_id: str, display_name: str, item_type: str
    ) -> FabricItem | None:
        """Find a unique item by display name and type."""
        matches = [
            item
            for item in self.list_items(workspace_id)
            if item.display_name == display_name
            and item.type.casefold() == item_type.casefold()
        ]
        if len(matches) > 1:
            raise FabricApiError(
                f"Multiple {item_type} items are named {display_name!r}"
            )
        return matches[0] if matches else None

    def create_item(
        self,
        workspace_id: str,
        route: str,
        display_name: str,
        *,
        description: str = "",
        definition: dict[str, Any] | None = None,
        creation_payload: dict[str, Any] | None = None,
        folder_id: str | None = None,
    ) -> FabricItem:
        """Create an item and resolve its identity after provisioning."""
        body: dict[str, Any] = {
            "displayName": display_name,
            "description": description,
        }
        if definition is not None:
            body["definition"] = definition
        if creation_payload is not None:
            body["creationPayload"] = creation_payload
        if folder_id is not None:
            body["folderId"] = folder_id
        self.request_lro(
            "POST",
            f"/workspaces/{workspace_id}/{route}",
            body=body,
            synchronous=(201,),
        )
        item_type = {
            "lakehouses": "Lakehouse",
            "sqlDatabases": "SQLDatabase",
            "notebooks": "Notebook",
            "semanticModels": "SemanticModel",
            "reports": "Report",
            "dataAgents": "DataAgent",
            "dataPipelines": "DataPipeline",
        }[route]
        for _ in range(12):
            item = self.find_item(workspace_id, display_name, item_type)
            if item is not None:
                return item
            time.sleep(5)
        raise FabricApiError(f"Created {item_type} {display_name!r} was not found")

    def update_definition(
        self,
        workspace_id: str,
        item_id: str,
        definition: dict[str, Any],
    ) -> None:
        """Update one item definition."""
        self.request_lro(
            "POST",
            f"/workspaces/{workspace_id}/items/{item_id}/updateDefinition",
            body={"definition": definition},
            synchronous=(200,),
        )

    def list_folders(self, workspace_id: str) -> list[dict[str, Any]]:
        """List every workspace folder."""
        return self.paged(f"/workspaces/{workspace_id}/folders")

    def create_folder(self, workspace_id: str, display_name: str) -> str:
        """Create one root workspace folder and return its ID."""
        payload = self.request(
            "POST",
            f"/workspaces/{workspace_id}/folders",
            body={"displayName": display_name},
            expected=(201,),
        ).json()
        return payload["id"]

    def move_items(
        self, workspace_id: str, item_ids: list[str], folder_id: str
    ) -> None:
        """Move up to 50 items into a workspace folder."""
        self.request(
            "POST",
            f"/workspaces/{workspace_id}/items/bulkMove",
            body={"items": item_ids, "targetFolderId": folder_id},
            expected=(200,),
        )
