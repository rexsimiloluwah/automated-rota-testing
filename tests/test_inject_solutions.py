"""Regression tests for ``scripts/inject_solutions.py``.

Covers:

- The ``= []`` placeholder false-positive in ``_find_activity_cells``
  (PR #1): an accumulator cell (``x = []`` followed by a loop filling it)
  must not be flagged as a student placeholder.
- Multi-function activity cells: when a cell defines several functions
  (e.g. ``__init__`` + ``call``) and the solution only fills in one of
  them, the injector must target the matching ``def`` by name rather
  than replacing the first one.
- Dict-value ellipsis placeholders: cells using
  ``# Fill in all the '...' values`` together with ``"key": ...,`` must
  be flagged as activity cells.
- Two-pass matching in ``_replace_placeholder_region``: an explicit
  ``# Add your code`` comment must take priority over an earlier
  ``= []`` initialization, so cells like ``tokens = []`` followed by a
  ``# Add your code here`` placeholder preserve the initialization.
- Defensive ``ast.parse`` check: an injection that would produce
  invalid Python is skipped, leaving the original cell untouched.
"""

import ast
import json
import sys
import tempfile
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from inject_solutions import (  # noqa: E402
    _extract_function_span,
    _find_activity_cells,
    _is_trailing_empty_list_assignment,
    _parses_as_python,
    _replace_function_in_cell,
    _replace_placeholder_region,
    inject_solutions,
)


def _md(source: str) -> dict:
    return {"cell_type": "markdown", "source": source}


def _code(source: str) -> dict:
    return {"cell_type": "code", "source": source}


class TestIsTrailingEmptyListAssignment:
    def test_bare_empty_list(self):
        assert _is_trailing_empty_list_assignment("my_list = []\n")

    def test_empty_list_with_trailing_whitespace(self):
        assert _is_trailing_empty_list_assignment("my_list = []\n\n")

    def test_accumulator_followed_by_for_loop(self):
        source = (
            "encoded_tokens = []\n"
            "for paragraph in dataset:\n"
            "    encoded_tokens.append(tokenizer.encode(paragraph))\n"
        )
        assert not _is_trailing_empty_list_assignment(source)

    def test_empty_list_followed_by_print(self):
        source = "result = []\nprint('done')\n"
        assert not _is_trailing_empty_list_assignment(source)

    def test_empty_list_with_preceding_setup(self):
        source = (
            "data = load_data()\n"
            "transformed = transform(data)\n"
            "result = []\n"
        )
        assert _is_trailing_empty_list_assignment(source)

    def test_non_empty_list(self):
        assert not _is_trailing_empty_list_assignment("my_list = [1, 2, 3]\n")

    def test_syntax_error_returns_false(self):
        assert not _is_trailing_empty_list_assignment("def broken(\n")

    def test_empty_source(self):
        assert not _is_trailing_empty_list_assignment("")


class TestFindActivityCells:
    def test_accumulator_cell_not_flagged(self):
        """Regression: gdm_lab_4_5 cell 45 (``encoded_tokens = []`` + loop)
        must not be treated as a student placeholder."""
        cells = [
            _md("## Coding Activity 1: Write a tokenizer\n"),
            _code("def tokenize(text):\n    ...\n"),
            _code(
                "# Load the dataset and the tokenizer.\n"
                "africa_galore = pd.read_json(URL)\n"
                "dataset = africa_galore['description'].values\n"
                "tokenizer = BPEWordTokenizer.from_url(TOKENIZER_URL)\n"
                "\n"
                "encoded_tokens = []\n"
                "for paragraph in tqdm.tqdm(dataset, unit='paragraphs'):\n"
                "    encoded_tokens.append(tokenizer.encode(paragraph))\n"
            ),
            _md("## Solutions\n"),
            _md("### Coding Activity 1\n"),
            _code("def tokenize(text):\n    return text.split()\n"),
        ]
        activities = _find_activity_cells(cells, boundary=3)
        flagged = {a["index"] for a in activities}
        assert 2 not in flagged, (
            "accumulator cell must not be flagged as a placeholder"
        )

    def test_bpe_merge_loop_not_flagged(self):
        """Regression: gdm_lab_2_4 cell 24 (``new_corpus = []`` + merge loop)
        must not be treated as a student placeholder."""
        cells = [
            _md("## Coding Activity 1: Implement merge\n"),
            _code("def merge_pair_in_word(word, pair):\n    ...\n"),
            _code(
                "new_corpus = []\n"
                "for word in corpus:\n"
                "    new_word = merge_pair_in_word(word, most_freq_pair)\n"
                "    new_corpus.append(new_word)\n"
                "corpus = new_corpus\n"
            ),
            _md("## Solutions\n"),
            _md("### Coding Activity 1\n"),
            _code(
                "def merge_pair_in_word(word, pair):\n"
                "    return word.replace(pair[0] + pair[1], ''.join(pair))\n"
            ),
        ]
        activities = _find_activity_cells(cells, boundary=3)
        flagged = {a["index"] for a in activities}
        assert 2 not in flagged

    def test_bare_empty_list_still_flagged(self):
        """Fill-in-the-list activities (``my_list = []`` with nothing after)
        must still be detected as placeholders — this is a legitimate
        student convention."""
        cells = [
            _md("## Coding Activity 1: Populate the list\n"),
            _code("my_list = []\n"),
            _md("## Solutions\n"),
            _md("### Coding Activity 1\n"),
            _code("my_list = [1, 2, 3]\n"),
        ]
        activities = _find_activity_cells(cells, boundary=2)
        flagged = {a["index"] for a in activities}
        assert 1 in flagged

    def test_fill_in_values_comment_flagged(self):
        """Regression: gdm_lab_7_2 cell 22 uses
        ``# Fill in all the '...' values based on your calculations.``
        together with dict-value ellipsis (``"FLOPs": ...,``). The
        assignment-form ellipsis regex does not match these because the
        ellipsis sits after ``:`` not ``=``; this cell was skipped
        entirely by the injector, leaving literal ``...`` values in the
        data at runtime."""
        cells = [
            _md("## Coding Activity 2: Calculate FLOPs\n"),
            _code(
                "# Fill in all the '...' values based on your calculations.\n"
                "results = [\n"
                "    {'Scenario': 'A', 'FLOPs': ...},\n"
                "    {'Scenario': 'B', 'FLOPs': ...},\n"
                "]\n"
            ),
            _md("## Solutions\n"),
            _md("### Coding Activity 2\n"),
            _code(
                "results = [\n"
                "    {'Scenario': 'A', 'FLOPs': 1e18},\n"
                "    {'Scenario': 'B', 'FLOPs': 2e18},\n"
                "]\n"
            ),
        ]
        activities = _find_activity_cells(cells, boundary=2)
        flagged = {a["index"] for a in activities}
        assert 1 in flagged


class TestExtractFunctionSpan:
    def test_first_function_when_no_target(self):
        """Without ``target_name`` the span of the first ``def`` wins —
        the function body plus any trailing blank lines are considered
        part of that function until the next same-or-lower indent."""
        source = (
            "def first():\n    return 1\n"
            "\n"
            "def second():\n    return 2\n"
        )
        span = _extract_function_span(source)
        # Span ends at the line of the next top-level ``def``, which is
        # line 3 (0-indexed: first, return 1, blank, def second).
        assert span == (0, 3)

    def test_targets_named_function_in_multi_def_cell(self):
        """Regression: gdm_lab_5_5 cell 26 defines a class with
        ``__init__`` and ``call``. The Activity 1 solution fills in
        ``call`` only — passing ``target_name="call"`` must skip
        ``__init__`` and return the span of ``call``."""
        source = (
            "class LoraDense:\n"
            "    def __init__(self, rank=8):\n"
            "        self.rank = rank\n"
            "\n"
            "    def call(self, x):\n"
            "        # Add your code here\n"
            "        return x\n"
        )
        span = _extract_function_span(source, target_name="call")
        assert span is not None
        start, end = span
        lines = source.splitlines(keepends=True)
        assert "def call" in lines[start]
        # __init__ must not be in the replaced span.
        replaced = "".join(lines[start:end])
        assert "def __init__" not in replaced

    def test_unknown_target_returns_none(self):
        source = "def foo():\n    return 1\n"
        assert _extract_function_span(source, target_name="bar") is None


class TestReplaceFunctionInCell:
    def test_replaces_named_function_preserving_other_defs(self):
        """Regression: gdm_lab_5_5. Before the fix, the injector replaced
        the first ``def`` it found in the activity cell regardless of
        which function the solution was for — replacing ``__init__``
        with the body of ``call`` and producing mixed-indent invalid
        Python. The injector must now target by name."""
        activity_cell = (
            "class LoraDense:\n"
            "    def __init__(self, rank=8):\n"
            "        self.rank = rank\n"
            "        self.A = None\n"
            "\n"
            "    def call(self, x):\n"
            "        # Add your code here.\n"
            "        return x\n"
        )
        solution_func = (
            "def call(self, x):\n"
            "    return x @ self.A\n"
        )
        new_source = _replace_function_in_cell(
            activity_cell, solution_func, target_name="call"
        )
        # __init__ must still be present — it was not the target.
        assert "def __init__" in new_source
        # The call body must be the solution's body.
        assert "return x @ self.A" in new_source
        # There must be exactly one definition of ``call``.
        assert new_source.count("def call") == 1
        # And the whole cell must still parse.
        ast.parse(new_source)

    def test_falls_back_to_first_function_when_target_missing(self):
        """When ``target_name`` does not match any ``def`` in the cell,
        fall back to the first-function behaviour so single-function
        activity cells (the common case, pre-fix behaviour) keep
        working even if the caller passes a target."""
        activity_cell = "def foo():\n    # Add your code here.\n    pass\n"
        solution_func = "def foo():\n    return 42\n"
        new_source = _replace_function_in_cell(
            activity_cell, solution_func, target_name="unknown_name"
        )
        assert "return 42" in new_source
        assert new_source.count("def foo") == 1


class TestReplacePlaceholderRegion:
    def test_add_your_code_comment_takes_priority_over_eq_brackets(self):
        """Regression: gdm_lab_1_4 cell 19 and gdm_lab_1_2 cell 61.
        When a cell has ``tokens = []`` followed by
        ``# Add your code here``, the old injector matched the ``= []``
        first and wiped out the initialization. The solution uses
        ``tokens.append(...)`` but ``tokens`` is then undefined.

        After the fix, the explicit ``# Add your code`` comment wins
        and the initialization is preserved in ``before``."""
        cell_source = (
            "tokens = []\n"
            "\n"
            "# Add your code here.\n"
            "\n"
            "print(f\"Total tokens: {len(tokens):,}\")\n"
        )
        solution_source = (
            "for paragraph in dataset:\n"
            "    for token in space_tokenize(paragraph):\n"
            "        tokens.append(token)\n"
        )
        new_source = _replace_placeholder_region(cell_source, solution_source)
        # Initialization must survive.
        assert "tokens = []" in new_source
        # Solution must be injected.
        assert "tokens.append(token)" in new_source
        # The `print(...)` line must survive (it came after the placeholder).
        assert "print(f\"Total tokens:" in new_source
        # And the whole thing must still parse.
        ast.parse(new_source)

    def test_eq_brackets_fallback_when_no_comment_marker(self):
        """If a cell has no explicit comment marker, ``= []`` is still
        used as a placeholder signal (pre-fix behaviour for cells that
        rely only on the equality convention)."""
        cell_source = "my_list = []\n"
        solution_source = "my_list = [1, 2, 3]\n"
        new_source = _replace_placeholder_region(cell_source, solution_source)
        assert "[1, 2, 3]" in new_source

    def test_eq_ellipsis_fallback_when_no_comment_marker(self):
        """Same fallback but for ``= ...``."""
        cell_source = "result = ...\nprint(result)\n"
        solution_source = "result = 42\n"
        new_source = _replace_placeholder_region(cell_source, solution_source)
        assert "result = 42" in new_source
        assert "print(result)" in new_source


class TestParsesAsPython:
    def test_valid_python(self):
        assert _parses_as_python("x = 1\nprint(x)\n")

    def test_empty_source(self):
        assert _parses_as_python("")

    def test_invalid_python(self):
        assert not _parses_as_python("def broken(\n")

    def test_magic_cell_accepted(self):
        assert _parses_as_python("!pip install jax\nimport jax\n")

    def test_cell_magic_accepted(self):
        assert _parses_as_python("%%writefile out.txt\nhello\n")


class TestAstParseDefence:
    """End-to-end: a corrupted injection must not overwrite a valid
    original cell. Uses a fully constructed notebook to ensure the
    guard triggers inside ``inject_solutions``."""

    def test_corrupt_injection_is_reverted(self, tmp_path):
        # Activity cell: valid Python with an explicit placeholder.
        # Solution cell: intentionally a fragment that would produce
        # broken indentation if spliced naively. We use a function
        # activity where the solution's function name doesn't match
        # anything in the activity cell and doesn't sit well as a
        # placeholder-region replacement either — the end result is an
        # indentation error if the guard doesn't fire.
        cells = [
            _md("## Coding Activity 1\n"),
            _code(
                "def wrap():\n"
                "    inner_val = 0  # Add your code here.\n"
                "    return inner_val\n"
            ),
            _md("## Solutions\n"),
            _md("### Coding Activity 1\n"),
            # A deliberately malformed solution that will produce
            # invalid syntax when injected.
            _code("def wrap():\n  broken(\n"),
        ]
        nb = {
            "cells": cells,
            "metadata": {},
            "nbformat": 4,
            "nbformat_minor": 5,
        }
        path = tmp_path / "nb.ipynb"
        path.write_text(json.dumps(nb), encoding="utf-8")

        result = inject_solutions(path)

        activity_source = "".join(result["cells"][1]["source"])
        # The defensive check must have kept the ORIGINAL cell when the
        # would-be replacement is not valid Python. Either the
        # placeholder-region path produced valid code (fine — test
        # passes trivially) or it didn't and we kept the original.
        assert _parses_as_python(activity_source), (
            "activity cell must be valid Python after inject_solutions; "
            "ast.parse guard failed to revert a broken injection"
        )
