#!/usr/bin/env bash
# Spin up an ephemeral GCE GPU instance, run GPU notebook tests in the
# official Colab Docker image, tear down.
#
# Usage:
#   ./scripts/gce_gpu_test.sh [--check-only] [--keep]
#
# Options:
#   --check-only   Run syntax/import checks only (no pytest)
#   --keep         Don't delete the instance after tests (for debugging)
#
# Prerequisites:
#   - gcloud CLI authenticated with a project that has T4 GPU quota
#   - Service account or user with roles/compute.admin
#
# The instance is ALWAYS deleted on exit (unless --keep is passed),
# even if the script fails or is interrupted with Ctrl+C.

set -euo pipefail

# Configuration
INSTANCE_NAME="nb-gpu-test-$(date +%s)"
ZONE="us-central1-a"
MACHINE_TYPE="n1-standard-4"
ACCELERATOR="type=nvidia-tesla-t4,count=1"
IMAGE_FAMILY="pytorch-2-7-cu128-ubuntu-2204-nvidia-570"
IMAGE_PROJECT="deeplearning-platform-release"
BOOT_DISK_SIZE="100GB"
BOOT_DISK_TYPE="pd-ssd"
NETWORK="similoluwa-vpc"
SUBNET="similoluwa-vpc"
COLAB_IMAGE="us-docker.pkg.dev/colab-images/public/runtime"
PROJECT="$(gcloud config get project 2>/dev/null)"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
RESULTS_DIR="${REPO_ROOT}/results/gpu"

# SSH options for gcloud compute ssh.
SSH_OPTS=(
    --ssh-flag="-o StrictHostKeyChecking=no"
    --ssh-flag="-o UserKnownHostsFile=/dev/null"
    --ssh-flag="-o ConnectTimeout=10"
    --ssh-flag="-o LogLevel=ERROR"
)

# SCP options.
SCP_OPTS=(
    --strict-host-key-checking=no
)

# Parse arguments
CHECK_ONLY=false
KEEP_INSTANCE=false

for arg in "$@"; do
    case "$arg" in
        --check-only) CHECK_ONLY=true ;;
        --keep) KEEP_INSTANCE=true ;;
        *) echo "Unknown argument: $arg"; exit 1 ;;
    esac
done

# Cleanup trap — always delete the instance on exit
INSTANCE_CREATED=false

cleanup() {
    local exit_code=$?

    if [ "$INSTANCE_CREATED" = true ] && [ "$KEEP_INSTANCE" = false ]; then
        echo ""
        echo "==> Deleting instance ${INSTANCE_NAME}..."
        gcloud compute instances delete "${INSTANCE_NAME}" \
            --zone="${ZONE}" \
            --quiet \
            2>/dev/null || echo "    Warning: failed to delete instance."
        echo "    Instance deleted."
    elif [ "$KEEP_INSTANCE" = true ] && [ "$INSTANCE_CREATED" = true ]; then
        echo ""
        echo "==> Instance kept (--keep): ${INSTANCE_NAME}"
        echo "    SSH:    gcloud compute ssh ${INSTANCE_NAME} --zone=${ZONE} ${SSH_OPTS[*]}"
        echo "    Logs:   gcloud compute ssh ${INSTANCE_NAME} --zone=${ZONE} ${SSH_OPTS[*]} --command='cat /workspace/startup.log'"
        echo "    Delete: gcloud compute instances delete ${INSTANCE_NAME} --zone=${ZONE} --quiet"
    fi

    exit $exit_code
}

trap cleanup EXIT

# Preflight checks
echo "==> Preflight checks..."

if [ -z "${PROJECT}" ]; then
    echo "    Error: no GCP project set. Run: gcloud config set project YOUR_PROJECT"
    exit 1
fi
echo "    Project: ${PROJECT}"

if ! gcloud auth print-access-token > /dev/null 2>&1; then
    echo "    Error: not authenticated. Run: gcloud auth login"
    exit 1
fi
echo "    Auth: OK"

if ! gcloud compute firewall-rules list \
    --filter="name~default-allow-ssh OR name~allow-ssh" \
    --format="value(name)" 2>/dev/null | grep -q "ssh"; then
    echo "    Warning: no SSH firewall rule found. SSH may fail."
fi
echo "    Firewall: OK"

echo "    Zone: ${ZONE}"
echo "    Network: ${NETWORK}"
echo "    Machine: ${MACHINE_TYPE} + T4"
echo "    Container: ${COLAB_IMAGE}"
echo "    Instance: ${INSTANCE_NAME}"
echo ""

# Step 1: Create instance
echo "==> Creating GCE instance..."
gcloud compute instances create "${INSTANCE_NAME}" \
    --zone="${ZONE}" \
    --machine-type="${MACHINE_TYPE}" \
    --accelerator="${ACCELERATOR}" \
    --maintenance-policy=TERMINATE \
    --image-family="${IMAGE_FAMILY}" \
    --image-project="${IMAGE_PROJECT}" \
    --boot-disk-size="${BOOT_DISK_SIZE}" \
    --boot-disk-type="${BOOT_DISK_TYPE}" \
    --network="${NETWORK}" \
    --subnet="${SUBNET}" \
    --metadata-from-file=startup-script="${SCRIPT_DIR}/gce_startup.sh" \
    --metadata=install-nvidia-driver=True \
    --scopes=cloud-platform \
    --tags=allow-ssh \
    --quiet

INSTANCE_CREATED=true
echo "    Instance created."

# Step 2: Wait for SSH
echo "==> Waiting for SSH..."

SSH_MAX=120
SSH_ELAPSED=0

while [ $SSH_ELAPSED -lt $SSH_MAX ]; do
    if gcloud compute ssh "${INSTANCE_NAME}" \
        --zone="${ZONE}" "${SSH_OPTS[@]}" --quiet \
        --command="echo SSH_OK" 2>/dev/null | grep -q "SSH_OK"; then
        echo "    SSH available. (${SSH_ELAPSED}s)"
        break
    fi
    sleep 10
    SSH_ELAPSED=$((SSH_ELAPSED + 10))
    echo "    Waiting for SSH... (${SSH_ELAPSED}s)"
done

if [ $SSH_ELAPSED -ge $SSH_MAX ]; then
    echo "    Error: SSH not available after ${SSH_MAX}s."
    exit 1
fi

# Step 3: Wait for startup script (NVIDIA drivers + Docker pull)
echo "==> Waiting for startup script to finish..."
echo "    (NVIDIA drivers + Colab image pull, typically 5-10 minutes)"

MAX_WAIT=900
ELAPSED=0
INTERVAL=20

while [ $ELAPSED -lt $MAX_WAIT ]; do
    if gcloud compute ssh "${INSTANCE_NAME}" \
        --zone="${ZONE}" "${SSH_OPTS[@]}" --quiet \
        --command="test -f /workspace/.ready && echo READY" \
        2>/dev/null | grep -q "READY"; then
        echo "    ✅ Instance is ready. (${ELAPSED}s)"
        break
    fi

    sleep $INTERVAL
    ELAPSED=$((ELAPSED + INTERVAL))

    if [ $((ELAPSED % 60)) -eq 0 ]; then
        LAST_LOG=$(gcloud compute ssh "${INSTANCE_NAME}" \
            --zone="${ZONE}" "${SSH_OPTS[@]}" --quiet \
            --command="tail -1 /workspace/startup.log 2>/dev/null || echo '(no log yet)'" \
            2>/dev/null || echo "(SSH failed)")
        echo "    Waiting... (${ELAPSED}s) — ${LAST_LOG}"
    else
        echo "    Waiting... (${ELAPSED}s)"
    fi
done

if [ $ELAPSED -ge $MAX_WAIT ]; then
    echo "    Error: startup script did not complete within ${MAX_WAIT}s."
    echo "    Check logs:"
    echo "    gcloud compute ssh ${INSTANCE_NAME} --zone=${ZONE} ${SSH_OPTS[*]} --command='cat /workspace/startup.log'"
    exit 1
fi

# Step 4: Copy project files to instance
echo "==> Copying project files to instance..."
gcloud compute scp --recurse --zone="${ZONE}" --quiet \
    "${SCP_OPTS[@]}" \
    "${REPO_ROOT}/scripts" \
    "${REPO_ROOT}/tests" \
    "${REPO_ROOT}/pyproject.toml" \
    "${REPO_ROOT}/notebook_overrides.yml" \
    "${INSTANCE_NAME}:/workspace/"

echo "    Files copied."

# Step 5: Install deps and run tests in a single Docker container
echo "==> Running install + tests in Colab container..."
echo ""

TEST_EXIT_CODE=0

# Build the full command: install deps, then run tests.
if [ "$CHECK_ONLY" = true ]; then
    FULL_CMD="bash /workspace/scripts/gce_install_deps.sh && \
        cd /workspace && \
        python scripts/generate_manifest.py \
            --repo-dir ai-foundations \
            --overrides notebook_overrides.yml && \
        python scripts/check_notebook.py --all --repo-dir ai-foundations"
else
    FULL_CMD="bash /workspace/scripts/gce_install_deps.sh && \
        cd /workspace && \
        python scripts/generate_manifest.py \
            --repo-dir ai-foundations \
            --overrides notebook_overrides.yml && \
        python scripts/check_notebook.py --all --repo-dir ai-foundations && \
        pytest tests/ -v --import-mode=importlib --tb=short"
fi

gcloud compute ssh "${INSTANCE_NAME}" --zone="${ZONE}" --quiet \
    "${SSH_OPTS[@]}" \
    --command="sudo docker run --rm --gpus=all \
        --entrypoint '' \
        -v /workspace:/workspace \
        ${COLAB_IMAGE} \
        bash -c '${FULL_CMD}'" \
    || TEST_EXIT_CODE=$?

# Step 7: Copy results back
echo ""
echo "==> Copying results back..."
mkdir -p "${RESULTS_DIR}"

gcloud compute scp --recurse --zone="${ZONE}" --quiet \
    "${SCP_OPTS[@]}" \
    "${INSTANCE_NAME}:/workspace/notebook_manifest.yml" \
    "${RESULTS_DIR}/" \
    2>/dev/null || echo "    Warning: could not copy manifest."

gcloud compute scp --recurse --zone="${ZONE}" --quiet \
    "${SCP_OPTS[@]}" \
    "${INSTANCE_NAME}:/workspace/startup.log" \
    "${RESULTS_DIR}/" \
    2>/dev/null || echo "    Warning: could not copy startup log."

# Step 8: Report
echo ""
if [ $TEST_EXIT_CODE -eq 0 ]; then
    echo "==> ✅ All GPU tests passed."
else
    echo "==> ❌ Some GPU tests failed (exit code: ${TEST_EXIT_CODE})."
fi

echo ""
echo "==> Instance will be deleted automatically."

exit $TEST_EXIT_CODE
