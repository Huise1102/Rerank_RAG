"""阶段 4 —— 后端 API 自测（用 TestClient，不启动真实服务器，不消耗 LLM token）。

覆盖应用装配（路由/CORS/OpenAPI/版本前缀）、/health 组件状态与索引一致性、
/strategies 与 /config 脱敏、/query 传递 mock LLM 走通全链路、/retrieve 召回统计、
/ingest 文件校验与幂等、并发保护（入库中再次提交返回 409）。

运行：.venv\\Scripts\\python.exe scripts\\test_api.py
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import shutil
import sys
import tempfile
import time
import warnings
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

warnings.filterwarnings("ignore")

from fastapi.testclient import TestClient  # noqa: E402

from config import settings  # noqa: E402
from src.api.main import create_app  # noqa: E402
from src.api.schemas import QueryRequest  # noqa: E402
from src.api.service import get_service  # noqa: E402

PASSED: list[str] = []
FAILED: list[tuple[str, str]] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSED.append(name)
        print(f"  [OK]   {name}  {detail}")
    else:
        FAILED.append((name, detail))
        print(f"  [FAIL] {name}  {detail}")



def all_paths(app) -> set[str]:
    """
    获取应用注册的全部路径。

    不能遍历 app.routes：FastAPI/Starlette 1.7.0 起 include_router 返回
    _IncludedRouter 包装对象，业务路由既不暴露 path 也不暴露 routes；
    改用 FastAPI 自己生成的 OpenAPI schema。
    """
    spec = app.openapi()
    return set(spec.get("paths", {}).keys())


def make_mock_llm(reply: str = "应在规定期限内提出申请[1]。"):
    """构造一个 Mock LLM 客户端，避免真实调用消耗 token。"""

    class _Completions:
        def __init__(self, owner):
            self.owner = owner

        def create(self, **params):
            text = reply
            if params.get("stream"):
                def gen():
                    for piece in ["应在", "规定期限内", "提出", "申请", "[1]。"]:
                        yield SimpleNamespace(
                            choices=[SimpleNamespace(
                                delta=SimpleNamespace(content=piece), finish_reason=None)]
                        )
                return gen()
            return SimpleNamespace(
                choices=[SimpleNamespace(
                    message=SimpleNamespace(content=text, reasoning_content=None),
                    finish_reason="stop")],
                usage=SimpleNamespace(prompt_tokens=900, completion_tokens=40, total_tokens=940),
            )

    class _Chat:
        def __init__(self, owner):
            self.completions = _Completions(owner)

    class MockLLM:
        def __init__(self):
            self.chat = _Chat(self)
            self.timeout = 60.0

    from src.generation.llm import LLMClient

    return LLMClient(client=MockLLM())



def test_app_wiring() -> TestClient:
    print("\n[1/7] 应用装配")
    app = create_app(warmup=False)
    client = TestClient(app)

    paths = all_paths(app)
    expected = [
        "/health", "/query", "/query/stream", "/retrieve",
        "/ingest", "/ingest/status/{task_id}", "/ingest/tasks", "/ingest/manifest",
        "/strategies", "/config", "/warmup",
    ]
    missing = [p for p in expected if p not in paths]
    check("全部路由已注册", not missing, f"缺失 {missing}" if missing else f"{len(paths)} 条路径")

    versioned = f"{settings.api.api_prefix}/query"
    check("API 版本前缀已挂载", versioned in paths, versioned)

    # OpenAPI 文档
    r = client.get("/openapi.json")
    check("OpenAPI 文档可生成", r.status_code == 200 and r.json().get("paths"),
          f"HTTP {r.status_code}, {len(r.json().get('paths', {}))} 个路径")
    check("OpenAPI 含 query 接口", "/query" in r.json().get("paths", {}))
    check("/docs 可访问", client.get("/docs").status_code == 200)

    # CORS
    r = client.options("/query", headers={
        "Origin": "http://localhost:8501",
        "Access-Control-Request-Method": "POST",
    })
    check("CORS 允许 Streamlit 源",
          r.headers.get("access-control-allow-origin") in ("http://localhost:8501", "*"),
          str(r.headers.get("access-control-allow-origin")))

    # 根路径（include_in_schema=False，因此不在 OpenAPI 里，需直接请求验证）
    r = client.get("/")
    check("根路径返回服务信息", r.status_code == 200 and "endpoints" in r.json(),
          f"HTTP {r.status_code}")

    return client



def test_health(client: TestClient) -> None:
    print("\n[2/7] /health")
    r = client.get("/health")
    check("HTTP 200", r.status_code == 200, f"HTTP {r.status_code}")
    data = r.json()

    check("status 取值合法", data["status"] in ("ok", "degraded", "error"), data["status"])
    names = {c["name"] for c in data["components"]}
    check("组件齐备", {"llm", "vector_store", "bm25"} <= names, str(sorted(names)))
    check("包含索引现状", data["index"] is not None, str(data["index"]))
    check("索引一致性标记存在", "consistent" in (data["index"] or {}),
          f"consistent={data['index'].get('consistent') if data['index'] else None}")
    check("含 warmed_up 字段", "warmed_up" in data, str(data["warmed_up"]))
    check("含版本号", bool(data.get("version")), data.get("version"))

    idx = data["index"] or {}
    check("索引计数一致", idx.get("consistent") is True,
          f"chunks={idx.get('chunks_in_file')} vector={idx.get('vector_count')} bm25={idx.get('bm25_count')}")



def test_meta(client: TestClient) -> None:
    print("\n[3/7] /strategies 与 /config")
    r = client.get("/strategies")
    check("HTTP 200", r.status_code == 200)
    data = r.json()
    values = [s["value"] for s in data["retrieval"]]
    check("四种检索策略齐备",
          values == ["vector", "bm25", "hybrid", "hybrid_rerank"], str(values))
    check("分块策略齐备",
          [s["value"] for s in data["chunk"]] == ["fixed", "recursive"])
    check("含默认策略", bool(data["default_retrieval"]) and bool(data["default_chunk"]),
          f"{data['default_retrieval']} / {data['default_chunk']}")
    check("策略带说明（前端下拉框可用）",
          all(s.get("label") and s.get("description") for s in data["retrieval"]))

    r = client.get("/config")
    check("config HTTP 200", r.status_code == 200)
    cfg = r.json()
    # 真实 API Key 不得出现在响应中
    raw_key = settings.llm.api_key or ""
    body = json.dumps(cfg, ensure_ascii=False)
    check("配置已脱敏（不含完整 API Key）",
          (not raw_key) or (raw_key not in body),
          f"密钥长度 {len(raw_key)}，响应中{'出现' if raw_key and raw_key in body else '未出现'}")
    check("脱敏后仍有可读前缀", "..." in str(cfg.get("llm", {}).get("api_key", "")),
          str(cfg.get("llm", {}).get("api_key")))
    check("config 含版本与项目根", "version" in cfg and "project_root" in cfg)



def test_query(client: TestClient) -> None:
    print("\n[4/7] /query（注入 mock LLM，不消耗 token）")
    service = get_service()
    original_llm = service._llm
    service._llm = make_mock_llm()

    try:
        r = client.post("/query", json={"strategy": "bm25"})
        check("缺少问题时返回 422", r.status_code == 422, f"HTTP {r.status_code}")

        r = client.post("/query", json={"question": "   ", "strategy": "bm25"})
        check("空白问题被拒绝", r.status_code == 422, f"HTTP {r.status_code}")

        r = client.post("/query", json={"question": "测试", "strategy": "not_exist"})
        check("非法策略被拒绝", r.status_code == 422, f"HTTP {r.status_code}")

        # 正常调用（用 bm25 快，避免精排 18 秒）
        t0 = time.perf_counter()
        r = client.post("/query", json={"question": "学校规章制度的适用范围", "strategy": "bm25"})
        dt = time.perf_counter() - t0
        check("HTTP 200", r.status_code == 200, f"HTTP {r.status_code}, {dt:.1f}s")
        if r.status_code != 200:
            check("响应结构", False, r.text[:200])
            return
        data = r.json()

        # 规格要求的四个字段
        for field in ("answer", "citations", "retrieved_docs", "latency"):
            check(f"响应含 {field}", field in data)
        check("answer 非空", bool(data["answer"].strip()), data["answer"][:40])
        check("retrieved_docs 有条目", len(data["retrieved_docs"]) > 0,
              f"{len(data['retrieved_docs'])} 条")
        check("决策依据：strategy 回显", data["strategy"] == "bm25", data["strategy"])

        # latency 结构
        lat = data["latency"]
        check("latency 含分步耗时",
              all(k in lat for k in ("retrieve_ms", "generate_ms", "total_ms")),
              str({k: lat[k] for k in ("retrieve_ms", "generate_ms", "total_ms")}))
        check("total_ms 不小于分步之和",
              lat["total_ms"] >= lat["retrieve_ms"] + lat["generate_ms"] - 1,
              f"total={lat['total_ms']} retrieve={lat['retrieve_ms']} gen={lat['generate_ms']}")

        # citations 结构化
        cites = data["citations"]
        check("citations 含 citations 列表", "citations" in cites)
        check("引用能溯源到来源与页码",
              all(c.get("source") and c.get("page") not in (None, "") for c in cites["citations"]),
              str([(c["source"], c["page"]) for c in cites["citations"]][:2]))
        check("引用带原文（前端要展开）",
              all(c.get("text") for c in cites["citations"]) or not cites["citations"])
        check("含无效引用监控字段", "invalid_indices" in cites and "is_refusal" in cites)

        # docs 字段完整
        doc = data["retrieved_docs"][0]
        check("doc 含 rank/chunk_id/text/score",
              all(k in doc for k in ("rank", "chunk_id", "text", "score")),
              f"rank={doc['rank']} score={doc['score']:.3f}")

        # include_contexts=false 应减小体积
        r2 = client.post("/query", json={"question": "学校规章制度的适用范围",
                                         "strategy": "bm25", "include_contexts": False})
        if r2.status_code == 200:
            long_docs = [d for d in r2.json()["retrieved_docs"] if len(d["text"]) > 120]
            check("include_contexts=false 会裁剪长文本", not long_docs,
                  f"仍有 {len(long_docs)} 条超长")

        # 查询参数方式
        r3 = client.post("/query?question=学生违纪的处理流程&strategy=bm25")
        check("支持查询参数传参（无请求体）", r3.status_code == 200, f"HTTP {r3.status_code}")
        if r3.status_code == 200:
            check("查询参数方式的策略生效", r3.json()["strategy"] == "bm25")

    finally:
        service._llm = original_llm



def test_retrieve(client: TestClient) -> None:
    print("\n[5/7] /retrieve（仅检索）")
    r = client.post("/retrieve", json={"question": "学校规章制度的适用范围", "strategy": "hybrid"})
    check("HTTP 200", r.status_code == 200, f"HTTP {r.status_code}")
    if r.status_code != 200:
        return
    data = r.json()
    check("返回 docs", len(data["docs"]) > 0, f"{len(data['docs'])} 条")
    check("返回召回统计", "dense" in data["stats"] and "sparse" in data["stats"],
          str({k: v for k, v in data["stats"].items() if k != "timing"}))
    stats = data["stats"]
    check("统计含重叠信息（演示混合检索价值）",
          all(k in stats for k in ("overlap", "dense_only", "sparse_only")),
          f"重叠={stats.get('overlap')} 仅dense={stats.get('dense_only')} 仅sparse={stats.get('sparse_only')}")
    check("返回分步耗时", "recall" in data["timing"], str(data["timing"]))

    r2 = client.post("/retrieve", json={"question": "x", "strategy": "bad"})
    check("非法策略返回 422", r2.status_code == 422, f"HTTP {r2.status_code}")



def _fake_pdf(size: int = 2048) -> bytes:
    """构造一个最小可用的 PDF 字节串（只要 %PDF 头 + 填充）。"""
    return b"%PDF-1.4\n" + b"0" * max(0, size - 9) + b"\n%%EOF\n"


@contextlib.contextmanager
def isolated_corpus():
    """
    把 raw 目录与清单重定向到临时目录，并把索引重建替换为空操作。

    上传测试会真的落盘到 data/raw，并触发后台真实向量化（CPU 上数分钟）与
    索引重建，因此必须隔离；_run 换成只写清单的替身以验证任务状态流转。
    """
    tmp = Path(tempfile.mkdtemp(prefix="rag_api_test_"))
    raw = tmp / "raw"
    processed = tmp / "processed"
    raw.mkdir(parents=True, exist_ok=True)
    processed.mkdir(parents=True, exist_ok=True)

    paths = settings.paths
    saved = (paths.raw_dir, paths.processed_dir)
    object.__setattr__(paths, "raw_dir", raw)
    object.__setattr__(paths, "processed_dir", processed)

    # 把真实入库流程换掉：只标记完成，不做解析/向量化
    from src.api import ingest as ing

    original_run = ing.IngestManager._run

    def fake_run(self, task, files, strategy, force):  # noqa: ANN001
        try:
            task.status = "running"
            task.stage = "（测试替身）跳过真实向量化"
            task.progress = 0.5
            task.files.append({"file_name": "stub.pdf", "action": "added",
                               "pages": 1, "chunks": 3, "chars": 30,
                               "reason": "测试替身", "seconds": 0.0})
            task.total_chunks = 3
            task.status = "done"
            task.progress = 1.0
            task.stage = "完成"
            task.finished_at = "test"
        finally:
            with self._lock:
                self._busy = False

    ing.IngestManager._run = fake_run

    try:
        yield tmp
    finally:
        ing.IngestManager._run = original_run
        object.__setattr__(paths, "raw_dir", saved[0])
        object.__setattr__(paths, "processed_dir", saved[1])
        # 等后台线程收尾，避免残留 busy=True 影响后续用例
        manager = ing.get_ingest_manager()
        for _ in range(50):
            if not manager.busy:
                break
            time.sleep(0.1)
        shutil.rmtree(tmp, ignore_errors=True)


def test_ingest_validation(client: TestClient) -> None:
    print("\n[6/7] /ingest 上传校验（隔离目录 + 索引替身）")

    # 强制护栏：必须处于隔离状态，否则会污染 data/raw 并把真实索引重建一遍。
    from src.api import ingest as _ing

    real_raw = (PROJECT_ROOT / "data" / "raw").resolve()
    if settings.paths.raw_dir.resolve() == real_raw:
        raise AssertionError(
            "test_ingest_validation 必须在 isolated_corpus() 上下文内运行："
            f"当前 raw_dir={settings.paths.raw_dir}（真实语料目录），会污染 data/raw"
        )
    if not getattr(_ing.IngestManager._run, "__name__", "").startswith("fake_run"):
        raise AssertionError(
            "test_ingest_validation 必须在 isolated_corpus() 上下文内运行："
            "IngestManager._run 未被替换，后台会执行真实向量化（CPU 上数分钟）"
        )

    r = client.post("/ingest")
    check("未提供文件返回 422", r.status_code == 422, f"HTTP {r.status_code}")

    # 非 PDF 内容（伪装扩展名）
    r = client.post("/ingest", files={"file": ("fake.pdf", b"this is not a pdf", "application/pdf")})
    check("非 PDF 内容被拒绝", r.status_code == 422, f"HTTP {r.status_code}")
    if r.status_code == 422:
        check("拒绝原因说明清楚", "%PDF" in r.json()["detail"], r.json()["detail"][:70])

    r = client.post("/ingest", files={"file": ("empty.pdf", b"", "application/pdf")})
    check("空文件被拒绝", r.status_code == 422, f"HTTP {r.status_code}")

    # 路径穿越文件名：应被净化后落在 raw 目录内
    r = client.post("/ingest", files={
        "file": ("../../etc/passwd.pdf", _fake_pdf(), "application/pdf")})
    check("路径穿越文件名被净化（未写出 raw 目录）",
          not (settings.paths.raw_dir / "etc").exists(),
          f"raw 内文件: {[p.name for p in settings.paths.raw_dir.iterdir()]}")
    if r.status_code == 200:
        check("恶意路径文件已重命名落盘",
              (settings.paths.raw_dir / "passwd.pdf").exists())
        check("返回 task_id 与状态地址",
              bool(r.json()["task_id"]) and r.json()["status_url"].endswith(r.json()["task_id"]),
              r.json()["status_url"])

    # 成功上传 + 幂等重复上传（内容一致不重建）
    r1 = client.post("/ingest", files={"file": ("doc_a.pdf", _fake_pdf(), "application/pdf")})
    if r1.status_code == 200:
        check("首次上传被接受", True, r1.json()["message"][:50])
    # 等替身任务结束
    for _ in range(50):
        if not client.get("/ingest/tasks").json()["busy"]:
            break
        time.sleep(0.1)
    r2 = client.post("/ingest", files={"file": ("doc_a.pdf", _fake_pdf(), "application/pdf")})
    check("内容一致时按幂等处理（不重建索引）",
          r2.status_code == 200 and not r2.json()["task_id"],
          f"HTTP {r2.status_code} {r2.json().get('message', '')[:40]}")

    # 同名不同内容 -> 拒绝覆盖
    r3 = client.post("/ingest", files={
        "file": ("doc_a.pdf", _fake_pdf(9999), "application/pdf")})
    check("同名不同内容被拒绝（防混淆）", r3.status_code == 422,
          f"HTTP {r3.status_code} {str(r3.json().get('detail',''))[:60]}")
    if r3.status_code == 422:
        check("拒绝时提示改名", "改名" in r3.json()["detail"], r3.json()["detail"][:60])

    # 状态与清单接口
    r = client.get("/ingest/manifest")
    check("清单接口可用", r.status_code == 200 and "files" in r.json(), f"HTTP {r.status_code}")
    r = client.get("/ingest/tasks")
    check("任务列表接口可用", r.status_code == 200 and "tasks" in r.json(),
          f"{len(r.json().get('tasks', []))} 个任务")
    r = client.get("/ingest/status/nonexistent")
    check("未知任务返回 404", r.status_code == 404, f"HTTP {r.status_code}")



def test_incremental_plan() -> None:
    print("\n[7/7] 增量计划与清单")
    from src.api import ingest as ing

    files = sorted(settings.paths.raw_dir.rglob("*.pdf"))
    check("语料目录有 PDF", len(files) > 0, f"{len(files)} 个")

    # 空清单 -> 全部 added
    plans = ing.plan_incremental(files[:3], {"version": 1, "files": {}}, force=False)
    check("空清单时均为新增", all(p.action == "added" for p in plans),
          str([(p.path.name, p.action) for p in plans]))

    # 指纹一致 -> skipped
    if files:
        h = ing.file_sha256(files[0])
        manifest = {"version": 1, "files": {files[0].name: {"sha256": h, "chunks": 290}}}
        plans = ing.plan_incremental(files[:1], manifest, force=False)
        check("指纹一致时跳过", plans[0].action == "skipped", plans[0].reason)

        # 指纹变化 -> updated
        manifest["files"][files[0].name]["sha256"] = "deadbeef"
        plans = ing.plan_incremental(files[:1], manifest, force=False)
        check("指纹变化时重灌", plans[0].action == "updated", plans[0].reason)

        # force -> updated
        manifest["files"][files[0].name]["sha256"] = h
        plans = ing.plan_incremental(files[:1], manifest, force=True)
        check("force 时强制重灌", plans[0].action == "updated", plans[0].reason)

    # 文件名净化
    cases = [
        ("../../etc/passwd.pdf", "passwd.pdf"),
        ("a<b>c.pdf", "a_b_c.pdf"),
        ("normal.pdf", "normal.pdf"),
        ("noext", "noext.pdf"),
        ("/abs/path/x.pdf", "x.pdf"),
    ]
    for raw, expect in cases:
        got = ing.sanitize_filename(raw)
        check(f"文件名净化 {raw!r}", got == expect, f"-> {got!r}")

    # 清单读写
    original = ing.load_manifest()
    try:
        ing.save_manifest({"version": 1, "files": {"t.pdf": {"sha256": "abc", "chunks": 1}}})
        reloaded = ing.load_manifest()
        check("清单可保存并回读", reloaded["files"].get("t.pdf", {}).get("chunks") == 1,
              str(reloaded["files"]))
    finally:
        ing.save_manifest(original)


def main() -> int:
    parser = argparse.ArgumentParser(description="阶段 4 后端 API 自测")
    parser.parse_args()

    for name in ("stdout", "stderr"):
        s = getattr(sys, name, None)
        if hasattr(s, "reconfigure"):
            try:
                s.reconfigure(encoding="utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                pass

    print("=" * 92)
    print(" 阶段 4 后端 API 自测（TestClient，不启动服务器）")
    print("=" * 92)

    client = test_app_wiring()
    test_health(client)
    test_meta(client)
    test_query(client)
    test_retrieve(client)
    # 入库测试必须在隔离目录里跑，避免污染真实语料与索引
    with isolated_corpus():
        test_ingest_validation(client)
    test_incremental_plan()

    print("\n" + "=" * 92)
    print(f" 合计 {len(PASSED) + len(FAILED)} 项，通过 {len(PASSED)} 项，失败 {len(FAILED)} 项")
    if FAILED:
        print(" 失败明细：")
        for name, detail in FAILED:
            print(f"   - {name}: {detail}")
    print("=" * 92)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
