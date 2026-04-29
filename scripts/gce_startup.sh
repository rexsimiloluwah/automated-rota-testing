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

# Wait for the dpkg/apt lock. The deeplearning-platform image runs
# unattended-upgrades and apt-daily during early boot, which holds
# /var/lib/dpkg/lock-frontend and causes our apt-get to fail.
wait_for_apt_lock() {
    local waited=0
    local max_wait=600
    while fuser /var/lib/dpkg/lock-frontend > /dev/null 2>&1 \
            || fuser /var/lib/apt/lists/lock > /dev/null 2>&1 \
            || fuser /var/lib/dpkg/lock > /dev/null 2>&1; do
        if [ $waited -ge $max_wait ]; then
            echo "==> [startup] WARNING: apt lock held after ${max_wait}s, proceeding anyway."
            return
        fi
        sleep 5
        waited=$((waited + 5))
        if [ $((waited % 30)) -eq 0 ]; then
            echo "    Waiting for apt lock... (${waited}s)"
        fi
    done
}

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
    echo "==> [startup] Docker not found. Waiting for apt lock..."
    wait_for_apt_lock

    echo "==> [startup] Running apt-get update..."
    if ! timeout 300 apt-get update; then
        echo "==> [startup] apt-get update failed/timed out. Retrying once..."
        sleep 10
        wait_for_apt_lock
        timeout 300 apt-get update \
            || echo "==> [startup] WARNING: apt-get update failed twice."
    fi

    echo "==> [startup] Installing docker.io..."
    if ! timeout 600 apt-get install -y docker.io; then
        echo "==> [startup] ERROR: docker.io install failed/timed out."
        exit 1
    fi

    echo "==> [startup] Adding NVIDIA Container Toolkit repo..."
    timeout 60 curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
        | gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
    timeout 60 curl -fsSL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
        | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
        > /etc/apt/sources.list.d/nvidia-container-toolkit.list

    echo "==> [startup] Installing nvidia-container-toolkit..."
    timeout 300 apt-get update || true
    if ! timeout 600 apt-get install -y nvidia-container-toolkit; then
        echo "==> [startup] WARNING: nvidia-container-toolkit install failed/timed out."
    fi

    if command -v nvidia-ctk > /dev/null 2>&1; then
        timeout 60 nvidia-ctk runtime configure --runtime=docker || true
    fi

    timeout 60 systemctl enable --now docker || true
    timeout 60 systemctl restart docker || true
    sleep 3
fi

if ! command -v docker > /dev/null 2>&1; then
    echo "==> [startup] ERROR: Docker still not found after install attempt."
    exit 1
fi

echo "==> [startup] Docker version: $(docker --version)"

# Verify the daemon is actually up so the test step doesn't fail with
# 'Cannot connect to the Docker daemon'.
echo "==> [startup] Verifying Docker daemon..."
DAEMON_WAIT=0
while [ $DAEMON_WAIT -lt 60 ]; do
    if docker info > /dev/null 2>&1; then
        echo "==> [startup] Docker daemon is up."
        break
    fi
    sleep 5
    DAEMON_WAIT=$((DAEMON_WAIT + 5))
done

if [ $DAEMON_WAIT -ge 60 ]; then
    echo "==> [startup] WARNING: Docker daemon not responding."
    docker info 2>&1 | head -5 || true
fi

echo "==> [startup] $(date): Done."
