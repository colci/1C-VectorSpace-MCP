param(
    [Parameter(Mandatory = $true)]
    [string]$VolumeName,

    [Parameter(Mandatory = $true)]
    [string]$BackupFile,

    [string]$Image = "memgraph/memgraph:latest",

    [switch]$CreateMissing,

    [switch]$DryRun,

    [switch]$Force
)

$ErrorActionPreference = "Stop"
$OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$archivePath = (Resolve-Path -LiteralPath $BackupFile).Path
$archiveName = Split-Path -Leaf $archivePath
$archiveDir = Split-Path -Parent $archivePath

docker volume inspect $VolumeName *> $null
$volumeExists = $LASTEXITCODE -eq 0
if (-not $volumeExists) {
    if (-not $CreateMissing) {
        throw "Docker volume not found: $VolumeName. Pass -CreateMissing to create it during restore."
    }
    Write-Host "[restore] creating missing volume: $VolumeName"
    docker volume create $VolumeName *> $null
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to create Docker volume: $VolumeName"
    }
}

Write-Host "[restore] archive=$archivePath"
Write-Host "[restore] volume=$VolumeName"

if ($DryRun) {
    Write-Host "[restore] dry run only; no data was written"
    exit 0
}

if (-not $Force) {
    throw "Restore writes archive contents into the target volume. Re-run with -Force to continue."
}

docker run --rm `
    --entrypoint sh `
    -v "${VolumeName}:/target" `
    -v "${archiveDir}:/backup:ro" `
    $Image `
    -lc "cd /target && tar xzf /backup/$archiveName"

if ($LASTEXITCODE -ne 0) {
    throw "Restore failed for volume: $VolumeName"
}

Write-Host "[restore] success"
