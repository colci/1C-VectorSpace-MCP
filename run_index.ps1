<#
.SYNOPSIS
Скрипт для запуска индексации конфигурации 1С в векторную БД Qdrant.
#>
param (
    [string]$Filter,
    [int]$Threads,
    [int]$BatchSize,
    [switch]$CleanCache
)

# Установка кодировки UTF-8 для корректного вывода кириллицы
$OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

# Пути
$PYTHON = ".venv/Scripts/python.exe"
$SCRIPT = "index_config.py"
$CACHE_FILE = "indexing_cache.json"

# 1. Проверка виртуального окружения
if (-not (Test-Path $PYTHON)) {
    Write-Error "Виртуальное окружение не найдено. Убедитесь, что папка .venv существует в каталоге проекта."
    Exit 1
}

# 2. Очистка кэша при необходимости
if ($CleanCache) {
    # Удаляем все кэш-файлы для разных моделей
    Get-ChildItem -Filter "indexing_cache_*.json" | Remove-Item -Force
    if (Test-Path $CACHE_FILE) {
        Remove-Item $CACHE_FILE -Force
    }
    Write-Host ("[$(Get-Date -Format 'HH:mm:ss')] Файлы кэша очищены.") -ForegroundColor Yellow
}

# 3. Настройка переменных окружения
$env:NO_PROXY = "localhost,127.0.0.1"

if ($Filter) {
    $env:INDEX_FILTER = $Filter
    Write-Host ("[$(Get-Date -Format 'HH:mm:ss')] Установлен фильтр индексации: $Filter") -ForegroundColor Cyan
} else {
    $env:INDEX_FILTER = $null
}

if ($Threads -gt 0) {
    $env:FASTEMBED_THREADS = $Threads
    Write-Host ("[$(Get-Date -Format 'HH:mm:ss')] Лимит потоков CPU: $Threads") -ForegroundColor Cyan
} else {
    $env:FASTEMBED_THREADS = $null
}

if ($BatchSize -gt 0) {
    $env:INDEX_BATCH_SIZE = $BatchSize
    Write-Host ("[$(Get-Date -Format 'HH:mm:ss')] Размер батча: $BatchSize чанков") -ForegroundColor Cyan
} else {
    $env:INDEX_BATCH_SIZE = $null
}

Write-Host ("[$(Get-Date -Format 'HH:mm:ss')] Запуск скрипта индексации...") -ForegroundColor Green

# 4. Запуск скрипта
& $PYTHON $SCRIPT

if ($LASTEXITCODE -eq 0) {
    Write-Host ("[$(Get-Date -Format 'HH:mm:ss')] Процесс индексации успешно завершен!") -ForegroundColor Green
} else {
    Write-Warning ("[$(Get-Date -Format 'HH:mm:ss')] Произошла ошибка во время индексации. Код возврата: $LASTEXITCODE")
}
