#!/usr/bin/env python3
"""
Cross-platform launcher for Stub TUI.
Run this from the stub directory (parent of stub_main).
"""
import os
import sys
import subprocess

def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    stub_dir = os.path.dirname(script_dir)
    os.chdir(stub_dir)

    print("Starting Stub TUI...")

    # Check if Python is available
    try:
        result = subprocess.run([sys.executable, "--version"], capture_output=True, text=True)
        print(f"Using: {result.stdout.strip()}")
    except Exception:
        print("ERROR: Python not found")
        sys.exit(1)

    # Check if stub_main folder exists
    if not os.path.exists("stub_main/main.py"):
        print("ERROR: stub_main folder not found")
        print("Make sure you're running this from the stub directory")
        sys.exit(1)

    # Run the application
    try:
        subprocess.run([sys.executable, "-m", "stub_main.main"])
    except KeyboardInterrupt:
        pass
    except Exception as e:
        print(f"Application crashed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()