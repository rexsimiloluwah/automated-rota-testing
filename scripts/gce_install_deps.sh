#!/usr/bin/env bash
# Install Python dependencies on the GCE GPU instance.
# This script runs ON the instance after project files have been copied.

set -e

cd /workspace
source venv/bin/activate

echo "Upgrading pip..."
pip install --quiet --upgrade pip

echo "Installing project dependencies (gpu extras)..."
pip install --quiet ".[gpu]"

echo "Installing ai_foundations (no-deps)..."
pip install --quiet --no-deps -e ai-foundations

echo "Installing Jupyter kernel..."
python -m ipykernel install --name python3 2>/dev/null || true

echo "Dependencies installed."
