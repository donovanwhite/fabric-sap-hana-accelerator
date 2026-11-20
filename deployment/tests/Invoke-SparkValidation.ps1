#!/usr/bin/env pwsh
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT
#Requires -Version 7.0

<#
.SYNOPSIS
    Run isolated local Spark/Delta validation without changing the host Python or Java.
.DESCRIPTION
    Download pinned test dependencies through the configured host package index
    and verified HTTPS, then run bounded, network-disabled Linux containers.
    No Fabric, SAP, or production endpoints are called.
.PARAMETER Suite
    Select Recovery, Gold, Medallion, or all three suites.
.PARAMETER DependencyCache
    Local directory for pinned dependency downloads. Never stores credentials or source data.
.EXAMPLE
    .\deployment\tests\Invoke-SparkValidation.ps1 -Suite Recovery
.NOTES
    Requires a local Linux Docker engine, Python, curl, and PowerShell 7.
#>
[CmdletBinding()]
param(
    [ValidateSet('Recovery', 'Gold', 'Medallion', 'All')]
    [string]$Suite = 'All',
    [string]$DependencyCache = (Join-Path $env:LOCALAPPDATA 'fabric-sap-validation\delta-3.2.1')
)

$ErrorActionPreference = 'Stop'

if ($MyInvocation.InvocationName -ne '.') {
    $ContainerName = 'sap-finance-validation-' + [guid]::NewGuid().ToString('N')
    try {
        $Repository = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
        $Context = (& docker context show).Trim()
        if ($LASTEXITCODE -ne 0) { throw 'Cannot resolve the local Docker context.' }
        $Endpoint = (& docker context inspect --format '{{.Endpoints.docker.Host}}').Trim()
        if ($LASTEXITCODE -ne 0 -or $Endpoint -notmatch '^(npipe|unix):') {
            throw 'Validation requires a local Docker socket; remote Docker contexts are not used.'
        }
        $OperatingSystem = (& docker info --format '{{.OSType}}').Trim()
        if ($LASTEXITCODE -ne 0 -or $OperatingSystem -ne 'linux') {
            throw 'A running local Linux Docker engine is required.'
        }
        $null = New-Item -ItemType Directory -Force -Path $DependencyCache
        $DependencyCache = (Resolve-Path $DependencyCache).Path
        $Wheel = Join-Path $DependencyCache 'delta_spark-3.2.1-py3-none-any.whl'
        if (-not (Test-Path -LiteralPath $Wheel)) {
            & python -m pip download --quiet --only-binary=:all: --no-deps `
                --dest $DependencyCache delta-spark==3.2.1
            if ($LASTEXITCODE -ne 0) { throw 'Pinned Delta wheel download failed.' }
        }
        $Dependencies = @{
            'delta-spark_2.12-3.2.1.jar' = 'io/delta/delta-spark_2.12/3.2.1/delta-spark_2.12-3.2.1.jar'
            'delta-storage-3.2.1.jar' = 'io/delta/delta-storage/3.2.1/delta-storage-3.2.1.jar'
        }
        foreach ($Name in $Dependencies.Keys) {
            $Destination = Join-Path $DependencyCache $Name
            if (-not (Test-Path -LiteralPath $Destination)) {
                $Download = $Destination + '.download-' + [guid]::NewGuid().ToString('N')
                try {
                    & curl.exe --fail --silent --show-error --location `
                        ('https://repo.maven.apache.org/maven2/' + $Dependencies[$Name]) `
                        --output $Download
                    if ($LASTEXITCODE -ne 0) { throw "Pinned dependency download failed: $Name" }
                    Move-Item -LiteralPath $Download -Destination $Destination -Force
                }
                finally {
                    if (Test-Path -LiteralPath $Download) {
                        Remove-Item -LiteralPath $Download
                    }
                }
            }
        }
        $Image = (& docker build --builder $Context --quiet --target runtime `
            --file (Join-Path $PSScriptRoot 'Dockerfile.spark') $PSScriptRoot).Trim()
        if ($LASTEXITCODE -ne 0 -or $Image -notmatch '^sha256:[a-f0-9]+$') {
            throw 'Isolated Spark runtime build failed.'
        }
        $Scripts = switch ($Suite) {
            'Recovery' { @('spark_validation.py') }
            'Gold' { @('test_gold_incremental.py') }
            'Medallion' { @('spark_medallion_validation.py') }
            'All' { @('test_gold_incremental.py', 'spark_validation.py', 'spark_medallion_validation.py') }
        }
        $Commands = @(
            'python3 -m pip install --quiet --root-user-action=ignore --no-deps --no-index --target /tmp/sap-deps /validation-deps/delta_spark-3.2.1-py3-none-any.whl',
            'export PYTHONPATH="/tmp/sap-deps:$PYTHONPATH"'
        )
        foreach ($Script in $Scripts) {
            $Commands += '/opt/spark/bin/spark-submit --master "local[2]" --driver-memory 1g ' +
                '--jars /validation-deps/delta-spark_2.12-3.2.1.jar,/validation-deps/delta-storage-3.2.1.jar ' +
                '--conf spark.sql.extensions=io.delta.sql.DeltaSparkSessionExtension ' +
                '--conf spark.sql.catalog.spark_catalog=org.apache.spark.sql.delta.catalog.DeltaCatalog ' +
                '--conf spark.ui.enabled=false /workspace/deployment/tests/' + $Script
        }
        $Arguments = @(
            'run', '--rm', '--name', $ContainerName, '--hostname', 'localhost',
            '--network', 'none', '--cpus=2', '--memory=4g',
            '--env', 'GOLD_SPARK_TESTS=1',
            '--mount', "type=bind,source=$Repository,target=/workspace,readonly",
            '--mount', "type=bind,source=$DependencyCache,target=/validation-deps,readonly",
            '--entrypoint', '/bin/bash', $Image, '-lc', ($Commands -join ' && ')
        )
        & docker @Arguments
        if ($LASTEXITCODE -ne 0) { throw "Spark validation suite failed: $Suite" }
    }
    finally {
        $ValidationExitCode = $LASTEXITCODE
        & docker container inspect $ContainerName *> $null
        if ($LASTEXITCODE -eq 0) {
            & docker stop $ContainerName *> $null
        }
        $global:LASTEXITCODE = $ValidationExitCode
    }
}
