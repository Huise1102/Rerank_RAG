"""文件存储抽象（storage.py）。

用户上传的原始文档按 ``STORAGE_TYPE`` 落到本地 ``storage/`` 或 MinIO：
- local：直接写 ``storage/<key>``，``file_path`` 存相对 key 即可
- minio：上传到 bucket，解析时把对象 ``materialize`` 到本地临时文件

上层（document_service / Celery 任务）只依赖 :func:`get_storage` 返回的统一接口，
两种后端可无感切换；``file_path`` 列统一存 **key**（相对对象名），不存绝对路径。
"""

from __future__ import annotations

import shutil
import tempfile
from abc import ABC, abstractmethod
from functools import lru_cache
from pathlib import Path
from typing import BinaryIO

from src.core.config import settings
from src.core.logging import get_logger

logger = get_logger(__name__)


class BaseStorage(ABC):
    """存储后端接口。"""

    @abstractmethod
    def save_stream(self, key: str, stream: BinaryIO, length: int | None = None) -> str:
        """保存字节流，返回存储 key。"""

    @abstractmethod
    def materialize(self, key: str) -> Path:
        """把对象落地为可供解析器读取的本地文件路径。"""

    @abstractmethod
    def delete(self, key: str) -> bool:
        """删除对象，返回是否确实删除。"""

    @abstractmethod
    def exists(self, key: str) -> bool:
        """对象是否存在。"""

    def save_bytes(self, key: str, data: bytes) -> str:
        """保存字节串（内部转为流）。"""
        import io

        return self.save_stream(key, io.BytesIO(data), len(data))

    def read_bytes(self, key: str) -> bytes:
        """读取对象全部内容（供在线预览 / 下载）。"""
        return self.materialize(key).read_bytes()


class LocalStorage(BaseStorage):
    """本地文件系统后端：storage/<key>。"""

    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root or settings.paths.storage_dir)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        # key 使用相对对象名，落到 storage 根下，避免路径穿越
        rel = key.replace("\\", "/").lstrip("/")
        p = (self.root / rel).resolve()
        if self.root.resolve() not in p.parents and p != self.root.resolve():
            raise ValueError(f"非法存储 key: {key}")
        return p

    def save_stream(self, key: str, stream: BinaryIO, length: int | None = None) -> str:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as fh:
            shutil.copyfileobj(stream, fh)
        return key

    def materialize(self, key: str) -> Path:
        path = self._path(key)
        if not path.exists():
            raise FileNotFoundError(f"本地存储文件不存在: {path}")
        return path

    def delete(self, key: str) -> bool:
        path = self._path(key)
        if path.exists():
            path.unlink()
            return True
        return False

    def exists(self, key: str) -> bool:
        return self._path(key).exists()


class MinioStorage(BaseStorage):
    """MinIO 对象存储后端：上传到 bucket，解析时下载到临时文件。"""

    def __init__(self) -> None:
        from src.utils.minio_client import ensure_bucket, get_minio_client

        self.bucket = settings.minio.bucket
        self._client = get_minio_client()
        ensure_bucket()

    def save_stream(self, key: str, stream: BinaryIO, length: int | None = None) -> str:
        if length is None:
            stream.seek(0, 2)
            length = stream.tell()
            stream.seek(0)
        self._client.put_object(self.bucket, key, stream, length=length)
        return key

    def materialize(self, key: str) -> Path:
        suffix = Path(key).suffix
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        tmp.close()
        self._client.fget_object(self.bucket, key, tmp.name)
        return Path(tmp.name)

    def read_bytes(self, key: str) -> bytes:
        # 直接读对象流，避免 materialize 产生的临时文件残留
        resp = self._client.get_object(self.bucket, key)
        try:
            return resp.read()
        finally:
            resp.close()
            resp.release_conn()

    def delete(self, key: str) -> bool:
        try:
            self._client.remove_object(self.bucket, key)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("MinIO 删除失败 key=%s: %s", key, exc)
            return False

    def exists(self, key: str) -> bool:
        try:
            self._client.stat_object(self.bucket, key)
            return True
        except Exception:  # noqa: BLE001
            return False


@lru_cache(maxsize=1)
def get_storage() -> BaseStorage:
    """按配置返回存储后端单例。"""
    if settings.storage.type == "minio":
        return MinioStorage()
    return LocalStorage()


def build_key(kb_id: int, filename: str) -> str:
    """生成对象 key：``kb_<kb_id>/<uuid>_<filename>``，避免同名覆盖。"""
    import uuid

    safe = Path(filename).name.replace(" ", "_")
    return f"kb_{int(kb_id)}/{uuid.uuid4().hex[:8]}_{safe}"
