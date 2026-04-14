"""Tests that dynamically extract solutions from notebooks and validate them.

Instead of hardcoding solution functions, this module uses
``inject_solutions`` to extract reference solutions from each notebook's
``## Solutions`` section at test time. The extracted code is compiled and
executed in an isolated namespace, then passed to the upstream feedback
validators.

If the upstream repo changes a solution or feedback validator, these
tests automatically pick up the change.
"""

import json
import re
import sys
from pathlib import Path

import pytest

# Make the scripts directory importable.
_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from inject_solutions import (
    _collect_solution_cells,
    _extract_function_name,
    _find_solutions_boundary,
)
from check_notebook import _is_placeholder_cell

# Path to the upstream repo (cloned during Docker build / CI setup).
_REPO_DIR = Path(__file__).resolve().parent.parent / "ai-foundations"


class _SolutionNamespace:
    """Wrapper around a notebook's extracted solution namespace.

    Behaves like a read-only dict for tests, but when a key is missing
    it reports the exception that was raised while executing that
    solution cell, instead of the uninformative ``KeyError`` you get
    from a plain dict.

    The underlying ``dict`` is what ``exec`` wrote into — it is kept
    separate from this wrapper because CPython bypasses ``__getitem__``
    on dict subclasses during ``exec`` globals lookups, so we cannot
    subclass ``dict`` safely.
    """

    def __init__(
        self,
        namespace: dict[str, object],
        load_errors: dict[str, Exception],
    ) -> None:
        self._ns = namespace
        self._load_errors = load_errors

    def __getitem__(self, key: str) -> object:
        if key in self._ns:
            return self._ns[key]
        exc = self._load_errors.get(key)
        if exc is not None:
            raise RuntimeError(
                f"Solution {key!r} failed to load at extraction time: "
                f"{type(exc).__name__}: {exc}"
            ) from exc
        raise KeyError(key)

    def __contains__(self, key: str) -> bool:
        return key in self._ns

    def get(self, key: str, default: object = None) -> object:
        try:
            return self[key]
        except (KeyError, RuntimeError):
            return default

    @property
    def load_errors(self) -> dict[str, Exception]:
        """Expose captured errors for diagnostic use."""
        return dict(self._load_errors)


def _extract_solutions(notebook_name: str) -> _SolutionNamespace:
    """Extract and compile all solutions from a notebook.

    First executes non-placeholder code cells from the main body of the
    notebook (to pick up helper functions, imports, etc.), then executes
    solution cells from the ``## Solutions`` section.  Everything runs in
    a shared namespace so interdependent functions work.

    Body-cell exec failures are expected (they often need runtime
    context like data downloads or Colab APIs) and are discarded.
    Solution-cell exec failures are **not** expected — if a solution
    fails to compile or throws at definition time, the broken state is
    captured into ``load_errors`` keyed by function name. Tests that
    later try to access the solution get a ``RuntimeError`` that names
    the original exception, instead of an opaque ``KeyError`` that
    tells you nothing about the root cause.

    Args:
        notebook_name: Filename relative to the repo course directory,
            e.g. ``"course_1/gdm_lab_1_2_experiment_with_n_gram_models.ipynb"``.

    Returns:
        ``_SolutionNamespace`` wrapping all compiled solution objects.
    """
    nb_path = _REPO_DIR / notebook_name
    if not nb_path.exists():
        pytest.skip(f"Notebook not found: {nb_path}")

    with open(nb_path, "r", encoding="utf-8") as fh:
        nb = json.load(fh)

    cells = nb.get("cells", [])
    boundary = _find_solutions_boundary(cells)
    if boundary is None:
        pytest.skip(f"No ## Solutions section in {notebook_name}")

    # Build a shared namespace with common imports that solutions may need.
    namespace: dict[str, object] = {}
    exec(
        "import re\n"
        "import unicodedata\n"
        "from collections import Counter\n",
        namespace,
    )
    # Add ai_foundations utilities if available.
    try:
        from ai_foundations.utils import formatting
        namespace["formatting"] = formatting
    except ImportError:
        pass

    # Step 1: Execute non-placeholder code cells from the notebook body.
    # This picks up helper functions, constants, and imports that
    # solutions may depend on. Body cells are allowed to fail (runtime
    # context is often missing), so we discard those exceptions.
    for cell in cells[:boundary]:
        if cell.get("cell_type") != "code":
            continue
        source = "".join(cell.get("source", []))
        # Skip shell commands, magics, and placeholder cells.
        lines = [
            line for line in source.splitlines(keepends=True)
            if not line.strip().startswith(("!", "%"))
            and not re.match(r".*#\s*@(title|param|markdown)", line.strip())
        ]
        filtered = "".join(lines).strip()
        if not filtered or _is_placeholder_cell(filtered):
            continue
        try:
            exec(filtered, namespace)
        except Exception:
            # Cells that need runtime context (data downloads, model
            # loading, Colab APIs, etc.) will fail — that's expected.
            pass

    # Step 2: Execute solution cells, overriding any placeholder stubs.
    # Solution-cell failures are real bugs, not expected — capture them
    # so tests can surface the original exception.
    load_errors: dict[str, Exception] = {}
    solutions = _collect_solution_cells(cells, boundary)
    for sol in solutions:
        try:
            exec(sol["source"], namespace)
        except Exception as exc:
            key = (
                sol.get("function_name")
                or _extract_function_name(sol["source"])
                or f"<activity_{sol.get('activity_number', '?')}>"
            )
            load_errors[key] = exc

    return _SolutionNamespace(namespace, load_errors)


# ---------------------------------------------------------------------------
# Course 1: N-gram models (gdm_lab_1_2)
# ---------------------------------------------------------------------------

class TestCourse1Ngrams:
    """Course 1: N-gram feedback tests with dynamically extracted solutions."""

    @pytest.fixture(autouse=True)
    def _load(self):
        self.ns = _extract_solutions(
            "course_1/gdm_lab_1_2_experiment_with_n_gram_models.ipynb"
        )

    def test_generate_ngrams(self):
        from ai_foundations.feedback.course_1 import ngrams
        ngrams.test_generate_ngrams(
            self.ns["generate_ngrams"],
            self.ns["space_tokenize"],
        )

    def test_ngram_counts(self):
        from ai_foundations.feedback.course_1 import ngrams
        ngrams.test_ngram_counts(
            self.ns["get_ngram_counts"],
            self.ns["generate_ngrams"],
        )

    def test_build_ngram_model(self):
        from ai_foundations.feedback.course_1 import ngrams
        ngrams.test_build_ngram_model(
            self.ns["build_ngram_model"],
            self.ns["get_ngram_counts"],
        )


# ---------------------------------------------------------------------------
# Course 1: SLM (gdm_lab_1_4)
# ---------------------------------------------------------------------------

class TestCourse1Slm:
    """Course 1: SLM feedback tests with dynamically extracted solutions."""

    @pytest.fixture(autouse=True)
    def _load(self):
        self.ns = _extract_solutions(
            "course_1/gdm_lab_1_4_prepare_the_dataset_for_training_a_slm.ipynb"
        )

    def test_build_vocabulary(self):
        from ai_foundations.feedback.course_1 import slm
        slm.test_build_vocabulary(self.ns["build_vocabulary"])


# ---------------------------------------------------------------------------
# Course 2: Preprocessing (gdm_lab_2_1)
# ---------------------------------------------------------------------------

class TestCourse2Preprocess:
    """Course 2: Preprocessing feedback tests with dynamically extracted solutions."""

    @pytest.fixture(autouse=True)
    def _load(self):
        self.ns = _extract_solutions(
            "course_2/gdm_lab_2_1_preprocess_data.ipynb"
        )

    def test_clean_html(self):
        from ai_foundations.feedback.course_2 import preprocess
        preprocess.test_clean_html(self.ns["clean_html"])

    def test_clean_unicode(self):
        from ai_foundations.feedback.course_2 import preprocess
        preprocess.test_clean_unicode(self.ns["clean_unicode"])


# ---------------------------------------------------------------------------
# Course 7: FLOPs (gdm_lab_7_2)
# ---------------------------------------------------------------------------

class TestCourse7Flops:
    """Course 7: FLOPs feedback tests with dynamically extracted solutions."""

    @pytest.fixture(autouse=True)
    def _load(self):
        self.ns = _extract_solutions(
            "course_7/gdm_lab_7_2_estimate_training_flops.ipynb"
        )

    def test_compute_num_flops(self):
        from ai_foundations.feedback.course_7 import flops
        flops.test_compute_num_flops(self.ns["compute_num_flops"])


# ---------------------------------------------------------------------------
# Course 7: Memory (gdm_lab_7_4)
# ---------------------------------------------------------------------------

class TestCourse7Memory:
    """Course 7: Memory calculation feedback tests with dynamically extracted solutions."""

    @pytest.fixture(autouse=True)
    def _load(self):
        self.ns = _extract_solutions(
            "course_7/gdm_lab_7_4_estimate_gpu_memory.ipynb"
        )

    def test_calculate_param_memory(self):
        from ai_foundations.feedback.course_7 import memory
        memory.test_calculate_param_memory(
            self.ns["calculate_param_memory"]
        )

    def test_calculate_input_data_memory(self):
        from ai_foundations.feedback.course_7 import memory
        memory.test_calculate_input_data_memory(
            self.ns["calculate_input_data_memory"]
        )

    def test_calculate_gradient_memory(self):
        from ai_foundations.feedback.course_7 import memory
        memory.test_calculate_gradient_memory(
            self.ns["calculate_gradient_memory"]
        )

    def test_calculate_optimizer_memory(self):
        from ai_foundations.feedback.course_7 import memory
        memory.test_calculate_optimizer_memory(
            self.ns["calculate_optimizer_memory"]
        )

    def test_calculate_activation_memory(self):
        from ai_foundations.feedback.course_7 import memory
        memory.test_calculate_activation_memory(
            self.ns["calculate_activation_memory"]
        )


# ---------------------------------------------------------------------------
# Course 3: MLP design (gdm_lab_3_4)
# ---------------------------------------------------------------------------

class TestCourse3Mlp:
    """Course 3: MLP feedback tests with dynamically extracted solutions."""

    @pytest.fixture(autouse=True)
    def _load(self):
        self.ns = _extract_solutions(
            "course_3/gdm_lab_3_4_design_your_own_mlp.ipynb"
        )

    def test_construct_operations(self):
        from ai_foundations.feedback.course_3 import mlp
        mlp.test_construct_operations(self.ns["construct_operations"])


# ---------------------------------------------------------------------------
# Course 4: Attention mask (gdm_lab_4_3)
# ---------------------------------------------------------------------------

class TestCourse4AttentionMask:
    """Course 4: Attention mask feedback test with dynamically extracted solutions."""

    @pytest.fixture(autouse=True)
    def _load(self):
        self.ns = _extract_solutions(
            "course_4/gdm_lab_4_3_implement_attention_equation_2.ipynb"
        )

    def test_compute_attention_mask(self):
        try:
            from ai_foundations.feedback.course_4 import attention
        except (ImportError, AttributeError) as exc:
            pytest.skip(f"Cannot import attention feedback module: {exc}")
        attention.test_compute_attention_mask(
            self.ns["compute_attention_mask"]
        )


# ---------------------------------------------------------------------------
# Course 4: Counting parameters (gdm_lab_4_5)
# ---------------------------------------------------------------------------

class TestCourse4CountingParameters:
    """Course 4: Parameter counting feedback tests with dynamically extracted solutions."""

    @pytest.fixture(autouse=True)
    def _load(self):
        self.ns = _extract_solutions(
            "course_4/gdm_lab_4_5_reflection_on_trainable_parameters.ipynb"
        )

    def test_parameter_count_embedding(self):
        from ai_foundations.feedback.course_4 import counting_parameters
        counting_parameters.test_parameter_count_embedding(
            self.ns["parameter_count_embedding"]
        )

    def test_parameter_count_layer_norm(self):
        from ai_foundations.feedback.course_4 import counting_parameters
        counting_parameters.test_parameter_count_layer_norm(
            self.ns["parameter_count_layer_norm"]
        )

    def test_parameter_count_attention(self):
        from ai_foundations.feedback.course_4 import counting_parameters
        counting_parameters.test_parameter_count_attention(
            self.ns["parameter_count_attention"]
        )

    def test_parameter_count_mlp(self):
        from ai_foundations.feedback.course_4 import counting_parameters
        counting_parameters.test_parameter_count_mlp(
            self.ns["parameter_count_mlp"]
        )

    def test_parameter_count_output_layer(self):
        from ai_foundations.feedback.course_4 import counting_parameters
        counting_parameters.test_parameter_count_output_layer(
            self.ns["parameter_count_output_layer"]
        )

    def test_parameter_count_transformer_block(self):
        from ai_foundations.feedback.course_4 import counting_parameters
        counting_parameters.test_parameter_count_transformer_block(
            self.ns["parameter_count_transformer_block"]
        )

    def test_parameter_count_transformer(self):
        from ai_foundations.feedback.course_4 import counting_parameters
        counting_parameters.test_parameter_count_transformer(
            self.ns["parameter_count_transformer"]
        )


# ---------------------------------------------------------------------------
# Course 5: QA formatting (gdm_lab_5_2)
# ---------------------------------------------------------------------------

class TestCourse5Formatting:
    """Course 5: QA formatting feedback test with dynamically extracted solutions."""

    @pytest.fixture(autouse=True)
    def _load(self):
        import pandas as pd
        self.ns = _extract_solutions(
            "course_5/gdm_lab_5_2_format_text_for_turn_based_dialogue.ipynb"
        )
        self.dataset = pd.DataFrame([{
            "category": "Geography",
            "question": "What is the tallest mountain in Africa?",
            "answer": "Mount Kilimanjaro",
        }])

    def test_check_qa_format(self):
        from ai_foundations.feedback.course_5 import formatting
        formatting.check_qa_format(self.ns["format_qa"], self.dataset)
