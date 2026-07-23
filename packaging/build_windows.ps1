[CmdletBinding()]
param(
    [ValidateSet("release", "onedir", "onefile", "installer", "check", "clean")]
    [string]$Mode = "release",
    [switch]$RecreateEnvironment,
    [switch]$SkipTests,
    [switch]$RequireInstaller,
    [switch]$DeepClean,
    [switch]$CI
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
$script:ProjectVersion = $null

$env:PIP_DISABLE_PIP_VERSION_CHECK = "1"
$env:PIP_NO_INPUT = "1"
$env:PYTHONDONTWRITEBYTECODE = "1"
if ($CI) {
    $env:CI = "true"
}

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
        throw "Command failed with exit code ${LASTEXITCODE}: $FilePath $($Arguments -join ' ')"
    }
}

function Get-SafeProjectPath([Parameter(Mandatory = $true)][string]$Path) {
    $full = [System.IO.Path]::GetFullPath($Path)
    $root = [System.IO.Path]::GetFullPath($ProjectRoot).TrimEnd('\')
    $prefix = $root + [System.IO.Path]::DirectorySeparatorChar
    if ($full.Equals($root, [System.StringComparison]::OrdinalIgnoreCase) -or
        -not $full.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to modify a path outside the project output area: $full"
    }
    return $full
}

function Remove-Tree([string]$Path) {
    $safePath = Get-SafeProjectPath $Path
    if (-not (Test-Path -LiteralPath $safePath)) { return }
    $item = Get-Item -LiteralPath $safePath -Force
    if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        Remove-Item -LiteralPath $safePath -Force
        return
    }
    Remove-Item -LiteralPath $safePath -Recurse -Force
}

function Test-IsProtectedGeneratedPath([string]$Path) {
    $full = [System.IO.Path]::GetFullPath($Path).TrimEnd('\')
    foreach ($protectedPath in @((Join-Path $ProjectRoot ".git"), $BuildVenv)) {
        $protected = [System.IO.Path]::GetFullPath($protectedPath).TrimEnd('\')
        $prefix = $protected + [System.IO.Path]::DirectorySeparatorChar
        if ($full.Equals($protected, [System.StringComparison]::OrdinalIgnoreCase) -or
            $full.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
            return $true
        }
    }
    return $false
}

function Clear-GeneratedChildren([string]$Directory) {
    foreach ($item in Get-ChildItem -LiteralPath $Directory -Force -ErrorAction SilentlyContinue) {
        if (Test-IsProtectedGeneratedPath $item.FullName) { continue }
        if ($item.PSIsContainer) {
            if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                continue
            }
            if ($item.Name -in @("__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache") -or
                $item.Name.EndsWith(".egg-info", [System.StringComparison]::OrdinalIgnoreCase)) {
                Remove-Tree $item.FullName
                continue
            }
            [void](Get-SafeProjectPath $item.FullName)
            Clear-GeneratedChildren $item.FullName
            continue
        }
        if ($item.Extension -in @(".pyc", ".pyo", ".tmp", ".bak") -or $item.Name.EndsWith("~")) {
            $safeFile = Get-SafeProjectPath $item.FullName
            Remove-Item -LiteralPath $safeFile -Force -ErrorAction Stop
        }
    }
}

function Clear-GeneratedFiles([switch]$IncludeEnvironment) {
    Write-Step "Cleaning generated files"
    foreach ($path in @($BuildDir, $DistDir, $OneFileDistDir, $ReleaseDir)) {
        Remove-Tree $path
    }

    # Walk one directory level at a time and never descend into reparse points.
    # This avoids following a junction/symlink outside the repository during cleanup.
    Clear-GeneratedChildren $ProjectRoot

    if ($IncludeEnvironment) {
        Remove-Tree $BuildVenv
    }
}

function Resolve-BasePython {
    $candidates = @(
        @{ Command = "py"; Prefix = @("-3.13-64") },
        @{ Command = "py"; Prefix = @("-3.12-64") },
        @{ Command = "py"; Prefix = @("-3.11-64") },
        @{ Command = "py"; Prefix = @("-3.10-64") },
        @{ Command = "python"; Prefix = @() },
        @{ Command = "python3"; Prefix = @() }
    )
    foreach ($candidate in $candidates) {
        $command = Get-Command $candidate.Command -ErrorAction SilentlyContinue
        if (-not $command) { continue }
        try {
            $prefix = @($candidate.Prefix)
            & $command.Source @prefix "-c" "import struct,sys; raise SystemExit(0 if (3,10) <= sys.version_info[:2] < (3,14) and struct.calcsize('P') * 8 == 64 else 1)" 2>$null
            if ($LASTEXITCODE -eq 0) {
                return @{ Path = $command.Source; Prefix = @($candidate.Prefix) }
            }
        } catch {
            continue
        }
    }
    throw "A supported 64-bit Python 3.10-3.13 installation was not found. Install a python.org x64 build and retry."
}

function Test-BuildInterpreter {
    if (-not (Test-Path -LiteralPath $BuildPython -PathType Leaf)) { return $false }
    try {
        & $BuildPython -c "import struct,sys; raise SystemExit(0 if (3,10) <= sys.version_info[:2] < (3,14) and struct.calcsize('P') * 8 == 64 else 1)" 2>$null
        return $LASTEXITCODE -eq 0
    } catch {
        return $false
    }
}

function Ensure-BuildEnvironment {
    if ($RecreateEnvironment -and (Test-Path -LiteralPath $BuildVenv)) {
        Remove-Tree $BuildVenv
    }
    if ((Test-Path -LiteralPath $BuildVenv) -and -not (Test-BuildInterpreter)) {
        Write-Warning "The existing build environment is broken or uses an unsupported interpreter; recreating it."
        Remove-Tree $BuildVenv
    }
    if (-not (Test-BuildInterpreter)) {
        Write-Step "Creating isolated 64-bit build environment"
        $base = Resolve-BasePython
        Invoke-Checked -FilePath $base.Path -Arguments (@($base.Prefix) + @("-m", "venv", $BuildVenv))
    }

    Write-Step "Checking build interpreter architecture"
    Invoke-Checked -FilePath $BuildPython -Arguments @(
        "-c",
        "import struct,sys; raise SystemExit(0 if (3,10) <= sys.version_info[:2] < (3,14) and struct.calcsize('P') * 8 == 64 else 1)"
    )

    Write-Step "Installing the complete SHA-256 locked Windows dependency set"
    Invoke-Checked -FilePath $BuildPython -Arguments @(
        "-m", "pip", "install",
        "--disable-pip-version-check",
        "--no-input",
        "--no-cache-dir",
        "--force-reinstall",
        "--only-binary=:all:",
        "--require-hashes",
        "-r", (Join-Path $ProjectRoot "packaging\requirements-windows.lock")
    )
    Invoke-Checked -FilePath $BuildPython -Arguments @("-m", "pip", "check")
    Invoke-Checked -FilePath $BuildPython -Arguments @(
        (Join-Path $ProjectRoot "tools\verify_build_environment.py"),
        "--lock", (Join-Path $ProjectRoot "packaging\requirements-windows.lock")
    )
}

function Get-ProjectVersion {
    $version = & $BuildPython -c "import pathlib,sys,tomllib; print(tomllib.loads((pathlib.Path(sys.argv[1]) / 'pyproject.toml').read_text(encoding='utf-8'))['project']['version'])" $ProjectRoot
    if ($LASTEXITCODE -ne 0) { throw "Unable to read the project version" }
    $version = "$version".Trim()
    if ($version -notmatch '^\d+\.\d+\.\d+$') {
        throw "Project version is not a supported semantic version: $version"
    }
    return $version
}

function Assert-VersionConsistency {
    $version = Get-ProjectVersion
    $installer = Get-Content -LiteralPath (Join-Path $ProjectRoot "packaging\installer\Pyping.iss") -Raw
    $versionInfo = Get-Content -LiteralPath (Join-Path $ProjectRoot "packaging\windows_version_info.txt") -Raw
    $i18n = Get-Content -LiteralPath (Join-Path $ProjectRoot "pyping_app\i18n.py") -Raw
    if ($installer -notmatch [regex]::Escape("#define MyAppVersion `"$version`"")) {
        throw "Inno Setup version does not match pyproject.toml"
    }
    if ($versionInfo -notmatch [regex]::Escape("u'$version'")) {
        throw "Windows version resource does not match pyproject.toml"
    }
    if ($i18n -notmatch [regex]::Escape("APP_VERSION = `"v$version`"")) {
        throw "Application version does not match pyproject.toml"
    }
    $script:ProjectVersion = $version
}

function Invoke-ProjectChecks {
    if ($SkipTests) { return }
    Write-Step "Running unit tests and static security policy"
    Invoke-Checked -FilePath $BuildPython -Arguments @(
        "-m", "unittest", "discover",
        "-s", (Join-Path $ProjectRoot "tests"),
        "-p", "test_*.py", "-v"
    )
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
    if (-not (Test-Path -LiteralPath $exe -PathType Leaf)) {
        throw "Expected executable was not created: $exe"
    }
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
    if (-not (Test-Path -LiteralPath $exe -PathType Leaf)) {
        throw "Expected executable was not created: $exe"
    }
}

function Find-InnoCompiler {
    $knownPaths = @()
    foreach ($base in @($env:ProgramFiles, ${env:ProgramFiles(x86)})) {
        if (-not $base) { continue }
        $knownPaths += Join-Path $base "Inno Setup 7\ISCC.exe"
        $knownPaths += Join-Path $base "Inno Setup 6\ISCC.exe"
    }
    $found = $knownPaths | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
    if ($found) { return $found }
    $command = Get-Command "iscc.exe" -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }
    return $null
}

function Build-Installer([switch]$Required) {
    $iscc = Find-InnoCompiler
    if (-not $iscc) {
        $message = "Inno Setup 6/7 was not found. Install it and rerun -Mode installer, or omit -RequireInstaller for portable-only output."
        if ($Required) { throw $message }
        Write-Warning $message
        return $false
    }
    Write-Step "Building Inno Setup installer"
    Invoke-Checked -FilePath $iscc -Arguments @((Join-Path $ProjectRoot "packaging\installer\Pyping.iss"))
    $expected = Join-Path $ReleaseDir "Pyping-Setup-$script:ProjectVersion-x64.exe"
    if (-not (Test-Path -LiteralPath $expected -PathType Leaf)) {
        throw "Inno Setup completed without producing the expected installer: $expected"
    }
    return $true
}

function Write-Utf8NoBom([string]$Path, [string]$Content) {
    $encoding = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($Path, $Content, $encoding)
}

function Publish-ReleaseFiles([bool]$InstallerBuilt) {
    Write-Step "Creating exact release archives, manifest and checksums"
    New-Item -ItemType Directory -Path $ReleaseDir -Force | Out-Null

    $portableName = "Pyping-v$script:ProjectVersion-Windows-x64-portable.zip"
    $oneFileName = "Pyping-v$script:ProjectVersion-Windows-x64-onefile.exe"
    $installerName = "Pyping-Setup-$script:ProjectVersion-x64.exe"
    $portableZip = Join-Path $ReleaseDir $portableName
    if (Test-Path -LiteralPath $portableZip) { Remove-Item -LiteralPath $portableZip -Force }
    Compress-Archive -LiteralPath (Join-Path $DistDir "Pyping") -DestinationPath $portableZip -CompressionLevel Optimal

    $oneFileSource = Join-Path $OneFileDistDir "Pyping.exe"
    if (-not (Test-Path -LiteralPath $oneFileSource -PathType Leaf)) {
        throw "Onefile executable is missing: $oneFileSource"
    }
    Copy-Item -LiteralPath $oneFileSource -Destination (Join-Path $ReleaseDir $oneFileName) -Force

    $payloadNames = @($portableName, $oneFileName)
    if ($InstallerBuilt) {
        $installerPath = Join-Path $ReleaseDir $installerName
        if (-not (Test-Path -LiteralPath $installerPath -PathType Leaf)) {
            throw "Installer was reported as built but is missing: $installerPath"
        }
        $payloadNames += $installerName
    }

    $manifestFiles = foreach ($name in ($payloadNames | Sort-Object)) {
        $file = Get-Item -LiteralPath (Join-Path $ReleaseDir $name)
        [ordered]@{
            name = $file.Name
            size = [int64]$file.Length
            sha256 = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        }
    }
    $sourceCommit = "local"
    if ($env:GITHUB_SHA) {
        if ($env:GITHUB_SHA -notmatch '^[0-9a-fA-F]{40}$') {
            throw "GITHUB_SHA is not a full commit SHA"
        }
        $sourceCommit = $env:GITHUB_SHA.ToLowerInvariant()
    } elseif ($CI) {
        throw "CI release builds require GITHUB_SHA"
    }
    $sourceRef = if ($env:GITHUB_REF) { $env:GITHUB_REF } else { "local" }
    $manifest = [ordered]@{
        schema = 1
        application = "Pyping GUI"
        version = $script:ProjectVersion
        platform = "windows-x64"
        source_commit = $sourceCommit
        source_ref = $sourceRef
        built_at_utc = [DateTime]::UtcNow.ToString("o")
        files = @($manifestFiles)
    }
    $manifestPath = Join-Path $ReleaseDir "release-manifest.json"
    Write-Utf8NoBom -Path $manifestPath -Content ($manifest | ConvertTo-Json -Depth 5)

    $checksumNames = @($payloadNames + "release-manifest.json") | Sort-Object
    $checksumLines = foreach ($name in $checksumNames) {
        $hash = (Get-FileHash -LiteralPath (Join-Path $ReleaseDir $name) -Algorithm SHA256).Hash.ToLowerInvariant()
        "$hash  $name"
    }
    Write-Utf8NoBom -Path (Join-Path $ReleaseDir "SHA256SUMS.txt") -Content (($checksumLines -join "`n") + "`n")

    $allowed = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::Ordinal)
    foreach ($name in @($checksumNames + "SHA256SUMS.txt")) { [void]$allowed.Add($name) }
    $unexpected = Get-ChildItem -LiteralPath $ReleaseDir -File | Where-Object { -not $allowed.Contains($_.Name) }
    if ($unexpected) {
        throw "Unexpected files are present in release output: $($unexpected.Name -join ', ')"
    }

    $verifyArguments = @(
        (Join-Path $ProjectRoot "tools\verify_release.py"),
        "--directory", $ReleaseDir,
        "--version", $script:ProjectVersion,
        "--source-commit", $sourceCommit,
        "--source-ref", $sourceRef
    )
    if ($InstallerBuilt) { $verifyArguments += "--require-installer" }
    Invoke-Checked -FilePath $BuildPython -Arguments $verifyArguments
}

Set-Location $ProjectRoot
if ($Mode -eq "clean") {
    Clear-GeneratedFiles -IncludeEnvironment:$DeepClean
    Write-Host "Clean completed." -ForegroundColor Green
    exit 0
}

Ensure-BuildEnvironment
Assert-VersionConsistency
Invoke-ProjectChecks
if ($Mode -eq "check") {
    Write-Host "Checks completed successfully for v$script:ProjectVersion." -ForegroundColor Green
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
        $installerBuilt = Build-Installer -Required:$RequireInstaller
        Publish-ReleaseFiles -InstallerBuilt:$installerBuilt
    }
}

Write-Host "`nBuild completed successfully." -ForegroundColor Green
Write-Host "Project: $ProjectRoot"
Write-Host "Version: $script:ProjectVersion"
Write-Host "Output:  $ReleaseDir"
