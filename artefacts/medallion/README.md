---
title: SAP Finance Fabric medallion notebooks
description: Run order, data contracts, and processing behavior for the schema-enabled Finance Lakehouses
author: Data and AI Engineering
ms.date: 2026-11-19
ms.topic: reference
keywords:
  - Microsoft Fabric
  - medallion architecture
  - SAP Finance
  - Delta Lake
estimated_reading_time: 4
---

## Lakehouse design

| Lakehouse | Content |
|-----------|---------|
| `lh_brz_slv_finance` | Immutable run/partition Bronze files, current-state `slv` Delta tables, `quarantine`, and durable `audit` controls |
| `lh_gld_finance` | SCD2 dimensions in `dim`, atomic facts in `fct`, and monthly Materialized Lake Views in `mlv` |

Attach both schema-enabled Lakehouses before the Spark session starts.
Notebook 06 requires `lh_gld_finance` as its default Lakehouse.

## Run order

1. `00_Setup_and_Namespace_Validation` plans and persists logical work
2. Existing pipeline Copy loop, followed by `01_Bronze_CSV_Inventory` per work item
3. `02_Bronze_CSV_to_Silver_Delta`
4. `03_Gold_SCD2_Dimensions`
5. `04_Gold_Atomic_Finance_Fact`
6. `05_Gold_Budget_Fact`
7. `06_Gold_Materialized_Lake_Views`
8. `07_End_to_End_Validation`
9. `08_Bronze_Snapshot_Cleanup` commits checkpoints and retains Bronze

Notebooks 04 and 05 can run in parallel after notebook 03.

## Run and source contract

The existing deployment embeds the tested shared `sap_extraction.py` and
`sap_runtime.py` modules and the approved
[`factory-copy-contract.json`](../sap/factory-copy-contract.json) in notebooks
00, 01, 02 and 08. They do not download code or import a developer's local path
in Fabric. Gold notebooks receive the same logical `run_id`.

```text
Files/bronze/sap_finance/<table>/<logical-partition>/
  run_id=<run>/work_id=<work>/attempt_id=<attempt>/data.csv
```

Notebook 00 requires a run ID and captured UTC upper bound. Notebook 01
requires the planned work ID and successful Copy output. Notebooks 02 and
08 require the original run ID. Missing, malformed or incomplete extractions
fail explicitly; there is no success-shaped fallback to today's files.
See the [pipeline operations](../pipeline/README.md) for initialization,
incremental, partition refresh, reconciliation and recovery parameters.

## Silver contract

Silver is a current-state layer. Valid full-snapshot rows are upserted by SAP
business key, rejected extraction rows reach `quarantine`, and operational
results reach `audit`. Incremental discovery never infers deletion.
`PARTITION_REFRESH` and `SNAPSHOT_COMPARE` permit scoped deletion only after
source controls, uniqueness, currency amounts and completeness pass.

Every Silver row contains:

* `BATCH_RUN_ID`
* `EXTRACT_DATE`
* `LOAD_DATE`
* `SOURCE_SYSTEM`
* `SOURCE_FILE_NAME`
* `DQ_PASSED`
* `DQ_FAILURE_REASON`
* `DQ_WARNING`

Rows also retain `ROW_HASH`, `SOURCE_SCHEMA`, `SOURCE_TABLE`,
`EXTRACTION_MODE`, `PARTITION_ID`, `EXTRACTED_AT_UTC` and applicable entry
boundaries. Hashes cover only the explicitly ordered, typed business
projection. Unchanged rows are not updated on ordinary replay.

Required nulls, cast/scale failures, invalid periods/times, inverted validity
intervals and duplicate keys reject the extraction before publication.
Financial amounts remain decimal. Complete CSKS/CEPC keys include `DATBI`;
different source-validity slices are not collapsed.

Each Silver scope publishes in one Delta MERGE. The affected-key journal is
durable before publication, and the original Delta baseline is pinned for
recovery. Reconciliation failure restores the last valid scope. Independent
healthy scopes may complete, but an incomplete run cannot advance checkpoints.

## Bronze retention

Notebook 08 runs only after successful end-to-end validation and atomically
marks the run's published work committed. Successful empty entry windows also
commit their captured boundary. Bronze is retained, including failed attempts.
Do not VACUUM pinned Delta baseline versions or delete Bronze until an
approved retention/recovery policy permits it.

## Gold contract

Dimensions are Company, GL Account, Cost Center, Profit Center, Division,
Vendor, Customer, Date, and Fiscal Period. Atomic facts are Finance
Transaction and Budget and Plan. Monthly views cover Finance Actual, Vendor
Spend, GL Balance, and Plan vs Actual.

Notebook 03 calculates an all-column SHA-256 change hash for each SCD2
dimension. Facts relate through version-level surrogate keys, preserving the
master-data context effective at posting time.

Gold reads the durable affected-key journal, not only the latest Silver batch
ID. BKPF-only changes fan out to all matching ledger lines. Master changes
repair the relevant fact dependencies without assigning historical postings
to today's dimension version. Confirmed source removals delete only journaled
fact keys; dimensions retain historical versions and expire removed masters.

Finance retains RTCUR additively. Planning retains client, ledger and local
currency; custom budget context is explicit metadata. Legacy Gold currency
backfill uses atomic schema-evolving MERGE and requires Delta 3.2 or newer.
Missing or ambiguous legacy identity fails with recovery guidance, never a
destructive overwrite. Monetary MLV groups and semantic measures preserve
client/ledger/currency scope. See the [semantic contract](../semantic/README.md).

Validate ledger scope, currency and sign semantics, fiscal periods, reversals,
chart of accounts, controlling area, language, and business-key derivation
with SAP Finance owners before production use.
