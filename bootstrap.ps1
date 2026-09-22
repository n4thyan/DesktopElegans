$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$LocalPython = Join-Path $PSScriptRoot ".python310\python.exe"
if (Test-Path $LocalPython) {
    $Python = $LocalPython
} else {
    $Python = $null
    try {
        $Python = (py -3.10 -c "import sys; print(sys.executable)").Trim()
    } catch {
        throw "Python 3.10 is required. Install 64-bit Python 3.10, then rerun bootstrap.ps1."
    }
}

$LocalJava = Join-Path $PSScriptRoot ".runtime\jre\bin"
if (Test-Path (Join-Path $LocalJava "java.exe")) {
    $env:PATH = "$LocalJava;$env:PATH"
}

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    & $Python -m venv .venv
}

$VenvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
& $VenvPython -m pip install --upgrade pip setuptools wheel
& $VenvPython -m pip install -e .

$Lock = Get-Content "UPSTREAM.lock.json" | ConvertFrom-Json
$Revision = $Lock.c302.revision
$Repository = $Lock.c302.repository
if (-not (Test-Path "vendor\c302\.git")) {
    New-Item -ItemType Directory -Force -Path vendor | Out-Null
    git clone $Repository "vendor\c302"
}
git -C "vendor\c302" fetch --all --tags
git -C "vendor\c302" checkout --detach $Revision
& $VenvPython -m pip install -e "vendor\c302"

Write-Host ""
Write-Host "Dependency check:"
& $VenvPython -m nematode_mind doctor
