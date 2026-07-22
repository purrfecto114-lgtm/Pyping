[CmdletBinding()]
param(
    [ValidateSet("release", "onedir", "onefile", "installer", "check", "clean")]
    [string]$Mode = "release",
    [switch]$RecreateEnvironment,
    [switch]$SkipTests,
    [switch]$RequireInstaller,
    [switch]$DeepClean
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$BuildVenv = Join-Path $ProjectRoot ".venv-build"
$BuildPython = Join-Path $BuildVenv "Scripts\python.exe"
$BuildDir = Join-Path $ProjectRoot "build"
$DistDir = Join-Path $ProjectRoot "dist"
$OneFileDistDir = Join-Path $ProjectRoot "dist-onefile"
$ReleaseDir = Join-Path $ProjectRoot "release"

function Write-Step([string]$Message) {
    Write-Host "`n==> $Message" -ForegroundColor Cyan
}

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments
    )
    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code $LASTEXITCODE: $FilePath $($Arguments -join ' ')"
    }
}

function Remove-Tree([string]$Path) {
    if (Test-Path $Path) {
        Remove-Item -LiteralPath $Path -Recurse -Force
    }
}

function Clear-GeneratedFiles([switch]$IncludeEnvironment) {
    Write-Step "Cleaning generated files"
    foreach ($path in @($BuildDir, $DistDir, $OneFileDistDir, $ReleaseDir)) {
        Remove-Tree $path
    }
    Get-ChildItem -Path $ProjectRoot -Directory -Recurse -Force -ErrorAction SilentlyContinue |
        Where-Object {
            $_.FullName -notlike "$BuildVenv*" -and
            $_.Name -in @("__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache")
        } |
        Sort-Object FullName -Descending |
        ForEach-Object { Remove-Tree $_.FullName }
    Get-ChildItem -Path $ProjectRoot -File -Recurse -Force -ErrorAction SilentlyContinue |
        Where-Object {
            $_.FullName -notlike "$BuildVenv*" -and
            ($_.Extension -in @(".pyc", ".pyo", ".tmp", ".bak") -or $_.Name.EndsWith("~"))
        } |
        Remove-Item -Force -ErrorAction SilentlyContinue
    if ($IncludeEnvironment) {
        Remove-Tree $BuildVenv
    }
}

function Resolve-BasePython {
    $candidates = @(
        @{ Command = "py"; Prefix = @("-3.13") },
        @{ Command = "py"; Prefix = @("-3.12") },
        @{ Command = "py"; Prefix = @("-3.11") },
        @{ Command = "py"; Prefix = @("-3.10") },
        @{ Command = "python"; Prefix = @() },
        @{ Command = "python3"; Prefix = @() }
    )
    foreach ($candidate in $candidates) {
        $command = Get-Command $candidate.Command -ErrorAction SilentlyContinue
        if (-not $command) { continue }
        try {
            $prefix = @($candidate.Prefix)
            & $command.Source @prefix "-c" "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" 2>$null
            if ($LASTEXITCODE -eq 0) {
                return @{ Path = $command.Source; Prefix = @($candidate.Prefix) }
            }
        } catch {
            continue
        }
    }
    throw "Python 3.10 or newer was not found. Install a 64-bit python.org build and retry."
}

function Ensure-BuildEnvironment {
    if ($RecreateEnvironment -and (Test-Path $BuildVenv)) {
        Remove-Tree $BuildVenv
    }
    if (-not (Test-Path $BuildPython)) {
        Write-Step "Creating isolated build environment"
        $base = Resolve-BasePython
        Invoke-Checked -FilePath $base.Path -Arguments (@($base.Prefix) + @("-m", "venv", $BuildVenv))
    }
    Write-Step "Installing build dependencies"
    Invoke-Checked -FilePath $BuildPython -Arguments @("-m", "pip", "install", "--disable-pip-version-check", "--upgrade", "pip", "setuptools", "wheel")
    Invoke-Checked -FilePath $BuildPython -Arguments @(
        "-m", "pip", "install", "--disable-pip-version-check",
        "-r", (Join-Path $ProjectRoot "requirements.txt"),
        "-r", (Join-Path $ProjectRoot "packaging\requirements-build.txt")
    )
}

function Invoke-ProjectChecks {
    if ($SkipTests) { return }
    Write-Step "Running unit tests and static validation"
    Invoke-Checked -FilePath $BuildPython -Arguments @("-m", "unittest", "discover", "-s", (Join-Path $ProjectRoot "tests"), "-p", "test_*.py", "-v")
    Invoke-Checked -FilePath $BuildPython -Arguments @((Join-Path $ProjectRoot "tools\validate_project.py"))
}

function Build-OneDir {
    Write-Step "Building Windows onedir package"
    Invoke-Checked -FilePath $BuildPython -Arguments @(
        "-m", "PyInstaller", "--noconfirm", "--clean",
        "--workpath", $BuildDir,
        "--distpath", $DistDir,
        (Join-Path $ProjectRoot "packaging\Pyping.spec")
    )
    $exe = Join-Path $DistDir "Pyping\Pyping.exe"
    if (-not (Test-Path $exe)) { throw "Expected executable was not created: $exe" }
}

function Build-OneFile {
    Write-Step "Building portable onefile package"
    Invoke-Checked -FilePath $BuildPython -Arguments @(
        "-m", "PyInstaller", "--noconfirm", "--clean",
        "--workpath", (Join-Path $BuildDir "onefile"),
        "--distpath", $OneFileDistDir,
        (Join-Path $ProjectRoot "packaging\Pyping-onefile.spec")
    )
    $exe = Join-Path $OneFileDistDir "Pyping.exe"
    if (-not (Test-Path $exe)) { throw "Expected executable was not created: $exe" }
}

function Find-InnoCompiler {
    $knownPaths = @()
    foreach ($base in @($env:ProgramFiles, ${env:ProgramFiles(x86)})) {
        if (-not $base) { continue }
        $knownPaths += Join-Path $base "Inno Setup 7\ISCC.exe"
        $knownPaths += Join-Path $base "Inno Setup 6\ISCC.exe"
    }
    $found = $knownPaths | Where-Object { Test-Path $_ } | Select-Object -First 1
    if ($found) { return $found }
    $command = Get-Command "iscc.exe" -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }
    return $null
}

function Build-Installer([switch]$Required) {
    $iscc = Find-InnoCompiler
    if (-not $iscc) {
        $message = "Inno Setup was not found. The onedir and portable packages are still valid. Install Inno Setup 6/7 and run -Mode installer to create Setup.exe."
        if ($Required) { throw $message }
        Write-Warning $message
        return $false
    }
    Write-Step "Building Inno Setup installer"
    Invoke-Checked -FilePath $iscc -Arguments @((Join-Path $ProjectRoot "packaging\installer\Pyping.iss"))
    return $true
}

function Publish-ReleaseFiles {
    Write-Step "Creating release archives and checksums"
    New-Item -ItemType Directory -Path $ReleaseDir -Force | Out-Null
    $portableZip = Join-Path $ReleaseDir "Pyping-v0.4.0-Windows-x64-portable.zip"
    if (Test-Path $portableZip) { Remove-Item $portableZip -Force }
    Compress-Archive -Path (Join-Path $DistDir "Pyping") -DestinationPath $portableZip -CompressionLevel Optimal
    $oneFileSource = Join-Path $OneFileDistDir "Pyping.exe"
    if (Test-Path $oneFileSource) {
        Copy-Item $oneFileSource (Join-Path $ReleaseDir "Pyping-v0.4.0-Windows-x64-onefile.exe") -Force
    }
    $checksumPath = Join-Path $ReleaseDir "SHA256SUMS.txt"
    Get-ChildItem $ReleaseDir -File | Where-Object { $_.Name -ne "SHA256SUMS.txt" } |
        Sort-Object Name |
        ForEach-Object {
            $hash = (Get-FileHash $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
            "$hash  $($_.Name)"
        } | Set-Content $checksumPath -Encoding ascii
}

Set-Location $ProjectRoot
if ($Mode -eq "clean") {
    Clear-GeneratedFiles -IncludeEnvironment:$DeepClean
    Write-Host "Clean completed." -ForegroundColor Green
    exit 0
}

Ensure-BuildEnvironment
Invoke-ProjectChecks
if ($Mode -eq "check") {
    Write-Host "Checks completed successfully." -ForegroundColor Green
    exit 0
}

Clear-GeneratedFiles
switch ($Mode) {
    "onedir" {
        Build-OneDir
    }
    "onefile" {
        Build-OneFile
    }
    "installer" {
        Build-OneDir
        [void](Build-Installer -Required)
    }
    "release" {
        Build-OneDir
        Build-OneFile
        [void](Build-Installer -Required:$RequireInstaller)
        Publish-ReleaseFiles
    }
}

Write-Host "`nBuild completed successfully." -ForegroundColor Green
Write-Host "Project: $ProjectRoot"
Write-Host "Output:  $ReleaseDir"
