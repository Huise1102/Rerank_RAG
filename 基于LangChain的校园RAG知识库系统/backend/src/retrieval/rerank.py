import re
from typing import Any, Optional, Sequence

from langchain_core.callbacks import Callbacks
from langchain_core.documents import Document
from langchain_core.documents.compressor import BaseDocumentCompressor
from pydantic import ConfigDict

TABLE_RE = re.compile(r"表\s*(\d{1,2})")
ARTICLE_RE = re.compile(r"第[一二三四五六七八九十百千0-9]+条")
# 用于区分同名条款/同名表格所属的文件族
SECTION_KEYWORDS = (
    "自然科学",
    "人文社会科学",
    "教学研究",
    "横向项目",
    "绩效工资",
    "专项奖励",
    "补充意见",
    "指导学生",
)


def _table_no(text: str) -> str:
    match = TABLE_RE.search(text or "")
    return match.group(1) if match else ""


def _bonus(doc: Document, query: str, spread: float, weights: dict) -> float:
    """按题面显式提到的表号、条号、文件族给文档加分，缓解同名内容抢排。"""
    meta = doc.metadata or {}
    caption = str(meta.get("table_caption") or "")
    section = str(meta.get("section_title") or "")
    total = 0.0

    query_table = _table_no(query)
    if query_table and _table_no(caption) == query_table:
        total += weights["table"] * spread

    article = ARTICLE_RE.search(query)
    if article and str(meta.get("article_no") or "") == article.group(0):
        total += weights["article"] * spread

    families = [word for word in SECTION_KEYWORDS if word in query]
    if families and any(word in section for word in families):
        total += weights["section"] * spread
    return min(total, weights["cap"] * spread)


class MetadataBoostReranker(BaseDocumentCompressor):
    """交叉编码精排，并叠加表号/条号/文件族对齐加权。"""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    model: Any
    top_n: int = 5
    table_weight: float = 0.35
    article_weight: float = 0.12
    section_weight: float = 0.08
    max_ratio: float = 0.45

    def compress_documents(
        self,
        documents: Sequence[Document],
        query: str,
        callbacks: Optional[Callbacks] = None,
    ) -> Sequence[Document]:
        if not documents:
            return []
        pinned = [doc for doc in documents if (doc.metadata or {}).get("retrieval_pin")]
        others = [doc for doc in documents if not (doc.metadata or {}).get("retrieval_pin")]
        if not others:
            return pinned[: self.top_n]

        pairs = [(query, doc.page_content) for doc in others]
        scores = [float(score) for score in self.model.score(pairs)]
        spread = max(scores) - min(scores)
        if spread <= 1e-6:
            spread = max(abs(score) for score in scores) or 1.0
        weights = {
            "table": self.table_weight,
            "article": self.article_weight,
            "section": self.section_weight,
            "cap": self.max_ratio,
        }
        ranked = [
            (doc, score + _bonus(doc, query, spread, weights))
            for doc, score in zip(others, scores)
        ]
        ranked.sort(key=lambda item: item[1], reverse=True)
        kept = pinned[: self.top_n]
        kept_keys = {
            str((doc.metadata or {}).get("chunk_id") or doc.page_content[:64]) for doc in kept
        }
        for doc, _ in ranked:
            if len(kept) >= self.top_n:
                break
            key = str((doc.metadata or {}).get("chunk_id") or doc.page_content[:64])
            if key in kept_keys:
                continue
            kept.append(doc)
            kept_keys.add(key)
        return kept
