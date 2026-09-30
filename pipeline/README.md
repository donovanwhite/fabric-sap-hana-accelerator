---
title: SAP Finance full-snapshot Fabric pipeline
description: Configure and operate the full-table SAP Finance ingestion pipeline
author: Data and AI Engineering
ms.date: 2026-09-29
ms.topic: tutorial
keywords:
  - Microsoft Fabric
  - Data Factory
  - SAP Finance
  - full snapshot
estimated_reading_time: 5
---

## Pipeline flow

```text
Capture immutable run timestamp
        ↓
Sequential ForEach over 13 source projections
        └─ Full Copy to <table>_yyyyMMdd.csv
        ↓
00 Setup and validation
        ↓
01 Bronze inventory
        ↓
02 Silver business-key upserts
        ↓
03 Gold dimensions
        ├─ 04 Finance fact
        └─ 05 Budget fact
        ↓
06 Materialized Lake Views
        ↓
07 End-to-end validation
        ↓
08 Retain current Bronze snapshot and delete older snapshots
```

> [!IMPORTANT]
> No source watermark or CDC is assumed. The selected SAP HANA tables do not
> expose one reliable technical column that captures inserts, amendments,
> late postings, reversals, and deletes. Posting and document dates describe the
> business event, not when the source row changed. Full snapshots are therefore
> the correctness baseline.

## Runtime contract

The pipeline has one parameter:

| Parameter | Purpose |
|-----------|---------|
| `p_table_config` | Thirteen source objects, projections, Bronze folders, and file prefixes |

`run_upper_bound_utc` is captured once. Its date controls every Bronze filename
and the `processing_date` passed to notebooks 01, 02, and 08.

## Source and destination

The Copy activity queries the complete projection from each `SAPABAP1` or
`SAPDEMO` source table. Review the contracts in:

* [factory-copy-contract.json](../sap/factory-copy-contract.json)
* [factory-copy-queries.sql](../sap/factory-copy-queries.sql)

The destination is:

```text
lh_brz_slv_finance/Files/bronze/sap_finance/<table>/<table>_yyyyMMdd.csv
```

## Connection binding

The current definition uses:

* Workspace: `ws_finance`
* Source: `SAP_FIN_DB`
* Connection: `conn_sap_finance_sql`
* Destination: `lh_brz_slv_finance`

For another environment, replace the source connection, workspace, SQL
Database, Lakehouse, and notebook item IDs.

Use only [pipeline-content.json](./pipeline-content.json) in the Fabric
pipeline source-code editor. The Copy activity is configured inline and does
not invoke a separate Copy Job.

## Processing and replay

Notebook 02 validates the full snapshot and merges valid rows into Silver by
the configured SAP business key:

* Existing keys are updated
* New keys are inserted
* Invalid rows are quarantined
* Source deletes are not inferred

Any table error or quarantined row fails notebook 02. The Bronze snapshot is
then retained for diagnosis and replay, and downstream Gold and cleanup steps
do not run.

Notebook 08 runs only after notebook 07 succeeds. It retains the current
run-date files and deletes older dated snapshots. This preserves replay for
the latest successful period without unbounded Bronze growth. Every retained
or deleted file is recorded in `audit.bronze_cleanup`.

## Validation

After import:

1. Confirm the `ForEach` contains one configured full-snapshot Copy activity.
2. Confirm notebooks 01 and 02 receive `processing_date`.
3. Confirm notebook 08 depends on successful notebook 07 completion.
4. Run without parameter input.
5. Confirm 13 dated Bronze files are created.
6. Confirm Silver contains one row per business key.
7. Confirm the current Bronze snapshot remains and older snapshots are audited
   and deleted.
