# Colab Enterprise Notebook Testing

Run notebooks on Colab Enterprise via the Vertex AI API. This tests against Google's managed Colab runtime rather than a Docker image snapshot.

## When to use this

Use this when you want to verify notebooks work on the actual Colab runtime. This catches issues that the Docker-based tests miss, such as Colab backend dependency updates.

Notebooks that use `google.colab.userdata` (Kaggle-gated notebooks) are automatically skipped since Colab Enterprise uses Secret Manager instead.

## Setup

Run the setup script once to create the GCS bucket and runtime templates (CPU + GPU):

```bash
# Make sure you're authenticated and have a project set
gcloud auth login
gcloud config set project YOUR_PROJECT_ID

# Run setup
./colab-enterprise-workflow/setup.sh
```

This will:
- Enable the Vertex AI and Storage APIs
- Create a GCS bucket named `automated-rota-testing-notebook-ci`
- Create a CPU runtime template (`n1-standard-4`)
- Create a GPU runtime template (`n1-standard-4` + T4 GPU)

After setup, note the template IDs printed at the end. You can also list them:

```bash
gcloud colab runtime-templates list --region=us-central1
```

## Usage

### Run CPU notebooks

```bash
python colab-enterprise-workflow/run_on_colab.py \
    --project YOUR_PROJECT_ID \
    --bucket YOUR_BUCKET_NAME \
    --runtime-template YOUR_TEMPLATE_ID \
    --mode cpu
```

### Run GPU notebooks

Use the GPU runtime template:

```bash
python colab-enterprise-workflow/run_on_colab.py \
    --project YOUR_PROJECT_ID \
    --bucket YOUR_BUCKET_NAME \
    --runtime-template YOUR_GPU_TEMPLATE_ID \
    --mode gpu
```

### Run all notebooks

Run CPU notebooks with the CPU template, then GPU notebooks with the GPU template:

```bash
# CPU notebooks
python colab-enterprise-workflow/run_on_colab.py \
    --project YOUR_PROJECT_ID \
    --bucket YOUR_BUCKET_NAME \
    --runtime-template YOUR_CPU_TEMPLATE_ID \
    --mode cpu

# GPU notebooks
python colab-enterprise-workflow/run_on_colab.py \
    --project YOUR_PROJECT_ID \
    --bucket YOUR_BUCKET_NAME \
    --runtime-template YOUR_GPU_TEMPLATE_ID \
    --mode gpu
```

### Generate a summary report

```bash
python colab-enterprise-workflow/run_on_colab.py \
    --project YOUR_PROJECT_ID \
    --bucket YOUR_BUCKET_NAME \
    --runtime-template YOUR_TEMPLATE_ID \
    --mode cpu \
    --summary results/colab-enterprise/summary.md
```

## Options

| Flag | Default | Description |
|------|---------|-------------|
| `--project` | (required) | GCP project ID |
| `--region` | `us-central1` | GCP region |
| `--bucket` | (required) | GCS bucket for staging (without `gs://`) |
| `--runtime-template` | (required) | Runtime template ID |
| `--repo-dir` | `ai-foundations` | Path to the upstream repo clone |
| `--mode` | `cpu` | `cpu`, `gpu`, or `all` |
| `--timeout` | `1800` | Max wait time per notebook (seconds) |
| `--summary` | None | Path to write markdown summary |

## What it does

1. Generates the notebook manifest (reuses `generate_manifest.py`)
2. Injects reference solutions into placeholder cells (reuses `inject_solutions.py`)
3. Uploads each notebook to GCS
4. Submits an execution job via `gcloud colab executions create`
5. Polls until the job completes
6. Downloads the executed notebook from GCS
7. Parses cell outputs for errors
8. Reports pass/fail per notebook

## What gets skipped

- Notebooks with `skip: true` in `notebook_overrides.yml`
- GPU-required notebooks when running in `--mode cpu`
- Notebooks that use `google.colab.userdata` (would need Secret Manager changes)

## Cost

Colab Enterprise uses Compute Engine pricing:

| Component | Approximate cost |
|-----------|-----------------|
| n1-standard-4 (CPU) | ~$0.19/hr |
| n1-standard-4 + T4 GPU | ~$0.56/hr |

Each notebook execution takes 1-15 minutes depending on content.

## Comparison with Docker-based testing

| | Docker (current) | Colab Enterprise |
|---|---|---|
| Runtime | Colab Docker image snapshot | Google-managed Colab runtime |
| Fidelity | Close proxy | Actual Colab environment |
| Cost | Free (local) / GCE VM | Compute Engine pricing |
| Speed | Fast (local Docker) | Slower (runtime provisioning) |
| Kaggle notebooks | Needs credentials in CI | Needs Secret Manager |
| pytest integration | Yes | No (parse .ipynb for errors) |
