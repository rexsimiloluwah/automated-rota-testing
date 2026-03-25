"""Tests for ai_foundations utility modules.

Tests pure functions in the ai_foundations package that don't require
notebook context, model data, or GPU.
"""

import pandas as pd
import pytest

from ai_foundations.utils.formatting import bytes_to_gb
from ai_foundations.utils.formatting import format_flops
from ai_foundations.utils.formatting import format_large_number
from ai_foundations.formatting.format_qa import format_qa


class TestBytesToGb:
    """Tests for bytes_to_gb()."""

    def test_exact_gigabyte(self):
        assert bytes_to_gb(1024**3) == 1.0

    def test_four_gigabytes(self):
        assert bytes_to_gb(4 * 1024**3) == 4.0

    def test_zero(self):
        assert bytes_to_gb(0) == 0.0

    def test_fractional(self):
        result = bytes_to_gb(512 * 1024**2)
        assert result == pytest.approx(0.5)


class TestFormatFlops:
    """Tests for format_flops()."""

    def test_scientific_notation(self):
        assert format_flops(1e18) == "1.00e+18"

    def test_small_number(self):
        assert format_flops(6000.0) == "6.00e+03"

    def test_none(self):
        assert format_flops(None) == "N/A"

    def test_zero(self):
        assert format_flops(0) == "0.00e+00"


class TestFormatLargeNumber:
    """Tests for format_large_number()."""

    def test_trillion(self):
        assert format_large_number(1.5e12) == "1.5 trillion"

    def test_billion(self):
        assert format_large_number(7e9) == "7.0 billion"

    def test_million(self):
        assert format_large_number(110e6) == "110.0 million"

    def test_below_million(self):
        assert format_large_number(50000) == "50,000"

    def test_none(self):
        assert format_large_number(None) == "N/A"


class TestFormatQa:
    """Tests for format_qa()."""

    def test_basic_formatting(self):
        data = {
            "category": "Geography",
            "question": "What is the tallest mountain in Africa?",
            "answer": "Mount Kilimanjaro",
        }
        q, a = format_qa(data)
        assert q == (
            "<start_of_turn>user\n"
            "What is the tallest mountain in Africa?"
            "<end_of_turn>\n"
        )
        assert a == (
            "<start_of_turn>model\n"
            "Category: Geography\n"
            "Mount Kilimanjaro"
            "<end_of_turn>"
        )

    def test_custom_tokens(self):
        data = {
            "category": "Food",
            "question": "What is jollof rice?",
            "answer": "A West African dish.",
        }
        q, a = format_qa(data, sot="<S>", eot="<E>")
        assert "<S>user\n" in q
        assert "<E>" in q
        assert "<S>model\n" in a
        assert "Category: Food" in a

    def test_with_pandas_series(self):
        series = pd.Series({
            "category": "History",
            "question": "Who was Mansa Musa?",
            "answer": "Emperor of Mali.",
        })
        q, a = format_qa(series)
        assert "Mansa Musa" in q
        assert "Category: History" in a
