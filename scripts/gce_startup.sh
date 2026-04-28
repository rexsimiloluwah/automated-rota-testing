#!/usr/bin/env bash
# Startup script for GCE GPU test instance.
#
# Waits for NVIDIA drivers and marks the instance as ready. The Colab
# GPU Docker image is pulled lazily by the test step's ``docker run``
# command rather than here, so progress is visible in the workflow log
# and pull failures surface as clear test errors instead of silent
# startup hangs.
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

# Set permissions on /workspace so the test step can write results.
echo "==> [startup] Setting permissions on /workspace..."
chmod -R a+rwX /workspace || true

echo "==> [startup] $(date): Done."
