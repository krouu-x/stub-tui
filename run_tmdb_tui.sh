#!/bin/bash
# Stub TUI Launcher - Linux/macOS
# Run from the stub directory (parent of stub_main)

cd "$(dirname "$0")/.."

echo "Starting Stub TUI..."

# Simple check - just run it, let Python handle errors
if ! command -v python3 &> /dev/null; then
    echo "ERROR: python3 not found in PATH"
    echo "Install Python 3.10+ and add to PATH"
    read -p "Press Enter to exit"
    exit 1
fi

if [[ ! -f "stub_main/main.py" ]]; then
    echo "ERROR: stub_main/main.py not found"
    echo "Run this from the stub directory (parent of stub_main)"
    read -p "Press Enter to exit"
    exit 1
fi

python3 -m stub_main.main

# Pause on error so user can see the message
if [[ $? -ne 0 ]]; then
    echo ""
    echo "Application exited with error"
    read -p "Press Enter to exit"
fi