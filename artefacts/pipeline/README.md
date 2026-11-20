---
title: SAP Finance hybrid extraction pipeline
description: Runtime flow for the deployed SAP Finance accelerator
author: Data and AI Engineering
ms.date: 2026-11-20
ms.topic: reference
keywords:
  - Microsoft Fabric
  - Data Factory
  - SAP Finance
  - incremental extraction
estimated_reading_time: 7
---

## Pipeline flow

```text
Capture immutable run timestamp
        ↓
00 Validate contracts, acquire writer and persist extraction plan
        ↓
Bounded parallel Copy loop
        └─ 01 Validate immutable Bronze receipt and source controls
        ↓
02 Validate and atomically MERGE each healthy Silver scope
        ↓
03 Gold dimensions
        ├─ 04 Finance fact
        └─ 05 Budget fact
        ↓
06 Materialized Lake Views
        ↓
07 End-to-end validation
        ↓
08 Commit entry boundaries and retain Bronze
```

The existing pipeline and Copy activity names are retained for deployment
compatibility. Their historical `full_snapshot` names do not restrict the
extraction mode. The deployer compiles `p_table_config` from
[`factory-copy-contract.json`](../sap/factory-copy-contract.json), the single
source contract. No separate orchestration service or database is introduced.

| Mode | Source read | Silver absence semantics |
|------|-------------|--------------------------|
| `FULL_SNAPSHOT` | Complete approved projection | Upsert only, preserving legacy behavior |
| `DOCUMENT_INCREMENTAL` | Inclusive BKPF entry window, joined to ACDOCA by complete document key | Never infer deletion |
| `PARTITION_REFRESH` | Complete configured fiscal-year/company/ledger scope | Delete absent keys only inside the validated scope |
| `SNAPSHOT_COMPARE` | Complete master/reference projection | Hash-based changes and confirmed scoped removals |

## Runtime contract

| Pipeline parameter | Use |
|--------------------|-----|
| `p_operation` | `AUTO` (default), `INITIALIZE`, `INCREMENTAL`, `FULL_SNAPSHOT`, `PARTITION_REFRESH`, or `RECONCILE` |
| `p_run_id` | Empty for a new pipeline run; set to the original logical run ID for recovery |
| `p_partitions` | Optional explicit refresh requests, shown below |
| `p_max_parallelism` | Must match the deployed source `maxConcurrency`; change the contract and redeploy to alter the bound |
| `p_table_config` | Validated table metadata, generated from the shared contract |

`AUTO` chooses partitioned initialization when no compatible committed
checkpoint exists, otherwise incremental ingestion. `INCREMENTAL` fails
explicitly if initialization has not completed. Initialization coverage is
the explicit `partitionValues` inventory, not an assertion that all SAP years,
companies or ledgers have been read.

Notebook 00 captures the original run upper boundary and persists all queries,
partition scopes and attempt paths together with complete work membership in
one Delta transaction. A missing work record blocks replay/publication rather
than silently turning a partial plan into a complete run. BKPF `CPUDT/CPUTM` are compared as
inclusive date/time pairs using the configured SAP source timezone and overlap
hours. Timestamp ties are expected. Successful empty windows advance to their
captured upper bound, not a maximum timestamp found in returned rows.

Deployment binds source identity to the managed connection, a hash of its
connection definition, and SQL simulator geometry where applicable. A
checkpoint is specific to the source instance and table contract. Scheduling,
concurrency and overlap-policy adjustments do not force unrelated historical
initialization; changing the physical source or its projection does not reuse
another source's checkpoint. An unfinished run still requires its exact pinned
contract on replay.

Fabric requires a native integer `batchCount`, not an expression object. The
deployer compiles it from source `maxConcurrency`; notebook 00 rejects a
different per-run value before acquiring the writer or querying the source.
The ForEach is parallel, while each Copy uses `parallelCopies=1` and
`partitionOption=None`. These logical partitions do not assume physical HANA
partitioning or multiply source concurrency through nested parallelism.
Start conservatively; validate representative HANA execution plans before
raising the source-specific limit.

## Initialization and operations

Run the existing pipeline with `p_operation=INITIALIZE` to initialize the
configured years. Run `INCREMENTAL` for document discovery and master
snapshots; neither posting date nor fiscal year is a change watermark.

For an on-demand refresh, use `p_operation=PARTITION_REFRESH` with:

```json
[
  {
    "table": "ACDOCA",
    "partition": {"GJAHR": "2025", "RBUKRS": "1000", "RLDNR": "0L"}
  }
]
```

`RECONCILE` selects due configured partitions. The shared contract specifies
`CURRENT_FISCAL_YEAR`, `PREVIOUS_FISCAL_YEAR`, and
`HISTORICAL_FISCAL_YEARS` intervals in days. An explicit `p_partitions`
request overrides due-date selection, supporting `ON_DEMAND_PARTITIONS`.
Schedule this operation through the existing Fabric pipeline scheduler.
The supplied daily/weekly/30-day intervals are synthetic starting points;
Finance owns the fiscal-year label, inventory and accounting-close frequency.

## Processing and replay

Each source SELECT returns business rows, per-key/currency financial control
rows and one total marker, including empty scopes. Controls are calculated
inside the same source statement. Notebook 01 checks the landed row count
against Copy output and source controls. Notebook 02 repeats validation,
checks keys and exact decimals, journals changed keys, then publishes each
scope in one Delta MERGE. Failed or incomplete extracts cannot authorize
deletions or replace a valid partition.

`audit.extraction_batch` stores plans, receipts, publication metrics and
commit states. `audit.extraction_changes` records complete typed affected keys
for incremental Gold processing and confirmed deletes. The source writer
lock is a control row in the same Delta batch table.

Checkpoint publication occurs only in notebook 08, after Gold, MLVs and
end-to-end validation succeed. A partial failure can leave independent
healthy Silver scopes published, but no run checkpoint advances. The
affected-key journal and pinned Silver baseline make replay idempotent even
when a process stops after MERGE but before recording its acknowledgement.

To recover, stop the failed pipeline execution, retain its contract and
operation, and rerun with its `p_run_id`. Do not run two executions of the
same logical run concurrently. Failed copies receive a new immutable attempt
path; validated extractions are reused, and published scopes are not copied
again. Failed-attempt paths, metrics and errors remain in durable attempt
history, and quarantined rows retain the exact source path. A new unrelated
run is rejected while a failed run owns the writer.
There is no automatic stale-lock expiry or destructive reset.

Bronze uses the established root with additional logical scope and run
folders:

```text
Files/bronze/sap_finance/acdoca/gjahr=2026/rbukrs=1000/rldnr=0L/
  run_id=<run>/work_id=<work>/attempt_id=<attempt>/data.csv
```

Ordinary ingestion never deletes prior Bronze attempts. Configure a separate,
approved retention procedure that protects replay baselines, failed attempts
and the reconciliation horizon. Delta VACUUM must not remove pinned baseline
versions while an uncommitted run may need recovery.

## Evidence and limits

Inspect batch `PLAN_JSON` and `METRICS_JSON` for boundaries, scope, source and
target counts, insert/update/delete counts, bytes reported by Copy, duration
and reconciliation failures. Unreported connector metrics are null, not
fabricated measurements. Control rows add transfer overhead; performance
comparisons must include that overhead.

This is not CDC. Subsequent changes to old documents, clearing/reset,
archiving/removal and unmatched BKPF relationships require partition
reconciliation. Reads across different source objects are not a shared
database transaction. Partition controls prove completeness of the approved
query result, not a complete capture of every intermediate source change.
Authoritative absence assumes the approved read scope and permissions remain
stable; review permission, filtering and archiving changes before treating
absence as a business deletion.
