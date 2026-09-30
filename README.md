---
title: Eskom SAP Finance Fabric solution accelerator
description: Architecture, deployment, and validation guide for the synthetic SAP Finance accelerator on Microsoft Fabric
author: Data and AI Engineering
ms.date: 2026-09-30
ms.topic: tutorial
keywords:
  - Microsoft Fabric
  - SAP Finance
  - medallion architecture
  - Kimball
  - Direct Lake
estimated_reading_time: 10
---

## Solution synopsis

This accelerator demonstrates how SAP Finance data can move from SAP HANA
into Microsoft Fabric and become governed analytical products for finance
reporting and natural-language analysis.

It provides:

* Full-snapshot extraction for 13 SAP and reference objects
* Bronze, Silver, and Gold processing in schema-enabled Lakehouses
* A Kimball-style Finance model with SCD Type 2 dimensions
* Atomic facts and monthly Materialized Lake Views
* A Direct Lake semantic model and executive Power BI report
* A Fabric Data Agent configured for governed Finance questions

> [!IMPORTANT]
> The included records are synthetic demonstration data. Do not represent any
> output as actual Eskom financial, supplier, customer, or operational results.

## Architecture

```text
SAP HANA or SAP_FIN_DB simulator
        |
        | Full-snapshot Fabric Data Factory Copy
        v
lh_brz_slv_finance
  Bronze CSV -> Silver Delta -> quarantine and audit
        |
        | Fabric notebooks
        v
lh_gld_finance
  dim SCD2 dimensions
  fct atomic facts
  mlv monthly Materialized Lake Views
        |
        +-> Direct Lake semantic model -> Executive report
        |
        +-> Lakehouse SQL endpoint -> Fabric Data Agent
```

> [!IMPORTANT]
> The selected SAP HANA tables do not provide one reliable technical watermark
> column that captures inserts, amendments, late postings, reversals, and
> deletes. Business dates such as posting or document date are not change
> timestamps. The pipeline therefore takes dated full snapshots and performs
> business-key Silver upserts rather than presenting a synthetic watermark as
> CDC.

## Medallion and Kimball design

| Layer | Design |
|-------|--------|
| Bronze | Dated, source-shaped CSV extracts under `Files/bronze/sap_finance` |
| Silver | Current-state Delta tables in `slv`, upserted by SAP business key, with quarantine and audit records |
| Gold | Conformed SCD2 dimensions in `dim`, atomic facts in `fct`, and monthly analytical views in `mlv` |

The Gold layer is a Kimball-style fact constellation. Shared dimensions filter
all compatible facts with one-to-many, single-direction relationships.
Dimensions use version-level surrogate keys so finance postings retain the
correct historical master-data context.

| Object type | Objects |
|-------------|---------|
| Dimensions | Company, GL Account, Cost Center, Profit Center, Division, Vendor, Customer, Date, Fiscal Period |
| Atomic facts | Finance Transaction at ACDOCA document-line grain; Budget and Plan at source, scenario, entity, period, and version grain |
| Monthly views | Finance Actual, Vendor Spend, GL Balance, and Plan vs Actual |

Fiscal periods use `FISCAL_YEAR * 100 + FISCAL_PERIOD` as a single relationship
key. This supports SAP periods 13 through 16, which do not map to unique
calendar dates.

## Repository contents

| Folder | Purpose |
|--------|---------|
| `artefacts` | Deployable Fabric definitions, notebooks, SQL source simulator, semantic model, report, Data Agent, and synthetic data |
| `deployment` | Single-command Python installer, manifest, tests, and deployment runbook |

## Prerequisites

Fabric prerequisites:

* An existing Microsoft Fabric tenant
* An existing workspace on active Fabric capacity
* Contributor or higher access to that workspace

Deployment host requirements:

* Python 3.11–3.13
* Azure CLI authenticated to the Fabric tenant

## Automated deployment

The only Fabric prerequisites are an existing tenant and an existing workspace
on active capacity. The installer creates and binds the remaining items in
dependency order, organizes them into workload folders, seeds `SAP_FIN_DB`,
and leaves the pipeline ready to run.

```text
agents/
database/
lakehouses/
notebooks/
pipelines/
reports/
semantic models/
```

```powershell
.\deployment\deploy.ps1 -Workspace "My Fabric Workspace"
```

Use a dry run to validate authentication, workspace resolution, capacity, and
item conflicts:

```powershell
.\deployment\deploy.ps1 -Workspace "My Fabric Workspace" -DryRun
```

See the [deployment runbook](./deployment/README.md) for conflict handling,
service-principal authentication, chronological deployment, and validation.

After deployment, run `pl_full_medallion_sap_finance` in Fabric. No other item
creation or data-loading step is required.

## Manual deployment reference

The installer deploys these notebooks in order:

1. `00_Source_Full_Snapshot`
2. `00_Setup_and_Namespace_Validation`
3. `01_Bronze_CSV_Inventory`
4. `02_Bronze_CSV_to_Silver_Delta`
5. `03_Gold_SCD2_Dimensions`
6. `04_Gold_Atomic_Finance_Fact`
7. `05_Gold_Budget_Fact`
8. `06_Gold_Materialized_Lake_Views`
9. `07_End_to_End_Validation`
10. `08_Bronze_Snapshot_Cleanup`

Notebooks 04 and 05 can run in parallel after notebook 03 succeeds.

Run the pipeline without parameter input. The immutable run date controls
Bronze filenames and notebooks 01, 02, and 08. Silver upserts the full snapshot
by business key. After validation, the current Bronze snapshot is retained for
replay and older snapshots are audited and deleted.

Confirm that:

* All 13 source objects land in their expected Bronze folders
* Invalid rows are quarantined and valid rows are upserted into Silver
* Gold dimension keys are unique and mandatory fact keys resolve
* Document-level signed amounts remain balanced
* All four monthly views are populated
* Notebook 07 completes without failed checks

### Semantic model and report

Definitions are under [artefacts/semantic](./artefacts/semantic). The installer
rebinds:

* Direct Lake workspace and `lh_gld_finance` item IDs
* Report connection to the deployed `SAP Finance` semantic model

Retain single-direction dimension-to-fact relationships, hide technical keys,
disable implicit measures, and verify the report pages and measures against
the Gold validation results.

### Fabric Data Agent

Create a Data Agent with only the `lh_gld_finance` SQL endpoint as its data
source. The copy-ready source material is in
[dataAgent.md](./artefacts/dataAgent/dataAgent.md):

* Agent and source instructions
* Data source topics
* Ten tested question and SQL pairs
* Schema object descriptions

Do not expose Bronze, Silver, audit, quarantine, or restricted tax identifier
fields to the agent.

## Production transition

Before connecting to production SAP HANA:

* Confirm ledger, company, chart-of-accounts, currency, sign, reversal, fiscal
  calendar, language, and special-period rules with SAP Finance owners
* Replace demo reference tables with approved SAP Z tables or governed sources
* Retain full snapshots unless an SAP-supported change and deletion feed is
  proven complete for a specific source
* Use managed identity or approved OAuth, least privilege, private
  connectivity, and secret-free configuration
* Parameterize all workspace, item, schema, and connection identifiers
* Define reconciliation totals, operational alerts, replay procedures, and
  capacity monitoring

## Detailed runbooks

* [Deployment runbook](./deployment/README.md) covers automated installation
* [SAP source runbook](./artefacts/sap/README.md) covers the source simulator and SAP
  HANA transition
* [Pipeline runbook](./artefacts/pipeline/README.md) covers full-snapshot operation,
  Silver upserts, and Bronze retention
* [Medallion runbook](./artefacts/medallion/README.md) covers Lakehouse layout and
  notebook behavior
* [Semantic model runbook](./artefacts/semantic/README.md) covers the fact constellation
  and relationships
* [Data Agent runbook](./artefacts/dataAgent/dataAgent.md) contains the complete agent
  configuration
