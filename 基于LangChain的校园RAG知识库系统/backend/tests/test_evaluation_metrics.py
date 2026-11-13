import pytest

from src.evaluation.metrics import (
    hit_at_k,
    keyword_coverage,
    mrr,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
)


def test_hit_and_recall():
    ranked = ["a", "b", "c"]
    relevant = {"b", "c"}
    assert hit_at_k(ranked, relevant, 1) == 0
    assert hit_at_k(ranked, relevant, 2) == 1
    assert recall_at_k(ranked, relevant, 2) == 0.5
    assert recall_at_k(ranked, relevant, 3) == 1.0


def test_precision_and_mrr():
    ranked = ["x", "a", "b"]
    relevant = {"a", "b"}
    assert precision_at_k(ranked, relevant, 3) == pytest.approx(2 / 3)
    assert mrr(ranked, relevant) == 0.5
    assert mrr(["x", "y"], relevant) == 0.0


def test_ndcg_prefers_early_hits():
    assert ndcg_at_k(["a", "b"], {"a", "b"}, 2) == 1.0
    assert ndcg_at_k(["b", "a"], {"a", "b"}, 2) == 1.0
    assert ndcg_at_k(["x", "a"], {"a"}, 2) < 1.0


def test_keyword_coverage():
    assert keyword_coverage("学生可以申请转专业", ["转专业", "申请"]) == 1.0
    assert keyword_coverage("学生可以申请转专业", ["转专业", "休学"]) == 0.5
    assert keyword_coverage("任意内容", []) == 1.0
