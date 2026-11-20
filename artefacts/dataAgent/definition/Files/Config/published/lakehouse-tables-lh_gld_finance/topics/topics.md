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
