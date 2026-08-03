param(
    [string[]]$VolumeName = @(
        "qdrant_storage",
        "1c-vectorspace-mcp_memgraph_data",
        "1c-vectorspace-mcp_memgraph_log"
    ),

    [string]$BackupDir = "backups/docker-volumes",

    [string]$Image = "memgraph/memgraph:latest"
)

$ErrorActionPreference = "Stop"
$OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$backupRoot = (Resolve-Path -LiteralPath (New-Item -ItemType Directory -Force -Path $BackupDir)).Path
$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"

foreach ($volume in $VolumeName) {
    docker volume inspect $volume *> $null
    if ($LASTEXITCODE -ne 0) {
        throw "Docker volume not found: $volume"
    }

    $safeVolumeName = $volume -replace '[^a-zA-Z0-9_.-]', '_'
    $archiveName = "${safeVolumeName}_${timestamp}.tar.gz"
    $archivePath = Join-Path $backupRoot $archiveName

    Write-Host "[backup] volume=$volume -> $archivePath"
    docker run --rm `
        --entrypoint sh `
        -v "${volume}:/source:ro" `
        -v "${backupRoot}:/backup" `
        $Image `
        -lc "cd /source && tar czf /backup/$archiveName ."

    if ($LASTEXITCODE -ne 0) {
        throw "Backup failed for volume: $volume"
    }
}

Write-Host "[backup] success"
