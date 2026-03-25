#!/usr/bin/env python3
"""Execute a single Jupyter notebook and report results.

Wraps ``papermill`` to run a notebook top-to-bottom, capturing per-cell
errors. Optionally strips ``%%capture`` / ``!pip install`` cells that
are only needed in Colab.

Usage:
    python scripts/run_notebook.py <notebook_path> \
        --output-dir results/ [--timeout 600] [--strip-installs]
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path

import papermill as pm


def _strip_install_lines(notebook_path: Path) -> None:
    """Remove ``!pip install`` and ``%%capture`` lines from code cells.

    Only the individual lines are removed, preserving all other code
    (imports, variable assignments, etc.) in the same cell.

    Args:
        notebook_path: Path to the ``.ipynb`` file (modified in-place).
    """
    with open(notebook_path, "r", encoding="utf-8") as fh:
        nb = json.load(fh)

    modified = False
    for cell in nb.get("cells", []):
        if cell.get("cell_type") != "code":
            continue
        new_lines = []
        for line in cell.get("source", []):
            if re.match(r"\s*!pip\s+install", line):
                continue
            if re.match(r"\s*%%capture", line):
                continue
            new_lines.append(line)
        if len(new_lines) != len(cell.get("source", [])):
            cell["source"] = new_lines
            modified = True

    if modified:
        with open(notebook_path, "w", encoding="utf-8") as fh:
            json.dump(nb, fh, indent=1, ensure_ascii=False)
            fh.write("\n")


def run_notebook(
    notebook_path: Path,
    output_dir: Path,
    timeout: int,
    strip_installs: bool,
) -> bool:
    """Execute a notebook via papermill.

    Args:
        notebook_path: Path to the ``.ipynb`` file.
        output_dir: Directory for the executed notebook output.
        timeout: Per-cell execution timeout in seconds.
        strip_installs: If True, remove pip install cells before running.

    Returns:
        True if execution succeeded, False otherwise.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / notebook_path.name

    if strip_installs:
        _strip_install_lines(notebook_path)

    print(f"==> Running: {notebook_path.name}")
    start = time.time()

    try:
        pm.execute_notebook(
            str(notebook_path),
            str(output_path),
            kernel_name="python3",
            cwd=str(notebook_path.parent),
            request_save_on_cell_execute=True,
            execution_timeout=timeout,
        )
        elapsed = time.time() - start
        print(f"    PASSED ({elapsed:.1f}s)")
        return True

    except pm.PapermillExecutionError as exc:
        elapsed = time.time() - start
        print(f"    FAILED ({elapsed:.1f}s)")
        print(f"    Cell {exc.cell_index}: {exc.ename}: {exc.evalue}")
        return False

    except Exception as exc:
        elapsed = time.time() - start
        print(f"    ERROR ({elapsed:.1f}s): {exc}")
        return False


def main() -> None:
    """Entry point for notebook execution."""
    parser = argparse.ArgumentParser(
        description="Execute a Jupyter notebook via papermill."
    )
    parser.add_argument(
        "notebook",
        type=Path,
        help="Path to the .ipynb file.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("results"),
        help="Directory for executed notebook output (default: results/).",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=600,
        help="Per-cell execution timeout in seconds (default: 600).",
    )
    parser.add_argument(
        "--strip-installs",
        action="store_true",
        help="Strip pip install / %%capture cells before execution.",
    )
    args = parser.parse_args()

    if not args.notebook.exists():
        print(f"Error: '{args.notebook}' not found.", file=sys.stderr)
        sys.exit(1)

    success = run_notebook(
        args.notebook,
        args.output_dir,
        args.timeout,
        args.strip_installs,
    )
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()