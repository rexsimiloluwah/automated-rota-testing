# Automated Notebook Testing for AI Foundations

Automated testing infrastructure for the [google-deepmind/ai-foundations](https://github.com/google-deepmind/ai-foundations) course notebooks.

This repo does **not** contain the notebooks themselves. It clones them fresh from upstream on every CI run to test the latest state.

## Workflows

| Workflow | Trigger | What it does |
|----------|---------|--------------|
| `unit-tests.yml` | push/PR, nightly 02:00 UTC, manual | pytest (utility + feedback solution tests) |
| `notebook-imports.yml` | push/PR, nightly 03:00 UTC, manual | Notebook syntax & import checks (CPU only) |
| `notebook-smoke.yml` | weekly Sun 04:00 UTC, manual | pytest + notebook checks (CPU only) |
| `gpu-tests.yml` | weekly Sun 05:00 UTC, manual | Spins up GCE T4 GPU instance, runs all checks |

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

### Setup with uv

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
#    (--no-deps needed — upstream has conflicting numpy/jax pins)
uv pip install --no-deps -e ai-foundations
```

### Run tests locally

```bash
# Run all tests (utility + feedback solution validation)
uv run pytest tests/ -v --import-mode=importlib

# Run notebook syntax and import checks
uv run python scripts/generate_manifest.py
uv run python scripts/check_notebook.py --all --skip-gpu

# Check a single notebook
uv run python scripts/check_notebook.py ai-foundations/course_1/gdm_lab_1_1_create_your_own_probability_distribution.ipynb
```

### Run GPU tests locally

Requires `gcloud` CLI authenticated with a project that has T4 GPU quota.

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
```

Once configured, the `gpu-tests.yml` workflow will authenticate automatically on every run.

## Project structure

```
.github/workflows/
  unit-tests.yml              pytest (push/PR/nightly)
  notebook-imports.yml        Notebook syntax & import checks (push/PR/nightly)
  notebook-smoke.yml          pytest + notebook checks (weekly)
  gpu-tests.yml               GPU notebook tests via GCE (weekly)
tests/
  test_utilities.py           Tests for ai_foundations utility functions
  test_feedback_solutions.py  Solution code validated through feedback functions
scripts/
  sync_upstream.sh            Shallow-clone upstream repo
  generate_manifest.py        Auto-classify notebooks, build manifest
  check_notebook.py           Validate notebook syntax and imports
  gce_gpu_test.sh             Local: ephemeral GCE GPU instance lifecycle
  gce_startup.sh              Startup script for GCE GPU instance
  gce_install_deps.sh         Dependency install script for GCE instance
  inject_solutions.py         Replace placeholders with solution code (future use)
  run_notebook.py             Execute notebook via papermill (future use)
pyproject.toml                Project dependencies (cpu/gpu extras)
uv.lock                      Locked dependency versions
notebook_overrides.yml        Manual skip/timeout overrides
```

## Notebook classification

| Category | Count | Notes |
|----------|-------|-------|
| CPU-only | 21 | Tested on `ubuntu-latest` in CI |
| GPU-required | 15 | Tested on ephemeral GCE T4 instance |
| Total | 36 | Across courses 1-5 and 7 |
