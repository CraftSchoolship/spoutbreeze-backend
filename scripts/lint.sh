#!/usr/bin/env bash
set -e

echo "Running ruff formatter..."
ruff format .

echo "Running ruff check with auto-fix..."
ruff check . --fix

echo "Running mypy type checking..."
mypy app/

echo "All checks completed successfully."