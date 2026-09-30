---
title: SAP Finance Fabric medallion notebooks
description: Run order, data contracts, and processing behavior for the schema-enabled Finance Lakehouses
author: Data and AI Engineering
ms.date: 2026-09-29
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
| `lh_brz_slv_finance` | Dated Bronze CSV files, current-state `slv` Delta tables, `quarantine`, and `audit` |
| `lh_gld_finance` | SCD2 dimensions in `dim`, atomic facts in `fct`, and monthly Materialized Lake Views in `mlv` |

Attach both schema-enabled Lakehouses before the Spark session starts.
Notebook 06 requires `lh_gld_finance` as its default Lakehouse.

## Run order

1. `00_Setup_and_Namespace_Validation`
2. `01_Bronze_CSV_Inventory`
3. `02_Bronze_CSV_to_Silver_Delta`
4. `03_Gold_SCD2_Dimensions`
5. `04_Gold_Atomic_Finance_Fact`
6. `05_Gold_Budget_Fact`
7. `06_Gold_Materialized_Lake_Views`
8. `07_End_to_End_Validation`
9. `08_Bronze_Snapshot_Cleanup`

Notebooks 04 and 05 can run in parallel after notebook 03.

## Run-date contract

Notebooks 01 and 02 accept `processing_date` in `yyyy-MM-dd` format. The
pipeline passes the immutable run date used in each Bronze filename:

```text
Files/bronze/sap_finance/<table>/<table>_yyyyMMdd.csv
```

Only that file is inventoried and loaded. Missing or unreadable run-date files
fail the pipeline. Interactive execution defaults to the current UTC date.
All 13 source contracts, including division and GL account references, are
covered.

## Silver contract

Silver is a current-state layer. Valid full-snapshot rows are upserted by SAP
business key, invalid rows reach `quarantine`, and operational results reach
`audit`. Source deletes are not inferred without an authoritative deletion
indicator.

Every Silver row contains:

* `BATCH_RUN_ID`
* `EXTRACT_DATE`
* `LOAD_DATE`
* `SOURCE_SYSTEM`
* `SOURCE_FILE_NAME`
* `DQ_PASSED`
* `DQ_FAILURE_REASON`
* `DQ_WARNING`

Required nulls, cast failures, invalid values, and duplicate business keys are
quarantined. Any quarantine or table error fails the Silver notebook so the
Bronze snapshot remains available for diagnosis and replay.

## Bronze retention

Notebook 08 runs after successful end-to-end validation. It retains the
current run-date snapshot for replay and deletes older dated snapshots.
Per-file retention and deletion evidence is appended to
`audit.bronze_cleanup`.

## Gold contract

Dimensions are Company, GL Account, Cost Center, Profit Center, Division,
Vendor, Customer, Date, and Fiscal Period. Atomic facts are Finance
Transaction and Budget and Plan. Monthly views cover Finance Actual, Vendor
Spend, GL Balance, and Plan vs Actual.

Notebook 03 calculates an all-column SHA-256 change hash for each SCD2
dimension. Facts relate through version-level surrogate keys, preserving the
master-data context effective at posting time.

Validate ledger scope, currency and sign semantics, fiscal periods, reversals,
chart of accounts, controlling area, language, and business-key derivation
with SAP Finance owners before production use.
