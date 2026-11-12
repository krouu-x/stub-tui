#!/bin/bash
# TMDB TUI Launcher - Bash version (with JS Server support)
# Runs the modular TMDB TUI application from stub_main folder.
# The JS server (TMDB-Embed-API-main) is started automatically by the TUI
# when it is not already running.
# Run this from the stub directory (parent of stub_main).

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STUB_DIR="$(dirname "$SCRIPT_DIR")"
cd "$STUB_DIR"

echo "Starting TMDB TUI..."

# Check if Python is available
if ! command -v python3 &> /dev/null; then
    echo "ERROR: Python3 not found in PATH"
    echo "Please install Python 3.10+ and add it to PATH"
    read -p "Press Enter to exit"
    exit 1
fi

PYTHON_VERSION=$(python3 --version)
echo "Using: $PYTHON_VERSION"

# Check if stub_main folder exists
if [[ ! -f "stub_main/main.py" ]]; then
    echo "ERROR: stub_main folder not found"
    echo "Make sure you're running this from the stub directory"
    read -p "Press Enter to exit"
    exit 1
fi

# The TUI checks the JS server and starts it automatically when needed,
# so no separate pre-flight health check is required here (it only added startup delay).

# Run the application
python3 -m stub_main.main

EXIT_CODE=$?
if [[ $EXIT_CODE -ne 0 ]]; then
    echo ""
    echo "Application exited with code $EXIT_CODE"
    read -p "Press Enter to exit"
fi

exit $EXIT_CODE