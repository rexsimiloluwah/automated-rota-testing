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
LOG="/workspace/startup.log"
exec > >(tee -a "$LOG") 2>&1

# Always mark ready on exit so the workflow stops waiting, even on
# partial failure. Diagnostics are in the log.
trap 'touch /workspace/.ready' EXIT

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
    echo "==> [startup] Installing Docker and NVIDIA Container Toolkit..."

    # Docker repo
    apt-get update -qq || true
    apt-get install -y -qq ca-certificates curl gnupg || true
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
        | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    chmod a+r /etc/apt/keyrings/docker.gpg
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
        > /etc/apt/sources.list.d/docker.list

    # NVIDIA Container Toolkit repo
    curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
        | gpg --dearmor -o /etc/apt/keyrings/nvidia-container-toolkit.gpg
    curl -fsSL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
        | sed 's#deb https://#deb [signed-by=/etc/apt/keyrings/nvidia-container-toolkit.gpg] https://#g' \
        > /etc/apt/sources.list.d/nvidia-container-toolkit.list

    apt-get update -qq || true
    apt-get install -y -qq \
        docker-ce docker-ce-cli containerd.io \
        nvidia-container-toolkit || true

    nvidia-ctk runtime configure --runtime=docker || true
    systemctl restart docker || true
fi

if ! command -v docker > /dev/null 2>&1; then
    echo "==> [startup] ERROR: Docker install failed."
    exit 1
fi

echo "==> [startup] Docker version: $(docker --version)"

# Set permissions on /workspace so the test step can write results.
echo "==> [startup] Setting permissions on /workspace..."
chmod -R a+rwX /workspace || true

echo "==> [startup] $(date): Done."
