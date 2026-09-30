---
title: SAP Finance Fabric automated deployment
description: Deploy the complete accelerator into an existing Microsoft Fabric workspace
author: Data and AI Engineering
ms.date: 2026-09-30
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

The local machine requires Python 3.11–3.13 and an authenticated Azure CLI.

## Deploy

From the repository root:

```powershell
.\deployment\deploy.ps1 -Workspace "My Fabric Workspace"
```

Use `-DryRun` to validate the workspace and print the deployment plan without
creating items:

```powershell
.\deployment\deploy.ps1 -Workspace "My Fabric Workspace" -DryRun
```

Deployment fails on conflicting item names by default. Use `-Overwrite` to
update compatible existing items:

```powershell
.\deployment\deploy.ps1 -Workspace "My Fabric Workspace" -Overwrite
```

## Deployment order

1. Validate workspace and capacity
2. Create/reuse workload folders
3. Create both schema-enabled Lakehouses under `lakehouses`
4. Create and seed `SAP_FIN_DB` under `database`
5. Deploy and bind all ten notebooks under `notebooks`
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

The pipeline uses `00_Source_Full_Snapshot` to read `SAP_FIN_DB` with the
pipeline execution identity. It does not require a separately created OAuth
connection.

The semantic model uses Direct Lake on SQL. The installer resolves the Gold
Lakehouse SQL endpoint and database name and substitutes them into the TMDL
expression before deployment.

The installer is idempotent with `-Overwrite`. It never runs the data pipeline.
After a successful deployment, the team runs
`pl_full_medallion_sap_finance` to create Silver, Gold, the Materialized Lake
Views, and validation evidence.

## Authentication

For an interactive deployment:

```powershell
az login --allow-no-subscriptions
```

For automation, sign in Azure CLI with a service principal before invoking the
same command. The principal must have Contributor or higher access to the
target workspace and tenant permission to create Fabric items and connections.

## Failure behavior

Each deployment step is validated before its dependants start. The installer
prints the created or reused item IDs and stops on the first failed operation.
It does not delete pre-existing workspace items or perform an automatic
rollback.
