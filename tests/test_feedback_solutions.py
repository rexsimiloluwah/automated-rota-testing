"""Tests that run solution code through the upstream feedback validators.

Each test extracts a reference solution from the notebooks' Solutions
section and passes it to the corresponding feedback validation function.
If the feedback function doesn't raise, the solution is correct and the
validation logic works.

Only self-contained tests are included here — tests where the feedback
function has hardcoded test data and the solution function has no
external dependencies beyond standard library + the package itself.
"""

import re
import unicodedata
from collections import Counter

import pytest

from ai_foundations.utils import formatting


# ---------------------------------------------------------------------------
# Solution functions extracted from notebooks
# ---------------------------------------------------------------------------

# Course 1: N-gram solutions (from gdm_lab_1_2)

def space_tokenize(text: str) -> list[str]:
    """Splits a string into a list of words."""
    return text.split(" ")


def generate_ngrams(text: str, n: int) -> list[tuple[str]]:
    """Generates n-grams from a given text."""
    tokens = space_tokenize(text)
    ngrams = []
    num_of_tokens = len(tokens)
    for i in range(0, num_of_tokens - n + 1):
        ngrams.append(tuple(tokens[i:i + n]))
    return ngrams


def get_ngram_counts(
    dataset: list[str], n: int
) -> dict[str, Counter]:
    """Computes the n-gram counts from a dataset."""
    ngram_counts = {}
    for text in dataset:
        ngrams = generate_ngrams(text, n)
        for ngram in ngrams:
            context = " ".join(ngram[:-1])
            next_token = ngram[-1]
            if context not in ngram_counts:
                ngram_counts[context] = Counter()
            ngram_counts[context][next_token] += 1
    return ngram_counts


def build_ngram_model(
    dataset: list[str], n: int
) -> dict[str, dict[str, float]]:
    """Builds an n-gram language model."""
    ngram_model = {}
    ngram_counts = get_ngram_counts(dataset, n)
    for context, next_tokens in ngram_counts.items():
        context_total_count = sum(next_tokens.values())
        ngram_model[context] = {}
        for token, count in next_tokens.items():
            ngram_model[context][token] = count / context_total_count
    return ngram_model


# Course 1: SLM solutions (from gdm_lab_1_4)

def build_vocabulary(tokens: list[str]) -> list[str]:
    """Builds a vocabulary list from the set of tokens."""
    return list(set(tokens))


# Course 2: Preprocessing solutions (from gdm_lab_2_1)

def clean_html(text: str) -> str:
    """Strip basic HTML markup and common entities from a string."""
    text = re.sub(r"<.*?>", "", text)
    text = re.sub("&nbsp;", " ", text)
    text = re.sub("&amp;", "&", text)
    text = re.sub("&lt;", "<", text)
    text = re.sub("&gt;", ">", text)
    return text


def clean_unicode(text: str) -> str:
    """Removes non-text unicode characters from a string."""
    categories_to_keep = {"L", "N", "P"}
    keep = []
    for ch in text:
        do_keep = ch.isspace()
        if not do_keep:
            for category in categories_to_keep:
                if unicodedata.category(ch).startswith(category):
                    do_keep = True
                    break
        if do_keep:
            keep.append(ch)
    return "".join(keep)


# Course 7: FLOPs solution (from gdm_lab_7_2)

def compute_num_flops(param_count: float, num_tokens: float) -> float:
    """Estimates the training FLOPs for one epoch."""
    return 6 * param_count * num_tokens


# Course 7: Memory calculation solutions (from gdm_lab_7_4)

def calculate_param_memory(
    param_count: int, bytes_per_param: int
) -> float:
    """Calculates memory in GB for model parameters."""
    return formatting.bytes_to_gb(param_count * bytes_per_param)


def calculate_input_data_memory(
    batch_size: int, max_length: int, bytes_per_token_id: int
) -> float:
    """Calculates memory in GB for a batch of input token IDs."""
    return formatting.bytes_to_gb(
        batch_size * max_length * bytes_per_token_id
    )


def calculate_gradient_memory(
    param_count: int, bytes_per_param: int
) -> float:
    """Calculates memory in GB for gradients."""
    return formatting.bytes_to_gb(param_count * bytes_per_param)


def calculate_optimizer_memory(
    param_count: int, bytes_per_param: int
) -> float:
    """Calculates memory in GB for Adam optimizer states."""
    return formatting.bytes_to_gb(2 * param_count * bytes_per_param)


def calculate_activation_memory(
    batch_size: int,
    max_length: int,
    num_layers: int,
    embedding_dim: int,
    bytes_per_param: int,
) -> float:
    """Estimates memory in GB for activations."""
    total_bytes = (
        batch_size * max_length * num_layers
        * embedding_dim * bytes_per_param
    )
    return formatting.bytes_to_gb(total_bytes)


# ---------------------------------------------------------------------------
# Tests: pass solution functions through feedback validators
# ---------------------------------------------------------------------------

class TestCourse1Ngrams:
    """Course 1: N-gram feedback tests with reference solutions."""

    def test_generate_ngrams(self):
        from ai_foundations.feedback.course_1 import ngrams
        ngrams.test_generate_ngrams(generate_ngrams, space_tokenize)

    def test_ngram_counts(self):
        from ai_foundations.feedback.course_1 import ngrams
        ngrams.test_ngram_counts(get_ngram_counts, generate_ngrams)

    def test_build_ngram_model(self):
        from ai_foundations.feedback.course_1 import ngrams
        ngrams.test_build_ngram_model(build_ngram_model, get_ngram_counts)


class TestCourse1Slm:
    """Course 1: SLM feedback tests with reference solutions."""

    def test_build_vocabulary(self):
        from ai_foundations.feedback.course_1 import slm
        slm.test_build_vocabulary(build_vocabulary)


class TestCourse2Preprocess:
    """Course 2: Preprocessing feedback tests with reference solutions."""

    def test_clean_html(self):
        from ai_foundations.feedback.course_2 import preprocess
        preprocess.test_clean_html(clean_html)

    def test_clean_unicode(self):
        from ai_foundations.feedback.course_2 import preprocess
        preprocess.test_clean_unicode(clean_unicode)


class TestCourse7Flops:
    """Course 7: FLOPs feedback tests with reference solutions."""

    def test_compute_num_flops(self):
        from ai_foundations.feedback.course_7 import flops
        flops.test_compute_num_flops(compute_num_flops)


class TestCourse7Memory:
    """Course 7: Memory calculation feedback tests with reference solutions."""

    def test_calculate_param_memory(self):
        from ai_foundations.feedback.course_7 import memory
        memory.test_calculate_param_memory(calculate_param_memory)

    def test_calculate_input_data_memory(self):
        from ai_foundations.feedback.course_7 import memory
        memory.test_calculate_input_data_memory(calculate_input_data_memory)

    def test_calculate_gradient_memory(self):
        from ai_foundations.feedback.course_7 import memory
        memory.test_calculate_gradient_memory(calculate_gradient_memory)

    def test_calculate_optimizer_memory(self):
        from ai_foundations.feedback.course_7 import memory
        memory.test_calculate_optimizer_memory(calculate_optimizer_memory)

    def test_calculate_activation_memory(self):
        from ai_foundations.feedback.course_7 import memory
        memory.test_calculate_activation_memory(calculate_activation_memory)
