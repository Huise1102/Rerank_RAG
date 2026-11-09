"""入库编排，把加载、分块与索引串成一条入库链路。

被 Celery 任务与种子数据脚本共用，避免两处各写一遍解析、分块、索引的编排而产生行为差异。

两道防线：分块文件一次性写入，同一批文件全部解析分块后合并、再统一落盘，
不在逐文件循环里写，否则后一个文件覆盖前一个；入库结束做一致性校验，
分块文件块数、向量索引条数与 BM25 索引条数必须相等，不一致直接报错中止，
避免索引与数据对不上的静默错误。

增量与重建：知识库已有索引时新文档块追加到索引，不重建；
尚无索引时用全部块构建一次。
"""

from __future__ import annotations

import functools
import threading
from pathlib import Path
from typing import Any, Sequence

from src.core.config import settings
from src.core.logging import get_logger
from src.ingest.indexer import (
    append_chunks_to_index,
    build_kb_index,
    delete_kb_index,
    index_stats,
)
from src.ingest.loader import load_document
from src.ingest.splitter import (
    Chunk,
    ChunkResult,
    chunks_file,
    load_chunks,
    merge_results,
    save_chunks,
    split_documents,
    to_documents,
)

logger = get_logger(__name__)


# 索引变更互斥锁。上传的增量入库、删除文档后的整体重建与删除知识库的清理
# 都在后台线程里执行，可能彼此并发，例如一次拖入多个文件或边删边传；
# 不加锁会出现一方读写分块文件、另一方删建集合并重嵌入的交叉，
# 最终导致分块文件块数与向量、BM25 条数不一致。
# 锁只把这几条变更串行化，不改变任何入库参数。
_INDEX_LOCK = threading.Lock()


def _index_guard(fn):
    """串行化索引变更函数，避免后台并发写入导致索引与分块文件不一致。"""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        with _INDEX_LOCK:
            return fn(*args, **kwargs)

    return wrapper


def split_file(path: str | Path, strategy: str) -> ChunkResult:
    """解析并分块单个文件，返回该文件的分块结果。"""
    pages = load_document(path)
    if not pages:
        return ChunkResult(chunks=[], strategy=strategy, params={})
    docs = to_documents(pages)
    return split_documents(docs, strategy=strategy)


def _verify_consistency(kb_id: int, strategy: str, expected: int) -> dict[str, Any]:
    """校验分块文件、向量索引、BM25 索引三者条数一致。"""
    stats = index_stats(kb_id, strategy)
    actual_vec = int(stats.get("vector_count") or 0)
    actual_bm25 = int(stats.get("bm25_count") or 0)
    chunks_in_file = int(stats.get("chunks") or 0)
    if not (chunks_in_file == actual_vec == actual_bm25 == expected):
        raise RuntimeError(
            f"kb={kb_id}/{strategy} 入库一致性校验失败："
            f"分块文件={chunks_in_file} 向量索引={actual_vec} "
            f"BM25索引={actual_bm25}，期望={expected}"
        )
    return stats


@_index_guard
def ingest_document(kb_id: int, file_path: str | Path, strategy: str) -> dict[str, Any]:
    """把单个用户上传文档解析入库，采用增量方式。

    返回文件名、新增块数、总块数、向量条数与 BM25 条数。
    """
    name = strategy.strip().lower()
    new = split_file(file_path, name)
    if new.total == 0:
        raise ValueError(f"文档未解析出任何文本内容：{file_path}")

    # 读取已有分块，合并后一次性落盘
    if chunks_file(kb_id, name).exists():
        existing = load_chunks(kb_id, name)
        merged = merge_results([existing, new])
        has_index = bool(existing.chunks)
    else:
        merged = new
        has_index = False

    sources = sorted({c.source for c in merged.chunks})
    save_chunks(merged, kb_id, name, source_files=sources)

    if has_index:
        result = append_chunks_to_index(kb_id, name, new.chunks, show_progress=False)
        logger.info("增量入库 kb=%d %s added=%d", kb_id, Path(file_path).name, result.get("added"))
    else:
        report = build_kb_index(kb_id, name, chunks=merged.chunks, rebuild=True, show_progress=False)
        result = {"added": report.chunks, "vector_count": report.vector.get("count"),
                  "bm25_count": report.bm25.get("count")}

    stats = _verify_consistency(kb_id, name, merged.total)

    _clear_pipelines()
    return {
        "filename": Path(file_path).name,
        "added_chunks": new.total,
        "total_chunks": merged.total,
        "vector_count": stats.get("vector_count", 0),
        "bm25_count": stats.get("bm25_count", 0),
    }


@_index_guard
def ingest_paths(
    kb_id: int,
    paths: Sequence[str | Path],
    strategy: str,
    *,
    show_progress: bool = False,
) -> dict[str, Any]:
    """批量入库，供种子数据使用：全部解析分块后一次性写入并构建索引。

    返回总块数、每个文件的块数、向量条数、BM25 条数与索引报告。
    """
    name = strategy.strip().lower()
    results: list[ChunkResult] = []
    per_file: dict[str, int] = {}

    for p in paths:
        r = split_file(p, name)
        results.append(r)
        per_file[Path(p).name] = r.total

    merged = merge_results(results)
    if merged.total == 0:
        raise ValueError(f"知识库 {kb_id} 未解析出任何文本，无法构建索引")

    save_chunks(merged, kb_id, name, source_files=sorted(per_file))
    report = build_kb_index(
        kb_id, name, chunks=merged.chunks, rebuild=True, show_progress=show_progress
    )
    stats = _verify_consistency(kb_id, name, merged.total)

    logger.info("批量入库完成 kb=%d files=%d chunks=%d", kb_id, len(per_file), merged.total)
    _clear_pipelines()
    return {
        "total_chunks": merged.total,
        "per_file": per_file,
        "vector_count": stats.get("vector_count", 0),
        "bm25_count": stats.get("bm25_count", 0),
        "report": report,
    }


@_index_guard
def remove_source(kb_id: int, filename: str, strategy: str) -> int:
    """从索引中移除某个来源文件的全部分块（删除文档时调用）。

    通过过滤分块文件后整体重建实现；返回被移除的块数。
    """
    name = strategy.strip().lower()
    if not chunks_file(kb_id, name).exists():
        delete_kb_index(kb_id, name)
        _clear_pipelines()
        return 0

    existing = load_chunks(kb_id, name)
    kept: list[Chunk] = [c for c in existing.chunks if c.source != filename]
    removed = existing.total - len(kept)

    if not kept:
        chunks_file(kb_id, name).unlink(missing_ok=True)
        delete_kb_index(kb_id, name)
        _clear_pipelines()
        return removed

    remaining = ChunkResult(chunks=kept, strategy=name, params=existing.params)
    save_chunks(remaining, kb_id, name, source_files=sorted({c.source for c in kept}))
    build_kb_index(kb_id, name, chunks=kept, rebuild=True, show_progress=False)
    _verify_consistency(kb_id, name, remaining.total)
    _clear_pipelines()
    return removed


@_index_guard
def drop_kb(kb_id: int, strategy: str | None = None) -> None:
    """删除知识库的全部索引与分块文件。"""
    name = (strategy or settings.chunk.default_strategy).strip().lower()
    delete_kb_index(kb_id, name)
    cf = chunks_file(kb_id, name)
    if cf.exists():
        cf.unlink(missing_ok=True)
    _clear_pipelines()


def _clear_pipelines() -> None:
    """索引变更后清空检索管线缓存，使下次检索重新加载。"""
    try:
        from src.retrieval.pipeline import clear_pipelines

        clear_pipelines()
    except Exception as exc:  # noqa: BLE001 - 清理失败不影响入库结果
        logger.warning("清空检索管线缓存失败: %s", exc)
