param(
    [Parameter(Mandatory = $true)]
    [string]$Workspace,
    [string]$SqlConnection = "conn_sap_finance_sql",
    [string]$PlanningSqlConnection = "conn_sap_finance_planning_sql",
    [string]$SemanticModelConnection = "conn_sap_finance_planning_dataset",
    [switch]$Overwrite,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$deploymentRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$arguments = @(
    (Join-Path $deploymentRoot "deploy.py"),
    "--workspace",
    $Workspace,
    "--sql-connection",
    $SqlConnection,
    "--planning-sql-connection",
    $PlanningSqlConnection,
    "--semantic-model-connection",
    $SemanticModelConnection
)

if ($Overwrite) {
    $arguments += "--overwrite"
}
if ($DryRun) {
    $arguments += "--dry-run"
}

& python @arguments
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
