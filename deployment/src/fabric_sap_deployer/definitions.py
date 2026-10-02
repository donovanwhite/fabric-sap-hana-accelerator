"""Build environment-specific Fabric item definitions from repository assets."""

from __future__ import annotations

import base64
import csv
import json
import re
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any, Iterable

OLD_WORKSPACE_ID = "0448ed4f-990e-4171-83f1-ea2a293e12d9"
OLD_BRONZE_LAKEHOUSE_ID = "333965d7-4af9-49d1-bfeb-78cabeccbf8b"
OLD_GOLD_LAKEHOUSE_ID = "afdc0c5b-8ace-4cee-940f-b17076bcc0a7"
OLD_SQL_DATABASE_ID = "de838c75-ed06-488c-ae5c-7bb3484e8e53"
OLD_SEMANTIC_MODEL_ID = "7f649a72-ce03-4788-8bc1-a92ca208867b"

LEGACY_ENVIRONMENT_IDS = frozenset(
    {
        OLD_WORKSPACE_ID,
        OLD_BRONZE_LAKEHOUSE_ID,
        OLD_GOLD_LAKEHOUSE_ID,
        OLD_SQL_DATABASE_ID,
        OLD_SEMANTIC_MODEL_ID,
        "378594a7-2b2f-4d78-97ca-7b4997fbc202",
        "608c836a-24ac-43d7-9275-ff9ba2bca24e",
        "8cea0e2b-5a4d-4996-aa55-a8b9d63faa36",
        "272aa892-f5e9-498f-af1d-054dda8b6d3c",
        "8483e349-36e3-4102-b69f-e24a807df8fb",
        "30005a8a-313e-48c0-9576-23c6b0bdd59c",
        "097d87ac-6153-49d9-94a6-6a53c042173e",
        "5f94118d-3996-43a2-be7b-7b9524a2c6f0",
        "bc3890bc-d0e1-4e1c-8eed-503e2a95f8d9",
    }
)

UNRESOLVED_DEPLOYMENT_TOKENS = frozenset(
    {
        "__WORKSPACE_ID__",
        "__SQL_DATABASE_ID__",
        "__SQL_CONNECTION_ID__",
        "__BRONZE_LAKEHOUSE_ID__",
        "__NOTEBOOK_ID__",
        "__SEMANTIC_MODEL_ID__",
        "__GOLD_SQL_SERVER__",
        "__GOLD_SQL_DATABASE__",
    }
)

PIPELINE_NOTEBOOKS = {
    "nb_schema_val": "00_Setup_and_Namespace_Validation",
    "nb_tbl_inventory": "01_Bronze_CSV_Inventory",
    "nb_brz_slv_sap": "02_Bronze_CSV_to_Silver_Delta",
    "nb_gld_dim_sap": "03_Gold_SCD2_Dimensions",
    "nb_gld_fct_fin_sap": "04_Gold_Atomic_Finance_Fact",
    "nb_gld_bgt_sap": "05_Gold_Budget_Fact",
    "nb_gld_fct_mlv": "06_Gold_Materialized_Lake_Views",
    "nb_full_validation": "07_End_to_End_Validation",
    "nb_bronze_cleanup": "08_Bronze_Snapshot_Cleanup",
}

NUMC_WIDTHS = {
    "ACDOCA": {"GJAHR": 4, "POPER": 3, "DOCLN": 6, "RACCT": 10},
    "BKPF": {"GJAHR": 4, "MONAT": 2},
    "SKA1": {"SAKNR": 10},
    "SKAT": {"SAKNR": 10},
    "FAGLFLEXP": {"RYEAR": 4, "RACCT": 10},
    "BUDGET": {"GL_ACCOUNT": 10, "FISCAL_YEAR": 4, "FISCAL_PERIOD": 3},
    "GL_ACCOUNT_REFERENCE": {"GL_ACCOUNT": 10},
}


def encode_bytes(value: bytes) -> str:
    """Return standard Base64 text."""
    return base64.b64encode(value).decode("ascii")


def encode_text(value: str) -> str:
    """Encode UTF-8 text as Base64."""
    return encode_bytes(value.encode("utf-8"))


def part(path: str, payload: bytes | str) -> dict[str, str]:
    """Build one InlineBase64 definition part."""
    encoded = encode_text(payload) if isinstance(payload, str) else encode_bytes(payload)
    return {"path": path, "payload": encoded, "payloadType": "InlineBase64"}


def json_text(value: Any) -> str:
    """Serialize deterministic JSON for a definition part."""
    return json.dumps(value, indent=2, ensure_ascii=False) + "\n"


def validate_deployable_definition(definition: dict[str, Any]) -> None:
    """Reject generated definitions that retain source-environment metadata."""
    for definition_part in definition.get("parts", []):
        try:
            content = base64.b64decode(
                definition_part["payload"], validate=True
            ).decode("utf-8")
        except (KeyError, UnicodeDecodeError, ValueError):
            continue
        stale_ids = sorted(
            value for value in LEGACY_ENVIRONMENT_IDS if value in content
        )
        unresolved = sorted(
            token for token in UNRESOLVED_DEPLOYMENT_TOKENS if token in content
        )
        if stale_ids or unresolved:
            details = []
            if stale_ids:
                details.append("legacy IDs: " + ", ".join(stale_ids))
            if unresolved:
                details.append("unresolved tokens: " + ", ".join(unresolved))
            raise ValueError(
                f"Definition part {definition_part.get('path', '<unknown>')!r} "
                "contains environment-specific metadata: "
                + "; ".join(details)
            )


def notebook_definition(
    path: Path,
    *,
    workspace_id: str,
    default_lakehouse_id: str,
    default_lakehouse_name: str,
    known_lakehouse_ids: list[str],
) -> dict[str, Any]:
    """Create a rebound notebook definition."""
    notebook = json.loads(path.read_text(encoding="utf-8"))
    dependencies = notebook.setdefault("metadata", {}).setdefault("dependencies", {})
    dependencies["lakehouse"] = {
        "default_lakehouse": default_lakehouse_id,
        "default_lakehouse_name": default_lakehouse_name,
        "default_lakehouse_workspace_id": workspace_id,
        "known_lakehouses": [
            {"id": lakehouse_id} for lakehouse_id in known_lakehouse_ids
        ],
    }
    return {
        "format": "ipynb",
        "parts": [part("artifact.content.ipynb", json_text(notebook))],
    }


def directory_definition(
    root: Path,
    *,
    format_name: str,
    replacements: dict[str, str],
) -> dict[str, Any]:
    """Create a multipart definition from a PBIP-style directory."""
    parts: list[dict[str, str]] = []
    for path in sorted(file for file in root.rglob("*") if file.is_file()):
        relative = path.relative_to(root).as_posix()
        if relative == "model-manifest.json":
            continue
        if path.suffix.lower() in {".json", ".tmdl", ".pbir", ".pbism"}:
            content = path.read_text(encoding="utf-8")
            for old, new in replacements.items():
                content = content.replace(old, new)
            parts.append(part(relative, content))
        else:
            parts.append(part(relative, path.read_bytes()))
    return {"format": format_name, "parts": parts}


def pipeline_definition(
    path: Path,
    *,
    workspace_id: str,
    notebook_ids: dict[str, str],
    sql_database_id: str,
    bronze_lakehouse_id: str,
    sql_connection_id: str,
) -> dict[str, Any]:
    """Rebind the pipeline to target item and connection identifiers."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    activities = payload["properties"]["activities"]
    for activity in activities:
        properties = activity.get("typeProperties", {})
        if activity["name"] == "fe_copy_sap_full_snapshot":
            copy_activity = properties["activities"][0]
            source_settings = copy_activity["typeProperties"]["source"][
                "datasetSettings"
            ]["connectionSettings"]["properties"]["typeProperties"]
            source_settings["workspaceId"] = workspace_id
            source_settings["artifactId"] = sql_database_id
            copy_activity["typeProperties"]["source"]["datasetSettings"][
                "connectionSettings"
            ]["properties"]["externalReferences"][
                "connection"
            ] = sql_connection_id
            sink_settings = copy_activity["typeProperties"]["sink"][
                "datasetSettings"
            ]["linkedService"]["properties"]["typeProperties"]
            sink_settings["workspaceId"] = workspace_id
            sink_settings["artifactId"] = bronze_lakehouse_id
            copy_activity["typeProperties"]["sink"]["datasetSettings"].pop(
                "externalReferences", None
            )
        if activity["type"] == "TridentNotebook":
            notebook_name = PIPELINE_NOTEBOOKS.get(activity["name"])
            if notebook_name is None:
                raise ValueError(
                    f"No notebook binding is configured for pipeline activity "
                    f"{activity['name']!r}"
                )
            properties["notebookId"] = notebook_ids[notebook_name]
            properties["workspaceId"] = workspace_id
    return {"parts": [part("pipeline-content.json", json_text(payload))]}


def _sql_literal(value: str, width: int | None = None) -> str:
    normalized = value.strip()
    if not normalized:
        return "NULL"
    if width and normalized.isdigit():
        normalized = normalized.zfill(width)
    return "N'" + normalized.replace("'", "''").replace("\r", " ").replace("\n", " ") + "'"


def _chunks(values: list[dict[str, str]], size: int) -> Iterable[list[dict[str, str]]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


def _seed_merge_sql(
    *,
    table_name: str,
    source_object: str,
    keys: list[str],
    csv_path: Path,
) -> str:
    """Generate idempotent batched MERGE statements for one source table."""
    with csv_path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        rows = list(reader)
        columns = reader.fieldnames or []
    if not columns:
        raise ValueError(f"{csv_path} has no header")
    widths = NUMC_WIDTHS.get(table_name, {})
    quoted_columns = ", ".join(f"[{column}]" for column in columns)
    join = " AND ".join(
        f"target.[{key}] = source.[{key}]" for key in keys
    )
    non_keys = [column for column in columns if column not in keys]
    update = ", ".join(
        f"target.[{column}] = source.[{column}]" for column in non_keys
    )
    insert_values = ", ".join(f"source.[{column}]" for column in columns)
    output: list[str] = [f"-- Seed {source_object} from {csv_path.name}"]
    for batch in _chunks(rows, 100):
        values = ",\n".join(
            "  ("
            + ", ".join(
                _sql_literal(row.get(column, ""), widths.get(column))
                for column in columns
            )
            + ")"
            for row in batch
        )
        output.append(
            f"""MERGE {source_object} AS target
USING (VALUES
{values}
) AS source ({quoted_columns})
ON {join}
WHEN MATCHED THEN UPDATE SET {update}
WHEN NOT MATCHED THEN
  INSERT ({quoted_columns}) VALUES ({insert_values});
GO"""
        )
    return "\n\n".join(output)


def sql_database_definition(artifacts_root: Path) -> dict[str, Any]:
    """Build a SQL project definition that creates and seeds SAP_FIN_DB."""
    contract = json.loads(
        (artifacts_root / "sap" / "factory-copy-contract.json").read_text(
            encoding="utf-8"
        )
    )
    schema_sql = (artifacts_root / "sap" / "schema.sql").read_text(
        encoding="utf-8"
    )
    seed_blocks: list[str] = []
    data_root = artifacts_root / "sap_synthetic_data" / "sap"
    for table in contract["tables"]:
        folder = data_root / table["sinkFolder"].rsplit("/", 1)[-1]
        files = sorted(folder.glob("*.csv"))
        if len(files) != 1:
            raise ValueError(f"Expected one seed CSV in {folder}; found {len(files)}")
        seed_blocks.append(
            _seed_merge_sql(
                table_name=table["name"],
                source_object=table["sourceObject"],
                keys=table["businessKey"],
                csv_path=files[0],
            )
        )
    project = """<Project Sdk="Microsoft.Build.Sql/1.0.0">
  <PropertyGroup>
    <Name>SAP_FIN_DB</Name>
    <DSP>Microsoft.Data.Tools.Schema.Sql.SqlDbFabricDatabaseSchemaProvider</DSP>
    <ModelCollation>1033, CS</ModelCollation>
    <DefaultCollation>Latin1_General_100_BIN2_UTF8</DefaultCollation>
  </PropertyGroup>
  <ItemGroup>
    <Build Remove="Scripts\\Deploy.sql" />
    <PostDeploy Include="Scripts\\Deploy.sql" />
  </ItemGroup>
</Project>
"""
    deploy_sql = (
        "-- Generated by fabric-sap-hana-deployer.\n"
        + schema_sql
        + "\n\n"
        + "\n\n".join(seed_blocks)
        + "\n"
    )
    return {
        "format": "sqlproj",
        "parts": [
            part("sqldb.sqlproj", project),
            part("Scripts/Deploy.sql", deploy_sql),
        ],
    }


def extract_marker(text: str, marker: str) -> str:
    """Extract one markdown marker block."""
    match = re.search(
        rf"<!-- BEGIN_{marker} -->\s*(.*?)\s*<!-- END_{marker} -->",
        text,
        re.DOTALL,
    )
    if not match:
        raise ValueError(f"Marker {marker} was not found")
    return match.group(1).strip()


def data_agent_definition(
    markdown_path: Path,
    *,
    workspace_id: str,
    gold_lakehouse_id: str,
) -> dict[str, Any]:
    """Create a complete rebound Finance Data Agent definition."""
    text = markdown_path.read_text(encoding="utf-8")
    instructions = extract_marker(text, "AGENT_INSTRUCTIONS")
    source_description = extract_marker(text, "SOURCE_DESCRIPTION")
    source_instructions = extract_marker(text, "SOURCE_INSTRUCTIONS")
    few_shots: list[dict[str, str]] = []
    for index in range(1, 11):
        token = f"{index:02d}"
        question = extract_marker(text, f"EXAMPLE_{token}_QUESTION")
        query = extract_marker(text, f"EXAMPLE_{token}_SQL")
        query = re.sub(r"^```sql\s*|\s*```$", "", query, flags=re.DOTALL).strip()
        few_shots.append(
            {
                "id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"sap-finance-{index}")),
                "question": question,
                "query": query,
            }
        )
    template_root = markdown_path.parent / "definition"
    if not template_root.is_dir():
        raise FileNotFoundError(f"Data Agent template not found: {template_root}")
    definition_parts: list[dict[str, str]] = []
    for path in sorted(file for file in template_root.rglob("*") if file.is_file()):
        relative = path.relative_to(template_root).as_posix()
        if relative == ".platform":
            continue
        if path.suffix.lower() != ".json":
            definition_parts.append(part(relative, path.read_bytes()))
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        if relative.endswith("/stage_config.json"):
            payload["aiInstructions"] = instructions
        elif relative.endswith("/datasource.json"):
            payload["artifactId"] = gold_lakehouse_id
            payload["workspaceId"] = workspace_id
            payload["displayName"] = "lh_gld_finance"
            payload["userDescription"] = source_description
            payload["dataSourceInstructions"] = source_instructions
        elif relative.endswith("/fewshots.json"):
            payload["fewShots"] = deepcopy(few_shots)
        definition_parts.append(part(relative, json_text(payload)))
    return {"parts": definition_parts}
