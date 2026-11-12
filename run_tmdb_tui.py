#!/usr/bin/env python3
"""
Stub TUI Launcher - Cross-platform (Linux/macOS/Windows)
Run from the stub directory (parent of stub_main)
"""
import os
import sys
import subprocess

# Change to parent directory (stub root)
script_dir = os.path.dirname(os.path.abspath(__file__))
os.chdir(os.path.dirname(script_dir))

print("Starting Stub TUI...")

# Use virtual environment if available (relative to stub_main folder)
script_dir = os.path.dirname(os.path.abspath(__file__))
venv_python = os.path.join(script_dir, "venv", "bin", "python")
if sys.platform == "win32":
    venv_python = os.path.join(script_dir, "venv", "Scripts", "python.exe")

python_exe = venv_python if os.path.exists(venv_python) else sys.executable

# Simple checks
if not os.path.exists("stub_main/main.py"):
    print("ERROR: stub_main/main.py not found")
    print("Run this from the stub directory (parent of stub_main)")
    input("Press Enter to exit")
    sys.exit(1)

try:
    subprocess.run([python_exe, "-m", "stub_main.main"])
except KeyboardInterrupt:
    pass
except FileNotFoundError:
    print("ERROR: Python not found")
    input("Press Enter to exit")
    sys.exit(1)
except Exception as e:
    print(f"Application crashed: {e}")
    input("Press Enter to exit")
    sys.exit(1)

# Check return code
if hasattr(sys, 'last_exit_code') and sys.last_exit_code != 0:
    print(f"\nApplication exited with code {sys.last_exit_code}")
    input("Press Enter to exit")