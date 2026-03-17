#!/usr/bin/env python3
"""Repo Auditor — entry point.

Usage:
    python main.py teams.csv
    python main.py teams.json --output-dir ./reports --verbose
    python main.py teams.csv --skip-ai
"""

from auditor.cli import main

if __name__ == "__main__":
    main()
