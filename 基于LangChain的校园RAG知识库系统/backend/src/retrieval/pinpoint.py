import re
from typing import Any

from langchain_core.callbacks import CallbackManagerForRetrieverRun
from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever
from pydantic import ConfigDict

from src.retrieval.scope import RetrievalScope, build_chroma_filter
from src.retrieval.vector_store import get_vector_store

ARTICLE_RE = re.compile(r"第[一二三四五六七八九十百千0-9]+条")
# 口语里出现、但在条款里写法不同的高频词，命中后强制保留一条
PIN_TERMS = (
    "访学",
    "脱产进修",
    "就高不重复",
)
# 题面点名的办法族，条号补召回时只在该族内挑选，避免同名条款跨文件串台
FAMILY_WORDS = ("自然科学", "人文社会科学", "教学研究")
# 题面未点名办法、但能指向某个办法族的说法
FAMILY_ALIASES = {"教学成果奖": "教学研究", "教研业绩": "教学研究"}
# 与条款正文里的“说明/计分标准”表格对应的对象词，用于补召回被条号块漏掉的说明表
SUBJECT_TERMS = ("论文", "专利", "项目", "平台", "团队", "教材", "竞赛", "成果奖", "著作")
# 奖项等级词，题面点名时补召回只保留含该等级的块
LEVEL_TERMS = ("特等奖", "一等奖", "二等奖", "三等奖")
# 题面对象词 -> 表名里的对象词，用于剔除同章节下不匹配的表格
TABLE_OBJECTS = {
    "指导": "指导",
    "带领": "指导",
    "学生": "指导",
    "论文": "论文",
    "期刊": "论文",
    "专利": "专利",
    "发明": "专利",
    "教材": "教材",
    "著作": "教材",
}


def _merge_where(base: Any, extra: dict) -> dict:
    if not base:
        return extra
    if isinstance(base, dict) and "$and" in base:
        return {"$and": list(base["$and"]) + [extra]}
    return {"$and": [base, extra]}


def _bigrams(text: str) -> set[str]:
    clean = "".join(character for character in text if character.isalnum())
    return {clean[index : index + 2] for index in range(len(clean) - 1)}


def _overlap(query: str, text: str) -> int:
    return len(_bigrams(query) & _bigrams(text))


class PinpointRetriever(BaseRetriever):
    """精排前补召回：题面条号与专有词强制入候选，并剔除同章节下口径不符的表格。"""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    inner: BaseRetriever
    kb_id: int
    scope: RetrievalScope
    max_pins: int = 2

    def _fetch(self, article: str = "", term: str = "", limit: int = 40) -> list[Document]:
        where = build_chroma_filter(self.scope, self.kb_id)
        if article:
            where = _merge_where(where, {"article_no": article})
        document_filter = {"$contains": term} if term else None
        store = get_vector_store(self.kb_id)
        payload = store._collection.get(
            where=where,
            where_document=document_filter,
            include=["documents", "metadatas"],
            limit=limit,
        )
        documents: list[Document] = []
        for text, metadata in zip(payload.get("documents", []), payload.get("metadatas", [])):
            if text:
                documents.append(Document(page_content=text, metadata=dict(metadata or {})))
        return documents

    @staticmethod
    def _rank(query: str, documents: list[Document], term: str = "") -> list[Document]:
        def score(document: Document) -> tuple[int, float]:
            metadata = document.metadata or {}
            label = f"{metadata.get('section_title') or ''}{metadata.get('table_caption') or ''}"
            text = document.page_content or ""
            hit_term = 1 if term and term in text else 0
            return hit_term, 2 * _overlap(query, label) + _overlap(query, text)

        return sorted(documents, key=score, reverse=True)

    def _pin(self, query: str) -> list[Document]:
        pins: list[Document] = []
        families = [word for word in FAMILY_WORDS if word in query]
        families += [
            target for alias, target in FAMILY_ALIASES.items() if alias in query and target not in families
        ]
        for article in dict.fromkeys(ARTICLE_RE.findall(query)):
            candidates = self._fetch(article=article)
            if families:
                scoped = [
                    document
                    for document in candidates
                    if any(word in str((document.metadata or {}).get("section_title") or "") for word in families)
                ]
                candidates = scoped or candidates
            pinned = self._rank(query, candidates)
            if pinned:
                pins.append(pinned[0])

        if families:
            levels = [word for word in LEVEL_TERMS if word in query]
            for subject in SUBJECT_TERMS:
                if subject not in query:
                    continue
                candidates = [
                    document
                    for document in self._fetch(term=subject)
                    if any(
                        word in str((document.metadata or {}).get("section_title") or "")
                        for word in families
                    )
                ]
                if levels:
                    leveled = [
                        document
                        for document in candidates
                        if any(level in document.page_content for level in levels)
                    ]
                    candidates = leveled or candidates
                pinned = self._rank(query, candidates, subject)
                if pinned:
                    pins.append(pinned[0])
                break
        for term in PIN_TERMS:
            if term not in query:
                continue
            pinned = self._rank(query, self._fetch(term=term), term=term)
            if pinned:
                pins.append(pinned[0])
            break

        seen: set[str] = set()
        result: list[Document] = []
        for document in pins:
            key = str((document.metadata or {}).get("chunk_id") or document.page_content[:64])
            if key in seen:
                continue
            seen.add(key)
            document.metadata["retrieval_pin"] = True
            result.append(document)
        return result[: self.max_pins]

    @staticmethod
    def _drop_conflicting_tables(documents: list[Document], query: str) -> list[Document]:
        objects = {target for word, target in TABLE_OBJECTS.items() if word in query}
        if len(objects) != 1:
            return documents
        target = next(iter(objects))
        captions = {
            str((document.metadata or {}).get("table_caption") or "")
            for document in documents
        }
        captions.discard("")
        matched = {caption for caption in captions if target in caption}
        if not matched or len(matched) == len(captions):
            return documents
        matched_sections = {
            str((document.metadata or {}).get("section_title") or "")
            for document in documents
            if str((document.metadata or {}).get("table_caption") or "") in matched
        }
        kept: list[Document] = []
        for document in documents:
            metadata = document.metadata or {}
            caption = str(metadata.get("table_caption") or "")
            section = str(metadata.get("section_title") or "")
            if (
                caption
                and caption not in matched
                and section in matched_sections
                and not metadata.get("retrieval_pin")
            ):
                continue
            kept.append(document)
        return kept

    def _get_relevant_documents(
        self, query: str, *, run_manager: CallbackManagerForRetrieverRun
    ) -> list[Document]:
        documents = self._drop_conflicting_tables(list(self.inner.invoke(query)), query)
        pins = self._pin(query)
        if not pins:
            return documents

        pinned_keys = {
            str((document.metadata or {}).get("chunk_id") or document.page_content[:64])
            for document in pins
        }
        rest = [
            document
            for document in documents
            if str((document.metadata or {}).get("chunk_id") or document.page_content[:64])
            not in pinned_keys
        ]
        return pins + rest
