---
title: SAP Finance semantic model relationship mapping
description: Dimension-to-fact relationship specification for the Fabric Gold layer
author: Data and AI Engineering
ms.date: 2026-09-29
ms.topic: reference
keywords:
  - Microsoft Fabric
  - Power BI
  - Direct Lake
  - semantic model
  - SAP Finance
estimated_reading_time: 8
---

## Model shape

Use a Direct Lake fact constellation over the `lh_gld_finance` lakehouse.
Shared conformed dimensions filter the atomic facts and derived Fabric
Materialized Lake Views (MLVs).

Apply these rules to every relationship:

* Cardinality: one-to-many from dimension to fact
* Cross-filter direction: single, dimension to fact
* Active: yes, except future role-playing date relationships
* Referential integrity: validate before deployment
* Fact foreign keys: hidden in the semantic model
* Dimension surrogate keys: hidden in the semantic model

Do not create relationships between facts. Do not create relationships between
dimensions. Avoid bidirectional filters because the shared dimensions already
provide consistent filtering across facts.

The complete row-level specification is available in
[relationship_mapping.csv](./relationship_mapping.csv).

## Recommended semantic names

| Gold object                  | Semantic table             | Grain                                                                    |
|------------------------------|----------------------------|--------------------------------------------------------------------------|
| `dim.company`                | Company                    | One SCD2 version per SAP client and company code                          |
| `dim.gl_account`             | GL Account                 | One SCD2 version per SAP client, chart of accounts, and GL account        |
| `dim.cost_center`            | Cost Center                | One SCD2 version per SAP client, controlling area, and cost center        |
| `dim.profit_center`          | Profit Center              | One SCD2 version per SAP client, controlling area, and profit center      |
| `dim.division`               | Division                   | One SCD2 version per division                                             |
| `dim.vendor`                 | Vendor                     | One SCD2 version per SAP client and vendor                                |
| `dim.customer`               | Customer                   | One SCD2 version per SAP client and customer                              |
| `dim.date`                   | Date                       | One row per calendar date                                                 |
| `dim.fiscal_period`          | Fiscal Period              | One row per fiscal year and SAP fiscal period, including periods 13-16    |
| `fct.finance_transaction`    | Finance Transaction        | One ACDOCA ledger document line                                           |
| `fct.budget`                 | Budget and Plan            | One planning source, scenario, entity combination, year, period, version  |
| `mlv.finance_actual_monthly` | Finance Actual Monthly     | Monthly actuals by the available conformed Finance dimensions             |
| `mlv.vendor_spend_monthly`   | Vendor Spend Monthly       | Monthly vendor spend by company, division, vendor, and GL account         |
| `mlv.gl_balance_monthly`     | GL Balance Monthly         | Monthly GL movement by the available conformed Finance dimensions         |
| `mlv.plan_vs_actual_monthly` | Plan vs Actual Monthly     | Monthly actual, budget, forecast, and plan by scenario and version         |

## Relationship matrix

`Ready` means both relationship columns exist in the Gold layer.

| Dimension     | Finance transaction | Budget and plan | Finance actual monthly | Vendor spend monthly | GL balance monthly | Plan vs actual monthly |
|---------------|---------------------|-----------------|------------------------|----------------------|--------------------|------------------------|
| Company       | Ready               | Ready           | Ready                  | Ready                | Ready              | Ready                  |
| GL Account    | Ready               | Ready           | Ready                  | Ready                | Ready              | Ready                  |
| Cost Center   | Ready               | Ready           | Ready                  | Not applicable       | Ready              | Ready                  |
| Profit Center | Ready               | Ready           | Ready                  | Not applicable       | Ready              | Ready                  |
| Division      | Ready               | Ready           | Ready                  | Ready                | Ready              | Ready                  |
| Vendor        | Ready               | Not applicable  | Not applicable         | Ready                | Not applicable     | Not applicable         |
| Customer      | Ready               | Not applicable  | Not applicable         | Not applicable       | Not applicable     | Not applicable         |
| Date          | Ready               | Not applicable  | Not applicable         | Not applicable       | Not applicable     | Not applicable         |
| Fiscal Period | Ready               | Ready           | Ready                  | Ready                | Ready              | Ready                  |

## Ready relationship keys

### Finance Transaction

| Dimension     | Dimension key  | Fact key           | Null behavior                                  |
|---------------|----------------|--------------------|------------------------------------------------|
| Company       | `DIMENSION_SK` | `COMPANY_SK`       | Must resolve                                   |
| GL Account    | `DIMENSION_SK` | `GL_ACCOUNT_SK`    | Must resolve                                   |
| Cost Center   | `DIMENSION_SK` | `COST_CENTER_SK`   | Must resolve for the current synthetic dataset |
| Profit Center | `DIMENSION_SK` | `PROFIT_CENTER_SK` | Must resolve for the current synthetic dataset |
| Division      | `DIMENSION_SK` | `DIVISION_SK`      | Must resolve                                   |
| Vendor        | `DIMENSION_SK` | `VENDOR_SK`        | Null for non-vendor postings                   |
| Customer      | `DIMENSION_SK` | `CUSTOMER_SK`      | Null for non-customer postings                 |
| Date          | `DATE_SK`      | `POSTING_DATE_SK`  | Must resolve                                   |
| Fiscal Period | `FISCAL_PERIOD_KEY` | `FISCAL_PERIOD_KEY` | Must resolve                               |

`POSTING_DATE_SK` is the active date relationship. Do not relate extraction,
Silver load, or Gold load timestamps to the Date table. If Document Date or
Entry Date analysis is required later, add dedicated integer date keys
upstream and create inactive role-playing relationships only when measures
activate them with `USERELATIONSHIP`.

### Budget and Plan

| Dimension     | Dimension key  | Fact key           | Null behavior                                      |
|---------------|----------------|--------------------|----------------------------------------------------|
| Company       | `DIMENSION_SK` | `COMPANY_SK`       | Must resolve                                       |
| GL Account    | `DIMENSION_SK` | `GL_ACCOUNT_SK`    | Must resolve                                       |
| Cost Center   | `DIMENSION_SK` | `COST_CENTER_SK`   | Null for FAGLFLEXP rows without cost-center detail |
| Profit Center | `DIMENSION_SK` | `PROFIT_CENTER_SK` | Must resolve                                       |
| Division      | `DIMENSION_SK` | `DIVISION_SK`      | Must resolve                                       |
| Fiscal Period | `FISCAL_PERIOD_KEY` | `FISCAL_PERIOD_KEY` | Must resolve                                   |

The Budget and Plan fact uses the current SCD2 version during planning
resolution. This differs intentionally from Finance Transaction, which stores
the point-in-time dimension version for each posting date.

### Derived monthly MLVs

Use the matching lowercase surrogate key in each MLV:

* `company_sk` to Company `DIMENSION_SK`
* `gl_account_sk` to GL Account `DIMENSION_SK`
* `cost_center_sk` to Cost Center `DIMENSION_SK`, where present
* `profit_center_sk` to Profit Center `DIMENSION_SK`, where present
* `division_sk` to Division `DIMENSION_SK`
* `vendor_sk` to Vendor `DIMENSION_SK` for Vendor Spend Monthly
* `fiscal_period_key` to Fiscal Period `FISCAL_PERIOD_KEY`

## Fiscal-period design

Power BI does not support composite relationship keys. The Gold layer now
provides `dim.fiscal_period` at this grain:

```text
Fiscal year + fiscal period (1 through 16)
```

The same integer key is present in Finance Transaction, Budget and Plan, and
all four monthly MLVs:

```text
FISCAL_PERIOD_KEY = (FISCAL_YEAR * 100) + FISCAL_PERIOD
```

The fiscal-period dimension includes:

* `FISCAL_PERIOD_KEY`
* `FISCAL_YEAR`
* `FISCAL_PERIOD`
* `FISCAL_PERIOD_NAME`
* `FISCAL_QUARTER`
* `IS_SPECIAL_PERIOD`
* `FISCAL_YEAR_PERIOD_LABEL`
* `FISCAL_YEAR_PERIOD_SORT`

Periods 13 through 16 are SAP special periods. They relate through Fiscal
Period rather than Date because no unique calendar date represents them.

## SCD2 handling

Each SCD2 dimension can contain multiple rows for one natural business key.
`DIMENSION_SK` identifies one historical version and remains unique across the
dimension. Facts already store this version-level surrogate key.

Create relationships on `DIMENSION_SK`, not on natural keys such as `BUKRS`,
`SAKNR`, `KOSTL`, or `PRCTR`. Natural keys are not unique across SCD2 history
and some are only unique when combined with SAP client or controlling-area
columns.

The current MD5 surrogate keys are strings. They are relationship-compatible,
but integer surrogate keys would usually provide better compression and join
performance if the Gold design is revised later.

## Model implementation settings

For each semantic-model relationship:

```text
From: fact foreign key
To: dimension surrogate key
Cardinality: Many to one (*:1)
Cross-filter direction: Single
Active: Yes
```

Set these model properties:

* Direct Lake partitions for every Gold object
* `discourageImplicitMeasures` enabled
* Auto date/time disabled
* Technical keys hidden
* Numeric measure source columns hidden after explicit measures are created
* `summarizeBy: none` for keys, years, periods, document numbers, and codes

## Validation before publishing

Run these checks against the Gold layer:

1. Confirm every dimension `DIMENSION_SK` is unique.
2. Confirm `dim.date.DATE_SK` is unique and contiguous.
3. Confirm mandatory fact foreign keys are non-null.
4. Confirm every non-null fact foreign key resolves to its dimension.
5. Confirm the Finance Transaction row count does not change after joining all
   dimensions.
6. Confirm the Budget and Plan row count does not change after joining its
   dimensions.
7. Confirm every relationship uses matching data types.
8. Confirm all relationships filter from dimension to fact only.

> [!IMPORTANT]
> Decide which overlapping facts are visible to report authors. For example,
> Plan vs Actual Monthly already contains actual and planning measures. Exposing
> it alongside Finance Actual Monthly and Budget and Plan is valid, but measure
> names and descriptions must prevent double counting or ambiguous report use.
