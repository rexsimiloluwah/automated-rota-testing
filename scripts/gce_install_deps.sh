#!/usr/bin/env bash
# Install dependencies inside the Colab Docker container.
# This script runs INSIDE the container, not on the host.

set -e

cd /workspace

echo "Cloning upstream repo..."
git clone --depth 1 --branch main \
    https://github.com/google-deepmind/ai-foundations.git

echo "Installing ai_foundations..."
pip install --no-cache-dir -e ai-foundations

echo "Installing testing tools..."
pip install --no-cache-dir pytest pyyaml

echo "Dependencies installed."
