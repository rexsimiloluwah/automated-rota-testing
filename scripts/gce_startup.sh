#!/usr/bin/env bash
# Startup script for GCE GPU test instance.
#
# This runs on the instance at boot time. It waits for NVIDIA drivers,
# installs system packages, and clones the upstream repo.
#
# Python dependency installation happens AFTER gce_gpu_test.sh copies
# the project files (including pyproject.toml) to the instance.
#
# Logs: /workspace/startup.log

set -euo pipefail

export DEBIAN_FRONTEND=noninteractive

mkdir -p /workspace
LOG="/workspace/startup.log"
exec > >(tee -a "$LOG") 2>&1

echo "==> [startup] $(date): Starting..."

# Wait for NVIDIA drivers
echo "==> [startup] Waiting for NVIDIA drivers..."
DRIVER_WAIT=0
DRIVER_MAX=600

while [ $DRIVER_WAIT -lt $DRIVER_MAX ]; do
    if nvidia-smi > /dev/null 2>&1; then
        echo "==> [startup] NVIDIA drivers ready. (${DRIVER_WAIT}s)"
        nvidia-smi --query-gpu=name,driver_version --format=csv,noheader
        break
    fi
    sleep 10
    DRIVER_WAIT=$((DRIVER_WAIT + 10))
    if [ $((DRIVER_WAIT % 60)) -eq 0 ]; then
        echo "    Still waiting for drivers... (${DRIVER_WAIT}s)"
    fi
done

if [ $DRIVER_WAIT -ge $DRIVER_MAX ]; then
    echo "==> [startup] ERROR: NVIDIA drivers not ready after ${DRIVER_MAX}s."
    touch /workspace/.ready
    exit 1
fi

# Install system packages
echo "==> [startup] Installing system packages..."
apt-get update -qq || true
apt-get install -y -qq git python3-pip python3-venv 2>&1 || true

# Clone upstream repo
cd /workspace

echo "==> [startup] Cloning upstream repo..."
git clone --depth 1 --branch main \
    https://github.com/google-deepmind/ai-foundations.git

# Create virtualenv (deps installed later by gce_gpu_test.sh)
echo "==> [startup] Creating virtualenv..."
python3 -m venv /workspace/venv

# Set permissions and mark ready
echo "==> [startup] Setting permissions..."
chmod -R a+rwX /workspace

echo "==> [startup] Writing ready marker..."
touch /workspace/.ready

echo "==> [startup] $(date): Done."
