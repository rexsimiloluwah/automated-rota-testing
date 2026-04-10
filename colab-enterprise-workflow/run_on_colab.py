#!/usr/bin/env python3
"""Run notebooks on Colab Enterprise via the Vertex AI API.

Uploads notebooks to GCS, submits them as execution jobs, waits for
completion, downloads the executed notebooks, and parses cell outputs
for errors.

Prerequisites:
    - gcloud CLI authenticated (``gcloud auth login``)
    - A GCP project with Vertex AI API enabled
    - A GCS bucket for staging notebooks
    - A Colab Enterprise runtime template
    - ``google-cloud-aiplatform`` and ``google-cloud-storage`` packages

Usage:
    python colab-enterprise-workflow/run_on_colab.py \
        --project my-project \
        --region us-central1 \
        --bucket my-notebook-bucket \
        --runtime-template TEMPLATE_ID \
        --repo-dir ai-foundations \
        --mode cpu

Setup (one-time):
    1. Create a runtime template:
       gcloud colab runtime-templates create \\
           --display-name="notebook-ci-cpu" \\
           --machine-type=n1-standard-4 \\
           --region=us-central1

    2. Create a GCS bucket:
       gsutil mb gs://my-notebook-bucket

    3. Enable the API:
       gcloud services enable aiplatform.googleapis.com
"""

import argparse
import atexit
import json
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

# Add the scripts directory so we can reuse existing tools.
_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPTS_DIR = _REPO_ROOT / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from generate_manifest import generate_manifest
from inject_solutions import inject_solutions

# Track submitted job IDs for cleanup on interrupt.
_submitted_jobs: list[dict] = []  # [{"id": str, "project": str, "region": str}]


def _gcloud(*args: str, capture: bool = True) -> subprocess.CompletedProcess:
    """Run a gcloud command.

    Args:
        *args: Arguments to pass to gcloud.
        capture: Whether to capture stdout/stderr.

    Returns:
        The completed process.
    """
    cmd = ["gcloud"] + list(args)
    return subprocess.run(
        cmd,
        capture_output=capture,
        text=True,
    )


def _cleanup_jobs() -> None:
    """Cancel all submitted execution jobs and delete active runtimes."""
    if not _submitted_jobs:
        return

    print("\n==> Cleaning up...")

    project = _submitted_jobs[0]["project"]
    region = _submitted_jobs[0]["region"]

    # Cancel running execution jobs.
    result = _gcloud(
        "colab", "executions", "list",
        f"--project={project}",
        f"--region={region}",
        "--filter=jobState=JOB_STATE_RUNNING",
        "--format=value(name)",
    )
    if result.returncode == 0 and result.stdout.strip():
        for job_name in result.stdout.strip().splitlines():
            job_name = job_name.strip()
            if not job_name:
                continue
            print(f"    Cancelling job: {job_name.split('/')[-1]}")
            _gcloud(
                "colab", "executions", "delete", job_name,
                f"--project={project}",
                f"--region={region}",
                "--quiet",
            )

    # Delete active runtimes.
    result = _gcloud(
        "colab", "runtimes", "list",
        f"--project={project}",
        f"--region={region}",
        "--format=value(name)",
    )
    if result.returncode == 0 and result.stdout.strip():
        for runtime_name in result.stdout.strip().splitlines():
            runtime_name = runtime_name.strip()
            if not runtime_name:
                continue
            print(f"    Deleting runtime: {runtime_name.split('/')[-1]}")
            _gcloud(
                "colab", "runtimes", "delete", runtime_name,
                f"--project={project}",
                f"--region={region}",
                "--quiet",
            )

    print("    Cleanup done.")


def _handle_interrupt(signum, frame):
    """Handle Ctrl+C by cleaning up and exiting."""
    _cleanup_jobs()
    sys.exit(1)


def upload_notebook(
    notebook_path: Path,
    bucket: str,
    prefix: str = "notebook-ci",
) -> str:
    """Upload a notebook to GCS.

    Args:
        notebook_path: Local path to the ``.ipynb`` file.
        bucket: GCS bucket name (without ``gs://``).
        prefix: GCS path prefix within the bucket.

    Returns:
        The full ``gs://`` URI of the uploaded notebook.
    """
    gcs_uri = f"gs://{bucket}/{prefix}/{notebook_path.name}"
    result = _gcloud(
        "storage", "cp", str(notebook_path), gcs_uri,
        "--quiet",
    )
    if result.returncode != 0:
        print(f"    Error uploading {notebook_path.name}: {result.stderr}")
    return gcs_uri


def submit_execution(
    gcs_notebook_uri: str,
    gcs_output_uri: str,
    runtime_template: str,
    project: str,
    region: str,
    display_name: str,
    user_email: str,
) -> str | None:
    """Submit a notebook execution job to Colab Enterprise.

    Args:
        gcs_notebook_uri: GCS URI of the input notebook.
        gcs_output_uri: GCS URI prefix for output.
        runtime_template: Runtime template resource name or ID.
        project: GCP project ID.
        region: GCP region.
        display_name: Display name for the execution job.
        user_email: User email for the execution.

    Returns:
        The execution job ID, or None if submission failed.
    """
    result = _gcloud(
        "colab", "executions", "create",
        f"--display-name={display_name}",
        f"--notebook-runtime-template={runtime_template}",
        f"--gcs-notebook-uri={gcs_notebook_uri}",
        f"--gcs-output-uri={gcs_output_uri}",
        f"--user-email={user_email}",
        f"--project={project}",
        f"--region={region}",
        "--format=json",
    )
    if result.returncode != 0:
        print(f"    Error submitting: {result.stderr}")
        return None

    try:
        data = json.loads(result.stdout)
        # The job name is like:
        # projects/PROJECT/locations/REGION/notebookExecutionJobs/JOB_ID
        return data.get("name", "").split("/")[-1]
    except (json.JSONDecodeError, KeyError):
        print(f"    Error parsing response: {result.stdout}")
        return None


def wait_for_execution(
    job_id: str,
    project: str,
    region: str,
    timeout: int = 1800,
    poll_interval: int = 30,
) -> str:
    """Poll until an execution job completes.

    Args:
        job_id: The execution job ID.
        project: GCP project ID.
        region: GCP region.
        timeout: Maximum wait time in seconds.
        poll_interval: Seconds between polls.

    Returns:
        The final job state (e.g. "SUCCEEDED", "FAILED").
    """
    elapsed = 0
    while elapsed < timeout:
        result = _gcloud(
            "colab", "executions", "describe", job_id,
            f"--project={project}",
            f"--region={region}",
            "--format=json",
        )
        if result.returncode == 0:
            try:
                data = json.loads(result.stdout)
                state = data.get("jobState", "UNKNOWN")
                if state in ("JOB_STATE_SUCCEEDED", "SUCCEEDED"):
                    return "SUCCEEDED"
                if state in (
                    "JOB_STATE_FAILED", "FAILED",
                    "JOB_STATE_CANCELLED", "CANCELLED",
                ):
                    return state
            except json.JSONDecodeError:
                pass

        time.sleep(poll_interval)
        elapsed += poll_interval
        if elapsed % 60 == 0:
            print(f"    Waiting... ({elapsed}s)")

    return "TIMEOUT"


def download_output(
    gcs_output_uri: str,
    notebook_name: str,
    output_dir: Path,
) -> Path | None:
    """Download the executed notebook from GCS.

    Colab Enterprise may place the output notebook at various paths
    under the output URI. This function lists all ``.ipynb`` files
    and downloads the first match.

    Args:
        gcs_output_uri: GCS URI prefix where output was written.
        notebook_name: Name of the notebook file.
        output_dir: Local directory to download to.

    Returns:
        Path to the downloaded notebook, or None if download failed.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    local_path = output_dir / notebook_name

    # List all .ipynb files under the output URI to find the result.
    list_result = _gcloud(
        "storage", "ls", f"{gcs_output_uri}/**",
    )
    if list_result.returncode == 0 and list_result.stdout.strip():
        for line in list_result.stdout.strip().splitlines():
            if line.endswith(".ipynb"):
                dl_result = _gcloud(
                    "storage", "cp", line.strip(), str(local_path),
                    "--quiet",
                )
                if dl_result.returncode == 0 and local_path.exists():
                    return local_path

    # Fallback: try the exact path.
    result = _gcloud(
        "storage", "cp",
        f"{gcs_output_uri}/{notebook_name}",
        str(local_path),
        "--quiet",
    )
    if result.returncode == 0 and local_path.exists():
        return local_path

    print(f"    Warning: could not download output for {notebook_name}")
    return None


def parse_notebook_errors(notebook_path: Path) -> list[str]:
    """Parse an executed notebook for cell errors.

    Args:
        notebook_path: Path to the executed ``.ipynb`` file.

    Returns:
        List of error descriptions. Empty if all cells passed.
    """
    with open(notebook_path, "r", encoding="utf-8") as fh:
        nb = json.load(fh)

    errors = []
    for i, cell in enumerate(nb.get("cells", [])):
        if cell.get("cell_type") != "code":
            continue
        for output in cell.get("outputs", []):
            if output.get("output_type") == "error":
                ename = output.get("ename", "Error")
                evalue = output.get("evalue", "")
                errors.append(f"Cell {i}: {ename}: {evalue}")
    return errors


def _get_user_email() -> str:
    """Get the authenticated user's email from gcloud."""
    result = _gcloud("auth", "list", "--filter=status:ACTIVE", "--format=value(account)")
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip().splitlines()[0]
    return ""


def _write_summary(
    path: Path,
    results: list[dict],
    passed: int,
    failed: int,
    skipped: int,
) -> None:
    """Write a markdown summary table."""
    lines = []
    lines.append("## Colab Enterprise Execution Results\n")

    if failed == 0:
        lines.append(
            f"All **{passed}** notebook(s) passed "
            f"({skipped} skipped).\n"
        )
    else:
        lines.append(
            f"**{failed}** notebook(s) failed, "
            f"**{passed}** passed ({skipped} skipped).\n"
        )

    lines.append("| Status | Notebook | Duration | Details |")
    lines.append("|--------|----------|----------|---------|")

    for r in results:
        if r["status"] == "pass":
            icon = "✅"
            detail = ""
        elif r["status"] == "fail":
            icon = "❌"
            detail = r.get("error", "")
        else:
            icon = "⏭️"
            detail = r.get("reason", "skipped")
        duration = (
            f"{r.get('duration', 0):.0f}s"
            if r["status"] != "skip"
            else ""
        )
        lines.append(
            f"| {icon} {r['status'].upper()} | `{r['name']}` "
            f"| {duration} | {detail} |"
        )

    lines.append("")

    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))


def main() -> None:
    """Entry point."""
    parser = argparse.ArgumentParser(
        description="Run notebooks on Colab Enterprise."
    )
    parser.add_argument(
        "--project", default=None,
        help="GCP project ID (default: from gcloud config).",
    )
    parser.add_argument(
        "--region", default="us-central1", help="GCP region."
    )
    parser.add_argument(
        "--bucket", required=True,
        help="GCS bucket for staging notebooks (without gs://).",
    )
    parser.add_argument(
        "--runtime-template", required=True,
        help="Colab Enterprise runtime template ID.",
    )
    parser.add_argument(
        "--repo-dir",
        type=Path,
        default=_REPO_ROOT / "ai-foundations",
        help="Path to the upstream repo clone.",
    )
    parser.add_argument(
        "--overrides",
        type=Path,
        default=_REPO_ROOT / "notebook_overrides.yml",
        help="Path to the overrides YAML file.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=_REPO_ROOT / "results" / "colab-enterprise",
        help="Local directory for downloaded results.",
    )
    parser.add_argument(
        "--mode",
        choices=["cpu", "gpu", "all"],
        default="cpu",
        help="cpu: skip GPU notebooks. gpu/all: run everything except skipped.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=1800,
        help="Max wait time per notebook execution (default: 1800s).",
    )
    parser.add_argument(
        "--summary",
        type=Path,
        default=None,
        help="Write a markdown summary to this file.",
    )
    args = parser.parse_args()

    # Auto-detect project from gcloud config if not provided.
    if not args.project:
        result = _gcloud("config", "get", "project")
        args.project = result.stdout.strip() if result.returncode == 0 else ""
    if not args.project:
        print("Error: no GCP project set.", file=sys.stderr)
        print("Run: gcloud config set project YOUR_PROJECT", file=sys.stderr)
        sys.exit(1)

    if not args.repo_dir.exists():
        print(f"Error: repo directory '{args.repo_dir}' not found.",
              file=sys.stderr)
        sys.exit(1)

    user_email = _get_user_email()
    if not user_email:
        print("Error: could not determine authenticated user email.",
              file=sys.stderr)
        print("Run: gcloud auth login", file=sys.stderr)
        sys.exit(1)
    print(f"Authenticated as: {user_email}")

    # Register cleanup handlers.
    signal.signal(signal.SIGINT, _handle_interrupt)
    signal.signal(signal.SIGTERM, _handle_interrupt)
    atexit.register(_cleanup_jobs)

    # Generate manifest.
    manifest = generate_manifest(args.repo_dir, args.overrides)

    # Prepare working directory for injected copies.
    injected_dir = args.output_dir / "injected"
    injected_dir.mkdir(parents=True, exist_ok=True)

    gcs_output_prefix = f"gs://{args.bucket}/notebook-ci-output"
    results = []

    print(f"\nRunning {len(manifest)} notebook(s) in {args.mode} mode "
          f"on Colab Enterprise...\n")

    for entry in manifest:
        nb_path = args.repo_dir / entry["path"]
        name = nb_path.name

        # Skip logic.
        if entry.get("skip"):
            reason = entry.get("reason", "manual skip")
            print(f"  ⏭️  SKIP  {name}  ({reason})")
            results.append({
                "name": name, "status": "skip",
                "reason": reason, "duration": 0,
            })
            continue

        if args.mode == "cpu" and entry.get("gpu_required"):
            print(f"  ⏭️  SKIP  {name}  (GPU required)")
            results.append({
                "name": name, "status": "skip",
                "reason": "GPU required", "duration": 0,
            })
            continue

        # Skip notebooks that use google.colab.userdata (need Secret
        # Manager changes to work on Colab Enterprise).
        with open(nb_path, "r", encoding="utf-8") as fh:
            nb_content = fh.read()
        if "google.colab" in nb_content and "userdata" in nb_content:
            print(f"  ⏭️  SKIP  {name}  (uses google.colab.userdata)")
            results.append({
                "name": name, "status": "skip",
                "reason": "uses google.colab.userdata", "duration": 0,
            })
            continue

        print(f"  ▶️  RUN   {name}")

        # Step 1: Copy and inject solutions.
        working_copy = injected_dir / name
        shutil.copy2(nb_path, working_copy)
        nb_dict = inject_solutions(working_copy)
        with open(working_copy, "w", encoding="utf-8") as fh:
            json.dump(nb_dict, fh, indent=1, ensure_ascii=False)
            fh.write("\n")

        # Step 2: Upload to GCS.
        gcs_input_uri = upload_notebook(working_copy, args.bucket)

        # Step 3: Submit execution.
        gcs_output_uri = f"{gcs_output_prefix}/{name.replace('.ipynb', '')}"
        display_name = f"ci-{name.replace('.ipynb', '')}"
        start = time.time()

        job_id = submit_execution(
            gcs_input_uri, gcs_output_uri,
            args.runtime_template,
            args.project, args.region,
            display_name, user_email,
        )

        if not job_id:
            duration = time.time() - start
            results.append({
                "name": name, "status": "fail",
                "error": "Failed to submit execution job",
                "duration": duration,
            })
            continue

        # Track for cleanup on interrupt.
        _submitted_jobs.append({
            "id": job_id, "project": args.project, "region": args.region,
        })

        # Step 4: Wait for completion.
        print(f"    Job ID: {job_id}")
        state = wait_for_execution(
            job_id, args.project, args.region, args.timeout,
        )
        duration = time.time() - start

        if state != "SUCCEEDED":
            # Fetch error details from the job.
            detail_result = _gcloud(
                "colab", "executions", "describe", job_id,
                f"--project={args.project}",
                f"--region={args.region}",
                "--format=json",
            )
            error_detail = state
            if detail_result.returncode == 0:
                try:
                    detail_data = json.loads(detail_result.stdout)
                    error_msg = detail_data.get("error", {}).get("message", "")
                    if error_msg:
                        error_detail = f"{state}: {error_msg}"
                except json.JSONDecodeError:
                    pass
            print(f"    ❌ {error_detail} ({duration:.0f}s)")
            results.append({
                "name": name, "status": "fail",
                "error": error_detail,
                "duration": duration,
            })
            continue

        # Step 5: Download and parse output.
        output_nb = download_output(
            gcs_output_uri, name, args.output_dir,
        )

        if output_nb:
            errors = parse_notebook_errors(output_nb)
            if errors:
                error_summary = "; ".join(errors[:3])
                print(f"    ❌ FAIL ({duration:.0f}s) — {error_summary}")
                results.append({
                    "name": name, "status": "fail",
                    "error": error_summary, "duration": duration,
                })
            else:
                print(f"    ✅ PASS ({duration:.0f}s)")
                results.append({
                    "name": name, "status": "pass",
                    "duration": duration,
                })
        else:
            print(f"    ⚠️  PASS (assumed — could not download output) "
                  f"({duration:.0f}s)")
            results.append({
                "name": name, "status": "pass",
                "duration": duration,
            })

    # Summary.
    passed = sum(1 for r in results if r["status"] == "pass")
    failed = sum(1 for r in results if r["status"] == "fail")
    skipped = sum(1 for r in results if r["status"] == "skip")

    print()
    if failed == 0:
        print(f"All {passed} notebook(s) passed ({skipped} skipped).")
    else:
        print(f"{failed} notebook(s) failed, {passed} passed "
              f"({skipped} skipped).")

    if args.summary:
        _write_summary(args.summary, results, passed, failed, skipped)
        print(f"Summary written to: {args.summary}")

    sys.exit(1 if failed > 0 else 0)


if __name__ == "__main__":
    main()
