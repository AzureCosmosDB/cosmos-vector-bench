[CmdletBinding()]
param(
    [ValidateSet('quantizedFlat', 'quantizedflat', 'diskANN', 'diskann')]
    [string]$IndexType = 'quantizedFlat',

    [ValidateSet('hpk', 'docid', 'sessionid')]
    [string]$PartitionKeyMode = 'docid',

    [string]$ResourceGroup = $env:resourceGroup,

    [string]$AccountName = $env:accountName
)

$ErrorActionPreference = 'Stop'

if (-not $ResourceGroup) {
    $callerResourceGroup = Get-Variable -Name resourceGroup -Scope 1 -ValueOnly -ErrorAction SilentlyContinue
    if ($callerResourceGroup) {
        $ResourceGroup = $callerResourceGroup
    }
}

if (-not $ResourceGroup) {
    throw 'Set $env:resourceGroup, set $resourceGroup, or pass -ResourceGroup <resource-group-name>.'
}

if (-not $AccountName) {
    $callerAccountName = Get-Variable -Name accountName -Scope 1 -ValueOnly -ErrorAction SilentlyContinue
    if ($callerAccountName) {
        $AccountName = $callerAccountName
    }
}

$repoRoot = Split-Path -Parent $PSScriptRoot
$normalizedIndexType = if ($IndexType -ieq 'diskANN') { 'diskANN' } else { 'quantizedFlat' }
if ($normalizedIndexType -eq 'diskANN' -and $PartitionKeyMode -ne 'docid') {
    throw 'hpk and sessionid partition-key variants are available for quantizedFlat only.'
}

$variantSuffix = switch ($PartitionKeyMode) {
    'hpk' { '-hpk' }
    'sessionid' { '-sessionid' }
    default { '-docid' }
}
if ($normalizedIndexType -eq 'diskANN') {
    $variantSuffix = ''
}
$containerSuffix = switch ($PartitionKeyMode) {
    'hpk' { '-hpk' }
    'sessionid' { '-sessionid' }
    default { '' }
}
$projectPath = '.\src_dotnet\CosmosVectorBench.csproj'

$scenarios = @(
    @{ Config = 1; BulkSize = 18; NumClients = 10; TotalDocs = 180000 },
    @{ Config = 2; BulkSize = 20; NumClients = 20; TotalDocs = 300000 },
    @{ Config = 3; BulkSize = 30; NumClients = 30; TotalDocs = 1200000 },
    @{ Config = 4; BulkSize = 40; NumClients = 10; TotalDocs = 400000 },
    @{ Config = 5; BulkSize = 10; NumClients = 40; TotalDocs = 90000 }
)

Push-Location $repoRoot
try {
    foreach ($scenario in $scenarios) {
        $config = $scenario.Config
        $paramFile = ".\scenarios\infra\config-$config-$normalizedIndexType$variantSuffix.bicepparam"
        $containerName = "s$config-$normalizedIndexType$containerSuffix"

        Write-Host ""
        Write-Host "=== OpenAI config $config ($normalizedIndexType) ==="
        Write-Host "Provisioning $containerName"
        $deploymentParameters = @($paramFile)
        if ($AccountName) {
            $deploymentParameters += "accountName=$AccountName"
        }
        az deployment group create --resource-group $ResourceGroup --parameters $deploymentParameters
        if ($LASTEXITCODE -ne 0) {
            exit $LASTEXITCODE
        }

        Write-Host "Running benchmark against $containerName"
        dotnet run --project $projectPath -c Release -- `
            --bulk-size $scenario['BulkSize'] `
            --num-clients $scenario['NumClients'] `
            --total-docs $scenario['TotalDocs'] `
            --data-path .\data\open_ai_corpus-initial-indexing.json `
            --container-name $containerName `
            --partition-key-mode $PartitionKeyMode

        if ($LASTEXITCODE -ne 0) {
            exit $LASTEXITCODE
        }
    }
}
finally {
    Pop-Location
}