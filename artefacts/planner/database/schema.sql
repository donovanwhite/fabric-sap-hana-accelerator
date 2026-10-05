SET NOCOUNT ON;
GO

IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = N'plan')
    EXEC(N'CREATE SCHEMA [plan] AUTHORIZATION dbo');
GO

IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = N'audit')
    EXEC(N'CREATE SCHEMA audit AUTHORIZATION dbo');
GO

IF OBJECT_ID(N'[plan].[scenario]', N'U') IS NULL
BEGIN
    CREATE TABLE [plan].[scenario]
    (
        SCENARIO_CODE varchar(30) NOT NULL,
        SCENARIO_NAME nvarchar(100) NOT NULL,
        SCENARIO_TYPE varchar(20) NOT NULL,
        PLAN_VERSION varchar(20) NOT NULL,
        STATUS varchar(20) NOT NULL,
        FISCAL_YEAR_FROM int NOT NULL,
        FISCAL_YEAR_TO int NOT NULL,
        OWNER_UPN nvarchar(256) NULL,
        CREATED_UTC datetime2(3) NOT NULL
            CONSTRAINT DF_scenario_created_utc DEFAULT SYSUTCDATETIME(),
        UPDATED_UTC datetime2(3) NOT NULL
            CONSTRAINT DF_scenario_updated_utc DEFAULT SYSUTCDATETIME(),
        CONSTRAINT PK_scenario PRIMARY KEY (SCENARIO_CODE),
        CONSTRAINT CK_scenario_type
            CHECK (SCENARIO_TYPE IN ('BUDGET', 'FORECAST', 'PLAN', 'WHAT_IF')),
        CONSTRAINT CK_scenario_status
            CHECK (STATUS IN ('DRAFT', 'SUBMITTED', 'APPROVED', 'LOCKED')),
        CONSTRAINT CK_scenario_year_range
            CHECK (FISCAL_YEAR_FROM BETWEEN 1900 AND 2100
                AND FISCAL_YEAR_TO BETWEEN FISCAL_YEAR_FROM AND 2100)
    );
END;
GO

IF OBJECT_ID(N'[plan].[assumption]', N'U') IS NULL
BEGIN
    CREATE TABLE [plan].[assumption]
    (
        SCENARIO_CODE varchar(30) NOT NULL,
        COMPANY_CODE varchar(4) NOT NULL,
        FISCAL_YEAR int NOT NULL,
        FISCAL_PERIOD int NOT NULL,
        ASSUMPTION_CODE varchar(50) NOT NULL,
        ASSUMPTION_VALUE decimal(23,6) NULL,
        ASSUMPTION_TEXT nvarchar(1000) NULL,
        OWNER_UPN nvarchar(256) NULL,
        UPDATED_UTC datetime2(3) NOT NULL
            CONSTRAINT DF_assumption_updated_utc DEFAULT SYSUTCDATETIME(),
        CONSTRAINT PK_assumption PRIMARY KEY
            (SCENARIO_CODE, COMPANY_CODE, FISCAL_YEAR,
             FISCAL_PERIOD, ASSUMPTION_CODE),
        CONSTRAINT FK_assumption_scenario FOREIGN KEY (SCENARIO_CODE)
            REFERENCES [plan].[scenario] (SCENARIO_CODE),
        CONSTRAINT CK_assumption_fiscal_year
            CHECK (FISCAL_YEAR BETWEEN 1900 AND 2100),
        CONSTRAINT CK_assumption_fiscal_period
            CHECK (FISCAL_PERIOD BETWEEN 1 AND 16),
        CONSTRAINT CK_assumption_value
            CHECK (ASSUMPTION_VALUE IS NOT NULL OR ASSUMPTION_TEXT IS NOT NULL)
    );
END;
GO

IF OBJECT_ID(N'audit.planning_release', N'U') IS NULL
BEGIN
    CREATE TABLE audit.planning_release
    (
        RELEASE_ID uniqueidentifier NOT NULL
            CONSTRAINT DF_planning_release_id DEFAULT NEWID(),
        SCENARIO_CODE varchar(30) NOT NULL,
        RELEASE_STATUS varchar(20) NOT NULL,
        WRITEBACK_TABLE nvarchar(256) NOT NULL,
        WRITEBACK_ID nvarchar(100) NULL,
        ROW_COUNT bigint NULL,
        RELEASED_BY_UPN nvarchar(256) NULL,
        RELEASED_UTC datetime2(3) NOT NULL
            CONSTRAINT DF_planning_release_utc DEFAULT SYSUTCDATETIME(),
        NOTES nvarchar(2000) NULL,
        CONSTRAINT PK_planning_release PRIMARY KEY (RELEASE_ID),
        CONSTRAINT FK_planning_release_scenario FOREIGN KEY (SCENARIO_CODE)
            REFERENCES [plan].[scenario] (SCENARIO_CODE),
        CONSTRAINT CK_planning_release_status
            CHECK (RELEASE_STATUS IN ('STARTED', 'SUCCEEDED', 'FAILED'))
    );
END;
GO
