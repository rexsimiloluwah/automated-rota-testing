#!/usr/bin/env bash
# One-time setup for Colab Enterprise notebook testing.
#
# Creates a GCS bucket and runtime templates (CPU + GPU) for running
# notebooks on Colab Enterprise via the Vertex AI API.
#
# Usage:
#   ./colab-enterprise-workflow/setup.sh
#
# Prerequisites:
#   - gcloud CLI authenticated (gcloud auth login)
#   - A GCP project set (gcloud config set project YOUR_PROJECT)

set -euo pipefail

# Configuration — edit these to match your environment.
PROJECT="$(gcloud config get project 2>/dev/null)"
REGION="us-central1"
BUCKET="automated-rota-testing-notebook-ci"

if [ -z "${PROJECT}" ]; then
    echo "Error: no GCP project set."
    echo "Run: gcloud config set project YOUR_PROJECT"
    exit 1
fi

echo "==> Setup for Colab Enterprise notebook testing"
echo "    Project: ${PROJECT}"
echo "    Region:  ${REGION}"
echo "    Bucket:  gs://${BUCKET}"
echo ""

# Step 1: Enable required APIs.
echo "==> Enabling APIs..."
gcloud services enable aiplatform.googleapis.com --quiet
gcloud services enable storage.googleapis.com --quiet
echo "    Done."

# Step 2: Create GCS bucket (if it doesn't exist).
echo ""
echo "==> Creating GCS bucket..."
if gsutil ls -b "gs://${BUCKET}" > /dev/null 2>&1; then
    echo "    Bucket gs://${BUCKET} already exists."
else
    gsutil mb -l "${REGION}" "gs://${BUCKET}"
    echo "    Created gs://${BUCKET}"
fi

# Step 3: Create CPU runtime template.
echo ""
echo "==> Creating CPU runtime template..."
CPU_TEMPLATE_OUTPUT=$(gcloud colab runtime-templates create \
    --display-name="automated-rota-testing-cpu" \
    --machine-type=n1-standard-4 \
    --region="${REGION}" \
    --format="value(name)" \
    2>&1) || true

if echo "${CPU_TEMPLATE_OUTPUT}" | grep -q "notebookRuntimeTemplates"; then
    CPU_TEMPLATE_ID=$(echo "${CPU_TEMPLATE_OUTPUT}" | grep -o '[^/]*$')
    echo "    CPU template ID: ${CPU_TEMPLATE_ID}"
else
    echo "    ${CPU_TEMPLATE_OUTPUT}"
    echo "    (If the template already exists, you can list them with:"
    echo "     gcloud colab runtime-templates list --region=${REGION})"
fi

# Step 4: Create GPU runtime template.
echo ""
echo "==> Creating GPU runtime template..."
GPU_TEMPLATE_OUTPUT=$(gcloud colab runtime-templates create \
    --display-name="automated-rota-testing-gpu" \
    --machine-type=n1-standard-4 \
    --accelerator-type=NVIDIA_TESLA_T4 \
    --accelerator-count=1 \
    --region="${REGION}" \
    --format="value(name)" \
    2>&1) || true

if echo "${GPU_TEMPLATE_OUTPUT}" | grep -q "notebookRuntimeTemplates"; then
    GPU_TEMPLATE_ID=$(echo "${GPU_TEMPLATE_OUTPUT}" | grep -o '[^/]*$')
    echo "    GPU template ID: ${GPU_TEMPLATE_ID}"
else
    echo "    ${GPU_TEMPLATE_OUTPUT}"
    echo "    (If the template already exists, you can list them with:"
    echo "     gcloud colab runtime-templates list --region=${REGION})"
fi

# Summary.
echo ""
echo "==> Setup complete!"
echo ""
echo "Run CPU notebooks:"
echo "  python colab-enterprise-workflow/run_on_colab.py \\"
echo "      --project ${PROJECT} \\"
echo "      --bucket ${BUCKET} \\"
echo "      --runtime-template CPU_TEMPLATE_ID \\"
echo "      --mode cpu"
echo ""
echo "Run GPU notebooks:"
echo "  python colab-enterprise-workflow/run_on_colab.py \\"
echo "      --project ${PROJECT} \\"
echo "      --bucket ${BUCKET} \\"
echo "      --runtime-template GPU_TEMPLATE_ID \\"
echo "      --mode gpu"
echo ""
echo "List your runtime templates:"
echo "  gcloud colab runtime-templates list --region=${REGION}"
