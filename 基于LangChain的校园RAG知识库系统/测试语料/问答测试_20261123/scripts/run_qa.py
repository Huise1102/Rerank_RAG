"""批量问答评测：按题库逐题调用问答链路，输出 jsonl 结果。

用法（在项目根目录执行）：
    backend\\.venv\\Scripts\\python.exe 测试语料\\问答测试_20261123\\scripts\\run_qa.py results_api.jsonl
    backend\\.venv\\Scripts\\python.exe 测试语料\\问答测试_20261123\\scripts\\run_qa.py results_api.jsonl kb4-hop-001 kb4-hop-002

第 1 个参数为输出文件名（写入同目录），其余参数为要跑的题号；不传题号则跑全部。
模型与知识库取自数据库 system_settings.model_config，需先在管理端「模型配置」切换。
"""
import asyncio
import json
import os
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
TEST_DIR = SCRIPT_DIR.parent
BASE = TEST_DIR.parents[1]
BACKEND = BASE / "backend"
os.chdir(BACKEND)
sys.path.insert(0, str(BACKEND))

from sqlalchemy import select  # noqa: E402

from src.db.models import User  # noqa: E402
from src.db.session import async_session_factory  # noqa: E402
from src.schemas.chat import ChatRequest  # noqa: E402
from src.services import chat_service  # noqa: E402
from src.services.chat_service import prepare_chat, stream_answer  # noqa: E402
from src.services.setting_service import get_model_config  # noqa: E402

ADMIN_USER_ID = 1
STRATEGY_ID = 1


async def _no_cache(*args, **kwargs):
    """评测要求逐题真实检索，关闭精确与语义缓存。"""
    return None


chat_service.lookup_cached_answer = _no_cache


def load_questions() -> dict[str, dict]:
    items: dict[str, dict] = {}
    for name in ("qa_kb3.jsonl", "qa_kb4.jsonl"):
        for line in (TEST_DIR / name).read_text(encoding="utf-8").splitlines():
            if line.strip():
                item = json.loads(line)
                items[item["id"]] = item
    return items


def parse_sse(raw: str) -> dict | None:
    if not raw.startswith("data: "):
        return None
    try:
        return json.loads(raw[6:])
    except json.JSONDecodeError:
        return None


async def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("缺少输出文件名")
    out = TEST_DIR / sys.argv[1]
    questions = load_questions()
    ids = sys.argv[2:] or list(questions)
    out.write_text("", encoding="utf-8")
    async with async_session_factory() as db:
        user = (await db.execute(select(User).where(User.id == ADMIN_USER_ID))).scalar_one()
        config = await get_model_config(db)
        model = config["api_model"] if config["provider"] != "ollama" else config["ollama_model"]
        label = f"{config['provider']}-{model}"
        print("RUN", label, "questions", len(ids), flush=True)
        for index, qid in enumerate(ids, start=1):
            item = questions[qid]
            payload = ChatRequest(
                question=item["question"], kb_id=item["kb_id"], strategy_id=STRATEGY_ID, mode="fast"
            )
            record = {
                "id": qid,
                "kb_id": item["kb_id"],
                "category": item["category"],
                "question": item["question"],
                "model": label,
            }
            started = time.perf_counter()
            try:
                context = await prepare_chat(db, user, payload)
                record["retrieval_s"] = round(time.perf_counter() - started, 2)
                record["citations"] = len(context.citations)
                parts: list[str] = []
                first_token = None
                async for raw in stream_answer(context, user):
                    event = parse_sse(raw)
                    if not event:
                        continue
                    if event.get("event") == "delta":
                        if first_token is None:
                            first_token = round(time.perf_counter() - started, 2)
                        parts.append(str(event.get("content") or ""))
                    if event.get("event") == "error":
                        record["error"] = event.get("detail")
                record["answer"] = "".join(parts).strip()
                record["first_token_s"] = first_token
                record["total_s"] = round(time.perf_counter() - started, 2)
            except Exception as exc:  # noqa: BLE001
                record["error"] = f"{type(exc).__name__}: {exc}"
                record["total_s"] = round(time.perf_counter() - started, 2)
                record["answer"] = ""
            with open(out, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            print(
                f"[{index}/{len(ids)}] {qid} total={record.get('total_s')} "
                f"len={len(record.get('answer') or '')} err={record.get('error') or '-'}",
                flush=True,
            )
    print("DONE", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
