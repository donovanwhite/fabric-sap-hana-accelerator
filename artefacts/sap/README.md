---
title: SAP Finance source contracts and incremental extraction
description: Read-only SAP HANA extraction, synthetic source compatibility and schema evidence
author: Data and AI Engineering
ms.date: 2026-11-20
ms.topic: tutorial
keywords:
  - Microsoft Fabric
  - SQL Database
  - SAP HANA
  - Data Factory
  - document discovery
estimated_reading_time: 20
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

## SAP ledger configuration requirements and caveats

`ACDOCA.RLDNR` identifies the accounting ledger. `0L` is SAP's standard
leading-ledger default: zero followed by L, not the letter O. We retain it as
the synthetic profile's default. It does not by itself identify an accounting
principle, a currency, or every ledger a customer needs for reporting.

### Ownership and required decisions

SAP Finance and the SAP functional configuration team determine the approved
accounting scope. Engineering translates that decision into extraction and
reporting controls; it must not infer the authoritative ledger from its code
alone or automatically include every ledger discovered at the source.

| Owner                    | Required decision or evidence                                         |
| ------------------------ | --------------------------------------------------------------------- |
| SAP Finance              | Approve accounting views, ledger scope, history and close policies.   |
| SAP functional team      | Confirm ledger types, company assignments, currencies and calendars.  |
| SAP source or Basis team | Verify installed fields, read permissions and HANA workload limits.   |
| Data engineering         | Enforce scope, initialize history and reconcile each approved ledger. |
| Reporting owners         | Select ledger defaults and preserve comparable monetary scope.        |

Record the approved source client, ledger codes and types, company-code
assignments, accounting principles, currency mappings, fiscal-year variants,
activation/history coverage and reconciliation frequency. Non-leading ledgers
need not be active for every company code. Use the installed SAP configuration
and Finance-approved reports as evidence, not the demonstration inventory.

The accelerator reads SAP through authorized SQL. It does not create ledgers,
change SAP configuration, or require an ABAP add-on.

### Current extraction scope

The ACDOCA contract in
[factory-copy-contract.json](./factory-copy-contract.json) declares logical
partitions on `GJAHR`, `RBUKRS` and `RLDNR`. Its supplied ledger list is
`["0L"]`. Ledger is also part of the complete ACDOCA business key, so rows
from different ledgers remain distinct in Silver and Gold.

> [!WARNING]
> The current runtime does not enforce one ledger allowlist across all
> extraction modes. `partitionValues.RLDNR` alone is not a global source
> filter. Resolve this scope-consistency gap before production onboarding
> or treating an additional ledger as a configuration-only change.

| Extraction path           | Current ACDOCA ledger behavior                                        |
| ------------------------- | --------------------------------------------------------------------- |
| Historical initialization | Uses `partitionValues.RLDNR`, currently `0L`.                         |
| Scheduled reconciliation  | Uses the same configured partition list.                              |
| On-demand partition read  | Uses the requested ledger; does not check membership in that list.    |
| Document incremental      | Reads all ledger lines for discovered documents unless filters apply. |
| Full snapshot             | Reads all ledger rows permitted by configured source filters.         |

The supplied contract has no `filters.RLDNR` predicate. Consequently,
incremental discovery can ingest another ledger's newly entered documents
without that ledger's history having been initialized or scheduled for
reconciliation. Engineering must make the approved ledger selection govern
both partition generation and query filtering, including on-demand requests,
rather than maintain conflicting independent lists.

BKPF headers are not ledger-specific. Keep the existing client, company,
fiscal-year and document-number join; do not invent a BKPF ledger join or
duplicate header extraction for each ACDOCA ledger.

### Adding or removing a standard ledger

After Finance approves a second standard ledger, its partition inventory can
be represented in ACDOCA's existing `partitionValues` as follows:

```json
{
  "GJAHR": ["2025", "2026"],
  "RBUKRS": ["1000", "1100", "1200", "1300"],
  "RLDNR": ["0L", "2L"]
}
```

`2L`, the years and the company codes above are examples, not customer
defaults. The planner creates combinations of the configured values; adding a
ledger therefore adds work for every configured company and year. Confirm
that these combinations represent the approved source scope. Logical
partitions do not imply physical HANA partitioning.

A standard additional ledger does not require its own notebook, but it does
require an approved historical load before ongoing discovery can represent
complete history. Changing partition values changes checkpoint compatibility.
With the current checkpoint design, the next `AUTO` operation can select
`INITIALIZE` and re-read existing configured partitions as well as the new
ledger. It is not a new-ledger-only backfill.

An on-demand `PARTITION_REFRESH` does not advance the document-entry
checkpoint and is not a substitute for initializing a changed scope. Complete
or recover any unfinished run with its pinned contract before changing
metadata. Plan the rebaseline and its source/compute cost, then use the
[existing deployment](../../deployment/README.md#required-deployment-permissions)
and [pipeline operations](../pipeline/README.md).

Removing a ledger from the inventory does not automatically purge its
previously loaded Silver or Gold records. Finance must approve the retention
and reporting treatment; do not interpret a configuration change as an
instruction to delete historical financial data.

### Extension ledgers

An extension ledger can hold adjustments while relying on an underlying
ledger, potentially through another extension ledger, for complete reporting.
A direct SQL read filtered only to the extension code does not automatically
reproduce that combined accounting view.

The current generic path preserves the extracted ledger rows; it does not
resolve extension-ledger chains or reconstruct underlying-plus-adjustment
reporting. SAP Finance must define that interpretation and the engineering
team must implement and reconcile an approved mapping before exposing an
extension ledger as a complete accounting view. Do not treat it as an
ordinary additional standard-ledger code.

### Reporting, currency and planning impacts

Preserve client, ledger and the currency corresponding to each monetary
measure. The same economic posting can appear in multiple standard ledgers;
summing them without an approved accounting interpretation can double-count
activity. Ledger selection is not currency conversion or consolidation.
See [SAP schema evidence](#sap-schema-evidence-and-release-assumptions) for
the exact amount/currency pairs.

Gold and the MLVs retain ledger identity. Monetary semantic measures reject
ambiguous client/ledger/currency contexts, so adding a ledger can require
report-filter changes before existing visuals return a total. Data Agent SQL
must filter to one approved view or return separately labelled groups.

The custom BUDGET source has a separate `financeContext.ledger`, currently
`0L`. Adding an actuals ledger does not copy or reassign budgets to it.
Finance must approve comparable actual/plan ledger and currency mappings.
The supplied FAGLFLEXP projection is synthetic and still requires the
release-specific planning mapping described below.

### Production onboarding checklist

1. Obtain Finance-approved client, ledger/type, company, history, currency
   and fiscal-calendar scope, including any underlying-ledger relationships.
2. Verify that every extraction mode enforces that scope. The current
   incremental and on-demand allowlist gaps must be resolved first.
3. Finish or recover existing runs, version the approved metadata and plan
   initialization/rebaseline without discarding recovery data.
4. In an authorized test workspace, reconcile complete keys, document
   coverage and exact currency amounts against approved SAP reports. Exercise
   new documents, historical adjustments, ledger isolation and replay.
5. Confirm report and Data Agent scope, explicit budget mappings and the
   accounting-close reconciliation schedule before production use.

SAP references:

* [Maintaining ledgers in Financial Accounting](https://learning.sap.com/courses/maintaining-financial-accounting-core-settings/maintaining-ledgers-in-financial-accounting)
* [Defining and using extension ledgers](https://learning.sap.com/courses/detailing-configuration-of-r2r-posting-processes/defining-and-using-extension-ledgers_c05ca87b-a24e-44d5-9450-a63549417534)

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
