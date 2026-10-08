# Держит на раннере зеркало репозитория конфигурации и подтягивает в него свежие коммиты и теги.
# Генератору и проверке нужна только история git, рабочая копия не нужна, поэтому зеркало голое:
# первый раз клонируется целиком, дальше - только fetch.
#
# Токен задания подставляется в адрес только на время fetch и в настройках зеркала не остается.
param(
    [Parameter(Mandatory = $true)][string]$Url,
    [Parameter(Mandatory = $true)][string]$Mirror,
    [string]$Token = $env:CI_JOB_TOKEN
)

$ErrorActionPreference = 'Stop'

$authUrl = $Url
if ($Token) { $authUrl = $Url -replace '^https://', "https://gitlab-ci-token:$Token@" }

function Invoke-Git {
    & git @args
    if ($LASTEXITCODE -ne 0) { throw "git $($args[0]) завершился с кодом $LASTEXITCODE" }
}

# Зеркало могли положить руками под другой учетной записью, а задание идет под учетной записью
# службы раннера: без safe.directory git откажется работать с "чужим" каталогом.
$safe = $Mirror -replace '\\', '/'
if (@(& git config --global --get-all safe.directory) -notcontains $safe) {
    Invoke-Git config --global --add safe.directory $safe
}

if (-not (Test-Path (Join-Path $Mirror 'HEAD'))) {
    New-Item -ItemType Directory -Force (Split-Path $Mirror) | Out-Null
    Invoke-Git clone --mirror --quiet $authUrl $Mirror
    Invoke-Git -C $Mirror remote set-url origin $Url
}
else {
    try {
        Invoke-Git -C $Mirror remote set-url origin $authUrl
        # Ветки забираются как есть: у голого клона, в отличие от --mirror, этой настройки нет.
        Invoke-Git -C $Mirror config remote.origin.fetch '+refs/heads/*:refs/heads/*'
        Invoke-Git -C $Mirror fetch --prune --tags --quiet origin
    }
    finally {
        & git -C $Mirror remote set-url origin $Url
    }
}
Write-Output "Зеркало $Mirror обновлено"
