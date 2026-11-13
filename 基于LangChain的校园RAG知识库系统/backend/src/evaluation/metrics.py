import math


def hit_at_k(ranked: list[str], relevant: set[str], k: int) -> float:
    if not relevant:
        return 0.0
    return 1.0 if any(item in relevant for item in ranked[:k]) else 0.0


def precision_at_k(ranked: list[str], relevant: set[str], k: int) -> float:
    if k <= 0:
        return 0.0
    top = ranked[:k]
    return sum(1 for item in top if item in relevant) / k


def recall_at_k(ranked: list[str], relevant: set[str], k: int) -> float:
    if not relevant:
        return 0.0
    top = ranked[:k]
    return sum(1 for item in top if item in relevant) / len(relevant)


def mrr(ranked: list[str], relevant: set[str]) -> float:
    for index, item in enumerate(ranked, start=1):
        if item in relevant:
            return 1.0 / index
    return 0.0


def ndcg_at_k(ranked: list[str], relevant: set[str], k: int) -> float:
    dcg = 0.0
    for index, item in enumerate(ranked[:k], start=1):
        if item in relevant:
            dcg += 1.0 / math.log2(index + 1)
    ideal = sum(1.0 / math.log2(index + 1) for index in range(1, min(len(relevant), k) + 1))
    return dcg / ideal if ideal else 0.0


def keyword_coverage(text: str, keywords: list[str]) -> float:
    if not keywords:
        return 1.0
    matched = sum(1 for keyword in keywords if keyword and keyword in text)
    return matched / len(keywords)


def aggregate(values: list[float]) -> float:
    return round(sum(values) / len(values), 4) if values else 0.0
