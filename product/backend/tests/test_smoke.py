"""冒烟测试：不依赖 MySQL / Redis / 模型文件。

覆盖：应用可导入、路由已注册、配置端点返回预期的链路参数、
关键纯函数（策略归一化 / 文件类型判定 / 语料发现与去重）行为正确。
"""

from __future__ import annotations

import pytest

from src.services.kb_service import normalize_strategies
from src.services.document_service import detect_file_type
from src.ingest.loader import supported_extensions


def test_supported_extensions():
    assert set(supported_extensions()) == {"pdf", "docx", "txt", "md"}


def test_detect_file_type():
    assert detect_file_type("a.PDF") == "pdf"
    assert detect_file_type("b.docx") == "docx"
    assert detect_file_type("c.txt") == "txt"
    assert detect_file_type("d.md") == "md"
    assert detect_file_type("e.exe") == ""


def test_normalize_strategies_defaults_and_partial_override():
    assert normalize_strategies(None) == {"vector": True, "bm25": True, "fusion": True, "rerank": True}
    out = normalize_strategies({"vector": False, "unknown": True})
    assert out["vector"] is False
    assert out["bm25"] is True
    assert "unknown" not in out


def test_discover_files_dedups_same_stem(tmp_path):
    from scripts.seed_data import discover_files

    (tmp_path / "商标法.pdf").write_bytes(b"%PDF-1.4")
    (tmp_path / "商标法.txt").write_text("dup", encoding="utf-8")
    (tmp_path / "广告法.txt").write_text("a", encoding="utf-8")
    files = discover_files(tmp_path)
    names = {p.name for p in files}
    # 同 stem 只保留优先级更高的 pdf
    assert "商标法.pdf" in names
    assert "商标法.txt" not in names
    assert "广告法.txt" in names


def test_app_routes_and_openapi():
    from src.api.main import app

    paths = app.openapi()["paths"]
    for expected in (
        "/api/v1/kb",
        "/api/v1/kb/{kb_id}",
        "/api/v1/kb/{kb_id}/strategies",
        "/api/v1/kb/{kb_id}/documents",
        "/api/v1/documents/{doc_id}",
        "/api/v1/kb/{kb_id}/retrieve",
        "/api/v1/chat",
        "/api/v1/chat/stream",
        "/api/v1/conversations",
        "/api/v1/logs/retrieval",
        "/api/v1/logs/system",
    ):
        assert expected in paths, f"缺少路由: {expected}"


@pytest.fixture()
def client():
    # 关闭启动预热，避免测试触发模型加载
    from src.core.config import settings

    settings.api.warmup_on_startup = False
    from fastapi.testclient import TestClient

    from src.api.main import app

    return TestClient(app)


def test_root_endpoint(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.json()["name"]


def test_config_endpoint_reflects_chain_params(client):
    resp = client.get("/config")
    assert resp.status_code == 200
    cfg = resp.json()
    # 链路参数与 demo 一致，不得漂移
    assert cfg["retrieval"]["dense_top_k"] == 50
    assert cfg["retrieval"]["sparse_top_k"] == 50
    assert cfg["retrieval"]["top_k"] == 20
    assert cfg["retrieval"]["rrf_k"] == 60
    assert cfg["rerank"]["top_k"] == 5
    assert cfg["generation"]["context_max_chars"] == 6000
    # 密钥脱敏
    assert cfg["database"]["url"] == "***"
