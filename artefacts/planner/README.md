---
title: SAP Finance Planning
description: Fabric Planning design over the SAP Finance Gold semantic model
author: Data and AI Engineering
ms.date: 2026-10-05
ms.topic: reference
keywords:
  - Microsoft Fabric
  - Planning
  - SAP Finance
  - forecast
  - budget
  - writeback
estimated_reading_time: 6
---

## Architecture

`SAP Finance Planning Enterprise` reads governed actual, budget, forecast, and plan
measures from the `SAP Finance` Direct Lake semantic model. Planning inputs
remain separate from actuals.

Fabric creates an internal application database when the Plan item is created.
That database is reserved for Plan metadata and collaboration. Do not query or
write to it directly.

`SAP_FIN_PLAN_DB` is the governed destination for business planning data:

* `plan.scenario` owns scenario and version lifecycle.
* `plan.assumption` stores controlled planning drivers.
* `audit.planning_release` records promoted writeback batches.
* Fabric Planning creates and manages `plan.finance_budget_writeback`,
  `plan.finance_forecast_writeback`, and
  `plan.finance_assumption_writeback`.

## Finance planning workspace

The Plan contains five purpose-specific sheets:

| Sheet | Audience | Purpose | Writeback |
| --- | --- | --- | --- |
| Executive Plan Summary | CFO and finance leadership | Consolidated actual, budget, forecast, and variance review | Read-only |
| Budget Input | Finance business partners and cost owners | Monthly budget capture at organizational and GL grain | `plan.finance_budget_writeback` |
| Rolling Forecast | FP&A and business finance | Reforecast remaining periods against actuals | `plan.finance_forecast_writeback` |
| Scenario Assumptions | FP&A and scenario owners | Inflation, FX, headcount growth, and rationale inputs | `plan.finance_assumption_writeback` |
| Actual vs Plan Review | Finance controllers | Detailed actual, plan, forecast, and variance investigation | Read-only |

All editable sheets use **Long with Changes** so revised values retain an
auditable history rather than replacing the only stored value.

## Planning grain

The intended writeback grain is:

```text
Company
+ Division
+ Profit Center
+ Cost Center
+ GL Account
+ Fiscal Year
+ Fiscal Period
+ Scenario
+ Plan Version
```

The business key is the complete grain. SAP fiscal periods range from 1
through 16. Monetary values use local currency units with two decimal places.
Timestamps are UTC.

The detailed contract is in
[`writeback-contract.json`](./writeback-contract.json).

## Deployment

The installer creates:

1. `SAP_FIN_PLAN_DB`
2. `SAP Finance Planning Enterprise`
3. The Plan item internal application database, created automatically by
   Fabric

The Plan definition is connected to the Gold-backed `SAP Finance` semantic
model. A Power BI semantic model OAuth2 connection is required during
deployment:

```powershell
.\deployment\deploy.ps1 `
  -Workspace "<workspace-id>" `
  -SqlConnection "<fabric-sql-source-connection>" `
  -PlanningSqlConnection "<planning-sql-connection>" `
  -SemanticModelConnection "<semantic-model-connection>" `
  -DryRun
```

> [!IMPORTANT]
> A generic `PowerBIDatasets` connection that passes `testConnection` is not
> sufficient unless Fabric Planning created or reauthorized it through
> **Manage Semantic Model Connection**. Planning requires a delegated
> Organizational Account refresh token and Microsoft doesn't allow that token
> to be supplied through the public Connections API.
>
> Name the delegated semantic-model connection
> `conn_sap_finance_planning_dataset`. All solution-owned connection names are
> lowercase and start with `conn_`.

## Connection binding

The installer binds `conn_sap_finance_planning_sql` to the Plan application
database for viewer collaboration and to all three governed writeback
destinations. `conn_sap_finance_sql` remains dedicated to `SAP_FIN_DB` source
ingestion. The planning connection must already exist and have valid OAuth2
credentials because Fabric does not expose interactive user consent through
the deployment API.

After deployment:

1. Open `SAP Finance Planning Enterprise`.
2. Verify that the viewer-collaboration connection is
   `conn_sap_finance_planning_sql`.
3. Verify that all five generated Planning visuals can query the semantic
   model.
4. Confirm finance users have Read and Build access to the semantic model and
   connection access for their assigned workflow.
5. Test one controlled writeback in each editable sheet and inspect the
   generated destination table.

The generated Plan definition includes all visual and writeback metadata.
Do not commit OAuth credentials or tenant connection IDs.

## Lifecycle and ownership

Gold Lakehouse and the semantic model own actuals and curated historical
facts. `SAP_FIN_PLAN_DB` owns forward-looking inputs. Approved plans should be
promoted into governed downstream reporting through an explicit release
process rather than updating Gold actual tables in place.
