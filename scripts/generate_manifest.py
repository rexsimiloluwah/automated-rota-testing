#!/usr/bin/env python3
"""Scan upstream notebooks and generate a testing manifest.

This script walks the upstream ai-foundations repo, finds all Jupyter
notebooks, auto-classifies them as GPU or CPU based on cell contents,
and merges any manual overrides from ``notebook_overrides.yml``.

Usage:
    python scripts/generate_manifest.py [--repo-dir ai-foundations] \
        [--overrides notebook_overrides.yml] [--output notebook_manifest.yml]
"""

import argparse
import json
import re
import sys
from pathlib import Path

import yaml


# Patterns that indicate a notebook needs a GPU runtime.
_GPU_MARKDOWN_PATTERNS: list[re.Pattern] = [
    re.compile(r"change\s+runtime\s+type", re.IGNORECASE),
    re.compile(r"hardware\s+accelerator", re.IGNORECASE),
    re.compile(r"T4\s+GPU", re.IGNORECASE),
    re.compile(r"must\s+be\s+run\s+on\s+a\s+GPU", re.IGNORECASE),
    re.compile(r"recommend\s+running.*GPU", re.IGNORECASE),
    re.compile(r"needs\s+to\s+be\s+run\s+on\s+a\s+GPU", re.IGNORECASE),
]

_GPU_CODE_PATTERNS: list[re.Pattern] = [
    re.compile(r"load_gemma\("),
    re.compile(r"keras_nlp\.models\.Gemma"),
    re.compile(r"nvidia-smi"),
]

# Default timeout in seconds for notebook execution.
_DEFAULT_TIMEOUT: int = 1200


def _detect_gpu(notebook_path: Path) -> bool:
    """Detect whether a notebook requires a GPU runtime.

    Args:
        notebook_path: Path to the ``.ipynb`` file.

    Returns:
        True if GPU signals are found in any cell, False otherwise.
    """
    with open(notebook_path, "r", encoding="utf-8") as fh:
        nb = json.load(fh)

    for cell in nb.get("cells", []):
        source = "".join(cell.get("source", []))
        cell_type = cell.get("cell_type", "")

        if cell_type == "markdown":
            for pattern in _GPU_MARKDOWN_PATTERNS:
                if pattern.search(source):
                    return True

        if cell_type == "code":
            for pattern in _GPU_CODE_PATTERNS:
                if pattern.search(source):
                    return True

    return False


def _extract_course(notebook_path: Path, repo_dir: Path) -> str:
    """Extract the course identifier from the notebook path.

    Args:
        notebook_path: Absolute path to the notebook.
        repo_dir: Root directory of the upstream repo.

    Returns:
        The course directory name (e.g. ``course_1``).
    """
    relative = notebook_path.relative_to(repo_dir)
    return relative.parts[0]


def _find_notebooks(repo_dir: Path) -> list[Path]:
    """Find all Jupyter notebooks under course directories.

    Args:
        repo_dir: Root directory of the upstream repo.

    Returns:
        Sorted list of notebook paths.
    """
    notebooks = sorted(repo_dir.glob("course_*/gdm_lab_*.ipynb"))
    return notebooks


def _load_overrides(overrides_path: Path) -> dict[str, dict]:
    """Load manual overrides from a YAML file.

    Args:
        overrides_path: Path to ``notebook_overrides.yml``.

    Returns:
        Dictionary mapping notebook relative paths to override fields.
    """
    if not overrides_path.exists():
        return {}

    with open(overrides_path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}

    overrides = {}
    for entry in data.get("overrides", []):
        path = entry.pop("path", None)
        if path:
            overrides[path] = entry
    return overrides


def generate_manifest(
    repo_dir: Path,
    overrides_path: Path,
) -> list[dict]:
    """Generate the notebook manifest.

    Args:
        repo_dir: Root directory of the upstream repo.
        overrides_path: Path to ``notebook_overrides.yml``.

    Returns:
        List of notebook entries with metadata.
    """
    overrides = _load_overrides(overrides_path)
    notebooks = _find_notebooks(repo_dir)

    manifest = []
    for nb_path in notebooks:
        relative_path = str(nb_path.relative_to(repo_dir))
        course = _extract_course(nb_path, repo_dir)
        gpu_required = _detect_gpu(nb_path)

        entry = {
            "path": relative_path,
            "course": course,
            "gpu_required": gpu_required,
            "timeout": _DEFAULT_TIMEOUT,
            "skip": False,
            "reason": "",
        }

        # Merge manual overrides.
        if relative_path in overrides:
            override = overrides[relative_path]
            # Map 'gpu' override key to 'gpu_required' entry key.
            if "gpu" in override:
                entry["gpu_required"] = override.pop("gpu")
            for key, value in override.items():
                if key in entry:
                    entry[key] = value

        manifest.append(entry)

    return manifest


def main() -> None:
    """Entry point for manifest generation."""
    parser = argparse.ArgumentParser(
        description="Generate a notebook testing manifest."
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
        help="Path to the overrides YAML file (default: notebook_overrides.yml).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("notebook_manifest.yml"),
        help="Path to write the generated manifest (default: notebook_manifest.yml).",
    )
    args = parser.parse_args()

    if not args.repo_dir.exists():
        print(f"Error: repo directory '{args.repo_dir}' does not exist.", file=sys.stderr)
        sys.exit(1)

    manifest = generate_manifest(args.repo_dir, args.overrides)

    output_data = {"notebooks": manifest}
    with open(args.output, "w", encoding="utf-8") as fh:
        yaml.dump(output_data, fh, default_flow_style=False, sort_keys=False)

    # Print summary.
    total = len(manifest)
    gpu_count = sum(1 for e in manifest if e["gpu_required"])
    cpu_count = total - gpu_count
    skip_count = sum(1 for e in manifest if e["skip"])

    print(f"Manifest generated: {args.output}")
    print(f"  Total notebooks: {total}")
    print(f"  CPU-only:        {cpu_count}")
    print(f"  GPU-required:    {gpu_count}")
    print(f"  Skipped:         {skip_count}")


if __name__ == "__main__":
    main()