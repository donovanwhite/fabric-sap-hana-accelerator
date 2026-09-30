---
title: Eskom SAP Finance Fabric solution accelerator
description: Architecture, deployment, and validation guide for the synthetic SAP Finance accelerator on Microsoft Fabric
author: Data and AI Engineering
ms.date: 2026-09-29
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
        | Incremental Fabric Data Factory Copy
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
| `sap` | SQL source simulator, synthetic loader, HANA projections, and extraction contract |
| `sap_synthetic_data` | Synthetic SAP Finance CSV snapshot |
| `pipeline` | Native full-snapshot pipeline definition, bindings, and runbook |
| `medallion` | Ordered notebooks for Bronze, Silver, Gold, views, and validation |
| `semantic` | Direct Lake TMDL model and executive Power BI report definition |
| `dataAgent` | Copy-ready Fabric Data Agent instructions, topics, examples, and validation |

## Prerequisites

* A Microsoft Fabric workspace on supported capacity
* Permission to create Lakehouses, notebooks, pipelines, semantic models,
  reports, SQL Database items, and Data Agents
* A managed SAP HANA connection, or the included `SAP_FIN_DB` simulator
* Organizational Account OAuth access to the source
* Python 3.11 or `uv`, Azure CLI, and ODBC Driver 18 when loading the simulator

## Deploy the accelerator

### 1. Create the Fabric data stores

Create these schema-enabled Lakehouses with the exact names:

* `lh_brz_slv_finance`
* `lh_gld_finance`

For the demonstration source, create a Fabric SQL Database named
`SAP_FIN_DB`, run [schema.sql](./sap/schema.sql), then seed it:

```powershell
uv run .\sap\load_synthetic_data.py --mode initial
```

For SAP HANA, retain the approved business projections but replace the demo
database and schema with environment-specific values.

### 2. Import and configure the notebooks

Import the eight notebooks from [medallion](./medallion). Attach both
Lakehouses before the Spark session starts. Set `lh_gld_finance` as the
default Lakehouse for notebook 06.

Run order:

1. `00_Setup_and_Namespace_Validation`
2. `01_Bronze_CSV_Inventory`
3. `02_Bronze_CSV_to_Silver_Delta`
4. `03_Gold_SCD2_Dimensions`
5. `04_Gold_Atomic_Finance_Fact`
6. `05_Gold_Budget_Fact`
7. `06_Gold_Materialized_Lake_Views`
8. `07_End_to_End_Validation`

Notebooks 04 and 05 can run in parallel after notebook 03 succeeds.

### 3. Configure the full-snapshot pipeline

Create an Organizational Account connection named
`conn_sap_finance_sql`. In an existing Fabric pipeline source-code editor,
paste
[pipeline-content.json](./pipeline/pipeline-content.json),
then update:

* Source connection ID
* Target workspace and Lakehouse IDs
* All eight notebook IDs and their workspace ID

Use only the native pipeline content file. Validate that the sequential Copy
loop completes before notebook 00 starts.

### 4. Run and validate the data platform

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

### 5. Deploy the semantic model and report

Deploy the definitions under [semantic](./semantic), then replace the
environment-specific references:

* Direct Lake workspace and `lh_gld_finance` item IDs
* Report connection to the deployed `SAP Finance` semantic model

Retain single-direction dimension-to-fact relationships, hide technical keys,
disable implicit measures, and verify the report pages and measures against
the Gold validation results.

### 6. Configure the Fabric Data Agent

Create a Data Agent with only the `lh_gld_finance` SQL endpoint as its data
source. Follow [dataAgent.md](./dataAgent/dataAgent.md) to add:

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
* Select a supported delta strategy for every source table
* Use managed identity or approved OAuth, least privilege, private
  connectivity, and secret-free configuration
* Parameterize all workspace, item, schema, and connection identifiers
* Define reconciliation totals, operational alerts, replay procedures, and
  capacity monitoring

## Detailed runbooks

* [SAP source runbook](./sap/README.md) covers the source simulator and SAP
  HANA transition
* [Pipeline runbook](./pipeline/README.md) covers full-snapshot operation,
  Silver upserts, and Bronze retention
* [Medallion runbook](./medallion/README.md) covers Lakehouse layout and
  notebook behavior
* [Semantic model runbook](./semantic/README.md) covers the fact constellation
  and relationships
* [Data Agent runbook](./dataAgent/dataAgent.md) contains the complete agent
  configuration
