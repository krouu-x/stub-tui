@echo off
REM TMDB TUI Launcher (with JS Server support)
REM Runs the modular TMDB TUI application from stub_main folder

cd /d "%~dp0.."

echo Starting TMDB TUI...
echo.

REM Check if Python is available
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found in PATH
    echo Please install Python 3.8+ and add it to PATH
    pause
    exit /b 1
)

REM Check if stub_main folder exists
if not exist "stub_main\main.py" (
    echo ERROR: stub_main folder not found
    echo Make sure you're running this from the stub directory
    pause
    exit /b 1
)

REM The TUI checks the JS server and starts it automatically when needed, so no
REM separate pre-flight health check is required here (it only added startup delay).

REM Run the application
python -m stub_main.main

REM Check exit code
if errorlevel 1 (
    echo.
    echo Application exited with error code %errorlevel%
    pause
)
