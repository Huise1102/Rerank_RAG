"""文本分块，提供两种可对比的策略。

fixed 固定长度，按字符窗口滑动切分，切分时尽量落在句末标点或换行处，避免把词切开。recursive 递归语义，基于 RecursiveCharacterTextSplitter，按段落、行、句子、词、字符的优先级逐层下钻。两种策略都保留加载阶段的元数据，并补充分块编号、序号、总块数与策略名，产物按策略名分别落盘，互不覆盖。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Sequence

from config import StrategySpec, settings

# 分块产物 JSON 的结构版本号，结构变更时递增
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
    """生成稳定且不重复的分块编号，由策略、文件名、页码、序号与正文哈希组成。

    同一份分块结果重复写入得到相同编号；编号包含页码、序号与正文哈希，重新分块或改变清洗规则后编号会整体变化，不能当作长期稳定的外部主键。
    """
    digest = hashlib.md5(text.encode("utf-8")).hexdigest()[:8]
    return f"{strategy}:{source}:p{page}:c{index}:{digest}"


def _best_break(text: str, ideal: int, min_pos: int) -> int:
    """在理想位置附近向前寻找最佳断点。

    优先级依次为段落换行、句末标点、行尾、空格；找不到合适断点时返回理想位置直接切分。向前搜索范围由最远位置限制，避免块过短。
    """
    if ideal >= len(text):
        return len(text)

    window = text[min_pos:ideal]

    # 1 段落边界
    pos = window.rfind("\n\n")
    if pos != -1:
        return min_pos + pos + 2
    # 2 句末标点，优先中文标点，避免句子被拆开
    for punct in ("。", "！", "？", "；", ".", "!", "?", ";"):
        pos = window.rfind(punct)
        if pos != -1:
            return min_pos + pos + 1
    # 3 换行
    pos = window.rfind("\n")
    if pos != -1:
        return min_pos + pos + 1
    # 4 空格
    pos = window.rfind(" ")
    if pos != -1:
        return min_pos + pos + 1
    return ideal


def _merge_short_chunks(chunks: list[str], min_size: int) -> list[str]:
    """把过短的碎块并入相邻块，避免产生无信息量的碎片。

    非首块过短时并入前一块；首块没有前块，必须并入后一块，否则文档开头的孤立小标题会变成很小的碎片混进索引。
    """
    if min_size <= 0 or not chunks:
        return chunks

    merged: list[str] = []
    for piece in chunks:
        if merged and len(piece) < min_size:
            merged[-1] = f"{merged[-1]}\n{piece}".strip()
        else:
            merged.append(piece)

    # 首块过短时并入后一块
    if len(merged) > 1 and len(merged[0]) < min_size:
        head = merged.pop(0)
        merged[0] = f"{head}\n{merged[0]}".strip()
    # 末块过短时并入前一块
    if len(merged) > 1 and len(merged[-1]) < min_size:
        tail = merged.pop()
        merged[-1] = f"{merged[-1]}\n{tail}".strip()
    return merged


def _absorb_orphans(pieces: list[str], min_size: int) -> list[str]:
    """把过短的碎片并回相邻块。

    用于超长块二次切分后新产生的碎片，该步骤在合并之后，必须再处理一次，否则跨页断句留下的极短片段会进入索引。优先并回前一块以保持原文顺序，首块则并给后一块；整页只有一个极短片段时保留原样。
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
    """固定长度分块，滑动窗口加边界对齐。

    与按固定步长直接切分的区别在于，切分前会在窗口内向前寻找最近的段落或句子边界作为断点，同时依靠重叠保证跨块语义连续。小于最小块长的碎片并入前一块。
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
            # 允许向前最多回退四分之一窗口，保证块不会过短
            end = _best_break(text, ideal, max(start + 1, int(start + chunk_size * 0.75)))
        piece = text[start:end].strip()
        if piece:
            chunks.append(piece)
        if end >= n:
            break
        # 下一块起点回退重叠长度，但不越过本块起点
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
    """递归语义分块，基于 LangChain 的递归字符切分器。

    分隔符默认按段落、行、句子、空格、字符的顺序逐层下探，尽量保持语义单元完整。
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
    """对文档列表按指定策略分块。

    策略名取固定长度或递归语义，未指定时用配置默认值；策略参数可显式传入，不传则从配置读取。进度开关控制是否显示进度条。返回分块结果对象。
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

        # 最大块长是硬性上限，超长块按固定窗口再切一次
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

        # 二次切分后依旧过短的碎片并回同一文档的相邻块。碎片多来自跨页断句的残留，丢弃会丢信息，留在索引中会稀释检索质量
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



def chunks_file(strategy: str, out_dir: str | Path | None = None) -> Path:
    """返回分块产物路径，文件名以策略名结尾。"""
    directory = Path(out_dir) if out_dir else settings.paths.processed_dir
    return directory / f"chunks_{strategy}.json"


def save_chunks(
    result: ChunkResult,
    strategy: str | None = None,
    out_dir: str | Path | None = None,
    *,
    source_files: Sequence[str] | None = None,
) -> Path:
    """把分块结果写入 JSON 文件，返回文件路径，中文不转义。"""
    name = strategy or result.strategy
    path = chunks_file(name, out_dir)
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
    # 先写中间文件再替换，避免中途失败留下半个文件
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    return path


def load_chunks(
    strategy: str,
    out_dir: str | Path | None = None,
) -> ChunkResult:
    """读取分块产物，文件不存在时抛出异常并提示下一步命令。"""
    path = chunks_file(strategy, out_dir)
    if not path.exists():
        raise FileNotFoundError(
            f"未找到分块文件: {path}\n"
            f"请先执行数据入库：\n"
            f"    python -m src.ingest.cli load --strategy {strategy}"
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


def to_documents(chunks: Sequence[Chunk]):
    """把分块列表转为文档对象列表，供索引入库使用。"""
    from langchain_core.documents import Document

    return [Document(page_content=c.text, metadata=dict(c.metadata)) for c in chunks]


def list_processed(out_dir: str | Path | None = None) -> list[Path]:
    directory = Path(out_dir) if out_dir else settings.paths.processed_dir
    if not directory.exists():
        return []
    return sorted(directory.glob("chunks_*.json"))
