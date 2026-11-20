---
title: SAP Finance Fabric automated deployment
description: Deploy the complete accelerator into an existing Microsoft Fabric workspace
author: Data and AI Engineering
ms.date: 2026-11-20
ms.topic: tutorial
keywords:
  - Microsoft Fabric
  - deployment
  - REST API
  - SAP Finance
estimated_reading_time: 7
---

## Deployment contract

The only Fabric prerequisites are:

* An existing Microsoft Fabric tenant
* An existing workspace on active Fabric capacity
* Contributor or higher access to that workspace
* Capacity administrator access on the assigned capacity, or supported
  capacity-level Contributor permission for the capacity preflight
* An authorized Shareable Cloud `FabricSql` source connection using OAuth2
* A separate Shareable Cloud `FabricSql` planning connection using OAuth2
* An authorized Shareable Cloud `PowerBIDatasets` connection using OAuth2

All accelerator-owned connections use lowercase names prefixed with `conn_`.
The defaults are `conn_sap_finance_sql`,
`conn_sap_finance_planning_sql`, and
`conn_sap_finance_planning_dataset`.

The local machine requires Python 3.11–3.13 and an authenticated Azure CLI.
For named source timezones on Windows, install the package with
`python -m pip install .\deployment`; its conditional `tzdata` dependency
provides the IANA timezone database. The synthetic UTC profile needs no
additional timezone data.

## Required deployment permissions

Workspace and capacity permissions are separate. The deploying identity needs
Contributor or higher access on the target workspace. It also needs capacity
administrator access on the assigned capacity, or a supported capacity-level
Contributor assignment. Workspace Contributor alone does not make the
assigned capacity visible to the installer.

The current installer calls `GET /v1/capacities`, finds the workspace's
assigned capacity and requires its state to be `Active` before creating
items. Microsoft's
[List Capacities API](https://learn.microsoft.com/rest/api/fabric/core/capacities/list-capacities)
accepts administrator or contributor permission on the capacity. For delegated
callers, its documented scope is `Capacity.Read.All` or
`Capacity.ReadWrite.All`; API scopes do not replace the required resource role.

| Access boundary | Required access                                                |
| --------------- | -------------------------------------------------------------- |
| Workspace       | Contributor, Member or Admin on the target workspace           |
| Capacity        | Capacity administrator or supported capacity-level Contributor |
| Connections     | Permission to use, read and test each selected connection      |

> [!IMPORTANT]
> If an active capacity is absent from the caller's capacity list, preflight
> can report it as `not visible`. Check capacity-level access and the active
> tenant before treating this as a paused-capacity problem. Use the same
> deploying identity for `-DryRun` and deployment.

Have the capacity owner arrange the appropriate access under your
organization's least-privilege policy. Capacity administrator is not the same
as Fabric tenant administrator or Azure subscription Owner; this installer
does not require those broader roles to deploy into an already assigned
workspace. It does not create, resize or reassign capacities, or grant roles.
See [capacity administration](https://learn.microsoft.com/fabric/admin/capacity-settings).

## Deploy

From the repository root:

```powershell
az login --tenant "<tenant-id>" --allow-no-subscriptions
.\deployment\deploy.ps1 -Workspace "<workspace-id>"
```

The default connection name is `conn_sap_finance_sql`. If the tenant uses a
different name, or has duplicate display names, pass the connection ID:

```powershell
.\deployment\deploy.ps1 `
  -Workspace "<workspace-id>" `
  -SqlConnection "<connection-name-or-id>" `
  -PlanningSqlConnection "<planning-connection-name-or-id>" `
  -SemanticModelConnection "<connection-name-or-id>"
```

The access token issued by `az login` determines the Fabric tenant. The
deployer accepts either a workspace name or ID, but the ID is recommended
because it is unambiguous. If Azure CLI is already signed into the correct
tenant, another login is not required.

For an authorized SAP HANA source, retain the same deployment path and pass
its existing managed gateway connection and a release-verified contract:

```powershell
.\deployment\deploy.ps1 -Workspace "<test-workspace-id>" `
  -HanaConnection "<managed-hana-connection-name-or-id>" `
  -ExtractionContract "<approved-source-contract.json>" -DryRun
```

The HANA preflight checks the managed `SapHana` connection, gateway connectivity
and supported managed Basic/Windows authentication. It does not create source
credentials or SAP objects. Every source projection must be marked
`schemaVerified=true`, the contract must name `validatedRelease`, and standard
SAP tables require explicit client filters. The supplied synthetic contract is
not a customer release certification. Planning connections remain separate.

Use `-DryRun` to validate the workspace and print the deployment plan without
creating items:

```powershell
.\deployment\deploy.ps1 -Workspace "<workspace-id>" -DryRun
```

Dry-run resolves the connection, verifies that it is a Shareable Cloud
`FabricSql` connection using OAuth2, and calls the Fabric connection test API.
It performs the same checks for the `PowerBIDatasets` connection used by the
Plan item. An offline, expired, ambiguous, missing, or incorrectly typed
connection stops the deployment before any workspace item is created.

## Create the SQL connections

OAuth2 authorization requires an interactive user consent flow, so the
installer does not create or silently replace the connection.

1. In Fabric, open **Settings** and select
   **Manage connections and gateways**.
2. Create a new **Cloud** connection of type **Fabric SQL database**.
3. Name it `conn_sap_finance_sql`, or record its connection ID for
   `-SqlConnection`.
4. Create a second OAuth2 Fabric SQL connection through the Plan connection
   wizard and name it `conn_sap_finance_planning_sql`, or record its
   connection ID for `-PlanningSqlConnection`.
5. Select **OAuth 2.0**, sign in, and save both connections.
6. Run the installer with `-DryRun` before deployment.

If a refresh token expires, dry-run reports the connection as offline and
provides the Fabric error before deployment. Reauthorize the credentials in
the same connection settings and rerun dry-run.

Deployment fails on conflicting item names by default. Use `-Overwrite` to
update compatible existing items:

```powershell
.\deployment\deploy.ps1 -Workspace "<workspace-id>" -Overwrite
```

`-Overwrite` updates only items with the expected name and type. A duplicate
name or a name occupied by another item type fails before mutation, preventing
Fabric metadata collisions.

## Deployment order

1. Validate workspace, capacity, and required OAuth2 connections
2. Create/reuse workload folders
3. Create both schema-enabled Lakehouses under `lakehouses`
4. Create and seed `SAP_FIN_DB` under `database`
5. Create `SAP_FIN_PLAN_DB` under `database`
6. Deploy and bind all nine transformation and validation notebooks under `notebooks`
7. Deploy the Direct Lake semantic model under `semantic models`
8. Deploy the report under `reports`
9. Deploy `SAP Finance Planning Enterprise` with five Planning sheets and one
   Intelligence sheet under `planner`
10. Deploy the Finance Data Agent under `agents`
11. Deploy the pipeline under `pipelines`
12. Validate the final item inventory

The folder structure is:

```text
agents
database
lakehouses
notebooks
pipelines
planner
reports
semantic models
```

The existing parameterized `ForEach` now uses bounded parallelism. Notebook 00
persists the plan before Copy; notebook 01 validates each immutable receipt.
The source is `FabricSqlDatabaseSource` for the simulator, or `SapHanaSource`
for an explicitly verified HANA deployment. Each Copy runs one query, avoiding
nested query parallelism. Shared metadata supplies all projections, scopes,
entry windows, business keys and validation contracts.

The semantic model uses Direct Lake on SQL. The installer resolves the Gold
Lakehouse SQL endpoint and database name and substitutes them into the TMDL
expression before deployment.

The Plan item uses the `SAP Finance` semantic model for governed actuals. Fabric
creates its internal application database automatically. The installer binds `conn_sap_finance_planning_sql` to the application
database for viewer collaboration. `conn_sap_finance_sql` remains isolated
to source ingestion. Editable input and writeback runtime state must be
initialized once through the Plan UI because the public item-definition API
does not create the internal Planning sidecar. The separate `SAP_FIN_PLAN_DB`
stores governed scenarios, assumptions, and writeback data. Validation and
initialization steps are documented in
[`artefacts/planner/README.md`](../artefacts/planner/README.md).

The installer is idempotent with `-Overwrite`. Generated definitions are
checked for unresolved deployment tokens and source-tenant workspace, item,
notebook, semantic model, and connection identifiers before publication. It
never runs the data pipeline. After a successful deployment, the team runs
`pl_full_medallion_sap_finance` to create Silver, Gold, the Materialized Lake
Views, and validation evidence. `AUTO` initializes configured fiscal-year
partitions on first use and then selects document incremental extraction.
Use `RECONCILE` for the separately scheduled historical refresh policy.
The pipeline item name is retained for compatibility; it is no longer
restricted to full snapshots.

Incremental runs retain much less financial source data when their document
window is selective, but master snapshots and reconciliation still consume
source/compute capacity. Source control rows add transfer overhead. Retained
immutable Bronze and pinned Delta versions increase storage until the approved
retention horizon. No additional orchestration service is provisioned. Measure
representative HANA query plans, transfer and capacity before raising
concurrency; local synthetic timings are not production benchmarks.

For an existing Plan, `-Overwrite` preserves the live Plan definition instead
of replacing it. This protects UI-initialized editable inputs, writeback
sidecar state, and Intelligence-sheet visuals that are not safely recreated
by the public item-definition API.

## Authentication

For an interactive deployment:

```powershell
az login --tenant "<tenant-id>" --allow-no-subscriptions
```

For automation, sign in Azure CLI with a service principal before invoking the
same command. The principal itself must satisfy the
[workspace, capacity and connection permissions](#required-deployment-permissions);
the operator's personal roles are not inherited. Connections must already
exist and be shared with the principal. Tenant settings must also permit
the service principal to use the required Fabric APIs.

## Failure behavior

Each deployment step is validated before its dependants start. The installer:

* Requires the assigned capacity to be visible and active for the deploying identity
* Tests OAuth connection health before workspace mutation
* Rejects ambiguous names and incompatible item-type collisions
* Rebinds notebook, Lakehouse, SQL database, report, model, and agent metadata
* Rejects source-tenant identifiers and unresolved deployment tokens
* Prints each created or reused item ID and stops on the first failure

It does not delete pre-existing workspace items or perform an automatic
rollback.

## Local regression checks

From the repository root:

```powershell
$env:PYTHONPATH = Join-Path (Get-Location) "deployment\src"
python -m unittest discover -s deployment\tests -q
```

This checks source SQL generation, boundaries, metadata, deterministic
scenarios, financial controls, affected-key behavior and deployment bindings
without requiring production SAP or Fabric credentials.

Actual Spark/Delta tests use an isolated Linux container with Python 3.11,
Spark 3.5.5 and Delta 3.2.1. From PowerShell 7 on Windows:

```powershell
.\deployment\tests\Invoke-SparkValidation.ps1 -Suite Recovery
.\deployment\tests\Invoke-SparkValidation.ps1 -Suite Gold
.\deployment\tests\Invoke-SparkValidation.ps1 -Suite Medallion
```

`-Suite All` runs all three sequentially. The launcher uses the configured host
package index for its pinned wheel and verified HTTPS for pinned Maven jars.
It does not replace registries or disable TLS verification. The local Docker
context must use a local socket; source files are mounted read-only and test
containers have no external network, two CPUs and a 4 GiB memory limit.
Host Python and Java are not changed. Downloaded dependencies are cached outside
the repository; `-DependencyCache` can select an existing approved cache.

Recovery exercises real Delta publication, partial failure, checkpoint
membership, lost acknowledgements and replay. Gold covers native currency
migration and repair. Medallion executes compiled Gold notebooks and
end-to-end assertions over all 13 synthetic source tables, then verifies
business-state replay and MLV SELECT results.

A preinstalled local Fabric-compatible Spark/Delta environment can execute
the same Python scripts directly. Local Spark does not validate Fabric gateway
connectivity, notebook activity output contracts, Fabric MLV DDL/refresh,
Power BI DAX execution or live Direct Lake/Data Agent behavior. Those require
an explicitly authorized test workspace and appropriate authoring tooling.
