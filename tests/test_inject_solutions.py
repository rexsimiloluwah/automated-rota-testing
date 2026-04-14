"""Regression tests for ``scripts/inject_solutions.py``.

Focused on the ``= []`` placeholder false-positive described in
``simi_repo_review.md`` Comment 1: a cell that initializes a list
accumulator (``x = []`` followed by a loop filling it) used to be
flagged as a student placeholder, which caused ``inject_solutions`` to
splice a solution function into the middle of a data-loading cell.
"""

import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from inject_solutions import (  # noqa: E402
    _find_activity_cells,
    _is_trailing_empty_list_assignment,
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
