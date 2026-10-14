"""初始化种子数据（scripts/seed_data.py）：预置「法律领域」知识库。

行为：
1. 预置 1 个知识库「法律领域」，``enabled_strategies`` 默认全开。
2. 扫描仓库根 ``resource/法律领域/`` 下的 pdf / docx / txt / md 四种格式文件
   （**只读引用**，不复制、不改写），复用 ``ingest_service.ingest_paths`` 一次性
   解析 -> 分块 -> 建索引（向量 + BM25），并在 ``documents`` 表登记 completed 记录。
3. 幂等：已存在同名知识库直接复用；同名文档（同文件名）已登记则跳过，不重复入库。

用法::

    python scripts/seed_data.py                       # 预置「法律领域」（默认 recursive）
    python scripts/seed_data.py --strategy fixed      # 指定分块策略
    python scripts/seed_data.py --kb-name 法律领域 --resource-dir D:\\resource
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

#: 后端项目根：scripts/seed_data.py -> scripts -> backend
BACKEND_ROOT: Path = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from sqlalchemy import select  # noqa: E402

from src.core.config import settings  # noqa: E402
from src.core.logging import get_logger, setup_logging  # noqa: E402
from src.db.models.document import Document  # noqa: E402
from src.db.models.knowledge_base import DEFAULT_STRATEGIES, KnowledgeBase  # noqa: E402
from src.db.session import AsyncSessionLocal  # noqa: E402
from src.ingest.loader import supported_extensions  # noqa: E402
from src.services.ingest_service import ingest_paths  # noqa: E402

logger = get_logger("scripts.seed_data")

PRESET_KB_NAME = "法律领域"

#: 同一部法律多格式副本时的保留优先级（pdf > docx > md > txt）
_EXT_ORDER: dict[str, int] = {ext: i for i, ext in enumerate(supported_extensions())}


def discover_files(root: Path) -> list[Path]:
    """扫描目录下四种格式文件，同一文件名（stem）只保留优先级最高的一个副本。"""
    if not root.exists():
        raise FileNotFoundError(f"资源目录不存在: {root}")

    by_stem: dict[str, Path] = {}
    candidates = sorted(
        (p for p in root.iterdir() if p.is_file()),
        key=lambda p: (_EXT_ORDER.get(p.suffix.lower().lstrip("."), 99), p.name),
    )
    for p in candidates:
        ext = p.suffix.lower().lstrip(".")
        if ext not in _EXT_ORDER:
            continue
        by_stem.setdefault(p.stem, p)  # 先到（优先级高）者胜出
    return sorted(by_stem.values(), key=lambda p: p.name)


def _resolve_resource_dir(kb_name: str, override: str | None) -> Path:
    """确定语料目录：优先命令行 > resource/<kb_name> > resource/。"""
    if override:
        return Path(override).expanduser().resolve()
    base = settings.resource_dir
    sub = base / kb_name
    return sub if sub.exists() else base


async def seed(kb_name: str, strategy: str, resource_override: str | None) -> None:
    root = _resolve_resource_dir(kb_name, resource_override)
    files = discover_files(root)
    if not files:
        logger.warning("目录 %s 下没有可入库的 pdf/docx/txt/md 文件", root)
        return
    logger.info("发现 %d 个待入库文件: %s", len(files), [p.name for p in files])

    async with AsyncSessionLocal() as db:
        kb = (
            await db.execute(select(KnowledgeBase).where(KnowledgeBase.name == kb_name))
        ).scalar_one_or_none()
        if kb is None:
            kb = KnowledgeBase(
                name=kb_name,
                description=f"预置知识库：{kb_name}（语料只读引用自 {root}）",
                chunk_strategy=strategy,
                enabled_strategies=dict(DEFAULT_STRATEGIES),
            )
            db.add(kb)
            await db.commit()
            await db.refresh(kb)
            logger.info("已创建知识库 id=%s name=%s", kb.id, kb.name)
        else:
            logger.info("知识库「%s」已存在 id=%s，复用", kb.name, kb.id)

        rows = await db.execute(select(Document.filename).where(Document.kb_id == kb.id))
        existing = {name for (name,) in rows.all()}
        to_add = [p for p in files if p.name not in existing]
        if not to_add:
            logger.info("全部文档均已入库，无需重复处理（幂等跳过）")
            return

        # 用全部文件重建索引，保证分块文件与索引一致
        result = ingest_paths(kb.id, files, kb.chunk_strategy, show_progress=True)
        per_file: dict[str, int] = result["per_file"]

        for p in to_add:
            db.add(
                Document(
                    kb_id=kb.id,
                    filename=p.name,
                    file_path=str(p),  # 只读引用，不复制
                    file_size=p.stat().st_size,
                    file_type=p.suffix.lower().lstrip("."),
                    status="completed",
                    chunk_count=int(per_file.get(p.name, 0)),
                )
            )
        await db.commit()
        logger.info(
            "种子数据入库完成 kb=%s 新增文档=%d 总块数=%d（向量=%d / BM25=%d）",
            kb.id, len(to_add), result["total_chunks"],
            result.get("vector_count", 0), result.get("bm25_count", 0),
        )


async def _main(args: argparse.Namespace) -> None:
    setup_logging()
    settings.ensure_dirs()
    await seed(args.kb_name, args.strategy, args.resource_dir)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="预置「法律领域」知识库并导入 resource 语料")
    parser.add_argument("--kb-name", default=PRESET_KB_NAME, help="知识库名称")
    parser.add_argument(
        "--strategy", default=settings.chunk.default_strategy,
        choices=("fixed", "recursive"), help="分块策略",
    )
    parser.add_argument("--resource-dir", default=None, help="语料目录（默认 resource/<kb-name>/）")
    asyncio.run(_main(parser.parse_args()))
