"""增量入库：上传 PDF → 解析清洗 → 分块 → 重建索引（向量 + BM25）。

增量依据 ``data/processed/ingest_manifest.json`` 的 sha256 与确定性 ``chunk_id``
（文件名+页码+块序号+正文哈希，见 ``splitter.make_chunk_id``）：新增或 sha256 变化则重解析，
文件名与 sha256 都没变则跳过，``force=True`` 忽略指纹全部重灌。重建先删集合再写入，
等价于从零建库，因此重复上传同一文件是幂等的。

安全约束：只接受 ``.pdf``（并校验 %PDF 魔数）、文件名净化禁止路径穿越、
同名但内容不同的文件拒绝覆盖（要求改名重传）。
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable

from config import settings
from src.utils.logger import get_logger

logger = get_logger(__name__)

#: 清单文件格式版本
MANIFEST_VERSION = 1
#: 上传文件大小上限（字节）。215 页的教材约 1~20 MB，给足余量但避免误传大文件
MAX_UPLOAD_BYTES = 200 * 1024 * 1024
#: PDF 魔数
_PDF_MAGIC = b"%PDF"
#: 文件名中不允许出现的字符（Windows 保留字符 + 路径分隔符）
_UNSAFE_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')



def manifest_path() -> Path:
    return settings.paths.processed_dir / "ingest_manifest.json"


def load_manifest() -> dict[str, Any]:
    """读取入库清单；损坏或不存在时返回空清单（不抛异常，避免阻断入库）。"""
    path = manifest_path()
    if not path.exists():
        return {"version": MANIFEST_VERSION, "files": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or "files" not in data:
            raise ValueError("清单结构异常")
        return data
    except Exception as exc:  # noqa: BLE001
        logger.warning("入库清单读取失败，将按全新入库处理: %s", exc)
        return {"version": MANIFEST_VERSION, "files": {}}


def save_manifest(data: dict[str, Any]) -> Path:
    """原子写入清单（先写临时文件再替换，避免中途失败留下半个文件）。"""
    path = manifest_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data["version"] = MANIFEST_VERSION
    data["updated_at"] = datetime.now().isoformat(timespec="seconds")
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    return path


def file_sha256(path: Path, chunk_size: int = 1024 * 1024) -> str:
    """计算文件 sha256（分块读取，避免大文件占内存）。"""
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            block = fh.read(chunk_size)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()



def sanitize_filename(name: str) -> str:
    """净化上传文件名，防止路径穿越与非法字符：
    ``../../etc/passwd`` -> ``passwd``；``a<b>c.pdf`` -> ``a_b_c.pdf``
    """
    # 只取最后一段，去掉目录成分（同时处理两种分隔符）
    base = Path(str(name).replace("\\", "/")).name
    base = _UNSAFE_NAME.sub("_", base).strip(" .")
    if not base:
        base = f"upload_{uuid.uuid4().hex[:8]}.pdf"
    if not base.lower().endswith(".pdf"):
        base += ".pdf"
    return base[:180]  # 防止超长文件名


def save_upload(data: bytes, filename: str) -> tuple[Path, str]:
    """
    校验并保存上传的 PDF。

    :returns: ``(保存路径, 冲突提示)``；提示非空表示未保存，需改名重传。
    :raises ValueError: 内容不合法（空文件、非 PDF、超大）。
    """
    if not data:
        raise ValueError("上传内容为空")
    if len(data) > MAX_UPLOAD_BYTES:
        raise ValueError(
            f"文件过大（{len(data) / 1024 / 1024:.1f} MB），上限 {MAX_UPLOAD_BYTES // 1024 // 1024} MB"
        )
    if not data.startswith(_PDF_MAGIC):
        raise ValueError("不是有效的 PDF 文件（缺少 %PDF 文件头），请确认文件类型")

    name = sanitize_filename(filename)
    target = settings.paths.raw_dir / name
    target.parent.mkdir(parents=True, exist_ok=True)

    new_hash = hashlib.sha256(data).hexdigest()

    # 同名文件：内容相同则视为重复上传（幂等），不同则拒绝覆盖
    if target.exists():
        existing_hash = file_sha256(target)
        if existing_hash == new_hash:
            logger.info("上传的 %s 与已有文件内容一致，按幂等处理", name)
            return target, "identical"
        return target, (
            f"已存在同名文件「{name}」但内容不同。为避免混淆，请改名后重新上传"
            f"（例如 {Path(name).stem}_v2.pdf）"
        )

    tmp = target.with_suffix(".pdf.tmp")
    tmp.write_bytes(data)
    tmp.replace(target)
    logger.info("已保存上传文件: %s（%.2f MB）", name, len(data) / 1024 / 1024)
    return target, ""


def validate_existing_files() -> list[tuple[Path, str]]:
    """检查 raw 目录里的文件是否为有效 PDF，返回可疑文件列表。"""
    bad: list[tuple[Path, str]] = []
    for path in sorted(settings.paths.raw_dir.rglob("*.pdf")):
        try:
            with path.open("rb") as fh:
                if not fh.read(4).startswith(_PDF_MAGIC):
                    bad.append((path, "缺少 %PDF 文件头"))
        except Exception as exc:  # noqa: BLE001
            bad.append((path, f"{type(exc).__name__}: {exc}"))
    return bad



@dataclass
class FilePlan:
    """单个文件的处理计划。"""

    path: Path
    action: str  # added | updated | skipped
    reason: str = ""
    sha256: str = ""
    old_chunks: int = 0


def plan_incremental(
    files: Iterable[Path],
    manifest: dict[str, Any],
    *,
    force: bool = False,
) -> list[FilePlan]:
    """
    对比磁盘文件与清单，得出每个文件的处理动作。
    :param force: True 时忽略指纹，全部按"更新"处理。
    """
    known: dict[str, Any] = manifest.get("files", {}) or {}
    plans: list[FilePlan] = []

    for path in files:
        name = path.name
        try:
            current_hash = file_sha256(path)
        except Exception as exc:  # noqa: BLE001
            plans.append(FilePlan(path, "updated", reason=f"无法计算哈希: {exc}"))
            continue

        record = known.get(name)
        if force:
            plans.append(FilePlan(path, "updated", reason="force=true 强制重灌",
                                  sha256=current_hash,
                                  old_chunks=int(record.get("chunks", 0)) if record else 0))
        elif record is None:
            plans.append(FilePlan(path, "added", reason="首次入库", sha256=current_hash))
        elif record.get("sha256") != current_hash:
            plans.append(FilePlan(
                path, "updated", reason="文件内容已变化，重新解析",
                sha256=current_hash, old_chunks=int(record.get("chunks", 0)),
            ))
        else:
            plans.append(FilePlan(
                path, "skipped", reason="内容未变化，跳过",
                sha256=current_hash, old_chunks=int(record.get("chunks", 0)),
            ))
    return plans



@dataclass
class IngestTask:
    """一次入库任务的状态。"""

    task_id: str
    status: str = "pending"  # pending | running | done | failed
    stage: str = ""
    progress: float = 0.0
    files: list[dict[str, Any]] = field(default_factory=list)
    total_chunks: int = 0
    started_at: str = ""
    finished_at: str = ""
    seconds: float = 0.0
    error: str = ""
    _t0: float = field(default_factory=time.perf_counter)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "status": self.status,
            "stage": self.stage,
            "progress": round(self.progress, 4),
            "files": list(self.files),
            "total_chunks": self.total_chunks,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "seconds": round(self.seconds, 2),
            "error": self.error,
        }


class IngestManager:
    """入库任务管理器（进程内）。单进程单 worker 够用；水平扩展需换外部队列。"""

    def __init__(self, *, keep_tasks: int = 20) -> None:
        self._tasks: dict[str, IngestTask] = {}
        self._lock = threading.RLock()
        self._keep = keep_tasks
        self._busy = False
    def get(self, task_id: str) -> IngestTask | None:
        with self._lock:
            return self._tasks.get(task_id)

    def list_recent(self, limit: int = 5) -> list[dict[str, Any]]:
        with self._lock:
            tasks = sorted(self._tasks.values(), key=lambda t: t._t0, reverse=True)
            return [t.to_dict() for t in tasks[:limit]]

    @property
    def busy(self) -> bool:
        with self._lock:
            return self._busy
    def submit(
        self,
        files: list[Path],
        *,
        strategy: str | None = None,
        force: bool = False,
    ) -> IngestTask:
        """
        提交入库任务并在后台线程执行，立即返回任务对象。

        :raises RuntimeError: 已有任务在跑（避免两个入库同时改索引导致不一致）
        """
        with self._lock:
            if self._busy:
                raise RuntimeError("已有入库任务正在执行，请等待其完成后再提交")
            task = IngestTask(
                task_id=uuid.uuid4().hex[:12],
                started_at=datetime.now().isoformat(timespec="seconds"),
            )
            self._tasks[task.task_id] = task
            self._busy = True
            self._trim()

        thread = threading.Thread(
            target=self._run,
            args=(task, list(files), strategy or settings.chunk.default_strategy, force),
            name=f"ingest-{task.task_id}",
            daemon=True,
        )
        thread.start()
        return task

    def _trim(self) -> None:
        """只保留最近 N 个任务，避免长时间运行后内存里堆积状态。"""
        if len(self._tasks) <= self._keep:
            return
        for task in sorted(self._tasks.values(), key=lambda t: t._t0)[: len(self._tasks) - self._keep]:
            self._tasks.pop(task.task_id, None)
    def _run(self, task: IngestTask, files: list[Path], strategy: str, force: bool) -> None:
        from src.ingest import indexer, loader, splitter

        try:
            task.status = "running"
            task.stage = "扫描文件与清单"
            task.progress = 0.02
            t_start = time.perf_counter()

            manifest = load_manifest()
            plans = plan_incremental(files, manifest, force=force)
            todo = [p for p in plans if p.action != "skipped"]
            skipped = [p for p in plans if p.action == "skipped"]

            # 兜底自愈：清单记录的总块数应与分块文件一致。若不一致（例如上次入库
            # 在写清单前中断），必须重新解析全部文件，否则索引缺块而清单却声称已入库。
            existing_chunks = 0
            cf = splitter.chunks_file(strategy)
            if cf.exists():
                try:
                    existing_chunks = splitter.load_chunks(strategy).total
                except Exception:  # noqa: BLE001
                    existing_chunks = 0
            manifest_chunks = sum(int(r.get("chunks", 0)) for r in manifest.get("files", {}).values())
            if manifest_chunks and existing_chunks != manifest_chunks:
                logger.warning(
                    "分块文件(%d 块)与清单(%d 块)不一致，将重新解析全部文件以自愈",
                    existing_chunks, manifest_chunks,
                )
                todo = list(plans)
                skipped = []

            logger.info(
                "入库任务 %s: 共 %d 个文件，需处理 %d 个，跳过 %d 个",
                task.task_id, len(plans), len(todo), len(skipped),
            )

            for plan in skipped:
                task.files.append({
                    "file_name": plan.path.name, "action": "skipped",
                    "pages": 0, "chunks": plan.old_chunks, "chars": 0,
                    "reason": plan.reason, "seconds": 0.0,
                })

            # 只处理变化的文件，结果累积后一次性写入规范分块文件。
            new_manifest = {"version": MANIFEST_VERSION, "files": dict(manifest.get("files", {}))}
            parsed_chunks: list[Any] = []
            parsed_params: dict[str, Any] = {}
            parsed_strategy = strategy
            total_todo = max(len(todo), 1)

            for idx, plan in enumerate(todo, 1):
                task.stage = f"解析并分块：{plan.path.name}（{idx}/{len(todo)}）"
                task.progress = 0.02 + 0.48 * (idx - 1) / total_todo
                t_file = time.perf_counter()
                try:
                    # 只解析当前这一个文件；load_pdfs(目录) 会把全目录解析 N 遍
                    pages, _headers = loader.load_pdf(plan.path)
                    if not pages:
                        raise ValueError("清洗后没有可用文本（可能是扫描件，需 OCR）")

                    result = splitter.split_documents(
                        loader.to_documents(pages), strategy=strategy
                    )
                    if result.total == 0:
                        raise ValueError("该文件未产生任何文档块")

                    chars = sum(len(p.text) for p in pages)
                    parsed_chunks.extend(result.chunks)
                    parsed_params = result.params
                    parsed_strategy = result.strategy
                    new_manifest["files"][plan.path.name] = {
                        "sha256": plan.sha256,
                        "size": plan.path.stat().st_size,
                        "pages": len(pages),
                        "chunks": result.total,
                        "chars": chars,
                        "indexed_at": datetime.now().isoformat(timespec="seconds"),
                    }
                    task.files.append({
                        "file_name": plan.path.name, "action": plan.action,
                        "pages": len(pages), "chunks": result.total,
                        "chars": chars, "reason": plan.reason,
                        "seconds": round(time.perf_counter() - t_file, 2),
                    })
                    logger.info(
                        "已处理 %s: %d 页 -> %d 块（%.1fs）",
                        plan.path.name, len(pages), result.total, time.perf_counter() - t_file,
                    )
                except Exception as exc:  # noqa: BLE001 - 单文件失败不影响其它文件
                    logger.warning("处理 %s 失败: %s", plan.path.name, exc)
                    task.files.append({
                        "file_name": plan.path.name, "action": "failed",
                        "pages": 0, "chunks": 0, "chars": 0,
                        "reason": f"{type(exc).__name__}: {exc}",
                        "seconds": round(time.perf_counter() - t_file, 2),
                    })

            # 必须在这里写分块文件：build_index() 默认只读取 chunks_<strategy>.json 而不写入，
            # 否则索引会基于旧文件重建，表现为"清单记录 295 块、索引只有 290 块"。
            if parsed_chunks:
                combined = splitter.ChunkResult(
                    chunks=parsed_chunks, strategy=parsed_strategy, params=parsed_params
                )
                # 分块文件的记录范围是所有已入库文件，因此用清单的键而非本次解析的文件名
                splitter.save_chunks(
                    combined, strategy=strategy,
                    source_files=sorted(new_manifest["files"].keys()),
                )
                logger.info("已写入分块文件: 共 %d 块", combined.total)
            elif todo:
                logger.warning("本次没有任何文件成功解析，索引将保持原状")
            else:
                logger.info("所有文件内容未变，直接复用现有分块文件重建索引")

            task.stage = "重建向量索引（CPU 上较慢，需数分钟）"
            task.progress = 0.55
            embedder = None
            try:
                embedder, device = indexer.load_embedder()
                task.stage = f"重建向量索引（device={device}）"
            except Exception as exc:  # noqa: BLE001
                logger.warning("Embedding 模型加载失败: %s", exc)

            task.progress = 0.75
            report = indexer.build_index(
                strategy, rebuild=True, embedder=embedder, show_progress=False,
            )

            task.progress = 0.95
            task.stage = "校验索引与清单一致性"

            # 一致性校验：清单块数之和必须等于索引实际条数，否则说明有文件未进索引，
            # 必须显式报错而非静默通过（表现为"入库报告成功、但新文件检索不到"）。
            expected = sum(int(rec.get("chunks", 0)) for rec in new_manifest["files"].values())
            actual = report.chunks
            if expected != actual:
                raise RuntimeError(
                    f"入库结果不一致：清单记录 {expected} 块，索引实际 {actual} 块。"
                    f"可能有文件内容未写入索引。请检查 data/processed/chunks_{strategy}.json "
                    f"与 indexes/ 是否匹配（可分块文件中 source_files 与清单对比）。"
                )
            logger.info("一致性校验通过：清单 %d 块 == 索引 %d 块", expected, actual)

            task.stage = "写入清单"
            save_manifest(new_manifest)

            task.total_chunks = report.chunks
            task.status = "done"
            task.stage = "完成"
            task.progress = 1.0
            task.seconds = time.perf_counter() - t_start
            task.finished_at = datetime.now().isoformat(timespec="seconds")
            logger.info(
                "入库任务 %s 完成: %d 块，耗时 %.1fs", task.task_id, task.total_chunks, task.seconds
            )

        except Exception as exc:  # noqa: BLE001
            task.status = "failed"
            task.error = f"{type(exc).__name__}: {exc}"
            task.seconds = time.perf_counter() - task._t0
            task.finished_at = datetime.now().isoformat(timespec="seconds")
            logger.exception("入库任务 %s 失败", task.task_id)
        finally:
            with self._lock:
                self._busy = False


_MANAGER: IngestManager | None = None
_MANAGER_LOCK = threading.Lock()


def get_ingest_manager() -> IngestManager:
    """获取全局入库任务管理器单例。"""
    global _MANAGER
    if _MANAGER is None:
        with _MANAGER_LOCK:
            if _MANAGER is None:
                _MANAGER = IngestManager()
    return _MANAGER
