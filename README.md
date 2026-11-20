---
title: SAP Finance Fabric solution accelerator
description: Deploy a complete SAP Finance medallion analytics solution into Microsoft Fabric
author: Data and AI Engineering
ms.date: 2026-11-19
ms.topic: overview
keywords:
  - Microsoft Fabric
  - SAP Finance
  - medallion architecture
  - Direct Lake
estimated_reading_time: 2
---

## Overview

A deployable Microsoft Fabric accelerator for SAP Finance analytics. It
includes hybrid read-only ingestion, governed Silver upserts, a Kimball Gold
model, Materialized Lake Views, Direct Lake reporting, and a Fabric Data
Agent.

> [!IMPORTANT]
> All included data is synthetic demonstration data.

## Deploy

You need an existing Fabric workspace on active capacity, Python 3.11–3.13,
Azure CLI, and Contributor access.

```powershell
az login --tenant "<tenant-id>" --allow-no-subscriptions
.\deployment\deploy.ps1 -Workspace "<workspace-id>"
```

Then run `pl_full_medallion_sap_finance` in the deployed workspace.
Its `AUTO` operation initializes configured fiscal-year partitions once, then
uses BKPF document-entry discovery and master-data snapshot comparison.
Run `RECONCILE` on an accounting-approved schedule to revisit older documents.

> [!IMPORTANT]
> Document discovery is not CDC or complete source change capture. Historical
> amendments, clearing, removals and headerless journal records require
> complete partition reconciliation. No CDC service or ABAP add-on is used.

## Architecture

![SAP Finance Fabric accelerator architecture](./deployment/fabric-architecture.svg)

## Included

| Capability | Implementation |
|------------|----------------|
| Source | Seeded Fabric SQL Database simulating SAP HANA Finance |
| Ingestion | BKPF entry windows, fiscal-year refreshes, full snapshots and snapshot comparison |
| Medallion | Bronze files, Silver business-key MERGE, Kimball Gold |
| Analytics | Four Materialized Lake Views and Direct Lake on SQL |
| Consumption | Executive report and Finance Data Agent |
| Automation | Idempotent REST deployment, bounded Copy concurrency and durable replay |

## Documentation

* [Automated deployment](./deployment/README.md)
* [Pipeline and replay](./artefacts/pipeline/README.md)
* [Medallion notebooks](./artefacts/medallion/README.md)
* [SAP source and production transition](./artefacts/sap/README.md)
* [Semantic model](./artefacts/semantic/README.md)
* [Data Agent configuration](./artefacts/dataAgent/dataAgent.md)
