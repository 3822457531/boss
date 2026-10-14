# 直聊小蜜 packager (PyInstaller + sidecar runtime + Inno Setup)
# Usage: powershell -ExecutionPolicy Bypass -File packaging\build.ps1 [-Version 1.0.3] [-LicenseServer https://your-license-server.example.com]

param(
  [string]$Version = "1.0.2",
  [string]$NodeVersion = "20.18.1",
  [string]$BossCliVersion = "0.6.6",
  [string]$LicenseServer = "https://your-license-server.example.com"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$AppId = "ZhiLiaoXiaoMi"

function Ensure-Dir([string]$Path) {
  if (-not (Test-Path $Path)) { New-Item -ItemType Directory -Path $Path | Out-Null }
}

Write-Host "==> Version $Version ($AppId)"
$verFile = Join-Path $Root "admin\version.py"
$verText = Get-Content $verFile -Raw -Encoding UTF8
$verText = [regex]::Replace($verText, 'CLIENT_VERSION\s*=\s*"[^"]*"', "CLIENT_VERSION = `"$Version`"")
Set-Content -Path $verFile -Value $verText -Encoding UTF8

$licDefault = ($LicenseServer -as [string]).Trim().TrimEnd("/")
if (-not $licDefault) { $licDefault = "https://your-license-server.example.com" }
Write-Host "==> Default license server: $licDefault"
$appPy = Join-Path $Root "admin\app.py"
$appText = Get-Content $appPy -Raw -Encoding UTF8
$appText = [regex]::Replace(
  $appText,
  '(def _default_license_server\(\) -> str:\s+return \(\s+\(os\.environ\.get\("LICENSE_SERVER_DEFAULT"\) or ""\)\.strip\(\)\.rstrip\("/"\)\s+or\s+")[^"]+(")',
  "`${1}$licDefault`${2}"
)
Set-Content -Path $appPy -Value $appText -Encoding UTF8

$iss = Join-Path $Root "packaging\ZhiLiaoXiaoMi.iss"
$issText = Get-Content $iss -Raw -Encoding UTF8
$issText = [regex]::Replace($issText, '#define MyAppVersion "[^"]*"', "#define MyAppVersion `"$Version`"")
Set-Content -Path $iss -Value $issText -Encoding UTF8

# ---- Sidecar: portable Node + @joohw/boss-cli ----
$Runtime = Join-Path $Root "runtime"
$Cache = Join-Path $Root "packaging\.cache"
Ensure-Dir $Cache
Ensure-Dir $Runtime

$nodeTag = "v$NodeVersion"
$nodeZipName = "node-$nodeTag-win-x64.zip"
$nodeZip = Join-Path $Cache $nodeZipName
$nodeUrl = "https://nodejs.org/dist/$nodeTag/$nodeZipName"
$nodeExe = Join-Path $Runtime "node.exe"
$bossCliJs = Join-Path $Runtime "node_modules\@joohw\boss-cli\dist\cli\index.js"

Write-Host "==> Sidecar runtime (Node $nodeTag + boss-cli@$BossCliVersion)"
if (-not (Test-Path $nodeExe)) {
  if (-not (Test-Path $nodeZip)) {
    Write-Host "    Downloading $nodeUrl"
    curl.exe -L --connect-timeout 30 --max-time 600 -o $nodeZip $nodeUrl
    if (-not (Test-Path $nodeZip) -or ((Get-Item $nodeZip).Length -lt 5MB)) {
      throw "Failed to download portable Node: $nodeZip"
    }
  }
  $extract = Join-Path $Cache "node-extract"
  if (Test-Path $extract) { Remove-Item $extract -Recurse -Force }
  Expand-Archive -Path $nodeZip -DestinationPath $extract -Force
  $inner = Get-ChildItem $extract -Directory | Select-Object -First 1
  if (-not $inner) { throw "Invalid Node zip layout" }
  Get-ChildItem $Runtime -Force -ErrorAction SilentlyContinue | ForEach-Object {
    if ($_.Name -ne "node_modules") { Remove-Item $_.FullName -Recurse -Force -ErrorAction SilentlyContinue }
  }
  Copy-Item -Path (Join-Path $inner.FullName "*") -Destination $Runtime -Recurse -Force
  Write-Host "    Node ready: $nodeExe"
} else {
  Write-Host "    Node exists, skip download"
}

$npmCmd = Join-Path $Runtime "npm.cmd"
if (-not (Test-Path $npmCmd)) { throw "npm.cmd missing in runtime. Delete runtime\ and rebuild." }

if (-not (Test-Path $bossCliJs)) {
  Write-Host "    npm install @joohw/boss-cli@$BossCliVersion"
  Push-Location $Runtime
  try {
    & $npmCmd install --omit=dev --no-fund --no-audit "@joohw/boss-cli@$BossCliVersion"
    if ($LASTEXITCODE -ne 0) { throw "npm install boss-cli failed ($LASTEXITCODE)" }
  } finally {
    Pop-Location
  }
} else {
  Write-Host "    boss-cli exists, skip npm install"
}

if (-not (Test-Path $bossCliJs)) {
  throw "boss-cli entry missing: $bossCliJs"
}

# recommend 性别角标补丁（man/woman class + SVG）；npm 装完后必须再打一次
Write-Host "    patch boss-cli gender (recommend.js)"
& $nodeExe (Join-Path $Root "tools\patch_boss_cli_gender.js")
if ($LASTEXITCODE -ne 0) { throw "patch_boss_cli_gender.js failed ($LASTEXITCODE)" }

Write-Host "    Sidecar OK"

Write-Host "==> PyInstaller"
python -m pip install -q pyinstaller pymysql cryptography
python -m PyInstaller --noconfirm --clean (Join-Path $Root "packaging\ZhiLiaoXiaoMi.spec")

$DistApp = Join-Path $Root "dist\$AppId"
$DistRuntime = Join-Path $DistApp "runtime"
Write-Host "==> Copy runtime -> dist\$AppId\runtime"
if (Test-Path $DistRuntime) { Remove-Item $DistRuntime -Recurse -Force }
Ensure-Dir $DistApp
robocopy $Runtime $DistRuntime /E /NFL /NDL /NJH /NJS /nc /ns /np | Out-Null
if ($LASTEXITCODE -ge 8) { throw "robocopy runtime failed: $LASTEXITCODE" }
if (-not (Test-Path (Join-Path $DistRuntime "node.exe"))) {
  throw "dist runtime missing node.exe"
}

$iscc = @(
  "${env:LocalAppData}\Programs\Inno Setup 6\ISCC.exe",
  "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
  "${env:ProgramFiles}\Inno Setup 6\ISCC.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1

if (-not $iscc) {
  Write-Host ""
  Write-Host "PyInstaller + runtime OK: dist\$AppId\"
  Write-Warning "Inno Setup 6 (ISCC.exe) not found. Installer NOT built."
  Write-Host "Expected: dist\installer\$AppId-Setup-$Version.exe"
  exit 0
}

Write-Host "==> Inno Setup: $iscc"
& $iscc $iss
$out = Join-Path $Root "dist\installer\$AppId-Setup-$Version.exe"
if (Test-Path $out) {
  $sha = (Get-FileHash $out -Algorithm SHA256).Hash.ToLower()
  Write-Host "==> Done: $out"
  Write-Host "    SHA256: $sha"
  Write-Host "Upload this file in 直聊小蜜总控 > 客户端发版."
} else {
  Write-Error "Installer output not found: $out"
}
