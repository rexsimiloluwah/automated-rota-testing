"""Tests for ``scripts/generate_manifest.py``.

Focused on the Windows path-separator bug described in
``simi_repo_review.md`` Comment 2: ``notebook_overrides.yml`` uses
POSIX-style keys (``course_5/gdm_lab_...``) but ``generate_manifest``
built the lookup key via ``str(Path.relative_to(...))``, which yields
backslash-separated paths on Windows. Result: every override silently
missed on Windows developer machines.
"""

import sys
import textwrap
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from generate_manifest import generate_manifest  # noqa: E402


_EMPTY_NB = '{"cells": [], "metadata": {}, "nbformat": 4, "nbformat_minor": 5}'


def _make_repo(tmp_path: Path) -> Path:
    """Build a minimal fake upstream repo with two course dirs."""
    repo = tmp_path / "ai-foundations"
    (repo / "course_5").mkdir(parents=True)
    (repo / "course_1").mkdir(parents=True)
    (repo / "course_5" / "gdm_lab_5_4_needs_kaggle.ipynb").write_text(_EMPTY_NB)
    (repo / "course_1" / "gdm_lab_1_1_plain.ipynb").write_text(_EMPTY_NB)
    return repo


def _write_overrides(tmp_path: Path) -> Path:
    overrides = tmp_path / "overrides.yml"
    overrides.write_text(
        textwrap.dedent(
            """\
            overrides:
              - path: course_5/gdm_lab_5_4_needs_kaggle.ipynb
                skip: true
                reason: "Requires Kaggle credentials"
            """
        )
    )
    return overrides


def test_manifest_paths_use_forward_slashes(tmp_path):
    """Paths in the manifest must always be POSIX-style, even on Windows."""
    manifest = generate_manifest(_make_repo(tmp_path), _write_overrides(tmp_path))
    paths = {entry["path"] for entry in manifest}
    assert "course_5/gdm_lab_5_4_needs_kaggle.ipynb" in paths
    assert "course_1/gdm_lab_1_1_plain.ipynb" in paths
    for path in paths:
        assert "\\" not in path, (
            f"Manifest path {path!r} contains backslash — "
            "overrides keyed with forward slashes will not match."
        )


def test_override_applied_on_any_platform(tmp_path):
    """The Kaggle-gated notebook must be marked ``skip: true`` regardless of
    the host OS's native path separator."""
    manifest = generate_manifest(_make_repo(tmp_path), _write_overrides(tmp_path))
    by_path = {entry["path"]: entry for entry in manifest}
    kaggle_entry = by_path["course_5/gdm_lab_5_4_needs_kaggle.ipynb"]
    assert kaggle_entry["skip"] is True
    assert "Kaggle" in kaggle_entry["reason"]
    # Non-overridden notebook should keep the default skip=False.
    assert by_path["course_1/gdm_lab_1_1_plain.ipynb"]["skip"] is False
