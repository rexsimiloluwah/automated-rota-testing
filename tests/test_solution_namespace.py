"""Tests for ``_SolutionNamespace`` and its integration with
``_extract_solutions``.

Covers the silent-exec-failure issue described in
``simi_repo_review.md`` Comment 3: solution cells that raise at
extraction time used to be silently swallowed, producing an opaque
``KeyError`` downstream.
"""

import json
import sys
from pathlib import Path

import pytest

_TESTS_DIR = Path(__file__).resolve().parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))

from test_feedback_solutions import _SolutionNamespace, _extract_solutions  # noqa: E402


class TestSolutionNamespace:
    def test_hit_returns_value(self):
        ns = _SolutionNamespace({"foo": 42}, {})
        assert ns["foo"] == 42
        assert "foo" in ns

    def test_miss_with_no_error_raises_keyerror(self):
        ns = _SolutionNamespace({}, {})
        with pytest.raises(KeyError):
            ns["missing"]

    def test_miss_with_captured_error_raises_runtime_error(self):
        exc = NameError("helper is not defined")
        ns = _SolutionNamespace({}, {"broken_func": exc})
        with pytest.raises(RuntimeError) as info:
            ns["broken_func"]
        assert "broken_func" in str(info.value)
        assert "NameError" in str(info.value)
        assert "helper is not defined" in str(info.value)
        assert info.value.__cause__ is exc

    def test_get_returns_default_on_miss(self):
        ns = _SolutionNamespace({"foo": 1}, {"broken": NameError("x")})
        assert ns.get("foo") == 1
        assert ns.get("missing") is None
        assert ns.get("missing", "default") == "default"
        assert ns.get("broken") is None

    def test_load_errors_property_returns_copy(self):
        originals = {"broken": NameError("x")}
        ns = _SolutionNamespace({}, originals)
        errors = ns.load_errors
        assert "broken" in errors
        errors["mutation"] = ValueError()
        assert "mutation" not in ns.load_errors


def _make_notebook(tmp_path: Path, body: str, solutions: list[dict]) -> Path:
    """Write a minimal notebook with a body and named solution cells."""
    cells = [
        {"cell_type": "code", "source": body, "metadata": {}, "outputs": [],
         "execution_count": None},
        {"cell_type": "markdown", "source": "## Solutions\n", "metadata": {}},
    ]
    for sol in solutions:
        cells.append({
            "cell_type": "markdown",
            "source": f"### Coding Activity {sol['activity']}\n",
            "metadata": {},
        })
        cells.append({
            "cell_type": "code",
            "source": sol["source"],
            "metadata": {},
            "outputs": [],
            "execution_count": None,
        })
    nb = {
        "cells": cells,
        "metadata": {"kernelspec": {"name": "python3", "display_name": "Python 3"}},
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    course_dir = tmp_path / "ai-foundations" / "course_0"
    course_dir.mkdir(parents=True)
    path = course_dir / "gdm_lab_0_1_fake.ipynb"
    path.write_text(json.dumps(nb), encoding="utf-8")
    return path


def test_extract_solutions_captures_broken_solution(tmp_path, monkeypatch):
    """A solution that raises at definition time must surface through
    ``_SolutionNamespace`` as a RuntimeError naming the original exception,
    not an opaque ``KeyError``."""
    import test_feedback_solutions as tfs

    _make_notebook(
        tmp_path,
        body="x = 1\n",
        solutions=[
            {
                "activity": 1,
                "source": (
                    "def working_solution():\n"
                    "    return 100\n"
                ),
            },
            {
                "activity": 2,
                "source": (
                    # ImportError fires before `def` runs, so the name
                    # never binds — exactly the failure mode we want to
                    # surface through _SolutionNamespace.
                    "import nonexistent_module_xyz_abc_123\n"
                    "def broken_solution():\n"
                    "    return 42\n"
                ),
            },
        ],
    )
    monkeypatch.setattr(tfs, "_REPO_DIR", tmp_path / "ai-foundations")

    ns = _extract_solutions("course_0/gdm_lab_0_1_fake.ipynb")

    # Working solution is present.
    assert ns["working_solution"]() == 100

    # Broken solution surfaces with a helpful RuntimeError, not a bare KeyError.
    with pytest.raises(RuntimeError) as info:
        ns["broken_solution"]
    message = str(info.value)
    assert "broken_solution" in message
    assert "ModuleNotFoundError" in message or "nonexistent_module" in message


def test_extract_solutions_clean_notebook_has_no_load_errors(tmp_path, monkeypatch):
    """A notebook with no broken solutions records zero load errors."""
    import test_feedback_solutions as tfs

    _make_notebook(
        tmp_path,
        body="x = 1\n",
        solutions=[
            {"activity": 1, "source": "def f():\n    return 1\n"},
            {"activity": 2, "source": "def g():\n    return 2\n"},
        ],
    )
    monkeypatch.setattr(tfs, "_REPO_DIR", tmp_path / "ai-foundations")

    ns = _extract_solutions("course_0/gdm_lab_0_1_fake.ipynb")
    assert ns["f"]() == 1
    assert ns["g"]() == 2
    assert ns.load_errors == {}
