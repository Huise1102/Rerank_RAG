import argparse
import asyncio
import json
from pathlib import Path

from sqlalchemy import select

from src.db.models import StrategyPreset
from src.db.session import async_session_factory, engine
from src.evaluation.generation_eval import evaluate_generation
from src.evaluation.retrieval_eval import evaluate_retrieval
from src.evaluation.testset import load_testset, validate_testset
from src.services.setting_service import get_model_config


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="校园知识库评估工具")
    parser.add_argument("command", choices=["validate", "retrieval", "generation"])
    parser.add_argument("--testset", required=True, help="JSONL 测试集路径")
    parser.add_argument("--kb-id", type=int, default=None, help="知识库 ID")
    parser.add_argument("--strategy-id", type=int, default=None, help="检索策略 ID")
    parser.add_argument("--limit", type=int, default=None, help="限制评估样本数")
    parser.add_argument("--top-k", type=int, default=10, help="检索评估截断位置")
    parser.add_argument("--role", default="admin", choices=["admin", "dept_admin", "staff", "student"])
    parser.add_argument("--department-id", type=int, default=None)
    parser.add_argument("--output", default=None, help="报告输出路径，支持 .md 或 .json")
    return parser


async def _run(args) -> None:
    items = load_testset(args.testset)
    if args.command == "validate":
        report = validate_testset(items)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return

    if args.kb_id is None:
        raise SystemExit("检索与生成评估必须指定 --kb-id")

    async with async_session_factory() as db:
        preset = await db.get(StrategyPreset, args.strategy_id) if args.strategy_id else None
        if args.command == "retrieval":
            result = await evaluate_retrieval(
                items,
                args.kb_id,
                preset,
                top_k=args.top_k,
                limit=args.limit,
                role=args.role,
                department_id=args.department_id,
            )
        else:
            config = await get_model_config(db)
            result = await evaluate_generation(
                items,
                args.kb_id,
                preset,
                config,
                limit=args.limit,
                role=args.role,
                department_id=args.department_id,
            )
    await engine.dispose()

    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
    if args.output:
        target = Path(args.output)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.suffix.lower() == ".json":
            target.write_text(
                json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        else:
            target.write_text(result["markdown"], encoding="utf-8")
        print(f"报告已写入 {target}")


def main() -> None:
    args = _parser().parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
