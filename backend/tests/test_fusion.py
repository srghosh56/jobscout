from __future__ import annotations

import pytest

from jobscout.retrieval.fusion import rrf


def test_rrf_basic_maths() -> None:
    out = dict(rrf([["a", "b", "c"], ["b", "a", "d"]], k=60))
    assert out["a"] == pytest.approx(1 / 61 + 1 / 62)
    assert out["b"] == pytest.approx(1 / 62 + 1 / 61)
    assert out["c"] == pytest.approx(1 / 63)
    assert out["d"] == pytest.approx(1 / 63)


def test_rrf_order_and_ties_deterministic() -> None:
    ranked = [d for d, _ in rrf([["a", "b"], ["b", "a"]])]
    assert ranked == ["a", "b"]  # tie → first appearance wins


def test_rrf_doc_in_both_lists_beats_top_of_one() -> None:
    ranked = [d for d, _ in rrf([["x", "shared"], ["y", "shared"]], k=60)]
    assert ranked[0] == "shared"


def test_rrf_small_k_rewards_top_ranks_more() -> None:
    lists = [["a", "b", "c", "d"], ["d", "c", "b", "a"]]
    assert rrf(lists, k=1)[0][1] > rrf(lists, k=60)[0][1]


def test_rrf_edge_cases() -> None:
    assert rrf([]) == []
    assert rrf([[], []]) == []
    assert dict(rrf([["a", "a", "b"]]))["a"] == pytest.approx(1 / 61)  # dup ignored
    with pytest.raises(ValueError):
        rrf([["a"]], k=0)
