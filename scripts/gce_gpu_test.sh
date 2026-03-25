#!/usr/bin/env bash
# Spin up an ephemeral GCE GPU instance, run GPU notebook tests, tear down.
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

# SCP options — gcloud compute scp uses native flags, not --ssh-flag.
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

# Check that an SSH firewall rule exists.
if ! gcloud compute firewall-rules list \
    --filter="name~default-allow-ssh OR name~allow-ssh" \
    --format="value(name)" 2>/dev/null | grep -q "ssh"; then
    echo "    Warning: no SSH firewall rule found. SSH may fail."
    echo "    Fix: gcloud compute firewall-rules create allow-ssh --allow=tcp:22 --direction=INGRESS"
fi
echo "    Firewall: OK"

echo "    Zone: ${ZONE}"
echo "    Network: ${NETWORK}"
echo "    Machine: ${MACHINE_TYPE} + T4"
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

# Step 2: Wait for SSH to become available
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

# Step 3: Wait for startup script to complete
echo "==> Waiting for startup script to finish..."
echo "    (NVIDIA drivers + pip installs, typically 5-10 minutes)"

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

    # Show progress with context every 60s.
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

# Step 5: Install Python dependencies
echo "==> Installing Python dependencies on instance..."
if ! gcloud compute ssh "${INSTANCE_NAME}" --zone="${ZONE}" --quiet \
    "${SSH_OPTS[@]}" \
    --command="bash /workspace/scripts/gce_install_deps.sh"; then
    echo "    Error: dependency installation failed."
    exit 1
fi

# Step 6: Run tests
echo "==> Running GPU notebook tests..."
echo ""

TEST_EXIT_CODE=0

# Generate manifest and run import/syntax checks on ALL notebooks.
gcloud compute ssh "${INSTANCE_NAME}" --zone="${ZONE}" --quiet \
    "${SSH_OPTS[@]}" \
    --command="cd /workspace && source venv/bin/activate && \
        python scripts/generate_manifest.py \
            --repo-dir ai-foundations \
            --overrides notebook_overrides.yml && \
        python scripts/check_notebook.py --all --repo-dir ai-foundations" \
    || TEST_EXIT_CODE=$?

if [ "$CHECK_ONLY" = true ]; then
    echo ""
    echo "==> Check-only mode. Skipping pytest."
else
    # Run pytest (feedback solution tests + utility tests).
    echo ""
    echo "==> Running pytest..."
    gcloud compute ssh "${INSTANCE_NAME}" --zone="${ZONE}" --quiet \
        "${SSH_OPTS[@]}" \
        --command="cd /workspace && source venv/bin/activate && \
            pytest tests/ -v --import-mode=importlib --tb=short" \
        || TEST_EXIT_CODE=$?
fi

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
