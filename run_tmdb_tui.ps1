<#
.SYNOPSIS
    TMDB TUI Launcher - PowerShell version (with JS Server support)
.DESCRIPTION
    Runs the modular TMDB TUI application from stub_main folder.
    The JS server (TMDB-Embed-API-main) is started automatically by the TUI
    when it is not already running.
    Run this from the stub directory (parent of stub_main).
#>

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$stubDir = Split-Path -Parent $scriptDir
Set-Location $stubDir

Write-Host "Starting TMDB TUI..." -ForegroundColor Green
Write-Host ""

# Check if Python is available
try {
    $pythonVersion = python --version 2>&1
    Write-Host "Using: $pythonVersion" -ForegroundColor Cyan
}
catch {
    Write-Error "ERROR: Python not found in PATH"
    Write-Host "Please install Python 3.8+ and add it to PATH"
    Read-Host "Press Enter to exit"
    exit 1
}

# Check if stub_main folder exists
if (-not (Test-Path "stub_main\main.py")) {
    Write-Error "ERROR: stub_main folder not found"
    Write-Host "Make sure you're running this from the stub directory"
    Read-Host "Press Enter to exit"
    exit 1
}

# The TUI checks the JS server and starts it automatically when needed, so no
# separate pre-flight health check is required here (it only added startup delay).

# Run the application
try {
    python -m stub_main.main
}
catch {
    Write-Error "Application crashed: $_"
    Read-Host "Press Enter to exit"
    exit 1
}

$exitCode = $LASTEXITCODE
if ($exitCode -ne 0) {
    Write-Host ""
    Write-Warning "Application exited with code $exitCode"
    Read-Host "Press Enter to exit"
}

exit $exitCode