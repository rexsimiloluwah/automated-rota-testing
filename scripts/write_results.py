#!/usr/bin/env python3
"""Parse test outputs and write structured JSON result files.

Each job in the CI pipeline calls this script to convert raw pytest
output and notebook check output into a standardized JSON format
that ``write_to_sheets.py`` can consume.

Usage:
    python scripts/write_results.py \
        --pytest-output results/pytest_output.txt \
        --notebook-summary results/notebook_summary.md \
        --output results/results.json \
        --job-name "Unit Tests"
"""

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path


def parse_pytest_output(path: Path) -> list[dict]:
    """Parse pytest verbose output into structured results.

    Args:
        path: Path to the pytest output file.

    Returns:
        List of dicts with keys: file, class, name, status.
    """
    if not path.exists():
        return []

    results = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            match = re.match(
                r"(\S+)::(\S+)::(\S+)\s+(PASSED|FAILED|ERROR)", line
            )
            if match:
                results.append({
                    "file": match.group(1),
                    "class": match.group(2),
                    "name": match.group(3),
                    "status": match.group(4),
                })
    return results


def parse_notebook_summary(path: Path) -> list[dict]:
    """Parse the notebook check markdown summary into structured results.

    Args:
        path: Path to the notebook summary markdown file.

    Returns:
        List of dicts with keys: status, notebook, details.
    """
    if not path.exists():
        return []

    results = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            match = re.match(
                r"\|\s*[^\|]*?(PASS|FAIL|SKIP)\s*\|\s*`([^`]+)`\s*\|\s*(.*?)\s*\|",
                line,
            )
            if match:
                results.append({
                    "status": match.group(1),
                    "notebook": match.group(2),
                    "details": match.group(3).strip(),
                })
    return results


def main() -> None:
    """Entry point."""
    parser = argparse.ArgumentParser(
        description="Parse test outputs into structured JSON."
    )
    parser.add_argument(
        "--pytest-output",
        type=Path,
        default=None,
        help="Path to pytest output file.",
    )
    parser.add_argument(
        "--notebook-summary",
        type=Path,
        default=None,
        help="Path to notebook summary markdown file.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Path to write the JSON results file.",
    )
    parser.add_argument(
        "--job-name",
        type=str,
        required=True,
        help="Name of the job (e.g. 'Unit Tests').",
    )
    args = parser.parse_args()

    result = {
        "job_name": args.job_name,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "pytest": [],
        "notebooks": [],
    }

    if args.pytest_output:
        result["pytest"] = parse_pytest_output(args.pytest_output)

    if args.notebook_summary:
        result["notebooks"] = parse_notebook_summary(args.notebook_summary)

    # Compute summary counts.
    pytest_passed = sum(1 for t in result["pytest"] if t["status"] == "PASSED")
    pytest_failed = sum(1 for t in result["pytest"] if t["status"] != "PASSED")
    nb_passed = sum(1 for n in result["notebooks"] if n["status"] == "PASS")
    nb_failed = sum(1 for n in result["notebooks"] if n["status"] == "FAIL")
    nb_skipped = sum(1 for n in result["notebooks"] if n["status"] == "SKIP")

    result["summary"] = {
        "pytest_total": len(result["pytest"]),
        "pytest_passed": pytest_passed,
        "pytest_failed": pytest_failed,
        "notebooks_total": len(result["notebooks"]),
        "notebooks_passed": nb_passed,
        "notebooks_failed": nb_failed,
        "notebooks_skipped": nb_skipped,
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2)

    print(f"Results written to {args.output}")


if __name__ == "__main__":
    main()
