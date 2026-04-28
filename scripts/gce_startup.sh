#!/usr/bin/env bash
# Startup script for GCE GPU test instance.
#
# Waits for NVIDIA drivers, ensures Docker + NVIDIA Container Toolkit
# are installed, then marks the instance as ready. The Colab GPU
# Docker image is pulled lazily by the test step's ``docker run``
# command rather than here, so progress is visible in the workflow
# log and pull failures surface as clear test errors instead of
# silent startup hangs.
#
# Logs: /workspace/startup.log

export DEBIAN_FRONTEND=noninteractive

mkdir -p /workspace
chmod 777 /workspace
LOG="/workspace/startup.log"
exec > >(tee -a "$LOG") 2>&1

# Always mark ready on exit and ensure /workspace is writable, so the
# workflow's SCP step works even if the script exits early.
trap 'chmod -R a+rwX /workspace 2>/dev/null; touch /workspace/.ready' EXIT

echo "==> [startup] $(date): Starting..."

# Wait for NVIDIA drivers. The deeplearning-platform image installs
# them on first boot, which can take several minutes.
echo "==> [startup] Waiting for NVIDIA drivers..."
DRIVER_WAIT=0
DRIVER_MAX=900

while [ $DRIVER_WAIT -lt $DRIVER_MAX ]; do
    if nvidia-smi > /dev/null 2>&1; then
        echo "==> [startup] NVIDIA drivers ready. (${DRIVER_WAIT}s)"
        nvidia-smi --query-gpu=name,driver_version --format=csv,noheader || true
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
    exit 1
fi

# Install Docker + NVIDIA Container Toolkit if not already present.
echo "==> [startup] Checking Docker..."
if ! command -v docker > /dev/null 2>&1; then
    echo "==> [startup] Installing docker.io and nvidia-container-toolkit..."
    apt-get update -qq
    apt-get install -y -qq docker.io nvidia-container-toolkit
    if command -v nvidia-ctk > /dev/null 2>&1; then
        nvidia-ctk runtime configure --runtime=docker
    fi
    systemctl enable --now docker
    systemctl restart docker
fi

if ! command -v docker > /dev/null 2>&1; then
    echo "==> [startup] ERROR: Docker install failed."
    exit 1
fi

echo "==> [startup] Docker version: $(docker --version)"
echo "==> [startup] Docker info:"
docker info 2>&1 | head -20 || true

echo "==> [startup] $(date): Done."
