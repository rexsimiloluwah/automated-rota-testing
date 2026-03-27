#!/usr/bin/env python3
"""Write test results to a Google Sheets spreadsheet.

Reads JSON result files produced by ``write_results.py`` and creates
a styled Google Sheets spreadsheet with one sheet per test job plus
a summary sheet. The spreadsheet is moved to a specified Drive folder.

Auth:
    - In GitHub Actions: uses Workload Identity Federation credentials
      (picked up automatically via ``google.auth.default()``).
    - Locally: uses OAuth credentials or service account impersonation.

Usage:
    python scripts/write_to_sheets.py \
        --results-dir all-results/ \
        --folder-id YOUR_DRIVE_FOLDER_ID \
        [--run-id 12345] [--run-url https://...]
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import google.auth
from googleapiclient.discovery import build as build_api

# Optional: gspread for easier spreadsheet manipulation.
try:
    import gspread
except ImportError:
    print("Error: gspread is required. Install with: pip install gspread")
    sys.exit(1)


# Header style: bold white text on dark blue.
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

# Data cell style.
DATA_FORMAT = {
    "wrapStrategy": "WRAP",
    "textFormat": {"fontSize": 10},
    "verticalAlignment": "TOP",
}

STATUS_EMOJIS = {
    "PASSED": "✅ PASSED",
    "FAILED": "❌ FAILED",
    "ERROR": "❌ ERROR",
    "PASS": "✅ PASS",
    "FAIL": "❌ FAIL",
    "SKIP": "⏭️ SKIP",
    "DISABLED": "⏸️ DISABLED",
}


def load_results(results_dir: Path) -> dict[str, dict]:
    """Load all JSON result files from the results directory.

    Args:
        results_dir: Directory containing artifact subdirectories,
            each with a ``results.json`` file.

    Returns:
        Dict mapping job names to result dicts.
    """
    results = {}
    for json_file in sorted(results_dir.rglob("results.json")):
        with open(json_file, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        results[data["job_name"]] = data
    return results


def style_sheet(spreadsheet, worksheet, num_rows, num_cols, col_widths=None):
    """Apply consistent styling to a worksheet.

    Args:
        spreadsheet: The gspread Spreadsheet object.
        worksheet: The gspread Worksheet object.
        num_rows: Total number of rows including header.
        num_cols: Number of columns.
        col_widths: Optional dict mapping column index to pixel width.
    """
    # Style header row.
    worksheet.format("1:1", HEADER_FORMAT)

    # Style data rows.
    if num_rows > 1:
        worksheet.format(f"2:{num_rows}", DATA_FORMAT)

    sheet_id = worksheet._properties["sheetId"]
    requests = []

    # Column widths.
    default_widths = {0: 200, 1: 350, 2: 200, 3: 200}
    widths = col_widths or default_widths
    for col_idx in range(num_cols):
        width = widths.get(col_idx, 200)
        requests.append({
            "updateDimensionProperties": {
                "range": {
                    "sheetId": sheet_id,
                    "dimension": "COLUMNS",
                    "startIndex": col_idx,
                    "endIndex": col_idx + 1,
                },
                "properties": {"pixelSize": width},
                "fields": "pixelSize",
            }
        })

    # Row heights.
    requests.append({
        "updateDimensionProperties": {
            "range": {
                "sheetId": sheet_id,
                "dimension": "ROWS",
                "startIndex": 0,
                "endIndex": 1,
            },
            "properties": {"pixelSize": 40},
            "fields": "pixelSize",
        }
    })
    if num_rows > 1:
        requests.append({
            "updateDimensionProperties": {
                "range": {
                    "sheetId": sheet_id,
                    "dimension": "ROWS",
                    "startIndex": 1,
                    "endIndex": num_rows,
                },
                "properties": {"pixelSize": 35},
                "fields": "pixelSize",
            }
        })

    # Freeze header row.
    requests.append({
        "updateSheetProperties": {
            "properties": {
                "sheetId": sheet_id,
                "gridProperties": {"frozenRowCount": 1},
            },
            "fields": "gridProperties.frozenRowCount",
        }
    })

    spreadsheet.batch_update({"requests": requests})


def write_pytest_sheet(spreadsheet, title, pytest_results):
    """Write a pytest results sheet.

    Args:
        spreadsheet: The gspread Spreadsheet object.
        title: Sheet title.
        pytest_results: List of pytest result dicts.

    Returns:
        The created worksheet.
    """
    rows = [["Status", "Test File", "Test Class", "Test Name"]]
    for test in pytest_results:
        status = STATUS_EMOJIS.get(test["status"], test["status"])
        rows.append([status, test["file"], test["class"], test["name"]])

    if len(rows) == 1:
        rows.append(["", "No tests found", "", ""])

    ws = spreadsheet.add_worksheet(title=title, rows=len(rows), cols=4)
    ws.update(f"A1:D{len(rows)}", rows)
    style_sheet(
        spreadsheet, ws, len(rows), 4,
        {0: 150, 1: 350, 2: 250, 3: 250},
    )
    return ws


def write_notebook_sheet(spreadsheet, title, notebook_results):
    """Write a notebook check results sheet.

    Args:
        spreadsheet: The gspread Spreadsheet object.
        title: Sheet title.
        notebook_results: List of notebook result dicts.

    Returns:
        The created worksheet.
    """
    rows = [["Status", "Notebook", "Course", "Details"]]
    for nb in notebook_results:
        status = STATUS_EMOJIS.get(nb["status"], nb["status"])
        name = nb["notebook"]
        course = name.split("_lab_")[0].replace("gdm_", "") if "_lab_" in name else ""
        rows.append([status, name, course, nb.get("details", "")])

    if len(rows) == 1:
        rows.append(["", "No notebooks checked", "", ""])

    ws = spreadsheet.add_worksheet(title=title, rows=len(rows), cols=4)
    ws.update(f"A1:D{len(rows)}", rows)
    style_sheet(
        spreadsheet, ws, len(rows), 4,
        {0: 150, 1: 400, 2: 120, 3: 300},
    )
    return ws


def write_summary_sheet(spreadsheet, results, run_id, run_url):
    """Write the summary sheet.

    Args:
        spreadsheet: The gspread Spreadsheet object.
        results: Dict mapping job names to result dicts.
        run_id: GitHub Actions run ID (or empty).
        run_url: GitHub Actions run URL (or empty).

    Returns:
        The created worksheet.
    """
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    rows = [
        ["Metric", "Value", "Notes"],
        ["Run Date", now, ""],
        ["Run ID", str(run_id), run_url],
        ["Python Version", "3.12.13", "Colab runtime"],
        ["", "", ""],
    ]

    for job_name, data in results.items():
        s = data.get("summary", {})
        rows.append([f"--- {job_name} ---", "", ""])

        if s.get("pytest_total", 0) > 0:
            pytest_status = "✅" if s["pytest_failed"] == 0 else "❌"
            rows.append([
                "  Pytest",
                f"{pytest_status} {s['pytest_passed']}/{s['pytest_total']} passed",
                f"{s['pytest_failed']} failed" if s["pytest_failed"] else "",
            ])

        if s.get("notebooks_total", 0) > 0:
            nb_status = "✅" if s["notebooks_failed"] == 0 else "❌"
            rows.append([
                "  Notebooks",
                f"{nb_status} {s['notebooks_passed']} passed",
                f"{s['notebooks_failed']} failed, {s['notebooks_skipped']} skipped",
            ])

    ws = spreadsheet.add_worksheet(title="Summary", rows=len(rows), cols=3)
    ws.update(f"A1:C{len(rows)}", rows)
    style_sheet(
        spreadsheet, ws, len(rows), 3,
        {0: 250, 1: 300, 2: 300},
    )
    return ws


def create_spreadsheet(
    results: dict[str, dict],
    folder_id: str,
    run_id: str = "",
    run_url: str = "",
) -> str:
    """Create a Google Sheets spreadsheet with all test results.

    Args:
        results: Dict mapping job names to result dicts.
        folder_id: Google Drive folder ID.
        run_id: GitHub Actions run ID.
        run_url: GitHub Actions run URL.

    Returns:
        URL of the created spreadsheet.
    """
    creds, _ = google.auth.default(scopes=[
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ])
    gc = gspread.authorize(creds)

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    title = f"Notebook Test Results - {now}"
    if run_id:
        title += f" - Run #{run_id}"

    print(f"Creating spreadsheet: {title}")
    sh = gc.create(title)

    # Move to the target folder.
    drive = build_api("drive", "v3", credentials=creds)
    drive.files().update(
        fileId=sh.id,
        addParents=folder_id,
        removeParents="root",
        fields="id, parents",
    ).execute()

    # Write sheets for each job.
    sheets_created = []

    for job_name, data in results.items():
        if data.get("pytest"):
            ws = write_pytest_sheet(sh, f"{job_name} - Pytest", data["pytest"])
            sheets_created.append(ws.title)

        if data.get("notebooks"):
            ws = write_notebook_sheet(
                sh, f"{job_name} - Notebooks", data["notebooks"]
            )
            sheets_created.append(ws.title)

    # GPU placeholder if not present.
    if "GPU Tests" not in results:
        ws = sh.add_worksheet(title="GPU Tests", rows=3, cols=3)
        ws.update("A1:C2", [
            ["Status", "Notebook", "Details"],
            ["⏸️ DISABLED", "GPU tests not yet enabled", "Pending WIF setup"],
        ])
        style_sheet(sh, ws, 2, 3, {0: 150, 1: 300, 2: 300})

    # Summary sheet.
    write_summary_sheet(sh, results, run_id, run_url)

    # Delete the default empty Sheet1.
    default_sheet = sh.sheet1
    if default_sheet.title == "Sheet1" and len(sh.worksheets()) > 1:
        sh.del_worksheet(default_sheet)

    print(f"Spreadsheet URL: {sh.url}")
    return sh.url


def main() -> None:
    """Entry point."""
    parser = argparse.ArgumentParser(
        description="Write test results to Google Sheets."
    )
    parser.add_argument(
        "--results-dir",
        type=Path,
        required=True,
        help="Directory containing result JSON files.",
    )
    parser.add_argument(
        "--folder-id",
        type=str,
        required=True,
        help="Google Drive folder ID.",
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
    url = create_spreadsheet(
        results, args.folder_id, args.run_id, args.run_url
    )
    print(f"Done: {url}")


if __name__ == "__main__":
    main()
