---
title: SAP Finance full-snapshot Fabric pipeline
description: Runtime flow for the deployed SAP Finance accelerator
author: Data and AI Engineering
ms.date: 2026-10-02
ms.topic: reference
keywords:
  - Microsoft Fabric
  - Data Factory
  - SAP Finance
  - full snapshot
estimated_reading_time: 3
---

## Pipeline flow

```text
Capture immutable run timestamp
        ↓
Parameterized full-snapshot Copy loop
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

`fe_copy_sap_full_snapshot` iterates over `p_table_config` and runs
`cpy_full_table_snapshot` for each of the 13 configured source projections.
Each Copy activity selects the configured columns without an incremental
predicate and writes a dated CSV snapshot to the configured Bronze folder.

## Runtime contract

The `p_table_config` array controls each source object, selected column list,
file prefix, and Bronze folder. `run_upper_bound_utc` is captured once and its
date controls every Bronze filename and downstream `processing_date`.

The pipeline does not claim source CDC. Full snapshots are required because
the selected SAP HANA tables do not expose one reliable technical watermark
that captures inserts, amendments, late postings, reversals, and deletes.

## Processing and replay

Notebook 02 validates the full snapshot and merges valid rows into Silver by
the configured SAP business key:

* Existing keys are updated
* New keys are inserted
* Invalid rows are quarantined
* Source deletes are not inferred

Any table error or quarantined row fails notebook 02. Bronze is retained for
diagnosis and replay, and downstream Gold and cleanup steps do not run.

Notebook 08 runs only after notebook 07 succeeds. It retains the current
run-date files and deletes older dated snapshots. Every retained or deleted
file is recorded in `audit.bronze_cleanup`.
