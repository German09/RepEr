# Собирает .cfe из XML-исходников расширения и упаковывает его в архив исправления.
#
# Основной режим - через работающий автономный сервер (ibsrv): ibcmd передает ему одну операцию
# "импорт XML с записью в файл", она занимает доли секунды и не оставляет расширение в базе.
# Сервер задается каталогом его данных (-ServerData, процесс ibsrv ищется по командной строке),
# идентификатором процесса (-ServerPid) либо адресом шлюза администрирования (-Remote).
#
# Если сервер не задан, расширение собирается во временной пустой файловой базе.
#
#   powershell -File ci/ef/build_cfe.ps1 -EfDir build/ef/EF_9731 -Platform "C:\Program Files\1cv8\8.3.27.2325\bin" -ServerData C:\cicd\ibsrv
param(
    [Parameter(Mandatory = $true)][string]$EfDir,
    [Parameter(Mandatory = $true)][string]$Platform,
    [string]$ServerData = $env:EF_SERVER_DATA,
    [string]$ServerPid = $env:EF_SERVER_PID,
    [string]$Remote = $env:EF_SERVER_REMOTE,
    # Учетные данные базы сервера сборки, не рабочей базы: EF_IB_USER и EF_IB_PASSWORD - пользователь рабочей базы
    # для этапа Установка, с ними ibcmd на пустой базе сервера завершается с ошибкой.
    [string]$User = $env:EF_SERVER_USER,
    [string]$Password = $env:EF_SERVER_PASSWORD,
    [int]$TimeoutSec = 300
)

$ErrorActionPreference = 'Stop'

$EfDir = (Resolve-Path $EfDir).Path
$ibcmd = Join-Path $Platform 'ibcmd.exe'
$info = Get-Content (Join-Path $EfDir 'ef.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$name = $info.name
$cfe = Join-Path $EfDir "$name.cfe"
$zip = Join-Path $EfDir "$name.zip"
Remove-Item $cfe, $zip -Force -ErrorAction SilentlyContinue

# ibcmd при нехватке прав запрашивает имя пользователя с клавиатуры. Пустой стандартный ввод
# и ограничение по времени не дают шагу CI зависнуть на таком запросе.
$stdin = Join-Path $EfDir 'stdin.empty'
Set-Content -Path $stdin -Value '' -Encoding ASCII

function Invoke-Ibcmd {
    param([string[]]$Arguments)
    $line = ($Arguments | ForEach-Object { if ($_ -match '\s') { '"' + $_ + '"' } else { $_ } }) -join ' '
    $process = Start-Process -FilePath $ibcmd -ArgumentList $line -NoNewWindow -PassThru -RedirectStandardInput $stdin
    $null = $process.Handle
    if (-not $process.WaitForExit($TimeoutSec * 1000)) {
        $process.Kill()
        throw "ibcmd $($Arguments[0..2] -join ' ') не завершился за $TimeoutSec с"
    }
    if ($process.ExitCode -ne 0) {
        throw "ibcmd $($Arguments[0..2] -join ' ') завершился с кодом $($process.ExitCode)"
    }
}

$work = $null
if ($Remote) {
    $connection = @("--remote=$Remote")
    $mode = "автономный сервер $Remote"
}
elseif ($ServerData -or $ServerPid) {
    if (-not $ServerPid) {
        # Файл lock.pid в каталоге данных сервер держит открытым монопольно, прочитать его нельзя.
        $data = (Resolve-Path $ServerData).Path.TrimEnd('')
        $server = Get-CimInstance Win32_Process -Filter "Name='ibsrv.exe'" |
            Where-Object { $_.CommandLine -and $_.CommandLine.Replace('/', '') -like "*$data*" } |
            Select-Object -First 1
        if (-not $server) { throw "Автономный сервер с каталогом данных $data не запущен" }
        $ServerPid = $server.ProcessId
    }
    $connection = @("--pid=$ServerPid")
    $mode = "автономный сервер, процесс $ServerPid"
}
else {
    $work = Join-Path $EfDir 'ib'
    if (Test-Path $work) { Remove-Item $work -Recurse -Force }
    New-Item -ItemType Directory -Force (Join-Path $work 'db'), (Join-Path $work 'data') | Out-Null
    $connection = @("--db-path=$(Join-Path $work 'db')", "--data=$(Join-Path $work 'data')")
    $mode = 'временная файловая база'
    Invoke-Ibcmd (@('infobase', 'create') + $connection)
}
if ($User) { $connection += @("--user=$User", "--password=$Password") }

try {
    Invoke-Ibcmd (@('infobase', 'config', 'import') + $connection + @("--extension=$name", "--out=$cfe", (Join-Path $EfDir 'xml')))
}
finally {
    if ($work) { Remove-Item $work -Recurse -Force -ErrorAction SilentlyContinue }
    Remove-Item $stdin -Force -ErrorAction SilentlyContinue
}
if (-not (Test-Path $cfe)) { throw "ibcmd не создал файл $cfe" }

# Архив в формате исправлений БСП: файл расширения и Manifest.xml в корне.
Compress-Archive -Path $cfe, (Join-Path $EfDir 'Manifest.xml') -DestinationPath $zip

Write-Output "Собрано ($mode): $cfe"
Write-Output "Архив: $zip"
