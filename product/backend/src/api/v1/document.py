"""文档管理路由（document.py）。

上传成功后触发 Celery 任务 ``parse_and_index_document.delay(doc_id)``；
若 Redis（broker）不可用，则退化为在线程池内同步入库，保证无异步基础设施时仍可用。
"""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.logging import get_logger
from src.db.session import get_db
from src.schemas.document import DocumentOut
from src.services import document_service

logger = get_logger(__name__)

router = APIRouter(tags=["文档"])

#: 预览/下载时使用的 Content-Type（docx 浏览器不能内联渲染，交给本地软件打开）
_MEDIA_TYPES = {
    "pdf": "application/pdf",
    "txt": "text/plain; charset=utf-8",
    "md": "text/markdown; charset=utf-8",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


async def _trigger_ingest(doc_id: int) -> None:
    """触发异步入库；broker 不可用时同步兜底。"""
    from src.tasks.document_tasks import parse_and_index_document
    from src.utils.redis_client import ping

    if ping():
        try:
            parse_and_index_document.delay(doc_id)
            logger.info("已投递 Celery 入库任务 doc=%s", doc_id)
            return
        except Exception as exc:  # noqa: BLE001
            logger.warning("投递 Celery 任务失败，降级同步入库: %s", exc)

    logger.info("Redis 不可用，在线程池内同步入库 doc=%s", doc_id)
    await run_in_threadpool(parse_and_index_document, doc_id)


@router.post(
    "/kb/{kb_id}/documents",
    response_model=DocumentOut,
    status_code=status.HTTP_201_CREATED,
    summary="上传文档（pdf/docx/txt/md）",
)
async def upload_document(
    kb_id: int,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="上传文件为空")
    try:
        doc = await document_service.upload_document(
            db, kb_id, filename=file.filename or "unnamed", content=content
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    await _trigger_ingest(doc.id)
    return doc


@router.get("/kb/{kb_id}/documents", response_model=list[DocumentOut], summary="知识库文档列表")
async def list_documents(kb_id: int, db: AsyncSession = Depends(get_db)):
    return await document_service.list_documents(db, kb_id)


@router.get("/documents/{doc_id}", response_model=DocumentOut, summary="文档详情")
async def get_document(doc_id: int, db: AsyncSession = Depends(get_db)):
    doc = await document_service.get_document(db, doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"文档 {doc_id} 不存在")
    return doc


@router.get("/documents/{doc_id}/file", summary="查看/下载原始文档")
async def get_document_file(
    doc_id: int,
    download: bool = Query(default=False, description="true 时作为附件下载，否则内联预览"),
    db: AsyncSession = Depends(get_db),
):
    doc = await document_service.get_document(db, doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"文档 {doc_id} 不存在")
    try:
        content = await run_in_threadpool(document_service.read_document_bytes, doc)
    except Exception as exc:  # noqa: BLE001
        logger.warning("读取原始文件失败 doc=%s key=%s: %s", doc_id, doc.file_path, exc)
        raise HTTPException(status_code=404, detail="原始文件不存在或已丢失") from exc

    # 中文文件名须按 RFC 5987 编码，否则部分浏览器会截断或乱码
    disposition = "attachment" if download else "inline"
    headers = {
        "Content-Disposition": f"{disposition}; filename*=UTF-8''{quote(doc.filename)}",
        "Content-Length": str(len(content)),
    }
    return Response(
        content=content,
        media_type=_MEDIA_TYPES.get(doc.file_type, "application/octet-stream"),
        headers=headers,
    )


@router.delete("/documents/{doc_id}", summary="删除文档")
async def delete_document(doc_id: int, db: AsyncSession = Depends(get_db)) -> dict:
    ok = await document_service.delete_document(db, doc_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"文档 {doc_id} 不存在")
    return {"deleted": True, "doc_id": doc_id}
