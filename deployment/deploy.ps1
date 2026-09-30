param(
    [Parameter(Mandatory = $true)]
    [string]$Workspace,
    [switch]$Overwrite,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$deploymentRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$arguments = @(
    (Join-Path $deploymentRoot "deploy.py"),
    "--workspace",
    $Workspace
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
