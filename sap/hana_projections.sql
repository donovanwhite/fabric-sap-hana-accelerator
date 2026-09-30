/*
Production SAP HANA direct base-table projection templates.

Replace <SAP_SCHEMA> with the ABAP schema in the target HANA system. It is
often SAPABAP1, but it is environment-specific. These projections deliberately
use SAP table and column names that match the SAP_FIN_DB simulator.

Business data is selected directly from the underlying HANA tables. These
templates are full projections because the selected tables do not provide one
complete, reliable change timestamp or deletion feed. Do not assume the legacy
demo LAST_MODIFIED_UTC column exists in SAP.
*/

SELECT
    "RCLNT","RLDNR","RBUKRS","GJAHR","POPER","BELNR","DOCLN","RACCT",
    "RCNTR","PRCTR","KOKRS","LIFNR","KUNNR","BUDAT","BLDAT","DRCRK",
    "BLART","AWTYP","SGTXT","RHCUR","RKCUR","RWCUR","HSL","KSL","TSL",
    "WSL","MSL"
FROM "<SAP_SCHEMA>"."ACDOCA";

SELECT
    "MANDT","BUKRS","GJAHR","BELNR","MONAT","BUDAT","BLDAT","BLART",
    "STBLG","BKTXT"
FROM "<SAP_SCHEMA>"."BKPF";

SELECT "MANDT","BUKRS","BUTXT","ORT01","LAND1","WAERS","KTOPL"
FROM "<SAP_SCHEMA>"."T001";

SELECT
    "MANDT","KOKRS","KOSTL","BUKRS","PRCTR","VERAK","KOSAR","KHINR",
    "DATAB","DATBI"
FROM "<SAP_SCHEMA>"."CSKS";

SELECT
    "MANDT","KOKRS","PRCTR","BUKRS","VERAK","KHINR","SEGMENT","DATAB",
    "DATBI"
FROM "<SAP_SCHEMA>"."CEPC";

SELECT "MANDT","KTOPL","SAKNR","XBILK","GVTYP","KTOKS"
FROM "<SAP_SCHEMA>"."SKA1";

SELECT "MANDT","SPRAS","KTOPL","SAKNR","TXT20","TXT50"
FROM "<SAP_SCHEMA>"."SKAT";

SELECT
    "MANDT","LIFNR","NAME1","NAME2","LAND1","ORT01","REGIO","KTOKK",
    "STCD1"
FROM "<SAP_SCHEMA>"."LFA1";

SELECT
    "MANDT","KUNNR","NAME1","NAME2","LAND1","ORT01","REGIO","KTOKD",
    "STCD1"
FROM "<SAP_SCHEMA>"."KNA1";

SELECT
    "RCLNT","RLDNR","RBUKRS","RYEAR","RACCT","PRCTR","KOKRS","VERSN",
    "CURTYPE","RHCUR","TSL01","TSL02","TSL03","TSL04","TSL05","TSL06",
    "TSL07","TSL08","TSL09","TSL10","TSL11","TSL12","TSL13","TSL14",
    "TSL15","TSL16"
FROM "<SAP_SCHEMA>"."FAGLFLEXP";

/*
The following objects are custom demo contracts, not standard SAP tables.
Map them to approved Z tables, CDS views, or planning sources in production.
*/

SELECT
    "COMPANY_CODE","GL_ACCOUNT","COST_CENTER","PROFIT_CENTER","FISCAL_YEAR",
    "FISCAL_PERIOD","BUDGET_AMOUNT_LOCAL","FORECAST_AMOUNT_LOCAL",
    "PLAN_VERSION","DATA_CLASSIFICATION"
FROM "<CUSTOM_SCHEMA>"."<BUDGET_SOURCE>";

SELECT "DIVISION_CODE","DIVISION_NAME"
FROM "<CUSTOM_SCHEMA>"."<DIVISION_SOURCE>";

SELECT "GL_ACCOUNT","GL_ACCOUNT_NAME","FINANCE_CATEGORY"
FROM "<CUSTOM_SCHEMA>"."<GL_ACCOUNT_REFERENCE_SOURCE>";
