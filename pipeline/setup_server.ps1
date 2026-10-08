# Готовит сервер раннера к пайплайну исправлений: рабочие каталоги и автономный сервер для сборки .cfe.
# Запускать один раз от администратора. Повторный запуск безопасен: существующее не пересоздается.
#
#   powershell -ExecutionPolicy Bypass -File setup_server.ps1 -Platform "C:\Program Files\1cv8\8.3.27.2074\bin"
#
# Автономный сервер нужен только для скорости: без него build_cfe.ps1 собирает во временной базе.
# База у сервера пустая и без пользователей - ibcmd импортирует расширение, не сверяя его с конфигурацией,
# поэтому конфигурация в ней не нужна. Сервер слушает только localhost.
param(
    [Parameter(Mandatory = $true)][string]$Platform,
    [string]$Root = 'D:\cicd',
    [int]$Port = 18430,
    [string]$Service = 'release-ibsrv'
)

$ErrorActionPreference = 'Stop'

$data = Join-Path $Root 'ibsrv'
$db = Join-Path $data 'db'
foreach ($dir in (Join-Path $Root 'mirror'), $data) {
    New-Item -ItemType Directory -Force $dir | Out-Null
}

$ibcmd = Join-Path $Platform 'ibcmd.exe'
$ibsrv = Join-Path $Platform 'ibsrv.exe'
if (-not (Test-Path (Join-Path $db '1Cv8.1CD'))) {
    & $ibcmd infobase create "--db-path=$db" "--data=$data"
    if ($LASTEXITCODE -ne 0) { throw "ibcmd infobase create: $LASTEXITCODE" }
}

# Шлюз прямого подключения выключен: иначе ibsrv занимает порт 1541 и конфликтует с агентом кластера.
$config = Join-Path $data 'config.yaml'
$yaml = @"
server:
  address: "localhost"
  port: $Port
database:
  path: '$db'
features:
  direct-gate: "no"
"@
[IO.File]::WriteAllText($config, $yaml, (New-Object Text.UTF8Encoding $false))

$binPath = "`"$ibsrv`" --service --data=`"$data`" --config=`"$config`""
$existing = Get-CimInstance Win32_Service -Filter "Name='$Service'"
if ($existing -and $existing.PathName -ne $binPath) {
    Stop-Service $Service -ErrorAction SilentlyContinue
    & sc.exe delete $Service | Out-Null
    Start-Sleep 2
    $existing = $null
}
if (-not $existing) {
    # New-Service, а не sc.exe create: PowerShell 5.1 портит кавычки внутри аргументов внешних программ.
    New-Service -Name $Service -BinaryPathName $binPath -StartupType Automatic `
        -DisplayName 'release: автономный сервер сборки расширений' | Out-Null
}
Start-Service $Service
Get-Service $Service | Format-Table Name, Status, StartType -AutoSize
Write-Output "Каталог данных сервера для EF_SERVER_DATA: $data"
