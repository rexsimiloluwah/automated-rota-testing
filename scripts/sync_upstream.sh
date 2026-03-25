#!/usr/bin/env bash
# Shallow-clone the upstream ai-foundations repo.
#
# Usage:
#   ./scripts/sync_upstream.sh [target_dir]
#
# Arguments:
#   target_dir  Directory to clone into (default: ./ai-foundations)
#
# The script removes any existing clone and fetches a fresh shallow copy
# from the main branch so that every CI run tests the latest upstream state.

set -euo pipefail

UPSTREAM_REPO="https://github.com/google-deepmind/ai-foundations.git"
TARGET_DIR="${1:-ai-foundations}"

echo "==> Syncing upstream repo into ${TARGET_DIR}..."

if [ -d "${TARGET_DIR}" ]; then
    echo "    Removing existing clone..."
    rm -rf "${TARGET_DIR}"
fi

echo "    Cloning ${UPSTREAM_REPO} (shallow, main branch)..."
git clone --depth 1 --branch main "${UPSTREAM_REPO}" "${TARGET_DIR}"

echo "==> Upstream sync complete."