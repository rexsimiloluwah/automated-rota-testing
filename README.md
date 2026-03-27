# Automated Notebook Testing for AI Foundations

Automated testing infrastructure for the [google-deepmind/ai-foundations](https://github.com/google-deepmind/ai-foundations) course notebooks.

This repo does **not** contain the notebooks themselves. It clones them fresh from upstream on every CI run to test the latest state.

## Environment consistency

All tests run inside the **official Google Colab Docker image** (`us-docker.pkg.dev/colab-images/public/cpu-runtime` for CPU, `us-docker.pkg.dev/colab-images/public/runtime` for GPU). This guarantees that test results match what students experience on Colab — same Python version, same package versions, same runtime behavior.

## Workflows

| Workflow | Trigger | What it does |
|----------|---------|--------------|
| `unit-tests.yml` | push/PR, nightly 02:00 UTC, manual | pytest (utility + feedback solution tests) |
| `notebook-imports.yml` | push/PR, nightly 03:00 UTC, manual | Notebook syntax & import checks (CPU only) |
| `notebook-smoke.yml` | weekly Sun 04:00 UTC, manual | pytest + notebook checks (CPU only) |
| `gpu-tests.yml` | weekly Sun 05:00 UTC, manual | Spins up GCE T4 GPU instance, runs all checks |

All workflows use the Colab Docker image as the execution environment.

## How it works

### Test suite (`tests/`)

**`test_utilities.py`** — tests pure functions in `ai_foundations`:
- `bytes_to_gb()`, `format_flops()`, `format_large_number()`, `format_qa()`

**`test_feedback_solutions.py`** — passes reference solution code through the upstream feedback validation functions:
- Course 1: n-gram generation, counting, model building, vocabulary
- Course 2: HTML cleaning, Unicode cleaning
- Course 7: FLOPs estimation, GPU memory calculations

If upstream changes break a solution or a feedback validator, these tests catch it.

### Notebook validation (`scripts/`)

```
sync_upstream.sh          Clone google-deepmind/ai-foundations@main
        |
generate_manifest.py      Scan notebooks, auto-classify GPU/CPU, merge overrides
        |
check_notebook.py         Validate syntax and imports per notebook
```

### GPU detection

`generate_manifest.py` scans each notebook for GPU signals:

- **Markdown cells:** "Change runtime type", "Hardware Accelerator", "T4 GPU", "must be run on a GPU"
- **Code cells:** `load_gemma(`, `keras_nlp.models.Gemma`, `nvidia-smi`

Notebooks matching any signal are tagged `gpu_required: true`.

### Overrides

Edit `notebook_overrides.yml` to force-skip notebooks or adjust settings:

```yaml
overrides:
  - path: course_5/gdm_lab_5_4_full_parameter_fine_tuning_of_gemma.ipynb
    skip: true
    reason: "Requires Kaggle credentials for Gemma model download"
```

## Local testing

### Using Docker (recommended)

Docker provides the exact Colab environment. No Python version or package mismatch.

```bash
# Run all CPU tests (pytest + notebook checks)
docker compose run test

# Run only pytest
docker compose run pytest

# Run only notebook checks
docker compose run check

# Drop into a shell for debugging
docker compose run shell
```

### Using uv (without Docker)

```bash
# 1. Install uv (if not already installed)
curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. Create the virtual environment and install dependencies
uv sync --extra cpu

# 3. Activate the virtual environment
source .venv/bin/activate

# 4. Clone the upstream repo
bash scripts/sync_upstream.sh

# 5. Install the ai_foundations package
uv pip install --no-deps -e ai-foundations

# 6. Run tests
uv run pytest tests/ -v --import-mode=importlib
uv run python scripts/generate_manifest.py
uv run python scripts/check_notebook.py --all --skip-gpu
```

Note: results may differ from Colab due to Python version and package differences.

### Run GPU tests locally

Requires `gcloud` CLI authenticated with a project that has T4 GPU quota. Runs tests inside the Colab GPU Docker image on an ephemeral GCE instance.

```bash
# Full run: create instance, run tests, delete instance
./scripts/gce_gpu_test.sh

# Syntax/import checks only (faster)
./scripts/gce_gpu_test.sh --check-only

# Keep instance alive after tests (for debugging)
./scripts/gce_gpu_test.sh --keep
```

## Setting up GCP Workload Identity Federation

The `gpu-tests.yml` workflow authenticates to GCP using Workload Identity Federation (keyless). This requires a one-time setup.

### 1. Create a service account

```bash
export PROJECT_ID="your-project-id"
export PROJECT_NUMBER=$(gcloud projects describe $PROJECT_ID --format="value(projectNumber)")
export GITHUB_ORG="your-github-org"
export GITHUB_REPO="automated-rota-testing"

gcloud iam service-accounts create notebook-ci-runner \
  --display-name="Notebook CI Runner"

gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:notebook-ci-runner@${PROJECT_ID}.iam.gserviceaccount.com" \
  --role="roles/compute.admin"
```

### 2. Create a Workload Identity Pool and Provider

```bash
gcloud iam workload-identity-pools create "github-pool" \
  --location="global" \
  --display-name="GitHub Actions Pool"

gcloud iam workload-identity-pools providers create-oidc "github-provider" \
  --location="global" \
  --workload-identity-pool="github-pool" \
  --display-name="GitHub Provider" \
  --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository" \
  --issuer-uri="https://token.actions.githubusercontent.com"
```

### 3. Allow your GitHub repo to impersonate the service account

```bash
gcloud iam service-accounts add-iam-policy-binding \
  notebook-ci-runner@${PROJECT_ID}.iam.gserviceaccount.com \
  --role="roles/iam.workloadIdentityUser" \
  --member="principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/github-pool/attribute.repository/${GITHUB_ORG}/${GITHUB_REPO}"
```

### 4. Add GitHub secrets

Add these two secrets to your GitHub repository (Settings > Secrets and variables > Actions):

| Secret | Value |
|--------|-------|
| `GCP_WORKLOAD_IDENTITY_PROVIDER` | `projects/<PROJECT_NUMBER>/locations/global/workloadIdentityPools/github-pool/providers/github-provider` |
| `GCP_SERVICE_ACCOUNT` | `notebook-ci-runner@<PROJECT_ID>.iam.gserviceaccount.com` |

### 5. Enable required APIs

```bash
gcloud services enable iamcredentials.googleapis.com
gcloud services enable compute.googleapis.com
gcloud services enable sheets.googleapis.com
gcloud services enable drive.googleapis.com
```

### 6. Share your Drive folder with the service account

Share the Google Drive results folder with the service account email as an **Editor**:

```
notebook-ci-runner@<PROJECT_ID>.iam.gserviceaccount.com
```

### 7. Add the Drive folder secret

Add one more secret to your GitHub repository:

| Secret | Value |
|--------|-------|
| `GOOGLE_DRIVE_FOLDER_ID` | The folder ID from the Drive URL |

### 8. Enable Google Sheets writing

In `.github/workflows/notebook-tests.yml`, change `WRITE_SHEETS: 'false'` to `WRITE_SHEETS: 'true'` in the `report` job.

Once configured, every workflow run will:
- Write results to the GitHub Actions summary
- Create a new Google Sheet in your Drive folder with styled results

## Project structure

```
.github/workflows/
  notebook-tests.yml          Main workflow: all jobs + report (push/PR/nightly)
  unit-tests.yml              Standalone pytest (manual)
  notebook-imports.yml        Standalone notebook checks (manual)
  gpu-tests.yml               Standalone GPU tests (manual)
tests/
  test_utilities.py           Tests for ai_foundations utility functions
  test_feedback_solutions.py  Solution code validated through feedback functions
scripts/
  sync_upstream.sh            Shallow-clone upstream repo
  generate_manifest.py        Auto-classify notebooks, build manifest
  check_notebook.py           Validate notebook syntax and imports
  write_results.py            Parse test outputs into structured JSON
  write_to_sheets.py          Write results to Google Sheets
  gce_gpu_test.sh             Local: ephemeral GCE GPU instance lifecycle
  gce_startup.sh              Startup script for GCE GPU instance
  gce_install_deps.sh         Install deps inside Colab Docker container
  inject_solutions.py         Replace placeholders with solution code (future use)
  run_notebook.py             Execute notebook via papermill (future use)
Dockerfile                    Colab CPU image for CI and local testing
Dockerfile.gpu                Colab GPU image for GPU testing
docker-compose.yml            Local Docker testing commands
pyproject.toml                Project dependencies (cpu/gpu/sheets extras)
uv.lock                      Locked dependency versions
notebook_overrides.yml        Manual skip/timeout overrides
```

## Notebook classification

| Category | Count | Notes |
|----------|-------|-------|
| CPU-only | 21 | Tested in Colab CPU container |
| GPU-required | 15 | Tested in Colab GPU container on GCE T4 |
| Total | 36 | Across courses 1-5 and 7 |
