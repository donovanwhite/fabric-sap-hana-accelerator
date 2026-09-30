---
title: SAP Finance SQL source simulator
description: Fabric SQL Database simulator and full-snapshot extraction runbook
author: Data and AI Engineering
ms.date: 2026-09-29
ms.topic: tutorial
keywords:
  - Microsoft Fabric
  - SQL Database
  - SAP HANA
  - Data Factory
  - full snapshot
estimated_reading_time: 12
---

## Purpose

`SAP_FIN_DB` simulates direct extraction from the underlying SAP HANA Finance
tables for a Fabric Data Factory Copy activity. Synthetic CSV snapshots seed
source-faithful physical tables. The active pipeline reads complete source
projections and does not depend on synthetic change tracking.

The intended flow is:

```text
Synthetic CSV snapshot
        ↓
SAP_FIN_DB
  SAPABAP1 standard tables
  SAPDEMO custom demo tables
        ↓
Factory full-snapshot Copy
        ↓
lh_brz_slv_finance/Files/bronze/sap_finance/<table>/<table>_yyyyMMdd.csv
```

## Database and schemas

| Object | Purpose |
|--------|---------|
| `SAP_FIN_DB` | Fabric SQL Database source-system simulator |
| `SAPABAP1` | Standard SAP Finance and master-data tables |
| `SAPDEMO` | Custom budget and reference contracts that are not standard SAP tables |
| `etl` | Demo loader batches and staging tables; legacy watermark objects are not used by the active pipeline |

`SAPABAP1` is a common ABAP application schema name. The real HANA schema is
environment-specific and must be parameterized. The templates in
[hana_projections.sql](./hana_projections.sql) read physical base tables
directly and use `<SAP_SCHEMA>`.

## Source fidelity

The standard source tables and columns use SAP names so Factory projections can
be reused against a real HANA source:

* `ACDOCA`
* `BKPF`
* `T001`
* `CSKS`
* `CEPC`
* `SKA1`
* `SKAT`
* `LFA1`
* `KNA1`
* `FAGLFLEXP`

SAP character and NUMC fields remain character types. The loader pads numeric
SAP account, line, month, and period values where the synthetic CSV omitted
leading zeros. SAP DATS values remain `YYYYMMDD` character values, matching
source extraction behavior.

`BUDGET`, `DIVISION_REFERENCE`, and `GL_ACCOUNT_REFERENCE` are custom demo
contracts under `SAPDEMO`. Map them to approved underlying Z tables, planning
tables, or reference tables in a real environment.

## Files

| File | Purpose |
|------|---------|
| [schema.sql](./schema.sql) | Idempotent SQL source schema and demo loader controls |
| [load_synthetic_data.py](./load_synthetic_data.py) | Initial seed and deterministic daily delta loader |
| [connection.example.json](./connection.example.json) | Template for optional manual loader endpoint metadata |
| [factory-copy-contract.json](./factory-copy-contract.json) | Table, business key, source-object, and Bronze folder mapping |
| [hana_projections.sql](./hana_projections.sql) | HANA-compatible business column projections |

## Prerequisites

* Azure CLI authenticated to the Fabric tenant
* ODBC Driver 18 for SQL Server
* `uv`, or Python 3.11 with `pyodbc`
* Access to `SAP_FIN_DB`
* Synthetic CSV folders under `sap_synthetic_data/sap`

No SQL password is stored. The loader acquires an Entra token from Azure CLI.
The automated deployment does not use this loader. For manual development,
copy `connection.example.json` to an ignored local file and pass it with
`--connection-config`.

## Deploy the schema

The automated installer creates the database schema and loads all synthetic
rows through its generated SQL project. [schema.sql](./schema.sql) remains the
authoritative schema source for that deployment.

## Load the initial snapshot

From this folder:

```powershell
uv run .\load_synthetic_data.py --mode initial `
  --connection-config .\connection.local.json
```

If `pyodbc` is already installed, run the script directly:

```powershell
python .\load_synthetic_data.py --mode initial `
  --connection-config .\connection.local.json
```

To load from another local or OneLake-synced Bronze root:

```powershell
uv run .\load_synthetic_data.py `
  --mode initial `
  --source-root "C:\path\to\bronze"
```

The root must contain one CSV under each lower-case table folder.

## Simulate a daily incremental batch

The following command deterministically touches about 5% of each table and
adjusts selected budget forecast values by 1%. Business values in balanced
ACDOCA documents are not changed.

```powershell
uv run .\load_synthetic_data.py `
  --mode delta `
  --business-date 2026-09-30 `
  --touch-percent 5 `
  --forecast-adjustment-percent 1
```

Run another date to simulate the next daily extract:

```powershell
uv run .\load_synthetic_data.py `
  --mode delta `
  --business-date 2026-10-01 `
  --touch-percent 5 `
  --forecast-adjustment-percent 1
```

## Factory pipeline pattern

> [!IMPORTANT]
> The selected SAP HANA tables have no common, trustworthy watermark column.
> Posting dates, document dates, fiscal periods, and validity dates cannot
> reliably identify late postings, subsequent amendments, reversals, or
> deletes. The active pipeline therefore reads complete projections and
> delegates current-state reconciliation to Silver business-key MERGE.

For each table in
[factory-copy-contract.json](./factory-copy-contract.json):

1. Copy the complete approved projection:

   ```sql
   SELECT
       RCLNT,RLDNR,RBUKRS,GJAHR,POPER,BELNR,DOCLN,RACCT,RCNTR,PRCTR,
       KOKRS,LIFNR,KUNNR,BUDAT,BLDAT,DRCRK,BLART,AWTYP,SGTXT,RHCUR,
       RKCUR,RWCUR,HSL,KSL,TSL,WSL,MSL
   FROM SAPABAP1.ACDOCA;
   ```

2. Write a dated CSV to:

   ```text
   Files/bronze/sap_finance/acdoca/acdoca_yyyyMMdd.csv
   ```

3. Run the Bronze-to-Silver notebook after all 13 Copy operations succeed.
4. Upsert valid rows into Silver by the configured SAP business key.
5. After complete validation, retain the current Bronze snapshot and delete
   older snapshots with per-file audit evidence.

## Production SAP HANA transition

Keep the selected table and business column names. Replace:

* `SAP_FIN_DB` connection with the managed SAP HANA connection
* `SAPABAP1` with the environment's ABAP schema
* `SAPDEMO` tables with approved underlying Z or planning tables

The business rows must come directly from approved HANA tables or views. Full
snapshots are the correctness baseline where no reliable change or deletion
signal exists. Reassess source-specific SAP extraction services separately
before introducing any incremental strategy.

## Safety and replay

* Dated Bronze snapshots are deterministic by run date.
* Silver MERGE is idempotent by the configured business key.
* Reusing the same synthetic delta date updates the same source keys.
* Each load is recorded in `etl.load_batch`.
* The loader creates reusable empty `etl.stage_<TABLE>` tables for set-based
  JSON ingestion. They are truncated before and after each table load.
