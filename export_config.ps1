# Установка кодировки UTF-8 для корректного вывода кириллицы
$OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

# Настройки путей (настройте под вашу систему)
$DBPATH = "D:\Base\1C"
$EXPORTPATH = "D:\Export\1C"
$USER = "Администратор"
$PASSWORD = ""

# 1. Автоматический поиск самой свежей версии 1С
$ProgramFiles = [System.Environment]::GetFolderPath('ProgramFiles')
$1cDir = "$ProgramFiles\1cv8"
$IBCMD = $null

if (Test-Path $1cDir) {
    # Ищем папки формата 8.3.*, сортируем по убыванию (свежие вверху)
    $latestVersionDir = Get-ChildItem -Path $1cDir -Directory -Filter "8.3.*" | 
                        Sort-Object Name -Descending | 
                        Select-Object -First 1

    if ($latestVersionDir) {
        $IBCMD = Join-Path $latestVersionDir.FullName "bin\ibcmd.exe"
    }
}

# Если автоопределение не сработало, используем жестко заданный путь
if (-not $IBCMD -or -not (Test-Path $IBCMD)) {
    $IBCMD = "C:\Program Files\1cv8\8.3.27.1859\bin\ibcmd.exe"
}

# Проверка физического наличия ibcmd
if (-not (Test-Path $IBCMD)) {
    Write-Error "Не найден ibcmd.exe. Проверьте правильность путей установки 1С."
    Pause
    Exit 1
}

# Создаем папку экспорта если её нет
if (-not (Test-Path $EXPORTPATH)) {
    New-Item -ItemType Directory -Force -Path $EXPORTPATH | Out-Null
}

Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Используется ibcmd из: $IBCMD" -ForegroundColor Cyan
Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Запуск синхронизации конфигурации..." -ForegroundColor Green

# Запуск экспорта с флагом --sync для инкрементального обновления
& $IBCMD config export `
  --db-path=$DBPATH `
  --user=$USER `
  --password=$PASSWORD `
  --threads=4 `
  --sync `
  $EXPORTPATH

if ($LASTEXITCODE -eq 0) {
    Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Синхронизация успешно завершена!" -ForegroundColor Green
} else {
    Write-Warning "[$(Get-Date -Format 'HH:mm:ss')] Произошла ошибка во время выгрузки. Код ошибки: $LASTEXITCODE"
}

Read-Host -Prompt "Нажмите Enter для выхода"
