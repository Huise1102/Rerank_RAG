from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import client_ip, require_admin, require_document_manager
from src.db.models import AuditLog, EvaluationReport, StrategyPreset, User, utcnow
from src.db.session import get_db
from src.evaluation.generation_eval import evaluate_generation
from src.evaluation.retrieval_eval import evaluate_retrieval
from src.evaluation.testset import load_testset, validate_testset
from src.schemas.evaluation import (
    EvaluationReportOut,
    EvaluationRunRequest,
    EvaluationValidateRequest,
)
from src.services.setting_service import get_model_config

router = APIRouter(prefix="/admin/evaluation", tags=["评估"])
BACKEND_ROOT = Path(__file__).resolve().parents[3]
ALLOWED_DIRS = [BACKEND_ROOT / "tests", BACKEND_ROOT / "evaluation_data"]


def _resolve_testset(path_text: str) -> Path:
    candidate = Path(path_text)
    if not candidate.is_absolute():
        candidate = (BACKEND_ROOT / candidate).resolve()
    else:
        candidate = candidate.resolve()
    if not any(candidate.is_relative_to(directory.resolve()) for directory in ALLOWED_DIRS):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="测试集路径不在允许目录内")
    if not candidate.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="测试集不存在")
    return candidate


@router.get("/reports", response_model=list[EvaluationReportOut])
async def list_reports(
    user: User = Depends(require_document_manager),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(EvaluationReport).order_by(EvaluationReport.id.desc()).limit(50))
    return result.scalars().all()


@router.get("/reports/{report_id}")
async def report_detail(
    report_id: int,
    user: User = Depends(require_document_manager),
    db: AsyncSession = Depends(get_db),
):
    report = await db.get(EvaluationReport, report_id)
    if report is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="报告不存在")
    return {
        "id": report.id,
        "name": report.name,
        "kind": report.kind,
        "status": report.status,
        "summary": report.summary,
        "report": report.report,
        "markdown": report.markdown,
        "testset": report.testset,
        "created_at": report.created_at,
        "finished_at": report.finished_at,
    }


@router.post("/validate")
async def validate(
    payload: EvaluationValidateRequest,
    user: User = Depends(require_document_manager),
):
    path = _resolve_testset(payload.testset_path)
    return validate_testset(load_testset(path))


@router.post("/run")
async def run_evaluation(
    payload: EvaluationRunRequest,
    request: Request,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    path = _resolve_testset(payload.testset_path)
    items = load_testset(path)
    validation = validate_testset(items)
    if not validation["valid"]:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"message": "测试集校验失败", "errors": validation["errors"][:20]},
        )
    preset = await db.get(StrategyPreset, payload.strategy_id) if payload.strategy_id else None
    if payload.kind == "retrieval":
        result = await evaluate_retrieval(
            items,
            payload.kb_id,
            preset,
            top_k=payload.top_k,
            limit=payload.limit,
            role=payload.role,
            department_id=payload.department_id,
        )
    else:
        model_config = await get_model_config(db)
        result = await evaluate_generation(
            items,
            payload.kb_id,
            preset,
            model_config,
            limit=payload.limit,
            role=payload.role,
            department_id=payload.department_id,
        )

    report = EvaluationReport(
        name=f"{path.stem}-{payload.kind}",
        kind=payload.kind,
        status="completed",
        testset=str(path),
        summary=result["summary"],
        report={"summary": result["summary"], "rows": result["rows"]},
        markdown=result["markdown"],
        created_by=admin.id,
        finished_at=utcnow(),
    )
    db.add(report)
    await db.flush()
    db.add(
        AuditLog(
            user_id=admin.id,
            username=admin.username,
            action="run_evaluation",
            target_type="evaluation",
            target_id=str(report.id),
            detail=f"{payload.kind} 评估，样本 {result['summary']['total']} 条",
            ip=client_ip(request),
        )
    )
    await db.commit()
    await db.refresh(report)
    return {
        "id": report.id,
        "summary": result["summary"],
        "markdown": result["markdown"],
        "validation": validation,
        "created_at": report.created_at,
    }
