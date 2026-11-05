from __future__ import annotations

import math

import pytest

from jobscout.evaluation import metrics as m


def test_recall_mrr_ndcg() -> None:
    ranked = ["a", "b", "c", "d"]
    assert m.recall_at_k(ranked, {"b", "z"}, 2) == 0.5
    assert m.recall_at_k(ranked, set(), 2) == 0.0
    assert m.reciprocal_rank(ranked, {"c"}) == pytest.approx(1 / 3)
    assert m.reciprocal_rank(ranked, {"z"}) == 0.0
    assert m.ndcg_at_k(["a", "b"], {"a", "b"}, 10) == pytest.approx(1.0)
    assert m.ndcg_at_k(["x", "a"], {"a"}, 10) == pytest.approx(1 / math.log2(3))
    assert m.ndcg_at_k(["b", "a"], {"a": 3.0, "b": 1.0}, 2) < 1.0
    assert m.ndcg_at_k([], {"a"}, 5) == 0.0


def test_kappa() -> None:
    assert m.cohens_kappa([1, 0, 1, 0], [1, 0, 1, 0]) == 1.0
    assert m.cohens_kappa([1, 1, 0, 0], [1, 0, 1, 0]) == pytest.approx(0.0)
    assert m.cohens_kappa(["a", "a"], ["a", "a"]) == 1.0
    with pytest.raises(ValueError):
        m.cohens_kappa([1], [1, 0])


def test_percentile_mean_and_result_set_match() -> None:
    assert m.percentile([1, 2, 3, 4], 50) == 2 and m.percentile([], 95) == 0.0
    assert m.mean([]) == 0.0 and m.mean([1, 3]) == 2
    assert m.result_set_match([["Berlin", 3]], [["berlin ", 3]])
    assert m.result_set_match([[1.001], [2]], [[2], [1.0]])
    assert not m.result_set_match([[1]], [[1], [1]])
