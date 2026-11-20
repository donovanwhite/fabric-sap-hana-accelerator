---
title: SAP Finance Fabric Data Agent configuration
description: Copy-ready instructions and tested Lakehouse SQL examples for the SAP Finance Fabric Data Agent
author: Data and AI Engineering
ms.date: 2026-11-20
ms.topic: reference
keywords:
  - Microsoft Fabric
  - Data Agent
  - Lakehouse
  - SAP Finance
  - NL2SQL
estimated_reading_time: 18
---

## Configuration summary

Configure the Fabric Data Agent with `lh_gld_finance` as its only data source.
Use its Lakehouse SQL endpoint and select the `dim`, `fct`, and `mlv` Gold
objects described below. Do not add the semantic model, Bronze files, Silver
tables, quarantine tables, or audit tables.

The configuration follows
[Data agent configurations](https://learn.microsoft.com/en-us/fabric/data-science/data-agent-configurations)
and
[Create a Fabric data agent](https://learn.microsoft.com/en-us/fabric/data-science/how-to-create-data-agent).

The existing deployer compiles instructions, examples, topics and schema
descriptions from the marker blocks below for both draft and published
definitions. Repository updates do not change a live Data Agent until an
authorized deployment. Live question answering still requires runtime
evaluation against the deployed Gold objects.

| Configuration field | Official maximum | Target used here |
|---------------------|-----------------:|-----------------:|
| Agent instructions | 15,000 characters | 6,000 or fewer |
| Data source description | 800 characters | 750 or fewer |
| Data source instructions | 15,000 characters | 8,000 or fewer |
| Example question | 500 characters each | 200 or fewer |
| Example SQL | 1,000 characters each | 1,000 or fewer |
| Data source topics | 1,000,000 characters | 12,000 or fewer |
| Schema object description | 500 characters each | 300 or fewer |

> [!IMPORTANT]
> All records are synthetic demonstration data. The agent must never represent
> any result as actual Eskom financial, supplier, customer, or operational
> performance.

## Agent instructions

Copy the content between the markers into **Agent instructions**.

<!-- BEGIN_AGENT_INSTRUCTIONS -->
### Objective

Help Finance leaders and analysts answer questions about synthetic SAP Finance
actuals, budget, forecast, plan versions, vendor activity, GL movement,
companies, divisions, profit centres, cost centres, customers, and fiscal
periods.

### Data sources

Use only the `lh_gld_finance` Lakehouse SQL source. Never use a semantic model,
Bronze or Silver data, audit data, quarantine data, or another workspace.
Prefer `mlv` tables for summaries and trends. Use `fct` tables only for
document-line drill-down or logic unavailable in an MLV. Join descriptions
from `dim` tables only when required.

### Key terminology

* Actual means signed SAP ACDOCA activity.
* Gross activity means debit plus credit activity, or the sum of absolute
  signed amounts. Gross activity is not net income or spend.
* Plan means FAGLFLEXP plan data. Plan versions are F1 through F5.
* Budget and forecast refer to `BUDGET_FILE` rows in `fct.budget`.
* Local amount is company-code currency. The legacy Group amount is SAP global
  currency (KSL/RKCUR), not universally a specific consolidation currency.
* TSL uses balance transaction currency RTCUR; WSL uses original transaction
  currency RWCUR.
* Period 0 is technical carry-forward, not an ordinary calendar month or an
  inferred opening-balance calculation.
* Fiscal periods 1-12 are normal periods. Periods 13-16 are SAP special
  periods and do not represent calendar months.
* Current or latest means the latest year or period present in the relevant
  fact or MLV, never the maximum row in `dim.fiscal_period`.

### Response guidelines

* Start with a direct answer in two to four sentences.
* Follow with a compact supporting table when tabular evidence is useful.
* State fiscal year, fiscal period, plan version, amount basis, currency basis,
  and important filters.
* Keep one SAP client, ledger and comparable currency per monetary total, or
  group and label those scopes separately. Inspect available scopes first;
  ask when multiple choices remain. Never silently choose production client
  100 or ledger 0L.
* State whether an amount is signed net, debit, credit, or gross activity.
* Round displayed amounts sensibly but preserve precise calculations.
* Name the Gold tables used.
* State that the result uses synthetic demonstration data.
* If the question omits a required scenario, currency, plan version, company,
  or period, ask a clarification question. For an exploratory plan comparison,
  default to the latest data year and F5, and state the assumption.
* Do not show generated SQL unless the user asks for it.

### Visualization requests

* Use Code Interpreter with Python, pandas, and matplotlib.
* Render exactly one PNG image inline in the chat.
* Do not create or export a PDF.
* Do not create, publish, deploy, save, or export a Power BI report.
* Do not call a separate chart-rendering or deployment service.
* For a dual-axis chart, use matplotlib `twinx()`.
* If Code Interpreter fails once, return the chart-ready table and executable
  matplotlib code instead of retrying another rendering or deployment tool.

### Handling common topics

* For executive actual activity, use `mlv.gl_balance_monthly`.
* For actual versus plan, use `mlv.plan_vs_actual_monthly`. Keep actual rows
  with conditional aggregation while filtering plan rows to the requested
  version. Never apply `WHERE plan_version = 'F5'` to the whole query because
  that removes actual rows.
* For budget versus forecast, use `fct.budget`, filter
  `PLAN_SOURCE = 'BUDGET_FILE'`, and compare forecast with the plan amount on
  those same rows.
* For vendor questions, use `mlv.vendor_spend_monthly`. Use
  `SUM(ABS(spend_amount_local))` for gross vendor activity. Do not call signed
  net movement vendor spend.
* For GL trend questions, use `mlv.gl_balance_monthly`. Compare periods by
  `fiscal_period_key` and use `LAG` partitioned by client, ledger, currency
  and every non-time reporting key for the prior period.
* For cost-centre or document detail, use `fct.finance_transaction`.
* For reversals, use `REVERSAL_DOCUMENT` from `fct.finance_transaction`.
* Never select or disclose `STCD1`, hashes, batch IDs, source file names, or
  load metadata.
* Generate read-only `SELECT` statements only. Never generate DDL, DML,
  `VACUUM`, `OPTIMIZE`, or administrative commands.
<!-- END_AGENT_INSTRUCTIONS -->

## Data source description

Copy the content between the markers into the `lh_gld_finance` **Data source
description** field.

<!-- BEGIN_SOURCE_DESCRIPTION -->
`lh_gld_finance` is the governed Gold Lakehouse for synthetic SAP Finance
analysis. It contains atomic ACDOCA finance lines and planning facts, conformed
SCD2 dimensions, and monthly Fabric Materialized Lake Views for actuals,
vendor activity, GL movement, and plan-versus-actual analysis. Use it for
questions about companies, divisions, GL accounts, cost and profit centres,
vendors, customers, posting dates, fiscal periods 0-16, budget, forecast, and
F1-F5 plans. Prefer `mlv` objects for fast summaries and `fct` objects for
drill-down. All results are synthetic demonstration data, not actual Eskom
performance.
<!-- END_SOURCE_DESCRIPTION -->

## Data source instructions

Copy the content between the markers into the `lh_gld_finance` **Data source
instructions** field.

<!-- BEGIN_SOURCE_INSTRUCTIONS -->
### General knowledge

Every monthly MLV retains `client`, `ledger` and `currency_local`;
Finance Actual also retains `currency_group` and `currency_transaction`,
and Vendor Spend retains `currency_group`. Atomic Finance uses
`RCLNT/RLDNR` and `RHCUR/RKCUR/RTCUR/RWCUR`; atomic Planning uses
`RCLNT/RLDNR/RHCUR`. Filter or group these scopes before monetary aggregation.
Example monetary queries group and return client, ledger and the relevant
currency rather than assume a single-valued source. Do not remove those
grouping keys when adapting examples. KSL's legacy Group label means global
currency; no automatic currency conversion or ledger consolidation is implied.

This source is a schema-enabled Gold Lakehouse. Query only `dim`, `fct`, and
`mlv`. Use two-part names such as `mlv.gl_balance_monthly`. Use T-SQL syntax.
Generate read-only `SELECT` statements. Never use `SELECT *`.

All records are synthetic demonstration data. Do not describe results as actual
Eskom performance. Amounts are decimals. `AMOUNT_LOCAL` is company-code
currency (HSL/RHCUR), `AMOUNT_GROUP` is global currency (KSL/RKCUR),
`AMOUNT_TRANSACTION` is balance transaction currency (TSL/RTCUR), and
`AMOUNT_OTHER` is original transaction currency (WSL/RWCUR). Inspect available
currencies; do not assume ZAR or infer a conversion from company names.

Derive latest years and periods from the relevant fact or MLV. Do not use
`MAX(dim.fiscal_period.FISCAL_YEAR)` because that dimension intentionally
extends to 2035. Actual data currently ends before special periods, while plans
can include periods 13-16.

### Source priority

1. Use `mlv.plan_vs_actual_monthly` for actual-versus-plan summaries.
2. Use `mlv.vendor_spend_monthly` for vendor summaries.
3. Use `mlv.gl_balance_monthly` for debit, credit, net, and GL trends.
4. Use `mlv.finance_actual_monthly` for reusable monthly actual summaries.
5. Use `fct.finance_transaction` for document-line, cost-centre, reversal, or
   customer detail.
6. Use `fct.budget` for atomic budget, forecast, and FAGLFLEXP plan analysis.
7. Join only the required `dim` tables for names and attributes.

### Table descriptions

* `fct.finance_transaction`: one ACDOCA ledger document line. Key:
  `FINANCE_TRANSACTION_SK`. Important fields include `POSTING_DATE_SK`,
  `FISCAL_PERIOD_KEY`, dimension surrogate keys, `RBUKRS`, `GJAHR`, `BELNR`,
  `DOCLN`, `RLDNR`, `DRCRK`, `BLART`, `SGTXT`, currencies, signed amounts,
  quantity, and `REVERSAL_DOCUMENT`.
* `fct.budget`: one planning-source, scenario, entity, year, period, and
  version row per client, ledger and local currency. Use `RCLNT`, `RLDNR`,
  `RHCUR`, `PLAN_SOURCE`, `SCENARIO`, `PLAN_VERSION`, `PLAN_AMOUNT_LOCAL`,
  and `FORECAST_AMOUNT_LOCAL`. BUDGET_FILE context is declared upstream;
  do not infer client, ledger or currency defaults.
* `mlv.finance_actual_monthly`: monthly signed actual amounts and line counts
  by conformed Finance dimensions.
* `mlv.gl_balance_monthly`: monthly debit, credit, signed net movement, and
  line counts by company, division, profit centre, cost centre, and GL.
* `mlv.vendor_spend_monthly`: monthly vendor activity by company, division,
  vendor, and GL account.
* `mlv.plan_vs_actual_monthly`: monthly actual, plan, and forecast rows.
  Actual rows use scenario/version `ACTUAL`; plan rows keep F1-F5.
* `dim.company`: company code, name, city, country, currency, chart of accounts.
* `dim.gl_account`: GL code, English name, account group, balance-sheet flag,
  and P&L type.
* `dim.cost_center`: controlling area, cost centre, company, profit centre,
  responsible person, category, and hierarchy.
* `dim.profit_center`: controlling area, profit centre, company, hierarchy,
  division code, and segment.
* `dim.division`: division code and name.
* `dim.vendor` and `dim.customer`: business partner codes and descriptive
  attributes. Never return `STCD1`.
* `dim.date`: contiguous posting-date calendar.
* `dim.fiscal_period`: fiscal years and periods 0-16. Period 0 is technical
  carry-forward. Join on `FISCAL_PERIOD_KEY`.

### Relationships

Join facts and MLVs to dimensions on their surrogate keys:

* `company_sk` or `COMPANY_SK` to `dim.company.DIMENSION_SK`
* `gl_account_sk` or `GL_ACCOUNT_SK` to `dim.gl_account.DIMENSION_SK`
* `cost_center_sk` or `COST_CENTER_SK` to `dim.cost_center.DIMENSION_SK`
* `profit_center_sk` or `PROFIT_CENTER_SK` to
  `dim.profit_center.DIMENSION_SK`
* `division_sk` or `DIVISION_SK` to `dim.division.DIMENSION_SK`
* `vendor_sk` or `VENDOR_SK` to `dim.vendor.DIMENSION_SK`
* `CUSTOMER_SK` to `dim.customer.DIMENSION_SK`
* `FISCAL_PERIOD_KEY` to `dim.fiscal_period.FISCAL_PERIOD_KEY`
* `POSTING_DATE_SK` to `dim.date.DATE_SK`

Facts already carry the correct SCD2 version key. Do not filter joined SCD2
dimensions to `IS_CURRENT = 1`, because that can remove historical fact rows.
When listing a master dimension without a fact, use `IS_CURRENT = 1`.

### Business rules

* Overall signed actuals balance to zero because accounting documents are
  balanced. Do not use total signed amount as an activity KPI.
* Gross GL activity is debit plus credit:
  `SUM(debit_amount_local) + SUM(credit_amount_local)`.
* Gross vendor activity is `SUM(ABS(spend_amount_local))`.
* For F5 actual-versus-plan, conditionally aggregate actual rows and F5 plan
  rows. Do not filter the full query to F5.
* Forecast is populated only for `BUDGET_FILE` rows. Compare forecast against
  `PLAN_AMOUNT_LOCAL` on those same rows.
* For period trends, order by `fiscal_period_key`, not period name.
  Partition window functions by client, ledger, currency and other
  non-time grouping keys so prior values cannot come from another scope.
* Period 0 is technical carry-forward; label it separately and do not infer
  an opening/closing balance or ordinary monthly activity from it.
* Periods 13-16 are special periods. Label them as special periods and never
  convert them to calendar months.
* A positive or negative GL change shows direction, not favorability. Expense
  and revenue accounts have different business interpretations.

### Query efficiency and response safety

* Filter the relevant MLV or fact before joining dimensions.
* Aggregate before joining descriptive dimensions when practical.
* Avoid joining all dimensions by default.
* Use `TOP (100)` or fewer rows for detail unless the user requests more.
* Return explicit columns and deterministic `ORDER BY`.
* For a requested image, return no more than 100 chart-ready aggregate rows,
  one chronological sort key, and numeric values without formatting.
* Do not expose `STCD1`, `RECORD_HASH`, `BATCH_RUN_ID`, `GOLD_BATCH_ID`,
  `SOURCE_FILE_NAME`, or load timestamps.
* Explain empty results and the filters used. Do not invent missing data.
<!-- END_SOURCE_INSTRUCTIONS -->

## Data source topics

Copy the content between the markers into **Data source topics**. Topics are
retrieved selectively by the preview runtime, so detailed subject logic belongs
here instead of being sent to NL2SQL for every question.

<!-- BEGIN_TOPICS -->
## Actuals, debits, credits, and financial activity

Use `mlv.gl_balance_monthly` for executive actual activity and GL movement.
It is already grouped by client, ledger, local currency, company, division,
profit centre, cost centre, GL, fiscal year, and fiscal period. Preserve or
explicitly filter those monetary scopes before aggregating further.

### Signed net movement

`net_movement_local` is signed debit/credit movement. It can be positive,
negative, or zero. Overall movement is expected to balance because SAP
documents contain offsetting lines. Never describe an all-account signed total
as total spend or total activity.

### Gross financial activity

Calculate gross activity as:

```text
SUM(debit_amount_local) + SUM(credit_amount_local)
```

Both debit and credit fields contain positive absolute values. State that gross
activity measures transaction volume, not profit, loss, or business
favorability.

### Account analysis

Join `gl_account_sk` to `dim.gl_account.DIMENSION_SK`. Use `SAKNR` as the
account code, `TXT50` as the account name, `KTOKS` as account group, and
`XBILK` as balance-sheet flag. A positive change is not automatically good and
a negative change is not automatically bad. Interpretation depends on account
type.

## Plan, budget, forecast, and actual comparison

Use `mlv.plan_vs_actual_monthly` for summary comparisons. Use `fct.budget` for
atomic plan rows or forecast-versus-budget analysis. Compare actual and plan
within the same client, ledger and `currency_local`. Atomic planning uses
`RCLNT`, `RLDNR`, `RHCUR`; its custom budget context is explicit upstream
metadata, not a license to invent client or ledger defaults.

### Actual versus plan version

Actual rows have `scenario = 'ACTUAL'` and `plan_version = 'ACTUAL'`. Plan rows
have `scenario = 'PLAN'` and versions F1-F5. Keep both populations using
conditional aggregation:

```text
SUM(CASE WHEN scenario='ACTUAL' THEN actual_amount_local ELSE 0 END)
SUM(CASE WHEN scenario='PLAN' AND plan_version='F5'
    THEN plan_amount_local ELSE 0 END)
```

Never put `plan_version = 'F5'` in the query-wide `WHERE` clause, because that
removes actual rows. If the user does not specify a plan version, ask which
version to use. For exploration, default to F5 and state the assumption.

### Forecast versus budget

Forecast exists on `fct.budget` rows where `PLAN_SOURCE = 'BUDGET_FILE'`.
Compare `FORECAST_AMOUNT_LOCAL` with `PLAN_AMOUNT_LOCAL` on those same rows.
Do not compare forecast with the full FAGLFLEXP multi-version plan envelope.

### Plan envelope

FAGLFLEXP contains F1-F5. Summing all versions produces a multi-version
envelope, not one approved plan. Group by version or filter to one version
before presenting a plan KPI.

## Vendor and supplier activity

Use `mlv.vendor_spend_monthly` and join `vendor_sk` to
`dim.vendor.DIMENSION_SK`.

### Gross vendor activity

Vendor postings can contain signed offsetting lines. Use:

```text
SUM(ABS(spend_amount_local))
```

Call this gross vendor activity unless the Finance owner confirms a spend sign
rule. Signed `SUM(spend_amount_local)` can net to zero and must not be called
total vendor spend.

### Vendor concentration

Return vendor code `LIFNR`, vendor name `NAME1`, gross activity, line count,
fiscal year, and important filters. Rank by gross activity descending. Use
`TOP (15)` unless the user requests another limit.

### Restricted vendor fields

Never select or return `STCD1`. Do not return batch IDs, hashes, file names, or
load timestamps.

## GL movement trends and drill-down

Use `mlv.gl_balance_monthly` for trends and
`fct.finance_transaction` for document-line drill-down.

### Prior-period comparison

Aggregate by client, ledger, local currency, requested reporting keys and
`fiscal_period_key`. Use `LAG(...) OVER(PARTITION BY client,ledger,currency_local
ORDER BY fiscal_period_key)` for one reporting series; include every additional
non-time reporting key in `PARTITION BY` when returning multiple series.
Calculate change as current net less prior net and use
`NULLIF(ABS(prior_net),0)` to avoid division by zero.

Use neutral directional language:

* Up means current signed net is greater than prior signed net.
* Down means current signed net is lower than prior signed net.
* Direction does not mean favorable or unfavorable.

### GL hierarchy

Use GL account group `KTOKS`, then account code `SAKNR`, then account name
`TXT50` for drill paths. Filter by surrogate key joins, not text.

### Detail rows

For detail, use `fct.finance_transaction` and return at most 100 rows by
default. Include company, year, document, line, ledger, document type, line
text, amount, currency, and only the dimensions requested by the user.

## Finance documents and reversals

`fct.finance_transaction` is at ACDOCA document-line grain. The business
line key is client `RCLNT`, company code `RBUKRS`, fiscal year `GJAHR`,
document number `BELNR`, ledger `RLDNR`, and line `DOCLN`.

### Document count

Count distinct documents by client, company, fiscal year, and document number.
Keep ledger scope explicit; distinguish counting documents from counting
ledger-document representations. Do not count only `BELNR`, because numbers
can repeat between clients, companies or fiscal years.

### Balanced documents

A valid synthetic document normally has signed local amount summing to zero
across its lines. A zero document net does not mean no activity.

### Reversals

Use non-null `REVERSAL_DOCUMENT` to identify reversal-linked postings. Return
both the document and reversal document numbers. Do not infer reversal status
from negative amounts alone.

## Company, division, cost centre, and profit centre

Use facts or MLVs as the starting point, then join only the requested
dimensions.

### Company

`dim.company` contains `BUKRS`, `BUTXT`, local currency `WAERS`, and chart of
accounts `KTOPL`.

### Division

`dim.division` contains division code and name. Division is derived upstream
from profit-centre hierarchy.

### Cost centre

`dim.cost_center` contains controlling area `KOKRS`, cost centre `KOSTL`,
company, assigned profit centre, responsible person `VERAK`, category `KOSAR`,
and hierarchy `KHINR`.

### Profit centre

`dim.profit_center` contains controlling area, profit centre `PRCTR`, company,
hierarchy/division code `KHINR`, and segment.

### SCD2 rules

Facts contain version-level surrogate keys. Join using `DIMENSION_SK` and do
not add `IS_CURRENT = 1` to fact joins. Use `IS_CURRENT = 1` only when listing a
master dimension without a fact.

## Fiscal years, posting dates, and special periods

Use `dim.fiscal_period` for Finance reporting. Use `dim.date` only for calendar
posting-date questions.

### Latest data period

Derive the latest year and period from the relevant fact or MLV. Never derive
latest data from `dim.fiscal_period`, because it intentionally contains future
rows through 2035.

### Special periods

Period 0 is technical carry-forward. Label it separately; it is not a normal
month or, by itself, a validated opening/closing balance.

Periods 13-16 are SAP special periods. They are not calendar months. Include
them for plan or year-end special-period questions, but do not map them to
month names.

### Calendar caveat

The date dimension currently uses a calendar fiscal mapping as a demo
assumption. Prefer fiscal year and fiscal period from facts and MLVs for
financial reporting.

## Currency and amount selection

Every monetary total must preserve one client, ledger and currency, or return
separately labelled groups. MLV keys are `client`, `ledger`, `currency_local`;
Finance Actual also has `currency_group` and `currency_transaction`, while
Vendor Spend also has `currency_group`. Group its local-currency average by
local currency too. Do not average averages without their line-count weights.

Use local amounts with RHCUR and global amounts with RKCUR. A global amount is
not automatically comparable across companies: verify the same currency and
ledger context. No exchange-rate conversion or consolidation is performed by
choosing a differently named measure.

In document detail:

* `RHCUR` identifies local currency.
* `RKCUR` identifies global currency for legacy `AMOUNT_GROUP`.
* `RTCUR` identifies balance transaction currency for `AMOUNT_TRANSACTION`.
* `RWCUR` identifies original transaction currency for `AMOUNT_OTHER`.
* State the amount basis and never substitute RWCUR for RTCUR.

## Visualizations with Code Interpreter

Use this topic when the user asks to plot, chart, graph, visualize, or render an
image.

### Required tool route

Use Code Interpreter only. Execute Python with pandas and matplotlib. Return
exactly one PNG image inline in the chat. Do not request PDF output. Do not
create, publish, deploy, save, or export a report. Do not call a separate
chart-rendering service.

### Chart-ready data

Keep the query result to 100 rows or fewer. Include an explicit chronological
sort key. Return numeric values as numbers, not formatted strings. Use short,
unique aliases and exclude technical metadata.

### Dual-axis charts

For Actual and Plan columns with a variance line:

1. Sort the DataFrame by fiscal-period key.
2. Scale amounts to billions in Python.
3. Use side-by-side `ax.bar` calls for Actual and Plan.
4. Create the percentage axis with `ax2 = ax.twinx()`.
5. Use `ax2.plot` for variance percentage.
6. Add one combined legend, axis units, filters, source, and synthetic label.
7. Highlight the largest negative variance with one red annotation.
8. Render and return the PNG inline without exporting a file or PDF.

Use Eskom cyan `#00AEEF` for Actual, blue `#00529B` for Plan, orange
`#F58220` for variance, red `#D22630` for the exception, and light background
`#F4F7FA`.
<!-- END_TOPICS -->

## Data source example queries

Add each question and SQL statement as one example under `lh_gld_finance`.
These examples were designed for the Lakehouse SQL endpoint. The top three
relevant examples are supplied to the Data Agent at query time.

### Example 1: Executive financial activity

<!-- BEGIN_EXAMPLE_01_QUESTION -->
What are gross debit, credit, and total financial activity by company for the latest actual fiscal year?
<!-- END_EXAMPLE_01_QUESTION -->

<!-- BEGIN_EXAMPLE_01_SQL -->
```sql
WITH y AS (
  SELECT MAX(fiscal_year) fy FROM mlv.gl_balance_monthly
)
SELECT g.client,g.ledger,g.currency_local,c.BUKRS,c.BUTXT,g.fiscal_year,
       SUM(g.debit_amount_local) debit_local,
       SUM(g.credit_amount_local) credit_local,
       SUM(g.debit_amount_local)+SUM(g.credit_amount_local) gross_local
FROM mlv.gl_balance_monthly g
JOIN dim.company c ON c.DIMENSION_SK=g.company_sk
CROSS JOIN y
WHERE g.fiscal_year=y.fy
GROUP BY g.client,g.ledger,g.currency_local,c.BUKRS,c.BUTXT,g.fiscal_year
ORDER BY gross_local DESC,g.client,g.ledger,g.currency_local,c.BUKRS;
```
<!-- END_EXAMPLE_01_SQL -->

### Example 2: Actual versus F5 plan

<!-- BEGIN_EXAMPLE_02_QUESTION -->
Which GL accounts have the largest actual-versus-F5-plan variance in the latest fiscal year?
<!-- END_EXAMPLE_02_QUESTION -->

<!-- BEGIN_EXAMPLE_02_SQL -->
```sql
WITH m AS (
SELECT p.client,p.ledger,p.currency_local,gl.SAKNR,gl.TXT50,p.fiscal_year,
  SUM(CASE WHEN p.scenario='ACTUAL' THEN p.actual_amount_local ELSE 0 END) actual,
  SUM(CASE WHEN p.scenario='PLAN' AND p.plan_version='F5'
      THEN p.plan_amount_local ELSE 0 END) plan_f5
FROM mlv.plan_vs_actual_monthly p
JOIN dim.gl_account gl ON gl.DIMENSION_SK=p.gl_account_sk
WHERE p.fiscal_year=(SELECT MAX(fiscal_year) FROM mlv.plan_vs_actual_monthly)
GROUP BY p.client,p.ledger,p.currency_local,gl.SAKNR,gl.TXT50,p.fiscal_year
)
SELECT TOP (20) client,ledger,currency_local,SAKNR,TXT50,fiscal_year,
       actual,plan_f5,actual-plan_f5 variance
FROM m
ORDER BY ABS(actual-plan_f5) DESC,client,ledger,currency_local,SAKNR;
```
<!-- END_EXAMPLE_02_SQL -->

### Example 3: Forecast versus budget

<!-- BEGIN_EXAMPLE_03_QUESTION -->
How does the latest forecast compare with the matching budget by company?
<!-- END_EXAMPLE_03_QUESTION -->

<!-- BEGIN_EXAMPLE_03_SQL -->
```sql
WITH y AS (
  SELECT MAX(FISCAL_YEAR) fy
  FROM fct.budget WHERE PLAN_SOURCE='BUDGET_FILE'
)
SELECT b.RCLNT client,b.RLDNR ledger,b.RHCUR currency_local,
       c.BUKRS,c.BUTXT,b.FISCAL_YEAR,
       SUM(b.PLAN_AMOUNT_LOCAL) budget_local,
       SUM(b.FORECAST_AMOUNT_LOCAL) forecast_local,
       SUM(b.FORECAST_AMOUNT_LOCAL)-SUM(b.PLAN_AMOUNT_LOCAL) variance_local
FROM fct.budget b
JOIN dim.company c ON c.DIMENSION_SK=b.COMPANY_SK
CROSS JOIN y
WHERE b.PLAN_SOURCE='BUDGET_FILE' AND b.FISCAL_YEAR=y.fy
  AND b.FORECAST_AMOUNT_LOCAL IS NOT NULL
GROUP BY b.RCLNT,b.RLDNR,b.RHCUR,c.BUKRS,c.BUTXT,b.FISCAL_YEAR
ORDER BY ABS(SUM(b.FORECAST_AMOUNT_LOCAL)-SUM(b.PLAN_AMOUNT_LOCAL)) DESC,
         b.RCLNT,b.RLDNR,b.RHCUR,c.BUKRS;
```
<!-- END_EXAMPLE_03_SQL -->

### Example 4: Gross vendor activity

<!-- BEGIN_EXAMPLE_04_QUESTION -->
Who are the top vendors by gross local-currency activity in the latest fiscal year?
<!-- END_EXAMPLE_04_QUESTION -->

<!-- BEGIN_EXAMPLE_04_SQL -->
```sql
WITH y AS (
  SELECT MAX(fiscal_year) fy FROM mlv.vendor_spend_monthly
)
SELECT TOP (15) s.client,s.ledger,s.currency_local,v.LIFNR,v.NAME1,s.fiscal_year,
       SUM(ABS(s.spend_amount_local)) gross_vendor_activity_local,
       SUM(s.line_item_count) line_count
FROM mlv.vendor_spend_monthly s
JOIN dim.vendor v ON v.DIMENSION_SK=s.vendor_sk
CROSS JOIN y
WHERE s.fiscal_year=y.fy
GROUP BY s.client,s.ledger,s.currency_local,v.LIFNR,v.NAME1,s.fiscal_year
ORDER BY gross_vendor_activity_local DESC,s.client,s.ledger,s.currency_local,v.LIFNR;
```
<!-- END_EXAMPLE_04_SQL -->

### Example 5: GL period trend

<!-- BEGIN_EXAMPLE_05_QUESTION -->
Show monthly net movement and period-over-period change for GL account 400200 in the latest fiscal year.
<!-- END_EXAMPLE_05_QUESTION -->

<!-- BEGIN_EXAMPLE_05_SQL -->
```sql
WITH m AS (
  SELECT g.client,g.ledger,g.currency_local,g.fiscal_period_key,
         g.fiscal_year,g.fiscal_period,
         SUM(g.net_movement_local) net_local
  FROM mlv.gl_balance_monthly g
  JOIN dim.gl_account a ON a.DIMENSION_SK=g.gl_account_sk
  WHERE a.SAKNR='0000400200'
    AND g.fiscal_year=(SELECT MAX(fiscal_year) FROM mlv.gl_balance_monthly)
  GROUP BY g.client,g.ledger,g.currency_local,g.fiscal_period_key,
           g.fiscal_year,g.fiscal_period
), c AS (
  SELECT client,ledger,currency_local,fiscal_period_key,fiscal_year,
         fiscal_period,net_local,
         LAG(net_local) OVER(PARTITION BY client,ledger,currency_local
                            ORDER BY fiscal_period_key) prior_local FROM m
)
SELECT client,ledger,currency_local,fiscal_year,fiscal_period,net_local,prior_local,
       net_local-prior_local movement_change,
       (net_local-prior_local)/NULLIF(ABS(prior_local),0) change_pct
FROM c ORDER BY client,ledger,currency_local,fiscal_period_key;
```
<!-- END_EXAMPLE_05_SQL -->

### Example 6: Finance document drill-down

<!-- BEGIN_EXAMPLE_06_QUESTION -->
Show recent finance document lines with GL, cost centre, profit centre, vendor, and customer context.
<!-- END_EXAMPLE_06_QUESTION -->

<!-- BEGIN_EXAMPLE_06_SQL -->
```sql
SELECT TOP (25) f.RCLNT,f.RBUKRS,f.GJAHR,f.BELNR,f.DOCLN,f.RLDNR,f.BLART,
       f.SGTXT,f.AMOUNT_LOCAL,f.RHCUR,a.SAKNR,a.TXT50,
       cc.KOSTL,pc.PRCTR,v.LIFNR,cu.KUNNR
FROM fct.finance_transaction f
JOIN dim.gl_account a ON a.DIMENSION_SK=f.GL_ACCOUNT_SK
LEFT JOIN dim.cost_center cc ON cc.DIMENSION_SK=f.COST_CENTER_SK
LEFT JOIN dim.profit_center pc ON pc.DIMENSION_SK=f.PROFIT_CENTER_SK
LEFT JOIN dim.vendor v ON v.DIMENSION_SK=f.VENDOR_SK
LEFT JOIN dim.customer cu ON cu.DIMENSION_SK=f.CUSTOMER_SK
WHERE f.GJAHR=(SELECT MAX(GJAHR) FROM fct.finance_transaction)
ORDER BY f.GJAHR DESC,f.RCLNT,f.RBUKRS,f.RLDNR,f.BELNR DESC,f.DOCLN;
```
<!-- END_EXAMPLE_06_SQL -->

### Example 7: Cost-centre activity

<!-- BEGIN_EXAMPLE_07_QUESTION -->
Which cost centres have the highest gross local-currency activity in the latest fiscal year?
<!-- END_EXAMPLE_07_QUESTION -->

<!-- BEGIN_EXAMPLE_07_SQL -->
```sql
WITH y AS (
  SELECT MAX(GJAHR) fy FROM fct.finance_transaction
)
SELECT TOP (15) f.RCLNT client,f.RLDNR ledger,f.RHCUR currency_local,
       c.KOSTL,c.VERAK,c.KHINR,
       SUM(ABS(f.AMOUNT_LOCAL)) gross_activity_local,
       COUNT_BIG(*) line_count
FROM fct.finance_transaction f
JOIN dim.cost_center c ON c.DIMENSION_SK=f.COST_CENTER_SK
CROSS JOIN y
WHERE f.GJAHR=y.fy
GROUP BY f.RCLNT,f.RLDNR,f.RHCUR,c.KOSTL,c.VERAK,c.KHINR
ORDER BY gross_activity_local DESC,f.RCLNT,f.RLDNR,f.RHCUR,c.KOSTL;
```
<!-- END_EXAMPLE_07_SQL -->

### Example 8: SAP special periods

<!-- BEGIN_EXAMPLE_08_QUESTION -->
What is the F5 plan amount for SAP special periods 13 through 16 in the latest plan year?
<!-- END_EXAMPLE_08_QUESTION -->

<!-- BEGIN_EXAMPLE_08_SQL -->
```sql
WITH y AS (
  SELECT MAX(FISCAL_YEAR) fy FROM fct.budget WHERE PLAN_VERSION='F5'
)
SELECT b.RCLNT client,b.RLDNR ledger,b.RHCUR currency_local,
       b.FISCAL_YEAR,b.FISCAL_PERIOD,
       SUM(b.PLAN_AMOUNT_LOCAL) f5_plan_amount_local
FROM fct.budget b
CROSS JOIN y
WHERE b.PLAN_VERSION='F5' AND b.FISCAL_YEAR=y.fy
  AND b.FISCAL_PERIOD BETWEEN 13 AND 16
GROUP BY b.RCLNT,b.RLDNR,b.RHCUR,b.FISCAL_YEAR,b.FISCAL_PERIOD
ORDER BY b.RCLNT,b.RLDNR,b.RHCUR,b.FISCAL_PERIOD;
```
<!-- END_EXAMPLE_08_SQL -->

### Example 9: Reversal documents

<!-- BEGIN_EXAMPLE_09_QUESTION -->
Which recent SAP documents are marked as reversals, and what is their signed net local amount?
<!-- END_EXAMPLE_09_QUESTION -->

<!-- BEGIN_EXAMPLE_09_SQL -->
```sql
SELECT TOP (20) f.RCLNT client,f.RLDNR ledger,f.RHCUR currency_local,
       f.RBUKRS,f.GJAHR,f.BELNR,f.REVERSAL_DOCUMENT,
       COUNT_BIG(*) line_count,SUM(f.AMOUNT_LOCAL) net_amount_local
FROM fct.finance_transaction f
WHERE f.REVERSAL_DOCUMENT IS NOT NULL
GROUP BY f.RCLNT,f.RLDNR,f.RHCUR,f.RBUKRS,f.GJAHR,f.BELNR,f.REVERSAL_DOCUMENT
ORDER BY f.GJAHR DESC,f.RCLNT,f.RBUKRS,f.RLDNR,f.RHCUR,f.BELNR DESC;
```
<!-- END_EXAMPLE_09_SQL -->

### Example 10: Company master

<!-- BEGIN_EXAMPLE_10_QUESTION -->
List the current SAP companies, currencies, countries, and charts of accounts.
<!-- END_EXAMPLE_10_QUESTION -->

<!-- BEGIN_EXAMPLE_10_SQL -->
```sql
SELECT MANDT,BUKRS,BUTXT,ORT01,LAND1,WAERS,KTOPL
FROM dim.company
WHERE IS_CURRENT=1
ORDER BY MANDT,BUKRS;
```
<!-- END_EXAMPLE_10_SQL -->

## Schema object descriptions

Add these descriptions in **Schema object descriptions**. Paths are
case-sensitive. Each description is below the 500-character UI limit.

<!-- BEGIN_SCHEMA_DESCRIPTIONS -->
| Schema object path | Description |
|--------------------|-------------|
| `Schemas > fct > Tables > finance_transaction` | Atomic SAP ACDOCA fact at ledger document-line grain. Use for document, reversal, cost-centre, customer, and detailed actual analysis. Signed amounts balance across complete documents. |
| `Schemas > fct > Tables > budget` | Atomic planning fact by client, ledger, local currency, source, scenario, company, GL, cost/profit centre, year, period and version. Includes BUDGET_FILE forecasts and FAGLFLEXP F1-F5 plans. |
| `Schemas > mlv > Tables > finance_actual_monthly` | Monthly signed actuals by client, ledger, local/global/balance transaction currencies and conformed Finance dimensions, year and period. Preserve the relevant currency when aggregating. |
| `Schemas > mlv > Tables > gl_balance_monthly` | Monthly debit, credit, signed net movement and counts by client, ledger, local currency and conformed Finance dimensions. Preserve monetary scope for GL activity and trends. |
| `Schemas > mlv > Tables > vendor_spend_monthly` | Monthly vendor activity by client, ledger, local/global currency, company, division, vendor, GL, year and period. Preserve currency scope for amounts and local-currency averages. |
| `Schemas > mlv > Tables > plan_vs_actual_monthly` | Monthly actual, budget, forecast and F1-F5 plan rows by client, ledger, local currency and conformed Finance dimensions. Use conditional aggregation to retain actual rows when selecting a plan version. |
| `Schemas > dim > Tables > company` | SCD2 SAP company master with company code, name, city, country, local currency, and chart of accounts. |
| `Schemas > dim > Tables > gl_account` | SCD2 GL account master with account code, English names, balance-sheet flag, P&L type, and account group. |
| `Schemas > dim > Tables > cost_center` | SCD2 cost-centre master with controlling area, company, profit centre, responsible person, category, hierarchy, and validity context. |
| `Schemas > dim > Tables > profit_center` | SCD2 profit-centre master with controlling area, company, hierarchy or division code, segment, responsible person, and validity context. |
| `Schemas > dim > Tables > division` | SCD2 division master derived from the profit-centre hierarchy. Contains division code and name. |
| `Schemas > dim > Tables > vendor` | SCD2 vendor master with vendor code, names, country, city, region, and account group. Do not expose STCD1. |
| `Schemas > dim > Tables > customer` | SCD2 customer master with customer code, names, country, city, region, and account group. Do not expose STCD1. |
| `Schemas > dim > Tables > date` | Contiguous calendar date dimension for posting-date analysis. The fiscal mapping is a demo calendar assumption. |
| `Schemas > dim > Tables > fiscal_period` | Finance fiscal-period dimension with years and SAP periods 0-16. Period 0 is technical carry-forward, 13-16 are special periods; neither is an ordinary calendar month. |
| `Schemas > fct > Tables > finance_transaction > Columns > AMOUNT_LOCAL` | Signed amount in company-code local currency. State the currency basis and do not describe the all-account sum as gross activity. |
| `Schemas > fct > Tables > finance_transaction > Columns > AMOUNT_GROUP` | Signed KSL amount in global currency RKCUR. Legacy Group naming does not imply a universal group/consolidation currency. Keep client, ledger and RKCUR scope explicit. |
| `Schemas > fct > Tables > finance_transaction > Columns > AMOUNT_TRANSACTION` | Signed TSL amount in balance transaction currency RTCUR. RWCUR is not its currency. |
| `Schemas > fct > Tables > finance_transaction > Columns > AMOUNT_OTHER` | Signed WSL amount in original transaction currency RWCUR. |
| `Schemas > fct > Tables > finance_transaction > Columns > RCLNT` | SAP client. Preserve it in document identity, monetary grouping and cross-client filters. |
| `Schemas > fct > Tables > finance_transaction > Columns > RLDNR` | SAP ledger. Preserve or explicitly filter it before monetary aggregation. |
| `Schemas > fct > Tables > finance_transaction > Columns > RHCUR` | Company-code currency of HSL / AMOUNT_LOCAL. |
| `Schemas > fct > Tables > finance_transaction > Columns > RKCUR` | Global currency of KSL / legacy AMOUNT_GROUP. |
| `Schemas > fct > Tables > finance_transaction > Columns > RTCUR` | Balance transaction currency of TSL / AMOUNT_TRANSACTION. Never substitute RWCUR. |
| `Schemas > fct > Tables > finance_transaction > Columns > RWCUR` | Original transaction currency of WSL / AMOUNT_OTHER, not TSL. |
| `Schemas > fct > Tables > finance_transaction > Columns > BELNR` | SAP accounting document number. Combine with client, company and fiscal year for document counts; ledger identifies its journal representation. |
| `Schemas > fct > Tables > finance_transaction > Columns > DOCLN` | SAP document line identifier within a ledger accounting document. |
| `Schemas > fct > Tables > finance_transaction > Columns > REVERSAL_DOCUMENT` | Related reversal document number. A non-null value identifies a reversal-linked posting. |
| `Schemas > fct > Tables > budget > Columns > PLAN_SOURCE` | Planning origin. BUDGET_FILE rows contain budget and forecast values; FAGLFLEXP rows contain SAP plan versions. |
| `Schemas > fct > Tables > budget > Columns > PLAN_VERSION` | Planning version. FAGLFLEXP uses F1-F5; budget-file rows use their source version. Group or filter before summing plans. |
| `Schemas > fct > Tables > budget > Columns > FORECAST_AMOUNT_LOCAL` | Forecast amount in local currency. Populated for BUDGET_FILE rows; compare with plan amount on those same rows. |
| `Schemas > fct > Tables > budget > Columns > RCLNT` | SAP client supplied by the source or explicit budget financeContext. Never infer a customer default. |
| `Schemas > fct > Tables > budget > Columns > RLDNR` | Ledger supplied by the source or explicit budget financeContext. Preserve it when comparing actuals and plans. |
| `Schemas > fct > Tables > budget > Columns > RHCUR` | Local currency of plan and forecast amounts. Budget-file currency is resolved upstream from the company mapping. |
| `Schemas > mlv > Tables > finance_actual_monthly > Columns > client` | SAP client in the monthly actual grain. Preserve or filter before monetary aggregation. |
| `Schemas > mlv > Tables > finance_actual_monthly > Columns > ledger` | SAP ledger in the monthly actual grain. Never silently sum multiple ledger representations. |
| `Schemas > mlv > Tables > finance_actual_monthly > Columns > currency_local` | RHCUR currency of actual_amount_local. |
| `Schemas > mlv > Tables > finance_actual_monthly > Columns > currency_group` | RKCUR global currency of legacy actual_amount_group. |
| `Schemas > mlv > Tables > finance_actual_monthly > Columns > currency_transaction` | RTCUR balance transaction currency of actual_amount_transaction. |
| `Schemas > mlv > Tables > vendor_spend_monthly > Columns > client` | SAP client in the vendor serving grain. |
| `Schemas > mlv > Tables > vendor_spend_monthly > Columns > ledger` | SAP ledger in the vendor serving grain. |
| `Schemas > mlv > Tables > vendor_spend_monthly > Columns > currency_local` | Currency of spend_amount_local and average_line_item_value. Preserve it in totals and weighted averages. |
| `Schemas > mlv > Tables > vendor_spend_monthly > Columns > currency_group` | Global RKCUR currency of legacy spend_amount_group. |
| `Schemas > mlv > Tables > gl_balance_monthly > Columns > client` | SAP client in the GL movement grain; preserve it in totals and window partitions. |
| `Schemas > mlv > Tables > gl_balance_monthly > Columns > ledger` | SAP ledger in the GL movement grain; preserve it in totals and window partitions. |
| `Schemas > mlv > Tables > gl_balance_monthly > Columns > currency_local` | Currency of debit, credit and net movement. Preserve it in sums and prior-period window partitions. |
| `Schemas > mlv > Tables > plan_vs_actual_monthly > Columns > client` | SAP client shared by each comparable actual/plan group. |
| `Schemas > mlv > Tables > plan_vs_actual_monthly > Columns > ledger` | SAP ledger shared by each comparable actual/plan group. |
| `Schemas > mlv > Tables > plan_vs_actual_monthly > Columns > currency_local` | Comparable company-code currency for actual, plan, forecast and variance amounts. No conversion is implied. |
| `Schemas > mlv > Tables > gl_balance_monthly > Columns > net_movement_local` | Signed monthly net GL movement. Positive or negative direction is not automatically favorable or unfavorable. |
| `Schemas > mlv > Tables > gl_balance_monthly > Columns > debit_amount_local` | Positive absolute monthly debit activity in local currency. |
| `Schemas > mlv > Tables > gl_balance_monthly > Columns > credit_amount_local` | Positive absolute monthly credit activity in local currency. |
| `Schemas > mlv > Tables > vendor_spend_monthly > Columns > spend_amount_local` | Signed monthly vendor activity in local currency. Use SUM(ABS(value)) for gross vendor activity. |
| `Schemas > mlv > Tables > plan_vs_actual_monthly > Columns > scenario` | Row scenario such as ACTUAL, PLAN, or BUDGET. Use conditional aggregation for comparisons. |
| `Schemas > mlv > Tables > plan_vs_actual_monthly > Columns > plan_version` | ACTUAL on actual rows and F1-F5 on plan rows. Do not filter the entire query to F5 when comparing with actuals. |
| `Schemas > dim > Tables > fiscal_period > Columns > FISCAL_PERIOD_KEY` | Integer key calculated as fiscal year times 100 plus fiscal period. Use for joins and chronological ordering. |
| `Schemas > dim > Tables > fiscal_period > Columns > IS_SPECIAL_PERIOD` | True for SAP special periods 13-16. Special periods are not calendar months. |
| `Schemas > dim > Tables > vendor > Columns > STCD1` | Restricted tax identifier. Never select, return, summarize, or expose this column. |
| `Schemas > dim > Tables > customer > Columns > STCD1` | Restricted tax identifier. Never select, return, summarize, or expose this column. |
<!-- END_SCHEMA_DESCRIPTIONS -->

## Validation checklist

1. Paste only the text between the markers into each corresponding Fabric UI
   field.
2. Verify the UI character counter remains below its maximum.
3. Add each example question and SQL statement as a separate pair.
4. Paste the Topics block into **Data source topics**.
5. Apply schema descriptions to the matching case-sensitive object paths.
6. Test the ten example questions in the Data Agent test pane.
7. Review the **Open Data Source Topic** run step to confirm relevant topics
   were retrieved.
8. Confirm responses state that the data is synthetic.
9. Confirm generated queries use `mlv` before `fct` for summary questions.
10. Confirm no response exposes restricted technical or tax columns.
