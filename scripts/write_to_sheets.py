#!/usr/bin/env python3
"""Append test results to a Google Sheets spreadsheet.

Reads JSON result files produced by ``write_results.py`` and appends
rows to a pre-existing Google Sheets spreadsheet. The spreadsheet has
four permanent sheets — one per test type. Each workflow run appends
new rows with a timestamp and run ID, making it easy to filter and
track trends over time.

Spreadsheet structure:
    - "Unit Tests"      — one row per pytest test per run
    - "Notebook Checks" — one row per notebook per run
    - "Smoke Tests"     — one row per pytest test per run
    - "GPU Tests"       — one row per notebook/test per run
    - "Run Summary"     — one row per run with pass/fail counts

Auth:
    - In GitHub Actions: uses WIF credentials via google.auth.default().
    - Locally: uses OAuth or service account impersonation.

Usage:
    python scripts/write_to_sheets.py \
        --results-dir all-results/ \
        --spreadsheet-id YOUR_SPREADSHEET_ID \
        [--run-id 12345] [--run-url https://...]
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import google.auth

try:
    import gspread
except ImportError:
    print("Error: gspread is required. Install with: pip install gspread")
    sys.exit(1)


STATUS_EMOJIS = {
    "PASSED": "✅ PASSED",
    "FAILED": "❌ FAILED",
    "ERROR": "❌ ERROR",
    "PASS": "✅ PASS",
    "FAIL": "❌ FAIL",
    "SKIP": "⏭️ SKIP",
}

# Sheet names and their headers.
SHEETS = {
    "Unit Tests": [
        "Run Date", "Run ID", "Status", "Test File",
        "Test Class", "Test Name",
    ],
    "Notebook Checks": [
        "Run Date", "Run ID", "Status", "Notebook",
        "Course", "Details",
    ],
    "Smoke Tests": [
        "Run Date", "Run ID", "Status", "Test File",
        "Test Class", "Test Name",
    ],
    "GPU Tests": [
        "Run Date", "Run ID", "Status", "Test/Notebook",
        "Type", "Details",
    ],
    "Run Summary": [
        "Run Date", "Run ID", "Run URL", "Job",
        "Pytest Passed", "Pytest Failed",
        "Notebooks Passed", "Notebooks Failed", "Notebooks Skipped",
    ],
}

HEADER_FORMAT = {
    "textFormat": {
        "bold": True,
        "fontSize": 11,
        "foregroundColorStyle": {
            "rgbColor": {"red": 1, "green": 1, "blue": 1}
        },
    },
    "backgroundColor": {"red": 0.2, "green": 0.3, "blue": 0.5},
    "horizontalAlignment": "CENTER",
    "wrapStrategy": "WRAP",
}


def load_results(results_dir: Path) -> dict[str, dict]:
    """Load all JSON result files from the results directory.

    Args:
        results_dir: Directory containing artifact subdirectories.

    Returns:
        Dict mapping job names to result dicts.
    """
    results = {}
    for json_file in sorted(results_dir.rglob("results.json")):
        with open(json_file, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        results[data["job_name"]] = data
    return results


def ensure_sheet(spreadsheet, sheet_name):
    """Get or create a worksheet with the correct headers.

    Args:
        spreadsheet: The gspread Spreadsheet object.
        sheet_name: Name of the sheet.

    Returns:
        The gspread Worksheet object.
    """
    headers = SHEETS[sheet_name]

    try:
        ws = spreadsheet.worksheet(sheet_name)
    except gspread.exceptions.WorksheetNotFound:
        ws = spreadsheet.add_worksheet(
            title=sheet_name, rows=1, cols=len(headers)
        )
        ws.update(f"A1:{chr(64 + len(headers))}1", [headers])
        ws.format("1:1", HEADER_FORMAT)

        # Freeze header row.
        sheet_id = ws._properties["sheetId"]
        spreadsheet.batch_update({"requests": [{
            "updateSheetProperties": {
                "properties": {
                    "sheetId": sheet_id,
                    "gridProperties": {"frozenRowCount": 1},
                },
                "fields": "gridProperties.frozenRowCount",
            }
        }]})

    return ws


def append_pytest_rows(ws, run_date, run_id, pytest_results):
    """Append pytest result rows to a worksheet.

    Args:
        ws: The gspread Worksheet object.
        run_date: Formatted run date string.
        run_id: GitHub Actions run ID.
        pytest_results: List of pytest result dicts.
    """
    if not pytest_results:
        return

    rows = []
    for test in pytest_results:
        status = STATUS_EMOJIS.get(test["status"], test["status"])
        rows.append([
            run_date, run_id, status,
            test["file"], test["class"], test["name"],
        ])

    ws.append_rows(rows, value_input_option="RAW")


def append_notebook_rows(ws, run_date, run_id, notebook_results):
    """Append notebook check result rows to a worksheet.

    Args:
        ws: The gspread Worksheet object.
        run_date: Formatted run date string.
        run_id: GitHub Actions run ID.
        notebook_results: List of notebook result dicts.
    """
    if not notebook_results:
        return

    rows = []
    for nb in notebook_results:
        status = STATUS_EMOJIS.get(nb["status"], nb["status"])
        name = nb["notebook"]
        course = (
            name.split("_lab_")[0].replace("gdm_", "")
            if "_lab_" in name else ""
        )
        rows.append([
            run_date, run_id, status,
            name, course, nb.get("details", ""),
        ])

    ws.append_rows(rows, value_input_option="RAW")


def append_gpu_rows(ws, run_date, run_id, data):
    """Append GPU test result rows to a worksheet.

    Args:
        ws: The gspread Worksheet object.
        run_date: Formatted run date string.
        run_id: GitHub Actions run ID.
        data: Result dict for the GPU job.
    """
    rows = []

    for test in data.get("pytest", []):
        status = STATUS_EMOJIS.get(test["status"], test["status"])
        rows.append([
            run_date, run_id, status,
            test["name"], "pytest", "",
        ])

    for nb in data.get("notebooks", []):
        status = STATUS_EMOJIS.get(nb["status"], nb["status"])
        rows.append([
            run_date, run_id, status,
            nb["notebook"], "notebook", nb.get("details", ""),
        ])

    if rows:
        ws.append_rows(rows, value_input_option="RAW")


def append_summary_row(ws, run_date, run_id, run_url, job_name, summary):
    """Append a summary row for one job.

    Args:
        ws: The gspread Worksheet object.
        run_date: Formatted run date string.
        run_id: GitHub Actions run ID.
        run_url: GitHub Actions run URL.
        job_name: Name of the job.
        summary: Summary dict with pass/fail counts.
    """
    row = [
        run_date, run_id, run_url, job_name,
        summary.get("pytest_passed", ""),
        summary.get("pytest_failed", ""),
        summary.get("notebooks_passed", ""),
        summary.get("notebooks_failed", ""),
        summary.get("notebooks_skipped", ""),
    ]
    ws.append_rows([row], value_input_option="RAW")


def write_results(
    spreadsheet_id: str,
    results: dict[str, dict],
    run_id: str = "",
    run_url: str = "",
) -> None:
    """Append all test results to the spreadsheet.

    Args:
        spreadsheet_id: Google Sheets spreadsheet ID.
        results: Dict mapping job names to result dicts.
        run_id: GitHub Actions run ID.
        run_url: GitHub Actions run URL.
    """
    creds, _ = google.auth.default(scopes=[
        "https://www.googleapis.com/auth/spreadsheets",
    ])
    gc = gspread.authorize(creds)

    sh = gc.open_by_key(spreadsheet_id)
    run_date = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # Job name to sheet name mapping.
    job_sheet_map = {
        "Unit Tests": "Unit Tests",
        "Notebook Imports": "Notebook Checks",
        "Smoke Tests": "Smoke Tests",
        "GPU Tests": "GPU Tests",
    }

    # Write results for each job.
    for job_name, data in results.items():
        sheet_name = job_sheet_map.get(job_name)
        if not sheet_name:
            print(f"  Unknown job: {job_name}, skipping.")
            continue

        print(f"  Writing {job_name} results...")

        if sheet_name == "GPU Tests":
            ws = ensure_sheet(sh, sheet_name)
            append_gpu_rows(ws, run_date, run_id, data)
        elif data.get("pytest"):
            ws = ensure_sheet(sh, sheet_name)
            append_pytest_rows(ws, run_date, run_id, data["pytest"])
        if data.get("notebooks") and sheet_name != "GPU Tests":
            ws = ensure_sheet(sh, "Notebook Checks")
            append_notebook_rows(ws, run_date, run_id, data["notebooks"])

        # Summary row.
        summary_ws = ensure_sheet(sh, "Run Summary")
        append_summary_row(
            summary_ws, run_date, run_id, run_url,
            job_name, data.get("summary", {}),
        )

    print("  Done.")


def main() -> None:
    """Entry point."""
    parser = argparse.ArgumentParser(
        description="Append test results to Google Sheets."
    )
    parser.add_argument(
        "--results-dir",
        type=Path,
        required=True,
        help="Directory containing result JSON files.",
    )
    parser.add_argument(
        "--spreadsheet-id",
        type=str,
        required=True,
        help="Google Sheets spreadsheet ID.",
    )
    parser.add_argument(
        "--run-id",
        type=str,
        default="",
        help="GitHub Actions run ID.",
    )
    parser.add_argument(
        "--run-url",
        type=str,
        default="",
        help="GitHub Actions run URL.",
    )
    args = parser.parse_args()

    if not args.results_dir.exists():
        print(f"Error: {args.results_dir} does not exist.")
        sys.exit(1)

    results = load_results(args.results_dir)
    if not results:
        print("No result files found.")
        sys.exit(1)

    print(f"Found results for: {', '.join(results.keys())}")
    write_results(
        args.spreadsheet_id, results, args.run_id, args.run_url
    )


if __name__ == "__main__":
    main()
