import json
import sys
from pathlib import Path

import httpx

BASE_URL = "http://127.0.0.1:8004"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
RESOURCE_DIR = PROJECT_ROOT / "resource" / "示例"

ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = sys.argv[1] if len(sys.argv) > 1 else ""
USER_USERNAME = "student01"
USER_PASSWORD = "Student2026"


def login(client: httpx.Client, username: str, password: str) -> str:
    response = client.post(f"{BASE_URL}/api/v1/auth/login", json={"username": username, "password": password})
    response.raise_for_status()
    return response.json()["access_token"]


def auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def ensure_documents(client: httpx.Client, token: str, kb_id: int) -> None:
    existing = client.get(f"{BASE_URL}/api/v1/kb/{kb_id}/documents", headers=auth_headers(token))
    existing.raise_for_status()
    names = {item["filename"] for item in existing.json()}
    for path in sorted(RESOURCE_DIR.glob("*.md")):
        if path.name in names:
            continue
        with open(path, "rb") as handle:
            response = client.post(
                f"{BASE_URL}/api/v1/kb/{kb_id}/documents",
                headers=auth_headers(token),
                files={"file": (path.name, handle, "text/markdown")},
                timeout=120,
            )
        response.raise_for_status()
        document_id = response.json()["id"]
        print(f"已上传 {path.name}，文档 ID {document_id}")
        ingest = client.post(
            f"{BASE_URL}/api/v1/kb/{kb_id}/documents/{document_id}/ingest",
            headers=auth_headers(token),
            timeout=900,
        )
        ingest.raise_for_status()
        print(f"入库完成 {path.name}，分块数 {ingest.json()['chunk_count']}")


def ask(
    client: httpx.Client,
    token: str,
    kb_id: int,
    strategy_id: int,
    question: str,
    app_key: str = "admin",
) -> dict:
    payload = {
        "question": question,
        "kb_id": kb_id,
        "strategy_id": strategy_id,
        "app_key": app_key,
    }
    answer = ""
    citations: list = []
    message_id = None
    with client.stream(
        "POST",
        f"{BASE_URL}/api/v1/chat/stream",
        headers={**auth_headers(token), "Content-Type": "application/json"},
        json=payload,
        timeout=600,
    ) as response:
        response.raise_for_status()
        for line in response.iter_lines():
            if not line.startswith("data:"):
                continue
            event = json.loads(line[5:].strip())
            if event["event"] == "delta":
                answer += event["content"]
            elif event["event"] == "citations":
                citations = event["citations"]
            elif event["event"] == "done":
                message_id = event["message_id"]
            elif event["event"] == "error":
                print(f"生成错误：{event['detail']}")
    return {"answer": answer, "citations": citations, "message_id": message_id}


def main() -> int:
    if not ADMIN_PASSWORD:
        print("用法：python scripts/smoke_test.py 管理员初始密码")
        return 2
    with httpx.Client(timeout=30) as client:
        health = client.get(f"{BASE_URL}/health")
        health.raise_for_status()
        print(f"健康检查：{health.json()}")

        admin_token = login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
        print("管理员登录成功")

        kb_response = client.get(f"{BASE_URL}/api/v1/kb", headers=auth_headers(admin_token))
        kb_response.raise_for_status()
        kbs = kb_response.json()
        if not kbs:
            print("没有可用知识库")
            return 1
        kb_id = kbs[0]["id"]

        strategies = client.get(f"{BASE_URL}/api/v1/strategies", headers=auth_headers(admin_token)).json()
        strategy_id = next((item["id"] for item in strategies if item["is_default"]), strategies[0]["id"])

        ensure_documents(client, admin_token, kb_id)

        result = ask(client, admin_token, kb_id, strategy_id, "请假三天需要谁审批？")
        print(f"回答：{result['answer'][:200]}")
        print(f"引用数量：{len(result['citations'])}，消息 ID：{result['message_id']}")
        if not result["citations"]:
            print("未返回引用")
            return 1

        user_token = login(client, USER_USERNAME, USER_PASSWORD)
        user_result = ask(
            client,
            user_token,
            kb_id,
            strategy_id,
            "奖学金一等奖金额是多少？",
            app_key="user",
        )
        print(f"用户端回答：{user_result['answer'][:160]}")
        if not user_result["message_id"]:
            print("用户端未返回消息 ID")
            return 1

        feedback = client.post(
            f"{BASE_URL}/api/v1/feedback",
            headers=auth_headers(user_token),
            json={
                "assistant_message_id": user_result["message_id"],
                "feedback_type": "回答不完整",
                "description": "示例反馈：希望补充奖学金申请的截止日期说明。",
                "contact": "student01@example.com",
            },
        )
        if feedback.status_code == 409:
            print("该消息已反馈，跳过重复提交")
        else:
            feedback.raise_for_status()
            print(f"反馈提交成功，工单号 {feedback.json()['ticket_no']}")

        stats = client.get(f"{BASE_URL}/api/v1/admin/feedback/stats", headers=auth_headers(admin_token))
        stats.raise_for_status()
        print(f"反馈统计：{stats.json()}")

    print("冒烟测试完成")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
