$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$Python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    throw "Run .\bootstrap.ps1 first."
}
$LocalJava = Join-Path $PSScriptRoot ".runtime\jre\bin"
if (Test-Path (Join-Path $LocalJava "java.exe")) {
    $env:PATH = "$LocalJava;$env:PATH"
}
& $Python -m nematode_mind desktop --config config.yaml @args
