#!/usr/bin/env bash
# Startup script for GCE GPU test instance.
#
# Waits for NVIDIA drivers, pulls the Colab GPU Docker image,
# and marks the instance as ready.
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

# Ensure Docker and NVIDIA Container Toolkit are available
echo "==> [startup] Checking Docker..."
if ! command -v docker &> /dev/null; then
    echo "==> [startup] Installing Docker..."
    apt-get update -qq
    apt-get install -y -qq docker.io nvidia-container-toolkit 2>&1
    systemctl restart docker
fi

# Verify nvidia-docker works
echo "==> [startup] Verifying GPU access in Docker..."
if ! sudo docker run --rm --gpus=all nvidia/cuda:12.0.0-base-ubuntu22.04 nvidia-smi > /dev/null 2>&1; then
    echo "==> [startup] WARNING: GPU not accessible from Docker. Restarting Docker..."
    systemctl restart docker
    sleep 5
fi

# Pull the Colab GPU image
echo "==> [startup] Pulling Colab GPU image..."
sudo docker pull us-docker.pkg.dev/colab-images/public/runtime

# Set permissions and mark ready
echo "==> [startup] Setting permissions..."
chmod -R a+rwX /workspace

echo "==> [startup] Writing ready marker..."
touch /workspace/.ready

echo "==> [startup] $(date): Done."
