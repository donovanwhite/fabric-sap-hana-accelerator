## Actuals, debits, credits, and financial activity

Use `mlv.gl_balance_monthly` for executive actual activity and GL movement.
It is already grouped by company, division, profit centre, cost centre, GL,
fiscal year, and fiscal period.

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
atomic plan rows or forecast-versus-budget analysis.

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

Aggregate by `fiscal_period_key`, then use `LAG` ordered by that key. Calculate
change as current net less prior net. Calculate percentage with
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
document key is company code `RBUKRS`, fiscal year `GJAHR`, document number
`BELNR`, ledger `RLDNR`, and line `DOCLN`.

### Document count

Count distinct documents by the combination of company, fiscal year, and
document number. Do not count only `BELNR`, because document numbers can repeat
between company codes or fiscal years.

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

Periods 13-16 are SAP special periods. They are not calendar months. Include
them for plan or year-end special-period questions, but do not map them to
month names.

### Calendar caveat

The date dimension currently uses a calendar fiscal mapping as a demo
assumption. Prefer fiscal year and fiscal period from facts and MLVs for
financial reporting.

## Currency and amount selection

Use local amounts for company-level analysis and clearly state company-code
currency. Use group amounts for cross-company analysis when available. Do not
sum transaction-currency amounts across different currencies without grouping
by currency.

In document detail:

* `RHCUR` identifies local currency.
* `RKCUR` identifies group currency.
* `RWCUR` identifies transaction or additional currency context.
* `AMOUNT_LOCAL`, `AMOUNT_GROUP`, and `AMOUNT_TRANSACTION` must be labelled
  with their amount basis.