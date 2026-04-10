#!/usr/bin/env python3
"""Validate notebook code cells for syntax errors and broken imports.

Parses every code cell with ``ast.parse`` and attempts to resolve all
import statements. Does **not** execute the notebook.

Usage:
    python scripts/check_notebook.py <notebook_path>
    python scripts/check_notebook.py --all --repo-dir ai-foundations

Exit codes:
    0  All checks passed.
    1  One or more checks failed.
"""

import argparse
import ast
import json
import re
import shlex
import subprocess
import sys
from pathlib import Path


def _extract_code_cells(notebook_path: Path) -> list[tuple[int, str]]:
    """Extract code cells from a notebook, skipping install lines.

    Args:
        notebook_path: Path to the ``.ipynb`` file.

    Returns:
        List of (cell_index, source) tuples.
    """
    with open(notebook_path, "r", encoding="utf-8") as fh:
        nb = json.load(fh)

    cells = []
    for i, cell in enumerate(nb.get("cells", [])):
        if cell.get("cell_type") != "code":
            continue
        lines = cell.get("source", [])
        # Filter out shell commands and magics that aren't valid Python.
        filtered = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("!"):
                continue
            if stripped.startswith("%%"):
                continue
            if stripped.startswith("%"):
                continue
            # Skip Colab form annotations.
            if re.match(r".*#\s*@(title|param|markdown)", stripped):
                continue
            filtered.append(line)
        source = "".join(filtered).strip()
        if not source:
            continue
        # Skip activity placeholder cells — these are intentionally
        # incomplete code that students fill in.
        if _is_placeholder_cell(source):
            continue
        cells.append((i, source))
    return cells


def _is_placeholder_cell(source: str) -> bool:
    """Detect if a cell contains student activity placeholders.

    Args:
        source: The joined source code of a cell.

    Returns:
        True if the cell contains placeholder patterns that would cause
        syntax errors by design.
    """
    return bool(
        re.search(r"(?<![!=<>])=\s*\.\.\.", source)
        or re.search(r"#\s*Add your code here", source)
        or re.search(r"#\s*Your code here", source, re.IGNORECASE)
        or source.strip() == "..."
        or re.search(r"=\s*$", source, re.MULTILINE)
        or re.search(r"=\s*#", source)
    )


def check_syntax(notebook_path: Path) -> list[str]:
    """Check all code cells for syntax errors.

    Args:
        notebook_path: Path to the ``.ipynb`` file.

    Returns:
        List of error messages. Empty if all cells parse successfully.
    """
    errors = []
    cells = _extract_code_cells(notebook_path)

    for cell_idx, source in cells:
        try:
            ast.parse(source)
        except SyntaxError as exc:
            errors.append(
                f"  Cell {cell_idx}: SyntaxError at line {exc.lineno}: "
                f"{exc.msg}"
            )
    return errors


def _extract_imports(source: str) -> list[tuple[str, str]]:
    """Extract import statements from source code.

    Returns both the top-level module name (for dedup) and the full
    import statement to execute (to catch submodule-level errors).

    Args:
        source: Python source code.

    Returns:
        List of (top_level_module, full_import_statement) tuples.
        For ``from ai_foundations import generation`` this returns
        ``("ai_foundations", "from ai_foundations import generation")``.
    """
    imports: list[tuple[str, str]] = []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return imports

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                imports.append((top, f"import {alias.name}"))
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                top = node.module.split(".")[0]
                names = ", ".join(a.name for a in node.names)
                imports.append(
                    (top, f"from {node.module} import {names}")
                )
    return imports


def _extract_pip_installs(notebook_path: Path) -> list[str]:
    """Extract ``!pip install`` commands from notebook code cells.

    Args:
        notebook_path: Path to the ``.ipynb`` file.

    Returns:
        List of pip install argument strings in cell order.
    """
    with open(notebook_path, "r", encoding="utf-8") as fh:
        nb = json.load(fh)

    commands = []
    for cell in nb.get("cells", []):
        if cell.get("cell_type") != "code":
            continue
        for line in cell.get("source", []):
            stripped = line.strip()
            match = re.match(r"^!pip\s+install\s+(.+)", stripped)
            if match:
                commands.append(match.group(1))
    return commands


def _pip_freeze() -> dict[str, str]:
    """Snapshot currently installed package versions.

    Returns:
        Dictionary mapping package name to version string.
    """
    result = subprocess.run(
        [sys.executable, "-m", "pip", "freeze"],
        capture_output=True,
        text=True,
    )
    packages = {}
    for line in result.stdout.strip().splitlines():
        if "==" in line:
            name, version = line.split("==", 1)
            packages[name] = version
    return packages


def _restore_packages(
    snapshot: dict[str, str],
    current: dict[str, str],
) -> None:
    """Restore packages that were changed back to their snapshot versions.

    Args:
        snapshot: Package versions before notebook pip installs.
        current: Package versions after notebook pip installs.
    """
    to_restore = [
        f"{pkg}=={ver}"
        for pkg, ver in snapshot.items()
        if snapshot.get(pkg) != current.get(pkg)
    ]
    if to_restore:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "--quiet"]
            + to_restore,
            capture_output=True,
        )


def _run_pip_installs(commands: list[str]) -> None:
    """Run pip install commands so the environment matches the notebook.

    Mirrors Colab behaviour: each ``!pip install`` cell runs as a
    plain ``pip install`` with no special flags.

    Args:
        commands: List of pip install argument strings.
    """
    for args in commands:
        cmd = (
            [sys.executable, "-m", "pip", "install"]
            + shlex.split(args)
        )
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"    pip install {args}")
            print(f"      exit code: {result.returncode}")
            if result.stderr:
                for line in result.stderr.strip().splitlines()[-3:]:
                    print(f"      {line}")


def _check_import_subprocess(statement: str) -> tuple[bool, str]:
    """Check a single import statement in a subprocess.

    Uses a fresh Python process so that newly pip-installed packages
    are picked up without interference from ``sys.modules`` in the
    parent process.

    Args:
        statement: Full import statement to execute
            (e.g. ``from ai_foundations import generation``).

    Returns:
        Tuple of (success, error_message).
    """
    result = subprocess.run(
        [sys.executable, "-c", statement],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        return True, ""
    # Extract the last line of stderr for a concise error message.
    stderr_lines = result.stderr.strip().splitlines()
    error_line = stderr_lines[-1] if stderr_lines else "unknown error"
    return False, error_line


def check_imports(notebook_path: Path) -> list[str]:
    """Check that all imported modules can be resolved.

    Runs any ``!pip install`` commands found in the notebook first so
    that the environment matches what the notebook expects at runtime.
    After checking, any packages changed by the pip installs are
    restored to their original versions. This mirrors Colab where
    each notebook gets a fresh runtime — pip installs from one
    notebook cannot leak into another.

    Args:
        notebook_path: Path to the ``.ipynb`` file.

    Returns:
        List of error messages. Empty if all imports resolve.
    """
    pip_commands = _extract_pip_installs(notebook_path)
    has_pip_installs = bool(pip_commands)

    # Snapshot package versions before any pip installs so we can
    # restore the environment afterwards.
    snapshot = _pip_freeze() if has_pip_installs else {}

    if has_pip_installs:
        _run_pip_installs(pip_commands)

    errors = []
    cells = _extract_code_cells(notebook_path)

    seen_statements: set[str] = set()
    for cell_idx, source in cells:
        imports = _extract_imports(source)
        for _top_level, statement in imports:
            if statement in seen_statements:
                continue
            seen_statements.add(statement)
            if has_pip_installs:
                ok, err_msg = _check_import_subprocess(statement)
                if not ok:
                    errors.append(
                        f"  Cell {cell_idx}: {err_msg} "
                        f"[{statement}]"
                    )
            else:
                try:
                    exec(statement)  # noqa: S102
                except ImportError:
                    errors.append(
                        f"  Cell {cell_idx}: ImportError: "
                        f"cannot import '{statement}'"
                    )
                except Exception as exc:
                    errors.append(
                        f"  Cell {cell_idx}: {type(exc).__name__} "
                        f"importing '{statement}': {exc}"
                    )

    # Restore any packages changed by this notebook's pip installs
    # so the next notebook starts from a clean environment.
    if has_pip_installs:
        current = _pip_freeze()
        _restore_packages(snapshot, current)

    return errors


def check_notebook(notebook_path: Path) -> bool:
    """Run all checks on a single notebook.

    Args:
        notebook_path: Path to the ``.ipynb`` file.

    Returns:
        True if all checks passed, False otherwise.
    """
    name = notebook_path.name
    syntax_errors = check_syntax(notebook_path)
    import_errors = check_imports(notebook_path)

    if not syntax_errors and not import_errors:
        print(f"  ✅ PASS  {name}")
        return True

    print(f"  ❌ FAIL  {name}")
    for err in syntax_errors + import_errors:
        print(err)
    return False


def _load_manifest(manifest_path: Path) -> dict[str, dict]:
    """Load the generated manifest for GPU filtering.

    Args:
        manifest_path: Path to ``notebook_manifest.yml``.

    Returns:
        Dictionary mapping notebook paths to their manifest entries.
    """
    if not manifest_path.exists():
        return {}

    import yaml

    with open(manifest_path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}

    return {nb["path"]: nb for nb in data.get("notebooks", [])}


def main() -> None:
    """Entry point for notebook validation."""
    parser = argparse.ArgumentParser(
        description="Validate notebook syntax and imports."
    )
    parser.add_argument(
        "notebook",
        type=Path,
        nargs="?",
        help="Path to a single .ipynb file.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Check all notebooks under --repo-dir.",
    )
    parser.add_argument(
        "--repo-dir",
        type=Path,
        default=Path("ai-foundations"),
        help="Path to the upstream repo (default: ai-foundations).",
    )
    parser.add_argument(
        "--skip-gpu",
        action="store_true",
        help="Skip GPU-required notebooks (uses notebook_manifest.yml).",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("notebook_manifest.yml"),
        help="Path to the manifest file (default: notebook_manifest.yml).",
    )
    parser.add_argument(
        "--summary",
        type=Path,
        default=None,
        help="Write a markdown summary to this file (for CI).",
    )
    args = parser.parse_args()

    if not args.all and not args.notebook:
        parser.error("Provide a notebook path or use --all.")

    notebooks = []
    if args.all:
        notebooks = sorted(args.repo_dir.glob("course_*/gdm_lab_*.ipynb"))
    else:
        notebooks = [args.notebook]

    if not notebooks:
        print("No notebooks found.")
        sys.exit(1)

    # Load manifest for GPU filtering.
    manifest = {}
    if args.skip_gpu:
        manifest = _load_manifest(args.manifest)

    print(f"Checking {len(notebooks)} notebook(s)...\n")
    results = []
    for nb_path in notebooks:
        relative = str(nb_path.relative_to(args.repo_dir))
        entry = manifest.get(relative, {})

        if args.skip_gpu and (entry.get("gpu_required") or entry.get("skip")):
            print(f"  ⏭️  SKIP  {nb_path.name}")
            results.append(("skip", nb_path.name, []))
            continue

        passed = check_notebook(nb_path)
        status = "pass" if passed else "fail"
        errors = []
        if not passed:
            errors = check_syntax(nb_path) + check_imports(nb_path)
        results.append((status, nb_path.name, errors))

    passed = sum(1 for s, _, _ in results if s == "pass")
    failed = sum(1 for s, _, _ in results if s == "fail")
    skipped = sum(1 for s, _, _ in results if s == "skip")

    print()
    if failed == 0:
        print(f"All {passed} notebook(s) passed ({skipped} skipped).")
    else:
        print(
            f"{failed} notebook(s) failed, {passed} passed "
            f"({skipped} skipped)."
        )

    # Write markdown summary if requested.
    if args.summary:
        _write_summary(args.summary, results, passed, failed, skipped)

    sys.exit(0)


def _write_summary(
    path: Path,
    results: list[tuple[str, str, list[str]]],
    passed: int,
    failed: int,
    skipped: int,
) -> None:
    """Write a markdown summary table.

    Args:
        path: File path to write the summary to.
        results: List of (status, notebook_name, errors) tuples.
        passed: Count of passed notebooks.
        failed: Count of failed notebooks.
        skipped: Count of skipped notebooks.
    """
    lines = []
    lines.append("## Notebook Check Results\n")

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

    lines.append("| Status | Notebook | Details |")
    lines.append("|--------|----------|---------|")

    for status, name, errors in results:
        if status == "pass":
            icon = "✅"
            detail = ""
        elif status == "fail":
            icon = "❌"
            detail = "; ".join(e.strip() for e in errors)
        else:
            icon = "⏭️"
            detail = "GPU required"
        lines.append(f"| {icon} {status.upper()} | `{name}` | {detail} |")

    lines.append("")

    with open(path, "a", encoding="utf-8") as fh:
        fh.write("\n".join(lines))


if __name__ == "__main__":
    main()
