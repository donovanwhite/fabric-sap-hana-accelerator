---
title: SAP Finance Fabric automated deployment
description: Deploy the complete accelerator into an existing Microsoft Fabric workspace
author: Data and AI Engineering
ms.date: 2026-10-02
ms.topic: tutorial
keywords:
  - Microsoft Fabric
  - deployment
  - REST API
  - SAP Finance
estimated_reading_time: 5
---

## Deployment contract

The only Fabric prerequisites are:

* An existing Microsoft Fabric tenant
* An existing workspace on active Fabric capacity
* Contributor or higher access to that workspace
* An authorized Shareable Cloud `FabricSql` connection using OAuth2

The local machine requires Python 3.11–3.13 and an authenticated Azure CLI.

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
  -SqlConnection "<connection-name-or-id>"
```

The access token issued by `az login` determines the Fabric tenant. The
deployer accepts either a workspace name or ID, but the ID is recommended
because it is unambiguous. If Azure CLI is already signed into the correct
tenant, another login is not required.

Use `-DryRun` to validate the workspace and print the deployment plan without
creating items:

```powershell
.\deployment\deploy.ps1 -Workspace "<workspace-id>" -DryRun
```

Dry-run resolves the connection, verifies that it is a Shareable Cloud
`FabricSql` connection using OAuth2, and calls the Fabric connection test API.
An offline, expired, ambiguous, missing, or incorrectly typed connection stops
the deployment before any workspace item is created.

## Create the SQL connection

OAuth2 authorization requires an interactive user consent flow, so the
installer does not create or silently replace the connection.

1. In Fabric, open **Settings** and select
   **Manage connections and gateways**.
2. Create a new **Cloud** connection of type **Fabric SQL database**.
3. Name it `conn_sap_finance_sql`, or record its connection ID for
   `-SqlConnection`.
4. Select **OAuth 2.0**, sign in, and save the connection.
5. Run the installer with `-DryRun` before deployment.

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
5. Deploy and bind all nine transformation and validation notebooks under `notebooks`
6. Deploy the Direct Lake semantic model under `semantic models`
7. Deploy the report under `reports`
8. Deploy the Finance Data Agent under `agents`
9. Deploy the pipeline under `pipelines`
10. Validate the final item inventory

The folder structure is:

```text
agents
database
lakehouses
notebooks
pipelines
reports
semantic models
```

The pipeline uses a parameterized, sequential `ForEach` with a nested Copy
activity to read complete table projections from `SAP_FIN_DB` into dated
Bronze CSV snapshots. The source query has no watermark or incremental
predicate. Table names, selected columns, source objects, and sink paths are
configured through the `p_table_config` pipeline parameter.

The semantic model uses Direct Lake on SQL. The installer resolves the Gold
Lakehouse SQL endpoint and database name and substitutes them into the TMDL
expression before deployment.

The installer is idempotent with `-Overwrite`. Generated definitions are
checked for unresolved deployment tokens and source-tenant workspace, item,
notebook, semantic model, and connection identifiers before publication. It
never runs the data pipeline. After a successful deployment, the team runs
`pl_full_medallion_sap_finance` to create Silver, Gold, the Materialized Lake
Views, and validation evidence.

## Authentication

For an interactive deployment:

```powershell
az login --tenant "<tenant-id>" --allow-no-subscriptions
```

For automation, sign in Azure CLI with a service principal before invoking the
same command. The principal must have Contributor or higher access to the
target workspace and permission to read and test the selected connection. The
connection must already exist and be shared with the principal.

## Failure behavior

Each deployment step is validated before its dependants start. The installer:

* Tests OAuth connection health before workspace mutation
* Rejects ambiguous names and incompatible item-type collisions
* Rebinds notebook, Lakehouse, SQL database, report, model, and agent metadata
* Rejects source-tenant identifiers and unresolved deployment tokens
* Prints each created or reused item ID and stops on the first failure

It does not delete pre-existing workspace items or perform an automatic
rollback.
