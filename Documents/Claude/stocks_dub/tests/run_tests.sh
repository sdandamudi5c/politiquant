#!/bin/bash
# Run all PolitiQuant tests
cd "$(dirname "$0")/.."
source venv/bin/activate
python3 -m pytest tests/ -v --tb=short 2>&1 || python3 -m unittest discover -s tests -p "test_*.py" -v
