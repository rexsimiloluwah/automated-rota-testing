#!/usr/bin/env python3
"""Orchestrate end-to-end notebook execution with solution injection.

For each non-skipped notebook in the manifest:
  1. Copy to a working directory (upstream repo stays untouched).
  2. Inject reference solutions into activity placeholder cells.
  3. Execute the notebook top-to-bottom via papermill.
  4. Record pass/fail, duration, and errors.

Usage:
    python scripts/run_all_notebooks.py \
        --repo-dir ai-foundations \
        --overrides notebook_overrides.yml \
        --output-dir results/notebooks \
        --mode cpu \
        --strip-installs \
        --summary results/execution_summary.md
"""

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

# Add the scripts directory to sys.path so sibling modules can be imported
# regardless of the working directory.
_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from generate_manifest import generate_manifest  # noqa: E402
from inject_solutions import inject_solutions  # noqa: E402
from run_notebook import run_notebook  # noqa: E402


def _write_summary(
    path: Path,
    results: list[dict],
    passed: int,
    failed: int,
    skipped: int,
) -> None:
    """Write a markdown summary table.

    Args:
        path: File path to write the summary to.
        results: List of result dicts.
        passed: Count of passed notebooks.
        failed: Count of failed notebooks.
        skipped: Count of skipped notebooks.
    """
    lines = []

    if failed == 0:
        lines.append(
            f"All **{passed}** notebook(s) executed successfully "
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
            f"{r.get('duration', 0):.1f}s"
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
    """Entry point for notebook execution orchestrator."""
    parser = argparse.ArgumentParser(
        description="Run notebooks end-to-end with solution injection."
    )
    parser.add_argument(
        "--repo-dir",
        type=Path,
        default=Path("ai-foundations"),
        help="Path to the upstream repo clone (default: ai-foundations).",
    )
    parser.add_argument(
        "--overrides",
        type=Path,
        default=Path("notebook_overrides.yml"),
        help="Path to the overrides YAML file.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("results/notebooks"),
        help="Directory for executed notebook output.",
    )
    parser.add_argument(
        "--mode",
        choices=["cpu", "gpu", "all"],
        default="cpu",
        help="cpu: skip GPU notebooks. gpu/all: run everything except skipped.",
    )
    parser.add_argument(
        "--strip-installs",
        action="store_true",
        help="Strip pip install / %%capture cells before execution.",
    )
    parser.add_argument(
        "--summary",
        type=Path,
        default=None,
        help="Write a markdown summary to this file.",
    )
    args = parser.parse_args()

    if not args.repo_dir.exists():
        print(
            f"Error: repo directory '{args.repo_dir}' does not exist.",
            file=sys.stderr,
        )
        sys.exit(1)

    # In CPU mode, disable CUDA so JAX/TensorFlow don't hang trying
    # to initialize GPU drivers.  Set in both the current process (for
    # papermill's kernel subprocess) and as a flag for notebook
    # injection.
    if args.mode == "cpu":
        os.environ["CUDA_VISIBLE_DEVICES"] = ""

    # Generate manifest in memory.
    manifest = generate_manifest(args.repo_dir, args.overrides)

    # Prepare working directory for injected copies.
    injected_dir = args.output_dir / "injected"
    injected_dir.mkdir(parents=True, exist_ok=True)
    args.output_dir.mkdir(parents=True, exist_ok=True)

    results = []

    print(f"Running {len(manifest)} notebook(s) in {args.mode} mode...\n")

    for entry in manifest:
        nb_path = args.repo_dir / entry["path"]
        name = nb_path.name

        # Always respect manual skip overrides.
        if entry.get("skip"):
            print(
                f"  ⏭️  SKIP  {name}  "
                f"({entry.get('reason', 'manual skip')})"
            )
            results.append({
                "name": name,
                "status": "skip",
                "reason": entry.get("reason", "manual skip"),
                "duration": 0,
            })
            continue

        # In CPU mode, skip GPU-required notebooks.
        if args.mode == "cpu" and entry.get("gpu_required"):
            print(f"  ⏭️  SKIP  {name}  (GPU required)")
            results.append({
                "name": name,
                "status": "skip",
                "reason": "GPU required",
                "duration": 0,
            })
            continue

        # Step 1: Copy notebook to working directory.
        working_copy = injected_dir / name
        shutil.copy2(nb_path, working_copy)

        # Step 2: Inject solutions into the copy.
        nb_dict = inject_solutions(working_copy)
        with open(working_copy, "w", encoding="utf-8") as fh:
            json.dump(nb_dict, fh, indent=1, ensure_ascii=False)
            fh.write("\n")

        # Step 3: Execute notebook.
        # Use the original notebook's parent as cwd so that relative
        # data paths in cells resolve correctly.
        original_cwd = nb_path.parent.resolve()
        timeout = entry.get("timeout", 1200)
        start = time.time()
        success, error_msg = run_notebook(
            working_copy,
            args.output_dir,
            timeout,
            args.strip_installs,
            cwd=original_cwd,
        )
        duration = time.time() - start

        if success:
            results.append({
                "name": name,
                "status": "pass",
                "duration": duration,
            })
        else:
            results.append({
                "name": name,
                "status": "fail",
                "duration": duration,
                "error": error_msg or "Unknown error (see output notebook)",
            })

    # Summary.
    passed = sum(1 for r in results if r["status"] == "pass")
    failed = sum(1 for r in results if r["status"] == "fail")
    skipped = sum(1 for r in results if r["status"] == "skip")

    # Print a clear results table to the terminal.
    print()
    print("=" * 72)
    print("  NOTEBOOK EXECUTION RESULTS")
    print("=" * 72)
    print()

    for r in results:
        if r["status"] == "pass":
            icon = "✅ PASS"
        elif r["status"] == "fail":
            icon = "❌ FAIL"
        else:
            icon = "⏭️  SKIP"

        duration = f"({r['duration']:.1f}s)" if r["duration"] else ""
        print(f"  {icon}  {r['name']}  {duration}")

        if r["status"] == "fail" and r.get("error"):
            print(f"         {r['error']}")
        if r["status"] == "skip" and r.get("reason"):
            print(f"         {r['reason']}")

    print()
    print("-" * 72)
    if failed == 0:
        print(f"  ✅ All {passed} notebook(s) passed ({skipped} skipped).")
    else:
        print(
            f"  ❌ {failed} failed, {passed} passed, "
            f"{skipped} skipped."
        )
    print("-" * 72)

    if args.summary:
        _write_summary(args.summary, results, passed, failed, skipped)
        print(f"Summary written to: {args.summary}")

    sys.exit(1 if failed > 0 else 0)


if __name__ == "__main__":
    main()
