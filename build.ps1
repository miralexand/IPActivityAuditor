[CmdletBinding()]
param(
    [switch]$SkipInstall,
    [switch]$SkipInstaller,
    [string]$PythonExe
)

$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

$script:pyExe = $null
$script:pyArgs = @()
if ($PythonExe) {
    if (-not (Get-Command $PythonExe -ErrorAction SilentlyContinue)) {
        throw "Python executable not found: $PythonExe"
    }
    & $PythonExe -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" 2>$null
    if ($LASTEXITCODE -ne 0) {
        throw "Specified Python is not 3.11+: $PythonExe"
    }
    $script:pyExe = $PythonExe
}
else {
    $candidates = @(
        @{ Exe = "py"; Args = @("-3.11") },
        @{ Exe = "py"; Args = @("-3") },
        @{ Exe = "python"; Args = @() }
    )
    foreach ($candidate in $candidates) {
        if (-not (Get-Command $candidate.Exe -ErrorAction SilentlyContinue)) {
            continue
        }
        $probe = @($candidate.Args) + @(
            "-c",
            "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)"
        )
        & $candidate.Exe @probe 2>$null
        if ($LASTEXITCODE -eq 0) {
            $script:pyExe = $candidate.Exe
            $script:pyArgs = @($candidate.Args)
            break
        }
    }
}
if (-not $script:pyExe) {
    throw "Python 3.11+ not found. Install Python 3.11+ and add it to PATH."
}

function Run-Py {
    param([string[]]$PyArguments)
    $all = $script:pyArgs + $PyArguments
    & $script:pyExe @all
    if ($LASTEXITCODE -ne 0) {
        throw "Python command failed: $($PyArguments -join ' ')"
    }
}

function Get-PyOutput {
    param([string[]]$PyArguments)
    $all = $script:pyArgs + $PyArguments
    $output = & $script:pyExe @all
    if ($LASTEXITCODE -ne 0) {
        throw "Python command failed: $($PyArguments -join ' ')"
    }
    return ($output | Out-String).Trim()
}

Write-Host "[1/6] Preparing Python environment..."
if (-not $SkipInstall) {
    Run-Py @("-m", "pip", "install", "--disable-pip-version-check", "-r", "requirements.txt")
}
else {
    Write-Host "      Skipping dependency installation."
}

Write-Host "[2/6] Generating application icon..."
Run-Py @("tools\make_icon.py")

$version = Get-PyOutput @(
    "-c",
    "import sys; sys.path.insert(0, 'src'); from version import APP_VERSION; print(APP_VERSION)"
)
Write-Host "      Application version: $version"

Write-Host "[3/6] Building executable with PyInstaller..."
Run-Py @(
    "-m", "PyInstaller",
    "--noconfirm", "--clean", "--onefile", "--console", "--hide-console", "hide-early",
    "--name", "IPActivityAuditor",
    "--icon", "assets\icon.ico",
    "--version-file", "assets\version_info.txt",
    "--add-data", "assets;assets",
    "--collect-all", "scapy",
    "--collect-all", "customtkinter",
    "--collect-all", "pystray",
    "src\main.py"
)

$exe = Join-Path $PSScriptRoot "dist\IPActivityAuditor.exe"
if (-not (Test-Path -LiteralPath $exe)) {
    throw "Build output not found: $exe"
}

Write-Host "[4/6] Preparing portable package..."
$portable = Join-Path $PSScriptRoot "dist\IPActivityAuditor-portable"
if (Test-Path -LiteralPath $portable) {
    Remove-Item -LiteralPath $portable -Recurse -Force
}
New-Item -ItemType Directory -Path $portable | Out-Null
Copy-Item -LiteralPath $exe -Destination $portable
Copy-Item -LiteralPath "config.ini.example" -Destination (Join-Path $portable "config.ini")
Copy-Item -LiteralPath "config.ini.example" -Destination $portable
Copy-Item -LiteralPath "README.md" -Destination $portable
Copy-Item -LiteralPath "assets\icon.ico" -Destination $portable

Copy-Item -LiteralPath "config.ini.example" -Destination "dist\config.ini.example" -Force
Copy-Item -LiteralPath "README.md" -Destination "dist\README.md" -Force

$zip = Join-Path $PSScriptRoot "dist\IPActivityAuditor-portable-$version.zip"
if (Test-Path -LiteralPath $zip) {
    Remove-Item -LiteralPath $zip -Force
}
$sevenZip = @(
    "C:\Program Files\7-Zip\7z.exe",
    "C:\Program Files (x86)\7-Zip\7z.exe"
) | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if ($sevenZip) {
    & $sevenZip a -tzip -mx=9 $zip (Join-Path $portable "*") | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "7-Zip packaging failed."
    }
}
else {
    Compress-Archive -Path (Join-Path $portable "*") -DestinationPath $zip -Force
}

Write-Host "[5/6] Building installer..."
if (-not $SkipInstaller) {
    $iscc = @(
        (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe"),
        "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
        "C:\Program Files\Inno Setup 6\ISCC.exe"
    ) | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1

    if ($iscc) {
        & $iscc "/DMyAppVersion=$version" "installer\setup.iss"
        if ($LASTEXITCODE -ne 0) {
            throw "Inno Setup compilation failed."
        }
    }
    else {
        Write-Warning "Inno Setup 6 not found. Install it to build the installer, or run with -SkipInstaller."
    }
}
else {
    Write-Host "      Skipping installer build."
}

Write-Host "[6/6] Build summary"
Get-ChildItem -LiteralPath "dist" | Where-Object {
    $_.Name -in @(
        "IPActivityAuditor.exe",
        "IPActivityAuditor-portable-$version.zip",
        "IPActivityAuditor-Setup-$version.exe"
    )
} | ForEach-Object {
    Write-Host ("      {0}  ({1:N1} MB)" -f $_.Name, ($_.Length / 1MB))
}
Write-Host "Build complete."
