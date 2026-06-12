# Установка кодировки UTF-8 для корректного вывода кириллицы
System.Text.ASCIIEncoding = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

<#
.SYNOPSIS
Скрипт для запуска индексации конфигурации 1С в векторную БД Qdrant.
#>

param (
    [string] = '',
    [int] = 0,
    [int] = 0,
    [switch]
)

# Пути
 = '.\\.venv\\Scripts\\python.exe'
 = 'index_config.py'
 = 'indexing_cache.json'

# 1. Проверка виртуального окружения
if (-not (Test-Path )) {
    Write-Error 'Виртуальное окружение не найдено. Убедитесь, что папка .venv существует в каталоге проекта.'
    Exit 1
}

# 2. Очистка кэша при необходимости
if () {
    if (Test-Path ) {
        Write-Host '[] Очистка файла кэша ()...' -ForegroundColor Yellow
        Remove-Item  -Force
    } else {
        Write-Host '[] Файл кэша не найден, очистка не требуется.' -ForegroundColor Gray
    }
}

# 3. Настройка переменных окружения
 = 'localhost,127.0.0.1'

if () {
     = 
    Write-Host '[] Установлен фильтр индексации: ' -ForegroundColor Cyan
} else {
     = 
}

if ( -gt 0) {
     = 
    Write-Host '[] Лимит потоков CPU: ' -ForegroundColor Cyan
} else {
     = 
}

if ( -gt 0) {
     = 
    Write-Host '[] Размер батча:  чанков' -ForegroundColor Cyan
} else {
     = 
}

Write-Host '[] Запуск скрипта индексации...' -ForegroundColor Green

# 4. Запуск скрипта
&  

if ( -eq 0) {
    Write-Host '[] Процесс индексации успешно завершен!' -ForegroundColor Green
} else {
    Write-Warning '[] Произошла ошибка во время индексации. Код возврата: '
}
