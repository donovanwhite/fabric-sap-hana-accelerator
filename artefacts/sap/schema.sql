-- T-SQL for the explicitly synthetic Fabric SQL/SQL Server source simulator.
-- Never execute this file against customer SAP HANA or treat it as SAP DDL.
SET NOCOUNT ON;
SET XACT_ABORT ON;
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
        RTCUR varchar(5) NULL,
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

IF COL_LENGTH(N'SAPABAP1.ACDOCA', N'RTCUR') IS NULL
    ALTER TABLE SAPABAP1.ACDOCA ADD RTCUR varchar(5) NULL;
GO

-- Only the original equal-currency simulator seed can use this backfill.
UPDATE SAPABAP1.ACDOCA SET RTCUR = RWCUR WHERE RTCUR IS NULL;
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
        CPUDT varchar(8) NULL,
        CPUTM varchar(6) NULL,
        BLART varchar(2) NULL,
        STBLG varchar(10) NULL,
        STJAH varchar(4) NULL,
        BSTAT varchar(1) NULL,
        BKTXT nvarchar(100) NULL,
        CONSTRAINT PK_BKPF PRIMARY KEY (MANDT, BUKRS, GJAHR, BELNR)
    );
END;
GO

IF COL_LENGTH(N'SAPABAP1.BKPF', N'CPUDT') IS NULL
    ALTER TABLE SAPABAP1.BKPF ADD CPUDT varchar(8) NULL;
IF COL_LENGTH(N'SAPABAP1.BKPF', N'CPUTM') IS NULL
    ALTER TABLE SAPABAP1.BKPF ADD CPUTM varchar(6) NULL;
IF COL_LENGTH(N'SAPABAP1.BKPF', N'STJAH') IS NULL
    ALTER TABLE SAPABAP1.BKPF ADD STJAH varchar(4) NULL;
IF COL_LENGTH(N'SAPABAP1.BKPF', N'BSTAT') IS NULL
    ALTER TABLE SAPABAP1.BKPF ADD BSTAT varchar(1) NULL;
GO

-- Deterministic entry-time backfill for the original 20260928 snapshot.
-- This does not claim that posting date BUDAT is an entry/change watermark,
-- nor does it reconstruct authentic SAP entry history.
UPDATE SAPABAP1.BKPF
SET CPUDT = COALESCE(CPUDT, '20260928'),
    CPUTM = COALESCE(CPUTM, '000000'),
    STJAH = COALESCE(STJAH, '0000');
GO

-- Preserve the seed's legacy STBLG values, including unresolved references.
-- Infer a year only when the target number identifies exactly one document
-- within the same client/company; do not invent reciprocal reversal pairs.
UPDATE header
SET STJAH = reversal.GJAHR
FROM SAPABAP1.BKPF AS header
INNER JOIN (
    SELECT MANDT, BUKRS, BELNR, MIN(GJAHR) AS GJAHR
    FROM SAPABAP1.BKPF
    GROUP BY MANDT, BUKRS, BELNR
    HAVING COUNT_BIG(*) = 1
) AS reversal
    ON reversal.MANDT = header.MANDT
    AND reversal.BUKRS = header.BUKRS
    AND reversal.BELNR = header.STBLG
WHERE header.STJAH = '0000';
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
        DATBI varchar(8) NOT NULL,
        CONSTRAINT PK_CSKS PRIMARY KEY (MANDT, KOKRS, KOSTL, DATBI)
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
        DATBI varchar(8) NOT NULL,
        CONSTRAINT PK_CEPC PRIMARY KEY (MANDT, KOKRS, PRCTR, DATBI)
    );
END;
GO

-- Preserve every existing master row. Only the known old/current simulator
-- key shapes are accepted; unexpected custom keys cause a rollback, not loss.
BEGIN TRY
    BEGIN TRANSACTION;

    DECLARE @master_tables TABLE (table_name sysname, entity_column sysname);
    INSERT INTO @master_tables (table_name, entity_column)
    VALUES (N'CSKS', N'KOSTL'), (N'CEPC', N'PRCTR');

    DECLARE @master_table sysname, @entity_column sysname;
    DECLARE @master_object_id int, @pk_index_id int, @pk_name sysname;
    DECLARE @current_keys nvarchar(max), @old_keys nvarchar(200);
    DECLARE @expected_keys nvarchar(200), @master_sql nvarchar(max);
    DECLARE synthetic_master_keys CURSOR LOCAL FAST_FORWARD FOR
        SELECT table_name, entity_column FROM @master_tables;
    OPEN synthetic_master_keys;
    FETCH NEXT FROM synthetic_master_keys INTO @master_table, @entity_column;
    WHILE @@FETCH_STATUS = 0
    BEGIN
        SET @master_object_id = OBJECT_ID(N'SAPABAP1.' + @master_table, N'U');
        SET @pk_name = NULL;
        SET @pk_index_id = NULL;
        SELECT @pk_name = kc.name, @pk_index_id = kc.unique_index_id
        FROM sys.key_constraints AS kc
        WHERE kc.parent_object_id = @master_object_id AND kc.type = 'PK';

        SELECT @current_keys =
            STRING_AGG(CONVERT(nvarchar(max), c.name), N',')
            WITHIN GROUP (ORDER BY ic.key_ordinal)
        FROM sys.index_columns AS ic
        INNER JOIN sys.columns AS c
            ON c.object_id = ic.object_id AND c.column_id = ic.column_id
        WHERE ic.object_id = @master_object_id
            AND ic.index_id = @pk_index_id AND ic.key_ordinal > 0;
        SET @old_keys = N'MANDT,KOKRS,' + @entity_column;
        SET @expected_keys = @old_keys + N',DATBI';
        IF @current_keys IS NOT NULL
            AND @current_keys <> @old_keys AND @current_keys <> @expected_keys
            THROW 51001, 'Unexpected simulator master primary key; migration stopped.', 1;

        SET @master_sql = N'
            UPDATE SAPABAP1.' + QUOTENAME(@master_table) + N'
            SET DATBI = ''99991231''
            WHERE DATBI IS NULL OR LTRIM(RTRIM(DATBI)) IN ('''', ''00000000'');
            IF EXISTS (
                SELECT 1 FROM SAPABAP1.' + QUOTENAME(@master_table) + N'
                GROUP BY MANDT, KOKRS, ' + QUOTENAME(@entity_column) + N', DATBI
                HAVING COUNT_BIG(*) > 1
            )
                THROW 51002, ''Duplicate simulator validity keys; migration stopped.'', 1;';
        EXEC sys.sp_executesql @master_sql;

        IF EXISTS (
            SELECT 1 FROM sys.columns
            WHERE object_id = @master_object_id AND name = N'DATBI'
                AND is_nullable = 1
        )
        BEGIN
            SET @master_sql = N'ALTER TABLE SAPABAP1.' + QUOTENAME(@master_table)
                + N' ALTER COLUMN DATBI varchar(8) NOT NULL;';
            EXEC sys.sp_executesql @master_sql;
        END;
        IF @current_keys IS NULL OR @current_keys = @old_keys
        BEGIN
            IF @pk_name IS NOT NULL
            BEGIN
                SET @master_sql = N'ALTER TABLE SAPABAP1.' + QUOTENAME(@master_table)
                    + N' DROP CONSTRAINT ' + QUOTENAME(@pk_name) + N';';
                EXEC sys.sp_executesql @master_sql;
            END;
            SET @master_sql = N'ALTER TABLE SAPABAP1.' + QUOTENAME(@master_table)
                + N' ADD CONSTRAINT ' + QUOTENAME(N'PK_' + @master_table)
                + N' PRIMARY KEY (MANDT, KOKRS, ' + QUOTENAME(@entity_column) + N', DATBI);';
            EXEC sys.sp_executesql @master_sql;
        END;
        FETCH NEXT FROM synthetic_master_keys INTO @master_table, @entity_column;
    END;
    CLOSE synthetic_master_keys;
    DEALLOCATE synthetic_master_keys;
    COMMIT TRANSACTION;
END TRY
BEGIN CATCH
    IF CURSOR_STATUS('local', 'synthetic_master_keys') > -1
        CLOSE synthetic_master_keys;
    IF CURSOR_STATUS('local', 'synthetic_master_keys') >= -1
        DEALLOCATE synthetic_master_keys;
    IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION;
    THROW;
END CATCH;
GO

IF OBJECT_ID(N'SAPABAP1.SKA1', N'U') IS NULL
BEGIN
    CREATE TABLE SAPABAP1.SKA1
    (
        MANDT varchar(3) NOT NULL,
        KTOPL varchar(4) NOT NULL,
        SAKNR varchar(10) NOT NULL,
        XBILK varchar(1) NULL,
        GVTYP varchar(30) NULL,
        KTOKS varchar(4) NULL,
        CONSTRAINT PK_SKA1 PRIMARY KEY (MANDT, KTOPL, SAKNR)
    );
END;
GO

IF COL_LENGTH(N'SAPABAP1.SKA1', N'GVTYP') < 30
    ALTER TABLE SAPABAP1.SKA1 ALTER COLUMN GVTYP varchar(30) NULL;
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
        CONSTRAINT CK_load_batch_mode CHECK (LOAD_MODE IN ('initial','delta','scenario')),
        CONSTRAINT CK_load_batch_status CHECK
            (STATUS IN ('RUNNING','SUCCEEDED','FAILED'))
    );
END;
GO

BEGIN TRY
    BEGIN TRANSACTION;
    IF EXISTS (
        SELECT 1 FROM sys.check_constraints
        WHERE parent_object_id = OBJECT_ID(N'etl.load_batch')
            AND name = N'CK_load_batch_mode'
            AND definition NOT LIKE N'%''scenario''%'
    )
        ALTER TABLE etl.load_batch DROP CONSTRAINT CK_load_batch_mode;

    IF NOT EXISTS (
        SELECT 1 FROM sys.check_constraints
        WHERE parent_object_id = OBJECT_ID(N'etl.load_batch')
            AND name = N'CK_load_batch_mode'
    )
        ALTER TABLE etl.load_batch ADD CONSTRAINT CK_load_batch_mode
            CHECK (LOAD_MODE IN ('initial','delta','scenario'));
    COMMIT TRANSACTION;
END TRY
BEGIN CATCH
    IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION;
    THROW;
END CATCH;
GO

-- The scenario CLI also requires --synthetic-target to equal DB_NAME().
-- This marker attests only that this synthetic schema was explicitly installed.
IF NOT EXISTS (
    SELECT 1 FROM sys.extended_properties
    WHERE class = 0 AND major_id = 0 AND minor_id = 0
        AND name = N'SAP_FINANCE_SYNTHETIC_SIMULATOR'
)
    EXEC sys.sp_addextendedproperty
        @name = N'SAP_FINANCE_SYNTHETIC_SIMULATOR', @value = N'1';
GO
