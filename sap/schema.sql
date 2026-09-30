SET NOCOUNT ON;
GO

IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = N'SAPABAP1')
    EXEC(N'CREATE SCHEMA SAPABAP1 AUTHORIZATION dbo');
GO

IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = N'SAPDEMO')
    EXEC(N'CREATE SCHEMA SAPDEMO AUTHORIZATION dbo');
GO

IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = N'etl')
    EXEC(N'CREATE SCHEMA etl AUTHORIZATION dbo');
GO

IF OBJECT_ID(N'SAPABAP1.ACDOCA', N'U') IS NULL
BEGIN
    CREATE TABLE SAPABAP1.ACDOCA
    (
        RCLNT varchar(3) NOT NULL,
        RLDNR varchar(2) NOT NULL,
        RBUKRS varchar(4) NOT NULL,
        GJAHR varchar(4) NOT NULL,
        POPER varchar(3) NOT NULL,
        BELNR varchar(10) NOT NULL,
        DOCLN varchar(6) NOT NULL,
        RACCT varchar(10) NOT NULL,
        RCNTR varchar(10) NULL,
        PRCTR varchar(10) NULL,
        KOKRS varchar(4) NULL,
        LIFNR varchar(10) NULL,
        KUNNR varchar(10) NULL,
        BUDAT varchar(8) NULL,
        BLDAT varchar(8) NULL,
        DRCRK varchar(1) NULL,
        BLART varchar(2) NULL,
        AWTYP varchar(10) NULL,
        SGTXT nvarchar(100) NULL,
        RHCUR varchar(5) NULL,
        RKCUR varchar(5) NULL,
        RWCUR varchar(5) NULL,
        HSL decimal(23,2) NULL,
        KSL decimal(23,2) NULL,
        TSL decimal(23,2) NULL,
        WSL decimal(23,2) NULL,
        MSL decimal(23,3) NULL,
        CONSTRAINT PK_ACDOCA PRIMARY KEY
            (RCLNT, RLDNR, RBUKRS, GJAHR, BELNR, DOCLN)
    );
END;
GO

IF OBJECT_ID(N'SAPABAP1.BKPF', N'U') IS NULL
BEGIN
    CREATE TABLE SAPABAP1.BKPF
    (
        MANDT varchar(3) NOT NULL,
        BUKRS varchar(4) NOT NULL,
        GJAHR varchar(4) NOT NULL,
        BELNR varchar(10) NOT NULL,
        MONAT varchar(2) NULL,
        BUDAT varchar(8) NULL,
        BLDAT varchar(8) NULL,
        BLART varchar(2) NULL,
        STBLG varchar(10) NULL,
        BKTXT nvarchar(100) NULL,
        CONSTRAINT PK_BKPF PRIMARY KEY (MANDT, BUKRS, GJAHR, BELNR)
    );
END;
GO

IF OBJECT_ID(N'SAPABAP1.T001', N'U') IS NULL
BEGIN
    CREATE TABLE SAPABAP1.T001
    (
        MANDT varchar(3) NOT NULL,
        BUKRS varchar(4) NOT NULL,
        BUTXT nvarchar(100) NULL,
        ORT01 nvarchar(50) NULL,
        LAND1 varchar(3) NULL,
        WAERS varchar(5) NULL,
        KTOPL varchar(4) NULL,
        CONSTRAINT PK_T001 PRIMARY KEY (MANDT, BUKRS)
    );
END;
GO

IF OBJECT_ID(N'SAPABAP1.CSKS', N'U') IS NULL
BEGIN
    CREATE TABLE SAPABAP1.CSKS
    (
        MANDT varchar(3) NOT NULL,
        KOKRS varchar(4) NOT NULL,
        KOSTL varchar(10) NOT NULL,
        BUKRS varchar(4) NULL,
        PRCTR varchar(10) NULL,
        VERAK nvarchar(50) NULL,
        KOSAR varchar(4) NULL,
        KHINR varchar(12) NULL,
        DATAB varchar(8) NULL,
        DATBI varchar(8) NULL,
        CONSTRAINT PK_CSKS PRIMARY KEY (MANDT, KOKRS, KOSTL)
    );
END;
GO

IF OBJECT_ID(N'SAPABAP1.CEPC', N'U') IS NULL
BEGIN
    CREATE TABLE SAPABAP1.CEPC
    (
        MANDT varchar(3) NOT NULL,
        KOKRS varchar(4) NOT NULL,
        PRCTR varchar(10) NOT NULL,
        BUKRS varchar(4) NULL,
        VERAK nvarchar(50) NULL,
        KHINR varchar(12) NULL,
        SEGMENT varchar(10) NULL,
        DATAB varchar(8) NULL,
        DATBI varchar(8) NULL,
        CONSTRAINT PK_CEPC PRIMARY KEY (MANDT, KOKRS, PRCTR)
    );
END;
GO

IF OBJECT_ID(N'SAPABAP1.SKA1', N'U') IS NULL
BEGIN
    CREATE TABLE SAPABAP1.SKA1
    (
        MANDT varchar(3) NOT NULL,
        KTOPL varchar(4) NOT NULL,
        SAKNR varchar(10) NOT NULL,
        XBILK varchar(1) NULL,
        GVTYP varchar(2) NULL,
        KTOKS varchar(4) NULL,
        CONSTRAINT PK_SKA1 PRIMARY KEY (MANDT, KTOPL, SAKNR)
    );
END;
GO

IF OBJECT_ID(N'SAPABAP1.SKAT', N'U') IS NULL
BEGIN
    CREATE TABLE SAPABAP1.SKAT
    (
        MANDT varchar(3) NOT NULL,
        SPRAS varchar(2) NOT NULL,
        KTOPL varchar(4) NOT NULL,
        SAKNR varchar(10) NOT NULL,
        TXT20 nvarchar(20) NULL,
        TXT50 nvarchar(50) NULL,
        CONSTRAINT PK_SKAT PRIMARY KEY (MANDT, SPRAS, KTOPL, SAKNR)
    );
END;
GO

IF OBJECT_ID(N'SAPABAP1.LFA1', N'U') IS NULL
BEGIN
    CREATE TABLE SAPABAP1.LFA1
    (
        MANDT varchar(3) NOT NULL,
        LIFNR varchar(10) NOT NULL,
        NAME1 nvarchar(80) NULL,
        NAME2 nvarchar(80) NULL,
        LAND1 varchar(3) NULL,
        ORT01 nvarchar(50) NULL,
        REGIO varchar(3) NULL,
        KTOKK varchar(4) NULL,
        STCD1 nvarchar(20) NULL,
        CONSTRAINT PK_LFA1 PRIMARY KEY (MANDT, LIFNR)
    );
END;
GO

IF OBJECT_ID(N'SAPABAP1.KNA1', N'U') IS NULL
BEGIN
    CREATE TABLE SAPABAP1.KNA1
    (
        MANDT varchar(3) NOT NULL,
        KUNNR varchar(10) NOT NULL,
        NAME1 nvarchar(80) NULL,
        NAME2 nvarchar(80) NULL,
        LAND1 varchar(3) NULL,
        ORT01 nvarchar(50) NULL,
        REGIO varchar(3) NULL,
        KTOKD varchar(4) NULL,
        STCD1 nvarchar(20) NULL,
        CONSTRAINT PK_KNA1 PRIMARY KEY (MANDT, KUNNR)
    );
END;
GO

IF OBJECT_ID(N'SAPABAP1.FAGLFLEXP', N'U') IS NULL
BEGIN
    CREATE TABLE SAPABAP1.FAGLFLEXP
    (
        RCLNT varchar(3) NOT NULL,
        RLDNR varchar(2) NOT NULL,
        RBUKRS varchar(4) NOT NULL,
        RYEAR varchar(4) NOT NULL,
        RACCT varchar(10) NOT NULL,
        PRCTR varchar(10) NOT NULL,
        KOKRS varchar(4) NULL,
        VERSN varchar(3) NOT NULL,
        CURTYPE varchar(2) NULL,
        RHCUR varchar(5) NULL,
        TSL01 decimal(23,2) NULL,
        TSL02 decimal(23,2) NULL,
        TSL03 decimal(23,2) NULL,
        TSL04 decimal(23,2) NULL,
        TSL05 decimal(23,2) NULL,
        TSL06 decimal(23,2) NULL,
        TSL07 decimal(23,2) NULL,
        TSL08 decimal(23,2) NULL,
        TSL09 decimal(23,2) NULL,
        TSL10 decimal(23,2) NULL,
        TSL11 decimal(23,2) NULL,
        TSL12 decimal(23,2) NULL,
        TSL13 decimal(23,2) NULL,
        TSL14 decimal(23,2) NULL,
        TSL15 decimal(23,2) NULL,
        TSL16 decimal(23,2) NULL,
        CONSTRAINT PK_FAGLFLEXP PRIMARY KEY
            (RCLNT, RLDNR, RBUKRS, RYEAR, RACCT, PRCTR, VERSN)
    );
END;
GO

IF OBJECT_ID(N'SAPDEMO.BUDGET', N'U') IS NULL
BEGIN
    CREATE TABLE SAPDEMO.BUDGET
    (
        COMPANY_CODE varchar(4) NOT NULL,
        GL_ACCOUNT varchar(10) NOT NULL,
        COST_CENTER varchar(10) NOT NULL,
        PROFIT_CENTER varchar(10) NOT NULL,
        FISCAL_YEAR varchar(4) NOT NULL,
        FISCAL_PERIOD varchar(3) NOT NULL,
        BUDGET_AMOUNT_LOCAL decimal(23,2) NOT NULL,
        FORECAST_AMOUNT_LOCAL decimal(23,2) NULL,
        PLAN_VERSION varchar(10) NOT NULL,
        DATA_CLASSIFICATION varchar(30) NULL,
        CONSTRAINT PK_BUDGET PRIMARY KEY
            (COMPANY_CODE, GL_ACCOUNT, COST_CENTER, PROFIT_CENTER,
             FISCAL_YEAR, FISCAL_PERIOD, PLAN_VERSION)
    );
END;
GO

IF OBJECT_ID(N'SAPDEMO.DIVISION_REFERENCE', N'U') IS NULL
BEGIN
    CREATE TABLE SAPDEMO.DIVISION_REFERENCE
    (
        DIVISION_CODE varchar(12) NOT NULL,
        DIVISION_NAME nvarchar(100) NOT NULL,
        CONSTRAINT PK_DIVISION_REFERENCE PRIMARY KEY (DIVISION_CODE)
    );
END;
GO

IF OBJECT_ID(N'SAPDEMO.GL_ACCOUNT_REFERENCE', N'U') IS NULL
BEGIN
    CREATE TABLE SAPDEMO.GL_ACCOUNT_REFERENCE
    (
        GL_ACCOUNT varchar(10) NOT NULL,
        GL_ACCOUNT_NAME nvarchar(100) NOT NULL,
        FINANCE_CATEGORY nvarchar(50) NOT NULL,
        CONSTRAINT PK_GL_ACCOUNT_REFERENCE PRIMARY KEY (GL_ACCOUNT)
    );
END;
GO

IF OBJECT_ID(N'etl.load_batch', N'U') IS NULL
BEGIN
    CREATE TABLE etl.load_batch
    (
        LOAD_BATCH_ID uniqueidentifier NOT NULL,
        LOAD_MODE varchar(10) NOT NULL,
        BUSINESS_DATE date NOT NULL,
        STARTED_UTC datetime2(3) NOT NULL,
        COMPLETED_UTC datetime2(3) NULL,
        STATUS varchar(20) NOT NULL,
        TABLE_COUNT int NOT NULL CONSTRAINT DF_load_batch_table_count DEFAULT 0,
        SOURCE_ROW_COUNT bigint NOT NULL CONSTRAINT DF_load_batch_row_count DEFAULT 0,
        ERROR_MESSAGE nvarchar(2000) NULL,
        CONSTRAINT PK_load_batch PRIMARY KEY (LOAD_BATCH_ID),
        CONSTRAINT CK_load_batch_mode CHECK (LOAD_MODE IN ('initial','delta')),
        CONSTRAINT CK_load_batch_status CHECK
            (STATUS IN ('RUNNING','SUCCEEDED','FAILED'))
    );
END;
GO
