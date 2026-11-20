---
title: SAP Finance source contracts and incremental extraction
description: Read-only SAP HANA extraction, synthetic source compatibility and schema evidence
author: Data and AI Engineering
ms.date: 2026-11-19
ms.topic: tutorial
keywords:
  - Microsoft Fabric
  - SQL Database
  - SAP HANA
  - Data Factory
  - document discovery
estimated_reading_time: 15
---

## Purpose

`SAP_FIN_DB` simulates direct extraction from the underlying SAP HANA Finance
tables for a Fabric Data Factory Copy activity. Synthetic CSV snapshots seed
physical tables. The active pipeline supports BKPF-driven document discovery,
logical partition refresh, complete snapshots and master snapshot comparison.
It does not depend on synthetic modification timestamps, CDC or ABAP add-ons.

The intended flow is:

```text
Synthetic CSV snapshot
        ↓
SAP_FIN_DB
  SAPABAP1 standard tables
  SAPDEMO custom demo tables
        ↓
Factory read-only SQL Copy
        ↓
lh_brz_slv_finance/Files/bronze/sap_finance/<table>/<scope>/run_id=<run>/...
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

The simulator uses representative SAP names for:

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

These are synthetic projections, not certification of every field against an
installed SAP release. In particular, CEPC company assignment and the simplified
FAGLFLEXP plan projection require explicit production mapping.

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
| [factory-copy-contract.json](./factory-copy-contract.json) | Shared projections, complete keys, modes, types, partitions, overlap and reconciliation policies |
| [hana_projections.sql](./hana_projections.sql) | HANA-compatible business column projections |
| [../medallion/sap_extraction.py](../medallion/sap_extraction.py) | Validated SELECT generation, entry boundaries, typed hashing and reconciliation contracts |
| [../medallion/sap_runtime.py](../medallion/sap_runtime.py) | Existing pipeline's durable Fabric/Delta adapter |

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

The legacy delta mode remains available for compatibility. To exercise real
new-document and historical-change scenarios, use the pure
`synthetic_scenarios.py` helpers or guarded scenario mode in the same loader:

```powershell
uv run .\artefacts\sap\load_synthetic_data.py --mode scenario `
  --scenario reversal --entered-at 2026-09-30T12:34:56Z `
  --connection-config .\artefacts\sap\connection.local.json `
  --synthetic-target "<exact-simulator-database-name>"
```

Run that example from the repository root. Scenario mode reads the current
SQL simulator, requires an exact `DB_NAME()` match and the
`SAP_FINANCE_SYNTHETIC_SIMULATOR=1` database marker, and applies scoped changes
transactionally. It must never target customer SAP. Apply `schema.sql` first
when upgrading a simulator; the loader also upgrades legacy staging columns
before inserting the new entry-time/currency fields.

Canonical scenarios are `new_documents`, `late_current_month`,
`prior_fiscal_year`, `reversal`, `multiple_ledgers`, `cross_company`,
`master_changes`, `validity_slices`, `reopened_historical_period`,
`source_row_removal`, `source_document_removal`, `headerless_adjustment`, and
`checkpoint_same_second`. Extraction interruption, duplicate replay and
recovery are tested by the processing adapter, not fabricated as source events.

For offline tests, `load_seed_rows(<repo>/artefacts/sap_synthetic_data/sap)`
and `apply_scenario(source, name, entered_at)` return independent, deterministic
snapshots using exact decimals. Original seed business fields are preserved.
Legacy seed reversal-reference anomalies remain observable; new reversal
scenarios are reciprocal and retain both the original and reversal documents.

## Factory pipeline pattern

> [!IMPORTANT]
> The selected SAP HANA tables have no common, trustworthy watermark column.
> `GJAHR` identifies fiscal year, `BUDAT` posting date, and BKPF
> `CPUDT/CPUTM` document entry. None is a complete modification watermark.
> Document discovery must be supplemented by complete historical reconciliation.

For each table in
[factory-copy-contract.json](./factory-copy-contract.json):

1. Select the configured extraction mode and approved projection. ACDOCA
   document discovery uses:

   ```sql
   SELECT A."RCLNT", A."RLDNR", A."RBUKRS", A."GJAHR",
          A."BELNR", A."DOCLN", A."RHCUR", A."HSL"
   FROM "<SAP_SCHEMA>"."ACDOCA" A
   INNER JOIN "<SAP_SCHEMA>"."BKPF" B
     ON A."RCLNT" = B."MANDT" AND A."RBUKRS" = B."BUKRS"
    AND A."GJAHR" = B."GJAHR" AND A."BELNR" = B."BELNR"
   WHERE A."RCLNT" = '<APPROVED_CLIENT>'
     AND (B."CPUDT" > '<START_DATE>' OR
          (B."CPUDT" = '<START_DATE>' AND B."CPUTM" >= '<START_TIME>'))
     AND (B."CPUDT" < '<END_DATE>' OR
          (B."CPUDT" = '<END_DATE>' AND B."CPUTM" <= '<END_TIME>'));
   ```

2. Write an immutable run/work/attempt CSV below:

   ```text
   Files/bronze/sap_finance/acdoca/
   ```

3. Validate source-generated controls and Copy counts before accepting a receipt.
4. Publish validated Silver scopes with mode-specific deletion semantics.
5. Commit checkpoints only after downstream validation; retain Bronze for replay.

The generated SQL adds same-statement source count, key and currency/amount
controls. Templates above show the business selection only. Fabric's documented
HANA Copy interface accepts query text, not a documented bind-value array.
The generator therefore validates identifiers and strictly restricts predicate
tokens before rendering quoted literals; it does not concatenate arbitrary SQL
from pipeline parameters.

`amountColumns` supplies additional scalar financial controls for planning
values. They compare one complete business key at a time, preserve null
forecasts, and never aggregate unlike currencies. This verifies amount
transport; production planning currency interpretation still requires the
approved mapping. `fiscalYearRange` retains the demonstration's original
1900-2100 technical rule without hardcoding that range in the engine.

## Production SAP HANA transition

Keep the selected table and business column names. Replace:

* `SAP_FIN_DB` connection with the managed SAP HANA connection
* `SAPABAP1` with the environment's ABAP schema
* `SAPDEMO` tables with approved underlying Z or planning tables

Use `--hana-connection` and a separate `--extraction-contract` with the existing
deployer. The connection must already be managed in Fabric, backed by an
on-premises gateway with the HANA ODBC driver. Credentials remain Fabric-managed.
The contract must declare `validatedRelease`, set `schemaVerified=true` for
each approved projection and specify explicit client filters.

Do not mark the supplied synthetic projections production-verified without
checking the installed DDIC/catalog. No triggers, procedures, source tables,
custom extractors, ABAP installation or SAP configuration changes are required.
`SAPABAP1` is a simulator default, not a required customer schema.

Use an isolated, approved workspace for transition to a different physical
source. Do not mix synthetic and customer history through an uncontrolled
in-place connection switch. Source identity changes cannot reuse simulator
checkpoints; rebaseline scope and dimension history under Finance ownership.

The native CEPC projection does not assume `BUKRS` exists. Use the actual
controlling-area/validity contract and approved company assignment. The supplied
FAGLFLEXP model uses synthetic `RACCT/PRCTR/VERSN` planning grain; it is not the
native FAGLFLEXP DDIC key. Preserve the existing synthetic planning demonstration,
but supply a release-specific native mapping before enabling it against HANA.

## Safety and replay

* Successful Bronze attempts are immutable and never overwritten by another run.
* Silver MERGE is idempotent by the configured business key.
* Reusing the same synthetic delta date updates the same source keys.
* Each load is recorded in `etl.load_batch`.
* The loader creates reusable empty `etl.stage_<TABLE>` tables for set-based
  JSON ingestion. They are truncated before and after each table load.

## Add another source table

Extend the same shared contract with an approved `sourceObject`, ordered
`columns`, complete `businessKey`, mode and explicit non-string `types`.
Strings remain strings by default. Supply partition columns/values for
partitioned initialization, verified amount/currency pairs or scalar amount
controls, and a managed connection through the existing deployment path.
Update synthetic schema/fixtures/loader only when the simulator must include
the new object. Add source-query, completeness, precision and replay tests.

The generic Bronze/Silver adapter can process additional metadata contracts;
adding a table does not invent a Gold business transformation. Define its
approved dimensional/fact mapping separately. BSEG was researched but is not
silently added to the current synthetic profile or equated with ACDOCA.

## SAP schema evidence and release assumptions

The keys below were corroborated with published DDIC snapshots. They are not
a substitute for source-owner verification of the customer's installed release.
No live customer SAP system was available for that verification.

| Table | Complete logical key | Partition or validity semantics |
|-------|----------------------|---------------------------------|
| ACDOCA | RCLNT, RLDNR, RBUKRS, GJAHR, BELNR, DOCLN | Logical GJAHR/RBUKRS/RLDNR extraction partitions |
| BKPF | MANDT, BUKRS, BELNR, GJAHR | CPUDT/CPUTM entry discovery; GJAHR/BUKRS partition refresh |
| BSEG | MANDT, BUKRS, BELNR, GJAHR, BUZEI | Entry-view lines; researched, not an enabled synthetic source |
| T001 | MANDT, BUKRS | Company master snapshot |
| CSKS | MANDT, KOKRS, KOSTL, DATBI | Preserve inclusive DATAB/DATBI validity slices |
| CEPC | MANDT, PRCTR, DATBI, KOKRS | Preserve controlling area and validity slices |
| SKA1 | MANDT, KTOPL, SAKNR | Chart-level account master |
| SKAT | MANDT, SPRAS, KTOPL, SAKNR | Language is part of the text key |
| LFA1 | MANDT, LIFNR | General supplier, not company-code master |
| KNA1 | MANDT, KUNNR | General customer master |
| FAGLFLEXP | RCLNT, RYEAR, DOCNR, RLDNR, RBUKRS, DOCLN, DOCCT, RPMAX | Native legacy planning identity differs from this simulator |

ACDOCA document lines can have zero or one matching BKPF header; a header
serves multiple lines and ledgers. Ledger is not a BKPF join key. Do not equate
six-character ACDOCA `DOCLN` with three-digit BSEG `BUZEI`.
Carry-forward, migration, archiving and special-ledger behavior require local
verification. Initialization and partition refresh read ACDOCA directly and
therefore retain headerless records that document discovery cannot see.

Reconcile `HSL/RHCUR`, `KSL/RKCUR`, `TSL/RTCUR` and `WSL/RWCUR` independently,
with client/company/year/ledger and complete keys. `TSL` is balance transaction
currency; `WSL` is original transaction currency. `KSL` is global currency,
not universally a particular group currency. Preserve physical decimal scale
and validate SAP currency-decimal presentation settings before interpreting
native amounts. Never total unlike currencies.

BKPF `STBLG/STJAH` link reversals; `BSTAT` retains document status. A newly
entered reversal is discoverable, but later changes to the original header,
clearing/reset, historical adjustments, source removal and carry-forward
updates can leave the original entry boundary unchanged. Reconcile those
periods; never turn a reversal into a physical-delete instruction.

BSID/BSAD/BSIK/BSAK are compatibility views in the researched S/4HANA
simplification list. Clearing can move existing items between open/cleared
sets without a new original-document entry timestamp. CDHDR/CDPOS record
application-defined change-document coverage, not database CDC; their
availability does not establish complete capture.

Authoritative references and corroborating dictionaries:

* [SAP Universal Journal FAQ](https://help.sap.com/docs/SAP_S4HANA_ON-PREMISE_UPA/e55549ee96814207af9232a7688dd64a/8b8e5695c4dc4749a706f9fa2f6bda92.html)
* [SAP balance carry-forward behavior](https://help.sap.com/doc/8353d7531a4d424de10000000a174cb4/700_SFIN3E%20006/en-US/1a065652ebc3735fe10000000a441470.html)
* [SAP DDIC date/time semantics](https://help.sap.com/doc/abapdocu_816_index_htm/8.16/en-US/ABENDDIC_DATE_TIME_TYPES.html)
* [SAP native SQL client handling](https://help.sap.com/doc/abapdocu_816_index_htm/8.16/en-US/ABENCLIENT_HANDLING_GUIDL.html)
* [SAP 2025 simplification list, pages 163-164](https://help.sap.com/doc/0df2ffddebab40cf9338488b2f18dc41/2025.latest/en-US/SIMPL_OP2025.pdf)
* [SAP change-document coverage requirements](https://help.sap.com/doc/saphelp_em900/9.0/en-US/48/dfd498ab14280de10000000a42189c/content.htm)
* [ACDOCA DDIC snapshot](https://docsfortec.com/sap/S4/tables/ACDOCA), [BKPF](https://docsfortec.com/sap/S4/tables/BKPF), [CSKS](https://docsfortec.com/sap/S4/tables/CSKS), [CEPC](https://docsfortec.com/sap/S4/tables/CEPC), [FAGLFLEXP](https://docsfortec.com/sap/S4/tables/FAGLFLEXP)
* [Fabric SAP HANA Copy configuration](https://learn.microsoft.com/fabric/data-factory/connector-sap-hana-copy-activity)
