param(
    [Parameter(Mandatory = $true)]
    [string]$ConfigId,

    [int]$FastEmbedThreads = 4
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonExe = Join-Path $projectRoot ".venv\Scripts\python.exe"
$indexScript = Join-Path $projectRoot "index_config.py"

if (-not (Test-Path $pythonExe)) {
    throw "Python interpreter not found: $pythonExe"
}

if (-not (Test-Path $indexScript)) {
    throw "Indexer script not found: $indexScript"
}

$env:ACTIVE_CONFIG_ID = $ConfigId
$env:FASTEMBED_THREADS = [string]$FastEmbedThreads

Set-Location $projectRoot
& $pythonExe $indexScript
