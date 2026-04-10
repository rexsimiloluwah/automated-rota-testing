#!/usr/bin/env python3
"""Inject solution code into notebook activity cells.

Notebooks contain placeholder activity cells (with ``...`` or blank
assignments) and a "## Solutions" section at the bottom with reference
implementations. This script replaces only the function definitions
within activity cells, preserving any surrounding code (variable
declarations, loops, print statements, etc.).

Usage:
    python scripts/inject_solutions.py <notebook_path> [--output <output_path>]

If ``--output`` is omitted the notebook is modified in-place.
"""

import argparse
import json
import re
import sys
from pathlib import Path


def _find_solutions_boundary(cells: list[dict]) -> int | None:
    """Return the index of the '## Solutions' markdown cell.

    Args:
        cells: List of notebook cell dicts.

    Returns:
        The cell index, or None if no solutions section exists.
    """
    for i, cell in enumerate(cells):
        if cell.get("cell_type") != "markdown":
            continue
        source = "".join(cell.get("source", []))
        if re.search(r"^##\s+Solutions?\s*$", source, re.MULTILINE):
            return i
    return None


def _extract_function_name(source: str) -> str | None:
    """Extract the first function name defined in a code cell.

    Args:
        source: The joined source code of a cell.

    Returns:
        The function name, or None if no function definition is found.
    """
    match = re.search(r"^\s*def\s+(\w+)\s*\(", source, re.MULTILINE)
    if match:
        return match.group(1)
    return None


def _extract_activity_number(source: str) -> int | None:
    """Extract the activity number from a markdown heading.

    Matches patterns like ``## Coding Activity 1``,
    ``### Activity 2``, etc.

    Args:
        source: The joined source of a markdown cell.

    Returns:
        The activity number, or None if not found.
    """
    match = re.search(
        r"#{2,3}\s+(?:Coding\s+)?Activity\s+(\d+)", source, re.IGNORECASE
    )
    if match:
        return int(match.group(1))
    return None


def _extract_function_span(source: str) -> tuple[int, int] | None:
    """Find the start and end line indices of the first function definition.

    Detects the function by the ``def`` keyword and determines where it
    ends by looking for the next line at the same or lower indentation
    level (or end of source).

    Args:
        source: The joined source code.

    Returns:
        A (start, end) tuple of line indices (0-based, end exclusive),
        or None if no function is found.
    """
    lines = source.splitlines(keepends=True)
    func_start = None
    func_indent = None
    signature_complete = False

    for i, line in enumerate(lines):
        stripped = line.rstrip()

        # Find the def line.
        if func_start is None:
            match = re.match(r"^(\s*)def\s+\w+\s*\(", line)
            if match:
                func_start = i
                func_indent = len(match.group(1))
                # Check if the signature is complete on this line.
                signature_complete = stripped.endswith(":")
            continue

        # Wait for the multi-line signature to close (line ending with ':').
        if not signature_complete:
            if stripped.endswith(":"):
                signature_complete = True
            continue

        # Skip blank lines inside the function.
        if stripped == "":
            continue

        leading = len(line) - len(line.lstrip())

        # A comment or code at the function's indent level (or less)
        # means we've left the function body.
        if leading <= func_indent:
            return (func_start, i)

    # Function goes to the end of the cell.
    if func_start is not None:
        return (func_start, len(lines))

    return None


def _extract_solution_function(source: str) -> str | None:
    """Extract just the function definition from a solution cell.

    Strips any leading comment lines (e.g. ``# Complete implementation
    of ...``) that precede the ``def`` line.

    Args:
        source: The joined source of a solution code cell.

    Returns:
        The function source code, or None if no function found.
    """
    lines = source.splitlines(keepends=True)

    # Find where def starts.
    def_start = None
    for i, line in enumerate(lines):
        if re.match(r"^\s*def\s+\w+\s*\(", line):
            def_start = i
            break

    if def_start is None:
        return None

    return "".join(lines[def_start:])


def _replace_placeholder_region(
    cell_source: str, solution_source: str
) -> str:
    """Replace the placeholder region in a cell with solution code.

    Finds the first placeholder marker (``# Add your code``,
    ``= ...``, ``= []``, etc.) and replaces from that line through
    any subsequent blank or placeholder lines with the solution code.
    Code before and after the placeholder region is preserved.

    If no clear placeholder marker is found, the entire cell is
    replaced as a fallback.

    Args:
        cell_source: The original cell source code.
        solution_source: The solution source code.

    Returns:
        The modified cell source.
    """
    placeholder_patterns = [
        re.compile(r"#\s*Add your code", re.IGNORECASE),
        re.compile(r"#\s*Your code here", re.IGNORECASE),
        re.compile(r"#\s*Change code here", re.IGNORECASE),
        re.compile(r"(?<![!=<>])=\s*\.\.\."),
        re.compile(r"(?<![!=<>])=\s*\[\]"),
        re.compile(r"(?<![!=<>])=\s*$"),
        re.compile(r"(?<![!=<>])=\s*#"),
    ]

    lines = cell_source.splitlines(keepends=True)
    placeholder_start = None

    for i, line in enumerate(lines):
        for pattern in placeholder_patterns:
            if pattern.search(line):
                placeholder_start = i
                break
        if placeholder_start is not None:
            break

    if placeholder_start is None:
        # No marker found — fall back to full replacement.
        return solution_source

    # Keep everything before the placeholder line.
    before = lines[:placeholder_start]

    # Find where the placeholder region ends: skip blank lines and
    # subsequent placeholder lines after the marker.
    placeholder_end = placeholder_start + 1
    while placeholder_end < len(lines):
        stripped = lines[placeholder_end].strip()
        if stripped == "":
            placeholder_end += 1
            continue
        # Check if this line is also a placeholder.
        is_placeholder_line = False
        for pattern in placeholder_patterns:
            if pattern.search(lines[placeholder_end]):
                is_placeholder_line = True
                break
        if is_placeholder_line:
            placeholder_end += 1
            continue
        break

    # Keep everything after the placeholder region.
    after = lines[placeholder_end:]

    # Ensure solution ends with newline.
    if not solution_source.endswith("\n"):
        solution_source += "\n"

    return "".join(before) + solution_source + "".join(after)


def _replace_function_in_cell(
    cell_source: str, solution_func: str
) -> str:
    """Replace a function definition in a cell with the solution version.

    Preserves all code before and after the function definition.

    Args:
        cell_source: The original cell source code.
        solution_func: The solution function source code.

    Returns:
        The modified cell source with the function replaced.
    """
    span = _extract_function_span(cell_source)
    if span is None:
        return cell_source

    lines = cell_source.splitlines(keepends=True)
    start, end = span

    # Ensure the solution function ends with a newline.
    if not solution_func.endswith("\n"):
        solution_func += "\n"

    # Rebuild: code before function + solution function + code after function.
    before = lines[:start]
    after = lines[end:]

    return "".join(before) + solution_func + "".join(after)


def _collect_solution_cells(
    cells: list[dict], boundary: int
) -> list[dict]:
    """Collect solution code cells paired with their activity numbers.

    Args:
        cells: Full list of notebook cells.
        boundary: Index of the '## Solutions' cell.

    Returns:
        List of dicts with keys ``activity_number``, ``function_name``,
        and ``source`` (the full solution code as a string).
    """
    solutions = []
    current_activity_number = None

    for cell in cells[boundary + 1:]:
        source = "".join(cell.get("source", []))

        if cell.get("cell_type") == "markdown":
            num = _extract_activity_number(source)
            if num is not None:
                current_activity_number = num
            continue

        if cell.get("cell_type") == "code" and current_activity_number is not None:
            func_name = _extract_function_name(source)
            solutions.append({
                "activity_number": current_activity_number,
                "function_name": func_name,
                "source": source,
            })

    return solutions


def _find_activity_cells(
    cells: list[dict], boundary: int
) -> list[dict]:
    """Find activity cells above the solutions boundary.

    An activity cell is a code cell that contains placeholders like
    ``= ...``, literal ``...`` on its own, or ``# Add your code here``.

    Args:
        cells: Full list of notebook cells.
        boundary: Index of the '## Solutions' cell.

    Returns:
        List of dicts with keys ``index``, ``function_name``, and
        ``activity_number`` (inferred from preceding markdown).
    """
    activities = []
    last_activity_number = 0

    for i, cell in enumerate(cells[:boundary]):
        source = "".join(cell.get("source", []))

        if cell.get("cell_type") == "markdown":
            num = _extract_activity_number(source)
            if num is not None:
                last_activity_number = num
            continue

        if cell.get("cell_type") != "code":
            continue

        is_placeholder = (
            re.search(r"(?<![!=<>])=\s*\.\.\.", source)
            or re.search(r"#\s*Add your code here", source, re.IGNORECASE)
            or re.search(r"#\s*Add your code", source, re.IGNORECASE)
            or re.search(r"#\s*Your code here", source, re.IGNORECASE)
            or re.search(r"#\s*Change code here", source, re.IGNORECASE)
            or re.search(r"(?<![!=<>])=\s*$", source, re.MULTILINE)
            or re.search(r"(?<![!=<>])=\s*#", source)
            or re.search(r"(?<![!=<>])=\s*\[\]", source)
            or source.strip() == "..."
        )
        if not is_placeholder:
            continue

        func_name = _extract_function_name(source)
        activities.append({
            "index": i,
            "function_name": func_name,
            "activity_number": last_activity_number,
        })

    return activities


def _source_to_lines(source: str) -> list[str]:
    """Convert a source string to a list of lines for notebook JSON.

    Notebook JSON stores source as a list of strings where each string
    includes its trailing newline, except possibly the last line.

    Args:
        source: The source code as a single string.

    Returns:
        List of line strings suitable for notebook cell ``source`` field.
    """
    if not source:
        return []
    lines = source.splitlines(keepends=True)
    return lines


def inject_solutions(notebook_path: Path) -> dict:
    """Inject solutions into a notebook's activity cells.

    For cells that contain a function definition, only the function is
    replaced — surrounding code (variable declarations, loops, etc.) is
    preserved. For cells without a function, the entire cell is replaced
    with the solution.

    Args:
        notebook_path: Path to the ``.ipynb`` file.

    Returns:
        The modified notebook dict. If the notebook has no solutions
        section, it is returned unmodified.
    """
    with open(notebook_path, "r", encoding="utf-8") as fh:
        nb = json.load(fh)

    cells = nb.get("cells", [])
    boundary = _find_solutions_boundary(cells)

    if boundary is None:
        return nb

    solutions = _collect_solution_cells(cells, boundary)
    activities = _find_activity_cells(cells, boundary)

    # Build lookup: try function name first, fall back to activity number.
    solution_by_func: dict[str, str] = {}
    solution_by_number: dict[int, list[str]] = {}
    for sol in solutions:
        if sol["function_name"]:
            solution_by_func[sol["function_name"]] = sol["source"]
        solution_by_number.setdefault(
            sol["activity_number"], []
        ).append(sol["source"])

    # Track which number-based solutions have been consumed.
    number_consumed: dict[int, int] = {}

    replacements = 0
    for activity in activities:
        func_name = activity["function_name"]
        act_num = activity["activity_number"]
        idx = activity["index"]
        cell_source = "".join(cells[idx]["source"])

        solution_source = None

        # Match by function name first.
        if func_name and func_name in solution_by_func:
            solution_source = solution_by_func[func_name]
        # Fall back to matching by activity number.
        elif act_num in solution_by_number:
            consumed = number_consumed.get(act_num, 0)
            candidates = solution_by_number[act_num]
            if consumed < len(candidates):
                solution_source = candidates[consumed]
                number_consumed[act_num] = consumed + 1

        if solution_source is None:
            continue

        # If both the activity and solution have a function, replace
        # only the function definition within the cell.
        if func_name and _extract_function_name(solution_source):
            solution_func = _extract_solution_function(solution_source)
            if solution_func:
                new_source = _replace_function_in_cell(
                    cell_source, solution_func
                )
                cells[idx]["source"] = _source_to_lines(new_source)
                replacements += 1
                continue

        # For non-function activities, try to preserve surrounding code
        # by replacing only the placeholder region.
        new_source = _replace_placeholder_region(
            cell_source, solution_source
        )
        cells[idx]["source"] = _source_to_lines(new_source)
        replacements += 1

    nb["cells"] = cells
    print(
        f"  {notebook_path.name}: {replacements} activity cell(s) replaced"
    )
    return nb


def main() -> None:
    """Entry point for solution injection."""
    parser = argparse.ArgumentParser(
        description="Inject solutions into notebook activity cells."
    )
    parser.add_argument(
        "notebook",
        type=Path,
        help="Path to the .ipynb file.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output path. If omitted, modifies the notebook in-place.",
    )
    args = parser.parse_args()

    if not args.notebook.exists():
        print(f"Error: '{args.notebook}' not found.", file=sys.stderr)
        sys.exit(1)

    nb = inject_solutions(args.notebook)

    output_path = args.output or args.notebook
    with open(output_path, "w", encoding="utf-8") as fh:
        json.dump(nb, fh, indent=1, ensure_ascii=False)
        fh.write("\n")

    print(f"  Written to: {output_path}")


if __name__ == "__main__":
    main()
