"""阶段 1 —— 文本分块（splitter.py）：两种可对比的分块策略。

    fixed      固定长度：按字符窗口滑动（chunk_size=500 / overlap=50），
               切分时尽量在句末标点或换行处断开，避免把词切成两半
    recursive  递归语义：LangChain RecursiveCharacterTextSplitter，
               分隔符 ["\\n\\n", "\\n", "。", " ", ""]，按 段落->行->句子->词->字符 递归下钻

两者都保留 loader 的元数据（source / page / ...）并补充 chunk_id、chunk_index、total_chunks、
strategy。产物以知识库维度落盘：``data/processed/kb_<kb_id>/chunks_<strategy>.json``，
不同知识库 / 分块策略互不覆盖。

主流程::

    pages = load_document("product/resource/法律领域/xxx.pdf")   # -> list[LoadedPage]
    docs = to_documents(pages)                            # -> list[Document]
    result = split_documents(docs, strategy="recursive")
    save_chunks(result, kb_id=1, strategy="recursive")
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Sequence

from src.core.config import StrategySpec, settings

#: 分块产物 JSON 的结构版本号（结构变更时递增，便于兼容旧文件）
CHUNK_FORMAT_VERSION = 1


@dataclass
class Chunk:
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def chunk_id(self) -> str:
        return str(self.metadata.get("chunk_id", ""))

    @property
    def source(self) -> str:
        return str(self.metadata.get("source", ""))

    @property
    def page(self) -> int | str:
        return self.metadata.get("page", "")

    def __len__(self) -> int:
        return len(self.text)


@dataclass
class ChunkResult:
    chunks: list[Chunk]
    strategy: str
    params: dict[str, Any]

    @property
    def total(self) -> int:
        return len(self.chunks)

    def lengths(self) -> list[int]:
        return [len(c) for c in self.chunks]

    def stats(self) -> dict[str, Any]:
        ls = self.lengths()
        if not ls:
            return {"count": 0, "min": 0, "max": 0, "avg": 0.0, "median": 0, "total_chars": 0}
        srt = sorted(ls)
        mid = len(srt) // 2
        median = srt[mid] if len(srt) % 2 else (srt[mid - 1] + srt[mid]) // 2
        return {
            "count": len(ls),
            "min": min(ls),
            "max": max(ls),
            "avg": round(sum(ls) / len(ls), 1),
            "median": median,
            "total_chars": sum(ls),
        }

    def summary(self) -> str:
        s = self.stats()
        if not s["count"]:
            return f"策略 {self.strategy}: 无分块产出"
        return (
            f"策略 {self.strategy}: {s['count']} 块 | "
            f"长度 min/中位/均值/max = {s['min']}/{s['median']}/{s['avg']}/{s['max']} | "
            f"共 {s['total_chars']:,} 字符"
        )



def make_chunk_id(source: str, page: int | str, index: int, text: str, strategy: str) -> str:
    """生成稳定且不重复的 chunk_id：``策略:文件名:p页码:c序号:正文md5前8位``。

    同一份分块结果重复写入得到同样的 id；但 id 含页码/序号/正文哈希，重新分块
    （改参数或改清洗）后重建索引，id 会整体变化 —— 不要当长期稳定的外部主键用。
    """
    digest = hashlib.md5(text.encode("utf-8")).hexdigest()[:8]
    return f"{strategy}:{source}:p{page}:c{index}:{digest}"


def _best_break(text: str, ideal: int, min_pos: int) -> int:
    """在 ideal 位置附近向前找最佳断点：段落换行 > 句末标点 > 行尾 > 空格；
    找不到合适断点时返回 ideal（硬切）。

    :param ideal: 理想切分位置（chunk_size）
    :param min_pos: 允许向前搜索的最远位置，避免块过短
    """
    if ideal >= len(text):
        return len(text)

    window = text[min_pos:ideal]

    # 1) 段落边界
    pos = window.rfind("\n\n")
    if pos != -1:
        return min_pos + pos + 2
    # 2) 句末标点（中文标点优先，避免句子被拆开）
    for punct in ("。", "！", "？", "；", ".", "!", "?", ";"):
        pos = window.rfind(punct)
        if pos != -1:
            return min_pos + pos + 1
    # 3) 换行
    pos = window.rfind("\n")
    if pos != -1:
        return min_pos + pos + 1
    # 4) 空格
    pos = window.rfind(" ")
    if pos != -1:
        return min_pos + pos + 1
    return ideal


def _merge_short_chunks(chunks: list[str], min_size: int) -> list[str]:
    """把过短的碎块并入相邻块，避免产生大量无信息量的碎片。

    非首块过短并入**前**一块；首块没有前块可并，必须并入后一块，
    否则文档开头的孤立小标题会变成一个 8 字符的碎片块混进索引。
    """
    if min_size <= 0 or not chunks:
        return chunks

    merged: list[str] = []
    for piece in chunks:
        if merged and len(piece) < min_size:
            merged[-1] = f"{merged[-1]}\n{piece}".strip()
        else:
            merged.append(piece)

    # 首块过短：并入后一块
    if len(merged) > 1 and len(merged[0]) < min_size:
        head = merged.pop(0)
        merged[0] = f"{head}\n{merged[0]}".strip()
    # 末块过短：并入前一块
    if len(merged) > 1 and len(merged[-1]) < min_size:
        tail = merged.pop()
        merged[-1] = f"{merged[-1]}\n{tail}".strip()
    return merged


def _absorb_orphans(pieces: list[str], min_size: int) -> list[str]:
    """把过短的碎片并回相邻块。

    与 ``_merge_short_chunks`` 的区别：那个处理「切分阶段」的碎片，本函数处理
    「超长块二次切分」后新产生的碎片 —— 那一步在合并之后，必须再收一次尾，
    否则跨页断句留下的「定。」这类碎片会进入索引。

    优先并回**前**一块以保持原文顺序，首块则并给后一块；整页只有一个极短片段时保留原样。
    """
    if min_size <= 0 or not pieces:
        return pieces

    out: list[str] = []
    for piece in pieces:
        if out and len(piece) < min_size:
            out[-1] = f"{out[-1]}\n{piece}".strip()
        else:
            out.append(piece)

    if len(out) > 1 and len(out[0]) < min_size:
        head = out.pop(0)
        out[0] = f"{head}\n{out[0]}".strip()
    if len(out) > 1 and len(out[-1]) < min_size:
        tail = out.pop()
        out[-1] = f"{out[-1]}\n{tail}".strip()
    return out



def split_fixed(
    text: str,
    *,
    chunk_size: int = 500,
    chunk_overlap: int = 50,
    min_chunk_size: int = 80,
) -> list[str]:
    """固定长度分块（滑动窗口 + 边界对齐）。

    与朴素 ``range(0, len, size)`` 切法的区别：会在窗口内向前找最近的段落/句子边界
    作为断点，同时仍靠 overlap 保证跨块语义连续。

    :param chunk_size: 目标块长度（字符）；:param chunk_overlap: 相邻块重叠字符数
    :param min_chunk_size: 小于该长度的碎块并入前一块
    """
    text = text.strip()
    if not text:
        return []
    if chunk_size <= 0:
        raise ValueError("chunk_size 必须为正数")
    if chunk_overlap >= chunk_size:
        raise ValueError(f"chunk_overlap({chunk_overlap}) 必须小于 chunk_size({chunk_size})")
    if len(text) <= chunk_size:
        return [text]

    chunks: list[str] = []
    start = 0
    n = len(text)
    while start < n:
        ideal = min(start + chunk_size, n)
        if ideal >= n:
            end = n
        else:
            # 允许向前最多回退 25%，保证块不会过短
            end = _best_break(text, ideal, max(start + 1, int(start + chunk_size * 0.75)))
        piece = text[start:end].strip()
        if piece:
            chunks.append(piece)
        if end >= n:
            break
        # 下一块起点回退 overlap，但不倒退回起点之前
        start = max(end - chunk_overlap, start + 1)

    return _merge_short_chunks(chunks, min_chunk_size)



def split_recursive_semantic(
    text: str,
    *,
    chunk_size: int = 500,
    chunk_overlap: int = 50,
    separators: Sequence[str] | None = None,
    min_chunk_size: int = 80,
) -> list[str]:
    """递归语义分块，基于 LangChain RecursiveCharacterTextSplitter。

    分隔符默认 ["\\n\\n", "\\n", "。", " ", ""]，按段落 -> 行 -> 句子 -> 词 -> 字符
    递归下探，最大限度保持语义单元完整。
    """
    text = text.strip()
    if not text:
        return []
    seps = list(separators) if separators else ["\n\n", "\n", "。", " ", ""]

    from langchain_text_splitters import RecursiveCharacterTextSplitter

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=seps,
        keep_separator=True,
        length_function=len,
        strip_whitespace=True,
    )
    pieces = splitter.split_text(text)
    return _merge_short_chunks(pieces, min_chunk_size)



def split_documents(
    documents: Sequence[Any],
    strategy: str | None = None,
    spec: StrategySpec | None = None,
    *,
    show_progress: bool = False,
) -> ChunkResult:
    """对 LangChain Document 列表执行指定策略的分块。

    :param documents: ``to_documents()`` 的产物
    :param strategy: "fixed" | "recursive"，默认取 config.chunk.default_strategy
    :param spec: 显式指定策略参数（不传则从 config 读取）
    :param show_progress: 是否显示 tqdm 进度条；:returns: ChunkResult
    """
    name = (strategy or settings.chunk.default_strategy).strip().lower()
    if name not in ("fixed", "recursive"):
        raise ValueError(f"未知分块策略 '{strategy}'，可选：fixed | recursive")

    sp = spec or settings.chunk.for_strategy(name)
    params = {
        "chunk_size": sp.chunk_size,
        "chunk_overlap": sp.chunk_overlap,
        "min_chunk_size": sp.min_chunk_size,
        "max_chunk_size": sp.max_chunk_size,
        "separators": list(sp.separators) if name == "recursive" else None,
    }

    iterator: Iterable[Any] = documents
    if show_progress:
        from tqdm import tqdm

        iterator = tqdm(documents, desc=f"分块[{name}]", unit="页", ncols=90)

    chunks: list[Chunk] = []
    for doc in iterator:
        text = getattr(doc, "page_content", "") or ""
        base_meta = dict(getattr(doc, "metadata", {}) or {})
        if not text.strip():
            continue

        if name == "fixed":
            pieces = split_fixed(
                text,
                chunk_size=sp.chunk_size,
                chunk_overlap=sp.chunk_overlap,
                min_chunk_size=sp.min_chunk_size,
            )
        else:
            pieces = split_recursive_semantic(
                text,
                chunk_size=sp.chunk_size,
                chunk_overlap=sp.chunk_overlap,
                separators=sp.separators,
                min_chunk_size=sp.min_chunk_size,
            )

        # max_chunk_size 是硬上限：超长块按固定窗口再切一次
        if sp.max_chunk_size:
            refined: list[str] = []
            for piece in pieces:
                if len(piece) > sp.max_chunk_size:
                    refined.extend(
                        split_fixed(
                            piece,
                            chunk_size=sp.max_chunk_size,
                            chunk_overlap=sp.chunk_overlap,
                            min_chunk_size=sp.min_chunk_size,
                        )
                    )
                else:
                    refined.append(piece)
            pieces = refined

        # 兜底：合并后依旧过短的碎块并回**同一文档**的相邻块。碎片多来自跨页断句的残留
        # （如某页只剩一行「定。」），丢弃会丢信息、留在索引里会稀释检索质量，故并回邻块
        pieces = _absorb_orphans(pieces, sp.min_chunk_size)

        total = len(pieces)
        source = str(base_meta.get("source", "unknown"))
        page = base_meta.get("page", 0)
        for i, piece in enumerate(pieces):
            meta = dict(base_meta)
            meta.update(
                {
                    "chunk_id": make_chunk_id(source, page, i, piece, name),
                    "chunk_index": i,
                    "total_chunks": total,
                    "strategy": name,
                    "char_count": len(piece),
                }
            )
            chunks.append(Chunk(text=piece, metadata=meta))

    return ChunkResult(chunks=chunks, strategy=name, params=params)



def to_documents(pages: Sequence[Any], cfg: Any = None):
    """``LoadedPage`` 列表 -> LangChain Document 列表（供 ``split_documents`` 使用）。

    元数据字段由 ``chunk.metadata_fields`` 控制；loader 的 ``LoadedPage`` 无
    ``metadata()`` 方法，这里直接按字段构造。
    """
    from langchain_core.documents import Document

    chunk_cfg = settings.chunk if cfg is None else cfg
    fields = set(chunk_cfg.metadata_fields)

    docs: list[Document] = []
    for pg in pages:
        full = {
            "source": getattr(pg, "source", ""),
            "file_name": getattr(pg, "source", ""),
            "file_path": getattr(pg, "file_path", ""),
            "page": getattr(pg, "page", 0),
            "total_pages": getattr(pg, "total_pages", 0),
        }
        meta = {k: v for k, v in full.items() if k in fields}
        meta.setdefault("source", full["source"])
        meta.setdefault("page", full["page"])
        docs.append(Document(page_content=getattr(pg, "text", ""), metadata=meta))
    return docs



def chunks_file(kb_id: int, strategy: str) -> Path:
    """分块产物路径：data/processed/kb_<kb_id>/chunks_<strategy>.json"""
    return settings.paths.processed_dir / f"kb_{int(kb_id)}" / f"chunks_{strategy}.json"


def save_chunks(
    result: ChunkResult,
    kb_id: int,
    strategy: str | None = None,
    *,
    source_files: Sequence[str] | None = None,
) -> Path:
    """把分块结果写入知识库专属 JSON（UTF-8，不转义中文），返回文件路径。"""
    name = strategy or result.strategy
    path = chunks_file(kb_id, name)
    path.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "format_version": CHUNK_FORMAT_VERSION,
        "strategy": name,
        "params": result.params,
        "stats": result.stats(),
        "built_at": datetime.now().isoformat(timespec="seconds"),
        "source_files": list(source_files or sorted({c.source for c in result.chunks})),
        "chunks": [{"text": c.text, "metadata": c.metadata} for c in result.chunks],
    }
    # 先写临时文件再替换，避免中途失败留下半个文件
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    return path


def load_chunks(kb_id: int, strategy: str) -> ChunkResult:
    """读取知识库分块产物；文件不存在时抛出带下一步命令提示的 FileNotFoundError。"""
    path = chunks_file(kb_id, strategy)
    if not path.exists():
        raise FileNotFoundError(
            f"未找到知识库 {kb_id} 的分块文件: {path}\n"
            f"请先完成该知识库的文档入库（load -> split -> save -> index）。"
        )
    payload = json.loads(path.read_text(encoding="utf-8"))
    chunks = [
        Chunk(text=item["text"], metadata=dict(item.get("metadata", {})))
        for item in payload.get("chunks", [])
    ]
    return ChunkResult(
        chunks=chunks,
        strategy=payload.get("strategy", strategy),
        params=payload.get("params", {}),
    )


def merge_results(results: Sequence[ChunkResult]) -> ChunkResult:
    """合并多个 ``ChunkResult``（同一知识库多文档入库时用）。

    分块序号（chunk_index/total_chunks）在 ``split_documents`` 内已按单文档写好，
    这里只按顺序拼接，不重排；策略与参数取第一个非空结果。
    """
    usable = [r for r in results if r is not None]
    if not usable:
        return ChunkResult(chunks=[], strategy=settings.chunk.default_strategy, params={})

    merged_chunks: list[Chunk] = []
    for r in usable:
        merged_chunks.extend(r.chunks)
    return ChunkResult(
        chunks=merged_chunks,
        strategy=usable[0].strategy,
        params=dict(usable[0].params),
    )
