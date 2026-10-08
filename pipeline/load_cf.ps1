# Загружает .cf релиза в основную конфигурацию рабочей базы объединением. Конфигурацию базы данных не обновляет.
#
# Конфигуратор в пакетном режиме: /MergeCfg <файл> -Settings merge-all.xml -force. По настройкам объединения
# все объекты берутся из файла, объекты, которых в файле нет, удаляются; объекты без расхождений не трогаются.
# /UpdateDBCfg не выполняется: изменения проверяются в Конфигураторе и применяются вручную. Отказаться от них -
# "Конфигурация - Конфигурация базы данных - Вернуться к конфигурации БД".
#
#   powershell -File load_cf.ps1 -Cf D:\CF\3.20\system_3.20.cf -Platform "C:\Program Files\1cv8\8.3.27.2074\bin" -Server "server\base"
param(
    [Parameter(Mandatory = $true)][string]$Cf,
    [Parameter(Mandatory = $true)][string]$Platform,
    # Рабочая база в кластере: <сервер>\<база>.
    [string]$Server = $env:EF_PROD_IB,
    # Пользователь рабочей базы с правом администрирования - тот же, что у этапа Установка.
    [string]$User = $env:EF_IB_USER,
    [string]$Password = $env:EF_IB_PASSWORD,
    # Пусто - merge-all.xml рядом со скриптом ($PSScriptRoot в значениях параметров PowerShell 5.1 пуст).
    [string]$Settings = '',
    [string]$Log = 'build/release/load.log',
    [int]$TimeoutSec = 3600
)

$ErrorActionPreference = 'Stop'

if (-not $Server) { throw 'Не задана рабочая база: переменная EF_PROD_IB (<сервер>\<база>)' }
$designer = Join-Path $Platform '1cv8.exe'
if (-not (Test-Path $designer)) { throw "Нет Конфигуратора ${designer}: нужен клиент 1С той же версии, что кластер" }
$Cf = (Resolve-Path $Cf).Path
if (-not $Settings) { $Settings = Join-Path $PSScriptRoot 'merge-all.xml' }
$Settings = (Resolve-Path $Settings).Path
$Log = Join-Path (Get-Location) $Log
New-Item -ItemType Directory -Force (Split-Path $Log) | Out-Null
Remove-Item $Log -Force -ErrorAction SilentlyContinue

$arguments = @('DESIGNER', '/S', $Server)
if ($User) { $arguments += @('/N', $User) }
if ($Password) { $arguments += @('/P', $Password) }
$arguments += @('/DisableStartupDialogs', '/DisableStartupMessages', '/AppAutoCheckVersion-', '/Out', $Log,
    '/MergeCfg', $Cf, '-Settings', $Settings, '-force')
$line = ($arguments | ForEach-Object { if ($_ -match '\s' -or $_ -eq '') { '"' + $_ + '"' } else { $_ } }) -join ' '

# Командная строка не выводится: в ней пароль.
Write-Output "Объединение $Cf с основной конфигурацией $Server"
$process = Start-Process -FilePath $designer -ArgumentList $line -NoNewWindow -PassThru
$null = $process.Handle
if (-not $process.WaitForExit($TimeoutSec * 1000)) {
    $process.Kill()
    throw "Конфигуратор не завершился за $TimeoutSec с"
}
if (Test-Path $Log) { Get-Content $Log }
if ($process.ExitCode -ne 0) { throw "Конфигуратор завершился с кодом $($process.ExitCode)" }

Write-Output "Загружено в основную конфигурацию ${Server}: $Cf"
Write-Output 'Конфигурация базы данных не обновлялась: проверить и обновить в Конфигураторе.'
